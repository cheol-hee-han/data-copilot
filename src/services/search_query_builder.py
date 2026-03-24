"""검색 쿼리 빌더.

사용자의 전처리된 입력을 분석하여 각 데이터 소스(ES, PostgreSQL, Qdrant)에
최적화된 검색 쿼리를 생성한다.

핵심 전략:
    1. 도메인 용어 매칭 → 테이블명·컬럼명·카테고리 추출
    2. 한국어 불용어 제거 → 검색 노이즈 최소화
    3. 동의어/별칭 확장 → 검색 재현율(recall) 향상
    4. 소스별 쿼리 특화 → 각 소스의 검색 메커니즘에 최적화
       - ES table_meta: 테이블명 부스트 + 도메인 키워드
       - ES report_sql: 업무 목적 중심 자연어
       - PostgreSQL history: 핵심 키워드 OR 조합
       - Qdrant manual: 의미 보강된 자연어 (벡터 검색 최적화)

정규화 연동:
    NormalizedQuery 가 있으면 search_keywords 를 활용하여
    기존 도메인 사전 기반 전략을 보완한다.
    - meta_search → ES/History 키워드 보강
    - vector_search → Qdrant 쿼리 보강

TODO:
- SQL HISTORY 에서 조회된 Value 포맷이 seed_sql_history.py 에서 시딩된 데이터와 일관되게 처리되고 있는지 점검 필요

"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.services.domain.domain_dictionary import (
    DOMAIN_DICTIONARY,
    DomainTerm,
    lookup_terms,
)
from src.services.domain.similar_tables import (
    SIMILAR_TABLE_GROUPS,
    SimilarTable,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── 카테고리 → ES domain_cd 매핑 ──
# 실제 ES table_meta 인덱스의 domain_cd 필드값과 매핑.
# ES의 table_name이 keyword 타입이라 부분검색이 불가하므로,
# domain_cd를 쿼리에 포함하여 도메인 필터링 효과를 얻는다.
# resources/domain/domain_categories.yaml 이 있으면 해당 파일의 매핑을 사용한다.

_DEFAULT_CATEGORY_TO_DOMAIN_CD: dict[str, list[str]] = {
    "고객": ["CUS"],
    "여신": ["LON"],
    "수신": ["DEP"],
    "거래": ["TRX", "DEP"],
    "카드": ["CRD"],
    "외환": ["FEX"],
    "금융지표": ["MGT", "LON"],
    "조직": ["CUS"],  # 지점 정보는 CUS 도메인에 포함
}


def _load_domain_cd_mapping() -> dict[str, list[str]]:
    """resources/domain/domain_categories.yaml 에서 매핑을 로드한다."""
    from src.utils.resource_loader import load_yaml

    data = load_yaml("domain/domain_categories.yaml", None)
    if data is None:
        return _DEFAULT_CATEGORY_TO_DOMAIN_CD
    mapping = data.get("mapping", {})
    return mapping if mapping else _DEFAULT_CATEGORY_TO_DOMAIN_CD


_CATEGORY_TO_DOMAIN_CD: dict[str, list[str]] = _load_domain_cd_mapping()


# ── 한국어 불용어 (검색 노이즈를 유발하는 조사·어미·부사) ──
# resources/domain/stopwords.yaml 이 있으면 해당 파일의 불용어 목록을 사용한다.

_DEFAULT_STOPWORDS: frozenset[str] = frozenset({
    # 조사
    "이", "가", "을", "를", "의", "에", "에서", "로", "으로", "와", "과",
    "도", "는", "은", "만", "까지", "부터", "에게", "한테", "께",
    # 어미·접미
    "좀", "것", "수", "등", "해줘", "줘", "주세요", "알려줘",
    "보여줘", "뽑아줘", "조회해줘", "보여주세요", "알려주세요", "뽑아주세요",
    # 지시어
    "어떤", "무슨", "얼마나", "몇",
    # 보조 동사·부사
    "있는", "하는", "된", "한", "할", "하고", "해서",
    "그", "저", "다",
})


def _load_stopwords() -> frozenset[str]:
    """resources/domain/stopwords.yaml 에서 불용어를 로드한다."""
    from src.utils.resource_loader import load_yaml

    data = load_yaml("domain/stopwords.yaml", None)
    if data is None:
        return _DEFAULT_STOPWORDS

    words: set[str] = set()
    for section in ("particles", "suffixes", "determiners", "auxiliaries"):
        words.update(data.get(section, []))
    return frozenset(words) if words else _DEFAULT_STOPWORDS


_STOPWORDS: frozenset[str] = _load_stopwords()

# 조사 패턴 (단어 끝에 붙는 1~2글자 조사 제거용)
_PARTICLE_PATTERN = re.compile(
    r"(은|는|이|가|을|를|의|에|에서|로|으로|와|과|도|만|까지|부터)$"
)


@dataclass
class SourceQuery:
    """소스별 최적화된 검색 쿼리.

    각 필드는 해당 소스의 검색 메커니즘에 맞게 가공된 쿼리 문자열이다.
    """

    es_table_query: str  # ES 테이블 메타 검색용
    es_report_query: str  # ES 보고서 SQL 검색용
    history_db_query: str  # 이력 DB 과거 SQL 검색용
    qdrant_query: str  # Qdrant biz_manual 벡터 검색용
    sql_history_query: str = ""  # Qdrant sql_history 벡터 검색용

    matched_terms: list[DomainTerm] = field(default_factory=list)
    extracted_tables: list[str] = field(default_factory=list)  # 매칭된 테이블명
    extracted_columns: list[str] = field(default_factory=list)  # 매칭된 컬럼명
    categories: list[str] = field(default_factory=list)  # 관련 카테고리
    core_keywords: list[str] = field(default_factory=list)  # 핵심 키워드
    expanded_keywords: list[str] = field(default_factory=list)  # 동의어 확장 키워드


class SearchQueryBuilder:
    """도메인 지식 기반 검색 쿼리 전략 빌더.

    preprocessed_input 을 받아 4개 소스 각각에 최적화된
    검색 쿼리를 생성한다.

    NormalizedQuery 가 있으면 search_keywords 를 활용하여
    검색 정확도를 향상시킨다.
    """

    def build_from_normalized(
        self,
        preprocessed_input: str,
        normalized_query: object,
    ) -> SourceQuery:
        """NormalizedQuery 의 search_keywords 를 활용하여 검색 쿼리를 생성한다.

        기존 도메인 사전 기반 결과를 먼저 만든 뒤,
        정규화 결과의 키워드로 보강한다.
        sql_history 벡터 검색 쿼리도 NormalizedQuery 슬롯에서 합성한다.
        """
        base = self.build(preprocessed_input)

        if normalized_query is None:
            return base

        # NormalizedQuery.search_keywords 접근
        sk = getattr(normalized_query, "search_keywords", None)
        if sk is None:
            return base

        meta_kws: list[str] = getattr(sk, "meta_search", [])
        vector_kw: str = getattr(sk, "vector_search", "")

        # meta_search 키워드를 core_keywords 에 병합
        if meta_kws:
            existing = set(kw.lower() for kw in base.core_keywords)
            for kw in meta_kws:
                if kw.lower() not in existing and len(kw) >= 2:
                    base.core_keywords.append(kw)
                    existing.add(kw.lower())

            # ES table 쿼리 보강
            base.es_table_query = _enrich_query(
                base.es_table_query, meta_kws,
            )
            # History DB 쿼리 보강
            base.history_db_query = _enrich_query(
                base.history_db_query, meta_kws[:10],
            )

        # vector_search 를 Qdrant 쿼리에 보강
        if vector_kw:
            base.qdrant_query = f"{base.qdrant_query} {vector_kw}"
            # 보고서 검색에도 활용
            base.es_report_query = (
                f"{base.es_report_query} {vector_kw}"
            )

        # 엔티티/측정값 정보 보강
        entities = getattr(normalized_query, "entities", [])
        for ent in entities:
            term = getattr(ent, "term", "")
            n_term = getattr(ent, "normalized_term", None)
            if n_term and n_term not in base.extracted_columns:
                base.expanded_keywords.append(n_term)
            if term and term not in base.expanded_keywords:
                base.expanded_keywords.append(term)

        # ── sql_history 벡터 검색 쿼리 합성 ──
        # NormalizedQuery 슬롯에서 규칙 기반으로 합성하되,
        # 이미 생성된 sql_history_search 가 있으면 우선 사용
        sql_hist_search: str = getattr(
            sk, "sql_history_search", "",
        )
        if sql_hist_search:
            base.sql_history_query = sql_hist_search
        else:
            base.sql_history_query = (
                _build_sql_history_vector_query(
                    normalized_query, vector_kw,
                )
            )

        return base

    def build(self, preprocessed_input: str) -> SourceQuery:
        """소스별 최적화된 검색 쿼리를 생성한다.

        Args:
            preprocessed_input: 전처리된 사용자 입력.

        Returns:
            SourceQuery: 소스별 쿼리와 추출된 메타 정보.
        """
        # Step 1: 도메인 용어 매칭
        matched_terms = lookup_terms(preprocessed_input)

        # Step 2: 구조화된 엔티티 추출
        tables = _extract_tables(matched_terms)
        columns = _extract_columns(matched_terms)
        categories = _extract_categories(matched_terms)

        # Step 3: 핵심 키워드 추출 (불용어 제거)
        core_keywords = _extract_core_keywords(preprocessed_input)

        # Step 4: 동의어/별칭 확장
        expanded_keywords = _expand_with_aliases(
            core_keywords, matched_terms,
        )

        # Step 5: 유사 테이블 신호어 수집
        signal_keywords = _collect_signal_keywords(tables, categories)

        # Step 6: 소스별 쿼리 생성
        es_table_query = _build_es_table_query(
            core_keywords, tables, columns, signal_keywords, categories,
        )
        es_report_query = _build_es_report_query(
            preprocessed_input, core_keywords, categories,
        )
        history_db_query = _build_history_db_query(
            core_keywords, expanded_keywords, tables,
        )
        qdrant_query = _build_qdrant_query(
            preprocessed_input, matched_terms, categories,
        )

        source_query = SourceQuery(
            es_table_query=es_table_query,
            es_report_query=es_report_query,
            history_db_query=history_db_query,
            qdrant_query=qdrant_query,
            matched_terms=matched_terms,
            extracted_tables=tables,
            extracted_columns=columns,
            categories=categories,
            core_keywords=core_keywords,
            expanded_keywords=expanded_keywords,
        )

        logger.info(
            "검색 쿼리 전략 생성 완료",
            input=preprocessed_input[:60],
            matched_terms=len(matched_terms),
            tables=tables,
            categories=categories,
            core_keywords=core_keywords[:8],
        )

        return source_query


# ──────────────────────────────────────────────────────────────
# Step 1~2: 엔티티 추출
# ──────────────────────────────────────────────────────────────

def _extract_tables(terms: list[DomainTerm]) -> list[str]:
    """매칭된 도메인 용어에서 테이블명을 중복 없이 추출한다."""
    seen: set[str] = set()
    tables: list[str] = []
    for t in terms:
        if t.table_name and t.table_name not in seen:
            seen.add(t.table_name)
            tables.append(t.table_name)
    return tables


def _extract_columns(terms: list[DomainTerm]) -> list[str]:
    """매칭된 도메인 용어에서 컬럼명을 중복 없이 추출한다."""
    seen: set[str] = set()
    columns: list[str] = []
    for t in terms:
        if t.column_name and t.column_name not in seen:
            seen.add(t.column_name)
            columns.append(t.column_name)
    return columns


def _extract_categories(terms: list[DomainTerm]) -> list[str]:
    """매칭된 도메인 용어에서 카테고리를 중복 없이 추출한다."""
    seen: set[str] = set()
    categories: list[str] = []
    for t in terms:
        if t.category and t.category not in seen:
            seen.add(t.category)
            categories.append(t.category)
    return categories


# ──────────────────────────────────────────────────────────────
# Step 3: 핵심 키워드 추출
# ──────────────────────────────────────────────────────────────

def _extract_core_keywords(text: str) -> list[str]:
    """불용어·조사를 제거하고 핵심 키워드만 추출한다.

    처리 순서:
        1. 공백 기준 토큰화
        2. 1글자 토큰 제거
        3. 불용어 목록 필터
        4. 어미 조사 제거
        5. 빈 문자열·중복 제거
    """
    tokens = text.strip().split()
    keywords: list[str] = []
    seen: set[str] = set()

    for token in tokens:
        # 1글자는 대부분 조사이므로 제거
        if len(token) <= 1:
            continue

        # 불용어 목록 체크
        if token.lower() in _STOPWORDS:
            continue

        # 어미 조사 제거
        cleaned = _PARTICLE_PATTERN.sub("", token)
        if not cleaned or len(cleaned) <= 1:
            continue

        # 불용어 재체크 (조사 제거 후)
        if cleaned.lower() in _STOPWORDS:
            continue

        if cleaned not in seen:
            seen.add(cleaned)
            keywords.append(cleaned)

    return keywords


# ──────────────────────────────────────────────────────────────
# Step 4: 동의어/별칭 확장
# ──────────────────────────────────────────────────────────────

def _expand_with_aliases(
    core_keywords: list[str],
    matched_terms: list[DomainTerm],
) -> list[str]:
    """매칭된 도메인 용어의 동의어/별칭으로 키워드를 확장한다.

    예: "여신" 매칭 → "대출", "론", "대여금" 추가
    """
    expanded: list[str] = list(core_keywords)
    seen: set[str] = set(kw.lower() for kw in core_keywords)

    for term in matched_terms:
        # term 자체 추가
        term_clean = term.term.replace(" ", "")
        if term_clean.lower() not in seen:
            expanded.append(term_clean)
            seen.add(term_clean.lower())

        # aliases 추가
        for alias in term.aliases:
            alias_clean = alias.replace(" ", "")
            if alias_clean.lower() not in seen:
                expanded.append(alias_clean)
                seen.add(alias_clean.lower())

    return expanded


# ──────────────────────────────────────────────────────────────
# Step 5: 유사 테이블 신호어 수집
# ──────────────────────────────────────────────────────────────

def _collect_signal_keywords(
    tables: list[str],
    categories: list[str],
) -> list[str]:
    """유사 테이블 그룹에서 관련 신호어를 수집한다.

    매칭된 테이블이나 카테고리와 관련된 유사 테이블 그룹의
    signal_keywords 를 반환한다.
    """
    signal_kws: list[str] = []
    seen: set[str] = set()

    for group in SIMILAR_TABLE_GROUPS:
        # 매칭된 테이블이 이 그룹에 속하는지 확인
        group_relevant = any(t in group.tables for t in tables)
        # 또는 카테고리가 이 그룹의 도메인과 일치하는지
        if not group_relevant:
            group_relevant = group.domain in categories

        if group_relevant:
            for table_info in group.tables.values():
                for kw in table_info.signal_keywords:
                    if kw not in seen:
                        seen.add(kw)
                        signal_kws.append(kw)

    return signal_kws


# ──────────────────────────────────────────────────────────────
# Step 6: 소스별 쿼리 생성
# ──────────────────────────────────────────────────────────────

def _build_es_table_query(
    core_keywords: list[str],
    tables: list[str],
    columns: list[str],
    signal_keywords: list[str],
    categories: list[str] | None = None,
) -> str:
    """ES 테이블 메타 검색에 최적화된 쿼리를 생성한다.

    전략:
        1. domain_cd 주입 — ES table_meta의 table_name이 keyword 타입이라
           부분검색이 불가하므로, 카테고리에서 추론한 domain_cd를 쿼리에
           포함하여 도메인 필터링 효과를 얻는다.
        2. 테이블명 부스트 — 도메인 사전에서 추출한 테이블명 반복.
        3. 컬럼명·키워드·신호어 순서로 추가.
    """
    parts: list[str] = []

    # domain_cd 주입 (ES에서 가장 효과적인 필터)
    if categories:
        for cat in categories:
            for domain_cd in _CATEGORY_TO_DOMAIN_CD.get(cat, []):
                if domain_cd not in parts:
                    parts.append(domain_cd)

    # 테이블명을 2회 반복하여 부스트
    for table in tables:
        parts.append(table)
        parts.append(table)

    # 컬럼명 추가
    parts.extend(columns)

    # 핵심 키워드 (시간 관련 제외 — 테이블 검색에 불필요)
    time_keywords = {"이번", "지난", "올해", "전월", "당월", "금월", "분기"}
    for kw in core_keywords:
        if kw not in time_keywords:
            parts.append(kw)

    # 신호어 추가 (상위 5개만)
    parts.extend(signal_keywords[:5])

    return " ".join(parts) if parts else " ".join(core_keywords)


def _build_es_report_query(
    original_input: str,
    core_keywords: list[str],
    categories: list[str],
) -> str:
    """ES 보고서 SQL 검색에 최적화된 쿼리를 생성한다.

    전략:
        보고서는 업무 목적(~현황, ~분석, ~추이)으로 검색되므로
        원본 입력의 의미를 유지하면서 카테고리를 보강한다.
    """
    # 시간 표현 제거 (보고서 검색에 불필요)
    time_patterns = re.compile(
        r"(이번\s*달|지난\s*달|올해|전월|당월|금월|이달|작달"
        r"|지난\s*분기|이번\s*분기|전년|작년)",
    )
    cleaned = time_patterns.sub("", original_input).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    # 카테고리명 추가
    parts = [cleaned]
    for cat in categories:
        if cat not in cleaned:
            parts.append(cat)

    return " ".join(parts)


def _build_history_db_query(
    core_keywords: list[str],
    expanded_keywords: list[str],
    tables: list[str],
) -> str:
    """이력 DB 과거 SQL 검색에 최적화된 쿼리를 생성한다.

    전략:
        ILIKE 기반이므로 핵심 키워드 + 동의어 확장 키워드를 조합.
        테이블명도 포함하여 동일 테이블 사용 이력을 찾도록 한다.
        단, 너무 많은 키워드는 OR 조건 폭발을 유발하므로 상위 N개로 제한.
    """
    # 핵심 키워드 우선, 확장 키워드 보조, 테이블명 추가
    all_keywords: list[str] = []
    seen: set[str] = set()

    # 핵심 키워드 (최우선)
    for kw in core_keywords:
        if kw.lower() not in seen and len(kw) >= 2:
            seen.add(kw.lower())
            all_keywords.append(kw)

    # 테이블명
    for table in tables:
        if table.lower() not in seen:
            seen.add(table.lower())
            all_keywords.append(table)

    # 확장 키워드 (보조, 상위 5개만)
    expansion_count = 0
    for kw in expanded_keywords:
        if kw.lower() not in seen and len(kw) >= 2:
            seen.add(kw.lower())
            all_keywords.append(kw)
            expansion_count += 1
            if expansion_count >= 5:
                break

    # ILIKE 쿼리용으로 공백 구분 문자열 반환
    return " ".join(all_keywords[:15])


def _build_qdrant_query(
    original_input: str,
    matched_terms: list[DomainTerm],
    categories: list[str],
) -> str:
    """Qdrant 벡터 검색에 최적화된 쿼리를 생성한다.

    전략:
        벡터 검색은 의미(semantic) 기반이므로 자연어 문장 형태를 유지하되,
        도메인 용어의 정식 명칭과 설명을 보강하여
        임베딩 공간에서 업무 매뉴얼과 더 가까운 벡터를 만든다.
    """
    parts = [original_input]

    # 매칭된 도메인 용어의 정식 명칭 추가
    term_names = []
    for t in matched_terms:
        if t.category != "시간":  # 시간 표현은 매뉴얼 검색에 불필요
            term_names.append(t.term)
            if t.description:
                # 설명 중 핵심부만 추가 (80자 이내)
                desc_short = t.description[:80]
                term_names.append(desc_short)

    if term_names:
        parts.append(" ".join(term_names[:6]))

    # 카테고리 추가 (매뉴얼의 카테고리 필드와 매칭)
    for cat in categories:
        if cat != "시간":
            parts.append(cat)

    return " ".join(parts)


# ──────────────────────────────────────────────────────────────
# Step 7: sql_history 벡터 검색 쿼리 합성
# ──────────────────────────────────────────────────────────────

# Intent → 비즈니스 동작어 매핑
_INTENT_TO_ACTION: dict[str, str] = {
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


def _extract_base_query(nq: object) -> str:
    """NormalizedQuery에서 기본 쿼리 텍스트를 추출한다."""
    base = getattr(nq, "rewritten_query", "") or ""
    if not base:
        base = getattr(nq, "original_query", "") or ""
    return base.strip()


def _extract_intent_action(nq: object) -> str:
    """Intent 슬롯에서 비즈니스 동작어를 추출한다."""
    intent_slot = getattr(nq, "intent", None)
    if not intent_slot:
        return ""
    primary = getattr(intent_slot, "primary", "")
    return _INTENT_TO_ACTION.get(primary, "")


def _extract_slot_terms(
    items: list[object],
) -> list[str]:
    """엔티티/측정값 슬롯에서 대표 용어를 추출한다.

    normalized_term 우선, 없으면 term 사용.
    """
    terms: list[str] = []
    for item in items:
        n_term = getattr(item, "normalized_term", None)
        term = getattr(item, "term", "")
        candidate = n_term or term
        if candidate:
            terms.append(candidate)
    return terms


def _extract_dimension_terms(
    dimensions: list[object],
) -> list[str]:
    """Dimension 슬롯에서 '~별' 분류축을 추출한다."""
    terms: list[str] = []
    for d in dimensions:
        term = getattr(d, "term", "")
        if term:
            dim = term if term.endswith("별") else f"{term}별"
            terms.append(dim)
    return terms


def _append_novel_terms(
    parts: list[str],
    candidates: list[str],
    base_lower: str,
) -> None:
    """base_lower에 없는 후보만 parts에 추가한다."""
    for candidate in candidates:
        if candidate.lower() not in base_lower:
            parts.append(candidate)


def _build_sql_history_vector_query(
    normalized_query: object,
    vector_search_fallback: str = "",
) -> str:
    """NormalizedQuery 슬롯에서 sql_history 벡터 검색 쿼리를 합성한다.

    sql_history의 description은 "부서별 분기 매출 실적 집계" 같은
    비즈니스 목적 문장이다. 따라서 검색 쿼리도 동일한 형식으로
    재구성해야 임베딩 공간에서 가깝다.

    합성 순서:
        1. rewritten_query — LLM이 정제한 비즈니스 표현
        2. intent.primary — 동작 유형 힌트
        3. entities[].normalized_term — 도메인 엔티티
        4. measures[].term/normalized_term — 지표 명칭
        5. dimensions[].term — "~별" 분류축
        6. vector_search — LLM 생성 보충 텍스트
    """
    nq = normalized_query

    # 1. 기본 텍스트
    base = _extract_base_query(nq)
    parts: list[str] = [base] if base else []
    base_lower = base.lower()

    # 2. Intent → 비즈니스 동작어
    action = _extract_intent_action(nq)
    if action and action not in base:
        parts.append(action)

    # 3~4. Entity + Measure 용어
    entity_terms = _extract_slot_terms(
        getattr(nq, "entities", []),
    )
    measure_terms = _extract_slot_terms(
        getattr(nq, "measures", []),
    )
    _append_novel_terms(parts, entity_terms, base_lower)
    _append_novel_terms(parts, measure_terms, base_lower)

    # 5. Dimension "~별" 분류축
    dim_terms = _extract_dimension_terms(
        getattr(nq, "dimensions", []),
    )
    _append_novel_terms(parts, dim_terms, base_lower)

    # 6. vector_search 보충
    if vector_search_fallback:
        existing = set(base_lower.split())
        novel = [
            w for w in vector_search_fallback.split()
            if len(w) >= 2 and w.lower() not in existing
        ]
        parts.extend(novel)

    result = " ".join(parts)

    logger.debug(
        "sql_history 벡터 쿼리 합성",
        base=base[:60],
        result=result[:80],
    )

    return result


# ──────────────────────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────────────────────

def _enrich_query(base_query: str, keywords: list[str]) -> str:
    """기존 쿼리에 누락된 키워드를 추가하여 보강한다."""
    existing = set(base_query.lower().split())
    additions = [
        kw for kw in keywords
        if kw.lower() not in existing and len(kw) >= 2
    ]
    if additions:
        return f"{base_query} {' '.join(additions)}"
    return base_query


# ──────────────────────────────────────────────────────────────
# 모듈 레벨 싱글턴
# ──────────────────────────────────────────────────────────────

_builder = SearchQueryBuilder()


def build_source_queries(preprocessed_input: str) -> SourceQuery:
    """모듈 레벨 편의 함수. SearchQueryBuilder.build()를 호출한다."""
    return _builder.build(preprocessed_input)


def build_source_queries_with_normalization(
    preprocessed_input: str,
    normalized_query: object | None = None,
) -> SourceQuery:
    """NormalizedQuery 활용 편의 함수.

    normalized_query 가 있으면 search_keywords 를 활용하여 보강하고,
    없으면 기존 build() 를 사용한다.
    """
    if normalized_query is not None:
        return _builder.build_from_normalized(
            preprocessed_input, normalized_query,
        )
    return _builder.build(preprocessed_input)
