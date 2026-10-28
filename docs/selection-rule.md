# Selection rule

The corpus keeps the 20 seed applications in `corpus/repos.tsv` (`source = seed`) and adds new ones by this rule (`source = rule`).

## Candidates

All GitHub repositories whose main language is Java and that have at least `--min-stars` stars (default 1,000), plus repositories tagged with Java persistence topics (`spring-boot`, `mybatis`, `mybatis-plus`, `jpa`, `hibernate`, `spring-data-jpa`, `jdbc`) whatever GitHub reports as their main language. Forks are left out. They are ranked by stars, highest first:

```bash
python3 tools/search_candidates.py search --min-stars 1000
python3 tools/search_candidates.py show --from 0 --n 150
```

## Accept a repository when all of these hold

1. **It is an application**: a deployable program with its own business logic and its own data. Web applications, back-end services, servers and platforms count, and so do complete reference applications such as Spring PetClinic or JPetStore. Not applications: libraries, frameworks, SDKs, starters and plugins; engines meant to be embedded (workflow, rules, search); developer tools; Android and desktop apps; games; tutorials, courses, collections of examples or snippets, interview notes; code generators and scaffolds with no domain of their own.
2. **It uses a relational database from Java**, through JDBC, JPA or Hibernate, MyBatis or MyBatis-Plus, JDBI, jOOQ or Spring Data JDBC, with a relational driver or schema (MySQL, PostgreSQL, Oracle, SQL Server, MariaDB, H2, and so on). NoSQL-only applications (MongoDB, Cassandra, Elasticsearch, Redis) do not qualify.
3. **txscan finds at least 10 transactions** in its non-test code, at least one of which writes.
4. **It is not a near-duplicate** of an application already in the corpus: a fork, mirror, rename, edition or variant of the same code base (for example the many RuoYi editions), or a code base whose class names overlap an accepted one by half or more (`tools/near_dupes.py`). Keep the one with more stars; a seed always stays.
5. **It can be pinned and analysed**: the code is public, it clones, and txscan finishes within the probe's time limit (15 minutes by default).

When an application is split over several repositories, take the back-end repository. An archived repository may qualify; say so in the notes.

## Stop

Walk down the ranking and stop when the requested number of new applications (`--add`, default 20) has been accepted. The seeds are kept whatever the rule would say about them.

## Log

Every examined candidate gets a line in `corpus/selection.tsv` (machine-readable) and in `corpus/selection.md` (for people):

| Field | Content |
|---|---|
| `rank` | position in `corpus/candidates.tsv` |
| `repo` | owner/repo |
| `stars` | at search time |
| `decision` | `accept`, `reject`, `duplicate`, `failed` (could not be cloned or analysed), `dropped` (accepted, then left out later, for example by calibration) |
| `reason` | which condition decided it, with the evidence (for example "library: README says 'a lightweight ORM'"; "no relational database: MongoDB only"; "7 transactions") |
| `date` | when it was decided |

`corpus/selection.md` also records the search date, the minimum stars, the queries and the number of candidates (from `corpus/candidates.meta.json`).

## About the seeds

The 20 seeds were chosen in an earlier session with this rule in spirit, but not with this log. They include two kinds of exceptions on purpose: BenchBase, a benchmark harness whose TPC-C, SmallBank and similar procedures are the standard workloads in transaction research, and reference applications (PetClinic, JPetStore, RealWorld) that are small but widely studied.
