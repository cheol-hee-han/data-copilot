# Tool-Prompt 동기화 설계 — 하이브리드 방안

작성: 2026-04-17

## 배경

### 현재 구조

- **Tool 정의**: `src/agents/nodes/reason/tools.py` — `TOOL_MAP` 딕셔너리에 10개 도구 등록
- **Tool 참조 프롬프트**: 2개 파일에서 tool 이름을 텍스트로 하드코딩
  - `resources/prompts/reason/recovery_agent_system.txt`
  - `resources/prompts/reason/context_interpreter_system.txt`

### 문제

tool을 disable하거나 추가할 때 `TOOL_MAP`만 수정하면 코드는 반영되지만,
프롬프트에 하드코딩된 tool 이름·우선순위·사용법·예시는 수동으로 편집해야 한다.

프롬프트에는 단순 나열이 아닌 **맥락적 가이드**(도구 간 교체 전략, 조건부 우선순위,
위반 사례, JSON 예시 등)가 포함되어 있어 전체를 자동 렌더링하기 어렵다.

---

## 설계 원칙

**자동 생성 영역**과 **수동 유지 영역**을 분리하고, 수동 영역에는 섹션 태깅을 적용한다.

| 영역 | 내용 | 관리 방식 |
|------|------|----------|
| 자동 생성 | tool 목록, 입력 형식, 페이징 지원 여부 | Tool 레지스트리에서 런타임 렌더링 |
| 수동 유지 | 전략, 판단 기준, 위반 사례, JSON 예시 | 텍스트 작성 + `[TOOL:name]` 태깅 |

---

## 구현 설계

### 1단계: Tool 레지스트리 확장 (`tools.py`)

`TOOL_MAP`의 단순 `{name: func}` 구조를 메타데이터 포함 구조로 확장한다.

```python
@dataclass
class ToolDef:
    """도구 정의 — 프롬프트 렌더링에 필요한 메타데이터 포함."""
    name: str
    func: Callable
    enabled: bool = True
    category: str = "search"           # lookup | search | get
    priority: int = 5                  # 1(최고) ~ 10(최저)
    description: str = ""              # 프롬프트용 한 줄 설명
    input_format: str = ""             # 입력 형식 설명
    supports_paging: bool = False
    usage_notes: list[str] = field(default_factory=list)

TOOL_REGISTRY: list[ToolDef] = [
    ToolDef(
        name="search_table_meta",
        func=_tool_search_table_meta,
        category="search",
        priority=1,
        description="업무 키워드로 테이블/컬럼 메타 검색",
        input_format="검색어, page=N",
        supports_paging=True,
    ),
    ToolDef(
        name="lookup_table_meta",
        func=_tool_lookup_table_meta,
        category="lookup",
        priority=2,
        description="영문 테이블명으로 단일 테이블 메타 조회",
        input_format="테이블명",
        supports_paging=False,
    ),
    # ... 나머지 도구 ...
]

# 기존 TOOL_MAP은 레지스트리에서 자동 생성
TOOL_MAP: dict[str, Any] = {
    t.name: t.func for t in TOOL_REGISTRY if t.enabled
}
```

**핵심**: `TOOL_MAP`은 기존과 완전 호환. `execute_tool()`, `_TABLE_META_TOOLS`, `_QDRANT_TOOLS` 등 기존 코드 변경 없음.

### 2단계: 프롬프트 자동 생성 함수 (`prompt_tool_renderer.py`)

레지스트리에서 enabled 도구만 추출하여 프롬프트 섹션을 자동 생성한다.

```python
def render_tool_list(registry: list[ToolDef]) -> str:
    """[TOOLS] 섹션의 도구 목록·입력 형식을 자동 생성."""
    enabled = [t for t in registry if t.enabled]
    enabled.sort(key=lambda t: t.priority)

    sections: list[str] = []
    for category, label in [("lookup", "lookup 도구"), ("search", "search 도구"), ("get", "get 도구")]:
        tools = [t for t in enabled if t.category == category]
        if not tools:
            continue
        lines = [f"\n{label}:"]
        for t in tools:
            page_note = " (page 지원)" if t.supports_paging else " (page 미지원)"
            lines.append(f"- {t.name}({t.input_format}): {t.description}{page_note}")
            for note in t.usage_notes:
                lines.append(f"  ※ {note}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def render_priority_guide(registry: list[ToolDef]) -> str:
    """우선순위 가이드를 자동 생성."""
    enabled = sorted(
        [t for t in registry if t.enabled],
        key=lambda t: t.priority,
    )
    lines = ["## 도구 우선순위 가이드", ""]
    for i, t in enumerate(enabled, 1):
        lines.append(f"{i}. {t.name}: {t.description}")
    return "\n".join(lines)


def render_input_format(registry: list[ToolDef]) -> str:
    """입력 형식 규칙을 자동 생성."""
    enabled = [t for t in registry if t.enabled]
    lines = ["## 도구 input 형식 규칙", ""]
    for category in ["lookup", "search", "get"]:
        tools = [t for t in enabled if t.category == category]
        for t in tools:
            lines.append(f"- {t.name}: \"input\": \"{t.input_format}\"")
    return "\n".join(lines)
```

### 3단계: 프롬프트 섹션 태깅

수동 작성 영역(전략, 위반 사례, JSON 예시)에 `[TOOL:name]...[/TOOL:name]` 태그를 추가한다.
복수 도구에 걸치는 가이드는 쉼표로 나열한다.

**recovery_agent_system.txt 적용 예시:**

```
[TOOLS]

## 사용 가능한 도구
{{tool_list}}

## 도구 페이징
{{tool_paging_notes}}

{{tool_priority_guide}}

{{tool_input_formats}}


[HALLUCINATION_GUARD]

원칙:
- [TOOLS]에 명시된 도구 이름만 execution_plan에 사용한다
- dead_ends에 기록된 동일 (tool, input) 조합을 반복하지 않는다
...

[TOOL:search_table_meta,search_use_cases,search_manual]
### 위반 2 — dead_ends 반복
- 입력 상황: dead_ends에 search_table_meta("연체율, page=1") 실패 기록 존재
- 잘못된 출력: 동일한 {"tool": "search_table_meta", "input": "연체율, page=1"}
- 올바른 출력: search_manual("연체율 산출식, page=1") 또는 서술형 문장으로 변형하여 search_use_cases 호출
[/TOOL:search_table_meta,search_use_cases,search_manual]

[TOOL:search_table_meta,search_use_cases]
### 위반 3 — 도구 교체를 확장 탐색으로 오해
- 입력 상황: search_table_meta 짧은 키워드로 실패
- 잘못된 출력: 같은 짧은 키워드를 그대로 search_use_cases에 입력
- 올바른 출력: 입력을 서술형 문장으로 재구성한 뒤 search_use_cases 호출
[/TOOL:search_table_meta,search_use_cases]
```

**`{{placeholder}}`**: 자동 생성 영역. 프롬프트 로드 시 레지스트리에서 렌더링.
**`[TOOL:name]...[/TOOL:name]`**: 수동 유지 영역. 태그 내 도구가 전부 disabled면 해당 블록 제거.

### 4단계: 프롬프트 로더 통합

기존 프롬프트 로딩 시점에 자동 생성 + 태그 필터링을 적용한다.

```python
import re

def load_prompt(
    template_path: str,
    registry: list[ToolDef],
) -> str:
    """프롬프트 템플릿을 로드하고 도구 섹션을 동적으로 구성한다."""
    template = Path(template_path).read_text(encoding="utf-8")

    # 1. 자동 생성 영역 치환
    template = template.replace("{{tool_list}}", render_tool_list(registry))
    template = template.replace("{{tool_priority_guide}}", render_priority_guide(registry))
    template = template.replace("{{tool_input_formats}}", render_input_format(registry))

    # 2. disabled tool 관련 섹션 제거
    disabled = {t.name for t in registry if not t.enabled}
    template = _strip_disabled_sections(template, disabled)

    return template


def _strip_disabled_sections(text: str, disabled: set[str]) -> str:
    """[TOOL:name]...[/TOOL:name] 블록에서 모든 도구가 disabled이면 제거."""
    def replacer(m: re.Match) -> str:
        tool_names = {n.strip() for n in m.group(1).split(",")}
        # 태그 내 도구가 모두 disabled이면 블록 전체 제거
        if tool_names.issubset(disabled):
            return ""
        return m.group(2)  # 하나라도 enabled이면 내용만 유지 (태그 제거)

    return re.sub(
        r"\[TOOL:([\w,\s]+)\](.*?)\[/TOOL:\1\]",
        replacer,
        text,
        flags=re.DOTALL,
    )
```

---

## 영향 범위

### 수정 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/agents/nodes/reason/tools.py` | `ToolDef` dataclass 추가, `TOOL_REGISTRY` 정의, `TOOL_MAP` 자동 생성 |
| `src/utils/prompt_tool_renderer.py` | 신규 — 자동 렌더링 함수 |
| `resources/prompts/reason/recovery_agent_system.txt` | [TOOLS] 섹션을 `{{placeholder}}`로 교체, 수동 영역에 `[TOOL:name]` 태깅 |
| `resources/prompts/reason/context_interpreter_system.txt` | 예시 내 tool 참조에 `[TOOL:name]` 태깅 |
| 프롬프트 로딩 호출부 | `load_prompt()` 호출 시 `registry` 파라미터 추가 |

### 변경 없는 파일

| 파일 | 이유 |
|------|------|
| `execute_tool()` | `TOOL_MAP` 인터페이스 동일 |
| `context_retriever_node` | `TOOL_MAP[name](input)` 호출 패턴 동일 |
| `_TABLE_META_TOOLS`, `_QDRANT_TOOLS` | `TOOL_REGISTRY`와 별개로 유지 가능 (또는 레지스트리에서 파생) |

---

## 적용 범위별 태깅 가이드

### recovery_agent_system.txt 태깅 대상

| 라인 | 내용 | 태그 |
|------|------|------|
| 24 | 도구 변경 — search_table_meta ↔ search_use_cases ↔ search_manual 간 교체 | `[TOOL:search_table_meta,search_use_cases,search_manual]` |
| 60-119 | [TOOLS] 섹션 전체 (도구 목록, 페이징, 우선순위, 입력 형식) | `{{placeholder}}`로 교체 |
| 131-154 | 위반 사례 1~5 | 각 사례별 참조 도구에 태깅 |
| 166-304 | 예시 1~6 | 각 예시별 사용 도구에 태깅 |

### context_interpreter_system.txt 태깅 대상

| 라인 | 내용 | 태그 |
|------|------|------|
| 70-79 | search_use_cases enrichment 규칙 | `[TOOL:search_use_cases]` |
| 128-132 | 도구별 explored_* 배열 규칙 | 각 도구별 태깅 |
| 173-263 | 예시 1 (search_use_cases + search_table_meta + get_sample_rows) | `[TOOL:search_use_cases,search_table_meta,get_sample_rows]` |
| 266-341 | 예시 2 (get_column_values + get_column_profile) | `[TOOL:get_column_values,get_column_profile]` |
| 355-412 | 예시 3 (search_use_cases 충돌) | `[TOOL:search_use_cases]` |
| 416-556 | 예시 4 (교차 참조) | `[TOOL:search_use_cases,search_table_meta,search_biz_terms]` |

---

## 구현 순서

1. `ToolDef` + `TOOL_REGISTRY` 정의 (tools.py) — `TOOL_MAP` 하위호환 유지
2. `prompt_tool_renderer.py` 작성 — 렌더링 함수 + 태그 필터링
3. recovery_agent_system.txt 태깅 적용 — [TOOLS] 섹션 플레이스홀더 교체 + 수동 영역 태깅
4. context_interpreter_system.txt 태깅 적용
5. 프롬프트 로딩 호출부 통합
6. 기존 프롬프트 출력과 diff 비교 — 렌더링 결과가 기존 텍스트와 동일한지 검증

---

## 검증 방법

- **렌더링 일치 테스트**: 모든 tool enabled 상태에서 렌더링된 프롬프트가 기존 텍스트와 의미적으로 동일한지 확인
- **disable 테스트**: 특정 tool을 `enabled=False`로 설정 후 해당 섹션이 프롬프트에서 제거되는지 확인
- **예시 보존 테스트**: 태깅된 예시 블록이 관련 tool이 활성일 때 정상 포함되는지 확인

---

## 고려사항

### 태깅 한계

- 도구 간 상호참조("search_table_meta 실패 시 search_use_cases로 교체")는 양쪽 도구에 모두 태깅해야 한다
- 한쪽만 disabled되면 교체 전략 자체가 무의미해지므로 블록 제거가 올바른 동작
- 태그가 중첩(`[TOOL:A]...[TOOL:B]...[/TOOL:B]...[/TOOL:A]`)되면 정규식이 복잡해진다 — 중첩 금지, 플랫 구조 유지

### 폐쇄망 배포 시

- 폐쇄망에서 Qdrant/MongoDB 미사용 시 관련 도구를 `enabled=False`로 설정하면 프롬프트에서 자동 제거
- 설정 파일(`.env` 등)에서 disabled_tools를 지정하고 앱 시작 시 레지스트리에 반영하는 패턴 권장
