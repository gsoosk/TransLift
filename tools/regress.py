#!/usr/bin/env python3
"""Compare the previous extractor run (output/out_prev/) with the current one (out/): what an extractor change did.

Usage: regress.py [apps ...] [--show N]
  Per app: transactions by kind, operations, unknown-table share, and how many transactions appeared,
  disappeared or changed content (by stable key and content hash). --show N lists N examples of each."""
import argparse, collections, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib


def load(d, app):
    p = os.path.join(d, app + '.json')
    return kitlib.jload(p) if os.path.exists(p) else None


def summary(d):
    txs = d['transactions']
    k = collections.Counter(t['kind'] for t in txs)
    ops = sum(t['n_ops'] for t in txs)
    unk = sum(t['ops_unknown_table'] for t in txs)
    return {'tx': len(txs), 'explicit': k['explicit'], 'implicit': k['implicit'], 'adhoc': k['adhoc'], 'ops': ops,
            'unk_pct': round(100 * unk / max(1, ops), 1)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('apps', nargs='*')
    ap.add_argument('--show', type=int, default=3)
    a = ap.parse_args()
    any_change = False
    for app in kitlib.app_list(a.apps):
        old, new = load(kitlib.OUT_PREV, app), load(kitlib.OUT, app)
        if old is None or new is None:
            print(f"{app}: {'no previous run' if old is None else 'no current run'}")
            continue
        so, sn = summary(old), summary(new)
        ko = {t.get('key') or t['id']: t for t in old['transactions']}
        kn = {t.get('key') or t['id']: t for t in new['transactions']}
        io, inew = kitlib.id_to_key(old['transactions']), kitlib.id_to_key(new['transactions'])
        gone = sorted(set(ko) - set(kn))
        came = sorted(set(kn) - set(ko))
        changed = sorted(k for k in set(ko) & set(kn) if kitlib.tx_chash(ko[k], io) != kitlib.tx_chash(kn[k], inew))
        delta = ', '.join(f"{f} {so[f]}→{sn[f]}" for f in so if so[f] != sn[f])
        if not (gone or came or changed or delta):
            print(f"{app:45s} unchanged")
            continue
        any_change = True
        print(f"{app:45s} {delta or 'same counts'} · {len(came)} new, {len(gone)} gone, {len(changed)} changed")
        for label, keys, src in (('new', came, kn), ('gone', gone, ko), ('changed', changed, kn)):
            for k in keys[:a.show]:
                t = src[k]
                extra = ''
                if label == 'changed':
                    o = ko[k]
                    extra = (f"  ops {o['n_ops']}→{t['n_ops']} R{o['tables_read'][:3]}→{t['tables_read'][:3]} "
                             f"W{o['tables_written'][:3]}→{t['tables_written'][:3]}")
                print(f"    {label:7s} {t['kind']}/{t['subkind']} {t['root']} ({t['file']}:{t['line']}){extra}")
    if not any_change:
        print('no differences')


if __name__ == '__main__':
    main()
