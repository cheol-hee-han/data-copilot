# Pipeline Architecture — Data Copilot

> **Version 3.0** (2026-03-25)
> 이 문서는 실제 구현 코드(`src/agents/graph/pipeline.py`)를 기반으로 작성되었으며,
> 사용자 질의 입력부터 최종 응답까지의 전체 처리 흐름을 기술한다.

---

## 1. 아키텍처 개요

3계층(interpret → reason → present) 단일 LangGraph 파이프라인으로 구성된다.

| 계층 | 역할 | 노드 |
|------|------|------|
| **Interpret** | 사용자 의도 해석 | preprocess, resolve_history, classify_intent, normalize_query, clarify |
| **Reason** | 에이전틱 추론 루프 | planner, context_explorer, confidence_evaluator, sql_generator, sql_validator, recovery_planner, result_finalizer |
| **Present** | 결과 생성 및 표현 | execute_sql, analyze_data, format_response, error_end |

**핵심 소스 파일:**

| 파일 | 역할 |
|------|------|
| `src/agents/graph/pipeline.py` | 그래프 빌더 + 라우팅 함수 전체 |
| `src/agents/graph/runner.py` | 파이프라인 실행 진입점 |
| `src/agents/state/state.py` | PipelineState, ReasoningState 정의 |
| `src/services/confidence_scorer.py` | 행동 판정 SSOT (evaluate_readiness) |

---

## 2. 파이프라인 그래프 — 노드 수준 개요

전체 노드 연결을 한눈에 보여주는 그래프이다.

```mermaid
flowchart TD
    subgraph interpret["Interpret 계층"]
        A([사용자 질의]) --> B["preprocess<br/><small>전처리·인젝션 감지</small>"]
        B -->|정상| R["resolve_history<br/><small>대화 이력 해소</small>"]
        B -->|인젝션/에러| ERR
        R -->|CONTINUE / NEW| C["classify_intent<br/><small>의도 분류</small>"]
        R -->|UNSURE| END_R([명확화 질문 → 종료])
        C -->|DATA| D["normalize_query<br/><small>8-Slot 정규화</small>"]
        C -->|비데이터 의도| CLR["clarify<br/><small>명확화 질문 생성</small>"]
        C -->|에러| ERR
        CLR --> END_CLR([종료])
    end

    subgraph reason["Reason 계층 — 에이전틱 추론 루프"]
        D --> P["planner<br/><small>가설 생성·탐색 계획</small>"]
        P -->|fast-path| GEN
        P -->|일반| EXP["context_explorer<br/><small>도구 기반 컨텍스트 탐색</small>"]
        EXP --> EVAL["confidence_evaluator<br/><small>준비도 판정</small>"]
        EVAL -->|EXPLORE| EXP
        EVAL -->|GENERATE| GEN["sql_generator<br/><small>SQL 생성</small>"]
        EVAL -->|REPLAN| REC["recovery_planner<br/><small>가설 교체·재계획</small>"]
        EVAL -->|TERMINATE / ASK_USER| FIN
        GEN --> VAL["sql_validator<br/><small>3-레이어 검증</small>"]
        VAL -->|SUCCESS| FIN["result_finalizer<br/><small>최종 결과 조립</small>"]
        VAL -->|FAIL_SYNTAX / FAIL_SEMANTIC| GEN
        VAL -->|FAIL_STRUCTURAL / FAIL_EMPTY| REC
        REC -->|새 가설| EXP
        REC -->|가설 소진| FIN
    end

    subgraph present["Present 계층"]
        FIN -->|validated_sql| EXEC["execute_sql<br/><small>SQL 실행</small>"]
        FIN -->|에러| ERR
        FIN -->|명확화 필요| END_FIN_CLR([명확화 질문 → 종료])
        EXEC -->|DATA_ANALYSIS| ANA["analyze_data<br/><small>통계·인사이트·시각화</small>"]
        EXEC -->|DATA_EXTRACTION| FMT["format_response<br/><small>보고서 포맷팅</small>"]
        EXEC -->|에러| ERR
        ANA --> FMT
        FMT --> END_OK([최종 응답])
    end

    ERR["error_end<br/><small>에러 메시지 생성</small>"] --> END_OK
```

---

## 3. 세션 관리 및 멀티턴 흐름

대화 이력과 명확화 왕복을 포함한 세션 레벨 흐름이다.

```mermaid
flowchart LR
    subgraph client["클라이언트"]
        U([사용자])
    end

    subgraph server["FastAPI 서버"]
        WS["WebSocket<br/>/ws/{session_id}"]
        REST["REST API<br/>POST /api/query"]
    end

    subgraph session["세션 관리<br/>SessionStore"]
        MEM["MemoryStore<br/><small>개발용 (in-memory)</small>"]
        RED["RedisStore<br/><small>운영용 (TTL 30분)</small>"]
    end

    subgraph pipeline["파이프라인"]
        direction TB
        PP["preprocess"]
        HR["resolve_history"]
        CI["classify_intent"]
        CLR2["clarify"]
        PIPE["... (reason → present)"]
    end

    U -->|"1. 최초 질의<br/>session_id=abc"| WS
    U -->|"1. 최초 질의"| REST
    WS --> session
    REST --> session
    session -->|"conversation_history 로드"| PP
    PP --> HR

    HR -->|"UNSURE:<br/>맥락 불분명"| CLR_RESP(["명확화 질문 반환<br/>awaiting_clarification=true"])
    CLR_RESP -->|"2. 사용자 재입력"| PP

    HR -->|"CONTINUE:<br/>이전 맥락 합성"| CI
    HR -->|"NEW:<br/>독립 질의"| CI
    CI -->|"AMBIGUOUS"| CLR2
    CLR2 --> CLR_RESP2(["명확화 질문 반환<br/>clarification_turns++"])
    CLR_RESP2 -->|"3. 사용자 응답"| PP

    CI -->|"DATA"| PIPE
    PIPE --> RESP(["최종 응답"])
    RESP -->|"history에 추가"| session
```

**멀티턴 상태 전이:**

| 턴 | 사용자 입력 | history_resolver 판정 | 결과 |
|----|-----------|---------------------|------|
| 1 | "데이터 좀 뽑아줘" | SKIP (이력 없음) | AMBIGUOUS → 명확화 질문 |
| 2 | "이번달 여신 잔액" | CONTINUE (명확화 응답) | 이전 맥락 + 현재 입력 합성 → 정규화 진행 |
| 3 | "그거 지점별로 나눠줘" | CONTINUE (지시대명사 "그거") | 이전 결과 + "지점별" 추가 → 재실행 |
| 4 | "오늘 날씨 어때?" | NEW (완전히 다른 주제) | 이전 맥락 리셋 → CASUAL_TALK |

**history_resolver 판정 규칙** (`services/history_resolver.py`):

1. 대화 이력 없음 → `SKIP` (LLM 호출 안 함)
2. 이력 있음 → 룰 기반 게이트:
   - 지시대명사 ("그", "거기", "아까") → LLM 호출
   - 수정 표현 ("추가로", "빼고") → LLM 호출
   - 짧은 입력 (≤10자) → LLM 호출
   - 명확화 응답 패턴 ("1번", "2)") → LLM 호출
   - 기타 이력 있는 경우 → LLM 호출 (안전 우선)
3. LLM 판정 결과: `CONTINUE` / `NEW` / `UNSURE`

**세션 스토어 구현:**

| 구현체 | 키 | TTL | 용도 |
|--------|-----|-----|------|
| `MemoryStore` | dict key = session_id | FIFO (100건) | 개발/테스트 |
| `RedisStore` | `session:{sid}:history` | 슬라이딩 30분 | 운영 |
| `RedisStore` | `session:{sid}:clarify` | 고정 5분 | 명확화 상태 |

---

## 4. Reason 계층 — 에이전틱 추론 상세 흐름

Reason 계층은 탐색-판정-생성-검증-복구의 순환 루프를 통해 SQL을 점진적으로 완성한다.

### 4.1 상태 모델 (ReasoningState)

`src/agents/state/state.py`의 `ReasoningState`가 추론 루프 전체 상태를 관리한다.

**Phase 전이:**
```
PLANNING → EXPLORING → VERIFYING → GENERATING → VALIDATING → REPLANNING → DONE
```

**핵심 필드:**

| 필드 | 타입 | 역할 |
|------|------|------|
| `phase` | `str` (7가지) | 현재 추론 단계 |
| `knowledge_items` | `list[KnowledgeItem]` | 수집된 지식 (테이블, 컬럼, 조건 매핑) |
| `hypotheses` | `list[Hypothesis]` | 탐색 가설 (상태: PENDING/ACTIVE/SUCCESS/FAILED) |
| `execution_plan` | `list[ExecutionStep]` | 탐색 도구 실행 계획 |
| `candidate_tables` | `list[CandidateTable]` | 후보 테이블 목록 |
| `confirmed_join_path` | `list[dict]` | 확인된 조인 경로 |
| `dead_ends` | `list[DeadEnd]` | 실패한 가설 기록 |
| `loop_guard` | `LoopGuard` | 루프 제어 카운터 |
| `fast_path_triggered` | `bool` | Fast-Path 바이패스 여부 |

**지식 항목(KnowledgeItem) 상태 전이:**
```
UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED
                                  ↘ CONFLICTED → (사용자 확인)
```

### 4.2 상세 추론 흐름 다이어그램

```mermaid
flowchart TD
    START(["Reason 진입<br/><small>normalized_query 수신</small>"]) --> PLAN

    subgraph planning["PLANNING 단계"]
        PLAN["planner_node"]
        PLAN -->|"1. 질의 분해 (decomposition)"| PLAN
        PLAN -->|"2. knowledge_items 초기화"| PLAN
        PLAN -->|"3. 가설 생성 (hypotheses)"| PLAN
        PLAN -->|"4. 탐색 계획 수립 (execution_plan)"| PLAN
    end

    PLAN -->|"fast_path_triggered?"| FP_CHECK{"Fast-Path 판정"}
    FP_CHECK -->|"Yes: 충분한 힌트<br/>+ 후보 테이블 확보<br/>+ 미해결 항목 0건"| GEN
    FP_CHECK -->|"No"| EXP

    subgraph exploring["EXPLORING 단계"]
        EXP["context_explorer_node<br/><small>execution_plan 순차 실행</small>"]
        EXP -->|"도구 실행: ES 검색,<br/>Qdrant 벡터 검색,<br/>코드 메타 조회 등"| EXP_UPDATE["지식 업데이트<br/><small>knowledge_items 갱신</small>"]
        EXP_UPDATE -->|"조기 탈출 체크:<br/>evaluate_readiness()"| EXP_EARLY{"GENERATE?"}
        EXP_EARLY -->|"Yes"| EVAL
        EXP_EARLY -->|"No: 다음 스텝"| EXP
    end

    EXP --> EVAL

    subgraph evaluating["판정 단계 — SSOT"]
        EVAL["confidence_evaluator_node<br/><small>evaluate_readiness() 호출</small>"]
        EVAL --> EVAL_LOGIC{"판정 우선순위"}
        EVAL_LOGIC -->|"1. 루프 가드 초과<br/>(도구≥20, 재계획≥3,<br/>생성≥4, 또는 가설 소진)"| TERMINATE
        EVAL_LOGIC -->|"2. CONFLICTED 항목 존재"| ASK_USER
        EVAL_LOGIC -->|"3. score≥0.75<br/>AND 핵심 항목 확인 완료"| GENERATE
        EVAL_LOGIC -->|"4. 탐색 스텝 남음"| EXPLORE_MORE
        EVAL_LOGIC -->|"5. 확신 부족"| REPLAN_V
    end

    TERMINATE["TERMINATE<br/>→ reason_finalize"] --> FIN
    ASK_USER["ASK_USER<br/>→ reason_finalize<br/><small>(명확화 요청)</small>"] --> FIN
    GENERATE["GENERATE<br/>→ sql_generator"] --> GEN
    EXPLORE_MORE["EXPLORE<br/>→ context_explorer"] --> EXP
    REPLAN_V["REPLAN<br/>→ recovery_planner"] --> REC

    subgraph generating["GENERATING 단계"]
        GEN["sql_generator_node"]
        GEN -->|"1. DB 소스 판별 (dialect)"| GEN
        GEN -->|"2. 교차 DB 감지 → 명확화"| GEN
        GEN -->|"3. 프롬프트 조립<br/>(지식 + 힌트 + dead_ends)"| GEN
        GEN -->|"4. LLM SQL 생성"| GEN
    end

    GEN --> VAL

    subgraph validating["VALIDATING 단계"]
        VAL["sql_validator_node<br/><small>3-레이어 검증</small>"]
        VAL --> L1["Layer 1: 안전성 검증<br/><small>DML 차단, PII, 카탈로그,<br/>sqlglot 파싱 (dialect)</small>"]
        L1 --> L2["Layer 2a: 구조 검증<br/><small>GROUP BY, 집계 일관성,<br/>LIMIT 존재</small>"]
        L2 --> L2B["Layer 2b: 의미 검증 (LLM)<br/><small>7점 체크리스트 대조<br/>(선택적)</small>"]
        L2B --> L3["Layer 3: 실행 검증<br/><small>LIMIT 5 실제 실행<br/>(db_source 라우팅)</small>"]
    end

    VAL --> VAL_RESULT{"검증 결과"}
    VAL_RESULT -->|"SUCCESS"| FIN
    VAL_RESULT -->|"FAIL_SYNTAX<br/>(생성 < 4회)"| GEN
    VAL_RESULT -->|"FAIL_SEMANTIC_LOCAL<br/>(로컬 수정 < 2회)"| GEN
    VAL_RESULT -->|"FAIL_SEMANTIC_LOCAL<br/>(에스컬레이션)"| REC
    VAL_RESULT -->|"FAIL_STRUCTURAL<br/>FAIL_EMPTY<br/>FAIL_DB_ERROR"| REC
    VAL_RESULT -->|"fast-path 실패"| EXP

    subgraph replanning["REPLANNING 단계"]
        REC["recovery_planner_node"]
        REC -->|"1. 현재 가설 FAILED 처리"| REC
        REC -->|"2. DeadEnd 기록"| REC
        REC -->|"3. 다음 PENDING 가설<br/>또는 LLM 새 가설 생성"| REC
        REC -->|"4. 새 execution_plan 수립"| REC
    end

    REC -->|"새 가설 있음"| EXP
    REC -->|"가설 소진 → phase=DONE"| FIN

    FIN["result_finalizer_node<br/><small>최종 결과 조립</small>"]
    FIN -->|"validated_sql 있음"| EXEC(["→ Present: execute_sql"])
    FIN -->|"CONFLICTED"| CLARIFY(["명확화 질문 → 종료<br/><small>자체 생성 질문, clarify 미경유</small>"])
    FIN -->|"에러"| ERROR(["→ error_end"])
```

### 4.3 확신도(Confidence Score) 계산

`services/confidence_scorer.py`의 `calculate_readiness()`가 0.0~1.0 점수를 산출한다.

**3차원 가중 평균:**

| 차원 | 가중치 | 계산 방식 |
|------|--------|----------|
| **용어 해소율** (term_resolution) | 50% | `CONFIRMED/PROBABLE 항목 수 / 전체 knowledge_items 수` |
| **유사 SQL 매칭** (use_case_match) | 30% | `탐색된 use_cases 중 최대 similarity 값` |
| **조인 경로 확인** (join_path) | 20% | `다중 테이블이면 confirmed_join_path 존재 여부, 단일 테이블이면 1.0` |

**임계값:**
- `≥ 0.75` + 핵심 항목 전부 확인 → **GENERATE**
- `≤ 0.30` → **REPLAN** (가설 자체 교체)

### 4.4 루프 가드 (LoopGuard)

무한 루프를 방지하는 카운터 기반 안전장치이다 (`state.py`).

| 카운터 | 한계 | 초과 시 |
|--------|------|--------|
| `total_tool_calls` | 20 | `should_terminate()` = True → TERMINATE |
| `replan_count` | 3 | 동일 |
| `generate_attempts` | 4 | 동일 |
| `local_fix_count` | 2 | `should_escalate_to_structural()` → REPLAN 에스컬레이션 |

**should_terminate() 조건:**
```python
total_tool_calls >= 20
OR replan_count >= 3
OR generate_attempts >= 4
OR final_status == "failure"
OR (pending 가설 없음 AND current_hypothesis 없음)
```

### 4.5 Fast-Path 바이패스

planner 단계에서 탐색 없이 바로 SQL 생성으로 진입하는 최적화 경로이다.

**트리거 조건** (`agentic/planner.py`):
- structural_hints가 충분히 채워져 있음
- candidate_tables가 이미 확보됨
- UNRESOLVED knowledge_items가 0건
- ambiguities가 없음

**Fast-Path 실패 처리:**
검증에서 실패하면 `explore_after_fast_path` → 정상 탐색 루프로 전환된다.

### 4.6 SQL 검증 실패 유형별 라우팅

`_route_after_reason_validate_sql()` (pipeline.py:159-198)이 실패 유형에 따라 5가지 경로로 분기한다.

| 검증 결과 | 조건 | 라우팅 | 설명 |
|----------|------|--------|------|
| `SUCCESS` | — | → reason_finalize | SQL 확정 |
| `FAIL_SYNTAX` | generate_attempts < 4 | → sql_generator (fix_syntax) | 구문 오류 수정 재시도 |
| `FAIL_SEMANTIC_LOCAL` | local_fix < 2 | → sql_generator (fix_local) | GROUP BY 누락 등 로컬 수정 |
| `FAIL_SEMANTIC_LOCAL` | local_fix ≥ 2 | → recovery_planner (replan) | 에스컬레이션: 가설 자체 교체 |
| `FAIL_STRUCTURAL` | — | → recovery_planner (replan) | 테이블/컬럼 불일치 |
| `FAIL_EMPTY` | — | → recovery_planner (replan) | 결과 0건 |
| `FAIL_DB_ERROR` | — | → recovery_planner (replan) | DB 실행 오류 |
| fast-path 실패 | fast_path_triggered | → context_explorer | 정상 탐색으로 전환 |
| 기타 / 한계 초과 | — | → reason_finalize (실패) | 최종 실패 처리 |

---

## 5. Interpret 계층 상세

### 5.1 전처리 (preprocess_node)

LLM 호출 없이 입력을 정규화하고 보안 위협을 사전 차단한다.

- 유니코드 NFKC 정규화
- 입력 길이 제한 (500자)
- SQL 인젝션 감지 (키워드 + 패턴)
- 프롬프트 인젝션 감지 (13종 패턴)
- 공백 정규화

### 5.2 의도 분류 (classify_intent_node)

2단계 분류 구조:

**Stage 1 — Intent Gate** (LLM):
```
DATA_QUERY → Stage 2로
DATA_ANALYSIS → 바로 DATA_ANALYSIS
CASUAL_TALK / META_QUESTION / AMBIGUOUS → 명확화 또는 종료
```

**Stage 2 — 세분류** (규칙 기반):
```
DATA_QUERY → 분석 키워드 검사 ("추이", "비교", "분석", "변화")
  → 있으면: DATA_ANALYSIS
  → 없으면: DATA_EXTRACTION
```

**폴백:** Intent Gate LLM 실패 시 → Legacy 분류기 (`intent_classification.txt`)로 폴백

### 5.3 질의 정규화 (normalize_query_node)

자연어를 8-Slot 구조화 모델(`NormalizedQuery`)로 변환한다.

| 슬롯 | 설명 | 예시 |
|------|------|------|
| INTENT | 질의 유형 | AGGREGATE, RANK, COMPARE, TREND |
| ENTITY | 대상 엔티티 | {"term": "대출", "type": "DIRECT"} |
| MEASURE | 측정 지표 | {"term": "건수", "agg_function": "COUNT"} |
| DIMENSION | 분류 축 | {"term": "지점", "role": "GROUP"} |
| FILTER | 조건 | {"column": "상태", "op": "EQUALS", "value": "정상"} |
| TIME | 시간 범위 | {"type": "RELATIVE", "base_period": "이번달"} |
| MODIFIER | 후처리 | {"type": "RANK", "direction": "DESC", "limit": 10} |
| OUTPUT_HINT | 출력 형식 | {"format": "SPEC_SHEET", "doc_type": "연체명세"} |

**2-Phase 파이프라인:**
- Phase 1: LLM 슬롯 추출 + 후처리 (동의어 사전 확장, search_keywords 자동 생성)
- Phase 2 (선택): LLM 교차 검증 (R1~R12 규칙)

### 5.4 명확화 질문 생성 (clarify_node)

4가지 비데이터 intent에 대해 적절한 응답을 생성하거나, 정규화 단계에서 발견된 모호성에 대해 명확화 질문을 생성한다.

**진입 경로:**

| 출발 노드 | `state.intent` | `ambiguities` | 동작 |
| ----------- | --------------- | --------------- | ------ |
| classify_intent | `CLARIFICATION_NEEDED` | 없음 | LLM이 모호성 판단 후 명확화 질문 생성 |
| classify_intent | `CASUAL_TALK` | 없음 | 간단 응대 + 데이터 요청 유도 |
| classify_intent | `GENERAL_QUESTION` | 없음 | 업무 질문 답변 + 데이터 요청 유도 |
| classify_intent | `META_QUESTION` | 없음 | 시스템 역량 안내 |
| normalize_query | `CLARIFICATION_NEEDED` (업데이트) | 있음 | ambiguities 기반 타겟 질문 생성 |

**패스스루 로직:**

`state.clarification_question`이 이미 채워져 있으면 LLM 호출 없이 기존 질문을 그대로 사용한다.
이는 상류 노드(result_finalizer, sql_generator)가 자체적으로 명확화 질문을 생성한 경우를 위한 것이나,
현재 구현에서는 reason 계층의 명확화 경로가 clarify 노드를 경유하지 않고 바로 END로 나가므로,
패스스루는 향후 경로 변경에 대한 안전장치 역할을 한다.

**프롬프트 구조:**

- 시스템 프롬프트(`clarifier_system.txt`): intent별 응답 규칙 + few-shot 예시
- 유저 프롬프트(`clarifier_user.txt`): `{intent}`, `{ambiguities_block}`, `{query}` 플레이스홀더

**reason 계층의 명확화 경로 (clarify 미경유):**

reason 계층에서 명확화가 필요한 경우(CONFLICTED 지식 항목, 교차 DB 감지 등)
result_finalizer 또는 sql_generator가 직접 `clarification_question`을 생성하여
`_route_after_reason_finalize()`에서 바로 END로 나간다 (clarify 노드를 거치지 않음).

---

## 6. Present 계층 상세

### 6.1 SQL 실행 (execute_sql_node)

- 이중 방어: 실행 전 `validate_sql_safety()` 재검증
- 읽기 전용 계정 사용 (SELECT 전용)
- 결과 행 수 제한 (기본 10,000건)
- 실행 시간 측정 → `sql_result.execution_time_ms`

### 6.2 데이터 분석 (analyze_data_node)

`intent == DATA_ANALYSIS`인 경우에만 실행되는 3단계 프로세스:

1. **통계 분석**: LLM이 summary, insights[], statistics{} 생성
2. **시각화 판정**: LLM이 차트 유형 결정 (bar/line/pie/table/none)
3. **SVG 생성**: 3-Tier 폴백
   - Tier 1: LLM 직접 SVG 생성
   - Tier 2: 규칙 기반 chart_generator
   - Tier 3: 생략 (NONE)

### 6.3 결과 포맷팅 (format_response_node)

SQL 결과를 사용자 친화적 한국어 보고서로 변환한다.

- 기술 용어 최소화 (SQL, JOIN, WHERE 등 미사용)
- 숫자 포맷: 금액(만원/억원), 비율(%), 건수
- 날짜: "2026년 3월" 형태
- 조건 설명: 자연어로 풀어서 설명
- 데이터 부재 시: 원인 분석 + 대안 제시

---

## 7. 라우팅 함수 요약

`pipeline.py`에 정의된 모든 라우팅 함수의 분기 조건을 정리한다.

| 함수 | 위치 | 입력 조건 | 분기 |
|------|------|----------|------|
| `_route_after_preprocess` | L92 | `status == ERROR` | → error_end / resolve_history |
| `_route_after_history_resolve` | L101 | `status == AWAITING_CLARIFICATION` | → END / classify_intent |
| `_route_after_intent` | L110 | `intent 유형 + clarification_turns` | → clarify / normalize_query / reason_plan / error_end |
| `_next_after_intent` | L132 | `normalization_enabled` | → normalize_query / reason_plan |
| `_route_after_reason_plan` | L143 | `fast_path_triggered` | → reason_generate_sql / reason_explore |
| `_route_after_reason_evaluate` | L152 | `evaluate_readiness()` 반환값 | → explore / generate_sql / replan / conclude_failure / ask_user |
| `_route_after_reason_validate_sql` | L159 | `sql_validation_result.overall` | → 6가지 분기 (4.6절 참조) |
| `_route_after_reason_recover` | L201 | `phase == "DONE"` | → conclude / explore |
| `_route_after_reason_finalize` | L229 | `awaiting_clarification / error_message / validated_sql` | → END (자체 명확화 질문) / error_end / execute_sql |
| `_route_after_execution` | L231 | `status == ERROR / intent 유형` | → error_end / analyze_data / format_response |

---

## 8. 상태 모델 — 전체 레퍼런스

> **정본(SSOT):** `src/agents/state/state.py`
> 이 섹션은 정본의 스냅샷이며, 코드와 불일치 시 코드가 우선한다.

### 8.1 구조 개요

```
PipelineState (최상위)
├── 공통 ─────────── user_input, session_id, conversation_history
├── Interpret ────── preprocessed_input, intent, intent_confidence,
│                    query_category, normalized_query,
│                    clarification_question, clarification_response,
│                    awaiting_clarification, clarification_turns
├── Reason ───────── reason: ReasoningState (중첩)
│   ├── 진행 상태 ── phase
│   ├── 플래너 ───── query_decomposition, hypotheses, current_hypothesis,
│   │                execution_plan, current_step_index
│   ├── 누적 지식 ── knowledge_items, explored_use_cases, candidate_tables,
│   │                confirmed_join_path, table_resolution,
│   │                searched_queries, sampled_tables, rejected_tables,
│   │                structural_hints
│   ├── 실패 기록 ── dead_ends
│   ├── SQL ──────── generated_sql, validated_sql, sql_fix_instruction,
│   │                sql_validation_result
│   ├── 루프 제어 ── loop_guard, fast_path_triggered
│   ├── 최종 출력 ── final_status, exploration_summary
│   └── 기타 ────── cache_refs
├── Present ──────── context, sql_result, analysis_result, visualization,
│                    formatted_response
└── 관리 ─────────── status, error_message, trace_log
```

### 8.2 PipelineState 필드 상세

**중요도 기준:** 핵심 = 라우팅 판단 또는 5+파일 참조 / 중간 = 3~4파일 / 낮음 = 1~2파일

#### 공통

| 필드 | 타입 | 용도 | 참조 파일 수 | 중요도 |
| ---- | ---- | ---- | ------------ | ------ |
| `user_input` | `str` | 사용자 원본 입력 (정제 전) | 13 | 핵심 |
| `session_id` | `str` | 세션 식별자 (멀티턴 추적) | 4 | 핵심 |
| `conversation_history` | `list[dict]` | 이전 대화 턴 목록 `{role, content}` | 5 | 핵심 |

#### Interpret 계층

| 필드 | 타입 | 용도 | 참조 파일 수 | 중요도 |
| ---- | ---- | ---- | ------------ | ------ |
| `preprocessed_input` | `str` | 정제된 입력 (NFKC, 공백 정규화, 길이 제한) | 17 | 핵심 |
| `intent` | `IntentType` | 사용자 의도 분류 결과 | 6 | 핵심 |
| `intent_confidence` | `float` | 의도 분류 확신도 (0.0~1.0) | 2 | **낮음** |
| `query_category` | `str` | 의도 세부 카테고리 (LLM reason 필드) | 3 | **낮음** |
| `normalized_query` | `Any` | 8-Slot 정규화 결과 (`NormalizedQuery`) | 8 | 핵심 |
| `clarification_question` | `str` | 사용자에게 보낼 명확화 질문 | 5 | 핵심 |
| `clarification_response` | `str` | 명확화 질문에 대한 사용자 답변 | 3 | 중간 |
| `awaiting_clarification` | `bool` | 명확화 응답 대기 중 여부 (라우팅 분기) | 5 | 핵심 |
| `clarification_turns` | `int` | 명확화 왕복 횟수 (최대 제한용) | 6 | 중간 |

#### Reason 계층 (ReasoningState)

| 필드 | 타입 | 용도 | 참조 파일 수 | 중요도 |
| ---- | ---- | ---- | ------------ | ------ |
| `phase` | `Phase` | 추론 단계 (7단계) | 8 | 핵심 |
| `query_decomposition` | `dict` | 질의 분해 결과 (플래너 산출물) | 5 | 중간 |
| `hypotheses` | `list[Hypothesis]` | 탐색 가설 목록 | 5 | 핵심 |
| `current_hypothesis` | `Optional[Hypothesis]` | 현재 활성 가설 | 5 | 핵심 |
| `execution_plan` | `list[ExecutionStep]` | 탐색 도구 실행 계획 | 4 | 핵심 |
| `current_step_index` | `int` | 실행 계획 내 현재 위치 | 3 | 중간 |
| `knowledge_items` | `list[KnowledgeItem]` | 탐색으로 축적된 지식 | 8 | 핵심 |
| `explored_use_cases` | `list[dict]` | 탐색된 활용사례 | 5 | 중간 |
| `candidate_tables` | `list[CandidateTable]` | 후보 테이블 목록 | 6 | 핵심 |
| `confirmed_join_path` | `list[dict]` | 확인된 조인 경로 | 3 | 중간 |
| `table_resolution` | `Optional[TableResolution]` | 테이블 충족성 검증 결과 | 2 | **낮음** |
| `searched_queries` | `list[str]` | 실행한 검색 쿼리 목록 (중복 방지) | 5 | 중간 |
| `sampled_tables` | `list[str]` | 샘플링 완료 테이블 (중복 방지) | 5 | 중간 |
| `rejected_tables` | `list[str]` | 부적합 판정된 테이블 목록 | 4 | 중간 |
| `structural_hints` | `StructuralHints` | 유사 SQL에서 추출한 구조 힌트 (12가지) | 5 | 핵심 |
| `dead_ends` | `list[DeadEnd]` | 실패한 탐색 경로 기록 | 4 | 중간 |
| `generated_sql` | `Optional[str]` | 생성된 SQL (검증 전) | 4 | 핵심 |
| `validated_sql` | `Optional[str]` | 검증 통과 SQL (실행 대상) | 5 | 핵심 |
| `sql_fix_instruction` | `Optional[str]` | SQL 수정 지시 (재생성 시 주입) | 3 | 중간 |
| `sql_validation_result` | `Optional[SqlValidationResult]` | 3-레이어 검증 상세 결과 | 4 | 핵심 |
| `loop_guard` | `LoopGuard` | 루프 제어 카운터 (4종) | 6 | 핵심 |
| `fast_path_triggered` | `bool` | Fast-Path 바이패스 여부 | 4 | 중간 |
| `final_status` | `Literal["success","failure","pending"]` | 추론 최종 결과 | 3 | 핵심 |
| `exploration_summary` | `str` | 탐색 과정 요약 텍스트 | 3 | 중간 |
| `cache_refs` | `dict[str,str]` | 외부 캐시 참조 | **1** | **낮음** |

#### Present 계층

| 필드 | 타입 | 용도 | 참조 파일 수 | 중요도 |
| ---- | ---- | ---- | ------------ | ------ |
| `context` | `ContextInfo` | CONFIRMED 테이블 메타 + 참조 정보 | 6 | 핵심 |
| `sql_result` | `SQLResult` | SQL 실행 결과 (columns, rows, row_count) | 5 | 핵심 |
| `analysis_result` | `AnalysisResult` | 데이터 분석 결과 (summary, insights, statistics) | 3 | 중간 |
| `visualization` | `VisualizationData` | 시각화 데이터 (svg_code, chart_type, title) | 3 | 중간 |
| `formatted_response` | `str` | 사용자에게 반환할 최종 응답 텍스트 | 6 | 핵심 |

#### 상태 관리

| 필드 | 타입 | 용도 | 참조 파일 수 | 중요도 |
| ---- | ---- | ---- | ------------ | ------ |
| `status` | `QueryStatus` | 파이프라인 처리 상태 (13단계) | 10+ | 핵심 |
| `error_message` | `str` | 에러 메시지 (사용자 노출용) | 6 | 핵심 |
| `trace_log` | `list[TraceEntry]` | 추론 과정 추적 로그 | 10+ | 핵심 |

### 8.3 열거형 (Enum) 정의

#### IntentType — 사용자 의도 유형

| 값 | 설명 | 라우팅 |
| --- | ---- | ------ |
| `DATA_EXTRACTION` | 데이터 추출 요청 | → normalize_query → reason |
| `DATA_ANALYSIS` | 데이터 분석 요청 | → normalize_query → reason → analyze_data |
| `CLARIFICATION_NEEDED` | 모호한 데이터 요청 | → clarify → END |
| `GENERAL_QUESTION` | 업무 일반 질문 | → clarify → END |
| `CASUAL_TALK` | 인사, 잡담 | → clarify → END |
| `META_QUESTION` | 시스템/데이터 메타 질문 | → clarify → END |
| `UNKNOWN` | 미분류 (초기값) | — |

#### QueryStatus — 파이프라인 처리 상태

```
PENDING → PREPROCESSING → INTENT_CLASSIFIED → QUERY_NORMALIZED
  → (Reason 내부 처리) → SQL_VALIDATED → EXECUTED
  → ANALYZED → FORMATTED → COMPLETED
  ├→ ERROR (어디서든 전환 가능)
  ├→ AWAITING_CLARIFICATION (어디서든 전환 가능)
  └→ SQL_RETRY (sql_generator 재시도 중)
```

#### Phase — Reason 추론 단계

```
PLANNING → EXPLORING → VERIFYING → GENERATING → VALIDATING → REPLANNING → DONE
```

#### ConfidenceStatus — 지식 항목 상태

```
UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED
                                  ↘ CONFLICTED → (사용자 확인)
```

#### ValidationOverall — SQL 검증 결과

| 값 | 설명 | 후속 라우팅 |
| --- | ---- | ---------- |
| `SUCCESS` | 검증 통과 | → result_finalizer |
| `FAIL_SYNTAX` | 구문 오류 | → sql_generator (재시도) |
| `FAIL_SEMANTIC_LOCAL` | 의미 오류 (로컬 수정 가능) | → sql_generator 또는 recovery_planner |
| `FAIL_STRUCTURAL` | 구조적 불일치 | → recovery_planner |
| `FAIL_EMPTY` | 결과 0건 | → recovery_planner |
| `FAIL_DB_ERROR` | DB 실행 오류 | → recovery_planner |

#### FailureType — 탐색 실패 유형

`no_use_case` / `no_table` / `term_unresolvable` / `sql_syntax` / `sql_semantic_local` / `sql_structural` / `empty_result` / `db_error`

### 8.4 서브타입 구조

```
KnowledgeItem          — key, value, confidence, status(ConfidenceStatus), source, evidence[], is_critical
Hypothesis             — hypothesis_id, description, based_on_use_case, missing_terms[], priority, strategy, status
ExecutionStep          — step, tool, input, purpose, expected_output, status, result_ref, insight
CandidateTable         — table_name, schema_name, db_source, role, relevant_columns[], column_alt_names{},
│                        join_keys[], missing_coverage[], key_date_columns[], observed_date_columns[],
│                        sample_rows[], inferred_* (5 fields), inference_confidence
├── KeyDateColumn      — column_name, suffix, source
└── ObservedDateColumn — column_name, date_range, date_pattern
DeadEnd                — hypothesis_id, reason, tried_tables[], rejected_tables[], tried_terms[], failure_type
ColumnMapping          — need, table, column, confidence
TableResolution        — can_resolve, column_mapping[], missing_info[], join_needed, join_path, main_table, reasoning
LoopGuard              — total_tool_calls, replan_count, generate_attempts, local_fix_count
SqlValidationResult    — layer1~3_status, layer2_passed[], layer2_failed[], layer2_failure_type, layer3_row_count,
                         layer3_is_sane, overall(ValidationOverall)
StructuralHints        — join_patterns[], code_columns{}, agg_expressions[], date_filters[],
                         source_tables[], select_columns[], group_by_columns[], order_by_columns[],
                         limit_value, has_distinct, has_subquery, has_having
ContextInfo            — table_metas[], past_sqls[], report_sqls[], manual_references[], domain_terms{},
                         table_disambiguation_guide, vector_past_sqls[], failed_sources[]
SQLResult              — columns[], rows[], row_count, execution_time_ms
AnalysisResult         — summary, insights[], statistics{}
VisualizationData      — svg_code, chart_type(VisualizationType), title
TraceEntry             — node, action, detail, timestamp
```

### 8.5 필드 비판적 분석 — 중복·미사용·타입 문제

#### ISSUE-1: `cache_refs` — 사실상 미사용 (Dead Code)

**현상:** `ReasoningState.cache_refs`는 state.py 정의 외 **참조 0건**. 쓰는 코드가 없다.

**판정:** 삭제 후보.

**반론:** Redis 캐시 통합 시 "탐색 결과를 캐시 키로 저장 → 재사용" 용도로 설계된 것일 수 있다. 향후 캐시 전략 구현 시 필요할 가능성.

**권장:** 현재로서는 삭제. 필요 시 재추가가 1줄이므로 리스크 없음.

---

#### ISSUE-2: `intent_confidence` — 쓰기만 하고 읽지 않음

**현상:** intent_classifier에서 값을 기록하지만 (2곳), **라우팅이나 다운스트림에서 참조하는 곳이 0건**. `_route_after_intent()`는 `state.intent` 값만 보고 분기한다.

**판정:** 현재 불필요.

**반론:** 확신도 기반 "낮은 확신 시 명확화 질문" 로직을 추가한다면 핵심 필드가 된다. 또한 trace/evaluation에서 품질 분석용으로 활용 가능.

**권장:** 유지하되, 라우팅에 활용하지 않는 한 "진단용(diagnostic)" 필드로 명시. 활용 계획이 없으면 삭제 검토.

---

#### ISSUE-3: `query_category` — `intent`와 역할 경계 모호

**현상:** `intent`가 `DATA_EXTRACTION` 등 대분류, `query_category`가 LLM이 반환한 원문 카테고리(`"DATA_QUERY"` 등). 그런데 query_category를 참조하는 곳은 intent_classifier(쓰기)와 insight_builder(읽기) **2곳뿐**.

**판정:** intent로 충분하며 query_category는 중복.

**반론:** intent는 파이프라인 코드가 사용하는 enum이고, query_category는 LLM 원문 응답을 보존하는 것. LLM 응답 디버깅이나 프롬프트 튜닝 시 원문이 필요할 수 있다.

**권장:** trace_log에 기록하는 것으로 대체 가능. state 필드로 유지할 필요성 낮음.

---

#### ISSUE-4: `normalized_query: Any` — 타입 안전성 위반

**현상:** 실제로는 `NormalizedQuery` 인스턴스가 들어가지만, 타입이 `Any`로 선언되어 있다. 모든 참조처에서 `hasattr(nq, "ambiguities")`, `getattr(nq, "time_range", None)` 같은 방어 코드를 쓰고 있다.

**판정:** `Optional[NormalizedQuery]`로 변경해야 한다.

**반론:** `NormalizedQuery`가 `src/models/normalization.py`에 있어서 순환 import 위험이 있을 수 있다. 실제로 state.py는 `src.models.*`에서 이미 다수 import하고 있으므로 추가해도 문제없어 보이지만, 확인 필요.

**권장:** 순환 import 검증 후 타입 교체. `TYPE_CHECKING` 가드 사용 가능.

---

#### ISSUE-5: `clarification_response` — 실질적으로 사용되지 않음

**현상:** runner.py에서 명확화 응답 시 값을 기록하고, history_resolver에서 빈 문자열로 리셋한다. **다운스트림에서 이 값을 읽어서 사용하는 곳이 0건**. 명확화 응답은 `user_input` → `preprocessed_input`으로 흘러가기 때문.

**판정:** 불필요.

**반론:** 세션 스토어에서 "마지막 명확화 응답이 뭐였는지" 추적하는 데 유용할 수 있다. 디버깅·감사 추적 용도.

**권장:** trace_log에 기록하는 것으로 대체 가능. state 최상위 필드로 둘 필요성 낮음.

---

#### ISSUE-6: `table_resolution` — 사용 범위가 매우 좁음

**현상:** table_verifier.py에서만 쓰기/읽기. 다른 노드에서 참조 0건.

**판정:** 현재는 table_verifier 전용 로컬 상태에 가까움.

**반론:** table_resolution의 `column_mapping`, `missing_info`는 sql_generator에서 매핑 검증에 활용할 수 있다. 현재 활용하지 않을 뿐 설계 의도는 크로스 노드 공유.

**권장:** 유지. 다만 sql_generator에서 실제로 활용하도록 연결하지 않으면 의미 없음.

---

#### ISSUE-7: `visualization`와 `analysis_result` 분리 — 타당한가?

**현상:** `VisualizationData`(svg_code, chart_type, title)와 `AnalysisResult`(summary, insights, statistics)가 별도 최상위 필드. 두 모델 모두 analyze_data 노드에서 생성되며, main.py에서 각각 읽음.

**판정:** 현재 구조가 타당.

**이유:** 분석과 시각화는 독립적 관심사. DATA_EXTRACTION에서는 analysis_result 없이 visualization만 생성할 수 있고(향후), 분석 결과에 시각화가 항상 동반되지 않음. 병합하면 오히려 불필요한 결합이 생긴다.

---

#### ISSUE-8: 비정형 `dict` 필드 다수 — 타입 안전성 저하

**현상:** `query_decomposition: dict`, `explored_use_cases: list[dict]`, `confirmed_join_path: list[dict]`가 비정형 dict로 선언. 어떤 키가 들어오는지 코드를 읽어야만 알 수 있다.

**판정:** Pydantic 모델로 정형화하는 것이 이상적.

**반론:** 이 필드들은 LLM이 생성한 JSON을 그대로 저장하는 경우가 많아서, 스키마가 유동적. 정형화하면 LLM 응답 파싱 실패 시 파이프라인이 깨질 수 있다.

**권장:** 최소한 TypedDict 또는 docstring으로 기대 스키마를 문서화. 완전 정형화는 LLM 응답 안정화 이후.

---

#### ISSUE-9: `rejected_tables` 중복 — DeadEnd vs ReasoningState

**현상:** `DeadEnd.rejected_tables`와 `ReasoningState.rejected_tables` 둘 다 존재. 전자는 특정 가설 실패 시의 제외 테이블, 후자는 전역 누적 제외 테이블.

**판정:** 의도적 분리이며 중복 아님.

**이유:** DeadEnd는 "가설 H1 시도 시 T1, T2를 제외했다"는 이력. ReasoningState의 rejected_tables는 "전체 탐색에서 누적으로 제외된 테이블 → 이후 가설에서도 재시도하지 않음". 스코프가 다르다.

---

### 8.6 분석 요약

| 분류 | 필드 | 권장 조치 |
| ---- | ---- | --------- |
| **삭제 후보** | `cache_refs` | 참조 0건, 즉시 삭제 가능 |
| **삭제/이관 검토** | `clarification_response` | trace_log 이관 후 삭제 가능 |
| **삭제/이관 검토** | `query_category` | trace_log 이관 후 삭제 가능 |
| **활용 계획 필요** | `intent_confidence` | 라우팅에 활용하지 않으면 진단용으로 격하 또는 삭제 |
| **타입 수정** | `normalized_query: Any` | `Optional[NormalizedQuery]`로 변경 |
| **타입 수정** | `query_decomposition: dict` 등 | TypedDict 또는 Pydantic 모델로 정형화 |
| **현행 유지** | 나머지 전체 | 적절한 사용 빈도와 설계 의도 확인됨 |

---

## 9. 프롬프트 관리

모든 프롬프트는 `resources/prompts/` 하위 3계층 디렉토리에 외부 파일로 관리된다.
`src/agents/nodes/prompts/system_prompts.py`에서 모듈 상수로 로딩한다.

**명명 규칙:** `{노드파일명}[_{하위기능}][_{phase}]_{역할}.txt`

| 디렉토리 | 파일 수 | 예시 |
|----------|---------|------|
| `interpret/` | 11 | `intent_classifier_system.txt`, `clarifier_user.txt`, `query_normalizer_phase1_user.txt` |
| `reason/` | 6 | `planner_system.txt`, `sql_generator_fix_section.txt` |
| `present/` | 8 | `analyzer_system.txt`, `formatter_user.txt` |

---

## 10. 커넥터 아키텍처

`ConnectorManager` 싱글턴이 6종 외부 시스템 연결을 관리한다.

| 커넥터 | 용도 | Dummy 모드 |
|--------|------|-----------|
| MongoDB | 테이블/컬럼 메타, 코드 메타, 비즈용어 사전 (메타 검색 주 소스) | 샘플 데이터 반환 |
| ElasticSearch | 보고서 SQL 검색 전용 (table_meta/code_meta는 하위 호환용 보존) | 샘플 데이터 반환 |
| Info DB (PostgreSQL) | 정보계 SQL 실행 (읽기 전용) | 샘플 결과 반환 |
| History DB (PostgreSQL) | 과거 SQL 이력 검색 | 샘플 SQL 반환 |
| Qdrant | 업무 매뉴얼 + SQL 이력 벡터 검색 | 샘플 문서 반환 |
| Redis | 세션 캐시 | MemoryStore 폴백 |

**폐쇄망 전환:** `.env`의 `USE_DUMMY=false` + 각 커넥터 접속 정보 설정으로 전환.
상세: `docs/guides/migration-guide.md` 참조.
