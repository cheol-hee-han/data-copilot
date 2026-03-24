"""LLM 응답 스냅샷 캐시.

LLM 호출의 비결정성으로 인한 CI flaky test 를 방지하기 위해,
첫 실행의 LLM 응답을 JSON 파일로 저장하고 이후 실행에서 재사용한다.

사용 방법:
    # conftest 또는 테스트 파일에서
    from tests.fixtures.llm_snapshot import snapshot_cache

    @pytest.fixture
    def llm_cache():
        return snapshot_cache("test_classify_intent")

    async def test_something(llm_cache):
        cached = llm_cache.get("data_extraction_query")
        if cached:
            result = cached  # 캐시된 응답 사용
        else:
            result = await real_llm_call(...)
            llm_cache.save("data_extraction_query", result)

캐시 무효화:
    - tests/fixtures/snapshots/ 디렉토리의 JSON 파일을 삭제하면 재생성
    - 환경 변수 LLM_SNAPSHOT_UPDATE=1 로 실행하면 강제 갱신
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


class SnapshotCache:
    """LLM 응답 스냅샷 캐시 관리자."""

    def __init__(self, module_name: str) -> None:
        self._module = module_name
        self._dir = SNAPSHOT_DIR / module_name
        self._dir.mkdir(parents=True, exist_ok=True)
        self._force_update = os.getenv("LLM_SNAPSHOT_UPDATE") == "1"

    def get(self, key: str) -> dict | None:
        """캐시된 스냅샷을 로드한다. 없으면 None."""
        if self._force_update:
            return None

        path = self._dir / f"{key}.json"
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("response")
        except (json.JSONDecodeError, KeyError):
            return None

    def save(self, key: str, response: Any) -> None:
        """LLM 응답을 스냅샷으로 저장한다."""
        path = self._dir / f"{key}.json"
        snapshot = {
            "module": self._module,
            "key": key,
            "saved_at": datetime.now().isoformat(),
            "response": response,
        }
        path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_or_call(self, key: str, call_fn: Any) -> Any:
        """캐시가 있으면 반환, 없으면 call_fn 실행 후 저장.

        동기 함수용. 비동기는 get_or_call_async 사용.
        """
        cached = self.get(key)
        if cached is not None:
            return cached
        result = call_fn()
        self.save(key, result)
        return result

    async def get_or_call_async(self, key: str, call_fn: Any) -> Any:
        """캐시가 있으면 반환, 없으면 async call_fn 실행 후 저장."""
        cached = self.get(key)
        if cached is not None:
            return cached
        result = await call_fn()
        self.save(key, result)
        return result


def snapshot_cache(module_name: str) -> SnapshotCache:
    """테스트 모듈용 스냅샷 캐시를 생성한다."""
    return SnapshotCache(module_name)
