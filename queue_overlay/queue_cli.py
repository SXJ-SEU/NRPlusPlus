#!/usr/bin/env python3
"""Print card and tile as soon as a deployment enters the scheduler queue."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import frida


def load_card_names(path: Path) -> dict[int, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: item["name"] for item in data["items"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_or_pid")
    parser.add_argument("script")
    parser.add_argument("--host", default="127.0.0.1:27042")
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--cards", default=Path(__file__).with_name("card_catalog.json"))
    args = parser.parse_args()

    try:
        target: int | str = int(args.package_or_pid)
    except ValueError:
        target = args.package_or_pid

    card_names = load_card_names(Path(args.cards))
    source = Path(args.script).read_text(encoding="utf-8")
    device = frida.get_device_manager().add_remote_device(args.host)
    session = device.attach(target)

    def on_message(message: dict, _data: bytes | None) -> None:
        if message["type"] == "error":
            print(f"[error] {message['description']}", file=sys.stderr, flush=True)
            return
        if message["type"] != "send":
            return
        try:
            event = json.loads(message["payload"])
        except (TypeError, json.JSONDecodeError):
            return
        if event.get("event") == "queue_deploy_ready":
            print("[+] queue deploy monitor active; press Ctrl-C to stop", flush=True)
            return
        if event.get("event") != "queue_deploy":
            return
        card_id = event["card_id"]
        name = card_names.get(card_id, f"Unknown card {card_id}")
        tile = event["target_tile_center"]
        clock = event.get("battle_clock")
        clock_text = f" clock={clock:.3f}" if isinstance(clock, (int, float)) else ""
        print(
            f"[queue{clock_text}] {name} ({card_id}) -> tile ({tile['x']}, {tile['y']})",
            flush=True,
        )

    script = session.create_script(source)
    script.on("message", on_message)
    script.load()
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
