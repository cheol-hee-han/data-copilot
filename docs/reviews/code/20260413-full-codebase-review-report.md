# 전체 소스코드 코드리뷰 보고서

**검토일**: 2026-04-13
**검토 범위**: `src/` 전체 (109개 파일), `resources/` (58개), `tests/` (86개)
**검토 방법**: 6개 영역 병렬 심층 리뷰 (Pipeline Core, Interpret, Reason, Present+Infra, Prompts+Resources)
**재검토**: 초안 작성 후 실제 코드 대조를 통해 과대 판단 항목을 재분류함
**이전 리뷰**: 20260406-full-codebase-review-summary.md

---

## 요약 통계

| 심각도 | 건수 | 핵심 테마 |
|--------|------|-----------|
| **Warning** | 12 | 로직 버그, 코드 중복, 커넥션 안전성, 프롬프트 표준화, CORS/PII |
| **Info** | 20 | Dead code, 네이밍, 성능, 구조 개선, 방어 강화 |

> **Critical 0건**: 초안에서 10건 Critical로 분류했으나 재검토 결과 모두 하향 조정됨.
> 주된 이유: (1) tools.py SQL 삽입은 LLM 도구 호출 경로이며 식별자 화이트리스트+SELECT 전용 커넥터로 방어됨,
> (2) 다운로드 캐시는 UUID4 기반으로 추측 불가, (3) 에러 메시지는 user-facing content가 이미 안전,
> (4) ainvoke에 루프 가드(MAX_GENERATES/MAX_REPLANS/MAX_TOOL_CALLS)가 존재.

---

## 1. WARNING -- 조기 수정 권장

### 로직 버그 (W-01 ~ W-02)

#### W-01. agg_function "NONE" 값이 truthy로 평가 → false positive FAIL

- **파일**: `src/agents/nodes/reason/sql_validator.py:531`
- **카테고리**: 로직 버그
- **상세**: `any(m.get("agg_function") for m in measures)` — `"NONE"` 값이 truthy로 평가되어 decomposition에 집계함수가 없는데 있는 것으로 판단. 불필요한 FAIL 발생.
- **수정안**:

```python
concrete_aggs = {"SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MAX", "MIN"}
has_agg_in_decomp = any(
    m.get("agg_function", "").upper() in concrete_aggs for m in measures
)
```

### 코드 중복 (W-02 ~ W-04)

#### W-02. AmbiguitySignal dict 변환 로직 중복

- **파일**: `src/agents/nodes/interpret/intent_classifier.py:213-232`, `src/agents/nodes/interpret/query_normalizer.py:157-171`
- **카테고리**: 코드 중복 (DRY)
- **상세**: 두 파일 모두 `dict → AmbiguitySignal` 변환을 인라인으로 수행하며 동일한 `.get()` 패턴과 폴백 값 사용.
- **수정안**: `AmbiguitySignal`에 `from_llm_dict()` 팩토리 메서드 추가하여 통합

#### W-03. context_interpreter + recovery_agent 중복 코드

- **파일**: `src/agents/nodes/reason/context_interpreter.py:322-338`, `src/agents/nodes/reason/recovery_agent.py:687-703`
- **카테고리**: 코드 중복
- **상세**: `_VALUE_LABEL`, `_ROLE_DESC` 딕셔너리와 `_serialize_unresolved_items` 함수가 양쪽에 완전 중복. 반환 타입도 다름(str vs list[str]).
- **수정안**: `src/agents/nodes/reason/knowledge_serializers.py` 공용 모듈로 추출

#### W-04. intent_classifier _parse_response에서 extract_json 미사용

- **파일**: `src/services/intent_classifier.py:230-231`
- **카테고리**: 일관성 / 견고성
- **상세**: 코드블록 마커 제거 후 `json.loads()` 직접 호출. LLM이 JSON 전후에 설명 텍스트를 추가하면 파싱 실패. `query_normalizer.py`에서는 이미 `extract_json(raw, strict=True)`를 사용 중.
- **수정안**: `extract_json` 사용으로 통일

---

### 인프라/커넥터 (W-06 ~ W-07)

#### W-06. ADWConnector 단일 커넥션 공유

- **파일**: `src/connectors/impl/adw_connector.py:121-128, 184-201`
- **카테고리**: 동시성 / 안정성
- **상세**: 단일 `self._conn`을 모든 요청이 공유. `asyncio.to_thread()`로 스레드 풀에서 실행하지만, 동시 요청 시 커넥션 상태 오염 가능. Sybase IQ 커서는 thread-safe하지 않음.
- **수정안**: 세마포어 기반 커넥션 풀(N=3~5) 구현 또는 쿼리별 커넥션 생성/반환

#### W-07. WebSocket Rate Limiting 없음

- **파일**: `src/main.py:550-639`
- **카테고리**: 보안 / DoS
- **상세**: 메시지 수/속도 제한 없음. `mark_active`/`clear_active`로 세션 단위 동시 실행은 추적하지만 메시지 유입 자체는 무제한. 악의적 클라이언트가 빠르게 연속 전송하면 LLM 비용 증가.
- **참고**: 은행 내부망 환경에서 실질적 위험은 낮으나, 폐쇄망 배포 시에도 세션당 쿨다운(예: 2초) 적용 권장

---

### 프롬프트 표준화 (W-08 ~ W-10)

#### W-08. intent_classifier_system.txt [TAG] 형식 미사용

- **파일**: `resources/prompts/interpret/intent_classifier_system.txt`
- **카테고리**: 프롬프트 품질
- **상세**: 유일하게 `[HALLUCINATION_GUARD]`, `[FORMAT_LOCK]` 섹션이 없음. 다른 모든 프롬프트는 `[TAG]` 구조 채택. Qwen3.5 이관 시 가장 취약한 프롬프트.
- **수정안**: `[TAG]` 구조 + `[HALLUCINATION_GUARD]` + `[FORMAT_LOCK]` 추가

#### W-09. sql_validator_system.txt 호칭 불일치

- **파일**: `resources/prompts/reason/sql_validator_system.txt` (L4)
- **카테고리**: 일관성
- **상세**: "너는 생성된 SQL의..." — 다른 모든 프롬프트는 "당신은" 사용.
- **수정안**: "당신은 생성된 SQL의..."로 변경

#### W-10. query_normalizer_phase2 가드레일/예시 부족

- **파일**: `resources/prompts/interpret/query_normalizer_phase2_system.txt`
- **카테고리**: 프롬프트 품질
- **상세**: R1~R12 규칙 중 R1, R2만 예시 있음. `[HALLUCINATION_GUARD]` 미적용. 복잡한 규칙(R4 TREND, R7 implicit filters, R10 search_keywords)에 예시 없음.
- **수정안**: 최소 R4, R7, R10 예시 추가 + `[HALLUCINATION_GUARD]` 섹션 추가

---

### 설정/배포 (W-11 ~ W-13)

#### W-11. CORS allow_origins=["*"] 환경변수 미연동

- **파일**: `src/main.py:250`
- **카테고리**: 배포 준비
- **상세**: 주석에 "운영 시 특정 도메인으로 제한 필요"라고 명시되어 있으나, `settings`를 통한 설정 메커니즘이 없어 코드 수정 없이는 변경 불가.
- **수정안**: `cors_allowed_origins: list[str]`를 Settings에 추가하고 참조

#### W-12. 다운로드 엔드포인트 PII 마스킹 미적용

- **파일**: `src/main.py:785-825`
- **카테고리**: 데이터 보호
- **상세**: SQL 생성 프롬프트가 PII 컬럼 선택을 억제하고, 포맷 응답은 `mask_pii()`를 거치지만, 다운로드 CSV/JSON은 `sql_result` 원본을 그대로 반환. LLM이 PII 컬럼을 포함한 SQL을 생성하는 예외 상황에서 마스킹 누락.
- **수정안**: 다운로드 데이터에 컬럼 레벨 마스킹 적용 (sql_safety_checker.MASKING_COLUMNS 활용)

#### W-13. turn_text_store stdlib logging 사용

- **파일**: `src/services/turn_text_store.py:29`
- **카테고리**: 일관성
- **상세**: 유일하게 `import logging` 사용. structlog 체인(PII 마스킹, KST 타임스탬프, 파일 출력, contextvars)을 우회함.
- **수정안**: `from src.utils.logger import get_logger` 전환

---

## 2. INFO -- 점진적 개선 권장

### 방어 강화 (현재도 동작하지만 더 견고하게)

#### I-01. ainvoke에 명시적 timeout 추가 권장

- **파일**: `src/agents/graph/runner.py:295-312`
- **상세**: LLM 클라이언트에 `settings.llm_long_timeout`, 루프 가드에 `MAX_GENERATES`/`MAX_REPLANS`/`MAX_TOOL_CALLS`가 있어 현재도 무한 대기는 방지됨. 다만 `settings.agentic_total_timeout`(180s)을 belt-and-suspenders로 ainvoke에 적용하면 예상치 못한 hang 방어 가능.
- **수정안**: `async with asyncio.timeout(settings.agentic_total_timeout):` 래핑

#### I-02. 프롬프트 placeholder 경계 태그 추가 권장

- **파일**: 전체 프롬프트의 `{{USER_QUERY}}`, `{{CONTEXT}}` 등
- **상세**: 입력 단계에서 `detect_prompt_injection()` + `sanitize()`가 이미 동작하여 기본 방어는 갖춰짐. 경계 태그(`[USER_INPUT]...[/USER_INPUT]`)는 defense-in-depth로 소형 LLM의 경계 인식을 강화하는 효과. Qwen3.5 이관 시 적용 권장.

#### I-03. 에러 턴 저장 시 error_message 범위 축소 고려

- **파일**: `src/agents/graph/runner.py:509-510`
- **상세**: `error_type=type(e).__name__`, `error_message=str(e)` — 사용자에게 보여지는 `content`는 이미 `"처리 중 오류가 발생했습니다."`로 안전함. `error_message`는 DB 진단 필드로, 관리자 API를 통해 노출될 수 있으나 현재 그러한 API는 없음. 향후 관리 화면 구축 시 마스킹 필요.

#### I-04. 다운로드 캐시 UUID 기반 — 추측 불가하나 TTL 부재

- **파일**: `src/main.py:738-764`
- **상세**: session_id가 UUID4 기반이라 실질적 추측은 불가. 다만 캐시에 TTL이 없어 서버 재시작 전까지 메모리에 잔존. 동시 100세션 × 10,000행 규모의 메모리 사용 가능.
- **수정안**: TTL 기반 만료(5분) 또는 `functools.lru_cache` 활용

#### I-05. checkpointer pool open/wait에 timeout 명시 권장

- **파일**: `src/agents/graph/checkpointer.py:74-82`
- **상세**: `AsyncConnectionPool` 생성자에 `timeout` 파라미터가 있으나 명시하지 않아 라이브러리 기본값 사용. `lifespan`에서 호출되므로 DB 미연결 시 서버 기동이 지연될 수 있으나, 이 경우 lifespan 실패로 서버가 기동되지 않아 올바른 동작.

#### I-06. 비밀번호 기본값 빈 문자열

- **파일**: `src/config.py:49, 64, 73 등`
- **상세**: `use_dummy=True`(개발 모드) 기본값에서는 DB 연결을 하지 않으므로 문제없음. `use_dummy=False`로 전환 시 `.env` 파일이 필수. fail-fast validator 추가는 좋으나 현재 동작에 문제는 없음.

---

### 코드 품질

#### I-07. tools.py SQL 조립 방식 — 현행 유지 합리적

- **파일**: `src/agents/nodes/reason/tools.py:280-321`
- **상세**: 초안에서 Critical로 분류했으나 재검토 결과 하향. `keyword`는 LLM 도구 호출에서 생성된 값이며(사용자 직접 입력 아님), 테이블/컬럼명은 `_IDENT_RE` 화이트리스트로 검증, 모든 커넥터가 SELECT/WITH만 허용. LIKE 와일드카드(`%`, `_`)는 검색 기능의 의도된 동작. Impala/Hive가 파라미터 바인딩을 지원하지 않으므로 현행 f-string + 이스케이프 방식이 전 DB 호환 현실적 선택.
- **참고**: `limit` 파라미터(C-02로 지적했던 항목)도 항상 함수 시그니처의 int 기본값(10, 20, 30)이며 호출부에서 사용자 입력이 전달되지 않음. 이슈 아님.

#### I-08. get_compiled_app 싱글턴 checkpointer 주의

- **파일**: `src/agents/graph/pipeline.py:614-625`
- **상세**: 첫 호출의 checkpointer가 고정되지만, 실제로는 `lifespan`이 항상 먼저 호출되어 실제 checkpointer를 전달. 경고 로그 추가는 좋으나 현재 동작에 버그 없음.

#### I-09. _execute_and_finalize 245행 복잡도

- **파일**: `src/agents/graph/runner.py:269-514`
- **상세**: 그래프 실행, interrupt 감지, 명확화/정상/에러 턴 저장을 모두 처리하여 길지만, 단일 트랜잭션 흐름을 하나의 함수에서 관리하는 것이 오히려 문맥 파악에 유리. 무리하게 분해하면 흐름 추적이 어려워질 수 있음. 현행 유지 합리적.

#### I-10. bare raise 제어 흐름 (runner.py:495)

- **파일**: `src/agents/graph/runner.py:494-495`
- **상세**: `if _pool is None: raise` — pool이 없으면 턴 저장을 모두 건너뛰고 원래 예외를 재발생. 기능적으로 올바르지만 제어 흐름이 직관적이지 않음. `pass` + 조건 분기가 더 명확하나, 현재 동작에 버그는 없음.

#### I-11. 로그에 user_input 원문

- **파일**: `src/agents/graph/runner.py:134-138`
- **상세**: 은행 직원의 데이터 조회 질의("이번 달 여신 실행 건수")에 PII가 포함될 가능성은 낮음. `mask_for_logging` 적용은 방어적으로 좋으나 실질적 위험은 미미. 내부 서버 로그이며 외부 노출 경로 없음.

#### I-12. 명확화 답변 검증 실패 시 원문 사용

- **파일**: `src/agents/nodes/interpret/clarification_handler.py:183-190`
- **상세**: `validate_answer` 실패 시 원문을 사용하지만, WebSocket 입력 단계에서 이미 `sanitize()` + `detect_prompt_injection()`이 적용됨. 추가 길이 제한(500자)은 방어적으로 좋으나 현재 입력 단계에서 `max_length=2000`으로 이미 제한됨.

---

### Dead Code / 정리

#### I-13. 백업 프롬프트 파일 14개

- **파일**: `resources/prompts/` 하위 `*_org*`, `*_bak*`, `*_v2*`, `*_v3*` 파일들
- **상세**: `system_prompts.py`가 명시적 파일명으로 로드하므로 잘못 로드될 위험은 없음. 다만 디렉토리가 지저분해지므로 `resources/prompts/_archive/`로 이동 권장.

#### I-14. CLARIFICATION_MAX_TURNS 미사용

- **파일**: `src/agents/graph/pipeline.py:114`
- **상세**: 정의만 있고 참조 없음. dead code. 삭제 권장.

#### I-15. ES 설정 주석 블록 + ES 커넥터 잔존

- **파일**: `src/config.py:80-90`, `src/connectors/impl/elasticsearch_connector.py`
- **상세**: ES 완전 제거 확인됨(프로젝트 메모리). 설정 주석 삭제 + 커넥터 파일 제거 권장.

#### I-16. 미사용 import `from psycopg.sql import SQL`

- **파일**: `src/agents/nodes/reason/recovery_agent.py:31`
- **수정안**: 삭제

#### I-17. `del state` 비관례적 사용

- **파일**: `src/agents/graph/pipeline.py:427`
- **수정안**: 파라미터명을 `_state` 또는 `_`로 변경

#### I-18. _build_assumption_signals private prefix + 외부 import

- **파일**: `src/agents/nodes/reason/sql_generator.py:604`
- **상세**: `_` prefix이지만 `result_finalizer.py`에서 import. 공개 API로 사용됨.
- **수정안**: prefix 제거 또는 공용 유틸리티 모듈로 이동

---

### 성능/구조 개선

#### I-19. 토큰 추정 한국어 비율 미반영

- **파일**: `src/agents/nodes/reason/context_interpreter.py:384-385`
- **상세**: "1토큰=3자" 추정은 영어 기준. 한국어는 1토큰 ≈ 1.5~2자. 한국어 비중이 높은 프롬프트에서 토큰 예산 과소평가 가능.
- **수정안**: 추정치를 "1토큰=2자"로 조정

#### I-20. connect_all 순차 연결

- **파일**: `src/connectors/manager.py:119-144`
- **상세**: mongo, qdrant, postgres 순차 연결. `asyncio.gather()`로 병렬화하면 기동 시간 단축 가능.

---

## 3. 초안 대비 변경 내역

### 삭제/하향 사유 요약

| 초안 ID | 초안 심각도 | 변경 후 | 사유 |
|---------|-----------|---------|------|
| C-01 | Critical | **I-07** (Info) | LLM 도구 호출 경로, 사용자 입력 아님. `_IDENT_RE` 화이트리스트 + SELECT 전용 커넥터. Impala 파라미터 바인딩 미지원으로 현행 방식이 현실적 |
| C-02 | Critical | **삭제** | `limit`은 항상 함수 시그니처 int 기본값(10,20,30). 호출부에서 사용자 입력 전달 없음. 이슈 아님 |
| C-03 | Critical | **W-11** (Warning) | 개발 단계의 알려진 TODO. 프로덕션 미배포. 설정 가능화는 필요하나 Critical 아님 |
| C-04 | Critical | **I-04** (Info) | UUID4 기반 session_id는 실질적 추측 불가. 인증 시스템 자체가 미존재 (별도 과제) |
| C-05 | Critical | **I-03** (Info) | user-facing `content`는 이미 `"처리 중 오류가 발생했습니다."` (L506). `error_message`는 DB 진단 필드, 사용자 노출 API 없음 |
| C-06 | Critical | **W-12** (Warning) | SQL 생성 프롬프트가 PII 선택 억제. 예외 상황 대비로 Warning 유지 |
| C-07 | Critical | **I-01** (Info) | LLM `llm_long_timeout` + 루프 가드 3중(`MAX_GENERATES`/`MAX_REPLANS`/`MAX_TOOL_CALLS`) 존재. 추가 timeout은 belt-and-suspenders |
| C-08 | Critical | **I-10** (Info) | 기능적으로 올바름 (pool 없으면 저장 불가 → 건너뜀 → 원래 예외 전파). 제어 흐름만 비직관적 |
| C-09 | Critical | **I-13** (Info) | `system_prompts.py`가 명시적 파일명으로 로드. glob 아님. 실수 로드 위험 없음 |
| C-10 | Critical | **I-02** (Info) | `detect_prompt_injection()` + `sanitize()`가 입력 단계에서 동작. 경계 태그는 defense-in-depth |
| W-01 | Warning | **I-11** (Info) | 은행 질의에 PII 포함 가능성 낮음. 내부 서버 로그, 외부 노출 경로 없음 |
| W-04 | Warning | **삭제** | Layer 1 안전성 검증 이미 통과. sqlglot 파싱 실패는 방언 미지원. Layer 3(DB 실행)이 최종 검증 |
| W-08 | Warning | **삭제** | 주석에 설계 의도 명시. recovery_agent의 `replan_count`가 가드 역할. 의도된 설계 |
| W-11 | Warning | **삭제** (I-07에 통합) | `extra="allow"`는 Qdrant 반환 데이터의 유연한 수용을 위한 의도된 설정 |
| W-14 | Warning | **I-05** (Info) | lifespan에서 호출. DB 미연결 시 서버 기동 실패가 올바른 동작 |
| W-15 | Warning | **I-06** (Info) | `use_dummy=True` 기본값에서 DB 미연결. `.env` 필수는 배포 프로세스에서 관리 |
| W-16 | Warning | **삭제** | `$limit` stage가 파이프라인에 이미 존재. `to_list(length=None)`은 커서 드레인일 뿐 |
| W-17 | Warning | **W-13** (유지) | structlog 체인 우회는 실질적 일관성 문제 |
| W-18 | Warning | **I-12** (Info) | WebSocket 입력 단계에서 `sanitize()` + `detect_prompt_injection()` 이미 적용 |
| W-13 (_execute_and_finalize) | Warning | **I-09** (Info) | 단일 트랜잭션 흐름. 무리한 분해가 오히려 문맥 파악을 어렵게 할 수 있음 |

---

## 4. 수정 우선순위 로드맵

### Phase 1: 조기 수정 (2주 이내)

| 우선순위 | 항목 | 예상 난이도 |
|----------|------|-------------|
| 1 | W-01: agg_function "NONE" false positive 수정 | 하 |
| 2 | W-02: _clean_sql_response 비-SQL 방어 | 하 |
| 3 | W-05: extract_json 통일 | 하 |
| 4 | W-03+W-04: 중복 코드 팩토리/공용모듈 추출 | 중 |
| 5 | W-11: CORS 설정 가능화 | 하 |
| 6 | W-12: 다운로드 PII 마스킹 | 중 |
| 7 | W-13: turn_text_store structlog 전환 | 하 |

### Phase 2: 인프라 개선 (1개월 이내)

| 우선순위 | 항목 | 예상 난이도 |
|----------|------|-------------|
| 8 | W-06: ADWConnector 커넥션 안전성 | 중 |
| 9 | W-07: WebSocket 쿨다운 | 중 |
| 10 | W-08~10: 프롬프트 구조 표준화 (Qwen3.5 이관 전) | 중 |

### Phase 3: 점진적 정리

| 우선순위 | 항목 | 예상 난이도 |
|----------|------|-------------|
| 11 | I-13~17: Dead code/ES 잔존 정리 | 하 |
| 12 | I-19~20: 성능 최적화 | 하 |
| 13 | I-01~02: 방어 강화 (timeout, 경계 태그) | 중 |

---

## 5. 이전 리뷰(20260406) 대비 변화

- **개선된 점**: 명확화 핸들링, 다중턴 파이프라인, 추적 시스템이 크게 발전
- **새로 발견**: _clean_sql_response 비-SQL 반환(W-02), agg_function "NONE" 버그(W-01)
- **해소된 지적**: tools.py SQL injection은 재검토 결과 방어 계층이 충분. 이전 리뷰의 과대 판단 확인
- **지속 TODO**: CORS 설정(W-11), ES 잔존(I-15)은 이전에도 지적됨

---

*끝*
