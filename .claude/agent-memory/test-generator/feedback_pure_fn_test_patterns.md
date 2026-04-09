---
name: Pure Function Test Patterns
description: tool_renderers/insight_builder 같은 순수 포맷 함수 테스트에서 발견된 주의사항
type: feedback
---

ExecutionStep.raw_result는 `dict | list | None` 유니온 타입이다. 문자열을 직접 전달하면 Pydantic ValidationError가 발생한다. "잘못된 타입"을 테스트하려면 list(dict가 아닌 타입)를 사용해야 한다.

**Why:** `raw_result: dict[str, Any] | list | None` — str은 허용되지 않음.

**How to apply:** invalid type 테스트에서는 `["not", "a", "dict"]` 같은 list를 사용한다.

## 빈 컨테이너 falsy 동작

`_render_date_distribution`처럼 `if not raw:` 패턴을 쓰는 렌더러는 `{}` (빈 dict)도 falsy로 처리한다. `raw_result={}` 전달 시 "결과 없음" 메시지가 반환된다. `raw_result=None`은 `serialize_single_step`에서 더 일찍 빈 문자열로 반환된다.

**How to apply:** 빈 컨테이너({},[])와 None을 구분해서 테스트한다. None은 `== ""` 검증, {}는 "결과 없음" 포함 검증.

## Windows 콘솔 인코딩 문제

pytest --tb=short 출력에서 한글이 깨져 보이지만, 실제 파이썬 문자열 내부에는 올바른 Unicode가 저장된다. `"→" in result` 같은 단언은 실제로 작동한다. 콘솔 출력 깨짐만으로 테스트 실패를 판단하지 말 것.

**How to apply:** 한글/특수문자 단언 실패 시, 실제 문자열을 `repr()` 로 출력해 확인한다.

## null_rate 조건부 렌더링

`get_column_profile` 렌더러는 `null_rate is not None`일 때만 "NULL율: X.X%" 라인을 출력한다. 생략 여부는 한글 "NULL율" 검색이 아닌 `r"\d+\.\d+%"` regex로 검증한다.

**Why:** 한글 인코딩 이슈로 str 검색 실패 가능성. regex는 인코딩 무관.
