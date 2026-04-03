# output_template_registry 제거 변경사항 정합성 검토

**일시**: 2026-04-03  
**검토 대상**: output_template_registry 제거 및 LLM 자유 추론 전환 (4개 파일)  
**검토 유형**: 변경 정합성 / 누락 참조 / 데이터 흐름 검증

---

## 검토 결과 요약

| 등급 | 건수 | 요약 |
|------|------|------|
| Critical | 1 | output_hint가 query_decomposition에 포함되지 않아 sql_generator에 전달 불가 |
| Warning | 2 | 죽은 코드 잔존, docstring 미갱신 |
| Info | 1 | 정상 확인된 사항 |

---

## Critical (1건)

### C-01: output_hint가 query_decomposition에 누락 -- sql_generator에 빈 값 전달

**파일**: `src/agents/nodes/reason/reasoning_preparer.py` (147-153행)  
**영향**: sql_generator_system.txt의 `{output_hint}` 플레이스홀더가 항상 `{}` 로 치환됨

**문제 상세**:

변경 의도는 LLM이 추론한 `expected_columns`를 sql_generator에 `output_hint`로 전달하는 것이다. 실제 데이터 흐름은 다음과 같다:

1. `query_normalizer.py` -- NormalizedQuery.output_hint에 LLM 추론 결과 저장 (정상)
2. `reasoning_preparer.py` -- `_build_decomposition_from_normalized()` 에서 query_decomposition dict 구성 (문제 지점)
3. `sql_generator.py` -- `serialize_decomp_slots(decomp)` 로 `{output_hint}` 치환

2단계에서 `_build_decomposition_from_normalized()` 함수가 반환하는 dict에는 `measures`, `filters`, `group_by`, `order_limit`, `required_concepts`만 포함되어 있고 **`output_hint` 키가 없다**.

`serialize_decomp_slots()` (prompt.py 40-42행)은 `decomp.get("output_hint", {})`로 접근하므로 에러는 발생하지 않지만, 항상 빈 dict `{}`가 반환되어 sql_generator_system.txt 규칙 10의 "output_hint의 expected_columns가 있으면 이를 참고" 조건이 절대 활성화되지 않는다.

**수정 방안**:

`reasoning_preparer.py`의 `_build_decomposition_from_normalized()` 에서 output_hint를 포함해야 한다:

```python
# 147행 부근의 return dict에 추가
return {
    "measures": measures,
    "filters": filters,
    "group_by": group_by,
    "order_limit": order_limit,
    "required_concepts": required_concepts,
    "output_hint": {                                    # 추가
        "format": nq.output_hint.format,
        "doc_type": nq.output_hint.doc_type,
        "expected_columns": nq.output_hint.expected_columns,
    },
}
```

---

## Warning (2건)

### W-01: prompt.py에 serialize_template_registry 함수가 죽은 코드로 잔존

**파일**: `src/utils/llm/prompt.py` (88-110행)  
**영향**: 사용처 없는 함수 및 관련 docstring이 남아 있어 혼동 유발

query_normalizer.py에서 import와 호출이 모두 제거된 것은 확인되었다. 그러나 prompt.py에 `serialize_template_registry()` 함수 자체(88-110행)와 모듈 docstring(12행)에 해당 함수 설명이 그대로 남아 있다.

프로젝트 전체 검색(`src/`, `resources/`) 결과 이 함수를 호출하는 곳이 없다.

**수정 방안**:
- `serialize_template_registry` 함수 삭제 (88-110행)
- 모듈 docstring 12행의 `- serialize_template_registry: 출력 템플릿 dict -> 프롬프트 주입 텍스트` 제거

### W-02: query_normalizer.py의 system 프롬프트에 미치환 플레이스홀더 없는지 확인

**파일**: `src/services/query_normalizer.py` (599행)

`p1_system = phase1_system`으로 system 프롬프트를 직접 할당하고 있으며, `render_prompt`를 통한 치환 없이 그대로 LLM에 전달된다. 프롬프트 파일(query_normalizer_phase1_system.txt) 전문을 검토한 결과 `{...}` 형태의 플레이스홀더가 존재하지 않으므로 현재는 **문제 없음**이다.

다만 이 구조는 향후 system 프롬프트에 플레이스홀더가 추가될 경우 치환 누락 사고 가능성이 있다. user 프롬프트는 `render_prompt`를 거치지만 system 프롬프트는 거치지 않는 비대칭 처리임을 인지해 두어야 한다.

---

## Info (1건)

### I-01: 정상 확인된 항목

다음 사항은 문제 없음이 확인되었다:

| 확인 항목 | 결과 |
|-----------|------|
| `{output_template_text}` 플레이스홀더가 시스템 프롬프트에서 완전 제거 | 정상 -- query_normalizer_phase1_system.txt에 해당 플레이스홀더 없음 |
| query_normalizer.py에서 serialize_template_registry import 제거 | 정상 -- import 목록에 없음 |
| query_normalizer.py에서 OUTPUT_TEMPLATE_REGISTRY 로드/치환 제거 | 정상 -- load_yaml 호출이 synonyms 1건만 남아있음 |
| `_post_output_hint_merge()` 함수 정상 동작 | 정상 -- expected_columns를 meta_search에 병합하는 로직 유지됨 |
| `serialize_decomp_slots`에서 output_hint dict 처리 | 정상 -- `json.dumps`는 dict/list 모두 처리 가능. 빈 dict `{}`도 유효한 JSON 문자열로 직렬화됨 |
| sql_generator_system.txt의 `{output_hint}` 플레이스홀더 치환 경로 | 정상 -- `serialize_decomp_slots()` -> `render_prompt()` 경로로 치환됨 (단, C-01로 인해 실제 값은 빈 dict) |
| sql_generator_system.txt 규칙 10 추가 | 정상 -- 문법 및 맥락 적절. expected_columns 참고 + 실제 테이블 컬럼 확인 조건 명시 |
| resources/ 디렉토리에 output_template 잔여 참조 | 정상 -- 없음 |

---

## 조치 우선순위

1. **[즉시]** C-01: `_build_decomposition_from_normalized()`에 output_hint 추가 -- 이 변경이 없으면 전체 기능이 의도대로 동작하지 않음
2. **[권장]** W-01: prompt.py의 죽은 코드 정리
