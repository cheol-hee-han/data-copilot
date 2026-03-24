"""외부 시스템 커넥터 패키지.

구조:
    interfaces.py  — 커넥터 추상 인터페이스 (BaseConnector, SearchConnector, DatabaseConnector)
    manager.py     — 커넥터 싱글턴 통합 관리자 (ConnectorManager)
    dummy_data.py  — Dummy 모드용 샘플 데이터
    impl/          — 인터페이스 구현체
        elasticsearch_connector.py  — ES 메타/보고서 검색
        postgres_connector.py       — 정보계·이력 PostgreSQL
        qdrant_connector.py         — 업무 매뉴얼 벡터 검색
        impala_connector.py         — Cloudera CDP 7.1.9 Impala (폐쇄망)
        hive_connector.py           — Cloudera CDP 7.1.9 Hive 3.1.3 (폐쇄망)
        sybase_connector.py         — SAP Sybase IQ 16.1 (폐쇄망)
"""
