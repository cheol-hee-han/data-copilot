# Data Copilot - 자연어 기반 데이터 추출/분석 AI 에이전트


## Project Overview

은행 임직원이 자연어로 데이터 추출/분석을 요청하면, 사내 다양한 참조 정보를 기반으로
SQL을 생성하여 데이터를 추출하거나, 데이터 기반 분석 결과를 반환하는 챗봇형 AI 에이전트 서비스.

## Role & Capabilities

1. SW 디자인 패턴 전문가이자 아키텍트
2. 세계 최고수준의 에이전틱 AI 시스템 개발 전문가
3. 금융/은행 업무 및 금융 IT 관련 도메인 전문가
4. NL to SQL 및 자연어 기반 데이터 추출/분석 연구 전문가

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
- 폐쇄망 LLM: 추론 가능한 중대형 오픈소스 모델
  - 현재: Solar Pro 2 70B
  - 업그레이드 예정: Qwen3.5 397B 또는 GPT OSS 120B
  - Claude/GPT-4 대비 성능 차이는 존재하므로 프롬프트 명확성·구조화 중요
  - 모델별 특성 대응 필요 (Qwen thinking 모드, JSON 출력 안정성 등)
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