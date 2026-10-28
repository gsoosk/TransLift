#!/usr/bin/env python3
"""Split the transactions that still need a verdict into review batches (reviews/batches/<id>.json).

Usage: make_batches.py [apps ...] [--max-tokens 60000] [--max-tx 25] [--kinds explicit,implicit,adhoc]
                       [--skip-readonly] [--pilot N] [--seed 1] [--rebuild] [--dry-run]

  A transaction needs a verdict when reviews/<app>/tx/<key>.json is missing or was stamped for different
  content. Batches keep one app each, ordered by root file and line, so one reviewer reads related code
  together. A transaction whose draft is larger than --max-tokens gets a batch of its own.
  --pilot N    a stratified sample: up to N transactions per app, spread over kinds and sizes (ids pilot-...)
  --rebuild    first delete batches in which no verdict has been written yet
Needs the freeze (tools/freeze.py) and the drafts (tools/export_blocks.py)."""
import argparse, collections, os, random, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
import reviewlib


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('apps', nargs='*')
    ap.add_argument('--max-tokens', type=int, default=60000, help='draft tokens per batch (default 60000)')
    ap.add_argument('--max-tx', type=int, default=25, help='transactions per batch (default 25)')
    ap.add_argument('--kinds', default='explicit,implicit,adhoc')
    ap.add_argument('--skip-readonly', action='store_true', help='leave out implicit read-only transactions')
    ap.add_argument('--pilot', type=int, default=0, metavar='N')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--rebuild', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-freeze-check', action='store_true')
    a = ap.parse_args()
    if not a.no_freeze_check:
        r = subprocess.run([sys.executable, os.path.join(kitlib.ROOT, 'tools', 'freeze.py'), 'check'],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(r.stdout.strip() + '\nFreeze first (python3 tools/freeze.py freeze), or pass --no-freeze-check.')
    kinds = set(a.kinds.split(','))
    apps = kitlib.app_list(a.apps)
    batches = reviewlib.load_batches()
    if a.rebuild and not a.dry_run:
        for bid, b in list(batches.items()):
            started = any(reviewlib.read_verdict(it['app'], it['key']) != (None, None) for it in b['items'])
            if not started:
                os.remove(kitlib.batch_path(bid))
                del batches[bid]
    covered = {(it['app'], it['key'], it['chash']) for b in batches.values() for it in b['items']}
    rnd = random.Random(a.seed)
    created, n_items, n_tokens, skipped = [], 0, 0, []
    for app in apps:
        if not os.path.exists(os.path.join(kitlib.OUT, app + '.json')):
            skipped.append(app)
            continue
        items = reviewlib.app_items(app)
        drafted = kitlib.jload(os.path.join(kitlib.DRAFTS, app, 'chash.json'), default={})
        old = [it for it in items if drafted.get(it['key']) != it['chash']]
        if old:
            raise SystemExit(f"{app}: {len(old)} drafts are missing or older than output/out/{app}.json: "
                             f"run python3 tools/export_blocks.py {app}")
        todo = []
        for it in items:
            if it['kind'] not in kinds:
                continue
            if a.skip_readonly and it['kind'] == 'implicit' and it['subkind'].endswith('/read-only'):
                continue
            if (app, it['key'], it['chash']) in covered:
                continue
            st, _, _ = reviewlib.verdict_state(it)
            if st == 'ok':
                continue
            if not os.path.exists(os.path.join(kitlib.ROOT, it['draft'])):
                raise SystemExit(f"missing draft {it['draft']}: run python3 tools/export_blocks.py {app}")
            todo.append(it)
        if a.pilot:
            # round-robin over (kind, size bucket) so the sample covers small and large transactions of each kind
            groups = collections.defaultdict(list)
            for it in todo:
                size = 'small' if it['n_ops'] <= 3 else ('medium' if it['n_ops'] <= 15 else 'large')
                groups[(it['kind'], size)].append(it)
            for g in groups.values():
                rnd.shuffle(g)
            pick, keys = [], sorted(groups)
            while len(pick) < a.pilot and any(groups.values()):
                for k in keys:
                    if groups[k] and len(pick) < a.pilot:
                        pick.append(groups[k].pop())
            todo = pick
        todo.sort(key=lambda it: (it['file'] or '', it['line'] or 0, it['key']))
        prefix = f"pilot-{app}" if a.pilot else app
        if a.pilot and any(b.startswith(prefix + '-') for b in batches):
            print(f"{app}: pilot batches exist already; skipped (delete them with --rebuild if none is started)")
            continue
        used = [int(b.rsplit('-', 1)[1]) for b in batches if b.startswith(prefix + '-') and b.rsplit('-', 1)[1].isdigit()]
        nxt = max(used, default=0) + 1
        cur, cur_tok = [], 0
        def flush():
            nonlocal cur, cur_tok, nxt
            if not cur:
                return
            bid = f"{prefix}-{nxt:04d}"
            nxt += 1
            b = {'id': bid, 'app': app, 'created': kitlib.now(), 'pilot': bool(a.pilot), 'tokens': cur_tok,
                 'large': len(cur) == 1 and cur_tok > a.max_tokens, 'items': cur}
            if not a.dry_run:
                kitlib.jdump(b, kitlib.batch_path(bid))
            batches[bid] = b
            created.append(b)
            cur, cur_tok = [], 0
        for it in todo:
            if cur and (cur_tok + it['tokens'] > a.max_tokens or len(cur) >= a.max_tx):
                flush()
            cur.append(it)
            cur_tok += it['tokens']
            n_items += 1
            n_tokens += it['tokens']
        flush()
    if skipped:
        print(f"no extractor output yet, skipped: {', '.join(skipped)}", file=sys.stderr)
    large = sum(1 for b in created if b['large'])
    print(f"{'would create' if a.dry_run else 'created'} {len(created)} batches with {n_items:,} transactions "
          f"(~{n_tokens / 1e6:.1f}M draft tokens; {large} single-transaction batches over {a.max_tokens:,} tokens)")
    per_app = collections.Counter(b['app'] for b in created)
    for app, n in per_app.items():
        print(f"  {app:45s} {n:4d} batches")


if __name__ == '__main__':
    main()
