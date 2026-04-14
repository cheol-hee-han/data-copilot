"""serialize_decomp_slots 현행 vs 가독성 개선안 비교 시뮬레이션.

reasoning_preparer._build_query_decomposition()이 만드는
실제 query_decomposition 구조 기준으로 테스트한다.
"""
from __future__ import annotations

import json
from typing import Any


# ── 현행: json.dumps ──
def serialize_current(decomp: dict[str, Any]) -> dict[str, str]:
    """현행 방식 — json.dumps 직렬화."""
    result = {
        f"{{{slot}}}": json.dumps(
            decomp.get(slot, []), ensure_ascii=False,
        )
        for slot in ("measures", "filters", "group_by", "order_limit")
    }
    result["{output_hint}"] = json.dumps(
        decomp.get("output_hint", {}), ensure_ascii=False,
    )
    return result


# ── 개선안: 사람/LLM 가독성 ──
def serialize_readable(decomp: dict[str, Any]) -> dict[str, str]:
    """개선안 — 사람/LLM 가독성 중심 직렬화."""
    result: dict[str, str] = {}

    # measures: [{term, agg_function}]
    measures = decomp.get("measures", [])
    if measures:
        lines = []
        for m in measures:
            term = m.get("term", "")
            agg = m.get("agg_function", "")
            note = m.get("note", "")
            line = f'"{term}"'
            if agg and agg not in ("NONE", "UNKNOWN"):
                line += f" (집계: {agg})"
            elif agg == "UNKNOWN":
                line += " (집계: 미확정)"
            if note:
                line += f" — {note}"
            lines.append(line)
        result["{measures}"] = "\n".join(f"  - {l}" for l in lines)
    else:
        result["{measures}"] = "(없음)"

    # filters: [{term, operator, value}]
    filters = decomp.get("filters", [])
    if filters:
        lines = []
        for f in filters:
            term = f.get("term", "")
            op = f.get("operator", "")
            val = f.get("value", [])
            val_str = ", ".join(str(v) for v in val) if isinstance(val, list) else str(val) if val else ""
            line = f'"{term}"'
            if op and val_str:
                line += f" {op} [{val_str}]"
            elif val_str:
                line += f" = {val_str}"
            lines.append(line)
        result["{filters}"] = "\n".join(f"  - {l}" for l in lines)
    else:
        result["{filters}"] = "(없음)"

    # group_by: [문자열] (dimensions에서 GROUP role만 추출된 term 리스트)
    groups = decomp.get("group_by", [])
    if groups:
        result["{group_by}"] = ", ".join(f'"{g}"' for g in groups)
    else:
        result["{group_by}"] = "(없음)"

    # order_limit: [{type, value}]
    order_limits = decomp.get("order_limit", [])
    if order_limits:
        lines = []
        for o in order_limits:
            otype = o.get("type", "")
            value = o.get("value", "")
            line = otype
            if value:
                line += f": {value}"
            lines.append(line)
        result["{order_limit}"] = ", ".join(lines)
    else:
        result["{order_limit}"] = "(없음)"

    # output_hint: {format, doc_type, expected_columns}
    hint = decomp.get("output_hint", {})
    if hint and hint.get("format"):
        parts = [hint["format"]]
        if hint.get("doc_type"):
            parts.append(f'문서유형="{hint["doc_type"]}"')
        cols = hint.get("expected_columns", [])
        if cols:
            parts.append(f'기대컬럼=[{", ".join(cols)}]')
        result["{output_hint}"] = ", ".join(parts)
    else:
        result["{output_hint}"] = "(없음)"

    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 테스트 케이스 (reasoning_preparer._build_query_decomposition 출력 형태)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
test_cases = [
    ("지점별 이번 달 신규 대출 건수", {
        "measures": [{"term": "건수", "agg_function": "COUNT"}],
        "filters": [
            {"term": "기간", "operator": "GTE", "value": ["이번 달"]},
            {"term": "신규", "operator": "IMPLICIT", "value": []},
        ],
        "group_by": ["지점"],
        "order_limit": [],
        "output_hint": {"format": "SUMMARY", "doc_type": None, "expected_columns": []},
    }),
    ("수신잔액 상위 10개 지점", {
        "measures": [{"term": "잔액", "agg_function": "SUM"}],
        "filters": [],
        "group_by": ["지점"],
        "order_limit": [{"type": "RANK", "value": "10"}],
        "output_hint": {"format": "SUMMARY", "doc_type": None, "expected_columns": []},
    }),
    ("영업점별 연체율 작년 동기 대비", {
        "measures": [{"term": "연체율", "agg_function": "NONE", "note": "연체대출잔액 합계 / 총대출잔액 합계"}],
        "filters": [],
        "group_by": ["영업점"],
        "order_limit": [{"type": "DELTA_RATE", "value": "연체율"}],
        "output_hint": {"format": "COMPARISON", "doc_type": None, "expected_columns": []},
    }),
    ("서울 VIP 고객 여신 명세", {
        "measures": [],
        "filters": [
            {"term": "지역", "operator": "EQUALS", "value": ["서울"]},
            {"term": "고객등급", "operator": "EQUALS", "value": ["VIP"]},
        ],
        "group_by": [],
        "order_limit": [],
        "output_hint": {"format": "SPEC_SHEET", "doc_type": "여신명세", "expected_columns": ["고객명", "계좌번호", "대출금액", "대출일자", "만기일자"]},
    }),
    ("이번 달 영업점별 여신 실행 건수 전월 대비 증감", {
        "measures": [{"term": "실행 건수", "agg_function": "COUNT"}],
        "filters": [],
        "group_by": ["영업점"],
        "order_limit": [{"type": "DELTA", "value": "실행 건수"}],
        "output_hint": {"format": "COMPARISON", "doc_type": None, "expected_columns": []},
    }),
]


if __name__ == "__main__":
    for label, decomp in test_cases:
        print(f'━━━ "{label}" ━━━')
        print()

        print("[현행 — json.dumps]")
        cur = serialize_current(decomp)
        for k, v in cur.items():
            print(f"- {k.strip('{}')}: {v}")
        print()

        print("[개선안 — 가독성]")
        new = serialize_readable(decomp)
        for k, v in new.items():
            print(f"- {k.strip('{}')}: {v}")
        print()
        print()
