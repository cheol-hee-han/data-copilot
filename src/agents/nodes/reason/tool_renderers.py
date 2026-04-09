"""도구별 결과 렌더러 — step.raw_result를 LLM 친화적 텍스트로 변환한다.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

context_interpreter가 LLM 배치 해석 프롬프트를 조립할 때 사용한다.
각 렌더러는 purpose → result → 판단 가이드(→) 3단 구조 블록을 생성한다.

핵심 함수:
    - serialize_tool_results_by_step: DONE 스텝 순회 → 블록 조립
    - _TOOL_RENDERERS: 도구명 → 렌더러 함수 맵

렌더링 원칙:
    - JSON 0% — 전부 자연어/구조화 텍스트
    - 정보 축소(truncate) 하지 않음 (토큰 문제는 Level 0/1 fallback으로 해결)
    - 결과 없음도 명시적으로 표현
"""

from __future__ import annotations

from typing import Any, Callable

from src.agents.state.state import ExecutionStep, StepStatus


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 직렬화 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def serialize_tool_results_by_step(
    execution_plan: list[ExecutionStep],
) -> str:
    """DONE 스텝을 순회하며 step.raw_result로부터 purpose + result 블록을 조립한다.

    도구별 전용 렌더러(_TOOL_RENDERERS)를 사용하여 JSON이 아닌
    자연어/구조화 텍스트로 변환한다. Level 0 배치 해석 프롬프트에 사용.
    """
    blocks: list[str] = []
    for step in execution_plan:
        if step.status != StepStatus.DONE or step.raw_result is None:
            continue
        renderer = _TOOL_RENDERERS.get(step.tool, _render_unknown)
        block = renderer(step)
        if block:
            blocks.append(block)
    return "\n\n".join(blocks) if blocks else "(도구 실행 결과 없음)"


def serialize_single_step(step: ExecutionStep) -> str:
    """단일 스텝의 raw_result를 렌더링한다.

    Level 1(스텝별 분할) 모드에서 개별 LLM 호출 프롬프트에 사용한다.
    """
    if step.status != StepStatus.DONE or step.raw_result is None:
        return ""
    renderer = _TOOL_RENDERERS.get(step.tool, _render_unknown)
    return renderer(step)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 도구별 렌더러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _render_use_cases(step: ExecutionStep) -> str:
    """search_use_cases 결과를 렌더링한다."""
    raw = step.raw_result
    if not isinstance(raw, dict):
        return ""
    use_cases = raw.get("use_cases", [])
    header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"


    if not use_cases:
        return f"{header}\n\n결과 없음 — 유사한 과거 SQL이 존재하지 않습니다."

    # per-use_case enrichment 테이블을 통합 인덱스로 구성
    table_meta: dict[str, dict] = {}
    for uc in use_cases:
        for t in uc.get("enrichment_tables", []):
            tname = t.get("table_name", "")
            if tname and tname not in table_meta:
                table_meta[tname] = t

    lines = [header, f"\n발견된 유사 SQL {len(use_cases)}건:"]
    rendered_tables: set[str] = set()

    for i, uc in enumerate(use_cases, 1):
        desc = uc.get("description", uc.get("query_desc", ""))
        score = uc.get("score", uc.get("similarity", 0))
        domain = uc.get("domain", "")
        sql = uc.get("sql", uc.get("query_sql", ""))

        sql_id = uc.get("_point_id", "")
        lines.append(f"\n{i}. \"{desc}\" (유사도: {score:.2f}, sql_id: {sql_id})")
        if domain:
            lines.append(f"   도메인: {domain}")
        if sql:
            lines.append(f"   SQL: {sql}")

        # 사용 테이블 표시 (per-use_case enrichment에서)
        uc_tables = [
            t.get("table_name", "") for t in uc.get("enrichment_tables", [])
        ]
        for tname in uc_tables:
            if not tname:
                continue
            if tname in rendered_tables:
                lines.append(f"   - 사용 테이블: {tname}")
                lines.append(f"     (위와 동일 — 중복 생략)")
                continue
            meta = table_meta.get(tname)
            if meta:
                alt = meta.get("alt_name", "")
                label = f"{tname} ({alt})" if alt else tname
                lines.append(f"   - 사용 테이블: {label}")
                # PK
                pk_cols = [c for c in meta.get("columns", []) if c.get("is_pk")]
                if pk_cols:
                    pk_str = ", ".join(
                        f"{c['name']}({c.get('alt_name', '')})" for c in pk_cols
                    )
                    lines.append(f"     PK: {pk_str}")
                # 컬럼
                all_cols = meta.get("columns", [])
                non_pk = [c for c in all_cols if not c.get("is_pk")]
                if non_pk:
                    col_strs = [
                        f"{c['name']}({c.get('alt_name', '')})"
                        for c in non_pk
                    ]
                    lines.append(f"     PK 외 컬럼: {', '.join(col_strs)}")
                rendered_tables.add(tname)
            else:
                lines.append(f"   - {tname}")

    lines.append(
        "\n→ 위 SQL에서 현재 질의에 재활용 가능한 테이블, 조인 구조, "
        "필터 조건을 판단하세요."
    )
    return "\n".join(lines)


_TABLE_TYPE = {
    "M": "마스터",
    "D": "상세",
    "L": "내역",
    "H": "이력",
    "G": "로그",
    "S": "집계",
    "P": "스냅샷",
    "C": "코드",
    "F": "인터페이스",
}


def _render_table_meta(step: ExecutionStep) -> str:
    """search_table_meta 결과를 렌더링한다."""
    raw = step.raw_result
    if not isinstance(raw, dict):
        return ""
    tables = raw.get("tables", [])
    header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"

    if not tables:
        return f"{header}\n\n결과 없음 — 관련 테이블을 찾지 못했습니다."

    lines = [header, f"\n발견된 테이블 {len(tables)}건:"]
    for i, t in enumerate(tables, 1):
        tname = t.get("table_name", "")
        alt = t.get("alt_name", "")
        desc = t.get("description", "")
        label = f"{tname} ({alt})" if alt else tname
        desc_part = f" — {desc}" if desc else ""
        lines.append(f"\n{i}. {label}{desc_part}")
        ttype = _TABLE_TYPE.get(tname[-1], "기타")
        lines.append(f"\n  테이블 유형: {tname[-1]}({ttype})")

        # 컬럼
        cols = t.get("columns", [])
        for c in cols:
            ctype = c.get("col_type", "")
            alt_c = c.get("alt_name", "")
            label_c = f"{c['name']}({alt_c})" if alt_c else c["name"]
            type_part = f" {ctype}" if ctype else ""
            pk_mark = " (PK)" if c.get("is_pk") else ""
            lines.append(f"   - {label_c}{type_part}{pk_mark}")

        # 샘플 행 (enrichment로 포함된 경우)
        sample = t.get("sample_rows")
        if sample and isinstance(sample, list) and sample:
            lines.append(f"\n   - 샘플 {len(sample)}행:")
            cols_keys = list(sample[0].keys()) if sample else []
            if cols_keys:
                lines.append(f"   {' | '.join(cols_keys)}")
                lines.append(f"   {'--- | ' * len(cols_keys)}")
                for row in sample[:5]:
                    vals = [str(row.get(k, "")) for k in cols_keys]
                    lines.append(f"   {' | '.join(vals)}")

    lines.append("\n→ 각 테이블이 질의에 적합한지 판단하세요.")
    return "\n".join(lines)


def _render_code_meta(step: ExecutionStep) -> str:
    """lookup_code_meta 결과를 렌더링한다."""
    result = step.raw_result
    if not isinstance(result, list) or not result:
        header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"
        return f"{header}\n\n결과 없음 — 해당 코드 메타를 찾지 못했습니다."

    lines = [f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"]
    for item in result:
        col = item.get("code_field", "")
        col_desc = item.get("code_field_desc", "")
        codes = item.get("codes", {})
        label = f"{col} ({col_desc})" if col_desc else col
        lines.append(f"\n{label} 코드값 {len(codes)}건:")
        for code_val, code_desc in codes.items():
            lines.append(f"  - {code_val}: {code_desc}")

    lines.append(
        "\n→ 질의 조건에 해당하는 코드값을 특정하세요. "
        "여러 값이 해당되면 모두 포함하세요."
    )
    return "\n".join(lines)


def _render_biz_terms(step: ExecutionStep) -> str:
    """search_biz_terms 결과를 렌더링한다."""
    result = step.raw_result
    if not isinstance(result, list) or not result:
        header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"
        return f"{header}\n\n결과 없음 — 관련 용어를 찾지 못했습니다."

    lines = [f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"]
    for item in result:
        name = item.get("name", "")
        definition = item.get("biz_term_definition", "")
        synonyms = item.get("synonyms", [])
        related = item.get("table_name", "")
        lines.append(f"\n- {name}: {definition}")
        if synonyms:
            lines.append(f"  동의어: {', '.join(synonyms)}")
        if related:
            lines.append(f"  관련 테이블: {related}")

    lines.append(
        "\n→ 이 정의가 SQL 변환(집계 방식, 필터 조건, 산출식)에 "
        "어떤 힌트를 주는지 판단하세요."
    )
    return "\n".join(lines)


def _render_biz_manuals(step: ExecutionStep) -> str:
    """search_manual 결과를 렌더링한다."""
    result = step.raw_result
    if not isinstance(result, list) or not result:
        header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"
        return f"{header}\n\n결과 없음 — 관련 매뉴얼을 찾지 못했습니다."

    lines = [f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"]
    for i, item in enumerate(result, 1):
        content = item.get("content", "")
        score = item.get("score", 0)
        lines.append(f"\n{i}. (유사도: {score:.2f})")
        lines.append(f"   {content}")

    lines.append(
        "\n→ 산출식, 업무 규칙, 데이터 기준이 SQL 로직에 영향을 주는지 판단하세요."
    )
    return "\n".join(lines)


def _render_sample_rows(step: ExecutionStep) -> str:
    """get_sample_rows 결과를 렌더링한다."""
    result = step.raw_result
    if not isinstance(result, list) or not result:
        header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"
        return f"{header}\n\n결과 없음 — 샘플 데이터가 없습니다."

    table_name = step.input.split(",")[0].strip()
    lines = [f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"]
    cols = list(result[0].keys()) if result else []
    lines.append(f"\n{table_name} 샘플 {len(result)}행:")
    if cols:
        lines.append(" | ".join(cols))
        lines.append(" | ".join("---" for _ in cols))
        for row in result:
            vals = [str(row.get(k, "")) for k in cols]
            lines.append(" | ".join(vals))

    lines.append(
        "\n→ 날짜 포맷, 코드값 패턴, NULL 여부 등 실제 데이터 특성을 확인하세요."
    )
    return "\n".join(lines)


def _render_date_distribution(step: ExecutionStep) -> str:
    """get_date_distribution 결과를 렌더링한다."""
    raw = step.raw_result
    if not raw:
        header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"
        return f"{header}\n\n결과 없음 — 날짜 분포 데이터가 없습니다."

    parts = [p.strip() for p in step.input.split(",")]
    table_name = parts[0] if parts else ""
    column_name = parts[1] if len(parts) > 1 else ""

    lines = [f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"]

    if isinstance(raw, dict):
        dates = raw.get("dates", [])
        recent = raw.get("recent_values", [])
    elif isinstance(raw, list):
        dates = raw
        recent = sorted(raw, reverse=True)[:10]
    else:
        dates = []
        recent = []

    if dates:
        sorted_dates = sorted(dates)
        date_range = f"{sorted_dates[0]} ~ {sorted_dates[-1]}"
        # 패턴 추정
        sample = str(sorted_dates[0])
        if len(sample) == 8:
            pattern = "YYYYMMDD"
        elif len(sample) == 6:
            pattern = "YYYYMM"
        elif len(sample) == 10 and "-" in sample:
            pattern = "YYYY-MM-DD"
        else:
            pattern = sample

        lines.append(f"\n{table_name}.{column_name}:")
        lines.append(f"  데이터 범위: {date_range}")
        lines.append(f"  날짜 패턴: {pattern}")
        if recent:
            lines.append(f"  최근 {len(recent)}건: {', '.join(str(d) for d in recent)}")

    lines.append(
        "\n→ 질의의 시간 조건이 이 범위에 포함되는지, "
        "적재 주기(일별/월별/영업일)와 날짜 포맷을 확인하세요."
    )
    return "\n".join(lines)


def _render_column_values(step: ExecutionStep) -> str:
    """get_column_values 결과를 렌더링한다."""
    result = step.raw_result
    if not isinstance(result, list) or not result:
        header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"
        return f"{header}\n\n결과 없음 — 검색 결과가 없습니다."

    parts = [p.strip() for p in step.input.split(",")]
    table_name = parts[0] if parts else ""
    column_name = parts[1] if len(parts) > 1 else ""
    search_term = parts[2] if len(parts) > 2 else ""

    lines = [f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"]
    search_desc = f" 에서 '{search_term}' 검색" if search_term else ""
    lines.append(f"\n{table_name}.{column_name}{search_desc} 결과 {len(result)}건:")
    for v in result:
        lines.append(f"  - {v}")

    lines.append("\n→ 질의 조건에 사용할 정확한 값을 특정하세요.")
    return "\n".join(lines)


def _render_column_profile(step: ExecutionStep) -> str:
    """get_column_profile 결과를 렌더링한다."""
    result = step.raw_result
    if not isinstance(result, dict) or not result:
        header = f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"
        return f"{header}\n\n결과 없음 — 컬럼 통계 데이터가 없습니다."

    parts = [p.strip() for p in step.input.split(",")]
    table_name = parts[0] if parts else ""
    column_name = parts[1] if len(parts) > 1 else ""

    lines = [f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n목적: {step.purpose}"]
    lines.append(f"\n{table_name}.{column_name} 컬럼 통계:")
    lines.append(f"  총 행수: {result.get('total_rows', 'N/A'):,}")
    lines.append(f"  NOT NULL: {result.get('non_null_count', 'N/A'):,}")

    null_rate = result.get("null_rate")
    if null_rate is not None:
        lines.append(f"  NULL율: {null_rate:.1%}")
    lines.append(f"  고유값 수: {result.get('distinct_count', 'N/A'):,}")
    min_v = result.get("min_val")
    max_v = result.get("max_val")
    if min_v is not None:
        lines.append(f"  MIN: {min_v}")
    if max_v is not None:
        lines.append(f"  MAX: {max_v}")

    lines.append(
        "\n→ NULL율이 높거나 고유값 수가 예상과 다르면 "
        "데이터 품질 이슈를 보고하세요."
    )
    return "\n".join(lines)


def _render_unknown(step: ExecutionStep) -> str:
    """미등록 도구에 대한 fallback 렌더러."""
    return (
        f"### [Step {step.step}] {step.tool}(\"{step.input}\")\n"
        f"목적: {step.purpose}\n\n"
        f"(렌더러 미등록 도구 — 원본 결과 참조 필요)"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 렌더러 맵
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_TOOL_RENDERERS: dict[str, Callable[[ExecutionStep], str]] = {
    "search_use_cases": _render_use_cases,
    "search_table_meta": _render_table_meta,
    "lookup_table_meta": _render_table_meta,
    "lookup_code_meta": _render_code_meta,
    "search_biz_terms": _render_biz_terms,
    "search_manual": _render_biz_manuals,
    "get_sample_rows": _render_sample_rows,
    "get_date_distribution": _render_date_distribution,
    "get_column_values": _render_column_values,
    "get_column_profile": _render_column_profile,
}
