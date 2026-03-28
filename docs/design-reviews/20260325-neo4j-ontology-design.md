# Neo4j 온톨로지 그래프 통합 설계서

**작성일:** 2026-03-25
**대상:** Data Copilot — SQL 온톨로지 그래프 (테이블 관계, 업무 규칙, JOIN 경로, 계수산출식)
**배포 환경:** RHEL 9.4 폐쇄망

---

## 1. 인프라 구성

### 1.1 Neo4j 서버 설치 (RHEL 9.4 오프라인)

**사전 준비 (인터넷 환경에서):**
```bash
# Java 17 + Neo4j CE 5.x RPM 다운로드
wget https://download.oracle.com/java/17/latest/jdk-17_linux-x64_bin.rpm
# 또는 OpenJDK
dnf download --resolve java-17-openjdk-headless

# Neo4j Community Edition RPM
# https://neo4j.com/deployment-center/ 에서 다운로드
```

**폐쇄망 설치:**
```bash
# 1. Java 17 설치
sudo rpm -ivh java-17-openjdk*.rpm

# 2. Neo4j CE 설치
sudo rpm -ivh neo4j-community-5*.rpm

# 3. 서비스 등록 및 시작
sudo systemctl enable --now neo4j

# 4. 초기 비밀번호 설정
neo4j-admin dbms set-initial-password <password>
```

### 1.2 Neo4j 서버 설정 (`/etc/neo4j/neo4j.conf`)

온톨로지 그래프 규모 (5K 테이블 + 100K 컬럼 + 관계) 기준:

```properties
# ── 메모리 ──
server.memory.heap.initial_size=1g
server.memory.heap.max_size=2g
server.memory.pagecache.size=1g

# ── 네트워크 ──
server.default_listen_address=0.0.0.0    # 또는 내부 IP 바인딩
server.bolt.listen_address=:7687
server.http.listen_address=:7474

# ── 커넥션 풀 ──
server.bolt.connection_keep_alive=30s
server.bolt.connection_keep_alive_for_requests=ALL

# ── 안전장치 ──
db.transaction.timeout=30s
dbms.security.auth_enabled=true

# ── 로깅 ──
server.directories.logs=/var/log/neo4j
```

**메모리 산정 근거:**

| 항목 | 예상 크기 | 설명 |
|------|----------|------|
| Table 노드 | ~5,000 | 정보계 전체 테이블 |
| Column 노드 | ~100,000 | 테이블당 평균 20컬럼 |
| DomainConcept 노드 | ~1,000 | 금융 지표, 업무 용어 |
| CodeDefinition 노드 | ~20,000 | 코드값 정의 |
| 관계(엣지) | ~150,000 | FK, 소속, 매핑 등 |
| **총계** | **~276,000** | Neo4j CE 단일 노드로 충분 |

이 규모는 전체 그래프가 메모리에 적재되며, 경로 탐색이 1~5ms 이내에 완료된다.

### 1.3 Python 의존성 (`pyproject.toml`)

```toml
dependencies = [
    # ... 기존 의존성 ...
    "neo4j>=5.20.0",          # Neo4j Python async 드라이버 (Bolt 프로토콜)
]
```

`neo4j` 패키지는 순수 Python이므로 RHEL에서 C 컴파일 불필요. 오프라인 설치 시 `uv pip download neo4j`로 wheel을 사전 확보.

---

## 2. 커넥터 구현

### 2.1 설정 추가 (`src/config.py`)

```python
# ── Neo4j (온톨로지 그래프) ──
neo4j_host: str = "localhost"
neo4j_port: int = 7687
neo4j_user: str = "neo4j"
neo4j_password: str = ""
neo4j_database: str = "neo4j"
neo4j_pool_size: int = 10
neo4j_request_timeout: int = 10    # Cypher 실행 타임아웃 (초)
neo4j_cache_ttl: int = 300         # 온톨로지 캐시 TTL (초)
neo4j_max_path_hops: int = 4       # JOIN 경로 최대 홉 수
neo4j_batch_size: int = 500        # 시딩 배치 크기
neo4j_ingest_min_confidence: float = 0.7  # 자동 수집 최소 확신도
```

### 2.2 커넥터 클래스 (`src/connectors/impl/neo4j_connector.py`)

기존 `MongoConnector` 패턴(SearchConnector 인터페이스, Dummy 모드, lazy import, search dispatch)을 따른다.

```python
"""Neo4j 온톨로지 그래프 커넥터 — 테이블 관계/업무 규칙/JOIN 경로 탐색.

5종의 검색을 제공한다:
  - search_join_paths: 두 테이블 간 최단 JOIN 경로 탐색
  - search_table_relations: 특정 테이블의 직접 연결 관계 조회
  - search_domain_tables: 도메인 개념에서 관련 테이블 + FK 이웃 확장
  - search_formula: 계수산출식 재귀 분해
  - search_code_hierarchy: 코드값 계층 + 적용 컬럼 조회

Dummy 모드: use_dummy=True일 때 Neo4j 연결 없이 샘플 온톨로지 데이터를 반환한다.
"""

class Neo4jConnector(SearchConnector):

    async def connect(self) -> None:
        """Neo4j 연결 초기화 (lazy import)."""
        if self._use_dummy:
            return
        from neo4j import AsyncGraphDatabase
        self._driver = AsyncGraphDatabase.driver(
            f"bolt://{settings.neo4j_host}:{settings.neo4j_port}",
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=settings.neo4j_pool_size,
        )

    async def search_join_paths(
        self, source_table: str, target_table: str, max_hops: int = 4,
    ) -> list[dict]:
        """두 테이블 간 최단 JOIN 경로를 탐색한다."""
        cypher = """
        MATCH path = shortestPath(
            (a:Table {name: $source})-[:FK_TO*1..$max_hops]-(b:Table {name: $target})
        )
        RETURN [n IN nodes(path) | n.name] AS tables,
               [r IN relationships(path) |
                   {from_col: r.from_column, to_col: r.to_column,
                    join_type: r.join_type, confidence: r.confidence}
               ] AS joins
        """

    async def search_domain_tables(
        self, concept_name: str,
    ) -> list[dict]:
        """도메인 개념에서 관련 테이블 + FK 이웃을 확장 탐색한다."""
        cypher = """
        MATCH (c:DomainConcept)-[res:RESOLVED_BY]->(t:Table)
        WHERE c.name CONTAINS $concept OR ANY(syn IN c.synonyms WHERE syn CONTAINS $concept)
        OPTIONAL MATCH (t)-[fk:FK_TO*1..2]-(neighbor:Table)
        RETURN t.name AS table_name, t.alt_name AS alt_name,
               t.granularity AS granularity, t.refresh_cycle AS refresh_cycle,
               res.role AS role,
               collect(DISTINCT neighbor.name) AS joinable_tables
        """

    async def search_formula(
        self, formula_name: str, max_depth: int = 5,
    ) -> list[dict]:
        """계수산출식을 재귀 분해하여 컬럼/테이블까지 매핑한다."""
        cypher = """
        MATCH path = (root:DomainConcept {name: $name})
                     -[:COMPOSED_OF*1..$depth]->(leaf:DomainConcept)
        OPTIONAL MATCH (leaf)-[m:MEASURED_BY]->(col:Column)-[:BELONGS_TO]->(t:Table)
        RETURN root.name AS formula_name,
               root.definition AS formula_text,
               [n IN nodes(path) | {
                   name: n.name,
                   definition: n.definition
               }] AS decomposition_path,
               collect({
                   component: leaf.name,
                   column: col.name,
                   table: t.name,
                   agg_function: m.agg_function,
                   position: last(relationships(path)).position,
                   operator: last(relationships(path)).operator
               }) AS leaf_mappings
        """

    async def search_code_hierarchy(
        self, code_name: str,
    ) -> list[dict]:
        """코드값 계층 + 적용 컬럼/테이블을 조회한다."""
        cypher = """
        MATCH (code:CodeDefinition)
        WHERE code.code_name CONTAINS $code_name
              OR code.code_field CONTAINS $code_name
        OPTIONAL MATCH (code)-[:APPLIES_TO]->(col:Column)-[:BELONGS_TO]->(t:Table)
        OPTIONAL MATCH (code)-[:PARENT_OF*0..2]->(child:CodeDefinition)
        RETURN code.code_field, code.code_value, code.code_name,
               col.name AS column_name, t.name AS table_name,
               collect(DISTINCT {value: child.code_value, name: child.code_name}) AS children
        """

    async def _execute_cypher(
        self, cypher: str, params: dict | None = None,
    ) -> list[dict]:
        """공통 Cypher 실행 헬퍼 — 타이밍 로그 + 캐시 체크."""
```

### 2.3 커넥터 매니저 등록 (`src/connectors/manager.py`)

```python
from src.connectors.impl.neo4j_connector import Neo4jConnector

class ConnectorManager:
    def __init__(self, use_dummy: bool = True):
        # ... 기존 커넥터들 ...
        self.neo4j = Neo4jConnector(use_dummy=use_dummy)

    async def connect_all(self):
        # ... 기존 + ...
        await self.neo4j.connect()

    async def disconnect_all(self):
        # ... 기존 + ...
        await self.neo4j.disconnect()

    async def health_check_all(self):
        # ... 기존 + ...
        results["neo4j"] = await self.neo4j.health_check()
```

---

## 3. 온톨로지 그래프 스키마

### 3.1 노드 타입 (6종)

```cypher
// ── 스키마 제약조건 ──
CREATE CONSTRAINT table_name_unique IF NOT EXISTS
  FOR (t:Table) REQUIRE t.name IS UNIQUE;

CREATE CONSTRAINT domain_concept_unique IF NOT EXISTS
  FOR (d:DomainConcept) REQUIRE d.name IS UNIQUE;

CREATE CONSTRAINT subject_area_unique IF NOT EXISTS
  FOR (s:SubjectArea) REQUIRE s.name IS UNIQUE;

// ── 검색 인덱스 ──
CREATE FULLTEXT INDEX table_text_search IF NOT EXISTS
  FOR (t:Table) ON EACH [t.name, t.alt_name];

CREATE FULLTEXT INDEX concept_text_search IF NOT EXISTS
  FOR (d:DomainConcept) ON EACH [d.name, d.definition];

CREATE INDEX column_table_idx IF NOT EXISTS
  FOR (c:Column) ON (c.table_name);
```

| 노드 라벨 | 핵심 속성 | 원천 데이터 |
|-----------|----------|-----------|
| `Table` | name, alt_name, subject_area, granularity, refresh_cycle, entity_scope, db_source | MongoDB dpasset_table |
| `Column` | name, alt_name, data_type, table_name, is_pk, is_pii | MongoDB dpasset_column |
| `DomainConcept` | name, definition, synonyms[], category | 업무매뉴얼 + 수동 등록 |
| `CodeDefinition` | code_field, code_value, code_name | MongoDB standard_code + standard_code_value |
| `SubjectArea` | name, description | 테이블 그룹핑에서 추론 |
| `QueryCondition` | pattern, meaning, example_sql | 자동 수집 (신규) |

### 3.2 엣지 타입 (9종)

| 엣지 | 방향 | 속성 | 용도 |
|------|------|------|------|
| `BELONGS_TO` | Column → Table | — | 테이블-컬럼 소속 |
| `FK_TO` | Table → Table | from_column, to_column, join_type, confidence | **JOIN 경로 탐색** |
| `IN_AREA` | Table → SubjectArea | — | 주제영역 분류 |
| `RESOLVED_BY` | DomainConcept → Table | role (PRIMARY/DIMENSION) | **용어→테이블 매핑** |
| `MEASURED_BY` | DomainConcept → Column | agg_function | **지표→컬럼 매핑** |
| `COMPOSED_OF` | DomainConcept → DomainConcept | operator, position | **계수산출식 분해** |
| `APPLIES_TO` | CodeDefinition → Column | — | 코드→컬럼 바인딩 |
| `PARENT_OF` | CodeDefinition → CodeDefinition | — | 코드 계층 |
| `IMPLIES_CONDITION` | QueryCondition → Column | operator, value | **쿼리 조건 규칙** |

### 3.3 예시 데이터 시각화

```
(:SubjectArea {name:"여신_잔액"})
    ↑ IN_AREA
(:Table {name:"TB_LN_BAL_D", alt_name:"여신잔액일별", granularity:"일별"})
    │
    ├──FK_TO {from:"CUST_NO", to:"CUST_NO"}──→ (:Table {name:"TB_CUST_INFO"})
    ├──FK_TO {from:"BR_CD", to:"BR_CD"}──→ (:Table {name:"TB_BRANCH_M"})
    │
    ├──BELONGS_TO←── (:Column {name:"OVRD_PRINC_AMT", alt_name:"연체원금"})
    │                      ↑ MEASURED_BY {agg:"SUM"}
    │                 (:DomainConcept {name:"연체원금"})
    │                      ↑ COMPOSED_OF {position:"NUMERATOR", operator:"DIVIDE"}
    │                 (:DomainConcept {name:"연체율", definition:"연체원금/여신잔액×100"})
    │                      │ COMPOSED_OF {position:"DENOMINATOR", operator:"DIVIDE"}
    │                      ↓
    │                 (:DomainConcept {name:"여신잔액"})
    │                      │ MEASURED_BY {agg:"SUM"}
    │                      ↓
    └──BELONGS_TO←── (:Column {name:"LOAN_BAL_AMT", alt_name:"여신잔액"})

(:Column {name:"STAT_CD"})
    ↑ APPLIES_TO
(:CodeDefinition {code_field:"STAT_CD", code_value:"03", code_name:"연체"})
    ↑ APPLIES_TO
(:QueryCondition {pattern:"STAT_CD = '03'", meaning:"연체 상태 필터"})
```

---

## 4. 시딩 전략

### 4.1 3단계 시딩 파이프라인 (`devtools/scripts/seed_neo4j.py`)

```
Phase 1: MongoDB → Neo4j 노드 생성 (구조적 데이터)
Phase 2: 관계 추론 (FK, 코드 바인딩, 주제영역)
Phase 3: 업무 규칙 추출 (Qdrant 매뉴얼 + LLM)
```

#### Phase 1 — MongoDB 기반 노드 생성

| 원천 컬렉션 | → Neo4j 노드 | 엣지 |
|------------|-------------|------|
| dpasset_table | (:Table) | — |
| dpasset_column | (:Column) | (:Column)-[:BELONGS_TO]->(:Table) |
| standard_code | (:CodeDefinition) 필드 레벨 | — |
| standard_code_value | (:CodeDefinition) 값 레벨 | (:CodeDef)-[:PARENT_OF]->(:CodeDef) |
| glossary | (:DomainConcept) 초기 | (:DomainConcept)-[:RESOLVED_BY]->(:Table) |

```cypher
// 예: 테이블 노드 배치 생성
UNWIND $batch AS row
MERGE (t:Table {name: row.name})
SET t.alt_name = row.alt_name,
    t.schema_name = row.schema_name,
    t.db_source = row.db_source,
    t.update_cycle = coalesce(row.update_cycle, "")
```

#### Phase 2 — 관계 추론

**(a) FK 기반 JOIN 경로 추론**

MongoDB 메타에 명시적 FK 정보가 없는 경우, 컬럼 명명 규칙에서 추론:

```python
# 추론 규칙:
# 1. 컬럼명이 동일하고 한쪽이 PK → FK_TO (confidence: 0.9)
# 2. 컬럼명이 _NO, _ID, _CD 접미사이고 다른 테이블에 동일명 PK 존재 → FK_TO (confidence: 0.7)
# 3. SQL 이력에서 JOIN 패턴이 반복 확인 → FK_TO (confidence: 0.8)
```

```cypher
// 추론된 FK 관계 생성
MATCH (a:Table {name: $source}), (b:Table {name: $target})
MERGE (a)-[r:FK_TO]->(b)
SET r.from_column = $from_col,
    r.to_column = $to_col,
    r.join_type = "INNER",
    r.confidence = $confidence,
    r.evidence = $evidence
```

**(b) 코드 컬럼 바인딩**

```python
# 추론 규칙:
# 1. 컬럼명과 코드 물리명이 완전 일치 → APPLIES_TO (예: STAT_CD = STAT_CD)
# 2. 컬럼 alt_name에 "코드", "구분" 포함 + 유사 코드명 존재 → APPLIES_TO
```

**(c) 주제영역 군집화**

```python
# 추론 규칙:
# 1. 테이블명 접두사 패턴: TB_LN_* → "여신", TB_DP_* → "수신"
# 2. schema_name 기반 그룹핑
# 3. 동일 접두사 테이블의 접미사로 입도 구분: _D(일별), _M(월별), _HIST(이력)
```

#### Phase 3 — 업무 규칙 추출 (LLM 지원)

Qdrant의 `biz_manual` 컬렉션에서 계수산출식 관련 문서를 검색하고,
LLM으로 구조화된 산출식을 추출하여 `DomainConcept` + `COMPOSED_OF` 관계를 생성한다.

```python
# 추출 프롬프트 (예시):
"""
다음 업무매뉴얼 텍스트에서 계수산출식을 추출하세요.

텍스트: "연체율은 납기가 지난 대출액(연체원금)을 전체 대출잔액(여신잔액)으로 나눈 비율이다."

JSON 출력:
{
  "formula_name": "연체율",
  "definition": "연체원금 / 여신잔액 × 100",
  "components": [
    {"name": "연체원금", "position": "NUMERATOR", "operator": "DIVIDE"},
    {"name": "여신잔액", "position": "DENOMINATOR", "operator": "DIVIDE"}
  ]
}
"""
```

### 4.2 실행 방법

```bash
# Phase 1+2 (MongoDB 데이터 기반, LLM 불필요)
python -m devtools.scripts.seed_neo4j --phases 1,2

# Phase 3 (LLM 필요)
python -m devtools.scripts.seed_neo4j --phases 3

# 전체 재시딩 (idempotent — MERGE 사용)
python -m devtools.scripts.seed_neo4j --phases 1,2,3 --full-reset
```

---

## 5. 프로덕션 최적화

### 5.1 인메모리 캐시

온톨로지 데이터는 변경 빈도가 낮으므로, 커넥터 내부에 TTL 캐시를 둔다:

```python
class Neo4jConnector:
    def __init__(self, ...):
        self._cache: dict[str, tuple[float, list]] = {}

    async def _execute_cypher_cached(self, cache_key, cypher, params):
        now = time.time()
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < settings.neo4j_cache_ttl:
                return data
        result = await self._execute_cypher(cypher, params)
        self._cache[cache_key] = (now, result)
        return result
```

| 쿼리 유형 | 캐시 적용 | TTL | 사유 |
|----------|----------|-----|------|
| search_join_paths | O | 5분 | FK 관계는 거의 변하지 않음 |
| search_domain_tables | O | 5분 | 도메인 매핑 안정적 |
| search_formula | O | 5분 | 산출식 변경 빈도 낮음 |
| search_code_hierarchy | O | 5분 | 코드 체계 안정적 |
| 자동 수집 쓰기 | X | — | 쓰기는 캐시 불필요 |

### 5.2 인덱스 최적화

```cypher
// 경로 탐색 성능을 위한 관계 속성 인덱스 (Neo4j 5.x)
CREATE INDEX fk_confidence_idx IF NOT EXISTS
  FOR ()-[r:FK_TO]-() ON (r.confidence);

// 도메인 개념 동의어 검색
CREATE FULLTEXT INDEX concept_synonym_search IF NOT EXISTS
  FOR (d:DomainConcept) ON EACH [d.name, d.definition];

// 코드값 검색
CREATE INDEX code_value_idx IF NOT EXISTS
  FOR (c:CodeDefinition) ON (c.code_field, c.code_value);
```

### 5.3 쿼리 안전장치

```python
# 경로 탐색 최대 홉 수 제한 (밀집 그래프에서의 성능 보호)
max_hops = min(requested_hops, settings.neo4j_max_path_hops)

# 트랜잭션 타임아웃
async with self._driver.session(
    database=settings.neo4j_database,
    default_access_mode=neo4j.READ_ACCESS,     # 읽기 전용
) as session:
    result = await session.run(
        cypher, params,
        timeout=settings.neo4j_request_timeout,
    )
```

---

## 6. 런타임 자동 수집 시스템

### 6.1 아키텍처

```
파이프라인 실행 중 새로운 지식 발견
    │
    ├─ context_explorer: 유사 SQL에서 새 JOIN 패턴 발견
    ├─ sql_validator: 검증 통과한 SQL에서 JOIN 확인
    ├─ 사용자/관리자: 업무 규칙 텍스트 직접 입력
    │
    ▼
OntologyIngestor
    │
    ├─ 규칙 유형 분류
    ├─ LLM 구조화 파싱 (비정형 텍스트 → 구조화 규칙)
    ├─ 확신도 게이트 (< 0.7이면 로깅만, DB 반영 안 함)
    │
    ▼
Neo4j MERGE (idempotent 반영)
```

### 6.2 자동 수집 유형별 처리

#### 유형 1: 업무 설명 → 계수산출식 자동 반영

입력:
```
"연체율은 납기가 지난 대출액을 대출총액으로 나눈 값"
```

**LLM 파싱 프롬프트 (`resources/prompts/reason/ontology_formula_extraction.txt`):**
```
당신은 금융 업무 규칙을 그래프 온톨로지 구조로 변환하는 전문가입니다.

## 입력
업무 설명 텍스트가 주어집니다.

## 출력 규칙
1. 계수산출식이면 components로 분해하세요.
2. 각 component에 position(NUMERATOR/DENOMINATOR/ADDEND/SUBTRAHEND)과
   operator(DIVIDE/SUM/SUBTRACT/MULTIPLY)를 명시하세요.
3. component가 다시 분해 가능하면 sub_components로 재귀 표현하세요.
4. 확신도를 HIGH/MEDIUM/LOW로 판단하세요.

## 입력 텍스트
{input_text}

## 출력 (JSON만)
```

**파싱 결과:**
```json
{
  "formula_name": "연체율",
  "definition": "납기가 지난 대출액 / 대출총액",
  "confidence": "HIGH",
  "components": [
    {
      "name": "연체원금",
      "synonyms": ["납기가 지난 대출액"],
      "position": "NUMERATOR",
      "operator": "DIVIDE"
    },
    {
      "name": "여신잔액",
      "synonyms": ["대출총액"],
      "position": "DENOMINATOR",
      "operator": "DIVIDE"
    }
  ]
}
```

**Neo4j 반영 Cypher:**
```cypher
// 1. 루트 개념 생성/갱신
MERGE (root:DomainConcept {name: $formula_name})
SET root.definition = $definition,
    root.category = "금융지표",
    root.source = "auto_ingest",
    root.created_at = datetime()

// 2. 구성 요소 생성 + COMPOSED_OF 관계
UNWIND $components AS comp
MERGE (sub:DomainConcept {name: comp.name})
SET sub.synonyms = comp.synonyms
MERGE (root)-[r:COMPOSED_OF]->(sub)
SET r.position = comp.position,
    r.operator = comp.operator
```

#### 유형 2: 쿼리 조건 설명 → 조건 규칙 자동 반영

입력:
```
"WHERE 절 조건 CUS_TYPE_CD = '03' AND LON_CD = '05' 는 개인 주택담보대출을 의미한다"
```

**LLM 파싱 프롬프트 (`resources/prompts/reason/ontology_condition_extraction.txt`):**
```
당신은 SQL WHERE 조건의 업무적 의미를 그래프 온톨로지 구조로 변환하는 전문가입니다.

## 입력
SQL 조건과 그 업무적 의미가 주어집니다.

## 출력 규칙
1. 각 조건을 code_field, operator, value로 분해하세요.
2. 전체 조건 조합의 업무적 의미를 meaning에 기재하세요.
3. 개별 코드값의 의미를 code_meaning에 기재하세요.
4. 이 조건이 적용되는 테이블을 추론할 수 있으면 applicable_tables에 기재하세요.

## 입력 텍스트
{input_text}

## 출력 (JSON만)
```

**파싱 결과:**
```json
{
  "meaning": "개인 주택담보대출",
  "confidence": "HIGH",
  "conditions": [
    {
      "code_field": "CUS_TYPE_CD",
      "operator": "=",
      "value": "03",
      "code_meaning": "개인"
    },
    {
      "code_field": "LON_CD",
      "operator": "=",
      "value": "05",
      "code_meaning": "주택담보대출"
    }
  ],
  "applicable_tables": ["TB_LN_BAL_D", "TB_LN_EXEC_D"]
}
```

**Neo4j 반영 Cypher:**
```cypher
// 1. QueryCondition 노드 생성
MERGE (qc:QueryCondition {
    pattern: "CUS_TYPE_CD = '03' AND LON_CD = '05'"
})
SET qc.meaning = $meaning,
    qc.source = "auto_ingest",
    qc.created_at = datetime()

// 2. 개별 코드값 연결
UNWIND $conditions AS cond
MERGE (cd:CodeDefinition {code_field: cond.code_field, code_value: cond.value})
SET cd.code_name = cond.code_meaning
MERGE (qc)-[:INCLUDES_CODE]->(cd)

// 3. 적용 가능 테이블 연결
UNWIND $applicable_tables AS tbl
MATCH (t:Table {name: tbl})
MERGE (qc)-[:APPLIES_TO_TABLE]->(t)

// 4. 도메인 개념 연결 (의미 → 개념)
MERGE (dc:DomainConcept {name: $meaning})
MERGE (qc)-[:DEFINES]->(dc)
```

**반영 후 활용 예시:**

사용자가 "개인 주택담보대출 잔액"을 요청하면:

```cypher
MATCH (dc:DomainConcept {name: "개인 주택담보대출"})
      <-[:DEFINES]-(qc:QueryCondition)
      -[:APPLIES_TO_TABLE]->(t:Table)
RETURN qc.pattern AS where_clause,
       t.name AS table_name
```

→ `WHERE CUS_TYPE_CD = '03' AND LON_CD = '05'` + `TB_LN_BAL_D`가 즉시 반환.
→ SQL Generator가 정확한 WHERE 절을 코드값까지 포함하여 생성 가능.

### 6.3 확신도 게이트 (`OntologyIngestor`)

```python
class OntologyIngestor:
    """런타임 온톨로지 규칙 자동 수집기."""

    async def ingest(self, text: str, rule_type: str) -> dict:
        """비정형 텍스트에서 온톨로지 규칙을 추출하여 Neo4j에 반영한다.

        rule_type: "formula" | "condition" | "join_pattern"
        """
        # 1. LLM 파싱
        parsed = await self._parse_with_llm(text, rule_type)

        # 2. 확신도 게이트
        confidence = parsed.get("confidence", "LOW")
        if confidence == "LOW":
            logger.warning("확신도 낮음 — 로깅만 수행", text=text[:100])
            return {"status": "logged_only", "reason": "low_confidence"}

        # 3. Neo4j 반영
        if rule_type == "formula":
            await self._write_formula(parsed)
        elif rule_type == "condition":
            await self._write_condition(parsed)
        elif rule_type == "join_pattern":
            await self._write_join(parsed)

        # 4. 캐시 무효화
        self._neo4j.invalidate_cache()

        return {"status": "ingested", "parsed": parsed}
```

### 6.4 파이프라인 통합 지점

| 통합 지점 | 트리거 조건 | 수집 유형 | 실행 방식 |
|----------|-----------|----------|----------|
| context_explorer | 유사 SQL에서 새 JOIN 패턴 발견 | join_pattern | fire-and-forget (`asyncio.create_task`) |
| sql_validator | Layer 3 통과한 SQL의 JOIN 조건 | join_pattern | fire-and-forget |
| 관리자 API | POST /api/ontology/ingest | formula, condition | 동기 (즉시 반영 확인) |
| 시딩 스크립트 | seed_neo4j.py Phase 3 | formula | 배치 |

---

## 7. 파이프라인 도구 연동

### 7.1 tools.py에 추가할 도구

| 도구명 | 입력 | 출력 | 사용 노드 |
|-------|------|------|----------|
| `search_join_path` | 테이블 2개 | 최단 JOIN 경로 | context_explorer, sql_generator |
| `search_domain_tables` | 도메인 키워드 | 관련 테이블 + FK 이웃 | planner |
| `search_formula` | 지표명 | 산출식 분해 구조 | planner, sql_generator |
| `search_code_mapping` | 한글 코드명 | 코드 필드 + 값 + 테이블 | context_explorer |

### 7.2 활용 흐름

```
사용자: "VIP 고객의 지점별 여신 연체율"
    │
    ▼ planner_node
    │
    ├─ search_domain_tables("여신")
    │   → TB_LN_BAL_D (PRIMARY), joinable: [TB_CUST_INFO, TB_BRANCH_M]
    │
    ├─ search_formula("연체율")
    │   → 연체원금(SUM, OVRD_PRINC_AMT) / 여신잔액(SUM, LOAN_BAL_AMT) × 100
    │   → 필요 테이블: TB_LN_BAL_D
    │
    ├─ search_code_mapping("VIP")
    │   → CUST_GRD_CD = '01', 테이블: TB_CUST_INFO
    │
    ▼ confidence_evaluator
    │
    │  knowledge_items 전부 CONFIRMED (그래프에서 구조적으로 확인)
    │  join_path: TB_LN_BAL_D ──CUST_NO──→ TB_CUST_INFO (confidence: 0.9)
    │            TB_LN_BAL_D ──BR_CD──→ TB_BRANCH_M (confidence: 0.9)
    │  → score ≥ 0.75 → GENERATE
    │
    ▼ sql_generator
    │
    │  구조적으로 확인된 정보만으로 SQL 조립:
    │  SELECT b.BR_NM AS 지점명,
    │         SUM(l.OVRD_PRINC_AMT) / NULLIF(SUM(l.LOAN_BAL_AMT), 0) * 100 AS 연체율
    │  FROM TB_LN_BAL_D l
    │  JOIN TB_CUST_INFO c ON l.CUST_NO = c.CUST_NO
    │  JOIN TB_BRANCH_M b ON l.BR_CD = b.BR_CD
    │  WHERE c.CUST_GRD_CD = '01'
    │  GROUP BY b.BR_NM
```

---

## 8. 구현 로드맵

### Phase 1: 커넥터 기반 (LLM 불필요)
| 파일 | 작업 | 예상 |
|------|------|------|
| `pyproject.toml` | neo4j 의존성 추가 | 5분 |
| `src/config.py` | Neo4j 설정 블록 추가 | 10분 |
| `src/connectors/impl/neo4j_connector.py` | 커넥터 신규 생성 (Dummy 포함) | 2시간 |
| `src/connectors/manager.py` | neo4j 커넥터 등록 | 15분 |
| `src/connectors/dummy_data.py` | Neo4j 더미 데이터 함수 추가 | 30분 |

### Phase 2: 스키마 & 시딩
| 파일 | 작업 | 예상 |
|------|------|------|
| `resources/connectors/neo4j/init_neo4j.cypher` | 스키마 제약조건/인덱스 정의 | 1시간 |
| `resources/connectors/neo4j/seed_queries.cypher` | 시딩용 Cypher 템플릿 | 1시간 |
| `devtools/scripts/seed_neo4j.py` | MongoDB → Neo4j 시딩 스크립트 | 3시간 |

### Phase 3: 자동 수집
| 파일 | 작업 | 예상 |
|------|------|------|
| `src/services/ontology_ingestor.py` | 자동 수집 서비스 | 3시간 |
| `resources/prompts/reason/ontology_formula_extraction.txt` | 산출식 추출 프롬프트 | 1시간 |
| `resources/prompts/reason/ontology_condition_extraction.txt` | 조건 규칙 추출 프롬프트 | 1시간 |

### Phase 4: 파이프라인 통합
| 파일 | 작업 | 예상 |
|------|------|------|
| `src/agents/nodes/reason/tools.py` | Neo4j 도구 4종 추가 | 1시간 |
| `src/agents/nodes/reason/planner.py` | 초기 컨텍스트에 그래프 검색 통합 | 2시간 |
| `src/agents/nodes/reason/context_explorer.py` | JOIN 경로 확인 도구 통합 | 1시간 |
| `src/services/confidence_scorer.py` | join_path 차원 개선 (이진→연속) | 30분 |

---

*작성자: Claude Opus 4.6*
*최종 갱신: 2026-03-25*
