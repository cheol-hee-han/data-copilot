"""통찰(Insight) 데이터 빌더 — 파이프라인 추론 과정을 사용자 친화적으로 구성한다.

파이프라인 실행 완료 후 PipelineState에서 추론 과정 데이터를 추출하여
UI의 '💡 통찰' 패널에 표시할 구조화된 딕셔너리를 반환한다.

핵심 함수:
    - build_insight: State에서 통찰 데이터를 구성하여 dict로 반환
"""

from __future__ import annotations

from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


def build_insight(state: dict[str, Any]) -> dict[str, Any]:
    """파이프라인 완료 State에서 통찰 데이터를 구성한다.

    Args:
        state: 파이프라인 최종 상태 딕셔너리

    Returns:
        UI 통찰 패널에 전달할 구조화된 딕셔너리
    """
    reason = state.get("reason")
    normalized = state.get("normalized_query") or {}
    trace_log = state.get("trace_log", [])
    sql_result = state.get("sql_result")

    return {
        "query_interpretation": _build_query_interpretation(state, normalized),
        "tables_used": _build_tables_used(reason),
        "tables_rejected": _build_tables_rejected(reason),
        "sql_summary": _build_sql_summary(reason),
        "sql_code": _extract_sql(reason),
        "join_path": _format_join_path(reason),
        "references": _build_references(reason),
        "total_elapsed": _calc_total_elapsed(trace_log),
        "step_timings": _build_step_timings(trace_log),
        "confidence": _assess_confidence(reason),
        "caveats": _build_caveats(state, reason),
        "result_stats": _build_result_stats(sql_result),
    }


def _build_query_interpretation(
    state: dict[str, Any],
    normalized: Any,
) -> dict[str, str]:
    """질문 해석 정보를 구성한다."""
    # normalized_query가 Pydantic 모델 또는 dict일 수 있음
    if hasattr(normalized, "model_dump"):
        nq = normalized.model_dump()
    elif isinstance(normalized, dict):
        nq = normalized
    else:
        nq = {}

    return {
        "original": state.get("user_input", ""),
        "period": nq.get("period", nq.get("date_range", "")),
        "target": nq.get("target", nq.get("entity", "")),
        "metric": nq.get("metric", nq.get("measure", "")),
        "category": str(state.get("query_category", "")),
    }


def _build_tables_used(reason: Any) -> list[dict[str, Any]]:
    """사용된 테이블 목록을 구성한다."""
    if not reason:
        return []

    candidates = _get_attr_or_key(reason, "candidate_tables", [])
    tables = []
    for t in candidates:
        if hasattr(t, "model_dump"):
            td = t.model_dump()
        elif isinstance(t, dict):
            td = t
        else:
            continue
        tables.append({
            "name": td.get("table_name", td.get("name", "")),
            "desc": td.get("description", td.get("table_desc", "")),
            "reason": td.get("selection_reason", td.get("reason", "")),
            "columns": td.get("columns_used", td.get("key_columns", [])),
        })
    return tables


def _build_tables_rejected(reason: Any) -> list[dict[str, Any]]:
    """제외된 테이블 목록을 구성한다."""
    if not reason:
        return []

    rejected = _get_attr_or_key(reason, "rejected_tables", [])
    tables = []
    for t in rejected:
        if hasattr(t, "model_dump"):
            td = t.model_dump()
        elif isinstance(t, dict):
            td = t
        else:
            continue
        tables.append({
            "name": td.get("table_name", td.get("name", "")),
            "desc": td.get("description", td.get("table_desc", "")),
            "reason": td.get("rejection_reason", td.get("reason", "")),
        })
    return tables


def _build_sql_summary(reason: Any) -> str:
    """SQL 요약 설명을 생성한다."""
    if not reason:
        return ""

    candidates = _get_attr_or_key(reason, "candidate_tables", [])
    if not candidates:
        return ""

    table_names = []
    for t in candidates:
        name = _get_attr_or_key(t, "table_name", "")
        desc = _get_attr_or_key(t, "description", "")
        if name:
            table_names.append(f"{desc}({name})" if desc else name)

    if not table_names:
        return ""

    return f"{', '.join(table_names)}에서 데이터를 조회했습니다."


def _extract_sql(reason: Any) -> str:
    """검증된 SQL 또는 생성된 SQL을 추출한다."""
    if not reason:
        return ""
    return (
        _get_attr_or_key(reason, "validated_sql", "")
        or _get_attr_or_key(reason, "generated_sql", "")
    )


def _format_join_path(reason: Any) -> str:
    """JOIN 경로를 사용자 친화적 문자열로 변환한다."""
    if not reason:
        return ""

    join_path = _get_attr_or_key(reason, "confirmed_join_path", None)
    if not join_path:
        return ""

    if isinstance(join_path, str):
        return join_path

    if isinstance(join_path, list):
        return " → ".join(str(p) for p in join_path)

    if hasattr(join_path, "model_dump"):
        jp = join_path.model_dump()
        return str(jp)

    return str(join_path)


def _build_references(reason: Any) -> list[dict[str, str]]:
    """참고한 자료 목록을 구성한다."""
    refs: list[dict[str, str]] = []
    if not reason:
        return refs

    # 유사 SQL 이력
    searched = _get_attr_or_key(reason, "searched_queries", [])
    if searched:
        refs.append({
            "source": "sql_history",
            "title": "유사 SQL 이력",
            "detail": f"{len(searched)}건의 유사 쿼리를 참조했습니다.",
        })

    # 탐색한 유즈케이스 (보고서 등)
    use_cases = _get_attr_or_key(reason, "explored_use_cases", [])
    if use_cases:
        refs.append({
            "source": "use_cases",
            "title": "업무 사례",
            "detail": f"{len(use_cases)}건의 관련 업무 사례를 확인했습니다.",
        })

    # 지식 항목 (매뉴얼 등)
    knowledge = _get_attr_or_key(reason, "knowledge_items", [])
    manual_count = 0
    for k in knowledge:
        source = _get_attr_or_key(k, "source", "")
        if "manual" in str(source).lower() or "qdrant" in str(source).lower():
            manual_count += 1
    if manual_count:
        refs.append({
            "source": "biz_manual",
            "title": "업무 매뉴얼",
            "detail": f"{manual_count}건의 업무 규정을 확인했습니다.",
        })

    # 샘플 데이터
    sampled = _get_attr_or_key(reason, "sampled_tables", [])
    if sampled:
        refs.append({
            "source": "data_sample",
            "title": "데이터 샘플",
            "detail": f"{len(sampled)}개 테이블의 샘플 데이터를 확인했습니다.",
        })

    return refs


def _calc_total_elapsed(trace_log: list[Any]) -> float:
    """전체 처리 시간을 계산한다 (초 단위)."""
    if not trace_log:
        return 0.0

    timestamps: list[str] = []
    for entry in trace_log:
        ts = _get_attr_or_key(entry, "timestamp", "")
        if ts:
            timestamps.append(ts)

    if len(timestamps) < 2:
        return 0.0

    # TraceEntry는 timestamp가 ISO 형식이므로 간단 파싱
    try:
        from datetime import datetime

        first = datetime.fromisoformat(timestamps[0])
        last = datetime.fromisoformat(timestamps[-1])
        return max((last - first).total_seconds(), 0.0)
    except (ValueError, TypeError):
        return 0.0


def _build_step_timings(trace_log: list[Any]) -> list[dict[str, Any]]:
    """단계별 소요 시간을 구성한다."""
    node_label_map = {
        "preprocess": "입력 전처리",
        "resolve_history": "대화 이력 분석",
        "classify_intent": "질문 의도 분석",
        "normalize_query": "질문 정규화",
        "reason_plan": "탐색 계획 수립",
        "reason_explore": "데이터 탐색",
        "reason_evaluate": "탐색 평가",
        "reason_generate_sql": "SQL 생성",
        "reason_validate_sql": "SQL 검증",
        "reason_recover": "대안 탐색",
        "reason_finalize": "결과 확정",
        "execute_sql": "데이터 조회",
        "analyze_data": "결과 분석",
        "format_response": "보고서 작성",
    }

    steps: list[dict[str, Any]] = []
    seen_nodes: dict[str, float] = {}

    for entry in trace_log:
        node = _get_attr_or_key(entry, "node", "")
        action = _get_attr_or_key(entry, "action", "")
        ts = _get_attr_or_key(entry, "timestamp", "")

        if not node or not ts:
            continue

        try:
            from datetime import datetime
            t = datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            continue

        if node not in seen_nodes:
            seen_nodes[node] = t
        else:
            elapsed = max(t - seen_nodes[node], 0.0)
            label = node_label_map.get(node, node)
            steps.append({
                "label": label,
                "node": node,
                "elapsed": round(elapsed, 1),
            })
            seen_nodes[node] = t

    # trace_log에서 쌍을 못 찾은 경우 노드별 0초로 표시
    if not steps:
        for entry in trace_log:
            node = _get_attr_or_key(entry, "node", "")
            if node and node in node_label_map:
                label = node_label_map[node]
                if not any(s["node"] == node for s in steps):
                    steps.append({
                        "label": label,
                        "node": node,
                        "elapsed": 0.0,
                    })

    return steps


def _assess_confidence(reason: Any) -> str:
    """추론 과정의 신뢰도를 평가한다."""
    if not reason:
        return "보통"

    guard = _get_attr_or_key(reason, "loop_guard", None)
    if not guard:
        return "보통"

    replans = _get_attr_or_key(guard, "replan_count", 0)
    gen_attempts = _get_attr_or_key(guard, "generate_attempts", 0)

    if replans == 0 and gen_attempts <= 1:
        return "높음"
    if replans <= 1 and gen_attempts <= 2:
        return "보통"
    return "낮음"


def _build_caveats(
    state: dict[str, Any],
    reason: Any,
) -> list[str]:
    """사용자에게 알려야 할 주의사항을 수집한다."""
    caveats: list[str] = []
    if not reason:
        return caveats

    guard = _get_attr_or_key(reason, "loop_guard", None)
    if guard and _get_attr_or_key(guard, "replan_count", 0) > 0:
        caveats.append(
            "처음 시도한 방법이 적합하지 않아 다른 접근을 시도했습니다.",
        )

    if _get_attr_or_key(reason, "structural_hints", None):
        caveats.append(
            "일부 컬럼 설명이 불완전하여 유사 SQL과 데이터 샘플을 참고하여 추론했습니다.",
        )

    candidates = _get_attr_or_key(reason, "candidate_tables", [])
    join_path = _get_attr_or_key(reason, "confirmed_join_path", None)
    if not join_path and len(candidates) > 1:
        caveats.append(
            "테이블 간 연결 경로가 명시되지 않아 컬럼명으로 추론했습니다.",
        )

    dead_ends = _get_attr_or_key(reason, "dead_ends", [])
    if dead_ends:
        caveats.append(
            f"탐색 과정에서 {len(dead_ends)}건의 막다른 경로를 발견하고 우회했습니다.",
        )

    return caveats


def _build_result_stats(sql_result: Any) -> dict[str, Any]:
    """SQL 실행 결과 통계를 구성한다."""
    if not sql_result:
        return {}

    return {
        "row_count": _get_attr_or_key(sql_result, "row_count", 0),
        "column_count": len(_get_attr_or_key(sql_result, "columns", [])),
        "execution_time_ms": _get_attr_or_key(
            sql_result, "execution_time_ms", 0.0,
        ),
    }


def _get_attr_or_key(obj: Any, key: str, default: Any = None) -> Any:
    """객체의 attribute 또는 dict key로 값을 조회한다.

    Pydantic 모델과 dict 모두 지원하기 위한 유틸리티.
    """
    if obj is None:
        return default
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default
