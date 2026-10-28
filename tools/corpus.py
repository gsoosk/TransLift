#!/usr/bin/env python3
"""Read and edit the app list (corpus/repos.tsv) without hand-editing tabs.

  corpus.py list                                   table of apps with their pipeline state
  corpus.py apps                                   active slugs, one per line (for scripts)
  corpus.py repo <slug>                            owner/repo of an app
  corpus.py add <owner/repo> --source rule [--name N] [--domain D] [--persistence P] [--stars S] [--notes T]
  corpus.py set <slug> field=value [field=value ...]   e.g. persistence="Spring Data JPA"
  corpus.py drop <slug> --reason TEXT              keep the row but leave the app out of every tool
  corpus.py decide <owner/repo> --decision accept|reject|duplicate|failed --reason TEXT
                                                   log a selection decision (corpus/selection.tsv and selection.md)
"""
import argparse, csv, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib

EDITABLE = set(kitlib.REPO_COLS) - {'slug', 'repo'}


def cmd_list(_):
    rows = kitlib.read_repos(include_dropped=True)
    print(f"{'#':>3} {'slug':45s} {'stars':>7} {'source':6s} {'pin':12s} {'clone':5s} {'out':5s} {'calib':5s}  name")
    for i, r in enumerate(rows, 1):
        s = r['slug']
        sha, _ = kitlib.read_pin(s)
        cl = 'yes' if os.path.isdir(os.path.join(kitlib.CLONES, s)) else '-'
        out = 'yes' if os.path.exists(os.path.join(kitlib.OUT, s + '.json')) else '-'
        cal = 'yes' if os.path.exists(os.path.join(kitlib.REVIEWS, s, 'calibration.md')) else '-'
        drop = '  [dropped]' if r['status'] == 'dropped' else ''
        print(f"{i:3d} {s:45s} {r['stars'] or '?':>7} {r['source']:6s} {(sha or '-')[:12]:12s} {cl:5s} {out:5s} {cal:5s}  {r['name']}{drop}")


def cmd_apps(_):
    for s in kitlib.app_list():
        print(s)


def cmd_repo(a):
    meta = kitlib.repo_meta()
    if a.slug not in meta:
        raise SystemExit(f"{a.slug} is not in corpus/repos.tsv")
    print(meta[a.slug]['repo'])


def cmd_add(a):
    if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', a.repo):
        raise SystemExit('give the repository as owner/repo')
    rows = kitlib.read_repos(include_dropped=True)
    slug = kitlib.check_slug(kitlib.slug_of(a.repo))
    if any(r['slug'].lower() == slug.lower() for r in rows):
        raise SystemExit(f"{slug} is already listed")
    name = a.name or a.repo.split('/')[1]
    if any(r['name'].lower() == name.lower() for r in rows):
        name = f"{name} ({a.repo.split('/')[0]})"          # names must be unique: the workbook matches apps by name
    rows.append({'slug': slug, 'repo': a.repo, 'name': name, 'domain': a.domain or '',
                 'persistence': a.persistence or '', 'stars': a.stars or '', 'source': a.source,
                 'added': kitlib.today(), 'status': '', 'notes': (a.notes or '').replace('\t', ' ')})
    kitlib.write_repos(rows)
    print(f"added {slug}")


def cmd_set(a):
    rows = kitlib.read_repos(include_dropped=True)
    hit = [r for r in rows if r['slug'] == a.slug]
    if not hit:
        raise SystemExit(f"{a.slug} is not in corpus/repos.tsv")
    for kv in a.pairs:
        if '=' not in kv:
            raise SystemExit(f"expected field=value, got {kv!r}")
        k, v = kv.split('=', 1)
        if k not in EDITABLE:
            raise SystemExit(f"field must be one of {sorted(EDITABLE)}")
        hit[0][k] = v.replace('\t', ' ').replace('\n', ' ')
    kitlib.write_repos(rows)
    print(f"updated {a.slug}")


def cmd_drop(a):
    rows = kitlib.read_repos(include_dropped=True)
    hit = [r for r in rows if r['slug'] == a.slug]
    if not hit:
        raise SystemExit(f"{a.slug} is not in corpus/repos.tsv")
    hit[0]['status'] = 'dropped'
    hit[0]['notes'] = (hit[0]['notes'] + '; ' if hit[0]['notes'] else '') + 'dropped: ' + a.reason.replace('\t', ' ')
    kitlib.write_repos(rows)
    log_decision(hit[0]['repo'], 'dropped', a.reason, '', hit[0].get('stars', ''))
    print(f"dropped {a.slug}")


SEL_COLS = ['rank', 'repo', 'stars', 'decision', 'reason', 'date']


def cmd_decide(a):
    if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', a.repo):
        raise SystemExit('give the repository as owner/repo')
    log_decision(a.repo, a.decision, a.reason, a.rank or '', a.stars or '')
    print(f"logged {a.decision}: {a.repo}")


def log_decision(repo, decision, reason, rank, stars):
    """Append one decision to corpus/selection.tsv and corpus/selection.md."""
    reason = ' '.join(reason.split())
    cand = os.path.join(kitlib.CORPUS, 'candidates.tsv')
    if os.path.exists(cand) and not (rank and stars):
        with open(cand, newline='', encoding='utf8') as fh:
            for r in csv.DictReader(fh, delimiter='\t'):
                if r['repo'].lower() == repo.lower():
                    rank, stars = rank or r['rank'], stars or r['stars']
                    break
    tsv = os.path.join(kitlib.CORPUS, 'selection.tsv')
    new = not os.path.exists(tsv)
    with open(tsv, 'a', newline='', encoding='utf8') as fh:
        w = csv.DictWriter(fh, fieldnames=SEL_COLS, delimiter='\t', lineterminator='\n')
        if new:
            w.writeheader()
        w.writerow({'rank': rank, 'repo': repo, 'stars': stars, 'decision': decision, 'reason': reason,
                    'date': kitlib.today()})
    md = os.path.join(kitlib.CORPUS, 'selection.md')
    if not os.path.exists(md) or 'Candidates examined' not in open(md, encoding='utf8').read():
        meta = kitlib.jload(os.path.join(kitlib.CORPUS, 'candidates.meta.json'), default={})
        with open(md, 'a', encoding='utf8') as fh:
            fh.write('\n## Candidates examined\n\n')
            if meta:
                fh.write(f"Search of {meta.get('date')}: {meta.get('candidates')} candidates with at least "
                         f"{meta.get('min_stars')} stars, from the queries {', '.join('`' + q + '`' for q in meta.get('queries', []))}.\n\n")
            fh.write('| Rank | Repository | Stars | Decision | Reason | Date |\n|---|---|---|---|---|---|\n')
    with open(md, 'a', encoding='utf8') as fh:
        fh.write(f"| {rank} | {repo} | {stars} | {decision} | {reason.replace('|', '/')} | {kitlib.today()} |\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list').set_defaults(f=cmd_list)
    sub.add_parser('apps').set_defaults(f=cmd_apps)
    p = sub.add_parser('repo'); p.add_argument('slug'); p.set_defaults(f=cmd_repo)
    p = sub.add_parser('add'); p.add_argument('repo')
    p.add_argument('--source', default='rule', choices=('seed', 'rule', 'manual'))
    for f in ('name', 'domain', 'persistence', 'stars', 'notes'):
        p.add_argument('--' + f)
    p.set_defaults(f=cmd_add)
    p = sub.add_parser('set'); p.add_argument('slug'); p.add_argument('pairs', nargs='+'); p.set_defaults(f=cmd_set)
    p = sub.add_parser('drop'); p.add_argument('slug'); p.add_argument('--reason', required=True); p.set_defaults(f=cmd_drop)
    p = sub.add_parser('decide'); p.add_argument('repo')
    p.add_argument('--decision', required=True, choices=('accept', 'reject', 'duplicate', 'failed', 'dropped'))
    p.add_argument('--reason', required=True); p.add_argument('--rank'); p.add_argument('--stars'); p.set_defaults(f=cmd_decide)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
