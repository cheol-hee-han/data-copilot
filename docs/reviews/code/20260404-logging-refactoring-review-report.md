# 로깅 리팩토링 코드 리뷰 보고서

**일자**: 2026-04-04
**대상 파일**:
- `src/utils/logger.py`
- `src/config.py` (272-275행)
- `src/main.py` (54행, 106행)
- `src/utils/tracker/callback_handler.py` (34-57행, 297행, 731행, 791행, 881행)
- `src/utils/tracker/dispatch.py` (20-24행, 89-91행)

**리뷰 관점**: 보안, 성능, 타입 안전성, 에러 처리, 디자인 패턴, 명명 일관성, 유지보수성

---

## Critical (RED)

### C-01. `shutdown_logging()`이 private 속성에 직접 접근

**파일**: `src/utils/logger.py:411-412`

```python
_file_writer._handler.close()
_file_writer._error_handler.close()
```

`shutdown_logging()`은 모듈 레벨 함수(public)인데, `_DualWriter`의 private 속성 `_handler`, `_error_handler`에 직접 접근한다. 캡슐화 위반이며, `_DualWriter` 내부 구조 변경 시 외부 함수가 깨진다.

**해결안**: `_DualWriter`에 `close()` 메서드를 추가하고 `shutdown_logging()`에서 호출.

```python
# _DualWriter 내부
def close(self) -> None:
    """핸들러를 안전하게 종료한다."""
    self.flush()
    self._handler.close()
    self._error_handler.close()

# shutdown_logging()
def shutdown_logging() -> None:
    global _file_writer, _initialized
    if _file_writer is not None:
        _file_writer.close()
        _file_writer = None
    _initialized = False
```

---

### C-02. `flush()` 메서드의 스레드 안전성 결여

**파일**: `src/utils/logger.py:305-310`

```python
def flush(self) -> None:
    sys.stderr.flush()
    if self._handler.stream and not self._handler.stream.closed:
        self._handler.stream.flush()
    if self._error_handler.stream and not self._error_handler.stream.closed:
        self._error_handler.stream.flush()
```

`_write_to_handler()`는 `handler.lock`을 획득하여 스레드 안전하게 기록하지만, `flush()`는 lock 없이 `handler.stream`에 접근한다. 롤오버 중 stream이 교체되는 시점에 TOCTOU race가 발생할 수 있다 (check 시점과 flush 시점 사이에 stream이 닫힘).

**해결안**:

```python
def flush(self) -> None:
    sys.stderr.flush()
    for handler in (self._handler, self._error_handler):
        with handler.lock:
            if handler.stream and not handler.stream.closed:
                handler.stream.flush()
```

---

### C-03. `_file_renderer`에서 `log_level` / `level` 키 양쪽 pop으로 인한 데이터 소실

**파일**: `src/utils/logger.py:183-186`

```python
level = (
    event_dict.pop("log_level", None)
    or event_dict.pop("level", "info")
).upper()
```

`_file_logging_processor`가 `dict(event_dict)` 복사본을 `_file_renderer`에 전달하므로 원본 event_dict 자체는 보존된다. 그러나 `_file_renderer` 내부에서 `log_level`과 `level`을 모두 pop한 뒤 209행의 `_skip_keys`에도 같은 키를 포함시키고 있어 중복 방어가 되어 있긴 하다.

문제는 structlog의 `add_log_level` 프로세서가 주입하는 키는 `log_level`인데, `or` fallback으로 `level`도 pop하는 부분이다. 만약 `log_level`이 빈 문자열(`""`)이면 falsy로 평가되어 `level` 키까지 pop되지만, structlog 표준에서 빈 문자열 레벨은 발생하지 않으므로 현재는 실질적 문제가 없다. 다만 방어 코드의 의도가 불명확하므로 주석을 추가하거나 명시적으로 `None` 체크로 변경해야 한다.

**해결안**:

```python
level = event_dict.pop("log_level", None)
if level is None:
    level = event_dict.pop("level", "info")
level = level.upper()
```

---

## Warning (YELLOW)

### W-01. `_DualWriter.write()` - 콘솔 출력에 `sys.stderr` 하드코딩

**파일**: `src/utils/logger.py:281-283`

```python
def write(self, msg: str) -> None:
    """콘솔에 컬러 출력을 전달한다."""
    sys.stderr.write(msg)
```

`structlog.PrintLoggerFactory(file=_file_writer)`에 의해 `_file_writer.write()`가 콘솔 출력 경로로 사용된다. 이것은 structlog의 `PrintLogger`가 `file.write()`를 호출하는 구조를 이용한 것인데, 메서드명 `write()`가 "파일에 쓴다"로 오해될 수 있고, 실제로는 stderr에만 전달한다.

**해결안**: docstring을 보강하거나, 메서드를 `_console_write` 등 internal로 분리하고 `write`는 `PrintLogger` 인터페이스 준수를 위한 것임을 명확히 표시.

```python
def write(self, msg: str) -> None:
    """PrintLogger 인터페이스 구현 -- 콘솔(stderr)에 출력한다.

    파일 기록은 _file_logging_processor에서 write_file()을 별도 호출한다.
    """
    sys.stderr.write(msg)
```

---

### W-02. `_warning_levels`가 인스턴스 변수로 매번 생성됨

**파일**: `src/utils/logger.py:279`

```python
self._warning_levels = {"WARNING", "ERROR", "CRITICAL"}
```

이 set은 불변이며 모든 인스턴스에서 동일하다. 클래스 변수 또는 모듈 상수로 추출해야 한다.

**해결안**:

```python
_WARNING_LEVELS: frozenset[str] = frozenset({"WARNING", "ERROR", "CRITICAL"})

class _DualWriter:
    # ... 생성자에서 self._warning_levels 제거, 대신 _WARNING_LEVELS 참조
```

---

### W-03. `get_logger()` 반환 타입이 부정확

**파일**: `src/utils/logger.py:417-419`

```python
def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """모듈별 로거 생성."""
    return structlog.get_logger(name)
```

`structlog.get_logger()`의 실제 반환 타입은 `Any`이다. `structlog.configure()`에서 `wrapper_class=structlog.make_filtering_bound_logger()`를 설정했으므로 실제 런타임 타입은 `FilteringBoundLogger`이다. `structlog.stdlib.BoundLogger`는 stdlib 로깅 통합용 클래스로 현재 설정과 맞지 않는다.

타입 힌트가 부정확하면 IDE 자동완성과 mypy 검증이 오동작한다.

**해결안**:

```python
def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    """모듈별 로거 생성."""
    return structlog.get_logger(name)
```

---

### W-04. `_make_rotating_handler`의 `extMatch` 정규식이 `namer`의 파일명 패턴과 불일치

**파일**: `src/utils/logger.py:247, 250-254`

```python
handler.extMatch = re.compile(r"^\d{8}", re.ASCII)
# ...
def _namer(default_name: str) -> str:
    """app.log.20260405 -> logs/20260405_app.log"""
    dir_name = os.path.dirname(default_name)
    date_suffix = default_name.rsplit(".", 1)[-1]
    return os.path.join(dir_name, f"{date_suffix}_{base_name}")
```

`TimedRotatingFileHandler.getFilesToDelete()`는 `os.path.basename(self.baseFilename) + "."`를 prefix로 하여 디렉토리를 스캔한 뒤, prefix를 제거한 나머지에 대해 `extMatch`를 매칭한다.

그런데 `namer`가 파일명 형식을 `20260405_app.log`로 바꾸었으므로, 기본 파일명 `app.log.`를 prefix로 찾는 기본 로직으로는 `20260405_app.log` 파일을 찾을 수 없다. 따라서 `getFilesToDelete()`가 **절대로 오래된 파일을 삭제하지 못할 가능성**이 있다. `backupCount`가 작동하지 않으면 디스크가 채워질 수 있다.

**해결안**: `rotator`와 함께 `getFilesToDelete()`를 오버라이드하거나, `namer` 사용 시 `getFilesToDelete`도 커스텀해야 한다.

```python
def _make_rotating_handler(
    file_path: Path,
    backup_count: int,
) -> TimedRotatingFileHandler:
    handler = TimedRotatingFileHandler(...)
    handler.suffix = "%Y%m%d"
    base_name = file_path.name

    def _namer(default_name: str) -> str:
        dir_name = os.path.dirname(default_name)
        date_suffix = default_name.rsplit(".", 1)[-1]
        return os.path.join(dir_name, f"{date_suffix}_{base_name}")

    def _get_files_to_delete() -> list[str]:
        """namer에 맞게 롤링 파일 목록을 수집한다."""
        dir_name = os.path.dirname(handler.baseFilename)
        pattern = re.compile(r"^\d{8}_" + re.escape(base_name) + "$")
        result = []
        for fname in os.listdir(dir_name):
            if pattern.match(fname):
                result.append(os.path.join(dir_name, fname))
        result.sort()
        if len(result) > backup_count:
            return result[: len(result) - backup_count]
        return []

    handler.namer = _namer
    handler.getFilesToDelete = _get_files_to_delete
    return handler
```

이 이슈는 실제로 **로그 파일이 영구 축적되어 디스크를 채울 수 있는 운영 리스크**이므로 우선순위가 높다.

---

### W-05. `_file_logging_processor`가 `level` 추출을 중복 수행

**파일**: `src/utils/logger.py:329-332` vs `183-186`

```python
# _file_logging_processor (329행)
level = (
    event_dict.get("log_level")
    or event_dict.get("level", "info")
).upper()

# _file_renderer (183행) - 동일 로직 반복
level = (
    event_dict.pop("log_level", None)
    or event_dict.pop("level", "info")
).upper()
```

레벨 추출 로직이 두 곳에 중복되어 있으며, 하나는 `get`을 사용하고 다른 하나는 `pop`을 사용한다. 만약 키 이름이 변경되면 두 곳 모두 수정해야 한다.

**해결안**: `_file_logging_processor`에서 추출한 level을 `_file_renderer`에 인자로 전달하거나, renderer가 자체적으로만 level을 추출하도록 단일화.

---

### W-06. `dispatch.py` - bare `except Exception` 없이 `RuntimeError`만 포착

**파일**: `src/utils/tracker/dispatch.py:86-90`

```python
except RuntimeError:
    # LangGraph 실행 컨텍스트 밖 -- 무시
    pass
except Exception:
    logger.debug("tracking event dispatch 실패", event=name)
```

`RuntimeError`는 무시하고 그 외 `Exception`은 DEBUG 로그만 남긴다. 이 구조 자체는 합리적이다. 다만 `adispatch_custom_event`가 내부적으로 다른 예외(예: `TypeError`, `ValueError`)를 발생시키는 경우에도 조용히 삼켜진다. 트래킹 이벤트가 누락되는 원인을 파악하기 어려울 수 있다.

**해결안**: DEBUG 로그에 `exc_info=True`를 추가하여 스택 트레이스를 남기도록.

```python
except Exception:
    logger.debug("tracking event dispatch 실패", event=name, exc_info=True)
```

---

### W-07. `callback_handler.py`에서 `logger` import 위치 비일관성

**파일**: `src/utils/tracker/callback_handler.py:56-58`

```python
from src.utils.logger import get_logger

logger = get_logger(__name__)
```

다른 import 그룹(34-54행)과 분리되어 56행에 단독으로 `get_logger` import가 위치한다. 파일 상단의 import 블록(34-54행)에 통합되어야 한다.

**해결안**: 46-54행의 tracker 내부 import 블록 바로 위(또는 42행의 src import 그룹)에 통합.

---

### W-08. `_DualWriter`가 `write` + `write_file` 이중 쓰기 인터페이스 -- 역할 혼합

**파일**: `src/utils/logger.py:260-310`

`_DualWriter`는 두 가지 다른 경로로 사용된다:
1. `structlog.PrintLoggerFactory(file=_file_writer)` -- `write()` 호출 (콘솔 출력)
2. `_file_logging_processor` -- `write_file()` 호출 (파일 기록)

하나의 클래스가 "structlog의 file-like 인터페이스"와 "파일 로깅 관리자" 두 가지 책임을 갖는다. 이름이 `_DualWriter`이므로 의도적이긴 하지만, 확장 시(예: 파일 포맷 변경, 콘솔 필터링 추가) 변경 이유가 두 가지가 된다.

**해결안**: 현재 규모에서는 수용 가능하나, 향후 요구사항 증가 시 `_ConsoleWriter`(file-like)와 `_FileLogManager`(핸들러 관리)로 분리를 고려. 현 시점에서는 클래스 docstring에 두 역할을 명확히 문서화하는 것으로 충분.

---

## Info (GREEN)

### I-01. `log_backup_count: int = 15` 주석이 "0=무제한"이지만 실제 동작 미검증

**파일**: `src/config.py:275`

```python
log_backup_count: int = 15  # 롤링 보관 일수 (0=무제한)
```

`TimedRotatingFileHandler`의 `backupCount=0`은 "파일을 삭제하지 않음"이므로 사실상 무제한 보관이 맞다. 그러나 W-04에서 지적한 바와 같이 `namer` 커스텀으로 인해 `backupCount` 자체가 작동하지 않을 수 있으므로 이 주석은 사실상 무의미하다. W-04 해결 후 주석 유효성 재확인 필요.

---

### I-02. `_MULTILINE_KEYS` 미사용

**파일**: `src/utils/logger.py:41`

```python
_MULTILINE_KEYS = {"sql", "result_data", "groups", "tables", "targets", "errors"}
```

이 상수는 파일 어디에서도 참조되지 않는다. 이전 구현에서 남은 dead code로 보인다.

**해결안**: 제거.

---

### I-03. `_DualWriter.__init__`에서 `file_path.parent.mkdir` 중복 호출

**파일**: `src/utils/logger.py:274` vs `358`

```python
# _DualWriter.__init__ (274행)
file_path.parent.mkdir(exist_ok=True)

# setup_logging() (358행)
_LOG_DIR.mkdir(exist_ok=True)
```

`_LOG_DIR`과 `file_path.parent`는 같은 디렉토리이다. `setup_logging()`에서 이미 디렉토리를 생성한 뒤 `_DualWriter`를 생성하므로 274행은 중복이다.

**해결안**: `_DualWriter.__init__`에서 디렉토리 생성 제거 (호출자 책임 원칙).

---

### I-04. `_LOG_DIR` 경로가 `__file__` 기반 상대 경로

**파일**: `src/utils/logger.py:29`

```python
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
```

`parent.parent.parent`는 `src/utils/logger.py` -> `src/utils` -> `src` -> `프로젝트 루트`를 의미한다. 파일 위치가 변경되면 깨진다. 프로젝트 루트를 결정하는 유틸리티나 설정값을 사용하는 것이 안전하다.

**해결안**: `config.py`에 `project_root` 또는 `log_dir` 설정을 추가하거나, 기존 `eval_tracker_output_dir` 처럼 상대 경로 문자열로 관리.

---

### I-05. `callback_handler.py` 내부 `_format_value` 함수명 충돌

**파일**: `src/utils/tracker/callback_handler.py:910` vs `src/utils/logger.py:104`

두 모듈 모두 `_format_value`라는 이름의 private 함수를 가지고 있다. 모듈 스코프이므로 실제 충돌은 없지만, grep 검색이나 디버깅 시 혼동을 유발한다.

**해결안**: callback_handler.py의 것을 `_format_state_value`로 rename하여 용도를 명확히.

---

### I-06. `structlog.configure`에서 `cache_logger_on_first_use=False`

**파일**: `src/utils/logger.py:398`

```python
cache_logger_on_first_use=False,
```

이 설정은 매 로그 호출마다 프로세서 체인을 재구성한다. 개발 중 동적 설정 변경에는 유용하지만, 운영 환경에서는 성능 오버헤드가 있다. `settings.log_format`이나 `log_level`을 런타임에 변경할 필요가 없다면 `True`로 설정하는 것이 좋다.

**해결안**: 운영 환경에서는 `True`, 개발에서는 `False`로 설정 가능하도록 config 분리. 또는 현재 설정이 의도적이라면 주석으로 이유 기재.

---

### I-07. `dispatch.py`의 `from __future__ import annotations` 불필요

**파일**: `src/utils/tracker/dispatch.py:18`

Python 3.12 대상이며, 파일 내에 postponed annotation이 필요한 forward reference가 없다. 불필요한 import이다.

**해결안**: 제거. (단, 프로젝트 전체에서 일관되게 사용 중이라면 스타일 통일 차원에서 유지 가능.)

---

### I-08. `_PIPELINE_START_EVENTS` / `_PIPELINE_END_EVENTS`가 단일 요소 set

**파일**: `src/utils/logger.py:37-38`

```python
_PIPELINE_START_EVENTS = {"파이프라인 실행 시작"}
_PIPELINE_END_EVENTS = {"파이프라인 실행 완료"}
```

현재 각 set에 요소가 1개뿐이다. 향후 확장을 위한 설계라면 주석으로 의도를 기재. 단일 값이 고정이라면 단순 문자열 비교가 더 명확.

---

## 요약

| 등급 | 건수 | 핵심 이슈 |
|------|------|-----------|
| Critical | 3건 | shutdown_logging 캡슐화 위반, flush TOCTOU race, level 키 추출 불명확성 |
| Warning | 8건 | getFilesToDelete 미작동(디스크 위험), level 추출 중복, 타입 힌트 부정확, DualWriter 역할 혼합 |
| Info | 8건 | dead code, 경로 하드코딩, 함수명 충돌, 캐시 설정 |

**우선 처리 권장 순서**:
1. **W-04** (getFilesToDelete 미작동) -- 운영 환경 디스크 풀 위험
2. **C-02** (flush race condition) -- 멀티스레드 환경 데이터 손실 위험
3. **C-01** (shutdown_logging 캡슐화) -- 유지보수성
4. **W-03** (타입 힌트 정정) -- IDE/mypy 오동작 방지
