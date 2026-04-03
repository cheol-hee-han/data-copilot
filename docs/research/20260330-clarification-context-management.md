# NL-to-SQL 명확화 대화 컨텍스트 관리: Query Rewriting vs. Structured Context Passing

**작성일**: 2026-03-30
**작성자**: Research Analyst Agent
**분류**: NL2SQL / Conversational AI / Context Management

---

## 요약 (Executive Summary)

이 보고서는 NL-to-SQL 시스템에서 명확화(clarification) 답변을 SQL 생성 단계로 전달하는 두 가지 설계 접근법을 비교한다.

- **Approach A (Query Rewriting)**: 명확화 답변을 원본 질의에 합쳐 재작성된 단일 질의를 생성
- **Approach B (Structured Context Passing)**: 원본 질의를 보존하고 명확화 Q&A를 별도 구조화 컨텍스트로 병렬 전달

**핵심 결론**: 학술 연구(논문 10편 이상)와 프로덕션 시스템(SiriusBI, Sphinteract, AmbiSQL) 증거가 설계 방향에서 갈린다. **일반적 Multi-Turn 대화 연속성**에서는 Approach B(구조화 컨텍스트)가 우위를 보이지만, **명확화 후 단일 SQL 생성 최종 단계**에서 일부 시스템은 Query Rewriting을 마지막 통합 단계에 사용한다. 이 프로젝트(은행 도메인, 폐쇄망 중형 LLM)에서는 **Hybrid 전략**—원본 보존 + 구조화 Q&A 통과 + 최종 단계 Rewriting 선택적 적용—이 가장 안전하다.

---

## 1. 연구 배경 및 문제 정의

### 1.1 문제 상황

Data Copilot의 명확화 흐름:

```
사용자: "여신 데이터 뽑아줘"
                ↓
시스템: "어떤 여신 데이터를 원하시나요? 1) 신규 여신 건수 2) 여신 실행 금액"
                ↓
사용자: "신규 여신 실행 금액이요"
                ↓
SQL 생성 노드가 받을 컨텍스트 = ???
```

**Approach A**: 재작성 → "신규 여신 실행 금액 데이터 뽑아줘"
**Approach B**: 원본 + Q&A → `{original: "여신 데이터 뽑아줘", clarifications: [{Q: "...", A: "신규 여신 실행 금액이요"}]}`

### 1.2 평가 기준

| 기준 | 설명 |
|------|------|
| 정보 손실 위험 | 의미 왜곡, 핵심 엔티티 소실 여부 |
| LLM 이해 품질 | SQL 생성 정확도 |
| 감사 가능성 | 명확화 근거 추적 |
| 다중 라운드 확장성 | 2~3회 명확화 후 컨텍스트 누적 |
| 약한 LLM 호환성 | 폐쇄망 70B 급 모델 대응 |

---

## 2. 주요 벤치마크 및 학술 데이터셋 분석

### 2.1 CoSQL (EMNLP 2019) — 대화형 NL2SQL 기준 데이터셋

**규모**: 30,000+ turns, 10,000+ SQL, 3,000 dialogues, 200 databases, 138 domains

CoSQL은 대화 맥락 처리의 두 가지 패러다임을 병립시킨다:

1. **Full History Concatenation**: 이전 모든 turns의 질의와 SQL을 순서대로 프롬프트에 포함
2. **Question Rewriting (QR)**: 현재 utterance를 독립 가능한 단일 질문으로 재작성

CoSQL에서 달성된 최고 성능은 CodeS-7B 기반 fine-tuning으로, 두 패러다임을 **모델 가중치 병합(model merging)**으로 통합하는 접근이다. 이는 어느 단일 방법도 우위를 확정하지 못했음을 시사한다.

**DIR(Dialogue Rewrite) 데이터셋 결과**: SParC와 CoSQL 기반으로 구축된 5,908개 대화에서 oracle 재작성 질문을 사용했을 때 SParC +12%, CoSQL +27% 성능 향상이 나타났다. 그러나 이는 "완벽한 재작성" 기준이며, 자동 재작성 오류는 이 수치를 크게 낮춘다.

### 2.2 SParC (ACL 2019) — Sequential Interaction

12,000+ sequential NL-SQL pairs. CoE-SQL(NAACL 2024)의 결과:

| 방법 | SParC QM | SParC IM |
|------|---------|---------|
| ACT-SQL (Question Rewriting) | 63.8% | 38.9% |
| **CoE-SQL (Chain-of-Editions)** | **70.3%** | **50.5%** |
| RASAT+PICARD (fine-tuned) | 73.3% | 54.0% |

**CoE-SQL의 핵심 발견**: ACT-SQL처럼 질문을 재작성하는 접근은 **"error propagation"** (오류 전파) 문제를 야기한다. CoE-SQL은 대신 이전 SQL에 대한 최소 편집 체인(chain of editions)을 유지함으로써 컨텍스트를 구조화된 형태로 전달하고, 재작성 없이 6.5% 성능 우위를 달성했다.

> 인용: "ACT-SQL performs poorly under the multi-turn setup due to the error propagation occurring in the process of question rewriting." — CoE-SQL, NAACL 2024

### 2.3 PRACTIQ (Amazon Science / NAACL 2025) — 모호한 질의 처리

**데이터**: 4-turn 대화 구조 (모호 질의 → 명확화 요청 → 사용자 답변 → 최종 SQL)

PRACTIQ가 정의한 4-turn 대화 구조는 Approach B와 직접 대응된다:
- Turn 1: 원본 사용자 질의 (보존됨)
- Turn 2: 시스템 명확화 질문
- Turn 3: 사용자 답변
- Turn 4: SQL 생성 (Turn 1 + Q&A 컨텍스트 사용)

PRACTIQ는 원본 질의를 재작성하지 않고 4-turn 대화 컨텍스트 전체를 SQL 생성 단계에 전달한다. 이는 **Approach B와 구조적으로 동일**하다.

### 2.4 Track-SQL (NAACL 2025) — 이중 추출 컨텍스트 추적

Track-SQL은 질문을 재작성하지 않고 다음 두 개의 구조화 저장소를 유지한다:

- **History Schema Store**: 각 Turn의 스키마 추출 확률 보존
- **History Question & SQL Store**: 이전 질의-SQL 쌍 전체 보존

Multi-turn 질의를 `Q1 & Q2 & ... & Qm` 형태로 연결하되, 각각 독립적으로 보존. SParC +7.1%, CoSQL +9.55% 실행 정확도 향상.

---

## 3. 프로덕션 시스템 분석

### 3.1 Sphinteract (VLDB 2025, UCSB / Microsoft Research)

**SRA(Summarize-Review-Ask) 패러다임**:
1. 원본 질의에서 SQL 초안 생성
2. SQL 실행 결과를 사용자에게 제시
3. 사용자 피드백 기반 명확화 질문 생성 (multiple choice)
4. 명확화 답변 → 다음 SQL 생성

Sphinteract는 명확화 답변을 **구조화 피드백 컨텍스트**로 처리하며, 원본 질의는 변경하지 않는다. KaggleDBQA/BIRD 벤치마크에서 명확화 질문만으로 **최대 42% 정확도 향상**을 기록했다.

### 3.2 SiriusBI (Tencent, VLDB 2025)

Tencent의 사내 BI 시스템으로 Finance(97%), Advertisement(93%), Cloud(96%) SQL 생성 정확도 달성.

**MRD-Q(Multi-Round Dialogue with Querying) 구조**:
- **Semantic Completion**: 불완전 입력을 보완할 때 이전 대화에서 직접 컨텍스트를 검색 (재작성 아님)
- **Knowledge-Guided Clarification**: 명확화 답변을 도메인 지식과 조합하여 구조화 컨텍스트로 저장
- **SQL 생성**: 보강된 컨텍스트를 SQL 생성 모듈에 전달

SiriusBI는 "원본 질의를 재작성하지 않고 구조화 대화 컨텍스트를 유지"하는 Approach B에 가까운 설계를 채택했다.

```
SiriusBI 컨텍스트 통합 방식:
원본 질의 (보존) + 도메인 지식 + 명확화 Q&A → SQL 생성 모듈
```

### 3.3 AmbiSQL (XiYan-SQL 통합, 2025)

Alibaba의 XiYan-SQL 상용 백엔드에 통합된 시스템. **87.2% 모호성 탐지 정밀도**, SQL 정확도 +50% (42.5% → 92.5%).

AmbiSQL은 **Hybrid 전략**을 사용한다:
1. 명확화 Q&A를 트리 구조로 저장 (Approach B의 구조화 컨텍스트)
2. 최종 SQL 제출 전 "refined query" 생성 (Approach A의 Rewriting을 최종 단계에만 적용)
3. Rewriting된 질의가 여전히 모호할 경우 재검증 루프 실행

이 설계는 **감사 가능성(원본+Q&A 보존)과 LLM 호환성(단일 명확한 질의 제공)**을 모두 달성한다.

---

## 4. 핵심 분석: 두 접근법의 특성 비교

### 4.1 정보 손실 위험

**Approach A (Query Rewriting)**의 취약점:

1. **핵심 엔티티 변형**: "Intent Scoping and Paraphrasing for Robust NL2SQL" (VLDB 2025 AIDB Workshop, ETH Zurich/Zalando/IBM)에서 "generic paraphrases can be detrimental for NL2SQL as they may alter critical entities (e.g., IDs, numerical values) essential for correct SQL"임을 경고한다.

2. **다중 라운드 오류 누적**: 긴 대화에서 재작성 오류가 누적되며, "query reformulation errors accumulate from each turn as the conversation goes on, leading to degradation in overall performance."

3. **금융 도메인 특수성**: 은행 쿼리에서 "신규", "실행", "건수" vs "금액"과 같은 미묘한 차이는 재작성 과정에서 소실될 수 있다.

**Approach B (Structured Context)**는 원본 질의가 보존되므로 위 세 위험이 구조적으로 제거된다.

### 4.2 LLM 이해 품질

**약한 LLM(Solar Pro 2 70B)에 대한 고려**:

- Long context에서 약한 LLM은 "Lost in the Middle" 현상을 겪는다 (핵심 정보가 컨텍스트 중간에 묻힘).
- Approach B의 구조화 컨텍스트는 명시적 역할 레이블(`Q: ...`, `A: ...`)로 LLM의 attention을 유도할 수 있다.
- Approach A의 재작성 질의는 단순해 보이지만, 재작성 과정 자체에 LLM 호출이 필요하며 약한 LLM의 재작성 품질이 불안정하다.

**강한 LLM(Claude Sonnet, GPT-4)**: 두 방법 모두 처리 가능하나, 구조화 컨텍스트에서 더 일관성 있는 SQL을 생성하는 경향이 있다.

**BIRD 벤치마크 힌트 실험**: 구조화 힌트(명확화와 유사) 추가 시 단순 질의 대비 +16.36% 정확도 향상 (43.87% → 60.23%).

### 4.3 감사 가능성

| 측면 | Approach A | Approach B |
|------|-----------|-----------|
| 원본 질의 보존 | 소실됨 | 보존됨 |
| 명확화 근거 추적 | 불가 | 가능 |
| SQL 생성 근거 설명 | 어려움 | 용이함 |
| 규제 감사 대응 | 취약 | 강함 |

은행 시스템에서 감사 대응은 비기능 요구사항이 아닌 **필수 요건**이다. Approach B가 명백히 우위다.

### 4.4 다중 라운드 확장성

명확화가 2~3회 발생하는 경우:

**Approach A**:
```
Round 1: "여신 데이터 뽑아줘" + Answer → "신규 여신 실행 금액 데이터 뽑아줘"
Round 2: "신규 여신 실행 금액 데이터 뽑아줘" + Answer → "이번 달 신규 여신 실행 금액 데이터 뽑아줘"
Round 3: "이번 달 신규 여신 실행 금액 데이터 뽑아줘" + Answer → "이번 달 신규 가계 여신 실행 금액 데이터 뽑아줘"
→ 오류 누적 위험, 각 단계 재작성 품질 의존
```

**Approach B**:
```
원본: "여신 데이터 뽑아줘"
clarifications: [
    {Q: "어떤 데이터?", A: "신규 여신 실행 금액"},
    {Q: "기간은?", A: "이번 달"},
    {Q: "여신 종류는?", A: "가계 여신"}
]
→ 선형 누적, 독립적, 오류 전파 없음
```

### 4.5 약한 LLM 특수 고려사항

폐쇄망 Solar Pro 2 70B 기준:

- **JSON 출력 안정성**: 구조화 Pydantic 스키마로 전달 시 명시적 역할 분리가 오히려 유리하다.
- **재작성 지시 따르기**: 약한 LLM에게 "이 질의를 재작성하시오"보다 "이 원본 질의와 명확화 답변을 참고하여 SQL을 생성하시오"가 더 단순하고 오류가 적다.
- **Qwen thinking 모드**: thinking 토큰을 소모하는 Qwen 모델에서 재작성 단계는 추가 토큰 비용을 야기한다.

---

## 5. 각 접근법의 기각 이유 / 채택 이유

### 5.1 Approach A (Pure Query Rewriting) 기각 이유

1. **CoE-SQL (NAACL 2024) 실증**: 재작성 기반 ACT-SQL 대비 구조화 컨텍스트 편집 체인이 SParC에서 +6.5% 우위. 오류 전파 문제가 재현 가능한 취약점으로 확인됨.
2. **Intent Scoping 논문 (VLDB 2025)**: 엔티티 소실 위험 경고 — "generic paraphrases detrimental for NL2SQL".
3. **감사 불가능**: 원본 질의 소실로 금융 규제 요건 충족 불가.
4. **다중 라운드 누적 오류**: 각 재작성 단계의 LLM 오류가 선형적으로 누적됨.
5. **약한 LLM 취약성**: 70B 급 모델의 재작성 품질이 불안정하며, 재작성 자체를 위한 추가 LLM 호출 비용 발생.

### 5.2 Approach B (Pure Structured Context) 채택 근거

1. **SiriusBI 프로덕션 사례**: Tencent 금융 도메인에서 97% 정확도 달성, 구조화 대화 컨텍스트 유지 방식 채택.
2. **PRACTIQ 설계 패턴**: Amazon Science의 4-turn 구조가 원본 질의 보존 + Q&A 컨텍스트 전달 방식임을 입증.
3. **Track-SQL (NAACL 2025)**: 재작성 없이 구조화 히스토리 스토어로 SParC +7.1%, CoSQL +9.55% 달성.
4. **감사 가능성**: 원본 질의 + 명확화 근거 전체 보존으로 금융 규제 감사 대응.
5. **LLM 부담 감소**: 재작성 단계 제거로 약한 LLM에서 오히려 더 단순한 작업 분해.

---

## 6. 권고안: Hybrid Structured Context with Optional Terminal Synthesis

### 6.1 핵심 설계 원칙

```
원본 질의는 절대 변경하지 않는다.
명확화 Q&A는 구조화 스키마로 독립 누적한다.
SQL 생성 LLM에게는 원본 + Q&A 전체를 분리된 프롬프트 섹션으로 전달한다.
선택적으로: SQL 생성 전 단계에서 "enriched question"을 내부 생성하되, 이를 상태에 저장하지 않는다.
```

### 6.2 LangGraph 상태 설계 권고

```python
class ClarificationEntry(BaseModel):
    """단일 명확화 교환 단위."""
    question: str          # 시스템이 물은 질문
    answer: str            # 사용자 답변
    clarification_type: str  # "scope", "period", "aggregation", "filter" 등
    round: int             # 명확화 라운드 번호

class AgentState(TypedDict):
    # 원본 질의는 절대 수정하지 않음
    original_query: str

    # 명확화 Q&A 누적 리스트 (Approach B)
    clarifications: list[ClarificationEntry]

    # [선택] SQL 생성 직전 단계에서 생성되는 enriched description
    # 이 필드는 상태에 캐시하지 않고 SQL 노드 내에서 일시적으로 생성 가능
    # enriched_query: str | None  # 사용 시 주의

    # 이하 기존 상태 필드들...
```

### 6.3 SQL 생성 노드 프롬프트 구조

```
[시스템 지시]
당신은 은행 데이터를 조회하는 SQL을 생성하는 전문가입니다.

[사용자 원본 질의]
{original_query}

[명확화 답변]
다음은 사용자의 의도를 명확히 하기 위해 나눈 대화입니다.
원본 질의와 함께 반드시 참고하십시오:

라운드 1:
  질문: {clarifications[0].question}
  사용자 답변: {clarifications[0].answer}

라운드 2:
  질문: {clarifications[1].question}
  사용자 답변: {clarifications[1].answer}

[데이터베이스 스키마]
{schema_context}

[이전 유사 SQL 참조]
{similar_sql_examples}

[지시사항]
위 원본 질의와 명확화 답변을 모두 반영하는 SQL을 생성하십시오.
```

### 6.4 선택적 Terminal Synthesis 패턴

AmbiSQL이 채택한 방식으로, 다음 조건일 때만 적용을 고려한다:

- 명확화 라운드가 3회 이상인 경우 (컨텍스트 복잡도 증가)
- 약한 모델(70B 미만)이 structured context를 처리하지 못할 경우
- 사용자 확인 후 "이해한 내용" 요약을 명시적으로 보여줄 때

```python
def build_enriched_question(
    original_query: str,
    clarifications: list[ClarificationEntry]
) -> str:
    """
    SQL 생성 직전 단계에서만 사용하는 임시 질의 강화.
    이 결과를 상태(state)에 저장하거나 원본 질의를 대체하지 않는다.
    감사 목적으로 별도 로그에만 기록한다.
    """
    ...
```

---

## 7. 금융/은행 도메인 특화 고려사항

### 7.1 금융 용어의 미묘한 차이

은행 도메인에서 유사 개념의 구분은 SQL 생성의 정확성을 좌우한다:

| 원본 표현 | 가능한 해석 1 | 가능한 해석 2 |
|---------|-------------|-------------|
| "여신 데이터" | 신규 여신 실행 건수 | 잔액 기준 여신 잔고 |
| "연체율" | 잔액 기준 연체율 | 건수 기준 연체율 |
| "이번 달 매출" | 당월 1일~오늘 | 전전월 결산 기준 |

이런 미묘한 차이는 **재작성 LLM이 틀릴 경우 회복 불가능**하다. Approach B에서는 명확화 답변이 독립적으로 보존되어, SQL 생성 단계에서 각 명확화 항목을 개별적으로 SQL 절에 매핑할 수 있다.

### 7.2 감사 추적 요건

금융기관 IT 시스템의 감사 요건상, 사용자의 원본 요청과 AI의 해석 과정이 모두 로그로 남아야 한다. Approach B는 이를 구조적으로 지원한다:

```python
# 감사 로그 구조
audit_entry = {
    "user_id": user_id,
    "session_id": session_id,
    "original_query": state["original_query"],        # 원본 보존
    "clarifications": state["clarifications"],          # 명확화 근거
    "generated_sql": final_sql,                         # 생성 SQL
    "executed_at": datetime.now(),
}
```

### 7.3 불완전한 IT 메타 대응

은행 정보계 DB의 불충분한 테이블/컬럼 설명을 고려할 때, 명확화 답변이 "어떤 테이블을 선택해야 하는지"에 대한 힌트를 포함하는 경우가 많다. 이 정보를 구조화 컨텍스트로 분리 전달하면 ES 메타 검색 쿼리에도 재사용이 가능하다.

---

## 8. 기각된 대안

| 대안 | 기각 이유 |
|------|---------|
| **Pure Query Rewriting (Approach A)** | 오류 전파, 엔티티 소실, 감사 불가, 다중 라운드 취약 |
| **Full History Concatenation (raw turns)** | 컨텍스트 노이즈 증가, "40+ messages" 문제 (실제 프로덕션 사례에서 보고), 구조 없는 텍스트로 약한 LLM 혼란 |
| **Dialog State Tracking (DST) Slot Filling** | 은행 도메인 슬롯 사전 정의 비용 과다, 사전 정의되지 않은 표현에 취약, 시스템 구축 복잡도 증가 |
| **Single-pass 재작성 (마지막 라운드만)** | 첫 라운드 정보 소실 가능, 라운드 간 의존성 처리 불가 |

---

## 9. 벤치마크 수치 요약

| 실험 | 방법 | 정확도 | 출처 |
|------|------|--------|------|
| SParC QM | ACT-SQL (Query Rewriting) | 63.8% | CoE-SQL, NAACL 2024 |
| SParC QM | CoE-SQL (Structured Edit Chain) | **70.3%** | CoE-SQL, NAACL 2024 |
| SParC Exec | Track-SQL vs baseline | **+7.1%** | Track-SQL, NAACL 2025 |
| CoSQL Exec | Track-SQL vs baseline | **+9.55%** | Track-SQL, NAACL 2025 |
| BIRD (hint 추가) | Structured hint | +16.36% | Long Context NL2SQL, VLDB 2025 |
| KaggleDBQA/BIRD | Sphinteract clarification | +최대 42% | Sphinteract, VLDB 2025 |
| AmbiSQL 통합 | Structured Q&A + terminal rewrite | +50% (42.5→92.5%) | AmbiSQL, 2025 |
| Clarification (general) | GPT-3.5 ambiguous→clarified | 34.5%→49.0% | Multi-turn NL2SQL, 2024 |

---

## 10. 결론

**Data Copilot 구현 권고**: **Approach B (Structured Context Passing)를 기본으로, AmbiSQL 패턴의 선택적 Terminal Synthesis 통합**

구체적 설계 원칙:
1. `original_query`는 상태(state)에서 immutable로 취급한다.
2. 명확화 교환은 `ClarificationEntry` 리스트로 독립 누적한다.
3. SQL 생성 노드는 원본 + 명확화 Q&A를 **분리된 프롬프트 섹션**으로 전달한다.
4. 감사 로그에는 원본 + 전체 명확화 Q&A + 생성 SQL을 기록한다.
5. 다중 라운드(3회+) 또는 약한 모델 환경에서만 Terminal Synthesis(내부 enriched question 생성)를 옵션으로 적용한다. 이 경우에도 원본 질의를 대체하지 않는다.

---

## 참고 문헌

### Tier 1 (학술 논문)

1. **CoE-SQL** (NAACL 2024): "CoE-SQL: In-Context Learning for Multi-Turn Text-to-SQL with Chain-of-Editions". X-LANCE, 상하이교통대. https://arxiv.org/abs/2405.02712
   - 핵심 기여: Query Rewriting 대비 Structured Edit Chain의 우위 (+6.5% SParC QM) 및 오류 전파 문제 실증

2. **Track-SQL** (NAACL 2025): "Enhancing Generative Language Models with Dual-Extractive Modules for Schema and Context Tracking in Multi-turn Text-to-SQL". DMIRLAB Group. https://arxiv.org/abs/2603.05996
   - 핵심 기여: 재작성 없는 구조화 히스토리 스토어로 SParC +7.1%, CoSQL +9.55% 달성

3. **PRACTIQ** (NAACL 2025): "A Practical Conversational Text-to-SQL dataset with Ambiguous and Unanswerable Queries". Amazon Science. https://arxiv.org/abs/2410.11076
   - 핵심 기여: 4-turn 구조 (원본 보존 + Q&A 컨텍스트)의 실용적 설계 패턴 제시

4. **Sphinteract** (VLDB 2025): "Resolving Ambiguities in NL2SQL Through User Interaction". UCSB/Microsoft Research. https://dl.acm.org/doi/10.14778/3717755.3717772
   - 핵심 기여: SRA 패러다임으로 명확화를 통해 최대 42% 정확도 향상 실증

5. **SiriusBI** (VLDB 2025): "A Comprehensive LLM-Powered Solution for Data Analytics in Business Intelligence". Tencent. https://www.vldb.org/pvldb/vol18/p4860-xie.pdf
   - 핵심 기여: 금융 도메인 프로덕션 시스템에서 구조화 대화 컨텍스트 유지 방식의 97% 정확도 달성

6. **AmbiSQL** (2025): "Interactive Ambiguity Detection and Resolution for Text-to-SQL". XiYan-SQL. https://arxiv.org/abs/2508.15276
   - 핵심 기여: Hybrid 전략 (구조화 Q&A + 선택적 terminal rewriting)으로 +50% SQL 정확도

7. **Long Context NL2SQL** (VLDB 2025): "Is Long Context All You Need? Leveraging LLM's Extended Context for NL2SQL". https://www.vldb.org/pvldb/vol18/p2735-ozcan.pdf
   - 핵심 기여: 구조화 힌트(명확화와 동치) 추가로 +16.36% 정확도 향상 실증

8. **Intent Scoping** (VLDB 2025 Workshop): "Intent Scoping and Paraphrasing for Robust NL2SQL". ETH Zurich/Zalando/IBM. https://www.vldb.org/2025/Workshops/VLDB-Workshops-2025/AIDB/AIDB25_5.pdf
   - 핵심 기여: 일반적 paraphrase의 NL2SQL 유해성 경고 (핵심 엔티티 소실 위험)

9. **VLDB 2025 NL2SQL Survey**: "Natural Language to SQL: State of the Art and Open Problems". Tsinghua University. https://dbgroup.cs.tsinghua.edu.cn/ligl/papers/VLDB25-NL2SQL.pdf
   - 핵심 기여: 대화형 명확화를 NL2SQL Open Problem으로 정식 분류

10. **DIR Dataset** (Applied Sciences 2023): "A Large-Scale Dialogue Rewrite Dataset for Cross-Domain Conversational Text-to-SQL". https://www.mdpi.com/2076-3417/13/4/2262
    - 핵심 기여: Oracle 재작성이 SParC +12%, CoSQL +27%를 보이지만, 자동 재작성 오류의 한계를 명시

11. **CQR-SQL** (EMNLP Findings 2022): "Conversational Question Reformulation Enhanced Context-Dependent Text-to-SQL Parsers". https://arxiv.org/abs/2205.07686
    - 핵심 기여: 스키마 인식 재작성이 순수 재작성보다 낫지만, 구조화 컨텍스트 접근의 등장 이전 세대 방법론

### Tier 2 (벤치마크 데이터셋)

- **CoSQL** (EMNLP 2019): https://yale-lily.github.io/cosql
- **SParC** (ACL 2019): Yale LILY Lab
- **CHASE**: 중국어 대화형 Text-to-SQL 최대 데이터셋
- **BIRD**: 비즈니스 인텔리전스 벤치마크 (힌트 포함)
