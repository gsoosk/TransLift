#!/usr/bin/env python3
"""Aggregate per-app extractor outputs into CSV files and corpus-level statistics.

Usage: aggregate.py [--in out_reviewed|out] [--out deliver] [apps ...]
  --in out_reviewed   reviewed outputs only; stop if an app has none (use this for the final build)
  --in out            extractor outputs only
  default             per app: out_reviewed/ when it exists, else out/ (with a warning)
Writes transactions.csv, operations.csv, corpus_summary.csv, excluded.csv and corpus_stats.json."""
import argparse, collections, csv, json, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib

EXTERNAL = ('http', 'message', 'email', 'search-index', 'object-store')
LOCK_FLAGS = {'for_update', 'pessimistic_lock', 'lock', 'advisory_lock'}
NO_TABLE_FLAGS = {'flush', 'advisory_lock', 'ddl_or_admin', 'sequence'}
TX_FIELDS = ['app', 'tx_id', 'key', 'kind', 'subkind', 'root', 'file', 'line', 'propagation', 'read_only', 'isolation',
             'non_public', 'api', 'timeout', 'rollback_for', 'no_rollback_for', 'tx_manager', 'effective', 'inside_db_tx',
             'n_ops', 'n_reads', 'n_writes', 'n_tables', 'tables_read', 'tables_written',
             'ops_in_loops', 'writes_in_loops', 'ops_conditional', 'ops_unknown_table', 'db_flags', 'db_locking',
             'versioned_entities', 'nested_tx_calls', 'external_effects', 'app_events', 'cache_ops', 'nondet_clock',
             'nondet_id', 'nondet_random', 'nondet_timer', 'app_locks', 'async_calls', 'max_call_depth',
             'methods_visited', 'unresolved_calls', 'polymorphic_skipped', 'truncated', 'invoked_by_count',
             'invoked_by', 'units_total', 'explicit_tx_called', 'signature', 'source', 'review_verdict',
             'review_confidence', 'review_depth']
OP_FIELDS = ['app', 'tx_id', 'tx_kind', 'seq', 'op', 'api', 'call', 'at', 'entity', 'tables_read', 'tables_written',
             'flags', 'in_loop', 'conditional', 'call_depth', 'sql', 'source', 'review_note']


def nondet_kind(detail):
    d = detail or ''
    if 'nanoTime' in d:
        return 'timer'
    if 'UUID' in d or 'IdWorker' in d or 'Snowflake' in d or 'ObjectId' in d or 'nextId' in d:
        return 'id'
    if 'andom' in d:
        return 'random'
    return 'clock'


def default_persistence(stats):
    mix = collections.Counter(stats.get('db_api_mix') or {})
    names = {'spring-data': 'Spring Data', 'jpa': 'JPA', 'jpa-query': 'JPA', 'hibernate': 'Hibernate',
             'mybatis': 'MyBatis', 'mybatis-plus': 'MyBatis-Plus', 'jdbc': 'JDBC', 'jdbctemplate': 'JdbcTemplate',
             'jdbi': 'JDBI', 'jooq': 'jOOQ'}
    seen = []
    for api, _ in mix.most_common():
        n = names.get(api, api)
        if n and n not in seen:
            seen.append(n)
    return ' + '.join(seen[:3])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('apps', nargs='*')
    ap.add_argument('--in', dest='src', choices=('out', 'out_reviewed'))
    ap.add_argument('--out', default=kitlib.DELIVER)
    a = ap.parse_args()
    order = kitlib.app_list(a.apps)
    meta = kitlib.repo_meta()
    sources = {}
    for app in order:
        rev, raw = os.path.join(kitlib.OUT_REV, app + '.json'), os.path.join(kitlib.OUT, app + '.json')
        if a.src == 'out_reviewed':
            if not os.path.exists(rev):
                raise SystemExit(f"{app}: no out_reviewed/{app}.json: run python3 tools/apply_reviews.py first")
            sources[app] = rev
        elif a.src == 'out' or not os.path.exists(rev):
            if os.path.exists(raw):
                sources[app] = raw
        else:
            sources[app] = rev
    unrev = [x for x in order if sources.get(x, '').startswith(kitlib.OUT + os.sep)]
    if a.src is None and unrev and len(unrev) < len(sources):
        print(f"warning: no reviewed output for {', '.join(unrev)}; using the extractor output for them", file=sys.stderr)
    src = 'out_reviewed' if not unrev else ('out' if len(unrev) == len(sources) else 'mixed')
    os.makedirs(a.out, exist_ok=True)
    all_tx, apps, excluded = [], [], []
    for app in order:
        f = sources.get(app)
        if not f:
            print(f"{app}: no extractor output, skipped", file=sys.stderr)
            continue
        d = kitlib.jload(f)
        for t in d['transactions']:
            t['_app'] = app
        for t in d.get('excluded', []):
            t['_app'] = app
        all_tx.extend(d['transactions'])
        excluded.extend(d.get('excluded', []))
        apps.append((app, d['stats'], d['transactions'], d.get('review_summary')))

    # display names, made unique (the workbook's formulas match apps by name)
    names, seen = {}, collections.Counter()
    for app in order:
        m = meta.get(app) or {}
        base = m.get('name') or app.split('_', 1)[-1]
        seen[base.lower()] += 1
        names[app] = base if seen[base.lower()] == 1 else f"{base} ({app.split('_', 1)[0]})"

    def name_of(app):
        return names[app]

    # ---------------- per-transaction inventory
    def tx_row(t):
        attrs = t.get('attrs') or {}
        eff = collections.Counter(e['kind'] for e in t.get('effects', []) if e.get('kind'))
        nd = collections.Counter(nondet_kind(n.get('detail')) for n in t.get('nondet', []))
        rv = t.get('review') or {}
        return {
            'app': name_of(t['_app']), 'tx_id': t['id'], 'key': t.get('key', ''), 'kind': t['kind'], 'subkind': t['subkind'],
            'root': t['root'], 'file': t['file'], 'line': t['line'], 'propagation': attrs.get('propagation', ''),
            'read_only': attrs.get('readOnly', ''), 'isolation': attrs.get('isolation') or '',
            'non_public': attrs.get('non_public', ''), 'api': attrs.get('api', '') or attrs.get('annotation', ''),
            'timeout': attrs.get('timeout') or '', 'rollback_for': attrs.get('rollbackFor') or '',
            'no_rollback_for': attrs.get('noRollbackFor') or '', 'tx_manager': attrs.get('manager') or '',
            'effective': '' if attrs.get('effective') is None else bool(attrs.get('effective')),
            'inside_db_tx': '' if attrs.get('inside_db_tx') is None else bool(attrs.get('inside_db_tx')),
            'n_ops': t['n_ops'], 'n_reads': t['n_reads'], 'n_writes': t['n_writes'], 'n_tables': t['n_tables'],
            'tables_read': ';'.join(t['tables_read']), 'tables_written': ';'.join(t['tables_written']),
            'ops_in_loops': t['ops_in_loops'], 'writes_in_loops': t['writes_in_loops'],
            'ops_conditional': t['ops_conditional'], 'ops_unknown_table': t['ops_unknown_table'],
            'db_flags': ';'.join(t['db_flags']), 'db_locking': bool(set(t['db_flags']) & LOCK_FLAGS),
            'versioned_entities': ';'.join(t.get('versioned_entities', [])),
            'nested_tx_calls': len(t.get('nested_tx_calls', [])),
            'external_effects': ';'.join(f"{k}x{v}" for k, v in sorted(eff.items()) if k in EXTERNAL),
            'app_events': eff.get('event', 0), 'cache_ops': eff.get('cache', 0),
            'nondet_clock': nd.get('clock', 0), 'nondet_id': nd.get('id', 0), 'nondet_random': nd.get('random', 0),
            'nondet_timer': nd.get('timer', 0),
            'app_locks': ';'.join(sorted({l['kind'] for l in t.get('app_locks', []) if l.get('kind')})),
            'async_calls': len(t.get('async', [])), 'max_call_depth': t.get('max_call_depth', 0),
            'methods_visited': t.get('methods_visited', 0), 'unresolved_calls': t.get('unresolved_calls', 0),
            'polymorphic_skipped': t.get('poly_skipped', 0), 'truncated': t.get('truncated', False),
            'invoked_by_count': len(t.get('invoked_by', [])), 'invoked_by': ' | '.join(t.get('invoked_by', [])[:5]),
            'units_total': t.get('units_total', ''), 'explicit_tx_called': ';'.join(t.get('explicit_tx_called', [])[:10]),
            'signature': (t.get('signature') or '')[:300], 'source': t.get('source', 'txscan'),
            'review_verdict': rv.get('verdict', '') or ('added' if t.get('source') == 'review' else ''),
            'review_confidence': rv.get('confidence', ''), 'review_depth': rv.get('depth', ''),
        }

    tx_rows = [tx_row(t) for t in all_tx]
    with open(os.path.join(a.out, 'transactions.csv'), 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=TX_FIELDS)
        w.writeheader(); w.writerows(tx_rows)

    op_rows = []
    for t in all_tx:
        for i, o in enumerate(t['ops']):
            op_rows.append({'app': name_of(t['_app']), 'tx_id': t['id'], 'tx_kind': t['kind'], 'seq': i + 1, 'op': o['op'],
                            'api': o.get('api') or '', 'call': o.get('call') or '', 'at': o.get('at') or '',
                            'entity': o.get('entity') or '', 'tables_read': ';'.join(o.get('reads') or []),
                            'tables_written': ';'.join(o.get('writes') or []), 'flags': ';'.join(o.get('flags') or []),
                            'in_loop': bool(o.get('loop')), 'conditional': bool(o.get('cond')), 'call_depth': o.get('depth', ''),
                            'sql': (o.get('sql') or '').replace('\n', ' ').replace('\r', ' ')[:300],
                            'source': 'review' if o.get('orig') is None and 'orig' in o else 'txscan',
                            'review_note': o.get('review', '')})
    with open(os.path.join(a.out, 'operations.csv'), 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=OP_FIELDS)
        w.writeheader(); w.writerows(op_rows)

    with open(os.path.join(a.out, 'excluded.csv'), 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['app', 'tx_id', 'key', 'kind', 'subkind', 'root', 'file', 'line', 'reason'])
        w.writeheader()
        for t in excluded:
            w.writerow({'app': name_of(t['_app']), 'tx_id': t['id'], 'key': t.get('key', ''), 'kind': t['kind'],
                        'subkind': t['subkind'], 'root': t['root'], 'file': t['file'], 'line': t['line'],
                        'reason': (t.get('review') or {}).get('exclude_reason', '')})

    # ---------------- per-app summary (extractor statistics + counts)
    rows = []
    for app, st, txs, rs in apps:
        m = meta.get(app) or {}
        exp = [t for t in txs if t['kind'] == 'explicit']
        imp = [t for t in txs if t['kind'] == 'implicit']
        rows.append({
            'app': name_of(app), 'repo': (m.get('repo') or app.replace('_', '/', 1)), 'stars': m.get('stars', ''),
            'source': m.get('source', ''), 'commit': (st.get('commit') or '')[:12], 'domain': m.get('domain', ''),
            'persistence': m.get('persistence') or default_persistence(st),
            'java_files': st['java_files'], 'lines_of_code': st['loc'], 'entities': st['entities'],
            'mybatis_statements': st['mybatis_statements'], 'db_call_sites': st['db_call_sites'],
            'entry_points': st['entry_points'],
            'explicit_declarative': sum(1 for t in exp if t['subkind'] in ('declarative', 'app-rule')),
            'explicit_programmatic': sum(1 for t in exp if t['subkind'].startswith('programmatic')),
            'implicit_multi_write': sum(1 for t in imp if t['subkind'].endswith('multi-write')),
            'implicit_read_then_write': sum(1 for t in imp if t['subkind'].endswith('read-then-write')),
            'implicit_single_write': sum(1 for t in imp if t['subkind'].endswith('single-write')),
            'implicit_read_only': sum(1 for t in imp if t['subkind'].endswith('read-only')),
            'adhoc': sum(1 for t in txs if t['kind'] == 'adhoc'),
            'explicit_with_writes': sum(1 for t in exp if t['n_writes'] > 0),
            'ops_total': sum(t['n_ops'] for t in txs),
            'ops_unknown_table': sum(t['ops_unknown_table'] for t in txs),
            'versioned_entities': st['versioned_entities'], 'app_lock_sites': st['lock_sites'],
            'sync_methods': st['sync_methods'],
            'review_confirmed': (rs or {}).get('confirmed', ''), 'review_corrected': (rs or {}).get('corrected', ''),
            'review_added': (rs or {}).get('added', ''), 'review_excluded': (rs or {}).get('excluded', ''),
            'review_unreviewed': (rs or {}).get('unreviewed', ''),
        })
    if rows:
        with open(os.path.join(a.out, 'corpus_summary.csv'), 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)

    # ---------------- corpus statistics
    def pct(n, d):
        return round(100 * n / max(1, d), 1)
    exp = [t for t in all_tx if t['kind'] == 'explicit']
    imp = [t for t in all_tx if t['kind'] == 'implicit']
    adh = [t for t in all_tx if t['kind'] == 'adhoc']
    expw = [t for t in exp if t['n_writes'] > 0]
    byid = {(r['app'], r['tx_id']): r for r in tx_rows}
    def R(t):
        return byid[(name_of(t['_app']), t['id'])]
    stats = collections.OrderedDict()
    stats['source'] = src
    stats['built'] = kitlib.today()
    stats['apps'] = len(apps)
    stats['loc'] = sum(st['loc'] for _, st, _, _ in apps)
    stats['transactions_total'] = len(all_tx)
    stats['explicit'] = len(exp)
    stats['explicit_declarative'] = sum(1 for t in exp if t['subkind'] in ('declarative', 'app-rule'))
    stats['explicit_programmatic'] = sum(1 for t in exp if t['subkind'].startswith('programmatic'))
    stats['explicit_with_writes'] = len(expw)
    stats['explicit_read_only_flag'] = sum(1 for t in exp if (t.get('attrs') or {}).get('readOnly'))
    stats['explicit_no_db_ops_found'] = sum(1 for t in exp if t['n_ops'] == 0)
    stats['explicit_on_non_public_method'] = sum(1 for t in exp if (t.get('attrs') or {}).get('non_public'))
    stats['implicit'] = len(imp)
    for sub in ('multi-write', 'read-then-write', 'single-write', 'read-only'):
        stats['implicit_' + sub] = sum(1 for t in imp if t['subkind'].endswith(sub))
    stats['implicit_distinct_write_patterns'] = len({(t['_app'], t.get('signature', t['id'])) for t in imp if not t['subkind'].endswith('read-only')})
    stats['adhoc'] = len(adh)
    stats['adhoc_kinds'] = dict(collections.Counter(k for t in adh for k in t['subkind'].split(',')))
    ops = [t['n_ops'] for t in expw]
    tabs = [t['n_tables'] for t in expw]
    stats['explicitW_ops_median'] = statistics.median(ops) if ops else 0
    stats['explicitW_ops_p90'] = statistics.quantiles(ops, n=10, method='inclusive')[-1] if len(ops) > 1 else (ops[0] if ops else 0)
    stats['explicitW_tables_median'] = statistics.median(tabs) if tabs else 0
    stats['explicitW_tables_p90'] = statistics.quantiles(tabs, n=10, method='inclusive')[-1] if len(tabs) > 1 else (tabs[0] if tabs else 0)
    stats['explicitW_single_table_pct'] = pct(sum(1 for t in expw if t['n_tables'] <= 1), len(expw))
    stats['explicitW_with_loop_writes_pct'] = pct(sum(1 for t in expw if t['writes_in_loops'] > 0), len(expw))
    stats['explicitW_with_conditional_ops_pct'] = pct(sum(1 for t in expw if t['ops_conditional'] > 0), len(expw))
    stats['explicitW_with_external_effects_pct'] = pct(sum(1 for t in expw if R(t)['external_effects']), len(expw))
    stats['explicitW_with_app_events_pct'] = pct(sum(1 for t in expw if R(t)['app_events']), len(expw))
    stats['explicitW_with_cache_ops_pct'] = pct(sum(1 for t in expw if R(t)['cache_ops']), len(expw))
    stats['explicitW_with_clock_id_random_pct'] = pct(sum(1 for t in expw if R(t)['nondet_clock'] + R(t)['nondet_id'] + R(t)['nondet_random']), len(expw))
    stats['explicitW_with_requires_new_pct'] = pct(sum(1 for t in expw if t.get('nested_tx_calls')), len(expw))
    stats['explicitW_with_async_pct'] = pct(sum(1 for t in expw if t.get('async')), len(expw))
    stats['explicitW_with_db_locking_pct'] = pct(sum(1 for t in expw if R(t)['db_locking']), len(expw))
    stats['explicitW_with_app_locks_pct'] = pct(sum(1 for t in expw if t.get('app_locks')), len(expw))
    stats['explicitW_touching_versioned_entity_pct'] = pct(sum(1 for t in expw if t.get('versioned_entities')), len(expw))
    stats['explicitW_with_unknown_table_ops_pct'] = pct(sum(1 for t in expw if t['ops_unknown_table'] > 0), len(expw))
    stats['explicitW_truncated'] = sum(1 for t in expw if t.get('truncated'))
    stats['ops_total'] = sum(t['n_ops'] for t in all_tx)
    stats['ops_unknown_table_pct'] = pct(sum(t['ops_unknown_table'] for t in all_tx), stats['ops_total'])
    stats['ops_without_own_table'] = sum(1 for t in all_tx for o in t['ops'] if set(o.get('flags') or []) & NO_TABLE_FLAGS)
    unk = sorted(((pct(r['ops_unknown_table'], r['ops_total']), r['app']) for r in rows if r['ops_total']), reverse=True)
    stats['worst_unknown_table_apps'] = [f"{n} ({p}%)" for p, n in unk[:3] if p > 0]
    rsum = collections.Counter()
    for _, _, _, rs in apps:
        for k, v in (rs or {}).items():
            if isinstance(v, int):
                rsum[k] += v
    stats['review'] = dict(rsum) if rsum else None
    recall = kitlib.jload(os.path.join(a.out, 'recall.json'), default={})
    stats['recall'] = {k: recall[k] for k in ('found', 'annotated', 'recall_pct') if k in recall} or None
    kitlib.jdump(stats, os.path.join(a.out, 'corpus_stats.json'))
    for k, v in stats.items():
        print(f"{k:42s} {v}")
    print('rows: transactions', len(tx_rows), 'operations', len(op_rows), 'excluded', len(excluded))


if __name__ == '__main__':
    main()
