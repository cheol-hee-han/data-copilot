"""자연어 질의를 8-Slot NormalizedQuery 구조로 변환하는 정규화 서비스.

비정형 자연어를 intent, entities, measures, dimensions, filters, time,
modifiers, output_hint의 8개 슬롯으로 구조화하여, 후속 SQL 생성 단계에서
LLM이 정확한 쿼리를 작성할 수 있도록 정형 입력을 제공한다.
5단계 파이프라인으로 동작한다:
  1. 전처리 — 약어 확장(ABBREVIATION_MAP), 구어체 기호 정리
  2. Phase 1 LLM — 동의어 사전을 주입하여 8-Slot JSON 분해
  3. 구조 검증 — 모든 Enum 필드를 검증하고 대소문자 자동 보정, 실패 시 기본값 적용
  4. Phase 2 LLM — 교차 검증 규칙 R1~R12 적용 (설정으로 비활성화 가능)
  5. 후처리 — 집계함수 자동 추론, RANK by 필드 보정, 검색 키워드 불용어 제거,
     sql_history 벡터 검색용 쿼리 합성

프롬프트(Phase 1/2 시스템 및 유저 템플릿)는 호출하는 노드에서 인자로 주입받아,
프롬프트 변경이 서비스 코드 수정 없이 가능하도록 설계되었다.

핵심 함수:
    - run_normalization: 5단계 파이프라인 전체를 오케스트레이션하는 메인 진입점
    - _preprocess_for_normalization: 약어 확장 + 구어체 기호 정리
    - _parse_llm_json: 코드 펜스 포함 LLM 응답에서 JSON 추출
    - _validate_structure: 8개 슬롯의 Enum 값 검증 및 자동 보정
    - _postprocess: 정합성 보정(집계함수/RANK) + 검색 키워드 최적화 + sql_history 쿼리 합성

설계 결정: Phase 2는 소형 로컬 모델에서 비용 대비 품질이 낮을 수 있어
settings.normalization_phase2_enabled로 비활성화할 수 있다.
"""

from __future__ import annotations

import json
import re
from src.utils.timezone import today_kst

from src.config import settings
from src.services.domain.domain_synonyms import (
    ABBREVIATION_MAP,
    get_output_template_prompt_text,
    get_synonym_prompt_text,
)
from src.agents.models.normalization import (
    VALID_AGG_FUNCS,
    VALID_CONFIDENCE,
    VALID_DIM_GRAN,
    VALID_DIM_ROLES,
    VALID_ENTITY_TYPES,
    VALID_FILTER_POS,
    VALID_FILTER_TYPES,
    VALID_INTENTS,
    VALID_MEASURE_TYPES,
    VALID_MOD_TYPES,
    VALID_OUTPUT_FMT,
    VALID_SORT_DIR,
    VALID_TIME_GRAN,
    VALID_TIME_RESOLVE,
    VALID_TIME_TYPES,
    NormalizedQuery,
)
from src.utils.llm import get_llm_client
from src.utils.logger import get_logger
from src.utils.tracker import record_prompt_variables

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# 전처리기
# ──────────────────────────────────────────────────────────────

def _preprocess_for_normalization(text: str) -> str:
    """정규화 전 텍스트를 전처리한다.

    약어 확장과 구어체 정리만 수행한다.
    """
    text = re.sub(r"[~～]+", "~", text)

    for abbr, full in ABBREVIATION_MAP.items():
        text = re.sub(
            rf"\b{re.escape(abbr)}\b",
            full,
            text,
            flags=re.IGNORECASE,
        )

    return text


# ──────────────────────────────────────────────────────────────
# JSON 파서
# ──────────────────────────────────────────────────────────────

def _parse_llm_json(raw_text: str) -> dict:
    """LLM 응답에서 JSON을 추출하고 파싱한다."""
    # 코드 펜스가 있으면 내부만 추출
    if "```" in raw_text:
        parts = re.split(r"```(?:json)?\s*", raw_text)
        if len(parts) >= 2:
            # 코드 펜스 내부에서 닫는 ``` 이전까지
            inner = parts[1].split("```")[0].strip()
            try:
                return json.loads(inner)
            except json.JSONDecodeError:
                pass  # 아래 폴백으로 진행

    # 코드 펜스 없거나 내부 추출 실패 시 전체에서 시도
    cleaned = re.sub(r"```(?:json)?\s*", "", raw_text)
    cleaned = cleaned.replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("정규화 JSON 파싱 실패", error=str(e))
        raise ValueError(
            f"LLM이 유효한 JSON을 반환하지 않았습니다: {e}"
        ) from e


# ──────────────────────────────────────────────────────────────
# 구조 검증기
# ──────────────────────────────────────────────────────────────

def _validate_enum(
    value: str, valid_set: set[str], field_name: str,
) -> str | None:
    """Enum 값을 검증하고 대소문자 보정을 시도한다."""
    if value in valid_set:
        return value
    upper = value.upper()
    if upper in valid_set:
        logger.warning(
            "Enum 자동보정",
            field=field_name,
            original=value,
            corrected=upper,
        )
        return upper
    logger.error(
        "Enum 검증 실패", field=field_name, value=value,
    )
    return None


def _validate_structure(
    data: dict,
) -> tuple[dict, list[str]]:
    """LLM 출력 JSON의 Enum 필드를 검증하고 자동 보정한다."""
    errors: list[str] = []

    _validate_intent(data, errors)
    _validate_entities(data)
    _validate_measures(data)
    _validate_dimensions(data)
    _validate_filters(data)
    _validate_time(data)
    _validate_modifiers(data, errors)
    _validate_output_hint(data)

    return data, errors


def _validate_intent(data: dict, errors: list[str]) -> None:
    """intent 슬롯 검증."""
    intent = data.get("intent", {})
    corrected = _validate_enum(
        intent.get("primary", ""),
        VALID_INTENTS,
        "intent.primary",
    )
    if corrected:
        intent["primary"] = corrected
    else:
        intent["primary"] = "EXTRACT"
        errors.append("intent.primary 보정 → EXTRACT")
    secondaries = []
    for s in intent.get("secondary", []):
        v = _validate_enum(
            s, VALID_INTENTS, "intent.secondary",
        )
        if v:
            secondaries.append(v)
    intent["secondary"] = secondaries
    # LLM이 intent 키 자체를 누락한 경우 data에 재삽입
    data["intent"] = intent


def _validate_entities(data: dict) -> None:
    """entities 슬롯 검증."""
    for i, e in enumerate(data.get("entities", [])):
        if not _validate_enum(
            e.get("type", ""),
            VALID_ENTITY_TYPES,
            f"entities[{i}].type",
        ):
            e["type"] = "DIRECT"
        # confidence 필드는 더 이상 사용하지 않음 (제거 허용)
        e.pop("confidence", None)


def _validate_measures(data: dict) -> None:
    """measures 슬롯 검증."""
    for i, m in enumerate(data.get("measures", [])):
        if not _validate_enum(
            m.get("agg_function", ""),
            VALID_AGG_FUNCS,
            f"measures[{i}].agg",
        ):
            m["agg_function"] = "UNKNOWN"
        if not _validate_enum(
            m.get("measure_type", ""),
            VALID_MEASURE_TYPES,
            f"measures[{i}].type",
        ):
            m["measure_type"] = "RAW"


def _validate_dimensions(data: dict) -> None:
    """dimensions 슬롯 검증."""
    all_gran = VALID_TIME_GRAN | VALID_DIM_GRAN
    for i, d in enumerate(data.get("dimensions", [])):
        if not _validate_enum(
            d.get("role", ""),
            VALID_DIM_ROLES,
            f"dim[{i}].role",
        ):
            d["role"] = "GROUP"
        gran = d.get("granularity", "")
        if gran not in all_gran:
            d["granularity"] = "UNKNOWN"


def _validate_filters(data: dict) -> None:
    """filters 슬롯 검증."""
    for i, f in enumerate(data.get("filters", [])):
        if not _validate_enum(
            f.get("filter_type", ""),
            VALID_FILTER_TYPES,
            f"filter[{i}].type",
        ):
            f["filter_type"] = "EQUALS"
        if not _validate_enum(
            f.get("position", ""),
            VALID_FILTER_POS,
            f"filter[{i}].pos",
        ):
            f["position"] = "PRE_AGG"


def _validate_time(data: dict) -> None:
    """time 슬롯 검증."""
    time_slot = data.get("time", {})
    if not _validate_enum(
        time_slot.get("type", ""),
        VALID_TIME_TYPES,
        "time.type",
    ):
        time_slot["type"] = "NONE"
    for pk in ("base_period", "compare_period"):
        period = time_slot.get(pk)
        if period and period.get("resolve"):
            if not _validate_enum(
                period["resolve"],
                VALID_TIME_RESOLVE,
                f"time.{pk}.resolve",
            ):
                period["resolve"] = "ABSOLUTE_RANGE"
    # LLM이 time 키 자체를 누락한 경우 data에 재삽입
    data["time"] = time_slot


def _validate_modifiers(
    data: dict, errors: list[str],
) -> None:
    """modifiers 슬롯 검증."""
    for i, mod in enumerate(data.get("modifiers", [])):
        if not _validate_enum(
            mod.get("type", ""),
            VALID_MOD_TYPES,
            f"mod[{i}].type",
        ):
            mod["_remove"] = True
            errors.append(f"modifiers[{i}] 제거됨")
        if mod.get("direction"):
            if not _validate_enum(
                mod["direction"],
                VALID_SORT_DIR,
                f"mod[{i}].dir",
            ):
                mod["direction"] = "DESC"
    data["modifiers"] = [
        m for m in data.get("modifiers", [])
        if not m.get("_remove")
    ]


def _validate_output_hint(data: dict) -> None:
    """output_hint 슬롯 검증."""
    oh = data.get("output_hint", {})
    if oh:
        if not _validate_enum(
            oh.get("format", ""),
            VALID_OUTPUT_FMT,
            "output_hint.format",
        ):
            oh["format"] = "NONE"
        # LLM이 output_hint 키를 누락한 경우 data에 재삽입
        data["output_hint"] = oh
    else:
        data["output_hint"] = {
            "format": "NONE",
            "doc_type": None,
            "expected_columns": [],
            # confidence 필드 제거됨
        }


# ──────────────────────────────────────────────────────────────
# 후처리기
# ──────────────────────────────────────────────────────────────

_POST_STOPWORDS = frozenset({
    "좀", "을", "를", "이", "가", "의", "에", "에서",
    "으로", "로", "한", "된", "하는", "있는",
    "해줘", "줘", "해주세요", "알려줘",
    "뽑아줘", "보여줘", "분석해줘", "조회해줘", "확인해줘",
    "부탁", "감사", "그리고", "또는", "및", "와", "과",
})


def _postprocess(data: dict) -> dict:
    """최종 정합성 보장 + 검색 키워드 최적화."""
    intent_set = {data["intent"]["primary"]} | set(
        data["intent"].get("secondary", [])
    )

    _post_aggregate_fix(data, intent_set)
    _post_rank_fix(data)
    _post_output_hint_merge(data)
    _post_optimize_keywords(data)
    _post_build_sql_history_search(data)

    return data


def _post_aggregate_fix(
    data: dict, intent_set: set[str],
) -> None:
    """AGGREGATE: measures에 집계함수 필수."""
    has_group = any(
        d.get("role") == "GROUP"
        for d in data.get("dimensions", [])
    )
    if not (has_group or "AGGREGATE" in intent_set):
        return
    for m in data.get("measures", []):
        if m.get("agg_function") == "NONE":
            m["agg_function"] = "SUM"
            data.setdefault("ambiguities", []).append(
                f"'{m.get('term')}'의 집계함수가 "
                "명시되지 않아 SUM으로 추정됨"
            )


def _post_rank_fix(data: dict) -> None:
    """RANK: by 필드 필수."""
    for mod in data.get("modifiers", []):
        if mod.get("type") == "RANK" and not mod.get("by"):
            if data.get("measures"):
                mod["by"] = data["measures"][0].get(
                    "term", "",
                )


def _post_output_hint_merge(data: dict) -> None:
    """output_hint.expected_columns를 meta_search에 병합."""
    oh = data.get("output_hint", {})
    if not oh.get("expected_columns"):
        return
    sk = data.get("search_keywords", {})
    existing = set(sk.get("meta_search", []))
    for col in oh["expected_columns"]:
        if col not in existing:
            sk.setdefault("meta_search", []).append(col)


def _post_optimize_keywords(data: dict) -> None:
    """검색 키워드 불용어 제거 + 중복 제거."""
    sk = data.get("search_keywords", {})
    if "meta_search" not in sk:
        return
    optimized: list[str] = []
    seen: set[str] = set()
    for kw in sk["meta_search"]:
        kw_clean = kw.strip()
        if (
            kw_clean
            and kw_clean not in _POST_STOPWORDS
            and kw_clean not in seen
        ):
            optimized.append(kw_clean)
            seen.add(kw_clean)
    sk["meta_search"] = optimized


# Intent → 비즈니스 동작어 매핑 (sql_history description 형식)
_INTENT_ACTION_MAP: dict[str, str] = {
    "EXTRACT": "조회",
    "AGGREGATE": "집계",
    "COMPARE": "비교",
    "TREND": "추이 분석",
    "RANK": "순위",
    "DISTRIBUTE": "분포",
    "EXIST_CHECK": "존재 여부 확인",
    "DEDUP": "중복 제거",
    "PIVOT": "교차 분석",
}


def _collect_slot_terms(
    items: list[dict[str, str]],
) -> list[str]:
    """엔티티/측정값 슬롯에서 대표 용어를 추출한다."""
    terms: list[str] = []
    for item in items:
        term = (
            item.get("normalized_term")
            or item.get("term", "")
        )
        if term:
            terms.append(term)
    return terms


def _collect_dimension_labels(
    dimensions: list[dict[str, str]],
) -> list[str]:
    """Dimension 슬롯에서 '~별' 분류축을 추출한다."""
    labels: list[str] = []
    for d in dimensions:
        term = d.get("term", "")
        if term:
            dim = term if term.endswith("별") else f"{term}별"
            labels.append(dim)
    return labels


def _post_build_sql_history_search(data: dict) -> None:
    """NormalizedQuery 슬롯에서 sql_history 벡터 검색 쿼리를 합성한다.

    sql_history의 description은 "부서별 대출건수 및 총잔액 집계"
    같은 비즈니스 목적 문장이다.
    NormalizedQuery의 구조화된 슬롯을 결합하여
    동일한 형식의 검색 쿼리를 만든다.
    """
    sk = data.setdefault("search_keywords", {})

    base = (
        data.get("rewritten_query", "")
        or data.get("original_query", "")
        or ""
    ).strip()

    parts: list[str] = [base] if base else []
    base_lower = base.lower()

    # Intent → 동작어
    primary = data.get("intent", {}).get("primary", "")
    action = _INTENT_ACTION_MAP.get(primary, "")
    if action and action not in base:
        parts.append(action)

    # Entity + Measure + Dimension 용어 수집
    candidates = (
        _collect_slot_terms(data.get("entities", []))
        + _collect_slot_terms(data.get("measures", []))
        + _collect_dimension_labels(
            data.get("dimensions", []),
        )
    )
    for term in candidates:
        if term.lower() not in base_lower:
            parts.append(term)

    sk["sql_history_search"] = " ".join(parts)


# ──────────────────────────────────────────────────────────────
# LLM 호출 헬퍼
# ──────────────────────────────────────────────────────────────

async def _call_llm(system: str, user: str) -> str:
    """LLM을 호출하고 텍스트 응답을 반환한다."""
    client = get_llm_client()
    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=settings.normalization_max_tokens,
        timeout=settings.llm_long_timeout,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if not response.content:
        raise ValueError("LLM 응답이 비어있습니다")
    return response.content[0].text


# ──────────────────────────────────────────────────────────────
# User Prompt 템플릿 (기본값)
# ──────────────────────────────────────────────────────────────

_PHASE1_USER_TEMPLATE = """\
다음 자연어 질의를 8-Slot 구조로 분해하여 JSON으로 출력해 주세요.

[입력 질의]
{query}

[오늘 날짜]
{today}

[동의어 사전]
{synonym_dict}

위 동의어 사전에 해당하는 용어가 질의에 있으면 \
normalized_term에 표준 용어를 기재하세요.
사전에 없는 용어는 normalized_term을 null로 두세요.

JSON만 출력하세요."""


_PHASE2_USER_TEMPLATE = """\
아래는 원본 질의와 Phase 1에서 생성된 정규화 JSON입니다.
교차 검증 규칙 R1~R12를 모두 적용하여 \
수정된 JSON을 출력해 주세요.

[원본 질의]
{query}

[Phase 1 결과 JSON]
{phase1_json}

JSON만 출력하세요."""


# ──────────────────────────────────────────────────────────────
# 메인 오케스트레이션 함수
# ──────────────────────────────────────────────────────────────

async def run_normalization(
    raw_query: str,
    *,
    phase1_system: str,
    phase2_system: str,
    phase1_user_template: str | None = None,
    phase2_user_template: str | None = None,
) -> NormalizedQuery:
    """자연어 질의를 8-Slot NormalizedQuery로 정규화한다.

    Args:
        raw_query: 사용자의 원본 자연어 질의.
        phase1_system: Phase 1 LLM 시스템 프롬프트.
        phase2_system: Phase 2 LLM 시스템 프롬프트.
        phase1_user_template: Phase 1 사용자 프롬프트 템플릿.
            None이면 기본 템플릿을 사용한다.
        phase2_user_template: Phase 2 사용자 프롬프트 템플릿.
            None이면 기본 템플릿을 사용한다.

    Returns:
        NormalizedQuery: 8-Slot 구조화된 정규화 결과.
    """
    p1_user_tpl = phase1_user_template or _PHASE1_USER_TEMPLATE
    p2_user_tpl = phase2_user_template or _PHASE2_USER_TEMPLATE

    cleaned = _preprocess_for_normalization(raw_query)

    # Phase 1 LLM
    today = today_kst().isoformat()
    synonym_text = get_synonym_prompt_text()
    template_text = get_output_template_prompt_text()

    p1_system = phase1_system.replace(
        "{output_template_text}", template_text,
    )
    phase1_user = p1_user_tpl.format(
        query=cleaned,
        today=today,
        synonym_dict=synonym_text,
    )

    logger.info("Phase 1 LLM 호출")
    phase1_raw = await _call_llm(p1_system, phase1_user)
    record_prompt_variables({
        "query": cleaned,
        "today": today,
        "synonym_dict": synonym_text[:200] + "..." if len(synonym_text) > 200 else synonym_text,
        "output_template_text": template_text[:200] + "..." if len(template_text) > 200 else template_text,
    })

    phase1_data = _parse_llm_json(phase1_raw)
    phase1_data, errors1 = _validate_structure(phase1_data)
    phase1_data["original_query"] = raw_query
    if errors1:
        logger.warning(
            "Phase 1 검증 오류",
            count=len(errors1),
            errors=errors1,
        )

    # Phase 2 LLM (설정에 따라 스킵)
    if settings.normalization_phase2_enabled:
        final_data = await _run_phase2(
            cleaned, phase1_data, raw_query,
            phase2_system=phase2_system,
            phase2_user_template=p2_user_tpl,
        )
    else:
        logger.info("Phase 2 스킵")
        final_data = phase1_data

    final_data = _postprocess(final_data)
    return NormalizedQuery.model_validate(final_data)


async def _run_phase2(
    cleaned: str,
    phase1_data: dict,
    raw_query: str,
    *,
    phase2_system: str,
    phase2_user_template: str,
) -> dict:
    """Phase 2 교차 검증 실행."""
    logger.info("Phase 2 LLM 호출 (교차 검증)")
    phase1_json_str = json.dumps(
        phase1_data, ensure_ascii=False, indent=2,
    )
    phase2_user = phase2_user_template.format(
        query=cleaned,
        phase1_json=phase1_json_str,
    )
    phase2_raw = await _call_llm(
        phase2_system, phase2_user,
    )
    record_prompt_variables({
        "query": cleaned,
        "phase1_json": phase1_json_str[:500] + "..." if len(phase1_json_str) > 500 else phase1_json_str,
    })
    final_data = _parse_llm_json(phase2_raw)
    final_data, errors2 = _validate_structure(final_data)
    final_data["original_query"] = raw_query
    if errors2:
        logger.warning(
            "Phase 2 검증 오류", count=len(errors2),
        )
    return final_data
