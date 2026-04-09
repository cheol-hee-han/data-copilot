# 리네임 + 기능추가 후 코드 정합성 검증 리포트

**일시**: 2026-04-04  
**범위**: glossary->biz_term, ManualEntry->BizManualEntry 리네임 + state/knowledge_fetcher 기능 추가  
**검증 제외**: `.claude/worktrees/`

---

## 1. glossary 잔존 참조

### src/, resources/ -- PASS (잔존 없음)

`src/` 전체와 `resources/` 전체에서 `glossary` (대소문자 무관) 참조가 완전히 제거되었다.
- `mongo_biz_term_collection` (config.py:114)
- `_TPL_BIZ_TERM` (mongo_connector.py:46)
- `search_biz_terms` (tools.py:119, mongo_connector.py:316)
- `pipeline_biz_term.json` (resources/connectors/mongo/)

### tests/ -- WARNING (잔존 2건)

| 등급 | 파일 | 라인 | 내용 |
|------|------|------|------|
| **[W]** WARNING | `tests/test_cases/agentic_e2e_test_catalog.json` | 106 | `"expected_action": "explore_glossary"` -- `explore_biz_terms`로 변경 필요 |
| [I] Info | `tests/reports/agentic_real_e2e_report.txt` | 20, 65 | `[OK] mongo_glossary`, `[OK] table_glossary_search` -- 과거 실행 결과 로그이므로 자동 갱신됨. 재실행 시 해소 |

### docs/ -- INFO (과거 문서, 기능 영향 없음)

docs/ 내 glossary 잔존은 모두 **과거 작성된 설계 리뷰/전략 문서**이며 실행 코드에 영향을 주지 않는다.

| 파일 | 잔존 건수 | 비고 |
|------|-----------|------|
| `docs/architecture/project-structure.md` | 1건 | `pipeline_glossary.json` 경로 |
| `docs/architecture/state-architecture.md` | 5건 | `glossary:연체율` 등 KI 키 예시 |
| `docs/guides/migration-guide.md` | 1건 | MongoDB 컬렉션명 표 |
| `docs/reviews/code/20260331-*.md` | 1건 | 코드 리뷰 인용 |
| `docs/reviews/design/20260325-*.md` | 3건+2건 | 설계 리뷰 도구명 |
| `docs/reviews/design/20260401-*.md` | 6건 | 크로스 리뷰 도구명 |
| `docs/working/*/prototype/nodes/prompts/plan_system.txt` | 2건 | 프로토타입 프롬프트 |

### devtools/ -- PASS (잔존 없음)

`devtools/scripts/seed_mongodb.py`에서 `glossary` -> `biz_term` 리네임 완료.

---

## 2. ManualEntry / explored_manuals 잔존

### PASS -- 잔존 없음

`src/`, `tests/`, `devtools/`, `resources/` 전체에서 다음 리네임 전 이름이 발견되지 않는다:
- `ManualEntry` (0건)
- `explored_manuals` (0건, `explored_biz_manuals`만 존재)
- `manual_id` (0건, `biz_manual_id`만 존재)
- `_store_manuals` (0건, `_store_biz_manuals`만 존재)

---

## 3. import 정합성

### PASS

**state.py 정의 확인**:
- `BizManualEntry` (L170), `BizTermEntry` (L185): BaseModel로 올바르게 정의
- `RelevanceStatus` (enums.py:138): Enum으로 올바르게 정의, state.py에서 re-export (L35)

**knowledge_fetcher.py import 확인** (L19-31):
- `BizTermEntry`, `BizManualEntry`, `ColumnInfo`, `CandidateTable`, `CodeMeta`, `KeyDateColumn`, `ObservedDateColumn`, `Phase`, `StepStatus`, `MAX_TOOL_CALLS` -- 모두 사용 확인됨, 미사용 import 없음

**다른 파일의 import**:
- `BizManualEntry`, `BizTermEntry`는 현재 `knowledge_fetcher.py`에서만 import됨
- `__init__.py`에는 re-export되지 않음 (향후 `knowledge_interpreter` 등에서 사용 시 추가 필요)

---

## 4. 직렬화 정합성

### ColumnInfo -- PASS (기존 호환)

신규 필드 전부 `Optional`(None 기본값)으로 선언되어 기존 코드와 호환:
- `total_rows: int | None = None` (L159)
- `non_null_count: int | None = None` (L160)
- `null_rate: float | None = None` (L161)
- `distinct_count: int | None = None` (L162)
- `min_val: str | None = None` (L163)
- `max_val: str | None = None` (L164)
- `discovered_values: list[str] | None = None` (L167)

### BizManualEntry / BizTermEntry -- PASS

전 필드가 JSON 직렬화 가능한 기본 타입(str, float, list[str], RelevanceStatus(str Enum)):
- `BizManualEntry`: biz_manual_id(str), content(str), score(float), source(str), relevance_status(RelevanceStatus), relevance_reason(str)
- `BizTermEntry`: biz_term_id(str), term(str), definition(str), synonyms(list[str]), related_tables(list[str]), source(str), relevance_status(RelevanceStatus), relevance_reason(str)

### ReasoningState 새 필드 -- PASS

- `explored_biz_manuals: list[BizManualEntry] = Field(default_factory=list)` (L476)
- `explored_biz_terms: list[BizTermEntry] = Field(default_factory=list)` (L480)

`default_factory=list`로 올바르게 선언되어 기존 상태와 호환.

---

## 5. 로직 문제

### 5-1. `_find_table` 파싱 -- PASS

```python
# knowledge_fetcher.py L286-287
raw_table = qualified_input.split(",")[0].strip()
bare_name = raw_table.rpartition(".")[2] if "." in raw_table else raw_table
```

쉼표 구분 첫 번째 요소에서 `schema.table`을 `table`로 정확히 분리. `_should_skip_step`의 동일 패턴(L75-77)과 일관.

### 5-2. `_store_column_values` / `_find_column` 매칭 -- PASS

```python
# L352: column_name = parts[1] (쉼표 구분 두 번째)
# L358: col = _find_column(table, column_name) -> col.name == column_name
```

TOOL_MAP 어댑터(`_tool_search_column_values`)가 `table,column,keyword` 형식으로 전달하므로 `parts[1]`이 column_name. `_find_column`은 `col.name`으로 정확 매칭.

### 5-3. `_store_date_distribution` 중복 가드 -- PASS

```python
# L334: existing = {odc.column_name for odc in table.observed_date_columns}
# L335: if date_column in existing: return
```

Phase 1에서 `_apply_tool_result` 경유 저장 + Phase 2에서 `_observe_all_date_distributions` 경유 저장 모두 동일한 중복 가드 패턴 사용 (Phase 2: L570).

### 5-4. dead code 제거 확인 -- PASS

`knowledge_fetcher_node`(L484-532)에서 `knowledge_items`, `discovered_facts` 추출/재할당 코드가 완전히 제거됨. `grep -n "knowledge_items\|discovered_facts" knowledge_fetcher.py` 결과 0건.

### 5-5. `_store_column_profile`의 `null_count` 필드 누락 -- CRITICAL

```python
# knowledge_fetcher.py L384
col.null_count = int(result.get("null_count", 0))
```

`ColumnInfo` 모델에 `null_count` 필드가 정의되어 있지 않다. Pydantic v2의 기본 설정(`extra="ignore"`)에서 이 할당은 **Python 인스턴스 속성으로만 설정되고 모델 필드에 포함되지 않는다**. 따라서:

1. `model_dump()` / JSON 직렬화 시 `null_count`가 누락됨
2. 하류 노드가 `col.null_count`를 참조하면 **AttributeError 발생 가능** (model_copy 후 사라짐)
3. `null_rate`는 이미 있으므로 `null_count`는 `total_rows - non_null_count`로 계산 가능하지만, 명시적 필드가 있어야 직렬화에 포함됨

**수정 방안**: `ColumnInfo`에 `null_count: int | None = None` 필드를 추가하거나, L384를 제거하고 `null_rate`만 사용.

---

## 6. 죽은 코드

### 사용되지 않는 import -- PASS

`knowledge_fetcher.py`의 모든 import가 본문에서 사용됨을 확인.

### 참조되지 않는 함수/변수 -- PASS

모든 헬퍼 함수(`_find_table`, `_find_column`, `_store_*`, `_identify_*`, `_parse_meta_columns`, `_resolve_key_date_columns`)가 최소 1회 이상 호출됨.

### 의미 없는 할당 -- PASS

`knowledge_items`, `discovered_facts` 추출만 하고 수정/재할당하지 않는 dead code가 제거됨 확인.

---

## 7. 명명 일관성

### biz_manual vs manual 혼재 -- WARNING (의도적 혼재)

| 위치 | 표현 | 비고 |
|------|------|------|
| TOOL_MAP 키 | `search_manual` | LLM이 생성하는 도구명 (프롬프트와 일치 필요) |
| state 필드 | `explored_biz_manuals` | 내부 상태 필드 |
| 모델 클래스 | `BizManualEntry` | Pydantic 모델 |
| 함수명 | `_store_biz_manuals` | 내부 헬퍼 |

**평가**: TOOL_MAP 키(`search_manual`)와 내부 상태(`biz_manual`)의 차이는 **의도적**이다. TOOL_MAP 키는 LLM 프롬프트에 노출되는 도구 이름이므로, 프롬프트 변경과 동시에 수행해야 한다. 현재는 프롬프트와 TOOL_MAP이 `search_manual`로 일관되고, 내부 모델만 `BizManual`이므로 실질적 혼란은 적다. 단, 추후 통일을 고려할 수 있다.

### biz_term vs glossary 혼재 -- PASS

`src/`, `resources/` 범위에서 `glossary` 완전 제거. `biz_term` 용어로 통일 확인.

### docstring 일관성 -- PASS

`knowledge_fetcher.py` 내 모든 docstring이 `biz_manual`, `biz_term` 용어를 사용.
state.py의 `BizManualEntry`, `BizTermEntry` docstring도 올바른 용어 사용.

---

## 종합 결과

| 등급 | 건수 | 요약 |
|------|------|------|
| CRITICAL | 1건 | `ColumnInfo.null_count` 필드 누락 (knowledge_fetcher L384) |
| WARNING | 1건 | `agentic_e2e_test_catalog.json` L106: `explore_glossary` 잔존 |
| INFO | 다수 | docs/ 내 과거 문서 glossary 참조 (기능 영향 없음) |

### 필수 조치 항목

1. **[CRITICAL]** `src/agents/state/state.py` ColumnInfo에 `null_count: int | None = None` 필드 추가
2. **[WARNING]** `tests/test_cases/agentic_e2e_test_catalog.json` L106: `"expected_action": "explore_glossary"` -> `"expected_action": "explore_biz_terms"` 변경
3. **[INFO]** docs/ 내 glossary 참조는 문서 최신화 시 일괄 정리 (기능 영향 없으므로 급하지 않음)
