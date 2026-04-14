"""리소스 로더 — resources/ 디렉토리의 비-Python 파일 통합 로딩.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

프롬프트(.txt), 도메인 사전(.yaml), 평가 골든셋(.json),
SQL 쿼리 템플릿(.sql), MongoDB 파이프라인, Cypher 쿼리 등
앱이 실행 시 읽는 비-Python 파일을 한 곳(resources/)에서 관리한다.

리소스를 코드 안에 하드코딩하지 않고 파일로 분리하는 이유:
  - 프롬프트/쿼리를 코드 변경 없이 수정 가능
  - 폐쇄망 배포 시 resources/ 디렉토리만 교체하여 환경별 커스터마이징
  - 파일이 없으면 코드 내 기본값(fallback)을 사용하므로 점진적 도입 가능

로딩 원칙:
    - resources/ 에 파일이 있으면 해당 파일 사용
    - 파일이 없으면 코드 내 기본값 사용 (fallback)
    - resources/ 디렉토리 자체가 없어도 정상 동작

핵심 함수:
    - load_text / load_text_required — 텍스트 로드 (프롬프트 등)
    - load_yaml / load_json / load_csv — 구조화 데이터 로드
    - load_sql_template — SQL 템플릿 로드 (주석 제거)
    - load_mongo_pipeline — MongoDB aggregation 파이프라인 로드
    - load_cypher — Neo4j Cypher 쿼리 로드 (주석 제거 + 치환)
    - exists — 파일 존재 여부 확인
    - RESOURCES_DIR — resources/ 디렉토리 경로 상수
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


# ── MongoDB 파이프라인 ──

def load_mongo_pipeline(name: str) -> dict[str, Any]:
    """MongoDB aggregation pipeline 템플릿을 로드한다.

    _comment, _note 키는 제거한다.
    ${변수} 플레이스홀더는 호출 시점에 치환이 필요하다.

    Args:
        name: resources/ 하위 상대 경로
            (예: "connectors/mongo/pipeline_table_meta.json")
    """
    raw = load_text_required(name)
    data: dict[str, Any] = json.loads(raw)
    data.pop("_comment", None)
    data.pop("_note", None)
    return data


# ── Neo4j Cypher ──

def load_cypher(name: str, **replacements: str) -> str:
    """Cypher 쿼리 파일을 로드하고 플레이스홀더를 치환한다.

    // 주석 행은 제거한다. {key} 형식의 플레이스홀더를 치환한다.

    Args:
        name: resources/ 하위 상대 경로
            (예: "connectors/neo4j/cypher_join_paths.cypher")
        **replacements: 플레이스홀더 치환 (예: max_hops="4")
    """
    raw = load_text_required(name)
    lines = [
        line for line in raw.splitlines()
        if not line.lstrip().startswith("//")
    ]
    cypher = "\n".join(lines).strip()
    for key, value in replacements.items():
        cypher = cypher.replace(f"{{{key}}}", str(value))
    return cypher


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
