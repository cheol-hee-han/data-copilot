"""구조화된 로깅 설정 모듈.

콘솔: 컬러 출력 (개발 편의)
파일(logs/app.log): 가독성 높은 포맷 (분석 용이)

파일 로그 포맷 개선사항:
  - 질의 ID(query_id) 표시 — 어떤 질의에 대한 로그인지 즉시 식별
  - 시간: yyyy-mm-dd HH:MM:SS (마이크로초 제거)
  - SQL, 리스트, 딕셔너리는 개행 + 인덴트로 정리
  - \\n 리터럴을 실제 개행으로 변환
  - 파이프라인 시작/종료 시 구분선 표시
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import structlog

from src.config import settings

# 로그 디렉토리
_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "app.log"

# ANSI escape 코드 제거 정규식
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# 파이프라인 시작/종료를 나타내는 이벤트 키워드
_PIPELINE_START_EVENTS = {"파이프라인 실행 시작"}
_PIPELINE_END_EVENTS = {"파이프라인 실행 완료"}

# 여러 줄로 포맷팅할 키 목록 (SQL, 데이터 등)
_MULTILINE_KEYS = {"sql", "result_data", "groups", "tables", "targets", "errors"}

# 3순위: 내부 디버깅용, 자주 안 바뀜
_LONG_VALUE_THRESHOLD = settings.log_long_value_threshold
_SEP_WIDTH = settings.log_separator_width


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
    """파일 출력용 가독성 렌더러.

    포맷:
      [시간] [레벨] [query_id] 이벤트
        key1: value1
        key2:
          멀티라인 값
    """
    # 시간 포맷: yyyy-mm-dd HH:MM:SS
    timestamp = event_dict.pop("timestamp", "")
    if isinstance(timestamp, str) and len(timestamp) > 19:
        # ISO 포맷에서 초까지만 추출
        timestamp = timestamp[:19].replace("T", " ")
        # Z 나 +00:00 제거는 슬라이싱으로 이미 처리됨

    level = event_dict.pop("log_level", "info").upper()
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

    # 헤더 라인
    header = f"[{timestamp}] [{level:5s}]{query_id_str} {event}"

    # 구조화된 키-값 포맷팅
    # 이미 헤더에 포함된 키와 내부 키는 제외
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


class _DualWriter:
    """콘솔(컬러) + 파일(가독성 포맷) 동시 기록.

    콘솔에는 structlog ConsoleRenderer 출력을 그대로 전달하고,
    파일에는 별도의 가독성 렌더러(_file_renderer)로 포맷팅한 결과를 기록한다.
    """

    def __init__(self, file_path: Path) -> None:
        file_path.parent.mkdir(exist_ok=True)
        self._file = open(  # noqa: SIM115
            file_path, "a", encoding="utf-8",
        )

    def write(self, msg: str) -> None:
        # 콘솔: 컬러 그대로
        sys.stderr.write(msg)
        # 파일은 별도 렌더러에서 직접 기록 (_FileWriter 사용)

    def write_file(self, msg: str) -> None:
        """파일에만 기록한다."""
        self._file.write(msg)
        self._file.flush()

    def flush(self) -> None:
        sys.stderr.flush()
        self._file.flush()


# 모듈 레벨 파일 Writer (file_renderer 프로세서에서 접근)
_file_writer: _DualWriter | None = None


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
        # event_dict 복사본으로 파일 렌더링 (원본 훼손 방지)
        file_output = _file_renderer(logger, name, dict(event_dict))
        _file_writer.write_file(file_output)
    return event_dict


def setup_logging() -> None:
    """structlog 기반 로깅 초기화 (콘솔 + 파일)."""
    global _file_writer

    _LOG_DIR.mkdir(exist_ok=True)

    log_level = getattr(
        logging, settings.log_level.upper(), logging.INFO,
    )

    # 콘솔용 컬러 렌더러
    console_renderer = structlog.dev.ConsoleRenderer()

    # 파일 Writer
    _file_writer = _DualWriter(_LOG_FILE)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # 파일에 가독성 포맷으로 별도 기록
            _file_logging_processor,
            # 콘솔에 컬러 출력
            console_renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            log_level,
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(
            file=_file_writer,
        ),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """모듈별 로거 생성."""
    return structlog.get_logger(name)


def bind_query_context(query_id: str) -> None:
    """현재 질의의 컨텍스트를 로그에 바인딩한다.

    파이프라인 시작 시 호출하면 이후 모든 로그에 query_id 가 자동 포함된다.
    """
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(query_id=query_id)


def clear_query_context() -> None:
    """질의 컨텍스트를 해제한다."""
    structlog.contextvars.clear_contextvars()
