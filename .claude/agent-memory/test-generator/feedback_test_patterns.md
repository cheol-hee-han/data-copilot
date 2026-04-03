---
name: Test Patterns
description: 이 프로젝트 단위 테스트 작성 규칙 — conftest 패턴, asyncio, 헬퍼 구조
type: feedback
---

Mock 사용 금지 — 실제 환경에서 테스트해야 한다.

**Why:** Mock 테스트 통과 후 실제 환경에서 실패하는 사례 방지.

**How to apply:** LLM 호출이 필요한 경우에도 실제 API를 호출하거나, LLM 없이 검증 가능한
로직 경계(signal 생성, 필터링, 라우팅)만 단위 테스트로 분리한다.

## 파일 구조 패턴
- `sys.path.insert(0, str(Path(__file__).resolve().parents[3]))` — 프로젝트 루트를 sys.path에 추가
- `from tests.conftest import get_test_logger, log_test_case` — 공통 로깅 유틸 사용
- 클래스 기반 그룹핑: `class TestXxx:` — 기능 단위로 묶음
- 헬퍼 팩토리: `_make_signal()`, `_make_state()` — 테스트마다 반복되는 객체 생성을 모듈 상단에 분리

## asyncio 패턴
- `pyproject.toml`에 `asyncio_mode = "auto"` 설정 — `@pytest.mark.asyncio` 불필요하지만 명시해도 무방
- LangGraph interrupt를 포함하는 노드는 테스트에서 직접 호출하지 않음 (interrupt가 발생하므로)
  — INFER only 경로만 asyncio 테스트로 검증

## PipelineState 생성
- Pydantic BaseModel — `PipelineState(field=value, ...)` 직접 생성
- `model_copy(update={...})` 패턴으로 변형 상태 생성 가능

## log_test_case
- 모든 assertion 전에 `log_test_case(logger, 이름, 입력, 기대, 실제, passed)` 호출
- 입력/기대/실제는 500자 자동 트런케이트
