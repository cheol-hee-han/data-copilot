# Phase 3 — Continue `handoff_note` 하류 소비 전수 + `{previous_sql}` 주입 구현 계획

작성일: 2026-04-20
상태: 설계 확정 · 착수 대기
SSoT 설계 문서: [20260418-continue-orchestrator-4way-redesign.md §14](20260418-continue-orchestrator-4way-redesign.md)

## 목차

- [1. 배경과 전제](#1-배경과-전제) — 본 계획의 기원·범위·SSoT 원칙 (L1-L80)
- [2. 설계 확정 사항 (Critical 수정 완료)](#2-설계-확정-사항-critical-수정-완료) — 2026-04-20 리뷰로 수정된 5 Critical + R2/R3/R5 반영 요약
- [3. 커밋 구조 개요](#3-커밋-구조-개요) — 6 커밋 의존성 그래프와 병렬성
- [4. 커밋별 파일 체인지 리스트](#4-커밋별-파일-체인지-리스트) — P3-#1 ~ P3-#5 전 파일·라인 범위·테스트
- [5. 공통 테스트와 회귀 가드](#5-공통-테스트와-회귀-가드) — R3/R5 테스트, 기존 1767 unit + 143 auto
- [6. 리스크와 롤백 가드](#6-리스크와-롤백-가드) — blast radius 기준 merge 순서·rollback 전략
- [7. 착수 전 사전 검증 체크리스트](#7-착수-전-사전-검증-체크리스트) — 구현 시작 직전 grep·가정 검증
- [8. 진행 체크리스트](#8-진행-체크리스트) — 커밋 단위 완료 추적용
- [9. 참고 레퍼런스](#9-참고-레퍼런스) — parent doc 주요 섹션, 기존 코드 위치, memory 포인터

---

## 1. 배경과 전제

### 1.1 Phase 3 정체성

Phase 1 (4-way route 설계) + Phase 2 (hydration 매트릭스 + rows JIT fetch + process_summary 확장) 에 이어,
**연속 턴 방문 가능 모든 노드의 `handoff_note` 소비 전수 + 직전 턴 참고 SQL (`{previous_sql}`) 직교 채널 주입** 을 설계·구현하는 단계.

기존 소비자 4 개(sql_generator / sql_validator / analyzer / visualizer) 에 추가로 3 개 노드(`query_normalizer` / `context_interpreter` / `recovery_agent`) 도입 + `{previous_sql}` 는 sql_generator / recovery_agent 2 개 노드 소비.

### 1.2 SSoT 원칙

설계 내용은 parent 문서(`docs/todo/20260418-continue-orchestrator-4way-redesign.md` §14) 에만 존재한다. 본 문서는 **구현 실행 계획 · 파일별 변경 범위 · 테스트 플랜** 만 담는다. 설계 재서술 금지. 설계 변경이 필요하면 parent 문서 §14 를 수정하고 본 문서는 파일 목록만 업데이트.

관련 이력 문서(스텁): `20260420-continue-handoff-consumer-design.md` — 설계 이관 완료.

### 1.3 전제된 작업 원칙 (`.claude/rules/`)

- **전체 관점 사고 (holistic-thinking.md)**: 커밋마다 6 관점(디자인·일관성·유지보수성·효율성·기능·성능) 점검 · 최소 2 대안 비교 · 정리 기회 탐색.
- **Consumer opt-in 단일 패턴**: 모든 소비자는 기존 LLM 프롬프트에 `{handoff_note}` / `{previous_sql}` 플레이스홀더 추가로만 소비. rule-based 파싱·별도 LLM 호출 금지.
- **데이터 보안 (data-security.md)**: 프롬프트 주입 방어 · 로그 마스킹 · SELECT 전용 유지.
- **금융 도메인 (financial-domain.md)**: 유사 테이블 존재 · 불완전 메타 · 계수산출식 불확실성 대응.

### 1.4 기술 스택 전제

- Python 3.12, 의존성은 `uv` 로만 설치 (pyproject.toml).
- Pydantic v2 (frozen 필드 대신 unit test 로 hydration-only 검증 — R3).
- 타입 힌트 필수 (`mypy --strict`).
- LLM: AsyncAnthropic (Claude), 폐쇄망 타겟 Qwen3.5 397B.

### 1.5 범위 경계

**포함**:
- 3 개 신규 노드 `{handoff_note}` 소비 도입.
- `{previous_sql}` 직교 채널 신설 (sql_generator / recovery_agent).
- REGENERATE non-local_fix 실패 차단 가드 확장.
- visualizer ANALYZE 재판정 가드 한 단락.

**제외** (본 Phase 범위 아님):
- ConversationHistory 클래스 도입 (`message_store.get_conversation_history` 가 이미 전 턴 복원 — memory `project_conversation_history_restore.md`).
- 현재 턴 실패 SQL 의 recovery_agent 주입 (§14.8 Q4 — 별도 Phase).
- context_interpreter Level 1 개별 스텝 주입 (§14.3.2 Q2).

---

## 2. 설계 확정 사항 (Critical 수정 완료)

2026-04-20 prompt-engineer + code-reviewer 교차 리뷰 결과 5 Critical + 2 Recommended (R2 / R5) + 1 test-only Recommended (R3) 반영 완료. parent 문서 §14 및 관련 섹션에 모두 반영됨.

| # | 항목 | 최종 결정 | parent doc 반영 위치 |
| --- | --- | --- | --- |
| C1 | §4.5 ↔ §14.3.1 모순 해소 | §4.5 를 §14.3.1 redirect 로 교체 + L23/L172/L176/L234/L301/L1789/L1808 Phase 3 각주 | §4.5, §2.5, §3.1, §8.1, §9 Q1 |
| C2 | `normalize_previous_sql_explanation` 중복 제거 | SQL 본문·설명 공용 **단일 `normalize_previous_sql(value)`** 로 통합 | §14.3.6 "유틸 신설" / §14.9.2 체크리스트 |
| C3 | 토큰 수치 클레임 허위 | 사전 수치 제거 → **"구현 후 실측하여 §14.7.2 표에 갱신"** | §14.6 가드레일 / §14.7.2 표 |
| C4 | 헤더 네이밍 불일치 | 기존 선례 완전 통일 — directive: `## 연속 질의 오케스트레이터 지시 (handoff_note)` / hint-only: `## 연속 질의 오케스트레이터 지시 (handoff_note, 참고용)` / previous_sql: `## 직전 턴 참고 SQL (previous_sql)` | §14.3.1 / §14.3.2 / §14.3.3 / §14.6 |
| C5 | `{previous_sql}` 복사금지 지시 위치 | placeholder **바로 위 첫 불릿** 에 "참고용 — 그대로 복사 금지" 선언 | §14.3.6 sql_generator/recovery_agent 프롬프트 변경안 |
| R2 | continue_orchestrator.py:481 주석 갱신 | §14.3.6 체크리스트에 명시 (코드 수정 시 함께) | §14.9.2 체크리스트 |
| R3 | `previous_turn_sql` read-only 강제 | pydantic `frozen=True` 대신 **단위 테스트 1 개** 로 검증 | §14.9.4 |
| R5 | NEW 턴 회귀 방어 체크리스트 | 신규 3 섹션 헤더 존재 + `### ` 서브헤더 NEW 턴 부재 assert 테스트 | §14.9.4 |

**드롭된 항목**: R1 (`{previous_explanation}` 축약 — 네이밍 일관성 저하), R4 (Level 1 정책 명문화 — §14.3.2 에 이미 명시), R6 (빈 섹션 stub — `normalize_handoff_note` 폴백으로 이미 해결).

---

## 3. 커밋 구조 개요

### 3.1 의존성 그래프

```
P3-#1  (query_normalizer directive)                  ─┐
P3-#1' (ReasoningState.previous_turn_sql + util)     ─┼→ P3-#2 (context_interpreter hint-only)
                                                       ├→ P3-#3 (visualizer ANALYZE 가드)
                                                       ├→ P3-#4 (REGENERATE non-local_fix 차단)
                                                       └→ P3-#5 (recovery_agent + sql_generator {previous_sql})
```

- **P3-#1 / P3-#1'** 는 상호 독립 — 병렬 PR 가능.
- **P3-#2 / P3-#3** 은 P3-#1 이후 권장 (REFINE 파이프라인 통합 검증 목적) 이지만 상호 독립.
- **P3-#4** 는 P3-#1' 후 (state 필드 의존).
- **P3-#5** 는 마지막 — P3-#1' 의 `{previous_sql}` 기반공사와 `sql_generator` 치환을 묶어 완결.

### 3.2 PR 분할 권장안

| PR | 포함 커밋 | blast radius | 리뷰 난이도 |
| --- | --- | --- | --- |
| PR-1 | P3-#1' | **큼 (state + hydration)** | 중 |
| PR-2 | P3-#1 | 중 | 낮 |
| PR-3 | P3-#2 + P3-#3 | 작 | 낮 |
| PR-4 | P3-#4 | 작 (pipeline 분기 1 개) | 중 |
| PR-5 | P3-#5 (sql_generator + recovery_agent) | 중 | 중 |

PR-1 먼저 merge → PR-2~PR-4 병렬 → PR-5 최종 통합.

### 3.3 예상 규모 총합

- 프롬프트 파일: **5 개** 편집 + **신규 섹션 총 5 개 (handoff_note 3 + previous_sql 2)** + visualizer judge 1 단락.
- Python 소스: **8 개** (state.py, utils/handoff.py, continue_orchestrator.py, query_normalizer.py × 2, context_interpreter.py, sql_generator.py, recovery_agent.py, graph/pipeline.py).
- 신규 테스트: **7 개** 파일.

---

## 4. 커밋별 파일 체인지 리스트

### 4.1 P3-#1: `query_normalizer` `{handoff_note}` directive 도입 (§14.3.1)

**목적**: REFINE route 진입 노드가 연속 처리 의도를 슬롯에 직접 반영하도록 한다.

**파일 체인지**

| 파일 | 라인 | 변경 내용 |
| --- | --- | --- |
| [resources/prompts/interpret/query_normalizer_phase1_system.txt](../../resources/prompts/interpret/query_normalizer_phase1_system.txt) | L258~259 사이 | `## 추가 출력 필드` 바로 앞에 `## 6. 연속 질의 오케스트레이터 지시 (handoff_note)` 섹션 신설 (parent §14.3.1 프롬프트 변경안 본문 그대로). 말미에 `{handoff_note}` 플레이스홀더. |
| [src/services/query_normalizer.py:580-618](../../src/services/query_normalizer.py#L580-L618) | `run_normalization` 시그니처 | `handoff_note: str = "(없음)"` 매개변수 추가 → `render_prompt` 치환맵에 `"{handoff_note}": handoff_note` 키 추가 |
| [src/agents/nodes/interpret/query_normalizer.py:73-80](../../src/agents/nodes/interpret/query_normalizer.py#L73-L80) | `run_normalization` 호출부 | `from src.agents.utils.handoff import normalize_handoff_note` import 추가 + `run_normalization(..., handoff_note=normalize_handoff_note(state.handoff_note))` |
| `tests/unit/services/test_query_normalizer_phase3.py` | 신규 | (1) NEW 턴 (`handoff_note=None` → `"(없음)"`) 슬롯 결과 기존 골든셋 동등, (2) CONTINUE REFINE 시나리오에서 `"서울 지점 조건 추가"` 의도 → FILTER 슬롯에 지점 조건 추가 검증 |

**Phase2 프롬프트 변경 없음** — §14.8 Q1 결정.

**검증 커맨드**

```bash
uv run pytest tests/unit/services/test_query_normalizer.py tests/unit/services/test_query_normalizer_phase3.py -x
uv run mypy src/services/query_normalizer.py src/agents/nodes/interpret/query_normalizer.py --strict
```

---

### 4.2 P3-#1': `previous_turn_sql` 기반공사 (§14.3.6 전반부)

**목적**: hydration 전용 read-only 필드 분리 + 공용 유틸 + orchestrator hydration 경로 변경.

**파일 체인지**

| 파일 | 라인 | 변경 내용 |
| --- | --- | --- |
| [src/agents/state/state.py:531+](../../src/agents/state/state.py#L531) | `ReasoningState` 본문 | `previous_turn_sql: str = ""` + `previous_turn_sql_explanation: str = ""` 2 필드 추가. 주석에 `W: continue_orchestrator (hydration only) / R: GEN, RCV` 명시. |
| [src/agents/utils/handoff.py](../../src/agents/utils/handoff.py) | 파일 말미 | `PREVIOUS_SQL_EMPTY_PLACEHOLDER = "(없음)"` 상수 + `def normalize_previous_sql(value: str | None) -> str` **단일 함수** 추가. docstring 에 "SQL 본문·설명 공용 (중복 금지)" 명시. |
| [src/agents/nodes/interpret/continue_orchestrator.py:481-484](../../src/agents/nodes/interpret/continue_orchestrator.py#L481-L484) | hydration 블록 | `reason.generated_sql = snapshot.generated_sql` 제거 → `reason.previous_turn_sql = snapshot.generated_sql` / `reason.previous_turn_sql_explanation = snapshot.sql_explanation or ""` 로 교체. **L481 주석** 을 `"모든 CONTINUE 경로에서 직전 턴 SQL 을 previous_turn_sql 로 복원 → sql_generator / recovery_agent 가 {previous_sql} 로 read-only 참조 (§14.3.6)"` 로 교체 (R2). |
| `tests/unit/utils/test_normalize_previous_sql.py` | 신규 | `None` / `""` / `"   "` / `"SELECT 1"` 4 케이스 폴백 동작 검증 |
| `tests/unit/state/test_previous_turn_sql_write_rule.py` | 신규 (**R3**) | sql_generator / sql_validator / recovery_agent 실행 전후 `reason.previous_turn_sql` · `reason.previous_turn_sql_explanation` 값 동등성 assert. hydration 경로 외 write 0 검증 단위 테스트. |

**사전 검증 (필수, 7.2 참조)**:
```bash
# reason.generated_sql 의존 경로가 더 없는지 확인
uv run grep -rn "reason.generated_sql\|snapshot.generated_sql" src/ tests/
```
의존 경로가 추가로 발견되면 본 커밋 범위 확장 후 동시 반영.

**검증 커맨드**

```bash
uv run pytest tests/unit/state/test_previous_turn_sql_write_rule.py tests/unit/utils/test_normalize_previous_sql.py -x
uv run pytest tests/integration/test_continue_orchestrator_hydration.py -x
uv run mypy src/agents/state/state.py src/agents/utils/handoff.py src/agents/nodes/interpret/continue_orchestrator.py --strict
```

---

### 4.3 P3-#2: `context_interpreter` Level 0 `{handoff_note}` hint-only (§14.3.2)

**목적**: 연속 처리 의도 힌트로 `unresolved_items` 판정 우선순위 조정.

**파일 체인지**

| 파일 | 라인 | 변경 내용 |
| --- | --- | --- |
| [resources/prompts/reason/context_interpreter_system.txt](../../resources/prompts/reason/context_interpreter_system.txt) | L646~647 사이 | `## 도구 실행 결과` 뒤, `[TASK]` 앞에 `## 연속 질의 오케스트레이터 지시 (handoff_note, 참고용)` 섹션 신설 (parent §14.3.2 프롬프트 변경안 본문 그대로). 말미에 `{handoff_note}` 플레이스홀더. |
| [src/agents/nodes/reason/context_interpreter.py:448-459](../../src/agents/nodes/reason/context_interpreter.py#L448-L459) | Level 0 배치 호출 | `batch_vars` 에 `handoff_note: normalize_handoff_note(state.handoff_note)` 추가. `render_vars` 키 포함. Level 1 개별 스텝(L554-567) 은 **건드리지 않음** (§14.8 Q2). |

**검증 커맨드**

```bash
uv run pytest tests/unit/agents/nodes/reason/test_context_interpreter.py -x
uv run mypy src/agents/nodes/reason/context_interpreter.py --strict
```

---

### 4.4 P3-#3: `visualizer` ANALYZE 재판정 가드 (§14.3.4)

**목적**: ANALYZE 경로에서 hydration 으로 복원된 이전 턴 visualization 을 무시하고 현재 분석 결과로 재판정.

**파일 체인지**

| 파일 | 라인 | 변경 내용 |
| --- | --- | --- |
| [resources/prompts/present/visualizer_judgment_system.txt](../../resources/prompts/present/visualizer_judgment_system.txt) | 기존 `{handoff_note}` 섹션 내 | `### 분석 초점` 가드 한 단락 추가 (parent §14.3.4 프롬프트 변경안 본문 그대로) |
| [src/agents/nodes/present/visualizer.py](../../src/agents/nodes/present/visualizer.py) | ANALYZE 분기 | judge 단계가 **매번 재실행**되는지 확인. 이미 매번 호출이면 코드 변경 0. 조건부 스킵 경로 있으면 제거. |

**사전 검증**:
```bash
uv run grep -n "judge_visualization\|state.visualization" src/agents/nodes/present/visualizer.py
```

**검증 커맨드**

```bash
uv run pytest tests/unit/agents/nodes/present/test_visualizer.py -x
```

---

### 4.5 P3-#4: REGENERATE 비-local_fix 차단 가드 확장 (§14.3.5)

**목적**: REGENERATE 경로에서 sql_validator non-local_fix 실패 발생 시 recovery_agent 진입 차단 → conclude_failure 직행.

**파일 체인지**

| 파일 | 라인 | 변경 내용 |
| --- | --- | --- |
| [src/agents/graph/pipeline.py](../../src/agents/graph/pipeline.py) | `_route_after_sql_validator` | 기존 분기 상단에 REGENERATE 가드 추가: `if state.route == ContinueRoute.REGENERATE and state.reason.failure_type not in {None, FailureType.SQL_SYNTAX, FailureType.SQL_SEMANTIC_LOCAL}: return "conclude_failure"` (parent §4.4.7 확장판 코드 그대로) |
| `tests/unit/agents/graph/test_regenerate_non_local_fix_guard.py` | 신규 | REGENERATE × {`EMPTY_RESULT`, `SQL_STRUCTURAL`, `DB_ERROR`, `SQL_SEMANTIC_GLOBAL`} 4 케이스 → `"conclude_failure"` assert / REGENERATE × {`None`, `SQL_SYNTAX`, `SQL_SEMANTIC_LOCAL`} 3 케이스 → 기존 루프 분기 유지 assert / REFINE·REDISPLAY·ANALYZE 는 영향 없음 assert |

**검증 커맨드**

```bash
uv run pytest tests/unit/agents/graph/test_regenerate_non_local_fix_guard.py tests/unit/agents/graph/ -x
uv run mypy src/agents/graph/pipeline.py --strict
```

---

### 4.6 P3-#5: `recovery_agent` 통합 + `sql_generator` `{previous_sql}` (§14.3.3 + §14.3.6 후반부)

**목적**: recovery_agent 에 `{handoff_note}` + `{previous_sql}` 동시 주입 + sql_generator 에 `{previous_sql}` 주입 완결. 프롬프트 · 코드 · 테스트를 **한 커밋에 묶어** 크로스 레퍼런스 누락 방지.

**파일 체인지**

| 파일 | 라인 | 변경 내용 |
| --- | --- | --- |
| [resources/prompts/reason/recovery_agent_system.txt](../../resources/prompts/reason/recovery_agent_system.txt) | L505~506 | 2 섹션 **동시** 신설. 순서: `## 직전 턴 참고 SQL (previous_sql)` → `## 연속 질의 오케스트레이터 지시 (handoff_note, 참고용)`. 복사금지 첫 불릿 포함 (§14.3.6 recovery_agent 프롬프트 변경안 그대로). 플레이스홀더 3 개: `{handoff_note}` · `{previous_sql}` · `{previous_sql_explanation}`. |
| [src/agents/nodes/reason/recovery_agent.py:1105-1118](../../src/agents/nodes/reason/recovery_agent.py#L1105-L1118) | `_build_recovery_prompt::replacements` | 3 치환 추가 (import 갱신): `"{handoff_note}": normalize_handoff_note(state.handoff_note)` / `"{previous_sql}": normalize_previous_sql(reason.previous_turn_sql)` / `"{previous_sql_explanation}": normalize_previous_sql(reason.previous_turn_sql_explanation)`. 호출부 시그니처에 `state` 또는 `handoff_note` 전파 확인. |
| [resources/prompts/reason/sql_generator_system.txt](../../resources/prompts/reason/sql_generator_system.txt) | 기존 `{handoff_note}` 섹션 **인접** | `## 직전 턴 참고 SQL (previous_sql)` 섹션 신설. 복사금지 첫 불릿 포함 (§14.3.6 sql_generator 프롬프트 변경안 그대로). 플레이스홀더 2 개. |
| [src/agents/nodes/reason/sql_generator.py:438-450](../../src/agents/nodes/reason/sql_generator.py#L438-L450) | `replacements` | `"{previous_sql}": normalize_previous_sql(reason.previous_turn_sql)` + `"{previous_sql_explanation}": normalize_previous_sql(reason.previous_turn_sql_explanation)` 2 치환 추가 |
| `tests/unit/agents/nodes/reason/test_sql_generator_previous_sql.py` | 신규 | (1) NEW 턴 `previous_turn_sql=""` 시 `{previous_sql}="(없음)"` 치환 + 기존 SQL 생성 골든셋 동등, (2) REGENERATE 에서 직전 SQL 구조 재현 + `fix_section` 과 충돌 시 `fix_section` 우선 스냅샷 |
| `tests/unit/agents/nodes/reason/test_recovery_agent_phase3.py` | 신규 | (1) NEW 턴 `handoff_note=None` + `previous_turn_sql=""` 시 기존 execution_plan 골든셋 동등, (2) CONTINUE REGENERATE 실패 시나리오에서 `{previous_sql}` 이 탐색 방향 결정에 반영되는 snapshot |

**R5 NEW 턴 회귀 방어 테스트** — 본 커밋 또는 별도 커밋으로 추가:

| 파일 | 라인 | 변경 내용 |
| --- | --- | --- |
| `tests/unit/prompts/test_phase3_new_turn_headers.py` | 신규 (**R5**) | 신규 3 섹션 상위 헤더(`## 6. 연속 질의 ...` / `## 연속 질의 ... 참고용` / `## 직전 턴 참고 SQL`) 는 NEW 턴 렌더링 결과에 **존재** / 서브헤더 `### 연속 처리 의도` · `### SQL 생성 지시` · `### 분석 초점` · `### 시각화/포맷 지시` 는 **부재** assert |

**검증 커맨드**

```bash
uv run pytest tests/unit/agents/nodes/reason/ tests/unit/prompts/test_phase3_new_turn_headers.py -x
uv run pytest tests/integration/test_continue_regenerate_full_flow.py -x
uv run mypy src/agents/nodes/reason/sql_generator.py src/agents/nodes/reason/recovery_agent.py --strict
```

---

## 5. 공통 테스트와 회귀 가드

### 5.1 각 커밋 필수 회귀

```bash
# 전체 단위 회귀
uv run pytest tests/unit -x --ff

# 기존 handoff_note 소비자 4 개 회귀
uv run pytest tests/unit/agents/nodes/reason/test_sql_generator.py \
               tests/unit/agents/nodes/reason/test_sql_validator.py \
               tests/unit/agents/nodes/analyze/test_analyzer.py \
               tests/unit/agents/nodes/present/test_visualizer.py -x

# 타입 체크
uv run mypy src/ --strict
```

### 5.2 NEW 턴 회귀 (5 채널)

- `query_normalizer`: 8-slot 정규화 골든셋 `handoff_note=None` 동등
- `context_interpreter`: 지식 판정 골든셋 Level 0 배치 동등
- `recovery_agent`: execution_plan 골든셋 동등
- `sql_generator`: SQL 생성 골든셋 `previous_turn_sql=""` 동등
- `recovery_agent` (previous_sql): execution_plan 골든셋 동등

### 5.3 CONTINUE 턴 통합 시나리오 (신규)

- REGENERATE + `{previous_sql}` 직전 구조 재현
- REFINE 경로 `query_normalizer → context_interpreter → sql_generator → validator` 에서 `handoff_note` 일관 반영
- recovery_agent 진입 시 `{previous_sql}` 이 연속성 앵커로 탐색 방향 결정
- REGENERATE × EMPTY_RESULT → conclude_failure 직행

### 5.4 R3 / R5 신규 테스트 (재강조)

- **R3**: `tests/unit/state/test_previous_turn_sql_write_rule.py` — hydration 외 write 0
- **R5**: `tests/unit/prompts/test_phase3_new_turn_headers.py` — 서브헤더 NEW 턴 부재

---

## 6. 리스크와 롤백 가드

### 6.1 Blast radius 순서

1. **P3-#1' (state + hydration)** — 가장 큰 변경. `reason.generated_sql` 의존 경로가 있으면 누락 시 회귀 위험. 별도 PR merge.
2. **P3-#5 (recovery_agent 통합)** — 프롬프트 2 섹션 + 코드 2 파일 동시 변경. 가장 많은 크로스 레퍼런스.
3. 나머지 (P3-#1 / P3-#2 / P3-#3 / P3-#4) — 국소 변경.

### 6.2 롤백 전략

- 각 PR 은 독립 revert 가능하도록 설계 — 한 PR 에 복수 커밋 금지.
- PR-1 (P3-#1') revert 시 후속 PR 의 `{previous_sql}` 주입 코드가 `reason.previous_turn_sql` 참조로 깨짐 → PR-1 revert 할 때는 PR-2~PR-5 도 함께 revert.
- PR-4 (REGENERATE 가드) 는 단독 revert 안전.

### 6.3 폐쇄망 (Qwen3.5 397B) 검증

- NEW 턴 드리프트: R5 테스트로 프롬프트 레벨 차단.
- 직전 SQL 복사 행위: C5 의 "복사 금지" 첫 불릿 배치 + `{fix_section}` 우선 명시.
- JSON 출력 안정성: query_normalizer / recovery_agent `[HARD_CONSTRAINTS]` 오버라이드 불가 명시로 커버.

---

## 7. 착수 전 사전 검증 체크리스트

구현 시작 **직전** 수행 필수.

### 7.1 parent 문서 §14 최신 상태 확인

```bash
# §14.3.6 에 단일 normalize_previous_sql 반영 확인
uv run grep -n "normalize_previous_sql_explanation" docs/todo/20260418-continue-orchestrator-4way-redesign.md
# → 결과 0 줄이어야 함 (C2 반영 확인)

# 헤더 네이밍 통일 확인
uv run grep -n "연속 처리 의도 (handoff_note)" docs/todo/20260418-continue-orchestrator-4way-redesign.md
# → 이전 (Phase 1/2) 선례 서술 영역만 남아있어야 함
```

### 7.2 `reason.generated_sql` 의존 경로 탐색

```bash
uv run grep -rn "reason.generated_sql" src/ tests/
```
- hydration 외 write 경로: sql_generator 결과 저장 1 지점만 예상. 추가 발견 시 P3-#1' 범위 확장.
- read 경로: sql_validator, result_finalizer, formatter 등이 **현재 턴** 결과 읽어야 하므로 유지. `previous_turn_sql` 로 잘못 치환하지 않도록 주의.

### 7.3 기존 코드 스타일 · 패턴 확인

```bash
uv run cat src/agents/utils/handoff.py         # normalize_handoff_note 시그니처 참조
uv run cat resources/prompts/reason/sql_generator_system.txt | head -100   # 헤더 선례
```

### 7.4 테스트 실행 환경 확인

```bash
uv sync                               # 의존성 동기화
uv run pytest --collect-only tests/unit -q | tail -5   # 수집 가능 여부
uv run mypy src/ --strict             # 기존 baseline PASS
```

---

## 8. 진행 체크리스트

### 8.1 PR-1 (P3-#1')

- [ ] `reason.generated_sql` 의존 경로 전수 조사 완료 (§7.2)
- [ ] `ReasoningState` 2 필드 추가 + 주석
- [ ] `normalize_previous_sql(value)` 단일 함수 추가 (handoff.py)
- [ ] `continue_orchestrator.py:481-484` hydration 교체 + 주석 갱신 (R2)
- [ ] `tests/unit/utils/test_normalize_previous_sql.py` 4 케이스
- [ ] `tests/unit/state/test_previous_turn_sql_write_rule.py` (R3)
- [ ] 기존 hydration integration 테스트 PASS
- [ ] mypy --strict PASS

### 8.2 PR-2 (P3-#1)

- [ ] `query_normalizer_phase1_system.txt` §6 섹션 신설
- [ ] `run_normalization(...)` 시그니처 확장
- [ ] `query_normalizer.py:73-80` 호출부 갱신
- [ ] `tests/unit/services/test_query_normalizer_phase3.py` NEW + REFINE 2 케이스
- [ ] Phase2 프롬프트 변경 **없음** 확인
- [ ] mypy --strict PASS

### 8.3 PR-3 (P3-#2 + P3-#3)

- [ ] `context_interpreter_system.txt` hint-only 섹션 신설
- [ ] `context_interpreter.py:448-459` Level 0 치환 추가
- [ ] Level 1 (L554-567) 변경 없음 확인
- [ ] `visualizer_judgment_system.txt` ANALYZE 가드 단락 추가
- [ ] visualizer.py judge 재실행 경로 확인
- [ ] mypy --strict PASS

### 8.4 PR-4 (P3-#4)

- [ ] `pipeline.py::_route_after_sql_validator` REGENERATE 가드 추가
- [ ] `tests/unit/agents/graph/test_regenerate_non_local_fix_guard.py` 7 케이스
- [ ] REFINE/REDISPLAY/ANALYZE 영향 없음 assert 포함
- [ ] mypy --strict PASS

### 8.5 PR-5 (P3-#5)

- [ ] `recovery_agent_system.txt` 2 섹션 동시 신설 (복사금지 첫 불릿 포함)
- [ ] `recovery_agent.py:1105-1118` replacements 3 치환 추가
- [ ] `sql_generator_system.txt` `{previous_sql}` 섹션 신설
- [ ] `sql_generator.py:438-450` replacements 2 치환 추가
- [ ] `tests/unit/agents/nodes/reason/test_sql_generator_previous_sql.py`
- [ ] `tests/unit/agents/nodes/reason/test_recovery_agent_phase3.py`
- [ ] `tests/unit/prompts/test_phase3_new_turn_headers.py` (R5)
- [ ] CONTINUE 통합 시나리오 PASS
- [ ] mypy --strict PASS

### 8.6 최종 통합

- [ ] 기존 1767 unit + 143 auto test PASS
- [ ] 토큰 길이 실측 → §14.7.2 표 갱신 (C3 후속)
- [ ] parent 문서 §14.9 모든 체크박스 체크
- [ ] 본 계획서 §8 모든 체크박스 체크
- [ ] memory `project_phase3_scope.md` 상태 "구현 완료" 로 업데이트

---

## 9. 참고 레퍼런스

### 9.1 parent 문서 주요 섹션

- 설계 전체: [20260418-continue-orchestrator-4way-redesign.md §14](20260418-continue-orchestrator-4way-redesign.md)
- hydration 매트릭스: §3.3
- `_build_hydration_updates`: §4.4.3
- `_route_after_sql_validator` / `_route_after_sql_generator`: §4.4.7
- sql_generator 주석 정정: §4.6 (L899)
- `{handoff_note}` 소비 원칙: §2.5 (Phase 3 각주 포함)

### 9.2 기존 코드 위치 (구현 시 참조)

- [src/agents/utils/handoff.py](../../src/agents/utils/handoff.py) — `normalize_handoff_note` 시그니처 선례
- [src/agents/state/state.py:531+](../../src/agents/state/state.py#L531) — `ReasoningState` 정의
- [src/agents/nodes/interpret/continue_orchestrator.py:481-484](../../src/agents/nodes/interpret/continue_orchestrator.py#L481-L484) — hydration 수정 지점
- [src/agents/graph/pipeline.py](../../src/agents/graph/pipeline.py) — `_route_after_sql_validator`
- [resources/prompts/reason/sql_generator_system.txt:401](../../resources/prompts/reason/sql_generator_system.txt#L401) — 헤더 네이밍 선례 (directive)
- [resources/prompts/reason/sql_validator_system.txt:652](../../resources/prompts/reason/sql_validator_system.txt#L652) — 헤더 네이밍 선례 (hint-only)

### 9.3 memory 포인터 (`C:\Users\cjfgm\.claude\projects\...\memory\`)

- `project_phase3_scope.md` — Phase 3 는 전수 설계, 좁게 해석 금지
- `project_phase3_previous_sql_injection.md` — `{previous_sql}` 주입 채널 결정
- `project_regenerate_non_local_fix_guard.md` — REGENERATE 차단 배경
- `feedback_consumer_opt_in_pattern.md` — rule-based/별도 LLM 금지
- `feedback_holistic_working_principle.md` — 6 관점 점검 원칙
- `feedback_stay_within_agreed_design.md` — 합의된 설계 범위 준수

### 9.4 관련 rules (`.claude/rules/`)

- `holistic-thinking.md` — 6 관점 작업 원칙
- `code-style.md` — 기존 구현 확인 의무 · 중복 금지
- `data-security.md` — SELECT 전용 · 프롬프트 인젝션 방어
- `financial-domain.md` — 유사 테이블 · 불완전 메타 대응
- `user-interaction.md` — IT 용어 최소화 (recovery_agent ask_user 에 영향)
