"""유사 테이블 선택 엔진.

유사 테이블 그룹 데이터를 기반으로 세 가지 핵심 로직을 제공한다:

1. 컨텍스트 수집 시 유사 테이블 어노테이션
   - ES 검색 결과에서 유사 그룹에 속하는 테이블이 감지되면
   - 프롬프트에 주입할 "유사 테이블 구분 가이드"를 자동 생성

2. SQL 검증 시 테이블 적절성 검증
   - 생성된 SQL에서 사용된 테이블이 유사 그룹 규칙에 부합하는지 검증
   - 부적합한 테이블 사용 시 재생성 피드백 또는 명확화 질문을 생성

3. 골든셋 평가용 부적합 테이블 검증

유사 테이블 그룹 **데이터 정의**는 domain/similar_tables.py 에 위치한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.services.domain.similar_tables import (
    SIMILAR_TABLE_GROUPS,
    SimilarTable,
    SimilarTableGroup,
    TABLE_TO_GROUPS,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


class TableVerdict(str, Enum):
    """테이블 선택 검증 결과."""

    PASS = "pass"  # 적절한 테이블 선택
    WARNING = "warning"  # 부적합 가능성, 재생성 권고
    AMBIGUOUS = "ambiguous"  # 모호, 사용자 확인 필요


@dataclass
class TableSelectionResult:
    """테이블 선택 검증 결과."""

    verdict: TableVerdict
    used_tables: list[str]  # SQL에서 사용된 테이블
    matched_groups: list[str]  # 매칭된 유사 그룹 ID
    warnings: list[str] = field(default_factory=list)
    suggestion: str = ""  # 재생성 시 테이블 변경 제안
    clarification_question: str = ""  # 사용자 확인 질문


# ──────────────────────────────────────────────────────────────
# Stage 1: 컨텍스트 수집 시 유사 테이블 어노테이션
# ──────────────────────────────────────────────────────────────

def find_relevant_groups(
    table_names: list[str],
) -> list[SimilarTableGroup]:
    """테이블 목록에서 유사 그룹에 속하는 테이블을 감지한다.

    하나의 그룹에서 2개 이상의 테이블이 후보에 있거나,
    그룹에 속하는 테이블이 1개라도 있으면 해당 그룹을 반환한다.
    (같은 그룹의 다른 테이블을 잘못 사용할 위험이 있으므로)
    """
    found: dict[str, SimilarTableGroup] = {}
    for name in table_names:
        for group in TABLE_TO_GROUPS.get(name, []):
            found[group.group_id] = group
    return list(found.values())


def build_table_disambiguation_prompt(
    groups: list[SimilarTableGroup],
) -> str:
    """유사 테이블 구분 가이드를 프롬프트용 문자열로 생성한다.

    SQL 생성 프롬프트에 주입되어 LLM이 올바른 테이블을 선택하도록 유도한다.
    """
    if not groups:
        return ""

    lines = [
        "\n## 유사 테이블 구분 가이드 (중요!)",
        "아래 테이블들은 비슷한 데이터를 담고 있지만 용도가 다릅니다.",
        "반드시 구분 기준을 확인하고 적합한 테이블만 사용하세요.\n",
    ]

    for group in groups:
        lines.append(f"### [{group.domain}] {group.description}")
        for tname, tinfo in group.tables.items():
            lines.append(f"- **{tname}**: {tinfo.purpose} [{tinfo.update_cycle}]")
            lines.append(f"  적합: {', '.join(tinfo.suitable_for[:3])}")
            lines.append(f"  부적합: {', '.join(tinfo.unsuitable_for[:3])}")
        lines.append(f"- **구분 기준**: {group.disambiguation_rule}")
        lines.append("")

    return "\n".join(lines)


def score_table_for_query(
    query: str,
    table: SimilarTable,
) -> float:
    """사용자 질의와 테이블의 적합도 점수를 계산한다.

    신호어 매칭 기반의 간단한 점수 산정.
    향후 임베딩 유사도 등으로 고도화 가능.
    """
    query_lower = query.lower().replace(" ", "")
    score = 0.0

    # 신호어 매칭 (+1점씩)
    for keyword in table.signal_keywords:
        if keyword.replace(" ", "") in query_lower:
            score += 1.0

    # 적합 요청 유형 매칭 (+0.5점씩)
    for suitable in table.suitable_for:
        suitable_lower = suitable.lower().replace(" ", "")
        if any(
            w in query_lower
            for w in suitable_lower.split()
            if len(w) > 1
        ):
            score += 0.5

    # 부적합 요청 유형 매칭 (-1.0점씩)
    for unsuitable in table.unsuitable_for:
        unsuitable_lower = unsuitable.lower().replace(" ", "")
        if any(
            w in query_lower
            for w in unsuitable_lower.split()
            if len(w) > 1
        ):
            score -= 1.0

    return score


def recommend_tables_for_query(
    query: str,
    groups: list[SimilarTableGroup],
) -> dict[str, dict[str, Any]]:
    """사용자 질의에 대해 각 유사 그룹별 추천 테이블을 반환한다.

    Returns:
        {group_id: {"recommended": table_name, "scores": {table: score}, "confident": bool}}
    """
    recommendations: dict[str, dict[str, Any]] = {}

    for group in groups:
        scores: dict[str, float] = {}
        for tname, tinfo in group.tables.items():
            scores[tname] = score_table_for_query(query, tinfo)

        sorted_tables = sorted(
            scores.items(), key=lambda x: x[1], reverse=True,
        )
        best_name, best_score = sorted_tables[0]

        # 점수 차이가 충분하면 확신, 아니면 모호
        if len(sorted_tables) > 1:
            second_score = sorted_tables[1][1]
            score_gap = best_score - second_score
            confident = score_gap >= 1.0 and best_score > 0
        else:
            confident = best_score > 0

        recommendations[group.group_id] = {
            "recommended": best_name,
            "scores": scores,
            "confident": confident,
            "disambiguation_rule": group.disambiguation_rule,
        }

    return recommendations


# ──────────────────────────────────────────────────────────────
# Stage 3: SQL 검증 시 테이블 적절성 검증
# ──────────────────────────────────────────────────────────────

def extract_tables_from_sql(sql: str) -> list[str]:
    """SQL에서 사용된 테이블명을 추출한다.

    FROM, JOIN 절에서 테이블명을 추출하며,
    서브쿼리 내부의 테이블도 포함한다.
    """
    # FROM/JOIN 뒤의 테이블명 추출 (alias 포함)
    pattern = r"(?:FROM|JOIN)\s+(\w+)"
    matches = re.findall(pattern, sql, re.IGNORECASE)

    # TB_ 접두사를 가진 것만 (서브쿼리 alias 등 제외)
    tables = [
        m.upper() for m in matches
        if m.upper().startswith("TB_")
    ]
    return list(dict.fromkeys(tables))  # 중복 제거, 순서 유지


def validate_table_selection(
    sql: str,
    query: str,
    context_tables: list[str],
) -> TableSelectionResult:
    """생성된 SQL의 테이블 선택이 적절한지 검증한다.

    Args:
        sql: 생성된 SQL
        query: 사용자 원본 질의
        context_tables: 컨텍스트 수집에서 반환된 테이블 목록

    Returns:
        TableSelectionResult: 검증 결과 (PASS/WARNING/AMBIGUOUS)
    """
    used_tables = extract_tables_from_sql(sql)

    if not used_tables:
        return TableSelectionResult(
            verdict=TableVerdict.WARNING,
            used_tables=[],
            matched_groups=[],
            warnings=["SQL에서 테이블을 식별할 수 없습니다"],
        )

    # 사용된 테이블이 속한 유사 그룹 찾기
    relevant_groups = find_relevant_groups(used_tables)
    if not relevant_groups:
        # 유사 그룹에 속하지 않는 테이블만 사용 → 검증 불필요
        return TableSelectionResult(
            verdict=TableVerdict.PASS,
            used_tables=used_tables,
            matched_groups=[],
        )

    warnings: list[str] = []
    suggestions: list[str] = []
    ambiguous_groups: list[str] = []

    for group in relevant_groups:
        # 이 그룹에서 어떤 테이블이 사용되었는지
        used_in_group = [
            t for t in used_tables if t in group.tables
        ]
        if not used_in_group:
            continue

        # 각 사용된 테이블에 대해 적합도 점수 계산
        recommendations = recommend_tables_for_query(
            query, [group],
        )
        rec = recommendations.get(group.group_id, {})
        recommended = rec.get("recommended", "")
        confident = rec.get("confident", False)
        scores = rec.get("scores", {})

        for used_table in used_in_group:
            used_score = scores.get(used_table, 0)
            rec_score = scores.get(recommended, 0)

            if used_table == recommended:
                # 추천 테이블과 일치 → OK
                continue

            if confident and used_score < rec_score:
                # 확신 있게 다른 테이블을 추천하는데 잘못된 것을 사용
                warnings.append(
                    f"'{used_table}' 대신 '{recommended}'이 "
                    f"더 적합할 수 있습니다. "
                    f"구분 기준: {group.disambiguation_rule}"
                )
                suggestions.append(
                    f"'{used_table}' 대신 '{recommended}'을 "
                    f"사용하세요. 이유: {group.disambiguation_rule}"
                )
            elif not confident:
                # 확신 없음 → 모호
                ambiguous_groups.append(group.group_id)

    matched_group_ids = [g.group_id for g in relevant_groups]

    # 최종 판정
    if ambiguous_groups:
        # 모호한 그룹이 있으면 사용자에게 확인
        clarification = _build_table_clarification(
            query, ambiguous_groups,
        )
        return TableSelectionResult(
            verdict=TableVerdict.AMBIGUOUS,
            used_tables=used_tables,
            matched_groups=matched_group_ids,
            warnings=warnings,
            clarification_question=clarification,
        )

    if warnings:
        return TableSelectionResult(
            verdict=TableVerdict.WARNING,
            used_tables=used_tables,
            matched_groups=matched_group_ids,
            warnings=warnings,
            suggestion="\n".join(suggestions),
        )

    return TableSelectionResult(
        verdict=TableVerdict.PASS,
        used_tables=used_tables,
        matched_groups=matched_group_ids,
    )


def _build_table_clarification(
    query: str,
    ambiguous_group_ids: list[str],
) -> str:
    """모호한 테이블 선택에 대한 명확화 질문을 생성한다."""
    group_map = {g.group_id: g for g in SIMILAR_TABLE_GROUPS}

    parts = ["어떤 데이터가 필요하신지 확인하겠습니다.\n"]
    option_num = 1

    for gid in ambiguous_group_ids:
        group = group_map.get(gid)
        if not group:
            continue

        for tname, tinfo in group.tables.items():
            # 사용자 친화적 설명 (기술 용어 없이)
            suitable_desc = tinfo.suitable_for[0] if tinfo.suitable_for else tinfo.purpose
            parts.append(f"{option_num}) {suitable_desc}")
            option_num += 1

    parts.append(f"{option_num}) 다른 데이터 (직접 입력해 주세요)")

    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────
# 골든셋 평가용: 부적합 테이블 검증
# ──────────────────────────────────────────────────────────────

def check_rejected_tables(
    sql: str,
    rejected_tables: list[str],
) -> tuple[bool, list[str]]:
    """SQL에 부적합한 유사 테이블이 사용되지 않았는지 검증한다.

    골든셋 평가에서 expected_tables(필수 포함)와 함께
    rejected_tables(포함되면 안 되는 테이블)를 검증한다.

    Returns:
        (passed, errors)
    """
    if not rejected_tables:
        return True, []

    used_tables = extract_tables_from_sql(sql)
    errors: list[str] = []

    for rejected in rejected_tables:
        if rejected.upper() in [t.upper() for t in used_tables]:
            errors.append(
                f"부적합한 유사 테이블 '{rejected}' 사용됨"
            )

    return len(errors) == 0, errors
