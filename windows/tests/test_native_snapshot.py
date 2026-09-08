from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT / "tools"))

from capture_native_entity_stream import (  # noqa: E402
    BattleStateCoordinator,
    DEFAULT_STREAM_INTERVAL_MS,
    normalize_snapshot,
)
from proc_memory import BattlePointers, BattleStateLocator  # noqa: E402


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

    def test_player_resource_order_is_not_entity_side_order(self) -> None:
        coordinator = BattleStateCoordinator(
            lambda: iter([{"own_elixir": 4, "local_player_index": 0}]).__next__
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
                }
            )
        )

        self.assertEqual(snapshot["own_elixir"], 6)
        self.assertEqual(snapshot["opponent_elixir"], 3)

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


if __name__ == "__main__":
    unittest.main()
