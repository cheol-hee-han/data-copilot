"""5단계 조회 과정 요약 빌더.

작성자: 한철희 / 최종수정: 2026-04-07

State 필드에서 조회 과정 정보를 추출하여 구조화된 dict를 생성한다.
stream.end의 process_summary 필드로 전송되어 프론트엔드에서 접기 블록으로 렌더링된다.

5단계 구조:
    1. 질의 분류 — intent, is_continuation
    2. 질의 해석 — normalized_query (measures, filters, time, entities 등)
    3. 활용 정보 — explored_tables, explored_use_cases, explored_biz_manuals
    4. AI 판단  — resolved_signals(INFER), pending_assumptions
    5. 검증 결과 — validation_summary, sql_result.row_count
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.models.enums import SelectionStatus, TargetDbStatus
from src.utils.sql_formatter import format_sql_tabular
from src.utils.sqlglot_analyzer import get_real_tables

if TYPE_CHECKING:
    from src.agents.state.state import PipelineState


def build_process_summary(state: PipelineState) -> dict[str, Any] | None:
    """5단계 조회 과정 요약을 구조화 dict로 반환한다."""
    intent = _build_intent_dict(state)
    interpretation = _build_interpretation_dict(state)
    context = _build_context_dict(state)
    ai_decisions = _build_ai_decision_dict(state)
    validation = _build_validation_dict(state)

    if not any([intent, interpretation, context, validation]):
        return None

    result: dict[str, Any] = {
        "intent": intent,
        "interpretation": interpretation,
        "context": context,
        "validation": validation,
    }
    if ai_decisions:
        result["ai_decisions"] = ai_decisions
    return result


_INTENT_LABELS = {
    "data_extraction": "데이터 추출",
    "data_analysis": "데이터 분석",
}


def _build_intent_dict(state: PipelineState) -> dict[str, Any]:
    """1단계: 질의 분류."""
    return {
        "label": _INTENT_LABELS.get(state.intent.value, state.intent.value),
        "is_continuation": state.is_continuation,
    }


def _build_interpretation_dict(state: PipelineState) -> dict[str, Any]:
    """2단계: 질의 해석 — normalized_query 슬롯 요약."""
    nq = state.normalized_query
    if not nq:
        return {}

    result: dict[str, Any] = {}
    if nq.rewritten_query and nq.rewritten_query != nq.original_query:
        result["rewritten_query"] = nq.rewritten_query
    if nq.measures:
        terms = [m.term for m in nq.measures if m.term]
        if terms:
            result["measures"] = terms
    if nq.filters:
        items = [f.target for f in nq.filters if f.target]
        if items:
            result["filters"] = items
    if nq.time and nq.time.base_period:
        result["period"] = (
            nq.time.base_period.label
            or nq.time.base_period.resolve
            or str(nq.time.type)
        )
    if nq.entities:
        result["entities"] = [e.term for e in nq.entities]
    if nq.dimensions:
        result["dimensions"] = [d.term for d in nq.dimensions]
    return result


def _build_context_dict(state: PipelineState) -> dict[str, Any]:
    """3단계: 활용 정보 — 탐색된 테이블, 유사 SQL, 매뉴얼, 용어."""
    reason = state.reason
    result: dict[str, Any] = {}

    # SQL에서 실제 사용된 테이블명 추출
    sql = reason.validated_sql or reason.generated_sql or ""
    sql_tables: set[str] = set()
    if sql:
        sql_tables = {t.upper() for t in get_real_tables(sql)}

    non_rejected = [
        t for t in reason.explored_tables
        if t.selection_status != SelectionStatus.REJECTED
    ]
    if non_rejected:
        result["tables"] = [
            {
                "name": t.table_name,
                "label": t.alt_name or t.description or t.table_name,
                "status": t.selection_status.value,
                "used": t.table_name.upper() in sql_tables,
                "reason": t.selection_reason or "",
            }
            for t in non_rejected
        ]

    rejected = [
        t for t in reason.explored_tables
        if t.selection_status == SelectionStatus.REJECTED
    ]
    if rejected:
        result["rejected_tables"] = [
            {
                "name": t.table_name,
                "label": t.alt_name or t.description or t.table_name,
            }
            for t in rejected
        ]

    relevant_ucs = [uc for uc in reason.explored_use_cases if uc.relevant]
    if relevant_ucs:
        result["use_cases"] = [
            uc.description for uc in relevant_ucs if uc.description
        ]

    selected_manuals = [
        m for m in reason.explored_biz_manuals
        if m.selection_status == SelectionStatus.SELECTED
    ]
    if selected_manuals:
        _MAX_MANUAL_LEN = 60
        result["manuals"] = [
            m.content[:_MAX_MANUAL_LEN] + ("…" if len(m.content) > _MAX_MANUAL_LEN else "")
            for m in selected_manuals if m.content
        ]

    selected_terms = [
        bt for bt in reason.explored_biz_terms
        if bt.selection_status == SelectionStatus.SELECTED
    ]
    if selected_terms:
        result["biz_terms"] = [bt.term for bt in selected_terms]

    return result


def _build_ai_decision_dict(state: PipelineState) -> dict[str, Any] | None:
    """4단계: AI 판단 — INFER 시그널 + pending_assumptions.

    내용이 없으면 None을 반환하여 섹션 자체를 생략한다.
    같은 질의-값 쌍이 여러 노드에서 생성될 수 있어
    question+value 기준으로 중복을 제거한다.
    """
    inferences: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    # 타겟 DB 자동 선택 사유 (AMBIGUOUS 에서만 사용자에게 표면화)
    decision = state.reason.target_db_decision
    if (decision is not None
            and decision.status == TargetDbStatus.AMBIGUOUS
            and decision.decision_rationale):
        inferences.append({
            "question": "사용할 DB 결정",
            "value": decision.decision_rationale,
            "source_node": "readiness_gate",
        })
        seen_keys.add(
            f"사용할 DB 결정|{decision.decision_rationale}",
        )

    tid = state.turn_id
    if tid:
        for s in state.resolved_signals:
            if (
                s.decision == "INFER"
                and s.turn_id is not None
                and s.turn_id == tid
            ):
                key = f"{s.question}|{s.inferred_value}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                inferences.append({
                    "question": s.question,
                    "value": s.inferred_value or "",
                    "source_node": s.source_node,
                })

    # pending_assumptions 중 inferences와 겹치는 항목 제거
    pending: list[str] = []
    for a in state.reason.pending_assumptions:
        a_text = a if isinstance(a, str) else str(a)
        # inferences의 value와 겹치면 스킵
        if any(a_text in inf["value"] or inf["value"] in a_text
               for inf in inferences if inf.get("value")):
            continue
        pending.append(a_text)

    if not inferences and not pending:
        return None

    result: dict[str, Any] = {}
    if inferences:
        result["inferences"] = inferences
    if pending:
        result["pending_assumptions"] = pending
    result["notice"] = "다른 기준을 원하시면 말씀해 주세요"
    return result


def _build_validation_dict(state: PipelineState) -> dict[str, Any]:
    """5단계: 결과 — 실행 SQL + 검증 결과 + 조회 건수."""
    reason = state.reason
    result: dict[str, Any] = {}

    if reason.validated_sql or reason.generated_sql:
        result["sql"] = format_sql_tabular(
            reason.validated_sql or reason.generated_sql or "",
        )

    if reason.validation_summary:
        result["summary"] = reason.validation_summary
    else:
        checks = reason.validation_checks
        if checks:
            passed = sum(
                1 for v in checks.values()
                if isinstance(v, dict) and v.get("pass")
            )
            total = sum(
                1 for v in checks.values()
                if isinstance(v, dict)
            )
            result["summary"] = f"SQL 검증: {total}개 항목 중 {passed}개 통과"

    if state.sql_result and state.sql_result.row_count > 0:
        result["row_count"] = state.sql_result.row_count
        result["row_label"] = f"{state.sql_result.row_count:,}건 조회 완료."
    elif state.sql_result:
        result["row_count"] = 0
        result["row_label"] = "조회 결과 0건."

    return result
