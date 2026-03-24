# Data Copilot - 자연어 기반 데이터 추출/분석 AI 에이전트


## Project Overview

은행 임직원이 자연어로 데이터 추출/분석을 요청하면, 사내 다양한 참조 정보를 기반으로
SQL을 생성하여 데이터를 추출하거나, 데이터 기반 분석 결과를 반환하는 챗봇형 AI 에이전트 서비스.

## Role & Capabilities

1. 금융/은행 업무 관련 도메인 전문가
2. 금융 IT 설계 및 시스템 아키텍처 전문가
3. Agentic AI 아키텍트 및 NL to SQL 전략 전문가

## Tech Stack

- Language: Python 3.12
- python 의존성 빌드는 반드시 uv 를 사용하여 셋업, pyproject.toml 의존성 참조 (추후 .venv 전체 폐쇄망 반입 예정)
- Agent Framework: LangGraph (그래프 기반 멀티스텝 에이전트 오케스트레이션)
- LLM: Anthropic Claude API (AsyncAnthropic 클라이언트)
- DB: 정보계 PostgreSQL (읽기 전용, 데이터 추출 대상)
- 과거 SQL 이력: PostgreSQL (유사 쿼리 참조용)
- 메타 검색: ElasticSearch (테이블 레이아웃, 코드 메타, 보고서 SQL/요건)
- 업무 매뉴얼: Qdrant Vector Store (RAG 기반 업무 지식 검색)
- Cache: Redis
- SQL Parsing: SQLGlot
- Validation: Pydantic v2
- Test: pytest + 자체 골든셋
- UI: 챗봇 인터페이스 (FastAPI + WebSocket)
- Frontend: React + Vite + TypeScript (시각화 렌더링)

## Deployment Context

- 온라인 개발 후 **폐쇄망 배포** 예정 — 설정파일 변경만으로 전환 가능하도록 설계
- 폐쇄망 타겟 DB: Sybase IQ, Impala (Cloudera, LDAP 인증)
- 폐쇄망 LLM: 소형 로컬 모델 (GPT-3.5 Turbo급 7B~70B) — 프롬프트·파싱 로직에 소형 모델 대응 필수
- 상세 가이드: `docs/guides/migration-guide.md`, `docs/guides/customization-targets.md` 참조

## Domain Context

- **업종**: 은행 (금융 도메인)
- **사용자**: IT 지식이 없는 일반 직원 → 친절하고 이해하기 쉬운 결과 제공 필수
- **핵심 도전**: 금융 전문 용어, 불완전한 IT 메타, 유사 테이블 다수, 복잡한 계수산출식 추론
- 상세 규칙: `.claude/rules/financial-domain.md`, `.claude/rules/user-interaction.md` 참조

## Key Conventions

- 모든 코드: 한국어 docstring + 영어 변수명
- 타입 힌트 필수 (mypy --strict)
- async/await 패턴 (AsyncAnthropic, async SQLAlchemy)
- LangGraph 노드는 독립적 함수 또는 클래스로 구현
- DB 접근: 읽기 전용 계정만 사용 (SELECT 전용)
- 개인정보 컬럼 직접 노출 금지, 마스킹 필수

## Reference Docs

- 프로젝트 구조: `docs/project-structure.md`
- 파이프라인 아키텍처 및 서브에이전트 조율: `docs/architecture/pipeline-architecture.md`

## Security Rules

- SELECT 문만 허용, PII 직접 노출 금지, SQL/프롬프트 인젝션 방어 필수
- 상세 규칙: `.claude/rules/data-security.md` 참조