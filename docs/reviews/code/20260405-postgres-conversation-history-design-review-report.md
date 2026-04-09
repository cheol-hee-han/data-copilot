# 설계문서 리뷰: PostgreSQL 기반 대화 이력 관리

- **대상**: `docs/todo/20260405-postgres-conversation-history-design.md`
- **리뷰일**: 2026-04-05
- **리뷰어**: Code Reviewer Agent
- **등급**: Critical(R), Warning(Y), Info(G)

---

## 요약 (200자 이내)

설계 철학과 아키텍처는 건전하나, 실제 코드베이스와의 **통합 이슈가 다수** 존재한다. `handler.elapsed_ms`, `handler.last_node_name`, `settings.llm.model_id` 등 설계문서가 참조하는 API가 현재 코드에 없다. ConnectorManager에 `dc_pool`을 추가하는 방식은 체크포인터 pool과 `autocommit` 설정이 충돌하며, Phase 2에서 dc_pool을 lifespan에서도 별도 생성하여 이중 관리 위험이 있다. Phase 3의 SessionStore 축소 범위가 불명확하고, main.py의 `store.append_history()` 호출 위치가 설계문서의 가정과 다르다.

---

## A. 통합 이슈

### R-01. `handler.elapsed_ms` 존재하지 않음

- **위치**: 설계문서 5.1 1-4, 681행
- **현상**: `latency_ms=int(handler.elapsed_ms)` 참조. `DataCopilotCallbackHandler`에 `elapsed_ms` 프로퍼티가 없다. `_start_time` 필드만 있고 경과 시간을 계산하는 public API가 없음.
- **영향**: Phase 1 구현 시 런타임 AttributeError
- **해결**: `DataCopilotCallbackHandler`에 `elapsed_ms` 프로퍼티 추가 필요. 또는 `runner.py`에서 직접 시간 측정.

### R-02. `handler.last_node_name` 존재하지 않음

- **위치**: 설계문서 5.1 1-4, 684행
- **현상**: `exit_node=handler.last_node_name` 참조. 핸들러에 `_run_to_node` dict는 있으나 "마지막 노드명"을 반환하는 public 프로퍼티 없음.
- **영향**: Phase 1 구현 시 런타임 AttributeError
- **해결**: 핸들러에 `last_node_name` 프로퍼티 추가하거나, `result` dict에서 상태 기반으로 추론.

### R-03. `handler.run_id` 접근 방식 불일치

- **위치**: 설계문서 5.1 1-4, 685행
- **현상**: `trace_id=handler.run_id` 참조. 실제 핸들러에서는 `self._run_id` (private). public `run_id` 프로퍼티 미정의.
- **영향**: AttributeError 또는 private 필드 직접 접근(캡슐화 위반)
- **해결**: `run_id` public 프로퍼티 추가.

### R-04. `settings.llm.model_id` 존재하지 않음

- **위치**: 설계문서 5.1 1-4, 685행
- **현상**: `model_id=settings.llm.model_id` 참조. 실제 config.py에 `settings.llm` 중첩 객체 없음. 모델명은 `settings.llm_model` (flat 필드).
- **영향**: AttributeError
- **해결**: `settings.llm_model`로 수정.

### Y-05. `result` 변수 타입 혼동 — `result` vs `result_dict`

- **위치**: 설계문서 5.1 1-4, 660~697행
- **현상**: 설계문서에서 `result = _build_result(handler, result_dict)` 후, `result_dict.get("reason")` 등으로 원본 dict에도 접근. 그러나 runner.py 실제 코드에서 `result`는 `app.ainvoke()` 반환값 (dict)이고, `_build_result()`가 PipelineResult로 변환한다. 설계문서는 `result_dict`라는 변수명을 도입하여 두 값을 구분하지만, 실제 코드(runner.py:198)에서 `_build_result(handler, result)` 호출 후 `result`를 PipelineResult로 재할당하므로 원본 dict 참조가 불가.
- **영향**: 구현 시 원본 dict를 별도 변수로 보존해야 함. 설계대로 구현하면 변수 섀도잉 발생.
- **해결**: runner.py에서 `raw_state = result` / `pipeline_result = _build_result(handler, raw_state)` 패턴으로 명확히 분리.

### Y-06. ConnectorManager `dc_pool` vs Phase 2 lifespan `dc_pool` 이중 관리

- **위치**: 설계문서 5.1 1-3 (609~638행) vs 5.2 2-2 (802~819행)
- **현상**: Phase 1(1-3)에서 ConnectorManager 내부에 `_dc_pool`을 생성하면서, Phase 2(2-2)에서는 main.py lifespan에서 별도로 `dc_pool`을 생성. 동일 DB에 두 개의 커넥션 풀이 생성되며, 어느 것을 사용할지 모호.
- **영향**: 커넥션 리소스 낭비, pool 관리 혼란
- **해결**: 하나로 통일. ConnectorManager에서 관리하면 lifespan 코드에서 별도 생성 불필요. 또는 lifespan에서만 생성하고 ConnectorManager에 주입.

### Y-07. main.py의 현재 `store.append_history()` 호출 위치가 설계 가정과 불일치

- **위치**: 설계문서 5.3 Phase 3 (836~847행)
- **현상**: 설계문서의 "변경 전" 코드가 `await store.add_message(...)` 패턴을 가정하지만, 실제 main.py(343행)는 `store.append_history()`를 사용하며, `try/finally` 블록 안에서 user 턴을 먼저 기록하고 pipeline_result 후 assistant 턴을 기록. 메서드명과 호출 구조가 다름.
- **영향**: Phase 3 구현 시 실제 삭제 대상 코드가 설계문서의 가정과 다름
- **해결**: Phase 3 변경 전/후 코드를 실제 main.py 기준으로 재작성.

### Y-08. main.py WebSocket에서 `request` 객체 접근 불가

- **위치**: 설계문서 5.1 1-5, 761~763행
- **현상**: `client_ip=request.client.host`, `user_agent=request.headers.get("user-agent")` 참조. WebSocket 핸들러에서는 `request` 객체가 아닌 `websocket` 객체를 사용. `websocket.client.host`와 `websocket.headers.get("user-agent")`가 올바른 접근 경로.
- **영향**: NameError (request 미정의)
- **해결**: `websocket.client.host`, `websocket.headers.get("user-agent")`로 수정.

### G-09. `current_user.id` 참조 — 인증 체계 미구현

- **위치**: 설계문서 5.1 1-5, 760행
- **현상**: `user_id=current_user.id` 참조. 현재 코드에 인증/사용자 식별 체계 없음. `current_user` 객체 미정의.
- **영향**: 구현 시 `"anonymous"` 하드코딩이 필요하거나 인증 체계를 먼저 구현해야 함
- **해결**: Phase 1에서는 `user_id="anonymous"` 고정, SSO 연동은 별도 작업으로 분리.

---

## B. 일관성

### Y-10. DDL `turn_id` DEFAULT vs `save_turn()` 파라미터 불일치

- **위치**: DDL 155행 vs save_turn() 548~603행
- **현상**: DDL에서 `turn_id UUID NOT NULL DEFAULT gen_random_uuid()`로 DB 자동 생성하지만, `save_turn()`의 INSERT 문에 `turn_id` 컬럼이 포함되지 않음. 이는 의도적(DB 자동 채번)이나, DDL의 `turn_id`와 PipelineState의 `turn_id`(str, runner.py에서 uuid4 생성)가 동일 값인지 불명확.
- **영향**: 두 시스템의 turn_id가 다른 값을 가져 교차 참조 불가능할 수 있음
- **해결**: PipelineState의 `turn_id`를 INSERT 시 명시적으로 전달하거나, 두 turn_id의 관계를 문서에 명시.

### Y-11. `metadata` JSONB 구조와 PipelineResult 필드 불완전 매핑

- **위치**: 설계문서 §2.2.2 (218~265행) 매핑 테이블 (268~277행)
- **현상**: metadata에 `retry_count`, `context_sources_hit`, `node_durations_ms`, `validation_errors` 등 운영 메트릭이 포함되지만, 이 데이터를 PipelineResult나 handler에서 어떻게 추출하는지 코드 예시가 없음. 특히 `node_durations_ms`는 핸들러의 타임라인 데이터를 가공해야 하지만 해당 API 미정의.
- **영향**: 구현 시 metadata 일부 필드가 빈 값으로 저장될 수 있음
- **해결**: Phase 1에서는 확실히 채울 수 있는 필드만 포함하고, 운영 메트릭은 Phase 2 이후로 분리.

### G-12. Phase 번호와 단계 번호 일관성

- **위치**: 5, 6, 7 전체
- **현상**: Phase 1(5.1) ~ Phase 4(5.4) 번호가 일관되며, 6(파일별 매트릭스)과 7(구현 순서)도 Phase 1~4를 정확히 참조. 일관성 양호.
- **등급**: 이슈 없음.

---

## C. 디자인 패턴

### Y-13. `turn_text_store.py`가 `pool`을 직접 받는 패턴 — 프로젝트 패턴과 불일치

- **위치**: 설계문서 5.1 1-2 (524~603행)
- **현상**: `save_turn(pool, ...)`, `get_conversation_history(pool, ...)` 함수가 pool을 첫 번째 인자로 받음. 프로젝트의 기존 패턴은 `get_connector_manager()`를 내부에서 호출하여 커넥터에 접근(예: 노드 함수에서 `manager = get_connector_manager()`).
- **영향**: 호출부(runner.py, main.py)마다 `manager.dc_pool`을 명시적으로 전달해야 하며, 테스트 시 pool mock 주입이 필요.
- **해결 제안**: (1) 프로젝트 패턴을 따라 내부에서 `get_connector_manager().dc_pool`로 접근하거나, (2) pool 직접 전달을 유지하되 설계 근거(테스트 용이성, DI)를 문서에 명시. 현재 설계문서 1-3에서도 "일관성을 위해" ConnectorManager를 사용한다고 명시하므로, 함수 시그니처도 이에 맞추는 것이 일관적.

### Y-14. `save_turn()`이 runner.py에서 직접 호출 — 계층 위반 가능성

- **위치**: 설계문서 5.1 1-4 (656~743행)
- **현상**: `runner.py`(파이프라인 실행 계층)에서 `save_turn()` (이력 저장 계층)을 직접 호출. runner.py의 현재 역할은 "sanitize + interrupt 감지 + ainvoke + 결과 조립"이며, 이력 저장은 관심사에 포함되지 않음.
- **영향**: runner.py의 책임 범위가 확대되어 단일 책임 원칙 위반. 에러 핸들링이 복잡해짐(save_turn 실패 시 파이프라인 결과 반환에 영향).
- **해결 제안**: (1) main.py에서 `run_pipeline()` 호출 후 턴 저장을 수행(현재 `store.append_history()`가 main.py에 있는 것과 동일 패턴), 또는 (2) runner.py에서 호출하되 `try/except`로 감싸 저장 실패가 파이프라인 결과에 영향을 주지 않도록 방어.

---

## D. 영향도 분석

### Y-15. 변경 매트릭스 누락 파일

- **위치**: 설계문서 6 (977~990행)
- **현상**: 다음 파일이 매트릭스에서 누락됨:
  - `src/utils/tracker/callback_handler.py` — `elapsed_ms`, `last_node_name`, `run_id` 프로퍼티 추가 필요 (R-01, R-02, R-03)
  - `src/services/session/__init__.py` — `get_session_store` 팩토리 수정 또는 `turn_text_store` re-export
  - `src/models/enums.py` — `turn_type` CHECK 제약(`'normal'`, `'clarification'`, `'error'`)에 대응하는 StrEnum 추가 검토. DDL의 `status` CHECK(`'success'`, `'failure'`, `'cancelled'`, `'timeout'`)도 Enum화 대상.
  - `.env.example` / `.env` — `dc_pool` 관련 설정이 필요한지 확인 (현재 `history_db`를 공유하므로 추가 설정 불필요할 수 있으나 pool_min/pool_max 설정은 config.py에 추가 필요 가능성)
- **영향**: Phase 1 구현 시 예상치 못한 추가 변경 발생
- **해결**: 매트릭스에 위 파일 추가.

### G-16. REST API(`/api/query`) 엔드포인트 변경 누락

- **위치**: 설계문서 전체
- **현상**: main.py의 `query_endpoint()`도 `store.append_history()`를 호출하므로 동일한 전환 작업이 필요하지만, 설계문서가 WebSocket 핸들러만 언급.
- **영향**: REST API 경로에서 이력이 이중 저장되거나 전환이 누락
- **해결**: `/api/query` 엔드포인트의 변경도 명시.

---

## E. 죽은 코드 / 정리 계획

### Y-17. Phase 3 SessionStore 축소 범위 불명확

- **위치**: 설계문서 5.3 (823~847행)
- **현상**: 현재 `SessionStore` (ABC)의 메서드 목록과 Phase 3 정리 대상의 매핑이 불명확.

  **현재 SessionStore 메서드:**
  | 메서드 | Phase 3 처리 | 비고 |
  |--------|-------------|------|
  | `get_history()` | 3-1에서 turn_texts로 대체 | 명확 |
  | `append_history()` | 3-2에서 제거 (체크포인터가 자동 저장) | **주의**: 체크포인터는 PipelineState를 저장하지 대화 텍스트를 저장하지 않음. turn_texts의 save_turn()이 대체해야 함 |
  | `get_clarification()` | 이미 deprecated | 3-3에서 완전 제거 |
  | `set_clarification()` | 이미 deprecated | 3-3에서 완전 제거 |
  | `ensure_session()` | 3-5에서 SessionIndex로 이관 | session_index upsert로 대체 |
  | `clear_session()` | 유지 또는 재설계 필요 | `/reset` 명령 지원. turn_texts에서 DELETE? 감사 테이블이므로 DELETE 불가(설계문서 3.1: "DELETE/UPDATE 미부여") → 논리 삭제 필요? |
  | `connect()` / `disconnect()` | 축소 | dc_pool이 대체 |
  | `health_check()` | 제거 또는 dc_pool health_check로 대체 | |

- **영향**: `clear_session()`의 `/reset` 기능이 감사 불변성 원칙과 충돌. 현재 SessionStore는 메모리/Redis에서 단순 삭제하지만, PostgreSQL turn_texts는 DELETE 권한이 없으므로 `/reset` 시 체크포인터 상태만 초기화하고 turn_texts는 보존해야 함.
- **해결**: `/reset` 동작을 재정의하고, Phase 3 단계별 삭제/유지 메서드를 명시적으로 표로 정리.

### Y-18. `_handle_slash_command` `/history` 전환 미언급

- **위치**: main.py 284~314행
- **현상**: `/history` 명령이 `store.get_history()`를 호출하여 대화 이력을 표시. Phase 3에서 이 경로도 `get_conversation_history()`로 전환해야 하지만 설계문서에 미언급.
- **영향**: `/history` 명령이 전환 후에도 구 SessionStore를 참조
- **해결**: Phase 3 단계 3-1에 `/history` 명령 전환을 명시적으로 추가.

### G-19. `HistoryEntryType` enum 전환

- **위치**: `src/services/session/store.py:35-41`, main.py에서 import
- **현상**: main.py가 `HistoryEntryType.QUERY`, `RESPONSE`, `CLARIFICATION`을 사용. Phase 3에서 `append_history()` 제거 시 이 enum의 사용처도 제거 대상. 단, turn_texts의 `turn_type` 컬럼이 유사한 역할을 하므로, 이 enum을 turn_texts용으로 재활용하거나 새 enum으로 교체해야 함.
- **영향**: 낮음, 단 명확한 전환 계획이 필요
- **해결**: `HistoryEntryType`을 `models/enums.py`로 이동하고 `turn_type`과 통일.

---

## 종합 이슈 카운트

| 등급 | 건수 | 핵심 |
|------|------|------|
| Critical (R) | 4건 | handler API 미존재(R-01~03), settings 경로 오류(R-04) |
| Warning (Y) | 11건 | 변수 섀도잉(Y-05), pool 이중관리(Y-06), WebSocket 객체(Y-08), 패턴 불일치(Y-13~14), 매트릭스 누락(Y-15~16), SessionStore 축소(Y-17~18) |
| Info (G) | 4건 | 인증 미구현(G-09), Phase 일관성 양호(G-12), REST 누락(G-16), enum 전환(G-19) |

---

## 권장 액션

1. **즉시 수정 (Phase 1 차단)**: R-01~R-04 해결 -- `DataCopilotCallbackHandler`에 `elapsed_ms`, `last_node_name`, `run_id` 프로퍼티 추가, `settings.llm.model_id` -> `settings.llm_model` 수정
2. **설계 재검토**: Y-06 (dc_pool 이중 관리), Y-14 (save_turn 호출 위치)
3. **문서 보완**: Y-15 (변경 매트릭스 보완), Y-17 (SessionStore 축소 상세화), Y-18 (`/history` 전환)
4. **Phase 1 구현 전**: Y-05 (변수명 분리), Y-08 (WebSocket 객체 접근) 코드 예시 수정
