# 전체 코드베이스 리뷰 통합 요약서

> 리뷰 일시: 2026-04-06
> 대상: `src/` 전체 (80+ 파일) + `resources/prompts/` (21 파일) + `static/embedded.html`
> 검증: 1차 리뷰 후 전수 재검증 완료 (오탐 6건 제거/하향, 심각도 5건 조정)

## 원본 리뷰 문서

| # | 문서 | 범위 |
|---|------|------|
| 1 | [core-pipeline-review-report.md](20260406-core-pipeline-review-report.md) | pipeline.py, runner.py, cancel.py, checkpointer.py, state.py, main.py, sessions.py |
| 2 | [interpret-layer-review-report.md](20260406-interpret-layer-review-report.md) | Interpret 계층 (의도 분류, 쿼리 정규화, 명확화 처리) |
| 3 | [reason-layer-security-review-report.md](20260406-reason-layer-security-review-report.md) | Reason 계층 (SQL 생성, 검증, 복구, 도구) + 보안 |
| 4 | [present-layer-code-review-report.md](20260406-present-layer-code-review-report.md) | Present 계층 (SQL 실행, 포맷팅, 분석, 시각화) |
| 5 | [connector-config-utility-review-report.md](20260406-connector-config-utility-review-report.md) | 커넥터, config, 유틸리티 |
| 6 | [prompts-frontend-review-report.md](20260406-prompts-frontend-review-report.md) | 프롬프트 (21파일) + 프론트엔드 (embedded.html) |

---

## 검증 결과 반영 사항

1차 리뷰에서 도출된 이슈들을 실제 코드와 대조하여 다음을 수정했습니다:

| 원래 ID | 변경 | 사유 |
|---------|------|------|
| INT-04 (get_compiled_app 순서 의존성) | **제거 (오탐)** | lifespan 메커니즘이 순서 보장, CLI 모드는 의도된 설계 |
| LOG-02 (connect_all 매 요청 호출) | **제거 (오탐)** | `_connected` 플래그로 멱등성 보장, CLI 지원용 방어적 패턴 |
| SEC-11 (formatter XSS) | **제거 (오탐)** | `trace_summary`는 서버 내부 고정 문자열만 사용, 사용자 입력 주입 경로 없음 |
| W-ERR-04 (connect_all 전체 기동 중단) | **제거 (오탐)** | `enabled = 필수` 의미론, fail-fast 의도된 설계 |
| SEC-06 Sybase 부분 | **제거 (오탐)** | Sybase는 ODBC/키워드 인자 연결, f-string URL 해당 없음 |
| PRM-01 (━━━ 6개소) | **수정** | 실제 12줄 (6쌍), 설계서 변환 대상이나 카운트 오류 보정 |
| PRM-02 (□ 포함 6개소) | **수정** | □ 0개소(오탐), 실제는 ■ 4개소 + ━━━ 2개소 |
| SEC-01 | **심각도 유지, 설명 보정** | 수동 sanitize(L187)는 존재하나 파라미터 바인딩 대비 불충분 |
| SEC-02 f-string | **Critical → Warning** | 식별자 영역이라 파라미터 바인딩 불가, `_IDENT_RE` 화이트리스트가 방어 |
| SEC-03 (SELECT *) | **Critical → Warning** | 내부 탐색 도구 용도, 사용자 직접 노출 경로 미확인 |
| LOG-01 | **Critical → Warning** | `_MAX_CACHE=100` 상한 존재, "메모리 누수" 과장. 멀티워커 공유 불가만 유효 |
| INT-03 | **Critical → Warning** | runner.py에서 순차 호출 구조라 실제 발생 확률 극히 낮음 |
| LOG-03 | **Critical → Info** | 호출부에 `if extraction:` 가드 존재, 빈 문자열 설정 실제 불가 |

---

## 최종 이슈 목록

### Phase 1 — 보안 (즉시 수정 필요)

| # | 파일 | 문제점 | 왜 수정해야 하는가 | 중요도 | 개선 방안 |
|---|------|--------|-------------------|--------|-----------|
| 1 | [tools.py:187-205](src/agents/nodes/reason/tools.py) | `search_column_values`에서 LIKE 검색 키워드를 수동 이스케이프(`''`,`\\` 치환) 후 f-string으로 SQL에 삽입. 파라미터 바인딩 미사용 | 수동 sanitize는 DB 드라이버별 특수문자 처리 차이, 유니코드 바이패스 가능성에 취약. 금융 서비스에서 SQL 인젝션은 치명적 | ~~Critical~~ | ⏸️ **미적용/TODO** (A-1) — 현재 화이트리스트+이스케이프+읽기전용+LLM출력으로 실질적 위험 극히 낮음. 폐쇄망 배포 후 재검토 |
| 2 | [sql_executor.py:114](src/agents/nodes/present/sql_executor.py) | 트래킹 로그에 `rows[0]` 원본 데이터가 마스킹 없이 기록 | PII(주민번호, 계좌번호 등)가 로그에 평문 저장되어 금융 감사 규정 위반. 로그 접근자에게 개인정보 노출 | ~~Critical~~ | ⏸️ **미적용** (B-1) — 로그 마스킹은 당분간 현행 유지 |
| 3 | [sql_executor.py:51-54](src/agents/nodes/present/sql_executor.py) | cancel 시 `validated_sql[:200]`이 `formatted_response`에 삽입되어 사용자 UI에 SQL 원문 노출 | IT 지식 없는 일반 직원에게 SQL 구조 노출은 보안/UX 모두 부적절. 테이블명/컬럼명으로 DB 구조 유추 가능 | Critical | cancel 메시지를 "요청이 취소되었습니다" 같은 자연어로 교체 |
| 4 | [postgres_connector.py:61-67,176-179](src/connectors/impl/postgres_connector.py) | PostgreSQL 연결 URL에 비밀번호가 f-string으로 직접 삽입. 특수문자(`@`, `#` 등) 포함 시 URL 파싱 실패 또는 보안 노출 | 폐쇄망 배포 시 비밀번호 정책에 특수문자 요구가 일반적. 연결 자체 실패 가능 | Critical | SQLAlchemy `URL.create()` 또는 `urllib.parse.quote_plus()` 적용 |
| 5 | [mongo_connector.py:145-149](src/connectors/impl/mongo_connector.py) | MongoDB 연결 URI에도 동일한 f-string 비밀번호 삽입 문제 | 위와 동일 | Critical | `quote_plus(password)` 적용 |
| 6 | [embedded.html:~2042](static/embedded.html) | `sanitizeHTML`에서 `script`, `iframe` 등은 제거하지만 `<style>` 태그 미제거 | CSS 인젝션으로 UI 위조, `@import url()`로 외부 서버에 데이터 유출 가능 | Critical | sanitize 제거 목록에 `style` 태그 추가 |
| 7 | [embedded.html:~2036](static/embedded.html) | `sanitizeSVG`에서 `javascript:` 프로토콜만 차단, `<use href="http://...">`로 외부 리소스 로드 가능 | SVG 내 외부 URL 참조로 내부 네트워크 정보 유출, SSRF 벡터 | Critical | href 허용을 `#` (내부 참조)만으로 제한하거나, 외부 URL 프로토콜 전면 차단 |
| 8 | [hive_connector.py:128](src/connectors/impl/hive_connector.py), [impala_connector.py:126](src/connectors/impl/impala_connector.py) | `cursor.execute(query)` 호출 시 `params` 인자를 전달하지 않음. 인터페이스에 params가 있으나 무시됨 | 폐쇄망 배포 대상 커넥터에서 파라미터 바인딩 미작동. SQL 인젝션 방어 계층 누락 | ~~Critical~~ | ❌ **제외** — LLM 생성 SQL만 실행하므로 params 전달 경로 자체가 없음. 호출부 전수 확인 완료 |

### Phase 2 — 데이터 무결성 / 동시성

| # | 파일 | 문제점 | 왜 수정해야 하는가 | 중요도 | 개선 방안 |
|---|------|--------|-------------------|--------|-----------|
| 9 | [pipeline.py:233,252](src/agents/graph/pipeline.py) | `_route_after_sql_validator`에서 `state.reason.recovery_entry_source = "..."` 형태로 state 직접 mutation | LangGraph 라우팅 함수는 순수 함수여야 함. 체크포인터가 mutation 전 상태를 저장하면 interrupt/resume 시 상태 불일치 발생 | Critical | mutation 로직을 라우팅 함수에서 제거하고, 별도 노드 또는 노드 반환값으로 state 갱신 |
| 10 | [cancel_store.py:80-87](src/services/cancel_store.py) | `pop_cancel`에서 Redis GET과 DELETE가 별도 명령으로 실행. 두 명령 사이 경쟁 상태 가능 | 동일 세션에 동시 cancel 확인 요청 시 이중 처리 가능. 실무적 빈도는 낮으나, Redis `GETDEL` 1줄로 해결 가능한 간단한 수정 | Warning | Redis 7.0+ `GETDEL` 명령 사용, 또는 Lua 스크립트로 원자적 처리 |
| 11 | [redis_store.py:92,106,117](src/services/session/redis_store.py) | `connect()` 미호출 시 `self._client = None`이어서 `get_history`, `append_history` 등 모든 메서드에서 `AttributeError` | 초기화 순서가 잘못되거나 연결 실패 후 재시도 시 즉시 크래시. `health_check`만 None 체크 존재 | ~~Critical~~ | ⏸️ **Redis 활성화 시 적용** (B-2) — 현재 `session_backend=memory` 기본값이므로 런타임 영향 없음 |
| 12 | [clarification_handler.py](src/agents/nodes/interpret/clarification_handler.py) | `AmbiguitySignal` 객체를 in-place mutation으로 수정 | 현재는 동작하지만, Pydantic `frozen=True` 전환 시 즉시 장애. LangGraph State 불변성 패턴 위반 | ~~Warning~~ | ❌ **제외** — 프로젝트 전체(reason/interpret/present 모든 노드)가 동일한 in-place mutation 패턴. 이 객체만 변경하면 일관성 파괴. frozen=True 전환 계획 없음 |

### Phase 3 — 기능 / 아키텍처

| # | 파일 | 문제점 | 왜 수정해야 하는가 | 중요도 | 개선 방안 |
|---|------|--------|-------------------|--------|-----------|
| 13 | [main.py](src/main.py) | `_sql_result_cache` 전역 dict (`_MAX_CACHE=100`). 멀티워커 환경에서 워커 간 캐시 비공유 | 프로덕션 배포(gunicorn 멀티워커) 시 다운로드 요청이 다른 워커에 도착하면 캐시 miss → 404. 현재 개발 단계에서는 무해 | ~~Warning~~ | ⏸️ **TODO로 이관** (A-3) — 파일 기반 캐시 방향. 현재 copy 기능으로 다운로드 대체 가능 |
| 14 | [runner.py](src/agents/graph/runner.py) | `run_pipeline` 함수가 약 170줄 단일 함수. sanitize/interrupt/실행/턴저장/에러처리 모두 포함 | 테스트 어려움, 에러 경로 추적 어려움, 변경 영향 범위 큼 | Warning | sanitize/interrupt/실행/후처리를 별도 private 함수로 분리 |
| 15 | [sessions.py](src/routers/sessions.py) | `cancel_pipeline` 엔드포인트에 세션 소유권 검증 없음 | session_id만 알면 타인의 파이프라인 취소 가능. 현재 인증 미구현이지만, 인증 추가 시 함께 적용 필요 | ~~Warning~~ | ⏸️ **보류** (A-4) — 사내 SSO redirection 연동 시 함께 적용 |
| 16 | [embedded.html](static/embedded.html) | WebSocket 연결 시 인증 토큰 없음 + 세션 ID가 타임스탬프 기반(`'session-' + Date.now()`)으로 예측 가능 | 세션 ID 추측으로 타인 세션 접근 가능. 내부 네트워크 배포라도 세션 하이재킹 위험 | ~~Warning~~ | ⏸️ **보류** (A-4) — 사내 SSO redirection 연동 시 함께 적용 |
| 17 | [user_messages.py:52-59](src/agents/models/user_messages.py) | ES 제거 완료 상태인데 `es_` 접두어 소스 키 잔존. 단, `format_context_warning()` 자체가 호출 0건인 데드코드 | 코드 혼란 유발, ES 미사용 원칙 위배 | Info | 함수 자체를 삭제하거나, ES 관련 키 제거 |
| 18 | [config.py](src/config.py) | ES 미사용 확정인데 config에 ES 관련 필드 12개 + elasticsearch_connector + manager import 잔존 | 불필요한 의존성, 설정 혼란, 폐쇄망에서 ES 패키지 설치 시도 가능 | Warning | ES 관련 config 필드·connector·manager를 **주석 처리** (향후 재사용 가능성 있어 삭제하지 않음) |

### Phase 4 — 커넥터 / 폐쇄망 대응

| # | 파일 | 문제점 | 왜 수정해야 하는가 | 중요도 | 개선 방안 |
|---|------|--------|-------------------|--------|-----------|
| 19 | hive/impala/sybase 커넥터 | `sanitize_row()` import도 호출도 없음. `interfaces.py` docstring의 의무 사항 위반 | 폐쇄망 배포 시 Decimal, date, bytes 등 비직렬화 타입이 JSON 변환에서 TypeError 발생 | Critical | 각 커넥터 `execute_query` 반환 전에 `sanitize_row()` 적용 |
| 20 | postgres_connector.py | InfoDBConnector / HistoryDBConnector가 90% 코드 중복 | 유지보수 시 한쪽만 수정하여 불일치 발생 위험 | Warning | ⏸️ **통합 안함** (B-3) — 폐쇄망 실 연동 전이므로 현행 유지 |
| 21 | hive/impala 커넥터 | 95% 코드 중복 + 단일 커넥션으로 동시 요청 시 race condition | 폐쇄망 배포 시 동시 사용자 요청에서 커넥션 경합 | Warning | ⏸️ **통합 안함** (B-3) — 폐쇄망 실 연동 전이므로 현행 유지 |
| 22 | [postgres_connector.py](src/connectors/impl/postgres_connector.py) | HistoryDBConnector가 SELECT 제한 없이 임의 쿼리 실행 가능 | SQL 이력 DB는 읽기 전용이어야 하나, 인터페이스 수준에서 DML 차단 없음 | ~~Warning~~ | ✅ **모든 DML 허용** (A-5) — 이력 적재 용도이므로 현행 유지 |

### Phase 5 — 프롬프트

| # | 파일 | 문제점 | 왜 수정해야 하는가 | 중요도 | 개선 방안 |
|---|------|--------|-------------------|--------|-----------|
| 23 | [analyzer_viz_svg_system.txt](resources/prompts/present/analyzer_viz_svg_system.txt) | `━━━[...]━━━` 구분선 패턴이 6쌍(12줄) 미변환 | 폐쇄망 소형 모델(Solar Pro 2 70B)이 특수문자를 토큰 낭비하거나 잘못 해석할 가능성. 설계서에 변환 대상으로 명시됨 | Warning | `## 제목` 형태로 변환 (설계서 `20260402-prompt-format-unification.md` 잔여 작업 #1) |
| 24 | [query_normalizer_phase1_system.txt](resources/prompts/interpret/query_normalizer_phase1_system.txt) | `■` 4개소 (L270,320,373,428) + `━━━` 2개소 (L485,487) 잔존 | 위와 동일 | Warning | `■ 예제 N:` → `### 예제 N:`, `━━━` → 제거/`##` 대체 |
| 25 | [analyzer_viz_judgment_system.txt](resources/prompts/present/analyzer_viz_judgment_system.txt) | `□` 체크박스 3개소 (L70-72) 잔존 | 위와 동일 | Warning | `□` → `-` 로 변환 |
| 26 | [query_normalizer_phase2_system.txt](resources/prompts/interpret/query_normalizer_phase2_system.txt) | `■` 블릿 2개소 (L80, L93) 잔존. 기존 todo 문서에 미기재 | 위와 동일. 잔여 작업 목록에서도 누락되어 있어 수정 누락 위험 | Warning | `■` → `###` 로 변환, todo 문서에도 추가 |
| 27 | [intent_classifier_system.txt](resources/prompts/interpret/intent_classifier_system.txt) | Few-shot 예제에서 `ambiguities` 키 자체를 생략하지만, 스키마 정의에서는 `"ambiguities": []` 빈 배열 출력을 명시 | LLM이 예제를 따라 키를 생략하면 파싱 시 KeyError 또는 None 처리 필요. 폐쇄망 모델일수록 예제 의존도 높음 | Warning | Few-shot 예제에 `"ambiguities": []` 명시적 포함 |
| 28 | 프롬프트 다수 | `{code_mappings}`, `{tool_results}` 등 변수가 경계 마커 없이 프롬프트에 삽입 | 검색 결과에 프롬프트 인젝션 페이로드가 포함될 경우 LLM 지시 변조 가능 | Warning | `<context>...</context>` 같은 XML 경계 태그로 삽입 영역 명확화 |

### Phase 6 — 코드 품질 / 성능

| # | 파일 | 문제점 | 왜 수정해야 하는가 | 중요도 | 개선 방안 |
|---|------|--------|-------------------|--------|-----------|
| 29 | [input_sanitizer.py:42](src/services/input_sanitizer.py) | SQL 인젝션 패턴 중 `--` 가 자연어 입력에서 오탐 발생 ("2024-03--2024-06", "A--B 비교" 등) | 정상적인 사용자 입력이 차단되어 사용자 경험 저해 | ~~Warning~~ | ⏸️ **스킵** (B-5) — 해당 패턴 사용 빈도 극히 낮아 현행 유지 |
| 30 | [sql_safety_checker.py:155](src/services/sql_safety_checker.py) | UNION 키워드 전면 차단이 UNION ALL을 사용하는 정당한 SQL도 차단 | NL-to-SQL에서 "A와 B 합쳐서 보여줘" 같은 UNION 요청을 원천 차단 | ~~Info~~ | ✅ **적용 완료** (A-6) — security.py에서 UNION 차단 패턴 제거. input_sanitizer는 유지 |
| 31 | response_formatter, data_analyzer 서비스 | LLM 클라이언트를 raw `client.messages.create()`로 직접 호출 — 재시도/폴백 없음 | LLM API 일시적 장애 시 사용자에게 즉시 에러 반환. 다른 노드들은 재시도 로직 보유 | Warning | 공통 LLM 호출 wrapper 사용 또는 tenacity 재시도 데코레이터 적용 |
| 32 | [recovery_agent.py](src/agents/nodes/reason/recovery_agent.py) | recovery action 보정 방향이 실패 시 "replan"(재시도) — 무한 루프 위험 | replan은 동일 실패를 반복할 가능성. "give_up"이 안전한 기본값 | ~~Warning~~ | ❌ **제외** — LLM이 action을 오타낸 것이지 해결 불가능한 상황이 아님. replan 보정이 올바른 동작. LoopGuard가 최종 횟수 제한 보장 |
| 33 | [chart_generator.py:72](src/services/visualization/chart_generator.py) | 모듈 레벨에서 YAML 파일 I/O 실행 (`_COLORS, _FONT = _load_chart_config()`) | 모듈 import 시점에 파일 I/O 발생. YAML 파일 부재 시 import 자체 실패, 테스트 격리 어려움 | Info | lazy loading 패턴 또는 `functools.cache` 적용 |
| 34 | [neo4j_connector.py](src/connectors/impl/neo4j_connector.py) | 인메모리 캐시(dict)가 TTL/LRU 없이 무제한 성장 | 장기 실행 시 메모리 점유 증가 | Info | `functools.lru_cache` 또는 TTL 캐시 도입 |
| 35 | [turn_text_store.py](src/services/turn_text_store.py) | `MAX(turn_seq)+1` 서브쿼리 채번이 autocommit 환경에서 동시 INSERT 시 이론적 중복 가능 | runner.py 순차 호출 구조라 실제 발생 확률 극히 낮음. 대화 이력 설계 구현 시 함께 개선 가능 | ~~Info~~ | ❌ **제외** — session_id별 독립 채번이므로 전역 SEQUENCE 부적합. 순차 호출 구조라 동시성 문제 없음. MAX+1이 현재 구조에 적합 |
| 36 | [embedded.html](static/embedded.html) | 프론트엔드 변수 명명이 극단적 축약 (TM, MS, RD, SE, ED, CN 등 2자 약어) | 유지보수성 저하. 새 개발자가 코드 파악 어려움 | Info | 주요 모듈 변수를 의미 있는 이름으로 리네이밍 (TimerManager, MessageStore 등) |
| 37 | 프롬프트 다수 | intent_classifier 16개 Few-shot + phase1 400+ 라인 — 폐쇄망 70B 모델의 컨텍스트 소비 과다 | 70B 모델의 컨텍스트 윈도우(8K~32K) 내에서 프롬프트+입력+출력이 맞지 않을 가능성 | ~~Warning~~ | ❌ **제외** (A-7) — 현재 3만 토큰 수준으로 문제 없음 |

---

## 통계 요약

| 구분 | 건수 | 항목 |
|------|------|------|
| ✅ 적용 완료 | **1건** | #30 (UNION 차단 제거) |
| 🔧 수정 대상 | **15건** | #3~7, #9, #10, #14, #17~19, #23~27, #31 |
| ⏸️ 미적용/TODO/보류 | **13건** | #1(A-1), #2(B-1), #11(B-2), #13(A-3), #15~16(A-4), #20~21(B-3), #28(B-4), #29(B-5) |
| ❌ 제외 (수정 불필요) | **8건** | #8(params불필요), #12(전체패턴), #22(A-5), #32(replan보정), #33~35(Info/부적합), #37(A-7) |
| **전체** | **37건** | 오탐 6건 제거, 심각도 5건 조정 후 |

---

## 수정 우선순위 로드맵 (결정 반영)

```
Phase 1 (즉시) ── 보안 Critical #3~7
  └─ cancel SQL 노출, 커넥터 비밀번호, 프론트엔드 sanitize
  └─ 제외: #1(TODO), #2(미적용), #8(params 불필요)

Phase 2 (1주 내) ── 데이터 무결성 #9, #10
  └─ pipeline state mutation(A-2 결정 반영), cancel 원자성
  └─ 제외: #11(Redis 활성화 시), #12(전체 패턴, 제외)

Phase 3 (2주 내) ── 아키텍처/기능 #14, #17, #18
  └─ 함수 분리, ES 잔존 제거
  └─ 제외: #13(TODO), #15~16(SSO 보류)

Phase 4 (폐쇄망 배포 전) ── 커넥터 #19
  └─ sanitize_row 적용
  └─ 제외: #20~21(통합 안함), #22(DML 허용)

Phase 5 (병행) ── 프롬프트 #23~27
  └─ 포맷 통일 잔여 작업, Few-shot ambiguities 정렬
  └─ 제외: #28(TODO), #37(제외)

Phase 6 (여유 시) ── 품질/성능 #31, #36
  └─ LLM 재시도, 프론트엔드 명명 개선
  └─ 제외: #29(스킵), #30(완료), #32(replan 보정 정상), #33~35(Info/부적합)
```

---

## 미결 결정사항 및 개선방안 보완 필요 항목

아래 항목들은 개선방안이 단순 코드 수정이 아니라 **설계 결정이 필요**하거나, **상세 개선안이 부족**한 항목입니다.
수정 착수 전에 방향을 확정해야 합니다.

### A. 설계 결정 필요 (방향 미확정)

| # | 이슈 | 결정 사항 | 선택지 | 판단 기준 |
|---|------|----------|--------|-----------|
| A-1 | #1 (tools.py 파라미터 바인딩) | 커넥터 인터페이스에 `params` 지원을 어디까지 확장할 것인가 | **미적용, TODO로 이관** | 현재 `_IDENT_RE` 화이트리스트 + 수동 이스케이프 + 읽기 전용 계정 + LLM 출력(사용자 직접 입력 아님)으로 실질적 위험 극히 낮음. 파라미터 바인딩 시 디버깅 로깅 불편도 고려. 폐쇄망 배포 후 필요 시 재검토 |
| A-2 | #9 (pipeline state mutation) | `recovery_entry_source` 설정을 어느 노드에서 수행할 것인가 | **(a) `sql_validator_node` 반환값에 포함으로 결정** | `sql_validator_node`가 이미 `failure_type`/`failure_reason`을 설정하므로 같은 위치에 `recovery_entry_source = "sql_validator"` 추가. `pipeline.py:233,252`의 라우팅 함수 mutation 2줄 삭제 |
| A-3 | #13 (sql_result_cache) | 멀티워커 대응 캐시 방식 | **TODO로 이관 (파일 기반 캐시 방향)** | 메모리 캐시는 멀티워커 시 위험. 파일 기반 저장이 적합하나, 현재 copy 기능으로 다운로드 대체 가능하므로 우선순위 낮음 |
| A-4 | #15, #16 (인증/인가) | 인증 체계를 언제, 어떤 방식으로 도입할 것인가 | **보류** — 사내 SSO redirection 방식 연동 예정 | 폐쇄망 배포 시점에 SSO/LDAP 연동 구현. 현재 단계에서는 미구현 유지 |
| A-5 | #22 (HistoryDB SELECT 제한) | HistoryDB에 INSERT를 허용할 것인가 | **모든 DML 허용으로 결정** | 이력 적재 용도이므로 INSERT 등 DML 허용. 현행 유지 |
| A-6 | #30 (UNION 차단) | UNION ALL을 허용할 것인가 | **input_sanitizer는 유지, security.py(LLM 생성 SQL 검사)에서는 UNION 차단 제거** | 사용자 자연어 입력의 UNION SELECT 패턴 차단은 인젝션 방어로 유지. LLM이 생성한 SQL의 정당한 UNION ALL은 허용해야 "합쳐서 보여줘" 유형 처리 가능 |
| A-7 | #37 (프롬프트 경량화) | 폐쇄망 모델의 컨텍스트 윈도우에 맞는 Few-shot 수 | **제외 — 현재 3만 토큰 수준으로 문제 없음** | 컨텍스트 윈도우 내 충분히 수용 가능. 축소 불필요 |

### B. 개선방안 상세화 필요 (방향은 명확하나 구현 세부가 부족)

| # | 이슈 | 부족한 부분 | 보완 필요 사항 |
|---|------|-----------|---------------|
| B-1 | #2 (PII 로그 마스킹) | ~~어떤 컬럼을 PII로 판정할 것인가~~ | **미적용으로 결정** — 로그 마스킹은 당분간 현행 유지 |
| B-2 | #11 (redis_store None 방어) | `self._client = None` 상태에서 메서드 호출 시 AttributeError 크래시 | **B안 채택 (명시적 RuntimeError)** — 각 메서드에 `if not self._client: raise RuntimeError("Redis 미연결")` 추가. 현재 `session_backend=memory` 기본값이므로 RedisSessionStore 미사용 중 → **런타임 영향 없음**. Redis 활성화 시점에 적용 |
| B-3 | #20, #21 (커넥터 중복 통합) | Hive/Impala 95% 중복 | **통합 안함으로 결정** — 폐쇄망 실 연동 전이므로 현행 유지 |
| B-4 | #28 (프롬프트 인젝션 경계 마커) | LLM 프롬프트에 외부 DB 데이터를 `{변수}`로 직접 삽입하는 2곳 | **TODO에만 기록, 현재 미적용** — `context_interpreter_system.txt` L26 `{tool_results}`, `formatter_system.txt` L25 `{code_mappings}`에 경계 마커 추가 필요. 향후 적용 예정 |
| B-5 | #29 (input_sanitizer 오탐) | `input_sanitizer.py` L41의 `r"--"` 패턴이 자연어 입력을 잘못 차단 | **스킵** — 해당 패턴(`--`)으로 차단되는 실사용 빈도 극히 낮아 현행 유지 |

### C. 수정하면 안 되는 항목 (확인 완료)

아래 항목들은 리뷰에서 이슈로 도출되었으나, 의도된 설계이므로 **수정하면 안 됩니다**.

| 원래 ID | 항목 | 수정하면 안 되는 이유 |
|---------|------|---------------------|
| ~~INT-04~~ | `get_compiled_app` 싱글턴 초기화 순서 | `lifespan`이 순서 보장. CLI 모드에서 checkpointer 없이 실행은 의도된 설계 |
| ~~LOG-02~~ | `connect_all()` 매 요청 호출 | `_connected` 플래그로 멱등성 보장. CLI 모드 + WebSocket 양쪽 지원용 방어적 패턴 |
| ~~W-ERR-04~~ | `connect_all()` 한 커넥터 실패 시 전체 중단 | `enabled_connectors`는 "필수" 의미론. 비필수는 목록에서 빼면 됨. fail-fast 의도 |
| ~~SEC-11~~ | formatter.py `<details>` XSS | `trace_summary`는 서버 내부 고정 문자열만 사용. 사용자 입력 주입 경로 없음 |
| ~~SEC-06 Sybase~~ | Sybase 커넥터 비밀번호 | ODBC/키워드 인자 방식 연결. f-string URL 문제 해당 없음 |

---

## 상세 리뷰 보고서 참조

| 영역 | 보고서 |
|------|--------|
| 코어 파이프라인 | `20260406-core-pipeline-review-report.md` |
| Interpret 계층 | `20260406-interpret-layer-review-report.md` |
| Reason 계층 | `20260406-reason-layer-security-review-report.md` |
| Present 계층 | `20260406-present-layer-code-review-report.md` |
| 커넥터/설정 | `20260406-connector-config-utility-review-report.md` |
| 프롬프트/프론트엔드 | `20260406-prompts-frontend-review-report.md` |
| 검증 (오탐 확인) | `20260406-issue-verification-report.md`, `20260406-present-layer-issue-verification-report.md`, `20260406-connector-sanitizer-issue-verification-report.md`, `20260406-false-positive-verification-report.md` |
