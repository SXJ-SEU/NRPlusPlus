from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pygame


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CARD_CATALOG = PROJECT_ROOT / "deploy" / "cards.json"
LOG_RECT = pygame.Rect(92, 10, 396, 320)
LOG_HEADER_RECT = pygame.Rect(92, 10, 396, 38)
LOG_BODY_RECT = pygame.Rect(100, 55, 380, 265)
ROW_HEIGHT = 25
VISIBLE_ROWS = LOG_BODY_RECT.height // ROW_HEIGHT
HERO_ENTITY_CARD_BASE = 203_000_000
HERO_ENTITY_CARD_LIMIT = 204_000_000
HERO_DECK_CARD_BASE = 26_000_000


def _font(size: int, *, bold: bool = False) -> pygame.font.Font:
    candidates = (
        Path("C:/Windows/Fonts/msyhbd.ttc")
        if bold
        else Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/arialbd.ttf")
        if bold
        else Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return pygame.font.Font(str(path), size)
    return pygame.font.Font(None, size)


@dataclass(frozen=True)
class BattleLogEntry:
    elapsed_ms: int
    kind: str
    side: str | None = None
    card_id: int | None = None
    card_name: str | None = None
    form: str | None = None
    elixir_cost: int | float | None = None
    elixir_before: int | None = None
    elixir_after: int | None = None
    local_damage: int = 0
    opponent_damage: int = 0
    target_side: str | None = None
    target_name: str | None = None
    target_kind: str | None = None
    damage: int = 0


@dataclass(frozen=True)
class _CardDefinition:
    name: str
    elixir_cost: int | float | None


def _timestamp(elapsed_ms: int) -> str:
    total_seconds = max(0, elapsed_ms) // 1_000
    return f"[{total_seconds // 60:02d}:{total_seconds % 60:02d}]"


def _number(value: int | float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def format_entry(entry: BattleLogEntry, language: str = "zh-CN") -> str:
    timestamp = _timestamp(entry.elapsed_ms)
    if entry.kind == "damage_target":
        target_name = entry.target_name or "Unknown Unit"
        if language == "en-US":
            actor = "You" if entry.side == "local" else "Opponent"
            target_owner = "Your" if entry.target_side == "local" else "Opponent"
            return (
                f"{timestamp} {actor} → {target_owner} {target_name}: "
                f"{entry.damage} damage"
            )
        target_name = {
            "king_tower": "国王塔",
            "left_princess_tower": "左侧公主塔",
            "right_princess_tower": "右侧公主塔",
            "unknown": "未知单位",
        }.get(entry.target_kind, target_name)
        actor = "我方" if entry.side == "local" else "对方"
        target_owner = "我方" if entry.target_side == "local" else "对方"
        return (
            f"{timestamp} {actor} → {target_owner}{target_name}："
            f"造成 {entry.damage} 点伤害"
        )
    if entry.kind == "damage_summary":
        if language == "en-US":
            return (
                f"{timestamp} Damage: you {entry.local_damage}, "
                f"opponent {entry.opponent_damage}"
            )
        return (
            f"{timestamp} 伤害汇总：我方造成 {entry.local_damage}，"
            f"对方造成 {entry.opponent_damage}"
        )

    name = entry.card_name or (
        f"#{entry.card_id}" if entry.card_id is not None else "Unknown"
    )
    if language == "en-US":
        form = {"evolution": "Evolved ", "hero": "Hero "}.get(
            entry.form, ""
        )
        actor = "You" if entry.side == "local" else "Opponent"
        text = f"{timestamp} {actor} played {form}{name}"
        if entry.elixir_cost is not None:
            text += f", cost {_number(entry.elixir_cost)} elixir"
        if entry.elixir_before is not None and entry.elixir_after is not None:
            text += f" ({entry.elixir_before} → {entry.elixir_after})"
        return text

    form = {"evolution": "进化 ", "hero": "英雄 "}.get(entry.form, "")
    actor = "我方" if entry.side == "local" else "对方"
    text = f"{timestamp} {actor}使用了{form}{name}"
    if entry.elixir_cost is not None:
        text += f"，消耗 {_number(entry.elixir_cost)} 圣水"
    if entry.elixir_before is not None and entry.elixir_after is not None:
        text += f"（{entry.elixir_before} → {entry.elixir_after}）"
    return text


class BattleLogPanel:
    """Turns the current battle snapshot stream into a readable event log."""

    def __init__(self, card_catalog: Path = CARD_CATALOG) -> None:
        self._cards = self._load_cards(card_catalog)
        self._entries: list[BattleLogEntry] = []
        self._seen_card_events: set[tuple[object, ...]] = set()
        self._elixir_samples: deque[tuple[int, int | None, int | None]] = deque()
        self._battle_start_ms: int | None = None
        self._battle_was_active = False
        self._has_battle = False
        self._entity_health: dict[object, tuple[int, int, int]] = {}
        self._damage_by_actor = {"local": 0, "opponent": 0}
        self._damage_by_target: dict[tuple[str, str, str, str, str], int] = {}
        self._next_damage_summary_ms = 3_000
        self._scroll_index = 0
        self._follow_latest = True
        self._title_font = _font(17, bold=True)
        self._body_font = _font(13)
        self._english_font = _font(12)
        self._empty_font = _font(14)

    @staticmethod
    def _load_cards(path: Path) -> dict[int, _CardDefinition]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("card catalog must contain an items list")
        result: dict[int, _CardDefinition] = {}
        for item in payload["items"]:
            if not isinstance(item, dict):
                continue
            data_id = item.get("id")
            name = item.get("name")
            cost = item.get("elixirCost")
            if not isinstance(data_id, int) or not isinstance(name, str):
                continue
            result[data_id] = _CardDefinition(
                name=name,
                elixir_cost=cost if isinstance(cost, (int, float)) else None,
            )
        return result

    @property
    def entries(self) -> tuple[BattleLogEntry, ...]:
        return tuple(self._entries)

    def _start_battle(self, observed_ms: int) -> None:
        self._entries.clear()
        self._seen_card_events.clear()
        self._elixir_samples.clear()
        self._battle_start_ms = observed_ms
        self._has_battle = True
        self._entity_health.clear()
        self._damage_by_actor = {"local": 0, "opponent": 0}
        self._damage_by_target.clear()
        self._next_damage_summary_ms = 3_000
        self._scroll_index = 0
        self._follow_latest = True

    def _flush_damage_summaries(self, elapsed_ms: int) -> None:
        while elapsed_ms >= self._next_damage_summary_ms:
            local_damage = self._damage_by_actor["local"]
            opponent_damage = self._damage_by_actor["opponent"]
            if local_damage or opponent_damage:
                for (
                    actor,
                    target_side,
                    target_kind,
                    target_name,
                    _,
                ), damage in sorted(self._damage_by_target.items()):
                    self._entries.append(
                        BattleLogEntry(
                            elapsed_ms=self._next_damage_summary_ms,
                            kind="damage_target",
                            side=actor,
                            target_side=target_side,
                            target_name=target_name,
                            target_kind=target_kind,
                            damage=damage,
                        )
                    )
                self._entries.append(
                    BattleLogEntry(
                        elapsed_ms=self._next_damage_summary_ms,
                        kind="damage_summary",
                        local_damage=local_damage,
                        opponent_damage=opponent_damage,
                    )
                )
            self._damage_by_actor = {"local": 0, "opponent": 0}
            self._damage_by_target.clear()
            self._next_damage_summary_ms += 3_000

    def _observe_entity_health(
        self,
        entities: object,
        local_side: object,
    ) -> None:
        if not isinstance(entities, list) or local_side not in (0, 1):
            return
        current: dict[object, tuple[int, int, int]] = {}
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            address = entity.get("address")
            side = entity.get("side")
            hp = entity.get("hp")
            max_hp = entity.get("max_hp")
            if (
                not isinstance(address, (int, str))
                or side not in (0, 1)
                or not isinstance(hp, int)
                or not isinstance(max_hp, int)
                or not 0 <= hp <= max_hp
            ):
                continue
            current[address] = (side, hp, max_hp)
            previous = self._entity_health.get(address)
            if previous is None or previous[0] != side or previous[2] != max_hp:
                continue
            health_loss = previous[1] - hp
            if health_loss <= 0:
                continue
            actor = "opponent" if side == local_side else "local"
            target_side = "local" if side == local_side else "opponent"
            target_kind, target_name, target_group = self._target_description(
                entity, address
            )
            self._damage_by_actor[actor] += health_loss
            target_key = (
                actor,
                target_side,
                target_kind,
                target_name,
                target_group,
            )
            self._damage_by_target[target_key] = (
                self._damage_by_target.get(target_key, 0) + health_loss
            )
        self._entity_health = current

    def _target_description(
        self,
        entity: dict[str, Any],
        address: object,
    ) -> tuple[str, str, str]:
        card_id = entity.get("card_id")
        if isinstance(card_id, int):
            catalog_id = (
                HERO_DECK_CARD_BASE + card_id - HERO_ENTITY_CARD_BASE
                if HERO_ENTITY_CARD_BASE <= card_id < HERO_ENTITY_CARD_LIMIT
                else card_id
            )
            definition = self._cards.get(catalog_id)
            name = definition.name if definition is not None else f"#{catalog_id}"
            return "card", name, f"card:{catalog_id}"

        x = entity.get("x")
        if entity.get("kind") == 13 and isinstance(x, int):
            if 8_000 <= x <= 10_000:
                return "king_tower", "King Tower", "tower:king"
            if x < 9_000:
                return (
                    "left_princess_tower",
                    "Left Princess Tower",
                    "tower:princess:left",
                )
            return (
                "right_princess_tower",
                "Right Princess Tower",
                "tower:princess:right",
            )
        return "unknown", "Unknown Unit", f"unknown:{address}"

    def _sampled_elixir(
        self,
        side: str,
        observed_after_ms: int,
        observed_ms: int,
    ) -> tuple[int | None, int | None]:
        sample_index = 1 if side == "local" else 2
        before: int | None = None
        after: int | None = None
        for sample in self._elixir_samples:
            sample_ms = sample[0]
            value = sample[sample_index]
            if sample_ms <= observed_after_ms and value is not None:
                before = value
            if sample_ms >= observed_ms and value is not None:
                after = value
                break
        if after is None and self._elixir_samples:
            after = self._elixir_samples[-1][sample_index]
        return before, after

    def observe(self, snapshot: dict[str, Any] | None) -> None:
        active = bool(snapshot and snapshot.get("battle_active") is True)
        if not active:
            self._battle_was_active = False
            return
        assert snapshot is not None
        observed_ms = snapshot.get("t_ms")
        if not isinstance(observed_ms, int):
            return
        if not self._battle_was_active:
            self._start_battle(observed_ms)
        self._battle_was_active = True
        battle_start_ms = (
            observed_ms if self._battle_start_ms is None else self._battle_start_ms
        )
        elapsed_ms = max(0, observed_ms - battle_start_ms)
        self._flush_damage_summaries(elapsed_ms)
        self._observe_entity_health(
            snapshot.get("entities"), snapshot.get("local_side")
        )

        own_elixir = snapshot.get("own_elixir")
        opponent_elixir = snapshot.get("opponent_elixir")
        self._elixir_samples.append(
            (
                observed_ms,
                own_elixir if isinstance(own_elixir, int) else None,
                opponent_elixir if isinstance(opponent_elixir, int) else None,
            )
        )
        while self._elixir_samples and observed_ms - self._elixir_samples[0][0] > 10_000:
            self._elixir_samples.popleft()

        events = snapshot.get("card_play_events")
        if not isinstance(events, list):
            events = []
        for event in events:
            if not isinstance(event, dict):
                continue
            side = event.get("side")
            data_id = event.get("data_id")
            event_ms = event.get("observed_ms")
            observed_after_ms = event.get("observed_after_ms")
            if (
                side not in ("local", "opponent")
                or not isinstance(data_id, int)
                or not isinstance(event_ms, int)
            ):
                continue
            if not isinstance(observed_after_ms, int):
                observed_after_ms = event_ms
            key = (side, observed_after_ms, event_ms, data_id)
            if key in self._seen_card_events:
                continue
            self._seen_card_events.add(key)
            definition = self._cards.get(data_id)
            before, after = self._sampled_elixir(
                side, observed_after_ms, event_ms
            )
            self._entries.append(
                BattleLogEntry(
                    elapsed_ms=max(0, event_ms - battle_start_ms),
                    kind="card_play",
                    side=side,
                    card_id=data_id,
                    card_name=(
                        definition.name if definition is not None else f"#{data_id}"
                    ),
                    form=(
                        event.get("form")
                        if event.get("form") in ("evolution", "hero")
                        else None
                    ),
                    elixir_cost=(
                        definition.elixir_cost if definition is not None else None
                    ),
                    elixir_before=before,
                    elixir_after=after,
                )
            )
        self._entries.sort(key=lambda entry: entry.elapsed_ms)
        if self._follow_latest:
            self._scroll_index = max(0, len(self._entries) - VISIBLE_ROWS)

    def wheel(self, position: tuple[int, int], steps: int) -> bool:
        if not LOG_RECT.collidepoint(position):
            return False
        maximum = max(0, len(self._entries) - VISIBLE_ROWS)
        self._scroll_index = max(
            0,
            min(maximum, self._scroll_index - int(steps)),
        )
        self._follow_latest = self._scroll_index >= maximum
        return True

    def cancel_pointer(self) -> None:
        return None

    def draw(self, surface: pygame.Surface, *, language: str = "zh-CN") -> None:
        panel = pygame.Surface(LOG_RECT.size, pygame.SRCALPHA)
        panel.fill((5, 13, 22, 222))
        pygame.draw.rect(panel, (89, 145, 188, 210), panel.get_rect(), 1, border_radius=6)
        surface.blit(panel, LOG_RECT)

        title = "战斗记录" if language != "en-US" else "Battle Log"
        title_image = self._title_font.render(title, True, (229, 242, 252))
        surface.blit(
            title_image,
            title_image.get_rect(midleft=(LOG_HEADER_RECT.left + 12, LOG_HEADER_RECT.centery)),
        )
        status_color = (74, 218, 139) if self._battle_was_active else (118, 142, 159)
        pygame.draw.circle(
            surface,
            status_color,
            (LOG_HEADER_RECT.right - 17, LOG_HEADER_RECT.centery),
            4,
        )
        pygame.draw.line(
            surface,
            (66, 101, 128),
            (LOG_HEADER_RECT.left + 8, LOG_HEADER_RECT.bottom),
            (LOG_HEADER_RECT.right - 8, LOG_HEADER_RECT.bottom),
        )

        if not self._entries:
            if not self._has_battle:
                empty = "暂无本局记录" if language != "en-US" else "No current battle"
            elif self._battle_was_active:
                empty = "正在记录本局…" if language != "en-US" else "Recording battle…"
            else:
                empty = "本局暂无事件" if language != "en-US" else "No events recorded"
            image = self._empty_font.render(empty, True, (137, 168, 190))
            surface.blit(image, image.get_rect(center=LOG_BODY_RECT.center))
            return

        previous_clip = surface.get_clip()
        surface.set_clip(LOG_BODY_RECT)
        font = self._english_font if language == "en-US" else self._body_font
        visible = self._entries[
            self._scroll_index : self._scroll_index + VISIBLE_ROWS
        ]
        for row, entry in enumerate(visible):
            text = format_entry(entry, language)
            timestamp = text[:7]
            body = text[8:]
            y = LOG_BODY_RECT.top + row * ROW_HEIGHT
            time_image = font.render(timestamp, True, (128, 148, 162))
            surface.blit(time_image, (LOG_BODY_RECT.left, y))
            if entry.kind == "damage_summary":
                color = (238, 211, 132)
            elif entry.side == "local":
                color = (99, 190, 255)
            else:
                color = (255, 126, 132)
            body_image = font.render(body, True, color)
            surface.blit(body_image, (LOG_BODY_RECT.left + 58, y))
        surface.set_clip(previous_clip)

        maximum = max(0, len(self._entries) - VISIBLE_ROWS)
        if maximum > 0:
            track = pygame.Rect(LOG_RECT.right - 7, LOG_BODY_RECT.top, 3, LOG_BODY_RECT.height)
            pygame.draw.rect(surface, (35, 58, 75), track, border_radius=2)
            thumb_height = max(24, round(track.height * VISIBLE_ROWS / len(self._entries)))
            travel = track.height - thumb_height
            thumb_y = track.top + round(travel * self._scroll_index / maximum)
            pygame.draw.rect(
                surface,
                (111, 164, 199),
                (track.left, thumb_y, track.width, thumb_height),
                border_radius=2,
            )
