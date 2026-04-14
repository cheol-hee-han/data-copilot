"""target_db 결정 서비스 — readiness_gate 진입 시 단일 진실원 확정.

작성자: 한철희 / 최종수정: 2026-04-11

readiness_gate 가 GENERATING 단계로 전이할 때 호출되어
ReasoningState.target_db / target_db_decision 을 채운다.

결정 우선순위:
    1. settings.target_db_code(시스템 코드, 예: ADW/BDP/CRP)가
       지정되어 있으면 → FORCED
    2. SELECTED 테이블이 없으면 → NO_SELECTION (호출부에서 fail 처리)
    3. SELECTED 테이블이 단일 시스템이면 → SINGLE
    4. 복수 시스템 혼재 → AMBIGUOUS (자동 선정하지 않고 사용자 명확화 요청)

본 서비스는 시스템 코드 어휘만 다룬다. 물리 커넥터로의 override 는
ConnectorManager.get_query_db 내부의 settings.resolve_system_connector 가
수행한다 (단일 적용 지점).
"""

from __future__ import annotations

from src.agents.state.state import (
    ReasoningState,
    SelectionStatus,
    TableMeta,
    TargetDbDecision,
    TargetDbStatus,
)
from src.config import Settings


def _table_db_source(ct: TableMeta) -> str:
    """TableMeta.db_source 를 그대로 반환한다.

    from_meta 가 이미 ConnectorManager.parse_db_source 로 시스템 코드를
    태깅하므로 추가 파싱이 불필요하다.
    """
    return ct.db_source


def resolve_target_db(
    reason: ReasoningState,
    settings: Settings,
) -> TargetDbDecision:
    """SELECTED 테이블 집합과 settings 를 근거로 target_db 를 결정한다.

    Args:
        reason: 추론 상태. explored_tables 의 SELECTED 항목만 고려한다.
        settings: 애플리케이션 설정. target_db_code 를 참조한다.

    Returns:
        TargetDbDecision: status / target / chosen_tables / dropped_tables /
            decision_rationale 가 채워진 결정 객체.
    """
    # 1. FORCED — settings.target_db_code(시스템 코드) 강제 지정
    if settings.target_db_code:
        target_system = settings.target_db_code
        chosen: list[str] = []
        dropped: list[tuple[str, str]] = []
        for ct in reason.explored_tables:
            if ct.selection_status != SelectionStatus.SELECTED:
                continue
            src = _table_db_source(ct)
            if src == target_system:
                chosen.append(ct.table_name)
            else:
                dropped.append((ct.table_name, src or "unknown"))
        return TargetDbDecision(
            status=TargetDbStatus.FORCED,
            target=target_system,
            chosen_tables=chosen,
            dropped_tables=dropped,
            decision_rationale=(
                f"운영 설정에 의해 '{target_system}' 시스템만 사용합니다."
            ),
        )

    # 2/3/4. SELECTED 테이블 기반 동적 결정
    sources: dict[str, list[str]] = {}
    for ct in reason.explored_tables:
        if ct.selection_status != SelectionStatus.SELECTED:
            continue
        src = _table_db_source(ct)
        if not src:
            continue
        sources.setdefault(src, []).append(ct.table_name)

    if not sources:
        return TargetDbDecision(
            status=TargetDbStatus.NO_SELECTION,
            target="",
            decision_rationale=(
                "선택된 테이블이 없어 사용할 DB를 결정할 수 없습니다."
            ),
        )

    if len(sources) == 1:
        target = next(iter(sources))
        return TargetDbDecision(
            status=TargetDbStatus.SINGLE,
            target=target,
            chosen_tables=sources[target],
            decision_rationale=(
                f"선택된 테이블이 모두 '{target}' 시스템 소속이므로 "
                f"해당 DB로 조회합니다."
            ),
        )

    # 복수 시스템 혼재 → 자동 선정하지 않고 명확화 요청
    return TargetDbDecision(
        status=TargetDbStatus.AMBIGUOUS,
        target="",
        chosen_tables=[],
        dropped_tables=[],
        decision_rationale=(
            f"복수 업무 시스템({', '.join(sorted(sources))})에 걸친 "
            f"질의는 지원하지 않습니다. 단일 시스템으로 범위를 좁혀 주세요."
        ),
    )
