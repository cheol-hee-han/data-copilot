"""parse_analysis_markdown 단위 테스트.

테스트 대상:
    [src/services/data_analyzer.py :: parse_analysis_markdown]
    - 4개 섹션(## 핵심 요약 / ## 데이터 현황 / ## 분석 인사이트 / ## 후속 조치) 파싱
    - 불릿 항목 추출 및 빈 항목 제거
    - 섹션 누락 시 ValueError
"""

from __future__ import annotations

import pytest

from src.services.data_analyzer import parse_analysis_markdown


def test_parse_all_four_sections() -> None:
    text = """## 핵심 요약
3개 지점의 연체 현황입니다.

## 데이터 현황
- 송파지점 11억
- 강남지점 5.8억

## 분석 인사이트
- 송파 58% 쏠림
- 강남 건당 4,833만원

## 후속 조치
- 즉시 점검
- 주간 모니터링
"""
    r = parse_analysis_markdown(text)
    assert r.summary.startswith("3개 지점")
    assert r.initial_reading == ["송파지점 11억", "강남지점 5.8억"]
    assert r.insights == ["송파 58% 쏠림", "강남 건당 4,833만원"]
    assert r.action_items == ["즉시 점검", "주간 모니터링"]
    assert r.statistics == {}
    assert r.reasoning_summary == ""


def test_empty_insights_section_yields_empty_list() -> None:
    text = """## 핵심 요약
데이터 1건뿐이어서 추이 분석 불가.

## 데이터 현황
- 정기예금A 가입건수 5건

## 분석 인사이트
-

## 후속 조치
- 조건 추가 재조회
"""
    r = parse_analysis_markdown(text)
    assert r.insights == []
    assert r.action_items == ["조건 추가 재조회"]


def test_numbered_bullets_also_parsed() -> None:
    text = """## 핵심 요약
요약
## 데이터 현황
1. 첫 항목
2. 둘째 항목
## 분석 인사이트
- a
## 후속 조치
- b
"""
    r = parse_analysis_markdown(text)
    assert r.initial_reading == ["첫 항목", "둘째 항목"]


def test_missing_summary_section_raises() -> None:
    text = "## 데이터 현황\n- a\n"
    with pytest.raises(ValueError):
        parse_analysis_markdown(text)
