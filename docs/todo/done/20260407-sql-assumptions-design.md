# SQL Generator `assumptions` 필드 설계

> SQL 생성 시 LLM이 모호한 해석을 선택한 내용을 사용자에게 표면화하는 기능

## 배경

"이번년도 예금신규 금액 top 10 지점 알려줘" 같은 질의에서 "신규 금액"이
**신규 시점의 금액**인지 **기간 내 전체 금액**인지 모호할 수 있다.

- 정규화 단계(normalizer)에서는 메타가 없어 모호성을 감지하지 못할 수 있음
- 테이블 탐색 후 sql_generator LLM이 컬럼을 보고 해석을 선택하게 됨
- 현재: LLM이 `reasons`에 "가정: ..." 형태로 출력하도록 유도하고 있으나, **success 시 reasons가 state에 저장되지 않고 드랍됨**
- 사용자는 어떤 해석으로 SQL이 생성됐는지 알 수 없음

## 설계 방향

### LLM 출력 스키마 변경

기존:
```json
{ "status", "sql", "reasons", "explanation" }
```

변경:
```json
{ "status", "sql", "failure_reasons", "assumptions", "explanation" }
```

- `reasons` → `failure_reasons` 리네이밍: fail 시 실패 사유 전용
- `assumptions`: success 시 사용자에게 알려야 할 해석적 선택 (신규)
- 필드명만으로 역할이 자명 — LLM이 success에서 failure_reasons를 채우거나,
  fail에서 assumptions를 채우는 혼동이 원천 차단됨

### 프롬프트 지시 전략

필드명 `assumptions`는 LLM 학습 데이터에서 "state your assumptions" 패턴이
빈번하여 자연스럽게 간결한 출력을 유도한다.
프롬프트 지시는 "사용자에게 알려야 할 해석 가정"으로 프레이밍하여
LLM이 사용자 관점에서 중요한 해석만 필터링하도록 한다.

### 사용자 표면화 경로: `build_auto_resolved_notice` 통합

assumptions를 별도 렌더링하지 않고, 기존 `resolved_signals` → `build_auto_resolved_notice`
파이프라인에 합류시킨다.

**이유**:
1. formatter에 이미 "조회 기준 안내:" 섹션이 있음 (normalizer INFER와 동일 위치)
2. 사용자 입장에서 "정규화 시점 추론"과 "SQL 생성 시점 추론"은 구분할 필요 없음
3. 새로운 렌더링 로직 불필요

**방법**: sql_generator_node에서 assumptions → AmbiguitySignal(INFER) 변환

```
조회 기준 안내:
- '기간' 조건이 명시되지 않아 → 최근 1개월 기준으로 조회  ← normalizer INFER
- '예금신규 금액'을 → 요청 기간 내 신규된 예금의 전체 금액으로 해석  ← sql_generator assumption
(다른 기준을 원하시면 말씀해 주세요)
```

## 변경 대상 파일

### 1. 프롬프트: `resources/prompts/reason/sql_generator_system.txt`

**출력 스키마 수정** (line 142~156)

현재:
```
{
  "status": "success" | "fail",
  "sql": "SELECT ... 또는 빈 문자열",
  "reasons": ["항목1", "항목2"] 또는 [],
  "explanation": "한 줄 설명"
}

→ success: sql 필수, reasons는 가정이 있을 때만 기재
→ fail: reasons 필수, sql은 빈 문자열
```

변경:
```
{
  "status": "success" | "fail",
  "sql": "SELECT ... 또는 빈 문자열",
  "failure_reasons": ["항목1", "항목2"] 또는 [],
  "assumptions": ["가정1", "가정2"] 또는 [],
  "explanation": "한 줄 설명"
}

→ success: sql 필수, assumptions는 해석적 선택이 있을 때만 기재
→ fail: failure_reasons 필수, sql/assumptions은 빈 문자열/배열
```

**assumptions 작성 규칙 섹션 추가** (status 판단 기준 뒤)

프롬프트 엔지니어링 검토 사항:
- (A) 판단 기준을 구체적 체크리스트로 제시 — 추상적 "다른 해석이 가능했다면"은 모델별 편차가 큼
- (B) 사고 과정 STEP과 연결 — STEP 1, 4, 6에서 해석 선택이 발생하므로 해당 시점에 판단하도록 유도
- (C) `→` 구분자 출력 형식을 명시적으로 지시 — 코드에서 split하므로 형식 준수 필수

```
### assumptions 작성 규칙

STEP 1, 2, 4, 6에서 질의의 모호한 부분을 해석할 때, "가능한 여러 해석 중 실제로 선택한 해석" 이 있을 경우 assumptions 에 작성하세요.
사용자가 결과를 보고 "내 의도와 어시스턴트의 추론이 일치했는지" 확인할 수 있도록 정보를 제공하세요.

기재 사항 체크리스트 (하나라도 해당하면 기재):
- 용어 해석: 특정 용어가 문맥상 2가지 이상으로 해석 가능한데 하나를 선택한 경우
  예: "신규" → 신규 개설 건 / 신규 유입 금액 / 신규 가입 고객
  예: "잔액" → 기말잔액 / 평균잔액 / 최저잔액
- 용어 범위: 포함/제외 범위가 불명확한 경우
  예: "여신" → 일반대출만 / 한도대출 포함 여부
- 용어 구체화: 상위 개념 용어를 구체적인 하위 항목으로 확장한 경우
  예: "마케팅 명세" → 고객명 + 이벤트ID + 이벤트설명 + 고객 상태정보 ...
- 집계 기준: 집계 방식이 불명확한데 하나를 선택한 경우
  예: "상위" → 금액 기준 / 건수 기준
- 기간 해석: 기간 범위가 불명확한데 특정 범위를 선택한 경우
  예: "최근" → 최근 1개월 / 최근 1분기
- 그 외, 해석 선택으로 인해 결과가 달라질 수 있는 경우

비기재 사항 체크리스트 (assumptions에 쓰지 마세요):
- confirmed_terms에 이미 확인된 항목
- dialect, LIMIT, 날짜 함수 등 SQL 문법 선택
- 테이블/컬럼 선택 (confirmed_terms에서 확정된 것)
- 데이터를 요청한 현업 사용자가 알 필요 없는 내용 (예를들어 테이블 선택 기준 등)

출력 형식: 반드시 "해석 대상 → 선택한 해석" 형태로 기재하세요.

- 올바른 예: "'예금신규 금액'의 해석 → 요청 기간 내 신규된 예금의 전체 금액"
- 올바른 예: "'상위' 기준 → 금액 기준 내림차순"
- 잘못된 예: "예금신규 금액을 전체 금액으로 해석했습니다" (→ 구분자 누락)

용어 규칙: 테이블명·컬럼명 등 IT 용어를 사용할 때는 반드시 "한글명(영문)" 형태로 기재하세요.
사용자는 IT 비전문자이므로 영문 식별자만으로는 의미를 파악할 수 없습니다.

- 올바른 예: "'신규' 해석 → 대출실행일자(LN_DT) 기준 당월 실행 건"
- 올바른 예: "'잔액' 해석 → 대출잔액(LN_BAL_AMT) 기말 기준"
- 잘못된 예: "'신규' 해석 → LN_DT 기준 당월 실행 건" (한글명 누락)
```

**기존 reasons에서 "가정:" 관련 지시 제거 + 리네이밍** (line 170~176)

현재 `success를 유지하는 경우` 섹션에서 reasons에 가정을 쓰라고 되어 있는데,
이걸 assumptions로 이관하고 reasons → failure_reasons로 리네이밍:

변경:
```
success를 유지하는 경우 (결과 정확성에 영향 없는 불확실성):
  - 날짜 범위 해석, 정렬 방향, 행 제한 수, 출력 컬럼 선택
  → 합리적으로 가정하고 SQL을 생성하라.
    success로 판단하고, assumptions에 해석적 선택 내용을 기재
```

**failure_reasons 작성 규칙** (기존 reasons 작성 규칙 리네이밍)

```
### failure_reasons 작성 규칙

recovery 모듈이 부족한 정보를 채울 도구를 선택할 수 있도록,
각 항목은 "무엇이 부족한지"와 "왜 필요한지"를 함께 작성하라.
```

**few-shot 예시 수정**

예시 1 (line 190~204) — success + assumptions 단일:
```json
{
  "status": "success",
  "sql": "SELECT COUNT(*) AS 실행건수, SUM(LN_EXC_AMT) AS 실행금액합계 FROM ...",
  "failure_reasons": [],
  "assumptions": ["'이번 달 신규'의 해석 → 대출실행일자(LN_DT) 기준 당월 실행 건"],
  "explanation": "이번 달 여신기본마스터에서 신규 실행 건수와 금액 합계를 집계"
}
```

예시 2 (line 208~222) — success + assumptions 없음 (모호성 없는 명확한 질의):
```json
{
  "status": "success",
  "sql": "SELECT TOP 1000 ...",
  "failure_reasons": [],
  "assumptions": [],
  "explanation": "1등급 연체 고객을 지점별로 조회..."
}
```

(신규 추가) 예시 — success + assumptions 복수:

- 복수의 해석적 선택이 동시에 필요한 케이스를 보여줌
- 한글명(영문) 형식 준수 예시

입력:

- 사용자 질의: "이번년도 예금신규 금액 top 10 지점 알려줘"
- confirmed_terms: [table:ADWOWN.TB_ADW_DEP201P → 수신기본, ...]

```json
{
  "status": "success",
  "sql": "SELECT ... ORDER BY 신규금액합계 DESC LIMIT 10",
  "failure_reasons": [],
  "assumptions": [
    "'예금신규 금액'의 해석 → 요청 기간 내 신규된 예금의 전체 잔액(수신잔액(DEP_BAL_AMT))",
    "'상위' 기준 → 금액 합계 기준 내림차순"
  ],
  "explanation": "올해 예금 신규 금액 기준 상위 10개 지점 집계"
}
```

예시 3~5 (line 226~287) — fail:
```json
{
  "status": "fail",
  "sql": "",
  "failure_reasons": ["연체율 산출식 미확인 — 연체잔액/여신잔액인지 연체건수/총건수인지 불명"],
  "assumptions": [],
  "explanation": "연체율 산출식과 코드값이 확인되지 않아 정확한 SQL 작성 불가"
}
```

### 2. 파서: `src/agents/nodes/reason/sql_generator.py`

**`_parse_sql_response()` (line 475~506)**

```python
# 변경: reasons → failure_reasons 리네이밍 + assumptions 추가
return {
    "status": status,
    "sql": data.get("sql", "").strip(),
    "failure_reasons": data.get("failure_reasons", []),  # 리네이밍
    "assumptions": data.get("assumptions", []),           # 추가
    "explanation": data.get("explanation", ""),
}
```

fallback (코드 블록 추출) 시에도 빈 리스트 반환:
```python
return {
    "status": "success",
    "sql": cleaned,
    "failure_reasons": [],  # 리네이밍
    "assumptions": [],      # 추가
    "explanation": "",
}
```

**`sql_generator_node()` fail 분기 (line 325~338) — reasons → failure_reasons**

```python
else:
    reason.generated_sql = None
    reason.failure_type = FailureType.GENERATION_FAILED
    reason.failure_reason = "\n".join(
        result.get("failure_reasons")           # 리네이밍
        or ["SQL 생성 실패 (사유 미제공)"],
    )
```

**`sql_generator_node()` success 분기 (line 314~324)**

```python
if result["status"] == "success" and result["sql"]:
    reason.generated_sql = result["sql"]
    reason.failure_type = None
    reason.failure_reason = None
    # 추가: assumptions를 임시 보관 (재시도 시 덮어쓰기됨)
    # resolved_signals로의 전환은 result_finalizer에서 수행
    reason.pending_assumptions = result.get("assumptions", [])
```

**새 함수 `_build_assumption_signals()` 추가**

`→` 구분자로 question/inferred_value를 분리하여 `build_auto_resolved_notice`
렌더링과 호환한다. (상세는 "build_auto_resolved_notice 렌더링 확인" 섹션 참조)

```python
def _build_assumption_signals(
    assumptions: list[str],
    turn_id: str | None,
) -> list[AmbiguitySignal]:
    """SQL 생성 시 assumptions를 INFER AmbiguitySignal로 변환한다."""
    if not assumptions:
        return []
    signals = []
    for text in assumptions:
        if "→" in text:
            q, v = text.split("→", 1)
            question = q.strip()
            inferred = v.strip()
        else:
            question = text
            inferred = text
        signals.append(AmbiguitySignal(
            source_node="sql_generator",
            decision="INFER",
            ambiguity_type=AmbiguityType.INTENT,
            confidence=ConfidenceLevel.MEDIUM,
            question=question,
            question_type="confirm",
            inferred_value=inferred,
            reasoning="SQL 생성 시 해석적 선택",
            turn_id=turn_id,
        ))
    return signals
```

**노드 반환값** (기존과 동일 — reason에 pending_assumptions가 포함됨)

```python
return {"reason": reason}
```

sql_generator에서는 resolved_signals에 직접 넣지 않는다.
`pending_assumptions`는 ReasoningState 내부 필드로 reason과 함께 전달된다.
최종 전환은 result_finalizer가 담당한다 (섹션 4 참조).

**tracking event에 assumptions 추가 (line 379~383)**

```python
"output": {
    "status": result["status"],
    "sql": (result.get("sql") or "")[:200],
    "explanation": result.get("explanation", ""),
    "assumptions": result.get("assumptions", []),  # 추가
},
```

### 3. State 필드 추가: `src/agents/state/state.py`

**`ReasoningState`** (line 525 부근, `generated_sql` 아래)

```python
# ── SQL 생성 가정 (재시도 시 덮어쓰기, 최종 성공 시 resolved_signals로 전환) ──
# W: GEN  R: FIN
pending_assumptions: list[str] = Field(
    default_factory=list,
)
```

### 4. 최종 전환: `src/agents/nodes/reason/result_finalizer.py`

성공 확정 시 `pending_assumptions` → AmbiguitySignal 변환 → `resolved_signals` 반환.

```python
from src.agents.nodes.reason.sql_generator import (
    _build_assumption_signals,
)

# _build_success_summary() 또는 result_finalizer_node() 내부
assumption_signals = _build_assumption_signals(
    reason.pending_assumptions,
    state.turn_id,
)
# 노드 반환값에 추가
if assumption_signals:
    updates["resolved_signals"] = assumption_signals
```

### 5. 영향 없음 (변경 불필요)

| 파일                       | 이유                                                                                                 |
| -------------------------- | ---------------------------------------------------------------------------------------------------- |
| `sql_validator.py`         | generated_sql만 참조, 메타데이터 불참조                                                              |
| `formatter.py`             | 기존 `build_auto_resolved_notice(state)` 호출이 resolved_signals의 INFER를 자동 렌더링 — 변경 불필요 |
| `clarification_context.py` | build_auto_resolved_notice가 INFER 시그널을 question → inferred_value 형태로 렌더링 — 변경 불필요    |

### 6. 선택적 개선: `src/services/insight_builder.py`

**`_build_caveats()` (line 387~418)**

assumptions가 resolved_signals에 AmbiguitySignal로 들어가므로,
insight_builder에서도 참조 가능:

```python
# 선택적: caveats에 assumptions 반영
resolved = state.get("resolved_signals", [])
sql_assumptions = [
    s for s in resolved
    if (getattr(s, "source_node", "") == "sql_generator"
        and getattr(s, "decision", "") == "INFER")
]
if sql_assumptions:
    for s in sql_assumptions:
        q = getattr(s, "question", "")
        if q:
            caveats.append(q)
```

→ insight 패널의 "주의사항" 섹션에도 assumptions가 표시됨

### 5. 테스트: `tests/auto/unit/test_sql_generator_format.py`

**추가할 테스트 케이스**:

```python
def test_parse_sql_response_with_assumptions():
    """success + assumptions 파싱"""
    raw = json.dumps({
        "status": "success",
        "sql": "SELECT * FROM TB_X LIMIT 100",
        "failure_reasons": [],
        "assumptions": ["'최근'을 최근 1개월로 해석"],
        "explanation": "단순 조회",
    })
    result = _parse_sql_response(raw)
    assert result["status"] == "success"
    assert result["assumptions"] == ["'최근'을 최근 1개월로 해석"]


def test_parse_sql_response_without_assumptions():
    """assumptions 필드 없는 LLM 응답 (하위 호환)"""
    raw = json.dumps({
        "status": "success",
        "sql": "SELECT 1",
        "failure_reasons": [],
        "explanation": "",
    })
    result = _parse_sql_response(raw)
    assert result["assumptions"] == []


def test_build_assumption_signals():
    """assumptions → AmbiguitySignal 변환"""
    signals = _build_assumption_signals(
        ["'잔액'을 기말잔액으로 해석"],
        turn_id="test-turn-001",
    )
    assert len(signals) == 1
    assert signals[0].decision == "INFER"
    assert signals[0].source_node == "sql_generator"
    assert signals[0].turn_id == "test-turn-001"


def test_build_assumption_signals_empty():
    """assumptions 비어있으면 빈 리스트"""
    assert _build_assumption_signals([], None) == []
```

## build_auto_resolved_notice 렌더링 확인

현재 `build_auto_resolved_notice`는 아래 형태로 렌더링:

```
조회 기준 안내:
- {signal.question} → {signal.inferred_value}
(다른 기준을 원하시면 말씀해 주세요)
```

assumptions를 AmbiguitySignal로 변환할 때 `question`과 `inferred_value`에
동일한 값을 넣으면 중복 표시되므로, **렌더링 형태를 고려한 값 분리가 필요**:

```python
AmbiguitySignal(
    question="'예금신규 금액'의 해석",                    # 무엇에 대한 판단인지
    inferred_value="요청 기간 내 신규된 예금의 전체 금액",  # 어떻게 해석했는지
)
```

→ 렌더링 결과:
```
조회 기준 안내:
- '기간' 조건이 명시되지 않아 → 최근 1개월 기준으로 조회
- '예금신규 금액'의 해석 → 요청 기간 내 신규된 예금의 전체 금액
(다른 기준을 원하시면 말씀해 주세요)
```

이를 위해 **프롬프트에서 assumptions 출력 형태를 구조화**할 수 있음:

```json
"assumptions": [
    "'예금신규 금액'의 해석 → 요청 기간 내 신규된 예금의 전체 금액",
    "'상위' 기준 → 금액 기준 내림차순"
]
```

`_build_assumption_signals`에서 `→` 구분자로 question/inferred_value를 분리:

```python
def _build_assumption_signals(
    assumptions: list[str],
    turn_id: str | None,
) -> list[AmbiguitySignal]:
    if not assumptions:
        return []
    signals = []
    for text in assumptions:
        if "→" in text:
            q, v = text.split("→", 1)
            question = q.strip()
            inferred = v.strip()
        else:
            question = text
            inferred = text
        signals.append(AmbiguitySignal(
            source_node="sql_generator",
            decision="INFER",
            ambiguity_type=AmbiguityType.INTENT,
            confidence=ConfidenceLevel.MEDIUM,
            question=question,
            question_type="confirm",
            inferred_value=inferred,
            reasoning="SQL 생성 시 해석적 선택",
            turn_id=turn_id,
        ))
    return signals
```

## 데이터 흐름 요약

```
┌──────────────────────────┐
│ sql_generator LLM 응답   │
│ { assumptions: [...] }   │
└────────┬─────────────────┘
         │
┌────────▼─────────────────┐
│ _parse_sql_response()    │  assumptions 필드 추출
└────────┬─────────────────┘
         │
┌────────▼─────────────────┐
│ sql_generator_node()     │  reason.pending_assumptions에 임시 보관
│                          │  (재시도 시 덮어쓰기됨)
└────────┬─────────────────┘
         │
    validator → (실패 시 재시도 → sql_generator 재진입,
                  pending_assumptions 덮어쓰기)
         │
┌────────▼─────────────────┐
│ result_finalizer         │  성공 확정 시:
│ _build_assumption_signals│  pending_assumptions → AmbiguitySignal(INFER)
└────────┬─────────────────┘
         │
┌────────▼─────────────────┐
│ resolved_signals         │  operator.add 리듀서로 누적
│ (PipelineState)          │  normalizer INFER + intent INFER + sql_gen INFER
└────────┬─────────────────┘
         │
┌────────▼─────────────────┐
│ formatter.py             │
│ build_auto_resolved_     │  turn_id 기반 INFER 필터 → 렌더링
│ notice()                 │
└────────┬─────────────────┘
         │
┌────────▼─────────────────┐
│ 사용자 응답 상단          │
│                          │
│ 조회 기준 안내:           │
│ - 질의 해석 → 선택값     │
│ (다른 기준을 원하시면...) │
└──────────────────────────┘
```

## reasons → failure_reasons 리네이밍 추가 영향

`sql_generator.py` 내부에서 `result.get("reasons")` → `result.get("failure_reasons")` 변경 **3건**:

- line 304: LLM 호출 예외 fallback dict — `"reasons"` → `"failure_reasons"`, `"assumptions": []` 추가
- line 329: fail 분기 — `failure_reason` state 필드에 저장
- line 337: logger.warning — 로그 출력

```python
# line 299~306: LLM 호출 예외 fallback (누락 주의)
except Exception as e:
    result = {
        "status": "fail",
        "sql": "",
        "failure_reasons": [f"LLM 호출 오류: {type(e).__name__}"],  # 리네이밍
        "assumptions": [],                                           # 추가
        "explanation": "",
    }
```

`_parse_sql_response()`의 반환 dict 키만 바뀌므로 외부 모듈 영향 없음
(이 함수의 반환값은 `sql_generator_node()` 내부에서만 소비됨).

## 재시도 시 assumptions 중복 누적 방지

sql_generator는 validator 실패 시 재진입될 수 있다. `resolved_signals`는
`operator.add` 리듀서이므로, 1차 시도의 assumptions가 남아있는 상태에서
2차 시도의 assumptions가 추가되어 **상충하는 가정이 공존**할 수 있다.

예:
- 1차: `"'잔액' 해석 → 기말잔액"` → resolved_signals에 추가
- validator 실패 → sql_generator 재진입
- 2차: `"'잔액' 해석 → 평균잔액"` → resolved_signals에 또 추가
- 결과: 두 가정이 모두 사용자에게 표시됨 ← 혼란

**대응**: assumptions는 **최종 성공 시에만** resolved_signals에 추가.
sql_generator_node가 success를 반환해도 이후 validator에서 실패하면
recovery → sql_generator 재진입이 발생하므로, assumptions를 즉시
resolved_signals에 넣지 않고 **ReasoningState에 임시 보관**한 뒤
**result_finalizer(최종 성공 확정 노드)에서 resolved_signals로 전환**한다.

```
변경 사항 (설계 수정):
1. ReasoningState에 pending_assumptions: list[str] 필드 추가
2. sql_generator_node: success 시 reason.pending_assumptions = result["assumptions"]
   (resolved_signals에 직접 넣지 않음)
3. result_finalizer: 성공 확정 시 pending_assumptions → AmbiguitySignal 변환
   → resolved_signals에 추가
```

이 변경으로 인한 추가 영향:

| 파일 | 변경 |
|------|------|
| `state.py` | `ReasoningState`에 `pending_assumptions: list[str] = Field(default_factory=list)` 추가 |
| `sql_generator.py` | success 시 `reason.pending_assumptions = result.get("assumptions", [])` |
| `result_finalizer.py` | `_build_assumption_signals()` 호출 → `resolved_signals` 반환 |
| `sql_generator.py` | `_build_assumption_signals()` 함수는 sql_generator.py에 두되, result_finalizer에서 import |

## 구현 순서

1. `state.py`: `ReasoningState`에 `pending_assumptions: list[str]` 필드 추가
2. 프롬프트 수정: 출력 스키마 (reasons→failure_reasons) + assumptions 규칙 + few-shot
3. 파서 수정: `_parse_sql_response()`에 failure_reasons 리네이밍 + assumptions 추출
4. `sql_generator.py`: fail 분기 reasons→failure_reasons + success 분기 `pending_assumptions` 저장 + LLM 예외 fallback dict 수정
5. `result_finalizer.py`: 성공 확정 시 `pending_assumptions` → `_build_assumption_signals()` → `resolved_signals` 반환
6. 테스트 추가
7. (선택) insight_builder caveats 연동
