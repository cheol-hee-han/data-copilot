---
name: project_graph_db_ontology
description: SQL 온톨로지 구현 그래프 DB 선정 리서치 (2026-03-25): Neo4j CE 권고, 라이선스 기각 이유, 노드/엣지 모델 설계
type: project
---

Neo4j Community Edition (GPLv3)을 SQL 온톨로지 구현 그래프 DB로 권고 확정 (2026-03-25).

**Why:** RHEL 9.4 폐쇄망 + async Python + OSI 승인 라이선스 3가지 조건을 동시에 충족하는 유일한 후보. ArangoDB/Memgraph/FalkorDB는 BSL/SSPL로 은행 법무팀 리스크. Apache AGE는 async 미지원으로 기각. JanusGraph는 130K 노드 규모에 과잉.

**How to apply:** 향후 그래프 DB 관련 구현 질문 시 Neo4j CE + neo4j Python driver 5.x (AsyncGraphDatabase) 조합을 기준으로 설계. Apache AGE는 "추가 인프라 Zero" 전략으로만 고려.

## 핵심 기각 근거
- ArangoDB 3.12+: BSL 1.1, 100GB 생산 제한, SaaS 금지
- Memgraph: BSL 1.1, OSI 미승인, 생산환경 사용 조건 불명확
- FalkorDB: SSPL v1, OSI 미승인
- JanusGraph: 단일 노드에도 Cassandra+Elasticsearch 필요, 과잉
- Apache AGE: Python async 드라이버 미지원 (psycopg3 기반이나 공식 미지원)

## 권고 스택
- DB: Neo4j Community Edition (GPLv3, RHEL 9.4 공식 RPM)
- 드라이버: neo4j 5.28.x (AsyncGraphDatabase)
- Java: OpenJDK 21 (RHEL 9 기본 제공)

## 그래프 모델 핵심 노드·엣지
- 노드: Table, Column, DomainConcept, CodeDefinition, JoinPath
- 엣지: HAS_COLUMN, FK_TO, RESOLVED_BY, REQUIRES_COLUMN, COMPOSED_OF, PARENT_OF, USES_CODE
- FK_TO 엣지에 confidence 속성으로 확신도 관리 (명시=1.0, 추론=0.7)

## 학술 근거
- SchemaGraphSQL (arXiv 2505.18363, 2025): FK 기반 스키마 그래프 + 경로탐색 → BIRD SOTA
- DCG-SQL (ACL 2025): 테이블 재현율 97.4%, 컬럼 재현율 95.5%
- CHESS (2024): 스키마 선택에 유사도 검색과 그래프 탐색 병행

## 보고서 위치
c:\Users\cjfgm\Desktop\workspace\data-copilot\docs\research\20260325-graph-db-sql-ontology.md
