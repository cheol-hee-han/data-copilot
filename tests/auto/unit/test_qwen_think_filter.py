"""QwenThinkFilter 경계 상태머신 단위 테스트.

테스트 대상:
    [src/utils/llm/client.py :: QwenThinkFilter]
    - 태그가 청크 내부에 완전 포함된 경우
    - 태그가 청크 경계에 걸친 경우 (<th / ink>)
    - 여러 번 open/close 반복
    - 미닫힌 태그 잔여는 flush() 에서 방출
    - 태그가 없는 경우 원본 통과

실행:
    pytest tests/auto/unit/test_qwen_think_filter.py -v
"""

from __future__ import annotations

from src.utils.llm.client import QwenThinkFilter, StreamEvent


def _collect(events: list[StreamEvent]) -> list[tuple[str, str]]:
    return [(e.kind, e.text) for e in events]


def test_tag_fully_inside_single_chunk() -> None:
    f = QwenThinkFilter()
    out = f.feed("앞부분<think>내부</think>뒷부분")
    assert _collect(out) == [
        ("text", "앞부분"),
        ("thinking", "내부"),
        ("text", "뒷부분"),
    ]


def test_tag_split_across_chunks() -> None:
    f = QwenThinkFilter()
    out: list[StreamEvent] = []
    for c in ["앞부분<th", "ink>내부</thi", "nk>뒷부분"]:
        out.extend(f.feed(c))
    assert _collect(out) == [
        ("text", "앞부분"),
        ("thinking", "내부"),
        ("text", "뒷부분"),
    ]


def test_multiple_think_blocks() -> None:
    f = QwenThinkFilter()
    out = f.feed("A<think>X</think>B<think>Y</think>C")
    assert _collect(out) == [
        ("text", "A"),
        ("thinking", "X"),
        ("text", "B"),
        ("thinking", "Y"),
        ("text", "C"),
    ]


def test_unclosed_think_tag_flushed() -> None:
    """종료 시점까지 </think> 미등장: flush() 로 thinking 잔여 방출."""
    f = QwenThinkFilter()
    out = f.feed("시작<think>추론중")
    out.extend(f.flush())
    assert _collect(out) == [
        ("text", "시작"),
        ("thinking", "추론중"),
    ]


def test_plain_text_no_tags_passes_through() -> None:
    f = QwenThinkFilter()
    out = f.feed("태그 없는 일반 텍스트")
    out.extend(f.flush())
    assert _collect(out) == [("text", "태그 없는 일반 텍스트")]


def test_partial_tag_suffix_hold_until_next_chunk() -> None:
    """청크 끝이 태그 접두사면 hold, 다음 청크에서 완성되면 전환."""
    f = QwenThinkFilter()
    # '<' 만 들어와서 hold
    assert f.feed("텍스트<") == [StreamEvent("text", "텍스트")]
    # 'think' 추가되어도 아직 부분 일치
    assert f.feed("think") == []
    # '>' 로 완성, 이후 thinking 모드
    out = f.feed(">추론")
    assert _collect(out) == [("thinking", "추론")]


def test_false_alarm_tag_prefix_eventually_flushed() -> None:
    """'<' 로 시작하지만 think 가 아닌 경우 정상 방출."""
    f = QwenThinkFilter()
    out = f.feed("값 <=")
    out.extend(f.flush())
    # 중간 '<' hold 후 '=' 오면서 부분 일치 해제 → 전체 text 방출
    assert "".join(e.text for e in out) == "값 <="
    assert all(e.kind == "text" for e in out)
