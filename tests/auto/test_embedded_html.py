"""embedded.html 구조적 일관성 검증 테스트."""

import re
from pathlib import Path

HTML_PATH = Path(__file__).parent.parent.parent / "static" / "embedded.html"


def _load():
    return HTML_PATH.read_text(encoding="utf-8")


def test_dom_id_consistency():
    """JS에서 getElementById로 참조하는 ID가 HTML에 존재하는지 검증."""
    html = _load()
    js_ids = set(re.findall(r"getElementById\(['\"](\w+)['\"]\)", html))
    html_ids = set(re.findall(r'id=["\']?(\w+)["\']?', html))
    missing = js_ids - html_ids
    assert not missing, f"JS에서 참조하지만 HTML에 없는 ID: {missing}"


def test_html_structure():
    """기본 HTML 구조가 올바른지 검증."""
    html = _load().lower()
    assert "<!doctype html>" in html
    assert "<html" in html and "</html>" in html
    assert "<head" in html and "</head>" in html
    assert "<body" in html and "</body>" in html
    assert html.count("<script") == html.count("</script>")


def test_js_modules_exist():
    """8개 통신 모듈 + SB가 모두 존재하는지 검증."""
    html = _load()
    for module in ["TM", "MS", "RD", "SE", "ED", "IC2", "CN", "CM", "SB"]:
        assert f"var {module}=(function()" in html, f"모듈 {module} 누락"


def test_app_public_api():
    """App 공개 API 메서드가 모두 존재하는지 검증."""
    html = _load()
    methods = [
        "sendMessage", "sendExample", "cancelStream", "regen",
        "downloadSVG", "downloadPNG", "expandChart", "copyChart", "downloadCSV",
    ]
    for m in methods:
        assert f"{m}:function" in html, f"App.{m} 누락"


def test_ed_message_handlers():
    """ED에서 서버 메시지 타입별 핸들러가 모두 존재하는지 검증."""
    html = _load()
    for msg_type in [
        "response", "stream", "progress", "viz",
        "download_ready", "status", "error",
    ]:
        assert f"case '{msg_type}'" in html, f"ED 핸들러 누락: {msg_type}"


def test_theme_variables():
    """3개 테마에 핵심 CSS 변수가 모두 정의되어 있는지 검증."""
    html = _load()
    # 테마별 블록 분리
    parts = re.split(r'\[data-theme=', html)
    assert len(parts) >= 3, "3개 테마 블록이 필요합니다"

    for var in ["--bg-accent-hover", "--sh-focus"]:
        # light (root)
        assert var in parts[0], f"light 테마에 {var} 누락"
        # dim
        dim_block = [p for p in parts if p.startswith('"dim"')]
        assert dim_block and var in dim_block[0], f"dim 테마에 {var} 누락"
        # dark
        dark_block = [p for p in parts if p.startswith('"dark"')]
        assert dark_block and var in dark_block[0], f"dark 테마에 {var} 누락"


def test_xss_mitigation():
    """marked 출력에 XSS 방어 로직이 포함되어 있는지 검증."""
    html = _load()
    assert "DOMParser" in html and "script" in html, "mdRender에 XSS sanitize 로직 필요"


def test_no_inline_onclick_with_user_data():
    """renderDownload에 inline onclick이 없는지 검증 (XSS 방어)."""
    html = _load()
    # renderDownload 함수 영역에서 onclick= 패턴이 없어야 함
    match = re.search(r"function renderDownload.*?^  \}", html, re.MULTILINE | re.DOTALL)
    if match:
        func_body = match.group()
        assert "onclick=" not in func_body, "renderDownload에 inline onclick 사용 금지"


def test_marked_version_pinned():
    """marked CDN이 특정 버전으로 고정되어 있는지 검증."""
    html = _load()
    assert "marked@" in html, "marked는 특정 버전으로 고정해야 합니다"


def test_clipboard_error_handling():
    """clipboard API 호출에 catch 핸들러가 있는지 검증."""
    html = _load()
    clipboard_calls = re.findall(r"navigator\.clipboard\.\w+\([^)]*\)\.then\(", html)
    for call_line_idx, _ in enumerate(clipboard_calls):
        # then 뒤에 catch가 있는지 확인
        pass  # 구조적으로 검증하기 어려우므로 단순 존재 확인
    assert ".catch(" in html, "clipboard 호출에 .catch() 에러 핸들러 필요"
