"""자연어 질의 정규화 모델.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

8-Slot 구조화된 정규화 결과를 표현하는 Pydantic v2 모델.
LLM이 사용자의 자연어 질의를 분해한 결과를 검증 가능한 형태로 저장한다.

슬롯 구성:
  1. INTENT    — 질의 유형 (EXTRACT, AGGREGATE, COMPARE, ...)
  2. ENTITY    — 대상 엔티티 (테이블 후보)
  3. MEASURE   — 측정값/지표
  4. DIMENSION — 분류/그룹 축
  5. FILTER    — 조건
  6. TIME      — 시간 범위
  7. MODIFIER  — 결과 가공 지시자
  8. OUTPUT_HINT — 출력 형식 힌트
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from src.models.enums import ConfidenceLevel


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 0. QUERY CATEGORY (Intent Gate — 파이프라인 진입 전 1차 분류)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QueryCategory(str, Enum):
    """Intent Gate 출력 → 파이프라인 라우팅 분기 조건.

    8-Slot 정규화는 DATA_EXTRACTION 또는 DATA_ANALYSIS일 때 실행된다.
    하위 호환을 위해 DATA_QUERY도 유지한다 (DATA_EXTRACTION으로 처리).
    """

    DATA_EXTRACTION = "DATA_EXTRACTION"
    DATA_ANALYSIS = "DATA_ANALYSIS"
    DATA_QUERY = "DATA_QUERY"  # 하위 호환용 (DATA_EXTRACTION으로 처리)
    CASUAL_TALK = "CASUAL_TALK"
    META_QUESTION = "META_QUESTION"
    AMBIGUOUS = "AMBIGUOUS"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. INTENT TYPE (질의 유형 — SQL 뼈대 결정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NormIntentType(str, Enum):
    """정규화 질의 유형."""

    EXTRACT = "EXTRACT"
    AGGREGATE = "AGGREGATE"
    COMPARE = "COMPARE"
    TREND = "TREND"
    RANK = "RANK"
    DISTRIBUTE = "DISTRIBUTE"
    EXIST_CHECK = "EXIST_CHECK"
    DEDUP = "DEDUP"
    PIVOT = "PIVOT"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. ENTITY 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class EntityType(str, Enum):
    """엔티티 참조 유형."""

    DIRECT = "DIRECT"
    INDIRECT = "INDIRECT"
    IMPLIED = "IMPLIED"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. MEASURE 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AggFunction(str, Enum):
    """집계 함수 유형."""

    SUM = "SUM"
    AVG = "AVG"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    MAX = "MAX"
    MIN = "MIN"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class MeasureType(str, Enum):
    """측정값 유형."""

    RAW = "RAW"
    DERIVED = "DERIVED"
    RATIO = "RATIO"
    WINDOW = "WINDOW"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. DIMENSION 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DimensionRole(str, Enum):
    """차원 역할."""

    GROUP = "GROUP"
    PARTITION = "PARTITION"
    FILTER = "FILTER"
    DISPLAY = "DISPLAY"


class TimeGranularity(str, Enum):
    """시간 차원 세분화 수준."""

    YEAR = "YEAR"
    QUARTER = "QUARTER"
    MONTH = "MONTH"
    WEEK = "WEEK"
    DAY = "DAY"
    HOUR = "HOUR"
    UNKNOWN = "UNKNOWN"


class DimensionGranularity(str, Enum):
    """비시간 차원 세분화 수준."""

    INDIVIDUAL = "INDIVIDUAL"
    CATEGORY = "CATEGORY"
    HIERARCHY = "HIERARCHY"
    UNKNOWN = "UNKNOWN"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. FILTER 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FilterType(str, Enum):
    """필터 조건 유형."""

    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    IN = "IN"
    NOT_IN = "NOT_IN"
    RANGE = "RANGE"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"
    LIKE = "LIKE"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"
    IMPLICIT = "IMPLICIT"


class FilterPosition(str, Enum):
    """필터 적용 위치."""

    PRE_AGG = "PRE_AGG"
    POST_AGG = "POST_AGG"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. TIME 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TimeType(str, Enum):
    """시간 범위 유형."""

    ABSOLUTE = "ABSOLUTE"
    RELATIVE = "RELATIVE"
    COMPARISON = "COMPARISON"
    CUMULATIVE = "CUMULATIVE"
    NONE = "NONE"


class TimePeriodResolve(str, Enum):
    """시간 기간 해석 방식."""

    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    LAST_N_DAYS = "LAST_N_DAYS"
    THIS_WEEK = "THIS_WEEK"
    LAST_WEEK = "LAST_WEEK"
    LAST_N_WEEKS = "LAST_N_WEEKS"
    THIS_MONTH = "THIS_MONTH"
    LAST_MONTH = "LAST_MONTH"
    LAST_N_MONTHS = "LAST_N_MONTHS"
    THIS_QUARTER = "THIS_QUARTER"
    LAST_QUARTER = "LAST_QUARTER"
    PREVIOUS_QUARTER = "PREVIOUS_QUARTER"
    LAST_N_QUARTERS = "LAST_N_QUARTERS"
    THIS_HALF = "THIS_HALF"
    LAST_HALF = "LAST_HALF"
    THIS_YEAR = "THIS_YEAR"
    LAST_YEAR = "LAST_YEAR"
    LAST_N_YEARS = "LAST_N_YEARS"
    YTD = "YTD"
    MTD = "MTD"
    QTD = "QTD"
    ABSOLUTE_RANGE = "ABSOLUTE_RANGE"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. MODIFIER 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ModifierType(str, Enum):
    """결과 가공 유형."""

    SORT = "SORT"
    LIMIT = "LIMIT"
    RANK = "RANK"
    RATIO = "RATIO"
    DELTA = "DELTA"
    DELTA_RATE = "DELTA_RATE"
    CUMULATIVE = "CUMULATIVE"
    MOVING_AVG = "MOVING_AVG"
    PERCENTAGE = "PERCENTAGE"


class SortDirection(str, Enum):
    """정렬 방향."""

    ASC = "ASC"
    DESC = "DESC"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. OUTPUT HINT 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class OutputFormat(str, Enum):
    """출력 형식."""

    SPEC_SHEET = "SPEC_SHEET"
    SUMMARY = "SUMMARY"
    DETAIL_LIST = "DETAIL_LIST"
    REPORT = "REPORT"
    COMPARISON = "COMPARISON"
    NONE = "NONE"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. 슬롯 스키마 (Pydantic v2 BaseModel)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class IntentSlot(BaseModel):
    """질의 유형 슬롯."""

    primary: str
    secondary: list[str] = Field(default_factory=list)


class EntitySlot(BaseModel):
    """엔티티 슬롯."""

    term: str
    type: str = "DIRECT"
    confidence: str = "MEDIUM"
    normalized_term: Optional[str] = None
    note: Optional[str] = None


class MeasureSlot(BaseModel):
    """측정값 슬롯."""

    term: str
    measure_type: str = "RAW"
    agg_function: str = "UNKNOWN"
    confidence: str = "MEDIUM"
    normalized_term: Optional[str] = None
    note: Optional[str] = None


class DimensionSlot(BaseModel):
    """차원 슬롯."""

    term: str
    role: str = "GROUP"
    granularity: str = "UNKNOWN"
    confidence: str = "MEDIUM"
    normalized_term: Optional[str] = None
    is_time_dimension: bool = False
    note: Optional[str] = None


class FilterSlot(BaseModel):
    """필터 슬롯."""

    target: str = ""
    filter_type: str = "EQUALS"
    position: str = "PRE_AGG"
    confidence: str = "MEDIUM"
    values: Optional[list[str]] = None
    note: Optional[str] = None


class TimePeriod(BaseModel):
    """시간 기간."""

    label: str = ""
    resolve: str = ""
    n: Optional[int] = None
    absolute_start: Optional[str] = None
    absolute_end: Optional[str] = None


class TimeSlot(BaseModel):
    """시간 범위 슬롯."""

    type: str = "NONE"
    base_period: Optional[TimePeriod] = None
    compare_period: Optional[TimePeriod] = None


class ModifierSlot(BaseModel):
    """결과 가공 슬롯."""

    type: str
    direction: Optional[str] = None
    limit: Optional[int] = None
    by: Optional[str] = None
    note: Optional[str] = None


class OutputHintSlot(BaseModel):
    """출력 형식 힌트 슬롯."""

    format: str = "NONE"
    doc_type: Optional[str] = None
    expected_columns: list[str] = Field(default_factory=list)
    confidence: str = "MEDIUM"
    note: Optional[str] = None



class SearchKeywords(BaseModel):
    """정규화 결과에서 파생되는 검색 키워드 집합.

    meta_search: MongoDB 메타/이력 키워드 검색용.
    vector_search: biz_manual 벡터 검색용 (LLM 생성).
    sql_history_search: sql_history 벡터 검색용
        (NormalizedQuery 슬롯 기반 규칙 합성, query_normalizer 후처리에서 생성).
    """

    meta_search: list[str] = Field(default_factory=list)
    vector_search: str = ""
    sql_history_search: str = ""


class NormalizedQuery(BaseModel):
    """정규화된 질의 최종 출력 스키마 (8-Slot).

    LLM이 사용자 자연어를 intent·entity·measure 등 8개 슬롯으로 분해한 결과.
    reason 계층의 reasoning_preparer가 이 구조를 시드로 탐색 계획을 수립하고,
    sql_generator가 SQL 뼈대를 결정하는 데 활용한다.
    """

    original_query: str = ""
    rewritten_query: str = ""
    intent: IntentSlot = Field(default_factory=lambda: IntentSlot(primary="EXTRACT"))
    entities: list[EntitySlot] = Field(default_factory=list)
    measures: list[MeasureSlot] = Field(default_factory=list)
    dimensions: list[DimensionSlot] = Field(default_factory=list)
    filters: list[FilterSlot] = Field(default_factory=list)
    time: TimeSlot = Field(default_factory=TimeSlot)
    modifiers: list[ModifierSlot] = Field(default_factory=list)
    output_hint: OutputHintSlot = Field(default_factory=OutputHintSlot)
    ambiguities: list[dict] = Field(default_factory=list)
    search_keywords: SearchKeywords = Field(default_factory=SearchKeywords)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Enum 허용값 집합 (검증용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VALID_INTENTS: set[str] = {e.value for e in NormIntentType}
VALID_ENTITY_TYPES: set[str] = {e.value for e in EntityType}
VALID_CONFIDENCE: set[str] = {e.value for e in ConfidenceLevel}
VALID_AGG_FUNCS: set[str] = {e.value for e in AggFunction}
VALID_MEASURE_TYPES: set[str] = {e.value for e in MeasureType}
VALID_DIM_ROLES: set[str] = {e.value for e in DimensionRole}
VALID_TIME_GRAN: set[str] = {e.value for e in TimeGranularity}
VALID_DIM_GRAN: set[str] = {e.value for e in DimensionGranularity}
VALID_FILTER_TYPES: set[str] = {e.value for e in FilterType}
VALID_FILTER_POS: set[str] = {e.value for e in FilterPosition}
VALID_TIME_TYPES: set[str] = {e.value for e in TimeType}
VALID_TIME_RESOLVE: set[str] = {e.value for e in TimePeriodResolve}
VALID_MOD_TYPES: set[str] = {e.value for e in ModifierType}
VALID_SORT_DIR: set[str] = {e.value for e in SortDirection}
VALID_OUTPUT_FMT: set[str] = {e.value for e in OutputFormat}
VALID_QUERY_CATEGORIES: set[str] = {e.value for e in QueryCategory}
