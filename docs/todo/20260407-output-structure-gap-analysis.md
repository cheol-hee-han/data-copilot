# 답변 출력 구조 심층 분석 — 정보 흐름 단절과 사용자 전달 누락

> **작성일**: 2026-04-07
> **관련 문서**: `20260407-formatter-smart-improvement.md` (포맷팅 품질 개선 — 상호 보완)
> **분석 범위**: reasoning → present → WebSocket → UI 전체 데이터 흐름
> **목적**: 사용 가능하지만 활용하지 못하는 정보, 사용자 친화성 부족, 출력 구조 결함 도출

---

## 핵심 문제 요약

ReasoningState에는 8-slot 정규화, 지식항목, 테이블 선택 근거, 비즈 용어 매핑, 유사 SQL 참조, 검증 상세, 실패 경로 등 **풍부한 추론 컨텍스트**가 축적되지만, Present 계층(formatter)은 `user_input + sql_result + code_mappings`만 소비하여 대부분의 정보가 **소실**된다. insight_builder가 일부를 보전하지만 UI 사이드패널(접이식)에만 노출되어 사용자 발견율이 매우 낮다.

---

## 1. State 정보 활용 현황 — 소비 매트릭스

| ReasoningState 필드 | formatter 소비 | insight_builder 소비 | 본문 노출 | insight 패널 노출 |
|---|:---:|:---:|:---:|:---:|
| `validated_sql` | (실행용) | O (sql_code) | X | O |
| `explored_codes` | O (코드매핑) | X | O (코드→한글) | X |
| `inference_notes` | O (면책고지) | X | △ (비구조적) | X |
| `is_force_generated` | O (면책고지) | X | △ | X |
| `query_decomposition` (8-Slot) | **X** | **X** | **X** | **X** |
| `hypotheses` | **X** | **X** | **X** | **X** |
| `knowledge_items` (CONFIRMED) | **X** | △ (manual 건수만) | **X** | △ |
| `explored_tables` (SELECTED) | **X** | O (3단계 분류) | **X** | O |
| `explored_use_cases` | **X** | △ (건수만) | **X** | △ |
| `explored_biz_manuals` | **X** | **X** | **X** | **X** |
| `explored_biz_terms` | **X** | **X** | **X** | **X** |
| `dead_ends` | **X** | O | **X** | O |
| `validation_checks` | **X** | O | **X** | O |
| `exploration_summary` | **X** | O (실패 시) | **X** | O (실패 시) |
| `discovered_facts` | **X** | **X** | **X** | **X** |
| `execution_plan[].insight` | **X** | O (reasoning_trail) | **X** | O |
| `loop_guard` | **X** | O (신뢰도 추정) | **X** | O |
| `resolved_signals` (INFER) | O (상단 안내) | **X** | △ | **X** |

**요약**: 17개 주요 필드 중 formatter가 소비하는 것은 4개(explored_codes, inference_notes, is_force_generated, resolved_signals). 나머지 13개는 insight_builder 경유로만 부분 노출되거나 완전 미사용.

---

## 2. 요구사항별 갭 분석

### 2-1. 데이터 응답 본문 (테이블 + 요약 + 분석 + 시각화)

**현재 동작:**
- formatter: `user_input` + `sql_result` + `code_mappings` → LLM → 보고서 마크다운
- analyzer: `sql_result` + `analysis_query` → LLM → `AnalysisResult` + `VisualizationData`
- WebSocket: viz는 `type: "viz"` 별도 메시지, 텍스트는 `type: "stream"`

**갭:**

| # | 문제 | 근거 위치 | 영향 |
|---|------|-----------|------|
| 1 | **Analyzer↔Formatter 단절** | `formatter.py:67-73` — format_response() 인자에 analysis 관련 파라미터 없음 | formatter LLM이 분석 인사이트를 모르므로 보고서에 분석 맥락 반영 불가 |
| 2 | **분석 인사이트 본문 미포함** | `AnalysisResult.insights`가 formatted_response에 통합되지 않음 | 사용자는 접이식 insight 패널을 클릭해야만 인사이트 확인 가능 |
| 3 | **시각화-텍스트 분리** | `main.py:444-461` — viz 별도 전송, formatter는 viz 정보 미수신 | "위 차트에서 보시듯이..." 같은 연결 문구 불가, 차트와 텍스트가 독립적 |
| 4 | **완료 안내문구 부재** | `formatter_system.txt` — 완료 안내 규칙 없음 | 사용자가 답변 완료 여부를 progress bar 종료로만 인지 |
| 5 | **DATA_EXTRACTION 시 요약 품질** | formatter_system.txt의 "1~2줄 요약" 일률 규칙 | 추이/비교/단건 등 패턴별 차별화된 요약 없음 (→ formatter-smart-improvement.md B/C 참조) |

### 2-2. AI 추론에 따른 모호성 결정사항

**현재 동작:**
- `build_auto_resolved_notice()`: INFER 시그널만 "조회 기준 안내:" 형태로 상단 표시
- `_build_inference_disclaimer()`: recovery_agent의 inference_notes 기반 면책고지

**갭:**

| # | 문제 | 근거 위치 | 영향 |
|---|------|-----------|------|
| 1 | **암묵적 추론 누락** | `clarification_context.py:110-116` — AmbiguitySignal(decision="INFER")만 대상 | reasoning 과정의 자연스러운 결정(예: "예금신규"→"예금신규건수", 명세 필드 선택)은 AmbiguitySignal로 등록되지 않아 사용자 미고지 |
| 2 | **8-Slot 정규화 결과 미노출** | `query_decomposition`이 formatter/insight 어디에도 구조적으로 표시되지 않음 | AI가 질문을 어떻게 분해했는지(기간, 대상, 지표, 필터 등) 사용자 확인 불가 |
| 3 | **knowledge_items 추론 근거 미활용** | KnowledgeItem에 confidence, evidence, source 필드 존재하지만 formatter 미수신 | "왜 이 테이블/컬럼을 선택했는지" 근거를 사용자에게 전달할 수 없음 |
| 4 | **WHERE 조건 코드값 미설명** | `_build_code_mappings()`는 SELECT 컬럼만 필터. formatter LLM은 SQL 미수신 | WHERE에 사용된 코드(예: `대출구분='01'`)의 의미("정상대출만 조회")가 설명되지 않음 |
| 5 | **inference_notes 비구조적** | `list[str]` 형태의 자유 텍스트, recovery_agent가 임의 형식으로 생성 | 무엇이 추론이고 무엇이 확정인지 사용자가 구분할 수 없음 |
| 6 | **INFER 안내와 면책고지 중복/혼재** | `build_auto_resolved_notice`와 `_build_inference_disclaimer`가 독립 실행 | 두 메커니즘이 별도로 상단에 붙어 정보가 산만하고 체계 없음 |

### 2-3. 조회 및 분석 과정 요약정보

**현재 동작:**
- `format_trace_summary()`: trace_log를 `"1. {action}: {detail}"` 형태 plain text 나열
- `<details><summary>조회 과정 요약</summary>` 접기 태그로 본문 하단 첨부
- `build_insight()`: 별도 insight dict → UI 사이드패널

**갭 — 요구사항의 5단계 구조 vs 현재 구현 대비:**

```
요구사항                              현재 구현                    갭
─────────────────────────────────────────────────────────────────────
1. (의도분석) 신규 질의,              trace_log에 "의도분류:       8-slot 정규화 결과,
   데이터추출/분석 요청               DATA_EXTRACTION" 1줄         intent_confidence 미표시

2. (8-slot 정규화)                   query_decomposition이        완전 미노출.
   유형/엔티티/측정값/출력형식/       insight에도 없음              query_interpretation에
   모호성 건수                                                    period/target/metric만
                                                                  부분 매핑

3. (활용지식)                        insight에서:                  - 테이블: 건수만, SQL에
   v 테이블+사유                     - tables_used (이름+사유)       사용한 컬럼 미매핑
   v 참조SQL (돋보기)                - references (건수만)         - 유사SQL: 설명/원문 미제공
   v 코드값                          - explored_codes는            - 매뉴얼/용어: 완전 미표시
   v 매뉴얼                            formatter에만 전달         - 코드: SELECT 컬럼만,
                                                                    WHERE 코드 미표시

4. (SQL 검증)                        insight에서                   - 본문에 미노출
   정규화 정의 반영 확인,            validation_detail로 표시      - 검증 통과 여부만,
   코드값 확인, 의도 부합                                           "의도에 맞는 SQL" 신뢰
                                                                    근거 부족

5. (분석인사이트)                     AnalysisResult 존재하지만     formatter 본문에 미통합,
   분석 관련 추론내용                 formatter가 미소비            insight 패널에서만 접근
```

| # | 문제 | 근거 위치 | 영향 |
|---|------|-----------|------|
| 1 | **trace_summary 구조 부재** | `trace.py:59-63` — `f"{i}. {action}: {detail}"` 나열 | 단순 로그 나열이어서 "왜 이런 판단을 했는지" 맥락 없음 |
| 2 | **insight 패널 접근성** | UI `renderInsight()` — 접이식 사이드패널, 클릭 필요 | 핵심 추론 정보가 숨겨져 대부분 사용자가 보지 않음 |
| 3 | **유사SQL 상세 미제공** | `insight_builder.py:242-247` — `"N건의 유사 쿼리를 참조했습니다"` | 어떤 SQL을 참조했는지 설명/원문 접근 불가 |
| 4 | **테이블 사용 컬럼 미매핑** | `insight_builder.py:137` — `columns_used` fallback `key_columns` | 실제 SQL에서 사용한 컬럼과 매핑되지 않음 |
| 5 | **매뉴얼/용어 완전 미표시** | `explored_biz_manuals`, `explored_biz_terms` — insight_builder에서도 미처리 | 업무 규정/용어 참조 여부를 사용자가 알 수 없음 |
| 6 | **코드값 결정 근거 미표시** | `explored_codes`의 선택 과정 미기록 | "이 코드값을 왜 이 의미로 해석했는지" 근거 없음 |

### 2-4. 실패 시 사용자 친화적 원인 설명

**현재 동작:**
- `result_finalizer._build_failure_output()`: 규칙 기반 실패 내러티브 생성
- `insight_builder._build_failure_narrative()`: exploration_summary > error_message 우선순위
- `_build_dead_end_trail()`: dead_ends를 insight에 포함

**갭:**

| # | 문제 | 근거 위치 | 영향 |
|---|------|-----------|------|
| 1 | **FailureType별 메시지 미분기** | FailureType 9종(NO_TABLE, TERM_UNRESOLVABLE 등) 존재하지만 동일 형태 표시 | "테이블을 찾지 못했습니다" vs "용어를 매핑하지 못했습니다" 차별화 없음 |
| 2 | **exploration_summary 품질 불균일** | result_finalizer의 규칙 기반 생성 — dead_ends/unresolved terms 나열 | 내부 디버깅 정보 성격, 사용자 언어가 아님 |
| 3 | **부분 성공 정보 미제공** | 실패 시에도 explored_tables, knowledge_items에 찾은 정보 존재 | "예금 잔액 테이블은 찾았으나 신규 금액 컬럼 없음" 같은 안내 불가 |
| 4 | **대안 질문 제시 부재** | 실패 응답에 재시도 가이드 없음 | "이런 질문으로 다시 시도해보세요" 체계적 제공 없음 |
| 5 | **dead_end 교훈이 내부 메모** | DeadEnd.lessons_learned는 recovery_agent 관점 생성 | "다음 시도에서 X를 피해야 한다" — 사용자 대상 언어가 아님 |
| 6 | **UI retry 맹목적** | 프론트엔드 "다시 시도" 버튼 — 동일 질문 재실행 | 실패 원인 반영한 수정 질문 제안 없음 |

---

## 3. 추가 발견 — 사용자에게 필요하지만 현재 미제공 정보

### 3-1. 데이터 신뢰도/주의사항

| 항목 | 현재 | 필요한 개선 |
|------|------|-------------|
| **결과 절삭 이중 경고** | `(총 N건 중 상위 100건 표시)` 텍스트만 | `max_query_rows`(DB 레벨)와 `format_max_rows`(LLM 레벨) 두 단계 절삭 구분 불가. "전체 N건 중 M건을 표시합니다. 전체 데이터는 다운로드하세요." |
| **NULL 값 처리** | `None → ""` (빈 문자열) | 금융 데이터에서 NULL은 "미등록"/"해당없음"/"0"이 다름. "일부 항목이 비어있습니다" 안내 필요 |
| **데이터 기준일** | 미표시 | 정보계 DB는 T+1/T+2 배치 갱신이 일반적. "N월 N일 기준 데이터입니다" 안내 필요 |
| **집계 기준 명시** | formatter LLM 재량 | SUM/AVG/COUNT 등 집계 방식이 명시적으로 설명되지 않을 수 있음 |

### 3-2. 조인 데이터 정합성 경고

- `caveats`에 "다중 테이블 사용 — 조인 조건은 LLM이 컬럼명으로 추론했습니다"가 있지만 **insight 패널에만** 존재 (`insight_builder.py:409`)
- 본문에 미표시되어 사용자가 조인 추론의 위험성을 인지 불가
- 조인으로 인한 데이터 증폭/누락 가능성 경고 없음

### 3-3. 코드값 변환 투명성

- `_build_code_mappings()`은 SELECT 컬럼만 필터 (`formatter.py:200-206`)
- WHERE 조건의 코드값(예: `WHERE 대출구분 = '01'`)은 formatter LLM이 SQL 자체를 보지 못하므로 변환/설명 불가
- 사용자는 "어떤 조건으로 필터링되었는지" 코드 레벨에서 확인할 방법 없음

### 3-4. 실행 성능 투명성

- `execution_time_ms`, `step_timings`가 insight에 존재하지만 본문 미표시
- 장시간 소요 시 사용자가 "왜 오래 걸렸는지" 확인 불가
- 복잡한 쿼리(다중 조인, 대량 데이터)의 경우 성능 경고 필요

---

## 4. 정보 흐름 단절 지도

```
┌─ interpret ─────────────────────────────────────────────────────┐
│  normalized_query (8-Slot)                                      │
│    → query_decomposition (dict)  ──→ [formatter: 미사용]         │
│  intent, intent_confidence        ──→ [formatter: 미사용]         │
│  resolved_signals (INFER)         ──→ [formatter: 부분사용]       │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─ reason ────────────────────────────────────────────────────────┐
│  knowledge_items (CONFIRMED)      ──→ [formatter: 미사용]         │
│  explored_tables (SELECTED+이유)  ──→ [insight만, 본문X]          │
│  explored_use_cases (참조SQL)     ──→ [insight만, 건수만]         │
│  explored_biz_terms (용어매핑)    ──→ [미사용]                    │
│  explored_biz_manuals             ──→ [미사용]                    │
│  explored_codes                   ──→ [formatter: SELECT 컬럼만]  │
│  validation_checks (검증상세)     ──→ [insight만, 본문X]          │
│  dead_ends (실패경로+교훈)        ──→ [insight만, 본문X]          │
│  inference_notes                  ──→ [formatter: 비구조적]       │
│  exploration_summary              ──→ [미사용]                    │
│  discovered_facts                 ──→ [미사용]                    │
│  execution_plan[].insight         ──→ [insight만]                 │
│  loop_guard (재시도 횟수)         ──→ [insight: 신뢰도만]         │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─ present ───────────────────────────────────────────────────────┐
│  sql_result                       ──→ [formatter: O]              │
│  analysis_result (분석인사이트)    ──→ [formatter: 미사용!]        │
│  visualization (SVG)              ──→ [WebSocket: 별도전송]       │
│  formatted_response               ──→ [WebSocket: 스트리밍]      │
│  insight (insight_builder 결과)   ──→ [WebSocket: 사이드패널]     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 개선 방향 제안

### 5-1. 데이터 응답 본문 강화

| 개선 | 내용 | 변경 대상 |
|------|------|-----------|
| analysis_result 주입 | analyzer 결과(summary, insights)를 formatter 프롬프트에 `[분석 요약]` 섹션으로 추가 | `formatter.py`, `formatter_user.txt` |
| 완료 안내문구 | "조회 완료 + 건수 + 조건 요약" 규칙 추가 | `formatter_system.txt` |
| 시각화 참조 연결 | formatter에 `viz_type`/`viz_title` 전달, "아래 차트를 참고하세요" 연결 | `formatter.py`, `formatter_system.txt` |

### 5-2. AI 추론 결정사항 투명화

| 개선 | 내용 | 변경 대상 |
|------|------|-----------|
| 8-Slot 정규화 노출 | `query_decomposition`을 본문 상단에 구조화 표시. 예: "분석 기준: 기간=2024년 3월, 대상=예금, 지표=신규건수" | `formatter.py`, 프롬프트 |
| 추론 근거 통합 | INFER 안내 + inference_notes + knowledge_items(confidence<1.0)를 하나의 "AI 판단 사항" 섹션으로 통합 | `formatter.py` |
| WHERE 코드값 설명 | SQL WHERE 절의 코드값을 explored_codes에서 매핑하여 "정상대출(01)만 조회했습니다" 안내 | `formatter.py`, `sqlglot_analyzer` |

### 5-3. 조회 과정 요약 구조화

| 개선 | 내용 | 변경 대상 |
|------|------|-----------|
| 5단계 구조화 | 의도분석 → 정규화 → 활용지식 → SQL검증 → 분석인사이트 구조로 재설계 | `format_trace_summary` 또는 신규 빌더 |
| 활용지식 상세화 | 테이블(이름+사유), 참조SQL(설명+접이식 원문), 코드매핑, 매뉴얼/용어 참조 여부 | `insight_builder.py` 확장 |
| 본문 통합 배치 | `<details>` 접기 대신 본문 하단 구조화 섹션 또는 UI 별도 탭 | `formatter.py`, 프론트엔드 |

### 5-4. 실패 원인 사용자 친화화

| 개선 | 내용 | 변경 대상 |
|------|------|-----------|
| FailureType별 메시지 | 9종 실패 유형별 사용자 친화 메시지 템플릿 | 신규 매핑 테이블 |
| 부분 성공 표시 | "찾은 것 / 찾지 못한 것" 분리 표시 | `result_finalizer`, `formatter.py` |
| 대안 질문 제시 | 실패 원인 기반 수정된 질문 예시 생성 | `result_finalizer` 또는 전용 LLM 호출 |
| exploration_summary 재작성 | 내부 디버깅 텍스트를 사용자 언어로 LLM 재가공 | `formatter.py` 실패 분기 |

### 5-5. 데이터 신뢰도 안내 추가

| 개선 | 내용 | 변경 대상 |
|------|------|-----------|
| 결과 절삭 명확화 | DB 절삭/표시 절삭 구분, 다운로드 안내 | `formatter.py`, `response_formatter.py` |
| NULL 경고 | NULL 비율 높을 시 "일부 항목이 비어있습니다" 안내 | `formatter.py` |
| 데이터 기준일 | 정보계 DB 갱신 주기 기반 "N일 기준 데이터" 안내 | 설정 기반 + `formatter.py` |
| 조인 신뢰도 경고 | 다중 테이블 조인 시 본문에 주의사항 표시 | `formatter.py` (caveats 본문 통합) |

---

## 6. 관련 파일

| 용도 | 파일 경로 |
|------|-----------|
| Formatter 노드 | `src/agents/nodes/present/formatter.py` |
| Formatter 서비스 | `src/services/response_formatter.py` |
| Formatter 시스템 프롬프트 | `resources/prompts/present/formatter_system.txt` |
| Formatter 유저 프롬프트 | `resources/prompts/present/formatter_user.txt` |
| Insight 빌더 | `src/services/insight_builder.py` |
| Trace 모델/서식 | `src/models/trace.py` |
| 명확화 컨텍스트 | `src/agents/utils/clarification_context.py` |
| Result Finalizer | `src/agents/nodes/reason/result_finalizer.py` |
| PipelineResult 조립 | `src/agents/graph/runner.py` (_build_result) |
| WebSocket 전송 | `src/main.py` (_run_ws_pipeline) |
| UI 렌더링 | `static/embedded.html` (renderInsight, renderViz) |
| State 정의 | `src/agents/state/state.py` |
| 응답 모델 | `src/agents/models/response.py` |
| 결과 모델 | `src/models/result.py` |
| 포맷팅 품질 개선 (관련) | `docs/todo/20260407-formatter-smart-improvement.md` |
