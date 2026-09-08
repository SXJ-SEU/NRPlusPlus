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

    def set_active(self, active: bool) -> None:
        with self._lock:
            if active == self._active:
                return
            self._active = active
            self._state = None
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
            state = (
                dict(self._state)
                if self._active and snapshot.get("battle_active") and self._state is not None
                else None
            )
        if state is not None:
            snapshot.update(
                {key: value for key, value in state.items() if key in SECONDARY_STATE_FIELDS}
            )
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


def make_battle_reader(adb_path: Path, serial: str, pid: int):
    locator = BattleStateLocator(RootProcessMemory(AdbRuntime(AdbConfig(adb_path, serial)), pid))
    pointers = None
    retry_at = 0.0
    diagnostics: dict[str, Any] = {"status": "starting"}
    deck_ids: list[int | None] = [None] * 8
    conflicted_indices: set[int] = set()
    opponent_deck_ids: list[int | None] = [None] * 8
    local_player_index: int | None = None

    def read() -> dict[str, Any] | None:
        nonlocal pointers, retry_at, local_player_index
        try:
            if pointers is None:
                now = time.monotonic()
                if now < retry_at:
                    return None
                retry_at = now + 1.0
                diagnostics.update({"status": "locating"})
                pointers = locator.locate()
                local_player_index = None
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
