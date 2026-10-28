# txscan maintainer log

## 2026-10-27 — apache_roller calibration (txscan e398b9d7c6a6 -> 0c29234f0183)

Source: "Problems for the extractor" in `reviews/apache_roller/calibration.md` (no notes in `reviews/notes/`).
Corpus not frozen, no verdicts in `reviews/*/tx/`. Only `clones/apache_roller` is checked out, so the
regression was run on apache_roller alone (`out_prev/apache_roller.json` as baseline); the other 19 apps could
not be re-run. Changes that could behave differently in a Spring/MyBatis/JDBI app are marked below.

### 1. Implicit transactions walked straight through programmatic regions — fixed

Cause: `walk(mode='implicit')` made a `('tx', ...)` unit only for callees in `tb.tx_attr`, and the implicit
entry-point loop skipped an entry point only when `mid in tb.tx_attr`. Programmatic regions (region roots from
a begin or an app commit point) and their entry points were therefore walked as if their statements
auto-committed one by one. In an app whose explicit transactions are all programmatic this produced twins and
miscounts (`units_explicit_tx` was 0 for all 125 implicit records, `invoked_by` empty for all 102 regions).

Fix: `TxBuilder.compute_explicit_roots()` (called from `prepare()`, so every caller of `walk()` sees the same
boundaries, including `tools/export_blocks.py`) computes `explicit_roots` = declarative roots + programmatic
callbacks + the programmatic regions `run()` emits, and `region_tx_roots` (the region list `run()` iterates).
`walk(mode='implicit')` now emits a tx unit and stops at any callee in `explicit_roots`; self-invocation still
exempts a `@Transactional` callee only — a Spring proxy is bypassed, a programmatic region is not. The implicit
loop skips an entry point that is itself an explicit root and records it in that transaction's `invoked_by`.
Effect here: 52 implicit twins and 5 pure-delegation entry points gone, 59 regions got `invoked_by`,
`units_explicit_tx` / `explicit_tx_called` now populated. It also removes the "re-walking this root found a
different number of operations" warning that `export_blocks.py` would otherwise print on every implicit draft.

Side effect of the same restructuring: a programmatic region whose walk finds no operation is treated as a
begin/commit helper and is not emitted (`JPAPersistenceStrategy.getEntityManager`, 0 ops, was explicit tx 14),
which also keeps such helpers out of `explicit_roots`. This can drop a 0-op region in another app; a region
with no operations carries no corpus content, and `@Transactional` roots are never filtered (recall unaffected).

### 2. A synchronized block took the whole method's operations — fixed

Cause: the adhoc record used `tb.walk(mid)` over the entire method, so `SaltValidator.consumeSubmittedSalt`
listed 6 operations that all come from calls at lines 52 and 62, outside the `synchronized (saltCache)` block
at 65-71.

Fix: `walk()` takes `sync_ranges`; call sites and callbacks of the root method outside those line ranges are
not followed (statements reached through a call inside the block still count). `run()` passes the block ranges
when every lock site of the method is a synchronized block with a known end; an explicit lock/unlock pair or a
synchronized method still covers the whole method. The record disappears when nothing is left, as here.

### 3. `Connection.commit()` with no visible `setAutoCommit(false)` was not a region — fixed

Cause: region roots came only from `what in ('begin', 'commit-point')`. `DatabaseInstaller.upgradeTo400`
commits five times (lines 369, 520, 577, 626, 718) without a visible `setAutoCommit(false)`, so its JDBC work
folded into the two `Install.*` implicit transactions.

Fix: `what == 'commit'` also marks a region root. Two new explicit regions here
(`DatabaseInstaller.upgradeTo400`, `SQLScriptRunner.runScript`), and they are now units of `Install.bootstrap`
and `Install.upgrade` instead of loose statements. Kept conservative by 1: a region found only through a commit
must have operations of its own, so bare `commit()` wrappers produce nothing. This is the change most likely to
add a region in another app (JPA/Hibernate `Transaction.commit`, `PlatformTransactionManager.commit`), and only
in a method that holds database statements.

### 4. "JPQL aggregate queries lose the entity" — fixed, different cause

`jpql_entities` handles `SELECT COUNT(u) FROM User u` correctly. The real cause: `parse_jpa_xml` matched
`<named-query ...>\s*<query>`, and both affected queries have an XML comment between the two tags
(`User.orm.xml:68-73`, `Weblog.orm.xml:32-35`), so the query text was never read and the operation had no SQL.
Fix: strip `<!-- ... -->` from orm.xml/hbm.xml before parsing (a commented-out mapping is now ignored too,
which is correct). 9 operations gained their table.

### 5. App factories around `createNamedQuery` — fixed in the extractor, no rules key needed

Cause: `JPAPersistenceStrategy.getNamedUpdate(String)` and `getNamedQueryCommitFirst(String, Class)` wrap
`em.createNamedQuery`, but only `getNamedQuery` is in `QUERY_BUILDERS`; the existing "query text handed to an
app helper" branch of `_query_op` only accepts an argument that looks like SQL, and a named-query *name* does
not. Fix: `BodyScanner.named_queries` is filled before the body scan (the `@NamedQuery` collection moved ahead
of the scan for that) and that branch also accepts an argument that is a declared named query, turning it into
`NAMED:<name>`. No false positive is possible: the string must be a declared query name. 6 operations gained
their SQL and table. A `query_factories` rules key is therefore not needed.

### 6. Roller's own `Job` interface matched the Quartz entry-point rule — fixed

Cause: `compute_entries` matched `execute` against the simple supertype names
`Job/QuartzJobBean/Tasklet/StatefulJob`, and `org.apache.roller.weblogger.business.runnable.Job` is the app's
own interface. Fix: new `TxBuilder.framework_super()` requires the matching supertype to come from a library
(`EXT:` or not a project type), the same test the Spring Batch builtin already uses. Quartz jobs in other apps
keep matching (Quartz is a dependency, not app source, and an app interface extending `org.quartz.Job` still
has the library type among its supertypes). `HitCountProcessingJob.execute` stays in the corpus as the explicit
region it is; `WeblogCacheWarmupJob.execute` is no longer an entry point and had no record of its own before.

### Not fixed

- **Real scheduled work stays invisible** (`RollerTaskWithLeasing.run` as a plain `Runnable` submitted to an
  executor, `TaskScheduler.java:190`). `docs/extractor.md` deliberately excludes code that only a custom
  dispatcher or an executor runs from entry points; making `Runnable.run` a root would add roots across every
  app in the corpus on weak evidence. Reviewers add the wrapper as an added transaction.
- Reviewer-level items from the calibration (operations after the commit point inside a region, a region
  starting at the first write, `strategy.store(Object)` losing the table, Velocity model calls) were left to
  review as the calibration asks; none has a general static fix that does not over-approximate.

### Regression (apache_roller only)

```
apache_roller  tx 231->176, explicit 102->103, implicit 125->70, adhoc 4->3,
               ops 1599->1058, unk_pct 2.6->1.7 · 2 new, 57 gone, 38 changed
```

- new (2): `DatabaseInstaller.upgradeTo400`, `SQLScriptRunner.runScript` (problem 3).
- gone (57): 51 implicit web and 1 implicit scheduled whose root is now the explicit region itself (problem 1);
  3 implicit web whose only unit is one explicit region (`CommentDataServlet.doPost`, `Install.create`,
  `EntryEdit.saveDraft`); `JPAPersistenceStrategy.getEntityManager` (0-op region, problem 1);
  `SaltValidator.consumeSubmittedSalt` (problem 2).
- changed (38): implicit records whose statements became `units_explicit_tx` (problem 1) and records whose
  operations gained SQL or a table (problems 4 and 5: `unknown_table` dropped from 41 to 18 operations, the
  rest are the documented `strategy.store(Object)` and runtime-SQL cases). 59 regions additionally gained
  `invoked_by`, which the content hash ignores.
- `python3 tools/check_recall.py`: 0/0 for apache_roller, unchanged (the app has no `@Transactional`); no other
  app has output to compare.
- Determinism: two consecutive runs of apache_roller give identical `transactions`, `entities` and stats (only
  `stats.seconds_index_scan` varies, as before this change).

Suggestion for the orchestrator (not changed here): `docs/extractor.md` could state that in implicit mode a
programmatic region called from an entry point is a unit of work rather than inlined statements, and that a
region with no operations of its own is dropped.

## 2026-10-27 — apache_roller pilot review + calibration, round 2 (txscan 0c29234f0183 to 55c84539a215)

Input: `reviews/notes/pilot-apache_roller-0001.md` (problems 1 and 2) and the first, third and fourth
"Problems for the extractor" items of `reviews/apache_roller/calibration.md` (problems 3 to 5).
Single-app round: only `clones/apache_roller` is checked out, so the baseline is `out_prev/apache_roller.json`
and the regression is `python3 tools/regress.py apache_roller`.

### 1. A programmatic region included the reads that run before its lazy begin — fixed

Cause: `TxBuilder.walk` anchored a `programmatic-region` at method entry, so every operation reachable from
the root method was in the unit. Roller's unit of work is opened lazily by
`JPAPersistenceStrategy.getEntityManager(true)` (`JPAPersistenceStrategy.java:244-246`), which only the writes,
`getNamedUpdate`, `refresh` and the pessimistic find call; a plain read goes through `getEntityManager(false)`
and auto-commits on its own.

Fix: new `TxBuilder.region_bounds()` and `TxBuilder.trim_region_ops()`, applied in `walk` for `mode='tx'`.
For a region recognised **only** by an app commit point (`commit_points` in `rules/<app>.json`) and with no
`begin` call in the root method, the unit now starts at the first write or locking read: the earliest root-method
call site through which an operation with `op` `W`/`L` or a `lockhint` is reached. Operations reached through an
earlier call site of the root method are dropped. The begin is marked in the draft's code listing through a
synthetic `txapis` entry (`region begins here (lazy begin ...)`), so reviewers see the boundary; `txapis` is
display-only (`export_blocks.py:203`, `show.py:37`), no counter reads it. `TxBuilder.region_info[root]` keeps
the begin line, the commit line and how many operations each side dropped, for inspection with `tools/dbg.py`;
it is not written to `out/<app>.json`, so the output format is unchanged.

`250ee4ce4855 FrontpageSettings.applyInitial` is now 2 operations (the pessimistic find at
`JPAPropertiesManagerImpl:155` and the `persist`), with ops 2, 3 and 5 dropped, exactly as the reviewer asked,
and the marker sits on `mgr.compareAndSetProperty(...)` at `FrontpageSettings.java:99`.

Deliberate limits, written here because they are judgement calls, not oversights:

- Attribution uses the first (shortest) path from the root, the same `parents` chain the drafts use. A callee
  reached both before and after the begin counts as "before": that is what the reviewer's own example asks for
  (op 3 is `mgr.getProperty`, called at line 93 *and* at line 101, and was judged pre-begin).
- Operations marked `loop` are never dropped: a line comparison cannot tell which iteration they belong to.
- Roller also begins the unit in `getNamedQueryCommitFirst` and `refresh`; the general rule (first write or
  locking read) misses these. Checked: no dropped operation comes from `JPAAutoPingManagerImpl:91` or
  `JPAWeblogManagerImpl:229`, the only two `getNamedQueryCommitFirst` call sites, and the 7 dropped
  `strategy.refresh` operations are all dropped by problem 2 below (after the flush), not before the begin. So
  the approximation costs nothing in this app.
- A region with no write and no lock anywhere is left untouched (nothing shows where its unit starts), so no
  region loses all of its operations and no region disappears.

### 2. Operations after the commit point were still inside the region — fixed

Cause: same anchoring; the whole root method was the region, including the statements after its `flush()`.

Fix: the same `region_bounds()` returns the line of the **last** commit point in the root method, and
`trim_region_ops` drops operations reached through a later call site of the root method (again, never one in a
loop). All six sites calibration lists are now out: `1072bc347f3d JPAMediaFileManagerImpl.updateThumbnail` keeps
only the `persist` (the `strategy.refresh` at `JPAMediaFileManagerImpl.java:287` is gone), and the same for
`createMediaFile`, `createThemeMediaFile`, `moveMediaFiles` (two) and `updateMediaFile` (two).

Scope decision: the trim applies only to regions whose evidence is an app commit point, not to a region with a
real `begin`/`commit` API (JDBC, a transaction manager). With a real begin the transaction is open from the
first statement, and dropping what follows a `commit()` would silently lose a unit that nothing else records —
`DatabaseInstaller.upgradeTo400` commits five times (`:369,520,577,626,718`) and still writes
`roller_properties` in `updateDatabaseVersion` afterwards. That record stays at 30 operations and reviewers
split it, as calibration asks. Only `apache_roller` declares `commit_points`, so problems 1 and 2 cannot change
any other app's output.

### 3. Servlet entry points matched by method name only — fixed

Cause: `compute_entries` matched `m.name in ('doGet','doPost','doPut','doDelete','service')` with
`sup & {'HttpServlet','GenericServlet'}` and nothing else. Fix: new module function `is_servlet_signature(m)`
requires a non-private, non-static method with exactly two parameters whose types end in `ServletRequest` and
`ServletResponse`. Roller's three private helpers (`RollerAtomServlet.java:217,258,282`) are no longer entry
points; the 19 records rooted at a real servlet method are unchanged, including the ones whose signature is
spread over two lines (`ThemeDataServlet`, `OpenSearchServlet`, `TagDataServlet`). Their work stays in
`ab7a64a31643 RollerAtomServlet.service`, and the eight Atom regions they called keep
`RollerAtomServlet.service [web]` in `invoked_by`.

### 4. Struts 2 / xwork interceptors are now entry points — fixed

Cause: the interceptor rule covered only Spring MVC `HandlerInterceptor` and servlet/WebFlux filters. Fix: a
method named `intercept` or `doIntercept` with one parameter in a class whose supertype is a
Struts/xwork `Interceptor`, `AbstractInterceptor` or `MethodFilterInterceptor` is a `web` entry point. The
supertype is checked **qualified** (new `TxBuilder.is_struts_interceptor`: the matching supertype's name must
contain `struts` or `xwork`), so MyBatis's `org.apache.ibatis.plugin.Interceptor.intercept(Invocation)` and
Hibernate's `Interceptor` — both one-parameter `intercept` methods — do not match. Three records appear:
`UIActionInterceptor.doIntercept` (reads `weblog`, `roller_user`), `UISecurityInterceptor.doIntercept` (reads
`userrole`, `roller_permission`, `weblog`), `ValidateSaltInterceptor.intercept` (reads `roller_user`).
`UIActionPrepareInterceptor.doIntercept` has no DB work of its own, so it stays an entry point without a record.

### 5. Struts action getters matched the action-method rule — fixed

Cause: the rule accepted any public no-argument `String` method of an `ActionSupport`/`UIAction` subtype. Fix:
new module function `is_bean_accessor(name)` (`get|is|has` + an upper-case letter, plus `toString`/`hashCode`)
excludes JavaBean accessors, which the view calls while rendering but the framework never dispatches to. The
four one-read records calibration lists are gone (`c5e9c20854e5`, `065189bd9bc6`, `385309c6e0b3`,
`05160eed15e8`). 66 of Roller's 234 entry points were such accessors; the other 62 had no DB operation and no
record, and none of them was the `invoked_by` of a region, so nothing else changed. Reading
`<action method=...>` from `struts.xml` would be exact but Roller declares no `method` attribute on any of its
59 actions (every action uses the default), so it would not help here and would need new XML plumbing.

### Not fixed

- **The reads dropped from a region are not re-attributed to the entry point that calls it.** They really are
  auto-commit statements of the caller, but `walk(mode='implicit')` stops at a region root and records a unit,
  so it never sees them; four implicit records (`EntryEdit.publish`, `MediaFileView.delete`,
  `MediaFileView.includeInGallery`, `RollerAtomServlet.service`) lost a table this way. Adding them would mean
  carrying a region's dropped operations into its callers' records, whose draft call tree does not contain the
  methods they live in, so `export_blocks` could not place them. Reviewers can add them per transaction.
- Nothing else from the calibration's reviewer list was touched: those are per-transaction corrections
  (`upgradeTo400` split, `PingQueueProcessor.inside_db_tx`, the `strategy.store(Object)` unknown tables).

### Regression (apache_roller only; the other 19 apps are not checked out)

```
apache_roller  tx 176->172, explicit 103->103, implicit 70->66, adhoc 3->3,
               ops 1058->824, unk_pct 1.7->2.1 · 3 new, 7 gone, 68 changed
```

- new (3): the Struts 2 interceptors (problem 4).
- gone (7): the four Struts getters (problem 5) and the three private `RollerAtomServlet` helpers (problem 3).
- changed (68): all 68 are explicit regions whose operations were trimmed (problems 1 and 2) — 221 operations
  dropped, every one of them a read: not a single write was dropped, no record was emptied, no subkind changed.
  Beyond the content hash, 4 implicit records only lost a table read through the regions they call
  (`EntryEdit.publish`, `MediaFileView.delete`, `MediaFileView.includeInGallery`, `RollerAtomServlet.service`),
  and 9 records changed `invoked_by` (8 Atom regions lost the two private-helper labels,
  `BasicUserAutoProvision.execute` gained two interceptors).
- `unk_pct` rises from 1.7 to 2.1 because the denominator shrinks: 18 unknown-table operations of 1,058 before,
  17 of 824 now.
- `python3 tools/check_recall.py`: 0/0 for apache_roller, unchanged (no `@Transactional` in the app); the other
  apps have no output to compare in this round.
- Determinism: two consecutive runs give identical `transactions`, `entities` and all non-timing stats (only
  `stats.seconds_*` differ, as before).
- Verdicts: `1072bc347f3d` and `250ee4ce4855` of the 5 pilot verdicts now judge changed content and need a new
  verdict; `96c072380741`, `159907733aa7` and `11fbbb1a491d` are untouched.

### Risk for the apps that cannot be run in this round

- Problems 1 and 2 are gated on `commit_points`, which only `rules/apache_roller.json` declares: no app with
  `@Transactional`, MyBatis or JDBI transactions can be affected, and no JDBC `con.commit()` or
  `PlatformTransactionManager.commit()` region is affected either.
- Problem 3 can only *remove* a servlet entry point, and only one whose signature is not the container's
  (private, static, or not `(ServletRequest, ServletResponse)`); a real override always keeps it.
- Problem 4 can only *add* entry points, and only in an app with a Struts/xwork interceptor; the qualified-name
  check keeps MyBatis and Hibernate `Interceptor` implementations out. Not testable on another Struts 2 app in
  this round.
- Problem 5 can only remove entry points, and only `get*`/`is*`/`has*`/`toString`/`hashCode` methods of an
  `ActionSupport`/`UIAction` subtype. An app that declares `<action method="getSomething">` would lose that
  entry point; Roller uses no `method` attribute at all.

Suggestion for the orchestrator (not changed here): the `docs/extractor.md` blind spot "A programmatic region
is the whole method that holds the commit: code after an app commit point (Roller's `Weblogger.flush()`) ... are
walked as part of it" is now out of date for commit-point regions, and the `commit_points` row of the rules
table could say that such a region begins at its first write or locking read and ends at its last commit point.
