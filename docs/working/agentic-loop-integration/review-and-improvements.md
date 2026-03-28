# 비판적 검토 결과 및 개선 방향

> **검토 대상**: design.md + prototype/ 전체
> **검토 관점**: 아키텍처 일관성, 원본 전략 대비, 상태 설계, 루프 안전성, 에러 핸들링, 폐쇄망 대응, 성능, 테스트, 구현 현실성, 엣지 케이스
> **검토일**: 2026-03-24

---

## 발견 사항 요약 (30건)

| 등급 | 건수 | 대표 이슈 |
|------|------|-----------|
| CRITICAL | 7건 | 경계면 상태 누락, 루프 차단, 소형 모델 비호환, Fast-Path 복구 부재 |
| WARNING | 18건 | 상태 mutation 패턴, 중복 검색, 의존성 주입 부재 |
| CAUTION | 5건 | 도구 누락, 문서화 부족, 파싱 정확도 |

---

## CRITICAL (즉시 수정 필요) — 7건

### C-01. trace_log 역변환 누락 → 프로토타입 반영 완료

- `AgenticCoreState`에 `trace_entries: list[dict[str, str]]` 필드 추가
- `agentic_to_pipeline()`에서 `result["trace_log"] = agentic_state.trace_entries` 역변환

### C-02. conversation_history 미전달 → 프로토타입 반영 완료

- `AgenticCoreState`에 `conversation_history: list[dict[str, str]]` 필드 추가
- `pipeline_to_agentic()`에서 `conversation_history` 매핑

### C-09. should_terminate가 정상 흐름 차단 가능

**위치**: `agentic_state.py` should_terminate()
**문제**: `len(pending) == 0 and current_hypothesis is None` 조건이 replan 후 가설 소진 시 explore로 불필요하게 이동한 뒤 terminate됨. replan에서 이미 실패를 결정했는데 explore를 한 번 더 실행하는 낭비.
**개선안**:
- replan에서 가설 소진 시 `replan → conclude` 직접 라우팅 조건부 엣지 추가
- `should_terminate`에 `state.final_status == "failure"` 조건 추가
- replan 노드에서 `phase = "DONE"` 시 explore 대신 conclude로 라우팅되도록 그래프 수정:
  ```python
  # agentic_core.py 변경
  graph.add_conditional_edges(
      "replan",
      lambda state: "conclude" if state.phase == "DONE" else "explore",
      {"conclude": "conclude", "explore": "explore"},
  )
  ```

### C-12. LLM/DB 호출에 타임아웃 부재

**위치**: 모든 노드의 async 호출
**문제**: plan, generate, validate의 외부 호출에 타임아웃 없음.
**개선안**:
```python
async def _with_timeout(coro, timeout_sec: float, fallback):
    try:
        return await asyncio.wait_for(coro, timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.warning("Timeout", timeout=timeout_sec)
        return fallback
```

### C-15. plan 노드의 소형 모델 비호환 (검토자피드백 : 이 내용은 적용하지 않기로 함, plan 에서 LLM 추론 필수)

**위치**: `nodes/plan.py` — Heavy LLM 호출
**문제**: 한 번의 LLM 호출로 질의 분해 + 가설 수립 + 실행계획 생성을 모두 수행. 7B~70B 모델의 메타인지 능력 부족으로 가설 품질 저하.
**개선안**:
1. plan을 "분해 전용"과 "가설 전용" 2-Phase로 분리
2. 가설 수립을 rule-based 템플릿으로 대체하는 fallback:
   ```python
   # NormalizedQuery.entities 유형에 따른 가설 템플릿
   HYPOTHESIS_TEMPLATES = {
       "AGGREGATE": [활용사례 기반, 테이블메타 직접탐색, 보고서SQL 참조],
       "EXTRACT": [활용사례 기반, 테이블메타 직접탐색],
       "COMPARE": [활용사례 기반, 피벗 구조 탐색],
   }
   ```
3. `settings.model_size` 설정에 따라 LLM/Rule-based 분기

### C-22. Layer 2 LLM 의미 검증의 소형 모델 품질 불확실

**위치**: `nodes/validate.py` _validate_layer2()
**문제**: 소형 모델의 SQL 파싱 능력이 제한적 → false positive/negative 빈번.
**개선안**:
- Layer 2를 2단계로 분리:
  - Layer 2a (Rule-based): **구조적 sanity check만 수행**. 한국어 업무 용어 ↔ 영어 컬럼명 ↔ 코드값 의미 매칭은 불가하므로, 명백한 구조적 누락만 체크:
    - query_decomposition에 group_by 있는데 SQL에 GROUP BY 절 자체가 없음
    - query_decomposition에 agg_function 있는데 SQL에 집계함수가 하나도 없음
    - SQL에 사용된 테이블이 candidate_tables에 없음
    - SQL에 사용된 컬럼이 해당 테이블에 존재하지 않음
    - ※ "고객 수"가 COUNT(cust_no)에 대응하는지 등 **의미적 매칭은 하지 않음** (과잉 필터링 방지)
  - Layer 2b (LLM): "이 SQL이 사용자 질의의 의도를 반영하는가?" 의미 검증 담당
- 소형 모델 환경에서는 Layer 2b를 스킵하는 설정 추가 (Layer 2a sanity check만으로도 명백한 오류는 걸러짐)

### C-24. Fast-Path 검증 실패 시 복구 경로 부재 → 해소 (프로토타입 반영 완료)

- `route_from_validator()`에 Fast-Path 실패 감지 로직 추가: `fast_path_triggered and result != SUCCESS → "explore_after_fast_path"`
- `build_agentic_core()`의 conditional_edges에 `"explore_after_fast_path": "context_explorer"` 경로 추가

---

## WARNING (설계 보완 필요) — 18건

### C-03. table_metas의 columns 빈 리스트 → 프로토타입 반영 완료

- `agentic_to_pipeline()`에서 `CandidateTable.relevant_columns` → `ColumnMeta` 리스트로 변환

### C-04. 외부 캐시 인터페이스 미정의
- cache_refs 필드 존재하나, CacheStore 프로토콜/스텁 없음
- 개선: `CacheStore` Protocol + `InMemoryCacheStore` 스텁 정의

### C-05. 자기검증 체크리스트 미반영
- generate의 context에 self_verification_checklist 키 없음
- 개선: query_decomposition에서 체크리스트 자동 생성

### C-07. LangGraph 상태 관리와 충돌하는 mutation 패턴 → 해소 (프로토타입 반영 완료)

- recovery_planner: `state.record_dead_end()` 직접 mutation → `model_copy()` + updates dict 반환으로 수정
- planner: `top.status = "ACTIVE"` 직접 mutation → `model_copy()` 후 복사본 수정으로 수정
- 패턴: state를 읽기만 하고, 변경은 복사본으로 하고, dict로 반환

### C-08. normalized_query: Any 타입
- mypy --strict 위배
- 개선: `TYPE_CHECKING` 블록에서 import, `Optional[NormalizedQuery]` 선언

### C-10. explore/assess 조기 탈출 조건 불일치 → `evaluate_readiness()` 단일 판정 함수로 해소
- **문제**: 판단 로직이 explore 내부, assess 노드, route_from_assess 3곳에 분산되어 조건 불일치 발생
- **개선 (프로토타입 반영 완료)**:
  - `confidence_scorer.py`에 `ReadinessVerdict` enum + `evaluate_readiness()` 단일 판정 함수 추가
  - explore 조기 탈출: `evaluate_readiness(temp_state) == ReadinessVerdict.GENERATE` 시 break
  - route_from_assess: `evaluate_readiness(state).value` 한 줄로 위임
  - assess 노드: `VERDICT_TO_PHASE[verdict]`로 phase 매핑만 수행
  - 효과: 판단 로직이 한 곳에 집중되어 불일치 원천 차단

### C-13. explore 노드 전체 예외 미처리
- 개별 도구 실행만 try/except, 루프 외부 예외 시 전체 실패
- 개선: 최상위 try/except 추가, 부분 결과 반환 + phase = "REPLANNING"

### C-14. agentic_entry_node 서브그래프 실행 실패 시 미처리
- 개선: try/except + `{"error_message": "...", "status": "ERROR"}` 반환

### C-16. replan LLM 호출 소형 모델 대응
- _generate_new_hypotheses의 dead-end 분석 + 대안 도출은 소형 모델 한계
- 개선: 가설 큐를 plan에서 충분히 준비, replan은 큐 소비만. 소진 시 rule-based fallback

### C-17. 초기 수집과 explore의 중복 검색
- plan의 _collect_initial_context에서 사용한 쿼리가 searched_queries에 미등록
- 개선: `updates["searched_queries"]`에 초기 검색 쿼리 추가

### C-18. 서브그래프 매번 빌드
- agentic_entry_node에서 build_agentic_core() + compile() 매 요청 호출
- 개선: 모듈 레벨 싱글톤 또는 build_pipeline에서 미리 컴파일

### C-20. 외부 의존성 주입 메커니즘 부재 → 불필요 (기존 Dummy 모드로 충분)

- 커넥터 레벨의 `use_dummy=True`가 이미 동일한 역할 수행
- 모든 커넥터(MongoDB, ES, Qdrant, PostgreSQL)가 Dummy 모드 내장
- 외부 인프라 없이 전체 파이프라인 테스트 가능 → Protocol 추상화 불필요

### C-21. _is_covered_by_tables 제거 → confidence_scorer 3차원 축소 (옵션 C) + 역할 분리로 해소
- **문제**: 한국어 업무 용어 ↔ 영어 컬럼명 텍스트 매칭은 원천적으로 부정확
- **개선 (프로토타입 반영 완료)**:
  - `_is_covered_by_tables()` 함수 제거
  - `calculate_readiness()`를 4차원 → 3차원으로 축소 (옵션 C):
    - term_resolution (50%): knowledge_items의 CONFIRMED 비율 (용어+테이블 통합)
    - use_case_match (30%): 유사 SQL 유사도
    - join_path (20%): 조인 경로 확인
  - `CandidateTable.confirmed` 필드 제거 — 테이블 적합성 판단을 knowledge_items로 통합
  - `CandidateTable`: 구조 데이터 운반용으로만 사용 (컬럼, 조인키)
  - `knowledge_items`: 모든 확인 상태의 단일 진실 공급원 (table:TB_CUST_INFO → CONFIRMED)
  - context_explorer에서 테이블 발견 시 `KnowledgeItem(key="table:...", status="CANDIDATE")` 등록
  - sql_generator/result_finalizer/agentic_to_pipeline: knowledge_items 기반으로 CONFIRMED 테이블 조회

### C-23. search_use_cases 결과에서 sqlglot 힌트 추출 미구현
- explore 내부에서 search_use_cases 결과의 structural_hints 갱신 로직 없음
- 개선: _interpret_result에 search_use_cases 핸들러 추가, sql_hint_extractor 호출

### C-25. 사용자 명확화(ask_user) 응답 후 재진입 미설계 → 설계 완료 (design.md 5-9절)

- LangGraph checkpointer (RedisSaver) + turn_id 기반 중간 상태 저장/재개
- 핵심 개념 정리:
  - session_id: 대화 세션 단위 (conversation_history 키, 전체 대화 누적)
  - turn_id: 질의 해결 단위 (checkpointer 키, 명확화 왕복 포함)
  - LangGraph의 thread_id 파라미터에 turn_id를 전달
- turn_id 발급: resolve_history가 NEW/SKIP → 새 turn_id, CONTINUE → 기존 turn_id 유지
- 프로토타입 코드는 미구현 (실제 구현 시 RedisSaver 설정 필요)

### C-26. is_critical 판단 → context_explorer의 LLM이 도구 결과 해석 시 결정 (프로토타입 반영 완료)
- **문제**: all_critical_confirmed()가 모든 항목을 critical로 취급하여, 보조 테이블 미확인으로 SQL 생성이 불필요하게 차단됨
- **개선**:
  - `KnowledgeItem.is_critical: bool = True` 필드 활성화 (기본값 True = 보수적)
  - `all_critical_confirmed()`: is_critical=True인 항목만 필터링
  - **판단 주체**: context_explorer의 LLM (explore_observe 프롬프트)이 도구 결과 해석 시 status와 is_critical을 동시에 결정
  - 프롬프트 지시: "이 항목 없이 SQL을 만들 수 있는가?" → is_critical: true/false
  - 프로토타입에서는 기본값 True (보수적), 실제 구현 시 LLM 해석으로 False 설정

### C-28. _infer_failure_type 반환 타입 str → FailureType
- 타입 힌트 불일치

### C-29. LoopGuard 클래스와 MAX_LOCAL_FIXES 상수 배치 순서
- 상수를 클래스 변수로 이동 또는 파일 상단 이동

---

## CAUTION (구현 시 주의) — 5건

### C-06. search_glossary 도구 누락 → 프로토타입 반영 완료
- context_explorer의 tool_map에 `search_glossary` 등록, 스텁 함수 추가
- 중복 방지 목록에도 포함
- 데이터 소스: MongoConnector.search_glossary() (glossary 컬렉션)

### C-11. ask_user 라우팅 조건 문서화 부족 → C-10 반영으로 해소

- `evaluate_readiness()` 단일 판정 함수 도입(C-10)으로 라우팅 로직이 한 곳에 집중됨
- 판단 우선순위가 docstring에 명시: TERMINATE → ASK_USER → GENERATE → EXPLORE → REPLAN
- phase 의존성 제거 — `has_conflicted_items(state)` 직접 판단으로 변경

### C-19. explore 내부에서 AgenticCoreState 전체 생성 오버헤드 → 보류 (실제 영향 미미)

- Pydantic 검증은 ms 단위, 도구 실행(DB/ES)은 수십~수백 ms → 무시 가능
- 실제 구현 시 병목이 확인되면 evaluate_readiness()에 경량 시그니처 추가로 최적화

### C-27. 미확인 단일 테이블 사용 가능성 → C-21 반영으로 해소

- knowledge_items 기반 테이블 판단 도입(C-21)으로, 단일 테이블도 CONFIRMED/PROBABLE 상태 확인 후 사용
- evaluate_readiness가 GENERATE로 판정한 시점이면 해당 테이블의 knowledge도 충분히 승격된 상태

### C-30. Sybase IQ 파싱 정확도 85~90% → 개선 대상 제외 (추후 검토)

- 파싱 실패 시 빈 힌트 폴백이 이미 안전하게 동작 (parse_sql_safe → None → 빈 힌트)
- KEY JOIN 등 고유 구문 전처리를 해도 결과는 동일하게 빈 힌트 → 전처리 불필요
- 폐쇄망 배포 후 실제 SQL 이력의 파싱 실패율을 모니터링하고, 필요 시 재검토

---

## 핵심 개선 권고 5가지

### 1. 경계면(Entry/Exit) 상태 변환 완성도 (C-01, C-02, C-03)

서브그래프 격리 전략의 핵심은 진입/탈출 변환의 완전성이다.
trace_log, conversation_history, table columns 3개의 누락 필드를 반드시 추가하고,
**경계면 변환에 대한 단위 테스트를 Phase 1에서 작성**해야 한다.

### 2. 대형 모델 적극 활용 + fallback 보험 유지 (C-15, C-16, C-22)

폐쇄망 배포 모델이 Qwen3.5 397B / GPT OSS 120B+ 급 대형 모델일 가능성이 높으므로,
LLM Heavy 노드(planner, recovery_planner, sql_validator-Layer2b)를 **대형 모델 기준으로 설계**하되,
rule-based fallback은 모델 교체/양자화/장애 대비 보험으로 유지.

- planner: 단일 LLM 호출로 분해+가설+계획 한 번에 (fallback: 2-Phase 분리)
- recovery_planner: LLM으로 교훈 도출 + 새 가설 수립 (fallback: 가설 큐 소비만)
- sql_validator: Layer 2a + Layer 2b 모두 활성화 (fallback: Layer 2a만)
- 설정 플래그(`plan_use_llm`, `validate_layer2b_enabled`, `replan_use_llm`)로 런타임 전환
- 상세: design.md 부록 A 참조

### 3. LangGraph 상태 관리 패턴 준수 (C-07) → 해소

- recovery_planner, planner 모두 `model_copy()` + updates dict 반환으로 수정 완료
- 패턴: state를 읽기만 하고, 변경은 복사본으로 하고, dict로 반환

### 4. Fast-Path 실패 복구 + 사용자 명확화 재진입 (C-24, C-25) → 해소

- **C-24 (프로토타입 반영 완료)**: `route_from_validator`에 Fast-Path 실패 시 `context_explorer` 복귀 경로 추가
- **C-25 (설계 완료)**: LangGraph RedisSaver checkpointer + turn_id 기반 재진입 설계 (design.md 5-9절)
  - session_id: conversation_history 키 (세션 전체 대화 누적)
  - turn_id: checkpointer 키 (질의 해결 단위, LangGraph thread_id로 사용)
  - 코드 구현은 실제 구현 시 RedisSaver 설정으로 대응

### 5. 의존성 주입 패턴 확립 (C-20) → 불필요

- 기존 커넥터의 `use_dummy=True` Dummy 모드가 동일한 역할을 이미 수행
- Protocol 추상화 없이도 외부 인프라 없는 테스트 가능

---

## 설계 문서 보완 대상 섹션

| 섹션 | 보완 내용 |
|------|-----------|
| 3-1. 상태 설계 | trace_entries, conversation_history, is_critical 필드 추가 |
| 4-2. 서브그래프 구조 | replan → conclude 조건부 엣지, validate → explore (Fast-Path 복구) 추가 |
| 5-1. plan 노드 | 2-Phase 분리 + rule-based fallback |
| 5-5. validate 노드 | Layer 2를 2a(rule)+2b(LLM)로 분리 |
| 5-8. ask_user | checkpointer 기반 중간 상태 저장/재개 |
| 7. 서비스 재사용 | search_glossary 도구 추가 |
| 8. 디렉토리 구조 | exploration_cache.py에 CacheStore Protocol 추가 |
| 9. 구현 우선순위 | Phase 1에 경계면 단위 테스트, ToolKit Protocol 추가 |
| 10. 리스크 | 소형 모델 fallback 경로를 리스크 완화에 명시 |
