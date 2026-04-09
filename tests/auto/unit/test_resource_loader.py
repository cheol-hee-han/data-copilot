"""리소스 로더 단위 테스트.

테스트 대상:
    - exists: 파일 존재 여부 확인
    - load_text / load_text_required: 텍스트 파일 로드
    - load_sql_template: SQL 템플릿 로드 (주석 제거)
    - load_json: JSON 파일 로드 (없으면 default 반환)
    - load_yaml: YAML 파일 로드 (없으면 default 반환)
    - load_csv: CSV 파일 로드 (dict 리스트 반환)

전략:
    - tmp_path fixture로 임시 파일을 생성하고
      RESOURCES_DIR 을 monkeypatch로 교체하여
      실제 resources/ 디렉토리에 의존하지 않는다.

실행 스크립트:
    pytest tests/auto/unit/test_resource_loader.py -v

참고:
    - yaml 패키지가 없어도 load_yaml 기본값 반환 동작 검증 가능
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.conftest import get_test_logger, log_test_case

logger = get_test_logger("test_resource_loader")


@pytest.fixture
def resource_dir(tmp_path, monkeypatch):
    """RESOURCES_DIR를 tmp_path로 교체하는 픽스처."""
    import src.utils.resource_loader as rl
    monkeypatch.setattr(rl, "RESOURCES_DIR", tmp_path)
    return tmp_path


def _write(base: Path, name: str, content: str) -> Path:
    """tmp_path 하위에 파일을 생성하고 경로를 반환한다."""
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ════════════════════════════════════════════════════════════
# exists
# ════════════════════════════════════════════════════════════

class TestExists:
    """exists: 파일 존재 여부 확인."""

    def test_existing_file_returns_true(self, resource_dir):
        """존재하는 파일에 대해 True를 반환한다."""
        from src.utils.resource_loader import exists
        _write(resource_dir, "prompts/test.txt", "hello")
        result = exists("prompts/test.txt")
        passed = result is True
        log_test_case(logger, "exists_true", "prompts/test.txt", True, result, passed)
        assert passed

    def test_missing_file_returns_false(self, resource_dir):
        """존재하지 않는 파일에 대해 False를 반환한다."""
        from src.utils.resource_loader import exists
        result = exists("prompts/nonexistent.txt")
        passed = result is False
        log_test_case(logger, "exists_false", "prompts/nonexistent.txt", False, result, passed)
        assert passed

    def test_directory_returns_false(self, resource_dir):
        """디렉토리 경로는 파일이 아니므로 False를 반환한다."""
        from src.utils.resource_loader import exists
        (resource_dir / "subdir").mkdir()
        result = exists("subdir")
        passed = result is False
        log_test_case(logger, "exists_directory", "subdir/", False, result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# load_text
# ════════════════════════════════════════════════════════════

class TestLoadText:
    """load_text: 텍스트 파일 로드."""

    def test_loads_existing_file(self, resource_dir):
        """존재하는 파일의 내용을 반환한다."""
        from src.utils.resource_loader import load_text
        content = "안녕하세요\n이것은 프롬프트입니다"
        _write(resource_dir, "prompts/hello.txt", content)
        result = load_text("prompts/hello.txt", "기본값")
        passed = result == content
        log_test_case(logger, "load_text_existing", "hello.txt", content[:30], result[:30], passed)
        assert passed

    def test_returns_default_if_missing(self, resource_dir):
        """파일이 없으면 default 값을 반환한다."""
        from src.utils.resource_loader import load_text
        result = load_text("no_such_file.txt", "기본값입니다")
        passed = result == "기본값입니다"
        log_test_case(logger, "load_text_default", "no_such_file.txt", "기본값입니다", result, passed)
        assert passed

    def test_empty_default_on_missing(self, resource_dir):
        """default가 빈 문자열이고 파일이 없으면 빈 문자열을 반환한다."""
        from src.utils.resource_loader import load_text
        result = load_text("missing.txt", "")
        passed = result == ""
        log_test_case(logger, "load_text_empty_default", "missing.txt", "", result, passed)
        assert passed

    def test_utf8_korean_content(self, resource_dir):
        """한글 내용이 포함된 파일을 정확히 읽는다."""
        from src.utils.resource_loader import load_text
        content = "여신 잔액 기준일: {기준일}\n지점코드: {지점}"
        _write(resource_dir, "prompts/korean.txt", content)
        result = load_text("prompts/korean.txt", "")
        passed = result == content
        log_test_case(logger, "load_text_korean", "korean.txt", content[:30], result[:30], passed)
        assert passed


# ════════════════════════════════════════════════════════════
# load_text_required
# ════════════════════════════════════════════════════════════

class TestLoadTextRequired:
    """load_text_required: 필수 파일 로드 (없으면 FileNotFoundError)."""

    def test_loads_existing_file(self, resource_dir):
        """존재하는 파일의 내용을 반환한다."""
        from src.utils.resource_loader import load_text_required
        content = "필수 프롬프트 내용"
        _write(resource_dir, "required/prompt.txt", content)
        result = load_text_required("required/prompt.txt")
        passed = result == content
        log_test_case(logger, "load_text_required_ok", "prompt.txt", content, result, passed)
        assert passed

    def test_raises_on_missing(self, resource_dir):
        """파일이 없으면 FileNotFoundError를 발생시킨다."""
        from src.utils.resource_loader import load_text_required
        raised = False
        try:
            load_text_required("missing_required.txt")
        except FileNotFoundError:
            raised = True
        log_test_case(logger, "load_text_required_missing", "missing_required.txt", "FileNotFoundError", raised, raised)
        assert raised


# ════════════════════════════════════════════════════════════
# load_sql_template
# ════════════════════════════════════════════════════════════

class TestLoadSqlTemplate:
    """load_sql_template: SQL 파일 로드 + 주석 제거."""

    def test_removes_line_comments(self, resource_dir):
        """-- 로 시작하는 주석 라인이 제거된다."""
        from src.utils.resource_loader import load_sql_template
        raw = "-- 이것은 주석\nSELECT * FROM TB_CRM_CUSTOMER\n-- 또 다른 주석\nWHERE CUST_STAT_CD = :status"
        _write(resource_dir, "queries/test.sql", raw)
        result = load_sql_template("queries/test.sql")
        passed = "--" not in result and "SELECT" in result and "WHERE" in result
        log_test_case(logger, "load_sql_template_comments", raw[:50], "주석 없음", result[:60], passed)
        assert passed

    def test_inline_comment_preserved(self, resource_dir):
        """인라인 주석(SELECT 뒤에 붙은 --)은 라인 시작이 아니므로 유지된다."""
        from src.utils.resource_loader import load_sql_template
        raw = "SELECT id -- 고객번호\nFROM TB_CRM_CUSTOMER"
        _write(resource_dir, "queries/inline.sql", raw)
        result = load_sql_template("queries/inline.sql")
        # 인라인 주석 라인은 strip 후 --로 시작하지 않으므로 유지됨
        passed = "SELECT id" in result
        log_test_case(logger, "load_sql_inline_comment", raw, "SELECT id 포함", result, passed)
        assert passed

    def test_strips_leading_trailing_whitespace(self, resource_dir):
        """결과 앞뒤의 공백이 제거된다."""
        from src.utils.resource_loader import load_sql_template
        raw = "\n\nSELECT 1\n\n"
        _write(resource_dir, "queries/spaces.sql", raw)
        result = load_sql_template("queries/spaces.sql")
        passed = result == result.strip()
        log_test_case(logger, "load_sql_stripped", raw, "앞뒤 공백 제거", repr(result), passed)
        assert passed

    def test_raises_on_missing(self, resource_dir):
        """파일이 없으면 FileNotFoundError를 발생시킨다."""
        from src.utils.resource_loader import load_sql_template
        raised = False
        try:
            load_sql_template("queries/no_such.sql")
        except FileNotFoundError:
            raised = True
        log_test_case(logger, "load_sql_template_missing", "no_such.sql", "FileNotFoundError", raised, raised)
        assert raised


# ════════════════════════════════════════════════════════════
# load_json
# ════════════════════════════════════════════════════════════

class TestLoadJson:
    """load_json: JSON 파일 로드."""

    def test_loads_dict(self, resource_dir):
        """JSON 객체를 dict로 반환한다."""
        from src.utils.resource_loader import load_json
        data = {"name": "테스트", "count": 42}
        _write(resource_dir, "data/test.json", json.dumps(data, ensure_ascii=False))
        result = load_json("data/test.json", {})
        passed = result == data
        log_test_case(logger, "load_json_dict", "data/test.json", data, result, passed)
        assert passed

    def test_loads_list(self, resource_dir):
        """JSON 배열을 list로 반환한다."""
        from src.utils.resource_loader import load_json
        data = [{"id": 1}, {"id": 2}]
        _write(resource_dir, "data/list.json", json.dumps(data))
        result = load_json("data/list.json", [])
        passed = result == data
        log_test_case(logger, "load_json_list", "data/list.json", data, result, passed)
        assert passed

    def test_returns_default_on_missing(self, resource_dir):
        """파일이 없으면 default 값을 반환한다."""
        from src.utils.resource_loader import load_json
        result = load_json("data/missing.json", {"default": True})
        passed = result == {"default": True}
        log_test_case(logger, "load_json_missing", "data/missing.json", {"default": True}, result, passed)
        assert passed

    def test_returns_default_on_invalid_json(self, resource_dir):
        """파싱 불가 JSON은 default 값을 반환한다."""
        from src.utils.resource_loader import load_json
        _write(resource_dir, "data/bad.json", "{ invalid json }")
        result = load_json("data/bad.json", "fallback")
        passed = result == "fallback"
        log_test_case(logger, "load_json_invalid", "{ invalid json }", "fallback", result, passed)
        assert passed

    def test_korean_content(self, resource_dir):
        """한글이 포함된 JSON을 정확히 파싱한다."""
        from src.utils.resource_loader import load_json
        data = {"용어": "여신", "정의": "대출금 잔액"}
        _write(resource_dir, "data/korean.json", json.dumps(data, ensure_ascii=False))
        result = load_json("data/korean.json", {})
        passed = result["용어"] == "여신"
        log_test_case(logger, "load_json_korean", "data/korean.json", data, result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# load_yaml
# ════════════════════════════════════════════════════════════

class TestLoadYaml:
    """load_yaml: YAML 파일 로드."""

    def test_loads_yaml_dict(self, resource_dir):
        """YAML 파일을 dict로 반환한다."""
        pytest.importorskip("yaml")
        from src.utils.resource_loader import load_yaml
        content = "name: 테스트\ncount: 42\n"
        _write(resource_dir, "domain/test.yaml", content)
        result = load_yaml("domain/test.yaml", {})
        passed = result.get("name") == "테스트" and result.get("count") == 42
        log_test_case(logger, "load_yaml_dict", "domain/test.yaml", {"name": "테스트", "count": 42}, result, passed)
        assert passed

    def test_returns_default_on_missing(self, resource_dir):
        """파일이 없으면 default 값을 반환한다."""
        from src.utils.resource_loader import load_yaml
        result = load_yaml("domain/missing.yaml", {"default": True})
        passed = result == {"default": True}
        log_test_case(logger, "load_yaml_missing", "missing.yaml", {"default": True}, result, passed)
        assert passed

    def test_nested_yaml(self, resource_dir):
        """중첩 구조의 YAML을 파싱한다."""
        pytest.importorskip("yaml")
        from src.utils.resource_loader import load_yaml
        content = "여신:\n  - 가계대출\n  - 기업대출\n"
        _write(resource_dir, "domain/nested.yaml", content)
        result = load_yaml("domain/nested.yaml", {})
        passed = "여신" in result and isinstance(result["여신"], list)
        log_test_case(logger, "load_yaml_nested", content, "여신 리스트", result, passed)
        assert passed


# ════════════════════════════════════════════════════════════
# load_csv
# ════════════════════════════════════════════════════════════

class TestLoadCsv:
    """load_csv: CSV 파일 로드."""

    def test_loads_csv_as_dict_list(self, resource_dir):
        """CSV를 헤더 기반 dict 리스트로 반환한다."""
        from src.utils.resource_loader import load_csv
        content = "id,name,amount\n1,테스트,10000\n2,샘플,20000\n"
        _write(resource_dir, "data/sample.csv", content)
        result = load_csv("data/sample.csv")
        passed = (
            len(result) == 2
            and result[0]["id"] == "1"
            and result[0]["name"] == "테스트"
            and result[1]["amount"] == "20000"
        )
        log_test_case(logger, "load_csv_basic", "sample.csv", "2행 dict", result, passed)
        assert passed

    def test_returns_default_on_missing(self, resource_dir):
        """파일이 없으면 default 값을 반환한다."""
        from src.utils.resource_loader import load_csv
        result = load_csv("data/no_such.csv", [{"key": "val"}])
        passed = result == [{"key": "val"}]
        log_test_case(logger, "load_csv_missing", "no_such.csv", "[{'key':'val'}]", result, passed)
        assert passed

    def test_empty_default_on_missing(self, resource_dir):
        """default 미지정 시 빈 리스트를 반환한다."""
        from src.utils.resource_loader import load_csv
        result = load_csv("data/absent.csv")
        passed = result == []
        log_test_case(logger, "load_csv_empty_default", "absent.csv", [], result, passed)
        assert passed

    def test_header_only_csv(self, resource_dir):
        """헤더만 있는 CSV는 빈 리스트를 반환한다."""
        from src.utils.resource_loader import load_csv
        _write(resource_dir, "data/header_only.csv", "id,name\n")
        result = load_csv("data/header_only.csv")
        passed = result == []
        log_test_case(logger, "load_csv_header_only", "id,name\\n", [], result, passed)
        assert passed

    def test_unicode_csv(self, resource_dir):
        """한글 헤더와 데이터를 포함한 CSV를 정확히 파싱한다."""
        from src.utils.resource_loader import load_csv
        content = "고객번호,지점명,여신금액\nC001,강남,50000000\n"
        _write(resource_dir, "data/korean.csv", content)
        result = load_csv("data/korean.csv")
        passed = len(result) == 1 and result[0]["고객번호"] == "C001"
        log_test_case(logger, "load_csv_unicode", "korean.csv", "C001 포함", result, passed)
        assert passed
