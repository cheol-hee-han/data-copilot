# Recovery Agent 프롬프트 AS-IS / TO-BE 비교

> 기준 시점: Round 3 recovery_agent 진입 (readiness 47%, 2차 recovery)
> 실제 trace: `trace_reasoning_20260408_anonymous_session-1775579942931_a8a36f0e696b`
> 원래 질의: "연령대별 남자 평균 여신 잔액 알려줘"

---

## AS-IS: 현재 LLM이 받는 프롬프트 (변수 섹션만)

아래는 `_build_prompt()`가 치환하는 7개 플레이스홀더의 **실제 렌더링 결과**이다.
프롬프트 고정 부분(역할, 도구 목록, 응답 형식, 지시, 예시)은 생략한다.

```
## 진입 경로
readiness_gate에서 진입: 초기 탐색이 불충분합니다.
실패 유형: FailureType.TERM_UNRESOLVABLE
상세 사유:
SQL 생성에 필요한 정보가 부족합니다.
- 확정된 지식: 2/3건
- 후보 테이블: 3개 (탐색 27개)
- 확신도 47%로 생성 기준 미달

## 현재 확인된 지식
[] measure:평균 여신 잔액 — PROBABLE (AVG(LN_BAL_AMT), 활용사례)
[] table:고객기본정보 — CONFIRMED (TB_ADW_CSC101M (현재 데이터), 테이블메타)

## 아직 확인되지 않은 항목
[] filter:성별=['남성'] — CANDIDATE

## 도구 실행 이력
[스텝 1] ✓ search_table_meta("고객 마스터 정보, page=1")
  관련성: SELECTED 2건(TB_ADW_CSC101M, TB_ADW_CSC102H), REJECTED 8건
  발견: 고객 정보 테이블 TB_ADW_CSC101M(현재) 및 TB_ADW_CSC102H(이력)에서 성별(GNDR_DCD)과 연령대(AGE_GRP_CD) 컬럼을 확인했습니다. 여신 잔액 정보를 결합하기 위한 고객 기준 테이블로 적합합니다.
[스텝 2] ✓ search_table_meta("고객 인적사항, page=1")
  관련성: SELECTED 1건(TB_ADW_LNB301M), REJECTED 10건, PENDING 6건
  발견: 추가 인적사항 테이블들을 탐색했으나, 고객의 성별 및 연령대는 이미 Step 1에서 식별된 TB_ADW_CSC101M이 가장 표준적입니다. 나머지 테이블들은 본 질의(연령대/성별별 여신 잔액)와 직접적인 관계가 부족합니다.
[스텝 3] ✓ search_biz_terms("성별")

## 탐색된 테이블
- TB_ADW_LNB301M (SELECTED): DATA TABLE
  컬럼: LN_NO, STD_DT, EDPS_CSN, LN_EXC_AMT, LN_BAL_AMT, LN_DT, MTRTY_DT, APLY_RT, LN_DCD, LN_STCD (+6)
- TB_ADW_CRD442P (PENDING): CRD카드일별잔액스냅샷
  컬럼: CRD_NO, STD_DT, BAL_AMT, EVAL_AMT, DCD, RGST_DT, INS_DTM, UPD_DTM
- TB_ADW_DEA237P (PENDING): DEP일별잔액스냅샷
  컬럼: ACN, BAL_DT, STD_DT, BAL_AMT, EVAL_AMT, DCD, RGST_DT, INS_DTM, UPD_DTM
- TB_ADW_GLB1302P (PENDING): FIN계정잔액. 당행 업무 데이터.
  컬럼: GL_ACCT_CD, BLNG_BRCD, STD_DT, BAL_AMT, EVAL_AMT, DCD, RGST_DT, INS_DTM, UPD_DTM
- TB_ADW_GLB1338H (PENDING): FIN계정잔액변경이력. 당행 업무 데이터.
  컬럼: GL_ACCT_CD, BLNG_BRCD, CHG_DT, SEQ, CHG_SEQ, CHG_RSN_DCD, BEF_VAL, AFT_VAL, CHG_USR_ID, INS_DTM (+1)
- TB_ADW_INS828M (PENDING): DATA TABLE
  컬럼: INS_NO, LOAN_SEQ, STD_DT, NM, DCD, CD, AMT, USE_YN, RGST_DT, RGST_USR_ID (+2)
- TB_ADW_LNA304L (PENDING): LON여신승인내역. 당행 업무 데이터.
  컬럼: LN_NO, APPR_SEQ, TR_DT, EXEC_DT, AMT, DCD, CHN_CD, RGST_DT, INS_DTM, UPD_DTM
- TB_ADW_CSC101M (SELECTED): CUS고객정보기본(현재)
  컬럼: EDPS_CSN, STD_DT, CSM, CUS_DCD, JOIN_DT, BLNG_BRCD, GNDR_DCD, AGE_GRP_CD, CUS_GRD_CD, TEL_NO (+3)
- TB_ADW_CSC102H (SELECTED): CUS고객마스터(이력) 테이블. 당행 CUS 업무 영역에서 관리하는 데이터로, 관련 업무 프로세스 수행 시 데이터가 적재된다. 갱신 주기는 일배
  컬럼: EDPS_CSN, STD_DT, CSM, CUS_DCD, RGST_DT, BLNG_BRCD, GNDR_DCD, AGE_GRP_CD, CUS_GRD_CD, CUS_ADR (+3)

## 이전 실패 기록 (이 경로들은 피하세요)
- [FailureType.TERM_UNRESOLVABLE] SQL 생성에 필요한 정보가 부족합니다.
- 확정된 지식: 1/2건
- 후보 테이블: 1개 (탐색 10개)
- 확신도 35%로 생성 기준 미달 (교훈: 여신 데이터와 고객 개인정보(성별, 연령대)는 물리적으로 분리되어 관리될 가능성이 높으며, 고객번호를 매개로 한 조인 테이블 탐색이 필수적입니다.)
- [FailureType.TERM_UNRESOLVABLE] SQL 생성에 필요한 정보가 부족합니다.
- 확정된 지식: 2/3건
- 후보 테이블: 3개 (탐색 27개)
- 확신도 47%로 생성 기준 미달

## 샘플 데이터 현황
- TB_ADW_LNB301M: 0행 (데이터 없음 또는 미조회)
- TB_ADW_CRD442P: 0행 (데이터 없음 또는 미조회)
- TB_ADW_DEA237P: 0행 (데이터 없음 또는 미조회)
- TB_ADW_GLB1302P: 0행 (데이터 없음 또는 미조회)
- TB_ADW_GLB1338H: 0행 (데이터 없음 또는 미조회)
- TB_ADW_INS828M: 0행 (데이터 없음 또는 미조회)
- TB_ADW_LNA304L: 0행 (데이터 없음 또는 미조회)
- TB_ADW_CSC101M: 0행 (데이터 없음 또는 미조회)
- TB_ADW_CSC102H: 0행 (데이터 없음 또는 미조회)
```

### AS-IS 문제점 분석

| # | 문제 | 영향 |
|---|------|------|
| 1 | **원래 질의 없음** — LLM이 "무엇을 위한 탐색인지" 직접 볼 수 없음 | 탐색 목적 모호 |
| 2 | **실패 사유 "확정된 지식: 2/3건"** — 뭐가 미해소인지 안 나옴 | LLM이 공백 파악 불가 |
| 3 | **`[] filter:성별=['남성'] — CANDIDATE`** — ID 없음, 근거 없음, 왜 CANDIDATE인지 불명 | 정보 단절 |
| 4 | **`[] measure:평균 여신 잔액 — PROBABLE (AVG(LN_BAL_AMT), 활용사례)`** — 어떤 활용사례? 어떤 테이블? | 근거 불투명 |
| 5 | **REJECTED 테이블 18건 완전 누락** — TB_ADW_LNB341P, CRD419M, DEP201P 등 왜 부적합했는지 모름 | 동일 테이블 재탐색 위험 |
| 6 | **컬럼 영문명만, 10개 잘림** — `LN_NO, STD_DT, EDPS_CSN, ...` 한글 설명 없음 | 컬럼 용도 추론 어려움 |
| 7 | **`0행 (데이터 없음 또는 미조회)`** — 모든 테이블이 미조회인데 "데이터 없음"과 구분 불가 | 샘플 조회 기회 상실 |
| 8 | **누적 인사이트 미전달** — Round 0~1의 발견사항(EDPS_CSN 조인, GNDR_DCD 발견 등)이 별도 요약 없음 | 전체 맥락 파편화 |
| 9 | **PENDING 테이블 5건 (카드/수신/보험/계정)** — 여신 질의와 무관한 테이블이 공간 차지 | 토큰 낭비 |

---

## TO-BE: 개선 설계 적용 후 LLM이 받게 될 프롬프트

동일 시점(Round 3, readiness 47%)의 데이터를 개선된 렌더링 로직으로 표현한다.

```
## 사용자 질의
"연령대별 남자 평균 여신 잔액 알려줘"

## 진입 경로
readiness_gate 진입 (Round 2)
실패 유형: TERM_UNRESOLVABLE
상세 사유:
- 확정된 지식: 2/3건
- 후보 테이블: 3개 (탐색 27개)
- 미해소 항목:
  · filter:성별=['남성'] (CANDIDATE) — 추정: GNDR_DCD 코드값 미확인
- 거부된 테이블:
  · TB_ADW_LNB341P: 여신 잔액 컬럼 없음. 승인금액(LN_APR_AMT)만 존재
  · TB_ADW_CRD419M: 카드 도메인 테이블로 여신(대출) 조회 목적에 부합하지 않음
  · TB_ADW_DEP201P: 수신(예금) 도메인 테이블로 여신 조회 목적에 부합하지 않음
- 확신도 47%로 생성 기준(60%) 미달

## 아직 해소되지 않은 항목

[미확인 항목]
[K2] filter:성별=['남성']
  상태: CANDIDATE
  추정값: GNDR_DCD

## 이전 실패 기록 (이 경로들은 피하세요)
- [TERM_UNRESOLVABLE] SQL 생성에 필요한 정보가 부족합니다 — 확정된 지식 1/2건, 확신도 35%
  교훈: 여신 데이터와 고객 개인정보(성별, 연령대)는 물리적으로 분리되어 관리될 가능성이 높으며, 고객번호를 매개로 한 조인 테이블 탐색이 필수적입니다.
- [TERM_UNRESOLVABLE] SQL 생성에 필요한 정보가 부족합니다 — 확정된 지식 2/3건, 확신도 47%

## 현재 라운드 탐색 결과
- [search_table_meta(고객 마스터 정보, page=1)] 고객 정보 테이블 TB_ADW_CSC101M(현재) 및 TB_ADW_CSC102H(이력)에서 성별(GNDR_DCD)과 연령대(AGE_GRP_CD) 컬럼을 확인했습니다. 여신 잔액 정보를 결합하기 위한 고객 기준 테이블로 적합합니다.
- [search_table_meta(고객 인적사항, page=1)] 추가 인적사항 테이블들을 탐색했으나, 고객의 성별 및 연령대는 이미 Step 1에서 식별된 TB_ADW_CSC101M이 가장 표준적입니다. 나머지 테이블들은 본 질의(연령대/성별별 여신 잔액)와 직접적인 관계가 부족합니다.

## 현재 확인된 지식
[K1] measure:평균 여신 잔액
  상태: PROBABLE
  값: AVG(LN_BAL_AMT)
  출처: 활용사례
  근거: TB_ADW_LNB301M(여신정보기본)에서 잔액(LN_BAL_AMT) 집계 패턴 확인. 성별/연령대 컬럼은 이 테이블에 없음.

[K3] table:고객기본정보
  상태: CONFIRMED
  값: TB_ADW_CSC101M
  출처: 테이블메타
  근거: TB_ADW_CSC101M에서 성별(GNDR_DCD)과 연령대(AGE_GRP_CD) 컬럼 확인. EDPS_CSN으로 여신 테이블과 조인 가능.

## 도구 실행 이력
[스텝 1] ✓ search_table_meta("고객 마스터 정보, page=1")
  관련성: SELECTED 2건(TB_ADW_CSC101M, TB_ADW_CSC102H), REJECTED 8건
  발견: 고객 정보 테이블 TB_ADW_CSC101M(현재) 및 TB_ADW_CSC102H(이력)에서 성별(GNDR_DCD)과 연령대(AGE_GRP_CD) 컬럼을 확인했습니다. 여신 잔액 정보를 결합하기 위한 고객 기준 테이블로 적합합니다.
[스텝 2] ✓ search_table_meta("고객 인적사항, page=1")
  관련성: SELECTED 1건(TB_ADW_LNB301M), REJECTED 10건, PENDING 6건
  발견: 추가 인적사항 테이블들을 탐색했으나, 고객의 성별 및 연령대는 이미 Step 1에서 식별된 TB_ADW_CSC101M이 가장 표준적입니다.
[스텝 3] ✓ search_biz_terms("성별")

## 탐색된 테이블
- TB_ADW_LNB301M (여신정보기본) (SELECTED): DATA TABLE
  선택 사유: 여신 잔액(LN_BAL_AMT)을 보유한 유일한 핵심 테이블로, 연령대 및 성별 정보를 결합하기 위한 기준 테이블임
  컬럼: LN_NO(대출번호), STD_DT(기준일자), EDPS_CSN(전산고객번호), LN_EXC_AMT(여신실행금액), LN_BAL_AMT(여신잔액), LN_DT(여신일자), MTRTY_DT(만기일자), APLY_RT(적용금리), LN_DCD(여신구분코드), LN_STCD(여신상태코드) (+6)
- TB_ADW_CSC101M (고객정보기본(현재)) (SELECTED): CUS고객정보기본(현재)
  선택 사유: 현재 시점의 고객 기본 정보(성별, 연령대)를 포함하여 분석에 적합함
  컬럼: EDPS_CSN(전산고객번호), STD_DT(기준일자), CSM(고객성명), CUS_DCD(고객구분코드), JOIN_DT(가입일자), BLNG_BRCD(소속부점코드), GNDR_DCD(성별구분코드), AGE_GRP_CD(연령대그룹코드), CUS_GRD_CD(고객등급코드), TEL_NO(전화번호) (+3)
- TB_ADW_CSC102H (고객마스터(이력)) (SELECTED): CUS고객마스터(이력) 테이블
  선택 사유: 이력 데이터로 시점별 분석 시 활용 가능
  컬럼: EDPS_CSN(전산고객번호), STD_DT(기준일자), CSM(고객성명), CUS_DCD(고객구분코드), RGST_DT(등록일자), BLNG_BRCD(소속부점코드), GNDR_DCD(성별구분코드) (+6)

[제외된 테이블 — 재탐색 불필요]
- TB_ADW_LNB341P (여신승인정보) — 제외: 잔액 컬럼 없음. 승인금액만 존재, 질의 목적과 다름
- TB_ADW_CRD419M (카드기본) — 제외: 카드 도메인 테이블로 여신(대출) 조회 목적에 부합하지 않음
- TB_ADW_DEP201P (수신잔액) — 제외: 수신(예금) 도메인으로 여신 조회 목적에 부합하지 않음
- TB_ADW_CSC107M (고객직업정보) — 제외: 직업 정보 테이블로 성별/연령대/잔액과 직접 관계 부족
- TB_ADW_CSC113M (고객연락처) — 제외: 연락처 정보 테이블로 본 질의와 직접 관계 부족

## 샘플 데이터 현황
- TB_ADW_LNB301M: 미조회 (get_sample_rows로 확인 가능)
- TB_ADW_CSC101M: 미조회 (get_sample_rows로 확인 가능)
- TB_ADW_CSC102H: 미조회 (get_sample_rows로 확인 가능)
  "0행" 테이블은 다시 조회하지 마세요.
  "미조회" 테이블은 필요하면 get_sample_rows로 조회할 수 있습니다.
```

---

## 섹션별 데이터 소스 추적

각 섹션의 데이터가 파이프라인 어디에서 생성되는지 추적한다.
프롬프트에 들어가는 내용이 아니라, 구현 시 참조용 데이터 흐름 맵이다.

### S1. 사용자 질의 `{original_query}`

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| 원래 질의문 | 사용자 입력 | `PipelineState.original_query` |

현재 recovery_agent에서 접근 불가 — `_build_prompt()`는 `ReasoningState`만 받음.
→ `_build_recovery_plan()` 파라미터에 `original_query` 추가 필요.

### S2. 진입 경로 `{entry_source_description}`

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| failure_type | `readiness_gate._set_failure_context()` | `reason.failure_type` |
| failure_reason (현행) | 같은 함수, 인라인 parts 조립 | `reason.failure_reason` |
| 미해소 항목 상세 (TO-BE) | `_collect_failure_diagnostics()` 신규 | `diagnostics["unresolved_details"]` |
| 거부 테이블 사유 (TO-BE) | 같은 함수 | `diagnostics["rejected_details"]` |
| selection_reason 원본 | `context_interpreter._apply_table_judgments()` → LLM 판정 | `TableMeta.selection_reason` |

**핵심 데이터 흐름**:
```
context_interpreter LLM → {"status": "REJECTED", "reason": "잔액 컬럼 없음..."} 
  → _apply_table_judgments() → TableMeta.selection_reason에 저장
  → readiness_gate._collect_failure_diagnostics() → diagnostics["rejected_details"]
  → _format_unresolvable_reason() → reason.failure_reason
  → recovery_agent._build_entry_description() → {entry_source_description}
```

### S3. 미해소 항목 `{unresolved_items}`

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| KI 목록 | `reasoning_preparer._initialize_knowledge_items()` | `reason.knowledge_items` |
| KI key (예: `filter:성별`) | NormalizedQuery.filters에서 파생 | `KnowledgeItem.key` |
| KI status (CANDIDATE) | `context_interpreter` knowledge_updates → `promote()` | `KnowledgeItem.status` |
| knowledge_id (K1, K2) | `reasoning_preparer`에서 순번 채번 | `KnowledgeItem.knowledge_id` |
| 추정값 (GNDR_DCD) | `context_interpreter` knowledge_updates의 "value" | `KnowledgeItem.value` |

**문제 발견**: `knowledge_id`가 빈 문자열인 케이스가 trace에서 확인됨.
→ `_build_prompt()`에서 fallback 채번: `ki.knowledge_id or f"K{idx}"`

### S4. 이전 실패 기록 `{dead_ends_summary}`

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| DeadEnd 레코드 | `readiness_gate` REPLAN 판정 시 생성 | `reason.dead_ends: list[DeadEnd]` |
| failure_type | `_set_failure_context()` | `DeadEnd.failure_type` |
| reason | `_set_failure_context()` → failure_reason 복사 | `DeadEnd.reason` |
| lessons_learned | recovery_agent LLM 응답의 `lessons_learned` 필드 | `DeadEnd.lessons_learned` |

**교훈 첨부 흐름**:
```
recovery_agent LLM → RecoveryPlan.lessons_learned
  → _attach_lessons() → reason.dead_ends[-1].lessons_learned에 첨부
```

### S5. 현재 라운드 탐색 결과 `{current_round_facts}` (신규)

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| discovered_facts 전체 | `context_interpreter._populate_discovered_facts()` | `reason.discovered_facts: list[str]` |
| 각 fact 형식 | `[tool(input)] insight` — insight는 LLM 해석 결과 | 같은 곳 |
| recovery_fact_start_index | recovery_agent_node 진입 시 기록 (TO-BE) | `reason.recovery_fact_start_index` |
| 현재 라운드 facts | `discovered_facts[start_index:]` 슬라이스 | 렌더링 시점 계산 |

**인사이트 생성 흐름**:
```
context_retriever → step.raw_result (도구 실행 결과)
  → context_interpreter LLM → {"insight": "TB_ADW_CSC101M에서 GNDR_DCD 확인..."} 
  → _apply_batch_insights() → step.insight에 저장
  → _populate_discovered_facts() → discovered_facts에 "[tool(input)] insight" 형식으로 append
```

### S6. 확인된 지식 `{confirmed_knowledge}`

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| KI 목록 | `reasoning_preparer` 초기 생성 + `context_interpreter` 갱신 | `reason.knowledge_items` |
| ki.value (예: AVG(LN_BAL_AMT)) | `context_interpreter` knowledge_updates의 "value" | `KnowledgeItem.value` |
| ki.source (예: 활용사례) | knowledge_updates의 "source" | `KnowledgeItem.source` |
| ki.evidence (TO-BE 활용) | `KnowledgeItem.promote()` 호출 시 append | `KnowledgeItem.evidence: list[str]` |

**evidence 생성 흐름**:
```
context_interpreter LLM → knowledge_updates: [{"key": "measure:평균 여신 잔액", 
  "value": "AVG(LN_BAL_AMT)", "evidence": "TB_ADW_LNB301M에서 잔액 집계 패턴 확인", ...}]
  → context_interpreter 코드에서 KnowledgeItem 생성 시 evidence=[evidence_text]
  → 또는 기존 KI의 promote() 호출 시 evidence.append(evidence_text)
```

**TO-BE에서 활용**: `ki.evidence[-1][:120]`을 "근거:" 라인으로 렌더링

### S7. 도구 실행 이력 `{tool_execution_history}` (기존 유지)

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| execution_plan | `reasoning_preparer` 또는 `recovery_agent` LLM이 생성 | `reason.execution_plan: list[ExecutionStep]` |
| step.status (DONE/SKIPPED) | `context_retriever` 도구 실행 후 설정 | `ExecutionStep.status` |
| step.raw_result | `context_retriever` 도구 실행 결과 | `ExecutionStep.raw_result` |
| step.insight | `context_interpreter._apply_batch_insights()` | `ExecutionStep.insight` |
| "관련성" 라인 | `_RELEVANCE_BUILDERS[tool]()` — explored_tables의 selection_status 집계 | 렌더링 시점 계산 |

### S8. 탐색된 테이블 `{explored_tables_summary}`

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| TableMeta 목록 | `context_retriever._extract_tables()` — MongoDB 메타에서 변환 | `reason.explored_tables: list[TableMeta]` |
| table_name | MongoDB `table_meta.table_name` | `TableMeta.table_name` |
| alt_name (한글명) | MongoDB `table_meta.alt_name` | `TableMeta.alt_name` |
| description | MongoDB `table_meta.description` | `TableMeta.description` |
| columns[].name | MongoDB `table_meta.columns[].name` | `ColumnInfo.name` |
| columns[].alt_name (한글) | MongoDB `table_meta.columns[].alt_name` | `ColumnInfo.alt_name` |
| selection_status | `context_interpreter._apply_table_judgments()` LLM 판정 | `TableMeta.selection_status` |
| selection_reason | 같은 곳, LLM이 생성한 판정 사유 | `TableMeta.selection_reason` |

**REJECTED 테이블 판정 흐름**:
```
context_retriever → search_table_meta("고객 인적사항") → MongoDB에서 10건 반환
  → _extract_tables() → TableMeta 10건 생성 (selection_status=PENDING)
  → context_interpreter LLM → {"explored_tables": [
      {"table_name": "TB_ADW_CSC107M", "status": "REJECTED", 
       "reason": "직업 정보 테이블로 성별/연령대/잔액과 직접 관계 부족"}, ...]}
  → _apply_table_judgments() → TableMeta.selection_status=REJECTED, selection_reason 저장
```

### S9. 샘플 데이터 현황 `{sample_data_summary}`

| 데이터 | 생성 주체 | 저장 위치 |
|--------|----------|----------|
| sample_rows: None | 기본값 (get_sample_rows 미실행) | `TableMeta.sample_rows = None` |
| sample_rows: [] | `context_retriever` get_sample_rows 실행 → 0건 | `TableMeta.sample_rows = []` |
| sample_rows: [...] | `context_retriever` get_sample_rows 실행 → N건 | `TableMeta.sample_rows = [dict, ...]` |

---

## 개선 효과 상세 비교

### 1. 진입 경로 (entry_source_description)

| 항목 | AS-IS | TO-BE |
|------|-------|-------|
| 미해소 항목 | 없음 | `filter:성별=['남성'] (CANDIDATE) — 추정: GNDR_DCD 코드값 미확인` |
| 거부 테이블 | 없음 | 상위 3건 + 사유 |
| 원래 질의 | 없음 | `"연령대별 남자 평균 여신 잔액 알려줘"` |
| 기준 미달 이유 | "확신도 47%" (숫자만) | "확신도 47%로 생성 기준(60%) 미달" (기준값 명시) |

### 2. 지식 항목 (confirmed_knowledge / unresolved_items)

| 항목 | AS-IS | TO-BE |
|------|-------|-------|
| knowledge_id | `[]` (빈 문자열) | `[K1]`, `[K2]`, `[K3]` |
| 포맷 | 한 줄에 `— () ,` 혼용 | 들여쓰기 key-value (상태/값/출처/근거 분리) |
| evidence | 없음 | `근거:` 행으로 최신 1건 120자 |
| PROBABLE 표시 | `(AVG(LN_BAL_AMT), 활용사례)` — value와 source 구분 불가 | `값: AVG(LN_BAL_AMT)` + `출처: 활용사례` 분리 |
| CANDIDATE 추정값 | 없음 | `추정값: GNDR_DCD` |

### 3. 테이블 요약 (explored_tables_summary)

| 항목 | AS-IS | TO-BE |
|------|-------|-------|
| 한글명 | 없음 | `TB_ADW_LNB301M (여신정보기본)` |
| 선택 사유 | 없음 | `여신 잔액(LN_BAL_AMT)을 보유한 유일한 핵심 테이블...` |
| 컬럼 한글명 | 없음 | `LN_NO(대출번호), EDPS_CSN(전산고객번호), ...` |
| REJECTED 테이블 | **완전 누락 (18건)** | 상위 5건 + 사유 표시 |
| PENDING 무관 테이블 | 카드/수신/보험/계정 5건 표시 | SELECTED가 아니면 비노출 |

### 4. 샘플 데이터 (sample_data_summary)

| 항목 | AS-IS | TO-BE |
|------|-------|-------|
| 미조회 vs 0건 | `0행 (데이터 없음 또는 미조회)` (9건 모두 동일) | `미조회 (get_sample_rows로 확인 가능)` |
| 무관 테이블 | 카드/수신/보험/계정 5건 포함 | SELECTED 테이블만 3건 |
| 행동 가이드 | "0행인 테이블은 다시 조회하지 마세요" (미조회도 차단) | 미조회→조회 가능, 0행→재조회 불필요 |

### 5. 누적 인사이트 (current_round_facts) — AS-IS에는 없음

| 항목 | AS-IS | TO-BE |
|------|-------|-------|
| 누적 발견사항 | **섹션 자체가 없음** | 현재 라운드 facts 슬라이스로 제공 |

---

## 토큰 비교

| 섹션 | AS-IS 문자수 | TO-BE 문자수 | 변화 |
|------|------------|------------|------|
| 사용자 질의 | 0 | ~60 | +60 |
| 진입 경로 | ~250 | ~480 | +230 (미해소/거부 상세 추가) |
| 미해소 항목 | ~50 | ~70 | +20 |
| 실패 기록 | ~350 | ~300 | -50 (압축) |
| 누적 인사이트 | 0 | ~380 | +380 (신규) |
| 확인된 지식 | ~130 | ~350 | +220 (evidence 추가) |
| 도구 이력 | ~700 | ~700 | 0 (유지) |
| 테이블 요약 | ~1,450 | ~1,600 | +150 (한글명+사유+REJECTED, PENDING 제거로 상쇄) |
| 샘플 데이터 | ~520 | ~170 | -350 (SELECTED만, 무관 테이블 제거) |
| **변수 합계** | **~3,450** | **~4,110** | **+660 (+19%)** |
| **추정 토큰** | **~1,730** | **~2,050** | **+320 (+18%)** |

토큰 18% 증가로 정보 밀도가 대폭 향상. 변수 합계 4,110자는 토큰 가드 한도(10,000자)의 41%로 충분히 여유 있음.
