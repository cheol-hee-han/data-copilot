# Continue 질의 시 이전 성공 SQL/컨텍스트 전달 설계

**작성일**: 2026-04-13
**최종 수정**: 2026-04-14 (전문가 토론 4라운드 결과 반영)
**상태**: 설계 확정 (구현 대기)

---

## 1. 문제 정의

### 현재 동작

사용자가 이전 성공 답변에 대한 후속(continue) 질의를 하면:

1. `intent_classifier`가 `CONTINUE`를 감지하고 `continue_context`(맥락 반영 재작성 질의)를 생성
2. `turn_reset_updates()`가 새 턴 시작 시 **19개 턴 스코프 필드를 전부 초기화**
   - `sql_result`, `reason`(ReasoningState), `analysis_result` 등 모두 빈 값으로 리셋
3. SQL 생성기가 **이전 턴의 SQL을 전혀 참조하지 못함**
4. 모든 추론 과정(테이블 탐색 → 컨텍스트 해석 → SQL 생성)을 **처음부터 다시 수행**

### 핵심 갭

| 항목 | 현재 상태 | 기대 상태 |
|------|-----------|-----------|
| 이전 성공 SQL | 턴 리셋으로 소멸 | CONTINUE 시 프롬프트에 주입 |
| 이전 재작성 질의 | 턴 리셋으로 소멸 | SQL과 함께 의도 전달 |

### 사용자 시나리오 예시

```
[턴 1] "지난달 신규 여신 실행건수 알려줘"
→ SQL 생성 성공, 결과 반환

[턴 2] "거기에 금액도 추가해줘"  ← CONTINUE
→ 현재: 처음부터 테이블 탐색, SQL 생성 (이전 SQL 참조 불가)
→ 기대: 이전 SQL에 금액 컬럼만 추가하는 방식으로 생성

[턴 3] "기간을 최근 3개월로 바꿔줘"  ← CONTINUE
→ 기대: 이전 SQL의 WHERE 날짜 조건만 변경
```

---

## 2. 설계 목표

1. **CONTINUE 질의 시** 이전 성공 SQL + 재작성 질의를 SQL 생성기에 전달
2. **턴 격리 원칙 유지** — 새 턴은 여전히 독립적으로 동작 가능해야 함
3. **최소 변경** — 신규 모델 없이 dict 필드 1개로 구현
4. **프롬프트 토큰 효율** — SQL 1개 + 질의 1줄 수준의 경량 컨텍스트

---

## 3. 검토한 설계 옵션

### 옵션 A: PipelineState에 `last_success_snapshot` dict 필드 추가 ← **채택**

성공 턴 종료 시 `final_sql` + `rewritten_query`를 dict로 캡처하여 세션 수준에 보존.

### 옵션 B: `success_history` 리스트로 N턴 보존

최근 N개 스냅샷 유지. A 이후 확장 경로로 열어둠.

### 옵션 C: Checkpointer 기반 이전 턴 상태 조회

**비추천.** LangGraph 설계 철학(노드는 State만으로 동작)에 위배.

### 옵션 D: conversation_history 강화

**비추천.** conversation_history의 단일 책임(텍스트 이력) 유지가 낫다.

### 옵션 E: explored_use_cases에 이전 SQL 삽입 (검토 후 기각)

기존 파이프라인에 자연스럽게 녹이는 접근이지만, **신호 강도 문제**로 기각.
- reference_sqls의 역할: "패턴을 선별 차용" (여러 참고 사례 중 하나)
- CONTINUE에서 이전 SQL의 역할: "이걸 기반으로 수정" (수정 대상 그 자체)
- 이 둘을 섞으면 LLM이 "어느 게 수정 대상이고 어느 게 참고용인지" 구분 불가

---

## 4. 확정 설계

### 4.1 저장할 값 (검토 근거 포함)

전문가 토론을 통해 각 후보 필드의 필요성을 엄격히 검토했다.

| 필드 | 판정 | 근거 |
|------|------|------|
| **`final_sql`** | **필수** | CONTINUE의 핵심. 수정 대상 SQL이 없으면 의미 없음 |
| **`rewritten_query`** | **필수** | 턴 리셋으로 소멸하는 값. conversation_history에 없음. SQL의 의도를 명시적으로 전달 |
| `original_query` | 불필요 | conversation_history에 이미 존재 |
| `used_tables` | 불필요 (Phase 1) | final_sql에서 파싱 가능. Phase 2 탐색 최적화 시 추가 고려 |
| `result_columns` | 불필요 | final_sql의 SELECT 절에서 LLM이 읽을 수 있음 |
| `result_sample` | 불필요 | 토큰 대비 활용도 낮음. conversation_history에 텍스트로 존재 |
| `row_count` | 불필요 | conversation_history에 존재 |
| `reasoning_summary` | 선택 (Phase 2) | 있으면 판단 근거 전달에 도움. 없어도 SQL+질의로 충분 |
| `code_mappings` | 불필요 | 매 턴 context_retriever가 조회 |
| `intent` | 불필요 | original_query에서 추론 가능 |

### 4.2 모델 정의 및 상태 변경

**모델**: `src/models/result.py`에 추가 (SQLResult·AnalysisResult와 같은 계층)

```python
# src/models/result.py — 기존 파일에 클래스 추가

class SuccessSnapshot(BaseModel):
    """이전 성공 턴의 SQL 스냅샷.

    CONTINUE(연속) 질의 시 sql_generator 프롬프트에 주입되어
    LLM이 이전 SQL을 기반으로 수정/확장할 수 있게 한다.
    """
    final_sql: str = ""
    rewritten_query: str = ""
```

**상태**: `src/agents/state/state.py`의 `PipelineState`에 필드 추가

```python
# state.py — PipelineState
# ── 세션 지속 (턴 리셋 제외) ──
# W: format_response (성공 시)  R: sql_generator (CONTINUE 시)
last_success_snapshot: SuccessSnapshot | None = None
```

- `turn_reset_updates()`에 포함하지 **않음** → 턴 경계에서 보존
- checkpointer 직렬화 등록 필요 (`src/agents/graph/checkpointer.py`의 allowlist에 `SuccessSnapshot` 추가)

### 4.3 저장 (Write) — 어디서, 어떻게

**저장 노드**: `format_response` (파이프라인 종료 직전)

**저장 조건**:
- SQL 실행 성공 (`status == COMPLETED` 또는 `ANALYZED`)
- `state.reason.current_hypothesis`에 성공한 SQL 존재
- 에러 없음

**저장 시점 근거**:
- `execute_sql` 직후는 빈 결과/타임아웃 케이스를 못 거름
- `format_response`는 "검증된 최종 응답" 시점 → "검증된 SQL" 시점과 일치
- 분석(DATA_ANALYSIS)까지 완료된 경우에도 동일하게 캡처

**저장 코드** (`src/agents/nodes/present/format_response.py` 노드 말미):

```python
# 성공 판정 후
updates = { ... }  # 기존 반환값

if state.status in (QueryStatus.COMPLETED, QueryStatus.ANALYZED) \
   and state.reason.current_hypothesis:
    updates["last_success_snapshot"] = SuccessSnapshot(
        final_sql=state.reason.current_hypothesis.sql,
        rewritten_query=(
            state.normalized_query.rewritten_query
            if state.normalized_query else state.preprocessed_input
        ),
    )

return updates
```

### 4.4 활용 (Read) — 어디서, 어떻게

**활용 노드**: `sql_generator` (유일한 소비자)

**활용 조건**:
- `state.is_continuation == True` (intent_classifier가 CONTINUE 판정)
- `state.last_success_snapshot is not None` (이전 성공 턴 존재)

**활용 코드** (`src/agents/nodes/reason/sql_generator.py`의 `_build_agentic_prompt()`):

```python
# replacements dict 구성 부분
if state.is_continuation and state.last_success_snapshot:
    snap = state.last_success_snapshot
    continue_section = CONTINUE_SECTION_TEMPLATE.replace(
        "{prev_rewritten_query}", snap.rewritten_query,
    ).replace(
        "{prev_final_sql}", snap.final_sql,
    )
else:
    continue_section = ""  # 섹션 완전 제거

replacements["{continue_section}"] = continue_section
```

**프롬프트 주입 위치**: `{reference_sqls}` 뒤, `{dead_ends}` 앞 (상세는 §5)

### 4.5 전체 데이터 흐름

```
[턴 N: 성공 턴]
  ...일반 파이프라인 흐름...
  execute_sql (성공)
    └─ state.sql_result 채워짐
  format_response
    └─ 성공 판정 → SuccessSnapshot 생성
        └─ state.last_success_snapshot = SuccessSnapshot(sql, rewritten_query)
            └─ checkpointer(thread_id=session_id)가 PostgreSQL/Memory에 직렬화 저장

[턴 N+1: CONTINUE 질의 도착]
  runner.run_pipeline()
    └─ checkpointer에서 이전 state 로드 → last_success_snapshot 복원됨
  turn_reset 노드
    └─ 19개 턴 스코프 필드 초기화
    └─ last_success_snapshot은 리셋 대상 아님 → 그대로 보존

  intent_classifier 노드
    └─ 대화 히스토리 참조, CONTINUE 감지
    └─ is_continuation=True, continue_context=재작성된 질의

  normalize_query / reasoning 루프 (기존과 동일)
    └─ 테이블 탐색, 메타 조회, 지식 확인 → 신선한 컨텍스트 확보
    └─ 스냅샷은 이 단계에서 참조하지 않음

  sql_generator 노드 ★★
    └─ is_continuation == True AND last_success_snapshot != None 확인
    └─ SuccessSnapshot → CONTINUE_SECTION_TEMPLATE 치환
        → {continue_section} placeholder에 주입
    └─ LLM 호출: 이전 SQL 기반 수정/확장 SQL 생성

  execute_sql → format_response
    └─ 성공 시 last_success_snapshot을 현재 턴 값으로 갱신 (교체)

[턴 N+2 이후]
  마찬가지로 최신 성공 스냅샷이 CONTINUE 시 sql_generator에 전달됨
```

### 4.6 관련 노드 요약

| 노드 | 역할 | 스냅샷 관계 |
|------|------|-------------|
| `turn_reset` | 턴 스코프 필드 초기화 | **보존** (리셋 대상 아님) |
| `intent_classifier` | CONTINUE/NEW 판정 | 참조 안 함 (대화 히스토리만 사용) |
| `normalize_query` | 8슬롯 정규화 | 참조 안 함 |
| `reasoning_preparer`, `context_retriever`, `context_interpreter` | 지식 탐색 | 참조 안 함 (Phase 2 최적화 대상) |
| `sql_generator` | **SQL 생성 — 스냅샷 소비자** | `is_continuation` 시 프롬프트에 주입 |
| `execute_sql` | SQL 실행 | 참조 안 함 |
| `format_response` | **최종 응답 — 스냅샷 저장자** | 성공 시 갱신/생성 |
| `recovery_agent` | 복구 | `is_continuation=False` 리셋 (스냅샷 자체는 유지) |

### 4.4 주입 대상 노드

전문가 토론 결과, Phase 1에서 건드리는 노드는 **2곳**으로 한정:

| 노드 | 역할 | 변경 내용 |
|------|------|-----------|
| **`format_response`** | 캡처 | 성공 시 `final_sql` + `rewritten_query` 저장 |
| **`sql_generator`** | 소비 | CONTINUE 시 프롬프트에 `{continue_section}` 주입 |

나머지 노드(intent_classifier, normalize_query, reasoning 루프)는 **기존과 동일하게 동작**.
reasoning 루프가 그대로 돌아서 테이블 메타·코드값을 신선하게 가져오고,
sql_generator만 "이전 SQL도 참고해"라는 추가 맥락을 받는 구조.

### 4.5 recovery_agent 재진입 처리

recovery_agent 진입 = 이전 시도 실패 → CONTINUE 맥락 무효화.

```python
# recovery_agent 노드 진입부
async def recovery_agent(state: PipelineState) -> dict:
    return {
        "is_continuation": False,  # CONTINUE 컨텍스트 무효화
        # ... 기존 recovery 로직 결과
    }
```

- `is_continuation=False` → `_build_agentic_prompt()`에서 `continue_section = ""` → 스냅샷 섹션 자연 소멸
- `last_success_snapshot` 자체는 유지 (recovery 실패 후 사용자가 다시 후속 질문 시 재활용 가능)

---

## 5. 프롬프트 섹션 설계 (전문가 토론 4라운드 확정)

### 5.1 배치 위치

`{reference_sqls}` 뒤, `{dead_ends}` 앞. `{continue_section}` placeholder로 관리.

```
{reference_sqls}

{continue_section}

{dead_ends}
```

### 5.2 조건부 렌더링

- `is_continuation == True` AND `last_success_snapshot != None` → 텍스트 블록 치환
- 그 외 → **빈 문자열** (섹션 완전 제거)
- "(없음)" 같은 빈 값 렌더링 금지 (Qwen3.5가 혼선을 일으키는 사례 확인됨)

### 5.3 프롬프트 전문

```
## 이전 턴 SQL 스냅샷 (연속 질의 참조용)

이전 턴에서 성공적으로 실행된 SQL이 있습니다. 아래를 참조하되, 판단 기준을 엄격히 지킵니다.

- 이전 턴 질의: {prev_rewritten_query}
- 이전 턴 SQL:
{prev_final_sql}

### 판단 기준: 수정 vs 재설계

아래 체크리스트를 순서대로 확인하여 판단합니다.

**재설계 (이전 SQL 구조를 버리고 새로 작성):**
- 현재 질의가 참조하는 테이블 또는 조인 대상이 이전과 다르다
- 집계 단위(GROUP BY 기준 컬럼)가 이전과 다르다
- 집계 함수 또는 측정 지표가 변경되었다 (COUNT → SUM, 건수 → 잔액)
- 날짜 기준 컬럼이 변경되었다 (실행일 → 약정일, 결산일 → 등록일)
- 파티션 테이블명이 변경되었다 (예: TB_ADW_LNB301M → TB_ADW_LNB302M)
- 이전 SQL에서 참조한 테이블이 현재 [INPUT_CONTEXT]에 확인되지 않는다

위 항목 중 하나라도 해당하면 이전 SQL에 무관하게 [INPUT_CONTEXT]만 근거로 처음부터 작성합니다.

**수정 (이전 SQL 구조를 유지하고 부분 변경):**
- 조인 대상·집계 단위가 동일하고, 조건값(날짜 범위, 코드값, 임계값)만 바뀐 경우
- 출력 컬럼이 추가되거나 제거된 경우

수정이 허용된 경우에도, 이전 SQL의 컬럼명·코드값·테이블명은 현재 [INPUT_CONTEXT]와 대조하여 일치하는 것만 유지합니다. 이전 SQL이 잘못된 가정 위에 작성되었을 수 있으므로, 현재 확인된 정보와 충돌하면 현재 정보를 따릅니다.

특히 아래 케이스는 현재 [INPUT_CONTEXT]로 반드시 재확인합니다:
- 코드값 가정 (예: 이전 SQL이 LOAN_TYPE_CD = '01' 을 사용했으나 현재 메타에서 미확인)
- 테이블 존재 가정 (이전 SQL의 테이블명이 현재 INPUT_CONTEXT에 없는 경우)
- 컬럼 존재 가정 (이전 SQL의 컬럼이 현재 메타에 없는 경우)
```

### 5.4 프롬프트 설계 근거 (전문가 토론 요약)

| 쟁점 | 결론 | 근거 |
|------|------|------|
| "구조 최대한 재사용" vs "참조만" | **조건부 판단** | "재사용"은 LLM이 복붙 후 값만 바꾸는 행동 유발. 체크리스트로 수정/재설계 분기 |
| 금융 도메인 함정 방어 | **재설계 트리거 6개** | 파티션 테이블명, 집계 함수 변경, 날짜 기준 컬럼 변경 등 은행 정보계 특유 케이스 |
| 이전 SQL 오류 가능성 | **구체 케이스 명시** | "잘못된 가정" 추상 지시 → Qwen이 실행 불가. 코드값/테이블/컬럼 3가지로 구체화 |
| recovery 재진입 시 스냅샷 | **is_continuation=False 리셋** | 별도 플래그 불필요. 의미적으로도 "이전 시도 실패 = CONTINUE 무효" |
| CONTINUE인데 스냅샷 None | **graceful fallback** | 일반 질의와 동일 처리. rewritten_query에 이미 맥락 반영되어 있음 |
| 빈 값 렌더링 | **섹션 완전 제거** | Qwen3.5가 "(없음)" 토큰에 불필요하게 반응하는 사례 확인 |

---

## 6. 구현 스펙

### 6.1 `_build_agentic_prompt()` — 스냅샷 주입

```python
# sql_generator.py — replacements dict 구성 부분에 추가
if state.is_continuation and state.last_success_snapshot:
    snap = state.last_success_snapshot
    continue_section = CONTINUE_SECTION_TEMPLATE.replace(
        "{prev_rewritten_query}", snap["rewritten_query"],
    ).replace(
        "{prev_final_sql}", snap["final_sql"],
    )
else:
    continue_section = ""

replacements["{continue_section}"] = continue_section
```

### 6.2 `format_response` — 스냅샷 캡처

```python
# format_response 노드 — SQL 실행 성공 판정 직후
updates = { ... }  # 기존 반환값

if generated_sql and not has_error:
    updates["last_success_snapshot"] = {
        "final_sql": generated_sql,
        "rewritten_query": (
            state.normalized_query.rewritten_query
            if state.normalized_query else state.preprocessed_input
        ),
    }

return updates
```

### 6.3 `recovery_agent` — is_continuation 리셋

```python
# recovery_agent 노드 진입부
updates = { ... }  # 기존 recovery 로직 결과
updates["is_continuation"] = False  # CONTINUE 컨텍스트 무효화
return updates
```

---

## 7. 스냅샷 갱신 규칙

| 상황 | last_success_snapshot 처리 |
|------|---------------------------|
| 데이터 추출/분석 성공 (SQL 실행 완료) | **갱신** (현재 턴 값으로 교체) |
| CONTINUE 성공 | **갱신** (수정된 SQL + 현재 rewritten_query로 교체) |
| CASUAL_TALK / META_QUESTION | **유지** (변경 없음) |
| SQL 실행 실패 | **유지** (이전 성공 유지) |
| recovery_agent 진입 | **유지** (is_continuation만 False로 리셋) |
| 명확화 진행 중 (AWAITING_CLARIFICATION) | **유지** |

---

## 8. 변경 범위 요약

| 구분 | 파일 | 변경 내용 |
|------|------|-----------|
| 상태 | `src/agents/state/state.py` | `last_success_snapshot: dict | None` 필드 추가 |
| 캡처 | `src/agents/nodes/present/format_response.py` | 성공 시 스냅샷 dict 생성 → state 반환 |
| 프롬프트 | `resources/prompts/reason/sql_generator_system.txt` | `{continue_section}` placeholder + 섹션 텍스트 |
| 주입 | `src/agents/nodes/reason/sql_generator.py` | `_build_agentic_prompt()`에서 조건부 치환 |
| 리셋 | `src/agents/nodes/reason/recovery_agent.py` | 진입 시 `is_continuation = False` |
| 직렬화 | `src/agents/graph/checkpointer.py` | dict이므로 별도 등록 불필요 |

---

## 9. 엣지 케이스

### 9.1 실패 후 CONTINUE

```
[턴 1] 성공 → snapshot 저장
[턴 2] 실패 → snapshot 유지 (턴 1 것)
[턴 3] "아까 결과에서 기간만 바꿔줘" → 턴 1 snapshot 참조
```

실패 턴이 끼어도 마지막 성공 스냅샷이 유지되므로 자연스럽게 동작.

### 9.2 DATA_ANALYSIS → DATA_EXTRACTION 전환

```
[턴 1] "지난달 여신 실행 추이 분석해줘" (DATA_ANALYSIS) → 성공
[턴 2] "그 데이터 그대로 뽑아줘" (DATA_EXTRACTION, CONTINUE)
```

스냅샷의 SQL은 동일하므로 intent가 달라져도 SQL 재활용 가능.

### 9.3 CONTINUE인데 스냅샷 None (첫 턴 실패 후)

```
[턴 1] 실패 → snapshot 없음
[턴 2] "그거 다시 해줘" → CONTINUE 판정, snapshot None
```

`continue_section = ""` → 일반 질의와 동일 처리. `continue_context`(재작성 질의)에 이미 맥락이 반영되어 있으므로 SQL 생성 자체는 가능.

### 9.4 스냅샷 무효화 (화제 전환)

- `last_success_snapshot`은 명시적 초기화하지 않음
- `is_continuation`이 False면 `continue_section = ""` → 스냅샷이 있어도 무시
- 다음에 다시 CONTINUE 판정이 나오면 재활용 가능 ("아까 그거랑 비교해줘")

---

## 10. Phase 2 확장 (향후)

### 10.1 탐색 최적화 — PENDING 테이블 시드 주입

CONTINUE 시 이전 SQL에서 파싱한 테이블을 `explored_tables`에 PENDING으로 추가하여 탐색 범위를 좁힘:

- readiness_gate가 "이미 충분하다"고 조기 판단하는 문제를 방지 (CONFIRMED가 아닌 PENDING)
- context_retriever가 해당 테이블 메타를 우선 조회 → 탐색 루프 단축
- Phase 1에서 SQL 생성 품질을 검증한 뒤 추가

### 10.2 reasoning_summary 추가

스냅샷에 `reasoning_summary`를 선택적으로 포함하여 이전 판단 근거를 전달.
Phase 1 효과 검증 후 필요 시 추가.

### 10.3 N턴 히스토리

`last_success_snapshot` → `list[dict]`로 전환. 필드 타입 변경 + append 로직 추가뿐이므로 마이그레이션 비용 낮음.

---

## 11. 검증 방법

### 골든 테스트 시나리오

```yaml
- name: "컬럼 추가 CONTINUE"
  turns:
    - query: "이번 달 신규 수신 계좌 수 알려줘"
      expect: SQL 성공, snapshot 저장
    - query: "금액도 같이 보여줘"
      expect:
        - is_continuation: true
        - SQL이 이전 SQL의 SELECT에 금액 컬럼 추가
        - JOIN/WHERE는 동일

- name: "기간 변경 CONTINUE"
  turns:
    - query: "지난달 여신 실행건수"
      expect: SQL 성공
    - query: "최근 3개월로 바꿔줘"
      expect:
        - SQL의 날짜 조건만 변경
        - 테이블/JOIN 구조 동일

- name: "집계 단위 변경 → 재설계"
  turns:
    - query: "지점별 수신 잔액"
      expect: SQL 성공
    - query: "영업부문별로 바꿔줘"
      expect:
        - GROUP BY 기준 변경 → 재설계 트리거
        - 조직코드 테이블 JOIN 추가 가능

- name: "실패 후 CONTINUE"
  turns:
    - query: "지난달 카드 매출"
      expect: SQL 성공, snapshot 저장
    - query: "신용카드만 보여줘"
      expect: SQL 실패
    - query: "아까 전체 결과에서 법인카드 제외해줘"
      expect:
        - 턴 1의 snapshot 참조
        - 적절한 WHERE 조건 추가

- name: "분석 → 추출 전환 CONTINUE"
  turns:
    - query: "지난 분기 수신 추이 분석해줘"
      expect: DATA_ANALYSIS 성공
    - query: "그 데이터 그대로 뽑아줘"
      expect:
        - DATA_EXTRACTION으로 전환
        - 동일 SQL 재사용 또는 최소 변경
```

---

## 12. 관련 파일 참조

| 파일 | 역할 |
|------|------|
| [state.py](src/agents/state/state.py) | PipelineState 정의, turn_reset_updates() |
| [intent_classifier.py (node)](src/agents/nodes/interpret/intent_classifier.py) | CONTINUE 감지, continue_context 생성 |
| [sql_generator.py](src/agents/nodes/reason/sql_generator.py) | _build_agentic_prompt(), 프롬프트 조립 |
| [sql_generator_system.txt](resources/prompts/reason/sql_generator_system.txt) | SQL 생성기 시스템 프롬프트 |
| [format_response.py](src/agents/nodes/present/format_response.py) | 최종 응답 포맷팅 (스냅샷 캡처) |
| [recovery_agent.py](src/agents/nodes/reason/recovery_agent.py) | 복구 에이전트 (is_continuation 리셋) |
| [runner.py](src/agents/graph/runner.py) | 파이프라인 실행, 초기 상태 구성 |
| [pipeline.py](src/agents/graph/pipeline.py) | 그래프 정의, 노드 라우팅 |
