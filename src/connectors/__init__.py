"""외부 시스템 커넥터 패키지.

구조:
    interfaces.py  — 커넥터 추상 인터페이스
                     (BaseConnector, SearchConnector, DatabaseConnector)
    manager.py     — 커넥터 싱글턴 통합 관리자 (ConnectorManager)
    dummy_data.py  — Dummy 모드용 샘플 데이터
    impl/          — 인터페이스 구현체
        postgres_connector.py  — PostgreSQL 공통 메타 DB (이력·체크포인터)
        qdrant_connector.py    — 업무 매뉴얼·SQL 이력 벡터 검색
        adw_connector.py       — ADW 업무 시스템 (Sybase IQ 드라이버)
        bdp_connector.py       — BDP 업무 시스템 (Impala 드라이버)
        crp_connector.py       — CRP 업무 시스템 (Oracle 드라이버)
        test_connector.py      — 외부망 테스트용 (PostgreSQL 드라이버)
        hive_connector.py      — Hive 드라이버 (향후 업무 시스템 연동 대기)
"""
