from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))

from subprocess_utils import hidden_process_kwargs  # noqa: E402
from adb_runtime import AdbConfig, AdbRuntime  # noqa: E402
from proc_memory import BattleStateLocator, RootProcessMemory  # noqa: E402


LOCAL_HELPER = ROOT / "runtime" / "cr-arm-entity-stream-x86_64"
REMOTE_HELPER = "/data/local/tmp/cr-arm-entity-stream"
DEFAULT_STREAM_INTERVAL_MS = 20
CARD_ID_ALIASES = {
    26_000_104: 28_000_025,
    26_000_105: 28_000_025,
}


def _load_card_names() -> dict[int, str]:
    try:
        path = ROOT.parent / "deploy" / "cards.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = [item for item in payload.get("items", []) if isinstance(item, dict)]
        names = {
            int(item["id"]): str(item["name"])
            for item in items
            if "id" in item and "name" in item
        }
        return names
    except (OSError, ValueError, TypeError):
        return {}


CARD_NAMES = _load_card_names()
SECONDARY_STATE_FIELDS = frozenset(
    {
        "battle_clock",
        "hand",
        "next_card",
        "state_diagnostics",
        "local_player_index",
        "opponent_cards",
    }
)


class SnapshotStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] | None = None

    def update(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._snapshot = dict(snapshot)

    def current(self) -> dict[str, Any] | None:
        with self._lock:
            return None if self._snapshot is None else dict(self._snapshot)


class BattleStateCoordinator:
    """Coordinates the slower secondary reader with the native snapshot stream."""

    def __init__(self, reader_factory: Callable[[], Callable[[], dict[str, Any] | None]]) -> None:
        self._lock = threading.Lock()
        self._reader_factory = reader_factory
        self._reader: Callable[[], dict[str, Any] | None] | None = None
        self._active = False
        self._state: dict[str, Any] | None = None
        self._observed_card_ids_by_side: tuple[
            list[tuple[int, int]], list[tuple[int, int]]
        ] = ([], [])
        self._active_entity_keys_by_side: tuple[set[object], set[object]] = (
            set(),
            set(),
        )

    def set_active(self, active: bool) -> None:
        with self._lock:
            if active == self._active:
                return
            self._active = active
            self._state = None
            for card_ids in self._observed_card_ids_by_side:
                card_ids.clear()
            for entity_keys in self._active_entity_keys_by_side:
                entity_keys.clear()
            self._reader = self._reader_factory() if active else None

    def poll(self) -> None:
        with self._lock:
            if not self._active or self._reader is None:
                return
            reader = self._reader
        state = reader()
        if state is not None:
            with self._lock:
                if self._active and reader is self._reader:
                    if self._state is None:
                        self._state = {}
                    self._state.update(state)

    def merge(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._active and snapshot.get("battle_active"):
                entities = snapshot.get("entities")
                if isinstance(entities, list):
                    observed_ms = snapshot.get("t_ms")
                    if not isinstance(observed_ms, int):
                        observed_ms = int(time.time() * 1000)
                    current_entity_keys_by_side: tuple[set[object], set[object]] = (
                        set(),
                        set(),
                    )
                    for entity in entities:
                        if not isinstance(entity, dict):
                            continue
                        side = entity.get("side")
                        card_id = entity.get("card_id")
                        if (
                            side in (0, 1)
                            and isinstance(card_id, int)
                        ):
                            address = entity.get("address")
                            entity_key: object = (
                                (address, card_id)
                                if isinstance(address, (int, str))
                                else ("card", card_id)
                            )
                            current_entity_keys_by_side[side].add(entity_key)
                            if entity_key not in self._active_entity_keys_by_side[side]:
                                previous_event = (
                                    self._observed_card_ids_by_side[side][-1]
                                    if self._observed_card_ids_by_side[side]
                                    else None
                                )
                                if previous_event != (observed_ms, card_id):
                                    self._observed_card_ids_by_side[side].append(
                                        (observed_ms, card_id)
                                    )
                    self._active_entity_keys_by_side = current_entity_keys_by_side
            state = (
                dict(self._state)
                if self._active and snapshot.get("battle_active") and self._state is not None
                else None
            )
            observed_card_ids_by_side = tuple(
                list(card_ids) for card_ids in self._observed_card_ids_by_side
            )
        if state is not None:
            snapshot.update(
                {key: value for key, value in state.items() if key in SECONDARY_STATE_FIELDS}
            )
        local_player_index = snapshot.get("local_player_index")
        if local_player_index in (0, 1):
            # Player-list order and entity.side use the same battle-side index.
            # The native helper cannot resolve the local account and reports a
            # placeholder, so replace it once account-ID matching completes.
            snapshot["local_side"] = local_player_index
        local_side = snapshot.get("local_side")
        observed_opponent_card_ids = (
            observed_card_ids_by_side[1 - local_side]
            if local_side in (0, 1)
            else []
        )
        opponent_cards = snapshot.get("opponent_cards")
        opponent_deck = state.get("opponent_deck") if state is not None else None
        if (
            snapshot.get("battle_active")
            and isinstance(opponent_cards, list)
            and (opponent_cards or isinstance(opponent_deck, list))
        ):
            opponent_deck_ids = {
                item.get("data_id")
                for item in opponent_deck or []
                if isinstance(item, dict) and isinstance(item.get("data_id"), int)
            }
            revealed_events: list[tuple[int, int, int]] = []
            for slot, item in enumerate(opponent_cards):
                if not isinstance(item, dict) or not isinstance(item.get("data_id"), int):
                    continue
                observed_ms = item.get("observed_ms")
                if not isinstance(observed_ms, int):
                    observed_ms = slot
                revealed_events.append((observed_ms, 0, item["data_id"]))

            for observed_ms, observed_id in observed_opponent_card_ids:
                deck_id = observed_id if observed_id in opponent_deck_ids else None
                alias = CARD_ID_ALIASES.get(observed_id)
                if deck_id is None and alias in opponent_deck_ids:
                    deck_id = alias
                if deck_id is None and observed_id in CARD_NAMES:
                    same_name = [
                        candidate
                        for candidate in opponent_deck_ids
                        if CARD_NAMES.get(candidate) == CARD_NAMES[observed_id]
                    ]
                    if len(same_name) == 1:
                        deck_id = same_name[0]
                if deck_id is not None:
                    revealed_events.append((observed_ms, 1, deck_id))

            revealed_ids = []
            for _, _, card_id in sorted(revealed_events):
                if card_id not in revealed_ids:
                    revealed_ids.append(card_id)
            snapshot["opponent_cards"] = [
                {
                    "slot": slot,
                    "data_id": revealed_ids[slot] if slot < len(revealed_ids) else None,
                }
                for slot in range(8)
            ]
        player_elixir = snapshot.get("player_elixir")
        local_player_index = snapshot.get("local_player_index")
        if (
            isinstance(player_elixir, list)
            and len(player_elixir) >= 2
            and local_player_index in (0, 1)
        ):
            snapshot["own_elixir"] = player_elixir[local_player_index]
            snapshot["opponent_elixir"] = player_elixir[1 - local_player_index]
        return snapshot


def run_command(command: list[str], *, timeout: float) -> None:
    subprocess.run(
        command,
        check=True,
        timeout=timeout,
        cwd=str(ROOT),
        **hidden_process_kwargs(),
    )


def command_output(command: list[str], *, timeout: float) -> str:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(ROOT),
        **hidden_process_kwargs(),
    )
    return result.stdout.strip()


def normalize_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    entities = payload.get("entities")
    if not isinstance(entities, list):
        entities = []
    normalized_entities = []
    for item in entities:
        if not isinstance(item, dict):
            continue
        entity = dict(item)
        if entity.get("card_id") == -1:
            entity["card_id"] = None
        normalized_entities.append(entity)
    local_side = payload.get("local_side")
    if local_side not in (0, 1):
        local_side = 1
    # These slots follow the battle's player-list order. The local player's
    # index changes between battles and is resolved separately by account ID.
    player_elixir = payload.get("player_elixir", payload.get("side_elixir"))
    if not isinstance(player_elixir, list) or len(player_elixir) < 2:
        player_elixir = [None, None]
    player_elixir = [
        value if isinstance(value, int) and 0 <= value <= 10 else None
        for value in player_elixir[:2]
    ]
    local_player_index = payload.get("local_player_index")
    if local_player_index not in (0, 1):
        local_player_index = None
    own_elixir = payload.get("own_elixir")
    if local_player_index is not None:
        own_elixir = player_elixir[local_player_index]
    elif not isinstance(own_elixir, int) or not 0 <= own_elixir <= 10:
        own_elixir = None
    opponent_elixir = (
        player_elixir[1 - local_player_index]
        if local_player_index is not None
        else None
    )
    battle_clock = payload.get("battle_clock")
    if not isinstance(battle_clock, (int, float)) or not 0.0 <= battle_clock <= 600.0:
        battle_clock = None
    hand = payload.get("hand", [])
    if not isinstance(hand, list):
        hand = []
    hand = [
        {**item, "data_id": None if item.get("data_id") == -1 else item.get("data_id")}
        for item in hand if isinstance(item, dict)
    ]
    next_card = payload.get("next_card")
    if isinstance(next_card, dict) and next_card.get("data_id") == -1:
        next_card = {**next_card, "data_id": None}
    opponent_cards = payload.get("opponent_cards", [])
    if not isinstance(opponent_cards, list):
        opponent_cards = []
    opponent_cards = [
        {**item, "data_id": None if item.get("data_id") == -1 else item.get("data_id")}
        for item in opponent_cards
        if isinstance(item, dict)
    ]
    return {
        "event": "runtime_snapshot",
        "t_ms": int(time.time() * 1000),
        "battle_active": bool(payload.get("battle_active")),
        "local_side": local_side,
        "local_player_index": local_player_index,
        "own_elixir": own_elixir,
        "opponent_elixir": opponent_elixir,
        "player_elixir": player_elixir,
        "battle_clock": battle_clock,
        "hand": hand,
        "next_card": next_card,
        "opponent_cards": opponent_cards,
        "entities": normalized_entities,
        "native_sequence": payload.get("sequence"),
        "native_read_us": payload.get("read_us"),
    }


def played_hand_indices(
    previous: tuple[int, ...] | None, current: tuple[int, ...]
) -> tuple[int, ...]:
    """Return deck indices that disappeared from a four-card hand."""
    if previous is None:
        return ()
    current_indices = set(current)
    return tuple(index for index in previous if index not in current_indices)


def make_battle_reader(adb_path: Path, serial: str, pid: int):
    locator = BattleStateLocator(RootProcessMemory(AdbRuntime(AdbConfig(adb_path, serial)), pid))
    pointers = None
    retry_at = 0.0
    diagnostics: dict[str, Any] = {"status": "starting"}
    deck_ids: list[int | None] = [None] * 8
    conflicted_indices: set[int] = set()
    opponent_deck_ids: list[int | None] = [None] * 8
    local_player_index: int | None = None
    opponent_hand_pointers = None
    previous_opponent_hand: tuple[int, ...] | None = None
    opponent_play_events: list[tuple[int, int]] = []
    played_opponent_indices: set[int] = set()

    def read() -> dict[str, Any] | None:
        nonlocal pointers, retry_at, local_player_index
        nonlocal opponent_hand_pointers, previous_opponent_hand
        try:
            if pointers is None:
                now = time.monotonic()
                if now < retry_at:
                    return None
                retry_at = now + 1.0
                diagnostics.update({"status": "locating"})
                pointers = locator.locate()
                local_player_index = None
                opponent_hand_pointers = None
                diagnostics.update({"status": "located" if pointers else "no_candidate",
                                    "timing": dict(locator.last_timing)})
            if pointers is None:
                return {"state_diagnostics": dict(diagnostics)}
            if local_player_index is None:
                try:
                    local_player_index = locator.poll_local_player_index(pointers)
                except Exception as exc:
                    diagnostics.update({"status": "player_identity_failed", "error": str(exc)})
                else:
                    diagnostics.update({"status": "player_identified"})
                    return {
                        "local_player_index": local_player_index,
                        "state_diagnostics": dict(diagnostics),
                    }
            if local_player_index is not None and opponent_hand_pointers is None:
                try:
                    opponent_hand_pointers = locator.locate_player_hand_pointers(
                        1 - local_player_index
                    )
                except Exception as exc:
                    diagnostics.update(
                        {"status": "opponent_hand_bind_failed", "error": str(exc)}
                    )
            elixir = clock = None
            try:
                elixir, clock = locator.poll_elixir(pointers)
            except Exception as exc:
                diagnostics.update({"status": "elixir_read_failed", "error": str(exc)})
            indices: tuple[int, ...] = ()
            try:
                indices = locator.poll_hand_indices(pointers)
            except Exception as exc:
                diagnostics.update({"status": "hand_read_failed", "error": str(exc)})
            if not indices and elixir is None:
                pointers = None
                return {"state_diagnostics": dict(diagnostics)}
            resolved = locator.poll_card_object_deck(pointers)
            if resolved is not None:
                for index, data_id in enumerate(resolved):
                    if data_id is None or index in conflicted_indices:
                        continue
                    if deck_ids[index] is None:
                        deck_ids[index] = data_id
                    elif deck_ids[index] != data_id:
                        deck_ids[index] = None
                        conflicted_indices.add(index)
            if local_player_index is not None and any(
                value is None for value in opponent_deck_ids
            ):
                opponent_resolved = locator.poll_opponent_card_deck(
                    pointers, local_player_index
                )
                if opponent_resolved is not None:
                    for index, data_id in enumerate(opponent_resolved[:8]):
                        if data_id is not None:
                            opponent_deck_ids[index] = data_id
            if opponent_hand_pointers is not None:
                try:
                    current_opponent_hand = locator.poll_player_hand_indices(
                        opponent_hand_pointers
                    )
                    for deck_index in played_hand_indices(
                        previous_opponent_hand, current_opponent_hand
                    ):
                        if deck_index not in played_opponent_indices:
                            played_opponent_indices.add(deck_index)
                            opponent_play_events.append(
                                (int(time.time() * 1000), deck_index)
                            )
                    previous_opponent_hand = current_opponent_hand
                except Exception as exc:
                    diagnostics.update(
                        {"status": "opponent_hand_read_failed", "error": str(exc)}
                    )
                    opponent_hand_pointers = None
            next_index = -1
            try:
                next_index = locator.poll_next_deck_index(pointers)
            except Exception:
                pass
            hand = [{"slot": slot, "deck_index": index,
                     "data_id": deck_ids[index] if 0 <= index < 8 else None}
                     for slot, index in enumerate(indices)]
            diagnostics.update({"status": "ready", "resolved_card_ids":
                                sum(value is not None for value in deck_ids)})
            result = {"battle_clock": clock, "hand": hand,
                    "next_card": {"deck_index": next_index,
                                  "data_id": deck_ids[next_index] if 0 <= next_index < 8 else None},
                    "opponent_cards": [
                        {
                            "slot": slot,
                            "data_id": opponent_deck_ids[deck_index],
                            "observed_ms": observed_ms,
                        }
                        for slot, (observed_ms, deck_index) in enumerate(
                            opponent_play_events
                        )
                        if opponent_deck_ids[deck_index] is not None
                    ],
                    "opponent_deck": [
                        {"slot": slot, "data_id": data_id}
                        for slot, data_id in enumerate(opponent_deck_ids)
                    ],
                    "state_diagnostics": dict(diagnostics)}
            if local_player_index is not None:
                result["local_player_index"] = local_player_index
            return result
        except Exception as exc:
            diagnostics.update({"status": "reader_failed", "error": str(exc)})
            pointers = None
            return {"state_diagnostics": dict(diagnostics)}
    return read


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Visualize and log the native entity/HP stream"
    )
    parser.add_argument("--serial", default="localhost:5557")
    parser.add_argument("--package", default="nullsroyale.rel.free")
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="seconds to capture; 0 runs until the window closes",
    )
    parser.add_argument("--interval-ms", type=int, default=DEFAULT_STREAM_INTERVAL_MS)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--log", type=Path, default=ROOT / "captures" / "native_entity_stream.jsonl"
    )
    args = parser.parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    if not LOCAL_HELPER.is_file():
        raise SystemExit(f"native helper is missing: {LOCAL_HELPER}")

    adb_path = Path(shutil.which("adb") or "adb").resolve()
    adb = str(adb_path)
    adb_prefix = [adb, "-s", args.serial]
    if command_output([*adb_prefix, "shell", "id", "-u"], timeout=10) != "0":
        raise SystemExit("MuMu adbd is not running as root")
    pid = int(
        command_output([*adb_prefix, "shell", "pidof", args.package], timeout=10).split()[0]
    )
    battle_state = BattleStateCoordinator(
        lambda: make_battle_reader(adb_path, args.serial, pid)
    )
    state_stop = threading.Event()
    native_active = threading.Event()

    def state_worker() -> None:
        while not state_stop.is_set():
            if not native_active.wait(0.5):
                continue
            battle_state.poll()
            state_stop.wait(0.1)

    state_thread = threading.Thread(target=state_worker, daemon=True)
    state_thread.start()
    run_command(
        [*adb_prefix, "push", str(LOCAL_HELPER), REMOTE_HELPER], timeout=30
    )
    run_command([*adb_prefix, "shell", "chmod", "755", REMOTE_HELPER], timeout=10)

    remote_command = [REMOTE_HELPER, str(pid), str(args.interval_ms)]
    if args.duration > 0:
        remote_command = ["timeout", str(args.duration), *remote_command]
    command = [*adb_prefix, "exec-out", *remote_command]

    store = SnapshotStore()
    stop = threading.Event()
    summary: dict[str, Any] = {
        "snapshots": 0,
        "active_snapshots": 0,
        "entity_snapshots": 0,
        "read_times": [],
        "error": None,
    }

    def capture() -> None:
        process: subprocess.Popen[str] | None = None
        try:
            with args.log.open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(ROOT),
                    **hidden_process_kwargs(),
                )
                assert process.stdout is not None
                for line in process.stdout:
                    if stop.is_set():
                        break
                    line = line.strip()
                    if not line:
                        continue
                    snapshot = normalize_snapshot(json.loads(line))
                    if snapshot["battle_active"]:
                        battle_state.set_active(True)
                        native_active.set()
                    else:
                        native_active.clear()
                        battle_state.set_active(False)
                    snapshot = battle_state.merge(snapshot)
                    store.update(snapshot)
                    encoded = json.dumps(snapshot, separators=(",", ":"))
                    log.write(encoded + "\n")
                    log.flush()
                    summary["snapshots"] += 1
                    summary["active_snapshots"] += int(snapshot["battle_active"])
                    summary["entity_snapshots"] += int(bool(snapshot["entities"]))
                    if isinstance(snapshot["native_read_us"], int):
                        summary["read_times"].append(snapshot["native_read_us"])
                    if args.headless and snapshot["entities"]:
                        print(encoded, flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            summary["error"] = str(exc)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            state_stop.set()
            stop.set()

    print("Enter a battle and deploy several troops.", flush=True)
    worker = threading.Thread(target=capture, daemon=True)
    worker.start()
    if args.headless:
        worker.join()
    else:
        from minimal_visualizer import run

        try:
            run(store.current)
        finally:
            stop.set()
            worker.join(timeout=5)

    read_times = summary.pop("read_times")
    summary.update(
        {
            "event": "capture_complete",
            "average_read_us": (
                round(sum(read_times) / len(read_times)) if read_times else None
            ),
            "max_read_us": max(read_times) if read_times else None,
            "log": str(args.log),
        }
    )
    print(json.dumps(summary, separators=(",", ":")), flush=True)
    return 1 if summary["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
