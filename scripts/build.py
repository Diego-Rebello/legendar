"""Build pt-BR.srt from en.srt timings + translated parts, and validate structure."""
import re, sys
d = sys.argv[1]
en = open(f'{d}/en.srt', encoding='utf-8-sig').read().replace('\r\n', '\n').strip()
cues = {}
for b in re.split(r'\n\s*\n', en):
    l = b.split('\n')
    cues[int(l[0])] = (l[1], l[2:])

pt = {}
for n in range(1, 5):
    for line in open(f'{d}/pt{n}.txt', encoding='utf-8'):
        line = line.rstrip('\n')
        if not line:
            continue
        m = re.fullmatch(r'(\d+) (.+)', line)
        assert m, repr(line)
        i = int(m[1])
        assert i not in pt, f'dup {i}'
        pt[i] = [x.strip() for x in m[2].split('|')]

for line in open(f'{d}/overrides.txt', encoding='utf-8'):
    m = re.fullmatch(r'(\d+) (.+)', line.rstrip('\n'))
    assert m and int(m[1]) in pt, repr(line)
    pt[int(m[1])] = [x.strip() for x in m[2].split('|')]

problems = []
if set(pt) != set(cues):
    problems.append(f'ids differ: missing {sorted(set(cues) - set(pt))} extra {sorted(set(pt) - set(cues))}')

def stamp(s):
    h, m, r = s.split(':')
    sec, ms = r.split(',')
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

long_lines, fast = [], []
out = []
for i in sorted(cues):
    timing, src = cues[i]
    tr = pt.get(i, [''])
    s, t = ' '.join(src), ' '.join(tr)
    for tag in ('<i>', '</i>'):
        if s.count(tag) != t.count(tag):
            problems.append(f'{i}: {tag} count {s.count(tag)} vs {t.count(tag)}')
    if s.count('♪') != t.count('♪'):
        problems.append(f'{i}: note count {s.count("♪")} vs {t.count("♪")}')
    if t.count('<i>') != t.count('</i>'):
        problems.append(f'{i}: unbalanced italics')
    if len(re.findall(r'\[', s)) != len(re.findall(r'\[', t)):
        problems.append(f'{i}: bracket count differs')
    if len(tr) > 2 and len(tr) > len(src):
        problems.append(f'{i}: {len(tr)} lines (src {len(src)})')
    plain = [re.sub(r'</?i>', '', x) for x in tr]
    for x in plain:
        if len(x) > 42:
            long_lines.append((i, len(x), x))
    a, b = (stamp(x) for x in timing.split(' --> '))
    chars = sum(len(x) for x in plain)
    if b > a and chars / (b - a) > 22 and chars > 15:
        fast.append((i, round(chars / (b - a), 1), ' / '.join(plain)))
    out.append(f'{i}\n{timing}\n' + '\n'.join(tr) + '\n')

open(f'{d}/pt-BR.srt', 'w', encoding='utf-8').write('\n'.join(out))
print('cues', len(cues), 'translated', len(pt))
print('PROBLEMS', len(problems))
for p in problems:
    print(' ', p)
print('LONG LINES (>42)', len(long_lines))
for x in long_lines:
    print(' ', x)
print('FAST (>22 cps)', len(fast))
for x in fast:
    print(' ', x)
