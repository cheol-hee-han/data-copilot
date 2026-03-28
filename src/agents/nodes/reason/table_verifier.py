"""table_verifier 노드 — 선택된 테이블의 질의 충족성 검증 및 JOIN 분석.

context_explorer에서 후보 테이블이 확정된 후, evaluate 전에 실행된다.
선택된 테이블들의 컬럼 정보를 LLM에게 제시하여 다음을 판단한다:
  1. 질의에 필요한 정보가 모두 충족되는가
  2. 부족한 정보가 있는가 (→ 추가 탐색 또는 명확화)
  3. JOIN이 필요한가 (→ confirmed_join_path 설정)

핵심 함수:
    - table_verifier_node: explore 결과를 검증하고
      confirmed_join_path와 missing knowledge를 설정
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.agents.nodes.system_prompts import (
    REASON_TABLE_VERIFIER,
    REASON_TABLE_VERIFIER_USER,
)
from src.agents.state.state import (
    ColumnMapping,
    KnowledgeItem,
    PipelineState,
    TableResolution,
)
from src.config import settings
from src.utils.llm.client import get_llm_client
from src.utils.logger import get_logger
from src.utils.tracker import (
    get_current_tracker,
    record_prompt_variables,
)

logger = get_logger(__name__)


async def table_verifier_node(
    state: PipelineState,
) -> dict:
    """선택된 테이블들로 질의를 충족할 수 있는지 검증한다.

    candidate_tables가 비어있거나 1회 이상 검증 완료된 경우
    스킵하여 불필요한 LLM 호출을 방지한다.
    """
    reason = state.reason.model_copy(deep=True)
    candidates = reason.candidate_tables

    # 검증 스킵 조건
    if not candidates:
        logger.info("table_verifier 스킵: 후보 테이블 없음")
        return {"reason": reason}

    if reason.table_resolution is not None:
        # 이전 검증 결과가 있고, 테이블이 변경되지 않았으면 스킵
        prev_tables = set(
            m.table for m in reason.table_resolution.column_mapping
        )
        curr_tables = set(t.table_name for t in candidates)
        if prev_tables == curr_tables:
            logger.info(
                "table_verifier 스킵: 테이블 변경 없음",
            )
            return {"reason": reason}

    # ── 프롬프트 조립 ──
    nq = state.normalized_query
    measures = _extract_slot(nq, "measures")
    dimensions = _extract_slot(nq, "dimensions")
    filters = _extract_slot(nq, "filters")
    table_info = _build_table_info(candidates)

    prompt_vars = {
        "original_query": state.preprocessed_input or "",
        "measures": measures,
        "dimensions": dimensions,
        "filters": filters,
        "table_info": table_info,
    }

    prompt = REASON_TABLE_VERIFIER
    for vk, vv in prompt_vars.items():
        prompt = prompt.replace(f"{{{vk}}}", vv)

    # ── LLM 호출 ──
    client = get_llm_client()
    try:
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=1024,
            timeout=settings.llm_default_timeout,
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": REASON_TABLE_VERIFIER_USER,
                },
            ],
        )
        record_prompt_variables(prompt_vars)
        raw = response.content[0].text

        resolution = _parse_resolution(raw)
    except Exception as e:
        logger.warning(
            "table_verifier LLM 실패, 스킵",
            error=str(e),
        )
        return {"reason": reason}

    # ── 결과 반영 ──
    reason.table_resolution = resolution

    # JOIN 경로 확정
    if resolution.join_needed and resolution.join_path:
        reason.confirmed_join_path = [
            {"path": resolution.join_path},
        ]
        logger.info(
            "조인 경로 확정",
            join_path=resolution.join_path,
        )

    # 부족한 정보 → knowledge_items에 UNRESOLVED 추가
    for info in resolution.missing_info:
        reason.knowledge_items.append(
            KnowledgeItem(
                key=f"missing:{info[:50]}",
                value=info,
                status="UNRESOLVED",
                is_critical=True,
                source="table_verifier",
                evidence=[
                    f"테이블 검증에서 부족한 정보: {info}",
                ],
            ),
        )

    # ── 추적 ──
    tracker = get_current_tracker()
    if tracker and tracker.enabled:
        tracker.track_decision(
            node="reason_verify_tables",
            decision_type="table_resolution",
            chosen=(
                f"can_resolve={resolution.can_resolve}, "
                f"join={resolution.join_needed}"
            ),
            confidence=(
                1.0 if resolution.can_resolve else 0.0
            ),
            reason=resolution.reasoning[:200],
        )

    logger.info(
        "테이블 검증 완료",
        can_resolve=resolution.can_resolve,
        join_needed=resolution.join_needed,
        missing=len(resolution.missing_info),
    )

    return {"reason": reason}


def _extract_slot(nq: Any, slot_name: str) -> str:
    """NormalizedQuery에서 슬롯을 JSON 문자열로 추출."""
    if nq is None:
        return "[]"
    slot = getattr(nq, slot_name, None)
    if slot is None:
        return "[]"
    if isinstance(slot, list):
        items = []
        for item in slot:
            if hasattr(item, "model_dump"):
                items.append(item.model_dump())
            elif isinstance(item, dict):
                items.append(item)
            else:
                items.append(str(item))
        return json.dumps(
            items, ensure_ascii=False, indent=2,
        )
    return str(slot)


def _build_table_info(
    candidates: list[Any],
) -> str:
    """후보 테이블들의 정보를 프롬프트용 텍스트로 조립."""
    blocks = []
    for ct in candidates:
        name = ct.table_name
        desc = ct.description or "(설명 없음)"
        cols = ct.key_columns if hasattr(ct, "key_columns") else []

        lines = [f"  {name} ({desc})"]
        if cols:
            col_str = ", ".join(
                f"{c}" for c in cols[:30]
            )
            lines.append(f"    컬럼: {col_str}")

        # 샘플 데이터
        samples = (
            ct.sample_rows
            if hasattr(ct, "sample_rows")
            else []
        )
        if samples:
            sample = samples[0] if samples else {}
            sample_str = ", ".join(
                f"{k}={v}"
                for k, v in list(sample.items())[:8]
            )
            lines.append(f"    샘플: {sample_str}")

        # LLM 추론 필드
        if hasattr(ct, "inferred_entity_scope") and ct.inferred_entity_scope:
            lines.append(
                f"    엔티티 범위: {ct.inferred_entity_scope} (LLM 추론)",
            )

        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _parse_resolution(raw: str) -> TableResolution:
    """LLM 응답을 TableResolution으로 파싱."""
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        return TableResolution()

    try:
        data = json.loads(json_match.group())
    except json.JSONDecodeError:
        return TableResolution()

    mappings = []
    for m in data.get("column_mapping", []):
        mappings.append(
            ColumnMapping(
                need=m.get("need", ""),
                table=m.get("table", ""),
                column=m.get("column", ""),
                confidence=m.get("confidence", "추정"),
            ),
        )

    return TableResolution(
        can_resolve=data.get("can_resolve", False),
        column_mapping=mappings,
        missing_info=data.get("missing_info", []),
        join_needed=data.get("join_needed", False),
        join_path=data.get("join_path") or "",
        main_table=data.get("main_table", ""),
        reasoning=data.get("reasoning", ""),
    )
