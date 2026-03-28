# 멀티 DB 라우팅 설계안 — ADW(Sybase IQ) + BDP(Impala)

> **상태**: Working Draft v1.0
> **작성일**: 2026-03-25
> **목적**: 사용자의 자연어 질의로부터 올바른 DB 소스를 자동 판별하여 SQL 생성·검증·실행하는 설계

---

## 1. 핵심 아이디어

```
사용자: "지점별 대출 잔액 알려줘"
                ↓
        에이전트가 자동 판별
                ↓
  TB_ADW_LNB301M → 시스템코드 "ADW" → Sybase IQ로 실행
```

**DB 타입은 별도 저장하지 않는다.** 테이블명의 시스템코드 3자리로 파싱한다.

---

## 2. 테이블명 규칙과 시스템코드 파싱

### 명명 규칙

```
TB_{SYS}_{SubjectCode}{Serial}{Type}
│    │       │          │       └─ M(마스터) / L(로그) / P(스냅샷) / ...
│    │       │          └──────── 일련번호 (2~3자리)
│    │       └─────────────────── 업무코드 (3~4자리)
│    └─────────────────────────── 시스템코드 (3자리) ★ DB 라우팅 키
└──────────────────────────────── 접두사
```

### 시스템코드 → DB 매핑

```python
DB_SOURCE_MAP = {
    "ADW": "adw",       # Sybase IQ (정보계 DW)
    "BDP": "bigdata",   # Impala (빅데이터 플랫폼, 특수 시 Hive)
}

DB_DIALECT_MAP = {
    "adw":     "tsql",   # Sybase IQ → sqlglot tsql 근사 매핑
    "bigdata": "hive",   # Impala → sqlglot hive 근사 매핑
}

DB_CONNECTOR_MAP = {
    "adw":     "adw_db",      # SybaseIQConnector
    "bigdata": "bigdata_db",  # ImpalaConnector
}
```

### 파싱 함수

```python
def parse_db_source(table_name: str) -> str:
    """테이블명에서 시스템코드를 추출하여 DB 소스를 반환한다.

    TB_ADW_CSC101M → "adw"
    TB_BDP_LCT001L → "bigdata"
    """
    parts = table_name.split("_")
    if len(parts) >= 3:
        sys_code = parts[1].upper()
        return DB_SOURCE_MAP.get(sys_code, "adw")  # 기본값: ADW
    return "adw"
```

**별도 메타데이터 필드 불필요** — 테이블명 자체가 DB 소스 정보를 내포.

---

## 3. 에이전틱 흐름에서의 변화

### 3-1. 전체 흐름

```
[planner]
  → search_table_meta("대출 잔액")
  → 결과: TB_ADW_LNB301M (시스템코드 ADW)

[context_explorer]
  → CandidateTable 생성 시 db_source 자동 파싱
  → CandidateTable(table_name="TB_ADW_LNB301M", db_source="adw")

[sql_generator]                              ★ 최종 dialect 결정
  → candidate_tables의 db_source 확인
  → 모든 테이블이 같은 db_source인지 검증
  → db_source에 맞는 SQL dialect로 생성
    - adw → Sybase IQ 문법 (TOP N, DATEADD, ...)
    - bigdata → Impala/Hive 문법 (LIMIT, FROM_UNIXTIME, ...)

[sql_validator]
  → Layer 1: db_source에 맞는 dialect로 sqlglot 파싱
  → Layer 2a: 구조적 sanity check (dialect 무관)
  → Layer 3: db_source에 맞는 커넥터로 LIMIT 5 실행

[sql_executor]
  → db_source에 따라 올바른 커넥터 선택
  → mgr.get_query_db("adw") → SybaseIQConnector.execute_query()
```

### 3-2. sql_generator의 dialect 결정 (핵심)

```python
def _determine_dialect(state: AgenticCoreState) -> str:
    """candidate_tables의 db_source로 SQL dialect을 결정한다."""
    sources = {
        parse_db_source(ct.table_name)
        for ct in state.candidate_tables
        if ct.table_name
    }

    if len(sources) > 1:
        # 크로스 DB → 다수 DB에 걸친 테이블 사용 감지
        return "CROSS_DB"

    if not sources:
        return "tsql"  # 기본값: ADW (Sybase IQ)

    source = sources.pop()
    return DB_DIALECT_MAP.get(source, "tsql")
```

sql_generator는 dialect에 따라 프롬프트에 **SQL 문법 힌트를 주입**한다:

```
dialect == "tsql" (Sybase IQ):
  → "SELECT TOP 10 ... (LIMIT 대신 TOP 사용)"
  → "DATEADD(month, -1, GETDATE()) (날짜 연산)"
  → "CONVERT(VARCHAR, column, 112) (날짜 포맷팅)"

dialect == "hive" (Impala):
  → "SELECT ... LIMIT 10"
  → "FROM_UNIXTIME, DATE_SUB (날짜 연산)"
  → "CAST(column AS STRING) (타입 변환)"
```

### 3-3. 크로스 DB 감지 시 처리

```python
if dialect == "CROSS_DB":
    # 예: TB_ADW_LNB301M (ADW) + TB_BDP_LCT001L (BDP)
    # → 하나의 SQL로 조인 불가
    # → 사용자에게 안내 or 분리 실행

    updates["needs_user_input"] = True
    updates["user_question"] = (
        "요청하신 데이터가 서로 다른 시스템에 있습니다:\n"
        "  - {adw_tables}: 정보계 DW (ADW)\n"
        "  - {bdp_tables}: 빅데이터 (BDP)\n"
        "각각 따로 조회해 드릴까요?"
    )
```

---

## 4. sql_validator의 동작 상세

### 4-1. Layer 1 — Rule-based (dialect 인식)

```
입력: generated_sql + db_source (candidate_tables에서 파싱)
```

| 검증 항목 | 동작 | dialect별 차이 |
|---|---|---|
| sql_safety_checker | DML/DDL/시스템카탈로그 차단 | dialect 무관 (공통 규칙) |
| sqlglot 파싱 | **db_source의 dialect로 파싱** | `adw` → `tsql`, `bigdata` → `hive` |
| 테이블 존재 확인 | SQL 내 테이블이 candidate_tables에 있는지 | dialect 무관 |
| 컬럼 존재 확인 | SQL 내 컬럼이 relevant_columns에 있는지 | dialect 무관 |

```python
# Layer 1 변경점
def _validate_layer1(sql: str, state: AgenticCoreState):
    # 1. 공통 안전성 검증
    safety = validate_sql_safety(sql)

    # 2. dialect 결정 후 sqlglot 파싱
    dialect = _determine_dialect(state)
    if dialect == "CROSS_DB":
        return {"status": "FAIL", "feedback": "크로스 DB 조인 불가"}

    ast = parse_sql_safe(sql, dialect=dialect)  # ← dialect 전달
    if ast is None:
        return {"status": "FAIL", "feedback": f"SQL 파싱 실패 ({dialect} 문법)"}

    # 3. 테이블/컬럼 존재 확인 (기존과 동일)
    ...
```

### 4-2. Layer 2a — 구조적 sanity check (dialect 무관)

```
dialect에 관계없이 동일한 구조적 검증 수행:
  □ group_by 있는데 SQL에 GROUP BY 없음
  □ agg_function 있는데 SQL에 집계함수 없음
  → 이 검증은 SQL 의미 구조이므로 dialect에 영향받지 않음
```

### 4-3. Layer 2b — LLM 의미 검증 (dialect 힌트 포함)

```
LLM 프롬프트에 dialect 정보 추가:
  "이 SQL은 {Sybase IQ / Impala} 문법으로 작성되었습니다.
   해당 DB의 문법 규칙에 맞는지도 확인하세요."
```

### 4-4. Layer 3 — 실행 검증 (커넥터 라우팅)

```python
# Layer 3 변경점
async def _validate_layer3(sql: str, state: AgenticCoreState):
    db_source = _determine_db_source(state)

    # db_source에 맞는 커넥터로 LIMIT 5 실행
    mgr = get_connector_manager()
    db = mgr.get_query_db(db_source)

    # dialect별 LIMIT 문법 차이 처리
    if db_source == "adw":
        # Sybase IQ: SELECT TOP 5 * FROM (...)
        limited_sql = f"SELECT TOP 5 * FROM ({sql}) _t"
    else:
        # Impala/Hive: SELECT * FROM (...) LIMIT 5
        limited_sql = f"SELECT * FROM ({sql}) _t LIMIT 5"

    result = await db.execute_query(limited_sql)
    ...
```

---

## 5. ConnectorManager 변경

### 현재

```python
self.info_db = InfoDBConnector()  # PostgreSQL 단일
```

### 변경 후

```python
# 외부망 (개발)
self.info_db = InfoDBConnector()          # PostgreSQL (기본)

# 내부망 (폐쇄망) — config 기반 전환
self.adw_db = SybaseIQConnector()         # ADW
self.bigdata_db = ImpalaConnector()       # 빅데이터
# self.hive_db = HiveConnector()          # 특수 케이스 예비

def get_query_db(self, db_source: str) -> DatabaseConnector:
    """db_source에 따라 올바른 업무 DB 커넥터를 반환한다."""
    if settings.deployment_mode == "internal":
        if db_source == "bigdata":
            return self.bigdata_db
        return self.adw_db
    return self.info_db  # 외부망: PostgreSQL
```

### 설정

```python
# config.py
deployment_mode: str = "external"     # "external" | "internal"
default_db_source: str = "adw"        # 시스템코드 파싱 실패 시 기본값
```

---

## 6. CandidateTable 변경

```python
class CandidateTable(BaseModel):
    table_name: str
    db_source: str = ""          # ← 신규: "adw" | "bigdata" (자동 파싱)
    role: str = ""
    relevant_columns: list[str] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)
    missing_coverage: list[str] = Field(default_factory=list)
```

`db_source`는 `context_explorer._interpret_result`에서 `parse_db_source(table_name)`으로 자동 설정.
**별도 메타데이터 저장 불필요.**

---

## 7. 변경 영향 범위

| 파일 | 변경 내용 | 난이도 |
|---|---|---|
| `agentic_state.py` | `CandidateTable.db_source` 필드 추가 | 낮음 |
| `agentic_state.py` (또는 별도 util) | `parse_db_source()` 함수 추가 | 낮음 |
| `context_explorer.py` | `_interpret_result`에서 db_source 자동 파싱 | 낮음 |
| `sql_generator.py` | dialect 결정 + 프롬프트 힌트 주입 + 크로스 DB 감지 | 중간 |
| `sql_validator.py` | Layer1 dialect 전달, Layer3 커넥터 라우팅 | 중간 |
| `sql_executor.py` | db_source → 커넥터 선택 | 낮음 |
| `manager.py` | `adw_db`, `bigdata_db` 등록 + `get_query_db()` | 낮음 |
| `config.py` | `deployment_mode`, `default_db_source` | 낮음 |
| `sql_hint_extractor.py` | `DIALECT_MAP`에 이미 sybase_iq/impala 존재 (변경 불필요) | 없음 |

---

## 8. 엣지 케이스

| 시나리오 | 처리 |
|---|---|
| 크로스 DB 조인 (ADW + BDP) | sql_generator에서 감지 → 사용자에게 분리 조회 안내 |
| 시스템코드 미식별 테이블 | `default_db_source` (ADW) 사용 |
| 외부망 개발 환경 | `deployment_mode=external` → 모든 쿼리가 PostgreSQL로 실행 |
| BDP 테이블인데 Hive 필요 | ImpalaConnector가 기본, Hive는 설정 플래그로 전환 |
| 동일 DB 소스 내 다중 테이블 조인 | 정상 처리 (같은 DB이므로 조인 가능) |
