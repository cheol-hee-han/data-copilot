"""입력 → 전처리 → 이력 해소 → 의도 분류 → 질의 정규화 연쇄 매뉴얼 테스트.

파이프라인 앞단 4개 노드를 순차 실행하여 각 단계의 결과를 확인한다.
대화형 멀티턴 시뮬레이션을 지원하며, 대화 이력이 자동으로 축적된다.

사용법:
    # 단일 질의 (이력 없이)
    uv run python tests/manual/test_input_to_normalization.py "이번 달 신규 고객 수 알려줘"

    # 대화형 멀티턴 시뮬레이션
    uv run python tests/manual/test_input_to_normalization.py

    # 상세 출력 (LLM 원본 응답 포함)
    uv run python tests/manual/test_input_to_normalization.py -v

명령어 (대화형 모드):
    /reset   — 대화 이력 초기화
    /history — 현재 대화 이력 확인
    q        — 종료

필요 환경:
    .env에 LLM 설정 필요 (ANTHROPIC_API_KEY 또는 OPENAI_API_KEY)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def run_pipeline_front(
    query: str,
    history: list[dict[str, str]],
    *,
    verbose: bool = False,
    awaiting_clarification: bool = False,
) -> dict:
    """전처리 → 이력 해소 → 의도 분류 → 질의 정규화를 순차 실행한다."""
    from src.agents.state.state import PipelineState, QueryStatus
    from src.services.input_sanitizer import sanitize
    from src.agents.nodes.interpret.history_resolver import resolve_history_node
    from src.agents.nodes.interpret.intent_classifier import classify_intent_node
    from src.agents.nodes.interpret.query_normalizer import query_normalizer_node
    from src.services.history_resolver import HistoryDecision
    from src.config import settings

    result_info: dict = {"original": query}

    if verbose:
        print(f"[LLM 모델] {settings.llm_model}")
        print(f"[이력 턴수] {len(history)}턴")
        if awaiting_clarification:
            print("[상태] 명확화 응답 대기 중")

    # ── Step 1: 전처리 ──
    print("\n[1/4] 전처리 (preprocess)")
    state = PipelineState(
        user_input=query,
        conversation_history=history,
        awaiting_clarification=awaiting_clarification,
    )
    san = sanitize(state.user_input)

    if san.is_error:
        print(f"  ERROR: {san.error_message}")
        result_info["error"] = san.error_message
        return result_info

    preprocessed = san.text
    print(f"  입력: {query}")
    print(f"  출력: {preprocessed}")
    state = state.model_copy(update={
        "preprocessed_input": san.text,
        "status": QueryStatus.PREPROCESSING,
    })

    # ── Step 2: 이력 해소 ──
    print("\n[2/4] 이력 해소 (resolve_history)")
    resolve_result = await resolve_history_node(state)
    state = state.model_copy(update=resolve_result)

    # UNSURE → 명확화 질문으로 종료
    if state.status == QueryStatus.AWAITING_CLARIFICATION:
        question = resolve_result.get("clarification_question", "")
        print(f"  판정: UNSURE → 명확화 질문 생성")
        print(f"  질문: {question}")
        result_info["decision"] = "UNSURE"
        result_info["clarification_question"] = question
        result_info["awaiting"] = True
        return result_info

    resolved = state.preprocessed_input
    was_rewritten = resolved != preprocessed
    decision = "CONTINUE" if was_rewritten else "NEW/SKIP"
    print(f"  판정: {decision}")
    if was_rewritten:
        print(f"  재작성: {resolved}")
    else:
        print(f"  원본 유지: {resolved}")
    result_info["decision"] = decision
    result_info["resolved_query"] = resolved

    # ── Step 3: 의도 분류 ──
    print("\n[3/4] 의도 분류 (classify_intent)")
    intent_result = await classify_intent_node(state)
    state = state.model_copy(update=intent_result)

    intent = state.intent
    confidence = state.intent_confidence
    category = state.query_category
    print(f"  카테고리: {category}")
    print(f"  의도: {intent.value} (신뢰도 {confidence:.2f})")
    result_info["category"] = category
    result_info["intent"] = intent.value
    result_info["confidence"] = confidence

    # 의도 분류에서 명확화 필요로 판단되면 여기서 종료
    from src.models.enums import IntentType
    if intent in (
        IntentType.CLARIFICATION_NEEDED,
        IntentType.CASUAL_TALK,
        IntentType.META_QUESTION,
    ):
        print(f"  → 명확화/비데이터 의도, 정규화 스킵")
        result_info["normalized"] = False
        return result_info

    # ── Step 4: 질의 정규화 ──
    if not settings.normalization_enabled:
        print("\n[4/4] 질의 정규화 (스킵 — normalization_enabled=False)")
        result_info["normalized"] = False
        return result_info

    print("\n[4/4] 질의 정규화 (normalize_query)")
    norm_result = await query_normalizer_node(state)

    nq = norm_result.get("normalized_query")
    if nq:
        print(f"  의도(intent): {nq.intent.primary}")
        if nq.intent.secondary:
            print(f"  부가 의도: {nq.intent.secondary}")
        if nq.entities:
            for e in nq.entities:
                norm = f" → {e.normalized_term}" if e.normalized_term else ""
                print(f"  엔티티: {e.term}{norm} ({e.type})")
        if nq.measures:
            for m in nq.measures:
                print(f"  측정값: {m.term} (agg={m.agg_function})")
        if nq.dimensions:
            for d in nq.dimensions:
                print(f"  차원: {d.term} (role={d.role})")
        print(f"  시간: {nq.time.type}")
        if nq.search_keywords and nq.search_keywords.meta_search:
            print(f"  검색 키워드: {nq.search_keywords.meta_search}")
        if nq.ambiguities:
            print(f"  모호성: {nq.ambiguities}")

        result_info["normalized"] = True
        result_info["norm_intent"] = nq.intent.primary
    else:
        print("  정규화 실패 (기본값 사용)")
        result_info["normalized"] = False

    return result_info


def interactive_mode(verbose: bool = False) -> None:
    """대화형 멀티턴 시뮬레이션."""
    v_tag = " + verbose" if verbose else ""
    print(f"\n입력 → 정규화 파이프라인 테스트 [{v_tag}]")
    print("명령어: /reset, /history, q\n")

    history: list[dict[str, str]] = []
    awaiting = False
    turn = 0

    while True:
        turn += 1
        try:
            query = input(f"[턴 {turn}] 질의> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료")
            break

        if not query:
            turn -= 1
            continue
        if query.lower() in ("q", "quit", "exit"):
            break
        if query == "/reset":
            history.clear()
            awaiting = False
            turn = 0
            print("대화 이력 초기화 완료.\n")
            continue
        if query == "/history":
            if not history:
                print("(이력 없음)\n")
            else:
                for i, h in enumerate(history, 1):
                    role = "사용자" if h["role"] == "user" else "시스템"
                    print(f"  {i}. [{role}] {h['content'][:80]}")
                print()
            turn -= 1
            continue

        # 파이프라인 앞단 실행
        print(f"\n{'='*60}")
        result = asyncio.run(
            run_pipeline_front(
                query, history,
                verbose=verbose,
                awaiting_clarification=awaiting,
            ),
        )
        print(f"{'='*60}")

        # 이력에 사용자 입력 추가
        history.append({"role": "user", "content": query})

        # UNSURE → 명확화 질문 출력, 다음 턴에서 awaiting=True
        if result.get("awaiting"):
            awaiting = True
            history.append({
                "role": "assistant",
                "content": result.get("clarification_question", ""),
            })
            print()
            continue

        awaiting = False

        # 시스템 응답 시뮬레이션
        try:
            sim = input("시스템 응답 시뮬레이션> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료")
            break

        if sim.lower() in ("q", "quit"):
            break
        if sim:
            history.append({"role": "assistant", "content": sim})
        else:
            history.append({
                "role": "assistant",
                "content": f"(턴 {turn} 응답)",
            })
        print()


def main() -> None:
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    if "--verbose" in args:
        args.remove("--verbose")
    if "-v" in args:
        args.remove("-v")

    if args:
        query = " ".join(args)
        print(f"\n{'='*60}")
        asyncio.run(
            run_pipeline_front(query, [], verbose=verbose),
        )
        print(f"{'='*60}\n")
    else:
        interactive_mode(verbose=verbose)


if __name__ == "__main__":
    main()
