# 테이블 3측면 설명 보강 전략 — 설계 가이드

> **작성일**: 2026-03-26 (v2 — 비판적 검토 반영)
> **관련 리뷰**: `docs/reviews/design/20260325-reasoning-pipeline-review.md` §2.2(A)
> **관련 코드**: `src/agents/nodes/reason/context_explorer.py`, `src/services/similar_table_resolver.py`
> **목적**: 이 문서를 보고 올바른 방향으로 3측면 보강을 구현할 수 있도록 설계 근거와 전략을 기술한다.

---

## 1. 문제 정의

### 1.1 3측면이란

은행 정보계 DB에는 같은 도메인의 유사 테이블이 다수 존재한다.
테이블을 SQL에 사용하기 전에 아래 3가지 측면으로 검증해야 올바른 테이블을 선택할 수 있다.

| # | 측면 | 설명 | 예시 |
|---|------|------|------|
| 1 | **엔티티 집합 정의** | 테이블 내 어떤 데이터가 있는지 | "전체 여신 계좌" vs "정상 여신만" |
| 2 | **기능적 정의** | 데이터가 어디에 어떻게 쓰이는지 | "잔액 조회용" vs "실적 집계용" |
| 3 | **데이터 발생규칙** | 데이터가 언제 생성되어 적재되는지 | "일별 배치(D+1)" vs "월말 배치" vs "실시간" |

### 1.2 현재 상태

- `CandidateTable` 모델에 3측면 필드가 없다 (`state.py:157-165`)
- `context_explorer._extract_tables()`에서 PK 정보(`column_pk`)와 한글 컬럼명(`column_alt_name`)을 활용하지 않는다
- 폐쇄망 MongoDB 메타에 `update_cycle` 필드는 **존재하지 않는다** — 적재주기 정보는 메타에서 직접 얻을 수 없음
- `similar_table_resolver.py`에 유사 테이블 구분 로직이 구현되어 있으나 **파이프라인에 연결되지 않았다**
  - `src/agents/` 어디에서도 import 하지 않음
  - 하드코딩된 5개 그룹만 처리 가능하여 확장 불가
- `sql_generator`는 모든 candidate_tables를 LLM에 나열만 하고, 비교 기준을 제공하지 않는다

### 1.3 폐쇄망 MongoDB 메타 실제 필드

파이프라인 출력 형식 (`resources/connectors/mongo/pipeline_table_meta.json`):

```
테이블: name, alt_name, description, schema_name
컬럼:   columns[].name, .alt_name, .type, .description, .is_pk
```

> `update_cycle` 필드는 폐쇄망에 존재하지 않는다.
> 적재주기는 날짜 분포 패턴에서 간접 추론하거나, 비교 LLM이 직접 판단한다.

### 1.4 왜 문제인가

"이번 달 여신 잔액" 질의에서:

| 테이블 | 적재주기 | 데이터 범위 | 적합 여부 |
|--------|---------|------------|----------|
| `TB_LN_BAL_D` | 일별 배치(D+1) | 어제까지 | **적합** — 이번 달 데이터 있음 |
| `TB_LN_BAL_M` | 월말 배치 | 전월까지 | **부적합** — 이번 달 데이터 없음 |
| `TB_LN_BAL_RT` | 실시간 | 현재 시점 | 적합 — 단, 정보계가 아닐 수 있음 |

테이블 이름과 설명만으로는 구분 불가. 3측면 정보가 있어야 올바른 테이블을 선택할 수 있다.

---

## 2. 별도 보강 에이전트 제안에 대한 비판적 검토

### 2.1 검토한 제안

> CandidateTable에 샘플데이터 + 코드정보가 확보되면
> LLM으로 3측면 설명을 보강하는 별도 에이전트/노드를 추가한다.

### 2.2 문제점

#### (A) 트리거 조건의 비결정성

context_explorer는 planner가 세운 `execution_plan`을 순차 실행한다.
각 테이블의 정보(메타, 샘플, 코드)가 **언제 모이는지 예측 불가**하다.

```
step 1: search_table_meta "여신" → TB_LN_BAL_D, TB_LN_BAL_M 발견
step 2: search_code_meta "LOAN_DCD" → 코드값 확인
step 3: get_sample_data "TB_LN_BAL_D" → 샘플 도착
step 4: get_sample_data "TB_LN_BAL_M" → 계획에 없을 수도 있음
```

- planner가 모든 후보 테이블에 `get_sample_data`를 계획하리라는 보장이 없다
- "샘플 + 코드정보가 모두 주어진 경우"라는 트리거 조건 자체가 비결정적이다
- 일부 테이블만 보강되고 나머지는 안 되는 비대칭 상태가 발생한다

#### (B) LLM 호출 비용 과다

context_explorer의 `_interpret_with_llm()`이 **매 도구 실행마다** 이미 LLM을 호출하고 있다.
여기에 보강 에이전트가 추가되면:

```
도구 실행 → _interpret_with_llm()  (기존)
        → 3측면 보강 LLM 호출      (추가)
```

테이블 5개 기준: 해석 5회 + 보강 5회 = LLM **10회 호출**.
폐쇄망 소형 LLM(7B~70B) 환경에서 탐색 단계만으로 이 비용은 과하다.

#### (C) 진짜 문제는 "보강"이 아니라 "비교"

각 테이블을 개별적으로 3측면 보강하면:
- TB_LN_BAL_D: "일별 스냅샷, D+1 배치, 전일까지"
- TB_LN_BAL_M: "월별 스냅샷, 월말 배치, 전월까지"

이건 유용하지만, **핵심 판단은 "이번 달"이라는 질의 맥락과 대조해서 어느 테이블이 적합한가**이다.
이것은 개별 테이블 보강으로는 해결되지 않고, **질의 대비 테이블 간 비교 추론**이 필요하다.

| | 개별 보강 | 질의 대비 비교 |
|---|---|---|
| 입력 | 테이블 1개의 메타+샘플+코드 | 후보 테이블 N개 + 질의 맥락 |
| 출력 | 풍부한 테이블 설명 | "이 질의에는 A가 적합, B는 부적합" |
| LLM 호출 | N회 (테이블당 1회) | **1회** (한번에 비교) |
| 유사 테이블 구분 | 간접적 (설명만 풍부해짐) | **직접적** (판정까지) |

#### (D) 그래프 복잡도 증가

별도 노드를 추가하면 라우팅 로직이 복잡해진다:

```
현재: explorer → evaluator → (EXPLORE/GENERATE/REPLAN)
제안: explorer → enricher → evaluator → ...
```

- enricher가 매 루프마다 실행되면 비용 문제
- 조건부 실행이면 라우팅 분기 추가 필요
- 기존 `_interpret_with_llm()` 프롬프트 확장이 더 자연스러운 대안이 됨

---

## 3. 설계 원칙

비판적 검토를 통해 도출된 설계 원칙을 먼저 정리한다.

### 3.1 rule-based vs LLM 배분 원칙

**rule-based는 기계적 판단에만, 의미적 판단은 LLM에 위임한다.**

| 판단 유형 | 처리 주체 | 근거 |
|---|---|---|
| PK에서 날짜 접미사 매칭 | rule-based | 기계적 패턴 매칭, 정확도 높음 |
| 날짜 분포 패턴 검출 (매일/매월말) | rule-based | 기계적 계산, LLM 불필요 |
| "이 테이블들이 유사한가?" (비교 트리거) | **LLM 출력 활용** | 의미적 유사성 판단, 접두사 매칭은 부정확 |
| "이 질의에 어떤 테이블이 적합한가?" (비교 판정) | **LLM** | 본질적으로 추론 작업 |

> **경고**: 의미적 판단을 rule-based로 처리하면 정확도에 더 큰 위험이 된다.
> 과도한 LLM 호출은 지양하되, **이미 호출되는 LLM의 출력을 최대한 재활용**한다.

### 3.2 오역 방지 원칙

**"정보 차단"이 아니라 "생성 품질 통제 + 출처 명시"로 오역을 방지한다.**

- **생성 단계**: `_interpret_with_llm()` 프롬프트에서 "관찰된 사실만, 불확실하면 빈 문자열" 강하게 지시 → 오역 자체를 억제
- **소비 단계**: 모든 정보를 출처 태그와 함께 전달 → `(메타 원본)`, `(관찰)`, `(LLM 추론)` 표시
- 비교 LLM이 출처를 보고 신뢰도를 스스로 조절하여 판단

> inferred 필드를 차단하면 비교 LLM의 판단 재료가 줄어들어 오히려 비교 품질이 떨어진다.
> 원본 데이터와 함께 전달하면 비교 LLM이 자체적으로 교차 검증할 수 있다.

---

## 4. 전체 실행 흐름

```
context_explorer_node 내부:

  ┌─ 탐색 루프 (기존 그대로) ──────────────────────────────────┐
  │ for step in execution_plan:                                │
  │   result = await execute_tool(step.tool, step.input)       │
  │   insight, knowledge, new_tables = await _interpret_result( │
  │       step, result, original_query                         │
  │   )                                                        │
  │   ※ _interpret_with_llm()에서 3측면 필드도 함께 추출       │
  │   ※ _extract_tables()에서 PK 기반 기준 컬럼도 식별        │
  │                                                            │
  │   조기 탈출 판정 (evaluate_readiness)                       │
  └────────────────────────────────────────────────────────────┘
           ↓ 루프 종료 후
  ┌─ 날짜 분포 일괄 조회 (신규) ───────────────────────────────┐
  │ key_date_columns가 있는 CandidateTable에 대해              │
  │ get_date_distribution 경량 쿼리 실행                       │
  │ → observed_date_columns에 컬럼별 요약 저장                 │
  └────────────────────────────────────────────────────────────┘
           ↓
  ┌─ 유사 테이블 비교 판정 (신규) ─────────────────────────────┐
  │ inferred_entity_scope 기반으로 비교 필요 여부 판정         │
  │ → 필요하면 LLM 1회 호출로 테이블 비교                     │
  │ → rejected 테이블을 candidate_tables에서 제거              │
  └────────────────────────────────────────────────────────────┘
           ↓
  return {"reason": reason}
```

> **설계 결정 근거**: 날짜 분포 조회와 비교 판정을 탐색 루프 종료 후에 배치한 이유:
> - 기존 탐색 루프 로직을 전혀 건드리지 않음
> - 루프 완료 후 모든 candidate_tables가 확정된 상태에서 일괄 처리
> - 조기 탈출로 루프가 일찍 끝나도 분포 조회는 실행됨 (비교 판정에 필요하므로)
> - `get_date_distribution`은 경량 쿼리(`SELECT DISTINCT ... LIMIT 30`)라 DB 부하 미미
> - 수집된 분포 데이터는 비교 판정뿐 아니라 sql_generator에서도 활용 가능

---

## 5. 상세 설계

### 5.1 CandidateTable 모델 확장

**위치**: `src/agents/state/state.py`

```python
class CandidateTable(BaseModel):
    """탐색 중 발견된 후보 테이블."""

    table_name: str
    db_source: str = ""
    role: str = ""                                    # 원본 메타 설명 (보존)
    relevant_columns: list[str] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)
    missing_coverage: list[str] = Field(default_factory=list)

    # 기준 컬럼 (rule-based 식별) ─────────────────
    key_date_columns: list[KeyDateColumn] = Field(default_factory=list)

    # 관찰 사실 (rule-based, 검증 가능) ─────────────
    observed_date_columns: list[ObservedDateColumn] = Field(default_factory=list)

    # LLM 추론 (출처 태그 부착하여 비교 프롬프트에도 전달) ──
    inferred_entity_scope: str = ""                   # 추론: "정상 여신만"
    inferred_functional_usage: str = ""               # 추론: "잔액 조회용"
    inferred_key_date_column: str = ""                # PK 기반 식별 실패 시 LLM 추론
    inference_confidence: float = 0.0                 # 추론 자체의 확신도
```

보조 모델:

```python
class KeyDateColumn(BaseModel):
    """기준 날짜 컬럼."""

    column_name: str              # "BASE_YMD", "TRN_YMD"
    suffix: str                   # "YMD", "YM", "YY"
    source: Literal["pk_rule", "alt_name_rule", "llm_fallback"] = "pk_rule"


class ObservedDateColumn(BaseModel):
    """날짜 컬럼의 분포 관찰 결과 (get_date_distribution 조회 후)."""

    column_name: str              # "BASE_YMD", "TRN_YMD"
    date_range: str = ""          # "2024-01-31 ~ 2024-12-31"
    date_pattern: str = ""        # "매월 말일만 존재 (12건/12개월)"
```

### 5.2 탐색 루프 내: `_interpret_with_llm()` 프롬프트 확장 (추가 비용 0)

**위치**: `src/agents/nodes/reason/context_explorer.py` → `_interpret_with_llm()`
**변경 대상**: `resources/prompts/reason/context_explorer_system.txt`

기존에 이미 호출되는 LLM에 **출력 스키마 필드만 추가**하여 3측면 정보를 수집한다.

#### 프롬프트 출력 스키마 확장

현재:
```json
{
  "new_tables": [{
    "table_name": "...",
    "role": "...",
    "relevant_columns": ["..."],
    "join_keys": ["..."]
  }]
}
```

확장:
```json
{
  "new_tables": [{
    "table_name": "...",
    "role": "...",
    "relevant_columns": ["..."],
    "join_keys": ["..."],
    "entity_scope": "어떤 데이터가 포함되어 있는지 (메타/샘플에서 관찰된 사실만)",
    "functional_usage": "어디에 쓰이는 테이블인지 (메타 설명 기반, 확실하지 않으면 빈 문자열)",
    "data_refresh_hint": "적재 주기 힌트 (날짜 패턴에서 관찰된 것만, 확실하지 않으면 빈 문자열)"
  }]
}
```

#### 프롬프트 지시사항 추가

```
## 테이블 정보 추출 시 주의사항

- entity_scope, functional_usage, data_refresh_hint는 **관찰된 사실만** 기록하세요.
- 메타 설명이나 샘플 데이터에서 직접 확인할 수 없는 내용은 빈 문자열("")로 남기세요.
- 추측하지 마세요. "~일 것이다", "~로 보인다"는 기록하지 마세요.
```

#### 이 필드들의 활용처 (2곳)

| 활용 지점 | 용도 | 상세 |
|---|---|---|
| **비교 트리거 판정** (§5.5) | 같은 도메인 테이블 감지 | `inferred_entity_scope`에서 도메인 키워드 공유 여부 확인 |
| **비교 프롬프트** (§5.6) | 판단 보조 재료 | 출처 태그 `(LLM 추론)` 부착하여 전달 |

### 5.3 탐색 루프 내: `_extract_tables()` 수정 — PK 기반 기준 컬럼 식별

**위치**: `src/agents/nodes/reason/context_explorer.py` → `_extract_tables()`

MongoDB 메타의 PK 정보(`is_pk`)와 한글 컬럼명(`alt_name`)으로 기준 컬럼을 식별한다.

```python
def _extract_tables(step, result):
    # ... 기존 로직 ...

    raw_cols = meta.get("columns", [])

    # PK 컬럼 목록 추출
    pk_columns = [c.get("name", "") for c in raw_cols if c.get("is_pk")]

    # 1단계: PK 내 행내표준 접미사 매칭 (rule-based)
    key_date_cols = _identify_key_date_columns(pk_columns)

    # 2단계: PK에서 못 찾으면 한글 컬럼명(alt_name)에서 "기준" 키워드 보조 탐지
    if not key_date_cols:
        key_date_cols = _identify_key_date_by_alt_name(raw_cols)

    # 3단계: 그래도 없으면 → LLM fallback은 _interpret_with_llm()에서 처리

    tables.append(CandidateTable(
        table_name=table_name,
        db_source=parse_db_source(table_name),
        role=desc,
        relevant_columns=columns,
        key_date_columns=key_date_cols,
    ))
```

#### 기준 컬럼 식별 함수 — 1단계: PK + 접미사

```python
# 행내표준 날짜 접미사 (우선순위 순)
DATE_SUFFIXES = ["YMD", "YM", "YY", "DT"]

def _identify_key_date_columns(pk_columns: list[str]) -> list[KeyDateColumn]:
    """PK 컬럼에서 행내표준 날짜 접미사로 기준 컬럼을 식별한다."""
    result: list[KeyDateColumn] = []
    for col in pk_columns:
        col_upper = col.upper()
        for suffix in DATE_SUFFIXES:
            if col_upper.endswith(f"_{suffix}") or col_upper == suffix:
                result.append(KeyDateColumn(
                    column_name=col,
                    suffix=suffix,
                    source="pk_rule",
                ))
                break
    return result
```

#### 기준 컬럼 식별 함수 — 2단계: 한글 컬럼명 보조

```python
KOREAN_DATE_KEYWORDS = ["기준일", "기준년월", "기준년", "거래일", "실행일"]

def _identify_key_date_by_alt_name(columns: list[dict]) -> list[KeyDateColumn]:
    """한글 컬럼명(alt_name)에서 기준 컬럼을 보조 식별한다."""
    result: list[KeyDateColumn] = []
    for col in columns:
        alt = col.get("alt_name", "")
        name = col.get("name", "")
        for kw in KOREAN_DATE_KEYWORDS:
            if kw in alt:
                suffix = _infer_suffix_from_name(name)  # 컬럼명에서 YMD/YM 추론
                result.append(KeyDateColumn(
                    column_name=name,
                    suffix=suffix,
                    source="alt_name_rule",
                ))
                break
    return result
```

#### PK 날짜 컬럼 수에 따른 처리

| PK 날짜 컬럼 수 | 처리 방식 |
| --- | --- |
| **1개** | rule-based 확정 → 기준 컬럼으로 분포 조회 |
| **2개 이상** | 모두 기준 컬럼 후보로 기록 → **각각** 분포 조회 → 비교 LLM에 둘 다 전달 |
| **0개** | `alt_name` 보조 탐지 → 그래도 없으면 LLM fallback (`inferred_key_date_column`) |

### 5.4 탐색 루프 종료 후: 날짜 분포 일괄 조회 (rule-based)

**위치**: `src/agents/nodes/reason/context_explorer.py` → `context_explorer_node()` 루프 종료 후, `return` 전

탐색 루프가 완료된 후, `key_date_columns`가 있는 모든 CandidateTable에 대해 일괄 조회한다.

#### 경량 도구 — `get_date_distribution`

**위치**: `src/agents/nodes/reason/tools.py` — TOOL_MAP에 등록하지 않음 (planner 계획 대상이 아님, 직접 호출)

```python
async def get_date_distribution(
    table_name: str,
    date_column: str,
    limit: int = 30,
) -> list[str]:
    """테이블의 날짜 컬럼 DISTINCT 값을 조회한다 (경량).

    SQL 인젝션 방지: 식별자 화이트리스트 검증 후 실행.
    """
    # 식별자 검증 (허용 패턴: 영문 대소문자, 숫자, 언더스코어)
    import re
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", table_name):
        return []
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", date_column):
        return []

    sql = f"SELECT DISTINCT {date_column} FROM {table_name} ORDER BY {date_column} LIMIT {limit}"
    mgr = get_connector_manager()
    try:
        result = await mgr.info_db.execute_query(sql)
        if hasattr(result, "rows"):
            return [str(row.get(date_column, "")) for row in result.rows]
        return []
    except Exception as e:
        logger.warning("get_date_distribution 실패", table=table_name, column=date_column, error=str(e))
        return []
```

#### 일괄 조회 함수

```python
async def _observe_all_date_distributions(
    candidate_tables: list[CandidateTable],
) -> None:
    """모든 CandidateTable의 기준 컬럼별 날짜 분포를 조회한다.

    context_explorer_node의 탐색 루프 종료 후, return 전에 호출.
    """
    for table in candidate_tables:
        if not table.key_date_columns:
            continue
        for kdc in table.key_date_columns:
            distinct_dates = await get_date_distribution(
                table.table_name, kdc.column_name,
            )
            if not distinct_dates:
                continue
            dates = sorted(distinct_dates)
            table.observed_date_columns.append(ObservedDateColumn(
                column_name=kdc.column_name,
                date_range=f"{dates[0]} ~ {dates[-1]}",
                date_pattern=_detect_date_pattern(dates),
            ))
```

#### `_detect_date_pattern` — rule-based 패턴 분석

```python
def _detect_date_pattern(dates: list[str]) -> str:
    """날짜 DISTINCT 값 목록에서 입도 패턴을 탐지한다.

    반환 예시: "매일 연속 (90건/90일)", "매월 말일 (12건/12개월)"
    """
    if not dates or len(dates) < 2:
        return f"{len(dates)}건"

    # YYYYMMDD 형태 (8자리)인지 YYYYMM 형태 (6자리)인지 판별
    sample = dates[0].replace("-", "").replace("/", "")

    if len(sample) == 6:  # YYYYMM
        return f"매월 ({len(dates)}건)"

    if len(sample) == 8:  # YYYYMMDD
        # 말일만 있는지 확인
        all_month_end = all(_is_month_end(d) for d in dates)
        if all_month_end:
            return f"매월 말일 ({len(dates)}건)"

        # 연속 일자인지 확인
        # (간격 계산 후 평균 간격으로 판단)
        return f"매일 ({len(dates)}건)"

    return f"{len(dates)}건"
```

### 5.5 탐색 루프 종료 후: 유사 테이블 비교 트리거 판정

**위치**: `src/agents/nodes/reason/context_explorer.py` → 날짜 분포 조회 후, `return` 전

#### 왜 접두사 매칭이 아닌 `inferred_entity_scope`를 사용하는가

접두사 매칭(`"_".join(name.split("_")[:3])`)의 한계:

| 케이스 | 접두사 매칭 | inferred_entity_scope |
|---|---|---|
| `TB_LN_BAL_D` vs `TB_LN_BAL_M` | **감지** (같은 접두사) | **감지** (둘 다 "여신 잔액") |
| `TB_LOAN_INFO` vs `TB_LOAN_OVERDUE_STAT` | **미감지** (접두사 다름) | **감지** (둘 다 "여신" 도메인) |
| `TB_LN_BAL_D` vs `TB_LN_EXEC_D` | 오감지 (같은 접두사, 다른 데이터) | **미감지** ("잔액" vs "실행") |

`inferred_entity_scope`는 `_interpret_with_llm()`에서 **추가 비용 0**으로 이미 수집되는 필드이다.
이 필드의 도메인 키워드가 겹치는 테이블이 2개 이상이면 비교 트리거.

> 이 용도에서 inferred 오역의 위험은 **없다** — 비교할지 말지만 결정하며, SQL에 직접 영향을 주지 않는다.
> 오판(비교 불필요한데 비교함)의 비용은 LLM 1회 호출뿐이고, 미감지(비교 필요한데 안 함)의 비용은 잘못된 테이블 선택이므로 **과감지가 미감지보다 낫다.**

#### 트리거 판정 함수

```python
def _needs_table_comparison(candidate_tables: list[CandidateTable]) -> list[list[CandidateTable]]:
    """비교가 필요한 테이블 그룹을 반환한다.

    판정 기준:
      1차: inferred_entity_scope에서 도메인 키워드 공유 (LLM 추론 활용)
      2차 (fallback): 테이블명 접두사 매칭 (inferred_entity_scope가 비어있을 때)

    Returns:
        비교 그룹 리스트. 예: [[TB_LN_BAL_D, TB_LN_BAL_M], [TB_DP_BAL_D, TB_DP_BAL_M]]
        비교 불필요하면 빈 리스트.
    """
    # 1차: entity_scope 기반 그룹핑
    domain_keywords = _extract_domain_keywords(candidate_tables)
    groups = _group_by_shared_keywords(candidate_tables, domain_keywords)

    # 2차 fallback: entity_scope가 비어있는 테이블은 접두사 매칭
    ungrouped = [t for t in candidate_tables if not any(t in g for g in groups)]
    if ungrouped:
        prefix_groups = _group_by_prefix(ungrouped)
        groups.extend(prefix_groups)

    # 2개 이상인 그룹만 반환
    return [g for g in groups if len(g) >= 2]
```

### 5.6 유사 테이블 비교 판정 — LLM 1회 호출

**위치**: `src/agents/nodes/reason/context_explorer.py` → 트리거 판정 후

비교 그룹별로 **1회** LLM 호출로 질의 맥락 대비 테이블 적합성을 판정한다.

#### 비교 프롬프트 설계

```
## 테이블 비교 판정

사용자 질의: "{original_query}"
질의의 시간 조건: "{time_slot}"

아래 후보 테이블들 중 이 질의에 가장 적합한 테이블을 판정하세요.
각 정보의 출처를 확인하고, (LLM 추론) 태그가 붙은 정보는 참고만 하세요.

### TB_LN_BAL_D
- 메타 설명: "여신잔액일별" (메타 원본)
- 기준 컬럼: BAL_DT (PK, alt_name: "기준일자") (메타 원본)
- 날짜 분포: 2024-03-01 ~ 2024-03-24, 매일 연속 (관찰)
- 주요 컬럼: BAL_DT(기준일자), LOAN_NO(대출번호), BAL_AMT(잔액) (메타 원본)
- 엔티티 범위: "전체 여신 계좌의 일별 잔액" (LLM 추론)
- 기능적 용도: "잔액 조회용" (LLM 추론)

### TB_LN_BAL_M
- 메타 설명: "여신잔액월별" (메타 원본)
- 기준 컬럼: BASE_YM (PK, alt_name: "기준년월") (메타 원본)
- 날짜 분포: 202401 ~ 202412, 매월 (관찰)
- 주요 컬럼: BASE_YM(기준년월), LOAN_NO(대출번호), BAL_AMT(잔액) (메타 원본)
- 엔티티 범위: "전체 여신 계좌의 월별 잔액" (LLM 추론)
- 기능적 용도: "월별 통계 집계용" (LLM 추론)

## 판정 기준
- 질의의 시간 조건과 테이블의 날짜 분포/패턴이 부합하는지 판단
- (관찰) 데이터를 우선 신뢰하고, (LLM 추론) 데이터는 보조로 참고
- 적합/부적합 판정에 반드시 이유를 명시

## 출력 형식 (JSON만 출력, 다른 텍스트 금지)
{
  "selected": ["TB_LN_BAL_D"],
  "rejected": ["TB_LN_BAL_M"],
  "reason": "판정 이유"
}
```

#### 비교 결과 반영

```python
async def _apply_comparison_result(
    candidate_tables: list[CandidateTable],
    comparison_result: dict,
) -> None:
    """비교 판정 결과를 candidate_tables에 반영한다.

    rejected 테이블을 candidate_tables에서 제거한다.
    KnowledgeItem은 건드리지 않는다 — confidence_scorer의
    readiness 점수에 의도치 않은 영향을 주지 않기 위함.
    """
    rejected = set(comparison_result.get("rejected", []))
    # 리스트에서 rejected 테이블 제거
    candidate_tables[:] = [
        t for t in candidate_tables
        if t.table_name not in rejected
    ]
```

> **설계 결정**: rejected 테이블의 KnowledgeItem confidence를 낮추는 방식은 사용하지 않는다.
> `confidence_scorer.calculate_readiness()`가 `ki.confidence >= 0.8` 비율로 `term_score`를 계산하므로,
> KnowledgeItem을 강등하면 전체 readiness 점수가 떨어져 의도치 않게 REPLAN이 트리거될 수 있다.
> rejected 테이블은 `candidate_tables`에서 **제거**하는 것이 가장 깔끔하다.

---

## 6. 기존 similar_table_resolver.py와의 관계

### 6.1 현재 상태

- `similar_table_resolver.py`는 파이프라인에 연결되지 않음
- 하드코딩된 5개 그룹 + 키워드 매칭 기반 → 확장 불가
- LLM을 사용하지 않음

### 6.2 향후 역할

3측면 비교 판정이 구현되면, `similar_table_resolver`의 역할은 다음으로 축소된다:

| 역할 | 비교 판정 LLM | similar_table_resolver |
|------|-------------|----------------------|
| 유사 테이블 감지 | `inferred_entity_scope` 기반 | 하드코딩 그룹 데이터 |
| 테이블 비교 판단 | **주 담당** (원본 + 추론 데이터 기반) | 폴백/힌트 제공 |
| 프롬프트 가이드 생성 | 불필요 | `build_table_disambiguation_prompt()` 보조 활용 가능 |

→ 사전 정의된 그룹 데이터는 **비교 프롬프트의 추가 힌트**로 제공할 수 있다.
→ 단, 핵심 판단은 LLM이 수행하며, 하드코딩 그룹에 의존하지 않는다.

---

## 7. 구현 순서

| 순서 | 작업 | 난이도 | 효과 |
|------|------|--------|------|
| 1 | `CandidateTable` + 보조 모델(`KeyDateColumn`, `ObservedDateColumn`) 정의 | 낮음 | 구조적 기반 |
| 2 | `pipeline_table_meta.json` 필드 정합성 확인 (§1.3 참조) | 낮음 | 전제 조건 |
| 3 | `_extract_tables()`에서 PK + alt_name 기반 기준 컬럼 식별 (§5.3) | 낮음 | 즉시 활용 가능 |
| 4 | `get_date_distribution` + `_detect_date_pattern` 구현 (§5.4) | 중간 | 관찰 사실 확보 |
| 5 | `context_explorer_node` 루프 종료 후 일괄 분포 조회 연동 (§4 흐름) | 중간 | 실행 흐름 완성 |
| 6 | `context_explorer_system.txt` 프롬프트에 3측면 출력 필드 추가 (§5.2) | 낮음 | 추가 비용 0 |
| 7 | 비교 트리거 판정 함수 구현 — `inferred_entity_scope` 활용 (§5.5) | 중간 | **핵심 트리거** |
| 8 | 비교 판정 LLM 프롬프트 + 결과 반영 구현 (§5.6) | 중간 | **핵심 효과** |
| 9 | 골든셋에 유사 테이블 구분 테스트 케이스 추가 | 중간 | 품질 검증 |

---

## 8. 주의사항 체크리스트

구현 시 반드시 확인할 항목:

**파이프라인 메타:**
- [x] `pipeline_table_meta.json`: 필드명이 §1.3과 일치하는지 확인 (2026-03-26)
- [x] `update_cycle`은 폐쇄망에 존재하지 않으므로 코드/프롬프트에서 참조하지 않음 (2026-03-26)

**기준 컬럼 식별:**
- [x] PK 기반 기준 컬럼 식별: 행내표준 접미사(YMD/YM/YY/DT) 매칭으로 우선 처리 (2026-03-26)
- [x] PK 식별 실패 시 `alt_name`에서 "기준" 키워드 보조 탐지 (LLM fallback 전) (2026-03-26)
- [x] PK 날짜 컬럼이 복수이면 모두 후보로 기록하고 각각 분포 조회 (하나만 고르지 않음) (2026-03-26)
- [x] PK + alt_name 모두 실패 시 LLM fallback — `source: "llm_fallback"` 표시 필수 (2026-03-26)

**날짜 분포 조회:**
- [x] `get_date_distribution`은 `SELECT DISTINCT ... LIMIT 30` 경량 쿼리로 구현 (2026-03-26)
- [x] SQL 인젝션 방지: 테이블명/컬럼명에 `^[A-Za-z_][A-Za-z0-9_]*$` 패턴 검증 (2026-03-26)
- [x] 분포 조회는 탐색 루프 **종료 후** 일괄 실행 (루프 내에서 하지 않음) (2026-03-26)

**비교 트리거:**
- [x] `inferred_entity_scope` 기반으로 도메인 유사성 판정 (접두사 매칭은 fallback으로만) (2026-03-26)
- [x] 비교 판정은 유사 테이블 그룹이 존재할 때만 트리거 (불필요한 LLM 호출 방지) (2026-03-26)

**비교 프롬프트:**
- [x] 모든 정보에 출처 태그 부착: `(메타 원본)`, `(관찰)`, `(LLM 추론)` (2026-03-26)
- [x] `CandidateTable.role` (원본 메타 설명)은 절대 덮어쓰지 않는다 (2026-03-26)
- [x] LLM fallback 기준 컬럼은 `(LLM 추론)` 태그 부착 (2026-03-26)

**비교 결과 반영:**
- [x] rejected 테이블은 `candidate_tables`에서 **제거** (KnowledgeItem은 건드리지 않음) (2026-03-26)
- [x] 비교 판정 LLM 실패 시 → 기존 동작 유지 (모든 후보를 sql_generator에 전달) (2026-03-26)
- [x] 폐쇄망 소형 LLM에서 비교 프롬프트의 JSON 출력 파싱 실패 대비 fallback (2026-03-26)

**기타:**
- [x] `similar_table_resolver`의 하드코딩 그룹 데이터는 비교 프롬프트의 보조 힌트로만 사용 (2026-03-26)
- [x] 비교 프롬프트에 한글 컬럼명(`alt_name`)을 영문 컬럼명과 함께 전달 (2026-03-26)

**추가 개선 (이번 세션에서 수행):**
- [x] `data_refresh_hint`를 entity_scope 인라인 병합에서 독립 필드(`inferred_data_refresh_hint`)로 분리
- [x] `column_alt_names` 필드를 CandidateTable에 추가하여 한글 컬럼명 보존
- [x] SQL Generator 프롬프트에 3측면 정보 + 관찰된 날짜 분포 전달 (`_format_table_for_sql_prompt`)
- [x] `_extract_tables()` Cognitive Complexity 개선 — 헬퍼 함수 분리
- [x] 유닛 테스트 32건 작성 (전체 통과)
