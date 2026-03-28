// 두 테이블 간 최단 JOIN 경로 탐색
// params: $source, $target
// NOTE: *1..{max_hops} 부분은 코드에서 정수 치환 (Neo4j 파라미터 바인딩 미지원)
MATCH path = shortestPath(
    (a:Table {name: $source})-[:FK_TO*1..{max_hops}]-(b:Table {name: $target})
)
RETURN [n IN nodes(path) | n.name] AS tables,
       [r IN relationships(path) | {
           from_col: r.from_column,
           to_col: r.to_column,
           join_type: coalesce(r.join_type, 'INNER'),
           confidence: coalesce(r.confidence, 'INFERRED')
       }] AS joins
