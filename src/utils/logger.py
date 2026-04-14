"""구조화된 로깅 설정 모듈 — structlog 기반 콘솔+파일 이중 출력.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

프로젝트 전체의 로깅 인프라를 구성한다. structlog를 사용하여
콘솔(컬러 텍스트 또는 JSON)과 파일(가독성 포맷, 일자별 롤링)에
동시 기록하며, contextvars를 통해 질의 ID(query_id)를 자동 전파한다.

stdlib logging 대신 structlog를 사용하는 이유:
  - 구조화된 키-값 로깅으로 파이프라인 디버깅 효율 향상
  - contextvars 기반 query_id 전파로 멀티턴 질의 추적 용이
  - 콘솔(개발)/JSON(운영) 렌더러 전환이 설정 한 줄로 가능

파일 로그 포맷 설계:
  - 질의 ID(query_id) 표시 — 어떤 질의에 대한 로그인지 즉시 식별
  - 시간: yyyy-mm-dd HH:MM:SS.SSS (KST 기준)
  - SQL, 리스트, 딕셔너리는 개행 + 인덴트로 정리
  - \\n 리터럴을 실제 개행으로 변환
  - 파이프라인 시작/종료 시 구분선으로 경계 표시
  - WARNING 이상은 별도 error.log에도 기록

핵심 함수/클래스:
  - setup_logging: structlog 초기화 (콘솔 + 파일 핸들러 구성)
  - shutdown_logging: 핸들러 종료 및 버퍼 플러시
  - get_logger: 모듈별 구조화 로거 생성
  - bind_query_context: 현재 질의 ID를 로그에 자동 바인딩
  - _DualWriter: 콘솔+파일 동시 기록 Writer
  - _file_renderer: 파일 출력용 가독성 렌더러
"""

import json
import logging
import os
import re
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

from src.config import settings
from src.utils.timezone import now_stamp

# 로그 디렉토리
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "app.log"
_ERROR_LOG_FILE = _LOG_DIR / "error.log"

# ANSI escape 코드 제거 정규식
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# 파이프라인 시작/종료를 나타내는 이벤트 키워드
_PIPELINE_START_EVENTS = {"파이프라인 실행 시작"}
_PIPELINE_END_EVENTS = {"파이프라인 실행 완료"}

# 3순위: 내부 디버깅용, 자주 안 바뀜
_LONG_VALUE_THRESHOLD = settings.log_long_value_threshold
_SEP_WIDTH = settings.log_separator_width

# error.log에 기록할 레벨 (WARNING 이상)
_WARNING_LEVELS: frozenset[str] = frozenset({"WARNING", "ERROR", "CRITICAL"})

# ── 민감 정보 마스킹 상수 ─────────────────────────────────

# Secrets: 키 이름에 포함되면 값 전체를 "****"로 교체
_SENSITIVE_KEY_PARTS: frozenset[str] = frozenset({
    "password", "api_key", "secret", "token", "credential",
})

# PII 마스킹 대상에서 제외할 키 (값이 길어도 PII가 아닌 것이 확실한 키)
_PII_SKIP_KEYS: frozenset[str] = frozenset({
    "event", "timestamp", "log_level", "level", "query_id",
    "node", "phase", "status", "connector", "path",
    "latency_ms", "duration_ms", "rows", "count", "size",
    "source", "tool", "intent", "thread",
})


def _expand_newlines(text: str) -> str:
    """리터럴 \\n 을 실제 개행으로 변환한다."""
    return text.replace("\\n", "\n")


def _indent_multiline(text: str, indent: str = "    ") -> str:
    """여러 줄 텍스트에 인덴트를 적용한다."""
    lines = text.strip().split("\n")
    return "\n" + "\n".join(f"{indent}{line}" for line in lines)


def _format_sql(value: str) -> str:
    """SQL 문자열을 가독성 있게 포맷팅한다."""
    sql = _expand_newlines(value)
    if "\n" in sql:
        return _indent_multiline(sql)
    return sql


def _format_list(value: list[Any], indent: str = "    ") -> str:
    """리스트를 항목별 개행으로 포맷팅한다."""
    if not value:
        return "[]"
    # 짧은 리스트는 한 줄로
    if len(value) <= 2 and all(
        isinstance(v, str) and len(str(v)) < 40 for v in value
    ):
        return str(value)
    lines = []
    for item in value:
        if isinstance(item, dict):
            item_str = json.dumps(item, ensure_ascii=False, default=str)
            lines.append(f"{indent}- {item_str}")
        else:
            lines.append(f"{indent}- {item}")
    return "\n" + "\n".join(lines)


def _format_dict(value: dict[str, Any], indent: str = "    ") -> str:
    """딕셔너리를 키별 개행으로 포맷팅한다."""
    if not value:
        return "{}"
    if len(str(value)) < _LONG_VALUE_THRESHOLD:
        return json.dumps(value, ensure_ascii=False, default=str)
    lines = [f"{indent}{k}: {v}" for k, v in value.items()]
    return "\n" + "\n".join(lines)


def _format_str(value: str) -> str:
    """문자열에서 리터럴 \\n 을 실제 개행으로 변환한다."""
    expanded = _expand_newlines(value)
    if "\n" in expanded:
        return _indent_multiline(expanded)
    return expanded


def _format_value(key: str, value: Any) -> str:
    """값을 가독성 있게 포맷팅한다."""
    if value is None:
        return "null"
    if key == "sql" and isinstance(value, str):
        return _format_sql(value)
    if isinstance(value, list):
        return _format_list(value)
    if isinstance(value, dict):
        return _format_dict(value)
    if isinstance(value, str):
        return _format_str(value)
    return str(value)


def _file_renderer(
    _logger: Any,
    _name: str,
    event_dict: dict[str, Any],
) -> str:
    """파일 출력용 가독성 렌더러 (logs/app.log).

    포맷을 바꾸고 싶으면 이 함수 안의 f-string 만 수정하면 된다.

    현재 출력 예시::

        [2026-04-04 23:23:26.693] [INFO ] [a1b2c3d4] SQL 실행 완료
          sql: SELECT COUNT(*) FROM tb_loan
          rows: 150

    사용 가능한 변수:

        ============== ============================== =======================
        변수           예시                           출처
        ============== ============================== =======================
        timestamp      "2026-04-04 23:23:26.693"      _kst_timestamper
        level          "INFO", "WARNING", "ERROR"     structlog add_log_level
        event          "SQL 실행 완료"                logger.info() 첫 인자
        query_id       "a1b2c3d4"                     bind_query_context()
        **kwargs       sql=, rows=, latency_ms= 등    logger.info(key=value)
        ============== ============================== =======================

    f-string 정렬 문법::

        f"{level:5s}"     -> "INFO "      5자 왼쪽 정렬 (기본)
        f"{level:>8s}"    -> "    INFO"   8자 오른쪽 정렬
        f"{level:^8s}"    -> "  INFO  "   8자 가운데 정렬
        f"{level:.<8s}"   -> "INFO...."   채움 문자 지정

    헤더 라인 수정 예시::

        # 현재
        f"[{timestamp}] [{level:5s}]{query_id_str} {event}"
        -> [2026-04-04 23:23:26.693] [INFO ] [a1b2c3d4] SQL 실행 완료

        # 대괄호 제거
        f"{timestamp} {level:5s}{query_id_str} {event}"
        -> 2026-04-04 23:23:26.693 INFO  [a1b2c3d4] SQL 실행 완료

        # 밀리초 제거
        f"[{timestamp[:19]}] [{level:5s}]{query_id_str} {event}"
        -> [2026-04-04 23:23:26] [INFO ] [a1b2c3d4] SQL 실행 완료

    상세 키-값 인덴트 수정 예시::

        현재:   f"  {key}: {formatted}"       ->   sql: SELECT ...
        4칸:    f"    {key}: {formatted}"     ->     sql: SELECT ...
        정렬:   f"  {key:<20s}: {formatted}"  ->   sql                 : SELECT ...

    연관 설정:

        - 타임스탬프 포맷: ``src/utils/timezone.py`` _STAMP_FMT
        - 구분선 폭: ``config.py`` log_separator_width (기본 72)
        - 멀티라인 인덴트: 이 파일의 ``_indent_multiline()``
        - 긴 값 기준: ``config.py`` log_long_value_threshold (기본 80)
    """
    # 시간 포맷: yyyy-mm-dd HH:MM:SS.SSS (KST)
    timestamp = event_dict.pop("timestamp", "")

    # make_filtering_bound_logger → "level" 키, add_log_level → "log_level" 키
    level = event_dict.pop("log_level", None)
    if level is None:
        level = event_dict.pop("level", "info")
    level = level.upper()
    event = event_dict.pop("event", "")

    # query_id 추출 (contextvars 에서 주입됨)
    query_id = event_dict.pop("query_id", "")
    query_id_str = f" [{query_id}]" if query_id else ""

    # 파이프라인 구분선
    separator = ""
    if event in _PIPELINE_START_EVENTS:
        user_input = event_dict.get("user_input", "")
        separator = (
            f"\n{'=' * _SEP_WIDTH}\n"
            f"  PIPELINE START: {user_input}\n"
            f"{'=' * _SEP_WIDTH}\n"
        )
    elif event in _PIPELINE_END_EVENTS:
        separator = f"{'─' * _SEP_WIDTH}\n"

    # ── 헤더 라인 (포맷 변경 시 이 줄만 수정) ──
    header = f"[{timestamp}] [{level:5s}]{query_id_str} {event}"

    # ── 상세 키-값 (인덴트·정렬 변경 시 아래 f-string 수정) ──
    _skip_keys = {"log_level", "level", "timestamp", "event", "query_id"}
    detail_lines: list[str] = []
    for key, value in event_dict.items():
        if key.startswith("_") or key in _skip_keys:
            continue
        formatted = _format_value(key, value)
        if "\n" in formatted:
            detail_lines.append(f"  {key}:{formatted}")
        else:
            detail_lines.append(f"  {key}: {formatted}")

    # 조합
    parts = [separator, header]
    if detail_lines:
        parts.append("\n")
        parts.append("\n".join(detail_lines))
    parts.append("\n")

    return "".join(parts)


def _make_rotating_handler(
    file_path: Path,
    backup_count: int,
) -> TimedRotatingFileHandler:
    """일자별 롤링 핸들러를 생성한다.

    롤링 시 파일명: yyyymmdd_app.log (예: 20260405_app.log)
    """
    handler = TimedRotatingFileHandler(
        filename=str(file_path),
        when="midnight",
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.suffix = "%Y%m%d"
    # suffix 변경 시 extMatch도 동기화해야 getFilesToDelete()가 작동함
    handler.extMatch = re.compile(r"^\d{8}", re.ASCII)
    base_name = file_path.name  # "app.log" 또는 "error.log"

    def _namer(default_name: str) -> str:
        """app.log.20260405 → logs/20260405_app.log"""
        dir_name = os.path.dirname(default_name)
        date_suffix = default_name.rsplit(".", 1)[-1]
        return os.path.join(dir_name, f"{date_suffix}_{base_name}")

    # handler 객체 자체를 캡처하면 순환 참조가 발생하므로
    # 불변 문자열(baseFilename)만 캡처한다.
    _base_filename = handler.baseFilename

    def _get_files_to_delete() -> list[str]:
        """namer에 맞게 롤링 백업 파일을 수집하여 삭제 대상을 반환한다."""
        dir_name = os.path.dirname(_base_filename)
        pattern = re.compile(r"^\d{8}_" + re.escape(base_name) + "$")
        result = []
        try:
            for fname in os.listdir(dir_name):
                if pattern.match(fname):
                    result.append(os.path.join(dir_name, fname))
        except OSError:
            return []
        result.sort()
        if len(result) > backup_count:
            return result[: len(result) - backup_count]
        return []

    handler.namer = _namer
    handler.getFilesToDelete = _get_files_to_delete  # type: ignore[method-assign]
    return handler


class _DualWriter:
    """콘솔(컬러) + 파일(가독성 포맷, 일자별 롤링) 동시 기록.

    콘솔에는 structlog ConsoleRenderer 출력을 그대로 전달하고,
    파일에는 별도의 가독성 렌더러(_file_renderer)로 포맷팅한 결과를 기록한다.
    에러(WARNING+)는 별도 error.log에도 기록한다.
    """

    def __init__(
        self,
        file_path: Path,
        error_file_path: Path,
        backup_count: int,
    ) -> None:
        file_path.parent.mkdir(exist_ok=True)
        self._handler = _make_rotating_handler(file_path, backup_count)
        self._error_handler = _make_rotating_handler(
            error_file_path, backup_count,
        )
        # _WARNING_LEVELS 모듈 상수 참조

    def write(self, msg: str) -> None:
        """PrintLogger 인터페이스 구현 — 콘솔(stderr)에 출력한다.

        파일 기록은 _file_logging_processor에서 write_file()을 별도 호출한다.
        """
        sys.stderr.write(msg)

    def _write_to_handler(
        self, handler: TimedRotatingFileHandler, msg: str,
    ) -> None:
        """핸들러에 메시지를 기록한다 (롤링 체크 포함, 스레드 안전)."""
        # logging 표준 라이브러리는 핸들러 생성 시 lock 을 항상 설정하지만
        # 타입 시그니처가 LockType | None 이라 명시 가드가 필요하다.
        assert handler.lock is not None
        with handler.lock:
            record = logging.LogRecord("", 0, "", 0, msg, (), None)
            if handler.shouldRollover(record):
                handler.doRollover()
            stream = handler.stream
            if stream and not stream.closed:
                stream.write(msg)
                stream.flush()

    def write_file(self, msg: str, level: str = "") -> None:
        """파일에 기록한다 (롤링 핸들러 경유)."""
        self._write_to_handler(self._handler, msg)
        # WARNING 이상은 에러 로그에도 기록
        if level in _WARNING_LEVELS:
            self._write_to_handler(self._error_handler, msg)

    def flush(self) -> None:
        """stderr 및 파일 핸들러의 버퍼를 즉시 디스크에 기록한다.

        각 핸들러의 lock을 획득하여 스트림이 열려 있을 때만 flush한다.
        프로세스 종료 직전이나 크래시 대비 로그 보존에 사용된다.
        """
        sys.stderr.flush()
        for handler in (self._handler, self._error_handler):
            assert handler.lock is not None
            with handler.lock:
                if handler.stream and not handler.stream.closed:
                    handler.stream.flush()

    def close(self) -> None:
        """핸들러를 안전하게 종료한다."""
        self.flush()
        self._handler.close()
        self._error_handler.close()


# 모듈 레벨 파일 Writer (file_renderer 프로세서에서 접근)
_file_writer: _DualWriter | None = None
_initialized: bool = False


def _file_logging_processor(
    logger: Any,
    name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """파일에 가독성 포맷으로 별도 기록하는 프로세서.

    ConsoleRenderer 이전에 실행되어 event_dict 의 복사본으로 파일에 기록한다.
    원본 event_dict 는 변경하지 않으므로 ConsoleRenderer 는 정상 동작한다.
    """
    if _file_writer is not None:
        level = (
            event_dict.get("log_level")
            or event_dict.get("level", "info")
        ).upper()
        # event_dict 복사본으로 파일 렌더링 (원본 훼손 방지)
        file_output = _file_renderer(logger, name, dict(event_dict))
        _file_writer.write_file(file_output, level=level)
    return event_dict


def _kst_timestamper(
    logger: Any,
    name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """KST 타임스탬프를 주입하는 structlog 프로세서."""
    event_dict["timestamp"] = now_stamp()
    return event_dict


def _mask_sensitive_processor(
    _logger: Any,
    _name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog 프로세서 — Secrets 키 마스킹 + PII 값 마스킹.

    1단계 (Secrets): 키 이름에 password, api_key 등이 포함되면
    값을 ``"****"``로 교체한다.

    2단계 (PII): 문자열 값에 주민번호, 카드번호, 전화번호 등
    PII 패턴이 있으면 ``security.mask_pii()``로 마스킹한다.
    ``_PII_SKIP_KEYS``에 해당하는 키는 PII 검사를 건너뛴다
    (성능 최적화 + 오탐 방지).
    """
    from src.utils.security import mask_pii  # lazy import (순환 참조 방지)

    for key, value in event_dict.items():
        if not isinstance(value, str):
            continue

        # 1단계: Secrets 키 마스킹
        if any(s in key.lower() for s in _SENSITIVE_KEY_PARTS):
            event_dict[key] = "****"
            continue

        # 2단계: PII 값 마스킹 (skip 대상이 아닌 키, 최소 길이 충족)
        # settings.pii_masking_enabled=False 이면 mask_pii 내부에서 원문 반환
        if key not in _PII_SKIP_KEYS and len(value) > 5:
            event_dict[key] = mask_pii(value)

    return event_dict


def setup_logging() -> None:
    """structlog 기반 로깅 초기화 (콘솔 + 파일).

    중복 호출 시 무시한다 (lifespan과 CLI에서 각각 호출될 수 있음).
    """
    global _file_writer, _initialized
    if _initialized:
        return

    _LOG_DIR.mkdir(exist_ok=True)

    log_level = getattr(
        logging, settings.log_level.upper(), logging.INFO,
    )

    # 콘솔 렌더러: log_format 설정에 따라 선택
    #   "console" → 컬러 텍스트 (개발용, 기본값)
    #   "json"    → JSON 한 줄 출력 (운영/폐쇄망, 로그 수집기 연동)
    console_renderer: Any
    if settings.log_format.lower() == "json":
        console_renderer = structlog.processors.JSONRenderer(
            ensure_ascii=False,
        )
    else:
        console_renderer = structlog.dev.ConsoleRenderer()

    # 파일 Writer (일자별 롤링, 에러 로그 분리)
    _file_writer = _DualWriter(
        _LOG_FILE,
        _ERROR_LOG_FILE,
        backup_count=settings.log_backup_count,
    )

    # structlog의 Processor 타입은 매우 엄격하지만(MutableMapping+bytes 등),
    # 실제 런타임은 dict만 통과시키면 동작한다. 커스텀 프로세서/Writer는
    # 덕 타이핑으로 문제없으므로 type: ignore로 명시 억제.
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _kst_timestamper,  # type: ignore[list-item]
            _mask_sensitive_processor,  # type: ignore[list-item]
            _file_logging_processor,  # type: ignore[list-item]
            console_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            log_level,
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(
            file=_file_writer,  # type: ignore[arg-type]
        ),
        cache_logger_on_first_use=False,
    )
    _initialized = True


def shutdown_logging() -> None:
    """로깅 핸들러를 안전하게 종료한다.

    서버 종료 시 호출하여 버퍼를 플러시하고 파일 핸들을 닫는다.
    """
    global _file_writer, _initialized
    if _file_writer is not None:
        _file_writer.close()
        _file_writer = None
    _initialized = False


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    """모듈별 로거 생성."""
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger


def bind_query_context(query_id: str) -> None:
    """현재 질의의 컨텍스트를 로그에 바인딩한다.

    파이프라인 시작 시 호출하면 이후 모든 로그에 query_id 가 자동 포함된다.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(query_id=query_id)


def clear_query_context() -> None:
    """질의 컨텍스트를 해제한다."""
    structlog.contextvars.clear_contextvars()
