#!/usr/bin/env python3
"""Recall check: every method annotated @Transactional (with a body, in non-test sources) should be an
explicit declarative root in output/out/<app>.json.

Usage: check_recall.py [apps ...] [--json output/deliver/recall.json] [--misses N]"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
import txscan


def annotated_methods(app_dir, rules):
    """(file, line) of every method with a body that carries @Transactional, outside test code."""
    found = []
    for dp, dns, fns in os.walk(app_dir):
        dns[:] = sorted(x for x in dns if x not in ('.git', 'node_modules', 'target', 'build', '.gradle', '.idea'))
        for fn in sorted(fns):
            if not fn.endswith('.java'):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), app_dir)
            if txscan.is_test_path(rel):
                continue
            if any((rel.startswith(x) if x.endswith('/') else x in rel) for x in rules.get('exclude_paths', [])):
                continue
            src = open(os.path.join(dp, fn), 'rb').read()
            if b'Transactional' not in src:
                continue
            tree = txscan.PARSER.parse(src)
            stack = [tree.root_node]
            while stack:
                n = stack.pop()
                if n.type == 'method_declaration' and n.child_by_field_name('body') is not None:
                    names = set()
                    for m in (c for c in n.children if c.type == 'modifiers'):
                        for a in m.named_children:
                            if a.type in ('annotation', 'marker_annotation'):
                                nm = a.child_by_field_name('name')
                                if nm is not None:
                                    names.add(txscan.T(nm, src).split('.')[-1])
                    if 'Transactional' in names:
                        found.append((rel, n.start_point[0] + 1))
                stack.extend(n.named_children)
    return found


def check_app(app, reviewed=False):
    d = kitlib.load_out(app, reviewed=reviewed)
    roots = {(t['file'], t['line']) for t in d['transactions'] if t['kind'] == 'explicit'}
    if reviewed:   # a root excluded by review was still found by the extractor
        roots |= {(t['file'], t['line']) for t in d.get('excluded', []) if t['kind'] == 'explicit'}
    ann = annotated_methods(os.path.join(kitlib.CLONES, app), kitlib.load_rules(app))
    misses = [(f, l) for (f, l) in ann if (f, l) not in roots]
    return len(ann) - len(misses), len(ann), misses


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('apps', nargs='*')
    ap.add_argument('--json', help='also write the result to this file')
    ap.add_argument('--misses', type=int, default=20, help='misses to print (default 20)')
    ap.add_argument('--reviewed', action='store_true', help='check output/out_reviewed/ instead of output/out/')
    a = ap.parse_args()
    res = {'apps': {}, 'found': 0, 'annotated': 0, 'misses': []}
    for app in kitlib.app_list(a.apps):
        if not os.path.exists(os.path.join(kitlib.OUT_REV if a.reviewed else kitlib.OUT, app + '.json')):
            print(f"{app:45s} no output, skipped")
            continue
        found, total, misses = check_app(app, a.reviewed)
        res['apps'][app] = {'found': found, 'annotated': total, 'misses': [f"{f}:{l}" for f, l in misses]}
        res['found'] += found
        res['annotated'] += total
        res['misses'] += [f"{app} {f}:{l}" for f, l in misses]
        print(f"{app:45s} {found}/{total}")
    res['recall_pct'] = round(100 * res['found'] / max(1, res['annotated']), 2)
    print(f"TOTAL {res['found']}/{res['annotated']} = {res['recall_pct']:.2f}%")
    for m in res['misses'][:a.misses]:
        print('  miss', m)
    if a.json:
        kitlib.jdump(res, a.json)


if __name__ == '__main__':
    main()
