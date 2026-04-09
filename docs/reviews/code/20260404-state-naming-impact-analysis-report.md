# 구현 준비 점검 -- 영향도 분석 Part 1: State 모델 + 네이밍 변경

- 일시: 2026-04-04
- 기준 문서: `docs/working/tool-result-renderer-design.md` SS8, SS10
- 대상: 네이밍 변경, Dead field 제거, 신규 필드 추가

---

## A. CandidateTable -> TableEntry (클래스명 변경)

### A-1. src/ 참조 (총 55개소)

```
[src/agents/state/state.py:10]     주석 — "CandidateTable 보조 모델" 설명
[src/agents/state/state.py:202]    선언 — class CandidateTable(BaseModel)
[src/agents/state/state.py:235]    사용 — from_meta() 반환 타입 CandidateTable | None
[src/agents/state/state.py:236]    주석 — docstring "MongoDB 메타 dict -> CandidateTable"
[src/agents/state/state.py:460]    사용 — candidate_tables: list[CandidateTable]

[src/agents/state/__init__.py:9]   import — CandidateTable (re-export docstring)
[src/agents/state/__init__.py:16]  import — CandidateTable (실제 import문)

[src/agents/nodes/reason/knowledge_interpreter.py:8]    주석 — Phase 4 설명
[src/agents/nodes/reason/knowledge_interpreter.py:10]   주석 — Phase 5 설명
[src/agents/nodes/reason/knowledge_interpreter.py:28]   import — CandidateTable
[src/agents/nodes/reason/knowledge_interpreter.py:136]  주석 — "판정 결과를 CandidateTable에 마킹"
[src/agents/nodes/reason/knowledge_interpreter.py:291]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:308]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:479]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:482]  주석 — "기존 CandidateTable에 인플레이스 병합"
[src/agents/nodes/reason/knowledge_interpreter.py:524]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:564]  타입힌트 — table: CandidateTable
[src/agents/nodes/reason/knowledge_interpreter.py:582]  타입힌트 — table: CandidateTable
[src/agents/nodes/reason/knowledge_interpreter.py:583]  주석 — "CandidateTable을 프롬프트 라인 목록으로"
[src/agents/nodes/reason/knowledge_interpreter.py:623]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:624]  반환타입 — tuple[list[list[CandidateTable]], ...]
[src/agents/nodes/reason/knowledge_interpreter.py:626]  타입힌트 — dict[str, list[CandidateTable]]
[src/agents/nodes/reason/knowledge_interpreter.py:633]  타입힌트 — list[list[CandidateTable]]
[src/agents/nodes/reason/knowledge_interpreter.py:651]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:653]  반환타입 — list[list[CandidateTable]]
[src/agents/nodes/reason/knowledge_interpreter.py:662]  타입힌트 — dict[str, list[CandidateTable]]
[src/agents/nodes/reason/knowledge_interpreter.py:679]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:680]  반환타입 — list[list[CandidateTable]]
[src/agents/nodes/reason/knowledge_interpreter.py:696]  타입힌트 — group: list[CandidateTable]

[src/agents/nodes/reason/knowledge_fetcher.py:22]   import — CandidateTable
[src/agents/nodes/reason/knowledge_fetcher.py:63]   타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:179]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:278]  주석 — "CandidateTable 반영 헬퍼"
[src/agents/nodes/reason/knowledge_fetcher.py:283]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:284]  반환타입 — CandidateTable | None
[src/agents/nodes/reason/knowledge_fetcher.py:295]  타입힌트 — table: CandidateTable
[src/agents/nodes/reason/knowledge_fetcher.py:298]  주석 — "CandidateTable에서 컬럼명으로"
[src/agents/nodes/reason/knowledge_fetcher.py:308]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:310]  주석 — "CandidateTable.sample_rows에 저장"
[src/agents/nodes/reason/knowledge_fetcher.py:323]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:325]  주석 — "CandidateTable.observed_date_columns에 저장"
[src/agents/nodes/reason/knowledge_fetcher.py:348]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:369]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:399]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:434]  사용 — CandidateTable.from_meta(m)
[src/agents/nodes/reason/knowledge_fetcher.py:550]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:552]  주석 — "CandidateTable의 기준 컬럼별"
[src/agents/nodes/reason/knowledge_fetcher.py:599]  타입힌트 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:724]  반환타입 — list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:725]  주석 — "CandidateTable을 추출한다"
[src/agents/nodes/reason/knowledge_fetcher.py:734]  타입힌트 — tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:736]  사용 — CandidateTable.from_meta(meta)

[src/agents/nodes/reason/sql_generator.py:24]   주석 — "CandidateTable -> 프롬프트용 텍스트"
[src/agents/nodes/reason/sql_generator.py:39]   import — CandidateTable
[src/agents/nodes/reason/sql_generator.py:69]   타입힌트 — ct: CandidateTable
[src/agents/nodes/reason/sql_generator.py:70]   주석 — "CandidateTable을 SQL 생성 프롬프트용"
[src/agents/nodes/reason/sql_generator.py:81]   타입힌트 — ct: CandidateTable
[src/agents/nodes/reason/sql_generator.py:107]  타입힌트 — ct: CandidateTable
[src/agents/nodes/reason/sql_generator.py:114]  타입힌트 — ct: CandidateTable

[src/services/insight_builder.py:218]   주석 — "CandidateTable 또는 dict에서 테이블명을 추출"
```

### A-2. tests/ 참조 (총 41개소)

```
[tests/auto/e2e/test_agentic_core.py:31]      import — CandidateTable
[tests/auto/e2e/test_agentic_core.py:416]     import — CandidateTable
[tests/auto/e2e/test_agentic_core.py:428]     사용 — CandidateTable(...)

[tests/auto/e2e/test_agentic_e2e.py:27]       import — CandidateTable
[tests/auto/e2e/test_agentic_e2e.py:141]      반환타입 — -> CandidateTable
[tests/auto/e2e/test_agentic_e2e.py:142]      사용 — return CandidateTable(...)
[tests/auto/e2e/test_agentic_e2e.py:353-354]  사용 — CandidateTable(table_name="A")
[tests/auto/e2e/test_agentic_e2e.py:755]      주석 — "CandidateTable.sample_rows"
[tests/auto/e2e/test_agentic_e2e.py:756]      사용 — CandidateTable(table_name="TB_A", ...)

[tests/auto/e2e/test_agentic_flow_trace.py:23]    import — CandidateTable
[tests/auto/e2e/test_agentic_flow_trace.py:460]   사용 — CandidateTable(...)
[tests/auto/e2e/test_agentic_flow_trace.py:501]   사용 — CandidateTable(...)
[tests/auto/e2e/test_agentic_flow_trace.py:654]   사용 — CandidateTable(...)

[tests/auto/unit/test_recovery_agent.py:256]   import — CandidateTable (지역 import)
[tests/auto/unit/test_recovery_agent.py:265]   사용 — CandidateTable(...)

[tests/auto/unit/test_three_aspect_enrichment.py:19]   import — CandidateTable
[tests/auto/unit/test_three_aspect_enrichment.py:186-480]  사용 — CandidateTable(...) x24개소
[tests/auto/unit/test_three_aspect_enrichment.py:455]  주석 — "CandidateTable.qualified_name 테스트"
```

### A-3. resources/ 참조

```
(없음 — 프롬프트 파일에 CandidateTable 클래스명 직접 사용 없음)
```

---

## B. candidate_tables -> explored_tables (필드명/변수명/문자열 키)

### B-1. src/ 참조 (총 63개소)

```
[src/agents/state/state.py:460]     선언 — candidate_tables: list[CandidateTable] = Field(...)

[src/agents/nodes/reason/knowledge_interpreter.py:5]    주석 — "candidate_tables/code_map을 갱신"
[src/agents/nodes/reason/knowledge_interpreter.py:98]   사용 — candidate_tables = list(reason.candidate_tables)
[src/agents/nodes/reason/knowledge_interpreter.py:107]  사용 — candidate_tables (지역변수 전달)
[src/agents/nodes/reason/knowledge_interpreter.py:118]  사용 — _merge_llm_inferred_fields(candidate_tables, ...)
[src/agents/nodes/reason/knowledge_interpreter.py:145]  사용 — for ct in candidate_tables
[src/agents/nodes/reason/knowledge_interpreter.py:182]  사용 — _promote_sampled_confidence(candidate_tables, ...)
[src/agents/nodes/reason/knowledge_interpreter.py:185]  사용 — reason.candidate_tables = candidate_tables
[src/agents/nodes/reason/knowledge_interpreter.py:249]  주석 — "candidate_tables는 별도 처리"
[src/agents/nodes/reason/knowledge_interpreter.py:291]  파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:294]  사용 — if not candidate_tables
[src/agents/nodes/reason/knowledge_interpreter.py:297]  사용 — for table in candidate_tables
[src/agents/nodes/reason/knowledge_interpreter.py:308]  파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:327]  사용 — _serialize_table_observations(candidate_tables)
[src/agents/nodes/reason/knowledge_interpreter.py:479]  파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:488]  사용 — for ct in candidate_tables
[src/agents/nodes/reason/knowledge_interpreter.py:524]  파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:529]  사용 — ct.table_name for ct in candidate_tables
[src/agents/nodes/reason/knowledge_interpreter.py:623]  파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:627]  사용 — for t in candidate_tables
[src/agents/nodes/reason/knowledge_interpreter.py:651]  파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:656]  사용 — for t in candidate_tables
[src/agents/nodes/reason/knowledge_interpreter.py:679]  파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_interpreter.py:686]  사용 — if len(candidate_tables) < 2
[src/agents/nodes/reason/knowledge_interpreter.py:689]  사용 — _group_by_keyword(candidate_tables)
[src/agents/nodes/reason/knowledge_interpreter.py:691]  사용 — _group_by_prefix(candidate_tables, grouped)

[src/agents/nodes/reason/knowledge_fetcher.py:63]    파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:80]    사용 — for t in candidate_tables
[src/agents/nodes/reason/knowledge_fetcher.py:92]    파라미터 — candidate_tables: list
[src/agents/nodes/reason/knowledge_fetcher.py:119]   사용 — candidate_tables.extend(new_tables)
[src/agents/nodes/reason/knowledge_fetcher.py:124]   사용 — searched_queries, candidate_tables
[src/agents/nodes/reason/knowledge_fetcher.py:179]   파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:194]   사용 — result, searched_queries, candidate_tables, code_map
[src/agents/nodes/reason/knowledge_fetcher.py:203-209]  사용 — _store_xxx(..., candidate_tables) x4
[src/agents/nodes/reason/knowledge_fetcher.py:283-376]  파라미터/사용 — candidate_tables x12
[src/agents/nodes/reason/knowledge_fetcher.py:399]   파라미터 — candidate_tables: list[CandidateTable]
[src/agents/nodes/reason/knowledge_fetcher.py:406]   주석 — "candidate_tables와 code_map에 직접 반영"
[src/agents/nodes/reason/knowledge_fetcher.py:436]   사용 — candidate_tables.append(ct)
[src/agents/nodes/reason/knowledge_fetcher.py:493]   사용 — candidate_tables = list(reason.candidate_tables)
[src/agents/nodes/reason/knowledge_fetcher.py:506]   사용 — _should_skip_step(step, ..., candidate_tables)
[src/agents/nodes/reason/knowledge_fetcher.py:511]   사용 — candidate_tables, explored_use_cases
[src/agents/nodes/reason/knowledge_fetcher.py:527-528]  사용 — await _xxx(candidate_tables) x2
[src/agents/nodes/reason/knowledge_fetcher.py:530]   사용 — reason.candidate_tables = candidate_tables
[src/agents/nodes/reason/knowledge_fetcher.py:550-605]  파라미터/사용 — candidate_tables x6

[src/agents/nodes/reason/sql_generator.py:3]     주석 — "candidate_tables를 기반으로"
[src/agents/nodes/reason/sql_generator.py:8]     주석 — "candidate_tables의 db_source"
[src/agents/nodes/reason/sql_generator.py:153]   사용 — for ct in reason.candidate_tables
[src/agents/nodes/reason/sql_generator.py:165]   사용 — for ct in reason.candidate_tables
[src/agents/nodes/reason/sql_generator.py:176]   사용 — for ct in reason.candidate_tables
[src/agents/nodes/reason/sql_generator.py:228]   사용 — ct.qualified_name for ct in reason.candidate_tables
[src/agents/nodes/reason/sql_generator.py:261]   사용 — ct for ct in reason.candidate_tables

[src/agents/nodes/reason/sql_validator.py:231]   주석 — "candidate_tables에 존재하는지"
[src/agents/nodes/reason/sql_validator.py:235]   사용 — for ct in reason.candidate_tables
[src/agents/nodes/reason/sql_validator.py:250]   주석 — "candidate_tables의 컬럼 범위"
[src/agents/nodes/reason/sql_validator.py:336]   사용 — for ct in reason.candidate_tables

[src/agents/nodes/reason/recovery_agent.py:358]  사용 — for ct in reason.candidate_tables
[src/agents/nodes/reason/recovery_agent.py:392]  문자열키 — "{candidate_tables_summary}"
[src/agents/nodes/reason/recovery_agent.py:479]  사용 — for ct in reason.candidate_tables

[src/agents/nodes/reason/readiness_gate.py:91]   문자열키 — stats['candidate_tables'] (로그용)
[src/agents/nodes/reason/readiness_gate.py:154]  사용 — len(reason.candidate_tables)
[src/agents/nodes/reason/readiness_gate.py:201]  문자열키 — "candidate_tables": len(reason.candidate_tables)

[src/agents/nodes/reason/result_finalizer.py:9]   주석 — "candidate_tables와 교차하여"
[src/agents/nodes/reason/result_finalizer.py:120]  사용 — for ct in reason.candidate_tables

[src/services/insight_builder.py:115]  문자열키 — "candidate_tables" (dict/attr 양방향)
[src/services/insight_builder.py:142]  문자열키 — "candidate_tables"
[src/services/insight_builder.py:167]  문자열키 — "candidate_tables"
[src/services/insight_builder.py:190]  문자열키 — "candidate_tables"
[src/services/insight_builder.py:264]  주석 — "candidate_tables에서 sample_rows"
[src/services/insight_builder.py:265]  문자열키 — "candidate_tables"
[src/services/insight_builder.py:403]  문자열키 — "candidate_tables"

[src/connectors/manager.py:171]  주석 — "candidate_tables로 라우팅"
[src/connectors/manager.py:188]  사용 — for ct in reason.candidate_tables

[src/utils/tracker/callback_handler.py:839-840]  문자열키 — "candidate_tables_count", getattr(..., "candidate_tables", [])
[src/utils/tracker/visualizer.py:235]  문자열키 — detail.get("candidate_tables", [])
[src/utils/tracker/visualizer.py:352]  문자열키 — detail.get("candidate_tables", [])
```

### B-2. resources/ 참조

```
[resources/prompts/reason/recovery_agent_system.txt:22]  플레이스홀더 — {candidate_tables_summary}
```

**주의**: `{candidate_tables_summary}`는 프롬프트 플레이스홀더로, Python 코드의 `recovery_agent.py:392`에서 치환된다. 이 플레이스홀더를 `{explored_tables_summary}`로 변경할지는 판단 필요. 프롬프트 내부 명칭이므로 코드와 일관성을 맞추려면 변경 권장.

### B-3. tests/ 참조 (총 26개소)

```
[tests/auto/e2e/test_agentic_core.py:427]      사용 — candidate_tables=[...]
[tests/auto/e2e/test_agentic_core.py:447]      사용 — state.reason.candidate_tables
[tests/auto/e2e/test_agentic_core.py:449]      사용 — state.reason.candidate_tables[0].table_name

[tests/auto/e2e/test_agentic_e2e.py:177]       사용 — candidate_tables=[_ct(...)]
[tests/auto/e2e/test_agentic_e2e.py:352]       사용 — candidate_tables=[...]
[tests/auto/e2e/test_agentic_e2e.py:385]       사용 — ReasoningState(candidate_tables=[...])
[tests/auto/e2e/test_agentic_e2e.py:391]       사용 — ReasoningState(candidate_tables=[...])
[tests/auto/e2e/test_agentic_e2e.py:757]       사용 — _state(candidate_tables=[ct])
[tests/auto/e2e/test_agentic_e2e.py:758]       사용 — state.reason.candidate_tables

[tests/auto/e2e/test_agentic_flow_trace.py:114-115]  사용 — reason.candidate_tables
[tests/auto/e2e/test_agentic_flow_trace.py:127]      문자열 — "candidate_tables_count"
[tests/auto/e2e/test_agentic_flow_trace.py:160]      사용 — reason.candidate_tables
[tests/auto/e2e/test_agentic_flow_trace.py:242]      사용 — reason.candidate_tables
[tests/auto/e2e/test_agentic_flow_trace.py:459]      사용 — candidate_tables=[...]
[tests/auto/e2e/test_agentic_flow_trace.py:500]      사용 — candidate_tables=[...]
[tests/auto/e2e/test_agentic_flow_trace.py:653]      사용 — reason.candidate_tables = [...]
[tests/auto/e2e/test_agentic_flow_trace.py:674]      사용 — state.reason.candidate_tables

[tests/manual/e2e/test_agentic_real_e2e.py:337]   사용 — reason.candidate_tables
[tests/manual/e2e/test_agentic_real_e2e.py:341]   사용 — reason.candidate_tables
[tests/manual/e2e/test_agentic_real_e2e.py:438]   문자열키 — "candidate_tables"
[tests/manual/e2e/test_agentic_real_e2e.py:439]   사용 — reason.candidate_tables
[tests/manual/e2e/test_agentic_real_e2e.py:522]   사용 — state2.reason.candidate_tables
[tests/manual/e2e/test_agentic_real_e2e.py:528]   사용 — state3.reason.candidate_tables

[tests/test_cases/agentic_e2e_test_catalog.json:163]  문자열 — "candidate_tables=[]" (verify 조건)
[tests/reports/agentic_real_e2e_report.txt:72]        문자열 — "candidate_tables: 0" (리포트 출력)
```

---

## C. Dead field 제거

### C-1. KnowledgeItem.is_inferred

| 위치 | 유형 | 설명 |
|------|------|------|
| `src/agents/state/state.py:85` | 선언 | `is_inferred: bool = False` |

**소스 코드 참조: 1개소 (선언만 존재)**

- resources/prompts에 `is_inferred` 직접 참조 없음
- tests/에 `is_inferred` 직접 참조 없음
- 읽기/쓰기하는 코드가 선언 외에 전무 -- **안전하게 제거 가능**

### C-2. conflicted_bounce_count

| 위치 | 유형 | 설명 |
|------|------|------|
| `src/agents/state/state.py:523` | 선언 | `conflicted_bounce_count: int = 0` |
| `src/agents/nodes/reason/reasoning_preparer.py:56` | 쓰기 | `reason.conflicted_bounce_count = 0` (초기화) |

**소스 코드 참조: 2개소**

- 설계 상 `_route_after_recovery_agent`에서 increment + 가드 역할이었으나, 현재 구현에서는 **초기화만 되고 읽히거나 증가되지 않음**
- resources/prompts에 참조 없음, tests/에 참조 없음
- **안전하게 제거 가능** (reasoning_preparer.py:56 라인도 함께 삭제)

### C-3. last_verdict

| 위치 | 유형 | 설명 |
|------|------|------|
| `src/agents/state/state.py:518` | 선언 | `last_verdict: str \| None = None` |
| `src/agents/nodes/reason/readiness_gate.py:72` | 쓰기 | `reason.last_verdict = verdict.value` |
| `src/agents/nodes/reason/reasoning_preparer.py:53` | 쓰기 | `reason.last_verdict = None` (초기화) |

**소스 코드 참조: 3개소**

- **쓰기만 존재하고 읽는 코드가 없음** -- dead write 패턴
- 설계 상 라우팅 함수에서 `reason.last_verdict`를 참조하도록 계획되었으나, 현재 라우팅은 readiness_gate 반환값을 직접 사용
- resources/prompts에 참조 없음, tests/에 참조 없음
- **안전하게 제거 가능** (readiness_gate.py:72, reasoning_preparer.py:53도 함께 삭제)

---

## D. 신규 필드 추가 충돌 분석

### D-1. ExecutionStep.raw_result: dict[str, Any] | list | None = None

현재 `ExecutionStep` 정의 (state.py:111-121):
```python
class ExecutionStep(BaseModel):
    step: int
    tool: str
    input: str
    purpose: str
    expected_output: str = ""
    status: StepStatus = StepStatus.PENDING
    insight: str | None = None
```

- `raw_result`라는 기존 필드 없음 -- **충돌 없음**
- 기본값 `None`이므로 기존 인스턴스 생성 코드에 영향 없음
- `dict[str, Any]`를 사용하므로 `from typing import Any` import 필요 (state.py 상단에 이미 존재하는지 확인 필요)

### D-2. ObservedDateColumn.recent_values: list[str] = Field(default_factory=list)

현재 `ObservedDateColumn` 정의 (state.py:133-138):
```python
class ObservedDateColumn(BaseModel):
    column_name: str
    date_range: str = ""
    date_pattern: str = ""
```

- `recent_values`라는 기존 필드 없음 -- **충돌 없음**
- `Field(default_factory=list)` 기본값이므로 기존 인스턴스 생성 코드에 영향 없음

---

## E. 설계문서 SS10 대비 누락/불필요 파일 검증

### SS10에 명시된 파일 vs 실제 영향

| SS10 파일 | 변경 내용 (SS10) | 실제 검증 결과 |
|-----------|-----------------|---------------|
| `src/agents/state/state.py` | CandidateTable->TableEntry, candidate_tables->explored_tables, + 신규/제거 | **[OK]** 핵심 파일 |
| `src/agents/state/__init__.py` | re-export 변경 | **[OK]** L9, L16 |
| `src/models/enums.py` | SelectionStatus 통합 (완료) | **[OK]** 이미 완료 |
| `src/agents/nodes/reason/knowledge_fetcher.py` | 네이밍 + raw_result 등 | **[OK]** 대량 참조 확인 |
| `src/agents/nodes/reason/knowledge_interpreter.py` | 네이밍 + 렌더러 등 | **[OK]** 최다 참조 파일 |
| `src/agents/nodes/reason/sql_generator.py` | CandidateTable->TableEntry, candidate_tables->explored_tables | **[OK]** |
| `src/agents/nodes/reason/sql_validator.py` | 동일 네이밍 변경 | **[OK]** L231, 235, 250, 336 |
| `src/agents/nodes/reason/recovery_agent.py` | 동일 네이밍 변경 | **[OK]** L358, 392, 479 |
| `src/agents/nodes/reason/readiness_gate.py` | candidate_tables->explored_tables, last_verdict 제거 | **[OK]** L72, 91, 154, 201 |
| `src/agents/nodes/reason/result_finalizer.py` | 동일 네이밍 변경 | **[OK]** L9, 120 |
| `src/services/insight_builder.py` | CandidateTable->TableEntry, candidate_tables->explored_tables | **[OK]** L115, 142, 167, 190, 218, 264, 265, 403 |
| `src/connectors/manager.py` | candidate_tables->explored_tables | **[OK]** L171, 188 |
| `src/utils/tracker/callback_handler.py` | "candidate_tables" 문자열 키 변경 | **[OK]** L839-840 |
| `src/utils/tracker/visualizer.py` | "candidate_tables" 문자열 키 변경 | **[OK]** L235, 352 |
| `resources/prompts/reason/knowledge_interpreter_system.txt` | 프롬프트 갱신 | **[OK]** Phase 2-3에서 처리 |
| `resources/prompts/reason/recovery_agent_system.txt` | get_date_distribution 계획 지시 | **[OK]** + `{candidate_tables_summary}` 플레이스홀더 변경 검토 필요 |
| `src/agents/nodes/reason/reasoning_preparer.py` | dead field 초기화 코드 제거 | **[OK]** L53, 56 |
| `resources/prompts/reason/reasoning_preparer_system.txt` | 프롬프트 갱신 | **[OK]** |
| `src/services/confidence_scorer.py` | 구현 시 확인 | **[OK-불필요]** grep 결과 candidate_tables/CandidateTable 직접 참조 없음 |
| 테스트 파일 6개+ | 네이밍 변경 반영 | **[OK]** 아래 상세 |

### [MISSING] SS10에 누락된 파일

| 파일 | 누락 사유 | 변경 필요 내용 |
|------|----------|---------------|
| **[MISSING]** `tests/auto/e2e/test_agentic_core.py` | CandidateTable import + candidate_tables 사용 | import 변경, 인스턴스 생성자 변경, 속성 접근 변경 |
| **[MISSING]** `tests/auto/e2e/test_agentic_e2e.py` | CandidateTable import + candidate_tables 사용 (최다) | 동일 |
| **[MISSING]** `tests/auto/e2e/test_agentic_flow_trace.py` | CandidateTable import + candidate_tables 사용 | 동일 + "candidate_tables_count" 문자열 변경 |
| **[MISSING]** `tests/auto/unit/test_recovery_agent.py` | CandidateTable 지역 import + 사용 | 동일 |
| **[MISSING]** `tests/auto/unit/test_three_aspect_enrichment.py` | CandidateTable import + 24개소 사용 (최다 테스트) | 동일 |
| **[MISSING]** `tests/manual/e2e/test_agentic_real_e2e.py` | candidate_tables 사용 6개소 | 속성 접근 변경 |
| **[MISSING]** `tests/test_cases/agentic_e2e_test_catalog.json` | "candidate_tables=[]" 문자열 | 문자열 변경 (verify 조건) |
| **[MISSING]** `tests/reports/agentic_real_e2e_report.txt` | "candidate_tables: 0" 출력 | 리포트 텍스트 (자동 생성이면 무시 가능) |

> SS10에 "테스트 파일 6개+"로 포괄 명시되어 있으므로 완전 누락은 아니지만, 구체적 파일명이 없어 구현 시 빠뜨릴 위험이 있다.

### [UNNECESSARY] SS10에 있지만 변경 불필요한 파일

| 파일 | 사유 |
|------|------|
| **[UNNECESSARY]** `src/services/confidence_scorer.py` | grep 결과 `CandidateTable`/`candidate_tables` 직접 참조 없음. SS10에도 "직접 수정 불필요할 수 있음"으로 표기됨 |

---

## F. 추가 발견 사항

### F-1. recovery_agent_system.txt 프롬프트 플레이스홀더 (Info)

`{candidate_tables_summary}`가 프롬프트 플레이스홀더로 사용 중(L22). 이 플레이스홀더는 Python 코드 `recovery_agent.py:392`에서 문자열 치환되므로, **양쪽을 동시에 변경**해야 한다. SS10에서 recovery_agent_system.txt의 변경 사유는 "get_date_distribution 계획 포함 지시 추가"로만 기술되어 있고, 플레이스홀더 네이밍 변경은 명시되어 있지 않다.

### F-2. insight_builder.py의 문자열 키 접근 패턴 (Warning)

`insight_builder.py`는 `_get_attr_or_key(reason, "candidate_tables", [])`로 문자열 키 기반 접근을 6회 사용한다. 필드명이 `explored_tables`로 변경되면 이 문자열 6개소를 모두 수동 변경해야 하며, 런타임까지 오류가 노출되지 않는다. 타입 체크로 잡히지 않는 위험 지점이다.

### F-3. tracker 문자열 키 호환성 (Info)

`callback_handler.py:839`의 `getattr(val, "candidate_tables", [])`, `visualizer.py:235,352`의 `detail.get("candidate_tables", [])` -- 이들은 직렬화된 딕셔너리 키에도 의존한다. 기존에 저장된 트래커 로그/리포트와의 하위호환이 깨질 수 있으나, 이는 개발 도구 수준이므로 허용 가능.

### F-4. knowledge_fetcher.py:92 타입힌트 누락 (Info)

`candidate_tables: list` (L92)에서 제네릭 타입이 누락되어 있다. 변경 시 `explored_tables: list[TableEntry]`로 정확히 수정 권장.

---

## G. 변경 규모 요약

| 항목 | src/ | resources/ | tests/ | 합계 |
|------|------|-----------|--------|------|
| CandidateTable (클래스명) | ~55 | 0 | ~41 | ~96 |
| candidate_tables (필드/변수) | ~63 | 1 | ~26 | ~90 |
| is_inferred (dead field) | 1 | 0 | 0 | 1 |
| conflicted_bounce_count (dead) | 2 | 0 | 0 | 2 |
| last_verdict (dead field) | 3 | 0 | 0 | 3 |
| ExecutionStep.raw_result (신규) | 1 | 0 | 0 | 1 |
| ObservedDateColumn.recent_values (신규) | 1 | 0 | 0 | 1 |
| **총계** | **~126** | **1** | **~67** | **~194** |

Phase 1 (네이밍 + dead field 제거)의 총 영향 범위는 약 **194개소, 22개 파일** (src 13 + tests 7 + resources 1 + test_catalog.json 1)이다.
