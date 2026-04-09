# 설계서 종합 점검 보고서: tool-result-renderer-design.md

**대상**: `docs/working/tool-result-renderer-design.md`
**일시**: 2026-04-04
**점검 관점**: 결정사항 반영 여부, 설계서 내부 일관성, 현재 구현 적용 시 문제점


---

## 1. 누락된 결정사항

### 1-1. ObservedDateColumn.recent_values -- fetcher 저장 로직 미기술 (Info)

설계서 SS8.3과 SS5.7에 `recent_values` 필드 추가와 `sorted(dates, reverse=True)[:10]` 저장이 명시되어 있다.
그러나 SS3.2의 enrichment 테이블에서 `get_date_distribution`의 raw_result 구조가 "날짜 분포 데이터"로만
기술되어 있어, fetcher가 raw_result에 recent_values를 어떤 구조로 포함해야 하는지 불명확하다.
`raw_result`에 `{date_range, date_pattern, recent_values}` 형태로 저장하는지,
아니면 원본 날짜 리스트를 그대로 저장하고 interpreter가 파싱하는지 명시 필요.

### 1-2. 결정사항 #13 -- seen_tables 초기화 소스 불완전 (Warning)

SS3.4에서 "이전 라운드의 explored_tables(state)도 seen_tables 초기값으로 포함"이라고 기술.
그러나 **enrichment로 자동 조회되는 테이블**(search_use_cases의 테이블 메타, 코드 메타)도
seen_tables에 추가해야 하는지 명시되지 않음. 현행 fetcher의 `_fetch_use_case_related_metas`에서
`searched_queries`로 중복을 방지하는데, seen_tables와 searched_queries의 관계가 불명확.

### 1-3. 결정사항 #14 -- re_evaluate_table 도구 상세 미기술 (Info)

SS3.5에서 "별도 도구(re_evaluate_table)로 명시적 처리"만 언급.
이 도구의 입력/출력, TOOL_MAP 등록, interpreter 판정 범위 포함 여부 등이 미기술.
Phase 1~4 구현 범위에도 포함되지 않음. 향후 구현 시 혼란 가능.


---

## 2. 잘못 반영된 결정사항

### 2-1. 결정사항 #3 -- Dead field 제거 시 프롬프트 정리 누락 (Warning)

SS8.5에서 `KnowledgeItem.is_inferred` 제거가 명시되어 있다.
그러나 현행 `knowledge_interpreter_system.txt` 프롬프트의 출력 형식 예시에서
LLM이 is_inferred를 출력할 수 있고, `_parse_batch_result`(knowledge_interpreter.py line 388)에서
해당 키를 파싱한다.
SS10 수정 대상 파일에 프롬프트 갱신이 포함되어 있으나, is_inferred 관련 예시 정리가 구체적으로 명시되지 않음.

### 2-2. 결정사항 #17 -- 프롬프트 예시 갱신 범위 미명시 (Warning)

결정사항 #17: "top-level 배열 제거, interpretations 하위에 explored_tables/explored_use_cases/
explored_biz_terms/explored_biz_manuals 통합, SelectionStatus 사용"

SS4.2 예시에는 이것이 정확히 반영되어 있다.
**그러나 현행 `knowledge_interpreter_system.txt`(line 110~149)의 출력 형식은
top-level `selected`/`rejected`/`relevant_use_cases` 배열 구조이며,
예시 2개(line 154~343)도 동일 구조.**
SS9.3에서 변경 내용을 기술하면서 프롬프트 내 예시 갱신 범위가 누락.

### 2-3. 결정사항 #10 -- enrichment PK 정보 포함 여부 모호 (Warning)

결정사항: "테이블 한글명(alt_name) + 전체 컬럼(컬럼 한글명) 포함"
SS5.1 렌더링 예시에서는 `PK: ..., 컬럼: ...`으로 PK와 일반 컬럼을 구분하여 표시하는데,
enrichment raw_result 구조(`{use_cases, tables, codes}`)의 tables 내부에
PK 정보 포함 여부가 불명확. 현행 MongoDB 메타에서 is_pk 정보가 있으므로 enrichment에서 함께 가져와야 한다.


---

## 3. 설계서 내부 모순

### 3-1. SS3.2 vs SS5.2 -- search_table_meta enrichment 범위 모호 (Warning)

SS3.2: `search_table_meta`의 enrichment는 "샘플만" 조회.
SS5.2 렌더링 예시: 컬럼 타입까지 표시 (`BRANCH_CD(지점코드) VARCHAR`).
컬럼 타입은 원본 메타 응답에 이미 포함된 것이므로 실질적 모순은 아니지만,
SS3.2 테이블의 "enrichment 내용" 칸에 "샘플"만 적혀 있어 "raw_result에 이미 메타 전체가 포함"이 명시적이지 않다.

### 3-2. SS3.3 vs SS3.2 -- Phase 2 삭제와 enrichment 샘플 자동 조회 (Warning)

SS3.3: Phase 2의 `_sample_unsampled_tables`를 삭제하고 enrichment로 대체.
SS3.2: `search_table_meta`의 enrichment에 "샘플" 포함.
이 enrichment 샘플 조회는 현행 Phase 2의 `_sample_unsampled_tables`와 동일한 역할이나,
범위가 "이 스텝에서 발견된 테이블만"으로 제한된다는 차이가 설계서에서 명시적이지 않다.
search_use_cases enrichment에서 발견된 테이블에도 샘플이 필요한지 미기술.

### 3-3. SS10 수정 대상 파일 누락 (Warning)

다음 파일이 SS10에서 누락:

| 파일 | 변경 사유 |
|------|----------|
| `src/services/confidence_scorer.py` | KnowledgeItem.is_inferred 제거 시 import 영향 확인 |
| `src/agents/nodes/reason/reasoning_preparer.py` | get_date_distribution 필수 포함 프롬프트 지시 |
| `resources/prompts/reason/reasoning_preparer_system.txt` | 동일 |

### 3-4. code_map 적재 경로 미기술 (Info)

SS3.6에서 code_map은 "판정 없이 적재"로 명시.
그러나 interpreter가 raw_result에서 code_map 데이터를 꺼내 state에 적재하는
구체적 코드 패스가 "판정 후 state 적재" 흐름의 예외로서 명확히 기술되어 있지 않다.


---

## 4. 구현 적용 시 문제점 (파일별)

### 4-1. knowledge_fetcher.py -- fetcher state 쓰기 전수 조사 (Critical)

현행 fetcher(`knowledge_fetcher_node`, line 484-532)가 state에 쓰는 모든 필드:

| 쓰기 대상 | 위치 | 설계서 처리 | 비고 |
|-----------|------|-------------|------|
| `reason.phase` | L491 | 유지 | phase 전이는 노드 책임 |
| `reason.searched_queries` | L519 | **미언급** | read-only 원칙과 충돌 |
| `reason.explored_use_cases` | L520 | 제거 (raw_result) | |
| `reason.code_map` | L521 | 제거 (raw_result) | |
| `reason.explored_biz_manuals` | L522 | 제거 (raw_result) | |
| `reason.explored_biz_terms` | L523 | 제거 (raw_result) | |
| `reason.loop_guard` | L524 | 유지 | 루프 카운터는 fetcher 책임 |
| `reason.candidate_tables` | L530 | 제거 (raw_result) | |
| step.status 변경 | L502-514 | 유지 | 스텝 상태는 fetcher 책임 |

**핵심 문제: `searched_queries` 갱신 정책 미정.**
설계서의 "fetcher read-only" 원칙을 엄격히 적용하면 searched_queries도 state에 쓰면 안 되지만,
이것은 중복 검색 방지의 핵심이다.

**권장**: "state read-only"의 범위를 "도구 결과 데이터(테이블, use_case, biz_term 등)만
read-only, 메타 관리(step.status, searched_queries, loop_guard, phase)는 갱신 허용"으로 재정의.

또한 `_fetch_use_case_related_metas` (L396-476)에서 `searched_queries`, `candidate_tables`,
`code_map`에 직접 쓰는 로직이 enrichment로 전환되면 raw_result 구조 안에 통합되어야 한다.

### 4-2. knowledge_interpreter.py -- 후처리 로직 전면 재구성 (Critical)

현행 interpreter의 후처리 흐름과 필요한 변경:

**(a) `_serialize_tool_results` (L242-287) -- 완전 교체**
현행: state 필드(explored_use_cases, code_map)에서 읽음.
변경: step.raw_result에서 읽어 렌더러 맵으로 디스패치. `serialize_tool_results_by_step()`으로 교체.

**(b) `_serialize_table_observations` (L290-299) -- 제거**
현행: candidate_tables에서 별도 섹션으로 직렬화.
변경: 스텝 블록에 통합되므로 별도 직렬화 불필요.

**(c) `_parse_batch_result` (L374-399) -- 구조 변경 필요**
현행: `data["selected"]`, `data["rejected"]` top-level 파싱.
변경: `data["interpretations"][i]["explored_tables"]` 등 스텝 하위에서 파싱.
BatchInterpretResult 클래스 구조도 변경 필요.

**(d) Phase 5 (L136-178) -- 판정 수집 방식 변경**
현행: `batch_result.selected`, `batch_result.rejected`에서 직접 구성.
변경: interpretations 순회하며 각 스텝의 explored_tables 등에서 수집.

**(e) 신규: interpreter가 state 최초 적재**
현행: fetcher가 이미 적재한 상태에서 interpreter가 마킹만.
변경: interpreter가 raw_result 파싱 후 `reason.explored_tables`, `reason.explored_use_cases`,
`reason.explored_biz_terms`, `reason.explored_biz_manuals`, `reason.code_map`에 최초 적재.

**(f) 신규: LLM fallback 시 적재 정책 필요**
현행 `_interpret_batch_fallback`(L402-421)은 insight만 기록.
새 구조에서는 fallback 시에도 raw_result의 테이블/use_case를 PENDING 상태로 적재해야
sql_generator가 참조 가능. **설계서에 미기술.**

**(g) 신규: raw_result = None 클리어**
모든 DONE 스텝의 step.raw_result = None 설정 로직 추가.

### 4-3. sql_generator.py -- explored_tables 소비 패턴 (Info)

현행 sql_generator의 테이블 소비 패턴 (L260-267):
```python
active_tables = [
    ct for ct in reason.candidate_tables
    if ct.selection_status != SelectionStatus.REJECTED
]
```
네이밍 변경(`explored_tables`) 외에 구조적 변화 없음. TableEntry가 CandidateTable과
동일한 필드를 유지하므로 소비 패턴은 호환.

### 4-4. readiness_gate.py -- 네이밍 변경 + 날짜 분포 fallback (Critical)

**(a) 네이밍 변경 필요 개소:**
- `_set_failure_context` L155: `len(reason.candidate_tables)`
- `_collect_stats` L202: `len(reason.candidate_tables)`

**(b) Phase 2 삭제 후 get_date_distribution fallback 부재:**
현행에서는 `_observe_all_date_distributions`가 모든 테이블의 key_date_columns에 대해
자동으로 날짜 분포를 조회. 새 구조에서는 플래너가 누락하면 ObservedDateColumn이 비어 있는
상태로 SQL이 생성될 수 있다.

**영향 범위:**
- sql_generator `_format_table_details`(L123-127): 날짜 범위/패턴 정보 없이 SQL 생성
- sql_validator: 시간 조건 검증 시 실제 데이터 범위 불명
- 사용자가 "이번 달" 같은 시간 조건을 요청했는데 테이블 날짜 범위가 미확인인 상태로 생성된 SQL은 빈 결과 가능

**권장**: 두 가지 중 하나:
1. readiness_gate에서 "SELECTED 테이블에 key_date_columns가 있지만 observed_date_columns가 비어 있으면
   get_date_distribution 스텝을 자동 추가하고 EXPLORE 반환" 로직 추가.
2. search_table_meta enrichment에 날짜 분포도 포함 (단, 설계서 SS3.2와 상충).

### 4-5. recovery_agent.py -- execution_plan 교체 시 raw_result (Info)

설계서 SS3.1에서 이미 언급.
현행 파이프라인 흐름(fetcher -> interpreter -> readiness_gate -> recovery_agent)에서
interpreter가 반드시 거쳐 raw_result = None이 보장되므로 문제 없음.

### 4-6. insight_builder.py -- 6개소 네이밍 변경 필요 (Warning)

`_get_attr_or_key(reason, "candidate_tables", [])`를 `"explored_tables"`로 변경 필요:
- `_build_tables_used` (L115)
- `_build_tables_candidate` (L142)
- `_build_tables_rejected` (L167)
- `_build_sql_summary` (L190)
- `_build_references` (L265)
- `_build_caveats` (L403)

SS10에 포함되어 있으나 구체적 변경 개소 목록은 없다.

### 4-7. knowledge_interpreter_system.txt -- 프롬프트 전면 재작성 필요 (Critical)

현행 프롬프트 출력 형식(L110-149):
```json
{
  "interpretations": [...],
  "selected": [...],
  "rejected": [...],
  "relevant_use_cases": [...]
}
```

설계서 SS4.2 새 형식:
```json
{
  "interpretations": [
    {
      "explored_tables": [...],
      "explored_use_cases": [...],
      "explored_biz_terms": [...],
      "explored_biz_manuals": [...]
    }
  ]
}
```

프롬프트 전면 재작성 + 예시 2개 갱신 필요.
폐쇄망 LLM(Solar Pro 2 70B)에서는 출력 형식이 복잡해지면 JSON 파싱 실패율 상승 우려.
관찰 도구에서도 빈 배열 명시 등 일관된 구조 권장.


---

## 5. 종합 평가

### 반영 완성도

21개 결정사항 중 **18개 정확히 반영**, **2개 부분적 반영**, **1개 상세 미기술**.
전체적으로 높은 완성도.

### 이슈 등급별 요약

**Critical (4건)**

| # | 이슈 | 위치 | 영향 |
|---|------|------|------|
| C1 | searched_queries의 state 쓰기 정책 미정 | SS3.4, fetcher L519 | fetcher read-only 원칙 충돌 |
| C2 | interpreter LLM fallback 시 테이블 적재 정책 미기술 | SS3.6, interpreter L402 | LLM 실패 시 테이블 소실, sql_generator 참조 불가 |
| C3 | Phase 2 삭제 후 get_date_distribution fallback 부재 | SS3.3, readiness_gate | 시간 조건 없이 SQL 생성, 빈 결과 위험 |
| C4 | 프롬프트 예시 갱신 범위 미명시 | SS9, interpreter_system.txt | 프롬프트-파서 불일치, 폐쇄망 LLM JSON 파싱 실패 위험 |

**Warning (8건)**

| # | 이슈 | 위치 |
|---|------|------|
| W1 | seen_tables와 searched_queries 관계 불명확 | SS3.4 |
| W2 | is_inferred 제거 시 프롬프트 정리 범위 누락 | SS8.5, SS10 |
| W3 | 현행 프롬프트 예시 2개 갱신 누락 | SS9.3 |
| W4 | search_use_cases enrichment PK 정보 포함 여부 모호 | SS3.2, SS5.1 |
| W5 | Phase 2 삭제 vs enrichment 샘플 자동 조회 차이 미명시 | SS3.2, SS3.3 |
| W6 | SS10에 reasoning_preparer.py, confidence_scorer.py 누락 | SS10 |
| W7 | code_map 적재 경로가 "판정 후 적재" 흐름 예외로서 미기술 | SS3.6 |
| W8 | insight_builder.py 6개소 구체적 변경 개소 미명시 | SS10 |

**Info (3건)**

| # | 이슈 | 위치 |
|---|------|------|
| I1 | ObservedDateColumn.recent_values raw_result 구조 미상세 | SS3.2, SS8.3 |
| I2 | re_evaluate_table 도구 상세 미기술 | SS3.5 |
| I3 | recovery_agent raw_result 소멸 -- 문제 없음 확인 | SS3.1 |

### 권장 조치 (우선순위순)

1. **(C1)** SS3.4에 "state read-only"의 범위 재정의: "도구 결과 데이터만 read-only, 스텝 메타(step.status, searched_queries, loop_guard, phase)는 갱신 허용"
2. **(C2)** SS3.6에 interpreter fallback 적재 정책 추가: "LLM 실패 시 raw_result의 테이블은 PENDING 상태로 explored_tables에 적재, use_case는 미판정으로 적재"
3. **(C3)** SS3.3 또는 readiness_gate 섹션에 날짜 분포 fallback 추가: "SELECTED 테이블에 key_date_columns 존재 + observed_date_columns 비어있음 -> EXPLORE로 전환하며 get_date_distribution 스텝 자동 추가"
4. **(C4)** SS9에 프롬프트 갱신 상세 범위 명시: 출력 형식 템플릿 + 예시 2개 + is_inferred 참조 제거
5. **(W6)** SS10에 reasoning_preparer.py, reasoning_preparer_system.txt, confidence_scorer.py 추가
