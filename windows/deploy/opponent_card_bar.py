from __future__ import annotations

import ctypes
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pygame


WINDOW_SIZE = (500, 340)
LEFT_RAIL_WIDTH = 82
PANEL_TOP = (13, 133, 220)
PANEL_BOTTOM = (7, 72, 143)
CARD_SIZE = (88, 127)
UNKNOWN_SIZE = (82, 92)
CARD_COLUMNS = (99, 194, 289, 384)
CARD_ROWS = (10, 145)
STATUS_RECT = pygame.Rect(99, 289, 389, 44)
ELIXIR_BADGE_RECT = pygame.Rect(96, 289, 44, 44)
ELIXIR_METER_RECT = pygame.Rect(132, 299, 282, 25)
AVERAGE_RECT = pygame.Rect(421, 288, 67, 45)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ICON_ROOT = PROJECT_ROOT / "resources" / "icons"
ASSET_ROOT = PROJECT_ROOT / "resources" / "ui" / "opponent_card_bar"
CARD_CATALOG = PROJECT_ROOT / "deploy" / "cards.json"
ICON_VARIANTS = (
    "normal",
    "hero",
    "evolution_0_of_1",
    "evolution_1_of_1",
    "evolution_0_of_2",
    "evolution_1_of_2",
    "evolution_2_of_2",
)


def _load_card_costs(path: Path = CARD_CATALOG) -> dict[int, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return {
        int(item["id"]): float(item["elixirCost"])
        for item in payload.get("items", [])
        if isinstance(item, dict)
        and "id" in item
        and isinstance(item.get("elixirCost"), (int, float))
    }


CARD_COSTS = _load_card_costs()


def average_revealed_cost(cards: list[dict[str, Any]]) -> float | None:
    card_ids = {
        data_id
        for item in cards
        if isinstance(item, dict)
        and isinstance((data_id := item.get("data_id")), int)
        and data_id in CARD_COSTS
    }
    return (
        sum(CARD_COSTS[data_id] for data_id in card_ids) / len(card_ids)
        if card_ids
        else None
    )


def card_cell_rect(slot: int) -> pygame.Rect:
    return pygame.Rect(CARD_COLUMNS[slot % 4], CARD_ROWS[slot // 4], *CARD_SIZE)


def unknown_slot_rect(slot: int) -> pygame.Rect:
    cell = card_cell_rect(slot)
    return pygame.Rect(cell.left + 3, cell.top + 17, *UNKNOWN_SIZE)


def _panel_color_at(y: int) -> tuple[int, int, int]:
    ratio = max(0, min(WINDOW_SIZE[1] - 1, y)) / (WINDOW_SIZE[1] - 1)
    return tuple(
        round(start + (end - start) * ratio)
        for start, end in zip(PANEL_TOP, PANEL_BOTTOM)
    )


def _vertical_gradient(
    surface: pygame.Surface,
    rect: pygame.Rect,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> None:
    denominator = max(1, rect.height - 1)
    for offset in range(rect.height):
        ratio = offset / denominator
        color = tuple(round(start + (end - start) * ratio) for start, end in zip(top, bottom))
        pygame.draw.line(
            surface,
            color,
            (rect.left, rect.top + offset),
            (rect.right - 1, rect.top + offset),
        )


def _rounded_vertical_gradient(
    surface: pygame.Surface,
    rect: pygame.Rect,
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
    radius: int,
) -> None:
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    _vertical_gradient(layer, layer.get_rect(), top, bottom)
    mask = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)
    layer.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    surface.blit(layer, rect)


def _windows_font(size: int, *, bold: bool = False) -> pygame.font.Font:
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return pygame.font.Font(str(path), size)
    return pygame.font.Font(None, size)


class OpponentCardBarRenderer:
    def __init__(
        self,
        icon_root: Path = ICON_ROOT,
        asset_root: Path = ASSET_ROOT,
    ) -> None:
        self.icon_paths = self._index_icons(icon_root)
        self.asset_root = asset_root
        self.card_images: dict[tuple[int, str], pygame.Surface] = {}
        self.badges = {
            value: self._load_scaled(asset_root / f"cost_{value}.png", (44, 44))
            for value in [*range(11), "unknown"]
            if (asset_root / f"cost_{value}.png").is_file()
        }
        self.question = self._load_height(asset_root / "question_inset.png", 46)
        self.digits = {
            character: pygame.image.load(str(asset_root / "digits" / filename)).convert_alpha()
            for character, filename in {
                **{str(value): f"{value}.png" for value in range(10)},
                ".": "dot.png",
            }.items()
        }
        self.label_font = _windows_font(10, bold=True)
        self.fallback_number_font = _windows_font(20, bold=True)

    @staticmethod
    def _index_icons(icon_root: Path) -> dict[int, dict[str, Path]]:
        result: dict[int, dict[str, Path]] = {}
        if not icon_root.is_dir():
            return result
        for directory in icon_root.iterdir():
            if not directory.is_dir():
                continue
            prefix, _, _ = directory.name.partition("_")
            try:
                data_id = int(prefix)
            except ValueError:
                continue
            variants = {
                variant: path
                for variant in ICON_VARIANTS
                if (path := directory / f"{variant}.png").is_file()
            }
            if "normal" in variants:
                result[data_id] = variants
        return result

    @staticmethod
    def _load_scaled(path: Path, size: tuple[int, int]) -> pygame.Surface:
        image = pygame.image.load(str(path)).convert_alpha()
        return pygame.transform.smoothscale(image, size)

    @classmethod
    def _load_height(cls, path: Path, height: int) -> pygame.Surface:
        image = pygame.image.load(str(path)).convert_alpha()
        width = max(1, round(image.get_width() * height / image.get_height()))
        return pygame.transform.smoothscale(image, (width, height))

    @classmethod
    def _load_hero_fitted_to_normal(
        cls,
        hero_path: Path,
        normal_path: Path,
    ) -> pygame.Surface:
        hero = pygame.image.load(str(hero_path)).convert_alpha()
        hero_bounds = hero.get_bounding_rect()
        normal = cls._load_scaled(normal_path, CARD_SIZE)
        target_bounds = normal.get_bounding_rect()
        if hero_bounds.width <= 0 or hero_bounds.height <= 0:
            return cls._load_scaled(hero_path, CARD_SIZE)
        visible_hero = hero.subsurface(hero_bounds)
        fitted = pygame.transform.smoothscale(visible_hero, target_bounds.size)
        result = pygame.Surface(CARD_SIZE, pygame.SRCALPHA)
        result.blit(fitted, target_bounds)
        return result

    def _card_icon_path(self, card: dict[str, Any]) -> Path | None:
        data_id = card.get("data_id")
        if not isinstance(data_id, int):
            return None
        variants = self.icon_paths.get(data_id)
        if variants is None:
            return None

        variant = "normal"
        if card.get("form") == "hero":
            variant = "hero"
        elif card.get("form") == "evolution":
            cycles = card.get("evolution_cycles")
            charge = card.get("evolution_charge")
            if cycles in (1, 2) and type(charge) is int:
                normalized_charge = max(0, min(charge, cycles))
                variant = f"evolution_{normalized_charge}_of_{cycles}"
        return variants.get(variant) or variants.get("normal")

    def _card_image(self, card: dict[str, Any]) -> pygame.Surface | None:
        data_id = card.get("data_id")
        if not isinstance(data_id, int):
            return None
        path = self._card_icon_path(card)
        if path is None:
            return None
        cache_key = (data_id, path.stem)
        if cache_key in self.card_images:
            return self.card_images[cache_key]
        normal_path = self.icon_paths[data_id]["normal"]
        image = (
            self._load_hero_fitted_to_normal(path, normal_path)
            if path.stem == "hero"
            else self._load_scaled(path, CARD_SIZE)
        )
        self.card_images[cache_key] = image
        return image

    def _draw_background(self, surface: pygame.Surface) -> None:
        rail = pygame.Rect(0, 0, LEFT_RAIL_WIDTH, WINDOW_SIZE[1])
        for x in range(rail.width):
            if x <= 57:
                ratio = x / 57
                start, end = (7, 61, 118), (11, 85, 149)
            else:
                ratio = (x - 57) / 24
                start, end = (11, 85, 149), (6, 53, 107)
            color = tuple(round(a + (b - a) * ratio) for a, b in zip(start, end))
            pygame.draw.line(surface, color, (x, 0), (x, WINDOW_SIZE[1] - 1))
        pygame.draw.rect(surface, (20, 109, 173), (0, 0, LEFT_RAIL_WIDTH, 4))
        pygame.draw.rect(surface, (3, 43, 89), (0, WINDOW_SIZE[1] - 4, LEFT_RAIL_WIDTH, 4))
        pygame.draw.rect(surface, (6, 45, 91), (LEFT_RAIL_WIDTH - 4, 0, 4, WINDOW_SIZE[1]))
        pygame.draw.line(
            surface,
            (37, 141, 209),
            (LEFT_RAIL_WIDTH - 5, 0),
            (LEFT_RAIL_WIDTH - 5, WINDOW_SIZE[1] - 1),
        )

        panel = pygame.Rect(LEFT_RAIL_WIDTH, 0, WINDOW_SIZE[0] - LEFT_RAIL_WIDTH, WINDOW_SIZE[1])
        _vertical_gradient(surface, panel, PANEL_TOP, PANEL_BOTTOM)
        pygame.draw.rect(surface, (53, 167, 237), (LEFT_RAIL_WIDTH, 0, panel.width, 4))
        pygame.draw.rect(surface, (4, 60, 119), (LEFT_RAIL_WIDTH, WINDOW_SIZE[1] - 4, panel.width, 4))

    def _draw_unknown(self, surface: pygame.Surface, slot: int) -> None:
        rect = unknown_slot_rect(slot)
        shadow = rect.move(0, 2)
        pygame.draw.rect(surface, (3, 50, 99), shadow, border_radius=8)
        _rounded_vertical_gradient(surface, rect, (9, 87, 149), (5, 60, 118), 8)
        panel_color = _panel_color_at(rect.top)
        edge_color = tuple(
            min(255, channel + offset)
            for channel, offset in zip(panel_color, (14, 17, 3))
        )
        pygame.draw.rect(surface, edge_color, rect, 2, border_radius=8)
        inner = rect.inflate(-4, -4)
        pygame.draw.rect(surface, (6, 66, 126), inner, 2, border_radius=6)
        question_rect = self.question.get_rect(center=rect.center)
        question_rect.y -= 1
        surface.blit(self.question, question_rect)

    def _draw_cards(self, surface: pygame.Surface, cards: list[dict[str, Any]]) -> None:
        for slot in range(8):
            item = cards[slot] if slot < len(cards) and isinstance(cards[slot], dict) else {}
            image = self._card_image(item)
            if image is None:
                self._draw_unknown(surface, slot)
            else:
                surface.blit(image, card_cell_rect(slot))

    def _draw_meter(self, surface: pygame.Surface, elixir: int | None) -> None:
        rect = ELIXIR_METER_RECT
        pygame.draw.rect(surface, (3, 37, 87), rect, border_radius=7)
        inner = rect.inflate(-6, -6)
        pygame.draw.rect(surface, (5, 46, 104), inner, border_radius=3)
        if elixir is not None:
            fill_width = round(inner.width * max(0, min(elixir, 10)) / 10)
            if fill_width > 0:
                fill = pygame.Rect(inner.x, inner.y, fill_width, inner.height)
                _vertical_gradient(surface, fill, (242, 62, 231), (205, 22, 205))
                pygame.draw.line(surface, (255, 137, 245), fill.topleft, (fill.right - 1, fill.top))
        for index in range(1, 10):
            x = inner.x + round(inner.width * index / 10)
            pygame.draw.line(surface, (117, 19, 142), (x, inner.top), (x, inner.bottom - 1), 2)
        pygame.draw.rect(surface, (3, 37, 87), rect, 2, border_radius=7)

    def _draw_average_number(
        self,
        surface: pygame.Surface,
        text: str,
        center: tuple[int, int],
    ) -> None:
        glyphs: list[tuple[pygame.Surface, int]] = []
        for character in text:
            source = self.digits.get(character)
            if source is None:
                rendered = self.fallback_number_font.render(character, True, (250, 252, 255))
                glyphs.append((rendered, -rendered.get_height() // 2))
                continue
            height = 5 if character == "." else 18
            width = max(1, round(source.get_width() * height / source.get_height()))
            y_offset = 4 if character == "." else -height // 2
            glyphs.append((pygame.transform.smoothscale(source, (width, height)), y_offset))
        total_width = sum(glyph.get_width() for glyph, _ in glyphs) + max(0, len(glyphs) - 1)
        x = center[0] - total_width // 2
        for glyph, y_offset in glyphs:
            surface.blit(glyph, (x, center[1] + y_offset))
            x += glyph.get_width() + 1

    def _draw_status(
        self,
        surface: pygame.Surface,
        elixir: int | None,
        average: float | None,
    ) -> None:
        self._draw_meter(surface, elixir)
        badge_key: int | str = "unknown" if elixir is None else max(0, min(round(elixir), 10))
        badge = self.badges.get(badge_key) or self.badges.get("unknown")
        if badge is not None:
            surface.blit(badge, ELIXIR_BADGE_RECT)

        _rounded_vertical_gradient(
            surface,
            AVERAGE_RECT,
            (18, 111, 179),
            (7, 77, 144),
            8,
        )
        pygame.draw.rect(surface, (4, 53, 108), AVERAGE_RECT, 2, border_radius=8)
        label = self.label_font.render("平均费用", True, (217, 237, 255))
        surface.blit(label, label.get_rect(midtop=(AVERAGE_RECT.centerx, AVERAGE_RECT.top + 4)))
        if average is None:
            text = "--"
        else:
            text = f"{average:.1f}"
        self._draw_average_number(surface, text, (AVERAGE_RECT.centerx, AVERAGE_RECT.top + 31))

    def draw(self, surface: pygame.Surface, snapshot: dict[str, Any] | None) -> None:
        self._draw_background(surface)
        cards = snapshot.get("opponent_cards", []) if snapshot is not None else []
        if not isinstance(cards, list):
            cards = []
        cards = [item for item in cards if isinstance(item, dict)]
        self._draw_cards(surface, cards)
        raw_elixir = snapshot.get("opponent_elixir") if snapshot is not None else None
        elixir = raw_elixir if isinstance(raw_elixir, int) else None
        self._draw_status(surface, elixir, average_revealed_cost(cards))


def _set_always_on_top() -> None:
    if sys.platform != "win32":
        return
    window = pygame.display.get_wm_info().get("window")
    if not isinstance(window, int):
        return
    swp_nomove = 0x0002
    swp_nosize = 0x0001
    swp_noactivate = 0x0010
    set_window_pos = ctypes.windll.user32.SetWindowPos
    set_window_pos.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    )
    set_window_pos.restype = ctypes.c_int
    set_window_pos(
        ctypes.c_void_p(window),
        ctypes.c_void_p(-1),
        0,
        0,
        0,
        0,
        swp_nomove | swp_nosize | swp_noactivate,
    )


def _cursor_position() -> tuple[int, int]:
    if sys.platform != "win32":
        return pygame.mouse.get_pos()

    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = Point()
    get_cursor_position = ctypes.windll.user32.GetCursorPos
    get_cursor_position.argtypes = (ctypes.POINTER(Point),)
    get_cursor_position.restype = ctypes.c_int
    get_cursor_position(ctypes.byref(point))
    return point.x, point.y


def _dragged_window_position(
    cursor_start: tuple[int, int],
    window_start: tuple[int, int],
    cursor: tuple[int, int],
) -> tuple[int, int]:
    return (
        window_start[0] + cursor[0] - cursor_start[0],
        window_start[1] + cursor[1] - cursor_start[1],
    )


def run(snapshot_provider: Callable[[], dict[str, Any] | None]) -> None:
    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE, pygame.NOFRAME)
    pygame.display.set_caption("NR++ opponent cards")
    _set_always_on_top()
    renderer = OpponentCardBarRenderer()
    clock = pygame.time.Clock()
    drag_origin: tuple[tuple[int, int], tuple[int, int]] | None = None

    from pygame._sdl2 import Window

    window = Window.from_display_module()
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYUP and event.key == pygame.K_ESCAPE:
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                drag_origin = (_cursor_position(), window.position)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                drag_origin = None

        if drag_origin is not None and pygame.mouse.get_pressed(num_buttons=3)[0]:
            cursor_start, window_start = drag_origin
            cursor = _cursor_position()
            window.position = _dragged_window_position(cursor_start, window_start, cursor)

        renderer.draw(screen, snapshot_provider())
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
