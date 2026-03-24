# Data Copilot — LangGraph 에이전트 아키텍처 설계 문서

> 은행 임직원의 자연어 데이터 추출/분석 요청을 LangGraph 파이프라인(Pipeline)으로 처리하는 AI 에이전트의 전체 구조, 컴포넌트 간 관계, 데이터 흐름을 정의한다.

**버전**: 1.2
**최종 수정**: 2026-03-19
**대상 독자**: 본 프로젝트의 설계·구현·운영에 참여하는 모든 구성원 및 AI 서브에이전트(Sub-Agent)

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [LangGraph 그래프 설계](#2-langgraph-그래프-설계)
3. [정확한 요구사항 분석을 위한 설계 아이디어](#3-정확한-요구사항-분석을-위한-설계-아이디어)
4. [데이터 정합성 보장을 위한 설계 아이디어](#4-데이터-정합성-보장을-위한-설계-아이디어)
5. [노드별 상세 설계](#5-노드별-상세-설계)
6. [커넥터 아키텍처](#6-커넥터-아키텍처)
7. [향후 고도화 방향](#7-향후-고도화-방향)

---

## 1. 시스템 개요

Data Copilot은 은행 임직원이 **자연어로 데이터 추출/분석을 요청**하면,
사내 다양한 참조 정보를 기반으로 SQL을 생성하여 데이터를 추출하거나
데이터 기반 분석 결과를 반환하는 **LangGraph 기반 AI 에이전트**이다.

```
┌─────────────────────────────────────────────────────────────┐
│                    사용자 (은행 직원)                        │
│               "이번 달 신규 고객 수 알려줘"                 │
└────────────────────────┬────────────────────────────────────┘
                         │ WebSocket / REST API
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                FastAPI 서버 (server.py)                      │
│         프롬프트 인젝션 감지 · PII 마스킹 · 세션 관리       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│            LangGraph 파이프라인 (pipeline.py)                │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  ┌──────────┐  │
│  │입력전처리│→│ 의도분류 │→│컨텍스트수집│→│ SQL 생성 │  │
│  └──────────┘  └────┬─────┘  └────────────┘  └─────┬────┘  │
│                     │                               │       │
│            ┌────────┴────┐              ┌───────────▼────┐  │
│            │ 명확화 질문 │              │   SQL 검증     │  │
│            │ (모호한요청)│              │ (보안+구문+PII)│  │
│            └─────────────┘              └───────────┬────┘  │
│                                                     │       │
│                                          ┌──────────▼────┐  │
│                                          │   SQL 실행    │  │
│                                          │  (정보계 DB)  │  │
│                                          └──────────┬────┘  │
│                                                     │       │
│                                          ┌──────────┴────┐  │
│                                          │  분석 필요?   │  │
│                                          ├─YES→ 분석노드 │  │
│                                          └─NO──→ 포맷팅  │  │
│                                                     │       │
│                                          ┌──────────▼────┐  │
│                                          │ 결과 포맷팅   │  │
│                                          │ (보고서 형태) │  │
│                                          └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   ┌────────────┐ ┌────────────┐ ┌────────────┐
   │ElasticSearch│ │ PostgreSQL │ │  Qdrant    │
   │ 메타·보고서│ │ 정보계·이력│ │ 업무매뉴얼 │
   └────────────┘ └────────────┘ └────────────┘
```

---

## 2. LangGraph 그래프 설계

### 2.1 설계 원칙

| 원칙 | 설명 |
|------|------|
| **단일 공유 상태** | `PipelineState` 하나를 모든 노드(Node)가 읽고 쓰며, 노드 간 데이터 전달 문제를 원천 차단한다 |
| **조건부 분기** | 의도 분류·SQL 검증·분석 여부에 따라 4곳에서 동적 라우팅한다 |
| **Fail-fast** | 전처리·SQL 검증 실패 시 즉시 `error_end`로 분기하여 불필요한 LLM 호출을 방지한다 |
| **노드 독립성** | 각 노드는 순수 함수(입력 State → 출력 dict)로 구현하여 단위 테스트가 가능하다 |
| **커넥터 추상화** | Dummy/실제 모드를 설정만으로 전환할 수 있다 (폐쇄망 배포 대비) |
| **LLM 프로바이더 추상화** | `UnifiedLLMClient`가 Anthropic/OpenAI 호환 API를 동일 인터페이스로 래핑한다 |

### 2.2 그래프 정의 (StateGraph)

```python
# src/agents/graph/pipeline.py:158-172 — 핵심 구조

workflow = StateGraph(PipelineState)

# 10개 노드 등록
workflow.add_node("preprocess",       preprocess_node)        # 입력 정규화 + 인젝션 감지
workflow.add_node("classify_intent",  classify_intent_node)   # LLM 의도 분류
workflow.add_node("clarify",          clarify_node)           # 명확화 질문 생성
workflow.add_node("collect_context",  collect_context_node)   # 5개 소스 컨텍스트 수집
workflow.add_node("generate_sql",     generate_sql_node)      # LLM SQL 생성
workflow.add_node("validate_sql",     validate_sql_node)      # 보안·구문·PII 검증
workflow.add_node("execute_sql",      execute_sql_node)       # DB 쿼리 실행
workflow.add_node("analyze_data",     analyze_data_node)      # LLM 데이터 분석
workflow.add_node("format_response",  format_response_node)   # 보고서 포맷팅
workflow.add_node("error_end",        _handle_error)          # 에러 메시지 생성
```

### 2.3 조건부 분기 (Conditional Edges)

```
                    ┌─ INJECT_DETECTED ─→ error_end → END
   preprocess ──────┤
                    └─ OK ─────→ classify_intent
                                     │
                    ┌─ clarification_needed ──→ clarify → END
   classify_intent ─┤─ general_question ─────→ clarify → END
                    └─ data_extraction ──────→ collect_context
                      data_analysis ─────────→ collect_context
                                                    │
                      collect_context ─→ generate_sql ─→ validate_sql
                                                              │
                           ┌─ VALIDATION_PASSED ─→ execute_sql
                           │                            │
      validate_sql ────────┤─ AMBIGUOUS ─────────→ clarify → END
                           │
                           └─ VALIDATION_FAILED
                               & retry < 2 ──→ generate_sql
                               & retry >= 2 ─→ error_end → END
                                                    │
                           ┌─ data_analysis ─→ analyze_data
      execute_sql ─────────┤                       │
                           │                 format_response → END
                           └─ data_extraction ──→ format_response → END
```

**분기 포인트 4곳:**

1. **전처리 후** (`_route_after_preprocess`) — SQL 인젝션·프롬프트 인젝션 감지 시 즉시 차단한다
2. **의도 분류 후** (`_route_after_intent`) — 모호한 요청은 명확화(Clarification) 경로로, 나머지는 SQL 생성 경로로 분기한다. 명확화 횟수가 최대치(2회)를 초과하면 현재 입력으로 컨텍스트 수집(Context Collection)을 시도한다
3. **SQL 검증 후** (`_route_after_validation`) — 3가지 경로: 통과 → 실행, 실패 & 재시도 가능 → 재생성 루프(최대 2회), 테이블 모호 → 명확화 분기
4. **SQL 실행 후** (`_route_after_execution`) — 분석 의도면 분석 노드 경유, 추출 의도면 바로 포맷팅

**SQL 재생성 루프 상세:**

- `validate_sql` 검증 실패 시 `validation_feedback`에 실패 원인과 실패한 SQL을 기록한다
- `generate_sql` 재진입 시 `sql_retry_count`를 증가시키고 `validation_feedback`을 프롬프트에 주입한다
- LLM이 이전 실패 원인을 인지한 채로 SQL을 재생성한다
- 최대 2회(`SQL_MAX_RETRY`) 재시도 후에도 실패하면 `error_end`로 분기한다

**멀티턴 명확화 상세:**

- `clarify_node`가 `awaiting_clarification=True`로 설정하고 END한다
- 챗봇 레이어가 사용자 응답을 `clarification_response`에 채워 파이프라인을 재실행한다
- `preprocess_node`가 원래 질의와 명확화 응답을 `"[원래 질의]\n추가 조건: [응답]"` 형태로 합성한다
- `clarification_turns`를 증가시키고 대화 이력(conversation_history)에 왕복 기록을 추가한다
- 최대 2회(`CLARIFICATION_MAX_TURNS`) 왕복 후에도 모호하면 현재 입력으로 진행한다

### 2.4 공유 상태 (PipelineState)

```python
# src/agents/state/state.py:119-184

class PipelineState(BaseModel):
    # ── 입력 ──
    user_input: str                          # 원본 사용자 입력
    session_id: str                          # 세션 추적용
    conversation_history: list[dict]         # 멀티턴 대화 이력

    # ── 전처리 ──
    preprocessed_input: str                  # 정규화된 입력

    # ── 의도 분류 ──
    intent: IntentType                       # 5가지 의도 중 하나
    intent_confidence: float                 # 분류 신뢰도 (0.0~1.0)

    # ── 명확화 (멀티턴) ──
    clarification_question: str              # 사용자에게 보낼 질문
    clarification_response: str              # 사용자 응답 (멀티턴)
    awaiting_clarification: bool             # 명확화 응답 대기 중 여부
    clarification_turns: int                 # 명확화 왕복 횟수 (최대 2회)

    # ── 컨텍스트 ──
    context: ContextInfo                     # 5개 소스에서 수집한 참조 정보
    #   ├─ table_metas: list[TableMeta]      # ES 메타 → 테이블/컬럼 정의
    #   │    └─ enriched_description: str    # LLM 보강된 상세 설명
    #   ├─ past_sqls: list[str]              # 이력 DB → 과거 유사 SQL
    #   ├─ report_sqls: list[str]            # ES 보고서 → 보고서 SQL
    #   ├─ manual_references: list[str]      # Qdrant → 업무 매뉴얼
    #   ├─ domain_terms: dict[str, str]      # 도메인 용어 매핑
    #   └─ table_disambiguation_guide: str   # 유사 테이블 구분 가이드

    # ── SQL 생성 및 검증 ──
    generated_sql: str                       # LLM이 생성한 SQL
    validated_sql: str                       # 검증 통과한 SQL
    sql_validation_errors: list[str]         # 검증 실패 사유
    sql_retry_count: int                     # SQL 재생성 시도 횟수 (최대 2회)
    validation_feedback: str                 # 검증 실패 내용 → 재생성 프롬프트 주입

    # ── 테이블 선택 검증 ──
    table_selection_verdict: str             # pass/warning/ambiguous
    table_selection_warnings: list[str]      # 유사 테이블 경고 메시지

    # ── 실행 결과 ──
    sql_result: SQLResult                    # 컬럼, 행, 건수, 실행시간

    # ── 분석 결과 ──
    analysis_result: AnalysisResult          # 요약, 인사이트, 통계, 시각화
    #   ├─ summary: str                      # 분석 요약
    #   ├─ insights: list[str]               # 주요 인사이트
    #   ├─ statistics: dict[str, Any]        # 핵심 지표
    #   ├─ visualization_code: str           # SVG 차트 코드
    #   ├─ visualization_type: VisualizationType  # 차트 유형
    #   └─ visualization_title: str          # 차트 제목

    # ── 최종 출력 ──
    formatted_response: str                  # 사용자에게 보여줄 최종 응답

    # ── 상태 관리 ──
    status: QueryStatus                      # 현재 처리 단계 (13단계)
    error_message: str                       # 에러 발생 시 메시지

    # ── 추론 추적 ──
    trace_log: list[TraceEntry]              # 노드별 추론 과정 기록
```

---

## 3. 정확한 요구사항 분석을 위한 설계 아이디어

요구사항에 명시된 **"정확한 분석"** 을 달성하기 위해 적용한 핵심 아이디어 7가지를 기술한다.

### 3.1 다중 소스 컨텍스트 수집 (Multi-Source RAG)

```
사용자 질의: "이번 달 담보대출 연체 현황 보여줘"
                    │
    ┌───────────────┼──────────────────────────┐
    ▼               ▼               ▼          ▼
 ES 메타검색     보고서 SQL       과거 SQL    Qdrant
    │               │               │          │
 TB_LOAN_INFO    연체율 추이      유사 SQL    연체 관리
 컬럼 정의       보고서 SQL       검증된 패턴 분류기준
    │               │               │          │
    └───────────────┴───────────────┴──────────┘
                    │
            ContextInfo (통합)
                    │
            SQL 생성 프롬프트에 전부 주입
```

**왜 이렇게 했는가:**

- 단일 소스(예: 테이블 메타만)로는 불완전한 IT 메타를 보완할 수 없다
- 과거 SQL 이력은 **"이미 검증된 패턴"** 을 제공하여 LLM의 환각을 줄인다
- 보고서 SQL은 **복잡한 계수산출식**(연체율, BIS비율 등)의 정확한 산출 방법을 제공한다
- 업무 매뉴얼은 **업무 규정**(연체 분류 기준 등)을 제공하여 조건식 정확도를 높인다

### 3.1.1 검색 쿼리 전략 (SearchQueryBuilder) — 2026-03-20 추가

기존에 `preprocessed_input`을 4개 소스에 동일하게 전달하던 구조에서,
**소스별 특화 쿼리를 생성하는 전략 계층**을 추가하였다.

```
src/services/search_query_builder.py

preprocessed_input
  ├─ Step 1: 도메인 용어 매칭 (150+개 금융 용어 사전)
  ├─ Step 2: 구조화 엔티티 추출 (테이블명, 컬럼명, 카테고리)
  ├─ Step 3: 불용어 제거 (조사·어미·요청동사 60+개)
  ├─ Step 4: 동의어 확장 ("여신"→"대출","론","대여금")
  ├─ Step 5: 유사 테이블 신호어 수집
  └─ Step 6: 소스별 쿼리 특화
       ├─ ES table:   domain_cd 주입 + 테이블명 부스트 + 시간어 제거
       ├─ ES report:  시간 표현 제거 + 카테고리 보강
       ├─ History DB:  핵심 키워드 + 동의어 확장 + 테이블명 (15개 제한)
       └─ Qdrant:      원본 유지 + 도메인 설명 보강 (벡터 의미 강화)
```

**domain_cd 주입**: ES table_meta의 `table_name`이 keyword 타입이라 부분 검색이 불가하므로,
카테고리에서 추론한 `domain_cd`(LON, DEP, CUS, CRD, TRX 등)를 쿼리 선두에 주입하여
535개 테이블에서 도메인 필터링 효과를 얻는다.

**검증 결과 (골든셋 90건 E2E)**:
- ES table_meta: 98.9% (89/90)
- Qdrant sql_history: 85.6% (77/90)
- Qdrant biz_manual: 88.9% (80/90)
- 종합: 91.1% (246/270)

### 3.2 금융 도메인 사전 (Domain Dictionary)

```python
# src/services/domain/finance_terms.py

DomainTerm(
    term="담보대출",
    aliases=["담보여신", "유담보대출", "주담대", "주택담보대출"],
    table_name="TB_LOAN_INFO",
    column_name="LOAN_TYPE_CD",
    condition="LOAN_TYPE_CD = '02'",
    category="여신",
)
```

**해결하는 문제:**

| 문제 | 도메인 사전의 해결 방식 |
|------|----------------------|
| 사용자가 "주담대"라고 말하면? | aliases로 "담보대출"과 동일하게 인식한다 |
| 코드값 '02'가 뭔지 LLM이 모르면? | condition으로 정확한 WHERE절을 직접 제공한다 |
| 어떤 테이블을 써야 하는지 모호하면? | table_name으로 테이블을 사전에 지정한다 |
| "이번 달"이 SQL에서 어떻게 표현? | 시간 용어도 사전에 포함한다 (DATE_TRUNC 패턴) |

**흐름:**

```
사용자 입력 → lookup_terms() → 매칭된 DomainTerm 목록
                                      │
                          format_domain_context()
                                      │
                                      ▼
            SQL 생성 프롬프트의 "매칭된 도메인 용어"
            섹션에 주입
```

현재 **9개 카테고리, 150+개 용어**를 등록하고 있으며,
각 용어에는 **동의어(aliases)**, **테이블/컬럼 매핑**, **SQL 조건식**이 포함되어 있어
LLM이 코드값이나 테이블명을 추론하지 않고 **사전에서 정확한 값을 받아 사용**한다.

### 3.3 유사 테이블 구분 전략

정보계(Informational DB) DB에는 유사 도메인의 테이블이 다수 존재하는 문제가 있다.
(예: TB_LOAN_INFO vs TB_LOAN_OVERDUE_STAT — 둘 다 "대출 연체" 관련)

**적용한 구분 전략:**

1. **테이블 메타에 갱신주기 포함** — "일배치" vs "월배치" vs "실시간"으로 용도를 구분한다
2. **테이블 설명에 용도 명시** — "월말 기준 집계" 같은 힌트를 프롬프트에 전달한다
3. **보고서 SQL 참조** — 기존 보고서가 사용하는 테이블이 가장 신뢰도 높다
4. **도메인 사전의 table_name** — "연체율" 용어는 TB_LOAN_OVERDUE_STAT으로 직접 매핑한다

```
"연체율 추이 보여줘"
    │
    ├─ 도메인 사전: "연체율" → TB_LOAN_OVERDUE_STAT
    ├─ 보고서 SQL: "연체율 추이" 보고서 → 해당 테이블
    └─ ES 메타: 두 테이블 모두 반환, 설명+갱신주기로
       LLM이 판단
```

### 3.4 불완전한 IT 메타 보완 전략

행내에는 테이블/컬럼 설명이 불충분한 경우가 많다. 이를 보완하기 위한 다층 추론 전략을 적용한다.

```
                컬럼 설명이 불충분한 경우
                       │
    ┌──────────────────┼──────────────────┐
    ▼                  ▼                  ▼
1순위: 보고서 SQL  2순위: 과거 SQL    3순위: 컬럼명 패턴
해당 컬럼이 어떤   유사 요청에서      _CD → 코드
조건/집계로 사용   이 컬럼을 어떻게   _DT → 일자
되었는지 참조      사용했는지 참조    _AMT → 금액
    │                  │               _YN → Y/N 플래그
    └──────────────────┴───────────────────┘
                       │
          프롬프트에 모든 참조 정보를 주입하여
          LLM이 종합적으로 판단하도록 함
```

**코드 메타 자동 매핑:**

```python
# ES에서 코드 메타를 검색하여 도메인 용어에 자동 추가
# "01" → "신용대출", "02" → "담보대출" 등의 매핑을
# SQL 생성 프롬프트에 직접 주입
for code_val, code_desc in codes.items():
    domain_terms[code_desc] = f"{field} = '{code_val}'"
```

#### 3.4.1 테이블 설명 자동 보강 (Table Description Enrichment)

테이블 설명(table_description)은 보통 1~2줄의 엔티티 집합 정의만 되어 있어
SQL 생성 시 테이블의 용도와 특성을 정확히 파악하기 어렵다.
이를 해결하기 위해 **테이블 설명 자동 보강** 단계를 컨텍스트 수집에 추가했다.

**좋은 테이블 설명의 세 가지 관점:**

| 관점 | 설명 | 예시 |
|------|------|------|
| 엔티티 집합 정의 | 테이블에 어떤 데이터가 있는지 | "고객별 개별 대출 건의 현재 상태를 저장" |
| 기능적 정의 | 데이터가 어디에 어떻게 쓰이는지 | "여신 업무 전반에서 기본 참조 데이터로 활용" |
| 데이터 발생규칙 | 데이터가 언제 생성되어 적재되는지 | "일배치로 갱신, 대출 실행 시 행 생성" |

**보강 흐름:**

```
 컨텍스트 수집 (search_context_assembler.py)
     │
     ├─ [1] ES 메타에서 테이블 메타 수집
     │
     ├─ [2] 테이블 설명 보강 (table_meta_enricher.py)
     │       │
     │       ├─ 충분성 판단: 길이 ≥ 20자 AND
     │       │   3관점 키워드 포함?
     │       │   ├─ YES → 보강 생략
     │       │   └─ NO  → LLM 보강 호출 (병렬)
     │       │
     │       └─ 보조 정보 수집:
     │           ├─ 컬럼 정보 (이름, 타입, 설명, PII)
     │           ├─ 해당 테이블 참조 보고서 SQL
     │           └─ 해당 테이블 참조 과거 SQL
     │
     ├─ [3] 유사 테이블 그룹 감지
     │
     └─ ContextInfo 반환 (enriched_description 포함)
```

**SQL 생성 프롬프트에서의 활용:**

```markdown
### TB_LOAN_INFO - 여신(대출) 정보 테이블
[상세 설명] 고객별 개별 대출 건의 현재 상태를 저장하는
테이블로, 한 행이 하나의 대출 계약을 나타낸다. 대출
유형별 실행 현황 조회, 연체 관리 등 여신 업무 전반에서
기본 참조 데이터로 활용된다. 일배치로 매일 갱신되며,
대출 실행 시 행이 생성된다.
갱신주기: 일배치
컬럼: ...
```

**설계 결정:**

- 보강은 컨텍스트 수집 단계에서 수행한다 (SQL 생성 전)
- 불충분한 테이블만 선별하여 LLM을 호출한다 → 토큰 비용 최소화
- 여러 테이블을 `asyncio.gather`로 병렬 보강한다 → 지연 시간 최소화
- LLM 실패 시 원본 설명을 유지한다 → fail-safe

### 3.5 계수산출식 추론 전략

금융 지표(연체율, BIS비율 등)는 정확한 산출식이 필수이다.

**추론 경로 (우선순위):**

```
"연체율 보여줘"
    │
    ▼
1순위: 도메인 사전
    └─ "연체율" → description에 산출식 포함
    │
    ▼ (사전에 없는 경우)
2순위: 업무 매뉴얼 (Qdrant)
    └─ "연체 관리 기준" 문서에서 산출식 확인
    │
    ▼ (매뉴얼에도 없는 경우)
3순위: 보고서 SQL (ES)
    └─ "연체율 추이" 보고서 SQL에서 산출식 역추출
    │
    ▼ (모든 소스에서 확인 불가)
4순위: 사용자에게 확인 요청 (명확화 질문)
    └─ "연체율 산출 방식을 확인해주시겠어요?
        1) 연체금액 / 총 대출금액
        2) 연체건수 / 총 대출건수
        3) 다른 산출 방식"
```

### 3.6 의도 분류의 세분화

단순 "추출/분석" 이분법이 아닌, **5가지 의도 + 신뢰도 기반** 분류를 수행한다:

```python
# src/agents/state/state.py:12-19

class IntentType(str, Enum):
    DATA_EXTRACTION = "data_extraction"        # "~건수", "~금액", "~뽑아줘"
    DATA_ANALYSIS = "data_analysis"            # "~분석", "~비교", "~추이"
    CLARIFICATION_NEEDED = "clarification_needed"  # "데이터 뽑아줘" (모호)
    GENERAL_QUESTION = "general_question"      # "여신 심사 절차 알려줘"
    UNKNOWN = "unknown"                        # 파싱 실패 폴백
```

**핵심:** `intent_confidence`가 낮으면(< 0.7) 추측하지 않고 명확화 질문으로 분기한다.
이는 **"틀린 SQL을 생성하느니 질문하는 게 낫다"** 는 설계 철학을 반영한다.

### 3.7 SQL 생성 프롬프트의 다층 컨텍스트 주입

SQL 생성 시 LLM에 제공하는 프롬프트 구조:

```
┌──────────────────────────────────────────────┐
│           시스템 프롬프트 구성                 │
├──────────────────────────────────────────────┤
│  1. 절대 규칙 (10개)                          │
│     SELECT 전용, 단일 쿼리, PII 보호 등       │
│                                              │
│  2. 테이블 정보 (ES 메타 + LLM 보강)          │
│     테이블명, 원본 설명, 갱신주기              │
│     [상세 설명] LLM 보강 3관점 설명            │
│     컬럼명, 타입, 설명, PII 여부               │
│                                              │
│  3. 보고서 SQL (ES 보고서 저장소)              │
│     유사 보고서의 검증된 SQL                    │
│                                              │
│  4. 과거 SQL 이력 (이력 DB)                   │
│     유사 요청에 사용된 기존 SQL                 │
│                                              │
│  5. 업무 매뉴얼 (Qdrant)                     │
│     관련 업무 규정, 산출식, 프로세스             │
│                                              │
│  6. 매칭된 도메인 용어 (도메인 사전)            │
│     용어 → 테이블, 컬럼, SQL 조건식 매핑        │
│                                              │
│  7. 도메인 용어 매핑 (코드 메타 포함)           │
│     "신용대출" → LOAN_TYPE_CD = '01'           │
│     "이번 달" → >= DATE_TRUNC(...)             │
│                                              │
│  8. 유사 테이블 구분 가이드                    │
│     유사 테이블 그룹별 구분 기준과 신호어        │
│                                              │
│  9. 검증 피드백 (재생성 시에만)                │
│     이전 SQL의 검증 실패 원인과 수정 지시        │
└──────────────────────────────────────────────┘
                    +
┌──────────────────────────────────────────────┐
│           사용자 메시지                        │
│  "이번 달 담보대출 연체 현황 보여줘"            │
└──────────────────────────────────────────────┘
                    ↓
               LLM 응답: 순수 SQL
```

---

## 4. 데이터 정합성 보장을 위한 설계 아이디어

### 4.1 3중 SQL 검증 체계

```
        사용자 입력
            │
    ┌───────▼──────────┐
    │  1차: 입력 전처리  │  SQL/프롬프트 인젝션 감지
    │  (preprocessor)   │  11개 SQL 패턴 + 프롬프트 패턴
    └───────┬──────────┘
            │
    ┌───────▼──────────┐
    │  2차: SQL 검증    │  ← 핵심 검증 단계
    │  (sql_validator)  │
    │                   │  a) 금지 패턴 검사 (17개 패턴)
    │                   │  b) sqlglot 구문 파싱 (PostgreSQL)
    │                   │  c) PII 컬럼 직접 노출 검사
    │                   │  d) LIMIT 존재 확인 (집계 예외)
    │                   │  e) 시스템 카탈로그 접근 차단
    │                   │  f) 테이블 적절성 검증
    └───────┬──────────┘
            │
    ┌───────▼──────────┐
    │  3차: 커넥터 검증  │  실행 직전 SELECT 재확인
    │  (InfoDBConnector)│  CTE(WITH) 시작도 허용
    └───────┬──────────┘
            │
            ▼
       DB 실행 (읽기 전용 계정)
```

### 4.2 골든셋(Golden Set) 기반 다차원 정확도 평가

SQL 정합성을 **4개 차원**으로 측정한다:

```
┌─────────────────────────────────────────────┐
│              평가 차원 4가지                  │
├───────────────┬─────────────────────────────┤
│ 1. 의도 분류  │ 사용자 요청의 의도를 정확히   │
│   (intent)    │ 파악했는가?                   │
├───────────────┼─────────────────────────────┤
│ 2. 테이블 선택│ 올바른 테이블을 사용했는가?    │
│   (table)     │                             │
├───────────────┼─────────────────────────────┤
│ 3. SQL 패턴   │ 올바른 집계/조건/조인          │
│   (pattern)   │ 구조인가?                    │
├───────────────┼─────────────────────────────┤
│ 4. SQL 구문   │ 유효한 SQL 문법인가?          │
│   (syntax)    │ (sqlglot 파싱)               │
└───────────────┴─────────────────────────────┘

종합 판정:
  의도 ✓ AND 테이블 ✓ AND (패턴 ✓ OR 구문 ✓) = PASS
```

**골든셋 15건** 구성:

| 난이도 | 건수 | 예시 |
|--------|------|------|
| easy | 5건 | 단순 COUNT, SUM 집계 |
| medium | 6건 | GROUP BY + 다중 조건, JOIN |
| hard | 4건 | 계수산출식, 분기 비교, 다중 JOIN + 정렬 |

### 4.3 대용량 데이터 보호

```python
# 1) LIMIT 강제 — 비집계 쿼리에 LIMIT이 없으면 검증 실패
if "LIMIT" not in sql_upper and not _is_aggregate_query(sql_upper):
    errors.append("LIMIT 절이 필요합니다")

# 2) 결과 행 수 상한 — 설정 파일로 제어 (기본 10,000건)
max_rows = settings.max_query_rows
if len(rows) > max_rows:
    rows = rows[:max_rows]

# 3) 대용량 테이블 날짜 조건 강제 — 프롬프트 규칙으로 지정
# "TB_TRANSACTION 테이블은 반드시 TXN_DT 날짜 조건을 포함해야 함"
```

### 4.4 PII 보호 이중 장치

```
        SQL 생성 단계                 결과 반환 단계
            │                             │
  ┌─────────▼──────────┐       ┌──────────▼─────────┐
  │ 프롬프트 규칙       │       │ PII 마스킹          │
  │ "PII 컬럼 직접     │       │ 응답 텍스트에서      │
  │  SELECT 금지"       │       │ 주민번호, 전화번호   │
  └─────────┬──────────┘       │ 등 패턴 감지 후      │
            │                  │ 마스킹               │
  ┌─────────▼──────────┐       └──────────┬──────────┘
  │ SQL 검증기         │                  │
  │ PII_COLUMNS 목록과  │       010-1234-5678
  │ 대조하여 차단       │       → 01*****78
  └────────────────────┘
```

**PII 마스킹 대상** (`src/utils/security.py:24-40`):

- 주민등록번호, 카드번호, 계좌번호(하이픈 포함), 전화번호, 이메일

**SQL 검증 PII 차단 대상** (`src/agents/nodes/sql_validator.py:115-143`):

- 직접 노출 금지: 주민번호(8개 변형), 카드번호(3개), 계좌번호(5개), 비밀번호(5개), CVC(4개), 외국인등록번호(2개) — 총 27개 컬럼명
- 마스킹 필요: 전화번호(6개), 이메일(3개), 생년월일(4개), 주소(5개), 고객명(2개) — 총 20개 컬럼명

### 4.5 에러 격리 및 사용자 안전 응답

```python
# 모든 LLM 호출 노드에 적용된 패턴:
try:
    response = await client.messages.create(...)
except Exception as e:
    logger.error("오류", error=str(e))  # 내부 로그에만 기록
    return {
        "status": QueryStatus.ERROR,
        "error_message": "사용자 친화적 메시지",  # 기술 정보 노출 없음
    }
```

### 4.6 파이프라인 추론 추적 (Pipeline Trace)

각 노드가 수행한 주요 결정·판단을 `TraceEntry`로 기록하여 추론 과정의 투명성을 제공한다.

**데이터 모델:**

```python
# src/agents/state/state.py:40-52

class TraceEntry(BaseModel):
    node: str       # 노드 이름 (전처리, 의도분류, SQL생성, ...)
    action: str     # 수행한 작업 요약
    detail: str     # 상세 내용 (선택)
    timestamp: str  # UTC ISO 형식 자동 생성
```

**각 노드가 기록하는 추적 항목:**

| 노드 | 기록 내용 | 예시 |
|------|----------|------|
| 전처리 | 입력 정규화 결과 | '이번 달 신규 고객 수...' |
| 의도분류 | 분류 결과 + 신뢰도 | '데이터 추출' 의도 (97%) |
| 컨텍스트수집 | 수집한 참조 정보 요약 | 테이블 3건, 설명 보강 2건, 보고서SQL 2건 |
| SQL생성 | 사용 테이블 + 재시도 여부 | 사용 테이블: TB_CUST_INFO |
| SQL검증 | 검증 통과/실패 사유 | 보안·구문·테이블 검증 통과 |
| SQL실행 | 결과 건수 + 실행 시간 | 쿼리 실행 완료 (342건, 15.2ms) |
| 분석 | 인사이트 건수 + 시각화 | 데이터 분석 완료 (인사이트 3건, 시각화: bar_chart) |
| 포맷팅 | 보고서 정리 완료 | 보고서 형태로 결과 정리 완료 |

**3가지 노출 경로:**

1. **사용자 응답** — 포맷팅된 결과 끝에 `<details>` 접기로 "조회 과정 요약"을 표시한다
2. **REST API** — `include_trace: true` 파라미터 시 `trace` 배열을 반환한다
3. **CLI** — 실행 결과 아래에 추론 과정 목록을 출력한다

---

## 5. 노드별 상세 설계

### 5.1 전처리 노드 (preprocessor)

**책임**: 사용자의 자연어 입력을 정규화하고 인젝션 공격을 차단한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/preprocessor.py` |
| 입력 | `user_input`, `clarification_response` (재진입 시) |
| 출력 | `preprocessed_input`, `status` |
| 기능 | 유니코드 NFKC 정규화, 연속 공백 단일화, 입력 길이 제한(500자) |
| 보안 | SQL 인젝션 패턴 11개 + 프롬프트 인젝션 패턴 감지 |
| 분기 | 인젝션 감지 시 → ERROR, 정상 → PREPROCESSING |
| 멀티턴 | `awaiting_clarification=True`이면 원래 질의 + 명확화 응답을 합성한다 |

### 5.2 의도 분류 노드 (intent_classifier)

**책임**: 사용자 입력의 의도를 LLM으로 분류한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/intent_classifier.py` |
| 입력 | `preprocessed_input` |
| 출력 | `intent`, `intent_confidence` |
| LLM | 설정 모델 (timeout 15초, max_tokens 50) |
| 분류 | data_extraction / data_analysis / clarification_needed / general_question / unknown |
| 재시도 | `llm_call_with_parse_retry`로 포맷 불일치 시 최대 N회 재시도한다 |
| 폴백 | 최종 파싱 실패 시 `UNKNOWN`으로 폴백한다 |

### 5.3 명확화 노드 (clarifier)

**책임**: 모호한 요청에 대해 사용자에게 선택지 형태의 명확화 질문을 생성한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/clarifier.py` |
| 입력 | `preprocessed_input` |
| 출력 | `clarification_question`, `formatted_response`, `awaiting_clarification` |
| 규칙 | 질문 2~3개, 선택지 형태, 기술 용어 금지 |

### 5.4 컨텍스트 수집 노드 (context_collector)

**책임**: 5개 데이터 소스에서 SQL 생성에 필요한 참조 정보를 수집한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/context_collector.py` → `src/services/search_context_assembler.py` |
| 입력 | `preprocessed_input` |
| 출력 | `context` (ContextInfo) |
| 수집 소스 | ES 메타, ES 보고서, 이력 DB, Qdrant 매뉴얼, ES 코드메타 |
| 후처리 1 | **테이블 설명 보강** — `table_meta_enricher.enrich_table_descriptions()` (3.4.1절 참고) |
| 후처리 2 | **유사 테이블 구분 가이드 생성** — `similar_table_resolver.find_relevant_groups()` + `build_table_disambiguation_prompt()` |
| 타임아웃 | 보강 단계 전체 60초, 개별 LLM 호출 15초, 동시 호출 최대 3개 |

### 5.5 SQL 생성 노드 (sql_generator)

**책임**: 수집된 컨텍스트와 도메인 사전을 기반으로 LLM이 SQL을 생성한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/sql_generator.py` |
| 입력 | `preprocessed_input`, `context`, `validation_feedback` (재시도 시) |
| 출력 | `generated_sql`, `sql_retry_count` |
| LLM | 설정 모델 (timeout 30초, max_tokens 2,000) |
| 재시도 | 진입 시 `sql_retry_count`를 증가시키고 `validation_feedback`을 프롬프트에 주입한다 |
| 후처리 | 마크다운 코드 블록 제거(`_clean_sql_response`)하여 순수 SQL을 추출한다 |

### 5.6 SQL 검증 노드 (sql_validator)

**책임**: 생성된 SQL의 안전성과 유효성을 검증한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/sql_validator.py` |
| 입력 | `generated_sql` |
| 출력 | `validated_sql` 또는 `sql_validation_errors` + `validation_feedback` |
| 검증 항목 | 금지패턴(17개), 구문파싱(sqlglot), PII 컬럼(27개), LIMIT 강제, 시스템 카탈로그 차단 |
| 테이블 검증 | `validate_table_selection()`으로 유사 테이블 적절성을 판정한다 (pass/warning/ambiguous) |
| 비동기 | 불필요 (순수 계산) → 동기 함수로 구현한다 |

### 5.7 SQL 실행 노드 (sql_executor)

**책임**: 검증 통과한 SQL을 정보계 DB에서 실행한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/sql_executor.py` |
| 입력 | `validated_sql` |
| 출력 | `sql_result` (SQLResult: columns, rows, row_count, execution_time_ms) |
| 안전장치 | SELECT/WITH 문 재확인, 결과 행 수 상한(10,000건) |

### 5.8 분석 노드 (analyzer)

**책임**: 추출된 데이터를 기반으로 요약, 인사이트, 통계를 산출하고 시각화 차트를 생성한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/analyzer.py` + `src/agents/nodes/analyzer.py` + `src/utils/chart_generator.py` |
| 입력 | `sql_result`, `user_input` |
| 출력 | `analysis_result` (summary, insights, statistics, visualization_code, visualization_type) |
| LLM | 설정 모델 (JSON 구조 응답 + 시각화 판단 + SVG 생성) |
| 재시도 | `llm_call_with_parse_retry`로 JSON 파싱 실패 시 재시도, 최종 실패 시 텍스트 폴백한다 |
| 시각화 | 3단계 하이브리드 — LLM 판단 → LLM SVG 생성 → 템플릿 폴백 (5.10절 참고) |

### 5.9 포맷팅 노드 (formatter)

**책임**: SQL 실행 결과 또는 분석 결과를 사용자 친화적인 보고서 형태로 변환한다.

| 항목 | 내용 |
|------|------|
| 파일 | `src/agents/nodes/formatter.py` |
| 입력 | `sql_result`, `user_input`, `trace_log` |
| 출력 | `formatted_response` |
| 규칙 | 기술용어 금지, 금액 단위 변환, 코드값→이름 변환, 표 형태 |
| 추론 추적 | 응답 끝에 `<details>` 접기로 "조회 과정 요약"을 추가한다 |

### 5.10 분석결과 자동 시각화

분석 의도(`data_analysis`)로 분류된 요청에 대해, 데이터 특성에 따라
LLM이 시각화 필요 여부를 판단하고 SVG 차트를 자동 생성한다.

**설계 원칙:**

- **LLM 주도 판단**: 시각화가 가독성을 높이는 경우에만 생성한다 (단순 1~2행 집계는 생략)
- **하이브리드 생성**: 고성능 LLM → 직접 SVG, 소형 로컬 LLM → 템플릿 폴백
- **보안 우선**: LLM 생성 SVG는 신뢰할 수 없는 입력으로 취급하여 새니타이징(Sanitizing)이 필수이다

**시각화 흐름:**

```
analyze_data 노드
    │
    ├─ [1] 데이터 분석 (DATA_ANALYSIS 프롬프트)
    │       → AnalysisResult
    │
    ├─ [2] 시각화 필요 판단 (행 수 ≥ 3 일 때만)
    │       │
    │       └─ LLM 호출 (VISUALIZATION_JUDGMENT)
    │           → CHART_TYPE + CHART_TITLE
    │
    ├─ [3-A] LLM 직접 SVG 생성 (고성능 모델)
    │       │
    │       └─ LLM 호출 (VISUALIZATION_SVG_GENERATION)
    │           → 순수 <svg>...</svg> 코드
    │
    └─ [3-B] 템플릿 폴백 (LLM SVG 실패 시)
            │
            └─ chart_generator.py
                → 서버사이드 SVG 생성
```

**차트 유형 판단 기준:**

| 데이터 특성 | 차트 유형 | 예시 |
|------------|----------|------|
| 시계열 + 수치 1개 | `line_chart` | 월별 대출 건수 추이 |
| 카테고리 + 수치 비교 | `bar_chart` | 지점별 실적 비교 |
| 전체 대비 구성 비율 | `pie_chart` | 여신 유형별 비중 |
| 복수 수치 카테고리 비교 | `stacked_bar` | 부서별 건수·금액 비교 |
| 단일 집계값 (1~2행) | `none` | 총 고객 수: 1,234명 |

**프론트엔드 렌더링:**

```
WebSocket 응답 JSON
    │
    ├─ "message": "마크다운 보고서 텍스트"
    │
    └─ "visualization": {
           "type": "svg",
           "code": "<svg>...</svg>",
           "chart_type": "bar_chart",
           "title": "지점별 실적 비교"
       }
         │
         ▼
    sanitizeSVG()
    ├─ <script>, <foreignObject> 등 제거
    ├─ on* 이벤트 핸들러 속성 제거
    ├─ javascript: URL 차단
    └─ xlink:href 내 javascript: 차단
         │
         ▼
    .viz-container 에 SVG 렌더링 + 다운로드 버튼
```

**보안 고려사항:**

| 계층 | 방어 |
|------|------|
| 서버 (템플릿 생성) | `html.escape()`로 레이블을 이스케이프한다 |
| LLM 프롬프트 | `<script>` 태그, `on*` 이벤트, `javascript:` URL 금지 규칙을 명시한다 |
| 클라이언트 | `sanitizeSVG()` — DOMParser 기반 화이트리스트 새니타이징을 수행한다 |

**폐쇄망 대응:**

소형 로컬 LLM은 복잡한 SVG를 안정적으로 생성하기 어려울 수 있다.
이를 위해 `chart_generator.py`가 템플릿 기반 폴백을 제공한다:

- LLM은 **차트 유형 + 제목만 판단**한다 (VISUALIZATION_JUDGMENT — 2줄 출력, 소형 모델도 가능)
- SVG 코드는 **Python 서버사이드에서 생성**한다 (외부 라이브러리 의존성 없음)
- 지원 차트: 막대(bar), 꺾은선(line), 원형(pie)

---

## 6. 커넥터 아키텍처

```
         ConnectorManager (싱글턴)
                 │
 ┌───────────────┼───────────────────────┐
 ▼               ▼               ▼       ▼
ElasticSearch  InfoDB         HistoryDB Qdrant
Connector      Connector      Connector Connector
 │               │               │       │
 │ use_dummy=True/False          │       │
 │               │               │       │
 ▼               ▼               ▼       ▼
Dummy 데이터   Dummy 생성기   Dummy SQL Dummy 매뉴얼
(6 테이블)     (SQL 분석)     (5건)     (5건 문서)
 or              or              or       or
실제 ES 연결   실제 PostgreSQL 실제 PG  실제 Qdrant
```

**설정 파일 하나로 Dummy↔실제 전환:**

```python
# src/connectors/manager.py:68-85

manager = get_connector_manager(use_dummy=True)   # 개발환경
manager = get_connector_manager(use_dummy=False)  # 폐쇄망 배포
```

**LLM 프로바이더 전환:**

```python
# src/utils/llm/client.py:149-179
# 환경 변수 LLM_PROVIDER로 전환

client = get_llm_client()
# llm_provider="anthropic"       → AsyncAnthropic
# llm_provider="openai_compatible" → AsyncOpenAI (Groq, OpenRouter 등)
```

### 6.1 인프라 변경 사항 (2026-03-20)

**ES nori 한글 분석기 적용:**

```text
standalone/docker/elasticsearch/Dockerfile   ← 신규 생성
  FROM elasticsearch:8.15.0
  RUN bin/elasticsearch-plugin install --batch analysis-nori

standalone/docker/docker-compose.dev.yml
  elasticsearch:
    build: ./standalone/docker/elasticsearch   ← image → build 변경
    image: dc-elasticsearch:8.15.0-nori

standalone/scripts/seed_elasticsearch.py
  SHARD_SETTINGS에 korean analyzer 정의 (nori_tokenizer + nori_readingform)
  모든 text 필드: "analyzer": "standard" → "analyzer": "korean"
```

**효과:** "여신" 검색 2건→29건, "대출" 0건→7건, "고객" 0건→41건 (535개 테이블 기준)

**Qdrant 임베딩 모델 통일:**

```text
시딩 (seed_qdrant.py):   sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
조회 (qdrant_connector.py): intfloat/multilingual-e5-small → 시딩 모델과 동일하게 수정
```

차원(384)은 같았으나 임베딩 공간이 달라 유사도 검색 품질이 저하되고 있었음. 수정 후 정상화.

---

## 7. 향후 고도화 방향

| 영역 | 현재 상태 | 고도화 방향 |
|------|----------|------------|
| 멀티턴 대화 | **구현 완료** — 최대 2회 명확화 왕복 + 재진입 합성 | 대화 문맥 요약을 통한 장기 세션 지원 |
| SQL 재생성 | **구현 완료** — 검증 피드백 주입 + 최대 2회 재시도 루프 | 자동 수정 전략 다양화 (부분 수정, 힌트 강화) |
| 유사 테이블 구분 | **구현 완료** — 5개 그룹, 신호어 기반 점수 + 명확화 질문 | 임베딩 유사도 기반 테이블 추천으로 고도화 |
| 테이블 설명 보강 | **구현 완료** — 3관점 충분성 판단 + LLM 보강 + Semaphore 병렬 | 보강 결과 캐싱(Redis), 사용자 피드백으로 품질 개선 |
| LLM 포맷 재시도 | **구현 완료** — `llm_call_with_parse_retry` 공용 유틸리티 | 프로바이더별 최적 포맷 힌트 자동 선택 |
| 캐싱 | 설정만 존재 (Redis 미연동) | Redis 기반 동일 질의 캐싱 + 보강 설명 캐싱 |
| 벡터 검색 | Qdrant (업무 매뉴얼만) | 과거 SQL 이력도 벡터 유사도 검색으로 전환 |
| 분석결과 시각화 | **구현 완료** — LLM 판단 + SVG 생성 + 템플릿 폴백 | 인터랙티브 차트, 추가 차트 유형, PNG/PDF 내보내기 |
| 모델 교체 | Anthropic + OpenAI 호환 (설정으로 변경 가능) | 폐쇄망 로컬 LLM 대응 프롬프트 최적화 |
| 프로그램 저장소 | 미구현 | 프로그램 코드에서 SQL 패턴 추출 |

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|-----|------|---------|-------|
| 1.0 | 2026-03-19 | 최초 작성 | pipeline-designer |
| 1.1 | 2026-03-19 | 시각화, 추론 추적, 멀티턴 명확화 등 반영 | pipeline-designer |
| 1.2 | 2026-03-19 | 문서 작성 가이드 준수 형태로 전면 갱신: 메타 정보·목차·변경 이력 추가, 용어 영문 병기, 소스 코드 줄 번호 반영, 인젝션 패턴 수·PII 컬럼 수 등 코드 불일치 수정, LLM 프로바이더 추상화·포맷 재시도 등 누락 내용 보완 | doc-writer |
