#!/usr/bin/env python3
"""txscan: static extraction of database transactions from Java applications.

Syntax-based (tree-sitter) analysis that does not need the project's dependencies:
annotations and library calls are matched by name, project-internal calls are
resolved with declared types plus class-hierarchy analysis (CHA).

Usage: python3 -I txscan.py <app_src_dir> <out_dir> [--app NAME] [--commit SHA] [--rules JSON]
In the kit, run it through tools/run_one.sh <slug>, which passes the app's rules and pinned commit.
"""
import os, re, sys, json, argparse, collections, hashlib, time

from tree_sitter import Language, Parser
import tree_sitter_java

LANG = Language(tree_sitter_java.language())
PARSER = Parser(LANG)

# ----------------------------------------------------------------------------
# Library knowledge
# ----------------------------------------------------------------------------
SPRING_DATA_REPOS = {'Repository', 'CrudRepository', 'PagingAndSortingRepository', 'JpaRepository',
                     'ListCrudRepository', 'ListPagingAndSortingRepository', 'JpaSpecificationExecutor',
                     'QuerydslPredicateExecutor', 'RevisionRepository', 'JpaRepositoryImplementation',
                     'ReactiveCrudRepository', 'R2dbcRepository', 'MongoRepository', 'ElasticsearchRepository',
                     'KeyValueRepository', 'CassandraRepository', 'Neo4jRepository', 'ReactiveNeo4jRepository',
                     'ReactiveMongoRepository', 'CouchbaseRepository'}
NOSQL_REPOS = {'MongoRepository', 'ElasticsearchRepository', 'KeyValueRepository', 'CassandraRepository',
               'Neo4jRepository', 'ReactiveNeo4jRepository', 'ReactiveMongoRepository', 'CouchbaseRepository'}
REPO_WRITE = {'save', 'saveAll', 'saveAndFlush', 'saveAllAndFlush', 'insert', 'delete', 'deleteById',
              'deleteAll', 'deleteAllById', 'deleteInBatch', 'deleteAllInBatch', 'deleteAllByIdInBatch', 'flush'}
REPO_READ = {'findById', 'findAll', 'findAllById', 'getById', 'getOne', 'getReferenceById', 'existsById',
             'count', 'findOne', 'exists', 'findBy', 'findRevisions', 'findLastChangeRevision'}
MP_MAPPER = {'BaseMapper', 'Mapper', 'MPJBaseMapper', 'MPJJoinMapper', 'tk.mybatis.mapper.common.Mapper'}
MP_SERVICE = {'IService', 'ServiceImpl', 'MPJBaseService', 'MPJBaseServiceImpl', 'CrudRepository_MP'}
MP_SERVICE_LIBTX = {'saveBatch', 'saveOrUpdateBatch', 'updateBatchById', 'removeByIds', 'removeBatchByIds'}
EM_TYPES = {'EntityManager'}
SESSION_TYPES = {'Session', 'StatelessSession', 'SessionImplementor', 'SharedSessionContract'}
QUERY_TYPES = {'Query', 'TypedQuery', 'NativeQuery', 'SQLQuery', 'Criteria', 'SelectionQuery', 'MutationQuery',
               'StoredProcedureQuery', 'JPAQuery', 'JPQLQuery', 'HibernateQuery'}
QUERY_BUILDERS = {'createQuery', 'createNamedQuery', 'createNativeQuery', 'createSQLQuery', 'createCriteria',
                  'getNamedQuery', 'createStoredProcedureQuery', 'createSelectionQuery', 'createMutationQuery',
                  'createNamedStoredProcedureQuery', 'getNamedNativeQuery'}
QUERY_TERMINAL_R = {'getResultList', 'getSingleResult', 'getResultStream', 'list', 'uniqueResult',
                    'uniqueResultOptional', 'scroll', 'iterate', 'stream', 'getSingleResultOrNull',
                    'fetch', 'fetchOne', 'fetchFirst', 'fetchCount', 'fetchResults'}
QUERY_TERMINAL_W = {'executeUpdate', 'execute'}
JDBC_TEMPLATE = {'JdbcTemplate', 'NamedParameterJdbcTemplate', 'JdbcOperations', 'NamedParameterJdbcOperations',
                 'JdbcClient'}
JDBC_STMT = {'Statement', 'PreparedStatement', 'CallableStatement'}
EFFECT_TYPES = {
    'RestTemplate': 'http', 'WebClient': 'http', 'HttpClient': 'http', 'CloseableHttpClient': 'http',
    'OkHttpClient': 'http', 'RestClient': 'http', 'HttpURLConnection': 'http', 'AsyncHttpClient': 'http',
    'KafkaTemplate': 'message', 'RabbitTemplate': 'message', 'AmqpTemplate': 'message', 'JmsTemplate': 'message',
    'RocketMQTemplate': 'message', 'StreamBridge': 'message', 'KafkaProducer': 'message', 'Producer': 'message',
    'JavaMailSender': 'email', 'MailSender': 'email', 'JavaMailSenderImpl': 'email',
    'RedisTemplate': 'cache', 'StringRedisTemplate': 'cache', 'ValueOperations': 'cache', 'HashOperations': 'cache',
    'ListOperations': 'cache', 'SetOperations': 'cache', 'ZSetOperations': 'cache', 'Jedis': 'cache',
    'RedissonClient': 'cache', 'RBucket': 'cache', 'RMap': 'cache', 'CacheManager': 'cache', 'Cache': 'cache',
    'RedisCache': 'cache', 'RedisService': 'cache', 'RedisUtil': 'cache', 'RedisUtils': 'cache',
    'DistributedCache': 'cache',
    'ElasticsearchRestTemplate': 'search-index', 'RestHighLevelClient': 'search-index',
    'ElasticsearchClient': 'search-index', 'ElasticsearchOperations': 'search-index',
    'MinioClient': 'object-store', 'AmazonS3': 'object-store', 'S3Client': 'object-store',
    'ApplicationEventPublisher': 'event', 'ApplicationContext': None,
}
NONDET_STATIC = {('UUID', 'randomUUID'), ('Math', 'random'), ('System', 'currentTimeMillis'), ('System', 'nanoTime'),
                 ('LocalDateTime', 'now'), ('LocalDate', 'now'), ('LocalTime', 'now'), ('Instant', 'now'),
                 ('ZonedDateTime', 'now'), ('OffsetDateTime', 'now'), ('Calendar', 'getInstance'),
                 ('ThreadLocalRandom', 'current'), ('DateUtils', 'getNowDate'), ('DateUtil', 'date'),
                 ('DateUtil', 'now'), ('IdUtil', 'getSnowflakeNextId'), ('IdUtil', 'simpleUUID'), ('IdUtil', 'fastUUID'),
                 ('IdWorker', 'getId'), ('IdWorker', 'getIdStr'), ('RandomUtil', 'randomInt'),
                 ('RandomStringUtils', 'randomAlphanumeric'), ('DateTime', 'now'), ('Clock', 'systemUTC')}
NONDET_NEW = {'Random', 'SecureRandom', 'Date', 'Timestamp'}
ASYNC_METHODS = {('ExecutorService', 'submit'), ('ExecutorService', 'execute'), ('Executor', 'execute'),
                 ('ThreadPoolTaskExecutor', 'execute'), ('ThreadPoolTaskExecutor', 'submit'),
                 ('ThreadPoolExecutor', 'execute'), ('ThreadPoolExecutor', 'submit'),
                 ('ScheduledExecutorService', 'schedule'), ('ScheduledExecutorService', 'scheduleAtFixedRate'),
                 ('ScheduledExecutorService', 'scheduleWithFixedDelay'), ('CompletableFuture', 'runAsync'),
                 ('CompletableFuture', 'supplyAsync'), ('TaskExecutor', 'execute'), ('AsyncTaskExecutor', 'submit'),
                 ('TaskScheduler', 'schedule'), ('ForkJoinPool', 'submit'), ('ForkJoinPool', 'execute')}
ASYNC_NAMES = {'runAsync', 'supplyAsync', 'thenApplyAsync', 'thenAcceptAsync', 'thenRunAsync', 'thenComposeAsync'}
MAP_COND_NAMES = {'computeIfAbsent', 'computeIfPresent', 'compute', 'merge'}
OPTIONAL_COND_NAMES = {'ifPresent', 'ifPresentOrElse', 'orElseGet', 'orElseThrow', 'or', 'map', 'flatMap', 'filter'}
STREAM_LOOP_NAMES = {'forEach', 'forEachOrdered', 'map', 'flatMap', 'filter', 'peek', 'reduce', 'collect',
                     'anyMatch', 'allMatch', 'noneMatch', 'removeIf', 'replaceAll', 'computeIfAbsent',
                     'computeIfPresent', 'compute', 'merge', 'mapToObj', 'mapToInt', 'mapToLong', 'sorted',
                     'forEachRemaining', 'toMap', 'groupingBy', 'iterate', 'generate', 'takeWhile', 'dropWhile'}
TX_CALLBACK_APIS = {('TransactionTemplate', 'execute'), ('TransactionTemplate', 'executeWithoutResult'),
                    ('TransactionOperations', 'execute'), ('TransactionOperations', 'executeWithoutResult'),
                    ('TransactionalOperator', 'execute'), ('TransactionalOperator', 'transactional'),
                    ('Jdbi', 'inTransaction'), ('Jdbi', 'useTransaction'), ('Handle', 'inTransaction'),
                    ('Handle', 'useTransaction'), ('EntitySqlDaoTransactionalJdbiWrapper', 'execute'),
                    ('TransactionCallbackWithoutResult', None)}
DECL_TX_ANNOTS = {'Transactional', 'GlobalTransactional', 'TransactionAttribute', 'Transaction'}
ENTRY_METHOD_ANNOTS = {'RequestMapping', 'GetMapping', 'PostMapping', 'PutMapping', 'DeleteMapping',
                       'PatchMapping', 'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'Scheduled',
                       'SchedulerLock', 'KafkaListener', 'RabbitListener', 'JmsListener', 'SqsListener',
                       'StreamListener', 'EventListener', 'TransactionalEventListener', 'MessageMapping',
                       'XxlJob', 'Async'}
ENTRY_CLASS_ANNOTS = {'RocketMQMessageListener', 'KafkaListener', 'RabbitListener'}
WEB_ANNOTS = {'RequestMapping', 'GetMapping', 'PostMapping', 'PutMapping', 'DeleteMapping', 'PatchMapping',
              'GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'MessageMapping'}
LOCK_TYPES = {'Lock', 'ReentrantLock', 'ReadWriteLock', 'ReentrantReadWriteLock', 'StampedLock', 'RLock',
              'RReadWriteLock', 'RFencedLock', 'InterProcessMutex', 'InterProcessLock', 'InterProcessSemaphoreMutex',
              'DistributedLock', 'RedisLock', 'RedisLockRegistry', 'LockRegistry', 'Semaphore', 'RSemaphore',
              'RPermitExpirableSemaphore', 'RCountDownLatch'}
LOCK_GETTERS = {'getLock', 'getFairLock', 'getReadWriteLock', 'getMultiLock', 'getRedLock', 'getSpinLock',
                'getFencedLock', 'obtain', 'readLock', 'writeLock', 'getSemaphore', 'getPermitExpirableSemaphore'}
PRIMITIVES = {'int', 'long', 'short', 'byte', 'char', 'boolean', 'float', 'double', 'void', 'var'}
JAVA_LANG = {'String', 'Object', 'Integer', 'Long', 'Boolean', 'Double', 'Float', 'Short', 'Byte', 'Character',
             'Math', 'System', 'Thread', 'StringBuilder', 'StringBuffer', 'Exception', 'RuntimeException',
             'Iterable', 'Class', 'Enum', 'Number', 'Void', 'Runnable', 'Throwable', 'Error', 'Record'}

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def T(node, src):
    return src[node.start_byte:node.end_byte].decode('utf8', 'replace')

def is_test_path(rel):
    """Exclude test code: test source sets, test/e2e/example modules, and *Test.java files.
    Package directories (below src/main/java) are not used for exclusion."""
    parts = rel.replace('\\', '/').split('/')
    if 'src' in parts[:-1]:
        si = parts.index('src')
        mods = parts[:si]
        nxt = parts[si + 1].lower() if si + 1 < len(parts) - 1 else ''
        if nxt and nxt not in ('main', 'java', 'resources', 'gen', 'generated', 'webapp', 'mybatis'):
            return True
    else:
        mods = parts[:-1]
    for p in mods:
        pl = p.lower()
        if pl in ('test', 'tests', 'it', 'testfixtures', 'testing', 'jmh', 'e2e', 'integration-test',
                  'integration-tests', 'integrationtest', 'test-utils', 'testutils', 'node_modules', 'target',
                  'build', '.git', 'archetype-resources', 'example', 'examples', 'demo', 'demos', 'sample',
                  'samples', 'docs', 'doc', 'msa', 'perf', 'benchmark-tests', 'load-tests', 'loadtest'):
            return True
        if pl.endswith('-test') or pl.endswith('-tests') or pl.startswith('test-') or 'e2e' in pl \
                or pl.endswith('-it') or pl.endswith('-testing') or pl.endswith('-test-utils') \
                or pl.endswith('-example') or pl.endswith('-examples') or pl.endswith('-demo') \
                or pl.endswith('-sample') or pl.endswith('-samples') or 'integration-test' in pl:
            return True
    f = parts[-1]
    return bool(re.search(r'(Test|Tests|IT|TestCase|TestBase|TestUtil|TestUtils|Mock\w*)\.java$', f))

def base_type(t):
    """'Map<String, Foo>[]' -> 'Map'; strip annotations/generics/arrays."""
    if not t:
        return ''
    t = re.sub(r'@[\w.]+(\([^)]*\))?\s*', '', t).strip()
    i = t.find('<')
    if i >= 0:
        t = t[:i]
    t = t.replace('[]', '').replace('...', '').strip()
    if t.startswith('? extends '):
        t = t[10:]
    if t.startswith('? super '):
        t = t[8:]
    return t.strip()

def type_args(t):
    """'A<B, C<D>>' -> ['B', 'C<D>']"""
    if not t or '<' not in t:
        return []
    i = t.find('<')
    depth = 0
    out, cur = [], ''
    for ch in t[i + 1:]:
        if ch == '<':
            depth += 1
            cur += ch
        elif ch == '>':
            if depth == 0:
                out.append(cur.strip())
                break
            depth -= 1
            cur += ch
        elif ch == ',' and depth == 0:
            out.append(cur.strip())
            cur = ''
        else:
            cur += ch
    return [o for o in out if o]

def norm_table(t):
    """Canonical table key: unquoted, schema dropped, lower case (unquoted SQL identifiers are case-insensitive)."""
    t = (t or '').replace('\\', '').strip().strip('`"[]')
    if '.' in t:
        t = t.split('.')[-1].strip('`"[]')
    return t.lower()

def simple(name):
    if not name:
        return ''
    if name.startswith('EXT:'):
        name = name[4:]
    return name.split('.')[-1].split('$')[-1]

def snake(name):
    s = re.sub(r'(?<!^)(?=[A-Z])', '_', name).lower()
    return s

# ----------------------------------------------------------------------------
# Declarations index (pass 1)
# ----------------------------------------------------------------------------
class CU:
    __slots__ = ('rel', 'pkg', 'imports', 'wild', 'static_single', 'static_wild')
    def __init__(self, rel):
        self.rel = rel; self.pkg = ''; self.imports = {}; self.wild = []; self.static_single = {}; self.static_wild = []

class TypeDecl:
    __slots__ = ('fqn', 'name', 'kind', 'cu', 'outer', 'annots', 'supers_raw', 'supers', 'fields', 'methods',
                 'line', 'end', 'tparams', 'is_abstract', 'is_test')
    def __init__(self):
        self.supers = []; self.fields = {}; self.methods = collections.defaultdict(list)

class Field:
    __slots__ = ('name', 'type_raw', 'type', 'annots', 'static', 'final', 'init_parts', 'sqlstmt_parts', 'line')

class Method:
    __slots__ = ('id', 'name', 'cls', 'params', 'ret_raw', 'ret', 'annots', 'mods', 'line', 'end', 'has_body',
                 'varargs', 'file', 'is_ctor', 'pseudo', 'parent', 'ret_parts')
    def label(self):
        return f"{self.cls}.{self.name}"

def parse_annotations(mod_node, src):
    out = []
    if mod_node is None:
        return out
    for c in mod_node.named_children:
        if c.type in ('marker_annotation', 'annotation'):
            nm = c.child_by_field_name('name')
            name = T(nm, src) if nm else ''
            args = c.child_by_field_name('arguments')
            out.append((name.split('.')[-1], name, T(args, src) if args else '', c.start_point[0] + 1))
    return out

def modifiers_of(node, src):
    for c in node.children:
        if c.type == 'modifiers':
            kws = set()
            for k in c.children:
                if k.type not in ('marker_annotation', 'annotation'):
                    kws.add(T(k, src))
            return parse_annotations(c, src), kws
    return [], set()

def expr_parts(node, src, depth=0):
    """Flatten a (string) expression into parts: ('lit', s) | ('ref', name) | ('?', text)."""
    if node is None or depth > 60:
        return [('?', '')]
    t = node.type
    if t == 'string_literal':
        s = T(node, src)
        if s.startswith('"""'):
            s = s[3:-3]
        elif len(s) >= 2:
            s = s[1:-1]
        s = s.replace('\\n', ' ').replace('\\t', ' ').replace('\\"', '"')
        return [('lit', s)]
    if t == 'binary_expression':
        op = node.child_by_field_name('operator')
        if op is not None and T(op, src) == '+':
            # iterative left-spine flattening to avoid deep recursion
            parts = []
            stack = [node]
            seq = []
            while stack:
                n = stack.pop()
                if n.type == 'binary_expression' and n.child_by_field_name('operator') is not None \
                        and T(n.child_by_field_name('operator'), src) == '+':
                    stack.append(n.child_by_field_name('right'))
                    stack.append(n.child_by_field_name('left'))
                else:
                    seq.append(n)
            for n in seq:
                parts.extend(expr_parts(n, src, depth + 1))
            return parts
        return [('?', T(node, src)[:80])]
    if t in ('identifier', 'field_access', 'scoped_identifier'):
        return [('ref', T(node, src))]
    if t == 'parenthesized_expression':
        return expr_parts(node.named_children[0], src, depth + 1) if node.named_children else [('?', '')]
    if t == 'object_creation_expression':
        ty = node.child_by_field_name('type')
        args = node.child_by_field_name('arguments')
        if ty is not None and base_type(T(ty, src)).split('.')[-1] in ('SQLStmt', 'String', 'StringBuilder') \
                and args is not None and args.named_children:
            return expr_parts(args.named_children[0], src, depth + 1)
        return [('?', T(node, src)[:80])]
    if t == 'method_invocation':
        nm = node.child_by_field_name('name')
        name = T(nm, src) if nm else ''
        obj = node.child_by_field_name('object')
        args = node.child_by_field_name('arguments')
        if name in ('concat',) and obj is not None and args is not None and args.named_children:
            return expr_parts(obj, src, depth + 1) + expr_parts(args.named_children[0], src, depth + 1)
        if name in ('trim', 'toUpperCase', 'toLowerCase', 'toString', 'strip', 'intern') and obj is not None:
            return expr_parts(obj, src, depth + 1)
        if name in ('format', 'formatted') and args is not None:
            al = list(args.named_children)
            if name == 'formatted':
                fmt_parts = expr_parts(obj, src, depth + 1) if obj is not None else []
                arg_nodes = al
            else:
                if len(al) > 1 and al[0].type != 'string_literal' and al[1].type == 'string_literal':
                    al = al[1:]
                fmt_parts = expr_parts(al[0], src, depth + 1) if al else []
                arg_nodes = al[1:]
            if fmt_parts and all(k == 'lit' for k, v in fmt_parts):
                fmt = ''.join(v for k, v in fmt_parts)
                pieces = re.split(r'%(?:\d+\$)?[-#+ 0,(]*\d*(?:\.\d+)?[sdfbBhHcxXeEgGo]', fmt)
                out = []
                for i, p in enumerate(pieces):
                    out.append(('lit', p))
                    if i < len(pieces) - 1:
                        out.extend(expr_parts(arg_nodes[i], src, depth + 1) if i < len(arg_nodes) else [('?', '')])
                return out
            out = list(fmt_parts)
            for a in arg_nodes:
                out.extend(expr_parts(a, src, depth + 1))
            return out
        if name == 'join' and args is not None:
            out = []
            for a in args.named_children:
                out.extend(expr_parts(a, src, depth + 1))
                out.append(('lit', ' '))
            return out
        return [('call', T(node, src)[:120])]
    if t == 'ternary_expression':
        a = node.child_by_field_name('consequence'); b = node.child_by_field_name('alternative')
        return expr_parts(a, src, depth + 1) + [('lit', ' ')] + expr_parts(b, src, depth + 1)
    return [('?', T(node, src)[:80])]

class Index:
    def __init__(self, root):
        self.root = root
        self.types = {}           # fqn -> TypeDecl
        self.by_simple = collections.defaultdict(list)
        self.methods = {}         # id -> Method
        self.files = {}           # rel -> CU
        self.subtypes = collections.defaultdict(set)
        self._allsupers = {}
        self._subs_all = {}
        self.parse_errors = 0
        self.n_files = 0
        self.loc = 0

    # --- pass 1 -----------------------------------------------------------
    def index_file(self, rel, src):
        tree = PARSER.parse(src)
        root = tree.root_node
        if root.has_error:
            self.parse_errors += 1
        cu = CU(rel)
        for child in root.children:
            if child.type == 'package_declaration':
                for c in child.named_children:
                    if c.type in ('scoped_identifier', 'identifier'):
                        cu.pkg = T(c, src)
            elif child.type == 'import_declaration':
                txt = T(child, src)
                m = re.match(r'import\s+(static\s+)?([\w.]+?)(\.\*)?\s*;', txt)
                if not m:
                    continue
                is_static, name, star = m.group(1), m.group(2), m.group(3)
                if is_static:
                    if star:
                        cu.static_wild.append(name)
                    else:
                        cls, mem = name.rsplit('.', 1)
                        cu.static_single[mem] = cls
                else:
                    if star:
                        cu.wild.append(name)
                    else:
                        cu.imports[name.split('.')[-1]] = name
        self.files[rel] = cu
        for child in root.children:
            if child.type in ('class_declaration', 'interface_declaration', 'enum_declaration',
                              'record_declaration', 'annotation_type_declaration'):
                self._walk_type(child, src, cu, None)
        return tree

    def _walk_type(self, node, src, cu, outer):
        nm = node.child_by_field_name('name')
        if nm is None:
            return
        name = T(nm, src)
        fqn = f"{outer}.{name}" if outer else (f"{cu.pkg}.{name}" if cu.pkg else name)
        td = TypeDecl()
        td.fqn, td.name, td.kind, td.cu, td.outer = fqn, name, node.type.replace('_declaration', ''), cu, outer
        td.annots, kws = modifiers_of(node, src)
        td.is_abstract = 'abstract' in kws or td.kind == 'interface'
        td.line, td.end = node.start_point[0] + 1, node.end_point[0] + 1
        td.supers_raw = []
        td.is_test = False
        tp = node.child_by_field_name('type_parameters')
        td.tparams = []
        if tp is not None:
            for c in tp.named_children:
                if c.type == 'type_parameter':
                    ident = [x for x in c.named_children if x.type in ('type_identifier', 'identifier')]
                    if ident:
                        td.tparams.append(T(ident[0], src))
        sc = node.child_by_field_name('superclass')
        if sc is not None:
            for c in sc.named_children:
                td.supers_raw.append(T(c, src))
        for c in node.children:
            if c.type in ('super_interfaces', 'extends_interfaces'):
                for tl in c.named_children:
                    if tl.type == 'type_list':
                        for x in tl.named_children:
                            td.supers_raw.append(T(x, src))
                    else:
                        td.supers_raw.append(T(tl, src))
        ifs = node.child_by_field_name('interfaces')
        if ifs is not None and not any(c.type in ('super_interfaces', 'extends_interfaces') for c in node.children):
            for tl in ifs.named_children:
                for x in (tl.named_children if tl.type == 'type_list' else [tl]):
                    td.supers_raw.append(T(x, src))
        self.types[fqn] = td
        self.by_simple[name].append(fqn)
        # record components
        if node.type == 'record_declaration':
            params = node.child_by_field_name('parameters')
            if params is not None:
                for p in params.named_children:
                    if p.type == 'formal_parameter':
                        pt = p.child_by_field_name('type'); pn = p.child_by_field_name('name')
                        if pt is not None and pn is not None:
                            f = Field(); f.name = T(pn, src); f.type_raw = T(pt, src); f.type = None
                            f.annots = []; f.static = False; f.final = True; f.init_parts = None
                            f.sqlstmt_parts = None; f.line = p.start_point[0] + 1
                            td.fields[f.name] = f
        body = node.child_by_field_name('body')
        if body is None:
            return
        members = list(body.named_children)
        # enum body declarations
        extra = []
        for m in members:
            if m.type == 'enum_body_declarations':
                extra.extend(m.named_children)
        members.extend(extra)
        for m in members:
            if m.type in ('field_declaration', 'constant_declaration'):
                annots, kws = modifiers_of(m, src)
                ty = m.child_by_field_name('type')
                tyraw = T(ty, src) if ty is not None else ''
                for d in m.named_children:
                    if d.type != 'variable_declarator':
                        continue
                    dn = d.child_by_field_name('name')
                    if dn is None:
                        continue
                    f = Field(); f.name = T(dn, src); f.type_raw = tyraw; f.type = None; f.annots = annots
                    f.static = 'static' in kws or td.kind == 'interface'
                    f.final = 'final' in kws or td.kind == 'interface'
                    f.init_parts = None; f.sqlstmt_parts = None; f.line = d.start_point[0] + 1
                    val = d.child_by_field_name('value')
                    if val is not None:
                        bt = base_type(tyraw)
                        if bt == 'String' and (f.final or f.static):
                            f.init_parts = expr_parts(val, src)
                        elif bt == 'SQLStmt' and val.type == 'object_creation_expression':
                            f.sqlstmt_parts = expr_parts(val, src)
                    td.fields[f.name] = f
            elif m.type in ('method_declaration', 'constructor_declaration', 'compact_constructor_declaration'):
                self._add_method(m, src, td, cu)
            elif m.type in ('class_declaration', 'interface_declaration', 'enum_declaration',
                            'record_declaration', 'annotation_type_declaration'):
                self._walk_type(m, src, cu, fqn)

    def _add_method(self, m, src, td, cu, pseudo=False, parent=None, name_override=None):
        annots, kws = modifiers_of(m, src)
        nm = m.child_by_field_name('name')
        name = name_override or (T(nm, src) if nm is not None else '<init>')
        if m.type in ('constructor_declaration', 'compact_constructor_declaration'):
            name = '<init>'
        params = []
        varargs = False
        ps = m.child_by_field_name('parameters')
        if ps is not None:
            for p in ps.named_children:
                if p.type == 'formal_parameter':
                    pt = p.child_by_field_name('type'); pn = p.child_by_field_name('name')
                    params.append((T(pn, src) if pn is not None else '_', T(pt, src) if pt is not None else ''))
                elif p.type == 'spread_parameter':
                    varargs = True
                    ty = [c for c in p.named_children if c.type not in ('modifiers', 'variable_declarator')]
                    vd = [c for c in p.named_children if c.type == 'variable_declarator']
                    pname = T(vd[0].child_by_field_name('name'), src) if vd and vd[0].child_by_field_name('name') else '_'
                    params.append((pname, (T(ty[0], src) if ty else '') + '[]'))
        ret = m.child_by_field_name('type')
        line = m.start_point[0] + 1
        mt = Method()
        mt.name = name; mt.cls = td.fqn; mt.params = params; mt.ret_raw = T(ret, src) if ret is not None else ''
        mt.ret = None; mt.annots = annots; mt.mods = kws; mt.line = line; mt.end = m.end_point[0] + 1
        mt.has_body = m.child_by_field_name('body') is not None
        mt.varargs = varargs; mt.file = cu.rel; mt.is_ctor = (name == '<init>'); mt.pseudo = pseudo
        mt.parent = parent
        mt.ret_parts = None
        # String getters such as `String schema() { return SCHEMA; }`: keep the returned expression
        if not params and mt.has_body and base_type(mt.ret_raw) == 'String':
            stmts = [c for c in m.child_by_field_name('body').named_children if 'comment' not in c.type]
            if len(stmts) == 1 and stmts[0].type == 'return_statement' and stmts[0].named_children:
                mt.ret_parts = expr_parts(stmts[0].named_children[0], src)
        mt.id = f"{td.fqn}#{name}/{len(params)}@{line}"
        td.methods[name].append(mt)
        self.methods[mt.id] = mt
        return mt

    # --- type resolution -------------------------------------------------------
    def resolve(self, raw, cu, ctx_cls=None):
        """Resolve a type text to a project FQN, or 'EXT:<name>' for library/unknown types."""
        b = base_type(raw)
        if not b or b in PRIMITIVES:
            return b or None
        if ctx_cls is not None:
            td = self.types.get(ctx_cls)
            if td is not None and b in td.tparams:
                return 'TV:' + b
            # nested types in enclosing chain
            c = ctx_cls
            while c:
                cand = f"{c}.{b}"
                if cand in self.types:
                    return cand
                t = self.types.get(c)
                c = t.outer if t else None
            # member types inherited from supertypes
            for s in self.all_supers(ctx_cls):
                cand = f"{s}.{b}"
                if cand in self.types:
                    return cand
        if '.' in b:
            if b in self.types:
                return b
            first, rest = b.split('.', 1)
            r = self.resolve(first, cu, ctx_cls)
            if r and not r.startswith('EXT:') and not r.startswith('TV:'):
                cand = f"{r}.{rest}"
                if cand in self.types:
                    return cand
            return 'EXT:' + b
        if cu is not None:
            if b in cu.imports:
                q = cu.imports[b]
                return q if q in self.types else 'EXT:' + q
            if cu.pkg and f"{cu.pkg}.{b}" in self.types:
                return f"{cu.pkg}.{b}"
            for w in cu.wild:
                if f"{w}.{b}" in self.types:
                    return f"{w}.{b}"
        if b in JAVA_LANG:
            return 'EXT:java.lang.' + b
        cands = self.by_simple.get(b, [])
        if len(cands) == 1 and cu is not None and not cu.wild:
            # same simple name, unique in project, but not imported -> probably external; keep external
            pass
        if cu is not None and cu.wild:
            for w in cu.wild:
                return 'EXT:' + w + '.' + b
        return 'EXT:' + b

    def finalize(self):
        for td in self.types.values():
            res = []
            for raw in td.supers_raw:
                r = self.resolve(raw, td.cu, td.outer or td.fqn)
                args = [self.resolve(a, td.cu, td.fqn) for a in type_args(raw)]
                res.append((r, args, raw))
                if r and not r.startswith('EXT:'):
                    self.subtypes[r].add(td.fqn)
            td.supers = res
            for f in td.fields.values():
                f.type = self.resolve(f.type_raw, td.cu, td.fqn)
        for m in self.methods.values():
            td = self.types.get(m.cls)
            if td is not None:
                m.ret = self.resolve(m.ret_raw, td.cu, td.fqn) if m.ret_raw else None

    def build_ext_impls(self):
        self.ext_impls = collections.defaultdict(list)
        for td in self.types.values():
            if td.kind == 'interface':
                continue
            for sup in self.all_supers(td.fqn):
                if sup.startswith('EXT:'):
                    sn = simple(sup)
                    if sn in JAVA_LANG or sn in ('Serializable', 'Comparable', 'Cloneable', 'AutoCloseable', 'Closeable'):
                        continue
                    self.ext_impls[sn].append(td.fqn)

    def all_supers(self, fqn):
        """Transitive supertypes: project FQNs and external names (EXT:...)."""
        if fqn in self._allsupers:
            return self._allsupers[fqn]
        self._allsupers[fqn] = []
        out, seen = [], set()
        stack = [fqn]
        while stack:
            c = stack.pop()
            td = self.types.get(c)
            if td is None:
                continue
            for (r, args, raw) in (td.supers if td.supers else [(self.resolve(x, td.cu, td.fqn), [], x) for x in td.supers_raw]):
                if r and r not in seen:
                    seen.add(r)
                    out.append(r)
                    if not r.startswith('EXT:'):
                        stack.append(r)
        self._allsupers[fqn] = out
        return out

    def super_names(self, fqn):
        return {simple(s) for s in self.all_supers(fqn)}

    def all_subtypes(self, fqn):
        if fqn in self._subs_all:
            return self._subs_all[fqn]
        out, seen, stack = [], {fqn}, [fqn]
        while stack:
            c = stack.pop()
            for s in sorted(self.subtypes.get(c, ())):      # sorted: results must not depend on hash order
                if s not in seen:
                    seen.add(s); out.append(s); stack.append(s)
        self._subs_all[fqn] = out
        return out

    def generic_binding(self, fqn, ext_names):
        """Find type args of the first supertype (transitively) whose simple name is in ext_names."""
        stack, seen = [fqn], set()
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            td = self.types.get(c)
            if td is None:
                continue
            for (r, args, raw) in td.supers:
                if r and simple(r) in ext_names:
                    return r, args
                if r and not r.startswith('EXT:'):
                    stack.append(r)
        return None, []

    def field_lookup(self, cls, name):
        c, seen = cls, set()
        chain = [cls]
        while chain:
            c = chain.pop(0)
            if c in seen or c is None:
                continue
            seen.add(c)
            td = self.types.get(c)
            if td is None:
                continue
            if name in td.fields:
                return td.fields[name], c
            for (r, a, raw) in td.supers:
                if r and not r.startswith('EXT:'):
                    chain.append(r)
            if td.outer:
                chain.append(td.outer)
        return None, None

    def find_methods_up(self, cls, name, arity):
        """Declarations of name/arity in cls or its project supertypes (nearest first)."""
        out = []
        order = [cls] + [s for s in self.all_supers(cls) if not s.startswith('EXT:')]
        for c in order:
            td = self.types.get(c)
            if td is None:
                continue
            for m in td.methods.get(name, ()):
                if len(m.params) == arity or (m.varargs and arity >= len(m.params) - 1):
                    out.append(m)
        return out

    def _impl_in(self, cls, name, arity):
        """Concrete implementation visible in class cls: own method, else superclass chain, else default methods."""
        c, seen = cls, set()
        while c and c not in seen:
            seen.add(c)
            td = self.types.get(c)
            if td is None:
                break
            for m in td.methods.get(name, ()):
                if m.has_body and (len(m.params) == arity or (m.varargs and arity >= len(m.params) - 1)):
                    return m
            nxt = None
            for (r, a, raw) in td.supers:
                t2 = self.types.get(r) if r else None
                if t2 is not None and t2.kind in ('class',):
                    nxt = r
                    break
            c = nxt
        for s in self.all_supers(cls):
            td = self.types.get(s)
            if td is not None and td.kind == 'interface':
                for m in td.methods.get(name, ()):
                    if m.has_body and (len(m.params) == arity or (m.varargs and arity >= len(m.params) - 1)):
                        return m
        return None

    def targets(self, cls, name, arity):
        """CHA targets: the implementation each concrete subtype of the receiver type would run."""
        decls = self.find_methods_up(cls, name, arity)
        if not decls:
            return [], []
        tg, seen = [], set()
        self.last_selection = {}
        for s in [cls] + self.all_subtypes(cls):
            td = self.types.get(s)
            if td is None or td.kind == 'interface':
                continue
            if td.is_abstract and s != cls and self.all_subtypes(s):
                continue
            m = self._impl_in(s, name, arity)
            if m is not None:
                self.last_selection.setdefault(m.id, []).append(s)
                if m.id not in seen:
                    tg.append(m); seen.add(m.id)
        if not tg:
            for m in decls:
                if m.has_body:
                    tg.append(m)
                    break
        return decls, tg


# ----------------------------------------------------------------------------
# SQL / JPQL utilities
# ----------------------------------------------------------------------------
SQL_KW_RE = re.compile(r'\b(select|insert|update|delete|merge|replace|upsert)\b', re.I)
TABLE_STOP = {'select', 'where', 'set', 'values', 'value', 'on', 'using', 'join', 'left', 'right', 'inner', 'outer',
              'cross', 'full', 'natural', 'group', 'order', 'having', 'limit', 'offset', 'union', 'for', 'as',
              'lateral', 'unnest', 'dual', 'with', 'when', 'then', 'else', 'end', 'case', 'and', 'or', 'not',
              'into', 'from', 'by', 'distinct', 'all', 'returning', 'ignore', 'only', 'low_priority', 'quick',
              'table', 'if', 'exists', 'fetch', 'first', 'next', 'rows', 'row', 'partition', 'window', 'is', 'null',
              'in', 'like', 'between', 'over', 'filter', 'top', 'straight_join', 'force', 'index', 'use',
              'duplicate', 'key', 'conflict', 'do', 'nothing', 'default', 'true', 'false', 'current_timestamp'}

def norm_sql(s):
    s = re.sub(r'<!\[CDATA\[|\]\]>', ' ', s)
    s = re.sub(r'<[^>]+>', ' ', s)  # strip xml tags
    s = re.sub(r'[#$]\{[^}]*\}', '?', s)
    s = re.sub(r'--[^\n]*', ' ', s)
    s = re.sub(r'/\*.*?\*/', ' ', s, flags=re.S)
    s = s.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
    return re.sub(r'\s+', ' ', s).strip()

IDENT = r'[`"\[]?[A-Za-z_][\w$]*[`"\]]?(?:\.[`"\[]?[A-Za-z_][\w$]*[`"\]]?)?'

def sql_tables(sql):
    """Return (op, tables_read, tables_written, flags) for an SQL text (best effort)."""
    s = norm_sql(sql)
    low = s.lower()
    m = SQL_KW_RE.search(low)
    op = m.group(1) if m else None
    if low.lstrip().startswith('with ') and m:
        # find main statement after CTEs: last top-level verb
        mm = list(SQL_KW_RE.finditer(low))
        op = mm[-1].group(1) if mm else op
    reads, writes = set(), set()
    def clean(t):
        t = t.strip('`"[]')
        if '.' in t:
            t = t.split('.')[-1].strip('`"[]')
        return t
    def grab_list(pos):
        out = []
        rest = s[pos:]
        for seg in re.split(r'\s*,\s*', rest, maxsplit=20):
            mm = re.match(r'\s*(' + IDENT + r')', seg)
            if not mm:
                break
            tok = mm.group(1)
            if tok.lower() in TABLE_STOP or tok == '?':
                break
            out.append(clean(tok))
            # stop if the segment contains a keyword that ends the from-list
            tail = seg[mm.end():].lower()
            if re.search(r'\b(where|join|group|order|limit|having|union|set|on|values|for|left|right|inner|cross)\b', tail):
                break
        return out
    for mm in re.finditer(r'\bfrom\s+', low):
        # skip 'delete from' handled as write
        before = low[max(0, mm.start() - 12):mm.start()]
        lst = grab_list(mm.end())
        if re.search(r'\bdelete\s+$', before):
            writes.update(lst)
        else:
            reads.update(lst)
    for mm in re.finditer(r'\bjoin\s+(' + IDENT + ')', s, flags=re.I):
        t = mm.group(1)
        if t.lower() not in TABLE_STOP:
            reads.add(clean(t))
    for mm in re.finditer(r'\binto\s+(' + IDENT + ')', s, flags=re.I):
        t = mm.group(1)
        if t.lower() not in TABLE_STOP:
            writes.add(clean(t))
    for mm in re.finditer(r'\bupdate\s+(' + IDENT + ')', s, flags=re.I):
        before = low[max(0, mm.start() - 12):mm.start()]
        if 'key ' in before or 'do ' in before or 'for ' in before:
            continue
        t = mm.group(1)
        if t.lower() not in TABLE_STOP:
            writes.add(clean(t))
    for mm in re.finditer(r'\b(?:merge|replace)\s+into\s+(' + IDENT + ')', s, flags=re.I):
        writes.add(clean(mm.group(1)))
    flags = []
    if re.search(r'\bfor\s+update\b|\bfor\s+share\b|lock\s+in\s+share\s+mode|\bfor\s+no\s+key\s+update\b|with\s*\(\s*updlock', low):
        flags.append('for_update')
    if re.search(r'\bset\b[^;]*\bversion\s*=\s*version\s*\+\s*1|\bwhere\b[^;]*\bversion\s*=\s*\?', low):
        flags.append('version_check')
    if op in ('select',) and writes:
        # e.g. "insert into x select ..." caught by SQL_KW_RE as select? recompute
        op = 'insert' if 'insert' in low else op
    # names defined by WITH clauses are not tables: WITH w AS (...), u AS (UPDATE ...) SELECT ...
    ctes = set()
    if re.search(r'\bwith\b', low):
        for cm in re.finditer(r'(?:\bwith\s+(?:recursive\s+)?|,\s*)([a-z_]\w*)\s*(?:\([^()]*\)\s*)?as\s*(?:not\s+)?(?:materialized\s+)?\(', low):
            ctes.add(cm.group(1))
    reads = {r for r in reads if r and r.lower() not in TABLE_STOP and not r.startswith('?') and r.lower() not in ctes}
    writes = {w for w in writes if w and w.lower() not in TABLE_STOP and not w.startswith('?') and w.lower() not in ctes}
    if op in ('update', 'delete', 'insert', 'merge', 'replace', 'upsert'):
        reads -= writes
    return op, sorted(reads), sorted(writes), flags

def jpql_entities(q):
    """Entity names referenced in a JPQL/HQL query (FROM/JOIN/UPDATE/DELETE)."""
    s = re.sub(r'\s+', ' ', q)
    low = s.lower().strip()
    op = None
    for kw in ('select', 'update', 'delete', 'insert', 'from'):
        if low.startswith(kw):
            op = 'select' if kw == 'from' else kw
            break
    if op is None:
        m = SQL_KW_RE.search(low)
        op = m.group(1).lower() if m else 'select'
    names = []
    for mm in re.finditer(r'\b(?:from|join|update|into|fetch)\s+([A-Za-z_][\w.$]*)', s, flags=re.I):
        n = mm.group(1)
        if '.' in n and n[0].islower():
            last = n.split('.')[-1]
            if not (n.count('.') >= 2 and last[:1].isupper()):
                continue  # association path like o.pets (but keep fully qualified names like org.x.Order)
        if n.lower() in TABLE_STOP and (n.islower() or n.isupper()):   # keywords, but keep entities like Group, Order
            continue
        names.append(n.split('.')[-1])
    # comma-separated additional roots: "from A a, B b"
    for mm in re.finditer(r'\bfrom\s+[A-Za-z_][\w.]*\s+(?:as\s+)?\w+\s*,\s*([A-Z][\w]*)', s):
        n = mm.group(1)
        if n.lower() in TABLE_STOP and (n.islower() or n.isupper()):
            continue    # e.g. "from Holiday h, IN(h.offices) o" declares a collection member, not an entity
        names.append(n)
    return op, names

# ----------------------------------------------------------------------------
# MyBatis XML and Kill Bill StringTemplate files
# ----------------------------------------------------------------------------
MB_STMT_RE = re.compile(r'<(select|insert|update|delete)\b([^>]*)>(.*?)</\1\s*>', re.S | re.I)
MB_SQL_RE = re.compile(r'<sql\b[^>]*\bid\s*=\s*"([^"]+)"[^>]*>(.*?)</sql\s*>', re.S | re.I)
MB_INCLUDE_RE = re.compile(r'<include\b[^>]*\brefid\s*=\s*"([^"]+)"[^>]*/?>', re.I)

def parse_mybatis(root, files):
    mappers = {}  # namespace -> {id: (kind, sql)}
    fragments = {}
    raw = []
    for rel in files:
        try:
            with open(os.path.join(root, rel), 'r', encoding='utf8', errors='replace') as fh:
                x = fh.read()
        except Exception:
            continue
        m = re.search(r'<mapper\b[^>]*\bnamespace\s*=\s*"([^"]+)"', x)
        if not m:
            continue
        ns = m.group(1)
        raw.append((ns, x, rel))
        for fm in MB_SQL_RE.finditer(x):
            fragments[(ns, fm.group(1))] = fm.group(2)
            fragments[('*', fm.group(1))] = fm.group(2)
    for ns, x, rel in raw:
        stmts = mappers.setdefault(ns, {})
        for sm in MB_STMT_RE.finditer(x):
            kind = sm.group(1).lower()
            attrs = sm.group(2)
            idm = re.search(r'\bid\s*=\s*"([^"]+)"', attrs)
            if not idm:
                continue
            body = sm.group(3)
            for _ in range(3):
                def rep(mm):
                    ref = mm.group(1)
                    if '.' in ref:
                        n2, r2 = ref.rsplit('.', 1)
                        return fragments.get((n2, r2), fragments.get(('*', r2), ' '))
                    return fragments.get((ns, ref), fragments.get(('*', ref), ' '))
                body = MB_INCLUDE_RE.sub(rep, body)
            stmts[idm.group(1)] = (kind, body, rel)
    return mappers

def parse_jpa_xml(root, files):
    """JPA orm.xml and Hibernate hbm.xml: entity->table and named queries."""
    ents, named = {}, {}
    for rel in files:
        try:
            with open(os.path.join(root, rel), 'r', encoding='utf8', errors='replace') as fh:
                x = fh.read()
        except Exception:
            continue
        if '<entity' not in x and '<hibernate-mapping' not in x and '<named-query' not in x:
            continue
        pkg = ''
        pm = re.search(r'<(?:hibernate-mapping|entity-mappings)\b[^>]*\bpackage\s*=\s*"([^"]+)"', x)
        if pm:
            pkg = pm.group(1)
        pm2 = re.search(r'<package>\s*([\w.]+)\s*</package>', x)
        if pm2:
            pkg = pm2.group(1)
        for em in re.finditer(r'<entity\s([^>]*)>(.*?)</entity>', x, re.S):
            cm = re.search(r'\bclass\s*=\s*"([^"]+)"', em.group(1))
            if not cm:
                continue
            cls = cm.group(1)
            if '.' not in cls and pkg:
                cls = pkg + '.' + cls
            tm = re.search(r'<table\b[^>]*\bname\s*=\s*"([^"]+)"', em.group(2))
            nm = re.search(r'\bname\s*=\s*"([^"]+)"', em.group(1))
            ents[cls] = {'table': tm.group(1) if tm else cls.split('.')[-1], 'name': nm.group(1) if nm else cls.split('.')[-1],
                         'versioned': '<version' in em.group(2), 'table_declared': bool(tm)}
            im = re.search(r'<inheritance\b([^>]*)/?>', em.group(2))
            if im:
                sm = re.search(r'\bstrategy\s*=\s*"(\w+)"', im.group(1))
                ents[cls]['inheritance'] = sm.group(1).upper() if sm else 'SINGLE_TABLE'
        for cm in re.finditer(r'<(class|joined-subclass|subclass|union-subclass)\b([^>]*)>', x):
            a = cm.group(2)
            nmm = re.search(r'\bname\s*=\s*"([^"]+)"', a)
            if not nmm:
                continue
            cls = nmm.group(1)
            if '.' not in cls and pkg:
                cls = pkg + '.' + cls
            tm = re.search(r'\btable\s*=\s*"([^"]+)"', a)
            e = ents.setdefault(cls, {'table': tm.group(1) if tm else cls.split('.')[-1], 'name': cls.split('.')[-1],
                                      'versioned': False, 'table_declared': bool(tm)})
            if cm.group(1) != 'class':      # Hibernate declares the mapping strategy on each subclass
                e['hbm_subclass'] = {'subclass': 'SINGLE_TABLE', 'joined-subclass': 'JOINED',
                                     'union-subclass': 'TABLE_PER_CLASS'}[cm.group(1)]
        for qm in re.finditer(r'<named-query\b[^>]*\bname\s*=\s*"([^"]+)"[^>]*>\s*<query>(.*?)</query>', x, re.S):
            named[qm.group(1).replace('&amp;', '&')] = ('jpql', re.sub(r'<!\[CDATA\[|\]\]>', ' ', qm.group(2)).replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&'))
        for qm in re.finditer(r'<named-native-query\b[^>]*\bname\s*=\s*"([^"]+)"[^>]*>.*?<query>(.*?)</query>', x, re.S):
            named[qm.group(1)] = ('sql', re.sub(r'<!\[CDATA\[|\]\]>', ' ', qm.group(2)))
        for qm in re.finditer(r'<query\b[^>]*\bname\s*=\s*"([^"]+)"[^>]*>(.*?)</query>', x, re.S):
            named.setdefault(qm.group(1), ('jpql', re.sub(r'<!\[CDATA\[|\]\]>', ' ', qm.group(2))))
        for qm in re.finditer(r'<sql-query\b[^>]*\bname\s*=\s*"([^"]+)"[^>]*>(.*?)</sql-query>', x, re.S):
            named[qm.group(1)] = ('sql', re.sub(r'<[^>]+>|<!\[CDATA\[|\]\]>', ' ', qm.group(2)))
    return ents, named

def parse_stg(root, files):
    out = {}
    for rel in files:
        try:
            with open(os.path.join(root, rel), 'r', encoding='utf8', errors='replace') as fh:
                x = fh.read()
        except Exception:
            continue
        name = os.path.basename(rel).replace('.sql.stg', '').replace('.stg', '')
        tm = re.search(r'tableName\(\)\s*::=\s*"([^"]+)"', x)
        hm = re.search(r'historyTableName\(\)\s*::=\s*"([^"]+)"', x)
        out[name] = {'table': tm.group(1) if tm else None, 'history': hm.group(1) if hm else None}
    return out


# ----------------------------------------------------------------------------
# Pass 2: method bodies
# ----------------------------------------------------------------------------
LOOP_NODES = {'for_statement', 'enhanced_for_statement', 'while_statement', 'do_statement'}
# operations that have no table of their own (not counted as "unknown table")
NO_TABLE_FLAGS = {'flush', 'advisory_lock', 'ddl_or_admin', 'sequence'}

class BodyScanner:
    def __init__(self, idx, entities, mappers, stg, app_rules):
        self.idx = idx
        self.entities = entities          # fqn -> {'name','table'}
        self.entity_by_name = {}
        for f, e in entities.items():
            self.entity_by_name.setdefault(e['name'], f)
            self.entity_by_name.setdefault(simple(f), f)
        self.mappers = mappers
        self.stg = stg
        self.rules = app_rules
        self.calls = collections.defaultdict(list)      # method id -> list of call records
        self.callbacks = collections.defaultdict(list)  # method id -> list of (pseudo id, mode, site)
        self.pseudo_methods = {}
        self.sync_methods = set()
        self.region_roots = {}            # method id -> list of (what, line)
        self.callback_roots = {}          # pseudo id -> info
        self.wrapper_params = {}          # method id -> param index used as tx callback
        self.lock_sites = []
        self.const_cache = {}

    def entity_for_name(self, n):
        """Entity for a JPQL name: an entity name, or a mapped interface/superclass with exactly one entity."""
        f = self.entity_by_name.get(n)
        if f is not None:
            return f
        if n in self.idx.types:                       # fully qualified
            return self.as_entity(n)
        cands = {self.as_entity(fq) for fq in self.idx.by_simple.get(n.split('.')[-1], [])}
        cands.discard(None)
        return cands.pop() if len(cands) == 1 else None

    # --- constants ------------------------------------------------------------
    def resolve_ref(self, name, cls, depth=0):
        if depth > 6:
            return None
        key = (name, cls)
        if key in self.const_cache:
            return self.const_cache[key]
        self.const_cache[key] = None
        td = self.idx.types.get(cls)
        val = None
        if '.' in name:
            head, tail = name.rsplit('.', 1)
            r = self.idx.resolve(head, td.cu if td else None, cls)
            if r and not r.startswith('EXT:') and not r.startswith('TV:'):
                f, owner = self.idx.field_lookup(r, tail)
                if f is not None and (f.init_parts or f.sqlstmt_parts):
                    val = self.join_parts(f.init_parts or f.sqlstmt_parts, owner, depth + 1)
        else:
            f, owner = self.idx.field_lookup(cls, name)
            if f is not None and (f.init_parts or f.sqlstmt_parts):
                val = self.join_parts(f.init_parts or f.sqlstmt_parts, owner, depth + 1)
            elif td is not None and name in td.cu.static_single:
                ocls = self.idx.resolve(td.cu.static_single[name], td.cu, cls)
                if ocls and not ocls.startswith('EXT:'):
                    f, owner = self.idx.field_lookup(ocls, name)
                    if f is not None and f.init_parts:
                        val = self.join_parts(f.init_parts, owner, depth + 1)
            elif td is not None:
                for w in td.cu.static_wild:       # import static a.b.Constants.*;
                    ocls = w if w in self.idx.types else self.idx.resolve(w, td.cu, cls)
                    if ocls and not ocls.startswith('EXT:'):
                        f, owner = self.idx.field_lookup(ocls, name)
                        if f is not None and f.init_parts:
                            val = self.join_parts(f.init_parts, owner, depth + 1)
                            break
        self.const_cache[key] = val
        return val

    def join_parts(self, parts, cls, depth=0, local_strings=None, env=None, src=None):
        out = []
        for kind, v in parts:
            if kind == 'lit':
                out.append(v)
            elif kind == 'ref':
                if local_strings is not None and v in local_strings:
                    out.append(local_strings[v])
                    continue
                if env is not None and v in env['strings']:
                    s = self.local_string(v, env['strings'], src, cls, env=env)
                    if s:
                        out.append(s)
                        continue
                r = self.resolve_ref(v, cls, depth)
                out.append(r if r is not None else ' ?ref ')
            elif kind == 'call':
                r = self.call_string(v, cls, env, depth)
                out.append(r if r is not None else ' ? ')
            else:
                out.append(' ? ')
        return ''.join(out)

    def const_expr(self, expr, cls):
        """Value of a compile-time String expression from annotation source text (literals, text blocks,
        concatenation, constants, simple getters), or None when some part cannot be resolved."""
        expr = (expr or '').strip()
        if not expr:
            return None
        code = ('class C__ { String s__ = ' + expr + '; }').encode('utf8')
        root = PARSER.parse(code).root_node
        val, stack = None, [root]
        while stack:
            n = stack.pop()
            if n.type == 'variable_declarator':
                val = n.child_by_field_name('value')
                break
            stack.extend(n.named_children)
        if val is None:
            return None
        out = []
        for k, v in expr_parts(val, code):
            if k == 'lit':
                out.append(v)
            elif k == 'ref':
                r = self.resolve_ref(v, cls)
                if r is None or '?ref' in r:
                    return None
                out.append(r)
            elif k == 'call':
                r = self.call_string(v, cls)
                if r is None or '?ref' in r:
                    return None
                out.append(r)
            else:
                return None
        return ''.join(out)

    def call_string(self, text, cls, env=None, depth=0):
        """Value of a String-valued call inside a query expression: X.class.getSimpleName(), or a zero-argument
        getter whose body is `return <constant expression>;` (e.g. Fineract's rowMapper.schema())."""
        if depth > 6 or not text:
            return None
        m = re.fullmatch(r'\s*([\w.]+)\.class\s*\.\s*get(Simple)?Name\s*\(\s*\)\s*', text)
        if m:
            return m.group(1).split('.')[-1]
        m = re.fullmatch(r'\s*(?:(this|[A-Za-z_$][\w$]*)\s*\.\s*)?([A-Za-z_$][\w$]*)\s*\(\s*\)\s*', text)
        if not m:
            return None
        recv, name = m.group(1), m.group(2)
        td = self.idx.types.get(cls)
        rcls = None
        if recv in (None, 'this'):
            rcls = cls
        else:
            lt = env['locals'].get(recv) if env is not None else None
            if isinstance(lt, str):
                rcls = lt
            else:
                f, owner = self.idx.field_lookup(cls, recv)
                if f is not None:
                    rcls = f.type
                elif td is not None:
                    rcls = self.idx.resolve(recv, td.cu, cls)     # static call on a type
        if not rcls or rcls.startswith('EXT:') or rcls.startswith('TV:') or rcls not in self.idx.types:
            return None
        cands = [mm for mm in self.idx.find_methods_up(rcls, name, 0) if mm.ret_parts]
        if not cands:
            # implementations in subtypes of an interface receiver, when they all agree
            vals = set()
            for s in self.idx.all_subtypes(rcls):
                mm = self.idx._impl_in(s, name, 0)
                if mm is not None and mm.ret_parts:
                    vals.add(self.join_parts(mm.ret_parts, mm.cls, depth + 1))
            return vals.pop() if len(vals) == 1 else None
        mm = cands[0]
        return self.join_parts(mm.ret_parts, mm.cls, depth + 1)

    # --- per file -------------------------------------------------------------
    def scan_file(self, rel, src, tree):
        cu = self.idx.files[rel]
        root = tree.root_node
        for child in root.children:
            if child.type in ('class_declaration', 'interface_declaration', 'enum_declaration',
                              'record_declaration', 'annotation_type_declaration'):
                self._scan_type(child, src, cu, None)

    def _scan_type(self, node, src, cu, outer):
        nm = node.child_by_field_name('name')
        if nm is None:
            return
        name = T(nm, src)
        fqn = f"{outer}.{name}" if outer else (f"{cu.pkg}.{name}" if cu.pkg else name)
        td = self.idx.types.get(fqn)
        if td is None:
            return
        body = node.child_by_field_name('body')
        if body is None:
            return
        members = list(body.named_children)
        for m in list(members):
            if m.type == 'enum_body_declarations':
                members.extend(m.named_children)
        for m in members:
            if m.type in ('method_declaration', 'constructor_declaration', 'compact_constructor_declaration'):
                line = m.start_point[0] + 1
                mname = '<init>' if m.type != 'method_declaration' else T(m.child_by_field_name('name'), src)
                cands = [x for x in td.methods.get(mname, []) if x.line == line]
                if not cands:
                    continue
                meth = cands[0]
                b = m.child_by_field_name('body')
                if b is not None:
                    self._scan_body(meth, b, src, cu, td)
                if 'synchronized' in meth.mods:
                    self.sync_methods.add(meth.id)
            elif m.type in ('class_declaration', 'interface_declaration', 'enum_declaration',
                            'record_declaration', 'annotation_type_declaration'):
                self._scan_type(m, src, cu, fqn)
            elif m.type == 'field_declaration':
                # lambdas / anonymous classes in field initializers are ignored (rare in tx code)
                pass

    # --- locals ------------------------------------------------------------------
    def _collect_locals(self, body, src, meth, cu, td):
        types, strings, lambdas = {}, {}, {}
        for (pn, pt) in meth.params:
            types[pn] = self.idx.resolve(pt, cu, td.fqn)
        stack = [body]
        while stack:
            n = stack.pop()
            t = n.type
            if t == 'local_variable_declaration':
                ty = n.child_by_field_name('type')
                tyraw = T(ty, src) if ty is not None else ''
                for d in n.named_children:
                    if d.type != 'variable_declarator':
                        continue
                    dn = d.child_by_field_name('name')
                    if dn is None:
                        continue
                    vn = T(dn, src)
                    val = d.child_by_field_name('value')
                    if base_type(tyraw) == 'var' and val is not None:
                        types[vn] = ('INFER', val)
                    else:
                        types[vn] = self.idx.resolve(tyraw, cu, td.fqn)
                    if val is not None:
                        if base_type(tyraw) in ('String', 'var', 'StringBuilder', 'StringBuffer'):
                            strings.setdefault(vn, []).append(val)
                        elif val.type == 'method_invocation':
                            strings.setdefault('__init__' + vn, []).append(val)
                        if val.type in ('lambda_expression', 'method_reference'):
                            lambdas[vn] = val
                        elif val.type == 'object_creation_expression' and val.child_by_field_name('body') is None \
                                and any(c.type == 'class_body' for c in val.children):
                            lambdas[vn] = val
                        elif val.type == 'object_creation_expression' and any(c.type == 'class_body' for c in val.children):
                            lambdas[vn] = val
            elif t in ('enhanced_for_statement',):
                ty = n.child_by_field_name('type'); nmn = n.child_by_field_name('name')
                if ty is not None and nmn is not None:
                    types[T(nmn, src)] = self.idx.resolve(T(ty, src), cu, td.fqn)
            elif t == 'catch_formal_parameter':
                nmn = n.child_by_field_name('name')
                ct = [c for c in n.named_children if c.type == 'catch_type']
                if nmn is not None and ct:
                    types[T(nmn, src)] = self.idx.resolve(T(ct[0], src).split('|')[0], cu, td.fqn)
            elif t == 'resource':
                ty = n.child_by_field_name('type'); nmn = n.child_by_field_name('name')
                val = n.child_by_field_name('value')
                if nmn is not None:
                    vn = T(nmn, src)
                    if ty is not None and base_type(T(ty, src)) != 'var':
                        types[vn] = self.idx.resolve(T(ty, src), cu, td.fqn)
                    elif val is not None:
                        types[vn] = ('INFER', val)
                    if val is not None and val.type == 'method_invocation':
                        strings.setdefault('__init__' + vn, []).append(val)
            elif t == 'formal_parameter':
                ty = n.child_by_field_name('type'); nmn = n.child_by_field_name('name')
                if ty is not None and nmn is not None:
                    types.setdefault(T(nmn, src), self.idx.resolve(T(ty, src), cu, td.fqn))
            elif t == 'assignment_expression':
                left = n.child_by_field_name('left'); right = n.child_by_field_name('right')
                if left is not None and left.type == 'identifier' and right is not None:
                    vn = T(left, src)
                    strings.setdefault(vn, []).append(right)
                    if right.type == 'method_invocation':
                        strings.setdefault('__init__' + vn, []).append(right)
                    if right.type in ('lambda_expression', 'method_reference'):
                        lambdas[vn] = right
            elif t == 'method_invocation':
                nm2 = n.child_by_field_name('name'); obj = n.child_by_field_name('object')
                if nm2 is not None and T(nm2, src) == 'append' and obj is not None and obj.type == 'identifier':
                    args = n.child_by_field_name('arguments')
                    if args is not None and args.named_children:
                        strings.setdefault(T(obj, src), []).append(args.named_children[0])
            for c in n.named_children:
                if c.type in ('class_body',):
                    continue
                stack.append(c)
        return types, strings, lambdas

    def local_string(self, name, strings, src, cls, depth=0, env=None):
        if depth > 3 or name not in strings:
            return None
        out = []
        for node in strings[name]:
            parts = expr_parts(node, src)
            sub = []
            for k, v in parts:
                if k == 'lit':
                    sub.append(v)
                elif k == 'ref':
                    if v in strings and v != name:
                        r = self.local_string(v, strings, src, cls, depth + 1, env=env)
                        sub.append(r if r else ' ? ')
                    else:
                        r = self.resolve_ref(v, cls)
                        sub.append(r if r is not None else ' ? ')
                elif k == 'call':
                    r = self.call_string(v, cls, env)
                    sub.append(r if r is not None else ' ? ')
                else:
                    sub.append(' ? ')
            out.append(''.join(sub))
        return ' '.join(out)

    # --- generic type arguments -----------------------------------------------------
    def bind_tvar(self, tvar, decl_cls, use_cls):
        """Binding of type variable tvar (declared by decl_cls) as seen from subclass use_cls, or None."""
        if decl_cls == use_cls:
            return None
        stack, seen = [(use_cls, {})], set()
        while stack:
            c, sub = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            td = self.idx.types.get(c)
            if td is None:
                continue
            for (r, args, raw) in td.supers:
                if not r or r.startswith('EXT:'):
                    continue
                rtd = self.idx.types.get(r)
                if rtd is None:
                    continue
                m = {}
                for i, tp in enumerate(rtd.tparams):
                    if i < len(args):
                        a = args[i]
                        if a and a.startswith('TV:'):
                            a = sub.get(a[3:])
                        m[tp] = a
                if r == decl_cls:
                    return m.get(tvar)
                stack.append((r, m))
        return None

    def decl_type_entity(self, type_raw, decl_cls, use_cls):
        """Entity named by the type arguments of a declared type, e.g. JpaRepository<E, UUID> with E bound in use_cls."""
        targs = type_args(type_raw or '')
        if not targs:
            return None
        dtd = self.idx.types.get(decl_cls)
        if dtd is None:
            return None
        for a in targs[:2]:
            r = self.idx.resolve(a, dtd.cu, decl_cls)
            if r and r.startswith('TV:'):
                r = self.bind_tvar(r[3:], decl_cls, use_cls) if use_cls else None
            e = self.as_entity(r) if r else None
            if e:
                return e
        return None

    def recv_decl_entity(self, obj, src, env):
        """Entity bound by the declared generic type of a receiver expression (field or getter result)."""
        if obj is None:
            return None
        use = env['td'].fqn
        if obj.type == 'identifier' or (obj.type == 'field_access' and obj.child_by_field_name('object') is not None
                                        and obj.child_by_field_name('object').type == 'this'):
            n = T(obj, src).split('.')[-1]
            if n in env['locals']:
                return None
            f, owner = self.idx.field_lookup(use, n)
            if f is not None:
                return self.decl_type_entity(f.type_raw, owner, use)
            return None
        if obj.type == 'method_invocation':
            return env.setdefault('_ret_ent', {}).get((obj.start_byte, obj.end_byte))
        return None

    # --- expression typing ------------------------------------------------------
    def expr_type(self, node, src, env, depth=0):
        """Returns a type token: project FQN, 'EXT:Name', special 'QUERY'/'SESSION'/..., or None.
        env: dict with meth, cu, td, locals, strings, sqlhint"""
        if node is None or depth > 12:
            return None
        t = node.type
        idx = self.idx
        if t == 'identifier':
            n = T(node, src)
            lt = env['locals'].get(n)
            if lt is not None:
                if isinstance(lt, tuple):
                    return self.expr_type(lt[1], src, env, depth + 1)
                return lt
            f, owner = idx.field_lookup(env['td'].fqn, n)
            if f is not None:
                return f.type
            r = idx.resolve(n, env['cu'], env['td'].fqn)
            if r and n[:1].isupper():
                return ('STATIC', r)
            return None
        if t == 'this':
            return env['td'].fqn
        if t == 'super':
            for (r, a, raw) in env['td'].supers:
                return r
            return None
        if t == 'field_access':
            obj = node.child_by_field_name('object'); fld = node.child_by_field_name('field')
            fname = T(fld, src) if fld is not None else ''
            if obj is not None and obj.type == 'this':
                f, owner = idx.field_lookup(env['td'].fqn, fname)
                return f.type if f is not None else None
            ot = self.expr_type(obj, src, env, depth + 1)
            if isinstance(ot, tuple) and ot[0] == 'STATIC':
                ot = ot[1]
            if ot and not ot.startswith('EXT:') and ot in idx.types:
                f, owner = idx.field_lookup(ot, fname)
                if f is not None:
                    return f.type
            # qualified static reference like Foo.Bar (type)
            r = idx.resolve(T(node, src), env['cu'], env['td'].fqn)
            if r and fname[:1].isupper():
                return ('STATIC', r)
            return None
        if t == 'object_creation_expression':
            ty = node.child_by_field_name('type')
            return idx.resolve(T(ty, src), env['cu'], env['td'].fqn) if ty is not None else None
        if t == 'cast_expression':
            ty = node.child_by_field_name('type')
            return idx.resolve(T(ty, src), env['cu'], env['td'].fqn) if ty is not None else None
        if t == 'parenthesized_expression':
            return self.expr_type(node.named_children[0], src, env, depth + 1) if node.named_children else None
        if t in ('string_literal', 'text_block'):
            return 'EXT:java.lang.String'
        if t == 'array_access':
            a = node.child_by_field_name('array')
            return self.expr_type(a, src, env, depth + 1)
        if t == 'ternary_expression':
            return self.expr_type(node.child_by_field_name('consequence'), src, env, depth + 1)
        if t == 'method_invocation':
            info = self.call_info(node, src, env, depth + 1)
            return info.get('ret')
        return None

    def recv_entity(self, rtype):
        """Entity bound by generics on a project receiver type (repositories, mappers, generic stores)."""
        if not rtype or rtype.startswith('EXT:') or rtype not in self.idx.types:
            return None
        stack, seen = [rtype], set()
        while stack:
            c = stack.pop()
            if c in seen:
                continue
            seen.add(c)
            td = self.idx.types.get(c)
            if td is None:
                continue
            for (r, args, raw) in td.supers:
                for a in args:
                    if a in self.entities:
                        return a
                if r and not r.startswith('EXT:'):
                    stack.append(r)
        return None

    def call_info(self, node, src, env, depth=0):
        """Resolve a method_invocation: returns dict with classification and return type."""
        idx = self.idx
        nm = node.child_by_field_name('name')
        name = T(nm, src) if nm is not None else ''
        obj = node.child_by_field_name('object')
        args_node = node.child_by_field_name('arguments')
        args = list(args_node.named_children) if args_node is not None else []
        arity = len(args)
        td = env['td']
        info = {'name': name, 'arity': arity}
        # receiver type
        rkind = 'expr'
        if obj is None:
            rkind = 'implicit'
            rtype = td.fqn
            # outer classes / static imports
            if not idx.find_methods_up(td.fqn, name, arity):
                c = td.outer
                found = None
                while c:
                    if idx.find_methods_up(c, name, arity):
                        found = c
                        break
                    t2 = idx.types.get(c)
                    c = t2.outer if t2 else None
                if found:
                    rtype = found
                elif name in td.cu.static_single:
                    rtype = idx.resolve(td.cu.static_single[name], td.cu, td.fqn)
                    rkind = 'static'
        elif obj.type == 'this':
            rkind = 'this'; rtype = td.fqn
        elif obj.type == 'super':
            rkind = 'super'
            rtype = td.supers[0][0] if td.supers else None
        else:
            rt = self.expr_type(obj, src, env, depth + 1)
            if isinstance(rt, tuple):
                rkind = 'static'; rtype = rt[1]
            else:
                rtype = rt
        info['rkind'] = rkind
        info['rtype'] = rtype
        rs = simple(rtype) if rtype else ''
        rsupers = idx.super_names(rtype) if rtype and rtype in idx.types else set()
        rnames = {rs} | rsupers
        # for super.m(...) the generic binding (entity) comes from the calling class, e.g. X extends ServiceImpl<M, E>
        ent_type = td.fqn if rkind == 'super' else rtype
        # ---------- typed factories: become/getMapper/onDemand/attach(X.class) ----------
        if name in ('become', 'getMapper', 'onDemand', 'attach', 'getBean', 'getDao', 'getRepository', 'getService') and args \
                and args[0].type == 'class_literal':
            ty = T(args[0], src)[:-len('.class')]
            info['ret'] = idx.resolve(ty, env['cu'], td.fqn)
            info['cls'] = 'lib'
            return info
        # ---------- transaction callback APIs (also when implemented in the project, e.g. Kill Bill) ----------
        if any((n, name) in TX_CALLBACK_APIS for n in rnames):
            info.update({'cls': 'txapi', 'what': 'callback', 'api': rs})
            return info
        for (tname, mname) in self.rules.get('commit_points', []):
            if name == mname and (rs == tname or tname in rnames):
                info.update({'cls': 'txapi', 'what': 'commit-point', 'api': 'app:' + tname})
                return info
        # ---------- project resolution ----------
        if rtype and rtype in idx.types:
            decls, tg = idx.targets(rtype, name, arity)
            if decls:
                info['cls'] = 'project'
                info['decl'] = decls[0].id
                info['targets'] = [m.id for m in tg]
                if self.recv_entity(rtype) is None and len(tg) <= 8:
                    # entity bound by the subtypes that select each target; kept only when unambiguous
                    te = {}
                    for mid_, sels in getattr(idx, 'last_selection', {}).items():
                        es = {self.recv_entity(sc_) for sc_ in sels}
                        if len(es) == 1 and None not in es:
                            te[mid_] = es.pop()
                    if te:
                        info['target_ent'] = te
                ret = decls[0].ret
                # e.g. JpaRepository<E, UUID> getRepository(): remember the entity the result is bound to
                if ret and ret.startswith('EXT:') and '<' in (decls[0].ret_raw or ''):
                    rent = self.decl_type_entity(decls[0].ret_raw, decls[0].cls, rtype)
                    if rent:
                        env.setdefault('_ret_ent', {})[(node.start_byte, node.end_byte)] = rent
                # generic return through type variables -> unknown
                if ret and ret.startswith('TV:'):
                    ret = None
                info['ret'] = ret
                # project interface with JDBI / MyBatis annotations and no implementation
                dm = decls[0]
                ann = {a[0] for a in dm.annots}
                if not tg:
                    op = self.classify_declared_dao(dm, rtype, rnames, ann, name, args, src, env)
                    if op:
                        info.update(op)
                        info['cls'] = 'db'
                return info
        # ---------- special chain types ----------
        if rtype in ('QUERY',) or rs in QUERY_TYPES:
            if name in QUERY_TERMINAL_R:
                info.update(self._query_op(obj, src, env, 'R'))
                return info
            if name in ('executeUpdate',):
                info.update(self._query_op(obj, src, env, 'W'))
                return info
            if name in ('setLockMode', 'setLockOptions', 'setHint') and args:
                at = T(args[0], src)
                if 'PESSIMISTIC' in at or 'UPGRADE' in at or 'lock' in at.lower():
                    info['lockhint'] = at
            info['ret'] = 'QUERY'
            info['sql_carrier'] = True
            return info
        if rtype == 'SESSION' or rs in SESSION_TYPES or rs in EM_TYPES or rtype == 'EM':
            fam = 'jpa' if (rs in EM_TYPES or rtype == 'EM') else 'hibernate'
            if name in QUERY_BUILDERS:
                info['ret'] = 'QUERY'
                return info
            if name in ('getTransaction',):
                info['ret'] = 'EXT:EntityTransaction'
                return info
            if name in ('beginTransaction',):
                info.update({'cls': 'txapi', 'what': 'begin', 'api': fam})
                info['ret'] = 'EXT:Transaction'
                return info
            if name in ('unwrap', 'getDelegate', 'getSession'):
                info['ret'] = 'SESSION'
                return info
            op = None
            if name in ('persist', 'merge', 'remove', 'save', 'saveOrUpdate', 'update', 'delete', 'replicate',
                        'insert', 'upsert'):
                op = 'W'
            elif name in ('flush',):
                op = 'W'
            elif name in ('find', 'getReference', 'refresh', 'get', 'load', 'byId', 'byNaturalId', 'bySimpleNaturalId',
                          'byMultipleIds', 'findMultiple'):
                op = 'R'
            elif name in ('lock', 'buildLockRequest'):
                op = 'L'
            if op:
                ent = self._entity_from_args(args, src, env)
                lockhint = None
                for a in args:
                    at = T(a, src)
                    if 'PESSIMISTIC' in at or 'LockMode' in at or 'LockOptions' in at:
                        lockhint = at
                info.update({'cls': 'db', 'api': fam, 'op': op, 'entity': ent, 'lockhint': lockhint})
                return info
            return info
        if rs in ('EntityTransaction', 'Transaction') and name in ('begin', 'commit', 'rollback'):
            info.update({'cls': 'txapi', 'what': name, 'api': 'jpa'})
            return info
        if rs in ('EntityManagerFactory',) and name == 'createEntityManager':
            info['ret'] = 'EM'
            return info
        if rs in ('SessionFactory',) and name in ('getCurrentSession', 'openSession', 'openStatelessSession'):
            info['ret'] = 'SESSION'
            return info
        # chained calls returning session/em by convention
        if rtype is None or rtype.startswith('EXT:') or rtype in ('QUERY',):
            if name in ('getCurrentSession', 'currentSession', 'getSession', 'openSession') and arity == 0:
                info['ret'] = 'SESSION'
                return info
            if name in ('getEntityManager', 'entityManager', 'em', 'getEm') and arity <= 1:
                info['ret'] = 'EM'
                return info
        # ---------- Spring Data repositories ----------
        if rnames & SPRING_DATA_REPOS:
            fam = 'nosql-repo' if rnames & NOSQL_REPOS else 'spring-data'
            op = None
            if name in REPO_WRITE or name.startswith('saveAll') or name.startswith('deleteAll'):
                op = 'W'
            elif name in REPO_READ or re.match(r'(find|read|get|query|search|stream|count|exists)', name):
                op = 'R'
            elif re.match(r'(delete|remove)', name):
                op = 'W'
            if op:
                ent = self.recv_entity(ent_type)
                if ent is None and ent_type and ent_type in idx.types:
                    _, a = idx.generic_binding(ent_type, SPRING_DATA_REPOS)
                    ent = a[0] if a else None
                if ent is None or ent not in self.entities:
                    ent = self.recv_decl_entity(obj, src, env) or ent
                info.update({'cls': 'db', 'api': fam, 'op': op, 'entity': ent, 'libtx': op == 'W'})
                return info
        # ---------- MyBatis-Plus service / mapper inherited methods ----------
        if rnames & MP_SERVICE:
            op = None
            if re.match(r'(save|update|remove|delete|insert)', name):
                op = 'W'
            elif re.match(r'(get|list|page|count|exists|query|select|one|lambdaQuery|ktQuery)', name):
                op = 'R'
            if name in ('lambdaQuery', 'lambdaUpdate', 'query', 'update', 'ktQuery', 'ktUpdate') and arity == 0:
                # the chain carries the entity: MPCHAIN:<R|W>:<entity fqn>
                info['ret'] = 'MPCHAIN:' + ('W' if 'pdate' in name else 'R') + ':' + (self.recv_entity(ent_type) or '')
                return info
            if name in ('getBaseMapper',):
                _, a = idx.generic_binding(ent_type, {'ServiceImpl', 'MPJBaseServiceImpl'})
                info['ret'] = a[0] if a else None
                return info
            if op:
                ent = self.recv_entity(ent_type) or self.recv_decl_entity(obj, src, env)
                info.update({'cls': 'db', 'api': 'mybatis-plus', 'op': op, 'entity': ent,
                             'libtx': name in MP_SERVICE_LIBTX})
                return info
        if rtype and rtype.startswith('MPCHAIN:'):
            parts = rtype.split(':', 2)
            cent = parts[2] if len(parts) > 2 and parts[2] else None
            if name in ('update', 'remove'):
                info.update({'cls': 'db', 'api': 'mybatis-plus', 'op': 'W', 'entity': cent})
                return info
            if name in ('list', 'one', 'page', 'count', 'exists', 'oneOpt', 'listObjs', 'listMaps'):
                info.update({'cls': 'db', 'api': 'mybatis-plus', 'op': 'R', 'entity': cent})
                return info
            info['ret'] = rtype
            return info
        if rnames & MP_MAPPER:
            op = None
            if re.match(r'(insert|update|delete|save)', name):
                op = 'W'
            elif re.match(r'(select|exists|count)', name):
                op = 'R'
            if op:
                ent = self.recv_entity(ent_type) or self.recv_decl_entity(obj, src, env)
                info.update({'cls': 'db', 'api': 'mybatis-plus', 'op': op, 'entity': ent})
                return info
        # ---------- JdbcTemplate ----------
        if rs in JDBC_TEMPLATE:
            sql = self._sql_arg(args, src, env)
            if name in ('update', 'batchUpdate'):
                op = 'W'
            elif name.startswith('query'):
                op = 'R'
            elif name in ('execute', 'call'):
                op = 'U'
            elif name == 'sql':
                info['ret'] = 'JDBCCLIENT'
                info['sqltext'] = sql
                return info
            else:
                return info
            info.update({'cls': 'db', 'api': 'jdbc-template', 'op': op, 'sql': sql})
            return info
        if rtype == 'JDBCCLIENT':
            if name in ('update',):
                info.update({'cls': 'db', 'api': 'jdbc-client', 'op': 'W', 'sql': self._chain_sql(obj, src, env)})
                return info
            if name in ('query', 'list', 'single', 'optional', 'set'):
                info.update({'cls': 'db', 'api': 'jdbc-client', 'op': 'R', 'sql': self._chain_sql(obj, src, env)})
                return info
            info['ret'] = 'JDBCCLIENT'
            return info
        if rs in ('SimpleJdbcInsert',) and name.startswith('execute'):
            info.update({'cls': 'db', 'api': 'jdbc-template', 'op': 'W', 'sql': None})
            return info
        # ---------- plain JDBC ----------
        if rs == 'Connection' or rs.endswith('Connection'):
            if name in ('prepareStatement', 'prepareCall'):
                info['ret'] = 'EXT:PreparedStatement'
                info['sqltext'] = self._sql_arg(args, src, env)
                return info
            if name == 'createStatement':
                info['ret'] = 'EXT:Statement'
                return info
            if name == 'setAutoCommit' and args and T(args[0], src).strip() == 'false':
                info.update({'cls': 'txapi', 'what': 'begin', 'api': 'jdbc'})
                return info
            if name in ('commit', 'rollback', 'setSavepoint', 'releaseSavepoint'):
                info.update({'cls': 'txapi', 'what': name, 'api': 'jdbc'})
                return info
            if name == 'setTransactionIsolation':
                info.update({'cls': 'txapi', 'what': 'isolation', 'api': 'jdbc', 'detail': T(args[0], src) if args else ''})
                return info
        if rs in JDBC_STMT:
            op = None
            if name in ('executeQuery',):
                op = 'R'
            elif name in ('executeUpdate', 'executeLargeUpdate', 'executeBatch', 'executeLargeBatch'):
                op = 'W'
            elif name == 'execute':
                op = 'U'
            if op:
                sql = self._sql_arg(args, src, env) if args else self._stmt_sql(obj, src, env)
                info.update({'cls': 'db', 'api': 'jdbc', 'op': op, 'sql': sql})
                return info
            return info
        # ---------- JDBI ----------
        if rs in ('Handle',):
            if name in ('inTransaction', 'useTransaction'):
                info.update({'cls': 'txapi', 'what': 'callback', 'api': 'jdbi'})
                return info
            if name in ('createUpdate', 'execute', 'prepareBatch', 'createBatch'):
                info.update({'cls': 'db', 'api': 'jdbi', 'op': 'W', 'sql': self._sql_arg(args, src, env)})
                return info
            if name in ('createQuery', 'select'):
                info.update({'cls': 'db', 'api': 'jdbi', 'op': 'R', 'sql': self._sql_arg(args, src, env)})
                return info
            if name in ('attach',) and args and args[0].type == 'class_literal':
                info['ret'] = idx.resolve(T(args[0], src)[:-6], env['cu'], td.fqn)
                return info
        # ---------- generic "become/getMapper(X.class)" ----------
        if name in ('become', 'getMapper', 'onDemand', 'attach', 'getBean', 'getDao', 'getRepository') and args \
                and args[0].type == 'class_literal':
            ty = T(args[0], src)[:-len('.class')]
            info['ret'] = idx.resolve(ty, env['cu'], td.fqn)
            return info
        # ---------- transactions (programmatic) ----------
        if (rs, name) in TX_CALLBACK_APIS or (name in ('execute', 'executeWithoutResult') and rs in ('TransactionTemplate', 'TransactionOperations')):
            info.update({'cls': 'txapi', 'what': 'callback', 'api': rs})
            return info
        if rs.endswith('TransactionManager') or rs in ('PlatformTransactionManager',):
            if name == 'getTransaction':
                info.update({'cls': 'txapi', 'what': 'begin', 'api': 'spring-ptm'})
                return info
            if name in ('commit', 'rollback'):
                info.update({'cls': 'txapi', 'what': name, 'api': 'spring-ptm'})
                return info
        if rs in ('GlobalTransaction',) and name in ('begin', 'commit', 'rollback'):
            info.update({'cls': 'txapi', 'what': name, 'api': 'seata'})
            return info
        # app-specific commit points (e.g., Roller's Weblogger.flush())
        for (tname, mname) in self.rules.get('commit_points', []):
            if name == mname and (rs == tname or tname in rnames):
                info.update({'cls': 'txapi', 'what': 'commit-point', 'api': 'app'})
                return info
        # ---------- locks / ad hoc ----------
        if (rs in LOCK_TYPES or rnames & LOCK_TYPES or rtype == 'LOCK') and name in ('lock', 'tryLock', 'lockInterruptibly', 'acquire', 'tryAcquire', 'readLock', 'writeLock'):
            if name in ('readLock', 'writeLock'):
                info['ret'] = 'LOCK'
                return info
            info.update({'cls': 'lock', 'lock_kind': 'app-lock:' + (rs or 'Lock'), 'what': 'acquire'})
            return info
        if (rs in LOCK_TYPES or rnames & LOCK_TYPES or rtype == 'LOCK') and name in ('unlock', 'release', 'forceUnlock'):
            info.update({'cls': 'lock', 'lock_kind': 'app-lock:' + (rs or 'Lock'), 'what': 'release'})
            return info
        if name in LOCK_GETTERS and (rs in ('RedissonClient', 'Redisson', 'RedisLockRegistry', 'LockRegistry',
                                            'ReadWriteLock', 'ReentrantReadWriteLock', 'RReadWriteLock',
                                            'CuratorFramework', 'DistributedLockFactory') or 'Redisson' in rs
                                     or 'Lock' in rs):
            info['ret'] = 'LOCK'
            return info
        if name in ('setIfAbsent', 'setnx', 'setNX', 'setIfPresent', 'tryLock', 'tryGetDistributedLock',
                    'getDistributedLock', 'lock') and (EFFECT_TYPES.get(rs) == 'cache' or 'Redis' in rs or 'redis' in (T(obj, src).lower() if obj is not None else '') or 'Lock' in rs):
            info.update({'cls': 'lock', 'lock_kind': 'redis-setnx' if 'set' in name.lower() else 'app-lock:' + rs, 'what': 'acquire'})
            return info
        if name in ('opsForValue', 'opsForHash', 'opsForList', 'opsForSet', 'opsForZSet', 'boundValueOps', 'boundHashOps'):
            info['ret'] = 'EXT:ValueOperations'
            return info
        # ---------- async ----------
        if (rs, name) in ASYNC_METHODS or name in ASYNC_NAMES or (name in ('submit', 'execute', 'schedule', 'invokeAll') and ('Executor' in rs or 'Pool' in rs or 'executor' in (T(obj, src).lower() if obj is not None else ''))):
            info.update({'cls': 'async', 'async_kind': f"{rs}.{name}"})
            return info
        if name == 'start' and rs == 'Thread':
            info.update({'cls': 'async', 'async_kind': 'Thread.start'})
            return info
        # ---------- effects ----------
        eff = EFFECT_TYPES.get(rs)
        if eff is None:
            for nme in rnames:
                if nme in EFFECT_TYPES and EFFECT_TYPES[nme]:
                    eff = EFFECT_TYPES[nme]
                    break
        if eff is None and rtype and rtype in idx.types:
            ann = {a[0] for a in idx.types[rtype].annots}
            if 'FeignClient' in ann:
                eff = 'http'
        if eff:
            if eff == 'event' and name != 'publishEvent':
                return info
            info.update({'cls': 'effect', 'effect_kind': eff, 'detail': f"{rs}.{name}"})
            return info
        if rkind == 'static' and (rs, name) in NONDET_STATIC:
            info.update({'cls': 'nondet', 'detail': f"{rs}.{name}"})
            return info
        if (rs, name) in NONDET_STATIC:
            info.update({'cls': 'nondet', 'detail': f"{rs}.{name}"})
            return info
        # external interface (e.g. an API jar) implemented by project classes
        if rtype and rtype.startswith('EXT:') and rkind != 'static':
            impls = idx.ext_impls.get(rs)
            if impls:
                tg = []
                for c in impls:
                    td2 = idx.types.get(c)
                    if td2 is None:
                        continue
                    for m in td2.methods.get(name, ()):
                        if m.has_body and (len(m.params) == arity or (m.varargs and arity >= len(m.params) - 1)):
                            tg.append(m.id)
                if tg:
                    info['cls'] = 'project'
                    info['decl'] = tg[0]
                    info['targets'] = tg
                    info['via_ext_interface'] = rs
                    r0 = idx.methods[tg[0]].ret
                    info['ret'] = None if (r0 and r0.startswith('TV:')) else r0
        return info

    def classify_declared_dao(self, dm, rtype, rnames, ann, name, args, src, env):
        """Abstract method declared in a project interface (no implementation): MyBatis mapper,
        Spring Data repository custom method, JDBI SqlObject, Kill Bill EntitySqlDao, Feign."""
        idx = self.idx
        rtd = idx.types.get(dm.cls)
        rann = {a[0] for a in rtd.annots} if rtd else set()
        # receiver interface implemented by a Spring Data repository interface (e.g. Syncope's XxxDAO <- XxxRepo)
        if not (rnames & SPRING_DATA_REPOS) and rtype in idx.types:
            for sub in idx.all_subtypes(rtype):
                if idx.super_names(sub) & SPRING_DATA_REPOS:
                    rnames = rnames | {'Repository'}
                    if self.recv_entity(sub):
                        rtype = sub
                    break
        # Spring Data repository methods declared in the interface
        if rnames & SPRING_DATA_REPOS or idx.super_names(dm.cls) & SPRING_DATA_REPOS:
            q = None; native = False; modifying = 'Modifying' in ann; lock = None
            for (an, aq, aargs, al) in dm.annots:
                if an in ('Query', 'NativeQuery'):
                    q = aargs
                    qa = ann_args(aargs)
                    native = qa.get('nativeQuery', '').strip() == 'true' or an == 'NativeQuery'
                if an == 'Lock':
                    lock = aargs
            ent = self.recv_entity(rtype) or self.recv_entity(dm.cls)
            qtext = None
            if q:
                qtext = self.const_expr(ann_args(q).get('value'), dm.cls)
                if qtext is None:
                    mm = re.findall(r'"((?:[^"\\]|\\.)*)"', q)
                    qtext = ' '.join(mm) if mm else q
            if q and qtext:
                low = qtext.lower().lstrip()
                op = 'W' if (modifying or re.match(r'(update|delete|insert)', low)) else 'R'
            elif re.match(r'(delete|remove)', name):
                op = 'W'
            elif modifying:
                op = 'W'
            else:
                op = 'R'
            return {'api': 'spring-data', 'op': 'L' if lock and 'PESSIMISTIC' in lock else op, 'entity': ent,
                    'sql': qtext, 'sql_kind': ('sql' if native else 'jpql') if qtext else 'derived',
                    'lockhint': lock, 'libtx': True}
        # MyBatis annotations
        for (an, aq, aargs, al) in dm.annots:
            if an in ('Select', 'SelectProvider'):
                return {'api': 'mybatis', 'op': 'R', 'sql': ' '.join(re.findall(r'"((?:[^"\\]|\\.)*)"', aargs)), 'sql_kind': 'sql'}
            if an in ('Insert', 'Update', 'Delete', 'InsertProvider', 'UpdateProvider', 'DeleteProvider'):
                return {'api': 'mybatis', 'op': 'W', 'sql': ' '.join(re.findall(r'"((?:[^"\\]|\\.)*)"', aargs)), 'sql_kind': 'sql'}
            if an in ('SqlQuery', 'SqlUpdate', 'SqlBatch', 'SqlCall'):
                stg = self.stg.get(simple(rtype)) or self.stg.get(simple(dm.cls)) or {}
                q = ' '.join(re.findall(r'"((?:[^"\\]|\\.)*)"', aargs)) or None
                return {'api': 'jdbi', 'op': 'R' if an == 'SqlQuery' else 'W', 'sql': q, 'sql_kind': 'sql',
                        'table_hint': stg.get('table'), 'history_hint': stg.get('history'),
                        'audited': any(a2[0] == 'Audited' for a2 in dm.annots)}
        # MyBatis XML
        for ns in [rtype, dm.cls] + [s for s in idx.all_supers(rtype) if not s.startswith('EXT:')]:
            st = self.mappers.get(ns, {}).get(name)
            if st:
                kind, body, rel = st
                return {'api': 'mybatis', 'op': 'R' if kind == 'select' else 'W', 'sql': body, 'sql_kind': 'sql',
                        'mapper_xml': rel}
        mybatis_cu = rtd is not None and (any('ibatis' in v or 'mybatis' in v for v in rtd.cu.imports.values())
                                         or any('ibatis' in w or 'mybatis' in w for w in rtd.cu.wild))
        if ('Mapper' in rann and mybatis_cu) or rnames & MP_MAPPER or (mybatis_cu and rtype and simple(rtype).endswith('Mapper')) \
                or (self.mappers and rtype and simple(rtype).endswith('Mapper') and rtd is not None and rtd.kind == 'interface'
                    and not any('mapstruct' in v for v in rtd.cu.imports.values())):
            if re.match(r'(insert|update|delete|save|add|remove|batch|modify|set|create)', name, re.I):
                op = 'W'
            elif re.match(r'(select|get|find|query|list|count|exists|load|page|search|check|sum)', name, re.I):
                op = 'R'
            else:
                op = 'U'
            return {'api': 'mybatis', 'op': op, 'sql': None, 'entity': self.recv_entity(rtype)}
        # Kill Bill style DAO interfaces with JDBI annotations inherited from a base interface
        if rnames & {'EntitySqlDao', 'Transmogrifier', 'AuditSqlDao', 'HistorySqlDao'} or simple(rtype or '').endswith('SqlDao'):
            info = None
            for (an, aq, aargs, al) in dm.annots:
                if an in ('SqlQuery',):
                    info = {'api': 'jdbi', 'op': 'R'}
                elif an in ('SqlUpdate', 'SqlBatch'):
                    info = {'api': 'jdbi', 'op': 'W'}
            if info is None:
                info = {'api': 'jdbi', 'op': 'W' if re.match(r'(create|update|delete|insert|add|remove|set|batch)', name) else 'R'}
            stg = self.stg.get(simple(rtype)) or {}
            info['table_hint'] = stg.get('table')
            info['history_hint'] = stg.get('history')
            info['audited'] = any(a2[0] == 'Audited' for a2 in dm.annots)
            info['sql'] = None
            return info
        if 'FeignClient' in rann:
            return None
        mapstruct = rtd is not None and (any('mapstruct' in v for v in rtd.cu.imports.values()) or any('mapstruct' in w for w in rtd.cu.wild))
        if simple(rtype or '').endswith(('Dao', 'DAO', 'Repository', 'Store', 'Mapper')) and rtd is not None and rtd.kind == 'interface' \
                and not mapstruct:
            op = 'W' if re.match(r'(save|insert|update|delete|remove|add|create|persist|merge|set|batch)', name) else 'R'
            return {'api': 'dao-unresolved', 'op': op, 'entity': self.recv_entity(rtype)}
        return None

    def as_entity(self, t):
        """Map a type to an entity: itself, an entity supertype (mapped superclass) or an entity implementation."""
        if not isinstance(t, str) or t not in self.idx.types:
            return None
        if t in self.entities:
            return t
        for s in self.idx.all_supers(t):
            if s in self.entities:
                return s
        for s in self.idx.all_subtypes(t):
            if s in self.entities:
                return s
        return None

    def _entity_from_args(self, args, src, env):
        for a in args:
            if a.type == 'class_literal':
                ty = T(a, src)[:-len('.class')]
                r = self.as_entity(self.idx.resolve(ty, env['cu'], env['td'].fqn))
                if r:
                    return r
            # Broadleaf: entityConfiguration.lookupEntityClass("org.x.domain.Order") / Class.forName("...")
            if a.type == 'method_invocation':
                nm = a.child_by_field_name('name'); an = a.child_by_field_name('arguments')
                if nm is not None and T(nm, src) in ('lookupEntityClass', 'forName', 'createEntityInstance') \
                        and an is not None and an.named_children and an.named_children[0].type == 'string_literal':
                    fq = T(an.named_children[0], src).strip('"')
                    r = self.as_entity(fq) if fq in self.idx.types else None
                    if r:
                        return r
        for a in args[:2]:
            t = self.expr_type(a, src, env)
            e = self.as_entity(t) if isinstance(t, str) else None
            if e:
                return e
        return None

    def _sql_arg(self, args, src, env):
        for a in args[:2]:
            if a.type in ('string_literal', 'binary_expression', 'text_block'):
                return self.join_parts(expr_parts(a, src), env['td'].fqn, env=env, src=src)
            if a.type == 'identifier':
                n = T(a, src)
                if n in env['strings']:
                    s = self.local_string(n, env['strings'], src, env['td'].fqn, env=env)
                    if s and SQL_KW_RE.search(s):
                        return s
                r = self.resolve_ref(n, env['td'].fqn)
                if r:
                    return r
                f, owner = self.idx.field_lookup(env['td'].fqn, n)
                if f is not None and f.sqlstmt_parts:
                    return self.join_parts(f.sqlstmt_parts, owner)
            if a.type in ('field_access',):
                r = self.resolve_ref(T(a, src), env['td'].fqn)
                if r:
                    return r
            if a.type == 'method_invocation':
                nm = a.child_by_field_name('name')
                if nm is not None and T(nm, src) == 'toString':
                    o = a.child_by_field_name('object')
                    if o is not None and o.type == 'identifier' and T(o, src) in env['strings']:
                        return self.local_string(T(o, src), env['strings'], src, env['td'].fqn, env=env)
                parts = expr_parts(a, src)
                if parts and parts[0][0] != 'call':      # String.format / formatted / concat / join
                    s = self.join_parts(parts, env['td'].fqn, env=env, src=src)
                    if s and SQL_KW_RE.search(s):
                        return s
                else:
                    s = self.call_string(T(a, src), env['td'].fqn, env)
                    if s and SQL_KW_RE.search(s):
                        return s
        return None

    def _local_strs(self, env, src):
        return None

    def _stmt_sql(self, obj, src, env):
        """SQL attached to a statement variable / chain (prepareStatement / getPreparedStatement)."""
        if obj is None:
            return None
        if obj.type == 'method_invocation':
            info = self.call_info(obj, src, env)
            if info.get('sqltext'):
                return info['sqltext']
            # BenchBase getPreparedStatement(conn, SQLStmtField, ...)
            args_node = obj.child_by_field_name('arguments')
            if args_node is not None:
                for a in args_node.named_children:
                    if a.type in ('identifier', 'field_access'):
                        f, owner = self.idx.field_lookup(env['td'].fqn, T(a, src).split('.')[-1])
                        if f is not None and f.sqlstmt_parts:
                            return self.join_parts(f.sqlstmt_parts, owner)
            return None
        if obj.type == 'identifier':
            n = T(obj, src)
            inits = [i for i in env['strings'].get('__init__' + n, []) if i.start_byte < obj.start_byte]
            inits.sort(key=lambda i: i.start_byte, reverse=True)
            for init in inits[:1] or env['strings'].get('__init__' + n, [])[:1]:
                s = self._stmt_sql(init, src, env)
                if s:
                    return s
        return None

    def _chain_sql(self, obj, src, env):
        n = obj
        while n is not None and n.type == 'method_invocation':
            nm = n.child_by_field_name('name')
            if nm is not None and T(nm, src) == 'sql':
                args_node = n.child_by_field_name('arguments')
                return self._sql_arg(list(args_node.named_children) if args_node is not None else [], src, env)
            n = n.child_by_field_name('object')
        return None

    def _criteria_root(self, var, env, src):
        """Entity of a JPA criteria query variable: the X in var.from(X.class)."""
        roots = env.get('_criteria_roots')
        if roots is None:
            roots = {}
            stack = [env['body']]
            while stack:
                n2 = stack.pop()
                if n2.type == 'method_invocation':
                    nm = n2.child_by_field_name('name'); o = n2.child_by_field_name('object')
                    an = n2.child_by_field_name('arguments')
                    if nm is not None and T(nm, src) == 'from' and o is not None and o.type == 'identifier' \
                            and an is not None and an.named_children and an.named_children[0].type == 'class_literal':
                        e = self.as_entity(self.idx.resolve(T(an.named_children[0], src)[:-6], env['cu'], env['td'].fqn))
                        if e:
                            roots.setdefault(T(o, src), e)
                stack.extend(n2.named_children)
            env['_criteria_roots'] = roots
        return roots.get(var)

    def _query_op(self, obj, src, env, op):
        """Find the createQuery(...) at the start of a query chain or the variable's initializer."""
        n = obj
        qtext, kind, ent = None, None, None
        hops = 0
        while n is not None and hops < 30:
            hops += 1
            if n.type == 'method_invocation':
                nm = n.child_by_field_name('name')
                name = T(nm, src) if nm is not None else ''
                if name in QUERY_BUILDERS:
                    args_node = n.child_by_field_name('arguments')
                    args = list(args_node.named_children) if args_node is not None else []
                    if args and args[0].type == 'identifier' and T(args[0], src) not in env['strings']:
                        ce = self._criteria_root(T(args[0], src), env, src)   # createQuery(criteriaQuery)
                        if ce:
                            ent = ce
                            break
                    qtext = self._sql_arg(args, src, env)
                    kind = 'sql' if name in ('createNativeQuery', 'createSQLQuery', 'getNamedNativeQuery') else ('named' if 'Named' in name else 'jpql')
                    if kind == 'named' and args and args[0].type == 'string_literal':
                        qtext = 'NAMED:' + T(args[0], src).strip('"')
                    for a in args[1:]:
                        if a.type == 'class_literal':
                            r = self.idx.resolve(T(a, src)[:-6], env['cu'], env['td'].fqn)
                            if r in self.entities:
                                ent = r
                    if name == 'createCriteria' and args and args[0].type == 'class_literal':
                        r = self.idx.resolve(T(args[0], src)[:-6], env['cu'], env['td'].fqn)
                        if r in self.entities:
                            ent = r
                    break
                # query text handed to an app helper that builds the query, e.g. nativeSynchronizedQuery(sql)
                if name not in ('setParameter', 'setParameterList', 'setFirstResult', 'setMaxResults', 'setHint',
                                'setLockMode', 'setFlushMode', 'setCacheable', 'setReadOnly', 'setFetchSize'):
                    args_node = n.child_by_field_name('arguments')
                    hargs = list(args_node.named_children) if args_node is not None else []
                    cand = self._sql_arg(hargs, src, env) if hargs else None
                    if isinstance(cand, str) and re.match(r'\s*\(?\s*(select|update|delete|insert|from|with|merge)\b', cand, re.I):
                        qtext = cand
                        kind = 'sql' if re.search(r'native|sql', name, re.I) else None
                        break
                n = n.child_by_field_name('object')
            elif n.type == 'identifier':
                inits = [i for i in env['strings'].get('__init__' + T(n, src), []) if i.start_byte < n.start_byte]
                if not inits:
                    inits = env['strings'].get('__init__' + T(n, src), [])
                if not inits:
                    break
                n = max(inits, key=lambda i: i.start_byte) if any(i.start_byte < n.start_byte for i in inits) else inits[0]
            else:
                break
        out = {'cls': 'db', 'api': 'jpa-query', 'op': op, 'sql': qtext, 'sql_kind': kind, 'entity': ent}
        if qtext and op == 'R' and kind in ('jpql', 'sql'):
            low = qtext.lower().lstrip()
            if re.match(r'(update|delete|insert)', low):
                out['op'] = 'W'
        return out

    # --- body walk ----------------------------------------------------------------
    def _scan_body(self, meth, body, src, cu, td, pseudo_of=None):
        localtypes, strings, lambdas = self._collect_locals(body, src, meth, cu, td)
        env = {'meth': meth, 'cu': cu, 'td': td, 'locals': localtypes, 'strings': strings, 'body': body}
        mid = meth.id
        # stack items: (node, method_id, loop, cond, sync, catch)
        stack = [(c, mid, False, False, 0, False) for c in reversed(body.named_children)]
        while stack:
            node, cur, loop, cond, sync, catch = stack.pop()
            t = node.type
            if t == 'lambda_expression' or (t == 'object_creation_expression' and any(c.type == 'class_body' for c in node.children)) \
                    or t == 'method_reference':
                pid = self._register_pseudo(node, src, cur, td, cu, meth)
                if t == 'lambda_expression':
                    b = node.child_by_field_name('body')
                    if b is not None:
                        # lambda params
                        ps = node.child_by_field_name('parameters')
                        if ps is not None:
                            for p in ps.named_children:
                                if p.type == 'formal_parameter':
                                    pt = p.child_by_field_name('type'); pn = p.child_by_field_name('name')
                                    if pt is not None and pn is not None:
                                        localtypes[T(pn, src)] = self.idx.resolve(T(pt, src), cu, td.fqn)
                        children = [b] if b.type != 'block' else list(b.named_children)
                        for c in reversed(children):
                            stack.append((c, pid, False, False, sync, False))
                elif t == 'object_creation_expression':
                    cb = [c for c in node.children if c.type == 'class_body'][0]
                    for mnode in cb.named_children:
                        if mnode.type == 'method_declaration':
                            b = mnode.child_by_field_name('body')
                            if b is None:
                                continue
                            sub = f"{pid}#{T(mnode.child_by_field_name('name'), src)}"
                            self._ensure_pseudo(sub, td, cu, mnode.start_point[0] + 1, meth, mnode, src)
                            self.callbacks[pid].append((sub, 'sync', None))
                            for (pn, pt) in self.pseudo_methods[sub].params:
                                localtypes.setdefault(pn, self.idx.resolve(pt, cu, td.fqn))
                            for c in reversed(b.named_children):
                                stack.append((c, sub, False, False, sync, False))
                    # also scan constructor args
                    args = node.child_by_field_name('arguments')
                    if args is not None:
                        for c in reversed(args.named_children):
                            stack.append((c, cur, loop, cond, sync, catch))
                    self._record_ctor(node, src, cur, env, loop, cond, sync, catch)
                else:
                    self._record_mref(node, src, cur, env, pid, loop, cond)
                continue
            if t == 'method_invocation':
                self._record_call(node, src, cur, env, loop, cond, sync, catch, lambdas)
            elif t == 'object_creation_expression':
                self._record_ctor(node, src, cur, env, loop, cond, sync, catch)
            elif t == 'synchronized_statement':
                self.lock_sites.append({'method': cur, 'kind': 'synchronized-block', 'line': node.start_point[0] + 1,
                                        'end': node.end_point[0] + 1, 'detail': T(node.named_children[0], src)[:80] if node.named_children else ''})
                for c in reversed(node.named_children):
                    stack.append((c, cur, loop, cond, sync + 1, catch))
                continue
            elif t == 'explicit_constructor_invocation':
                pass
            elif t in ('class_declaration', 'interface_declaration', 'enum_declaration', 'record_declaration'):
                continue  # local classes: skipped
            # children with context
            if t in LOOP_NODES:
                for c in reversed(node.named_children):
                    stack.append((c, cur, True, cond, sync, catch))
                continue
            if t == 'if_statement':
                cnd = node.child_by_field_name('condition')
                for c in reversed(node.named_children):
                    stack.append((c, cur, loop, cond or (c != cnd), sync, catch))
                continue
            if t in ('ternary_expression',):
                cnd = node.child_by_field_name('condition')
                for c in reversed(node.named_children):
                    stack.append((c, cur, loop, cond or (c != cnd), sync, catch))
                continue
            if t in ('switch_block_statement_group', 'switch_rule'):
                for c in reversed(node.named_children):
                    stack.append((c, cur, loop, True, sync, catch))
                continue
            if t in ('catch_clause',):
                for c in reversed(node.named_children):
                    stack.append((c, cur, loop, True, sync, True))
                continue
            for c in reversed(node.named_children):
                stack.append((c, cur, loop, cond, sync, catch))

    def _register_pseudo(self, node, src, cur, td, cu, meth):
        line, col = node.start_point[0] + 1, node.start_point[1]
        kind = 'L' if node.type == 'lambda_expression' else ('A' if node.type == 'object_creation_expression' else 'R')
        pid = f"{meth.id}${kind}{line}:{col}"
        if pid not in self.pseudo_methods:
            self._ensure_pseudo(pid, td, cu, line, meth, node, src)
        return pid

    def _ensure_pseudo(self, pid, td, cu, line, meth, node, src):
        if pid in self.pseudo_methods:
            return
        m = Method()
        m.id = pid; m.name = pid.split('$')[-1]; m.cls = td.fqn; m.params = []; m.ret_raw = ''; m.ret = None
        m.annots = []; m.mods = set(); m.line = line; m.end = node.end_point[0] + 1; m.has_body = True
        m.varargs = False; m.file = cu.rel; m.is_ctor = False; m.pseudo = True; m.parent = meth.id
        m.ret_parts = None
        if node.type == 'method_declaration':
            ps = node.child_by_field_name('parameters')
            if ps is not None:
                for p in ps.named_children:
                    if p.type == 'formal_parameter':
                        pt = p.child_by_field_name('type'); pn = p.child_by_field_name('name')
                        if pt is not None and pn is not None:
                            m.params.append((T(pn, src), T(pt, src)))
        self.pseudo_methods[pid] = m
        self.idx.methods[pid] = m

    def _arg_summaries(self, args, src, cur, lambdas, env):
        out = []
        for i, a in enumerate(args):
            if a.type == 'lambda_expression':
                out.append(('lambda', f"{env['meth'].id}$L{a.start_point[0]+1}:{a.start_point[1]}"))
            elif a.type == 'object_creation_expression' and any(c.type == 'class_body' for c in a.children):
                out.append(('anon', f"{env['meth'].id}$A{a.start_point[0]+1}:{a.start_point[1]}"))
            elif a.type == 'method_reference':
                out.append(('mref', f"{env['meth'].id}$R{a.start_point[0]+1}:{a.start_point[1]}"))
            elif a.type == 'identifier' and T(a, src) in lambdas:
                ln = lambdas[T(a, src)]
                k = 'L' if ln.type == 'lambda_expression' else ('R' if ln.type == 'method_reference' else 'A')
                out.append(('lambda', f"{env['meth'].id}${k}{ln.start_point[0]+1}:{ln.start_point[1]}"))
            elif a.type == 'identifier' and any(T(a, src) == pn for (pn, pt) in env['meth'].params):
                pi = [pn for (pn, pt) in env['meth'].params].index(T(a, src))
                out.append(('param', pi))
            else:
                out.append(('other', None))
        return out

    def _record_call(self, node, src, cur, env, loop, cond, sync, catch, lambdas):
        info = self.call_info(node, src, env)
        args_node = node.child_by_field_name('arguments')
        args = list(args_node.named_children) if args_node is not None else []
        rec = {'line': node.start_point[0] + 1, 'col': node.start_point[1], 'loop': loop, 'cond': cond,
               'sync': sync > 0, 'catch': catch, 'name': info['name'], 'rkind': info.get('rkind'),
               'rtype': info.get('rtype') if isinstance(info.get('rtype'), str) else None,
               'cls': info.get('cls', 'lib')}
        for k in ('decl', 'targets', 'api', 'op', 'entity', 'sql', 'sql_kind', 'what', 'lock_kind', 'effect_kind',
                  'async_kind', 'detail', 'lockhint', 'libtx', 'table_hint', 'mapper_xml', 'target_ent', 'audited',
                  'history_hint'):
            if k in info and info[k] is not None:
                rec[k] = info[k]
        if rec['cls'] == 'db' and rec.get('op') in ('W', 'R', 'L', 'U') and not rec.get('entity'):
            if rec.get('api') in ('spring-data', 'jpa', 'hibernate', 'mybatis-plus'):
                ent = self._entity_from_args(args, src, env)
                if ent:
                    rec['entity'] = ent
            if not rec.get('entity') and not rec.get('sql') and rec.get('api') in ('jpa-query', 'jpa', 'hibernate'):
                cl = env.get('class_lits')
                if cl is None:
                    cl = []
                    stack = [env['body']]
                    while stack:
                        n2 = stack.pop()
                        if n2.type == 'class_literal':
                            e = self.as_entity(self.idx.resolve(T(n2, src)[:-6], env['cu'], env['td'].fqn))
                            if e and e not in cl:
                                cl.append(e)
                        stack.extend(n2.named_children)
                    env['class_lits'] = cl
                if len(cl) == 1:      # only when unambiguous
                    rec['entity'] = cl[0]
                    rec['entity_src'] = 'class-literal-in-method'
        summ = self._arg_summaries(args, src, cur, lambdas, env)
        cb = [s for s in summ if s[0] in ('lambda', 'anon', 'mref')]
        if cb:
            mode = 'sync'
            if rec['cls'] == 'async':
                mode = 'async'
            elif rec['cls'] == 'txapi' and rec.get('what') == 'callback':
                mode = 'tx'
            elif info['name'] in MAP_COND_NAMES:
                mode = 'cond'      # Map.computeIfAbsent(k, f): f runs at most once
            elif info['name'] in STREAM_LOOP_NAMES and self._is_iteration(node, info, src):
                mode = 'loop'
            elif info['name'] in OPTIONAL_COND_NAMES:
                mode = 'cond'      # Optional.ifPresent / map / orElseGet: runs only if a value is (or is not) present
            elif rec['cls'] == 'project' and rec.get('decl') in self.wrapper_params:
                mode = 'tx'
            for (k, pid) in cb:
                self.callbacks[cur].append((pid, mode, rec['line']))
                if mode == 'tx':
                    self.callback_roots[pid] = {'api': rec.get('api') or rec.get('decl'), 'site_method': cur,
                                                'line': rec['line']}
            rec['callbacks'] = [pid for (k, pid) in cb]
        if rec['cls'] == 'txapi' and rec.get('what') == 'callback':
            params = [s[1] for s in summ if s[0] == 'param']
            if params:
                self.wrapper_params[env['meth'].id] = params[-1]
        if rec['cls'] == 'project':
            rec['argkinds'] = [s[0] for s in summ]
            for ai, a in enumerate(args[:3]):
                if a.type == 'class_literal':      # e.g. strategy.load(WeblogEntry.class, id)
                    at = self.idx.resolve(T(a, src)[:-len('.class')], env['cu'], env['td'].fqn)
                else:
                    at = self.expr_type(a, src, env)
                e = self.as_entity(at) if isinstance(at, str) else None
                if e:
                    rec['arg_entity'] = e
                    rec['arg_entity_pos'] = ai
                    break
        if rec['cls'] == 'txapi' and rec.get('what') in ('begin', 'commit-point'):
            self.region_roots.setdefault(cur, []).append((rec.get('api'), rec['what'], rec['line']))
        if rec['cls'] == 'lock':
            self.lock_sites.append({'method': cur, 'kind': rec['lock_kind'], 'line': rec['line'], 'end': None,
                                    'detail': rec.get('what')})
        if rec['cls'] in ('lib',) and not info.get('ret') and rec['name'] in ('lock', 'unlock', 'tryLock') and rec.get('rtype') is None:
            pass
        self.calls[cur].append(rec)

    STREAMY = {'Stream', 'IntStream', 'LongStream', 'DoubleStream', 'List', 'Set', 'Collection', 'Iterable', 'Map',
               'ArrayList', 'LinkedList', 'HashMap', 'HashSet', 'TreeMap', 'TreeSet', 'LinkedHashMap', 'LinkedHashSet',
               'Iterator', 'Queue', 'Deque', 'ConcurrentHashMap', 'SortedMap', 'SortedSet', 'NavigableMap',
               'Collectors', 'Page', 'Slice', 'IPage', 'Flux', 'Multimap', 'ImmutableList', 'ImmutableSet'}

    def _is_iteration(self, node, info, src):
        """Is a lambda passed to this call executed once per element (stream/collection op), as opposed to
        Optional.map / ifPresent etc.?"""
        name = info['name']
        rs = simple(info.get('rtype')) if isinstance(info.get('rtype'), str) else ''
        if rs in ('Optional', 'OptionalInt', 'OptionalLong', 'CompletableFuture', 'Mono', 'Either', 'Try'):
            return False
        if name in ('forEach', 'forEachOrdered', 'removeIf', 'replaceAll', 'forEachRemaining', 'iterate', 'generate'):
            return True
        if rs in self.STREAMY:
            return True
        obj = node.child_by_field_name('object')
        txt = T(obj, src) if obj is not None else ''
        return bool(re.search(r'\.stream\(\)|\.parallelStream\(\)|Stream\.of|Arrays\.stream|IntStream|LongStream|\.entrySet\(\)|\.values\(\)|\.keySet\(\)', txt))

    def _record_ctor(self, node, src, cur, env, loop, cond, sync, catch):
        ty = node.child_by_field_name('type')
        if ty is None:
            return
        r = self.idx.resolve(T(ty, src), env['cu'], env['td'].fqn)
        args_node = node.child_by_field_name('arguments')
        arity = len(args_node.named_children) if args_node is not None else 0
        rec = {'line': node.start_point[0] + 1, 'col': node.start_point[1], 'loop': loop, 'cond': cond,
               'sync': sync > 0, 'catch': catch, 'name': '<init>', 'rtype': r, 'cls': 'lib'}
        if r and r in self.idx.types and (self.idx.super_names(r) & {'Throwable', 'Exception', 'RuntimeException', 'Error'}):
            pass    # building an exception: the transaction is failing; message lookups inside are not its work
        elif r and r in self.idx.types:
            # a constructor call runs exactly that class's constructor: no dispatch to subclasses
            tg = [m for m in self.idx.types[r].methods.get('<init>', ())
                  if m.has_body and (len(m.params) == arity or (m.varargs and arity >= len(m.params) - 1))]
            if tg:
                rec['cls'] = 'project'; rec['targets'] = [m.id for m in tg]; rec['decl'] = tg[0].id
        else:
            sn = simple(r or '')
            if sn in NONDET_NEW:
                rec['cls'] = 'nondet'; rec['detail'] = f"new {sn}"
            elif sn == 'Thread':
                rec['cls'] = 'async'; rec['async_kind'] = 'new Thread'
            elif sn in ('ReentrantLock', 'ReentrantReadWriteLock', 'StampedLock'):
                pass
        if rec['cls'] != 'lib':
            self.calls[cur].append(rec)

    def _record_mref(self, node, src, cur, env, pid, loop, cond):
        # resolve X::m to project methods; the pseudo method calls them
        txt = T(node, src)
        if '::' not in txt:
            return
        lhs, m = txt.rsplit('::', 1)
        lhs = lhs.strip(); m = m.strip()
        if lhs in ('this',):
            rt = env['td'].fqn
        elif lhs == 'super':
            rt = env['td'].supers[0][0] if env['td'].supers else None
        else:
            lt = env['locals'].get(lhs)
            if lt is not None and not isinstance(lt, tuple):
                rt = lt
            else:
                f, owner = self.idx.field_lookup(env['td'].fqn, lhs)
                rt = f.type if f is not None else self.idx.resolve(lhs, env['cu'], env['td'].fqn)
        if not rt or rt not in self.idx.types:
            return
        name = '<init>' if m == 'new' else m
        cands = []
        for c in [rt] + [s for s in self.idx.all_supers(rt) if not s.startswith('EXT:')]:
            tdd = self.idx.types.get(c)
            if tdd:
                cands.extend([x for x in tdd.methods.get(name, [])])
            if cands:
                break
        tg = [x.id for x in cands if x.has_body]
        for s in self.idx.all_subtypes(rt):
            tdd = self.idx.types.get(s)
            if tdd:
                tg.extend([x.id for x in tdd.methods.get(name, []) if x.has_body])
        if tg:
            self.calls[pid].append({'line': node.start_point[0] + 1, 'col': node.start_point[1], 'loop': False,
                                    'cond': False, 'sync': False, 'catch': False, 'name': name, 'rtype': rt,
                                    'cls': 'project', 'targets': tg, 'decl': cands[0].id if cands else tg[0]})


# ----------------------------------------------------------------------------
# Pass 3: transactions
# ----------------------------------------------------------------------------
def _strip_nested(s):
    """Remove nested (...) and {...} groups so only top-level attributes remain."""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r'\([^()]*\)', '', s)
        s = re.sub(r'\{[^{}]*\}', '', s)
    return s

def split_top(s, sep=','):
    """Split on sep outside string literals, text blocks and (), [], {} groups."""
    out, cur, depth, i, n = [], [], 0, 0, len(s)
    while i < n:
        if s.startswith('"""', i):
            j = s.find('"""', i + 3)
            j = n if j < 0 else j + 3
            cur.append(s[i:j]); i = j
            continue
        ch = s[i]
        if ch in '"\'':
            j = i + 1
            while j < n and s[j] != ch:
                j += 2 if s[j] == '\\' else 1
            cur.append(s[i:j + 1]); i = j + 1
            continue
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == sep and depth == 0:
            out.append(''.join(cur)); cur = []; i += 1
            continue
        cur.append(ch); i += 1
    if ''.join(cur).strip():
        out.append(''.join(cur))
    return out

def ann_args(aargs):
    """Top-level annotation attributes: '(name = "t", indexes = {...})' -> {'name': '"t"', 'indexes': '{...}'}."""
    d = {}
    if not aargs:
        return d
    s = aargs.strip()
    if s.startswith('(') and s.endswith(')'):
        s = s[1:-1]
    for part in split_top(s):
        m = re.match(r'\s*([A-Za-z_]\w*)\s*=(?!=)\s*(.*)$', part, re.S)
        if m:
            d[m.group(1)] = m.group(2).strip()
        elif part.strip():
            d['value'] = part.strip()
    return d

class TxBuilder:
    def __init__(self, idx, scan, entities, app, rules):
        self.idx, self.scan, self.entities, self.app, self.rules = idx, scan, entities, app, rules
        self.tx_attr = {}   # method id -> attrs dict (effective declarative tx)
        self.async_methods = set()
        self.entry = {}     # method id -> kind
        self.mapper_tables = {}

    # --- declarative --------------------------------------------------------------
    def compute_declarative(self):
        idx = self.idx
        def tx_annot(annots):
            for (an, aq, aargs, al) in annots:
                if an in DECL_TX_ANNOTS:
                    if an == 'Transaction' and 'jdbi' not in aq.lower() and not aq == 'Transaction':
                        continue
                    if an == 'Transactional' and aq not in ('Transactional',) and 'transaction' not in aq.lower():
                        continue
                    return an, aq, ann_args(aargs), al
            return None
        for m in list(idx.methods.values()):
            if m.pseudo or not m.has_body or m.is_ctor:
                continue
            td = idx.types.get(m.cls)
            if td is None:
                continue
            if any(a[0] == 'Async' for a in m.annots):
                self.async_methods.add(m.id)
            src = None
            a = tx_annot(m.annots)
            if a:
                src = ('method', a)
            else:
                # overridden methods in supertypes
                for s in idx.all_supers(m.cls):
                    std = idx.types.get(s)
                    if std is None:
                        continue
                    for om in std.methods.get(m.name, ()):
                        if len(om.params) == len(m.params):
                            a = tx_annot(om.annots)
                            if a:
                                src = ('super-method', a)
                                break
                    if src:
                        break
                if not src:
                    a = tx_annot(td.annots)
                    if a and ('public' in m.mods or td.kind == 'interface'):
                        src = ('class', a)
                    else:
                        for s in idx.all_supers(m.cls):
                            std = idx.types.get(s)
                            if std is None:
                                continue
                            a = tx_annot(std.annots)
                            if a and 'public' in m.mods:
                                # type-level annotation on a supertype applies to methods it declares (interfaces) or all (classes)
                                if std.kind == 'interface' and not std.methods.get(m.name):
                                    continue
                                src = ('super-class', a)
                                break
            if src:
                where, (an, aq, args, al) = src
                prop = args.get('propagation', args.get('value', '') if an == 'Transactional' and aq != 'Transactional' else '')
                prop = args.get('propagation', '')
                if an == 'TransactionAttribute' or (an == 'Transactional' and 'TxType' in args.get('value', '')):
                    prop = args.get('value', '')
                ptxt = prop.split('.')[-1] if prop else 'REQUIRED'
                ro = 'true' in args.get('readOnly', '')
                visible = 'public' in m.mods or 'protected' in m.mods or td.kind == 'interface'
                self.tx_attr[m.id] = {'annotation': an, 'source': where, 'propagation': ptxt or 'REQUIRED',
                                      'readOnly': ro, 'isolation': args.get('isolation', '').split('.')[-1] or None,
                                      'timeout': args.get('timeout'), 'rollbackFor': args.get('rollbackFor'),
                                      'manager': args.get('transactionManager') or (args.get('value') if args.get('value') and 'Propagation' not in args.get('value', '') and 'TxType' not in args.get('value', '') else None),
                                      'non_public': not visible}
        # framework methods that always run in a transaction: Spring Batch runs a tasklet's execute and each
        # chunk's read/process/write in a step transaction; plus app-specific roots (e.g., BenchBase procedures)
        builtin = [('Tasklet', 'execute'), ('ItemReader', 'read'), ('ItemProcessor', 'process'), ('ItemWriter', 'write')]
        for (supername, mname) in builtin + [tuple(x) for x in self.rules.get('procedure_roots', [])]:
            for td in idx.types.values():
                if td.kind == 'interface' or supername not in idx.super_names(td.fqn):
                    continue
                if (supername, mname) in builtin and not any(s.startswith('EXT:') and 'batch' in s for s in idx.all_supers(td.fqn)):
                    continue        # a project type that merely shares the name, not Spring Batch's interface
                for m in td.methods.get(mname, []):
                    if m.has_body and m.id not in self.tx_attr:
                        self.tx_attr[m.id] = {'annotation': 'app-rule', 'source': f'{supername}.{mname}',
                                              'propagation': 'REQUIRED', 'readOnly': False, 'isolation': None,
                                              'timeout': None, 'rollbackFor': None, 'manager': None,
                                              'non_public': False}

    def compute_entries(self):
        idx = self.idx
        for m in idx.methods.values():
            if m.pseudo or not m.has_body or m.is_ctor:
                continue
            td = idx.types.get(m.cls)
            if td is None:
                continue
            ann = {a[0] for a in m.annots}
            cann = {a[0] for a in td.annots}
            sup = idx.super_names(m.cls)
            kind = None
            web = ann & WEB_ANNOTS
            if web and (cann & {'Controller', 'RestController', 'RequestMapping', 'Path'} or ann & {'GET', 'POST', 'PUT', 'DELETE', 'PATCH'}):
                kind = 'web'
            elif web:
                kind = 'web'
            elif ann & {'Scheduled', 'SchedulerLock', 'XxlJob'}:
                kind = 'scheduled'
            elif ann & {'KafkaListener', 'RabbitListener', 'JmsListener', 'SqsListener', 'StreamListener', 'RabbitHandler'}:
                kind = 'message'
            elif ann & {'EventListener', 'TransactionalEventListener'}:
                kind = 'event'
            elif cann & ENTRY_CLASS_ANNOTS and m.name in ('onMessage', 'consume', 'handle'):
                kind = 'message'
            elif m.name == 'onMessage' and sup & {'RocketMQListener', 'MessageListener', 'RocketMQReplyListener', 'MessageListenerConcurrently'}:
                kind = 'message'
            elif m.name == 'execute' and sup & {'Job', 'QuartzJobBean', 'Tasklet', 'StatefulJob'}:
                kind = 'scheduled'
            elif m.name == 'executeInternal' and sup & {'QuartzJobBean'}:
                kind = 'scheduled'
            elif m.name in ('write',) and sup & {'ItemWriter'}:
                kind = 'batch'
            elif m.name in ('process',) and sup & {'ItemProcessor'}:
                kind = 'batch'
            elif m.name in ('doGet', 'doPost', 'doPut', 'doDelete', 'service') and sup & {'HttpServlet', 'GenericServlet'}:
                kind = 'web'
            elif sup & {'ActionSupport', 'UIAction'} and 'public' in m.mods and len(m.params) == 0 and base_type(m.ret_raw) == 'String':
                kind = 'web'
            elif m.name in ('preHandle', 'postHandle', 'afterCompletion') and sup & {'HandlerInterceptor', 'AsyncHandlerInterceptor', 'HandlerInterceptorAdapter', 'WebRequestInterceptor'}:
                kind = 'web'           # Spring MVC interceptors run on every matching request, outside the handler's transaction
            elif (m.name == 'doFilter' and sup & {'Filter', 'GenericFilterBean', 'HttpFilter'}) or \
                    (m.name == 'doFilterInternal' and sup & {'OncePerRequestFilter'}) or (m.name == 'filter' and sup & {'WebFilter'}):
                kind = 'web'           # servlet and WebFlux filters
            elif m.name == 'onApplicationEvent' and sup & {'ApplicationListener', 'SmartApplicationListener', 'GenericApplicationListener'}:
                kind = 'event'
            elif m.name == 'run' and sup & {'CommandLineRunner', 'ApplicationRunner'}:
                kind = 'startup'
            elif (ann & {'PostConstruct'}) or (m.name == 'afterPropertiesSet' and sup & {'InitializingBean'}):
                kind = 'startup'
            else:
                # JAX-RS annotations on implemented interface methods (e.g. Syncope, Fineract)
                for s in idx.all_supers(m.cls):
                    std = idx.types.get(s)
                    if std is None:
                        continue
                    for om in std.methods.get(m.name, ()):
                        if len(om.params) == len(m.params) and {a[0] for a in om.annots} & WEB_ANNOTS:
                            kind = 'web'
                            break
                    if kind:
                        break
            if kind:
                self.entry[m.id] = kind

    def compute_wrapper_roots(self):
        """Project methods that pass one of their parameters to a tx callback API are tx wrappers;
        callbacks given to them become tx roots (re-scan of call sites)."""
        sc = self.scan
        changed = True
        rounds = 0
        while changed and rounds < 3:
            changed = False
            rounds += 1
            for cur, recs in sc.calls.items():
                for rec in recs:
                    if rec.get('cls') != 'project' or not rec.get('callbacks'):
                        continue
                    targets = [rec.get('decl')] + rec.get('targets', [])
                    if any(t in sc.wrapper_params for t in targets if t):
                        for pid in rec['callbacks']:
                            if pid not in sc.callback_roots:
                                sc.callback_roots[pid] = {'api': 'wrapper:' + (rec.get('decl') or ''), 'site_method': cur,
                                                          'line': rec['line']}
                                changed = True
                        # also mark the callback edges as tx
                        lst = sc.callbacks.get(cur, [])
                        for i, (pid, mode, line) in enumerate(lst):
                            if pid in rec['callbacks'] and mode != 'tx':
                                lst[i] = (pid, 'tx', line)

    # --- traversal ------------------------------------------------------------------
    def is_separate_tx(self, target_id, rec):
        a = self.tx_attr.get(target_id)
        if not a:
            return None
        if rec.get('rkind') in ('implicit', 'this') and self.idx.methods.get(target_id) and \
                self.idx.methods[target_id].cls == self.idx.methods.get(rec.get('_caller', ''), self.idx.methods[target_id]).cls:
            return 'self-invocation'
        return a['propagation']

    def walk(self, root_id, mode='tx', max_nodes=6000, max_depth=40):
        """Collect everything reachable from root within the same transaction context."""
        idx, sc = self.idx, self.scan
        ops, nested, asyncs, effects, nondet, locks, txapis = [], [], [], [], [], [], []
        visited = {}
        unresolved = 0
        poly = 0
        skipped = {}  # (method, line, call) -> calls not followed: more than 8 targets, or no implementation found
        units = []  # for implicit mode
        op_index = {}  # (method, line, col, call, entity) -> op, to merge repeated visits of one call site
        parents = {root_id: None}  # method -> (caller method, call line) on the first (shortest) path from the root
        queue = collections.deque([(root_id, 0, False, False, None, (root_id,))])
        def ctx_entity_for(mid, ctx):
            if not ctx:
                return None
            ent, kind, target = ctx
            if kind == 'arg' and target == mid:
                return ent
            if kind == 'generic':
                m0 = idx.methods.get(mid)
                td0 = idx.types.get(m0.cls) if m0 else None
                if td0 is not None and td0.tparams:
                    return ent
                if m0 is not None and m0.pseudo:
                    return None
            return None
        nodes = 0
        while queue and nodes < max_nodes:
            mid, depth, ploop, pcond, ctxent, path = queue.popleft()
            # the entity context is part of the key: a generic DAO method reached for two entities is walked twice
            key = (mid, ploop, pcond, ctxent[0] if ctxent else None)
            if key in visited:
                continue
            visited[key] = depth
            nodes += 1
            meth = idx.methods.get(mid)
            if mid in sc.sync_methods:
                locks.append({'kind': 'synchronized-method', 'method': mid, 'line': meth.line if meth else None})
            for rec in sc.calls.get(mid, ()):
                loop = ploop or rec['loop']; cond = pcond or rec['cond']
                cls = rec['cls']
                if cls == 'db':
                    ent = rec.get('entity') or ctx_entity_for(mid, ctxent)
                    okey = (mid, rec['line'], rec.get('col'), rec['name'], ent)
                    prev = op_index.get(okey)
                    if prev is not None:
                        # same call site on the same entity reached again (other path or loop/branch context):
                        # one operation; in a loop if any path loops, conditional only if every path is
                        prev['loop'] = prev['loop'] or loop
                        prev['cond'] = prev['cond'] and cond
                        prev['depth'] = min(prev['depth'], depth)
                        continue
                    op = dict(op=rec.get('op'), api=rec.get('api'), method=mid, line=rec['line'], loop=loop,
                              cond=cond, catch=rec['catch'], depth=depth, entity=ent, sql=rec.get('sql'),
                              sql_kind=rec.get('sql_kind'), call=rec['name'], lockhint=rec.get('lockhint'),
                              table_hint=rec.get('table_hint'), libtx=rec.get('libtx', False), sync=rec['sync'],
                              rtype=rec.get('rtype'), decl=rec.get('decl'), mapper_xml=rec.get('mapper_xml'),
                              audited=rec.get('audited', False), history_hint=rec.get('history_hint'))
                    op_index[okey] = op
                    if mode == 'implicit':
                        units.append(('op', op))
                    ops.append(op)
                elif cls == 'txapi':
                    txapis.append({'what': rec.get('what'), 'api': rec.get('api'), 'method': mid, 'line': rec['line']})
                elif cls == 'lock':
                    locks.append({'kind': rec.get('lock_kind'), 'method': mid, 'line': rec['line'], 'what': rec.get('what')})
                elif cls == 'effect':
                    effects.append({'kind': rec.get('effect_kind'), 'detail': rec.get('detail'), 'method': mid, 'line': rec['line'], 'loop': loop})
                elif cls == 'nondet':
                    nondet.append({'detail': rec.get('detail'), 'method': mid, 'line': rec['line']})
                elif cls == 'async':
                    asyncs.append({'kind': rec.get('async_kind'), 'method': mid, 'line': rec['line']})
                elif cls == 'project':
                    tg = rec.get('targets', [])
                    if not tg:
                        if rec.get('decl') and not idx.methods.get(rec['decl']).has_body:
                            unresolved += 1
                            skipped.setdefault((mid, rec['line'], rec.get('name')), ('no implementation', rec.get('decl')))
                        continue
                    if len(tg) > 8:
                        poly += 1
                        skipped.setdefault((mid, rec['line'], rec.get('name')), (f"{len(tg)} possible targets", rec.get('decl')))
                        continue
                    rt = rec.get('rtype')
                    gent = sc.recv_entity(rt) if (rt and rt in idx.types) else None
                    tents = rec.get('target_ent') or {}
                    self_call = rec.get('rkind') in ('implicit', 'this', 'super')
                    for t in tg:
                        tm = idx.methods.get(t)
                        if tm is None:
                            continue
                        # entity binding for the callee, most specific evidence first
                        if gent:                                   # receiver type binds the entity
                            tent = (gent, 'generic', None)
                        elif self_call and ctxent and ctxent[1] == 'generic':
                            tent = ctxent                          # same object: keep the caller's binding
                        elif tents.get(t):                         # all selecting subtypes agree
                            tent = (tents[t], 'generic', None)
                        elif rec.get('arg_entity'):
                            tdt = idx.types.get(tm.cls)
                            ap = rec.get('arg_entity_pos', 0)
                            if tdt is not None and tdt.tparams and ap < len(tm.params) and base_type(tm.params[ap][1]) in tdt.tparams:
                                tent = (rec['arg_entity'], 'generic', None)   # argument binds the type variable
                            else:
                                tent = (rec['arg_entity'], 'arg', t)
                        elif ctxent and ctxent[1] == 'generic':
                            tent = ctxent
                        else:
                            tent = None
                        if t in self.async_methods:
                            asyncs.append({'kind': '@Async', 'method': mid, 'line': rec['line'], 'target': t})
                            continue
                        a = self.tx_attr.get(t)
                        selfinv = rec.get('rkind') in ('implicit', 'this') and tm.cls == (meth.cls if meth else None)
                        if a and not selfinv and t != root_id:
                            prop = a['propagation']
                            if mode == 'implicit':
                                units.append(('tx', {'target': t, 'line': rec['line'], 'method': mid, 'loop': loop, 'cond': cond}))
                                continue
                            if prop in ('REQUIRES_NEW', 'NOT_SUPPORTED', 'NEVER') or (prop in ('REQUIRED', 'SUPPORTS', 'MANDATORY', 'NESTED') and False):
                                nested.append({'target': t, 'propagation': prop, 'method': mid, 'line': rec['line'], 'loop': loop})
                                continue
                        if a and selfinv and mode == 'implicit' and t != root_id:
                            # Spring proxies ignore self-invocation: the callee runs without a transaction
                            pass
                        if depth + 1 <= max_depth:
                            if t not in parents:
                                parents[t] = (mid, rec['line'])
                            queue.append((t, depth + 1, loop, cond, tent, path + (t,) if len(path) < 6 else path))
            for (pid, cmode, line) in sc.callbacks.get(mid, ()):
                if cmode == 'async':
                    asyncs.append({'kind': 'async-callback', 'method': mid, 'line': line, 'target': pid})
                    continue
                if cmode == 'tx' and pid != root_id:
                    if mode == 'implicit':
                        units.append(('tx', {'target': pid, 'line': line, 'method': mid, 'loop': ploop, 'cond': pcond}))
                        continue
                    # nested programmatic tx callback: joins (REQUIRED) by default -> traverse
                    txapis.append({'what': 'nested-callback', 'api': 'callback', 'method': mid, 'line': line})
                if pid not in parents:
                    parents[pid] = (mid, line)
                queue.append((pid, depth + 1, ploop or cmode == 'loop', pcond or cmode == 'cond', ctxent, path))
        truncated = bool(queue) and nodes >= max_nodes
        return {'ops': ops, 'nested': nested, 'async': asyncs, 'effects': effects, 'nondet': nondet, 'locks': locks,
                'txapis': txapis, 'unresolved': unresolved, 'poly_skipped': poly, 'methods_visited': nodes,
                'truncated': truncated, 'units': units, 'parents': parents,
                'skipped': [{'method': k[0], 'line': k[1], 'call': k[2], 'why': v[0], 'decl': v[1]}
                            for k, v in sorted(skipped.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or ''))],
                'max_depth': max(visited.values()) if visited else 0}

    # --- tables -------------------------------------------------------------------------
    def op_tables(self, op):
        """Return (reads, writes, flags, label) for an op."""
        reads, writes, flags = set(), set(), []
        ent = op.get('entity')
        sql = op.get('sql')
        kind = op.get('sql_kind')
        if isinstance(sql, str) and sql.startswith('NAMED:'):
            nq = getattr(self, 'named_queries', {}).get(sql[6:])
            if nq:
                kind, sql = nq
                op = dict(op); op['sql'] = sql; op['sql_kind'] = kind
                if op.get('op') == 'R' and re.match(r'\s*(update|delete|insert)', sql, re.I):
                    op['op'] = 'W'
        o = op.get('op')
        # operations that touch no table of their own
        if op.get('call') == 'flush' and op.get('api') in ('jpa', 'hibernate'):
            flags.append('flush')          # writes whatever is dirty in the persistence context
        if isinstance(sql, str) and not sql.startswith('NAMED:'):
            low = sql.lower()
            if 'pg_advisory' in low or 'get_lock(' in low or 'sp_getapplock' in low:
                flags.append('advisory_lock')
            if re.match(r'\s*(create|alter|drop|truncate|comment\s+on|grant|revoke|vacuum|analyze|refresh\s+materialized|set\s|reset\s|show\s)', low):
                flags.append('ddl_or_admin')
            if re.search(r'\bnextval\s*\(|\bcurrval\s*\(|\bnext\s+value\s+for\b|\.nextval\b', low):
                flags.append('sequence')
        etable, ejoined = None, []
        if ent and ent in self.entities and 'flush' not in flags:
            etable = self.entities[ent]['table']
            ejoined = self.entities[ent].get('joined') or []
        is_jpql = kind == 'jpql'
        if sql and isinstance(sql, str) and not is_jpql and kind != 'sql' and op.get('api') in ('jpa-query', 'spring-data'):
            if not re.search(r'\bfrom\s+[a-z_]+\b', sql) and re.search(r'\bfrom\s+[A-Z]', sql):
                is_jpql = True
            elif kind is None and not sql.startswith('NAMED:'):
                _q, nn = jpql_entities(sql)
                is_jpql = bool(nn) and all(n in self.scan.entity_by_name for n in nn)
        if sql and isinstance(sql, str) and not sql.startswith('NAMED:'):
            if is_jpql:
                qop, names = jpql_entities(sql)
                tabs = set()
                for n in names:
                    f = self.scan.entity_for_name(n)
                    tabs.add(self.entities[f]['table'] if f else n)
                    if f:
                        tabs.update(self.entities[f].get('joined') or [])
                if o in ('W',) or qop in ('update', 'delete', 'insert'):
                    first = names[0] if names else None
                    if first:
                        f = self.scan.entity_for_name(first)
                        writes.add(self.entities[f]['table'] if f else first)
                        if f:
                            writes.update(self.entities[f].get('joined') or [])
                    reads |= tabs - writes
                else:
                    reads |= tabs
                if re.search(r'for\s+update', sql, re.I):
                    flags.append('for_update')
            else:
                sop, r, w, fl = sql_tables(sql)
                reads |= set(r); writes |= set(w); flags += fl
                if not r and not w and etable:
                    (writes if o == 'W' else reads).update([etable] + ejoined)
        elif op.get('table_hint'):
            (writes if o == 'W' else reads).add(op['table_hint'])
        elif etable:
            if o == 'W':
                writes.update([etable] + ejoined)
            elif o == 'L':
                reads.update([etable] + ejoined); flags.append('lock')
            else:
                reads.update([etable] + ejoined)
        if not reads and not writes and not (set(flags) & NO_TABLE_FLAGS):
            flags.append('unknown_table')
        # app wrappers that add writes to audited operations (Kill Bill: history table + audit_log, after re-reading the row)
        if op.get('audited') and self.rules.get('audited_writes'):
            if op.get('history_hint'):
                writes.add(op['history_hint'])
            writes.update(self.rules['audited_writes'])
            if op.get('table_hint'):
                reads.add(op['table_hint'])
            flags.append('audited')
        if op.get('lockhint') and ('PESSIMISTIC' in str(op['lockhint']) or 'UPGRADE' in str(op['lockhint'])):
            flags.append('pessimistic_lock')
        if o == 'L':
            flags.append('lock')
        reads = {norm_table(x) for x in reads if x}
        writes = {norm_table(x) for x in writes if x}
        reads.discard(''); writes.discard('')
        return reads, writes, flags

    def summarize(self, w):
        ops = w['ops']
        reads, writes, allflags = set(), set(), set()
        nR = nW = nU = nL = 0
        unknown_tables = 0
        opl = []
        for op in ops:
            r, wr, fl = self.op_tables(op)
            reads |= r; writes |= wr; allflags |= set(fl)
            if 'unknown_table' in fl:
                unknown_tables += 1
            o = op.get('op')
            nR += o == 'R'; nW += o == 'W'; nU += o == 'U'; nL += o == 'L'
            opl.append({'op': o, 'api': op.get('api'), 'call': op.get('call'), 'reads': sorted(r), 'writes': sorted(wr),
                        'flags': fl, 'loop': op['loop'], 'cond': op['cond'], 'depth': op['depth'],
                        'at': f"{simple(self.idx.methods[op['method']].cls) if op['method'] in self.idx.methods else '?'}:{op['line']}",
                        'entity': simple(op.get('entity')) if op.get('entity') else None,
                        'sql': (op.get('sql')[:300] if isinstance(op.get('sql'), str) else None)})
        versioned = sorted({simple(e) for e in self.entities if self.entities[e].get('versioned') and self.entities[e]['table'] in (writes | reads)})
        return {
            'n_ops': len(ops), 'n_reads': nR, 'n_writes': nW, 'n_unknown_op': nU, 'n_locks_db': nL,
            'tables_read': sorted(reads), 'tables_written': sorted(writes),
            'n_tables': len(reads | writes), 'ops_in_loops': sum(1 for o in ops if o['loop']),
            'writes_in_loops': sum(1 for o in ops if o['loop'] and o.get('op') == 'W'),
            'ops_conditional': sum(1 for o in ops if o['cond']),
            'ops_unknown_table': unknown_tables, 'db_flags': sorted(allflags),
            'versioned_entities': versioned, 'ops': opl,
        }


def label_of(idx, mid):
    m = idx.methods.get(mid)
    if m is None:
        return mid
    if m.pseudo:
        parent = idx.methods.get(m.parent)
        base = f"{simple(parent.cls)}.{parent.name}" if parent else m.cls
        return f"{base}${m.id.split('$', 1)[1].split('#')[0]}"
    return f"{simple(m.cls)}.{m.name}"


def tx_family(t):
    """The part of a transaction's kind that, with its root method, identifies it."""
    if t['kind'] == 'explicit':
        return t['subkind']
    if t['kind'] == 'adhoc':
        return 'annotation' if t['subkind'].startswith('annotation:') else 'construct'
    return t['kind']


def tx_key(t):
    """Stable id of a transaction (kind, family, root method fqn#name/arity@line). Unlike the running
    number in t['id'], it does not change when other transactions appear or disappear."""
    raw = f"{t['kind']}|{tx_family(t)}|{t['root_id']}"
    return hashlib.sha1(raw.encode('utf8')).hexdigest()[:12]


def prepare(app_dir, app, rules):
    """Index the code base, collect entities and DB call sites, and set up the transaction builder."""
    t0 = time.time()
    idx = Index(app_dir)
    java_files, xml_files, stg_files = [], [], []
    for dp, dns, fns in os.walk(app_dir):
        dns[:] = sorted(d for d in dns if d not in ('.git', 'node_modules', 'target', 'build', '.gradle', '.idea'))
        for fn in sorted(fns):
            rel = os.path.relpath(os.path.join(dp, fn), app_dir)
            if is_test_path(rel):
                continue
            # exclude_paths: 'dir/' = path prefix, anything else = substring
            if any((rel.startswith(x) if x.endswith('/') else x in rel) for x in rules.get('exclude_paths', [])):
                continue
            if fn.endswith('.java'):
                java_files.append(rel)
            elif fn.endswith('.xml'):
                xml_files.append(rel)
            elif fn.endswith('.stg'):
                stg_files.append(rel)
    sources = {}
    for rel in java_files:
        try:
            with open(os.path.join(app_dir, rel), 'rb') as fh:
                src = fh.read()
        except Exception:
            continue
        sources[rel] = src
        idx.n_files += 1
        idx.loc += src.count(b'\n')
        idx.index_file(rel, src)
    idx.finalize()
    idx.build_ext_impls()
    # entities
    cres = BodyScanner(idx, {}, {}, {}, rules)   # used only to evaluate String constants in annotations

    def const_str(expr, cls):
        """Value of a compile-time String expression such as "t_" + Names.ORDER, or None."""
        v = cres.const_expr(expr, cls)
        return None if (v is None or '?' in v) else v

    entities = {}
    unresolved_table_consts = []
    for td in idx.types.values():
        an = {a[0]: a for a in td.annots}
        if 'Entity' in an or 'TableName' in an or ('Table' in an and 'Entity' in an):
            name = td.name
            table = None
            if 'Entity' in an:
                ea = ann_args(an['Entity'][2])
                if ea.get('name'):
                    name = const_str(ea['name'], td.fqn) or name
            tv = None
            if 'Table' in an:
                ta = ann_args(an['Table'][2])
                tv = ta.get('name') or ta.get('value')
            if 'TableName' in an:
                ta = ann_args(an['TableName'][2])
                tv = ta.get('value') or ta.get('name') or (an['TableName'][2] if '=' not in an['TableName'][2] else None) or tv
            if tv:
                table = const_str(tv, td.fqn)
                if table is None:
                    unresolved_table_consts.append((td.fqn, tv))
            versioned = any(any(a[0] == 'Version' for a in f.annots) for f in td.fields.values())
            entities[td.fqn] = {'name': name, 'table': norm_table(table or name), 'table_declared': table is not None,
                                'versioned': versioned, 'jpa': 'Entity' in an}
            if 'Inheritance' in an:
                st = ann_args(an['Inheritance'][2]).get('strategy') or ''
                entities[td.fqn]['inheritance'] = ('JOINED' if 'JOINED' in st else
                                                   'TABLE_PER_CLASS' if 'TABLE_PER_CLASS' in st else 'SINGLE_TABLE')
    idx.unresolved_table_consts = unresolved_table_consts
    # inherited @Version via mapped superclasses
    for fqn in list(entities):
        if not entities[fqn]['versioned']:
            for s in idx.all_supers(fqn):
                std = idx.types.get(s)
                if std and any(any(a[0] == 'Version' for a in f.annots) for f in std.fields.values()):
                    entities[fqn]['versioned'] = True
                    break
    # MyBatis-Plus / tk.mybatis entities without @TableName: the type argument of BaseMapper<T>, IService<T>,
    # ServiceImpl<M, T>; default table name is the class name in snake case
    MP_ENT_ARG = {'BaseMapper': 0, 'IService': 0, 'ServiceImpl': 1, 'MPJBaseMapper': 0, 'MPJBaseService': 0,
                  'MPJBaseServiceImpl': 1}
    for td in list(idx.types.values()):
        for (r, args, raw) in td.supers:
            if not r:
                continue
            sn = simple(r)
            pos = MP_ENT_ARG.get(sn)
            if pos is None and sn == 'Mapper' and r.startswith('EXT:tk.mybatis'):
                pos = 0
            if pos is None or pos >= len(args):
                continue
            e = args[pos]
            if e and e in idx.types and e not in entities:
                etd = idx.types[e]
                versioned = any(any(a[0] == 'Version' for a in f.annots) for f in etd.fields.values())
                entities[e] = {'name': etd.name, 'table': norm_table(snake(etd.name)), 'table_declared': False,
                               'versioned': versioned}
    xml_ents, named_queries = parse_jpa_xml(app_dir, xml_files)
    for cls, e in xml_ents.items():
        if cls in idx.types and cls not in entities:
            entities[cls] = {'name': e['name'], 'table': norm_table(e['table']), 'table_declared': e.get('table_declared', True),
                             'versioned': e['versioned'], 'jpa': True}
            for k in ('inheritance', 'hbm_subclass'):
                if e.get(k):
                    entities[cls][k] = e[k]
    # entity inheritance: a subclass without a table of its own shares the root entity's table (SINGLE_TABLE, the
    # JPA default); a JOINED subclass has its own table and also reads and writes its ancestors' tables
    def entity_ancestors(fqn):
        out, c, seen = [], fqn, {fqn}
        while True:
            td0 = idx.types.get(c)
            nxt = None
            for (r, _a, _raw) in (td0.supers if td0 is not None else []):
                t2 = idx.types.get(r) if r else None
                if t2 is not None and t2.kind == 'class':
                    nxt = r
                    break
            if nxt is None or nxt in seen:
                return out
            seen.add(nxt)
            if nxt in entities and entities[nxt].get('jpa'):
                out.append(nxt)
            c = nxt
    for fqn in sorted(entities):
        e = entities[fqn]
        if not e.get('jpa'):
            continue
        anc = entity_ancestors(fqn)
        if not anc:
            continue
        root = anc[-1]
        strat = e.get('hbm_subclass') or entities[root].get('inheritance') or 'SINGLE_TABLE'
        if strat == 'SINGLE_TABLE' and not e['table_declared']:
            e['table'] = entities[root]['table']
            e['inherits'] = simple(root)
        elif strat == 'JOINED':
            e['joined'] = sorted({entities[x]['table'] for x in anc} - {e['table']})
    for td in idx.types.values():
        for (an, aq, aargs, al) in td.annots:
            if an in ('NamedQuery', 'NamedQueries', 'NamedNativeQuery', 'NamedNativeQueries'):
                for qm in re.finditer(r'@?(NamedQuery|NamedNativeQuery)?\s*\(?\s*name\s*=\s*"([^"]+)"\s*,\s*query\s*=\s*((?:"(?:[^"\\]|\\.)*"\s*\+?\s*)+)', aargs):
                    qtxt = ' '.join(re.findall(r'"((?:[^"\\]|\\.)*)"', qm.group(3)))
                    named_queries[qm.group(2)] = ('sql' if 'Native' in (qm.group(1) or an) else 'jpql', qtxt)
                    if '.' not in qm.group(2):
                        named_queries[td.name + '.' + qm.group(2)] = named_queries[qm.group(2)]
    mappers = parse_mybatis(app_dir, xml_files)
    stg = parse_stg(app_dir, stg_files)
    scan = BodyScanner(idx, entities, mappers, stg, rules)
    for rel, src in sources.items():
        tree = PARSER.parse(src)
        scan.scan_file(rel, src, tree)
    t1 = time.time()
    tb = TxBuilder(idx, scan, entities, app, rules)
    tb.named_queries = named_queries
    tb.compute_declarative()
    tb.compute_entries()
    tb.compute_wrapper_roots()
    return idx, scan, tb, entities, mappers, t0, t1


def run(app_dir, out_dir, app, commit, rules):
    idx, scan, tb, entities, mappers, t0, t1 = prepare(app_dir, app, rules)
    # ----- explicit transactions
    txs = []
    def mk_root(mid, kind, sub, attrs, extra=None):
        m = idx.methods.get(mid)
        w = tb.walk(mid)
        s = tb.summarize(w)
        rec = {'app': app, 'id': f"{app}:{len(txs)+1}", 'kind': kind, 'subkind': sub, 'root': label_of(idx, mid),
               'root_id': mid, 'file': m.file if m else None, 'line': m.line if m else None, 'attrs': attrs,
               'nested_tx_calls': w['nested'], 'async': w['async'], 'effects': w['effects'], 'nondet': w['nondet'],
               'app_locks': w['locks'], 'txapis': w['txapis'], 'unresolved_calls': w['unresolved'],
               'poly_skipped': w['poly_skipped'], 'methods_visited': w['methods_visited'], 'truncated': w['truncated'],
               'max_call_depth': w['max_depth']}
        rec.update(s)
        if extra:
            rec.update(extra)
        txs.append(rec)
        return rec
    for mid, a in sorted(tb.tx_attr.items(), key=lambda kv: kv[0]):
        sub = 'declarative' if a['annotation'] != 'app-rule' else 'app-rule'
        mk_root(mid, 'explicit', sub, a)
    for pid, info in sorted(scan.callback_roots.items()):
        mk_root(pid, 'explicit', 'programmatic-callback', {'api': info['api'], 'site': label_of(idx, info['site_method']) + f":{info['line']}"})
    cp_names = {m for (t, m) in rules.get('commit_points', [])}
    for mid, lst in sorted(scan.region_roots.items()):
        if mid in tb.tx_attr:
            continue
        mm = idx.methods.get(mid)
        if mm is not None and mm.name in cp_names and all(w == 'commit-point' for (a, w, l) in lst):
            continue
        apis = sorted({f"{a}:{w}" for (a, w, l) in lst})
        mk_root(mid, 'explicit', 'programmatic-region', {'api': ','.join(apis)})
    # ----- implicit transactions (entry points without an enclosing transaction)
    by_root = {t['root_id']: t for t in txs}
    for t in txs:
        t['invoked_by'] = []
    implicit = []
    for mid, ekind in sorted(tb.entry.items()):
        if mid in tb.tx_attr:
            by_root[mid]['invoked_by'].append(label_of(idx, mid) + f" [{ekind}]")
            continue
        w = tb.walk(mid, mode='implicit')
        units = []   # (kind, op R/W, reads, writes, loop, ref)
        for kind, u in w['units']:
            if kind == 'op':
                r, wr, fl = tb.op_tables(u)
                units.append(('op', 'W' if u.get('op') == 'W' else ('R' if u.get('op') in ('R', 'L') else 'U'),
                              set(r), set(wr), u.get('loop'), None))
            else:
                t = by_root.get(u['target'])
                if t is None:
                    continue
                lab = label_of(idx, mid) + f" [{ekind}]"
                if lab not in t['invoked_by']:
                    t['invoked_by'].append(lab)
                units.append(('tx', 'W' if t['n_writes'] else 'R', set(t['tables_read']), set(t['tables_written']),
                              u.get('loop'), t['id']))
        op_units = [u for u in units if u[0] == 'op']
        tx_units = [u for u in units if u[0] == 'tx']
        write_units = sum((2 if u[4] else 1) for u in units if u[1] == 'W' or u[3])
        rmw = False
        for i, x in enumerate(units):
            if x[1] != 'R':
                continue
            for j, y in enumerate(units):
                if i != j and (y[1] == 'W' or y[3]) and ((x[2] | x[3]) & (y[2] | y[3])):
                    rmw = True
                    break
            if rmw:
                break
        if not op_units and write_units < 2:
            continue
        if write_units >= 2:
            sub = 'multi-write'
        elif rmw:
            sub = 'read-then-write'
        elif write_units == 1:
            sub = 'single-write'
        else:
            sub = 'read-only'
        s = tb.summarize(w)
        m = idx.methods.get(mid)
        rec = {'app': app, 'id': f"{app}:{len(txs)+1}", 'kind': 'implicit', 'subkind': f"{ekind}/{sub}",
               'root': label_of(idx, mid), 'root_id': mid, 'file': m.file, 'line': m.line, 'attrs': {},
               'units_total': len(units), 'units_autocommit_ops': len(op_units), 'units_explicit_tx': len(tx_units),
               'explicit_tx_called': sorted({u[5] for u in tx_units}), 'write_units': write_units,
               'nested_tx_calls': [], 'async': w['async'], 'effects': w['effects'], 'nondet': w['nondet'],
               'app_locks': w['locks'], 'txapis': w['txapis'], 'unresolved_calls': w['unresolved'],
               'poly_skipped': w['poly_skipped'], 'methods_visited': w['methods_visited'],
               'truncated': w['truncated'], 'max_call_depth': w['max_depth'], 'invoked_by': [label_of(idx, mid) + f" [{ekind}]"]}
        rec.update(s)
        rec['signature'] = '|'.join(sorted({f"{o['op']}:{o['call']}@{o['at']}" for o in s['ops']}) + sorted({u[5] for u in tx_units if u[5]}))
        # include tables touched by the explicit transactions this entry calls
        for u in tx_units:
            rec['tables_read'] = sorted(set(rec['tables_read']) | u[2])
            rec['tables_written'] = sorted(set(rec['tables_written']) | u[3])
        rec['n_tables'] = len(set(rec['tables_read']) | set(rec['tables_written']))
        txs.append(rec)
        implicit.append(rec['id'])
    # ----- ad hoc transactions: app-level coordination constructs guarding DB operations
    by_method = collections.defaultdict(list)
    for ls in scan.lock_sites:
        by_method[ls['method']].append(ls)
    for mid in scan.sync_methods:
        m = idx.methods.get(mid)
        by_method[mid].append({'method': mid, 'kind': 'synchronized-method', 'line': m.line if m else None, 'end': None, 'detail': ''})
    for mid, sites in sorted(by_method.items()):
        kinds = sorted({s['kind'] for s in sites if s.get('detail') != 'release'})
        if not kinds:
            continue
        w = tb.walk(mid)
        if not w['ops']:
            continue
        s = tb.summarize(w)
        m = idx.methods.get(mid)
        inside_tx = mid in tb.tx_attr or mid in scan.callback_roots
        rec = {'app': app, 'id': f"{app}:{len(txs)+1}", 'kind': 'adhoc', 'subkind': ','.join(kinds),
               'root': label_of(idx, mid), 'root_id': mid, 'file': m.file if m else None,
               'line': m.line if m else None, 'attrs': {'inside_db_tx': inside_tx,
                                                       'sites': [f"{x['kind']}@{x['line']}" for x in sites][:10]},
               'nested_tx_calls': w['nested'], 'async': w['async'], 'effects': w['effects'], 'nondet': w['nondet'],
               'app_locks': w['locks'], 'txapis': w['txapis'], 'unresolved_calls': w['unresolved'],
               'poly_skipped': w['poly_skipped'], 'methods_visited': w['methods_visited'], 'truncated': w['truncated'],
               'max_call_depth': w['max_depth']}
        rec.update(s)
        txs.append(rec)
    # custom lock annotations (e.g. @DistributedLock, @RedisLock, @Idempotent)
    for m in list(idx.methods.values()):
        if m.pseudo or not m.has_body:
            continue
        la = [a for a in m.annots if re.search(r'(Lock|Idempotent|Lockable|RateLimit|Synchronized|NoRepeatSubmit)$', a[0]) and a[0] not in ('Lock',)]
        if not la:
            continue
        w = tb.walk(m.id)
        if not w['ops']:
            continue
        s = tb.summarize(w)
        rec = {'app': app, 'id': f"{app}:{len(txs)+1}", 'kind': 'adhoc', 'subkind': 'annotation:' + ','.join(a[0] for a in la),
               'root': label_of(idx, m.id), 'root_id': m.id, 'file': m.file, 'line': m.line,
               'attrs': {'inside_db_tx': m.id in tb.tx_attr, 'annotation_args': [a[2][:120] for a in la]},
               'nested_tx_calls': w['nested'], 'async': w['async'], 'effects': w['effects'], 'nondet': w['nondet'],
               'app_locks': w['locks'], 'txapis': w['txapis'], 'unresolved_calls': w['unresolved'],
               'poly_skipped': w['poly_skipped'], 'methods_visited': w['methods_visited'], 'truncated': w['truncated'],
               'max_call_depth': w['max_depth']}
        rec.update(s)
        txs.append(rec)
    # stable keys, used by the review files
    seen_keys = collections.Counter()
    for t in txs:
        k = tx_key(t)
        seen_keys[k] += 1
        t['key'] = k if seen_keys[k] == 1 else f"{k}-{seen_keys[k]}"
    t2 = time.time()
    # ----- app statistics
    n_methods = sum(1 for m in idx.methods.values() if not m.pseudo)
    dbcalls = sum(1 for recs in scan.calls.values() for r in recs if r['cls'] == 'db')
    stats = {
        'app': app, 'commit': commit, 'java_files': idx.n_files, 'loc': idx.loc, 'types': len(idx.types),
        'methods': n_methods, 'parse_error_files': idx.parse_errors, 'entities': len(entities),
        'entity_table_consts_unresolved': [f"{simple(c)}={v}" for (c, v) in getattr(idx, 'unresolved_table_consts', [])][:20],
        'versioned_entities': sum(1 for e in entities.values() if e['versioned']),
        'mybatis_namespaces': len(mappers), 'mybatis_statements': sum(len(v) for v in mappers.values()),
        'db_call_sites': dbcalls, 'entry_points': len(tb.entry),
        'entry_points_by_kind': dict(collections.Counter(tb.entry.values())),
        'tx_declarative': sum(1 for t in txs if t['kind'] == 'explicit' and t['subkind'] in ('declarative', 'app-rule')),
        'tx_programmatic': sum(1 for t in txs if t['kind'] == 'explicit' and t['subkind'].startswith('programmatic')),
        'tx_implicit': sum(1 for t in txs if t['kind'] == 'implicit'),
        'tx_adhoc': sum(1 for t in txs if t['kind'] == 'adhoc'),
        'lock_sites': len(scan.lock_sites), 'sync_methods': len(scan.sync_methods),
        'seconds_index_scan': round(t1 - t0, 1), 'seconds_tx': round(t2 - t1, 1),
        'db_api_mix': dict(collections.Counter(r.get('api') for recs in scan.calls.values() for r in recs if r['cls'] == 'db')),
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{app}.json"), 'w') as fh:
        json.dump({'stats': stats, 'transactions': txs,
                   'entities': {simple(k): v for k, v in entities.items()}}, fh, indent=1, default=str)
    return stats, txs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('app_dir')
    ap.add_argument('out_dir')
    ap.add_argument('--app', default=None)
    ap.add_argument('--commit', default='')
    ap.add_argument('--rules', default='{}')
    a = ap.parse_args()
    app = a.app or os.path.basename(os.path.normpath(a.app_dir))
    rules = json.loads(a.rules)
    stats, txs = run(a.app_dir, a.out_dir, app, a.commit, rules)
    print(json.dumps(stats, indent=1))


if __name__ == '__main__':
    main()
