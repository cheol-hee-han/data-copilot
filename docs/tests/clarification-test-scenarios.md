# 명확화(Clarification) 테스트 시나리오

작성일: 2026-04-14
관련 문서:
- 설계: `docs/todo/20260414-clarification-ui-design.md`
- 전략: `docs/strategy-proposals/checkpointer-multi-turn/01-strategy.md`
- 카탈로그: `tests/test_cases/agentic_e2e_test_catalog.json` (CAT-06, CAT-07, CAT-09)
- 모델: `src/agents/models/clarification.py`

---

## 1. 트리거 맵

| 트리거 | 노드 | 유형 | UI question_type | 비고 |
|--------|------|------|------------------|------|
| **T1** | `history_resolver` | UNSURE (하드코딩 ASK) | FREE_TEXT | 지시대명사·history 미해소 |
| **T2** | `intent_classifier` | AMBIGUOUS (LLM 판정) | FREE_TEXT | 의도 자체가 모호 |
| **T3** | `query_normalizer` | ambiguities (LLM 판정) | FREE_TEXT / SINGLE_SELECT | 메타/이력 없이 정규화 시점 모호 |
| **T4** | `sql_generator` | Cross-DB | **INFER 고정** | UI 노출 없음 (사용자에게 DB를 묻지 않음) |
| **T5** | `confidence_evaluator` | CONFLICTED | SINGLE_SELECT | 신뢰도 충돌 — 옵션 제시형 |

> T4는 UI 테스트 대상이 아니다. 카드가 뜨는 경로는 **T1/T2/T3/T5**.

---

## 2. 핵심 테스트 질의

### 2.1 T1 — history UNSURE (FREE_TEXT)

지시대명사가 포함되고 컨텍스트가 없는 상태.

| ID | 질의 | 기대 |
|----|------|------|
| CL-T1-01 | `지난달 것 뽑아줘` | "무엇의 지난달?" 자유입력 카드 |
| CL-T1-02 | `그거 다시 뽑아줘` | "무엇을 다시?" 자유입력 카드 |
| CL-T1-03 | `아까 그 테이블로 다시` | history 참조 실패 → FREE_TEXT |

### 2.2 T2 — intent AMBIGUOUS (FREE_TEXT)

의도가 data_extraction/data_analysis/meta_question 사이에서 모호.

| ID | 질의 | 기대 |
|----|------|------|
| CL-T2-01 | `고객 정보 뽑아줘` | 범위·조건 확인 자유입력 |
| CL-T2-02 | `실적 좀 알려줘` | 어떤 실적/기간 자유입력 |
| CL-T2-03 | `최근 현황 알려줘` | 최근 범위·대상 자유입력 |

### 2.3 T3 — normalize ambiguities (주로 SINGLE_SELECT)

가장 풍부한 UI 검증 경로. 옵션이 2~4개 제시되는 케이스.

| ID | 질의 | 예상 옵션 |
|----|------|-----------|
| CL-T3-01 | `잔액 알려줘` | 예금 잔액 / 대출 잔액 |
| CL-T3-02 | `연체 현황` | 연체 건수 / 연체율 / 연체금액 |
| CL-T3-03 | `등급별로 분석해줘` | 고객등급 / 연체등급 / 신용등급 / 마케팅등급 |
| CL-T3-04 | `금리 현황` | 예금금리 / 대출금리 / 가중평균 |
| CL-T3-05 | `상위 고객 리스트` | 자산 기준 / 거래량 기준 / 대출잔액 기준 |
| CL-T3-06 | `수수료 얼마야` | 카드 / 환전 / 대출 수수료 |
| CL-T3-07 | `VIP 현황 보여줘` | CUS_GRD_CD VIP / MKT_GRD_CD VIP |

### 2.4 T5 — confidence CONFLICTED (SINGLE_SELECT)

Interpret 통과 후 Reason 단계에서 충돌 발생.

| ID | 질의 | 비고 |
|----|------|------|
| CL-T5-01 | `리스크 현황 보여줘` | 신용/시장/운영 리스크 |
| CL-T5-02 | `추이 분석` + 후속 `연체율 추이요` | 산출식 경합 가능 |

---

## 3. UI 동작 시나리오

설계 문서(`20260414-clarification-ui-design.md`)의 카드 위치·히스토리·복원 규칙을 검증한다.

### 3.1 S1 — 옵션 선택(option) 경로

1. `잔액 알려줘` 전송
2. 카드 등장 확인
   - [ ] 어시스턴트 버블 직후에 카드가 인라인으로 붙음
   - [ ] 입력창 placeholder가 "명확화 답변 대기 중…"으로 변경
   - [ ] 입력창 좌측 🤔 배지 표시
3. "예금 잔액" 라디오 선택 → `[제출]`
4. 기대
   - [ ] 제출 즉시 버튼 disabled
   - [ ] DEP 계열 테이블 SELECT로 SQL 생성
   - [ ] assistant turn metadata: `clarification.resolution.selected_kind == "option"`
   - [ ] user turn metadata: `clarification_answer.selected_kind == "option"`, `selected_index == 0`

### 3.2 S2 — 커스텀 입력(custom) 경로

1. `등급별로 분석해줘` 전송 → 카드 등장
2. "기타 (직접 입력)" 선택 → 텍스트박스 노출 확인
3. `신용등급 기준으로` 입력 → `[제출]`
4. 기대
   - [ ] 자유 텍스트가 resume 값으로 전송됨
   - [ ] metadata: `selected_kind == "custom"`, `selected_value == "신용등급 기준으로"`

### 3.3 S3 — 건너뛰기(ignored) 경로

1. `수수료 얼마야` 전송 → 카드 등장
2. 카드는 손대지 않고 입력창에 `오늘 날짜 알려줘` 입력·전송
3. 기대
   - [ ] 카드가 "답변 없이 다른 질문으로 진행됨" 회색 배지로 전환
   - [ ] 이전 interrupt가 서버에서 정리됨(다음 턴이 독립 처리)
   - [ ] 원 assistant metadata: `resolution.selected_kind == "ignored"`

### 3.4 S4 — FREE_TEXT 카드

1. `지난달 것 뽑아줘` 전송
2. 기대
   - [ ] 라디오 없이 텍스트 입력창만 있는 카드
   - [ ] placeholder "답변을 입력해주세요…"
   - [ ] 빈 값 제출 차단

### 3.5 S5 — confirm 타입 (현재 서버 생성 빈도 낮음, 수동 확인용)

강제 경로: `AmbiguitySignal.question_type = CONFIRM`이 발생하는 fixture를 주입하거나 로깅에서 확인.
   - [ ] "예/아니오" 버튼만 렌더
   - [ ] 즉시 제출 vs `[제출]` 버튼 병행 여부는 설계 §7-3 미결정

### 3.6 S6 — 복원(Readonly) 렌더

S1/S2/S3 시나리오 수행 후 새로고침 또는 다른 세션에서 재방문.

| 원 경로 | 복원 모드 | 시각 확인 |
|---------|-----------|-----------|
| S1(option) | Readonly 선택됨 | 선택한 옵션에 `✓ 선택됨` 배지, 나머지 dim |
| S2(custom) | Readonly 직접입력 | "기타"에 체크, 입력값 readonly 표시 |
| S3(ignored) | Readonly 건너뜀 | "답변 없이 다른 질문으로 진행됨" 배지 |
| 미응답 상태 로그아웃 | Readonly 미응답 | "답변 대기 중이었음" 배지 |

### 3.7 S7 — Interactive 복원 (Phase 4)

세션 재개 설계 완료 후 수행. 명확화 대기 상태에서 새로고침 → 카드가 다시 interactive로 렌더되어 이어서 답변 가능해야 한다. 서버 `/status` 또는 WebSocket `hello`에 `awaiting_clarification: true, signal_id: ...` 포함 확인.

### 3.8 S8 — 연속 명확화 / max turns

1. `데이터 뽑아줘` → 카드 등장
2. 커스텀 입력 `모호한 답변`
3. 다시 카드 등장 → 또 모호한 답변
4. 기대
   - [ ] 설정된 max clarification turns(예: 2) 초과 시 강제 진행
   - [ ] 최종 결과에 "명확화 한계 도달로 기본값으로 진행" 안내 포함

---

## 4. 접근성·엣지 체크리스트

- [ ] 키보드만으로 라디오 이동(↑↓) + Enter 제출 가능
- [ ] 스크린리더가 질문 본문을 aria-live로 1회 고지
- [ ] 옵션 10개 초과 시 스크롤/검색 UI로 전환(설계 §5.4)
- [ ] 옵션 텍스트 80자 초과 시 2-line clamp + tooltip
- [ ] `question`/`options`/`selected_value` 모두 `esc()` 적용 — `<script>alert(1)</script>` 주입 테스트
- [ ] 이중 제출 방지: `[제출]` 더블클릭 시 1회만 전송
- [ ] WebSocket 끊김 중 제출 → 재연결 후 에러 토스트 + 카드 재활성

---

## 5. 서버·데이터 검증 포인트

### 5.1 메시지 스키마

`stream.end` 페이로드에 다음 필드가 전부 존재하는지 확인:

```json
{
  "status": "awaiting_clarification",
  "turn_id": "...",
  "clarification_request": {
    "signal_id": "...",
    "question": "...",
    "question_type": "single_select | free_text | confirm",
    "options": [],
    "ambiguity_type": "TABLE | INTENT | VALUE | FORMULA | TIMEFRAME | CONTEXT | CONFLICT",
    "source_node": "..."
  }
}
```

### 5.2 DB 저장 검증

각 시나리오 수행 후 PostgreSQL에서 확인:

```sql
SELECT turn_id, role, turn_type, content,
       metadata->'clarification' AS clar,
       metadata->'clarification_answer' AS ans
  FROM dc_turn
 WHERE thread_id = :session
 ORDER BY created_at;
```

- [ ] assistant 턴: `turn_type='clarification'`, `metadata.clarification.question_type` 존재
- [ ] user 턴(응답): `metadata.clarification_answer.answers_turn_id`가 직전 assistant turn_id와 일치
- [ ] user 턴(ignored): `metadata.clarification_answer` 없음, 원 assistant의 `resolution.selected_kind == "ignored"`

### 5.3 Signal 추적

`resolved_signals`에 AmbiguitySignal이 누적되는지 — checkpoint 덤프 또는 트레이스에서 확인.
- [ ] `signal.answer` 필드에 사용자 응답 저장
- [ ] `signal.resolved_at` timestamp 설정
- [ ] `turn_id` 필드로 소속 턴 격리 확인(CAT-09 격리 테스트)

---

## 6. 회귀 테스트 묶음

빠른 smoke test (매 배포 전):

1. `잔액 알려줘` → 옵션 선택 → DEP SELECT 성공
2. `지난달 것 뽑아줘` → free_text → history 주입 확인
3. `리스크 현황 보여줘` → T5 경로 카드 → 옵션 선택 → 성공
4. S1 수행 후 새로고침 → Readonly 선택됨 상태 유지

실패 시 블로커 처리. 통과 기준: 4개 전부 그린.

---

## 7. 미구현/알려진 한계

현 시점(2026-04-14) 기준:

- `stream.end` 페이로드의 `clarification_request` 필드 누락 가능 — `runner.py:366-373` 점검 필요
- 프론트 `_renderClarification` 미구현 → 실 UI 테스트는 설계 Phase 2 완료 이후
- `turn.metadata` 컬럼 미존재 가능성(Phase 0) → 확인 후 DDL 반영
- Interactive 복원(S7)은 세션 재개 설계(`20260410-session-resume-and-crash-recovery-design.md`) 완료 전까지 스킵

이 문서는 설계·구현이 Phase별로 진행됨에 따라 체크박스가 그린으로 채워지는 실행 체크리스트 역할을 한다.
