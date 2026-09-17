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

import communication_panel as communication  # noqa: E402
import opponent_card_bar as card_bar  # noqa: E402


class FakeOpponentInfoController:
    def __init__(self) -> None:
        self.requests = 0

    def request(self) -> None:
        self.requests += 1

    @staticmethod
    def current() -> card_bar.OpponentInfoState:
        return card_bar.OpponentInfoState(status="loading")


class CommunicationPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def make_panel(self, blacklist_path: Path) -> communication.CommunicationPanel:
        return communication.CommunicationPanel(blacklist_path=blacklist_path)

    def test_catalog_contains_every_currently_available_emote(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(Path(directory) / "blacklist.json")
            self.assertEqual(len(panel.emotes), 572)
            self.assertEqual(len({entry.identifier for entry in panel.emotes}), 572)

    def test_sidebar_action_toggles_communication_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(Path(directory) / "blacklist.json")
            controller = card_bar.OpponentCardBarController(
                communication=panel
            )
            self.assertTrue(controller.handle_sidebar_action("communication"))
            self.assertEqual(controller.page, "communication")
            self.assertTrue(controller.handle_sidebar_action("communication"))
            self.assertEqual(controller.page, "cards")

    def test_switching_from_communication_preserves_opponent_info_and_cards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(Path(directory) / "blacklist.json")
            info = FakeOpponentInfoController()
            controller = card_bar.OpponentCardBarController(info, panel)

            self.assertTrue(controller.handle_sidebar_action("communication"))
            self.assertTrue(controller.handle_sidebar_action("opponent_info"))
            self.assertEqual(controller.page, "opponent_info")
            self.assertEqual(info.requests, 1)
            self.assertTrue(controller.handle_sidebar_action("opponent_info"))
            self.assertEqual(controller.page, "cards")

    def test_scroll_and_drag_are_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(Path(directory) / "blacklist.json")
            panel.scroll(10_000_000)
            self.assertEqual(panel.scroll_x, panel.max_scroll)
            panel.scroll(-10_000_000)
            self.assertEqual(panel.scroll_x, 0)
            start = communication.EMOTE_VIEWPORT.center
            self.assertTrue(panel.press(start))
            self.assertTrue(panel.drag((start[0] - 80, start[1])))
            self.assertEqual(panel.scroll_x, 80)
            self.assertFalse(panel.release((start[0] - 80, start[1])))

    def test_click_toggles_and_immediately_persists_blacklist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blacklist.json"
            panel = self.make_panel(path)
            position = panel._emote_tile_rect(0).center
            identifier = panel.emotes[0].identifier

            self.assertTrue(panel.press(position))
            self.assertTrue(panel.release(position))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(identifier, payload["animated_emote_ids"])

            self.assertTrue(panel.press(position))
            self.assertTrue(panel.release(position))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn(identifier, payload["animated_emote_ids"])

    def test_text_buttons_use_the_six_expected_messages(self) -> None:
        self.assertEqual(
            [label for _identifier, label in communication.TEXT_LABELS],
            ["祝你好运！", "厉害！", "哇哦！", "承让！", "精彩的比赛！", "哎呦"],
        )
        rects = [communication.text_button_rect(index) for index in range(6)]
        self.assertTrue(all(communication.TEXT_AREA.contains(rect) for rect in rects))

    def test_blocked_item_draws_gray_overlay_and_red_prohibition_mark(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            panel = self.make_panel(Path(directory) / "blacklist.json")
            surface = pygame.Surface(card_bar.WINDOW_SIZE, pygame.SRCALPHA)
            panel.blocked_emotes.add(panel.emotes[0].identifier)
            panel.draw(surface)
            tile = panel._emote_tile_rect(0)
            red_pixels = sum(
                1
                for x in range(tile.left, tile.right)
                for y in range(tile.top, tile.bottom)
                if (lambda color: color.r > 190 and color.r > color.g * 1.8)(
                    surface.get_at((x, y))
                )
            )
            self.assertGreater(red_pixels, 25)


if __name__ == "__main__":
    unittest.main()
