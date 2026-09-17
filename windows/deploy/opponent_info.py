from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal


SIMULATED_PLAYER_TAG = "8JCRL98YC"
PLAYER_PAGE_URL = "https://www.royaletools.com/player/{tag}"


@dataclass(frozen=True)
class DeckSummary:
    card_ids: tuple[int, ...]
    forms: tuple[tuple[int, str], ...]
    uses: int
    wins: int
    losses: int
    win_rate: float

    def form_for(self, data_id: int) -> str | None:
        return dict(self.forms).get(data_id)


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


def _extract_flight_value(page: str, key: str, next_key: str | None) -> Any:
    marker = f'\\"{key}\\":'
    end_marker = (
        f',\\"{next_key}\\":'
        if next_key is not None
        else ']},\\"publicDistinctions\\":'
    )
    start = page.find(marker)
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


def parse_royaletools_player_page(page: str) -> OpponentInfo:
    player = _extract_flight_value(page, "player", "recent")
    recent = _extract_flight_value(page, "recent", "battles")
    recent_decks = _extract_flight_value(page, "recentDecks", None)
    if not isinstance(player, dict) or not isinstance(recent, dict):
        raise ValueError("invalid player response")

    decks: list[DeckSummary] = []
    if isinstance(recent_decks, list):
        for item in recent_decks:
            if not isinstance(item, dict):
                continue
            cards = item.get("cards")
            if not isinstance(cards, list):
                continue
            card_ids: list[int] = []
            forms: list[tuple[int, str]] = []
            for card in cards:
                if not isinstance(card, dict):
                    continue
                data_id = _int_value(card.get("id"))
                if data_id is None:
                    continue
                card_ids.append(data_id)
                form = _card_form(card)
                if form is not None:
                    forms.append((data_id, form))
            uses = _int_value(item.get("uses")) or 0
            wins = _int_value(item.get("wins")) or 0
            losses = _int_value(item.get("losses")) or 0
            raw_rate = item.get("winRate")
            win_rate = float(raw_rate) if isinstance(raw_rate, (int, float)) else 0.0
            if card_ids:
                decks.append(
                    DeckSummary(
                        card_ids=tuple(card_ids[:8]),
                        forms=tuple(forms),
                        uses=uses,
                        wins=wins,
                        losses=losses,
                        win_rate=max(0.0, min(win_rate, 1.0)),
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
        return parse_royaletools_player_page(page)
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
