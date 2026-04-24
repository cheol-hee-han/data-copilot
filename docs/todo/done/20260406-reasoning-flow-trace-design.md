# Reasoning Flow 트레이스 설계

> 작성일: 2026-04-06
> 목적: 에이전틱 루프의 LLM 판단 흐름을 사람이 한눈에 읽을 수 있도록 추적·렌더링하는 시스템 설계
> 대상: `src/utils/tracker/`, 전체 노드(interpret/reason/present), `src/utils/tracker/visualizer.py`

---

## 0. 배경 및 동기

### 현재 Trace의 한계

현재 `DataCopilotCallbackHandler`가 생성하는 trace JSON은 기계 분석용으로 충분하지만,
**사람이 에이전트의 사고 흐름을 따라가기에는** 다음 한계가 있다.

| 갭 | 현재 | 필요 |
|----|------|------|
| 서사적 연결 | `llm_calls[]`, `decisions[]`가 별도 배열 | Input→판단→라우팅이 하나의 단위로 묶여야 함 |
| 라우팅 근거 | 코드 로직으로만 존재 | "왜 이 엣지로 갔는지" 명시 |
| 가설 라운드 | `recovery_rounds` 숫자만 존재 | 라운드별 실패→복구→재탐색 계층 표현 |
| Validator 다층 | Layer별 결과 미기록 | 어느 층에서 통과/실패했는지 |
| Recovery 사고과정 | `prompt_variables`에 200자 truncate | 7개 입력 전문 + LLM 분석/교훈/계획 |
| Analyzer/Formatter | 추적 이벤트 없음 | 분석 인사이트, 시각화 판단 근거 |

### 기존 보고서 구조와의 관계

현재 7섹션 보고서에서 **섹션 2(Decision Trail), 3(Referenced Info), 4(State Evolution)**가
Reasoning Flow에 완전히 흡수된다. 나머지 섹션은 유지.

| 현재 | 변경 |
|------|------|
| 1. Executive Summary | 유지 |
| 2. Decision Trail | **삭제** → Reasoning Flow에 흡수 |
| 3. Referenced Information | **삭제** → Reasoning Flow에 흡수 |
| 4. State Evolution | **삭제** → Reasoning Flow에 흡수 |
| 5. Node Flow | 유지 (→ 3번으로 변경) |
| 6. Performance | 유지 (→ 4번으로 변경) |
| 7. Automated Findings | 유지 (→ 5번으로 변경) |
| Appendix: Detailed Timeline | 유지 |
| Appendix: Generated SQL | 유지 |

**변경 후 보고서 구조:**

```
# Pipeline Trace: {run_id}

## 1. Executive Summary
## 2. Reasoning Flow              ← 신규 (기존 2+3+4 대체)
## 3. Node Flow                   ← 기존 5
## 4. Performance                 ← 기존 6
## 5. Automated Findings          ← 기존 7
## Appendix: Detailed Timeline
## Appendix: Generated SQL
```

---

## 1. 데이터 모델

### 1-1. 신규 Pydantic 모델 (`evaluation.py`에 추가)

```python
class RoutingDecision(BaseModel):
    """LLM/Rule 판단 후 엣지 결정."""
    next_node: str                     # 다음 노드 이름
    reason: str = ""                   # "NEW+DATA_EXTRACTION → 정규화 진행"
    is_retry: bool = False             # 재시도 여부
    retry_count: int = 0               # 몇 번째 재시도


class ReasoningStep(BaseModel):
    """에이전트 사고 흐름의 단일 단계."""

    seq: int                           # 글로벌 순번 (1부터)
    node: str                          # 노드 이름
    phase: str                         # interpret | reason | present
    round: int = 0                     # 가설 라운드 (0=초기, 1+=복구)
                                       # 소스: loop_guard.replan_count
                                       # (주의: state.recovery_rounds는 미사용 dead field)
    hypothesis_id: str = ""            # H_INIT, H_R1, ...
    step_type: str                     # llm_decision | rule_decision | tool_execution
                                       # | validation | recovery | analysis

    # 입력 요약 (사람이 읽을 수 있는 수준으로 압축)
    inputs: dict[str, Any] = Field(default_factory=dict)

    # LLM/Rule 판단 결과
    output: dict[str, Any] = Field(default_factory=dict)

    # 라우팅
    routing: RoutingDecision = Field(default_factory=RoutingDecision)

    # 메타
    duration_ms: float = 0.0
    model: str = ""                    # LLM 모델 (rule-based면 빈 문자열)
    tokens: int = 0                    # 총 토큰
    timestamp: str = Field(default_factory=now_stamp)
```

### 1-2. `EvaluationTrace`에 필드 추가

```python
class EvaluationTrace(BaseModel):
    # ... 기존 필드 유지 ...
    reasoning_flow: list[ReasoningStep] = Field(default_factory=list)  # 신규
```

---

## 2. 이벤트 디스패치

### 2-1. 이벤트 상수 추가 (`dispatch.py`)

```python
REASONING_STEP = "reasoning.step"
```

### 2-2. 디스패치 패턴 (각 노드 끝에서 1회 호출)

```python
await dispatch_tracking_event(REASONING_STEP, {
    "node": "intent_classifier",
    "phase": "interpret",
    "step_type": "llm_decision",
    "round": 0,
    "hypothesis_id": "",
    "inputs": { ... },
    "output": { ... },
    "routing": {"next_node": "normalize_query", "reason": "NEW + DATA_EXTRACTION"},
})
```

### 2-3. 콜백 핸들러 수신 (`callback_handler.py`)

`on_custom_event`에 `reasoning` 도메인 추가:

```python
case "reasoning":
    self._record_reasoning_step(node, data)

def _record_reasoning_step(self, node: str, data: dict) -> None:
    step = ReasoningStep(
        seq=self._next_reasoning_seq(),
        node=data.get("node", node),
        phase=data.get("phase", ""),
        round=data.get("round", 0),
        hypothesis_id=data.get("hypothesis_id", ""),
        step_type=data.get("step_type", ""),
        inputs=data.get("inputs", {}),
        output=data.get("output", {}),
        routing=RoutingDecision(**data.get("routing", {})),
        model=data.get("model", ""),
        tokens=data.get("tokens", 0),
        duration_ms=data.get("duration_ms", 0.0),
        timestamp=now_stamp(),
    )
    self._trace.reasoning_flow.append(step)
```

순번 관리:

```python
def __init__(self, ...):
    # ... 기존 ...
    self._reasoning_seq: int = 0

def _next_reasoning_seq(self) -> int:
    self._reasoning_seq += 1
    return self._reasoning_seq
```

---

## 3. 노드별 inputs / output 스펙

### 3-1. Intent Classifier

```python
step_type = "llm_decision"

inputs = {
    "query": "올해 지점별 여신 연체율 분석해줘",
    "history": "(없음)" | "최근 4턴: [...]",
    "clarification_history": "(없음)" | "1건: ...",
}

output = {
    "resolution": "NEW (HIGH)",
    "resolution_reason": "독립 질의, 분석 요청",
    "intent": "DATA_ANALYSIS",
    "confidence": 0.95,
    "ambiguities": [],
}

routing = {
    "next_node": "normalize_query",
    "reason": "NEW + DATA_ANALYSIS → 정규화 진행",
}
```

### 3-2. Query Normalizer (Phase 1 + Phase 2를 하나의 step으로)

```python
step_type = "llm_decision"

inputs = {
    "raw_query": "올해 지점별 여신 연체율 분석해줘",
    "clarification_context": "(없음)" | "INFER 1건: ...",
}

output = {
    "rewritten_query": "올해 지점별 여신 연체율(연체금액÷대출잔액×100)을 산출하여 비교 분석한다",
    "8_slot": {
        "intent": "AGGREGATE [COMPARE]",
        "entities": ["여신→대출 (MEDIUM)", "지점 (HIGH)"],
        "measures": ["연체율 RATIO (LOW)"],
        "time": "THIS_YEAR (올해)",
        "filters": [],
        "dimensions": ["지점 GROUP INDIVIDUAL"],
        "modifiers": [],
        "output_hint": "CHART [지점명, 연체율]",
    },
    "ambiguities": [
        {"type": "MEASURE",
         "question": "연체율 산출 기준이 다음 중 어느 것인가요?",
         "options": ["연체금액/대출잔액×100", "연체건수/총건수×100"],
         "decision": "INFER → 연체금액/대출잔액×100"},
    ],
    "search_keywords": {
        "meta": ["여신", "대출", "연체", "연체율", "연체금액", "대출잔액", "지점"],
        "vector": "올해 지점별 여신 연체율을 연체금액 대비 대출잔액으로 산출하여 분석",
    },
}

routing = {
    "next_node": "reasoning_preparer",
    "reason": "8-Slot 완료, 모호성 1건 INFER 처리",
}
```

### 3-3. Reasoning Preparer (rule-based)

```python
step_type = "rule_decision"

inputs = {
    "normalized_query": "(8-Slot 참조)",
}

output = {
    "query_decomposition": {
        "measures": ["연체율 RATIO"],
        "filters": [],
        "group_by": ["지점"],
        "order_limit": [],
    },
    "knowledge_items": [
        "K1: measure:연체율 (UNRESOLVED, critical)",
        "K2: measure:연체금액 (UNRESOLVED)",
        "K3: measure:대출잔액 (UNRESOLVED)",
    ],
    "hypothesis": "H_INIT: 유사SQL+테이블메타 기반 초기 탐색",
    "execution_plan": [
        "Step 1: search_use_cases(\"올해 지점별 ... 산출하여 조회한다, page=1\")",
        "Step 2: search_table_meta(\"여신 대출 연체 연체금액 대출잔액 지점, page=1\")",
    ],
}

routing = {
    "next_node": "context_retriever",
    "reason": "초기 탐색 계획 수립 완료 → 도구 실행",
}
```

### 3-4. Context Retriever (tool execution)

```python
step_type = "tool_execution"

inputs = {
    "hypothesis": "H_INIT",
    "plan": ["Step 1: search_use_cases", "Step 2: search_table_meta"],
}

output = {
    "results": [
        {"step": 1, "tool": "search_use_cases", "count": 3, "latency": "18.2s",
         "summary": "유사SQL 3건 (잔액 집계 패턴, 연체율 사례 없음)"},
        {"step": 2, "tool": "search_table_meta", "count": 12, "latency": "210ms",
         "summary": "후보 테이블 12건 수집"},
    ],
}

routing = {
    "next_node": "context_interpreter",
    "reason": "도구 실행 완료 → 결과 해석",
}
```

### 3-5. Context Interpreter

```python
step_type = "llm_decision"

inputs = {
    "tool_results_summary": "유사SQL 3건 + 테이블메타 12건",
    "unresolved_knowledge": ["K1: measure:연체율", "K2: measure:연체금액", "K3: measure:대출잔액"],
    "original_query": "올해 지점별 여신 연체율 분석해줘",
    "time_slot": "THIS_YEAR",
}

output = {
    "table_decisions": {
        "SELECTED": [
            "TB_ADW_LNB301M (여신기본) — 대출잔액(BAL_AMT) 보유, 지점코드(BLNG_BRCD) JOIN 가능",
            "TB_ADW_COM001M (부점정보) — 지점명(BR_NM) 제공, BLNG_BRCD로 조인 가능",
        ],
        "REJECTED": [
            "TB_ADW_LNB302M (여신실행이력) — 실행 이력 전용, 잔액/연체 정보 없음",
            "TB_ADW_LNB501P (여신상환내역) — 상환 내역 전용, 연체 직접 판단 불가",
            "외 8건",
        ],
    },
    "knowledge_updates": [
        "K3: measure:대출잔액 → CONFIRMED (LNB301M.BAL_AMT)",
        "K1: measure:연체율 → UNRESOLVED — 산출식 미확인, 연체금액 컬럼 미발견",
        "K2: measure:연체금액 → UNRESOLVED — LNB301M에 연체금액 컬럼 없음",
    ],
    "key_insights": [
        "LNB301M에 BAL_AMT(대출잔액)는 있으나 연체금액 컬럼이 없음",
        "유사SQL 3건 모두 단순 잔액 집계, 연체율 산출 사례 없음",
        "연체 관련 별도 테이블 탐색 필요",
    ],
}

routing = {
    "next_node": "readiness_gate",
    "reason": "테이블 2건 선정, K1·K2 미해소",
}
```

### 3-6. Readiness Gate (rule-based)

```python
step_type = "rule_decision"

inputs = {
    "readiness_score": 0.35,
    "knowledge_status": "1/3 CONFIRMED (33%) — K1:연체율 ✗, K2:연체금액 ✗, K3:대출잔액 ✓",
    "table_status": "2건 SELECTED",
    "pending_steps": 0,
    "replan_count": 0,
}

output = {
    "verdict": "REPLAN",
    "score_breakdown": "knowledge=0.33, critical_unresolved=2건",
    "failure_type": "TERM_UNRESOLVABLE",
    "failure_reason": "핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견",
}

routing = {
    "next_node": "recovery_agent",
    "reason": "readiness 35% < threshold → REPLAN",
}
```

### 3-7. Recovery Agent (가장 중요)

기존 `_build_prompt`가 조립하는 7개 텍스트를 **truncate 없이** 그대로 inputs에 사용한다.

```python
step_type = "recovery"

inputs = {
    "entry_source": (
        "readiness_gate에서 진입: 초기 탐색이 불충분\n"
        "실패 유형: TERM_UNRESOLVABLE\n"
        "실패 사유: 핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견"
    ),
    "confirmed_knowledge": (
        "[K3] measure:대출잔액 — CONFIRMED (BAL_AMT, ADWOWN.TB_ADW_LNB301M)"
    ),
    "unresolved_items": (
        "[K1] measure:연체율 — UNRESOLVED\n"
        "[K2] measure:연체금액 — UNRESOLVED"
    ),
    "tool_execution_history": (
        "[스텝 1] ✓ search_use_cases(\"올해 지점별 여신 연체율을..., page=1\")\n"
        "  결과: 3건 | 관련: 관련 1건(\"지점별 대출잔액 상위 조회\"), 비관련 2건\n"
        "  발견: 연체율 산출 사례 없음, 잔액 집계만 존재\n"
        "[스텝 2] ✓ search_table_meta(\"여신 대출 연체 연체금액 대출잔액 지점, page=1\")\n"
        "  결과: 12건 | 관련: SELECTED 2건(LNB301M, COM001M), REJECTED 10건\n"
        "  발견: LNB301M에 연체 관련 컬럼 없음"
    ),
    "explored_tables": (
        "- TB_ADW_LNB301M (SELECTED): 여신기본 — 대출잔액 보유\n"
        "  컬럼: ACN, STD_DT, LN_DCD, BAL_AMT, BLNG_BRCD, PD_CD, LN_DT (+8)\n"
        "- TB_ADW_COM001M (SELECTED): 부점정보기본\n"
        "  컬럼: BLNG_BRCD, BR_NM, RGN_CD, BIZ_DCD, USE_YN"
    ),
    "dead_ends": (
        "- [TERM_UNRESOLVABLE] 핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견"
    ),
    "sample_data": (
        "- TB_ADW_LNB301M: 0행 (미조회)\n"
        "- TB_ADW_COM001M: 0행 (미조회)"
    ),
}

output = {
    "analysis": (
        "LNB301M은 여신 기본 테이블로 대출잔액(BAL_AMT)은 있으나 "
        "연체금액 컬럼이 없다. 연체 정보는 별도의 연체 관리 테이블에 "
        "있을 가능성이 높다."
    ),
    "lessons_learned": (
        "여신기본 테이블에 연체 정보가 없음. "
        "연체는 별도 관리 테이블(연체원장 등)에서 관리될 수 있음"
    ),
    "action": "replan",
    "new_hypothesis": {
        "id": "H_R1",
        "description": "연체 전용 테이블 탐색 + 산출식 확인",
        "strategy": "연체 키워드로 테이블 재탐색, 업무 매뉴얼에서 산출식 확인",
    },
    "new_plan": [
        "Step 1: search_table_meta(\"연체 연체금액 연체원장 여신연체, page=1\")",
        "Step 2: search_manual(\"여신 연체율 산출식, page=1\")",
        "Step 3: search_biz_terms(\"연체율, page=1\")",
    ],
}

routing = {
    "next_node": "context_retriever",
    "reason": "replan → 가설 H_R1로 재탐색 (Round 1)",
}
```

**구현 포인트**: `_build_prompt` 반환값 확장 (**recovery_agent 전용**)

`recovery_agent._build_prompt`만 직접 `prompt.replace()` + 200자 truncate를 수행한다.
다른 노드(`sql_generator`, `sql_validator`, `context_interpreter`)는 `render_prompt()` 유틸리티를
사용하며, 이 함수는 **truncate 없이** full text 변수를 반환하므로 추가 변경이 불필요하다.

`_build_prompt`의 호출부는 `_build_recovery_plan()` **1곳**뿐이므로 변경 영향 범위는 극소.

```python
# 현재 (recovery_agent.py:605)
return prompt, variables                  # variables = 200자 truncated

# 변경
return prompt, variables, replacements    # replacements = full text 원본
```

| 노드 | 치환 방식 | truncate | 추가 변경 |
| ---- | -------- | -------- | -------- |
| recovery_agent | 직접 `prompt.replace()` | **200자** | 3-tuple 반환 필요 |
| sql_generator | `render_prompt()` | 없음 | 불필요 — 기존 `variables`가 full text |
| sql_validator | `render_prompt()` | 없음 | 불필요 |
| context_interpreter | `render_prompt()` | 없음 | 불필요 |

### 3-8. SQL Generator

```python
step_type = "llm_decision"

inputs = {
    "tables": [
        "LNB301M: 15컬럼 (ACN, STD_DT, BAL_AMT, BLNG_BRCD, ...)",
        "LNB401P: 10컬럼 (ACN, STD_DT, OVDU_AMT, ...)",
        "COM001M: 5컬럼 (BLNG_BRCD, BR_NM, ...)",
    ],
    "confirmed_terms": [
        "K1: 연체율 = OVDU_AMT/BAL_AMT×100 (CONFIRMED, 업무매뉴얼)",
        "K2: 연체금액 = LNB401P.OVDU_AMT (CONFIRMED)",
        "K3: 대출잔액 = LNB301M.BAL_AMT (CONFIRMED)",
    ],
    "reference_sqls": ["유사SQL#1: SELECT ... (sim=0.52)"],
    "dead_ends": [
        "- [TERM_UNRESOLVABLE] LNB301M에 연체금액 없음 (교훈: 연체는 LNB401P)",
    ],
    "failure_reason": None,    # 재시도 시에만 채워짐
    "fix_section": None,       # 재시도 시에만 채워짐
    "attempt": 1,
}

output = {
    "status": "success",
    "sql": "SELECT C.BR_NM AS 지점명, ... ORDER BY 연체율 DESC",
    "explanation": "LNB301M(잔액)과 LNB401P(연체)를 LEFT JOIN, NULLIF로 0 나누기 방어",
}

routing = {
    "next_node": "sql_validator",
    "reason": "SQL 생성 완료 → 검증 진행",
}
```

**재시도 시 inputs 변경 사항:**

```python
inputs = {
    # ... 기존 + 추가:
    "failure_reason": "STD_DT = CURRENT_DATE에 데이터 없음",
    "fix_section": "MAX(STD_DT) 서브쿼리로 최근 기준일 사용 필요",
    "attempt": 2,
}

routing = {
    "next_node": "sql_validator",
    "reason": "SQL 재생성 완료 → 재검증",
    "is_retry": True,
    "retry_count": 1,
}
```

### 3-9. SQL Validator

```python
step_type = "validation"

inputs = {
    "sql": "(생성된 SQL)",
    "query_decomposition": "measures=[연체율 RATIO], group_by=[지점]",
}

output = {
    "layer1_rule": {"status": "PASS", "detail": "안전성/구문 통과"},
    "layer2a_structural": {"status": "PASS", "detail": "C-22 커버리지 충족"},
    "layer3_execution": {"status": "PASS", "rows": 42, "latency": "12.3ms"},
    "layer2b_semantic": {
        "status": "PASS",
        "checks": {
            "measure_reflected": "✅ SUM(OVDU_AMT)/SUM(BAL_AMT)×100 = 연체율",
            "filter_reflected": "✅ STD_DT = CURRENT_DATE",
            "grouping_correct": "✅ GROUP BY C.BR_NM",
            "aggregation_correct": "✅ SUM + ROUND",
        },
    },
    "final_verdict": "PASS",
}

routing = {
    "next_node": "execute_sql",
    "reason": "전 Layer 통과 → 실행",
}
```

**실패 시 output 예시:**

```python
output = {
    "layer1_rule": {"status": "PASS"},
    "layer2a_structural": {"status": "PASS"},
    "layer3_execution": {"status": "FAIL", "rows": 0, "latency": "5.2ms"},
    "layer2b_semantic": {
        "status": "FAIL",
        "classification": "structural",
        "checks": {
            "measure_reflected": "✅",
            "filter_reflected": "❌ STD_DT=CURRENT_DATE에 데이터 없음, MAX(STD_DT) 필요",
            "grouping_correct": "✅",
        },
        "feedback": "정보계는 배치 적재이므로 CURRENT_DATE에 데이터 없음",
    },
    "final_verdict": "FAIL (SQL_STRUCTURAL)",
}

routing = {
    "next_node": "recovery_agent",
    "reason": "structural 실패 → recovery",
    "is_retry": False,
}
```

### 3-10. Analyzer

```python
step_type = "analysis"

inputs = {
    "query": "올해 지점별 여신 연체율 분석해줘",
    "sql_result": {"row_count": 42, "columns": ["지점명", "연체금액합계", "대출잔액합계", "연체율"]},
    "viz_eligible": True,
}

output = {
    "summary": "42개 지점 평균 연체율 1.24%, 강남지점(3.87%) 최고",
    "insights": [
        "강남·서초 지점이 타 지점 대비 2배 이상 높음",
        "상위 5개 고연체 지점이 전체 연체금액의 41% 차지",
        "전체 86%가 연체율 2% 미만으로 양호",
    ],
    "recommendations": ["강남·서초 지점 연체 원인 심층 분석 필요"],
    "viz_judgment": "APPROVED — 지점별 비교에 가로막대 차트 적합",
    "chart_type": "BARCHART",
}

routing = {
    "next_node": "format_response",
    "reason": "분석 완료, 시각화 생성 완료",
}
```

### 3-11. Formatter

```python
step_type = "llm_decision"

inputs = {
    "user_input": "올해 지점별 여신 연체율 분석해줘",
    "sql_result": "42건",
    "analysis_result": "(인사이트 3건, 시각화 1건)",
}

output = {
    "format": "마크다운 테이블 + 인사이트 + 시각화(SVG)",
    "auto_resolved_notice": "연체율 산출 기준: 연체금액÷대출잔액×100 (자동 해석)",
    "inference_disclaimer": "(없음 — 모든 항목 CONFIRMED)",
    "trace_summary_appended": True,
}

routing = {
    "next_node": "(완료)",
    "reason": "최종 응답 생성 완료",
}
```

---

## 4. Markdown 렌더링 설계

### 4-1. 렌더러 함수 (`visualizer.py` 또는 신규 파일)

```python
def render_reasoning_flow(trace_data: dict[str, Any]) -> str:
    """reasoning_flow를 시간순 서사형 Markdown으로 렌더링한다.

    reasoning_flow가 있으면 사용하고,
    없으면 기존 decisions/llm_calls/nodes에서 fallback 렌더링한다.
    """
    reasoning_flow = trace_data.get("reasoning_flow", [])
    if reasoning_flow:
        return _render_from_reasoning_flow(reasoning_flow, trace_data)
    # 하위 호환: 기존 3개 함수 호출
    return (
        render_decision_trail(trace_data)
        + render_referenced_info(trace_data)
        + render_state_evolution(trace_data)
    )
```

### 4-2. 포맷 규칙

| 데이터 유형 | 렌더링 방식 |
|------------|-----------|
| 단순 문자열/숫자 | 인라인: `query: "올해 지점별 여신 연체율 분석해줘"` |
| 짧은 리스트 (원소 ≤ 50자) | 한 줄: `entities: [여신→대출, 지점]` |
| 긴 리스트 (원소 dict 등) | 각 원소 한 줄씩 들여쓰기 |
| 8-Slot 정규화 | 2열 테이블 `| Slot | 값 |` |
| Layer별 검증 결과 | 3열 테이블 `| Layer | 결과 | 상세 |` |
| tool 실행 결과 | 4열 테이블 `| Step | Tool | 결과 | 소요 |` |
| SQL | 코드블록 ` ```sql ``` ` |
| Recovery 7개 입력 | 각각 `► 소제목` + 내용 블록 |
| 라우팅 | 화살표 `→ **next_node** — reason` |

### 4-3. Phase/Round 구분 헤더

```markdown
### Phase 1: Interpret
### Phase 2: Reason — Round 0 (H_INIT)
### ◆ Recovery → Round 1
### Phase 2: Reason — Round 1 (H_R1)
### Phase 3: Present
```

---

## 5. 기존 프롬프트 조립 함수 재활용 전략

각 노드의 `inputs`를 구성할 때, **새로운 요약 로직을 만들지 않고**
기존에 LLM 프롬프트를 위해 이미 조립하고 있는 함수들을 재활용한다.

### 5-1. Recovery Agent

`_build_prompt`의 `replacements` dict (truncate 전 원본)를 그대로 사용:

| 프롬프트 치환키 | 조립 함수 | reasoning step inputs 키 |
|--------------|---------|----------------------|
| `{entry_source_description}` | `_build_prompt` 내부 | `entry_source` |
| `{confirmed_knowledge}` | `_build_prompt` 내부 | `confirmed_knowledge` |
| `{unresolved_items}` | `_build_prompt` 내부 | `unresolved_items` |
| `{tool_execution_history}` | `_build_tool_execution_history()` | `tool_execution_history` |
| `{explored_tables_summary}` | `_build_prompt` 내부 | `explored_tables` |
| `{dead_ends_summary}` | `_build_prompt` 내부 | `dead_ends` |
| `{sample_data_summary}` | `_build_sample_summary()` | `sample_data` |

### 5-2. SQL Generator

`_build_agentic_prompt`는 `render_prompt()`를 사용하므로 **truncate 없이** full text `variables`를 반환한다.
추가 변경 없이 기존 반환값을 그대로 reasoning step inputs로 활용:

| 프롬프트 치환키 | reasoning step inputs 키 |
|--------------|----------------------|
| `{tables}` | `tables` |
| `{confirmed_terms}` | `confirmed_terms` |
| `{dead_ends}` | `dead_ends` |
| `{fix_section}` | `fix_section` (재시도 시만) |
| `{clarification_context}` | `clarification_context` |

### 5-3. SQL Validator

`_validate_layer2b`는 `render_prompt()`를 사용하므로 추가 변경 불필요.
각 Layer 함수의 반환값을 reasoning step output으로 조합:

| 소스 | reasoning step output 키 |
| ---- | ---------------------- |
| `_validate_layer1` 반환값 | `layer1_rule` |
| `_validate_layer2a` 반환값 | `layer2a_structural` |
| `_validate_layer3` 반환값 | `layer3_execution` |
| `_validate_layer2b` LLM 응답 | `layer2b_semantic` |

### 5-4. Context Interpreter

`_interpret_batch`는 `render_prompt()`를 사용하므로 추가 변경 불필요.
기존 `record_prompt_variables` 호출에서 사용하는 `variables`:

| 프롬프트 치환키 | reasoning step inputs 키 |
|--------------|----------------------|
| `original_query` | `original_query` |
| `time_slot` | `time_slot` |
| `knowledge_items` | `unresolved_knowledge` |
| `tool_results` | `tool_results_summary` (건수 요약) |

---

## 6. 수정 대상 파일 목록

| 파일 | 변경 내용 | 규모 |
| ---- | ------- | ---- |
| **Phase A: 인프라** | | |
| `src/config.py` | `eval_tracker_enabled` → 3개 플래그 분리 | 소 |
| `src/utils/tracker/evaluation.py` | `ReasoningStep`, `RoutingDecision` 모델 추가, `EvaluationTrace.reasoning_flow` 필드 추가 | 소 |
| `src/utils/tracker/dispatch.py` | `REASONING_STEP` 상수 추가 | 극소 |
| `src/utils/tracker/callback_handler.py` | `_record_reasoning_step()`, `resume_from()`, `save()` 개별 플래그, `_enabled` 변경 | 중 |
| `src/agents/graph/runner.py` | resume 시 이전 trace 로드 + `resume_from()`, except 블록에 `handler.save()` | 중 |
| **Phase B: 노드 디스패치** | | |
| `src/agents/nodes/interpret/intent_classifier.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| `src/agents/nodes/interpret/query_normalizer.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| `src/agents/nodes/reason/reasoning_preparer.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| `src/agents/nodes/reason/context_retriever.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| `src/agents/nodes/reason/context_interpreter.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| `src/agents/nodes/reason/readiness_gate.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| `src/agents/nodes/reason/sql_generator.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| `src/agents/nodes/reason/sql_validator.py` | `REASONING_STEP` 디스패치 추가 (Layer별 결과 포함) | 중 |
| `src/agents/nodes/reason/recovery_agent.py` | `_build_prompt` 3-tuple 반환 (호출부 1곳), `REASONING_STEP` 디스패치 추가 | 중 |
| `src/agents/nodes/present/analyzer.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| `src/agents/nodes/present/formatter.py` | `REASONING_STEP` 디스패치 추가 | 소 |
| **Phase C: 렌더링** | | |
| `src/utils/tracker/visualizer.py` | `render_reasoning_flow()` 추가, `render_full_report()` 구조 변경, fallback 보존 | 중 |

---

## 7. 출력 예시 — 전체 Reasoning Flow (1회 실패 + Recovery)

아래는 **"올해 지점별 여신 연체율 분석해줘"** 라는 DATA_ANALYSIS 질의가
Round 0에서 연체금액 컬럼 미발견으로 REPLAN 후, Round 1에서 연체 테이블을 찾아 성공하는 전체 예시.

````markdown
# Pipeline Trace: session-2048193847261

## 1. Executive Summary

**질의**: 올해 지점별 여신 연체율 분석해줘
**결과**: ✅ 성공 (1회 재탐색 후)
**소요**: 128.4s | LLM 12회, 62,480토큰

| 단계 | 결과 |
|------|------|
| 의도 분류 | DATA_ANALYSIS (95%) |
| 질문 정규화 | AGGREGATE [COMPARE] |
| 준비도 판정 | REPLAN → GENERATE (2차) |
| SQL | 1회 시도, 검증 통과, 실행 성공 (42건) |
| 분석 | 인사이트 3건, 시각화 BARCHART |

---

## 2. Reasoning Flow

> 총 소요 128.4s · LLM 12회 · 62,480tok
> 경로: intent → normalize(×2) → prepare → retrieve → interpret → gate(**REPLAN**)
> → recovery → retrieve② → interpret② → gate(**GENERATE**) → generate → validate(PASS)
> → execute → analyze(×3) → format

---

### Phase 1: Interpret

#### [1] Intent Classification (12.8s, 4,820tok)

► **입력**
  query: "올해 지점별 여신 연체율 분석해줘"
  history: (없음)

◄ **LLM 판단**
  resolution: NEW (HIGH) — "독립 질의, 분석 요청"
  intent: DATA_ANALYSIS (0.95)
  → analysis_query 보존: "올해 지점별 여신 연체율 분석해줘"

→ **normalize_query** — NEW + DATA_ANALYSIS

---

#### [2] Query Normalization (18.2s, Phase1 6,540tok + Phase2 7,120tok)

► **입력**
  raw_query: "올해 지점별 여신 연체율 분석해줘"

◄ **Phase 1+2 — 8-Slot 정규화**

| Slot | 값 |
|------|---|
| intent | AGGREGATE [COMPARE] |
| entities | 여신→대출 (MEDIUM), 지점 (HIGH) |
| measures | 연체율 RATIO (LOW) ⚠ |
| time | THIS_YEAR (올해) |
| filters | (없음) |
| dimensions | 지점 GROUP INDIVIDUAL |
| modifiers | (없음) |
| output_hint | CHART [지점명, 연체율] |

  rewritten: "올해 지점별 여신 연체율(연체금액÷대출잔액×100)을 산출하여 비교 분석한다"

  ⚠ **모호성 1건**:
  "연체율 산출 기준이 다음 중 어느 것인가요?"
  ① 연체금액/대출잔액×100  ② 연체건수/총건수×100
  → **INFER: 연체금액/대출잔액×100** (은행 표준 기준)

→ **reasoning_preparer** — 모호성 1건 INFER 처리

---

### Phase 2: Reason — Round 0 (H_INIT)

> 가설: "유사 SQL + 테이블 메타 기반 초기 탐색"

#### [3] Reasoning Preparer (0.8ms, rule-based)

  query_decomposition:
    measures: [연체율 RATIO] · filters: [] · group_by: [지점] · order_limit: []
  knowledge_items:
    K1: measure:연체율 (UNRESOLVED, **critical**) — 산출식 필요
    K2: measure:연체금액 (UNRESOLVED)
    K3: measure:대출잔액 (UNRESOLVED)
  hypothesis: H_INIT
  execution_plan:
    Step 1: search_use_cases("올해 지점별 여신 연체율을 연체금액 대비 대출잔액으로 산출하여 분석, page=1")
    Step 2: search_table_meta("여신 대출 연체 연체금액 대출잔액 지점, page=1")

→ **context_retriever**

---

#### [4] Context Retriever — H_INIT (22.4s)

| Step | Tool | 결과 | 소요 |
|-----:|------|-----:|-----:|
| 1 | search_use_cases | 3건 | 18.2s |
| 2 | search_table_meta | 12건 | 210ms |

→ **context_interpreter**

---

#### [5] Context Interpretation (11.3s, 10,820tok)

► **입력**
  tool 결과 2건, unresolved: [K1:연체율, K2:연체금액, K3:대출잔액]

◄ **LLM 판단**
  **테이블 선정**:
    ✅ TB_ADW_LNB301M (여신기본) — 대출잔액(BAL_AMT) 보유, 지점코드(BLNG_BRCD) JOIN 가능
    ✅ TB_ADW_COM001M (부점정보) — 지점명(BR_NM) 제공, BLNG_BRCD로 조인 가능
    ❌ TB_ADW_LNB302M (여신실행이력) — 실행 이력 전용, 잔액/연체 정보 없음
    ❌ TB_ADW_LNB501P (여신상환내역) — 상환 내역 전용, 연체 직접 판단 불가
    ❌ 외 8건

  **지식 갱신**:
    K3: measure:대출잔액 → **CONFIRMED** (LNB301M.BAL_AMT)
    K1: measure:연체율 → **UNRESOLVED** ⚠ 산출식 미확인, 연체금액 컬럼 미발견
    K2: measure:연체금액 → **UNRESOLVED** ⚠ LNB301M에 연체금액 컬럼 없음

  **인사이트**:
    "LNB301M에 BAL_AMT(대출잔액)는 있으나 연체금액 컬럼이 없음"
    "유사SQL 3건 모두 단순 잔액 집계, 연체율 산출 사례 없음"
    "연체 관련 별도 테이블 탐색 필요"

→ **readiness_gate** — K1·K2 미해소

---

#### [6] Readiness Gate (rule-based)

| 항목 | 값 |
|------|---|
| readiness_score | 0.35 |
| knowledge | 1/3 CONFIRMED (33%) — K1:연체율 ✗, K2:연체금액 ✗, K3:대출잔액 ✓ |
| tables | 2건 SELECTED |
| pending_steps | 0 |
| replan_count | 0 |

  **verdict: REPLAN** (score 0.35 < threshold)
  failure_type: TERM_UNRESOLVABLE
  failure_reason: "핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견"

→ **recovery_agent**

---

### ◆ Recovery → Round 1

#### [7] Recovery Agent (4.1s, 8,240tok)

► **진입 맥락**
  readiness_gate에서 진입: 초기 탐색이 불충분
  실패 유형: TERM_UNRESOLVABLE
  실패 사유: 핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견

► **확인된 지식**
  [K3] measure:대출잔액 — CONFIRMED (BAL_AMT, ADWOWN.TB_ADW_LNB301M)

► **미해소 항목**
  [K1] measure:연체율 — UNRESOLVED
  [K2] measure:연체금액 — UNRESOLVED

► **도구 실행 이력**
  [스텝 1] ✓ search_use_cases("올해 지점별 여신 연체율을..., page=1")
    결과: 3건 | 관련: 관련 1건("지점별 대출잔액 상위 조회"), 비관련 2건
    발견: 연체율 산출 사례 없음, 잔액 집계만 존재
  [스텝 2] ✓ search_table_meta("여신 대출 연체 연체금액 대출잔액 지점, page=1")
    결과: 12건 | 관련: SELECTED 2건(LNB301M, COM001M), REJECTED 10건
    발견: LNB301M에 연체 관련 컬럼 없음

► **탐색된 테이블**
  - TB_ADW_LNB301M (SELECTED): 여신기본 — 대출잔액 보유
    컬럼: ACN, STD_DT, LN_DCD, BAL_AMT, BLNG_BRCD, PD_CD, LN_DT (+8)
  - TB_ADW_COM001M (SELECTED): 부점정보기본
    컬럼: BLNG_BRCD, BR_NM, RGN_CD, BIZ_DCD, USE_YN

► **이전 실패 기록**
  - [TERM_UNRESOLVABLE] 핵심 측정값 '연체율' 산출식 미확인, 연체금액 컬럼 미발견

► **샘플 데이터**
  - TB_ADW_LNB301M: 0행 (미조회)
  - TB_ADW_COM001M: 0행 (미조회)

◄ **LLM 판단**
  analysis: "LNB301M은 여신 기본 테이블로 대출잔액(BAL_AMT)은 있으나
            연체금액 컬럼이 없다. 연체 정보는 별도의 연체 관리 테이블에
            있을 가능성이 높다."
  lessons: "여신기본 테이블에 연체 정보가 없음.
           연체는 별도 관리 테이블(연체원장 등)에서 관리될 수 있음"
  action: **replan**
  new_hypothesis: H_R1 "연체 전용 테이블 탐색 + 산출식 확인"
    strategy: "연체 키워드로 테이블 재탐색, 업무 매뉴얼에서 산출식 확인"
  new_plan:
    Step 1: search_table_meta("연체 연체금액 연체원장 여신연체, page=1")
    Step 2: search_manual("여신 연체율 산출식, page=1")
    Step 3: search_biz_terms("연체율, page=1")

→ **context_retriever** — replan, 가설 H_R1로 재탐색

---

### Phase 2: Reason — Round 1 (H_R1)

> 가설: "연체 전용 테이블 탐색 + 산출식 확인"

#### [8] Context Retriever — H_R1 (8.6s)

| Step | Tool | 결과 | 소요 |
|-----:|------|-----:|-----:|
| 1 | search_table_meta | 6건 | 150ms |
| 2 | search_manual | 2건 | 5.2s |
| 3 | search_biz_terms | 1건 | 3.1s |

→ **context_interpreter**

---

#### [9] Context Interpretation — Round 1 (9.8s, 9,640tok)

► **입력**
  tool 결과 3건, unresolved: [K1:연체율, K2:연체금액]

◄ **LLM 판단**
  **테이블 선정**:
    ✅ TB_ADW_LNB401P (여신연체현황) — 연체금액(OVDU_AMT) 보유, ACN+STD_DT로 LNB301M 조인 가능
    ❌ TB_ADW_LNB402M (연체이력) — 이벤트 이력 구조, 현재 시점 집계에 부적합
    ❌ 외 4건

  **지식 갱신**:
    K2: measure:연체금액 → **CONFIRMED** ✅ (LNB401P.OVDU_AMT)
    K1: measure:연체율 → **CONFIRMED** ✅
      산출식: **연체금액(OVDU_AMT) / 대출잔액(BAL_AMT) × 100**
      근거: 업무 매뉴얼 "여신 연체율 = 연체원금 합계 ÷ 대출잔액 합계 × 100"
      추가: 용어사전 "연체율: 총 대출잔액 대비 연체금액 비율(%)"

  **인사이트**:
    "LNB401P에서 OVDU_AMT(연체금액) 확보"
    "LNB301M(잔액) + LNB401P(연체)를 ACN+STD_DT JOIN → 지점별 SUM 후 비율 산출"

→ **readiness_gate**

---

#### [10] Readiness Gate — Round 1 (rule-based)

| 항목 | 값 |
|------|---|
| readiness_score | 1.0 |
| knowledge | 3/3 CONFIRMED (100%) ✅ |
| tables | 3건 SELECTED (LNB301M, LNB401P, COM001M) |
| pending_steps | 0 |
| replan_count | 1 |

  **verdict: GENERATE** (score 1.0)

→ **sql_generator**

---

#### [11] SQL Generation (2.8s, 5,640tok)

► **입력**
  tables: [LNB301M (15cols), LNB401P (10cols), COM001M (5cols)]
  confirmed_terms:
    K1: 연체율 = OVDU_AMT/BAL_AMT×100 (CONFIRMED, 업무매뉴얼)
    K2: 연체금액 = LNB401P.OVDU_AMT (CONFIRMED)
    K3: 대출잔액 = LNB301M.BAL_AMT (CONFIRMED)
  dead_ends:
    - [TERM_UNRESOLVABLE] LNB301M에 연체금액 없음 (교훈: 연체는 LNB401P)

◄ **LLM 판단**
  status: **success**

```sql
SELECT C.BR_NM                    AS 지점명,
       SUM(B.OVDU_AMT)            AS 연체금액합계,
       SUM(A.BAL_AMT)             AS 대출잔액합계,
       ROUND(SUM(B.OVDU_AMT)
           / NULLIF(SUM(A.BAL_AMT), 0) * 100, 2) AS 연체율
  FROM ADWOWN.TB_ADW_LNB301M A
  LEFT JOIN ADWOWN.TB_ADW_LNB401P B
    ON A.ACN = B.ACN AND A.STD_DT = B.STD_DT
  JOIN ADWOWN.TB_ADW_COM001M C
    ON A.BLNG_BRCD = C.BLNG_BRCD
 WHERE A.STD_DT = CURRENT_DATE
 GROUP BY C.BR_NM
 ORDER BY 연체율 DESC
```

  explanation: "LNB301M(잔액)과 LNB401P(연체)를 LEFT JOIN, NULLIF로 0 나누기 방어"

→ **sql_validator**

---

#### [12] SQL Validation (5.2s)

| Layer | 결과 | 상세 |
|-------|------|------|
| L1 (safety+parse) | ✅ PASS | SELECT only, sqlglot 파싱 통과 |
| L2a (structural) | ✅ PASS | C-22 커버리지 충족 |
| L3 (execution) | ✅ PASS | 42건, 12.3ms |
| L2b (semantic, 4.8s) | ✅ **PASS** | |

  L2b checks:
    measure_reflected: ✅ SUM(OVDU_AMT)/SUM(BAL_AMT)×100 = 연체율
    filter_reflected: ✅ STD_DT = CURRENT_DATE
    grouping_correct: ✅ GROUP BY C.BR_NM
    aggregation_correct: ✅ SUM + ROUND

→ **execute_sql** ✓ 검증 통과

---

### Phase 3: Present

#### [13] SQL Execution

  ✅ 42건, 12.3ms

---

#### [14] Data Analysis (8.4s, 3회 LLM)

► **입력**
  query: "올해 지점별 여신 연체율 분석해줘"
  sql_result: 42건 (지점명, 연체금액합계, 대출잔액합계, 연체율)

◄ **분석 LLM** (4.2s, 2,840tok)
  summary: "42개 지점 평균 연체율 1.24%, 강남지점(3.87%) 최고"
  insights:
    ① "강남·서초 지점이 타 지점 대비 2배 이상 높음"
    ② "상위 5개 고연체 지점이 전체 연체금액의 41% 차지"
    ③ "전체 86%가 연체율 2% 미만으로 양호"
  recommendations:
    "강남·서초 지점 연체 원인 심층 분석 필요"

◄ **시각화 판단 LLM** (1.8s, 1,620tok)
  judgment: APPROVED — "지점별 비율 비교에 가로막대 차트 적합"
  chart_type: BARCHART

◄ **SVG 생성 LLM** (2.4s, 2,180tok)
  svg: (지점명 × 연체율 가로막대 차트, 상위 10개 강조)

→ **format_response**

---

#### [15] Response Formatting (1.6s, 1,420tok)

  format: 마크다운 테이블 + 인사이트 + 시각화(SVG)
  auto_resolved_notice: "연체율 산출 기준: 연체금액÷대출잔액×100 (자동 해석)"
  trace_summary:
    "1. NEW+DATA_ANALYSIS: 독립 질의
     2. 8-Slot 정규화 완료, 모호성 1건 자동추론
     3. 1차 탐색: 연체 컬럼 미발견 → 재탐색
     4. 2차 탐색: LNB401P 연체 테이블 발견, 산출식 확인
     5. 쿼리 실행 완료 (42건, 12.3ms)"

→ **완료** ✓

---

> **실행 요약**
> 총 소요: 128.4s · LLM 12회 · 62,480tok
> 가설: H_INIT(FAILED → 연체 컬럼 미발견) → H_R1(SUCCESS → LNB401P 발견)
> 경로: intent → normalize(×2) → prepare → retrieve → interpret → gate(**REPLAN**)
>       → recovery → retrieve② → interpret② → gate(**GENERATE**)
>       → generate → validate(PASS) → execute → analyze(×3) → format
>
> 복구 내역:
> ┌─ Round 0: H_INIT ─────────────────────────────────────────────┐
> │ 실패: TERM_UNRESOLVABLE — 연체금액 컬럼 미발견, 산출식 미확인  │
> │ 교훈: 여신기본에 연체 정보 없음, 별도 연체 테이블 필요          │
> └──────────────────────────────────────────────────────────────┘
> ┌─ Round 1: H_R1 ──────────────────────────────────────────────┐
> │ 전략: 연체 키워드로 테이블 재탐색 + 업무매뉴얼에서 산출식 확인  │
> │ 결과: K1~K3 모두 CONFIRMED → SQL 생성·검증 성공                │
> └──────────────────────────────────────────────────────────────┘
````

---

## 8. 하위 호환

- `reasoning_flow`가 **빈 배열**인 기존 trace JSON → `render_reasoning_flow`가 기존 3개 함수(`render_decision_trail`, `render_referenced_info`, `render_state_evolution`)를 fallback 호출
- 기존 3개 함수는 **삭제하지 않고 보존**
- 기존 `llm_calls[]`, `decisions[]`, `context_retrievals[]`, `timeline[]` → 변경 없이 유지 (기존 소비자 영향 없음)
- `reasoning_flow`는 이들과 **중복이 아닌 상위 추상화** — 기존 배열은 메트릭, reasoning_flow는 서사

---

## 9. 설계 검토 반영 사항

### 9-1. 다중 LLM 호출 노드의 step 정책

일부 노드는 내부에서 LLM을 여러 번 호출한다. reasoning step 단위 정책:

| 노드 | LLM 호출 수 | step 정책 | 근거 |
| ---- | ---------- | -------- | ---- |
| context_interpreter (Level 0) | 1회 (배치) | **1 step** | 단일 판단 행위 |
| context_interpreter (Level 1) | N회 (step별) | **1 step** (결과를 합산) | 토큰 초과 대응일 뿐 논리적 판단은 1회 |
| analyzer | 3회 (분석 + 시각화판단 + SVG) | **1 step** | 사용자 관점에서 "분석" 하나의 행위 |
| query_normalizer | 2회 (Phase1 + Phase2) | **1 step** | 이미 설계에 반영됨 (섹션 3-2) |

원칙: **"사람이 읽을 때 하나의 판단 단위"** = 1 step.
내부 LLM 호출 횟수는 output의 부가 정보로 표기 (예: `"llm_calls": 3`).

### 9-2. 미포함 노드에 대한 범위 결정

다음 노드는 reasoning(추론)을 수행하지 않으므로 **reasoning_flow 대상에서 제외**한다:

| 노드 | 제외 사유 |
| ---- | -------- |
| result_finalizer | LLM 호출 없는 순수 상태 관리. 실패 판단은 recovery_agent에서 이미 추적 |
| execute_sql | SQL 실행만 수행. 실행 결과는 sql_validator Layer 3에서 이미 캡처 |
| clarification_handler | 사용자 응답 수신/재개 메커니즘. 에이전트 추론 아님 |
| simple_responder | 비데이터 의도(CASUAL_TALK 등) 응답. reasoning 흐름과 무관 |

단, result_finalizer의 최종 verdict(SUCCESS/FAILURE/CANCELLED)는 reasoning_flow의
**마지막 step의 routing으로** 자연스럽게 표현된다:

- 성공: formatter의 `routing.next_node = "(완료)"`
- 실패: recovery_agent의 `output.action = "give_up"`, `routing.next_node = "result_finalizer"`

---

## 10. 파일 생성 정책

### 10-1. 설정 플래그

기존 `eval_tracker_enabled` 단일 플래그를 3개로 분리한다:

```python
# config.py
eval_trace_json_enabled: bool = True        # 기계 분석용 JSON
eval_trace_report_enabled: bool = True      # 기존 5섹션 보고서
eval_trace_reasoning_enabled: bool = True   # 신규 reasoning flow
```

콜백 핸들러의 이벤트 수집은 3개 중 하나라도 `True`면 활성화된다.
`save()` 내부에서 각 플래그를 개별 체크하여 파일을 선택적으로 생성한다.

```python
# callback_handler.py
def __init__(self, ...):
    self._enabled = (
        settings.eval_trace_json_enabled
        or settings.eval_trace_report_enabled
        or settings.eval_trace_reasoning_enabled
    )
```

### 10-2. 파일 명명 규칙

**공통 패턴**: `trace_{type}_{yyyymmdd}_{user-id}_{session-id}_{turn-id}.{ext}`

| 파일 | 명명 패턴 |
| ---- | --------- |
| JSON | `trace_telemetry_{yyyymmdd}_{user-id}_{session-id}_{turn-id}.json` |
| 보고서 | `trace_report_{yyyymmdd}_{user-id}_{session-id}_{turn-id}.md` |
| reasoning | `trace_reasoning_{yyyymmdd}_{user-id}_{session-id}_{turn-id}.md` |

구성요소:

| 요소 | 형식 | 예시 |
| ---- | ---- | ---- |
| `yyyymmdd` | 실행 일자 (KST) | `20260406` |
| `user-id` | 인증 사용자 ID (현재 `anonymous`) | `anonymous` |
| `session-id` | 세션 식별자 전체 | `sess-abc123` |
| `turn-id` | UUID 앞 **12자리** | `7f3a2b1c4e9d` |

예시:

```text
trace_telemetry_20260406_anonymous_sess-abc123_7f3a2b1c4e9d.json
trace_report_20260406_anonymous_sess-abc123_7f3a2b1c4e9d.md
trace_reasoning_20260406_anonymous_sess-abc123_7f3a2b1c4e9d.md
```

**turn-id를 12자리로 하는 근거**:

- 8자리(32bit): 전역 77,000건에서 50% 충돌. `turn_id` 단독 검색 시 위험
- 12자리(48bit): 일 1만건 × 1년(365만건)에도 충돌 확률 2.3% — 실용적으로 안전
- 전체 UUID(36자): 파일명이 과도하게 길어짐
- 전체 UUID는 파일 내부 메타데이터에 기록

`turn_id`는 새 질의 시에만 생성되므로 (resume 시 기존 State의 turn_id를 유지),
명확화 왕복을 포함한 **"하나의 질의 해결 단위" = turn_id 1개 = 파일 1개**가 보장된다.

파일 탐색 예시:

```bash
ls trace_*_20260406_*                    # 오늘 전체
ls trace_reasoning_*                     # reasoning만
ls trace_*_sess-abc123_*                 # 특정 세션 전체
ls trace_*_7f3a2b1c4e9d*                 # 특정 턴 (3개 파일)
```

저장 디렉토리: `{eval_tracker_output_dir}/` (기본 `logs/traces/`)

### 10-3. 명확화 턴 이어쓰기

명확화(interrupt → resume) 발생 시에도 **1개 파일로 전체 흐름을 통합**한다.

**현재 문제**: `run_pipeline` 호출마다 handler가 새로 생성되어 trace가 분리됨.

```text
[턴 1] run_pipeline("연체율 뽑아줘")
  → handler① 생성 → intent~clarification 수집 → handler①.save() → 파일 A
[턴 2] run_pipeline("연체금액 기준으로")  (resume)
  → handler② 생성 → reasoning~format 수집 → handler②.save() → 파일 B (분리됨!)
```

**변경 후**: resume 시 이전 handler의 trace를 이어받는다.

```text
[턴 1] handler① → intent~clarification 수집 → handler①.save() → 파일 A 저장
[턴 2] handler② 생성 → handler②.resume_from(파일 A) → reasoning~format 수집
       → handler②.save() → 파일 A 덮어쓰기 (같은 turn_id이므로 같은 파일명)
```

**변경 사항:**

| 파일 | 내용 | 규모 |
| ---- | ---- | ---- |
| `callback_handler.py` | `resume_from(trace_data)` 메서드 추가 — 이전 trace의 reasoning_flow, timeline, llm_calls, seq 이어받기 | 중 |
| `runner.py` (resume 분기) | 이전 trace 파일 탐색(`turn_id` 기반) → `handler.resume_from()` 호출 | 소 |
| `callback_handler.py` save() | 같은 turn_id 파일명으로 저장 (이전 파일 자연 덮어쓰기) | 소 |

`resume_from` 핵심 로직:

```python
def resume_from(self, previous: dict[str, Any]) -> None:
    """이전 trace 데이터를 이어받아 연속 기록한다."""
    prev_flow = previous.get("reasoning_flow", [])
    self._trace.reasoning_flow = [
        ReasoningStep(**s) for s in prev_flow
    ]
    self._reasoning_seq = len(prev_flow)

    # 기존 수집 데이터도 이어받기
    self._trace.timeline = [
        TimelineEntry(**e) for e in previous.get("timeline", [])
    ]
    self._seq = len(self._trace.timeline)
    self._trace.llm_calls = [
        LLMCallRecord(**c) for c in previous.get("llm_calls", [])
    ]
    self._trace.total_llm_calls = len(self._trace.llm_calls)
    self._trace.total_llm_tokens = sum(
        c.prompt_tokens + c.response_tokens
        for c in self._trace.llm_calls
    )
    self._trace.node_path = previous.get("node_path", [])
    self._trace.start_time = previous.get("start_time", "")
    self._trace.user_input = previous.get("user_input", "")
```

### 10-4. 실패 시 로깅

현재 에러 발생 시(`runner.py` except 블록) trace를 저장하지 않는다.
`handler.end_run() + handler.save()`를 except 블록에 추가한다:

```python
# runner.py — _execute_and_finalize except 블록
except Exception as e:
    # ── trace 저장 (에러 시에도 수집된 부분까지 기록) ──
    try:
        handler.end_run(
            final_status="error",
            error_message=str(e)[:500],
        )
        handler.save()
    except Exception:
        logger.debug("에러 trace 저장 실패", exc_info=True)

    # ── 에러 턴 DB 저장 (기존 로직 유지) ──
    try:
        ...  # 기존 save_turn 코드
    except Exception:
        logger.warning("에러 턴 저장 실패", exc_info=True)
    raise
```

변경 규모: `runner.py` except 블록에 **6줄 추가**. handler 수정 없음.

실패 trace 파일에는 에러 발생 전까지 수집된 노드 실행, LLM 호출, reasoning_flow가 포함되어
"어느 노드에서 어떤 에러로 실패했는지" 디버깅이 가능하다.

### 10-5. 생성 조건 요약

| 조건 | JSON | 보고서 | reasoning |
| ---- | ---- | ------ | --------- |
| 정상 완료 (DATA_*) | ✅ | ✅ | ✅ |
| 정상 완료 (CASUAL 등) | ✅ | ✅ | ❌ (reasoning_flow 비어있음) |
| 명확화 중간 턴 | ✅ (임시 저장, 최종 턴에 덮어쓰기) | ✅ (동일) | ✅ (동일) |
| 에러 | ✅ (수집된 부분까지) | ✅ | ✅ (수집된 부분까지) |
