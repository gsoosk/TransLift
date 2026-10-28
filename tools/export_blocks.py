#!/usr/bin/env python3
"""Write one Markdown file per transaction: metadata, database operations, the transaction's own block, every
method on the call paths from the root to its database operations (and to nested transactions, explicit
transactions called from implicit ones, external effects and locks), and the SQL / repository declarations behind
each operation.

Usage:
  export_blocks.py [apps ...]                          drafts for reviewers: out/ -> output/drafts/<app>/<kind>/<key>.md
  export_blocks.py --reviewed [apps ...]               final files: output/out_reviewed/ -> output/tx-blocks/<app>/<kind>/<n>_<root>.md
  export_blocks.py --only KEY[,KEY...] --stdout APP     print selected transactions (used by show_tx.py)

Re-walks each root with the same analysis as txscan.py, so the operations match output/out/<app>.json exactly.
"""
import argparse, collections, json, os, re, shutil, sys, textwrap, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
import txscan
from txscan import simple

EXTERNAL = ('http', 'message', 'email', 'search-index', 'object-store')
MAX_METHOD_LINES = 400        # longer methods are cut around the interesting lines


def sanitize(s, n=110):
    s = re.sub(r'[^A-Za-z0-9._-]+', '_', s).strip('_')
    return s[:n]


def plural(n, word):
    return f"{n:,} {word}" + ('' if n == 1 else 's')


def md_escape(s):
    return str(s).replace('|', '\\|').replace('\n', ' ')


class Sources:
    def __init__(self, root):
        self.root, self.cache = root, {}

    def lines(self, rel):
        if rel not in self.cache:
            try:
                with open(os.path.join(self.root, rel), 'r', encoding='utf8', errors='replace') as fh:
                    self.cache[rel] = fh.read().split('\n')
            except Exception:
                self.cache[rel] = []
        return self.cache[rel]


def stg_index(app_dir):
    out = {}
    for dp, dns, fns in os.walk(app_dir):
        dns[:] = sorted(d for d in dns if d not in ('.git', 'target', 'build', 'node_modules'))
        for fn in sorted(fns):
            if fn.endswith('.stg'):
                rel = os.path.relpath(os.path.join(dp, fn), app_dir)
                if not txscan.is_test_path(rel):
                    out.setdefault(fn.replace('.sql.stg', '').replace('.stg', ''), []).append(rel)
    return out


def stg_template(app_dir, stgs, dao, name, seen=None):
    """StringTemplate text of `name` in <dao>.sql.stg or the groups it imports."""
    seen = seen or set()
    for rel in stgs.get(dao, []):
        if rel in seen:
            continue
        seen.add(rel)
        try:
            x = open(os.path.join(app_dir, rel), encoding='utf8', errors='replace').read()
        except Exception:
            continue
        m = re.search(r'^' + re.escape(name) + r'\s*\([^)]*\)\s*::=\s*(<<.*?>>|"[^"\n]*")', x, re.M | re.S)
        if m:
            return rel, x[:m.start()].count('\n') + 1, x[m.start():m.end()]
        for im in re.finditer(r'^import\s+"([^"]+)"', x, re.M):
            base = os.path.basename(im.group(1)).replace('.sql.stg', '').replace('.stg', '')
            r = stg_template(app_dir, stgs, base, name, seen)
            if r:
                return r
    return None


def lex_root(idx, mid):
    """Nearest enclosing real (non-lambda, non-anonymous) method."""
    guard = 0
    m = idx.methods.get(mid)
    while m is not None and m.pseudo and m.parent in idx.methods and guard < 20:
        mid = m.parent
        m = idx.methods.get(mid)
        guard += 1
    return mid


class AppExport:
    """Everything needed to render one app's transactions (the index is built once)."""

    def __init__(self, app, reviewed=False):
        self.app = app
        self.reviewed = reviewed
        self.app_dir = os.path.join(kitlib.CLONES, app)
        if not os.path.isdir(self.app_dir):
            raise SystemExit(f"{app}: no checkout in clones/{app} (run tools/clone_app.sh {app})")
        rules = kitlib.load_rules(app)
        self.idx, self.scan, self.tb, self.entities, self.mappers, _, _ = txscan.prepare(self.app_dir, app, rules)
        self.data = kitlib.load_out(app, reviewed=reviewed)
        self.src = Sources(self.app_dir)
        self.stgs = stg_index(self.app_dir)
        self.by_id = {t['id']: t for t in self.data['transactions']}
        self.methods_by_file = collections.defaultdict(list)
        for m in self.idx.methods.values():
            self.methods_by_file[m.file].append(m)

    # ---------------------------------------------------------------- file names
    def rel_name(self, t, names):
        if names == 'key':
            return os.path.join(t['kind'], f"{t['key']}.md")
        num = kitlib.tx_num(t)
        num = f"{int(num):05d}" if num.isdigit() else num
        return os.path.join(t['kind'], f"{num}_{sanitize(t['root'])}.md")

    def method_at(self, cls_simple, line, file=None):
        """Innermost real method of class `cls_simple` (or in `file`) whose body spans `line`."""
        cands = []
        pool = self.methods_by_file.get(file, []) if file else self.idx.methods.values()
        for m in pool:
            if m.pseudo or not m.line or not m.end:
                continue
            if cls_simple and simple(m.cls) != cls_simple:
                continue
            if m.line <= line <= m.end:
                cands.append(m)
        if not cands:
            return None
        return min(cands, key=lambda m: m.end - m.line).id

    # ---------------------------------------------------------------- render
    def render(self, t, files):
        idx, tb = self.idx, self.tb
        root = t.get('root_id')
        added = t.get('source') == 'review'
        rv = t.get('review') or {}
        if added or root not in idx.methods:
            root = self.method_at(None, t.get('line') or 0, t.get('file')) or root
        mode = 'implicit' if (rv.get('kind_txscan') or t['kind']) == 'implicit' else 'tx'
        w = tb.walk(root, mode=mode) if root in idx.methods else {
            'ops': [], 'nested': [], 'units': [], 'effects': [], 'locks': [], 'txapis': [], 'async': [], 'parents': {root: None}}
        mismatch = False
        if not added:
            n_orig = rv.get('n_ops_txscan', t['n_ops']) if self.reviewed else t['n_ops']
            mismatch = len(w['ops']) != n_orig
        parents = w['parents']
        rm = idx.methods.get(root)

        # ---- operations to list: the record's own (reviewed) list; walk ops give the code positions
        kept = {}
        if self.reviewed:
            for i, o in enumerate(t['ops']):
                if o.get('orig') is not None:
                    kept[o['orig']] = i
        marks = collections.defaultdict(list)
        targets = set()
        walk_ops = [] if added else w['ops']
        for i, op in enumerate(walk_ops):
            if self.reviewed and not mismatch and i not in kept:
                continue                                   # removed by the review
            if self.reviewed and not mismatch:
                o = t['ops'][kept[i]]
                tabs = ','.join(o['writes']) if o['writes'] else ','.join(o['reads'])
                marks[(op['method'], op['line'])].append(f"DB {o['op']} {tabs or '?'}")
            else:
                r, wr, fl = tb.op_tables(op)
                tabs = ','.join(sorted(wr)) if wr else ','.join(sorted(r))
                marks[(op['method'], op['line'])].append(f"DB {op['op']} {tabs or '?'}")
            targets.add(op['method'])
        if self.reviewed:
            for o in t['ops']:
                if o.get('orig') is None and o.get('at'):
                    cls, _, ln = o['at'].rpartition(':')
                    mid = self.method_at(cls, int(ln)) if ln.isdigit() else None
                    if mid:
                        tabs = ','.join(o.get('writes') or []) or ','.join(o.get('reads') or [])
                        marks[(mid, int(ln))].append(f"DB {o['op']} {tabs or '?'} (added by review)")
                        targets.add(mid)
                        if mid not in parents:
                            parents[mid] = None
        for n in w['nested']:
            marks[(n['method'], n['line'])].append(f"{n['propagation']} transaction {txscan.label_of(idx, n['target'])} (commits separately)")
            targets.add(n['method'])
        for kind, u in w['units']:
            if kind == 'tx':
                marks[(u['method'], u['line'])].append(f"explicit transaction {txscan.label_of(idx, u['target'])} (commits on return)")
                targets.add(u['method'])
        for e in w['effects']:
            if e['kind'] in EXTERNAL:
                marks[(e['method'], e['line'])].append(f"external effect: {e['kind']}")
                targets.add(e['method'])
        for l in w['locks']:
            if l.get('line'):
                marks[(l['method'], l['line'])].append(f"lock: {l['kind']}")
                targets.add(l['method'])
        for x in w['txapis']:
            marks[(x['method'], x['line'])].append(f"tx api: {x['what']}")
            targets.add(x['method'])
        for a in w['async']:
            marks[(a['method'], a['line'])].append(f"async: {a['kind']}")

        # ---- slice: every method on the first path from the root to an interesting site
        in_slice = {root}
        for m0 in targets:
            c, guard = m0, 0
            while c is not None and guard < 200:
                in_slice.add(c)
                p = parents.get(c)
                c = p[0] if p else None
                guard += 1
        children = collections.defaultdict(list)
        for m0 in in_slice:
            p = parents.get(m0)
            if p and p[0] in in_slice:
                children[p[0]].append((p[1], m0))
        # depth-first from the root; methods off the walk's tree (holding operations a reviewer added) come last
        orphans = sorted(m0 for m0 in in_slice if m0 != root and not parents.get(m0))
        order, seen = [], set()
        stack = list(reversed(orphans)) + [root]
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            order.append(c)
            for line, ch in sorted(children.get(c, []), reverse=True):
                stack.append(ch)

        # ---- header and metadata
        out = []
        attrs = t.get('attrs') or {}
        out.append(f"# {t['id']} · {t['kind']} / {t['subkind']}\n")
        meta = [('Root', f"`{t['root']}`"), ('Source', f"`{t['file']}:{t['line']}`"), ('Key', f"`{t['key']}`")]
        if t['kind'] == 'explicit':
            a = []
            if attrs.get('propagation'):
                a.append(f"propagation {attrs['propagation']}")
            if attrs.get('readOnly'):
                a.append('readOnly')
            if attrs.get('isolation'):
                a.append(f"isolation {attrs['isolation']}")
            for k in ('timeout', 'rollbackFor', 'noRollbackFor', 'manager'):
                if attrs.get(k):
                    a.append(f"{k} {attrs[k]}")
            if attrs.get('api'):
                a.append(f"api {attrs['api']}")
            if attrs.get('annotation') and attrs['annotation'] != 'app-rule':
                a.append(f"@{attrs['annotation']} on {attrs.get('source')}")
            if attrs.get('non_public'):
                a.append('non-public method: Spring proxies ignore this annotation')
            if attrs.get('effective') is False:
                a.append('not effective (see review)')
            meta.append(('Attributes', md_escape(' · '.join(a)) or '-'))
        elif t['kind'] == 'adhoc':
            meta.append(('Construct', md_escape(', '.join(attrs.get('sites', [])[:6]) or t['subkind'])))
            meta.append(('Inside a DB transaction', 'yes' if attrs.get('inside_db_tx') else 'no'))
        if self.reviewed and rv.get('boundary'):
            meta.append(('Boundary', md_escape(rv['boundary'])))
        if t.get('invoked_by'):
            ib = t['invoked_by']
            meta.append(('Reached from', md_escape('; '.join(ib[:5]) + (f" (+{len(ib) - 5} more)" if len(ib) > 5 else ''))))
        meta.append(('Tables read', ', '.join(f"`{x}`" for x in t['tables_read']) or '-'))
        meta.append(('Tables written', ', '.join(f"`{x}`" for x in t['tables_written']) or '-'))
        meta.append(('Shape', f"{plural(t['n_ops'], 'DB operation')} ({t['n_reads']} reads, {t['n_writes']} writes; "
                              f"{t['ops_in_loops']} in loops, {t['ops_conditional']} conditional, "
                              f"{t['ops_unknown_table']} on unknown tables) · call depth {t.get('max_call_depth', 0)} · "
                              f"{plural(t.get('methods_visited', 0), 'method')} walked"))
        extra = []
        if t.get('nested_tx_calls'):
            extra.append('nested transactions: ' + ', '.join(sorted({f"{txscan.label_of(idx, n['target'])} ({n['propagation']})" for n in t['nested_tx_calls']}))[:400])
        eff = collections.Counter(e['kind'] for e in t.get('effects', []) if e.get('kind'))
        if eff:
            extra.append('effects: ' + ', '.join(f"{k}×{v}" for k, v in sorted(eff.items())))
        nd = collections.Counter((n.get('detail') or '?') for n in t.get('nondet', []))
        if nd:
            extra.append('nondeterminism: ' + ', '.join(f"{k}×{v}" for k, v in nd.most_common(6)))
        lk = sorted({l['kind'] for l in t.get('app_locks', []) if l.get('kind')})
        if lk:
            extra.append('application locks: ' + ', '.join(lk))
        if t.get('async'):
            extra.append(f"async calls: {len(t['async'])}")
        if t.get('db_flags'):
            extra.append('DB flags: ' + ', '.join(t['db_flags']))
        if t.get('versioned_entities'):
            extra.append('@Version entities: ' + ', '.join(t['versioned_entities']))
        if t.get('explicit_tx_called'):
            links = [f"[{x}](../{files[x]})" if x in files else x for x in t['explicit_tx_called']]
            extra.append('explicit transactions called (each commits on its own): ' + ', '.join(links))
        if t.get('poly_skipped') or t.get('unresolved_calls') or t.get('truncated'):
            extra.append(f"analysis gaps: {t.get('poly_skipped', 0)} calls with >8 targets skipped, {t.get('unresolved_calls', 0)} unresolved"
                         + (', walk truncated at 6,000 methods' if t.get('truncated') else ''))
        if mismatch:
            extra.append('the code markers below may be incomplete: re-walking this root found a different number of operations')
        for x in extra:
            meta.append(('Also', md_escape(x) if not x.startswith('explicit transactions called') else x))
        if self.reviewed:
            if added:
                meta.append(('Review', f"added by review · confidence {rv.get('confidence', '?')}"))
            elif rv.get('verdict'):
                meta.append(('Review', f"{rv['verdict']} · confidence {rv.get('confidence', '?')} · {rv.get('depth', '?')} review"))
            else:
                meta.append(('Review', 'not reviewed' + (' (verdict out of date)' if rv.get('stale') else '')))
        out.append('| | |\n|---|---|')
        for k, v in meta:
            out.append(f"| {k} | {v} |")
        out.append('')

        # ---- operations table
        if t['ops']:
            out.append('## Database operations\n')
            out.append('| # | Op | API | Call | At | Entity | Reads | Writes | Notes |')
            out.append('|---|---|---|---|---|---|---|---|---|')
            for i, o in enumerate(t['ops'], 1):
                notes = [x.replace('_', ' ') for x in o.get('flags') or []] + (['loop'] if o.get('loop') else []) + \
                        (['conditional'] if o.get('cond') else [])
                if self.reviewed and o.get('review'):
                    notes.append(o['review'])
                out.append(f"| {i} | {o['op']} | {o.get('api') or ''} | `{md_escape(o.get('call') or '')}` | {o.get('at') or ''} | "
                           f"{md_escape(o.get('entity') or '')} | {', '.join(o.get('reads') or [])} | {', '.join(o.get('writes') or [])} | "
                           f"{md_escape(', '.join(notes))} |")
            out.append('')
        elif t['kind'] == 'implicit':
            out.append('This entry point performs no database operation of its own; it only calls the explicit transactions listed above.\n')
        else:
            out.append('No database operation was found on the call paths from this root (calls through reflection, external libraries or more than 8 possible targets are not followed).\n')

        # ---- code
        out.append('## Code\n')
        opener = None
        if rm is not None and rm.pseudo:
            opener = lex_root(idx, root)
            if opener != root and opener in idx.methods:
                out.append(f"The transaction is the callback at lines {rm.line}–{rm.end} of `{txscan.label_of(idx, opener)}`, shown in full first.\n")
                order = [opener] + order
        printed = set()
        num = 0
        for mid in order:
            m = idx.methods.get(mid)
            if m is None:
                continue
            if m.pseudo and lex_root(idx, mid) in printed:
                continue           # a lambda or anonymous class already shown inside its enclosing method
            printed.add(mid)
            lines = self.src.lines(m.file)
            if not lines:
                continue
            a, b = m.line, min(m.end, len(lines))
            body = lines[a - 1:b]
            mk = {}
            for (mm, ln), texts in marks.items():
                if (mm == mid or lex_root(idx, mm) == mid) and a <= ln <= b:
                    mk.setdefault(ln, []).extend(texts)
            if len(body) > MAX_METHOD_LINES:
                keep = set()
                for ln in list(mk.keys()) + [c[0] for c in children.get(mid, [])]:
                    for k in range(ln - 6, ln + 7):
                        keep.add(k)
                keep |= set(range(a, a + 15)) | set(range(b - 3, b + 1))
                shown, last = [], None
                for ln in range(a, b + 1):
                    if ln in keep:
                        if last is not None and ln != last + 1:
                            shown.append((None, f"    // ... {ln - last - 1} lines omitted ..."))
                        shown.append((ln, lines[ln - 1]))
                        last = ln
            else:
                shown = [(ln, lines[ln - 1]) for ln in range(a, b + 1)]
            ded = textwrap.dedent('\n'.join(x for _, x in shown)).split('\n')
            num += 1
            caller = parents.get(mid)
            hdr = f"### {num}. `{txscan.label_of(idx, mid)}`"
            if mid == root:
                hdr += ' (root)'
            elif mid == opener:
                hdr += ' (opens the transaction)'
            out.append(hdr)
            info = [f"`{m.file}:{a}-{b}`"]
            if caller and caller[0] in idx.methods:
                info.append(f"called from `{txscan.label_of(idx, caller[0])}` at line {caller[1]}")
            td = idx.types.get(m.cls)
            if td is not None:
                text = '\n'.join(body)
                deps = []
                for fname, f in td.fields.items():
                    if f.static and f.final:
                        continue
                    if re.search(r'\b' + re.escape(fname) + r'\b', text):
                        deps.append(f"{f.type_raw} {fname}")
                if deps:
                    info.append('fields used: ' + ', '.join(f"`{d}`" for d in deps[:12]))
            out.append(' · '.join(info) + '\n')
            out.append('```java')
            for (ln, _), dl in zip(shown, ded):
                if ln is not None and ln in mk:
                    dl = dl.rstrip() + '   // <-- ' + '; '.join(dict.fromkeys(mk[ln]))
                out.append(dl)
            out.append('```\n')

        # ---- calls the extractor did not follow (so the reviewer can follow them by hand)
        sk = w.get('skipped') or []
        if sk:
            out.append('## Calls not followed\n')
            out.append('The extractor did not walk into these calls. Follow them by hand if they can reach the database.\n')
            for x in sk[:40]:
                m0 = idx.methods.get(x['method'])
                where = f"{simple(m0.cls)}:{x['line']}" if m0 else f"?:{x['line']}"
                decl = f" (declared in `{txscan.label_of(idx, x['decl'])}`)" if x.get('decl') in idx.methods else ''
                out.append(f"- `{where}` · `{x['call']}`{decl} · {x['why']}")
            if len(sk) > 40:
                out.append(f"- ... {len(sk) - 40} more")
            out.append('')

        # ---- SQL and repository declarations (for the walk's operations that are still listed)
        qs, seen_q = [], set()
        for i, op in enumerate(walk_ops):
            if self.reviewed and not mismatch and i not in kept:
                continue
            m = idx.methods.get(op['method'])
            decl = op.get('decl')
            dm = idx.methods.get(decl) if decl else None
            sql = op.get('sql')
            key = decl or (op['method'], op['line'])
            if key in seen_q:
                continue
            seen_q.add(key)
            block = []
            literal_q = False
            if dm is not None and dm.id not in printed:
                lines = self.src.lines(dm.file)
                if lines:
                    decl_txt = textwrap.dedent('\n'.join(lines[dm.line - 1:min(dm.end, len(lines))]))
                    block.append((f"`{txscan.label_of(idx, dm.id)}` · `{dm.file}:{dm.line}`", 'java', decl_txt))
                literal_q = any(an in ('Query', 'NativeQuery', 'Select', 'Insert', 'Update', 'Delete', 'SqlQuery',
                                       'SqlUpdate', 'SqlBatch', 'SqlCall') and '"' in (aargs or '')
                                for (an, aq, aargs, al) in dm.annots)
            if literal_q:
                pass
            elif op.get('api') == 'mybatis' and isinstance(sql, str) and sql.strip():
                ns = dm.cls if dm else None
                st = None
                for cand in [ns, op.get('rtype')] + ([s2 for s2 in idx.all_supers(ns) if not s2.startswith('EXT:')] if ns else []):
                    st = self.mappers.get(cand, {}).get(op['call']) if cand else None
                    if st:
                        break
                kind = st[0] if st else ('select' if op['op'] == 'R' else 'update')
                body = re.sub(r'<!--.*?-->', '', sql, flags=re.S)            # MyBatis Generator banners etc.
                body = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', body)
                body = textwrap.dedent(body.strip('\n')).strip()
                xml = op.get('mapper_xml') or (st[2] if st else None)
                block.append((f"MyBatis statement `{simple(ns) if ns else ''}.{op['call']}`" + (f" · `{xml}`" if xml else ''),
                              'xml', f"<{kind} id=\"{op['call']}\">\n{body}\n</{kind}>"))
            elif op.get('api') == 'jdbi' and dm is not None and not sql:
                dao = simple(op.get('rtype') or dm.cls)
                tpl = stg_template(self.app_dir, self.stgs, dao, op['call']) or stg_template(self.app_dir, self.stgs, simple(dm.cls), op['call'])
                if tpl:
                    block.append((f"StringTemplate `{op['call']}` · `{tpl[0]}:{tpl[1]}`", 'sql', tpl[2]))
            elif isinstance(sql, str) and sql.strip():
                txt = sql
                if sql.startswith('NAMED:'):
                    nq = getattr(tb, 'named_queries', {}).get(sql[6:])
                    txt = f"-- named query {sql[6:]}\n" + (nq[1].strip() if nq else '(definition not found)')
                where = f"{simple(m.cls) if m else '?'}:{op['line']}"
                block.append((f"Query used at `{where}` ({op.get('sql_kind') or 'sql'}; `?` marks parts built at runtime)",
                              'sql', txt.strip()))
            if block:
                qs.append(block)
        if qs:
            out.append('## SQL and repository declarations\n')
            for block in qs:
                for title, lang, txt in block:
                    out.append(f"**{title}**\n")
                    out.append(f"```{lang}\n{txt}\n```\n")

        # ---- review
        if self.reviewed and (rv.get('verdict') or added):
            out.append('## Review\n')
            if rv.get('boundary'):
                out.append(f"**Boundary.** {rv['boundary']}\n")
            ch = rv.get('changes') or []
            if ch:
                out.append('**Corrections to the extractor output**\n')
                for c in ch:
                    out.append(f"- {c}")
                out.append('')
            if rv.get('notes'):
                out.append(f"**Notes.** {rv['notes']}\n")
        return '\n'.join(out), len(order), mismatch


def export_app(app, reviewed, dest, names, only=None, to_stdout=False):
    t0 = time.time()
    ex = AppExport(app, reviewed=reviewed)
    txs = ex.data['transactions']
    if only:
        sel = [t for t in txs if t['key'] in only or t['id'] in only or kitlib.tx_num(t) in only]
        missing = set(only) - {t['key'] for t in sel} - {t['id'] for t in sel} - {kitlib.tx_num(t) for t in sel}
        if missing:
            print(f"not found in {app}: {', '.join(sorted(missing))}", file=sys.stderr)
    else:
        sel = txs
    files = {t['id']: ex.rel_name(t, names) for t in txs}
    adir = os.path.join(dest, app)
    if not only and not to_stdout and os.path.isfile(os.path.join(adir, 'index.md')):
        shutil.rmtree(adir)          # a full export replaces its earlier export, so no stale files remain
    index_rows, mismatches = [], 0
    for t in sel:
        text, n_methods, mm = ex.render(t, files)
        mismatches += mm
        if to_stdout:
            print(text)
            print('\n' + '=' * 100 + '\n')
            continue
        rel = files[t['id']]
        os.makedirs(os.path.join(adir, os.path.dirname(rel)), exist_ok=True)
        with open(os.path.join(adir, rel), 'w', encoding='utf8') as fh:
            fh.write(text)
        index_rows.append((t, rel, n_methods))
    if to_stdout:
        return len(sel), mismatches
    # per-app index
    st = ex.data['stats']
    rs = ex.data.get('review_summary')
    lines = [f"# {app.replace('_', '/', 1)} · {len(index_rows)} transactions\n",
             f"Commit `{(st.get('commit') or '')[:12]}` · {st['java_files']:,} Java files · {st['loc']:,} lines · "
             f"{st['tx_declarative'] + st['tx_programmatic']} explicit, {st['tx_implicit']} implicit, {st['tx_adhoc']} ad hoc.\n"]
    if rs:
        lines.append(f"Review: {rs['confirmed']} confirmed, {rs['corrected']} corrected, {rs['added']} added, "
                     f"{rs['excluded']} excluded (listed in `excluded.md`), {rs['unreviewed']} not reviewed.\n")
    lines += ['| Transaction | Kind | Root | Ops (R/W) | Tables written | Review | Methods shown |', '|---|---|---|---|---|---|---|']
    for t, rel, nm in index_rows:
        rv = (t.get('review') or {}).get('verdict') or ('added' if t.get('source') == 'review' else '')
        lines.append(f"| [{kitlib.tx_num(t)}]({rel}) | {t['kind']}/{t['subkind']} | `{md_escape(t['root'])}` | "
                     f"{t['n_ops']} ({t['n_reads']}/{t['n_writes']}) | {md_escape(', '.join(t['tables_written'][:6]))}"
                     f"{' …' if len(t['tables_written']) > 6 else ''} | {rv} | {nm} |")
    os.makedirs(adir, exist_ok=True)
    with open(os.path.join(adir, 'index.md'), 'w', encoding='utf8') as fh:
        fh.write('\n'.join(lines) + '\n')
    if not reviewed and not only:
        # which content each draft shows, so make_batches.py can tell when drafts are out of date
        id2key = kitlib.id_to_key(txs)
        kitlib.jdump({t['key']: kitlib.tx_chash(t, id2key) for t in txs}, os.path.join(adir, 'chash.json'))
    if reviewed and ex.data.get('excluded'):
        ex_lines = [f"# {app.replace('_', '/', 1)} · excluded by review\n", '| Transaction | Kind | Root | Reason |', '|---|---|---|---|']
        for t in ex.data['excluded']:
            ex_lines.append(f"| {t['id']} | {t['kind']}/{t['subkind']} | `{md_escape(t['root'])}` | "
                            f"{md_escape((t.get('review') or {}).get('exclude_reason', ''))} |")
        with open(os.path.join(adir, 'excluded.md'), 'w', encoding='utf8') as fh:
            fh.write('\n'.join(ex_lines) + '\n')
    print(f"{app:45s} {len(index_rows):5d} files  op-count mismatches {mismatches}  {time.time() - t0:.0f}s", flush=True)
    return len(index_rows), mismatches


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('apps', nargs='*', help='app slugs (default: every app in corpus/repos.tsv)')
    ap.add_argument('--reviewed', action='store_true', help='read output/out_reviewed/ and write the final output/tx-blocks/')
    ap.add_argument('--dest', help='output directory (default output/drafts/ or, with --reviewed, output/tx-blocks/)')
    ap.add_argument('--names', choices=('key', 'num'), help='file names by stable key (drafts) or by number (final)')
    ap.add_argument('--only', help='comma-separated keys, ids or numbers to render (needs exactly one app)')
    ap.add_argument('--stdout', action='store_true', help='print instead of writing files')
    a = ap.parse_args()
    dest = a.dest or (kitlib.BLOCKS if a.reviewed else kitlib.DRAFTS)
    names = a.names or ('num' if a.reviewed else 'key')
    apps = kitlib.app_list(a.apps)
    only = set(x.strip() for x in a.only.split(',')) if a.only else None
    if only and len(apps) != 1:
        raise SystemExit('--only needs exactly one app')
    tot = mm = 0
    for app in apps:
        if not os.path.exists(os.path.join(kitlib.OUT_REV if a.reviewed else kitlib.OUT, app + '.json')):
            print(f"{app}: no {'reviewed ' if a.reviewed else ''}extractor output, skipped", file=sys.stderr)
            continue
        n, m = export_app(app, a.reviewed, dest, names, only, a.stdout)
        tot += n; mm += m
    if not a.stdout:
        print('TOTAL', tot, 'mismatches', mm)


if __name__ == '__main__':
    main()
