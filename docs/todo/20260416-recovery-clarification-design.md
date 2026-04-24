# Recovery Agent 명확화 설계 — 탐색 실패 시 사용자 질문

작성일: 2026-04-16
최종 수정: 2026-04-17

## 배경

recovery_agent가 특정 항목에 대해 반복 탐색해도 해소하지 못할 때,
사용자에게 명확화 질문을 던져 업무 지식으로 돌파하는 기능.

기존 명확화 인프라(AmbiguitySignal → clarification_handler → interrupt/resume)는
이미 구축되어 있으나, reason 계층 내부에 구조적 문제가 있어 먼저 정리한 후 구현한다.


## 설계 원칙

1. **탐색 한계 도달 시 순차 판단** — 탐색으로 해소 안 되면 "사용자가 도울 수 있는가?"를 판단하여 ask_user 또는 give_up 분기 (연구 근거: Decomposed Prompting, ToT, Abstain-QA)
2. **ask_user vs give_up의 본질적 차이**:
   - ask_user = 데이터를 찾기는 해봤으나, 사용자의 업무 판단이 있어야 특정할 수 있는 문제
   - give_up = 정보계 DB에 데이터가 없거나, 사용자에게 물어봐도 답이 나올 수 없는 문제
3. **2회 실패 트리거** — 동일 항목에 대해 2회 이상 탐색 실패하면 ask_user/give_up 검토. 횟수 카운팅은 Python이 수행하여 프롬프트에 주입
4. **ask_user 라운드트립 = replan 0회 소비** — 명확화 재진입 시 `increment_replan()`을 수행하지 않는다
5. **ask_user 횟수 제한** — `LoopGuard.ask_user_count`로 카운팅하며, `MAX_ASK_USER_ROUNDS`(기본 2)에 도달하면 ask_user 대신 give_up으로 전환한다. replan 예산과 독립된 별도 안전망
6. **기존 명확화 인프라 재사용** — AmbiguitySignal → pending_signals → clarification_handler → interrupt/resume → resolved_signals
7. **max_replans는 최후 안전망** — LLM이 replan을 반복해도 Python이 강제 종료


## 선행 작업 (완료)

### 1단계: readiness_gate/result_finalizer 명확화 제거 ✅

CONFLICTED 항목이 ASK_USER로 빠지지 않고 자연스럽게 REPLAN → recovery_agent로 흘러가도록 정리.

제거 항목:
- `ReadinessVerdict.ASK_USER`, `Phase.VERIFYING`, `should_ask_user()`, `_is_unresolvable_conflict()`
- result_finalizer의 VERIFYING 분기, `_build_conflicted_signals()`
- pipeline.py의 `Phase.VERIFYING: "ask_user"` 라우팅
- `_VALID_RETURN_TARGETS`에서 sql_generator, readiness_gate 제거

검증: src/, tests/ 전체에서 ASK_USER, VERIFYING, should_ask_user, _build_conflicted_signals 참조 제거 확인 완료.

### 2단계: CONFLICTED 자연 흡수 확인 ✅

CONFLICTED → all_critical_confirmed=false → 점수 미달 → REPLAN → recovery_agent. 별도 코드 변경 없이 자연 흡수.


---

## 3단계: recovery_agent ask_user 구현

### 의사결정 구조

```
recovery_agent (LLM)
  │
  ├─ 탐색 여지 있음 → replan (새 가설 + 탐색 계획)
  │
  └─ 탐색 한계 도달 (동일 항목 2회+ 실패, 방법 소진 등)
       │
       ├─ 사용자가 도울 수 있음 → ask_user
       │   (뭘 원하는지 몰라서 못 찾음: 범위, 기준, 산출식)
       │
       └─ 사용자가 도울 수 없음 → give_up
           (찾을 게 없어서 못 찾음: 데이터 부재, SQL 불가)
```

### ask_user 두 케이스

| 케이스 | 상황 | question_type | 예시 |
|--------|------|---------------|------|
| **A. 후보 복수** | 유효 후보 2개 이상, 선택이 업무 판단에 의존 | single_select | "고객 범위: 개인/기업/전체?" |
| **B. 지식 부재** | 매뉴얼·이력 전부 탐색해도 산출식·업무조건 미확인 | free_text | "BIS 비율 산출 방식을 알고 계시나요?" |

### 전체 흐름

```
recovery_agent (LLM)
  → action="ask_user" + clarification 오브젝트 생성
  → Python: AmbiguitySignal(source_node="recovery_agent") 생성 → pending_signals
  → _route_after_recovery_agent: pending_signals 감지 → clarification_handler
  → clarification_handler: interrupt → 사용자 응답 수신
  → resolved_signals에 answer 기록
  → _route_after_clarify: source_node="recovery_agent" → recovery_agent 재진입
  → recovery_agent: {clarification_history}에서 답변 읽음
    → 유용한 답변 → replan (새 가설 수립)
    → "모르겠다" → replan 계속 또는 give_up → 결국 max_replans 종료
```


### 변경 1: 프롬프트 — resources/prompts/reason/recovery_agent_system.txt

#### [RULES] 변경: give_up 원칙 → 탐색 한계 판단으로 교체

기존 `## give_up 원칙` 전체를 아래로 교체:

```
## 탐색 한계 판단

탐색으로 해소되지 않으면 ask_user 또는 give_up을 선택한다.

- ask_user: 데이터를 찾기는 해봤으나, 사용자의 업무 판단이 있어야 특정할 수 있는 문제
  - 대상 범위가 불명 (개인/기업, 전체/부분 등)
  - 산출 기준이 불명 (금액/건수, 영업일/역일 등)
  - 업무 조건이 매뉴얼에 없어 실무자만 아는 경우
- give_up: 정보계 DB에 데이터가 없거나, 사용자에게 물어봐도 답이 나올 수 없는 문제
  - 테이블 자체가 정보계에 존재하지 않는다는 강한 근거가 있는 경우
  - SQL로 표현 불가능한 질의

탐색 한계의 근거:
- "항목별 탐색 시도 현황"에서 "탐색 한계" 표시된 항목을 우선 참고한다
- 표시가 없더라도, 실패 경로(dead_ends)와 전체 탐색 이력을 종합하여
  추가 탐색으로 해소할 수 없다고 판단되면 ask_user 또는 give_up을 선택할 수 있다

탐색 여지가 남아 있으면 반드시 replan을 선택한다.

- ask_user 시: execution_plan은 빈 배열([]), new_hypothesis는 null, clarification은 필수
- give_up 시: execution_plan은 빈 배열([]), new_hypothesis는 null, clarification은 null
```

#### [RULES] 변경: 가설 원칙 수정

기존:
```
- replan 시 new_hypothesis는 필수, give_up 시 null
```

변경:
```
- replan 시 new_hypothesis는 필수
- ask_user 또는 give_up 시 null
```

#### [HALLUCINATION_GUARD] 추가: 위반 케이스 3건

기존 위반 5 뒤에 추가:

```
### 위반 6 — 탐색 여지가 남아 있는데 ask_user/give_up 선택
- 입력 상황: 아직 시도하지 않은 검색어·도구·페이지가 있음
- 잘못된 출력: "action": "ask_user" 또는 "action": "give_up"
- 올바른 출력: replan으로 남은 탐색 방법 시도

### 위반 7 — IT 용어가 포함된 명확화 질문
- 잘못된 출력: "question": "TB_PERS_CUST 또는 TB_CORP_CUST 중 어느 테이블을 사용할까요?"
- 올바른 출력: "question": "고객 수를 조회할 때, 어떤 고객을 대상으로 하시나요?"

### 위반 8 — ask_user인데 execution_plan을 채움
- 잘못된 출력: "action": "ask_user", "execution_plan": [{"tool": "search_table_meta", ...}]
- 올바른 출력: "action": "ask_user", "execution_plan": []
```

#### [OUTPUT_CONTRACT] 변경

기존 전체를 아래로 교체:

```
[OUTPUT_CONTRACT]

반드시 아래 구조의 JSON 객체를 출력한다.

{
  "analysis": string,
  "failure_type": string,
  "lessons_learned": string,
  "action": "replan" | "ask_user" | "give_up",
  "execution_plan": array,
  "new_hypothesis": object | null,
  "clarification": object | null,
  "reasoning_summary": string
}

### 필드 설명

- analysis: 진입 경로 명시 + 실패 원인 분석 (1~3줄)
- failure_type: 실패 유형 라벨 (코드값 불명 / 코드 명칭 미동반 / 산출식 불명 / 테이블 부재 / 컬럼 부재 / 포맷 불일치 / 필터값 불명 / 데이터 부재 중 하나)
- lessons_learned: 이번 실패에서 얻은 교훈 (1줄)
- action: "replan" | "ask_user" | "give_up"
- execution_plan: array
  - action이 replan이면 1~3개 스텝
  - action이 ask_user 또는 give_up이면 빈 배열([])
  - 각 스텝: {"tool": string, "input": string, "purpose": string, "depends_on": number | null}
- new_hypothesis: object 또는 null
  - action이 replan이면 필수
  - action이 ask_user 또는 give_up이면 null
  - 구조: {"hypothesis_id": string, "description": string, "strategy": string}
  - hypothesis_id는 시스템이 자동 채번하므로 임의의 값("H2" 등)으로 출력해도 됨
- clarification: object 또는 null
  - action이 ask_user이면 필수
  - action이 replan 또는 give_up이면 null
  - 구조: {"type": "single_select" | "free_text", "question": string, "options": array | null}
  - question: 업무 담당자가 이해할 수 있는 용어로 작성. 테이블명·컬럼명·코드값 포함 금지
  - options: type이 single_select이면 탐색에서 확인된 후보 나열. free_text이면 null
- reasoning_summary: 최종 출력 도출의 핵심 근거 1~3줄

### 상호 검증 규칙

정방향 (필드 → action):
- clarification이 있으면 action은 반드시 ask_user
- execution_plan에 1개 이상 스텝이 있으면 action은 반드시 replan
- new_hypothesis가 있으면 action은 반드시 replan

역방향 (action → 필드):
- action이 ask_user이면 clarification은 반드시 존재
- action이 replan이면 execution_plan에 1개 이상 스텝 필수
- action이 give_up이면 clarification은 null, execution_plan은 빈 배열

### 형식 고정 (JSON 안정성)

출력은 JSON 객체 하나로만 구성한다.
- 출력의 첫 문자는 반드시 { 이어야 한다.
- 마크다운 코드 펜스(```json, ```) 를 포함하지 않는다.
- JSON 이전이나 이후에 어떤 설명 텍스트도 쓰지 않는다.
- 모든 문자열은 큰따옴표만 사용한다.
```

#### [EXAMPLES] 변경

기존 예시 4 (give_up)를 ask_user 케이스 A로 교체. 기존 예시 6 (코드 명칭 미동반)은 유지. 예시 7로 케이스 B 추가. 예시 8로 give_up 재배치:

최종 예시 번호 배치:

- 예시 1~3: 기존 유지 (replan)
- 예시 4: ask_user single_select (신규, 기존 give_up 교체)
- 예시 5: 기존 유지 (금융 산출식 replan)
- 예시 6: 기존 유지 (코드 명칭 미동반 replan)
- 예시 7: ask_user free_text (신규)
- 예시 8: give_up (기존 예시 4를 재배치)

```
## 예시 4: 대상 범위 불명 → 사용자 질문 (ask_user, single_select)

상황: "고객 수 알려줘"에서 '고객'의 대상 범위를 2회 탐색했으나 특정 불가.
- 실패 기록: dead_end 2건 — 동일 유형(필터값 불명) 반복
  - H1: search_table_meta("고객") → 개인고객(TB_PERS_CUST), 기업고객(TB_CORP_CUST) 둘 다 발견
  - H2: search_use_cases("고객 수를 집계하여 조회한다, page=1") → 개인 2건, 기업 1건, 전체 1건 — 편중 판단 불가
- 탐색 한계 항목: scope:고객범위 (2회 실패, 탐색 한계)

{
  "analysis": "필터값 불명 2회 반복. 개인(TB_PERS_CUST), 기업(TB_CORP_CUST) 모두 유효한 후보이나 탐색으로 특정 불가. 과거 SQL도 편향 없음. 사용자의 업무 판단이 필요한 문제.",
  "failure_type": "필터값 불명",
  "lessons_learned": "고객 범위는 업무 목적에 따라 결정됨. 메타·이력 어디에도 기본값 없음.",
  "action": "ask_user",
  "execution_plan": [],
  "new_hypothesis": null,
  "clarification": {
    "type": "single_select",
    "question": "고객 수를 조회할 때, 어떤 고객을 대상으로 하시나요?",
    "options": ["개인고객", "기업고객", "전체 (개인+기업)"]
  },
  "reasoning_summary": "scope:고객범위가 탐색 한계. 3개 후보가 탐색에서 확인되어 single_select로 질문."
}

---

## 예시 5: 금융 산출식 불명 (readiness_gate 진입)

... (기존 예시 5 유지)

---

## 예시 6: 코드 명칭 미동반 (sql_validator 진입)

... (기존 예시 6 유지)

---

## 예시 7: 산출식 미확인 → 사용자 질문 (ask_user, free_text)

상황: "부서별 BIS 비율"을 구해야 하는데 산출식이 확인 불가.
- 실패 기록: dead_end 2건 — 동일 유형(산출식 불명) 반복
  - H1: search_manual("BIS 비율 산출식, page=1") → BIS 관련 문서 0건
  - H2: search_use_cases("BIS 비율을 자기자본과 위험가중자산으로 산출하여 조회한다, page=1") → 유사 SQL 0건
- 탐색 한계 항목: measure:BIS비율 (2회 실패, 탐색 한계)

{
  "analysis": "산출식 불명 2회 반복. 매뉴얼과 과거 SQL 모두에서 BIS 비율 산출식 미확인. 테이블·컬럼은 존재하므로 데이터 부재(give_up)가 아님. 산출 기준은 업무 관행이므로 사용자가 알고 있을 가능성이 높음 → ask_user.",
  "failure_type": "산출식 불명",
  "lessons_learned": "BIS 비율은 매뉴얼에 미등록. 실무 관행으로만 전승되는 산출식일 수 있다.",
  "action": "ask_user",
  "execution_plan": [],
  "new_hypothesis": null,
  "clarification": {
    "type": "free_text",
    "question": "BIS 비율을 어떤 방식으로 계산하시나요? 예를 들어 자기자본÷위험가중자산 같은 산출 기준이 있으시면 알려주세요.",
    "options": null
  },
  "reasoning_summary": "measure:BIS비율이 탐색 한계. 매뉴얼·SQL이력 모두 미확인이므로 free_text로 산출 기준 질문."
}

---

## 예시 8: 데이터 부재 확인 → 종료 (give_up)

상황: "특수금융상품 수익률 추이"를 3회 재시도했으나 관련 테이블 미확인.
- 실패 기록: dead_end 3건 — 검색어 변경·도구 변경·페이지 증가·주제 범위 확장 모두 소진
  - "특수금융상품 수익률", "펀드 수익률 성과", "투자상품 성과 지표" 시도 완료
  - search_table_meta, search_use_cases, search_manual 모두 시도 완료
  - page 1~3 탐색 완료, "투자상품", "펀드", "수익관리" 주제영역 확장 완료
- 탐색 한계 항목: table:수익률테이블 (3회 실패, 탐색 한계)

{
  "analysis": "4가지 변경 방법 모두 소진. '특수금융상품 수익률' 데이터가 정보계 DB에 적재되지 않았을 가능성 강함. 사용자에게 물어봐도 답이 나올 수 없는 문제.",
  "failure_type": "데이터 부재",
  "lessons_learned": "모든 업무 데이터가 정보계에 적재되는 것은 아니다. 원천 시스템 경유가 필요한 데이터는 recovery 범위를 벗어난다.",
  "action": "give_up",
  "execution_plan": [],
  "new_hypothesis": null,
  "clarification": null,
  "reasoning_summary": "4가지 변경 방법 모두 소진 + 테이블 부재 3회 반복. 정보계 미적재 가능성 높아 give_up."
}
```

#### [CONTEXT] 추가: 2개 섹션

기존 `## 아직 확인되지 않은 정보 (unresolved_items)` 뒤에 추가:

```
## 항목별 탐색 시도 현황

{ask_user_eligible_items}

"탐색 한계" 표시된 항목은 ask_user 또는 give_up의 우선 검토 대상이다.
표시가 없더라도 실패 경로를 종합하여 탐색 한계를 판단할 수 있다.
```

기존 `## 샘플 데이터 현황` 뒤에 추가:

```
## 사용자 명확화 응답

{clarification_history}

위 응답이 있으면:
- 사용자 응답을 해당 항목의 확정 근거로 활용하여 replan을 수립한다
- new_hypothesis의 description에 사용자 응답을 직접 인용한다
- 단, 사용자가 "모르겠다"고 답한 경우 해당 항목은 해소 불가로 간주하고
  남은 탐색 여지에 따라 replan 또는 give_up을 판단한다
```

#### [TASK] 변경

기존 스텝 5, 6을 아래로 교체:

```
5. 탐색 한계를 판단한다:
   - "항목별 탐색 시도 현황"의 "탐색 한계" 표시를 참고한다
   - 실패 경로(dead_ends)와 탐색 이력을 종합하여 추가 탐색 여지가 있는지 판단한다
6. 탐색 여지가 있으면 replan. 없으면:
   - 사용자의 업무 판단으로 해소 가능 → ask_user (clarification 작성)
   - 사용자에게 물어봐도 답이 나올 수 없는 문제 → give_up
7. reasoning_summary에 최종 출력 도출의 핵심 근거를 1~3줄로 요약한다.
```


### 변경 1.5: LoopGuard 확장 — src/agents/state/state.py + src/agents/config.py

`LoopGuard`에 `ask_user_count` 카운터 추가:

```python
class LoopGuard(BaseModel):
    total_tool_calls: int = 0
    replan_count: int = 0
    generate_attempts: int = 0
    local_fix_count: int = 0
    ask_user_count: int = 0            # 추가
```

`config.py`에 상한 상수 추가:

```python
MAX_ASK_USER_ROUNDS: int = 2
```

ask_user 루프는 replan 예산(`max_replans`)과 독립적으로 제한된다.
`MAX_ASK_USER_ROUNDS` 도달 시 ask_user 대신 give_up으로 전환하여 무한 루프를 방지한다.


### 변경 2: Python — src/agents/nodes/reason/recovery_agent.py

#### (a) ClarificationRequest 모델 추가, RecoveryPlan 확장

```python
class ClarificationRequest(BaseModel):
    """ask_user 시 사용자에게 보내는 명확화 요청."""
    type: str = "free_text"              # "single_select" | "free_text"
    question: str = "추가 정보가 필요합니다."
    options: list[str] | None = None     # single_select일 때만

class RecoveryPlan(BaseModel):
    action: str = "give_up"              # 기본값 유지 (파싱 실패 시 안전)
    lessons_learned: str = ""
    execution_plan: list[ExecutionStep] = Field(default_factory=list)
    new_hypothesis: Hypothesis | None = None
    clarification: ClarificationRequest | None = None  # ask_user 시 필수
```

#### (b) _parse_plan_response 전면 재설계 — action별 분기 구조

현재 함수는 "일단 다 파싱하고 마지막에 분기"하는 2-action 전제의 설계.
3-action에서는 각 action이 필요한 필드만 파싱하도록 "분기 → 각자 파싱" 구조로 변경.

기존 함수 전체(lines 478-540)를 아래로 교체:

```python
def _parse_plan_response(raw_text: str) -> RecoveryPlan:
    """LLM 응답에서 action별로 분기하여 RecoveryPlan을 조립한다."""
    data = extract_json(raw_text)
    if not data:
        raise ValueError("recovery LLM 응답에서 JSON 추출 실패")

    action = data.get("action", "give_up")
    if action not in ("replan", "ask_user", "give_up"):
        action = "replan"

    lessons = data.get("lessons_learned", "")

    # ── ask_user ──────────────────────────────────
    if action == "ask_user":
        cd = data.get("clarification") or {}
        q_type = str(cd.get("type", "free_text")).lower().strip()
        return RecoveryPlan(
            action="ask_user",
            lessons_learned=lessons,
            clarification=ClarificationRequest(
                type=q_type if q_type in ("single_select", "free_text") else "free_text",
                question=cd.get("question", "추가 정보가 필요합니다."),
                options=cd.get("options"),
            ),
        )

    # ── give_up ───────────────────────────────────
    if action == "give_up":
        return RecoveryPlan(action="give_up", lessons_learned=lessons)

    # ── replan ────────────────────────────────────
    steps: list[ExecutionStep] = []
    for i, step_data in enumerate(data.get("execution_plan", [])):
        if isinstance(step_data, dict) and step_data.get("tool"):
            steps.append(ExecutionStep(
                step=i + 1,
                tool=step_data["tool"],
                input=step_data.get("input", ""),
                purpose=step_data.get("purpose", ""),
            ))

    # replan인데 유효한 스텝 없으면 → give_up 안전 전환
    if not steps:
        return RecoveryPlan(action="give_up", lessons_learned=lessons)

    # 새 가설 파싱 (LLM 누락 시 execution_plan에서 추론)
    new_hypothesis = None
    hyp_data = data.get("new_hypothesis")
    if isinstance(hyp_data, dict) and hyp_data.get("description"):
        new_hypothesis = Hypothesis(
            hypothesis_id="",
            description=hyp_data["description"],
            strategy=hyp_data.get("strategy", ""),
            priority=0.7,
            status=HypothesisStatus.ACTIVE,
        )
    if new_hypothesis is None:
        new_hypothesis = Hypothesis(
            hypothesis_id="",
            description=steps[0].purpose,
            strategy=", ".join(s.purpose for s in steps),
            priority=0.7,
            status=HypothesisStatus.ACTIVE,
        )

    return RecoveryPlan(
        action="replan",
        lessons_learned=lessons,
        execution_plan=steps,
        new_hypothesis=new_hypothesis,
    )
```

설계 근거:
- ask_user는 execution_plan/new_hypothesis 파싱이 불필요 → 조기 리턴
- give_up도 마찬가지 → 조기 리턴
- `not steps` 안전망은 replan 전용으로 의미가 명확해짐
- B4(ClarificationRequest.type 정규화)가 ask_user 분기 내에서 자연스럽게 처리됨

#### (c) recovery_agent_node — 진입부 분기 + ask_user 분기

**진입부: 일반 진입 vs 명확화 재진입 분기**

명확화 재진입 시에는 가설 전이, replan_count 증가, failure 리셋을 수행하지 않는다.
이 구조로 B2(replan_count 직접 조작), C1(placeholder dead_end 오염)이 구조적으로 발생하지 않는다.

기존 lines 86-107 (phase 설정 ~ failure 리셋)을 아래로 교체:

```python
    reason = state.reason.model_copy(deep=True)
    reason.phase = Phase.REPLANNING
    reason.exploration_phase = "recovery"
    reason.target_db = ""
    reason.target_db_decision = None

    reason.loop_guard = reason.loop_guard.model_copy()
    is_clarification_reentry = _has_clarification_answer(state)

    if not is_clarification_reentry:
        # 일반 진입: 가설 전이 + replan 카운트 + failure 컨텍스트 보존/리셋
        _handle_hypothesis_transition(reason)
        reason.loop_guard.increment_replan()
        entry_failure_type = reason.failure_type
        entry_failure_reason = reason.failure_reason
        reason.failure_type = None
        reason.failure_reason = None
    else:
        # 명확화 재진입: 가설 전이 스킵, replan 미증가, failure 리셋 스킵
        entry_failure_type = None
        entry_failure_reason = None
```

```python
def _has_clarification_answer(state: PipelineState) -> bool:
    """현재 턴에서 recovery_agent 발 명확화 응답이 있는지 확인."""
    return any(
        s.source_node == "recovery_agent" and s.answer is not None
        for s in state.resolved_signals
        if s.turn_id == state.turn_id
    )
```

**LLM 호출부 — `_build_recovery_plan` 호출 변경**

기존 lines 112-117의 `_build_recovery_plan` 호출에 `is_clarification_reentry` 전달:

```python
    plan_result, full_variables = await _build_recovery_plan(
        reason,
        state=state,
        entry_failure_type=entry_failure_type,
        entry_failure_reason=entry_failure_reason,
        is_clarification_reentry=is_clarification_reentry,
    )
```

**ask_user 조기 리턴 (should_terminate 전)**

ask_user는 가설 주입·should_terminate와 무관하므로 그 전에 조기 리턴한다.
이 구조로 placeholder 가설이 불필요하고, 잔류 ACTIVE 가설 문제가 원천 차단된다.

```python
    # ── ask_user: should_terminate 전에 조기 리턴 ──
    if plan_result is not None and plan_result.action == "ask_user":
        # ask_user 횟수 제한 — MAX_ASK_USER_ROUNDS 초과 시 give_up 전환
        if reason.loop_guard.ask_user_count >= MAX_ASK_USER_ROUNDS:
            logger.info("recovery_agent: ask_user 횟수 한도 초과, give_up 전환")
            plan_result = RecoveryPlan(
                action="give_up",
                lessons_learned=plan_result.lessons_learned,
            )
            # give_up 경로로 fall-through (아래 give_up 분기에서 처리)
        else:
            reason.loop_guard.ask_user_count += 1
            _attach_lessons(reason, plan_result)
            signal = _build_ask_user_signal(plan_result, turn_id=state.turn_id)
            await _dispatch_reasoning_step(
                reason, entry_failure_type, entry_failure_reason,
                plan_result, full_variables,
                action="ask_user",
                next_node="clarification_handler",
                routing_reason="ask_user → 사용자 명확화 대기",
                clarification_question=plan_result.clarification.question if plan_result.clarification else None,
                clarification_options=plan_result.clarification.options if plan_result.clarification else None,
            )
            return {"reason": reason, "pending_signals": [signal]}

    # ── replan: 가설 주입 (기존 그대로) ──
    if (
        plan_result is not None
        and plan_result.action == "replan"
        and plan_result.new_hypothesis is not None
    ):
        plan_result.new_hypothesis.hypothesis_id = (
            f"H{len(reason.hypotheses) + 1}"
        )
        reason.hypotheses.append(plan_result.new_hypothesis)
        reason.current_hypothesis = plan_result.new_hypothesis

    # ── should_terminate (replan/give_up만 도달) ──
    # ── give_up (기존 그대로) ──
```

(기존 (d) 섹션은 본 섹션에 통합되어 삭제)

#### (e) _build_ask_user_signal 헬퍼

필수 import 추가: `AmbiguitySignal`, `AmbiguityType`, `QuestionType`
(from `src.agents.models.clarification`)
`ConfidenceLevel` (from `src.models.enums` — clarification.py에서 re-export됨)

```python
def _build_ask_user_signal(
    plan: RecoveryPlan,
    *,
    turn_id: str,
) -> AmbiguitySignal:
    """ask_user 판정 시 AmbiguitySignal을 생성한다."""
    q = plan.clarification
    question = q.question if q else "추가 정보가 필요합니다."
    options = (q.options or []) if q else []
    q_type = QuestionType.FREE_TEXT
    if q and q.type == "single_select":
        q_type = QuestionType.SINGLE_SELECT

    return AmbiguitySignal(
        source_node="recovery_agent",
        ambiguity_type=AmbiguityType.CONTEXT,
        decision="ASK",
        confidence=ConfidenceLevel.LOW,
        question=question,
        question_type=q_type,
        options=options,
        reasoning=plan.lessons_learned,
        turn_id=turn_id,
    )
```

#### (f) _build_clarification_history — 재진입 시 프롬프트 주입

```python
def _build_clarification_history(state: PipelineState) -> str:
    """recovery_agent가 발생시킨 명확화의 사용자 응답을 추출한다."""
    parts: list[str] = []
    for signal in state.resolved_signals:
        if signal.source_node != "recovery_agent":
            continue
        if signal.turn_id != state.turn_id:
            continue
        if signal.question:
            parts.append(f"시스템 질문: {signal.question}")
        if signal.answer:
            parts.append(f"사용자 응답: {signal.answer}")
    return "\n".join(parts) if parts else ""
```

#### (g) _build_ask_user_eligible_items + _estimate_item_failure_count

DeadEnd에 `related_knowledge_keys` 필드 추가 (state.py):

```python
class DeadEnd(BaseModel):
    hypothesis_id: str                                               # required (기본값 없음)
    failure_type: FailureType = FailureType.NO_KNOWLEDGE
    reason: str = ""
    lessons_learned: str = ""
    related_knowledge_keys: list[str] = Field(default_factory=list)  # 추가
```

`_handle_hypothesis_transition`에서 DeadEnd를 생성하는 경로가 2곳 있다.
양쪽 모두 `related_knowledge_keys`를 기록한다:

```python
# _handle_hypothesis_transition 내부 — 공통 헬퍼
unresolved_keys = [
    ki.key for ki in reason.knowledge_items
    if ki.status in (ConfidenceStatus.UNRESOLVED, ConfidenceStatus.CONFLICTED)
    and ki.is_critical
]

# 경로 1: 가설이 있는 경우 (line 352 부근)
reason.dead_ends.append(
    DeadEnd(
        hypothesis_id=failed.hypothesis_id,
        failure_type=...,
        reason=...,
        related_knowledge_keys=unresolved_keys,
    )
)

# 경로 2: 가설이 없는 경우 (line 362 부근)
reason.dead_ends.append(
    DeadEnd(
        hypothesis_id="no_hypothesis",
        failure_type=...,
        reason=...,
        related_knowledge_keys=unresolved_keys,
    )
)
```

항목별 실패 횟수 계산:

```python
def _estimate_item_failure_count(
    ki: KnowledgeItem, dead_ends: list[DeadEnd],
) -> int:
    """해당 KI가 UNRESOLVED였던 dead_end 횟수를 반환한다."""
    return sum(1 for de in dead_ends if ki.key in de.related_knowledge_keys)
```

프롬프트 주입:

```python
def _build_ask_user_eligible_items(reason: ReasoningState) -> str:
    """미해소 항목별 실패 횟수를 계산하고 탐색 한계 여부를 표시한다."""
    unresolved = [
        ki for ki in reason.knowledge_items
        if ki.status in (ConfidenceStatus.UNRESOLVED, ConfidenceStatus.CONFLICTED)
        and ki.is_critical
    ]
    if not unresolved:
        return "(미해소 항목 없음)"

    lines: list[str] = []
    for ki in unresolved:
        count = _estimate_item_failure_count(ki, reason.dead_ends)
        if count >= 2:
            lines.append(f"- {ki.key} → {count}회 실패, 탐색 한계")
        else:
            lines.append(f"- {ki.key} → {count}회 실패")

    return "\n".join(lines)
```

#### (h) _build_prompt 시그니처 변경 및 치환 추가

기존 패턴(분해된 값 전달)을 유지한다. PipelineState를 직접 넘기지 않는다.

`_build_recovery_plan`에서 계산해서 문자열로 전달:

```python
# _build_recovery_plan 내부 (lines 419-475)
clarification_history = _build_clarification_history(state) if state else ""
ask_user_eligible = _build_ask_user_eligible_items(reason)

prompt, variables, full_variables = _build_prompt(
    reason,
    original_query=original_query,
    rewritten_query=rewritten_query,
    entry_failure_type=entry_failure_type,
    entry_failure_reason=entry_failure_reason,
    is_clarification_reentry=is_clarification_reentry,
    clarification_history=clarification_history,
    ask_user_eligible_items=ask_user_eligible,
)
```

`_build_recovery_plan` 시그니처에도 `is_clarification_reentry` 전달 필요:

```python
async def _build_recovery_plan(
    reason: ReasoningState,
    *,
    state: PipelineState | None = None,
    entry_failure_type: FailureType | None = None,
    entry_failure_reason: str | None = None,
    is_clarification_reentry: bool = False,       # 추가
) -> tuple[RecoveryPlan | None, dict[str, str]]:
```

`_build_prompt` 시그니처에 파라미터 3개 추가:

```python
def _build_prompt(
    reason: ReasoningState,
    *,
    original_query: str = "",
    rewritten_query: str = "",
    entry_failure_type: FailureType | None = None,
    entry_failure_reason: str | None = None,
    is_clarification_reentry: bool = False,  # 추가
    clarification_history: str = "",          # 추가
    ask_user_eligible_items: str = "",        # 추가
) -> tuple[str, dict[str, str], dict[str, str]]:
```

`_build_prompt` 내부 entry_desc 분기에 재진입 경로 추가 (기존 if-elif 체인 최상단):

```python
    if is_clarification_reentry:
        entry_desc = (
            "진입 경로: 사용자 명확화 응답 수신\n"
            "아래 '사용자 명확화 응답' 섹션의 답변을 근거로 새 가설을 수립하세요."
        )
    elif entry_src == "sql_validator":
        # ... 기존 그대로
```

replacements dict에 추가:

```python
replacements["{clarification_history}"] = clarification_history or "(없음)"
replacements["{ask_user_eligible_items}"] = ask_user_eligible_items or "(없음)"
```

#### (i-1) _dispatch_reasoning_step — ask_user 추적 정보 확장

기존 `_dispatch_reasoning_step`에 ask_user 시 명확화 정보를 추적 출력에 포함한다.
시그니처에 optional 파라미터 추가:

```python
async def _dispatch_reasoning_step(
    reason: ReasoningState,
    entry_failure_type: FailureType | None,
    entry_failure_reason: str | None,
    plan_result: RecoveryPlan,
    full_variables: dict[str, str],
    *,
    action: str = "replan",
    next_node: str = "",
    routing_reason: str = "",
    clarification_question: str | None = None,   # ask_user 시 질문
    clarification_options: list[str] | None = None,  # ask_user 시 선택지
) -> None:
```

추적 출력(reasoning_step)에 ask_user인 경우 `clarification_question`, `clarification_options`를 포함하여 감사 로그에 기록한다.

#### (i-2) _finalize_give_up 유지

max_replans 도달 시 `should_terminate() → _finalize_give_up` 경로는 그대로 유지.
LLM이 give_up을 선택하는 경로와 Python이 강제 종료하는 경로 모두 존재.


### 변경 3: Pipeline — src/agents/graph/pipeline.py

#### _VALID_RETURN_TARGETS에 recovery_agent 추가

```python
_VALID_RETURN_TARGETS = frozenset({
    "intent_classifier",
    "normalize_query",
    "recovery_agent",       # 3단계 추가
})
```

`_route_after_clarify`의 엣지 맵은 `_VALID_RETURN_TARGETS`에서 comprehension(`{target: target for target in _VALID_RETURN_TARGETS}`)으로 자동 확장됨. 별도 엣지 맵 수정 불필요.

기존 `_route_after_recovery_agent`의 `pending_signals` 체크(line 279)가 그대로 동작:
```python
if state.pending_signals:
    return "clarification_handler"  # ask_user 시 여기로 라우팅
```


---

## 변경하지 않는 것

| 항목 | 사유 |
|------|------|
| readiness_gate의 점수 기반 판정 로직 | 별도 설계 과제 |
| reasoning_preparer의 output_scope CONFLICTED | 본질적으로 정규화 단계 문제, 별도 과제 |
| sql_generator의 assumption INFER 시그널 | 정상 동작 중 |
| clarification_handler_node 자체 | 통합 명확화 노드, 변경 불필요 |
| runner.py의 interrupt 감지/재개 로직 | 변경 불필요 |
| _finalize_give_up 로직 | max_replans 강제 종료 시 그대로 사용 |


---

## 테스트 계획

### 단위 테스트: test_recovery_agent.py

| 테스트 | 내용 |
|--------|------|
| _parse_plan_response: ask_user 파싱 | ask_user JSON → RecoveryPlan.clarification 정상 파싱 |
| _parse_plan_response: ask_user type 정규화 | "SINGLE_SELECT" → "single_select"로 정규화 |
| _parse_plan_response: 가드절 | "ask_user"가 "replan"으로 변환되지 않는지 확인 |
| _parse_plan_response: give_up 유지 | give_up JSON 입력 시 정상 처리 |
| _parse_plan_response: replan 빈 스텝 → give_up | replan인데 execution_plan 없으면 give_up 전환 |
| _parse_plan_response: ask_user clarification 누락 | clarification 필드 없으면 기본값 적용 |
| _parse_plan_response: ask_user 빈 plan은 give_up 아님 | ask_user의 빈 execution_plan이 give_up으로 변환되지 않음 |
| _build_ask_user_signal | ClarificationRequest → AmbiguitySignal 변환, turn_id 포함 |
| _build_ask_user_signal: question_type 매핑 | single_select → QuestionType.SINGLE_SELECT |
| _build_ask_user_signal: clarification=None 방어 | q=None일 때 기본 질문 + 빈 options |
| _build_clarification_history | resolved_signals에서 recovery_agent + turn_id 매칭 응답만 추출 |
| _build_clarification_history: 다른 노드 혼재 | recovery_agent가 아닌 시그널 필터 확인 |
| _build_ask_user_eligible_items | dead_ends.related_knowledge_keys 기반 항목별 실패 횟수 계산 |
| _build_ask_user_eligible_items: 탐색 한계 표시 | count >= 2인 항목에 "탐색 한계" 표시 |
| _estimate_item_failure_count | related_knowledge_keys에 ki.key 포함된 dead_end만 카운트 |
| ask_user 조기 리턴 | ask_user가 should_terminate 전에 리턴하여 placeholder 불필요 확인 |
| ask_user 횟수 제한 | ask_user_count >= MAX_ASK_USER_ROUNDS 시 give_up 전환 |
| ask_user_count 증가 | ask_user 조기 리턴 시 ask_user_count += 1 확인 |
| replan_count 미증가 | 재진입 시 increment_replan() 스킵 확인 |
| RecoveryPlan 기본값 | action="give_up", clarification=None |

### 라우팅 테스트: test_pipeline_routing.py

| 테스트 | 내용 |
|--------|------|
| recovery_agent → clarification_handler | pending_signals 반환 시 라우팅 |
| clarification_handler → recovery_agent | source_node="recovery_agent" + turn_id 매칭 |
| _VALID_RETURN_TARGETS 포함 확인 | "recovery_agent" in _VALID_RETURN_TARGETS |

### E2E 테스트

| 테스트 | 내용 |
|--------|------|
| ask_user 해피패스 | ask_user → interrupt → 사용자 응답 → resume → replan |
| 사용자 "모르겠다" | ask_user → 응답 "모르겠어요" → replan 계속 |
| give_up 경로 | 데이터 부재 → give_up → _finalize_give_up |
| max_replans 종료 | replan 반복 → should_terminate → _finalize_give_up |
| 기존 replan 회귀 | ask_user 추가 후에도 기존 replan 경로 정상 동작 |
