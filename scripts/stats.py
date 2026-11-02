import re,sys,collections
rows=[l.rstrip('\n').split('\t',1) for l in open(sys.argv[1],encoding='utf-8')]
strip=lambda s:re.sub(r'</?i>','',s).strip()
sfx,mus,lyr,dial=[],[],[],[]
for i,t in rows:
    p=[strip(x).lstrip('- ').strip() for x in t.split('|')]
    if all(re.fullmatch(r'\[[^\]]*\]',x) for x in p): sfx.append(t)
    elif all(re.fullmatch(r'♪[^♪]*',x) and not x.endswith('♪') or re.fullmatch(r'♪',x) or re.fullmatch(r'♪ [a-z ]+ music ♪',x) for x in p): mus.append(t)
    elif '♪' in t: lyr.append(t)
    else: dial.append(t)
n=len(rows)
for name,g in [('efeitos [..]',sfx),('tags de musica',mus),('letras',lyr),('dialogo',dial)]:
    print(f'{name:16}{len(g):4} cues {len(g)*100//n:3}%  {sum(map(len,g)):6} chars  unicos {len(set(g))}')
print('total chars',sum(len(t) for _,t in rows))
print('repetidos fora dialogo',len(sfx)+len(mus)-len(set(sfx))-len(set(mus)))
