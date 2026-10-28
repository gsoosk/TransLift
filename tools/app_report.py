#!/usr/bin/env python3
"""Calibration report for one app: what txscan found, where it is unsure, and hints for rules/<app>.json.

Usage: app_report.py <app> [--no-recall]"""
import argparse, collections, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
import txscan

HINT = re.compile(r'(^|[-_.])(test|tests|testing|it|demo|demos|example|examples|sample|samples|fixture|fixtures|mock|mocks|'
                  r'benchmark|benchmarks|jmh|generator|codegen|archetype|tutorial|quickstart|playground|sandbox|e2e|'
                  r'perf|load|stress|simulator|tools?)([-_.]|$)', re.I)


def modules(app_dir, rules):
    """Non-test Java files per top-level directory (two levels for aggregator projects)."""
    count = collections.Counter()
    for dp, dns, fns in os.walk(app_dir):
        dns[:] = sorted(x for x in dns if x not in ('.git', 'node_modules', 'target', 'build', '.gradle', '.idea'))
        for fn in fns:
            if not fn.endswith('.java'):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), app_dir)
            if txscan.is_test_path(rel):
                continue
            if any((rel.startswith(x) if x.endswith('/') else x in rel) for x in rules.get('exclude_paths', [])):
                continue
            parts = rel.split(os.sep)
            key = parts[0] if len(parts) < 3 or parts[0] == 'src' else '/'.join(parts[:2]) if parts[1] != 'src' else parts[0]
            count[key] += 1
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('app')
    ap.add_argument('--no-recall', action='store_true')
    a = ap.parse_args()
    app = kitlib.app_list([a.app])[0]
    meta = kitlib.repo_meta()[app]
    d = kitlib.load_out(app)
    st, txs = d['stats'], d['transactions']
    rules = kitlib.load_rules(app)
    sha, date = kitlib.read_pin(app)
    out_f = os.path.join(kitlib.OUT, app + '.json')
    import datetime
    run_at = datetime.datetime.fromtimestamp(os.path.getmtime(out_f)).strftime('%Y-%m-%d %H:%M')
    print(f"== {app} ({meta['repo']}) @ {(sha or '?')[:12]} ({date or '?'})")
    print(f"extractor: txscan {kitlib.sha256_file(os.path.join(kitlib.ROOT, 'tools', 'txscan.py'))[:12]}, run {run_at}")
    print(f"code: {st['java_files']:,} Java files (non-test) · {st['loc']:,} lines · {st['types']:,} types · "
          f"{st['methods']:,} methods · {st['parse_error_files']} files with parse errors")
    mix = sorted((st.get('db_api_mix') or {}).items(), key=lambda kv: -kv[1])
    print(f"persistence calls: {', '.join(f'{k} {v}' for k, v in mix) or 'none'}")
    print(f"entities: {st['entities']} ({st['versioned_entities']} with @Version) · MyBatis statements: {st['mybatis_statements']} · "
          f"DB call sites: {st['db_call_sites']}")
    print(f"entry points: {st['entry_points']} {json.dumps(st.get('entry_points_by_kind') or {})}")
    kinds = collections.Counter(t['kind'] for t in txs)
    sub = collections.Counter(t['subkind'].rsplit('/', 1)[-1] for t in txs if t['kind'] == 'implicit')
    adh = collections.Counter(k for t in txs if t['kind'] == 'adhoc' for k in t['subkind'].split(','))
    print(f"transactions: {len(txs)} · explicit {kinds['explicit']} (declarative {st['tx_declarative']}, programmatic "
          f"{st['tx_programmatic']}) · implicit {kinds['implicit']} {dict(sub)} · adhoc {kinds['adhoc']} {dict(adh)}")
    writes = sum(1 for t in txs if t['n_writes'] > 0)
    print(f"  with at least one write: {writes}")
    if not a.no_recall:
        import check_recall
        found, total, misses = check_recall.check_app(app)
        print(f"recall: {found}/{total} @Transactional methods are explicit roots" +
              (f"; misses: {', '.join(f'{f}:{l}' for f, l in misses[:8])}" if misses else ''))
    n_ops = sum(t['n_ops'] for t in txs)
    unk = sum(t['ops_unknown_table'] for t in txs)
    print(f"operations: {n_ops:,} · unknown table {unk:,} ({100 * unk / max(1, n_ops):.1f}%)")
    hot = collections.Counter()
    for t in txs:
        for o in t['ops']:
            if 'unknown_table' in o['flags']:
                hot[(o['api'], o['call'], o['at'].split(':')[0])] += 1
    if hot:
        print('unknown-table hot spots (api, call, class: count):')
        for (api, call, cls), n in hot.most_common(15):
            print(f"  {n:5d}  {api:12s} {call:30s} {cls}")
    empty = [t for t in txs if t['kind'] == 'explicit' and t['n_ops'] == 0]
    print(f"explicit transactions with no DB operation found: {len(empty)}")
    for t in empty[:8]:
        print(f"  {t['root']} ({t['file']}:{t['line']}) unresolved={t['unresolved_calls']} poly_skipped={t['poly_skipped']}")
    big = sorted(txs, key=lambda t: -t['n_ops'])[:8]
    print('largest transactions:')
    for t in big:
        print(f"  {t['n_ops']:5d} ops  {t['kind']}/{t['subkind']}  {t['root']}" + ('  [walk truncated]' if t['truncated'] else ''))
    trunc = sum(1 for t in txs if t['truncated'])
    print(f"analysis gaps: {trunc} walks truncated · {sum(t['poly_skipped'] for t in txs):,} calls with >8 targets skipped · "
          f"{sum(t['unresolved_calls'] for t in txs):,} unresolved interface calls (summed over transactions)")
    if st.get('entity_table_consts_unresolved'):
        print(f"entity table names not resolved: {', '.join(st['entity_table_consts_unresolved'][:10])}")
    err = os.path.join(kitlib.OUT, app + '.err')
    if os.path.exists(err) and os.path.getsize(err):
        tail = open(err, encoding='utf8', errors='replace').read().strip().splitlines()[-5:]
        print('txscan log (last lines):')
        for l in tail:
            print('  ' + l[:200])
    mods = modules(os.path.join(kitlib.CLONES, app), rules)
    print('Java files by module (non-test, after exclude_paths):')
    for m, n in sorted(mods.items(), key=lambda kv: -kv[1])[:25]:
        flag = '   <- name suggests test/demo/tooling code: consider exclude_paths' if HINT.search(m) else ''
        print(f"  {n:6d}  {m}{flag}")
    print(f"rules/{app}.json: {json.dumps(rules) if rules else '(none)'}")


if __name__ == '__main__':
    main()
