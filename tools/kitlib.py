"""Shared paths and helpers for the Java transaction corpus kit.

Every tool in tools/ works relative to the kit root (the parent of tools/), so run them from anywhere:
    python3 tools/<script>.py ...
"""
import csv, datetime, hashlib, json, os, re, signal, tempfile

# command-line tools: exit quietly when the output is piped into head and the like
if hasattr(signal, 'SIGPIPE'):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def P(*parts):
    return os.path.join(ROOT, *parts)


CLONES = P('clones')            # git checkouts, one per app (not shipped)
OUT = P('out')                  # txscan output: out/<slug>.json
OUT_PREV = P('out_prev')        # previous txscan output, for regression checks
OUT_REV = P('out_reviewed')     # txscan output with review verdicts applied
DRAFTS = P('drafts')            # one Markdown draft per transaction, read by reviewers
BLOCKS = P('tx-blocks')         # final Markdown per transaction (from out_reviewed)
REVIEWS = P('reviews')          # batches, verdicts, calibration reports, notes
BATCHES = P('reviews', 'batches')
DELIVER = P('deliver')          # CSVs and statistics
BUILD = P('build')              # workbook
RULES_DIR = P('rules')          # per-app txscan settings: rules/<slug>.json
CORPUS = P('corpus')            # app list, pins, selection log, candidates, probes
PINS = P('corpus', 'pins')
REPOS_TSV = P('corpus', 'repos.tsv')

REPO_COLS = ['slug', 'repo', 'name', 'domain', 'persistence', 'stars', 'source', 'added', 'status', 'notes']
SAFE = re.compile(r'^[A-Za-z0-9._-]+$')
TOKENS_PER_BYTE = 1 / 3.5       # rough size of Markdown with code, for budgeting


def today():
    return datetime.date.today().isoformat()


def now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def jload(path, default=None):
    try:
        with open(path, encoding='utf8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def jdump(obj, path, indent=1):
    """Write JSON atomically (temp file + rename), so a killed process never leaves half a file."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or '.', suffix='.tmp')
    with os.fdopen(fd, 'w', encoding='utf8') as fh:
        json.dump(obj, fh, indent=indent, default=str, ensure_ascii=False)
        fh.write('\n')
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- app list
def slug_of(repo):
    """owner/repo -> owner_repo (the first slash only)."""
    return repo.strip().replace('/', '_', 1)


def check_slug(slug):
    if not SAFE.match(slug or ''):
        raise SystemExit(f"bad app slug {slug!r}: use letters, digits, '.', '_' and '-' only")
    return slug


def read_repos(include_dropped=False):
    """Rows of corpus/repos.tsv in file order. Lines starting with # are comments."""
    rows = []
    if not os.path.exists(REPOS_TSV):
        return rows
    with open(REPOS_TSV, newline='', encoding='utf8') as fh:
        lines = [l for l in fh if l.strip() and not l.startswith('#')]
    for r in csv.DictReader(lines, delimiter='\t'):
        r = {k: (r.get(k) or '').strip() for k in REPO_COLS}
        if not r['slug'] and r['repo']:
            r['slug'] = slug_of(r['repo'])
        if not r['slug']:
            continue
        if r['status'] == 'dropped' and not include_dropped:
            continue
        rows.append(r)
    return rows


def write_repos(rows):
    header = ('# App list for the corpus. One row per application; tools read it in this order.\n'
              '# source: seed (the original 20) or rule (added by java-repo-scout); status: empty, or dropped.\n')
    os.makedirs(CORPUS, exist_ok=True)
    tmp = REPOS_TSV + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf8') as fh:
        fh.write(header)
        w = csv.DictWriter(fh, fieldnames=REPO_COLS, delimiter='\t', lineterminator='\n')
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in REPO_COLS})
    os.replace(tmp, REPOS_TSV)


def repo_meta():
    return {r['slug']: r for r in read_repos(include_dropped=True)}


def app_list(apps=None):
    """Slugs to work on: the given ones (validated against repos.tsv) or every active app."""
    active = [r['slug'] for r in read_repos()]
    if not apps:
        return active
    known = set(r['slug'] for r in read_repos(include_dropped=True))
    out = []
    for a in apps:
        a = slug_of(a) if '/' in a else a
        if a not in known:
            raise SystemExit(f"{a} is not in corpus/repos.tsv")
        out.append(a)
    return out


def load_rules(slug):
    f = os.path.join(RULES_DIR, slug + '.json')
    return jload(f) if os.path.exists(f) else {}


def read_pin(slug):
    f = os.path.join(PINS, slug + '.txt')
    if not os.path.exists(f):
        return None, None
    parts = open(f).read().split()
    return (parts[0], parts[1] if len(parts) > 1 else '') if parts else (None, None)


def write_pin(slug, sha, date):
    os.makedirs(PINS, exist_ok=True)
    with open(os.path.join(PINS, slug + '.txt'), 'w') as fh:
        fh.write(f"{sha} {date}\n")


def load_out(slug, reviewed=False):
    return jload(os.path.join(OUT_REV if reviewed else OUT, slug + '.json'))


# --------------------------------------------------------------------------- transactions
def id_to_key(txs):
    return {t['id']: t.get('key', t['id']) for t in txs}


def tx_chash(t, id2key=None):
    """Content hash of what a reviewer sees for one transaction. A verdict applies only while it matches."""
    id2key = id2key or {}
    payload = {
        'kind': t['kind'], 'subkind': t['subkind'], 'root_id': t.get('root_id'), 'file': t.get('file'),
        'line': t.get('line'), 'attrs': t.get('attrs') or {},
        'ops': [[o.get('op'), o.get('api'), o.get('call'), o.get('at'), o.get('entity'), o.get('reads'),
                 o.get('writes'), sorted(o.get('flags') or []), bool(o.get('loop')), bool(o.get('cond')), o.get('sql')]
                for o in t.get('ops', [])],
        'nested': sorted(f"{n.get('target')}|{n.get('propagation')}" for n in t.get('nested_tx_calls', [])),
        'called': sorted(id2key.get(x, x) for x in t.get('explicit_tx_called', [])),
        'effects': sorted(f"{e.get('kind')}|{e.get('method')}|{e.get('line')}" for e in t.get('effects', [])),
        'locks': sorted(f"{l.get('kind')}|{l.get('method')}|{l.get('line')}" for l in t.get('app_locks', [])),
        'async': sorted(f"{a.get('kind')}|{a.get('method')}|{a.get('line')}" for a in t.get('async', [])),
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode('utf8')).hexdigest()[:12]


def tx_num(t):
    """The number part of '<slug>:<n>' (or 'r<n>' for transactions a reviewer added)."""
    return t['id'].rsplit(':', 1)[1]


def draft_rel(slug, t):
    """Draft file of a transaction, relative to the kit root. Named by key so it survives renumbering."""
    return os.path.join('drafts', slug, t['kind'], f"{t['key']}.md")


def tokens_of(path):
    try:
        return int(os.path.getsize(path) * TOKENS_PER_BYTE) + 1
    except OSError:
        return 0


# --------------------------------------------------------------------------- verdicts
VERDICTS = ('confirmed', 'corrected', 'excluded')
CONFIDENCE = ('high', 'medium', 'low')
DEPTHS = ('full', 'sampled')
KINDS = ('explicit', 'implicit', 'adhoc')
OPCODES = ('R', 'W', 'L', 'U')
SHAPES = ('multi-write', 'read-then-write', 'single-write', 'read-only')
EXPLICIT_SUBKINDS = ('declarative', 'app-rule', 'programmatic-callback', 'programmatic-region')
EFFECT_KINDS = ('http', 'message', 'email', 'search-index', 'object-store', 'event', 'cache', 'file', 'other')
VERDICT_KEYS = {'key', 'verdict', 'confidence', 'depth', 'kind', 'subkind', 'attrs', 'boundary', 'ops_remove',
                'ops_fix', 'ops_add', 'effects_add', 'exclude_reason', 'notes',
                # stamped by review_batch.py check
                'chash', 'batch', 'app', 'stamped_at'}
OP_FIX_KEYS = {'n', 'op', 'reads', 'writes', 'flags_add', 'flags_remove', 'loop', 'cond', 'entity', 'note'}
OP_ADD_KEYS = {'op', 'at', 'call', 'api', 'entity', 'reads', 'writes', 'flags', 'loop', 'cond', 'sql', 'note'}
ADDED_KEYS = {'verdict', 'kind', 'subkind', 'root', 'file', 'line', 'attrs', 'boundary', 'confidence', 'ops',
              'effects', 'notes', 'found_in_batch', 'app', 'stamped_at'}
AT_RE = re.compile(r'^[\w$.<>]+:\d+$')


def _strs(x):
    return isinstance(x, list) and all(isinstance(s, str) and s.strip() for s in x)


def _check_subkind(kind, sub, errs):
    if kind == 'explicit' and sub not in EXPLICIT_SUBKINDS:
        errs.append(f"subkind for explicit must be one of {', '.join(EXPLICIT_SUBKINDS)}")
    if kind == 'implicit':
        if not isinstance(sub, str) or '/' not in sub or sub.rsplit('/', 1)[1] not in SHAPES:
            errs.append("subkind for implicit must be '<entry kind>/<shape>', shape one of " + ', '.join(SHAPES))
    if kind == 'adhoc' and not (isinstance(sub, str) and sub.strip()):
        errs.append('subkind for adhoc must name the construct, e.g. synchronized-block or app-lock:ReentrantLock')


def _check_new_op(o, where, errs):
    if not isinstance(o, dict):
        errs.append(f"{where}: must be an object")
        return
    bad = set(o) - OP_ADD_KEYS
    if bad:
        errs.append(f"{where}: unknown fields {sorted(bad)}")
    if o.get('op') not in OPCODES:
        errs.append(f"{where}: op must be one of {OPCODES}")
    if not isinstance(o.get('at'), str) or not AT_RE.match(o.get('at', '')):
        errs.append(f"{where}: at must be 'Class:line', e.g. OrderService:120")
    if not isinstance(o.get('call'), str) or not o.get('call'):
        errs.append(f"{where}: call is required (the method or statement that runs it)")
    for f in ('reads', 'writes'):
        if f in o and not (isinstance(o[f], list) and all(isinstance(s, str) for s in o[f])):
            errs.append(f"{where}: {f} must be a list of table names")
    if 'flags' in o and not _strs(o['flags']):
        errs.append(f"{where}: flags must be a list of strings")
    for f in ('loop', 'cond'):
        if f in o and not isinstance(o[f], bool):
            errs.append(f"{where}: {f} must be true or false")
    if not isinstance(o.get('note'), str) or not o.get('note', '').strip():
        errs.append(f"{where}: note is required (why this operation runs, with file:line evidence)")


def validate_verdict(v, key, n_ops, orig_kind=None):
    """Errors in a verdict for transaction `key` with `n_ops` operations (an empty list means valid)."""
    errs = []
    if not isinstance(v, dict):
        return ['the file must hold one JSON object']
    bad = set(v) - VERDICT_KEYS
    if bad:
        errs.append(f"unknown fields {sorted(bad)}")
    if v.get('key') != key:
        errs.append(f"key must be {key!r} (got {v.get('key')!r})")
    verdict = v.get('verdict')
    if verdict not in VERDICTS:
        errs.append(f"verdict must be one of {VERDICTS}")
    if v.get('confidence') not in CONFIDENCE:
        errs.append(f"confidence must be one of {CONFIDENCE}")
    if v.get('depth') not in DEPTHS:
        errs.append(f"depth must be one of {DEPTHS}")
    changes = [f for f in ('kind', 'subkind', 'attrs', 'ops_remove', 'ops_fix', 'ops_add', 'effects_add') if v.get(f)]
    if verdict == 'confirmed' and changes:
        errs.append(f"a confirmed verdict changes nothing; use 'corrected' (found {changes})")
    if verdict == 'corrected' and not changes:
        errs.append("a corrected verdict needs at least one of kind, subkind, attrs, ops_remove, ops_fix, ops_add, effects_add")
    if verdict == 'excluded':
        if not isinstance(v.get('exclude_reason'), str) or not v.get('exclude_reason', '').strip():
            errs.append('exclude_reason is required when the verdict is excluded')
    elif verdict in ('confirmed', 'corrected'):
        if not isinstance(v.get('boundary'), str) or len(v.get('boundary', '').strip()) < 10:
            errs.append('boundary is required: where the transaction begins, commits and rolls back')
    kind = v.get('kind') or orig_kind
    if v.get('kind') is not None and v['kind'] not in KINDS:
        errs.append(f"kind must be one of {KINDS}")
    if v.get('kind') and v['kind'] != orig_kind and not v.get('subkind'):
        errs.append('when you change kind, give subkind too')
    if v.get('subkind'):
        _check_subkind(kind, v['subkind'], errs)
    if 'attrs' in v and not isinstance(v['attrs'], dict):
        errs.append('attrs must be an object, e.g. {"propagation": "REQUIRES_NEW", "effective": false}')
    nums = []
    rem = v.get('ops_remove') or []
    if not isinstance(rem, list):
        errs.append('ops_remove must be a list')
        rem = []
    for i, r in enumerate(rem):
        n = r.get('n') if isinstance(r, dict) else r
        if not isinstance(n, int) or isinstance(n, bool):
            errs.append(f"ops_remove[{i}]: give the operation number (the # column of the draft), "
                        f"or {{\"n\": number, \"note\": \"why\"}}")
            continue
        if isinstance(r, dict) and (set(r) - {'n', 'note'} or not isinstance(r.get('note', ''), str)):
            errs.append(f"ops_remove[{i}]: only n and note are allowed")
        nums.append(n)
    fixes = v.get('ops_fix') or []
    if not isinstance(fixes, list):
        errs.append('ops_fix must be a list')
        fixes = []
    for i, f in enumerate(fixes):
        where = f"ops_fix[{i}]"
        if not isinstance(f, dict):
            errs.append(f"{where}: must be an object")
            continue
        bad = set(f) - OP_FIX_KEYS
        if bad:
            errs.append(f"{where}: unknown fields {sorted(bad)}")
        n = f.get('n')
        if not isinstance(n, int) or isinstance(n, bool):
            errs.append(f"{where}: n (operation number) is required")
            continue
        nums.append(n)
        if 'op' in f and f['op'] not in OPCODES:
            errs.append(f"{where}: op must be one of {OPCODES}")
        for k in ('reads', 'writes'):
            if k in f and not (isinstance(f[k], list) and all(isinstance(s, str) for s in f[k])):
                errs.append(f"{where}: {k} must be a list of table names (it replaces the old list)")
        for k in ('flags_add', 'flags_remove'):
            if k in f and not _strs(f[k]):
                errs.append(f"{where}: {k} must be a list of strings")
        for k in ('loop', 'cond'):
            if k in f and not isinstance(f[k], bool):
                errs.append(f"{where}: {k} must be true or false")
        if len(set(f) - {'n', 'note'}) == 0:
            errs.append(f"{where}: changes nothing")
        if not isinstance(f.get('note'), str) or not f.get('note', '').strip():
            errs.append(f"{where}: note is required (what was wrong, with file:line evidence)")
    for n in nums:
        if not 1 <= n <= n_ops:
            errs.append(f"operation {n} does not exist (this transaction has {n_ops})")
    if len(nums) != len(set(nums)):
        errs.append('an operation number appears twice in ops_remove/ops_fix')
    adds = v.get('ops_add') or []
    if not isinstance(adds, list):
        errs.append('ops_add must be a list')
        adds = []
    for i, o in enumerate(adds):
        _check_new_op(o, f"ops_add[{i}]", errs)
    effs = v.get('effects_add') or []
    if not isinstance(effs, list):
        errs.append('effects_add must be a list')
        effs = []
    for i, e in enumerate(effs):
        if not isinstance(e, dict) or e.get('kind') not in EFFECT_KINDS or not AT_RE.match(str(e.get('at', ''))):
            errs.append(f"effects_add[{i}]: needs kind (one of {EFFECT_KINDS}) and at 'Class:line'")
    if 'notes' in v and not isinstance(v['notes'], str):
        errs.append('notes must be a string')
    return errs


def validate_added(a):
    """Errors in a reviewer-added transaction (reviews/<slug>/added/<name>.json)."""
    errs = []
    if not isinstance(a, dict):
        return ['the file must hold one JSON object']
    bad = set(a) - ADDED_KEYS
    if bad:
        errs.append(f"unknown fields {sorted(bad)}")
    if a.get('verdict') != 'added':
        errs.append("verdict must be 'added'")
    if a.get('kind') not in KINDS:
        errs.append(f"kind must be one of {KINDS}")
    else:
        _check_subkind(a['kind'], a.get('subkind'), errs)
    for f in ('root', 'file', 'boundary'):
        if not isinstance(a.get(f), str) or not a.get(f, '').strip():
            errs.append(f"{f} is required")
    if not isinstance(a.get('line'), int) or isinstance(a.get('line'), bool):
        errs.append('line (of the root method) is required')
    if a.get('confidence') not in CONFIDENCE:
        errs.append(f"confidence must be one of {CONFIDENCE}")
    ops = a.get('ops')
    if not isinstance(ops, list):
        errs.append('ops must be a list (it may be empty)')
        ops = []
    for i, o in enumerate(ops):
        _check_new_op(o, f"ops[{i}]", errs)
    if 'attrs' in a and not isinstance(a['attrs'], dict):
        errs.append('attrs must be an object')
    effs = a.get('effects') or []
    if not isinstance(effs, list):
        errs.append('effects must be a list')
        effs = []
    for i, e in enumerate(effs):
        if not isinstance(e, dict) or e.get('kind') not in EFFECT_KINDS or not AT_RE.match(str(e.get('at', ''))):
            errs.append(f"effects[{i}]: needs kind (one of {EFFECT_KINDS}) and at 'Class:line'")
    return errs


# --------------------------------------------------------------------------- batches
def batch_path(bid):
    return os.path.join(BATCHES, bid + '.json')


def list_batches():
    if not os.path.isdir(BATCHES):
        return []
    return sorted(f[:-5] for f in os.listdir(BATCHES) if f.endswith('.json'))


def verdict_path(slug, key):
    return os.path.join(REVIEWS, slug, 'tx', key + '.json')
