from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame


WINDOWS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_ROOT / "deploy"))

import opponent_card_bar as card_bar  # noqa: E402


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

    def test_sidebar_layout_stays_inside_the_left_rail(self) -> None:
        self.assertTrue(
            pygame.Rect(0, 0, card_bar.LEFT_RAIL_WIDTH, card_bar.WINDOW_SIZE[1]).contains(
                card_bar.SIDEBAR_TITLE_RECT
            )
        )
        button_rects = list(card_bar.SIDEBAR_BUTTON_RECTS.values())
        self.assertEqual(list(card_bar.SIDEBAR_BUTTON_RECTS), list(card_bar.SIDEBAR_ACTIONS))
        self.assertEqual(len(button_rects), 4)
        for index, rect in enumerate(button_rects):
            self.assertLessEqual(rect.right, card_bar.LEFT_RAIL_WIDTH)
            self.assertGreater(rect.top, card_bar.SIDEBAR_TITLE_RECT.bottom)
            if index:
                self.assertFalse(rect.colliderect(button_rects[index - 1]))

    def test_sidebar_button_click_feedback_requires_release_on_same_button(self) -> None:
        renderer = card_bar.OpponentCardBarRenderer()
        first, second = list(card_bar.SIDEBAR_BUTTON_RECTS.values())[:2]

        self.assertTrue(renderer.press_sidebar(first.center))
        self.assertEqual(renderer.pressed_sidebar_action, "opponent_info")
        self.assertIsNone(renderer.release_sidebar(second.center))

        self.assertTrue(renderer.press_sidebar(first.center))
        self.assertEqual(renderer.release_sidebar(first.center), "opponent_info")
        self.assertFalse(renderer.press_sidebar((card_bar.LEFT_RAIL_WIDTH + 1, 10)))

    def test_sidebar_feedback_only_exists_while_button_is_pressed(self) -> None:
        renderer = card_bar.OpponentCardBarRenderer()
        rect = card_bar.SIDEBAR_BUTTON_RECTS["settings"]
        normal = pygame.Surface(card_bar.WINDOW_SIZE)
        renderer.draw(normal, None)

        renderer.press_sidebar(rect.center)
        pressed = pygame.Surface(card_bar.WINDOW_SIZE)
        renderer.draw(pressed, None)
        self.assertNotEqual(
            pygame.image.tobytes(normal.subsurface(rect), "RGB"),
            pygame.image.tobytes(pressed.subsurface(rect), "RGB"),
        )

        renderer.release_sidebar(rect.center)
        released = pygame.Surface(card_bar.WINDOW_SIZE)
        renderer.draw(released, None)
        self.assertEqual(
            pygame.image.tobytes(normal.subsurface(rect), "RGB"),
            pygame.image.tobytes(released.subsurface(rect), "RGB"),
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

    def test_indexes_every_card_icon_variant(self) -> None:
        self.assertEqual(len(self.renderer.icon_paths), 122)
        self.assertTrue(
            all("normal" in variants for variants in self.renderer.icon_paths.values())
        )
        self.assertEqual(
            set(self.renderer.icon_paths[26_000_000]),
            {
                "normal",
                "hero",
                "evolution_0_of_2",
                "evolution_1_of_2",
                "evolution_2_of_2",
            },
        )

    def test_selects_icons_for_hero_and_every_evolution_charge(self) -> None:
        cases = (
            ({"data_id": 26_000_000}, "normal.png"),
            ({"data_id": 26_000_000, "form": "hero"}, "hero.png"),
            (
                {
                    "data_id": 26_000_000,
                    "form": "evolution",
                    "evolution_cycles": 2,
                    "evolution_charge": 0,
                },
                "evolution_0_of_2.png",
            ),
            (
                {
                    "data_id": 26_000_000,
                    "form": "evolution",
                    "evolution_cycles": 2,
                    "evolution_charge": 1,
                },
                "evolution_1_of_2.png",
            ),
            (
                {
                    "data_id": 26_000_000,
                    "form": "evolution",
                    "evolution_cycles": 2,
                    "evolution_charge": 2,
                },
                "evolution_2_of_2.png",
            ),
            (
                {
                    "data_id": 26_000_004,
                    "form": "evolution",
                    "evolution_cycles": 1,
                    "evolution_charge": 0,
                },
                "evolution_0_of_1.png",
            ),
            (
                {
                    "data_id": 26_000_004,
                    "form": "evolution",
                    "evolution_cycles": 1,
                    "evolution_charge": 1,
                },
                "evolution_1_of_1.png",
            ),
        )
        for card, filename in cases:
            with self.subTest(filename=filename):
                path = self.renderer._card_icon_path(card)
                self.assertIsNotNone(path)
                self.assertEqual(path.name, filename)

    def test_missing_or_invalid_form_icon_falls_back_to_normal(self) -> None:
        for card in (
            {"data_id": 26_000_002, "form": "evolution"},
            {
                "data_id": 26_000_000,
                "form": "evolution",
                "evolution_cycles": 3,
                "evolution_charge": 2,
            },
        ):
            with self.subTest(card=card):
                path = self.renderer._card_icon_path(card)
                self.assertIsNotNone(path)
                self.assertEqual(path.name, "normal.png")

    def test_card_image_cache_separates_forms_of_the_same_card(self) -> None:
        normal = self.renderer._card_image({"data_id": 26_000_000})
        hero = self.renderer._card_image(
            {"data_id": 26_000_000, "form": "hero"}
        )
        evolution = self.renderer._card_image(
            {
                "data_id": 26_000_000,
                "form": "evolution",
                "evolution_cycles": 2,
                "evolution_charge": 1,
            }
        )
        self.assertIsNotNone(normal)
        self.assertIsNotNone(hero)
        self.assertIsNotNone(evolution)
        self.assertIsNot(normal, hero)
        self.assertIsNot(normal, evolution)

    def test_every_hero_closely_matches_its_normal_card_visible_bounds(self) -> None:
        hero_ids = [
            data_id
            for data_id, variants in self.renderer.icon_paths.items()
            if "hero" in variants
        ]
        self.assertEqual(len(hero_ids), 16)
        for data_id in hero_ids:
            with self.subTest(data_id=data_id):
                normal = self.renderer._card_image({"data_id": data_id})
                hero = self.renderer._card_image(
                    {"data_id": data_id, "form": "hero"}
                )
                self.assertIsNotNone(normal)
                self.assertIsNotNone(hero)
                normal_bounds = normal.get_bounding_rect()
                hero_bounds = hero.get_bounding_rect()
                for normal_edge, hero_edge in zip(
                    (
                        normal_bounds.left,
                        normal_bounds.top,
                        normal_bounds.right,
                        normal_bounds.bottom,
                    ),
                    (
                        hero_bounds.left,
                        hero_bounds.top,
                        hero_bounds.right,
                        hero_bounds.bottom,
                    ),
                ):
                    self.assertLessEqual(abs(normal_edge - hero_edge), 2)
                self.assertGreaterEqual(
                    hero_bounds.width * hero_bounds.height
                    / (normal_bounds.width * normal_bounds.height),
                    0.95,
                )

    def test_runtime_assets_cover_all_elixir_states(self) -> None:
        self.assertEqual(set(self.renderer.badges), {*range(11), "unknown"})
        self.assertEqual(set(self.renderer.digits), {*map(str, range(10)), "."})

    def test_approved_background_fills_the_entire_window(self) -> None:
        source = pygame.image.load(str(card_bar.BACKGROUND_ASSET)).convert()
        expected = pygame.transform.smoothscale(source, card_bar.WINDOW_SIZE)
        surface = pygame.Surface(card_bar.WINDOW_SIZE)

        self.renderer._draw_background(surface)

        self.assertEqual(self.renderer.background.get_size(), card_bar.WINDOW_SIZE)
        for point in ((0, 0), (80, 170), (250, 100), (499, 339)):
            with self.subTest(point=point):
                self.assertEqual(surface.get_at(point), expected.get_at(point))

    def test_zero_and_ten_use_complete_badge_artwork(self) -> None:
        standard = pygame.image.load(str(card_bar.ASSET_ROOT / "cost_8.png"))
        self.assertEqual(standard.get_size(), (112, 112))
        for value in (0, 10):
            badge = pygame.image.load(str(card_bar.ASSET_ROOT / f"cost_{value}.png"))
            self.assertEqual(badge.get_size(), standard.get_size())
            self.assertGreater(
                sum(
                    badge.get_at((x, y)) != standard.get_at((x, y))
                    for y in range(112)
                    for x in range(112)
                    if not pygame.Rect(18, 20, 78, 74).collidepoint(x, y)
                ),
                500,
            )
            self.assertTrue(pygame.Rect(0, 0, 112, 112).contains(badge.get_bounding_rect()))
            if value == 0:
                bounds = badge.get_bounding_rect()
                self.assertGreaterEqual(bounds.width, 102)
                self.assertGreaterEqual(bounds.height, 104)

    def test_average_zero_uses_the_same_approved_zero_glyph(self) -> None:
        average_zero = pygame.image.load(
            str(card_bar.ASSET_ROOT / "digits" / "0.png")
        )
        approved_zero = pygame.image.load(
            str(card_bar.ASSET_ROOT / "glyph_refs" / "average_0.png")
        )
        self.assertEqual(average_zero.get_size(), approved_zero.get_size())
        self.assertEqual(
            pygame.image.tobytes(average_zero, "RGBA"),
            pygame.image.tobytes(approved_zero, "RGBA"),
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
