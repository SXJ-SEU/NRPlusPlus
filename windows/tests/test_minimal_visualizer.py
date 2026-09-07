from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pygame


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT / "deploy"))

import minimal_visualizer as visualizer  # noqa: E402


class ArenaPerspectiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.font.init()

    def test_side_one_is_local_blue_and_drawn_at_bottom(self) -> None:
        local = {"side": 1, "kind": 13, "x": 6_000, "y": 29_000, "card_id": None}
        opponent = {"side": 0, "kind": 13, "x": 12_000, "y": 3_000, "card_id": None}

        local_point = visualizer._arena_point(local)
        opponent_point = visualizer._arena_point(opponent)
        self.assertIsNotNone(local_point)
        self.assertIsNotNone(opponent_point)
        assert local_point is not None and opponent_point is not None
        self.assertGreater(local_point[1], visualizer.ARENA.centery)
        self.assertLess(opponent_point[1], visualizer.ARENA.centery)

        surface = pygame.Surface(visualizer.SCREEN_SIZE)
        visualizer._draw_arena(
            surface,
            [local, opponent],
            pygame.font.Font(None, 16),
        )
        self.assertEqual(tuple(surface.get_at(local_point)[:3]), (72, 184, 213))
        self.assertEqual(tuple(surface.get_at(opponent_point)[:3]), (231, 105, 91))

    def test_side_zero_perspective_rotates_the_arena(self) -> None:
        local = {"side": 0, "kind": 13, "x": 3_500, "y": 6_500, "card_id": None}
        opponent = {"side": 1, "kind": 13, "x": 14_500, "y": 25_500, "card_id": None}

        local_point = visualizer._arena_point(local, local_side=0)
        opponent_point = visualizer._arena_point(opponent, local_side=0)
        self.assertIsNotNone(local_point)
        self.assertIsNotNone(opponent_point)
        assert local_point is not None and opponent_point is not None
        self.assertGreater(local_point[1], visualizer.ARENA.centery)
        self.assertLess(opponent_point[1], visualizer.ARENA.centery)

        surface = pygame.Surface(visualizer.SCREEN_SIZE)
        visualizer._draw_arena(
            surface,
            [local, opponent],
            pygame.font.Font(None, 16),
            local_side=0,
        )
        self.assertEqual(tuple(surface.get_at(local_point)[:3]), (72, 184, 213))
        self.assertEqual(tuple(surface.get_at(opponent_point)[:3]), (231, 105, 91))


if __name__ == "__main__":
    unittest.main()
