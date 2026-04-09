# 설계문서 검증 보고서: tool-result-renderer-design.md

- **대상**: `docs/working/tool-result-renderer-design.md`
- **검증일**: 2026-04-04
- **검증자**: Code Reviewer Agent


---

## 관점 1: 결정사항 반영 확인 (21개)

### 1. 파이프라인 책임 분리: fetcher는 step.raw_result에만 저장, interpreter가 gatekeeper
- **[OK]** S2.1, S3.1~3.7에서 명확히 기술. "interpreter가 진정한 gatekeeper"(S2.1), fetcher는 step.raw_result에 저장하고 state는 read-only(S3.4).

### 2. state 쓰기 범위 정의
- **[OK]** S3.4에서 Read-Only(도구 결과 데이터)와 갱신 허용(스텝 메타: step.status, searched_queries, loop_guard, phase)을 표로 정의.

### 3. enrichment 통합: search_use_cases(테이블메타+샘플+코드), search_table_meta(샘플만)
- **[OK]** S3.2 표에서 search_use_cases는 `{use_cases, tables, codes}`, search_table_meta는 `{tables}` + 샘플로 명시.

### 4. Phase 2 삭제: _sample_unsampled_tables, _observe_all_date_distributions 완전 제거
- **[OK]** S3.3에서 두 함수의 삭제와 대체 방안을 명시.

### 5. date_distribution: 플래너가 "필요 시" 계획에 포함, readiness_gate + recovery가 안전망
- **[OK]** S3.3에서 "시간 조건이 있는 질의에서 ... get_date_distribution을 포함하라" + "readiness_gate + recovery 사이클이 안전망"으로 기술.

### 6. 멀티 라운드 중복 처리: seen_tables + re_evaluate_table 별도 도구
- **[OK]** S3.5에서 "이미 explored_tables에 있는 테이블은 건드리지 않는다", 재판정은 `re_evaluate_table` 별도 도구로 명시.

### 7. 네이밍 변경: CandidateTable->TableEntry, candidate_tables->explored_tables
- **[OK]** S8.1에서 표로 명시.

### 8. 출력 형식: top-level selected/rejected 제거, interpretations 하위에 통합 + SelectionStatus
- **[OK]** S4.1, S4.2에서 "top-level selected/rejected 배열을 제거하고 interpretations 하위에 통합" 명시. explored_tables/explored_use_cases/explored_biz_terms/explored_biz_manuals 배열과 SelectionStatus 사용.

### 9. 판정 범위 확장: interpreter가 테이블, use_case, biz_terms, biz_manuals 모두 판정
- **[OK]** S3.6 표에서 4가지 도구 결과에 대한 SELECTED/REJECTED 판정을 명시.

### 10. code_map은 판정 없이 적재
- **[OK]** S3.6 표에서 "판정 없이 적재"로 명시.

### 11. REJECTED도 state에 적재
- **[OK]** S3.6에서 "REJECTED도 state에 적재한다"와 3가지 이유(정보 유실 방지, 탈락 사유 표시, 재탐색 방지) 명시.

### 12. interpreter 완료 후 raw_result = None
- **[OK]** S3.1("interpreter 완료 후 step.raw_result = None"), S3.6("적재 완료 후 모든 DONE 스텝의 step.raw_result = None") 두 곳에서 명시.

### 13. discovered_facts 유지
- **[OK]** S8.6에서 명시적으로 "제거하지 않는다" + 라운드를 넘어 누적되는 유일한 인사이트 저장소라는 사유 기술.

### 14. dead field 제거: KnowledgeItem.is_inferred, conflicted_bounce_count, last_verdict
- **[OK]** S8.5에서 3개 필드 모두 나열.

### 15. 토큰 fallback: Level 0(배치) -> Level 1(스텝별 개별 + 종합 판정)
- **[OK]** S6.2에서 Level 0/1 전략을 상세히 기술.

### 16. LLM 연결 실패: 모든 노드 공통 fallback, 사용자 알림 + 그래프 종료
- **[ISSUE]** 설계문서에 **LLM 연결 실패 시 공통 fallback 정책에 대한 명시적 기술이 없다**. S6(토큰 fallback)은 토큰 초과 대응이지 LLM 연결 실패 대응이 아니다. interpreter 전용 정책이 없다는 결정은 반영되었으나(없으므로), "모든 노드 공통 fallback -> 사용자 알림 + 그래프 종료"라는 정책 자체가 문서 어디에도 기술되지 않아 구현자가 혼란할 수 있다.

### 17. 9개 도구별 렌더러
- **[OK]** S7.1에서 `_TOOL_RENDERERS` 맵으로 9개 렌더러를 모두 나열. S5에서 각 렌더러의 상세 렌더링 예시 제공.

### 18. ObservedDateColumn.recent_values: 최근 10건 날짜 저장
- **[OK]** S5.7에서 "sorted(dates, reverse=True)[:10]"과 S8.3에서 모델 필드 추가 명시.

### 19. SelectionStatus Enum 통합
- **[OK]** S8.2에서 "TableSelectionStatus + RelevanceStatus -> SelectionStatus 단일 Enum (완료)" 명시.

### 20. 프롬프트 갱신 범위
- **[OK]** S9.4에서 5가지(출력 형식 템플릿, 예시 2건, 분석 지침, dead field 제거, 입력 형식 설명) 모두 나열.

### 21. 수정 대상 파일: reasoning_preparer.py, reasoning_preparer_system.txt, confidence_scorer.py 포함
- **[ISSUE]** S10에서 `reasoning_preparer.py`와 `reasoning_preparer_system.txt`는 포함되어 있으나, **`confidence_scorer.py`의 경로가 `src/agents/nodes/reason/confidence_scorer.py`로 기재되어 있다.** 실제 파일은 `src/services/confidence_scorer.py`에 위치한다. 또한 현재 confidence_scorer.py는 `candidate_tables` 문자열을 직접 참조하지 않고 `ReasoningState`의 속성으로 접근하므로 네이밍 변경 영향이 없을 수 있다(S10의 변경 필요성 재확인 필요).


---

## 관점 2: 잘못 반영된 내용 확인

### "반드시 포함하라"가 남아있는지
- **[OK]** 설계문서 전문에서 "반드시 포함"이라는 표현은 출력 규칙(프롬프트 지시)에만 사용되며, date_distribution에 대해서는 "필요 시 계획에 포함"(S3.3)으로 올바르게 기술됨.

### LLM 실패 시 PENDING 상태 적재 정책
- **[OK]** 문서에 PENDING 상태로 적재한다는 내용이 없다. 결정대로 공통 fallback으로 처리하는 방향.

### enrichment에 date_distribution 포함 여부
- **[OK]** S3.2 표에서 search_use_cases의 enrichment는 "테이블메타+샘플+코드"이고, search_table_meta는 "샘플만". date_distribution은 enrichment에 포함되지 않으며 별도 도구로 처리됨.

### fetcher가 동적으로 스텝을 추가한다는 내용
- **[OK]** S3.2~3.3에서 enrichment는 "step.raw_result에 결과를 통합"하는 것이지 execution_plan에 스텝을 추가하는 것이 아님. "execution_plan에 스텝을 추가하지 않고"(기존 _fetch_use_case_related_metas 주석)라는 기조를 유지.

### 추가 발견: 현행 프롬프트의 출력 형식과 설계 불일치
- **[ISSUE]** 현행 프롬프트(`knowledge_interpreter_system.txt`)의 출력 형식에는 여전히 **top-level `selected`, `rejected`, `relevant_use_cases` 배열**이 존재한다(L110~149). 설계문서 S9.3은 이를 제거하고 interpretations 하위로 통합한다고 했으나, 현행 프롬프트의 **분석 지침 6번(유사 SQL 평가)**도 `relevant_use_cases` 배열을 참조하고 있어, 프롬프트 전면 갱신 시 이 지침도 함께 변경해야 한다.

### 추가 발견: 현행 프롬프트에 is_inferred가 남아있는지
- **[OK]** 현행 프롬프트에 `is_inferred`에 대한 직접 언급은 없다. 다만 "필수 여부(is_critical) 판단" 섹션은 존재하며, is_critical은 제거 대상이 아니므로 문제없음.


---

## 관점 3: 현재 구현에 적용 시 문제점

### 3-1. state.py -- 현재 state 모델 구조가 설계와 충돌하는지

- **[ISSUE]** `CandidateTable` 클래스(L202) 주석에 "ES에서 파싱"이라고 되어 있으나 실제로는 MongoDB에서 파싱한다. ES 제거 후 미정리된 잔재. 설계 적용 시 TableEntry로 리네이밍하면서 주석도 정리해야 한다.

- **[ISSUE]** `last_verdict`(L518)는 dead field 제거 대상이지만, `readiness_gate.py` L72에서 `reason.last_verdict = verdict.value`로 매 라운드 갱신하고 있다. 삭제 시 readiness_gate의 해당 라인도 제거해야 한다.

- **[ISSUE]** `conflicted_bounce_count`(L523)는 dead field 제거 대상이지만, `reasoning_preparer.py` L56에서 초기화(`reason.conflicted_bounce_count = 0`)하고 있다. 삭제 시 reasoning_preparer도 수정 필요. **설계문서 S10의 수정 대상 파일에서 reasoning_preparer.py의 변경 내용이 "candidate_tables->explored_tables 네이밍 변경"으로만 기술되어 있으나, 실제로는 dead field 초기화 코드 제거도 필요하다.** (reasoning_preparer.py에는 candidate_tables 참조가 없으므로 네이밍 변경은 해당 없음)

- **[ISSUE]** `ExecutionStep`(L111~121)에 `raw_result` 필드가 아직 없다. 설계 S3.1의 핵심 변경이며, 이 필드가 추가되어야 fetcher의 state read-only 전환이 가능하다.

- **[OK]** `ObservedDateColumn`(L133~138)에 `recent_values` 필드가 아직 없으나, 이는 예상된 미구현 상태이며 설계 S8.3에서 추가 예정으로 기술됨.

### 3-2. knowledge_fetcher.py -- state 직접 쓰기 제거 시 빠지는 부분

- **[ISSUE]** fetcher의 `_apply_tool_result`(L175~209)에서 `get_sample_rows`, `get_date_distribution`, `search_column_values`, `get_column_profile` 결과를 **candidate_tables 내부 객체에 직접 반영**한다(sample_rows, observed_date_columns, discovered_values 등). 설계에서 fetcher는 state read-only라고 했지만, 이 4개 관찰 도구의 결과가 특정 테이블의 보조 필드에 반영되는 현행 로직을 step.raw_result로 전환할 때, **interpreter가 raw_result에서 해당 테이블을 매칭하여 보조 필드를 채우는 로직을 새로 구현해야 한다.** 설계문서 S3.6에서 "관찰 도구는 판정 대상 아님, 해당 테이블의 보조 정보로 반영"이라고만 되어 있고 구체적인 매칭/반영 로직은 기술되지 않았다.

- **[ISSUE]** `_fetch_use_case_related_metas`(L396~477)에서 발견된 테이블을 `candidate_tables.append(ct)`로 직접 적재하고, 코드 메타를 `code_map[col]`에 직접 적재한다. 설계에서는 이것을 enrichment로 통합하여 step.raw_result에 저장한다고 했지만, **enrichment 결과(tables, codes)가 raw_result에 들어간 후 interpreter에서 이를 파싱하여 TableEntry와 code_map으로 변환하는 구체적인 파싱 로직이 설계에 없다.** 렌더러는 프롬프트 텍스트 생성만 담당하고, state 적재 로직은 별도로 필요하다.

### 3-3. knowledge_interpreter.py -- 현재 파싱 로직이 새 출력 형식으로 전환 시 문제

- **[ISSUE]** `_parse_batch_result`(L374~399)가 `data.get("selected", [])` / `data.get("rejected", [])` top-level 배열에서 판정 결과를 읽는다. 새 출력 형식에서는 `interpretations[i].explored_tables[j].status`에서 SELECTED/REJECTED를 읽어야 한다. 파싱 로직 전면 재작성 필요.

- **[ISSUE]** `BatchInterpretResult` 클래스(L60~78)의 `selected`, `rejected` 필드가 제거되고, 대신 `explored_tables`, `explored_use_cases`, `explored_biz_terms`, `explored_biz_manuals`를 interpretations에서 추출하는 로직이 필요하다. 현행 구조에서는 도구별 판정이 아닌 테이블 단위 판정이므로 데이터 구조가 근본적으로 다르다.

- **[ISSUE]** 현행 L137~152의 `selected_map`/`rejected_map` 기반 마킹 로직은 테이블에 대해서만 동작한다. 새 형식에서는 use_case, biz_terms, biz_manuals에 대한 마킹 로직도 추가해야 하며, 각각 다른 state 필드(explored_use_cases, explored_biz_terms, explored_biz_manuals)에 반영해야 한다.

### 3-4. knowledge_interpreter_system.txt -- 현행 프롬프트 구조

- **[ISSUE]** 현행 프롬프트의 입력 변수(`{tool_results}`, `{table_observations}`)가 분리되어 있다. 새 설계에서는 렌더러가 스텝 단위 블록을 조립하므로 하나의 변수로 통합되어야 한다. 프롬프트 변수 리팩터링과 함께 `render_prompt` 호출부(`_interpret_batch` L330~341)도 수정 필요.

### 3-5. readiness_gate.py -- candidate_tables 외 다른 곳에서도 참조하는지

- **[OK]** readiness_gate.py에서 `candidate_tables`는 3곳(L91 로깅 문자열, L154 len(), L201 len())에서 참조. 모두 `reason.candidate_tables` 경로로 접근하므로 state 필드명 변경 시 일괄 치환으로 대응 가능.

- **[ISSUE]** readiness_gate.py L72의 `reason.last_verdict = verdict.value`는 dead field 제거 시 삭제 필요하지만, **이 값을 다른 노드에서 참조하는지 확인 필요**. Grep 결과 `last_verdict`는 state.py(선언), readiness_gate.py(갱신), reasoning_preparer.py(초기화) 3곳에서만 사용되어 안전하게 제거 가능.

### 3-6. reasoning_preparer.py -- 존재 여부 및 candidate_tables 참조

- **[OK]** 파일 존재 확인. `candidate_tables` / `CandidateTable` 참조가 **0건**이다. 따라서 설계문서 S10에서 기술한 "candidate_tables->explored_tables 네이밍 변경"은 **해당 없음**.

- **[ISSUE]** 설계문서 S10에서 reasoning_preparer.py의 변경 내용이 부정확하다. 실제 필요한 변경은 `conflicted_bounce_count` 초기화 코드와 `last_verdict` 초기화 코드 제거(dead field 정리)이며, candidate_tables 네이밍 변경은 해당 없다.

### 3-7. confidence_scorer.py -- 존재 여부 및 candidate_tables 참조

- **[ISSUE]** 파일 위치가 `src/services/confidence_scorer.py`이며, 설계문서 S10에 기재된 `src/agents/nodes/reason/confidence_scorer.py`는 **존재하지 않는 경로**이다.

- **[OK]** `src/services/confidence_scorer.py`에서 `candidate_tables` / `CandidateTable` 직접 참조가 0건. `ReasoningState` 객체의 속성으로 접근하므로, state.py에서 필드명이 `explored_tables`로 변경되면 `ReasoningState` 클래스의 필드명 변경만으로 자동 반영된다. confidence_scorer.py 자체의 코드 변경은 불필요할 수 있다.


---

## 요약

| 등급 | 건수 | 핵심 사항 |
|------|------|----------|
| Critical | 4 | fetcher->interpreter 관찰 도구 매칭/반영 로직 미기술, enrichment->state 적재 파싱 로직 미기술, interpreter 파싱 전면 재작성 필요, confidence_scorer.py 경로 오류 |
| Warning | 4 | LLM 실패 공통 정책 미기술, reasoning_preparer.py 변경 내용 부정확, dead field 제거 영향 범위 누락(readiness_gate, reasoning_preparer), 현행 프롬프트 분석 지침도 갱신 필요 |
| Info | 2 | CandidateTable 주석의 ES 잔재, confidence_scorer.py 변경 필요성 재검토 |

### Critical 세부

1. **(S3.6 보완 필요)** fetcher가 현재 `_store_sample_rows`, `_store_date_distribution`, `_store_column_values`, `_store_column_profile`로 candidate_tables 내부 객체에 직접 반영하는 로직이 있다. 이것을 step.raw_result로 전환 시 interpreter가 raw_result에서 테이블을 매칭하여 보조 필드를 채우는 구체적인 처리 흐름이 설계에 없다.

2. **(S3.2 보완 필요)** search_use_cases enrichment의 tables/codes가 raw_result에 통합된 후, interpreter에서 이를 TableEntry와 code_map으로 변환하여 state에 적재하는 파싱 로직이 설계에 없다. 렌더러는 프롬프트 텍스트 생성만 담당하므로 별도의 적재(hydration) 로직 설계가 필요하다.

3. **(S10 경로 오류)** `src/agents/nodes/reason/confidence_scorer.py`는 존재하지 않음. 올바른 경로는 `src/services/confidence_scorer.py`.

4. **(S10 변경 내용 오류)** reasoning_preparer.py의 변경은 candidate_tables 네이밍이 아니라 dead field(last_verdict, conflicted_bounce_count) 초기화 코드 제거.
