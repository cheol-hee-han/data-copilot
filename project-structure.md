# Project Structure

> **Updated: 2026-03-25** — 3계층 통합 파이프라인 + 에이전틱 Reason 루프 반영

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
│   │   ├── pipeline.py                # 그래프 빌더 — 3계층 노드·엣지·라우팅 함수 조립
│   │   └── runner.py                  # 파이프라인 실행 — run_pipeline(), CLI 엔트리포인트
│   │
│   ├── state/                         # LangGraph 파이프라인 공유 상태
│   │   ├── __init__.py
│   │   └── state.py                   # PipelineState + ReasoningState + Phase·LoopGuard
│   │
│   ├── models/                        # Pydantic 데이터 모델
│   │   ├── __init__.py
│   │   ├── normalization.py           # NormalizedQuery — 8-Slot 정규화 스키마 (Enum + 모델)
│   │   ├── response.py               # PipelineResult, VisualizationData — 파이프라인 응답 모델
│   │   └── user_messages.py           # 사용자 메시지 상수 (에러·안내 문구)
│   │
│   └── nodes/                         # 파이프라인 처리 단계 노드 구현
│       ├── __init__.py
│       │
│       ├── interpret/                 # Interpret 계층 — 질의 해석
│       │   ├── __init__.py
│       │   ├── preprocessor.py        # 입력 전처리 — SQL/프롬프트 인젝션 탐지, 정제
│       │   ├── history_resolver.py    # 대화 이력 해소 — CONTINUE/NEW/UNSURE 판정
│       │   ├── intent_classifier.py   # 의도 분류 — Intent Gate + 세분류 (LLM 기반)
│       │   ├── query_normalizer.py    # 질의 정규화 — 8-Slot 구조화 (2-Phase LLM)
│       │   └── clarifier.py           # 명확화 질문 생성 — 4개 intent 분기 + ambiguities 활용
│       │
│       ├── reason/                    # Reason 계층 — 에이전틱 추론 루프
│       │   ├── __init__.py
│       │   ├── planner.py             # 가설 생성 — 질의 분해, 탐색 계획, Fast-Path 판정
│       │   ├── context_explorer.py    # 컨텍스트 탐색 — 도구 기반 ES/Qdrant/DB 검색
│       │   ├── confidence_evaluator.py # 준비도 판정 — evaluate_readiness() SSOT 호출
│       │   ├── sql_generator.py       # SQL 생성 — dialect 라우팅 + LLM SQL 생성
│       │   ├── sql_validator.py       # SQL 검증 — 3-레이어 (안전성·구조·실행) 검증
│       │   ├── recovery_planner.py    # 복구 계획 — 실패 가설 교체, DeadEnd 기록
│       │   ├── result_finalizer.py    # 결과 조립 — 최종 상태 결정 (성공/실패/명확화)
│       │   └── tools.py               # 도구 정의 — 탐색 도구 인터페이스
│       │
│       ├── present/                   # Present 계층 — 결과 생성 및 표현
│       │   ├── __init__.py
│       │   ├── sql_executor.py        # SQL 실행 — 정보계 DB 읽기 전용 실행
│       │   ├── analyzer.py            # 데이터 분석 — LLM 기반 인사이트·시각화 생성
│       │   └── formatter.py           # 결과 포맷팅 — 사용자 친화적 보고서 형태로 변환
│       │
│       └── prompts/                   # 시스템 프롬프트 로더 (공용)
│           ├── __init__.py
│           └── system_prompts.py      # resources/prompts/ 3계층 로드 및 버전 관리 (v2.0)
│
├── services/                          # 서비스 계층 — 검색·분석·도메인 지식
│   ├── __init__.py
│   ├── intent_resolver.py             # 의도 분류 서비스 — Intent Gate + 레거시 폴백
│   ├── query_normalizer.py            # 정규화 서비스 — 8-Slot LLM 파이프라인
│   ├── history_resolver.py            # 이력 해소 서비스 — 룰 게이트 + LLM 판정
│   ├── data_analyzer.py               # 데이터 분석 서비스 — 통계·시각화 판정·SVG 생성
│   ├── response_formatter.py          # 결과 포맷팅 서비스 — 보고서 변환
│   ├── confidence_scorer.py           # 확신도 계산 — evaluate_readiness() SSOT
│   ├── sql_safety_checker.py          # SQL 안전성 검증 — 5단계 방어 파이프라인
│   ├── sql_hint_extractor.py          # SQL 힌트 추출 — sqlglot 기반 테이블·컬럼 추출
│   ├── search_query_builder.py        # 검색 쿼리 빌더 — 소스별 최적화 쿼리 생성
│   ├── search_query_embedder.py       # 검색 쿼리 임베더 — BGE-M3 Dense+Sparse 벡터 변환
│   ├── reranker.py                    # 재순위기 — BGE-Reranker ONNX/INT8 CPU 최적화
│   ├── similar_table_resolver.py      # 유사 테이블 해결기 — 규칙 기반 구분·검증·추천
│   ├── input_sanitizer.py             # 입력 정제 — 전처리 서비스
│   │
│   ├── domain/                        # 정적 도메인 참조 데이터
│   │   ├── __init__.py
│   │   ├── domain_dictionary.py       # 금융 용어 사전 — resources/domain/ 로드
│   │   ├── domain_synonyms.py         # 정규화 동의어·약어 사전 — 질의 정규화용
│   │   └── similar_tables.py          # 유사 테이블 그룹 정의 — YAML 오버라이드
│   │
│   ├── session/                       # 세션 관리
│   │   ├── __init__.py
│   │   ├── store.py                   # SessionStore 인터페이스
│   │   ├── memory_store.py            # 인메모리 구현 (개발용)
│   │   └── redis_store.py             # Redis 구현 (운영용, TTL 30분)
│   │
│   └── visualization/                 # 시각화
│       ├── __init__.py
│       └── chart_generator.py         # 템플릿 기반 SVG 차트 생성기 (폴백)
│
├── connectors/                        # 외부 시스템 커넥터
│   ├── __init__.py
│   ├── interfaces.py                  # 추상 인터페이스 — BaseConnector 프로토콜
│   ├── manager.py                     # 커넥터 매니저 — 전체 커넥터 수명주기 관리 (싱글턴)
│   ├── dummy_data.py                  # Dummy 모드 샘플 데이터
│   └── impl/                          # 커넥터 구현체
│       ├── __init__.py
│       ├── elasticsearch_connector.py # ElasticSearch — 테이블·보고서·코드 메타 검색
│       ├── postgres_connector.py      # PostgreSQL — 정보계(읽기전용) + SQL이력 DB
│       ├── qdrant_connector.py        # Qdrant — 매뉴얼(Dense) + SQL이력(하이브리드)
│       ├── mongo_connector.py         # MongoDB — 메타 정보 저장 (선택)
│       ├── hive_connector.py          # Hive — 폐쇄망 (미래)
│       ├── impala_connector.py        # Impala — 폐쇄망 (미래)
│       └── sybase_connector.py        # Sybase IQ — 폐쇄망 (미래)
│
├── models/                            # 공용 데이터 모델
│   ├── __init__.py
│   ├── context.py                     # ContextInfo — 검색 결과 통합 모델
│   ├── enums.py                       # 공용 열거형
│   ├── result.py                      # SQLResult — SQL 실행 결과 모델
│   └── trace.py                       # TraceEntry — 추론 추적 모델
│
├── tools/                             # 독립 실행 도구·개발 유틸
│   ├── __init__.py
│   ├── seed_sql_history.py            # SQL 이력 벡터 시딩
│   ├── langsmith.py                   # LangSmith 연동 (개발 환경)
│   └── langgraph_studio.py            # LangGraph Studio 인터페이스 (개발 전용)
│
└── utils/                             # 공통 유틸리티
    ├── __init__.py
    ├── db_routing.py                  # DB 라우팅 — 멀티 DB 소스 분기
    ├── llm/                           # LLM 클라이언트 및 재시도
    │   ├── __init__.py                # re-export: get_llm_client, llm_call_with_parse_retry
    │   ├── client.py                  # 프로바이더 추상화 — Anthropic/OpenAI 호환 래퍼 (싱글턴)
    │   └── retry.py                   # 응답 파싱 재시도 — 포맷 불일치 시 교정 힌트 재호출
    ├── tracker/                       # 평가 트래커 및 컨텍스트 전파
    │   ├── __init__.py                # re-export: EvaluationTracker, get_current_tracker
    │   ├── evaluation.py              # 평가 트래커 — 노드별 입출력·의사결정 기록
    │   └── context.py                 # 트래커 전파 — contextvars 기반
    ├── security.py                    # 보안 — PII 마스킹, SQL 인젝션 검증, 프롬프트 인젝션 탐지
    ├── logger.py                      # 구조화 로깅 — structlog 기반
    └── resource_loader.py             # 리소스 로더 — resources/ 하위 YAML/JSON/TXT 로드
```

## 리소스 (`resources/`)

```text
resources/
├── README.md                          # 리소스 디렉토리 가이드
│
├── domain/                            # 금융 도메인 설정
│   ├── README.md
│   ├── domain_dictionary.yaml         # 금융 용어 사전 (자연어 → DB 스키마 매핑)
│   ├── domain_synonyms.yaml           # 동의어·약어 사전 (정규화·검색 확장용)
│   ├── business_categories.yaml       # 카테고리 → ES domain_cd 매핑
│   ├── pii_columns.yaml               # PII 컬럼 정의 (forbidden + masking)
│   ├── chart_config.yaml              # 차트 생성 템플릿 설정
│   ├── similar_tables.yaml            # 유사 테이블 구분 규칙
│   └── stopwords.yaml                 # 검색 불용어 목록
│
├── prompts/                           # LLM 프롬프트 — 3계층 디렉토리 구조
│   ├── README.md
│   ├── interpret/                     # 질의 해석 계층 (10 files)
│   │   ├── intent_classifier_system.txt        # 의도 분류 (주경로)
│   │   ├── intent_classifier_user.txt          # 의도 분류 사용자 템플릿
│   │   ├── intent_classifier_legacy_system.txt # 의도 분류 (레거시 폴백)
│   │   ├── clarifier_system.txt                # 명확화 질문 생성 (4개 intent 분기)
│   │   ├── clarifier_user.txt                  # 명확화 사용자 템플릿 (intent + ambiguities)
│   │   ├── query_normalizer_phase1_system.txt  # 정규화 Phase1
│   │   ├── query_normalizer_phase1_user.txt    # 정규화 Phase1 사용자 템플릿
│   │   ├── query_normalizer_phase2_system.txt  # 정규화 Phase2 교차검증
│   │   ├── query_normalizer_phase2_user.txt    # 정규화 Phase2 사용자 템플릿
│   │   ├── history_resolver_system.txt         # 이력 해소
│   │   └── history_resolver_user.txt           # 이력 해소 사용자 템플릿
│   ├── reason/                        # 추론 계층 (6 files)
│   │   ├── planner_system.txt                  # 가설 생성·탐색 계획
│   │   ├── context_explorer_system.txt         # 도구 결과 해석
│   │   ├── sql_generator_system.txt            # SQL 생성
│   │   ├── sql_generator_fix_section.txt       # SQL 수정 피드백 삽입 조각
│   │   ├── sql_validator_system.txt            # 의미 검증 (Layer 2b)
│   │   └── recovery_planner_system.txt         # 복구 재계획
│   └── present/                       # 표현 계층 (8 files)
│       ├── analyzer_system.txt                 # 데이터 분석
│       ├── analyzer_user.txt                   # 분석 사용자 템플릿
│       ├── analyzer_viz_judgment_system.txt     # 시각화 판정
│       ├── analyzer_viz_judgment_user.txt       # 시각화 판정 사용자 템플릿
│       ├── analyzer_viz_svg_system.txt          # SVG 생성
│       ├── analyzer_viz_svg_user.txt            # SVG 생성 사용자 템플릿
│       ├── formatter_system.txt                # 결과 포맷팅
│       └── formatter_user.txt                  # 포맷팅 사용자 템플릿
│
├── connectors/                        # 커넥터별 설정
│   └── elasticsearch/                 # ES 쿼리 템플릿
│       ├── table_meta_query.json
│       ├── report_sql_query.json
│       └── code_meta_query.json
│
└── evaluation/                        # 평가 데이터
    ├── README.md
    ├── golden_queries.json            # 골든셋 테스트 쿼리
    └── test_queries.json              # 일반 테스트 쿼리
```

## 테스트 (`tests/`)

```text
tests/
├── __init__.py
├── conftest.py                        # 공통 fixture (로거, 스냅샷 캐시, SLA 타이머)
│
├── fixtures/                          # 테스트 데이터 및 유틸리티
│   ├── __init__.py
│   └── llm_snapshot.py                # LLM 응답 스냅샷 캐시 (flaky test 방지)
│
├── test_cases/                        # 테스트 데이터 JSON
│   └── agentic_e2e_test_catalog.json
│
├── reports/                           # 테스트 리포트 출력
│
├── auto/                              # 자동 테스트 — CI에서 실행 (pytest 기본)
│   ├── unit/                          # 단위 테스트 (22 files) — mock 사용, 외부 인프라 불필요
│   │   ├── test_preprocessor.py       # 입력 전처리
│   │   ├── test_classify_intent.py    # 의도 분류
│   │   ├── test_query_normalizer.py   # 질의 정규화
│   │   ├── test_clarify_node.py       # 명확화 질문
│   │   ├── test_sql_validator.py      # SQL 안전성 검증 (서비스 레이어)
│   │   ├── test_sql_validator_aggregate.py  # 집계 쿼리 판별
│   │   ├── test_sql_validator_edge_cases.py # SQL 검증 엣지 케이스
│   │   ├── test_execute_sql.py        # SQL 실행
│   │   ├── test_analyze_data.py       # 데이터 분석
│   │   ├── test_format_response.py    # 결과 포맷팅
│   │   ├── test_chart_generator.py    # SVG 차트 생성
│   │   ├── test_search_query_builder.py # 검색 쿼리 빌더
│   │   ├── test_security.py           # 보안 유틸리티
│   │   ├── test_finance_terms.py      # 도메인 사전
│   │   ├── test_finance_terms_edge_cases.py # 도메인 사전 엣지
│   │   ├── test_table_selector.py     # 유사 테이블 선택
│   │   ├── test_connectors.py         # 커넥터 Dummy 모드
│   │   ├── test_evaluation_tracker.py # 평가 트래커
│   │   ├── test_evaluator.py          # 평가 모듈
│   │   ├── test_langsmith.py          # LangSmith 트레이싱
│   │   ├── test_trace.py              # 추론 추적 로그
│   │   └── test_edge_cases.py         # 전 구간 엣지 보강
│   │
│   └── e2e/                           # E2E 테스트 (5 files) — 다중 노드 연쇄, mock LLM
│       ├── test_pipeline_e2e.py       # 파이프라인 E2E (전처리→정규화→검증)
│       ├── test_node_chain.py         # 노드 연쇄 흐름
│       ├── test_agentic_core.py       # 에이전틱 코어 통합
│       ├── test_agentic_e2e.py        # 에이전틱 시나리오 (7 카테고리)
│       └── test_agentic_flow_trace.py # 에이전틱 흐름 추적
│
└── manual/                            # 수동 테스트 — 실제 인프라/LLM 필요
    ├── unit/                          # 인터랙티브 단위 테스트 (4 files)
    │   ├── test_history_resolve_scenarios.py
    │   ├── test_history_to_intent.py
    │   ├── test_intent_interactive.py
    │   └── test_normalization_interactive.py
    │
    └── e2e/                           # 인프라 E2E 테스트 (7 files)
        ├── test_agentic_real_e2e.py   # Docker + LLM API 전체 흐름
        ├── test_golden_set_context_quality.py # 골든셋 90건 품질
        ├── test_infra_connectivity.py # 인프라 연결 확인
        ├── test_input_to_normalization.py
        ├── test_search_es_schema.py   # ES 검색 품질
        ├── test_search_qdrant_manual.py # Qdrant 매뉴얼 검색 품질
        └── test_search_qdrant_sql_history.py # Qdrant SQL 이력 검색 품질
```

## 스크립트 (`devtools/`)

```text
devtools/
├── docker/                            # Docker 구성
│   ├── docker-compose.dev.yml         # 개발 환경 (ES + PostgreSQL + Qdrant + Redis + MongoDB)
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
    ├── seed_mongodb.py                # MongoDB 메타 시딩
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
├── project-structure.md               # 이 문서 — 프로젝트 구조 정의
├── project-plan.md                    # 프로젝트 계획·마일스톤
├── project-requirements.txt           # 프로젝트 요구사항
│
├── architecture/                      # 시스템·파이프라인 아키텍처 정의
│   ├── architecture.md                # 전체 아키텍처 개요
│   ├── pipeline-architecture.md       # 파이프라인 아키텍처 상세 (v3.1)
│   ├── state-architecture.md          # State 구조 비판적 분석 — 데이터 활용 파이프라인 관점
│   ├── large-model-architecture.md    # 중대형 모델 기반 파이프라인 재설계 제안서
│   └── embedding-search-integration.md # 임베딩 검색 통합 설계
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
│   └── test-data-requirements.md      # 테스트 데이터 요건 정의
│
├── data-generation-rules/             # 테스트 데이터 생성 규칙 (TYPE-1~4 불완전성)
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
│   ├── customization-targets.md       # 폐쇄망 커스터마이징 대상 (20항목)
│   ├── migration-guide.md             # 폐쇄망 이관 가이드
│   ├── datasource-management-guide.md # 데이터소스 관리 가이드
│   ├── env-configuration-guide.md     # 환경 변수(.env) 설정 레퍼런스
│   ├── local-test-guide.md            # 로컬 테스트 환경 구성
│   └── vibe-coding-guide.md           # 바이브 코딩 가이드
│
├── strategy-proposals/                # 개선 전략·제안서
│   ├── answer-accuracy/               # 응답 정확도 전략
│   ├── context-search/                # 컨텍스트 검색 전략
│   ├── nl-query-normaliazion/         # 질의 정규화 전략
│   └── session-management/            # 세션 관리 전략
│
├── research/                          # 기술 리서치 기록
│   ├── 20260323-impyla-kerberos-dependencies.md
│   ├── 20260323-sybase-iq-python-drivers.md
│   └── 20260324-sqlglot-parsing-accuracy.md
│
├── working/                           # 진행 중 작업 문서
│   ├── agentic-loop-integration/      # 에이전틱 루프 통합 설계
│   ├── agentic-test-plan.md
│   ├── architecture-analysis-report.md
│   ├── legacy-linear-pipeline-reference.md
│   └── multi-db-routing-design.md
│
└── todo/                              # 백로그·정리 항목
    ├── indexes.md
    └── 단순정리.md
```

## 루트 설정 파일

| 파일 | 용도 |
| ---- | ---- |
| `pyproject.toml` | Python 프로젝트 메타·의존성·빌드·lint·pytest 설정 |
| `langgraph.json` | LangGraph Studio 진입점 설정 |
| `.env` / `.env.example` | 환경변수 (API 키, DB 접속 정보 등) |
| `.env.langgraph` | LangGraph Studio 전용 환경변수 |
| `uv.lock` | UV 의존성 잠금 파일 |

## 패키지 역할 요약

| 패키지 | 역할 | 주요 패턴 |
| ------ | ---- | --------- |
| `agents/graph/` | 파이프라인 조립·실행 | `create_app()` → `run_pipeline()` |
| `agents/nodes/interpret/` | Interpret 계층 노드 (전처리·분류·정규화·명확화) | `async def xxx_node(state) -> dict` |
| `agents/nodes/reason/` | Reason 계층 에이전틱 노드 (탐색·판정·생성·검증·복구) | 순환 루프 |
| `agents/nodes/present/` | Present 계층 노드 (실행·분석·포맷팅) | `async def xxx_node(state) -> dict` |
| `agents/state/` | 공유 상태 | `PipelineState` + `ReasoningState` 중첩 |
| `agents/models/` | 데이터 모델 | Pydantic BaseModel |
| `services/` | 비즈니스 로직 | 노드에서 위임받아 실행 |
| `services/domain/` | 정적 도메인 참조 데이터 | YAML 오버라이드 |
| `services/session/` | 세션 관리 | Memory / Redis 백엔드 |
| `connectors/impl/` | 외부 시스템 연결 | Dummy/Real 전환 |
| `utils/llm/` | LLM 프로바이더 추상화 | `get_llm_client()` 싱글턴 |
| `utils/tracker/` | 평가 트래커 | contextvars 전파 |
