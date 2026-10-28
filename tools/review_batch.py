#!/usr/bin/env python3
"""Reviewer's helper for one batch.

  review_batch.py show <batch>              the transactions to review, their drafts and verdict files
  review_batch.py check <batch>             validate every verdict of the batch and stamp it (run until it passes)
  review_batch.py check-all                 the same for every batch (lists invalid or stale verdicts only)
  review_batch.py find <app> <text>         transactions whose root file or root contains <text> (to spot missed ones)
  review_batch.py added <app>               validate the transactions reviewers added for <app>

Verdict files: reviews/<app>/tx/<key>.json. Added transactions: reviews/<app>/added/<name>.json.
The format is in docs/review-rules.md."""
import argparse, collections, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
import reviewlib


def load_batch(bid):
    if not kitlib.SAFE.match(bid):
        raise SystemExit(f"bad batch id {bid!r}")
    p = kitlib.batch_path(bid)
    if not os.path.exists(p):
        raise SystemExit(f"no batch {bid} (see python3 tools/review_status.py --pending)")
    return kitlib.jload(p)


def cmd_show(a):
    b = load_batch(a.batch)
    current = {it['key']: it['chash'] for it in reviewlib.app_items(b['app'])}
    print(f"batch {b['id']} · app {b['app']} · {len(b['items'])} transactions · ~{b['tokens']:,} draft tokens"
          + (' · one large transaction: review it at depth "sampled" if it has more than 40 operations' if b.get('large') else ''))
    print(f"source checkout: clones/{b['app']}/   rules: docs/review-rules.md\n")
    for i, it in enumerate(b['items'], 1):
        st, _, errs = reviewlib.verdict_state(it)
        if current.get(it['key']) != it['chash']:
            st = 'outdated: the transaction changed after this batch was made; skip it'
        print(f"{i:2d}. {it['kind']}/{it['subkind']}  {it['root']}  ({it['file']}:{it['line']})")
        print(f"    key {it['key']} · {it['n_ops']} ops · ~{it['tokens']:,} tokens · verdict: {st}")
        print(f"    draft   {it['draft']}")
        print(f"    verdict reviews/{it['app']}/tx/{it['key']}.json")
    print(f"\nWhen done: python3 tools/review_batch.py check {b['id']}")


def check_batch(b, report_missing=True):
    """Validate and stamp the verdicts of one batch. Returns a dict: counts per verdict, and the numbers of
    missing, invalid, stale and outdated items and of added transactions."""
    res = {'counts': {}, 'missing': 0, 'invalid': 0, 'stale': 0, 'outdated': 0, 'added': 0, 'added_invalid': 0}
    current = {it['key']: it['chash'] for it in reviewlib.app_items(b['app'])}
    for i, it in enumerate(b['items'], 1):
        if current.get(it['key']) != it['chash']:
            res['outdated'] += 1            # changed since the batch was made; a newer batch has it
            continue
        st, v, errs = reviewlib.verdict_state(it)
        if st == 'missing':
            if report_missing:
                print(f"{i:2d}. {it['key']} {it['root']}: MISSING reviews/{it['app']}/tx/{it['key']}.json")
            res['missing'] += 1
            continue
        if st == 'invalid':
            print(f"{b['id']} #{i} {it['key']} {it['root']}: INVALID")
            for e in errs:
                print(f"      - {e}")
            res['invalid'] += 1
            continue
        if v.get('chash') and v['chash'] != it['chash']:
            # an old verdict, written for different content of this transaction
            print(f"{b['id']} #{i} {it['key']} {it['root']}: STALE verdict from batch {v.get('batch')}: review the "
                  f"transaction again and rewrite the file (without the chash, batch, app and stamped_at fields)")
            res['stale'] += 1
            continue
        if not v.get('chash'):
            # valid and new: stamp it with the content it judged
            v['chash'], v['batch'], v['app'], v['stamped_at'] = it['chash'], b['id'], it['app'], kitlib.now()
            kitlib.jdump(v, kitlib.verdict_path(it['app'], it['key']))
        res['counts'][v['verdict']] = res['counts'].get(v['verdict'], 0) + 1
    # transactions this batch added
    adir = os.path.join(kitlib.REVIEWS, b['app'], 'added')
    if os.path.isdir(adir):
        for fn in sorted(os.listdir(adir)):
            if not fn.endswith('.json'):
                continue
            try:
                x = kitlib.jload(os.path.join(adir, fn))
            except Exception as e:  # noqa: BLE001
                print(f"added/{fn}: not valid JSON: {e}")
                res['added_invalid'] += 1
                continue
            if x.get('found_in_batch') != b['id']:
                continue
            errs = kitlib.validate_added(x)
            if errs:
                print(f"added/{fn}: INVALID")
                for e in errs:
                    print(f"      - {e}")
                res['added_invalid'] += 1
            else:
                res['added'] += 1
    return res


def cmd_check(a):
    b = load_batch(a.batch)
    r = check_batch(b)
    summary = ', '.join(f"{n} {k}" for k, n in sorted(r['counts'].items()))
    live = len(b['items']) - r['outdated']
    print(f"batch {b['id']}: {sum(r['counts'].values())}/{live} valid ({summary or 'none'})"
          + (f"; {r['added']} added" if r['added'] else '') + (f"; {r['outdated']} outdated (in a newer batch)" if r['outdated'] else ''))
    problems = r['missing'] + r['invalid'] + r['stale'] + r['added_invalid']
    if problems:
        print(f"{problems} problem(s): fix them and run this check again")
        sys.exit(1)


def cmd_check_all(a):
    """Stamp every valid verdict; list invalid and stale verdicts, and the batches that are not finished."""
    tot, bad, unfinished = collections.Counter(), 0, []
    for bid, b in sorted(reviewlib.load_batches().items()):
        r = check_batch(b, report_missing=False)
        tot.update(r['counts'])
        tot['added'] += r['added']
        tot['missing'] += r['missing']
        bad += r['invalid'] + r['stale'] + r['added_invalid']
        if r['missing'] and sum(r['counts'].values()) + r['invalid'] + r['stale']:
            unfinished.append(f"{bid} ({r['missing']} missing)")
    print('verdicts: ' + ', '.join(f"{n} {k}" for k, n in sorted(tot.items())))
    if unfinished:
        print('started but not finished: ' + ', '.join(unfinished[:30]) + (' ...' if len(unfinished) > 30 else ''))
    if bad:
        print(f"{bad} verdict(s) invalid or stale (listed above)")
        sys.exit(1)


def cmd_find(a):
    d = kitlib.load_out(a.app)
    in_batch = {}
    for bid, b in sorted(reviewlib.load_batches().items()):
        if b['app'] == a.app:
            for it in b['items']:
                in_batch[it['key']] = bid
    hits = [t for t in d['transactions'] if a.text in (t['file'] or '') or a.text in t['root']]
    for t in hits:
        print(f"{t['id']:>12} {t['key']} {t['kind']}/{t['subkind']} {t['root']} ({t['file']}:{t['line']})"
              f"  [{'batch ' + in_batch[t['key']] if t['key'] in in_batch else 'no batch yet'}]")
    print(f"{len(hits)} transactions match {a.text!r}")


def cmd_added(a):
    adir = os.path.join(kitlib.REVIEWS, a.app, 'added')
    bad = 0
    for fn in sorted(os.listdir(adir)) if os.path.isdir(adir) else []:
        if fn.endswith('.json'):
            try:
                errs = kitlib.validate_added(kitlib.jload(os.path.join(adir, fn)))
            except Exception as e:  # noqa: BLE001
                errs = [f"not valid JSON: {e}"]
            print(f"{fn}: {'ok' if not errs else 'INVALID'}")
            for e in errs:
                print(f"   - {e}")
            bad += bool(errs)
    sys.exit(1 if bad else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('show'); p.add_argument('batch'); p.set_defaults(f=cmd_show)
    p = sub.add_parser('check'); p.add_argument('batch'); p.set_defaults(f=cmd_check)
    p = sub.add_parser('check-all'); p.set_defaults(f=cmd_check_all)
    p = sub.add_parser('find'); p.add_argument('app'); p.add_argument('text'); p.set_defaults(f=cmd_find)
    p = sub.add_parser('added'); p.add_argument('app'); p.set_defaults(f=cmd_added)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
