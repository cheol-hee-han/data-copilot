# Multi-Turn CONTINUE Orchestrator — 구현 현황 정리

작성일: 2026-04-17
상태: **구현 중단 (사용자 지시 — 남은 범위 재검토 필요)**
설계 원본: `docs/todo/20260416-multi-turn-continue-orchestrator-design.md`

---

## 요약

사용자 지시는 **ConversationHistory 설계 단계까지**였으나, compaction 이후 재개에서 범위를 오해하여
Phase 1~4b(코드 구현 대부분)까지 전진했다. 남은 Phase 5·6은 미착수 상태이며
모든 변경은 **언커밋** 이다 (git staged/commit 없음).

---

## 현재까지 구현된 범위

| Phase | 내용 | 상태 | 테스트 |
|---|---|---|---|
| Phase 1 | 데이터 모델·Enum·State 필드 | 완료 | 32 통과 |
| Phase 2 | save_turn_snapshot 노드·runner seq·pipeline edge | 완료 | 6 통과 (test_node_chain) |
| Phase 3 | TurnSnapshotStore·main.py 세션 재접속 복원 | 완료 | 30 통과 |
| Phase 4a | continue_orchestrator 프롬프트 2개 | 완료 | — |
| Phase 4b | intent_classifier CONTINUE_DETECTED·continue_orchestrator 노드·pipeline 라우팅 | **미검토** (방금 완료 보고만 받음) | test_continue_orchestrator (검증 필요) |
| Phase 5 | 하류 노드 snapshot 참조 | 미착수 | — |
| Phase 6 | 통합 테스트 | 미착수 | — |

전체: 1841 passed(Phase 3 완료 시점) + Phase 4b 테스트 미검증.

---

## 신설 파일 (NEW)

| 경로 | 용도 | Phase |
|---|---|---|
| `src/agents/models/snapshot.py` | TurnSnapshot Pydantic v2 모델 (9 필드, frozen, extra=forbid) | 1 |
| `src/agents/nodes/present/save_turn_snapshot.py` | 턴 완료 시 snapshot append + FIFO 4개 | 2 |
| `src/services/turn_snapshot_store.py` | DB 복원(Partial Hydration, MongoDB fan-out) | 3 |
| `resources/prompts/interpret/continue_orchestrator_system.txt` | 오케스트레이터 시스템 프롬프트 (7 few-shot) | 4a |
| `resources/prompts/interpret/continue_orchestrator_user.txt` | 사용자 프롬프트 템플릿 | 4a |
| `src/agents/nodes/interpret/continue_orchestrator.py` | 오케스트레이터 LLM 노드 (Command 반환) | 4b |
| `tests/auto/unit/test_turn_snapshot_model.py` | 32 단위 테스트 (W4 Command 주입 포함) | 1 |
| `tests/auto/unit/test_turn_snapshot_store.py` | 30 단위 테스트 (Partial Hydration 9 케이스) | 3 |
| `tests/auto/unit/test_continue_orchestrator.py` | 오케스트레이터 노드 테스트 | 4b |

## 수정 파일 (MODIFIED)

| 경로 | 변경 내용 | Phase |
|---|---|---|
| `src/models/enums.py` | `QueryStatus.CONTINUE_DETECTED` + `ContinueRoute(redisplay/analyze/refine)` 추가 (3-way, 2026-04-18 재설계) | 1 |
| `src/agents/state/state.py` | PipelineState 5 필드 추가 (turn_snapshots/reference_snapshot/continue_route/continue_hint/current_user_message_seq). `turn_reset_updates()` 4 필드 리셋 추가. turn_snapshots/reference_snapshot는 순환 임포트 회피로 `Any` 타입 (TODO: meta.py 분리) | 1 |
| `src/agents/graph/runner.py` | `insert_message` 반환 seq를 `initial_state.current_user_message_seq`로 전파. `initial_turn_snapshots` 파라미터 추가(세션 재접속 복원용) | 2, 3 |
| `src/agents/graph/pipeline.py` | `formatter → save_turn_snapshot → END` 엣지 변경. `continue_orchestrator` 노드 등록 + `_route_after_intent_classifier`에 CONTINUE_DETECTED 분기 | 2, 4b |
| `src/services/process_summary_builder.py` | `inferences` dict에 `reason: s.reasoning or ""` 1줄 추가 | 2 |
| `src/main.py` | 기존 session_id 재접속 시 `TurnSnapshotStore.restore_from_db()` 호출 → turn_snapshots 주입 | 3 |
| `src/agents/nodes/system_prompts.py` | `CONTINUE_ORCHESTRATOR_SYSTEM`/`CONTINUE_ORCHESTRATOR_USER` 상수 등록 (v2.2) | 4a |
| `src/services/intent_classifier.py` | CONTINUE 판정 + turn_snapshots 존재 시 `QueryStatus.CONTINUE_DETECTED` 설정 | 4b |
| `src/agents/nodes/thinking_modes.py` | `LLMNode.CONTINUE_ORCHESTRATOR` enum 값 추가 (추정 — 검증 필요) | 4b |
| `tests/auto/e2e/test_node_chain.py` | `CONTINUE_DETECTED`를 terminal 집합에 추가 | 2 |

---

## 핵심 설계 결정 (구현에 반영된 것)

### 1. TurnSnapshot 9 필드 스키마
```
user_message_seq, intent, generated_sql, sql_explanation,
result_data(rows 제외), visualization, selected_tables(TableMeta 풀),
explored_codes(dict[str, CodeMeta]), inferred_signals(dict list)
```
- `frozen=True, extra="forbid"` — 불변·스키마 엄격
- **rows 단일 원천**: checkpoint_dc_messages.metadata.result_data.rows (JIT hydration)

### 2. FIFO 4개 제한
- 설계 근거: CoE-SQL(NAACL 2024) 연구에서 4턴 초과 시 성능 저하
- 4개 초과 시 앞부터 제거

### 3. 저장·복원 경로
- **세션 중**: `state.turn_snapshots` (in-memory) — save_turn_snapshot 노드가 관리
- **DB 저장**: Phase 2는 state에만 쓰고 DB에는 **쓰지 않음**. 기존 `update_message_metadata`가 저장하는 필드(result_data, visualization, process_summary, executed_sql 등)를 복원 시 재활용
- **재접속 복원**: 옵션 B — process_summary 파싱 + sqlglot 컬럼 추출 + MongoDB fan-out으로 9 필드 재구성
- **Partial Hydration**: 일부 실패해도 나머지 복원

### 4. 스킵 규칙
- **C3 REDISPLAY 스킵**: `continue_route == REDISPLAY`이면 snapshot 저장 스킵(이전 스냅샷이 참조 대상으로 유지)
- **I4 intent_classifier 제외**: CONTINUE 판정 시 생성되는 "연속으로 해석" INFER 시그널은 snapshot에서 제외(반복 노출 방지)
- **비데이터 턴 스킵**: `validated_sql` 없으면 snapshot 저장 자체를 스킵

### 5. Command 기반 라우팅 (§3.5, §4.3) — 2026-04-18 3-way 재설계
continue_orchestrator는 정적 엣지 없이 `Command(update=..., goto=...)`로만 분기:
| route | goto | 하류 처리 |
|---|---|---|
| `redisplay` | `visualizer` | result_data/visualization을 SQLResult로 hydration 후 재렌더 (SQL 재실행 없음) |
| `analyze` | `analyzer` | result_data hydration 후 분석, 이후 visualizer → formatter 합류 |
| `refine` | `query_normalizer` | 스냅샷 selected_tables 시드 + handoff_note 를 기반으로 재추론, SQL 재생성 |

### 6. 순환 불가 (상류 회귀 없음)
3-way 라우팅은 모두 하류 노드로만 향한다. 이전 4-way 설계의 `fallback → intent_classifier`
(상류 회귀) 경로는 제거됐다. 빈 스냅샷·LLM 파싱 실패 등 판정 불가 상황은 즉시 `error_end`.
→ 재진입 가드(`_count_orchestrator_reentry`·`_MAX_ORCHESTRATOR_REENTRY`)도 함께 제거됨.

### 7. Partial Hydration 커버
| 실패 케이스 | 처리 |
|---|---|
| DB pool=None | `[]` 반환 |
| DB 조회 전체 실패 | `[]` 반환 |
| 성공 턴 없음 | `[]` 반환 |
| user_seq 조회 실패 | seq=0 폴백 |
| 개별 턴 빌드 실패 | 해당 턴만 스킵 |
| MongoDB 테이블 조회 실패 | 해당 테이블만 누락 |
| MongoDB 코드 컬럼 조회 실패 | 해당 컬럼만 누락 |
| process_summary 파싱 실패 | selected_tables=[], inferred_signals=[] |
| SQL 파싱(sqlglot) 실패 | explored_codes={} |

---

## 중간 검토에서 제기된 개선 권장(미반영)

Phase 2·3 검토에서 차단 이슈는 없었으나 아래 🟡 개선은 반영 안 됨(Phase 5~6 이후 정리 예정):

| 항목 | 내용 | 위치 |
|---|---|---|
| W1 (Phase 1) | `turn_snapshots/reference_snapshot`의 `Any` 타입 — TableMeta/CodeMeta를 `src/agents/models/meta.py`로 분리 후 forward ref 복원 | `src/agents/state/state.py` |
| W2 (Phase 2) | save_turn_snapshot 위치(present 계층) vs `turn_reset_updates()` 위치 분산 — docstring 대칭 관계는 추가됨, 파일 이동은 보류 | — |
| W3 (Phase 3) | 코드 컬럼 추출 정규식(`_CD|_TP|_FG|…`)이 설계 문서에 없는 암묵 가정 — `resources/config/`로 외부화 권장 | `turn_snapshot_store.py` |
| W4 (Phase 3) | MongoDB fan-out 무제한 동시성 — 4턴 × 10 테이블 = 40 동시 쿼리. `asyncio.Semaphore(10)` 적용 권장 | `turn_snapshot_store.py` |

---

## Phase 4b (방금 완료 보고받음, 미검토) 상세

### 실제 구현 확인된 것 (2026-04-18 3-way 재설계 반영)
`src/agents/nodes/interpret/continue_orchestrator.py` 구조:
- `_serialize_snapshots()` — turn_snapshots를 프롬프트용 YAML 유사 텍스트로 직렬화 (1턴당 5-7줄, rows 제외)
- `_parse_orchestrator_response()` — JSON 파싱 + 정규식 백업 파싱 (폐쇄망 70B 대응)
- `_find_snapshot()` — reference_turn_seq로 스냅샷 조회
- `_build_error_end_command()` — 판정 불가(빈 스냅샷/파싱 실패) 시 error_end로 즉시 종료
- `continue_orchestrator_node()` — 메인 노드 함수, LLM 호출 후 Command 반환
  - REDISPLAY/ANALYZE 경로에서 result_data→SQLResult, visualization→VisualizationData **오케스트레이터가 hydration**
  - 스냅샷 없음·파싱 실패는 상류 회귀 없이 즉시 error_end (순환 위험 원천 차단)

### 에이전트가 판단한 설계 이슈 (해소 완료)
1. **snapshot → SQLResult hydration 위치**: 오케스트레이터가 수행. 하류 노드(visualizer/analyzer)는 state.sql_result를 평소처럼 읽음
2. **판정 불가 폴백**: 상류 회귀 없이 error_end (3-way 모두 하류이므로 순환 불가)
3. **analyze 강제 intent 교체**: `data_analysis`로 강제 (C2 오라우팅 방지)
4. **refine 경로 intent**: 대표 스냅샷 intent 유지 (기본 DATA_EXTRACTION)
5. **redisplay 경로 intent**: 대표 스냅샷 intent 유지

---

## 테스트 현황

| 파일 | 테스트 수 | 상태 |
|---|---|---|
| `test_turn_snapshot_model.py` | 32 | 통과 |
| `test_turn_snapshot_store.py` | 30 | 통과 |
| `test_node_chain.py` (CONTINUE_DETECTED terminal) | 1 추가 | 통과 |
| `test_continue_orchestrator.py` | ? | **미검증** |
| 전체 | 1841+α | Phase 4b 테스트 실행 필요 |

---

## 롤백 가능성

모든 변경은 커밋 전이므로 `git restore` + `rm` 으로 완전 원복 가능하다.
중간 지점 롤백(예: Phase 1만 남기기)도 가능.

```
# 전체 롤백 예시 (주의: 이 CONTINUE 작업 외의 modified 파일도 같이 있을 수 있음)
# 반드시 개별 확인 후 실행.
```
