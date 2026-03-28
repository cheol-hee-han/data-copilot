"""대화 이력 해소 → 의도 분류 연쇄 테스트 스크립트.

history_resolver(이력 해소) → intent_resolver(의도 분류) 두 단계를
연속 실행하여, 후속 질의가 재작성된 후 올바른 의도로 분류되는지 확인한다.

대화형 모드에서 멀티턴 시뮬레이션을 지원한다:
    - 대화 이력이 자동으로 축적되며, 매 턴마다 이력 해소 → 의도 분류 실행
    - /reset 으로 대화 초기화, /history 로 현재 이력 확인
    - 시스템 응답은 임의 텍스트로 시뮬레이션 (실제 SQL 실행 없음)

사용법:
    # 단일 질의 (이력 없이)
    uv run python tests/manual/test_history_to_intent.py "이번 달 신규 고객 수"

    # 대화형 멀티턴 시뮬레이션
    uv run python tests/manual/test_history_to_intent.py

    # LLM 원본 응답까지 출력
    uv run python tests/manual/test_history_to_intent.py -v

    # 이력 해소만 (의도 분류 스킵)
    uv run python tests/manual/test_history_to_intent.py --resolve-only

출력 예시 (대화형):
    [턴 1] 질의> 이번 달 신규 고객 수 알려줘
    ────────────────────────────────────────
    [이력 해소] 스킵 (독립 질의)
    [의도 분류] DATA_EXTRACTION (HIGH, 0.95)
      근거: 비즈니스 엔티티(고객) + 수치(수) + 조회 동사(알려줘)
    ════════════════════════════════════════
    시스템 응답 시뮬레이션> 이번 달 신규 고객은 1,234명입니다.

    [턴 2] 질의> 그 중에서 VIP 등급은?
    ────────────────────────────────────────
    [이력 해소] 재작성 완료 (지시대명사/참조 표현 감지)
      원본: 그 중에서 VIP 등급은?
      재작성: 이번 달 신규 고객 중 VIP 등급 고객 수 알려줘
    [의도 분류] DATA_EXTRACTION (HIGH, 0.95)
      근거: ...
    ════════════════════════════════════════

필요 환경:
    - .env에 LLM 설정 필요 (ANTHROPIC_API_KEY 또는 OPENAI_API_KEY)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


# ──────────────────────────────────────────────────────────────
# 이력 해소 + 의도 분류 연쇄 실행
# ──────────────────────────────────────────────────────────────

async def run_resolve_and_classify(
    query: str,
    conversation_history: list[dict[str, str]],
    *,
    verbose: bool = False,
    resolve_only: bool = False,
) -> dict:
    """이력 해소 → 의도 분류를 순차 실행하고 결과를 출력한다.

    Returns:
        {"resolved_query": str, "intent": str, ...} 결과 dict
    """
    from src.agents.nodes.prompts.system_prompts import (
        HISTORY_RESOLVE,
        HISTORY_RESOLVE_USER,
        INTENT_GATE,
        INTENT_GATE_USER,
    )
    from src.config import settings
    from src.services.history_resolver import resolve_history
    from src.services.intent_resolver import (
        _map_category_to_intent,
        _parse_gate_response,
        get_intent_label,
    )
    from src.utils.llm import get_llm_client

    result_info: dict = {"original_query": query}

    if verbose:
        print(f"[LLM 모델]  {settings.llm_model}")
        if conversation_history:
            print(f"[이력 턴수]  {len(conversation_history)}턴")

    # ── Step 1: 이력 해소 ──
    resolve_result = await resolve_history(
        query,
        conversation_history,
        system_prompt=HISTORY_RESOLVE,
        user_template=HISTORY_RESOLVE_USER,
    )

    resolved_query = resolve_result.resolved_query
    result_info["resolved_query"] = resolved_query
    result_info["was_rewritten"] = resolve_result.was_rewritten

    if resolve_result.was_rewritten:
        print(f"[이력 해소] 재작성 완료 ({resolve_result.reason})")
        print(f"  원본:   {query}")
        print(f"  재작성: {resolved_query}")
    else:
        reason = resolve_result.reason or "독립 질의"
        print(f"[이력 해소] 스킵 ({reason})")

    if resolve_only:
        return result_info

    # ── Step 2: 의도 분류 (Intent Gate) ──
    user_prompt = INTENT_GATE_USER.format(query=resolved_query)

    if verbose:
        print(f"\n{'─'*40}")
        print("[Gate 시스템 프롬프트] (앞 300자)")
        print(INTENT_GATE[:300])
        print(f"\n[Gate 유저 프롬프트]")
        print(user_prompt)

    print("[의도 분류] LLM 호출 중...")

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
            return result_info
        raw = response.content[0].text
    except Exception as e:
        print(f"[오류] LLM 호출 실패: {e}")
        return result_info

    if verbose:
        print(f"\n[LLM 원본 응답]")
        print(raw)

    gate_result = _parse_gate_response(raw)
    category = gate_result.get("category", "AMBIGUOUS")
    confidence_str = gate_result.get("confidence", "MEDIUM")
    reason = gate_result.get("reason", "")

    intent, confidence = _map_category_to_intent(
        category, confidence_str,
    )
    label = get_intent_label(intent)

    print(f"[의도 분류] {category} ({confidence_str}, {confidence:.2f})")
    print(f"  최종 의도: {label}")
    print(f"  근거: {reason}")

    result_info.update({
        "category": category,
        "confidence": confidence,
        "intent": intent.value,
        "intent_label": label,
        "reason": reason,
    })

    if verbose:
        print(f"\n  Gate JSON: {json.dumps(gate_result, ensure_ascii=False)}")

    return result_info


# ──────────────────────────────────────────────────────────────
# 대화형 멀티턴 시뮬레이션
# ──────────────────────────────────────────────────────────────

def interactive_mode(
    verbose: bool = False,
    resolve_only: bool = False,
) -> None:
    """대화형 멀티턴 시뮬레이션.

    매 턴마다 이력 해소 → 의도 분류를 실행하고,
    시스템 응답을 수동 입력하여 대화 이력을 축적한다.
    """
    v_tag = " + verbose" if verbose else ""
    r_tag = " (resolve-only)" if resolve_only else ""
    print(f"\n이력 해소 → 의도 분류 테스트 [{r_tag}{v_tag}]")
    print("명령어: /reset (대화 초기화), /history (이력 확인), q (종료)\n")

    history: list[dict[str, str]] = []
    turn = 0

    while True:
        turn += 1

        # 사용자 질의 입력
        try:
            query = input(f"[턴 {turn}] 질의> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료")
            break

        if not query:
            turn -= 1
            continue
        if query.lower() in ("q", "quit", "exit"):
            print("종료")
            break
        if query == "/reset":
            history.clear()
            turn = 0
            print("대화 이력이 초기화되었습니다.\n")
            continue
        if query == "/history":
            if not history:
                print("(이력 없음)\n")
            else:
                for i, h in enumerate(history):
                    role = "사용자" if h["role"] == "user" else "시스템"
                    print(f"  [{i+1}] {role}: {h['content'][:80]}")
                print()
            turn -= 1
            continue

        # 이력 해소 + 의도 분류 실행
        print(f"{'─'*40}")
        result = asyncio.run(
            run_resolve_and_classify(
                query,
                history,
                verbose=verbose,
                resolve_only=resolve_only,
            ),
        )
        print(f"{'═'*40}")

        # 대화 이력에 사용자 질의 추가
        history.append({"role": "user", "content": query})

        # 시스템 응답 시뮬레이션
        try:
            sim_response = input("시스템 응답 시뮬레이션> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료")
            break

        if sim_response.lower() in ("q", "quit", "exit"):
            print("종료")
            break
        if sim_response:
            history.append(
                {"role": "assistant", "content": sim_response},
            )
        else:
            # 빈 응답이면 기본 응답 생성
            default = f"(턴 {turn} 응답 시뮬레이션)"
            history.append(
                {"role": "assistant", "content": default},
            )

        print()


# ──────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────

def main() -> None:
    """메인 엔트리포인트."""
    args = sys.argv[1:]

    verbose = "--verbose" in args or "-v" in args
    if "--verbose" in args:
        args.remove("--verbose")
    if "-v" in args:
        args.remove("-v")

    resolve_only = "--resolve-only" in args
    if resolve_only:
        args.remove("--resolve-only")

    if args:
        # 단일 질의 (이력 없이)
        query = " ".join(args)
        print(f"\n{'═'*40}")
        print(f"[원본 질의] {query}")
        print(f"{'─'*40}")
        asyncio.run(
            run_resolve_and_classify(
                query,
                [],
                verbose=verbose,
                resolve_only=resolve_only,
            ),
        )
        print(f"{'═'*40}\n")
    else:
        interactive_mode(
            verbose=verbose,
            resolve_only=resolve_only,
        )


if __name__ == "__main__":
    main()
