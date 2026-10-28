#!/usr/bin/env python3
"""Candidate repositories for the corpus, from GitHub, in star order.

  search_candidates.py search [--min-stars 1000] [--no-topics]
      Every Java repository with at least --min-stars stars (the star range is split until each query returns
      at most 1,000 results, GitHub's search limit), plus repositories tagged with Java persistence topics
      whatever GitHub thinks their main language is. Writes corpus/candidates.tsv and corpus/candidates.meta.json.
  search_candidates.py show [--from 0] [--n 150] [--hide-flagged] [--grep REGEX]
      Candidates in star order with hint flags; repos already in corpus/repos.tsv or decided in
      corpus/selection.tsv are marked.
  search_candidates.py stars          refresh the stars column of corpus/repos.tsv
  search_candidates.py meta owner/repo [...]   metadata of specific repositories

GitHub access: the gh CLI when it is installed and logged in (gh auth login), otherwise the REST API with
$GITHUB_TOKEN (or anonymously, which is slow). --mock FILE reads a JSON list of repositories instead (tests)."""
import argparse, csv, json, math, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib

CAND_TSV = os.path.join(kitlib.CORPUS, 'candidates.tsv')
CAND_META = os.path.join(kitlib.CORPUS, 'candidates.meta.json')
SEL_TSV = os.path.join(kitlib.CORPUS, 'selection.tsv')
COLS = ['rank', 'repo', 'stars', 'language', 'archived', 'pushed', 'license', 'size_kb', 'topics', 'description',
        'flags', 'query']
TOPICS = ['spring-boot', 'mybatis', 'mybatis-plus', 'jpa', 'hibernate', 'spring-data-jpa', 'jdbc']
# hints only: the scout decides
FLAG_RES = [
    ('tutorial?', re.compile(r'\b(tutorials?|learn(ing)?|guides?|interviews?|leetcode|algorithms?|courses?|book|notes|study|'
                             r'awesome|cheat-?sheet|roadmap|examples|samples|demos?|exercises?|practice|playground|'
                             r'boilerplate|template|scaffold)\b|学习|教程|面试|笔记|示例|入门|脚手架', re.I)),
    ('library?', re.compile(r'\b(library|libraries|sdk|starter|plugin|client|driver|toolkit|utils?|parser|binding|'
                            r'wrapper|annotation processor|orm|serializ\w+|logging|mock\w*|code generator)\b', re.I)),
    ('framework?', re.compile(r'\b(framework|engine|middleware|rpc|gateway|registry|scheduler|workflow engine)\b', re.I)),
    ('android', re.compile(r'\bandroid\b', re.I)),
    ('tool?', re.compile(r'\b(ide|intellij|decompiler|minecraft|game|emulator|crawler|spider|downloader|cli|desktop|'
                         r'javafx|swing|jvm|compiler|bytecode|proxy|database engine|message queue|search engine)\b', re.I)),
    ('app+', re.compile(r'\b(system|platform|management|mall|shop|e-?commerce|erp|crm|cms|blog|forum|admin|billing|'
                        r'banking|payment|hospital|health\w*|clinic|school|education|oa|wms|mes|hrm?|inventory|'
                        r'ticket\w*|booking|hotel|saas|portal|wiki|lms|accounting|financ\w+|iot|store)\b|商城|系统|平台|管理',
                        re.I)),
]


class RateLimited(Exception):
    pass


class GitHub:
    def __init__(self, mock=None):
        self.mock = json.load(open(mock)) if mock else None
        self.gh = shutil.which('gh') if not mock else None
        self.token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        self.last_search = 0.0

    def get(self, path, params, search=False):
        if self.mock is not None:
            return self.mock_get(path, params)
        if search:   # 30 search requests per minute when authenticated
            wait = 2.2 - (time.time() - self.last_search)
            if wait > 0:
                time.sleep(wait)
            self.last_search = time.time()
        for attempt in range(8):
            try:
                return self._get(path, params)
            except RateLimited:
                print(f"  rate limited; waiting 60s (attempt {attempt + 1})", file=sys.stderr)
                time.sleep(60)
        raise SystemExit('GitHub keeps rate-limiting; try again later')

    def _get(self, path, params):
        if self.gh:
            args = ['gh', 'api', '-X', 'GET', path]
            for k, v in params.items():
                args += ['-f', f'{k}={v}']
            r = subprocess.run(args, capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                return json.loads(r.stdout)
            err = (r.stderr + r.stdout).lower()
            if 'rate limit' in err or 'secondary rate' in err:
                raise RateLimited()
            raise SystemExit(f"gh api {path} failed: {(r.stderr or r.stdout).strip()[:300]}")
        url = f"https://api.github.com/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'tx-corpus-kit'})
        if self.token:
            req.add_header('Authorization', f'Bearer {self.token}')
        try:
            with urllib.request.urlopen(req, timeout=60) as fh:
                return json.load(fh)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                raise RateLimited()
            raise SystemExit(f"GitHub API {path}: HTTP {e.code} {e.read()[:300]!r}")

    def mock_get(self, path, params):
        repos = self.mock
        if path.startswith('repos/'):
            name = path[len('repos/'):]
            for r in repos:
                if r['full_name'].lower() == name.lower():
                    return r
            raise SystemExit(f"mock: no repo {name}")
        q = params['q']
        lo, hi = 0, 10 ** 9
        m = re.search(r'stars:(\d+)\.\.(\d+)', q) or re.search(r'stars:>=(\d+)', q)
        if m:
            lo = int(m.group(1)); hi = int(m.group(2)) if m.lastindex == 2 else hi
        lang = re.search(r'language:(\S+)', q)
        topic = re.search(r'topic:(\S+)', q)
        hits = [r for r in repos if lo <= r['stargazers_count'] <= hi
                and (not lang or (r.get('language') or '').lower() == lang.group(1).lower())
                and (not topic or topic.group(1) in (r.get('topics') or []))
                and not r.get('fork')]
        hits.sort(key=lambda r: -r['stargazers_count'])
        page, per = int(params.get('page', 1)), int(params.get('per_page', 100))
        if page * per > 1000:
            return {'total_count': len(hits), 'items': []}
        return {'total_count': len(hits), 'incomplete_results': False, 'items': hits[(page - 1) * per: page * per]}


def flags_of(r):
    text = ' '.join([r.get('full_name', '').replace('/', ' ').replace('-', ' '), r.get('description') or '',
                     ' '.join(r.get('topics') or [])])
    fl = [name for name, rx in FLAG_RES if rx.search(text)]
    if r.get('archived'):
        fl.append('archived')
    return fl


def search_query(gh, base, lo, hi, out, warn):
    """All results of `base` with stars in [lo, hi] (hi None = no upper bound), splitting ranges over 1,000 results."""
    q = f"{base} stars:{lo}..{hi}" if hi is not None else f"{base} stars:>={lo}"
    first = gh.get('search/repositories', {'q': q, 'sort': 'stars', 'order': 'desc', 'per_page': 100, 'page': 1}, search=True)
    total = first.get('total_count', 0)
    if total > 1000:
        top = hi if hi is not None else (first['items'][0]['stargazers_count'] if first.get('items') else lo)
        if top > lo:
            mid = max(lo, int(math.sqrt(lo * top)))
            mid = min(mid, top - 1)
            print(f"  {q}: {total} results, splitting at {mid}", file=sys.stderr)
            search_query(gh, base, mid + 1, top, out, warn)
            search_query(gh, base, lo, mid, out, warn)
            return
        warn.append(f"{q}: {total} results; GitHub returns only the first 1,000")
    pages = min(10, math.ceil(total / 100))
    for page in range(1, pages + 1):
        res = first if page == 1 else gh.get('search/repositories', {'q': q, 'sort': 'stars', 'order': 'desc',
                                                                       'per_page': 100, 'page': page}, search=True)
        for r in res.get('items', []):
            out.setdefault(r['full_name'], (r, base))
    print(f"  {q}: {total} results", file=sys.stderr)


def cmd_search(a):
    gh = GitHub(a.mock)
    found, warn = {}, []
    queries = ['language:Java fork:false'] + ([] if a.no_topics else [f"topic:{t} fork:false" for t in TOPICS])
    for base in queries:
        print(f"query: {base} (stars >= {a.min_stars})", file=sys.stderr)
        search_query(gh, base, a.min_stars, None, found, warn)
    rows = []
    for full, (r, base) in found.items():
        rows.append({'repo': full, 'stars': r['stargazers_count'], 'language': r.get('language') or '',
                     'archived': 'yes' if r.get('archived') else '', 'pushed': (r.get('pushed_at') or '')[:10],
                     'license': ((r.get('license') or {}).get('spdx_id') or ''), 'size_kb': r.get('size', ''),
                     'topics': ','.join(r.get('topics') or []),
                     'description': re.sub(r'\s+', ' ', r.get('description') or '').strip()[:300],
                     'flags': ','.join(flags_of(r)), 'query': base})
    rows.sort(key=lambda x: (-int(x['stars']), x['repo'].lower()))
    for i, x in enumerate(rows, 1):
        x['rank'] = i
    os.makedirs(kitlib.CORPUS, exist_ok=True)
    with open(CAND_TSV, 'w', newline='', encoding='utf8') as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, delimiter='\t', lineterminator='\n')
        w.writeheader(); w.writerows(rows)
    kitlib.jdump({'date': kitlib.today(), 'min_stars': a.min_stars, 'queries': queries, 'candidates': len(rows),
                  'warnings': warn, 'source': 'mock' if a.mock else ('gh' if gh.gh else 'rest')}, CAND_META)
    print(f"{len(rows)} candidates written to corpus/candidates.tsv")
    for x in warn:
        print('warning:', x)


def read_tsv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline='', encoding='utf8') as fh:
        return list(csv.DictReader((l for l in fh if not l.startswith('#')), delimiter='\t'))


def cmd_show(a):
    rows = read_tsv(CAND_TSV)
    if not rows:
        raise SystemExit('no candidates yet: python3 tools/search_candidates.py search')
    listed = {r['repo'].lower() for r in kitlib.read_repos(include_dropped=True)}
    decided = {r['repo'].lower(): r.get('decision', '') for r in read_tsv(SEL_TSV)}
    rx = re.compile(a.grep, re.I) if a.grep else None
    shown = 0
    for r in rows[a.start:]:
        if a.hide_flagged and any(f.endswith('?') or f == 'android' for f in r['flags'].split(',') if f) and 'app+' not in r['flags']:
            continue
        if rx and not rx.search(r['repo'] + ' ' + r['description'] + ' ' + r['topics']):
            continue
        mark = 'LISTED' if r['repo'].lower() in listed else (decided.get(r['repo'].lower(), '') or '')
        print(f"{r['rank']:>5} {int(r['stars']):>7} {r['repo']:50s} {r['language'][:10]:10s} [{r['flags']}] {mark:8s} "
              f"{r['description'][:110]}")
        shown += 1
        if shown >= a.n:
            break


def cmd_stars(a):
    gh = GitHub(a.mock)
    rows = kitlib.read_repos(include_dropped=True)
    report = []
    for r in rows:
        try:
            info = gh.get(f"repos/{r['repo']}", {})
        except SystemExit as e:          # renamed, deleted or private: keep the old value
            report.append(f"{r['repo']:50s} not updated ({str(e)[:80]})")
            continue
        r['stars'] = str(info.get('stargazers_count', r['stars']))
        report.append(f"{r['repo']:50s} {r['stars']:>7}")
    kitlib.write_repos(rows)
    print('\n'.join(report))


def cmd_meta(a):
    gh = GitHub(a.mock)
    for repo in a.repos:
        r = gh.get(f"repos/{repo}", {})
        print(json.dumps({k: r.get(k) for k in ('full_name', 'stargazers_count', 'language', 'archived', 'fork',
                                                'pushed_at', 'description', 'topics', 'default_branch', 'size')},
                         ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mock', help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('search'); p.add_argument('--min-stars', type=int, default=1000)
    p.add_argument('--no-topics', action='store_true'); p.set_defaults(f=cmd_search)
    p = sub.add_parser('show'); p.add_argument('--from', dest='start', type=int, default=0)
    p.add_argument('--n', type=int, default=150); p.add_argument('--hide-flagged', action='store_true')
    p.add_argument('--grep'); p.set_defaults(f=cmd_show)
    p = sub.add_parser('stars'); p.set_defaults(f=cmd_stars)
    p = sub.add_parser('meta'); p.add_argument('repos', nargs='+'); p.set_defaults(f=cmd_meta)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
