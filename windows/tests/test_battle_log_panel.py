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
                        {"address": 10, "side": 0, "hp": 1_000, "max_hp": 1_000},
                        {"address": 20, "side": 1, "hp": 800, "max_hp": 800},
                    ],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 2_500,
                    "local_side": 0,
                    "entities": [
                        {"address": 10, "side": 0, "hp": 880, "max_hp": 1_000},
                        {"address": 20, "side": 1, "hp": 750, "max_hp": 800},
                    ],
                }
            )
            panel.observe(
                {
                    "battle_active": True,
                    "t_ms": 4_000,
                    "local_side": 0,
                    "entities": [
                        {"address": 10, "side": 0, "hp": 880, "max_hp": 1_000},
                        {"address": 20, "side": 1, "hp": 750, "max_hp": 800},
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

            self.assertEqual(len(panel.entries), 1)
            summary = panel.entries[0]
            self.assertEqual(summary.kind, "damage_summary")
            self.assertEqual(summary.elapsed_ms, 3_000)
            self.assertEqual(summary.local_damage, 50)
            self.assertEqual(summary.opponent_damage, 120)

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

    def test_formats_card_and_damage_entries_in_both_languages(self) -> None:
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
        damage = battle_log.BattleLogEntry(
            elapsed_ms=9_000,
            kind="damage_summary",
            local_damage=326,
            opponent_damage=184,
        )

        self.assertEqual(
            battle_log.format_entry(card, "zh-CN"),
            "[00:07] 我方使用了进化 Knight，消耗 3 圣水（8 → 6）",
        )
        self.assertEqual(
            battle_log.format_entry(card, "en-US"),
            "[00:07] You played Evolved Knight, cost 3 elixir (8 → 6)",
        )
        self.assertEqual(
            battle_log.format_entry(damage, "zh-CN"),
            "[00:09] 伤害汇总：我方造成 326，对方造成 184",
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
