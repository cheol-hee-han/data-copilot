# 명확화 판정 아키텍처: LLM 판정 + 규칙 가드레일 2계층 설계

**작성일**: 2026-03-31
**상태**: 설계 확정, 전략 문서 반영 대기
**관련 문서**:
  - `docs/strategy-proposals/checkpointer-multi-turn/01-strategy.md` (기존 전략)
  - `docs/research/20260331-clarification-determination-in-nl2sql-agents.md` (트리거 기준 리서치)
  - `docs/research/20260330-hitl-clarification-unification.md` (HITL 통합 리서치)
  - `docs/reviews/design/20260331-clarification-policy-engine-review.md` (디자인 리뷰)

---

## 1. 문제 정의

### 1.1 핵심 질문

파이프라인에서 모호함을 발견했을 때, **사용자에게 질문할지(ASK) vs 추론해서 진행할지(INFER)**를 어떻게 결정하는가?

### 1.2 제약 조건

| 제약 | 설명 |
|---|---|
| 사용자 특성 | IT 비전문 은행 직원 — 과도한 질문은 피로도 유발 |
| 도메인 리스크 | 금융 — 산출식/테이블 선택 오답 시 규제 리스크 |
| 배포 환경 | 폐쇄망 — Solar Pro 2 70B / Qwen3.5 397B (Claude 대비 성능 제한) |
| 학술 근거 | 평균 2.18회 상호작용이 최적 (Sphinteract, VLDB 2025) |

### 1.3 설계 원칙

> **"문제해결에 꼭 필요한 것만 질문하고, 나머지는 추론 후 안내한다."**

- 모든 모호함에 질문 → 사용자 피로 (기각)
- 모든 모호함을 추론 → 금융 오답 리스크 (기각)
- 핵심만 질문 + 나머지 추론 + 추론 근거 안내 → **채택**

---

## 2. 아키텍처 결정 히스토리

| 단계 | 질문 | 결론 | 근거 |
|---|---|---|---|
| ① 노드 구조 | 분산 vs 집중? | 시그널 수집 분산, 판정 집중 (clarification_handler) | Sphinteract SRA, LangGraph interrupt() 규칙 |
| ② interrupt 방식 | 정적 vs 동적? | 동적 interrupt() | LangGraph 공식 권장 |
| ③ 컨텍스트 전달 | Rewriting vs Structured? | Structured Context Passing (원본 보존) | CoE-SQL +6.5% (NAACL 2024) |
| ④ 도메인 기본값 | 모든 모호성을 질문? | 업무 관행상 일반적이면 추론 후 안내 | PRACTIQ (NAACL 2025) |
| ⑤ 분류 체계 | 7종 유지? 폐기? | 7종 유지 (가드레일에 필요) | 유형별 맞춤 규칙 가능 |
| ⑥ 판정 주체 | 규칙? LLM? | LLM이 판정, 규칙은 가드레일 | LLM이 전체 맥락 보유, 규칙 단독은 맥락 손실 |
| ⑦ 유형 명칭 | AmbiSQL 원본? | 단일 영어 단어로 단순화 | LLM 오기(typo) 방지, 의미 직관성 |

---

## 3. 확정 설계: 2계층 구조

### 3.1 전체 흐름

```text
[각 노드 LLM] ─ 업무 수행 중 모호함 발견
  │  ① 감지: "후보 테이블이 2개 있다"
  │  ② 분류: "TABLE"
  │  ③ 판정: "맥락상 A가 맞을 것 같다 → INFER"
  │  ④ 근거: "월별 집계 테이블이 질의 의도에 부합"
  │
  │  → UncertaintySignal 생성, state.uncertainty_signals에 추가
  ▼
[가드레일] ─ 규칙 기반 보정 (LLM 호출 0)
  │  - INFER → ASK 단방향 보정만 수행
  │  - 유형별 맞춤 규칙 (7종 분류 활용)
  │  - ASK → INFER 변환은 절대 없음
  ▼
[clarification_handler 노드]
  ├─ 최종 INFER → auto_resolved 기록, 진행
  │   → 결과 상단에 추론 근거 안내
  └─ 최종 ASK → interrupt(ClarificationRequest)
      → 사용자 응답 → handler 검증 → ClarificationEntry 누적
      → return_to 노드 복귀
```

### 3.2 모호성 유형 분류 (7종)

AmbiSQL 논문(arXiv 2508.15276)의 7종 분류를 **단일 영어 단어로 재명명**한다. LLM이 JSON 출력 시 오기(typo)를 방지하고, 금융 도메인에서 의미가 즉시 통하도록 한다.

| Enum 값 | AmbiSQL 원본 | 정의 | 금융 도메인 예시 |
|---|---|---|---|
| `TABLE` | AmbiSchema | 테이블/컬럼 참조 모호 | "여신 잔액" → `LOAN_BAL_D` vs `LOAN_BAL_M` |
| `INTENT` | AmbiIntent | 의도/연산 방식 모호 | "이번 달 여신" → 신규 건수? 실행 금액? 잔액? |
| `VALUE` | AmbiValue | 코드값 매칭 실패 | "VIP 고객" → DB 코드값과 매핑 안 됨 |
| `FORMULA` | AmbiSource | 산출식 출처 모호 | "연체율" → 업무 매뉴얼 산출식? 일반식? |
| `TIMEFRAME` | AmbiRef | 기간/시점 모호 | "최근 실적" → 이번 달? 이번 분기? |
| `CONTEXT` | AmbiContext | 추론 근거 부족 | `STATUS_CD = '02'`의 의미 불명 |
| `CONFLICT` | AmbiFallacy | 모순된 전제 | 3년 데이터 요청 + 3개월 테이블만 존재 |

```python
class AmbiguityType(str, Enum):
    TABLE = "TABLE"
    INTENT = "INTENT"
    VALUE = "VALUE"
    FORMULA = "FORMULA"
    TIMEFRAME = "TIMEFRAME"
    CONTEXT = "CONTEXT"
    CONFLICT = "CONFLICT"
```

**명명 설계 근거**:
- 단일 영어 대문자 단어 → LLM JSON 출력에서 오기 가능성 최소화
- `AmbiSource` → `FORMULA`: 금융 도메인에서 "산출식"이 핵심이므로 의미 직결
- `AmbiRef` → `TIMEFRAME`: "기간 모호성"임을 즉시 전달
- `AmbiFallacy` → `CONFLICT`: "모순"보다 "충돌"이 프로그래밍 맥락에서 직관적

### 3.3 계층 1: LLM — 감지 + 분류 + 판정

각 노드의 LLM이 **자기 업무를 수행하는 과정에서** 모호함을 발견하면, 추가 LLM 호출 없이 같은 호출 안에서 시그널을 생성한다.

#### UncertaintySignal 스키마

```python
class ConfidenceLevel(str, Enum):
    """LLM의 판정 확신도. float 대신 이산값으로 제한하여 calibration 문제 완화."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class UncertaintySignal(BaseModel):
    """각 노드가 생성하는 모호성 시그널."""
    source_node: str                              # 발생 노드명
    ambiguity_type: AmbiguityType                  # 7종 분류
    decision: Literal["ASK", "INFER"]              # LLM의 판정
    confidence: ConfidenceLevel                    # 판정 확신도
    description: str                               # 무엇이 모호한지 (한국어)
    candidates: list[str]                          # 후보들 (테이블명, 코드값, 기간 등)
    inferred_value: str | None = None              # INFER 시 추론값
    reasoning: str = ""                            # 판정 근거 (한국어)
    override_reason: str | None = None             # 가드레일 보정 시 사유
```

**ConfidenceLevel을 float이 아닌 이산값으로 제한하는 이유**:
- LLM의 자기 확신도(self-calibration)는 부정확함 (arXiv 2508.14056)
- float 임계값(0.85 등)은 모델 교체 시마다 재튜닝 필요
- `HIGH/MEDIUM/LOW` 3단계는 LLM이 안정적으로 출력 가능
- 가드레일에서 `LOW` 시 ASK 강제로 단순화

#### LLM 프롬프트 판정 기준

각 노드의 시스템 프롬프트에 다음 기준을 포함한다:

```text
업무 수행 중 모호함을 발견하면, 다음 기준으로 ASK/INFER를 판정하세요.

[ASK — 사용자에게 질문 필요]:
- 추론이 틀리면 완전히 다른 데이터가 나오는 경우
- 산출식/지표 정의가 달라지는 경우
- 후보가 여러 개이고 맥락으로 좁힐 수 없는 경우

[INFER — 추론 후 진행]:
- 업무 관행상 일반적인 해석이 있는 경우
- 포괄적으로 조회하면 해소되는 경우 (컬럼 선택 등)
- 기간 등 기본값을 적용하고 안내하면 되는 경우

판단이 애매하면 ASK를 선택하세요.
```

**Few-shot 예시** (프롬프트에 포함):

```text
[예시 1]
질의: "이번 달 여신 실적 보여줘"
발견: "여신 실적"이 실행 금액/건수/잔액 중 어느 것인지 불명확
분류: INTENT
도메인 기본값: "여신 실적 = 실행 금액" (과거 SQL 이력 85%)
판정: INFER
확신도: HIGH
추론값: "실행 금액"
근거: "과거 SQL 이력에서 85%가 실행 금액으로 조회"

[예시 2]
질의: "연체율 계산해줘"
발견: 연체율 산출식이 여러 버전 존재 가능
분류: FORMULA
판정: ASK
확신도: LOW
근거: "산출식이 달라지면 결과가 완전히 달라지므로 확인 필요"

[예시 3]
질의: "지점별 대출 현황"
발견: 유사 테이블 2개 존재 (일별 잔액 vs 월말 기준)
분류: TABLE
도메인 기본값: 없음
판정: ASK
확신도: LOW
근거: "두 테이블의 데이터 범위와 갱신주기가 달라 용도 확인 필요"

[예시 4]
질의: "주요 컬럼 다 보여줘"
발견: 어떤 컬럼을 원하는지 불명확
분류: INTENT
판정: INFER
확신도: HIGH
추론값: "주요 컬럼 전체 포함"
근거: "포괄적으로 조회하면 해소 가능, 불필요한 컬럼은 사용자가 제외 요청 가능"
```

### 3.4 계층 2: 규칙 가드레일 — INFER→ASK 단방향 보정

LLM이 INFER로 판단한 시그널에 대해서만, **유형별 안전 규칙**으로 검증한다. ASK→INFER 변환은 절대 수행하지 않는다.

```python
def apply_guardrails(
    signal: UncertaintySignal,
    query_context: QueryContext,
) -> UncertaintySignal:
    """
    LLM의 INFER 판정을 유형별 규칙으로 검증.
    ASK → INFER 변환은 절대 없음 (안전 방향만 보정).
    """
    if signal.decision == "ASK":
        return signal  # LLM이 ASK로 판단한 것은 무조건 존중

    match signal.ambiguity_type:

        case AmbiguityType.FORMULA:
            # 산출식 출처 모호 → 무조건 질문 (금융 규제 리스크)
            signal.decision = "ASK"
            signal.override_reason = "산출식 관련 모호함은 추론 금지 (금융 규제)"

        case AmbiguityType.TABLE:
            # 후보 테이블 2개+ AND 확신도 LOW → 질문
            if len(signal.candidates) >= 2 and signal.confidence == ConfidenceLevel.LOW:
                signal.decision = "ASK"
                signal.override_reason = "테이블 선택 확신도 부족"

        case AmbiguityType.INTENT:
            # 의도 모호 AND 확신도 LOW → 질문
            if signal.confidence == ConfidenceLevel.LOW:
                signal.decision = "ASK"
                signal.override_reason = "의도 판정 확신도 부족"

        case AmbiguityType.VALUE:
            # ES 코드 매칭 실패 → 질문
            if not query_context.has_code_match:
                signal.decision = "ASK"
                signal.override_reason = "코드값 매칭 실패"

        case AmbiguityType.TIMEFRAME:
            # 기간 모호 + 산출식 연관 → 질문
            if query_context.involves_calculation:
                signal.decision = "ASK"
                signal.override_reason = "산출식 연관 기간 모호함"

        case AmbiguityType.CONTEXT | AmbiguityType.CONFLICT:
            # LLM 판정 존중
            pass

    return signal
```

#### 가드레일 설계 원칙

| 원칙 | 설명 |
|---|---|
| **단방향 보정** | INFER → ASK만 가능. ASK → INFER는 절대 불가. |
| **유형별 맞춤** | 7종 분류가 있으므로 유형별 검증 조건이 다름 |
| **구조적 조건** | LLM의 자기 평가가 아닌, 후보 수·매칭 결과·산출식 연관 등 객관적 데이터 사용 |
| **LLM 호출 0** | 순수 규칙이므로 latency/비용 추가 없음 |
| **점진적 강화** | 초기에는 핵심 규칙만, 운영 데이터 축적 후 규칙 추가 |

#### 가드레일 규칙 요약 매트릭스

| 유형 | 가드레일 조건 | 보정 방향 | 근거 |
|---|---|---|---|
| `FORMULA` | 무조건 | INFER → ASK | 산출식 오류는 금융 규제 리스크 |
| `TABLE` | 후보 2+ & confidence LOW | INFER → ASK | 데이터 원천이 달라짐 |
| `INTENT` | confidence LOW | INFER → ASK | 연산 방식이 완전히 달라짐 |
| `VALUE` | ES 코드 매칭 실패 | INFER → ASK | 코드값 없으면 추론 불가 |
| `TIMEFRAME` | 산출식 연관 | INFER → ASK | 산출 결과가 달라짐 |
| `CONTEXT` | - (LLM 존중) | - | 추론 근거 부족은 LLM이 가장 잘 판단 |
| `CONFLICT` | - (LLM 존중) | - | 모순 감지는 LLM이 가장 잘 판단 |

### 3.5 clarification_handler 노드 — 최종 처리

```python
async def clarification_handler_node(state: PipelineState) -> dict:
    """통합 명확화 노드. 가드레일 적용 → INFER/ASK 분리 → 처리."""
    signals = state.uncertainty_signals
    if not signals:
        return {}

    # 1. 가드레일 적용
    query_ctx = build_query_context(state)
    signals = [apply_guardrails(s, query_ctx) for s in signals]

    # 2. ASK/INFER 분리
    ask_signals = [s for s in signals if s.decision == "ASK"]
    infer_signals = [s for s in signals if s.decision == "INFER"]

    # 3. INFER → auto_resolved 기록
    auto_entries = [to_auto_resolved(s) for s in infer_signals]

    # 4. ASK 처리
    if ask_signals:
        # 우선순위: INTENT/FORMULA > TABLE/VALUE > TIMEFRAME > CONTEXT/CONFLICT
        best = select_by_priority(ask_signals)
        request = build_clarification_request(best)
        answer = interrupt(request.model_dump())
        entry = validate_and_record(answer, request)

        return {
            "clarifications": state.clarifications + [entry],
            "auto_resolved": state.auto_resolved + auto_entries,
            "uncertainty_signals": [],
        }

    # 5. 질문 없이 진행
    return {
        "auto_resolved": state.auto_resolved + auto_entries,
        "uncertainty_signals": [],
    }
```

### 3.6 ASK 시그널 우선순위

복수 ASK 시그널이 존재할 때, 의존 관계를 반영한 우선순위로 1개를 선택한다:

```text
1순위: INTENT / FORMULA  — 의도·산출식이 확정돼야 나머지가 의미 있음
2순위: TABLE / VALUE     — 테이블·코드값이 확정돼야 기간·컬럼이 결정됨
3순위: TIMEFRAME         — 기간은 기본값 적용 가능
4순위: CONTEXT / CONFLICT — 보조적 모호성
```

1개를 질문하고 답변을 받으면, 나머지 시그널이 자동 해소되는 경우가 많다 (예: INTENT 확정 → TABLE이 자동 결정). 해소되지 않은 시그널은 다음 라운드에서 다시 수집된다.

### 3.7 도메인 기본값의 역할

도메인 기본값 사전은 **규칙의 분기 조건이 아니라 LLM의 컨텍스트 힌트**로 제공한다.

#### 기본값 출처 및 로딩 전략

| 출처 | 로딩 시점 | 갱신 주기 |
|---|---|---|
| 수동 yaml (`resources/domain_defaults.yaml`) | 서버 시작 시 메모리 로드 | git 관리, 수동 업데이트 |
| 과거 SQL 이력 (PostgreSQL) | 일 1회 배치 집계 → Redis 캐시 | 자동 |
| 업무 매뉴얼 (Qdrant) | 시그널 평가 시 on-demand 검색 | RAG 인덱스 갱신 시 |

#### LLM 컨텍스트 주입 형식

```text
[도메인 기본값]
- "여신 실적" → 일반적으로 "실행 금액"을 의미 (출처: 과거 SQL 이력 85%)
- "최근" → 일반적으로 "최근 1개월" (출처: 업무 매뉴얼)
- "연체" → 일반적으로 "1개월 이상 연체" (출처: 업무 매뉴얼)
```

LLM이 이 컨텍스트를 참고하여 INFER 여부를 판단한다. 기본값이 있다고 무조건 INFER하는 것이 아니라, **질의 맥락과 기본값을 종합하여 LLM이 판단**한다.

### 3.8 보조 메커니즘

#### auto_resolved 안내

추론으로 진행한 항목을 결과 상단에 자연어로 안내한다:

```text
📋 조회 기준 안내:
- "여신 실적"은 실행 금액 기준으로 조회했습니다 (다른 기준을 원하시면 말씀해 주세요)
- 기간은 이번 달(2026년 3월) 기준입니다
```

#### 정정 임계값

auto_resolved 항목에 대한 사용자 정정이 **동일 세션 내 2회 이상** 발생하면, 해당 세션의 남은 모호성은 모두 ASK 모드로 전환한다. 이 임계값은 설정으로 관리한다.

#### DTE 패턴 (Detect-Then-Explain)

명확화 질문에 "왜 묻는지"를 포함한다 (ACL 2023 Findings):

```text
좋은 예: "정보계에 유사한 테이블이 두 개 있어서 확인이 필요합니다:
         1) 일별 잔액 테이블 (매일 갱신)
         2) 월말 기준 잔액 테이블 (월 1회 갱신)
         어느 쪽이 필요하신가요?"

나쁜 예: "LOAN_BAL_D와 LOAN_BAL_M 중 어떤 테이블을 사용할까요?"
```

#### PRACTIQ 억제

Ambiguous SELECT/WHERE Column 유형은 명확화 대신 **포괄 조회 SQL을 반환**한다 (NAACL 2025). "어떤 컬럼을 원하세요?"를 묻는 대신 가능한 컬럼을 모두 SELECT한다.

#### 감사 추적

금융 규제 대응을 위해 clarification, auto_resolved, correction을 통합 스키마로 기록한다:

```python
class AuditEntry(BaseModel):
    entry_type: Literal["clarification", "auto_resolved", "correction"]
    timestamp: datetime
    ambiguity_type: AmbiguityType
    resolution: str
    resolution_source: str   # "user_answer" | "domain_default" | "inferred" | "user_correction"
    original_auto_value: str | None = None  # 정정 시: 원래 추론값
```

---

## 4. 비판적 검토

### 4.1 LLM 프롬프트 기반 판정의 메타인지 한계

**우려**: "추론이 틀리면 완전히 다른 데이터가 나오는 경우 → ASK"는 LLM이 "틀리면 어떻게 되는지"를 판단할 수 있어야 한다. 폐쇄망 70B 모델의 메타인지 능력은 제한적이다.

**완화**:
1. 추상적 원칙이 아닌 **few-shot 예시**로 판단 기준을 구체화
2. 가드레일이 고위험 유형(FORMULA 무조건 ASK, TABLE confidence LOW → ASK)을 잡아줌
3. 프롬프트 마지막에 "판단이 애매하면 ASK" — 안전 방향 디폴트

**잔존 리스크**: few-shot으로 커버 안 되는 edge case. 가드레일이 치명적 오답은 방지하므로 수용 가능.

### 4.2 ConfidenceLevel 이산값의 정보 손실

**우려**: `HIGH/MEDIUM/LOW` 3단계가 float 대비 정보를 잃는다.

**판단**: 정보 손실보다 **안정성 확보가 더 중요**하다.
- LLM의 float confidence는 모델마다 scale이 다르고 calibration이 부정확 (arXiv 2508.14056)
- 모델 교체 시(Solar → Qwen) float 임계값을 재튜닝해야 하지만, 이산값은 그대로 사용 가능
- 가드레일이 `LOW` 여부만 확인하므로 3단계로 충분

### 4.3 시그널 1개 선택 시 나머지 시그널의 처리

**우려**: ASK 시그널이 2개인데 1개만 질문하면, 나머지는 어떻게 되는가?

**설계**:
1. 1개 질문 → 사용자 답변 → clarification_handler 종료 → return_to 노드 복귀
2. 복귀한 노드가 답변을 반영하여 재실행
3. 재실행 과정에서 나머지 모호성이 해소되었으면 → 시그널 미발생
4. 해소되지 않았으면 → 새 시그널 발생 → 다시 clarification_handler → 2라운드 질문

이 방식은 Sphinteract의 2.18회 평균 상호작용과 일치한다.

### 4.4 기존 01-strategy.md와의 관계

이 설계는 01-strategy.md를 **교체하지 않고 확장**한다:

```text
01-strategy.md (기존)               이 설계 (추가)
──────────────────────────         ──────────────────────────
각 노드 → pending_clarification     각 노드 → uncertainty_signals
    (무조건 질문)                         (ASK/INFER 판정 포함)
                                             ↓
                                    apply_guardrails()
                                             ↓
                                    ASK → pending_clarification (기존 합류)
                                    INFER → auto_resolved

clarification_handler → interrupt()       변경 없음
HandlerRegistry → 응답 검증         변경 없음
ClarificationEntry → 이력 누적      변경 없음
```

변경 요약:
- 각 노드가 `pending_clarification` 직접 세팅 → `uncertainty_signals`에 시그널 추가로 변경
- clarification_handler 진입부에 가드레일 판정 로직 추가
- `auto_resolved` State 필드 추가
- `UncertaintySignal`, `AmbiguityType`, `ConfidenceLevel` 스키마 추가

---

## 5. 구현 우선순위

### Phase 2A (01-strategy Phase 2와 함께, 필수)

| 항목 | 설명 |
|---|---|
| `AmbiguityType` Enum | 7종 분류 정의 |
| `ConfidenceLevel` Enum | HIGH/MEDIUM/LOW |
| `UncertaintySignal` 스키마 | 시그널 데이터 모델 |
| `uncertainty_signals` State 필드 | PipelineState에 추가 |
| `auto_resolved` State 필드 | PipelineState에 추가 |
| 각 노드 프롬프트 확장 | ASK/INFER 판정 기준 + few-shot + 7종 분류 |
| `apply_guardrails()` 기본 규칙 | FORMULA 무조건 ASK, TABLE/INTENT confidence LOW → ASK |
| clarification_handler 진입부 확장 | 가드레일 적용 → ASK/INFER 분리 |

### Phase 2B (안정화 후, 개선)

| 항목 | 설명 |
|---|---|
| 도메인 기본값 yaml 구축 | `resources/domain_defaults.yaml` 초기 시딩 |
| 도메인 기본값 LLM 컨텍스트 주입 | 각 노드 프롬프트에 기본값 포함 |
| 가드레일 규칙 세분화 | VALUE 코드 매칭, TIMEFRAME 산출식 연관 |
| 정정 임계값 | auto_resolved 정정 2회+ → ASK 전환 |
| `AuditEntry` 통합 스키마 | 감사 추적 일원화 |
| 시그널 우선순위 | INTENT/FORMULA > TABLE/VALUE > TIMEFRAME |

---

## 6. 학술 근거 요약

| 출처 | 기여 | 본 설계 반영 |
|---|---|---|
| **Sphinteract** (VLDB 2025) | SRA 패러다임, +42% 정확도, 2.18회 최적 | 집중형 판정, 1개 질문 선택 |
| **AmbiSQL** (arXiv 2508.15276) | 7종 모호성 분류 체계 | 유형 분류 (명칭 단순화) |
| **EIG** (arXiv 2507.06467) | 기대정보이득 기반 질문 선택 | 우선순위 기반 1개 선택으로 근사 |
| **PRACTIQ** (NAACL 2025) | SELECT/WHERE 모호성은 포괄 조회가 효과적 | PRACTIQ 억제 규칙 |
| **DTE** (ACL 2023) | "왜 묻는지" 설명 포함 | 질문에 이유 포함 |
| **CoE-SQL** (NAACL 2024) | Structured Context > Rewriting (+6.5%) | 원본 보존 + Q&A 분리 |
| **DASG** (arXiv 2508.05061) | VoC > CoD 비용 모델 | 장기 옵션 (온라인 환경) |
| **Confidence Estimation** (arXiv 2508.14056) | LLM self-calibration 부정확 | ConfidenceLevel 이산값 채택 |
