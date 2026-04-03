# 컨텍스트 수집 적합성 평가 전략

> **작성일**: 2026-03-21
>
> **근거 문서**: `docs/reviews/design/20260321-graph-flow-evaluation.md` 3.1절 "컨텍스트 수집의 적합성 판단 부재"
>
> **목적**: `collect_context → generate_sql` 사이에 수집된 컨텍스트의 품질을 평가하고,
> 부족한 경우 재검색 또는 사용자 확인으로 분기하는 `context_evaluator` 전략을 구체화한다.

---

## 1. 핵심 문제 정의

현재 흐름:

```
collect_context → (결과 그대로) → generate_sql
```

ES에서 테이블 메타 10건, 보고서 SQL 3건 등을 가져오면 **"이게 정말 사용자 요구사항을 해결할 수 있는 테이블인가?"를 판단하지 않고** 그대로 LLM에 넘긴다.
이는 데이터 분석가가 테이블을 찾아놓고 **검증 없이 바로 SQL을 작성하는 것**과 같다.

문제가 발생하는 구체적인 경로를 3가지로 분류한다.

### 1.1 문제 경로 1 — 핵심 엔티티 누락

**예시**: *"지점별 외화예금 잔액 추이를 보여줘"*

이 질의의 핵심 엔티티는 4개이다:

| 핵심 엔티티 | 역할 |
|------------|------|
| 지점 | 조직 단위 (GROUP BY 대상) |
| 외화예금 | 상품/계좌 유형 (필터 대상) |
| 잔액 | 측정 값 (집계 대상) |
| 추이 | 시계열 분석 (시간축 필요) |

`search_query_builder.py`가 키워드를 추출하면 `["지점", "외화예금", "잔액"]` 정도가 나온다.
ES 검색이 "외화" + "예금"으로 검색하면:

| 반환된 테이블 | 실제 용도 | 문제 |
|--------------|----------|------|
| `TB_FX_RATE` | 환율 정보 | "외화"에 매칭됐지만 예금과 무관 |
| `TB_DEPOSIT_INFO` | 원화+외화 예금 현황 | 시계열 아님, 현재 스냅샷 |
| `TB_FX_REMIT_HIST` | 외화 송금 이력 | "외화"에 매칭됐지만 예금이 아닌 송금 |

정작 필요한 `TB_FX_DEPOSIT_DAILY_BAL`(외화예금 일별 잔액)은 누락될 수 있다.
현재는 이 상태로 바로 `generate_sql`에 넘기므로 **LLM이 `TB_FX_RATE`로 SQL을 만들어버린다.**

### 1.2 문제 경로 2 — 관련은 있지만 "목적에 맞지 않는" 테이블

**예시**: *"이번 달 신규 대출 건수를 알려줘"*

| 반환된 테이블 | 적합성 |
|--------------|--------|
| `TB_LOAN_INFO` | 건별 대출 정보 — 현재 상태만 있고 "신규" 시점을 판별하려면 `REG_DT` 컬럼 필요 |
| `TB_LOAN_MONTHLY_STAT` | 월별 대출 통계 — 신규 건수 컬럼이 있을 수도, 없을 수도 |

두 테이블 모두 "대출" 키워드에 매칭되어 반환되지만,
**"신규 건수"를 산출할 수 있는 컬럼이 실제로 있는지**는 확인하지 않는다.
`similar_table_resolver.py`의 유사 테이블 그룹이 일부 커버하지만, **모든 케이스를 사전 정의할 수 없다.**

### 1.3 문제 경로 3 — 검색 자체가 빗나감

**예시**: *"카드론 연체 고객 중 신용등급 하락자 비율"*

`search_query_builder.py`의 도메인 사전에 "카드론"이 없으면:
- "카드" + "론"으로 분리 → `TB_CARD_STAT`(카드 통계)만 반환
- "카드론"이 실제로는 `TB_LOAN_INFO`에서 `LOAN_TYPE_CD = '03'`(카드론)인 데이터인데, 이 매핑을 모름

검색 자체가 빗나갔는데 이를 인지하는 메커니즘이 없다.

---

## 2. 개선 아이디어 비교

### 2.1 아이디어 A — 엔티티 커버리지 체크 (규칙 기반)

가장 가볍고 빠른 접근. LLM 호출 없이 수행 가능하다.

```python
# 의사코드
def evaluate_entity_coverage(user_query: str, context: ContextInfo) -> EvalResult:
    # 1. 사용자 질의에서 핵심 엔티티 추출 (이미 search_query_builder에서 수행)
    required_entities = extract_entities(user_query)
    # 예: {"지점", "외화예금", "잔액"}

    # 2. 수집된 테이블의 컬럼/설명에서 커버 가능한 엔티티 계산
    covered = set()
    for table in context.table_metas:
        for col in table.columns:
            for entity in required_entities:
                if entity_matches(entity, col.name, col.description, table.description):
                    covered.add(entity)

    # 3. 커버리지 판정
    coverage_ratio = len(covered) / len(required_entities)
    missing = required_entities - covered

    if coverage_ratio >= 0.8:
        return EvalResult(action="PROCEED", missing=missing)
    elif coverage_ratio >= 0.5:
        return EvalResult(action="REFINE_SEARCH", missing=missing)
    else:
        return EvalResult(action="CLARIFY", missing=missing)
```

**장점**: 빠르고, 소형 LLM에 의존하지 않음

**단점**: 엔티티 매칭이 키워드 수준이라 의미적 유사성을 놓침

### 2.2 아이디어 B — LLM 기반 적합성 판정 (경량 프롬프트)

수집된 컨텍스트를 **요약**해서 LLM에게 "이 테이블들로 이 질문에 답할 수 있나?"를 물어본다.

```
[System]
당신은 데이터 분석 전문가입니다. 사용자 질의에 답하기 위해 필요한 데이터가
수집된 테이블에 충분히 포함되어 있는지 판단하세요.

[User Query] "지점별 외화예금 잔액 추이를 보여줘"

[수집된 테이블 요약]
1. TB_FX_RATE: 통화별 환율 정보 (USD_RATE, JPY_RATE...)
2. TB_DEPOSIT_INFO: 예금 현황 (ACCT_NO, BAL_AMT, CUST_NO) - 스냅샷
3. TB_FX_REMIT_HIST: 외화 송금 이력 (REMIT_AMT, REMIT_DT)

[판단 기준]
- 질의의 핵심 요소: 지점(조직), 외화예금(상품), 잔액(금액), 추이(시계열)
- 각 요소가 수집된 테이블로 해결 가능한지 판단

[출력 형식]
{
  "sufficient": false,
  "covered": ["예금 잔액"],
  "missing": ["외화 구분", "지점 정보", "시계열 데이터"],
  "suggestion": "외화예금 잔액의 시계열 데이터를 가진 테이블을 재검색하세요.
                 키워드: 외화예금잔액, FX_DEPOSIT, 일별잔액"
}
```

**장점**: 의미 수준에서 판단 가능, 재검색 키워드까지 제안

**단점**: LLM 호출 1회 추가 (latency + 비용), 소형 모델에서의 판단 품질 우려

### 2.3 아이디어 C — 하이브리드 (A + B 결합) ← 권장안

```
collect_context
    ↓
[규칙 기반 엔티티 커버리지 체크]  ← 아이디어 A (빠름, 비용 없음)
    ├─ coverage ≥ 80% → generate_sql로 진행
    ├─ coverage 50~80% → [LLM 적합성 판정] ← 아이디어 B (정밀)
    │      ├─ sufficient → generate_sql
    │      └─ insufficient → refine_search (키워드 변형 재검색, 최대 1회)
    └─ coverage < 50% → refine_search 또는 clarify (사용자 확인)
```

#### 구체 예시 트레이스

**질의**: *"지점별 외화예금 잔액 추이를 보여줘"*

**Step 1** — 엔티티 추출: `{지점, 외화예금, 잔액, 추이}`

**Step 2** — 규칙 기반 체크:
- `TB_FX_RATE` → "환율" ≠ 지점/외화예금/잔액 → 커버 0개
- `TB_DEPOSIT_INFO` → "예금", "잔액" 매칭 → 커버 {잔액}... 하지만 "외화"가 구분 가능한 컬럼 있는지? `CURRENCY_CD` 있음 → {외화예금, 잔액}
- `TB_FX_REMIT_HIST` → "외화" 매칭이지만 송금 ≠ 예금 → 커버 0개
- 지점: 어떤 테이블에도 `BRANCH_CD` 없음 → 미커버
- 추이: 시계열 컬럼(`_DT`, `_YMD`) 있는 테이블 없음 → 미커버
- **커버리지: 2/4 = 50%** → LLM 판정으로 진행

**Step 3** — LLM 판정:
- "지점 정보와 시계열 잔액 데이터가 누락됨"
- 재검색 키워드 제안: `"외화예금 일별잔액"`, `"FX_DEPOSIT_BAL"`, `"지점별 잔액"`

**Step 4** — `refine_search`: 제안된 키워드로 ES 재검색
- `TB_FX_DEPOSIT_DAILY_BAL` 발견 (지점코드 + 일자 + 외화예금 잔액)
- 커버리지 재계산: 4/4 = 100% → `generate_sql`로 진행

---

## 3. C안 상세 설계

### 3.1 파이프라인 흐름 변경

**현재 (AS-IS)**:
```
collect_context → generate_sql
```

**변경 후 (TO-BE)**:
```
collect_context → evaluate_context → [PROCEED    → generate_sql      ]
                                     [REFINE     → refine_search → evaluate_context (재평가)]
                                     [CLARIFY    → clarify                          ]
```

### 3.2 상태 모델 확장

`state.py`의 `PipelineState`에 다음 필드를 추가한다:

```python
class ContextEvaluation(BaseModel):
    """컨텍스트 적합성 평가 결과."""

    coverage_ratio: float = 0.0                     # 엔티티 커버리지 비율 (0.0~1.0)
    required_entities: list[str] = Field(default_factory=list)   # 질의에서 추출한 핵심 엔티티
    covered_entities: list[str] = Field(default_factory=list)    # 테이블이 커버하는 엔티티
    missing_entities: list[str] = Field(default_factory=list)    # 누락된 엔티티
    action: str = "PROCEED"                          # PROCEED | REFINE | CLARIFY
    llm_judgment: str = ""                           # LLM 판정 결과 (50~80% 구간에서만 사용)
    refined_keywords: list[str] = Field(default_factory=list)    # 재검색용 키워드
    evaluation_method: str = ""                      # "rule_based" | "llm_assisted"


class PipelineState(BaseModel):
    # ... 기존 필드 ...

    # 컨텍스트 평가 (신규)
    context_evaluation: ContextEvaluation = Field(default_factory=ContextEvaluation)
    context_refine_count: int = 0      # 재검색 횟수 (최대 1회)
```

### 3.3 `evaluate_context` 노드 구현

#### 3.3.1 Phase 1 — 핵심 엔티티 추출

`search_query_builder.py`의 기존 엔티티 추출 로직을 재활용하되,
**엔티티를 역할별로 분류**한다:

```python
@dataclass
class QueryEntity:
    """질의에서 추출된 핵심 엔티티."""
    name: str           # "외화예금"
    role: EntityRole     # DIMENSION | MEASURE | FILTER | TEMPORAL
    source_token: str    # 원본 토큰


class EntityRole(str, Enum):
    DIMENSION = "dimension"    # GROUP BY 대상 (지점, 고객유형, 연령대)
    MEASURE = "measure"        # 집계 대상 (잔액, 건수, 금리)
    FILTER = "filter"          # WHERE 조건 (VIP, 연체, 정상)
    TEMPORAL = "temporal"      # 시간축 (추이, 월별, 전년 대비)
    SUBJECT = "subject"        # 주체 (고객, 계좌, 대출)
```

**분류 규칙**:

| 패턴 | 역할 | 예시 |
|------|------|------|
| "~별", "~유형", "~등급" | DIMENSION | "지점별", "고객유형별" |
| "~금액", "~잔액", "~건수", "~비율" | MEASURE | "잔액 합계", "연체율" |
| "~인", "~이상", "~미만", "~중" | FILTER | "VIP인", "5천만원 이상" |
| "추이", "변화", "~별(시간)" | TEMPORAL | "월별 추이", "전년 대비" |
| 명사형 도메인 용어 | SUBJECT | "고객", "대출", "카드" |

#### 3.3.2 Phase 2 — 규칙 기반 커버리지 체크

수집된 테이블 메타의 **테이블 설명 + enriched_description + 컬럼명 + 컬럼 설명**에서
각 엔티티가 커버되는지 판단한다:

```python
def check_entity_coverage(
    entity: QueryEntity,
    table_metas: list[TableMeta],
) -> CoverageResult:
    """단일 엔티티의 커버리지를 판정한다."""

    # 1. 직접 매칭: 컬럼명/설명에 엔티티 키워드 포함
    for table in table_metas:
        # 테이블 설명 매칭
        if entity.name in table.table_description or entity.name in table.enriched_description:
            return CoverageResult(covered=True, matched_table=table.table_name)

        # 컬럼 매칭
        for col in table.columns:
            if entity_matches_column(entity, col):
                return CoverageResult(covered=True, matched_table=table.table_name, matched_column=col.column_name)

    # 2. 역할별 패턴 매칭
    if entity.role == EntityRole.TEMPORAL:
        # 시계열 엔티티: _DT, _YMD, _YYYYMM 류 컬럼이 있으면 커버
        for table in table_metas:
            if any(is_date_column(col) for col in table.columns):
                return CoverageResult(covered=True, matched_table=table.table_name)

    if entity.role == EntityRole.DIMENSION:
        # 차원 엔티티: _CD, _TYPE, _GRP 류 컬럼 중 의미 매칭
        for table in table_metas:
            if any(dimension_matches(entity, col) for col in table.columns):
                return CoverageResult(covered=True, matched_table=table.table_name, matched_column=col.column_name)

    return CoverageResult(covered=False)


def is_date_column(col: ColumnMeta) -> bool:
    """날짜/시계열 컬럼인지 판단."""
    date_suffixes = ("_DT", "_YMD", "_YYYYMM", "_DATE", "_YYMM")
    date_types = ("DATE", "TIMESTAMP", "DATETIME")
    return (
        col.column_name.upper().endswith(date_suffixes)
        or col.data_type.upper() in date_types
    )
```

**커버리지 비율에 따른 분기 결정**:

```python
def decide_action(coverage_ratio: float, context_refine_count: int) -> str:
    if coverage_ratio >= 0.8:
        return "PROCEED"
    elif coverage_ratio >= 0.5:
        if context_refine_count < 1:
            return "REFINE"          # LLM 판정 후 재검색
        else:
            return "PROCEED"         # 이미 1회 재검색 했으므로 진행
    else:  # < 0.5
        if context_refine_count < 1:
            return "REFINE"
        else:
            return "CLARIFY"         # 재검색 후에도 부족하면 사용자에게 확인
```

#### 3.3.3 Phase 3 — LLM 적합성 판정 (조건부)

coverage 50~80% 구간에서만 호출된다. 프롬프트를 최소화하여 소형 모델에서도 동작하도록 설계한다:

```python
CONTEXT_EVALUATION_PROMPT = """\
사용자 질의: {user_query}

수집된 테이블:
{table_summary}

누락 의심 항목: {missing_entities}

위 테이블로 사용자 질의에 답할 수 있습니까?
답할 수 없다면, 어떤 데이터가 부족하고 어떤 키워드로 재검색해야 합니까?

다음 형식으로만 답하세요:
sufficient: yes 또는 no
missing: 부족한 데이터 항목 (쉼표 구분)
keywords: 재검색 키워드 (쉼표 구분)
"""
```

**소형 모델 대응 포인트**:

- JSON이 아닌 `key: value` 단순 형식 사용
- 복잡한 추론 요구 제거, 판단(yes/no) + 키워드 나열만 요청
- `llm_retry.py`의 기존 parse retry 메커니즘 활용

#### 3.3.4 Phase 4 — `refine_search` (키워드 변형 재검색)

재검색 키워드를 생성하는 3가지 소스:

```python
async def build_refined_keywords(
    missing_entities: list[str],
    context: ContextInfo,
    llm_suggestion: list[str],
) -> list[str]:
    """재검색 키워드를 생성한다."""
    keywords = set()

    # 소스 1: LLM이 제안한 키워드
    keywords.update(llm_suggestion)

    # 소스 2: 도메인 사전에서 누락 엔티티의 유의어 조회
    for entity in missing_entities:
        synonyms = finance_terms.get_synonyms(entity)
        keywords.update(synonyms)

    # 소스 3: 보고서 SQL 역참조
    #   누락 엔티티가 포함된 보고서 SQL을 찾아 거기서 사용된 테이블명 추출
    for entity in missing_entities:
        related_reports = await es_connector.search_report_sql(entity)
        for report_sql in related_reports:
            table_names = extract_table_names_from_sql(report_sql)
            keywords.update(table_names)

    return list(keywords)[:10]  # 최대 10개
```

**재검색 실행 후**:
- 새로 수집된 테이블을 기존 `context.table_metas`에 **병합** (중복 제거)
- `context_refine_count += 1`
- `evaluate_context`를 다시 실행하여 커버리지 재판정

### 3.4 재검색 안전장치

| 안전장치 | 설정 | 이유 |
|---------|------|------|
| 재검색 최대 횟수 | 1회 | 무한 루프 방지 |
| LLM 판정 타임아웃 | `settings.llm_context_timeout` (기존 값 재사용) | latency 관리 |
| 재검색 후 테이블 최대 수 | 15건 | 토큰 예산 초과 방지 |
| LLM 판정 실패 시 | `PROCEED`로 진행 | 기존 동작과 동일 (graceful degradation) |

### 3.5 LangGraph 라우팅 변경

```python
def route_after_evaluate_context(state: PipelineState) -> str:
    """evaluate_context 노드 이후 라우팅."""
    action = state.context_evaluation.action

    if action == "PROCEED":
        return "generate_sql"
    elif action == "REFINE":
        return "refine_search"
    elif action == "CLARIFY":
        return "clarify"
    else:
        return "generate_sql"  # fallback


# 그래프 빌더 변경
graph.add_node("evaluate_context", evaluate_context_node)
graph.add_node("refine_search", refine_search_node)

graph.add_edge("collect_context", "evaluate_context")                  # AS-IS: collect_context → generate_sql
graph.add_conditional_edges("evaluate_context", route_after_evaluate_context)
graph.add_edge("refine_search", "evaluate_context")                    # 재검색 후 재평가
```

### 3.6 변경된 전체 파이프라인

```
preprocess → classify_intent → [clarify | collect_context]
                                         ↓
                                collect_context
                                         ↓
                                evaluate_context  ←──────────────────┐
                                    ↓         ↓         ↓           │
                              [PROCEED]   [REFINE]   [CLARIFY]      │
                                 ↓            ↓         ↓           │
                           generate_sql   refine_search  clarify    │
                                              ↓                     │
                                              └─────────────────────┘
                                 ↓
                           validate_sql
                                 ↓
                     [retry | execute_sql | clarify | error_end]
                                     ↓
                              execute_sql → [analyze_data | format_response]
                                                   ↓
                                         format_response → END
```

**노드 수 변경**: 9개 → 11개 (`evaluate_context`, `refine_search` 추가)

---

## 4. 단위 테스트 설계

`test_queries.json`의 기존 테스트 케이스를 기반으로,
`evaluate_context` 노드의 동작을 검증하는 테스트 셋을 설계한다.

### 4.1 테스트 구조

```python
# tests/unit/test_context_evaluator.py

@dataclass
class ContextEvalTestCase:
    """컨텍스트 적합성 평가 테스트 케이스."""
    test_id: str                    # CE-001
    source_query_id: str            # 원본 test_queries.json ID (EX-001 등)
    user_input: str                 # 사용자 질의
    expected_entities: list[str]    # 추출되어야 하는 핵심 엔티티
    provided_tables: list[str]      # 시뮬레이션: 수집되었다고 가정하는 테이블
    provided_columns: dict          # 테이블별 컬럼 목록 (커버리지 판정용)
    expected_action: str            # PROCEED | REFINE | CLARIFY
    expected_missing: list[str]     # 누락 판정되어야 하는 엔티티
    description: str                # 테스트 의도 설명
```

### 4.2 테스트 케이스

#### PROCEED 케이스 — 컨텍스트 충분, 바로 SQL 생성 진행

```python
CE_001 = ContextEvalTestCase(
    test_id="CE-001",
    source_query_id="EX-001",
    user_input="우리 은행 전체 고객 수 알려줘",
    expected_entities=["고객", "건수"],
    provided_tables=["TB_CUST_INFO"],
    provided_columns={
        "TB_CUST_INFO": ["CUST_NO", "CUST_NM", "CUST_TYPE_CD", "GENDER_CD", "AGE_GRP_CD", "REG_DT"]
    },
    expected_action="PROCEED",
    expected_missing=[],
    description="단일 테이블 COUNT — 엔티티(고객)가 완전히 커버됨. coverage=100%"
)

CE_002 = ContextEvalTestCase(
    test_id="CE-002",
    source_query_id="EX-003",
    user_input="담보대출 평균 금리 알려줘",
    expected_entities=["담보대출", "금리"],
    provided_tables=["TB_LOAN_INFO"],
    provided_columns={
        "TB_LOAN_INFO": ["LOAN_NO", "CUST_NO", "LOAN_TYPE_CD", "LOAN_AMT", "LOAN_BAL", "INT_RATE", "OVDU_GRD_CD"]
    },
    expected_action="PROCEED",
    expected_missing=[],
    description="대출유형(LOAN_TYPE_CD로 담보대출 필터) + 금리(INT_RATE) 모두 커버. coverage=100%"
)

CE_003 = ContextEvalTestCase(
    test_id="CE-003",
    source_query_id="EX-005",
    user_input="연령대별 고객 수와 평균 예금 잔액을 알고 싶어",
    expected_entities=["연령대", "고객", "예금잔액"],
    provided_tables=["TB_CUST_INFO", "TB_ACCT_BAL"],
    provided_columns={
        "TB_CUST_INFO": ["CUST_NO", "AGE_GRP_CD", "CUST_NM"],
        "TB_ACCT_BAL": ["ACCT_NO", "CUST_NO", "BAL_AMT", "ACCT_TYPE_CD"]
    },
    expected_action="PROCEED",
    expected_missing=[],
    description="2테이블 JOIN — 연령대(AGE_GRP_CD) + 잔액(BAL_AMT) 모두 커버. coverage=100%"
)
```

#### REFINE 케이스 — 핵심 엔티티 일부 누락, 재검색 필요

```python
CE_004 = ContextEvalTestCase(
    test_id="CE-004",
    source_query_id="EX-006",
    user_input="지점별 대출 잔액 합계를 많은 순서대로 10개만 보여줘",
    expected_entities=["지점", "대출", "잔액"],
    provided_tables=["TB_LOAN_INFO"],
    provided_columns={
        "TB_LOAN_INFO": ["LOAN_NO", "CUST_NO", "LOAN_TYPE_CD", "LOAN_BAL", "INT_RATE"]
    },
    expected_action="REFINE",
    expected_missing=["지점"],
    description=(
        "TB_LOAN_INFO에 대출/잔액은 있지만 지점 정보(BRANCH_CD)가 없음. "
        "TB_BRANCH가 누락되어 JOIN 불가. coverage=2/3≈67% → LLM 판정 후 재검색"
    )
)

CE_005 = ContextEvalTestCase(
    test_id="CE-005",
    source_query_id="EX-010",
    user_input="VIP 고객이 보유한 예금, 대출, 카드 현황을 한눈에 보고 싶어",
    expected_entities=["VIP고객", "예금", "대출", "카드"],
    provided_tables=["TB_CUST_INFO", "TB_ACCT_BAL"],
    provided_columns={
        "TB_CUST_INFO": ["CUST_NO", "CUST_GRD_CD", "CUST_NM"],
        "TB_ACCT_BAL": ["ACCT_NO", "CUST_NO", "BAL_AMT"]
    },
    expected_action="REFINE",
    expected_missing=["대출", "카드"],
    description=(
        "VIP(CUST_GRD_CD) + 예금(TB_ACCT_BAL) 커버되지만 "
        "대출(TB_LOAN_INFO) + 카드(TB_CARD_INFO) 테이블 누락. coverage=2/4=50% → 재검색"
    )
)

CE_006 = ContextEvalTestCase(
    test_id="CE-006",
    source_query_id="AN-009",
    user_input="지점별 연체율을 분석해서 어느 지점이 가장 관리가 필요한지 알려줘",
    expected_entities=["지점", "연체율", "대출"],
    provided_tables=["TB_LOAN_INFO"],
    provided_columns={
        "TB_LOAN_INFO": ["LOAN_NO", "CUST_NO", "LOAN_BAL", "OVDU_GRD_CD", "OVDU_AMT"]
    },
    expected_action="REFINE",
    expected_missing=["지점"],
    description=(
        "연체 관련 컬럼(OVDU_GRD_CD, OVDU_AMT)은 있지만 지점 정보 없음. "
        "TB_BRANCH 누락. coverage≈67% → 재검색 필요"
    )
)

CE_007 = ContextEvalTestCase(
    test_id="CE-007",
    source_query_id="VZ-018",
    user_input="최근 한 달간 일별 거래 건수 추이를 라인 차트로 보여줘",
    expected_entities=["거래", "건수", "추이"],
    provided_tables=["TB_TRX_HST"],
    provided_columns={
        "TB_TRX_HST": ["TRX_NO", "ACCT_NO", "TRX_AMT", "CHNL_CD"]
        # TRX_DT 컬럼이 누락된 상태를 시뮬레이션
    },
    expected_action="REFINE",
    expected_missing=["추이"],
    description=(
        "거래 테이블은 있지만 시계열 컬럼(TRX_DT)이 검색 결과에서 누락. "
        "TEMPORAL 엔티티 미커버. coverage=2/3≈67% → 재검색 또는 컬럼 재확인"
    )
)
```

#### CLARIFY 케이스 — 검색 자체가 빗나감, 사용자 확인 필요

```python
CE_008 = ContextEvalTestCase(
    test_id="CE-008",
    source_query_id="-",
    user_input="카드론 연체 고객 중 신용등급 하락자 비율",
    expected_entities=["카드론", "연체", "신용등급"],
    provided_tables=["TB_CARD_INFO"],
    provided_columns={
        "TB_CARD_INFO": ["CARD_NO", "CUST_NO", "CARD_TYPE_CD", "MON_USE_AMT"]
    },
    expected_action="REFINE",
    expected_missing=["카드론", "연체", "신용등급"],
    description=(
        "카드론은 TB_LOAN_INFO의 LOAN_TYPE_CD='03'이지만 카드 테이블만 검색됨. "
        "핵심 엔티티 3개 모두 미커버. coverage=0/3=0%. "
        "1차 재검색 후에도 부족하면 CLARIFY로 전환"
    )
)

CE_009 = ContextEvalTestCase(
    test_id="CE-009",
    source_query_id="EX-MKT-008",
    user_input="50대 이상 고객 중에 퇴직연금이나 연금보험 하나도 없는 고객 명단 뽑아줘",
    expected_entities=["50대이상 고객", "퇴직연금", "연금보험"],
    provided_tables=["TB_CUST_INFO"],
    provided_columns={
        "TB_CUST_INFO": ["CUST_NO", "CUST_NM", "AGE_GRP_CD", "CUST_TYPE_CD"]
    },
    expected_action="REFINE",
    expected_missing=["퇴직연금", "연금보험"],
    description=(
        "고객 정보는 있지만 연금 상품 테이블이 검색되지 않음. "
        "실제 연금 테이블이 DB에 없을 수 있으므로, 재검색 후 여전히 없으면 CLARIFY"
    )
)

CE_010 = ContextEvalTestCase(
    test_id="CE-010",
    source_query_id="AN-MKT-004",
    user_input="잔액이 줄어드는 추세인 고객이 얼마나 되는지, 이탈 위험이 높은 편인지 분석해줘",
    expected_entities=["잔액", "추세", "고객"],
    provided_tables=["TB_CUST_INFO"],
    provided_columns={
        "TB_CUST_INFO": ["CUST_NO", "CUST_NM", "CUST_GRD_CD"]
    },
    expected_action="REFINE",
    expected_missing=["잔액", "추세"],
    description=(
        "고객 테이블만 있고 잔액 관련 테이블(TB_ACCT_BAL, TB_ACCT_SMRY) 없음. "
        "잔액 추세 판단 불가. coverage=1/3≈33% → 재검색"
    )
)
```

#### 재검색 후 PROCEED 전환 케이스

```python
CE_011 = ContextEvalTestCase(
    test_id="CE-011",
    source_query_id="EX-006",
    user_input="지점별 대출 잔액 합계를 많은 순서대로 10개만 보여줘",
    expected_entities=["지점", "대출", "잔액"],
    provided_tables=["TB_LOAN_INFO", "TB_BRANCH"],  # 재검색으로 TB_BRANCH 추가됨
    provided_columns={
        "TB_LOAN_INFO": ["LOAN_NO", "CUST_NO", "BRANCH_CD", "LOAN_BAL"],
        "TB_BRANCH": ["BRANCH_CD", "BRANCH_NM", "REGION_CD"]
    },
    expected_action="PROCEED",
    expected_missing=[],
    description=(
        "CE-004의 재검색 후 상태. TB_BRANCH가 추가되어 "
        "지점(BRANCH_CD/BRANCH_NM) + 대출(TB_LOAN_INFO) + 잔액(LOAN_BAL) 모두 커버. "
        "coverage=3/3=100% → PROCEED"
    )
)

CE_012 = ContextEvalTestCase(
    test_id="CE-012",
    source_query_id="EX-010",
    user_input="VIP 고객이 보유한 예금, 대출, 카드 현황을 한눈에 보고 싶어",
    expected_entities=["VIP고객", "예금", "대출", "카드"],
    provided_tables=["TB_CUST_INFO", "TB_ACCT_BAL", "TB_LOAN_INFO", "TB_CARD_INFO"],
    provided_columns={
        "TB_CUST_INFO": ["CUST_NO", "CUST_GRD_CD", "CUST_NM"],
        "TB_ACCT_BAL": ["ACCT_NO", "CUST_NO", "BAL_AMT"],
        "TB_LOAN_INFO": ["LOAN_NO", "CUST_NO", "LOAN_BAL"],
        "TB_CARD_INFO": ["CARD_NO", "CUST_NO", "CARD_TYPE_CD"]
    },
    expected_action="PROCEED",
    expected_missing=[],
    description=(
        "CE-005의 재검색 후 상태. 4개 테이블 모두 수집되어 "
        "VIP(CUST_GRD_CD) + 예금 + 대출 + 카드 모두 커버. "
        "coverage=4/4=100% → PROCEED"
    )
)
```

#### 엣지 케이스

```python
CE_013 = ContextEvalTestCase(
    test_id="CE-013",
    source_query_id="EX-002",
    user_input="현재 정상 상태인 계좌가 몇 개야?",
    expected_entities=["계좌", "정상상태", "건수"],
    provided_tables=["TB_ACCT_BAL"],
    provided_columns={
        "TB_ACCT_BAL": ["ACCT_NO", "BAL_AMT", "ACCT_STAT_CD", "ACCT_TYPE_CD"]
    },
    expected_action="PROCEED",
    expected_missing=[],
    description=(
        "코드값 기반 필터(ACCT_STAT_CD='01')가 필요하지만, "
        "코드 컬럼 존재 여부만으로 커버 판정 가능. "
        "코드값 매핑의 정확성은 validate_sql에서 별도 검증"
    )
)

CE_014 = ContextEvalTestCase(
    test_id="CE-014",
    source_query_id="EX-MKT-005",
    user_input="VIP 기준 자산에 10% 이내로 근접한 고객 목록이랑 현재 자산 규모 뽑아줘",
    expected_entities=["VIP기준", "자산", "고객"],
    provided_tables=["TB_CUST_INFO", "TB_ACCT_BAL"],
    provided_columns={
        "TB_CUST_INFO": ["CUST_NO", "CUST_GRD_CD", "CUST_NM"],
        "TB_ACCT_BAL": ["ACCT_NO", "CUST_NO", "BAL_AMT"]
    },
    expected_action="PROCEED",
    expected_missing=[],
    description=(
        "VIP 기준 자산 임계값은 업무 매뉴얼(Qdrant)에서 참조해야 하지만, "
        "테이블 커버리지 자체는 충분. 매뉴얼 참조 여부는 별도 검증 대상"
    )
)

CE_015 = ContextEvalTestCase(
    test_id="CE-015",
    source_query_id="AN-017",
    user_input="승인 금액 대비 실행 비율이 낮은 대출 건들의 특성을 분석해줘",
    expected_entities=["승인금액", "실행금액", "대출", "고객특성"],
    provided_tables=["TB_LOAN_INFO"],
    provided_columns={
        "TB_LOAN_INFO": ["LOAN_NO", "CUST_NO", "LOAN_AMT", "LOAN_BAL", "INT_RATE"]
    },
    expected_action="REFINE",
    expected_missing=["승인금액", "고객특성"],
    description=(
        "LOAN_AMT(실행금액)은 있지만 APPR_AMT(승인금액)는 TB_LOAN_MST에 있음. "
        "고객 특성 분석을 위한 TB_CUST_INFO도 누락. "
        "TYPE-4(이중화) 케이스 — 올바른 테이블 조합이 필수. coverage=2/4=50%"
    )
)
```

### 4.3 테스트 실행 구조

```python
# tests/unit/test_context_evaluator.py

import pytest
from src.agents.nodes.context_evaluator import evaluate_entity_coverage, extract_query_entities
from src.agents.state.state import TableMeta, ColumnMeta, ContextInfo


def build_context(tables: dict[str, list[str]]) -> ContextInfo:
    """테스트용 ContextInfo를 간편하게 생성한다."""
    table_metas = []
    for table_name, columns in tables.items():
        cols = [ColumnMeta(column_name=c) for c in columns]
        table_metas.append(TableMeta(table_name=table_name, columns=cols))
    return ContextInfo(table_metas=table_metas)


class TestEntityExtraction:
    """Phase 1: 사용자 질의에서 핵심 엔티티를 정확히 추출하는지 검증."""

    @pytest.mark.parametrize("user_input, expected_entities", [
        ("우리 은행 전체 고객 수 알려줘", ["고객", "건수"]),
        ("지점별 대출 잔액 합계를 많은 순서대로 10개만 보여줘", ["지점", "대출", "잔액"]),
        ("최근 한 달간 일별 거래 건수 추이를 라인 차트로 보여줘", ["거래", "건수", "추이"]),
        ("VIP 고객이 보유한 예금, 대출, 카드 현황을 한눈에 보고 싶어", ["VIP고객", "예금", "대출", "카드"]),
    ])
    def test_entity_extraction(self, user_input: str, expected_entities: list[str]):
        entities = extract_query_entities(user_input)
        entity_names = [e.name for e in entities]
        for expected in expected_entities:
            assert any(expected in name or name in expected for name in entity_names), \
                f"엔티티 '{expected}'가 추출 결과에 없음: {entity_names}"


class TestCoverageCheck:
    """Phase 2: 엔티티 커버리지 판정이 올바른지 검증."""

    @pytest.mark.parametrize("test_case", [CE_001, CE_002, CE_003])
    def test_proceed_cases(self, test_case: ContextEvalTestCase):
        """충분한 컨텍스트 → PROCEED."""
        context = build_context(test_case.provided_columns)
        result = evaluate_entity_coverage(test_case.user_input, context, refine_count=0)
        assert result.action == "PROCEED", \
            f"[{test_case.test_id}] 예상: PROCEED, 실제: {result.action}. missing={result.missing_entities}"

    @pytest.mark.parametrize("test_case", [CE_004, CE_005, CE_006, CE_007])
    def test_refine_cases(self, test_case: ContextEvalTestCase):
        """부분 누락 → REFINE."""
        context = build_context(test_case.provided_columns)
        result = evaluate_entity_coverage(test_case.user_input, context, refine_count=0)
        assert result.action == "REFINE", \
            f"[{test_case.test_id}] 예상: REFINE, 실제: {result.action}"
        for missing in test_case.expected_missing:
            assert any(missing in m for m in result.missing_entities), \
                f"[{test_case.test_id}] 누락 엔티티 '{missing}'가 결과에 없음: {result.missing_entities}"

    @pytest.mark.parametrize("test_case", [CE_011, CE_012])
    def test_refine_then_proceed(self, test_case: ContextEvalTestCase):
        """재검색 후 충분 → PROCEED."""
        context = build_context(test_case.provided_columns)
        result = evaluate_entity_coverage(test_case.user_input, context, refine_count=1)
        assert result.action == "PROCEED", \
            f"[{test_case.test_id}] 재검색 후 예상: PROCEED, 실제: {result.action}"


class TestEdgeCases:
    """엣지 케이스 검증."""

    def test_code_value_filter_coverage(self):
        """코드값 필터(ACCT_STAT_CD='01') — 컬럼 존재만으로 커버 판정."""
        # CE_013
        context = build_context(CE_013.provided_columns)
        result = evaluate_entity_coverage(CE_013.user_input, context, refine_count=0)
        assert result.action == "PROCEED"

    def test_dual_table_type4(self):
        """TYPE-4(이중화) — 승인금액이 다른 테이블에 있는 경우."""
        # CE_015
        context = build_context(CE_015.provided_columns)
        result = evaluate_entity_coverage(CE_015.user_input, context, refine_count=0)
        assert result.action == "REFINE"
        assert any("승인" in m for m in result.missing_entities)

    def test_manual_reference_not_blocking(self):
        """업무 매뉴얼 참조가 필요해도 테이블 커버리지가 충분하면 PROCEED."""
        # CE_014
        context = build_context(CE_014.provided_columns)
        result = evaluate_entity_coverage(CE_014.user_input, context, refine_count=0)
        assert result.action == "PROCEED"

    def test_empty_context_returns_clarify(self):
        """컨텍스트가 완전히 비어있으면 REFINE(첫 시도) 또는 CLARIFY(재시도 후)."""
        context = ContextInfo()
        result = evaluate_entity_coverage("지점별 대출 잔액 보여줘", context, refine_count=1)
        assert result.action == "CLARIFY"

    def test_max_refine_count_forces_proceed(self):
        """재검색 횟수 초과 시 50~80% 구간에서도 PROCEED로 강제 진행."""
        context = build_context(CE_004.provided_columns)
        result = evaluate_entity_coverage(CE_004.user_input, context, refine_count=1)
        assert result.action == "PROCEED"  # 이미 1회 재검색 했으므로 강제 진행
```

### 4.4 테스트 케이스 커버리지 매트릭스

| 테스트 ID | 원본 쿼리 ID | 시나리오 | 커버리지 | 판정 | 검증 포인트 |
|-----------|-------------|---------|---------|------|------------|
| CE-001 | EX-001 | 단일 테이블, 완전 커버 | 100% | PROCEED | 기본 동작 |
| CE-002 | EX-003 | 코드 필터 + 집계, 완전 커버 | 100% | PROCEED | 코드 컬럼 인식 |
| CE-003 | EX-005 | 2테이블 JOIN, 완전 커버 | 100% | PROCEED | 다중 테이블 커버리지 |
| CE-004 | EX-006 | 지점 테이블 누락 | 67% | REFINE | 핵심 DIMENSION 누락 감지 |
| CE-005 | EX-010 | 4테이블 중 2테이블 누락 | 50% | REFINE | 다중 누락 감지 |
| CE-006 | AN-009 | 분석 질의, 지점 누락 | 67% | REFINE | 분석 의도에서의 누락 |
| CE-007 | VZ-018 | 시계열 컬럼 누락 | 67% | REFINE | TEMPORAL 엔티티 감지 |
| CE-008 | (신규) | 검색 완전 빗나감 | 0% | REFINE→CLARIFY | 도메인 용어 미등록 |
| CE-009 | EX-MKT-008 | DB에 테이블 없을 수 있음 | 33% | REFINE→CLARIFY | 존재하지 않는 데이터 |
| CE-010 | AN-MKT-004 | 잔액 테이블 누락 | 33% | REFINE | MEASURE 엔티티 누락 |
| CE-011 | EX-006 | CE-004 재검색 후 | 100% | PROCEED | 재검색 성공 검증 |
| CE-012 | EX-010 | CE-005 재검색 후 | 100% | PROCEED | 다중 테이블 재검색 성공 |
| CE-013 | EX-002 | 코드값 필터 존재만으로 커버 | 100% | PROCEED | 코드값 커버리지 경계 |
| CE-014 | EX-MKT-005 | 매뉴얼 참조 필요하나 테이블 충분 | 100% | PROCEED | 매뉴얼 의존 분리 |
| CE-015 | AN-017 | TYPE-4 이중화 테이블 누락 | 50% | REFINE | 승인/실행 테이블 분리 감지 |

---

## 5. 구현 우선순위

| 순서 | 항목 | 이유 |
|------|------|------|
| 1 | `QueryEntity` 모델 + 엔티티 추출 로직 | Phase 1 — 후속 단계의 입력 |
| 2 | 규칙 기반 커버리지 체크 | Phase 2 — LLM 없이 동작, 빠른 검증 |
| 3 | `evaluate_context` 노드 + LangGraph 라우팅 | 파이프라인 통합 |
| 4 | LLM 적합성 판정 프롬프트 | Phase 3 — 50~80% 구간 정밀화 |
| 5 | `refine_search` 노드 | Phase 4 — 재검색 실행 |
| 6 | 단위 테스트 (CE-001 ~ CE-015) | 동작 검증 |
