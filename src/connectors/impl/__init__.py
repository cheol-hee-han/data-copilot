"""커넥터 구현체 모듈.

interfaces.py에 정의된 SearchConnector / DatabaseConnector를 구현하는
실제 외부 시스템 커넥터를 포함한다.

온라인 개발 환경:
  - ElasticSearchConnector: 테이블 메타, 보고서 SQL, 코드 메타
  - InfoDBConnector / HistoryDBConnector: 정보계·이력 PostgreSQL
  - QdrantConnector: 업무 매뉴얼 벡터 검색

폐쇄망 환경:
  - ImpalaConnector: Cloudera CDP 7.1.9 Impala 4.0
  - HiveConnector: Cloudera CDP 7.1.9 Hive 3.1.3
  - SybaseIQConnector: SAP Sybase IQ 16.1
"""
