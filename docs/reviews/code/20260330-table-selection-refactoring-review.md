# TableSelectionStatus 리팩토링 리뷰

**날짜**: 2026-03-30
**대상**: `rejected_tables`/`sampled_tables` 제거 및 `CandidateTable.selection_status` 도입 리팩토링
**검토 범위**: src/, tests/, resources/, docs/

---

## 발견된 문제

### 1. [CRITICAL] 테스트 파일 — 제거된 필드 직접 참조

**파일**: `tests/auto/e2e/test_agentic_e2e.py` (line 753-756)

```python
def test_07_sampled_tables_tracking(self):
    """샘플 테이블 추적."""
    state = _state(sampled_tables=["TB_A"])
    assert "TB_A" in state.reason.sampled_tables
```

`ReasoningState`에서 `sampled_tables` 필드가 제거되었으므로, `_state(sampled_tables=["TB_A"])`는 `ReasoningState(**reason_kw)` 호출 시 Pydantic `ValidationError`로 실패한다. 이 테스트는 삭제하거나 `candidate_tables` + `selection_status` 기반으로 재작성해야 한다.

**수정안**:
```python
def test_07_candidate_table_selection_status(self):
    """후보 테이블 선택 상태 추적."""
    ct = CandidateTable(
        table_name="TB_A",
        selection_status=TableSelectionStatus.SELECTED,
    )
    state = _state(candidate_tables=[ct])
    selected = [
        t for t in state.reason.candidate_tables
        if t.selection_status == TableSelectionStatus.SELECTED
    ]
    assert any(t.table_name == "TB_A" for t in selected)
```

---

### 2. [CRITICAL] sql_generator.py — REJECTED 테이블이 프롬프트에 포함됨

**파일**: `src/agents/nodes/reason/sql_generator.py` (line 219-222, 225-229)

```python
tables_text = "\n".join(
    _format_table_for_sql_prompt(ct)
    for ct in reason.candidate_tables          # <-- 필터 없음
) if reason.candidate_tables else "(후보 테이블 없음)"

join_entries = [
    f"{ct.qualified_name}: join_keys={ct.join_keys}"
    for ct in reason.candidate_tables          # <-- 필터 없음
    if ct.join_keys
]
```

`candidate_tables`에 `REJECTED` 상태의 테이블이 포함되므로, LLM SQL 생성 프롬프트에 "부적합 판정된 테이블"이 그대로 들어간다. LLM이 거부된 테이블을 사용한 SQL을 생성할 위험이 있다.

**수정안**: `selection_status != REJECTED` 필터 추가:
```python
active_tables = [
    ct for ct in reason.candidate_tables
    if ct.selection_status != TableSelectionStatus.REJECTED
]
```

---

### 3. [CRITICAL] sql_validator.py — REJECTED 테이블이 허용 목록에 포함됨

**파일**: `src/agents/nodes/reason/sql_validator.py` (line 180-181)

```python
candidate_names = {ct.table_name.upper() for ct in reason.candidate_tables}
qualified_names = [ct.qualified_name for ct in reason.candidate_tables]
```

검증 로직이 "SQL에서 사용된 테이블이 candidate_tables에 있는가"를 확인하는데, REJECTED 테이블도 허용 목록에 포함된다. 즉 LLM이 거부된 테이블로 SQL을 생성해도 검증을 통과한다.

**수정안**: sql_generator와 동일하게 REJECTED 필터링.

---

### 4. [WARNING] sql_generator.py — 크로스 DB 감지에서 REJECTED 미필터링

**파일**: `src/agents/nodes/reason/sql_generator.py` (line 138-153)

REJECTED 테이블의 `db_source`도 크로스 DB 판단에 포함되므로, 실제 사용 테이블은 단일 DB인데 REJECTED 테이블이 다른 DB 소스를 가지면 잘못된 크로스 DB 경고가 발생할 수 있다.

---

### 5. [WARNING] planner.py — `sampled_tables` 키 이름이 프롬프트 템플릿과 일치

**파일**: `src/agents/nodes/reason/planner.py` (line 477-479)

```python
"sampled_tables": ", ".join(
    t.table_name for t in (candidate_tables or []) if t.sample_rows
) or "(없음)",
```

코드 자체는 정상 동작한다 (`sample_rows`가 있는 테이블만 추출). 하지만 `resources/prompts/reason/planner_system.txt` line 75의 `{sampled_tables}` 플레이스홀더와 대응하므로, 이 키 이름이 제거된 상태 필드와 혼동될 수 있다. REJECTED 상태인데 `sample_rows`가 있는 테이블도 포함된다는 점에서 필터링 추가가 바람직하다.

---

### 6. [INFO] 프롬프트 템플릿 — 잔존 참조 (의도된 사용)

**파일**: `resources/prompts/reason/recovery_planner_system.txt` (line 16, 18)

```
- 샘플 조회한 테이블 -- 재샘플링 금지: {sampled_tables}
- 부적합 테이블 -- 재사용 금지: {rejected_tables}
```

이 플레이스홀더는 `recovery_planner.py`의 `_build_replan_context`에서 `candidate_tables`로부터 파생하여 치환하므로 (line 220-277), 런타임 오류는 아니다. 다만 변수명이 제거된 상태 필드명과 동일하여 코드 이해에 혼란을 줄 수 있다.

---

### 7. [INFO] `TableSelectionStatus`가 `state/__init__.py`에서 re-export 되지 않음

**파일**: `src/agents/state/__init__.py`

`CandidateTable`, `DeadEnd`, `Phase` 등은 re-export되지만 `TableSelectionStatus`는 누락되어 있다. 현재 사용처(`context_explorer.py`, `recovery_planner.py`)는 `src.models.enums`에서 직접 import하므로 런타임 오류는 아니지만, `state/__init__.py`의 docstring이 "주요 클래스/타입을 패키지 레벨에서 직접 import할 수 있도록 re-export한다"고 명시하고 있으므로 일관성을 위해 추가가 바람직하다.

---

### 8. [INFO] docs/ 내 잔존 참조

아래 문서들이 제거된 필드를 참조한다. 실행 코드가 아니므로 런타임 영향은 없지만, 문서 최신화가 필요하다.

| 파일 | 내용 |
|------|------|
| `docs/architecture/pipeline-architecture.md` (line 504, 555-556, 650, 763-769) | `sampled_tables`, `rejected_tables` 필드 설명 |
| `docs/guides/customization-targets.md` (line 275) | `rejected_tables` 설명 |
| `docs/architecture/large-model-architecture.md` (line 587) | `rejected_tables` 언급 |
| `docs/unit-test-design.md` (line 251, 259) | `check_rejected_tables` 테스트 명세 |

---

## 요약

| 등급 | 건수 | 핵심 |
|------|------|------|
| CRITICAL | 3 | 테스트 깨짐(1), REJECTED 테이블이 SQL 생성/검증에 필터링 없이 포함(2) |
| WARNING | 2 | 크로스 DB 감지 오판 가능성(1), planner 프롬프트 REJECTED 미필터링(1) |
| INFO | 3 | re-export 누락(1), 프롬프트 변수명 혼동(1), docs 미최신화(1) |

**우선 조치 권장 순서**:
1. `sql_generator.py` + `sql_validator.py`에 REJECTED 필터링 추가 (CRITICAL - 논리 오류)
2. `test_agentic_e2e.py` test_07 수정 (CRITICAL - 테스트 실패)
3. `state/__init__.py`에 `TableSelectionStatus` re-export 추가 (INFO)
