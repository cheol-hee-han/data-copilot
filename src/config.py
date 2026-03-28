"""애플리케이션 설정 모듈.

pydantic-settings 기반으로 .env 파일 및 환경 변수에서
전체 설정을 로드한다. LLM·DB·ES·Qdrant·Redis 접속 정보,
임베딩/Reranker 모델 경로, 파이프라인 제어 파라미터,
시각화 레이아웃, 금액 단위 기준값 등을 포함한다.

폐쇄망 배포 시 .env 파일 교체만으로 LLM 프로바이더·
모델 캐시 경로·DB 접속 정보를 전환할 수 있도록 설계되었다.
모듈 수준 싱글턴 settings 를 export 하여 전역에서 참조한다.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """환경 변수 기반 애플리케이션 설정."""

    # LLM Provider: "anthropic" 또는 "openai_compatible" (Groq, OpenRouter 등)
    llm_provider: str = "anthropic"

    # Anthropic API
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-4-20250514"

    # OpenAI Compatible API (Groq, OpenRouter, 폐쇄망 로컬 LLM 등)
    openai_api_key: str = ""
    openai_base_url: str = ""  # 예: https://api.groq.com/openai/v1
    openai_referer: str = "https://data-copilot.local"
    openai_title: str = "Data Copilot"

    # 정보계 DB (읽기 전용)
    info_db_host: str = "localhost"
    info_db_port: int = 5432
    info_db_name: str = "info_db"
    info_db_user: str = "readonly_user"
    info_db_password: str = ""

    # SQL 이력 DB
    history_db_host: str = "localhost"
    history_db_port: int = 5432
    history_db_name: str = "history_db"
    history_db_user: str = "history_user"
    history_db_password: str = ""

    # ElasticSearch
    es_host: str = "localhost"
    es_port: int = 9200
    es_user: str = "elastic"
    es_password: str = ""
    es_table_meta_index: str = "table_meta"
    es_report_sql_index: str = "report_sql"
    es_code_meta_index: str = "code_meta"
    es_table_meta_size: int = 10
    es_report_sql_size: int = 5
    es_code_meta_size: int = 20

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_search_top_k: int = 3
    qdrant_collection_name: str = "biz_manual"
    # sql_history 벡터 검색
    qdrant_sql_history_collection: str = "sql_history"
    qdrant_sql_history_top_k: int = 5  # Reranker 후 최종 반환 건수
    qdrant_sql_history_prefetch_limit: int = 20  # 하이브리드 검색 후보 수

    # MongoDB (테이블 메타 + 코드 메타 + 비즈 메타)
    mongo_host: str = "localhost"
    mongo_port: int = 27017
    mongo_user: str = "mongoadmin"
    mongo_password: str = ""
    mongo_database: str = "meta_db"
    mongo_table_meta_collection: str = "dpasset_table"
    mongo_column_meta_collection: str = "dpasset_column"
    mongo_code_meta_collection: str = "standard_code"
    mongo_code_value_collection: str = "standard_code_value"
    mongo_glossary_collection: str = "glossary"
    mongo_biz_meta_collection: str = "biz_meta"
    mongo_table_meta_size: int = 10   # 테이블 메타 최대 반환 건수
    mongo_code_meta_size: int = 10    # 코드 메타 최대 반환 건수
    mongo_request_timeout: int = 10   # MongoDB 요청 타임아웃 (초)

    # Neo4j (온톨로지 그래프)
    neo4j_host: str = "localhost"
    neo4j_port: int = 7687
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    neo4j_pool_size: int = 10
    neo4j_request_timeout: int = 10    # Cypher 실행 타임아웃 (초)
    neo4j_cache_ttl: int = 300         # 온톨로지 캐시 TTL (초)
    neo4j_max_path_hops: int = 4       # JOIN 경로 최대 홉 수
    neo4j_batch_size: int = 500        # 시딩 배치 크기

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # 세션 백엔드: "memory" (인메모리 dict) 또는 "redis"
    session_backend: str = "memory"
    session_ttl: int = 1800  # 대화 이력 TTL (초, 기본 30분, 슬라이딩)
    session_clarify_ttl: int = 300  # 명확화 상태 TTL (초, 기본 5분)
    session_max_history: int = 20  # 대화 이력 최대 턴 수

    # ── 임베딩 모델 (BGE-M3, Dense + Sparse 하이브리드) ──
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024  # BGE-M3 Dense 벡터 차원
    embedding_use_fp16: bool = False  # FP16 (GPU 전용, CPU에서는 False 필수)
    embedding_cache_path: str = ""  # 폐쇄망: 오프라인 모델 캐시 경로
    embedding_batch_size: int = 64  # 임베딩 배치 크기

    # ── Reranker (BGE-Reranker-v2-m3, Cross-Encoder) ──
    reranker_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_use_fp16: bool = False  # FP16 (GPU 전용, CPU에서는 False 필수)
    reranker_cache_path: str = ""  # 폐쇄망: 오프라인 모델 캐시 경로
    reranker_top_k: int = 5  # 재순위 후 최종 반환 건수
    # ── Reranker CPU 최적화 (ONNX Runtime) ──
    # 백엔드: "pytorch" (기본) 또는 "onnx" (CPU 최적화)
    reranker_backend: str = "onnx"
    # ONNX 모델 경로 (빈 문자열이면 PyTorch 모델에서 자동 변환)
    reranker_onnx_path: str = ""
    # INT8 동적 양자화 (CPU 2.5~3.5x 가속, 품질 손실 <1%)
    reranker_quantize: bool = True
    # CPU 스레드 수 (0 = 물리 코어 수 자동 감지)
    reranker_cpu_threads: int = 0
    # 사전 필터링: 벡터 스코어 하위 후보 제거 (0.0 = 비활성화)
    reranker_score_threshold: float = 0.0
    # 사전 필터링 후 최소 보장 후보 수
    reranker_min_candidates: int = 15

    # ── Impala (Cloudera CDP 7.1.9, HiveServer2 Thrift) ──
    impala_host: str = "localhost"
    impala_port: int = 21050
    impala_auth_mechanism: str = "LDAP"   # NOSASL / PLAIN / LDAP / GSSAPI
    impala_user: str = ""
    impala_password: str = ""
    impala_use_ssl: bool = False
    impala_database: str = "default"
    impala_query_timeout: int = 60        # 쿼리 실행 타임아웃 (초)

    # ── Hive (3.1.3, Cloudera CDP 7.1.9, HiveServer2 Thrift) ──
    hive_host: str = "localhost"
    hive_port: int = 10000
    hive_auth_mechanism: str = "LDAP"     # NOSASL / PLAIN / LDAP / GSSAPI
    hive_user: str = ""
    hive_password: str = ""
    hive_use_ssl: bool = False
    hive_database: str = "default"
    hive_query_timeout: int = 120         # Hive는 Impala보다 느리므로 여유 있게

    # ── Sybase IQ (16.1) ──
    sybase_driver: str = "native"     # "native" (sqlanydb) 또는 "odbc" (pyodbc)
    sybase_host: str = "localhost"
    sybase_port: int = 2638
    sybase_database: str = ""
    sybase_user: str = ""
    sybase_password: str = ""
    sybase_odbc_driver: str = "SQL Anywhere 16"  # ODBC 방식 전용 (odbcinst -q -d로 확인)
    sybase_charset: str = "UTF-8"
    sybase_query_timeout: int = 60

    # DB 타임아웃
    db_pool_timeout: int = 30            # 커넥션 풀 대기 타임아웃 (초)
    # 쿼리 실행 타임아웃 (초, asyncpg command_timeout)
    db_query_timeout: int = 60

    # ES / Qdrant 타임아웃
    es_request_timeout: int = 10         # ES 검색 요청 타임아웃 (초)
    qdrant_request_timeout: int = 10     # Qdrant 검색 요청 타임아웃 (초)

    # Dummy 모드: True 면 외부 시스템 없이 내장 샘플 데이터로 동작
    use_dummy: bool = True

    # 배포 모드: "external" (외부망, PostgreSQL) | "internal" (내부망, ADW+BDP)
    deployment_mode: str = "external"
    default_db_source: str = "adw"      # 시스템코드 파싱 실패 시 기본 DB

    # ── 파이프라인 제어 ──
    sql_max_retry: int = 2              # SQL 재생성 최대 재시도 횟수
    clarification_max_turns: int = 2    # 명확화 최대 왕복 횟수
    max_input_length: int = 500         # 사용자 입력 최대 길이 (문자 수)
    max_sessions: int = 1000            # 동시 세션 최대 수

    # ── 에이전틱 코어 ──
    agentic_core_enabled: bool = True   # True: 에이전틱 코어, False: 기존 선형
    # LLM Heavy 노드 설정 (대형 모델: True, 소형 모델: False)
    plan_use_llm: bool = True           # planner에서 LLM 사용
    validate_layer2b_enabled: bool = True  # Layer 2b LLM 의미 검증 활성화
    replan_use_llm: bool = True         # recovery_planner에서 LLM 사용
    # 에이전틱 코어 타임아웃 (초)
    agentic_tool_timeout: float = 10.0  # 개별 도구 호출 타임아웃 (C-12)
    agentic_total_timeout: float = 120.0  # 서브그래프 전체 타임아웃

    # ── LLM 호출 ──
    llm_parse_max_retry: int = 2        # 포맷 불일치 시 최대 재시도 횟수
    llm_default_max_tokens: int = 1000  # LLM 기본 max_tokens
    llm_default_timeout: float = 15.0   # LLM 기본 타임아웃 (초)
    llm_long_timeout: float = 30.0      # SQL생성/분석/포맷팅 등 긴 작업 타임아웃 (초)
    llm_context_timeout: float = 60.0   # 컨텍스트 수집 전체 타임아웃 (초)
    llm_format_max_tokens: int = 2000   # 포맷팅/분석 응답 max_tokens
    llm_svg_max_tokens: int = 4000      # SVG 생성 max_tokens

    # ── 질의 정규화 ──
    # 질의 정규화 활성화 여부
    normalization_enabled: bool = True
    # Phase 2 교차 검증 활성화 (소형 LLM: True 권장)
    normalization_phase2_enabled: bool = True
    # 정규화 LLM 응답 max_tokens
    normalization_max_tokens: int = 3000

    # ── LLM 보조 파라미터 ──
    llm_concurrency_limit: int = 3      # 동시 LLM 호출 제한 (테이블 보강 등)
    min_description_length: int = 20    # 테이블 설명 보강 판단 최소 길이
    min_rows_for_visualization: int = 3  # 시각화 판단 최소 행 수
    format_max_rows: int = 50           # 포맷팅 프롬프트에 포함할 최대 행 수
    analysis_max_rows: int = 100        # 분석/시각화 프롬프트에 포함할 최대 행 수

    # LangSmith (외부망 개발 환경 전용, 폐쇄망에서는 False)
    langsmith_enabled: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "data-copilot"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # Evaluation Tracker (자체 트래킹, 폐쇄망 호환)
    eval_tracker_enabled: bool = True
    eval_tracker_output_dir: str = "devtools/evaluation/traces"

    # Application
    log_level: str = "INFO"
    log_format: str = "console"  # "console" or "json"
    max_query_rows: int = 10000

    # ── 3순위: SVG 차트 레이아웃 (프론트엔드에서 조절하는 게 맞아 변경 빈도 낮음) ──
    chart_width: int = 600
    chart_height: int = 400
    chart_margin_left: int = 80
    chart_margin_right: int = 40
    chart_margin_top: int = 60
    chart_margin_bottom: int = 70

    # ── 3순위: 로그 미리보기 길이 (내부 디버깅용, 자주 안 바뀜) ──
    log_long_value_threshold: int = 80
    log_separator_width: int = 72

    # ── 3순위: 금액 단위 기준값 (한국 금융 고정, 바뀔 일 없음) ──
    krw_eok_threshold: int = 1_0000_0000   # 억원 기준 (1억)
    krw_man_threshold: int = 1_0000        # 만원 기준 (1만)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
