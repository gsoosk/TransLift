# Java transaction corpus kit for Claude Code

This kit lets Claude Code build a corpus of the database transactions in well-known open-source Java applications, the way the original 20-app corpus was built, and then has agents check every transaction against the source.

1. **Select.** Rank GitHub's Java repositories by stars and keep real applications that use a relational database, by the rule in `docs/selection-rule.md`. The 20 seed apps stay; new ones are added by the rule, and every decision is logged with its reason.
2. **Extract.** Check out each app at a pinned commit and run the static extractor `tools/txscan.py`, which finds explicit, implicit and ad hoc transactions (`docs/transaction-kinds.md`) and the database operations each one reaches.
3. **Calibrate.** One agent per app tunes the extractor's per-app settings, spot-checks ten transactions and reports systematic problems; a maintainer agent fixes those in the extractor.
4. **Review every transaction.** Reviewer agents read each transaction's code and SQL and confirm, correct or exclude it, add missed operations and missed transactions, and state its boundary (`docs/review-rules.md`).
5. **Build.** CSVs, a workbook with formulas, and one Markdown file per transaction with its code, SQL and review.

## What you get

| Path | Contents |
|---|---|
| `deliver/transactions.csv` | one row per transaction: root, kind, attributes, tables read and written, chopping-relevant features, review verdict |
| `deliver/operations.csv` | one row per database operation, with tables, flags, SQL and whether a reviewer added or corrected it |
| `deliver/corpus_summary.csv`, `excluded.csv`, `corpus_stats.json`, `recall.json` | per-app counts, what review excluded and why, corpus statistics, the `@Transactional` recall check |
| `build/java_tx_corpus.xlsx` | the same data with README, Summary and Apps sheets built from formulas |
| `tx-blocks/<app>/<kind>/<n>_<root>.md` | one file per transaction: its block, every method on the call path to its operations, the SQL, and the review |
| `corpus/selection.md` | why each candidate application was accepted or rejected |

## Requirements

- **Claude Code**, a recent version (the kit uses project agents, a skill, and a hook).
- **Python 3.11+**: `pip install -r requirements.txt` (tree-sitter 0.26, tree-sitter-java 0.23.5, openpyxl).
- **git**, and the **GitHub CLI** logged in (`gh auth login`) for the selection phase. A `GITHUB_TOKEN` works too.
- **LibreOffice** (optional) to store computed values in the workbook; Excel computes them on opening anyway.
- Disk: about 10 GB for 40 checkouts plus drafts.

## Start

```bash
cd java-tx-corpus-kit
pip install -r requirements.txt
gh auth login                      # once, for the GitHub search
claude                             # accept the workspace trust prompt for this folder
```

In Claude Code:

```text
/tx-corpus status                  # where things stand
/tx-corpus all --add 20 --pilot 5  # the whole pipeline, with a 5-per-app pilot review first
```

Each phase can also run on its own and resumes from what is on disk: `/tx-corpus select`, `calibrate`, `fix`, `freeze`, `review`, `build`. The orchestrator asks before expensive or destructive steps unless you pass `--yes`. Options: `--min-stars N` (default 1,000), `--parallel N` agents at a time (default 6), `--max-batches N`, `--skip-readonly`.

## The agents

| Agent | Model | Job | Writes |
|---|---|---|---|
| `java-repo-scout` | your session's | applies the selection rule to the GitHub ranking, probes candidates, logs every decision | `corpus/` |
| `java-tx-calibrator` | your session's | one app: checkout, extractor run, per-app rules, 10-transaction spot check, report | `rules/<app>.json`, `reviews/<app>/calibration.md` |
| `txscan-maintainer` | your session's | fixes systematic extractor problems, with a regression run over every app | `tools/txscan.py` |
| `java-tx-reviewer` | Sonnet | one batch of transactions: a verdict per transaction | `reviews/` |

Change a model in the agent's file (`.claude/agents/<name>.md`, `model:` line: `sonnet`, `opus`, `haiku`, `inherit` or a full model id). A hook (`tools/guard_paths.py`, wired in `.claude/settings.json`) keeps each agent's file writes inside its own folders and keeps reviewers to read-only shell commands; it is a guard rail, not a sandbox. The project's permission rules pre-approve the kit's own tools; edits to `tools/txscan.py` by the maintainer still ask for your approval.

## Scale and cost

Reviewing every transaction is the expensive part, so measure before you commit:

- The 20 seed apps hold **9,573 transactions** (5,451 explicit, 3,969 implicit, 153 ad hoc) and 82,117 operations. Their review drafts total about **61 million tokens**, in about **1,170 batches** of at most 25 transactions or 60,000 draft tokens. Twenty more apps would roughly double that.
- A reviewer reads each draft, opens the source it needs and writes a verdict. In our tests a Sonnet reviewer took from about 12 seconds per transaction (a 25-transaction MyBatis batch in 5 minutes) to about 5 minutes (intricate JPA transactions in Roller); agents run in parallel (`--parallel`).
- The pilot (`--pilot 5`: five transactions per app, spread over kinds and sizes) gives real numbers for your account and the corrections reviewers make. Then choose: review everything, leave out implicit read-only transactions (`--skip-readonly`, about 2,200 fewer for the seeds), or change the reviewer's model.
- For long runs, use the unattended driver: one headless session per batch, resumable, with the estimated cost of each batch logged.

```bash
tools/run_reviews.sh -j 3                     # add --model opus, --budget-usd 3 or --max 50 as needed
python3 tools/review_status.py --cost         # estimated spend so far
```

It claims batches, so an interactive `/tx-corpus review` and the driver can run at the same time.

## How review results flow back

Each verdict is tied to a content hash of the transaction it judged. `tools/apply_reviews.py` applies valid, current verdicts to the extractor output (`out/` + `reviews/` → `out_reviewed/`): corrections replace operations and recompute counts, exclusions move to a separate list, and added transactions get ids `<app>:r1`, `r2`, .... If the extractor changes later, only transactions whose content changed lose their verdict; `tools/review_status.py` reports them and `tools/make_batches.py` puts them in new batches.

## Layout

| Path | Contents |
|---|---|
| `.claude/skills/tx-corpus/SKILL.md` | the `/tx-corpus` orchestration |
| `.claude/agents/` | the four agents |
| `.claude/settings.json` | permissions for the tools, the path guard hook |
| `CLAUDE.md` | project notes every agent reads |
| `docs/` | selection rule, transaction kinds, review rules, extractor and per-app rules |
| `tools/` | the extractor and every pipeline step (each has `--help`) |
| `corpus/repos.tsv`, `corpus/pins/` | the app list and pinned commits (the 20 seeds are included) |
| `corpus/probe/*.classes.txt` | class names of the seeds, for the near-duplicate check |
| `rules/` | per-app extractor settings (5 seeds have some) |
| `clones/`, `out/`, `drafts/`, `reviews/`, `out_reviewed/`, `tx-blocks/`, `deliver/`, `build/` | created by the pipeline |

## Working by hand

Every step is a plain command, so you can run or redo any of them yourself:

```bash
python3 tools/corpus.py list                       # apps and their state
tools/clone_app.sh apache_roller                   # checkout at the pinned commit
tools/run_one.sh apache_roller                     # extractor -> out/apache_roller.json
python3 tools/app_report.py apache_roller          # what it found, and where it is unsure
python3 tools/show_tx.py apache_roller --sample 5  # transactions as reviewers see them
python3 tools/export_blocks.py apache_roller       # drafts
python3 tools/review_status.py                     # pipeline and review progress
```

## Differences from the earlier 20-app corpus

The kit's extractor improves on the one used for the earlier corpus (9,487 transactions), so its numbers differ slightly:

- stable transaction keys, used by the review files;
- entity inheritance: single-table subclasses use their root's table, joined subclasses also touch their parents' tables (fixes, for example, Roller's `roller_permission` and Fineract's savings products);
- constructor calls resolve to exactly that class's constructor, and exception constructors are not walked;
- Spring MVC interceptors, servlet filters, `ApplicationListener`, `@PostConstruct` and `InitializingBean` are entry points;
- Spring Batch steps (`Tasklet.execute`, and the reader, processor and writer of chunk steps) are explicit transactions (`app-rule`), since they run in the step's transaction;
- the drafts list the calls the extractor did not follow, so reviewers can follow them.

The recall check is unchanged: 3,663 of 3,666 `@Transactional` methods with bodies are found as roots.
