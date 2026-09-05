#!/usr/bin/env python3
from __future__ import annotations
import argparse, re
from pathlib import Path
import lief
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

IMM_RE = re.compile(r'#(0x[0-9a-f]+|[0-9]+)', re.I)


def fileoff_to_va(binary, off:int):
    for s in binary.segments:
        start,end=s.file_offset,s.file_offset+s.physical_size
        if start <= off < end:
            return s.virtual_address + (off-start)
    return None


def exec_insns(data,binary):
    md=Cs(CS_ARCH_ARM64, CS_MODE_ARM); md.skipdata=True
    for s in binary.segments:
        if 'X' in str(s.flags):
            code=data[s.file_offset:s.file_offset+s.physical_size]
            yield from md.disasm_lite(code, s.virtual_address)


def parse_imm(op):
    m=IMM_RE.search(op)
    if not m: return None
    t=m.group(1); return int(t,16 if t.lower().startswith('0x') else 10)


def reg0(op):
    return op.split(',',1)[0].strip()


def main():
    ap=argparse.ArgumentParser(description='Find simple ARM64 adrp/add xrefs to strings.')
    ap.add_argument('elf')
    ap.add_argument('patterns', nargs='+')
    ap.add_argument('--window', type=int, default=8)
    args=ap.parse_args()
    data=Path(args.elf).read_bytes(); binary=lief.parse(args.elf)
    ins=list(exec_insns(data,binary))
    for pat in args.patterns:
        print(f'\npattern {pat!r}')
        start=0; found=[]
        bpat=pat.encode()
        while True:
            off=data.find(bpat,start)
            if off<0: break
            va=fileoff_to_va(binary,off)
            if va is not None: found.append((off,va))
            start=off+1
        for off,va in found[:20]:
            page=va & ~0xfff; low=va & 0xfff
            print(f'  string fileoff=0x{off:x} va=0x{va:x} page=0x{page:x} low=0x{low:x}')
            # adrp reg,#page then add same reg,same,#low
            count=0
            for idx,(a,_,mn,op) in enumerate(ins):
                if mn!='adrp' or parse_imm(op)!=page: continue
                r=reg0(op)
                for j in range(idx+1, min(idx+1+args.window,len(ins))):
                    a2,_,mn2,op2=ins[j]
                    if mn2=='add' and reg0(op2)==r and parse_imm(op2)==low and (','+r+',') in op2.replace(' ', '') + ',':
                        print(f'    xref adrp@0x{a:x} add@0x{a2:x}: {mn2} {op2}')
                        count+=1; break
                if count>=20: break
            if count==0: print('    no simple adrp/add xrefs found')
    return 0
if __name__=='__main__': raise SystemExit(main())
