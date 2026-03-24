# Project Structure

## 소스 코드 (`src/`)

```text
src/
├── __init__.py
├── config.py                          # Pydantic Settings — 환경변수 기반 전체 설정
├── main.py                            # FastAPI 서버 — 프로세스 진입점 (REST + WebSocket)
│
├── agents/                            # LangGraph 파이프라인 (그래프 + 노드)
│   ├── __init__.py
│   │
│   ├── graph/                         # LangGraph 파이프라인 정의 및 실행
│   │   ├── __init__.py
│   │   ├── pipeline.py                # 그래프 빌더 — 노드·엣지·조건분기 조립, create_app()
│   │   ├── runner.py                  # 파이프라인 실행 — run_pipeline(), CLI 엔트리포인트
│   │   └── instrumented_pipeline.py   # 트래킹 래퍼 — 노드 입출력·의사결정 자동 기록
│   │
│   ├── state/                         # LangGraph 파이프라인 공유 상태
│   │   ├── __init__.py
│   │   └── state.py                   # PipelineState — LangGraph 전체 공유 상태
│   │
│   ├── models/                        # Pydantic 데이터 모델
│   │   ├── __init__.py
│   │   ├── normalization.py           # NormalizedQuery — 8-Slot 정규화 스키마 (Enum + 모델)
│   │   └── response.py               # PipelineResult, VisualizationData — 파이프라인 응답 모델
│   │
│   └── nodes/                         # 파이프라인 처리 단계 노드 구현
│       ├── __init__.py
│       ├── preprocessor.py            # 입력 전처리 — SQL/프롬프트 인젝션 탐지, 정제
│       ├── intent_classifier.py       # 의도 분류 — Intent Gate + 세분류 (LLM 기반)
│       ├── query_normalizer.py        # 질의 정규화 — 8-Slot 구조화 (2-Phase LLM)
│       ├── clarifier.py               # 명확화 질문 생성 — 모호한 요청에 선택지 제시
│       ├── context_collector.py       # 컨텍스트 수집 — ES·Qdrant·이력DB 병렬 호출
│       ├── sql_generator.py           # SQL 생성 — 컨텍스트 기반 LLM SQL 생성
│       ├── sql_validator.py           # SQL 검증 — SQLGlot 파싱 + 보안 규칙 + 테이블 검증
│       ├── sql_executor.py            # SQL 실행 — 정보계 DB 읽기 전용 실행
│       ├── analyzer.py                # 데이터 분석 — LLM 기반 인사이트·시각화 생성
│       ├── formatter.py               # 결과 포맷팅 — 사용자 친화적 보고서 형태로 변환
│       └── prompts/                   # 시스템 프롬프트 로더
│           ├── __init__.py
│           └── system_prompts.py      # resources/prompts/*.txt 로드 및 버전 관리
│
├── services/                          # 서비스 계층 — 검색·컨텍스트 조립·도메인 지식
│   ├── __init__.py
│   ├── search_query_builder.py        # 검색 쿼리 빌더 — 소스별 최적화 쿼리 문자열 생성
│   ├── search_query_embedder.py       # 검색 쿼리 임베더 — BGE-M3 Dense+Sparse 벡터 변환
│   ├── search_context_assembler.py    # 검색 컨텍스트 조립기 — 6소스 병렬 수집·통합·보강
│   ├── reranker.py                    # 재순위기 — BGE-Reranker ONNX/INT8 CPU 최적화
│   ├── similar_table_resolver.py      # 유사 테이블 해결기 — 규칙 기반 구분·검증·추천
│   ├── table_meta_enricher.py         # 테이블 메타 보강기 — LLM 기반 설명 3관점 보강
│   └── domain/                        # 정적 도메인 참조 데이터
│       ├── __init__.py
│       ├── finance_terms.py           # 금융 도메인 용어 사전 — resources/domain/ 로드
│       ├── business_synonyms.py       # 정규화 동의어·약어 사전 — 질의 정규화용
│       └── similar_tables.py          # 유사 테이블 그룹 정의 — 5개 그룹 + YAML 오버라이드
│
├── connectors/                        # 외부 시스템 커넥터
│   ├── __init__.py
│   ├── base.py                        # 추상 인터페이스 — BaseConnector, SearchConnector, DatabaseConnector
│   ├── manager.py                     # 커넥터 매니저 — 전체 커넥터 수명주기 관리 (싱글턴)
│   ├── elasticsearch_connector.py     # ElasticSearch — 테이블 메타·보고서 SQL·코드 메타 검색
│   ├── postgres_connector.py          # PostgreSQL — 정보계 DB(읽기전용) + SQL 이력 DB
│   ├── qdrant_connector.py            # Qdrant — 업무 매뉴얼(Dense) + SQL이력(하이브리드) 검색
│   └── dummy_data.py                  # Dummy 모드 샘플 데이터 — 3개 커넥터 공용
│
├── tools/                             # 독립 실행 도구·개발 유틸
│   ├── __init__.py
│   ├── seed_sql_history.py            # SQL 이력 벡터 시딩 — DB추출→LLM추론→임베딩→Qdrant적재
│   ├── langsmith.py                   # LangSmith 연동 — 개발 환경 트레이싱 설정
│   └── langgraph_studio.py            # LangGraph Studio 인터페이스 (개발 전용)
│
└── utils/                             # 공통 유틸리티
    ├── __init__.py
    ├── llm/                           # LLM 클라이언트 및 재시도
    │   ├── __init__.py                # re-export: get_llm_client, llm_call_with_parse_retry
    │   ├── client.py                  # 프로바이더 추상화 — Anthropic/OpenAI 호환 래퍼 (싱글턴)
    │   └── retry.py                   # 응답 파싱 재시도 — 포맷 불일치 시 교정 힌트 포함 재호출
    ├── tracker/                       # 평가 트래커 및 컨텍스트 전파
    │   ├── __init__.py                # re-export: EvaluationTracker, get_current_tracker
    │   ├── evaluation.py              # 평가 트래커 — 노드별 입출력·의사결정·LLM 호출 기록
    │   └── context.py                 # 트래커 전파 — contextvars 기반 EvaluationTracker 전파
    ├── chart_generator.py             # 템플릿 기반 SVG 차트 생성기 (폴백)
    ├── security.py                    # 보안 — PII 마스킹, SQL 인젝션 검증, 프롬프트 인젝션 탐지
    ├── logger.py                      # 구조화 로깅 — structlog 기반, 쿼리 컨텍스트 바인딩
    └── resource_loader.py               # 리소스 로더 — resources/ 하위 YAML/JSON/TXT 로드
```

## 정적 파일 (`static/`)

```text
static/
└── embedded.html                      # 폴백 챗봇 UI (React 프론트엔드 미사용 시)
```

## 리소스 (`resources/`)

```text
resources/
├── README.md                          # 리소스 디렉토리 가이드
├── domain/                            # 금융 도메인 설정
│   ├── domain_dictionary.yaml         # 금융 용어 사전 (자연어 표현 → DB 스키마 매핑)
│   ├── domain_synonyms.yaml           # 동의어·약어 사전 (정규화·검색 확장용)
│   ├── domain_categories.yaml         # 카테고리 → ES domain_cd 매핑
│   ├── output_templates.yaml          # 문서 유형별 출력 템플릿 스펙
│   ├── example_codes.yaml             # 코드값 → 한글명 매핑 (포맷팅용)
│   ├── similar_tables.yaml            # 유사 테이블 구분 규칙
│   └── stopwords.yaml                  # 검색 불용어 목록
├── prompts/                           # LLM 프롬프트 (노드별 외부 파일)
│   ├── intent_gate.txt                # Intent Gate 프롬프트
│   ├── intent_classification.txt      # 의도 세분류 프롬프트
│   ├── normalization_phase1.txt       # 정규화 Phase1 프롬프트
│   ├── normalization_phase2.txt       # 정규화 Phase2 프롬프트
│   ├── clarification.txt              # 명확화 질문 생성 프롬프트
│   ├── sql_generation.txt             # SQL 생성 프롬프트
│   ├── table_enrichment.txt           # 테이블 설명 보강 프롬프트
│   ├── data_analysis.txt              # 데이터 분석 프롬프트
│   ├── visualization_judgment.txt     # 시각화 필요 여부 판단 프롬프트
│   ├── visualization_svg.txt          # SVG 차트 생성 프롬프트
│   └── result_formatting.txt          # 결과 포맷팅 프롬프트
├── elasticsearch/                     # ES 검색 설정
│   ├── synonyms.txt                   # 검색 동의어
│   └── user_dictionary.txt            # 사용자 정의 사전 (형태소 분석)
├── security/                          # 보안 설정
│   └── pii_columns.yaml              # PII 컬럼 정의 및 마스킹 규칙
├── evaluation/                        # 평가 설정
│   ├── golden_queries.json            # 골든셋 테스트 쿼리
│   └── test_queries.json              # 일반 테스트 쿼리
└── visualization/                     # 시각화 설정
    └── chart_config.yaml              # 차트 템플릿 설정
```

## 평가 (`evaluation/`)

```text
evaluation/
├── evaluator.py                       # 평가 프레임워크 — 골든셋 로드, 단건 평가, 보고서 생성
└── run_evaluation.py                  # 배치 평가 실행기 — 골든셋 전체/필터 실행 + 트레이스 저장
```

## 테스트 (`tests/`)

```text
tests/
├── __init__.py
├── test_infra_connectivity.py           # 인프라 연결 확인 (ES, Qdrant, PostgreSQL, Impala, Sybase)
├── test_golden_set_context_quality.py   # 골든셋 90건 기반 컨텍스트 탐색 품질 평가
│
├── fixtures/                            # 테스트 데이터 및 유틸리티
│   ├── __init__.py
│   ├── llm_snapshot.py                  # LLM 응답 스냅샷 캐시 (flaky test 방지)
│   ├── real_queries.json                # 실 사용 로그 기반 대표 질의 데이터 (20건)
│   └── snapshots/                       # 캐시된 LLM 응답 (자동 생성)
│
├── unit/                                # 단위 테스트 (445 tests)
│   ├── __init__.py
│   ├── conftest.py                      # 공통 fixture (로거, 스냅샷 캐시, SLA 타이머, 로그 로테이션)
│   │
│   │  ── 파이프라인 노드 ──
│   ├── test_preprocessor.py             # 입력 전처리 (12 tests)
│   ├── test_classify_intent.py          # 의도 분류 (15 tests)
│   ├── test_query_normalizer.py         # 질의 정규화 (27 tests)
│   ├── test_context_collection.py       # 컨텍스트 수집 (9 tests)
│   ├── test_sql_generator.py            # SQL 생성 (14 tests)
│   ├── test_sql_validator.py            # SQL 검증 (12 tests)
│   ├── test_sql_validator_aggregate.py  # SQL 검증 — 집계 쿼리 판별 (6 tests)
│   ├── test_sql_validator_edge_cases.py # SQL 검증 — 엣지 케이스 (31 tests)
│   ├── test_execute_sql.py              # SQL 실행 (11 tests)
│   ├── test_analyze_data.py             # 데이터 분석 (13 tests)
│   ├── test_format_response.py          # 결과 포맷팅 (11 tests)
│   ├── test_clarify_node.py             # 명확화 질문 (9 tests)
│   │
│   │  ── 검색 품질 ──
│   ├── test_search_query_builder.py     # 검색 쿼리 빌더 (48 tests)
│   ├── test_search_es_schema.py         # ES 테이블·코드 메타 검색 (8 tests)
│   ├── test_search_qdrant_manual.py     # Qdrant 업무 매뉴얼 검색 (6 tests)
│   ├── test_search_qdrant_sql_history.py # Qdrant SQL 이력 검색 (7 tests)
│   │
│   │  ── 도메인·보조 모듈 ──
│   ├── test_security.py                 # 보안 유틸리티 (23 tests)
│   ├── test_finance_terms.py            # 도메인 사전 (7 tests)
│   ├── test_finance_terms_edge_cases.py # 도메인 사전 엣지 (30 tests)
│   ├── test_table_selector.py           # 유사 테이블 선택 (25 tests)
│   ├── test_table_enricher.py           # 테이블 메타 보강 (21 tests)
│   ├── test_chart_generator.py          # SVG 차트 생성 (24 tests)
│   │
│   │  ── 인프라·추적 ──
│   ├── test_connectors.py               # 커넥터 Dummy 모드 (8 tests)
│   ├── test_evaluation_tracker.py       # 평가 트래커 (15 tests)
│   ├── test_evaluator.py                # 평가 모듈 (8 tests)
│   ├── test_langsmith.py                # LangSmith 트레이싱 (6 tests)
│   ├── test_trace.py                    # 추론 추적 로그 (11 tests)
│   │
│   │  ── 횡단 테스트 ──
│   ├── test_node_chain.py               # 노드 연쇄 흐름 (5 tests)
│   └── test_edge_cases.py               # 전 구간 엣지 보강 (23 tests)
│
└── integration/                         # 통합 테스트
    ├── __init__.py
    └── test_pipeline_e2e.py             # 파이프라인 E2E (26 tests)
```

## 스크립트 (`standalone/`)

```text
standalone/
├── docker/                            # Docker 구성
│   ├── docker-compose.dev.yml         # 개발 환경 (ES + PostgreSQL + Qdrant + Redis)
│   ├── docker-compose.override.yml    # Docker 오버라이드
│   ├── elasticsearch/
│   │   └── Dockerfile                 # 커스텀 ES 이미지 (nori 플러그인 + 사전)
│   └── scripts/
│       └── init_postgres.sql          # PostgreSQL 초기화 DDL
│
└── scripts/                           # 데이터 시딩·관리 스크립트
    ├── seed_all.sh                    # 전체 시딩 실행 (PostgreSQL → ES → Qdrant)
    ├── seed_postgres.py               # PostgreSQL 테스트 데이터 시딩
    ├── seed_elasticsearch.py          # ElasticSearch 메타데이터 시딩
    ├── seed_qdrant.py                 # Qdrant 벡터 데이터 시딩
    ├── qdrant_data_generators.py      # Qdrant 시딩용 데이터 생성 유틸리티
    ├── generate_all_ddl.py            # 스키마 기반 DDL 자동 생성
    ├── enrich_sql_history.py          # SQL 이력 description 보강 (LLM)
    ├── augment_report_sql.py          # 보고서 SQL 증강
    ├── augment_term_dict.py           # 용어 사전 증강
    └── init_postgres.sql              # PostgreSQL 초기화 SQL
```

## 문서 (`docs/`)

```text
docs/
├── architecture/                      # 시스템·파이프라인 아키텍처 정의
│   ├── architecture.md                # 전체 아키텍처 개요
│   ├── embedding-search-integration.md # 임베딩 검색 통합 설계
│   └── pipeline-architecture.md       # 파이프라인 아키텍처 상세
│
├── agent-guides/                      # AI 서브에이전트 참조 지침서
│   ├── benchmark-metrics.md           # 벤치마크 지표 정의
│   ├── code-review-checklist.md       # 코드 리뷰 체크리스트
│   ├── context-assembly.md            # 컨텍스트 조립 가이드
│   ├── design-review-framework.md     # 설계 리뷰 프레임워크
│   ├── documentation-guide.md         # 문서 작성 지침
│   ├── financial-data-model.md        # 금융 데이터 모델 정의
│   ├── golden-set-format.md           # 골든셋 포맷 명세
│   ├── output-format-guide.md         # 출력 포맷 가이드
│   ├── pipeline-stages.md             # 파이프라인 단계별 명세
│   ├── prompt-templates.md            # 프롬프트 템플릿 가이드
│   ├── research-methodology.md        # 기술 리서치 방법론
│   ├── schema-documentation.md        # 스키마 문서화 규칙
│   ├── security-rules.md              # 보안 규칙 가이드
│   ├── test-data-requirements.md      # 테스트 데이터 요건 정의
│   └── test-data-seeding-reference.py # 테스트 데이터 시딩 참조 코드
│
├── data-generation-rules/             # 테스트 데이터 생성 규칙 (TYPE-1~4 불완전성 포함)
│   ├── 01-realistic-meta-imperfections.md
│   ├── 02-confusing-similar-tables.md
│   ├── 03-data-distributions-correlations.md
│   ├── 04-business-state-lifecycle.md
│   └── 05-data-quality-issues.md
│
├── design-reviews/                    # 설계 리뷰·감사 기록
│   ├── 20260318-design-review.md
│   ├── 20260319-architecture-doc-review.md
│   ├── 20260320-query-strategy-review.md
│   ├── 20260321-graph-flow-evaluation.md
│   └── 20260321-security-audit.md
│
├── guides/                            # 개발·운영·배포 가이드
│   ├── customization-targets.md       # 폐쇄망 커스터마이징 대상
│   ├── datasource-management-guide.md # 데이터소스 관리 가이드
│   ├── env-configuration-guide.md     # 환경 변수(.env) 설정 레퍼런스
│   ├── local-test-guide.md            # 로컬 테스트 환경 구성
│   ├── migration-guide.md             # 폐쇄망 이관 가이드
│   └── vibe-coding-guide.md           # 바이브 코딩 가이드
│
├── strategy-proposals/                # 개선 전략·제안서 (주제별 서브디렉토리)
│   ├── answer-accuracy/              # 응답 정확도 전략
│   │   ├── answer-accuracy-strategy.md
│   │   └── output-specification-strategy.md
│   ├── context-search/               # 컨텍스트 검색 전략
│   │   ├── bge-reranker-cpu-optimization.md
│   │   ├── context-evaluation-strategy.md
│   │   ├── embedding-search-strategy.md
│   │   └── search-improvement-strategy.md
│   ├── nl-query-normaliazion/        # 질의 정규화 전략
│   │   ├── nl-query-normaliazion-strategy.md
│   │   └── prototype/                # 프로토타입 코드
│   └── session-management/           # 세션 관리 전략
│       └── session-ttl-notification-strategy.md  # TTL 만료 알림
│
├── unit-test-design.md                # 단위 테스트 설계서 (13개 구간, 445 tests)
├── project-plan.md                    # 프로젝트 계획·마일스톤
├── project-requirements.txt           # 프로젝트 요구사항
└── project-structure.md               # 이 문서 (프로젝트 구조 정의)
```

## 루트 설정 파일

| 파일 | 용도 |
| ---- | ---- |
| `pyproject.toml` | Python 프로젝트 메타·의존성·빌드·lint 설정 |
| `langgraph.json` | LangGraph Studio 진입점 설정 |
| `.env` / `.env.example` | 환경변수 (API 키, DB 접속 정보 등) |
| `.env.langgraph` | LangGraph Studio 전용 환경변수 |
| `uv.lock` | UV 의존성 잠금 파일 |

## 패키지 역할 요약

| 패키지 | 역할 | 주요 패턴 |
| ------ | ---- | --------- |
| `agents/` | LangGraph 파이프라인 (그래프 + 노드) | 하위 graph/, nodes/ 포함 |
| `agents/graph/` | 파이프라인 조립·실행·계측 | `create_app()` → `run_pipeline()` |
| `agents/nodes/` | 처리 단계 (각 노드는 독립 함수) | `async def xxx_node(state) -> dict` |
| `agents/state/` | LangGraph 파이프라인 공유 상태 | PipelineState |
| `agents/models/` | 데이터 모델 (정규화, 응답) | Pydantic BaseModel |
| `services/` | 검색·컨텍스트 조립·도메인 지식 | 쿼리 빌드→임베딩→검색→재순위→조립 |
| `services/domain/` | 정적 도메인 참조 데이터 (사전, 동의어, 유사 테이블) | YAML 오버라이드 |
| `connectors/` | 외부 시스템 연결 (Dummy/Real 전환) | `BaseConnector` 인터페이스 |
| `tools/` | 독립 실행 배치 도구 | CLI argparse, `python -m` |
| `utils/` | 횡단 관심사 (로깅, 보안 등) | 싱글턴 |
| `utils/llm/` | LLM 프로바이더 추상화 + 응답 파싱 재시도 | `get_llm_client()`, `llm_call_with_parse_retry()` |
| `utils/tracker/` | 평가 트래커 + contextvars 전파 | `EvaluationTracker`, `get_current_tracker()` |
