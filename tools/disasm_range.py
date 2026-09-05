#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import lief
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM


def va_to_offset(binary, va: int) -> int | None:
    for seg in binary.segments:
        start = seg.virtual_address
        end = start + seg.physical_size
        if start <= va < end:
            return seg.file_offset + va - start
    return None


def main() -> int:
    p = argparse.ArgumentParser(description='Disassemble an ARM64 ELF virtual-address range using LIEF+Capstone.')
    p.add_argument('elf')
    p.add_argument('start', type=lambda s: int(s, 0))
    p.add_argument('end', type=lambda s: int(s, 0))
    p.add_argument('--bytes', action='store_true')
    args = p.parse_args()

    data = Path(args.elf).read_bytes()
    binary = lief.parse(args.elf)
    off = va_to_offset(binary, args.start)
    if off is None:
        raise SystemExit(f'start VA 0x{args.start:x} is not mapped')
    size = max(0, args.end - args.start)
    code = data[off:off+size]
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    for ins in md.disasm(code, args.start):
        b = ' '.join(f'{x:02x}' for x in ins.bytes)
        if args.bytes:
            print(f'0x{ins.address:08x}: {b:<15} {ins.mnemonic:<8} {ins.op_str}')
        else:
            print(f'0x{ins.address:08x}: {ins.mnemonic:<8} {ins.op_str}')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
