from __future__ import annotations

import os
import sys
import unittest
from collections import deque
from pathlib import Path


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT / "deploy"))

import opponent_card_bar as card_bar  # noqa: E402


def _normalized_white_glyph(
    surface: pygame.Surface,
    component_count: int,
    roi: pygame.Rect | None = None,
    size: int = 64,
) -> set[tuple[int, int]]:
    bounds = roi or surface.get_rect()
    white_pixels = set()
    for y in range(bounds.top, bounds.bottom):
        for x in range(bounds.left, bounds.right):
            color = surface.get_at((x, y))
            channels = color[:3]
            if max(channels) - min(channels) < 78 and min(channels) >= 145 and color.a:
                white_pixels.add((x, y))

    components: list[set[tuple[int, int]]] = []
    remaining = set(white_pixels)
    while remaining:
        start = remaining.pop()
        component = {start}
        pending = deque([start])
        while pending:
            x, y = pending.popleft()
            for neighbor_y in range(y - 1, y + 2):
                for neighbor_x in range(x - 1, x + 2):
                    neighbor = (neighbor_x, neighbor_y)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        pending.append(neighbor)
        components.append(component)

    retained = set().union(
        *sorted(components, key=len, reverse=True)[:component_count]
    )
    left = min(x for x, _ in retained)
    right = max(x for x, _ in retained)
    top = min(y for _, y in retained)
    bottom = max(y for _, y in retained)
    width = right - left + 1
    height = bottom - top + 1
    return {
        (x, y)
        for y in range(size)
        for x in range(size)
        if (
            left + min(width - 1, x * width // size),
            top + min(height - 1, y * height // size),
        )
        in retained
    }


def _mask_iou(first: set[tuple[int, int]], second: set[tuple[int, int]]) -> float:
    return len(first & second) / len(first | second)


class OpponentCardBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()
        pygame.display.set_mode((1, 1))
        cls.renderer = card_bar.OpponentCardBarRenderer()

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def test_layout_uses_two_rows_of_four_cards(self) -> None:
        self.assertEqual(card_bar.WINDOW_SIZE, (500, 340))
        self.assertEqual(
            [card_bar.card_cell_rect(slot).topleft for slot in range(8)],
            [
                (99, 10),
                (194, 10),
                (289, 10),
                (384, 10),
                (99, 145),
                (194, 145),
                (289, 145),
                (384, 145),
            ],
        )

    def test_unknown_slots_match_visible_normal_card_face(self) -> None:
        for slot in range(8):
            cell = card_bar.card_cell_rect(slot)
            unknown = card_bar.unknown_slot_rect(slot)
            self.assertEqual(unknown.size, (82, 92))
            self.assertEqual(unknown.topleft, (cell.left + 3, cell.top + 17))

    def test_unknown_slot_edges_have_the_same_contrast_in_both_rows(self) -> None:
        surface = pygame.Surface(card_bar.WINDOW_SIZE, pygame.SRCALPHA)
        self.renderer.draw(surface, None)
        contrasts = []
        for slot in (0, 4):
            rect = card_bar.unknown_slot_rect(slot)
            edge = surface.get_at((rect.centerx, rect.top))[:3]
            panel = surface.get_at((rect.centerx, rect.top - 1))[:3]
            contrast = tuple(edge[channel] - panel[channel] for channel in range(3))
            self.assertTrue(all(value > 0 for value in contrast))
            contrasts.append(contrast)
        for top, bottom in zip(*contrasts):
            self.assertLessEqual(abs(top - bottom), 2)

    def test_indexes_every_normal_card_icon(self) -> None:
        self.assertEqual(len(self.renderer.icon_paths), 122)
        self.assertTrue(all(path.name == "normal.png" for path in self.renderer.icon_paths.values()))

    def test_runtime_assets_cover_all_elixir_states(self) -> None:
        self.assertEqual(set(self.renderer.badges), {*range(11), "unknown"})
        self.assertEqual(set(self.renderer.digits), {*map(str, range(10)), "."})

    def test_zero_and_ten_elixir_badges_keep_master_resolution(self) -> None:
        approved = pygame.image.load(str(card_bar.ASSET_ROOT / "cost_8.png"))
        approved_size = approved.get_size()
        self.assertEqual(approved_size, (112, 112))
        editable_center = pygame.Rect(18, 20, 78, 74)
        for value in (0, 10):
            badge = pygame.image.load(str(card_bar.ASSET_ROOT / f"cost_{value}.png"))
            self.assertEqual(badge.get_size(), approved_size)
            for y in range(approved_size[1]):
                for x in range(approved_size[0]):
                    if not editable_center.collidepoint(x, y):
                        self.assertEqual(badge.get_at((x, y)), approved.get_at((x, y)))

    def test_zero_and_ten_use_approved_reference_glyphs(self) -> None:
        editable_center = pygame.Rect(18, 20, 78, 74)
        for value, component_count in ((0, 1), (10, 2)):
            reference = pygame.image.load(
                str(card_bar.ASSET_ROOT / "glyph_refs" / f"elixir_{value}.png")
            )
            badge = pygame.image.load(str(card_bar.ASSET_ROOT / f"cost_{value}.png"))
            reference_mask = _normalized_white_glyph(reference, component_count)
            badge_mask = _normalized_white_glyph(
                badge,
                component_count,
                editable_center,
            )
            self.assertGreaterEqual(
                _mask_iou(reference_mask, badge_mask),
                0.94,
                f"cost_{value}.png does not preserve its approved glyph shape",
            )

    def test_average_cost_uses_unique_revealed_cards(self) -> None:
        cards = [
            {"data_id": 26_000_030},
            {"data_id": 26_000_000},
            {"data_id": 26_000_021},
            {"data_id": 26_000_017},
            {"data_id": 26_000_002},
            {"data_id": 26_000_002},
        ]
        self.assertEqual(card_bar.average_revealed_cost(cards), 3.0)
        self.assertIsNone(card_bar.average_revealed_cost([]))

    def test_panel_gradient_uses_approved_endpoints(self) -> None:
        surface = pygame.Surface((2, 5))
        card_bar._vertical_gradient(
            surface,
            surface.get_rect(),
            card_bar.PANEL_TOP,
            card_bar.PANEL_BOTTOM,
        )
        self.assertEqual(tuple(surface.get_at((0, 0))[:3]), (13, 133, 220))
        self.assertEqual(tuple(surface.get_at((0, 4))[:3]), (7, 72, 143))

    def test_dragging_preserves_the_initial_cursor_offset(self) -> None:
        self.assertEqual(
            card_bar._dragged_window_position((410, 220), (100, 80), (465, 260)),
            (155, 120),
        )

    def test_draws_waiting_and_revealed_states(self) -> None:
        waiting = pygame.Surface(card_bar.WINDOW_SIZE, pygame.SRCALPHA)
        self.renderer.draw(waiting, None)
        unknown_center = card_bar.unknown_slot_rect(0).center
        self.assertNotEqual(
            tuple(waiting.get_at(unknown_center)[:3]),
            card_bar.PANEL_TOP,
        )

        active = pygame.Surface(card_bar.WINDOW_SIZE, pygame.SRCALPHA)
        self.renderer.draw(
            active,
            {
                "opponent_elixir": 10,
                "opponent_cards": [{"data_id": 26_000_030}],
            },
        )
        self.assertNotEqual(
            tuple(active.get_at(card_bar.card_cell_rect(0).center)[:3]),
            tuple(waiting.get_at(card_bar.card_cell_rect(0).center)[:3]),
        )

    def test_inset_top_edges_do_not_contain_detached_accent_lines(self) -> None:
        surface = pygame.Surface(card_bar.WINDOW_SIZE, pygame.SRCALPHA)
        self.renderer.draw(surface, None)

        unknown_inner = card_bar.unknown_slot_rect(0).inflate(-4, -4)
        self.assertNotEqual(
            tuple(surface.get_at(unknown_inner.topleft)[:3]),
            (2, 45, 88),
        )
        self.assertNotEqual(
            tuple(
                surface.get_at(
                    (card_bar.AVERAGE_RECT.left + 4, card_bar.AVERAGE_RECT.top + 2)
                )[:3]
            ),
            (52, 166, 233),
        )


if __name__ == "__main__":
    unittest.main()
