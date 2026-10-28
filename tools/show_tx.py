#!/usr/bin/env python3
"""Print transactions of one app the way reviewers see them (it re-walks the code, so big apps take a minute).

  show_tx.py <app> <key|id|number> [...]
  show_tx.py <app> --sample N [--seed S] [--kind explicit|implicit|adhoc]   a spread over kinds and sizes
  show_tx.py <app> --list [--kind K]                                        one line per transaction
  --brief   only the header and the operations of each transaction (fast: no code, no re-walk)"""
import argparse, collections, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib


def sample(txs, n, seed):
    rnd = random.Random(seed)
    groups = collections.defaultdict(list)
    for t in txs:
        size = 'none' if t['n_ops'] == 0 else ('small' if t['n_ops'] <= 3 else ('medium' if t['n_ops'] <= 15 else 'large'))
        groups[(t['kind'], size)].append(t)
    for g in groups.values():
        rnd.shuffle(g)
    keys = sorted(groups, key=lambda k: (k[1] == 'none', k))      # transactions without operations last
    pick = []
    while len(pick) < n and any(groups.values()):
        for k in keys:
            if groups[k] and len(pick) < n:
                pick.append(groups[k].pop())
    return pick


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('app')
    ap.add_argument('which', nargs='*')
    ap.add_argument('--sample', type=int, default=0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--kind')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--brief', action='store_true')
    ap.add_argument('--reviewed', action='store_true', help='show output/out_reviewed/ instead of output/out/')
    a = ap.parse_args()
    app = kitlib.app_list([a.app])[0]
    d = kitlib.load_out(app, reviewed=a.reviewed)
    txs = [t for t in d['transactions'] if not a.kind or t['kind'] == a.kind]
    if a.list:
        for t in txs:
            print(f"{kitlib.tx_num(t):>6} {t['key']} {t['kind']}/{t['subkind']} {t['root']} ({t['file']}:{t['line']}) "
                  f"ops={t['n_ops']} R={','.join(t['tables_read'][:4])} W={','.join(t['tables_written'][:4])}")
        return
    if a.sample:
        sel = sample(txs, a.sample, a.seed)
    elif a.which:
        want = set(a.which)
        sel = [t for t in d['transactions'] if t['key'] in want or t['id'] in want or kitlib.tx_num(t) in want]
        found = {x for t in sel for x in (t['key'], t['id'], kitlib.tx_num(t))}
        for w in sorted(want - found):
            print(f"not found: {w}", file=sys.stderr)
    else:
        raise SystemExit('give transaction keys/ids/numbers, --sample N or --list')
    if not sel:
        raise SystemExit('nothing to show')
    print('Showing: ' + ', '.join(f"{kitlib.tx_num(t)} ({t['kind']}, {t['n_ops']} ops)" for t in sel) + '\n')
    if a.brief:
        for t in sel:
            attrs = {k: v for k, v in (t.get('attrs') or {}).items() if v and k in ('propagation', 'readOnly', 'isolation', 'api', 'annotation', 'inside_db_tx')}
            print(f"## {t['id']} · key {t['key']} · {t['kind']}/{t['subkind']} · {t['root']} ({t['file']}:{t['line']}) {attrs or ''}")
            print(f"   reads {', '.join(t['tables_read']) or '-'} · writes {', '.join(t['tables_written']) or '-'} · "
                  f"{t['n_ops']} ops, {t['ops_unknown_table']} unknown table, {t.get('poly_skipped', 0)} calls skipped")
            for i, o in enumerate(t['ops'], 1):
                fl = ','.join((o.get('flags') or []) + (['loop'] if o.get('loop') else []) + (['cond'] if o.get('cond') else []))
                print(f"   {i:3d} {o['op']} {o.get('api') or '':12s} {o.get('call') or '':28s} {o.get('at') or '':40s} "
                      f"R={','.join(o.get('reads') or [])} W={','.join(o.get('writes') or [])} {fl}")
            print()
        return
    import export_blocks
    ex = export_blocks.AppExport(app, reviewed=a.reviewed)
    files = {t['id']: ex.rel_name(t, 'key') for t in ex.data['transactions']}
    for t in sel:
        text, _, _ = ex.render(ex.by_id[t['id']], files)
        print(text)
        print('\n' + '=' * 100 + '\n')


if __name__ == '__main__':
    main()
