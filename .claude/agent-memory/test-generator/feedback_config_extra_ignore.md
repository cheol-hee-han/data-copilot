---
name: Config Extra Ignore Fix
description: .env에 stale ES 필드가 있어 Settings 로딩 실패 — extra="ignore" 추가로 해결
type: feedback
---

Settings 클래스의 `model_config`에 `"extra": "ignore"`가 없으면, .env에 정의된 필드 중 Settings에 없는 것이 있을 때 ValidationError로 전체 테스트 컬렉션이 실패한다.

**Why:** ES가 제거된 후 .env에 es_* 필드가 남아 있고 Settings에서 해당 필드가 주석처리되어 extra_forbidden이 발생한다.

**How to apply:** `src/config.py`의 `model_config`에 `"extra": "ignore"`를 포함시켜 두어야 한다. 새 테스트 작성 시 Settings import 오류가 나면 이 설정을 먼저 확인한다.
