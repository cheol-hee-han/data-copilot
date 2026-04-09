# 도구 결과 렌더러 및 파이프라인 재설계서

## 1. 배경 및 문제

### 1.1. 현재 구조의 문제점

knowledge_interpreter의 `_serialize_tool_results`가 LLM 배치 해석(batch interpret) 프롬프트에
도구 결과를 전달할 때 다음 문제가 있다.

1. **purpose와 result 분리**: 실행 스텝의 목적(purpose)은 "실행된 도구 목록"에, 결과는 타입별 섹션에 따로 표시.
   LLM이 "왜 이걸 실행했고 결과가 뭔지" 교차 참조해야 함.

2. **일부 도구 결과 누락**: `explored_biz_terms`, `explored_biz_manuals` 결과가 직렬화에서 빠져 있어
   LLM이 용어사전/매뉴얼 결과를 보지 못함.

3. **비구조적 직렬화**: `explored_use_cases`가 `json.dumps()`로 raw JSON 전달 — 내부 메타 필드까지 노출.

4. **use_case에 테이블 컨텍스트 부재**: 유사 SQL만 보여주고 해당 SQL이 사용하는 테이블의
   한글명·컬럼 정보가 없어 LLM이 SQL의 의미를 파악하기 어려움.

5. **table_meta에 컬럼 정보 부족**: 테이블명, PK, 컬럼 수만 표시하고 전체 컬럼 목록이 없음.

6. **date_distribution에 적재 주기 정보 부재**: 범위·패턴만 제공하고 실제 날짜 값이 없어
   일별/월별/영업일 적재 주기를 판단할 수 없음.

7. **파이프라인 책임 혼재**: fetcher가 도구 실행과 동시에 state 필드에 직접 적재하고,
   암묵적 후속 작업(`_fetch_use_case_related_metas`, `_sample_unsampled_tables`,
   `_observe_all_date_distributions`)까지 수행.
   interpreter는 이미 적재된 데이터에 도장만 찍는 구조로, gatekeeper 역할을 못 함.

8. **판정 범위 부족**: interpreter가 테이블과 use_case만 판정하고,
   biz_terms·biz_manuals는 판정 없이 fetcher가 직접 적재.


## 2. 설계 원칙

### 2.1. 파이프라인 책임 분리

| 노드 | 책임 |
|------|------|
| 플래너 | 실행 계획 수립 (모든 도구 호출을 명시적 스텝으로 계획) |
| fetcher | 계획된 도구 실행 + enrichment → **step.raw_result에 저장** (state는 read-only) |
| interpreter | step.raw_result에서 읽음 → 렌더링 → LLM 판정 → **검토 후 state에 적재** |

interpreter가 진정한 gatekeeper — 검토 후에만 state 필드에 적재한다.

### 2.2. 스텝 단위 블록 조립

실행된 DONE 스텝을 순회하며 각 스텝의 **purpose + result**를 한 블록으로 조립한다.
도구별 렌더러를 맵으로 등록하여 동적 디스패치한다.

### 2.3. LLM 친화적 포맷

- JSON 0% — 전부 자연어/구조화 텍스트
- 각 블록은 **purpose → 결과 → 판단 가이드(→)** 3단 구조
- 결과 없음도 명시적으로 표현

### 2.4. 정보 완전성 우선

- 컬럼, SQL 등 정보를 축소(truncate)하지 않는다
- 토큰 문제는 데이터 축소가 아니라 fallback 분할로 해결 (§6 참조)
- 단, 대형 테이블(100개 이상 컬럼)에 대해서는 §12.1의 보완 전략 검토 필요

### 2.5. 도구별 판단 가이드

각 렌더러 끝에 `→` 지시문으로 LLM에게 무엇을 판단해야 하는지 명시한다.

| tool | LLM 판단 요구 |
|------|--------------|
| search_use_cases | 테이블, 조인 구조, 필터 조건의 재활용 가능성 |
| search_table_meta | 테이블이 질의에 적합한가 |
| search_code_meta | 질의 조건에 해당하는 코드값 특정 |
| search_biz_terms | 용어 정의가 SQL 변환에 주는 힌트 |
| search_manual | 산출식, 업무 규칙이 SQL 로직에 미치는 영향 |
| get_sample_rows | 날짜 포맷, 코드값 패턴, NULL 등 데이터 특성 |
| get_date_distribution | 시간 조건 포함 여부, 적재 주기, 날짜 포맷 |
| search_column_values | 필터에 사용할 정확한 값 |
| get_column_profile | NULL율, 카디널리티 이상 여부 |


## 3. 파이프라인 구조 변경

### 3.1. ExecutionStep 변경

```python
class ExecutionStep(BaseModel):
    step: int
    tool: str
    input: str
    purpose: str
    expected_output: str = ""
    status: StepStatus = StepStatus.PENDING
    insight: str | None = None
    raw_result: dict[str, Any] | list | None = None  # 신규
```

- fetcher가 도구 실행 후 결과를 step.raw_result에 저장
- 타입을 `dict[str, Any] | list | None`으로 제한 (직렬화 안전)
- interpreter 완료 후 `step.raw_result = None` 설정 (deep copy 비용 + 메모리 해제)
- recovery_agent가 execution_plan을 새로 생성하면 이전 스텝도 자연스럽게 교체

### 3.2. Enrichment — 암묵적 후속 작업을 스텝 반환값에 통합

| tool | raw_result 구조 | enrichment 내용 |
|------|----------------|----------------|
| search_use_cases | `{use_cases: [...], tables: [...], codes: {...}}` | 테이블 메타(alt_name + 전체 컬럼) + 샘플 + 코드 |
| search_table_meta | `{tables: [...]}` | 샘플 |
| search_code_meta | 코드 메타 리스트 | — |
| search_biz_terms | 용어 리스트 | — |
| search_manual | 매뉴얼 리스트 | — |
| get_sample_rows | 샘플 행 리스트 | — |
| get_date_distribution | 날짜 분포 데이터 | — |
| search_column_values | 컬럼 값 리스트 | — |
| get_column_profile | 컬럼 통계 | — |

search_use_cases enrichment 상세:
- use_case SQL이 사용하는 테이블의 **한글명(alt_name)**과 **전체 컬럼(컬럼 한글명 포함)**을 함께 조회.
- LLM이 유사 SQL의 테이블 구조를 이해해야 조인 패턴·필터 조건의 재활용 가능성을 판단할 수 있기 때문.
- 같은 테이블이 여러 use_case에 반복되면 중복 생략하여 토큰 절약.

search_table_meta enrichment 상세:
- 발견된 테이블에 대해 샘플 행만 조회. 날짜분포는 별도 도구(get_date_distribution)로 처리.

### 3.2.1. _extract_tables rule-based 전처리 (fetcher에 유지)

search_table_meta 결과에 대해 fetcher가 수행하는 rule-based 전처리를 유지한다:
- `CandidateTable.from_meta()` → `TableEntry.from_meta()`로 변환
- `schema_name` 없으면 기본 스키마 보정
- PK 컬럼 접미사(`_YMD`, `_DT` 등)에서 `key_date_columns` 식별

이 전처리는 LLM 판정이 아닌 순수 rule-based 로직이며, sql_generator·interpreter 날짜 태깅에 필수적이다.

다만 현행처럼 `candidate_tables.extend()`로 state에 직접 적재하지 않고,
**변환된 TableEntry 리스트를 raw_result에 포함**한다:

```python
# 변경 전: fetcher가 state에 직접 적재
new_tables = _extract_tables(step, result)
candidate_tables.extend(new_tables)

# 변경 후: raw_result에 포함
tables = _extract_tables(step, result)  # rule-based 변환은 유지
step.raw_result = {"tables": [t.model_dump() for t in tables]}
```

### 3.3. Phase 2 삭제

현행의 암묵적 자동 수집 로직을 완전히 제거한다:
- `_sample_unsampled_tables` → search_table_meta/search_use_cases enrichment로 대체
- `_observe_all_date_distributions` → **플래너가 get_date_distribution 스텝을 필요 시 계획에 포함**하여 대체

이로써 fetcher의 역할이 "계획된 스텝 실행 + enrichment"로 순수화된다.
플래너(recovery_agent/reasoning_preparer) 프롬프트에
"시간 조건이 있는 질의에서 날짜 기준 컬럼 확인이 필요하면 get_date_distribution을 포함하라" 지시를 추가한다.
플래너가 빠뜨린 경우 readiness_gate + recovery 사이클이 안전망 역할을 한다.

### 3.4. fetcher 내부 중복 방지 및 state 쓰기 범위

fetcher가 `seen_tables: set`을 로컬로 유지하여 enrichment 시 중복 조회를 방지한다.
이전 라운드의 explored_tables(state)도 seen_tables 초기값으로 포함한다.

**state 쓰기 범위 정의**:

- **Read-Only (fetcher가 쓰지 않음)**: 도구 결과 데이터 — explored_tables, explored_use_cases, explored_biz_terms, explored_biz_manuals, code_map. 이 필드들은 interpreter만 적재한다.
- **갱신 허용 (fetcher가 씀)**: 스텝 메타 — step.status, step.raw_result, searched_queries, loop_guard, phase. 도구 실행 진행에 필수적인 메타데이터.

### 3.5. 멀티 라운드 테이블 중복 처리

이미 explored_tables에 있는 테이블은 건드리지 않는다:
- fetcher: seen_tables에 포함 → enrichment에서 스킵
- interpreter: 재판정 안 함, 이전 라운드 SELECTED/REJECTED 유지
- 재판정이 필요한 경우(recovery 반복 실패 등): 별도 도구(`re_evaluate_table`)로 명시적 처리

### 3.6. Interpreter의 gatekeeper 역할

1. 모든 DONE 스텝의 raw_result를 렌더러로 조립 → **LLM 1회 배치 호출**
2. LLM이 스텝별로 insight + 판정을 수행
3. 판정 결과에 따라 state 필드에 적재:

| 도구 결과 | interpreter 판정 | 적재 방식 |
|-----------|-----------------|----------|
| 테이블 | SELECTED/REJECTED | explored_tables에 적재 |
| use_case | SELECTED/REJECTED | explored_use_cases에 적재 |
| biz_terms | SELECTED/REJECTED | explored_biz_terms에 적재 |
| biz_manuals | SELECTED/REJECTED | explored_biz_manuals에 적재 |
| code_map | **판정 없이 적재** | code_map에 그대로 적재 |
| 관찰 도구 (sample_rows 등) | **판정 대상 아님** | 해당 테이블의 보조 정보로 반영 |

REJECTED도 state에 적재한다:
- recovery_agent가 execution_plan을 교체하면 step.raw_result 소멸 → rejected 정보 유실 방지
- insight_builder가 탈락 사유 표시, recovery_agent가 재탐색 방지에 활용

4. 적재 완료 후 모든 DONE 스텝의 `step.raw_result = None` 설정
5. 관찰 도구(get_sample_rows, get_date_distribution, search_column_values, get_column_profile)의 결과는
   해당 테이블의 explored_tables 엔트리에 보조 정보(sample_rows, observed_date_columns 등)로 매칭하여 반영한다.
6. search_use_cases enrichment의 tables/codes는 interpreter가 TableEntry/code_map으로 변환(hydration)하여 state에 적재한다.

### 3.7. LLM 호출 실패 정책

LLM 연결 실패(타임아웃, API 오류 등)는 interpreter 전용 정책이 아닌 **모든 노드 공통 fallback**으로 처리한다:
사용자에게 LLM 호출 비정상을 알리고 그래프를 종료한다.
토큰 초과에 의한 Level 0 → Level 1 분할(§6)과는 별개의 문제이다.

### 3.8. 변경되는 흐름 요약

```
[현행]
fetcher: 도구 실행 → state 필드에 즉시 적재 → Phase 2 자동 수집도 state에 적재
interpreter: state 필드에서 읽음 → LLM 판정 → 테이블/use_case만 마킹

[변경]
플래너: 모든 필요 도구를 명시적 스텝으로 계획 (get_date_distribution 포함)
fetcher: 계획된 스텝 실행 + enrichment → step.raw_result에 저장 (state read-only)
interpreter: step.raw_result에서 읽음 → 렌더링 → LLM 1회 배치 호출
         → 모든 도구 결과 판정 (테이블+use_case+biz_terms+biz_manuals)
         → 판정 결과에 따라 state 필드에 적재 → raw_result = None
```


## 4. Interpreter 출력 형식

### 4.1. 설계 원칙

- top-level selected/rejected 배열을 제거하고 **interpretations 하위에 통합**
- 각 스텝별로 explored_tables, explored_use_cases, explored_biz_terms, explored_biz_manuals 배열 포함
- SelectionStatus Enum 값(SELECTED/REJECTED) 통일 사용
- 관찰 도구는 판정 배열 없이 insight + knowledge_updates만
- state 필드 네이밍과 일치

### 4.2. 출력 형식

```json
{
  "interpretations": [
    {
      "tool_name": "search_use_cases",
      "tool_input": "신규 대출 실행 건수",
      "insight": "TB_LOAN_MASTER와 TB_LOAN_EXEC를 LOAN_NO로 조인하여 실행 건수를 집계하는 패턴 확인",
      "knowledge_updates": [
        {
          "key": "join:TB_LOAN_MASTER-TB_LOAN_EXEC",
          "value": "TB_LOAN_MASTER.LOAN_NO = TB_LOAN_EXEC.LOAN_NO",
          "confidence": 0.85,
          "new_status": "CONFIRMED",
          "source": "활용사례",
          "evidence": "기존 SQL에서 두 테이블 LOAN_NO 조인 패턴 확인",
          "is_critical": true
        }
      ],
      "explored_tables": [
        {"table_name": "TB_LOAN_EXEC", "status": "SELECTED", "reason": "활용사례에서 집계 대상 확인"},
        {"table_name": "TB_LOAN_MASTER", "status": "SELECTED", "reason": "조인 구조 확인"}
      ],
      "explored_use_cases": [
        {"sql_id": "uc_001", "status": "SELECTED", "reason": "동일 테이블·조인, 집계 패턴 참고 가능"}
      ]
    },
    {
      "tool_name": "search_table_meta",
      "tool_input": "대출 원장",
      "insight": "TB_LOAN_DETAIL은 부대조건 관리로 질의와 무관",
      "knowledge_updates": [...],
      "explored_tables": [
        {"table_name": "TB_LOAN_DETAIL", "status": "REJECTED", "reason": "부대조건 테이블, 건수 집계와 무관"}
      ]
    },
    {
      "tool_name": "search_biz_terms",
      "tool_input": "신규 실행",
      "insight": "신규실행은 EXEC_TYPE='N'으로 구분 가능",
      "knowledge_updates": [...],
      "explored_biz_terms": [
        {"term": "신규실행", "status": "SELECTED", "reason": "EXEC_TYPE 필터 조건 힌트 제공"}
      ]
    },
    {
      "tool_name": "search_manual",
      "tool_input": "연체율 산출",
      "insight": "연체율 산출식 확인",
      "knowledge_updates": [...],
      "explored_biz_manuals": [
        {"manual_id": "bm_001", "status": "SELECTED", "reason": "산출식이 SQL 로직에 직접 영향"},
        {"manual_id": "bm_002", "status": "REJECTED", "reason": "업무 프로세스 설명, SQL과 무관"}
      ]
    },
    {
      "tool_name": "get_sample_rows",
      "tool_input": "TB_LOAN_EXEC",
      "insight": "EXEC_TYPE 컬럼에 N(신규)/A(추가) 패턴 확인",
      "knowledge_updates": [...]
    }
  ]
}
```


## 5. 도구별 렌더러 상세

### 5.1. search_use_cases

**데이터 소스**: `step.raw_result` — `{use_cases, tables, codes}`

**특이사항**: use_case SQL이 사용하는 테이블의 한글명(alt_name)과 전체 컬럼(컬럼 한글명 포함)을 함께 표시.
같은 테이블이 여러 use_case에 반복되면 "위와 동일 — 중복 생략"으로 토큰 절약.
enrichment에 없는 테이블은 이름만 표시 (graceful degradation).

**렌더링 예시**:

```
### [Step 1] search_use_cases("지점별 기업대출 신규 실행 건수를 집계하여 조회한다")
목적: 유사 SQL에서 테이블/조인 구조, 집계 방식 참고

발견된 유사 SQL 2건:

1. "지점별 월간 대출 실행 건수 및 금액 집계 조회" (유사도: 0.87)
   도메인: LON
   SQL: SELECT b.BRANCH_NM, COUNT(*) AS EXEC_CNT, SUM(e.EXEC_AMT) AS EXEC_AMT
        FROM TB_LOAN_EXEC e
        JOIN TB_BRANCH b ON e.BRANCH_CD = b.BRANCH_CD
        WHERE e.EXEC_YMD BETWEEN '20240301' AND '20240331'
        GROUP BY b.BRANCH_NM ORDER BY EXEC_AMT DESC

   사용 테이블:
   - TB_LOAN_EXEC (여신실행내역)
     PK: LOAN_NO(대출번호), EXEC_SEQ(실행순번), EXEC_YMD(실행일자)
     컬럼: BRANCH_CD(지점코드), EXEC_AMT(실행금액), LOAN_DCD(대출구분코드),
           EXEC_TYPE(실행유형), CUST_ID(고객ID), CCY_CD(통화코드),
           INT_RATE(금리), EXEC_STS_CD(실행상태코드), GUAR_TYPE(담보유형),
           EXEC_CHANNEL(실행채널), REG_DT(등록일시), UPD_DT(수정일시)
   - TB_BRANCH (지점정보)
     PK: BRANCH_CD(지점코드)
     컬럼: BRANCH_NM(지점명), REGION_CD(지역코드), REGION_NM(지역명),
           BRANCH_TYPE(지점유형), OPEN_DT(개점일), CLOSE_YN(폐점여부)

2. "기업대출 실행 현황 조회" (유사도: 0.72)
   도메인: LON
   SQL: SELECT m.LOAN_NO, m.CUST_NM, e.EXEC_AMT, e.EXEC_YMD
        FROM TB_LOAN_MASTER m
        JOIN TB_LOAN_EXEC e ON m.LOAN_NO = e.LOAN_NO
        WHERE m.LOAN_DCD = '03' AND e.EXEC_YMD >= '20240101'

   사용 테이블:
   - TB_LOAN_MASTER (여신원장)
     PK: LOAN_NO(대출번호), BASE_YMD(기준일자)
     컬럼: CUST_ID(고객ID), CUST_NM(고객명), LOAN_DCD(대출구분코드),
           LOAN_AMT(대출금액), BRANCH_CD(지점코드), LOAN_STS_CD(대출상태코드),
           OPEN_YMD(개시일자), MAT_YMD(만기일자), INT_RATE(금리),
           GUAR_CD(담보코드), OVDU_DAYS(연체일수), OVDU_AMT(연체금액)
   - TB_LOAN_EXEC (여신실행내역)
     (위와 동일 — 중복 생략)

→ 위 SQL에서 현재 질의에 재활용 가능한 테이블, 조인 구조, 필터 조건을 판단하세요.
```

**결과 없음**:

```
### [Step 1] search_use_cases("외환파생상품 시가평가 손익을 거래유형별로 산출하여 조회한다")
목적: 유사 SQL에서 시가평가 산출 패턴 참고

결과 없음 — 유사한 과거 SQL이 존재하지 않습니다.
```


### 5.2. search_table_meta

**데이터 소스**: `step.raw_result` — `{tables: [...]}` (샘플 enrichment 포함)

**렌더링 예시**:

```
### [Step 2] search_table_meta("대출 실행")
목적: 대출 실행 관련 테이블 구조 확인

발견된 테이블 2건:

1. TB_LOAN_EXEC (여신실행내역) — 대출 건별 실행 정보
   PK: LOAN_NO(대출번호), EXEC_SEQ(실행순번), EXEC_YMD(실행일자)
   컬럼:
   - BRANCH_CD(지점코드) VARCHAR
   - EXEC_AMT(실행금액) NUMBER
   - LOAN_DCD(대출구분코드) VARCHAR
   - ...

→ 각 테이블이 질의에 적합한지 판단하세요.
```


### 5.3. search_code_meta

**데이터 소스**: `step.raw_result` (코드 메타 리스트)

**렌더링 예시**:

```
### [Step 3] search_code_meta("LOAN_DCD")
목적: 대출구분코드에서 '기업대출'에 해당하는 코드값 확인

LOAN_DCD (대출구분코드) 코드값 6건:
  - 01: 일반대출
  - 02: 주택담보대출
  - 03: 기업대출
  ...

→ 질의 조건에 해당하는 코드값을 특정하세요. 여러 값이 해당되면 모두 포함하세요.
```


### 5.4. search_biz_terms

**데이터 소스**: `step.raw_result` (용어 리스트)

**렌더링 예시**:

```
### [Step 4] search_biz_terms("신규 실행")
목적: '신규 실행'의 정확한 업무 정의 확인

- 신규실행: 여신 계약 체결 후 최초로 자금이 실행(지급)되는 행위.
            기존 대출의 추가 실행(추가대출)과 구분됨.
  동의어: 신규대출실행, 최초실행
  관련 테이블: TB_LOAN_EXEC

→ 이 정의가 SQL 변환(집계 방식, 필터 조건, 산출식)에 어떤 힌트를 주는지 판단하세요.
```


### 5.5. search_manual (search_biz_manual)

**데이터 소스**: `step.raw_result` (매뉴얼 리스트)

**렌더링 예시**:

```
### [Step 5] search_manual("연체율 산출")
목적: 업무 매뉴얼에서 연체율 공식 정의 확인

1. (유사도: 0.91)
   제4조(연체율 산출 기준) 연체율은 다음과 같이 산출한다.
   연체율(%) = 연체원금잔액 / 대출원금잔액 × 100
   ...

→ 산출식, 업무 규칙, 데이터 기준이 SQL 로직에 영향을 주는지 판단하세요.
```


### 5.6. get_sample_rows

**데이터 소스**: `step.raw_result` (샘플 행 리스트)

**렌더링 예시**:

```
### [Step 6] get_sample_rows("TB_LOAN_EXEC")
목적: EXEC_TYPE 값으로 신규/추가 구분 가능한지 확인

TB_LOAN_EXEC 샘플 5행:
LOAN_NO | EXEC_SEQ | EXEC_YMD | BRANCH_CD | EXEC_AMT | LOAN_DCD | EXEC_TYPE
--- | --- | --- | --- | --- | --- | ---
L20240001 | 1 | 20240315 | BR001 | 50000000 | 03 | N
...

→ 날짜 포맷, 코드값 패턴, NULL 여부 등 실제 데이터 특성을 확인하세요.
```


### 5.7. get_date_distribution

**데이터 소스**: `step.raw_result` (날짜 분포 데이터)

**state 변경**: `ObservedDateColumn.recent_values: list[str]` 필드 추가.
knowledge_fetcher에서 `sorted(dates, reverse=True)[:10]`을 저장. 추가 DB 쿼리 없음.

**렌더링 예시 — 영업일 단위 적재**:

```
### [Step 7] get_date_distribution("TB_LOAN_EXEC,EXEC_YMD")
목적: EXEC_YMD의 데이터 범위가 이번 달을 포함하는지 확인

TB_LOAN_EXEC.EXEC_YMD:
  데이터 범위: 20230101 ~ 20240328
  날짜 패턴: YYYYMMDD
  최근 10건: 20240328, 20240327, 20240326, 20240325, 20240322,
            20240321, 20240320, 20240319, 20240318, 20240315

→ 질의의 시간 조건이 이 범위에 포함되는지,
  적재 주기(일별/월별/영업일)와 날짜 포맷을 확인하세요.
```


### 5.8. search_column_values

**데이터 소스**: `step.raw_result` (컬럼 값 리스트)

**렌더링 예시**:

```
### [Step 8] search_column_values("TB_BRANCH,BRANCH_NM,서울")
목적: 지점명에 '서울'이 포함된 실제 값 목록 조회

TB_BRANCH.BRANCH_NM 에서 '서울' 검색 결과 8건:
  - 서울중앙지점
  - 서울강남지점
  ...

→ 질의 조건에 사용할 정확한 값을 특정하세요.
```


### 5.9. get_column_profile

**데이터 소스**: `step.raw_result` (컬럼 통계)

**렌더링 예시**:

```
### [Step 9] get_column_profile("TB_LOAN_EXEC,EXEC_AMT")
목적: 실행금액 컬럼의 데이터 품질 확인

TB_LOAN_EXEC.EXEC_AMT 컬럼 통계:
  총 행수: 1,234,567
  NOT NULL: 1,234,560
  NULL율: 0.0%
  고유값 수: 45,230
  MIN: 1000000
  MAX: 9999999999

→ NULL율이 높거나 고유값 수가 예상과 다르면 데이터 품질 이슈를 보고하세요.
```


## 6. 토큰 초과 fallback 전략

### 6.1. 원칙

- 데이터를 축소(truncate)하지 않는다
- 토큰 문제는 프롬프트 분할로 해결

### 6.2. 2-Level 전략

```
Level 0: 전체 배치 (기본)
  - 모든 DONE 스텝을 한 프롬프트에 포함
  - 교차 참조 가능, LLM 호출 1회
  - 토큰 예산 내이면 항상 이 모드

  ↓ 토큰 초과 예상 시

Level 1: 스텝별 개별 호출 + 종합 판정
  - 각 호출의 공통 컨텍스트:
    · 질의 원문 + 시간 조건
    · 누적 지식 (knowledge_items 요약)
    · 이전 라운드 인사이트 (discovered_facts)
    · 이전 스텝에서 이번 라운드에 도출한 insight (누적)
  - 각 호출의 개별 입력:
    · 이 스텝의 purpose + 결과 (전체, 축소 없음)
  - 각 호출의 출력:
    · insight + knowledge_updates + 해당 스텝의 판정
  - 마지막: 종합 판정 호출 1회
    · 모든 스텝의 insight + knowledge_updates + 스텝별 판정 결과
    · 최종 확인 및 교차 검증
  - 총 호출: N(스텝 수) + 1(종합 판정)
```

### 6.3. 토큰 추정

렌더링 완료 후 간이 추정. (§12.3의 보완 검토 참조)

### 6.4. 폐쇄망 LLM 고려

폐쇄망 타겟 모델(Solar Pro 2 70B, Qwen3.5 397B)은 컨텍스트가 짧을수록 안정적이므로
분할이 오히려 각 도구 결과에 대한 분석 품질을 높일 수 있다.


## 7. 구현 구조

### 7.1. 렌더러 맵

```python
_TOOL_RENDERERS: dict[str, Callable] = {
    "search_use_cases": _render_use_cases,
    "search_table_meta": _render_table_meta,
    "search_code_meta": _render_code_meta,
    "search_biz_terms": _render_biz_terms,
    "search_manual": _render_biz_manuals,
    "get_sample_rows": _render_sample_rows,
    "get_date_distribution": _render_date_distribution,
    "search_column_values": _render_column_values,
    "get_column_profile": _render_column_profile,
}
```

### 7.2. 메인 직렬화 함수

```python
def serialize_tool_results_by_step(
    execution_plan: list[ExecutionStep],
) -> str:
    """DONE 스텝을 순회하며 step.raw_result로부터 purpose + result 블록을 조립한다."""
```

state 필드를 인자로 받지 않는다. step.raw_result에서 모든 데이터를 읽는다.

### 7.3. Interpreter 적재 함수 분리

interpreter 메인 함수 후반부를 기능별로 분리한다:

```
1. _apply_batch_insights()          — 기존 유지. step.insight 설정
2. _populate_discovered_facts()     — 기존 유지. discovered_facts 누적
3. _apply_judgments()               — 신규. 4종 판정 결과를 state에 적재
   ├─ _apply_table_judgments()      — explored_tables 적재 (SELECTED/REJECTED)
   ├─ _apply_use_case_judgments()   — explored_use_cases 적재
   ├─ _apply_biz_term_judgments()   — explored_biz_terms 적재
   └─ _apply_biz_manual_judgments() — explored_biz_manuals 적재
4. _apply_observation_data()        — 신규. 관찰 도구 결과를 테이블 보조 정보로 매칭
   (sample_rows, observed_date_columns, discovered_values 등)
5. _hydrate_enrichment()            — 신규. use_case enrichment의 tables/codes → state 적재
   (code_map 적재 + enrichment 테이블을 explored_tables에 추가)
6. _cleanup_rejected_knowledge()    — 기존 로직 분리. rejected 테이블 관련 KI 정리
7. _dedup_knowledge_items()         — 기존 유지
8. _promote_sampled_confidence()    — 기존 유지
9. _clear_raw_results()             — 신규. 모든 DONE 스텝의 raw_result = None
```

- `_apply_judgments`가 4종을 하나의 진입점에서 처리하되, 내부적으로 entity별 helper로 분리
- `_apply_observation_data`와 `_hydrate_enrichment`는 LLM 판정과 무관한 데이터 적재 → 별도 함수
- `_clear_raw_results`는 최후에 실행 — 모든 적재가 끝난 후


## 8. State 변경 요약

### 8.1. 네이밍 변경

| 현행 | 변경 | 사유 |
|------|------|------|
| `CandidateTable` | `TableEntry` | BizManualEntry, BizTermEntry와 패턴 통일 |
| `candidate_tables` | `explored_tables` | explored_use_cases 등과 네이밍 통일 |

### 8.2. SelectionStatus Enum 통합 (완료)

`TableSelectionStatus` + `RelevanceStatus` → `SelectionStatus` 단일 Enum.
테이블, 매뉴얼, 용어사전, use_case 모두 SELECTED/REJECTED 상태 사용.

### 8.3. ObservedDateColumn.recent_values 추가

```python
class ObservedDateColumn(BaseModel):
    column_name: str
    date_range: str = ""
    date_pattern: str = ""
    recent_values: list[str] = Field(default_factory=list)  # 신규
```

### 8.4. ExecutionStep.raw_result 추가

```python
raw_result: dict[str, Any] | list | None = None
```

### 8.5. Dead field 제거 대상

- `KnowledgeItem.is_inferred`
- `conflicted_bounce_count`
- `last_verdict`
- `CandidateTable.inferred_entity_scope` — knowledge_updates·insight·alt_name과 중복
- `CandidateTable.inferred_functional_usage` — alt_name 재서술에 불과
- `CandidateTable.inferred_data_refresh_hint` — 거의 항상 빈 문자열, observed_date_columns와 중복
- `CandidateTable.inferred_key_date_column` — 쓰는 코드 없음(항상 빈 문자열), 유일한 사용처인 _observe_all_date_distributions도 Phase 2 삭제 대상

### 8.6. discovered_facts 유지

discovered_facts는 제거하지 않는다.
recovery_agent가 execution_plan을 교체하면 이전 plan의 insight가 소멸하지만,
discovered_facts는 라운드를 넘어 누적되는 유일한 인사이트 저장소이다.


## 9. Interpreter 프롬프트 변경 요약

### 9.1. 입력 변경

| 현행 | 변경 |
|------|------|
| tool_name/tool_input/tool_purpose/tool_result 4필드 | 렌더러가 조립한 스텝 단위 블록 |
| 별도 "후보 테이블 관찰 데이터" 섹션 | 제거 (스텝 블록에 통합) |

### 9.2. 분석 지침 추가

- 용어사전(biz_terms) 판정 지시 추가
- 업무매뉴얼(biz_manuals) 판정 지시 추가

### 9.3. 출력 형식 변경

- top-level selected/rejected/relevant_use_cases 배열 제거
- interpretations 하위에 explored_tables, explored_use_cases, explored_biz_terms, explored_biz_manuals 통합
- SelectionStatus 값(SELECTED/REJECTED) 사용
- 관찰 도구는 판정 배열 없이 insight + knowledge_updates만

### 9.4. 프롬프트 갱신 범위

- **출력 형식 템플릿**: §4.2 형식으로 전면 교체
- **예시 2건**: 현행 프롬프트의 2개 예시를 새 출력 형식에 맞게 전면 재작성
- **분석 지침**: biz_terms, biz_manuals 판정 지시 추가
- **dead field 제거**: `is_inferred` 관련 지시 삭제
- **입력 형식 설명**: 렌더러 블록 구조(purpose → result → 판단 가이드) 설명 추가


## 10. 수정 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/agents/state/state.py` | CandidateTable→TableEntry, candidate_tables→explored_tables, ObservedDateColumn.recent_values, ExecutionStep.raw_result, dead field 제거 (is_inferred, conflicted_bounce_count, last_verdict, inferred_entity_scope, inferred_functional_usage, inferred_data_refresh_hint) |
| `src/agents/state/__init__.py` | CandidateTable re-export → TableEntry |
| `src/models/enums.py` | SelectionStatus 통합 (완료) |
| `src/agents/nodes/reason/knowledge_fetcher.py` | state 직접 적재 제거, step.raw_result 저장, enrichment 통합, Phase 2 삭제, seen_tables |
| `src/agents/nodes/reason/knowledge_interpreter.py` | step.raw_result에서 읽기, 9개 렌더러, Level 0/1 분기, 판정 범위 확장, 판정 후 state 적재, raw_result=None, inferred_* 필드 적재/직렬화/그룹핑 로직 제거 (_group_by_keyword는 alt_name fallback 유지) |
| `src/agents/nodes/reason/sql_generator.py` | CandidateTable→TableEntry, candidate_tables→explored_tables, inferred_entity_scope/functional_usage/data_refresh_hint 참조 제거 |
| `src/agents/nodes/reason/sql_validator.py` | 동일 네이밍 변경 |
| `src/agents/nodes/reason/recovery_agent.py` | 동일 네이밍 변경 |
| `src/agents/nodes/reason/readiness_gate.py` | candidate_tables→explored_tables, last_verdict 갱신 코드 제거 (dead field) |
| `src/agents/nodes/reason/result_finalizer.py` | 동일 네이밍 변경 |
| `src/services/insight_builder.py` | CandidateTable→TableEntry, candidate_tables→explored_tables |
| `src/connectors/manager.py` | candidate_tables→explored_tables |
| `src/utils/tracker/callback_handler.py` | "candidate_tables" 문자열 키 변경 |
| `src/utils/tracker/visualizer.py` | "candidate_tables" 문자열 키 변경 |
| `resources/prompts/reason/knowledge_interpreter_system.txt` | 입력 형식, 분석 지침, 출력 형식 전면 갱신 |
| `resources/prompts/reason/recovery_agent_system.txt` | get_date_distribution 계획 포함 지시 추가, `{candidate_tables_summary}` placeholder 네이밍 변경 |
| `src/agents/nodes/reason/reasoning_preparer.py` | dead field(last_verdict, conflicted_bounce_count) 초기화 코드 제거 |
| `src/agents/nodes/reason/reasoning_preparer.py` 내 `_build_execution_plan()` | get_date_distribution 계획 포함 로직 추가 (프롬프트 파일 없음, rule-based 노드) |
| `src/services/confidence_scorer.py` | ReasoningState 속성으로 접근하므로 state.py 변경 시 자동 반영 가능 — 직접 수정 불필요할 수 있음 (구현 시 확인) |
| 테스트 파일 6개+ | 네이밍 변경 + 구조 변경 반영 |


## 11. 구현 우선순위

1. **Phase 1**: 네이밍 변경 (별도 PR) — CandidateTable→TableEntry, candidate_tables→explored_tables + dead field 제거
2. **Phase 2**: ExecutionStep.raw_result + fetcher 리팩터링 (Phase 2 삭제, enrichment, seen_tables, state read-only)
3. **Phase 3**: 9개 렌더러 + interpreter 리팩터링 (판정 범위 확장, 출력 형식 변경, state 적재, raw_result=None)
4. **Phase 4**: Level 0/1 fallback + 프롬프트 갱신 + 테스트


## 12. 보완 검토 필요사항

### 12.1. 대형 테이블 컬럼 처리 (Critical)

은행 정보계 테이블은 100~300개 컬럼이 흔하다.
"축소하지 않음" 원칙이 현실적인지 실 테이블 기준으로 토큰 시뮬레이션 후 결정 필요.
불가능하면 "PK + 질의 관련 컬럼 우선 + 나머지 이름만 나열" 전략 도입.

### 12.2. Level 1 종합 판정 시 정보 손실 (Critical)

Level 1에서 스텝별 개별 호출 후 종합 판정 시, 원본 데이터 없이 판정해야 하는 문제.
필요 시 종합 판정에 테이블 핵심 요약(이름, alt_name, PK, 컬럼수, 날짜 범위)을 추가 제공.

### 12.3. 토큰 추정 정확도 (Warning)

`len(text) // 3`은 SQL + 영문 테이블명 비중이 높으면 과대 추정.
tiktoken 기반 또는 한국어/영어 비율 가중 추정 적용 검토.

### 12.4. 판단 가이드 구체화 (Warning)

"확인하세요", "판단하세요"만으로는 폐쇄망 모델에서 빈약한 insight 생성 위험.
"X를 확인하고, Y가 발견되면 Z로 판정하세요" If-Then 패턴으로 통일 검토.

### 12.5. 프롬프트 정합성 (Warning)

현행 프롬프트의 분석 지침, 출력 규칙이 새 렌더러의 블록 구조 및 출력 형식에 맞게 전면 갱신 필요.

### 12.6. 스텝 간 테이블 중복 렌더링 (Warning)

search_use_cases enrichment 테이블과 search_table_meta 테이블이 겹칠 때
"Step N에서 이미 표시 — 생략" 처리로 토큰 절약.

### 12.7. Sybase/Hive/Impala 커넥터 sanitize_row 누락 (Info)

get_sample_rows에서 폐쇄망 DB 커넥터가 datetime/Decimal 타입을 반환할 수 있음.
해당 커넥터에 sanitize_row() 추가로 별도 수정.
