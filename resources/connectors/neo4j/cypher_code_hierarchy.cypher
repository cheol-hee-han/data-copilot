// 코드값 계층 + 적용 컬럼/테이블 조회
// params: $code_name
MATCH (code:CodeDefinition)
WHERE code.code_name CONTAINS $code_name
      OR code.code_field CONTAINS $code_name
OPTIONAL MATCH (code)-[:APPLIES_TO]->(col:Column)-[:BELONGS_TO]->(t:Table)
OPTIONAL MATCH (code)-[:PARENT_OF*0..2]->(child:CodeDefinition)
RETURN code.code_field AS code_field,
       code.code_value AS code_value,
       code.code_name AS code_name,
       col.name AS column_name,
       t.name AS table_name,
       collect(DISTINCT {
           value: child.code_value,
           name: child.code_name
       }) AS children
