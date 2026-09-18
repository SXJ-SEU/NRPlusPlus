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

import opponent_card_bar as card_bar  # noqa: E402
import settings_panel as settings_ui  # noqa: E402


class FakeOpponentInfoController:
    def __init__(self) -> None:
        self.requests = 0

    def request(self) -> None:
        self.requests += 1

    @staticmethod
    def current() -> card_bar.OpponentInfoState:
        return card_bar.OpponentInfoState(status="idle")


class SettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def make_store(self, directory: str) -> settings_ui.SettingsStore:
        return settings_ui.SettingsStore(Path(directory) / "settings.json")

    def click(self, panel: settings_ui.SettingsPanel, position: tuple[int, int]) -> None:
        self.assertTrue(panel.press(position))
        self.assertTrue(panel.release(position))

    def test_defaults_preserve_existing_runtime_behavior(self) -> None:
        defaults = settings_ui.PluginSettings()
        self.assertEqual(defaults.language, "zh-CN")
        self.assertTrue(defaults.always_on_top)
        self.assertFalse(defaults.auto_query_opponent)
        self.assertFalse(defaults.remember_window_position)
        self.assertFalse(defaults.auto_hide_outside_game)
        self.assertIsNone(defaults.window_position)

    def test_store_persists_validated_settings_and_window_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            store.update(
                language="en-US",
                always_on_top=False,
                remember_window_position=True,
                window_position=(321, 123),
            )
            loaded = self.make_store(directory).current
            self.assertEqual(loaded.language, "en-US")
            self.assertFalse(loaded.always_on_top)
            self.assertTrue(loaded.remember_window_position)
            self.assertEqual(loaded.window_position, (321, 123))

    def test_invalid_payload_falls_back_field_by_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "language": "invalid",
                        "always_on_top": "yes",
                        "auto_query_opponent": True,
                        "window_position": ["x", 2],
                    }
                ),
                encoding="utf-8",
            )
            loaded = settings_ui.SettingsStore(path).current
            self.assertEqual(loaded.language, "zh-CN")
            self.assertTrue(loaded.always_on_top)
            self.assertTrue(loaded.auto_query_opponent)
            self.assertIsNone(loaded.window_position)

    def test_panel_controls_all_five_values_and_restores_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            panel = settings_ui.SettingsPanel(store)

            self.click(panel, panel.LANGUAGE_BUTTONS["en-US"].center)
            self.assertEqual(store.current.language, "en-US")
            for key in panel.TOGGLE_KEYS:
                self.click(panel, panel.TOGGLE_RECTS[key].center)
            self.assertFalse(store.current.always_on_top)
            self.assertTrue(store.current.auto_query_opponent)
            self.assertTrue(store.current.remember_window_position)
            self.assertTrue(store.current.auto_hide_outside_game)

            store.update(window_position=(40, 80))
            self.click(panel, panel.RESET_RECT.center)
            self.assertEqual(store.current, settings_ui.PluginSettings())

    def test_settings_sidebar_toggles_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            controller = card_bar.OpponentCardBarController(settings_store=store)
            self.assertTrue(controller.handle_sidebar_action("settings"))
            self.assertEqual(controller.page, "settings")
            self.assertIsNotNone(controller.settings_panel)
            self.assertTrue(controller.handle_sidebar_action("settings"))
            self.assertEqual(controller.page, "cards")

    def test_auto_query_runs_once_per_battle_and_resets_between_battles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            store.update(auto_query_opponent=True)
            info = FakeOpponentInfoController()
            controller = card_bar.OpponentCardBarController(
                opponent_info=info,
                settings_store=store,
            )
            controller.observe_snapshot({"battle_active": True})
            controller.observe_snapshot({"battle_active": True, "opponent_tag": "#ABC"})
            self.assertEqual(info.requests, 1)
            controller.observe_snapshot({"battle_active": False})
            controller.observe_snapshot({"battle_active": True})
            self.assertEqual(info.requests, 2)

    def test_disabled_auto_query_never_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            info = FakeOpponentInfoController()
            controller = card_bar.OpponentCardBarController(
                opponent_info=info,
                settings_store=self.make_store(directory),
            )
            controller.observe_snapshot({"battle_active": True})
            self.assertEqual(info.requests, 0)

    def test_auto_hide_visibility_uses_battle_state(self) -> None:
        visible = settings_ui.PluginSettings(auto_hide_outside_game=False)
        hidden = settings_ui.PluginSettings(auto_hide_outside_game=True)
        self.assertTrue(card_bar._should_window_be_visible(visible, None))
        self.assertFalse(
            card_bar._should_window_be_visible(hidden, {"battle_active": False})
        )
        self.assertTrue(
            card_bar._should_window_be_visible(hidden, {"battle_active": True})
        )

    def test_settings_page_draws_in_both_languages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.make_store(directory)
            panel = settings_ui.SettingsPanel(store)
            renderer = card_bar.OpponentCardBarRenderer()
            chinese = pygame.Surface(card_bar.WINDOW_SIZE)
            renderer.draw(
                chinese,
                None,
                page="settings",
                settings_panel=panel,
                language="zh-CN",
            )
            store.update(language="en-US")
            english = pygame.Surface(card_bar.WINDOW_SIZE)
            renderer.draw(
                english,
                None,
                page="settings",
                settings_panel=panel,
                language="en-US",
            )
            self.assertNotEqual(
                pygame.image.tobytes(chinese, "RGB"),
                pygame.image.tobytes(english, "RGB"),
            )


if __name__ == "__main__":
    unittest.main()
