// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Neo4j 온톨로지 시딩용 Cypher 템플릿
//
// seed_neo4j.py 에서 파라미터($batch 등)와 함께 호출된다.
// 모든 쿼리는 MERGE 기반으로 idempotent (재실행 안전).
//
// 명명 규칙: SEED_{Phase}_{대상} (코드에서 키로 참조)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

// ── Phase 1: MongoDB → Neo4j 노드 생성 ──

// SEED_P1_TABLE: dpasset_table → (:Table) 노드
// params: $batch = [{name, alt_name, schema_name, desc, update_cycle, db_source}]
UNWIND $batch AS row
MERGE (t:Table {name: row.name})
SET t.alt_name = row.alt_name,
    t.schema_name = row.schema_name,
    t.description = row.desc,
    t.update_cycle = coalesce(row.update_cycle, ''),
    t.db_source = coalesce(row.db_source, '')

// SEED_P1_COLUMN: dpasset_column → (:Column) 노드 + BELONGS_TO
// params: $batch = [{name, alt_name, data_type, desc, pk, table_name}]
UNWIND $batch AS row
MERGE (c:Column {table_name: row.table_name, name: row.name})
SET c.alt_name = row.alt_name,
    c.data_type = coalesce(row.data_type, ''),
    c.description = coalesce(row.desc, ''),
    c.is_pk = coalesce(row.pk, false)
WITH c, row
MATCH (t:Table {name: row.table_name})
MERGE (c)-[:BELONGS_TO]->(t)

// SEED_P1_CODE: standard_code + standard_code_value → (:CodeDefinition) 노드
// params: $batch = [{code_field, code_value, code_name}]
UNWIND $batch AS row
MERGE (cd:CodeDefinition {code_field: row.code_field, code_value: row.code_value})
SET cd.code_name = row.code_name

// SEED_P1_CODE_PARENT: 동일 code_field 내 계층 (PARENT_OF는 Phase 2에서 추론)

// SEED_P1_GLOSSARY: glossary → (:DomainConcept) 초기 노드
// params: $batch = [{name, definition, synonyms, table_name}]
UNWIND $batch AS row
MERGE (d:DomainConcept {name: row.name})
SET d.definition = coalesce(row.definition, ''),
    d.synonyms = coalesce(row.synonyms, []),
    d.category = 'glossary',
    d.source = 'mongodb_seed'
WITH d, row
WHERE row.table_name IS NOT NULL AND row.table_name <> ''
MATCH (t:Table {name: row.table_name})
MERGE (d)-[:RESOLVED_BY {role: 'PRIMARY'}]->(t)

// ── Phase 2: 관계 추론 ──

// SEED_P2_FK: 추론된 FK 관계
// params: $batch = [{source, target, from_column, to_column, confidence, evidence}]
UNWIND $batch AS row
MATCH (a:Table {name: row.source}), (b:Table {name: row.target})
MERGE (a)-[r:FK_TO]->(b)
SET r.from_column = row.from_column,
    r.to_column = row.to_column,
    r.join_type = 'INNER',
    r.confidence = row.confidence,
    r.evidence = row.evidence

// SEED_P2_CODE_BIND: 코드 컬럼 바인딩 (CodeDefinition → Column)
// params: $batch = [{code_field, table_name, column_name}]
UNWIND $batch AS row
MATCH (cd:CodeDefinition {code_field: row.code_field})
MATCH (col:Column {table_name: row.table_name, name: row.column_name})
MERGE (cd)-[:APPLIES_TO]->(col)

// SEED_P2_SUBJECT_AREA: 주제영역 생성 + 테이블 소속
// params: $batch = [{area_name, area_desc, table_names}]
UNWIND $batch AS row
MERGE (s:SubjectArea {name: row.area_name})
SET s.description = row.area_desc
WITH s, row
UNWIND row.table_names AS tbl
MATCH (t:Table {name: tbl})
MERGE (t)-[:IN_AREA]->(s)

// ── Phase 3: 업무 규칙 (계수산출식) ──

// SEED_P3_FORMULA: 계수산출식 루트 + COMPOSED_OF
// params: $formula = {name, definition, category, components: [{name, position, operator, synonyms}]}
MERGE (root:DomainConcept {name: $formula.name})
SET root.definition = $formula.definition,
    root.category = coalesce($formula.category, '금융지표'),
    root.source = 'seed'
WITH root
UNWIND $formula.components AS comp
MERGE (sub:DomainConcept {name: comp.name})
SET sub.synonyms = coalesce(comp.synonyms, [])
MERGE (root)-[r:COMPOSED_OF]->(sub)
SET r.position = comp.position,
    r.operator = comp.operator

// SEED_P3_MEASURED_BY: DomainConcept → Column 매핑
// params: $batch = [{concept_name, table_name, column_name, agg_function}]
UNWIND $batch AS row
MATCH (d:DomainConcept {name: row.concept_name})
MATCH (col:Column {table_name: row.table_name, name: row.column_name})
MERGE (d)-[m:MEASURED_BY]->(col)
SET m.agg_function = row.agg_function
