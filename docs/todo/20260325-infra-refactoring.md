# TODO — 인프라 리팩토링

## 1. 커넥터 업무 로직 분리 (connectors → services)

**우선순위:** 중간
**등록일:** 2026-03-23

### 현황

커넥터 구현체 3곳에 검색 전략·SQL 조립 등 **업무 로직이 혼재**되어 있다.
커넥터는 순수 인프라 계층(연결·실행)만 담당하고, 업무 로직은 서비스 레이어에서 처리해야 한다.

### 대상 메서드

| 커넥터 | 메서드 | 혼재된 업무 로직 |
|--------|--------|-----------------|
| `HistoryDBConnector` | `search_similar_sql()` | 키워드 분해, ILIKE 조건 조립, 테이블명·컬럼명·LIMIT 하드코딩 |
| `ElasticSearchConnector` | `search_table_meta()` / `search_report_sql()` / `search_code_meta()` | 인덱스 라우팅, multi_match vs match 검색 전략 결정 |
| `QdrantConnector` | `search_sql_history()` | Dense+Sparse 하이브리드 전략, RRF 융합, 임베딩 서비스 직접 호출 |

### 문제점

- **폐쇄망 전환 위험**: `ILIKE`는 PostgreSQL 문법으로 Sybase IQ / Impala에서 비호환
- **역의존**: QdrantConnector가 `search_query_embedder` 서비스를 직접 import (커넥터 → 서비스 역방향 의존)
- **테스트 어려움**: 검색 전략 변경 시 커넥터 수정 필요
- **재사용 불가**: 동일 커넥터로 다른 검색 전략을 적용할 수 없음

### 개선 방향

```
현재:  service → connector.search_similar_sql("여신 연체")
                  └─ 키워드 분해 (업무)
                  └─ SQL 조립 (업무)
                  └─ DB 실행 (인프라)

개선:  service → 키워드 분해 + SQL 조립 (업무)
              → connector.execute_query(sql, params) (인프라)
```

- 커넥터: `execute_query()`, `search(index, body)`, `query_points(collection, query)` 등 범용 메서드만 노출
- 서비스(`search_context_assembler` 등): 검색 전략, SQL 조립, 임베딩 호출 등 업무 로직 담당
- 기존 편의 메서드(`search_similar_sql` 등)는 서비스 레이어로 이전 후 커넥터에서 제거

### 영향 범위

- `src/connectors/impl/postgres_connector.py` — `search_similar_sql()` 제거
- `src/connectors/impl/elasticsearch_connector.py` — `search_table_meta()` 등 3개 메서드 단순화
- `src/connectors/impl/qdrant_connector.py` — `search_sql_history()` 임베딩 의존 제거
- `src/services/search_context_assembler.py` — 업무 로직 이전 수신
- `tests/unit/test_connectors.py` 등 관련 테스트 수정

---

## 2. SQL 이력 데이터 포맷 일관성 점검

**우선순위:** 낮음
**등록일:** 2026-03-23

### 현황

`seed_sql_history.py`에서 시딩한 데이터와 `search_query_builder.py`·`qdrant_connector.py`에서
조회·처리하는 Value 포맷이 일관되게 처리되고 있는지 점검 필요.

### 관련 파일

- `src/tools/seed_sql_history.py` (L70-73)
- `src/services/search_query_builder.py` (L22-23)
