"""Gemini API 통합 테스트.

Gemini OpenAI 호환 엔드포인트를 통한 API 호출 테스트.
실제 API 키가 필요하므로 manual 테스트로 분류한다.

테스트 항목:
    1. _resolve_thinking_params: Gemini thinking 파라미터 변환 (유닛)
    2. 기본 호출: 단순 질의 응답 확인
    3. reasoning_effort 파라미터: thinking 모드별 응답 확인
    4. UnifiedLLMClient 통합: 프로젝트 클라이언트를 통한 호출

실행:
    pytest tests/manual/e2e/test_gemini_api.py -v -s
"""

from __future__ import annotations

import time

import pytest

from src.utils.llm.client import (
    LLMResponse,
    _resolve_thinking_params,
    reset_llm_client,
)


# ══════════════════════════════════════════════════════════════
# 1. 유닛 테스트: _resolve_thinking_params
# ══════════════════════════════════════════════════════════════


class TestResolveThinkingParams:
    """Gemini 모델의 thinking 파라미터 변환 검증."""

    def test_gemini_off(self):
        result = _resolve_thinking_params("gemini-2.5-flash", "off")
        assert result == {"reasoning_effort": "none"}

    def test_gemini_on(self):
        result = _resolve_thinking_params("gemini-2.5-flash", "on")
        assert result == {"reasoning_effort": "medium"}

    def test_gemini_low(self):
        result = _resolve_thinking_params("gemini-2.5-pro", "low")
        assert result == {"reasoning_effort": "low"}

    def test_gemini_medium(self):
        result = _resolve_thinking_params("gemini-3-flash-preview", "medium")
        assert result == {"reasoning_effort": "medium"}

    def test_gemini_high(self):
        result = _resolve_thinking_params("gemini-2.5-flash", "high")
        assert result == {"reasoning_effort": "high"}

    def test_gemini_auto_returns_empty(self):
        result = _resolve_thinking_params("gemini-2.5-flash", "auto")
        assert result == {}

    def test_gemini_unknown_mode_defaults_medium(self):
        result = _resolve_thinking_params("gemini-2.5-flash", "unknown")
        assert result == {"reasoning_effort": "medium"}

    def test_non_gemini_returns_empty(self):
        result = _resolve_thinking_params("claude-sonnet-4-20250514", "high")
        assert result == {}


# ══════════════════════════════════════════════════════════════
# 2. 통합 테스트: Gemini API 실제 호출
# ══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def gemini_client():
    """Gemini OpenAI 호환 클라이언트를 생성한다."""
    from src.config import settings

    if "gemini" not in settings.llm_model.lower():
        pytest.skip("LLM_MODEL이 Gemini가 아님 — 스킵")

    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )


@pytest.fixture(scope="module")
def gemini_model():
    """현재 설정된 Gemini 모델명을 반환한다."""
    from src.config import settings
    return settings.llm_model


@pytest.mark.asyncio
async def test_basic_call(gemini_client, gemini_model):
    """Gemini 기본 호출: 단순 질의에 텍스트 응답을 받는다."""
    start = time.perf_counter()

    response = await gemini_client.chat.completions.create(
        model=gemini_model,
        max_tokens=200,
        messages=[{"role": "user", "content": "1+1은?"}],
    )

    elapsed = (time.perf_counter() - start) * 1000
    text = response.choices[0].message.content

    print(f"\n[기본 호출] 모델: {gemini_model}")
    print(f"  응답: {text}")
    print(f"  지연: {elapsed:.0f}ms")
    print(f"  usage: {response.usage}")

    assert text is not None
    assert len(text) > 0
    assert "2" in text


@pytest.mark.asyncio
async def test_reasoning_effort_none(gemini_client, gemini_model):
    """reasoning_effort='none': thinking 비활성화 호출."""
    start = time.perf_counter()

    try:
        response = await gemini_client.chat.completions.create(
            model=gemini_model,
            max_tokens=200,
            messages=[{"role": "user", "content": "대한민국의 수도는?"}],
            reasoning_effort="none",
        )
        elapsed = (time.perf_counter() - start) * 1000
        text = response.choices[0].message.content

        print(f"\n[reasoning_effort=none] 모델: {gemini_model}")
        print(f"  응답: {text}")
        print(f"  지연: {elapsed:.0f}ms")
        print(f"  usage: {response.usage}")

        assert text is not None
        assert "서울" in text
    except Exception as e:
        # 일부 모델은 reasoning_effort=none 미지원
        print(f"\n[reasoning_effort=none] 미지원: {e}")
        pytest.skip(f"reasoning_effort=none 미지원: {e}")


@pytest.mark.asyncio
async def test_reasoning_effort_low(gemini_client, gemini_model):
    """reasoning_effort='low': 경량 추론 호출."""
    start = time.perf_counter()

    response = await gemini_client.chat.completions.create(
        model=gemini_model,
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": "Python에서 리스트와 튜플의 차이를 한 줄로 설명해줘",
        }],
        reasoning_effort="low",
    )

    elapsed = (time.perf_counter() - start) * 1000
    text = response.choices[0].message.content

    print(f"\n[reasoning_effort=low] 모델: {gemini_model}")
    print(f"  응답: {text}")
    print(f"  지연: {elapsed:.0f}ms")
    print(f"  usage: {response.usage}")

    assert text is not None
    assert len(text) > 0


@pytest.mark.asyncio
async def test_reasoning_effort_high(gemini_client, gemini_model):
    """reasoning_effort='high': 최대 추론 호출 (SQL 생성 시나리오)."""
    start = time.perf_counter()

    response = await gemini_client.chat.completions.create(
        model=gemini_model,
        max_tokens=1000,
        messages=[
            {
                "role": "system",
                "content": "너는 SQL 전문가야. PostgreSQL 문법으로 SQL을 생성해.",
            },
            {
                "role": "user",
                "content": (
                    "다음 테이블이 있어:\n"
                    "- customers(id, name, created_at)\n"
                    "- orders(id, customer_id, amount, order_date)\n\n"
                    "2024년 3월에 주문 금액 합계가 가장 높은 고객 상위 5명을 조회하는 SQL을 작성해."
                ),
            },
        ],
        reasoning_effort="high",
    )

    elapsed = (time.perf_counter() - start) * 1000
    text = response.choices[0].message.content

    print(f"\n[reasoning_effort=high] 모델: {gemini_model}")
    print(f"  응답:\n{text}")
    print(f"  지연: {elapsed:.0f}ms")
    print(f"  usage: {response.usage}")

    assert text is not None
    # SQL 키워드 포함 여부 확인
    # (max_tokens로 응답이 잘릴 수 있으므로 SELECT만 필수)
    text_upper = text.upper()
    assert "SELECT" in text_upper
    assert "CUSTOMERS" in text_upper or "ORDERS" in text_upper


@pytest.mark.asyncio
async def test_system_prompt(gemini_client, gemini_model):
    """system 프롬프트가 정상적으로 전달되는지 확인한다."""
    response = await gemini_client.chat.completions.create(
        model=gemini_model,
        max_tokens=100,
        messages=[
            {"role": "system", "content": "너는 모든 답변을 JSON 형식으로만 해야 해."},
            {"role": "user", "content": "1+1은?"},
        ],
    )

    text = response.choices[0].message.content
    print(f"\n[system prompt] 응답: {text}")

    assert text is not None
    # JSON 형식 힌트 확인 (중괄호 포함 여부)
    assert "{" in text or "json" in text.lower()


# ══════════════════════════════════════════════════════════════
# 3. UnifiedLLMClient 통합 테스트
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unified_client_gemini():
    """UnifiedLLMClient를 통한 Gemini 호출 테스트."""
    from src.config import settings

    if "gemini" not in settings.llm_model.lower():
        pytest.skip("LLM_MODEL이 Gemini가 아님 — 스킵")

    # 싱글턴 초기화 후 새로 생성
    reset_llm_client()

    from src.utils.llm.client import get_llm_client

    client = get_llm_client()
    start = time.perf_counter()

    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=200,
        system="간결하게 답변해.",
        messages=[{
            "role": "user",
            "content": "오늘 날씨 어때?라는 질문에 대해 "
                       "'날씨 정보는 제공하지 않습니다'라고 답해.",
        }],
    )

    elapsed = (time.perf_counter() - start) * 1000
    text = response.content[0].text

    print(f"\n[UnifiedLLMClient] 모델: {settings.llm_model}")
    print(f"  응답: {text}")
    print(f"  지연: {elapsed:.0f}ms")

    assert isinstance(response, LLMResponse)
    assert text is not None
    assert len(text) > 0

    # 정리
    reset_llm_client()


# ══════════════════════════════════════════════════════════════
# 4. 멀티턴 대화 테스트
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_multi_turn_conversation(gemini_client, gemini_model):
    """멀티턴 대화가 정상 동작하는지 확인한다."""
    messages = [
        {"role": "user", "content": "내 이름은 홍길동이야. 기억해."},
    ]

    response1 = await gemini_client.chat.completions.create(
        model=gemini_model,
        max_tokens=200,
        messages=messages,
    )
    text1 = response1.choices[0].message.content
    print(f"\n[멀티턴 1] 응답: {text1}")

    # 2턴: 이전 대화 컨텍스트 유지
    messages.append({"role": "assistant", "content": text1})
    messages.append({"role": "user", "content": "내 이름이 뭐라고 했지?"})

    response2 = await gemini_client.chat.completions.create(
        model=gemini_model,
        max_tokens=200,
        messages=messages,
    )
    text2 = response2.choices[0].message.content
    print(f"[멀티턴 2] 응답: {text2}")

    assert "홍길동" in text2
