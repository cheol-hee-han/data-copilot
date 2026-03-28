# Reasoning 파이프라인 종합 검토 보고서

**검토 일자:** 2026-03-25
**검토 대상:** Data Copilot — Reason 계층 중심 전체 파이프라인 (Interpret → Reason → Present)
**검토 범위:** 파이프라인 흐름, 노드 로직, 상태 모델, 확신도 판정, 데이터소스 연동, 프롬프트 설계
**검토 관점:** 실제 데이터소스 현황 기반 운영 가능성, 소형 LLM 대응, 금융 도메인 특수성

---

## 0. 검토 배경 — 실제 데이터소스 현황

검토 시점에서 확인된 실제 참조 가능 데이터소스와 현재 구현의 매핑은 다음과 같다.

| 저장소 | 실제 보유 데이터 | 현 구현의 활용 방식 | 간극 |
|--------|-----------------|-------------------|------|
| **Qdrant** | 상품설명서(텍스트 임베딩), 업무매뉴얼(텍스트 임베딩), 과거 SQL+설명(설명 임베딩) | `search_use_cases`(sql_history), `search_manual`(biz_manual) | **상품설명서 검색 도구 부재** |
| **MongoDB** | 테이블/컬럼 레이아웃+설명+주제영역, 코드 메타, 비즈용어 사전(~200개) | `search_table_meta`, `search_code_meta`, `search_glossary` | 비즈용어 부실 시 대안 부재 |
| **구현 검토 중** | 프로그램 저장소, 보고서 SQL+요건 저장소, SQL 골든셋 | ES `report_sql` 인덱스 (설계만 존재) | **모두 미구현** |

> **핵심 전제**: 현재 파이프라인은 "메타가 충분히 있다"는 가정 하에 설계되었으나,
> 실제 환경에서는 IT 메타 부실, 용어사전 부족, 보고서 SQL 미확보 상태이며,
> 이 간극이 파이프라인의 가장 큰 챌린지를 형성한다.

---

## 1. 파이프라인 아키텍처 수준 검토

### 1.1 잘 구현된 부분

#### (A) 3계층 분리 아키텍처 (Interpret → Reason → Present)

```
사용자 입력 → [Interpret] 의도 해석·정규화
           → [Reason]    에이전틱 추론 루프 (탐색-판정-생성-검증-복구)
           → [Present]   실행·분석·포맷팅
```

- 계층 간 의존성이 `PipelineState` 단일 상태 모델을 통해서만 연결되어, 각 계층을 독립적으로 테스트·교체 가능
- 라우팅 함수 10개가 `pipeline.py`에 선언적으로 정의되어 흐름 파악이 직관적
- 각 노드가 `async def node(state) -> dict` 시그니처를 지키면서 LangGraph의 상태 머지 의미론을 활용

#### (B) 에이전틱 추론 루프 (Reason 계층)

```mermaid
planner → explorer → evaluator ⇄ generator → validator → recovery → (루프)
```

NL-to-SQL의 본질적 불확실성에 대응하기 위해 **가설-탐색-검증-복구** 사이클을 도입한 것은
단순 프롬프트 체이닝(prompt chaining) 방식 대비 결정적 우위이다.

- **Hypothesis 기반 탐색**: 하나의 질의에 대해 여러 가설(예: "이건 여신 관련 테이블이다", "이건 수신 관련이다")을 세우고 순차 검증
- **DeadEnd 기록**: 실패한 가설·테이블·용어를 기록하여 동일 실패를 반복하지 않도록 학습
- **점진적 지식 축적**: `KnowledgeItem`의 상태 전이 (`UNRESOLVED → CANDIDATE → PROBABLE → CONFIRMED`)가 탐색 과정의 확신도를 추적

#### (C) 다층 루프 가드 (LoopGuard)

| 카운터 | 한계 | 초과 시 동작 |
|--------|------|-------------|
| `total_tool_calls` | 20 | 강제 종료 (TERMINATE) |
| `replan_count` | 3 | 강제 종료 |
| `generate_attempts` | 4 | 강제 종료 |
| `local_fix_count` | 2 | 에스컬레이션 (REPLAN) |

에이전틱 루프의 필연적 위험인 무한 반복을 4개 차원에서 독립적으로 제어하며,
경량 수정(local_fix)이 한계를 넘으면 구조적 재계획으로 에스컬레이션하는 설계가 합리적이다.

#### (D) Fast-Path 최적화

유사 SQL 힌트가 충분하고 미해결 항목이 없으면 탐색 단계를 건너뛰고 바로 SQL 생성으로 진입한다.
Fast-Path 실패 시 `explore_after_fast_path`로 정상 탐색 루프에 안전하게 재진입하는 폴백도 구현되어 있다.

#### (E) SSOT 기반 판정 (confidence_scorer.py)

`evaluate_readiness()` 함수가 explore의 조기 탈출과 evaluate의 라우팅 모두를 담당하여,
판정 로직의 분산으로 인한 불일치 위험을 원천 차단한다.

---

### 1.2 개선이 필요한 부분

#### (A) 데이터소스 이중 구현 — MongoDB vs. ElasticSearch 역할 분리 미흡

**현황:**

`planner.py`의 `_collect_initial_context()`에서:
```python
# MongoDB를 1차로, ES를 2차 폴백으로 사용
mongo = connectors.mongo
if mongo:
    meta_results = await mongo.search_table_meta(keywords)
# ES 폴백
if not meta_results:
    es = connectors.elasticsearch
```

| 데이터 유형 | MongoDB 보유 | ES 보유 | 현재 우선순위 |
|-------------|-------------|---------|-------------|
| 테이블/컬럼 레이아웃 | O (원본) | O (인덱싱) | MongoDB 우선 |
| 코드 메타 | O (원본) | O (인덱싱) | MongoDB 우선 |
| 비즈용어 사전 | O | X | MongoDB 단독 |
| 보고서 SQL | X | 설계만 존재 | — |

**문제 1 — 결과 Merge 전략 부재:**
두 소스에서 동일 테이블에 대해 다른 설명(예: MongoDB는 간략한 설명, ES는 확장된 동의어 포함)이
반환될 때 이를 병합하는 로직이 없다. 현재는 MongoDB가 결과를 반환하면 ES를 아예 호출하지 않으므로,
ES에만 있는 부가 정보(동의어, 유사 보고서)를 놓칠 수 있다.

**문제 2 — 충돌 해소 전략 부재:**
향후 보고서 SQL 저장소가 ES에 구현되면, MongoDB 메타와 ES 보고서 SQL이
동일 테이블에 대해 상충하는 정보를 제공할 수 있다.
(예: MongoDB에는 "월별 집계 테이블"이라 하고, 보고서 SQL에서는 해당 테이블을 일별로 사용)
이때의 신뢰도 판정 기준이 없다.

**권장 개선 방향:**
1. 메타 검색 시 MongoDB + ES를 **병렬 호출**하고, 결과를 `source` 태그와 함께 merge
2. 충돌 시 `KnowledgeItem.status = "CONFLICTED"`로 승격하여 사용자 확인 유도
3. 각 소스의 **권위도(authority)** 정의: 스키마 정보는 MongoDB 우선, 사용 패턴은 ES 우선

#### (B) 단선적 탐색 순서의 한계

**현황:**

`planner.py`의 `_build_execution_plan()`이 생성하는 탐색 계획:
```
Step 1: search_use_cases    (Qdrant sql_history)
Step 2: search_table_meta   (MongoDB)
Step 3: search_code_meta    (MongoDB)
Step 4: search_glossary     (MongoDB)
Step 5: get_sample_data     (Info DB)
```

**문제 — 질의 유형별 최적 탐색 순서가 다름:**

| 질의 유형 | 현재 순서 | 최적 순서 | 이유 |
|-----------|----------|----------|------|
| "연체율 보고서처럼 뽑아줘" | use_cases → table_meta → ... | **report_sql → 역추적** | 보고서 SQL이 가장 직접적 힌트 |
| "대출 잔액 조회" | use_cases → table_meta → ... | table_meta → **code_meta 병렬** | 단순 조회는 메타만으로 충분 |
| "LCR 비율 계산해줘" | use_cases → table_meta → ... | **manual → use_cases** | 계수산출식 확인이 선행 필수 |
| "작년 대비 수신 증감" | use_cases → table_meta → ... | use_cases → table_meta | 현재 순서가 적합 |

**문제 — 보고서 SQL 검색 미포함:**
`TOOL_MAP`에 `search_report_sql`이 정의되어 있으나 (`tools.py`), planner의 execution_plan 생성 로직에서
보고서 SQL 검색을 기본 탐색 계획에 포함시키는 경우가 LLM 판단에만 의존하며,
폴백(기본 계획)에서는 완전히 누락된다.

**권장 개선 방향:**
1. 질의 정규화 결과의 `OUTPUT_HINT` 슬롯을 활용하여 탐색 순서를 동적으로 조정
   - `OUTPUT_HINT.format == "REPORT"` → report_sql 검색 최우선
   - `MEASURE`에 파생 지표(DERIVED/RATIO) 포함 → manual 검색 최우선
2. 병렬 탐색 도입: `search_use_cases`와 `search_table_meta`를 동시에 실행
3. 보고서 SQL 검색을 폴백 계획에도 기본 포함

#### (C) 상품설명서(Qdrant) 활용 경로 완전 부재

**현황:**

Qdrant에 3종의 컬렉션이 있다:
- `sql_history` — 과거 SQL + 설명 (활용 O)
- `biz_manual` — 업무 매뉴얼 (활용 O)
- **상품설명서** — 상품 텍스트 임베딩 (활용 X)

`tools.py`의 `TOOL_MAP`:
```python
TOOL_MAP = {
    "search_use_cases": search_use_cases,      # sql_history
    "search_table_meta": search_table_meta,     # MongoDB
    "search_code_meta": search_code_meta,       # MongoDB
    "search_report_sql": search_report_sql,     # ES
    "search_manual": search_manual,             # biz_manual
    "search_glossary": search_glossary,         # MongoDB
    "get_sample_data": get_sample_data,         # Info DB
    # 상품설명서 검색 도구 없음
}
```

**영향 범위:**
금융 도메인에서 상품 관련 질의는 전체의 상당 비중을 차지한다:
- "정기예금 상품별 잔액 현황" → 상품분류 체계, 상품코드 매핑 필요
- "퇴직연금 수익률 비교" → 상품 유형별 특성 이해 필요
- "주택담보대출 금리 추이" → 상품별 금리 구조 이해 필요

상품설명서에는 상품 분류 체계, 상품코드, 적용 조건 등 **SQL의 WHERE 조건을 추론하는 데
간접적으로 유용한 정보**가 포함되어 있을 수 있다.

**권장 개선 방향:**
1. `search_product_info` 도구 추가 (Qdrant 상품설명서 컬렉션 벡터 검색)
2. planner에서 ENTITY 슬롯에 상품 관련 용어가 있을 때 자동으로 execution_plan에 포함
3. 검색 결과에서 상품코드, 상품분류를 추출하여 `knowledge_items`에 등록

---

## 2. Reason 계층 — 노드별 상세 검토

### 2.1 Planner 노드 (`src/agents/nodes/reason/planner.py`)

#### 잘 구현된 부분

**(A) NormalizedQuery → Decomposition 변환의 체계성**

`_build_decomposition_from_normalized()`가 8-Slot 정규화 결과를 가설 생성에 활용하는 구조가 체계적이다:
- ENTITY → 대상 테이블 탐색 키워드
- MEASURE → 필요 컬럼 식별
- FILTER → WHERE 조건 매핑 대상
- TIME → 날짜 컬럼/형식 추론 대상

각 슬롯이 `knowledge_items`의 초기 UNRESOLVED 항목으로 변환되어 이후 탐색 과정에서 점진적으로 해소된다.

**(B) 모호한 출력 범위 사전 감지**

```python
AMBIGUOUS_OUTPUT_KEYWORDS = ["명세", "현황", "정보", "내역", "데이터", "목록", "리스트"]
```

`_detect_ambiguous_output()`이 사용자의 모호한 출력 요구를 사전에 감지하여
`knowledge_items`에 `output_scope` 항목을 UNRESOLVED로 등록한다.
이는 금융 도메인에서 "현황"이 요약인지 상세인지 모호한 경우가 빈번한 점을 잘 반영했다.

#### 개선이 필요한 부분

**(A) 초기 컨텍스트 수집에서 테이블 간 관계(주제영역) 미활용**

**현황:**
MongoDB의 `dpasset_table` 컬렉션에는 `subject_area`(주제영역) 필드가 존재하나,
`_collect_initial_context()`에서는 키워드 매칭으로만 테이블을 검색한다.

**문제의 심각성:**
정보계 DB에는 동일 도메인의 유사 테이블이 다수 존재한다. 예를 들어 "여신" 도메인에:
```
주제영역: 여신_잔액
  - TB_LN_BAL_D      (여신 잔액 일별 — 일별 스냅샷)
  - TB_LN_BAL_M      (여신 잔액 월별 — 월말 기준)
  - TB_LN_BAL_HIST   (여신 잔액 변경이력 — 이벤트)

주제영역: 여신_실행
  - TB_LN_EXEC_D     (여신 실행 일별)
  - TB_LN_EXEC_HIST  (여신 실행 이력)
```

키워드 "여신 잔액"으로 검색하면 5개 테이블이 모두 후보로 올라오는데,
주제영역 정보를 활용하면 `여신_잔액` 그룹 3개로 범위를 좁히고,
그 안에서 시간 범위(TIME 슬롯)에 맞는 테이블을 선택할 수 있다.

**구체적 개선안:**
```python
# 1단계: 키워드 기반 검색
meta_results = await mongo.search_table_meta(keywords)

# 2단계: 주제영역 기반 군집화
subject_groups = group_by_subject_area(meta_results)

# 3단계: TIME 슬롯과 테이블 갱신주기 매칭
for group_name, tables in subject_groups.items():
    best_match = match_time_granularity(
        tables, normalized_query.time_slot
    )
```

**(B) 비즈용어 사전 부실 시 대안 추론 경로 부재 ★★★**

**현황:**
비즈용어 사전이 약 200개로 부실하다. "연체"를 검색해도 관련 용어가 사전에 없을 수 있다.
현재 `search_glossary`가 빈 결과를 반환하면, 해당 `knowledge_item`은 UNRESOLVED로 남는다.

**문제의 심각성:**
금융 도메인에서 용어 해소는 SQL 정확도의 핵심이다. "연체"가 용어사전에 없을 때:
- DB 컬럼에는 `OVRD_YN` (연체여부), `OVRD_AMT` (연체금액), `OVRD_DD` (연체일수) 등으로 표현
- 코드 메타에는 `상태코드 = '03'`이 연체를 의미
- 과거 SQL에서는 `WHERE STAT_CD = '03' AND OVRD_DD > 0`으로 연체를 필터링

이 **역추론 경로**가 체계적으로 구현되어 있지 않다.

**구체적 개선안:**
```
용어사전 검색 실패 시 보간(Interpolation) 전략:
1. 유사 SQL의 WHERE 조건에서 관련 코드값 패턴 추출 (sql_hint_extractor 활용)
2. 컬럼명 명명 규칙에서 의미 추론 (접두사/접미사 체계: _YN, _AMT, _CD 등)
3. get_sample_data로 실제 데이터 분포 확인 후 코드값 의미 유추
4. 여전히 불확실하면 KnowledgeItem을 CONFLICTED로 승격 → 사용자 확인
```

**(C) 가설 생성의 LLM 폴백 취약성 ★★★**

**현황:**
`_generate_hypotheses()`가 LLM 실패 시 단일 기본 가설만 생성:
```python
hypotheses = [Hypothesis(
    hypothesis_id="h_default",
    description="키워드 기반 직접 탐색",
    strategy="keyword_search",
    required_tables=initial_table_names,
    missing_terms=[ki.key for ki in knowledge_items if ki.status == "UNRESOLVED"],
)]
```

**문제의 심각성:**
폐쇄망의 소형 LLM(7B~70B)에서는 복잡한 JSON 구조의 가설 생성이 자주 실패할 것이다.
단일 기본 가설로는 유사 테이블이 많은 정보계 DB에서 정확한 테이블 선택이 어렵다.

예시 — "지점별 대출 연체 현황":
- 가설 A: 여신 잔액 테이블 + 연체 상태 필터 + 지점 조인
- 가설 B: 연체 관리 전용 테이블 + 지점 필터
- 가설 C: 여신 실적 테이블 + 연체율 산출

기본 가설은 "키워드 기반 직접 탐색" 하나뿐이므로 A/B/C 중 하나만 시도하고
실패하면 탐색 범위가 극도로 제한된다.

**구체적 개선안:**
1. 규칙 기반 다중 가설 생성기 구현 (LLM 불필요):
   - ENTITY 유형별 가설 분기 (DIRECT: 1:1 테이블 매핑, IMPLIED: 주제영역 탐색)
   - MEASURE 유형별 가설 분기 (RAW: 단순 조회, DERIVED: 산출식 탐색)
2. 초기 컨텍스트의 후보 테이블을 기반으로 테이블 그룹별 가설 자동 생성
3. LLM 가설 + 규칙 가설을 merge하여 다양성 확보

---

### 2.2 Context Explorer 노드 (`src/agents/nodes/reason/context_explorer.py`)

#### 잘 구현된 부분

**(A) LLM 해석 + 규칙 기반 이중 구조**

도구 결과 해석에서 LLM(`_interpret_with_llm`)이 실패하면
규칙 기반 파서(`_interpret_rule_based`)로 폴백한다.
이 이중 구조는 소형 LLM 환경에서의 안정성을 크게 높인다.

**(B) 조기 탈출 메커니즘**

각 도구 실행 후 `evaluate_readiness()`를 체크하여, 충분한 확신이 쌓이면
남은 탐색 스텝을 건너뛰고 바로 SQL 생성으로 진입한다.
이는 불필요한 LLM 호출과 외부 시스템 요청을 절약한다.

#### 개선이 필요한 부분

**(A) 테이블 3측면 검증 로직 부재 — 가장 중요한 누락 사항 ★★★**

**요구사항 원문:**
> "테이블은 사용하기 전에 테이블 내 어떤 데이터가 있는지(엔티티 집합 정의),
> 데이터는 어디에 어떻게 쓰이는지(기능적 정의),
> 데이터는 언제 생성되어 적재되는지(데이터 발생규칙)
> 세 가지의 측면에서 추론하는 것이 필요함"

**현황:**
`_extract_tables()`에서 `CandidateTable`을 생성할 때:
```python
class CandidateTable(BaseModel):
    table_name: str
    db_source: str = ""
    role: str = ""                          # 역할 설명 (자유 텍스트)
    relevant_columns: list[str] = []
    join_keys: list[str] = []
    missing_coverage: list[str] = []
```

3가지 측면 중 어느 것도 구조화되어 있지 않다:

| 측면 | CandidateTable 필드 | 검증 로직 |
|------|---------------------|----------|
| 엔티티 집합 정의 (어떤 데이터가 있는지) | 없음 | 없음 |
| 기능적 정의 (어디서 어떻게 쓰이는지) | `role` (자유 텍스트) | 없음 |
| 데이터 발생규칙 (언제 적재되는지) | 없음 | 없음 |

**영향:**
"이번 달 여신 잔액"이라는 질의에서:
- `TB_LN_BAL_D` (일별 스냅샷, 전일 배치 적재) — 어제까지의 데이터
- `TB_LN_BAL_M` (월별 스냅샷, 월말 배치 적재) — 전월까지의 데이터
- `TB_LN_BAL_RT` (실시간 잔액, 실시간 적재) — 현재 시점 데이터

테이블 이름과 설명만으로는 어떤 테이블이 "이번 달" 질의에 적합한지 판단할 수 없다.
데이터 발생규칙(적재 주기)을 알아야 올바른 테이블을 선택할 수 있다.

**구체적 개선안:**

```python
class CandidateTable(BaseModel):
    table_name: str
    db_source: str = ""
    role: str = ""
    relevant_columns: list[str] = []
    join_keys: list[str] = []
    missing_coverage: list[str] = []
    # --- 3측면 검증 필드 추가 ---
    entity_scope: str = ""          # 엔티티 집합: "전체 여신 계좌", "정상 여신만" 등
    functional_usage: str = ""      # 기능적 정의: "잔액 조회용", "실적 집계용" 등
    data_refresh_rule: str = ""     # 발생규칙: "일별 배치(D+1)", "월말 배치", "실시간"
    data_period_range: str = ""     # 데이터 보유 기간: "최근 3년", "당월만" 등
    granularity: str = ""           # 데이터 입도: "일별", "월별", "건별"
    suitability_score: float = 0.0  # 질의와의 적합도 (0.0~1.0)
```

검증 절차:
1. MongoDB 메타에서 테이블 설명을 기반으로 3측면 정보 추출
2. `get_sample_data`의 날짜 컬럼 분포에서 데이터 범위/입도 추론
3. NormalizedQuery의 TIME 슬롯과 테이블의 데이터 발생규칙 매칭
4. 적합하지 않은 후보는 `suitability_score`를 낮추어 SQL 생성에서 배제

**(B) 코드 메타 역방향 매핑의 불확실성**

**현황:**
사용자가 "정상 대출"이라고 했을 때 → 상태코드 '01'로 매핑해야 하는데,
`search_code_meta` 결과에서 코드값 → 의미 매핑은 있지만,
의미 → 코드값 역매핑의 **신뢰도가 불확실**할 때의 처리가 미흡하다.

예를 들어:
```
코드값 '01' = "정상"
코드값 '02' = "요주의"
코드값 '03' = "연체"
```
여기서 "정상 대출"은 `STAT_CD = '01'`이 명확하지만,
"문제 대출"은 `'02'`인지 `'03'`인지, 아니면 둘 다인지 불명확하다.

**문제의 심각성:**
코드값이 틀리면 SQL은 문법적으로 올바르지만 **의미적으로 완전히 다른 결과**를 반환한다.
이는 사용자가 감지하기 어려운 가장 위험한 오류 유형이다.

**구체적 개선안:**
1. 코드값 매핑의 확신도를 `KnowledgeItem.confidence`에 반영
   - 정확히 일치: 0.95 (예: "정상" → '01')
   - 부분 일치: 0.6 (예: "문제" → '02' or '03')
   - 추론: 0.3 (예: 유사 SQL 패턴에서 역추출)
2. 확신도 0.6 미만의 코드값 매핑은 `CONFLICTED`로 승격 → 사용자에게 선택지 제시
3. 유사 SQL의 WHERE 조건에서 해당 코드 컬럼의 사용 패턴을 크로스체크

---

### 2.3 Confidence Evaluator (`src/services/confidence_scorer.py`)

#### 잘 구현된 부분

**(A) 3차원 가중 평균 구조**

| 차원 | 가중치 | 의미 |
|------|--------|------|
| 용어 해소율 (term_resolution) | 50% | knowledge_items의 CONFIRMED/PROBABLE 비율 |
| 유사 SQL 매칭 (use_case_match) | 30% | 탐색된 use_cases 중 최대 similarity |
| 조인 경로 확인 (join_path) | 20% | 다중 테이블 시 조인 경로 존재 여부 |

SQL 생성 준비도를 단일 스칼라가 아닌 다차원으로 평가하는 것이 합리적이며,
각 차원의 가중치가 "무엇을 알아야 SQL을 쓸 수 있나"의 직관과 부합한다.

#### 개선이 필요한 부분

**(A) knowledge_items 비어있을 때의 과대평가**

**현황 (`confidence_scorer.py:100-108`):**
```python
items = reason.knowledge_items
if items:
    resolved = [i for i in items if i.confidence >= 0.8]
    term_score = len(resolved) / len(items)
else:
    term_score = 0.5  # 용어가 없으면 중립 (단순 질의)
```

**문제:**
`knowledge_items`가 비어있는 상황은 다음과 같다:
1. 질의 정규화가 실패한 경우 (`normalization_enabled=True`이지만 LLM 실패)
2. 정규화 비활성화 (`normalization_enabled=False`)
3. 플래너에서 `_initialize_knowledge_items()`가 NormalizedQuery 없이 호출된 경우

이 모든 경우에 `term_score = 0.5`가 되면,
나머지 차원(use_case_match, join_path)이 조금만 점수가 있어도
`총점 = 0.5 × 0.5 + 0.3 × X + 0.2 × Y`로 쉽게 0.75를 넘길 수 있다.

**결과:**
정규화 실패 → knowledge_items 없음 → 0.5 중립 → use_case 유사도 0.8이면 총점 0.49 + 0.24 = 0.73...
이 경우 임계값에 근접하여 탐색이 부족한 상태에서 SQL 생성에 진입할 수 있다.

**구체적 개선안:**
```python
if items:
    resolved = [i for i in items if i.confidence >= 0.8]
    term_score = len(resolved) / len(items)
else:
    # knowledge_items가 비어있으면 "아무것도 모르는 상태"
    # 단, 질의가 매우 단순하여 용어 해소가 불필요한 경우를 구분
    if reason.fast_path_triggered and reason.candidate_tables:
        term_score = 0.5  # Fast-Path에서 후보가 이미 확보된 경우만 중립
    else:
        term_score = 0.0  # 기본: 아무것도 모름 → 탐색 필요
```

**(B) use_case_match 유사도의 과신 위험**

**현황:**
```python
use_cases = reason.explored_use_cases
if use_cases:
    case_score = max(
        uc.get("similarity", 0.0) for uc in use_cases
    )
```

**문제:**
Qdrant의 SQL 이력은 **설명 텍스트로 임베딩**되어 있다. 벡터 유사도는 설명의 의미적 유사성을 측정하지만,
이것이 SQL 구조의 유사성을 보장하지 않는다.

예시:
| 질의 | 유사 SQL 설명 | 유사도 | 실제 SQL 구조 유사성 |
|------|-------------|--------|-------------------|
| "지점별 대출 잔액" | "지점별 대출 건수 현황" | 0.92 | **낮음** (잔액 vs 건수: 대상 테이블/컬럼 다름) |
| "지점별 대출 잔액" | "영업점 여신 잔고 조회" | 0.78 | **높음** (동의어: 지점=영업점, 대출=여신, 잔액=잔고) |

벡터 유사도 0.92를 그대로 `case_score`로 사용하면,
실제로는 구조가 다른 SQL을 높은 확신으로 참조하게 된다.

**구체적 개선안:**
1. 벡터 유사도에 **구조적 유사도 보정** 적용:
   - 유사 SQL에서 추출한 `StructuralHints`와 현재 질의의 `knowledge_items`를 대조
   - 대상 테이블이 일치하면 보정 계수 +0.1, 불일치하면 -0.2
2. 단순히 `max(similarity)`가 아닌, **상위 3개의 가중 평균**으로 안정성 확보
3. use_case_match 점수에 상한(cap)을 두어 유사 SQL만으로 0.75를 넘기지 못하게 제한

**(C) 조인 경로 확인의 이진적 판단**

**현황:**
```python
join_score = 1.0 if reason.confirmed_join_path else 0.0
```

**문제:**
조인 경로의 "존재 여부"만 판단하고 "품질"은 무시한다:

| 조인 경로 유형 | 현재 점수 | 실제 위험도 |
|---------------|----------|------------|
| FK로 확인된 직접 조인 | 1.0 | 낮음 |
| 유사 SQL에서 추론된 조인 | 1.0 | 중간 |
| 컬럼명 유사성 기반 추정 조인 | 1.0 | 높음 |
| 3단계 이상 다중 조인 | 1.0 | 높음 |
| 조인 경로 없음 | 0.0 | — |

**구체적 개선안:**
```python
if needs_join:
    path = reason.confirmed_join_path
    if not path:
        join_score = 0.0
    elif all(p.get("evidence") == "FK" for p in path):
        join_score = 1.0           # FK 기반: 가장 신뢰
    elif len(path) <= 2:
        join_score = 0.8           # 2단계 이내 추론: 비교적 안전
    else:
        join_score = 0.5           # 3단계 이상 / 추정 기반: 위험
```

---

### 2.4 SQL Generator (`src/agents/nodes/reason/sql_generator.py`)

#### 잘 구현된 부분

**(A) 에이전틱 프롬프트 조립**

`_build_agentic_prompt()`가 다음 정보를 모두 SQL 생성 프롬프트에 주입한다:
- confirmed knowledge_items (용어, 테이블, 컬럼 매핑)
- candidate_tables + relevant_columns
- structural_hints (유사 SQL에서 추출한 조인/코드/집계 패턴)
- dead_ends (이미 실패한 경로)
- sql_fix_instruction (이전 검증 실패의 수정 지시)

이 정보의 조합이 SQL 생성의 정확도를 결정적으로 높이며,
특히 dead_ends를 포함하여 동일 실수를 반복하지 않도록 한 것이 핵심이다.

**(B) Dialect-aware SQL 생성**

```python
def determine_dialect(candidate_tables):
    db_sources = {parse_db_source(t.table_name) for t in candidate_tables}
    # 단일 DB → 해당 dialect
    # 복수 DB → cross-DB 감지 → 사용자 확인
```

테이블명의 시스템코드(3자)에서 DB 소스를 파싱하여 PostgreSQL / Sybase IQ(tsql) / Impala(hive) 방언을 자동 선택하고,
교차 DB 질의를 감지하면 사용자에게 명확화를 요청한다.

#### 개선이 필요한 부분

**(A) 계수산출식(금융 지표 Formula) 추론 경로의 구조적 한계**

**현황:**
연체율, BIS비율, LCR, NIM 등 금융 지표의 산출식이 필요한 경우:
1. 업무매뉴얼(`search_manual`) 결과에서 관련 문장을 추출 → **비구조화 텍스트**로 프롬프트에 주입
2. 유사 SQL에서 집계식 패턴 추출(`structural_hints.agg_expressions`) → 참고용

**문제의 심각성:**
"연체율"의 정확한 산출식은:
```sql
연체율 = SUM(연체원금) / SUM(여신잔액) × 100
```
이것이 업무매뉴얼에 "연체율은 연체원금을 여신잔액으로 나눈 비율입니다"라는
자연어로만 존재한다면, 소형 LLM이 이를 정확한 SQL 수식으로 변환할 확률이 낮다.

특히 복잡한 지표(예: BIS비율 = 자기자본 / 위험가중자산)에서는:
- 자기자본의 구성 항목이 뭔지
- 위험가중자산의 산출 방식이 뭔지
이런 재귀적 정의가 자연어 텍스트에서는 추출이 매우 어렵다.

**구체적 개선안:**
1. **구조화된 산출식 저장소** 구현 (별도 MongoDB 컬렉션 또는 YAML):
```yaml
formulas:
  연체율:
    formula: "SUM(ovrd_princ_amt) / NULLIF(SUM(loan_bal_amt), 0) * 100"
    required_tables: ["TB_LN_BAL_D"]
    required_columns: ["ovrd_princ_amt", "loan_bal_amt"]
    unit: "%"
    note: "분모가 0인 경우 NULL 처리"
  BIS비율:
    formula: "SUM(equity_amt) / NULLIF(SUM(risk_weighted_asset_amt), 0) * 100"
    sub_formulas:
      equity_amt: "tier1_capital + tier2_capital"
    required_tables: ["TB_CAP_RATIO_M"]
```
2. planner에서 MEASURE 슬롯에 파생 지표(DERIVED/RATIO)가 있을 때 자동으로 산출식 조회
3. 산출식을 SQL 생성 프롬프트에 **구조화된 형태**로 주입:
   `"연체율 산출식: SUM(ovrd_princ_amt) / NULLIF(SUM(loan_bal_amt), 0) * 100"`

**(B) 구조적 힌트(StructuralHints)의 신뢰도 미구분**

**현황:**
`sql_hint_extractor.py`가 유사 SQL에서 추출한 힌트를 모두 동일 신뢰도로 프롬프트에 주입한다.

**문제:**
| 힌트 소스 | 실제 신뢰도 | 현재 처리 |
|----------|------------|----------|
| 프로덕션 보고서 SQL | 높음 (검증된 SQL) | 동일 |
| 과거 SQL 이력 (검토 완료) | 중간 | 동일 |
| 과거 SQL 이력 (미검토) | 낮음 | 동일 |
| 폐쇄망에서의 Sybase IQ SQL | 파싱 실패 가능 | 동일 |

신뢰도가 낮은 힌트가 높은 힌트를 오버라이드하거나, 잘못된 힌트가 SQL 생성을 오도할 수 있다.

**구체적 개선안:**
1. `StructuralHints`에 `source_reliability` 필드 추가
2. 프롬프트에 힌트를 주입할 때 신뢰도별로 구분:
   - 높음: "검증된 패턴 (반드시 따를 것)"
   - 중간: "참고 패턴 (구조만 참고)"
   - 낮음: "추정 패턴 (검증 필요)"

---

### 2.5 SQL Validator (`src/agents/nodes/reason/sql_validator.py`)

#### 잘 구현된 부분

**(A) 3-레이어 + 5-유형 검증 체계**

```
Layer 1 (규칙): 안전성 검증 (DML 차단, PII, 카탈로그, sqlglot 파싱)
Layer 2a (규칙): 구조 검증 (GROUP BY/집계 일관성, LIMIT 존재)
Layer 2b (LLM): 의미 검증 (7점 체크리스트 대조)
Layer 3 (실행): 실행 검증 (LIMIT 5 실제 실행)
```

검증 실패 유형이 5가지로 세분화되어(`SYNTAX`, `SEMANTIC_LOCAL`, `STRUCTURAL`, `EMPTY`, `DB_ERROR`),
각 유형에 맞는 복구 경로로 정확히 라우팅된다.

**(B) Dialect-aware LIMIT 처리**

```python
# Sybase IQ: SELECT TOP 5 ...
# Impala/Hive: SELECT ... LIMIT 5
# PostgreSQL: SELECT ... LIMIT 5
```

실행 검증(Layer 3)에서 DB 방언에 맞는 LIMIT 구문을 자동 적용한다.

**(C) Layer 2b의 7점 체크리스트**

```
1. MEASURE 반영 여부 — 요청된 지표가 SQL에 포함되었는가?
2. FILTER 반영 여부 — 요청된 조건이 WHERE에 정확히 매핑되었는가?
3. GROUP BY 반영 여부 — 요청된 분류축이 GROUP BY에 포함되었는가?
4. ORDER/LIMIT 반영 여부 — 정렬/상위N 조건이 반영되었는가?
5. 미확인 값 사용 여부 — CONFIRMED 아닌 코드값이 하드코딩되었는가?
6. DeadEnd 반복 여부 — 이미 실패한 패턴을 다시 사용하고 있지 않은가?
7. 논리적 일관성 — SELECT/WHERE/GROUP BY 간 논리적 정합성
```

LLM이 구조화된 체크리스트로 의미 검증을 수행하여, 자유형 검증 대비 일관성이 높다.

#### 개선이 필요한 부분

**(A) Layer 1에서 테이블/컬럼 존재 검증 누락**

**현황:**
Layer 1은 `validate_sql_safety()`(DML 차단, PII, 카탈로그 접근) + sqlglot 파싱만 수행한다.
생성된 SQL에 사용된 테이블/컬럼이 **실제로 메타데이터에 존재하는지** 확인하는 로직이 없다.

**문제의 심각성:**
소형 LLM은 hallucination으로 존재하지 않는 테이블/컬럼명을 생성할 수 있다.
현재는 이런 오류가 Layer 3(실행 검증)에서 DB 에러로 감지되지만:
- Layer 3까지 도달하는 데 불필요한 LLM 호출(Layer 2b)이 소모됨
- DB 에러 메시지만으로는 "어떤 테이블/컬럼이 없는지" 파싱이 불안정

**구체적 개선안:**
Layer 1 직후에 **Layer 1.5: 메타 존재 검증** 삽입:
```python
async def validate_meta_existence(sql: str, candidate_tables: list):
    """SQL에 사용된 테이블/컬럼이 후보 메타에 존재하는지 확인."""
    used_tables = extract_tables_from_sql(sql)  # sqlglot
    used_columns = extract_columns_from_sql(sql)

    known_tables = {t.table_name for t in candidate_tables}
    known_columns = {col for t in candidate_tables for col in t.relevant_columns}

    missing_tables = used_tables - known_tables
    missing_columns = used_columns - known_columns

    if missing_tables:
        return "FAIL_STRUCTURAL", f"존재하지 않는 테이블: {missing_tables}"
    if missing_columns:
        return "FAIL_SEMANTIC_LOCAL", f"확인되지 않은 컬럼: {missing_columns}"
    return "PASS", ""
```

**(B) Layer 3 실행 검증의 결과 정합성 미검증**

**현황:**
`LIMIT 5` 실행 후 "에러가 나지 않는다" + "결과가 0건이 아닌지"만 확인한다.

**문제의 심각성:**
실행은 성공하지만 의미적으로 잘못된 결과가 반환되는 경우:
- "이번 달 신규 대출"인데 → 결과에 작년 데이터가 포함 (날짜 조건 오류)
- "서울 지점"인데 → 결과에 전국 지점 포함 (필터 누락)
- 결과 건수가 비정상적으로 많거나 적은 경우 (카디널리티 이상)

이런 오류는 사용자가 결과를 받아보기 전까지 감지되지 않으며,
금융 데이터의 특성상 잘못된 숫자가 의사결정에 사용되면 심각한 문제가 된다.

**구체적 개선안:**
Layer 3 이후 **Layer 3.5: 결과 정합성 검증** 추가:
```python
async def validate_result_sanity(
    sample_rows: list[dict],
    normalized_query: NormalizedQuery,
    knowledge_items: list[KnowledgeItem],
) -> tuple[str, str]:
    """샘플 결과의 기본 정합성을 검증한다."""
    issues = []

    # 1. 날짜 범위 검증
    if normalized_query.time_slot:
        date_columns = [col for col in sample_rows[0].keys() if "date" in col.lower() or "dt" in col.lower()]
        for col in date_columns:
            dates = [row[col] for row in sample_rows if row.get(col)]
            if not is_within_expected_range(dates, normalized_query.time_slot):
                issues.append(f"날짜 범위 불일치: {col}의 값이 요청 기간 밖")

    # 2. 코드값 검증
    for ki in knowledge_items:
        if ki.key.startswith("code:") and ki.status == "CONFIRMED":
            code_col, code_val = ki.key.split(":")[1], ki.value
            if code_col in sample_rows[0]:
                actual_vals = {row[code_col] for row in sample_rows}
                if code_val not in actual_vals:
                    issues.append(f"코드값 불일치: {code_col}에 {code_val} 미존재")

    # 3. 카디널리티 이상 감지
    if len(sample_rows) == 5 and all identical:  # 모든 행이 동일
        issues.append("카디널리티 이상: 모든 샘플 행이 동일")

    if issues:
        return "FAIL_SEMANTIC_LOCAL", "; ".join(issues)
    return "PASS", ""
```

---

### 2.6 Recovery Planner (`src/agents/nodes/reason/recovery_planner.py`)

#### 잘 구현된 부분

**(A) 실패 원인 분류의 정밀성**

`_infer_failure_type()`이 SQL 검증 결과의 세부 정보를 분석하여 6가지 실패 유형으로 분류:
- `sql_structural`: 테이블/컬럼 불일치
- `empty_result`: 결과 0건
- `db_error`: DB 실행 오류
- `sql_syntax`: 구문 오류
- `sql_semantic_local`: GROUP BY 누락 등 로컬 수정 가능
- `term_unresolvable`: 용어 해소 실패

**(B) DeadEnd 기반 중복 회피**

`_build_replan_execution()`이 이전에 시도한 검색 쿼리(`searched_queries`)와
실패한 테이블(`tried_tables`)을 제외하여 동일 실패를 반복하지 않는다.

#### 개선이 필요한 부분

**(A) 복구 전략이 "가설 교체" 단일 패턴으로 한정**

**현황:**
모든 실패 유형에 대해 복구 전략이 동일하다:
1. 현재 가설을 FAILED 처리
2. DeadEnd 기록
3. 다음 PENDING 가설 활성화 (없으면 LLM으로 새 가설 생성)
4. 새 execution_plan 수립

**문제의 심각성:**
실패 유형별로 최적의 복구 전략이 다르다:

| 실패 유형 | 현재 복구 | 최적 복구 | 현재 문제 |
|----------|----------|----------|----------|
| `sql_structural` (테이블/컬럼 불일치) | 가설 교체 | **가설은 유지, 테이블만 교체** | 가설 자체는 맞을 수 있음 |
| `empty_result` (결과 0건) | 가설 교체 | **조건 완화** (날짜 범위 확대, 필터 제거) | 테이블/SQL은 맞고 조건만 너무 restrictive |
| `db_error` (DB 오류) | 가설 교체 | **방언 수정** (tsql vs hive 구문 차이) | 방언 오류는 가설과 무관 |
| `term_unresolvable` | 가설 교체 | **사용자에게 질문** | 정보가 없으면 가설을 바꿔도 같은 문제 |

**구체적 개선안:**
```python
async def recovery_planner_node(state: PipelineState) -> dict:
    failure_type = _infer_failure_type(state.reason)

    match failure_type:
        case "sql_structural":
            # 가설 유지, 대체 테이블 탐색
            return await _recover_by_table_swap(state)

        case "empty_result":
            # 조건 완화 시도 (날짜 범위 확대 → 필터 제거 → 가설 교체)
            return await _recover_by_condition_relaxation(state)

        case "db_error":
            # 방언 수정 재시도
            return await _recover_by_dialect_fix(state)

        case "term_unresolvable":
            # 사용자 확인 요청
            return await _recover_by_asking_user(state)

        case _:
            # 기존 가설 교체 로직
            return await _recover_by_hypothesis_swap(state)
```

**(B) 복구 시 이전 탐색 결과의 완전 폐기**

**현황:**
가설 교체 시 새 execution_plan을 처음부터 수립하며, 이전 가설에서 수집된
knowledge_items 중 여전히 유효한 정보도 사실상 재탐색한다.

**문제:**
예를 들어 가설 A에서 "지점 테이블은 TB_BRANCH_M이다"를 CONFIRMED했는데,
가설 B로 전환 시 다시 지점 테이블을 검색하는 것은 비효율적이다.

**구체적 개선안:**
- knowledge_items 중 가설에 독립적인 항목(공통 참조 테이블, 코드 매핑 등)은 유지
- 가설에 종속적인 항목(특정 테이블의 컬럼 매핑 등)만 UNRESOLVED로 리셋
- `KnowledgeItem`에 `hypothesis_scope: Optional[str]` 필드 추가하여 범위 구분

---

## 3. Interpret 계층 — 핵심 검토

### 3.1 질의 정규화 (8-Slot NormalizedQuery)

#### 잘 구현된 부분

**(A) 금융 도메인 특화 스키마 설계**

8-Slot 구조가 금융 데이터 질의의 핵심 요소를 빠짐없이 포착한다:
- INTENT (9종): AGGREGATE, RANK, COMPARE, TREND 등 금융 보고서의 전형적 질의 유형
- FILTER: IMPLICIT 타입으로 금융 도메인의 암묵적 조건 (정상 상태, 활성 계좌 등)을 명시적으로 표현
- OUTPUT_HINT: SPEC_SHEET(명세), REPORT(보고서) 등 금융 실무의 출력 형식 반영
- TIME: RELATIVE("이번 달", "전년 동기") 처리로 금융 보고서의 시간 기준 변환 지원

**(B) Phase 2 교차검증 (R1~R12 규칙)**

Phase 1의 LLM 추출 결과를 12개 규칙으로 교차 검증하여 내부 일관성을 보장한다:
- R1: DIMENSION ↔ MEASURE 일관성 (GROUP BY가 있으면 집계 필수)
- R4: RANK 시 SORT + LIMIT 자동 보완
- R5: TREND 시 TIME 슬롯 필수 검증
- R6: DISTRIBUTE 시 PERCENTAGE 모디파이어 자동 추가

#### 개선이 필요한 부분

**(A) 소형 LLM에서의 8-Slot 추출 안정성 우려**

**현황:**
Phase 1 프롬프트(`query_normalizer_phase1_system.txt`)가 약 2000+ 토큰이며,
기대 출력 JSON이 30+ 필드의 복잡한 중첩 구조이다.
Few-shot 예제는 3개만 제공된다.

**문제의 심각성:**
폐쇄망의 소형 LLM(7B~70B 급)에서:
- JSON 구조 자체가 깨질 확률이 높음 (닫는 괄호 누락, 키 오타 등)
- 중첩 필드(ENTITY 내부의 type, confidence, implied_tables 등)의 정확도가 낮을 것
- Few-shot 3개로는 9종 INTENT × 3종 ENTITY × 4종 MEASURE의 조합을 커버 불가

**영향 전파:**
정규화 실패 → knowledge_items 부실 → confidence_scorer 과대/과소 평가 → SQL 생성 품질 저하

**구체적 개선안:**
1. **경량 모드(Lite Mode)** 도입:
   - 핵심 4-Slot만 추출: ENTITY, MEASURE, FILTER, TIME
   - JSON 대신 `key=value` 쌍으로 출력 (파싱 안정성 ↑)
   - 나머지 슬롯(DIMENSION, MODIFIER, OUTPUT_HINT)은 규칙 기반으로 추론
2. **단계적 추출**:
   - 1차: INTENT + ENTITY만 추출 (가장 중요, 가장 단순)
   - 2차: MEASURE + FILTER 추출 (1차 결과를 컨텍스트로 제공)
   - 3차: 나머지 슬롯
3. **JSON 파싱 폴백 강화**:
   - JSON 파싱 실패 시 정규식으로 부분 추출
   - 부분 추출이라도 knowledge_items 초기화에 활용

**(B) 암묵적 필터(IMPLIED) 주입의 LLM 의존**

**현황:**
금융 도메인에서 "대출 잔액"이라 하면 암묵적으로 "정상 상태"만을 의미하는 경우가 많다.
현재 이 암묵적 필터는 NormalizedQuery의 FILTER에 `type: "IMPLICIT"`로 표현되지만,
이를 인식하고 추가하는 것이 전적으로 LLM의 도메인 지식에 의존한다.

**문제의 심각성:**
소형 LLM은 한국 금융 도메인의 암묵적 규칙을 학습하지 못했을 가능성이 높다.
암묵적 필터 누락은 "정상 + 연체 + 상각" 모든 상태의 데이터를 반환하게 되어,
집계 결과가 기대값과 크게 달라지는 원인이 된다.

**구체적 개선안:**
규칙 기반 암묵적 필터 주입 로직 (LLM 불필요):
```python
IMPLICIT_FILTERS = {
    "대출": {"column": "stat_cd", "op": "NOT_IN", "value": ["상각", "매각"]},
    "예금": {"column": "acct_stat_cd", "op": "EQUALS", "value": "정상"},
    "고객": {"column": "cust_stat_cd", "op": "EQUALS", "value": "활성"},
}

def inject_implicit_filters(normalized_query, entity_term):
    for keyword, filter_rule in IMPLICIT_FILTERS.items():
        if keyword in entity_term:
            normalized_query.filters.append(
                Filter(type="IMPLICIT", **filter_rule)
            )
```

이 규칙은 도메인 전문가가 관리하는 YAML 파일로 외부화한다.

### 3.2 의도 분류 — DATA_EXTRACTION vs DATA_ANALYSIS 구분

**현황:**
Stage 2 세분류가 키워드 기반:
```python
ANALYSIS_KEYWORDS = ["추이", "비교", "분석", "변화", "증감", "패턴"]
# 이 키워드가 있으면 → DATA_ANALYSIS, 없으면 → DATA_EXTRACTION
```

**문제:**
"지점별 대출 잔액"은 키워드상 DATA_EXTRACTION이지만, 사용자의 실제 의도는:
- 지점 간 비교를 원할 수 있고 (→ ANALYSIS)
- 단순 목록을 원할 수 있다 (→ EXTRACTION)

이 구분이 후속 처리에 미치는 영향:
- DATA_ANALYSIS → `analyze_data_node` 실행 → 인사이트 + 시각화 생성
- DATA_EXTRACTION → 바로 `format_response_node` → 표 형식 결과만 반환

**구체적 개선안:**
1. DIMENSION 슬롯에 GROUP 역할이 있으면 기본적으로 DATA_ANALYSIS로 승격
2. 결과 행 수 예상치가 임계값 이하(≤20건)이면 EXTRACTION, 초과이면 ANALYSIS 성향
3. 의도가 모호할 때 **양쪽 모두 실행**하고 format_response에서 분석 결과 포함 여부를 결정

---

## 4. Present 계층 — 핵심 검토

### 4.1 시각화 판정 및 SVG 생성

#### 잘 구현된 부분
- 20+ 차트 유형과 11개 제외 기준이 세밀
- 3-Tier 폴백 (LLM SVG → 규칙 기반 chart_generator → 생략)으로 안정성 확보

#### 개선이 필요한 부분

**SVG 생성 프롬프트의 과대한 크기**

`analyzer_viz_svg_system.txt`가 약 15,000줄로 매우 크다.
이 프롬프트를 소형 LLM(7B)에 전달하면:
- 컨텍스트 윈도우 대부분을 소모 → 실제 데이터에 할당할 토큰 부족
- 복잡한 SVG 명세를 이해하고 생성할 능력 자체가 부족할 가능성

**개선 방향:**
1. SVG 생성은 Tier 2(규칙 기반 chart_generator)를 **주 경로**로 전환
2. LLM SVG 생성은 Claude급 대형 모델에서만 활성화
3. 규칙 기반 생성기를 차트 유형별로 강화 (bar, line, pie 3종만이라도 완성도 높게)

---

## 5. 횡단적 챌린지

### 5.1 소형 LLM 대응의 구조적 미비 (P0)

**현재 LLM 호출 지점 (최소 7~10회/질의):**

| 호출 지점 | 프롬프트 복잡도 | JSON 출력 | 폴백 품질 |
|----------|---------------|----------|----------|
| history_resolver | 중 | O | 규칙 기반 게이트 (양호) |
| intent_classifier | 중 | O | Legacy 분류기 (양호) |
| normalize_phase1 | **상** | O (복잡) | **빈 결과** (미흡) |
| normalize_phase2 | 상 | O (복잡) | Phase 1 결과 유지 (보통) |
| planner (가설) | 상 | O | 기본 가설 1개 (미흡) |
| explorer (해석) | 중 | O | 규칙 기반 파서 (양호) |
| sql_generator | **상** | O (SQL) | **없음** (치명적) |
| sql_validator_L2b | 중 | O | SKIP (보통) |
| analyzer | 중 | O | 없음 |
| formatter | 중 | X (텍스트) | 템플릿 (보통) |

**핵심 위험:**
- `normalize_phase1`과 `sql_generator`의 폴백이 가장 취약
- 소형 LLM에서 JSON 파싱 실패가 빈번할 것으로 예상
- 누적 실패 확률: 각 단계 90% 성공이라도 7단계 통과 시 `0.9^7 = 47.8%`

**권장 대응 전략:**
1. **LLM 호출 최소화 모드**: normalization_phase2 비활성화, validator_L2b SKIP, analyzer 선택적
2. **JSON → 구조화 텍스트 폴백**: 파싱 실패 시 정규식 기반 부분 추출
3. **핵심 노드 Structured Output**: sql_generator에서 `tool_use`/`function_calling` 활용

### 5.2 골든셋 부재로 인한 품질 측정 불가 (P0)

**현황:**
참고 정보에서 "SQL 골든셋(아직 없음, 구현방안 미정)"이라 명시.

**영향 범위:**
현재 파이프라인의 모든 하이퍼파라미터가 경험적 추정에 의존:

| 하이퍼파라미터 | 현재 값 | 검증 상태 |
|--------------|---------|----------|
| confidence THRESHOLD_GENERATE | 0.75 | 미검증 |
| confidence THRESHOLD_REPLAN | 0.30 | 미검증 |
| 가중치 (term/use_case/join) | 50/30/20 | 미검증 |
| MAX_TOOL_CALLS | 20 | 미검증 |
| MAX_REPLANS | 3 | 미검증 |
| MAX_GENERATES | 4 | 미검증 |
| MAX_LOCAL_FIXES | 2 | 미검증 |
| SQL_MAX_RETRY | 2 | 미검증 |
| CLARIFICATION_MAX_TURNS | 2 | 미검증 |

**권장 대응 전략:**
1. 도메인별 대표 질의 50~100건으로 **최소 골든셋** 구축
2. 각 질의에 대해 정답 SQL + 정답 테이블 + 정답 컬럼 + 기대 행 수 표기
3. 평가 지표: 테이블 선택 정확도, SQL 실행 성공률, 결과 정합성(행 수/컬럼 일치)
4. 골든셋 기반으로 하이퍼파라미터 그리드 서치

### 5.3 메타데이터 불완전성 보간 전략 (P1)

**현황:**
파이프라인이 "메타가 있으면 잘 동작, 없으면 LLM 일반 지식에 의존"하는 이분법적 구조.

**필요한 보간 전략 (우선순위 순):**

| 보간 방법 | 정보원 | 신뢰도 | 현재 구현 |
|----------|--------|--------|----------|
| 유사 SQL WHERE 조건에서 코드값 역추론 | sql_history | 중 | **미구현** |
| `get_sample_data` 데이터 분포에서 컬럼 의미 추론 | info_db | 중 | **미구현** |
| 컬럼명 명명 규칙 파싱 (_CD, _AMT, _YN, _DT) | 메타 자체 | 낮음 | **미구현** |
| 유사 테이블의 메타로 유추 (같은 주제영역) | MongoDB | 중 | **미구현** |
| LLM 일반 지식 | 모델 내재 | 최저 | 현재 유일한 폴백 |

---

## 6. 코드 수준 구체적 이슈

| # | 파일 | 위치 | 이슈 | 심각도 |
|---|------|------|------|--------|
| C-01 | `confidence_scorer.py` | L108 | `term_score = 0.5` — knowledge_items 비어있을 때 과대평가 | **상** |
| C-02 | `planner.py` | `_should_fast_path()` | UNRESOLVED 0건이면서 knowledge_items 자체가 0건일 때도 Fast-Path 가능 | **상** |
| C-03 | `context_explorer.py` | `_extract_tables()` | CandidateTable에 갱신주기/데이터범위 미반영 — 유사 테이블 구분 불가 | **상** |
| C-04 | `sql_validator.py` | Layer 3 | LIMIT 5 결과의 의미적 정합성 미검증 (날짜 범위, 코드값 일치) | **중** |
| C-05 | `tools.py` | `TOOL_MAP` | 상품설명서 검색 도구 미등록 | **중** |
| C-06 | `recovery_planner.py` | 전체 | 복구 전략이 가설 교체 단일 패턴 — 실패 유형별 분기 없음 | **중** |
| C-07 | `sql_generator.py` | `_build_agentic_prompt()` | structural_hints의 신뢰도 구분 없이 동일 가중으로 주입 | **중** |
| C-08 | `pipeline.py` | L152-156 | `evaluate_readiness().value`로 직접 라우팅 — enum 변경 시 런타임 오류 | **하** |
| C-09 | `db_routing.py` | `parse_db_source()` | 3문자 시스템코드 파싱이 테이블명 convention에 강결합 | **하** |
| C-10 | `tools.py` | `search_use_cases()` | Qdrant 벡터 검색의 similarity 임계값이 코드에서 설정 불가 (하드코딩) | **하** |

---

## 7. 종합 평가 및 우선순위 제안

### 전체 평가

파이프라인 아키텍처는 **NL-to-SQL 분야의 최신 연구(가설-검증-복구 패턴, 에이전틱 추론 루프)**를
금융 도메인에 맞게 잘 적용했으며, 특히 3계층 분리, 다층 루프 가드, SSOT 판정, Fast-Path 최적화 등
**설계 수준이 높다**.

다만 실제 운영 환경에서의 **견고성(robustness)**에 대한 보완이 필요하다.
핵심 간극은: (1) 불완전한 메타데이터 환경, (2) 소형 LLM의 능력 한계,
(3) 하이퍼파라미터 검증 수단(골든셋) 부재 — 이 세 가지가 상호 결합하여
파이프라인의 실제 정확도를 예측하기 어렵게 만든다.

### 개선 우선순위

| 순위 | 항목 | 사유 | 관련 섹션 |
|------|------|------|----------|
| **P0** | 소형 LLM 경량 모드 설계 | 폐쇄망 배포의 전제 조건 — LLM 호출 최소화, JSON 폴백 강화 | 5.1 |
| **P0** | 골든셋 구축 + 평가 프레임워크 | 모든 하이퍼파라미터 튜닝 및 품질 측정의 전제 조건 | 5.2 |
| **P1** | 메타 불완전성 보간 전략 구현 | 실제 정보계 DB 환경에서의 정확도 직결 | 5.3, 2.1(B) |
| **P1** | 계수산출식 구조화 저장소 | 금융 지표 정확성의 핵심 — 자연어 텍스트만으로는 한계 | 2.4(A) |
| **P1** | 테이블 3측면 검증 + CandidateTable 확장 | 유사 테이블 다수인 정보계 DB에서 정확한 선택의 핵심 | 2.2(A) |
| **P2** | 복구 전략 세분화 (실패 유형별 분기) | 재시도 성공률 향상 | 2.6(A) |
| **P2** | 상품설명서 검색 도구 추가 | 상품 관련 질의 정확도 향상 | 1.2(C) |
| **P2** | confidence_scorer 개선 (빈 항목, 유사도 보정, 조인 품질) | 판정 정확도 향상 | 2.3 |
| **P2** | SQL Validator Layer 1.5 (메타 존재 검증) + Layer 3.5 (정합성 검증) | 검증 완전성 향상 | 2.5 |
| **P3** | 데이터소스 Merge 전략 (MongoDB + ES 병렬 호출) | 정보 활용 극대화 | 1.2(A) |
| **P3** | 탐색 순서 동적 조정 (질의 유형별) | 탐색 효율성 향상 | 1.2(B) |
| **P3** | 암묵적 필터 규칙 기반 주입 | 소형 LLM 도메인 지식 부족 보완 | 3.1(B) |

---

*검토자: Claude Opus 4.6*
*최종 갱신: 2026-03-25*
