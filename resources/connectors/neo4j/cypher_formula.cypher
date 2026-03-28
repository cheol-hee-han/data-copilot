// 계수산출식 재귀 분해 — DomainConcept → Column → Table 매핑
// params: $name
MATCH (root:DomainConcept {name: $name})
OPTIONAL MATCH path = (root)-[:COMPOSED_OF*1..5]->(leaf:DomainConcept)
OPTIONAL MATCH (leaf)-[m:MEASURED_BY]->(col:Column)-[:BELONGS_TO]->(t:Table)
WITH root, leaf, m, col, t, path,
     CASE WHEN path IS NOT NULL
          THEN last(relationships(path))
          ELSE NULL END AS last_rel
RETURN root.name AS formula_name,
       root.definition AS formula_text,
       collect(DISTINCT {
           component: leaf.name,
           column: col.name,
           table: t.name,
           agg_function: m.agg_function,
           position: last_rel.position,
           operator: last_rel.operator
       }) AS components
