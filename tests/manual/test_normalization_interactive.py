"""자연어 질의를 직접 입력하여 8-Slot 정규화 결과를 확인하는 수동 테스트 스크립트.

query_normalizer.py의 run_normalization 파이프라인을 대화형으로 호출하여,
전처리(약어 확장) → Phase 1 LLM → 구조 검증 → Phase 2 LLM → 후처리
전 과정의 결과를 사람이 읽기 쉬운 형태로 출력한다.

두 가지 모드를 지원한다:
    - full 모드: LLM을 호출하여 8-Slot 전체 결과(intent, entities, measures,
      dimensions, filters, time, modifiers, output_hint)를 JSON으로 출력
    - dry-run 모드: LLM 없이 전처리(약어 확장)와 동의어 역매핑만 확인

사용법:
    # 단일 질의 테스트 (LLM 호출)
    uv run python tests/manual/test_normalization_interactive.py "이번 달 신규 고객 수 알려줘"
    uv run python tests/manual/test_normalization_interactive.py "이번달 신규 고객 을 가장 많이 유치한 점포 TOP 10 알려주고, 고객 수 까지 알려줘"
    
    # 여러 질의 연속 테스트 (인자 없이 실행하면 대화형 모드)
    uv run python tests/manual/test_normalization_interactive.py

    # LLM 원본 응답·프롬프트까지 출력 (디버깅용)
    uv run python tests/manual/test_normalization_interactive.py -v "이번 달 신규 고객 수 알려줘"
    uv run python tests/manual/test_normalization_interactive.py \
        --verbose "이번 달 신규 고객 수 알려줘"

    # 전처리·동의어 역매핑만 확인 (LLM 없이)
    uv run python tests/manual/test_normalization_interactive.py --dry-run "YoY 대출잔액 추이"

출력 예시 (dry-run):
    ============================================================
    [원본 질의] YoY 대출잔액 추이
    [전처리 후] 전년동기대비 대출잔액 추이
    [약어 확장]
      YoY → 전년동기대비
    [동의어 매칭]
      "대출잔액" → "여신잔액"
    ============================================================

출력 예시 (full):
    [의도(intent)]     TREND
    [엔티티(entities)]
      - 대출 → 여신 (DIRECT, HIGH)
    [측정값(measures)]
      - 대출잔액 → 여신잔액 (agg=SUM, HIGH)
    [시간(time)]       COMPARISON
    --- 전체 JSON ---
    { ... }

필요 환경:
    - full 모드: .env 파일에 LLM 설정 필요 (ANTHROPIC_API_KEY 또는 OPENAI_API_KEY)
    - dry-run 모드: 외부 의존성 없음
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.query_normalizer import (
    _preprocess_for_normalization,
)
from src.services.domain.domain_synonyms import (
    ABBREVIATION_MAP,
    build_reverse_lookup,
)


def show_dry_run(query: str) -> None:
    """LLM 없이 전처리·동의어 역매핑 결과만 출력한다."""
    print(f"\n{'='*60}")
    print(f"[원본 질의] {query}")

    # 1. 전처리 (약어 확장)
    preprocessed = _preprocess_for_normalization(query)
    if preprocessed != query:
        print(f"[전처리 후] {preprocessed}")
    else:
        print("[전처리 후] (변경 없음)")

    # 2. 약어 매칭 확인
    matched_abbrs = []
    for abbr, full in ABBREVIATION_MAP.items():
        if abbr.lower() in query.lower():
            matched_abbrs.append(f"  {abbr} → {full}")
    if matched_abbrs:
        print("[약어 확장]")
        for m in matched_abbrs:
            print(m)

    # 3. 동의어 역매핑 확인
    reverse = build_reverse_lookup()
    tokens = preprocessed.split()
    matched_synonyms = []
    for token in tokens:
        standard = reverse.get(token.lower())
        if standard:
            matched_synonyms.append(f"  \"{token}\" → \"{standard}\"")

    # 복합 용어도 체크 (2~3 토큰 조합)
    for i in range(len(tokens)):
        for j in range(i + 2, min(i + 4, len(tokens) + 1)):
            compound = " ".join(tokens[i:j])
            standard = reverse.get(compound.lower())
            if standard:
                matched_synonyms.append(
                    f"  \"{compound}\" → \"{standard}\""
                )

    if matched_synonyms:
        print("[동의어 매칭]")
        for m in matched_synonyms:
            print(m)
    else:
        print("[동의어 매칭] (매칭 없음)")

    print(f"{'='*60}\n")


async def run_normalization_test(
    query: str,
    *,
    verbose: bool = False,
) -> None:
    """실제 LLM을 호출하여 8-Slot 정규화 결과를 출력한다.

    각 단계(전처리 → Phase 1 LLM → 검증 → Phase 2 LLM → 후처리)를
    개별 호출하면서, --verbose 모드에서는 LLM raw 응답과 중간 결과를
    모두 출력한다.
    """
    from datetime import date

    from src.agents.models.normalization import NormalizedQuery
    from src.agents.nodes.prompts.system_prompts import (
        NORMALIZATION_PHASE1,
        NORMALIZATION_PHASE1_USER,
        NORMALIZATION_PHASE2,
        NORMALIZATION_PHASE2_USER,
    )
    from src.config import settings
    from src.services.domain.domain_synonyms import (
        get_output_template_prompt_text,
        get_synonym_prompt_text,
    )
    from src.services.query_normalizer import (
        _call_llm,
        _parse_llm_json,
        _postprocess,
        _validate_structure,
    )

    print(f"\n{'='*60}")
    print(f"[원본 질의] {query}")
    if verbose:
        print(f"[LLM 모델]  {settings.llm_model}")

    # ── Step 1: 전처리 ──
    cleaned = _preprocess_for_normalization(query)
    if verbose and cleaned != query:
        print(f"[전처리 후] {cleaned}")

    # ── Step 2: Phase 1 프롬프트 조립 ──
    today = date.today().isoformat()
    synonym_text = get_synonym_prompt_text()
    template_text = get_output_template_prompt_text()

    p1_system = NORMALIZATION_PHASE1.replace(
        "{output_template_text}", template_text,
    )
    p1_user_tpl = NORMALIZATION_PHASE1_USER or (
        "다음 자연어 질의를 8-Slot 구조로 분해하여 JSON으로 "
        "출력해 주세요.\n\n[입력 질의]\n{query}\n\n"
        "[오늘 날짜]\n{today}\n\n[동의어 사전]\n{synonym_dict}\n\n"
        "JSON만 출력하세요."
    )
    phase1_user = p1_user_tpl.format(
        query=cleaned,
        today=today,
        synonym_dict=synonym_text,
    )

    if verbose:
        print(f"\n{'─'*60}")
        print("[Phase 1 시스템 프롬프트] (앞 500자)")
        print(p1_system[:500])
        print(f"\n[Phase 1 유저 프롬프트] (앞 500자)")
        print(phase1_user[:500])

    # ── Step 3: Phase 1 LLM 호출 ──
    print("\nPhase 1 LLM 호출 중...")
    try:
        phase1_raw = await _call_llm(p1_system, phase1_user)
    except Exception as e:
        print(f"[오류] Phase 1 LLM 호출 실패: {e}")
        print(f"{'='*60}\n")
        return

    if verbose:
        print(f"\n{'─'*60}")
        print("[Phase 1 LLM 원본 응답]")
        print(phase1_raw)
        print(f"{'─'*60}")

    # ── Step 4: Phase 1 파싱 + 검증 ──
    try:
        phase1_data = _parse_llm_json(phase1_raw)
    except ValueError as e:
        print(f"[오류] Phase 1 JSON 파싱 실패: {e}")
        if verbose:
            print(f"  raw 응답: {phase1_raw[:300]}")
        print(f"{'='*60}\n")
        return

    phase1_data, errors1 = _validate_structure(phase1_data)
    phase1_data["original_query"] = query
    if errors1:
        print(f"[Phase 1 검증 오류] {errors1}")

    if verbose:
        print(f"\n[Phase 1 검증 후 JSON]")
        print(json.dumps(phase1_data, ensure_ascii=False, indent=2))

    # ── Step 5: Phase 2 LLM 호출 (설정에 따라 스킵) ──
    if settings.normalization_phase2_enabled:
        p2_user_tpl = NORMALIZATION_PHASE2_USER or (
            "아래는 원본 질의와 Phase 1에서 생성된 정규화 JSON입니다.\n"
            "교차 검증 규칙 R1~R12를 모두 적용하여 "
            "수정된 JSON을 출력해 주세요.\n\n"
            "[원본 질의]\n{query}\n\n"
            "[Phase 1 결과 JSON]\n{phase1_json}\n\n"
            "JSON만 출력하세요."
        )
        phase1_json_str = json.dumps(
            phase1_data, ensure_ascii=False, indent=2,
        )
        phase2_user = p2_user_tpl.format(
            query=cleaned,
            phase1_json=phase1_json_str,
        )

        if verbose:
            print(f"\n{'─'*60}")
            print("[Phase 2 유저 프롬프트] (앞 500자)")
            print(phase2_user[:500])

        print("\nPhase 2 LLM 호출 중...")
        try:
            phase2_raw = await _call_llm(
                NORMALIZATION_PHASE2, phase2_user,
            )
        except Exception as e:
            print(f"[오류] Phase 2 LLM 호출 실패: {e}")
            print("Phase 1 결과로 계속 진행합니다.")
            phase2_raw = None

        if phase2_raw:
            if verbose:
                print(f"\n{'─'*60}")
                print("[Phase 2 LLM 원본 응답]")
                print(phase2_raw)
                print(f"{'─'*60}")

            try:
                final_data = _parse_llm_json(phase2_raw)
                final_data, errors2 = _validate_structure(final_data)
                final_data["original_query"] = query
                if errors2:
                    print(f"[Phase 2 검증 오류] {errors2}")
            except ValueError as e:
                print(f"[오류] Phase 2 JSON 파싱 실패: {e}")
                print("Phase 1 결과로 계속 진행합니다.")
                final_data = phase1_data
        else:
            final_data = phase1_data
    else:
        print("(Phase 2 스킵 — 설정에 의해 비활성화)")
        final_data = phase1_data

    # ── Step 6: 후처리 ──
    final_data = _postprocess(final_data)

    # ── Step 7: 모델 검증 + 출력 ──
    try:
        result = NormalizedQuery.model_validate(final_data)
    except Exception as e:
        print(f"[오류] NormalizedQuery 검증 실패: {e}")
        print(json.dumps(final_data, ensure_ascii=False, indent=2))
        print(f"{'='*60}\n")
        return

    _print_result(result)
    print(f"{'='*60}\n")


def _print_result(result) -> None:
    """NormalizedQuery 결과를 사람이 읽기 쉬운 형태로 출력한다."""
    print(f"\n{'─'*60}")
    print("[정규화 결과]")
    print(f"{'─'*60}")

    print(f"[의도(intent)]     {result.intent.primary}")
    if result.intent.secondary:
        print(f"  secondary:       {result.intent.secondary}")

    if result.entities:
        print("[엔티티(entities)]")
        for e in result.entities:
            norm = f" → {e.normalized_term}" if e.normalized_term else ""
            print(f"  - {e.term}{norm} ({e.type}, {e.confidence})")

    if result.measures:
        print("[측정값(measures)]")
        for m in result.measures:
            norm = f" → {m.normalized_term}" if m.normalized_term else ""
            print(
                f"  - {m.term}{norm} "
                f"(agg={m.agg_function}, {m.confidence})"
            )

    if result.dimensions:
        print("[차원(dimensions)]")
        for d in result.dimensions:
            print(
                f"  - {d.term} "
                f"(role={d.role}, gran={d.granularity})"
            )

    if result.filters:
        print("[필터(filters)]")
        for f in result.filters:
            print(
                f"  - {f.field} {f.filter_type} {f.value}"
            )

    print(f"[시간(time)]       {result.time.type}")
    if result.time.base_period:
        bp = result.time.base_period
        print(
            f"  base_period:     {bp.start} ~ {bp.end} "
            f"({bp.resolve})"
        )

    if result.modifiers:
        print("[수정자(modifiers)]")
        for mod in result.modifiers:
            print(f"  - {mod.type}: {mod.dict()}")

    print(
        f"[출력힌트]         "
        f"format={result.output_hint.format}"
    )
    if result.output_hint.doc_type:
        print(f"  doc_type:        {result.output_hint.doc_type}")

    if result.ambiguities:
        print("[모호성(ambiguities)]")
        for a in result.ambiguities:
            print(f"  - {a}")

    if result.search_keywords:
        sk = result.search_keywords
        if sk.meta_search:
            print(f"[검색 키워드]      {sk.meta_search}")
        if sk.sql_history_search:
            print(f"[SQL이력 검색]     {sk.sql_history_search}")

    # 전체 JSON
    result_dict = result.model_dump(exclude_none=True)
    print(f"\n--- 전체 JSON ---")
    print(
        json.dumps(
            result_dict, ensure_ascii=False, indent=2,
        )
    )


def interactive_mode(
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """대화형 모드로 질의를 반복 입력받는다."""
    mode = "dry-run (LLM 없음)" if dry_run else "full (LLM 호출)"
    v_tag = " + verbose" if verbose else ""
    print(f"\n정규화 테스트 대화형 모드 [{mode}{v_tag}]")
    print("종료하려면 'q' 또는 'quit' 입력\n")

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

        if dry_run:
            show_dry_run(query)
        else:
            asyncio.run(
                run_normalization_test(query, verbose=verbose),
            )


def main() -> None:
    """메인 엔트리포인트."""
    args = sys.argv[1:]

    dry_run = "--dry-run" in args
    if dry_run:
        args.remove("--dry-run")

    verbose = "--verbose" in args or "-v" in args
    if "--verbose" in args:
        args.remove("--verbose")
    if "-v" in args:
        args.remove("-v")

    if args:
        query = " ".join(args)
        if dry_run:
            show_dry_run(query)
        else:
            asyncio.run(
                run_normalization_test(query, verbose=verbose),
            )
    else:
        interactive_mode(dry_run=dry_run, verbose=verbose)


if __name__ == "__main__":
    main()
