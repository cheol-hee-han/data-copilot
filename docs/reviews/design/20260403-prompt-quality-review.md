# 프롬프트 전수 품질 검토 리포트

> 작성일: 2026-04-03
> 검토 대상: `resources/prompts/` 내 활성 프롬프트 14개 파일 (미사용 7개 제외)
> 검토 방법: 파이프라인 아키텍처 대조 + 프롬프트 조립 코드 확인 + 크로스커팅 분석
> 검토 관점: 정보 전달 적절성, 지시 정확성, 토큰 효율, 폐쇄망 모델 대응, 누락/중복/모순

---

## 검토 대상 목록

| 계층 | 파일 | 역할 |
|------|------|------|
| Interpret | `context_classifier_system.txt` | 이력 해소 + 의도 분류 |
| Interpret | `context_classifier_user.txt` | user 템플릿 |
| Interpret | `query_normalizer_phase1_system.txt` | 8-Slot 정규화 |
| Interpret | `query_normalizer_phase1_user.txt` | user 템플릿 (동의어 사전 주입) |
| Interpret | `query_normalizer_phase2_system.txt` | 교차 검증 (R1~R12) |
| Interpret | `query_normalizer_phase2_user.txt` | user 템플릿 |
| Reason | `knowledge_interpreter_system.txt` | 도구 결과 해석, 지식 승격 |
| Reason | `sql_generator_system.txt` | SQL 생성 |
| Reason | `sql_generator_fix_section.txt` | SQL 재생성 지침 (조건부 삽입) |
| Reason | `sql_validator_system.txt` | SQL 의미 검증 (Layer 2b) |
| Reason | `recovery_agent_system.txt` | 실패 후 재계획 |
| Reason | `table_comparison_system.txt` | 유사 테이블 비교 판정 |
| Present | `analyzer_system.txt` + `analyzer_user.txt` | 데이터 분석/인사이트 |
| Present | `formatter_system.txt` + `formatter_user.txt` | 보고서 포맷팅 |
| Present | `analyzer_viz_judgment_system.txt` + `_user.txt` | 시각화 유형 판정 |
| Present | `analyzer_viz_svg_system.txt` + `_user.txt` | SVG 생성 |

---

## 1. Interpret 계층

### 1.1 context_classifier_system.txt — 등급: B+

**잘된 점**
- continuity + intent 2단계 동시 판정 구조가 효율적 (1회 LLM 호출로 해결)
- 분류 기준의 판별 체크리스트가 구체적이고 예시 충분
- ambiguities 객체 구조가 clarification_handler의 AmbiguitySignal과 정합

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| CC-1 | 토큰 과다 | Few-shot 예시 **15개**. CONTINUE 6개, NEW 5개, UNSURE 2개, AMBIGUOUS 1개, MEDIUM 1개. 패턴별 대표 1개씩 **8개 이하**로 축소 가능. 특히 CONTINUE+DATA_EXTRACTION이 3개(조건 추가/범위 조정/조건 변경)인데 패턴이 동일 | 중 |
| CC-2 | **예시 누락** | `{clarification_history}`가 채워진 상태에서의 예시가 **0개**. 사용자가 명확화 질문에 "1번"이라고 답변했을 때 CONTINUE로 판정하고 context를 어떻게 합성하는지 가이드 없음. | **조치완료** |
| ~~CC-3~~ | ~~엣지케이스~~ | ~~해당 없음: 명확화 포기 시에도 NEW+CASUAL_TALK으로 통일 처리하면 되므로 별도 분기 불필요. 현재 아키텍처에서 포기와 일반 대화 종료의 후속 처리가 동일함~~ | — |
| ~~CC-4~~ | ~~파싱~~ | ~~취소: UNSURE일 때 빈 문자열 유지가 LLM 출력 안정성에 유리. 스키마 고정(항상 같은 키, 같은 타입)이 폐쇄망 모델에서 더 안전하며, null/필드 생략은 오히려 구조 붕괴 위험~~ | — |

**개선 제안**

**(A) CC-2 해결 — 명확화 재입력 예시 추가**

가장 중요한 누락. `{clarification_history}`가 있는 상태에서의 few-shot을 추가하여, 명확화 왕복 시의 판정 패턴을 학습시켜야 함.

추가할 예시 2개:
```
--- CONTINUE + DATA_EXTRACTION (명확화 응답 — 선택) ---

이전 대화:
  사용자: 대출
명확화 대화 이력:
  시스템: "대출과 관련하여 어떤 데이터가 필요하신가요?
          1) 대출 잔액 조회  2) 대출 실행 건수 조회  3) 대출 연체 현황 조회"
현재 입력: 1번
→
{
  "continuity": {"label": "CONTINUE", "confidence": "HIGH",
    "reason": "이전 명확화 질문에 대한 선택 응답",
    "context": "대출 잔액 조회해줘"},
  "intent": {"label": "DATA_EXTRACTION", "confidence": "HIGH",
    "reason": "명확화를 통해 대출 잔액 조회로 의도 확정"}
}

--- NEW + CASUAL_TALK (명확화 포기) ---

이전 대화:
  사용자: 데이터 좀 뽑아줘
명확화 대화 이력:
  시스템: "어떤 데이터가 필요하신가요? 1) 고객 현황  2) 대출 현황  3) 수신 현황"
현재 입력: 됐어 다른 거 할게
→
{
  "continuity": {"label": "NEW", "confidence": "HIGH",
    "reason": "명확화 포기 표현 — 이전 맥락 종료",
    "context": ""},
  "intent": {"label": "CASUAL_TALK", "confidence": "HIGH",
    "reason": "데이터 요청 없이 대화 종료 의사 표현"}
}
```

**(B) ~~CC-3 해결~~ — 해당 없음 (CC-3 취소에 따라 불필요)**

**(C) CC-1 해결 — 예시 축소 (15개 → 10개)**

제거 대상 (유사 패턴 통합):
- CONTINUE+DATA_EXTRACTION 3개(조건 추가/범위 조정/조건 변경) 중 2개 제거 → "조건 변경"("부산은?") 1개만 유지 (가장 짧고 핵심 패턴 대표)
- NEW+DATA_EXTRACTION 2개(이력 없음/이력 있으나 다른 주제) 중 1개 제거 → "이력 있으나 다른 주제" 1개만 유지 (이력 없는 케이스는 자명)
- NEW+CASUAL_TALK 2개(인사/맥락 종료) 중 1개 제거 → "맥락 종료" 제거 (위 (A)의 명확화 포기 예시로 대체)

결과: 기존 15개 - 5개 제거 + 2개 추가(명확화) = **12개** (토큰 ~800 절감)

**(D) ~~CC-4~~ — 취소**

재검토 결과, 현행(빈 문자열)이 더 적절함:
- LLM은 출력 스키마가 고정(항상 같은 키, 같은 타입)일 때 가장 안정적
- `null`이나 필드 생략은 조건부 구조 변경을 유발하여 폐쇄망 모델에서 출력 구조 붕괴 위험
- 다운스트림 파싱에서도 `if label:` 한 줄로 빈 문자열 처리 가능, 필드 생략 시 `KeyError` 방어 코드가 오히려 추가됨

---

### 1.2 query_normalizer_phase1_system.txt — 등급: B

**잘된 점**
- 8-Slot 모델이 정교하고 허용값이 명확하게 정의됨
- INTENT 판별 절차(Q1~Q7)의 우선순위 기반 접근이 체계적
- ambiguities의 INFER 접근이 우수 — 모호한 부분을 사용자에게 묻지 않고 추론하되, Reason 계층에서 검증 포인트로 활용

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| NP1-1 | ~~설계 우려~~ | ~~과도한 지적 — 제외: Q1~Q7 우선순위는 SQL 구조 복잡도 순(COMPARE→TREND→RANK→…)으로 설계되어 있으며, 복잡한 패턴일수록 primary여야 SQL 생성 시 핵심 구조가 보존됨. 리뷰 예시("지점별 대출잔액 추이 상위 5개")에서 TREND가 primary인 것이 정확하고, RANK 정보는 secondary+modifiers에 보존되어 손실 없음. "최종 결과물 형태 기준" 보충 규칙은 오히려 주관적 판단을 추가하여 폐쇄망 모델에서 출력 불안정 유발~~ | — |
| NP1-2 | 누락 | `{output_template_text}` 플레이스홀더가 있지만, 주입될 데이터의 형식에 대한 설명이 프롬프트에 없음. `serialize_template_registry()`가 마크다운으로 변환하지만 LLM이 이 블록을 어떤 의미로 해석해야 하는지 안내 부족 | **조치완료** — 템플릿 사전 제거, LLM 추론으로 전환. expected_columns를 output_hint로 sql_generator에 전달하도록 경로 추가 |
| NP1-3 | 누락 | `{synonym_dict}`(user 프롬프트에서 주입)의 활용 가이드가 system 프롬프트에 없음. "동의어 사전에 있으면 normalized_term 기재" 규칙은 user 프롬프트에만 있어 system의 슬롯 추출 규칙과 연결이 약함 | **조치완료** — 동의어 사전 주입 제거, LLM 자체 추론으로 전환. system 프롬프트의 normalized_term 설명도 "업무적으로 동일한 의미의 표준 용어"로 통일 |
| NP1-4 | ~~유형 부족~~ | ~~제외: ENTITY 모호성은 INTENT 유형으로 포괄 처리 가능하며, 유형을 세분화하면 폐쇄망 모델에서 분류 부담만 증가~~ | — |
| NP1-5 | ~~판단 부담~~ | ~~제외: 슬롯 3 규칙 5번에 "agg_function 확정 불가 시 null + ambiguities 기재" 지시가 이미 있고, 체크리스트에도 "잔액 → 평균잔액/기말잔액/최저잔액" 예시가 INTENT로 등재되어 있음. 사실 오류~~ | — |

**개선 제안**
1. ~~Q1~Q7 규칙 보충~~ — 제외 (NP1-1 취소에 따라 불필요)
2. ~~`{output_template_text}` 안내 추가~~ — 제외 (템플릿 사전 자체 제거, LLM 추론으로 전환)
3. ambiguity_type에 `ENTITY` 유형 추가 또는 INTENT의 설명을 "용어/엔티티/집계 기준 모호" 포괄로 확장

---

### 1.3 query_normalizer_phase2_system.txt — 등급: B-

**잘된 점**
- R1~R12 교차 검증 규칙이 체계적
- Phase 1과 역할 분리가 명확 (정합성 검증만 담당, ambiguities 수정 금지)

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| NP2-1 | **예시 부재** | Phase 1은 예시 4개인데 Phase 2는 **0개**. R1~R12 위반→수정의 구체적 예시가 없어서 LLM이 규칙을 올바르게 적용하는지 보장 불가. 특히 R1(DIMENSION↔MEASURE 정합성) 위반 수정 예시가 필수 | **조치완료** — R1(GROUP↔agg_function), R2(COMPARE↔compare_period) 수정 예시 2개 추가 |
| NP2-2 | 불명확 | R4 "TREND → 적절한 시간 차원을 추가" — **어떤 granularity**로 추가할지 가이드 없음. 월별? 분기별? 원본 질의의 시간 범위에서 추론하라는 것인지, 기본값이 있는지 불명 | **조치완료** — TIME.resolve → granularity 매핑 규칙 + MONTH 기본값 추가 |
| NP2-3 | ~~과도~~ | ~~제외: EXISTS는 filter_type enum 값(의미 태깅)이지 SQL 키워드가 아님. 실제 SQL 구현(EXISTS 서브쿼리 vs COUNT>0)은 sql_generator가 결정하므로 정규화 단계에서 과도하지 않음~~ | — |
| NP2-4 | ~~논리 갭~~ | ~~제외: NP2-2 조치(granularity 기본값 규칙)로 자동 해소. 슬롯 보정과 ambiguity 추가는 별개 동작이며 충돌 아님~~ | — |

**개선 제안**
1. **R1 위반 수정 예시 1개** + **R2 위반 수정 예시 1개** 추가 (최소 2개)
   ```
   ■ Phase 2 수정 예시: R1 위반
   Phase 1 결과: dimensions=[{term:"지점",role:"GROUP"}], measures=[{agg_function:"NONE"}]
   → R1 위반: GROUP이 있는데 agg_function이 NONE
   수정 후: measures=[{agg_function:"SUM"}]  (문맥상 합계가 자연스러운 경우)
   ```
2. R4에 granularity 추론 규칙 추가: "TIME 슬롯의 resolve에서 추론 — LAST_N_MONTHS → MONTH, LAST_N_YEARS → YEAR, 없으면 MONTH 기본값"
3. R6을 "EXISTS/NOT_EXISTS 또는 IMPLICIT 필터가 있어야 함"으로 완화

---

## 2. Reason 계층

### 2.1 knowledge_interpreter_system.txt — 등급: A-

**잘된 점**
- 도구 결과 교차 참조 가이드가 구체적 (활용사례 SQL ↔ 테이블 메타 교차 검증)
- 지식 항목 상태 판정 기준이 confidence 수치와 함께 명확
- 예시 2개가 정상 케이스(교차 확인)와 Cold Start(메타만 있는 경우)를 잘 대비

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| KI-1 | ~~지침 부족~~ | ~~제외: 예시에서 날짜 분포 기반 적합 판정 패턴을 이미 보여주고 있으며, 부적합/날짜 컬럼 없음 케이스는 LLM이 상식적으로 판단 가능한 수준~~ | — |
| KI-2 | ~~예시 누락~~ | ~~제외: CONFLICTED는 발생 빈도가 매우 낮은 케이스로, 예시 추가에 따른 토큰 증가 대비 효과 낮음. 규칙(L73)에 판정 기준은 이미 명시되어 있음~~ | — |
| KI-3 | 정합성 | `relevant_use_cases`의 `sql_id`가 도구 결과의 어떤 필드와 매핑되는지 불명확. 예시에서 `"sql_id": "uc_001"`이라 했는데 실제 도구 반환값에 이 필드가 없으면 LLM이 임의 ID 생성 | **조치완료** — knowledge_fetcher에서 uc_001 형태 id 채번 추가, 프롬프트에 "도구 결과의 id를 그대로 사용" 안내 추가 |

**개선 제안**
1. `{time_slot}` 아래에 판정 가이드 추가:
   ```
   - 테이블 날짜 분포의 MAX가 시간 조건 범위에 포함되면 → 적합
   - MAX가 시간 조건 이전이면 → 데이터 미적재 위험, reason에 명시
   - 날짜 컬럼이 없는 테이블은 → 시간 조건 미적용, 보조 테이블로만 사용 가능
   ```
2. CONFLICTED 판정 예시 1개 추가

---

### 2.2 sql_generator_system.txt + sql_generator_fix_section.txt — 등급: A-

**잘된 점**
- 환각 방지 규칙이 매우 구체적이고 **위반 예시**까지 포함 (타 프롬프트에 없는 좋은 패턴)
- dialect별 문법 차이가 3종(tsql/hive/postgresql) 모두 명확
- PII 마스킹 규칙이 dialect별로 제공
- 사고 과정 STEP 1~7이 체계적

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| SG-1 | **규칙-예시 모순** | 규칙 9: "집계 쿼리(GROUP BY + 집계함수만으로 구성)를 **제외한** 모든 쿼리에 행 제한 포함" → **예시 1**(순수 집계 COUNT+SUM, GROUP BY 없음)에 `LIMIT 10000` 포함. 규칙과 예시가 직접 모순 | **조치완료** — 규칙 9를 "모든 쿼리에 행 제한 포함"으로 단순화, 예시와 일치시킴 |
| SG-2 | **예시 내부 모순** | **예시 2**: "지점별 수신 잔액 현황 뽑아줘"인데 SQL에 `WHERE A.BASE_DT >= CONVERT(VARCHAR, DATEADD(month, -1, GETDATE()), 112)` 지난달 조건 포함. 질의 분해에 시간 조건 없고, confirmed_terms에도 기간 필터가 없는데 SQL에 기간 조건이 있음 | **조치완료** — 예시 2의 질의 분해에 기간 필터, confirmed_terms에 filter:기간 추가 |
| SG-3 | ~~안내 부족~~ | ~~제외: build_clarification_context() 출력이 자기 설명적(`[명확화 대화]`, `[자동 추론된 조건]` 헤더+구조화 텍스트)이라 별도 안내 불필요~~ | — |
| SG-4 | ~~사고 과정 누락~~ | ~~제외: STEP 2에서 confirmed_terms의 조인 정보가 자연스럽게 포함되고, 예시 2/3에서 조인 패턴이 이미 demonstrated~~ | — |
| SG-5 | ~~토큰 낭비~~ | ~~제외: 약 10토큰 수준의 차이로 수정 대비 효과 미미~~ | — |

**개선 제안**
1. 규칙 9 수정: "모든 쿼리에 행 제한을 포함한다 (집계 전용 쿼리는 LIMIT 10000, 비집계 쿼리도 LIMIT 1000)" — 또는 예시 1에서 LIMIT 제거
2. 예시 2의 질의 분해에 `filters=[{term: "기간", value: "최신"}]`과 confirmed_terms에 `filter:기간 → BASE_DT 조건` 추가
3. `## 명확화 컨텍스트` 아래에 "사용자가 명확화를 통해 확정한 조건과 자동 추론된 조건입니다. SQL 작성 시 이 조건을 반드시 반영하세요." 한 줄 추가
4. 사고 과정에 STEP 2.5 추가: "다중 테이블이면 confirmed_terms에서 조인 경로를 확인하고 JOIN 절을 결정"

---

### 2.3 sql_validator_system.txt — 등급: A

**잘된 점**
- 8개 체크포인트가 빠짐없이 설계됨
- failure_classification(local_fix/structural)의 판정 기준이 구체적이고 경계 사례까지 포함
- 예시 3개가 PASS/local_fix/structural을 각각 커버하며 예시 품질이 높음

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| SV-1 | 엣지케이스 | **0건 반환의 이중 판정 가이드 부재**. 예시 3에서 0건은 `db_execution.pass=true` + `logical_consistency.pass=false`로 처리. 그러나 0건이 정상인 케이스도 있음(예: "연체 없는 지점 수" → 0건이 정답). **0건이 정상인지 비정상인지 판단하는 기준**(질의 의도가 "존재 여부"인 경우 0건은 유효한 결과)이 필요 | **조치완료** — 체크 7에 "0건 반환은 의도에 따라 유효할 수 있음" 안내 추가 |
| SV-2 | ~~형식 미비~~ | ~~제외: 예시에서 이미 구체적이고 actionable한 fix_instruction이 demonstrated. 형식 강제 시 오히려 내용 빈약화 우려~~ | — |
| SV-3 | ~~누락~~ | ~~제외: 타입 관련 문제는 체크 8(db_execution)에서 실행 에러로 자연스럽게 잡히는 구조. 예시 2가 정확히 이 케이스를 demonstrated~~ | — |

**개선 제안**
- 체크 8의 detail에 "0건 반환 시: 질의 의도가 존재 여부 확인(EXIST_CHECK)이면 0건도 유효한 결과로 판단" 한 줄 추가

---

### 2.4 recovery_agent_system.txt — 등급: B+

**잘된 점**
- 도구 우선순위 가이드가 실전적이고 각 도구의 활용 시점이 명확
- dead_ends 반복 방지, exploration_history 참조로 이전 탐색 중복 방지
- 5개 예시가 다양한 실패 시나리오를 커버 (코드값 불명, 0건 리턴, 텍스트 필터, 산출식 불명, give_up)

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| RA-1 | ~~action 불일치~~ | ~~제외: force-generate는 LLM 판단이 아닌 코드 로직이 give_up 후 readiness score 기반으로 자동 결정(`_finalize_give_up()`). 프롬프트 action에 추가 불필요~~ | — |
| RA-2 | ~~토큰 과다~~ | ~~제외: 5개 예시가 각각 다른 도구 조합(코드메타/날짜분포/컬럼검색/매뉴얼+용어사전/give_up)을 보여줌. 토큰 제한 시 예시 3 제거가 최적이나 현시점 급하지 않음~~ | — |
| RA-3 | ~~구분 불명확~~ | ~~제외: exploration_history=중복 방지(negative), discovered_facts=탐색 방향(positive)으로 역할 구분 자명~~ | — |

**개선 제안**
1. action에 `force-generate` 옵션 추가 여부를 코드 로직과 대조하여 결정
2. 예시 3과 4를 제거하고 예시 1(코드값), 2(0건 진단), 5(give_up) 3개로 축소

---

### 2.5 table_comparison_system.txt — 등급: C+

**잘된 점**
- 역할이 명확하고 간결

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| TC-1~4 | **미사용** | `TABLE_COMPARISON_SYSTEM`이 import만 되어 있고 실제 LLM 호출에 사용되지 않음. knowledge_interpreter가 배치 해석에서 selected/rejected를 동시 처리하는 구조. 프롬프트 개선보다 **기능 재설계가 선행 필요** — recovery_agent의 도구(`compare_tables`)로 재구현하는 방안을 검토 중. 상세: `docs/todo/table-comparison-redesign.md` | **보류** |

**개선 제안**
1. output 구조를 knowledge_interpreter와 동일하게 변경:
   ```json
   {
     "selected": [{"table_name": "TB_A", "reason": "선정 사유"}],
     "rejected": [{"table_name": "TB_B", "reason": "제외 사유"}]
   }
   ```
2. 유사 테이블 비교 판정 기준 추가:
   ```
   - 갱신주기: 일배치 > 월배치 (최신성)
   - 데이터 범위: 질의 시간 조건을 포함하는 테이블 우선
   - 집계 수준: 질의가 건별 상세면 원장, 집계면 집계 테이블
   - 스냅샷 vs 이력: 특정 시점 데이터면 스냅샷, 변화 추이면 이력 테이블
   ```
3. 예시 최소 1개 추가 (유사 테이블 3개 비교 → 1개 선택, 2개 제외)

---

## 3. Present 계층

### 3.1 analyzer_system.txt — 등급: B+

**잘된 점**
- 사고 과정(STEP 1~5)이 분석 순서를 체계적으로 안내
- 예시 2개의 인사이트 품질이 높고 action_items가 실전적

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| ~~AN-1~~ | ~~역할 중복~~ | ~~제외: analyzer와 formatter는 상호 배타 경로(DATA_ANALYSIS vs DATA_EXTRACTION). 역할 중복이 아니라 각 경로에서 독립적으로 포맷팅을 수행하는 것이 정상~~ | — |
| ~~AN-2~~ | ~~정보 단절~~ | ~~제외: user_input 자체에 "비교해줘", "추이 분석해줘" 등 의도가 포함되어 있으므로 LLM이 충분히 추론 가능. intent enum을 별도 전달하는 것은 과잉 설계~~ | — |
| ~~AN-3~~ | ~~가이드 부족~~ | ~~제외: 데이터 규모별 분석 깊이는 LLM이 자연스럽게 조절하는 영역. 규칙화하면 오히려 경직~~ | — |

**개선 제안**
1. user 프롬프트에 `[질의 의도]\n{intent_type}` 섹션 추가 — 코드에서 NormalizedQuery의 intent를 전달
2. summary의 금액 변환 규칙을 명시하거나, "summary에서도 포맷 규칙 3의 금액 변환을 적용" 한 줄 추가

---

### 3.2 formatter_system.txt — 등급: B

**잘된 점**
- 금액/비율/건수 포맷 규칙이 한국 금융 관행에 맞음
- "기술 용어 금지" 원칙이 명확하고 예시가 이를 잘 시연

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| FM-1 | **코드값 하드코딩** | **조치완료**: `{code_mappings}` placeholder로 교체. SQLGlot alias 해소 + code_map 필터링으로 SQL 결과 컬럼 관련 코드만 동적 주입. 변경: `sqlglot_analyzer.py`, `formatter_system.txt`, `response_formatter.py`, `formatter.py` | — |
| FM-2 | 규칙 불완전 | **조치완료**: 금액 포맷 규칙을 5단계(조/억/만/원/소수점)로 보완. 변경: `formatter_system.txt` | — |
| FM-3 | 역할 중복 | "조회 건수가 0이면 가능한 원인과 재시도 방법을 제안" — result_finalizer에서 0건 처리가 이미 수행될 수 있어 중복 응답 위험 | 하 |

**개선 제안**
1. `## 코드값 참고` 섹션을 `{code_mappings}` 플레이스홀더로 교체하고, Reason 계층에서 확인된 코드 매핑을 동적 주입. 불가능하면 "아래 코드값은 참고용입니다. 데이터에 포함된 실제 코드값과 다를 수 있으므로, 확인된 코드값을 우선 적용하세요" 면책 문구 추가
2. 금액 포맷 규칙 보완:
   ```
   - 1조 이상: "X조 X,XXX억원"
   - 100억 이상: "XXX억 X,XXX만원" 또는 소수점 표기 "XXX.X억원"
   - 1억 이상: "X억 X,XXX만원"
   - 1억 미만: "X,XXX만원"
   - 100만 미만: "XX만 X,XXX원"
   ```

---

### 3.3 analyzer_viz_judgment_system.txt — 등급: B+

**잘된 점**
- 판정 기준 트리(규칙 1~18)가 체계적이고 우선순위 순서로 적용
- none 기준(N1~N9)이 매우 상세하고, N4(원시 레코드 제외)의 체크리스트가 실용적
- 정량 차트와 다이어그램을 모두 커버하는 포괄적 유형 지원

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| ~~VJ-1~~ | ~~토큰 과다~~ | ~~제외: 시각화 판정은 유형별 예시가 많을수록 정확도가 올라가는 영역. 폐쇄망 토큰 절감은 모델 업그레이드(Qwen3.5 397B)로 해소 예정~~ | — |
| ~~VJ-2~~ | ~~논리 혼란~~ | ~~제외: N4→N5 예외 롤백은 의도된 설계. 원시 레코드 목록이지만 순위/크기 비교 목적이면 차트가 유효한 실제 케이스가 존재~~ | — |
| ~~VJ-3~~ | ~~출력 불일치~~ | ~~제외: 평문 3줄 출력이 오히려 간결하고 파싱도 안정적. JSON 강제가 이 노드에서는 과잉~~ | — |

**개선 제안**
1. 예시 축소 우선순위: 제거 후보 = 예시 5(donut — pie와 유사), 6(grouped_bar — stacked_bar와 유사), 8(scatter — 은행 업무에서 드뭄), 9(waterfall — 드뭄), 10(heatmap — 드뭄), 13(mind_map), 14(org_chart), 16(value_chain), 19(요약문 — 17과 유사)
2. N4+N5를 하나로 합치기: "각 행이 고유 개체의 속성 나열이고, 순위/크기 비교 목적이 아닌 경우 → none"

---

### 3.4 analyzer_viz_svg_system.txt — 등급: B-

**잘된 점**
- 픽셀 단위 레이아웃 규격(viewBox, margin, plot area)이 정밀
- 색상 시스템과 스케일링 규칙이 체계적
- 계산 과정을 예시에 포함시켜 LLM의 좌표 계산을 도움

**지적사항**

| # | 유형 | 내용 | 심각도 |
|---|------|------|--------|
| VS-1 | **토큰 최대 소모** | 전체 프롬프트 중 **가장 큰** 파일 (~15,000+ 토큰). SVG 예시 4~5개가 각각 완성된 SVG 코드 전문(100~150줄)을 포함. 폐쇄망 모델(Solar Pro 2 70B)에서 이 프롬프트 + 입력 데이터를 처리하기 **매우 어려움** | **상** |
| ~~VS-2~~ | ~~커버리지 불균형~~ | ~~제외: 오분석. 실제 예시는 정량 5개(bar, line, pie, horizontal_bar, donut) + 다이어그램 3개(flowchart, timeline, mind_map) = 총 8개. "정량 4개 + flowchart 1개만"이라는 지적이 사실과 다름~~ | — |
| VS-3 | 근본적 우려 | **보류**: LLM SVG 좌표 계산 불안정 문제는 프롬프트 수정으로 해결 불가. 프론트엔드 차트 렌더링 전환(VS-1과 동일 방향) 시 자연 해소 | — |
| VS-4 | 예시 규격 불일치 | **조치완료**: 레이아웃 규격 + pie 계산 과정을 SVG 예시 좌표(cx=320, cy=250)에 맞춰 통일. SVG 좌표는 렌더링 검증된 값이므로 유지. 변경: `analyzer_viz_svg_system.txt` | — |

**근본적 제안**
프론트엔드(React)에서 Recharts/D3.js로 렌더링하는 방식으로 전환하면:
- judgment 프롬프트가 데이터+차트유형을 결정 (이미 이 역할을 수행 중)
- SVG 프롬프트 자체가 불필요 → **~15,000 토큰 절감**
- 렌더링 정확도 100% 보장 (좌표 계산을 코드가 수행)
- 폐쇄망 모델의 LLM 호출 1회 절감

---

## 4. 크로스커팅 이슈 (프롬프트 간 걸치는 문제)

### 4.1 코드값 정보 단절 — **조치완료** (FM-1)

`{code_mappings}` placeholder 도입으로 Reason 계층의 code_map이 formatter에 동적 주입됨. SQLGlot alias 해소를 통해 SQL 결과 컬럼 관련 코드만 필터링하여 전달.

### 4.2 금액 포맷 이중 처리 — **제외**

analyzer와 formatter는 상호 배타 경로(DATA_ANALYSIS vs DATA_EXTRACTION)이므로 동일 숫자를 다르게 표현하는 상황이 발생하지 않음. 각 경로에서 독립적으로 포맷팅하는 것이 정상.

### 4.3 analyzer에 질의 의도 미전달 — **제외** (AN-2와 동일)

user_input 자체에 "비교해줘", "추이 분석해줘" 등 의도가 포함되어 있으므로 LLM이 충분히 추론 가능. intent enum을 별도 전달하는 것은 과잉 설계.

---

## 5. 토큰 효율 종합

| 프롬프트 | 현재 예시 | 추정 토큰 | 권장 예시 | 절감 가능 |
|---------|----------|----------|----------|----------|
| context_classifier | 15개 | ~3,500 | 8~10개 | ~1,000 |
| normalizer_phase1 | 4개 | ~3,000 | 3개 | ~500 |
| **normalizer_phase2** | **0개** | ~800 | **+2개** | **+300 (증가)** |
| knowledge_interpreter | 2개 | ~2,500 | 2개 | 0 |
| sql_generator | 3개 | ~1,500 | 3개 | 0 |
| sql_validator | 3개 | ~1,200 | 3개 | 0 |
| recovery_agent | 5개 | ~1,500 | 3개 | ~500 |
| **table_comparison** | **0개** | ~300 | **+1개** | **+200 (증가)** |
| **viz_judgment** | **20개** | **~4,000** | **8개** | **~2,500** |
| **viz_svg** | **5개** | **~10,000** | **3개** | **~4,000** |
| **총계** | | ~28,300 | | **~8,000 절감** |

> **폐쇄망 모델(Solar Pro 2 70B) 기준**: 컨텍스트 윈도우가 제한적이므로, 프롬프트 토큰을 8,000 줄이면 입력 데이터와 출력에 더 많은 공간 확보 가능.
> 특히 viz_svg 프롬프트를 React 렌더링으로 대체하면 **15,000 토큰 + LLM 1회 호출**을 절감할 수 있음.

---

## 6. 우선순위별 개선 요약

### 즉시 수정 (상 등급 — 정확성/정합성 문제)

| # | 대상 | 이슈 | 작업 |
|---|------|------|------|
| 1 | sql_generator 예시 1 | 집계 쿼리에 LIMIT 포함 — 규칙 9와 모순 | 규칙 수정 또는 예시 수정 |
| 2 | sql_generator 예시 2 | 시간 조건 없는 질의에 날짜 WHERE 포함 | 예시 입력에 시간 조건 추가 또는 SQL에서 제거 |
| 3 | table_comparison | 예시 0개 + output 구조 불일치 | 예시 1개 추가 + reason을 테이블별로 분리 |
| 4 | normalizer_phase2 | 교차 검증 예시 0개 | 수정 전/후 예시 2개 추가 |
| 5 | context_classifier | 명확화 재입력 예시 부재 | 명확화 이력 있는 예시 1개 추가 — **조치완료** |
| 6 | normalizer_phase1 Q1~Q7 | ~~복합 의도 오분류 위험~~ | ~~제외 — 과도한 지적~~ |

### 중기 개선 (중 등급 — 품질/효율 개선)

| # | 대상 | 이슈 | 작업 |
|---|------|------|------|
| 7 | formatter 코드값 | 하드코딩 5종만 → 동적 주입 필요 | 코드 수정 (플레이스홀더 교체) |
| 8 | viz_judgment 예시 | 20개 → 8개 축소 | 예시 12개 제거 |
| 9 | viz_svg 예시 | 15,000 토큰 소모 | 예시 2개 제거 또는 React 렌더링 전환 검토 |
| 10 | recovery_agent 예시 | 5개 → 3개 축소 | 예시 2개 제거 |
| 11 | context_classifier 예시 | 15개 → 8~10개 축소 | 유사 패턴 예시 통합 |
| 12 | analyzer 의도 미전달 | intent_type 정보가 analyzer에 없음 | user 프롬프트에 intent 섹션 추가 |
| 13 | 금액 포맷 이중 처리 | analyzer/formatter 독립 변환 | 규칙 통일 또는 역할 분리 명확화 |

### 장기 검토 (아키텍처 수준)

| # | 대상 | 이슈 | 작업 |
|---|------|------|------|
| 14 | viz_svg | LLM SVG 생성의 근본적 불안정성 | React 렌더링 전환 검토 |
| 15 | 코드값 정보 단절 | Reason→Present 간 코드 매핑 미전달 | 파이프라인 데이터 흐름 개선 |

---

## 부록: 검토 대상 파일별 등급 요약

| 파일 | 등급 | 핵심 강점 | 핵심 약점 |
|------|------|----------|----------|
| context_classifier_system | B+ | 2단계 동시 판정 구조 | 명확화 예시 부재 |
| normalizer_phase1_system | B | 8-Slot 모델 정밀 | Q1~Q7 복합 의도 함정 |
| normalizer_phase2_system | B- | 교차 검증 규칙 체계적 | 예시 0개 |
| knowledge_interpreter_system | A- | 교차 참조 + 상태 판정 | time_slot 활용 부족 |
| sql_generator_system | A- | 환각 방지, dialect 분기 | 예시-규칙 모순 2건 |
| sql_validator_system | A | 8점 체크리스트 + failure 분류 | 0건 이중 판정 |
| recovery_agent_system | B+ | 도구 우선순위 + 다양한 예시 | action 불일치 가능 |
| table_comparison_system | C+ | 간결 | 예시 0, 출력 불일치, 기준 부족 |
| analyzer_system | B+ | 사고 과정 체계적 | 의도 미전달, 포맷 중복 |
| formatter_system | B | 한국 금융 포맷 적합 | 코드값 하드코딩 |
| viz_judgment_system | B+ | 판정 트리 체계적 | 예시 20개 토큰 과다 |
| viz_svg_system | B- | 정밀 레이아웃 규격 | 토큰 폭탄, 다이어그램 예시 부족 |
