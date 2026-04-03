# 통합 설계서(03-final-implementation-design.md) 일관성 및 누락 검토 보고서

- **검토일**: 2026-04-01
- **검토 대상**:
  - 원안: `docs/strategy-proposals/agentic-recovery-redesign/01-strategy.md`
  - 리뷰: `docs/reviews/design/20260401-agentic-recovery-redesign-cross-review-final.md`
  - 통합: `docs/strategy-proposals/agentic-recovery-redesign/03-final-implementation-design.md`
- **검토 관점**: 원안/리뷰에서 통합 문서로의 누락, 통합 문서 내부 불일치, 구현 시 빠트릴 수 있는 부분

---

## 1. 누락 사항

### 1.1 원안(01-strategy.md)에서 통합 문서로 빠진 내용

| # | 등급 | 항목 | 상세 |
|---|------|------|------|
| N-01 | 문제 없음 | 1 현황 분석 노드/파일/라인 수 테이블 | 통합 문서 1.1에 동일한 테이블이 완전히 포함되어 있음 |
| N-02 | 문제 없음 | 2 설계 목표 테이블 | 통합 문서 2.1에 동일하게 포함 |
| N-03 | 문제 없음 | 3 아키텍처 결정 검토 1~7 | 검토 1(LLM 효율) 3.3절, 검토 2(Hypothesis) 3.3절, 검토 3(컨텍스트 윈도우) 10.3절, 검토 4(복수 공백) 3.3절, 검토 5(Fast-Path) 7.2절, 검토 6(명확화) 9.1절, 검토 7(테스트) 6.4절에 각각 반영 |
| N-04 | 문제 없음 | 5 라우팅 테이블 | 통합 문서 7.1에 리뷰 반영 확장판으로 포함 |
| N-05 | 문제 없음 | 6 LoopGuard 종료 조건 | 통합 문서 8절에 5종 종료 조건, increment 위치, force-generate 모두 포함 |
| N-06 | 문제 없음 | 7 마이그레이션 전략 Step 1~4 | 통합 문서 11절에 Step 1~4 + 롤백 전략 모두 포함. Step 2가 State 필드 추가를 흡수하여 원안보다 상세 |
| N-07 | 문제 없음 | 8 체크리스트 | 통합 문서 12절에 P0 5건, P1 12건, P2 5건으로 리뷰 반영 확장 |
| N-08 | 🟡 | 부록 A (프롬프트 설계 가이드) | 통합 문서 10절에 통합되었으나, 원안의 "프롬프트 설계 원칙" 5항목이 통합 문서 10.2에서 8항목으로 확장 반영됨. 원안의 부록 A 자체는 별도 섹션이 아닌 10절로 흡수. **원안의 "부록 A"라는 명칭이 사라졌으므로 원안을 참조하는 다른 문서가 있으면 링크가 깨질 수 있음** |
| N-09 | 문제 없음 | 부록 B (폐쇄망 모델 호환성) | 통합 문서 부록 A로 포함. Solar Pro 2, Qwen3.5, 모델 무관 설계 원칙 모두 포함 |

### 1.2 리뷰(cross-review-final.md)에서 통합 문서로 빠진 내용

| # | 등급 | 항목 | 상세 |
|---|------|------|------|
| N-10 | 문제 없음 | S-1~S-8 보존 항목 | 통합 문서 2.2에 S-1~S-16으로 확장 포함. 원안의 보존 대상 8개(1.3절)와 리뷰의 S-1~S-8을 합산하여 16개로 정리 |
| N-11 | 문제 없음 | P0-1~P0-5 치명적 5건 | P0-1: 6.4절 `_finalize_recovery`, P0-2: 4.5절 planner 리셋, P0-3: 6.4절 `_execute_single_tool`, P0-4: 6.4절 `_finalize_recovery` decision=None, P0-5: 6.4절 `_execute_tools` 병렬화 — 모두 구현 코드 수준까지 반영 |
| N-12 | 문제 없음 | P1-1~P1-12 중요 12건 | P1-1: 9.1절, P1-2: 9.3절, P1-3: 9.2절, P1-4: 4.1/7.2절, P1-5: 4.2/5.2절, P1-6: 4.3절, P1-7: 6.4절 discovered_facts/프롬프트, P1-8: 5.1절 스키마, P1-9: 6.4절 진전 감지, P1-10: 6.4절 `_parse_recovery_response`, P1-11: 4.1/7.2절 entry_source, P1-12: 6.4절 recovery_rounds — 모두 반영 |
| N-13 | 문제 없음 | P2-1~P2-5 권장 5건 | P2-1: 9.1절, P2-2: 5.1절 search_use_cases 복원, P2-3: 8.3절, P2-4: 6.3절 readiness_gate, P2-5: 부록 A config — 모두 반영 |
| N-14 | 문제 없음 | 5 라우팅 흐름 요약 | 통합 문서 7.3절에 동일 내용 포함 |
| N-15 | 문제 없음 | 6 정확도 관점 종합 평가 | 통합 문서 9.4절에 동일 내용 포함 |
| N-16 | 문제 없음 | 7 구현 우선순위 체크리스트 | 통합 문서 12절에 반영 |
| N-17 | 문제 없음 | 8 참고 문헌 | 통합 문서 부록 B에 동일한 참고 문헌 테이블 포함 |
| N-18 | 🟡 | 리뷰 P1-8 두 번째 문제 — search_use_cases 도달 불가 코드 제거 | 리뷰에서는 "현시점에서 도달 불가 코드 제거"를 권장하면서 P2-5 구현 시 복원하는 단계적 접근을 제안. 그러나 통합 문서는 5.1절에서 search_use_cases를 ToolCall.tool Literal에 **이미 포함**시켜 놓았고, `_execute_single_tool`에서도 search_use_cases 분기를 구현해 두었음. 리뷰의 "단계적 접근"이 아닌 "즉시 복원"으로 결정을 변경한 것은 부록 C에서 설명하고 있으나, **Step 4-5 "dead code 제거"에 search_use_cases 조건이 예시로 남아 있어 상충** |

---

## 2. 불일치 사항

| # | 등급 | 항목 | 상세 |
|---|------|------|------|
| I-01 | 🔴 | **THRESHOLD_FORCE_GENERATE 값 미확정** | 통합 문서 8.3절에서 `THRESHOLD_FORCE_GENERATE: float = 0.55`를 config.py에 기재하면서 "실제 값은 confidence_scorer.py 현행 확인 후 통일"이라고 주석 처리. P0-1의 `_finalize_recovery` 코드에서도 동일 상수를 참조하나 구체값이 미정. 리뷰 P2-3에서 원안(0.55)과 시나리오(0.40)의 불일치를 지적했는데, 통합 문서가 이를 해소하지 않고 "확인 후 통일"로 남겨둠. **구현자가 어느 값을 사용해야 하는지 판단할 수 없음** |
| I-02 | 🟡 | **Step 4-5와 search_use_cases 복원의 상충** | 11절 Step 4-5에 "dead code 제거 (`_execute_single_tool`의 `search_use_cases` 조건 등)"이 남아 있으나, 5.1절과 6.4절에서 search_use_cases를 이미 도구 목록에 포함하고 구현 코드까지 제시함. Step 4-5의 예시가 현재 설계와 상충 |
| I-03 | 🟡 | **readiness_gate 예상 라인 수 차이** | 원안 5.2절: `readiness_gate.py ~160줄`, 통합 문서 6.5절: `readiness_gate.py ~180줄`. 통합 문서 6.3절에서 last_verdict 저장, PENDING 스텝 가드, `_should_ask_user` 등 추가 로직을 기술하고 있으므로 ~180줄이 정확하나, 원안과의 차이에 대한 설명이 없음 (minor) |
| I-04 | 🟡 | **Phase 전이 매핑에서 RECOVERING vs REPLANNING 결정** | 원안 5.4절: "RECOVERING을 추가할지 REPLANNING을 재사용할지는 구현 시 결정". 통합 문서 4.4절: "현행 REPLANNING을 재사용하여 State 변경을 최소화한다"로 명확히 결정함. 이것 자체는 개선이지만, 통합 문서 12절 체크리스트 P2에 `RECOVERING Phase 추가 여부 최종 결정` 항목이 없음. 원안의 P2 체크리스트에 있던 이 항목이 이미 결정되었으므로 제거된 것은 맞지만, 결정 사유를 체크리스트에서 명시적으로 "결정 완료"로 기록하면 추적이 용이함 |
| I-05 | 🟡 | **_route_after_readiness_gate에서 EXPLORE + recovery phase 처리** | 7.2절 `_route_after_readiness_gate` 코드에서 `EXPLORE` verdict 시 `exploration_phase == "initial"`이면 `knowledge_fetcher`, 아닌 경우 `recovery_agent`로 라우팅. 그러나 6.3절 `readiness_gate_node` 코드에서 EXPLORE + initial + PENDING 스텝 없음일 때 `exploration_phase = "recovery"`로 변경 + verdict를 REPLAN으로 변경하는 로직이 있음. 두 코드를 함께 보면 동작은 정확하지만, **`_route_after_readiness_gate`의 EXPLORE 분기에서 recovery_agent로 가는 경로가 실제로 도달 불가능한 코드가 될 수 있음** (readiness_gate에서 이미 REPLAN으로 변환하므로). 라우팅 테이블 7.1에서는 이 경로가 별도로 기재되어 있어 코드와 테이블 간 미세한 불일치 |
| I-06 | 🟢 | **sql_validator SEMANTIC_LOCAL 분기 확장** | 원안 3.4절에는 sql_validator 실패 유형이 `SQL_SYNTAX / SEMANTIC / STRUCTURAL / EMPTY / DB_ERROR`였으나, 통합 문서 3.4절과 7.1절에서 `SEMANTIC_LOCAL`이 신규 분기로 등장 (fix 가능 vs fix 초과). 이는 리뷰 반영 사항으로 판단되나, **리뷰 문서(cross-review-final.md)에서 SEMANTIC_LOCAL 분기를 명시적으로 제안한 부분이 없음**. 출처가 불명확 |

---

## 3. 보완 권장 사항

| # | 등급 | 항목 | 상세 |
|---|------|------|------|
| R-01 | 🔴 | **THRESHOLD_FORCE_GENERATE 현행 값 확인 및 확정** | 구현 전에 `confidence_scorer.py`에서 현행 값을 확인하고 통합 문서의 8.3절과 config.py 예시를 확정된 값으로 갱신해야 함. 미확정 상태로 구현하면 readiness_gate와 recovery_agent에서 서로 다른 값을 사용할 위험 |
| R-02 | 🟡 | **Step 4-5 dead code 예시 수정** | search_use_cases가 이미 복원되었으므로 Step 4-5의 예시에서 제거하거나 다른 예시로 교체 필요 |
| R-03 | 🟡 | **SEMANTIC_LOCAL 출처 명시** | sql_validator의 SEMANTIC_LOCAL 분기가 원안이나 리뷰 어느 쪽에서 유래한 것인지 명시하거나, 현행 코드에 이미 존재하는 분기인지 확인 필요 |
| R-04 | 🟡 | **`_should_ask_user`와 `evaluate_readiness` 간의 호출 관계 명시** | 6.3절에서 `readiness_gate_node`가 `evaluate_readiness()`를 호출한 뒤 `_should_ask_user`를 별도로 호출하는 것인지, `evaluate_readiness()` 내부에 이 로직이 통합되는 것인지 불명확. 현행 `evaluate_readiness()`가 ASK_USER verdict를 반환하는 구조이므로, `_should_ask_user`가 이 함수 내부를 수정하는 것인지 외부에서 verdict를 override하는 것인지 명시 필요 |
| R-05 | 🟡 | **`_route_after_readiness_gate` EXPLORE 분기 도달 불가 코드 정리** | readiness_gate_node에서 EXPLORE + 무 PENDING을 REPLAN으로 변환하므로, 라우팅 함수의 EXPLORE에서 recovery_agent로 가는 분기가 실질적으로 도달 불가. 라우팅 테이블과 코드를 일치시키려면 readiness_gate에서의 verdict 변환 로직과 라우팅 함수의 역할을 명확히 분리하거나, 라우팅 테이블에서 해당 경로를 "(가드에 의해 REPLAN으로 변환)" 주석 처리 |
| R-06 | 🟡 | **`_is_unresolvable_conflict` 함수의 구체적 판정 기준 미정의** | 6.3절 `_should_ask_user`에서 `_is_unresolvable_conflict(ki)`를 호출하나, 이 함수의 구체적 판정 로직(어떤 조건이 "추론 불가 충돌"인지)이 통합 문서 어디에도 정의되어 있지 않음. 9.2절 프롬프트에서 "서로 다른 테이블을 사용해야 하는 완전히 다른 의미" 등의 자연어 기준은 있으나 코드 수준 기준이 없음 |
| R-07 | 🟡 | **`KnowledgeItem.is_critical` 필드 미정의** | 6.3절 `_should_ask_user`에서 `ki.is_critical`을 참조하나, 4.2절 `KnowledgeItem` 스키마에 `is_critical` 필드가 없음. 추가 필요 여부 결정 또는 기존 필드로 대체하는 방안 명시 필요 |
| R-08 | 🟡 | **`_apply_table_updates` 함수 본체 미제시** | 6.4절 `recovery_agent_node`에서 `_apply_table_updates(reason, decision.table_updates)`를 호출하나, 이 함수의 구현 코드가 통합 문서에 없음. `_apply_knowledge_updates`는 5.2절에 구현이 있으나, table_updates 적용 로직은 누락 |
| R-09 | 🟢 | **도구 병렬화 시 LoopGuard 카운터 race condition 가능성** | 6.4절 `_execute_tools`에서 `asyncio.gather`로 병렬 실행 후 순차적으로 `increment_tool_calls()`를 호출하므로 실제로는 race condition 없음. 다만 향후 `_execute_single_tool` 내부에서 increment하도록 변경될 경우를 대비해 "increment는 gather 완료 후 순차 수행" 원칙을 명시하면 방어적 |
| R-10 | 🟢 | **`ReasoningState` 전체 필드 목록의 완전성** | 4.1절에서 기존 필드를 `# ...`으로 생략하고 신규 필드만 기재. 구현 시 기존 필드 목록이 필요하면 현행 `state.py`를 직접 참조해야 함. 통합 문서의 자족성(self-containedness) 관점에서 기존 필드의 전체 목록까지 포함하면 구현자가 state.py를 별도 참조하지 않아도 됨 |
| R-11 | 🟢 | **`calculate_readiness` 함수 참조 미확인** | 6.4절 `_finalize_recovery`에서 `calculate_readiness(reason)`을 호출하나, 현행 코드에서 이 함수명이 `evaluate_readiness()`인지 `calculate_readiness()`인지 확인 필요. `confidence_scorer.py`의 실제 함수명과 일치해야 함 |

---

## 4. LLM이 구현 시 빠트릴 수 있는 부분

| # | 등급 | 항목 | 상세 |
|---|------|------|------|
| L-01 | 🔴 | **`_is_unresolvable_conflict()` + `is_critical` 필드** | 코드만 제시하고 판정 기준/필드 정의가 없으므로, 구현자가 빈 구현(항상 True/False)을 넣거나 누락할 위험. R-06, R-07 참조 |
| L-02 | 🔴 | **`_apply_table_updates()` 구현** | 호출만 있고 본체가 없음. candidate_tables의 SELECT/REJECT/JOIN_KEY/DATE_COLUMN 갱신 로직을 구현해야 하는데, TableUpdate 스키마는 있지만 적용 시 기존 candidate_tables 구조와의 매핑 방법이 명시되지 않음. R-08 참조 |
| L-03 | 🟡 | **`planner_node` 리셋 로직의 누락 위험** | 4.5절에서 5개 필드 리셋을 명시하나, 마이그레이션 Step 2-9에서만 언급하고 Step 3의 작업 목록에는 빠져 있음. Step 2에서 planner_node 수정이 이루어져야 하는데, Step 2의 작업 2-1~2-9 중 2-9가 Step 2의 "readiness_gate 리네이밍" 제목과 맞지 않아 누락될 수 있음 |
| L-04 | 🟡 | **`THRESHOLD_FORCE_GENERATE` 이중 참조 시 값 불일치** | readiness_gate(8.3절)와 recovery_agent._finalize_recovery(6.4절) 양쪽에서 동일 상수를 사용해야 하나, 아직 값이 미확정이어서 구현자가 두 곳에 서로 다른 값을 하드코딩할 위험. R-01 참조 |
| L-05 | 🟡 | **`search_use_cases` 이전 검색 이력 전달 채널** | 10.1절 프롬프트에서 `{searched_use_case_queries}`를 참조하나, 이 데이터를 어디에 저장하는지(State 필드? recovery_agent 내부 변수?)가 명시되지 않음. planner에서 수행한 초기 검색 키워드와 recovery_agent에서 수행한 검색 키워드 모두를 추적해야 하므로, State 필드 추가가 필요할 수 있음 |
| L-06 | 🟢 | **`_build_recovery_prompt()` 함수의 전체 구현** | 10.1절에 프롬프트 템플릿은 있으나, 각 placeholder를 채우는 Python 코드(특히 `{candidate_tables_summary}`, `{dead_ends_summary}`, truncation 적용 로직)는 제시되지 않음. 마이그레이션 Step 3-3에 "기존 `_build_replan_context()` 기반"으로 안내하고 있으나, 구체적 매핑은 구현자 판단에 위임 |

---

## 5. 종합 평가

통합 문서(03-final-implementation-design.md)는 원안과 리뷰의 내용을 높은 완성도로 통합하고 있다. 원안의 핵심 구조(현황 분석, 설계 목표, 아키텍처 결정, LoopGuard, 마이그레이션, 체크리스트, 부록)가 빠짐없이 반영되었고, 리뷰의 P0 5건, P1 12건, P2 5건 전부가 구현 코드 수준까지 반영되었다.

**Critical 사항 3건**:
1. `THRESHOLD_FORCE_GENERATE` 값 미확정 (I-01, R-01, L-04)
2. `_is_unresolvable_conflict()` 및 `is_critical` 필드 미정의 (R-06, R-07, L-01)
3. `_apply_table_updates()` 구현 본체 미제시 (R-08, L-02)

이 3건은 구현 전에 확정해야 하며, 나머지 Warning/Info 사항은 구현 과정에서 순차적으로 해소 가능하다.
