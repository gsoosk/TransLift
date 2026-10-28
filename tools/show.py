#!/usr/bin/env python3
"""Print a readable summary of a txscan JSON file. Usage: show.py out/<app>.json [maxtx] [kindfilter]"""
import sys as _sys
if any(x in ('-h', '--help') for x in _sys.argv[1:]):
    print(__doc__)
    raise SystemExit(0)

import json, sys, collections

d = json.load(open(sys.argv[1]))
maxtx = int(sys.argv[2]) if len(sys.argv) > 2 else 40
kf = sys.argv[3] if len(sys.argv) > 3 else None
s = d['stats']
print(f"{s['app']}: files={s['java_files']} loc={s['loc']} entities={s['entities']} (versioned {s['versioned_entities']}) "
      f"mybatis_stmts={s['mybatis_statements']} dbcalls={s['db_call_sites']} entries={s['entry_points']} {s['entry_points_by_kind']}")
print(f"  explicit decl={s['tx_declarative']} prog={s['tx_programmatic']} implicit={s['tx_implicit']} adhoc={s['tx_adhoc']} "
      f"locks={s['lock_sites']} sync={s['sync_methods']} api={s['db_api_mix']} t={s['seconds_index_scan']}+{s['seconds_tx']}s")
kinds = collections.Counter((t['kind'], t['subkind']) for t in d['transactions'])
print('  ', dict(kinds))
n = 0
for t in d['transactions']:
    if kf and kf not in t['kind'] + '/' + t['subkind']:
        continue
    n += 1
    if n > maxtx:
        break
    attrs = {k: v for k, v in t['attrs'].items() if v and k not in ('source',)}
    print(f"[{t['kind']}/{t['subkind']}] {t['root']} ({t['file']}:{t['line']}) R{t['n_reads']} W{t['n_writes']} U{t['n_unknown_op']} "
          f"r={t['tables_read'][:8]} w={t['tables_written'][:8]} loopW={t['writes_in_loops']} unk={t['ops_unknown_table']} "
          f"depth={t['max_call_depth']} visited={t['methods_visited']} {attrs}")
    if t.get('invoked_by'):
        print('     invoked_by:', t['invoked_by'][:3], '...' if len(t['invoked_by']) > 3 else '')
    for o in t['ops'][:10]:
        print('     ', o['op'], o['api'], o['call'], o['at'], 'R', o['reads'], 'W', o['writes'], o['flags'] or '',
              'loop' if o['loop'] else '', 'cond' if o['cond'] else '', o['entity'] or '', (o['sql'] or '')[:70])
    extra = []
    for k in ('nested_tx_calls', 'effects', 'nondet', 'async', 'app_locks', 'txapis'):
        if t.get(k):
            extra.append(f"{k}={len(t[k])}")
    if extra:
        print('      +', ' '.join(extra))
