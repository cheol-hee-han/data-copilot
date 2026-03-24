"""파이프라인 End-to-End 통합 테스트.

LLM 호출(의도 분류, SQL 생성, 정규화)은 Mock으로 대체하여
API 키 없이 실행 가능하다.
커넥터는 use_dummy=True 로 설정된 Dummy 커넥터를 사용한다.

테스트 범위:
  전처리 → 의도 분류(Mock) → 질의 정규화(Mock)
  → 컨텍스트 수집(Dummy) → SQL 검증까지의 흐름
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.models.normalization import NormalizedQuery
from src.agents.state.state import IntentType, PipelineState, QueryStatus
from src.agents.nodes.context_collector import collect_context_node
from src.services.intent_resolver import _parse_intent_response
from src.agents.nodes.intent_classifier import classify_intent_node
from src.agents.nodes.preprocessor import preprocess_node
from src.services.query_normalizer import (
    _parse_llm_json,
    _postprocess,
    _validate_structure,
)
from src.agents.nodes.query_normalizer import normalize_query_node
from src.agents.nodes.sql_validator import validate_sql_node


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_mock_message(text: str) -> MagicMock:
    """Anthropic 응답 객체를 흉내 내는 Mock을 생성한다."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _has_validation_errors(result: dict) -> bool:
    """검증 오류가 있는지 확인한다."""
    return bool(result.get("sql_validation_errors"))


def _make_intent_gate_response(
    category: str = "DATA_QUERY",
    confidence: str = "HIGH",
    reason: str = "테스트",
) -> MagicMock:
    """Intent Gate JSON 응답 Mock을 생성한다."""
    gate_json = json.dumps({
        "category": category,
        "confidence": confidence,
        "reason": reason,
    })
    return _make_mock_message(gate_json)


def _make_normalization_response(
    intent_primary: str = "EXTRACT",
    entities: list | None = None,
    measures: list | None = None,
) -> MagicMock:
    """정규화 Phase 1 LLM 응답 Mock을 생성한다."""
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
    return _make_mock_message(
        json.dumps(nq_data, ensure_ascii=False),
    )


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

        preprocess_result = await preprocess_node(state)
        assert preprocess_result["status"] == QueryStatus.PREPROCESSING
        state = state.model_copy(update=preprocess_result)

        mock_resp = _make_intent_gate_response(
            category="DATA_QUERY",
            confidence="HIGH",
            reason="고객 엔티티 + 조회 동사",
        )
        with patch(
            "src.services.intent_resolver.get_llm_client",
        ) as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                return_value=mock_resp,
            )
            intent_result = await classify_intent_node(state)

        assert intent_result["intent"] == IntentType.DATA_EXTRACTION
        assert intent_result["query_category"] == "DATA_QUERY"
        assert intent_result["status"] == QueryStatus.INTENT_CLASSIFIED

    @pytest.mark.asyncio
    async def test_analysis_intent_flow(self):
        """분석 의도 흐름: Intent Gate가 직접 DATA_ANALYSIS로 분류."""
        state = PipelineState(
            user_input="지난 분기 대비 이번 분기 연체율 추이 분석해줘",
        )

        preprocess_result = await preprocess_node(state)
        state = state.model_copy(update=preprocess_result)

        mock_resp = _make_intent_gate_response(
            category="DATA_ANALYSIS",
            confidence="HIGH",
            reason="추이 분석 + 비교 요청",
        )
        with patch(
            "src.services.intent_resolver.get_llm_client",
        ) as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                return_value=mock_resp,
            )
            intent_result = await classify_intent_node(state)

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

        mock_resp = _make_intent_gate_response(
            category="CASUAL_TALK",
            confidence="HIGH",
            reason="인사말",
        )
        with patch(
            "src.services.intent_resolver.get_llm_client",
        ) as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                return_value=mock_resp,
            )
            intent_result = await classify_intent_node(state)

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

        mock_resp = _make_intent_gate_response(
            category="META_QUESTION",
            confidence="HIGH",
            reason="테이블 구조 질문",
        )
        with patch(
            "src.services.intent_resolver.get_llm_client",
        ) as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                return_value=mock_resp,
            )
            intent_result = await classify_intent_node(state)

        assert intent_result["intent"] == IntentType.META_QUESTION

    @pytest.mark.asyncio
    async def test_ambiguous_routes_to_clarification(self):
        """모호한 요청 흐름: AMBIGUOUS → clarification_needed."""
        state = PipelineState(
            user_input="데이터 좀 뽑아줘",
            preprocessed_input="데이터 좀 뽑아줘",
            status=QueryStatus.PREPROCESSING,
        )

        mock_resp = _make_intent_gate_response(
            category="AMBIGUOUS",
            confidence="LOW",
            reason="구체적 엔티티 없음",
        )
        with patch(
            "src.services.intent_resolver.get_llm_client",
        ) as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                return_value=mock_resp,
            )
            intent_result = await classify_intent_node(state)

        assert intent_result["intent"] == IntentType.CLARIFICATION_NEEDED

    @pytest.mark.asyncio
    async def test_sql_injection_blocked_before_intent(self):
        """SQL 인젝션은 전처리에서 차단된다."""
        state = PipelineState(
            user_input="고객 목록; DROP TABLE customers--",
        )
        preprocess_result = await preprocess_node(state)
        assert preprocess_result["status"] == QueryStatus.ERROR

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_legacy(self):
        """Intent Gate LLM 실패 시 레거시 분류로 폴백한다."""
        state = PipelineState(
            user_input="이번 달 신규 고객 수",
            preprocessed_input="이번 달 신규 고객 수",
            status=QueryStatus.PREPROCESSING,
        )

        # Intent Gate 호출 실패 → _classify_legacy 폴백
        # _classify_legacy 는 llm_call_with_parse_retry 를 사용
        legacy_resp = _make_mock_message(
            "INTENT: data_extraction\nCONFIDENCE: 0.85",
        )

        # Intent Gate 용 클라이언트: 에러 발생
        gate_client = AsyncMock()
        gate_client.messages.create = AsyncMock(
            side_effect=Exception("API 연결 오류"),
        )
        # 레거시 폴백용 클라이언트: 정상 응답
        legacy_client = AsyncMock()
        legacy_client.messages.create = AsyncMock(
            return_value=legacy_resp,
        )

        call_count = 0

        def _mock_get_client():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return gate_client
            return legacy_client

        with patch(
            "src.services.intent_resolver.get_llm_client",
            side_effect=_mock_get_client,
        ), patch(
            "src.utils.llm.retry.get_llm_client",
            return_value=legacy_client,
        ):
            intent_result = await classify_intent_node(state)

        # 레거시 폴백으로 정상 분류
        assert intent_result["intent"] == IntentType.DATA_EXTRACTION


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

        phase1_resp = _make_normalization_response(
            intent_primary="AGGREGATE",
            entities=[
                {
                    "term": "고객",
                    "type": "DIRECT",
                    "confidence": "HIGH",
                },
            ],
            measures=[
                {
                    "term": "고객수",
                    "measure_type": "RAW",
                    "agg_function": "COUNT",
                    "confidence": "HIGH",
                },
            ],
        )

        with patch(
            "src.services.query_normalizer.settings",
        ) as mock_settings:
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0

            with patch(
                "src.services.query_normalizer.get_llm_client",
            ) as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.messages.create = AsyncMock(
                    return_value=phase1_resp,
                )
                result = await normalize_query_node(state)

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
                {"term": "대출", "type": "DIRECT", "confidence": "HIGH"},
                {"term": "지점", "type": "DIRECT", "confidence": "HIGH"},
            ],
            "measures": [
                {
                    "term": "여신잔액",
                    "measure_type": "RAW",
                    "agg_function": "SUM",
                    "confidence": "HIGH",
                },
            ],
            "dimensions": [
                {
                    "term": "지점",
                    "role": "GROUP",
                    "granularity": "CATEGORY",
                    "confidence": "HIGH",
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
        phase1_resp = _make_mock_message(
            json.dumps(phase1_data, ensure_ascii=False),
        )

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
        phase2_resp = _make_mock_message(
            json.dumps(phase2_data, ensure_ascii=False),
        )

        with patch(
            "src.services.query_normalizer.settings",
        ) as mock_settings:
            mock_settings.normalization_phase2_enabled = True
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0

            with patch(
                "src.services.query_normalizer.get_llm_client",
            ) as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.messages.create = AsyncMock(
                    side_effect=[phase1_resp, phase2_resp],
                )
                result = await normalize_query_node(state)

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
                {"term": "대출", "type": "DIRECT", "confidence": "HIGH"},
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
                "confidence": "HIGH",
            },
            "ambiguities": [],
            "rewritten_query": "2026년 3월 연체명세서 조회",
            "search_keywords": {
                "meta_search": ["대출", "연체"],
                "vector_search": "연체명세서 조회",
            },
        }
        mock_resp = _make_mock_message(
            json.dumps(nq_data, ensure_ascii=False),
        )

        with patch(
            "src.services.query_normalizer.settings",
        ) as mock_settings:
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0

            with patch(
                "src.services.query_normalizer.get_llm_client",
            ) as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.messages.create = AsyncMock(
                    return_value=mock_resp,
                )
                result = await normalize_query_node(state)

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

        with patch(
            "src.services.query_normalizer.settings",
        ) as mock_settings:
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0

            with patch(
                "src.services.query_normalizer.get_llm_client",
            ) as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.messages.create = AsyncMock(
                    side_effect=Exception("LLM 연결 실패"),
                )
                result = await normalize_query_node(state)

        # 실패해도 파이프라인 계속 진행
        assert result["status"] == QueryStatus.QUERY_NORMALIZED
        nq = result["normalized_query"]
        assert isinstance(nq, NormalizedQuery)
        assert nq.original_query == "이번 달 고객 수"


# ---------------------------------------------------------------------------
# 전체 흐름: 전처리 → 의도 분류 → 정규화 → 컨텍스트 수집
# ---------------------------------------------------------------------------


class TestFullFlowWithNormalization:
    """전처리 ~ 정규화 ~ 컨텍스트 수집까지 전체 흐름 검증."""

    @pytest.mark.asyncio
    async def test_full_flow_to_context_collection(self):
        """전처리 → 의도분류 → 정규화 → 컨텍스트 수집 전체 흐름."""
        state = PipelineState(
            user_input="이번 달 대출 유형별 건수 알려줘",
        )

        # 1. 전처리
        pre_result = await preprocess_node(state)
        assert pre_result["status"] == QueryStatus.PREPROCESSING
        state = state.model_copy(update=pre_result)

        # 2. 의도 분류 (Mock Intent Gate)
        intent_resp = _make_intent_gate_response("DATA_QUERY")
        with patch(
            "src.services.intent_resolver.get_llm_client",
        ) as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                return_value=intent_resp,
            )
            intent_result = await classify_intent_node(state)
        state = state.model_copy(update=intent_result)
        assert state.intent == IntentType.DATA_EXTRACTION

        # 3. 정규화 (Mock LLM)
        norm_resp = _make_normalization_response(
            intent_primary="AGGREGATE",
            entities=[
                {"term": "대출", "type": "DIRECT", "confidence": "HIGH"},
            ],
            measures=[
                {
                    "term": "건수",
                    "measure_type": "RAW",
                    "agg_function": "COUNT",
                    "confidence": "HIGH",
                },
            ],
        )
        with patch(
            "src.services.query_normalizer.settings",
        ) as mock_settings:
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0

            with patch(
                "src.services.query_normalizer.get_llm_client",
            ) as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.messages.create = AsyncMock(
                    return_value=norm_resp,
                )
                norm_result = await normalize_query_node(state)
        state = state.model_copy(update=norm_result)
        assert state.normalized_query is not None
        assert state.status == QueryStatus.QUERY_NORMALIZED

        # 4. 컨텍스트 수집 (Dummy)
        ctx_result = await collect_context_node(state)
        assert ctx_result["status"] == QueryStatus.CONTEXT_COLLECTED
        assert "context" in ctx_result

    @pytest.mark.asyncio
    async def test_full_flow_to_sql_validation(self):
        """전체 흐름 → SQL 검증 통과까지."""
        state = PipelineState(
            user_input="지점별 고객 수 알려줘",
        )

        # 전처리
        pre_result = await preprocess_node(state)
        state = state.model_copy(update=pre_result)

        # 의도 분류
        intent_resp = _make_intent_gate_response("DATA_QUERY")
        with patch(
            "src.services.intent_resolver.get_llm_client",
        ) as mock_get:
            mock_client = AsyncMock()
            mock_get.return_value = mock_client
            mock_client.messages.create = AsyncMock(
                return_value=intent_resp,
            )
            intent_result = await classify_intent_node(state)
        state = state.model_copy(update=intent_result)

        # 정규화
        norm_resp = _make_normalization_response("AGGREGATE")
        with patch(
            "src.services.query_normalizer.settings",
        ) as mock_settings:
            mock_settings.normalization_phase2_enabled = False
            mock_settings.normalization_max_tokens = 3000
            mock_settings.llm_model = "test-model"
            mock_settings.llm_long_timeout = 30.0

            with patch(
                "src.services.query_normalizer.get_llm_client",
            ) as mock_get:
                mock_client = AsyncMock()
                mock_get.return_value = mock_client
                mock_client.messages.create = AsyncMock(
                    return_value=norm_resp,
                )
                norm_result = await normalize_query_node(state)
        state = state.model_copy(update=norm_result)

        # 컨텍스트 수집
        ctx_result = await collect_context_node(state)
        state = state.model_copy(update=ctx_result)

        # SQL 주입 후 검증
        valid_sql = (
            "SELECT b.BRCH_NM, COUNT(*) AS cust_cnt "
            "FROM TB_CUST_INFO c "
            "JOIN TB_BRANCH_INFO b ON c.BRCH_CD = b.BRCH_CD "
            "GROUP BY b.BRCH_NM ORDER BY cust_cnt DESC"
        )
        state = state.model_copy(update={
            "generated_sql": valid_sql,
            "status": QueryStatus.SQL_GENERATED,
        })

        validation_result = validate_sql_node(state)
        assert validation_result["status"] == QueryStatus.SQL_VALIDATED


# ---------------------------------------------------------------------------
# 컨텍스트 수집 흐름 (Dummy 커넥터)
# ---------------------------------------------------------------------------


class TestContextCollection:
    """Dummy 커넥터를 사용한 컨텍스트 수집 흐름."""

    @pytest.mark.asyncio
    async def test_context_collected_status(self):
        """컨텍스트 수집 후 CONTEXT_COLLECTED 상태 반환."""
        state = PipelineState(
            preprocessed_input="이번 달 신규 고객 수 알려줘",
            intent=IntentType.DATA_EXTRACTION,
            status=QueryStatus.INTENT_CLASSIFIED,
        )
        result = await collect_context_node(state)
        assert result["status"] == QueryStatus.CONTEXT_COLLECTED

    @pytest.mark.asyncio
    async def test_context_with_normalized_query(self):
        """NormalizedQuery 가 있으면 검색이 보강된다."""
        nq = NormalizedQuery(
            original_query="대출 연체 현황",
            intent={"primary": "AGGREGATE", "secondary": []},
            entities=[
                {"term": "대출", "type": "DIRECT", "confidence": "HIGH"},
            ],
            search_keywords={
                "meta_search": ["대출", "연체", "연체금액", "연체율"],
                "vector_search": "대출 연체 현황 조회",
            },
        )
        state = PipelineState(
            preprocessed_input="대출 연체 현황",
            intent=IntentType.DATA_EXTRACTION,
            status=QueryStatus.QUERY_NORMALIZED,
            normalized_query=nq,
        )
        result = await collect_context_node(state)
        assert result["status"] == QueryStatus.CONTEXT_COLLECTED
        assert hasattr(result["context"], "table_metas")

    @pytest.mark.asyncio
    async def test_context_contains_domain_terms(self):
        """컨텍스트에 도메인 용어 정보가 포함된다."""
        state = PipelineState(
            preprocessed_input="대출 연체 현황",
            intent=IntentType.DATA_EXTRACTION,
            status=QueryStatus.INTENT_CLASSIFIED,
        )
        result = await collect_context_node(state)
        context = result["context"]
        assert isinstance(context.domain_terms, dict)
        assert len(context.domain_terms) > 0


# ---------------------------------------------------------------------------
# SQL 검증 흐름
# ---------------------------------------------------------------------------


class TestSQLValidation:
    """SQL 검증 노드 테스트."""

    @pytest.mark.asyncio
    async def test_valid_select_passes(self):
        """정상 SELECT SQL이 검증을 통과한다."""
        state = PipelineState(
            generated_sql=(
                "SELECT LOAN_TYPE_CD, COUNT(*) AS cnt "
                "FROM TB_LOAN_INFO "
                "WHERE LOAN_DT >= DATE_TRUNC('month', CURRENT_DATE) "
                "GROUP BY LOAN_TYPE_CD"
            ),
            status=QueryStatus.SQL_GENERATED,
        )
        result = validate_sql_node(state)
        assert result["status"] == QueryStatus.SQL_VALIDATED

    @pytest.mark.asyncio
    async def test_dml_blocked(self):
        """DML SQL이 차단된다."""
        state = PipelineState(
            generated_sql="DELETE FROM TB_CUST_INFO WHERE 1=1",
            status=QueryStatus.SQL_GENERATED,
        )
        result = validate_sql_node(state)
        assert _has_validation_errors(result)

    @pytest.mark.asyncio
    async def test_pii_blocked(self):
        """PII 컬럼 직접 노출이 차단된다."""
        state = PipelineState(
            generated_sql=(
                "SELECT CUST_NO, JUMIN_NO FROM TB_CUST_INFO"
            ),
            status=QueryStatus.SQL_GENERATED,
        )
        result = validate_sql_node(state)
        assert _has_validation_errors(result)

    @pytest.mark.asyncio
    async def test_system_catalog_blocked(self):
        """시스템 카탈로그 접근이 차단된다."""
        state = PipelineState(
            generated_sql=(
                "SELECT table_name "
                "FROM information_schema.tables LIMIT 50"
            ),
            status=QueryStatus.SQL_GENERATED,
        )
        result = validate_sql_node(state)
        assert _has_validation_errors(result)


# ---------------------------------------------------------------------------
# 의도 분류 응답 파서 단위 테스트 (레거시 호환)
# ---------------------------------------------------------------------------


class TestIntentParser:
    """의도 분류 LLM 응답 파서를 검증한다."""

    def test_parse_data_extraction(self):
        intent, conf = _parse_intent_response(
            "INTENT: data_extraction\nCONFIDENCE: 0.95",
        )
        assert intent == IntentType.DATA_EXTRACTION
        assert conf == pytest.approx(0.95)

    def test_parse_data_analysis(self):
        intent, conf = _parse_intent_response(
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
    """골든셋의 expected_sql 이 검증 노드를 통과하는지 확인한다."""

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
        state = PipelineState(generated_sql=sql)
        result = validate_sql_node(state)
        assert result["status"] == QueryStatus.SQL_VALIDATED, (
            f"[{case_id}] SQL 검증 실패: "
            f"{result.get('sql_validation_errors')}"
        )
