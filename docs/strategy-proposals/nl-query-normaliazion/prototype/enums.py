"""
=============================================================================
 자연어 질의 정규화 시스템 - Enum & Schema 정의
=============================================================================
 정규화 JSON에서 사용되는 모든 제약값(Constrained Values)을 정의합니다.
 이 값들은 후속 로직에서 분기/매칭 조건으로 사용되므로
 반드시 이 목록 내에서만 선택되어야 합니다.
=============================================================================
"""

from enum import Enum
from typing import Optional, List
from dataclasses import dataclass, field


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 0. QUERY CATEGORY (Intent Gate — 파이프라인 진입 전 1차 분류)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QueryCategory(str, Enum):
    """
    사용처: Intent Gate 출력 → 파이프라인 라우팅 분기 조건
    8-Slot 정규화는 DATA_QUERY일 때만 실행됩니다.
    
    DATA_QUERY    → 데이터 추출/분석 요청. 정규화 파이프라인으로 진행.
    CASUAL_TALK   → 인사, 감사, 잡담 등 데이터와 무관한 대화.
    META_QUESTION → 데이터/시스템 자체에 대한 질문. SQL이 아닌 메타 검색 필요.
    CLARIFICATION → 직전 질의에 대한 보충/수정 요청. 이전 컨텍스트 결합 필요.
    AMBIGUOUS     → 데이터 요청일 수도 있고 아닐 수도 있어 판단 불가.
    """
    DATA_QUERY    = "DATA_QUERY"
    CASUAL_TALK   = "CASUAL_TALK"
    META_QUESTION = "META_QUESTION"
    CLARIFICATION = "CLARIFICATION"
    AMBIGUOUS     = "AMBIGUOUS"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. INTENT TYPE (질의 유형 — SQL 뼈대 결정)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class IntentType(str, Enum):
    """사용처: intent.primary, intent.secondary[]"""
    EXTRACT     = "EXTRACT"
    AGGREGATE   = "AGGREGATE"
    COMPARE     = "COMPARE"
    TREND       = "TREND"
    RANK        = "RANK"
    DISTRIBUTE  = "DISTRIBUTE"
    EXIST_CHECK = "EXIST_CHECK"
    DEDUP       = "DEDUP"
    PIVOT       = "PIVOT"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. ENTITY 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class EntityType(str, Enum):
    """사용처: entities[].type"""
    DIRECT   = "DIRECT"
    INDIRECT = "INDIRECT"
    IMPLIED  = "IMPLIED"


class ConfidenceLevel(str, Enum):
    """사용처: 전체 슬롯의 confidence 필드"""
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. MEASURE 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class AggFunction(str, Enum):
    """사용처: measures[].agg_function"""
    SUM            = "SUM"
    AVG            = "AVG"
    COUNT          = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"
    MAX            = "MAX"
    MIN            = "MIN"
    NONE           = "NONE"
    UNKNOWN        = "UNKNOWN"


class MeasureType(str, Enum):
    """사용처: measures[].measure_type"""
    RAW     = "RAW"
    DERIVED = "DERIVED"
    RATIO   = "RATIO"
    WINDOW  = "WINDOW"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. DIMENSION 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DimensionRole(str, Enum):
    """사용처: dimensions[].role"""
    GROUP     = "GROUP"
    PARTITION = "PARTITION"
    FILTER    = "FILTER"
    DISPLAY   = "DISPLAY"


class TimeGranularity(str, Enum):
    """사용처: dimensions[].granularity (시간 차원)"""
    YEAR    = "YEAR"
    QUARTER = "QUARTER"
    MONTH   = "MONTH"
    WEEK    = "WEEK"
    DAY     = "DAY"
    HOUR    = "HOUR"
    UNKNOWN = "UNKNOWN"


class DimensionGranularity(str, Enum):
    """사용처: dimensions[].granularity (비시간 차원)"""
    INDIVIDUAL = "INDIVIDUAL"
    CATEGORY   = "CATEGORY"
    HIERARCHY  = "HIERARCHY"
    UNKNOWN    = "UNKNOWN"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. FILTER 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FilterType(str, Enum):
    """사용처: filters[].filter_type"""
    EQUALS      = "EQUALS"
    NOT_EQUALS  = "NOT_EQUALS"
    IN          = "IN"
    NOT_IN      = "NOT_IN"
    RANGE       = "RANGE"
    GT          = "GT"
    GTE         = "GTE"
    LT          = "LT"
    LTE         = "LTE"
    LIKE        = "LIKE"
    IS_NULL     = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"
    EXISTS      = "EXISTS"
    NOT_EXISTS  = "NOT_EXISTS"
    IMPLICIT    = "IMPLICIT"


class FilterPosition(str, Enum):
    """사용처: filters[].position"""
    PRE_AGG  = "PRE_AGG"
    POST_AGG = "POST_AGG"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 6. TIME 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TimeType(str, Enum):
    """사용처: time.type"""
    ABSOLUTE   = "ABSOLUTE"
    RELATIVE   = "RELATIVE"
    COMPARISON = "COMPARISON"
    CUMULATIVE = "CUMULATIVE"
    NONE       = "NONE"


class TimePeriodResolve(str, Enum):
    """사용처: time.*.resolve"""
    TODAY            = "TODAY"
    YESTERDAY        = "YESTERDAY"
    LAST_N_DAYS      = "LAST_N_DAYS"
    THIS_WEEK        = "THIS_WEEK"
    LAST_WEEK        = "LAST_WEEK"
    LAST_N_WEEKS     = "LAST_N_WEEKS"
    THIS_MONTH       = "THIS_MONTH"
    LAST_MONTH       = "LAST_MONTH"
    LAST_N_MONTHS    = "LAST_N_MONTHS"
    THIS_QUARTER     = "THIS_QUARTER"
    LAST_QUARTER     = "LAST_QUARTER"
    PREVIOUS_QUARTER = "PREVIOUS_QUARTER"
    LAST_N_QUARTERS  = "LAST_N_QUARTERS"
    THIS_HALF        = "THIS_HALF"
    LAST_HALF        = "LAST_HALF"
    THIS_YEAR        = "THIS_YEAR"
    LAST_YEAR        = "LAST_YEAR"
    LAST_N_YEARS     = "LAST_N_YEARS"
    YTD              = "YTD"
    MTD              = "MTD"
    QTD              = "QTD"
    ABSOLUTE_RANGE   = "ABSOLUTE_RANGE"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 7. MODIFIER 관련
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ModifierType(str, Enum):
    """사용처: modifiers[].type"""
    SORT       = "SORT"
    LIMIT      = "LIMIT"
    RANK       = "RANK"
    RATIO      = "RATIO"
    DELTA      = "DELTA"
    DELTA_RATE = "DELTA_RATE"
    CUMULATIVE = "CUMULATIVE"
    MOVING_AVG = "MOVING_AVG"
    PERCENTAGE = "PERCENTAGE"


class SortDirection(str, Enum):
    """사용처: modifiers[].direction"""
    ASC  = "ASC"
    DESC = "DESC"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 8. OUTPUT HINT 관련 (8번째 슬롯)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class OutputFormat(str, Enum):
    """
    사용처: output_hint.format
    
    SPEC_SHEET  → 명세서/명세표 ("명세", "내역서", "세부내역")
    SUMMARY     → 요약/현황 ("현황", "요약", "개요", "대시보드")
    DETAIL_LIST → 상세 목록 ("상세", "전체", "전건", "raw")
    REPORT      → 보고서 ("보고서", "리포트", "report")
    COMPARISON  → 비교표 ("비교표", "대조표")
    NONE        → 특별한 출력 형식 힌트 없음
    """
    SPEC_SHEET  = "SPEC_SHEET"
    SUMMARY     = "SUMMARY"
    DETAIL_LIST = "DETAIL_LIST"
    REPORT      = "REPORT"
    COMPARISON  = "COMPARISON"
    NONE        = "NONE"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 9. 출력 스키마 (dataclass)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class IntentSlot:
    primary: str
    secondary: List[str] = field(default_factory=list)

@dataclass
class EntitySlot:
    term: str
    type: str
    confidence: str
    normalized_term: Optional[str] = None
    note: Optional[str] = None

@dataclass
class MeasureSlot:
    term: str
    measure_type: str
    agg_function: str
    confidence: str
    normalized_term: Optional[str] = None
    note: Optional[str] = None

@dataclass
class DimensionSlot:
    term: str
    role: str
    granularity: str
    confidence: str
    normalized_term: Optional[str] = None
    is_time_dimension: bool = False
    note: Optional[str] = None

@dataclass
class FilterSlot:
    target: str
    filter_type: str
    position: str
    confidence: str
    values: Optional[List[str]] = None
    note: Optional[str] = None

@dataclass
class TimePeriod:
    label: str
    resolve: str
    n: Optional[int] = None
    absolute_start: Optional[str] = None
    absolute_end: Optional[str] = None

@dataclass
class TimeSlot:
    type: str
    base_period: Optional[TimePeriod] = None
    compare_period: Optional[TimePeriod] = None

@dataclass
class ModifierSlot:
    type: str
    direction: Optional[str] = None
    limit: Optional[int] = None
    by: Optional[str] = None
    note: Optional[str] = None

@dataclass
class OutputHintSlot:
    format: str
    doc_type: Optional[str] = None
    expected_columns: List[str] = field(default_factory=list)
    confidence: str = "MEDIUM"
    note: Optional[str] = None

@dataclass
class SearchKeywords:
    meta_search: List[str]
    vector_search: str

@dataclass
class NormalizedQuery:
    """정규화된 질의 최종 출력 스키마 (8-Slot)"""
    original_query: str
    rewritten_query: str
    intent: IntentSlot
    entities: List[EntitySlot]
    measures: List[MeasureSlot]
    dimensions: List[DimensionSlot]
    filters: List[FilterSlot]
    time: TimeSlot
    modifiers: List[ModifierSlot]
    output_hint: OutputHintSlot
    ambiguities: List[str]
    search_keywords: SearchKeywords
