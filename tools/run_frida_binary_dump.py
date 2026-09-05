#!/usr/bin/env python3
"""Run a Frida hook and save binary payloads sent with send(payload, data)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import frida


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_or_pid")
    parser.add_argument("script")
    parser.add_argument("--host", default="127.0.0.1:27042")
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--outdir", default="work/inbound_dumps")
    args = parser.parse_args()

    target: int | str
    try:
        target = int(args.package_or_pid)
    except ValueError:
        target = args.package_or_pid

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    source = Path(args.script).read_text(encoding="utf-8")
    device = frida.get_device_manager().add_remote_device(args.host)
    session = device.attach(target)

    def on_message(message, data):
        if message["type"] == "send":
            payload = message.get("payload")
            if isinstance(payload, dict) and payload.get("event") == "dump" and data is not None:
                seq = int(payload["seq"])
                msg_id = int(payload["id"])
                version = int(payload["version"])
                length = int(payload["length"])
                zlib_offset = int(payload.get("zlibOffset", -1))
                path = outdir / f"{seq:02d}_msg_{msg_id}_v{version}_len_{length}.bin"
                path.write_bytes(data)
                print(
                    f"[dump] id={msg_id} version={version} len={length} "
                    f"zlibOffset={zlib_offset} path={path}",
                    flush=True,
                )
                return
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        else:
            print(message, flush=True)

    script = session.create_script(source)
    script.on("message", on_message)
    script.load()

    print(f"[+] binary dump hook alive for {args.seconds}s; press Ctrl-C to stop", flush=True)
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        session.detach()
    return 0


if __name__ == "__main__":
    sys.exit(main())
