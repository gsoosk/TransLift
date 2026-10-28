#!/usr/bin/env python3
"""Near-duplicate check for the selection rule: overlap of class names between code bases.

Usage: near_dupes.py [--threshold 0.5] [--min-classes 20] [slug ...]
  Compares each probed candidate (corpus/probe/<slug>.classes.txt) with every app in corpus/repos.tsv and with
  the other candidates. containment = shared names / names in the smaller code base; a value of 0.5 or more
  usually means a fork, a variant or a copy of the same application."""
import argparse, glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kitlib
import txscan

PROBE = os.path.join(kitlib.CORPUS, 'probe')
GENERIC = {'Application', 'App', 'Main', 'Config', 'Constants', 'Constant', 'Utils', 'Util', 'StringUtils', 'DateUtils',
           'Result', 'R', 'BaseEntity', 'BaseController', 'BaseService', 'BaseMapper', 'PageResult', 'PageQuery',
           'GlobalExceptionHandler', 'WebConfig', 'SwaggerConfig', 'RedisConfig', 'MybatisPlusConfig', 'CorsConfig',
           'JsonUtils', 'BeanUtils', 'SpringUtils', 'ServletUtils', 'IpUtils', 'Md5Utils', 'package-info',
           'SecurityConfig', 'ApiResponse', 'ResponseResult', 'BusinessException', 'ServiceException', 'User',
           'UserController', 'UserService', 'UserServiceImpl', 'UserMapper', 'Role', 'Menu', 'LoginController'}


def classes_of(slug):
    f = os.path.join(PROBE, slug + '.classes.txt')
    if os.path.exists(f):
        return {l.strip() for l in open(f) if l.strip()} - GENERIC
    d = os.path.join(kitlib.CLONES, slug)
    if not os.path.isdir(d):
        return None
    names = set()
    for dp, dns, fns in os.walk(d):
        dns[:] = [x for x in dns if x not in ('.git', 'node_modules', 'target', 'build')]
        for fn in fns:
            if fn.endswith('.java') and not txscan.is_test_path(os.path.relpath(os.path.join(dp, fn), d)):
                names.add(fn[:-5])
    return names - GENERIC


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('slugs', nargs='*', help='candidates to check (default: every probed repository)')
    ap.add_argument('--threshold', type=float, default=0.5)
    ap.add_argument('--min-classes', type=int, default=20)
    a = ap.parse_args()
    listed = [r['slug'] for r in kitlib.read_repos()]
    probed = sorted(os.path.basename(f)[:-len('.classes.txt')] for f in glob.glob(os.path.join(PROBE, '*.classes.txt')))
    cands = a.slugs or [s for s in probed if s not in listed]
    sets = {}
    missing = []
    for s in set(cands) | set(listed) | set(probed):
        c = classes_of(s)
        if c is None:
            missing.append(s)
        elif len(c) >= a.min_classes:
            sets[s] = c
    pairs = []
    for c in cands:
        if c not in sets:
            continue
        for o in sets:
            if o == c or (o in cands and o < c):
                continue
            inter = len(sets[c] & sets[o])
            cont = inter / min(len(sets[c]), len(sets[o]))
            if cont >= a.threshold:
                pairs.append((cont, c, o, inter, len(sets[c]), len(sets[o])))
    for cont, c, o, inter, nc, no in sorted(pairs, reverse=True):
        print(f"{cont:5.2f}  {c} ({nc} classes)  ~  {o} ({no} classes){'  [listed app]' if o in listed else ''}  shared {inter}")
    if not pairs:
        print(f"no pair with containment >= {a.threshold}")
    if missing:
        print('no class list for: ' + ', '.join(sorted(missing)))


if __name__ == '__main__':
    main()
