# Pipeline Cancel 구현 코드 리뷰 보고서

> 검토일: 2026-04-06
> 검토 범위: cancel 기능 신규/수정 파일 14개
> 검토 관점: 보안, 동시성, 에러 처리, 타입 안전성, 정합성, 명명, 테스트, 성능, 누락

---

## Critical (빨간색 원)

### CR-01. cancel API 인증/인가 부재 — 타 세션 cancel 가능

- **파일**: `src/routers/sessions.py:134-161`
- **현상**: `POST /sessions/{session_id}/cancel`에 인증 미들웨어가 없다. session_id만 알면 다른 사용자의 파이프라인을 취소할 수 있다.
- **위험**: 악의적 사용자가 UUID를 추측하거나 brute-force로 타 세션을 방해할 수 있다.
- **개선안**: 최소한 WebSocket 세션 소유자 검증 또는 토큰 기반 인증을 추가한다. 기존 REST 엔드포인트도 인증이 없지만, cancel은 파괴적 행위이므로 우선 적용이 필요하다.

### CR-02. RedisCancelStore에서 GETDEL의 Redis 버전 호환성

- **파일**: `src/services/cancel_store.py:82`
- **현상**: `GETDEL`은 Redis 6.2.0+ 전용 명령어이다. 폐쇄망 환경의 Redis 버전이 6.2 미만이면 런타임 오류가 발생한다.
- **위험**: 폐쇄망 배포 시 Redis 버전 미충족으로 cancel 기능 전체가 동작 불가.
- **개선안**: `GET` + `DEL`을 Lua 스크립트 또는 파이프라인으로 원자적 수행하거나, 최소 Redis 버전 요구사항을 문서화한다.

### CR-03. RedisCancelStore의 pop_cancel 비원자성 (경쟁 조건)

- **파일**: `src/services/cancel_store.py:80-85`
- **현상**: `GETDEL` 자체는 원자적이지만, 만약 CR-02 때문에 `GET` + `DEL`로 변경하면 경쟁 상태가 발생한다. 또한 `is_cancelled`와 `pop_cancel`이 서로 다른 시점에 호출되어, `is_cancelled`가 True를 반환한 후 `pop_cancel` 전에 다른 요청이 플래그를 소비할 수 있다.
- **위험**: 두 워커가 동일 세션의 cancel 플래그를 동시 소비하면 한쪽은 cancel 미인식.
- **개선안**: `is_cancelled` + `pop_cancel`을 Lua 스크립트로 원자적으로 결합하거나, 현재 아키텍처에서 한 세션은 한 워커에서만 처리됨을 명시적으로 문서화한다.

### CR-04. main.py에서 RedisSessionStore 내부 속성(_client) 직접 접근

- **파일**: `src/main.py:148`
- **현상**: `store._client`로 private 속성에 직접 접근하여 RedisCancelStore를 생성한다. 캡슐화 위반이며, RedisSessionStore 내부 구현이 변경되면 즉시 깨진다.
- **위험**: 유지보수 시 의도치 않은 파손. `_client`가 `None`일 수 있는 시점에 접근하면 TypeError.
- **개선안**: RedisSessionStore에 `get_redis_client()` 같은 public 메서드를 추가하거나, CancelStore 초기화를 별도 팩토리 함수로 분리한다.

---

## Warning (노란색 원)

### WR-01. recovery_agent 노드에 cancel 체크 누락

- **파일**: `src/agents/nodes/reason/recovery_agent.py`
- **현상**: context_retriever, readiness_gate, sql_generator, sql_executor에는 cancel 체크가 있지만 recovery_agent에는 없다. recovery_agent는 LLM 호출(재계획)을 수행하므로 수 초 소요될 수 있다.
- **영향**: cancel 요청 후에도 recovery_agent의 LLM 호출이 완료될 때까지 취소가 지연된다.
- **개선안**: recovery_agent_node 시작부에 동일한 `check_cancel` + `make_cancel_updates` 패턴을 추가한다.

### WR-02. context_interpreter 노드에 cancel 체크 누락

- **파일**: `src/agents/nodes/reason/context_interpreter.py`
- **현상**: context_interpreter는 LLM 배치 해석을 수행하는 노드로, LLM 호출 비용이 발생하지만 cancel 체크가 없다.
- **영향**: 불필요한 LLM 호출 비용과 지연.
- **개선안**: 시작부에 cancel 체크 추가.

### WR-03. _route_after_sql_generator에서 CANCELLED 체크 누락

- **파일**: `src/agents/graph/pipeline.py:189-203`
- **현상**: sql_generator 노드가 cancel로 `make_cancel_updates`를 반환하면 `status=CANCELLED`, `failure_type=None`이 아닌 상태가 된다. `error_message`가 설정되므로 `_route_after_sql_generator`에서 `failure_type`만 확인하고 `sql_validator`로 라우팅될 가능성이 있다.
- **영향**: cancel 후 불필요하게 sql_validator가 실행될 수 있다.
- **개선안**: `_route_after_sql_generator` 첫 줄에 `if state.status == QueryStatus.CANCELLED: return "conclude_failure"` 추가. `_route_after_readiness_gate`와 동일 패턴.

### WR-04. sql_executor의 cancel 반환이 다른 노드와 비일관

- **파일**: `src/agents/nodes/present/sql_executor.py:47-58`
- **현상**: 다른 노드(context_retriever, readiness_gate, sql_generator)는 `make_cancel_updates()`를 사용하여 `reason` state를 포함한 일관된 반환을 하지만, sql_executor는 자체적으로 `formatted_response`와 `status`만 반환한다. `reason.final_status`가 CANCELLED로 설정되지 않는다.
- **영향**: result_finalizer에서 CANCELLED 분기 조건(`state.status == QueryStatus.CANCELLED`)은 통과하지만, reason 계층의 상태가 불완전하다.
- **개선안**: sql_executor도 `make_cancel_updates` 패턴을 사용하거나, 최소한 `reason` state를 업데이트하여 정합성을 유지한다. 다만 sql_executor는 present 계층이므로 부분 결과(생성된 SQL) 포함이 의도적이라면 주석으로 명시한다.

### WR-05. RedisCancelStore.redis_client 타입 힌트 누락

- **파일**: `src/services/cancel_store.py:58`
- **현상**: `__init__(self, redis_client)` 에 타입 힌트가 없다. 코드 스타일 규칙("타입 힌트 필수")에 위반된다.
- **개선안**: `redis_client: redis.asyncio.Redis` 또는 적절한 프로토콜 타입으로 힌트를 추가한다.

### WR-06. check_cancel에서 와일드카드("*") 이중 조회 오버헤드

- **파일**: `src/agents/graph/cancel.py:46-49`
- **현상**: 매 cancel 체크마다 `is_cancelled(session_id, turn_id)` + `is_cancelled(session_id, "*")` 2회 Redis GET이 발생한다. 파이프라인 노드 진입마다 호출되므로 1회 실행 당 8-10회 Redis 조회가 추가된다.
- **영향**: Redis 부하 증가. 특히 다수 세션 동시 실행 시.
- **개선안**: Lua 스크립트로 한 번에 turn_id 매칭 + 와일드카드 매칭을 수행하거나, 와일드카드 모드에서는 turn_id 체크를 스킵한다. 또는 단일 `GET`으로 값을 가져와서 Python에서 `turn_id` 또는 `"*"` 매칭을 수행한다.

### WR-07. clear_cancel 함수가 사용되지 않음 (dead code)

- **파일**: `src/agents/graph/cancel.py:55-61`
- **현상**: `clear_cancel` 함수가 정의되어 있지만 프로젝트 전체에서 호출하는 곳이 없다. runner.py에서는 `pop_cancel`만 사용한다.
- **영향**: 죽은 코드. API로 제공되었지만 내부에서 사용하지 않아 혼란을 초래할 수 있다.
- **개선안**: 현재 사용하지 않으면 제거하거나, 향후 사용 계획이 있다면 주석으로 용도를 명시한다. `pop_cancel`이 원자적 대안이므로 `clear_cancel`은 불필요할 가능성이 높다.

### WR-08. result_finalizer에서 CANCELLED 판정 기준이 state.status

- **파일**: `src/agents/nodes/reason/result_finalizer.py:48`
- **현상**: `if state.status == QueryStatus.CANCELLED` 로 판정하는데, 이전 노드(context_retriever 등)에서 `make_cancel_updates`가 `status: QueryStatus.CANCELLED`를 반환한다. LangGraph의 상태 병합(reducer)이 이 값을 올바르게 설정하는지 확인이 필요하다.
- **검증 필요**: PipelineState의 `status` 필드 reducer가 마지막 반환값으로 덮어쓰는지 확인. 만약 reducer가 append 방식이면 CANCELLED가 유실될 수 있다.

### WR-09. cancel 후 interrupt 무시 시 체크포인터 상태 정합성

- **파일**: `src/agents/graph/runner.py:163-170`
- **현상**: interrupt 대기 중 cancel이 감지되면 `is_interrupt_pending = False`로 설정하여 새 턴을 시작한다. 그러나 체크포인터에 이전 interrupt 상태가 남아있어, 다음 턴 진입 시 `aget_state`에서 여전히 `next`가 존재할 수 있다.
- **영향**: cancel 후 새 질의를 보내면 이전 interrupt 상태와 충돌할 가능성.
- **개선안**: cancel 감지 시 `app.aupdate_state`로 체크포인터의 interrupt 상태를 명시적으로 해소하거나, 새 thread_id로 전환하는 방안을 검토한다.

---

## Info (초록색 원)

### IN-01. cancel_store.py의 CancelStore Protocol과 구현 분리 구조는 우수

- **파일**: `src/services/cancel_store.py`
- **소견**: Protocol 기반 추상화로 Memory/Redis 교체가 자연스럽다. SessionStore와 동일한 패턴을 따르는 것이 일관성 있다.

### IN-02. cancel_store.py의 _CANCEL_TTL 안전망 설계 적절

- **파일**: `src/services/cancel_store.py:56`
- **소견**: 300초 TTL로 미정리 플래그 자동 만료는 좋은 방어 설계. 다만 장시간 실행 파이프라인(복잡한 탐색 루프)이 300초를 초과할 경우 cancel이 만료될 수 있으므로, 필요 시 TTL을 설정에서 조절 가능하도록 하면 좋다.

### IN-03. conftest.py의 autouse fixture 범위가 넓음

- **파일**: `tests/conftest.py:107-114`
- **소견**: `autouse=True`로 모든 테스트에 cancel store 리셋이 적용된다. cancel과 무관한 테스트에도 import + 리셋이 수행되므로 미세한 오버헤드가 있다. 현 규모에서는 무시할 수준이지만, 테스트 수가 크게 증가하면 cancel 관련 테스트에만 적용하는 것을 고려한다.

### IN-04. make_cancel_updates의 exploration_summary 하드코딩 메시지

- **파일**: `src/agents/graph/cancel.py:86`
- **소견**: `"사용자 요청으로 중단되었습니다."` 가 `make_cancel_updates`와 `_build_cancel_summary` 양쪽에 유사하게 존재한다. user_messages.py 등 메시지 상수 모듈로 통합하면 일관성 유지가 용이하다.

### IN-05. sql_executor의 cancel 메시지에 SQL 부분 노출

- **파일**: `src/agents/nodes/present/sql_executor.py:51-54`
- **소견**: cancel 시 `state.reason.validated_sql[:200]`을 응답에 포함한다. 사용자 상호작용 규칙("SQL 자체는 보여주지 않거나 접기 처리")과 충돌할 수 있다. 부분 SQL은 디버그 로그에만 기록하고, 사용자 응답에서는 제외하는 것이 바람직하다.

### IN-06. runner.py의 _build_result에서 cancelled 판정 방식이 비직관적

- **파일**: `src/agents/graph/runner.py:397-401`
- **소견**: `_status == "cancelled"` 문자열 비교와 `_status.value == "cancelled"` 를 OR로 판정한다. QueryStatus Enum을 직접 비교하면 더 명확하다: `_cancelled = (_status == QueryStatus.CANCELLED)`. `str(Enum)` 비교는 Enum 값이 변경되면 깨진다.

### IN-07. cancel 엔드포인트 응답에 HTTP 202 (Accepted) 사용 권장

- **파일**: `src/routers/sessions.py:134`
- **소견**: 현재 200 OK를 반환하지만, cancel은 비동기 처리 요청이므로 의미적으로 `202 Accepted`가 더 적합하다. `status_code=202`를 명시하면 클라이언트가 "요청 접수됨, 아직 완료 아님"을 명확히 인지할 수 있다.

### IN-08. _handle_error에서 CANCELLED 시 status를 재설정하는 것은 중복

- **파일**: `src/agents/graph/pipeline.py:387`
- **소견**: `"status": QueryStatus.CANCELLED`를 반환하는데, 이미 state.status가 CANCELLED이다. LangGraph reducer에 따라 무해할 수 있지만, 명시적으로 동일 값을 재설정하는 것은 의도가 불명확하다. 주석을 추가하면 좋다.

---

## 종합 평가

### 잘 된 점
1. **Protocol 기반 추상화**: CancelStore 프로토콜로 Memory/Redis 전환이 깔끔하다.
2. **방어적 설계**: Redis 장애 시 False 반환, TTL 안전망, 예외 로깅이 일관적이다.
3. **턴 격리**: turn_id 매칭으로 이전 턴의 cancel이 새 턴에 영향을 주지 않는다.
4. **라우팅 완결성**: pipeline.py의 4개 라우팅 함수 모두에서 CANCELLED를 체크한다.
5. **테스트 격리**: conftest.py의 autouse fixture로 싱글턴 오염을 방지한다.

### 우선 개선 권장 사항
1. **(CR-01)** cancel API 인증 추가 — 보안 필수
2. **(CR-04)** `_client` 직접 접근 제거 — 캡슐화 복원
3. **(WR-01, WR-02)** recovery_agent, context_interpreter에 cancel 체크 추가 — 취소 응답성 개선
4. **(WR-03)** `_route_after_sql_generator`에 CANCELLED 체크 추가 — 경로 정합성
5. **(WR-06)** Redis 이중 조회 최적화 — 성능
6. **(CR-02)** GETDEL Redis 버전 호환성 확인/대응 — 폐쇄망 배포 대비
