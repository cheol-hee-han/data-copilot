# Recovery 노드 도구 확장 전략

> **문서 목적**: Recovery 노드가 가설 보완 시 활용할 수 있는 신규 도구를 정의하고,
> 각 도구의 입출력·보안 제약·Recovery 시나리오·아키텍처 영향을 상세히 기술한다.
>
> **대상 코드**: `src/agents/nodes/reason/tools.py`, `recovery_planner_system.txt`
>
> **작성일**: 2026-03-30

---

## 1. 요약표

### 1-1. 현행 도구 (AS-IS)

| # | 도구명 | 데이터 소스 | 입력 형식 | 용도 |
|---|--------|------------|-----------|------|
| A1 | `search_use_cases` | Qdrant (sql_history) | "검색 키워드" | 유사 SQL 활용사례 벡터 검색 + Reranker |
| A2 | `search_table_meta` | MongoDB (table meta) | "검색 키워드" | 테이블/컬럼 메타데이터 검색 |
| A3 | `search_code_meta` | MongoDB (code meta) | "컬럼명" | 코드성 컬럼의 정의된 코드값 목록 조회 |
| A4 | `search_manual` | Qdrant (biz_manual) | "검색 키워드" | 업무 매뉴얼 벡터 검색 |
| A5 | `search_glossary` | MongoDB (glossary) | "용어" | 금융 용어사전 검색 |
| A6 | `get_sample_rows` | Query DB | "테이블명" | 테이블 샘플 데이터 조회 (LIMIT 10) |
| A7 | `get_date_distribution` | Query DB | "테이블명,날짜컬럼명" | 날짜 컬럼 DISTINCT 값 분포 조회 |

### 1-2. 신규 제안 도구 (TO-BE)

| # | 도구명 | 카테고리 | 데이터 소스 | 주요 Recovery 시나리오 | 구현 우선순위 |
|---|--------|---------|------------|----------------------|-------------|
| B1 | `get_column_distinct_values` | DB 직접 탐색 | Query DB | FAIL_EMPTY, SQL_STRUCTURAL (코드값 불일치) | ★★★ P0 |
| B2 | `get_column_stats` | DB 직접 탐색 | Query DB | FAIL_EMPTY (범위 밖), DB_ERROR (타입 불일치) | ★★★ P0 |
| B3 | `get_table_schema` | DB 직접 탐색 | Query DB (카탈로그) | DB_ERROR (컬럼 미존재), SQL_STRUCTURAL | ★★★ P0 |
| B4 | `search_related_tables` | 메타 탐색 | MongoDB + Qdrant | SQL_STRUCTURAL (테이블 자체 부적절) | ★★★ P0 |
| B5 | `explore_with_sql` | LLM 자율 탐색 | Query DB | 모든 failure_type (자유 형태 원인 진단) | ★★☆ P1 |
| B6 | `explore_graph` | 그래프 탐색 | Neo4j | SQL_STRUCTURAL, TERM_UNRESOLVABLE | ★★☆ P1 |
| B7 | `search_report_sql` | 메타 탐색 | MongoDB (보고서) | TERM_UNRESOLVABLE (공식 쿼리 참조) | ★★☆ P1 |
| B8 | `validate_join_path` | DB 직접 탐색 | Query DB + Neo4j | SQL_STRUCTURAL (JOIN 실패) | ★★☆ P1 |
| B9 | `check_data_freshness` | DB 직접 탐색 | Query DB | FAIL_EMPTY (데이터 미적재) | ★☆☆ P2 |
| B10 | `explain_sql` | DB 직접 탐색 | Query DB | DB_ERROR (timeout) | ★☆☆ P2 |
| B11 | `search_similar_columns` | 메타 탐색 | MongoDB | DB_ERROR (컬럼명 불일치) | ★☆☆ P2 |
| B12 | `get_table_row_count` | DB 직접 탐색 | Query DB | FAIL_EMPTY (테이블 비어있음 vs 조건 문제) | ★☆☆ P2 |

### 1-3. Neo4j 데이터 확장 (TO-BE)

| # | 관계/노드 | 설명 | 지원 도구 |
|---|----------|------|----------|
| C1 | `(:Table)-[:ALTERNATIVE_OF]->(:Table)` | 대체 가능 테이블 (일별↔월별 등) | `explore_graph`, `search_related_tables` |
| C2 | `(:Column)-[:SAME_AS]->(:Column)` | 동일 의미 컬럼 (약어 차이) | `explore_graph`, `validate_join_path` |
| C3 | `(:QueryPattern)-[:USES]->(:Table)` | 검증된 SQL 사용 패턴 | `explore_graph` |
| C4 | `(:Table)-[:DATE_CONVENTION]->(:DatePattern)` | 기준일 컬럼·형식·갱신주기 | `explore_graph`, `check_data_freshness` |
| C5 | `(:Process)-[:PRODUCES_DATA]->(:Table)` | 업무 프로세스 → 테이블 매핑 | `explore_graph` |

---

## 2. 현행 도구 한계 분석

Recovery 노드는 실패 원인을 진단하고 새 가설을 수립해야 하지만,
현행 7개 도구만으로는 다음과 같은 **구조적 갭**이 존재한다.

| 갭 | 현행 | 문제 |
|----|------|------|
| **메타 vs 실데이터 괴리** | `search_code_meta`는 정의된 코드값만 반환 | 실제 DB에 `01,02,03`이 아니라 `A,B,C`가 들어있는 TYPE-2 불완전성 대응 불가 |
| **DB 스키마 직접 확인 불가** | `search_table_meta`는 MongoDB 메타 의존 | 메타에 등록 안 된 테이블/컬럼은 발견 자체가 불가능 |
| **고정 패턴만 지원** | 모든 도구가 사전 정의된 쿼리 형태 | "이 테이블에 3월 데이터가 있나?" 같은 맥락 의존적 질문에 답할 수 없음 |
| **그래프 관계 미활용** | 키워드 기반 텍스트 검색만 가능 | 테이블 간 FK 경로, 도메인 개념 분해, 대체 테이블 탐색 불가 |
| **교차 검증 부재** | 각 도구가 독립 실행 | 메타에서는 안 나오지만 매뉴얼에 나오는 용어 등 다중 소스 대조 불가 |
| **원인 진단 한계** | `FAIL_EMPTY` 시 "왜 비었는지" 확인 수단 없음 | 테이블이 비었는지, 조건이 좁은지, 날짜 범위 밖인지 구분 불가 |

---

## 3. 신규 도구 상세 정의

---

### B1. `get_column_distinct_values` — 컬럼 실제값 직접 확인

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | DB 직접 탐색 |
| **데이터 소스** | Query DB (PostgreSQL / Sybase IQ / Impala) |
| **입력 형식** | `"테이블명,컬럼명"` |
| **출력** | `list[dict]` — DISTINCT 값 + 건수 (최대 50행) |
| **우선순위** | ★★★ P0 |

#### 실행 SQL

```sql
-- PostgreSQL / Impala
SELECT {column}, COUNT(*) AS cnt
FROM {table}
GROUP BY {column}
ORDER BY cnt DESC
LIMIT 50

-- Sybase IQ
SELECT TOP 50 {column}, COUNT(*) AS cnt
FROM {table}
GROUP BY {column}
ORDER BY cnt DESC
```

#### 보안 제약

- 식별자 화이트리스트 검증 (`_IDENT_RE` 재사용)
- PII 컬럼 차단: `pii_columns.yaml`에 정의된 컬럼은 실행 거부, `"PII 컬럼으로 직접 조회 불가"` 반환
- LIMIT 50 강제 (파라미터 오버라이드 불가)
- 결과는 LLM 내부 추론에만 사용, 사용자에게 직접 노출하지 않음

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **FAIL_EMPTY** | WHERE 조건의 코드값이 틀렸을 때 | 실제 존재하는 코드값 직접 확인 → 조건 수정 |
| **SQL_STRUCTURAL** | 하드코딩된 코드값이 의미 검증에서 걸렸을 때 | 메타 정의 vs 실데이터 대조로 정확한 값 확보 |
| **DB_ERROR** | 타입 불일치 시 | 실제 데이터 형태(문자열/숫자) 확인 |

#### `search_code_meta`(A3)와의 차이

| 비교 항목 | `search_code_meta` | `get_column_distinct_values` |
|----------|-------------------|------------------------------|
| 데이터 소스 | MongoDB (정의된 코드 메타) | Query DB (실제 적재 데이터) |
| 커버리지 | 메타에 등록된 코드만 | 실데이터 전체 |
| TYPE-2 불완전성 | 대응 불가 | 직접 대응 |
| 비용 | 경량 (인덱스 조회) | DB 쿼리 실행 (상대적 무거움) |
| 사용 시점 | 1차 탐색 시 | `search_code_meta` 결과가 의심스러울 때 |

#### 사용 예시 (execution_plan)

```json
{
  "step": 2,
  "tool": "get_column_distinct_values",
  "input": "TB_LOAN_MASTER,LOAN_DCD",
  "purpose": "메타의 코드값 '05'가 실데이터에 존재하는지 직접 확인"
}
```

---

### B2. `get_column_stats` — 컬럼 통계 정보 조회

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | DB 직접 탐색 |
| **데이터 소스** | Query DB |
| **입력 형식** | `"테이블명,컬럼명"` |
| **출력** | `dict` — `{min, max, distinct_count, total_count, null_count}` |
| **우선순위** | ★★★ P0 |

#### 실행 SQL

```sql
SELECT
    MIN({column}) AS min_val,
    MAX({column}) AS max_val,
    COUNT(DISTINCT {column}) AS distinct_count,
    COUNT(*) AS total_count,
    SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS null_count
FROM {table}
```

#### 보안 제약

- 식별자 화이트리스트 검증
- PII 컬럼 차단 (B1과 동일)
- 타임아웃 5초 (대용량 테이블 풀스캔 방지)
- 결과는 LLM 내부 추론 전용

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **FAIL_EMPTY** | 날짜 범위가 데이터 범위 밖인지 확인 | MIN/MAX로 즉시 판별 → 조건 수정 |
| **FAIL_EMPTY** | 테이블 자체가 비어있는지 확인 | total_count = 0이면 테이블 문제, 아니면 조건 문제 |
| **DB_ERROR** | 타입 불일치 (숫자 vs 문자열) | min/max 값의 형태로 실제 타입 추론 |
| **SQL_STRUCTURAL** | 금액 단위 스케일 확인 (원 vs 만원 vs 억원) | MIN/MAX로 값 범위 파악 → 단위 추론 |

#### `get_date_distribution`(A7)과의 차이

| 비교 항목 | `get_date_distribution` | `get_column_stats` |
|----------|------------------------|-------------------|
| 대상 컬럼 | 날짜 컬럼 전용 | 모든 타입 (숫자, 문자, 날짜) |
| 반환 형태 | DISTINCT 값 목록 (패턴 분석용) | MIN/MAX/COUNT 요약 통계 |
| 적합한 상황 | 날짜 입도(일별/월별) 파악 | 값 범위·분포·NULL 비율 파악 |

#### 사용 예시

```json
{
  "step": 1,
  "tool": "get_column_stats",
  "input": "TB_LOAN_BALANCE,BASE_DT",
  "purpose": "데이터 기간(MIN~MAX) 확인 — 2024년 3월 데이터 존재 여부 판별"
}
```

---

### B3. `get_table_schema` — 실제 DB 스키마 직접 조회

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | DB 직접 탐색 (카탈로그) |
| **데이터 소스** | Query DB — information_schema 또는 DB 카탈로그 |
| **입력 형식** | `"테이블명"` 또는 `"테이블명,스키마명"` |
| **출력** | `list[dict]` — `[{column_name, data_type, is_nullable, ordinal_position}, ...]` |
| **우선순위** | ★★★ P0 |

#### 실행 SQL (dialect별)

```sql
-- PostgreSQL
SELECT column_name, data_type, is_nullable, ordinal_position
FROM information_schema.columns
WHERE table_name = $1 AND table_schema = $2
ORDER BY ordinal_position

-- Sybase IQ
SELECT c.column_name, c.domain AS data_type, c.nulls AS is_nullable
FROM SYS.SYSCOLUMN c
JOIN SYS.SYSTABLE t ON c.table_id = t.table_id
WHERE t.table_name = $1
ORDER BY c.column_id

-- Impala
DESCRIBE {schema}.{table}
```

#### 보안 제약

- **현행 보안 규칙과의 관계**: `data-security.md`에서 `information_schema, pg_*` 접근을 금지하고 있으나, 이는 **사용자 대상 SQL 생성 시** 카탈로그 노출 방지 목적이다. Recovery의 내부 탐색용으로는 별도 설정 플래그(`allow_catalog_for_recovery: bool`)로 제어한다.
- 테이블명 화이트리스트 검증 (인젝션 방지)
- 반환 결과에서 시스템 컬럼 필터링
- 결과는 LLM 내부 추론 전용

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **DB_ERROR** (컬럼 미존재) | 에러 메시지에 "column X does not exist" 포함 시 | 실제 컬럼 목록 즉시 확인 → 유사 컬럼 찾기 |
| **DB_ERROR** (타입 불일치) | 에러 메시지에 "type mismatch" 포함 시 | 실제 data_type 확인 → CAST 적용 |
| **SQL_STRUCTURAL** | 메타에 없는 컬럼이 실제 존재하는지 확인 | TYPE-3 불완전성(메타 설명 부실) 직접 대응 |
| **TERM_UNRESOLVABLE** | 메타 검색 결과 0건인데 테이블명은 알고 있을 때 | DB 카탈로그에서 직접 스키마 확인 |

#### `search_table_meta`(A2)와의 차이

| 비교 항목 | `search_table_meta` | `get_table_schema` |
|----------|--------------------|--------------------|
| 데이터 소스 | MongoDB (등록된 메타) | Query DB (실제 카탈로그) |
| 커버리지 | 메타에 등록된 테이블만 | DB에 존재하는 모든 테이블 |
| 정보 풍부도 | 설명(alt_name), 업무 용도 포함 | 컬럼명·타입·nullable만 (설명 없음) |
| TYPE-3 대응 | 불완전한 메타 그대로 반환 | 실제 스키마로 메타 보완 가능 |
| 사용 시점 | 1차 탐색 시 | 메타 신뢰 불가 시 실데이터 검증용 |

#### 사용 예시

```json
{
  "step": 1,
  "tool": "get_table_schema",
  "input": "TB_LOAN_MASTER",
  "purpose": "DB 에러 'column LOAN_STATUS does not exist' — 실제 컬럼 목록 확인"
}
```

---

### B4. `search_related_tables` — 연관 테이블 체계적 탐색

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | 메타 탐색 (다중 소스) |
| **데이터 소스** | MongoDB (테이블 메타) + Qdrant (SQL 이력) + Neo4j (FK 관계) |
| **입력 형식** | `"테이블명"` |
| **출력** | `list[dict]` — `[{table_name, relation_type, 공통점, 차이점}, ...]` |
| **우선순위** | ★★★ P0 |

#### 동작 로직 (3단계)

```
1단계: 접두사/접미사 기반 유사 테이블 (MongoDB)
   TB_LOAN_DAILY → TB_LOAN_*, *_LOAN_*
   매칭 기준: 동일 접두사 or 동일 업무 키워드 포함

2단계: SQL 이력에서 함께 사용된 테이블 (Qdrant)
   TB_LOAN_DAILY를 포함한 과거 SQL에서 JOIN된 다른 테이블 추출
   빈도순 정렬 → 자주 함께 쓰이는 테이블이 상위

3단계: FK 이웃 테이블 (Neo4j)
   (:Table {name: $name})-[:FK_TO*1..2]-(neighbor:Table)
   직접 연결(1홉) + 간접 연결(2홉) 테이블
```

#### 보안 제약

- 입력 테이블명 화이트리스트 검증
- 반환 건수 제한 (최대 20개)
- 실데이터 접근 없음 (메타/이력만 조회)

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **SQL_STRUCTURAL** | 테이블 자체가 부적절할 때 | 동일 도메인의 대체 테이블 체계적 발견 |
| **FAIL_EMPTY** | 테이블에 데이터가 없을 때 | 같은 데이터의 다른 집계 테이블 탐색 |
| **TERM_UNRESOLVABLE** | 키워드 검색 결과 0건일 때 | 유사 테이블에서 간접적으로 관련 컬럼 발견 |

#### `search_table_meta`(A2)와의 차이

| 비교 항목 | `search_table_meta` | `search_related_tables` |
|----------|--------------------|-----------------------|
| 탐색 방식 | 키워드 텍스트 매칭 | 구조적 관계(접두사, FK, SQL 이력) 기반 |
| 출발점 | 검색 키워드 (자유 형태) | 특정 테이블명 (이미 알고 있는 테이블) |
| 결과 특성 | 키워드와 유사한 모든 테이블 | 출발 테이블과 관계 있는 테이블만 |
| 적합한 상황 | 어떤 테이블이 있는지 모를 때 | 테이블은 알지만 대안이 필요할 때 |

#### 사용 예시

```json
{
  "step": 1,
  "tool": "search_related_tables",
  "input": "TB_LOAN_DAILY",
  "purpose": "여신 일별 테이블이 부적절 — 같은 도메인의 대체 테이블(월별/잔액 등) 탐색"
}
```

---

### B5. `explore_with_sql` — LLM 자율 SQL 탐색

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | LLM 자율 탐색 |
| **데이터 소스** | Query DB (자유 형태 SELECT) |
| **입력 형식** | LLM이 생성한 SELECT 문 (자유 형태) |
| **출력** | `dict` — `{columns: [...], rows: [...], row_count: int, truncated: bool}` |
| **우선순위** | ★★☆ P1 |

#### 핵심 가치

현행 도구는 모두 **미리 정의된 쿼리 패턴**으로만 DB를 탐색한다.
하지만 Recovery 상황에서 LLM이 실제로 필요한 탐색은 맥락에 따라 다르다:

- "이 테이블에 2024년 3월 데이터가 있어?" → `SELECT COUNT(*) WHERE base_dt BETWEEN ...`
- "이 코드값이 실제로 뭐가 있지?" → `SELECT DISTINCT col, COUNT(*) GROUP BY 1`
- "이 두 테이블의 키가 매칭되나?" → `SELECT a.key FROM a LIMIT 5` 후 `WHERE key IN (...)`
- "금액 단위가 원인지 천원인지?" → `SELECT MIN(amt), MAX(amt), AVG(amt) FROM ...`

이런 **맥락 의존적 자유 형태 탐색**은 고정 도구로 커버할 수 없다.

#### 보안 제약 (다층 방어)

```
Layer 1: sql_safety_checker 재사용
  - SELECT만 허용 (DML/DDL 차단)
  - 다중 쿼리(;) 차단
  - 시스템 카탈로그 접근 차단 (explore_schema와 구분)

Layer 2: 강제 제한 주입
  - LIMIT 50 강제 (기존 LIMIT 있으면 MIN(기존, 50)으로 교체)
  - 실행 타임아웃 5초

Layer 3: PII 필터링
  - sqlglot 파싱으로 SELECT 절의 컬럼 추출
  - pii_columns.yaml 매칭 컬럼은 결과에서 마스킹 처리

Layer 4: 비용 제어
  - LoopGuard에 explore_sql_count 추가 (최대 5회/세션)
  - 결과 행 수 50행, 컬럼 수 20개 제한
```

#### 결과 처리

- 실행 성공: `{columns, rows, row_count, truncated}` 반환 → LLM이 결과를 해석하여 가설 보완
- 실행 실패: `{error: "에러 메시지"}` 반환 → LLM이 에러 원인 분석에 활용
- **사용자에게 직접 노출하지 않음** — Recovery 내부 추론 전용

#### Recovery 시나리오

| failure_type | LLM이 생성할 탐색 SQL 예시 | 기대 효과 |
|-------------|--------------------------|----------|
| **FAIL_EMPTY** | `SELECT COUNT(*) FROM TB_X WHERE base_dt >= '20240301'` | 날짜 조건이 문제인지 즉시 판별 |
| **FAIL_EMPTY** | `SELECT DISTINCT status_cd, COUNT(*) FROM TB_X GROUP BY 1` | 실제 코드값 분포 확인 |
| **DB_ERROR** | `SELECT * FROM TB_X LIMIT 1` | 실제 컬럼명·타입 샘플 확인 |
| **SQL_STRUCTURAL** | `SELECT a.key_col FROM TB_A a LIMIT 5` → `SELECT * FROM TB_B WHERE key_col IN (...)` | JOIN 가능성 직접 검증 |

#### Recovery 사이클 단축 효과

```
현행 (explore_with_sql 없음):
  FAIL_EMPTY → 새 가설 수립 → 탐색(search_table_meta 등) → SQL 생성 → 검증
  = 최소 1 replan 사이클 소모

개선 (explore_with_sql 있음):
  FAIL_EMPTY → 원인 직접 확인(explore_with_sql) → 조건만 수정 → SQL 재생성
  = replan 없이 즉시 수정 가능한 케이스 증가
```

#### 사용 예시

```json
{
  "step": 1,
  "tool": "explore_with_sql",
  "input": "SELECT MIN(BASE_DT), MAX(BASE_DT), COUNT(*) FROM TB_LOAN_BALANCE",
  "purpose": "FAIL_EMPTY 원인 진단: 데이터 기간과 총 건수 확인"
}
```

---

### B6. `explore_graph` — Neo4j 그래프 의도 기반 탐색

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | 그래프 탐색 |
| **데이터 소스** | Neo4j (온톨로지 그래프) |
| **입력 형식** | `"출발점,탐색의도"` |
| **출력** | `list[dict]` — 탐색 의도에 따라 다른 구조 |
| **우선순위** | ★★☆ P1 |

#### 탐색 의도별 동작

그래프 DB의 핵심 장점은 **관계를 타고 범위를 좁히는 것**이다.
Recovery 시점에는 이미 실패한 가설의 컨텍스트(테이블명, 용어, 실패 유형)가 있으므로,
"무작위 탐색"이 아니라 **"실패 지점에서 1~2홉 확장"**이 자연스럽게 이루어진다.

| 탐색 의도 | 출발점 | Cypher 패턴 | 출력 구조 |
|----------|--------|------------|----------|
| `대체_테이블` | 테이블명 | `(t:Table)-[:ALTERNATIVE_OF*1..2]-(alt:Table)` | `[{table_name, 차이점, 권장조건}]` |
| `도메인_테이블` | 업무 키워드 | `(c:DomainConcept)-[:RESOLVED_BY]->(t:Table)` + FK 이웃 | `[{table_name, role, joinable_tables}]` |
| `산출식_분해` | 지표명 | `(root)-[:COMPOSED_OF*1..5]->(leaf)` + MEASURED_BY | `[{formula_name, components: [{name, operator, column, table, agg}]}]` |
| `JOIN_경로` | 테이블A,테이블B | `shortestPath((a)-[:FK_TO*1..4]-(b))` | `[{tables: [...], joins: [{from_col, to_col, type}]}]` |
| `동일_컬럼` | 컬럼명 | `(c:Column)-[:SAME_AS*1..2]-(other:Column)` | `[{column_name, table_name, 매칭_근거}]` |
| `코드_계층` | 코드명 | `(code)-[:PARENT_OF*0..2]->(child)` + APPLIES_TO | `[{code_field, values: [{code_value, name}], tables}]` |
| `업무_프로세스` | 프로세스명 | `(p:Process)-[:PRODUCES_DATA\|CONSUMES_DATA]->(t:Table)` | `[{process_step, table_name, direction}]` |
| `날짜_규칙` | 테이블명 | `(t:Table)-[:DATE_CONVENTION]->(dp:DatePattern)` | `{date_column, format, refresh_cycle}` |

#### 범위 제한 전략

| 제어 수단 | 설명 | 기본값 |
|----------|------|--------|
| **홉 수 제한** | 출발점에서 최대 탐색 깊이 | 2홉 (ALTERNATIVE_OF, SAME_AS), 4홉 (FK_TO, COMPOSED_OF) |
| **결과 수 제한** | 반환할 최대 노드/관계 수 | 20개 |
| **confidence 필터** | FK_TO의 confidence 속성 기반 필터링 | CONFIRMED + INFERRED만 (LOW 제외) |
| **failure_type 기반 의도 추천** | Recovery 프롬프트에서 failure_type별 권장 탐색 의도 명시 | 아래 매핑표 참조 |

#### failure_type → 탐색 의도 매핑

| failure_type | 1순위 탐색 의도 | 2순위 탐색 의도 |
|-------------|---------------|---------------|
| **SQL_STRUCTURAL** (테이블 부적절) | `대체_테이블` | `도메인_테이블` |
| **SQL_STRUCTURAL** (JOIN 실패) | `JOIN_경로` | `동일_컬럼` |
| **FAIL_EMPTY** | `날짜_규칙` | `대체_테이블` |
| **TERM_UNRESOLVABLE** (지표) | `산출식_분해` | `도메인_테이블` |
| **TERM_UNRESOLVABLE** (용어) | `도메인_테이블` | `업무_프로세스` |
| **DB_ERROR** (컬럼 미존재) | `동일_컬럼` | — |

#### 보안 제약

- 읽기 전용 (Cypher MATCH만, CREATE/MERGE/DELETE 불가)
- 트랜잭션 타임아웃 10초 (config: `neo4j_request_timeout`)
- 결과 수 제한 (LIMIT 20)
- 실데이터 접근 없음 (온톨로지 메타만 탐색)

#### 사용 예시

```json
{
  "step": 1,
  "tool": "explore_graph",
  "input": "TB_LOAN_DAILY,대체_테이블",
  "purpose": "여신 일별 테이블이 부적절 — 대체 가능한 월별/잔액 테이블 그래프 탐색"
}
```

```json
{
  "step": 2,
  "tool": "explore_graph",
  "input": "연체율,산출식_분해",
  "purpose": "연체율 산출식의 구성요소(연체원금, 여신잔액)와 해당 컬럼/테이블 확인"
}
```

---

### B7. `search_report_sql` — 보고서 SQL 검색

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | 메타 탐색 |
| **데이터 소스** | MongoDB (보고서 SQL 인덱스) |
| **입력 형식** | `"검색 키워드"` |
| **출력** | `list[dict]` — `[{report_name, description, sql, tables_used, created_at}, ...]` |
| **우선순위** | ★★☆ P1 |

#### `search_use_cases`(A1)와의 차이

| 비교 항목 | `search_use_cases` | `search_report_sql` |
|----------|-------------------|---------------------|
| 데이터 소스 | Qdrant (SQL 실행 이력) | MongoDB (보고서 SQL) |
| 데이터 성격 | 사용자들의 과거 질의 SQL (비공식) | 공식 보고서 SQL (검증됨) |
| 신뢰도 | 중간 (과거에 성공했지만 최적은 아닐 수 있음) | 높음 (공식 보고서로 검증·승인된 SQL) |
| 포함 정보 | SQL + description | SQL + 보고서명 + 요건 설명 + 사용 테이블 목록 |
| 검색 방식 | 벡터 유사도 | 텍스트 매칭 + 테이블/보고서명 인덱스 |

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **TERM_UNRESOLVABLE** | 지표/용어의 정확한 산출 방법을 모를 때 | 공식 보고서에서 검증된 SQL 패턴 참조 |
| **SQL_STRUCTURAL** | 테이블/JOIN 구조가 불확실할 때 | 보고서 SQL의 테이블 조합을 신뢰 가능한 참고안으로 활용 |
| **FAIL_EMPTY** | 올바른 조건 조합을 모를 때 | 보고서의 WHERE 절 패턴 참조 |

#### 사용 예시

```json
{
  "step": 1,
  "tool": "search_report_sql",
  "input": "연체율 보고서",
  "purpose": "연체율 산출에 사용되는 공식 보고서 SQL 참조"
}
```

---

### B8. `validate_join_path` — JOIN 경로 데이터 레벨 검증

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | DB 직접 탐색 + 그래프 탐색 |
| **데이터 소스** | Query DB + Neo4j |
| **입력 형식** | `"테이블A,테이블B"` |
| **출력** | `dict` — `{path_exists: bool, join_columns: [...], sample_match_rate: float, via_tables: [...]}` |
| **우선순위** | ★★☆ P1 |

#### 동작 로직 (3단계)

```
1단계: Neo4j에서 JOIN 경로 조회
   shortestPath((a:Table)-[:FK_TO*1..4]-(b:Table))
   → join_columns, via_tables 추출

2단계: 컬럼 매칭 검증 (경로가 없을 경우)
   두 테이블의 컬럼 목록 비교
   SAME_AS 관계 + 이름 유사도로 JOIN 가능한 컬럼 쌍 추정

3단계: 데이터 레벨 spot-check (선택적)
   테이블A에서 키 컬럼 샘플 5건 추출
   테이블B에서 해당 키값 존재 여부 확인
   → match_rate = 매칭된 건수 / 5
```

#### 보안 제약

- spot-check SQL은 `sql_safety_checker` 통과 필수
- LIMIT 5 강제 (경량 검증)
- PII 컬럼이 키인 경우 데이터 레벨 검증 생략
- 타임아웃 5초

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **SQL_STRUCTURAL** (JOIN 오류) | 두 테이블 간 JOIN이 의미 검증에서 걸렸을 때 | 올바른 JOIN 컬럼 + 우회 경로 발견 |
| **DB_ERROR** | JOIN 시 컬럼 타입 불일치 에러 | 실제 키 컬럼 타입 대조 → CAST 필요 여부 판단 |
| **FAIL_EMPTY** | JOIN 결과가 0건 | match_rate로 키 매칭률 확인 → 키 불일치가 원인인지 판별 |

#### 사용 예시

```json
{
  "step": 1,
  "tool": "validate_join_path",
  "input": "TB_LOAN_MASTER,TB_CUSTOMER",
  "purpose": "두 테이블 간 JOIN 가능한 컬럼 확인 + 데이터 매칭률 검증"
}
```

---

### B9. `check_data_freshness` — 데이터 갱신 상태 확인

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | DB 직접 탐색 |
| **데이터 소스** | Query DB |
| **입력 형식** | `"테이블명,날짜컬럼명"` |
| **출력** | `dict` — `{max_date: str, is_current: bool, gap_days: int}` |
| **우선순위** | ★☆☆ P2 |

#### 실행 SQL

```sql
SELECT MAX({date_column}) AS max_date FROM {table}
```

`gap_days`는 `오늘 - max_date`로 계산하며, `is_current`는 갱신주기 대비 판단:
- 일별 테이블: gap ≤ 2일이면 current
- 월별 테이블: gap ≤ 35일이면 current
- 갱신주기 정보는 Neo4j `DATE_CONVENTION` 또는 테이블 메타에서 참조

#### `get_column_stats`(B2)와의 차이

| 비교 항목 | `get_column_stats` | `check_data_freshness` |
|----------|-------------------|----------------------|
| 쿼리 비용 | 풀스캔 가능 (MIN/MAX/COUNT 3개 집계) | MAX 1건만 (인덱스 활용 가능, 경량) |
| 반환 정보 | 범용 통계 (모든 타입) | 갱신 상태 판단에 특화 (current 여부 포함) |
| 적합한 상황 | 범위·분포·NULL 등 종합 파악 | "최신 데이터가 있나?" 단일 질문에 답할 때 |

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **FAIL_EMPTY** | "3월 데이터 뽑아줘" → 0건 | MAX(기준일자) = 2월 28일 → "아직 3월 데이터 미적재" 진단 |
| **FAIL_EMPTY** | 월말 스냅샷 테이블에서 중간일 조회 | is_current=true지만 gap 확인 → 스냅샷 특성 안내 |

#### 사용 예시

```json
{
  "step": 1,
  "tool": "check_data_freshness",
  "input": "TB_LOAN_BALANCE,BASE_DT",
  "purpose": "FAIL_EMPTY 원인: 3월 데이터가 아직 적재되지 않았을 가능성 확인"
}
```

---

### B10. `explain_sql` — SQL 실행 계획 분석

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | DB 직접 탐색 |
| **데이터 소스** | Query DB |
| **입력 형식** | SQL 문 (자유 형태) |
| **출력** | `dict` — `{plan_text: str, estimated_rows: int, scan_type: str, estimated_cost: float}` |
| **우선순위** | ★☆☆ P2 |

#### 실행 SQL (dialect별)

```sql
-- PostgreSQL
EXPLAIN (FORMAT JSON) {sql}

-- Sybase IQ
SET TEMPORARY OPTION Query_Plan = 'ON';
{sql}  -- 실행 계획이 메시지로 반환

-- Impala
EXPLAIN {sql}
```

#### 보안 제약

- 입력 SQL은 `sql_safety_checker` 통과 필수 (SELECT만 허용)
- EXPLAIN만 실행 (EXPLAIN ANALYZE는 실제 실행이므로 기본 비활성)
- 타임아웃 5초
- 결과는 LLM 내부 추론 전용

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **DB_ERROR** (timeout) | 쿼리 타임아웃 발생 시 | 풀스캔 여부, 예상 행 수 확인 → 파티션 키 추가/쿼리 단순화 방향 도출 |
| **DB_ERROR** (일반) | 복잡한 쿼리의 실행 계획 확인 | 병목 구간 파악 → 서브쿼리 분리 등 구조 개선 |

#### 사용 예시

```json
{
  "step": 1,
  "tool": "explain_sql",
  "input": "SELECT ... (타임아웃 발생한 원본 SQL)",
  "purpose": "타임아웃 원인 진단: 풀스캔 여부와 예상 행 수 확인"
}
```

---

### B11. `search_similar_columns` — 유사 컬럼명 퍼지 매칭

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | 메타 탐색 |
| **데이터 소스** | MongoDB (테이블 메타) |
| **입력 형식** | `"컬럼명"` |
| **출력** | `list[dict]` — `[{column_name, table_name, similarity_score, alt_name}, ...]` |
| **우선순위** | ★☆☆ P2 |

#### 매칭 로직

```
1. 약어 확장 사전 매칭:
   CUST → CUSTOMER, CSTMR
   ACCT → ACCOUNT, ACC
   NO → NUM, NUMBER, ID
   AMT → AMOUNT
   DT → DATE
   CD → CODE
   NM → NAME

2. Edit distance (Levenshtein) 기반 퍼지 매칭:
   threshold ≤ 3 (삽입/삭제/치환 3회 이내)

3. 토큰 분할 매칭:
   LOAN_STATUS_CD → [LOAN, STATUS, CD]
   LN_STAT_CODE → [LN, STAT, CODE]
   → 토큰 단위 약어 사전 매칭 후 유사도 계산
```

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **DB_ERROR** (컬럼 미존재) | `CUST_NM` 없음 에러 시 | `CSTMR_NM`, `CUSTOMER_NAME` 등 유사 컬럼 발견 |
| **SQL_STRUCTURAL** | 메타의 컬럼명과 실제 DB 컬럼명이 다를 때 | 금융 IT 약어 관행 차이 극복 |

#### 사용 예시

```json
{
  "step": 2,
  "tool": "search_similar_columns",
  "input": "CUST_NM",
  "purpose": "DB 에러 'column CUST_NM does not exist' — 유사 컬럼(CSTMR_NM 등) 탐색"
}
```

---

### B12. `get_table_row_count` — 테이블 행 수 확인

#### 개요

| 항목 | 내용 |
|------|------|
| **카테고리** | DB 직접 탐색 |
| **데이터 소스** | Query DB |
| **입력 형식** | `"테이블명"` 또는 `"테이블명,조건절"` |
| **출력** | `dict` — `{total_count: int, condition: str}` |
| **우선순위** | ★☆☆ P2 |

#### 실행 SQL

```sql
-- 조건 없이
SELECT COUNT(*) AS total_count FROM {table}

-- 조건 있을 때 (조건절은 sql_safety_checker 통과 필수)
SELECT COUNT(*) AS total_count FROM {table} WHERE {condition}
```

#### `get_column_stats`(B2)와의 차이

| 비교 항목 | `get_column_stats` | `get_table_row_count` |
|----------|-------------------|-----------------------|
| 입력 | 테이블 + 컬럼 필수 | 테이블만 (조건 선택적) |
| 출력 | 5가지 통계 (MIN/MAX/DISTINCT/TOTAL/NULL) | COUNT 1개만 |
| 조건절 | 불가 | 가능 (`WHERE base_dt >= '20240301'`) |
| 적합한 상황 | 특정 컬럼의 값 분포 파악 | "이 조건으로 몇 건이나 되나?" 빠른 확인 |

#### Recovery 시나리오

| failure_type | 사용 시점 | 기대 효과 |
|-------------|----------|----------|
| **FAIL_EMPTY** | 테이블 자체가 비었는지 vs 조건 문제인지 | 조건 없이 COUNT → 0이면 테이블 문제 |
| **FAIL_EMPTY** | 조건을 점진적으로 좁혀가며 디버깅 | 전체 → 날짜조건 → 코드조건 순서로 COUNT 확인 |

#### 사용 예시

```json
{
  "step": 1,
  "tool": "get_table_row_count",
  "input": "TB_LOAN_MASTER",
  "purpose": "FAIL_EMPTY: 테이블 자체가 비어있는지 먼저 확인"
}
```

---

## 4. Neo4j 데이터 확장 상세

현행 Neo4j 스키마(6노드, 9엣지)에 추가하여,
Recovery 도구가 더 풍부한 그래프 탐색을 수행할 수 있도록 아래 관계/노드를 추가한다.

---

### C1. `ALTERNATIVE_OF` — 대체 가능 테이블 관계

#### 스키마

```
(:Table)-[:ALTERNATIVE_OF {
    차이점: str,        -- "일별 vs 월별", "상세 vs 요약"
    권장조건: str,      -- "집계 시 월별 우선", "최근 1년은 일별만 존재"
    confidence: str    -- CONFIRMED | INFERRED
}]->(:Table)
```

#### 시딩 전략

```
1단계: 규칙 기반 자동 추론
   접미사 패턴 매칭:
     TB_X_D ↔ TB_X_M (일별 ↔ 월별)
     TB_X_DETAIL ↔ TB_X_SUMMARY (상세 ↔ 요약)
     TB_X ↔ TB_X_HIST (현행 ↔ 이력)
     TB_X_BAL ↔ TB_X_TXN (잔액 ↔ 거래)
   confidence: INFERRED

2단계: SQL 이력 기반 보강
   동일 질의 유형에 대해 다른 테이블이 사용된 이력이 있으면 ALTERNATIVE_OF 추가
   confidence: INFERRED

3단계: 수동 등록
   도메인 전문가가 명시적으로 등록
   confidence: CONFIRMED
```

#### Recovery 활용

- `SQL_STRUCTURAL` → `explore_graph("TB_LOAN_DAILY,대체_테이블")` → `TB_LOAN_MONTHLY` 발견
- `FAIL_EMPTY` → 일별 테이블에 해당 기간 데이터 없음 → 월별 대체 테이블로 전환

---

### C2. `SAME_AS` — 동일 의미 컬럼 관계

#### 스키마

```
(:Column)-[:SAME_AS {
    매칭_근거: str,     -- "약어 패턴", "SQL 이력 JOIN 패턴", "수동 등록"
    confidence: str    -- CONFIRMED | INFERRED
}]->(:Column)
```

#### 시딩 전략

```
1단계: 약어 사전 기반 자동 매칭
   CUST_NO ↔ CSTMR_ID (CUST=CSTMR, NO=ID)
   ACCT_NO ↔ ACC_NUM (ACCT=ACC, NO=NUM)
   confidence: INFERRED

2단계: SQL 이력 기반 JOIN 패턴 분석
   과거 SQL에서 JOIN ON a.col1 = b.col2 패턴 추출
   col1 ≠ col2이면서 JOIN에 사용된 경우 SAME_AS 추가
   confidence: INFERRED (빈도 5회 이상이면 CONFIRMED)

3단계: FK_TO 관계에서 역추론
   (:Table)-[:FK_TO {from_column, to_column}]->(:Table)
   from_column ≠ to_column이면 SAME_AS 후보
   confidence: CONFIRMED (FK는 명시적 관계)
```

#### Recovery 활용

- `DB_ERROR` (컬럼 미존재) → `explore_graph("CUST_NO,동일_컬럼")` → `CSTMR_ID` 발견
- `SQL_STRUCTURAL` (JOIN 실패) → `validate_join_path`에서 SAME_AS 활용

---

### C3. `QueryPattern` — 검증된 SQL 사용 패턴

#### 스키마

```
(:QueryPattern {
    pattern_id: str,
    query_type: str,       -- "기간별_추이", "랭킹", "비교", "집계", "목록_조회"
    description: str,
    sql_template: str,     -- 파라미터화된 SQL 템플릿
    frequency: int         -- 이 패턴이 사용된 횟수
})-[:USES {role: str}]->(:Table)
  -[:JOINS]->(:Table)
  -[:FILTERS_BY]->(:Column)
  -[:AGGREGATES]->(:Column)
```

#### 시딩 전략

```
1단계: SQL 이력에서 자동 추출
   sqlglot으로 파싱 → 테이블/JOIN/WHERE/GROUP BY 패턴 추출
   유사 패턴끼리 클러스터링 → 대표 패턴을 QueryPattern 노드로 생성
   frequency = 클러스터 내 SQL 수

2단계: 보고서 SQL에서 추출
   공식 보고서 SQL → QueryPattern (frequency 가중치 부여)

3단계: 런타임 수집
   성공한 SQL → OntologyIngestor가 새 패턴 추가/기존 패턴 frequency 증가
```

#### Recovery 활용

- `TERM_UNRESOLVABLE` → "이 업무에는 보통 어떤 테이블 조합을 쓰지?" 질문에 구조적으로 답변
- `SQL_STRUCTURAL` → 동일 query_type의 검증된 패턴 참조 → 테이블/JOIN 구조 수정

---

### C4. `DATE_CONVENTION` — 날짜 규칙

#### 스키마

```
(:Table)-[:DATE_CONVENTION]->(dp:DatePattern {
    date_column: str,     -- "BASE_DT", "STD_YM"
    format: str,          -- "YYYYMMDD", "YYYYMM", "YYYY-MM-DD"
    granularity: str,     -- "일별", "월별", "월말_스냅샷", "분기말"
    refresh_cycle: str,   -- "매일 06:00", "매월 말일+2영업일"
    partition_key: bool   -- 파티션 키 여부
})
```

#### 시딩 전략

```
1단계: get_date_distribution 결과 분석
   detect_date_pattern() 결과 + 테이블별 날짜 컬럼 매핑
   자동 생성 가능

2단계: 테이블 메타에서 갱신주기 추출
   MongoDB 메타의 refresh_cycle 필드 참조

3단계: 수동 등록
   배치 스케줄 담당자가 실제 갱신 주기 확인 후 등록
```

#### Recovery 활용

- `FAIL_EMPTY` → `explore_graph("TB_LOAN_BAL,날짜_규칙")` → "월말 스냅샷, BASE_DT, YYYYMMDD" 확인
  → 중간일(20240315) 조회가 실패한 이유 즉시 파악
- `SQL_STRUCTURAL` → 날짜 필터 형식 불일치 방지 (`YYYYMMDD` vs `YYYY-MM-DD`)

---

### C5. `Process` — 업무 프로세스 흐름

#### 스키마

```
(:Process {
    name: str,           -- "여신 심사", "여신 실행", "수신 개설"
    department: str,     -- "여신심사부", "여신관리부"
    description: str
})-[:STEP {순서: int}]->(:Process)
  -[:PRODUCES_DATA]->(:Table)
  -[:CONSUMES_DATA]->(:Table)
```

#### 시딩 전략

```
1단계: 업무 매뉴얼(Qdrant)에서 프로세스 추출
   매뉴얼 문서 → LLM으로 프로세스 단계 + 관련 데이터 추출
   confidence: INFERRED

2단계: 수동 등록
   업무 담당자가 프로세스 흐름 + 산출 데이터 명시
   confidence: CONFIRMED
```

#### Recovery 활용

- `TERM_UNRESOLVABLE` → `explore_graph("여신 실행,업무_프로세스")` → 관련 테이블 체계적 탐색
- 모호한 요청("여신 관련 데이터") → 업무 프로세스 단계별 테이블 매핑으로 범위 좁히기

---

## 5. LoopGuard 확장

신규 도구 추가에 따른 비용 제어를 위해 LoopGuard에 카운터를 추가한다.

### 현행

```python
class LoopGuard(BaseModel):
    total_tool_calls: int    # MAX: 20
    replan_count: int        # MAX: 3
    generate_attempts: int   # MAX: 4
    local_fix_count: int     # MAX: 2
```

### 확장

```python
class LoopGuard(BaseModel):
    total_tool_calls: int        # MAX: 25 (도구 증가 반영)
    replan_count: int            # MAX: 3
    generate_attempts: int       # MAX: 4
    local_fix_count: int         # MAX: 2
    explore_sql_count: int       # MAX: 5 (B5 전용)
    db_query_count: int          # MAX: 10 (B1,B2,B3,B9,B10,B12 합산)
    graph_query_count: int       # MAX: 8 (B6 전용)
```

| 카운터 | 대상 도구 | 제한 사유 |
|--------|----------|----------|
| `explore_sql_count` | `explore_with_sql` | 자유 형태 SQL은 비용·보안 리스크 최대 → 별도 제어 |
| `db_query_count` | B1, B2, B3, B9, B10, B12 | Query DB 직접 쿼리 합산 → DB 부하 제어 |
| `graph_query_count` | `explore_graph` | Neo4j 쿼리 합산 → 그래프 DB 부하 제어 |

---

## 6. Recovery 프롬프트 변경 가이드

`recovery_planner_system.txt`의 "사용 가능한 도구" 섹션에 신규 도구를 추가하되,
**failure_type별 권장 도구**를 명시하여 LLM이 적절한 도구를 선택하도록 유도한다.

### 추가할 도구 테이블

```
| 도구명 | 입력 형식 | 설명 |
|---|---|---|
| get_column_distinct_values | "테이블명,컬럼명" | 컬럼의 실제 DISTINCT 값 + 건수 (최대 50) |
| get_column_stats | "테이블명,컬럼명" | MIN/MAX/DISTINCT수/총건수/NULL수 통계 |
| get_table_schema | "테이블명" | 실제 DB 스키마(컬럼명, 타입, nullable) 조회 |
| search_related_tables | "테이블명" | 접두사/FK/SQL이력 기반 연관 테이블 탐색 |
| explore_with_sql | "SELECT ..." | 자유 형태 탐색 SQL 실행 (LIMIT 50 강제) |
| explore_graph | "출발점,탐색의도" | Neo4j 그래프 의도 기반 탐색 |
| search_report_sql | "검색 키워드" | 공식 보고서 SQL 검색 |
| validate_join_path | "테이블A,테이블B" | JOIN 경로 + 데이터 매칭률 검증 |
| check_data_freshness | "테이블명,날짜컬럼명" | MAX(날짜) 조회로 데이터 갱신 상태 확인 |
| explain_sql | "SELECT ..." | SQL 실행 계획 분석 (비용, 스캔 방식) |
| search_similar_columns | "컬럼명" | 약어/유사 컬럼명 퍼지 매칭 |
| get_table_row_count | "테이블명" 또는 "테이블명,조건" | COUNT(*) 조회 |
```

### 추가할 failure_type별 권장 도구 섹션

```
### failure_type별 권장 도구 우선순위

#### SQL_STRUCTURAL
1순위: explore_graph (대체_테이블 / JOIN_경로)
2순위: search_related_tables
3순위: get_table_schema (컬럼 존재 여부 직접 확인)
4순위: explore_with_sql (가설 직접 검증)

#### EMPTY_RESULT
1순위: get_column_stats (날짜 MIN/MAX로 범위 확인)
2순위: get_column_distinct_values (코드값 실데이터 확인)
3순위: check_data_freshness (데이터 미적재 여부)
4순위: get_table_row_count (테이블 비어있음 vs 조건 문제)

#### TERM_UNRESOLVABLE
1순위: explore_graph (산출식_분해 / 도메인_테이블 / 업무_프로세스)
2순위: search_report_sql (공식 보고서에서 정의 참조)
3순위: search_manual (업무 매뉴얼 재검색 — 동의어/유사어)
4순위: search_glossary (용어사전 확장 검색)

#### DB_ERROR
1순위: get_table_schema (실제 스키마 vs 메타 대조)
2순위: search_similar_columns (컬럼명 약어 차이)
3순위: explain_sql (타임아웃 시 실행계획 분석)
4순위: explore_with_sql (에러 원인 직접 탐색)
```

---

## 7. 아키텍처 영향 요약

### 변경 대상 파일

| 파일 | 변경 내용 | 영향도 |
|------|----------|--------|
| `src/agents/nodes/reason/tools.py` | 신규 도구 함수 + TOOL_MAP 확장 | 높음 |
| `resources/prompts/reason/recovery_planner_system.txt` | 도구 테이블 + failure_type별 권장 도구 | 높음 |
| `src/agents/state/state.py` | LoopGuard 카운터 확장 | 중간 |
| `src/connectors/impl/neo4j_connector.py` | 신규 Cypher 쿼리 (ALTERNATIVE_OF, SAME_AS 등) | 중간 |
| `src/agents/nodes/reason/context_explorer.py` | 신규 도구 결과 해석(interpret) 로직 | 중간 |
| `devtools/scripts/seed_neo4j.py` | C1~C5 관계/노드 시딩 로직 | 중간 |
| `resources/connectors/neo4j/` | 신규 Cypher 파일 추가 | 낮음 |
| `resources/domain/pii_columns.yaml` | PII 차단 대상 컬럼 목록 (B1, B5에서 참조) | 낮음 |
| `src/services/sql_safety_checker.py` | `explore_with_sql`, `explain_sql` 입력 검증 | 낮음 |

### 기존 코드 호환성

- TOOL_MAP 확장은 기존 도구에 영향 없음 (추가만)
- LoopGuard 신규 필드는 `default=0`으로 하위 호환
- Recovery 프롬프트 변경은 기존 예시 유지 + 신규 도구 섹션 추가
- Neo4j 스키마 변경은 기존 노드/엣지 유지 + 신규 추가

---

## 8. 도구 간 중복·대체 관계 분석

신규 도구 12개 중 일부는 기능이 겹친다.
LLM이 올바른 도구를 선택하도록 **각 도구의 고유 용도**를 명확히 구분한다.

### DB 직접 탐색 도구 분화 맵

```
                          ┌─ get_column_distinct_values (B1)
                          │   "이 컬럼에 어떤 값이 있지?"
                          │   → 코드값 목록, 카테고리 분포
                          │
  특정 컬럼 탐색 ─────────┼─ get_column_stats (B2)
                          │   "이 컬럼의 범위와 분포는?"
                          │   → MIN/MAX/COUNT/NULL (요약 통계)
                          │
                          └─ get_date_distribution (A7, 기존)
                              "이 날짜 컬럼의 입도는?"
                              → DISTINCT 날짜 목록 (패턴 분석)

                          ┌─ get_table_row_count (B12)
                          │   "이 조건으로 몇 건?"
  테이블 레벨 탐색 ───────┤   → COUNT만 (조건절 가능, 경량)
                          │
                          ├─ get_sample_rows (A6, 기존)
                          │   "이 테이블에 뭐가 들어있지?"
                          │   → 실제 행 10개 (컬럼 구조 + 데이터 패턴 파악)
                          │
                          ├─ get_table_schema (B3)
                          │   "이 테이블의 정확한 스키마는?"
                          │   → 컬럼명/타입/nullable (데이터 없이 구조만)
                          │
                          └─ check_data_freshness (B9)
                              "최신 데이터가 언제까지?"
                              → MAX(날짜) + 갱신 판단 (단일 값, 최경량)

                          ┌─ explore_with_sql (B5)
  자유 형태 탐색 ─────────┤   "내가 직접 확인하고 싶은 게 있어"
                          │   → 맥락 의존적 SQL (위 도구로 해결 안 될 때)
                          │
                          └─ explain_sql (B10)
                              "이 쿼리가 왜 느린지 알고 싶어"
                              → 실행 계획 (timeout 전용)
```

### 선택 가이드: LLM이 혼동하기 쉬운 쌍

| 혼동 가능 쌍 | 구분 기준 |
|-------------|----------|
| B1 vs A3 (`search_code_meta`) | A3은 메타에 정의된 코드, B1은 실데이터. **A3 먼저 시도 → 의심스러우면 B1** |
| B2 vs B9 | B2는 범용 통계(5가지), B9는 최신 날짜만(1가지). **"최신 데이터?" → B9, "범위·분포?" → B2** |
| B2 vs B12 | B2는 특정 컬럼 통계, B12는 조건부 건수. **"컬럼값 범위?" → B2, "조건 만족 건수?" → B12** |
| B3 vs A2 (`search_table_meta`) | A2는 업무 설명 포함 메타, B3는 실제 DB 스키마. **업무 이해 → A2, 구조 검증 → B3** |
| B4 vs A2 (`search_table_meta`) | A2는 키워드 검색, B4는 기존 테이블 기반 관계 탐색. **모르는 테이블 찾기 → A2, 대안 찾기 → B4** |
| B5 vs B1/B2/B12 | B1/B2/B12는 고정 패턴, B5는 자유 형태. **고정 도구로 해결 가능하면 고정 도구 우선** |
| B6 vs B4 | B6은 그래프 전체 탐색, B4는 연관 테이블만. **FK/산출식/프로세스 필요 → B6, 대체 테이블만 → B4** |
| B7 vs A1 (`search_use_cases`) | A1은 비공식 SQL 이력, B7은 공식 보고서. **신뢰도 필요 → B7, 범용 참조 → A1** |

---

## 9. context_explorer 통합 설계

### 신규 도구의 호출 시점

신규 도구는 기존 도구와 동일하게 **context_explorer 노드의 execution_plan 루프**에서 호출된다.
별도의 실행 경로를 만들지 않고 기존 아키텍처에 자연스럽게 통합된다.

```
recovery_planner_node
  └─ execution_plan: [
       {tool: "get_column_stats", input: "TB_X,BASE_DT", ...},
       {tool: "explore_graph", input: "TB_X,대체_테이블", ...},
       {tool: "explore_with_sql", input: "SELECT ...", ...},
     ]
  ↓
context_explorer_node
  └─ Phase 1: execute_tool(step.tool, step.input)  ← 기존 디스패처 그대로
  └─ Phase 3: batch_interpret(results)              ← 해석 로직 확장 필요
  └─ Phase 4~6: apply insights, select tables, promote knowledge
```

### batch_interpret 결과 해석 확장

context_explorer의 `_interpret_batch`는 도구 결과를 LLM에 전달하여 해석한다.
신규 도구의 결과도 동일한 batch_interpret 프롬프트에 포함되지만,
**도구 유형별 해석 힌트**를 LLM에 제공하여 정확한 인사이트 추출을 유도한다.

| 도구 | 결과 해석 힌트 (LLM에 전달) |
|------|--------------------------|
| `get_column_distinct_values` | "실데이터 코드값 목록입니다. 메타 정의와 대조하여 불일치를 식별하세요." |
| `get_column_stats` | "컬럼 통계입니다. 날짜 범위, 금액 단위, NULL 비율을 분석하세요." |
| `get_table_schema` | "실제 DB 스키마입니다. 메타에 없는 컬럼이나 타입 차이를 식별하세요." |
| `search_related_tables` | "연관 테이블 목록입니다. 현재 실패한 테이블의 대안으로 적합한 것을 판단하세요." |
| `explore_with_sql` | "자유 탐색 SQL 결과입니다. 실패 원인 진단에 필요한 사실을 추출하세요." |
| `explore_graph` | "그래프 탐색 결과입니다. 관계 경로에서 새로운 테이블/컬럼/산출식 정보를 추출하세요." |
| `search_report_sql` | "공식 보고서 SQL입니다. 테이블 조합, JOIN 패턴, WHERE 조건을 참고하세요." |
| `validate_join_path` | "JOIN 경로 검증 결과입니다. match_rate가 낮으면 JOIN 키 불일치가 원인입니다." |
| `check_data_freshness` | "데이터 갱신 상태입니다. is_current=false면 데이터 미적재를 사용자에게 안내하세요." |
| `explain_sql` | "실행 계획입니다. Seq Scan이면 파티션 키 필터 추가, 예상 행 수가 크면 조건 축소를 제안하세요." |
| `search_similar_columns` | "유사 컬럼 후보입니다. similarity_score가 높은 것을 실제 컬럼으로 채택하세요." |
| `get_table_row_count` | "건수 결과입니다. 0건이면 테이블 비어있음, 양수인데 이전 쿼리 0건이면 조건 문제입니다." |

---

## 10. B12 조건절 SQL 인젝션 방어 상세

`get_table_row_count`는 조건절을 파라미터로 받으므로 **SQL 인젝션 리스크**가 존재한다.
`explore_with_sql`(B5)과 달리 LLM이 아닌 **다른 도구의 결과에서 조건을 조립**하는 패턴이므로,
별도의 방어 전략이 필요하다.

### 방어 전략

```
Layer 1: 조건절 화이트리스트 문법 검증
  - sqlglot으로 파싱하여 WHERE 절로 유효한지 확인
  - 서브쿼리 금지 (SELECT, UNION 포함 시 거부)
  - 세미콜론(;) 포함 시 거부
  - 시스템 함수 호출 금지 (pg_sleep, LOAD_FILE 등)

Layer 2: 식별자 검증
  - 조건절 내 컬럼명은 _IDENT_RE 패턴 매칭
  - 리터럴 값은 문자열('...')/숫자/NULL/BETWEEN/IN만 허용

Layer 3: 실행 제한
  - 타임아웃 5초
  - COUNT(*) 전용 (결과 자체는 숫자 1개뿐 → 데이터 유출 불가)

Layer 4: 입력 출처 제한
  - recovery_planner의 execution_plan에서만 호출 가능
  - 사용자 입력이 직접 조건절에 도달하지 않음
  - LLM이 생성한 조건도 Layer 1~2 통과 필수
```

### 허용 예시 vs 거부 예시

```
허용:
  "BASE_DT >= '20240301'"
  "LOAN_DCD IN ('01','02','03')"
  "BASE_DT BETWEEN '20240101' AND '20240331'"
  "STAT_CD = '01' AND BRC_CD = '001'"

거부:
  "1=1; DROP TABLE TB_X"                    → 세미콜론
  "LOAN_DCD = (SELECT code FROM ...)"       → 서브쿼리
  "pg_sleep(10)"                            → 시스템 함수
  "BASE_DT >= '20240301' UNION SELECT ..."  → UNION
```

---

## 11. 폐쇄망 배포 고려사항

신규 도구는 폐쇄망 환경의 제약을 반드시 고려해야 한다.

### DB 부하

| 환경 | Query DB | 특성 |
|------|----------|------|
| 개발 | PostgreSQL | 전용 인스턴스, 부하 제약 낮음 |
| 폐쇄망 | Sybase IQ / Impala | 공유 정보계 DB, 부하에 민감 |

**대응:**

- `db_query_count` 상한은 config로 환경별 조정 가능하게 설계 (개발: 10, 폐쇄망: 5)
- `explore_with_sql` 타임아웃도 환경별 설정 (개발: 5초, 폐쇄망: 3초)
- Sybase IQ는 `SET TEMPORARY OPTION` 기반 EXPLAIN이 비표준 → `explain_sql`(B10)은 폐쇄망에서 비활성화 옵션 제공

### Neo4j 가용성

| 환경 | Neo4j 상태 | `explore_graph` 동작 |
|------|------------|---------------------|
| 개발 | 실 인스턴스 | 정상 Cypher 실행 |
| 폐쇄망 (Neo4j 있음) | 실 인스턴스 | 정상 Cypher 실행 |
| 폐쇄망 (Neo4j 없음) | Dummy 모드 | 더미 응답 → 그래프 도구 효과 제한적 |

**대응:**

- `explore_graph`는 Neo4j 미가용 시 `search_related_tables`의 MongoDB/Qdrant 부분만 실행 (graceful degradation)
- Dummy 모드에서도 ALTERNATIVE_OF, SAME_AS 데이터를 YAML 파일로 제공하여 기본 탐색 가능

### 오픈소스 LLM 호환성

폐쇄망 LLM(Solar Pro 2 70B, Qwen3.5 397B)은 도구 선택 능력이 Claude 대비 약할 수 있다.

**대응:**

- Recovery 프롬프트의 failure_type별 권장 도구를 **더 명시적으로** 작성
- 도구 수가 19개(기존 7 + 신규 12)로 늘어나면 LLM이 혼란 가능 → Phase별 점진 도입으로 한 번에 노출되는 도구 수 제어
- `_build_replan_execution` (rule-based fallback)에 신규 도구 포함 → LLM이 적절한 도구를 선택하지 못해도 규칙 기반으로 커버

---

## 12. 성공 측정 기준

도구 추가 효과를 정량적으로 평가하기 위한 지표를 정의한다.

### 핵심 지표

| 지표 | 측정 방법 | 목표 |
|------|----------|------|
| **Recovery 성공률** | replan 후 최종 SUCCESS 비율 | 현행 대비 +20%p |
| **평균 replan 횟수** | 성공 케이스의 replan_count 평균 | 현행 대비 -0.5회 |
| **1회 replan 해결률** | replan 1회만에 SUCCESS하는 비율 | 60% 이상 |
| **give_up 비율** | 전체 Recovery 진입 건 중 최종 FAILURE 비율 | 현행 대비 -15%p |
| **도구별 기여도** | 각 도구가 포함된 execution_plan의 성공률 | 도구별 추적 |

### 평가 방법

```
1. 골든셋 기반 회귀 테스트
   - 기존 failure 케이스 30건 + 신규 edge case 20건
   - 도구 추가 전/후 A/B 비교

2. 트레이스 분석
   - logs/traces/에서 recovery 경로 추출
   - 도구 호출 패턴 → 성공/실패 상관관계 분석

3. LoopGuard 모니터링
   - 신규 카운터(db_query_count, explore_sql_count) 실사용량 추적
   - 상한 도달 빈도 → 상한 조정 필요성 판단
```

---

## 13. 구현 로드맵

### Phase 1: DB 직접 탐색 도구 (P0)

```
B1 get_column_distinct_values  ─┐
B2 get_column_stats            ─┼─ tools.py에 함수 추가 + TOOL_MAP 등록
B3 get_table_schema            ─┤  (기존 get_sample_rows 패턴 재사용)
B4 search_related_tables       ─┘
+ LoopGuard.db_query_count 추가
+ recovery_planner_system.txt 도구 테이블 갱신
+ PII 컬럼 차단 공통 로직 추출
```

### Phase 2: LLM 자율 탐색 + 그래프 (P1)

```
B5 explore_with_sql  ── 보안 다층 방어 구현 (safety_checker + LIMIT + PII 마스킹)
B6 explore_graph     ── Neo4j 커넥터에 탐색 의도 디스패처 추가
B7 search_report_sql ── MongoDB 보고서 인덱스 검색
B8 validate_join_path ── Neo4j + DB spot-check 조합
+ LoopGuard.explore_sql_count, graph_query_count 추가
+ Neo4j 스키마 확장 (C1~C5)
+ seed_neo4j.py 시딩 로직 확장
+ recovery_planner_system.txt failure_type별 권장 도구 섹션 추가
```

### Phase 3: 보조 도구 (P2)

```
B9  check_data_freshness    ─┐
B10 explain_sql              ─┼─ 개별 도구 추가 (Phase 1 패턴 재사용)
B11 search_similar_columns   ─┤
B12 get_table_row_count      ─┘
+ 약어 확장 사전 리소스 파일 생성
```
