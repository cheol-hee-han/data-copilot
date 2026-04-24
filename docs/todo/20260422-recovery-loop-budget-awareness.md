# recovery_agent 루프 예산·반복 인지 설계 (RC-D)

작성일: 2026-04-22
작성자: 한철희
상태: 설계 확정, 구현 대기

## 목차
- [배경](#배경) — 왜 필요한가 (V-04 5라운드 무한 replan 사례)
- [설계 원칙](#설계-원칙) — Qwen3.5 397B 에 맞춘 "원칙 기반, 알고리즘 기반 아님"
- [변경 범위](#변경-범위) — INPUT 5개 플레이스홀더 + RULES 4원칙, OUTPUT 무변경
- [프롬프트 배치 결정](#프롬프트-배치-결정) — 각 변경점의 섹션·위치 근거
- [드래프트 파일](#드래프트-파일) — 전체 드래프트 위치 및 주요 라인 맵
- [코드 변경](#코드-변경) — recovery_agent.py 에서 주입할 필드 5개 계산 방법
- [테스트 기준](#테스트-기준) — V-04, 예시 9 케이스 기대 동작
- [보류 항목](#보류-항목) — accept_hint / promoted_items (근본 원인 RC-B 해결 후 재평가)
- [관련 문서](#관련-문서)

## 배경

### 관찰된 문제 (V-04)

E2E `tests/reports/e2e_2026Q2/scenarios/V-04.md` — 질의 "고객별 월 거래 건수와 예금 잔액의 관계":

- normalize 가 이미 `measure=거래건수(COUNT(DISTINCT))`, `dimensions=고객/월` 힌트 제공
- 그러나 readiness_gate → recovery_agent → context_retriever 를 **5번 반복**
- 매 라운드마다 같은 TRX 계열 테이블 주변을 맴돌며 동일 후보 재발견
- 최종 `ask_user_count=1` 로 clarification_handler 로 빠짐, `sql_generator` 미진입

### 원인 분석 (LLM I/O 관점)

`recovery_agent` LLM 이 자기가 몇 번째 라운드인지 모른다. 프롬프트 `[CONTEXT]` 에 `replan_count`·`candidate_repeat_count` 가 노출되지 않아 LLM 이:

1. 매번 "신선한 1라운드" 인 것처럼 판단
2. 동일 방향 replan 을 반복 출력
3. 예산 소진 직전에도 수렴 판단 불가능

코드 레벨에서는 `MAX_REPLANS` 등 한도가 존재하지만, 이는 파이썬에서 **강제 종료** 용이지 LLM 이 **자발적으로 수렴**하는 데는 기여하지 않는다.

## 설계 원칙

Qwen3.5 397B 대상 설계는 "신뢰하되 원칙 제공". 알고리즘식 분기(`if replan_count > X: ...`)를 프롬프트에 강제하지 않고, **상태 노출 + 4원칙** 으로 LLM 자체 판단을 유도한다.

### 4원칙

1. **상류 추론 존중** — normalize 가 이미 확정한 항목은 재탐색 대상이 아님
2. **반복은 진전 아님** — `candidate_repeat_count ≥ 2` 이면 같은 방향 replan 금지
3. **묻기 전 자문** — ask_user 전에 "내가 힌트로 판단 가능한가" 자문
4. **예산 임박 시 수렴** — `replan_budget_left ≤ 1` 이면 replan 은 최후 보루

### 스코프 결정

- **INPUT (`[CONTEXT]`) 추가**: 5개 state 플레이스홀더
- **RULES 추가**: 4원칙
- **OUTPUT 무변경**: `key_observations`·`decision_reasoning` 추가 검토했으나 기존 `analysis`·`reasoning_summary` 와 역할 중복 → 드롭
- **보류**: `accept_hint` 액션 / `promoted_items` — 근본 원인은 RC-B (normalize 힌트가 interpreter 에 전달 안 됨). RC-B 수정 후 V-04 가 round 1 에서 GENERATE 로 가는지 재측정 후 필요성 재판단

## 변경 범위

| 영역 | 변경량 | 성격 |
|---|---|---|
| `[CONTEXT]` `## 탐색 루프 상태` 신규 섹션 | +15줄 | 추가 |
| `[RULES]` `## 진단 원칙` 끝 "상류 추론 존중" | +3줄 | 기존 확장 |
| `[RULES]` `## 루프 수렴 원칙` 신규 subsection | +9줄 | 추가 |
| `[HALLUCINATION_GUARD]` 위반 9 (루프 무시), 10 (상류 재탐색) | +10줄 | 추가 |
| `[EXAMPLES]` 기존 예시 1~8 상단에 "탐색 루프 상태:" 1줄 | +8줄 | 기존 확장 |
| `[EXAMPLES]` 예시 4, 7 에 `candidate_repeat_count=2` 반영 | 1~2줄 수정 | 기존 수정 |
| `[EXAMPLES]` 예시 8 에 `budget_left=0` 반영 | 1줄 수정 | 기존 수정 |
| `[EXAMPLES]` 예시 9 신설 (V-04 형) | +27줄 | 추가 |
| `[TASK]` 루프 상태 확인 단계 삽입 | +1줄 | 확장 |
| **프롬프트 합계** | **≈ +75줄 (559 → 635, +14%)** | |
| `recovery_agent.py` `_build_prompt` 에 5개 변수 주입 | +20줄 | 확장 |

## 프롬프트 배치 결정

각 변경점의 섹션·위치 선택 근거.

### (A) INPUT 5개 state → `[CONTEXT]` `## 진입 경로 및 실패 원인` 뒤

`## 진입 경로 → ## 탐색 루프 상태 → ## unresolved_items` 순.

**근거**: 진입(왜 여기 왔나) → 루프 상태(몇 번째, 반복 중인가) → 해소 과제(뭐가 남았나) 순으로 진단 흐름이 자연스러움. 맨 뒤(handoff_note 이후)에 붙이면 primacy effect 로 LLM 이 상위 판단을 이미 굳힌 뒤 루프 상태를 보게 되어 늦음.

### (B) RULES 4원칙 → 둘로 분리

| 원칙 | 배치 | 근거 |
|---|---|---|
| 1. 상류 추론 존중 | `## 진단 원칙` 끝 | 실패 원인 해석 국면에 해당 |
| 2. 반복은 진전 아님 | `## 루프 수렴 원칙` 신규 | 루프 제어 판단 |
| 3. 묻기 전 자문 | `## 루프 수렴 원칙` | ask_user 가드 |
| 4. 예산 임박 시 수렴 | `## 루프 수렴 원칙` | 수렴 트리거 |

"어떻게 진단하는가" (원칙 1) 와 "언제 멈추는가" (원칙 2~4) 는 성격이 다르므로 별도 subsection 에 두어 LLM 이 각 국면에서 해당 원칙만 참조 가능하게 한다.

### (C) HALLUCINATION_GUARD 위반 9, 10

- 위반 9 (루프 무시) — 위반 6 ("탐색 여지 남아있는데 ask_user/give_up") 의 **반대 위반** 대칭 가드
- 위반 10 (상류 재탐색) — 원칙 1 "상류 추론 존중" 의 구체화

### (D) OUTPUT_CONTRACT — 변경 없음

`key_observations`, `decision_reasoning` 을 검토했으나:
- `analysis` 가 이미 "진입 경로 명시 + 실패 원인 분석 1~3줄" 수행
- `reasoning_summary` 가 이미 "최종 출력 도출 근거" 수행
- 중복 필드는 LLM 혼선 유발

`reasoning_summary` 설명문만 "전체 출력 산출 근거 (action 결정 과정에 국한되지 않음)" 로 명시 보강.

### (E) EXAMPLES 보강

- 기존 예시 8건 모두 유지, 상단에 "탐색 루프 상태:" 1줄 추가 → 매 예시에서 state 인지·활용을 자연스럽게 시연
- 예시 4, 7 — `candidate_repeat_count` 활용 (반복 감지 수렴 사례)
- 예시 8 — `budget_left=0` 활용 (예산 소진 사례)
- 예시 9 신설 — V-04 원형 (예산 임박 + 상류 추론 존중 + 반복 감지 동시 발현)

## 드래프트 파일

전체 드래프트: [resources/prompts/reason/recovery_agent_system.txt.draft_rcd](../../resources/prompts/reason/recovery_agent_system.txt.draft_rcd) (635줄)

주요 변경 라인 맵:

| 섹션 | 라인 | 내용 |
|---|---|---|
| `[RULES]` 상류 추론 존중 | L32-L34 | `## 진단 원칙` 끝 추가 |
| `[RULES]` 루프 수렴 원칙 | L74-L82 | `## 탐색 한계 판단` 다음 신규 subsection |
| `[HALLUCINATION_GUARD]` 위반 9 | L181-L184 | 루프 상태 무시 replan |
| `[HALLUCINATION_GUARD]` 위반 10 | L186-L190 | normalize 힌트 재탐색 |
| `[EXAMPLES]` 예시 4 수정 | L272-L295 | repeat=2 반영 |
| `[EXAMPLES]` 예시 8 수정 | L391-L408 | budget_left=0 반영 |
| `[EXAMPLES]` 예시 9 신설 | L412-L438 | V-04 형 케이스 |
| `[CONTEXT]` 탐색 루프 상태 | L496-L510 | 신규 subsection, 5 placeholder |
| `[TASK]` 루프 상태 확인 | L617 | 3번 단계 삽입 |

## 코드 변경

[src/agents/nodes/reason/recovery_agent.py](../../src/agents/nodes/reason/recovery_agent.py) `_build_prompt` (또는 `_build_recovery_plan`) 에서 5개 변수 주입.

### 주입 필드

```python
replan_count: int = reason.loop_guard.replan_count
max_replans: int = MAX_REPLANS  # state.py 상수
replan_budget_left: int = max(0, max_replans - replan_count)
last_action: str = _get_last_action(reason)  # dead_ends 또는 별도 트래커에서
candidate_repeat_count: int = _count_candidate_repeats(reason)
```

### `_count_candidate_repeats` 계산 방법

`reason.knowledge_items` 중 status=CANDIDATE 인 항목이 **직전 N 라운드에 걸쳐 같은 `ki.value` 후보로 재등장한 횟수**. 다음 정보원으로 계산:

- `reason.execution_plan` 의 라운드별 purpose·input 에서 같은 후보가 반복 대상이었는지
- 또는 `reason.dead_ends` 에서 같은 후보 KI 에 대한 실패 기록 개수

초기 구현은 단순하게: CANDIDATE KI 중 `source` 에 "라운드 N, N+1 … 동일 후보" 패턴이 2회 이상인 것의 개수.

### `last_action` 계산 방법

ReasoningState 에 `last_recovery_action: str` 필드 추가하거나, `reason.dead_ends[-1].action` 을 간접 소스로 사용.

## 테스트 기준

### 골든셋 V-04 재측정

- **전제**: RC-B 수정(normalize 힌트 보존)이 먼저 완료된 상태
- **기대**: RC-B 만으로 round 1 GENERATE 성공 → recovery_agent 미진입
- RC-B 만으로 해소되지 않는 경우에만 본 설계의 효과 측정

### 단위 시나리오 (신규)

예시 9 를 실제 테스트로 구현:
- normalize 에서 `agg_function` 확정된 measure 에 대해 recovery 가 재탐색 시도 → 원칙 1 위반 탐지
- replan 4회째에 동일 후보 2회 반복 → ask_user 로 수렴하는지 확인

### 회귀 테스트

기존 recovery_agent 골든셋 8건 (예시 1~8 형) 모두 동작 유지 확인.

## 보류 항목

### accept_hint 액션 + promoted_items

V-04 원인은 "normalize 힌트를 interpreter 가 못 받음" (RC-B). RC-B 수정 후에도 루프가 도는 케이스가 남으면 추가.

결정 순서:
1. RC-B 구현 (`reasoning_preparer` + 3개 프롬프트 직렬화 확장)
2. V-04/N-08/D-01/S-04 재측정
3. 여전히 무한 루프 케이스 있으면 → accept_hint 설계 재개

### OUTPUT schema CoT 필드 (`key_observations`, `decision_reasoning`)

기존 `analysis`·`reasoning_summary` 와 역할 중복. 현 설계로 효과 불충분하면 재검토.

## 관련 문서

- [20260412-pipeline-failure-root-causes.md](20260412-pipeline-failure-root-causes.md) — RC-A/B/C/D 원인 프레임워크
- [20260408-recovery-context-enrichment.md](20260408-recovery-context-enrichment.md) — recovery_agent context 확장 선행 설계
- [20260416-recovery-clarification-design.md](20260416-recovery-clarification-design.md) — recovery ask_user 재진입 설계
- E2E prescan: `tests/reports/e2e_2026Q2/prescan_summary_20260421_0944.md`
- V-04 시나리오: `tests/reports/e2e_2026Q2/scenarios/V-04.md`
