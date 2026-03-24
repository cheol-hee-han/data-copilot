"""리소스 로더.

resources/ 디렉토리의 리소스 파일을 로드하는 유틸리티.

프롬프트(.txt), 도메인 사전(.yaml), ES 설정, 평가 골든셋(.json),
SQL 쿼리 템플릿(.sql) 등 앱이 실행 시 읽는 비-Python 파일을
한 곳(resources/)에서 관리한다.

로딩 원칙:
    - resources/ 에 파일이 있으면 해당 파일 사용
    - 파일이 없으면 코드 내 기본값 사용 (fallback)
    - resources/ 디렉토리 자체가 없어도 정상 동작

제공 함수:
    - load_yaml(name, default) — YAML 로드, 없으면 default 반환
    - load_json(name, default) — JSON 로드, 없으면 default 반환
    - load_csv(name, default) — CSV 로드, 없으면 default 반환
    - load_text(name, default) — 텍스트 로드, 없으면 default 반환
    - load_text_required(name) — 필수 텍스트 로드, 없으면 FileNotFoundError
    - load_sql_template(name) — SQL 템플릿 로드 (주석 제거)
    - load_es_query(name, query) — ES 쿼리 JSON 로드 + {query} 치환
    - exists(name) — 파일 존재 여부 확인
    - RESOURCES_DIR — resources/ 디렉토리 경로 상수

하위 호환:
    - CUSTOM_DIR — RESOURCES_DIR의 별칭 (기존 코드 호환)
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 프로젝트 루트 기준 resources/ 디렉토리 경로
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESOURCES_DIR = _PROJECT_ROOT / "resources"

# 하위 호환 별칭
CUSTOM_DIR = RESOURCES_DIR


def _resolve(name: str) -> Path:
    """resources/ 하위 상대 경로를 절대 경로로 변환한다."""
    return RESOURCES_DIR / name


def exists(name: str) -> bool:
    """resources/ 하위에 해당 파일이 존재하는지 확인한다."""
    return _resolve(name).is_file()


# ── 텍스트 ──

def load_text(name: str, default: str) -> str:
    """텍스트 파일을 로드한다. 없으면 default 반환.

    Args:
        name: resources/ 하위 상대 경로 (예: "prompts/sql_generation.txt")
        default: 파일이 없을 때 반환할 기본값
    """
    path = _resolve(name)
    if not path.is_file():
        return default
    try:
        content = path.read_text(encoding="utf-8")
        logger.info("리소스 로드", file=name, size=len(content))
        return content
    except Exception as e:
        logger.warning("리소스 로드 실패, 기본값 사용", file=name, error=str(e))
        return default


def load_text_required(name: str) -> str:
    """필수 텍스트 파일을 로드한다. 없으면 FileNotFoundError.

    프롬프트 등 파일이 반드시 존재해야 하는 경우에 사용한다.

    Args:
        name: resources/ 하위 상대 경로 (예: "prompts/sql_generation.txt")

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때
    """
    path = _resolve(name)
    if not path.is_file():
        raise FileNotFoundError(
            f"필수 파일 없음: resources/{name} "
            f"(절대경로: {path})"
        )
    content = path.read_text(encoding="utf-8")
    logger.info("필수 리소스 로드", file=name, size=len(content))
    return content


# ── SQL 템플릿 ──

def load_sql_template(name: str) -> str:
    """SQL 템플릿 파일을 로드한다. SQL 주석(-- ...)은 제거한다.

    Args:
        name: resources/ 하위 상대 경로 (예: "queries/search_similar_sql.sql")

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때
    """
    raw = load_text_required(name)
    lines = [
        line for line in raw.splitlines()
        if not line.lstrip().startswith("--")
    ]
    return "\n".join(lines).strip()


# ── ES 쿼리 ──

def load_es_query(name: str, query: str) -> dict[str, Any]:
    """ES 쿼리 JSON 템플릿을 로드하고 {query} 플레이스홀더를 치환한다.

    _comment 키는 제거한다.

    Args:
        name: resources/ 하위 상대 경로 (예: "elasticsearch/table_meta_query.json")
        query: 검색어 (템플릿의 {query}를 치환)
    """
    raw = load_text_required(name)
    replaced = raw.replace("{query}", query)
    data = json.loads(replaced)
    data.pop("_comment", None)
    return data


# ── YAML ──

def load_yaml(name: str, default: Any) -> Any:
    """YAML 파일을 로드한다. 없으면 default 반환.

    Args:
        name: resources/ 하위 상대 경로 (예: "domain/domain_dictionary.yaml")
        default: 파일이 없을 때 반환할 기본값
    """
    path = _resolve(name)
    if not path.is_file():
        return default
    try:
        import yaml  # lazy import — yaml 미설치 환경에서도 기본값 동작

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        logger.info("YAML 리소스 로드", file=name)
        return data
    except Exception as e:
        logger.warning("YAML 리소스 로드 실패, 기본값 사용", file=name, error=str(e))
        return default


# ── JSON ──

def load_json(name: str, default: Any) -> Any:
    """JSON 파일을 로드한다. 없으면 default 반환.

    Args:
        name: resources/ 하위 상대 경로 (예: "evaluation/golden_queries.json")
        default: 파일이 없을 때 반환할 기본값
    """
    path = _resolve(name)
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.info("JSON 리소스 로드", file=name)
        return data
    except Exception as e:
        logger.warning("JSON 리소스 로드 실패, 기본값 사용", file=name, error=str(e))
        return default


# ── CSV ──

def load_csv(
    name: str,
    default: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """CSV 파일을 로드하여 dict 리스트로 반환한다. 없으면 default 반환.

    첫 행을 헤더로 사용하며, 각 행을 {컬럼명: 값} dict로 변환한다.

    Args:
        name: resources/ 하위 상대 경로 (예: "data/sample.csv")
        default: 파일이 없을 때 반환할 기본값
    """
    if default is None:
        default = []
    path = _resolve(name)
    if not path.is_file():
        return default
    try:
        text = path.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        logger.info("CSV 리소스 로드", file=name, rows=len(rows))
        return rows
    except Exception as e:
        logger.warning("CSV 리소스 로드 실패, 기본값 사용", file=name, error=str(e))
        return default
