// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Neo4j 온톨로지 그래프 스키마 초기화
//
// 대상 DB: neo4j (기본)
// 노드 6종: Table, Column, DomainConcept, CodeDefinition, SubjectArea, QueryCondition
// 엣지 9종: BELONGS_TO, FK_TO, IN_AREA, RESOLVED_BY, MEASURED_BY,
//           COMPOSED_OF, APPLIES_TO, PARENT_OF, IMPLIES_CONDITION
//
// 실행:
//   cat resources/connectors/neo4j/init_neo4j.cypher | cypher-shell -u neo4j -p <password>
//
// 또는 Docker:
//   docker exec dc-neo4j cypher-shell -u neo4j -p neo4j_pass < init_neo4j.cypher
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

// ── 1. 제약조건 (Uniqueness) ──

CREATE CONSTRAINT table_name_unique IF NOT EXISTS
  FOR (t:Table) REQUIRE t.name IS UNIQUE;

CREATE CONSTRAINT domain_concept_unique IF NOT EXISTS
  FOR (d:DomainConcept) REQUIRE d.name IS UNIQUE;

CREATE CONSTRAINT subject_area_unique IF NOT EXISTS
  FOR (s:SubjectArea) REQUIRE s.name IS UNIQUE;

CREATE CONSTRAINT query_condition_unique IF NOT EXISTS
  FOR (q:QueryCondition) REQUIRE q.pattern IS UNIQUE;

// ── 2. 검색 인덱스 ──

// 테이블 영문명 + 한글명 풀텍스트 검색
CREATE FULLTEXT INDEX table_text_search IF NOT EXISTS
  FOR (t:Table) ON EACH [t.name, t.alt_name];

// 도메인 개념명 + 정의 풀텍스트 검색
CREATE FULLTEXT INDEX concept_text_search IF NOT EXISTS
  FOR (d:DomainConcept) ON EACH [d.name, d.definition];

// 컬럼 소속 테이블 인덱스 ($lookup 대응)
CREATE INDEX column_table_idx IF NOT EXISTS
  FOR (c:Column) ON (c.table_name);

// 코드값 검색 인덱스
CREATE INDEX code_field_value_idx IF NOT EXISTS
  FOR (c:CodeDefinition) ON (c.code_field, c.code_value);

// 코드명 텍스트 검색
CREATE FULLTEXT INDEX code_name_search IF NOT EXISTS
  FOR (c:CodeDefinition) ON EACH [c.code_name, c.code_field];
