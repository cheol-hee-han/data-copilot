# 코드 리뷰: 로깅 인프라 + 보안 헤더 + 기동 검증 변경 세트

**일자**: 2026-04-05
**범위**: logger.py, config.py, main.py, gunicorn.conf.py, tracker/*, 커넥터 8개, services 3개, truncate.py
**등급 기준**: Critical(RED) / Warning(YEL) / Info(GRN)

---

## 1. Critical (RED)

### C-01. `_mask_sensitive_processor` 에서 `event_dict` 직접 변경 -- 콘솔 출력에도 마스킹 적용

**파일**: `src/utils/logger.py` L422-436
**현상**: `_mask_sensitive_processor`는 프로세서 체인에서 `_file_logging_processor`보다 **앞에** 위치한다 (L477-479). 이 프로세서가 `event_dict`의 값을 직접 교체(`event_dict[key] = "****"`)하므로, 이후 실행되는 `_file_logging_processor`와 `console_renderer` 양쪽 모두에서 마스킹된 값만 보게 된다. 이는 **의도된 동작**이지만, 만약 디버깅 목적으로 콘솔에서만 원본 값을 보고 싶은 경우 전혀 불가능하다.

**판정**: 현재는 보안 정책상 올바른 설계이다. 다만 이 결정이 **의도적**임을 docstring에 명시해야 한다. "콘솔/파일 양쪽 모두 마스킹이 적용된다"는 한 줄을 추가할 것.

**우선순위**: Low (문서화만 필요)

---

### C-02. `_file_logging_processor`에서 `dict(event_dict)` 얕은 복사 -- 중첩 dict/list 공유 위험

**파일**: `src/utils/logger.py` L391
```python
file_output = _file_renderer(logger, name, dict(event_dict))
```

**현상**: `dict(event_dict)`는 얕은 복사(shallow copy)다. `event_dict` 값에 중첩된 dict나 list가 있을 경우 `_file_renderer`가 `.pop()` 호출(L211, L214, L218, L221)로 키를 제거하면 **원본 event_dict에는 영향 없지만**, 값이 mutable 객체인 경우 이후 `console_renderer`가 변경된 중첩 객체를 참조할 수 있다.

**현재 실제 위험도**: `_file_renderer`는 `.pop()`으로 최상위 키만 제거하고, 중첩 값은 읽기만 하므로 **현재 코드에서는 문제 없다**. 그러나 향후 `_file_renderer` 수정 시 실수로 중첩 값을 변경하면 콘솔 출력이 깨진다.

**권장**: `copy.deepcopy` 대신 현재 `dict()` 유지하되, 주석으로 "shallow copy -- 중첩 값은 읽기만 할 것" 경고를 남길 것.

---

### C-03. `_DualWriter._write_to_handler`에서 LogRecord 생성 -- 로그 레벨 무시

**파일**: `src/utils/logger.py` L340-341
```python
record = logging.LogRecord("", 0, "", 0, msg, (), None)
```

**현상**: `shouldRollover(record)`에 전달되는 LogRecord의 `level`이 항상 0(NOTSET)이다. `TimedRotatingFileHandler.shouldRollover`는 시간 기반이므로 레벨을 참조하지 않아 **현재는 문제 없다**. 그러나 향후 `RotatingFileHandler`(크기 기반)로 전환하거나 커스텀 필터를 추가할 경우 예상치 못한 동작이 발생할 수 있다.

**권장**: 현재는 무시 가능. TimedRotatingFileHandler 전용이라는 주석 추가.

---

## 2. Warning (YEL)

### W-01. `_mask_sensitive_processor`의 lazy import 매 호출

**파일**: `src/utils/logger.py` L421
```python
from src.utils.security import mask_pii  # lazy import (순환 참조 방지)
```

**현상**: 이 프로세서는 **모든 로그 메시지마다** 호출된다. 매번 `from src.utils.security import mask_pii`를 실행한다. Python의 import 시스템은 이미 로드된 모듈은 `sys.modules`에서 캐시하므로 **첫 호출 이후 오버헤드는 미미**하지만, import resolution 자체가 lock을 잡기 때문에 고빈도 호출 시 미세한 지연이 누적될 수 있다.

**권장**: 모듈 수준 변수로 캐시하는 패턴을 사용.
```python
_mask_pii_fn = None

def _mask_sensitive_processor(...):
    global _mask_pii_fn
    if _mask_pii_fn is None:
        from src.utils.security import mask_pii
        _mask_pii_fn = mask_pii
    # 이후 _mask_pii_fn(value) 사용
```

---

### W-02. `Settings.__repr__` 마스킹 키워드와 `_SENSITIVE_KEY_PARTS` 불일치

**파일**: `src/config.py` L320, `src/utils/logger.py` L65-67

| 위치 | 키워드 |
|------|--------|
| `Settings.__repr__` | `"password"`, `"api_key"`, `"secret"` |
| `_SENSITIVE_KEY_PARTS` | `"password"`, `"api_key"`, `"secret"`, `"token"`, `"credential"` |

**현상**: `Settings.__repr__`에 `"token"`, `"credential"`이 누락되어 있다. 현재 Settings 필드 중 `token` 또는 `credential`을 포함하는 필드는 없으므로 **당장 노출 위험은 없다**. 그러나 향후 필드 추가 시 누수 가능.

**권장**: `Settings.__repr__`의 `_mask_keywords`를 `_SENSITIVE_KEY_PARTS`와 동일하게 맞추거나, logger.py의 상수를 공유 모듈로 추출하여 단일 진실(single source of truth)로 관리.

---

### W-03. `health_check_timeout` 타입 불일치 가능성

**파일**: `src/config.py` L219
```python
health_check_timeout: float = 5.0
```
**파일**: `src/connectors/manager.py` L143
```python
timeout = settings.health_check_timeout
```
**파일**: `asyncio.wait_for` 시그니처: `timeout: float | None`

**현상**: 문제 없음. float 타입이 일관되게 전달된다. 다만 `settings.health_check_timeout`에 0 이하 값을 설정하면 `asyncio.wait_for`가 즉시 타임아웃(0) 또는 비활성(None)이 되어야 하는데, 현재 유효성 검증이 없다.

**권장**: `Field(gt=0)` 또는 `@field_validator`로 양수 제약을 추가.

---

### W-04. `gunicorn.conf.py` 멀티워커 경고는 주석뿐 -- 런타임 가드 없음

**파일**: `gunicorn.conf.py` L11-26

**현상**: workers > 1 시 임베딩 모델 메모리, 로그 파일 충돌, DB 풀 초과 위험을 주석으로 경고하고 있으나, 런타임에서 workers 값을 검증하는 코드가 없다. 운영팀이 주석을 읽지 않고 `workers=4`로 변경할 수 있다.

**권장**: `post_fork` 훅에서 workers 수를 로깅하고, LOG_FORMAT이 json이 아닌 상태에서 workers > 1이면 stderr에 WARNING을 출력하는 가드 추가.

---

### W-05. `_get_files_to_delete` 클로저가 `handler` 참조 -- 잠재적 GC 순환

**파일**: `src/utils/logger.py` L287-301

**현상**: `_get_files_to_delete` 클로저가 외부 스코프의 `handler` 객체를 캡처한다. `handler.getFilesToDelete = _get_files_to_delete`로 할당하면 `handler -> _get_files_to_delete -> handler` 순환 참조가 형성된다. Python GC가 순환 참조를 처리하지만, 파일 핸들러 특성상 `__del__` 시점이 지연되면 파일 디스크립터 누수가 발생할 수 있다.

**권장**: `weakref` 사용 또는 `handler.baseFilename`만 캡처하여 순환을 끊을 것.
```python
_base_filename = handler.baseFilename  # str은 immutable, 순환 없음

def _get_files_to_delete() -> list[str]:
    dir_name = os.path.dirname(_base_filename)
    ...
```

---

### W-06. `RedisSessionStore` 메서드들에 `self._client is None` 가드 없음

**파일**: `src/services/session/redis_store.py` L91-95

```python
async def get_history(self, session_id: str) -> list[dict[str, str]]:
    key = self._key(session_id, "history")
    raw = await self._client.get(key)  # self._client가 None이면 AttributeError
```

**현상**: `connect()` 호출 전에 `get_history`, `append_history`, `clear_session`을 호출하면 `AttributeError: 'NoneType' object has no attribute 'get'`이 발생한다. `health_check`에만 None 가드가 있다.

**권장**: 기본 방어로 메서드 시작부에 `if not self._client: raise RuntimeError("Redis 미연결")` 또는 `return []` 처리 추가.

---

### W-07. `connect_all`에서 순차 연결 -- 병렬화 가능

**파일**: `src/connectors/manager.py` L96-117

**현상**: 6개 커넥터를 순차적으로 `await connector.connect()`한다. 각 커넥터가 네트워크 I/O를 수반하므로, `asyncio.gather`로 병렬화하면 기동 시간을 단축할 수 있다.

**권장**: `health_check_all`처럼 `asyncio.gather`로 병렬 연결. 단, 의존관계(예: Qdrant가 MongoDB에 의존)가 있으면 순서 보장 필요. 현재 코드에서는 커넥터 간 의존이 없으므로 안전하게 병렬화 가능.

---

### W-08. ElasticSearch 관련 설정 잔존

**파일**: `src/config.py` L87-98

**현상**: 프로젝트 메모리에 "ES 완전 제거, 메타는 MongoDB, SQL이력은 Qdrant"로 기록되어 있으나, `config.py`에 ES 관련 설정(es_host, es_port, es_user 등 12개 필드)이 여전히 남아있다. `ElasticSearchConnector`도 manager에서 여전히 초기화된다.

**권장**: ES 제거 로드맵이 확정되었다면 단계적으로:
1. `config.py`에서 ES 필드에 `deprecated` 주석 추가
2. `manager.py`에서 ES 커넥터 초기화를 조건부로 변경 (사용하는 코드가 없으면 skip)

---

## 3. Info (GRN)

### I-01. 커넥터 8개의 `health_check` except 패턴 일관성 -- 양호

**변경 내용**: 기존 `except Exception: return False` (silent) 에서 `logger.debug("health_check 실패", error=str(e))`로 통일.

**평가**: 모든 커넥터(ES, Mongo, Neo4j, Qdrant, Postgres x2, Impala, Hive, Sybase, Redis)에서 일관되게 `logger.debug` 패턴이 적용되었다. `manager.health_check_all`의 `_safe_check`에서도 exception을 `logger.debug`로 기록하므로 이중 로깅이 발생하지만, debug 레벨이므로 운영 환경에서는 출력되지 않아 문제 없다.

---

### I-02. tracker `callback_handler.py`, `dispatch.py` stdlib->structlog 전환 -- 양호

**변경 내용**: `from src.utils.logger import get_logger` + `logger = get_logger(__name__)` 패턴으로 통일.

**평가**: structlog의 `FilteringBoundLogger` 타입이 올바르게 사용되고 있다. `dispatch.py`의 `logger.debug("tracking event dispatch 실패", event=name)` 호출 형식도 structlog 키워드 인자 패턴을 따르고 있어 정상.

---

### I-03. `main.py` 보안 헤더 미들웨어 -- 적절

**변경 내용**: `SecurityHeadersMiddleware` 추가 (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Cache-Control).

**평가**:
- `/vendor` 정적 파일 경로에 Cache-Control 예외를 둔 것은 적절
- `Content-Security-Policy` (CSP) 헤더가 없음 -- XSS 방어를 위해 추후 추가 권장
- `X-XSS-Protection` 미포함 -- 현대 브라우저에서 deprecated이므로 불필요

---

### I-04. `main.py` lifespan try/finally 패턴 -- 양호

**변경 내용**: 기동 실패 시에도 `store.disconnect()`, `manager.disconnect_all()`, `shutdown_logging()`이 보장됨.

**평가**: 리소스 누수 방지에 효과적인 설계. `shutdown_logging()`이 finally 블록의 마지막에 위치하여 종료 직전까지 로깅이 가능한 점도 적절.

---

### I-05. `main.py` health 엔드포인트 3분할 -- 양호

**변경 내용**: `/health`, `/health/live`, `/health/ready` 분리.

**평가**: Kubernetes liveness/readiness probe 패턴을 정확히 따르고 있다. `/health/live`는 외부 의존성 없이 즉시 200을 반환하여 hang 위험이 없고, `/health/ready`는 필수 커넥터만 검사하여 503을 반환한다.

---

### I-06. `main.py` 글로벌 예외 핸들러 -- 양호

**변경 내용**: `ValidationError` 핸들러(422)와 `Exception` 핸들러(500) 추가.

**평가**: 사용자에게 내부 스택 트레이스를 노출하지 않으며, 서버 로그에는 에러를 기록한다. `code-style.md`의 "에러 메시지에 내부 정보 포함 금지" 규칙을 준수. WebSocket은 이 핸들러를 거치지 않는다는 주석도 정확.

---

### I-07. `config.py` DB 풀 설정 -- 양호

**변경 내용**: `db_pool_size`, `db_pool_max_overflow`, `db_pool_recycle` 추가.

**평가**: `postgres_connector.py`에서 `pool_pre_ping=True` + `pool_recycle=1800`으로 stale connection 방어가 적절. `gunicorn.conf.py` 주석에서 workers x pool_size 계산 가이드도 제공.

---

### I-08. `logger.py` 구조 및 계층 -- 양호

**파일 내 배치 순서**:
1. 상수 정의 (L43-76)
2. 포맷팅 유틸리티 (L78-147)
3. 파일 렌더러 (L149-258)
4. 핸들러 팩토리 (L261-305)
5. DualWriter 클래스 (L308-368)
6. 프로세서 함수들 (L375-436)
7. 공개 API (L439-524)

의존 방향이 위에서 아래로 단방향이며, 공개 API가 가장 아래에 위치하여 논리적이다.

---

### I-09. 명명 규칙 일관성 -- 양호

| 패턴 | 예시 | 비고 |
|------|------|------|
| private 모듈 변수 | `_LOG_DIR`, `_ANSI_RE`, `_file_writer` | `_` 접두사 일관 |
| private 메서드 | `_write_to_handler`, `_emit_progress` | `_` 접두사 일관 |
| 상수 (frozenset) | `_WARNING_LEVELS`, `_SENSITIVE_KEY_PARTS` | UPPER_CASE 일관 |
| 설정 필드 | `log_backup_count`, `db_pool_size` | snake_case 일관 |

---

### I-10. `truncate.py` lazy import 패턴 -- 기능적으로 정상이나 개선 여지

**파일**: `src/utils/truncate.py` L29, L35

```python
def truncate_trace(val: str) -> str:
    from src.config import settings
    return _truncate(val, settings.trace_truncate_limit)
```

**현상**: 매 호출마다 `from src.config import settings`를 실행한다. `settings`는 모듈 수준 싱글턴이므로 `sys.modules` 캐시에서 즉시 반환되어 성능 문제는 미미하지만, 이 패턴의 이유(순환 참조 방지)를 주석으로 명시하면 가독성이 향상된다.

---

### I-11. `_DualWriter` docstring에 lock 메커니즘 설명 -- 양호

`_write_to_handler` 메서드의 `with handler.lock` 사용과 docstring "(롤링 체크 포함, 스레드 안전)"이 정확하게 대응한다. gunicorn 멀티워커에서 프로세스 간 보호 불가라는 한계도 `gunicorn.conf.py` 주석에서 명확히 안내하고 있다.

---

## 4. 요약

| 등급 | 건수 | 핵심 항목 |
|------|------|-----------|
| Critical (RED) | 0건 실질 | C-01~C-03은 현재 코드에서 문제 없으며 문서화/주석 보강만 권장 |
| Warning (YEL) | 8건 | W-01(lazy import 캐시), W-02(마스킹 키 불일치), W-05(클로저 순환), W-06(Redis None 가드), W-07(connect 병렬화) 가 실행 가능한 개선 사항 |
| Info (GRN) | 11건 | 전반적으로 일관되고 적절한 설계 |

## 5. 권장 조치 우선순위

1. **W-06** RedisSessionStore None 가드 -- 런타임 에러 방지 (즉시)
2. **W-05** 클로저 순환 참조 -- `_base_filename` 캡처로 변경 (즉시)
3. **W-02** 마스킹 키워드 통합 -- 보안 누수 방지 (이번 주)
4. **W-01** lazy import 캐시 -- 성능 미세 개선 (다음 스프린트)
5. **W-07** connect_all 병렬화 -- 기동 시간 단축 (다음 스프린트)
6. **W-08** ES 설정 정리 -- 코드 정리 (로드맵에 따라)

---

## 6. 종합 평가

이번 변경 세트는 로깅 인프라를 체계적으로 강화하고, 보안 헤더/글로벌 예외 핸들러/기동 시 연결 검증 등 운영 안정성 요소를 적절히 추가했다. 특히:

- **structlog 통합**: stdlib logging에서 structlog로의 전환이 프로젝트 전반에 걸쳐 일관되게 적용됨
- **이중 출력(콘솔+파일)**: `_DualWriter` + 프로세서 체인 패턴이 관심사 분리 원칙을 잘 따름
- **PII/Secrets 마스킹**: 프로세서 체인에 자연스럽게 통합되어 모든 로그에 자동 적용
- **health_check 타임아웃**: `asyncio.wait_for` + `gather` 조합으로 hang 방지와 병렬 실행을 동시 달성
- **Kubernetes 친화**: liveness/readiness 분리, graceful shutdown, 보안 헤더 등 프로덕션 운영 요소 충실

Warning 항목들은 모두 "현재는 문제 없으나 향후 위험을 줄이기 위한 방어적 개선"에 해당하며, 아키텍처적 결함은 발견되지 않았다.
