# 명확화 질문 UI 상세 설계

작성일: 2026-04-14
상태: 설계 초안 (구현 미착수)
선행 문서:
- `docs/todo/20260406-ui-ux-improvement-plan.md` §1.4 (최초 설계안)
- `docs/todo/20260409-embedded-html-gap-analysis.md` §4-4 (구현 스켈레톤)
- `docs/todo/20260409-embedded-html-implementation-plan.md` (Phase 3 제외 이력)
- `docs/todo/20260405-conversation-history-ui-design.md` REQ-ERR-05 (복원 요건)
- `src/agents/models/clarification.py` (AmbiguitySignal 모델)

---

## 1. 목적과 범위

1. **명시성**: 사용자가 "지금은 명확화 답변 차례"임을 UI에서 즉시 인지할 수 있어야 한다.
2. **UX 통일**: 피드백(`좋아요`/`싫어요`) 팝업과 동일 톤의 카드 디자인으로 선택+자유입력을 한 화면에서 처리한다.
3. **복원 정확성**: 새로고침/세션 전환 후 대화이력을 다시 열어도 "어떤 질문에 어떤 답을 선택했는지" 그대로 보인다.
4. **범위 제외**: 계수산출식·테이블 모호성 등 백엔드 명확화 로직 자체는 손대지 않는다. 본 문서는 UI/저장 구조/복원에 한정한다.

## 2. 명확화 카드 위치 결정

### 2.1 후보 비교

| 후보 | 설명 | 명시성 | 대화 흐름 일관성 | 자유 질문 차단 위험 |
|------|------|--------|-----------------|---------------------|
| **A. 어시스턴트 버블 직후 인라인 카드** (기존 설계) | `.bot-bubble` 아래 `.clarification-card` append | 중 | 높음 (질문은 AI가 하는 것이 자연스러움) | 없음 |
| **B. 사용자 입력영역 위 스티키 바** | 입력창 위에 "🤔 명확화 답변 대기 중 — 아래 카드를 사용하세요" 배너 고정 | 상 | 낮음 (대화 외 UI) | 없음 |
| **C. 입력창 교체** | 일반 텍스트 입력창을 선택지 폼으로 치환 | 상 | 중 | **있음** (다른 질문으로 빠져나갈 길 차단) |
| **D. A + 입력창 힌트**(채택) | A의 카드 + 입력창 placeholder를 "명확화 답변 대기 중… 다른 질문을 입력해도 됩니다"로 변경, 입력창 좌측에 🤔 배지 | 상 | 높음 | 없음 |

### 2.2 결정: **D 채택**

근거:
- AI가 질문하는 맥락이므로 카드는 **어시스턴트 측**에 둔다. 사용자 측에 두면 "내가 질문한 듯한" 착시가 생긴다.
- 사용자 프로필(IT 비전문 은행 직원, `.claude/rules/user-interaction.md`)상 "지금 뭘 해야 하는지" 맥락 고정이 중요하므로 입력창에도 상태 표시를 병행한다.
- 건너뛰기 버튼 대신 입력창에서 "그냥 다른 질문" 가능 경로를 열어둔다는 기존 결정(§1.4 동작 규칙)을 유지.

### 2.3 시각 스펙

```
[대화 영역]
  🤖 (assistant bubble) 질문 본문이 여기 스트리밍됨
  ┌─ .clarification-card ─────────────────┐
  │ 🤔 명확화가 필요해요                    │
  │ "어떤 테이블을 사용할까요?"             │
  │  ○ 여신원장 (LN_CONT)                  │
  │  ○ 일별 여신잔액 (LN_DLY_BAL)           │
  │  ○ 기타 (직접 입력)                     │
  │  [_________________________________]   │
  │                            [ 제출 ]     │
  └───────────────────────────────────────┘

[입력 영역]
  🤔 [ 명확화 답변 대기 중… 다른 질문을 입력해도 됩니다 ]  [전송]
```

- 카드 배경: `var(--bg2)`, 좌측 4px accent 보더(`var(--accent)`)로 일반 말풍선과 구분.
- 입력창 placeholder + 🤔 배지는 `pending_clarification` 상태일 때만 적용하고, 카드 제출/세션 이동 시 원복.

## 3. 대화 히스토리 관리

### 3.1 현재 한계

- `save_turn(role, content, turn_type, ...)` — content에 **질문 텍스트 한 줄만** 저장.
- `options`, `question_type`, `ambiguity_type`, `source_node`, 그리고 **사용자가 실제로 어떤 선택지를 골랐는지**(옵션 선택 vs 커스텀 입력 vs 무시)가 영속화되지 않는다.
- 복원 UI(`_renderClarificationRestored`)가 "다음 user 턴 text를 포함하는 p/li에 배경색 입히기"라는 휴리스틱에 의존 → 옵션 텍스트가 렌더된 마크다운에 우연히 섞여야만 동작.

### 3.2 제안: `turn.metadata` JSONB 확장

저장 위치는 기존 turn 행의 `metadata` 컬럼(없다면 추가). `src/services/turn_text_store.py`에 `clarification_payload` 인자를 추가하여 assistant 명확화 턴 저장 시 아래 객체를 넣는다.

#### 3.2.1 Assistant(질문) 턴 metadata 예시

```json
{
  "clarification": {
    "signal_id": "amb_20260414_0931_ctx_001",
    "ambiguity_type": "TABLE",
    "question_type": "single_select",
    "source_node": "context_retriever",
    "question": "어떤 테이블을 사용할까요?",
    "options": ["여신원장 (LN_CONT)", "일별 여신잔액 (LN_DLY_BAL)"],
    "confidence": "MEDIUM",
    "reasoning": "여신 관련 테이블이 2개 매칭됨",
    "resolution": null
  }
}
```

#### 3.2.2 User(답변) 턴 metadata 예시

```json
{
  "clarification_answer": {
    "answers_turn_id": "t_01HV…",         // 위 assistant turn id
    "signal_id": "amb_20260414_0931_ctx_001",
    "selected_kind": "option",             // "option" | "custom" | "ignored"
    "selected_index": 0,                   // option일 때만
    "selected_value": "여신원장 (LN_CONT)",
    "submitted_via": "card"                // "card" | "input_bypass"
  }
}
```

#### 3.2.3 해소(resolution) 업데이트

답변 턴 저장 직후, 원 assistant 턴의 `metadata.clarification.resolution` 필드를 업데이트(한 번의 UPDATE):

```json
"resolution": {
  "answered_turn_id": "t_01HV…_user",
  "selected_kind": "option",
  "selected_value": "여신원장 (LN_CONT)",
  "resolved_at": "2026-04-14T09:31:47Z"
}
```

#### 3.2.4 상태 전이

| 상황 | assistant.metadata.clarification.resolution | user.metadata.clarification_answer |
|------|---------------------------------------------|-----------------------------------|
| 사용자가 카드로 옵션 제출 | `{selected_kind: "option", …}` | `selected_kind: "option"` |
| 사용자가 "기타" 자유입력 | `{selected_kind: "custom", selected_value: "<입력>"}` | `selected_kind: "custom"` |
| 사용자가 카드를 무시하고 입력창에서 다른 질문 | `{selected_kind: "ignored"}` (다음 사용자 턴 저장 시 갱신) | user 턴은 `turn_type='normal'`, `answers_turn_id` 비워둠 |
| 세션 종료/장시간 무응답 | `resolution: null` 유지 → 복원 시 "대기 중" 상태 | (없음) |

### 3.3 서버측 메시지(`stream.end`) 스키마

`runner.py` `clarification_result` 생성 지점(L366-373)에서 `clarification_request`를 아래 형태로 고정:

```json
{
  "type": "stream",
  "action": "end",
  "status": "awaiting_clarification",
  "turn_id": "t_01HV…",
  "clarification_request": {
    "signal_id": "amb_20260414_0931_ctx_001",
    "question": "어떤 테이블을 사용할까요?",
    "question_type": "single_select",
    "options": ["여신원장 (LN_CONT)", "일별 여신잔액 (LN_DLY_BAL)"],
    "ambiguity_type": "TABLE",
    "source_node": "context_retriever"
  }
}
```

`signal_id`를 포함시켜 answer 전송 시 `Command(resume={signal_id, value})`로 서버가 어느 signal을 해소하는지 명확히 매칭할 수 있게 한다(현재는 단일 pending 가정 — 미래 다중 signal 대비).

## 4. 대화이력 복원

### 4.1 데이터 흐름

1. `GET /api/sessions/{id}` → `turns: [...]` 응답에 각 턴의 `metadata` 포함.
2. 프론트 `loadSession()`에서 `turn.metadata.clarification` 존재 시 `MS.create({turnType:'clarification', clarification: turn.metadata.clarification, ...})`로 메시지 객체 생성.
3. 렌더 시 카드를 **interactive 또는 readonly** 두 모드로 분기.

### 4.2 렌더 모드 분기

| 조건 | 모드 | 시각 |
|------|------|------|
| `resolution == null` AND 마지막 turn이 이 assistant 턴 AND 세션이 아직 live | **Interactive** (이어서 답변 가능) | 일반 카드 그대로, `[제출]` 활성 |
| `resolution.selected_kind == "option"` | **Readonly (선택됨)** | 선택 옵션에 `✓ 선택됨` 배지, 다른 옵션은 dim, 입력창·버튼 비활성 |
| `resolution.selected_kind == "custom"` | **Readonly (직접입력)** | "기타"에 체크, 입력창에 저장된 값 readonly 표시 |
| `resolution.selected_kind == "ignored"` | **Readonly (건너뜀)** | 카드 상단에 "답변 없이 다른 질문으로 진행됨" 회색 배지 |
| `resolution == null` AND 마지막 turn이 아님 (세션 재개 중 미해소 상태로 이탈) | **Readonly (미응답)** | "답변 대기 중이었음" 회색 배지 |

### 4.3 "Interactive 복원"의 서버 전제

사용자가 명확화 중간에 새로고침해도 이어서 답할 수 있으려면:
- LangGraph checkpoint에 interrupt 상태가 남아 있어야 함 (`20260410-session-resume-and-crash-recovery-design.md`와 연계).
- 세션 로드 직후 `GET /api/sessions/{id}/status` (또는 WebSocket `hello` 응답)에서 `awaiting_clarification: true, signal_id: ...`를 함께 반환.
- 프론트는 해당 signal_id가 마지막 assistant 턴 metadata와 일치할 때만 Interactive 모드로 렌더.

> **주의**: 서버 세션 복구가 아직 미구현이면 본 설계의 Interactive 복원은 "최근 turn 기준 fallback"으로 축소하고, 불일치 시 "답변 대기 중이었음(만료)"으로 표시. 이 fallback은 Phase 1, Interactive 복원은 Phase 2로 분리.

### 4.4 기존 `_renderClarificationRestored` 제거

metadata 기반 정식 렌더가 도입되면 휴리스틱 함수(embedded.html L2179-2195)는 삭제. 구 데이터(metadata 없는 레거시 턴)는 `content`만 그대로 표시하고 배지 없이 일반 assistant 말풍선처럼 렌더한다(하이라이트 유지 필요 없음 — 옵션 정보가 없으므로 오히려 오인 유도 방지).

## 5. 그 외 고려 디테일

### 5.1 접근성

- 카드는 `role="group" aria-labelledby="clarQ-<id>"`로 마크업. `<dialog>` 아님(대화 흐름 차단 X).
- 질문 본문 요소에 `id="clarQ-<id>"`, `aria-live="polite"` 부여하여 스트리밍 종료 시 스크린리더가 1회 고지.
- 키보드: `↑/↓` 라디오 이동(native), `Enter` = 제출, `Esc` = 포커스만 입력창으로 이동(취소 아님).
- `question_type=confirm`의 예/아니오 버튼은 클릭 즉시 제출 + 0.5s 내 중복 클릭 차단.

### 5.2 이중 제출·경쟁 상태

- `[제출]` 클릭 시 버튼 즉시 `disabled`, 카드에 `data-submitting="1"` 설정.
- WebSocket ACK 또는 다음 `stream.start` 수신 전까지 카드 DOM 유지. 실패 시(3s timeout + 에러 이벤트) 버튼 재활성 + 인라인 에러 표시.
- 입력창에서 동시에 다른 질문을 전송한 경우 → 카드는 즉시 "건너뜀" 상태로 전환, 서버는 기존 interrupt를 empty resume으로 해소하거나 새 요청을 취소 처리(서버 계약 확정 필요).

### 5.3 다중 pending signal

- 현재 `clarification_handler`는 한 번에 1개 질문만 표출. 장래 다중화 대비해 카드 자체는 배열 지원 가능한 구조(`clarificationQueue: AmbiguitySignal[]`)를 데이터 모델로 두고, UI는 "1/3" 형태 카운터를 선택적으로 노출.
- 단, **1차 구현은 단일 signal 전제**. 다중은 별도 설계 티켓.

### 5.4 옵션 UX 엣지

- 옵션이 10개 초과 → 라디오 대신 검색 가능한 `<select>` 또는 스크롤 리스트로 전환.
- 옵션 텍스트가 80자 초과 → 카드 내에서 2-line clamp + tooltip로 전체 표시.
- 옵션에 코드값 포함 시 `여신원장 (LN_CONT)` 형태로 "한글 설명 (물리명)" 포맷을 서버측에서 통일(`.claude/rules/user-interaction.md` 준수).

### 5.5 보안

- `question`, `options[]`, `selected_value`는 렌더 시 `esc()` 필수. 과거 SQL 이력에서 유입된 텍스트가 포함될 수 있음.
- `selected_value`를 서버로 보낼 때 기존 메시지 전송 경로(`CN.send`)를 재사용하여 동일한 입력 검증 파이프라인을 거치게 한다(SQL 주입·프롬프트 주입 방어가 자동 적용).

### 5.6 감사·분석 지표

- `selected_kind` 분포(옵션/커스텀/무시)를 주기 집계. "옵션 제시했는데 커스텀 입력 비율 높음" → 옵션 생성 품질 저하 신호, 프롬프트 개선 피드백 루프에 연결.
- `source_node` × `ambiguity_type` × `selected_kind` 3축 분석으로 모호성 판정 노드별 정확도 추적.

### 5.7 마이그레이션

- `turn.metadata` 컬럼이 아직 없다면 `resources/connectors/postgres/checkpoint/03_dc_custom_tables.sql`에 `metadata jsonb` 추가 + 기본값 `'{}'::jsonb`.
- 기존 clarification 턴(metadata 없음)은 그대로 두고, 렌더러가 "레거시" 판단 후 단순 표시. 백필 불필요.

### 5.8 국제화

- 현재 KR 고정이지만 카드 내 고정 문구("기타 (직접 입력)", "제출", "명확화가 필요해요", "✓ 선택됨", "답변 없이 다른 질문으로 진행됨")는 i18n 키로 분리. 폐쇄망 반입 시 문구 변경 용이.

## 6. 구현 Phase

| Phase | 범위 | 선행 |
|-------|------|------|
| **0** | `turn.metadata` 컬럼 확보 + `save_turn(..., metadata=...)` 확장 | DDL 변경 |
| **1** | 서버: `stream.end`에 `clarification_request{signal_id, ...}` 포함, assistant/user 명확화 턴 저장 시 metadata 기록 | Phase 0 |
| **2** | 프론트: `_renderClarification` interactive 카드 + 입력창 상태 표시 + 제출→resume | Phase 1 |
| **3** | 복원 렌더(Readonly 4종 모드), 레거시 턴 폴백, `_renderClarificationRestored` 휴리스틱 제거 | Phase 2 |
| **4** | Interactive 복원(세션 재개 중 미해소 상태 이어가기) | 세션 재개 설계(20260410) 완료 후 |
| **5** | 분석 대시보드(`selected_kind` 분포 등) | Phase 3 |

## 7. 미결정·후속 논의

1. 세션 재개 미구현 상태에서 Interactive 복원 대신 "만료 처리"를 할지, 최근 턴 한정 interactive를 허용할지.
2. `selected_kind="ignored"`로 전환하는 트리거를 서버(interrupt를 empty resume으로 해소) vs 프론트(단순 UI 전환) 중 어디에 둘지.
3. `question_type=confirm`에서 `[제출]` 버튼 유지(중복 안전) vs 즉시 제출(클릭 수 감소) — 사용자 검토 필요.
4. 다중 signal 동시 표출 UX(순차 1장 vs 카드 스택).
