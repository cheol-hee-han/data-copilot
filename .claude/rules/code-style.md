---
paths:
  - "src/**/*.py"
  - "tests/**/*.py"
---

# Python 코드 스타일 규칙

## 코드 수정 전 필수 확인

- **기존 구현 확인 의무**: 코드를 작성하거나 수정하기 전에 반드시 관련 모듈과 같은 계층의 기존 구현을 먼저 확인한다
  - 같은 디렉토리의 다른 파일에서 사용 중인 패턴, 헬퍼 함수, 유틸리티를 파악한다
  - `src/utils/`, `src/services/`에 이미 존재하는 공통 기능을 중복 구현하지 않는다
  - 새 함수를 만들기 전에 기존 함수를 재사용할 수 있는지 확인한다
- **프로젝트 패턴 준수**: 기존 코드에서 확립된 패턴(LLM 호출, 상태 관리, 프롬프트 치환, 로깅 등)을 따른다
- **Enum 사용 의무**: `FailureType`, `Phase`, `ConfidenceStatus`, `IntentType`, `QueryStatus` 등 정의된 Enum이 있으면 raw string 대신 Enum 멤버를 사용한다

## 기본

- Python 3.12, 타입 힌트 필수 (mypy --strict 통과)
- docstring: 한국어, Google 스타일
- 변수명/함수명: 영어 snake_case
- 클래스명: 영어 PascalCase

## 비동기 패턴

- DB·ES·Qdrant·LLM 호출은 모두 async/await 사용
- Anthropic 클라이언트: `AsyncAnthropic` (동기 `Anthropic` 사용 금지)
- SQLAlchemy: `create_async_engine` + `AsyncSession`

## LangGraph 규칙

- 그래프 State: `TypedDict` 또는 Pydantic 모델로 정의
- 노드 함수: `async def node_name(state: State) -> dict` 패턴
- 조건부 엣지: 순수 함수로 분기 로직 분리
- 노드 간 데이터 전달: State 필드를 통해서만 (전역 변수 금지)

## 에러 처리

- 모든 외부 호출(LLM, DB, ES, Qdrant)에 타임아웃 설정
- 재시도: 지수 백오프 (exponential backoff) 적용
- 사용자에게 노출되는 에러 메시지에 내부 정보 포함 금지

## 의존성

- 핵심: langgraph, anthropic, sqlalchemy, asyncpg, elasticsearch, qdrant-client
- 검증: sqlglot, pydantic
- 테스트: pytest, pytest-asyncio
