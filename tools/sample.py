#!/usr/bin/env python3
"""Random spot-check sample across apps, with the source line of every operation: sample.py <seed> <n> [kind]"""
import sys as _sys
if any(x in ('-h', '--help') for x in _sys.argv[1:]):
    print(__doc__)
    raise SystemExit(0)

import json, glob, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
seed, n = int(sys.argv[1]), int(sys.argv[2])
kind = sys.argv[3] if len(sys.argv) > 3 else None
rnd = random.Random(seed)
apps = [os.path.join(kitlib.OUT, a + '.json') for a in kitlib.app_list() if os.path.exists(os.path.join(kitlib.OUT, a + '.json'))]
rnd.shuffle(apps)
picked = 0
for f in apps:
    if picked >= n:
        break
    app = os.path.basename(f)[:-5]
    d = json.load(open(f))
    cands = [t for t in d['transactions'] if 1 <= t['n_ops'] <= 10 and t['n_writes'] > 0 and (kind is None or t['kind'] == kind)]
    if not cands:
        continue
    t = rnd.choice(cands)
    picked += 1
    print(f"\n### {app} [{t['kind']}/{t['subkind']}] {t['root']} ({t['file']}:{t['line']}) R={t['tables_read']} W={t['tables_written']}")
    for o in t['ops']:
        cls, line = o['at'].rsplit(':', 1)
        # find file with this class name
        hits = [x for x in glob.glob(os.path.join(kitlib.CLONES, app, '**', cls + '.java'), recursive=True) if '/test/' not in x]
        txt = ''
        if hits:
            try:
                txt = open(hits[0], errors='replace').read().split('\n')[int(line) - 1].strip()[:150]
            except Exception:
                pass
        print(f"   {o['op']} {o['api']:12s} {o['at']:40s} ent={o['entity']} R={o['reads']} W={o['writes']} {o['flags'] or ''}\n        src: {txt}")
