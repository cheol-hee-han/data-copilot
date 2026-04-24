"""_extract_svg_block / _strip_code_fence 단위 테스트.

테스트 대상:
    [src/services/data_analyzer.py :: _extract_svg_block, _strip_code_fence]
    - 순수 <svg>...</svg> 블록 추출
    - ```svg / ```xml / ``` 펜스 제거 후 추출
    - SVG 미포함 → ValueError
"""

from __future__ import annotations

import pytest

from src.services.data_analyzer import (
    _extract_svg_block,
    _strip_code_fence,
)


def test_extract_plain_svg() -> None:
    text = '<svg xmlns="x"><rect/></svg>'
    assert _extract_svg_block(text) == text


def test_extract_with_svg_fence() -> None:
    text = "```svg\n<svg><g/></svg>\n```"
    assert _extract_svg_block(text) == "<svg><g/></svg>"


def test_extract_with_xml_fence() -> None:
    text = "설명\n```xml\n<svg><g/></svg>\n```"
    assert _extract_svg_block(text) == "<svg><g/></svg>"


def test_extract_with_plain_fence() -> None:
    text = "```\n<svg><g/></svg>\n```"
    assert _extract_svg_block(text) == "<svg><g/></svg>"


def test_extract_raises_when_no_svg() -> None:
    with pytest.raises(ValueError):
        _extract_svg_block("그냥 설명 텍스트")


def test_strip_code_fence_no_fence() -> None:
    assert _strip_code_fence("hello") == "hello"
