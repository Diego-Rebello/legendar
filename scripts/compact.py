import re,sys
src,dst=sys.argv[1:3]
t=open(src,encoding='utf-8-sig').read().replace('\r\n','\n').strip()
blocks=re.split(r'\n\s*\n',t)
out=[]
for b in blocks:
    l=b.split('\n')
    assert re.fullmatch(r'\d+',l[0]) and '-->' in l[1],b
    out.append(l[0]+'\t'+' | '.join(l[2:]))
open(dst,'w',encoding='utf-8').write('\n'.join(out)+'\n')
print(len(blocks), 'pipe in text:', '|' in t)
