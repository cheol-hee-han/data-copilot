---
name: TurnSnapshotStore Phase 3 구현 완료
description: 세션 재접속 시 checkpoint_dc_messages에서 TurnSnapshot 복원 서비스 신설 및 main.py 훅 연결
type: project
---

Phase 3 구현 완료 (2026-04-17). 기존 1836 → 1841 tests pass.

**Why:** 서버 재시작/세션 재접속 후 CONTINUE Orchestrator가 이전 턴 맥락을 잃지 않도록 DB에서 스냅샷 복원이 필요.

**선택한 옵션: B** — Phase 2에서 save_turn_snapshot은 state에만 저장하지만, runner.py의 `update_message_metadata`가 `result_data`, `visualization`, `process_summary`를 metadata JSONB에 이미 저장하고 있어 별도 저장 훅 추가 없이 복원 가능.

**신설 파일:**
- `src/services/turn_snapshot_store.py` — `restore_from_db(pool, session_id, limit=4)` 함수 제공
- `tests/auto/unit/test_turn_snapshot_store.py` — 30개 단위 테스트

**수정 파일:**
- `src/agents/graph/runner.py` — `run_pipeline` + `_execute_and_finalize`에 `initial_turn_snapshots` 파라미터 추가, `initial_state.turn_snapshots`에 주입
- `src/main.py` — `_run_ws_pipeline`에서 대화이력 있는(기존) 세션 재접속 시 `restore_from_db` 호출 후 `run_pipeline`에 전달

**DB 복원 원천 매핑:**
- `executed_sql` 컬럼 → generated_sql
- `sql_explanation` 컬럼 → sql_explanation
- `metadata.result_data` (rows 제외) → result_data
- `metadata.visualization` → visualization
- `metadata.process_summary.context.tables[used=true].name` → selected_tables 이름 → lookup_table_meta fan-out
- `executed_sql` sqlglot 파싱 + _CD/_TP 등 패턴 → explored_codes 이름 → lookup_code_meta fan-out
- `metadata.process_summary.ai_decisions.inferences` (source_node≠intent_classifier) → inferred_signals

**How to apply:** Phase 3 이후 변경 시 runner.py initial_state 구성과 main.py _run_ws_pipeline의 복원 경로 함께 확인.
