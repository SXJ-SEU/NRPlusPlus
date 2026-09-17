from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse


SIMULATED_PLAYER_TAG = "8JCRL98YC"
PLAYER_PAGE_URL = "https://www.royaletools.com/player/{tag}"
ROYALTOOLS_ROOT = "https://www.royaletools.com"


def _default_icon_cache_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path(tempfile.gettempdir())
    return base / "NRPlusPlus" / "cache" / "card-icons"


@dataclass(frozen=True)
class CardSummary:
    data_id: int
    form: str | None
    elixir_cost: float | None
    icon_url: str | None
    cached_icon_path: str | None = None


@dataclass(frozen=True)
class DeckSummary:
    cards: tuple[CardSummary, ...]
    uses: int
    wins: int
    losses: int
    win_rate: float
    average_crowns_1v1: float | None = None

    @property
    def card_ids(self) -> tuple[int, ...]:
        return tuple(card.data_id for card in self.cards)

    def form_for(self, data_id: int) -> str | None:
        return next(
            (card.form for card in self.cards if card.data_id == data_id),
            None,
        )

    @property
    def average_elixir(self) -> float | None:
        costs = [card.elixir_cost for card in self.cards if card.elixir_cost is not None]
        return sum(costs) / len(costs) if costs else None


@dataclass(frozen=True)
class OpponentInfo:
    name: str
    tag: str
    clan_name: str | None
    trophies: int | None
    ranked_medals: int | None
    recent_games: int
    recent_wins: int
    recent_losses: int
    recent_win_rate: float
    decks: tuple[DeckSummary, ...]
    source: str = "RoyaleTools"


@dataclass(frozen=True)
class OpponentInfoState:
    status: Literal["idle", "loading", "ready", "error"] = "idle"
    info: OpponentInfo | None = None
    error: str | None = None


def _extract_flight_value(
    page: str,
    key: str,
    next_key: str | None,
    *,
    after_key: str | None = None,
) -> Any:
    marker = f'\\"{key}\\":'
    end_marker = (
        f',\\"{next_key}\\":'
        if next_key is not None
        else ']},\\"publicDistinctions\\":'
    )
    search_start = 0
    if after_key is not None:
        anchor = page.find(f'\\"{after_key}\\":')
        if anchor < 0:
            raise ValueError(f"missing {after_key} anchor")
        search_start = anchor + 1
    start = page.find(marker, search_start)
    if start < 0:
        raise ValueError(f"missing {key} data")
    start += len(marker)
    end = page.find(end_marker, start)
    if end < 0:
        raise ValueError(f"incomplete {key} data")
    if next_key is None:
        end += 1
    escaped_json = page[start:end]
    decoded_json = json.loads(f'"{escaped_json}"')
    return json.loads(decoded_json)


def _int_value(value: Any) -> int | None:
    return value if type(value) is int else None


def _card_form(card: dict[str, Any]) -> str | None:
    icon_url = card.get("iconUrl")
    if not isinstance(icon_url, str):
        return None
    if "/evolution-" in icon_url:
        return "evolution"
    if "/hero-" in icon_url:
        return "hero"
    return None


def _float_value(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _parse_card(card: dict[str, Any]) -> CardSummary | None:
    data_id = _int_value(card.get("id"))
    if data_id is None:
        return None
    icon_url = card.get("iconUrl")
    return CardSummary(
        data_id=data_id,
        form=_card_form(card),
        elixir_cost=_float_value(card.get("elixirCost")),
        icon_url=icon_url if isinstance(icon_url, str) else None,
    )


def _deck_signature(
    cards: list[dict[str, Any]] | tuple[CardSummary, ...],
) -> tuple[tuple[int, str], ...]:
    parsed: list[tuple[int, str]] = []
    for item in cards:
        card = item if isinstance(item, CardSummary) else _parse_card(item)
        if card is not None:
            parsed.append((card.data_id, card.form or "normal"))
    return tuple(sorted(parsed))


def _average_1v1_crowns_by_deck(battles: Any) -> dict[tuple[tuple[int, str], ...], float]:
    totals: dict[tuple[tuple[int, str], ...], list[int]] = {}
    if not isinstance(battles, list):
        return {}
    for battle in battles:
        if not isinstance(battle, dict) or battle.get("is2v2") is not False:
            continue
        player = battle.get("player")
        if not isinstance(player, dict):
            continue
        cards = player.get("deck")
        crowns = _int_value(player.get("crowns"))
        if not isinstance(cards, list) or crowns is None:
            continue
        signature = _deck_signature(cards)
        if signature:
            totals.setdefault(signature, []).append(crowns)
    return {
        signature: sum(crowns) / len(crowns)
        for signature, crowns in totals.items()
        if crowns
    }


def parse_royaletools_player_page(page: str) -> OpponentInfo:
    player = _extract_flight_value(page, "player", "recent")
    recent = _extract_flight_value(page, "recent", "battles")
    battles = _extract_flight_value(
        page, "battles", "recentDecks", after_key="recent"
    )
    recent_decks = _extract_flight_value(page, "recentDecks", None)
    if not isinstance(player, dict) or not isinstance(recent, dict):
        raise ValueError("invalid player response")

    crowns_by_deck = _average_1v1_crowns_by_deck(battles)
    decks: list[DeckSummary] = []
    if isinstance(recent_decks, list):
        for item in recent_decks:
            if not isinstance(item, dict):
                continue
            cards = item.get("cards")
            if not isinstance(cards, list):
                continue
            parsed_cards: list[CardSummary] = []
            for card in cards:
                if not isinstance(card, dict):
                    continue
                parsed_card = _parse_card(card)
                if parsed_card is not None:
                    parsed_cards.append(parsed_card)
            uses = _int_value(item.get("uses")) or 0
            wins = _int_value(item.get("wins")) or 0
            losses = _int_value(item.get("losses")) or 0
            raw_rate = item.get("winRate")
            win_rate = float(raw_rate) if isinstance(raw_rate, (int, float)) else 0.0
            if parsed_cards:
                deck_cards = tuple(parsed_cards[:8])
                decks.append(
                    DeckSummary(
                        cards=deck_cards,
                        uses=uses,
                        wins=wins,
                        losses=losses,
                        win_rate=max(0.0, min(win_rate, 1.0)),
                        average_crowns_1v1=crowns_by_deck.get(
                            _deck_signature(deck_cards)
                        ),
                    )
                )

    clan = player.get("clan")
    clan_name = clan.get("name") if isinstance(clan, dict) else None
    raw_recent_rate = recent.get("winRate")
    recent_games = _int_value(recent.get("sampleSize")) or 0
    recent_wins = _int_value(recent.get("wins")) or 0
    recent_losses = _int_value(recent.get("losses")) or 0
    recent_win_rate = (
        float(raw_recent_rate)
        if isinstance(raw_recent_rate, (int, float))
        else recent_wins / max(1, recent_wins + recent_losses)
    )
    name = player.get("name")
    tag = player.get("tag")
    if not isinstance(name, str) or not isinstance(tag, str):
        raise ValueError("player identity is unavailable")
    return OpponentInfo(
        name=name,
        tag=tag if tag.startswith("#") else f"#{tag}",
        clan_name=clan_name if isinstance(clan_name, str) else None,
        trophies=_int_value(player.get("trophies")),
        ranked_medals=_int_value(player.get("rankedMedals")),
        recent_games=recent_games,
        recent_wins=recent_wins,
        recent_losses=recent_losses,
        recent_win_rate=max(0.0, min(recent_win_rate, 1.0)),
        decks=tuple(decks),
    )


def _download_card_art(
    card: CardSummary,
    cache_root: Path,
    *,
    timeout: float,
) -> CardSummary:
    if card.icon_url is None or not card.icon_url.startswith(("/cards/", "/card-forms/")):
        return card
    suffix = Path(urlparse(card.icon_url).path).suffix.lower()
    if suffix not in (".png", ".webp", ".jpg", ".jpeg"):
        suffix = ".png"
    target = cache_root / f"{card.data_id}_{card.form or 'normal'}{suffix}"
    if target.is_file() and target.stat().st_size > 0:
        return replace(card, cached_icon_path=str(target))
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            urljoin(ROYALTOOLS_ROOT, card.icon_url),
            headers={"User-Agent": "NRPlusPlus/1.0 (+opponent-info)"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(5_000_001)
        if not payload or len(payload) > 5_000_000:
            return card
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(target)
    except (OSError, urllib.error.URLError):
        return card
    return replace(card, cached_icon_path=str(target))


def cache_online_card_art(
    info: OpponentInfo,
    *,
    cache_root: Path | None = None,
    timeout: float = 5.0,
) -> OpponentInfo:
    resolved_cache_root = cache_root or _default_icon_cache_root()
    unique_cards = {
        (card.data_id, card.form): card
        for deck in info.decks
        for card in deck.cards
    }
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(unique_cards)))) as pool:
        downloaded = pool.map(
            lambda card: _download_card_art(
                card,
                resolved_cache_root,
                timeout=timeout,
            ),
            unique_cards.values(),
        )
        resolved = dict(zip(unique_cards, downloaded))
    decks: list[DeckSummary] = []
    for deck in info.decks:
        cards: list[CardSummary] = []
        for card in deck.cards:
            key = (card.data_id, card.form)
            cards.append(resolved[key])
        decks.append(replace(deck, cards=tuple(cards)))
    return replace(info, decks=tuple(decks))


def fetch_simulated_opponent_info(
    tag: str = SIMULATED_PLAYER_TAG,
    *,
    timeout: float = 10.0,
) -> OpponentInfo:
    normalized_tag = tag.strip().lstrip("#").upper()
    request = urllib.request.Request(
        PLAYER_PAGE_URL.format(tag=normalized_tag),
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "NRPlusPlus/1.0 (+opponent-info)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            page = response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError("无法连接玩家资料服务") from exc
    try:
        info = parse_royaletools_player_page(page)
        return cache_online_card_art(info, timeout=min(timeout, 5.0))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("玩家资料格式暂时无法识别") from exc


class OpponentInfoController:
    def __init__(
        self,
        fetcher: Callable[[], OpponentInfo] = fetch_simulated_opponent_info,
    ) -> None:
        self._fetcher = fetcher
        self._lock = threading.Lock()
        self._state = OpponentInfoState()
        self._request_id = 0

    def current(self) -> OpponentInfoState:
        with self._lock:
            return self._state

    def request(self) -> None:
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            self._state = OpponentInfoState(status="loading")
        threading.Thread(
            target=self._load,
            args=(request_id,),
            name="nrpp-opponent-info",
            daemon=True,
        ).start()

    def _load(self, request_id: int) -> None:
        try:
            info = self._fetcher()
        except Exception as exc:
            state = OpponentInfoState(status="error", error=str(exc))
        else:
            state = OpponentInfoState(status="ready", info=info)
        with self._lock:
            if request_id == self._request_id:
                self._state = state
