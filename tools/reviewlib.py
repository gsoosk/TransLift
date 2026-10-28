"""Review bookkeeping shared by make_batches.py, review_batch.py, review_status.py and apply_reviews.py.

A review item is one transaction of out/<app>.json. Its verdict lives in reviews/<app>/tx/<key>.json and
counts only while its stamped content hash (chash) equals the transaction's current hash."""
import json, os
import kitlib


def app_items(slug):
    """Review items of one app, in out/<app>.json order."""
    d = kitlib.load_out(slug)
    txs = d['transactions']
    id2key = kitlib.id_to_key(txs)
    items = []
    for t in txs:
        if 'key' not in t:
            raise SystemExit(f"{slug}: out/{slug}.json has no transaction keys; re-run tools/run_one.sh {slug}")
        draft = kitlib.draft_rel(slug, t)
        items.append({'key': t['key'], 'id': t['id'], 'app': slug, 'kind': t['kind'], 'subkind': t['subkind'],
                      'root': t['root'], 'file': t['file'], 'line': t['line'], 'n_ops': t['n_ops'],
                      'chash': kitlib.tx_chash(t, id2key), 'draft': draft,
                      'tokens': kitlib.tokens_of(os.path.join(kitlib.ROOT, draft))})
    return items


def read_verdict(slug, key):
    path = kitlib.verdict_path(slug, key)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding='utf8') as fh:
            return json.load(fh), None
    except Exception as e:  # noqa: BLE001 - report any parse problem to the reviewer
        return None, f"not valid JSON: {e}"


def verdict_state(item, chash=None):
    """('missing' | 'invalid' | 'unstamped' | 'stale' | 'ok', verdict-or-None, errors)."""
    v, perr = read_verdict(item['app'], item['key'])
    if v is None and perr is None:
        return 'missing', None, []
    if perr:
        return 'invalid', None, [perr]
    errs = kitlib.validate_verdict(v, item['key'], item['n_ops'], item['kind'])
    if errs:
        return 'invalid', v, errs
    if not v.get('chash'):
        return 'unstamped', v, []
    if v['chash'] != (chash or item['chash']):
        return 'stale', v, []
    return 'ok', v, []


def load_batches():
    out = {}
    for bid in kitlib.list_batches():
        try:
            out[bid] = kitlib.jload(kitlib.batch_path(bid))
        except Exception:
            continue
    return out


def batch_progress(batch, current=None):
    """Counts of item states for one batch. `current` maps (app, key) -> current chash (stale detection)."""
    counts = {'ok': 0, 'missing': 0, 'invalid': 0, 'unstamped': 0, 'stale': 0, 'outdated_item': 0}
    for it in batch['items']:
        cur = (current or {}).get((it['app'], it['key']))
        if current is not None and cur is None:
            counts['outdated_item'] += 1          # the transaction no longer exists
            continue
        if current is not None and cur != it['chash']:
            counts['outdated_item'] += 1          # the transaction changed after the batch was made
            continue
        st, _, _ = verdict_state(it)
        counts[st] += 1
    return counts


def batch_status(counts, n):
    """done: nothing left to do here; outdated items are not this batch's work any more (make_batches.py
    puts the changed transactions in new batches)."""
    live = n - counts['outdated_item']
    if live == 0:
        return 'outdated'
    if counts['ok'] == live:
        return 'done'
    if counts['ok'] + counts['invalid'] + counts['unstamped'] + counts['stale'] == 0:
        return 'pending'
    return 'partial'
