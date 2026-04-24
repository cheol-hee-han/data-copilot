# checkpoint_dc_turn_texts 테이블 리네이밍 설계

**작성일**: 2026-04-14
**상태**: 설계 확정 (구현 대기)
**관련**: [20260413-continue-context-carry-over-design.md](20260413-continue-context-carry-over-design.md) 의 선결 작업

---

## 1. 배경

### 1.1 문제 인식

현재 `checkpoint_dc_turn_texts` 테이블과 관련 컬럼 네이밍이 다음 세 가지 혼동을 유발한다.

| 혼동 유형 | 실제 의미 | 현재 이름이 주는 인상 |
|-----------|----------|-----------------------|
| "턴(turn)"의 단위 | **한 메시지(user 또는 assistant 1건)** | "한 왕복(user+assistant)" 으로 오독 |
| `turn_seq`의 스코프 | 세션(thread_id) 내 단조증가 | "N번째 턴" 으로 읽히지만 실제는 메시지 단위 순번 |
| `turn_id` 와 `turn_seq` 역할 구분 | turn_id=전역 UUID, turn_seq=세션 내 순번 | 둘 다 "ID-naming" 이라 어느 것이 PK / 외부키인지 불명 |

### 1.2 업계 관례 재확인

- OpenAI, Anthropic, LangChain 생태계 모두 단일 발화 단위를 **"message"** 로 지칭
- LangGraph checkpointer 공식 테이블(`checkpoints`, `checkpoint_writes` 등)은 전부 `*_id` 접미사 사용 (UUID라도 `_uuid` 아님)
- 그러나 **같은 테이블 안에 PK 구성 컬럼과 외부 참조 UUID가 공존**할 때는 `_id`/`_uuid` 분리로 역할 구분이 일반적 (GitHub, GitLab 등)

### 1.3 선결 조건

[20260413-continue-context-carry-over-design.md](20260413-continue-context-carry-over-design.md) 에서 CONTINUE 질의 타깃 턴 지목 로직이 `message_seq` 기반으로 설계된다. 리네이밍을 먼저 적용해야 CONTINUE 설계 문서·코드·테스트가 처음부터 일관된 이름으로 작성된다.

---

## 2. 확정 매핑

### 2.1 테이블

| Before | After |
|--------|-------|
| `checkpoint_dc_turn_texts` | `checkpoint_dc_messages` |

### 2.2 컬럼

| Before | After | 타입 | 역할 |
|--------|-------|------|------|
| `thread_id` | `thread_id` *(유지)* | TEXT | LangGraph checkpointer 규약 |
| `turn_seq` | **`seq`** | SMALLINT | 세션 내 순번 (PK 구성 / 정렬 속성) |
| `turn_id` | **`message_uuid`** | UUID | 전역 유일 외부 참조 키 (API/UI 노출용) |
| `turn_type` | **`message_type`** | TEXT | 메시지 유형 (`normal` / `clarification` / `error`) |

**컬럼명 결정 근거**:
- `seq` 단독: 테이블 컨텍스트(messages)에서 "순번 속성"임이 자명. `message_seq` 로 prefix 중복 피하고 `message_uuid` 와의 "identity-naming" 충돌 회피
- `message_uuid`: 타입이 이름에 드러나 `seq`(SMALLINT) 와의 역할 구분 명확. GitHub/GitLab 의 dual-identifier 패턴
- `message_type`: `type` 단독은 Python 빌트인 충돌 위험, prefix 부여로 안전

### 2.3 인덱스

| Before | After |
|--------|-------|
| `idx_turn_texts_turn_id` | `idx_messages_message_uuid` |
| `idx_turn_texts_thread_created` | `idx_messages_thread_created` |
| `idx_turn_texts_status_created` | `idx_messages_status_created` |
| `idx_turn_texts_request_id` | `idx_messages_request_id` |
| `idx_turn_texts_liked` | `idx_messages_liked` |

### 2.4 파티션 자식 테이블

| Before | After |
|--------|-------|
| `checkpoint_dc_turn_texts_YYYYMM` | `checkpoint_dc_messages_YYYYMM` |

각 월별 파티션(`_202604`, `_202605`, …) 개별 rename.

### 2.5 PK

```sql
PRIMARY KEY (thread_id, seq, base_ymd)
```

(컬럼명만 자동 반영, 구조는 동일)

---

## 3. 리네이밍 대상에서 **제외되는** 항목 (혼동 방지)

다음은 이름이 비슷하나 **별개 개념**이므로 이번 리네이밍에서 손대지 않는다.

### 3.1 `state.turn_id` (파이프라인 내부 UUID)

- 정의: [src/agents/state/state.py:744](src/agents/state/state.py#L744) — `turn_id: str = ""`
- 생성: [src/agents/graph/runner.py:306](src/agents/graph/runner.py#L306) — `turn_id=str(uuid.uuid4())`
- 용도: 파이프라인 실행 중 내부 추적용. 명확화 신호 필터링, trace 파일명 등
- DB `checkpoint_dc_turn_texts.turn_id` 와는 **완전히 다른 값**

**영향 파일 (참고용 — 변경하지 않음)**:
- [src/agents/utils/clarification_context.py:41,55,73](src/agents/utils/clarification_context.py#L41)
- [src/services/process_summary_builder.py:185](src/services/process_summary_builder.py#L185)
- [src/utils/tracker/callback_handler.py:761,787](src/utils/tracker/callback_handler.py#L761)

### 3.2 `cancel_store` / `active_run_store` 의 `turn_id` 파라미터

- [src/services/cancel_store.py](src/services/cancel_store.py)
- [src/services/active_run_store.py](src/services/active_run_store.py)
- 파이프라인 실행·취소 추적용으로 `state.turn_id` 계열 사용
- DB turn_id 와 무관

### 3.3 `previous_cancel_turn_id`

- [src/agents/graph/runner.py:184,191,195,206,212,237,245,258,262](src/agents/graph/runner.py#L184)
- 취소된 이전 턴의 state 식별자

### 3.4 PipelineResult.turn_id / user_turn_id (**변경 대상**)

- [src/agents/graph/runner.py:472-473](src/agents/graph/runner.py#L472-L473) — `pipeline_result.turn_id = _assistant_turn_id`
- **이 값은 DB에서 받은 UUID** (save_turn RETURNING)
- `state.turn_id` 와 이름이 겹쳐 혼동 소지가 있으나 실제로는 DB값 파생 → **리네이밍 대상**

**확정 매핑**:

- `PipelineResult.turn_id` → `PipelineResult.message_uuid`
- `PipelineResult.user_turn_id` → `PipelineResult.user_message_uuid`

### 3.5 `turn_id` 토큰 전수 분류 (의미 기반)

`turn_id` 문자열은 코드 전역에서 **5개 의미**로 사용된다. 라인 번호는 stale 위험이 있으므로 **의미 단위 판단 규칙**을 따른다.

#### 의미 ① `state.turn_id` — 파이프라인 실행 단위 UUID (**유지**)

- **정의**: `AgenticCoreState.turn_id: str` ([state.py:744](src/agents/state/state.py#L744))
- **생성**: runner 진입 시 `str(uuid.uuid4())` ([runner.py:306](src/agents/graph/runner.py#L306))
- **용도**: 파이프라인 1회 실행을 유일 식별 — 명확화 시그널 필터링 / 취소 매칭 / trace 파일명 / 구조화 로깅
- **수명**: 단일 runner 실행 동안만 유효. **DB에 저장되지 않음**
- **왜 유지**: DB 컬럼(`message_uuid`)과 완전히 별개 개념. 이름만 같을 뿐 서로 다른 UUID
- **식별 시그니처**:
  - `state.turn_id`, `raw_state["turn_id"]` 접근
  - 함수 파라미터로 `session_id` 와 **짝을 이루어** 전달 (`def fn(session_id: str, turn_id: str)`)
  - `cancel_store` / `active_run_store` / `check_cancel` 의 인자
  - `clarification_context` / `pipeline` 의 시그널 필터링 기준
  - `callback_handler.save(turn_id=...)` 의 trace 파일명 재료
- **적용 파일·라인** (2026-04-14 grep 스냅샷):

| 파일 | 라인 | 역할 |
|------|------|------|
| `state/state.py` | 744 | 필드 정의 |
| `graph/runner.py` | 97, 306, 329, 331, 332, 534, 536, 537 | state 생성·callback_handler 전달·재진입 trace |
| `graph/cancel.py` | 44, 49, 54, 55, 56, 58, 59, 99 | cancel 매칭 키 |
| `graph/active_run.py` | 37, 47, 56, 59, 65 | active 래퍼 |
| `graph/pipeline.py` | 358, 363 | 시그널 필터 |
| `utils/clarification_context.py` | 13, 39, 41, 44, 54, 55, 72, 73, 88 | 시그널 필터 |
| `nodes/interpret/intent_classifier.py` | 178, 276 | state.turn_id 전달 |
| `nodes/interpret/query_normalizer.py` | 168 | 동일 |
| `nodes/interpret/clarification_handler.py` | 139 | 시그널 생성 시 state.turn_id 기입 |
| `nodes/reason/context_interpreter.py` | 131, 395, 423, 508, 525, 528 | check_cancel 전달 |
| `nodes/reason/result_finalizer.py` | 90 | cancel 체크 |
| `nodes/reason/sql_generator.py` | 606, 633 | 동일 |
| `nodes/reason/sql_validator.py` | 136 | 동일 |
| `nodes/present/analyzer.py` | 70 | 동일 |
| `services/cancel_store.py` | 22, 28, 49, 51, 53, 55, 58, 73, 84, 87, 90, 97 | 파라미터 |
| `services/active_run_store.py` | 24, 30, 47, 49, 51, 53, 54, 57, 59, 71, 89, 92, 95, 96, 98 | 파라미터 |
| `utils/tracker/callback_handler.py` | 761, 769, 787 | trace 파일명 조립 |
| `services/process_summary_builder.py` | 185, 190, 191 | 시그널 필터 |
| `routers/sessions.py` (**cancel 엔드포인트에 한함**) | 216, 235, 239, 244 | cancel_store 로 흐르는 Query 파라미터 |

#### 의미 ② `previous_cancel_turn_id` — 직전 취소된 턴의 state.turn_id (**유지**)

- **정의**: runner 가 interrupt pending 복구 시 이전 취소 대상 식별
- **용도**: 재시도 로직에서 이전 턴과 구분
- **왜 유지**: ①의 파생 개념. DB와 무관
- **적용**: `graph/runner.py` L158, 184, 191, 195, 206, 212, 237, 245, 258, 262

#### 의미 ③ DB 컬럼 `turn_id` — `checkpoint_dc_turn_texts.turn_id` (**변경 → `message_uuid`**)

- **정의**: 메시지 1건을 식별하는 UUID. `gen_random_uuid()` 기본값. `RETURNING turn_id::text` 로 `save_turn` 반환
- **용도**: DB 식별자, REST API 경로(`/turns/{turn_id}/…`), UI 좋아요·다운로드 API 호출, WebSocket `stream.end` 페이로드
- **왜 변경**: ①과 혼동 소지 + 타입(UUID) 명시 + PK 컬럼(`seq`)과의 역할 구분
- **식별 시그니처**:
  - SQL 본문의 `turn_id` 컬럼 참조
  - `RETURNING turn_id::text`
  - `%(turn_id)s::uuid` 바인딩
- **적용 파일**: `services/turn_text_store.py` L67, 99, 129, 134, 147, 200, 220, 231, 233, 245, 252, 261, 262, 265, 277, 286, 287, 289 (파일 전체가 변경 대상)

#### 의미 ④ `PipelineResult.turn_id` / `user_turn_id` / WS payload / REST path — DB 측 UUID의 전파 (**변경 → `message_uuid` / `user_message_uuid`**)

- **정의**: `save_turn` RETURNING 값을 runner 가 `pipeline_result` 에 기록, 이후 WS 페이로드로 노출 → UI 가 좋아요/다운로드 API 호출 시 사용
- **왜 변경**: ③과 직접 연결되는 값. ①과 이름만 겹칠 뿐 실제 값·수명·용도 모두 DB 측
- **식별 시그니처**:
  - `pipeline_result.turn_id`, `pipeline_result.user_turn_id`
  - `_assistant_turn_id`, `_user_turn_id` 지역변수 (save_turn 반환 저장)
  - WS payload key `"turn_id"`, `"user_turn_id"`
  - REST path `/turns/{turn_id}/(metadata|like|download)` (cancel 은 제외)
  - `TurnSummary.turn_id`, `TurnMetadataResponse.turn_id`

| 파일 | 라인 | 현재 역할 |
|------|------|----------|
| `graph/runner.py` (DB save 블록) | 345, 347, 354, 363, 364, 371, 372, 386, 388, 397, 472, 473 | `_user_turn_id`/`_assistant_turn_id` 지역변수, `pipeline_result.turn_id` 기입 |
| `main.py` | 498, 507, 508, 547, 589 | pipeline_result → WS 페이로드 |
| `services/session_service.py` | 76, 118, 121, 124, 129, 134, 138, 147, 150, 154 | DB turn_id 기반 `TurnSummary`/`TurnMetadataResponse` 생성 |
| `routers/sessions.py` (**메타/좋아요/다운로드 엔드포인트에 한함**) | 8, 9, 10, 140, 141, 143, 149, 150, 153, 160, 161, 163 | REST 경로 파라미터 (DB 측) |
| `static/embedded.html` | 2312, 2314, 2315, 2318, 2361, 2362, 2363, 2367, 2617, 2685 | WS 페이로드 키 수신 |

#### 의미 ⑤ 주석·docstring·테스트 카탈로그의 일반 명사 "턴" (**검토 제외**)

- **예시**: "같은 turn_id", "새 turn_id", "턴 단위 순번" 등 자연어 설명
- **적용**: `tests/test_cases/agentic_e2e_test_catalog.json` L164, L178 등, 일반 docstring
- **왜 제외**: 개념 설명용이며 리팩토링 대상 아님. 다만 DB 측을 설명하는 주석이라면 의미 ③/④로 재분류 필요

#### 판단 규칙 (작업자 체크리스트)

코드에서 `turn_id` 를 발견하면 다음 순서로 판단:

1. **`state.turn_id` / `raw_state["turn_id"]` / `session_id` 와 짝 파라미터 / cancel·active_run 문맥** → 의미 ① 유지
2. **`previous_cancel_` 접두사** → 의미 ② 유지
3. **SQL 본문 / `RETURNING turn_id` / `%(turn_id)s::uuid` 바인딩** → 의미 ③ 변경
4. **`pipeline_result.`, `_assistant_`, `_user_`, WS payload key, REST path `/turns/{...}/(metadata|like|download)`, `TurnSummary.`, `TurnMetadataResponse.`** → 의미 ④ 변경
5. **주석/docstring/테스트 카탈로그의 자연어** → 의미 ⑤ 건드리지 않음 (단 DB 측 설명이면 갱신)

### 3.6 고유 토큰 일괄 치환 대상 (의미 단일)

다음 토큰은 의미가 단일하므로 전역 치환 안전:

| 토큰 | 의미 | 치환 |
|------|------|------|
| `turn_text_store` | 모듈/파일명 | `message_store` |
| `turn_texts` | 테이블명 일부 | `messages` |
| `turn_seq` | DB 컬럼 | `seq` |
| `turn_type` | DB 컬럼 | `message_type` |
| `TurnSummary` | Pydantic 모델 클래스 | `MessageSummary` |
| `TurnMetadataResponse` | Pydantic 모델 클래스 | `MessageMetadataResponse` |
| `idx_turn_texts_` | 인덱스 접두사 | `idx_messages_` |

이 토큰들은 `turn_id` 와 달리 문맥 판별 불필요. Phase 1 작업 중 `grep` 1회 + 전역 치환으로 처리.

### 3.7 `turn_text_store` 참조 전수 (파일 리네임 영향)

모듈 파일명 변경 시 아래 20 지점 모두 경로 갱신:

**`turn_text_store.` 호출 (`session_service.py` 9곳)**

- `get_sessions_for_user` (L43), `get_session_title` (L65), `get_session_turns_for_ui` (L70)
- `get_turn_metadata` → `get_message_metadata` (L121)
- `toggle_like` (L134), `mark_downloaded` (L150)
- `archive_session` (L165), `unarchive_session` (L176), `update_session_title` (L188)
- 모듈 import L33 `from src.services import turn_text_store` → `from src.services import message_store`
- docstring L6

**`from src.services.turn_text_store import` (6곳)**

- `main.py` L55 — `get_conversation_history` import
- `runner.py` L115, L341, L380, L492 — `save_turn` lazy import (함수명은 의미 ④에 따라 `save_message` 로 변경)
- `runner.py` L224 — `upsert_session_index` lazy import (함수명 유지, 경로만)
- 테스트: `test_session_store_removal.py` L156, L174, L187, L202

**주석 내 `turn_text_store` 문자열 (4곳 — 단순 문자열 치환)**

- `main.py` L141
- `agents/graph/checkpointer.py` L57
- `agents/state/state.py` L581
- `connectors/manager.py` L107

---

## 4. DDL 영향

> **주의**: 본 문서의 라인 번호는 2026-04-14 기준 스냅샷. 구현 시점에 파일이 바뀌면 어긋날 수 있으므로, **함수/블록 이름 기준으로 탐색한 뒤 실제 라인을 재확인**할 것. 각 항목은 "라인 — 문맥"(예: "L116 — `save_turn()` INSERT 블록") 형태로 해석.

### 4.1 파일 수정 목록

| 파일 | 변경 유형 |
|------|-----------|
| [resources/connectors/postgres/checkpoint/01_schema_and_permissions.sql](resources/connectors/postgres/checkpoint/01_schema_and_permissions.sql) | L19 주석 (스키마 구조 테이블 목록) 갱신 |
| [resources/connectors/postgres/checkpoint/03_dc_custom_tables.sql](resources/connectors/postgres/checkpoint/03_dc_custom_tables.sql) | 테이블 정의 전면 수정 |
| [resources/connectors/postgres/checkpoint/04_partman_setup.sql](resources/connectors/postgres/checkpoint/04_partman_setup.sql) | L12, L15, L47 (`create_parent`), L66 (`UPDATE part_config WHERE`), L75 (`run_maintenance`), L86-92 (pg_cron 주석), L98-99 (검증 쿼리 주석) 전체 갱신 |

### 4.2 03_dc_custom_tables.sql 수정 라인

| 라인 | 내용 |
|------|------|
| 18 | 테이블 목록 주석 |
| 36 | `mask_pii()` 사용 예시 |
| 60-61 | 테이블 설명 주석 |
| 65-67 | `turn_seq 채번` 설명 주석 |
| 73 | `CREATE TABLE IF NOT EXISTS checkpoint_dc_turn_texts (` |
| 76 | `turn_seq` 컬럼 정의 |
| 77 | `turn_id` 컬럼 정의 |
| 89-90 | `turn_type` 컬럼 정의 |
| 116 | `(SELECT MAX(turn_seq) + 1` (미사용 주석이 있다면 정리) |
| 126 | `PRIMARY KEY (thread_id, turn_seq, base_ymd)` |
| 144 | 초기 파티션 생성 루프의 `checkpoint_dc_turn_texts_` |
| 146 | `PARTITION OF checkpoint_dc_turn_texts` |
| 154-164 | 5개 인덱스 CREATE 구문 |
| 202, 212, 222 | 마이그레이션 DO 블록 내 `table_name = 'checkpoint_dc_turn_texts'` |
| 205, 215, 225 | `ALTER TABLE checkpoint_dc_turn_texts ADD COLUMN` |
| 230 | `UPDATE checkpoint_dc_turn_texts` |
| 239-242 | 검증 쿼리 주석 |

---

## 5. Python 코드 영향

> **라인 번호 주의**: §4와 동일. 함수명·변수명 기준으로 탐색한 뒤 실제 라인을 재확인.

### 5.1 리네이밍 대상 파일

#### 5.1.1 코어 서비스 (높은 영향)

**[src/services/turn_text_store.py](src/services/turn_text_store.py)** — 파일명 자체도 `message_store.py` 로 변경 권장

| 라인 | 현재 | 변경 후 |
|------|------|---------|
| 5-17 | 모듈 docstring | 모든 `turn_*` → `message_*`, 테이블명 반영 |
| 48 | `turn_type: str = "normal"` 파라미터 | `message_type: str = "normal"` |
| 64-67 | `save_turn()` docstring | `save_message()` 로 함수명 변경 고려 |
| 77 | `"turn_type": turn_type` | `"message_type": message_type` |
| 99 | `last_turn_id` 지역변수 | `last_message_uuid` |
| 106-128 | INSERT SQL | 테이블명, 컬럼명 전면 교체 |
| 129 | `RETURNING turn_id::text` | `RETURNING message_uuid::text, seq` (CONTINUE 설계에서 seq 필요) |
| 134 | `last_turn_id = result["turn_id"]` | `last_message_uuid = result["message_uuid"]` |
| 147 | `return last_turn_id` | `return last_message_uuid` (또는 튜플 반환으로 seq 포함) |
| 154-182 | `get_conversation_history()` | SELECT에 `turn_type` → `message_type` (CONTINUE 설계에서 `seq` 추가 예정) |
| 167 | `SELECT role, content, turn_type` | `SELECT role, content, message_type, seq` |
| 170 | `ORDER BY turn_seq` | `ORDER BY seq` |
| 179 | `r["turn_type"]` | `r["message_type"]` |
| 189-215 | `get_session_turns_for_ui()` | SELECT / ORDER BY 컬럼명 교체 |
| 200-201 | `SELECT turn_id::text AS turn_id, turn_seq, role, content, turn_type, …` | `SELECT message_uuid::text AS message_uuid, seq, role, content, message_type, …` |
| 210 | `ORDER BY turn_seq` | `ORDER BY seq` |
| 218-236 | `get_turn_metadata()` → `get_message_metadata()` | 함수명, WHERE turn_id → WHERE message_uuid |
| 231 | `WHERE turn_id = %(turn_id)s::uuid` | `WHERE message_uuid = %(message_uuid)s::uuid` |
| 243-272 | `toggle_like()` | WHERE / RETURNING / 파라미터 |
| 275-292 | `mark_downloaded()` | WHERE / RETURNING / 파라미터 |

**함수명 변경 권장**:
- `save_turn` → `save_message`
- `get_turn_metadata` → `get_message_metadata`
- 파일명: `turn_text_store.py` → `message_store.py`

**[src/services/session_service.py](src/services/session_service.py)**

| 라인 | 현재 | 변경 후 |
|------|------|---------|
| 6 | docstring `turn_text_store` 모듈 참조 | `message_store` |
| 75-82 | `TurnSummary` 생성 매핑 | `MessageSummary` + 필드명 일괄 |
| 118, 121, 124 | `get_turn_metadata(pool, turn_id)` | `get_message_metadata(pool, message_uuid)` |
| 129, 134, 138 | `toggle_like(pool, turn_id, …)` | 파라미터명 변경 |
| 147, 150, 154 | `mark_downloaded(pool, turn_id)` | 파라미터명 변경 |

#### 5.1.2 모델

**[src/models/api/session_models.py](src/models/api/session_models.py)**

- `TurnSummary` → `MessageSummary` (클래스명)
- `TurnMetadataResponse` → `MessageMetadataResponse`
- 필드: `turn_id` → `message_uuid`, `turn_seq` → `seq`, `turn_type` → `message_type`
- 응답 스키마로 노출되므로 **API 계약과 동시 변경**

#### 5.1.3 라우터

**[src/routers/sessions.py](src/routers/sessions.py)**

| 라인 | 현재 | 변경 후 |
|------|------|---------|
| 8-10 | 주석 문서 `/api/turns/{turn_id}/…` | `/api/messages/{message_uuid}/…` |
| 140 | `@router.get("/turns/{turn_id}/metadata"` | `@router.get("/messages/{message_uuid}/metadata"` |
| 141, 143 | `async def get_turn_metadata(turn_id: str)` | `get_message_metadata(message_uuid: str)` |
| 149-157 | `/turns/{turn_id}/like` | `/messages/{message_uuid}/like` |
| 160-166 | `/turns/{turn_id}/download` | `/messages/{message_uuid}/download` |
| **216-244** | **cancel 엔드포인트의 `turn_id` Query 파라미터** | **state.turn_id 계열 — 변경 대상 아님 (주의)** |

#### 5.1.4 메인

**[src/main.py](src/main.py)**

| 라인 | 현재 | 변경 후 |
|------|------|---------|
| 498 | 주석 `turn_id/user_turn_id…` | `message_uuid/user_message_uuid…` |
| 507 | `"turn_id": pipeline_result.turn_id` | `"message_uuid": pipeline_result.message_uuid` |
| 508 | `"user_turn_id": pipeline_result.user_turn_id` | `"user_message_uuid": pipeline_result.user_message_uuid` |
| 547 | `"turn_id": pipeline_result.turn_id` (download 이벤트) | 동일 |
| 589 | 주석 `original_turn_id` | 정리 |
| 55 | `from src.services.turn_text_store import get_conversation_history` | `from src.services.message_store import …` |

#### 5.1.5 러너

**[src/agents/graph/runner.py](src/agents/graph/runner.py)** — **주의 깊게 분리**

| 라인 | 현재 | 변경 대상 여부 |
|------|------|----------------|
| 97 | 주석 `turn_id 가 확정되기 전 시점` | **state.turn_id 문맥 — 유지** |
| 120 | `turn_type="normal"` | **변경** → `message_type="normal"` |
| 158, 184, 191, 195, 206, 212, 237, 245, 258, 262 | `previous_cancel_turn_id` | **유지** (state 계열) |
| 306 | `turn_id=str(uuid.uuid4())` | **유지** (state.turn_id 생성) |
| 329-332 | `_turn_id = raw_state.get("turn_id", ""); handler.save(turn_id=_turn_id)` | **유지** (state → callback_handler) |
| 345, 347 | `_user_turn_id = None; _user_turn_id = await save_turn(...)` | **변경** → `_user_message_uuid = await save_message(...)` |
| 351, 358, 392, 402 | `turn_type=` 키워드 인자 | **변경** → `message_type=` |
| 354, 363-364, 371-372, 386, 388, 397 | `_assistant_turn_id`, `_user_turn_id` 변수 | **변경** → `_assistant_message_uuid`, `_user_message_uuid` |
| 472-473 | `pipeline_result.turn_id = _assistant_turn_id` | **변경** (PipelineResult 필드명) |
| 501, 508 | `turn_type="error"` | **변경** → `message_type="error"` |
| 534-537 | `_turn_id = result.get("turn_id"); handler.save(turn_id=_turn_id)` | **유지** (state.turn_id) |

**PipelineResult 모델** (위치 확인 필요 — runner.py 상단 또는 별도 모델 파일):
- `turn_id: str` → `message_uuid: str`
- `user_turn_id: str | None` → `user_message_uuid: str | None`

#### 5.1.6 시드/데브툴

**[devtools/scripts/seed_postgres.py](devtools/scripts/seed_postgres.py)**

| 라인 | 변경 |
|------|------|
| 2628, 2644 | 테이블 목록 문자열 `checkpoint_dc_turn_texts` → `checkpoint_dc_messages` |

### 5.2 함수/파일명 변경 요약

| 종류 | Before | After |
|------|--------|-------|
| 파일 | `src/services/turn_text_store.py` | `src/services/message_store.py` |
| 함수 | `save_turn` | `save_message` |
| 함수 | `get_turn_metadata` | `get_message_metadata` |
| 함수 | `get_conversation_history` | *(동일, 메시지 이력이라는 의미 유지)* |
| 함수 | `get_session_turns_for_ui` | `get_session_messages_for_ui` |
| 클래스 | `TurnSummary` | `MessageSummary` |
| 클래스 | `TurnMetadataResponse` | `MessageMetadataResponse` |

---

## 6. REST API 계약 영향

### 6.1 경로 변경

| Before | After |
|--------|-------|
| `GET  /api/turns/{turn_id}/metadata` | `GET  /api/messages/{message_uuid}/metadata` |
| `PATCH /api/turns/{turn_id}/like` | `PATCH /api/messages/{message_uuid}/like` |
| `PATCH /api/turns/{turn_id}/download` | `PATCH /api/messages/{message_uuid}/download` |

### 6.2 WebSocket 페이로드 키 변경

`stream.end` 이벤트 ([src/main.py:505-510](src/main.py#L505-L510)):

```diff
 {
   "type": "stream",
-  "turn_id": "...",
-  "user_turn_id": "...",
+  "message_uuid": "...",
+  "user_message_uuid": "...",
   "trace_files": [...]
 }
```

`download` 이벤트 ([src/main.py:547](src/main.py#L547)):

```diff
-  "turn_id": "..."
+  "message_uuid": "..."
```

### 6.3 응답 바디 필드

`MessageSummary` (구 `TurnSummary`) 필드가 각 REST 응답에 실림:
- `turn_id` → `message_uuid`
- `turn_seq` → `seq`
- `turn_type` → `message_type`

---

## 7. 프론트엔드 영향 ([static/embedded.html](static/embedded.html))

| 라인 | 현재 | 변경 후 |
|------|------|---------|
| 2312 | `/* ── Capture turn_id from legacy response ── */` | 주석 갱신 |
| 2314 | `if(data.turn_id){MS.update(msg.id,{turnId:data.turn_id});}` | `data.message_uuid` |
| 2315-2318 | `data.user_turn_id` 블록 | `data.user_message_uuid` |
| 2361 | 주석 | 갱신 |
| 2362-2367 | stream.end 블록 | `data.message_uuid`, `data.user_message_uuid` |
| 2617 | `turnId:t.turn_id\|\|null` | `turnId:t.message_uuid\|\|null` *(내부 상태 키 turnId 는 유지 가능)* |
| 2618 | `turnType:t.turn_type\|\|t.turnType\|\|null` | `turnType:t.message_type\|\|t.turnType\|\|null` |
| 2685 | `turnId:last.turn_id\|\|null, turnType:last.turn_type\|\|null` | `last.message_uuid, last.message_type` |

**내부 JS 변수명(`turnId`, `turnType`) 은 그대로 둘지 정리할지 결정 필요.** API 페이로드 키만 맞추면 최소 변경.

총 변경 지점: **11개 라인** (단일 파일)

---

## 8. 테스트 영향

### 8.1 영향 파일

**[tests/auto/unit/test_history_fixes.py](tests/auto/unit/test_history_fixes.py)**

| 라인 | 내용 |
|------|------|
| 99-106, 112-126 | `TurnSummary(turn_id=…, turn_seq=…, turn_type=…)` mock | 클래스명+필드명 일괄 변경 |
| 148-161 | mock 반환 dict `{"turn_id":…, "turn_seq":…, "turn_type":…}` | 키 이름 변경 |
| 154 | `"turn_type": "normal"` | `"message_type": "normal"` |
| 226, 237 | `turn_type="error"` | `message_type="error"` |

**[tests/auto/unit/test_session_store_removal.py](tests/auto/unit/test_session_store_removal.py)**

| 라인 | 내용 |
|------|------|
| 4, 125, 130, 144, 145 | 주석 `turn_type` | 업데이트 |
| 150-152, 165-170, 198 | mock 데이터 `turn_type` | `message_type` |

**[tests/auto/unit/test_cancel.py](tests/auto/unit/test_cancel.py)**

- **이 파일의 `turn_id` 는 cancel_store 의 state.turn_id 계열 — 변경 대상 아님**

### 8.2 전수 검증 체크리스트

Phase 1 구현 완료 후 아래 명령이 **예상 히트 외 추가 매치 없음**을 확인:

```bash
# 테스트 전역에서 DB 컬럼명·테이블명 잔존 탐지
grep -rn "turn_texts\|turn_seq\|turn_type" tests/
# 기대: §8.1 에 나열된 파일만 매치, 그 외 0건

# state.turn_id 계열(변경 제외 대상)은 그대로 남아있을 수 있음 — 필터
grep -rn "TurnSummary\|TurnMetadataResponse\|save_turn\|get_turn_metadata" tests/
# 기대: 0건 (모두 Message* / save_message / get_message_metadata 로 치환됨)

# fixtures/conftest 잔재
grep -rn "turn_texts\|turn_seq\|turn_type" tests/conftest.py tests/**/conftest.py 2>/dev/null
# 기대: 0건
```

---

## 9. 문서 영향

### 9.1 필수 업데이트 문서

| 파일 | 영향 |
|------|------|
| [docs/todo/20260405-postgres-conversation-history-design.md](docs/todo/20260405-postgres-conversation-history-design.md) | 설계 원문, 다수 언급 — 헤더에 "**리네이밍 전 문서. 최신 이름은 message-table-rename.md 참조**" 표기 |
| [docs/research/20260405-postgresql-conversation-history.md](docs/research/20260405-postgresql-conversation-history.md) | L194, 221 — 역사 문서로 두고 헤더에 주석 |
| [docs/todo/20260407-trace-download-restore-fix.md](docs/todo/20260407-trace-download-restore-fix.md) | L125-141 SQL 예시 — 갱신 |
| [docs/todo/20260413-session-store-removal.md](docs/todo/20260413-session-store-removal.md) | L13, 29, 32-33, 39-43, 153-155, 202 — 갱신 |
| [docs/todo/20260413-continue-context-carry-over-design.md](docs/todo/20260413-continue-context-carry-over-design.md) | 본 리네이밍 완료 후 새 이름으로 본문 일괄 치환 |
| [docs/todo/20260406-ui-ux-improvement-plan.md](docs/todo/20260406-ui-ux-improvement-plan.md) | L347 — 갱신 |
| [docs/reviews/ui/20260405-ui-implementation-requirements.md](docs/reviews/ui/20260405-ui-implementation-requirements.md) | L51, 53, 54, 179 — 갱신 |
| [docs/guides/env-configuration-guide.md](docs/guides/env-configuration-guide.md) | L221 — 테이블명 참조 갱신 |

### 9.2 역사 문서 취급

다음은 리네이밍 **이전** 설계/리뷰 기록이므로 원문 보존하되 상단에 아래 배너를 동일 문구로 삽입한다:

```markdown
> **용어 안내 (2026-04-14 리네이밍 이후 추가)**: 이 문서는 리네이밍 이전 용어로 작성됨.
> 최신 이름 대응:
> `checkpoint_dc_turn_texts` → `checkpoint_dc_messages`,
> `turn_seq` → `seq`,
> `turn_id` (DB 컬럼) → `message_uuid`,
> `turn_type` → `message_type`.
> 설계 근거: [20260414-message-table-rename.md](../todo/20260414-message-table-rename.md)
```

**배너 삽입 대상 파일**:

- `docs/todo/20260404-pipeline-cancel-design.md`
- `docs/todo/20260405-conversation-history-ui-design.md`
- `docs/todo/20260405-fastapi-ha-configuration.md`
- `docs/todo/20260406-code-review-action-plan.md`
- `docs/todo/20260410-active-run-registry-followups.md`
- `docs/reviews/code/20260405-conversation-history-interface-contract-report.md`
- `docs/reviews/code/20260406-full-codebase-review-summary.md`
- `docs/reviews/code/20260406-pipeline-cancel-design-review-report.md`
- `docs/reviews/code/20260406-core-pipeline-review-report.md`
- `docs/reviews/code/20260406-present-layer-code-review-report.md`
- `docs/reviews/code/20260406-present-layer-issue-verification-report.md`

**추가 검증**: 배너 삽입 후 `grep -rn "turn_texts\|turn_seq\|turn_type" docs/` 로 잔존 파일 확인 필요 (본 목록은 2026-04-14 grep 기준, 이후 문서 추가될 경우 누락 가능).

---

## 10. 마이그레이션 스크립트

### 10.1 DDL ALTER (비파괴적, 멱등)

다음을 신규 마이그레이션 SQL 파일로 작성:
`resources/connectors/postgres/checkpoint/05_rename_turn_to_message.sql`

```sql
-- ============================================================================
-- 05. checkpoint_dc_turn_texts → checkpoint_dc_messages 리네이밍
-- ============================================================================
-- 실행 대상: history_db / BDPTBL 스키마 / BDPETL 계정
-- 설계 근거: docs/todo/20260414-message-table-rename.md
-- 멱등성: IF 체크로 재실행 안전
-- ============================================================================

SET search_path TO BDPTBL, public;

-- ──────────────────────────────────────────────────────────────
-- 1. 테이블 리네이밍 (부모 파티션 테이블)
-- ──────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname = 'bdptbl'
          AND tablename = 'checkpoint_dc_turn_texts'
    ) THEN
        ALTER TABLE checkpoint_dc_turn_texts RENAME TO checkpoint_dc_messages;
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 2. 컬럼 리네이밍 (파티션 자동 전파)
-- ──────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'turn_seq'
    ) THEN
        ALTER TABLE checkpoint_dc_messages RENAME COLUMN turn_seq TO seq;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'turn_id'
    ) THEN
        ALTER TABLE checkpoint_dc_messages RENAME COLUMN turn_id TO message_uuid;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'bdptbl'
          AND table_name = 'checkpoint_dc_messages'
          AND column_name = 'turn_type'
    ) THEN
        ALTER TABLE checkpoint_dc_messages RENAME COLUMN turn_type TO message_type;
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 3. 인덱스 리네이밍
-- ──────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_turn_id')
        THEN ALTER INDEX idx_turn_texts_turn_id RENAME TO idx_messages_message_uuid; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_thread_created')
        THEN ALTER INDEX idx_turn_texts_thread_created RENAME TO idx_messages_thread_created; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_status_created')
        THEN ALTER INDEX idx_turn_texts_status_created RENAME TO idx_messages_status_created; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_request_id')
        THEN ALTER INDEX idx_turn_texts_request_id RENAME TO idx_messages_request_id; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_turn_texts_liked')
        THEN ALTER INDEX idx_turn_texts_liked RENAME TO idx_messages_liked; END IF;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 4. 파티션 자식 테이블 리네이밍
-- ──────────────────────────────────────────────────────────────
DO $$
DECLARE
    r RECORD;
    new_name TEXT;
BEGIN
    FOR r IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'bdptbl'
          AND tablename LIKE 'checkpoint_dc_turn_texts_%'
    LOOP
        new_name := replace(r.tablename, 'checkpoint_dc_turn_texts_', 'checkpoint_dc_messages_');
        EXECUTE format('ALTER TABLE %I RENAME TO %I', r.tablename, new_name);
    END LOOP;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 4b. 파티션 자식 인덱스·PK 제약 이름 리네이밍
-- ──────────────────────────────────────────────────────────────
-- Postgres 는 파티션 부모의 PK/인덱스를 자식 파티션마다 자동 복제하나,
-- 자식 테이블을 RENAME TO 해도 PK 인덱스 이름(`..._pkey`)과 상속된 로컬 인덱스
-- 이름(`idx_turn_texts_*_YYYYMM`)은 자동 변경되지 않음. pg_dump/복원·디버깅 시
-- 일관성을 위해 일괄 rename.
DO $$
DECLARE
    r RECORD;
    new_name TEXT;
BEGIN
    -- PK 인덱스: checkpoint_dc_turn_texts_YYYYMM_pkey → checkpoint_dc_messages_YYYYMM_pkey
    FOR r IN
        SELECT c.relname AS idx_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'bdptbl'
          AND c.relkind = 'i'
          AND c.relname LIKE 'checkpoint_dc_turn_texts_%_pkey'
    LOOP
        new_name := replace(r.idx_name, 'checkpoint_dc_turn_texts_', 'checkpoint_dc_messages_');
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx_name, new_name);
    END LOOP;

    -- 상속 로컬 인덱스: idx_turn_texts_*_YYYYMM → idx_messages_*_YYYYMM
    FOR r IN
        SELECT c.relname AS idx_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'bdptbl'
          AND c.relkind = 'i'
          AND c.relname LIKE 'idx_turn_texts_%'
    LOOP
        new_name := replace(r.idx_name, 'idx_turn_texts_', 'idx_messages_');
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx_name, new_name);
    END LOOP;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 5. pg_partman 설정 업데이트 (pg_partman 사용 시)
-- ──────────────────────────────────────────────────────────────
-- 주의: 04_partman_setup.sql 에서 'BDPTBL.checkpoint_dc_turn_texts' (대문자)로
-- create_parent() 호출했고, pg_partman 은 전달된 문자열을 part_config.parent_table
-- TEXT 컬럼에 정규화 없이 저장하는 버전이 다수임. 따라서 대소문자 무관 비교 필수.
-- Phase 0 리허설에서 실제 저장값 확인:
--   SELECT parent_table FROM partman.part_config WHERE lower(parent_table) LIKE '%checkpoint_dc_turn_texts%';
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.schemata WHERE schema_name = 'partman'
    ) AND EXISTS (
        SELECT 1 FROM partman.part_config
        WHERE lower(parent_table) = 'bdptbl.checkpoint_dc_turn_texts'
    ) THEN
        UPDATE partman.part_config
        SET parent_table = 'BDPTBL.checkpoint_dc_messages'
        WHERE lower(parent_table) = 'bdptbl.checkpoint_dc_turn_texts';
    END IF;
END $$;

-- ──────────────────────────────────────────────────────────────
-- 6. 검증
-- ──────────────────────────────────────────────────────────────
-- SELECT tablename FROM pg_tables WHERE schemaname='bdptbl' AND tablename LIKE 'checkpoint_dc_%';
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name='checkpoint_dc_messages' ORDER BY ordinal_position;
-- SELECT indexname FROM pg_indexes WHERE tablename LIKE 'checkpoint_dc_messages%';
```

### 10.2 기존 DDL 파일 업데이트

`03_dc_custom_tables.sql` 원문도 새 이름으로 갱신 (신규 환경 부트스트랩용). 05 스크립트는 **기존 운영 환경 마이그레이션 전용**.

### 10.3 롤백 스크립트

`resources/connectors/postgres/checkpoint/05_rollback_message_to_turn.sql` — 10.1과 역방향 대칭. Phase 0 리허설에서 `10.1 → 10.3 → 10.1` 왕복 실행으로 검증.

```sql
-- ============================================================================
-- 05_rollback. checkpoint_dc_messages → checkpoint_dc_turn_texts 롤백
-- ============================================================================
SET search_path TO BDPTBL, public;

-- 1. 부모 테이블 rename (역방향)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='bdptbl' AND tablename='checkpoint_dc_messages') THEN
        ALTER TABLE checkpoint_dc_messages RENAME TO checkpoint_dc_turn_texts;
    END IF;
END $$;

-- 2. 컬럼 역방향
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='bdptbl' AND table_name='checkpoint_dc_turn_texts' AND column_name='seq')
        THEN ALTER TABLE checkpoint_dc_turn_texts RENAME COLUMN seq TO turn_seq; END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='bdptbl' AND table_name='checkpoint_dc_turn_texts' AND column_name='message_uuid')
        THEN ALTER TABLE checkpoint_dc_turn_texts RENAME COLUMN message_uuid TO turn_id; END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema='bdptbl' AND table_name='checkpoint_dc_turn_texts' AND column_name='message_type')
        THEN ALTER TABLE checkpoint_dc_turn_texts RENAME COLUMN message_type TO turn_type; END IF;
END $$;

-- 3. 인덱스 역방향
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_message_uuid')
        THEN ALTER INDEX idx_messages_message_uuid RENAME TO idx_turn_texts_turn_id; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_thread_created')
        THEN ALTER INDEX idx_messages_thread_created RENAME TO idx_turn_texts_thread_created; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_status_created')
        THEN ALTER INDEX idx_messages_status_created RENAME TO idx_turn_texts_status_created; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_request_id')
        THEN ALTER INDEX idx_messages_request_id RENAME TO idx_turn_texts_request_id; END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='idx_messages_liked')
        THEN ALTER INDEX idx_messages_liked RENAME TO idx_turn_texts_liked; END IF;
END $$;

-- 4. 파티션 자식 테이블 역방향
DO $$
DECLARE r RECORD; new_name TEXT;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables
             WHERE schemaname='bdptbl' AND tablename LIKE 'checkpoint_dc_messages_%'
    LOOP
        new_name := replace(r.tablename, 'checkpoint_dc_messages_', 'checkpoint_dc_turn_texts_');
        EXECUTE format('ALTER TABLE %I RENAME TO %I', r.tablename, new_name);
    END LOOP;
END $$;

-- 4b. 자식 PK/상속 인덱스 역방향
DO $$
DECLARE r RECORD; new_name TEXT;
BEGIN
    FOR r IN SELECT c.relname AS idx_name
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='bdptbl' AND c.relkind='i'
               AND c.relname LIKE 'checkpoint_dc_messages_%_pkey'
    LOOP
        new_name := replace(r.idx_name, 'checkpoint_dc_messages_', 'checkpoint_dc_turn_texts_');
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx_name, new_name);
    END LOOP;

    FOR r IN SELECT c.relname AS idx_name
             FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='bdptbl' AND c.relkind='i'
               AND c.relname LIKE 'idx_messages_%'
    LOOP
        new_name := replace(r.idx_name, 'idx_messages_', 'idx_turn_texts_');
        EXECUTE format('ALTER INDEX %I RENAME TO %I', r.idx_name, new_name);
    END LOOP;
END $$;

-- 5. partman 역방향 (대소문자 무관)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name='partman')
       AND EXISTS (SELECT 1 FROM partman.part_config
                   WHERE lower(parent_table)='bdptbl.checkpoint_dc_messages') THEN
        UPDATE partman.part_config
        SET parent_table='BDPTBL.checkpoint_dc_turn_texts'
        WHERE lower(parent_table)='bdptbl.checkpoint_dc_messages';
    END IF;
END $$;
```

---

## 11. 구현 계획 (Phase 0 ~ Phase 3)

작업 단위를 PR 또는 체크리스트 항목으로 바로 쓸 수 있게 세분화. 각 Step 마다 **완료 정의(DoD)** 명시.

### Phase 0: 사전 준비 (착수 전, 환경 검증)

| Step | 작업 | DoD |
|------|------|-----|
| 0.1 | 테스트 DB(`history_db`) 백업 `pg_dump -t bdptbl.checkpoint_dc_turn_texts history_db > backup_pre_rename.sql` | 백업 파일 존재 확인 |
| 0.2 | `SELECT parent_table FROM partman.part_config WHERE lower(parent_table) LIKE '%checkpoint_dc_turn_texts%';` 실행 | 실제 저장값(대/소문자) 기록 |
| 0.3 | §10.1 `05_rename_turn_to_message.sql` 테스트 환경에 적용 | §12.1 DB 검증 쿼리 모두 통과 |
| 0.4 | §10.3 `05_rollback_message_to_turn.sql` 적용 → 구 스키마 복원 확인 | 구 이름 쿼리 통과, 신 이름 쿼리 실패 |
| 0.5 | 0.3 재실행 → 최종 신 스키마 확정 | §12.1 통과 |
| 0.6 | 기준선 grep 건수 수집 (유지 대상 검증용) — `grep -c "state.turn_id" src/agents/`, `grep -c "previous_cancel_turn_id" src/agents/graph/runner.py` 등 수치 기록 | 숫자 기록됨 |

### Phase 1: 코드 변경 (단일 PR, 순서 중요)

| Step | 작업 | 참조 | DoD |
|------|------|------|-----|
| 1.1 | `03_dc_custom_tables.sql` 전면 갱신 | §4.2 | 구 이름 grep 0건 |
| 1.2 | `04_partman_setup.sql` 주석·`create_parent`·`UPDATE part_config` 갱신 | §4.1 | 구 이름 grep 0건 |
| 1.3 | `01_schema_and_permissions.sql` L19 주석 갱신 | §4.1 | 구 이름 grep 0건 |
| 1.4 | `05_rename_turn_to_message.sql` 신규 작성 | §10.1 | Phase 0.3 에서 사용한 것과 동일 |
| 1.5 | `05_rollback_message_to_turn.sql` 신규 작성 | §10.3 | Phase 0.4 에서 사용한 것과 동일 |
| 1.6 | `src/services/turn_text_store.py` → `message_store.py` 파일 리네임 + 내부 전면 치환 (함수명: `save_turn→save_message`, `get_turn_metadata→get_message_metadata`) | §5.1.1, §3.7 | 파일명 바뀜, 내부 구 토큰 0건 |
| 1.7 | `src/models/api/session_models.py` — `TurnSummary→MessageSummary`, `TurnMetadataResponse→MessageMetadataResponse`, 필드 3개 치환 | §5.1.2 | 클래스명 바뀜 |
| 1.8 | `src/services/session_service.py` — 호출 9곳 + import L33 + docstring L6 + 생성자 인자 L76, L124, L138, L154 | §3.7, §5.1.1 | `turn_text_store` 0건, `TurnSummary`/`TurnMetadataResponse` 0건 |
| 1.9 | `src/agents/graph/runner.py` — lazy import 5곳 (L115/224/341/380/492) + DB save 블록 변수·필드명 | §3.7, §5.1.5 | `from src.services.turn_text_store` 0건. `state.turn_id`/`previous_cancel_turn_id` 건수는 0.6 기준선과 동일 |
| 1.10 | `src/main.py` — import L55, WS 페이로드 키 L498/507/508/547/589, 주석 L141 | §3.7, §5.1.4 | 의미 ④ 토큰 교체, 의미 ① 불변 |
| 1.11 | `src/routers/sessions.py` — REST path 3개 (`/turns/{turn_id}/metadata|like|download` → `/messages/{message_uuid}/...`). cancel 엔드포인트(L216~244)는 **건드리지 않음** | §5.1.3 | `/turns/` 3건 → 0건, cancel 은 유지 |
| 1.12 | 주석 3곳 정리 — `checkpointer.py:57`, `state.py:581`, `connectors/manager.py:107` | §3.7 | 주석 내 `turn_text_store` 0건 |
| 1.13 | 프론트엔드 `static/embedded.html` 11 지점 치환 | §7 | `data.turn_id` / `t.turn_id` / `t.turn_type` 0건 (내부 JS `turnId` 변수는 결정에 따라 유지 가능) |
| 1.14 | 테스트 갱신 — `test_history_fixes.py`, `test_session_store_removal.py` (import 4곳 + mock 필드명) | §8.1 | `TurnSummary`/`save_turn`/`get_turn_metadata`/`turn_text_store` 0건 |
| 1.15 | 문서 §9.1 — 8개 문서 본문 치환 | §9.1 | 구 토큰 0건 |
| 1.16 | 문서 §9.2 — 11개 역사 문서 상단 배너 삽입 | §9.2 | 각 파일 헤드에 배너 존재 |
| 1.17 | `uv run mypy src/` | — | 신규 오류 0건 |
| 1.18 | `uv run ruff check src/ tests/` | — | 신규 경고 0건 |
| 1.19 | `uv run pytest tests/auto/unit/ -x` | — | 전체 통과 |
| 1.20 | `uv run pytest tests/auto/integration/ -x` | — | 전체 통과 |
| 1.21 | **§12.4 Step 1-8 실행** (실수 방지 검증) | §12.4 | 모든 기대 결과 충족 |
| 1.22 | PR 올리기 + §12.4 Step 10 라벨링 | — | 리뷰어 4-Eyes 통과 |

### Phase 2: 배포

| Step | 작업 | DoD |
|------|------|-----|
| 2.1 | 운영(또는 대상) DB 백업 | 백업 파일 존재 |
| 2.2 | `psql -f 05_rename_turn_to_message.sql` 실행 | §12.1 DB 검증 쿼리 통과 |
| 2.3 | 애플리케이션 배포 (백엔드+프론트 동시) | 기동 성공, 500 에러 없음 |
| 2.4 | §12.2, §12.3 스모크 | 신규 세션 생성→질의→좋아요→다운로드→명확화 전부 OK |

### Phase 3: 사후 확인

| Step | 작업 | DoD |
|------|------|-----|
| 3.1 | §12.4 Step 1~8 재실행 (배포 환경에서) | 전부 통과 |
| 3.2 | 파티션 자식 테이블 이름 확인 — `SELECT tablename FROM pg_tables WHERE tablename LIKE 'checkpoint_dc_messages_%'` | 전부 새 규약 |
| 3.3 | partman 다음 월 신규 파티션 생성 모니터링 | 새 이름으로 생성됨 |
| 3.4 | 로그·Trace 에서 구 이름 참조 없는지 모니터링 (1주) | 매치 0건 |
| 3.5 | 본 문서 `docs/archive/` 로 이동, 상태를 **"완료"** 로 표기 | 이동됨 |
| 3.6 | CONTINUE 설계 문서(`20260413-continue-context-carry-over-design.md`) 본문 신 이름으로 일괄 치환 | 구 토큰 0건 |
| 3.7 | `.claude/rules/code-style.md` 등에 "`turn_*` 네이밍 발견 시 리네이밍 누락 의심" 가이드 추가 (선택) | 가이드 반영 |

### 중단·롤백 기준

Phase 2 중 다음 상황 시 즉시 롤백(`05_rollback_message_to_turn.sql` + 이전 코드 버전 재배포):

- 2.3 기동 시 `relation "checkpoint_dc_turn_texts" does not exist` 에러
- 2.4 스모크 중 좋아요/다운로드/세션 복원 중 하나라도 500
- §12.1 쿼리 "구 이름 잔재 없음" 불만족

---

## 12. 검증 체크리스트

### 12.1 DB 레벨

```sql
-- 1) 테이블 존재
SELECT tablename FROM pg_tables
WHERE schemaname='bdptbl' AND tablename LIKE 'checkpoint_dc_%';
-- 기대: checkpoint_dc_messages, checkpoint_dc_messages_YYYYMM*, checkpoint_dc_session_index

-- 2) 컬럼 구성
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema='bdptbl' AND table_name='checkpoint_dc_messages'
ORDER BY ordinal_position;
-- 기대: thread_id, seq, message_uuid, role, content, ..., message_type, ...

-- 3) 구 이름 잔재 없음
SELECT column_name FROM information_schema.columns
WHERE table_schema='bdptbl' AND table_name='checkpoint_dc_messages'
  AND column_name IN ('turn_seq','turn_id','turn_type');
-- 기대: 0 rows

-- 4) 인덱스
SELECT indexname FROM pg_indexes
WHERE schemaname='bdptbl' AND tablename LIKE 'checkpoint_dc_messages%';
-- 기대: idx_messages_*

-- 5) 삽입·조회 동작
INSERT INTO checkpoint_dc_messages (thread_id, role, content, message_type)
VALUES ('test-session', 'user', 'test', 'normal');
SELECT seq, message_uuid, role, message_type FROM checkpoint_dc_messages
WHERE thread_id='test-session';
```

### 12.2 API 레벨

```bash
# 좋아요
curl -X PATCH "http://localhost:8000/api/messages/{message_uuid}/like" \
     -H "Content-Type: application/json" \
     -d '{"is_liked": true}'

# 세션 상세 응답 스키마에 seq, message_uuid, message_type 포함 확인
curl "http://localhost:8000/api/sessions/{session_id}" | jq '.turns[0] | keys'
```

### 12.3 UI 레벨

- 세션 사이드바에서 과거 세션 복원 → 메시지 전부 표시
- 좋아요 버튼 클릭 → 반영
- 다운로드 기록 → 서버에 반영
- 명확화 요청 → clarification 유형 특수 UI 활성화

### 12.4 구현 완료 후 실수 방지 검증 (PR 제출 전 필수)

아래 단계를 **순차 실행**하고 각 단계의 "기대 결과" 를 충족하지 못하면 PR 제출 금지.

#### Step 1: 고유 토큰 전역 잔재 탐지 (§3.6 대상)

의미가 단일한 토큰은 단 1건이라도 남으면 치환 누락. 소스 전역에서 0건이어야 함.

```bash
# 1-a. 코드·리소스 전역
grep -rn -E "turn_text_store|turn_texts|turn_seq|turn_type|TurnSummary|TurnMetadataResponse|idx_turn_texts" \
     src/ devtools/ tests/ resources/ static/
# 기대: 0건

# 1-b. 설정·환경 파일
grep -rn -E "turn_text_store|turn_texts|turn_seq|turn_type" \
     .env.example pyproject.toml docker-compose*.yml 2>/dev/null
# 기대: 0건
```

1건이라도 매치되면:
- §9.2 "역사 문서 배너" 대상이라면 무시 가능 (문서에 한함)
- 그 외라면 **치환 누락** → 해당 파일 수정 후 재검증

#### Step 2: `turn_id` 토큰 문맥별 검증 (§3.5 대상)

```bash
# 2-a. DB/API 측(의미 ③④)에 `turn_id` 잔재 확인
grep -rn "turn_id" src/services/turn_text_store.py \
                  src/services/session_service.py \
                  src/routers/sessions.py \
                  src/main.py \
                  static/embedded.html
# 기대: routers/sessions.py 의 cancel 엔드포인트(§3.5 의미 ①)만 남음. 그 외 0건.
```

```bash
# 2-b. state.turn_id 계열(의미 ①②)이 실수로 제거되지 않았는지 확인
grep -rn "state.turn_id\|previous_cancel_turn_id\|session_id.*turn_id.*str\|turn_id.*str.*session_id" \
     src/agents/ src/services/cancel_store.py src/services/active_run_store.py
# 기대: 리네이밍 이전과 동일 건수. 감소했다면 의미 ① 오변경.
# 리네이밍 직전 기준선을 Phase 1 Step 0 에서 수집해 둘 것.
```

#### Step 3: 파일명·import 경로 정합성

```bash
# 3-a. 구 파일명이 남지 않았는지
ls src/services/turn_text_store.py 2>/dev/null
# 기대: "No such file or directory"

ls src/services/message_store.py
# 기대: 파일 존재

# 3-b. 구 import 경로 잔재
grep -rn "from src.services.turn_text_store\|from src.services import turn_text_store" \
     src/ devtools/ tests/
# 기대: 0건
```

#### Step 4: DDL·SQL 정합성

```bash
# 4-a. SQL 파일에서 구 이름 잔재
grep -rn -E "checkpoint_dc_turn_texts|turn_seq|turn_type|turn_id" \
     resources/connectors/postgres/checkpoint/
# 기대:
#   - 05_rename_*.sql, 05_rollback_*.sql 내부 마이그레이션 SQL 자체는 매치됨 (정상)
#   - 03_dc_custom_tables.sql, 04_partman_setup.sql 은 0건
#   - 01_schema_and_permissions.sql L19 주석 0건
```

#### Step 5: 정적 분석·테스트

```bash
# 5-a. 타입·린트
uv run mypy src/
uv run ruff check src/ tests/
# 기대: 본 리네이밍으로 인한 신규 오류 0건

# 5-b. 단위 테스트 전체
uv run pytest tests/auto/unit/ -x
# 기대: 전부 통과. save_turn·turn_text_store import 오류 0건

# 5-c. 파이프라인 통합 테스트
uv run pytest tests/auto/integration/ -x
# 기대: 전부 통과
```

#### Step 6: 런타임 스모크 (로컬 기동)

```bash
# 6-a. 앱 기동
uv run uvicorn src.main:app --reload

# 6-b. 신규 세션 생성 → 질의 → 응답 흐름 (의미 ③④ 전 경로)
curl -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"user_id":"test","query":"예금 잔액 알려줘"}'
# 기대: 200 응답, 이후 WebSocket 이벤트에 message_uuid/user_message_uuid 키 존재

# 6-c. 좋아요 API (의미 ④ 전 경로)
curl -X PATCH "http://localhost:8000/api/messages/{message_uuid}/like" \
     -H "Content-Type: application/json" -d '{"is_liked": true}'
# 기대: 200, is_liked=true 반영

# 6-d. 다운로드 API
curl -X PATCH "http://localhost:8000/api/messages/{message_uuid}/download"
# 기대: 200

# 6-e. 취소 API (의미 ① 유지 검증 — state.turn_id 경로)
curl -X POST "http://localhost:8000/api/sessions/{session_id}/cancel?turn_id={state_turn_id}"
# 기대: 200, cancel_store 에 저장, 진행 중 파이프라인 interrupt
```

#### Step 7: DB 레벨 end-to-end (§12.1 재실행)

§12.1 SQL 체크리스트 전체를 **실제 데이터 1건 이상이 들어간 상태** 에서 재실행.

#### Step 8: 문서 일관성

```bash
# 8-a. 본 리네이밍 이후 작성되는 모든 신규 문서에 구 용어 차단
grep -rn -E "turn_seq|turn_type|turn_texts|checkpoint_dc_turn_texts|TurnSummary|TurnMetadataResponse" \
     docs/ \
     --include="*.md" \
     | grep -v -E "(todo/20260404|todo/20260405|todo/20260406|todo/20260407|todo/20260410|todo/20260413|todo/20260414-message-table-rename|reviews/code/20260405|reviews/code/20260406|research/20260405)"
# 기대: 0건
# (§9.2 배너 적용 대상인 역사 문서는 제외)
```

#### Step 9: 롤백 가능성 재확인

Phase 0 에서 리허설한 롤백 스크립트(§10.3)를 **실제 운영 환경에 적용하기 직전** 에 1회 더 테스트 환경에서 왕복 실행:

1. `05_rename_turn_to_message.sql` 실행
2. `05_rollback_message_to_turn.sql` 실행
3. §12.1 의 "구 이름 잔재 없음" 쿼리가 이번에는 **구 이름으로** 통과하는지 확인 (롤백 정상)
4. 다시 `05_rename_turn_to_message.sql` 실행하여 신규 스키마 확정

#### Step 10: 최종 4-Eyes 리뷰

- 본 문서 §3.5 "판단 규칙" 을 리뷰어에게 공유하고, 변경된 각 `turn_id` 라인이 "의미 ③ 또는 ④" 인지 PR 코멘트로 라벨링
- 변경하지 않은 `turn_id` 가 "의미 ① 또는 ②" 에 해당함을 샘플링 확인
- 리뷰어는 §12.4 Step 1-8 을 재현 가능해야 함

---

## 13. 잠재적 위험 및 완화

| # | 위험 | 완화 |
|---|------|------|
| 1 | `state.turn_id` 와 DB `turn_id` 혼동으로 엉뚱한 위치 변경 | 본 문서 §3 명시적 제외 리스트 참조, 코드 리뷰 시 "변경 대상 아님" 라벨 확인 |
| 2 | PipelineResult 필드명 변경 시 다수 참조처 누락 | grep `pipeline_result.turn_id` / `pipeline_result.user_turn_id` 전수 확인 |
| 3 | `cancel_pipeline(turn_id=...)` 계열 파라미터와 혼동 | §3.2, §3.3 참조. 이 라인들은 손대지 않는다 |
| 4 | 프론트엔드 내부 JS 변수명(`turnId`, `turnType`) 부분 혼재 | API 페이로드 키만 맞추고 내부 변수는 별도 정리 이슈로 분리 가능 |
| 5 | 파티션 자식 테이블 rename 누락 시 파티션 일관성 저하 | 10.1 §4 스크립트로 자동화 |
| 6 | pg_partman 설정 미업데이트 시 신규 파티션이 구 이름으로 생성 | 10.1 §5 필수 실행 |
| 6-a | `part_config.parent_table` 대소문자 불일치로 UPDATE 0건 발생 (조용한 실패) | 10.1 §5 `lower()` 비교 사용 + Phase 0 리허설에서 실제 저장값 확인 |
| 6-b | 자식 파티션 PK 인덱스(`..._pkey`)·상속 인덱스 이름이 구 규약으로 잔류 | 10.1 §4b 루프로 일괄 rename |
| 7 | `turn_text_store.py` 파일명 변경 시 import 경로 전수 갱신 필요 | grep `from src.services.turn_text_store` / `import turn_text_store` 전수 |
| 8 | 마이그레이션 실패 시 롤백 필요 | 10.3 롤백 스크립트 + Phase 0 리허설 |
| 9 | 문서에 남은 구 이름이 CONTINUE 설계 혼란 유발 | §9 문서 업데이트를 Phase 1 에 포함 |
| 10 | 외부 연동 시스템(BI, 대시보드 등) 이 구 컬럼명에 의존 | 사전 확인 필요. 본 프로젝트는 해당 없을 가능성 높음 |

---

## 14. 작업 완료 후 정리

- 본 문서: `docs/todo/` 에서 `docs/archive/` 로 이동 (또는 상태를 "완료"로 표기)
- 05번 마이그레이션 SQL: 실행 완료 후에도 운영 환경에서는 이미 적용됨 → 신규 환경에는 03번 갱신본이 적용되므로 05번은 보관용
- CONTINUE 설계 문서: 새 이름 기준으로 본문 정리 완료 확인
- 코드 리뷰 가이드라인에 "`turn_*` 네이밍 발견 시 리네이밍 누락 의심" 항목 추가

---

## 15. 변경 파일 요약

### 코드 (수정)

| 유형 | 파일 수 | 대표 경로 |
|------|---------|-----------|
| DDL | 2 | `03_dc_custom_tables.sql`, `04_partman_setup.sql` |
| DDL (신규) | 1-2 | `05_rename_turn_to_message.sql` (+ 롤백) |
| Python 코어 | 6 | `src/services/turn_text_store.py` (rename), `session_service.py`, `routers/sessions.py`, `models/api/session_models.py`, `agents/graph/runner.py`, `main.py` |
| Python 데브툴 | 1 | `devtools/scripts/seed_postgres.py` |
| 프론트엔드 | 1 | `static/embedded.html` |
| 테스트 | 2 | `test_history_fixes.py`, `test_session_store_removal.py` |
| 문서 | ~10 | `docs/todo/`, `docs/research/`, `docs/reviews/`, `docs/guides/` |

### 코드 (손대지 않음 — 혼동 방지용으로 명시)

| 경로 | 사유 |
|------|------|
| `src/agents/state/state.py` L744 `turn_id` 필드 | 파이프라인 내부 UUID, DB와 무관 |
| `src/agents/utils/clarification_context.py` | state.turn_id 기반 필터링 |
| `src/services/process_summary_builder.py` | state.turn_id 참조 |
| `src/utils/tracker/callback_handler.py` | state.turn_id 로 trace 파일명 생성 |
| `src/services/cancel_store.py` | state.turn_id 계열 파라미터 |
| `src/services/active_run_store.py` | state.turn_id 계열 파라미터 |
| `tests/auto/unit/test_cancel.py` | cancel_store 대상 |
| `src/routers/sessions.py` L216-244 cancel 엔드포인트 | cancel_store state.turn_id 사용 |
