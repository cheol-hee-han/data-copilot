---
name: annotate
description: |
  코드 주석(모듈 docstring, 클래스/함수 docstring, 인라인 주석)을 일관된 품질로 작성·정비합니다.
  신규 코드 주석 작성, 기존 주석 정비, 주석 품질 일괄 점검 시 사용하세요.
user_invocable: true
---
# 역할

코드 주석 전문가. 프로젝트의 주석 컨벤션을 준수하면서, 코드의 **의도(Why)**와 **맥락(Context)**을 전달하는 주석을 작성한다.

# 사용법

```
/annotate                          # 전체 주석이 가능한 파일 점검·정비
/annotate src/services/            # 디렉토리 전체 점검·정비
/annotate src/services/reranker.py # 특정 파일 점검·정비
/annotate --check-only             # 정비 없이 부족한 곳만 리스트업
/annotate --this                   # 현재 열린 파일의 주석 정비
```

# 프로젝트 주석 컨벤션

## 언어 규칙

- **docstring**: 한국어 (프로젝트 컨벤션: `한국어 docstring + 영어 변수명`)
- **인라인 주석**: 한국어
- **코드 내 식별자**: 영어 (변수명, 함수명, 클래스명)

## 1. 모듈 상단 docstring (파일 헤더)

**가장 중요한 주석.** 파일을 열었을 때 "이 파일이 뭔지, 왜 존재하는지"를 즉시 파악할 수 있어야 한다.

### 필수 구성 요소

| 구성 요소 | 설명 | 필수 여부 |
|-----------|------|-----------|
| 한 줄 요약 | 모듈의 역할을 한 문장으로 | 필수 |
| 상세 설명 | 이 모듈이 존재하는 이유, 핵심 전략/알고리즘 | 필수 (3줄 이상) |
| 설계 결정 | 왜 이렇게 구현했는지 (대안 대비 선택 근거) | 해당 시 필수 |
| 의존 관계 | 이 모듈이 호출하는/호출되는 주요 모듈 | 선택 (복잡한 경우) |
| TODO | 알려진 개선점, 기술 부채 | 해당 시 권장 |

### 좋은 예시 — 서비스 모듈 (역할과 핵심 전략 설명)

```python
"""검색 쿼리 빌더.

사용자의 전처리된 입력을 분석하여 각 데이터 소스(ES, PostgreSQL, Qdrant)에
최적화된 검색 쿼리를 생성한다.

핵심 기능 및 전략:
    1. 도메인 용어 매칭 → 테이블명·컬럼명·카테고리 추출
    2. 한국어 불용어 제거 → 검색 노이즈 최소화
    3. 동의어/별칭 확장 → 검색 재현율(recall) 향상
    4. 소스별 쿼리 특화 → 각 소스의 검색 메커니즘에 최적화
       - ES table_meta: 테이블명 부스트 + 도메인 키워드
       - ES report_sql: 업무 목적 중심 자연어
       - PostgreSQL history: 핵심 키워드 OR 조합
       - Qdrant manual: 의미 보강된 자연어 (벡터 검색 최적화)

핵심 함수:
    - build_source_queries: 메인 엔트리, 전체 쿼리 빌드 파이프라인
    - _extract_domain_terms: 도메인 용어 추출 (매핑 기반)
    - _remove_korean_stopwords: 한국어 불용어 제거
    - _expand_with_synonyms: 동의어/별칭 확장

fallback 전략:
    - 도메인 용어 매핑 실패 시 → 전체 입력에서 핵심 명사 추출하여 대체
    - 동의어 확장 실패 시 → 원문 그대로 사용

정규화 연동:
    NormalizedQuery 가 있으면 search_keywords 를 활용하여
    기존 도메인 사전 기반 전략을 보완한다.
    - meta_search → ES/History 키워드 보강
    - vector_search → Qdrant 쿼리 보강
"""
```

### 좋은 예시 — 커넥터 모듈 (기본 역할과 핵심 전략 설명 외 해당 프로그램에서 주요한 설명 추가)

```python
"""ElasticSearch 커넥터.

테이블 메타, 보고서 SQL, 코드 메타 3개 인덱스에 대한 검색을 제공한다.
Dummy 모드(use_dummy=True)에서는 ES 없이 하드코딩된 샘플 데이터를 반환한다.

... (핵심 및 기능설명) ...

인덱스 매핑:
    - {es_table_meta_index}: 테이블·컬럼 레이아웃 (메인 메타 소스)
    - {es_report_sql_index}: 보고서명·요건·SQL (참조 SQL 소스)
    - {es_code_meta_index}: 코드 필드·코드값·설명 (도메인 코드 소스)

Dummy 모드:
    폐쇄망 개발·테스트 시 ES 없이 동작하도록
    dummy_data.py 의 샘플을 반환한다.
"""
```

### 좋은 예시 — 테스트 모듈 (테스트 대상과 테스트 방법 설명)

```python
"""질의 정규화(의도분석) 파이프라인 단위 테스트.

테스트 대상:
    자연어 질의 → 8-Slot NormalizedQuery 변환 파이프라인의 각 단계를 검증한다.
    LLM 호출 없이 전처리·파싱·검증·후처리·동의어 사전 로딩을 개별 테스트한다.

    ┌─────────────────────────────────────────────────────────────────────┐
    │  파이프라인 단계          테스트 클래스           테스트 대상 함수   │
    │  ─────────────────────── ─────────────────────── ──────────────── │
    │  0. 모델/스키마           TestNormalizationModels  NormalizedQuery  │
    │  1. 전처리 (약어 확장)    TestPreprocessor         _preprocess_*    │
    │  2. LLM 응답 JSON 파싱   TestJsonParser           _parse_llm_json  │
    │  3. 구조 검증 (Enum)     TestValidator            _validate_*      │
    │  4. 후처리 (정합성 보정)  TestPostProcessor        _postprocess     │
    │  5. 동의어 사전           TestSynonyms             ALL_SYNONYMS 등  │
    └─────────────────────────────────────────────────────────────────────┘

입력 예시 (정상):
    - 전처리: "YoY 매출 추이"
        → "전년동기대비 매출 추이" (약어 확장)
    - JSON 파싱: '```json\\n{"intent": {"primary": "AGGREGATE"}}\\n```'
        → {"intent": {"primary": "AGGREGATE"}} (코드 펜스 제거)
    - 구조 검증: {"intent": {"primary": "EXTRACT"}, "entities": [...], ...}
        → 동일 dict 반환, errors=[]
    - 후처리: AGGREGATE + GROUP 차원 + agg_function="NONE"
        → agg_function이 "SUM"으로 자동 보정

결과 예시 (오류 케이스):
    - JSON 파싱 실패: "not json at all"
        → ValueError("LLM이 유효한 JSON을 반환하지 않았습니다")
    - 잘못된 intent: {"intent": {"primary": "INVALID"}}
        → primary가 "EXTRACT"로 폴백, errors=["intent.primary 보정 → EXTRACT"]
    - 잘못된 modifier: {"modifiers": [{"type": "INVALID_TYPE"}]}
        → 해당 modifier 삭제됨 (modifiers=[])

실행 스크립트:
    # 전체 실행
    pytest tests/auto/unit/test_query_normalizer.py -v

    # 클래스별 실행
    pytest tests/auto/unit/test_query_normalizer.py::TestNormalizationModels -v
    pytest tests/auto/unit/test_query_normalizer.py::TestPreprocessor -v
    pytest tests/auto/unit/test_query_normalizer.py::TestJsonParser -v
    pytest tests/auto/unit/test_query_normalizer.py::TestValidator -v
    pytest tests/auto/unit/test_query_normalizer.py::TestPostProcessor -v
    pytest tests/auto/unit/test_query_normalizer.py::TestSynonyms -v

    # 개별 테스트 실행
    pytest tests/auto/unit/test_query_normalizer.py::TestValidator::test_invalid_intent_corrected -v

    # 실패 시 즉시 중단 + 상세 출력
    pytest tests/auto/unit/test_query_normalizer.py -v -x --tb=short

참고:
    - 외부 의존성 없음 (LLM, DB, ES, Qdrant 불필요)
    - .env 파일 없이도 모든 테스트 통과
    - 테스트 대상 소스: src/services/query_normalizer.py
    - 동의어 사전 소스: src/services/domain/domain_synonyms.py
    - 모델 정의: src/agents/models/normalization.py
"""
```




### 나쁜 예시

```python
"""ElasticSearch 커넥터."""  # 한 줄만 — 역할은 알겠지만 왜, 어떻게는 없음
```

```python
"""이 모듈은 ES에서 데이터를 검색하는 기능을 제공합니다."""  # "이 모듈은" 불필요, 경어체 불일치
```

## 2. 클래스 docstring

```python
class EvaluationTracker:
    """파이프라인 실행 트래커.

    각 파이프라인 실행을 추적하고 구조화된 JSON으로 저장한다.
    노드 실행 시간, LLM 호출, 의사결정, 컨텍스트 수집을 기록한다.
    """
```

- 한 줄 요약 + 빈 줄 + 상세 (2~3문장)
- 짧은 데이터 클래스는 한 줄 요약만으로 충분

```python
class ColumnMeta(BaseModel):
    """컬럼 메타 정보."""
```

## 3. 함수/메서드 docstring

### 공개 함수 (필수)

```python
async def collect_context(
    query: str,
    tracker: EvaluationTracker | None = None,
    normalized_query: object | None = None,
) -> ContextInfo:
    """사용자 질의에 대한 컨텍스트를 병렬로 수집한다.

    검색 쿼리 전략 빌더를 통해 소스별 최적화된 쿼리를 생성한 뒤,
    4개 소스를 asyncio.gather 로 동시에 호출한다.
    각 소스는 독립적으로 실패를 처리하므로 한 소스의 오류가 전체를 막지 않는다.

    Args:
        query: 전처리된 사용자 입력.
        tracker: 평가 트래커. 제공 시 각 소스별 쿼리/결과를 기록한다.
        normalized_query: 정규화된 질의 (search_keywords 활용).

    Returns:
        수집된 컨텍스트 (테이블 메타, 과거 SQL, 매뉴얼 등).
    """
```

- 한 줄 요약 (동사로 시작: "~한다")
- 동작 설명 (핵심 전략이나 주의사항)
- Args/Returns (파라미터 3개 이상이거나 의미가 불명확할 때)

### 비공개 함수 (선택적)

```python
def _normalize(text: str) -> str:
    """텍스트를 정규화한다.

    1. 유니코드 NFKC 정규화: 전각 문자 → 반각 ASCII 변환
    2. 연속 공백 단일화
    """
```

- 로직이 자명하면 한 줄 요약만
- 알고리즘이 있으면 번호 목록으로 단계 설명

### docstring 불필요한 경우

```python
def _record(self, name: str, start: float, error: str | None) -> None:
    # 이름·시그니처만으로 의도가 명확하면 생략 가능
```

## 4. 인라인 주석

### 작성 기준: "코드가 말하지 못하는 것"만 쓴다

```python
# 좋음 — Why/Context를 설명
# 벡터 검색 결과 우선 (의미 유사도 기반, 더 정확)
for sql in state.context.vector_past_sqls:

# 좋음 — 비직관적 로직의 이유
# LLM이 전각 문자를 포함한 SQL을 생성하는 경우를 방어한다
sql = normalize_unicode(state.generated_sql.strip())

# 좋음 — 섹션 구분 (긴 모듈에서)
# ── 카테고리 → ES domain_cd 매핑 ──

# 나쁨 — What을 반복 (코드가 이미 말하고 있음)
# 리스트를 순회한다
for item in items:

# 나쁨 — 변수명이 이미 설명
# 최대 입력 길이
MAX_INPUT_LENGTH = 500
```

### 섹션 구분자

긴 모듈에서 논리적 블록을 구분할 때:

```python
# ──────────────────────────────────────────────
# 전처리기
# ──────────────────────────────────────────────
```

또는 간결하게:

```python
# ── 전처리기 ──
```

## 5. 상수/설정값 주석

```python
# 동시 LLM 호출 최대 수 — API rate limit 방어
_LLM_CONCURRENCY_LIMIT = settings.llm_concurrency_limit

# PII 컬럼 목록 (직접 SELECT 금지) — 실제 컬럼명 변형 포함
# resources/security/pii_columns.yaml 이 있으면 해당 파일의 정의를 사용한다.
_DEFAULT_PII_COLUMNS = { ... }
```

- 설정값의 **존재 이유**와 **커스터마이징 방법** 기재
- 단위가 있으면 명시 (예: `timeout=60  # 초`)

# 작업 절차

1. 대상 파일/디렉토리를 Read로 확인
2. 모듈 상단 docstring → 클래스 docstring → 공개 함수 docstring → 인라인 주석 순으로 점검
3. 위 컨벤션과 비교하여 부족한 곳 식별
4. `--check-only` 모드면 리스트만 출력, 아니면 Edit으로 직접 정비
5. 정비 시 기존 주석의 의도를 보존하면서 컨벤션에 맞게 보강 (삭제하지 않고 개선)

# 판단 기준

| 등급 | 기준 | 조치 |
|------|------|------|
| 필수 정비 | 모듈 상단 docstring 없음 또는 한 줄만 | 즉시 작성 |
| 필수 정비 | 공개 함수 docstring 없음 | 즉시 작성 |
| 권장 정비 | 상단 docstring에 "왜/어떻게"가 없음 | 상세 설명 추가 |
| 권장 정비 | 비직관적 로직에 인라인 주석 없음 | Why 주석 추가 |
| 불필요 | 코드가 자명한 곳에 What 주석 | 삭제 |
