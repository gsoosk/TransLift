# Selection log

The rule is in `docs/selection-rule.md`. Seeds are kept as they are; new applications are added by the rule, and every candidate examined is logged below by `tools/corpus.py decide`.

## Seeds (20)

Chosen in an earlier session (October 2026) and pinned to the commits in `corpus/pins/`. Two kinds of exceptions were made on purpose: BenchBase, a benchmark harness whose TPC-C, SmallBank and similar procedures are the standard workloads in transaction research, and small reference applications (Spring PetClinic, JPetStore, RealWorld) that are widely studied.

| Application | Repository | Domain | Persistence |
|---|---|---|---|
| Spring PetClinic | spring-projects/spring-petclinic | Vet clinic (sample app) | Spring Data JPA |
| JPetStore 6 | mybatis/jpetstore-6 | E-commerce (sample app) | MyBatis |
| RealWorld Conduit | gothinkster/spring-boot-realworld-example-app | Blog / social | MyBatis |
| newbee-mall | newbee-ltd/newbee-mall | E-commerce | MyBatis |
| eladmin | elunez/eladmin | Admin platform | Spring Data JPA + MyBatis |
| 12306 | nageoffer/12306 | Train ticketing (microservices) | MyBatis-Plus + Redisson |
| RuoYi-Vue | yangzongzhuan/RuoYi-Vue | Admin platform | MyBatis |
| BenchBase | cmu-db/benchbase | OLTP benchmarks (TPC-C, SmallBank, ...) | JDBC |
| mall | macrozheng/mall | E-commerce | MyBatis |
| Apache Roller | apache/roller | Blog server | JPA (orm.xml, manual tx) |
| Shopizer | shopizer-ecommerce/shopizer | E-commerce | Spring Data JPA |
| litemall | linlinjava/litemall | E-commerce | MyBatis |
| jeecg-boot | jeecgboot/jeecg-boot | Low-code platform | MyBatis-Plus |
| Kill Bill | killbill/killbill | Billing & payments | JDBI (custom tx wrapper) |
| OpenMRS core | openmrs/openmrs-core | Medical records | Hibernate |
| Apache Syncope | apache/syncope | Identity management | JPA / Spring Data |
| Broadleaf Commerce | BroadleafCommerce/BroadleafCommerce | E-commerce | JPA / Hibernate |
| ThingsBoard | thingsboard/thingsboard | IoT platform | Spring Data JPA + JdbcTemplate |
| Apache Fineract | apache/fineract | Core banking | Spring Data JPA + JdbcTemplate |
| DHIS2 core | dhis2/dhis2-core | Health information system | Hibernate + JdbcTemplate |
