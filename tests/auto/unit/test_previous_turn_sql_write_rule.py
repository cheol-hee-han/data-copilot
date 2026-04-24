"""previous_turn_sql write 규칙 검증 (Phase 3 R3, §14.9.4).

`ReasoningState.previous_turn_sql` / `previous_turn_sql_explanation` 은
CONTINUE 턴 hydration 전용 read-only 채널이다.
`continue_orchestrator` 외 어떤 노드도 이 필드에 write 해서는 안 된다.

접근: Pydantic `frozen=True` 는 인스턴스 전체를 불변으로 만들어 부작용이 크므로
(기존 수십 개 노드가 `reason.*` 필드를 자유롭게 갱신) AST/정적 스캔 테스트 1 개로
write 경로 위반을 차단한다(parent doc §14.9.4, R3 결론).

테스트 대상 소스:
    src/**/*.py
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_FIELDS = ("previous_turn_sql", "previous_turn_sql_explanation")
_ALLOWED_WRITER = Path("src/agents/nodes/interpret/continue_orchestrator.py")
_SRC_ROOT = Path(__file__).resolve().parents[3] / "src"


def _is_target_attribute(node: ast.AST) -> bool:
    """`reason.previous_turn_sql(_explanation)` 형태의 Attribute 인지 판정."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr in _FIELDS
    )


def _collect_write_sites() -> list[tuple[Path, int, str]]:
    """src/ 트리 전체에서 `*.previous_turn_sql(_explanation) = ...` write site 를 수집."""
    sites: list[tuple[Path, int, str]] = []
    for py_file in _SRC_ROOT.rglob("*.py"):
        try:
            source = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if _is_target_attribute(target):
                        sites.append((py_file, node.lineno, target.attr))  # type: ignore[attr-defined]
            elif isinstance(node, ast.AugAssign):
                if _is_target_attribute(node.target):
                    sites.append(
                        (py_file, node.lineno, node.target.attr),  # type: ignore[attr-defined]
                    )
            elif isinstance(node, ast.AnnAssign):
                if _is_target_attribute(node.target):
                    sites.append(
                        (py_file, node.lineno, node.target.attr),  # type: ignore[attr-defined]
                    )
    return sites


class TestPreviousTurnSqlWriteRule:
    """hydration 외 경로에서 previous_turn_sql(_explanation) 에 write 하지 않음을 보장."""

    def test_only_continue_orchestrator_writes(self) -> None:
        sites = _collect_write_sites()
        violations = [
            (path, lineno, attr)
            for path, lineno, attr in sites
            if path.resolve() != (_SRC_ROOT.parent / _ALLOWED_WRITER).resolve()
        ]
        assert not violations, (
            "previous_turn_sql(_explanation) 은 continue_orchestrator 만 write 가능. "
            f"위반 write site: {violations}"
        )

    def test_continue_orchestrator_hydration_writes_exist(self) -> None:
        """hydration 자체는 존재해야 함(회귀로 사라지면 previous_sql 주입이 빈 값 고정)."""
        sites = _collect_write_sites()
        allowed_writes = [
            attr
            for path, _, attr in sites
            if path.resolve() == (_SRC_ROOT.parent / _ALLOWED_WRITER).resolve()
        ]
        assert "previous_turn_sql" in allowed_writes
        assert "previous_turn_sql_explanation" in allowed_writes


@pytest.mark.parametrize("field", _FIELDS)
def test_field_default_is_empty_string(field: str) -> None:
    """디폴트값 `""` 은 NEW 턴에서 `{previous_sql}` 이 `(없음)` 으로 치환되는 전제."""
    from src.agents.state.state import ReasoningState

    reason = ReasoningState()
    assert getattr(reason, field) == ""
