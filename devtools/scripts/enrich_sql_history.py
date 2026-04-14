"""SQL 수행이력 문서 보강 배치 스크립트.

LLM을 사용하여 sql_history의 description에 동의어·영어 표현·
관련 비즈니스 용어를 추가한다. 보강된 텍스트는 임베딩 대상이 되어
벡터 검색의 Recall을 대폭 향상시킨다.

전략 문서 참조: docs/strategy-proposals/embedding-search-strategy.md
  → "원칙 1: 모델보다 데이터 보강이 먼저"

사용법:
    # .env에 ANTHROPIC_API_KEY 또는 OPENAI_API_KEY 설정 필요
    python standalone/scripts/enrich_sql_history.py

    # 보강 후 Qdrant 재시딩
    python standalone/scripts/seed_qdrant.py

출력:
    standalone/scripts/enriched_sql_history.json
    (seed_qdrant.py가 이 파일을 자동 감지하여 사용)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path, encoding="utf-8")

# 배치 설정
BATCH_SIZE = 20  # LLM 동시 호출 수
OUTPUT_PATH = Path(__file__).resolve().parent / "enriched_sql_history.json"

ENRICHMENT_PROMPT = """\
다음 SQL 설명에 대해 동의어·유의어·영어 표현·관련 비즈니스 용어를 생성하세요.

원문: {description}

요구사항:
- 한국어 동의어/유의어 2~3개
- 영어 번역 및 유사 표현 2~3개
- 관련 금융/비즈니스 용어 1~2개
- 쉼표 구분 단일 라인으로 출력
- 원문을 반복하지 말 것

출력 예시: 사업부 분기 매출 현황, 팀별 분기 실적, \
quarterly revenue by department, division quarterly performance"""


def _get_llm_client():
    """LLM 클라이언트를 생성한다."""
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    if provider == "anthropic":
        import anthropic
        return "anthropic", anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
    # OpenAI Compatible (Groq, OpenRouter 등)
    import openai
    return "openai", openai.OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL", ""),
    )


def _call_anthropic(client, description: str) -> str:
    """Anthropic API로 문서 보강을 수행한다."""
    model = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
    response = client.messages.create(
        model=model,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": ENRICHMENT_PROMPT.format(
                description=description,
            ),
        }],
    )
    return response.content[0].text.strip()


def _call_openai(client, description: str) -> str:
    """OpenAI Compatible API로 문서 보강을 수행한다."""
    model = os.getenv("LLM_MODEL", "gpt-4o")
    response = client.chat.completions.create(
        model=model,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": ENRICHMENT_PROMPT.format(
                description=description,
            ),
        }],
    )
    return response.choices[0].message.content.strip()


def enrich_descriptions(
    sql_data: list[dict],
) -> list[dict]:
    """sql_history 데이터의 description을 LLM으로 보강한다."""
    provider, client = _get_llm_client()
    call_fn = (
        _call_anthropic if provider == "anthropic"
        else _call_openai
    )

    total = len(sql_data)
    enriched_count = 0
    error_count = 0

    for i, item in enumerate(sql_data):
        desc = item.get("description", "")
        if not desc:
            continue

        try:
            synonyms = call_fn(client, desc)
            item["enriched"] = f"{desc} | {synonyms}"
            enriched_count += 1
        except Exception as e:
            # 실패 시 원본 유지
            item["enriched"] = desc
            error_count += 1
            if error_count <= 5:
                print(f"  [오류] {i}: {e}")

        if (i + 1) % 100 == 0:
            print(
                f"  진행: {i + 1}/{total} "
                f"(성공 {enriched_count}, 실패 {error_count})"
            )
            # Rate limit 방지
            time.sleep(1)

    return sql_data


def main():
    """SQL 수행이력 문서를 LLM으로 보강하여 JSON 파일로 출력한다."""
    # 데이터 생성기 임포트
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from qdrant_data_generators import generate_sql_history_data

    print("SQL 수행이력 문서 보강 시작")
    print(f"  출력: {OUTPUT_PATH}")

    sql_data = generate_sql_history_data(10000)
    print(f"  데이터 생성: {len(sql_data)}건")

    start = time.time()
    enriched = enrich_descriptions(sql_data)
    elapsed = time.time() - start

    # 저장
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)

    enriched_count = sum(
        1 for d in enriched
        if d.get("enriched", "") != d.get("description", "")
    )
    print(f"\n보강 완료: {enriched_count}/{len(enriched)}건")
    print(f"소요 시간: {elapsed:.1f}초")
    print(f"저장: {OUTPUT_PATH}")
    print(
        "\n다음 단계: python standalone/scripts/seed_qdrant.py"
    )


if __name__ == "__main__":
    main()
