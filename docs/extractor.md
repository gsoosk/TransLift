# The extractor (txscan) and per-app rules

`tools/txscan.py` finds transaction candidates statically, without building the application.

1. Parse all non-test Java sources with tree-sitter-java; index types, fields, methods, imports and generic bindings.
2. Resolve calls by class-hierarchy analysis: for each concrete subtype of the receiver, the implementation it would run. Overloads are told apart by name and argument count. Calls with more than 8 targets are skipped, counted and listed in the drafts ("Calls not followed"). A constructor call runs exactly that class's constructor; constructors of exceptions are not walked (the transaction is failing).
3. From each root, walk the calls (depth 40, at most 6,000 methods), tracking which entity a generic DAO works on. Callees that start their own transaction (`REQUIRES_NEW`, `NOT_SUPPORTED`, `NEVER`) are recorded as nested and not entered; `@Async` callees are recorded.
4. Attribute tables from JPA annotations, `orm.xml` and `hbm.xml` (with inheritance: a single-table subclass uses its root entity's table, the JPA default; a joined subclass also touches its parents' tables), Spring Data (derived queries and `@Query`, constants included), JPQL/HQL, criteria queries, native SQL, MyBatis XML and annotations, MyBatis-Plus generics, JdbcTemplate/JDBC strings (with constants and simple getters resolved) and JDBI `.stg` templates.
5. Lambdas passed to stream or collection operations count as loops; lambdas passed to `Optional.ifPresent`/`map`/`orElseGet` or `Map.computeIfAbsent` count as conditional.

Entry points (roots of implicit transactions):

| Kind | Recognised as |
|---|---|
| `web` | Spring MVC mapping annotations (`@RequestMapping`, `@GetMapping`, ..., `@MessageMapping`); JAX-RS `@GET`, `@POST`, ... (also on implemented interface methods); servlet `doGet`/`doPost`/`doPut`/`doDelete`/`service`; Struts 2 actions (public no-argument `String` methods of `ActionSupport` subclasses); Spring MVC interceptors (`preHandle`, `postHandle`, `afterCompletion`); filters (`Filter.doFilter`, `OncePerRequestFilter.doFilterInternal`, WebFlux `WebFilter.filter`) |
| `scheduled` | `@Scheduled`, `@SchedulerLock`, `@XxlJob`; Quartz `Job.execute` and `QuartzJobBean.executeInternal` |
| `message` | `@KafkaListener`, `@RabbitListener`, `@RabbitHandler`, `@JmsListener`, `@SqsListener`, `@StreamListener`; `onMessage` of `MessageListener` and RocketMQ listeners |
| `event` | `@EventListener`, `@TransactionalEventListener`, `ApplicationListener.onApplicationEvent` |
| `startup` | `CommandLineRunner.run`, `ApplicationRunner.run`, `@PostConstruct`, `InitializingBean.afterPropertiesSet` |

Spring Batch steps are not entry points but explicit `app-rule` roots, because they run in the step's transaction: `Tasklet.execute`, `ItemReader.read`, `ItemProcessor.process` and `ItemWriter.write` of classes implementing Spring Batch's interfaces.

Anything else that runs code on its own (custom dispatchers, Netty handlers, plain `Runnable`s handed to executors) is not an entry point; reviewers record what matters as added transactions.

Test code is skipped: `src/test/`, test-named modules (`*-test`, `it`, `e2e`, `examples`, `demo`, `samples`, ...) and `*Test.java`-style files (`txscan.is_test_path`).

Output: `out/<app>.json` with `stats`, `transactions` (each with a stable `key`, its operations, effects, locks, nested calls and analysis gaps) and `entities`.

## Known blind spots

- JPA dirty-checking writes (setters on managed entities, flushed at commit) are not listed, and `persist` or `save` on an entity that is already managed is listed as if it inserted.
- Reads of eagerly fetched associations (JPA `@ManyToOne` and `@OneToOne` by default) are not listed; an operation lists the table of its statement's entity.
- Calls through reflection, external libraries, dispatch by name, or with more than 8 targets are not followed.
- About 5% of operations have no table: dynamic table names, generic DAOs reached without a type binding, SQL assembled at runtime.
- Transactions configured in XML or AOP (`<tx:advice>`, `TransactionInterceptor`) are not seen; their entry points show up as implicit.
- Methods of anonymous classes are never roots.
- Overloads with the same number of arguments are not told apart, so a call can reach the wrong overload.
- A programmatic region is the whole method that holds the commit: code after an app commit point (Roller's `Weblogger.flush()`), and other region roots called inside it, are walked as part of it.
- Everything is a static over-approximation: every branch and every possible subtype counts.

Reviewers correct these per transaction; calibrators and the maintainer fix systematic ones.

## Per-app rules: `rules/<app>.json`

A JSON object with any of these keys (all optional):

| Key | Value | Use it for |
|---|---|---|
| `exclude_paths` | list of strings. One ending in `/` is a path prefix from the repository root (`"fit/"`); anything else matches anywhere in the path (`"persistence-neo4j"`). | Test-fixture, integration-test, example, generator or alternative-backend modules that `is_test_path` does not catch. |
| `commit_points` | list of `[TypeName, methodName]` | App methods that commit the current unit of work, such as Roller's `Weblogger.flush()`. Methods that call them become `programmatic-region` roots. |
| `procedure_roots` | list of `[SuperTypeName, methodName]` | Methods of subclasses that the framework always runs in a transaction, such as BenchBase's `Procedure.run`. They become `app-rule` roots. |
| `audited_writes` | list of table names | Tables an audit wrapper writes on every audited write, such as Kill Bill's `audit_log`. |

Example (`rules/apache_syncope.json`):

```json
{"exclude_paths": ["persistence-neo4j", "fit/"]}
```

After editing: `tools/run_one.sh <app>` and `python3 tools/app_report.py <app>`.

## Changing txscan itself

Only the `txscan-maintainer` agent (or you) changes `tools/txscan.py`, and only before the freeze, or after thawing it (`python3 tools/freeze.py thaw`). Keep the output deterministic. Check every change on all apps:

```bash
tools/run_all.sh -j 4 --save-prev      # out/ -> out_prev/, then re-run
python3 tools/regress.py               # what changed, per app
python3 tools/check_recall.py          # @Transactional recall
```

Verdicts survive changes that do not touch their transaction: each verdict is tied to the content hash of the transaction it judged, and `tools/review_status.py` shows the ones that became stale.
