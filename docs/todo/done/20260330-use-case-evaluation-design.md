# (완료) 유사 SQL 평가 체계 + 배치 출력 구조 개선 설계

> **Version 3.0** (2026-03-29)
> 유사 SQL(explored_use_cases)을 **기존 배치 LLM 해석 과정에서 함께 평가**하여,
> 검증된 것만 sql_generator에 전달하고, structural_hints 프롬프트 주입을 제거하는 설계안.
>
> 동시에 배치 LLM 출력 JSON의 **selected/rejected 구조를 테이블별 reason 방식으로 개선**.
>
> v1.0(rule-based Phase 2.5) → v2.0(LLM-based 배치 해석 통합) → v3.0(출력 구조 정규화 + verdict 제거)
> R11(sql_generator 프롬프트 우선순위 규칙)을 대체하는 근본적 해법.

---

## 1. 배경: 현재의 문제

### 1.1 유사 SQL이 평가 없이 사용됨

```text
Qdrant search_use_cases(query)
    │
    ├── explored_use_cases에 원문 저장
    │     └── sql_generator: {reference_sqls}로 원문 주입 (최대 3건, 필터링 없음)
    │
    └── extract_hints_from_use_cases()로 sqlglot 파싱
          └── structural_hints에 저장
                └── sql_generator: {structural_hints}로 요약 주입
```

- 유사 SQL이 현재 질의와 **의미적으로 관련되는지** 평가하지 않음
- 유사 SQL의 **테이블/컬럼이 실제 존재하는지** 검증하지 않음
- 유사 SQL에서 **활용할 수 있는 지식(조인 패턴, 코드값)** 을 추출하지 않음
- `_rule_use_cases()`가 하는 건 `score > 0.5` 컷오프와 `use_case:*` KI 존재 기록뿐

### 1.2 같은 소스에서 이중 주입

`{reference_sqls}`(원문)과 `{structural_hints}`(파싱 요약)는 **동일 유사 SQL의 두 가지 표현**.
sql_generator LLM이 같은 정보를 두 곳에서 만나며, 두 소스가 `{confirmed_terms}`와 불일치할 때
어느 쪽을 우선할지 규칙이 없음 (R11 문제의 근본 원인).

### 1.3 v1.0 rule-based 접근의 한계

v1.0에서는 sqlglot 파싱 → 테이블/컬럼 대조(Phase 2.5)로 평가하려 했으나,
**근본적으로 문맥(context)을 모르는 구조적 매칭**의 한계가 있다:

- 테이블은 겹치지만 **요구사항이 완전히 다른** 유사 SQL을 걸러낼 수 없음
  (예: "여신 연체율" vs "여신 실행 건수" — 같은 테이블이지만 다른 요구사항)
- 테이블명이 다르지만 **같은 의미의 데이터**를 담고 있는 경우를 놓침
- 유사 SQL의 **비즈니스 로직(WHERE 조건, 집계 방식)**이 현재 질의에 유용한지 판단 불가

**핵심 인사이트**: 유사 SQL 평가는 "구조가 겹치는가?"가 아니라 "같은 종류의 요구사항인가?"를 판단해야 한다.
이것은 LLM만이 할 수 있는 작업이다.

### 1.4 배치 LLM 출력의 selected/rejected 구조 문제

현재 출력:

```json
{
  "selected": ["TB_LOAN_MASTER", "TB_LOAN_EXEC"],
  "rejected": ["TB_LOAN_DETAIL"],
  "comparison_reason": "TB_LOAN_MASTER와 TB_LOAN_EXEC는 활용사례에서 조인 패턴이 확인되고... TB_LOAN_DETAIL은 부대조건 관리 테이블로 무관"
}
```

문제점:
- `comparison_reason`이 모든 테이블의 판정 사유를 **한 문장에 뭉침**
- 테이블이 5~10개가 되면 어떤 테이블이 왜 빠졌는지 파싱 불가
- 코드에서 특정 테이블의 제외 사유를 추출하려면 자연어 파싱 필요

### 1.5 structural_hints의 실제 소비자 분석

| 소비자 | 용도 | reference_sqls로 대체 가능? |
| --- | --- | --- |
| planner — Fast-Path 판정 | `source_tables` 확인 | **불가** — rule-based 코드에서 테이블명 필요 |
| planner — 실행계획 생성 | `source_tables`로 search_table_meta 스텝 생성 | **불가** — 파싱된 테이블명 필요 |
| planner — 가설 수립 컨텍스트 | LLM에 구조 요약 전달 | 대체 가능 |
| **sql_generator** | 프롬프트 {structural_hints} | **대체 가능** — {reference_sqls}와 중복 |
| result_finalizer | 최종 응답 context | 대체 가능 |

**결론**: planner 내부 로직(rule-based)에서는 필요하지만, sql_generator 프롬프트에서는 불필요.

---

## 2. 설계 방안

### 2.1 핵심 아이디어

두 가지 개선을 **배치 LLM 출력 JSON 재설계**로 동시에 해결한다:

1. **유사 SQL 평가**: 기존 Phase 3 배치 LLM에서 함께 수행 (새 Phase 없음)
2. **테이블 판정 구조화**: selected/rejected를 테이블별 reason 방식으로 변경

배치 LLM은 이미 다음 컨텍스트를 모두 보유하고 있다:

- `{original_query}` — 사용자의 원래 질의
- `{tool_results}` — search_use_cases 결과 포함
- `{table_observations}` — candidate_tables의 메타/샘플/날짜분포
- `{unresolved_items}` — 아직 미해결된 지식 항목

따라서 LLM은 "이 유사 SQL이 현재 질의와 같은 종류의 요구사항인가?"를 **문맥 기반으로** 판단할 수 있다.

### 2.2 AS-IS → TO-BE 흐름

```text
AS-IS:
  유사 SQL (미평가) ──────────────────────── sql_generator 프롬프트까지 별도로 흘러감
  knowledge_items (탐색 검증) ─────────────── sql_generator 프롬프트까지 별도로 흘러감
  → 두 소스가 sql_generator에서 처음 만남 → LLM이 알아서 판단 (비결정적)

TO-BE:
  유사 SQL ──┐
             ▼
  context_explorer Phase 3: 배치 LLM 해석 (기존)
       │  + relevant_use_cases 출력 (관련 있는 것만)
       │  + selected/rejected 테이블별 reason
       │
       ▼
  Phase 4.5: _annotate_use_case_relevance()
       │    — 관련 판정된 use_case만 explored_use_cases에 마킹
       │
       ▼
  sql_generator: {reference_sqls}는 관련 판정분만 포함
                 {structural_hints} 제거
```

### 2.3 rule-based 대비 장점

| 관점 | rule-based (v1.0) | LLM-based (v3.0) |
| --- | --- | --- |
| 판단 기준 | 테이블/컬럼 구조 겹침 | 요구사항 의미 유사성 |
| 문맥 인식 | 없음 — 순수 집합 연산 | 전체 탐색 결과 + 원문 질의 참조 |
| 구현 복잡도 | `_evaluate_use_cases()` 신규 함수 + sqlglot 재파싱 | 프롬프트 가이드라인 추가 + 출력 필드 변경 |
| 추가 비용 | CPU (sqlglot 파싱) | 없음 — 기존 배치 LLM 호출에 포함 |
| 확장성 | 새로운 평가 기준마다 코드 추가 | 프롬프트 수정만으로 평가 기준 변경 |
| 오류 모드 | 구조는 겹치지만 의미가 다른 SQL 통과 | LLM hallucination (§4.2 참조) |

---

## 3. 배치 LLM 출력 JSON 재설계

### 3.1 AS-IS 출력 스키마

```json
{
  "interpretations": [
    {
      "tool_name": "도구명",
      "tool_input": "입력",
      "insight": "관찰 메모 한 문장",
      "knowledge_updates": [
        {
          "key": "용어 식별자",
          "value": "확인된 값",
          "confidence": 0.7,
          "new_status": "PROBABLE",
          "source": "출처",
          "evidence": "근거 한 문장",
          "is_critical": true
        }
      ],
      "new_tables": [
        {
          "table_name": "테이블명",
          "role": "역할",
          "relevant_columns": ["컬럼"],
          "join_keys": ["조인키"],
          "entity_scope": "",
          "functional_usage": "",
          "data_refresh_hint": ""
        }
      ]
    }
  ],
  "selected": ["테이블명1", "테이블명2"],
  "rejected": ["테이블명3"],
  "comparison_reason": "모든 테이블의 판정 사유를 한 문장에 뭉친 텍스트"
}
```

### 3.2 TO-BE 출력 스키마

```json
{
  "interpretations": [
    {
      "tool_name": "도구명",
      "tool_input": "입력",
      "insight": "관찰 메모 한 문장",
      "knowledge_updates": [
        {
          "key": "용어 식별자",
          "value": "확인된 값",
          "confidence": 0.7,
          "new_status": "PROBABLE",
          "source": "출처",
          "evidence": "근거 한 문장",
          "is_critical": true
        }
      ],
      "new_tables": [
        {
          "table_name": "테이블명",
          "role": "역할",
          "relevant_columns": ["컬럼"],
          "join_keys": ["조인키"],
          "entity_scope": "",
          "functional_usage": "",
          "data_refresh_hint": ""
        }
      ]
    }
  ],
  "selected": [
    {"table_name": "TB_LOAN_MASTER", "reason": "활용사례에서 조인 패턴 확인, 날짜 분포가 시간 조건 포함"},
    {"table_name": "TB_LOAN_EXEC", "reason": "EXEC_DT 기준 실행 내역, 활용사례에서 COUNT(*) 집계 대상"}
  ],
  "rejected": [
    {"table_name": "TB_LOAN_DETAIL", "reason": "부대조건 관리 테이블로 실행 건수 집계와 무관"}
  ],
  "relevant_use_cases": [
    {
      "sql_id": "uc_001",
      "reason": "현재 질의와 동일한 여신 실행 건수 조회. LOAN_NO 조인, EXEC_DT 기간 필터 패턴 참고 가능"
    }
  ]
}
```

### 3.3 변경 요약

| 필드 | AS-IS | TO-BE | 변경 이유 |
| --- | --- | --- | --- |
| `selected` | `["테이블명"]` (문자열 리스트) | `[{"table_name": "...", "reason": "..."}]` | 테이블별 선정 사유 추적 |
| `rejected` | `["테이블명"]` (문자열 리스트) | `[{"table_name": "...", "reason": "..."}]` | 테이블별 제외 사유 추적 |
| `comparison_reason` | 전체 판정 사유 한 문장 | **제거** | selected/rejected에 reason이 각각 포함되므로 불필요 |
| `relevant_use_cases` | *(없음)* | `[{"sql_id": "...", "reason": "..."}]` | **신규** — 관련 있는 유사 SQL만 목록. 없으면 무관 판정 |

### 3.4 relevant_use_cases 설계 원칙

- **존재 자체가 포함 판정**: 목록에 있으면 관련 있음, 없으면 무관
- verdict/include 같은 별도 판정 필드 없음
- `sql_id`: `{tool_results}` 내 search_use_cases 결과의 id와 매칭
- `reason`: 왜 관련 있는지 + 어떤 패턴을 참고할 수 있는지 (자연어)

---

## 4. 구현 계획

### 변경 1: `batch_interpret_system.txt` — 프롬프트 변경

**파일**: `resources/prompts/reason/batch_interpret_system.txt`

**(a) 분석 지침에 #6 추가:**

```text
6. 유사 활용사례 SQL 평가
   - search_use_cases 결과에 포함된 유사 SQL 각각에 대해:
     a) 현재 사용자 요구사항과 같은 종류의 데이터 추출/분석인지 판단
     b) 관련 있는 SQL만 relevant_use_cases에 포함
     c) 해당 SQL에서 참고할 수 있는 패턴을 reason에 기재
        (조인 패턴, WHERE 조건의 코드값, 집계 방식, 날짜 필터 패턴 등)
   - 현재 질의와 무관한 유사 SQL은 relevant_use_cases에 포함하지 않음
```

**(b) 출력 형식 섹션의 JSON 스키마 변경:**

```json
{
  "interpretations": [ ... ],
  "selected": [
    {"table_name": "적합한 테이블명", "reason": "선정 사유"}
  ],
  "rejected": [
    {"table_name": "부적합한 테이블명", "reason": "제외 사유"}
  ],
  "relevant_use_cases": [
    {"sql_id": "유사SQL식별자", "reason": "관련성 판단 사유 및 참고 가능한 패턴"}
  ]
}
```

**(c) 예시 1, 2의 출력 JSON도 새 스키마에 맞게 수정:**

예시 1 출력 변경:

```json
{
  "selected": [
    {"table_name": "TB_LOAN_MASTER", "reason": "활용사례에서 LOAN_NO 조인 확인, 날짜 분포 2020-01~2024-03으로 시간 조건 포함"},
    {"table_name": "TB_LOAN_EXEC", "reason": "활용사례에서 COUNT(*) 집계 대상, EXEC_DT 날짜 분포 시간 조건 부합"}
  ],
  "rejected": [
    {"table_name": "TB_LOAN_DETAIL", "reason": "부대조건 관리 테이블로 실행 건수 집계와 무관"}
  ],
  "relevant_use_cases": [
    {"sql_id": "uc_001", "reason": "현재 질의와 동일한 월간 신규 대출 실행 건수 조회. LOAN_NO 조인, EXEC_DT BETWEEN 기간 필터 패턴 참고 가능"}
  ]
}
```

예시 2 출력 변경 (Cold Start — 활용사례 없음):

```json
{
  "selected": [
    {"table_name": "TB_FX_TRADE", "reason": "메타 설명이 질의와 부합, 날짜 분포(2023-06~2024-03)가 시간 조건 포함. 단, 활용사례 교차 확인 없어 CANDIDATE 수준"}
  ],
  "rejected": [],
  "relevant_use_cases": []
}
```

### 변경 2: `context_explorer.py` — BatchInterpretResult + 파서 변경

**파일**: `src/agents/nodes/reason/context_explorer.py`

**(a) BatchInterpretResult 구조 변경:**

```python
class BatchInterpretResult:
    def __init__(
        self,
        interpretations: list[dict] | None = None,
        knowledge_updates: list[KnowledgeItem] | None = None,
        new_tables: list[dict] | None = None,
        selected: list[dict] | None = None,          # 변경: str → dict
        rejected: list[dict] | None = None,           # 변경: str → dict
        relevant_use_cases: list[dict] | None = None,  # 신규
    ) -> None:
        self.interpretations = interpretations or []
        self.knowledge_updates = knowledge_updates or []
        self.new_tables = new_tables or []
        self.selected = selected or []
        self.rejected = rejected or []
        self.relevant_use_cases = relevant_use_cases or []
```

`comparison_reason` 필드 제거.

**(b) `_parse_batch_result()` 변경:**

```python
def _parse_batch_result(data: dict) -> BatchInterpretResult:
    """배치 LLM 응답 JSON을 BatchInterpretResult로 파싱한다."""
    # ... (knowledge_updates, new_tables 파싱은 기존과 동일) ...

    return BatchInterpretResult(
        interpretations=...,
        knowledge_updates=...,
        new_tables=...,
        selected=data.get("selected", []),
        rejected=data.get("rejected", []),
        relevant_use_cases=data.get("relevant_use_cases", []),
    )
```

**(c) selected/rejected를 참조하는 기존 코드 수정:**

기존에 `batch_result.selected`를 `list[str]`로 사용하던 코드를 `list[dict]`에 맞게 수정:

```python
# AS-IS:
selected_names = set(batch_result.selected)
rejected_names = set(batch_result.rejected)

# TO-BE:
selected_names = {t["table_name"] for t in batch_result.selected}
rejected_names = {t["table_name"] for t in batch_result.rejected}
```

### 변경 3: `context_explorer.py` — Phase 4.5 주석 부착 함수

**파일**: `src/agents/nodes/reason/context_explorer.py`

Phase 4(해석 결과 반영)와 Phase 5(부적합 테이블 제거) 사이에 삽입:

```python
def _annotate_use_case_relevance(
    explored_use_cases: list[dict],
    relevant_use_cases: list[dict],
) -> None:
    """배치 LLM의 relevant_use_cases 결과를 explored_use_cases에 부착한다.

    relevant_use_cases 목록에 있는 use_case는 _relevant=True + _eval_reason 부착.
    목록에 없는 use_case는 _relevant=False.
    """
    relevant_map = {
        e["sql_id"]: e.get("reason", "")
        for e in relevant_use_cases
        if "sql_id" in e
    }
    for uc in explored_use_cases:
        uc_id = uc.get("id", uc.get("_id", ""))
        if uc_id in relevant_map:
            uc["_relevant"] = True
            uc["_eval_reason"] = relevant_map[uc_id]
        else:
            uc["_relevant"] = False
            uc["_eval_reason"] = ""
```

호출 위치:

```python
# Phase 4: 해석 결과 반영 (기존)
_apply_batch_result(batch_result, ...)

# Phase 4.5: 유사 SQL 관련성 주석 부착 (신규)
_annotate_use_case_relevance(
    reason.explored_use_cases,
    batch_result.relevant_use_cases,
)

# Phase 5: 부적합 테이블 제거 (기존)
_reject_low_confidence_tables(...)
```

### 변경 4: `sql_generator.py` — reference_sqls 필터링 + structural_hints 제거

**파일**: `src/agents/nodes/reason/sql_generator.py`

`_build_agentic_prompt()` 변경:

**(a) reference_sqls 조립 — 관련 판정분만, reason을 데이터로 포함:**

코드는 데이터 블록만 생성하고, 포맷 구조(헤더, 안내문)는 프롬프트 템플릿에서 정의한다.

```python
# AS-IS: SQL 텍스트만 추출
ref_sqls = [uc.get("sql", "") for uc in reason.explored_use_cases[:3] if uc.get("sql")]
ref_text = "\n".join(f"```sql\n{sql}\n```" for sql in ref_sqls) if ref_sqls else "(없음)"

# TO-BE: 관련 판정분만 필터 + reason을 데이터로 포함
relevant = [
    uc for uc in reason.explored_use_cases
    if uc.get("_relevant", True)  # _relevant 키가 없으면 True (미평가 = 통과)
]
ref_blocks: list[str] = []
for uc in relevant[:5]:
    sql = uc.get("sql", "")
    if not sql:
        continue
    reason_text = uc.get("_eval_reason", "")
    block = ""
    if reason_text:
        block += f"- 관련성: {reason_text}\n"
    block += f"```sql\n{sql}\n```"
    ref_blocks.append(block)
ref_text = "\n\n".join(ref_blocks) if ref_blocks else "(없음)"
```

**(b) structural_hints 프롬프트 변수 조립 코드 제거:**

```python
# AS-IS:
hints_text = reason.structural_hints.to_prompt_text() if reason.structural_hints else "(없음)"

# TO-BE: 이 변수와 관련 코드 전체 삭제
```

### 변경 5: `sql_generator_system.txt` — 프롬프트 섹션 변경

**파일**: `resources/prompts/reason/sql_generator_system.txt`

**제거**: `## 유사 질의의 구조적 힌트` 섹션 전체 (현재 line 29-31)

```text
# AS-IS (제거 대상):
## 유사 질의의 구조적 힌트

{structural_hints}

## 참고 활용사례 SQL (구조 템플릿으로만 활용, 그대로 복사 금지)

{reference_sqls}
```

**변경**: 구조 안내를 프롬프트에서 정의하고, `{reference_sqls}`에는 데이터만 치환

```text
# TO-BE:
## 검증된 참고 SQL (구조 참고용 — 그대로 복사 금지)

아래 SQL은 현재 질의와 관련성이 확인된 참고 사례입니다.
각 SQL에 "관련성" 설명을 중점으로 참고하세요.

{reference_sqls}
```

치환 후 실제 프롬프트 예시:

```text
## 검증된 참고 SQL (구조 참고용 — 그대로 복사 금지)

아래 SQL은 현재 질의와 관련성이 확인된 참고 사례입니다.
각 SQL에 "관련성" 설명이 있으면 해당 부분을 중점으로 참고하세요.

- 관련성: 현재 질의와 동일한 여신 실행 건수 조회. LOAN_NO 조인, EXEC_DT 기간 필터 패턴 참고 가능

SELECT COUNT(*) FROM TB_LOAN_MASTER m
JOIN TB_LOAN_EXEC e ON m.LOAN_NO = e.LOAN_NO
WHERE e.EXEC_DT BETWEEN '20240301' AND '20240331'

- 관련성: 유사한 지점별 집계 구조. GROUP BY BR_CD 패턴 참고 가능

SELECT BR_CD, COUNT(*) FROM TB_LOAN_MASTER
GROUP BY BR_CD

```

### 변경 6: `confidence_scorer.py` — is_critical 필터 적용

**파일**: `src/services/confidence_scorer.py`

```python
# AS-IS (L104-113):
items = reason.knowledge_items
if items:
    resolved = [i for i in items if i.confidence >= 0.7]
    term_score = len(resolved) / len(items)

# TO-BE:
items = [ki for ki in reason.knowledge_items if ki.is_critical]
if items:
    resolved = [i for i in items if i.confidence >= 0.7]
    term_score = len(resolved) / len(items)
```

**이유**: 유사 SQL 추출 KI는 `is_critical=False`로 생성되므로, 분모에서 제외하여 점수 희석을 방지.
이 변경은 유사 SQL 평가 체계와 **독립적으로도 유효** — 기존에도 `_rule_use_cases()`가 생성하는
`use_case:*` KI(is_critical=False)가 분모를 키우고 있었음.

---

## 5. 비판적 검토

### 5.1 유사 SQL이 0건이면 아무 효과 없다

Qdrant에 시드 데이터가 없거나 새로운 유형의 질의면 `explored_use_cases`가 빈 리스트.

**대응**: 기존에도 `{reference_sqls}`가 `(없음)`이었으므로 동일.
평가 체계는 "있을 때 더 잘 활용"하는 것이지, 없을 때 손해가 생기는 구조가 아니다.

### 5.2 LLM hallucination — relevant_use_cases에 없는 SQL id 출력

LLM이 `{tool_results}`에 없는 sql_id를 출력하거나, 관련 없는 SQL을 관련 있다고 판정할 수 있음.

**대응**:
- `_annotate_use_case_relevance()`에서 sql_id 매칭 실패 → `_relevant=False` (보수적)
- sql_generator에서 `_relevant=True`인 것만 통과하므로, 없는 id가 들어와도 아무 효과 없음
- 프롬프트에서 `{tool_results}`의 use_case에 id를 명시적으로 포함하면 매칭률 향상

### 5.3 소형 모델(Solar Pro 2)에서 출력 스키마 준수 불안

새 스키마를 소형 모델이 제대로 출력하지 못할 수 있음.

**대응**:
- `relevant_use_cases`: `data.get("relevant_use_cases", [])` — 기본값 빈 리스트
  → 필드 누락 시 모든 use_case가 `_relevant=True`(미평가 기본값) → 기존 동작과 동일
- `selected/rejected`: 프롬프트에 새 스키마가 명시되어 있으므로 모델은 dict 형식으로 출력
  → JSON 파싱 실패 시에만 `_interpret_batch_fallback`(기존 rule-based)으로 전환
- **소형 모델에서 기능이 퇴화하더라도 기존보다 나빠지지 않는 구조**

### 5.4 selected/rejected reason의 활용처

테이블별 reason이 추가되면 다음에 활용 가능:
- **recovery_planner**: rejected 사유를 보고 replan 전략 수립 (현재는 불가)
- **트레이싱/디버깅**: 특정 테이블이 왜 빠졌는지 즉시 확인
- **사용자 설명**: "~는 ~이유로 제외했습니다" 자연어 응답 생성

### 5.5 배치 LLM 토큰 증가

`relevant_use_cases` + 테이블별 reason 추가로 응답 토큰이 늘어남.

**대응**:
- `comparison_reason`(기존 장문) 제거로 상쇄
- sql_generator에서 `{structural_hints}` 섹션 제거로 입력 토큰 감소
- 전체적으로 토큰 총량은 비슷하거나 감소

---

## 6. structural_hints 필드의 향후 처리

### 6.1 planner 내부에서는 유지

structural_hints는 planner에서 다음 용도로 사용된다:

- `_should_fast_path()`: source_tables 존재 여부로 Fast-Path 판정
- `_build_fallback_execution()`: source_tables로 search_table_meta 스텝 생성
- `_generate_hypotheses()`: 가설 수립 컨텍스트에 구조 요약 포함

이 용도는 모두 **planner 내부의 rule-based/계획 수립 로직**이므로 유지.

### 6.2 sql_generator에서는 제거

sql_generator 프롬프트의 `{structural_hints}` 섹션을 제거.
유사 SQL 원문은 관련 판정된 것만 `{reference_sqls}`에 포함.

### 6.3 state.py의 structural_hints 필드

필드 자체는 유지. planner가 사용하고 있으므로 삭제하면 안 됨.
다만 "sql_generator가 읽는 필드"에서 "planner 전용 필드"로 역할이 변경됨을 문서화.

---

## 7. 변경 파일 요약

| 파일 | 변경 내용 |
| --- | --- |
| `batch_interpret_system.txt` | 가이드라인 #6 추가, 출력 JSON 스키마 변경 (selected/rejected → dict 리스트, comparison_reason 제거, relevant_use_cases 추가), 예시 1·2 수정 |
| `context_explorer.py` | `BatchInterpretResult` 구조 변경 (selected/rejected → dict, comparison_reason 제거, relevant_use_cases 추가), `_parse_batch_result` 확장, `_annotate_use_case_relevance()` 신규 (Phase 4.5), selected/rejected 참조 코드 수정 |
| `sql_generator.py` | `{structural_hints}` 프롬프트 변수 제거, `{reference_sqls}` 필터링 (`_relevant` 기반) |
| `sql_generator_system.txt` | `## 유사 질의의 구조적 힌트` 섹션 제거, `## 참고 활용사례 SQL` 헤더 업데이트 |
| `confidence_scorer.py` | `calculate_readiness`에서 `is_critical` 필터 적용 (점수 희석 방지) |
| `state.py` | 변경 없음 — structural_hints 필드는 planner 전용으로 유지 |

---

## 8. 해소되는 기존 문제들

| 기존 문제 | 해소 방식 |
| --- | --- |
| R11 (structural_hints ↔ knowledge_items 불일치) | structural_hints가 sql_generator에 도달하지 않으므로 불일치 자체가 소멸 |
| 유사 SQL 미평가 | 배치 LLM이 문맥 기반으로 관련성 판정 → 관련 있는 것만 통과 |
| sql_generator 프롬프트 토큰 낭비 | {structural_hints} 제거 + 무관 SQL 제거 → 15~20% 절감 |
| 소형 모델의 교차 대조 부담 | 정보원이 3개(③⑤⑥)에서 2개(③⑥)로 감소, ⑥은 관련 판정분만 |
| confidence_scorer 점수 희석 | is_critical 필터로 비핵심 KI가 분모에서 제외 |
| selected/rejected 사유 추적 불가 | 테이블별 reason으로 개별 판정 사유 확인 가능 |

---

## 9. 실행 순서

1. **변경 6** (confidence_scorer) — 독립적, 즉시 적용 가능. 기존 `use_case:*` KI의 희석도 함께 해결.
2. **변경 1 + 2** (프롬프트 + BatchInterpretResult) — 프롬프트와 파서를 함께 변경.
3. **변경 3** (Phase 4.5 주석 부착) — 변경 2에 의존.
4. **변경 4 + 5** (sql_generator 필터링 + 프롬프트 정리) — 변경 3에 의존. structural_hints 프롬프트 제거.
5. **검증**: 골든셋 회귀 테스트 → 유사 SQL이 있는 케이스에서 relevant_use_cases 출력 확인, 테이블별 reason 품질 확인.
