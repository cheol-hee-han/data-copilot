# resources/prompts/

LLM 프롬프트 파일. 3계층 구조로 파이프라인 흐름에 대응한다.

```
interpret/  →  reason/  →  present/
해석하다       추론하다      표현하다
```

## 계층별 역할

### interpret/ — 질의 해석 (10개)

사용자의 자연어 입력을 이해하고 구조화하는 프롬프트.

| 파일 | 노드 | 역할 |
|------|------|------|
| `intent_gate.txt` | intent_classifier | 의도 분류 게이트 (정규화 활성 시) |
| `intent_gate_user.txt` | intent_classifier | 의도 분류 user 템플릿 |
| `intent_classification.txt` | intent_classifier | 의도 분류 (레거시 폴백) |
| `clarification.txt` | clarifier | 명확화 질문 생성 |
| `normalization_phase1.txt` | query_normalizer | 8-Slot 정규화 Phase 1 |
| `normalization_phase1_user.txt` | query_normalizer | Phase 1 user 템플릿 |
| `normalization_phase2.txt` | query_normalizer | 정규화 Phase 2 (보정) |
| `normalization_phase2_user.txt` | query_normalizer | Phase 2 user 템플릿 |
| `history_resolve.txt` | history_resolver | 대화 이력 해소 |
| `history_resolve_user.txt` | history_resolver | 이력 해소 user 템플릿 |

### reason/ — 추론 (6개)

에이전틱 코어에서 데이터를 탐색하고 SQL을 생성/검증하는 프롬프트.
JSON few-shot 예제를 포함하므로 **Python `.format()` 사용 불가** — `.replace()`로 치환.

| 파일 | 노드 | 역할 |
|------|------|------|
| `plan_system.txt` | planner | 가설 수립 + 실행계획 생성 |
| `explore_observe_system.txt` | context_explorer | 도구 결과 해석 + 지식 갱신 |
| `generate_sql_system.txt` | sql_generator | SQL 생성 (dialect별 문법 포함) |
| `generate_sql_fix_section.txt` | sql_generator | SQL 재생성 지침 (fix 시 주입) |
| `validate_layer2b_system.txt` | sql_validator | LLM 기반 의미 검증 (7개 체크리스트) |
| `replan_system.txt` | recovery_planner | 실패 분석 + 새 가설 수립 |

### present/ — 표현 (8개)

SQL 실행 결과를 분석·시각화·포맷팅하여 사용자에게 전달하는 프롬프트.

| 파일 | 노드/서비스 | 역할 |
|------|------------|------|
| `data_analysis.txt` | analyzer → data_analyzer | 인사이트 도출 system |
| `analysis_user.txt` | analyzer → data_analyzer | 분석 user 템플릿 |
| `visualization_judgment.txt` | analyzer → data_analyzer | 차트 유형 판단 system |
| `visualization_judgment_user.txt` | analyzer → data_analyzer | 판단 user 템플릿 |
| `visualization_svg.txt` | analyzer → data_analyzer | SVG 생성 system (규칙+few-shot) |
| `visualization_svg_user.txt` | analyzer → data_analyzer | SVG 생성 user 템플릿 |
| `result_formatting.txt` | formatter → response_formatter | 보고서 포맷팅 system |
| `formatting_user.txt` | formatter → response_formatter | 포맷팅 user 템플릿 |

## 프롬프트 수정 가이드

### 파일 네이밍 규칙
- `*_system.txt` 또는 단독 파일명 → LLM의 **system** 메시지로 사용
- `*_user.txt` → LLM의 **user** 메시지 템플릿 (`{변수}` 플레이스홀더 포함)
- `*_fix_*.txt` → 재시도/수정 시 주입되는 보조 프롬프트

### 변수 치환
- interpret/present 계층: Python `.format()` 사용 → `{variable_name}` 형식
- reason 계층: `.replace()` 사용 → `{variable_name}` 형식이지만 JSON `{}`와 충돌 방지

### 출력 형식 규칙
- JSON 출력을 기대하는 프롬프트: 마크다운 코드블록(```) 사용 금지
- "순수 JSON만 출력하세요" 지시를 출력 형식 섹션 상단에 배치
- few-shot 예제의 출력도 코드블록 없이 raw JSON으로 기술

### 소형 모델 대응
- few-shot 예제를 충분히 포함 (최소 3개, 정상+에러 케이스 모두)
- 출력 형식을 명시적이고 반복적으로 강조
- 복잡한 지시보다 단순한 규칙 + 많은 예제가 효과적

## 코드 연결

모든 프롬프트는 `src/agents/nodes/prompts/system_prompts.py`에서 로드된다:

```python
from src.agents.nodes.prompts.system_prompts import (
    REASON_PLAN_SYSTEM,      # reason/plan_system.txt
    VIZ_SVG_SYSTEM,          # present/visualization_svg.txt
    INTENT_GATE,             # interpret/intent_gate.txt
)
```

프롬프트 파일을 추가/삭제할 때는 반드시 `system_prompts.py`도 함께 수정한다.
