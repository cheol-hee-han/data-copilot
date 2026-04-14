"""tabularRight 스타일 SQL 포매터 — sql-formatter(npm) 재현.

작성자: 한철희 / 최종수정: 2026-04-12

JS sql-formatter의 tabularRight indent 스타일을 Python으로 재현한다.
절 키워드(SELECT/FROM/WHERE 등)를 우측 정렬(9자 패딩)하여
데이터 영역이 10번째 열부터 수직 정렬되도록 출력한다.

알고리즘 개요:
  1. sqlparse로 AST를 생성한다.
  2. Statement/Where/Parenthesis 등 최상위 컨테이너를 순회한다.
  3. Identifier 그룹은 내부를 재포맷하지 않고 .value 원문을 재활용한다.
     (b.BR_NM, COUNT(a.ACN) 등 점(.) 연결 및 함수 인자 공백 문제 방지)
  4. 절 키워드/JOIN/AND/OR는 패딩 + 줄바꿈으로 처리한다.
  5. BETWEEN...AND 패턴의 AND는 논리 연산자로 취급하지 않는다.
  6. CASE/WHEN/THEN/ELSE/END는 BLOCK_LEVEL 들여쓰기로 처리한다.

핵심 함수:
  - format_sql_tabular: 공개 API
  - pad_keyword: 키워드 9자 우측 패딩
  - _classify_keyword: 키워드 분류 (CLAUSE/JOIN/SET_OP/LOGICAL/CASE/OTHER)
  - _render_node: AST 노드 재귀 렌더링 (핵심)
  - _find_between_and: BETWEEN...AND의 AND 인덱스 수집

주의:
  - BETWEEN ... AND 패턴의 AND는 논리 연산자 AND와 구분 필수
  - Identifier 그룹(.value 재활용)으로 b.BR_NM 공백 문제 해결
  - Parenthesis 내 SELECT 존재 여부로 서브쿼리 vs 함수 인자 구분
  - 에러 발생 시 원본 SQL을 그대로 반환 (예외 전파 금지)
"""

from __future__ import annotations

import re
from enum import Enum, auto
from typing import Any

import sqlparse
import sqlparse.sql as S
from sqlparse import tokens as T


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 상수 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# tabularRight 인덴테이션 단위 (패딩 7자 + 공백 1자 = 8자)
_INDENT_UNIT: int = 8

# BEGIN-END 블록 전용 들여쓰기 단위 (6칸)
# top_level은 8칸(절 키워드 정렬), block_level은 6칸(블록 중첩 구분)
_BLOCK_INDENT: int = 6

# 키워드 우측 정렬 폭 (7자 — 실사용 최대 키워드: DECLARE, CONNECT, QUALIFY, NATURAL)
_PAD_WIDTH: int = 7

# 재귀 깊이 제한 (서브쿼리 중첩 방어 — 정상 SQL은 5~10 이내)
_MAX_DEPTH: int = 30

# 절 키워드: 줄바꿈 + TOP_LEVEL 증가 (대문자 정규화 기준)
_CLAUSE_KEYWORDS: frozenset[str] = frozenset({
    # 표준 SQL
    "SELECT",
    "FROM",
    "WHERE",
    "GROUP BY",
    "HAVING",
    "ORDER BY",
    "OFFSET",
    "INSERT INTO",
    "VALUES",
    "UPDATE",
    "SET",
    "DELETE FROM",
    "RETURNING",
    "WITH",
    "LIMIT",
    # Oracle — 계층형 쿼리
    "CONNECT BY",
    "START WITH",
    # Impala — 윈도우 함수 필터
    "QUALIFY",
    # 프로시저 키워드 (Sybase IQ BEGIN-END 블록 내)
    "DECLARE",
    "IF",
})

# JOIN 키워드: 절과 같은 열 정렬 (decreaseTop → 출력 → increaseTop)
_JOIN_KEYWORDS: frozenset[str] = frozenset({
    "JOIN",
    "INNER JOIN",
    "LEFT JOIN",
    "RIGHT JOIN",
    "FULL JOIN",
    "CROSS JOIN",
    "LEFT OUTER JOIN",
    "RIGHT OUTER JOIN",
    "FULL OUTER JOIN",
    "NATURAL JOIN",
    "LEFT INNER JOIN",
})

# SET OPERATION 키워드: 최상위 레벨로 리셋 후 출력
_SET_OP_KEYWORDS: frozenset[str] = frozenset({
    "UNION",
    "UNION ALL",
    "UNION DISTINCT",
    "EXCEPT",
    "EXCEPT ALL",
    "INTERSECT",
    "INTERSECT ALL",
    # Oracle — EXCEPT 대신 MINUS 사용
    "MINUS",
})

# 논리 연산자: JOIN과 동일 처리 (BETWEEN...AND의 AND는 제외)
_LOGICAL_KEYWORDS: frozenset[str] = frozenset({"AND", "OR"})

# CASE 제어 키워드
_CASE_START: str = "CASE"
_CASE_END: str = "END"
_CASE_INNER: frozenset[str] = frozenset({"WHEN", "THEN", "ELSE"})

# ON 키워드 (JOIN 조건, JOIN/AND와 동일하게 9칸 우측 정렬)
_ON_KEYWORD: str = "ON"

# BEGIN-END 블록 키워드 (Sybase IQ 프로시저/스크립트)
_BLOCK_START: str = "BEGIN"
_BLOCK_END: str = "END"

# Identifier 내 공백 정규화용 사전 컴파일
_MULTI_WS_RE: re.Pattern[str] = re.compile(r"\s+")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 키워드 분류 Enum
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class KeywordKind(Enum):
    """SQL 키워드의 포맷팅 역할 분류."""

    CLAUSE = auto()      # SELECT, FROM, WHERE 등 — 줄바꿈 + TOP_LEVEL 증가
    JOIN = auto()        # JOIN 변형 — 절과 같은 열 정렬
    SET_OP = auto()      # UNION, INTERSECT, EXCEPT 등
    LOGICAL = auto()     # AND, OR — BETWEEN...AND의 AND는 제외
    CASE_START = auto()  # CASE
    CASE_INNER = auto()  # WHEN, THEN, ELSE
    CASE_END = auto()    # END
    BLOCK_START = auto() # BEGIN — 블록 시작 (Sybase IQ 등)
    BLOCK_END = auto()   # END — 블록 종료 (CASE END와 구분)
    ON = auto()          # JOIN ... ON
    OTHER = auto()       # 일반 키워드


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 패딩 함수 (공개)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def pad_keyword(token_text: str) -> str:
    """키워드를 7자 우측 정렬(rjust)로 패딩한다.

    sql-formatter tabularRight 방식을 기반으로,
    복합 키워드(GROUP BY, ORDER BY, INNER JOIN 등)는 첫 단어만
    패딩 기준으로 삼고 나머지를 그대로 붙인다.

    Examples:
        pad_keyword("SELECT")      → " SELECT"     (1 + 6 = 7)
        pad_keyword("FROM")        → "   FROM"     (3 + 4 = 7)
        pad_keyword("WHERE")       → "  WHERE"     (2 + 5 = 7)
        pad_keyword("GROUP BY")    → "  GROUP BY"  (첫 단어 패딩)
        pad_keyword("INNER JOIN")  → "  INNER JOIN"
        pad_keyword("LEFT JOIN")   → "   LEFT JOIN"

    Args:
        token_text: 패딩할 키워드 문자열 (대문자 권장).

    Returns:
        7자 우측 정렬된 키워드 문자열.
    """
    tail_parts: list[str] = []
    text = token_text

    # 복합 키워드: 공백이 있으면 첫 단어만 패딩 (GROUP BY, ORDER BY 등 포함)
    if " " in text:
        parts = text.split(" ", 1)
        text = parts[0]
        tail_parts = [parts[1]]

    padded = text.rjust(_PAD_WIDTH)
    if tail_parts:
        return padded + " " + " ".join(tail_parts)
    return padded


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 키워드 분류
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _classify_keyword(normalized: str) -> KeywordKind:
    """정규화된 키워드를 KeywordKind로 분류한다.

    END 키워드는 CASE_END로 반환한다. BEGIN-END 블록의 END는
    _handle_keyword에서 block_origins 스택을 참조하여
    BLOCK_END로 재분류한다.

    Args:
        normalized: 공백 압축 + 대문자 정규화된 키워드.

    Returns:
        KeywordKind 열거값.
    """
    if normalized in _CLAUSE_KEYWORDS:
        return KeywordKind.CLAUSE
    if normalized in _JOIN_KEYWORDS:
        return KeywordKind.JOIN
    if normalized in _SET_OP_KEYWORDS:
        return KeywordKind.SET_OP
    if normalized in _LOGICAL_KEYWORDS:
        return KeywordKind.LOGICAL
    if normalized == _BLOCK_START:
        return KeywordKind.BLOCK_START
    if normalized == _CASE_START:
        return KeywordKind.CASE_START
    if normalized in _CASE_INNER:
        return KeywordKind.CASE_INNER
    if normalized == _CASE_END:
        # CASE_END로 반환하되, BEGIN 블록의 END인 경우
        # _handle_keyword에서 BLOCK_END로 재분류
        return KeywordKind.CASE_END
    if normalized == _ON_KEYWORD:
        return KeywordKind.ON
    return KeywordKind.OTHER


def _normalize(value: str) -> str:
    """토큰 값을 대문자 + 공백 압축 정규화한다."""
    return " ".join(value.upper().split())


def _is_keyword(tok: sqlparse.sql.Token) -> bool:
    """토큰이 키워드 계열인지 확인한다."""
    if tok.ttype is None:
        return False
    return tok.ttype in T.Keyword


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 인덴테이션 스택
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class IndentStack:
    """TOP_LEVEL / BLOCK_LEVEL 인덴테이션 스택.

    sql-formatter tabular 모드의 두 종류 들여쓰기를 관리한다.

    - top_level: 절 키워드(SELECT/FROM/WHERE 등)로 증감.
    - block_level: CASE...END 등 블록 구조로 증감.

    들여쓰기 = (top_level + block_level) × 10 스페이스

    Attributes:
        top_level: 현재 TOP_LEVEL 깊이.
        block_level: 현재 BLOCK_LEVEL 깊이.
    """

    def __init__(self) -> None:
        self.top_level: int = 0
        self.block_level: int = 0

    @property
    def indent_str(self) -> str:
        """현재 인덴트 문자열을 반환한다.

        top_level은 10칸(절 키워드 정렬), block_level은 4칸(블록 중첩 구분).
        """
        return " " * (
            _INDENT_UNIT * max(0, self.top_level)
            + _BLOCK_INDENT * max(0, self.block_level)
        )

    def increase_top(self) -> None:
        """TOP_LEVEL을 1 증가시킨다."""
        self.top_level += 1

    def decrease_top(self) -> None:
        """TOP_LEVEL을 1 감소시킨다 (최소 0)."""
        self.top_level = max(0, self.top_level - 1)

    def increase_block(self) -> None:
        """BLOCK_LEVEL을 1 증가시킨다."""
        self.block_level += 1

    def decrease_block(self) -> None:
        """BLOCK_LEVEL을 1 감소시킨다 (최소 0)."""
        self.block_level = max(0, self.block_level - 1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 서브쿼리 판별
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parenthesis_has_select(
    paren: Any,
    max_depth: int = 5,
) -> bool:
    """Parenthesis 노드가 서브쿼리인지 판별한다.

    괄호 내부 자식을 재귀 탐색하여 SELECT/WITH DML 키워드가 있으면 서브쿼리.
    함수 인자 괄호 (COUNT(...), DATE_TRUNC(...) 등)는 False.
    이중 괄호 ``((SELECT ...))`` 도 감지한다.

    Args:
        paren: sqlparse Parenthesis 노드.
        max_depth: 재귀 탐색 최대 깊이 (기본 5).

    Returns:
        서브쿼리이면 True.
    """
    if max_depth <= 0:
        return False
    for tok in paren.tokens:
        if tok.ttype in (T.Keyword.DML, T.Keyword.CTE):
            if _normalize(tok.value) in ("SELECT", "WITH"):
                return True
        if hasattr(tok, "tokens"):
            if _parenthesis_has_select(tok, max_depth - 1):
                return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BETWEEN...AND 판별
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _find_between_and(
    token_list: list[sqlparse.sql.Token],
) -> frozenset[int]:
    """토큰 목록에서 BETWEEN...AND 패턴의 AND 인덱스를 수집한다.

    BETWEEN ... AND 패턴의 AND는 논리 연산자가 아닌 범위 구분자이므로
    절 정렬 대상에서 제외해야 한다.

    알고리즘:
      - BETWEEN 키워드를 발견하면 대기 상태로 전환
      - 대기 중 AND를 만나면 해당 인덱스를 기록하고 종료
      - 대기 중 절 키워드가 나오면 대기 취소

    Args:
        token_list: 순회할 토큰 목록 (공백 포함).

    Returns:
        BETWEEN의 AND에 해당하는 인덱스의 frozenset.
    """
    between_and_indices: set[int] = set()
    waiting = False

    for i, tok in enumerate(token_list):
        if not _is_keyword(tok):
            continue
        norm = _normalize(tok.value)
        if norm == "BETWEEN":
            waiting = True
            continue
        if waiting:
            if norm == "AND":
                between_and_indices.add(i)
                waiting = False
            elif _classify_keyword(norm) in (
                KeywordKind.CLAUSE,
                KeywordKind.JOIN,
                KeywordKind.SET_OP,
            ):
                waiting = False
    return frozenset(between_and_indices)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 핵심 렌더러 — AST 노드 재귀 처리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 렌더링 컨텍스트를 담는 간단한 구조체 역할의 클래스
class _RenderCtx:
    """렌더링 상태를 관리하는 컨텍스트.

    Attributes:
        parts: 출력 버퍼 (최종 join).
        stack: 인덴테이션 스택.
        uppercase: 키워드 대문자 변환 여부.
        after_space: 다음 토큰 앞에 공백 필요 여부.
        after_comma_newline: 콤마 후 줄바꿈 대기 여부.
        depth: 현재 재귀 깊이 (서브쿼리 중첩 방어용).
    """

    def __init__(self, uppercase: bool) -> None:
        self.parts: list[str] = []
        self.stack: IndentStack = IndentStack()
        self.uppercase: bool = uppercase
        self.after_space: bool = False
        self.after_comma_newline: bool = False
        self.depth: int = 0
        # BEGIN/CASE 블록 진입 순서 추적 — END 키워드가 어디 소속인지 구분
        # "BEGIN" 또는 "CASE" 문자열을 push/pop
        self.block_origins: list[str] = []
        # 현재 절 키워드 추적 (GROUP BY/ORDER BY 수평 나열 판단용)
        self.current_clause: str = ""

    def emit(self, text: str) -> None:
        """출력 버퍼에 텍스트를 추가한다."""
        self.parts.append(text)
        self.after_space = False

    def emit_space(self) -> None:
        """다음 emit 전 공백이 필요하다고 표시한다."""
        self.after_space = True

    def flush_space(self) -> None:
        """대기 중인 공백을 실제로 출력한다."""
        if self.after_space:
            self.parts.append(" ")
            self.after_space = False

    def emit_newline_indent(self) -> None:
        """줄바꿈 + 현재 인덴트를 출력한다."""
        self.parts.append("\n" + self.stack.indent_str)
        self.after_space = False

    @property
    def has_content(self) -> bool:
        """출력 버퍼에 내용이 있는지 확인한다."""
        return bool(self.parts)


def _render_identifier_value(node: Any, uppercase: bool) -> str:
    """Identifier 노드의 값을 키워드 대/소문자를 적용하여 반환한다.

    Identifier 내부는 재포맷하지 않고 원문(.value)을 재활용하되,
    uppercase=True이면 포함된 키워드(AS, DISTINCT 등)만 대문자 변환한다.

    Args:
        node: sqlparse Identifier 노드.
        uppercase: 키워드 대문자 변환 여부.

    Returns:
        정리된 식별자 문자열.
    """
    if not uppercase:
        # 공백 압축만 적용
        return " ".join(node.value.split())

    # 키워드를 대문자로 변환하면서 토큰 재조합
    parts: list[str] = []
    for tok in node.flatten():
        if _is_keyword(tok):
            parts.append(tok.value.upper())
        else:
            parts.append(tok.value)
    result = "".join(parts)
    # 연산자 주변 공백 정규화: '=' / '>' / '<' 등
    result = _MULTI_WS_RE.sub(" ", result).strip()
    return result


def _render_case_node(node: S.Case, ctx: _RenderCtx) -> None:
    """Case 노드를 CASE WHEN...THEN / ELSE / END 단위로 렌더링한다.

    출력 형식::

        CASE WHEN condition THEN result
             WHEN condition THEN result
             ELSE default
        END

    WHEN...THEN은 한 줄로 묶고, 두 번째 WHEN부터 CASE 뒤 WHEN 위치에 수직 정렬.
    ELSE도 동일 위치에 정렬. END는 CASE와 같은 열에 출력.

    Args:
        node: sqlparse Case 노드.
        ctx: 렌더링 컨텍스트.
    """
    # CASE가 위치한 실제 들여쓰기를 사용 (top_level×10 + block_level×4)
    base_indent = ctx.stack.indent_str
    # "CASE " = 5자, WHEN 정렬 위치 = base_indent + 5
    when_indent = base_indent + " " * 5

    first_when = True

    for tok in node.tokens:
        if tok.ttype in (T.Text.Whitespace, T.Newline) or (
            tok.ttype is not None and tok.ttype in T.Text.Whitespace
        ):
            continue

        if _is_keyword(tok):
            norm = _normalize(tok.value)
            kw_text = tok.value.upper() if ctx.uppercase else tok.value

            if norm == "CASE":
                ctx.flush_space()
                ctx.emit(kw_text + " ")
            elif norm == "WHEN":

                if first_when:
                    # CASE 바로 뒤 첫 WHEN — 같은 줄
                    ctx.after_space = False
                    ctx.emit(kw_text + " ")
                    first_when = False
                else:
                    ctx.after_space = False
                    ctx.emit("\n" + when_indent + kw_text + " ")
            elif norm == "THEN":
                ctx.after_space = False
                ctx.emit(" " + kw_text + " ")
            elif norm == "ELSE":

                ctx.after_space = False
                ctx.emit("\n" + when_indent + kw_text + " ")
            elif norm == "END":
                ctx.after_space = False
                ctx.emit("\n" + base_indent + kw_text)
                ctx.emit_space()
            else:
                ctx.flush_space()
                ctx.emit(kw_text)
                ctx.emit_space()

        elif isinstance(tok, S.Comparison) and _comparison_has_subquery(tok):
            _render_comparison_subquery(tok, ctx)

        elif isinstance(tok, (S.Identifier, S.Comparison)):
            ctx.flush_space()
            ctx.emit(_render_identifier_value(tok, ctx.uppercase))
            ctx.emit_space()

        elif tok.is_group:
            ctx.flush_space()
            _render_group(tok, ctx)

        else:
            ctx.flush_space()
            ctx.emit(tok.value)
            ctx.emit_space()


def _render_parenthesis(node: S.Parenthesis, ctx: _RenderCtx) -> None:
    """Parenthesis 노드를 서브쿼리 또는 인라인 괄호로 렌더링한다.

    서브쿼리(SELECT/WITH 포함): 내부를 재귀적으로 포맷팅.
    함수 인자(일반 괄호): 내부를 인라인으로 출력.

    Args:
        node: sqlparse Parenthesis 노드.
        ctx: 렌더링 컨텍스트.
    """
    is_subq = _parenthesis_has_select(node)

    if is_subq:
        # 깊이 제한 초과 시 원문 출력으로 fallback
        if ctx.depth >= _MAX_DEPTH:
            ctx.flush_space()
            ctx.emit(node.value)
            ctx.emit_space()
            return

        # 서브쿼리: 독립 스택으로 내부 포맷.
        # 닫는 ')' 는 서브쿼리 내부의 블록 indent 시작열에 정렬한다.
        ctx.flush_space()
        ctx.emit("(")

        saved_top = ctx.stack.top_level
        saved_block = ctx.stack.block_level
        ctx.stack.top_level = 0
        # 부모 block_level + 1 상속 — 서브쿼리 depth별 들여쓰기
        ctx.stack.block_level = saved_block + 1
        ctx.depth += 1

        # 닫는 ')' 들여쓰기: 부모의 전체 indent 위치에 맞춤
        close_indent = " " * (
            _INDENT_UNIT * max(0, saved_top)
            + _BLOCK_INDENT * max(0, saved_block)
        )

        # 괄호 내부 토큰 렌더링 (첫 '('와 마지막 ')' 제외)
        inner_tokens = [
            t for t in node.tokens
            if not (t.ttype is T.Punctuation and t.value in ("(", ")"))
        ]
        _render_token_list(inner_tokens, ctx)

        # 서브쿼리 닫힘: 블록 indent 시작열에 ')' 출력
        ctx.depth -= 1
        ctx.stack.top_level = saved_top
        ctx.stack.block_level = saved_block
        ctx.emit("\n" + close_indent + ")")
        ctx.emit_space()
    else:
        # 함수 인자 / 일반 괄호: 원문(.value) 그대로 출력.
        # node.value는 이미 '(' ... ')' 형태이므로 래핑 불필요.
        # uppercase=True이면 내부 키워드만 대문자 변환하여 재조합.
        ctx.flush_space()
        if ctx.uppercase:
            parts: list[str] = []
            for tok in node.flatten():
                parts.append(
                    tok.value.upper() if _is_keyword(tok) else tok.value
                )
            ctx.emit("".join(parts))
        else:
            ctx.emit(node.value)
        ctx.emit_space()


def _identifier_needs_recursion(node: S.Identifier) -> bool:
    """Identifier 노드가 재귀 렌더링이 필요한지 확인한다.

    CASE...END 또는 서브쿼리 Parenthesis가 포함된 경우
    원문 재활용 대신 재귀 렌더링해야 한다.
    (CTE: ``base AS (SELECT ...)`` 패턴 등)

    Args:
        node: 검사할 Identifier 노드.

    Returns:
        재귀 렌더링이 필요하면 True.
    """
    for child in node.tokens:
        if isinstance(child, S.Case):
            return True
        if isinstance(child, S.Parenthesis) and _parenthesis_has_select(child):
            return True
    return False


def _has_case_node(tok: sqlparse.sql.Token) -> bool:
    """토큰 또는 자식에 Case 노드가 포함되어 있는지 확인한다."""
    if isinstance(tok, S.Case):
        return True
    if hasattr(tok, "tokens"):
        return any(isinstance(c, S.Case) for c in tok.tokens)
    return False


def _is_simple_list(node: S.IdentifierList) -> bool:
    """IdentifierList의 모든 항목이 단순 컬럼(수식/함수 아님)인지 확인한다.

    단순 컬럼: ``a.col``, ``col``, ``col ASC``, ``col DESC`` 형태.
    함수 호출(COUNT, SUM 등), 연산(+, -, *), CASE...END, 서브쿼리가
    포함되면 False.

    Args:
        node: 검사할 IdentifierList 노드.

    Returns:
        모든 항목이 단순 컬럼이면 True.
    """
    # Case가 직계 자식(alias 없는 CASE WHEN) 또는 Identifier 내부
    if any(_has_case_node(tok) for tok in node.tokens):
        return False
    for tok in node.get_identifiers():
        val = tok.value.upper()
        if "(" in val:
            return False
        if any(op in val for op in ("+", "-", "*", "/", "||")):
            return False
    return True


def _render_inline_list(node: S.IdentifierList, ctx: _RenderCtx) -> None:
    """IdentifierList를 콤마+공백으로 수평 나열한다.

    GROUP BY / ORDER BY에서 단순 컬럼만 있을 때 사용한다.

    Args:
        node: 렌더링할 IdentifierList 노드.
        ctx: 렌더링 컨텍스트.
    """
    items: list[str] = []
    for tok in node.get_identifiers():
        items.append(_render_identifier_value(tok, ctx.uppercase))
    ctx.flush_space()
    ctx.emit(", ".join(items))
    ctx.emit_space()


def _current_line_length(ctx: _RenderCtx) -> int:
    """현재 출력 버퍼의 마지막 줄 길이(column position)를 반환한다."""
    text = "".join(ctx.parts)
    last_nl = text.rfind("\n")
    if last_nl == -1:
        return len(text)
    return len(text) - last_nl - 1


def _comparison_has_subquery(node: S.Comparison) -> bool:
    """Comparison 노드에 서브쿼리 Parenthesis가 직접 포함되어 있는지 확인한다."""
    for child in node.tokens:
        if isinstance(child, S.Parenthesis) and _parenthesis_has_select(child):
            return True
    return False


def _extract_inner_sql(paren: S.Parenthesis) -> str:
    """Parenthesis에서 '(' 와 ')' 를 제외한 내부 SQL 텍스트를 추출한다."""
    parts: list[str] = []
    for tok in paren.tokens:
        if tok.ttype is T.Punctuation and tok.value in ("(", ")"):
            continue
        parts.append(tok.value)
    return "".join(parts).strip()


def _is_simple_subquery(paren: S.Parenthesis) -> bool:
    """서브쿼리가 단순 구조인지 판별한다 (인라인 유지 여부).

    단순 조건 (모두 만족해야 인라인):
      - FROM이 단일 테이블 (JOIN 없음)
      - WHERE 조건 1개 이하 (논리 AND/OR 0개, BETWEEN...AND 제외)
      - GROUP BY / HAVING / ORDER BY / LIMIT / 집합연산 없음
    """
    raw = " " + paren.value.upper() + " "

    # JOIN 포함 → 복잡
    if " JOIN " in raw:
        return False

    # 추가 절 키워드 → 복잡
    for kw in (
        " GROUP BY ", " HAVING ", " ORDER BY ",
        " LIMIT ", " UNION ", " INTERSECT ",
        " EXCEPT ", " MINUS ",
    ):
        if kw in raw:
            return False

    # WHERE 뒤 논리 연산자 개수 (BETWEEN...AND의 AND 제외)
    between_count = raw.count(" BETWEEN ")
    and_count = raw.count(" AND ")
    or_count = raw.count(" OR ")
    logical_count = (and_count - between_count) + or_count
    if logical_count > 0:
        return False

    return True


def _emit_comparison_left(
    tokens: list[sqlparse.sql.Token],
    ctx: _RenderCtx,
) -> None:
    """Comparison 노드의 서브쿼리 Parenthesis 이전 토큰을 인라인 출력한다."""
    for child in tokens:
        if child.ttype in (T.Text.Whitespace, T.Newline) or (
            child.ttype is not None and child.ttype in T.Text.Whitespace
        ):
            continue
        if isinstance(child, (S.Identifier, S.Function)):
            ctx.flush_space()
            ctx.emit(_render_identifier_value(child, ctx.uppercase))
            ctx.emit_space()
        elif child.ttype is not None:
            ctx.flush_space()
            ctx.emit(
                child.value.upper()
                if ctx.uppercase and _is_keyword(child)
                else child.value,
            )
            ctx.emit_space()


def _find_subquery_paren(node: S.Comparison) -> int:
    """Comparison 노드에서 서브쿼리 Parenthesis의 인덱스를 반환한다."""
    for i, child in enumerate(node.tokens):
        if isinstance(child, S.Parenthesis) and _parenthesis_has_select(child):
            return i
    return -1


def _render_comparison_subquery(
    node: S.Comparison,
    ctx: _RenderCtx,
) -> None:
    """비교 연산 서브쿼리를 렌더링한다.

    단순 서브쿼리(단일 테이블, WHERE 조건 1개 이하)는 인라인 유지.
    복잡한 서브쿼리는 column-position 정렬로 전환한다.

    인라인 예시::

        a.col = (SELECT MAX(b.col) FROM t2 WHERE t2.id = a.id)

    column-position 예시::

        a.col = (SELECT MAX(b.col)
                   FROM t2 b
                  WHERE b.id = a.id
                    AND b.status = 'ACTIVE')
    """
    paren_idx = _find_subquery_paren(node)
    paren_node = node.tokens[paren_idx]

    # 단순 서브쿼리 → 인라인 출력
    if _is_simple_subquery(paren_node):
        ctx.flush_space()
        ctx.emit(_render_identifier_value(node, ctx.uppercase))
        ctx.emit_space()
        return

    # left + operator 출력
    _emit_comparison_left(list(node.tokens[:paren_idx]), ctx)

    # '(' 의 column position 계산
    ctx.flush_space()
    open_col = _current_line_length(ctx)

    # 서브쿼리 내부 SQL을 독립 포맷
    inner_sql = _extract_inner_sql(paren_node)
    formatted = format_sql_tabular(inner_sql, uppercase=ctx.uppercase)

    lines = formatted.split("\n")

    # 첫 줄: (SELECT ... — 같은 줄에 출력
    ctx.emit("(" + lines[0].lstrip())

    # 나머지 줄: open_col 만큼 indent 추가 (상대 indent 보존)
    for line in lines[1:]:
        ctx.emit("\n" + " " * open_col + line)

    # 닫는 )
    ctx.emit(")")
    ctx.emit_space()


def _render_group(
    node: sqlparse.sql.Token,
    ctx: _RenderCtx,
) -> None:
    """그룹 노드를 종류에 따라 적절히 렌더링한다.

    처리 우선순위:
    1. Case 노드: WHEN/THEN/ELSE/END 블록 렌더링
    2. Parenthesis 노드: 서브쿼리/인라인 분기
    3. IdentifierList: 재귀 렌더링 (콤마 후 줄바꿈 적용)
    4. Identifier 내 Case/서브쿼리 포함: 재귀 렌더링
    5. Comparison 내 서브쿼리: column-position 정렬 렌더링
    6. Identifier/Comparison/Function: 원문 재활용 (b.COL, func() 등)
    7. Where 및 기타: 재귀 렌더링

    Args:
        node: sqlparse 그룹 노드.
        ctx: 렌더링 컨텍스트.
    """
    if isinstance(node, S.Case):
        _render_case_node(node, ctx)
    elif isinstance(node, S.Parenthesis):
        _render_parenthesis(node, ctx)
    elif isinstance(node, S.IdentifierList):
        # GROUP BY / ORDER BY에서 단순 컬럼만 나열된 경우 수평 출력
        if ctx.current_clause in ("GROUP BY", "ORDER BY") and _is_simple_list(node):
            _render_inline_list(node, ctx)
        else:
            # SELECT 컬럼 목록 등: 재귀 렌더링하여 콤마 후 줄바꿈 적용
            _render_token_list(list(node.tokens), ctx)
    elif isinstance(node, S.Identifier) and _identifier_needs_recursion(node):
        # CASE...END가 Identifier로 감싸진 경우: 재귀 렌더링
        _render_token_list(list(node.tokens), ctx)
    elif isinstance(node, S.Comparison) and _comparison_has_subquery(node):
        # 비교 연산 서브쿼리: column-position 정렬
        _render_comparison_subquery(node, ctx)
    elif isinstance(
        node,
        (S.Identifier, S.Comparison, S.Function),
    ):
        # b.COL, func(args), INSERT t (cols) 등: 원문 재활용
        ctx.flush_space()
        ctx.emit(_render_identifier_value(node, ctx.uppercase))
        ctx.emit_space()
    else:
        # Where, Values, 기타 그룹: 내부 재귀 처리
        _render_token_list(list(node.tokens), ctx)  # type: ignore[attr-defined]


def _flush_comma_newline(ctx: _RenderCtx) -> None:
    """콤마 후 대기 중인 줄바꿈+인덴트를 출력한다.

    콤마 직후 실제 내용이 나오는 시점에 호출하여
    줄바꿈과 현재 인덴트를 삽입한다.

    Args:
        ctx: 렌더링 컨텍스트.
    """
    if ctx.after_comma_newline:
        ctx.emit("\n" + ctx.stack.indent_str)
        ctx.after_comma_newline = False


def _handle_punctuation(
    tok: sqlparse.sql.Token,
    ctx: _RenderCtx,
) -> bool:
    """구두점 토큰(콤마/세미콜론)을 처리한다.

    Args:
        tok: 처리할 구두점 토큰.
        ctx: 렌더링 컨텍스트.

    Returns:
        처리되었으면 True, 해당 없으면 False.
    """
    if tok.ttype is not T.Punctuation:
        return False
    if tok.value == ",":
        ctx.after_space = False
        ctx.emit(",")
        ctx.after_comma_newline = True
        return True
    if tok.value == ";":
        ctx.after_space = False
        ctx.stack.top_level = 0
        # block_level 유지 — BEGIN-END 내부에서 세미콜론 후에도 블록 유지
        ctx.emit(";")
        # 다음 토큰이 절 키워드면 자체 줄바꿈 사용, 아니면 이 플래그로 줄바꿈
        ctx.after_comma_newline = True
        return True
    return False


def _peek_next_keyword(
    token_list: list[sqlparse.sql.Token],
    idx: int,
) -> str:
    """idx 다음에 오는 첫 번째 비공백 키워드 값을 반환한다.

    DML 복합 키워드(INSERT INTO, DELETE FROM) 병합에 사용한다.

    Args:
        token_list: 전체 토큰 목록.
        idx: 현재 토큰 인덱스.

    Returns:
        다음 키워드 문자열(정규화). 없으면 빈 문자열.
    """
    for tok in token_list[idx + 1:]:
        if tok.ttype in (T.Text.Whitespace, T.Newline) or (
            tok.ttype is not None and tok.ttype in T.Text.Whitespace
        ):
            continue
        if _is_keyword(tok):
            return _normalize(tok.value)
        break
    return ""


def _resolve_dml_compound(
    norm: str,
    idx: int,
    token_list: list[sqlparse.sql.Token],
    uppercase: bool,
) -> tuple[str, str, int]:
    """DML 단독 키워드를 복합 CLAUSE 키워드로 병합 시도한다.

    INSERT → INSERT INTO, DELETE → DELETE FROM 처럼
    sqlparse가 DML과 다음 키워드를 분리하는 경우를 처리한다.

    Args:
        norm: 정규화된 현재 키워드 (예: "INSERT").
        idx: 현재 토큰 인덱스.
        token_list: 전체 토큰 목록.
        uppercase: 키워드 대문자 변환 여부.

    Returns:
        (최종 norm, 최종 kw_text, 추가 소비 토큰 수) 튜플.
    """
    # 병합 후보: 다음 키워드와 합쳐 복합 키워드가 되는 경우
    # INSERT INTO, DELETE FROM, START WITH, CONNECT BY 등
    _COMPOUND_HEADS = frozenset({"INSERT", "DELETE", "START", "CONNECT"})

    if norm not in _COMPOUND_HEADS:
        kw_text = norm if uppercase else norm.lower()
        return norm, kw_text, 0

    next_kw = _peek_next_keyword(token_list, idx)
    compound = norm + " " + next_kw
    if compound not in _CLAUSE_KEYWORDS:
        kw_text = norm if uppercase else norm.lower()
        return norm, kw_text, 0

    kw_text = compound if uppercase else compound.lower()
    # 다음 키워드 토큰까지 건너뛸 개수 계산
    extra = 0
    for j in range(idx + 1, len(token_list)):
        extra += 1
        t = token_list[j]
        if _is_keyword(t) and _normalize(t.value) == next_kw:
            break
    return compound, kw_text, extra


def _handle_keyword(
    tok: sqlparse.sql.Token,
    idx: int,
    token_list: list[sqlparse.sql.Token],
    between_and_set: frozenset[int],
    ctx: _RenderCtx,
) -> int:
    """키워드 토큰을 종류에 따라 처리하고 추가 소비 토큰 수를 반환한다.

    DML 복합 키워드(INSERT INTO, DELETE FROM) 병합은
    _resolve_dml_compound에 위임한다.

    Args:
        tok: 처리할 키워드 토큰.
        idx: 토큰의 목록 내 인덱스.
        token_list: 전체 토큰 목록.
        between_and_set: BETWEEN 패턴의 AND 인덱스 집합.
        ctx: 렌더링 컨텍스트.

    Returns:
        추가 소비 토큰 수 (0 이상). 키워드가 아니면 -1.
    """
    if not _is_keyword(tok):
        return -1

    norm = _normalize(tok.value)
    norm, kw_text, extra = _resolve_dml_compound(
        norm, idx, token_list, ctx.uppercase
    )
    kind = _classify_keyword(norm)

    # BETWEEN의 AND는 논리 연산자로 취급하지 않음
    if kind is KeywordKind.LOGICAL and idx in between_and_set:
        kind = KeywordKind.OTHER

    # BEGIN-END / CASE-END 소속 구분
    if kind is KeywordKind.BLOCK_START:
        ctx.block_origins.append("BEGIN")
    elif kind is KeywordKind.CASE_START:
        ctx.block_origins.append("CASE")
    elif kind is KeywordKind.CASE_END:
        # block_origins 스택에서 가장 최근 진입이 BEGIN이면 BLOCK_END로 재분류
        if ctx.block_origins and ctx.block_origins[-1] == "BEGIN":
            kind = KeywordKind.BLOCK_END
            ctx.block_origins.pop()
        elif ctx.block_origins:
            ctx.block_origins.pop()

    # 절/JOIN/SET_OP 키워드는 자체 줄바꿈을 생성하므로 콤마 줄바꿈 리셋.
    # OTHER 키워드(LEVEL, ASC 등)는 콤마 줄바꿈을 유지해야 한다.
    if kind not in (KeywordKind.OTHER, KeywordKind.CASE_START,
                    KeywordKind.CASE_INNER, KeywordKind.CASE_END):
        ctx.after_comma_newline = False
    _dispatch_keyword(kind, kw_text, ctx)
    return extra


def _dispatch_keyword(
    kind: KeywordKind,
    kw_text: str,
    ctx: _RenderCtx,
) -> None:
    """KeywordKind에 따라 적절한 출력 함수를 호출한다.

    Args:
        kind: 키워드 분류.
        kw_text: 출력할 키워드 문자열.
        ctx: 렌더링 컨텍스트.
    """
    if kind is KeywordKind.CLAUSE:
        _emit_clause(ctx, kw_text)
    elif kind in (KeywordKind.JOIN, KeywordKind.LOGICAL):
        _emit_join_like(ctx, kw_text)
    elif kind is KeywordKind.SET_OP:
        _emit_set_op(ctx, kw_text)
    elif kind is KeywordKind.ON:
        _emit_join_like(ctx, kw_text)
    elif kind is KeywordKind.BLOCK_START:
        # BEGIN: 절 키워드처럼 decrease_top → 패딩 출력 → block_level 증가
        ctx.after_space = False
        ctx.after_comma_newline = False
        ctx.stack.decrease_top()
        newline = "\n" if ctx.has_content else ""
        indent = ctx.stack.indent_str
        padded = pad_keyword(kw_text)
        ctx.emit(f"{newline}{indent}{padded}")
        # top_level 리셋 후 block 증가 — 내부 콘텐츠는 블록 기준 indent
        ctx.stack.top_level = 0
        ctx.stack.increase_block()
        # 다음 토큰에서 줄바꿈+인덴트 생성하도록 플래그 설정 (빈줄 방지)
        ctx.after_comma_newline = True
    elif kind is KeywordKind.BLOCK_END:
        # END (BEGIN 소속): block_level 감소 후 정렬
        ctx.stack.decrease_block()
        ctx.after_space = False
        ctx.after_comma_newline = False
        ctx.stack.decrease_top()
        indent = ctx.stack.indent_str
        padded = pad_keyword(kw_text)
        ctx.emit(f"\n{indent}{padded}")
        ctx.stack.top_level = 0
        ctx.emit_space()
    else:
        # CASE/WHEN/THEN/ELSE/END(CASE) 및 기타 키워드 (ASC, DESC, AS 등)
        # CASE 블록은 S.Case 그룹으로 파싱될 때 _render_case_node에서
        # 전용 처리된다. 개별 키워드로 도달하는 경우는 인라인 출력한다.
        _flush_comma_newline(ctx)
        ctx.flush_space()
        ctx.emit(kw_text)
        ctx.emit_space()


def _render_token_list(
    token_list: list[sqlparse.sql.Token],
    ctx: _RenderCtx,
) -> None:
    """토큰 목록을 순회하며 tabularRight 포맷으로 렌더링한다.

    각 토큰 타입별 처리를 전용 핸들러 함수에 위임한다:
    - 공백/개행: 무시 (포매터가 직접 생성)
    - 구두점(콤마/세미콜론): _handle_punctuation
    - 키워드: _handle_keyword → _dispatch_keyword
    - 그룹 노드(Identifier/Case/Parenthesis 등): _render_group
    - 일반 토큰(리터럴/연산자/와일드카드 등): 직접 출력

    Args:
        token_list: 처리할 토큰 목록.
        ctx: 렌더링 컨텍스트.
    """
    between_and_set = _find_between_and(token_list)
    i = 0
    n = len(token_list)

    while i < n:
        tok = token_list[i]

        # 공백/개행 무시
        if tok.ttype in (T.Text.Whitespace, T.Newline) or (
            tok.ttype is not None and tok.ttype in T.Text.Whitespace
        ):
            i += 1
            continue

        # 주석 — 인라인 유지, 콤마 줄바꿈 소비하지 않음
        # sqlparse는 단일행 주석을 Comment 그룹(ttype=None)으로,
        # 블록 주석을 Token(ttype=T.Comment.Multiline)으로 파싱한다.
        is_comment = (
            isinstance(tok, S.Comment)
            or (tok.ttype is not None and tok.ttype in T.Comment)
        )
        if is_comment:
            comment_text = tok.value.rstrip("\r\n")
            if ctx.after_comma_newline:
                # 콤마 뒤 주석: 같은 줄에 출력, 줄바꿈 플래그 유지
                ctx.emit(" " + comment_text)
            else:
                ctx.flush_space()
                ctx.emit(comment_text)
                ctx.emit_space()
            i += 1
            continue

        # 구두점 (콤마, 세미콜론)
        if _handle_punctuation(tok, ctx):
            i += 1
            continue

        # 키워드 — extra > 0이면 복합 키워드로 병합된 토큰 수만큼 추가 건너뜀
        extra = _handle_keyword(tok, i, token_list, between_and_set, ctx)
        if extra >= 0:
            i += 1 + extra
            continue

        # 그룹 노드 (Identifier, Case, Parenthesis, Where 등)
        if tok.is_group:
            _flush_comma_newline(ctx)
            _render_group(tok, ctx)
            i += 1
            continue

        # 일반 토큰 (리터럴, 연산자, 와일드카드 등)
        _flush_comma_newline(ctx)
        ctx.flush_space()
        ctx.emit(tok.value)
        ctx.emit_space()
        i += 1


def _emit_clause(ctx: _RenderCtx, keyword: str) -> None:
    """절 키워드를 줄바꿈 + 패딩과 함께 출력한다.

    이전 절 TOP_LEVEL 해제 → 패딩된 키워드 출력 → 새 절 TOP_LEVEL 증가.

    Args:
        ctx: 렌더링 컨텍스트.
        keyword: 출력할 키워드 문자열.
    """
    ctx.after_space = False
    ctx.after_comma_newline = False

    # 이전 절 인덴트 해제
    if ctx.stack.top_level > 0:
        ctx.stack.decrease_top()

    newline = "\n" if ctx.has_content else ""
    indent = ctx.stack.indent_str
    padded = pad_keyword(keyword)
    ctx.emit(f"{newline}{indent}{padded} ")

    # 현재 절 추적 (GROUP BY/ORDER BY 수평 나열 판단용)
    ctx.current_clause = keyword.upper()

    # 이 절의 내용 인덴트 시작
    ctx.stack.increase_top()


def _emit_join_like(ctx: _RenderCtx, keyword: str) -> None:
    """JOIN/AND/OR 키워드를 절과 같은 열에 정렬하여 출력한다.

    TOP_LEVEL -1 → 패딩 출력 → TOP_LEVEL +1

    Args:
        ctx: 렌더링 컨텍스트.
        keyword: 출력할 키워드 문자열.
    """
    ctx.after_space = False
    ctx.after_comma_newline = False

    ctx.stack.decrease_top()
    newline = "\n" if ctx.has_content else ""
    indent = ctx.stack.indent_str
    padded = pad_keyword(keyword)
    ctx.emit(f"{newline}{indent}{padded} ")
    ctx.stack.increase_top()


def _emit_set_op(ctx: _RenderCtx, keyword: str) -> None:
    """UNION/INTERSECT/EXCEPT 키워드를 최상위 레벨로 리셋 후 출력한다.

    Args:
        ctx: 렌더링 컨텍스트.
        keyword: 출력할 키워드 문자열.
    """
    ctx.after_space = False
    ctx.after_comma_newline = False

    ctx.stack.top_level = 0
    ctx.stack.block_level = 0
    padded = pad_keyword(keyword)
    newline = "\n" if ctx.has_content else ""
    # 뒤에 오는 절 키워드(SELECT 등)가 자체 줄바꿈을 추가하므로
    # 여기서는 앞 줄바꿈만 추가한다 (빈 줄 방지)
    ctx.emit(f"{newline}{padded}")


_TRAILING_SPACE_RE = re.compile(r" +\n")
_MULTI_SPACE_RE = re.compile(r"(?<=[^\n]) {2,}")
_LEADING_NEWLINES_RE = re.compile(r"^\n+")
_TRAILING_NEWLINES_RE = re.compile(r"\n+$")


def _post_process(sql: str) -> str:
    """렌더링된 SQL의 공백/개행을 정리한다.

    - 줄 끝 공백 제거
    - 콘텐츠 내 연속 공백을 단일 공백으로 (인덴트 앞 공백은 유지)
    - 앞뒤 불필요한 개행 제거

    Args:
        sql: 렌더링된 SQL 문자열.

    Returns:
        정리된 SQL 문자열.
    """
    # 줄 끝 공백 제거
    sql = _TRAILING_SPACE_RE.sub("\n", sql)

    # 각 줄 내부 연속 공백 정리 (인덴트는 그대로 유지)
    lines = sql.split("\n")
    cleaned: list[str] = []
    for line in lines:
        leading = len(line) - len(line.lstrip(" "))
        indent_part = line[:leading]
        content = _MULTI_SPACE_RE.sub(" ", line[leading:])
        cleaned.append(indent_part + content.rstrip())

    result = "\n".join(cleaned)
    result = _LEADING_NEWLINES_RE.sub("", result)
    return _TRAILING_NEWLINES_RE.sub("", result)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공개 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def format_sql_tabular(sql: str, uppercase: bool = True) -> str:
    """SQL 문자열을 tabularRight 스타일로 포맷한다.

    sql-formatter(npm) tabularRight indent 스타일을 Python으로 재현한다.
    절 키워드(SELECT/FROM/WHERE 등)를 9자 우측 정렬 패딩하여
    데이터 영역이 10번째 열부터 수직 정렬되도록 출력한다.

    파싱 또는 렌더링 중 에러가 발생하면 원본 SQL을 그대로 반환한다.

    예시 출력::

        SELECT b.BR_NM AS "지점명",
               COUNT(a.ACN) AS "신규개설건수"
          FROM ADWOWN.TB_ADW_DEA208M a
        INNER JOIN ADWOWN.TB_ADW_COM001M b ON a.BLNG_BRCD = b.BLNG_BRCD
         WHERE a.OPEN_DT >= DATE_TRUNC('year', CURRENT_DATE)
      GROUP BY b.BR_NM
      ORDER BY "신규개설건수" DESC
         LIMIT 10

    Args:
        sql: 포맷할 SQL 문자열. 공백·개행이 섞인 상태도 허용.
        uppercase: True이면 키워드를 대문자로 변환 (기본값: True).

    Returns:
        tabularRight 포맷된 SQL 문자열.
        에러 발생 시 원본 sql을 그대로 반환.
    """
    if not sql or not isinstance(sql, str) or not sql.strip():
        return sql or ""

    try:
        parsed_list = sqlparse.parse(sql.strip())
        if not parsed_list:
            return sql

        # 다중 Statement 지원 (BEGIN-END, 세미콜론 구분 등)
        results: list[str] = []
        for stmt in parsed_list:
            # 공백만으로 이루어진 Statement는 건너뜀
            if not stmt.tokens or str(stmt).strip() == "":
                continue
            ctx = _RenderCtx(uppercase=uppercase)
            _render_token_list(list(stmt.tokens), ctx)
            rendered = "".join(ctx.parts)
            results.append(_post_process(rendered))

        return "\n".join(results) if results else sql

    except Exception:
        # 포맷팅 실패는 에이전트 흐름을 차단하지 않음 — 원본 반환
        try:
            from src.utils.logger import get_logger
            get_logger(__name__).debug(
                "SQL tabular 포맷팅 실패, 원본 반환",
                sql_length=len(sql),
            )
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "SQL tabular formatting failed, returning original",
                exc_info=True,
            )
        return sql
