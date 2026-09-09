from __future__ import annotations

import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT / "tools"))

from capture_native_entity_stream import (  # noqa: E402
    BattleStateCoordinator,
    DEFAULT_STREAM_INTERVAL_MS,
    advance_evolution_charge,
    evolution_state,
    make_battle_reader,
    merge_card_deployment_events,
    normalize_snapshot,
    played_hand_indices,
)
from minimal_visualizer import _average_card_cost  # noqa: E402
from proc_memory import (  # noqa: E402
    BattlePointers,
    BattleStateLocator,
    CardDeckEntry,
    PlayerHandPointers,
)
from evolution_cycles import EVOLUTION_CYCLES  # noqa: E402


class FakeMemory:
    def __init__(self, reads: dict[tuple[int, int], bytes]) -> None:
        self.reads = reads

    def read(self, address: int, size: int, *, timeout: float) -> bytes:
        return self.reads[(address, size)]

    def read_many(
        self, requests: list[tuple[int, int]], *, timeout: float
    ) -> list[bytes]:
        return [self.read(address, size, timeout=timeout) for address, size in requests]


class NativeSnapshotTests(unittest.TestCase):
    def test_default_stream_interval_is_low_latency(self) -> None:
        self.assertLessEqual(DEFAULT_STREAM_INTERVAL_MS, 20)

    def test_evolution_charge_fills_then_resets_after_evolved_deployment(self) -> None:
        charge = 0
        observed = []
        for _ in range(3):
            charge = advance_evolution_charge(charge, 2)
            observed.append(charge)

        self.assertEqual(observed, [1, 2, 0])
        self.assertEqual(
            evolution_state(26_000_001, "evolution", 2),
            {
                "evolution_cycles": 2,
                "evolution_charge": 2,
                "evolution_ready": True,
            },
        )
        self.assertEqual(
            evolution_state(26_000_047, "evolution", 1)["evolution_ready"],
            True,
        )

    def test_cycle_metadata_covers_every_catalog_evolution(self) -> None:
        catalog = json.loads(
            (WINDOWS_ROOT.parent / "deploy" / "cards.json").read_text(
                encoding="utf-8"
            )
        )
        evolution_ids = {
            item["id"]
            for item in catalog["items"]
            if "evolutionMedium" in item.get("iconUrls", {})
        }

        self.assertTrue(evolution_ids.issubset(EVOLUTION_CYCLES))
        self.assertEqual(EVOLUTION_CYCLES[26_000_001], 2)
        self.assertEqual(EVOLUTION_CYCLES[26_000_047], 1)
        self.assertEqual(EVOLUTION_CYCLES[26_000_043], 1)

    def test_exact_opponent_events_supersede_delayed_entity_fallbacks(self) -> None:
        exact = [(1_000, 27_000_012, "evolution")]
        delayed_entities = [
            (3_000, 27_000_012, "evolution"),
            (12_000, 27_000_012, "evolution"),
        ]

        self.assertEqual(
            merge_card_deployment_events(exact, delayed_entities),
            exact,
        )

    def test_delayed_entity_duplicates_do_not_prevent_evolution_reset(self) -> None:
        card_id = 26_000_059
        exact = [
            (1_000, card_id, "evolution"),
            (10_000, card_id, "evolution"),
            (20_000, card_id, "evolution"),
        ]
        delayed_entities = [
            (3_000, card_id, "evolution"),
            (12_000, card_id, "evolution"),
        ]
        charge = 0
        for _, _, _ in merge_card_deployment_events(exact, delayed_entities):
            charge = advance_evolution_charge(charge, EVOLUTION_CYCLES[card_id])

        self.assertEqual(charge, 0)

    def test_maps_player_resource_order_to_local_and_opponent(self) -> None:
        snapshot = normalize_snapshot(
            {
                "battle_active": True,
                "local_side": 1,
                "local_player_index": 1,
                "player_elixir": [7, 4],
                "own_elixir": 2,
            }
        )

        self.assertEqual(snapshot["own_elixir"], 4)
        self.assertEqual(snapshot["opponent_elixir"], 7)
        self.assertEqual(snapshot["player_elixir"], [7, 4])

    def test_resolved_player_index_also_corrects_native_entity_side(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "own_elixir": 4,
                        "local_player_index": 0,
                        "opponent_cards": [{"slot": 0, "data_id": 26_000_034}],
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()

        snapshot = coordinator.merge(
            normalize_snapshot(
                {
                    "battle_active": True,
                    "local_side": 1,
                    "player_elixir": [6, 3],
                    "own_elixir": -1,
                    "entities": [
                        {"side": 0, "card_id": 26_000_000},
                        {"side": 1, "card_id": 26_000_034},
                    ],
                }
            )
        )

        self.assertEqual(snapshot["local_side"], 0)
        self.assertEqual(snapshot["own_elixir"], 6)
        self.assertEqual(snapshot["opponent_elixir"], 3)
        self.assertEqual(snapshot["opponent_cards"][0]["data_id"], 26_000_034)

    def test_only_reveals_opponent_cards_seen_in_entity_stream(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "local_player_index": 1,
                        "opponent_deck": [
                            {"slot": 0, "data_id": 26_000_005},
                            {"slot": 1, "data_id": 26_000_000},
                            {"slot": 2, "data_id": 26_000_003},
                        ]
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()

        first = coordinator.merge(
            normalize_snapshot(
                {
                    "battle_active": True,
                    "local_side": 1,
                    "entities": [
                        {"side": 0, "card_id": 26_000_003},
                        {"side": 1, "card_id": 26_000_000},
                    ],
                }
            )
        )
        self.assertEqual(first["opponent_cards"][0]["data_id"], 26_000_003)
        self.assertIsNone(first["opponent_cards"][1]["data_id"])
        self.assertIsNone(first["opponent_cards"][2]["data_id"])

        second = coordinator.merge(
            normalize_snapshot(
                {
                    "battle_active": True,
                    "local_side": 1,
                    "entities": [{"side": 0, "card_id": 26_000_005}],
                }
            )
        )
        self.assertEqual(second["opponent_cards"][0]["data_id"], 26_000_003)
        self.assertEqual(second["opponent_cards"][1]["data_id"], 26_000_005)

        coordinator.set_active(False)
        coordinator.set_active(True)
        reset = coordinator.merge(
            normalize_snapshot({"battle_active": True, "local_side": 1})
        )
        self.assertEqual(reset["opponent_cards"], [])

    def test_averages_revealed_opponent_card_costs(self) -> None:
        cards = [
            {"data_id": 26_000_003},  # Giant: 5
            {"data_id": 26_000_005},  # Minions: 3
            {"data_id": 26_000_000},  # Knight: 3
            {"data_id": 26_000_003},  # Repeated Giant is not counted twice
            {"data_id": None},
        ]

        self.assertAlmostEqual(_average_card_cost(cards), 3.7, places=1)

    def test_maps_spirit_empress_battle_form_to_deck_card(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "local_player_index": 0,
                        "opponent_deck": [{"slot": 0, "data_id": 28_000_025}],
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()

        snapshot = coordinator.merge(
            normalize_snapshot(
                {
                    "battle_active": True,
                    "player_elixir": [10, 4],
                    "entities": [{"side": 1, "card_id": 26_000_104}],
                }
            )
        )

        self.assertEqual(snapshot["opponent_cards"][0]["data_id"], 28_000_025)

    def test_revealed_opponent_card_keeps_equipped_form(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "local_player_index": 0,
                        "opponent_deck": [
                            {
                                "slot": 0,
                                "data_id": 26_000_047,
                                "form": "evolution",
                                "evolution_cycles": 1,
                                "evolution_charge": 1,
                                "evolution_ready": True,
                            }
                        ],
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()

        snapshot = coordinator.merge(
            normalize_snapshot(
                {
                    "battle_active": True,
                    "entities": [{"side": 1, "card_id": 26_000_047}],
                }
            )
        )

        self.assertEqual(snapshot["opponent_cards"][0]["data_id"], 26_000_047)
        self.assertEqual(snapshot["opponent_cards"][0]["form"], "evolution")
        self.assertEqual(snapshot["opponent_cards"][0]["evolution_cycles"], 1)
        self.assertEqual(snapshot["opponent_cards"][0]["evolution_charge"], 1)
        self.assertTrue(snapshot["opponent_cards"][0]["evolution_ready"])

    def test_first_entity_reveal_charges_opponent_evolution(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "local_player_index": 0,
                        "opponent_cards": [],
                        "opponent_deck": [
                            {
                                "slot": 0,
                                "data_id": 26_000_047,
                                "form": "evolution",
                                "evolution_cycles": 1,
                                "evolution_charge": 0,
                                "evolution_ready": False,
                            }
                        ],
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()

        snapshot = coordinator.merge(
            normalize_snapshot(
                {
                    "battle_active": True,
                    "entities": [
                        {
                            "address": "0x1234",
                            "side": 1,
                            "card_id": 26_000_047,
                        }
                    ],
                }
            )
        )

        self.assertEqual(snapshot["opponent_cards"][0]["data_id"], 26_000_047)
        self.assertEqual(snapshot["opponent_cards"][0]["evolution_charge"], 1)
        self.assertTrue(snapshot["opponent_cards"][0]["evolution_ready"])

    def test_entity_fallback_tracks_repeated_opponent_evolution_cycles(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "local_player_index": 0,
                        "opponent_cards": [],
                        "opponent_deck": [
                            {
                                "slot": 0,
                                "data_id": 26_000_001,
                                "form": "evolution",
                                "evolution_cycles": 2,
                                "evolution_charge": 0,
                                "evolution_ready": False,
                            }
                        ],
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()

        def merge_at(observed_ms: int, addresses: tuple[str, ...]) -> dict:
            entities = [
                {
                    "address": address,
                    "side": 1,
                    "card_id": 26_000_001,
                }
                for address in addresses
            ]
            snapshot = normalize_snapshot(
                {"battle_active": True, "entities": entities}
            )
            snapshot["t_ms"] = observed_ms
            return coordinator.merge(snapshot)

        first = merge_at(1_000, ("0x1000", "0x1001"))
        merge_at(1_100, ())
        second = merge_at(3_000, ("0x2000", "0x2001"))
        merge_at(3_100, ())
        evolved = merge_at(5_000, ("0x3000", "0x3001"))

        self.assertEqual(first["opponent_cards"][0]["evolution_charge"], 1)
        self.assertEqual(second["opponent_cards"][0]["evolution_charge"], 2)
        self.assertTrue(second["opponent_cards"][0]["evolution_ready"])
        self.assertEqual(evolved["opponent_cards"][0]["evolution_charge"], 0)
        self.assertFalse(evolved["opponent_cards"][0]["evolution_ready"])

    def test_entity_fallback_ignores_a_spawned_unit_with_a_different_kind(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "local_player_index": 0,
                        "opponent_cards": [],
                        "opponent_deck": [
                            {
                                "slot": 0,
                                "data_id": 27_000_012,
                                "form": "evolution",
                                "evolution_cycles": 2,
                                "evolution_charge": 0,
                                "evolution_ready": False,
                            }
                        ],
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()

        def merge_at(observed_ms: int, entities: list[dict]) -> dict:
            snapshot = normalize_snapshot(
                {"battle_active": True, "entities": entities}
            )
            snapshot["t_ms"] = observed_ms
            return coordinator.merge(snapshot)

        first = merge_at(
            1_000,
            [
                {
                    "address": "0xcage",
                    "side": 1,
                    "kind": 12,
                    "card_id": 27_000_012,
                }
            ],
        )
        merge_at(2_000, [])
        spawned_brawler = merge_at(
            22_000,
            [
                {
                    "address": "0xbrawler",
                    "side": 1,
                    "kind": 14,
                    "card_id": 27_000_012,
                }
            ],
        )

        self.assertEqual(first["opponent_cards"][0]["evolution_charge"], 1)
        self.assertEqual(
            spawned_brawler["opponent_cards"][0]["evolution_charge"], 1
        )

    def test_exact_and_entity_reports_count_as_one_opponent_deployment(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "local_player_index": 0,
                        "opponent_cards": [
                            {
                                "slot": 0,
                                "data_id": 26_000_047,
                                "form": "evolution",
                                "observed_ms": 1_000,
                            }
                        ],
                        "opponent_deck": [
                            {
                                "slot": 0,
                                "data_id": 26_000_047,
                                "form": "evolution",
                                "evolution_cycles": 1,
                                "evolution_charge": 1,
                                "evolution_ready": True,
                            }
                        ],
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()
        snapshot = normalize_snapshot(
            {
                "battle_active": True,
                "entities": [
                    {
                        "address": "0x1234",
                        "side": 1,
                        "card_id": 26_000_047,
                    }
                ],
            }
        )
        snapshot["t_ms"] = 2_300

        merged = coordinator.merge(snapshot)

        self.assertEqual(merged["opponent_cards"][0]["evolution_charge"], 1)
        self.assertTrue(merged["opponent_cards"][0]["evolution_ready"])

    def test_maps_hero_battle_entity_to_equipped_opponent_card(self) -> None:
        for card_suffix in (17, 27):
            with self.subTest(card_suffix=card_suffix):
                deck_card_id = 26_000_000 + card_suffix
                hero_entity_id = 203_000_000 + card_suffix
                coordinator = BattleStateCoordinator(
                    lambda: iter(
                        [
                            {
                                "local_player_index": 0,
                                "opponent_deck": [
                                    {
                                        "slot": 0,
                                        "data_id": deck_card_id,
                                        "form": "hero",
                                    }
                                ],
                            }
                        ]
                    ).__next__
                )
                coordinator.set_active(True)
                coordinator.poll()

                snapshot = coordinator.merge(
                    normalize_snapshot(
                        {
                            "battle_active": True,
                            "entities": [
                                {"side": 1, "card_id": hero_entity_id}
                            ],
                        }
                    )
                )

                self.assertEqual(
                    snapshot["opponent_cards"][0]["data_id"], deck_card_id
                )
                self.assertEqual(snapshot["opponent_cards"][0]["form"], "hero")

    def test_uses_exact_opponent_hand_transition_for_same_cost_spell(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter(
                [
                    {
                        "local_player_index": 1,
                        "opponent_deck": [
                            {"slot": 0, "data_id": 28_000_011},  # The Log: 2
                            {"slot": 1, "data_id": 26_000_038},  # Ice Golem: 2
                        ],
                        "opponent_cards": [
                            {
                                "slot": 0,
                                "data_id": 28_000_011,
                                "observed_ms": 1_000,
                            }
                        ],
                    }
                ]
            ).__next__
        )
        coordinator.set_active(True)
        coordinator.poll()

        snapshot = coordinator.merge(
            normalize_snapshot(
                {
                    "battle_active": True,
                    "local_side": 1,
                    "player_elixir": [8, 10],
                }
            )
        )

        self.assertEqual(snapshot["opponent_cards"][0]["data_id"], 28_000_011)

    def test_first_opponent_hand_is_only_a_baseline(self) -> None:
        self.assertEqual(played_hand_indices(None, (0, 3, 5, 1)), ())

    def test_detects_the_deck_index_removed_from_opponent_hand(self) -> None:
        self.assertEqual(
            played_hand_indices((0, 3, 5, 1), (0, 3, 7, 1)),
            (5,),
        )

    def test_reads_selected_forms_from_card_wrappers(self) -> None:
        entry = 0x1000
        container = 0x2000
        wrappers_data = 0x3000
        wrappers = tuple(0x4000 + index * 0x100 for index in range(8))
        data_objects = tuple(0x8000 + index * 0x100 for index in range(8))
        provider_raw = bytearray(0x88)
        struct.pack_into("<Q", provider_raw, 0, entry)
        struct.pack_into("<i", provider_raw, 0x30, 1)
        struct.pack_into("<Q", provider_raw, 0x58, container)
        container_raw = struct.pack("<Qii", wrappers_data, 8, 8)
        reads: dict[tuple[int, int], bytes] = {
            (entry, 8): b"PLAYER00",
            (container + 0x20, 0x10): container_raw,
            (wrappers_data, 64): struct.pack("<8Q", *wrappers),
        }
        form_levels = (1, 2, 7, 0, 0, 0, 0, 0)
        for index, (wrapper, data, form_level) in enumerate(
            zip(wrappers, data_objects, form_levels, strict=True)
        ):
            reads[(wrapper + 0x10, 0x10)] = struct.pack(
                "<Qii", data, 0, form_level
            )
            reads[(data + 0x40, 4)] = struct.pack("<i", 26_000_000 + index)
        locator = BattleStateLocator(FakeMemory(reads))  # type: ignore[arg-type]

        cards = locator._poll_provider_card_deck_details(
            bytes(provider_raw), 1, b"PLAYER00"
        )

        self.assertIsNotNone(cards)
        assert cards is not None
        self.assertEqual(cards[0], CardDeckEntry(26_000_000, "evolution"))
        self.assertEqual(cards[1], CardDeckEntry(26_000_001, "hero"))
        self.assertIsNone(cards[2].form)
        self.assertIsNone(cards[3].form)

    def test_battle_reader_maps_opponent_hand_transition_to_exact_card(self) -> None:
        class FakeLocator:
            def __init__(self, _memory: object) -> None:
                self.last_timing = {}
                self.opponent_hands = iter(
                    [(0, 3, 5, 1), (0, 3, 7, 1)]
                )

            def locate(self) -> BattlePointers:
                return BattlePointers(1, 2, 3, 4, 5)

            def poll_local_player_index(self, _pointers: BattlePointers) -> int:
                return 1

            def locate_player_hand_pointers(
                self, _player_index: int
            ) -> PlayerHandPointers:
                return PlayerHandPointers(10, 11, 12)

            def poll_elixir(self, _pointers: BattlePointers) -> tuple[int, float]:
                return 10, 1.0

            def poll_hand_indices(
                self, _pointers: BattlePointers
            ) -> tuple[int, int, int, int]:
                return (0, 1, 2, 3)

            def poll_card_object_deck_details(
                self, _pointers: BattlePointers
            ) -> tuple[CardDeckEntry, ...]:
                return tuple(CardDeckEntry(None, None) for _ in range(8))

            def poll_opponent_card_deck_details(
                self, _pointers: BattlePointers, _local_index: int
            ) -> tuple[CardDeckEntry, ...]:
                return tuple(
                    CardDeckEntry(data_id, "hero" if index == 5 else None)
                    for index, data_id in enumerate(
                        (
                            26_000_000,
                            26_000_001,
                            26_000_002,
                            26_000_003,
                            26_000_004,
                            28_000_015,
                            26_000_038,
                            26_000_007,
                        )
                    )
                )

            def poll_player_hand_indices(
                self, _pointers: PlayerHandPointers
            ) -> tuple[int, int, int, int]:
                return next(self.opponent_hands)

            def poll_next_deck_index(self, _pointers: BattlePointers) -> int:
                return 4

        with patch(
            "capture_native_entity_stream.BattleStateLocator", FakeLocator
        ):
            reader = make_battle_reader(Path("adb"), "test-device", 123)
            identity = reader()
            baseline = reader()
            played = reader()

        self.assertEqual(identity["local_player_index"], 1)
        self.assertEqual(baseline["opponent_cards"], [])
        self.assertEqual(played["opponent_cards"][0]["data_id"], 28_000_015)
        self.assertEqual(played["opponent_cards"][0]["form"], "hero")

    def test_battle_reader_tracks_both_players_evolution_charge(self) -> None:
        class FakeLocator:
            def __init__(self, _memory: object) -> None:
                self.last_timing = {}
                self.local_hands = iter([(0, 1, 2, 3), (4, 1, 2, 3)])
                self.opponent_hands = iter([(0, 1, 2, 3), (4, 1, 2, 3)])

            def locate(self) -> BattlePointers:
                return BattlePointers(1, 2, 3, 4, 5)

            def poll_local_player_index(self, _pointers: BattlePointers) -> int:
                return 0

            def locate_player_hand_pointers(
                self, _player_index: int
            ) -> PlayerHandPointers:
                return PlayerHandPointers(10, 11, 12)

            def poll_elixir(self, _pointers: BattlePointers) -> tuple[int, float]:
                return 10, 1.0

            def poll_hand_indices(
                self, _pointers: BattlePointers
            ) -> tuple[int, int, int, int]:
                return next(self.local_hands)

            def poll_card_object_deck_details(
                self, _pointers: BattlePointers
            ) -> tuple[CardDeckEntry, ...]:
                return (
                    CardDeckEntry(26_000_001, "evolution"),
                    *(CardDeckEntry(None, None) for _ in range(7)),
                )

            def poll_opponent_card_deck_details(
                self, _pointers: BattlePointers, _local_index: int
            ) -> tuple[CardDeckEntry, ...]:
                return (
                    CardDeckEntry(26_000_047, "evolution"),
                    *(CardDeckEntry(None, None) for _ in range(7)),
                )

            def poll_player_hand_indices(
                self, _pointers: PlayerHandPointers
            ) -> tuple[int, int, int, int]:
                return next(self.opponent_hands)

            def poll_next_deck_index(self, _pointers: BattlePointers) -> int:
                return 0

        with patch(
            "capture_native_entity_stream.BattleStateLocator", FakeLocator
        ):
            reader = make_battle_reader(Path("adb"), "test-device", 123)
            reader()
            reader()
            played = reader()

        self.assertEqual(played["next_card"]["evolution_cycles"], 2)
        self.assertEqual(played["next_card"]["evolution_charge"], 1)
        self.assertFalse(played["next_card"]["evolution_ready"])
        self.assertEqual(played["opponent_cards"][0]["evolution_cycles"], 1)
        self.assertEqual(played["opponent_cards"][0]["evolution_charge"], 1)
        self.assertTrue(played["opponent_cards"][0]["evolution_ready"])

    def test_uses_player_resource_when_ui_elixir_is_unavailable(self) -> None:
        snapshot = normalize_snapshot(
            {
                "battle_active": True,
                "local_side": 0,
                "local_player_index": 0,
                "player_elixir": [6, 2],
                "own_elixir": -1,
            }
        )

        self.assertEqual(snapshot["own_elixir"], 6)
        self.assertEqual(snapshot["opponent_elixir"], 2)

    def test_rejects_invalid_resource_values(self) -> None:
        snapshot = normalize_snapshot(
            {"player_elixir": [-1, 11], "own_elixir": -1}
        )

        self.assertIsNone(snapshot["own_elixir"])
        self.assertIsNone(snapshot["opponent_elixir"])

    def test_normalizes_opponent_cards(self) -> None:
        snapshot = normalize_snapshot(
            {
                "opponent_cards": [
                    {"slot": 0, "data_id": 26_000_001},
                    {"slot": 1, "data_id": -1},
                ]
            }
        )

        self.assertEqual(snapshot["opponent_cards"][0]["data_id"], 26_000_001)
        self.assertIsNone(snapshot["opponent_cards"][1]["data_id"])

    def test_battle_end_discards_previous_state(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter([
                {"own_elixir": 4, "battle_clock": 31.0, "local_player_index": 0}
            ]).__next__
        )

        coordinator.set_active(True)
        coordinator.poll()
        first = coordinator.merge(
            normalize_snapshot(
                {"battle_active": True, "player_elixir": [5, 4]}
            )
        )
        self.assertEqual(first["own_elixir"], 5)

        coordinator.set_active(False)
        between = coordinator.merge(
            normalize_snapshot(
                {"battle_active": False, "player_elixir": [-1, -1]}
            )
        )
        self.assertIsNone(between["own_elixir"])
        self.assertIsNone(between["opponent_elixir"])
        self.assertIsNone(between["battle_clock"])

    def test_second_battle_uses_a_new_reader(self) -> None:
        readers = iter(
            [
                iter([{"own_elixir": 4, "local_player_index": 1}]).__next__,
                iter([
                    {"own_elixir": 6, "battle_clock": 5.0, "local_player_index": 0}
                ]).__next__,
            ]
        )
        coordinator = BattleStateCoordinator(lambda: next(readers))
        coordinator.set_active(True)
        coordinator.poll()
        coordinator.set_active(False)
        coordinator.set_active(True)
        coordinator.poll()
        second = coordinator.merge(
            normalize_snapshot(
                {"battle_active": True, "player_elixir": [3, 6]}
            )
        )
        self.assertEqual(second["own_elixir"], 3)
        self.assertEqual(second["opponent_elixir"], 6)
        self.assertEqual(second["battle_clock"], 5.0)

    def test_resolves_local_player_index_by_account_id(self) -> None:
        libg = 0x100000
        manager = 0x200000
        context = 0x210000
        battle = 0x220000
        hp_state = 0x230000
        model = 0x300000
        player_context = 0x310000
        provider = 0x320000
        provider_entries = (0x330000, 0x340000)
        battle_entries = (0x350000, 0x360000)

        player_raw = bytearray(0x70)
        struct.pack_into("<Q", player_raw, 0, player_context)
        struct.pack_into("<i", player_raw, 0x68, 1)
        provider_raw = bytearray(0x38)
        struct.pack_into("<2Q", provider_raw, 0, *provider_entries)
        struct.pack_into("<i", provider_raw, 0x30, 2)
        hp_raw = bytearray(0x38)
        struct.pack_into("<2Q", hp_raw, 0, *battle_entries)
        struct.pack_into("<i", hp_raw, 0x30, 2)

        reads = {
            (model + 0x10, 0x70): bytes(player_raw),
            (player_context + 0x98, 8): struct.pack("<Q", provider),
            (provider + 0x30, 0x38): bytes(provider_raw),
            (provider_entries[1], 8): b"LOCAL123",
            (libg + BattleStateLocator.ARM_MANAGER_GLOBAL, 8): struct.pack("<Q", manager),
            (manager + BattleStateLocator.MANAGER_CONTEXT, 8): struct.pack("<Q", context),
            (context + BattleStateLocator.CONTEXT_BATTLE, 8): struct.pack("<Q", battle),
            (battle + BattleStateLocator.BATTLE_HP_STATE, 8): struct.pack("<Q", hp_state),
            (hp_state + 0x30, 0x38): bytes(hp_raw),
            (battle_entries[0], 8): b"OTHER456",
            (battle_entries[1], 8): b"LOCAL123",
        }
        locator = BattleStateLocator(FakeMemory(reads))  # type: ignore[arg-type]
        locator._libg_base = libg

        index = locator.poll_local_player_index(
            BattlePointers(0, 0, model, 0, 0)
        )

        self.assertEqual(index, 1)

    def test_binds_each_hand_model_to_its_battle_player_account(self) -> None:
        libg = 0x100000
        manager = 0x200000
        context = 0x210000
        battle = 0x220000
        hp_state = 0x230000
        models = (0x300000, 0x400000)
        player_contexts = (0x310000, 0x410000)
        provider = 0x500000
        provider_entries = (0x510000, 0x520000)
        battle_entries = (0x530000, 0x540000)

        model_reads = []
        for player_context, selector in zip(player_contexts, (0, 1), strict=True):
            raw = bytearray(0x70)
            struct.pack_into("<Q", raw, 0, player_context)
            struct.pack_into("<i", raw, 0x68, selector)
            model_reads.append(bytes(raw))
        provider_raw = bytearray(0x38)
        struct.pack_into("<2Q", provider_raw, 0, *provider_entries)
        struct.pack_into("<i", provider_raw, 0x30, 2)
        hp_raw = bytearray(0x38)
        struct.pack_into("<2Q", hp_raw, 0, *battle_entries)
        struct.pack_into("<i", hp_raw, 0x30, 2)

        reads = {
            (models[0] + 0x10, 0x70): model_reads[0],
            (models[1] + 0x10, 0x70): model_reads[1],
            (player_contexts[0] + 0x98, 8): struct.pack("<Q", provider),
            (player_contexts[1] + 0x98, 8): struct.pack("<Q", provider),
            (provider + 0x30, 0x38): bytes(provider_raw),
            (provider_entries[0], 8): b"PLAYER00",
            (provider_entries[1], 8): b"PLAYER11",
            (libg + BattleStateLocator.ARM_MANAGER_GLOBAL, 8): struct.pack(
                "<Q", manager
            ),
            (manager + BattleStateLocator.MANAGER_CONTEXT, 8): struct.pack(
                "<Q", context
            ),
            (context + BattleStateLocator.CONTEXT_BATTLE, 8): struct.pack(
                "<Q", battle
            ),
            (battle + BattleStateLocator.BATTLE_HP_STATE, 8): struct.pack(
                "<Q", hp_state
            ),
            (hp_state + 0x30, 0x38): bytes(hp_raw),
            (battle_entries[0], 8): b"PLAYER00",
            (battle_entries[1], 8): b"PLAYER11",
        }
        locator = BattleStateLocator(FakeMemory(reads))  # type: ignore[arg-type]
        locator._libg_base = libg
        candidates = (
            PlayerHandPointers(models[0], 0x600000, 0x610000),
            PlayerHandPointers(models[1], 0x700000, 0x710000),
        )
        locator._player_hand_candidates = candidates

        self.assertEqual(locator.locate_player_hand_pointers(0), candidates[0])
        self.assertEqual(locator.locate_player_hand_pointers(1), candidates[1])


if __name__ == "__main__":
    unittest.main()
