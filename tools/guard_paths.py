#!/usr/bin/env python3
"""PreToolUse hook (wired in .claude/settings.json): keeps each kit agent inside its own files.

Claude Code passes the tool call as JSON on stdin; agent_type names the subagent (or the --agent session).
Exit 0 lets the call through; exit 2 blocks it and shows the message on stderr to the agent.
The main session is not restricted here (its permission rules still apply). This is a guard rail for
well-meaning agents, not a sandbox: shell commands can always reach further than any pattern check."""
import json, os, re, sys

# where each agent may write files (with Write/Edit, or through shell redirects and tee)
WRITE_ALLOW = {
    'java-tx-reviewer': (r'reviews/[^/]+/(tx|added)/[^/]+\.json', r'reviews/notes/[^/]+\.md'),
    'java-tx-calibrator': (r'rules/[^/]+\.json', r'reviews/[^/]+/[^/]+\.md'),
    'java-repo-scout': (r'corpus/.+',),
    'txscan-maintainer': (r'tools/txscan\.py', r'reviews/maintainer-log\.md'),
}
WHERE = {
    'java-tx-reviewer': 'reviews/<app>/tx/, reviews/<app>/added/ and reviews/notes/',
    'java-tx-calibrator': 'rules/<app>.json and reviews/<app>/*.md',
    'java-repo-scout': 'corpus/',
    'txscan-maintainer': 'tools/txscan.py and reviews/maintainer-log.md',
}
# reviewers only read code and record verdicts (with the Write tool)
BASH_ALLOW = {
    'java-tx-reviewer': (r'python3 tools/(review_batch|show_tx)\.py( |$)', r'(rg|grep|ls|wc|head|tail|cat|nl)( |$)',
                         r'cd [^.]', r'find (?!.*-(exec|execdir|delete|ok|okdir|fprint|fprintf|fls))'),
}
BASH_DENY = (r'(^|\s)--pre(-glob)?(=|\s|$)',)          # rg --pre runs a program on every file
WRITE_TOOLS = ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')
SAFE_TARGETS = ('/dev/null', '/dev/stdout', '/dev/stderr')


def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def split_command(cmd):
    """Split a shell command at |, ||, &&, ; and newlines outside quotes.
    Returns (segments, redirect targets, unsafe), unsafe meaning command or process substitution."""
    segs, cur, q, i, unsafe, targets = [], [], None, 0, False, []
    n = len(cmd)
    while i < n:
        c = cmd[i]
        if q == "'":
            if c == "'":
                q = None
            cur.append(c)
        elif q == '"':
            if c == '\\' and i + 1 < n:
                cur.append(cmd[i:i + 2]); i += 2; continue
            if c == '"':
                q = None
            elif c == '`' or cmd.startswith('$(', i):
                unsafe = True
            cur.append(c)
        else:
            if c in '\'"':
                q = c
                cur.append(c)
            elif c == '\\' and i + 1 < n:
                cur.append(cmd[i:i + 2]); i += 2; continue
            elif c == '`' or cmd.startswith('$(', i) or cmd.startswith('<(', i) or cmd.startswith('>(', i):
                unsafe = True
                cur.append(c)
            elif c == '>':
                j = i + 1
                if j < n and cmd[j] == '>':
                    j += 1
                if j < n and cmd[j] == '&':          # >&2, 2>&1: a descriptor, not a file
                    cur.append(cmd[i:j + 1]); i = j + 1; continue
                while j < n and cmd[j] in ' \t':
                    j += 1
                k = j
                while k < n and cmd[k] not in ' \t;|&\n<>':
                    k += 1
                targets.append(cmd[j:k].strip('\'"'))
                cur.append(cmd[i:k]); i = k; continue
            elif c in '|;&\n':
                w = 2 if cmd[i:i + 2] in ('||', '&&') else 1
                segs.append(''.join(cur).strip())
                cur = []
                i += w
                continue
            else:
                cur.append(c)
        i += 1
    segs.append(''.join(cur).strip())
    segs = [s for s in segs if s]
    for s in segs:                                      # tee writes the files it names
        if re.match(r'tee(\s|$)', s):
            targets += [w for w in s.split()[1:] if not w.startswith('-')]
    return segs, targets, unsafe


def allowed_path(agent, path, cwd, project):
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(cwd or project, path))
    rel = os.path.relpath(full, project).replace(os.sep, '/')
    ok = not rel.startswith('..') and any(re.fullmatch(p, rel) for p in WRITE_ALLOW.get(agent, ()))
    return ok, rel


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    agent = data.get('agent_type') or ''
    tool = data.get('tool_name') or ''
    ti = data.get('tool_input') or {}
    if agent not in WRITE_ALLOW:
        sys.exit(0)
    project = os.path.realpath(os.environ.get('CLAUDE_PROJECT_DIR') or data.get('cwd') or os.getcwd())
    cwd = data.get('cwd') or project
    if tool in WRITE_TOOLS:
        path = ti.get('file_path') or ti.get('notebook_path') or ''
        if path:
            ok, rel = allowed_path(agent, path, cwd, project)
            if not ok:
                block(f"{agent} may only write {WHERE[agent]} (not {rel}). Record what you found in your report "
                      f"or notes instead.")
    elif tool == 'Bash':
        segs, targets, unsafe = split_command((ti.get('command') or '').strip())
        for t in targets:
            if t and t not in SAFE_TARGETS:
                ok, rel = allowed_path(agent, t, cwd, project)
                if not ok:
                    block(f"{agent} may only write {WHERE[agent]}; this command writes {rel}.")
        if agent in BASH_ALLOW:
            if unsafe or targets and any(t not in SAFE_TARGETS for t in targets):
                block('Reviewers run read-only commands only: no output redirects or command substitution. '
                      'Write verdict files with the Write tool.')
            for seg in segs:
                if any(re.search(p, seg) for p in BASH_DENY) or not any(re.match(p, seg) for p in BASH_ALLOW[agent]):
                    block('Reviewers may run python3 tools/review_batch.py, python3 tools/show_tx.py and read-only '
                          f'commands (rg, grep, ls, cat, head, tail, wc, find). Not allowed: {seg[:80]}')
    sys.exit(0)


if __name__ == '__main__':
    main()
