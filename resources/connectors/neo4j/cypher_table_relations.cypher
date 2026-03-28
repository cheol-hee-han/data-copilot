// 특정 테이블의 직접 연결 관계 (FK, 주제영역 등) 조회
// params: $name
MATCH (t:Table {name: $name})-[r]-(neighbor)
RETURN type(r) AS rel_type,
       labels(neighbor)[0] AS neighbor_label,
       neighbor.name AS neighbor_name,
       r.from_column AS from_column,
       r.to_column AS to_column,
       r.confidence AS confidence
