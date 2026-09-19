from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import pygame


CONTENT_RECT = pygame.Rect(82, 0, 418, 340)
LANGUAGE_OPTIONS = ("zh-CN", "en-US")
CARD_SORT_OPTIONS = ("deck", "elixir", "rarity")
CARD_IMAGE_QUALITIES = ("standard", "high")


def default_settings_path() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "NRPlusPlus" / "settings.json"
    return Path.home() / ".nrplusplus" / "settings.json"


@dataclass(frozen=True)
class PluginSettings:
    language: str = "zh-CN"
    always_on_top: bool = True
    auto_query_opponent: bool = False
    remember_window_position: bool = False
    auto_hide_outside_game: bool = False
    card_sort_order: str = "deck"
    show_card_details: bool = True
    card_image_quality: str = "standard"
    window_position: tuple[int, int] | None = None

    @classmethod
    def from_payload(cls, payload: object) -> PluginSettings:
        if not isinstance(payload, dict):
            return cls()
        defaults = cls()
        language = payload.get("language")
        card_sort_order = payload.get("card_sort_order")
        card_image_quality = payload.get("card_image_quality")
        position = payload.get("window_position")
        parsed_position = (
            (int(position[0]), int(position[1]))
            if isinstance(position, (list, tuple))
            and len(position) == 2
            and all(isinstance(value, int) for value in position)
            else None
        )
        return cls(
            language=language if language in LANGUAGE_OPTIONS else defaults.language,
            always_on_top=_boolean(payload, "always_on_top", defaults.always_on_top),
            auto_query_opponent=_boolean(
                payload, "auto_query_opponent", defaults.auto_query_opponent
            ),
            remember_window_position=_boolean(
                payload,
                "remember_window_position",
                defaults.remember_window_position,
            ),
            auto_hide_outside_game=_boolean(
                payload, "auto_hide_outside_game", defaults.auto_hide_outside_game
            ),
            card_sort_order=(
                card_sort_order
                if card_sort_order in CARD_SORT_OPTIONS
                else defaults.card_sort_order
            ),
            show_card_details=_boolean(
                payload, "show_card_details", defaults.show_card_details
            ),
            card_image_quality=(
                card_image_quality
                if card_image_quality in CARD_IMAGE_QUALITIES
                else defaults.card_image_quality
            ),
            window_position=parsed_position,
        )


def _boolean(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key)
    return value if isinstance(value, bool) else default


class SettingsStore:
    """Owns validated settings and best-effort JSON persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_settings_path()
        self.current = self._load()
        self.last_error: str | None = None

    def _load(self) -> PluginSettings:
        try:
            return PluginSettings.from_payload(
                json.loads(self.path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError, TypeError):
            return PluginSettings()

    def update(self, **changes: object) -> PluginSettings:
        allowed = set(PluginSettings.__dataclass_fields__)
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown setting: {', '.join(sorted(unknown))}")
        candidate = replace(self.current, **changes)
        self.current = PluginSettings.from_payload(asdict(candidate))
        self._save()
        return self.current

    def reset(self) -> PluginSettings:
        self.current = PluginSettings()
        self._save()
        return self.current

    def _save(self) -> None:
        payload = asdict(self.current)
        if self.current.window_position is not None:
            payload["window_position"] = list(self.current.window_position)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            self.last_error = str(exc)
        else:
            self.last_error = None


TEXT = {
    "zh-CN": {
        "title": "设置",
        "language": "语言",
        "chinese": "简体中文",
        "english": "English",
        "always_on_top": "窗口始终置顶",
        "auto_query_opponent": "自动查询对手信息",
        "remember_window_position": "记住窗口位置",
        "auto_hide_outside_game": "游戏外自动隐藏",
        "card_list": "卡牌列表",
        "card_sort_order": "默认排序方式",
        "sort_deck": "卡组顺序",
        "sort_elixir": "圣水",
        "sort_rarity": "稀有度",
        "show_card_details": "显示平均圣水、卡牌等级等数据",
        "card_image_quality": "卡牌图片质量",
        "quality_standard": "标准",
        "quality_high": "高清",
        "restore_defaults": "恢复默认设置",
        "on": "开",
        "off": "关",
    },
    "en-US": {
        "title": "Settings",
        "language": "Language",
        "chinese": "简体中文",
        "english": "English",
        "always_on_top": "Always on top",
        "auto_query_opponent": "Auto-query opponent",
        "remember_window_position": "Remember window position",
        "auto_hide_outside_game": "Hide outside battles",
        "card_list": "Card list",
        "card_sort_order": "Default order",
        "sort_deck": "Deck",
        "sort_elixir": "Elixir",
        "sort_rarity": "Rarity",
        "show_card_details": "Show elixir, levels and other data",
        "card_image_quality": "Card image quality",
        "quality_standard": "Standard",
        "quality_high": "High",
        "restore_defaults": "Restore defaults",
        "on": "On",
        "off": "Off",
    },
}


def text(language: str, key: str) -> str:
    catalog = TEXT.get(language, TEXT["zh-CN"])
    return catalog.get(key, TEXT["zh-CN"].get(key, key))


class SettingsPanel:
    LANGUAGE_RECT = pygame.Rect(98, 55, 384, 47)
    LANGUAGE_BUTTONS = {
        "zh-CN": pygame.Rect(282, 63, 92, 31),
        "en-US": pygame.Rect(378, 63, 92, 31),
    }
    CARD_LIST_RECT = pygame.Rect(98, 107, 384, 34)
    CARD_DROPDOWN_RECT = pygame.Rect(98, 145, 384, 143)
    CARD_SORT_RECTS = {
        "deck": pygame.Rect(272, 151, 64, 27),
        "elixir": pygame.Rect(340, 151, 64, 27),
        "rarity": pygame.Rect(408, 151, 64, 27),
    }
    CARD_DETAILS_RECT = pygame.Rect(106, 184, 368, 40)
    CARD_QUALITY_RECTS = {
        "standard": pygame.Rect(334, 235, 65, 27),
        "high": pygame.Rect(403, 235, 69, 27),
    }
    TOGGLE_KEYS = (
        "always_on_top",
        "auto_query_opponent",
        "remember_window_position",
        "auto_hide_outside_game",
    )
    TOGGLE_RECTS = {
        key: pygame.Rect(98, 148 + index * 35, 384, 31)
        for index, key in enumerate(TOGGLE_KEYS)
    }
    RESET_RECT = pygame.Rect(213, 301, 156, 27)

    def __init__(self, store: SettingsStore) -> None:
        self.store = store
        self._pressed: tuple[str, str | None] | None = None
        self.card_list_open = False
        self.title_font = _font(20, bold=True)
        self.label_font = _font(14)
        self.small_font = _font(12, bold=True)

    def item_at(self, position: tuple[int, int]) -> tuple[str, str | None] | None:
        for language, rect in self.LANGUAGE_BUTTONS.items():
            if rect.collidepoint(position):
                return "language", language
        if self.CARD_LIST_RECT.collidepoint(position):
            return "card_list", None
        if self.card_list_open:
            for value, rect in self.CARD_SORT_RECTS.items():
                if rect.collidepoint(position):
                    return "card_sort_order", value
            if self.CARD_DETAILS_RECT.collidepoint(position):
                return "card_details", None
            for value, rect in self.CARD_QUALITY_RECTS.items():
                if rect.collidepoint(position):
                    return "card_image_quality", value
            if self.CARD_DROPDOWN_RECT.collidepoint(position):
                return "card_dropdown", None
        for key, rect in self.TOGGLE_RECTS.items():
            if rect.collidepoint(position):
                return "toggle", key
        if self.RESET_RECT.collidepoint(position):
            return "reset", None
        return None

    def press(self, position: tuple[int, int]) -> bool:
        self._pressed = self.item_at(position)
        return self._pressed is not None

    def release(self, position: tuple[int, int]) -> bool:
        item = self.item_at(position)
        changed = item is not None and item == self._pressed
        self._pressed = None
        if not changed:
            return False
        kind, value = item
        if kind == "language" and value is not None:
            self.store.update(language=value)
        elif kind == "toggle" and value is not None:
            self.store.update(**{value: not getattr(self.store.current, value)})
        elif kind == "card_list":
            self.card_list_open = not self.card_list_open
        elif kind == "card_sort_order" and value is not None:
            self.store.update(card_sort_order=value)
        elif kind == "card_details":
            self.store.update(show_card_details=not self.store.current.show_card_details)
        elif kind == "card_image_quality" and value is not None:
            self.store.update(card_image_quality=value)
        elif kind == "reset":
            self.store.reset()
            self.card_list_open = False
        return True

    def cancel_pointer(self) -> None:
        self._pressed = None

    def draw(self, surface: pygame.Surface) -> None:
        settings = self.store.current
        language = settings.language
        panel = pygame.Surface(CONTENT_RECT.size, pygame.SRCALPHA)
        panel.fill((7, 59, 112, 224))
        surface.blit(panel, CONTENT_RECT)

        title = self.title_font.render(text(language, "title"), True, (242, 249, 255))
        surface.blit(title, (98, 18))
        pygame.draw.line(surface, (42, 145, 207), (98, 47), (482, 47), 1)

        self._draw_row(surface, self.LANGUAGE_RECT)
        label = self.label_font.render(text(language, "language"), True, (224, 240, 252))
        surface.blit(label, (110, 69))
        for option, rect in self.LANGUAGE_BUTTONS.items():
            selected = settings.language == option
            pygame.draw.rect(
                surface,
                (31, 143, 207) if selected else (8, 49, 89),
                rect,
                border_radius=7,
            )
            pygame.draw.rect(surface, (84, 186, 232), rect, 1, border_radius=7)
            key = "chinese" if option == "zh-CN" else "english"
            rendered = self.small_font.render(text(language, key), True, (244, 250, 255))
            surface.blit(rendered, rendered.get_rect(center=rect.center))

        self._draw_row(surface, self.CARD_LIST_RECT)
        card_label = self.label_font.render(
            text(language, "card_list"), True, (224, 240, 252)
        )
        surface.blit(
            card_label,
            (self.CARD_LIST_RECT.left + 12, self.CARD_LIST_RECT.centery - card_label.get_height() // 2),
        )
        summary = self.small_font.render(
            " · ".join(
                (
                    text(language, f"sort_{settings.card_sort_order}"),
                    text(language, f"quality_{settings.card_image_quality}"),
                )
            ),
            True,
            (145, 195, 225),
        )
        surface.blit(
            summary,
            summary.get_rect(right=self.CARD_LIST_RECT.right - 29, centery=self.CARD_LIST_RECT.centery),
        )
        arrow_center = (self.CARD_LIST_RECT.right - 14, self.CARD_LIST_RECT.centery)
        if self.card_list_open:
            arrow_points = (
                (arrow_center[0] - 5, arrow_center[1] + 3),
                (arrow_center[0] + 5, arrow_center[1] + 3),
                (arrow_center[0], arrow_center[1] - 3),
            )
        else:
            arrow_points = (
                (arrow_center[0] - 5, arrow_center[1] - 3),
                (arrow_center[0] + 5, arrow_center[1] - 3),
                (arrow_center[0], arrow_center[1] + 3),
            )
        pygame.draw.polygon(surface, (173, 218, 241), arrow_points)

        for key, rect in self.TOGGLE_RECTS.items():
            self._draw_row(surface, rect)
            label = self.label_font.render(text(language, key), True, (224, 240, 252))
            surface.blit(label, (rect.left + 12, rect.centery - label.get_height() // 2))
            self._draw_switch(surface, rect, bool(getattr(settings, key)), language)

        if self.card_list_open:
            self._draw_card_dropdown(surface, settings, language)

        pressed = self._pressed == ("reset", None)
        pygame.draw.rect(
            surface,
            (111, 47, 57) if pressed else (31, 75, 112),
            self.RESET_RECT,
            border_radius=8,
        )
        pygame.draw.rect(surface, (220, 109, 112), self.RESET_RECT, 1, border_radius=8)
        reset = self.label_font.render(text(language, "restore_defaults"), True, (255, 234, 234))
        surface.blit(reset, reset.get_rect(center=self.RESET_RECT.center))

    def _draw_card_dropdown(
        self,
        surface: pygame.Surface,
        settings: PluginSettings,
        language: str,
    ) -> None:
        pygame.draw.rect(
            surface, (3, 31, 65), self.CARD_DROPDOWN_RECT, border_radius=9
        )
        pygame.draw.rect(
            surface, (56, 158, 213), self.CARD_DROPDOWN_RECT, 2, border_radius=9
        )

        sort_label = self.small_font.render(
            text(language, "card_sort_order"), True, (215, 235, 248)
        )
        surface.blit(sort_label, (110, 158))
        for value, rect in self.CARD_SORT_RECTS.items():
            self._draw_choice_button(
                surface,
                rect,
                text(language, f"sort_{value}"),
                value == settings.card_sort_order,
            )

        pygame.draw.rect(
            surface, (6, 48, 87), self.CARD_DETAILS_RECT, border_radius=7
        )
        details = self.small_font.render(
            text(language, "show_card_details"), True, (215, 235, 248)
        )
        surface.blit(
            details,
            (self.CARD_DETAILS_RECT.left + 8, self.CARD_DETAILS_RECT.centery - details.get_height() // 2),
        )
        self._draw_switch(
            surface,
            self.CARD_DETAILS_RECT,
            settings.show_card_details,
            language,
        )

        quality = self.small_font.render(
            text(language, "card_image_quality"), True, (215, 235, 248)
        )
        surface.blit(quality, (110, 242))
        for value, rect in self.CARD_QUALITY_RECTS.items():
            self._draw_choice_button(
                surface,
                rect,
                text(language, f"quality_{value}"),
                value == settings.card_image_quality,
            )

    def _draw_choice_button(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        label: str,
        selected: bool,
    ) -> None:
        pygame.draw.rect(
            surface,
            (32, 145, 207) if selected else (11, 59, 99),
            rect,
            border_radius=6,
        )
        pygame.draw.rect(surface, (82, 183, 226), rect, 1, border_radius=6)
        rendered = self.small_font.render(label, True, (243, 249, 253))
        surface.blit(rendered, rendered.get_rect(center=rect.center))

    @staticmethod
    def _draw_row(surface: pygame.Surface, rect: pygame.Rect) -> None:
        pygame.draw.rect(surface, (5, 42, 82), rect, border_radius=8)
        pygame.draw.rect(surface, (28, 112, 174), rect, 1, border_radius=8)

    def _draw_switch(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        enabled: bool,
        language: str,
    ) -> None:
        switch = pygame.Rect(rect.right - 74, rect.centery - 12, 62, 24)
        pygame.draw.rect(
            surface,
            (35, 167, 118) if enabled else (40, 64, 87),
            switch,
            border_radius=12,
        )
        knob_x = switch.right - 12 if enabled else switch.left + 12
        pygame.draw.circle(surface, (244, 249, 252), (knob_x, switch.centery), 9)
        state_key = "on" if enabled else "off"
        state = self.small_font.render(text(language, state_key), True, (174, 214, 236))
        surface.blit(state, state.get_rect(right=switch.left - 7, centery=switch.centery))


def _font(size: int, *, bold: bool = False) -> pygame.font.Font:
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf") if bold else Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return pygame.font.Font(str(path), size)
    return pygame.font.Font(None, size)
