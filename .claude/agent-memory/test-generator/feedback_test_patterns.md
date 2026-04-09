---
name: Test Patterns
description: 이 프로젝트 단위 테스트 작성 규칙 — conftest 패턴, asyncio, 헬퍼 구조
type: feedback
---

Mock 사용은 서비스 품질(LLM·SQL 정확도) 경로에서 금지 — 실제 환경에서 테스트해야 한다.

**Why:** Mock 테스트 통과 후 실제 환경에서 실패하는 사례 방지.

**How to apply:** LLM 호출이 필요한 경우에도 실제 API를 호출하거나, LLM 없이 검증 가능한
로직 경계(signal 생성, 필터링, 라우팅)만 단위 테스트로 분리한다.
예외: 외부 인프라 클라이언트(Redis, DB 등)의 인터페이스 계약 검증은 AsyncMock 사용 가능.
기존 test_cancel.py 에서도 AsyncMock으로 Redis 클라이언트를 격리하고 있음.
settings 값 주입(truncate limit 등)은 unittest.mock.patch.object(settings, "field", value) 사용.

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

## sys.path 삽입 깊이

- tests/auto/unit/ 에서는 `parents[3]` 을 사용해야 프로젝트 루트에 도달한다
  (tests/auto/unit → tests/auto → tests → project_root)
- parents[2] 는 tests/ 디렉토리이므로 src 임포트가 실패한다

## 라우팅 함수 테스트 패턴

- 라우팅 함수는 PipelineState를 받아 문자열을 반환하는 순수 함수 → LLM 불필요
- `_make_state(**kwargs)`, `_make_reason(**kwargs)`, `_make_signal(...)` 팩토리를 모듈 상단에 정의
- ReasoningState 내부 필드(failure_type, loop_guard, phase)로 분기 조건 제어
- runner.py의 _build_result는 DataCopilotCallbackHandler 의존성 때문에 FakeHandler로 최소 stub 필요

## Enum 직렬화 주의사항

- `str(SomeEnum.VALUE)` 는 `"SomeEnum.VALUE"` 를 반환한다 (Python str Enum도 동일)
- 트레이스 dict에 저장할 때는 반드시 `.value` 속성을 사용: `FinalStatus.FAILURE.value == "failure"`
- 테스트에서 비교할 때도 `.value`로 맞춰야 한다

## resource_loader 테스트 패턴

- `monkeypatch.setattr(rl, "RESOURCES_DIR", tmp_path)` 로 실제 resources/ 분리
- tmp_path 하위에 `path.parent.mkdir(parents=True, exist_ok=True)` 후 파일 생성
- yaml 의존 테스트는 `pytest.importorskip("yaml")` 로 선택적 실행

## regex fallback 테이블 추출 패턴

- `get_real_tables` regex fallback 패턴: `TB_{3자리알파벳}_{7자리알파벳숫자}`
- TB_CRM_CUSTOMER(8자리)는 매칭 안 됨. TB_CRM_CUST001(7자리)는 매칭됨
- 폐쇄망 실제 테이블명 규칙 준수 필요

## extract_json 동작 경계

- `_JSON_PATTERN`은 greedy 매칭 — 첫 번째 `{` 부터 마지막 `}` 까지를 한 번에 잡음
- 앞에 `{1,2,3}` 같은 비-JSON 중괄호가 있으면 전체 파싱 시도 후 실패 → None 반환이 정상
- 코드펜스(```json```) 또는 텍스트 뒤에만 JSON이 있는 경우 추출 가능
