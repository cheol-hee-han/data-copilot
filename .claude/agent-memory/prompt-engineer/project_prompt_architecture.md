---
name: 프롬프트 아키텍처 결정 사항
description: src/agents/nodes/prompts/system_prompts.py를 마스터 저장소로 사용하는 구조 및 각 노드의 import 방식
type: project
---

모든 LLM 프롬프트는 `src/agents/nodes/prompts/system_prompts.py` 한 곳에서 관리한다.
각 노드 파일은 해당 상수를 import하여 사용하며 로컬 프롬프트 상수를 정의하지 않는다.

**Why:** 프롬프트 튜닝 시 단일 파일만 수정하면 전체 파이프라인에 반영되도록 중앙화.

**How to apply:** 노드 파일에 프롬프트 문자열을 직접 작성하지 말 것.
새 프롬프트가 필요하면 system_prompts.py 에 상수로 추가하고 노드에서 import한다.

## 현재 상수 목록 (system_prompts.py)

| 상수명 | 사용 노드 | 설명 |
|--------|-----------|------|
| INTENT_CLASSIFICATION | intent_classifier.py | 의도 분류 — 두 줄 출력 형식 강제 |
| CLARIFICATION | clarifier.py | 명확화 질문 — 선택지 형태 |
| SQL_GENERATION_RULES | sql_generator.py | SQL 생성 — {validation_feedback_section} 포함 |
| SQL_VALIDATION_FEEDBACK_SECTION | sql_generator.py | SQL 재생성 시 오류 주입 템플릿 |
| RESULT_FORMATTING | formatter.py | 결과 포맷팅 — system prompt 로만 사용 |
| DATA_ANALYSIS | analyzer.py | 데이터 분석 — JSON 출력 강제 |

## 메시지 구조 패턴

- **intent_classifier, clarifier**: system=프롬프트, messages=[user: 입력]
- **sql_generator**: system=SQL_GENERATION_RULES(동적 포맷), messages=[user: 입력]
- **formatter, analyzer**: system=프롬프트(고정), messages=[user: 요청+데이터 템플릿]
  - 사용자 요청과 데이터를 system이 아닌 user 메시지로 분리 → 소형 LLM 역할 인지 향상
