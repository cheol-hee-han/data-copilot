# Recovery Agent 컨텍스트 품질 개선 — 최종 설계서

> 작성일: 2026-04-08
> 관련 trace: `trace_telemetry_20260408_anonymous_session-1775579942931_a8a36f0e696b.json`
> 상태: 설계 완료 (4라운드 서브에이전트 협의 완료)
> 설계자: pipeline-designer + prompt-engineer (4라운드 교차 검토)

## 1. 문제 요약

recovery_agent LLM이 수신하는 프롬프트의 맥락 정보가 빈약하여,
적절한 재탐색 계획을 수립하지 못하고 동일 패턴의 루프에 빠진다.

**핵심 발견: trace 렌더링 문제가 아니라 실제 LLM 프롬프트가 빈약한 것임.**
telemetry의 `prompt_summary` 확인 결과 LLM이 받는 system 프롬프트 원문이
trace report에 표시된 것과 동일한 수준의 정보만 포함하고 있음.

---

## 2. 7가지 문제와 설계 결정

### 2.1 이전 노드 인사이트 정보 부재

**현상**: `reason.discovered_facts`에 누적된 핵심 발견사항이 프롬프트에 전달되지 않음.

**코드 위치**:
- `recovery_agent.py:557-587` — `_build_tool_execution_history()`: step별 insight만 나열
- `recovery_agent.py:681` — `{tool_execution_history}` placeholder 치환
- 프롬프트 템플릿에 누적 인사이트 플레이스홀더 없음

**설계 결정**:
- `ReasoningState`에 `recovery_fact_start_index: int = 0` 필드 추가
- recovery_agent 진입 시 `len(discovered_facts)`를 기록
- 렌더링 시 `discovered_facts[start_index:]`로 현재 라운드 facts만 슬라이스
- 프롬프트 템플릿에 `{current_round_facts}` 플레이스홀더 추가

```python
# state.py — ReasoningState에 추가
recovery_fact_start_index: int = 0

# recovery_agent.py — recovery_agent_node 진입 시
reason.recovery_fact_start_index = len(reason.discovered_facts)

# recovery_agent.py — 신규 서브함수
def _build_current_round_facts(reason: ReasoningState) -> str:
    facts = reason.discovered_facts[reason.recovery_fact_start_index:]
    if not facts:
        return "(이번 라운드 탐색 결과 없음)"
    return "\n".join(f"- {f}" for f in facts[-10:])
```

**불채택 대안**: `accumulated_insights: list[str]` 별도 필드 → discovered_facts와 이중 관리 부담

---

### 2.2 Knowledge Item에 evidence 없음

**현상**: CONFIRMED/PROBABLE 항목에 `ki.evidence` 미렌더링, UNRESOLVED는 상태만 표시.

**코드 위치**:
- `recovery_agent.py:636-651` — KI 렌더링
- `state.py:77` — `evidence: list[str]` 필드 존재, `promote()` 메서드가 append

**설계 결정**:
- CONFIRMED/PROBABLE: `evidence[-1][:120]` (최신 근거 1건) 추가
- CONFLICTED: 별도 그룹으로 분리하여 최우선 표시
- UNRESOLVED: 기존 1줄 유지 (시도 정보는 tool_execution_history에 이미 존재)
- `knowledge_id` 빈 문자열 fallback: `_build_prompt`에서 인덱스 기반 채번

렌더링 포맷 원칙: **들여쓰기 key-value** 사용. 한 줄에 다중 구분자(`—`, `()`, `,`) 혼용 금지.
오픈소스 모델(Solar Pro 2, Qwen)에서 괄호 중첩 + 콤마 구분은 파싱 불안정.

렌더링 예시 (CONFIRMED):
```
[K1] measure:평균 여신 잔액
  상태: PROBABLE
  값: AVG(LN_BAL_AMT)
  출처: 활용사례
  근거: TB_ADW_LNB301M에서 잔액(LN_BAL_AMT) 집계 패턴 확인
```

렌더링 예시 (CONFLICTED):
```
[K5] join:고객번호
  상태: CONFLICTED
  충돌: uc_001에서 EDPS_CSN 사용 vs uc_003에서 CSN 사용
```

렌더링 예시 (UNRESOLVED):
```
[K2] filter:성별=['남성']
  상태: CANDIDATE
  추정값: GNDR_DCD
```

```python
# 확인된 지식 렌더링 (CONFIRMED/PROBABLE)
def _build_confirmed_knowledge(reason: ReasoningState) -> str:
    lines = []
    for idx, ki in enumerate(reason.knowledge_items, 1):
        if ki.status not in (ConfidenceStatus.CONFIRMED, ConfidenceStatus.PROBABLE):
            continue
        kid = ki.knowledge_id or f"K{idx}"
        lines.append(f"[{kid}] {ki.key}")
        lines.append(f"  상태: {ki.status.value}")
        if ki.value:
            lines.append(f"  값: {ki.value}")
        if ki.source:
            lines.append(f"  출처: {ki.source}")
        if ki.evidence:
            lines.append(f"  근거: {ki.evidence[-1][:120]}")
        lines.append("")  # 항목 간 빈 줄
    return "\n".join(lines).strip() or "(없음)"

# 미해소 항목 렌더링 (CONFLICTED 분리)
def _build_unresolved_knowledge(reason: ReasoningState) -> str:
    conflicted, unresolved = [], []
    for idx, ki in enumerate(reason.knowledge_items, 1):
        kid = ki.knowledge_id or f"K{idx}"
        if ki.status == ConfidenceStatus.CONFLICTED:
            block = [f"[{kid}] {ki.key}", "  상태: CONFLICTED"]
            if ki.evidence:
                block.append(f"  충돌: {'; '.join(ki.evidence[-2:])}")
            conflicted.append("\n".join(block))
        elif ki.status in (ConfidenceStatus.UNRESOLVED, ConfidenceStatus.CANDIDATE):
            block = [f"[{kid}] {ki.key}", f"  상태: {ki.status.value}"]
            if ki.value:
                block.append(f"  추정값: {ki.value}")
            unresolved.append("\n".join(block))
    parts = []
    if conflicted:
        parts.append("[모순 항목 — 최우선 해소 필요]")
        parts.extend(conflicted)
    if unresolved:
        if conflicted:
            parts.append("\n[미확인 항목]")
        parts.extend(unresolved)
    return "\n".join(parts) or "(없음)"
```

**불채택 대안**: UNRESOLVED 3줄 블록 (필요이유/시도/남은가능성) → tool_execution_history와 중복, `_infer_needed_reason()` 구현 복잡도 과대

---

### 2.3 실패 사유가 범용적이고 구체성 부족

**현상**: TERM_UNRESOLVABLE의 failure_reason이 "SQL 생성에 필요한 정보가 부족합니다" + 통계 카운트만.

**코드 위치**:
- `readiness_gate.py:277-297` — `_set_failure_context()` TERM_UNRESOLVABLE 분기

**설계 결정**:
- `_set_failure_context()` → `_collect_failure_diagnostics()` + `_format_{type}_reason()` 3개 함수로 분리
- 진단 정보에 미해소 KI 상세(키+상태+추정값), REJECTED 테이블 사유 top 3, CONFLICTED 항목 추가
- 진단은 readiness_gate에서 생성 (추적 이벤트와 동기화 유지)

```python
def _set_failure_context(reason: ReasoningState, score: float) -> None:
    # failure_type 결정 (기존 로직 유지)
    ...
    diagnostics = _collect_failure_diagnostics(reason, score)
    reason.failure_reason = _format_failure_reason(reason.failure_type, diagnostics)

def _collect_failure_diagnostics(reason: ReasoningState, score: float) -> dict:
    unresolved = [ki for ki in reason.knowledge_items
                  if ki.status in (ConfidenceStatus.UNRESOLVED, ConfidenceStatus.CONFLICTED)]
    rejected = [t for t in reason.explored_tables
                if t.selection_status == SelectionStatus.REJECTED]
    return {
        "score": score,
        "ki_total": len(reason.knowledge_items),
        "ki_confirmed": len([i for i in reason.knowledge_items if i.confidence >= 0.8]),
        "selected_count": len([t for t in reason.explored_tables
                               if t.selection_status == SelectionStatus.SELECTED]),
        "explored_count": len(reason.explored_tables),
        "unresolved_details": [
            f"{ki.key} ({ki.status.value})" + (f" — 추정: {ki.value}" if ki.value else "")
            for ki in unresolved[:5]
        ],
        "rejected_details": [
            f"{t.table_name}: {t.selection_reason[:80]}"
            for t in rejected[:3] if t.selection_reason
        ],
        "conflicted_details": [
            f"{ki.key}: {'; '.join(ki.evidence[-2:])}"
            for ki in unresolved if ki.status == ConfidenceStatus.CONFLICTED
        ],
    }
```

---

### 2.4 테이블에 한글명·선택 사유 없음, 컬럼 잘림

**현상**: `c.alt_name` (한글) 미사용, `ct.selection_reason` 미사용, `columns[:10]` 하드코딩.

**코드 위치**:
- `recovery_agent.py:654-669` — 테이블 렌더링 블록
- `state.py:237` `alt_name`, `state.py:259` `selection_reason` — 채워져 있으나 미사용

**설계 결정**:
- `_build_explored_tables_summary()` 서브함수로 분리
- `_render_selected_table()`: alt_name 포함, selection_reason 포함, 동적 컬럼 제한
- `_render_rejected_table()`: 1줄 간결 포맷 (컬럼 목록 생략)
- 동적 컬럼 제한: 테이블 수 기반 (1~2개: 10, 3~4개: 7, 5개+: 5)

```python
def _build_explored_tables_summary(reason: ReasoningState) -> str:
    active_tables = [ct for ct in reason.explored_tables
                     if ct.selection_status != SelectionStatus.REJECTED]
    rejected_tables = [ct for ct in reason.explored_tables
                       if ct.selection_status == SelectionStatus.REJECTED]
    col_limit = _dynamic_column_limit(len(active_tables))

    selected_lines = []
    for ct in active_tables:
        name = ct.table_name
        if ct.alt_name:
            name += f" ({ct.alt_name})"
        line = f"- {name} ({ct.selection_status.value})"
        if ct.description:
            line += f": {ct.description[:80]}"
        if ct.selection_reason:
            line += f"\n  선택 사유: {ct.selection_reason[:100]}"
        col_parts = []
        for c in ct.columns[:col_limit]:
            col_parts.append(f"{c.name}({c.alt_name})" if c.alt_name else c.name)
        if col_parts:
            line += f"\n  컬럼: {', '.join(col_parts)}"
            if len(ct.columns) > col_limit:
                line += f" (+{len(ct.columns) - col_limit})"
        selected_lines.append(line)

    rejected_lines = []
    for ct in rejected_tables[:5]:
        name = ct.table_name
        if ct.alt_name:
            name += f" ({ct.alt_name})"
        reason_text = ct.selection_reason[:80] if ct.selection_reason else "사유 없음"
        rejected_lines.append(f"- {name} — 제외: {reason_text}")

    parts = []
    if selected_lines:
        parts.extend(selected_lines)
    if rejected_lines:
        parts.append("\n[제외된 테이블 — 재탐색 불필요]")
        parts.extend(rejected_lines)
    return "\n".join(parts) or "(없음)"

def _dynamic_column_limit(table_count: int) -> int:
    if table_count <= 2:
        return 10
    if table_count <= 4:
        return 7
    return 5
```

---

### 2.5 샘플 데이터 None vs [] 모호성

**현상**: `sample_rows`가 None(미조회)과 [](0건 반환) 모두 동일한 메시지 출력.

**코드 위치**:
- `recovery_agent.py:731-733` — `_build_sample_summary()`
- `state.py:252` — `sample_rows: list[dict] | None = None`

**설계 결정**: 3-state 구분

```python
def _build_sample_summary(reason: ReasoningState) -> str:
    lines = []
    for ct in reason.explored_tables:
        if ct.selection_status == SelectionStatus.REJECTED:
            continue
        rows = ct.sample_rows
        if rows is None:
            lines.append(f"- {ct.table_name}: 미조회 (get_sample_rows로 확인 가능)")
        elif len(rows) == 0:
            lines.append(f"- {ct.table_name}: 0행 (조회 완료, 데이터 없음)")
        else:
            cols = list(rows[0].keys())[:5]
            lines.append(f"- {ct.table_name}: {len(rows)}행 (컬럼: {', '.join(cols)})")
    return "\n".join(lines) or "(없음)"
```

프롬프트 지시 수정:
```
"0행" 테이블은 다시 조회하지 마세요.
"미조회" 테이블은 필요하면 get_sample_rows로 조회할 수 있습니다.
```

---

### 2.6 인사이트 생성 품질 (상류 원인)

**현상**: context_interpreter의 insight가 recovery 후속 활용을 고려하지 않고 작성됨.

**코드 위치**:
- `context_interpreter.py:591-618` — insight 생성·저장
- `resources/prompts/reason/context_interpreter_system.txt` — 프롬프트

**설계 결정**: 프롬프트 지시 추가 (코드 변경 없음)

`context_interpreter_system.txt`의 분석 지침 섹션에 추가:
```
insight 작성 규칙:
- 발견된 핵심 사실 (테이블명, 컬럼명, 코드값, 조인 키 등 구체적 DB 표현) 포함
- SQL 생성에 어떻게 활용할 수 있는지 (조인 조건, WHERE 필터, 집계 방식) 포함
- 이 스텝에서 해소되지 않은 잔여 의문점 포함
※ insight는 후속 recovery_agent가 탐색 계획 수립 시 직접 참조합니다.
```

**기대 효과**: insight 품질 개선이 곧 `{current_round_facts}` 품질 개선으로 직결.

---

### 2.7 REJECTED 테이블 정보 누락

**현상**: REJECTED 테이블이 프롬프트에서 완전 제외 (line 656-657 `continue`).

**코드 위치**: `recovery_agent.py:656-657`

**설계 결정**: 2.4 설계에 통합 — REJECTED 테이블을 별도 소섹션으로 분리, 상위 5건만 사유와 함께 포함.

---

## 3. 프롬프트 템플릿 구조 개선

### 3.1 섹션 순서 재배치 (LLM positional attention 최적화)

오픈소스 모델(Solar Pro 2 70B, Qwen3.5 397B)은 primacy bias + lost-in-the-middle 현상이 있으므로,
"무엇이 문제인가"를 최상위에, "무엇을 이미 아는가"를 중간에, "무엇을 해야 하는가"를 하단에 배치한다.

```
[고정] 역할 + 핵심 제약
[변수] S1: 사용자 질의 {original_query}                          ← 신규
[변수] S2: 진입 경로 {entry_source_description}
[변수] S3: 미해소 항목 {unresolved_items}                        ← 상단 이동, CONFLICTED 분리
[변수] S4: 이전 실패 기록 {dead_ends_summary}                    ← 미해소 항목 바로 아래
[변수] S5: 현재 라운드 탐색 결과 {current_round_facts}            ← 신규
[변수] S6: 확인된 지식 {confirmed_knowledge}                     ← evidence 포함
[변수] S7: 도구 실행 이력 {tool_execution_history}
[변수] S8: 탐색된 테이블 {explored_tables_summary}               ← REJECTED 포함
[변수] S9: 샘플 데이터 현황 {sample_data_summary}                ← 3-state
[고정] 도구 목록 / 페이징 / 우선순위
[고정] 응답 형식 (JSON)
[고정] 지시 (8항목)
[고정] 예시 5개                                                  ← 예시 6 "페이징" 제거
```

### 3.2 신규/변경 플레이스홀더

| 플레이스홀더 | 상태 | 데이터 소스 | 렌더링 함수 |
|-------------|------|-----------|-----------|
| `{original_query}` | 신규 | `PipelineState.original_query` | 직접 치환 |
| `{current_round_facts}` | 신규 | `reason.discovered_facts[start_index:]` | `_build_current_round_facts()` |
| `{confirmed_knowledge}` | 포맷 변경 | `reason.knowledge_items` (CONFIRMED/PROBABLE) | `_build_confirmed_knowledge()` |
| `{unresolved_items}` | 포맷 변경 | `reason.knowledge_items` (UNRESOLVED/CONFLICTED/CANDIDATE) | `_build_unresolved_knowledge()` |
| `{explored_tables_summary}` | 포맷 변경 | `reason.explored_tables` (전체) | `_build_explored_tables_summary()` |
| `{sample_data_summary}` | 포맷 변경 | `reason.explored_tables[*].sample_rows` | `_build_sample_summary()` |
| `{entry_source_description}` | 내용 풍부화 | readiness_gate `_format_failure_reason()` 결과 | `_build_entry_description()` |

### 3.3 Few-shot 예시 변경

- **예시 6 "페이징 활용" 제거**: 도구 페이징 섹션과 중복, ~200 토큰 절약
- **예시 5 "give_up" 유지**: 폐쇄망 모델의 포기 판단 능력이 약하므로 필수
- 최종 5개: 코드값 불명, SQL 0건, 텍스트 필터, 금융 산출식, give_up

---

## 4. `_build_prompt()` 리팩터링 구조

현재 단일 함수(line 594-711)를 9개 서브함수로 분리:

```
_build_prompt(reason, *, original_query, entry_failure_type, entry_failure_reason)
  ├── _build_entry_description()       → {entry_source_description}
  ├── _build_confirmed_knowledge()     → {confirmed_knowledge}
  ├── _build_unresolved_knowledge()    → {unresolved_items}
  ├── _build_tool_execution_history()  → {tool_execution_history}  [기존 유지]
  ├── _build_current_round_facts()     → {current_round_facts}     [신규]
  ├── _build_explored_tables_summary() → {explored_tables_summary} [신규 분리]
  ├── _build_dead_ends_summary()       → {dead_ends_summary}       [인라인→함수]
  ├── _build_sample_summary()          → {sample_data_summary}     [개선]
  └── _apply_token_guard(sections)     → 예산 초과 시 트리밍        [신규]
```

각 서브함수: `(reason: ReasoningState) -> str` 순수 함수, 단위 테스트 용이.

---

## 5. 토큰 예산 관리

### 5.1 예산 설정

```python
_RECOVERY_PROMPT_VAR_CHAR_BUDGET = 10_000  # 변수 섹션 합계 한도 (~5,000 토큰)
```

- 변수 섹션만 관리 (고정 부분 ~8,200자/~4,100 토큰은 안정적)
- 총 프롬프트: 변수 10,000 + 고정 8,200 = 18,200자 (약 9,100 토큰)
- Solar Pro 2 (32K context) 안전 마진 충분

### 5.2 트리밍 우선순위 (정보 밀도 낮은 순)

| 단계 | 대상 | 조치 | 절감 추정 |
|------|------|------|---------|
| 1 | `{sample_data_summary}` | 컬럼명 목록 제거, 행 수만 유지 | ~300자 |
| 2 | `{explored_tables_summary}` | 컬럼 수 강제 5개, REJECTED 제거 | ~2,000자 |
| 3 | `{tool_execution_history}` | insight 80자→40자 truncate | ~500자 |
| 4 | `{current_round_facts}` | 10건→5건→3건 축소 | ~500자 |
| 5 | `{confirmed_knowledge}` | evidence 행 생략 | ~600자 |

핵심 원칙: `{unresolved_items}`, `{dead_ends_summary}`, `{entry_source_description}`은 트리밍하지 않음 — recovery 판단에 가장 직접적으로 기여하는 정보.

### 5.3 토큰 예산 분석

| 섹션 | 정상 예산 | 트리밍 후 | 비고 |
|------|---------|---------|------|
| `{original_query}` | ~60자 | ~60자 | 트리밍 안 함 |
| `{entry_source_description}` | ~500자 | ~500자 | 트리밍 안 함 |
| `{unresolved_items}` | ~500자 | ~500자 | 트리밍 안 함 |
| `{dead_ends_summary}` | ~600자 | ~600자 | 트리밍 안 함 |
| `{current_round_facts}` | ~750자 | ~300자 | 4단계 |
| `{confirmed_knowledge}` | ~1,200자 | ~600자 | 5단계 |
| `{tool_execution_history}` | ~1,600자 | ~1,100자 | 3단계 |
| `{explored_tables_summary}` | ~4,500자 | ~1,500자 | 2단계 |
| `{sample_data_summary}` | ~800자 | ~500자 | 1단계 |
| **변수 합계** | **~10,510자** | **~5,660자** | |

---

## 6. State 변경

### 6.1 ReasoningState 필드 추가 (1개)

```python
# state.py — ReasoningState
recovery_fact_start_index: int = 0
```

- recovery_agent 진입 시 `len(discovered_facts)`를 기록
- 기존 `discovered_facts: list[str]` 그대로 활용 (별도 필드 추가 없음)
- 다른 노드(readiness_gate, sql_generator)에 영향 없음

### 6.2 기존 필드 활용 (state 변경 없이 렌더링만 변경)

| 필드 | 위치 | 현재 상태 | 개선 |
|------|------|---------|------|
| `KI.evidence: list[str]` | state.py:77 | 채워짐, 미렌더링 | 최신 1건 렌더링 |
| `KI.knowledge_id: str` | state.py:80 | 빈 문자열 가능 | fallback 채번 |
| `TableMeta.alt_name: str` | state.py:237 | 채워짐, 미사용 | 테이블명에 포함 |
| `TableMeta.selection_reason: str` | state.py:259 | 채워짐, 미사용 | 사유 렌더링 |
| `TableMeta.sample_rows: list|None` | state.py:252 | None/[] 미구분 | 3-state 구분 |
| `ColumnInfo.alt_name: str` | state.py:148 | 채워짐, 미사용 | 컬럼명에 포함 |

---

## 7. 수정 대상 파일 요약

| 파일 | 수정 내용 | 변경 규모 |
|------|----------|---------|
| `src/agents/state/state.py` | `recovery_fact_start_index` 필드 추가 | 1줄 |
| `src/agents/nodes/reason/recovery_agent.py` | `_build_prompt()` 9개 서브함수 분리 + 토큰 가드 | 대 |
| `src/agents/nodes/reason/readiness_gate.py` | `_set_failure_context()` 서브함수 분리 | 중 |
| `resources/prompts/reason/recovery_agent_system.txt` | 섹션 순서 재배치, 플레이스홀더 추가, 예시 축소 | 중 |
| `resources/prompts/reason/context_interpreter_system.txt` | insight 작성 규칙 추가 | 소 |

### 변경 불필요 파일

| 파일 | 이유 |
|------|------|
| `context_interpreter.py` | 프롬프트만 변경, 코드 변경 없음 |
| `context_retriever.py` | 데이터 수집 로직 변경 없음 |
| `pipeline.py` | 그래프 구조 변경 없음 |

---

## 8. 구현 Phase

### Phase 1: 데이터 모델 + 공급 로직 (의존성 기반)

1. `state.py` — `recovery_fact_start_index: int = 0` 추가
2. `context_interpreter_system.txt` — insight 작성 규칙 추가 (독립, 즉시 효과)
3. `readiness_gate.py` — `_set_failure_context()` 서브함수 분리, CONFLICTED 처리 추가

**Phase 1 검증**: readiness_gate 단위 테스트로 failure_reason 포맷 확인

### Phase 2: 소비 로직 (recovery_agent 리팩터링)

4. `recovery_agent.py` — `_build_prompt()` 9개 서브함수 분리
   - `_build_entry_description()`
   - `_build_confirmed_knowledge()` (evidence 포함)
   - `_build_unresolved_knowledge()` (CONFLICTED 분리)
   - `_build_current_round_facts()` (신규)
   - `_build_explored_tables_summary()` (REJECTED + 한글명 + selection_reason)
   - `_build_sample_summary()` (3-state)
   - `_build_dead_ends_summary()` (기존 인라인 → 함수)
   - `_build_tool_execution_history()` (기존 유지)
   - `_apply_token_guard()` (신규)
5. `recovery_agent_node()` — `original_query` 전달, `recovery_fact_start_index` 기록

**Phase 2 검증**: 서브함수별 단위 테스트 + 프롬프트 출력 비교

### Phase 3: 프롬프트 템플릿

6. `recovery_agent_system.txt` — 섹션 순서 재배치 + 플레이스홀더 추가 + 예시 6 제거

**Phase 3 검증**: recovery 관련 실패 케이스 5건으로 before/after 비교

### Phase 4: 검증 및 조정 (선택)

7. 골든셋 기반 A/B 비교
8. Solar Pro 2에서 positional bias 개선 효과 측정
9. 토큰 가드 임계값 조정

---

## 9. 설계 결정 근거 요약 (4라운드 협의 결과)

| 쟁점 | 최종 결정 | 근거 |
|------|----------|------|
| 섹션 순서 | 실패 진단 상단 배치 | 오픈소스 모델 primacy bias 대응 |
| `{original_query}` | 추가 | LLM이 탐색 목적을 직접 볼 수 없는 결함 해소, 비용 ~30 토큰 |
| UNRESOLVED 렌더링 | CONFLICTED 분리 + 1줄 | "시도" 행은 tool_execution_history와 중복, 역방향 매칭 신뢰도 낮음 |
| dead_ends 위치 | 미해소 항목 바로 아래 | 실패 정보 집중 배치로 인지 부담 감소 |
| discovered_facts | 기존 리스트 + 인덱스 슬라이스 | 별도 필드 추가 시 이중 관리 부담, state 변경 최소화 |
| 컬럼 제한 | 동적 (테이블 수 기반) | 고정 제한보다 토큰 효율 우수 |
| 토큰 예산 | 변수 섹션 10,000자 | 고정 부분 변경에 독립적, 가드 지점 명확 |
| Few-shot 수 | 5개 (예시 6 제거) | give_up 예시 필수 (폐쇄망 모델 포기 판단 약함), 페이징은 도구 설명과 중복 |
| 구현 순서 | 데이터 모델 → 공급 → 소비 → 프롬프트 | bottom-up 의존성 순서, 각 Phase 독립 테스트 가능 |
| evidence 렌더링 | `source + evidence[-1][:120]` | recovery LLM이 정보 출처를 알아야 보완 탐색 계획 가능 |
| `_infer_needed_reason()` | 불채택 | KI prefix 체계 미확립, 변경 범위 과대, 별도 이슈 분리 |
| 인사이트 품질 | 프롬프트 지시만 (코드 변경 없음) | 기존 `_populate_discovered_facts()` 경로 활용 |

---

## 10. 기타 발견 사항 (별도 이슈)

### 10.1 enrichment에서 search_table_meta 대신 lookup_table_meta

- `context_retriever.py:322` — 이미 `lookup_table_meta()` 사용 확인 (코드 검증 완료)
- 실제 원인: `asyncio.gather()` 병렬 실행 시 `seen_tables` 중복 제거 경합
- → 별도 이슈로 분리

### 10.2 knowledge_id 채번 누락

- `reasoning_preparer`에서 "K1", "K2" 채번하는 로직이 recovery 라운드 신규 KI에 적용되지 않음
- → 본 설계의 `_build_confirmed_knowledge()`에서 fallback 채번으로 임시 대응
- → 근본 수정은 별도 이슈

### 10.3 중복 KI 문제와 readiness score 왜곡

- 동일 의미 KI가 다른 키로 중복 생성 (예: `measure:평균 여신 잔액` vs `measure:평균여신잔액`)
- → KI dedup/merge 로직 필요, 별도 이슈
