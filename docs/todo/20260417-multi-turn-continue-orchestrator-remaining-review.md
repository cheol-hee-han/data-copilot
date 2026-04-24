# Multi-Turn CONTINUE Orchestrator — 남은 구현 재검토

작성일: 2026-04-17
관련:
- `docs/todo/20260416-multi-turn-continue-orchestrator-design.md` (원본 설계)
- `docs/todo/20260417-multi-turn-continue-orchestrator-implementation-status.md` (현황)
목적: **Phase 4b 정합성 재검토 + Phase 5 재설계 + Phase 6 테스트 계획**을 꼼꼼하게 정리하여
다음 구현 착수 전에 사용자와 합의할 지점을 명확히 한다.

---

## 결정이 필요한 핵심 지점 (우선순위순)

| # | 지점 | 현재 구현/설계 | 논의 포인트 |
|---|---|---|---|
| D1 | **RERUN/ANALYZE_ONLY hydration 위치** | 오케스트레이터가 `snapshot.result_data → SQLResult` 변환 후 Command(update)에 주입 | 설계 원본 §3.5는 "각 하류 노드가 `reference_snapshot`을 직접 선택 읽기"라고 명시. 오케스트레이터에 hydration을 두면 responsibility 경계가 흐려짐 |
| D2 | **Phase 5 하류 노드 수정 범위** | sql_generator / reasoning_preparer / context_interpreter / analyzer / formatter 5개 노드에 snapshot 참조 로직 주입 | 각 노드의 프롬프트·로직·테스트 영향도가 크다. 최소 수정으로 검증 가능한 단위부터 진행할지, 5개 동시 수정할지 |
| D3 | **intent_classifier CONTINUE 판정 기준** | Phase 4b에서 "CONTINUE 판정 + turn_snapshots 존재 시 `CONTINUE_DETECTED` 설정" | turn_snapshots가 비어있으면(첫 CONTINUE 턴) 기존대로 "연속으로 해석" INFER 생성 후 일반 흐름. 이 fallback이 맞는지 재확인 |
| D4 | **fallback 재진입 상한 정책** | trace_log 기반 `_count_orchestrator_reentry` + 상한 1회 | 설계 원본에는 명시 없음. 1회가 적정한지, trace_log 기반 카운트가 견고한지 |
| D5 | **analyze_only 강제 intent 교체** | LLM이 `updated_intent` 반환해도 `analyze_only`면 강제로 `data_analysis`로 교체 | 이중 안전장치인데, HALLUCINATION_GUARD에 명시된 이 규칙이 LLM 판단을 과하게 막지 않는지 |
| D6 | **모호한 updated_intent 폴백 선택** | `data_extraction`으로 폴백 | 설계 원본엔 없던 안전장치. 반대 방향(`data_analysis`)가 더 안전한지도 검토 |
| D7 | **conversation_history 슬라이스 크기** | 최근 6 메시지 | 프롬프트 토큰 예산 vs 맥락 충실도. 4/6/8/10 중 합리적 값 |
| D8 | **LLM streaming/thinking 설정** | llm_call_with_parse_retry 사용 (다른 interpret 노드와 동일 패턴) | Solar Pro 2 70B·Qwen thinking mode 특성상 thinking_modes 설정이 적절한지 확인 |
| D9 | **Phase 6 테스트 범위** | 설계 원본 Step 8: 단위 + 통합 E2E | 실제 LLM 호출 없이 mock으로 어디까지 커버할지, golden set 필요 여부 |
| D10 | **(외부 미해결) `explored_codes` 재조회 시 네이밍 규약** | Phase 3 W3: 정규식(`_CD|_TP|_FG|…`)이 설계 문서에 없음 | 설계 문서에 명시하거나 `resources/config/`로 외부화 필요 |

---

## A. Phase 4b 정합성 재검토

### A-1. 구현된 것 vs 설계 원본 차이

#### A-1-1. 오케스트레이터가 hydration을 수행 (D1)

**현재 구현** (continue_orchestrator.py:381-399):
```python
if route in ("rerun", "analyze_only") and snapshot is not None:
    snap_result_data = getattr(snapshot, "result_data", None)
    if snap_result_data:
        hydration_updates["sql_result"] = SQLResult(
            columns=snap_result_data.get("columns", []),
            rows=snap_result_data.get("rows", []),  # ⚠️ 주의
            ...
        )
```

**설계 원본 §3.5**:
> 하류 노드가 `state.reference_snapshot`을 **직접 읽는** 방식으로 통일한다.

**차이/위험**:
- `snap_result_data.get("rows", [])` 로 `rows`를 꺼내려 하지만, **TurnSnapshot은 rows를 저장하지 않음**(§3.1 rows 제외 규칙). 따라서 복원된 `SQLResult.rows`는 항상 빈 리스트가 되고, RERUN 경로의 `formatter`·ANALYZE_ONLY 경로의 `analyzer`가 **데이터 없이** 동작하는 심각한 버그
- 설계 원본은 `rows`를 **JIT hydration**(checkpoint_dc_messages에서 필요할 때 조회)으로 처리하도록 설계되어 있음
- 오케스트레이터에 hydration을 두는 것 자체가 설계 원칙 위배 — 각 하류 노드가 `reference_snapshot`을 직접 참조하는 구조가 맞음

**논의 포인트**:
- **옵션 A**: 오케스트레이터의 hydration 로직 **완전 제거**, rows가 필요한 경로(RERUN formatter, ANALYZE_ONLY analyzer)는 checkpoint_dc_messages에서 JIT 조회하는 헬퍼 별도 구현
- **옵션 B**: 오케스트레이터가 JIT 조회까지 수행하여 완성된 `sql_result`를 Command(update)에 담음
- **옵션 C (설계 원본)**: 하류 노드별로 필요한 필드만 `reference_snapshot`에서 읽는 방식. RERUN/ANALYZE_ONLY에서 rows가 필요하면 해당 노드가 직접 JIT

설계 원본의 정합성으로 보면 **옵션 C**가 맞음. 현재 구현은 rows 저장 규칙을 위반하는 버그가 있으므로 **반드시 수정 필요**.

#### A-1-2. fallback 재진입 상한

**현재 구현** (continue_orchestrator.py:52, 304-311):
```python
_MAX_ORCHESTRATOR_REENTRY = 1
reentry_count = _count_orchestrator_reentry(state)
if reentry_count >= _MAX_ORCHESTRATOR_REENTRY:
    return _build_fallback_command(..., force_error=True)
```

**설계 원본**: 재진입 상한 명시 없음.

**의견**:
- fallback → intent_classifier 재진입이 CONTINUE_DETECTED 분기로 다시 오케스트레이터로 돌아올 가능성 차단 필요 — 맞는 방향
- 상한 1회는 보수적. "fallback → 신규 처리 → (새 CONTINUE 아님)"이 정상 흐름이므로 1회로 충분
- trace_log 기반 카운트: trace_log는 매 턴 리셋되므로 **턴 내 재진입**만 카운트되어 의도대로 동작
- **수정 불필요**. 다만 상수 이름을 `_MAX_REENTRY_PER_TURN`으로 바꾸면 의도가 더 명확

#### A-1-3. intent 폴백 및 강제 교체

**analyze_only 강제 교체** (continue_orchestrator.py:377-379):
- LLM이 아무리 판단을 잘못해도 `analyze_only` 경로는 `data_analysis` intent로 강제 교체
- HALLUCINATION_GUARD 정책 + `_route_after_execution`(pipeline.py)의 intent 기반 라우팅 오작동 방지
- **이중 안전장치로 합리적**

**updated_intent 폴백** (continue_orchestrator.py:177-178):
```python
if updated_intent_raw not in _INTENT_MAP:
    updated_intent_raw = "data_extraction"
```
- 안전한 기본값이긴 하나, 이전 턴 intent를 `snapshot.intent` 에서 가져오는 쪽이 더 논리적
- **제안**: `snapshot.intent.value` 우선, 없으면 `data_extraction` 폴백

#### A-1-4. conversation_history 슬라이스

**현재 구현**: 최근 6 메시지 (`state.conversation_history[-6:]`)

**의견**:
- user + assistant 번갈아 → 약 3 턴 분량. CONTINUE 맥락상 1턴만 봐도 충분한 경우가 많음
- turn_snapshots 4개 × 요약 7줄 + conversation_history 6줄 = 약 1,000 토큰 추가
- **제안**: 4 (직전 2턴) 또는 6 유지 — 사용자 결정 필요

### A-2. Phase 4b에서 빠진 것(설계 대비 누락)

1. **LLMNode.CONTINUE_ORCHESTRATOR enum 추가** — 에이전트 보고서에서는 등록했다 했으나 실제 `thinking_modes.py` diff 확인 미완
2. **`_VALID_RETURN_TARGETS`에 `continue_orchestrator` 추가** — recovery_agent 등에서 복귀 가능하도록 (설계 §7 Step 3+4 "I3")
3. **모든 분기에서 status 갱신** — fallback의 `QueryStatus.INTENT_CLASSIFIED` 세팅은 OK. 다른 경로는 status 갱신 없음. pipeline 조건부 엣지에서 status를 다시 보는 경우 문제 될 수 있음 — 재확인 필요

### A-3. 결정 필요 항목

| ID | 결정 필요 | 추천 |
|---|---|---|
| A1 | 오케스트레이터 hydration 제거 + JIT 경로 구축 (옵션 C) | **옵션 C** (설계 원본 준수, rows 버그 차단) |
| A2 | fallback 재진입 상한 1회 유지 | **유지** |
| A3 | updated_intent 폴백 `snapshot.intent` 우선 | **수정** |
| A4 | conversation_history 슬라이스 크기 | **논의**: 4 vs 6 |
| A5 | LLMNode/VALID_RETURN_TARGETS/status 누락 점검 | **반드시 확인** |

---

## B. Phase 5 — 하류 노드 snapshot 참조 (재설계)

설계 원본 §7 Step 5 그대로 + 위 A-1-1 옵션 C 결정 반영.

### B-1. 대상 노드 5개와 영향 범위

| 노드 | 변경 규모 | 위험도 |
|---|---|---|
| `sql_generator.py` | 프롬프트 + 로직 | 중 (수정 모드 프롬프트 섹션 추가) |
| `reasoning_preparer.py` | 로직 | 소 (시드 주입만) |
| `context_interpreter.py` | 로직 | 소 (시드 주입만) |
| `analyzer.py` | 로직 (ANALYZE_ONLY 경로 hydration 포함) | 중 (rows JIT 조회 경로 신설 필요) |
| `formatter.py` | 로직 (RERUN 경로 hydration 포함) | 중 (rows JIT 조회 경로 신설 필요) |

### B-2. 각 노드의 수정 상세

#### B-2-1. sql_generator.py (MODIFY 경로)

**설계 §3.5**:
- `reference_snapshot.generated_sql` + `reference_snapshot.sql_explanation` + `state.continue_hint`를 "수정 모드" 섹션으로 시스템 프롬프트에 append
- 기존 `fix_section` 패턴을 재사용
- 이전 SQL을 확정 기준으로 hint 부분만 편집 (전체 스키마 재탐색 금지)

**필요 구현**:
1. `state.reference_snapshot`과 `state.continue_route == MODIFY` 체크
2. 시스템 프롬프트에 "이전 SQL을 기준 SQL로 하고 다음 지시에 따라 수정하라: {continue_hint}" 섹션 삽입
3. 프롬프트 파일 별도 분기(또는 상수) — 기존 `sql_generator_system*.txt`에 modify 모드 section placeholder 추가
4. DB 방언별 파일 5개(base/impala/oracle/postgres/sybase_iq) 동기화 필요

**위험**: 프롬프트 5개 파일 동시 수정 → 기존 회귀 위험. 최소 수정(section placeholder만 추가)으로 제한.

#### B-2-2. reasoning_preparer.py (MODIFY 경로)

**설계 §3.5**: `reference_snapshot.selected_tables`를 초기 탐색 시드로 주입

**필요 구현**:
1. `state.reference_snapshot`이 있으면 초기 `reason.explored_tables`에 `selected_tables` 풀 메타를 seed로 주입
2. 각 항목 `selection_status=SELECTED`, `selection_reason="이전 턴 참조"` 표시
3. 탐색 루프가 이 시드를 우선 활용, 부족하면 자동 추가 탐색

**위험**: 낮음. 기존 탐색 루프는 explored_tables가 비어있지 않은 것을 자연스럽게 처리함.

#### B-2-3. context_interpreter.py (MODIFY 경로)

**설계 §3.5**: `reference_snapshot.explored_codes`(dict[str, CodeMeta])를 초기 지식으로 주입

**필요 구현**:
1. `state.reference_snapshot`이 있으면 초기 `reason.explored_codes`에 snapshot의 dict를 union
2. 기존 dict 시맨틱과 자연 호환

**위험**: 낮음.

#### B-2-4. analyzer.py (ANALYZE_ONLY 경로)

**설계 §3.5**: 오케스트레이터가 hydration한 상태에서 분석 → **옵션 C 결정 시 변경됨**

**옵션 C 기반 재설계**:
1. `state.continue_route == ANALYZE_ONLY and state.reference_snapshot`이면:
   a. `reference_snapshot.result_data`에서 columns/total_count 확보
   b. rows는 **JIT 조회**: checkpoint_dc_messages에서 `user_message_seq`로 이전 턴 assistant 메시지 `metadata.result_data.rows` 가져오기
   c. 이걸로 임시 `SQLResult` 객체를 analyzer 내부에서 구성하여 분석
2. JIT 조회 실패 시 analyzer가 fallback 에러 처리

**위험**: 중. rows JIT 경로 신설은 새 코드이므로 테스트 필요. 분석 결과가 이전과 다를 수 있는 경우(시간 경과로 데이터 변경) 주의.

#### B-2-5. formatter.py (RERUN 경로)

**설계 §3.5**: RERUN 경로 — 이전 결과를 다른 형태로 재포맷 / 시각화 변경

**옵션 C 기반 재설계**:
1. `state.continue_route == RERUN and state.reference_snapshot`이면:
   a. `reference_snapshot.result_data` + `visualization` 기반으로 포맷
   b. rows는 **JIT 조회** (analyzer와 동일 경로)
   c. `state.continue_hint`에 포맷 변경 지시(예: "표로만 보여줘")가 있으면 반영

**위험**: 중. formatter가 원래 state.sql_result를 읽는 구조를 유지하려면 JIT 조회 결과를 sql_result에 주입하거나, formatter가 reference_snapshot을 직접 보는 분기를 추가.

### B-3. 공통 헬퍼 신설 필요

**`src/services/snapshot_rows_hydrator.py`** (제안, 신규):
```python
async def fetch_rows_from_snapshot(
    pool, session_id: str, user_message_seq: int,
) -> list[dict]:
    """checkpoint_dc_messages.metadata.result_data.rows JIT 조회."""
```
- analyzer/formatter가 공통 사용
- 실패 시 빈 리스트 반환 (Partial Hydration 원칙)
- 테스트 대상

### B-4. Phase 5 구현 순서 (의존성)

1. **B-3 헬퍼 신설** (단독 테스트 가능)
2. **B-2-4 analyzer** (ANALYZE_ONLY E2E 검증)
3. **B-2-5 formatter** (RERUN E2E 검증)
4. **B-2-2 reasoning_preparer** (MODIFY 시드)
5. **B-2-3 context_interpreter** (MODIFY 시드)
6. **B-2-1 sql_generator** (MODIFY 프롬프트, 가장 위험 — 마지막)

### B-5. 결정 필요 항목

| ID | 결정 필요 | 추천 |
|---|---|---|
| B1 | 옵션 C(하류 노드 직접 읽기) 채택 | **채택** (설계 원본 준수) |
| B2 | snapshot_rows_hydrator 공통 헬퍼 신설 | **신설** |
| B3 | sql_generator 프롬프트 5 DB 방언 동기화 범위 | **최소 변경** (placeholder 추가) |
| B4 | 5개 노드 한 번에 수정 vs 단계별 | **단계별** (B-4 순서) |

---

## C. Phase 6 — 테스트 계획 재검토

### C-1. 이미 작성된 테스트(Phase 1-4b)

| 파일 | 개수 | 대상 |
|---|---|---|
| `test_turn_snapshot_model.py` | 32 | TurnSnapshot 모델 + state.turn_reset_updates |
| `test_turn_snapshot_store.py` | 30 | DB 복원 Partial Hydration 9 케이스 |
| `test_continue_orchestrator.py` | ? (미확인) | 오케스트레이터 LLM 호출 mock 기반 |

### C-2. 추가 필요 테스트

**단위 테스트**:
| 대상 | 내용 |
|---|---|
| `snapshot_rows_hydrator` | JIT 조회 성공/실패/빈 메시지 |
| `sql_generator` MODIFY 모드 프롬프트 | 수정 섹션이 프롬프트에 포함되는지 |
| `reasoning_preparer` 시드 주입 | 스냅샷 테이블이 explored_tables 초기값에 들어가는지 |
| `context_interpreter` 시드 주입 | 스냅샷 코드가 explored_codes에 merge 되는지 |
| `analyzer` ANALYZE_ONLY 경로 | rows JIT 조회 후 분석 수행 |
| `formatter` RERUN 경로 | reference_snapshot 기반 재포맷 |

**통합 테스트 (E2E)**:
| 시나리오 | 검증 |
|---|---|
| RERUN: "같은 결과 표로 다시" | sql_generator/execute_sql 미호출, formatter 재실행 |
| MODIFY: "지역별로 쪼개줘" | reasoning_preparer/sql_generator/execute_sql 재실행, 시드 활용 확인 |
| ANALYZE_ONLY: "이 결과 분석해줘" | sql_generator/execute_sql 미호출, analyzer만 실행 |
| FALLBACK: 주제 이탈 | intent_classifier 재진입, 신규 턴 처리 |
| multi-turn seq 참조: "아까 처음 뽑았던 것에서" | 가장 오래된 스냅샷 선택 |
| 세션 재접속 복원 | restore_from_db 후 turn_snapshots 복원, CONTINUE 동작 |
| 4턴 FIFO | 5번째 턴 저장 시 가장 오래된 것 제거 |

**실패/엣지 테스트**:
| 케이스 | 검증 |
|---|---|
| turn_snapshots 비어있음 + CONTINUE | 기존 흐름으로 fallback (첫 CONTINUE 턴) |
| LLM JSON 파싱 실패 | fallback 경로 |
| reference_turn_seq가 스냅샷에 없음 | fallback 다운그레이드 |
| fallback 재진입 상한 초과 | error_end |

### C-3. Golden set 갱신 필요 여부

- 기존 NL-to-SQL golden set은 단일 턴 기준
- CONTINUE 시나리오용 별도 catalog 필요할 수 있음: `tests/test_cases/continue_orchestrator_catalog.json`
- 포함 내용: 사용자 발화 + 기대 route + 기대 snapshot 선택 + 기대 hint

### C-4. 결정 필요 항목

| ID | 결정 필요 | 추천 |
|---|---|---|
| C1 | 통합 테스트(E2E)를 실제 LLM 호출 vs mock | **mock 우선**, golden set은 수동 선별 |
| C2 | continue_orchestrator_catalog.json 신설 | **신설** (CONTINUE 전용 regression 확보) |
| C3 | 4턴 FIFO E2E 테스트 포함 | **포함** |

---

## D. 추가 고려 사항

### D-1. conversation_history vs turn_snapshots 역할 구분

현재 설계는 두 가지를 명확히 분리:
- **conversation_history**: 평문 대화(user·assistant·clarification 메시지). intent_classifier/query_normalizer가 참조
- **turn_snapshots**: 9필드 구조화 아카이브. continue_orchestrator/하류 노드가 참조

사용자가 "ConversationHistory 설계까지만"이라 했을 때, 이 ConversationHistory 어댑터 설계가 **별도 미완성 단계**인지 재확인 필요:

- 설계 원본 §3.x에 ConversationHistory 별도 클래스 설계가 있었는가?
- 있다면 그 부분이 미구현 상태인지 확인 필요
- 실제로 `src/models/` 하의 신규 ConversationHistory 클래스가 필요한지, 아니면 `state.conversation_history: list[dict]`로 충분한지

현재 구현은 `state.conversation_history`(기존 list[dict])를 그대로 사용 중 — **사용자 원래 지시 범위와 일치하는지 확인 필요**.

### D-2. 설계 문서와 구현 불일치 정리

Phase 4b 구현에서 설계 원본과 차이 난 부분을 **설계 문서에 역반영**할지, **구현을 설계에 맞출지** 결정 필요:

| 항목 | 설계 | 구현 | 결정 |
|---|---|---|---|
| hydration 위치 | 하류 노드가 직접 | 오케스트레이터 | 구현을 설계에 맞춤 (옵션 C) |
| fallback 재진입 상한 | 미정 | 1회 | 설계에 추가 |
| updated_intent 폴백 | 미정 | data_extraction | 설계에 추가 (snapshot.intent 우선 반영) |
| conversation_history 슬라이스 | 미정 | 최근 6 | 설계에 추가 |

---

## E. 권장 다음 단계

사용자 결정 후 수행할 작업을 우선순위대로:

1. **D1 옵션 C 확정** → 오케스트레이터의 hydration 로직 제거 + snapshot_rows_hydrator 신설
2. **A-2 설계 누락 확인** → LLMNode / _VALID_RETURN_TARGETS / status 갱신 실제 diff 검증
3. **A-3 결정 항목 반영** → 오케스트레이터 코드에 3-4줄 수정
4. **D-2 설계 문서 역반영** → 20260416 설계 문서에 구현 결정 추가 (새 섹션 §9 "구현 결정" 추천)
5. **Phase 5 착수** (B-4 순서대로)
6. **Phase 6 테스트 완성**
7. **최종 종합 검토 + 문서 업데이트**

---

## F. 질문 목록 (사용자에게 확인 필요)

1. **D1 hydration 위치**: 옵션 A(오케 제거)/B(오케가 JIT까지)/C(하류 직접) 중 어느 것? (추천 C)
2. **A-4 conversation_history 슬라이스**: 4 vs 6?
3. **C-1 Phase 4b 테스트 실제 검증**: 지금 `test_continue_orchestrator.py` 실행해서 통과 여부 확인할까?
4. **D-1 ConversationHistory 어댑터**: 사용자가 말한 "ConversationHistory 설계까지"가 정확히 어디까지를 의미했는지 (설계 문서 §3.1 TurnSnapshot 까지? 아니면 별도 ConversationHistory 어댑터 설계?)
5. **D-2 설계 역반영 범위**: 구현 결정을 설계 문서에 반영할지, 별도 문서로 유지할지?
6. **E 순서 타당성**: 권장 순서대로 진행할지, 다른 우선순위로 갈지?
