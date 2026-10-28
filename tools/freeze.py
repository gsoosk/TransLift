#!/usr/bin/env python3
"""Freeze the extractor output before reviews start, so every reviewer sees the same transactions.

  freeze.py freeze [apps ...]   record hashes of txscan.py, rules, pins and out/<app>.json in out/FROZEN.json
  freeze.py check               exit 1 (and say what changed) if any of them changed since the freeze
  freeze.py thaw                remove the freeze (do this before changing txscan.py or rules)

Verdicts stay tied to the content they judged (see kitlib.tx_chash), so thawing and re-running only
invalidates the verdicts of transactions whose content actually changed."""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib

FROZEN = os.path.join(kitlib.OUT, 'FROZEN.json')


def snapshot(apps):
    snap = {'txscan': kitlib.sha256_file(os.path.join(kitlib.ROOT, 'tools', 'txscan.py')), 'apps': {}}
    for a in apps:
        rf = os.path.join(kitlib.RULES_DIR, a + '.json')
        of = os.path.join(kitlib.OUT, a + '.json')
        snap['apps'][a] = {'rules': kitlib.sha256_file(rf) if os.path.exists(rf) else None,
                           'pin': kitlib.read_pin(a)[0],
                           'out': kitlib.sha256_file(of) if os.path.exists(of) else None}
    return snap


def diff(old, new):
    msgs = []
    if old['txscan'] != new['txscan']:
        msgs.append('tools/txscan.py changed')
    for a in sorted(set(old['apps']) | set(new['apps'])):
        o, n = old['apps'].get(a), new['apps'].get(a)
        if o is None:
            msgs.append(f"{a}: not in the freeze")
            continue
        if n is None:
            msgs.append(f"{a}: frozen but no longer in corpus/repos.tsv")
            continue
        for k in ('rules', 'pin', 'out'):
            if o[k] != n[k]:
                msgs.append(f"{a}: {k} changed")
    return msgs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=('freeze', 'check', 'thaw'))
    ap.add_argument('apps', nargs='*')
    a = ap.parse_args()
    if a.cmd == 'freeze':
        apps = kitlib.app_list(a.apps)
        missing = [x for x in apps if not os.path.exists(os.path.join(kitlib.OUT, x + '.json'))]
        if missing:
            raise SystemExit('no extractor output for: ' + ', '.join(missing))
        snap = snapshot(apps)
        snap['frozen_at'] = kitlib.now()
        kitlib.jdump(snap, FROZEN)
        print(f"frozen {len(apps)} apps at {snap['frozen_at']}")
    elif a.cmd == 'check':
        if not os.path.exists(FROZEN):
            print('not frozen (run: python3 tools/freeze.py freeze)')
            sys.exit(1)
        old = kitlib.jload(FROZEN)
        # the frozen apps, plus listed apps that have extractor output but were not frozen
        apps = kitlib.app_list(a.apps) if a.apps else sorted(
            set(old['apps']) | {x for x in kitlib.app_list() if os.path.exists(os.path.join(kitlib.OUT, x + '.json'))})
        new = snapshot([x for x in apps if x in set(kitlib.app_list())])
        if a.apps:
            old = dict(old, apps={k: v for k, v in old['apps'].items() if k in apps})
        msgs = diff(old, new)
        if msgs:
            print('changed since the freeze at ' + old.get('frozen_at', '?') + ':')
            for m in msgs:
                print('  ' + m)
            sys.exit(1)
        print(f"frozen at {old.get('frozen_at')}; nothing changed")
    else:
        if os.path.exists(FROZEN):
            os.remove(FROZEN)
        print('thawed')


if __name__ == '__main__':
    main()
