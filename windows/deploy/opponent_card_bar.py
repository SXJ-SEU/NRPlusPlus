from __future__ import annotations

import ctypes
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pygame

from opponent_info import CardSummary, OpponentInfoController, OpponentInfoState


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
CONTENT_RECT = pygame.Rect(82, 0, 418, 340)
SIDEBAR_TITLE_RECT = pygame.Rect(4, 7, 74, 30)
SIDEBAR_ACTIONS = (
    "opponent_info",
    "battle_log",
    "communication",
    "settings",
)
SIDEBAR_BUTTON_RECTS = {
    action: pygame.Rect(5, 43 + index * 73, 72, 67)
    for index, action in enumerate(SIDEBAR_ACTIONS)
}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ICON_ROOT = PROJECT_ROOT / "resources" / "icons"
ASSET_ROOT = PROJECT_ROOT / "resources" / "ui" / "opponent_card_bar"
BACKGROUND_ASSET = ASSET_ROOT / "background.png"
SIDEBAR_ASSET_ROOT = ASSET_ROOT / "sidebar"
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
        background = pygame.image.load(str(asset_root / "background.png")).convert()
        self.background = pygame.transform.smoothscale(background, WINDOW_SIZE)
        sidebar_root = asset_root / "sidebar"
        self.sidebar_title = self._load_contained(
            sidebar_root / "title.png", SIDEBAR_TITLE_RECT.size
        )
        self.sidebar_icons = {
            action: self._load_contained(sidebar_root / f"{action}.png", (62, 62))
            for action in SIDEBAR_ACTIONS
        }
        self.pressed_sidebar_action: str | None = None
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
        self.info_title_font = _windows_font(18, bold=True)
        self.info_body_font = _windows_font(13)
        self.info_small_font = _windows_font(11)
        self.info_stat_font = _windows_font(22, bold=True)
        self.deck_card_images: dict[tuple[int, str | None], pygame.Surface] = {}

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

    @staticmethod
    def _load_contained(path: Path, size: tuple[int, int]) -> pygame.Surface:
        image = pygame.image.load(str(path)).convert_alpha()
        scale = min(size[0] / image.get_width(), size[1] / image.get_height())
        fitted_size = (
            max(1, round(image.get_width() * scale)),
            max(1, round(image.get_height() * scale)),
        )
        return pygame.transform.smoothscale(image, fitted_size)

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
        surface.blit(self.background, (0, 0))

    @staticmethod
    def sidebar_action_at(position: tuple[int, int]) -> str | None:
        return next(
            (
                action
                for action, rect in SIDEBAR_BUTTON_RECTS.items()
                if rect.collidepoint(position)
            ),
            None,
        )

    def press_sidebar(self, position: tuple[int, int]) -> bool:
        action = self.sidebar_action_at(position)
        self.pressed_sidebar_action = action
        return action is not None

    def release_sidebar(self, position: tuple[int, int]) -> str | None:
        released_action = self.sidebar_action_at(position)
        clicked_action = (
            released_action
            if released_action is not None
            and released_action == self.pressed_sidebar_action
            else None
        )
        self.pressed_sidebar_action = None
        return clicked_action

    def _draw_sidebar(self, surface: pygame.Surface) -> None:
        surface.blit(self.sidebar_title, self.sidebar_title.get_rect(center=SIDEBAR_TITLE_RECT.center))
        for action, rect in SIDEBAR_BUTTON_RECTS.items():
            is_pressed = action == self.pressed_sidebar_action
            if is_pressed:
                feedback = pygame.Surface(rect.size, pygame.SRCALPHA)
                feedback_rect = feedback.get_rect().inflate(-4, -4)
                pygame.draw.rect(
                    feedback, (0, 25, 67, 115), feedback_rect, border_radius=10
                )
                pygame.draw.rect(
                    feedback,
                    (100, 197, 255, 210),
                    feedback_rect,
                    2,
                    border_radius=10,
                )
                surface.blit(feedback, rect)

            icon = self.sidebar_icons[action]
            if is_pressed:
                pressed_size = (
                    max(1, round(icon.get_width() * 0.91)),
                    max(1, round(icon.get_height() * 0.91)),
                )
                icon = pygame.transform.smoothscale(icon, pressed_size)
            icon_rect = icon.get_rect(center=rect.center)
            if is_pressed:
                icon_rect.move_ip(0, 2)
            surface.blit(icon, icon_rect)

    def _draw_unknown(self, surface: pygame.Surface, slot: int) -> None:
        rect = unknown_slot_rect(slot)
        shadow = rect.move(0, 2)
        pygame.draw.rect(surface, (3, 50, 99), shadow, border_radius=8)
        _rounded_vertical_gradient(surface, rect, (9, 87, 149), (5, 60, 118), 8)
        panel_color = surface.get_at((rect.centerx, max(0, rect.top - 1)))[:3]
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

    def _deck_card_image(self, card: CardSummary) -> pygame.Surface | None:
        cache_key = (card.data_id, card.form)
        if cache_key in self.deck_card_images:
            return self.deck_card_images[cache_key]
        variants = self.icon_paths.get(card.data_id)
        cached_path = (
            Path(card.cached_icon_path)
            if card.cached_icon_path is not None
            else None
        )
        path: Path | None = (
            cached_path
            if cached_path is not None and cached_path.is_file()
            else None
        )
        if path is None and variants is not None and card.form == "hero":
            path = variants.get("hero")
        elif path is None and variants is not None and card.form == "evolution":
            for candidate in ("evolution_2_of_2", "evolution_1_of_1"):
                if candidate in variants:
                    path = variants[candidate]
                    break
        elif path is None and variants is not None:
            path = variants.get("normal")
        if path is None and variants is not None:
            path = variants.get("normal")
        if path is None:
            return None
        image = pygame.image.load(str(path)).convert_alpha()
        bounds = image.get_bounding_rect()
        if bounds.width and bounds.height:
            image = image.subsurface(bounds)
        target_height = 43
        target_width = max(1, round(image.get_width() * target_height / image.get_height()))
        if target_width > 31:
            target_width = 31
        fitted = pygame.transform.smoothscale(image, (target_width, target_height))
        self.deck_card_images[cache_key] = fitted
        return fitted

    def _draw_info_panel(self, surface: pygame.Surface) -> None:
        panel = pygame.Surface(CONTENT_RECT.size, pygame.SRCALPHA)
        _vertical_gradient(panel, panel.get_rect(), (8, 74, 137), (3, 35, 84))
        panel.fill((4, 28, 68, 32), special_flags=pygame.BLEND_RGBA_ADD)
        surface.blit(panel, CONTENT_RECT)
        pygame.draw.line(surface, (39, 145, 220), CONTENT_RECT.topleft, CONTENT_RECT.bottomleft)

    def _draw_loading(self, surface: pygame.Surface) -> None:
        center = (CONTENT_RECT.centerx, CONTENT_RECT.centery - 12)
        radius = 18
        start = (pygame.time.get_ticks() // 6) % 360
        for index in range(10):
            angle = (start + index * 36) * 3.141592653589793 / 180
            alpha = 45 + index * 20
            point = (
                round(center[0] + radius * pygame.math.Vector2(1, 0).rotate_rad(angle).x),
                round(center[1] + radius * pygame.math.Vector2(1, 0).rotate_rad(angle).y),
            )
            pygame.draw.circle(surface, (104, 205, 255, min(alpha, 255)), point, 3)
        label = self.info_body_font.render("正在查询对手信息…", True, (218, 239, 255))
        surface.blit(label, label.get_rect(midtop=(center[0], center[1] + 30)))

    def _draw_info_error(self, surface: pygame.Surface, message: str | None) -> None:
        title = self.info_title_font.render("查询失败", True, (255, 211, 111))
        detail = self.info_body_font.render(message or "暂时无法取得玩家资料", True, (220, 235, 250))
        hint = self.info_small_font.render("返回卡牌页后再次点击即可重试", True, (149, 188, 220))
        center_x = CONTENT_RECT.centerx
        surface.blit(title, title.get_rect(center=(center_x, 135)))
        surface.blit(detail, detail.get_rect(center=(center_x, 169)))
        surface.blit(hint, hint.get_rect(center=(center_x, 198)))

    def _draw_opponent_info(self, surface: pygame.Surface, state: OpponentInfoState) -> None:
        self._draw_info_panel(surface)
        if state.status in ("idle", "loading"):
            self._draw_loading(surface)
            return
        if state.status == "error" or state.info is None:
            self._draw_info_error(surface, state.error)
            return

        info = state.info
        header = pygame.Rect(94, 10, 394, 61)
        pygame.draw.rect(surface, (3, 43, 91), header, border_radius=9)
        pygame.draw.rect(surface, (50, 156, 222), header, 1, border_radius=9)
        name = self.info_title_font.render(info.name, True, (250, 253, 255))
        tag = self.info_small_font.render(info.tag, True, (146, 207, 244))
        clan_text = info.clan_name or "无部落"
        clan = self.info_small_font.render(clan_text, True, (190, 219, 240))
        surface.blit(name, (header.x + 11, header.y + 7))
        surface.blit(tag, (header.x + 11, header.y + 33))
        surface.blit(clan, clan.get_rect(right=header.right - 10, top=header.y + 9))
        rating = info.ranked_medals if info.ranked_medals is not None else info.trophies
        rating_label = "天梯奖牌" if info.ranked_medals is not None else "奖杯"
        rating_text = "--" if rating is None else f"{rating:,}"
        rendered_rating = self.info_body_font.render(
            f"{rating_label} {rating_text}", True, (255, 222, 116)
        )
        rating_rect = rendered_rating.get_rect(
            right=header.right - 10, top=header.y + 34
        )
        surface.blit(rendered_rating, rating_rect)

        record = pygame.Rect(94, 79, 394, 68)
        pygame.draw.rect(surface, (6, 55, 108), record, border_radius=9)
        pygame.draw.rect(surface, (31, 123, 190), record, 1, border_radius=9)
        rate = self.info_stat_font.render(
            f"{info.recent_win_rate * 100:.1f}%", True, (104, 238, 177)
        )
        surface.blit(rate, rate.get_rect(midleft=(record.x + 13, record.centery - 4)))
        recent_label = self.info_small_font.render(
            f"近 {info.recent_games} 场胜率", True, (175, 214, 241)
        )
        surface.blit(recent_label, (record.x + 13, record.bottom - 20))
        wins = self.info_body_font.render(f"胜  {info.recent_wins}", True, (101, 232, 169))
        losses = self.info_body_font.render(f"负  {info.recent_losses}", True, (255, 133, 139))
        surface.blit(wins, (record.x + 230, record.y + 13))
        surface.blit(losses, (record.x + 310, record.y + 13))
        source = self.info_small_font.render(f"数据来源：{info.source}", True, (129, 177, 211))
        surface.blit(source, source.get_rect(right=record.right - 10, bottom=record.bottom - 8))

        section = self.info_body_font.render(
            "近期常用卡组 · 皇冠仅统计1v1", True, (229, 243, 255)
        )
        surface.blit(section, (96, 154))
        for index, deck in enumerate(info.decks[:3]):
            row = pygame.Rect(94, 177 + index * 52, 394, 47)
            pygame.draw.rect(surface, (4, 43, 88), row, border_radius=7)
            pygame.draw.rect(surface, (20, 99, 161), row, 1, border_radius=7)
            x = row.x + 7
            for card in deck.cards[:8]:
                image = self._deck_card_image(card)
                if image is None:
                    placeholder = pygame.Rect(x, row.y + 5, 28, 37)
                    pygame.draw.rect(surface, (8, 63, 113), placeholder, border_radius=4)
                    pygame.draw.rect(surface, (49, 129, 184), placeholder, 1, border_radius=4)
                    x += 31
                    continue
                image_rect = image.get_rect(midbottom=(x + 14, row.bottom - 2))
                surface.blit(image, image_rect)
                x += 31
            rate_text = self.info_body_font.render(
                (
                    f"{deck.win_rate * 100:.0f}% · "
                    f"{deck.average_elixir:.1f}费"
                    if deck.average_elixir is not None
                    else f"{deck.win_rate * 100:.0f}%"
                ),
                True,
                (255, 226, 116),
            )
            crowns_text = (
                f"{deck.average_crowns_1v1:.1f}冠"
                if deck.average_crowns_1v1 is not None
                else "--冠"
            )
            record_text = self.info_small_font.render(
                f"{deck.wins}胜{deck.losses}负 · {crowns_text}",
                True,
                (172, 207, 232),
            )
            surface.blit(rate_text, rate_text.get_rect(right=row.right - 9, top=row.y + 5))
            record_rect = record_text.get_rect(
                right=row.right - 9, bottom=row.bottom - 6
            )
            surface.blit(record_text, record_rect)

    def draw(
        self,
        surface: pygame.Surface,
        snapshot: dict[str, Any] | None,
        *,
        page: str = "cards",
        opponent_info_state: OpponentInfoState | None = None,
    ) -> None:
        self._draw_background(surface)
        self._draw_sidebar(surface)
        if page == "opponent_info":
            state = opponent_info_state or OpponentInfoState(status="loading")
            self._draw_opponent_info(surface, state)
            return
        cards = snapshot.get("opponent_cards", []) if snapshot is not None else []
        if not isinstance(cards, list):
            cards = []
        cards = [item for item in cards if isinstance(item, dict)]
        self._draw_cards(surface, cards)
        raw_elixir = snapshot.get("opponent_elixir") if snapshot is not None else None
        elixir = raw_elixir if isinstance(raw_elixir, int) else None
        self._draw_status(surface, elixir, average_revealed_cost(cards))


class OpponentCardBarController:
    def __init__(self, opponent_info: OpponentInfoController | None = None) -> None:
        self.page = "cards"
        self.opponent_info = opponent_info or OpponentInfoController()

    def handle_sidebar_action(self, action: str | None) -> bool:
        if action != "opponent_info":
            return False
        if self.page == "opponent_info":
            self.page = "cards"
        else:
            self.page = "opponent_info"
            self.opponent_info.request()
        return True


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
    controller = OpponentCardBarController()
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
                if renderer.press_sidebar(event.pos):
                    drag_origin = None
                else:
                    drag_origin = (_cursor_position(), window.position)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                controller.handle_sidebar_action(renderer.release_sidebar(event.pos))
                drag_origin = None

        if drag_origin is not None and pygame.mouse.get_pressed(num_buttons=3)[0]:
            cursor_start, window_start = drag_origin
            cursor = _cursor_position()
            window.position = _dragged_window_position(cursor_start, window_start, cursor)

        renderer.draw(
            screen,
            snapshot_provider(),
            page=controller.page,
            opponent_info_state=controller.opponent_info.current(),
        )
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
