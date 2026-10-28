#!/usr/bin/env python3
"""Debug helper: python3 -I dbg.py <app> <root-substring> [method-substring ...]
Prints the call records of methods on the walk from a root, with entity context."""
import sys as _sys
if any(x in ('-h', '--help') for x in _sys.argv[1:]):
    print(__doc__)
    raise SystemExit(0)

import json, os, sys, pickle
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txscan
import kitlib

app = sys.argv[1]
rules = kitlib.load_rules(app)
idx, scan, tb, entities, mappers, t0, t1 = txscan.prepare(os.path.join(kitlib.CLONES, app), app, rules)
pat = sys.argv[2]
roots = [mid for mid in idx.methods if pat in txscan.label_of(idx, mid) or pat in mid]
print('roots:', roots[:5])
for mid in roots[:2]:
    w = tb.walk(mid)
    for o in w['ops']:
        print('  OP', o['op'], o['api'], o['call'], o['method'].split('.')[-1], o['line'], 'ent=', o['entity'])
    for rec in scan.calls.get(mid, ()):
        print('  CALL', rec['name'], rec['cls'], rec.get('rkind'), rec.get('rtype'), 'targets=', [t.split('.')[-1] for t in rec.get('targets', [])][:4],
              'tent=', {k.split('.')[-1]: v.split('.')[-1] for k, v in (rec.get('target_ent') or {}).items()},
              'arg_ent=', rec.get('arg_entity'), 'gent=', scan.recv_entity(rec.get('rtype')) if rec.get('rtype') in idx.types else None)
for extra in sys.argv[3:]:
    for mid in idx.methods:
        if extra in mid:
            print('METHOD', mid)
            for rec in scan.calls.get(mid, ()):
                print('  CALL', rec['name'], rec['cls'], rec.get('rkind'), rec.get('rtype'), 'targets=', [t.split('.')[-1] for t in rec.get('targets', [])][:4],
                      'tent=', {k.split('.')[-1]: v.split('.')[-1] for k, v in (rec.get('target_ent') or {}).items()},
                      'arg_ent=', rec.get('arg_entity'), 'ent=', rec.get('entity'))
