from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT / "deploy"))

import battle_log_panel as battle_log  # noqa: E402


class BattleLogPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def make_panel(self, directory: str) -> battle_log.BattleLogPanel:
        catalog = Path(directory) / "cards.json"
        catalog.write_text(
            json.dumps(
                {
                    "items": [
                        {"id": 26_000_000, "name": "Knight", "elixirCost": 3},
                        {"id": 28_000_000, "name": "Fireball", "elixirCost": 4},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return battle_log.BattleLogPanel(card_catalog=catalog)

    def test_records_each_confirmed_card_play_once_with_cost_and_sampled_elixir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(directory)
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 1_000,
                    "own_elixir": 8,
                    "opponent_elixir": 6,
                    "card_play_events": [],
                    "entities": [],
                }
            )
            played = {
                "battle_active": True,
                "t_ms": 1_200,
                "own_elixir": 6,
                "opponent_elixir": 6,
                "card_play_events": [
                    {
                        "side": "local",
                        "observed_after_ms": 1_050,
                        "observed_ms": 1_190,
                        "data_id": 26_000_000,
                        "form": None,
                    }
                ],
                "entities": [],
            }

            panel.observe(played)
            panel.observe(played)

            self.assertEqual(len(panel.entries), 1)
            entry = panel.entries[0]
            self.assertEqual(entry.kind, "card_play")
            self.assertEqual(entry.side, "local")
            self.assertEqual(entry.card_name, "Knight")
            self.assertEqual(entry.elixir_cost, 3)
            self.assertEqual((entry.elixir_before, entry.elixir_after), (8, 6))
            self.assertEqual(entry.elapsed_ms, 190)

    def test_summarizes_all_entity_health_loss_every_three_seconds_and_skips_zeroes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(directory)
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 1_000,
                    "local_side": 0,
                    "entities": [
                        {
                            "address": 10,
                            "side": 0,
                            "card_id": 26_000_000,
                            "hp": 1_000,
                            "max_hp": 1_000,
                        },
                        {
                            "address": 20,
                            "side": 1,
                            "card_id": 28_000_000,
                            "hp": 800,
                            "max_hp": 800,
                        },
                    ],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 2_500,
                    "local_side": 0,
                    "entities": [
                        {
                            "address": 10,
                            "side": 0,
                            "card_id": 26_000_000,
                            "hp": 880,
                            "max_hp": 1_000,
                        },
                        {
                            "address": 20,
                            "side": 1,
                            "card_id": 28_000_000,
                            "hp": 750,
                            "max_hp": 800,
                        },
                    ],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 4_000,
                    "local_side": 0,
                    "entities": [
                        {
                            "address": 10,
                            "side": 0,
                            "card_id": 26_000_000,
                            "hp": 880,
                            "max_hp": 1_000,
                        },
                        {
                            "address": 20,
                            "side": 1,
                            "card_id": 28_000_000,
                            "hp": 750,
                            "max_hp": 800,
                        },
                    ],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 7_000,
                    "local_side": 0,
                    "entities": [],
                }
            )

            self.assertEqual(len(panel.entries), 2)
            local_detail, opponent_detail = panel.entries
            self.assertEqual(local_detail.kind, "damage_target")
            self.assertEqual(local_detail.side, "local")
            self.assertEqual(local_detail.target_name, "Fireball")
            self.assertEqual(local_detail.damage, 50)
            self.assertEqual(opponent_detail.kind, "damage_target")
            self.assertEqual(opponent_detail.side, "opponent")
            self.assertEqual(opponent_detail.target_name, "Knight")
            self.assertEqual(opponent_detail.damage, 120)
            self.assertNotIn(
                "damage_summary", {entry.kind for entry in panel.entries}
            )

    def test_reused_entity_address_with_different_max_health_is_not_damage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(directory)
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 1_000,
                    "local_side": 0,
                    "entities": [
                        {"address": 20, "side": 1, "hp": 1_000, "max_hp": 1_000}
                    ],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 2_000,
                    "local_side": 0,
                    "entities": [
                        {"address": 20, "side": 1, "hp": 200, "max_hp": 500}
                    ],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 4_000,
                    "local_side": 0,
                    "entities": [],
                }
            )

            self.assertEqual(panel.entries, ())

    def test_groups_same_card_targets_and_names_crown_towers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(directory)
            baseline = [
                {
                    "address": 21,
                    "side": 1,
                    "card_id": 26_000_000,
                    "hp": 500,
                    "max_hp": 500,
                },
                {
                    "address": 22,
                    "side": 1,
                    "card_id": 203_000_000,
                    "hp": 400,
                    "max_hp": 400,
                },
                {
                    "address": 30,
                    "kind": 13,
                    "side": 1,
                    "card_id": None,
                    "x": 3_500,
                    "hp": 3_000,
                    "max_hp": 3_000,
                },
            ]
            damaged = [
                {**baseline[0], "hp": 480},
                {**baseline[1], "hp": 370},
                {**baseline[2], "hp": 2_900},
            ]
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 1_000,
                    "local_side": 0,
                    "entities": baseline,
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 2_000,
                    "local_side": 0,
                    "entities": damaged,
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 4_000,
                    "local_side": 0,
                    "entities": damaged,
                }
            )

            details = [entry for entry in panel.entries if entry.kind == "damage_target"]
            self.assertEqual(
                [(entry.target_kind, entry.target_name, entry.damage) for entry in details],
                [
                    ("card", "Knight", 50),
                    ("left_princess_tower", "Left Princess Tower", 100),
                ],
            )
            self.assertEqual(
                battle_log.format_entry(details[1], "zh-CN"),
                "[00:03] 我方 → 对方左侧公主塔：造成 100 点伤害",
            )

    def test_summarizes_tower_health_and_card_costs_every_eighteen_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(directory)
            baseline = [
                {
                    "address": 10,
                    "kind": 13,
                    "side": 0,
                    "card_id": None,
                    "x": 9_000,
                    "hp": 1_000,
                    "max_hp": 1_000,
                },
                {
                    "address": 11,
                    "kind": 13,
                    "side": 0,
                    "card_id": None,
                    "x": 3_500,
                    "hp": 500,
                    "max_hp": 500,
                },
                {
                    "address": 20,
                    "kind": 13,
                    "side": 1,
                    "card_id": None,
                    "x": 9_000,
                    "hp": 1_200,
                    "max_hp": 1_200,
                },
                {
                    "address": 21,
                    "kind": 13,
                    "side": 1,
                    "card_id": None,
                    "x": 14_500,
                    "hp": 600,
                    "max_hp": 600,
                },
            ]
            events = [
                {
                    "side": "local",
                    "observed_after_ms": 1_500,
                    "observed_ms": 2_000,
                    "data_id": 26_000_000,
                },
                {
                    "side": "opponent",
                    "observed_after_ms": 2_500,
                    "observed_ms": 3_000,
                    "data_id": 28_000_000,
                },
                {
                    "side": "local",
                    "observed_after_ms": 10_000,
                    "observed_ms": 10_500,
                    "data_id": 26_000_000,
                },
            ]
            final = [
                {**baseline[0], "hp": 900},
                {**baseline[1], "hp": 400},
                {**baseline[2], "hp": 1_000},
                {**baseline[3], "hp": 500},
            ]
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 1_000,
                    "local_side": 1,
                    "entities": baseline,
                    "card_play_events": [],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 1_100,
                    "local_side": 0,
                    "entities": baseline,
                    "card_play_events": [],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 19_000,
                    "local_side": 0,
                    "entities": final,
                    "card_play_events": events,
                }
            )

            cycle = [entry for entry in panel.entries if entry.kind.startswith("cycle_")]
            self.assertEqual([entry.kind for entry in cycle], [
                "cycle_tower",
                "cycle_tower",
                "cycle_elixir",
            ])
            local_tower, opponent_tower, elixir = cycle
            self.assertEqual(
                (local_tower.side, local_tower.tower_hp_before, local_tower.tower_hp_after),
                ("local", 1_500, 1_300),
            )
            self.assertEqual(
                (
                    opponent_tower.side,
                    opponent_tower.tower_hp_before,
                    opponent_tower.tower_hp_after,
                ),
                ("opponent", 1_800, 1_500),
            )
            self.assertEqual(
                (elixir.local_elixir_spent, elixir.opponent_elixir_spent),
                (6, 4),
            )
            self.assertEqual(
                battle_log.format_entry(local_tower, "zh-CN"),
                "[00:18] 我方防御塔总血量：1500 → 1300（-200）",
            )
            self.assertEqual(
                battle_log.format_entry(elixir, "zh-CN"),
                "[00:18] 周期费用：我方 6，对方 4",
            )

    def test_formats_card_entries_in_both_languages(self) -> None:
        card = battle_log.BattleLogEntry(
            elapsed_ms=7_400,
            kind="card_play",
            side="local",
            card_id=26_000_000,
            card_name="Knight",
            form="evolution",
            elixir_cost=3,
            elixir_before=8,
            elixir_after=6,
        )
        self.assertEqual(
            battle_log.format_entry(card, "zh-CN"),
            "[00:07] 我方使用了进化 Knight，消耗 3 圣水（8 → 6）",
        )
        self.assertEqual(
            battle_log.format_entry(card, "en-US"),
            "[00:07] You played Evolved Knight, cost 3 elixir (8 → 6)",
        )

    def test_draws_the_log_area_bilingually_and_scrolls_inside_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(directory)
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 1_000,
                    "own_elixir": 10,
                    "opponent_elixir": 10,
                    "entities": [],
                    "card_play_events": [],
                }
            )
            events = []
            for index in range(16):
                events.append(
                    {
                        "side": "local" if index % 2 == 0 else "opponent",
                        "observed_after_ms": 1_010 + index * 100,
                        "observed_ms": 1_050 + index * 100,
                        "data_id": 26_000_000,
                    }
                )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 3_000,
                    "own_elixir": 7,
                    "opponent_elixir": 8,
                    "entities": [],
                    "card_play_events": events,
                }
            )
            chinese = pygame.Surface((500, 340), pygame.SRCALPHA)
            english = pygame.Surface((500, 340), pygame.SRCALPHA)
            panel.draw(chinese, language="zh-CN")
            panel.draw(english, language="en-US")

            self.assertNotEqual(
                pygame.image.tobytes(chinese, "RGBA"),
                pygame.image.tobytes(english, "RGBA"),
            )
            before_scroll = pygame.image.tobytes(chinese, "RGBA")
            self.assertTrue(panel.wheel((250, 180), 3))
            scrolled = pygame.Surface((500, 340), pygame.SRCALPHA)
            panel.draw(scrolled, language="zh-CN")
            self.assertNotEqual(before_scroll, pygame.image.tobytes(scrolled, "RGBA"))
            self.assertFalse(panel.wheel((40, 180), 1))


if __name__ == "__main__":
    unittest.main()
