#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import lief
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM


def exec_ranges(binary):
    for s in binary.segments:
        if 'X' in str(s.flags):
            yield s.virtual_address, s.virtual_address+s.physical_size, s.file_offset


def main():
    ap=argparse.ArgumentParser(description='Find direct ARM64 b/bl instructions to target VAs.')
    ap.add_argument('elf')
    ap.add_argument('targets', nargs='+', type=lambda s:int(s,0))
    ap.add_argument('--window', type=int, default=0)
    args=ap.parse_args()
    data=Path(args.elf).read_bytes(); binary=lief.parse(args.elf)
    targets=set(args.targets)
    md=Cs(CS_ARCH_ARM64, CS_MODE_ARM); md.skipdata=True
    hits=[]
    for start,end,off in exec_ranges(binary):
        for addr,_,mn,op in md.disasm_lite(data[off:off+end-start], start):
            if mn not in ('bl','b') or not op.startswith('#0x'):
                continue
            try: dest=int(op[1:],16)
            except ValueError: continue
            if dest in targets:
                hits.append((addr,mn,dest))
    for addr,mn,dest in hits:
        print(f'0x{addr:x}: {mn} 0x{dest:x}')
    return 0
if __name__=='__main__': raise SystemExit(main())
