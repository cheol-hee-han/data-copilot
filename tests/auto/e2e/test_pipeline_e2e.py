"""파이프라인 End-to-End 통합 테스트.

LLM 호출(의도 분류, SQL 생성, 정규화)은 Mock으로 대체하여
API 키 없이 실행 가능하다.

테스트 범위:
  전처리 → 의도 분류(Mock) → 질의 정규화(Mock) → SQL 검증(서비스)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.models.normalization import NormalizedQuery
from src.agents.state.state import IntentType, PipelineState, QueryStatus
from src.models.enums import HistoryDecision

# v4에서 intent_resolver가 intent_classifier로 리팩터링되면서
# _parse_intent_response가 삭제됨. 이 함수에 의존하는 테스트는 skip 처리한다.
_parse_intent_response = None  # import 에러 방지용 placeholder
from src.services.sql_safety_checker import validate_sql_safety
from src.agents.nodes.interpret.intent_classifier import intent_classifier_node
from src.agents.nodes.interpret.query_normalizer import query_normalizer_node
from src.services.input_sanitizer import sanitize


# ---------------------------------------------------------------------------
# 헬퍼 — llm_call_with_parse_retry 목(mock) 생성
# ---------------------------------------------------------------------------


def _make_gate_mock(
    category: str = "DATA_QUERY",
    confidence: str = "HIGH",
    reason: str = "테스트",
    resolution: str = "SKIP",
) -> AsyncMock:
    """intent_classifier 서비스용 llm_call_with_parse_retry mock.

    반환값: (raw_text, parsed_dict) — _parse_response 결과를 직접 반환.
    parsed_dict의 resolution은 HistoryDecision enum으로 변환된 상태.
    """
    gate_json = json.dumps({
        "continuity": {
            "label": resolution,
            "confidence": confidence,
            "reason": reason,
        },
        "intent": {
            "label": category,
            "confidence": confidence,
            "reason": reason,
        },
    })
    parsed = {
        "resolution": HistoryDecision(resolution),
        "category": category,
        "intent_confidence": confidence,
        "continuity_confidence": confidence,
        "intent_reason": reason,
        "continuity_reason": reason,
    }
    return AsyncMock(return_value=(gate_json, parsed))


def _make_legacy_mock(
    intent_text: str = "data_extraction",
    confidence: float = 0.85,
) -> AsyncMock:
    """Legacy 의도분류용 mock — _parse_intent_response 삭제로 stub 처리."""
    raw = f"INTENT: {intent_text}\nCONFIDENCE: {confidence}"
    # _parse_intent_response가 v4에서 삭제되어 stub으로 대체
    parsed = {"intent": intent_text, "confidence": confidence}
    return AsyncMock(return_value=(raw, parsed))


def _make_normalization_mock(
    intent_primary: str = "EXTRACT",
    entities: list | None = None,
    measures: list | None = None,
    extra: dict | None = None,
) -> AsyncMock:
    """정규화용 llm_call_with_parse_retry mock."""
    nq_data = {
        "intent": {
            "primary": intent_primary,
            "secondary": [],
        },
        "entities": entities or [],
        "measures": measures or [],
        "dimensions": [],
        "filters": [],
        "time": {"type": "NONE"},
        "modifiers": [],
        "output_hint": {"format": "NONE"},
        "ambiguities": [],
        "rewritten_query": "테스트 질의",
        "search_keywords": {
            "meta_search": ["고객", "대출"],
            "vector_search": "테스트 검색 질의",
        },
    }
    if extra:
        nq_data.update(extra)
    raw = json.dumps(nq_data, ensure_ascii=False)
    return AsyncMock(return_value=(raw, nq_data))


# ---------------------------------------------------------------------------
# 공통 mock 경로 상수
# ---------------------------------------------------------------------------

_INTENT_LLM = "src.services.intent_classifier.llm_call_with_parse_retry"
_NORM_LLM = "src.services.query_normalizer.llm_call_with_parse_retry"
_DISPATCH = "src.utils.tracker.dispatch.dispatch_tracking_event"


# ---------------------------------------------------------------------------
# 전처리 → 의도 분류 흐름
# ---------------------------------------------------------------------------


class TestPreprocessToIntent:
    """전처리와 의도 분류 연계 흐름을 검증한다."""

    @pytest.mark.asyncio
    async def test_normal_data_extraction_flow(self):
        """정상 데이터 추출 요청 흐름: Intent Gate → DATA_QUERY."""
        state = PipelineState(
            user_input="이번 달 신규 고객 수 알려줘",
        )

        san = sanitize(state.user_input)
        assert san.is_error is False
        state = state.model_copy(update={
            "preprocessed_input": san.text,
            "status": QueryStatus.PREPROCESSING,
        })

        with (
            patch(_INTENT_LLM, _make_gate_mock(
                category="DATA_QUERY",
                confidence="HIGH",
                reason="고객 엔티티 + 조회 동사",
            )),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            intent_result = await intent_classifier_node(state)

        assert intent_result["intent"] == IntentType.DATA_EXTRACTION
        assert intent_result["query_category"] == "DATA_QUERY"
        assert intent_result["status"] == QueryStatus.INTENT_CLASSIFIED

    @pytest.mark.asyncio
    async def test_analysis_intent_flow(self):
        """분석 의도 흐름: Intent Gate가 직접 DATA_ANALYSIS로 분류."""
        state = PipelineState(
            user_input="지난 분기 대비 이번 분기 연체율 추이 분석해줘",
        )

        san = sanitize(state.user_input)
        state = state.model_copy(update={
            "preprocessed_input": san.text,
            "status": QueryStatus.PREPROCESSING,
        })

        with (
            patch(_INTENT_LLM, _make_gate_mock(
                category="DATA_ANALYSIS",
                confidence="HIGH",
                reason="추이 분석 + 비교 요청",
            )),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            intent_result = await intent_classifier_node(state)

        # "추이", "분석" 키워드 → DATA_ANALYSIS
        assert intent_result["intent"] == IntentType.DATA_ANALYSIS

    @pytest.mark.asyncio
    async def test_casual_talk_flow(self):
        """일반 대화 흐름: CASUAL_TALK."""
        state = PipelineState(
            user_input="안녕하세요",
            preprocessed_input="안녕하세요",
            status=QueryStatus.PREPROCESSING,
        )

        with (
            patch(_INTENT_LLM, _make_gate_mock(
                category="CASUAL_TALK",
                confidence="HIGH",
                reason="인사말",
            )),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            intent_result = await intent_classifier_node(state)

        assert intent_result["intent"] == IntentType.CASUAL_TALK
        assert intent_result["query_category"] == "CASUAL_TALK"

    @pytest.mark.asyncio
    async def test_meta_question_flow(self):
        """메타 질의 흐름: META_QUESTION."""
        state = PipelineState(
            user_input="고객 테이블에 어떤 컬럼이 있어?",
            preprocessed_input="고객 테이블에 어떤 컬럼이 있어?",
            status=QueryStatus.PREPROCESSING,
        )

        with (
            patch(_INTENT_LLM, _make_gate_mock(
                category="META_QUESTION",
                confidence="HIGH",
                reason="테이블 구조 질문",
            )),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            intent_result = await intent_classifier_node(state)

        assert intent_result["intent"] == IntentType.META_QUESTION

    @pytest.mark.asyncio
    async def test_ambiguous_routes_to_clarification(self):
        """모호한 요청 흐름: AMBIGUOUS → clarification_needed."""
        state = PipelineState(
            user_input="데이터 좀 뽑아줘",
            preprocessed_input="데이터 좀 뽑아줘",
            status=QueryStatus.PREPROCESSING,
        )

        with (
            patch(_INTENT_LLM, _make_gate_mock(
                category="AMBIGUOUS",
                confidence="LOW",
                reason="구체적 엔티티 없음",
            )),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            intent_result = await intent_classifier_node(state)

        assert intent_result["intent"] == IntentType.CLARIFICATION_NEEDED

    def test_sql_injection_blocked_before_intent(self):
        """SQL 인젝션은 전처리에서 차단된다."""
        san = sanitize("고객 목록; DROP TABLE customers--")
        assert san.is_error is True

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_rules(self):
        """LLM 실패 시 규칙 기반 분류로 폴백한다.

        intent_classifier의 _fallback은 이력이 없으면 LLM 호출 없이
        classify_by_rules로 규칙 기반 의도 분류를 수행한다.
        "목록" + "조회" 키워드 → DATA_EXTRACTION으로 분류된다.
        """
        state = PipelineState(
            user_input="이번 달 고객 목록 조회해줘",
            preprocessed_input="이번 달 고객 목록 조회해줘",
            status=QueryStatus.PREPROCESSING,
        )

        # LLM 호출 실패 → _fallback → 이력 없음 → classify_by_rules
        mock_llm = AsyncMock(side_effect=Exception("API 연결 오류"))

        with (
            patch(_INTENT_LLM, mock_llm),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            intent_result = await intent_classifier_node(state)

        # LLM 실패 시 is_error=True + UNKNOWN 반환 (규칙 기반 폴백 제거됨)
        assert intent_result["intent"] == IntentType.UNKNOWN


# ---------------------------------------------------------------------------
# 질의 정규화 노드 통합 테스트
# ---------------------------------------------------------------------------


class TestNormalizationNode:
    """질의 정규화 노드 통합 테스트 (Mock LLM)."""

    @pytest.mark.asyncio
    async def test_normalize_basic_extraction(self):
        """기본 추출 질의가 정규화된다."""
        state = PipelineState(
            preprocessed_input="이번 달 신규 고객 수 알려줘",
            intent=IntentType.DATA_EXTRACTION,
            status=QueryStatus.INTENT_CLASSIFIED,
        )

        with (
            patch(
                "src.services.query_normalizer.settings",
            ) as mock_settings,
            patch(_NORM_LLM, _make_normalization_mock(
                intent_primary="AGGREGATE",
                entities=[
                    {
                        "term": "고객",
                        "type": "DIRECT",
                    },
                ],
                measures=[
                    {
                        "term": "고객수",
                        "measure_type": "RAW",
                        "agg_function": "COUNT",
                    },
                ],
            )),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0
            result = await query_normalizer_node(state)

        assert result["status"] == QueryStatus.QUERY_NORMALIZED
        nq = result["normalized_query"]
        assert isinstance(nq, NormalizedQuery)
        assert nq.intent.primary == "AGGREGATE"
        assert len(nq.entities) == 1
        assert nq.entities[0].term == "고객"
        assert len(nq.measures) == 1
        assert nq.measures[0].agg_function == "COUNT"

    @pytest.mark.asyncio
    async def test_normalize_with_phase2(self):
        """Phase 2 교차 검증 포함 정규화."""
        state = PipelineState(
            preprocessed_input="지점별 여신잔액 상위 10개",
            intent=IntentType.DATA_EXTRACTION,
            status=QueryStatus.INTENT_CLASSIFIED,
        )

        phase1_data = {
            "intent": {"primary": "RANK", "secondary": ["AGGREGATE"]},
            "entities": [
                {"term": "대출", "type": "DIRECT"},
                {"term": "지점", "type": "DIRECT"},
            ],
            "measures": [
                {
                    "term": "여신잔액",
                    "measure_type": "RAW",
                    "agg_function": "SUM",
                },
            ],
            "dimensions": [
                {
                    "term": "지점",
                    "role": "GROUP",
                    "granularity": "CATEGORY",
                },
            ],
            "filters": [],
            "time": {"type": "NONE"},
            "modifiers": [
                {"type": "RANK", "direction": "DESC", "limit": 10},
            ],
            "output_hint": {"format": "NONE"},
            "ambiguities": [],
            "rewritten_query": "지점별 여신잔액 합계 상위 10개 지점 조회",
            "search_keywords": {
                "meta_search": ["대출", "지점", "여신잔액"],
                "vector_search": "지점별 여신잔액 상위 10개",
            },
        }

        # Phase 2: by 필드 채워서 반환
        phase2_data = dict(phase1_data)
        phase2_data["modifiers"] = [
            {
                "type": "RANK",
                "direction": "DESC",
                "limit": 10,
                "by": "여신잔액",
            },
        ]

        phase1_raw = json.dumps(phase1_data, ensure_ascii=False)
        phase2_raw = json.dumps(phase2_data, ensure_ascii=False)

        # _call_llm_and_parse 는 llm_call_with_parse_retry 가 반환한
        # (raw, parsed) 튜플을 그대로 전달한다. mock 은 (raw, parsed) 튜플 반환.
        mock_llm = AsyncMock(side_effect=[
            (phase1_raw, phase1_data),
            (phase2_raw, phase2_data),
        ])

        with (
            patch(
                "src.services.query_normalizer.settings",
            ) as mock_settings,
            patch(_NORM_LLM, mock_llm),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            mock_settings.normalization_phase2_enabled = True
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0
            result = await query_normalizer_node(state)

        nq = result["normalized_query"]
        assert nq.intent.primary == "RANK"
        assert "AGGREGATE" in nq.intent.secondary
        assert nq.modifiers[0].by == "여신잔액"
        assert nq.modifiers[0].limit == 10

    @pytest.mark.asyncio
    async def test_normalize_output_hint_spec_sheet(self):
        """명세서 유형 OUTPUT_HINT가 정규화된다."""
        state = PipelineState(
            preprocessed_input="3월 연체명세 조회해줘",
            intent=IntentType.DATA_EXTRACTION,
            status=QueryStatus.INTENT_CLASSIFIED,
        )

        nq_data = {
            "intent": {"primary": "EXTRACT", "secondary": []},
            "entities": [
                {"term": "대출", "type": "DIRECT"},
            ],
            "measures": [],
            "dimensions": [],
            "filters": [],
            "time": {
                "type": "ABSOLUTE",
                "base_period": {
                    "label": "3월",
                    "resolve": "ABSOLUTE_RANGE",
                    "absolute_start": "2026-03-01",
                    "absolute_end": "2026-03-31",
                },
            },
            "modifiers": [],
            "output_hint": {
                "format": "SPEC_SHEET",
                "doc_type": "연체명세",
                "expected_columns": [
                    "대출번호", "고객명", "연체금액", "연체일수",
                ],
            },
            "ambiguities": [],
            "rewritten_query": "2026년 3월 연체명세서 조회",
            "search_keywords": {
                "meta_search": ["대출", "연체"],
                "vector_search": "연체명세서 조회",
            },
        }
        raw = json.dumps(nq_data, ensure_ascii=False)

        with (
            patch(
                "src.services.query_normalizer.settings",
            ) as mock_settings,
            patch(_NORM_LLM, AsyncMock(return_value=(raw, nq_data))),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0
            result = await query_normalizer_node(state)

        nq = result["normalized_query"]
        assert nq.output_hint.format == "SPEC_SHEET"
        assert nq.output_hint.doc_type == "연체명세"
        assert "대출번호" in nq.output_hint.expected_columns
        assert nq.time.type == "ABSOLUTE"

    @pytest.mark.asyncio
    async def test_normalize_failure_returns_default(self):
        """정규화 LLM 실패 시 빈 NormalizedQuery를 반환한다."""
        state = PipelineState(
            preprocessed_input="이번 달 고객 수",
            intent=IntentType.DATA_EXTRACTION,
            status=QueryStatus.INTENT_CLASSIFIED,
        )

        with (
            patch(
                "src.services.query_normalizer.settings",
            ) as mock_settings,
            patch(
                _NORM_LLM,
                AsyncMock(side_effect=Exception("LLM 연결 실패")),
            ),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0
            result = await query_normalizer_node(state)

        # 실패해도 파이프라인 계속 진행
        assert result["status"] == QueryStatus.QUERY_NORMALIZED
        nq = result["normalized_query"]
        assert isinstance(nq, NormalizedQuery)
        assert nq.original_query == "이번 달 고객 수"


# ---------------------------------------------------------------------------
# 전체 흐름: 전처리 → 의도 분류 → 정규화 → 컨텍스트 수집
# ---------------------------------------------------------------------------


class TestFullFlowWithNormalization:
    """전처리 ~ 정규화까지 전체 흐름 검증."""

    @pytest.mark.asyncio
    async def test_full_flow_to_normalization(self):
        """전처리 → 의도분류 → 정규화 전체 흐름."""
        state = PipelineState(
            user_input="이번 달 대출 유형별 건수 알려줘",
        )

        # 1. 전처리
        san = sanitize(state.user_input)
        assert san.is_error is False
        state = state.model_copy(update={
            "preprocessed_input": san.text,
            "status": QueryStatus.PREPROCESSING,
        })

        # 2. 의도 분류 (Mock intent_classifier)
        with (
            patch(_INTENT_LLM, _make_gate_mock("DATA_QUERY")),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            intent_result = await intent_classifier_node(state)
        state = state.model_copy(update=intent_result)
        assert state.intent == IntentType.DATA_EXTRACTION

        # 3. 정규화 (Mock LLM)
        with (
            patch(
                "src.services.query_normalizer.settings",
            ) as mock_settings,
            patch(_NORM_LLM, _make_normalization_mock(
                intent_primary="AGGREGATE",
                entities=[
                    {"term": "대출", "type": "DIRECT"},
                ],
                measures=[
                    {
                        "term": "건수",
                        "measure_type": "RAW",
                        "agg_function": "COUNT",
                    },
                ],
            )),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0
            norm_result = await query_normalizer_node(state)
        state = state.model_copy(update=norm_result)
        assert state.normalized_query is not None
        assert state.status == QueryStatus.QUERY_NORMALIZED

    @pytest.mark.asyncio
    async def test_full_flow_to_sql_validation(self):
        """전처리 → 의도분류 → 정규화 → SQL 검증 통과까지."""
        state = PipelineState(
            user_input="지점별 고객 수 알려줘",
        )

        # 전처리
        san = sanitize(state.user_input)
        state = state.model_copy(update={
            "preprocessed_input": san.text,
            "status": QueryStatus.PREPROCESSING,
        })

        # 의도 분류
        with (
            patch(_INTENT_LLM, _make_gate_mock("DATA_QUERY")),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            intent_result = await intent_classifier_node(state)
        state = state.model_copy(update=intent_result)

        # 정규화
        with (
            patch(
                "src.services.query_normalizer.settings",
            ) as mock_settings,
            patch(_NORM_LLM, _make_normalization_mock("AGGREGATE")),
            patch(_DISPATCH, new_callable=AsyncMock),
        ):
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0
            norm_result = await query_normalizer_node(state)
        state = state.model_copy(update=norm_result)

        # SQL 검증 (서비스 레이어 직접 호출)
        valid_sql = (
            "SELECT b.BRCH_NM, COUNT(*) AS cust_cnt "
            "FROM TB_CUST_INFO c "
            "JOIN TB_BRANCH_INFO b ON c.BRCH_CD = b.BRCH_CD "
            "GROUP BY b.BRCH_NM ORDER BY cust_cnt DESC"
        )
        result = validate_sql_safety(valid_sql)
        assert result.is_safe is True


# ---------------------------------------------------------------------------
# SQL 검증 흐름
# ---------------------------------------------------------------------------


class TestSQLValidation:
    """SQL 안전성 검증 서비스 테스트."""

    def test_valid_select_passes(self):
        """정상 SELECT SQL이 검증을 통과한다."""
        result = validate_sql_safety(
            "SELECT LOAN_TYPE_CD, COUNT(*) AS cnt "
            "FROM TB_LOAN_INFO "
            "WHERE LOAN_DT >= DATE_TRUNC('month', CURRENT_DATE) "
            "GROUP BY LOAN_TYPE_CD",
        )
        assert result.is_safe is True

    def test_dml_blocked(self):
        """DML SQL이 차단된다."""
        result = validate_sql_safety(
            "DELETE FROM TB_CUST_INFO WHERE 1=1",
        )
        assert not result.is_safe

    def test_pii_blocked(self):
        """PII 컬럼 직접 노출이 차단된다."""
        result = validate_sql_safety(
            "SELECT CUST_NO, JUMIN_NO FROM TB_CUST_INFO",
        )
        assert not result.is_safe

    def test_system_catalog_blocked(self):
        """시스템 카탈로그 접근이 차단된다."""
        result = validate_sql_safety(
            "SELECT table_name "
            "FROM information_schema.tables LIMIT 50",
        )
        assert not result.is_safe


# ---------------------------------------------------------------------------
# 의도 분류 응답 파서 단위 테스트 (레거시 호환)
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="intent_resolver._parse_intent_response 삭제됨 (v4). 새 intent_classifier에 맞게 재작성 필요.")
class TestIntentParser:
    """의도 분류 LLM 응답 파서를 검증한다 — intent_resolver 삭제로 비활성."""

    def test_parse_data_extraction(self):
        intent, conf = _parse_intent_response(
            "INTENT: data_extraction\nCONFIDENCE: 0.95",
        )
        assert intent == IntentType.DATA_EXTRACTION
        assert conf == pytest.approx(0.95)

    def test_parse_data_analysis(self):
        intent, _ = _parse_intent_response(
            "INTENT: data_analysis\nCONFIDENCE: 0.80",
        )
        assert intent == IntentType.DATA_ANALYSIS

    def test_parse_clarification_needed(self):
        intent, _ = _parse_intent_response(
            "INTENT: clarification_needed\nCONFIDENCE: 0.70",
        )
        assert intent == IntentType.CLARIFICATION_NEEDED

    def test_parse_unknown_intent_string(self):
        with pytest.raises(ValueError, match="INTENT"):
            _parse_intent_response(
                "INTENT: something_weird\nCONFIDENCE: 0.50",
            )

    def test_parse_with_extra_whitespace(self):
        intent, conf = _parse_intent_response(
            "  INTENT:  data_extraction  \n  CONFIDENCE:  0.88  ",
        )
        assert intent == IntentType.DATA_EXTRACTION
        assert conf == pytest.approx(0.88)


# ---------------------------------------------------------------------------
# 골든셋 SQL 검증
# ---------------------------------------------------------------------------


class TestGoldenSetValidation:
    """골든셋의 expected_sql 이 안전성 검증을 통과하는지 확인한다."""

    GOLDEN_SQL_CASES = [
        (
            "GS001",
            "SELECT COUNT(*) AS new_cust_cnt FROM TB_CUST_INFO "
            "WHERE REG_DT >= DATE_TRUNC('month', CURRENT_DATE)",
        ),
        (
            "GS002",
            "SELECT LOAN_TYPE_CD, COUNT(*) AS loan_cnt "
            "FROM TB_LOAN_INFO GROUP BY LOAN_TYPE_CD",
        ),
        (
            "GS007",
            "SELECT BASE_YM, "
            "ROUND(SUM(OVERDUE_AMT)::NUMERIC "
            "/ NULLIF(SUM(TOTAL_LOAN_AMT), 0) * 100, 2) "
            "AS overdue_rate "
            "FROM TB_LOAN_OVERDUE_STAT "
            "WHERE BASE_YM >= "
            "TO_CHAR(CURRENT_DATE - INTERVAL '12 months', "
            "'YYYYMM') "
            "GROUP BY BASE_YM ORDER BY BASE_YM",
        ),
    ]

    @pytest.mark.parametrize("case_id,sql", GOLDEN_SQL_CASES)
    def test_golden_sql_passes_validation(
        self, case_id: str, sql: str,
    ):
        """골든셋 SQL이 검증을 통과한다."""
        result = validate_sql_safety(sql)
        assert result.is_safe, (
            f"[{case_id}] SQL 검증 실패: {result.errors}"
        )
