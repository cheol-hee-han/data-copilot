# 파이프라인 아키텍처 분석 리포트

> **Version 1.0** | 2026-03-25
> Data Copilot 프로젝트의 Dual-State + Bridge 구조에 대한 비판적 분석 및 대안 제시

---

## 목차

1. [현재 구조 심층 분석](#1-현재-구조-심층-분석)
2. [대안 Option 분석](#2-대안-option-분석)
3. [업계 권장 아키텍처](#3-업계-권장-아키텍처)
4. [최종 권장안](#4-최종-권장안)

---

## 1. 현재 구조 심층 분석

### 1.1 구조 개요

현재 파이프라인은 두 개의 독립된 State + Graph로 구성된다:

```
┌─────────────────── Outer Pipeline (PipelineState) ───────────────────┐
│                                                                      │
│  preprocess → history_resolve → classify_intent → normalize_query   │
│                                                                      │
│       ┌──────────── agentic_entry_node (Bridge) ────────────┐       │
│       │  pipeline_to_agentic()                               │       │
│       │       ┌── Agentic Core (AgenticCoreState) ──┐       │       │
│       │       │  planner → context_explorer          │       │       │
│       │       │  → confidence_evaluator              │       │       │
│       │       │  → sql_generator → sql_validator     │       │       │
│       │       │  → recovery_planner                  │       │       │
│       │       │  → result_finalizer                  │       │       │
│       │       └──────────────────────────────────────┘       │       │
│       │  agentic_to_pipeline()                               │       │
│       └──────────────────────────────────────────────────────┘       │
│                                                                      │
│  execute_sql → analyze_data → format_response                       │
└──────────────────────────────────────────────────────────────────────┘
```

**핵심 메커니즘:**
- `agentic_entry_node`가 `PipelineState.model_dump()` -> `pipeline_to_agentic()` -> `AgenticCoreState`로 변환
- 서브그래프를 `ainvoke()`로 실행
- `agentic_to_pipeline()`으로 결과를 역변환하여 `dict`로 반환

### 1.2 장점

#### (1) 관심사 분리 (Separation of Concerns)

두 State가 각각의 책임 영역에 최적화되어 있다.

- **PipelineState** (28개 필드): 전처리, 의도분류, 세션, 명확화, 실행결과, 분석, 포맷팅 등 엔드투엔드 파이프라인의 전체 생명주기를 관리
- **AgenticCoreState** (35+ 필드): 가설(Hypothesis), 지식 항목(KnowledgeItem), 후보 테이블(CandidateTable), 루프 제어(LoopGuard), 실패 기록(DeadEnd) 등 탐색 루프에 특화된 복잡한 상태를 관리

만약 이것이 하나의 State였다면 60개 이상의 필드를 가진 거대한 모델이 되며, 각 노드가 자신과 무관한 수십 개의 필드에 노출된다. 현재 구조는 "Outer Head 노드는 AgenticCoreState를 알 필요가 없고, Agentic Core 노드는 visualization이나 formatted_response를 알 필요가 없다"는 원칙을 강제한다.

#### (2) 독립적 진화 가능성

에이전틱 코어의 내부 루프(가설 수립 -> 탐색 -> 신뢰도 평가 -> SQL 생성 -> 검증 -> 복구)는 외부 파이프라인과 독립적으로 발전할 수 있다. 예를 들어:
- 새로운 탐색 전략을 추가할 때 Outer Pipeline의 라우팅에 영향 없음
- `LoopGuard` 상수나 `MAX_REPLANS`를 변경해도 외부 파이프라인 코드 수정 불필요
- 에이전틱 코어의 테스트를 독립적으로 실행 가능 (`get_compiled_agentic_core()`)

#### (3) 소형 모델 대응 유연성

설정값 `agentic_core_enabled: bool`로 에이전틱 코어 전체를 비활성화하는 경로가 열려 있다 (현재는 선형 모드 폴백이 삭제된 상태이나, 구조적으로 다른 서브그래프로 교체가 가능). 폐쇄망에서 7B 모델로 운영할 때, 탐색 루프 대신 단순 파이프라인으로 전환하는 시나리오에 유리하다.

#### (4) 싱글톤 컴파일 최적화

`get_compiled_agentic_core()`가 모듈 레벨 싱글톤으로 서브그래프를 한 번만 컴파일한다. LangGraph 그래프 컴파일은 노드/엣지 검증 및 내부 자료구조 구축을 수반하므로, 요청마다 컴파일하지 않는 것은 올바른 설계다.

### 1.3 단점

#### (1) 상태 변환 시 정보 손실

`pipeline_to_agentic()`에서 전달하는 필드를 보자:

```python
AgenticCoreState(
    original_query=pipeline_state.get("preprocessed_input", ""),
    normalized_query=pipeline_state.get("normalized_query"),
    intent=str(intent),
    conversation_history=pipeline_state.get("conversation_history", []),
)
```

**전달되지 않는 PipelineState 필드들:**
- `session_id`: 에이전틱 코어 내부에서 세션별 캐싱/로깅 불가
- `context`: 기존에 수집된 컨텍스트가 있더라도 (예: 명확화 후 재진입) 전달되지 않음
- `sql_retry_count`, `validation_feedback`: 선형 모드의 재시도 이력이 무시됨
- `clarification_turns`: 명확화 횟수 카운터가 초기화되어, Outer와 Inner의 명확화 횟수가 별도로 관리됨
- `trace_log`: 기존 Outer Head의 트레이스가 전달되지 않음 (역변환에서 `trace_entries -> trace_log` 매핑으로 부분 보전)

**실제 기능적 문제:**
- 에이전틱 코어의 `planner_node`가 초기 컨텍스트를 다시 수집(`_collect_initial_context`)하므로, Outer에서 이미 수집한 컨텍스트와 중복 검색 발생
- 명확화 후 재진입 시 에이전틱 코어가 "처음부터" 시작하여 이전 탐색 상태가 완전히 유실됨

#### (2) 역변환의 추상화 누수

`agentic_to_pipeline()`의 변환 로직을 보면:

```python
# knowledge_items에서 CONFIRMED 테이블 추출 -> ContextInfo
confirmed_table_names = {
    ki.key.removeprefix("table:")
    for ki in agentic_state.knowledge_items
    if ki.key.startswith("table:") and ki.status == "CONFIRMED"
}
table_metas = [
    TableMeta(
        table_name=ct.table_name,
        table_description=ct.role,
        columns=[ColumnMeta(column_name=col, ...) for col in ct.relevant_columns],
    )
    for ct in agentic_state.candidate_tables
    if ct.table_name in confirmed_table_names
]
```

이 변환은 `AgenticCoreState`의 내부 표현(knowledge_items, candidate_tables)을 `PipelineState`의 외부 표현(ContextInfo.table_metas)으로 "번역"한다. 문제는:

- `ColumnMeta.column_description`, `data_type`, `is_pii`가 모두 빈 값으로 채워짐 -> Outer Tail 노드(포맷터, 분석기)가 컬럼 메타에 의존하는 경우 정보 품질 저하
- `past_sqls`, `report_sqls`, `manual_references`, `domain_terms` 등 ContextInfo의 다른 필드가 전달되지 않음 -> 에이전틱 코어가 발견한 풍부한 컨텍스트가 축약됨
- `structural_hints`가 역변환에 포함되지 않아, Outer Tail에서 활용 불가

#### (3) 직렬 호출의 오버헤드

`agentic_entry_node`에서 서브그래프를 `await compiled.ainvoke(agentic_input.model_dump())`로 호출한다. 이는:

1. `PipelineState` 전체를 `model_dump()` (직렬화)
2. `pipeline_to_agentic()` (새 객체 생성)
3. `AgenticCoreState.model_dump()` (다시 직렬화)
4. `ainvoke()` (역직렬화 + 실행)
5. `AgenticCoreState(**agentic_result)` (다시 객체 생성)
6. `agentic_to_pipeline()` (최종 dict 변환)

총 3번의 직렬화/역직렬화가 발생한다. 개별 비용은 작지만, `AgenticCoreState`에 수백 개의 `KnowledgeItem`이나 대량의 `candidate_tables`가 축적되면 무시할 수 없는 수준이 된다.

#### (4) 디버깅 및 관측성 단절

LangGraph의 기본 관측성 도구(LangSmith, 트레이싱)는 그래프의 노드 실행을 추적한다. 현재 구조에서 에이전틱 코어 전체가 `agentic_entry_node`라는 단일 노드로 나타나므로:

- LangSmith에서 에이전틱 코어 내부의 7개 노드 전이가 하나의 "블랙박스"로 보임
- 디버깅 시 어느 노드에서 문제가 발생했는지 외부에서 직접 확인 불가
- Evaluation Tracker가 Outer에서만 동작하고, Inner의 노드별 타이밍 추적이 별도 구현 필요

#### (5) 에러 복구 경로의 단순화

```python
except Exception as e:
    return {
        "error_message": "데이터 조회 중 오류가 발생했습니다.",
        "status": QueryStatus.ERROR,
    }
```

에이전틱 코어 내부에서 발생하는 다양한 실패 유형(가설 소진, 도구 타임아웃, LLM 호출 실패, 루프 한도 초과)이 모두 하나의 `except Exception`으로 뭉뚱그려진다. 서브그래프 내부에서 `result_finalizer`가 세밀한 실패 분류를 하더라도, 서브그래프 자체의 비정상 종료는 구분 없이 처리된다.

#### (6) 멀티 DB 라우팅 정보의 단절

`AgenticCoreState`의 `CandidateTable.db_source` 필드에 테이블별 DB 소스 정보가 있지만, `agentic_to_pipeline()` 역변환에서 `TableMeta`에는 `db_source` 필드가 없다. SQL 실행 노드(`execute_sql_node`)는 `manager.get_query_db()`를 사용하는데, 에이전틱 코어가 식별한 DB 소스 정보가 전달되지 않아 라우팅이 설정 기본값에 의존하게 된다.

### 1.4 종합 평가

| 측면 | 평가 | 근거 |
|------|------|------|
| 관심사 분리 | 우수 | 60+ 필드의 단일 State보다 현저히 관리 용이 |
| 데이터 완전성 | 미흡 | 변환 시 5개 이상의 필드가 유실, 특히 컨텍스트와 세션 정보 |
| 개발 생산성 | 보통 | 변환 로직 유지보수 부담이 있으나, 노드 간 독립성으로 상쇄 |
| 관측성 | 미흡 | 서브그래프 내부가 블랙박스화 |
| 성능 | 보통 | 직렬화 오버헤드가 있으나 LLM/DB I/O 대비 미미 |
| 확장성 | 우수 | 에이전틱 코어 내부 변경이 외부에 파급되지 않음 |

---

## 2. 대안 Option 분석

### Option A: 단일 State + 단일 그래프 통합

#### 구조 설명

`PipelineState`와 `AgenticCoreState`를 하나의 `UnifiedState`로 병합하고, 모든 노드를 단일 StateGraph에 배치한다.

```
UnifiedState (60+ fields)
├── 입력/세션 (PipelineState 원본)
├── 의도분류/정규화 (PipelineState 원본)
├── 명확화 (PipelineState 원본)
├── 에이전틱 탐색 (AgenticCoreState 이관)
│   ├── hypotheses, knowledge_items, candidate_tables
│   ├── execution_plan, loop_guard, dead_ends
│   └── structural_hints, sql_validation_result
├── SQL/실행/분석 (PipelineState 원본)
└── 최종 출력 (PipelineState 원본)

StateGraph(UnifiedState)
├── preprocess → history_resolve → classify_intent → normalize_query
├── planner → context_explorer → confidence_evaluator
├── sql_generator → sql_validator → recovery_planner → result_finalizer
├── execute_sql → analyze_data → format_response
└── 라우팅: 기존 Outer + Inner 라우팅 함수 통합
```

#### 장점

- **정보 손실 제로**: 변환이 없으므로 모든 필드가 항상 접근 가능
- **관측성 완전 확보**: LangSmith에서 모든 노드의 전이가 단일 그래프로 추적됨
- **코드 단순화**: `pipeline_to_agentic()`, `agentic_to_pipeline()`, `agentic_entry_node` 제거
- **디버깅 용이**: 단일 그래프 시각화로 전체 흐름 파악 가능
- **멀티 DB 라우팅 자연스러움**: `CandidateTable.db_source`가 그대로 `execute_sql_node`에서 참조 가능

#### 단점

- **거대 State 문제**: 60+ 필드의 State를 모든 노드가 공유. `preprocess_node`가 `hypotheses`를, `result_finalizer`가 `visualization`을 볼 수 있게 됨
- **노드 간 암묵적 결합**: 한 노드가 다른 노드의 내부 필드를 직접 수정하는 실수 발생 가능
- **테스트 복잡도 증가**: 에이전틱 코어만 독립 테스트하려면 60+ 필드의 초기 State를 구성해야 함
- **라우팅 복잡도**: 단일 그래프에 14개 이상의 조건부 라우팅이 얽히면 그래프가 스파게티화될 위험
- **선형/에이전틱 모드 전환**: `agentic_core_enabled=False` 시 대체 경로를 같은 그래프 안에서 관리해야 하므로 조건 분기가 더 복잡해짐

#### 마이그레이션 난이도: **높음**

- 두 State를 병합하며 필드명 충돌 해소 필요 (e.g., 양쪽의 `generated_sql`, `validated_sql`)
- 모든 Agentic 노드의 타입 힌트를 `AgenticCoreState` -> `UnifiedState`로 변경
- 기존 테스트 파일(test_agentic_core.py, test_agentic_e2e.py 등) 전면 수정
- 라우팅 함수 통합 시 경로 충돌 검증 필요

#### 적합한 상황

- 프로젝트 초기에 처음부터 설계하는 경우
- 에이전틱 코어의 복잡도가 낮고 노드 수가 적은 경우
- 개발팀이 소규모(1-2명)이어서 관심사 분리보다 단순성이 중요한 경우

---

### Option B: 현재 구조 유지 + 브릿지 최적화

#### 구조 설명

Dual-State 구조를 유지하되, 변환 함수를 보강하여 정보 손실을 최소화하고 관측성을 개선한다.

```
변경 포인트:
1. pipeline_to_agentic() 확장: session_id, trace_log, clarification_turns 전달
2. agentic_to_pipeline() 확장: 컬럼 메타 완전 보존, structural_hints, db_source 전달
3. agentic_entry_node에 세밀한 에러 분류 추가
4. EvaluationTracker를 서브그래프에도 주입 (C-18 확장)
```

#### 장점

- **최소 변경**: 기존 코드 구조 유지, 변환 함수만 수정
- **점진적 개선**: 필드별로 하나씩 추가 전달하며 검증 가능
- **기존 테스트 호환**: 테스트 코드 수정 최소화
- **관심사 분리 유지**: 두 State의 독립성 보전
- **위험도 낮음**: 프로덕션 안정성에 영향 최소

#### 단점

- **근본적 한계 미해소**: 변환 로직이 양쪽 State의 스키마 변경에 따라 계속 동기화해야 함
- **기술 부채 누적**: 양쪽 State에 필드가 추가될 때마다 변환 로직도 갱신해야 하는 유지보수 오버헤드
- **관측성 한계**: 세밀한 에러 분류를 해도, LangSmith 관점에서 여전히 단일 노드
- **직렬화 오버헤드 유지**: 3회 직렬화/역직렬화 구조 그대로

#### 마이그레이션 난이도: **낮음**

- `agentic_core.py`의 `pipeline_to_agentic()`, `agentic_to_pipeline()` 수정
- `AgenticCoreState`에 추가 입력 필드(session_id 등) 선언
- `PipelineState.context`에 db_source 관련 필드 추가
- 기존 테스트에 새 필드 전달 검증 추가

#### 적합한 상황

- 단기적으로 안정성을 우선시하는 경우
- 마이그레이션 리소스가 제한적인 경우
- 폐쇄망 배포 일정이 임박한 경우

---

### Option C: LangGraph 네이티브 서브그래프 (State Schema 활용)

#### 구조 설명

LangGraph의 공식 서브그래프 패턴을 활용한다. 핵심은 에이전틱 코어를 별도 `StateGraph`로 유지하되, 부모 그래프에 **네이티브 서브그래프 노드**로 등록하는 것이다.

LangGraph는 서브그래프를 노드로 등록할 때, 부모와 자식 State 간의 **공유 채널(shared channels)**을 통해 자동으로 상태를 매핑한다. 부모 State와 자식 State가 같은 이름의 필드를 가지면 자동 전달되고, 다른 이름의 필드는 자식 State 내부에서만 유지된다.

```python
# 부모 State: 공유 필드만 정의
class PipelineState(BaseModel):
    # 기존 필드 유지
    ...
    # 에이전틱 코어와 공유할 필드
    original_query: str = ""           # preprocessed_input에서 매핑
    normalized_query: Any = None
    intent: str = ""
    conversation_history: list = []
    generated_sql: str = ""
    validated_sql: str = ""
    # 에이전틱 코어 출력
    agentic_context: dict = {}         # 서브그래프 결과 요약

# 자식 State: 서브그래프 내부 전용 필드 + 공유 필드
class AgenticCoreState(BaseModel):
    # 공유 필드 (부모와 동일 이름)
    original_query: str = ""
    normalized_query: Any = None
    intent: str = ""
    conversation_history: list = []
    generated_sql: str = ""
    validated_sql: str = ""
    agentic_context: dict = {}
    # 서브그래프 전용 필드 (부모에 존재하지 않음)
    hypotheses: list[Hypothesis] = []
    knowledge_items: list[KnowledgeItem] = []
    ...

# 서브그래프를 노드로 등록
outer = StateGraph(PipelineState)
inner = build_agentic_core()  # StateGraph(AgenticCoreState)
outer.add_node("agentic_core", inner.compile())
```

이 방식에서 LangGraph 런타임이 자동으로:
1. 부모의 공유 필드를 자식에 전달 (입력 시)
2. 자식의 공유 필드를 부모로 역전달 (출력 시)
3. 자식 전용 필드는 자식 실행 중에만 존재 (부모에 영향 없음)

#### 장점

- **수동 변환 로직 제거**: `pipeline_to_agentic()`, `agentic_to_pipeline()` 삭제 가능
- **관측성 확보**: LangGraph 런타임이 서브그래프 내부 노드를 개별 추적 (LangSmith 네이티브 지원)
- **관심사 분리 유지**: 자식 State의 전용 필드가 부모에 노출되지 않음
- **직렬화 최적화**: LangGraph 내부 채널 매핑으로 불필요한 model_dump/재생성 회피
- **LangGraph 생태계 호환**: 체크포인팅, 휴먼인더루프, 스트리밍 등 LangGraph 기능과 자연스럽게 통합
- **부분적 상태 전달 제어**: 입출력 스키마(input/output schema)를 지정하여 공유 범위를 명시적으로 제한 가능

#### 단점

- **LangGraph 버전 의존성**: 서브그래프의 State 채널 매핑은 LangGraph 0.2+ 기능으로, API 안정성이 완전히 검증되지 않았을 수 있음
- **공유 필드 설계 주의**: 부모-자식 간 동일 이름의 필드가 자동 매핑되므로, 필드명을 의도적으로 설계해야 함. 실수로 같은 이름을 사용하면 의도치 않은 상태 전파 발생
- **Pydantic BaseModel vs TypedDict**: LangGraph의 네이티브 서브그래프는 `TypedDict` 기반 State에서 가장 잘 동작함. 현재 프로젝트의 `BaseModel` 기반 State와의 호환성 검증 필요
- **Reducer 충돌 가능성**: 부모와 자식의 같은 필드에 다른 Reducer(예: 리스트 append vs replace)가 적용되면 예측 불가능한 동작
- **폐쇄망 제약**: LangGraph 최신 버전 의존이 폐쇄망 패키지 관리에 부담

#### 마이그레이션 난이도: **중간**

- 두 State의 공유 필드를 식별하고 이름을 통일
- `agentic_entry_node` 래퍼를 제거하고 서브그래프를 직접 노드로 등록
- 변환 함수 제거, 대신 입출력 스키마 설계
- LangGraph 버전 업그레이드 및 호환성 테스트
- `BaseModel` -> `TypedDict` 전환 검토 (선택적이나 권장)

#### 적합한 상황

- LangGraph를 장기적으로 활용할 것이 확실한 경우
- 관측성과 디버깅이 핵심 요구사항인 경우
- LangGraph 최신 버전을 사용할 수 있는 환경인 경우

---

### Option D: 이벤트 기반 메시지 패싱 (Shared State 최소화)

#### 구조 설명

Agent 간 직접적인 State 공유를 최소화하고, 구조화된 메시지(요청/응답 프로토콜)로 통신하는 패턴이다. Anthropic의 "Orchestrator-Worker" 패턴과 유사하다.

```python
# 서브그래프 입출력 프로토콜 정의
class AgenticRequest(BaseModel):
    """에이전틱 코어에 전달하는 요청."""
    query: str
    normalized_query: Any
    intent: str
    session_id: str
    conversation_history: list[dict]
    prior_context: ContextInfo | None = None
    constraints: dict = {}  # 명확화 횟수, 타임아웃 등

class AgenticResponse(BaseModel):
    """에이전틱 코어가 반환하는 응답."""
    status: Literal["success", "failure", "needs_clarification"]
    validated_sql: str = ""
    context: ContextInfo
    clarification_question: str = ""
    trace_entries: list[dict] = []
    db_source: str = ""
    error_detail: str = ""
    exploration_summary: str = ""

# 브릿지 노드
async def agentic_entry_node(state: PipelineState) -> dict:
    request = AgenticRequest.from_pipeline_state(state)
    response = await run_agentic_core(request)
    return response.to_pipeline_updates()
```

#### 장점

- **명시적 계약**: 입출력 프로토콜이 인터페이스 역할을 하여 양쪽의 내부 변경에 강건
- **정보 손실 방지**: 전달해야 할 필드를 프로토콜에 명시적으로 선언
- **테스트 용이**: Request/Response 프로토콜로 에이전틱 코어를 완전 독립 테스트 가능
- **교체 가능성**: 프로토콜만 맞추면 에이전틱 코어를 완전히 다른 구현으로 교체 가능
- **마이크로서비스 전환**: 향후 에이전틱 코어를 별도 서비스로 분리할 때 프로토콜 재사용

#### 단점

- **현재 구조와 유사**: 본질적으로 현재의 변환 로직을 프로토콜로 공식화한 것이므로, 직렬화 오버헤드와 관측성 문제는 동일
- **관측성 여전히 제한적**: 서브그래프 내부가 여전히 블랙박스 (LangSmith 통합 불가)
- **프로토콜 유지보수**: 새로운 기능 추가 시 Request/Response에도 필드 추가 필요
- **과도한 추상화**: 현재 프로젝트 규모(단일 팀, 단일 서비스)에서는 불필요한 간접 레이어

#### 마이그레이션 난이도: **중간-낮음**

- `AgenticRequest`, `AgenticResponse` Pydantic 모델 정의
- 기존 변환 함수를 `from_pipeline_state()`, `to_pipeline_updates()`로 리팩토링
- 변환 로직의 필드 누락을 프로토콜 레벨에서 검증하는 테스트 추가

#### 적합한 상황

- 에이전틱 코어의 구현을 자주 교체/실험하는 경우
- 마이크로서비스 아키텍처로의 전환을 계획하는 경우
- 팀 간 분업이 필요한 경우 (Outer Pipeline 팀 vs Agentic Core 팀)

---

### Option 비교 요약

| 기준 | A: 단일 통합 | B: 브릿지 최적화 | C: 네이티브 서브그래프 | D: 메시지 패싱 |
|------|:---:|:---:|:---:|:---:|
| 정보 손실 | 없음 | 최소화 | 없음 (공유 채널) | 프로토콜 보장 |
| 관심사 분리 | 약함 | 유지 | 유지 | 강함 |
| 관측성 | 우수 | 제한적 | 우수 | 제한적 |
| LangGraph 호환 | 보통 | 보통 | 우수 | 보통 |
| 마이그레이션 비용 | 높음 | 낮음 | 중간 | 중간-낮음 |
| 유지보수 부담 | 낮음 (변환 없음) | 높음 (동기화 필수) | 낮음 (자동 매핑) | 중간 (프로토콜 관리) |
| 테스트 독립성 | 어려움 | 가능 | 가능 | 우수 |
| 소형 모델 대응 | 복잡 | 용이 | 용이 | 용이 |
| 폐쇄망 배포 | 제약 없음 | 제약 없음 | LangGraph 버전 의존 | 제약 없음 |

---

## 3. 업계 권장 아키텍처

### 3.1 LangGraph 공식 서브그래프 패턴

LangGraph 공식 문서에서 권장하는 Multi-Agent 패턴은 크게 세 가지다:

#### (1) Subgraph as Node

부모 그래프에 컴파일된 서브그래프를 노드로 직접 등록하는 패턴. 부모-자식 간 State의 같은 이름 필드가 자동 매핑된다.

```python
# 공식 패턴
parent = StateGraph(ParentState)
child = child_builder.compile()
parent.add_node("child_agent", child)  # 직접 등록
```

이 패턴에서 부모와 자식이 서로 다른 State 스키마를 사용할 수 있고, 공유하고 싶은 필드만 이름을 맞추면 된다. **현재 프로젝트의 구조와 가장 가까운 권장 패턴이며, Option C에 해당한다.**

#### (2) Shared State with Namespacing

모든 에이전트가 하나의 State를 공유하되, 필드명에 네임스페이스를 부여하여 충돌을 방지한다:

```python
class SharedState(TypedDict):
    # 공통 채널
    messages: Annotated[list, add_messages]
    # 에이전트별 네임스페이스
    planner__hypotheses: list[Hypothesis]
    explorer__knowledge: list[KnowledgeItem]
    validator__result: SqlValidationResult
```

이 패턴은 Option A의 변형으로, 이름 충돌은 방지하지만 State 크기가 큰 문제는 해결하지 못한다.

#### (3) Tool-Calling Agent

에이전트가 다른 에이전트를 "도구"로 호출하는 패턴. LangGraph의 `create_react_agent`와 호환된다. NL-to-SQL 도메인에서는 "탐색 에이전트", "SQL 생성 에이전트", "검증 에이전트"를 각각의 도구로 구성할 수 있다.

이 패턴은 LLM이 도구 선택을 직접 하므로 유연성이 높지만, 소형 모델에서는 도구 선택 정확도가 떨어지는 문제가 있다. 본 프로젝트의 폐쇄망 소형 모델 요구사항과는 적합하지 않다.

### 3.2 Anthropic 권장 에이전트 오케스트레이션 패턴

Anthropic은 "Building Effective Agents" 가이드에서 다음과 같은 패턴을 제시한다:

#### (1) Prompt Chaining (순차 체이닝)

한 LLM 호출의 출력이 다음 호출의 입력이 되는 패턴. 현재 프로젝트의 Outer Pipeline(전처리 -> 의도분류 -> 정규화)이 이 패턴에 해당한다. **간단하고 예측 가능하며 디버깅이 쉽다.**

#### (2) Orchestrator-Worker

오케스트레이터 LLM이 작업을 분배하고 워커가 실행하는 패턴. 현재 프로젝트의 에이전틱 코어(planner가 실행계획을 수립하고 context_explorer가 실행)가 이 패턴의 변형이다.

#### (3) Evaluator-Optimizer

하나의 LLM이 생성하고, 다른 LLM이 평가하여 반복 개선하는 패턴. 현재 프로젝트의 sql_generator -> sql_validator -> recovery_planner 루프가 이에 해당한다.

**Anthropic의 핵심 권고사항**: "가장 단순한 솔루션을 유지하고, 필요할 때만 복잡도를 추가하라." 불필요한 추상화보다는 각 단계의 입출력을 명확히 하고, 실패 시 원인을 쉽게 파악할 수 있는 구조가 권장된다.

### 3.3 NL-to-SQL 도메인 에이전트 아키텍처 사례

#### (1) DIN-SQL / CHESS 패턴

학술계에서 제안하는 NL-to-SQL 에이전트 구조는 대체로:

```
Schema Linking → SQL Generation → Self-Correction Loop
```

이 3단계 파이프라인이 표준이며, 현재 프로젝트의 에이전틱 코어가 이를 더 정교하게 구현한 것이다 (가설 기반 탐색 + 다층 검증 + 복구 계획). 학술 사례에서도 "Schema Linking" 단계가 별도의 상태와 로직을 가지며, SQL Generation과 분리되는 것이 일반적이다.

#### (2) MAC-SQL / Multi-Agent SQL 패턴

여러 전문화된 에이전트가 협업하는 구조:
- **Selector Agent**: 테이블/컬럼 선택
- **Decomposer Agent**: 복잡한 질의 분해
- **Refiner Agent**: SQL 검증 및 수정

이 패턴에서 각 에이전트는 독립된 상태를 가지며, 에이전트 간 통신은 구조화된 메시지로 이루어진다. Option D와 유사하나, 현재 프로젝트의 "단일 탐색 루프" 구조보다 더 분산적이다.

### 3.4 대규모 LLM 애플리케이션의 State 관리 Best Practice

#### (1) 상태 최소화 원칙

각 노드/에이전트가 필요한 최소한의 상태만 접근하도록 설계한다. LangGraph에서는 서브그래프의 입출력 스키마를 명시적으로 지정하여 이를 달성한다.

#### (2) 불변(Immutable) 상태 패턴

현재 프로젝트는 이미 이를 따르고 있다 (노드가 dict를 반환하여 LangGraph가 상태를 업데이트). 이는 올바른 설계이며, 단일 State로 통합하더라도 유지해야 한다.

#### (3) 체크포인팅과 복구

LangGraph의 `MemorySaver` 또는 `SqliteSaver`를 사용하면 각 노드 실행 후 상태를 자동 저장하여, 실패 시 중간 지점부터 재개할 수 있다. 현재의 Dual-State 구조에서는 서브그래프 내부의 체크포인트가 외부에서 접근 불가하여 이 기능을 활용하기 어렵다.

#### (4) 스트리밍과 중간 결과 전달

LangGraph의 `stream()` 메서드는 각 노드 실행 결과를 실시간으로 전달한다. 현재 구조에서는 에이전틱 코어 내부의 중간 결과(탐색 진행 상황, 신뢰도 변화)를 외부에 스트리밍하기 어렵다. 네이티브 서브그래프를 사용하면 내부 노드의 중간 결과도 스트리밍 가능하다.

---

## 4. 최종 권장안

### 4.1 프로젝트 특성 고려사항

본 프로젝트의 독특한 제약 조건을 정리한다:

| 제약 | 영향 |
|------|------|
| **폐쇄망 배포** | LangGraph 최신 버전 사용이 제한될 수 있음. 패키지 업데이트 주기가 느림 |
| **소형 모델 대응** | 에이전틱 코어의 탐색 루프가 모델 크기에 따라 단순화/비활성화되어야 함 |
| **멀티 DB 라우팅** | 에이전틱 코어에서 식별한 DB 소스 정보가 SQL 실행 단계까지 온전히 전달되어야 함 |
| **은행 보안 요구** | 감사 추적(audit trail)이 전체 파이프라인에 걸쳐 연속적이어야 함 |
| **사용자 경험** | 명확화 후 재진입 시 이전 탐색 상태를 최대한 보존해야 함 |
| **개발 속도** | 현재 개발 초기 단계, 잦은 변경이 예상됨 |

### 4.2 권장: 단기 Option B -> 중기 Option C 2단계 전환

#### 근거

1. **단기(1-2주)에는 Option B**가 합리적이다:
   - 폐쇄망 배포 일정에 맞춰 현재 구조를 안정화하는 것이 우선
   - 변환 함수 보강만으로 멀티 DB 라우팅 정보 손실, 트레이스 단절, 명확화 상태 유실 문제를 해결 가능
   - 기존 테스트와의 호환성 유지
   - 리스크 최소

2. **중기(1-2개월)에 Option C로 전환**하는 것이 최적이다:
   - LangGraph 네이티브 서브그래프로 전환하면 변환 로직 유지보수 부담이 영구적으로 제거됨
   - 관측성이 완전히 확보되어 프로덕션 운영에 유리
   - 체크포인팅, 스트리밍 등 LangGraph 고급 기능 활용 가능
   - 폐쇄망 배포 시 LangGraph 버전을 고정하여 반입하면 호환성 문제 해결 가능

3. **Option A(단일 통합)는 권장하지 않는다**:
   - 60+ 필드의 거대 State는 유지보수 부담이 크고, 소형 모델 대응을 위한 모드 전환이 복잡해짐
   - 에이전틱 코어의 독립적 테스트가 어려워져 개발 속도 저하

4. **Option D(메시지 패싱)는 과도한 추상화**:
   - 현재 단일 팀/단일 서비스 규모에서는 프로토콜 관리 오버헤드가 이점을 상회
   - 마이크로서비스 전환 계획이 구체화된 시점에 재검토

### 4.3 마이그레이션 로드맵

#### Phase 1: 브릿지 최적화 (1-2주)

목표: 현재 구조의 정보 손실 문제를 최소 변경으로 해결

**Step 1-1: 변환 함수 보강**
```
파일: src/agents/graph/agentic_core.py

pipeline_to_agentic() 확장:
  + session_id 전달
  + trace_log -> trace_entries 전달 (기존 Outer 트레이스 보존)
  + clarification_turns 전달

agentic_to_pipeline() 확장:
  + ContextInfo.past_sqls, report_sqls 등 에이전틱에서 수집한 전체 컨텍스트 전달
  + ColumnMeta에 description, data_type 보존
  + db_source 정보를 PipelineState에 전달할 수 있도록 필드 추가
  + structural_hints 요약 전달
```

**Step 1-2: 에러 분류 세밀화**
```
파일: src/agents/graph/pipeline.py - agentic_entry_node()

except Exception as e:
  → 에러 유형 분류:
    - TimeoutError → "처리 시간이 초과되었습니다"
    - LLM 호출 실패 → "AI 모델 응답 지연"
    - DB 연결 실패 → "데이터 소스 연결 오류"
    - 기타 → 기존 일반 메시지
```

**Step 1-3: 관측성 개선**
```
파일: src/agents/graph/agentic_core.py

EvaluationTracker를 서브그래프 노드에도 적용:
  - build_agentic_core()에 tracker 파라미터 추가
  - 각 노드를 tracker.track()으로 래핑
  - agentic_entry_node에서 tracker를 전달
```

깨질 수 있는 것:
- 기존 `test_agentic_core.py` 등에서 `AgenticCoreState` 초기화 시 새 필드 필요
- `agentic_to_pipeline()` 반환 dict 구조 변경으로 Outer Tail 노드의 기대값 변화

검증 방법:
- 기존 골든셋 테스트 실행
- 변환 전후 필드 완전성 단위 테스트 추가

#### Phase 2: 네이티브 서브그래프 전환 (1-2개월)

목표: LangGraph 네이티브 서브그래프로 전환하여 변환 로직 제거

**Step 2-1: State 스키마 재설계**
```
공유 필드 식별 및 이름 통일:
  PipelineState                    AgenticCoreState
  ─────────────                    ────────────────
  preprocessed_input          →    original_query         (이름 통일 필요)
  normalized_query            ↔    normalized_query       (이미 동일)
  intent                      →    intent                 (타입 차이: IntentType vs str)
  conversation_history        ↔    conversation_history   (이미 동일)
  generated_sql               ↔    generated_sql          (Optional vs str 차이)
  validated_sql               ↔    validated_sql          (Optional vs str 차이)
  context                     ←    (역변환 필요)
  trace_log                   ↔    trace_entries          (이름 통일 필요)
```

**Step 2-2: 서브그래프 등록 방식 전환**
```python
# 변경 전
workflow.add_node("agentic_entry", agentic_entry_node)

# 변경 후
agentic_graph = build_agentic_core().compile()
workflow.add_node("agentic_core", agentic_graph)
```

**Step 2-3: 변환 함수 제거**
```
삭제 대상:
  - pipeline_to_agentic()
  - agentic_to_pipeline()
  - agentic_entry_node()
```

**Step 2-4: BaseModel -> TypedDict 전환 검토**
```
LangGraph의 서브그래프 State 채널 매핑은 TypedDict에서 더 안정적.
다만, 현재 Pydantic 검증(validator, Field 등)을 활용하는 부분이 있으므로
전환 범위와 영향을 평가 후 결정.
```

깨질 수 있는 것:
- 모든 에이전틱 노드의 반환 dict에서 PipelineState 필드 이름과의 충돌 점검 필요
- 기존 `get_compiled_agentic_core()` 싱글톤 패턴 변경
- 세션 관리 로직에서 `agentic_entry_node`의 에러 핸들링 대체 필요
- 전체 테스트 스위트 재검증

검증 방법:
- LangGraph 서브그래프 통합 테스트 작성
- 골든셋 E2E 테스트로 결과 동등성 확인
- LangSmith에서 내부 노드 추적 확인

#### Phase 3: 고도화 (선택적, 장기)

- LangGraph 체크포인팅 도입 (중간 상태 저장/복구)
- WebSocket 스트리밍에 에이전틱 코어 내부 진행 상황 실시간 전달
- 소형 모델 전용 경량 서브그래프 구현 후 `agentic_core_enabled` 설정으로 라우팅
- 멀티 DB 라우팅을 에이전틱 코어 -> SQL 실행까지 일관되게 전달하는 채널 설계

---

## 부록: 핵심 파일 참조

| 파일 | 역할 |
|------|------|
| `src/agents/graph/pipeline.py` | 외부 파이프라인 그래프 정의, `agentic_entry_node` 브릿지 |
| `src/agents/graph/agentic_core.py` | 에이전틱 코어 서브그래프, 변환 함수 |
| `src/agents/state/state.py` | `PipelineState` (28 필드) |
| `src/agents/state/agentic_state.py` | `AgenticCoreState` (35+ 필드), 서브타입 10개 |
| `src/agents/nodes/reason/planner.py` | 가설 수립, 초기 컨텍스트 수집 |
| `src/agents/nodes/reason/result_finalizer.py` | 서브그래프 최종 출력 |
| `src/agents/nodes/present/sql_executor.py` | SQL 실행 (Outer Tail, PipelineState 의존) |
| `src/agents/nodes/present/analyzer.py` | 데이터 분석 (Outer Tail, PipelineState 의존) |
| `src/config.py` | `agentic_core_enabled` 등 모드 전환 설정 |
| `docs/architecture/pipeline-architecture.md` | 전체 아키텍처 문서 |
