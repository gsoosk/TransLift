#!/usr/bin/env python3
"""Where the pipeline stands, per app and for the review batches.

  review_status.py                  pipeline table (clone, extract, calibrate, drafts, review, applied) and batch counts
  review_status.py --pending        ids of batches that still need a reviewer, one per line [--max N] [--pilot]
  review_status.py --next N --claim the next N pending batches nobody is working on, claimed for you (reviews/claims/);
                                    --owner PID ties the claims to a running process: they lapse when it exits
  review_status.py --release ID ... drop claims (for example after a reviewer stopped early)
  review_status.py --estimate       transactions and draft tokens still to review
  review_status.py --notes          systematic issues reviewers reported (reviews/notes/*.md)
  review_status.py --cost           spend of headless review runs (reviews/logs/*.json from tools/run_reviews.sh)"""
import argparse, collections, glob, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
import reviewlib


def current_hashes(apps):
    cur = {}
    for app in apps:
        if os.path.exists(os.path.join(kitlib.OUT, app + '.json')):
            for it in reviewlib.app_items(app):
                cur[(app, it['key'])] = it['chash']
    return cur


CLAIMS = os.path.join(kitlib.REVIEWS, 'claims')


def claim_age(bid):
    try:
        return time.time() - os.path.getmtime(os.path.join(CLAIMS, bid))
    except OSError:
        return None


def claim_live(bid, ttl):
    """A claim holds while its owner process runs; a claim without an owner holds for ttl seconds."""
    age = claim_age(bid)
    if age is None:
        return False
    try:
        owner = open(os.path.join(CLAIMS, bid)).read().split()[0]
    except (OSError, IndexError):
        owner = '-'
    if owner.isdigit():
        if age > 24 * 3600:          # process ids get reused: no claim lasts longer than a day
            return False
        try:
            os.kill(int(owner), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
    return age < ttl


def try_claim(bid, ttl, owner=None):
    """Create reviews/claims/<bid> atomically; a lapsed claim is taken over."""
    os.makedirs(CLAIMS, exist_ok=True)
    p = os.path.join(CLAIMS, bid)
    if claim_age(bid) is not None and not claim_live(bid, ttl):
        try:
            os.remove(p)
        except OSError:
            pass
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, f"{owner or '-'} {kitlib.now()}\n".encode())
    os.close(fd)
    return True


def batch_table(apps, pilot_only=False):
    cur = current_hashes(apps)
    rows = []
    for bid, b in sorted(reviewlib.load_batches().items()):
        if b['app'] not in apps or (pilot_only and not b.get('pilot')):
            continue
        c = reviewlib.batch_progress(b, cur)
        rows.append((bid, b, c, reviewlib.batch_status(c, len(b['items']))))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('apps', nargs='*')
    ap.add_argument('--pending', action='store_true')
    ap.add_argument('--pilot', action='store_true', help='with --pending: pilot batches only')
    ap.add_argument('--max', type=int, default=0)
    ap.add_argument('--next', type=int, default=0, metavar='N')
    ap.add_argument('--claim', action='store_true')
    ap.add_argument('--owner', help='process id that holds the claims (they lapse when it exits)')
    ap.add_argument('--partial', action='store_true', help='ids of batches started but not finished')
    ap.add_argument('--ttl-hours', type=float, default=3.0, help='claims older than this are taken over (default 3)')
    ap.add_argument('--release', nargs='+', metavar='ID')
    ap.add_argument('--estimate', action='store_true')
    ap.add_argument('--notes', action='store_true')
    ap.add_argument('--cost', action='store_true')
    a = ap.parse_args()
    apps = kitlib.app_list(a.apps)

    if a.release:
        for bid in a.release:
            if kitlib.SAFE.match(bid) and os.path.exists(os.path.join(CLAIMS, bid)):
                os.remove(os.path.join(CLAIMS, bid))
        return

    if a.partial:
        for bid, b, c, st in batch_table(apps, a.pilot):
            if st == 'partial':
                print(f"{bid}  ok {c['ok']} · missing {c['missing']} · invalid {c['invalid']} · not stamped {c['unstamped']} · stale {c['stale']}"
                      + ('  [claimed]' if claim_live(bid, a.ttl_hours * 3600) else ''))
        return

    if a.pending or a.next:
        rows = [r for r in batch_table(apps, a.pilot) if r[3] in ('pending', 'partial')]
        rows.sort(key=lambda r: (not r[1].get('pilot'), r[0]))          # pilot batches first
        if a.pending:
            for bid, *_ in rows[:a.max or None]:
                print(bid)
            return
        ttl, got = a.ttl_hours * 3600, []
        for bid, *_ in rows:
            if len(got) >= a.next:
                break
            if a.claim:
                if not try_claim(bid, ttl, a.owner if a.owner and a.owner.isdigit() else None):
                    continue
            elif claim_live(bid, ttl):
                continue
            got.append(bid)
        if got:
            print('\n'.join(got))
        return

    if a.notes:
        for f in sorted(glob.glob(os.path.join(kitlib.REVIEWS, 'notes', '*.md'))):
            print(f"## {os.path.basename(f)[:-3]}")
            print(open(f, encoding='utf8').read().strip() + '\n')
        return

    if a.cost:
        tot, n, turns = 0.0, 0, 0
        for f in glob.glob(os.path.join(kitlib.REVIEWS, 'logs', '*.json')):
            try:
                r = json.load(open(f))
            except Exception:
                continue
            tot += float(r.get('total_cost_usd') or 0)
            turns += int(r.get('num_turns') or 0)
            n += 1
        print(f"{n} headless review runs · estimated spend ${tot:,.2f} (client-side estimate) · {turns:,} turns")
        return

    if a.estimate:
        need, tok, per_kind = 0, 0, collections.Counter()
        for app in apps:
            if not os.path.exists(os.path.join(kitlib.OUT, app + '.json')):
                continue
            for it in reviewlib.app_items(app):
                st, _, _ = reviewlib.verdict_state(it)
                if st != 'ok':
                    need += 1
                    tok += it['tokens']
                    per_kind[it['kind'] if it['kind'] != 'implicit' else 'implicit/' + it['subkind'].rsplit('/', 1)[1]] += 1
        print(f"{need:,} transactions still need a verdict · drafts total ~{tok / 1e6:.1f}M tokens")
        for k, v in sorted(per_kind.items()):
            print(f"  {k:28s} {v:6,}")
        print('A reviewer reads each draft once, plus the source it opens and its own notes; budget roughly 2x the draft tokens\n'
              'in input (most of it cached re-reads) and 1-2k output tokens per transaction.')
        return

    # pipeline table
    frozen = kitlib.jload(os.path.join(kitlib.OUT, 'FROZEN.json'), default={})
    print(f"{'app':45s} {'clone':5s} {'out':5s} {'calib':12s} {'txs':>6s} {'drafts':>6s} {'verdicts':>8s} {'applied':7s}")
    totals = collections.Counter()
    for app in apps:
        cl = 'yes' if os.path.isdir(os.path.join(kitlib.CLONES, app)) else '-'
        has_out = os.path.exists(os.path.join(kitlib.OUT, app + '.json'))
        cal = '-'
        cf = os.path.join(kitlib.REVIEWS, app, 'calibration.md')
        if os.path.exists(cf):
            head = open(cf, encoding='utf8').read(2000)
            cal = next((l.split(':', 1)[1].strip() for l in head.splitlines() if l.lower().startswith('status:')), 'written')
        n_tx = n_draft = n_ok = 0
        if has_out:
            items = reviewlib.app_items(app)
            n_tx = len(items)
            n_draft = sum(1 for it in items if os.path.exists(os.path.join(kitlib.ROOT, it['draft'])))
            n_ok = sum(1 for it in items if reviewlib.verdict_state(it)[0] == 'ok')
        applied = 'yes' if os.path.exists(os.path.join(kitlib.OUT_REV, app + '.json')) else '-'
        totals.update({'tx': n_tx, 'draft': n_draft, 'ok': n_ok})
        print(f"{app:45s} {cl:5s} {'yes' if has_out else '-':5s} {cal[:12]:12s} {n_tx:6d} {n_draft:6d} {n_ok:8d} {applied:7s}")
    print(f"{'TOTAL':45s} {'':5s} {'':5s} {'':12s} {totals['tx']:6d} {totals['draft']:6d} {totals['ok']:8d}")
    print(f"freeze: {frozen.get('frozen_at', 'not frozen')}")
    rows = batch_table(apps)
    if rows:
        c = collections.Counter(r[3] for r in rows)
        cur = current_hashes(apps)
        covered = {(it['app'], it['key'], it['chash']) for _, b, _, _ in rows for it in b['items']}
        uncovered = sum(1 for (app, key), ch in cur.items() if (app, key, ch) not in covered)
        print(f"batches: {len(rows)} · done {c['done']} · partial {c['partial']} · pending {c['pending']}"
              + (f" · outdated {c['outdated']} (superseded: their transactions changed)" if c['outdated'] else ''))
        if uncovered:
            print(f"  {uncovered} transactions are in no batch yet (new or changed): python3 tools/make_batches.py")
        probs = sum(r[2]['invalid'] + r[2]['unstamped'] for r in rows)
        if probs:
            print(f"  {probs} verdicts invalid or not stamped yet: run python3 tools/review_batch.py check-all")
    else:
        print('batches: none yet (python3 tools/make_batches.py)')
    notes = glob.glob(os.path.join(kitlib.REVIEWS, 'notes', '*.md'))
    if notes:
        print(f"reviewer notes on systematic issues: {len(notes)} (python3 tools/review_status.py --notes)")


if __name__ == '__main__':
    main()
