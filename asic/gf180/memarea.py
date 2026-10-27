# memarea.py MEMS_DUMP: on-chip RAM area on GF180 5 V, cheapest of foundry
# SRAM macro tiles (64/128/256/512 x 8) or flip-flops (64 um2/bit) per memory.
import re,sys
MAC={64:0.1006,128:0.1161,256:0.1472,512:0.2094}
t=open(sys.argv[1]).read(); rows=[]
for c in re.split(r'\n\s*cell \$mem_v2 ',t)[1:]:
    name=c.split('\n')[0].strip().lstrip('\\')
    iv=lambda s: int(s.split("'")[1],2) if "'" in s else int(s)
    gg=lambda k: iv(re.search(r'parameter \\'+k+r' (\S+)',c).group(1))
    S,W,wr,rd=gg('SIZE'),gg('WIDTH'),gg('WR_PORTS'),gg('RD_PORTS')
    flop=S*W*64e-6
    best,how=flop,'flops'
    if wr>0 and rd==1:
        for d,a in MAC.items():
            m=((S+d-1)//d)*((W+7)//8)*a
            if m<best: best,how=m,f'{(S+d-1)//d}x{(W+7)//8} of {d}x8'
    if wr==0: best,how=0,'ROM->logic'
    rows.append((best,S,W,how,name))
rows.sort(reverse=True); tot=sum(r[0] for r in rows)
bits=sum(r[1]*r[2] for r in rows)
print('%d memories, %.1f KiB, area %.2f mm2'%(len(rows),bits/8192,tot))
for r in rows[:14]: print('%.3f mm2 %5dx%-4d %-16s %s'%(r[0],r[1],r[2],r[3],r[4][:70]))
