from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pygame


SCREEN_SIZE = (760, 720)
ARENA = pygame.Rect(30, 76, 338, 600)
ARENA_WIDTH = 18_000
ARENA_HEIGHT = 32_000
DEFAULT_LOCAL_SIDE = 1
EMOTE_BLACKLIST_BUTTON = pygame.Rect(402, 642, 210, 32)
EVOLUTION_COLOR = (177, 126, 255)
_blacklist_editor_process: subprocess.Popen[bytes] | None = None


def _launch_emote_blacklist_editor() -> None:
    global _blacklist_editor_process
    if _blacklist_editor_process is not None and _blacklist_editor_process.poll() is None:
        return
    script = Path(__file__).with_name("emote_blacklist_ui.py")
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    _blacklist_editor_process = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(script.parent),
        creationflags=creation_flags,
    )


def _load_card_catalog() -> tuple[dict[int, str], dict[int, float]]:
    path = Path(__file__).resolve().parents[2] / "deploy" / "cards.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = [item for item in payload.get("items", []) if isinstance(item, dict)]
        names = {
            int(item["id"]): str(item["name"])
            for item in items
            if "id" in item and "name" in item
        }
        costs = {
            int(item["id"]): float(item["elixirCost"])
            for item in items
            if "id" in item and isinstance(item.get("elixirCost"), (int, float))
        }
        return names, costs
    except (OSError, ValueError, TypeError):
        return {}, {}


CARD_NAMES, CARD_COSTS = _load_card_catalog()


def _average_card_cost(cards: list[dict[str, Any]]) -> float | None:
    card_ids = {
        data_id
        for item in cards
        if isinstance(item, dict)
        and isinstance((data_id := item.get("data_id")), int)
        and data_id in CARD_COSTS
    }
    costs = [CARD_COSTS[data_id] for data_id in card_ids]
    return sum(costs) / len(costs) if costs else None


def _card_form_label(form: object) -> tuple[str, tuple[int, int, int]] | None:
    if form == "evolution":
        return "Evolution", EVOLUTION_COLOR
    if form == "hero":
        return "Hero", (255, 205, 92)
    return None


def _evolution_progress(card: dict[str, Any]) -> tuple[int, int] | None:
    if card.get("form") != "evolution":
        return None
    cycles = card.get("evolution_cycles")
    charge = card.get("evolution_charge")
    if not isinstance(cycles, int) or not 1 <= cycles <= 8:
        return None
    if not isinstance(charge, int):
        charge = 0
    return cycles, max(0, min(charge, cycles))


def _card_border_color(
    card: dict[str, Any], default: tuple[int, int, int]
) -> tuple[int, int, int]:
    if card.get("form") == "evolution" and card.get("evolution_ready") is True:
        return EVOLUTION_COLOR
    return default


def _draw_evolution_diamonds(
    screen: pygame.Surface,
    x: int,
    center_y: int,
    progress: tuple[int, int] | None,
) -> None:
    if progress is None:
        return
    cycles, charge = progress
    for index in range(cycles):
        center_x = x + index * 11
        points = (
            (center_x, center_y - 4),
            (center_x + 4, center_y),
            (center_x, center_y + 4),
            (center_x - 4, center_y),
        )
        pygame.draw.polygon(
            screen,
            EVOLUTION_COLOR,
            points,
            0 if index < charge else 1,
        )


def _draw_card_label(
    screen: pygame.Surface,
    card_rect: pygame.Rect,
    label: str,
    card: dict[str, Any],
    detail_font: pygame.font.Font,
    label_font: pygame.font.Font,
) -> None:
    form = card.get("form")
    form_label = _card_form_label(form)
    name_y = card_rect.y + (2 if form_label else 7)
    screen.blit(
        detail_font.render(label, True, (225, 229, 233)),
        (card_rect.x + 7, name_y),
    )
    if form_label is not None:
        text, color = form_label
        rendered = label_font.render(text, True, color)
        screen.blit(rendered, (card_rect.x + 7, card_rect.y + 19))
        _draw_evolution_diamonds(
            screen,
            card_rect.x + 12 + rendered.get_width(),
            card_rect.y + 25,
            _evolution_progress(card),
        )


def _entity_label(entity: dict[str, Any]) -> str:
    card_id = entity.get("card_id")
    if isinstance(card_id, int):
        return CARD_NAMES.get(card_id, str(card_id))
    if entity.get("kind") == 12:
        return "King"
    if entity.get("kind") == 13:
        return "Tower"
    return "Entity"


def _arena_point(
    entity: dict[str, Any],
    local_side: int = DEFAULT_LOCAL_SIDE,
) -> tuple[int, int] | None:
    x = entity.get("x")
    y = entity.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return None
    x = max(0.0, min(float(x), ARENA_WIDTH))
    y = max(0.0, min(float(y), ARENA_HEIGHT))
    if local_side == 0:
        x = ARENA_WIDTH - x
        y = ARENA_HEIGHT - y
    return (
        round(ARENA.left + x / ARENA_WIDTH * ARENA.width),
        round(ARENA.top + y / ARENA_HEIGHT * ARENA.height),
    )


def _draw_arena(
    screen: pygame.Surface,
    entities: list[dict[str, Any]],
    label_font: pygame.font.Font,
    local_side: int = DEFAULT_LOCAL_SIDE,
) -> None:
    pygame.draw.rect(screen, (35, 57, 48), ARENA)
    pygame.draw.rect(screen, (104, 117, 105), ARENA, 2)

    river_y = ARENA.centery
    pygame.draw.rect(screen, (35, 91, 126), (ARENA.left, river_y - 24, ARENA.width, 48))
    bridge_width = 54
    for fraction in (0.25, 0.75):
        center_x = round(ARENA.left + ARENA.width * fraction)
        pygame.draw.rect(
            screen,
            (145, 130, 94),
            (center_x - bridge_width // 2, river_y - 27, bridge_width, 54),
        )

    for entity in entities:
        point = _arena_point(entity, local_side)
        if point is None:
            continue
        side = entity.get("side")
        color = (72, 184, 213) if side == local_side else (231, 105, 91)
        kind = entity.get("kind")
        if kind == 12:
            shape = pygame.Rect(0, 0, 30, 30)
            shape.center = point
            pygame.draw.rect(screen, color, shape, border_radius=3)
        elif kind == 13:
            shape = pygame.Rect(0, 0, 24, 24)
            shape.center = point
            pygame.draw.rect(screen, color, shape, border_radius=3)
        else:
            pygame.draw.circle(screen, color, point, 11)
            pygame.draw.circle(screen, (235, 238, 239), point, 11, 1)

        hp = entity.get("hp")
        max_hp = entity.get("max_hp")
        if isinstance(hp, int) and isinstance(max_hp, int) and max_hp > 0:
            ratio = max(0.0, min(hp / max_hp, 1.0))
            bar = pygame.Rect(point[0] - 18, point[1] - 22, 36, 5)
            pygame.draw.rect(screen, (37, 39, 43), bar)
            pygame.draw.rect(screen, (88, 201, 111), (bar.x, bar.y, round(bar.width * ratio), 5))

        label = _entity_label(entity)
        if len(label) > 12:
            label = label[:11] + "."
        text = label_font.render(label, True, (239, 241, 242))
        text_rect = text.get_rect(midtop=(point[0], point[1] + 13))
        screen.blit(text, text_rect)


def run(snapshot_provider: Callable[[], dict | None]) -> None:
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    pygame.display.set_caption("Null's Royale state")
    clock = pygame.time.Clock()
    title_font = pygame.font.Font(None, 32)
    value_font = pygame.font.Font(None, 54)
    detail_font = pygame.font.Font(None, 23)
    label_font = pygame.font.Font(None, 16)

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif (
                event.type == pygame.MOUSEBUTTONUP
                and event.button == 1
                and EMOTE_BLACKLIST_BUTTON.collidepoint(event.pos)
            ):
                _launch_emote_blacklist_editor()

        snapshot = snapshot_provider()
        entities = []
        if snapshot is not None and isinstance(snapshot.get("entities"), list):
            entities = [item for item in snapshot["entities"] if isinstance(item, dict)]
        local_side = DEFAULT_LOCAL_SIDE
        if snapshot is not None and snapshot.get("local_side") in (0, 1):
            local_side = snapshot["local_side"]

        screen.fill((19, 22, 28))
        screen.blit(title_font.render("Battle state", True, (225, 229, 233)), (30, 26))
        status = (
            "Battle detected"
            if snapshot is not None and snapshot.get("battle_active")
            else "Waiting for battle"
        )
        status_color = (96, 201, 132) if entities else (151, 158, 169)
        screen.blit(detail_font.render(status, True, status_color), (402, 32))

        _draw_arena(screen, entities, label_font, local_side)

        panel_x = 402
        screen.blit(detail_font.render("Local elixir", True, (72, 184, 213)), (panel_x, 92))
        elixir = None if snapshot is None else snapshot.get("own_elixir")
        elixir_text = "--" if elixir is None else str(elixir)
        screen.blit(value_font.render(elixir_text, True, (207, 93, 238)), (panel_x, 118))

        opponent_x = panel_x + 150
        screen.blit(
            detail_font.render("Opponent elixir", True, (231, 105, 91)),
            (opponent_x, 92),
        )
        opponent_elixir = None if snapshot is None else snapshot.get("opponent_elixir")
        opponent_elixir_text = "--" if opponent_elixir is None else str(opponent_elixir)
        screen.blit(
            value_font.render(opponent_elixir_text, True, (207, 93, 238)),
            (opponent_x, 118),
        )

        battle_clock = None if snapshot is None else snapshot.get("battle_clock")
        clock_text = "--" if battle_clock is None else f"{float(battle_clock):.1f}"
        screen.blit(detail_font.render("Battle clock", True, (184, 190, 200)), (panel_x, 190))
        screen.blit(title_font.render(clock_text, True, (225, 229, 233)), (panel_x, 216))

        screen.blit(detail_font.render("Current hand", True, (184, 190, 200)), (panel_x, 284))
        hand = snapshot.get("hand", []) if snapshot is not None else []
        for slot in range(4):
            item = hand[slot] if slot < len(hand) and isinstance(hand[slot], dict) else {}
            data_id = item.get("data_id")
            label = CARD_NAMES.get(data_id, str(data_id) if data_id is not None else "--")
            if len(label) > 13:
                label = label[:12] + "."
            card_rect = pygame.Rect(panel_x + (slot % 2) * 132, 312 + (slot // 2) * 42, 122, 34)
            pygame.draw.rect(screen, (43, 48, 58), card_rect, border_radius=3)
            border_color = _card_border_color(item, (91, 101, 119))
            pygame.draw.rect(
                screen,
                border_color,
                card_rect,
                2 if border_color == EVOLUTION_COLOR else 1,
                border_radius=3,
            )
            _draw_card_label(
                screen,
                card_rect,
                label,
                item,
                detail_font,
                label_font,
            )

        next_card = snapshot.get("next_card") if snapshot is not None else None
        next_id = next_card.get("data_id") if isinstance(next_card, dict) else None
        next_label = CARD_NAMES.get(next_id, str(next_id) if next_id is not None else "--")
        screen.blit(detail_font.render(f"Next  {next_label}", True, (184, 190, 200)), (panel_x, 402))
        next_form = next_card.get("form") if isinstance(next_card, dict) else None
        next_form_label = _card_form_label(next_form)
        if next_form_label is not None:
            text, color = next_form_label
            rendered = label_font.render(text, True, color)
            screen.blit(rendered, (panel_x, 425))
            _draw_evolution_diamonds(
                screen,
                panel_x + 5 + rendered.get_width(),
                431,
                _evolution_progress(next_card),
            )

        screen.blit(
            detail_font.render("Opponent cards", True, (231, 105, 91)),
            (panel_x, 448),
        )
        opponent_cards = snapshot.get("opponent_cards", []) if snapshot is not None else []
        opponent_average = _average_card_cost(opponent_cards)
        opponent_average_text = "--" if opponent_average is None else f"{opponent_average:.1f}"
        average_x = panel_x + 270
        screen.blit(
            label_font.render("Avg cost", True, (184, 190, 200)),
            (average_x, 448),
        )
        screen.blit(
            title_font.render(opponent_average_text, True, (231, 105, 91)),
            (average_x, 470),
        )
        for slot in range(8):
            item = (
                opponent_cards[slot]
                if slot < len(opponent_cards) and isinstance(opponent_cards[slot], dict)
                else {}
            )
            data_id = item.get("data_id")
            label = CARD_NAMES.get(data_id, str(data_id) if data_id is not None else "?")
            if len(label) > 13:
                label = label[:12] + "."
            card_rect = pygame.Rect(
                panel_x + (slot % 2) * 132,
                476 + (slot // 2) * 42,
                122,
                34,
            )
            pygame.draw.rect(screen, (43, 48, 58), card_rect, border_radius=3)
            border_color = _card_border_color(item, (112, 78, 78))
            pygame.draw.rect(
                screen,
                border_color,
                card_rect,
                2 if border_color == EVOLUTION_COLOR else 1,
                border_radius=3,
            )
            _draw_card_label(
                screen,
                card_rect,
                label,
                item,
                detail_font,
                label_font,
            )

        pygame.draw.rect(
            screen,
            (43, 48, 58),
            EMOTE_BLACKLIST_BUTTON,
            border_radius=4,
        )
        pygame.draw.rect(
            screen,
            (91, 101, 119),
            EMOTE_BLACKLIST_BUTTON,
            1,
            border_radius=4,
        )
        screen.blit(
            detail_font.render("Manage emote blacklist", True, (225, 229, 233)),
            (EMOTE_BLACKLIST_BUTTON.x + 8, EMOTE_BLACKLIST_BUTTON.y + 7),
        )

        read_us = None if snapshot is None else snapshot.get("native_read_us")
        if isinstance(read_us, int):
            screen.blit(
                detail_font.render(f"Memory read  {read_us} us", True, (151, 158, 169)),
                (panel_x, 682),
            )

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
