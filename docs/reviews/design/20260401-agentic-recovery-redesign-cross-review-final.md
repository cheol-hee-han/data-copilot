# 최종 통합 설계 검토 보고서: Agentic Recovery Loop 재설계

- **검토 대상**:
  - 원안: `docs/strategy-proposals/agentic-recovery-redesign/01-strategy.md`, `02-detailed-design.md`
  - 1차 리뷰: `docs/reviews/design/20260401-agentic-recovery-redesign-review.md`
  - 크로스리뷰: `docs/reviews/design/20260401-agentic-recovery-redesign-cross-review.md`
- **검토 이력**:
  - 1차 리뷰: 2026-04-01 — State 관리 연속성 → LLM 동작 신뢰성 → 라우팅 일관성 → 답변 정확도 → 폐쇄망 호환성
  - 크로스리뷰(1차 교차 + 2차 프로덕션 재검토): 2026-04-01 — 원안과 리뷰를 비판적으로 재검토, 연구 근거 기반 교차 비교, 프로덕션 환경 관점 정확도 실효성 중심 재검토
  - 최종 통합 리뷰: 2026-04-01 — 1차 리뷰와 크로스리뷰의 전체 내용을 통합, 상충 해소, 누락 보완
- **코드 검증 기준**: `main` 브랜치 (`6491f9b`) — 코드베이스 직접 대조 완료

---

## 총평

본 문서는 1차 설계 리뷰(P0 3건, P1 4건, P2 4건)와 크로스리뷰(1차 교차 검토 + 프로덕션 재검토, 총 22건)를 최종 통합한 보고서다.

원안의 문제 진단(context_explorer 과부하, recovery_planner 실행 단절, 도구 일괄 전용)과 2-Phase Exploration 분리 전략은 NL-to-SQL 선행 연구(CHESS, DIN-SQL, MAC-SQL)의 "컨텍스트 수집과 SQL 생성의 완전 분리" 원칙과 정확히 부합한다. Phase 1 기계적 분리(knowledge_fetcher + knowledge_interpreter)는 behavioral change 없이 1,177줄 단일 파일을 해소하는 가장 안전하고 가치 높은 변경이다.

1차 리뷰가 제기한 이슈 대부분은 타당하나, 크로스리뷰를 통해 일부 해결안이 개선되었고, 리뷰가 놓친 결함 5건과 프로덕션 환경 신규 이슈 5건이 추가로 발견되었다. 특히 **프로덕션 환경의 세 가지 핵심 제약**이 설계 전반의 판단 기준이 된다:

1. **참조 저장소의 실질적 한계** — Qdrant의 상품설명서/업무매뉴얼은 SQL 추론에 직접적 힌트가 아님, MongoDB 비즈용어사전은 200개 미만으로 부실, 보고서 SQL/골든셋은 아직 없음
2. **70B~397B LLM의 ReAct 능력 한계** — 파인튜닝 없는 상태에서 복잡한 구조화 출력의 안정성이 보장되지 않음
3. **함축적 사용자 질의 + "선 추론 후 표시" 정책** — 대부분 명확화 질문 없이 추론으로 진행해야 함

**최종 심각도 분포**: P0 5건 / P1 12건 / P2 5건 (총 22건)

---

## 1. 잘 설계된 부분 (보존 권장) — 8건

프로덕션 환경에서도 유효하며, 환경 불문으로 적용 가능한 기본 설계.

### S-1. Phase 1 기계적 분리 (knowledge_fetcher + knowledge_interpreter)

- **원안 위치**: 01-strategy 3.2절, 02-detailed 2.1-2.2절

context_explorer의 Phase 1-2(순수 I/O)와 Phase 3-6(LLM 해석) 경계는 코드에서도 명확히 확인됨 — 도구 실행이 290행에서 완료되고 LLM 해석이 297행에서 시작하는 깔끔한 분리점 존재. `_run_step()`, `_should_skip_step()` 등은 독립 함수로 이미 구현되어 있어 기계적 추출이 안전. behavioral change 없이 1,177줄 단일 파일을 해소하는 가장 안전하고 가치 높은 변경.

**근거**:
- **CHESS** (Talaei et al., Stanford, 2024, arXiv:2405.16755): 4단계 분리 파이프라인에서 스키마 프루닝만으로 **LLM 토큰 5배 감소 + 정확도 2% 향상**. "컨텍스트 수집과 SQL 생성의 완전 분리가 핵심"이라는 결론이 원안의 분리 전략과 정확히 일치.
- **DIN-SQL** (Pourreza & Rafiei, NeurIPS 2023, arXiv:2304.11015): 태스크 분해 → 서브태스크 few-shot → 자기교정 패턴으로 단순 few-shot 대비 ~10% 향상.

---

### S-2. Hypothesis 상태 전이의 Python 코드 수행

- **원안 위치**: 01-strategy 검토 2, 3.3절

상태 전이를 deterministic Python 코드로 수행하는 결정이 올바름. Solar Pro 2 70B에서 복잡한 상태 전이 JSON의 안정적 출력을 기대하기 어려움. 현행 `recovery_planner`와도 일관된 패턴.

**근거**:
- **AgentBench** (Liu et al., Tsinghua, ICLR 2024, arXiv:2308.03688): 29개 모델 실험에서 70B 이하 OSS 모델이 "Poor long-term reasoning, decision-making, and instruction following"으로 복잡한 상태 전이에서 실패율이 높음을 확인.
- **ReAct** (Yao et al., 2023, arXiv:2210.03629) 원논문: observation parsing과 state tracking은 환경(코드)이 수행하고 LLM은 thought+action만 생성하는 역할 분리가 기본 전제.

---

### S-3. Structured Output 채택 (native tool-calling 기각)

- **원안 위치**: 01-strategy 3.3절

폐쇄망 모델 호환의 최소 공통분모. `max_items: 4` 강제, 감사 추적 용이성, LLM 호출 횟수 절감 모두 native tool-calling 대비 우위.

**근거**:
- **JSONSchemaBench** (Guidance-AI/Microsoft Research, 2025, arXiv:2501.10868): 10K 실세계 JSON 스키마 벤치마크에서 제약 디코딩이 비제약 대비 **생성 속도 50% 향상, 다운스트림 정확도 최대 4% 향상**.
- **BFCL** (UC Berkeley): Qwen 3 14B가 F1 0.971로 GPT-4 수준이나, "memory, dynamic decision-making, long-horizon reasoning은 미해결".
- **StructEval** (Tiger AI Lab, 2025): OSS 모델의 구조화 출력 준수율이 상용 모델 대비 ~10점 낮음.

---

### S-4. CONFLICTED 처리의 외부 위임

- **원안 위치**: 01-strategy 검토 6

recovery_agent 내부에서 `interrupt()`로 처리하면 LangGraph의 ReAct 루프 상태 직렬화가 복잡해지는 실질적 위험이 있으므로, CONFLICTED → readiness_gate → clarification_handler 경로로의 외부 위임 자체는 적절하다.

단, 프로덕션 환경에서는 **"선 추론 후 표시" 정책**이 적용되어, 대부분의 CONFLICTED 상황에서는 ASK_USER 대신 합리적 추론으로 진행하고 결과에 추론 근거를 표시해야 한다. ASK_USER는 "추론으로도 해결 불가능한" 경우(테이블 선택 충돌, 산출식 충돌 등)에만 발동되어야 하므로, readiness_gate의 ASK_USER 기준 변경이 필수 (P1-1 참조).

**근거**:
- **LangGraph 공식 문서** (2024): 내부 루프에서의 interrupt는 체크포인트 직렬화와 충돌할 수 있음을 명시.

---

### S-5. Truncation 전략의 티어별 설계

- **원안 위치**: 01-strategy 검토 3

confirmed_knowledge 전량 포함, REJECTED 테이블 제외, tool_results 최근 1라운드만 유지하는 전략이 8-16K 윈도우에 실용적. 예상 토큰 소모 2,000-4,000은 안전 마진 확보.

**근거**:
- **Complexity Trap** (2025, arXiv:2508.21433): "simple observation masking이 LLM 요약과 동등한 solve rate를 달성하면서 비용은 절반".
- **IBM Context Window Overflow** (Labate et al., 2025, arXiv:2511.22729): 메모리 포인터 방식으로 **토큰 사용량 7배 감소**.
- **Lost in the Middle** (Liu et al., 2023, Stanford): LLM은 컨텍스트의 시작과 끝에 집중하며 중간 정보 활용도가 낮음.

---

### S-6. Fast-path 보존 및 fast-path 실패 → knowledge_fetcher 라우팅

- **원안 위치**: 01-strategy 검토 5

fast-path 실패 시 초기 컨텍스트 자체가 없으므로 Phase 1부터 시작하는 것이 논리적으로 정확. 02-detailed 5.2절의 `_route_after_sql_validator`에서 `exploration_phase = "initial"` 설정도 올바름.

---

### S-7. 테스트 전략 — 단일 스텝 독립 함수 추출

- **원안 위치**: 01-strategy 검토 7, 02-detailed 8절

`_recovery_step()`, `_handle_hypothesis_transition()`, `_apply_knowledge_updates()`를 독립 함수로 추출하여 단위 테스트 가능하게 하는 설계가 올바름.

**근거**:
- **MAC-SQL** (Wang et al., COLING 2025, arXiv:2312.11242)에서도 Selector, Decomposer, Refiner 각 에이전트를 독립적으로 테스트 가능하게 설계하여 디버깅 효율을 향상.

---

### S-8. 마이그레이션의 Step 1→2→3→4 단계별 실행

- **원안 위치**: 01-strategy 7.1절

behavioral change 없는 Step 1-2를 먼저 수행하고, 핵심 변경인 Step 3을 이후에 수행하는 것은 리스크 격리에 효과적. Step 1 완료 기준(기존 e2e 테스트 수정 없이 통과)도 명확.

---

## 2. [P0] 치명적 — 구현 전 반드시 해소 (5건)

### P0-1. `_finalize_recovery` give_up 시 무한 루프 가능

- **출처**: 1차 리뷰 P0-1 → 크로스리뷰 4-1에서 해결안 개선
- **원안 위치**: 01-strategy 306-308행, 02-detailed 710행

**문제**: 01-strategy(즉시 종료)와 02-detailed(readiness_gate 재진입)에서 give_up 처리가 충돌함. 02-detailed의 접근(readiness_gate 위임)이 force-generate SSOT 유지에는 유리하지만, **readiness_gate와 recovery_agent 사이에 무한 루프가 형성될 수 있음**.

**위험 시나리오**:

```
[1회차]
recovery_agent 진입 (replan_count: 0 → 1)
  → LLM이 어떤 도구도 유용하지 않다고 판단
  → action: "give_up" 반환
  → _finalize_recovery: Phase.VERIFYING 설정
  → readiness_gate로 라우팅

readiness_gate:
  readiness_score = 0.30 (< THRESHOLD_FORCE_GENERATE 0.40)
  replan_count = 1 (< MAX_REPLANS 3)
  → verdict: REPLAN
  → recovery_agent로 라우팅  ← 다시 돌아옴!

[2회차~3회차]
  → 상태가 변하지 않았으므로 다시 give_up → 3회 반복 후 종료

결과: 실질적 탐색 없이 LLM 3회 호출 + 불필요한 3회 루프 소모.
```

**해결안**: 01-strategy의 즉시 종료 방식을 채택하되, force-generate 판정을 recovery_agent 내부에서 수행. 추가 state 필드(`recovery_gave_up`) 없이 깔끔하게 처리.

```python
def _finalize_recovery(reason, decision):
    if decision is None or decision.action == "give_up" or should_terminate(reason):
        score = calculate_readiness(reason)
        if score >= THRESHOLD_FORCE_GENERATE:
            reason.phase = Phase.GENERATING  # force-generate 직접 판정
        else:
            reason.phase = Phase.DONE
            reason.final_status = FinalStatus.FAILURE
        return
    if decision.action == "ready":
        reason.phase = Phase.GENERATING
```

> **참고 (1차 리뷰 해결안)**: 1차 리뷰에서는 `recovery_gave_up: bool` 플래그를 state에 추가하여 readiness_gate에서 무한 루프를 차단하는 방안을 제시했으나, 위 방식이 추가 필드 없이 readiness_gate 재진입을 완전 차단하여 더 깔끔하다.

**프로덕션 중요도**: "선 추론 후 표시" 정책에 따라 give_up이어도 score가 일정 수준이면 추론 기반으로 SQL을 생성하고 결과에 주의사항을 표시해야 하며, 70B 모델에서 give_up 빈도가 더 높으므로 이 경로의 안정성이 정확도에 직접적 영향을 준다.

**근거**:
- **MAST** (Cemri et al., UC Berkeley, NeurIPS 2025, arXiv:2503.13657): 1,600개 trace 분석에서 "lack of termination criteria"가 System Design Issues의 직접 원인으로 분류.
- **LLM Repetition Problem** (2024, arXiv:2512.04419): "once the model enters a repetitive state, the expected escape time is infinite under greedy decoding". 외부 종료 메커니즘 필수.

---

### P0-2. `exploration_phase` 리셋 시점 미정의 — 멀티턴 라우팅 오류

- **출처**: 1차 리뷰 P0-2 → 크로스리뷰 4-2에서 확인
- **원안 위치**: 02-detailed 816행

**문제**: `exploration_phase: Literal["initial", "recovery"]` 필드가 `"recovery"`로 설정된 후 `"initial"`로 복귀하는 경로가 fast-path 실패 시 하나뿐임.

**위험 시나리오 (멀티턴)**:

```
── 1번째 질의: "올해 1분기 지점별 여신 실행 금액" ──
planner → knowledge_fetcher → knowledge_interpreter → readiness_gate
  → REPLAN → exploration_phase = "recovery" ← 여기서 설정됨
  → recovery_agent → sql_generator → 성공 응답

── 2번째 질의: "작년 수신잔액 추이" (같은 세션) ──
planner → knowledge_fetcher → knowledge_interpreter → readiness_gate
  → EXPLORE verdict
  → exploration_phase == "recovery" (1번째 질의에서 잔류!)
  → recovery_agent로 잘못 라우팅! ← 초기 탐색인데 recovery 경로 진입
```

**해결안**: planner_node 진입 시 리셋.

```python
# planner.py — planner_node 시작부
async def planner_node(state: PipelineState) -> dict:
    reason = state.reason
    reason.exploration_phase = "initial"  # 매 질의 시작 시 리셋
    reason.recovery_rounds = 0           # recovery 카운터도 리셋
    ...
```

**근거**:
- **LangGraph 공식 문서** (2024): Checkpointer 패턴에서 "각 conversation turn의 시작에 ephemeral state를 리셋"하는 것이 기본 원칙.

---

### P0-3. execute_tool 어댑터와 현행 tool_input 형식 불일치

- **출처**: 1차 리뷰 P0-3 → 크로스리뷰 4-3에서 코드 직접 검증
- **원안 위치**: 02-detailed 1006-1021행

**문제**: `_execute_single_tool` 어댑터에서 `json.dumps(tc.kwargs)`로 변환하지만, 현행 `tools.py`의 도구들은 **쉼표 구분 문자열**을 파싱함.

**코드 검증**:

```python
# 실제 tools.py 259-263행
async def _tool_get_sample_rows(tool_input: str) -> Any:
    parts = [p.strip() for p in tool_input.split(",")]
    table_name = parts[0] if parts else ""
    return await get_sample_rows(table_name)
```

`json.dumps({"table_name": "TB_LOAN_INFO", "limit": "5"})` → `.split(",")` → `['{"table_name": "TB_LOAN_INFO"', '"limit": "5"}']` → table_name이 `{"table_name": "TB_LOAN_INFO"`가 됨 → DB 조회 실패.

**해결안**: kwargs를 현행 tool_input 형식으로 명시적 변환.

```python
async def _execute_single_tool(tc: ToolCall, reason: ReasoningState) -> Any:
    tool_name = tc.tool

    if tool_name in ("search_table_meta", "search_manual", "search_glossary"):
        tool_input = tc.kwargs.get("query") or tc.kwargs.get("term", "")
    elif tool_name == "search_code_meta":
        tool_input = tc.kwargs.get("column_name", "")
    elif tool_name == "get_sample_rows":
        parts = [tc.kwargs.get("table_name", "")]
        if tc.kwargs.get("schema_name"):
            parts.append(tc.kwargs["schema_name"])
        if tc.kwargs.get("db_source"):
            parts.append(tc.kwargs["db_source"])
        tool_input = ",".join(parts)
    elif tool_name == "get_date_distribution":
        parts = [
            tc.kwargs.get("table_name", ""),
            tc.kwargs.get("date_column", ""),
        ]
        if tc.kwargs.get("schema_name"):
            parts.append(tc.kwargs["schema_name"])
        if tc.kwargs.get("db_source"):
            parts.append(tc.kwargs["db_source"])
        tool_input = ",".join(parts)
    else:
        tool_input = next(iter(tc.kwargs.values()), "")

    return await execute_tool(tool_name, tool_input)
```

추가로 **`execute_tool` 시그니처를 kwargs 기반으로 변경하는 것을 Step 3과 병합하여 수행**하면 어댑터 자체가 불필요해지므로 더 깔끔하다.

---

### P0-4. `_finalize_recovery`에서 `decision is None` 케이스의 미흡한 처리

- **출처**: 크로스리뷰 2-3 (1차 리뷰 미지적)
- **원안 위치**: 02-detailed 4.10절 (705-712행)

**문제**: `decision is None`은 ReAct 루프가 `should_terminate()` 때문에 한 번도 실행되지 않았을 때 발생. 이 경우 `Phase.VERIFYING`으로 설정하여 readiness_gate로 보내는데, readiness_gate에서 REPLAN이 나오면 다시 recovery_agent로 돌아와 동일 상황이 반복.

**프로덕션 중요도**: 70B 모델의 파싱 실패율이 높아 `decision is None` 도달 확률이 상용 모델 대비 훨씬 높으므로, 즉시 종료하지 않으면 무한 루프 위험이 크게 증가.

**해결안**: `decision is None`일 때 즉시 종료. P0-1의 `_finalize_recovery` 통합 해결안에 이미 포함됨.

```python
def _finalize_recovery(reason, decision):
    if decision is None:
        reason.phase = Phase.DONE
        reason.final_status = FinalStatus.FAILURE
        return
    # ... 나머지 (P0-1 해결안 참조)
```

**근거**:
- **MAST** (arXiv:2503.13657): "lack of termination criteria"가 System Design Issues의 직접 원인. None 상태에서 재진입을 허용하는 것은 이 실패 모드에 해당.

---

### P0-5. 도구 실행 병렬화의 범위와 의존성 판별 미정의

- **출처**: 1차 리뷰 P2-3 → 크로스리뷰 5-3에서 **P0으로 격상**
- **원안 위치**: 02-detailed 459-482행

**문제**: 두 문서 모두 "독립 도구 간 병렬화"만 언급하고, **도구 간 의존성 판별 로직**은 제시하지 않음. 순차 실행으로는 recovery_agent의 핵심 가치인 "독립적 공백의 한 라운드 batch 처리"가 실현되지 않음.

**P0 격상 근거**: 단순한 지연 개선이 아니라, **70B 모델의 ReAct 라운드 수를 줄이는 핵심 수단**. 한 라운드에서 3~4개 도구를 병렬 실행하면 **동일 정보를 더 적은 라운드에서 수집** → LLM 판단 오류 기회 자체가 감소. 70B 모델에서 ReAct 라운드가 길어질수록 "조기 ready" 또는 "무한 call_tools" 위험이 급증 (AgentBench TLE 패턴).

```
── 순차 실행 (현재 설계) ──
search_table_meta("지점"): 200ms
search_code_meta("branch_type_cd"): 150ms
get_date_distribution("TB_LOAN_EXEC", "exec_dt"): 300ms
= 총 650ms

── 병렬 실행 (개선안) ──
asyncio.gather(...) = 총 ~300ms (가장 느린 도구 기준)
```

**해결안**: 프롬프트 수준 방어 + 병렬 실행 + 개별 예외 처리.

1. **프롬프트 수준 방어**: RECOVERY_AGENT_SYSTEM_PROMPT에 추가.
   ```
   7-1. 한 라운드의 tool_calls는 서로 독립적이어야 합니다.
        다른 도구의 결과가 필요한 호출은 다음 라운드에 요청하세요.
   ```

2. **병렬 실행 코드**:

```python
async def _execute_tools(
    tool_calls: list[ToolCall],
    reason: ReasoningState,
) -> list[dict]:
    tasks = [_execute_single_tool(tc, reason) for tc in tool_calls]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    for tc, raw in zip(tool_calls, raw_results):
        if isinstance(raw, Exception):
            results.append({"tool": tc.tool, "purpose": tc.purpose, "status": "error", "result": str(raw)})
        else:
            results.append({"tool": tc.tool, "purpose": tc.purpose, "status": "success", "result": raw})
        reason.loop_guard.increment_tool_calls()

    return results
```

**근거**:
- **Latency-Aware Orchestration** (2025, arXiv:2601.10560): "sequential scaling은 토큰 효율 우수, parallel scaling은 **1.6x 빠름**". 독립 단계는 병렬화, 의존성 있는 단계는 강제 병렬화 금지를 명시적 원칙으로 제시.

---

## 3. [P1] 중요 — 구현 초기에 해소 권장 (12건)

### P1-1. [프로덕션 신규] recovery_agent의 "선 추론 후 표시" 정책 미반영

- **출처**: 크로스리뷰 6-1 (1차 리뷰 미지적)

**문제**: 원안과 1차 리뷰 모두 **"모호하면 사용자에게 질문"** 패턴을 전제하지만, 프로덕션 환경에서는 대부분의 모호한 질의에서 **추론으로 진행하고 결과에 추론 근거를 표시**해야 한다.

```
── 현재 설계의 가정 ──
"예금신규 top 3" → 모호함 → ASK_USER → 사용자에게 "예금신규액? 예금신규건수?" 확인

── 프로덕션 정책 ──
"예금신규 top 3" → 모호함 → "예금신규액"으로 추론 → SQL 생성
                         → 결과에 "예금신규액 기준으로 조회하였습니다" 표시
```

**영향 범위**:

1. readiness_gate의 ASK_USER 발동 기준이 너무 낮음 — 대부분의 CONFLICTED 상황에서도 하나를 추론 선택하고 진행해야 함
2. recovery_agent의 give_up 기준이 너무 빠름 — 정확한 메타를 찾지 못해도 "합리적 추론"으로 SQL을 생성해야 하는 경우가 많음
3. sql_generator 프롬프트에 추론 근거 표시 채널이 없음 — Present Layer까지 전달되어야 함

**수정안**:

```python
# 1. readiness_gate.py — ASK_USER 발동 조건을 제한적으로 변경
def _should_ask_user(reason: ReasoningState) -> bool:
    """
    ASK_USER는 '추론으로도 해결 불가능한' 경우에만 발동.
    예: 두 테이블이 완전히 다른 결과를 주는 경우, 산출식 충돌 등.
    단순 용어 모호성(예금신규액 vs 건수)은 추론으로 처리.
    """
    critical_conflicts = [
        ki for ki in reason.knowledge_items
        if ki.status == ConfidenceStatus.CONFLICTED and ki.is_critical
    ]
    unresolvable = [
        ki for ki in critical_conflicts
        if _is_unresolvable_conflict(ki)
    ]
    return len(unresolvable) > 0


# 2. state.py — 추론 근거 전달 채널 추가
class ReasoningState(BaseModel):
    ...
    inference_notes: list[str] = Field(default_factory=list)
    """추론으로 결정한 사항과 그 근거. Present Layer에서 사용자에게 표시."""


# 3. recovery_agent.py — 추론 시 inference_notes에 기록
if decision.action == "ready" and reason.knowledge_items:
    for ki in reason.knowledge_items:
        if ki.status in (ConfidenceStatus.PROBABLE, ConfidenceStatus.CANDIDATE):
            reason.inference_notes.append(
                f"'{ki.key}'를 '{ki.value}' 기준으로 해석하였습니다 "
                f"({ki.evidence[-1] if ki.evidence else '추론'})"
            )
```

---

### P1-2. [프로덕션 신규] 참조 저장소 한계를 반영한 recovery_agent 도구 전략 부재

- **출처**: 크로스리뷰 6-2 (1차 리뷰 미지적)

**문제**: recovery_agent의 6개 도구를 동등하게 취급하지만, 프로덕션 저장소별 SQL 추론 기여도에 큰 차이가 있음.

| 도구 | 참조 저장소 | 프로덕션 기대 효과 |
|------|-----------|------------------|
| `search_table_meta` | MongoDB (테이블/컬럼) | **높음** |
| `search_code_meta` | MongoDB (코드 메타) | **높음** |
| `get_sample_rows` | PostgreSQL (직접 조회) | **높음** |
| `get_date_distribution` | PostgreSQL (직접 조회) | **중간** |
| `search_manual` | Qdrant (업무매뉴얼) | **낮음** — SQL 추론에 직접적 힌트 아님 |
| `search_glossary` | MongoDB (200개 미만) | **낮음** — 부실, 결과 없을 확률 높음 |

**수정안**: recovery_agent 프롬프트에 도구 우선순위 가이드 추가 + 이전 라운드 빈 결과 도구 피드백.

```
## 도구 우선순위 가이드
1. search_table_meta: 테이블/컬럼 구조 확인 (SQL 생성에 직접적 힌트)
2. get_sample_rows: 실제 데이터 패턴 확인
3. search_code_meta: 코드 컬럼의 값-설명 매핑 확인
4. get_date_distribution: 날짜 컬럼의 데이터 범위 확인
5. search_glossary: 금융 용어 정의 확인 (용어사전이 부실하여 결과가 없을 수 있음)
6. search_manual: 업무 프로세스 확인 (SQL 추론에 간접적 참고만 됨)

주의: search_glossary와 search_manual의 결과가 비어있는 것은 정상입니다.
결과가 없다면 다른 도구로 전환하세요. 동일 도구를 다른 검색어로 재시도하지 마세요.
```

```python
# 빈 결과 도구 목록을 프롬프트에 포함
empty_tools = [
    tr["tool"] for tr in tool_results
    if tr.get("status") == "success" and not tr.get("result")
]
if empty_tools:
    lines.append(f"\n## 이전 라운드에서 결과가 없었던 도구: {', '.join(empty_tools)}")
    lines.append("위 도구를 다시 호출해도 결과가 없을 가능성이 높습니다.")
```

**근거**: CHESS(2024)에서 "스키마 프루닝이 NL-to-SQL의 핵심"이라는 결론은, 역으로 비효과적 정보원에서의 탐색이 정확도를 저하시킨다는 의미.

---

### P1-3. [프로덕션 신규] 함축적 금융 용어의 "합리적 추론" 경로 부재

- **출처**: 크로스리뷰 6-3 (1차 리뷰 미지적)

**문제**: 현재 knowledge_items의 상태 전이 모델에는 **"증거 없이 관행적 추론으로 결정"하는 경로**가 없다. 도구 결과(evidence)가 없으면 UNRESOLVED에서 벗어날 수 없고, "여신 = 여신잔액"이라는 관행적 매핑은 메타 저장소에 없으므로 도구를 아무리 호출해도 발견할 수 없다.

```
── 시나리오 ──
사용자: "여신 top 3 지점"
→ "여신" → knowledge_item(status=UNRESOLVED)
→ search_table_meta("여신") → 여신실행, 여신잔액, 여신한도 등 다수 발견
→ 구체적 의미 불분명 → CONFLICTED → ASK_USER (불필요한 대화 턴)

프로덕션 기대:
→ "여신" → 가장 일반적 해석인 "여신잔액"으로 추론 → SQL 생성 → "여신잔액 기준" 명시
```

**수정안**: PROBABLE의 의미를 확장하여 추론 기반 설정을 허용 (방안 B, 최소 변경 권장).

```python
class KnowledgeUpdate(BaseModel):
    key: str
    new_status: Literal["CANDIDATE", "PROBABLE", "CONFIRMED", "CONFLICTED"]
    evidence: str  # 도구 증거 또는 "관행적 해석: 여신은 통상 여신잔액을 의미"
    value: str | None = None
    is_inferred: bool = False  # True이면 도구 증거 없는 추론
```

**recovery_agent 프롬프트 추가**:

```
## 추론 지침

도구 검색으로 정확한 답을 찾지 못했지만, 금융 도메인 관행상 합리적 추론이 가능한 경우:
- new_status를 "PROBABLE"로 설정하고 is_inferred=true로 표시
- evidence에 추론 근거를 명시 (예: "관행적 해석: '여신'은 통상 '여신잔액'을 의미")
- 이 추론은 결과 응답에서 사용자에게 표시됩니다

추론이 합리적인 경우:
- 금융 용어의 일반적 해석 (예: "여신" → 여신잔액, "수신" → 수신잔액)
- 기간 미지정 시 최근 기간 (예: "실적" → 당월 실적)
- 집계 기준 미지정 시 금액 기준 (예: "top 3" → 금액 기준 상위 3)

추론이 부적절한 경우 (ASK_USER 필요):
- 서로 다른 테이블을 사용해야 하는 완전히 다른 의미가 존재하는 경우
- 금융 지표 산출식이 불확실한 경우 (연체율, BIS비율 등)
```

**근거**: financial-domain.md에서 "금융 계수산출식은 정확한 산출식이 필수"라고 명시되어 있으나, 이는 **산출식**에 한정된 규칙. 일반적인 용어 해석은 "선 추론 후 표시" 정책이 적용됨.

---

### P1-4. Phase 기반 라우팅에서 ReadinessVerdict 정보 소실

- **출처**: 1차 리뷰 P1-1 → 크로스리뷰 4-4에서 확인

**문제**: `VERDICT_TO_PHASE` 변환 후 Phase만으로 라우팅하면, ASK_USER와 TERMINATE 등 verdict 구분이 간접적이고 취약함. `Phase.VERIFYING`일 때 `pending_signals` 존재 여부로 ASK_USER를 판별하는 것은 불안정.

**해결안**: ReadinessVerdict를 state에 직접 저장.

```python
# state.py
class ReasoningState(BaseModel):
    ...
    last_verdict: ReadinessVerdict | None = None

# readiness_gate.py
reason.last_verdict = verdict
reason.phase = VERDICT_TO_PHASE[verdict]

# _route_after_readiness_gate
def _route_after_readiness_gate(state: PipelineState) -> str:
    reason = state.reason
    verdict = reason.last_verdict  # Phase가 아닌 verdict 직접 참조

    if verdict == ReadinessVerdict.GENERATE:
        return "sql_generator"
    if verdict == ReadinessVerdict.EXPLORE:
        return "knowledge_fetcher" if reason.exploration_phase == "initial" else "recovery_agent"
    if verdict == ReadinessVerdict.REPLAN:
        return "recovery_agent"
    if verdict == ReadinessVerdict.ASK_USER:
        return "clarification_handler"
    return "result_finalizer"  # TERMINATE
```

**근거**: "Explicit is better than implicit" (PEP 20). 정보 변환 시 원본을 보존하는 것은 디버깅과 라우팅 정확성 모두에 유리.

---

### P1-5. knowledge_item 매칭 — ID 기반 참조로 전환

- **출처**: 1차 리뷰 P1-2 → 크로스리뷰 4-5에서 해결안 근본 개선

**문제**: `_find_knowledge_item`의 부분 일치 fallback에서, 짧은 key가 복수 항목과 매칭될 수 있음. 한국어 금융 용어는 접미어 공유가 매우 빈번(`~일자`, `~코드`, `~금액`, `~건수`, `~비율`).

**위험 예시**:

```python
knowledge_items = [
    KnowledgeItem(key="여신실행일자", value="exec_dt", status=CONFIRMED),
    KnowledgeItem(key="기준일자", value="base_dt", status=CONFIRMED),
    KnowledgeItem(key="만기일자", value="mtr_dt", status=UNRESOLVED),
]
# LLM이 key="일자"로 update → "여신실행일자"와 첫 번째 매칭 → value 오염
```

**해결안**: `KnowledgeItem`에 `knowledge_id` 필드를 추가하고, LLM은 ID로만 참조. "채번은 코드, 참조는 LLM" 원칙.

```python
# 1. KnowledgeItem에 ID 필드 추가
class KnowledgeItem(BaseModel):
    knowledge_id: str  # "K1", "K2" — 코드에서 자동 채번
    key: str
    status: ConfidenceStatus
    value: str | None = None
    evidence: list[str] = Field(default_factory=list)

# 2. KnowledgeUpdate에서 ID로 참조
class KnowledgeUpdate(BaseModel):
    item_id: str | None = None   # 기존 항목 갱신 시 "K1" 등, 신규 시 null
    key: str                      # 신규 생성 시에만 사용
    new_status: Literal["CANDIDATE", "PROBABLE", "CONFIRMED", "CONFLICTED"]
    evidence: str
    value: str | None = None

# 3. 적용 로직 — 문자열 매칭 불필요
def _apply_knowledge_update(reason: ReasoningState, update: KnowledgeUpdate):
    if update.item_id:
        item = knowledge_map[update.item_id]  # O(1) lookup
        item.status = update.new_status
        item.evidence.append(update.evidence)
    else:
        new_id = f"K{len(reason.knowledge_items) + 1}"
        reason.knowledge_items.append(
            KnowledgeItem(knowledge_id=new_id, key=update.key, ...)
        )

# 4. 프롬프트에서 ID 표시
"""
현재 미해결 항목:
  [K1] 여신실행일자 — UNRESOLVED
  [K2] 기준일자 — CONFIRMED
  [K3] 만기일자 — CANDIDATE
"""
```

> **참고 (1차 리뷰 해결안)**: 1차 리뷰에서는 복수 매칭 시 None 반환 → 새 항목 생성하는 방어 로직을 제안했으나, ID 기반 참조가 근본적 해결이다. `candidate_tables`(table_name 유니크)와 `execution_plan`(step 번호)은 기존 키가 충분하므로 별도 ID 추가 불필요. `knowledge_items`만 문자열 key의 모호성 문제가 있어 ID를 추가한다.

---

### P1-6. `PROMOTION_ORDER`에 CANDIDATE 누락

- **출처**: 1차 리뷰 P1-3 → 크로스리뷰 4-6에서 코드 직접 검증
- **원안 위치**: 02-detailed 536-544행

**문제**: `ConfidenceStatus.CANDIDATE`는 `src/models/enums.py:72`에 실제 존재하며, `context_explorer.py:562`에서 CANDIDATE로 knowledge_item을 생성하고, `confidence_scorer.py:163`에서 점수 계산에 사용됨. 그러나 PROMOTION_ORDER에 누락되어 CANDIDATE→UNRESOLVED 역행이 허용됨.

**해결안**:

```python
PROMOTION_ORDER = {
    ConfidenceStatus.UNRESOLVED: 0,
    ConfidenceStatus.CANDIDATE: 1,   # 추가 필수
    ConfidenceStatus.PROBABLE: 2,
    ConfidenceStatus.CONFIRMED: 3,
    ConfidenceStatus.CONFLICTED: 4,
}
```

---

### P1-7. discovered_facts 갱신 경로 부재 + CANDIDATE 프롬프트 포함

- **출처**: 1차 리뷰 P1-4 + 크로스리뷰 2-1 통합

**문제 1 (discovered_facts)**: recovery_agent의 `_apply_knowledge_updates`는 `knowledge_items`만 갱신하고, `discovered_facts` 추가 경로가 없음. sql_generator 프롬프트의 "발견된 사실" 섹션에 recovery_agent의 발견이 전달되지 않음.

```
recovery_agent: "TB_BRANCH_INFO 샘플 확인 결과 폐쇄지점도 포함됨"
→ discovered_facts에 추가되지 않음
→ sql_generator가 WHERE 조건에 "폐쇄여부 = 'N'" 누락할 수 있음
```

**문제 2 (CANDIDATE 프롬프트 누락)**: `_build_recovery_prompt()`에서 `confirmed`, `probable`, `unresolved`, `conflicted`만 필터링하고 **CANDIDATE 상태의 knowledge_items는 어디에도 포함되지 않음**. recovery_agent가 이미 단일 출처에서 확인된 항목을 보지 못해 중복 탐색.

**해결안**:

```python
# 1. discovered_facts — knowledge_updates가 있는 경우에만 추가 (노이즈 방지)
if decision.analysis and decision.knowledge_updates:
    reason.discovered_facts.append(f"[recovery] {decision.analysis}")

# 2. CANDIDATE를 프롬프트에 포함
candidate = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.CANDIDATE]
for ki in candidate:
    lines.append(f"- [{ki.knowledge_id}] [후보] {ki.key}: {ki.value} (단일 출처)")
```

**근거**:
- **Chain-of-Table** (Wang et al., 2024, arXiv:2401.04398): 중간 상태의 정보를 프롬프트에 포함하는 것이 최종 정확도를 5-10% 향상시킴.

---

### P1-8. `KnowledgeUpdate.new_status`에 CANDIDATE 미포함 + `search_use_cases` 도달 불가 코드 제거

- **출처**: 크로스리뷰 2-2, 2-4 (1차 리뷰 미지적)

**문제 1**: `KnowledgeUpdate`의 `new_status`가 `Literal["PROBABLE", "CONFIRMED", "CONFLICTED"]`로 정의되어 CANDIDATE로의 승격을 허용하지 않음. 실제 시스템에서 CANDIDATE는 "단일 출처 확인" 상태로, recovery_agent가 하나의 도구 결과만으로 확인한 경우 PROBABLE보다 CANDIDATE가 더 정확한 표현.

```python
# 수정
class KnowledgeUpdate(BaseModel):
    new_status: Literal["CANDIDATE", "PROBABLE", "CONFIRMED", "CONFLICTED"]
```

**문제 2**: `_execute_single_tool`의 조건문에 `"search_use_cases"`가 포함되어 있으나, 원안에서 명시적으로 recovery_agent 도구 목록에서 제외됨. `ToolCall.tool`의 Literal 타입에도 없으므로 도달 불가능한 코드.

```python
# 변경 전
if tool_name in ("search_table_meta", "search_use_cases", "search_manual", "search_glossary"):

# 변경 후
if tool_name in ("search_table_meta", "search_manual", "search_glossary"):
```

> **참고**: P2-5에서 `search_use_cases`의 recovery 경로 조건부 복원을 제안. 현시점에서는 도달 불가 코드를 제거하되, P2-5 구현 시 복원하는 단계적 접근 권장.

---

### P1-9. recovery_agent ReAct 루프에서 "진전 감지(progress detection)" 메커니즘 부재

- **출처**: 크로스리뷰 5-1 (1차 리뷰 미지적, **P2→P1 격상**)

**문제**: recovery_agent가 `call_tools`를 반복하면서 **knowledge_items의 상태가 변하지 않는** 경우, `max_internal_rounds`(5)까지 소모.

```
라운드 1: search_table_meta("여신") → 결과 있지만 knowledge_update 생성 실패
라운드 2: search_table_meta("대출") → 유사 결과, knowledge_update 없음
라운드 3-5: 반복, 진전 없음 = LLM 5회 + 도구 5-20회 호출 낭비
```

**격상 근거**: 70B 모델 + 부실한 메타데이터 조합에서 "도구를 호출했지만 유용한 정보를 못 찾는" 케이스가 빈번. 70B 모델 기준 라운드당 3-15초 → 진전 없는 5라운드 = 최대 75초 낭비.

**해결안**: 라운드 간 knowledge_items 변화를 추적하고, 2회 연속 변화 없으면 조기 종료.

```python
prev_knowledge_snapshot = _snapshot_knowledge_state(reason.knowledge_items)
no_progress_count = 0

for round_num in range(max_internal_rounds):
    # ... ReAct step + _apply_knowledge_updates ...

    curr_snapshot = _snapshot_knowledge_state(reason.knowledge_items)
    if curr_snapshot == prev_knowledge_snapshot and decision.action == "call_tools":
        no_progress_count += 1
        if no_progress_count >= 2:
            decision = RecoveryDecision(
                analysis="2회 연속 진전 없음, 탐색 중단",
                action="give_up",
                target_knowledge_gap="no_progress",
            )
            break
    else:
        no_progress_count = 0
    prev_knowledge_snapshot = curr_snapshot


def _snapshot_knowledge_state(items: list[KnowledgeItem]) -> tuple:
    """knowledge_items의 상태 스냅샷 — 변화 감지용."""
    return tuple((ki.key, ki.status, ki.value) for ki in sorted(items, key=lambda x: x.key))
```

**근거**:
- **Pre-Act** (Rawat et al., 2025, arXiv:2505.09970): ReAct 대비 Pre-Act가 Action Recall +70%를 달성한 핵심 요인이 사전 계획에 의한 불필요 반복 방지. progress detection은 사후적으로 동일한 효과.
- **AgentBench** (ICLR 2024): 실패 유형 중 "Task Limit Exceeded(루프 탈출 실패)"가 progress detection 없이 max_rounds만 의존할 때의 전형적 결과.

---

### P1-10. `_parse_recovery_response` fallback에서 tool_calls 복원 불가

- **출처**: 크로스리뷰 5-4 (1차 리뷰 미지적, **P2→P1 격상**)
- **원안 위치**: 02-detailed 4.9절

**문제**: JSON 파싱 실패 시 fallback이 `action`만 추출하고 `tool_calls`, `knowledge_updates`는 빈 리스트로 반환. action이 `"call_tools"`인데 tool_calls가 비어있으면 silent failure.

**격상 근거**: 파인튜닝 없는 70B 모델에서 JSON 파싱 실패는 예외가 아니라 **정상 운영 시나리오**. StructEval 기준 OSS 모델의 구조화 출력 준수율이 상용 대비 ~10점 낮음.

**해결안**: fallback에서 action이 `call_tools`이면 `give_up`으로 전환하고 로그 경고.

```python
def _parse_recovery_response(response: str) -> RecoveryDecision:
    # ... 1차, 2차 시도 ...

    # 3차: Fallback
    action = "give_up"  # 기본값은 안전한 give_up
    if re.search(r'"action"\s*:\s*"ready"', response):
        action = "ready"
    # call_tools는 tool_calls 없이 의미 없으므로 give_up 유지

    logger.warning(
        "recovery LLM 응답 JSON 파싱 실패, fallback 적용",
        action=action,
        response_preview=response[:200],
    )

    return RecoveryDecision(
        analysis=f"LLM 응답 파싱 실패 — fallback action: {action}",
        action=action,
        target_knowledge_gap="parsing_failure",
    )
```

**근거**:
- **StructEval** (Tiger AI Lab, 2025): OSS 모델의 구조화 출력 준수율이 상용 대비 ~10점 낮음.
- Fail-Safe Defaults (Saltzer & Schroeder, 1975): 시스템 실패 시 안전한 기본값으로 복귀.

---

### P1-11. recovery_agent와 readiness_gate 간의 라우팅 계약이 암시적 + 프롬프트 도구 독립성 규칙

- **출처**: 크로스리뷰 5-2, 5-3 프롬프트 부분 통합

**문제**: recovery_agent가 `Phase.VERIFYING`으로 종료 → readiness_gate → REPLAN → recovery_agent 재진입 시, 이전 실행의 tool_results를 잃어버림. 또한 **recovery_agent의 진입 경로**(readiness_gate에서 REPLAN vs sql_validator에서 실패)에 따라 동작이 달라야 하지만, 현재 설계에서는 이를 구분하지 않음.

**해결안**: 진입 경로를 state에 기록.

```python
class ReasoningState(BaseModel):
    recovery_entry_source: Literal["readiness_gate", "sql_validator", None] = None
```

이를 통해 recovery_agent 프롬프트에서 "초기 탐색 부족으로 진입했는지(넓은 탐색 필요), SQL 검증 실패로 진입했는지(특정 문제 해결 필요)"를 LLM에 전달하여 더 정확한 분석 유도.

**근거**:
- **Reflexion** (Shinn et al., 2023, arXiv:2303.11366): "이전 시도의 실패 원인을 명시적으로 다음 시도에 전달하는 것이 성공률을 20-30% 향상"시킴.

---

### P1-12. recovery_rounds 의미 명확화

- **출처**: 크로스리뷰 2-5 (1차 리뷰에서 P0-2의 리셋만 지적)
- **원안 위치**: 02-detailed 1.1절 (37-38행), 4.2절 (350행)

**문제**: `recovery_rounds`는 "recovery_agent 내부 ReAct 루프의 실행 라운드 수"로 정의되었으나, recovery_agent가 여러 번 진입할 경우 누적됨. trace/디버깅 용도라면 누적이 맞지만, 변수명이 "내부 라운드 수"를 시사하므로 의미가 혼동됨.

**해결안**: 두 가지 중 택일.
- **(A)** recovery_agent_node 진입 시 `recovery_rounds = 0`으로 리셋 (현재 진입의 라운드만 추적)
- **(B)** 필드명을 `total_recovery_rounds`로 변경 (전체 누적임을 명시)

---

## 4. [P2] 권장 — 품질/안정성 향상 (5건)

### P2-1. readiness_gate 추론 비중 체크 + 안내 강화

- **출처**: 크로스리뷰 6-4 (1차 리뷰 미지적)

**문제**: 함축적 질의에서 `knowledge_items`의 많은 항목이 도구 증거로 CONFIRMED까지 도달하기 어려움. `confidence_scorer.py`에서 CONFIRMED|PROBABLE을 모두 카운트하므로 PROBABLE이면 통과하지만, P1-3의 추론 경로가 없으면 이 항목들은 UNRESOLVED에 머물러 score가 낮아짐.

P1-3의 추론 경로가 구현되면 score 문제는 해소되지만, readiness_gate에서 **추론 기반 PROBABLE이 많으면 결과에 "추론 사항" 안내를 강화**하는 것을 권장.

```python
inferred_count = sum(
    1 for ki in reason.knowledge_items
    if ki.status == ConfidenceStatus.PROBABLE and ki.is_inferred
)
if inferred_count > 0:
    reason.inference_notes.append(
        f"총 {inferred_count}건의 용어를 관행적 해석으로 추론하였습니다. "
        "결과가 예상과 다를 경우 구체적으로 요청해 주세요."
    )
```

---

### P2-2. Qdrant SQL 이력 검색(`search_use_cases`)의 recovery 경로 조건부 복원

- **출처**: 크로스리뷰 6-5 (1차 리뷰 미지적)

**문제**: `search_use_cases`는 recovery_agent 도구 목록에서 제외되어 있으나(01-strategy 3.3절), Qdrant에 저장된 과거 SQL은 SQL 추론에 가장 직접적인 힌트가 될 수 있다. 특히 MongoDB 메타가 부실한 상황에서, 과거 유사 SQL이 "어떤 테이블을 어떤 조인으로 사용했는지"를 직접 보여줌. planner의 초기 검색과 recovery의 재검색은 **검색어가 다를 수 있음**.

```
planner: search_use_cases("지점별 여신 실행 금액") → 유사 SQL 0건 (너무 구체적)
recovery: search_use_cases("여신 실행") → 유사 SQL 3건 발견 → 한 번에 여러 knowledge 공백 해소
```

**수정안**: recovery_agent 도구 목록에 `search_use_cases`를 조건부 복원. 이전 검색 이력을 프롬프트에 포함하여 동일 검색어 중복 방지.

```python
class ToolCall(BaseModel):
    tool: Literal[
        "search_table_meta", "search_code_meta", "search_manual",
        "search_glossary", "get_sample_rows", "get_date_distribution",
        "search_use_cases",  # 복원
    ]
```

```
# 프롬프트 추가
## 과거 SQL 이력 검색 (search_use_cases)
이전에 검색한 키워드: {', '.join(reason.searched_use_case_queries)}
이미 검색한 키워드와 동일하거나 유사한 검색어는 사용하지 마세요.
```

P2로 분류하지만, 보고서 SQL 저장소가 구현되기 전까지 **Qdrant SQL 이력이 사실상 유일한 참조 SQL 소스**이므로 조기 구현을 권장.

---

### P2-3. force-generate 임계값 문서 간 불일치

- **출처**: 크로스리뷰 5-5

**문제**: 동일한 `THRESHOLD_FORCE_GENERATE` 상수에 대해 원안(0.55)과 리뷰 시나리오(0.40)에서 다른 값 사용. 0.40이면 더 많은 경우에 force-generate가 발동되어 불완전한 SQL 증가, 0.55이면 TERMINATE(실패) 증가.

**해결안**:
1. 실제 `confidence_scorer.py`에서 현재 사용 중인 값을 확인하고 모든 문서를 통일
2. config에서 관리하여 문서 불일치 위험을 근본적으로 제거
3. 임계값의 근거를 골든셋 실험으로 검증

---

### P2-4. EXPLORE verdict에서 knowledge_fetcher 재진입의 실효성

- **출처**: 1차 리뷰 P2-4 → 크로스리뷰 4-8에서 확인

**문제**: PENDING 스텝이 없는 상태에서 knowledge_fetcher 재진입 시, 상태 변화 없이 knowledge_interpreter로 전달 → 동일 해석 → 다시 EXPLORE → 무한 루프 가능.

**코드 검증**: `evaluate_readiness()`에서 PENDING 스텝이 남아있으면 EXPLORE를 반환하므로 논리적으로는 실행할 스텝이 있어야 함. 그러나 방어적 가드 추가가 엣지 케이스 방지에 적절.

**해결안**:

```python
# readiness_gate.py
if verdict == ReadinessVerdict.EXPLORE:
    pending_steps = [s for s in reason.execution_plan if s.status == StepStatus.PENDING]
    if reason.exploration_phase == "initial" and not pending_steps:
        reason.exploration_phase = "recovery"
        verdict = ReadinessVerdict.REPLAN  # recovery_agent로 전환
```

---

### P2-5. 70B 모델 전용 `max_internal_rounds` 설정

- **출처**: 1차 리뷰 P2-1 → 크로스리뷰 3-1에서 원안 우선 판정

**크로스리뷰 판정**: 1차 리뷰의 `degraded_mode`(모델별 `RECOVERY_MAX_ROUNDS` 딕셔너리 + 모델명 하드코딩)보다 **원안의 단일 config 기반 `max_internal_rounds`가 더 적절**.

원안에서 이미 `NODE_THINKING_MODES`를 config 기반으로 관리하고 있으므로, `max_internal_rounds`도 동일하게 config로 관리하면 충분. 모델별 코드 분기 없이 폐쇄망 배포 시 config 변경만으로 조절 가능.

```python
# config.py
RECOVERY_MAX_INTERNAL_ROUNDS: int = 5  # 환경별 설정 — 폐쇄망에서는 2-3으로 조절
```

> **참고 (1차 리뷰 제안)**: 1차 리뷰에서는 모델별 `RECOVERY_MAX_ROUNDS` 딕셔너리 + `model_family == "solar_pro_2"` 전용 강제 ready 로직을 제안했으나, 프로젝트 원칙("설정파일 변경만으로 전환 가능하도록 설계")에 부합하지 않는다. Solar Pro 2 → Qwen3.5 → GPT OSS 전환 시 코드 변경 없이 config만 조정하면 된다.

**근거**:
- **Pre-Act** (arXiv:2505.09970): Llama 3.1 70B fine-tuned가 GPT-4 대비 Action Accuracy +69.5%. 70B도 적절한 프롬프트와 구조화로 충분히 작동할 수 있으며, 모델별 하드코딩 분기보다 config 기반 조정이 유지보수에 유리.

> **참고 (LLM 호출 효율 비교)**: 1차 리뷰 P2-2에서 "단일 공백 시 신규가 약간 열위인 점을 문서에 명시"를 요청했으나, 크로스리뷰 3-2 판정에 따라 원안 서술이 핵심을 정확히 전달하고 있으므로 별도 문서 보완은 불필요. 다만 원안에 "단일 공백 시에는 동등~약간 열위" 한 문장 추가를 권장 (minor).

---

## 5. 라우팅 흐름 전체 요약 (수정 반영)

```
planner (exploration_phase/recovery_rounds 리셋 — P0-2)
  ├─ fast_path → sql_generator
  └─ else → knowledge_fetcher → knowledge_interpreter → readiness_gate
       ├─ GENERATE → sql_generator
       ├─ EXPLORE (PENDING 스텝 있음) → knowledge_fetcher (재진입)
       ├─ EXPLORE (PENDING 스텝 없음) → recovery_agent  [P2-4 가드]
       ├─ REPLAN → recovery_agent
       ├─ ASK_USER (추론 불가 충돌만) → clarification_handler  [P1-1 기준 변경]
       └─ TERMINATE → result_finalizer

recovery_agent (병렬 도구 실행 — P0-5, 진전 감지 — P1-9)
  ├─ ready → sql_generator (inference_notes 기록 — P1-1)
  ├─ give_up + score ≥ threshold → sql_generator (force-generate)  [P0-1]
  ├─ give_up + score < threshold → result_finalizer (실패)         [P0-1]
  ├─ decision=None → result_finalizer (실패)                       [P0-4]
  └─ CONFLICTED 발견 → readiness_gate → 추론 or ASK_USER

sql_generator → sql_validator
  ├─ PASS → result_finalizer
  ├─ SYNTAX (retry 가능) → sql_generator
  ├─ SEMANTIC_LOCAL (fix 가능) → sql_generator
  ├─ SEMANTIC_LOCAL (fix 초과) → recovery_agent (entry_source=sql_validator)
  ├─ STRUCTURAL/EMPTY/DB_ERROR → recovery_agent (entry_source=sql_validator)
  └─ fast_path 실패 → knowledge_fetcher (exploration_phase=initial)
```

---

## 6. 정확도 관점 종합 평가

### 현재 설계의 정확도 병목 (프로덕션 환경 기준)

```
사용자 질의 → [함축적 용어 해석] → [메타 검색] → [SQL 생성] → [검증] → 결과

      ↑ 병목 1              ↑ 병목 2         ↑ 병목 3
  관행적 추론 경로 없음   부실한 메타로      70B의 ReAct
  → ASK_USER 과다        도구 결과 빈약    라운드 증가
                        → recovery 루프 낭비  → 판단 오류 증가
```

### 수정안이 해소하는 영향

| 병목 | 관련 수정안 | 기대 효과 |
|------|-----------|----------|
| 병목 1: 함축적 용어 | P1-1(선 추론 정책), P1-3(추론 경로) | ASK_USER 빈도 50% 이상 감소, 대화 턴 절약 |
| 병목 2: 부실 메타 | P1-2(도구 우선순위), P2-2(SQL 이력 복원) | 유효하지 않은 도구 호출 감소, 참조 SQL 활용 |
| 병목 3: 70B ReAct | P0-5(병렬화), P1-9(진전 감지), P1-10(파싱 안전) | ReAct 라운드 감소, 비정상 종료 방지 |

### 최종 판단

설계의 기본 구조(2-Phase Exploration, Phase 1 기계적 분리, Structured Output, Hypothesis 관리)는 **프로덕션 환경에서도 유효**하다. 단, 위의 수정안 없이 구현하면:

1. **불필요한 ASK_USER**가 빈번하여 사용자 경험 저하 (대부분의 모호한 질의에서 질문)
2. **recovery 루프에서 비효과적 도구 호출**로 응답 시간 증가 (search_manual, search_glossary 낭비)
3. **70B 모델의 ReAct 불안정성**이 증폭되어 give_up 비율 증가 (진전 감지/파싱 안전 부재)

수정안이 반영되면, **함축적 질의 → 합리적 추론 → SQL 생성 → 추론 근거 표시**의 단축 경로가 확보되어, 프로덕션 환경에서의 정확도와 응답 속도 모두 의미 있게 개선될 것으로 판단.

---

## 7. 구현 우선순위 체크리스트

### P0 — 구현 전 설계 보완 필수 (5건)

| # | 항목 | 출처 | 핵심 근거 |
|---|------|------|----------|
| 1 | give_up 시 즉시 종료 + force-generate 내부 판정 | P0-1 | 무한 루프 차단, "선 추론" 정책 연계 |
| 2 | planner_node에서 exploration_phase/recovery_rounds 리셋 | P0-2 | 멀티턴 필수 |
| 3 | _execute_single_tool 어댑터 kwargs→문자열 변환 | P0-3 | 코드 직접 검증 |
| 4 | decision=None 시 즉시 종료 (FAILURE) | P0-4 | 70B 파싱 실패율 높음 |
| 5 | 도구 실행 병렬화 (asyncio.gather) + 프롬프트 독립성 규칙 | P0-5 | ReAct 라운드 감소 → 70B 판단 오류 감소 |

### P1 — 구현 초기 반영 권장 (12건)

| # | 항목 | 출처 | 핵심 근거 |
|---|------|------|----------|
| 5 | "선 추론 후 표시" 정책 — ASK_USER 기준 변경 + inference_notes 채널 | P1-1 | 정확도 + UX 핵심 |
| 6 | 도구 우선순위 가이드 + 빈 결과 도구 피드백 | P1-2 | 참조 저장소 한계 대응 |
| 7 | 함축적 용어의 관행적 추론 경로 (is_inferred 플래그) | P1-3 | 대부분의 프로덕션 질의가 함축적 |
| 8 | last_verdict 필드 추가, 라우팅에서 직접 참조 | P1-4 | PEP 20 |
| 9 | knowledge_item ID 기반 참조 전환 | P1-5 | 금융 도메인 접미어 공유 빈번 |
| 10 | PROMOTION_ORDER에 CANDIDATE 추가 | P1-6 | 코드 직접 검증 |
| 11 | discovered_facts 경로 + CANDIDATE 프롬프트 포함 | P1-7 | sql_generator 연계 |
| 12 | KnowledgeUpdate CANDIDATE 포함 + search_use_cases 제거 | P1-8 | 시스템 일관성 |
| 13 | 진전 감지 (2회 연속 무변화 → 조기 종료) | P1-9 | 부실 메타 환경 비용 효율 |
| 14 | fallback 파싱 안전성 강화 | P1-10 | 70B JSON 불안정 |
| 15 | recovery_entry_source 필드 추가 | P1-11 | 70B 맥락 제공 |
| 16 | recovery_rounds 의미 명확화 | P1-12 | 가독성 |

### P2 — 품질/안정성 향상 (5건)

| # | 항목 | 출처 | 핵심 근거 |
|---|------|------|----------|
| 17 | readiness_gate 추론 비중 체크 + 안내 강화 | P2-1 | 추론 기반 응답의 투명성 |
| 18 | search_use_cases 조건부 복원 (recovery 경로) | P2-2 | 유일한 참조 SQL 소스 |
| 19 | force-generate 임계값 통일 + config 관리 | P2-3 | 문서 일관성 |
| 20 | EXPLORE verdict PENDING 스텝 가드 | P2-4 | 방어적 프로그래밍 |
| 21 | max_internal_rounds config 기반 관리 (degraded_mode 불채택) | P2-5 | 폐쇄망 배포 원칙 부합 |

---

## 8. 참고 문헌

| ID | 제목 | 저자 | 연도 | 출처 |
|----|------|------|------|------|
| T1 | Pre-Act: Multi-Step Planning and Reasoning Improves Acting in LLM Agents | Rawat et al. (Uniphore) | 2025 | arXiv:2505.09970 |
| T2 | AgentBench: Evaluating LLMs as Agents | Xiao Liu et al. (Tsinghua) | 2024 | ICLR 2024, arXiv:2308.03688 |
| T3 | Why Do Multi-Agent LLM Systems Fail? (MAST) | Cemri, Pan, Yang et al. (UC Berkeley) | 2025 | NeurIPS 2025, arXiv:2503.13657 |
| T4 | JSONSchemaBench: A Rigorous Benchmark of Structured Outputs | Guidance-AI (Microsoft Research) | 2025 | arXiv:2501.10868 |
| T5 | StructEval: Benchmarking LLMs' Structured Output Capabilities | Tiger AI Lab | 2025 | arXiv:2505.20139 |
| T6 | Solving LLM Repetition Problem in Production | — | 2024 | arXiv:2512.04419 |
| T7 | Solving Context Window Overflow in AI Agents | Labate et al. (IBM Research Brazil) | 2025 | arXiv:2511.22729 |
| T8 | The Complexity Trap: Simple Observation Masking | — | 2025 | arXiv:2508.21433 |
| T9 | Learning Latency-Aware Orchestration for Parallel Multi-Agent Systems | — | 2025 | arXiv:2601.10560 |
| T10 | CHESS: Contextual Harnessing for Efficient SQL Synthesis | Talaei, Pourreza et al. (Stanford) | 2024 | arXiv:2405.16755 |
| T11 | MAC-SQL: Multi-Agent Collaborative Framework for Text-to-SQL | Wang et al. | 2025 | COLING 2025, arXiv:2312.11242 |
| T12 | DIN-SQL: Decomposed In-Context Learning of Text-to-SQL | Pourreza & Rafiei | 2023 | NeurIPS 2023, arXiv:2304.11015 |
| T13 | Lost in the Middle: How Language Models Use Long Contexts | Liu et al. (Stanford) | 2023 | — |
| T14 | Reflexion: Language Agents with Verbal Reinforcement Learning | Shinn et al. | 2023 | arXiv:2303.11366 |
| T15 | Chain-of-Table: Evolving Tables in the Reasoning Chain | Wang et al. | 2024 | arXiv:2401.04398 |
| B1 | Berkeley Function Calling Leaderboard (BFCL) | Patil, Mao et al. (UC Berkeley) | 지속 | gorilla.cs.berkeley.edu |
| R1 | LangGraph 공식 문서 | LangChain | 2024 | — |
