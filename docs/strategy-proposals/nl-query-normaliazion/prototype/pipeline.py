"""
=============================================================================
 자연어 질의 정규화 시스템 - 실행 파이프라인
=============================================================================
 아키텍처:
 
   [사용자 질의]
        │
        ▼
   ┌─────────────────┐
   │  Intent Gate     │  DATA_QUERY / CASUAL_TALK / META_QUESTION
   │  (0단계 분류)    │  / CLARIFICATION / AMBIGUOUS
   └────────┬────────┘
            │ DATA_QUERY만 통과
            ▼
   ┌─────────────────┐
   │ 1. Preprocessor  │  구어체 정리, 약어 확장
   └────────┬────────┘
            ▼
   ┌─────────────────┐
   │ 2. Phase 1 LLM   │  8-Slot 분해 (system + user prompt)
   └────────┬────────┘
            ▼
   ┌─────────────────┐
   │ 3. Validator     │  JSON 파싱 + Enum 값 검증
   └────────┬────────┘
            ▼
   ┌─────────────────┐
   │ 4. Phase 2 LLM   │  교차 검증 R1~R12 + 모호성 해소
   └────────┬────────┘
            ▼
   ┌─────────────────┐
   │ 5. PostProcessor │  최종 정합성 + 검색 키워드 최적화
   └────────┬────────┘
            ▼
   [NormalizedQuery JSON]
=============================================================================
"""

import json
import re
import logging
from datetime import date
from typing import Optional, Tuple

from enums import (
    QueryCategory, IntentType, EntityType, ConfidenceLevel,
    AggFunction, MeasureType, DimensionRole, TimeGranularity,
    DimensionGranularity, FilterType, FilterPosition,
    TimeType, TimePeriodResolve, ModifierType, SortDirection,
    OutputFormat, NormalizedQuery
)
from prompts import (
    INTENT_GATE_SYSTEM_PROMPT, INTENT_GATE_USER_TEMPLATE,
    PHASE1_SYSTEM_PROMPT, PHASE1_USER_TEMPLATE,
    PHASE2_SYSTEM_PROMPT, PHASE2_USER_TEMPLATE,
)
from synonyms import (
    get_synonym_prompt_text, get_output_template_prompt_text,
    build_reverse_lookup
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM 클라이언트 인터페이스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class LLMClient:
    """LLM 호출 추상 클래스. 사용하는 LLM API에 맞게 call()을 구현합니다."""

    def __init__(self, model: str = "claude-sonnet-4-20250514", temperature: float = 0.0):
        self.model = model
        self.temperature = temperature

    def call(self, system_prompt: str, user_prompt: str) -> str:
        """
        실제 구현 예시 (Anthropic Claude):
        
            import anthropic
            client = anthropic.Anthropic(api_key="...")
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                temperature=self.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return response.content[0].text
        """
        raise NotImplementedError("LLMClient.call()을 구현해 주세요.")


class MockLLMClient(LLMClient):
    """테스트용 Mock 클라이언트. 미리 준비된 응답을 순서대로 반환합니다."""
    
    def __init__(self, responses: list = None):
        super().__init__()
        self._responses = responses or []
        self._call_count = 0
    
    def call(self, system_prompt: str, user_prompt: str) -> str:
        if self._call_count < len(self._responses):
            result = self._responses[self._call_count]
            self._call_count += 1
            return result
        return "{}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 0: Intent Gate (의도 게이트)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class IntentGate:
    """
    파이프라인 진입 전 1차 분류.
    DATA_QUERY만 8-Slot 정규화로 진행하고,
    나머지는 각 전용 처리 경로로 라우팅합니다.
    """

    VALID_CATEGORIES = {e.value for e in QueryCategory}

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def classify(self, query: str) -> dict:
        """질의 카테고리를 분류하여 반환합니다."""
        user_prompt = INTENT_GATE_USER_TEMPLATE.format(query=query)
        raw = self.llm.call(
            system_prompt=INTENT_GATE_SYSTEM_PROMPT,
            user_prompt=user_prompt
        )
        
        try:
            cleaned = re.sub(r'```(?:json)?\s*', '', raw)
            cleaned = re.sub(r'```', '', cleaned).strip()
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error(f"[Intent Gate] JSON 파싱 실패, AMBIGUOUS로 처리")
            return {
                "category": "AMBIGUOUS",
                "confidence": "LOW",
                "reason": "Intent Gate 응답 파싱 실패"
            }
        
        # 카테고리 검증
        cat = result.get("category", "").upper()
        if cat not in self.VALID_CATEGORIES:
            logger.warning(f"[Intent Gate] 미인식 카테고리 '{cat}' → AMBIGUOUS로 보정")
            result["category"] = "AMBIGUOUS"
        
        return result

    def route(self, query: str, gate_result: dict) -> dict:
        """분류 결과에 따라 적절한 응답 구조를 반환합니다."""
        category = gate_result["category"]
        
        if category == "CASUAL_TALK":
            return {
                "category": "CASUAL_TALK",
                "action": "RESPOND_CHAT",
                "original_query": query,
                "message": "일반 대화로 분류됨 — 대화형 응답 처리로 라우팅"
            }
        
        elif category == "META_QUESTION":
            # 메타 질의에서 핵심 키워드만 추출
            return {
                "category": "META_QUESTION",
                "action": "SEARCH_META",
                "original_query": query,
                "message": "메타 질의로 분류됨 — 테이블/컬럼 메타 검색으로 라우팅"
            }
        
        elif category == "CLARIFICATION":
            return {
                "category": "CLARIFICATION",
                "action": "MERGE_WITH_PREVIOUS",
                "original_query": query,
                "message": "보충 요청으로 분류됨 — 이전 정규화 결과와 병합 필요"
            }
        
        elif category == "AMBIGUOUS":
            return {
                "category": "AMBIGUOUS",
                "action": "ASK_USER",
                "original_query": query,
                "suggested_question": "어떤 데이터를 조회하거나 분석하고 싶으신 건가요?",
                "message": "의도 불명확 — 사용자에게 되물음"
            }
        
        # DATA_QUERY는 None을 반환하여 파이프라인 계속 진행
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 1: 전처리기 (Preprocessor)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Preprocessor:
    """자연어 질의를 LLM 입력 전에 최소한으로 정리합니다."""

    def __init__(self):
        self.reverse_lookup = build_reverse_lookup()

    def clean(self, query: str) -> str:
        query = re.sub(r'\s+', ' ', query).strip()
        query = re.sub(r'[~～]+', '~', query)
        return query

    def expand_abbreviations(self, query: str) -> str:
        abbrev_map = {
            "YoY":  "전년동기대비",
            "MoM":  "전월대비",
            "QoQ":  "전분기대비",
            "YTD":  "연초부터현재까지",
            "MTD":  "월초부터현재까지",
            "ARPU": "객단가",
            "CVR":  "전환율",
            "DAU":  "일일활성사용자수",
            "MAU":  "월간활성사용자수",
            "GMV":  "총거래액",
            "AOV":  "평균주문금액",
        }
        for abbr, full in abbrev_map.items():
            query = re.sub(rf'\b{re.escape(abbr)}\b', full, query, flags=re.IGNORECASE)
        return query

    def process(self, query: str) -> str:
        query = self.clean(query)
        query = self.expand_abbreviations(query)
        logger.info(f"[전처리 완료] {query}")
        return query


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 3: 검증기 (Validator)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Validator:
    """LLM 출력 JSON을 파싱하고 Enum 필드 허용값을 검증합니다."""

    VALID_INTENTS       = {e.value for e in IntentType}
    VALID_ENTITY_TYPES  = {e.value for e in EntityType}
    VALID_CONFIDENCE    = {e.value for e in ConfidenceLevel}
    VALID_AGG_FUNCS     = {e.value for e in AggFunction}
    VALID_MEASURE_TYPES = {e.value for e in MeasureType}
    VALID_DIM_ROLES     = {e.value for e in DimensionRole}
    VALID_TIME_GRAN     = {e.value for e in TimeGranularity}
    VALID_DIM_GRAN      = {e.value for e in DimensionGranularity}
    VALID_FILTER_TYPES  = {e.value for e in FilterType}
    VALID_FILTER_POS    = {e.value for e in FilterPosition}
    VALID_TIME_TYPES    = {e.value for e in TimeType}
    VALID_TIME_RESOLVE  = {e.value for e in TimePeriodResolve}
    VALID_MOD_TYPES     = {e.value for e in ModifierType}
    VALID_SORT_DIR      = {e.value for e in SortDirection}
    VALID_OUTPUT_FMT    = {e.value for e in OutputFormat}

    def parse_json(self, raw_text: str) -> dict:
        cleaned = re.sub(r'```(?:json)?\s*', '', raw_text)
        cleaned = re.sub(r'```', '', cleaned).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"[JSON 파싱 실패] {e}")
            raise ValueError(f"LLM이 유효한 JSON을 반환하지 않았습니다: {e}")

    def _validate_enum(self, value: str, valid_set: set, field_name: str) -> str:
        if value in valid_set:
            return value
        upper = value.upper()
        if upper in valid_set:
            logger.warning(f"[자동보정] {field_name}: '{value}' → '{upper}'")
            return upper
        logger.error(f"[Enum 검증 실패] {field_name}: '{value}'")
        return None

    def validate_structure(self, data: dict) -> Tuple[dict, list]:
        errors = []

        # intent
        intent = data.get("intent", {})
        primary = self._validate_enum(
            intent.get("primary", ""), self.VALID_INTENTS, "intent.primary"
        )
        if not primary:
            intent["primary"] = "EXTRACT"
            errors.append("intent.primary 보정 → EXTRACT")
        secondaries = []
        for s in intent.get("secondary", []):
            v = self._validate_enum(s, self.VALID_INTENTS, "intent.secondary")
            if v:
                secondaries.append(v)
        intent["secondary"] = secondaries

        # entities
        for i, e in enumerate(data.get("entities", [])):
            if not self._validate_enum(e.get("type", ""), self.VALID_ENTITY_TYPES, f"entities[{i}].type"):
                e["type"] = "DIRECT"
            if not self._validate_enum(e.get("confidence", ""), self.VALID_CONFIDENCE, f"entities[{i}].conf"):
                e["confidence"] = "MEDIUM"

        # measures
        for i, m in enumerate(data.get("measures", [])):
            if not self._validate_enum(m.get("agg_function", ""), self.VALID_AGG_FUNCS, f"measures[{i}].agg"):
                m["agg_function"] = "UNKNOWN"
            if not self._validate_enum(m.get("measure_type", ""), self.VALID_MEASURE_TYPES, f"measures[{i}].type"):
                m["measure_type"] = "RAW"

        # dimensions
        for i, d in enumerate(data.get("dimensions", [])):
            if not self._validate_enum(d.get("role", ""), self.VALID_DIM_ROLES, f"dim[{i}].role"):
                d["role"] = "GROUP"
            gran = d.get("granularity", "")
            if gran not in (self.VALID_TIME_GRAN | self.VALID_DIM_GRAN):
                d["granularity"] = "UNKNOWN"

        # filters
        for i, f in enumerate(data.get("filters", [])):
            if not self._validate_enum(f.get("filter_type", ""), self.VALID_FILTER_TYPES, f"filter[{i}].type"):
                f["filter_type"] = "EQUALS"
            if not self._validate_enum(f.get("position", ""), self.VALID_FILTER_POS, f"filter[{i}].pos"):
                f["position"] = "PRE_AGG"

        # time
        time_slot = data.get("time", {})
        if not self._validate_enum(time_slot.get("type", ""), self.VALID_TIME_TYPES, "time.type"):
            time_slot["type"] = "NONE"

        # modifiers
        for i, mod in enumerate(data.get("modifiers", [])):
            if not self._validate_enum(mod.get("type", ""), self.VALID_MOD_TYPES, f"mod[{i}].type"):
                mod["_remove"] = True
                errors.append(f"modifiers[{i}] 제거됨")
            if mod.get("direction"):
                if not self._validate_enum(mod["direction"], self.VALID_SORT_DIR, f"mod[{i}].dir"):
                    mod["direction"] = "DESC"
        data["modifiers"] = [m for m in data.get("modifiers", []) if not m.get("_remove")]

        # output_hint
        oh = data.get("output_hint", {})
        if oh:
            if not self._validate_enum(oh.get("format", ""), self.VALID_OUTPUT_FMT, "output_hint.format"):
                oh["format"] = "NONE"
        else:
            data["output_hint"] = {"format": "NONE", "doc_type": None,
                                   "expected_columns": [], "confidence": "LOW"}

        return data, errors


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step 5: 후처리기 (PostProcessor)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PostProcessor:
    """Phase 2 결과를 코드 기반으로 최종 정제합니다."""

    STOPWORDS = {
        "좀", "을", "를", "이", "가", "의", "에", "에서", "으로", "로",
        "한", "된", "하는", "있는", "해줘", "줘", "해주세요", "알려줘",
        "뽑아줘", "보여줘", "분석해줘", "조회해줘", "확인해줘",
        "부탁", "감사", "그리고", "또는", "및", "와", "과"
    }

    def optimize_meta_keywords(self, keywords: list) -> list:
        optimized = []
        seen = set()
        for kw in keywords:
            kw_clean = kw.strip()
            if kw_clean and kw_clean not in self.STOPWORDS and kw_clean not in seen:
                optimized.append(kw_clean)
                seen.add(kw_clean)
        return optimized

    def ensure_consistency(self, data: dict) -> dict:
        intent_set = {data["intent"]["primary"]} | set(data["intent"].get("secondary", []))
        
        # AGGREGATE: measures에 집계함수 필수
        has_group = any(d.get("role") == "GROUP" for d in data.get("dimensions", []))
        if has_group or "AGGREGATE" in intent_set:
            for m in data.get("measures", []):
                if m.get("agg_function") == "NONE":
                    m["agg_function"] = "SUM"
                    m["confidence"] = "MEDIUM"
                    data.setdefault("ambiguities", []).append(
                        f"'{m.get('term')}'의 집계함수가 명시되지 않아 SUM으로 추정됨"
                    )

        # RANK: by 필드 필수
        for mod in data.get("modifiers", []):
            if mod.get("type") == "RANK" and not mod.get("by"):
                if data.get("measures"):
                    mod["by"] = data["measures"][0].get("term", "")

        # output_hint: expected_columns를 meta_search에 병합
        oh = data.get("output_hint", {})
        if oh.get("expected_columns"):
            sk = data.get("search_keywords", {})
            existing = set(sk.get("meta_search", []))
            for col in oh["expected_columns"]:
                if col not in existing:
                    sk.setdefault("meta_search", []).append(col)

        # search_keywords 최적화
        sk = data.get("search_keywords", {})
        if "meta_search" in sk:
            sk["meta_search"] = self.optimize_meta_keywords(sk["meta_search"])

        return data

    def process(self, data: dict) -> dict:
        data = self.ensure_consistency(data)
        logger.info("[후처리 완료]")
        return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 파이프라인
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QueryNormalizationPipeline:
    """
    자연어 질의 → 정규화 JSON 변환 전체 파이프라인.
    
    사용법:
        pipeline = QueryNormalizationPipeline(llm_client=MyLLMClient())
        result = pipeline.run("지난 분기 대비 이번 분기 지역별 매출 상위 30개 대리점 실적 뽑아줘")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.gate = IntentGate(llm_client)
        self.preprocessor = Preprocessor()
        self.validator = Validator()
        self.postprocessor = PostProcessor()

    def run(self, raw_query: str) -> dict:
        today = date.today().isoformat()
        
        # ── Step 0: Intent Gate ──
        logger.info(f"[Step 0] Intent Gate: {raw_query}")
        gate_result = self.gate.classify(raw_query)
        logger.info(f"[Step 0] 분류 결과: {gate_result}")
        
        # DATA_QUERY가 아니면 라우팅 결과 반환
        routed = self.gate.route(raw_query, gate_result)
        if routed is not None:
            return routed

        # ── Step 1: 전처리 ──
        logger.info("[Step 1] 전처리")
        cleaned = self.preprocessor.process(raw_query)

        # ── Step 2: Phase 1 LLM (8-Slot 분해) ──
        logger.info("[Step 2] Phase 1 LLM 호출")
        synonym_text = get_synonym_prompt_text()
        template_text = get_output_template_prompt_text()
        
        # output_template_text를 system prompt에 주입
        phase1_system = PHASE1_SYSTEM_PROMPT.replace(
            "{output_template_text}", template_text
        )
        phase1_user = PHASE1_USER_TEMPLATE.format(
            query=cleaned,
            today=today,
            synonym_dict=synonym_text
        )
        phase1_raw = self.llm.call(system_prompt=phase1_system, user_prompt=phase1_user)

        # ── Step 3: 검증 ──
        logger.info("[Step 3] Phase 1 검증")
        phase1_data = self.validator.parse_json(phase1_raw)
        phase1_data, errors1 = self.validator.validate_structure(phase1_data)
        phase1_data["original_query"] = raw_query
        if errors1:
            logger.warning(f"[Step 3] 검증 오류 {len(errors1)}건")

        # ── Step 4: Phase 2 LLM (교차 검증 R1~R12) ──
        logger.info("[Step 4] Phase 2 LLM 호출")
        phase1_json_str = json.dumps(phase1_data, ensure_ascii=False, indent=2)
        phase2_user = PHASE2_USER_TEMPLATE.format(
            query=cleaned,
            phase1_json=phase1_json_str
        )
        phase2_raw = self.llm.call(system_prompt=PHASE2_SYSTEM_PROMPT, user_prompt=phase2_user)
        
        phase2_data = self.validator.parse_json(phase2_raw)
        phase2_data, errors2 = self.validator.validate_structure(phase2_data)
        phase2_data["original_query"] = raw_query

        # ── Step 5: 후처리 ──
        logger.info("[Step 5] 후처리")
        result = self.postprocessor.process(phase2_data)

        logger.info("[파이프라인 완료]")
        return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 데모
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def demo():
    """다양한 입력 유형에 대한 파이프라인 동작을 시연합니다."""

    # ── Mock 응답 준비 (3개: Intent Gate + Phase1 + Phase2) ──
    
    # 테스트 1: 일반 대화
    mock_casual = MockLLMClient(responses=[
        json.dumps({"category": "CASUAL_TALK", "confidence": "HIGH",
                     "reason": "인사말"})
    ])
    p1 = QueryNormalizationPipeline(llm_client=mock_casual)
    print("=" * 70)
    print("[테스트 1] 일반 대화: '안녕하세요'")
    print("=" * 70)
    r1 = p1.run("안녕하세요")
    print(json.dumps(r1, ensure_ascii=False, indent=2))

    # 테스트 2: 메타 질의
    mock_meta = MockLLMClient(responses=[
        json.dumps({"category": "META_QUESTION", "confidence": "HIGH",
                     "reason": "테이블 구조 질문"})
    ])
    p2 = QueryNormalizationPipeline(llm_client=mock_meta)
    print("\n" + "=" * 70)
    print("[테스트 2] 메타 질의: '고객 테이블에 어떤 컬럼이 있어?'")
    print("=" * 70)
    r2 = p2.run("고객 테이블에 어떤 컬럼이 있어?")
    print(json.dumps(r2, ensure_ascii=False, indent=2))

    # 테스트 3: 데이터 질의 (거래명세)
    mock_phase1 = json.dumps({
        "original_query": "3월 거래명세 조회해줘",
        "rewritten_query": "3월의 거래명세서를 조회 (거래일자, 거래처, 품목, 수량, 단가, 공급가액, 세액, 합계 포함)",
        "intent": {"primary": "EXTRACT", "secondary": []},
        "entities": [
            {"term": "거래", "normalized_term": "거래", "type": "DIRECT", "confidence": "HIGH", "note": None}
        ],
        "measures": [],
        "dimensions": [],
        "filters": [],
        "time": {
            "type": "ABSOLUTE",
            "base_period": {"label": "3월", "resolve": "ABSOLUTE_RANGE", "n": None,
                           "absolute_start": "2026-03-01", "absolute_end": "2026-03-31"},
            "compare_period": None
        },
        "modifiers": [],
        "output_hint": {
            "format": "SPEC_SHEET",
            "doc_type": "거래명세",
            "expected_columns": [
                "거래일자", "거래번호", "거래처명", "품목명",
                "수량", "단가", "공급가액", "세액", "합계금액", "비고"
            ],
            "confidence": "HIGH",
            "note": "공급가액 = 수량 × 단가, 세액 = 공급가액 × 세율"
        },
        "ambiguities": [],
        "search_keywords": {
            "meta_search": ["거래", "거래처", "상품", "거래명세"],
            "vector_search": "거래명세서 형식으로 월별 거래 내역을 조회하는 쿼리"
        }
    }, ensure_ascii=False)

    mock_phase2 = json.dumps({
        "original_query": "3월 거래명세 조회해줘",
        "rewritten_query": "2026년 3월의 거래명세서를 조회 (거래일자, 거래번호, 거래처명, 품목명, 수량, 단가, 공급가액, 세액, 합계금액 포함)",
        "intent": {"primary": "EXTRACT", "secondary": []},
        "entities": [
            {"term": "거래", "normalized_term": "거래", "type": "DIRECT", "confidence": "HIGH", "note": None},
            {"term": "거래처", "normalized_term": "거래처", "type": "IMPLIED", "confidence": "HIGH",
             "note": "R11: 거래명세 형식에 거래처 정보 필요"},
            {"term": "상품", "normalized_term": "상품", "type": "IMPLIED", "confidence": "HIGH",
             "note": "R11: 거래명세 형식에 품목 정보 필요"}
        ],
        "measures": [
            {"term": "공급가액", "normalized_term": None, "measure_type": "DERIVED",
             "agg_function": "NONE", "confidence": "HIGH",
             "note": "R12: 공급가액 = 수량 × 단가"}
        ],
        "dimensions": [],
        "filters": [],
        "time": {
            "type": "ABSOLUTE",
            "base_period": {"label": "3월", "resolve": "ABSOLUTE_RANGE", "n": None,
                           "absolute_start": "2026-03-01", "absolute_end": "2026-03-31"},
            "compare_period": None
        },
        "modifiers": [],
        "output_hint": {
            "format": "SPEC_SHEET",
            "doc_type": "거래명세",
            "expected_columns": [
                "거래일자", "거래번호", "거래처명", "품목명",
                "수량", "단가", "공급가액", "세액", "합계금액", "비고"
            ],
            "confidence": "HIGH",
            "note": "공급가액 = 수량 × 단가, 세액 = 공급가액 × 세율"
        },
        "ambiguities": [
            "세율 기준(10% 고정 vs 품목별 차등) 확인 필요"
        ],
        "search_keywords": {
            "meta_search": ["거래", "거래처", "상품", "거래명세",
                           "거래일자", "거래번호", "공급가액", "세액"],
            "vector_search": "거래명세서 형식으로 월별 거래 내역을 조회하는 쿼리"
        }
    }, ensure_ascii=False)

    mock_data = MockLLMClient(responses=[
        json.dumps({"category": "DATA_QUERY", "confidence": "HIGH",
                     "reason": "거래 엔티티 + 조회 동사 + 시간 조건 충족"}),
        mock_phase1,
        mock_phase2,
    ])
    p3 = QueryNormalizationPipeline(llm_client=mock_data)
    print("\n" + "=" * 70)
    print("[테스트 3] 데이터 질의: '3월 거래명세 조회해줘'")
    print("=" * 70)
    r3 = p3.run("3월 거래명세 조회해줘")
    print(json.dumps(r3, ensure_ascii=False, indent=2))

    return r1, r2, r3


if __name__ == "__main__":
    demo()
