# 통합 검토 보고서: Agentic Recovery Loop 재설계 전략 vs 설계 리뷰

- **검토 대상**:
  - 원안: `docs/strategy-proposals/agentic-recovery-redesign/01-strategy.md`, `02-detailed-design.md`
  - 리뷰: `docs/reviews/design/20260401-agentic-recovery-redesign-review.md`
- **검토 이력**:
  - 1차 교차 검토: 2026-04-01 — 원안과 리뷰를 비판적으로 재검토, 연구 근거 기반 교차 비교
  - 2차 프로덕션 재검토: 2026-04-01 — 실제 프로덕션 환경(참조 저장소 제약, LLM 능력, 사용자 질의 패턴) 관점에서 정확도 실효성 중심 재검토
- **코드 검증 기준**: `main` 브랜치 (`6491f9b`) — 코드베이스 직접 대조 완료

---

## 총평

본 문서는 원안·리뷰에 대한 1차 교차 검토와, 프로덕션 환경 관점의 2차 재검토를 통합한 최종 보고서다. **프로덕션 환경의 세 가지 핵심 제약이 설계 전반의 판단 기준**이 된다:

1. **참조 저장소의 실질적 한계** — Qdrant의 상품설명서/업무매뉴얼은 SQL 추론에 직접적 힌트가 아님, MongoDB 비즈용어사전은 200개 미만으로 부실, 보고서 SQL/골든셋은 아직 없음
2. **70B~397B LLM의 ReAct 능력 한계** — 파인튜닝 없는 상태에서 복잡한 구조화 출력의 안정성이 보장되지 않음
3. **함축적 사용자 질의 + "선 추론 후 표시" 정책** — 대부분 명확화 질문 없이 추론으로 진행해야 함

원안의 문제 진단(context_explorer 과부하, recovery_planner 실행 단절, 도구 일괄 전용)과 2-Phase Exploration 분리 전략은 NL-to-SQL 선행 연구(CHESS, DIN-SQL, MAC-SQL)의 "컨텍스트 수집과 SQL 생성의 완전 분리" 원칙과 정확히 부합하며, **프로덕션 환경에서도 유효**하다. 리뷰(P0 3건, P1 4건, P2 4건)는 대부분 타당하나, 일부 제안(degraded_mode, LLM 효율 문서화)은 과도하며, 리뷰가 놓친 결함(CANDIDATE 상태 프롬프트 누락, fallback 파싱 안전성, 진전 감지 부재)이 존재한다.

위 프로덕션 제약을 감안하면, 1차 교차 검토의 20건 중 **14건은 그대로 유효**, **3건은 우선순위 격상이 필요**(도구 병렬화 P1→P0, 진전 감지 P2→P1, fallback 파싱 P2→P1), **3건은 프로덕션 환경에 맞게 보완/재설계 필요**하며, **프로덕션 환경에서 발견된 신규 이슈 5건**이 추가된다.

**최종 심각도 분포**: P0 5건 / P1 12건 / P2 5건 (총 22건)

| 분류 | 건수 | 비고 |
|------|------|------|
| 1장: 원안대로 적용 | 8건 | 프로덕션 환경에서도 유효 |
| 2장: 리뷰 누락, 수정 필요 | 5건 | 70B 환경에서 영향 증가 |
| 3장: 원안이 더 적절 | 2건 | config 기반 관리가 폐쇄망 원칙에 부합 |
| 4장: 리뷰 타당, 수정 필요 | 8건 | 코드 직접 검증 완료 |
| 5장: 둘 다 미흡, 보완 필요 | 5건 | **3건 프로덕션 기준 격상** |
| 6장: 프로덕션 신규 이슈 | 5건 | 정확도·UX에 직접적 영향 |

---

## 1. 비판적 검토가 없었고, 원안대로 적용하면 될 내용 (8건)

**프로덕션 재평가**: 8건 모두 프로덕션 환경에서도 유효. 환경 불문으로 적용 가능한 기본 설계.

### 1-1. Phase 1 기계적 분리 (knowledge_fetcher + knowledge_interpreter)

- **원안 위치**: 01-strategy 3.2절, 02-detailed 2.1-2.2절
- **리뷰 평가**: S-1 "보존 권장", 별도 이슈 미제기

**판정**: 원안대로 적용. context_explorer의 Phase 1-2(순수 I/O)와 Phase 3-6(LLM 해석) 경계는 코드에서도 명확히 확인됨 — 도구 실행이 290행에서 완료되고 LLM 해석이 297행에서 시작하는 깔끔한 분리점 존재. `_run_step()`, `_should_skip_step()` 등은 독립 함수로 이미 구현되어 있어 기계적 추출이 안전.

**근거**:
- **CHESS** (Talaei et al., Stanford, 2024, arXiv:2405.16755): 4단계 분리 파이프라인(IR→SS→CG→UT)에서 스키마 프루닝만으로 **LLM 토큰 5배 감소 + 정확도 2% 향상**. "컨텍스트 수집과 SQL 생성의 완전 분리가 핵심"이라는 결론이 본 원안의 분리 전략과 정확히 일치.
- **DIN-SQL** (Pourreza & Rafiei, NeurIPS 2023, arXiv:2304.11015): 태스크 분해 → 서브태스크 few-shot → 자기교정 패턴으로 단순 few-shot 대비 일관되게 ~10% 향상.

---

### 1-2. Hypothesis 상태 전이의 Python 코드 수행

- **원안 위치**: 01-strategy 검토 2, 3.3절
- **리뷰 평가**: S-2 "보존 권장"

**판정**: 원안대로 적용. 상태 전이를 deterministic Python 코드로 수행하는 결정은 올바름.

**근거**:
- **AgentBench** (Liu et al., Tsinghua, ICLR 2024, arXiv:2308.03688): 29개 모델 실험에서 70B 이하 OSS 모델이 "Poor long-term reasoning, decision-making, and instruction following"으로 복잡한 상태 전이에서 실패율이 높음을 확인. 상태 관리를 LLM에 위임하면 안 됨.
- **ReAct** (Yao et al., 2023, arXiv:2210.03629) 원논문: observation parsing과 state tracking은 환경(코드)이 수행하고 LLM은 thought+action만 생성하는 역할 분리가 기본 전제.

---

### 1-3. Structured Output 채택 (native tool-calling 기각)

- **원안 위치**: 01-strategy 3.3절
- **리뷰 평가**: S-3 "보존 권장"

**판정**: 원안대로 적용. 폐쇄망 모델 호환의 최소 공통분모.

**근거**:
- **JSONSchemaBench** (Guidance-AI/Microsoft Research, 2025, arXiv:2501.10868): 10K 실세계 JSON 스키마 벤치마크에서 제약 디코딩이 비제약 대비 **생성 속도 50% 향상, 다운스트림 정확도 최대 4% 향상**.
- **BFCL** (UC Berkeley, 지속 업데이트): Qwen 3 14B가 F1 0.971로 GPT-4 수준이나, "memory, dynamic decision-making, long-horizon reasoning은 미해결". 단순 function-calling은 가능하나 멀티턴 에이전틱 사용은 불안정.
- **StructEval** (Tiger AI Lab, 2025): OSS 모델의 구조화 출력 준수율이 상용 모델 대비 ~10점 낮음.

---

### 1-4. CONFLICTED 처리의 외부 위임

- **원안 위치**: 01-strategy 검토 6
- **리뷰 평가**: S-4 "보존 권장"

**판정**: 원안대로 적용하되, ASK_USER 발동 기준은 프로덕션 정책에 맞게 조정 필요.

CONFLICTED를 recovery_agent 내부에서 `interrupt()`로 처리하면 LangGraph의 ReAct 루프 상태 직렬화가 복잡해지는 실질적 위험이 있으므로, 외부 위임 자체는 적절하다. 그러나 프로덕션 환경에서는 **"선 추론 후 표시" 정책**이 적용되어, 대부분의 CONFLICTED 상황에서는 ASK_USER 대신 합리적 추론으로 진행하고 결과에 추론 근거를 표시해야 한다. ASK_USER는 "추론으로도 해결 불가능한" 경우(테이블 선택 충돌, 산출식 충돌 등)에만 발동되어야 하므로, readiness_gate의 ASK_USER 기준 변경이 필수 (6장 6-1 참조).

**근거**:
- **LangGraph 공식 문서** (2024): LangGraph는 FSM/지향 그래프로 에이전트 워크플로우를 명시적으로 모델링하며, 내부 루프에서의 interrupt는 체크포인트 직렬화와 충돌할 수 있음을 명시.

---

### 1-5. Truncation 전략의 티어별 설계

- **원안 위치**: 01-strategy 검토 3
- **리뷰 평가**: S-5 "보존 권장"

**판정**: 원안대로 적용. confirmed_knowledge 전량 포함, REJECTED 제외, tool_results 최근 1라운드만 유지하는 전략이 8-16K 윈도우에 실용적.

**근거**:
- **Complexity Trap** (2025, arXiv:2508.21433): "simple observation masking이 LLM 요약과 동등한 solve rate를 달성하면서 비용은 절반". 원안의 "이전 tool_results는 최근 1라운드만 유지" 전략이 이 연구의 마스킹 접근과 일치.
- **IBM Context Window Overflow** (Labate et al., 2025, arXiv:2511.22729): 메모리 포인터 방식으로 원시 데이터 대신 포인터를 컨텍스트에 유지하여 **토큰 사용량 7배 감소**.
- **Lost in the Middle** (Liu et al., 2023, Stanford): LLM은 컨텍스트의 시작과 끝에 집중하며 중간 정보 활용도가 낮음. 핵심 정보를 전량 포함하고 비핵심을 제거하는 전략이 적절.

---

### 1-6. Fast-path 보존 및 fast-path 실패 → knowledge_fetcher 라우팅

- **원안 위치**: 01-strategy 검토 5
- **리뷰 평가**: 별도 이슈 미제기

**판정**: 원안대로 적용. fast-path 실패 시 초기 컨텍스트 자체가 없으므로 Phase 1부터 시작하는 것이 논리적으로 정확. 02-detailed 5.2절의 `_route_after_sql_validator`에서 `exploration_phase = "initial"` 설정도 올바름.

---

### 1-7. 테스트 전략 — 단일 스텝 독립 함수 추출

- **원안 위치**: 01-strategy 검토 7, 02-detailed 8절
- **리뷰 평가**: 별도 이슈 미제기

**판정**: 원안대로 적용. `_recovery_step()`, `_handle_hypothesis_transition()`, `_apply_knowledge_updates()`를 독립 함수로 추출하여 단위 테스트 가능하게 하는 설계가 올바름.

**근거**:
- **MAC-SQL** (Wang et al., COLING 2025, arXiv:2312.11242)에서도 Selector, Decomposer, Refiner 각 에이전트를 독립적으로 테스트 가능하게 설계하여 디버깅 효율을 향상.

---

### 1-8. 마이그레이션의 Step 1→2→3→4 단계별 실행

- **원안 위치**: 01-strategy 7.1절
- **리뷰 평가**: 별도 이슈 미제기

**판정**: 원안대로 적용. behavioral change 없는 Step 1-2를 먼저 수행하고, 핵심 변경인 Step 3을 이후에 수행하는 것은 리스크 격리에 효과적. Step 1 완료 기준(기존 e2e 테스트 수정 없이 통과)도 명확.

---

## 2. 비판적 검토가 없었으나 수정이 필요한 내용 (5건)

### 2-1. `_build_recovery_prompt()`에서 CANDIDATE 상태 항목 누락

- **원안 위치**: 02-detailed 4.8절 (578-658행)
- **리뷰 관련**: P1-3에서 `PROMOTION_ORDER`의 CANDIDATE 누락만 지적, 프롬프트에서의 누락은 미지적

**문제**: `_build_recovery_prompt()`에서 `confirmed`, `probable`, `unresolved`, `conflicted`만 필터링하고 **`CANDIDATE` 상태의 knowledge_items는 어디에도 포함되지 않음**.

```python
# 02-detailed 594-605행
confirmed = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.CONFIRMED]
probable = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.PROBABLE]
# ...
unresolved = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.UNRESOLVED]
conflicted = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.CONFLICTED]
# CANDIDATE는 누락됨
```

**코드 검증**: `ConfidenceStatus.CANDIDATE`는 `src/models/enums.py:72`에 실제 존재하며, `context_explorer.py:562`에서 CANDIDATE 상태로 knowledge_item을 생성하고, `confidence_scorer.py:163`에서 점수 계산에 사용됨. recovery_agent가 CANDIDATE 항목을 프롬프트에서 보지 못하면 이미 단일 출처에서 확인된 항목을 중복 탐색하게 됨.

**수정안**: CANDIDATE를 PROBABLE과 함께 "확인된 지식" 섹션에 포함. `knowledge_id`를 표시하여 LLM이 ID로 참조할 수 있게 함 (4-5 참조).

```python
candidate = [ki for ki in reason.knowledge_items if ki.status == ConfidenceStatus.CANDIDATE]
for ki in candidate:
    lines.append(f"- [{ki.knowledge_id}] [후보] {ki.key}: {ki.value} (단일 출처)")
```

**근거**:
- **Chain-of-Table** (Wang et al., 2024, arXiv:2401.04398): 중간 상태의 정보를 프롬프트에 포함하는 것이 최종 정확도를 5-10% 향상시킴. 부분적으로 확인된 정보를 숨기면 LLM이 동일한 탐색을 반복하여 비효율 발생.

---

### 2-2. `KnowledgeUpdate.new_status`에 CANDIDATE 미포함

- **원안 위치**: 02-detailed 1.2절 (70-75행)
- **리뷰 관련**: 미지적

**문제**: `KnowledgeUpdate`의 `new_status`가 `Literal["PROBABLE", "CONFIRMED", "CONFLICTED"]`로 정의되어 CANDIDATE로의 승격을 허용하지 않음. 실제 시스템에서 CANDIDATE는 "단일 출처 확인" 상태로, recovery_agent가 하나의 도구 결과만으로 확인한 경우 PROBABLE보다 CANDIDATE가 더 정확한 표현.

**수정안**:

```python
class KnowledgeUpdate(BaseModel):
    new_status: Literal["CANDIDATE", "PROBABLE", "CONFIRMED", "CONFLICTED"]
```

---

### 2-3. `_finalize_recovery`에서 `decision is None` 케이스의 미흡한 처리

- **원안 위치**: 02-detailed 4.10절 (705-712행)
- **리뷰 관련**: P0-1에서 give_up 시 무한 루프만 지적, None 케이스는 미지적

**문제**: `decision is None`이 되는 케이스는 ReAct 루프가 `should_terminate()` 때문에 한 번도 실행되지 않았을 때 발생. 이 경우 `Phase.VERIFYING`으로 설정하여 readiness_gate로 보내는데, readiness_gate에서 REPLAN이 나오면 다시 recovery_agent로 돌아와 동일 상황이 반복. **프로덕션 환경에서는 70B 모델의 파싱 실패율이 높아 `decision is None` 도달 확률이 상용 모델 대비 훨씬 높으므로**, 즉시 종료하지 않으면 무한 루프 위험이 크게 증가한다.

**수정안**: `decision is None`일 때 `should_terminate()` true이므로 즉시 종료.

```python
def _finalize_recovery(reason, decision):
    if decision is None:
        reason.phase = Phase.DONE
        reason.final_status = FinalStatus.FAILURE
        return
    # ... 나머지
```

**근거**:
- **MAST** (Cemri et al., UC Berkeley, NeurIPS 2025, arXiv:2503.13657): 1,600개 trace 분석에서 "lack of termination criteria"가 System Design Issues의 직접 원인으로 분류. None 상태에서 재진입을 허용하는 것은 이 실패 모드에 해당.

---

### 2-4. `_execute_single_tool` 어댑터에서 `search_use_cases` 포함 오류

- **원안 위치**: 02-detailed 7.2절 (1010행)
- **리뷰 관련**: P0-3에서 kwargs 형식 불일치만 지적, 도구 목록 불일치는 미지적

**문제**: `_execute_single_tool`의 조건문에 `"search_use_cases"`가 포함되어 있으나, 01-strategy 3.3절에서 명시적으로 "recovery_agent의 도구 목록에서 `search_use_cases`는 제외"라고 기술. `ToolCall.tool`의 Literal 타입에도 `search_use_cases`가 없음. 도달 불가능한 코드. 단, 프로덕션 환경에서는 Qdrant SQL 이력이 사실상 유일한 참조 SQL 소스이므로 `search_use_cases`의 recovery 경로 조건부 복원이 향후 필요할 수 있다 (6장 6-5 참조). **현시점에서는 도달 불가능 코드를 제거하되, 6-5 구현 시 복원하는 단계적 접근**을 권장한다.

**수정안**: `_execute_single_tool`의 조건에서 `search_use_cases` 제거.

```python
# 변경 전
if tool_name in ("search_table_meta", "search_use_cases", "search_manual", "search_glossary"):

# 변경 후
if tool_name in ("search_table_meta", "search_manual", "search_glossary"):
```

---

### 2-5. recovery_agent 재진입 시 `recovery_rounds` 미리셋

- **원안 위치**: 02-detailed 1.1절 (37-38행), 4.2절 (350행)
- **리뷰 관련**: P0-2에서 `exploration_phase` 리셋만 지적

**문제**: `recovery_rounds`는 "recovery_agent 내부 ReAct 루프의 실행 라운드 수"로 정의되었으나, recovery_agent가 여러 번 진입할 경우(sql_validator 실패 → recovery → sql_generator → sql_validator 재실패 → recovery) 누적됨. 4.2절의 `reason.recovery_rounds += 1`이 매 라운드 증가하므로, 두 번째 recovery 진입 시 이전 값부터 시작.

trace/디버깅 용도라면 누적이 맞지만, 변수명이 "내부 라운드 수"를 시사하므로 의미가 혼동됨.

**수정안**: 두 가지 중 택일.
- (A) recovery_agent_node 진입 시 `recovery_rounds = 0`으로 리셋 (현재 진입의 라운드만 추적)
- (B) 필드명을 `total_recovery_rounds`로 변경 (전체 누적임을 명시)

---

## 3. 비판적 검토가 있었으나 원안이 더 좋아 보이는 내용 (2건)

### 3-1. P2-1: 70B 모델 전용 `degraded_mode` — 원안의 단일 `max_internal_rounds` 유지가 더 적절

- **리뷰 제안**: 모델별 `RECOVERY_MAX_ROUNDS` 딕셔너리 + `model_family == "solar_pro_2"` 전용 강제 ready 로직
- **원안**: `max_internal_rounds = 5` (단일 설정)

**판정**: 원안이 더 나음.

리뷰의 `degraded_mode`는 모델별 분기를 코드에 직접 넣어 복잡도를 높임.

```python
# 리뷰 제안 — 문제점: 모델명 하드코딩
RECOVERY_MAX_ROUNDS = {
    "claude": 5,
    "solar_pro_2": 2,  # 모델 교체 시 코드 수정 필요
    "qwen3.5": 4,
}
if round_num >= max_internal_rounds - 1 and model_family == "solar_pro_2":
    decision.action = "ready"  # 모델별 분기 코드
```

원안에서 이미 `NODE_THINKING_MODES`를 config 기반으로 관리하고 있으므로, `max_internal_rounds`도 동일하게 config로 관리하면 충분. 모델별 코드 분기 없이 폐쇄망 배포 시 config 변경만으로 조절 가능.

**더 나은 접근**:

```python
# config.py
RECOVERY_MAX_INTERNAL_ROUNDS: int = 5  # 환경별 설정 — 폐쇄망에서는 2-3으로 조절
```

프로덕션 재검토에서도 이 판단이 재확인되었다. 폐쇄망 배포에서 **설정 파일 변경만으로 전환**해야 한다는 프로젝트 원칙에 정확히 부합하며, Solar Pro 2 → Qwen3.5 → GPT OSS 전환 시 코드 변경 없이 config만 조정하면 된다.

**근거**:
- **Pre-Act** (Rawat et al., 2025, arXiv:2505.09970): Llama 3.1 70B fine-tuned가 GPT-4 대비 Action Accuracy +69.5%, Goal Completion Rate +28%. 70B도 적절한 프롬프트와 구조화로 충분히 작동할 수 있으며, 모델별 하드코딩 분기보다 config 기반 조정이 유지보수에 유리.
- 프로젝트 CLAUDE.md의 배포 컨텍스트: "설정파일 변경만으로 전환 가능하도록 설계"라는 원칙에도 부합.

---

### 3-2. P2-2: LLM 호출 효율 비교의 트레이드오프 문서화 요구 — 원안의 서술이 이미 충분

- **리뷰 제안**: 단일 공백에서 신규가 약간 열위인 점을 문서에 명시
- **원안**: 01-strategy 검토 1에서 "현행 2회/cycle × 3 cycle = 6회" vs "신규 2-3회로 3개 공백 해소"

**판정**: 원안 서술이 핵심을 정확히 전달하고 있음.

리뷰가 제시한 PENDING hypothesis 존재 시 최선 경로(cycle당 LLM 1회)는 맞지만, 이는 **이미 hypothesis가 실행 가능한 plan을 포함한 특수 케이스**. 대부분의 recovery 시나리오에서는 LLM 호출이 필요하므로 원안의 비교가 더 대표적.

다만 원안에 "단일 공백 시에는 동등~약간 열위" 한 문장 추가를 권장 (minor).

---

## 4. 비판적 검토가 타당해서 수정이 필요한 내용 (8건)

### 4-1. [P0-1] `_finalize_recovery` give_up 시 무한 루프 — 수정 필수

- **리뷰 지적**: 01-strategy(즉시 종료, 306-308행)와 02-detailed(readiness_gate 재진입, 710행)의 충돌. give_up 후 상태 변경 없이 REPLAN으로 3회 반복.
- **리뷰 해결안**: `recovery_gave_up` boolean 플래그 추가

**판정**: 지적은 100% 타당. 해결안은 개선 가능.

리뷰의 `recovery_gave_up` 플래그는 동작하지만, **01-strategy의 즉시 종료 방식을 채택하되 force-generate 판정을 recovery_agent 내부에서 수행**하는 것이 추가 state 필드 없이 더 깔끔:

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

이 방식이면 force-generate SSOT를 recovery_agent가 직접 판정하여 readiness_gate 재진입을 완전 차단. `recovery_gave_up` 필드 추가도 불필요. 프로덕션 환경에서는 이 해결안이 특히 중요한데, **"선 추론 후 표시" 정책**에 따라 give_up이어도 score가 일정 수준이면 추론 기반으로 SQL을 생성하고 결과에 주의사항을 표시해야 하며, 70B 모델에서 give_up 빈도가 더 높으므로 이 경로의 안정성이 정확도에 직접적 영향을 준다.

**근거**:
- **MAST** (Cemri et al., UC Berkeley, NeurIPS 2025, arXiv:2503.13657): 1,600개 trace 분석에서 **"lack of termination criteria"가 System Design Issues의 직접 원인**으로 분류.
- **LLM Repetition Problem** (2024, arXiv:2512.04419): 이론적 증명 — "once the model enters a repetitive state, the expected escape time is infinite under greedy decoding". 외부 종료 메커니즘 필수.

---

### 4-2. [P0-2] `exploration_phase` 리셋 시점 미정의 — 수정 필수

- **리뷰 지적**: 멀티턴에서 이전 질의의 `"recovery"` 잔류로 2번째 질의가 recovery_agent로 잘못 라우팅
- **리뷰 해결안**: planner_node 진입 시 `exploration_phase = "initial"` 리셋

**판정**: 완전 타당. 해결안도 적절.

**코드 검증**: `exploration_phase`는 `ReasoningState`의 신규 필드로, 리셋 경로가 fast-path 실패 시(`02-detailed 816행`) 하나뿐인 것을 확인. 멀티턴 세션에서 상태 초기화가 없으면 확실히 문제.

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

### 4-3. [P0-3] execute_tool 어댑터와 tool_input 형식 불일치 — 수정 필수

- **리뷰 지적**: `json.dumps(tc.kwargs)`를 전달하면 쉼표 구분 파서가 깨짐
- **리뷰 해결안**: 도구별 kwargs → 문자열 변환 어댑터

**판정**: 코드 직접 확인으로 100% 타당함을 검증.

```python
# 실제 tools.py 259-263행
async def _tool_get_sample_rows(tool_input: str) -> Any:
    parts = [p.strip() for p in tool_input.split(",")]
    table_name = parts[0] if parts else ""
    return await get_sample_rows(table_name)
```

`json.dumps({"table_name": "TB_LOAN_INFO", "limit": "5"})` → `'{"table_name": "TB_LOAN_INFO", "limit": "5"}'`를 `.split(",")` 하면 `['{"table_name": "TB_LOAN_INFO"', '"limit": "5"}']`가 되어 table_name이 `{"table_name": "TB_LOAN_INFO"`가 됨.

리뷰의 도구별 어댑터 해결안이 정확. 추가로 **`execute_tool` 시그니처를 kwargs 기반으로 변경하는 것을 Step 3과 병합하여 수행**하면 어댑터 자체가 불필요해지므로 더 깔끔.

---

### 4-4. [P1-1] Phase 기반 라우팅에서 ReadinessVerdict 정보 소실 — 수정 필요

- **리뷰 지적**: `VERDICT_TO_PHASE` 변환 후 Phase만으로 라우팅하면 verdict 구분이 어려움
- **리뷰 해결안**: `last_verdict: ReadinessVerdict | None` 필드 추가

**판정**: 타당.

**코드 검증**: `VERDICT_TO_PHASE`에서 `ASK_USER → Phase.VERIFYING`, `TERMINATE → Phase.DONE`으로 매핑되어 있어 이 둘은 구분 가능. 그러나 02-detailed의 `_route_after_readiness_gate()`에서 `Phase.VERIFYING`일 때 `pending_signals` 존재 여부로 ASK_USER를 판별하는 것은 간접적이고 취약.

리뷰의 `last_verdict` 필드 추가가 단순하고 명확한 해결책.

**근거**: "Explicit is better than implicit" (PEP 20, Zen of Python). 정보 변환 시 원본을 보존하는 것은 디버깅과 라우팅 정확성 모두에 유리.

---

### 4-5. [P1-2] knowledge_item 매칭 — 문자열 key 부분 일치 대신 ID 기반 참조로 전환

- **리뷰 지적**: "일자"가 "여신실행일자", "기준일자", "만기일자" 모두와 매칭
- **리뷰 해결안**: 복수 매칭 시 None 반환 → 새 항목 생성

**판정**: 리뷰 지적은 타당하나, 해결안(부분 일치 매칭 안전성 강화)은 근본적 해결이 아님. **`KnowledgeItem`에 `knowledge_id` 필드를 추가하고, LLM은 ID로만 참조**하는 방식이 더 확실하다. 한국어 금융 용어는 접미어 공유가 매우 빈번(`~일자`, `~코드`, `~금액`, `~건수`, `~비율`)하며, 함축적 금융 용어 질의가 대부분인 프로덕션 특성상 문자열 매칭으로는 복수 매칭 위험이 상시적이다.

**설계 원칙**: "채번은 코드, 참조는 LLM"
- `knowledge_id`는 코드에서 생성 시점에 자동 채번 (`K1`, `K2`, ...)
- LLM은 프롬프트에 표시된 ID를 참조만 함
- 신규 항목 생성 시 LLM은 `item_id`를 null로 두고, 코드가 다음 순번을 채번

```python
# 1. KnowledgeItem에 ID 필드 추가
class KnowledgeItem(BaseModel):
    knowledge_id: str  # "K1", "K2" — 코드에서 자동 채번
    key: str
    status: ConfidenceStatus
    value: str | None = None
    evidence: list[str] = Field(default_factory=list)
    ...

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
        # ID로 dict lookup — O(1), 매칭 오류 없음
        item = knowledge_map[update.item_id]
        item.status = update.new_status
        item.evidence.append(update.evidence)
    else:
        # 신규 항목 — 코드에서 다음 순번 채번
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

**참고**: `candidate_tables`(table_name이 유니크), `execution_plan`(step 번호가 ID 역할)은 기존 키가 충분하므로 별도 ID 추가 불필요. `knowledge_items`만 문자열 key의 모호성 문제가 있어 ID를 추가한다.

**근거**:
- SQL 생성에서 잘못된 knowledge 매핑은 잘못된 컬럼 참조로 직결. 문자열 부분 일치보다 ID 참조가 precision 관점에서 확실.
- `hypothesis_id`가 이미 동일 패턴(코드 채번 + LLM 참조)으로 동작 중이므로, 검증된 패턴의 확장.

---

### 4-6. [P1-3] `PROMOTION_ORDER`에 CANDIDATE 누락 — 수정 필수

- **리뷰 지적**: CANDIDATE가 없어 CANDIDATE→UNRESOLVED 역행이 허용됨

**판정**: 코드 검증으로 100% 타당 확인.

**코드 검증**: `ConfidenceStatus.CANDIDATE`는 `src/models/enums.py:72`에 실제 존재 (UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED → CONFLICTED). `context_explorer.py:562`에서 CANDIDATE로 knowledge_item 생성, `confidence_scorer.py:163`에서 점수 계산에 사용.

```python
# 리뷰 해결안 — 채택 필수
PROMOTION_ORDER = {
    ConfidenceStatus.UNRESOLVED: 0,
    ConfidenceStatus.CANDIDATE: 1,   # 추가 필수
    ConfidenceStatus.PROBABLE: 2,
    ConfidenceStatus.CONFIRMED: 3,
    ConfidenceStatus.CONFLICTED: 4,
}
```

---

### 4-7. [P1-4] discovered_facts 갱신 경로 부재 — 수정 필요

- **리뷰 지적**: recovery_agent가 발견한 사실이 discovered_facts에 추가되지 않아 sql_generator가 이를 모름
- **리뷰 해결안**: `decision.analysis`를 `discovered_facts`에 추가

**판정**: 타당.

**코드 검증**: `discovered_facts`는 `state.py:414`에 존재하며 sql_generator 프롬프트에서 참조됨. recovery_agent가 도구를 통해 "폐쇄지점도 포함됨" 같은 사실을 발견해도 이 채널이 없으면 sql_generator가 WHERE 조건에 반영하지 못함.

리뷰의 해결안이 적절. 단, **모든 analysis가 아닌 action이 "ready" 또는 "call_tools"이고 knowledge_updates가 있는 경우에만** 추가하여 노이즈를 방지:

```python
if decision.analysis and decision.knowledge_updates:
    reason.discovered_facts.append(f"[recovery] {decision.analysis}")
```

---

### 4-8. [P2-4] EXPLORE verdict에서 knowledge_fetcher 재진입의 실효성 — 수정 필요

- **리뷰 지적**: PENDING 스텝이 없는 상태에서 knowledge_fetcher 재진입 시 무한 루프

**판정**: 타당.

**코드 검증**: `evaluate_readiness()` (`confidence_scorer.py:70-75`)에서 PENDING 스텝이 남아있으면 EXPLORE를 반환하므로, 논리적으로는 knowledge_fetcher 재진입 시 실행할 스텝이 있어야 함. 그러나 **knowledge_fetcher가 스텝을 실행한 후에도 knowledge_interpreter의 해석 결과에 따라 추가 스텝이 생성되지 않으면** 다음 readiness_gate 평가에서 PENDING이 없어 EXPLORE 외의 verdict가 나옴.

그럼에도 방어적 가드를 추가하는 리뷰 제안은 엣지 케이스 방지에 적절:

```python
# readiness_gate.py
if verdict == ReadinessVerdict.EXPLORE:
    pending_steps = [s for s in reason.execution_plan if s.status == StepStatus.PENDING]
    if reason.exploration_phase == "initial" and not pending_steps:
        reason.exploration_phase = "recovery"
        verdict = ReadinessVerdict.REPLAN
```

---

## 5. 비판적 검토와 원안 둘 다 미흡한 점 — 보완 제안 (5건)

### 5-1. [P1] recovery_agent ReAct 루프에서 "진전 감지(progress detection)" 메커니즘 부재

- **원안 상태**: `max_internal_rounds`(5)와 `LoopGuard`만으로 종료 조건 관리
- **리뷰 상태**: P0-1에서 give_up 무한 루프만 지적, 내부 루프의 진전 부재 문제는 미지적
- **우선순위**: 1차 교차 검토 P2 → **프로덕션 재평가 P1 격상**

**문제**: recovery_agent가 `call_tools`를 반복하면서 **실질적으로 knowledge_items의 상태가 변하지 않는** 경우가 가능.

```
라운드 1: search_table_meta("여신") → 결과 있지만 LLM이 knowledge_update 생성 실패
라운드 2: search_table_meta("대출") → 유사 결과, knowledge_update 없음
라운드 3-5: 다른 검색어로 반복, 진전 없음
= LLM 5회 + 도구 5-20회 호출 낭비
```

**프로덕션 격상 근거**: **70B 모델 + 부실한 메타데이터** 조합에서는 "도구를 호출했지만 유용한 정보를 못 찾는" 케이스가 빈번할 것. MongoDB 비즈용어사전이 200개 미만, 보고서 SQL 저장소 없음 → 진전 없이 5라운드까지 도는 것은 LLM 비용 + 응답 시간 모두에 치명적. 70B 모델 기준 라운드당 3-15초 → 진전 없는 5라운드 = 최대 75초 낭비.

**보완 제안**: 라운드 간 knowledge_items 변화를 추적하고, 2회 연속 변화 없으면 조기 종료.

```python
# recovery_agent_node 내부
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
- **Pre-Act** (Rawat et al., 2025, arXiv:2505.09970): ReAct 대비 Pre-Act가 Action Recall +70%를 달성한 핵심 요인이 **사전 계획에 의한 불필요 반복 방지**. progress detection은 사후적으로 동일한 효과를 냄.
- **AgentBench** (ICLR 2024, arXiv:2308.03688): 실패 유형 중 "Task Limit Exceeded(루프 탈출 실패)"가 progress detection 없이 max_rounds만 의존할 때의 전형적 결과.

---

### 5-2. recovery_agent와 readiness_gate 간의 라우팅 계약이 암시적

- **원안 상태**: recovery_agent가 Phase를 설정하고, 라우팅 함수가 Phase를 읽어 분기
- **리뷰 상태**: P1-1에서 `last_verdict` 추가를 제안했으나, recovery_agent→readiness_gate→recovery_agent 재진입 시의 전체 상태 계약은 미정의

**문제**: recovery_agent가 `Phase.VERIFYING`으로 종료 → readiness_gate → REPLAN → recovery_agent 재진입 시, recovery_agent는 **이전 실행의 ReAct 루프 맥락(tool_results)을 잃어버림**. 새로 진입한 recovery_agent는 빈 `tool_results`로 시작하므로 이전 도구 호출 결과를 직접 참조할 수 없음.

또한 **recovery_agent의 진입 경로**(readiness_gate에서 REPLAN vs sql_validator에서 실패)에 따라 hypothesis 관리 동작이 달라야 하지만, 현재 설계에서는 이를 구분하지 않음. 프로덕션 환경에서는 70B 모델에 진입 경로를 명시적으로 제공하면 도구 선택 정확도가 향상되므로, readiness_gate 경유(초기 컨텍스트 부족 → 넓은 탐색 필요) vs sql_validator 경유(SQL 구조/의미 오류 → 특정 문제 해결 필요)의 구분이 특히 중요하다.

**보완 제안**: 진입 경로를 state에 기록.

```python
class ReasoningState(BaseModel):
    recovery_entry_source: Literal["readiness_gate", "sql_validator", None] = None
```

이를 통해 recovery_agent 프롬프트에서 "초기 탐색 부족으로 진입했는지, SQL 검증 실패로 진입했는지"를 LLM에 전달하여 더 정확한 분석 유도.

**근거**:
- **Reflexion** (Shinn et al., 2023, arXiv:2303.11366): "이전 시도의 실패 원인을 명시적으로 다음 시도에 전달하는 것이 성공률을 20-30% 향상"시킴.

---

### 5-3. [P0] 도구 실행 병렬화의 범위와 의존성 판별 미정의

- **원안 상태**: P2 (향후 최적화)로 분류, "asyncio.gather" 언급만
- **리뷰 상태**: P2-3에서 P1 격상 권장, 병렬화 코드 제시하나 의존성 판별 없음
- **우선순위**: 원안 P2 → 리뷰 P1 → **프로덕션 재평가 P0 격상**

**문제**: 두 문서 모두 "독립 도구 간 병렬화"만 언급하고, **도구 간 의존성 판별 로직**은 제시하지 않음. 현재 설계에서 LLM이 한 라운드에서 요청하는 tool_calls가 모두 독립적이라고 가정하고 있으나, 70B 모델에서 이 가정이 항상 성립하는지 검증 부재.

**프로덕션 격상 근거**: 단순한 지연 개선이 아니라, **70B 모델의 ReAct 라운드 수를 줄이는 핵심 수단**. 70B 모델에서 ReAct 라운드가 길어질수록 "조기 ready" 또는 "무한 call_tools" 위험이 급증 (AgentBench TLE 패턴). 한 라운드에서 3~4개 도구를 병렬 실행하면 **동일 정보를 더 적은 라운드에서 수집** → LLM 판단 오류 기회 자체가 감소. 프로덕션 시나리오: "지점별 여신 실행 금액" → search_table_meta("지점") + search_table_meta("여신") + search_code_meta("branch_type_cd")를 1라운드에서 병렬 처리하면 2-3라운드 절약.

**보완 제안**:

1. **프롬프트 수준 방어**: RECOVERY_AGENT_SYSTEM_PROMPT에 추가.
   ```
   7-1. 한 라운드의 tool_calls는 서로 독립적이어야 합니다.
        다른 도구의 결과가 필요한 호출은 다음 라운드에 요청하세요.
   ```
2. **병렬 실행 + 개별 예외 처리** (리뷰 P2-3 코드 채택)

**근거**:
- **Latency-Aware Orchestration** (2025, arXiv:2601.10560): "sequential scaling은 토큰 효율 우수, parallel scaling은 **1.6x 빠름**". 독립 단계는 병렬화, 의존성 있는 단계는 강제 병렬화 금지를 명시적 원칙으로 제시.

---

### 5-4. [P1] `_parse_recovery_response` fallback에서 tool_calls 복원 불가

- **원안 상태**: 02-detailed 4.9절 — regex fallback으로 action만 추출
- **리뷰 상태**: 미지적
- **우선순위**: 1차 교차 검토 P2 → **프로덕션 재평가 P1 격상**

**문제**: JSON 파싱 실패 시 fallback이 `action`만 추출하고 `tool_calls`, `knowledge_updates`는 빈 리스트로 반환. action이 `"call_tools"`인데 tool_calls가 비어있는 비정상 상태가 발생.

recovery_agent_node의 364행에 `if not decision.tool_calls: break` 방어가 있지만, call_tools를 반환했는데 도구 없이 종료되는 것은 **silent failure**로 디버깅이 어려움.

**프로덕션 격상 근거**: 파인튜닝 없는 70B 모델에서 JSON 파싱 실패는 예외가 아니라 **정상 운영 시나리오**. StructEval 기준 OSS 모델의 구조화 출력 준수율이 상용 대비 ~10점 낮음. Solar Pro 2 70B는 JSON 출력 안정성이 검증되지 않은 상태. 파싱 실패 시의 안전한 복구가 없으면 silent failure → 사용자에게 무응답 또는 오류.

**보완 제안**: fallback에서 action이 `call_tools`이면 `give_up`으로 전환하고 로그 경고.

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
- **StructEval** (Tiger AI Lab, 2025): OSS 모델의 구조화 출력 준수율이 상용 모델 대비 ~10점 낮음. 70B 모델에서 JSON 파싱 실패가 빈번할 수 있어 fallback 안전성이 실측에 기반한 요구사항.
- Fail-Safe Defaults (Saltzer & Schroeder, 1975): 시스템 실패 시 안전한 기본값으로 복귀하는 원칙.

---

### 5-5. readiness_gate의 force-generate 임계값 문서 간 불일치

- **원안 상태**: 01-strategy 6.3절 — `THRESHOLD_FORCE_GENERATE (0.55)`
- **리뷰 상태**: P0-1 시나리오에서 `THRESHOLD_FORCE_GENERATE 0.40` 사용

**문제**: 동일한 상수에 대해 문서마다 다른 값(0.40 vs 0.55). 이 차이는 결과에 유의미한 영향 — 0.40이면 더 많은 경우에 force-generate가 발동되어 불완전한 SQL이 생성될 확률 증가, 0.55이면 TERMINATE(실패)로 가는 경우가 증가.

**보완 제안**:

1. 실제 `confidence_scorer.py`에서 현재 사용 중인 값을 확인하고 모든 문서를 통일
2. config에서 관리하여 문서 불일치 위험을 근본적으로 제거
3. 임계값의 근거를 골든셋 실험으로 검증 (P1 권장)

---

## 6. 프로덕션 환경에서 발견한 신규 이슈 (5건)

> 1~5장의 교차 검토에서 다루지 않았으나, 프로덕션 환경(참조 저장소 제약, 70B LLM, 함축적 질의 패턴)을 감안할 때 추가로 필요한 설계 보완 사항.

### 6-1. [P0 신규] recovery_agent의 "선 추론 후 표시" 정책 미반영 — 정확도 직접 영향

**모든 문서 상태**: 교차 검토의 S-4(CONFLICTED → ASK_USER), 리뷰의 P2-4(EXPLORE 재진입), 원안의 검토 6(명확화 연동) 모두 **"모호하면 사용자에게 질문"** 패턴을 전제.

**프로덕션 현실**:

> "질의를 해석했을 때 문제를 해결할 수 없는 경우 외에는 대체적으로 모호한 용어 등은 **선 추론 후 결과에 추론내용을 같이 표시**하면 됨"

이것은 설계의 근본적 가정을 변경한다:

```
── 현재 설계의 가정 ──
"예금신규 top 3" → 모호함 → ASK_USER → 사용자에게 "예금신규액? 예금신규건수?" 확인
                                       → 사용자 응답 후 SQL 생성

── 프로덕션 정책 ──
"예금신규 top 3" → 모호함 → "예금신규액"으로 추론 → SQL 생성
                         → 결과에 "예금신규액 기준으로 조회하였습니다" 표시
```

**영향 범위**:

1. **readiness_gate의 ASK_USER 발동 기준이 너무 낮음**: 현재 `has_conflicted_items()` → ASK_USER인데, 실제로는 대부분의 CONFLICTED 상황에서도 하나를 추론 선택하고 진행해야 함
2. **recovery_agent의 give_up 기준이 너무 빠름**: 정확한 메타를 찾지 못해도 "합리적 추론"으로 SQL을 생성해야 하는 경우가 많음
3. **sql_generator 프롬프트에 추론 근거 표시 채널이 없음**: 결과 응답에 "이런 기준으로 조회했습니다"를 표시하려면 추론 이유가 Present Layer까지 전달되어야 함

**수정안**:

```python
# 1. CONFLICTED 처리 정책 변경
# readiness_gate.py — ASK_USER 발동 조건을 제한적으로 변경
def _should_ask_user(reason: ReasoningState) -> bool:
    """
    ASK_USER는 '추론으로도 해결 불가능한' 경우에만 발동.
    예: 두 테이블이 완전히 다른 결과를 주는 경우, 사용자 의도가 2가지 이상으로 분기하는 경우.
    단순 용어 모호성(예금신규액 vs 건수)은 추론으로 처리.
    """
    critical_conflicts = [
        ki for ki in reason.knowledge_items
        if ki.status == ConfidenceStatus.CONFLICTED and ki.is_critical
    ]
    # 단순 값 모호성은 추론 가능 → ASK_USER 불필요
    unresolvable = [
        ki for ki in critical_conflicts
        if _is_unresolvable_conflict(ki)  # 테이블 선택 충돌, 산출식 충돌 등
    ]
    return len(unresolvable) > 0


# 2. 추론 근거 전달 채널 추가
# state.py — ReasoningState에 추가
class ReasoningState(BaseModel):
    ...
    inference_notes: list[str] = Field(default_factory=list)
    """
    추론으로 결정한 사항과 그 근거. Present Layer에서 사용자에게 표시.
    예: ["'예금신규'를 '예금신규액' 기준으로 해석하였습니다 (가장 일반적 사용 패턴)"]
    """

# 3. recovery_agent에서 추론 시 inference_notes에 기록
# recovery_agent.py
if decision.action == "ready" and reason.knowledge_items:
    for ki in reason.knowledge_items:
        if ki.status in (ConfidenceStatus.PROBABLE, ConfidenceStatus.CANDIDATE):
            reason.inference_notes.append(
                f"'{ki.key}'를 '{ki.value}' 기준으로 해석하였습니다 ({ki.evidence[-1] if ki.evidence else '추론'})"
            )
```

**근거**: 사용자 상호작용 규칙(`user-interaction.md`)에서도 "모호한 요청은 추측하지 말고 명확화 질문으로 확인"이라 되어 있으나, 프로덕션 환경 정보가 이를 override하여 "선 추론 후 표시" 정책을 명시. 이 정책이 적용되지 않으면 **불필요한 명확화 질문이 빈번하여 사용자 경험이 저하**되고, 실제로는 추론으로 충분한 케이스에서 대화 턴이 추가됨.

---

### 6-2. [P1 신규] 참조 저장소 한계를 반영한 recovery_agent 도구 전략 부재

**모든 문서 상태**: recovery_agent의 6개 도구(`search_table_meta`, `search_code_meta`, `search_manual`, `search_glossary`, `get_sample_rows`, `get_date_distribution`)를 동등하게 취급.

**프로덕션 현실**:

| 도구 | 참조 저장소 | 프로덕션 기대 효과 |
|------|-----------|------------------|
| `search_table_meta` | MongoDB (테이블/컬럼 레이아웃) | **높음** — 가장 직접적 SQL 힌트 |
| `search_code_meta` | MongoDB (코드 메타) | **높음** — 코드값 매핑 필수 |
| `get_sample_rows` | PostgreSQL (직접 조회) | **높음** — 실제 데이터 확인 |
| `get_date_distribution` | PostgreSQL (직접 조회) | **중간** — 날짜 범위 확인 |
| `search_manual` | Qdrant (업무매뉴얼) | **낮음** — "SQL 추론에 필요한 직접적 힌트는 아님" |
| `search_glossary` | MongoDB (200개 미만) | **낮음** — 부실, 결과 없을 확률 높음 |

**문제**: recovery_agent의 LLM이 `search_manual`이나 `search_glossary`를 우선 호출하면, 유용하지 않은 결과로 ReAct 라운드를 소모. 특히 70B 모델은 도구 선택 정확도가 낮으므로(AgentBench IA 패턴) 이 위험이 높음.

**수정안**: recovery_agent 프롬프트에 도구 효과성 가이드 추가.

```
## 도구 우선순위 가이드

아래 도구들은 SQL 정확도에 대한 기여도 순으로 정렬되어 있습니다.
탐색 전략을 세울 때 상위 도구를 우선 고려하세요.

1. search_table_meta: 테이블/컬럼 구조 확인 (SQL 생성에 직접적 힌트)
2. get_sample_rows: 실제 데이터 패턴 확인 (컬럼값, NULL 여부, 코드값 추론)
3. search_code_meta: 코드 컬럼의 값-설명 매핑 확인 (WHERE 조건에 필수)
4. get_date_distribution: 날짜 컬럼의 데이터 범위 확인
5. search_glossary: 금융 용어 정의 확인 (용어사전이 부실하여 결과가 없을 수 있음)
6. search_manual: 업무 프로세스 확인 (SQL 추론에 간접적 참고만 됨)

주의: search_glossary와 search_manual의 결과가 비어있는 것은 정상입니다.
결과가 없다면 다른 도구로 전환하세요. 동일 도구를 다른 검색어로 재시도하지 마세요.
```

**추가**: `_build_recovery_prompt`에서 이전 라운드의 빈 결과 도구를 명시하여 LLM이 동일 도구를 재호출하지 않도록 가이드.

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

**근거**: CHESS(2024)에서 "스키마 프루닝이 NL-to-SQL의 핵심"이라는 결론은, 역으로 **비효과적 정보원에서의 탐색이 정확도를 저하**시킨다는 의미. 프로덕션에서 Qdrant 매뉴얼과 MongoDB 용어사전이 SQL 추론에 직접적 도움이 되지 않는다는 사실을 설계에 반영해야 함.

---

### 6-3. [P1 신규] 함축적 금융 용어의 "합리적 추론" 경로 부재

**모든 문서 상태**: knowledge_items의 상태 전이는 UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED 또는 CONFLICTED. 모든 전이에 도구 결과 기반의 `evidence`가 필요.

**프로덕션 현실**:

> "은행에서 관행적으로 사용하는 용어를 사용", "의미를 풀어서 요청하는 훈련이 되어있지 않아 함축적으로 요청"

```
── 시나리오 ──
사용자: "여신 top 3 지점"

현재 설계:
1. planner: "여신" → knowledge_item(key="여신", status=UNRESOLVED)
2. knowledge_fetcher: search_table_meta("여신") → 여신실행, 여신잔액, 여신한도 등 다수 테이블 발견
3. knowledge_interpreter: "여신"의 구체적 의미 불분명 → UNRESOLVED 유지 또는 CONFLICTED
4. readiness_gate: CONFLICTED → ASK_USER
5. → 사용자에게 "여신실행액? 여신잔액?" 질문 (불필요한 대화 턴)

프로덕션 기대:
1. "여신" → 가장 일반적 해석인 "여신잔액"으로 추론
2. SQL 생성 → 결과 표시 시 "여신잔액 기준" 명시
```

**문제**: 현재 knowledge_items의 상태 전이 모델에는 **"증거 없이 관행적 추론으로 결정"하는 경로**가 없다. 도구 결과(evidence)가 없으면 UNRESOLVED에서 벗어날 수 없고, 도구를 아무리 호출해도 "여신 = 여신잔액"이라는 관행적 매핑은 발견할 수 없다 (메타 저장소에 이런 매핑이 없으므로).

**수정안**: `INFERRED` 상태 추가 또는 PROBABLE의 의미 확장.

```python
# 방안 A: 새로운 상태 추가 (명시적이지만 enum 변경이 큼)
class ConfidenceStatus(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    INFERRED = "INFERRED"      # 도구 증거 없이 관행/맥락 기반 추론
    CANDIDATE = "CANDIDATE"
    PROBABLE = "PROBABLE"
    CONFIRMED = "CONFIRMED"
    CONFLICTED = "CONFLICTED"

# 방안 B: PROBABLE의 evidence 없는 사용 허용 (최소 변경, 권장)
# → recovery_agent 프롬프트에 관행적 추론 지침 추가
# → KnowledgeUpdate에 inference_type 필드 추가

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

**근거**: financial-domain.md에서 "금융 계수산출식은 정확한 산출식이 필수, 불확실하면 SQL 생성하지 않음"이라고 명시되어 있으나, 이는 **산출식**에 한정된 규칙. 일반적인 용어 해석은 "선 추론 후 표시" 정책이 적용됨. 현재 설계는 이 두 가지를 구분하지 못함.

---

### 6-4. [P1 신규] readiness_gate의 `term_resolution` 가중치(55%)가 프로덕션에서 과도

**코드 검증**: `confidence_scorer.py`의 `calculate_readiness()`:

```python
term_resolution (55%):   CONFIRMED|PROBABLE critical items / total critical items
table_coverage (25%):    candidate_tables with description / total candidates
join_path (20%):         1.0 if multi-table has common join_keys, else 0.3
```

**프로덕션 문제**:

함축적 질의에서는 `knowledge_items`의 많은 항목이 도구 증거로 CONFIRMED까지 도달하기 어렵다. 예를 들어 "예금신규 top 3" 질의에서:

| knowledge_item | 도구로 확인 가능? | 프로덕션 기대 상태 |
|----------------|-----------------|------------------|
| "예금신규" → 어떤 지표? | 불가 (관행적 해석 필요) | PROBABLE (추론) |
| "top 3" → 금액? 건수? | 불가 (관행적 해석 필요) | PROBABLE (추론) |
| "기간" → 언제? | 불가 (명시 안 됨) | PROBABLE (추론: 당월) |
| "테이블" → 어떤 테이블? | **가능** (search_table_meta) | CONFIRMED |

4개 critical items 중 1개만 CONFIRMED → term_resolution = 0.25 (PROBABLE 포함 시 1.0)

**문제**: `calculate_readiness`에서 PROBABLE이 CONFIRMED과 동등하게 처리되는지 확인 필요. 만약 CONFIRMED만 카운트한다면, 합리적 추론만으로 진행해야 하는 대부분의 프로덕션 질의에서 **readiness_score가 threshold에 도달하지 못하고 불필요한 recovery 루프에 진입**.

**코드 확인 결과**: confidence_scorer.py에서 `CONFIRMED|PROBABLE`을 모두 카운트하므로 PROBABLE이면 통과. 그러나 **6-3에서 제기한 "추론으로 PROBABLE 설정" 경로가 없으면**, 이 항목들은 UNRESOLVED에 머물러 score가 낮아짐.

**수정안**: 6-3의 추론 경로가 구현되면 이 문제는 자연스럽게 해소. 단, readiness_gate에서 PROBABLE(추론)과 PROBABLE(도구 증거)을 구분하여 **추론 기반 PROBABLE이 많으면 결과에 "추론 사항" 안내를 강화**하는 것을 권장.

```python
# readiness_gate에서 추론 비중 체크
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

### 6-5. [P2 신규] Qdrant SQL 이력 검색(`search_use_cases`)의 recovery 경로 부재

**원안 상태**: `search_use_cases`는 recovery_agent 도구 목록에서 제외 (01-strategy 3.3절). Phase 1(planner)에서만 사용.

**프로덕션 재평가**: Qdrant에 저장된 **과거 수행 SQL과 설명**은 실제로 SQL 추론에 가장 직접적인 힌트가 될 수 있다. 특히:

- MongoDB 메타가 부실한 상황에서, 과거 유사 SQL이 "어떤 테이블을 어떤 조인으로 사용했는지"를 직접 보여줌
- planner의 초기 검색과 recovery의 재검색은 **검색어가 다를 수 있음** — planner는 원본 질의로 검색하지만, recovery는 실패 원인을 반영한 더 구체적인 검색어를 사용할 수 있음

```
── 시나리오 ──
planner: search_use_cases("지점별 여신 실행 금액") → 유사 SQL 0건 (검색어가 너무 구체적)
→ recovery 진입 (테이블 확인 불확실)
recovery_agent: search_use_cases("여신 실행") → 유사 SQL 3건 발견!
  → 과거 SQL에서 TB_LOAN_EXEC 테이블, exec_dt 날짜 컬럼, branch_cd 지점코드 확인
  → 한 번에 여러 knowledge 공백 해소
```

**수정안**: recovery_agent 도구 목록에 `search_use_cases`를 **조건부 복원**. 단, 동일 검색어 중복 방지를 위해 이전 검색 이력을 프롬프트에 포함.

```python
# recovery_agent 도구 목록 (변경)
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
다른 관점의 검색어를 사용하세요 (예: "지점별 여신 실행 금액" 대신 "여신 실행" 또는 "대출 실적").
```

이 수정은 P2로 분류하지만, 보고서 SQL 저장소가 구현되기 전까지 **Qdrant SQL 이력이 사실상 유일한 참조 SQL 소스**이므로 조기 구현을 권장.

---

## 구현 우선순위 통합 (프로덕션 환경 반영)

### P0 — 구현 전 설계 보완 필수 (5건)

| # | 항목 | 출처 | 핵심 근거 |
|---|------|------|----------|
| 1 | give_up 시 즉시 종료 + force-generate 내부 판정 | 4-1 (리뷰 P0-1) | 70B give_up 빈도 높음, "선 추론" 정책과 연계 |
| 2 | planner_node에서 exploration_phase/recovery_rounds 리셋 | 4-2 (리뷰 P0-2) | 멀티턴 필수 |
| 3 | _execute_single_tool 어댑터 kwargs→문자열 변환 | 4-3 (리뷰 P0-3) | 코드 직접 검증 |
| 4 | decision=None 시 즉시 종료 | 2-3 | 70B 파싱 실패율 높음 |
| 5 | **[프로덕션 격상]** 도구 실행 병렬화 (asyncio.gather) | 5-3 (P1→P0) | ReAct 라운드 수 감소 → 70B 판단 오류 기회 감소 |

### P1 — 구현 초기 반영 권장 (12건)

| # | 항목 | 출처 | 핵심 근거 |
|---|------|------|----------|
| 6 | **[프로덕션 신규]** "선 추론 후 표시" 정책 반영 — ASK_USER 기준 변경 + inference_notes 채널 | 6-1 | **정확도 + UX 핵심** |
| 7 | **[프로덕션 신규]** 도구 우선순위 가이드 + 빈 결과 도구 피드백 | 6-2 | 참조 저장소 한계 대응 |
| 8 | **[프로덕션 신규]** 함축적 용어의 관행적 추론 경로 (is_inferred 플래그) | 6-3 | 대부분의 프로덕션 질의가 함축적 |
| 9 | last_verdict 필드 추가, 라우팅에서 직접 참조 | 4-4 (리뷰 P1-1) | PEP 20 |
| 10 | _find_knowledge_item 복수 매칭 시 None 반환 | 4-5 (리뷰 P1-2) | 금융 도메인 용어 특성 |
| 11 | PROMOTION_ORDER에 CANDIDATE 추가 | 4-6 (리뷰 P1-3) | 코드 직접 검증 |
| 12 | discovered_facts 경로 + CANDIDATE 프롬프트 포함 | 4-7, 2-1 (리뷰 P1-4) | sql_generator 연계 |
| 13 | KnowledgeUpdate CANDIDATE + search_use_cases 제거 | 2-2, 2-4 | 시스템 일관성 |
| 14 | **[프로덕션 격상]** 진전 감지 (2회 연속 무변화 → 조기 종료) | 5-1 (P2→P1) | 부실 메타 환경에서 비용 효율 |
| 15 | **[프로덕션 격상]** fallback 파싱 안전성 강화 | 5-4 (P2→P1) | 파인튜닝 없는 70B의 JSON 불안정 |
| 16 | 프롬프트에 tool_calls 독립성 규칙 명시 | 5-3 | 70B 모델 가이드 |
| 17 | recovery_entry_source 필드 추가 | 5-2 | 70B 맥락 제공 |

### P2 — 품질/안정성 향상 (5건)

| # | 항목 | 출처 | 핵심 근거 |
|---|------|------|----------|
| 18 | **[프로덕션 신규]** readiness_gate 추론 비중 체크 + 안내 강화 | 6-4 | 추론 기반 응답의 투명성 |
| 19 | **[프로덕션 신규]** search_use_cases 조건부 복원 (recovery 경로) | 6-5 | 유일한 참조 SQL 소스 |
| 20 | force-generate 임계값 통일 + config 관리 | 5-5 | 문서 일관성 |
| 21 | recovery_rounds 의미 명확화 (리셋 or 필드명 변경) | 2-5 | 가독성 |
| 22 | EXPLORE verdict PENDING 스텝 가드 | 4-8 (리뷰 P2-4) | 방어적 프로그래밍 |

---

## 정확도 관점 종합 평가

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
| 병목 1: 함축적 용어 | 6-1(선 추론 정책), 6-3(추론 경로) | ASK_USER 빈도 50% 이상 감소, 대화 턴 절약 |
| 병목 2: 부실 메타 | 6-2(도구 우선순위), 6-5(SQL 이력 복원) | 유효하지 않은 도구 호출 감소, 참조 SQL 활용 |
| 병목 3: 70B ReAct | 5-3(병렬화), 5-1(진전 감지), 5-4(파싱 안전) | ReAct 라운드 감소, 비정상 종료 방지 |

### 최종 판단

설계의 기본 구조(2-Phase Exploration, Phase 1 기계적 분리, Structured Output, Hypothesis 관리)는 **프로덕션 환경에서도 유효**하다. 단, 위의 수정안 없이 구현하면:

1. **불필요한 ASK_USER**가 빈번하여 사용자 경험 저하 (대부분의 모호한 질의에서 질문)
2. **recovery 루프에서 비효과적 도구 호출**로 응답 시간 증가 (search_manual, search_glossary 낭비)
3. **70B 모델의 ReAct 불안정성**이 증폭되어 give_up 비율 증가 (진전 감지/파싱 안전 부재)

수정안이 반영되면, **함축적 질의 → 합리적 추론 → SQL 생성 → 추론 근거 표시**의 단축 경로가 확보되어, 프로덕션 환경에서의 정확도와 응답 속도 모두 의미 있게 개선될 것으로 판단.

---

## 참고 문헌

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
