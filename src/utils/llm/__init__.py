"""LLM 유틸리티 패키지 — 호출·재시도·프롬프트 조립·응답 파싱.

4개 모듈로 구성된 LLM 호출 파이프라인 유틸리티:
    - client: 프로바이더 추상화 (Anthropic / OpenAI 호환)
    - retry: 파싱 실패 시 교정 메시지 포함 재시도 루프
    - prompt: 프롬프트 템플릿 {key} 치환 + dict 직렬화
    - response: LLM 응답 텍스트에서 JSON 객체 추출

주요 내보내기(exports):
    - get_llm_client / UnifiedLLMClient: 설정 기반 싱글턴 LLM 클라이언트
    - llm_call_with_parse_retry / ParseError: 파싱 재시도 루프
    - render_prompt: 프롬프트 템플릿 치환
    - extract_json: LLM 응답 JSON 추출
"""

from src.utils.llm.client import UnifiedLLMClient, get_llm_client, reset_llm_client
from src.utils.llm.response import extract_json
from src.utils.llm.prompt import render_prompt
from src.utils.llm.retry import ParseError, llm_call_with_parse_retry

__all__ = [
    "UnifiedLLMClient",
    "get_llm_client",
    "reset_llm_client",
    "ParseError",
    "llm_call_with_parse_retry",
    "extract_json",
    "render_prompt",
]
