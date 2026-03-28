# SQL 온톨로지 구현을 위한 오픈소스 그래프 데이터베이스 선정 리서치

**작성일**: 2026-03-25
**작성자**: Research Analyst Agent
**대상 프로젝트**: Data Copilot (NL-to-SQL, 은행 도메인)
**배포 환경**: RHEL 9.4, 폐쇄망 (Air-gapped)

---

## 1. 리서치 배경 및 목적

### 1.1 문제 정의

Data Copilot의 핵심 난제 중 하나는 **5,000개 이상 테이블, 100K 컬럼**이 산재한 정보계 DB에서 자연어 질의에 맞는 테이블·컬럼을 정확하게 선택하고 올바른 JOIN 경로를 추론하는 것이다. 현재 시스템은 Elasticsearch 메타 검색과 벡터 유사도(Qdrant)에 의존하고 있으나, 다음 한계가 확인된다.

- **구조적 조인 경로 미스**: 의미적으로 유사하지 않은 브리지 테이블(예: `코드_매핑`, `중간_집계`)은 벡터 검색에서 누락됨
- **도메인 개념 → 테이블 매핑의 명시성 부재**: "연체율"이 어떤 테이블 조합으로 산출되는지 그래프 구조로 표현되지 않음
- **코드 계층 탐색 불가**: 코드값 → 상위분류 → 대분류 간 계층 관계가 플랫 구조로 저장됨

이에 그래프 데이터베이스 도입을 검토하며, RHEL 9.4 폐쇄망 제약 하에서 실현 가능한 최적 솔루션을 선정한다.

### 1.2 저장 대상 데이터 규모

| 항목 | 수량 |
|------|------|
| 테이블 노드 | ~5,000 |
| 컬럼 노드 | ~100,000 |
| 도메인 용어 노드 | ~1,000 |
| 코드 정의 노드 | ~500 |
| 외래키/조인 엣지 | ~20,000 (추정) |
| 도메인→테이블 매핑 엣지 | ~5,000 (추정) |
| 전체 그래프 규모 | ~130K 노드, ~30K 엣지 (소규모) |

이 규모는 그래프 DB 기준으로 **매우 소규모**에 해당한다. 단일 노드 배포로 충분하며, 분산 처리가 불필요하다.

---

## 2. 후보 데이터베이스 평가

### 2.1 평가 기준 매트릭스

| 항목 | 가중치 | 설명 |
|------|--------|------|
| RHEL 9.4 설치 가능성 | 30% | RPM/컨테이너, 폐쇄망 전달 가능 여부 |
| 라이선스 (상업적 내부 사용) | 25% | OSI 승인 오픈소스 여부 |
| Python async 드라이버 성숙도 | 20% | asyncio 기반 LangGraph 노드 호환 |
| 쿼리 언어 및 경로탐색 성능 | 15% | Cypher/GQL, 조인 경로 탐색 |
| 운영 복잡도 | 10% | 단일 노드 배포, 유지보수 부담 |

---

### 2.2 Neo4j Community Edition

#### 라이선스
**GNU GPL v3.0** - OSI 승인 오픈소스. 상업적 내부 사용 허용. 단, 소스 코드를 수정·배포할 경우 GPLv3 조건 준수 필요. 폐쇄망 내부 서비스 운영은 분배(distribution)에 해당하지 않으므로 **상업적 내부 사용에 제약 없음**.

출처: [Neo4j GitHub](https://github.com/neo4j/neo4j), [Neo4j Community FAQ](https://neo4j.com/open-core-and-neo4j/)

#### Community Edition 기능 제한
- 클러스터링 불가 (단일 노드만 지원) → 본 프로젝트 요구사항과 일치
- 핫 백업 불가 → 콜드 백업으로 대체 가능
- 모니터링/보안 고급 기능 없음 → 내부 서비스로 수용 가능

#### RHEL 9.4 설치
공식 RPM 패키지 제공. `yum.neo4j.com` 저장소에서 다운로드 후 전달 가능. Java 21 런타임 필요(OpenJDK 21).

```bash
# 오프라인 패키지 사전 수집 (인터넷 연결 머신에서)
rpm --import https://debian.neo4j.com/neotechnology.gpg.key
wget https://yum.neo4j.com/stable/neo4j.repo -O /etc/yum.repos.d/neo4j.repo
yum install --downloadonly --downloaddir=/tmp/neo4j_pkgs neo4j
# /tmp/neo4j_pkgs/*.rpm 를 폐쇄망으로 전달 후 dnf localinstall
```

출처: [Neo4j RPM Operations Manual](https://neo4j.com/docs/operations-manual/current/installation/linux/rpm/), [Air-gapped Installation KB](https://neo4j.com/developer/kb/how-to-install-neo4j-in-a-disconnected-environment/)

#### Python Async 드라이버
**공식 `neo4j` 드라이버 5.x** — asyncio 네이티브 지원. 버전 5.0에서 도입, 5.8에서 안정화. 최신 버전 5.28.3 (2026-01-12 출시).

```python
from neo4j import AsyncGraphDatabase

async with AsyncGraphDatabase.driver(uri, auth=(user, password)) as driver:
    async with driver.session() as session:
        result = await session.run("MATCH (t:Table)-[:HAS_FK]->(t2:Table) RETURN t, t2")
```

출처: [Neo4j Python Driver Async API](https://neo4j.com/docs/api/python-driver/current/async_api.html)

#### 쿼리 언어 및 경로탐색
**Cypher** — 선언적, SQL 유사 문법. 조인 경로 탐색에 최적화된 패턴 매칭 지원.

```cypher
-- 두 테이블 간 최단 JOIN 경로 탐색
MATCH path = shortestPath(
  (t1:Table {name: "TB_LOAN_MASTER"})-[:FK_RELATION*..6]-(t2:Table {name: "TB_CUSTOMER"})
)
RETURN [node IN nodes(path) | node.name] AS join_path
```

#### 총평
- 성숙도: **최상** (2007년 출시, 업계 표준)
- 문서: **최상**
- RHEL 9.4 지원: **공식 지원**
- async 드라이버: **공식 지원 (성숙)**
- 상업적 사용: **제약 없음 (GPLv3, 내부 사용)**

---

### 2.3 Apache AGE (PostgreSQL Extension)

#### 라이선스
**Apache License 2.0** - OSI 승인. 상업적 사용 완전 무제한. Apache Top Level Project (2022년 5월 승격).

출처: [Apache AGE GitHub](https://github.com/apache/age)

#### 핵심 특성
PostgreSQL 확장(Extension)으로 동작. 기존 PostgreSQL 인스턴스에 설치하여 관계형 데이터와 그래프 데이터를 **단일 저장소**에서 함께 관리. ANSI SQL + openCypher 혼용 가능.

```sql
-- SQL과 Cypher 혼용 예시
SELECT * FROM ag_catalog.cypher('schema_graph', $$
  MATCH (t:Table)-[r:FK_RELATION]->(t2:Table)
  WHERE t.name = 'TB_LOAN'
  RETURN t2.name, r.join_column
$$) AS (table_name agtype, join_col agtype);
```

#### RHEL 9.4 설치
PostgreSQL 11~18 지원. PostgreSQL이 이미 설치된 환경이라면 소스 빌드 필요:

```bash
git clone https://github.com/apache/age.git
cd age
make PG_CONFIG=/usr/pgsql-15/bin/pg_config install
# PostgreSQL에서 확장 활성화
psql -c "CREATE EXTENSION IF NOT EXISTS age;"
```

**핵심 문제**: RHEL 9 공식 RPM 패키지가 없음. 소스 빌드에 `gcc`, `make`, `postgresql-devel`이 필요하며, 폐쇄망에서는 이 빌드 체인 전달이 추가 부담.

출처: [Apache AGE Docker Image](https://hub.docker.com/r/apache/age), [Apache AGE Postgres Pro Docs](https://postgrespro.com/docs/enterprise/current/apache-age)

#### Python 드라이버
`apache-age-python` (PyPI) — psycopg2/psycopg3 기반. **Incubator 상태로 미성숙**. async 지원에 대한 공식 문서 없음. psycopg3의 async 기능을 수동으로 활용하는 것은 가능하나 공식 지원이 아님.

출처: [apache-age-python PyPI](https://pypi.org/project/apache-age-python/)

#### 총평
- **가장 큰 장점**: 기존 PostgreSQL 스택과 통합 → 추가 인프라 불필요. Data Copilot은 이미 PostgreSQL 사용 중.
- **가장 큰 약점**: Python 드라이버 미성숙, 소스 빌드 필요 (RPM 없음), async 미지원
- 상업적 사용: **완전 무제한 (Apache 2.0)**

---

### 2.4 JanusGraph

#### 라이선스
**Apache License 2.0** — 완전 오픈소스.

#### 아키텍처 특성
JanusGraph는 **분산 처리 특화** 설계로, 스토리지 백엔드(Cassandra/HBase)와 인덱스 백엔드(Elasticsearch/Solr)를 별도로 구성해야 한다. 단일 노드 배포 시에도 최소 JanusGraph + BerkeleyDB + Elasticsearch 3개 프로세스 구성 필요.

출처: [JanusGraph Deployment Docs](https://docs.janusgraph.org/operations/deployment/)

#### RHEL 9.4 설치
공식 RPM 없음. Java 기반이므로 `.zip` 배포판을 수동 배포. BerkeleyDB를 로컬 스토리지로 사용하면 단일 노드 가능하나, 고가용성 미지원.

#### Python 드라이버
Gremlin 기반 `gremlinpython` 라이브러리. asyncio 지원 있으나, Gremlin Python 드라이버는 WebSocket 기반으로 연결 안정성 이슈가 보고됨.

#### 쿼리 언어
**Gremlin** (Apache TinkerPop) — 명령형(imperative), Cypher 대비 가독성 낮음:

```python
# Cypher 대비 복잡한 Gremlin 경로 탐색
g.V().has("Table", "name", "TB_LOAN") \
 .repeat(out("FK_RELATION")).until(has("Table", "name", "TB_CUSTOMER")) \
 .path().by("name")
```

#### 총평
- **치명적 약점**: 단일 노드 운영에도 복수 컴포넌트 필요. 운영 복잡도 과다.
- 본 프로젝트 규모(130K 노드)에 완전한 과잉(overkill).
- JanusGraph 대안 비교 리소스도 "단순 사용 사례에는 과도하다"고 평가.

**기각 이유**: 단일 노드 배포에도 Cassandra/HBase + Elasticsearch 추가 인프라 필요. 폐쇄망 설치 복잡도 과다. 데이터 규모가 분산 처리 역치에 미달.

출처: [JanusGraph Alternatives 2026](https://www.puppygraph.com/blog/janusgraph-alternatives)

---

### 2.5 ArangoDB

#### 라이선스 (결정적 문제)
버전 3.12 (2024년 Q1)부터 **Apache 2.0 → BSL 1.1 + ArangoDB Community License**로 전환.

**Community Edition 상업적 사용 제약**:
- 생산 환경 데이터셋 **100GB 한도** (단일 클러스터)
- 최대 **3개 클러스터** 제한
- SaaS/DBaaS 형태로 고객에게 제공 불가
- 4년 후 Apache 2.0으로 전환 예정이나, 3.12는 2028년까지 BSL 유지

폐쇄망 내부 서비스의 경우 100GB 한도는 본 데이터 규모(수백 MB 예상)에서 사실상 문제없으나, **법적 명확성 측면에서 은행 도메인 상업 환경에 불확실성 존재**.

출처: [ArangoDB Licensing Evolution](https://arango.ai/blog/evolving-arangodbs-licensing-model-for-a-sustainable-future/), [ArangoDB Community License PDF](https://arango.ai/wp-content/uploads/2025/11/ADB-Community-License_31OCT2023.pdf)

#### RHEL 9.4 설치
공식 `.rpm` 패키지 제공. 단일 노드 배포 지원.

#### Python 드라이버
`python-arango` — 동기 드라이버. `aioarango` (비공식 async 래퍼) 존재하나 유지보수 불확실.

#### 쿼리 언어
**AQL** (ArangoDB Query Language) — 문서/그래프 혼용 쿼리 지원.

#### 총평
- **기각 이유**: 라이선스 불확실성. BSL은 OSI 미승인. 은행 내부 법무 검토 시 리스크. AQL은 Cypher 대비 생태계 협소.

---

### 2.6 Memgraph

#### 라이선스 (결정적 문제)
**BSL 1.1 (Business Source License)** — OSI **미승인**. "isitreallyfoss.com" 분석에서 FOSS 불인정.

비생산 환경(개발/QA/테스트) 사용은 무료. 생산 환경 사용은 Additional Use Grant 조건 확인 필요. 4년 후 Apache 2.0 전환 예정이나 현재 버전은 BSL.

출처: [Memgraph BSL License](https://github.com/memgraph/memgraph/blob/master/licenses/BSL.txt), [Is Memgraph FOSS?](https://isitreallyfoss.com/projects/memgraph/)

#### RHEL 9.4 설치
**공식 RPM 지원** (RHEL 9 대응 CentOS-9 빌드 제공). 가장 간편한 설치.

```bash
sudo wget https://download.memgraph.com/memgraph/v3.1.1/centos-9/memgraph-3.1.1_1-1.x86_64.rpm
sudo dnf install -y ./memgraph-3.1.1_1-1.x86_64.rpm
sudo systemctl start memgraph
```

단, SELinux 정책 검토 필요.

출처: [Memgraph RHEL Install Docs](https://memgraph.com/docs/getting-started/install-memgraph/redhat)

#### 성능
**인메모리(In-Memory) 아키텍처** — Neo4j 대비 최대 41x 낮은 레이턴시, 100,000 노드 삽입에 400ms (Neo4j 3.8초 대비). 단, 인메모리 특성으로 재시작 시 데이터 로드 시간 발생. 130K 노드 규모는 수 초 내 로드 가능한 수준.

출처: [Memgraph vs Neo4j Performance](https://memgraph.com/blog/memgraph-vs-neo4j-performance-benchmark-comparison)

#### Python 드라이버
공식 문서에서 **Neo4j Python 드라이버 v5+** 사용 권장. Memgraph는 Bolt 프로토콜 호환이므로 neo4j driver가 그대로 동작. **하지만 공식 async 예시 없음** — neo4j 드라이버의 AsyncGraphDatabase 사용은 기술적으로 가능하나 Memgraph 공식 지원 사항이 아님.

#### 총평
- **기각 이유**: BSL 1.1은 OSI 미승인 오픈소스. 은행 도메인 상업적 내부 배포 시 법적 명확성 부족. 생산 환경 무료 사용 기준 불명확.

---

### 2.7 FalkorDB

#### 라이선스 (결정적 문제)
**SSPL v1 (Server Side Public License)** — OSI **미승인**. MongoDB가 고안한 라이선스로, 서비스 형태로 제공 시 전체 서비스 스택 소스코드 공개 의무. 내부 전용 사용 시에는 해당 없으나, OSI 미승인으로 일부 기업 법무팀이 사용 금지 리스트에 포함.

출처: [FalkorDB License Docs](https://docs.falkordb.com/license.html)

#### 기술적 특성
- Redis 기반 (GraphBLAS 기반 행렬 연산으로 그래프 처리)
- Python async 지원: `falkordb.asyncio` 모듈 제공
- 최신 LLM 도구들과 통합 사례 다수 (QueryWeaver, LangGraph)

#### SQL 온톨로지 활용 사례
FalkorDB의 공식 블로그에서 QueryWeaver 구현 소개: 테이블 노드, 컬럼 노드, FK 엣지로 스키마를 그래프화하여 멀티홉 조인 경로를 그래프 탐색으로 발견.

출처: [FalkorDB Text-to-SQL Knowledge Graphs](https://www.falkordb.com/blog/text-to-sql-knowledge-graphs/)

#### 총평
- **기각 이유**: SSPL은 OSI 미승인. 은행 도메인 법무 검토 시 리스크.

---

### 2.8 Amazon Neptune

사전 기각. 자체 호스팅 불가. 폐쇄망 요건 불충족.

---

## 3. 후보 비교 종합표

| 항목 | Neo4j CE | Apache AGE | JanusGraph | ArangoDB | Memgraph | FalkorDB |
|------|----------|------------|------------|----------|----------|----------|
| **라이선스** | GPLv3 (OSI) | Apache 2.0 (OSI) | Apache 2.0 (OSI) | BSL 1.1 (OSI 미승인) | BSL 1.1 (OSI 미승인) | SSPL v1 (OSI 미승인) |
| **상업적 내부 사용** | 제약없음 | 완전자유 | 완전자유 | 100GB/클러스터 제한 | 생산환경 불명확 | 내부전용 가능 |
| **RHEL 9.4 RPM** | 공식 지원 | RPM 없음(소스빌드) | RPM 없음(JAR) | 공식 지원 | 공식 지원 | Docker만 |
| **Python async** | 공식 지원(v5.8+) | 미지원 | 제한적 | 비공식 | 비공식 | 공식 지원 |
| **쿼리 언어** | Cypher | SQL+openCypher | Gremlin | AQL | Cypher | Cypher |
| **단일 노드 적합성** | 설계 목적 | PostgreSQL 확장 | 과잉 | 적합 | 인메모리 | 적합 |
| **경로탐색 성능** | 우수 | 보통 | 우수(과잉) | 우수 | 최상 | 최상 |
| **생태계 성숙도** | 최상(2007~) | 중간 | 중간 | 중간 | 중간 | 초기 |
| **폐쇄망 적합성** | 높음 | 중간 | 낮음 | 높음 | 높음 | 중간 |

---

## 4. SQL 온톨로지 그래프 모델 설계

### 4.1 학술적 근거

최근 NL-to-SQL 연구에서 그래프 기반 스키마 모델링이 활발히 연구되고 있다.

**SchemaGraphSQL (arXiv 2505.18363, 2025-05)**
이란 테헤란 대학 & 샤리프 공대 연구팀이 발표. 외래키 관계를 기반으로 스키마 그래프를 구성하고, LLM이 소스/목적 테이블을 추출하면 고전적 경로탐색 알고리즘(BFS/Dijkstra)으로 최적 조인 시퀀스를 결정. BIRD 벤치마크 SOTA 달성 (zero-shot, training-free).

핵심 발견: "스키마 그래프를 명시적으로 구성하고 경로탐색 알고리즘을 적용하는 방식이, 벡터 유사도만 사용하는 방식 대비 의미적으로 모호한 브리지 테이블 탐색에서 현저히 우수"

출처: [SchemaGraphSQL arXiv](https://arxiv.org/abs/2505.18363)

**DCG-SQL (ACL 2025, 2505.19956)**
Deep Contextual Schema Link Graph 구성 — 질문 토큰과 스키마 아이템(테이블/컬럼) 간 관계를 그래프로 표현. 테이블 재현율 97.4%, 컬럼 재현율 95.5% 달성.

출처: [DCG-SQL ACL 2025](https://arxiv.org/abs/2505.19956)

**QueryWeaver (FalkorDB, 2024)**
산업 구현 사례: FalkorDB를 사용하여 스키마 메타데이터를 그래프화. "그래프 탐색은 의미 유사성이 낮더라도 구조적으로 필수적인 브리지 테이블을 탐색할 수 있다 — 벡터 검색은 이를 놓친다"고 보고.

출처: [FalkorDB Text-to-SQL Blog](https://www.falkordb.com/blog/text-to-sql-knowledge-graphs/)

### 4.2 권장 노드·엣지 타입 설계

```cypher
-- 노드 타입 정의

(:Table {
  name: String,           -- 테이블명 (TB_LOAN_MASTER)
  description: String,    -- 업무 설명 (여신 원장 테이블)
  schema_name: String,    -- 스키마
  row_count: Integer,     -- 데이터 규모 참고
  update_cycle: String,   -- 갱신주기 (일/월/실시간)
  data_range: String,     -- 데이터 범위 (2019~현재)
  source_db: String       -- Sybase IQ / Impala / PostgreSQL
})

(:Column {
  name: String,           -- 컬럼명
  table_name: String,     -- 소속 테이블명
  data_type: String,      -- 데이터 타입
  description: String,    -- 업무 설명
  is_pk: Boolean,         -- 기본키 여부
  is_nullable: Boolean,   -- NULL 허용 여부
  code_group: String      -- 참조 코드그룹 (있는 경우)
})

(:DomainConcept {
  term: String,           -- 도메인 용어 (연체율, BIS비율)
  definition: String,     -- 업무 정의
  formula: String,        -- 계수산출식 (있는 경우)
  category: String        -- 분류 (여신/수신/외환/리스크)
})

(:CodeDefinition {
  code_group: String,     -- 코드그룹 (LOAN_TYPE_CD)
  code_value: String,     -- 코드값 (01)
  code_name: String,      -- 코드명 (담보대출)
  parent_code: String     -- 상위 코드값 (계층)
})

(:JoinPath {
  id: String,             -- 경로 식별자
  description: String,    -- 경로 설명
  validated: Boolean,     -- 검증된 경로 여부
  source_sql: String      -- 검증 근거 SQL
})

-- 엣지 타입 정의

(:Table)-[:HAS_COLUMN]->(:Column)
  # 속성: position(Integer)

(:Table)-[:FK_TO {
  from_column: String,    -- 출발 컬럼
  to_column: String,      -- 도착 컬럼
  join_type: String,      -- INNER/LEFT
  confidence: Float       -- 확신도 (메타 명시=1.0, 추론=0.7)
}]->(:Table)

(:DomainConcept)-[:RESOLVED_BY {
  primary: Boolean,       -- 주 테이블 여부
  role: String            -- 분자/분모/기준 등
}]->(:Table)

(:DomainConcept)-[:REQUIRES_COLUMN]->(:Column)

(:DomainConcept)-[:COMPOSED_OF]->(:DomainConcept)
  # 금융 지표의 구성 관계 (연체율 = 연체잔액 / 여신잔액)

(:CodeDefinition)-[:PARENT_OF]->(:CodeDefinition)
  # 코드 계층 (대분류 → 중분류 → 소분류)

(:Column)-[:USES_CODE]->(:CodeDefinition)
  # 컬럼이 참조하는 코드 정의

(:Table)-[:INCLUDED_IN]->(:JoinPath)
(:JoinPath)-[:VALIDATED_BY_SQL {
  sql_hash: String
}]->(:JoinPath)
```

### 4.3 핵심 쿼리 패턴

```cypher
-- 1. 두 테이블 간 최단 JOIN 경로 (최대 5홉)
MATCH path = shortestPath(
  (src:Table {name: $src_table})-[:FK_TO*..5]-(dst:Table {name: $dst_table})
)
RETURN [n IN nodes(path) | n.name] AS join_sequence,
       [r IN relationships(path) | {from: r.from_column, to: r.to_column}] AS join_conditions

-- 2. 도메인 용어로 관련 테이블 탐색
MATCH (dc:DomainConcept {term: $term})-[:RESOLVED_BY]->(t:Table)
OPTIONAL MATCH (dc)-[:REQUIRES_COLUMN]->(c:Column)
RETURN t.name, t.description, collect(c.name) AS required_columns

-- 3. 테이블 주변 연결 컨텍스트 (1~2홉)
MATCH (t:Table {name: $table_name})-[r:FK_TO]-(neighbor:Table)
RETURN neighbor.name, neighbor.description, r.from_column, r.to_column
ORDER BY r.confidence DESC

-- 4. 코드값 계층 탐색
MATCH path = (root:CodeDefinition {code_group: $group})-[:PARENT_OF*]->(leaf)
WHERE NOT (leaf)-[:PARENT_OF]->()
RETURN [n IN nodes(path) | {value: n.code_value, name: n.code_name}] AS hierarchy

-- 5. NL 질의에서 후보 테이블 범위 축소
MATCH (dc:DomainConcept)-[:RESOLVED_BY]->(t:Table)
WHERE dc.term IN $candidate_terms
WITH t, count(dc) AS match_score
MATCH (t)-[:HAS_COLUMN]->(c:Column)
WHERE c.name IN $candidate_columns
RETURN t.name, match_score + count(c) AS relevance_score
ORDER BY relevance_score DESC
LIMIT 10
```

---

## 5. 금융 도메인 온톨로지 참조 (FIBO/FIB-DM)

### 5.1 FIBO 구조적 시사점

FIBO (Financial Industry Business Ontology)는 OWL 기반 금융 온톨로지로, EDM Council이 관리. 2025 Q4 Production Release 기준 3,173개 엔터티 포함. FIB-DM은 FIBO를 관계형 데이터 모델로 변환한 참조 모델 (2026년 1월 갱신).

FIBO의 은행 관련 핵심 개념 계층:
- `FinancialInstrument` → `Loan` → `MortgageLoan`, `CorporateLoan`
- `Account` → `DepositAccount`, `LoanAccount`
- `Party` → `Organization` → `FinancialInstitution`

**Data Copilot 적용 시사점**: FIBO의 개념 계층을 `DomainConcept`-`[:COMPOSED_OF]`->` 관계로 매핑하여, "담보대출 잔액"이라는 용어가 `TB_LOAN_MASTER` WHERE `LOAN_TYPE_CD IN ('담보대출 코드들')`로 해석되는 경로를 명시화할 수 있다.

출처: [FIBO EDM Council](https://spec.edmcouncil.org/fibo/), [FIB-DM](https://fib-dm.com/)

---

## 6. 최종 권고

### 6.1 1순위 권고: Neo4j Community Edition

**근거**:

1. **라이선스 명확성**: GPLv3 OSI 승인. 내부 서비스 운영에 법적 리스크 없음. 은행 법무팀 검토 통과 가능성 최고.

2. **Python async 드라이버 성숙도**: neo4j 패키지 5.28.x — AsyncGraphDatabase 공식 지원. LangGraph 노드에서 `await session.run()` 패턴 직접 사용.

3. **RHEL 9.4 공식 RPM 지원**: 오프라인 RPM 패키지 전달 방식으로 폐쇄망 배포 가능. Java 21 (OpenJDK 21) 필요.

4. **Cypher 생태계**: SchemaGraphSQL(2025), DCG-SQL(2025) 등 최신 NL-to-SQL 연구가 Cypher 기반 스키마 그래프를 표준으로 채택. 향후 연구 성과 적용 직접 가능.

5. **단일 노드 충분**: 130K 노드 규모는 단일 Neo4j 노드의 수십분의 일 수준. 수백만 노드도 처리 가능한 검증된 성능.

6. **성숙도**: 2007년부터 금융권(Standard & Poor's, 기타 은행) 실사용 이력 다수.

**제약사항**:
- 클러스터링 미지원 (Community Edition) → 단일 노드 운영, HA 미지원
- 핫 백업 미지원 → 정기 콜드 백업 스크립트 필요
- Java 21 런타임 필요 (RHEL 9의 OpenJDK 21 패키지로 충족 가능)

### 6.2 2순위 대안: Apache AGE (추가 인프라 회피 전략)

Data Copilot이 이미 PostgreSQL을 사용하고 있다는 점에서, 추가 인프라 없이 PostgreSQL 확장으로 그래프 기능을 추가하는 전략.

**채택 조건**: Python 드라이버 async 미지원을 수용할 수 있고, PostgreSQL 버전이 11~18 범위이며, 소스 빌드 환경(gcc, make)을 폐쇄망에 구성할 수 있는 경우.

**채택 거부 조건**: LangGraph 노드 전체가 async/await 패턴을 사용하는 현재 아키텍처에서 동기 드라이버 강제는 이벤트 루프 블로킹을 유발하므로 기각.

### 6.3 기각된 후보 요약

| 후보 | 기각 근거 |
|------|-----------|
| JanusGraph | 단일 노드에도 Cassandra+ES 필요, 130K 노드 규모 대비 과잉 |
| ArangoDB | BSL 1.1 라이선스, 상업적 사용 법적 불명확 |
| Memgraph | BSL 1.1 라이선스, 생산 환경 사용 조건 불명확 |
| FalkorDB | SSPL v1 라이선스, OSI 미승인, 기업 법무팀 리스크 |
| Amazon Neptune | 자체 호스팅 불가, 폐쇄망 불가 |

---

## 7. 구현 로드맵

### Phase 1: 코어 스키마 그래프 구축 (Week 1~2)
- Neo4j Community Edition 설치 및 설정 (RHEL 9.4)
- PostgreSQL 메타 테이블(information_schema)에서 Table/Column 노드 자동 생성
- ES 메타 검색 결과를 보강하여 업무 설명 속성 추가
- FK 관계 탐지 및 FK_TO 엣지 자동 생성

### Phase 2: 도메인 매핑 레이어 (Week 3~4)
- DomainConcept 노드 구축 (금융 용어 사전 기반)
- RESOLVED_BY 엣지 수동 검증 (업무 담당자 협업)
- 코드 계층(CodeDefinition) 구축

### Phase 3: NL-to-SQL 파이프라인 통합 (Week 5~6)
- SchemaGraphSQL 방식의 조인 경로 탐색 모듈 구현
- LangGraph 노드에서 Neo4j async 드라이버 호출
- 기존 Elasticsearch 메타 검색과 하이브리드 운영

---

## 8. 참고 문헌

### 논문 (Tier 1)
1. Safdarian, A. et al. "SchemaGraphSQL: Efficient Schema Linking with Pathfinding Graph Algorithms for Text-to-SQL on Large-Scale Databases." arXiv:2505.18363 (May 2025). https://arxiv.org/abs/2505.18363

2. DCG-SQL: "Enhancing In-Context Learning for Text-to-SQL with Deep Contextual Schema Link Graph." ACL 2025. arXiv:2505.19956. https://arxiv.org/abs/2505.19956

3. "Plugging Schema Graph into Multi-Table QA: A Human-Guided Framework for Reducing LLM Reliance." arXiv:2506.04427 (2025). https://arxiv.org/html/2506.04427v1

4. "CHESS: Contextual Harnessing for Efficient SQL Synthesis." arXiv:2405.16755 (2024). https://arxiv.org/html/2405.16755v1

5. "Integration Strategy and Tool between Formal Ontology and Graph Database Technology." MDPI Electronics 10(21), 2616 (2021). https://www.mdpi.com/2079-9292/10/21/2616

6. "Retrieval-Augmented Generation of Ontologies from Relational Databases." arXiv:2506.01232 (2025). https://arxiv.org/html/2506.01232v1

### 기술 문서
- Neo4j Operations Manual - RPM Installation: https://neo4j.com/docs/operations-manual/current/installation/linux/rpm/
- Neo4j Python Driver Async API: https://neo4j.com/docs/api/python-driver/current/async_api.html
- Apache AGE GitHub: https://github.com/apache/age
- Memgraph RHEL Installation: https://memgraph.com/docs/getting-started/install-memgraph/redhat
- ArangoDB Community License: https://arango.ai/wp-content/uploads/2025/11/ADB-Community-License_31OCT2023.pdf

### 도메인 온톨로지
- FIBO: https://spec.edmcouncil.org/fibo/
- FIB-DM: https://fib-dm.com/

### 산업 구현 사례
- FalkorDB QueryWeaver (Text-to-SQL + Knowledge Graph): https://www.falkordb.com/blog/text-to-sql-knowledge-graphs/
- FalkorDB Graph Database Guide 2026: https://www.falkordb.com/blog/graph-database-guide/
