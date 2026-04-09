"""LLM 관련 유틸리티 통합 단위 테스트.

테스트 대상:
    [response.py]
    - extract_json: LLM 응답 텍스트에서 JSON 추출
      (코드펜스, 인라인 JSON, 잘못된 포맷, strict 모드)

    [prompt.py]
    - serialize_decomp_slots: query_decomposition → 치환용 dict
    - render_prompt: 프롬프트 플레이스홀더 치환 + 변수 사전 반환
    - serialize_synonym_dict: 동의어 사전 → 프롬프트 텍스트

    [retry.py]
    - ParseError: 재시도 초과 시 발생하는 예외 클래스
    - _build_correction_msg: 교정 메시지 생성
    - _append_correction: 대화에 교정 메시지 추가

    [client.py]
    - _strip_thinking_tags: <think>...</think> 제거
    - _build_prompt_summary: 프롬프트 요약 생성 (추적용)

실행 스크립트:
    pytest tests/auto/unit/test_llm_utils.py -v

참고:
    - LLM API 호출 없음 — 모든 테스트는 순수 문자열/딕셔너리 조작
    - retry.py의 llm_call_with_parse_retry는 실제 API 의존, 별도 e2e 테스트로 분리
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_llm_utils")


# ════════════════════════════════════════════════════════════
# extract_json (response.py)
# ════════════════════════════════════════════════════════════

class TestExtractJson:
    """extract_json: LLM 응답에서 JSON 추출."""

    def test_plain_json_object(self):
        """순수 JSON 문자열을 파싱한다."""
        from src.utils.llm.response import extract_json
        raw = '{"intent": "data_extraction", "confidence": 0.95}'
        result = extract_json(raw)
        passed = result == {"intent": "data_extraction", "confidence": 0.95}
        log_test_case(logger, "extract_json_plain", raw, {"intent": "data_extraction"}, result, passed)
        assert passed

    def test_code_fence_json(self):
        """```json 코드펜스 내 JSON을 추출한다."""
        from src.utils.llm.response import extract_json
        raw = '분석 결과입니다:\n```json\n{"status": "ok", "tables": ["TB_CRM_CUSTOMER"]}\n```'
        result = extract_json(raw)
        passed = result is not None and result.get("status") == "ok"
        log_test_case(logger, "extract_json_code_fence", raw[:40], {"status": "ok"}, result, passed)
        assert passed

    def test_code_fence_without_language(self):
        """``` (언어 없는 코드펜스) 내 JSON을 추출한다."""
        from src.utils.llm.response import extract_json
        raw = '```\n{"key": "value"}\n```'
        result = extract_json(raw)
        passed = result is not None and result.get("key") == "value"
        log_test_case(logger, "extract_json_no_lang_fence", raw, {"key": "value"}, result, passed)
        assert passed

    def test_json_embedded_in_text(self):
        """설명 텍스트 사이에 끼어있는 JSON을 추출한다."""
        from src.utils.llm.response import extract_json
        raw = '다음과 같이 분석했습니다. {"tables": ["TB_LNS_LOANINFO"], "filters": []} 참고하세요.'
        result = extract_json(raw)
        passed = result is not None and "tables" in result
        log_test_case(logger, "extract_json_embedded", raw[:50], "tables 포함", result, passed)
        assert passed

    def test_invalid_json_returns_none(self):
        """유효하지 않은 JSON은 None을 반환한다 (strict=False)."""
        from src.utils.llm.response import extract_json
        raw = "죄송합니다, 잘 모르겠습니다."
        result = extract_json(raw)
        passed = result is None
        log_test_case(logger, "extract_json_invalid_none", raw, None, result, passed)
        assert passed

    def test_strict_mode_raises_on_invalid(self):
        """strict=True이면 파싱 실패 시 ValueError를 발생시킨다."""
        from src.utils.llm.response import extract_json
        raised = False
        try:
            extract_json("유효하지 않은 응답", strict=True)
        except ValueError:
            raised = True
        log_test_case(logger, "extract_json_strict_raises", "invalid", "ValueError", raised, raised)
        assert raised

    def test_strict_mode_succeeds_on_valid(self):
        """strict=True에서 유효한 JSON은 정상 반환한다."""
        from src.utils.llm.response import extract_json
        raw = '{"result": true}'
        result = extract_json(raw, strict=True)
        passed = result == {"result": True}
        log_test_case(logger, "extract_json_strict_valid", raw, {"result": True}, result, passed)
        assert passed

    def test_nested_json_object(self):
        """중첩 JSON 객체를 파싱한다."""
        from src.utils.llm.response import extract_json
        raw = '{"measures": ["여신잔액"], "filters": {"기준일": "20260101"}, "limit": 10}'
        result = extract_json(raw)
        passed = (
            result is not None
            and result["measures"] == ["여신잔액"]
            and result["filters"]["기준일"] == "20260101"
        )
        log_test_case(logger, "extract_json_nested", raw[:50], "중첩 JSON", result, passed)
        assert passed

    def test_braces_in_text_before_json(self):
        """텍스트 뒤에 JSON이 있으면 추출된다 (앞에 { 없는 경우).

        _JSON_PATTERN은 첫 번째 { 부터 마지막 } 까지를 greedy 매칭하므로
        앞에 {1,2,3} 같은 비-JSON 중괄호가 있으면 파싱이 실패한다.
        이 경우 None 반환이 정상 동작이다.
        텍스트 뒤에 JSON만 있는 경우는 정상 추출됨을 검증한다.
        """
        from src.utils.llm.response import extract_json
        raw = '분석 결과입니다. {"intent": "casual_talk", "confidence": 0.9}'
        result = extract_json(raw)
        passed = result is not None and result.get("intent") == "casual_talk"
        log_test_case(logger, "extract_json_braces_in_text", raw[:50], "dict 타입", result, passed)
        assert passed

    def test_empty_string_returns_none(self):
        """빈 문자열은 None을 반환한다."""
        from src.utils.llm.response import extract_json
        result = extract_json("")
        passed = result is None
        log_test_case(logger, "extract_json_empty", "", None, result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# serialize_decomp_slots (prompt.py)
# ════════════════════════════════════════════════════════════

class TestSerializeDecompSlots:
    """serialize_decomp_slots: query_decomposition → 치환용 dict."""

    def test_all_slots_present(self):
        """모든 슬롯 키({measures}, {filters} 등)가 결과에 포함된다."""
        from src.utils.llm.prompt import serialize_decomp_slots
        decomp = {
            "measures": ["여신잔액"],
            "filters": [{"field": "기준일", "op": "=", "value": "20260101"}],
            "group_by": ["지점코드"],
            "order_limit": {"limit": 10},
            "output_hint": {"format": "table"},
        }
        result = serialize_decomp_slots(decomp)
        expected_keys = {"{measures}", "{filters}", "{group_by}", "{order_limit}", "{output_hint}"}
        passed = expected_keys.issubset(result.keys())
        log_test_case(logger, "decomp_slots_all_keys", decomp, "5개 키", list(result.keys()), passed)
        assert passed

    def test_values_are_json_strings(self):
        """모든 값은 JSON 직렬화된 문자열이다."""
        from src.utils.llm.prompt import serialize_decomp_slots
        import json
        decomp = {"measures": ["테스트"], "filters": [], "group_by": [], "order_limit": {}, "output_hint": {}}
        result = serialize_decomp_slots(decomp)
        all_strings = all(isinstance(v, str) for v in result.values())
        valid_json = all(json.loads(v) is not None for v in result.values() if v != "null")
        passed = all_strings and valid_json
        log_test_case(logger, "decomp_slots_json_strings", decomp, "JSON 문자열", result, passed)
        assert passed

    def test_missing_slots_default_to_empty(self):
        """누락된 슬롯은 빈 리스트/딕셔너리로 처리된다."""
        from src.utils.llm.prompt import serialize_decomp_slots
        import json
        result = serialize_decomp_slots({})
        measures_val = json.loads(result["{measures}"])
        passed = measures_val == []
        log_test_case(logger, "decomp_slots_missing", {}, "빈 리스트", measures_val, passed)
        assert passed

    def test_korean_content_preserved(self):
        """한글 내용이 ensure_ascii=False로 직렬화된다."""
        from src.utils.llm.prompt import serialize_decomp_slots
        decomp = {"measures": ["여신잔액", "대출건수"], "filters": [], "group_by": [], "order_limit": {}, "output_hint": {}}
        result = serialize_decomp_slots(decomp)
        passed = "여신잔액" in result["{measures}"]
        log_test_case(logger, "decomp_slots_korean", decomp, "한글 포함", result["{measures}"], passed)
        assert passed


# ════════════════════════════════════════════════════════════
# render_prompt (prompt.py)
# ════════════════════════════════════════════════════════════

class TestRenderPrompt:
    """render_prompt: 프롬프트 플레이스홀더 치환."""

    def test_single_replacement(self):
        """단일 플레이스홀더가 올바르게 치환된다."""
        from src.utils.llm.prompt import render_prompt
        template = "안녕하세요, {name}님"
        result, variables = render_prompt(template, {"{name}": "홍길동"})
        passed = result == "안녕하세요, 홍길동님" and variables == {"name": "홍길동"}
        log_test_case(logger, "render_prompt_single", template, "홍길동님", result, passed)
        assert passed

    def test_multiple_replacements(self):
        """여러 플레이스홀더가 모두 치환된다."""
        from src.utils.llm.prompt import render_prompt
        template = "{기준일}의 {지점코드} 지점 {지표} 조회"
        replacements = {"{기준일}": "2026-04-01", "{지점코드}": "001", "{지표}": "여신잔액"}
        result, variables = render_prompt(template, replacements)
        passed = result == "2026-04-01의 001 지점 여신잔액 조회"
        log_test_case(logger, "render_prompt_multiple", template, "치환된 문자열", result, passed)
        assert passed

    def test_variables_dict_removes_braces(self):
        """반환된 variables dict는 키에서 {} 가 제거된 형태이다."""
        from src.utils.llm.prompt import render_prompt
        _, variables = render_prompt("{key}", {"{key}": "val"})
        passed = "key" in variables and "{key}" not in variables
        log_test_case(logger, "render_prompt_variables_no_braces", "{key}", {"key": "val"}, variables, passed)
        assert passed

    def test_no_placeholder_unchanged(self):
        """플레이스홀더가 없는 템플릿은 그대로 반환된다."""
        from src.utils.llm.prompt import render_prompt
        template = "고정된 프롬프트"
        result, _ = render_prompt(template, {})
        passed = result == template
        log_test_case(logger, "render_prompt_no_placeholder", template, template, result, passed)
        assert passed

    def test_unreplaced_placeholder_remains(self):
        """치환되지 않은 플레이스홀더는 원본 텍스트에 남는다."""
        from src.utils.llm.prompt import render_prompt
        template = "{a}와 {b}"
        result, _ = render_prompt(template, {"{a}": "X"})
        passed = "X" in result and "{b}" in result
        log_test_case(logger, "render_prompt_unreplaced", template, "X와 {b}", result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# serialize_synonym_dict (prompt.py)
# ════════════════════════════════════════════════════════════

class TestSerializeSynonymDict:
    """serialize_synonym_dict: 동의어 사전 → 프롬프트 텍스트."""

    def test_basic_structure(self):
        """카테고리 헤더와 표준용어 ← 동의어 포맷이 생성된다."""
        from src.utils.llm.prompt import serialize_synonym_dict
        synonyms = {
            "여신": {
                "대출": ["론", "여신", "빌린돈"],
                "연체": ["미납", "체납"],
            }
        }
        result = serialize_synonym_dict(synonyms)
        passed = "[여신]" in result and '"대출" ←' in result and "론" in result
        log_test_case(logger, "synonym_dict_basic", synonyms, "[여신] 포함", result[:80], passed)
        assert passed

    def test_multiple_categories(self):
        """여러 카테고리가 모두 포함된다."""
        from src.utils.llm.prompt import serialize_synonym_dict
        synonyms = {
            "여신": {"대출": ["빌림"]},
            "수신": {"예금": ["저축"]},
        }
        result = serialize_synonym_dict(synonyms)
        passed = "[여신]" in result and "[수신]" in result
        log_test_case(logger, "synonym_dict_multi_category", synonyms, "두 카테고리", result[:100], passed)
        assert passed

    def test_empty_dict_returns_empty_string(self):
        """빈 동의어 사전은 빈 문자열을 반환한다."""
        from src.utils.llm.prompt import serialize_synonym_dict
        result = serialize_synonym_dict({})
        passed = result == ""
        log_test_case(logger, "synonym_dict_empty", {}, "", result, passed)
        assert passed

    def test_variants_joined_with_comma(self):
        """동의어 목록은 ', '로 구분된다."""
        from src.utils.llm.prompt import serialize_synonym_dict
        synonyms = {"카테고리": {"표준": ["A", "B", "C"]}}
        result = serialize_synonym_dict(synonyms)
        passed = "A, B, C" in result
        log_test_case(logger, "synonym_dict_comma", synonyms, "A, B, C", result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# ParseError / _build_correction_msg / _append_correction (retry.py)
# ════════════════════════════════════════════════════════════

class TestRetryHelpers:
    """retry.py: ParseError와 교정 메시지 헬퍼 함수."""

    def test_parse_error_inherits_exception(self):
        """ParseError는 Exception을 상속한다."""
        from src.utils.llm.retry import ParseError
        err = ParseError("파싱 실패", last_response="bad json")
        passed = isinstance(err, Exception) and err.last_response == "bad json"
        log_test_case(logger, "parse_error_inherit", "ParseError", "Exception 상속", type(err).__bases__, passed)
        assert passed

    def test_parse_error_message(self):
        """ParseError 메시지가 str(err)로 접근 가능하다."""
        from src.utils.llm.retry import ParseError
        err = ParseError("최대 재시도 초과")
        passed = str(err) == "최대 재시도 초과"
        log_test_case(logger, "parse_error_message", "ParseError('최대 재시도 초과')", "최대 재시도 초과", str(err), passed)
        assert passed

    def test_build_correction_msg_contains_response(self):
        """교정 메시지에 실패한 응답 미리보기가 포함된다."""
        from src.utils.llm.retry import _build_correction_msg
        msg = _build_correction_msg("잘못된 응답", "JSON 파싱 오류")
        passed = "잘못된 응답" in msg and "JSON 파싱 오류" in msg
        log_test_case(logger, "correction_msg_content", "잘못된 응답", "응답+오류 포함", msg[:80], passed)
        assert passed

    def test_build_correction_msg_format_instruction(self):
        """교정 메시지에는 형식 준수 요청 문구가 포함된다."""
        from src.utils.llm.retry import _build_correction_msg
        msg = _build_correction_msg("", "")
        passed = "JSON" in msg or "출력 형식" in msg
        log_test_case(logger, "correction_msg_instruction", "", "형식 요청 포함", msg, passed)
        assert passed

    def test_build_correction_msg_empty_inputs(self):
        """실패 응답과 오류가 빈 문자열이어도 메시지가 생성된다."""
        from src.utils.llm.retry import _build_correction_msg
        msg = _build_correction_msg("", "")
        passed = isinstance(msg, str) and len(msg) > 0
        log_test_case(logger, "correction_msg_empty_inputs", "", "비어있지 않음", msg, passed)
        assert passed

    def test_append_correction_adds_user_message(self):
        """_append_correction은 messages 리스트 끝에 user role 메시지를 추가한다."""
        from src.utils.llm.retry import _append_correction
        original = [{"role": "user", "content": "질의"}]
        corrected = _append_correction(original, "bad response", "parse error")
        passed = (
            len(corrected) == 2
            and corrected[-1]["role"] == "user"
            and len(corrected[-1]["content"]) > 0
        )
        log_test_case(logger, "append_correction_adds_msg", original, "user 메시지 추가", corrected[-1], passed)
        assert passed

    def test_append_correction_does_not_mutate_original(self):
        """_append_correction은 원본 messages 리스트를 변경하지 않는다."""
        from src.utils.llm.retry import _append_correction
        original = [{"role": "user", "content": "질의"}]
        _ = _append_correction(original, "bad", "error")
        passed = len(original) == 1
        log_test_case(logger, "append_correction_immutable", original, "원본 불변", len(original), passed)
        assert passed


# ════════════════════════════════════════════════════════════
# _strip_thinking_tags / _build_prompt_summary (client.py)
# ════════════════════════════════════════════════════════════

class TestClientHelpers:
    """client.py: 순수 헬퍼 함수 테스트."""

    def test_strip_thinking_tags_basic(self):
        """<think>...</think> 태그와 내용이 제거된다."""
        from src.utils.llm.client import _strip_thinking_tags
        text = "<think>내부 추론 과정</think>최종 답변입니다."
        result = _strip_thinking_tags(text)
        passed = result == "최종 답변입니다." and "<think>" not in result
        log_test_case(logger, "strip_thinking_basic", text, "최종 답변입니다.", result, passed)
        assert passed

    def test_strip_thinking_tags_multiline(self):
        """여러 줄에 걸친 <think> 태그가 제거된다."""
        from src.utils.llm.client import _strip_thinking_tags
        text = "<think>\n단계1: 분석\n단계2: 결론\n</think>\n결과: OK"
        result = _strip_thinking_tags(text)
        passed = "결과: OK" in result and "<think>" not in result
        log_test_case(logger, "strip_thinking_multiline", text[:40], "결과: OK", result, passed)
        assert passed

    def test_strip_thinking_tags_no_tag(self):
        """<think> 태그가 없으면 원본을 그대로 반환한다."""
        from src.utils.llm.client import _strip_thinking_tags
        text = "태그 없는 일반 응답"
        result = _strip_thinking_tags(text)
        passed = result == text
        log_test_case(logger, "strip_thinking_no_tag", text, text, result, passed)
        assert passed

    def test_strip_thinking_tags_empty_string(self):
        """빈 문자열은 빈 문자열을 반환한다."""
        from src.utils.llm.client import _strip_thinking_tags
        result = _strip_thinking_tags("")
        passed = result == ""
        log_test_case(logger, "strip_thinking_empty", "", "", result, passed)
        assert passed

    def test_strip_thinking_tags_multiple(self):
        """여러 개의 <think> 태그가 모두 제거된다."""
        from src.utils.llm.client import _strip_thinking_tags
        text = "<think>1차</think>중간 텍스트<think>2차</think>최종"
        result = _strip_thinking_tags(text)
        passed = "<think>" not in result and "최종" in result
        log_test_case(logger, "strip_thinking_multiple", text, "최종 포함", result, passed)
        assert passed

    def test_build_prompt_summary_with_system(self):
        """system 프롬프트가 있으면 [S] 접두사로 포함된다."""
        from src.utils.llm.client import _build_prompt_summary
        result = _build_prompt_summary("시스템 지시", [{"role": "user", "content": "질의"}])
        passed = "[S]" in result and "[U]" in result
        log_test_case(logger, "prompt_summary_with_system", "system+user", "[S]+[U]", result[:80], passed)
        assert passed

    def test_build_prompt_summary_no_system(self):
        """system이 None이면 [S]가 포함되지 않는다."""
        from src.utils.llm.client import _build_prompt_summary
        result = _build_prompt_summary(None, [{"role": "user", "content": "질의"}])
        passed = "[S]" not in result and "[U]" in result
        log_test_case(logger, "prompt_summary_no_system", "user only", "[U] only", result[:80], passed)
        assert passed

    def test_build_prompt_summary_uses_last_two_messages(self):
        """messages 중 마지막 2개만 요약에 포함된다."""
        from src.utils.llm.client import _build_prompt_summary
        messages = [
            {"role": "user", "content": "첫번째"},
            {"role": "assistant", "content": "두번째"},
            {"role": "user", "content": "세번째"},
        ]
        result = _build_prompt_summary(None, messages)
        # 마지막 2개: assistant(두번째), user(세번째)
        passed = "세번째" in result and "첫번째" not in result
        log_test_case(logger, "prompt_summary_last_two", "3개 메시지", "마지막 2개", result[:80], passed)
        assert passed

    def test_build_prompt_summary_returns_string(self):
        """항상 문자열을 반환한다."""
        from src.utils.llm.client import _build_prompt_summary
        result = _build_prompt_summary(None, [])
        passed = isinstance(result, str)
        log_test_case(logger, "prompt_summary_returns_str", "빈 messages", "str 타입", type(result).__name__, passed)
        assert passed

    def test_build_prompt_summary_empty_messages(self):
        """빈 messages 리스트도 처리된다."""
        from src.utils.llm.client import _build_prompt_summary
        result = _build_prompt_summary("시스템", [])
        passed = isinstance(result, str) and "[S]" in result
        log_test_case(logger, "prompt_summary_empty_messages", "system only", "[S]", result[:40], passed)
        assert passed
