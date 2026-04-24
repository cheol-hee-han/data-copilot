# E2E 테스트 이슈 CASE 정리

> **용도**: E2E 테스트로 발견된 이슈를 케이스 단위로 추적. 상태(대기중/완료/제외), 연관 시나리오, 상세 분석 흐름 포함.
> **정책**: 새 이슈는 `대기중` 으로 append. 정정/철회된 항목은 `제외` 로 두고 하단 "정정 이력" 에 사유 기록.
> **최초 작성**: 2026-04-21 (prescan 15건 + extended N-01~N-10 기반)
> **갱신**: 2026-04-21 — 실제 trace/state/log 원문 재확인 후 CASE-01·02·03·04 진단 정정, CASE-10 신규 추가

## 목차

- [요약표](#요약표) — 현재 CASE 상태 한눈에 보기
- [정정 이력](#정정-이력) — 초기 제안에서 변경된 항목
- [CASE-01: visualizer 사용자 명시 chart_type 무시](#case-01-visualizer-사용자-명시-chart_type-무시) — 대기중 / K-01
- [CASE-02: visualizer GROUP BY 분포 데이터에도 viz=NONE](#case-02-visualizer-group-by-분포-데이터에도-viznone) — 대기중 / N-04
- [CASE-03: search_table_meta 마스터 테이블 누락 (유형 힌트 부재)](#case-03-search_table_meta-마스터-테이블-누락-유형-힌트-부재) — 대기중 / N-02
- [CASE-04: context_interpreter 컬럼 hallucination](#case-04-context_interpreter-컬럼-hallucination) — 대기중 / N-02
- [CASE-05: 재탐색 동어반복 (hallucinated keyword 루프)](#case-05-재탐색-동어반복-hallucinated-keyword-루프) — 대기중 / N-02
- [CASE-06: N-08 intent 오분류 여부 재검토 필요](#case-06-n-08-intent-오분류-여부-재검토-필요) — 대기중 / N-08
- [CASE-07: N-10 지역 필터 rows=0](#case-07-n-10-지역-필터-rows0) — 대기중 / N-10
- [CASE-08: 관찰성 — readiness_gate UNRESOLVED KI 상세 노출](#case-08-관찰성--readiness_gate-unresolved-ki-상세-노출) — 대기중 / 관찰성
- [CASE-09: 관찰성 — context_interpreter reject_reason trace 기록](#case-09-관찰성--context_interpreter-reject_reason-trace-기록) — 대기중 / 관찰성
- [CASE-10: 시드 데이터 품질 (스키마-use_cases 불일치·중복 컬럼·분포 결함)](#case-10-시드-데이터-품질-스키마-use_cases-불일치중복-컬럼분포-결함) — 대기중 / N-02·N-04·N-10
- [CASE-X1: recovery_agent give_up (제외)](#case-x1-recovery_agent-give_up-제외) — 제외 / N-02
- [CASE-X2: embedding/rerank diff 저장 (제외)](#case-x2-embeddingrerank-diff-저장-제외) — 제외 / 관찰성

---

## 요약표

| ID | 제목 | 상태 | 우선순위 | 연관 시나리오 | 성격 |
|---|---|---|---|---|---|
| [CASE-01](#case-01-visualizer-사용자-명시-chart_type-무시) | visualizer 사용자 명시 chart_type 무시 | 대기중 | P0 | K-01 | 프롬프트 |
| [CASE-02](#case-02-visualizer-group-by-분포-데이터에도-viznone) | GROUP BY 결과에도 viz=NONE | 대기중 | P0 | N-04 | 프롬프트 |
| [CASE-03](#case-03-search_table_meta-마스터-테이블-누락-유형-힌트-부재) | search_table_meta 마스터 테이블 누락 | 대기중 | P1 | N-02 | 정규화/검색 |
| [CASE-04](#case-04-context_interpreter-컬럼-hallucination) | context_interpreter 컬럼 hallucination | 대기중 | P1 | N-02 | 프롬프트 |
| [CASE-05](#case-05-재탐색-동어반복-hallucinated-keyword-루프) | 재탐색 동어반복 루프 | 대기중 | P1 | N-02 | 재탐색 전략 |
| [CASE-06](#case-06-n-08-intent-오분류-여부-재검토-필요) | N-08 intent 오분류 여부 재검토 | 대기중 | - | N-08 | HYP 검토 |
| [CASE-07](#case-07-n-10-지역-필터-rows0) | N-10 지역 필터 rows=0 | 대기중 | - | N-10 | SQL/데이터 |
| [CASE-08](#case-08-관찰성--readiness_gate-unresolved-ki-상세-노출) | readiness_gate UNRESOLVED KI 상세 | 대기중 | P2 | 전반 | 관찰성 |
| [CASE-09](#case-09-관찰성--context_interpreter-reject_reason-trace-기록) | context_interpreter reject_reason trace | 대기중 | P2 | 전반 | 관찰성 |
| [CASE-10](#case-10-시드-데이터-품질-스키마-use_cases-불일치중복-컬럼분포-결함) | 시드 데이터 품질 결함 | 대기중 | P1 | N-02, N-04, N-10 | 데이터 |
| [CASE-X1](#case-x1-recovery_agent-give_up-제외) | recovery_agent give_up | **제외** | - | N-02 | 정책 모순 |
| [CASE-X2](#case-x2-embeddingrerank-diff-저장-제외) | embedding/rerank diff 저장 | **제외** | - | 관찰성 | 비용 과다 |

**상태 범례**: `대기중` = 조사·합의 완료, 구현 대기 / `완료` = 구현·회귀 확인 완료 / `제외` = 제안했으나 철회(사유는 상세 및 정정 이력 참조)

---

## 정정 이력

초기 [investigation_20260420.md](investigation_20260420.md) 에서 제안된 항목 및 [cases.md 초판(2026-04-21 오전)](cases.md) 에서 재분석 결과 수정·철회된 것들을 기록.

1. **P1-B (recovery_agent give_up 규칙 추가) → 철회** ([CASE-X1](#case-x1-recovery_agent-give_up-제외))
   - 초기: "동일 failure_type 3회 이상 반복 시 즉시 conclude_failure"
   - 정정: 프로젝트 정책상 give_up 미사용, `max_replan` 한도만 brake. N-02 의 `replan_count=10 → error_end` 는 **의도된 종료 동작**이며 recovery 결함이 아님.

2. **P1-A (context_interpreter에 "trivial COUNT(*) 예외" 추가) → 방향 수정**
   - 초기: "단일 마스터 테이블의 COUNT(*) 는 산출식 확인 없이 CONFIRMED"
   - 정정: 이는 증상 처치. 근본 원인은 **meta_search 에 테이블 유형 힌트가 없어 검색 레이어에서 마스터 테이블이 누락**된 것. [CASE-03](#case-03-search_table_meta-마스터-테이블-누락-유형-힌트-부재) 로 재프레이밍.

3. **P2-3 (embedding/rerank diff 저장) → 철회** ([CASE-X2](#case-x2-embeddingrerank-diff-저장-제외))
   - 초기: "run 간 검색 결과 diff 를 저장해 재현성 디버깅"
   - 정정: 구현 비용 큰데 비해 [CASE-09](#case-09-관찰성--context_interpreter-reject_reason-trace-기록) (reject_reason trace) 만으로도 대부분의 디버깅 가능.

4. **P0 K-01 조치 방향 (단순 override → 3단계 분기)**
   - 초기: "사용자/handoff_note chart_type 이 있으면 규칙 무시하고 우선 적용"
   - 정정: 사용자 요청이 데이터에 현저히 부적합한 경우(예: pie+20행)에는 오히려 대체 차트로 안내해야 UX 우수. A/B/C 3단계 분기로 세분화. [CASE-01](#case-01-visualizer-사용자-명시-chart_type-무시) 참조.

5. **CASE-01 재재분석 (trace 원문 확인 후, 2026-04-21 오후)**
   - 이전 주장: "visualizer 프롬프트 L5-12 '사용자 지시 우선' 과 L11 '충돌 시 무시' 가 **모순**"
   - 정정: 프롬프트 실제 구조는 `1. 데이터 특성 안전(최우선) > 2. 사용자 지시 > 3. 기본 규칙` 로 **모순 아님**. 설계상 안전 규칙이 우선. 진짜 이슈는:
     - **턴 1 (handoff_note 없음)도 viz=NONE** — N6 경계(행 `>20` vs 20) 엄격 적용 오류
     - **턴 2 에서 safety rule 발동 시 대체 차트 없이 NONE 반환** — 응답 과소
   - 조치 방향도 "override 프롬프트 추가" 가 아니라 "**N6 경계 엄밀화 + safety rule 발동 시 대체 차트 제안**" 으로 수정

6. **CASE-02 재재분석 — 시스템 결함에서 데이터 결함으로 재분류**
   - 이전 주장: "visualizer 가 GROUP BY 분포 데이터에도 NONE 반환하는 프롬프트 과도 보수성"
   - 정정: N-04 의 실제 data_summary 를 확인한 결과 **고객등급코드 182개 + 대부분 "미등록" 명칭** — 차트로 표현할 가치가 없는 데이터. LLM 의 N4+N6 판정은 **의미적으로 정당**. 시스템 결함이 아니라 **시드 데이터 품질 문제**([CASE-10](#case-10-시드-데이터-품질-스키마-use_cases-불일치중복-컬럼분포-결함)).
   - CASE-02 는 남기되 상세 섹션의 진단·조치 방향을 재작성

7. **CASE-03 심화 — 테이블 유형 힌트 부재 + 시드 데이터 품질 복합**
   - 이전 주장: meta_search 에 유형 힌트 없음 → 통계 테이블로 매칭 편향
   - 추가: 검색 결과가 `TB_ADW_FIN1319S`, `FXB534M` 등 4건 반환. 그러나 use_cases SQL 이 참조하는 COM*M 테이블들의 **PK 외 컬럼이 모두 동일**([CASE-10 증거 1](#증거-1--comm-테이블-컬럼-세트가-서로-동일)). 즉 LLM 은 검색 누락 + 메타 구분불가 **복합 원인**으로 혼란.
   - 조치는 유지하되 CASE-10 선행 수정 필요

8. **CASE-04 재재분석 — hallucination 이 아닌 "시드된 SQL 의 허구 컬럼 충실 재현"**
   - 이전 주장: "LLM 이 `BR_CD` 를 자체 생성 (hallucination)"
   - 정정: N-02 trace 의 use_cases SQL 5건 모두 **`WHERE BR_DCD = '02'` 로 필터** — 즉 LLM 은 시드된 SQL 이력에 있는 `BR_DCD` 컬럼을 "실존" 으로 신뢰. 그런데 **tables_meta 에는 BR_DCD 컬럼이 없음**. LLM 은 이 불일치 상황에서 "BR_CD" 같은 변형명으로 재탐색 시도. → Pure hallucination 이라기보다 **시드 불일치가 유발한 탐색 오류**. [CASE-10 증거 2](#증거-2--use_cases-sql-과-tables_meta-불일치) 참조.

---

## CASE-01: visualizer 사용자 명시 chart_type 무시

- **상태**: 대기중
- **우선순위**: P0
- **연관 시나리오**: K-01 (CONTINUE REDISPLAY — BAR→PIE)
- **영향 파일**: [resources/prompts/present/visualizer_judgment_system.txt](resources/prompts/present/visualizer_judgml_system.txt)

### 테스트 개요

K-01 은 멀티턴 REDISPLAY 시나리오이다:
- **턴 1**: "지점별 여신 잔액 보여줘" → SQL 실행 → rows=20 → bar_chart 기대
- **턴 2**: "같은 데이터 원형 차트로 보여줘" → REDISPLAY 경로 (SQL 재실행 없이 기존 결과에 pie_chart 시각화 재생성)

HYP:
```json
{
  "route_in": ["redisplay"],
  "path_excludes": ["sql_executor"],
  "viz_in": ["pie_chart", "donut_chart"]
}
```

### 실제 결과 (ACTUAL)

- `route=REDISPLAY` ✅, `sql_executor` 미진입 ✅ (재실행 회피 성공)
- 그러나 **턴1·2 모두 `viz=NONE`** — 턴2 의 "원형 차트" 요청 무시됨 → FAIL Minor

### 분석 흐름 (trace 원문 재확인)

트레이스 파일: [tests/reports/e2e_2026Q2/traces/K-01.trace.json](tests/reports/e2e_2026Q2/traces/K-01.trace.json)

1. **턴 1: handoff_note 없는 상태 — 그런데도 viz=NONE**
   - 쿼리: "지점별 여신 잔액 보여줘"
   - SQL 실행 → rows=20
   - visualizer_judgment 응답도 `chart_type=none`, reason 에 `(N6)` 단일 근거
   - 즉 **사용자 명시 요청과 무관하게** 행 20건을 N6("행>20") 경계에서 차트 부적합으로 판정
   - 프롬프트의 N6 문구는 "행이 20개를 **초과**" 인데 LLM 이 경계를 `>=20` 으로 해석

2. **턴 2: continue_orchestrator 가 handoff_note 로 pie_chart 힌트 전달**
   ```
   ### 시각화/포맷 지시
   - 기존 지점별 여신잔액 조회 결과 테이블을 원형 차트(pie_chart)로 전환
   - X축: 부점명, Y축: 여신잔액합계 값 활용
   - SQL 및 결과 데이터 재사용, 재조회 불필요
   ```

3. **턴 2 visualizer_judgment 실제 응답**
   ```json
   {
     "chart_type": "none",
     "reason": "행이 20개로 차트에 담기에는 과다하며, 부점명과 여신잔액합계의 비교 분석 목적이지만
                항목 수가 많아 원형 차트로는 가독성이 떨어집니다 (N6)"
   }
   ```

### 근본 원인 (정정 후)

**프롬프트 "모순" 주장은 철회** ([정정 이력 #5](#정정-이력) 참조). [visualizer_judgment_system.txt:10-12](resources/prompts/present/visualizer_judgment_system.txt#L10-L12) 는 실제로 명시적 우선순위를 선언한다:

```
1. 데이터 특성 안전 규칙(최우선) — K1~K3, N1~N8
2. 사용자 연속 처리 지시의 `### 시각화/포맷 지시` 섹션
3. 그 외 기본 규칙
```

즉 "안전 규칙이 발동되면 사용자 지시 무시" 는 설계상 의도된 동작이며 모순이 아니다. 실제 이슈는 둘로 분해된다:

#### A. N6 경계 오적용 (턴 1·2 공통)

- [visualizer_judgment_system.txt:130](resources/prompts/present/visualizer_judgment_system.txt#L130): `N6. 행이 20개를 초과하여 차트가 오히려 가독성을 해침`
- 문구는 `>20` 이나 LLM 은 20행에서 N6 적용 → 경계값 판단 오류
- 은행 도메인에서 20개 부점 bar_chart 는 관행적으로 적합

#### B. safety rule 발동 시 대체 차트 미제안

- 안전 규칙이 발동되어 사용자 요청 `pie_chart` 를 기각했지만, N6 의 취지는 "pie 부적합" 일 뿐 "모든 차트 부적합" 이 아님
- horizontal_bar 같이 20 카테고리에도 안전한 차트가 엄연히 존재하는데 NONE 으로 내려 사용자 경험 악화

### 조치 방향 (확정 전, 정정 후)

#### 조치 1 — N6 경계 엄밀화

[visualizer_judgment_system.txt:130](resources/prompts/present/visualizer_judgment_system.txt#L130) 을 구체 수치로 재진술:

```text
N6. 데이터 행이 50개를 초과하여 차트 범례·축 레이블이 가독성을 해치는 경우
    (막대/수평막대: 50행 초과, 원형/도넛: 10행 초과를 경계로 삼는다)
```

→ 숫자 임계를 **차트 타입별로** 명시해 LLM 의 경계값 오해 방지.

#### 조치 2 — safety rule 발동 시 대체 차트 제안 의무화

[visualizer_judgment_system.txt:5-18](resources/prompts/present/visualizer_judgment_system.txt#L5-L18) "사용자 연속 처리 지시" 항목에 보강:

```text
- 안전 규칙이 사용자 지시 차트 타입을 기각하는 경우에도 `chart_type="none"` 은 최후 수단.
  데이터 구조상 가능한 대체 차트(horizontal_bar 등)가 있으면 해당 타입을 선택하고
  reason 에 "요청 pie_chart 은 N6 (>10행) 으로 부적합, horizontal_bar 로 대체" 같은
  전환 사유를 명시.
```

#### 조치 3 (검증만)

턴 1의 rows=20 bar_chart 는 원래 기대값. 즉 조치 1 만으로도 턴 1 은 정상화될 가능성이 높음. 조치 2 는 턴 2 의 pie_chart→horizontal_bar 폴백에 해당.

---

## CASE-02: visualizer GROUP BY 분포 데이터에도 viz=NONE

- **상태**: 대기중 (재분류 완료 — **시스템 결함 아님, 데이터 결함**)
- **우선순위**: 보류 — [CASE-10](#case-10-시드-데이터-품질-스키마-use_cases-불일치중복-컬럼분포-결함) 선행 해결 후 재검증
- **연관 시나리오**: N-04 (고객등급별 분포)
- **영향 파일**: [devtools/scripts/seed_postgres.py](devtools/scripts/seed_postgres.py) 코드 마스터 시드

### 테스트 개요

- **Query**: "고객등급별 고객 수 분포 보여줘"
- **HYP**: `viz_in = [bar_chart, horizontal_bar, pie_chart, donut_chart, table_only]` (어떤 차트든 하나)
- **ACTUAL**: `rows=182`, `viz=NONE` → FAIL Minor

### 분석 흐름 (trace 원문 재확인)

트레이스 파일: [tests/reports/e2e_2026Q2/traces/N-04.trace.json](tests/reports/e2e_2026Q2/traces/N-04.trace.json)

1. SQL 정상 실행 — `GROUP BY CUS_GRD_CD` 형태로 집계.
2. `visualizer` 노드 입력 data_summary 확인 결과:

   ```text
   | 고객등급코드    | 고객등급명 | 고객수 | 고객수비율 |
   | CUSGR00001 | 미등록   | 5  | 1.01 |
   | CUSGR00002 | 미등록   | 2  | 0.40 |
   | CUSGR00003 | 미등록   | 1  | 0.20 |
   ... (총 182행, 등급명 대부분 "미등록")
   ```

3. visualizer_judgment 응답 reason 에 `(N6, N4)` 명시
   - N6: 행 20 초과로 가독성 저하
   - N4: 각 행이 개별 식별자 성격 → 카테고리 범주로 묶이지 않음

### 근본 원인 (정정 후)

**이전 주장**: "GROUP BY 분포 데이터에도 NONE 반환 = 프롬프트 과보수"

**trace 재확인 결과**: LLM 판정은 **의미적으로 타당**. 이유:

- **현실 은행의 고객등급코드는 5-10개**. 182개는 비정상.
- **등급명이 대부분 "미등록"** — 차트의 X축·범례가 "미등록" 반복으로 유의미한 분포 시각화 불가
- LLM 입장에서는 "182개 카테고리 × 동일 라벨" = 차트로 표현해도 사용자가 읽을 수 없는 구조
- N4(각 행이 개별 식별자) 적용 판정은 **정당**

즉 이 FAIL 은 **visualizer 프롬프트 결함이 아니라 코드 마스터 시드 데이터의 결함** — 사용자 의도인 "고객등급별 분포" 가 의미 있으려면 등급명이 실제 매핑돼 있어야 한다.

### 조치 방향 — CASE-02 (확정 전)

#### 조치 02-1 — CASE-10 선행 해결 필수

[CASE-10 증거 3](#증거-3--n-04-고객등급-분포-비정상) 항목의 시드 정비:

1. `devtools/scripts/seed_postgres.py` 의 코드 마스터(`TB_ADW_CMD*`) 에 실제 고객등급 5-10개 정의 ("VIP", "Gold", "일반", "미등록" 등)
2. 고객 데이터(`TB_ADW_CSC101M.CUS_GRD_CD`) 는 위 등급 중 하나를 참조하도록 시드
3. 재실행 시 rows 가 5-10건 수준으로 축소되고 등급명 매핑이 자연스러워짐 → LLM 이 bar_chart/horizontal_bar 선택할 것으로 예상

#### 조치 02-2 — 시드 정비 후 NONE 재현 시 CASE-01 조치 2 재사용

CASE-02 자체에 대한 프롬프트 수정은 **보류**. 시드 정비 후 FAIL 재현되면 그때 프롬프트 보강 여부 결정.

### 이 CASE 의 의미 — CASE-02

K-01 (CASE-01) 과 달리 N-04 의 viz=NONE 은 **LLM 의 판단이 정당한 케이스**. 이전에는 "visualizer 전반의 과보수 패턴" 으로 묶어 P0 대응 하려 했으나, trace 원문 확인으로 시스템·데이터 문제를 분리. 과잉 수정을 방지.

---

## CASE-03: search_table_meta 마스터 테이블 누락 (유형 힌트 부재)

- **상태**: 대기중
- **우선순위**: P1
- **연관 시나리오**: N-02 (지점 수 조회)
- **영향 파일**: [resources/prompts/interpret/query_normalizer_phase1_system.txt](resources/prompts/interpret/query_normalizer_phase1_system.txt), [src/agents/nodes/reason/reasoning_preparer.py](src/agents/nodes/reason/reasoning_preparer.py)

### 테스트 개요

- **Query**: "지점이 몇 개야?"
- **HYP**: `path_contains_any=[sql_generator, sql_executor]`, `sql_contains_any=[COUNT, count]`, `rows_min=1`, `replan_max=1`
- **ACTUAL**: `replan_count=10`, `sql_executor 미진입`, `error=True` → FAIL Critical
- **이전 run (22:29)**: PASS, replan=1, SQL=`COUNT(*) FROM TB_ADW_COM001M WHERE BR_DCD='02'`

### 분석 흐름 — CASE-03 (trace 원문 재확인)

트레이스 파일: [tests/reports/e2e_2026Q2/traces/N-02.trace.json](tests/reports/e2e_2026Q2/traces/N-02.trace.json)

1. **query_normalizer 실제 출력 (trace 확인)**
   - 원 질의 "지점이 몇 개야?" → 8-slot 정규화
   - `search_keywords.meta_search = ["지점", "지점 수", "개수", "건수"]` (trace phase1/2 응답에서 확인)
   - `search_keywords.vector_search = "전체 지점 수 조회"`

2. **reasoning_preparer.\_extract_meta_search_query**
   - [reasoning_preparer.py:369-370](src/agents/nodes/reason/reasoning_preparer.py#L369-L370): `" ".join(meta_kws)` → `"지점 지점 수 개수 건수"`
   - 이 문자열을 `search_table_meta` 쿼리로 전달

3. **search_table_meta 결과 (trace 확인)**
   - 1차 쿼리: `"지점 지점 수 개수 건수", page=1` → **4건**
   - 반환 테이블: `TB_ADW_FIN1319S`(지점성과통계), `TB_ADW_FXB534M`(FX외화보유) 등 — **모두 통계·실적 테이블**
   - 정답인 **`TB_ADW_COM001M` (지점 마스터) 는 상위 4건에 포함되지 않음**

4. **top_k 변동성 관찰**
   - N-02 첫 검색: 4건 반환
   - N-02 후속 검색(재탐색) 및 N-10 유사 질의: 10건 반환 (이때는 COM001M 포함)
   - 검색 레이어의 top_k 설정이 호출 시점에 따라 4/10 로 달라지는 것으로 추정 → **recall 불안정**

5. **왜 마스터 테이블이 누락됐는가**
   - 쿼리에 `"개수"`, `"건수"` 같은 **집계·통계어** 가 포함되어 통계 테이블과 임베딩 유사도가 역전
   - 8-slot 정규화는 업무 의미(entity/measure)는 잘 추출하지만 **"마스터 / 팩트 / 차원 / 통계"** 같은 **테이블 물리 유형** 힌트는 생성하지 않음
   - 사용자 질의에 "마스터", "기준정보" 같은 단어가 없으니 정규화에도 안 들어감

6. **CASE-10 (시드 데이터) 와의 복합 원인**
   - 설령 COM001M 이 검색에 포함돼도 [CASE-10 증거 1](#증거-1--comm-테이블-컬럼-세트가-서로-동일) 에 의해 **COM\*M 계열 5개 테이블이 PK 외 컬럼이 완전 동일**
   - LLM 이 "지점 마스터" 로 COM001M 을 선택하더라도 COM003M/COM006M/COM012M/COM021M 과 의미적 구분 근거 부족 → 오판 가능
   - 즉 검색 누락 해결만으로는 안정화되지 않음. CASE-10 선행 필요

### 조치 방향 — CASE-03 (확정 전)

#### A안 (정규화 측) — 우선 권장

- query_normalizer 프롬프트에 "측정이 COUNT 이고 entity 가 단일 개체(지점/고객/상품)일 때 meta_search 에 `'마스터'` 또는 `'기준정보'` 키워드 추가" 규칙 추가
- 또는 SearchKeywords 에 별도 슬롯 `table_type_hint: ["MASTER"|"FACT"|"STAT"]` 신설

#### B안 (탐색 전략 측)

- `reasoning_preparer` 가 search_table_meta 를 **병렬 2-쿼리** 로 발행: 원 meta_search + `"{entity} 마스터"` 합성 쿼리
- top_k 를 현행 4 → 10 확대 (recall 개선, 비용 미미). N-10 에서 10건 반환된 상태와 통일

#### C안 (임베딩 측, 장기)

- 테이블 설명 문서에 **"마스터"/"기준정보"/"통계"** 등 유형 라벨을 메타로 주입하여 재임베딩
- 비용 크므로 단기 범위 밖

**우선순위**: A → B → C. A + B 조합이 가장 효율적. 단 **[CASE-10](#case-10-시드-데이터-품질-스키마-use_cases-불일치중복-컬럼분포-결함) 선행 해결** 없이는 이들 조치의 효과가 반감된다.

---

## CASE-04: context_interpreter 컬럼 hallucination

- **상태**: 대기중 (재분류 완료 — **순수 hallucination 아니라 "시드 불일치가 유발한 탐색 오류"**)
- **우선순위**: P1 (단 [CASE-10](#case-10-시드-데이터-품질-스키마-use_cases-불일치중복-컬럼분포-결함) 선행 해결 시 완화 가능)
- **연관 시나리오**: N-02
- **영향 파일**: [resources/prompts/reason/context_interpreter_system.txt](resources/prompts/reason/context_interpreter_system.txt)

### 테스트 개요

N-02 분석 중 [CASE-03](#case-03-search_table_meta-마스터-테이블-누락-유형-힌트-부재) 와 별개로 발견된 2차 원인.

### 분석 흐름 — CASE-04 (trace 원문 재확인)

1. CASE-03 으로 인해 마스터 테이블이 초기 검색에서 누락 → `TB_ADW_COM006M`, `COM003M`, `COM021M` 등이 후보로 선정
2. **use_cases SQL 이 `BR_DCD='02'` 필터를 담고 있음** (trace 확인, 5건 모두 동일 패턴)

   ```sql
   SELECT * FROM ADWOWN.TB_ADW_COMxxxM WHERE BR_DCD = '0x' LIMIT 100
   ```

3. **그러나 tables_meta 에는 BR_DCD 컬럼이 존재하지 않음** — 시드된 use_cases 가 시드된 스키마와 **불일치** ([CASE-10 증거 2](#증거-2--use_cases-sql-과-tables_meta-불일치))
4. LLM 의 대응 시퀀스:
   - "use_cases 가 BR_DCD 로 필터하니 지점 구분 컬럼이 있어야 한다" 판정
   - tables_meta 에 BR_DCD 가 없으므로 **변형명 "BR_CD"** 로 재탐색 시도:
     - `search_table_meta` 쿼리 추이 (trace 확인):
       - #29: `"지점 고유 코드 BR_CD 컬럼", page=1` → 10건
       - #31: `"지점 고유 코드 컬럼 BR_CD", page=1` → 10건
       - #32: `"지점 고유 코드", page=1` → 10건
       - #34: `"지점 고유 코드 BR_CD 컬럼", page=2` → 0건
   - 최종 사용자 응답에 `"지점 고유 코드(BR_CD) 컬럼 미확인"` 으로 굳어짐

### 근본 원인 — CASE-04 (정정 후)

**이전 주장**: "LLM 이 BR_CD 를 허구로 생성 = 순수 hallucination"

**재확인 결과**: 3단 복합 원인

- **A. 시드 데이터 불일치 (진정한 뿌리)** — use_cases SQL 이 참조하는 `BR_DCD` 가 tables_meta 에 없음. LLM 은 "use_cases 에 있는 컬럼이 실존" 이라는 합리적 전제에서 출발 → [CASE-10](#case-10-시드-데이터-품질-스키마-use_cases-불일치중복-컬럼분포-결함)
- **B. LLM 의 변형명 추측** — 실재하지 않는 컬럼을 재탐색하는 과정에서 "BR_DCD → BR_CD" 변형. 기술적으로는 hallucination 이지만 **시드 불일치가 유발한 파생 현상**
- **C. 프롬프트 가드 부재** — context_interpreter 에 "tool_results 에 실재하는 컬럼만 근거로 사용" 규칙이 없음. 동일 시드 오류가 있어도 가드가 있으면 "use_cases 의 BR_DCD 는 tables_meta 에 부재 → UNRESOLVED" 로 조기 차단 가능

### 조치 방향 — CASE-04 (확정 전)

#### 조치 04-1 — CASE-10 선행 (뿌리 차단)

시드 정비로 use_cases SQL 컬럼 ↔ tables_meta 컬럼 정합성 확보. 이것만으로 N-02 상당 부분 안정화 예상.

#### 조치 04-2 — context_interpreter 프롬프트 가드 (회귀 방지)

[context_interpreter_system.txt](resources/prompts/reason/context_interpreter_system.txt) 에 규칙 추가:

1. `"knowledge_updates / explored_tables / explored_use_cases 의 reason 에 컬럼명을 언급할 때는 반드시 tool_results 에 포함된 실존 컬럼만 사용. 추론/변형 컬럼명 생성 금지."`
2. `"use_cases SQL 에 등장한 컬럼이 tables_meta 에 없으면 UNRESOLVED 사유로 '시드/메타 불일치' 로 명시. 변형명 재탐색 시도 금지."`
3. `"실존 컬럼으로 요건 충족 불가 시 'measure 산출 불가'로 UNRESOLVED 처리. 가공 컬럼명을 '필수' 로 제시하지 말 것."`

조치 2 는 **방어적 프롬프트** — 시드가 완벽해지더라도 미래의 메타 업데이트 지연 등으로 유사 상황 발생 가능.

---

## CASE-05: 재탐색 동어반복 (hallucinated keyword 루프)

- **상태**: 대기중
- **우선순위**: P1
- **연관 시나리오**: N-02
- **영향 파일**: [resources/prompts/reason/context_interpreter_system.txt](resources/prompts/reason/context_interpreter_system.txt), [resources/prompts/reason/recovery_agent_system.txt](resources/prompts/reason/recovery_agent_system.txt)

### 테스트 개요

CASE-04 와 체인. hallucinated 컬럼으로 **수렴하지 않는 재탐색 루프** 가 발생.

### 분석 흐름

N-02 트레이스 `readiness_gate trail`:
```
#1-4: replan (knowledge=0/1, tables=7)
#5-6: generate_sql (knowledge=0/1, tables=10)   ← force_generate 진입
#7-10: generate_sql (knowledge=0/1, tables=14)  ← KI 여전히 UNRESOLVED
```

1. 매 replan 시 context_interpreter 가 거의 **같은 reason** ("measure:지점 수 Unresolved") 반복
2. 재탐색 쿼리도 `"지점 고유 코드 BR_CD 컬럼"` 같은 **hallucinated 키워드 동어반복** (CASE-04 참조)
3. force_generate(5회 이후) 로 전환돼도 UNRESOLVED 가 풀리지 않아 sql_generator 도 실패 루프
4. replan_count=10 한도 도달 → `error_end` (이는 **의도된 brake**, recovery 결함 아님)

### 근본 원인

- **이전 탐색에서 시도한 쿼리가 다음 탐색 LLM 에 주입되지 않음** → 동어반복 발생
- context_interpreter 프롬프트에 "이전 탐색 쿼리는 X, Y, Z. 동일/유사 쿼리 반복 금지. 다른 각도에서 접근" 가이드 없음
- recovery_agent 프롬프트에도 "이전 실패 reason 과 동일한 원인이면 다른 각도 제시" 규칙 없음

### 조치 방향 (확정 전)

1. **context_interpreter 프롬프트**: 이전 N회의 `search_table_meta` / `search_use_cases` 쿼리 리스트를 입력 변수로 추가 (`{previous_search_queries}`). "동일/유사 키워드 재사용 금지. 다른 엔티티 각도·유형(마스터/기준정보) 힌트·다른 컬럼 후보 제시" 가이드 추가.
2. **recovery_agent 프롬프트**: 이전 실패의 failure_reason 을 입력 받아 "직전 N회와 같은 근본 원인이면 검색 축을 반드시 바꿀 것" 지침 추가.

※ 이는 [CASE-X1](#case-x1-recovery_agent-give_up-제외) 의 give_up 과 다름. give_up 대신 **발산 방향 전환** 유도.

---

## CASE-06: N-08 intent 오분류 여부 재검토 필요

- **상태**: 대기중
- **우선순위**: 보류 (HYP 수정만으로 해결 가능성 높음)
- **연관 시나리오**: N-08 (VIP 예금·대출 비교)

### 테스트 개요

- **Query**: "VIP 고객이 보유한 예금 총 잔액과 대출 총 잔액 비교해줘"
- **HYP**: `intent=data_extraction`, `path_contains_any=[sql_generator, sql_executor]`, `sql_contains_any=[JOIN, UNION]`, `rows_min=1`
- **ACTUAL**: `intent=data_analysis`, SQL 정상 (UNION ALL), rows=2, analyzer 진입 → FAIL Major (intent DIFF)

### 분석 흐름

1. 사용자 질의에 `"비교해줘"` 가 명시 → intent_classifier 가 `DATA_ANALYSIS` 로 분류
2. analyzer 가 비교 분석 insights 생성 시도했으나 데이터 부재(잔액=0)로 분석 불가 메시지 반환
3. SQL·rows·경로 모두 HYP 와 정합. **intent 키만 실패**

### 쟁점

"비교해줘" 는 **분석 요청** 인가 **추출 요청** 인가?
- 프로젝트 정의 관점: "비교" 는 분석 의도. LLM 분류가 **사실상 정확**
- HYP 오류 가능성 높음 — `"intent_in": ["data_extraction", "data_analysis"]` 로 완화해야 함

### 조치 방향

- 시스템 결함이 아니라 **HYP 정정 사안** 으로 판정 가능성 높음
- 다만 "분석으로 분류됐을 때 analyzer 가 데이터 0 상황에 충분히 친절한 응답을 했는가?" 관점은 별도 검토 여지 (현재는 JSON summary 반환)
- 사용자 확인 후 HYP 수정만으로 종결하거나, analyzer 폴백 응답 개선을 별도 case 로 승격

---

## CASE-07: N-10 지역 필터 rows=0

- **상태**: 대기중
- **우선순위**: 보류 (원인 추가 조사 필요)
- **연관 시나리오**: N-10 (서울 지역 지점 3단 조인)

### 테스트 개요

- **Query**: "서울 지역 지점의 고객 수랑 수신 잔액 합계"
- **HYP**: `sql_contains_any=[JOIN, join]`, `rows_min=1`
- **ACTUAL**: SQL 정상 생성, 실행 성공, **rows=0** → FAIL Minor

### 분석 흐름

생성된 SQL (요약):
```sql
SELECT c.BR_NM, COUNT(DISTINCT cu.EDPS_CSN), SUM(d.BAL_AMT)
FROM ADWOWN.TB_ADW_COM001M c
LEFT JOIN ADWOWN.TB_ADW_CSC101M cu ON c.BLNG_BRCD = cu.BLNG_BRCD
LEFT JOIN ADWOWN.TB_ADW_DEP201P d ON cu.EDPS_CSN = d.EDPS_CSN
LEFT JOIN ADWOWN.TB_ADW_COM010M r ON c.RGN_CD = r.RGN_CD
WHERE (r.NM LIKE '%서울%' OR c.BR_NM LIKE '%서울%')
GROUP BY c.BR_NM
```

- SQL 자체는 구조적으로 올바름
- `c.RGN_CD` ↔ `r.RGN_CD` 조인 후 `r.NM LIKE '%서울%'` 필터
- rows=0 이 나온 가능성:
  - 테스트 데이터에 서울 지역 지점 데이터 자체가 없음 (**데이터 문제**)
  - `r.NM` 컬럼값이 "서울특별시" / "서울시" 등 서로 다른 표기로 돼 있어 `'%서울%'` 이 매칭은 되지만 후속 조인에서 빈 결과 (**데이터 표기 문제**)
  - `TB_ADW_COM010M.NM` 이 실제 지역명을 담고 있지 않음 (**스키마 이해 오류**)

### 쟁점

- 시스템 결함인지 테스트 데이터 문제인지 분리 필요
- 조사 방법: 테스트 DB 에서 `SELECT RGN_CD, NM FROM ADWOWN.TB_ADW_COM010M WHERE NM LIKE '%서울%'` 직접 실행해 데이터 존재 여부 확인

### 조치 방향 (조사 후 확정)

- 데이터 부재라면 → 테스트 데이터 보강 (seeding 스크립트에 서울 지점 포함) + HYP 유지
- 데이터는 있는데 SQL 필터 로직 오류라면 → sql_generator 프롬프트 보강 (여러 표기 대응)

---

## CASE-08: 관찰성 — readiness_gate UNRESOLVED KI 상세 노출

- **상태**: 대기중
- **우선순위**: P2
- **연관 시나리오**: 전반 (디버깅 생산성)
- **영향 파일**: 에이전트 노드 — readiness_gate 결정 기록부

### 문제

현재 trace 의 `readiness_gate.decision` 에 `knowledge_items: 0 confirmed / 1 unresolved` 같은 **카운트만 기록**. 어떤 KI 가 왜 UNRESOLVED 인지 알려면 LLM 응답 본문을 역으로 따라가야 함.

### 분석 흐름

N-02 분석 시 10회의 readiness_gate 결정을 일일이 LLM 응답 본문 읽어 `measure:지점 수 Unresolved` 라는 공통 원인을 파악. 한 번에 1~2시간 소요.

### 조치 방향 (확정 전)

readiness_gate 결정 기록 포맷 확장:
```json
{
  "chosen": "replan",
  "knowledge_items_summary": {
    "confirmed": 0,
    "unresolved": 1,
    "unresolved_detail": [
      {"id": "K1", "key": "measure:지점 수", "reason": "집계 산출식 미확인"}
    ]
  }
}
```

이 필드가 채워지면 trace 요약만으로 무엇이 왜 해결되지 않았는지 즉시 파악.

---

## CASE-09: 관찰성 — context_interpreter reject_reason trace 기록

- **상태**: 대기중
- **우선순위**: P2
- **연관 시나리오**: 전반
- **영향 파일**: context_retrievals trace 작성부

### 문제

현재 trace `context_retrievals[i].results_summary` 는 `['결과 4건 수집 (배치 해석 대기)']` 같은 **개수 요약만** 기록. context_interpreter 가 어떤 테이블을 REJECTED 로 판단했는지, 사유는 무엇인지 분리 기록 없음.

### 분석 흐름

N-02 분석 시 "정답 테이블이 아예 검색에 없었나, 검색엔 있었지만 LLM 이 reject 했나?" 를 구분하기 위해 LLM 응답 본문을 수십 번 파싱. 구분 가능해지면 CASE-03 (검색 누락) vs CASE-04 (LLM 오판) 를 즉시 구분.

### 조치 방향 (확정 전)

context_retrievals 또는 llm_calls 에 파생 필드 추가:
```json
{
  "tables_selected": ["TB_ADW_COM006M", "TB_ADW_COM003M"],
  "tables_rejected": [
    {"name": "TB_ADW_COM007M", "reason": "BR_DCD='01' (본점) 조건만 사용"},
    {"name": "TB_ADW_FIN1319S", "reason": "손익 항목별 수치, 지점 수 집계 아님"}
  ]
}
```

---

## CASE-10: 시드 데이터 품질 (스키마-use_cases 불일치·중복 컬럼·분포 결함)

- **상태**: 대기중
- **우선순위**: P1 (시스템 결함이 아닌 테스트 환경 데이터 문제이나, N-02·N-04·N-10 등 다수 실패의 실제 근인)
- **연관 시나리오**: N-02 (필수 근인), N-04 (근인), N-10 (추정 근인)
- **영향 파일**: [devtools/scripts/seed_postgres.py](devtools/scripts/seed_postgres.py) 및 관련 시드/메타 생성 스크립트

### 발견 경위

CASE-01·02·03·04 재검증을 위해 trace 의 `llm_calls` 원문을 읽는 중, LLM 이 처리한 `tool_results` 내용이 **비정상적 시드 데이터**임을 발견. 이 원인은 LLM 결함이 아니라 입력 데이터 품질 문제.

### 증거 1 — COM*M 테이블 컬럼 세트가 서로 동일

N-02 첫 context_interpreter 의 `tool_results` 에 제시된 5개 테이블 스키마:

| 테이블 | 설명 | PK 외 컬럼 |
|---|---|---|
| `TB_ADW_COM006M` | COM직원정보기본 | NM, DESC_CONT, USE_YN, SORT_ORD, RGST_DT, RGST_USR_ID, INS_DTM, UPD_DTM |
| `TB_ADW_COM003M` | COM공통코드마스터 | **동일** |
| `TB_ADW_COM021M` | COM공지사항 | **동일** |
| `TB_ADW_COM007M` | COM영업일달력 | **동일** |
| `TB_ADW_COM012M` | COM통화코드 | **동일** |

→ 설명상 전혀 다른 업무의 테이블들이 PK 제외 컬럼이 완전 동일. LLM 이 테이블 의미를 구분할 수 없음. 실제 은행 스키마는 각 테이블이 고유 컬럼 세트 보유.

### 증거 2 — use_cases SQL 과 tables_meta 불일치

N-02 에서 retriever 가 반환한 유사 SQL 5건 모두:
```
SELECT * FROM ADWOWN.TB_ADW_COMxxxM WHERE BR_DCD = '0x' LIMIT 100
```
이 SQL 에 `BR_DCD` 컬럼을 필터로 사용하지만, **증거 1 의 컬럼 목록에는 `BR_DCD` 가 없음**. 즉 시드된 use_cases SQL 이 실제 시드된 스키마와 다른 가상의 컬럼을 참조.

→ LLM 이 "use_cases 에 BR_DCD='02' 가 있으니 이걸 써야 한다" 판단. 이후 search_table_meta 를 `BR_CD` 로 재탐색(hallucination). [CASE-04](#case-04-context_interpreter-컬럼-hallucination) 의 직접 원인.

### 증거 3 — N-04 고객등급 분포 비정상

N-04 의 `data_summary` (visualizer 입력):
```
| 고객등급코드 | 고객등급명 | 고객수 | 고객수비율 |
| CUSGR00001 | 미등록 | 5 | 1.01 |
| CUSGR00002 | 미등록 | 2 | 0.40 |
| CUSGR00003 | 미등록 | 1 | 0.20 |
... (총 182행, 대부분 고객등급명이 "미등록")
```
- 182개 고객등급코드 — 현실 은행 등급은 통상 5~10개
- 대부분 `등급명="미등록"` — 테스트 코드 마스터 시드에 실제 등급명이 매핑 안 됨
- LLM 이 N4(각 행이 개별 식별자) + N6(>20) 근거로 NONE 판정 — **의미적으로 정당**

→ 시스템 결함이 아니라 **데이터가 차트로 표현할 가치가 없는 상태**. [CASE-02](#case-02-visualizer-group-by-분포-데이터에도-viznone) 를 시스템 결함에서 데이터 결함으로 재분류해야 함.

### 증거 4 (추정) — N-10 서울 지역 지점 부재

N-10 생성 SQL:
```sql
WHERE (r.NM LIKE '%서울%' OR c.BR_NM LIKE '%서울%')
```
SQL 구조 정상, rows=0. 원인 후보:
- `TB_ADW_COM010M.NM` (지역 마스터) 에 "서울" 포함 행 없음
- `TB_ADW_COM001M.BR_NM` (지점명) 에 "서울" 포함 부점명 없음
- 혹은 `RGN_CD` 매핑 불일치

→ 시드 스크립트에 **서울·부산 등 실제 지역 데이터가 부족**할 가능성. 실행 SQL 로 재확인 필요.

### 조치 방향 (확정 전)

**A. 시드 데이터 리셋 — 스키마 정합성 우선**
1. `devtools/scripts/seed_postgres.py` 전면 점검
2. COM*M 테이블별 **고유 컬럼 세트** 재설계 (영업일달력·통화코드·공지사항 등은 각기 다른 도메인 컬럼 보유)
3. use_cases SQL 이 참조하는 컬럼이 실제 tables_meta 에 존재하는지 **교차 검증 스크립트** 추가

**B. 도메인 사실성 확보**
1. 고객등급코드 `CUSGR00001~05` 정도로 제한, 등급명("VIP", "골드", "일반" 등) 실제 매핑
2. 지역 시드(서울·부산·대구 등) 및 BR_NM/RGN_CD 매핑 일관성 확보
3. 지점 마스터 (`TB_ADW_COM001M`) 에 `BR_DCD` 컬럼 실제 추가 (use_cases SQL 과 정합)

**C. LLM 판단 가드 (CASE-04 연계)**
시드가 당장 정비되지 않아도, context_interpreter 프롬프트에 **"use_cases SQL 의 컬럼 중 tables_meta 에 없는 것은 사용 금지"** 규칙 추가 ([CASE-04](#case-04-context_interpreter-컬럼-hallucination) 과 동일 조치).

### 이 CASE 의 의미

이 케이스 발견 전까지 N-02 의 실패 원인을 **LLM 의 hallucination** 또는 **정규화 유형 힌트 부재** 로 돌렸으나, 실제로는 입력 데이터 자체가 논리적 모순을 품고 있어 **어떤 LLM 도 깔끔한 SQL 을 못 만드는 상황**이었음. 시스템 프롬프트를 고쳐도 이 이슈가 선행 해결되지 않으면 N-02 가 안정화되지 않을 가능성이 높음.

---

## CASE-X1: recovery_agent give_up (제외)

- **상태**: 제외
- **사유**: 프로젝트 정책상 give_up 미사용. `max_replan` 한도만 brake.
- **초기 제안 배경**: N-02 가 replan=10 까지 간 뒤 error_end 로 종료되는 것을 "recovery 루프 결함" 으로 오인
- **재분석 결론**: `replan_count=10` 도달 → `error_end` 는 **의도된 terminal behavior**. recovery_agent 는 정상적으로 replan 을 10회 제안한 것이며 결함 아님.
- **남은 과제**: "왜 10회 replan 에도 수렴 못했나" → [CASE-03](#case-03-search_table_meta-마스터-테이블-누락-유형-힌트-부재), [CASE-04](#case-04-context_interpreter-컬럼-hallucination), [CASE-05](#case-05-재탐색-동어반복-hallucinated-keyword-루프) 로 귀속.
- **참조 메모리**: `feedback_test_user_id.md` 유사 정책 메모. give_up 관련 별도 메모는 현재 없음 — 필요 시 신규 메모 작성 검토.

---

## CASE-X2: embedding/rerank diff 저장 (제외)

- **상태**: 제외
- **사유**: 구현 비용 크고, [CASE-09](#case-09-관찰성--context_interpreter-reject_reason-trace-기록) 만으로도 대부분의 디버깅 가능.
- **초기 제안 배경**: N-02 가 이전 run 에서는 PASS, 현재 run 에서 FAIL 로 바뀐 원인을 재현하려면 run 간 검색 결과 diff 가 필요하다는 가설
- **재분석 결론**:
  - 임베딩 자체는 같은 입력에 결정적이므로 변동 주 원인은 **쿼리 생성 LLM(query_normalizer) 의 meta_search 출력 변동성**
  - 이는 diff 저장 없이 `llm_calls` 의 `response_text` 를 비교하면 확인 가능
  - diff 저장 인프라는 운영 복잡도만 추가
- **대안**: [CASE-09](#case-09-관찰성--context_interpreter-reject_reason-trace-기록) + `llm_calls.response_text` 의 query_normalizer 응답만으로 충분

---

## Append 규칙

- 새 이슈 발견 시 요약표 맨 아래에 `CASE-NN` 로 추가하고 `대기중` 표기
- 구현 완료 시 `완료` 로 바꾸고 상세 섹션 하단에 "구현 커밋 / 검증 결과" 블록 추가
- 제안 철회 시 `제외` 로 표기, "정정 이력" 섹션에 사유 1줄 추가, 상세 섹션은 유지 (회고용)
- 요약표의 "연관 시나리오" 가 여러 개일 경우 쉼표로 열거
