# State Architecture 비판적 분석 — 데이터 활용 파이프라인 관점

> **Version 1.1** (2026-03-28)
> 현재 `PipelineState` + `ReasoningState` 구조가 "데이터 분석가의 사고 흐름"을 적절히 모델링하고 있는지
> 다각도로 분석한다. 필드 레퍼런스는 `pipeline-architecture.md §8`을 참조.

---

## 1. 분석 프레임워크

### 1.1 데이터 분석가의 사고 흐름

기반 지식이 없는 데이터 분석가가 요청을 처리하는 과정:

```
① 요청 수령·해석
   "무엇을 원하는지" 파악, 모호하면 되묻기

② 초기 가설 수립
   요청에 드러난 정보로 접근 방식을 가정

③ 지식 탐색
   관련 테이블·컬럼·업무 규칙·과거 사례를 찾아감

④ 이해 심화·가설 수정
   찾은 정보를 바탕으로 이해를 갱신하고, 접근 방식을 수정

⑤ 확신 축적
   "이 테이블이 맞다", "이 조인이 맞다" 점진적으로 확정

⑥ 결론 도출·검증
   SQL 생성 → 실행 → 결과 검증 → 사용자에게 전달
```

### 1.2 현재 State가 각 단계를 어떻게 모델링하는가

| 사고 단계 | State 매핑 | 충분도 |
| --------- | ---------- | ------ |
| ① 요청 해석 | `user_input` → `preprocessed_input` → `intent` → `normalized_query` | **양호** |
| ② 초기 가설 | `hypotheses`, `query_decomposition`, `execution_plan` | **양호** |
| ③ 지식 탐색 | `knowledge_items`, `candidate_tables`, `structural_hints`, `explored_use_cases` | **양호** |
| ④ 이해 심화 | `KnowledgeItem.promote()`, `ConfidenceStatus` 전이 | **부분적** |
| ⑤ 확신 축적 | `confidence_scorer`, `LoopGuard` | **부분적** |
| ⑥ 결론·검증 | `generated_sql` → `validated_sql` → `sql_result` | **양호** |

**"양호"와 "부분적" 사이의 간극이 이 문서의 분석 대상이다.**

---

## 2. 구조적 분석 — 잘 설계된 부분

변경이 불필요한 부분을 먼저 확인하여 불필요한 리팩터링을 방지한다.

### 2.1 점진적 확신 모델 (ConfidenceStatus)

```
UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED
                                  ↘ CONFLICTED
```

데이터 분석가의 "처음엔 모르겠고 → 아마 이건가 → 거의 확실하고 → 확정" 흐름을 정확히 반영한다.
`CONFLICTED`를 통한 사용자 확인 루프도 실무 분석가의 "모호하면 묻는다" 행동과 일치.

### 2.2 실패 기록 (DeadEnd)

```python
class DeadEnd:
    hypothesis_id, reason, tried_tables[], rejected_tables[], failure_type
```

"이 방향은 안 됐다 → 같은 실수를 반복하지 않는다" 패턴을 명시적으로 모델링.
`recovery_planner`가 dead_ends를 참조하여 새 가설을 생성하는 구조가 이를 뒷받침한다.

### 2.3 루프 가드 (LoopGuard)

실무에서 "이 분석은 너무 오래 걸리니 여기서 멈추자"는 판단을 4종 카운터로 모델링.
`MAX_TOOL_CALLS=20`, `MAX_REPLANS=3` 등 하드 리밋은 프로덕션 안정성에 필수.

### 2.4 구조적 힌트 (StructuralHints)

과거 SQL에서 sqlglot으로 추출한 12가지 구조 정보는, 분석가가 "이전 보고서를 참고하는" 행위와 정확히 대응.
`to_prompt_text()` 메서드로 LLM 프롬프트에 바로 주입할 수 있는 설계도 실용적.

---

## 3. 구조적 분석 — 문제점

### 3.1 해석 모델이 불변(Immutable)

**현상:**

`normalized_query`(8-Slot)는 Interpret 계층에서 한 번 생성되면 Reason 계층에서 **수정되지 않는다**.
그러나 실제 분석가는 탐색 과정에서 요청의 의미를 재해석하는 경우가 빈번하다.

**예시:**

> 사용자: "이번 달 연체 현황 보여줘"
> → `normalized_query`에서 ENTITY="연체", MEASURE="현황"으로 추출
> → 탐색 중 "연체 현황"이 건별 원장이 아니라 월말 스냅샷 테이블의 집계 컬럼임을 발견
> → 분석가라면 "아, 이건 집계 테이블을 써야겠다"로 이해를 수정
> → 현재 state에서는 이 재해석을 기록할 곳이 없음

`knowledge_items`에 `key=understanding:*` 같은 항목으로 메모할 수는 있지만,
이는 해석 모델의 **구조적 변경이 아니라 사이드 메모**일 뿐이다.

**반론:**

`normalized_query` 불변이 의도적일 수 있다. 해석 모델을 탐색 중에 수정하면:
- 이전 탐색 결과의 전제가 무효화될 수 있음
- 무한 재해석 루프 위험
- 디버깅·재현이 어려워짐

현재 구조에서는 `recovery_planner`가 새 가설을 세우는 것으로 대체하고 있으며,
이것이 "재해석"의 실질적 효과를 내고 있다.

**권장:** 당장 변경하지 않되, Reason 계층이 "원래 해석을 이렇게 보정했다"를 기록하는
`interpretation_notes: list[str]` 같은 경량 필드를 ReasoningState에 추가하는 것을 검토.
이는 불변 원칙을 유지하면서 재해석 이력을 추적할 수 있다.

---

### 3.2 지식 구조가 플랫(Flat) — 관계를 표현하지 못함

**현상:**

`knowledge_items`는 `list[KnowledgeItem]`이며, 각 항목은 독립적인 key-value 쌍이다.

```python
KnowledgeItem(key="table:TB_LOAN", value="대출 원장", status="CONFIRMED")
KnowledgeItem(key="column:LOAN_AMT", value="대출 금액", status="PROBABLE")
KnowledgeItem(key="filter:STATUS_CD", value="정상=01", status="CANDIDATE")
```

그러나 실제 분석가의 지식은 관계형이다:
- "TB_LOAN 테이블의 LOAN_AMT 컬럼이 사용자가 원하는 '대출 금액'에 해당한다"
- "STATUS_CD='01'은 '정상' 상태를 의미하며, 이는 사용자가 말한 '정상 대출'의 필터 조건이다"

현재 구조에서는 이 관계를 **개별 항목의 key 네이밍 컨벤션**(`table:`, `column:`, `filter:`)에만 의존하여 암시한다.
노드 코드에서 `ki.key.startswith("table:")` 같은 문자열 파싱으로 관계를 추론한다.

**영향:**

1. `sql_generator`가 "어떤 테이블의 어떤 컬럼이 어떤 요구사항에 매핑되는지" 알려면
   knowledge_items를 순회하며 key 파싱 → 간접 매칭해야 함
2. `confidence_evaluator`가 "핵심 항목이 모두 해소되었는지" 판단할 때도 동일한 문제
3. `CandidateTable.relevant_columns`가 이 관계를 부분적으로 보완하지만,
   "이 컬럼이 요청의 어떤 슬롯에 매핑되는지"는 담지 못함

**반론:**

플랫 구조의 장점이 있다:
- 단순하여 LLM이 생성/수정하기 쉬움
- 새로운 유형의 지식을 key 접두사만 추가하면 됨 (스키마 변경 불필요)
- 그래프 구조로 전환하면 LLM 프롬프트에 주입하기 어려워짐

또한 `TableResolution.column_mapping`이 정확히 이 관계(need ↔ table.column)를 모델링하지만,
현재 `table_verifier`에서만 사용되고 sql_generator에는 전달되지 않는 상태.

**권장:** 기존 플랫 구조를 유지하되, `TableResolution.column_mapping`을 sql_generator가
적극 활용하도록 연결. 이것만으로 관계 표현의 핵심 갭이 해소된다. 별도의 그래프 구조 도입은 과잉.

---

### 3.3 가설에 확신도가 없음

**현상:**

```python
class Hypothesis:
    hypothesis_id, description, based_on_use_case,
    missing_terms[], priority, strategy,
    status: "PENDING" | "ACTIVE" | "SUCCESS" | "FAILED"
```

`Hypothesis`에는 **확신도(confidence) 필드가 없다**. `priority`가 가장 유사하지만,
이는 "어떤 가설을 먼저 시도할까"의 순서이지 "이 가설이 맞을 확률"이 아니다.

분석가라면 탐색이 진행되면서 "이 접근이 맞을 것 같다"는 확신이 점진적으로 높아지거나
낮아진다. 현재 구조에서는 이를 **ACTIVE → SUCCESS/FAILED 이진 결과**로만 표현한다.

**영향:**

- `confidence_evaluator`가 "전체 탐색의 준비도"를 산출하지만,
  이것이 현재 활성 가설의 적합도와 직접 연결되지 않음
- 탐색 중에 "이 가설은 50% 맞는 것 같은데 다른 가설도 병행해볼까"라는 판단 불가
- 현재는 하나의 가설이 완전히 실패해야만(`FAILED`) 다음 가설로 넘어감

**반론:**

가설별 확신도를 추적하면 복잡도가 크게 증가한다:
- 매 탐색 스텝마다 확신도를 갱신하는 로직 필요
- 소형 LLM이 확신도를 정확히 산출하기 어려움 (숫자 추정 약점)
- 현재의 순차적 가설 탐색이 단순하면서도 효과적

실제로 현재 `confidence_evaluator`의 3차원 점수(term_resolution, use_case_match, join_path)가
간접적으로 활성 가설의 유효성을 평가하고 있다.

**권장:** `Hypothesis`에 `confidence: float = 0.0` 필드를 추가하는 것은 저비용.
다만 이를 **자동 갱신하는 로직**은 rule-based로 구현 가능:
`(해당 가설의 knowledge_items 중 CONFIRMED 비율)` → 가설 confidence.
LLM에 의존하지 않으므로 소형 모델에서도 안정적.

---

### 3.4 검증 실패의 지식 환류(Feedback Loop) 부재

**현상:**

SQL 검증이 실패하면 `sql_fix_instruction: str`에 수정 지시가 담기고,
`sql_generator`가 이를 참조하여 SQL을 재생성한다.

그러나 검증 실패 정보가 **knowledge_items에 환류되지 않는다**.

**예시:**

> SQL 검증에서 "컬럼 LOAN_STATUS가 TB_LOAN에 존재하지 않음" 에러 발생
> → sql_fix_instruction에 "LOAN_STATUS 대신 STATUS_CD 사용" 기록
> → sql_generator가 수정
> → 그런데 knowledge_items에는 여전히 "filter:LOAN_STATUS = CANDIDATE" 상태로 남아 있음

분석가라면 "아, LOAN_STATUS가 아니라 STATUS_CD구나"라는 깨달음이 지식에 반영된다.
현재는 SQL 수정은 되지만 **지식 모델은 갱신되지 않는다**.

**영향:**

- REPLAN 시 recovery_planner가 knowledge_items를 참조하는데,
  검증 실패에서 얻은 교훈이 반영되지 않아 같은 실수를 반복할 수 있음
- `exploration_summary`에도 검증 실패에서 얻은 정보가 누락

**반론:**

sql_fix_instruction이 사실상 이 역할을 하고 있다.
sql_generator는 이전 SQL + fix_instruction을 함께 보내므로,
LLM이 컨텍스트에서 수정 사항을 이해한다.
또한 검증 실패 후 REPLAN으로 넘어가면 DeadEnd에 failure 이유가 기록된다.

**권장:** `sql_validator`에서 검증 실패 시 `knowledge_items`에 부정 지식을 추가하는 로직 구현.
예: `KnowledgeItem(key="negative:LOAN_STATUS", value="TB_LOAN에 존재하지 않음", status="CONFIRMED")`.
구현 비용이 낮고 recovery_planner의 판단 품질을 직접 개선할 수 있다.

---

### 3.5 Phase 전이가 실제 흐름과 불일치

**현상:**

```
PLANNING → EXPLORING → VERIFYING → GENERATING → VALIDATING → REPLANNING → DONE
```

이 7단계는 **순방향 선형 흐름**을 암시하지만, 실제 코드는 비선형으로 전이한다:

```python
# 실제 phase 전이 (코드에서 추출)
PLANNING → EXPLORING (일반)
PLANNING → GENERATING (fast-path)
EXPLORING → EXPLORING (반복 탐색)
EXPLORING → VERIFYING (confidence_evaluator가 설정)
EXPLORING → GENERATING (confidence_evaluator가 설정)
EXPLORING → REPLANNING (confidence_evaluator가 설정)
GENERATING → VALIDATING
GENERATING → VERIFYING (교차 DB 감지)
VALIDATING → GENERATING (수정 재시도)
REPLANNING → EXPLORING (새 가설)
REPLANNING → DONE (가설 소진)
```

`VERIFYING`은 `confidence_evaluator`와 `sql_generator`의 교차 DB 감지 **두 곳에서** 설정되어
의미가 모호하다. 또한 `REPLANNING → EXPLORING`은 사실상 "다시 처음부터"인데
Phase는 `REPLANNING`에서 `EXPLORING`으로 **역행**한다.

**반론:**

Phase는 "현재 어디쯤에 있는가"의 대략적 지표이지, 엄밀한 상태 머신이 아니다.
실제 라우팅은 `pipeline.py`의 conditional edges가 담당하며,
Phase는 주로 **로깅·진단 용도**이다. 엄격한 상태 머신이 필요하지 않을 수 있다.

**권장:** Phase의 역할을 "진단용 라벨(diagnostic label)"로 명확히 정의.
현재처럼 노드마다 자유롭게 설정하되, 라우팅 분기에는 Phase를 **사용하지 않는 것**을 원칙으로
유지 (현재도 라우팅은 Phase가 아닌 다른 조건으로 분기함). 다만 `result_finalizer`의
`state.reason.phase == "VERIFYING"` 체크는 Phase에 의존하는 유일한 라우팅인데,
이를 `reason.awaiting_clarification` 등 명시적 필드로 교체하는 것이 안전.

---

### 3.6 Interpret ↔ Reason 단절 — 정규화 후 재질문 시 지식 손실

**현상:**

```
normalize_query → ambiguities 발견 → clarify → END → (사용자 답변) → 처음부터 재시작
```

명확화 후 사용자가 답변하면 **파이프라인이 처음부터 재실행**된다.
이전 턴에서 수행한 정규화 결과는 새 턴에서 다시 수행된다.

이것 자체는 정상이지만, Reason 계층 탐색 중 명확화가 필요한 경우
(result_finalizer의 CONFLICTED 경로)에도 동일하게 **전체 Reason 상태가 리셋**된다.
탐색 과정에서 축적한 knowledge_items, candidate_tables 등이 모두 소실된다.

**영향:**

분석가라면 "이 테이블인지 저 테이블인지 확인"을 묻고 답변을 받으면,
이전에 탐색한 내용을 기반으로 이어서 작업한다.
현재 구조에서는 답변 후 모든 탐색을 처음부터 다시 수행해야 한다.

**반론:**

파이프라인 재실행 시 이전 턴의 conversation_history가 포함되므로,
history_resolver → intent_classifier → normalizer가 이전 맥락을 반영한 질의를 생성한다.
또한 Reason 계층의 Fast-Path가 이전에 발견한 structural_hints를 SQL 이력에서 재발견할 수 있다.
"처음부터지만 이전 대화 맥락이 있어서 더 빨리 도달"하는 구조.

**권장:** 현행 유지. Reason 상태를 세션에 캐싱하여 이어가는 구조는 복잡도가 매우 높고,
상태 일관성 보장이 어렵다. 현재의 "재실행 + 대화 맥락" 접근이 단순하면서도 실용적.
다만 **CONFLICTED로 인한 명확화 빈도가 높다면** 캐싱 전략을 재검토해야 한다.

---

### 3.7 "내가 무엇을 모르는지" 추적이 약함

**현상:**

분석가의 핵심 역량 중 하나는 "지금 나에게 무엇이 부족한지"를 인식하는 것이다.
현재 state에서 이를 추적하는 메커니즘:

| 메커니즘 | 추적 대상 | 한계 |
| -------- | --------- | ---- |
| `KnowledgeItem(status=UNRESOLVED)` | 미해소 용어 | ① |
| `Hypothesis.missing_terms[]` | 가설별 미해소 용어 | ② |
| `CandidateTable.missing_coverage[]` | 테이블의 미충족 요구사항 | ③ |
| `TableResolution.missing_info[]` | 테이블 해결 시 부족 정보 | ④ |

한계점:
- ① `UNRESOLVED` 항목이 있으면 "뭔가 모른다"는 알지만, **왜 모르는지** (검색 안 함 / 검색했으나 못 찾음 / 모호함) 구분 불가
- ② `missing_terms`는 가설 생성 시점에 고정되며 탐색 중 갱신되지 않음
- ③④ 단일 노드에서만 사용되어 크로스 노드 활용이 안 됨

**반론:**

`UNRESOLVED`의 이유를 구분하려면 상태 관리 복잡도가 급증한다.
현재 `confidence_evaluator`가 "UNRESOLVED 항목이 남아있으면 탐색 계속"이라는
단순 규칙으로 이를 충분히 대체하고 있다.

**권장:** `KnowledgeItem`에 `unresolved_reason: Literal["not_searched", "not_found", "ambiguous"] = "not_searched"`를
추가하면 저비용으로 구분 가능. `recovery_planner`가 "검색했는데 못 찾은 것"과
"아직 검색 안 한 것"을 구분하여 전략을 차별화할 수 있다.

---

## 4. 종합 평가

### 4.1 스코어카드

| 평가 차원 | 점수 | 설명 |
| --------- | ---- | ---- |
| 요청 해석 모델링 | ★★★★☆ | 8-Slot 정규화 + IntentType으로 잘 구조화. `Any` 타입 문제만 해결하면 됨 |
| 가설 기반 탐색 | ★★★★☆ | Hypothesis + ExecutionStep + DeadEnd 삼각 구조가 효과적. 가설별 확신도만 부재 |
| 점진적 지식 축적 | ★★★☆☆ | ConfidenceStatus 전이는 우수하나 관계 표현 부재, 검증 실패 환류 부재 |
| 확신 기반 판단 | ★★★★☆ | 3차원 readiness 점수 + LoopGuard 조합이 실용적 |
| 실패 회복 | ★★★★★ | DeadEnd + recovery_planner + REPLAN 루프가 매우 잘 설계됨 |
| 상태 타입 안전성 | ★★☆☆☆ | `Any`, untyped `dict` 다수. 런타임 방어 코드에 의존 |

### 4.2 전체 판정

현재 state 구조는 **데이터 분석가의 사고 흐름을 70~80% 수준으로 모델링**하고 있다.
특히 가설 기반 탐색, 실패 회복, 루프 제어가 잘 설계되어 있다.

주요 갭은 "지식의 관계 표현"과 "검증 → 지식 환류"인데,
이는 기존 구조를 **대규모로 변경하지 않고** 점진적으로 보완할 수 있다.

---

## 5. 최종 권고

### 5.1 즉시 실행 가능 (Low-Effort, High-Impact)

| # | 권고 | 변경 범위 | 기대 효과 |
| - | ---- | --------- | --------- |
| R1 | `normalized_query: Any` → `Optional[NormalizedQuery]` | state.py + import | 전체 코드베이스의 방어적 `hasattr()` 제거 가능 |
| R2 | `cache_refs` 삭제 | state.py 1줄 | 데드 코드 정리 |
| R3 | `sql_validator`에서 검증 실패 시 부정 지식을 `knowledge_items`에 추가 | sql_validator.py | recovery_planner 판단 품질 향상 |

### 5.2 중기 개선 (Medium-Effort)

| # | 권고 | 변경 범위 | 기대 효과 |
| - | ---- | --------- | --------- |
| R4 | `Hypothesis`에 `confidence: float` 추가 + rule-based 자동 갱신 | state.py + confidence_evaluator | 가설별 적합도 추적, 병행 탐색 판단 근거 |
| R5 | `KnowledgeItem`에 `unresolved_reason` 추가 | state.py + planner + explorer | "못 찾음" vs "안 찾음" 구분 → 전략 차별화 |
| R6 | `TableResolution`을 sql_generator가 활용하도록 연결 | sql_generator.py | 이미 검증된 컬럼 매핑을 SQL 생성에 직접 활용 |
| R7 | `query_decomposition`, `explored_use_cases`, `confirmed_join_path`를 TypedDict 또는 Pydantic으로 정형화 | state.py + 관련 노드 | 타입 안전성 + 자동 완성 + 문서화 |

### 5.3 장기 검토 (High-Effort, 신중히 판단)

| # | 권고 | 리스크 | 판단 기준 |
| - | ---- | ------ | --------- |
| R8 | `normalized_query`를 Reason에서 보정 가능하게 변경 | 무한 재해석 루프, 디버깅 복잡도 | 현재 recovery_planner가 충분히 대체하는지 평가 후 결정 |
| R9 | Reason 상태 세션 캐싱 (CONFLICTED 명확화 후 이어가기) | 상태 일관성, 캐시 무효화 복잡도 | CONFLICTED 빈도가 전체 질의의 10% 이상일 때 검토 |
| R10 | knowledge_items 관계형 구조 전환 | LLM 프롬프트 주입 복잡도, 전체 노드 수정 | Neo4j 온톨로지 통합 시점에 함께 검토 |

---

## 6. 부록: 현재 State가 커버하지 못하는 분석가 행동

참고용으로 기록한다. 현재 구현에서 반드시 필요한 것은 아니지만,
향후 고도화 시 설계 방향을 제시한다.

| 분석가 행동 | 현재 State | 갭 |
| ----------- | ---------- | --- |
| "이건 아까 본 것과 비슷한데…" (유추) | structural_hints가 부분 대체 | 테이블 간 유사성 메모리 없음 |
| "이 두 테이블의 차이가 뭐지?" (비교 추론) | table_comparison 프롬프트로 처리 | 비교 결과가 knowledge에 구조적으로 저장되지 않음 |
| "일단 대충 뽑아보고 맞는지 확인하자" (탐색적 실행) | Layer 3 검증 (LIMIT 5 실행)이 유사 | 탐색적 실행 결과를 지식으로 환류하지 않음 |
| "이 숫자가 상식적으로 맞나?" (결과 상식 검증) | `layer3_is_sane` 필드 존재 | 실제 상식 검증 로직 미구현 |
| "고객이 원한 게 이게 맞나?" (최종 확인) | 없음 | 생성된 SQL과 원래 요청의 정합성을 검증하는 단계 부재 |

---

## 7. 파이프라인 정보 중복 분석

하나의 사실(fact)이 파이프라인을 거치며 여러 필드에 형태만 다르게 복제되는 현상을 추적한다.
중복 자체가 항상 문제는 아니지만, **LLM 프롬프트에 같은 정보가 다른 형태로 다중 주입되면
소형 모델에서 불일치 해석, 토큰 낭비, 환각 유발**의 원인이 된다.

### 7.1 추적 방법론

예시 질의 **"지점별 이번 달 신규 고객 수 알려줘"**를 기준으로,
핵심 사실 4가지가 파이프라인 각 노드를 거치며 어떤 state 필드에 어떤 형태로 저장되는지 추적한다.

### 7.2 사실별 중복 추적

#### 사실 A: "고객 수를 COUNT 해야 한다"

| # | 생성 노드 | 저장 필드 | 저장 형태 | 새 정보? |
| - | --------- | --------- | --------- | -------- |
| A1 | `query_normalizer` | `normalized_query.measures[0]` | `{term: "고객 수", agg_function: "COUNT"}` | **원본** |
| A2 | `planner` | `query_decomposition.measures[0]` | `{term: "고객 수", agg_function: "COUNT"}` | 아니오 — A1의 dict 복사 |
| A3 | `planner` | `knowledge_items` | `key="measure:고객 수", status=UNRESOLVED` | 부분적 — 탐색 상태 추적 목적 |
| A4 | `context_explorer` | `knowledge_items` 승격 | `key="column:CUST_NO", value="COUNT 대상", status=CONFIRMED` | **예** — 물리 컬럼 매핑 발견 |
| A5 | `table_verifier` | `table_resolution.column_mapping` | `need="고객 수(COUNT)", table="TB_ADW_CSC101M", column="CUST_NO"` | **예** — need↔table.column 관계 |
| A6 | `planner` (초기 컨텍스트) | `structural_hints.agg_expressions` | `"COUNT(*)"` | 부분적 — 과거 SQL 패턴 |
| A7 | `planner` (초기 컨텍스트) | 활용사례 SQL 원본 | `"SELECT COUNT(*) AS 고객수 FROM TB_ADW_CSC101M ..."` | **예** — 원본 SQL |

**sql_generator 프롬프트에 실제로 주입되는 형태:**

```
② 질의 분해 → measures: [{"term": "고객 수", "agg_function": "COUNT"}]     ← A2
③ confirmed_terms → "- column:CUST_NO: 고객번호 — COUNT 대상 (batch_interpret)"  ← A4
④ 사용할 테이블 → "TB_ADW_CSC101M 컬럼: CUST_NO, CUST_NM, ..."           ← 간접
⑥ 구조적 힌트 → "유사 질의 집계 방식: COUNT(*)"                            ← A6
⑦ 활용사례 SQL → "SELECT COUNT(*) AS 고객수 FROM TB_ADW_CSC101M ..."      ← A7
```

LLM은 이 5곳에서 "COUNT"라는 동일 사실을 만나며, **각각의 형태에서 독립적으로 해석**해야 한다.
A2에서는 "고객 수"라는 추상 용어, A4에서는 "CUST_NO"라는 물리 컬럼, A6에서는 `COUNT(*)`라는 SQL 함수.
소형 모델이 이 셋이 같은 것을 가리킨다고 정확히 매칭할 수 있을까?

#### 사실 B: "TB_ADW_CSC101M 테이블을 쓴다"

| # | 생성 노드 | 저장 필드 | 저장 형태 | 새 정보? |
| - | --------- | --------- | --------- | -------- |
| B1 | `context_explorer` | `candidate_tables[0]` | `CandidateTable(table_name=..., relevant_columns=[...], sample_rows=[...])` | **원본** |
| B2 | `context_explorer` | `knowledge_items` | `key="table:TB_ADW_CSC101M", value="고객마스터", status=CONFIRMED` | 아니오 — B1의 요약 |
| B3 | `table_verifier` | `table_resolution.column_mapping[].table` | `"TB_ADW_CSC101M"` | 아니오 — B1에서 파생 |
| B4 | `planner` (초기 컨텍스트) | `structural_hints.source_tables` | `["TB_ADW_CSC101M"]` | 부분적 — 과거 SQL에서 발견 |
| B5 | `result_finalizer` | `context.table_metas` | `TableMeta(table_name=..., columns=[...])` | 아니오 — B1의 재포맷 |

**같은 테이블명이 5가지 필드에 존재.** B2는 B1의 존재 자체를 key-value로 재기록한 것이고,
B5는 B1을 Present 계층용으로 다시 변환한 것이다.

#### 사실 C: "BLNG_BRCD로 GROUP BY 한다"

| # | 생성 노드 | 저장 필드 | 저장 형태 | 새 정보? |
| - | --------- | --------- | --------- | -------- |
| C1 | `query_normalizer` | `normalized_query.dimensions[0]` | `{term: "지점", role: "GROUP"}` | **원본** (추상 용어) |
| C2 | `planner` | `query_decomposition.group_by` | `["지점"]` | 아니오 — C1의 term 추출 |
| C3 | `context_explorer` | `knowledge_items` | `key="column:BLNG_BRCD", value="GROUP BY 대상"` | **예** — 물리 컬럼 발견 |
| C4 | `table_verifier` | `column_mapping` | `need="지점(GROUP BY)", column="BLNG_BRCD"` | 부분적 — C1↔C3 관계 |
| C5 | `planner` (초기 컨텍스트) | `structural_hints.group_by_columns` | `["BR_NM"]` | **주의** — 다른 컬럼! |

**C5에서 불일치 발생.** 과거 SQL에서는 `BR_NM`(지점명)으로 GROUP BY 했는데,
현재 탐색에서는 `BLNG_BRCD`(지점코드)를 발견했다. 둘 다 "지점"이지만 다른 컬럼이다.
sql_generator LLM은 이 불일치를 스스로 해소해야 한다.

#### 사실 D: "조인 경로 BLNG_BRCD = BRCD"

| # | 생성 노드 | 저장 필드 | 저장 형태 | 새 정보? |
| - | --------- | --------- | --------- | -------- |
| D1 | `table_verifier` | `table_resolution.join_path` | `"TB_ADW_CSC101M.BLNG_BRCD = TB_ADW_COM001M.BRCD"` | **원본** |
| D2 | `table_verifier` | `confirmed_join_path` | `[{"path": "TB_ADW_CSC101M.BLNG_BRCD = TB_ADW_COM001M.BRCD"}]` | 아니오 — D1의 즉시 복사 |
| D3 | `planner` (초기 컨텍스트) | `structural_hints.join_patterns` | `["CSC101M.BLNG_BRCD = COM001M.BRCD"]` | 부분적 — 과거 SQL 패턴 |
| D4 | 활용사례 SQL 원본 | `reference_sqls` | `"... INNER JOIN TB_ADW_COM001M B ON A.BLNG_BRCD = B.BRCD"` | **예** — 원본 SQL |

**D1→D2는 같은 노드(table_verifier) 안에서의 즉시 복제.** 두 필드가 항상 같은 값을 가지므로
한쪽은 불필요하다.

### 7.3 중복 유형 분류

추적 결과를 유형별로 정리한다.

#### 유형 1: 구조 변환 복제 (불필요)

원본의 구조만 바꾸어 다른 필드에 저장. **새로운 정보가 추가되지 않음.**

| 원본 | 복제본 | 변환 내용 |
| ---- | ------ | --------- |
| `normalized_query.measures` | `query_decomposition.measures` | Pydantic → dict 변환 |
| `normalized_query.dimensions` (role=GROUP) | `query_decomposition.group_by` | term만 추출 |
| `normalized_query.filters` | `query_decomposition.filters` | Pydantic → dict 변환 |
| `normalized_query.modifiers` | `query_decomposition.order_limit` | Pydantic → dict 변환 |
| `table_resolution.join_path` | `confirmed_join_path` | str → list[dict] 래핑 |
| `candidate_tables` | `knowledge_items (table:*)` | 존재 사실을 key-value로 재기록 |
| `candidate_tables` | `context.table_metas` (Present) | CandidateTable → TableMeta 변환 |

**`query_decomposition`은 `normalized_query`의 순수 구조 변환이며 planner에서 생성된다.**
planner 코드(`_build_decomposition_from_normalized`, L151-199)를 보면:

```python
# normalized_query에서 추출하는 전부:
measures = [{term, agg_function} for m in nq.measures]
filters = [{term, operator, value} for f in nq.filters]
group_by = [d.term for d in nq.dimensions if d.role == "GROUP"]
order_limit = [{type, value} for mod in nq.modifiers]
```

이후 sql_generator(L170), sql_validator(L263, L313)가 `query_decomposition`을 읽는데,
`normalized_query`를 직접 읽어도 동일한 정보를 얻을 수 있다.

#### 유형 2: 정보 보강 복제 (정당)

원본에 **새로운 정보가 추가**되어 다른 필드에 저장. 중복이지만 정당하다.

| 원본 | 보강본 | 추가된 정보 |
| ---- | ------ | ---------- |
| `normalized_query.measures` (추상 용어) | `knowledge_items (column:*)` | 물리 컬럼명 + 확신도 |
| `knowledge_items (CONFIRMED)` | `table_resolution.column_mapping` | need↔table.column 관계 |
| `structural_hints` (패턴) | `reference_sqls` (원본) | 맥락이 보존된 전체 SQL |

이 유형은 탐색의 자연스러운 결과이며 제거 대상이 아니다.

#### 유형 3: 외부 소스 중복 (부분 정당)

동일 사실을 **다른 소스**에서 독립적으로 발견하여 저장.

| 소스 1 | 소스 2 | 불일치 위험 |
| ------ | ------ | ---------- |
| `knowledge_items`: column:BLNG_BRCD=GROUP BY | `structural_hints.group_by_columns`: BR_NM | **높음** — 같은 "지점"인데 다른 컬럼 |
| `table_resolution.join_path` | `structural_hints.join_patterns` | 낮음 — 같은 패턴이지만 축약 정도 차이 |

이 유형은 **불일치가 발생하면 LLM이 혼란**스럽다.
특히 사실 C의 `BLNG_BRCD` vs `BR_NM` 불일치는 sql_generator가 어느 쪽을 선택할지
프롬프트에 명시적 우선순위 규칙이 없으므로 실행마다 결과가 달라질 수 있다.

### 7.4 비효율 영향 분석

#### 영향 1: sql_generator 프롬프트 토큰 낭비

현재 sql_generator 프롬프트에 주입되는 10개 섹션의 정보 성격을 분류한다.

```
[프롬프트 구성 현황]

① 사용자 질의                 ── 원문 (고유)
② 질의 분해                   ── normalized_query의 복사본 (유형 1 중복)
③ confirmed_terms              ── knowledge_items 텍스트화 (고유, 단 컬럼 매핑은 ⑤와 중복)
④ 사용할 테이블 (컬럼+샘플)     ── candidate_tables 텍스트화 (고유)
⑤ 조인 경로                   ── confirmed_join_path (유형 1 중복: table_resolution과 동일)
⑥ 구조적 힌트                 ── structural_hints (고유, 단 유형 3 불일치 위험)
⑦ 활용사례 SQL                ── reference_sqls (고유)
⑧ 실패한 접근 방식             ── dead_ends (고유)
⑨ fix_section                 ── 재시도 시만 (고유)
⑩ SQL 문법 규칙               ── 정적 규칙 (고유)
```

10개 중 **②와 ⑤가 순수 중복**, **③의 컬럼 관련 부분이 ④와 부분 중복**, **⑥이 ③⑤⑦과 교차 중복**.
추정 토큰 절감: 전체 프롬프트의 15~20% (②⑤ 제거 + ③ 간소화 시).

#### 영향 2: 소형 모델의 교차 대조 부담

"CUST_NO를 COUNT한다"는 하나의 사실을 sql_generator LLM이 인식하려면:

```
AS-IS:
  1. ②에서 measures.agg_function=COUNT를 확인
  2. ③에서 "column:CUST_NO: COUNT 대상"을 발견
  3. ④에서 TB_ADW_CSC101M의 컬럼 목록에 CUST_NO가 있는지 확인
  4. ⑥에서 "유사 질의 집계: COUNT(*)"를 참고
  5. 1~4를 통합하여 "SELECT COUNT(CUST_NO) FROM TB_ADW_CSC101M"을 도출

  → LLM이 5단계 교차 대조를 수행해야 함
```

대형 모델(Claude, GPT-4)은 이를 잘 처리하지만,
소형 모델(7B~70B)에서는 **2~3단계를 건너뛰고** 구조적 힌트(⑥)의 `COUNT(*)`만 참고하여
`COUNT(*)` 대신 `COUNT(CUST_NO)`를 놓치거나, 반대로 confirmed_terms(③)만 보고
structural_hints(⑥)의 패턴을 무시하는 등 **정보원 간 선택 편향**이 발생한다.

#### 영향 3: 불일치 시 환각 유발

사실 C에서 보았듯이 `structural_hints.group_by_columns`에 `BR_NM`이 있고
`knowledge_items`에 `BLNG_BRCD`가 있으면, sql_generator는 다음 중 하나를 선택해야 한다:

```
옵션 1: GROUP BY A.BLNG_BRCD         ← knowledge_items 기반 (탐색 결과)
옵션 2: GROUP BY B.BR_NM             ← structural_hints 기반 (과거 SQL 패턴)
옵션 3: GROUP BY A.BLNG_BRCD, B.BR_NM  ← 둘 다 사용 (가장 안전)
```

프롬프트에 "불일치 시 어느 소스를 우선하라"는 규칙이 없으므로,
LLM이 실행마다 다른 옵션을 선택할 수 있다 (비결정적 동작).
실제로는 옵션 3이 정답에 가장 가깝지만, 이를 보장하는 메커니즘이 없다.

### 7.5 중복 발생의 구조적 원인

```
normalized_query (8-Slot, Interpret 계층 산출물)
    │
    ├─→ planner: query_decomposition으로 재포맷 ─────── 구조 변환 복제
    │       │
    │       └─→ knowledge_items (UNRESOLVED)로 재포맷 ── 정보 보강 복제 (정당)
    │
    ├─→ context_explorer: knowledge_items 승격 ────────── 정보 보강 (정당)
    │
    ├─→ table_verifier: column_mapping + join_path ─── 정보 보강 (정당)
    │       │
    │       └─→ confirmed_join_path로 즉시 복사 ──────── 구조 변환 복제
    │
    └─→ sql_generator: 전체를 프롬프트에 다중 주입 ──── 중복 노출의 최종 지점
```

**원인:** 각 노드가 **자신이 필요한 형태로 state를 독립적으로 재가공**하여 저장한다.
노드 간 "이미 같은 정보가 다른 필드에 있다"는 인식이 설계에 반영되지 않았다.
이는 노드별 독립성을 극대화한 설계의 부작용이며,
노드가 state 전체를 알 필요 없이 자신의 입출력만 다루도록 한 LangGraph의 철학과도 관련된다.

---

## 8. 통합 설계 권고

### 8.1 권고 R11: `query_decomposition` 제거 → `normalized_query` 직접 참조

**현상:**

`planner._build_decomposition_from_normalized()`이 `normalized_query`의 슬롯을
dict로 복사하여 `query_decomposition`에 저장. 이후 sql_generator, sql_validator가 사용.

**변환 내용 (새 정보 없음):**

```python
# planner.py L151-199 — normalized_query → query_decomposition
measures = [{term, agg_function} for m in nq.measures]   # 그대로 복사
filters = [{term, operator, value} for f in nq.filters]  # 그대로 복사
group_by = [d.term for d in nq.dimensions if d.role == "GROUP"]  # term만 추출
order_limit = [{type, value} for mod in nq.modifiers]    # 그대로 복사
```

**통합안:**

sql_generator와 sql_validator가 `state.normalized_query`를 직접 참조.
`query_decomposition` 필드와 `_build_decomposition_from_normalized()` 함수 삭제.

**선행 조건:** `normalized_query: Any` → `Optional[NormalizedQuery]` 타입 수정 (R1).
타입이 `Any`인 상태에서 직접 참조하면 방어 코드가 오히려 늘어난다.

**영향 파일:** `state.py`, `planner.py`, `sql_generator.py`, `sql_validator.py`, `context_explorer.py`

**비판적 검토:**

- **찬성:** 순수 중복 제거. 정보의 단일 원천(single source of truth) 확보. state 필드 1개 감소.
- **반대:** `query_decomposition`은 sql_validator의 Layer 2a에서 rule-based 검증에 사용된다(`GROUP BY 존재 여부` 등). `normalized_query`의 Pydantic 모델에서 같은 정보를 추출하는 것이 dict 접근보다 약간 번거로울 수 있다. 다만 `normalized_query`가 타입화되면 이 차이는 무시할 수준.
- **최종:** R1(타입 수정) 이후에 실행하면 리스크 낮음. **권장.**

---

### 8.2 권고 R12: `confirmed_join_path` 제거 → `table_resolution` 직접 참조

**현상:**

table_verifier가 `table_resolution.join_path`를 생성한 즉시
같은 값을 `confirmed_join_path`에 복사 (L118-121):

```python
if resolution.join_needed and resolution.join_path:
    reason.confirmed_join_path = [{"path": resolution.join_path}]
```

sql_generator는 `confirmed_join_path`만 읽음.

**통합안:**

sql_generator가 `reason.table_resolution.join_path`를 직접 참조.
`confirmed_join_path` 필드 삭제.

**영향 파일:** `state.py`, `table_verifier.py`, `sql_generator.py`, `confidence_evaluator.py`

**비판적 검토:**

- **찬성:** 같은 노드 안에서 즉시 복제되는 전형적 중복. `table_resolution`이 join 관련 정보의 단일 원천이 되어 일관성 향상.
- **반대:** `confirmed_join_path`는 "join이 확인되었다"는 상태 신호 역할도 한다. `table_resolution`이 None일 수 있으므로 접근 시 null 체크가 추가된다. 또한 `confidence_evaluator`가 `confirmed_join_path` 존재 여부로 join 점수를 계산하는데, `table_resolution.join_needed`로 대체해야 한다.
- **최종:** 변경 범위가 작고 의미적으로 명확. **권장.**

---

### 8.3 권고 R13: sql_generator 프롬프트 정보 재편 — `column_mapping` 중심 구조

**현상:**

sql_generator에 10개 섹션이 주입되며, "고객 수를 COUNT한다"는 사실이 5곳에서 다른 형태로 등장.
소형 모델은 이 정보를 교차 대조하여 통합 해석하기 어려움.

**재편안:**

```text
AS-IS (10섹션)                          TO-BE (7섹션)
───────────────                        ───────────────
① 사용자 질의                           ① 사용자 질의 (원문)
② 질의 분해                             (삭제 — R11로 불필요)
③ confirmed_terms                       ② 컬럼 매핑 (column_mapping 테이블)
④ 사용할 테이블 (컬럼+샘플)              ③ 보조 지식 (코드값·날짜 조건만, 매핑과 미중복)
⑤ 조인 경로                             (삭제 — R12로 column_mapping에 흡수)
⑥ 구조적 힌트                           ④ 구조적 힌트 (참고용, 우선순위 명시)
⑦ 활용사례 SQL                          ⑤ 활용사례 SQL (구조 템플릿)
⑧ 실패한 접근 방식                       ⑥ 실패한 접근 방식 (재시도 시만)
⑨ fix_section                          ⑦ SQL 문법 규칙
⑩ SQL 문법 규칙
```

**TO-BE의 핵심 변화 — ② 컬럼 매핑:**

```markdown
## 컬럼 매핑 (검증 완료)

| 필요 정보 | 테이블 | 컬럼 | 확신도 | 조인 |
|-----------|--------|------|--------|------|
| 고객 수(COUNT) | TB_ADW_CSC101M | CUST_NO | 확실 | — |
| 지점(GROUP BY) | TB_ADW_CSC101M | BLNG_BRCD | 확실 | — |
| 지점명(표시용) | TB_ADW_COM001M | BR_NM | 확실 | BLNG_BRCD = BRCD |
| 기간 조건(신규) | TB_ADW_CSC101M | RGST_DT | 확실 | — |
```

이 테이블 하나로 LLM은:

- **무엇을**(CUST_NO) **어떻게**(COUNT) 집계하는지
- **어디서**(TB_ADW_CSC101M) 가져오는지
- **어떻게 연결**(BLNG_BRCD = BRCD) 하는지
- **어떤 조건**(RGST_DT)으로 필터링하는지

를 **교차 대조 없이 한 번에** 파악한다.

**TO-BE의 ③ 보조 지식 — column_mapping에 담을 수 없는 것만:**

```markdown
## 보조 지식

- code:CUST_STAT_CD → 01=정상, 02=해지, 03=휴면
- date:기준일자 형식 → 'YYYYMMDD' (char8)
```

**TO-BE의 ④ 구조적 힌트 — 우선순위 규칙 추가:**

```markdown
## 구조적 힌트 (참고용)

(기존 내용 유지)

※ 컬럼 매핑과 구조적 힌트가 불일치하면 컬럼 매핑을 우선하세요.
  컬럼 매핑은 현재 탐색에서 검증된 결과이고, 구조적 힌트는 과거 SQL의 패턴입니다.
```

**비판적 검토:**

- **찬성 (강력):**
  - 정보 중복 60% 이상 제거 (②③⑤ 통합)
  - 소형 모델의 교차 대조 부담 대폭 감소
  - 불일치 시 우선순위 명시로 비결정적 동작 방지
  - column_mapping이 "검증 완료"이므로 환각 리스크 감소

- **반대 (신중):**
  - `table_verifier`가 column_mapping을 정확히 생성하지 못하면 오히려 오류가 증폭됨 (잘못된 매핑이 그대로 SQL에 반영). 현재는 LLM이 여러 소스에서 교차 검증하므로 하나가 틀려도 다른 소스에서 보정 가능
  - column_mapping이 없는 경우(table_verifier 스킵/실패 시)의 폴백 처리 필요
  - 프롬프트 전면 재설계이므로 기존 품질 기준선(baseline)과의 A/B 비교 테스트 필수

- **완화 전략:**
  - `column_mapping`이 있으면 재편된 TO-BE 프롬프트, 없으면 기존 AS-IS 프롬프트를 선택하는 **이중 경로** 구현
  - column_mapping의 `confidence="추정"` 항목은 "검증 필요" 주석을 달아 LLM이 독자 판단하도록 유도
  - A/B 테스트로 품질 검증 후 전환

- **최종:** R11, R12가 선행된 후 실행. 이중 경로로 안전하게 전환. **조건부 권장.**

---

### 8.4 권고 R14: `table_verifier` 출력에서 `main_table` 삭제

**현상:**

`main_table` 필드는 LLM이 생성하지만 코드 어디서도 읽지 않는 Dead Output.
프롬프트 토큰을 소비하고 LLM의 출력 부담만 늘린다.

**통합안:** 프롬프트 출력 스키마, `_parse_resolution()`, `TableResolution` 모델에서 제거.

**비판적 검토:**

- **찬성:** 순수 토큰 절약. 영향 범위 최소 (table_verifier.py + 프롬프트만).
- **반대:** column_mapping 중심 재편(R13)에서 FROM 절의 주 테이블을 결정하는 데 유용할 수 있다. 하지만 column_mapping에서 가장 많은 컬럼을 매핑한 테이블이 사실상 main_table이므로 별도 필드 불필요.
- **최종:** 즉시 실행. **강력 권장.**

---

### 8.5 실행 순서 및 의존 관계

```text
R14 (main_table 삭제) ──────────────── 독립, 즉시 실행 가능
R1  (normalized_query 타입 수정) ──── 독립, 즉시 실행 가능
R2  (cache_refs 삭제) ──────────────── 독립, 즉시 실행 가능
R12 (confirmed_join_path 통합) ────── 독립, 즉시 실행 가능
        │
        ├── R1 완료 후
        │       │
        │       └── R11 (query_decomposition 제거) ── R1 선행 필요
        │
        └── R11 + R12 완료 후
                │
                └── R13 (sql_generator 프롬프트 재편) ── R11, R12 선행 필요
```

| Phase | 권고 | 리스크 | 예상 효과 |
| ----- | ---- | ------ | --------- |
| **즉시** | R14, R1, R2, R12 | 낮음 | Dead code 정리 + 타입 안전성 + 즉시 중복 1건 제거 |
| **단기** | R11 | 중간 | state 필드 1개 + 변환 함수 제거, 정보 단일 원천 확보 |
| **중기** | R13 | 높음 (A/B 테스트 필요) | 프롬프트 토큰 15~20% 절감, 소형 모델 정확도 향상 |
