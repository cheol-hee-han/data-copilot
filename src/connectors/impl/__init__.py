"""커넥터 구현체 모듈.

interfaces.py에 정의된 SearchConnector / DatabaseConnector를 구현하는
실제 외부 시스템 커넥터를 포함한다.

인프라 커넥터 (항상 활성):
  - MongoConnector: 테이블 메타, 코드 메타, 비즈 용어사전
  - PostgresConnector: SQL 이력·체크포인터 등 공통 PostgreSQL DB
  - QdrantConnector: 업무 매뉴얼·SQL 이력 벡터 검색

업무 DB 커넥터 (system_db_overrides + target_db_schema_map 에 따라 선택 활성):
  - ADWConnector: ADW 업무 시스템 (Sybase IQ 16.1 드라이버)
  - BDPConnector: BDP 업무 시스템 (Cloudera CDP 7.1.9 Impala 드라이버)
  - CRPConnector: CRP 업무 시스템 (Oracle 19c/21c 드라이버)
  - TESTConnector: 외부망 테스트 전용 (PostgreSQL 드라이버,
    system_db_overrides={"ADW":"TEST"} 설정 시 활성)
"""
