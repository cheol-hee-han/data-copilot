# 영향도 분석 Part 2: Fetcher + Interpreter 핵심 로직

**일자**: 2026-04-04  
**대상 설계문서**: `docs/working/tool-result-renderer-design.md`  
**분석 대상 파일**:
- `src/agents/nodes/reason/knowledge_fetcher.py`
- `src/agents/nodes/reason/knowledge_interpreter.py`

---

## 점검 1: knowledge_fetcher.py -- state 직접 쓰기 제거

### 1. state에 직접 쓰는 모든 위치

#### 1-A. candidate_tables에 append하는 곳

```
[_run_step:119]
  현재 동작: _extract_tables(step, result) 후 candidate_tables.extend(new_tables)
  설계 후 변경: 제거. step.raw_result에 저장하고, interpreter가 판정 후 explored_tables에 적재
  주의점: _extract_tables는 search_table_meta 전용 rule-based 로직. 
         CandidateTable.from_meta + PK 기준 날짜 컬럼 식별 등 중요한 전처리가 포함됨.
         이 전처리를 fetcher에 남길지 interpreter로 옮길지 결정 필요.
         설계상 raw_result는 dict|list|None이므로, CandidateTable 객체를 직접 담을 수 없음.
         원시 meta dict를 raw_result에 저장하고, interpreter에서 CandidateTable/TableEntry 변환 수행.

[_fetch_use_case_related_metas:436]
  현재 동작: use_case SQL에서 추출한 테이블명으로 search_table_meta 호출 후 
            CandidateTable.from_meta(m) -> candidate_tables.append(ct)
  설계 후 변경: 제거. enrichment 결과를 step.raw_result의 tables 키에 포함.
            {use_cases: [...], tables: [...], codes: {...}} 구조로 통합.
  주의점: 현재 execution_plan에 스텝 추가 없이 암묵적으로 동작. 
         설계는 이를 enrichment로 전환하여 step.raw_result 내에 포함시킴.
```

#### 1-B. explored_use_cases에 넣는 곳

```
[_store_use_cases:221-225]
  현재 동작: search_use_cases 결과에 id/검색쿼리 부여 후 explored_use_cases.extend(result)
  설계 후 변경: 제거. step.raw_result.use_cases에 저장. 
            interpreter가 판정(SELECTED/REJECTED) 후 explored_use_cases에 적재.
  주의점: 현재 id 채번 로직(uc_001, uc_002...)이 fetcher에 있음.
         id 채번을 fetcher의 raw_result 저장 시에 수행할지, interpreter 적재 시에 수행할지 결정 필요.
         fetcher에서 수행하는 것이 자연스러움 (raw_result에 id 포함 상태로 전달).
```

#### 1-C. explored_biz_terms에 넣는 곳

```
[_store_biz_terms:259-275]
  현재 동작: search_biz_terms 결과를 BizTermEntry로 변환, id 채번 후 explored_biz_terms.append
  설계 후 변경: 제거. step.raw_result에 원시 결과 저장.
            interpreter가 판정(SELECTED/REJECTED) 후 explored_biz_terms에 적재.
  주의점: BizTermEntry 변환(term, definition, synonyms, related_tables 매핑)을 
         interpreter로 이관해야 함. id 채번도 interpreter에서 수행.
```

#### 1-D. explored_biz_manuals에 넣는 곳

```
[_store_biz_manuals:243-256]
  현재 동작: search_manual 결과를 BizManualEntry로 변환, id 채번 후 explored_biz_manuals.append
  설계 후 변경: 제거. step.raw_result에 원시 결과 저장.
            interpreter가 판정(SELECTED/REJECTED) 후 explored_biz_manuals에 적재.
  주의점: 1-C와 동일 패턴. BizManualEntry 변환을 interpreter로 이관.
```

#### 1-E. code_map에 넣는 곳

```
[_store_code_meta:228-240]
  현재 동작: search_code_meta 결과를 CodeMeta로 변환, 컬럼 단위 중복 방지 후 code_map[col] = CodeMeta(...)
  설계 후 변경: 제거. step.raw_result에 원시 결과 저장.
            interpreter가 판정 없이 code_map에 그대로 적재 (설계 S3.6: "code_map은 판정 없이 적재").
  주의점: 중복 방지 로직(col not in code_map)을 interpreter 적재 시에도 유지 필요.

[_fetch_use_case_related_metas:458-467]
  현재 동작: use_case에서 추출한 코드 컬럼으로 search_code_meta 호출 후 code_map에 직접 적재
  설계 후 변경: 제거. enrichment 결과를 step.raw_result.codes에 포함.
  주의점: 1-A의 enrichment 통합과 동일 맥락.
```

#### 1-F. observed_date_columns 관련

```
[_store_date_distribution:320-342]
  현재 동작: get_date_distribution 결과를 ObservedDateColumn으로 변환, 
            table.observed_date_columns.append
  설계 후 변경: 제거. step.raw_result에 원시 날짜 리스트 저장.
            interpreter가 해당 테이블의 explored_tables 엔트리에 보조 정보로 반영.
  주의점: detect_date_pattern 호출이 fetcher에 있음. 
         이를 raw_result 저장 시 수행(날짜 패턴을 raw_result에 포함)할지,
         interpreter/렌더러에서 수행할지 결정 필요.
         설계 S5.7에서 렌더러가 "날짜 패턴"을 표시하므로, 
         fetcher에서 detect_date_pattern을 수행하여 raw_result에 포함하는 것이 적절.
         추가로 설계 S8.3의 recent_values(최근 10건)도 fetcher에서 계산하여 raw_result에 포함.

[_observe_all_date_distributions:549-596]  **Phase 2 삭제 대상**
  현재 동작: 모든 candidate_tables의 key_date_columns를 순회하며 get_date_distribution 호출,
            table.observed_date_columns.append
  설계 후 변경: 함수 전체 삭제. 플래너가 get_date_distribution 스텝을 명시적으로 계획.
  주의점: 아래 Phase 2 삭제 항목에서 상세 기술.
```

#### 1-G. sample_rows 관련

```
[_store_sample_rows:305-317]
  현재 동작: get_sample_rows 결과를 table.sample_rows에 저장
  설계 후 변경: 제거. step.raw_result에 원시 결과 저장.
            interpreter가 해당 테이블에 보조 정보로 반영.
  주의점: 현재 table.sample_rows is None 가드가 있어 Phase 2와 연계됨.
         Phase 2 삭제 후 이 가드도 불필요.

[_sample_unsampled_tables:598-626]  **Phase 2 삭제 대상**
  현재 동작: sample_rows가 None인 모든 candidate_tables에 대해 get_sample_rows 호출
  설계 후 변경: 함수 전체 삭제. search_table_meta enrichment로 대체.
  주의점: 아래 Phase 2 삭제 항목에서 상세 기술.
```

#### 1-H. 기타 state 필드 쓰기

```
[_store_column_values:345-363]
  현재 동작: search_column_values 결과를 ColumnInfo.discovered_values에 병합
  설계 후 변경: 제거. step.raw_result에 원시 결과 저장.
            interpreter가 해당 테이블 컬럼에 보조 정보로 반영.

[_store_column_profile:366-388]
  현재 동작: get_column_profile 결과를 ColumnInfo 통계 필드에 저장
  설계 후 변경: 제거. step.raw_result에 원시 결과 저장.
            interpreter가 해당 테이블 컬럼에 보조 정보로 반영.

[_run_step:111] step.status = StepStatus.DONE
  설계 후 변경: 유지 (갱신 허용 범위)

[_run_step:115] searched_queries.append(step.input)
  설계 후 변경: 유지 (갱신 허용 범위)

[knowledge_fetcher_node:491] reason.phase = Phase.EXPLORING
  설계 후 변경: 유지 (갱신 허용 범위)

[knowledge_fetcher_node:516-524] reason 필드 일괄 갱신 (searched_queries 등)
  설계 후 변경: searched_queries, loop_guard만 갱신. 
            explored_use_cases/code_map/explored_biz_manuals/explored_biz_terms 갱신 제거.
```

---

### 2. 설계에서 "갱신 허용"으로 남는 것

| 필드 | 위치 (라인) | 사유 |
|------|------------|------|
| `step.status` | 111, 71, 151 | 스텝 실행 진행 메타 |
| `step.insight` | 72, 84, 152 | 스킵/실패 사유 기록 |
| `step.raw_result` | **신규 추가** | 도구 결과 임시 저장 (설계 핵심) |
| `searched_queries` | 115, 425, 449 | 중복 방지 메타 |
| `loop_guard.total_tool_calls` | 499, 514, 517 | 도구 호출 카운팅 |
| `reason.phase` | 491 | 파이프라인 상태 |

---

### 3. 설계에서 "read-only"로 전환되어 제거해야 하는 것

| 필드 | 현재 쓰기 위치 (라인) | 제거 대상 함수/라인 |
|------|---------------------|-------------------|
| `candidate_tables` | 119, 317, 338, 363, 386, 436 | `_extract_tables` 반환 -> extend, `_store_*` 함수들, `_fetch_use_case_related_metas` |
| `explored_use_cases` | 225 | `_store_use_cases` |
| `explored_biz_terms` | 268 | `_store_biz_terms` |
| `explored_biz_manuals` | 251 | `_store_biz_manuals` |
| `code_map` | 236, 461 | `_store_code_meta`, `_fetch_use_case_related_metas` |
| `observed_date_columns` (테이블 내) | 338, 584 | `_store_date_distribution`, `_observe_all_date_distributions` |
| `sample_rows` (테이블 내) | 317, 613 | `_store_sample_rows`, `_sample_unsampled_tables` |
| `discovered_values` (컬럼 내) | 363 | `_store_column_values` |
| `ColumnInfo 통계` (컬럼 내) | 383-388 | `_store_column_profile` |

**노드 반환 dict에서도 제거**:
- 라인 519-523: `reason.explored_use_cases`, `reason.code_map`, `reason.explored_biz_manuals`, `reason.explored_biz_terms` 갱신 제거
- 라인 530: `reason.candidate_tables = candidate_tables` 제거 (Phase 2 삭제 후 불필요)

---

### 4. Phase 2 삭제 대상 함수

#### `_sample_unsampled_tables` (라인 598-626)

```
[_sample_unsampled_tables:598-626]
  정의 위치: knowledge_fetcher.py 라인 598
  호출 위치: knowledge_fetcher_node 라인 528
  호출 문맥: Phase 1 루프 완료 후 Phase 2 블록에서 직접 호출
  
  현재 동작: 
    - candidate_tables 순회, sample_rows is None인 테이블에 get_sample_rows 호출
    - 결과를 table.sample_rows에 직접 저장
  
  설계 후 변경: 함수 전체 삭제
  대체 경로: search_table_meta enrichment에서 샘플 조회 수행 (raw_result.tables에 포함)
  
  주의점:
    - 현재 Phase 1의 _store_sample_rows와 Phase 2의 이 함수가 이중 안전장치 역할
    - 삭제 후 search_table_meta enrichment가 누락되면 샘플 없이 interpreter 진행
    - readiness_gate + recovery 사이클이 안전망 역할 (설계 S3.3)
```

#### `_observe_all_date_distributions` (라인 549-596)

```
[_observe_all_date_distributions:549-596]
  정의 위치: knowledge_fetcher.py 라인 549
  호출 위치: knowledge_fetcher_node 라인 527
  호출 문맥: Phase 1 루프 완료 후 Phase 2 블록에서 직접 호출 (_sample_unsampled_tables 직전)
  
  현재 동작:
    - 모든 candidate_tables 순회
    - 각 테이블의 key_date_columns(1순위) 또는 inferred_key_date_column(2순위) 사용
    - Phase 1에서 이미 관찰된 컬럼은 스킵 (already_observed 세트)
    - get_date_distribution 호출 후 ObservedDateColumn 생성, table.observed_date_columns.append
  
  설계 후 변경: 함수 전체 삭제
  대체 경로: 플래너가 get_date_distribution 스텝을 명시적으로 계획에 포함
  
  주의점:
    - 현재 key_date_columns 식별은 _extract_tables(rule-based)에서 수행
    - 설계 전환 후 플래너가 어떤 테이블.컬럼에 대해 get_date_distribution을 
      계획해야 하는지 판단해야 함
    - 플래너가 빠뜨린 경우: readiness_gate에서 "날짜 분포 미확인" 감지 -> 
      recovery_agent가 get_date_distribution 스텝 추가
    - inferred_key_date_column(LLM fallback) 경로도 고려 필요
```

#### Phase 2 삭제 시 상위 흐름 변경

```
[knowledge_fetcher_node:526-531]
  현행:
    # Phase 2
    await _observe_all_date_distributions(candidate_tables)
    await _sample_unsampled_tables(candidate_tables)
    reason.candidate_tables = candidate_tables
    return {"reason": reason}
  
  변경 후:
    # Phase 2 삭제 -- 암묵적 자동 수집 없음
    return {"reason": reason}
  
  주의점: Phase 2 관련 상수/유틸도 정리 대상
    - DATE_SUFFIXES (라인 541): _extract_tables에서도 사용 -> 유지
    - KOREAN_DATE_KEYWORDS (라인 544): _identify_key_date_by_alt_name에서 사용 -> 유지
    - 위 두 상수는 _extract_tables 계열에서 사용하므로 Phase 2 삭제와 무관하게 유지
```

---

### 5. Enrichment 대상 함수 -- `_fetch_use_case_related_metas`

```
[_fetch_use_case_related_metas:396-476]
  현재 동작:
    1. extract_hints_from_use_cases(use_cases) -> HintResult(source_tables, code_columns)
    2. source_tables -> search_table_meta 병렬 호출 -> CandidateTable.from_meta -> candidate_tables.append
    3. code_columns -> search_code_meta 병렬 호출 -> CodeMeta -> code_map[col] = CodeMeta(...)
    4. 둘 다 searched_queries에 추가하여 중복 방지
    5. tool_calls 카운트에 미포함 (스텝 단위 카운팅이므로)
  
  설계 후 변경 (step.raw_result 전환):
    - 함수 자체는 유지하되 반환 구조 변경
    - candidate_tables.append -> 결과를 dict 리스트로 수집하여 반환
    - code_map 직접 적재 -> 코드 메타 dict 리스트로 수집하여 반환
    - 호출부(_run_step 또는 _apply_tool_result)에서 반환값을 
      step.raw_result = {use_cases: [...], tables: [...], codes: {...}} 형태로 조립
  
  시그니처 변경:
    현행: async def _fetch_use_case_related_metas(
            use_cases, searched_queries, candidate_tables, code_map) -> None
    변경: async def _fetch_use_case_related_metas(
            use_cases, searched_queries) -> dict
            반환: {"tables": [meta_dict, ...], "codes": [code_dict, ...]}
  
  주의점:
    - searched_queries 갱신은 유지 (갱신 허용 범위)
    - seen_tables(설계 S3.4) 도입 시 중복 방지 로직에 이전 라운드 테이블도 포함
    - 현재 asyncio.gather로 병렬 호출 -> 유지
    - return_exceptions=True 패턴 -> 유지
    - extract_hints_from_use_cases는 tools.py에 정의 -> 변경 없음
```

---

## 점검 2: knowledge_interpreter.py -- 파싱/적재 로직

### 1. 현재 직렬화 함수 `_serialize_tool_results`

```
[_serialize_tool_results:242-287]
  현재 동작:
    입력: explored_use_cases(list[dict]), code_map(dict[str, CodeMeta]), execution_plan(list)
    
    조립 순서:
    1. execution_plan에서 DONE 스텝 추출 -> "실행된 도구 목록" 블록 (tool, input, purpose)
    2. explored_use_cases -> json.dumps로 raw JSON 블록
    3. code_map -> "코드 메타" 블록 (컬럼별 코드값 나열)
    
    누락: explored_biz_terms, explored_biz_manuals가 직렬화에서 빠져 있음 (설계 S1.1 문제 2)
    
  설계 후 변경: 
    함수 전체 교체 -> serialize_tool_results_by_step(execution_plan)
    - state 필드를 인자로 받지 않음
    - step.raw_result에서 모든 데이터를 읽음
    - 도구별 렌더러(_TOOL_RENDERERS 맵)로 디스패치
    - purpose + result + 판단 가이드 3단 구조
    
  주의점:
    - 현행의 _serialize_table_observations (라인 290-299)도 제거 대상
      (테이블 관찰 데이터가 step.raw_result에 포함되므로)
    - _serialize_unresolved_items (라인 221-239)는 유지 가능 
      (knowledge_items 기반이므로 step.raw_result와 무관)
```

### 2. 현재 파싱 함수 `_parse_batch_result`

```
[_parse_batch_result:374-399]
  현재 동작:
    입력: LLM 응답 JSON (dict)
    
    추출 필드:
    - data["interpretations"] -> 각 interp에서:
      - interp["knowledge_updates"] -> KnowledgeItem 리스트로 변환
      - interp["new_tables"] -> dict 리스트 수집
    - data["selected"] -> dict 리스트 (top-level)
    - data["rejected"] -> dict 리스트 (top-level)
    - data["relevant_use_cases"] -> dict 리스트 (top-level)
    
    반환: BatchInterpretResult
    
  설계 후 변경:
    - top-level selected/rejected/relevant_use_cases 제거
    - interpretations 하위에 explored_tables, explored_use_cases, 
      explored_biz_terms, explored_biz_manuals 배열 추가
    - 각 interp에서 개별적으로 판정 결과 추출
    - new_tables 필드는 제거 (explored_tables로 통합)
    
  주의점:
    - 파싱 로직이 새 출력 형식(S4.2)에 맞게 전면 재작성 필요
    - 판정 status 값이 "SELECTED"/"REJECTED" 문자열 -> SelectionStatus Enum 변환 추가
    - 관찰 도구(get_sample_rows 등)는 판정 배열 없이 insight + knowledge_updates만
```

### 3. 현재 마킹 함수 -- selected/rejected 결과를 state에 반영

```
[knowledge_interpreter_node:137-178]
  현재 동작:
    1. batch_result.selected -> selected_map {table_name: reason}
    2. batch_result.rejected -> rejected_map {table_name: reason}
    3. candidate_tables 순회:
       - selected_map에 있으면 ct.selection_status = SELECTED, ct.selection_reason 설정
       - rejected_map에 있으면 ct.selection_status = REJECTED, ct.selection_reason 설정
    4. rejected 테이블의 KnowledgeItem 제거 (knowledge_items 필터링)
    5. 트래커에 비교 판정 기록
    
  설계 후 변경:
    - top-level selected/rejected 대신 interpretations 하위에서 추출
    - 테이블뿐 아니라 use_case, biz_terms, biz_manuals도 판정 적용
    - **적재 로직 신규**: 
      step.raw_result에서 원시 데이터를 읽어 판정 결과에 따라 state 필드에 적재
      (현행: 이미 적재된 데이터에 마킹만 / 변경: 적재 자체를 interpreter가 수행)
    - REJECTED도 state에 적재 (설계 S3.6: recovery_agent 재탐색 방지)
    - rejected 테이블의 KnowledgeItem 제거 로직은 유지
    
  주의점:
    - 적재 + 마킹이 결합되면서 함수 복잡도 증가
    - 도구별 적재 로직 분리 필요:
      * search_table_meta -> explored_tables에 TableEntry 적재 + 보조 정보(sample_rows) 매칭
      * search_use_cases -> explored_use_cases 적재 + enrichment hydration(tables -> TableEntry, codes -> code_map)
      * search_biz_terms -> explored_biz_terms 적재
      * search_manual -> explored_biz_manuals 적재
      * search_code_meta -> code_map 적재 (판정 없이)
      * get_sample_rows, get_date_distribution 등 -> 해당 테이블의 보조 정보로 매칭
    - raw_result = None 설정 (라인 없음, 신규): 적재 완료 후 모든 DONE 스텝에서 수행
```

### 4. BatchInterpretResult 클래스

```
[BatchInterpretResult:60-78]
  현재 필드:
    - interpretations: list[dict]          # 스텝별 해석 결과
    - knowledge_updates: list[KnowledgeItem]  # 지식 업데이트 (interpretations에서 추출)
    - new_tables: list[dict]               # LLM이 추론한 테이블 필드
    - selected: list[dict]                 # top-level 선택 테이블
    - rejected: list[dict]                 # top-level 탈락 테이블
    - relevant_use_cases: list[dict]       # 관련 use_case
    
  설계 후 변경:
    - selected, rejected 제거 (interpretations 하위로 이동)
    - relevant_use_cases 제거 (interpretations 하위로 이동)
    - new_tables 제거 (explored_tables로 통합)
    - 추가 필드 (스텝별 판정 결과를 구조화):
      * explored_tables: list[dict]        # 모든 스텝에서 판정된 테이블 통합
      * explored_use_cases: list[dict]     # 모든 스텝에서 판정된 use_case 통합
      * explored_biz_terms: list[dict]     # 판정된 용어
      * explored_biz_manuals: list[dict]   # 판정된 매뉴얼
    
  주의점:
    - 또는 interpretations 자체를 순회하며 적재하는 방식 (BatchInterpretResult를 얇게 유지)
    - _parse_batch_result에서 interpretations 하위의 판정 결과를 추출하는 로직 필요
    - 방식 A: _parse_batch_result에서 통합 추출 -> BatchInterpretResult 필드에 저장
    - 방식 B: knowledge_interpreter_node에서 interpretations 순회하며 직접 적재
    - 방식 B가 설계 의도(스텝별 판정)에 더 부합. BatchInterpretResult는 최소화.
```

### 5. `_interpret_batch_fallback`

```
[_interpret_batch_fallback:402-421]
  현재 동작:
    - LLM 호출 실패 시 실행
    - 모든 DONE 스텝에 "LLM 해석 실패" insight 기록
    - BatchInterpretResult(interpretations=interpretations)만 반환
    - 판정 없음 (selected/rejected/relevant_use_cases 비어 있음)
    
  설계 후 변경:
    - 설계 S3.7: LLM 연결 실패는 사용자에게 알리고 그래프 종료
    - 즉, _interpret_batch_fallback 자체가 불필요해질 수 있음
    - 토큰 초과에 의한 Level 0 -> Level 1 분할은 별도 처리 (S6)
    
  주의점:
    - 설계에서 "모든 노드 공통 fallback"으로 처리한다고 명시
    - 현행의 rule-based fallback(실패 사실만 기록하고 계속 진행) 패턴은 제거
    - 단, 토큰 초과 감지 시 Level 1 분할로 재시도하는 로직은 새로 추가
    - 파싱 실패(ParseError)와 연결 실패를 분리해야 함:
      * 연결 실패 -> 그래프 종료
      * 파싱 실패 -> llm_call_with_parse_retry가 재시도 (기존 메커니즘)
      * 토큰 초과 -> Level 1 분할 재시도 (신규)
    - 현행 except (ParseError, Exception) 블록을 세분화 필요
```

---

## 종합 영향도 요약

### Fetcher 변경 범위

| 구분 | 대상 | 변경 유형 |
|------|------|----------|
| **삭제** | `_observe_all_date_distributions` (549-596) | 함수 전체 삭제 |
| **삭제** | `_sample_unsampled_tables` (598-626) | 함수 전체 삭제 |
| **삭제** | `_apply_tool_result` (175-209) | 함수 전체 삭제 |
| **삭제** | `_store_use_cases` (215-225) | 함수 전체 삭제 |
| **삭제** | `_store_code_meta` (228-240) | 함수 전체 삭제 |
| **삭제** | `_store_biz_manuals` (243-256) | 함수 전체 삭제 |
| **삭제** | `_store_biz_terms` (259-275) | 함수 전체 삭제 |
| **삭제** | `_store_sample_rows` (305-317) | 함수 전체 삭제 |
| **삭제** | `_store_date_distribution` (320-342) | 함수 전체 삭제 |
| **삭제** | `_store_column_values` (345-363) | 함수 전체 삭제 |
| **삭제** | `_store_column_profile` (366-388) | 함수 전체 삭제 |
| **삭제** | `_find_table` (281-291) | _store_* 삭제 시 함께 삭제 |
| **삭제** | `_find_column` (294-302) | _store_* 삭제 시 함께 삭제 |
| **대폭 수정** | `_fetch_use_case_related_metas` (396-476) | 반환 구조 변경 (None -> dict) |
| **대폭 수정** | `_run_step` (89-169) | _apply_tool_result 호출 제거, step.raw_result 저장 추가 |
| **대폭 수정** | `knowledge_fetcher_node` (484-532) | Phase 2 호출 제거, state read-only 필드 갱신 제거, seen_tables 도입 |
| **유지** | `_should_skip_step` (60-86) | 유지 (searched_queries + sample dedup) |
| **유지** | `_extract_tables` (721-751) | 유지 (rule-based 전처리, raw_result 조립에 활용) |
| **유지** | 날짜 컬럼 식별 유틸 (634-718) | 유지 |

### Interpreter 변경 범위

| 구분 | 대상 | 변경 유형 |
|------|------|----------|
| **전면 교체** | `_serialize_tool_results` (242-287) | -> `serialize_tool_results_by_step` + 9개 렌더러 |
| **전면 교체** | `_serialize_table_observations` (290-299) | 제거 (렌더러에 통합) |
| **전면 교체** | `_parse_batch_result` (374-399) | 새 출력 형식(S4.2)에 맞게 재작성 |
| **전면 교체** | `BatchInterpretResult` (60-78) | 필드 구조 변경 |
| **대폭 수정** | `knowledge_interpreter_node` (85-189) | 적재 로직 추가, 마킹 로직 통합, raw_result=None |
| **대폭 수정** | 마킹 블록 (137-178) | top-level -> interpretations 하위, 범위 확장 |
| **제거/변경** | `_interpret_batch_fallback` (402-421) | 그래프 종료 정책으로 전환 |
| **수정** | `_interpret_batch` (307-371) | Level 0/1 분기 추가, 입력 변경 |
| **유지** | `_merge_llm_inferred_fields` (478-497) | 유지 (LLM 추론 필드 병합) |
| **유지** | `_dedup_knowledge_items` (505-519) | 유지 |
| **유지** | `_promote_sampled_confidence` (523-542) | 유지 |
| **유지** | `_extract_time_slot` (197-218) | 유지 |
| **유지** | `_serialize_unresolved_items` (221-239) | 유지 |
| **신규** | 9개 도구별 렌더러 함수 | `_render_use_cases` 등 |
| **신규** | `serialize_tool_results_by_step` | 메인 직렬화 함수 |
| **신규** | 도구별 적재 함수 | 판정 결과에 따른 state 적재 |
| **신규** | Level 0/1 분기 로직 | 토큰 추정 + 분할 호출 |

### 핵심 위험 포인트

1. **_extract_tables의 rule-based 전처리 위치 결정**: CandidateTable(TableEntry) 생성, PK 기준 날짜 컬럼 식별이 fetcher에 남는지 interpreter로 이동하는지에 따라 raw_result 구조가 달라짐. 설계는 raw_result를 dict|list|None으로 제한하므로, 원시 meta dict를 저장하고 interpreter에서 변환하는 것이 정합적.

2. **enrichment 반환 구조 전환**: `_fetch_use_case_related_metas`가 None 반환에서 dict 반환으로 변경되면서 호출부(`_run_step` 또는 전용 enrichment 함수)의 조립 로직이 필요.

3. **interpreter 적재 복잡도**: 현행은 fetcher가 적재, interpreter가 마킹만 수행하는 단순 구조. 변경 후 interpreter가 적재 + 마킹 + 보조 정보 매칭을 모두 수행해야 하므로 함수 분리 설계가 중요.

4. **fallback 정책 전환**: rule-based fallback 제거 시 LLM 실패 = 그래프 종료. 폐쇄망 환경에서 LLM 불안정 시 사용자 경험 영향. Level 1 분할이 완성되어야 안전망 확보.
