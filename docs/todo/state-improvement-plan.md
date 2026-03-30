# State Architecture 개선 계획

> **Version 1.0** (2026-03-29)
> `docs/architecture/state-architecture.md` v2.0의 권고안(R1~R10)을 구현 수준으로 상세화한다.
> R11(sql_generator 프롬프트 우선순위 규칙)은 유사 SQL 평가 체계 도입으로 대체되어 제외.
> 유사 SQL 평가 설계는 `docs/todo/use-case-evaluation-design.md`에 별도 문서화.

---

## 실행 순서 및 의존 관계

```text
R2  (검증 실패 부정 지식) ──────────── 독립, 즉시 실행 가능
R1  (normalized_query 타입 수정) ──── 독립, 즉시 실행 가능
        │
        └── R1 완료 후
                ├── R5 (untyped dict 정형화) ── R1 선행 필요
                └── R10 (query_decomposition 제거) ── R1 선행 필요
R3, R4 ─── 독립
R6 ─── 배치 LLM {unresolved_items} 효과 측정 후 결정
```

| Phase | 권고 | 리스크 | 예상 효과 |
| --- | --- | --- | --- |
| **즉시** | R1, R2 | 낮음 | 타입 안전성 + 검증 실패 지식 환류 |
| **단기** | R3, R4 | 낮음 | 가설 확신도 + 미해소 이유 구분 |
| **중기** | R5, R6, R10 | 중간 | 타입 정형화 + 승격 보강 + 중복 제거 |
| **장기** | R7, R8, R9 | 높음 | 판단 기준 충족 시에만 검토 |

---

## R1. `normalized_query: Any` → `Optional[NormalizedQuery]` 타입 수정

### 검출 이유

`state.py:535`에서 `normalized_query: Any = None`으로 선언되어 있다.
실제 이 필드에는 `NormalizedQuery` Pydantic 모델이 들어가지만, `Any` 타입이라
모든 소비자가 방어적 코드를 작성한다.

### 관련 객체

| 객체 | 위치 | 역할 |
| --- | --- | --- |
| `normalized_query` | `state.py:535` | 8-Slot 정규화 결과 — entities, measures, filters, dimensions, modifiers, time, ambiguities, context |
| `NormalizedQuery` | `src/agents/models/normalization.py` | 8개 슬롯을 갖는 Pydantic 모델. query_normalizer 노드가 생성 |
| `hasattr()` 패턴 | planner.py 등 다수 | `Any` 타입이므로 `hasattr(nq, "measures")` 같은 방어 코드를 사용 |

### 실제 state 예시

```python
# AS-IS: planner.py L240
def _initialize_knowledge_items(nq: Any, ...):
    if nq and hasattr(nq, "ambiguities"):  # ← Any 타입이라 hasattr 필수
        for amb in (nq.ambiguities or []):
            ...

# planner.py L171 — 타입 힌트에는 NormalizedQuery라 적었지만 실제 state에서 꺼내면 Any
def _build_decomposition_from_normalized(nq: NormalizedQuery | None) -> dict:
    if nq is None:
        return empty
    measures = [{"term": m.term, ...} for m in nq.measures]  # Any 접근 — IDE 자동완성 불가
```

### 개선 후 기대효과

```python
# TO-BE: state.py
from src.agents.models.normalization import NormalizedQuery

class PipelineState(BaseModel):
    normalized_query: NormalizedQuery | None = None  # ← 타입 명시

# 소비자 코드:
def _initialize_knowledge_items(nq: NormalizedQuery | None, ...):
    if nq and nq.ambiguities:  # ← hasattr 불필요, IDE 자동완성 동작
        ...
```

- mypy --strict 통과 (현재 Any라 경고 억제 중)
- 모든 소비 노드에서 hasattr() 방어 코드 제거 가능
- IDE에서 nq.measures, nq.filters, nq.time 등 자동완성
- 리팩터링 시 필드명 변경하면 mypy가 모든 사용처 잡아줌
- 런타임 AttributeError 가능성 제거

**영향 파일**: state.py + planner.py + context_explorer.py + sql_generator.py + sql_validator.py

---

## R2. 검증 실패 시 부정 지식(Negative Knowledge) 추가

### 검출 이유

`sql_validator.py`에서 검증 실패 시, `failure_type`/`failure_reason`에만 실패 정보를 기록한다.
이 정보는 sql_generator가 재시도할 때 `{fix_section}`으로 주입되어 SQL 수정에는 활용되지만,
`knowledge_items`에는 환류되지 않는다.

문제는 재시도로도 해결 안 되어 REPLAN이 발생했을 때이다.
recovery_planner는 `knowledge_items`를 참조하여 새 가설을 세우는데,
검증에서 밝혀진 "이 컬럼은 없다" 같은 사실이 knowledge_items에 없으므로 같은 실수를 반복한다.

### 관련 객체

| 객체 | 위치 | 역할 |
| --- | --- | --- |
| `failure_type` | `state.py:418` | `FailureType` Enum — SQL_SYNTAX, SQL_STRUCTURAL, SQL_SEMANTIC_LOCAL, EMPTY_RESULT 등 |
| `failure_reason` | `state.py:420` | 실패 상세 설명 문자열 (LLM 재시도 시 `{fix_section}`에 주입) |
| `DeadEnd` | `state.py:219-225` | hypothesis_id + failure_type + reason + lessons_learned |
| `recovery_planner` | `recovery_planner.py` | 실패한 가설을 FAILED로 전환, DeadEnd 기록, 새 가설 생성 |

### 실제 state 예시

사용자 질의: "연체율 보고서 뽑아줘"

```python
# sql_validator Layer 1 검증 결과:
# "미확인 컬럼: LOAN_STATUS — TB_LOAN의 컬럼 목록에 없습니다."

# AS-IS: failure_reason에만 기록
reason.failure_type = FailureType.SQL_SYNTAX
reason.failure_reason = "미확인 컬럼: LOAN_STATUS — TB_LOAN 컬럼 목록에 없음."

# knowledge_items는 변화 없음:
knowledge_items = [
    KnowledgeItem(key="measure:연체율", value="연체금액/대출잔액×100", status=PROBABLE),
    KnowledgeItem(key="table:TB_LOAN", value="대출 원장", status=CONFIRMED),
    # ← LOAN_STATUS가 없다는 정보가 어디에도 없음!
]
```

→ sql_generator 재시도에서도 같은 실수 → REPLAN → recovery_planner가 knowledge_items를 보는데
"LOAN_STATUS가 틀렸다"는 정보 없음 → 새 가설에서도 LOAN_STATUS 사용 가능성

### 개선 후 기대효과

```python
# TO-BE: sql_validator에서 검증 실패 시 부정 지식 추가
knowledge_items.append(KnowledgeItem(
    key="negative:LOAN_STATUS",
    value="TB_LOAN에 존재하지 않음. 실제 컬럼: STATUS_CD",
    confidence=1.0,
    status=ConfidenceStatus.CONFIRMED,
    source="sql_validator_layer1",
    is_critical=False,
))
```

- sql_generator 재시도: {confirmed_terms}에 "negative:LOAN_STATUS: TB_LOAN에 없음" 노출
- LLM이 STATUS_CD로 수정 → sql_validator 통과
- REPLAN 시 recovery_planner가 "LOAN_STATUS는 없다"를 인식
- 새 가설에서 STATUS_CD 기반 전략 수립

**개선 전**: 재시도 2회 → REPLAN → 같은 컬럼 실수 반복 → 추가 REPLAN (LoopGuard 소모)
**개선 후**: 1회 재시도로 수정 성공 확률 대폭 상승, REPLAN 시에도 교훈 보존

**영향 파일**: sql_validator.py

---

## R3. `Hypothesis`에 `confidence: float` 추가

### 검출 이유

`state.py:91-101`의 `Hypothesis`에는 `priority`만 있고 `confidence`가 없다.
`priority`는 "어떤 가설을 먼저 시도할까"의 순서이지 "이 가설이 얼마나 유효한가"가 아니다.
현재는 가설이 ACTIVE → SUCCESS/FAILED 이진 결과로만 전이된다.

### 관련 객체

| 객체 | 위치 | 역할 |
| --- | --- | --- |
| `Hypothesis` | `state.py:91-101` | hypothesis_id, description, based_on_use_case, missing_terms, priority, strategy, status |
| `HypothesisStatus` | `enums.py:91-97` | PENDING → ACTIVE → SUCCESS \| FAILED |
| `confidence_evaluator` | `confidence_evaluator.py` | 전체 탐색의 readiness를 평가하지만, 개별 가설의 유효성은 평가하지 않음 |
| `calculate_readiness()` | `confidence_scorer.py:90-149` | 3차원 가중평균 (term_resolution 55%, table_coverage 25%, join_path 20%) |

### 실제 state 예시

```python
# AS-IS: 가설이 2개 있을 때
hypotheses = [
    Hypothesis(
        hypothesis_id="H1",
        description="TB_LOAN_EXEC 기반 여신 실행 조회",
        priority=0.7,       # ← "먼저 시도" 순서일 뿐
        strategy="search_table_meta(여신 실행) → sample_data → SQL",
        status=HypothesisStatus.ACTIVE,
    ),
    Hypothesis(
        hypothesis_id="H2",
        description="TB_LOAN_SUMMARY 기반 여신 요약 조회",
        priority=0.5,
        status=HypothesisStatus.PENDING,
    ),
]

# 탐색 중 H1의 knowledge_items:
#   measure:여신건수 → CONFIRMED (0.85)
#   table:TB_LOAN_EXEC → CONFIRMED (0.85)
#   filter:신규 → UNRESOLVED (0.0)       ← 1개 미해소

# H1이 75% 정도 유효한데, 이 정보를 가설 자체에 기록할 수 없음
```

### 개선 후 기대효과

```python
# TO-BE:
class Hypothesis(BaseModel):
    ...
    confidence: float = 0.0    # ← 추가

# confidence_evaluator에서 rule-based 자동 갱신:
def _update_hypothesis_confidence(reason: ReasoningState) -> None:
    hyp = reason.current_hypothesis
    if not hyp:
        return
    related_kis = [ki for ki in reason.knowledge_items if ki.is_critical]
    if related_kis:
        confirmed = [ki for ki in related_kis if ki.confidence >= 0.7]
        hyp.confidence = len(confirmed) / len(related_kis)
```

- H1.confidence = 2/3 = 0.67 (3개 중 2개 해소)
- REPLAN 시 recovery_planner가 "H1은 67% 유효했다"를 인식
- 새 가설 생성 시 "H1 변형" vs "완전히 다른 방향" 판단 근거
- 로그에 "H1(67%) → FAILED" 기록 → 디버깅·분석 품질 향상
- 최종 사용자 관점: 불필요한 REPLAN 루프 감소 → 응답 속도 개선

**영향 파일**: state.py + confidence_evaluator.py

---

## R4. `KnowledgeItem`에 `unresolved_reason` 추가

### 검출 이유

UNRESOLVED 상태인 KI가 3가지 매우 다른 원인을 가질 수 있는데, 현재 구분이 불가능하다:
1. **not_searched** — planner가 등록만 하고 아직 탐색 안 함
2. **not_found** — context_explorer가 검색했지만 결과 없음
3. **ambiguous** — 후보가 여러 개라 결정 못 함

### 관련 객체

| 객체 | 위치 | 역할 |
| --- | --- | --- |
| `KnowledgeItem` | `state.py:68-88` | key, value, confidence, status, source, evidence, is_critical |
| `ConfidenceStatus.UNRESOLVED` | `enums.py:59` | "미확인" — 원인 구분 없이 하나의 상태 |
| `recovery_planner` | `recovery_planner.py` | UNRESOLVED KI 목록을 LLM에 전달하여 새 가설 생성 |

### 실제 state 예시

```python
# AS-IS: 세 가지 다른 원인인데 모두 UNRESOLVED
knowledge_items = [
    # (1) 아직 검색 안 함 — planner가 등록만 한 상태
    KnowledgeItem(key="measure:연체율", status=UNRESOLVED, evidence=[]),

    # (2) 검색했으나 못 찾음 — ES에서 "특수대출"을 검색했지만 결과 0건
    KnowledgeItem(key="filter:특수대출", status=UNRESOLVED, evidence=[]),

    # (3) 모호함 — "잔액"이 BAL_AMT인지 CUR_BAL인지 불명확
    KnowledgeItem(key="measure:잔액", status=UNRESOLVED, evidence=[]),
]

# recovery_planner 관점:
# 세 개 모두 "미해소"인데,
#   (1)은 "검색해봐"
#   (2)는 "다른 키워드로 재검색"
#   (3)은 "사용자에게 물어봐"가 적절함
# → 현재는 구분 불가능 → 모두 동일 전략으로 처리
```

### 개선 후 기대효과

```python
# TO-BE:
class KnowledgeItem(BaseModel):
    ...
    unresolved_reason: Literal["not_searched", "not_found", "ambiguous"] = "not_searched"
```

- planner에서: `KnowledgeItem(key="measure:연체율", unresolved_reason="not_searched")`
- context_explorer 탐색 후 결과 없으면: `ki.unresolved_reason = "not_found"`
- 후보가 2개 이상이면: `ki.unresolved_reason = "ambiguous"`
- recovery_planner가 원인별 차별화된 전략 수립 가능
  - not_searched → 즉시 탐색 계획
  - not_found → 유의어 검색 전략
  - ambiguous → 사용자 확인 경로

**영향 파일**: state.py + planner.py + context_explorer.py

---

## R5. `query_decomposition`, `explored_use_cases`를 TypedDict/Pydantic으로 정형화

### 검출 이유

`state.py:362`의 `query_decomposition: dict`와 `state.py:381`의 `explored_use_cases: list[dict]`는
untyped dict이다. 코드 전체에서 `decomp.get("measures", [])` 같은 문자열 키 접근을 하고,
오타에 대한 컴파일 타임 보호가 없다.

### 관련 객체

| 객체 | 위치 | 역할 |
| --- | --- | --- |
| `query_decomposition: dict` | `state.py:362` | measures, filters, group_by, order_limit, required_concepts를 담는 dict |
| `explored_use_cases: list[dict]` | `state.py:381` | ES에서 검색한 활용사례 목록. 각 dict에 sql, description, table_name 등 |
| `serialize_decomp_slots()` | `utils/llm/prompt.py` | query_decomposition을 프롬프트 치환 문자열로 변환 |

### 실제 state 예시

```python
# AS-IS:
query_decomposition = {
    "measures": [{"term": "고객 수", "agg_function": "COUNT"}],
    "filters": [{"term": "신규", "operator": "eq", "value": "이번 달"}],
    "group_by": ["지점"],
    "order_limit": [],
    "required_concepts": ["고객", "고객 수"],
}
# decomp["mesures"] 오타 → 런타임 KeyError (컴파일 타임에 안 잡힘)
# IDE가 키 목록을 제안하지 못함
```

### 개선 후 기대효과

```python
# TO-BE:
class QueryDecomposition(BaseModel):
    measures: list[MeasureSlot] = Field(default_factory=list)
    filters: list[FilterSlot] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_limit: list[ModifierSlot] = Field(default_factory=list)
    required_concepts: list[str] = Field(default_factory=list)

class UseCaseRef(BaseModel):
    sql: str = ""
    description: str = ""
    table_name: str = ""
    score: float = 0.0
```

- mypy가 오타를 컴파일 타임에 잡음
- IDE 자동완성 동작
- Pydantic validation으로 잘못된 타입 주입 시 즉시 에러

**R1 선행 필요.**
**영향 파일**: state.py + planner.py + sql_generator.py + sql_validator.py + context_explorer.py

---

## R6. `confidence_evaluator`에 rule-based 크로스매칭 fallback 추가

### 검출 이유

배치 LLM 프롬프트에 `{unresolved_items}`를 추가하여 LLM이 기존 UNRESOLVED KI의 key를
그대로 사용해 승격하도록 유도했으나, LLM이 지시를 따르지 않으면 여전히 새 key로 KI를 생성한다.

```
지시: "measure:연체율" key를 그대로 써서 승격하세요
LLM 응답: glossary:연체율 (새 key) → 기존 measure:연체율은 여전히 UNRESOLVED
```

### 관련 객체

| 객체 | 위치 | 역할 |
| --- | --- | --- |
| `all_critical_confirmed()` | `confidence_scorer.py:152-168` | is_critical=True인 KI 중 UNRESOLVED/CANDIDATE/CONFLICTED가 0개인지 확인 |
| `evaluate_readiness()` | `confidence_scorer.py:46-87` | SSOT 판정 — score ≥ 0.65 and all_critical_confirmed() → GENERATE |
| `_promote_sampled_confidence()` | `context_explorer.py:1176-1218` | `table:` prefix KI만 승격, 중복 제거 |

### 실제 state 예시

```python
# 배치 LLM이 지시를 따르지 않은 경우:
knowledge_items = [
    # planner 원본 — 여전히 UNRESOLVED
    KnowledgeItem(key="measure:대출실행건수", status=UNRESOLVED, confidence=0.0, is_critical=True),
    # 배치 LLM이 새 key로 추가 — 사실상 같은 개념
    KnowledgeItem(key="column:EXEC_CNT", value="대출 실행 건수, COUNT 대상", status=PROBABLE, confidence=0.7),
]

# all_critical_confirmed():
#   measure:대출실행건수 (UNRESOLVED, is_critical=True) → False!
#   → score 0.65 이상이어도 GENERATE 불가 → 불필요한 EXPLORE/REPLAN
```

### 개선 후 기대효과

```python
# TO-BE: confidence_evaluator에 크로스매칭 함수 추가
def _cross_match_unresolved(reason: ReasoningState) -> None:
    """UNRESOLVED KI와 개념어가 일치하는 해소된 KI가 있으면 승격한다."""
    resolved_values = {}
    for ki in reason.knowledge_items:
        if ki.confidence >= 0.6 and ki.value:
            resolved_values[ki.value.strip()] = ki

    for ki in reason.knowledge_items:
        if ki.status != ConfidenceStatus.UNRESOLVED:
            continue
        term = ki.key.split(":", 1)[-1]
        for concept, resolved_ki in resolved_values.items():
            if term in concept or concept in term:
                ki.promote(
                    new_status=resolved_ki.status,
                    value=resolved_ki.value,
                    confidence=resolved_ki.confidence,
                    source="cross_match_fallback",
                    evidence=f"개념어 일치: {resolved_ki.key}",
                )
                break
```

- LLM 비결정성에 강건한 rule-based 보완
- all_critical_confirmed() 통과율 향상
- 불필요한 EXPLORE/REPLAN 루프 1~2회 제거

**배치 LLM `{unresolved_items}` 효과 측정 후 결정.**
**영향 파일**: confidence_evaluator.py (또는 confidence_scorer.py)

---

## R10. `query_decomposition` 제거 → `normalized_query` 직접 참조

### 검출 이유

`planner.py:170-206`의 `_build_decomposition_from_normalized()`이 `normalized_query`의
8개 슬롯을 dict로 순수 복사한다. 같은 정보가 2곳에 존재하는 정보 중복.

### 관련 객체

| 객체 | 위치 | 역할 |
| --- | --- | --- |
| `normalized_query` | `state.py:535` | 8-Slot 정규화 결과 — **원본** |
| `query_decomposition` | `state.py:362` | normalized_query에서 추출한 dict — **복사본** |
| `serialize_decomp_slots()` | `utils/llm/prompt.py` | query_decomposition → 프롬프트 치환 |
| `_build_decomposition_from_normalized()` | `planner.py:170-206` | 복사 로직 |

### 주의사항

`required_concepts`는 `entities`와 `measures`의 term을 결합한 **파생 필드**이다.
단순 복사가 아니므로, `query_decomposition` 제거 시 이 로직을 별도로 보존해야 한다.

```python
# 파생 로직 (planner.py L197-198)
required_concepts = [e.term for e in nq.entities]
required_concepts.extend(m.get("term", "") for m in measures)
```

### 실제 state 예시

```python
# AS-IS: 같은 사실이 두 곳에 존재
normalized_query = NormalizedQuery(
    measures=[Measure(term="고객 수", agg_function="COUNT")],
    filters=[Filter(target="신규", filter_type="eq", values=["이번 달"])],
    dimensions=[Dimension(term="지점", role="GROUP")],
    ...
)

query_decomposition = {
    "measures": [{"term": "고객 수", "agg_function": "COUNT"}],     # ← 동일
    "filters": [{"term": "신규", "operator": "eq", "value": "이번 달"}], # ← 동일
    "group_by": ["지점"],                                            # ← 동일
    ...
}
```

### 개선 후 기대효과

- state 필드 1개 감소 (query_decomposition 삭제)
- planner에서 불필요한 복사 함수 삭제
- 정보의 단일 원천(SSOT) 확보
- sql_generator/sql_validator가 원본을 직접 참조
- 프롬프트 토큰 절감 (중복 섹션 제거 시)

**R1 선행 필수** — `normalized_query: Any`인 상태에서 직접 참조하면 방어 코드가 오히려 늘어남.
**영향 파일**: state.py, planner.py, sql_generator.py, sql_validator.py, context_explorer.py, prompt.py

---

## R7. `normalized_query`를 Reason에서 보정 가능하게 변경 (장기)

### 검출 이유

탐색 중 "연체 현황이 건별 원장이 아니라 월말 스냅샷"임을 발견해도
normalized_query는 불변이라 재해석을 기록할 수 없다.

### 현재 대체

recovery_planner가 새 가설을 세우는 것으로 "사실상의 재해석" 효과를 내고 있다.

### 판단 기준

recovery_planner로 커버되지 않는 재해석 실패 사례가 3건 이상 누적될 때 검토.

### 리스크

- 무한 재해석 루프
- 이전 탐색 결과의 전제 무효화
- 디버깅·재현 복잡도 증가

---

## R8. Reason 상태 세션 캐싱 (장기)

### 검출 이유

CONFLICTED → 사용자 확인 → 파이프라인 재실행 시 모든 탐색 상태가 리셋된다.

### 현재 대체

conversation_history에 이전 맥락이 포함되어 "빈 상태지만 맥락 있음" 구조.

### 판단 기준

CONFLICTED 명확화 빈도가 전체 질의의 10% 이상일 때 검토.

### 리스크

캐시된 상태의 일관성 보장, 캐시 무효화 조건 정의, 복잡도.

---

## R9. knowledge_items 관계형 구조 전환 (장기)

### 검출 이유

"TB_LOAN의 LOAN_DCD 컬럼이 대출구분이다" 같은 관계를 key 네이밍 컨벤션으로만 암시.
`ki.key.startswith("table:")` 문자열 파싱에 의존.

### 현재 대체

플랫 구조가 LLM이 생성/수정하기 쉽고, 새 유형 추가 시 key 접두사만 추가.

### 판단 기준

Neo4j 온톨로지 통합 시점 (관계 추론이 핵심이 되는 시점).

### 리스크

그래프 구조로 전환 시 LLM 프롬프트 주입 복잡도 급증, 전체 노드 수정 필요.
