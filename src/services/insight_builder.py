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

from src.models.enums import SelectionStatus, TargetDbStatus
from src.utils.logger import get_logger
from src.utils.sql_formatter import format_sql_tabular

logger = get_logger(__name__)


def build_insight(state: dict[str, Any]) -> dict[str, Any]:
    """파이프라인 완료 State에서 통찰 데이터를 구성한다.

    Args:
        state: 파이프라인 최종 상태 딕셔너리

    Returns:
        UI 통찰 패널에 전달할 구조화된 딕셔너리.
        journey 키가 포함되면 프론트엔드가 저니 뷰로 렌더링한다.
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

    result = {
        "is_success": is_success,
        "query_interpretation": _build_query_interpretation(
            state, normalized,
        ),
        "reasoning_trail": _build_reasoning_trail(reason),
        # 성공 전용
        "tables_used": _build_tables_used(reason, sql_tables),
        "tables_candidate": _build_tables_candidate(
            reason, sql_tables,
        ),
        "tables_rejected": _build_tables_rejected(reason),
        "validation_detail": _build_validation_detail(reason),
        "sql_summary": _build_sql_summary(reason, sql_tables),
        "sql_code": _extract_sql(reason),
        "join_path": "",
        "references": _build_references(reason),
        "confidence": _assess_confidence(reason),
        "caveats": _build_caveats(reason),
        "total_elapsed": _calc_total_elapsed(trace_log),
        "step_timings": _build_step_timings(trace_log),
        "result_stats": _build_result_stats(sql_result),
        # 실패 전용
        "failure_narrative": _build_failure_narrative(
            state, reason,
        ),
        "dead_end_trail": _build_dead_end_trail(reason),
    }

    # 저니 뷰 데이터 (hypothesis_id 태깅된 경우만)
    journey = _build_journey(reason, trace_log)
    if journey:
        result["journey"] = journey

    return result


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
    sql = (
        _get_attr_or_key(reason, "validated_sql", "")
        or _get_attr_or_key(reason, "generated_sql", "")
        or ""
    )
    return str(sql) if sql else ""


def _table_name(ct: Any) -> str:
    """TableMeta 또는 dict에서 테이블명을 추출한다."""
    if hasattr(ct, "qualified_name"):
        return str(ct.qualified_name)
    if isinstance(ct, dict):
        return str(ct.get("table_name", ""))
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
        "query_normalizer": "질문 정규화",
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
        "sql_executor": "데이터 조회",
        "analyzer": "결과 분석",
        "visualizer": "시각화 생성",
        "formatter": "보고서 작성",
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
    """SQL 검증 신뢰도를 반환한다.

    Layer2b validator가 산출한 confidence_score(0.0~1.0)를
    '높음/보통/낮음 (점수)' 형태로 변환한다.
    score가 없으면(Layer2b 미실행 등) '보통'을 반환한다.
    """
    if not reason:
        return "보통"

    score = _get_attr_or_key(reason, "confidence_score", 0.0)
    if not score:
        return "보통"

    if score >= 0.8:
        label = "높음"
    elif score >= 0.5:
        label = "보통"
    else:
        label = "낮음"
    return f"{label} ({score:.2f})"


def _build_caveats(
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

    # 타겟 DB 자동 선택 사유 반영 (AMBIGUOUS 에서만 사용자에게 표면화)
    decision = _get_attr_or_key(reason, "target_db_decision", None)
    if decision is not None:
        status = _get_attr_or_key(decision, "status", "")
        rationale = _get_attr_or_key(decision, "decision_rationale", "")
        if status == TargetDbStatus.AMBIGUOUS and rationale:
            caveats.append(rationale)

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


def _build_reasoning_trail(reason: Any) -> list[dict[str, Any]]:
    """추론 과정을 단계별 리스트로 구성한다.

    execution_plan의 완료된 스텝 중 insight가 존재하는 것만 추출한다.
    """
    if not reason:
        return []

    plan = _get_attr_or_key(reason, "execution_plan", [])
    trail: list[dict[str, Any]] = []

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


def _build_validation_detail(reason: Any) -> list[dict[str, Any]]:
    """SQL 검증 결과 체크 항목을 구성한다.

    sql_validator Layer2b PASS 시 저장된 checks를 사용자 친화적으로 변환한다.
    """
    if not reason:
        return []

    checks = _get_attr_or_key(reason, "validation_checks", {})
    if not checks:
        return []

    label_map = {
        "filters_reflected": "필터 반영",
        "group_by_reflected": "그룹핑 반영",
        "order_rank_reflected": "정렬/순위 반영",
        "no_unconfirmed_values": "미확인 값 사용 여부",
        "no_dead_end_repeat": "실패 패턴 반복 여부",
        "logical_consistency": "논리적 정합성",
        "db_execution": "DB 실행 결과",
        "code_name_paired": "코드 명칭 동반",
    }

    items: list[dict[str, Any]] = []
    for key, value in checks.items():
        if not isinstance(value, dict):
            continue
        detail = value.get("detail", "")
        if not detail:
            continue
        items.append({
            "label": label_map.get(key, key),
            "detail": detail,
            "pass": value.get("verdict") != "FAIL",
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
        return str(state.get("error_message", ""))

    summary = _get_attr_or_key(reason, "exploration_summary", "")

    # exploration_summary가 있고, 단순 규칙 기반 메시지("SQL 생성 실패"로 시작)가
    # 아닌 경우 → give_up_reason(LLM 총평)으로 간주하여 그대로 사용
    if summary:
        return str(summary)

    return str(state.get("error_message", ""))


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
        result: dict[str, Any] = obj.model_dump()
        return result
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 저니 뷰 빌더 — 가설 기반 추론 과정 트리 구조
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_TOOL_LABELS: dict[str, str] = {
    "search_table_meta": "테이블 메타 검색",
    "lookup_table_meta": "테이블 메타 조회",
    "search_use_cases": "유사 SQL 검색",
    "search_manual": "업무 매뉴얼 검색",
    "search_biz_terms": "비즈용어 검색",
    "lookup_code_meta": "코드 메타 조회",
    "get_sample_rows": "샘플 데이터 조회",
    "get_column_values": "컬럼값 조회",
    "get_column_profile": "컬럼 프로파일",
    "get_date_distribution": "날짜 분포 조회",
}

_VERDICT_LABELS: dict[str, str] = {
    "generate_sql": "SQL 생성 진행",
    "explore": "추가 탐색 필요",
    "replan": "재계획 수립",
    "ask_user": "사용자 확인 필요",
    "conclude_failure": "탐색 종료",
}


def _build_journey(
    reason: Any,
    trace_log: list[Any],
) -> dict[str, Any] | None:
    """가설 기반 저니 뷰 데이터를 구성한다.

    hypothesis_id가 태깅된 데이터가 없으면 None을 반환하여
    프론트엔드에서 기존 flat 뷰로 폴백한다.
    """
    if not reason:
        return None

    hypotheses = _get_attr_or_key(reason, "hypotheses", [])
    if not hypotheses:
        return None

    plan = _get_attr_or_key(reason, "execution_plan", [])
    # hypothesis_id가 태깅되지 않은 레거시 데이터이면 스킵
    has_tagged = any(
        _get_attr_or_key(s, "hypothesis_id", "")
        for s in plan
    )
    if not has_tagged:
        return None

    explored_tables = _get_attr_or_key(
        reason, "explored_tables", [],
    )
    explored_ucs = _get_attr_or_key(
        reason, "explored_use_cases", [],
    )
    explored_manuals = _get_attr_or_key(
        reason, "explored_biz_manuals", [],
    )
    explored_terms = _get_attr_or_key(
        reason, "explored_biz_terms", [],
    )
    dead_ends = _get_attr_or_key(reason, "dead_ends", [])

    hypo_list: list[dict[str, Any]] = []
    for hypo in hypotheses:
        hd = _to_dict(hypo) if not isinstance(hypo, dict) else hypo
        if hd is None:
            continue
        hid = hd.get("hypothesis_id", "")
        hypo_entry = _build_journey_hypothesis(
            hd, hid, plan,
            explored_tables, explored_ucs,
            explored_manuals, explored_terms,
            dead_ends, reason,
        )
        hypo_list.append(hypo_entry)

    caveats = _build_journey_caveats(reason)

    return {
        "hypotheses": hypo_list,
        "caveats": caveats,
        "total_elapsed": _calc_total_elapsed(trace_log),
        "step_timings": _build_step_timings(trace_log),
    }


def _build_journey_hypothesis(
    hd: dict[str, Any],
    hid: str,
    plan: list[Any],
    explored_tables: list[Any],
    explored_ucs: list[Any],
    explored_manuals: list[Any],
    explored_terms: list[Any],
    dead_ends: list[Any],
    reason: Any,
) -> dict[str, Any]:
    """단일 가설의 저니 데이터를 구성한다."""
    # 이 가설에 속한 스텝 필터
    steps = [
        s for s in plan
        if _get_attr_or_key(s, "hypothesis_id", "") == hid
        and _get_attr_or_key(s, "status", "") != "PENDING"
    ]

    tool_calls = _build_tool_calls(
        steps, hid,
        explored_tables, explored_ucs,
        explored_manuals, explored_terms,
    )

    entry: dict[str, Any] = {
        "id": hid,
        "description": hd.get("description", ""),
        "strategy": hd.get("strategy", ""),
        "status": hd.get("status", "PENDING"),
        "tool_calls": tool_calls,
    }

    # 준비도 판정
    r_score = hd.get("readiness_score")
    r_verdict = hd.get("readiness_verdict", "")
    if r_score is not None:
        entry["readiness"] = {
            "score": r_score,
            "verdict": r_verdict,
            "label": _VERDICT_LABELS.get(
                r_verdict, r_verdict,
            ),
        }

    # SQL (최종 성공 가설에만 있음)
    sql = (
        _get_attr_or_key(reason, "validated_sql", "")
        or _get_attr_or_key(reason, "generated_sql", "")
    )
    status = hd.get("status", "")
    if sql and status != "FAILED":
        entry["sql"] = format_sql_tabular(sql)
        # 검증 결과
        validation = _build_journey_validation(reason)
        if validation:
            entry["validation"] = validation

    # 실패 (dead_end에서 매칭)
    for de in dead_ends:
        de_d = (
            _to_dict(de)
            if not isinstance(de, dict) else de
        )
        if de_d and de_d.get("hypothesis_id") == hid:
            entry["failure"] = {
                "type": str(
                    de_d.get("failure_type", ""),
                ),
                "reason": de_d.get("reason", ""),
                "lesson": de_d.get(
                    "lessons_learned", "",
                ),
            }
            break

    return entry


def _build_tool_calls(
    steps: list[Any],
    hid: str,
    explored_tables: list[Any],
    explored_ucs: list[Any],
    explored_manuals: list[Any],
    explored_terms: list[Any],
) -> list[dict[str, Any]]:
    """가설에 속한 스텝들의 도구 호출 + 채택/제외 정보."""
    calls: list[dict[str, Any]] = []

    for step in steps:
        sd = (
            _to_dict(step)
            if not isinstance(step, dict) else step
        )
        if sd is None:
            continue

        tool = sd.get("tool", "")
        raw_input = sd.get("input", "")
        step_num = sd.get("step", 0)

        query, page = _parse_tool_input(raw_input)

        # 이 스텝에서 발견된 탐색결과 매칭
        adopted, rejected = _match_explored_items(
            tool, step_num, hid,
            explored_tables, explored_ucs,
            explored_manuals, explored_terms,
        )

        result_count = len(adopted) + len(rejected)

        call: dict[str, Any] = {
            "step": step_num,
            "tool": tool,
            "label": _TOOL_LABELS.get(tool, tool),
            "query": query,
            "result_count": result_count,
            "adopted": adopted,
            "rejected": rejected,
        }
        if page:
            call["page"] = page

        calls.append(call)

    return calls


def _parse_tool_input(raw: str) -> tuple[str, int | None]:
    """도구 입력 문자열에서 query와 page를 분리한다.

    형식: "검색어, page=N" 또는 "검색어, N" 또는 "검색어"
    """
    if not raw:
        return "", None

    parts = [p.strip() for p in raw.split(",")]
    query_parts: list[str] = []
    page: int | None = None

    for p in parts:
        if p.startswith("page="):
            try:
                page = int(p.split("=")[1])
            except (ValueError, IndexError):
                query_parts.append(p)
        elif (
            len(parts) > 1
            and p == parts[-1]
            and p.isdigit()
        ):
            page = int(p)
        else:
            query_parts.append(p)

    return ", ".join(query_parts), page


def _match_explored_items(
    tool: str,
    step_num: int,
    hid: str,
    explored_tables: list[Any],
    explored_ucs: list[Any],
    explored_manuals: list[Any],
    explored_terms: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """도구 호출에 매칭되는 채택/제외 탐색결과를 분류."""
    adopted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    if tool in (
        "search_table_meta", "lookup_table_meta",
    ):
        for t in explored_tables:
            if not _matches_step(t, step_num, hid):
                continue
            item = _table_to_item(t)
            if _is_adopted(t):
                adopted.append(item)
            else:
                rejected.append(item)

    elif tool == "search_use_cases":
        for uc in explored_ucs:
            if not _matches_step(uc, step_num, hid):
                continue
            item = _use_case_to_item(uc)
            if _get_attr_or_key(uc, "relevant", False):
                adopted.append(item)
            else:
                rejected.append(item)

    elif tool == "search_manual":
        for m in explored_manuals:
            if not _matches_step(m, step_num, hid):
                continue
            item = _manual_to_item(m)
            status = _get_attr_or_key(
                m, "selection_status", "",
            )
            if status == SelectionStatus.SELECTED:
                adopted.append(item)
            elif status == SelectionStatus.REJECTED:
                rejected.append(item)

    elif tool == "search_biz_terms":
        for bt in explored_terms:
            if not _matches_step(bt, step_num, hid):
                continue
            item = _biz_term_to_item(bt)
            status = _get_attr_or_key(
                bt, "selection_status", "",
            )
            if status == SelectionStatus.SELECTED:
                adopted.append(item)
            elif status == SelectionStatus.REJECTED:
                rejected.append(item)

    return adopted, rejected


def _matches_step(
    item: Any, step_num: int, hid: str,
) -> bool:
    """탐색결과가 특정 스텝+가설에 매칭되는지 판정."""
    item_step = _get_attr_or_key(item, "source_step", 0)
    item_hid = _get_attr_or_key(
        item, "hypothesis_id", "",
    )
    return bool(item_step == step_num and item_hid == hid)


def _is_adopted(table: Any) -> bool:
    """테이블이 채택(SELECTED)인지 판정."""
    status = _get_attr_or_key(
        table, "selection_status", "",
    )
    return bool(status == SelectionStatus.SELECTED)


def _table_to_item(t: Any) -> dict[str, Any]:
    """TableMeta → 저니 뷰 아이템."""
    name = _get_attr_or_key(t, "table_name", "")
    alt = _get_attr_or_key(t, "alt_name", "")
    display = f"{name} ({alt})" if alt else name
    return {
        "type": "table",
        "name": display,
        "label": "",
        "reason": _get_attr_or_key(
            t, "selection_reason", "",
        ),
    }


def _use_case_to_item(uc: Any) -> dict[str, Any]:
    """UseCaseEntry → 저니 뷰 아이템."""
    desc = _get_attr_or_key(uc, "description", "")
    return {
        "type": "use_case",
        "name": desc or _get_attr_or_key(uc, "id", ""),
        "label": "",
        "reason": _get_attr_or_key(
            uc, "eval_reason", "",
        ),
    }


def _manual_to_item(m: Any) -> dict[str, Any]:
    """BizManualEntry → 저니 뷰 아이템."""
    content = _get_attr_or_key(m, "content", "")
    _MAX = 60
    label = (
        content[:_MAX] + "…"
        if len(content) > _MAX else content
    )
    return {
        "type": "manual",
        "name": _get_attr_or_key(
            m, "biz_manual_id", "",
        ),
        "label": label,
        "reason": _get_attr_or_key(
            m, "selection_reason", "",
        ),
    }


def _biz_term_to_item(bt: Any) -> dict[str, Any]:
    """BizTermEntry → 저니 뷰 아이템."""
    return {
        "type": "biz_term",
        "name": _get_attr_or_key(bt, "term", ""),
        "label": _get_attr_or_key(
            bt, "definition", "",
        ),
        "reason": _get_attr_or_key(
            bt, "selection_reason", "",
        ),
    }


def _build_journey_validation(
    reason: Any,
) -> dict[str, Any] | None:
    """저니 뷰용 검증 결과를 구성한다.

    성공 시 전체 항목, 실패 시 실패 항목만 표시.
    """
    checks = _get_attr_or_key(
        reason, "validation_checks", {},
    )
    if not checks:
        return None

    label_map = {
        "filters_reflected": "필터 반영",
        "group_by_reflected": "그룹핑 반영",
        "order_rank_reflected": "정렬/순위 반영",
        "no_unconfirmed_values": "미확인 값 사용",
        "no_dead_end_repeat": "실패 패턴 반복",
        "logical_consistency": "논리적 정합성",
        "db_execution": "DB 실행 결과",
        "code_name_paired": "코드 명칭 동반",
    }

    all_pass = all(
        v.get("verdict") != "FAIL"
        for v in checks.values()
        if isinstance(v, dict)
    )

    items: list[dict[str, Any]] = []
    for key, value in checks.items():
        if not isinstance(value, dict):
            continue
        passed = value.get("verdict") != "FAIL"
        # 실패 시 실패 항목만, 성공 시 전체
        if not all_pass and passed:
            continue
        items.append({
            "label": label_map.get(key, key),
            "pass": passed,
            "detail": value.get("detail", ""),
        })

    summary = _get_attr_or_key(
        reason, "validation_summary", "",
    )

    return {
        "pass": all_pass,
        "summary": summary,
        "items": items,
    }


def _build_journey_caveats(reason: Any) -> list[str]:
    """저니 뷰용 caveats — 중복 제거된 고유 경고만.

    저니 뷰에서 이미 표현되는 정보(replan 발생, dead_end)는
    제외하고, 고유한 경고(다중 테이블 조인, DB 선택 모호)만 유지.
    """
    caveats: list[str] = []
    if not reason:
        return caveats

    # 다중 테이블 조인 경고 (고유)
    candidates = _get_attr_or_key(
        reason, "explored_tables", [],
    )
    selected = [
        ct for ct in candidates
        if _get_attr_or_key(
            ct, "selection_status", "",
        ) != SelectionStatus.REJECTED
    ]
    if len(selected) > 1:
        caveats.append(
            "다중 테이블 사용 — "
            "조인 조건은 LLM이 컬럼명으로 추론했습니다.",
        )

    # 타겟 DB AMBIGUOUS 경고 (고유)
    decision = _get_attr_or_key(
        reason, "target_db_decision", None,
    )
    if decision is not None:
        status = _get_attr_or_key(
            decision, "status", "",
        )
        rationale = _get_attr_or_key(
            decision, "decision_rationale", "",
        )
        if status == TargetDbStatus.AMBIGUOUS and rationale:
            caveats.append(rationale)

    return caveats
