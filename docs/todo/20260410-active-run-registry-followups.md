# 활성 파이프라인 레지스트리 — 후속 개선 사항

> 작성일: 2026-04-10
> 상태: 대기 (이번 PR 머지 후 별도 티켓으로 진행)
> 선행 작업: `20260410-session-resume-and-crash-recovery-design.md` 의 Phase 1 구현 완료
> 참고: Phase 1 구현 후 전문가 3인(pipeline-designer / code-reviewer / security-guard) 재검토에서 도출된 미반영 항목

---

## 1. 배경

크래시 복구 + 세션 스위칭 UX 버그 수정을 위해 "활성 파이프라인 레지스트리"
기능을 구현(Phase 1)하였다. 전문가 재검토에서 **Blocker 1건 + High 3건은
현 PR 에서 즉시 수정**하였으나, 나머지 항목은 이번 PR 범위를 넘어서므로
별도 티켓으로 분리한다.

Phase 1 에서 반영 완료된 항목:
- `src/routers/sessions.py` `pathlib.Path` / `fastapi.Path` 심볼 충돌 수정
- `runner.py` 의 `_run_key = uuid.uuid4()` — CAS 토큰 유니크화
- `mark_active` 호출을 유저 턴 조기 저장 **이전** 으로 이동
- `active_run_ttl_seconds: 600 → 1800`
- Frontend `/active` 호출 1회 재시도 로직
- `test_runner_active_tracking.TestCASRaceSafety.test_run_pipeline_generates_unique_run_keys` 회귀 테스트 추가

---

## 2. 미반영 항목

### 2-1. [High] 세션 엔드포인트 인증 부재
**지적자**: security-guard

`/api/sessions/*` 전 엔드포인트에 인증 미들웨어가 적용되어 있지 않다.
현재 `session_id` 포맷이 `session-{epoch_ms}` 로 예측 가능하여
공격자가 타임스탬프 범위를 순열 탐색하여 타인의 세션 활성 여부 및
대화 내용을 조회할 수 있다.

- 영향 범위: `/sessions`, `/sessions/{id}`, `/sessions/{id}/active`,
  `/sessions/{id}/cancel`, `/turns/{id}/*` 등 전체
- `/active` 는 DB 접근 없이 빠르게 응답하므로 자동화 탐색 비용이 특히 낮음

**수정안**:
- 라우터 차원의 `Depends(get_current_user)` 적용
- 조회 결과를 요청자의 `user_id` 와 대조하는 서버 측 검증
- 세션 ID 포맷을 UUID 로 전환하거나 서버 토큰과 바인딩

---

### 2-2. [Med] interrupt resume 경로의 `_validateTurns` 검증 부재
**지적자**: pipeline-designer

명확화 질문(interrupt) 대기 상태에서 `_execute_and_finalize` 가 return 시
finally 블록에서 `clear_active` 가 호출되어 `/active=false` 가 된다.
프론트엔드 `_validateTurns` 가 마지막 assistant 턴이 `clarification` 타입인
경우를 "응답 있음" 으로 처리하는지 미검증 상태.

**조치**:
- `static/embedded.html` `_validateTurns` 구현을 점검하여
  `turn_type === 'clarification'` 을 `_pendingResponse=false` 로 처리하는지 확인
- 테스트 케이스 추가:
  1. 명확화 질문 표시 중 세션 재방문 → gap 오인하지 않아야 함
  2. 명확화 답변 직후 크래시 → `_pendingResponse=true` + `/active=false` → gap 표시

---

### 2-3. [Med] Multi-worker Memory fallback 오탐
**지적자**: pipeline-designer

현재 `main.py` lifespan 에서 Redis 미연결 시 `MemoryActiveRunStore` 로
fallback 한다. L4 hash sticky session 이 깨지거나(워커 재시작·스케일),
운영 중 Redis 장애 시 B 워커의 빈 Memory 가 `is_active=false` 를 반환하여
**실행 중인 파이프라인을 gap 오인**할 수 있다.

**수정안**:
- 멀티워커 + Redis 장애 조합에서는 `ActiveRunStore` 를 `None` 으로 두고
  `check_active` 가 `None` 일 때 **보수적으로 `true`** 반환 (gap 표시 지연 감수)
- 또는 lifespan 에서 `settings.worker_count > 1 and not redis_available` 이면
  경고 로그 + 명시적 degraded 모드 진입

---

### 2-4. [Med] Redis 타임아웃 미설정
**지적자**: code-reviewer

`RedisActiveRunStore.set_active` / `clear_active` / `is_active` 는
Redis 클라이언트 레벨 `socket_timeout` 에만 의존하고 명시적 타임아웃이 없다.
`.claude/rules/code-style.md` 의 "외부 호출 타임아웃 필수" 규칙 위반.

**조치**:
- 클라이언트 생성 시 `socket_timeout=2.0` 설정 확인
- 또는 각 호출을 `asyncio.wait_for(..., timeout=2.0)` 로 감싸기

---

### 2-5. [Med] `_SESSION_ID_PATTERN` 다른 엔드포인트 미적용
**지적자**: code-reviewer (L1)

`_SESSION_ID_PATTERN` regex 검증이 `/sessions/{id}/active` 에만 적용되어
있다. 다른 엔드포인트(`GET /sessions/{id}`, `DELETE`, `PATCH`, `cancel_pipeline` 등)
는 무검증으로 통과되어 Redis 키 주입 방어가 일관되지 않는다.

**조치**:
- `APIRouter` 차원의 공통 path converter 정의 또는
- 전 엔드포인트에 `PathParam(..., pattern=_SESSION_ID_PATTERN)` 일괄 적용

---

### 2-6. [Med] EVALSHA 캐시 미사용
**지적자**: code-reviewer (M2)

`RedisActiveRunStore._CAS_DEL_SCRIPT` 를 매 호출마다 `EVAL` 로 전송.
네트워크 페이로드 최적화 여지. `src/services/cancel_store.py` 도 동일
패턴을 사용 중이므로 두 저장소를 일괄 `SCRIPT LOAD` + `EVALSHA` 로
개선하는 편이 일관성 있다.

**조치**: `active_run_store.py` + `cancel_store.py` 공통 리팩터링 PR.

---

### 2-7. [Low] `_startPendingPoll` 중간 `/active` 재확인 없음
**지적자**: pipeline-designer (P7)

프론트엔드 `_startPendingPoll` 은 2초 간격 60회(=2분) polling.
polling 도중 서버가 크래시하면 사용자는 2분간 "서버에서 처리 중…"
메시지를 보게 된다.

**조치**: 폴링 N회마다(예: 5회=10초) `/active` 재확인, `false` 면 조기 gap 전환.

---

### 2-8. [Low] 파이프라인 성공 + 저장 실패 케이스 가시성
**지적자**: pipeline-designer (S2)

`_execute_and_finalize` 내 `save_turn(assistant)` 이 네트워크 사유로 실패하면
파이프라인은 성공이지만 assistant 턴이 없는 상태로 finally → clear_active.
재방문 시 `/active=false` → gap 표시. 사용자는 "결과 유실"을 알 수 없고
handler trace 만 남는다.

**조치**:
- save_turn 실패를 별도 상태로 추적 (DB 저장 대기 큐 또는 관리자 알림)
- gap 메시지에 "저장 실패로 결과가 유실되었을 수 있습니다" 와 같은 부연 설명

---

### 2-9. [Low] 타입힌트 및 스타일 개선
**지적자**: code-reviewer (L2, L3, N1, N2)

- **L2**: `RedisActiveRunStore.redis_client: Any` → `redis.asyncio.Redis` 로
  구체 타입 지정 (mypy --strict 위반 해소)
- **L3**: `test_session_active_endpoint.py` 의 `store._active[...] = ...`
  내부 상태 직접 조작 → `asyncio.run(store.set_active(...))` 사용
- **N1**: `_SESSION_ID_PATTERN` 을 실제 포맷에 맞게 더 좁게
  `^(session-\d+|[A-Za-z0-9-]{36})$` 로 제한 (defense-in-depth)
- **N2**: `sessions.py:191` `except ValueError` 블록에서
  `raise HTTPException(...) from None` 로 체인 노이즈 제거

---

### 2-10. [Low] `download_trace` 회귀 테스트
**지적자**: code-reviewer

이번 PR 의 Blocker(B1)였던 `pathlib.Path` vs `fastapi.Path` 심볼 충돌
재발을 방지하기 위해 경로 순회 공격 요청(`../etc/passwd`, `..\windows\...`)
을 커버하는 엔드포인트 테스트가 필요하다.

**조치**: `tests/auto/unit/test_trace_download.py` (신규) 또는
`test_session_active_endpoint.py` 에 섹션 추가.

---

## 3. 우선순위 및 추천 진행 순서

| 순위 | 항목 | 이유 |
|---|---|---|
| 1 | 2-1 세션 엔드포인트 인증 | 보안 High, 전체 `/api/sessions/*` 에 영향 |
| 2 | 2-5 regex 일괄 적용 + 2-10 download_trace 회귀 테스트 | 작은 PR 로 묶어 빠르게 처리 |
| 3 | 2-3 Multi-worker fallback 보수화 | 운영 배포 전 필수 |
| 4 | 2-2 interrupt resume 검증 | 실제 엣지케이스 재현 후 수정 |
| 5 | 2-4 Redis 타임아웃 | 코드스타일 규칙 준수 |
| 6 | 2-6 EVALSHA 일괄 개선 | cancel_store 와 함께 리팩터링 |
| 7 | 2-7, 2-8, 2-9 | UX/품질 개선, 여유 있을 때 |

---

## 4. 관련 파일

- `src/routers/sessions.py`
- `src/services/active_run_store.py`
- `src/agents/graph/active_run.py`
- `src/agents/graph/runner.py`
- `src/main.py`
- `static/embedded.html`
- `tests/auto/unit/test_active_run_store.py`
- `tests/auto/unit/test_session_active_endpoint.py`
- `tests/auto/unit/test_runner_active_tracking.py`
