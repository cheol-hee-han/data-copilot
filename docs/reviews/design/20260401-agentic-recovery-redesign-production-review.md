# 프로덕션 환경 기반 재검토: Agentic Recovery Loop 재설계

- **검토 대상**: 교차 검토(`20260401-agentic-recovery-redesign-cross-review.md`) + 원안(`01-strategy.md`, `02-detailed-design.md`) + 리뷰(`20260401-agentic-recovery-redesign-review.md`)
- **검토일**: 2026-04-01
- **검토 관점**: 실제 프로덕션 환경(참조 저장소 제약, LLM 능력, 사용자 질의 패턴)에서의 **정확도 실효성** 중심
- **코드 검증 기준**: `main` 브랜치 (`6491f9b`) — 코드베이스 직접 대조 완료

---

## 총평

교차 검토 문서는 원안과 리뷰를 균형 있게 비교하고, 코드 직접 검증과 학술 근거를 포함하여 완성도가 높다. P0 4건, P1 9건, P2 7건의 통합 우선순위도 합리적이다. **그러나 프로덕션 환경의 세 가지 핵심 제약을 충분히 반영하지 못한 부분이 있다:**

1. **참조 저장소의 실질적 한계** — Qdrant의 상품설명서/업무매뉴얼은 SQL 추론에 직접적 힌트가 아님, MongoDB 비즈용어사전은 200개 미만으로 부실, 보고서 SQL/골든셋은 아직 없음
2. **70B~397B LLM의 ReAct 능력 한계** — 파인튜닝 없는 상태에서 복잡한 구조화 출력의 안정성
3. **함축적 사용자 질의 + "선 추론 후 표시" 정책** — 대부분 명확화 질문 없이 추론으로 진행해야 함

이 세 가지를 감안할 때, 교차 검토의 20건 중 **14건은 그대로 유효**, **3건은 우선순위 조정 필요**, **3건은 프로덕션 환경에 맞게 보완/재설계 필요**하며, **교차 검토가 놓친 신규 이슈 5건**을 추가로 제기한다.

**심각도 분포**: 신규 P0 1건 / 신규 P1 3건 / 신규 P2 1건 / 기존 항목 조정 3건

---

## A. 기존 교차 검토 항목 재평가

### A-1. 그대로 유효한 항목 (14건) — 변경 불필요

| # | 교차 검토 항목 | 재평가 근거 |
|---|--------------|-----------|
| 1 | P0-1: give_up 무한 루프 차단 | 환경 불문 치명적. 70B에서 give_up 빈도가 더 높으므로 오히려 중요도 증가 |
| 2 | P0-2: exploration_phase 리셋 | 멀티턴은 프로덕션 필수 시나리오 |
| 3 | P0-3: execute_tool 어댑터 형식 불일치 | 코드 직접 검증 완료. tools.py의 쉼표 파싱은 확정 |
| 4 | P0-4(교차2-3): decision=None 즉시 종료 | 70B에서 파싱 실패율이 높아 None 도달 확률 증가 |
| 5 | P1-1(교차4-4): last_verdict 필드 | 라우팅 명확성은 환경 불문 |
| 6 | P1-2(교차4-5): 부분 일치 복수 매칭 | 금융 도메인 접미어 공유("~일자", "~금액") 문제는 프로덕션에서 더 빈번 |
| 7 | P1-3(교차4-6): PROMOTION_ORDER CANDIDATE | 코드 검증 완료 (enums.py:72 확인) |
| 8 | P1-4(교차4-7): discovered_facts 경로 | sql_generator 프롬프트 연계 필수 |
| 9 | P1-9(교차2-1): 프롬프트 CANDIDATE 포함 | 중복 탐색 방지에 필수 |
| 10 | P1-10(교차2-2): KnowledgeUpdate CANDIDATE | 시스템 일관성 |
| 11 | P1-11(교차2-4): search_use_cases 제거 | 도달 불가능 코드 제거 |
| 12 | P2-17(교차5-5): force-generate 임계값 통일 | confidence_scorer.py에서 0.55 확인, 문서 통일 필요 |
| 13 | P2-18(교차2-5): recovery_rounds 의미 명확화 | 디버깅 용도 |
| 14 | P2-19(교차4-8): EXPLORE 재진입 가드 | 방어적 프로그래밍 |

---

### A-2. 우선순위 조정이 필요한 항목 (3건)

#### A-2-1. P1→**P0 격상**: 도구 실행 병렬화 (교차 5-3, 원 P2-3)

**교차 검토**: P1 12번으로 배치, "Latency-Aware Orchestration (1.6x)" 근거.

**프로덕션 재평가**: 단순한 지연 개선이 아니라, **70B 모델의 ReAct 라운드 수를 줄이는 핵심 수단**.

- 70B 모델에서 ReAct 라운드가 길어질수록 "조기 ready" 또는 "무한 call_tools" 위험이 급증 (AgentBench TLE 패턴)
- 한 라운드에서 3~4개 도구를 병렬 실행하면 **동일 정보를 더 적은 라운드에서 수집** → LLM 판단 오류 기회 자체가 감소
- 프로덕션 시나리오: "지점별 여신 실행 금액" → search_table_meta("지점") + search_table_meta("여신") + search_code_meta("branch_type_cd")를 1라운드에서 병렬 처리하면 2-3라운드 절약

**결론**: P0에 격상. recovery_agent의 정확도에 직접적 영향.

#### A-2-2. P2→**P1 격상**: 진전 감지 메커니즘 (교차 5-1)

**교차 검토**: P2 14번, "2회 연속 변화 없으면 조기 종료".

**프로덕션 재평가**: **70B 모델 + 부실한 메타데이터** 조합에서는 "도구를 호출했지만 유용한 정보를 못 찾는" 케이스가 빈번할 것.

- MongoDB 비즈용어사전이 200개 미만 → `search_glossary` 결과가 빈번히 빈 결과
- 보고서 SQL 저장소가 없음 → 참조할 유사 쿼리가 제한적
- 이 상황에서 진전 없이 5라운드까지 도는 것은 **LLM 비용 + 응답 시간 모두에 치명적**
- 70B 모델 기준 라운드당 3-15초 → 진전 없는 5라운드 = 최대 75초 낭비

**결론**: P1으로 격상. 불완전한 메타 환경에서의 비용 효율에 직접적 영향.

#### A-2-3. P2→**P1 격상**: fallback 파싱 안전성 (교차 5-4)

**교차 검토**: P2 16번, "call_tools인데 tool_calls 없으면 give_up 전환".

**프로덕션 재평가**: 파인튜닝 없는 70B 모델에서 JSON 파싱 실패는 예외가 아니라 **정상 운영 시나리오**.

- StructEval 기준 OSS 모델의 구조화 출력 준수율이 상용 대비 ~10점 낮음
- Solar Pro 2 70B는 JSON 출력 안정성이 검증되지 않은 상태
- 파싱 실패 시의 안전한 복구가 없으면 silent failure → 사용자에게 무응답 또는 오류

**결론**: P1으로 격상. 프로덕션 안정성의 기본 요건.

---

## B. 프로덕션 환경에서 발견한 신규 이슈 (5건)

### B-1. [P0 신규] recovery_agent의 "선 추론 후 표시" 정책 미반영 — 정확도 직접 영향

**모든 문서 상태**: 교차 검토의 S-4(CONFLICTED → ASK_USER), 리뷰의 P2-4(EXPLORE 재진입), 원안의 검토 6(명확화 연동) 모두 **"모호하면 사용자에게 질문"** 패턴을 전제.

**프로덕션 현실**:

> "질의를 해석했을 때 문제를 해결할 수 없는 경우 외에는 대체적으로 모호한 용어 등은 **선 추론 후 결과에 추론내용을 같이 표시**하면 됨"

이것은 설계의 근본적 가정을 변경한다:

```
── 현재 설계의 가정 ──
"예금신규 top 3" → 모호함 → ASK_USER → 사용자에게 "예금신규액? 예금신규건수?" 확인
                                       → 사용자 응답 후 SQL 생성

── 프로덕션 정책 ──
"예금신규 top 3" → 모호함 → "예금신규액"으로 추론 → SQL 생성
                         → 결과에 "예금신규액 기준으로 조회하였습니다" 표시
```

**영향 범위**:

1. **readiness_gate의 ASK_USER 발동 기준이 너무 낮음**: 현재 `has_conflicted_items()` → ASK_USER인데, 실제로는 대부분의 CONFLICTED 상황에서도 하나를 추론 선택하고 진행해야 함
2. **recovery_agent의 give_up 기준이 너무 빠름**: 정확한 메타를 찾지 못해도 "합리적 추론"으로 SQL을 생성해야 하는 경우가 많음
3. **sql_generator 프롬프트에 추론 근거 표시 채널이 없음**: 결과 응답에 "이런 기준으로 조회했습니다"를 표시하려면 추론 이유가 Present Layer까지 전달되어야 함

**수정안**:

```python
# 1. CONFLICTED 처리 정책 변경
# readiness_gate.py — ASK_USER 발동 조건을 제한적으로 변경
def _should_ask_user(reason: ReasoningState) -> bool:
    """
    ASK_USER는 '추론으로도 해결 불가능한' 경우에만 발동.
    예: 두 테이블이 완전히 다른 결과를 주는 경우, 사용자 의도가 2가지 이상으로 분기하는 경우.
    단순 용어 모호성(예금신규액 vs 건수)은 추론으로 처리.
    """
    critical_conflicts = [
        ki for ki in reason.knowledge_items
        if ki.status == ConfidenceStatus.CONFLICTED and ki.is_critical
    ]
    # 단순 값 모호성은 추론 가능 → ASK_USER 불필요
    unresolvable = [
        ki for ki in critical_conflicts
        if _is_unresolvable_conflict(ki)  # 테이블 선택 충돌, 산출식 충돌 등
    ]
    return len(unresolvable) > 0


# 2. 추론 근거 전달 채널 추가
# state.py — ReasoningState에 추가
class ReasoningState(BaseModel):
    ...
    inference_notes: list[str] = Field(default_factory=list)
    """
    추론으로 결정한 사항과 그 근거. Present Layer에서 사용자에게 표시.
    예: ["'예금신규'를 '예금신규액' 기준으로 해석하였습니다 (가장 일반적 사용 패턴)"]
    """

# 3. recovery_agent에서 추론 시 inference_notes에 기록
# recovery_agent.py
if decision.action == "ready" and reason.knowledge_items:
    for ki in reason.knowledge_items:
        if ki.status in (ConfidenceStatus.PROBABLE, ConfidenceStatus.CANDIDATE):
            reason.inference_notes.append(
                f"'{ki.key}'를 '{ki.value}' 기준으로 해석하였습니다 ({ki.evidence[-1] if ki.evidence else '추론'})"
            )
```

**근거**: 사용자 상호작용 규칙(`user-interaction.md`)에서도 "모호한 요청은 추측하지 말고 명확화 질문으로 확인"이라 되어 있으나, 프로덕션 환경 정보가 이를 override하여 "선 추론 후 표시" 정책을 명시. 이 정책이 적용되지 않으면 **불필요한 명확화 질문이 빈번하여 사용자 경험이 저하**되고, 실제로는 추론으로 충분한 케이스에서 대화 턴이 추가됨.

---

### B-2. [P1 신규] 참조 저장소 한계를 반영한 recovery_agent 도구 전략 부재

**모든 문서 상태**: recovery_agent의 6개 도구(`search_table_meta`, `search_code_meta`, `search_manual`, `search_glossary`, `get_sample_rows`, `get_date_distribution`)를 동등하게 취급.

**프로덕션 현실**:

| 도구 | 참조 저장소 | 프로덕션 기대 효과 |
|------|-----------|------------------|
| `search_table_meta` | MongoDB (테이블/컬럼 레이아웃) | **높음** — 가장 직접적 SQL 힌트 |
| `search_code_meta` | MongoDB (코드 메타) | **높음** — 코드값 매핑 필수 |
| `get_sample_rows` | PostgreSQL (직접 조회) | **높음** — 실제 데이터 확인 |
| `get_date_distribution` | PostgreSQL (직접 조회) | **중간** — 날짜 범위 확인 |
| `search_manual` | Qdrant (업무매뉴얼) | **낮음** — "SQL 추론에 필요한 직접적 힌트는 아님" |
| `search_glossary` | MongoDB (200개 미만) | **낮음** — 부실, 결과 없을 확률 높음 |

**문제**: recovery_agent의 LLM이 `search_manual`이나 `search_glossary`를 우선 호출하면, 유용하지 않은 결과로 ReAct 라운드를 소모. 특히 70B 모델은 도구 선택 정확도가 낮으므로(AgentBench IA 패턴) 이 위험이 높음.

**수정안**: recovery_agent 프롬프트에 도구 효과성 가이드 추가.

```
## 도구 우선순위 가이드

아래 도구들은 SQL 정확도에 대한 기여도 순으로 정렬되어 있습니다.
탐색 전략을 세울 때 상위 도구를 우선 고려하세요.

1. search_table_meta: 테이블/컬럼 구조 확인 (SQL 생성에 직접적 힌트)
2. get_sample_rows: 실제 데이터 패턴 확인 (컬럼값, NULL 여부, 코드값 추론)
3. search_code_meta: 코드 컬럼의 값-설명 매핑 확인 (WHERE 조건에 필수)
4. get_date_distribution: 날짜 컬럼의 데이터 범위 확인
5. search_glossary: 금융 용어 정의 확인 (용어사전이 부실하여 결과가 없을 수 있음)
6. search_manual: 업무 프로세스 확인 (SQL 추론에 간접적 참고만 됨)

주의: search_glossary와 search_manual의 결과가 비어있는 것은 정상입니다.
결과가 없다면 다른 도구로 전환하세요. 동일 도구를 다른 검색어로 재시도하지 마세요.
```

**추가**: `_build_recovery_prompt`에서 이전 라운드의 빈 결과 도구를 명시하여 LLM이 동일 도구를 재호출하지 않도록 가이드.

```python
# 빈 결과 도구 목록을 프롬프트에 포함
empty_tools = [
    tr["tool"] for tr in tool_results
    if tr.get("status") == "success" and not tr.get("result")
]
if empty_tools:
    lines.append(f"\n## 이전 라운드에서 결과가 없었던 도구: {', '.join(empty_tools)}")
    lines.append("위 도구를 다시 호출해도 결과가 없을 가능성이 높습니다.")
```

**근거**: CHESS(2024)에서 "스키마 프루닝이 NL-to-SQL의 핵심"이라는 결론은, 역으로 **비효과적 정보원에서의 탐색이 정확도를 저하**시킨다는 의미. 프로덕션에서 Qdrant 매뉴얼과 MongoDB 용어사전이 SQL 추론에 직접적 도움이 되지 않는다는 사실을 설계에 반영해야 함.

---

### B-3. [P1 신규] 함축적 금융 용어의 "합리적 추론" 경로 부재

**모든 문서 상태**: knowledge_items의 상태 전이는 UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED 또는 CONFLICTED. 모든 전이에 도구 결과 기반의 `evidence`가 필요.

**프로덕션 현실**:

> "은행에서 관행적으로 사용하는 용어를 사용", "의미를 풀어서 요청하는 훈련이 되어있지 않아 함축적으로 요청"

```
── 시나리오 ──
사용자: "여신 top 3 지점"

현재 설계:
1. planner: "여신" → knowledge_item(key="여신", status=UNRESOLVED)
2. knowledge_fetcher: search_table_meta("여신") → 여신실행, 여신잔액, 여신한도 등 다수 테이블 발견
3. knowledge_interpreter: "여신"의 구체적 의미 불분명 → UNRESOLVED 유지 또는 CONFLICTED
4. readiness_gate: CONFLICTED → ASK_USER
5. → 사용자에게 "여신실행액? 여신잔액?" 질문 (불필요한 대화 턴)

프로덕션 기대:
1. "여신" → 가장 일반적 해석인 "여신잔액"으로 추론
2. SQL 생성 → 결과 표시 시 "여신잔액 기준" 명시
```

**문제**: 현재 knowledge_items의 상태 전이 모델에는 **"증거 없이 관행적 추론으로 결정"하는 경로**가 없다. 도구 결과(evidence)가 없으면 UNRESOLVED에서 벗어날 수 없고, 도구를 아무리 호출해도 "여신 = 여신잔액"이라는 관행적 매핑은 발견할 수 없다 (메타 저장소에 이런 매핑이 없으므로).

**수정안**: `INFERRED` 상태 추가 또는 PROBABLE의 의미 확장.

```python
# 방안 A: 새로운 상태 추가 (명시적이지만 enum 변경이 큼)
class ConfidenceStatus(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    INFERRED = "INFERRED"      # 도구 증거 없이 관행/맥락 기반 추론
    CANDIDATE = "CANDIDATE"
    PROBABLE = "PROBABLE"
    CONFIRMED = "CONFIRMED"
    CONFLICTED = "CONFLICTED"

# 방안 B: PROBABLE의 evidence 없는 사용 허용 (최소 변경, 권장)
# → recovery_agent 프롬프트에 관행적 추론 지침 추가
# → KnowledgeUpdate에 inference_type 필드 추가

class KnowledgeUpdate(BaseModel):
    key: str
    new_status: Literal["CANDIDATE", "PROBABLE", "CONFIRMED", "CONFLICTED"]
    evidence: str  # 도구 증거 또는 "관행적 해석: 여신은 통상 여신잔액을 의미"
    value: str | None = None
    is_inferred: bool = False  # True이면 도구 증거 없는 추론
```

**recovery_agent 프롬프트 추가**:

```
## 추론 지침

도구 검색으로 정확한 답을 찾지 못했지만, 금융 도메인 관행상 합리적 추론이 가능한 경우:
- new_status를 "PROBABLE"로 설정하고 is_inferred=true로 표시
- evidence에 추론 근거를 명시 (예: "관행적 해석: '여신'은 통상 '여신잔액'을 의미")
- 이 추론은 결과 응답에서 사용자에게 표시됩니다

추론이 합리적인 경우:
- 금융 용어의 일반적 해석 (예: "여신" → 여신잔액, "수신" → 수신잔액)
- 기간 미지정 시 최근 기간 (예: "실적" → 당월 실적)
- 집계 기준 미지정 시 금액 기준 (예: "top 3" → 금액 기준 상위 3)

추론이 부적절한 경우 (ASK_USER 필요):
- 서로 다른 테이블을 사용해야 하는 완전히 다른 의미가 존재하는 경우
- 금융 지표 산출식이 불확실한 경우 (연체율, BIS비율 등)
```

**근거**: financial-domain.md에서 "금융 계수산출식은 정확한 산출식이 필수, 불확실하면 SQL 생성하지 않음"이라고 명시되어 있으나, 이는 **산출식**에 한정된 규칙. 일반적인 용어 해석은 "선 추론 후 표시" 정책이 적용됨. 현재 설계는 이 두 가지를 구분하지 못함.

---

### B-4. [P1 신규] readiness_gate의 `term_resolution` 가중치(55%)가 프로덕션에서 과도

**코드 검증**: `confidence_scorer.py`의 `calculate_readiness()`:

```python
term_resolution (55%):   CONFIRMED|PROBABLE critical items / total critical items
table_coverage (25%):    candidate_tables with description / total candidates
join_path (20%):         1.0 if multi-table has common join_keys, else 0.3
```

**프로덕션 문제**:

함축적 질의에서는 `knowledge_items`의 많은 항목이 도구 증거로 CONFIRMED까지 도달하기 어렵다. 예를 들어 "예금신규 top 3" 질의에서:

| knowledge_item | 도구로 확인 가능? | 프로덕션 기대 상태 |
|----------------|-----------------|------------------|
| "예금신규" → 어떤 지표? | 불가 (관행적 해석 필요) | PROBABLE (추론) |
| "top 3" → 금액? 건수? | 불가 (관행적 해석 필요) | PROBABLE (추론) |
| "기간" → 언제? | 불가 (명시 안 됨) | PROBABLE (추론: 당월) |
| "테이블" → 어떤 테이블? | **가능** (search_table_meta) | CONFIRMED |

4개 critical items 중 1개만 CONFIRMED → term_resolution = 0.25 (PROBABLE 포함 시 1.0)

**문제**: `calculate_readiness`에서 PROBABLE이 CONFIRMED과 동등하게 처리되는지 확인 필요. 만약 CONFIRMED만 카운트한다면, 합리적 추론만으로 진행해야 하는 대부분의 프로덕션 질의에서 **readiness_score가 threshold에 도달하지 못하고 불필요한 recovery 루프에 진입**.

**코드 확인 결과**: confidence_scorer.py에서 `CONFIRMED|PROBABLE`을 모두 카운트하므로 PROBABLE이면 통과. 그러나 **B-3에서 제기한 "추론으로 PROBABLE 설정" 경로가 없으면**, 이 항목들은 UNRESOLVED에 머물러 score가 낮아짐.

**수정안**: B-3의 추론 경로가 구현되면 이 문제는 자연스럽게 해소. 단, readiness_gate에서 PROBABLE(추론)과 PROBABLE(도구 증거)을 구분하여 **추론 기반 PROBABLE이 많으면 결과에 "추론 사항" 안내를 강화**하는 것을 권장.

```python
# readiness_gate에서 추론 비중 체크
inferred_count = sum(
    1 for ki in reason.knowledge_items
    if ki.status == ConfidenceStatus.PROBABLE and ki.is_inferred
)
if inferred_count > 0:
    reason.inference_notes.append(
        f"총 {inferred_count}건의 용어를 관행적 해석으로 추론하였습니다. "
        "결과가 예상과 다를 경우 구체적으로 요청해 주세요."
    )
```

---

### B-5. [P2 신규] Qdrant SQL 이력 검색(`search_use_cases`)의 recovery 경로 부재

**원안 상태**: `search_use_cases`는 recovery_agent 도구 목록에서 제외 (01-strategy 3.3절). Phase 1(planner)에서만 사용.

**프로덕션 재평가**: Qdrant에 저장된 **과거 수행 SQL과 설명**은 실제로 SQL 추론에 가장 직접적인 힌트가 될 수 있다. 특히:

- MongoDB 메타가 부실한 상황에서, 과거 유사 SQL이 "어떤 테이블을 어떤 조인으로 사용했는지"를 직접 보여줌
- planner의 초기 검색과 recovery의 재검색은 **검색어가 다를 수 있음** — planner는 원본 질의로 검색하지만, recovery는 실패 원인을 반영한 더 구체적인 검색어를 사용할 수 있음

```
── 시나리오 ──
planner: search_use_cases("지점별 여신 실행 금액") → 유사 SQL 0건 (검색어가 너무 구체적)
→ recovery 진입 (테이블 확인 불확실)
recovery_agent: search_use_cases("여신 실행") → 유사 SQL 3건 발견!
  → 과거 SQL에서 TB_LOAN_EXEC 테이블, exec_dt 날짜 컬럼, branch_cd 지점코드 확인
  → 한 번에 여러 knowledge 공백 해소
```

**수정안**: recovery_agent 도구 목록에 `search_use_cases`를 **조건부 복원**. 단, 동일 검색어 중복 방지를 위해 이전 검색 이력을 프롬프트에 포함.

```python
# recovery_agent 도구 목록 (변경)
class ToolCall(BaseModel):
    tool: Literal[
        "search_table_meta", "search_code_meta", "search_manual",
        "search_glossary", "get_sample_rows", "get_date_distribution",
        "search_use_cases",  # 복원
    ]
```

```
# 프롬프트 추가
## 과거 SQL 이력 검색 (search_use_cases)
이전에 검색한 키워드: {', '.join(reason.searched_use_case_queries)}
이미 검색한 키워드와 동일하거나 유사한 검색어는 사용하지 마세요.
다른 관점의 검색어를 사용하세요 (예: "지점별 여신 실행 금액" 대신 "여신 실행" 또는 "대출 실적").
```

이 수정은 P2로 분류하지만, 보고서 SQL 저장소가 구현되기 전까지 **Qdrant SQL 이력이 사실상 유일한 참조 SQL 소스**이므로 조기 구현을 권장.

---

## C. 교차 검토의 잘 된 부분 — 프로덕션에서 특히 중요한 판단

### C-1. 3-1(degraded_mode 기각 → config 관리) — 매우 적절

교차 검토가 리뷰의 모델별 하드코딩 분기를 기각하고 `RECOVERY_MAX_INTERNAL_ROUNDS` config로 대체한 판단은, 폐쇄망 배포에서 **설정 파일 변경만으로 전환**해야 한다는 프로젝트 원칙에 정확히 부합. Solar Pro 2 → Qwen3.5 → GPT OSS 전환 시 코드 변경 없이 config만 조정하면 됨.

### C-2. 4-1(give_up 시 recovery_agent 내부에서 force-generate 판정) — 매우 적절

교차 검토가 리뷰의 `recovery_gave_up` 플래그 대신, recovery_agent 내부에서 `calculate_readiness()` 호출 후 직접 Phase 결정하는 방식을 제안한 것이 더 깔끔. readiness_gate 재진입을 완전 차단하여 무한 루프 가능성을 근본적으로 제거. **프로덕션의 "선 추론 후 표시" 정책과도 일치** — give_up이어도 score가 일정 수준이면 추론 기반으로 SQL을 생성하고 결과에 주의사항을 표시.

### C-3. 5-2(recovery_entry_source 필드) — 프로덕션에서 더 중요

교차 검토가 P2로 배치했지만, 프로덕션에서는 중요도가 높다. recovery_agent가 "readiness_gate에서 왔는지, sql_validator에서 왔는지"에 따라:

- readiness_gate 경유: 초기 컨텍스트가 부족 → 넓은 탐색 필요
- sql_validator 경유: SQL 구조/의미 오류 → 특정 문제 해결 필요

70B 모델에 이 맥락을 명시적으로 제공하면 도구 선택 정확도가 향상됨.

---

## D. 구현 우선순위 통합 (프로덕션 환경 반영)

### P0 — 구현 전 설계 보완 필수 (5건)

| # | 항목 | 출처 | 프로덕션 근거 |
|---|------|------|-------------|
| 1 | give_up 시 즉시 종료 + force-generate 내부 판정 | 교차 4-1 | 70B give_up 빈도 높음, "선 추론" 정책과 연계 |
| 2 | planner_node에서 exploration_phase/recovery_rounds 리셋 | 교차 4-2 | 멀티턴 필수 |
| 3 | _execute_single_tool 어댑터 kwargs→문자열 변환 | 교차 4-3 | 코드 직접 검증 |
| 4 | decision=None 시 즉시 종료 | 교차 2-3 | 70B 파싱 실패율 높음 |
| 5 | **[신규]** 도구 실행 병렬화 (asyncio.gather) | A-2-1 격상 | ReAct 라운드 수 감소 → 70B 판단 오류 기회 감소 |

### P1 — 구현 초기 반영 권장 (12건)

| # | 항목 | 출처 | 프로덕션 근거 |
|---|------|------|-------------|
| 6 | **[신규]** "선 추론 후 표시" 정책 반영 — ASK_USER 기준 변경 + inference_notes 채널 | B-1 | **정확도 + UX 핵심** |
| 7 | **[신규]** 도구 우선순위 가이드 + 빈 결과 도구 피드백 | B-2 | 참조 저장소 한계 대응 |
| 8 | **[신규]** 함축적 용어의 관행적 추론 경로 (is_inferred 플래그) | B-3 | 대부분의 프로덕션 질의가 함축적 |
| 9 | last_verdict 필드 추가 | 교차 4-4 | 라우팅 명확성 |
| 10 | _find_knowledge_item 복수 매칭 시 None 반환 | 교차 4-5 | 금융 용어 접미어 |
| 11 | PROMOTION_ORDER에 CANDIDATE 추가 | 교차 4-6 | 코드 검증 |
| 12 | discovery_facts 경로 + CANDIDATE 프롬프트 포함 | 교차 4-7, 2-1 | sql_generator 연계 |
| 13 | KnowledgeUpdate CANDIDATE + search_use_cases 제거 | 교차 2-2, 2-4 | 시스템 일관성 |
| 14 | 진전 감지 (2회 연속 무변화 → 조기 종료) | A-2-2 격상 | 부실 메타 환경에서 비용 효율 |
| 15 | fallback 파싱 안전성 강화 | A-2-3 격상 | 파인튜닝 없는 70B의 JSON 불안정 |
| 16 | 프롬프트에 tool_calls 독립성 규칙 | 교차 5-3 | 70B 가이드 |
| 17 | recovery_entry_source 필드 | 교차 5-2 | 70B 맥락 제공 |

### P2 — 품질/안정성 향상 (5건)

| # | 항목 | 출처 | 프로덕션 근거 |
|---|------|------|-------------|
| 18 | readiness_gate 추론 비중 체크 + 안내 강화 | B-4 | 추론 기반 응답의 투명성 |
| 19 | **[신규]** search_use_cases 조건부 복원 (recovery 경로) | B-5 | 유일한 참조 SQL 소스 |
| 20 | force-generate 임계값 통일 + config 관리 | 교차 5-5 | 문서 일관성 |
| 21 | recovery_rounds 의미 명확화 | 교차 2-5 | 가독성 |
| 22 | EXPLORE verdict PENDING 스텝 가드 | 교차 4-8 | 방어적 프로그래밍 |

---

## E. 정확도 관점 종합 평가

### 현재 설계의 정확도 병목 (프로덕션 환경 기준)

```
사용자 질의 → [함축적 용어 해석] → [메타 검색] → [SQL 생성] → [검증] → 결과

      ↑ 병목 1              ↑ 병목 2         ↑ 병목 3
  관행적 추론 경로 없음   부실한 메타로      70B의 ReAct
  → ASK_USER 과다        도구 결과 빈약    라운드 증가
                        → recovery 루프 낭비  → 판단 오류 증가
```

### 이 재검토의 수정안이 해소하는 영향

| 병목 | 관련 수정안 | 기대 효과 |
|------|-----------|----------|
| 병목 1: 함축적 용어 | B-1(선 추론 정책), B-3(추론 경로) | ASK_USER 빈도 50% 이상 감소, 대화 턴 절약 |
| 병목 2: 부실 메타 | B-2(도구 우선순위), B-5(SQL 이력 복원) | 유효하지 않은 도구 호출 감소, 참조 SQL 활용 |
| 병목 3: 70B ReAct | A-2-1(병렬화), A-2-2(진전 감지), A-2-3(파싱 안전) | ReAct 라운드 감소, 비정상 종료 방지 |

### 최종 판단

교차 검토 문서의 기본 구조(2-Phase Exploration, Phase 1 기계적 분리, Structured Output, Hypothesis 관리)는 **프로덕션 환경에서도 유효**하다. 단, 위의 수정안 없이 구현하면:

1. **불필요한 ASK_USER**가 빈번하여 사용자 경험 저하 (대부분의 모호한 질의에서 질문)
2. **recovery 루프에서 비효과적 도구 호출**로 응답 시간 증가 (search_manual, search_glossary 낭비)
3. **70B 모델의 ReAct 불안정성**이 증폭되어 give_up 비율 증가 (진전 감지/파싱 안전 부재)

수정안이 반영되면, **함축적 질의 → 합리적 추론 → SQL 생성 → 추론 근거 표시**의 단축 경로가 확보되어, 프로덕션 환경에서의 정확도와 응답 속도 모두 의미 있게 개선될 것으로 판단.

---

## 참고

- 프로덕션 환경 정보: 사용자 제공 (2026-04-01)
- 코드 검증: `main` 브랜치 `6491f9b` 기준
- 교차 검토 참고 문헌은 해당 문서의 참고 문헌 섹션 참조
