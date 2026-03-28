// 도메인 개념 → 관련 테이블 + FK 이웃 확장
// params: $concept
MATCH (c:DomainConcept)-[res:RESOLVED_BY]->(t:Table)
WHERE c.name CONTAINS $concept
      OR ANY(syn IN coalesce(c.synonyms, []) WHERE syn CONTAINS $concept)
OPTIONAL MATCH (t)-[:FK_TO*1..2]-(neighbor:Table)
RETURN t.name AS table_name,
       t.alt_name AS alt_name,
       t.granularity AS granularity,
       t.refresh_cycle AS refresh_cycle,
       res.role AS role,
       collect(DISTINCT neighbor.name) AS joinable_tables
