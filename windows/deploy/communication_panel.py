from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pygame

from emote_blacklist_ui import load_blacklist, save_blacklist


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSET_ROOT = PROJECT_ROOT / "resources" / "ui" / "opponent_card_bar" / "communication"
CATALOG_PATH = PROJECT_ROOT / "resources" / "emotes.json"
BLACKLIST_PATH = PROJECT_ROOT / "resources" / "emote_blacklist.json"
ATLAS_PATH = ASSET_ROOT / "emotes_atlas.png"
MANIFEST_PATH = ASSET_ROOT / "emotes_atlas.json"

EMOTE_VIEWPORT = pygame.Rect(90, 11, 402, 238)
SCROLL_TRACK = pygame.Rect(96, 252, 390, 7)
TEXT_AREA = pygame.Rect(91, 268, 400, 65)
EMOTE_ROWS = 4
TILE_SIZE = (66, 53)
TILE_GAP = (5, 5)
TILE_PITCH = (TILE_SIZE[0] + TILE_GAP[0], TILE_SIZE[1] + TILE_GAP[1])
TEXT_LABELS = (
    ("Taunt1", "祝你好运！"),
    ("Taunt2", "厉害！"),
    ("Taunt3", "哇哦！"),
    ("Taunt4", "承让！"),
    ("Taunt5", "精彩的比赛！"),
    ("Taunt6", "哎呦"),
)


@dataclass(frozen=True)
class EmoteThumbnail:
    identifier: str
    name: str
    rect: pygame.Rect


def text_button_rect(index: int) -> pygame.Rect:
    column = index % 3
    row = index // 3
    return pygame.Rect(96 + column * 132, 268 + row * 34, 124, 29)


def _font(size: int, *, bold: bool = False) -> pygame.font.Font:
    paths = (
        Path("C:/Windows/Fonts/msyhbd.ttc") if bold else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    )
    for path in paths:
        if path.is_file():
            return pygame.font.Font(str(path), size)
    return pygame.font.Font(None, size)


class CommunicationPanel:
    def __init__(
        self,
        *,
        manifest_path: Path = MANIFEST_PATH,
        atlas_path: Path = ATLAS_PATH,
        blacklist_path: Path = BLACKLIST_PATH,
    ) -> None:
        self.blacklist_path = blacklist_path
        self.emotes = self._load_manifest(manifest_path)
        self.atlas = pygame.image.load(str(atlas_path)).convert_alpha()
        self.blocked_emotes, self.blocked_texts = load_blacklist(blacklist_path)
        self.scroll_x = 0.0
        self._pressed: tuple[str, str] | None = None
        self._press_position: tuple[int, int] | None = None
        self._press_scroll = 0.0
        self._dragged = False
        self.label_font = _font(12, bold=True)
        self.hint_font = _font(10)

    @staticmethod
    def _load_manifest(path: Path) -> list[EmoteThumbnail]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = []
        for item in payload.get("entries", []):
            if not isinstance(item, dict) or item.get("available") is False:
                continue
            rect = item.get("rect")
            if not isinstance(item.get("id"), str) or not (
                isinstance(rect, list) and len(rect) == 4
            ):
                continue
            result.append(
                EmoteThumbnail(
                    item["id"],
                    str(item.get("name") or item["id"]),
                    pygame.Rect(*(int(value) for value in rect)),
                )
            )
        return result

    @property
    def column_count(self) -> int:
        return (len(self.emotes) + EMOTE_ROWS - 1) // EMOTE_ROWS

    @property
    def max_scroll(self) -> float:
        content_width = self.column_count * TILE_PITCH[0] - TILE_GAP[0]
        return float(max(0, content_width - EMOTE_VIEWPORT.width))

    def set_scroll(self, value: float) -> None:
        self.scroll_x = max(0.0, min(self.max_scroll, float(value)))

    def scroll(self, amount: float) -> bool:
        before = self.scroll_x
        self.set_scroll(before + amount)
        return self.scroll_x != before

    def wheel(self, position: tuple[int, int], amount: float) -> bool:
        return EMOTE_VIEWPORT.collidepoint(position) and self.scroll(amount)

    def _emote_tile_rect(self, index: int) -> pygame.Rect:
        column, row = divmod(index, EMOTE_ROWS)
        return pygame.Rect(
            round(EMOTE_VIEWPORT.left + column * TILE_PITCH[0] - self.scroll_x),
            EMOTE_VIEWPORT.top + row * TILE_PITCH[1],
            *TILE_SIZE,
        )

    def item_at(self, position: tuple[int, int]) -> tuple[str, str] | None:
        if EMOTE_VIEWPORT.collidepoint(position):
            local_x = position[0] - EMOTE_VIEWPORT.left + self.scroll_x
            local_y = position[1] - EMOTE_VIEWPORT.top
            column = int(local_x // TILE_PITCH[0])
            row = int(local_y // TILE_PITCH[1])
            if local_x % TILE_PITCH[0] < TILE_SIZE[0] and local_y % TILE_PITCH[1] < TILE_SIZE[1]:
                index = column * EMOTE_ROWS + row
                if 0 <= index < len(self.emotes):
                    return "animated", self.emotes[index].identifier
        for index, (identifier, _label) in enumerate(TEXT_LABELS):
            if text_button_rect(index).collidepoint(position):
                return "text", identifier
        return None

    def press(self, position: tuple[int, int]) -> bool:
        if not (EMOTE_VIEWPORT.collidepoint(position) or TEXT_AREA.collidepoint(position)):
            return False
        self._pressed = self.item_at(position)
        self._press_position = position
        self._press_scroll = self.scroll_x
        self._dragged = False
        return True

    def drag(self, position: tuple[int, int]) -> bool:
        if self._press_position is None or not EMOTE_VIEWPORT.collidepoint(self._press_position):
            return False
        distance = position[0] - self._press_position[0]
        if abs(distance) >= 3:
            self._dragged = True
        self.set_scroll(self._press_scroll - distance)
        return True

    def release(self, position: tuple[int, int]) -> bool:
        pressed = self._pressed
        toggled = False
        if pressed is not None and not self._dragged and self.item_at(position) == pressed:
            self.toggle(*pressed)
            toggled = True
        self.cancel_pointer()
        return toggled

    def cancel_pointer(self) -> None:
        self._pressed = None
        self._press_position = None
        self._dragged = False

    def toggle(self, kind: str, identifier: str) -> None:
        target = self.blocked_emotes if kind == "animated" else self.blocked_texts
        if identifier in target:
            target.remove(identifier)
        else:
            target.add(identifier)
        save_blacklist(self.blocked_emotes, self.blocked_texts, self.blacklist_path)

    @staticmethod
    def _draw_prohibited(surface: pygame.Surface, center: tuple[int, int], radius: int) -> None:
        pygame.draw.circle(surface, (238, 48, 52), center, radius, 4)
        diagonal = round(radius * 0.68)
        pygame.draw.line(
            surface,
            (238, 48, 52),
            (center[0] - diagonal, center[1] - diagonal),
            (center[0] + diagonal, center[1] + diagonal),
            4,
        )

    def _draw_blocked_overlay(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        overlay.fill((85, 85, 85, 158))
        surface.blit(overlay, rect)
        self._draw_prohibited(surface, rect.center, min(rect.width, rect.height) // 4)

    def draw(self, surface: pygame.Surface) -> None:
        panel = pygame.Surface((418, 340), pygame.SRCALPHA)
        panel.fill((7, 59, 112, 220))
        surface.blit(panel, (82, 0))

        pygame.draw.rect(surface, (3, 34, 72), EMOTE_VIEWPORT.inflate(4, 4), border_radius=10)
        pygame.draw.rect(surface, (17, 103, 164), EMOTE_VIEWPORT, border_radius=8)
        old_clip = surface.get_clip()
        surface.set_clip(EMOTE_VIEWPORT)
        first_column = max(0, int(self.scroll_x // TILE_PITCH[0]))
        last_column = min(self.column_count, first_column + 7)
        for column in range(first_column, last_column):
            for row in range(EMOTE_ROWS):
                index = column * EMOTE_ROWS + row
                if index >= len(self.emotes):
                    break
                entry = self.emotes[index]
                tile = self._emote_tile_rect(index)
                pygame.draw.rect(surface, (246, 249, 251), tile, border_radius=9)
                pygame.draw.rect(surface, (65, 198, 229), tile, 2, border_radius=9)
                image = self.atlas.subsurface(entry.rect)
                image_rect = image.get_rect(center=tile.center)
                surface.blit(image, image_rect)
                if entry.identifier in self.blocked_emotes:
                    self._draw_blocked_overlay(surface, tile)
        surface.set_clip(old_clip)

        pygame.draw.rect(surface, (3, 34, 72), SCROLL_TRACK, border_radius=4)
        if self.max_scroll > 0:
            thumb_width = max(28, round(SCROLL_TRACK.width * EMOTE_VIEWPORT.width / (EMOTE_VIEWPORT.width + self.max_scroll)))
            thumb_left = SCROLL_TRACK.left + round((SCROLL_TRACK.width - thumb_width) * self.scroll_x / self.max_scroll)
        else:
            thumb_width = SCROLL_TRACK.width
            thumb_left = SCROLL_TRACK.left
        pygame.draw.rect(
            surface,
            (153, 215, 239),
            pygame.Rect(thumb_left, SCROLL_TRACK.top, thumb_width, SCROLL_TRACK.height),
            border_radius=4,
        )

        for index, (identifier, label) in enumerate(TEXT_LABELS):
            rect = text_button_rect(index)
            pygame.draw.rect(surface, (6, 25, 43), rect.inflate(4, 4), border_radius=10)
            pygame.draw.rect(surface, (250, 250, 250), rect, border_radius=8)
            text = self.label_font.render(label, True, (63, 63, 63))
            surface.blit(text, text.get_rect(center=rect.center))
            if identifier in self.blocked_texts:
                self._draw_blocked_overlay(surface, rect)
