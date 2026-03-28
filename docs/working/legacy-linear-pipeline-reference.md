# 레거시 선형 파이프라인 노드 참조 문서

> **목적**: 에이전틱 코어 통합으로 삭제된 선형 파이프라인 중간부 노드들의 설계 의도와
> 향후 개선에 참고할 만한 패턴을 기록한다.
> **삭제일**: 2026-03-24
> **대체**: `src/agents/nodes/reason/` 에이전틱 코어 노드

---

## 1. context_collector.py (컨텍스트 수집 노드)

### 핵심 설계

- `search_context_assembler.collect_context()`에 위임하여 6개 소스 병렬 수집
- 정규화된 질의(`normalized_query`)를 활용해 검색 정밀도 향상
- `EvaluationTracker` 주입으로 소스별 성능/실패 추적

### 에이전틱 코어에서의 대응

- **planner 노드**: 초기 컨텍스트 1차 수집 (`_collect_initial_context`)
- **context_explorer 노드**: 점진적 탐색 루프로 필요 시 추가 수집
- 기존 `collect_context()` 서비스는 그대로 재사용

### 향후 참고 포인트

- trace 로그에 수집 통계(테이블 수, 보강 수, SQL 수 등)를 기록하는 패턴이 유용
- `get_current_tracker()` 패턴 — 에이전틱 노드에서도 동일하게 적용 가능

---

## 2. context_enricher.py (컨텍스트 보강 노드)

### 핵심 설계

- `table_meta_enricher.enrich_table_descriptions()`에 위임
- Entity/Functional/Lifecycle 3관점 LLM 보강
- `asyncio.wait_for()` 타임아웃 + 실패 시 원본 메타 유지 폴백
- `ENRICHMENT_FORMAT_HINT`, `ENRICHMENT_SYSTEM` 프롬프트 활용

### 에이전틱 코어에서의 대응

- **context_explorer 노드의 `search_table_meta` 도구**: 테이블 메타 검색 후 보강 호출
- `table_meta_enricher` 서비스는 그대로 재사용

### 향후 참고 포인트

- 타임아웃 기반 그레이스풀 폴백 패턴: 보강 실패해도 원본으로 계속 진행
- Semaphore 기반 LLM 동시 호출 제한 (`llm_concurrency_limit`)

---

## 2-1. table_meta_enricher.py (테이블 설명 보강 서비스)

> **삭제일**: 2026-03-24
> **위치**: `src/services/table_meta_enricher.py` (삭제됨)
> **호출자**: `context_enricher.py` (삭제됨)

### 컨셉

정보계 DB의 테이블 설명이 미흡한 경우(20자 미만 또는 3관점 미커버),
LLM을 호출하여 **3관점 보강 설명**을 생성한다:

1. **엔티티 집합 정의** — 테이블에 어떤 데이터가 있는지
2. **기능적 정의** — 데이터가 어디에 어떻게 쓰이는지
3. **데이터 발생규칙** — 데이터가 언제 생성되어 적재되는지

### 구현 상세

```python
# 충분성 판단
def is_description_sufficient(table: TableMeta) -> bool:
    # 1. 설명 길이 >= 20자
    # 2. 3관점 키워드 모두 커버
    #    entity: ["데이터", "정보", "내역", "이력", ...]
    #    functional: ["사용", "활용", "조회", "분석", ...]
    #    generation: ["생성", "적재", "배치", "갱신", ...]

# 보강 프롬프트 조립
def _enrich_single_table(table, report_sqls, past_sqls):
    # 입력: 테이블명, 원본 설명, 갱신주기, 컬럼 요약, 관련 SQL
    # → LLM 호출 → enriched_description 생성
    # → _validate_enrichment (최소 길이 검증)

# 병렬 보강 (Semaphore 제한)
async def enrich_table_descriptions(tables, ...):
    # 불충분한 테이블만 필터
    # asyncio.Semaphore(llm_concurrency_limit) 로 동시 호출 제한
    # asyncio.gather로 병렬 처리
    # 실패 시 원본 유지 (graceful fallback)
```

### 에이전틱 코어 적용 시 고려사항

**적용하지 않은 이유 (2026-03-24 시점):**

1. 에이전틱 `sql_generator`가 `CandidateTable.role`만 사용하고
   `enriched_description`을 프롬프트에 주입하지 않아 보강해도 효과 없음
2. `CandidateTable` ↔ `TableMeta` 변환 레이어 필요
3. 탐색 루프 내에서 매 스텝마다 LLM 보강 호출 시 레이턴시 폭증
   (테이블 10개 × 2~5초 = 20~50초 추가)

**향후 적용 시 권장 방법:**

- `sql_generator`의 LLM 프롬프트에 `enriched_description` 주입 경로 추가
- 보강 대상을 `current_hypothesis.required_tables` (1~3개)로 제한
- 보강 결과를 `KnowledgeItem.value`에 저장하여 재탐색 시 재사용
- 또는 사전 배치 보강: 시딩 시점에 전체 테이블 설명을 미리 보강하여 저장

### 핵심 패턴 (재사용 가능)

- `asyncio.Semaphore` 기반 LLM rate limit 방어
- `asyncio.gather` 병렬 처리 + 개별 실패 시 원본 유지
- 3관점 키워드 매칭 충분성 판단 (단, 오판 가능성 있음)
- `llm_call_with_parse_retry` + `_validate_enrichment` 품질 검증 체인

---

## 3. sql_generator.py (SQL 생성 노드 — 선형 버전)

### 핵심 설계

- `sql_prompt_assembler.generate_sql()`에 위임
- 재시도 시 `validation_feedback`을 프롬프트에 추가 주입
- `sql_retry_count` 매 호출마다 증가
- 재생성 시 `validation_feedback` 초기화

### 에이전틱 코어에서의 대응

- **에이전틱 sql_generator 노드**: 동일한 서비스 재사용하되 추가 컨텍스트 주입
  - `structural_hints` (sqlglot 파싱 힌트)
  - `confirmed_terms` (CONFIRMED 지식 항목)
  - `dead_ends` (실패 방지)
  - `sql_fix_instruction` (재생성 사유)

### 향후 참고 포인트

- SQL 내 테이블 사용 감지 패턴: `t.table_name.upper() in generated.upper()`
  → 에이전틱에서는 `sqlglot.get_real_tables()`로 정확한 파싱 사용
- `SQL_GENERATION_RULES` + `SQL_VALIDATION_FEEDBACK_SECTION` 프롬프트 조합

---

## 3-1. sql_prompt_assembler.py (SQL 프롬프트 조립 서비스)

> **삭제일**: 2026-03-25
> **위치**: `src/services/sql_prompt_assembler.py` (삭제됨)
> **호출자**: 선형 sql_generator 노드 (삭제됨)
> **대체**: `src/agents/nodes/reason/sql_generator.py` 내 `_build_agentic_prompt()` + `_call_llm_for_sql()`

### 컨셉

수집된 컨텍스트(테이블 메타, 보고서 SQL, 과거 SQL, 업무 매뉴얼, 도메인 사전)와
정규화된 질의 구조를 하나의 시스템 프롬프트로 조립하여 LLM에 전달하고,
응답에서 순수 SQL만 추출하여 반환. 검증 실패 시 피드백 섹션을 추가하여 재생성 유도.

### 핵심 함수 (재사용 가능한 패턴)

- `build_table_info(table_metas)`: 테이블/컬럼 메타를 프롬프트용 문자열로 변환 (PII 마킹 포함)
- `build_past_sqls(past_sqls, vector_past_sqls)`: 벡터 검색 SQL 우선 배치 + 중복 제거 + 최대 8건 제한
- `build_normalization_section(normalized_query)`: 8-Slot NormalizedQuery를 자연어 섹션으로 변환 (intent, entities, measures, dimensions, filters, time, modifiers, output_hint)
- `clean_sql_response(raw)`: LLM 응답에서 마크다운 코드 블록 제거 → 순수 SQL 추출

### 설계 결정 — 프롬프트 주입 방식

```python
# 프롬프트 템플릿을 노드에서 인자로 주입받아 서비스가 직접 의존하지 않음
async def generate_sql(
    query, context, normalized_query, validation_feedback,
    *, system_prompt, feedback_template,   # ← 주입
):
    assembled = system_prompt.format(
        table_info=build_table_info(...),
        report_sqls=build_report_sqls(...),
        ...
    )
```

→ 에이전틱에서 `.format()` 대신 `.replace()`로 전환한 이유:
  JSON few-shot 예제의 `{}`가 `.format()`의 플레이스홀더로 오인되어 KeyError 발생.

### 토큰 관리 전략

- 과거 SQL: 벡터 검색 결과 우선 + 최대 8건 제한
- 보고서 SQL: 최대 3건
- 업무 매뉴얼: 최대 3건
- 도메인 용어: 질의에 매칭된 것만

→ 에이전틱에서는 `confirmed_terms`만 주입하므로 토큰 효율이 더 높음.

---

## 3-2. search_context_assembler.py (병렬 컨텍스트 수집 서비스)

> **삭제일**: 2026-03-25
> **위치**: `src/services/search_context_assembler.py` (삭제됨)
> **호출자**: 선형 context_collector 노드 (삭제됨)
> **대체**: `src/agents/nodes/reason/tools.py` 도구 래퍼 + `context_explorer.py` 탐색 루프

### 컨셉

6개 데이터 소스를 `asyncio.gather`로 **완전 병렬** 수집하여 `ContextInfo`로 통합.
개별 소스 실패 시 해당 소스만 빈 값으로 폴백 — 나머지 정상 반환.

### 6개 소스 + 병렬 전략

```
asyncio.gather(
    _fetch_table_metas()      → MongoDB 테이블/컬럼 메타
    _fetch_report_sqls()      → ES 보고서 SQL
    _fetch_past_sqls()        → 이력 DB 과거 SQL (키워드 ILIKE)
    _fetch_manual_refs()      → Qdrant 업무 매뉴얼
    _fetch_sql_history_vectors() → Qdrant sql_history (벡터+Reranker)
    _fetch_code_meta()        → MongoDB 코드 메타 전체 로드
)
```

### 핵심 패턴 (재사용 가능)

1. **개별 소스 격리 폴백**: 각 `_fetch_*` 함수가 자체 try/except로 빈 값 반환
   — `failed_sources` 리스트에 실패 소스명 기록
2. **Reranker 파이프라인** (`_fetch_sql_history_vectors`):
   Dense+Sparse 하이브리드 검색 → RRF 후보 → BGE-Reranker 재순위 → Top-K SQL
3. **코드 메타 전체 로드** (`_fetch_code_meta`):
   코드값→도메인 용어 매핑 자동 생성 (예: "정상" → "STATUS_CD = '01'")
   + 실패해도 기본 도메인 용어(여신/수신/연체 등) 보장
4. **유사 테이블 그룹 감지**: 수집 후 `similar_table_resolver`로 유사 테이블 자동 감지
   → `table_disambiguation_guide` 프롬프트 섹션 생성
5. **EvaluationTracker 통합**: 각 소스별 쿼리/결과/지연시간 추적

### 에이전틱 코어와의 차이

| 항목 | search_context_assembler (선형) | tools.py + context_explorer (에이전틱) |
|------|-------------------------------|---------------------------------------|
| 수집 시점 | 한 번에 전부 수집 | 필요할 때 점진적 탐색 |
| 병렬 전략 | asyncio.gather 완전 병렬 | 실행계획 스텝 순차 (필요 시만) |
| 소스 선택 | 항상 6개 전부 | 가설 기반으로 필요한 소스만 |
| Reranker | 내장 (벡터 검색 후 자동) | 미포함 (향후 통합 가능) |
| 코드 메타 | 전체 로드 + 도메인 용어 자동 생성 | search_code_meta(컬럼명) 건별 조회 |
| 유사 테이블 | 자동 감지 + 구분 가이드 | 미포함 (향후 통합 가능) |
| 토큰 비용 | 전부 수집 → 큼 | 필요한 것만 → 효율적 |

### 향후 에이전틱 코어에 재통합 고려 사항

- **Reranker**: `search_use_cases` 도구에 Reranker 단계 추가 권장
- **코드 메타 전체 로드**: 첫 탐색 시 1회 벌크 로드 → `knowledge_items`에 적재하는 패턴
- **유사 테이블 구분**: `planner`에서 초기 후보 테이블 감지 시 자동 disambiguation
- **failed_sources 추적**: 에이전틱 `loop_guard`에 소스별 실패 카운터 추가

---

## 3-3. search_similar_sql.sql (키워드 기반 SQL 이력 검색)

> **삭제일**: 2026-03-25
> **위치**: `resources/queries/search_similar_sql.sql` (삭제됨)
> **호출자**: `HistoryDBConnector.search_similar_sql()` → `search_context_assembler._fetch_past_sqls()`
> **대체**: Qdrant `sql_history` 벡터 검색 (`tools.search_use_cases`)

### SQL 템플릿

```sql
SELECT query_text, sql, executed_at, success
  FROM sql_query_history
 WHERE success = TRUE
   AND ({conditions})     -- 키워드별 ILIKE 조건 동적 삽입
 ORDER BY executed_at DESC
 LIMIT 5
```

### 한계 (벡터 검색으로 대체한 이유)

- ILIKE 키워드 매칭은 동의어/유사 표현에 취약 ("대출" vs "여신")
- 키워드 분해 로직이 `HistoryDBConnector`에 하드코딩
- 폐쇄망 전환 시 ILIKE → LIKE 변환 필요 (Sybase IQ/Impala)

---

## 4. sql_validator.py (SQL 검증 노드 — 선형 버전)

### 핵심 설계

- 2단계 검증: 안전성(`sql_safety_checker`) + 테이블 적절성(`similar_table_resolver`)
- 3가지 테이블 판정: `PASS` / `WARNING` / `AMBIGUOUS`
- AMBIGUOUS 시 명확화 질문 생성 → `clarification_question`
- WARNING 시 재생성 루프 유도 (`validation_feedback` 설정)

### 에이전틱 코어에서의 대응

- **에이전틱 sql_validator 노드**: 3-레이어 검증으로 확장
  - Layer 1 (Rule-based): 기존 `sql_safety_checker` + sqlglot 파싱 + 테이블/컬럼 존재
  - Layer 2a (Rule-based): 구조적 sanity check (GROUP BY, 집계함수, 테이블/컬럼)
  - Layer 2b (LLM): 의미 검증 (7개 체크리스트)
  - Layer 3 (Execution): LIMIT 5 실제 실행
- 5단계 실패 유형 분류 + `sql_fix_instruction` 생성

### 향후 참고 포인트

- `similar_table_resolver`의 3단계 검증(어노테이션/검증/골든셋) 패턴
- AMBIGUOUS → 명확화 분기 패턴 (에이전틱에서는 `ask_user` 노드로 대응)
- `build_validation_feedback()` — 검증 실패 내용을 재생성 프롬프트에 주입하는 패턴

---

## 5. 선형 파이프라인 전체 흐름 (삭제 전)

```
preprocess → resolve_history → classify_intent → normalize_query
  → [collect_context] → [enrich_context] → [generate_sql] → [validate_sql]
  → execute_sql → analyze_data → format_response
```

`[대괄호]` 표시된 4개 노드가 에이전틱 코어로 교체됨.

교체 후:
```
preprocess → resolve_history → classify_intent → normalize_query
  → [agentic_core (서브그래프)]
  → execute_sql → analyze_data → format_response
```
