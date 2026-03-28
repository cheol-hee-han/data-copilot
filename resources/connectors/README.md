# resources/connectors/

외부 데이터 소스(ElasticSearch, MongoDB, Neo4j)의 쿼리 템플릿.

## 디렉토리 구조

```
connectors/
  elasticsearch/             ES 검색 쿼리 body 템플릿
  │  table_meta_query.json     테이블/컬럼 메타 multi_match 검색
  │  report_sql_query.json     보고서 SQL multi_match 검색
  │  code_meta_query.json      코드값 match 검색
  │
  mongo/                     MongoDB aggregation 참조 + 초기화
  │  init_mongodb.js           컬렉션/인덱스 초기화 스크립트
  │  query_table_meta.json     테이블 메타 aggregation 참조
  │  query_code_meta.json      코드 메타 aggregation 참조
  │  query_dictionary.json     용어사전 aggregation 참조
  │
  neo4j/                     Neo4j 온톨로지 스키마 + 시딩 쿼리
     init_neo4j.cypher         제약조건/인덱스 초기화 스크립트
     seed_queries.cypher       시딩용 Cypher 템플릿 참조
```

## 사용처

| 파일 | 로드 위치 | 로딩 함수 |
|------|----------|----------|
| `elasticsearch/*.json` | `src/connectors/impl/elasticsearch_connector.py` | `load_es_query()` |
| `mongo/query_*.json` | 참조 문서 (코드 내 직접 pipeline 구성) | — |
| `mongo/init_mongodb.js` | MongoDB 초기 셋업 시 수동 실행 | `mongosh` |
| `neo4j/init_neo4j.cypher` | Neo4j 초기 셋업 시 수동 실행 | `cypher-shell` |
| `neo4j/seed_queries.cypher` | 참조 문서 (코드 내 직접 Cypher 구성) | — |

## ES 쿼리 커스터마이징

ES 쿼리 JSON에는 `{query}` 플레이스홀더가 포함되어 있다.
`load_es_query()`가 `{query}`를 실제 검색어로 치환한 후 ES에 전달한다.

```json
{
  "_comment": "테이블/컬럼 메타 검색",
  "query": {
    "multi_match": {
      "query": "{query}",
      "fields": ["table_name^3", "table_description^2", "columns.name", "columns.desc"]
    }
  }
}
```

### 검색 정밀도 개선 방법

1. **필드 가중치 조정**: `table_name^3` → `^5`로 올리면 테이블명 정확 매칭 우선
2. **분석기 변경**: nori 한글 형태소 분석기 적용 시 ES 인덱스 설정도 함께 변경
3. **쿼리 유형 변경**: `multi_match` → `bool` + `should` 조합으로 정밀 제어
4. **필드 추가**: 새 메타 필드(갱신주기, 소유자 등) 추가 시 여기에 반영

## MongoDB 쿼리 참고

`query_*.json` 파일은 코드에서 직접 로드하지 않고, aggregation pipeline 설계 시 **참조 문서**로 사용된다.
실제 pipeline은 `src/connectors/impl/mongo_connector.py` 내부에 구현되어 있다.

### 컬렉션 추가 시
1. `init_mongodb.js`에 컬렉션 생성 + 인덱스 정의 추가
2. `mongo_connector.py`에 조회 메서드 추가
3. (선택) `query_*.json`에 aggregation 참조 문서 추가

## Neo4j 쿼리 참고

`seed_queries.cypher`는 코드에서 직접 로드하지 않고, 시딩 Cypher 설계 시 **참조 문서**로 사용된다.
실제 검색 Cypher는 `src/connectors/impl/neo4j_connector.py` 내부에 구현되어 있다.
시딩 로직은 `devtools/scripts/seed_neo4j.py`에서 인라인 Cypher로 실행한다.
