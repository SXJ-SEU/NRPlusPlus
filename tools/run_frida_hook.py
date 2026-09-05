#!/usr/bin/env python3
"""Load a Frida JavaScript hook and keep the session alive."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import frida


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_or_pid")
    parser.add_argument("script")
    parser.add_argument("--host", default="127.0.0.1:27042")
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()

    target: int | str
    try:
        target = int(args.package_or_pid)
    except ValueError:
        target = args.package_or_pid

    source = Path(args.script).read_text(encoding="utf-8")
    device = frida.get_device_manager().add_remote_device(args.host)
    session = device.attach(target)

    def on_message(message, data):
        if message["type"] in {"send", "log"}:
            print(message["payload"], flush=True)
        else:
            print(message, flush=True)

    script = session.create_script(source)
    script.on("message", on_message)
    script.load()

    print(f"[+] hook alive for {args.seconds}s; press Ctrl-C to stop", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
