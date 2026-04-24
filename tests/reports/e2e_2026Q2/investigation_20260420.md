# Prescan 실패 근본 원인 조사 (2026-04-20)

> 대상: N-02 Critical, K-01 Minor.
> 방법: `tests/reports/e2e_2026Q2/traces/{ID}.trace.json` 의 LLM 호출·의사결정·검색 결과 분석.
> 영향: extended 진행 중 N-04 도 동일 visualizer 패턴으로 FAIL 관찰 → 구조 이슈 가능성.

## 목차
- [1. N-02 Critical](#1-n-02-critical) — 지점 수 조회, replan_count=10 → error_end
- [2. K-01 Minor](#2-k-01-minor) — REDISPLAY route 정상이나 visualizer=NONE
- [3. 공통 관찰 — visualizer 보수성](#3-공통-관찰--visualizer-보수성)
- [4. 조치 제안](#4-조치-제안)

---

## 1. N-02 Critical

### 증상
- 경과 305s, replan_count=10 (한도 초과), sql_executor 미진입, error_end
- 응답: *"지점 고유 코드(BR_CD) 컬럼 미확인 — 지점 수 산출에 필수"*
- 직전 run(22:29)은 동일 코드로 PASS (replan=1, SQL=`COUNT(*) FROM TB_ADW_COM001M WHERE BR_DCD='02'`)

### 근본 원인 (트레이스 분석)

**원인 A — 테이블 선택 오류**
- `search_table_meta` 1차 조회: `지점 지점 수 개수 건수` → 4건 수집
- `context_interpreter` (LLM) 가 **`TB_ADW_COM006M, COM003M, COM021M`** 을 SELECTED 으로 판정
- 정답 `TB_ADW_COM001M` (지점 마스터) 는 검색 결과에 포함되지 않았거나 LLM 이 제외
- **이전 run 은 COM001M 을 찾았음** → 임베딩·Rerank 결과 변동 또는 LLM 판정 변동 가능성

**원인 B — 측정법(measure) Unresolved 고착**
- 10회 batch_interpret 모두 동일 판정 반복:
  *"해당 활용사례는 지점 정보 테이블 일부만 참고 가능하나 집계 산출식 미확인으로 **measure:지점 수 Unresolved 상태 유지**"*
- 과거 use_cases 에 `BR_DCD='02'` 필터는 있으나 `COUNT(*)` 집계 패턴은 없어 LLM 이 FORMULA 미확인으로 판단
- `knowledge_items = 0 confirmed / 1 unresolved` 상태 10회 readiness_gate 재진입
- 현실: 마스터 테이블 `COUNT(*)` 는 trivial — 별도 산출식 불필요. **LLM 이 과도하게 strict**

**원인 C — 루프 탈출 실패**
- replan_count 5 한도 도달 → force_generate 전환 (readiness_gate.chosen=generate_sql 로 변경됨)
- 하지만 knowledge_items 는 여전히 0/1 UNRESOLVED
- sql_generator 가 맥락 부족으로 실패 → recovery_agent → sql_generator 재시도 5회 반복 → error_end
- force_generate 전환이 있긴 하나 sql_generator 가 UNRESOLVED KI 로도 올바른 SQL 을 생성하지 못함

### 핵심 증거
```
readiness_gate trail (10회):
  #1-4: replan (knowledge=0/1, tables=7)
  #5-6: generate_sql (knowledge=0/1, tables=10)   ← force_generate 진입
  #7-10: generate_sql (knowledge=0/1, tables=14)   ← KI 여전히 UNRESOLVED
```

---

## 2. K-01 Minor

### 증상
- route=REDISPLAY ✅, sql_executor 미진입 ✅ (재실행 회피 성공)
- 턴1·2 모두 `viz=NONE` — 턴2의 "원형 차트" 요청 무시

### 근본 원인

**단일 원인 — visualizer_judgment LLM 이 사용자 요청 무시**

1. **continue_orchestrator (turn 2) handoff_note** (정상):
   ```
   ### 시각화/출력 지시
   - 직전 턴에서 지점별잔액 조회 결과 테이블을 원형 차트(pie_chart)로 변환
   - X축: 지점명, Y축: 대출잔액합계 값 활용
   - SQL 및 결과 데이터는 재사용, 재조회 불필요
   ```
2. **visualizer_judgment (turn 2) response** (문제):
   ```json
   {
     "chart_type": "none",
     "reason": "총 20건은 차트로 보기에 적절하나, 기본적인 지점별잔액합계의 비교분석 시각으로
               원형 차트는 적절하지 않음 (N6)"
   }
   ```
   - `(N6)` = visualizer 프롬프트의 내부 규칙 (PIE 는 비율·구성 시각화만 허용)
   - **사용자 명시 요청 > rule** 우선순위가 프롬프트에 없어 LLM 이 규칙을 우선 적용

---

## 3. 공통 관찰 — visualizer 보수성

extended 런 N-04 에서도 동일 패턴 FAIL 발생:
- Query: *"고객등급별 고객 수 분포 보여줘"*
- rows = 182 (≫ min_rows_for_visualization=5)
- SQL: `SELECT cus_grd_cd, COUNT(*) FROM … GROUP BY …`
- viz = NONE (HYP: bar/pie/table_only 중 하나 기대)

→ visualizer 가 GROUP BY 결과에도 NONE 을 반환하는 경향. prompt judgment 규칙이 과도하게 보수적일 가능성 높음.

---

## 4. 조치 제안

### 우선순위 P0 — visualizer 프롬프트 재검토

- **문제**: GROUP BY·범주형 데이터에도 NONE 반환, 사용자 명시 차트 요청 무시
- **조치 후보**:
  1. `visualizer_judgment` 프롬프트에 "사용자가 명시적으로 차트 타입 요청한 경우 규칙보다 우선" 명시
  2. continue_orchestrator 의 handoff_note 로 내려온 chart_type 힌트를 visualizer 가 먼저 체크하는 분기 추가
  3. N6 규칙 완화: PIE 외에도 BAR/DONUT fallback 허용
- **영향 범위**: K-01, N-04, V-02, V-04, V-05 등 시각화 관련 시나리오 다수

### 우선순위 P1 — N-02 재안정화

- **문제 A — 테이블 선택 변동성**:
  - `search_table_meta` 결과 variance 가 큼 (COM001M 누락 사례 발생)
  - 후보: embedding 모델 결정성 확보 (temperature=0, 고정 seed) 또는 rerank top_k 확대
- **문제 B — FORMULA 과도 strict**:
  - `measure:지점 수` 같은 trivial count 를 UNRESOLVED 로 고착시키는 context_interpreter 프롬프트 보완
  - "단일 테이블 COUNT(*) 는 산출식 명시 불필요" 규칙 추가
- **문제 C — force_generate 후 실패 처리**:
  - UNRESOLVED KI 상태에서 force_generate 진입 시 sql_generator 가 실패 루프에 갇힘
  - recovery_agent 의 give_up 조건 강화: 동일 failure_type 반복 N회 시 즉시 conclude_failure

### 우선순위 P2 — 관찰성 보강

- `readiness_gate` 에서 UNRESOLVED KI 의 `key` 와 누락 이유를 decision.detail 에 추가
- `context_interpreter` 가 테이블 거부한 이유를 trace 에 명시 (reject_reason)
- 프롬프트 실행마다의 embedding/rerank 결과 diff 저장 (재현성 조사용)

---

## 다음 스텝

1. extended 42건 실행 완료 대기 (진행 중: N-03/N-04 까지 완료, 약 30-40분 추가 소요)
2. 완료 후 visualizer NONE FAIL 전체 카운트 집계 → P0 이슈 파급 범위 확정
3. P0 프롬프트 수정 안 초안 작성 → 사용자 검토 후 구현
