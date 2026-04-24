"""애플리케이션 설정 모듈.

작성자: 한철희 / 최종수정: 2026-04-07 12:56:37

pydantic-settings 기반으로 .env 파일 및 환경 변수에서
설정을 로드한다. 환경별로 달라지는 값(접속 정보, API 키 등)은
.env에서, 파이프라인 상수·레이아웃·단위 기준값 등 고정값은
코드 기본값으로만 관리한다.

폐쇄망 배포 시 .env 파일 교체만으로 LLM 프로바이더·
모델 캐시 경로·DB 접속 정보를 전환할 수 있도록 설계되었다.
모듈 수준 싱글턴 settings 를 export 하여 전역에서 참조한다.
"""

from typing import Literal

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings


class DbConnectionInfo(BaseModel):
    """DB 연결에 필요한 최소 정보 묶음 (Value Object)."""

    host: str = "localhost"
    port: int = 5432
    name: str = ""
    user: str = ""
    password: str = ""

    @property
    def dsn(self) -> str:
        """PostgreSQL DSN 문자열을 반환한다 (비밀번호 제외, 로깅 안전)."""
        return (
            f"host={self.host} port={self.port} "
            f"dbname={self.name} user={self.user}"
        )


class Settings(BaseSettings):
    """환경 변수 기반 애플리케이션 설정.

    pydantic-settings를 사용하여 .env 파일 및 환경 변수에서 설정을 로드한다.
    폐쇄망 배포 시 .env 파일만 교체하면 LLM·DB·벡터DB 등 전체 인프라를
    전환할 수 있도록 모든 외부 시스템 접속 정보를 환경 변수로 관리한다.
    """

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

    # ── IBK Custom LLM Gateway (폐쇄망 내부 LLM 관리툴) ──
    # 요청: POST {base_url}/gpt/api/{thread_id}, body={"token","extra":{placeholder: prompt}}
    # 응답: {"question","answer","status","threadId","updatedAt"}
    # 시스템 프롬프트는 관리툴에 단일 placeholder 로 등록, 코드는 system+messages 를
    # 조립한 단일 문자열을 extra[placeholder] 에 통째로 전달한다 (passthrough).
    ibk_base_url: str = ""                  # 예: http://dibkpgt.ibk.co.kr:35001
    ibk_token: str = ""                      # 관리툴 발급 단일 인증 토큰
    ibk_placeholder_name: str = "prompt"     # 관리툴 프롬프트의 placeholder 변수명
    ibk_default_timeout: float = 60.0        # IBK 게이트웨이 기본 타임아웃 (초)

    # 개발/테스트용 DB (읽기 전용, 폐쇄망 전환 시 제거)
    # 폐쇄망에서는 Sybase IQ/Impala/Oracle 로 대체된다.
    test_db_host: str = "localhost"
    test_db_port: int = 5432
    test_db_name: str = "test_db"
    test_db_user: str = "readonly_user"
    test_db_password: str = ""
    test_db_default_schema: str = "ADWOWN"

    # PostgreSQL DB (SQL 이력 + checkpointer 영속화 등 공통 메타 저장소)
    postgres_db_host: str = "localhost"
    postgres_db_port: int = 5432
    postgres_db_name: str = "postgres_db"
    postgres_db_user: str = "postgres_user"
    postgres_db_password: str = ""

    # ── Checkpointer ──
    checkpointer_backend: str = "memory"  # "memory" | "postgres"
    checkpointer_pool_min: int = 2
    checkpointer_pool_max: int = 10
    checkpointer_thread_ttl_days: int = 30  # 0=무제한
    # Postgres search_path (스키마 우선순위). Sybase IQ/Impala 전환 시 공백 가능
    checkpointer_search_path: str = "bdptbl,public"

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_search_top_k: int = 3
    qdrant_collection_name: str = "biz_manual"
    # sql_history 벡터 검색
    qdrant_sql_history_collection: str = "sql_history"
    qdrant_sql_history_top_k: int = 5  # Reranker 후 최종 반환 건수
    qdrant_sql_history_prefetch_limit: int = 20  # 하이브리드 검색 후보 수
    qdrant_max_prefetch: int = 100  # exclude_ids 누적 시 prefetch 상한
    # search_manual exclude_ids 누적 시 limit 상한
    qdrant_manual_max_limit: int = 30
    # 임베딩 계산용 ThreadPoolExecutor 워커 수 (CPU/GPU 코어 고려)
    qdrant_embed_workers: int = 2

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
    mongo_biz_term_collection: str = "biz_term"
    mongo_biz_meta_collection: str = "biz_meta"
    mongo_table_meta_size: int = 10   # 테이블 메타 최대 반환 건수
    mongo_code_meta_size: int = 10    # 코드 메타 최대 반환 건수
    mongo_biz_term_size: int = 20     # 비즈니스 용어 기본 limit (기존에는 limit 없었음)
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
    neo4j_cache_max_entries: int = 512  # LRU 캐시 상한 (메모리 누수 방지)
    neo4j_max_path_hops: int = 4       # JOIN 경로 최대 홉 수
    neo4j_batch_size: int = 500        # 시딩 배치 크기

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # CancelStore/ActiveRunStore 백엔드: "memory" 또는 "redis"
    # 멀티 워커 배포 시 "redis"로 설정하여 원자적 취소/크래시 감지 활성화.
    redis_backend: str = "memory"
    # 턴 단위: 사용자 1건 + AI 1건 = 2턴 (단방향 메시지 기준)
    prompt_history_window: int = 0  # LLM 프롬프트에 포함할 최근 턴 수 (0=전체)
    # 활성 파이프라인 레지스트리 TTL (Redis 백엔드 사용 시 stale 안전망)
    # 워커 kill -9 등으로 unregister 가 실행되지 못한 엔트리의 자동 만료 시간.
    # 파이프라인 최대 실행시간보다 길게 잡는다.
    active_run_ttl_seconds: int = 1800
    # 세션당 브로드캐스트 대기 메시지 버퍼 상한 (OOM/메모리 폭주 방지)
    message_store_pending_max: int = 50

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
    # ODBC 방식 전용 (odbcinst -q -d 로 확인)
    sybase_odbc_driver: str = "SQL Anywhere 16"
    sybase_charset: str = "UTF-8"
    sybase_query_timeout: int = 60

    # ── Oracle (19c/21c, python-oracledb) ──
    oracle_host: str = "localhost"
    oracle_port: int = 1521
    oracle_service_name: str = ""     # SID 대신 SERVICE_NAME 권장
    oracle_user: str = ""
    oracle_password: str = ""
    oracle_default_schema: str = ""   # 기본 스키마 (비어있으면 사용자 기본)
    oracle_thick_mode: bool = False   # True 시 Instant Client 필요
    oracle_query_timeout: int = 60

    # DB 커넥션 풀 (멀티워커 시 workers × pool_size ≤ DB max_connections 확인)
    db_pool_timeout: int = 30            # 커넥션 풀 대기 타임아웃 (초)
    db_pool_size: int = 5                # 기본 커넥션 풀 크기
    db_pool_max_overflow: int = 10       # 풀 초과 시 최대 추가 커넥션
    db_pool_recycle: int = 1800          # 커넥션 재활용 주기 (초, DB idle timeout 방어)
    # 쿼리 실행 타임아웃 (초, asyncpg command_timeout)
    db_query_timeout: int = 60

    # Qdrant 타임아웃
    qdrant_request_timeout: int = 10     # Qdrant 검색 요청 타임아웃 (초)
    health_check_timeout: float = 5.0    # 커넥터별 health_check 타임아웃 (초)

    # Dummy 모드: True 면 외부 시스템 없이 내장 샘플 데이터로 동작
    use_dummy: bool = True

    # ── 멀티 DB 라우팅 ──
    # 업무 DB 시스템 override — 외부망 테스트 환경 전환용.
    # 비어 있으면 identity 매핑 (ADW→ADW, BDP→BDP, CRP→CRP).
    # 외부망 테스트 환경에서만 {"ADW": "TEST"} 로 설정.
    # 폐쇄망 전환 시 이 값을 제거하거나 {} 로 두면 identity 동작.
    system_db_overrides: dict[str, str] = {}

    # push-down 필터링용 시스템 코드 → 스키마명 매핑.
    # target_db_schema_map 의 키 집합이 "알려진 시스템 코드의 단일 진실원" 역할을 겸한다.
    # 시스템 추가 시 이 dict 과 manager.py factory 두 곳에만 등록하면 된다.
    target_db_schema_map: dict[str, str] = {
        "ADW": "ADWOWN",
        # "BDP": "BDPOWN",
        # "CRP": "CRPOWN",
    }

    # 강제 타깃 시스템 코드 (미지정이면 SELECTED 테이블 기반 동적 결정).
    # 값은 target_db_schema_map 의 키(시스템코드) 중 하나여야 한다 (예: "ADW").
    target_db_code: str = "ADW"

    @property
    def target_schema(self) -> str:
        """target_db_code 에 매핑된 schema_name (push-down 필터용)."""
        return self.target_db_schema_map.get(self.target_db_code, "")

    def resolve_system_connector(self, system_code: str) -> str:
        """시스템 코드 → 실제 커넥터 이름 (override 적용).

        외부망: {"ADW":"TEST"} 로 override → "TEST" 반환.
        폐쇄망: {} → identity, "ADW" 반환.
        """
        return self.system_db_overrides.get(system_code, system_code)

    # ── 파이프라인 제어 ──
    sql_max_retry: int = 2              # SQL 재생성 최대 재시도 횟수
    max_input_length: int = 500         # 사용자 입력 최대 길이 (문자 수)
    clarification_max_turns: int = 3    # 명확화 최대 왕복 횟수

    # ── 에이전틱 코어 ──
    validate_layer2b_enabled: bool = True  # Layer 2b LLM 의미 검증 활성화
    # 에이전틱 코어 타임아웃 (초)
    agentic_tool_timeout: float = 10.0  # 개별 도구 호출 타임아웃 (C-12)
    agentic_total_timeout: float = 180.0  # 서브그래프 전체 타임아웃
    # 에이전틱 루프 제어 상수
    max_tool_calls: int = 40            # 도구 호출 총량 한도
    max_replans: int = 10               # 재계획 최대 횟수
    max_generates: int = 0              # SQL 생성 시도 최대 횟수 (0 = 무제한, MAX_TOOL_CALLS·MAX_REPLANS·MAX_LOCAL_FIXES가 상한 역할)
    max_local_fixes: int = 5            # 로컬 문법 교정 최대 횟수
    force_generate_after_replans: int = 5  # N회 replan 후 강제 SQL 생성 진입

    # ── Recovery Agent ──
    max_conflicted_bounces: int = 2         # CONFLICTED 왕복 가드
    max_ask_user_rounds: int = 2            # recovery_agent ask_user 최대 횟수

    # ── LLM 호출 ──
    llm_transport_max_retry: int = 5    # SDK 레벨 전송 재시도 (429/500/503/네트워크)

    # ── LLM 서킷브레이커 (외부 API 연속 실패 시 fast-fail) ──
    llm_cb_enabled: bool = True                  # False 로 내리면 CB 투명 통과
    llm_cb_fail_threshold: int = 5               # 연속 실패 임계 (이상이면 OPEN)
    llm_cb_reset_timeout_sec: float = 30.0       # OPEN → HALF_OPEN 대기 시간(초)

    llm_parse_max_retry: int = 2        # 포맷 불일치 시 최대 재시도 횟수
    llm_default_max_tokens: int = 3000  # LLM 기본 max_tokens
    llm_default_timeout: float = 15.0   # LLM 기본 타임아웃 (초)
    llm_long_timeout: float = 30.0      # SQL생성/분석/포맷팅 등 긴 작업 타임아웃 (초)
    llm_context_timeout: float = 60.0   # 컨텍스트 수집 전체 타임아웃 (초)
    llm_format_max_tokens: int = 3000   # 포맷팅/분석 응답 max_tokens
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
    min_rows_for_visualization: int = 1  # 시각화 판단 최소 행 수 (info_card 지원)
    format_max_rows: int = 50           # 포맷팅 프롬프트에 포함할 최대 행 수
    analysis_max_rows: int = 100        # 분석/시각화 프롬프트에 포함할 최대 행 수
    # 분석 응답 포맷: "markdown"(4섹션 스트리밍용) | "json"(레거시)
    analyzer_output_format: Literal["markdown", "json"] = "markdown"
    ui_result_max_rows: int = 500       # stream.end result_data에 포함할 최대 행 수

    # Evaluation Tracker (자체 트래킹, 폐쇄망 호환)
    eval_trace_json_enabled: bool = True        # 기계 분석용 JSON
    eval_trace_report_enabled: bool = True      # 기존 5섹션 보고서
    eval_trace_reasoning_enabled: bool = True   # 신규 reasoning flow
    eval_tracker_output_dir: str = "logs/traces"

    # Application
    log_level: str = "INFO"
    log_format: str = "console"  # "console" or "json"
    log_backup_count: int = 15  # 롤링 보관 일수 (0=무제한)
    # PII 마스킹 (False: 로그/트레이스/응답에서 비활성화)
    pii_masking_enabled: bool = True
    max_query_rows: int = 10000

    # 스트리밍 마스터 킬스위치 — True 시 클라이언트 streaming 플래그 무시하고 강제 OFF.
    # 폐쇄망 LLM(Solar Pro 2 등) 스트리밍 불안정 시 운영 측에서 즉시 차단 가능.
    streaming_disabled: bool = False

    # ── 3순위: SVG 차트 레이아웃 (프론트엔드에서 조절하는 게 맞아 변경 빈도 낮음) ──
    chart_width: int = 600
    chart_height: int = 400
    chart_margin_left: int = 80
    chart_margin_right: int = 40
    chart_margin_top: int = 60
    chart_margin_bottom: int = 70

    # ── 로그/트래커 출력 절삭 (0 = 무제한, 전부 출력) ──
    trace_truncate_limit: int = 0   # trace JSON (callback_handler)
    log_truncate_limit: int = 0     # structlog 필드값 (노드/커넥터 로그)

    # ── 3순위: 로그 미리보기 길이 (내부 디버깅용, 자주 안 바뀜) ──
    log_long_value_threshold: int = 80
    log_separator_width: int = 72

    # ── 3순위: 금액 단위 기준값 (한국 금융 고정, 바뀔 일 없음) ──
    krw_eok_threshold: int = 1_0000_0000   # 억원 기준 (1억)
    krw_man_threshold: int = 1_0000        # 만원 기준 (1만)

    @property
    def postgres_db(self) -> DbConnectionInfo:
        """PostgreSQL 공통 DB(이력/체크포인터 등) 연결 정보를 Value Object로 반환한다."""
        return DbConnectionInfo(
            host=self.postgres_db_host,
            port=self.postgres_db_port,
            name=self.postgres_db_name,
            user=self.postgres_db_user,
            password=self.postgres_db_password,
        )

    # 민감 필드 키워드 — logger.py의 _SENSITIVE_KEY_PARTS와 동일 기준
    _MASK_KEYWORDS: tuple[str, ...] = (
        "password", "api_key", "secret", "token", "credential",
    )

    def __repr__(self) -> str:
        """민감 필드를 마스킹하여 로깅 안전하게 출력한다."""
        safe: dict[str, object] = {}
        for key in self.model_fields:
            val = getattr(self, key)
            if any(s in key for s in self._MASK_KEYWORDS):
                safe[key] = "****" if val else ""
            else:
                safe[key] = val
        return f"Settings({safe})"

    @model_validator(mode="after")
    def _validate_target_db_code(self) -> "Settings":
        """target_db_code 와 system_db_overrides 유효성 검증.

        target_db_schema_map 의 키 집합이 "알려진 시스템 코드의 단일 진실원"
        역할을 하며, known_connectors 는 거기에 외부망 테스트 전용 TEST 를
        더한 파생값이다. 신규 시스템 추가 시 target_db_schema_map 한 곳만
        갱신하면 validator 도 자동 반영된다.
        """
        known_systems = set(self.target_db_schema_map.keys())
        known_connectors = known_systems | {"TEST"}

        if self.target_db_code:
            normalized = self.target_db_code.strip().upper()
            if normalized not in known_systems:
                raise ValueError(
                    f"target_db_code='{self.target_db_code}' 는 "
                    f"target_db_schema_map 키 집합 "
                    f"{sorted(known_systems)} 에 없습니다."
                )
            self.target_db_code = normalized

        for sys_code, conn_name in self.system_db_overrides.items():
            if sys_code not in known_systems:
                raise ValueError(
                    f"system_db_overrides 키 '{sys_code}' 는 "
                    f"target_db_schema_map {sorted(known_systems)} "
                    f"에 없습니다."
                )
            if conn_name not in known_connectors:
                raise ValueError(
                    f"system_db_overrides['{sys_code}']="
                    f"'{conn_name}' 는 알려진 커넥터 "
                    f"{sorted(known_connectors)} 에 없습니다."
                )
        return self

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
