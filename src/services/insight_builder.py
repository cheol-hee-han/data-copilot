"""통찰(Insight) 데이터 빌더 — 파이프라인 추론 과정을 사용자 친화적으로 구성한다.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

파이프라인 실행 완료 후 PipelineState에서 추론 과정 데이터를 추출하여
UI의 통찰 패널에 표시할 구조화된 딕셔너리를 반환한다.
IT 비전문자인 은행 직원이 "AI가 왜 이런 결과를 냈는지"를 이해할 수 있도록
추론 경로, 참조 자료, 신뢰도, 주의사항 등을 비기술적 언어로 재구성한다.

State의 ReasoningState(Pydantic 모델) 또는 dict 형태 양쪽을 모두 지원하기 위해
_get_attr_or_key 유틸리티를 사용하며, validated_sql에서 sqlglot으로 실제 사용
테이블을 추출하여 '사용/후보/제외' 3단계로 분류한다.

핵심 함수:
    - build_insight: State에서 통찰 데이터를 구성하여 dict로 반환
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.models.enums import SelectionStatus
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

    is_success = bool(
        reason and _get_attr_or_key(reason, "validated_sql", ""),
    )

    # SQL에서 실제 사용된 테이블명 추출
    sql_tables = _extract_tables_from_sql(reason)

    return {
        "is_success": is_success,
        "query_interpretation": _build_query_interpretation(state, normalized),
        "reasoning_trail": _build_reasoning_trail(reason),
        # 성공 전용
        "tables_used": _build_tables_used(reason, sql_tables),
        "tables_candidate": _build_tables_candidate(reason, sql_tables),
        "tables_rejected": _build_tables_rejected(reason),
        "validation_detail": _build_validation_detail(reason),
        "sql_summary": _build_sql_summary(reason, sql_tables),
        "sql_code": _extract_sql(reason),
        "join_path": "",
        "references": _build_references(reason),
        "confidence": _assess_confidence(reason),
        "caveats": _build_caveats(state, reason),
        "total_elapsed": _calc_total_elapsed(trace_log),
        "step_timings": _build_step_timings(trace_log),
        "result_stats": _build_result_stats(sql_result),
        # 실패 전용
        "failure_narrative": _build_failure_narrative(state, reason),
        "dead_end_trail": _build_dead_end_trail(reason),
    }


def _extract_tables_from_sql(reason: Any) -> set[str]:
    """validated_sql에서 실제 사용된 테이블명을 sqlglot으로 추출한다.

    explored_tables 전체가 아닌 SQL에 실제 등장하는 테이블만 추출하여
    '사용/후보/제외' 3단계 분류의 기준으로 사용한다.
    """
    sql = (
        _get_attr_or_key(reason, "validated_sql", "")
        or _get_attr_or_key(reason, "generated_sql", "")
    )
    if not sql:
        return set()

    try:
        from src.utils.sqlglot_analyzer import get_real_tables
        return set(get_real_tables(sql))
    except Exception as e:
        logger.debug("SQL 테이블 추출 실패", error=str(e))
        return set()


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


def _build_tables_used(
    reason: Any,
    sql_tables: set[str],
) -> list[dict[str, Any]]:
    """SQL에 실제 사용된 테이블 목록을 구성한다."""
    if not reason or not sql_tables:
        return []

    candidates = _get_attr_or_key(reason, "explored_tables", [])
    tables = []
    for t in candidates:
        td = _to_dict(t)
        if td is None:
            continue
        table_name = td.get("table_name", td.get("name", ""))
        if table_name not in sql_tables:
            continue
        tables.append({
            "name": table_name,
            "alt_name": td.get("alt_name", ""),
            "desc": td.get("selection_reason", "") or td.get("description", ""),
            "reason": td.get("selection_reason", ""),
            "columns": td.get("columns_used", td.get("key_columns", [])),
        })
    return tables


def _build_tables_candidate(
    reason: Any,
    sql_tables: set[str],
) -> list[dict[str, Any]]:
    """후보였으나 SQL에 미사용된 테이블 목록을 구성한다."""
    if not reason:
        return []

    candidates = _get_attr_or_key(reason, "explored_tables", [])
    tables = []
    for t in candidates:
        td = _to_dict(t)
        if td is None:
            continue
        table_name = td.get("table_name", td.get("name", ""))
        status = td.get("selection_status", "")
        # REJECTED는 별도 분류, SQL에 사용된 것도 제외
        if status == SelectionStatus.REJECTED or table_name in sql_tables:
            continue
        tables.append({
            "name": table_name,
            "alt_name": td.get("alt_name", ""),
            "desc": td.get("selection_reason", "") or td.get("description", ""),
            "reason": td.get("selection_reason", ""),
        })
    return tables


def _build_tables_rejected(reason: Any) -> list[dict[str, Any]]:
    """제외된 테이블 목록을 구성한다."""
    if not reason:
        return []

    candidates = _get_attr_or_key(reason, "explored_tables", [])
    tables = []
    for t in candidates:
        td = _to_dict(t)
        if td is None:
            continue
        status = td.get("selection_status", "")
        if status != SelectionStatus.REJECTED:
            continue
        tables.append({
            "name": td.get("table_name", td.get("name", "")),
            "alt_name": td.get("alt_name", ""),
            "desc": td.get("description", ""),
            "reason": td.get("selection_reason", ""),
        })
    return tables


def _build_sql_summary(reason: Any, sql_tables: set[str]) -> str:
    """SQL 요약 설명을 생성한다."""
    if not reason or not sql_tables:
        return ""

    candidates = _get_attr_or_key(reason, "explored_tables", [])
    table_names = []
    for t in candidates:
        name = _get_attr_or_key(t, "table_name", "")
        if name not in sql_tables:
            continue
        alt_name = _get_attr_or_key(t, "alt_name", "")
        desc = _get_attr_or_key(t, "description", "")
        label = alt_name or desc
        table_names.append(f"{label}({name})" if label else name)

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


def _table_name(ct: Any) -> str:
    """TableMeta 또는 dict에서 테이블명을 추출한다."""
    if hasattr(ct, "qualified_name"):
        return ct.qualified_name
    if isinstance(ct, dict):
        return ct.get("table_name", "")
    return str(ct)


def _build_references(reason: Any) -> list[dict[str, str]]:
    """참고한 자료 목록을 구성한다."""
    refs: list[dict[str, str]] = []
    if not reason:
        return refs

    # 탐색한 유즈케이스 (유사 SQL 이력 + 보고서 등)
    use_cases = _get_attr_or_key(reason, "explored_use_cases", [])
    if use_cases:
        refs.append({
            "source": "use_cases",
            "title": "유사 SQL 이력",
            "detail": f"{len(use_cases)}건의 유사 쿼리를 참조했습니다.",
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

    # 샘플 데이터 — explored_tables에서 sample_rows가 있는 것을 카운트
    candidates = _get_attr_or_key(reason, "explored_tables", [])
    sampled_count = sum(
        1 for t in candidates
        if _get_attr_or_key(t, "sample_rows", [])
    )
    if sampled_count:
        refs.append({
            "source": "data_sample",
            "title": "데이터 샘플",
            "detail": f"{sampled_count}개 테이블의 샘플 데이터를 확인했습니다.",
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
        "reasoning_preparer": "탐색 준비",
        "context_explorer": "데이터 탐색",
        "context_retriever": "데이터 수집",
        "context_interpreter": "데이터 해석",
        "readiness_gate": "탐색 평가",
        "recovery_agent": "복구 탐색",
        "sql_generator": "SQL 생성",
        "sql_validator": "SQL 검증",
        "recovery_planner": "대안 탐색",
        "result_finalizer": "결과 확정",
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

    candidates = _get_attr_or_key(reason, "explored_tables", [])
    selected = [
        ct for ct in candidates
        if _get_attr_or_key(ct, "selection_status", "") != SelectionStatus.REJECTED
    ]
    if len(selected) > 1:
        caveats.append(
            "다중 테이블 사용 — 조인 조건은 LLM이 컬럼명으로 추론했습니다.",
        )

    dead_ends = _get_attr_or_key(reason, "dead_ends", [])
    if dead_ends:
        caveats.append(
            f"탐색 과정에서 {len(dead_ends)}건의 막다른 경로를 발견하고 우회했습니다.",
        )

    # SQL 생성 시 해석적 선택 반영
    resolved = state.get("resolved_signals", [])
    sql_assumptions = [
        s for s in resolved
        if (getattr(s, "source_node", "") == "sql_generator"
            and getattr(s, "decision", "") == "INFER")
    ]
    for s in sql_assumptions:
        q = getattr(s, "question", "")
        v = getattr(s, "inferred_value", "")
        if q and v and q != v:
            caveats.append(f"{q} → {v}")
        elif q:
            caveats.append(q)

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


def _build_reasoning_trail(reason: Any) -> list[dict[str, str]]:
    """추론 과정을 단계별 리스트로 구성한다.

    execution_plan의 완료된 스텝 중 insight가 존재하는 것만 추출한다.
    """
    if not reason:
        return []

    plan = _get_attr_or_key(reason, "execution_plan", [])
    trail: list[dict[str, str]] = []

    for step in plan:
        if hasattr(step, "model_dump"):
            sd = step.model_dump()
        elif isinstance(step, dict):
            sd = step
        else:
            continue

        insight_text = sd.get("insight") or ""
        if not insight_text:
            continue

        # ⚠️ 표시: 한계/부재/실패 관련 insight
        is_warning = any(
            kw in insight_text
            for kw in ("부재", "부족", "불가", "없어", "없음", "실패", "제한")
        )

        trail.append({
            "text": insight_text,
            "tool": sd.get("tool", ""),
            "warning": is_warning,
        })

    return trail


def _build_validation_detail(reason: Any) -> list[dict[str, str]]:
    """SQL 검증 결과 체크 항목을 구성한다.

    sql_validator Layer2b PASS 시 저장된 checks를 사용자 친화적으로 변환한다.
    """
    if not reason:
        return []

    checks = _get_attr_or_key(reason, "validation_checks", {})
    if not checks:
        return []

    label_map = {
        "measure_reflected": "측정값 반영",
        "filters_reflected": "필터 반영",
        "group_by_reflected": "그룹핑 반영",
        "order_limit_reflected": "정렬/제한 반영",
        "no_unconfirmed_values": "미확인 값 사용 여부",
        "no_dead_end_repeat": "실패 패턴 반복 여부",
        "logical_consistency": "논리적 정합성",
    }

    items: list[dict[str, str]] = []
    for key, value in checks.items():
        if not isinstance(value, dict):
            continue
        detail = value.get("detail", "")
        if not detail:
            continue
        items.append({
            "label": label_map.get(key, key),
            "detail": detail,
            "pass": value.get("pass", True),
        })

    # 검증 총평 추가
    summary = _get_attr_or_key(
        reason, "validation_summary", "",
    )
    if summary:
        items.append({
            "label": "검증 총평",
            "detail": summary,
            "pass": True,
        })

    return items


def _build_failure_narrative(
    state: dict[str, Any],
    reason: Any,
) -> str:
    """실패 원인 내러티브를 구성한다.

    give_up_reason(LLM 총평) > exploration_summary > error_message 우선순위.
    """
    if not reason:
        return state.get("error_message", "")

    summary = _get_attr_or_key(reason, "exploration_summary", "")

    # exploration_summary가 있고, 단순 규칙 기반 메시지("SQL 생성 실패"로 시작)가
    # 아닌 경우 → give_up_reason(LLM 총평)으로 간주하여 그대로 사용
    if summary:
        return summary

    return state.get("error_message", "")


def _build_dead_end_trail(reason: Any) -> list[dict[str, str]]:
    """시도한 접근 경로를 구성한다.

    dead_ends 배열에서 실패 유형, 사유, 교훈을 추출한다.
    """
    if not reason:
        return []

    dead_ends = _get_attr_or_key(reason, "dead_ends", [])
    trail: list[dict[str, str]] = []

    for de in dead_ends:
        if hasattr(de, "model_dump"):
            dd = de.model_dump()
        elif isinstance(de, dict):
            dd = de
        else:
            continue

        trail.append({
            "failure_type": str(dd.get("failure_type", "")),
            "reason": dd.get("reason", ""),
            "lessons_learned": dd.get("lessons_learned", ""),
        })

    return trail


def _to_dict(obj: Any) -> dict[str, Any] | None:
    """Pydantic 모델 또는 dict를 dict로 변환한다.

    State가 Pydantic 모델(노드 실행 중)과 dict(직렬화 후 복원) 양쪽으로
    전달될 수 있으므로 두 형태를 통합 처리한다.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    return None


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
