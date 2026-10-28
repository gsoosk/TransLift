# What counts as a transaction

The corpus has three kinds. Every transaction has one root: the method (or lambda) where it starts.

## explicit: the framework runs the code as one database transaction

**Declarative** (`subkind: declarative`, or `app-rule`)

- `@Transactional` from Spring (`org.springframework.transaction.annotation`) or `jakarta`/`javax.transaction`, on the method, on the class, or on an interface or superclass method it implements.
- EJB `@TransactionAttribute`, Seata `@GlobalTransactional`.
- App rules (`app-rule`): framework methods that always run in a transaction. Built in: Spring Batch steps (`Tasklet.execute`, and `ItemReader.read`, `ItemProcessor.process`, `ItemWriter.write` of chunk steps, which run in the step's transaction). Per app: for example BenchBase `Procedure.run` (set in `rules/<app>.json`, see `docs/extractor.md`).

The annotation must take effect. With Spring's default proxy mode:

- a `private` or `final` method is never transactional;
- `protected` and package-private methods are transactional only with class-based proxies on Spring 6 or later;
- a call from the same object (`m()` or `this.m()`) bypasses the proxy, so the annotation does nothing on that path;
- AspectJ mode (`@EnableTransactionManagement(mode = AdviceMode.ASPECTJ)`) removes these limits.

Propagation decides whether the method starts a transaction:

| Propagation | Meaning for the root |
|---|---|
| `REQUIRED` (default) | Joins the caller's transaction, or starts one. A root. |
| `REQUIRES_NEW` | Always a separate transaction that commits on its own. A root; callers record it as a nested call. |
| `NESTED` | Joins with a savepoint, or starts one. A root. |
| `SUPPORTS`, `MANDATORY` | Never starts a transaction of its own. Not a root. |
| `NOT_SUPPORTED`, `NEVER` | Runs without a transaction; its statements auto-commit. Not a root. |

By default runtime exceptions and errors roll back and checked exceptions commit; `rollbackFor`, `noRollbackFor` (Spring) and `rollbackOn`, `dontRollbackOn` (Jakarta) change that.

**Programmatic** (`programmatic-callback`: the callback is the transaction; `programmatic-region`: the method holding begin ... commit)

- `TransactionTemplate.execute` / `executeWithoutResult`
- `PlatformTransactionManager.getTransaction(...)` ... `commit` / `rollback`
- JPA `EntityManager.getTransaction().begin()` ... `commit()`; Hibernate `Session.beginTransaction()`
- JDBC `setAutoCommit(false)` ... `commit()`
- JDBI `inTransaction` / `useTransaction`; jOOQ `transaction(...)`
- app wrappers, such as Kill Bill's `EntitySqlDaoTransactionalJdbiWrapper.execute`, or Roller's `Weblogger.flush()` commit points

Transactions configured outside the code (Spring XML `<tx:advice>` with AOP pointcuts, `TransactionInterceptor`, `TransactionProxyFactoryBean`) are explicit too. The extractor does not read that configuration; reviewers and calibrators look for it.

## implicit: statements that commit one by one

An entry point that reaches database operations with no enclosing transaction. Each statement, and each explicit transaction it calls, commits separately.

Entry points: web endpoints (Spring `@RequestMapping` family, JAX-RS `@Path`, servlets, Struts actions, and the filters and Spring MVC interceptors that run around them), `@Scheduled` and Quartz jobs, message listeners (`@KafkaListener`, `@RabbitListener`, `@JmsListener`), event listeners (`@EventListener`, `ApplicationListener`), and startup hooks (`CommandLineRunner`, `ApplicationRunner`, `@PostConstruct`, `InitializingBean`). The exact list the extractor uses is in `docs/extractor.md`.

Calls that are transactions of their own: Spring Data repository methods (each call is one unit), MyBatis-Plus `IService` batch methods such as `saveBatch`, and every explicit transaction called.

For an implicit transaction, the operations and their counts cover the entry point's own statements; its tables read and written also include those of the explicit transactions it calls, and its shape counts each called explicit transaction as one unit.

An entry point is listed as an implicit transaction when it runs database statements of its own, or when it calls two or more explicit transactions that write (they commit separately, so together they are an unprotected multi-write). An entry point whose only database work is one explicit transaction, or explicit transactions that only read, is not listed: the explicit transaction is the unit.

Subkind is `<entry kind>/<shape>`, for example `web/multi-write`:

- `multi-write`: two or more write units; a write inside a loop counts as two
- `read-then-write`: a read and a write on overlapping tables
- `single-write`
- `read-only`

Read-only ones stay in the corpus: they matter for conflicts.

## adhoc: hand-made coordination around database work

A region guarded by an application-level construct that reaches database operations:

- `synchronized` methods and blocks (`synchronized-method`, `synchronized-block`)
- `java.util.concurrent` locks: `ReentrantLock`, `ReadWriteLock`, `StampedLock`, `Semaphore` (`app-lock:<type>`)
- Redisson `RLock`; Redis `SETNX` / `setIfAbsent` locks
- lock and idempotency annotations: `@DistributedLock`, `@RedisLock`, `@Idempotent`, `@NoRepeatSubmit` and similar (`annotation:<name>`)

`attrs.inside_db_tx` says whether the region also runs inside a database transaction. A lock taken inside a transaction is usually released before the commit, which is a classic source of anomalies.
