# TODO — 도구 인터페이스 통일 + LLM execution_plan 복원

> **관련**: D-3 (LLM execution_plan 복원)
> **선행**: 도구 화이트리스트 검증, input 정제 로직

---

## 1. 도구 함수 input type 통일

### 현재 상태

```python
# TOOL_MAP 등록 도구 — 전부 (str) -> list[dict]
search_use_cases(query: str)
search_table_meta(query: str)
search_code_meta(column_name: str)
search_manual(query: str)
search_glossary(term: str)

# 후처리 전용 — (str, str, str) → TOOL_MAP 미등록
get_sample_rows(table_name: str, schema_name: str, db_source: str)
```

### 해결 방안

**A안 (단순)**: `get_sample_rows`를 `(str) -> list[dict]`로 변경
- table_name에서 schema_name, db_source를 자동 추론
- TOOL_MAP에 바로 등록 가능
- 단점: CandidateTable에 이미 있는 schema_name을 활용 못함

**B안 (확장)**: `ExecutionStep.input`을 `str | dict`로 확장
- `execute_tool`에서 dict이면 `**kwargs`로 전달
- 모든 도구가 구조화 인자 가능
- 단점: ExecutionStep 모델 변경 + LLM JSON 출력에 dict 포함 필요

### 권고

A안으로 먼저 통일 후, 필요 시 B안으로 확장

---

## 2. LLM execution_plan 복원

### 현재 상태

- LLM이 execution_plan을 출력하지만 코드에서 **무시**
- `_build_execution_plan()`이 rule-based로 생성
- rule-based는 search_glossary, search_manual을 절대 포함하지 않음

### 복원 시 필요한 방어 로직

1. **도구 화이트리스트**: LLM이 TOOL_MAP에 없는 도구를 지정하면 스킵
2. **input 정제**: 지시문이 아닌 검색 키워드만 허용
3. **스텝 수 제한**: 최대 N개로 제한
4. **source_tables 우선**: rule-based 스텝과 LLM 스텝을 병합

### 구현 순서

1. 도구 input type 통일 (A안)
2. get_sample_rows TOOL_MAP 등록
3. _parse_plan_response()에서 execution_plan도 파싱
4. 도구 화이트리스트 + input 정제 적용
5. rule-based 스텝과 LLM 스텝 병합 로직
