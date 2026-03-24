"""자연어 질의를 직접 입력하여 의도 분류 결과를 확인하는 수동 테스트 스크립트.

intent_resolver.py의 classify_with_gate(Intent Gate) 및 classify_legacy(Legacy)
두 분류 경로를 대화형으로 호출하여, 분류 결과를 비교 확인한다.

세 가지 모드를 지원한다:
    - gate 모드(기본): Intent Gate 5-Category 분류 + DATA_QUERY 세분류
    - legacy 모드: 기존 텍스트 파싱 기반 4-Category 분류
    - both 모드: 두 경로를 모두 호출하여 결과 비교

사용법:
    # Intent Gate 분류 (기본)
    uv run python tests/manual/test_intent_interactive.py "이번 달 신규 고객 수 알려줘"

    # Legacy 분류
    uv run python tests/manual/test_intent_interactive.py --legacy "이번 달 신규 고객 수 알려줘"

    # 두 경로 비교
    uv run python tests/manual/test_intent_interactive.py --both "이번 달 신규 고객 수 알려줘"

    # LLM 원본 응답까지 출력 (디버깅용)
    uv run python tests/manual/test_intent_interactive.py -v "안녕하세요"

    # LLM 없이 세분류 규칙만 확인
    uv run python tests/manual/test_intent_interactive.py --dry-run "지점별 대출 추이 분석해줘"

    # 대화형 모드
    uv run python tests/manual/test_intent_interactive.py

출력 예시 (gate):
    ============================================================
    [원본 질의] 지점별 대출 추이 분석해줘
    [Gate 분류]
      카테고리:    DATA_QUERY (HIGH)
      세분류:      DATA_ANALYSIS
      근거:        비즈니스 엔티티(대출)와 분석 요청 동사(분석해줘) 포함
      최종 의도:   데이터 분석 (0.95)
    ============================================================

출력 예시 (dry-run):
    ============================================================
    [원본 질의] 지점별 대출 추이 분석해줘
    [세분류 규칙 판정]
      분석 신호어 매칭: "추이", "분석"
      판정: DATA_ANALYSIS
    ============================================================

필요 환경:
    - gate/legacy/both 모드: .env에 LLM 설정 필요
    - dry-run 모드: 외부 의존성 없음
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.intent_resolver import (
    subclassify_data_query,
    get_intent_label,
)


# ──────────────────────────────────────────────────────────────
# dry-run: LLM 없이 세분류 규칙만 확인
# ──────────────────────────────────────────────────────────────

# DATA_QUERY 세분류에 사용하는 분석 신호어 (intent_resolver.py와 동일)
_ANALYSIS_SIGNALS = {
    "분석", "추이", "트렌드", "비교", "대비",
    "증감", "변화", "통계", "상관", "예측",
}


def show_dry_run(query: str) -> None:
    """LLM 없이 세분류 규칙(분석 신호어 매칭)만 확인한다."""
    print(f"\n{'='*60}")
    print(f"[원본 질의] {query}")

    matched = [s for s in _ANALYSIS_SIGNALS if s in query]
    if matched:
        print("[세분류 규칙 판정]")
        print(f"  분석 신호어 매칭: {', '.join(repr(s) for s in matched)}")
        print("  판정: DATA_ANALYSIS")
    else:
        print("[세분류 규칙 판정]")
        print("  분석 신호어 매칭: (없음)")
        print("  판정: DATA_EXTRACTION")

    print(f"{'='*60}\n")


# ──────────────────────────────────────────────────────────────
# Intent Gate 분류
# ──────────────────────────────────────────────────────────────

async def run_gate_test(
    query: str,
    *,
    verbose: bool = False,
) -> None:
    """Intent Gate 경로로 의도를 분류하고 결과를 출력한다.

    classify_with_gate 내부 흐름을 단계별로 호출하여,
    verbose 모드에서 LLM 프롬프트와 원본 응답을 함께 출력한다.
    """
    from src.agents.nodes.prompts.system_prompts import (
        INTENT_GATE,
        INTENT_GATE_USER,
    )
    from src.config import settings
    from src.services.intent_resolver import (
        _map_category_to_intent,
        _parse_gate_response,
    )
    from src.utils.llm import get_llm_client

    print(f"\n{'='*60}")
    print(f"[원본 질의] {query}")
    if verbose:
        print(f"[LLM 모델]  {settings.llm_model}")

    # ── Step 1: 프롬프트 조립 ──
    user_prompt = INTENT_GATE_USER.format(query=query)

    if verbose:
        print(f"\n{'─'*60}")
        print("[시스템 프롬프트] (앞 500자)")
        print(INTENT_GATE[:500])
        print(f"\n[유저 프롬프트]")
        print(user_prompt)

    # ── Step 2: LLM 호출 ──
    print("\nIntent Gate LLM 호출 중...")
    try:
        client = get_llm_client()
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=settings.llm_default_max_tokens,
            timeout=settings.llm_default_timeout,
            system=INTENT_GATE,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if not response.content:
            print("[오류] LLM 응답이 비어있습니다")
            print(f"{'='*60}\n")
            return
        raw = response.content[0].text
    except Exception as e:
        print(f"[오류] LLM 호출 실패: {e}")
        print(f"{'='*60}\n")
        return

    if verbose:
        print(f"\n{'─'*60}")
        print("[LLM 원본 응답]")
        print(raw)
        print(f"{'─'*60}")

    # ── Step 3: JSON 파싱 + 카테고리 매핑 ──
    gate_result = _parse_gate_response(raw)
    category = gate_result.get("category", "AMBIGUOUS")
    confidence_str = gate_result.get("confidence", "MEDIUM")
    reason = gate_result.get("reason", "")

    intent, confidence = _map_category_to_intent(
        category, confidence_str,
    )

    # ── Step 4: DATA_QUERY 세분류 ──
    sub_label = ""
    if category == "DATA_QUERY":
        intent, confidence = subclassify_data_query(
            query, confidence,
        )
        matched = [s for s in _ANALYSIS_SIGNALS if s in query]
        sub_label = (
            f"  세분류:      {intent.value.upper()}"
            f" (신호어: {matched or '없음'})"
        )

    # ── 결과 출력 ──
    label = get_intent_label(intent)

    print(f"\n{'─'*60}")
    print("[Gate 분류 결과]")
    print(f"{'─'*60}")
    print(f"  카테고리:    {category} ({confidence_str})")
    if sub_label:
        print(sub_label)
    print(f"  근거:        {reason}")
    print(f"  최종 의도:   {label} ({confidence:.2f})")

    if verbose:
        print(f"\n  Gate JSON:   {json.dumps(gate_result, ensure_ascii=False)}")

    print(f"{'='*60}\n")


# ──────────────────────────────────────────────────────────────
# Legacy 분류
# ──────────────────────────────────────────────────────────────

async def run_legacy_test(
    query: str,
    *,
    verbose: bool = False,
) -> None:
    """Legacy 경로로 의도를 분류하고 결과를 출력한다.

    llm_call_with_parse_retry를 통해 "INTENT:/CONFIDENCE:" 형식
    텍스트 응답을 파싱하는 기존 분류 방식을 테스트한다.
    """
    from src.agents.nodes.prompts.system_prompts import (
        INTENT_CLASSIFICATION,
        INTENT_FORMAT_HINT,
    )
    from src.config import settings
    from src.services.intent_resolver import classify_legacy

    print(f"\n{'='*60}")
    print(f"[원본 질의] {query}")
    if verbose:
        print(f"[LLM 모델]  {settings.llm_model}")
        print(f"\n{'─'*60}")
        print("[시스템 프롬프트] (앞 500자)")
        print(INTENT_CLASSIFICATION[:500])

    # ── LLM 호출 ──
    print("\nLegacy 분류 LLM 호출 중...")
    result = await classify_legacy(
        query,
        system_prompt=INTENT_CLASSIFICATION,
        format_hint=INTENT_FORMAT_HINT,
    )

    # ── 결과 출력 ──
    label = get_intent_label(result.intent)

    print(f"\n{'─'*60}")
    print("[Legacy 분류 결과]")
    print(f"{'─'*60}")
    print(f"  의도:        {result.intent.value} ({label})")
    print(f"  신뢰도:      {result.confidence:.2f}")
    if result.is_error:
        print(f"  오류:        {result.error_message}")
    print(f"{'='*60}\n")


# ──────────────────────────────────────────────────────────────
# Both 모드: 두 경로 비교
# ──────────────────────────────────────────────────────────────

async def run_both_test(
    query: str,
    *,
    verbose: bool = False,
) -> None:
    """Gate와 Legacy 두 경로를 모두 호출하여 결과를 비교한다."""
    print(f"\n{'='*60}")
    print(f"[비교 테스트] {query}")
    print(f"{'='*60}")

    await run_gate_test(query, verbose=verbose)
    await run_legacy_test(query, verbose=verbose)


# ──────────────────────────────────────────────────────────────
# 대화형 모드 + 메인
# ──────────────────────────────────────────────────────────────

def interactive_mode(
    mode: str = "gate",
    verbose: bool = False,
) -> None:
    """대화형 모드로 질의를 반복 입력받는다."""
    mode_labels = {
        "gate": "Intent Gate",
        "legacy": "Legacy",
        "both": "Gate + Legacy 비교",
        "dry-run": "dry-run (LLM 없음)",
    }
    v_tag = " + verbose" if verbose else ""
    print(f"\n의도 분류 테스트 대화형 모드 [{mode_labels[mode]}{v_tag}]")
    print("종료하려면 'q' 또는 'quit' 입력\n")

    runner_map = {
        "gate": lambda q: run_gate_test(q, verbose=verbose),
        "legacy": lambda q: run_legacy_test(q, verbose=verbose),
        "both": lambda q: run_both_test(q, verbose=verbose),
    }

    while True:
        try:
            query = input("질의> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료")
            break

        if not query:
            continue
        if query.lower() in ("q", "quit", "exit"):
            print("종료")
            break

        if mode == "dry-run":
            show_dry_run(query)
        else:
            asyncio.run(runner_map[mode](query))


def main() -> None:
    """메인 엔트리포인트."""
    args = sys.argv[1:]

    # 플래그 파싱
    dry_run = "--dry-run" in args
    if dry_run:
        args.remove("--dry-run")

    legacy = "--legacy" in args
    if legacy:
        args.remove("--legacy")

    both = "--both" in args
    if both:
        args.remove("--both")

    verbose = "--verbose" in args or "-v" in args
    if "--verbose" in args:
        args.remove("--verbose")
    if "-v" in args:
        args.remove("-v")

    # 모드 결정
    if dry_run:
        mode = "dry-run"
    elif both:
        mode = "both"
    elif legacy:
        mode = "legacy"
    else:
        mode = "gate"

    runner_map = {
        "gate": lambda q: run_gate_test(q, verbose=verbose),
        "legacy": lambda q: run_legacy_test(q, verbose=verbose),
        "both": lambda q: run_both_test(q, verbose=verbose),
    }

    if args:
        query = " ".join(args)
        if mode == "dry-run":
            show_dry_run(query)
        else:
            asyncio.run(runner_map[mode](query))
    else:
        interactive_mode(mode=mode, verbose=verbose)


if __name__ == "__main__":
    main()
