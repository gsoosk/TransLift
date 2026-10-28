#!/usr/bin/env python3
"""Apply review verdicts: output/out/<app>.json + reviews/<app>/ -> output/out_reviewed/<app>.json.

Usage: apply_reviews.py [apps ...] [--strict]

  confirmed   kept as extracted
  corrected   kind, subkind, attributes, operations and effects changed as the verdict says; counts recomputed
  excluded    moved to the file's "excluded" list, with the reason
  added       transactions from reviews/<app>/added/*.json, with ids <app>:r1, r2, ...
A verdict counts only if it is valid and was stamped for the transaction's current content; other
transactions are kept as extracted and marked not reviewed. --strict exits 1 if any are left."""
import argparse, collections, copy, hashlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
import reviewlib
from txscan import norm_table

NO_TABLE_FLAGS = {'flush', 'advisory_lock', 'ddl_or_admin', 'sequence'}
SUMMARY_KEYS = ('total', 'confirmed', 'corrected', 'excluded', 'added', 'unreviewed', 'stale', 'invalid')


def tables(xs):
    return sorted({norm_table(x) for x in (xs or []) if x and norm_table(x)})


def fix_unknown(o):
    fl = [f for f in (o.get('flags') or []) if f != 'unknown_table']
    if not o.get('reads') and not o.get('writes') and not (set(fl) & NO_TABLE_FLAGS):
        fl.append('unknown_table')
    o['flags'] = sorted(set(fl))


def resummarize(t, versioned):
    """Recompute the per-transaction counts from its operation list (as txscan's summarize does)."""
    ops = t['ops']
    reads, writes, flags = set(), set(), set()
    for o in ops:
        reads |= set(o.get('reads') or [])
        writes |= set(o.get('writes') or [])
        flags |= set(o.get('flags') or [])
    t.update({
        'n_ops': len(ops), 'n_reads': sum(o['op'] == 'R' for o in ops), 'n_writes': sum(o['op'] == 'W' for o in ops),
        'n_unknown_op': sum(o['op'] == 'U' for o in ops), 'n_locks_db': sum(o['op'] == 'L' for o in ops),
        'tables_read': sorted(reads), 'tables_written': sorted(writes), 'n_tables': len(reads | writes),
        'ops_in_loops': sum(1 for o in ops if o.get('loop')),
        'writes_in_loops': sum(1 for o in ops if o.get('loop') and o['op'] == 'W'),
        'ops_conditional': sum(1 for o in ops if o.get('cond')),
        'ops_unknown_table': sum(1 for o in ops if 'unknown_table' in (o.get('flags') or [])),
        'db_flags': sorted(flags),
        'versioned_entities': sorted({n for tab in reads | writes for n in versioned.get(tab, ())}),
    })


def apply_corrections(t, v, changes):
    if v.get('kind') and v['kind'] != t['kind']:
        changes.append(f"kind {t['kind']} → {v['kind']}")
        t['kind'] = v['kind']
    if v.get('subkind') and v['subkind'] != t['subkind']:
        changes.append(f"subkind {t['subkind']} → {v['subkind']}")
        t['subkind'] = v['subkind']
    for k, val in (v.get('attrs') or {}).items():
        old = (t.get('attrs') or {}).get(k)
        if old != val:
            changes.append(f"{k}: {old} → {val}")
            t.setdefault('attrs', {})[k] = val
    remove = {}
    for r in v.get('ops_remove') or []:
        if isinstance(r, dict):
            remove[r['n']] = r.get('note', '')
        else:
            remove[r] = ''
    fixes = {f['n']: f for f in v.get('ops_fix') or []}
    new_ops = []
    for i, o in enumerate(t['ops']):
        n = i + 1
        if n in remove:
            changes.append(f"removed operation {n}: `{o.get('call')}` at {o.get('at')}" + (f" ({remove[n]})" if remove[n] else ''))
            continue
        if n in fixes:
            f, what = fixes[n], []
            for k in ('op', 'entity', 'loop', 'cond'):
                if k in f and f[k] != o.get(k):
                    what.append(f"{k} {o.get(k)} → {f[k]}")
                    o[k] = f[k]
            for k in ('reads', 'writes'):
                if k in f:
                    nv = tables(f[k])
                    if nv != (o.get(k) or []):
                        what.append(f"{k} {o.get(k) or []} → {nv}")
                        o[k] = nv
            if f.get('flags_add'):
                o['flags'] = sorted(set(o.get('flags') or []) | set(f['flags_add']))
                what.append('flags +' + ','.join(f['flags_add']))
            if f.get('flags_remove'):
                o['flags'] = sorted(set(o.get('flags') or []) - set(f['flags_remove']))
                what.append('flags -' + ','.join(f['flags_remove']))
            fix_unknown(o)
            o['review'] = 'fixed: ' + f['note']
            changes.append(f"fixed operation {n}: `{o.get('call')}` at {o.get('at')}: {'; '.join(what) or 'note only'} ({f['note']})")
        new_ops.append(o)
    for a in v.get('ops_add') or []:
        new_ops.append(new_op(a))
        o = new_ops[-1]
        on = ', '.join(o['writes'] or o['reads'])
        changes.append(f"added {o['op']} `{o['call']}` at {o['at']}" + (f" on {on}" if on else '') + f" ({a['note']})")
    t['ops'] = new_ops
    for e in v.get('effects_add') or []:
        ln = e['at'].rpartition(':')[2]
        t.setdefault('effects', []).append({'kind': e['kind'], 'method': None, 'line': int(ln), 'at': e['at'],
                                            'detail': e.get('note', ''), 'source': 'review'})
        changes.append(f"added effect {e['kind']} at {e['at']}" + (f" ({e['note']})" if e.get('note') else ''))


def new_op(a):
    o = {'op': a['op'], 'api': a.get('api') or 'review', 'call': a['call'], 'reads': tables(a.get('reads')),
         'writes': tables(a.get('writes')), 'flags': sorted(set(a.get('flags') or [])), 'loop': bool(a.get('loop')),
         'cond': bool(a.get('cond')), 'depth': None, 'at': a['at'], 'entity': a.get('entity'), 'sql': a.get('sql'),
         'orig': None, 'review': 'added: ' + a['note']}
    fix_unknown(o)
    return o


def apply_app(slug, problems):
    d = kitlib.load_out(slug)
    txs = d['transactions']
    id2key = kitlib.id_to_key(txs)
    versioned = collections.defaultdict(set)
    for name, e in (d.get('entities') or {}).items():
        if e.get('versioned'):
            versioned[e['table']].add(name)
    kept, excluded = [], []
    summ = collections.Counter({k: 0 for k in SUMMARY_KEYS})
    for t0 in txs:
        summ['total'] += 1
        t = copy.deepcopy(t0)
        item = {'app': slug, 'key': t['key'], 'n_ops': t['n_ops'], 'kind': t['kind'], 'chash': kitlib.tx_chash(t0, id2key)}
        st, v, errs = reviewlib.verdict_state(item)
        n_orig = t['n_ops']
        for i, o in enumerate(t['ops']):
            o['orig'] = i
        if st != 'ok':
            if st == 'invalid':
                problems.append(f"{slug} {t['key']}: invalid verdict: {'; '.join(errs)}")
            t['review'] = {'verdict': None, 'stale': st in ('stale', 'unstamped'), 'n_ops_txscan': n_orig}
            summ['unreviewed'] += 1
            summ['stale'] += st in ('stale', 'unstamped')
            summ['invalid'] += st == 'invalid'
            kept.append(t)
            continue
        rv = {'verdict': v['verdict'], 'confidence': v['confidence'], 'depth': v['depth'],
              'boundary': v.get('boundary', ''), 'notes': v.get('notes', ''), 'batch': v.get('batch'),
              'n_ops_txscan': n_orig, 'kind_txscan': t['kind'], 'changes': []}
        if v['verdict'] == 'excluded':
            rv['exclude_reason'] = v['exclude_reason']
            t['review'] = rv
            excluded.append(t)
            summ['excluded'] += 1
            continue
        if v['verdict'] == 'corrected':
            apply_corrections(t, v, rv['changes'])
            summ['corrected'] += 1
        else:
            summ['confirmed'] += 1
        t['review'] = rv
        kept.append(t)
    # transactions a reviewer added
    adir = os.path.join(kitlib.REVIEWS, slug, 'added')
    adds = []
    for fn in sorted(os.listdir(adir)) if os.path.isdir(adir) else []:
        if not fn.endswith('.json'):
            continue
        try:
            x = kitlib.jload(os.path.join(adir, fn))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{slug} added/{fn}: not valid JSON: {e}")
            continue
        errs = kitlib.validate_added(x)
        if errs:
            problems.append(f"{slug} added/{fn}: {'; '.join(errs)}")
            continue
        adds.append((x['file'], x['line'], x['root'], fn, x))
    roots = {(t['file'], t['line'], t['kind']) for t in kept + excluded}
    n = 0
    for file, line, root, fn, x in sorted(adds):
        if (file, line, x['kind']) in roots:
            problems.append(f"{slug} added/{fn}: a {x['kind']} transaction rooted at {file}:{line} already exists; "
                            f"correct that one instead")
            continue
        roots.add((file, line, x['kind']))
        n += 1
        t = {'app': slug, 'id': f"{slug}:r{n}",
             'key': 'r' + hashlib.sha1(f"added|{x['kind']}|{file}|{line}".encode()).hexdigest()[:11],
             'kind': x['kind'], 'subkind': x['subkind'], 'root': root, 'root_id': None, 'file': file, 'line': line,
             'attrs': x.get('attrs') or {}, 'nested_tx_calls': [], 'async': [], 'nondet': [], 'app_locks': [],
             'txapis': [], 'unresolved_calls': 0, 'poly_skipped': 0, 'methods_visited': 0, 'truncated': False,
             'max_call_depth': 0, 'invoked_by': [], 'source': 'review',
             'effects': [{'kind': e.get('kind'), 'method': None, 'line': int(str(e.get('at', ':0')).rpartition(':')[2] or 0),
                          'at': e.get('at'), 'detail': e.get('note', ''), 'source': 'review'} for e in x.get('effects') or []],
             'ops': [new_op(o) for o in x['ops']],
             'review': {'verdict': 'added', 'confidence': x['confidence'], 'depth': 'full', 'boundary': x['boundary'],
                        'notes': x.get('notes', ''), 'batch': x.get('found_in_batch'), 'changes': [], 'file': fn}}
        kept.append(t)
        summ['added'] += 1
    # recompute counts; implicit transactions also cover the tables of the explicit transactions they call
    for t in kept:
        resummarize(t, versioned)
    byid = {t['id']: t for t in kept + excluded}
    for t in kept:
        # entry points (implicit, or made explicit by review) also cover the tables of the transactions they call
        if t['kind'] != 'implicit' and not t.get('explicit_tx_called'):
            continue
        r, w = set(t['tables_read']), set(t['tables_written'])
        for x in t.get('explicit_tx_called', []):
            c = byid.get(x)
            if c:
                r |= set(c['tables_read'])
                w |= set(c['tables_written'])
        t['tables_read'], t['tables_written'], t['n_tables'] = sorted(r), sorted(w), len(r | w)
        t['signature'] = '|'.join(sorted({f"{o['op']}:{o['call']}@{o['at']}" for o in t['ops']}) +
                                  sorted(t.get('explicit_tx_called', [])))
    st = d['stats']
    st['txscan_counts'] = {k: st.get(k) for k in ('tx_declarative', 'tx_programmatic', 'tx_implicit', 'tx_adhoc')}
    st['tx_declarative'] = sum(1 for t in kept if t['kind'] == 'explicit' and t['subkind'] in ('declarative', 'app-rule'))
    st['tx_programmatic'] = sum(1 for t in kept if t['kind'] == 'explicit' and t['subkind'].startswith('programmatic'))
    st['tx_implicit'] = sum(1 for t in kept if t['kind'] == 'implicit')
    st['tx_adhoc'] = sum(1 for t in kept if t['kind'] == 'adhoc')
    d['transactions'] = kept
    d['excluded'] = excluded
    d['review_summary'] = {k: summ[k] for k in SUMMARY_KEYS}
    d['review_applied_at'] = kitlib.now()
    kitlib.jdump(d, os.path.join(kitlib.OUT_REV, slug + '.json'))
    return d['review_summary']


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('apps', nargs='*')
    ap.add_argument('--strict', action='store_true')
    a = ap.parse_args()
    problems, tot = [], collections.Counter()
    for slug in kitlib.app_list(a.apps):
        if not os.path.exists(os.path.join(kitlib.OUT, slug + '.json')):
            print(f"{slug}: no extractor output, skipped")
            continue
        s = apply_app(slug, problems)
        tot.update(s)
        print(f"{slug:45s} {s['total']:5d} extracted · {s['confirmed']} confirmed · {s['corrected']} corrected · "
              f"{s['excluded']} excluded · {s['added']} added · {s['unreviewed']} not reviewed"
              + (f" ({s['stale']} stale, {s['invalid']} invalid)" if s['stale'] or s['invalid'] else ''))
    print(f"{'TOTAL':45s} {tot['total']:5d} extracted · {tot['confirmed']} confirmed · {tot['corrected']} corrected · "
          f"{tot['excluded']} excluded · {tot['added']} added · {tot['unreviewed']} not reviewed")
    for p in problems[:50]:
        print('  problem:', p)
    if len(problems) > 50:
        print(f"  ... {len(problems) - 50} more problems")
    if a.strict and tot['unreviewed']:
        sys.exit(1)


if __name__ == '__main__':
    main()
