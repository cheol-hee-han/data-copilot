# Checkpointer 도입 및 멀티턴 상호작용 설계 전략

- **작성일**: 2026-03-30
- **최종 갱신**: 2026-03-31 (v4: 2계층 판정 + AmbiSQL 7종 분류 + 가드레일 통합)
- **상태**: 설계 완료, 구현 대기
- **영향 범위**: `pipeline.py`, `runner.py`, `state.py`, `main.py`, `session/`, `config.py`, `response_formatter.py`
- **참조 리서치**:
  - `docs/research/20260330-langgraph-checkpointer-architecture.md`
  - `docs/research/20260330-hitl-clarification-unification.md`
  - `docs/research/20260330-clarification-context-management.md`
  - `docs/research/20260331-clarification-determination-in-nl2sql-agents.md` (트리거 기준 리서치)
  - `docs/research/20260331-clarification-judgment-architecture.md` (판정 아키텍처 설계)

---

## 1. 현황 분석 및 문제 정의

### 1.1 현재 아키텍처의 한계

| 영역 | 현재 구현 | 한계 |
| ---- | --------- | ---- |
| **상태 영속성** | 없음 (`workflow.compile()` — checkpointer 미사용) | 파이프라인 실행 중 서버 재시작 시 전체 상태 소멸 |
| **명확화 흐름** | `clarify → END` + 재호출 패턴 | 매번 전체 파이프라인 재실행 (불필요한 LLM 호출 2~3회) |
| **명확화 상태** | SessionStore에 별도 저장 (`session:{sid}:clarify`) | 그래프 상태와 분리되어 동기화 위험 |
| **명확화 트리거** | 5곳에 분산, 각각 다른 패턴 | 프론트엔드가 케이스별 분기 처리 필요, 재진입 지점 불명확 |
| **명확화 컨텍스트** | 원본 질의를 재작성하거나 무시 | 의미 뭉개짐, 감사 추적 불가 |
| **보안 검증** | main.py + preprocess_node 이중 실행 | `detect_prompt_injection`이 2회 중복 호출 |
| **오류 복구** | 없음 | Reason 계층 탐색 루프 중 네트워크 에러 시 전체 재시작 |
| **이중 세션 관리** | SessionStore (history + clarify) ≠ Graph state | 두 시스템 간 정보 불일치 가능성 |

### 1.2 도입 목표

1. **멀티턴 명확화**: `interrupt()` 기반 Unified Clarification Framework로 5개 트리거를 단일 패턴으로 통합
2. **2계층 판정**: LLM이 ASK/INFER를 판정하고, 규칙 가드레일이 단방향(INFER→ASK) 보정 — 과도한 질문 방지 + 금융 안전성 확보
3. **컨텍스트 보존**: Structured Context Passing — 원본 질의 보존 + 명확화 Q&A 독립 누적
4. **오류 복구**: 노드 실패 시 마지막 성공 체크포인트에서 재개
5. **세션 통합**: 그래프 체크포인터가 세션 상태의 단일 진실 공급원
6. **감사 추적**: 원본 질의 + 명확화/자동추론 근거 + 생성 SQL 전체 기록 (금융 규제 대응)

---

## 2. 아키텍처 결정

### 2.1 Checkpointer 선택: AsyncPostgresSaver

| 환경 | 선택 | 근거 |
| ---- | ---- | ---- |
| **개발/테스트** | `MemorySaver` | 외부 의존성 없음, pytest 즉시 사용 |
| **온라인 프로덕션** | `AsyncPostgresSaver` | 분산 배포, 완전 영속성, 감사 쿼리 가능 |
| **폐쇄망 배포** | `AsyncPostgresSaver` | PostgreSQL 이미 구축됨, 외부 네트워크 불필요 |

**기각된 대안**:

- `AsyncRedisSaver`: Redis 8.0+ 필요, time-travel 제약, 세션 캐시와 혼재
- `SqliteSaver`: FastAPI 다중 워커 환경에서 파일 잠금 충돌
- `ShallowRedisSaver`: 최신 1개만 저장 → 오류 복구/time-travel 불가

#### 비판적 검토 #1: PostgresSaver 도입 시 추가 DB 필요 여부

> **문제**: info_db는 읽기 전용, history_db는 SQL 이력용. 체크포인트 테이블을 어디에?
>
> **결론**: history_db에 체크포인트 테이블을 공존시킨다.
> - history_db는 이미 읽기/쓰기 가능한 DB
> - 체크포인트 3개 테이블(`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`)은 독립적 PK 구조라 기존 테이블과 충돌 없음
> - 별도 DB를 만들면 커넥션 풀이 늘어나 폐쇄망 리소스 부담
> - 운영 모니터링 편의를 위해 별도 스키마(`checkpoint`) 사용 검토

### 2.2 멀티턴 전략: 순수 interrupt()

**모든 명확화를 interrupt()로 통일**한다. 하이브리드(Shortcut + interrupt) 기각.

| 계층 | 상황 | 방식 | 근거 |
| ---- | ---- | ---- | ---- |
| **Interpret** | T1~T3 명확화 | `interrupt()` | 단일 패턴, clarification_handler 노드 경유 |
| **Reason** | T4~T5 CONFLICTED/Cross-DB | `interrupt()` | 탐색 상태 보존, 단일 패턴 |

#### 보안 검증: preprocess 노드 제거 → runner.py sanitize

기존 보안 검증 구조를 정리하고 **의도적 2계층 방어(Defense in Depth)**로 재정의한다:

- **계층 1 (main.py)**: `detect_prompt_injection()` — WebSocket/REST 핸들러 진입 시점의 빠른 거부(early reject). 명백한 인젝션 패턴을 그래프 실행 전에 즉시 차단한다. **제거하지 않는다.**
- **계층 2 (runner.py)**: `sanitize()` — 유니코드 정규화, 길이 제한, 포괄적 보안 검증. preprocess 노드에서 이동하여 `run_pipeline()` 진입 시 1회 실행.

두 계층은 역할이 다르므로 단순 중복이 아닌 **심층 방어**다. 계층 1은 웹 인터페이스 전용 빠른 거부, 계층 2는 runner.py를 통한 모든 실행 경로(직접 호출, interrupt resume 포함)를 포괄하는 완전한 검증이다.

interrupt resume 시에도 `run_pipeline()`을 경유하므로 계층 2 보안 검증이 자동 적용된다.

```text
main.py → run_pipeline(user_input) → sanitize(user_input)
                                    → interrupt 대기 중? → Command(resume=sanitized)
                                    → 새 턴?             → ainvoke(initial_state)
```

#### 기각된 대안: 하이브리드 (Checkpoint + Shortcut)

> **기각 사유**:
> 1. `_route_after_preprocess` 숏컷 분기 + `clarification_origin` 추적 → 구현 복잡도 증가
> 2. 이미 main.py에서 `detect_prompt_injection()`을 호출하고 있어 preprocess와 이중화
> 3. 계층별 다른 패턴 → 유지보수 부담
> 4. LangGraph 표준 interrupt() 패턴에서 이탈

#### 비판적 검토 #2: 직접 호출 시 보안 우회

> **문제**: 서버를 거치지 않고 `run_pipeline()`을 직접 호출하면?
>
> **해결**: `run_pipeline()` 내부에서 `sanitize()` 실행.
> 이미 sanitize된 입력이면 no-op 수준 (유니코드 이미 정규화, 인젝션 패턴 없음 → 즉시 통과).
> 방어적 프로그래밍이지 비효율적 이중화가 아님.

### 2.3 Unified Clarification Framework + 2계층 판정

5곳에 분산된 명확화 로직을 **단일 `clarification_handler` 노드 + `AmbiguitySignal` 단일 모델**로 통합하고,
**LLM 판정 + 규칙 가드레일 2계층 구조**로 "질문할지(ASK) vs 추론해서 진행할지(INFER)"를 결정한다.

> **설계 원칙**: "문제해결에 꼭 필요한 것만 질문하고, 나머지는 추론 후 안내한다."
> - 모든 모호함에 질문 → 사용자 피로 (기각)
> - 모든 모호함을 추론 → 금융 오답 리스크 (기각)
> - **핵심만 질문 + 나머지 추론 + 추론 근거 안내** → 채택

#### 5개 트리거의 통합 경로

| # | 트리거 | 계층 | 응답 형태 | source_node |
| - | ------ | ---- | --------- | ----------- |
| T1 | history_resolver UNSURE | Interpret | FREE_TEXT | resolve_history |
| T2 | classify_intent AMBIGUOUS | Interpret | FREE_TEXT | normalize_query |
| T3 | normalize_query ambiguities | Interpret | FREE_TEXT | normalize_query |
| T4 | sql_generator Cross-DB | Reason | INFER (DB코드 추론) | sql_generator |
| T5 | confidence_evaluator CONFLICTED | Reason | SINGLE_SELECT | confidence_evaluator |

#### T4 특수 처리: Cross-DB는 항상 INFER

Cross-DB 상황(정보계 PostgreSQL과 폐쇄망 Sybase IQ/Impala 중 어느 DB를 조회할지)은 사용자가 답변할 수 없는 시스템 내부 정보에 해당한다. 일반 직원은 DB 아키텍처를 알지 못하므로 질문이 의미 없다.

대신 **테이블명 시스템코드(접두사/접미사 패턴)를 기반으로 추론**하고, 결과 출력 시 어느 DB에서 추출했는지 안내한다.

**추론 우선순위**:

1. **테이블명 접두사/접미사 패턴**: ES 메타에 등록된 테이블의 `data_source` 또는 `system_code` 필드로 DB 귀속 판단 (예: `IQ_`, `IMP_` 접두사 → 폐쇄망, 나머지 → PostgreSQL info_db)
2. **과거 SQL 이력**: 동일 테이블을 조회한 이력 쿼리의 DB 접속 정보 참조
3. **우선 접근 가능한 DB**: 현재 실행 환경에서 접속 가능한 DB를 우선 선택 (온라인: PostgreSQL, 폐쇄망: Sybase IQ/Impala)

추론 결과는 `AmbiguitySignal(decision="INFER")`로 생성하며, 결과 출력 시 아래와 같이 안내한다:

```text
📋 조회 기준 안내:
- 정보계 데이터베이스(PostgreSQL)에서 조회했습니다 (LOAN_BAL_D 테이블)
  (다른 시스템의 데이터가 필요하시면 말씀해 주세요)
```

가드레일 규칙에서 T4는 **INFER→ASK 보정을 적용하지 않는다** — 사용자에게 DB 선택을 묻는 것은 IT 비전문 사용자 원칙에 위배된다.

#### 2계층 판정 구조

```text
[각 노드 LLM] ─ 업무 수행 중 모호함 발견
  │  ① 감지: "후보 테이블이 2개 있다"
  │  ② 분류: AmbiguityType (7종)
  │  ③ 판정: ASK / INFER
  │  ④ 근거: "월별 집계 테이블이 질의 의도에 부합"
  │
  │  → AmbiguitySignal 생성 → state.pending_signals에 추가
  ▼
[clarification_handler 노드]
  │  → _should_override_to_ask(): INFER→ASK 단방향 보정 (LLM 호출 0)
  │  → ASK/INFER 분리
  │
  ├─ INFER → resolved_signals에 누적, 진행 (결과 상단에 추론 근거 안내)
  └─ ASK → 우선순위 1개 선택 → interrupt(signal) → 사용자 응답 → validate_answer() 검증
```

#### 모호성 7종 분류 (AmbiSQL 기반, 명칭 단순화)

AmbiSQL 논문(arXiv 2508.15276)의 분류 체계를 금융 도메인에 맞게 재명명한다.

| Enum 값 | AmbiSQL 원본 | 정의 | 금융 예시 |
|---|---|---|---|
| `TABLE` | AmbiSchema | 테이블/컬럼 참조 모호 | "여신 잔액" → `LOAN_BAL_D` vs `LOAN_BAL_M` |
| `INTENT` | AmbiIntent | 의도/연산 방식 모호 | "이번 달 여신" → 건수? 금액? 잔액? |
| `VALUE` | AmbiValue | 코드값 매칭 실패 | "VIP 고객" → DB 코드값 매핑 안 됨 |
| `FORMULA` | AmbiSource | 산출식 출처 모호 | "연체율" → 업무 매뉴얼 산출식? 일반식? |
| `TIMEFRAME` | AmbiRef | 기간/시점 모호 | "최근 실적" → 이번 달? 이번 분기? |
| `CONTEXT` | AmbiContext | 추론 근거 부족 | `STATUS_CD = '02'`의 의미 불명 |
| `CONFLICT` | AmbiFallacy | 모순된 전제 | 3년 데이터 요청 + 3개월 테이블만 존재 |

#### 가드레일 규칙 매트릭스

| 유형 | 가드레일 조건 | 보정 방향 | 근거 |
|---|---|---|---|
| `FORMULA` | 무조건 | INFER → ASK | 산출식 오류는 금융 규제 리스크 |
| `TABLE` | 후보 2+ & confidence LOW | INFER → ASK | 데이터 원천이 달라짐 |
| `INTENT` | confidence LOW | INFER → ASK | 연산 방식이 완전히 달라짐 |
| `VALUE` | ES 코드 매칭 실패 | INFER → ASK | 코드값 없으면 추론 불가 |
| `TIMEFRAME` | 산출식 연관 | INFER → ASK | 산출 결과가 달라짐 |
| `CONTEXT` | LLM 존중 | - | 추론 근거 부족은 LLM이 가장 잘 판단 |
| `CONFLICT` | LLM 존중 | - | 모순 감지는 LLM이 가장 잘 판단 |

#### ASK 시그널 우선순위

복수 ASK 시그널이 존재할 때 **1개만 선택**하고, 나머지는 source_node 복귀 후 재수집한다 (1개씩 순차 해소, Sphinteract 평균 2.18회와 일치):

```text
1순위: INTENT / FORMULA  — 의도·산출식이 확정돼야 나머지가 의미 있음
2순위: TABLE / VALUE     — 테이블·코드값이 확정돼야 기간·컬럼이 결정됨
3순위: TIMEFRAME         — 기간은 기본값 적용 가능
4순위: CONTEXT / CONFLICT — 보조적 모호성
```

#### 불필요한 명확화 억제 (PRACTIQ, NAACL 2025)

Ambiguous SELECT/WHERE Column 유형은 명확화 대신 **포괄 조회 SQL을 반환**한다.
"어떤 컬럼을 원하세요?"를 묻는 대신 가능한 컬럼을 모두 SELECT한다.

#### DTE 패턴: 명확화 질문에 "왜 묻는지" 포함 (ACL 2023 Findings)

IT 비전문 사용자에게 "왜 확인이 필요한지"를 자연어로 설명한다:
- **좋은 예**: "정보계에 유사한 테이블이 두 개 있어서 확인이 필요합니다: 1) 일별 잔액 테이블 (매일 갱신) 2) 월말 기준 잔액 테이블 (월 1회 갱신) — 어느 쪽이 필요하신가요?"
- **나쁜 예**: "LOAN_BAL_D와 LOAN_BAL_M 중 어떤 테이블을 사용할까요?"

#### 도메인 기본값 — LLM 컨텍스트 힌트

도메인 기본값 사전은 규칙의 분기 조건이 아니라 **LLM의 컨텍스트 힌트**로 제공한다:

| 출처 | 로딩 시점 | 갱신 주기 |
|---|---|---|
| `resources/domain_defaults.yaml` | 서버 시작 시 | git 관리, 수동 |
| 과거 SQL 이력 (PostgreSQL) | 일 1회 배치 → Redis 캐시 | 자동 |
| 업무 매뉴얼 (Qdrant) | 시그널 평가 시 on-demand | RAG 갱신 시 |

#### INFER 자동추론 안내

추론으로 진행한 항목을 결과 상단에 자연어로 안내한다:
```text
📋 조회 기준 안내:
- "여신 실적"은 실행 금액 기준으로 조회했습니다 (다른 기준을 원하시면 말씀해 주세요)
- 기간은 이번 달(2026년 3월) 기준입니다
```

#### 핵심 구성요소

1. **`AmbiguitySignal`**: 모호성의 전체 생명주기(감지→가드레일→ASK/INFER→해소)를 **단일 모델**로 관리 — `AmbiguityType`(7종) + `ConfidenceLevel`(HIGH/MEDIUM/LOW) + ASK/INFER 판정 + 근거 + 사용자 응답(`answer`) + 해소 시점(`resolved_at`). 별도 `AuditEntry` 없이 하나의 객체에 감지~해소 전 과정이 기록됨 (금융 감사 추적).
2. **`_should_override_to_ask()`**: 규칙 기반 단방향 보정 (INFER→ASK only, LLM 호출 0) — `clarification_handler` 노드 내부에 인라인.
3. **`validate_answer()`**: 사용자 응답 검증/정규화 단일 함수 — `question_type`에 따른 분기만 수행 (ABC/Strategy 패턴 불필요).
4. **`clarification_handler` 노드**: 가드레일 적용 → ASK/INFER 분리 → interrupt()를 **항상 1회만** 호출 (LangGraph 인덱스 규칙 준수). 프론트엔드는 interrupt 페이로드의 `question_type`으로 자동 UI 렌더링.

#### 비판적 검토 #3: interrupt() 인덱스 규칙

> LangGraph 공식 문서: "interrupt calls should happen in the same order every time, and you should not conditionally skip interrupt calls within a node."
>
> 조건부 다중 interrupt()는 인덱스 불일치를 유발한다. 따라서 clarification_handler 노드에서 항상 단일 interrupt()만 호출하고, ASK 시그널이 복수일 때 우선순위로 1개만 선택한다.

#### 비판적 검토 #3-2: LLM 메타인지 한계 (폐쇄망 모델)

> **우려**: ASK/INFER 판정은 LLM이 "틀리면 어떻게 되는지"를 판단할 수 있어야 한다. Solar Pro 2 70B의 메타인지 능력은 제한적.
>
> **완화**:
> 1. 추상적 원칙이 아닌 **few-shot 예시**(3개+)로 판단 기준을 구체화
> 2. 가드레일이 고위험 유형(FORMULA 무조건 ASK, TABLE confidence LOW → ASK)을 잡아줌
> 3. 프롬프트 마지막에 "판단이 애매하면 ASK" — 안전 방향 디폴트
>
> **잔존 리스크**: few-shot 커버 안 되는 edge case. 가드레일이 치명적 오답을 방지하므로 수용 가능.

#### 비판적 검토 #3-3: ConfidenceLevel 이산값의 정보 손실

> **판단**: 정보 손실보다 **안정성 확보가 더 중요**.
> - LLM의 float confidence는 모델마다 scale이 다르고 calibration이 부정확 (arXiv 2508.14056)
> - 모델 교체 시(Solar → Qwen) float 임계값 재튜닝 필요 없이 이산값 그대로 사용 가능
> - 가드레일이 `LOW` 여부만 확인하므로 3단계로 충분

#### 주의: 시그널 생성-처리 타이밍 규칙

> 각 노드의 라우팅 함수(`_route_after_*`)는 `pending_signals`가 존재하면 **즉시 clarification_handler로 분기**한다.
> 따라서 clarification_handler에 도착하는 시그널은 항상 **단일 노드 기원**이다 — 복수 노드의 시그널이 혼재되는 상황은 설계상 발생하지 않는다.
>
> **구현 시 주의**: 각 노드의 라우팅 함수에서 이 검사를 누락하면 시그널이 후속 노드로 누수되어 복수 노드의 시그널이 섞일 수 있다. 방어적 조치로, clarification_handler 진입 시 `source_node`가 혼재되어 있으면 경고 로그를 남기고 상위 계층(Interpret > Reason 순) 노드의 시그널을 우선 처리한다.

#### 참조

- LangGraph 공식 Interrupts 문서
- Sphinteract SRA 패러다임 (PVLDB 2025, 정확도 +42%, 평균 2.18회 상호작용 최적)
- AmbiSQL 7종 모호성 분류 체계 (arXiv 2508.15276)
- DTE Detect-Then-Explain 패턴 (ACL 2023 Findings)
- PRACTIQ 불필요 명확화 억제 (NAACL 2025)
- EIG 기대정보이득 기반 질문 선택 (arXiv 2507.06467)
- LLM Confidence Estimation 부정확성 (arXiv 2508.14056)
- DASG 비용 모델 (arXiv:2508.05061) — VoC > CoD 일 때만 명확화 트리거

### 2.4 명확화 컨텍스트: Structured Context Passing

**원본 질의(`original_query`)는 절대 수정하지 않는다** (immutable).
명확화 Q&A는 `resolved_signals` 리스트에 `AmbiguitySignal`로 독립 누적한다.

#### 기각된 대안: Query Rewriting

> **기각 근거** (학술 10편 + 프로덕션 3개 시스템의 수렴적 증거):
>
> 1. **오류 전파**: ACT-SQL(Rewriting) 대비 CoE-SQL(구조화 체인)이 SParC +6.5% — "error propagation in question rewriting" 실증 (NAACL 2024)
> 2. **엔티티 소실**: "generic paraphrases detrimental for NL2SQL — may alter critical entities" (VLDB 2025, ETH/Zalando/IBM)
> 3. **감사 불가**: 원본 질의 소실로 금융 규제 감사 요건 충족 불가
> 4. **다중 라운드 취약**: 재작성 오류가 라운드마다 선형 누적
> 5. **프로덕션 검증**: SiriusBI(Tencent)가 구조화 컨텍스트로 금융 도메인 97% 정확도 달성 (VLDB 2025)

#### 복귀 노드 공통 컨텍스트 전달 방식

명확화 후 복귀하는 **모든 source_node 노드**는 LLM 프롬프트에 명확화 Q&A를 구조화된 섹션으로 주입한다.
이를 통해 handler가 ReasoningState를 직접 조작하지 않아도, LLM이 사용자 답변을 참조하여 상태를 스스로 재판단한다.

원본 + Q&A를 **분리된 프롬프트 섹션**으로 전달:

```text
[사용자 원본 질의]
여신 데이터 뽑아줘

[명확화 대화]
라운드 1:
  질문: 어떤 여신 데이터를 원하시나요? 1) 신규 여신 건수 2) 여신 실행 금액
  답변: 신규 여신 실행 금액이요

[스키마 컨텍스트]
...
```

> **적용 범위**: sql_generator뿐 아니라 confidence_evaluator, normalize_query 등
> 명확화 후 복귀하는 모든 노드에 동일 패턴을 적용한다.
> 공통 유틸 `build_clarification_context(state)` 함수로 섹션을 생성한다.

#### 현재 코드의 문제

- `synthesize_clarification()` — 정의만 되고 **호출되지 않음** (미구현 상태)
- `clarification_response` 필드 — 상태에 정의만 되고 **아무 곳에서도 채워지지 않음**
- 명확화 응답이 `user_input`으로 들어와 **새 질의로 취급**되어 전체 파이프라인 재실행

### 2.5 세션 관리 전략

| 컴포넌트 | 역할 (변경 후) |
| -------- | -------------- |
| **Checkpointer** | 그래프 상태의 단일 진실 공급원 (명확화 상태 포함) |
| **SessionStore** | conversation_history 관리 (현재 구조 유지), clarify 상태는 제거 |

- `conversation_history`는 현재 SessionStore 구조 그대로 유지
- 명확화 상태(`session:{sid}:clarify`)는 체크포인터가 대체 → SessionStore에서 제거
- 장기적으로 SessionStore를 경량 SessionIndex로 축소

### 2.6 State 스키마 변경 전략

#### 핵심 결정: Pydantic BaseModel 유지

- 15개 노드 + 30개 이상의 서브타입이 Pydantic 기반
- TypedDict 전환은 비용 대비 이점이 부족
- LangGraph의 `jsonplus` SerDe가 Pydantic v2를 네이티브 지원

#### 비판적 검토 #4: Pydantic State 직렬화 안전성

> **문제**: `normalized_query: Any = None` 필드. `Any` 타입은 직렬화 시 예측 불가.
>
> **해결**: `NormalizedQuery(BaseModel)`이므로 `NormalizedQuery | None`으로 타입 명시화.

#### 새로 추가할 State 필드

> **설계 원칙 (R-02/R-03/R-05 반영)**: 8개 필드를 3개로 축소하고, 커스텀 reducer를 제거한다.
> `AmbiguitySignal` 단일 모델이 생명주기 전체를 관리하므로 별도 `ClarificationEntry`, `AutoResolvedEntry`, `ClarificationRequest` 불필요.

```python
from typing import Annotated
import operator

class PipelineState(BaseModel):
    # ... 기존 필드 ...

    # [신규] 원본 질의 (immutable, 명확화 시에도 수정 금지)
    original_query: str = ""

    # [신규] 처리 대기 중인 모호성 시그널 (일반 필드, 덮어쓰기)
    pending_signals: list[AmbiguitySignal] = Field(default_factory=list)

    # [신규] 해소 완료된 모호성 시그널 (reducer: 자동 append)
    resolved_signals: Annotated[list[AmbiguitySignal], operator.add] = Field(default_factory=list)

    # [제거] clarifications — resolved_signals에서 도출 [s for s in resolved_signals if s.decision == "ASK"]
    # [제거] pending_clarification — interrupt 페이로드가 역할 대체
    # [제거] clarification_return_to — resolved_signals[-1].source_node에서 도출
    # [제거] selected_db_source — 복귀 노드 LLM이 resolved_signals에서 참조
    # [제거] user_schema_selection — 복귀 노드 LLM이 resolved_signals에서 참조
    # [제거] uncertainty_signals — pending_signals로 대체 (커스텀 reducer 제거)
    # [제거] auto_resolved — [s for s in resolved_signals if s.decision == "INFER"]로 도출
```

> **Annotated reducer 검증 완료** (LangGraph 1.1.2, Pydantic v2):
> - `pending_signals` — 일반 필드(덮어쓰기). clarification_handler가 처리 후 `[]`로 초기화.
> - `resolved_signals` — `operator.add` 누적 전용. 노드가 `[new]`만 반환 → 자동 append.
> - 커스텀 reducer(`_add_or_clear`) **사용하지 않음** — LangGraph 표준 패턴만 사용.
> - 수동 append(`list(state.field) + [new]`) 불필요 — 누락 시 데이터 소실 위험 제거.

---

## 3. 계층별 책임 구조

```text
┌───────────────────────────────────────────────────┐
│  main.py (서버 계층)                                │
│  WebSocket/REST 수신 → run_pipeline() 호출 → 전송  │
│  그래프 내부(interrupt, checkpointer)를 모름         │
└────────────────────┬──────────────────────────────┘
                     │
┌────────────────────▼──────────────────────────────┐
│  runner.py (오케스트레이션)                          │
│  sanitize() 1회 → interrupt 감지(aget_state)       │
│  → Command(resume=) 또는 ainvoke(initial_state)    │
│  → PipelineResult 반환                             │
└────────────────────┬──────────────────────────────┘
                     │
┌────────────────────▼──────────────────────────────┐
│  graph (순수 비즈니스 로직)                          │
│  preprocess 노드 없음                               │
│  5개 트리거 → AmbiguitySignal 생성                 │
│  → clarification_handler (가드레일 + ASK/INFER 분리)     │
│    ├─ INFER → resolved_signals 누적, 진행          │
│    └─ ASK → interrupt() → validate_answer() 검증   │
│  → source_node 노드 복귀                            │
└───────────────────────────────────────────────────┘
```

---

## 4. 상세 흐름 설계

### 4.1 명확화 흐름 (AS-IS vs TO-BE)

```text
━━━━━ AS-IS ━━━━━

사용자: "여신 데이터 뽑아줘"
  → preprocess → resolve_history → classify_intent
  → [CLARIFICATION_NEEDED] → clarify(질문 생성) → END
  → main.py: Redis에 clarify state 저장

사용자: "신규 여신 실행 금액이요"
  → preprocess(재실행) → resolve_history(LLM 재호출)
  → classify_intent(LLM 재호출) → normalize_query → planner → ...

  ※ LLM 호출 최소 2~3회 재실행, 원본 질의 소실
  ※ Redis clarify state와 graph state 동기화 필요


━━━━━ TO-BE (2계층 판정 + 순수 interrupt + Unified Clarification) ━━━━━

사용자: "여신 데이터 뽑아줘"
  → resolve_history → classify_intent
  → [모호함 발견] → LLM이 ASK/INFER 판정 + AmbiguityType 분류
  → AmbiguitySignal 생성 → state.pending_signals에 추가
  → clarification_handler 진입
    → _should_override_to_ask(): INFER→ASK 단방향 보정
    → ASK 시그널 존재 → 우선순위 1개 선택
    → interrupt({question, options, source_node, ambiguity_type})
  → 체크포인트 자동 저장, 그래프 중단
  → PipelineResult(awaiting_clarification=True)

사용자: "신규 여신 실행 금액이요"
  → run_pipeline: sanitize() → aget_state → interrupt 대기 중 감지
  → Command(resume=sanitized_text)
  → clarification_handler 재개 → validate_answer() 검증
  → resolved_signals에 누적 (original_query 보존)
  → source_node="normalize_query" → normalize_query → planner → ...

  ※ LLM 재호출 0회 (interrupt 시점에서 정확히 재개)
  ※ 원본 질의 보존, 명확화 Q&A + 자동추론 독립 누적 (resolved_signals)
  ※ INFER 항목은 결과 상단에 추론 근거 안내
  ※ 체크포인터가 유일한 상태 저장소
```

### 4.2 오류 복구 흐름

```text
━━━━━ AS-IS ━━━━━

planner → context_explorer → [ES 타임아웃] → 예외 발생
  → 전체 파이프라인 실패, 사용자에게 에러 메시지

━━━━━ TO-BE ━━━━━

planner (체크포인트 ✓) → context_explorer → [ES 타임아웃]
  → RetryPolicy 자동 재시도 (최대 2회, 지수 백오프)
  → 재시도 실패 → 에러 상태로 END
  → 사용자 재시도 시 → planner 체크포인트에서 재개
```

### 4.3 Reason 계층 CONFLICTED 흐름

```text
context_explorer → confidence_evaluator → [CONFLICTED]
  → AmbiguitySignal(TABLE, ASK, LOW) 생성
  → clarification_handler 진입 → 가드레일 통과 → ASK 확정
  → interrupt({question, options, source_node="confidence_evaluator"})
  → 체크포인트 저장 (Reason 탐색 상태 전체 보존)

사용자: "1번이요"
  → run_pipeline: sanitize → Command(resume="1번이요")
  → clarification_handler 재개 → validate_answer() 검증
  → resolved_signals에 Q&A 누적 (ReasoningState 직접 변경 안 함)
  → source_node="confidence_evaluator" 복귀
  → confidence_evaluator 재실행 시 프롬프트에 [명확화 대화] 섹션 주입
  → LLM이 사용자 답변을 참조하여 CONFLICTED → CONFIRMED 재판정
  → GENERATE → sql_generator
```

> **설계 원칙**: `validate_answer()`는 입력 검증만 담당하고, `resolved_signals`에 자동 누적된다.
> ReasoningState(knowledge_items, hypotheses, phase 등)의 상태 전환은
> **복귀 노드의 LLM이 명확화 컨텍스트를 보고 스스로 재판단**한다.
> 이 방식은 handler가 ReasoningState 내부 구조에 결합되는 것을 방지하고,
> LLM이 전체 맥락을 고려한 정확한 재평가를 수행할 수 있게 한다.

---

## 5. 변경 영향도 분석

### 5.1 파일별 변경 범위

| 파일 | Phase | 변경 유형 | 변경 내용 |
| ---- | ----- | --------- | --------- |
| `config.py` | 1 | 추가 | `CheckpointerConfig` + `DbConnectionInfo` Value Object 설정 |
| `pipeline.py` | 1+2 | **수정** | `compile(checkpointer=)`, preprocess 노드 제거, `clarification_handler` 노드 추가, 라우팅 변경 |
| `runner.py` | 1+2 | **수정** | sanitize 통합, `thread_id` config 주입, interrupt 감지 + `Command(resume=)` |
| `state.py` | 2A | **수정** | `original_query`, `pending_signals`, `resolved_signals` 필드 추가, `normalized_query` 타입 명시화 |
| `main.py` | 1+3 | **수정** | lifespan checkpointer 초기화, clarify Redis 호출 제거 |
| 신규: `checkpointer.py` | 1 | **생성** | `create_checkpointer()` async context manager 팩토리 (dev: Memory, prod: Postgres) |
| 신규: `clarification.py` | 2A | **생성** | `AmbiguitySignal` 단일 모델, `AmbiguityType`, `ConfidenceLevel`, `QuestionType` Enum |
| 신규: `clarification_handler.py` | 2A | **생성** | 통합 명확화 노드 + `_should_override_to_ask()` 가드레일 인라인 + `validate_answer()` |
| `preprocessor.py` | 2 | **제거** | 그래프에서 제거 (sanitize는 runner.py로 이동) |
| `clarifier.py` | 2 | **제거** | clarification_handler로 대체 |
| `history_resolver.py` | 2B | 수정 | UNSURE 시 `AmbiguitySignal` 생성 → `pending_signals`에 추가 (T1: 하드코딩 ASK) |
| `intent_classifier.py` | 2B | 수정 | AMBIGUOUS 시 `AmbiguitySignal` 생성 → `pending_signals`에 추가 (T2: LLM 판정) |
| `query_normalizer.py` | 2B | 수정 | ambiguities 시 `AmbiguitySignal` 생성 → `pending_signals`에 추가 (T3: LLM 판정) |
| `sql_generator.py` | 2C | 수정 | Cross-DB 시 `AmbiguitySignal` 생성 → `pending_signals`에 추가 (T4: 항상 INFER — 테이블명 시스템코드 기반 DB 추론, 결과 안내 포함) |
| `result_finalizer.py` | 2C | 수정 | CONFLICTED 시 `AmbiguitySignal` 생성 → `pending_signals`에 추가 (T5: LLM 판정) |
| `response_formatter.py` | 2C | 수정 | INFER 자동추론 항목을 결과 상단에 자연어 안내로 포함 (`resolved_signals`에서 도출) |
| 신규: `resources/domain_defaults.yaml` | 2D | **생성** | 도메인 기본값 사전 (LLM 컨텍스트 힌트) |
| `session/store.py` | 3 | 수정 | clarify 메서드 deprecated |
| `input_sanitizer.py` | 3 | 수정 | `synthesize_clarification()` 제거 (미사용) |
| 신규: `thread_manager.py` | 4 | **생성** | Thread TTL 정리 |

### 5.2 하위 호환성

| 영역 | 호환성 | 비고 |
| ---- | ------ | ---- |
| REST API `/api/query` | 완전 호환 | thread_id = session_id로 투명 매핑 |
| WebSocket `/ws/{session_id}` | 프로토콜 확장 | `AmbiguitySignal` interrupt 페이로드 기반 메시지 추가 (아래 상세 참조) |
| 프론트엔드 | 변경 필요 | 신규 컴포넌트 및 메시지 처리 추가 (아래 상세 참조) |
| CLI 실행 (`runner.py main`) | 완전 호환 | thread_id 미지정 시 체크포인트 미저장 |

#### WebSocket 메시지 스키마 변경

기존 명확화 메시지(`type: "clarification_question"`, 자유 텍스트)를 구조화된 `AmbiguitySignal` 페이로드로 교체한다.

**서버 → 클라이언트 (신규)**:
```json
{
  "type": "awaiting_clarification",
  "payload": {
    "question": "정보계에 유사한 테이블이 두 개 있어서 확인이 필요합니다.",
    "question_type": "SINGLE_SELECT",
    "options": ["일별 잔액 테이블 (매일 갱신)", "월말 기준 잔액 테이블 (월 1회 갱신)"],
    "ambiguity_type": "TABLE"
  }
}
```

**클라이언트 → 서버 (변경 없음)**: 기존 텍스트 메시지 그대로 (`"1번이요"`, `"일별 잔액이요"` 등). validate_answer()가 서버 측에서 정규화한다.

**기존 메시지 타입 변경 대상**:

| 기존 | 변경 후 |
|------|---------|
| `type: "clarification_question"` (문자열) | `type: "awaiting_clarification"` + `payload` 구조체 |
| `type: "result"` (단순 텍스트) | `type: "result"` + `auto_resolved_notice` 필드 추가 (INFER 항목 안내) |

#### 프론트엔드 변경 항목

| 항목 | 변경 유형 | 내용 |
|------|-----------|------|
| 명확화 질문 렌더러 | **전면 교체** | 기존 단순 텍스트 버블 → `question_type` 기반 자동 렌더링 분기 |
| `FREE_TEXT` 응답 UI | 유지 | 기존 채팅 입력창 그대로 사용 |
| `SINGLE_SELECT` 응답 UI | **신규** | `options` 배열을 버튼/라디오 형태로 렌더링 (번호 또는 텍스트 선택) |
| auto_resolved 안내 영역 | **신규** | 결과 상단 "📋 조회 기준 안내" 섹션 — 접기/펼치기 가능 |
| 기존 명확화 분기 처리 로직 | **제거** | `clarification_origin`, `clarify_state` 기반 케이스별 분기 → 삭제 |
| 대화 이력 렌더링 | **수정** | `HistoryEntryType.CLARIFICATION` 타입 메시지를 명확화 Q&A 형식으로 구별 렌더링 |

---

## 6. 구현 페이즈

### Phase 1: Core Checkpointer (필수, 최소 변경)

**목표**: 체크포인터를 연결하되 기존 흐름을 변경하지 않음

1. `config.py`에 checkpoint DB 설정 추가
2. `src/agents/graph/checkpointer.py` 팩토리 생성
3. `pipeline.py`의 `create_app()`에 checkpointer 주입
4. `runner.py`에서 `config={"configurable": {"thread_id": session_id}}` 전달
5. `main.py` lifespan에 checkpointer 초기화/정리 추가
6. PipelineState 직렬화 검증 (round-trip 테스트)

**검증**: 기존 동작이 동일하게 유지되면서 체크포인트가 저장됨

### Phase 2: Unified Clarification + 2계층 판정 + 순수 interrupt (핵심 변경)

**목표**: 5개 명확화 트리거를 통합 프레임워크로 전환하고, 2계층 판정(LLM + 가드레일)으로 질문 최적화

**Phase 2A** (인프라 — 스키마 + State + 노드 골격):

1. `AmbiguityType`, `ConfidenceLevel`, `QuestionType` Enum 정의
2. `AmbiguitySignal` 단일 모델 정의 (감지→해소 전 생명주기 통합)
3. `state.py`에 `original_query`, `pending_signals`, `resolved_signals` 필드 추가
4. `validate_answer()` 단일 함수 구현 (question_type별 검증)
5. `_should_override_to_ask()` 가드레일 인라인 함수 구현
6. `clarification_handler` 노드 구현 (가드레일 + 단일 interrupt)
7. `pipeline.py`에서 preprocess 노드 제거, clarification_handler 노드 추가, 라우팅 변경
8. `runner.py`에 sanitize 통합 + interrupt 감지 + `Command(resume=)` 분기
9. 기존 `clarifier.py`, `preprocessor.py` 제거

**Phase 2B** (Interpret 계층 — T1~T3 트리거 전환):

1. `history_resolver` — UNSURE 시 `AmbiguitySignal` 생성 → `pending_signals`에 추가 (T1: 하드코딩 ASK)
2. `intent_classifier` — AMBIGUOUS 시 `AmbiguitySignal` 생성 (T2: LLM 판정)
3. `query_normalizer` — ambiguities 시 `AmbiguitySignal` 생성 (T3: LLM 판정)
4. 각 노드 프롬프트에 ASK/INFER 판정 기준 + few-shot + 7종 분류 추가

**Phase 2C** (Reason 계층 — T4~T5 트리거 전환 + 결과 포맷):

1. `sql_generator` — Cross-DB 시 `AmbiguitySignal` 생성 (T4: 항상 INFER — 테이블명 시스템코드 기반 DB 추론)
2. `result_finalizer` — CONFLICTED 시 `AmbiguitySignal` 생성 (T5: LLM 판정)
3. `response_formatter`에 INFER 자동추론 안내 포함 (`resolved_signals`에서 도출)
4. `build_clarification_context()` — 복귀 노드 LLM 프롬프트 주입 유틸

**Phase 2D** (안정화 + 개선):

1. `resources/domain_defaults.yaml` 초기 시딩 + LLM 컨텍스트 주입
2. 가드레일 규칙 세분화 (VALUE 코드 매칭, TIMEFRAME 산출식 연관)
3. **[TODO]** 정정 임계값 — 자동추론 정정 감지 로직 + ASK 전환 (정정 판별 기준 미정의, 운영 데이터 축적 후 재검토)
4. ASK 시그널 우선순위 정교화 (INTENT/FORMULA > TABLE/VALUE > TIMEFRAME)

**검증**: 각 트리거별 명확화 → 응답 → resume → 정상 진행 E2E 테스트 + ASK/INFER 판정 정확도 검증

### Phase 3: 세션 관리 통합 (정리)

**목표**: SessionStore에서 clarify 관련 코드 제거

1. SessionStore에서 `get_clarification` / `set_clarification` deprecated
2. `main.py`에서 clarify Redis 호출 제거
3. `runner.py`에서 `clarification_state` 파라미터 제거
4. `synthesize_clarification()` 제거

### Phase 4: 고급 기능 (선택)

1. Thread TTL 기반 자동 정리 (cron/background task)
2. RetryPolicy를 외부 I/O 노드에 적용
3. State time-travel 디버깅 API
4. EncryptedSerializer 도입 (금융 데이터 보호)
5. SQL 승인 interrupt (execute_sql 노드, 선택적)

---

## 7. 위험 요소 및 완화 전략

| 위험 | 심각도 | 확률 | 완화 전략 |
| ---- | ------ | ---- | --------- |
| Pydantic 직렬화 실패 (Any 타입 필드) | 높음 | 낮음 | LangGraph 1.1.2 검증 완료: Pydantic v2 + Annotated reducer + MemorySaver/JsonPlusSerializer round-trip 정상. 체크포인터에 `with_msgpack_allowlist([("src.",)])` 설정 필수 (패키지 레벨 단일 접두사) |
| 체크포인트 DB 스토리지 급증 | 중간 | 높음 | Phase 4에서 TTL 정리, 노드당 상태 크기 프로파일링 |
| interrupt() resume 시 자유 텍스트 보안 | 중간 | 낮음 | run_pipeline 내 sanitize가 모든 입력에 적용 |
| PostgresSaver autocommit 미설정 | 높음 | 중간 | checkpointer 팩토리에서 강제 설정 |
| 기존 테스트 깨짐 | 중간 | 높음 | Phase 1에서 MemorySaver 기반 테스트, 기존 mock 유지 |
| 폐쇄망 패키지 누락 | 중간 | 낮음 | pyproject.toml에 의존성 명시, .whl 오프라인 번들 |
| clarification_handler 복귀 후 노드 재실행 시 상태 불일치 | 중간 | 중간 | validate_answer()는 입력 검증만 담당, resolved_signals에 자동 누적. 복귀 노드의 LLM 프롬프트에 [명확화 대화] 섹션을 주입하여 LLM이 전체 맥락을 보고 재판단 |
| 폐쇄망 LLM의 ASK/INFER 판정 메타인지 부족 | 중간 | 높음 | few-shot 예시 3개+, 가드레일 고위험 강제 보정, "애매하면 ASK" 디폴트 |
| INFER 자동추론 오류로 사용자 신뢰 저하 | 중간 | 중간 | 결과 상단 추론 근거 안내 + 사용자 정정 가능 안내 |
| 7종 분류에 해당하지 않는 모호성 유형 발생 | 낮음 | 낮음 | `CONTEXT` 유형이 catch-all 역할, 운영 데이터 축적 후 유형 확장 |

---

## 8. 의존성 추가

```toml
# pyproject.toml 추가 항목
[project]
dependencies = [
    # ... 기존 ...
    "langgraph-checkpoint-postgres>=2.0.0",
    "psycopg[binary,pool]>=3.1.0",
]
```

---

## 9. 설계 결정 요약 (ADR)

| # | 결정 | 근거 | 대안 및 기각 사유 |
| - | ---- | ---- | ----------------- |
| D1 | AsyncPostgresSaver 사용 | 분산 배포, 영속성, 감사 쿼리 | Redis: 버전 제약, time-travel 제한 |
| D2 | history_db에 체크포인트 테이블 공존 | 커넥션 풀 절약, 인프라 단순화 | 별도 DB: 리소스 과다 |
| D3 | 순수 interrupt() (모든 명확화 통일) | 단일 패턴, LangGraph 표준 준수, 구현 단순 | 하이브리드: 2가지 패턴 혼용 복잡도. 순수 Shortcut: Reason 재실행 비용 과다 |
| D4 | Pydantic BaseModel 유지 | 15개 노드 호환, 검증 내장 | TypedDict: 전면 리팩토링 필요 |
| D5 | Unified Clarification Framework | 5개 분산 트리거 통합, AmbiguitySignal 단일 모델로 생명주기 관리 | 분산 유지: 프론트엔드 분기 과중, 재진입 불명확. Strategy 패턴: 7개 중 6개 핸들러가 동일 로직, 과잉 추상화 |
| D6 | Structured Context Passing | 원본 보존, 감사 추적, 오류 전파 방지 | Query Rewriting: 엔티티 소실, 누적 오류, 감사 불가 (CoE-SQL NAACL 2024, Intent Scoping VLDB 2025) |
| D7 | preprocess 노드 제거 + runner sanitize | 이중 보안 검증 해소, 그래프 단순화 | preprocess 유지: main.py와 이중화 지속 |
| D8 | SessionStore conversation_history 유지 | 턴 간 대화 맥락용, 체크포인터는 턴 내 상태용 | 즉시 체크포인터 통합: 범위 과다, 점진적 전환이 안전 |
| D9 | 2계층 판정 (LLM 판정 + 규칙 가드레일) | LLM이 전체 맥락을 보유하되, 고위험 유형은 규칙이 강제 보정 | 규칙 단독: 맥락 손실. LLM 단독: FORMULA 등 고위험 판정 신뢰 부족 (Sphinteract VLDB 2025) |
| D10 | ConfidenceLevel 이산값 (HIGH/MEDIUM/LOW) | 모델 교체 시 재튜닝 불필요, LLM self-calibration 부정확 (arXiv 2508.14056) | float 신뢰도: 모델별 scale 불일치, 폐쇄망 임계값 튜닝 비용 과다 |
| D11 | AmbiSQL 7종 분류 체계 (명칭 단순화) | 유형별 맞춤 가드레일 규칙 적용 가능, LLM 오기 방지 | 분류 없이 자유형: 가드레일 규칙 적용 불가. AmbiSQL 원본 명칭: LLM 오기(typo) 위험 |
| D12 | INFER→ASK 단방향 보정만 허용 | 안전 방향으로만 보정하여 금융 리스크 최소화 | 양방향 보정: 가드레일이 질문을 억제할 경우 금융 오답 리스크 |
| D13 | PRACTIQ 억제 (SELECT/WHERE 컬럼 모호성 → 포괄 조회) | 불필요한 질문 감소, 사용자 경험 개선 (NAACL 2025) | 모든 모호성에 질문: 사용자 피로 |
| D14 | DTE 패턴 (질문에 "왜 묻는지" 설명 포함) | IT 비전문 사용자 이해도 향상 (ACL 2023 Findings) | 질문만 제시: 사용자가 왜 묻는지 이해 못함 |

---

## 10. 성공 지표

| 지표 | 현재 | 목표 |
| ---- | ---- | ---- |
| 명확화 응답 후 불필요 LLM 호출 수 | 2~3회 | 0회 |
| 명확화 왕복 시 상태 유실 건수 | 발생 가능 | 0건 |
| 원본 질의 보존 여부 | 소실/미구현 | 항상 보존 |
| 명확화 트리거 패턴 수 | 5가지 (분산) | 1가지 (통합) |
| 프론트엔드 명확화 분기 처리 | 케이스별 분기 | question_type 자동 렌더링 |
| 감사 추적 가능 범위 | 최종 결과만 | 원본 질의 + Q&A + 자동추론 근거 + SQL 전체 |
| 네트워크 에러 후 수동 재입력 | 항상 | 자동 복구 가능 |
| 세션 상태 저장소 수 | 2개 (SessionStore + 없음) | 1개 (Checkpointer) |
| 불필요한 명확화 질문 비율 | 측정 불가 (전부 질문) | INFER로 자동 추론 가능 항목 억제 |
| 평균 명확화 상호작용 횟수 | N/A | ~2.18회 (Sphinteract 최적치) |
| 자동 추론 정정률 | N/A | 추적 시작 (운영 데이터 축적 후, TODO) |
