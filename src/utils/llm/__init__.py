"""LLM 클라이언트 및 재시도 유틸리티 — 프로바이더 추상화와 파싱 재시도 패키지.

Anthropic Claude와 OpenAI 호환 API(로컬 모델 포함)를 동일한 인터페이스로
호출할 수 있도록 프로바이더를 추상화하고, 응답 파싱 실패 시 자동 재시도
로직을 제공한다.

주요 내보내기(exports):
    - get_llm_client / UnifiedLLMClient: 설정 기반으로 적절한 프로바이더의
      LLM 클라이언트를 싱글턴으로 생성·반환한다. 폐쇄망 소형 모델 전환 시에도
      클라이언트 코드 변경 없이 설정만으로 교체할 수 있다.
    - llm_call_with_parse_retry: LLM 호출 후 응답 파싱에 실패하면 에러 피드백을
      포함하여 재호출하는 루프. 소형/로컬 모델의 불안정한 출력 형식에 대응한다.
    - ParseError: 파싱 재시도 한도 초과 시 발생하는 예외.
"""

from src.utils.llm.client import UnifiedLLMClient, get_llm_client, reset_llm_client
from src.utils.llm.retry import ParseError, llm_call_with_parse_retry

__all__ = [
    "UnifiedLLMClient",
    "get_llm_client",
    "reset_llm_client",
    "ParseError",
    "llm_call_with_parse_retry",
]
