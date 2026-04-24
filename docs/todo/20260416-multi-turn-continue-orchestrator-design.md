# Multi-Turn CONTINUE Orchestrator 설계

> 작성일: 2026-04-16
> 상태: 논의 중 (설계 초안)
> 관련: 20260413-continue-context-carry-over-design.md, 20260412-turn-boundary-state-reset-design.md

---

## CHANGELOG

### 2026-04-18 후반 재재확정 — 4-way → **3-way 전면 재설계** (이 블록이 최신 진실)

오후 아키텍처 재검토에서 4-way(present/revise/analyze/fresh)의 설계 결함이 드러남:
- `fresh → intent_classifier`는 **상류 회귀**로서 `continue_orchestrator` 재진입 순환 위험
- `present`/`formatter` 직행은 formatter가 시각화 재생성을 실제로 수행하지 않음 (noop)
- `revise`/`reasoning_preparer`는 `normalize_query`를 건너뛰어 질의 재해석이 누락됨

**확정된 3-way 라우팅 (모두 하류 노드 — 상류 회귀 없음, 순환 불가):**

| 명칭 | 하류 노드 | 의미 |
| --- | --- | --- |
| **`redisplay`** | `visualizer` | SQL·결과 동일, 시각화·포맷·엑셀 등 "보이는 방식"만 변경 |
| **`analyze`** | `analyzer` | 기존 결과 해석 (왜/추세/비교/이상치/시사점) |
| **`refine`** | `query_normalizer` | 질의 수정(조건/기간/집계/테이블) → SQL 재생성 |

**제거된 4-way 명칭**: `present`(→redisplay), `revise`(→refine), `fresh`(→error_end).

**판정 불가 폴백**: 빈 스냅샷·LLM 파싱 실패 등은 즉시 `error_end`로 종료. 3-way 모두 하류이므로
재진입 가드(이전 `_MAX_ORCHESTRATOR_REENTRY`·`_count_orchestrator_reentry`)도 제거됨.

**`redisplay` 네이밍 근거**: "reformat"은 포맷 전용 뉘앙스라 시각화 변경(bar_chart↔pie_chart 등)을 포괄하지 못함.
"redisplay"는 시각화+포맷+엑셀 등 "재표시" 전반을 직관적으로 포괄.

**적용 현황 (2026-04-18):**
- `src/models/enums.py` — `ContinueRoute(REDISPLAY/ANALYZE/REFINE)` 3-way로 재정의
- `src/agents/nodes/interpret/continue_orchestrator.py` — `_ROUTE_TO_NODE` 매핑 교체, `_build_error_end_command` 신설, 재진입 가드 제거
- `src/agents/state/state.py` — `route` 필드 docstring 3-way 반영
- `src/agents/models/snapshot.py` — field docstring 3-way 반영
- `src/agents/nodes/present/save_turn_snapshot.py` — C3 스킵 규칙 `REDISPLAY` 기준
- `resources/prompts/interpret/continue_orchestrator_system.txt` — 3-way + 5 few-shot (redisplay/refine/refine-복합/analyze/refine-복합#2)
- `tests/auto/unit/test_continue_orchestrator.py` — 3-way 테스트 + error_end 테스트
- `tests/auto/unit/test_turn_snapshot_model.py` — enum 3 멤버로 축소

§3.1~§3.5 본문의 4-way 표기는 아카이브로 유지되며 **이 CHANGELOG가 우선 적용된다.**

---

### 2026-04-18 전반 재확정 사항 (4-way — 3-way에 의해 대체됨, 아카이브)

설계 초안(§3.2~§3.4)과 실제 운영 계약이 일부 달라졌었다. 아래는 **3-way 재설계 이전 4-way 버전**으로 참고용으로만 유지.

#### 1. Route 명칭 전면 교체 (4-way — 구 버전)

| 구 명칭 (§3.2~§3.4 본문에 잔존) | 구 4-way 명칭 | 의미 |
| --- | --- | --- |
| `RERUN` / `rerun` | `present` (→ 현 `redisplay`) | 같은 결과·다른 표현 (표↔차트, 엑셀, 정렬, 요약/상세) |
| `MODIFY` / `modify` | `revise` (→ 현 `refine`) | SQL 편집 (WHERE/JOIN/GROUP BY/LIMIT/ORDER BY/SELECT/집계/테이블) |
| `ANALYZE_ONLY` / `analyze_only` | `analyze` (유지) | 기존 결과 해석 (왜/추세/비교/이상치/시사점/검증) |
| `fallback` | `fresh` (→ 제거, error_end) | 이전 턴 무관 신규 처리 (CONTINUE_DETECTED 무효화) |

- 근거: 70B → 397B 모델 전제에서 LLM 판정 용이성·의미 경계 명확성·사용자 의도 동사 축 통일
- 적용: 4-way는 `resources/prompts/interpret/continue_orchestrator_system.txt` 에 반영 완료 (2026-04-18 전반). 이후 3-way로 재설계됨.

#### 2. OUTPUT 계약 3 필드 (5 필드 → 3 필드)

```json
{
  "route": "present | revise | analyze | fresh",
  "handoff_note": "다음 노드 LLM이 읽는 지시문 (0~200자, 1~3문장)",
  "reasoning": "판정 근거 (0~500자, 1~3문장, CoT·trace용)"
}
```

**제거된 필드와 근거**:

| 제거 필드 | 근거 |
| --- | --- |
| `reference_turn_seq` | intent_classifier가 이미 `reference_turns: list[str]`(T 라벨)을 산출. 라벨→seq 변환은 orchestrator 노드 코드가 `ConversationHistory.seq_for_label()`로 결정적 수행. LLM이 숫자 할루시네이션할 위험 제거. |
| `updated_intent` | `route` 하나로 하류 분기가 이미 결정됨. intent 문자열 재출력은 중복이며 enum 이탈 위험. 하류 노드가 intent로 분기하는 부분이 있다면 route 기반으로 정리. |

**이름 변경**: `continue_hint` → `handoff_note`. 근거: "어디로(route) / 무엇을(handoff_note) / 왜(reasoning)" 축 통일. "hint"는 참고용 뉘앙스라 필수 지시의 품질 기대치가 낮아지는 문제를 보정.

#### 3. INPUT 3 블록 구조 확정 (상세 §3.2.1 반영 완료)

```text
[A] 해석              (intent_classifier 산출 요약)
[B] 관련 턴 블록       (reference_turns 필터, 각 T는 ── 대화 ── / ── 시스템 처리 내역 ── subsection)
[C] 현재 발화          (state.user_input 원문)
```

placeholder: `{interpretation_block}` / `{reference_turns_block}` / `{current_utterance}` — `continue_orchestrator_user.txt` 반영 완료.

#### 4. Route별 handoff_note 작성 가이드 확정 — §3.2.4 신규 섹션 참조

#### 5. orchestrator·intent_classifier 분리 유지 확정 (흡수안 기각)

397B 기준 팩트 분석 결과:

- 입력 토큰: 분리 평균 6,080 / 흡수 평균 8,580 → 흡수 시 +41%
- 호출 수: 분리 1.3회 / 흡수 1.0회 → 흡수 0.3회 절감
- 평균 지연 추정: 분리 1,408ms / 흡수 1,358ms → 흡수 3.5% 유리
- **결론**: 토큰 비용 증가(+41%)가 지연 이득(3.5%)의 10배 크기 → 분리 우세
- 단, CONTINUE 턴 비중 60% 초과 시 역전 가능 → 서비스 초기 데이터 축적 후 재평가 가능
- 상세: §3.2.5 참조

#### 6. FIFO 4 제한 제거 → 무제한 누적 (§3.1 반영 완료)

#### 7. ConversationHistory.render_for_llm(only=...) 신규 API (§3.2.2 반영 완료)

---

## 1. 문제 정의

### 1.1 현상

사용자가 "이번년도 예금신규 top 10 지점 알려줘" → 명확화("예금신규 건수") → 결과 수신 후
"시각화 해준다면?"이라고 후속 질문 시, 이미 해소된 "예금신규" 의미를 다시 물어보는 문제.

### 1.2 근본 원인 3가지

**원인 A: resolved_signals 턴 경계 소실**
- `turn_reset_updates()`가 매 턴 `resolved_signals: []`로 초기화
- `build_clarification_context()`가 현재 turn_id만 필터링 (이중 차단)
- 결과: 이전 턴 명확화 Q&A가 normalizer에 전달되지 않음

**원인 B: conversation_history에서 명확화 메시지 의도적 제외**
- `intent_classifier.py`의 `_format_history()`가 `type="clarification"` 메시지를 필터링
- 수정 완료 (2026-04-16): 필터 제거, `[명확화]` 태그로 구분하도록 변경
- 단, 이것만으로는 문제 해결 불충분 (normalizer는 conversation_history를 참조하지 않음)

**원인 C: CONTINUE 턴의 구조적 한계**
- CONTINUE든 NEW든 무조건 reasoning_preparer부터 전체 파이프라인 재실행
- 이전 턴의 SQL, 테이블 선택, 추론 결과가 모두 소실된 상태에서 처음부터 재시작
- "시각화 해줘"처럼 이전 SQL 재실행만 필요한 경우에도 불필요한 전체 재탐색

### 1.3 영향 범위

명확화 반복 문제를 넘어서, CONTINUE 시나리오 전반의 맥락 단절 문제:
- 이전 SQL을 참조하지 못해 단순 수정도 처음부터 재생성
- 이전 테이블/컬럼 선택을 버리고 다시 메타 검색
- 이전 분석 인사이트를 활용하지 못함
- 레이턴시/비용 낭비 (불필요한 LLM 호출 4~6회)

---

## 2. 리서치 근거

### 2.1 학술 논문

| 논문 | 핵심 기여 | Data Copilot 적용 |
|------|----------|-------------------|
| CoE-SQL (NAACL 2024) | 이전 SQL + edition chain이 핵심 컨텍스트. 최대 4턴 참조가 최적 | TurnSnapshot에 SQL 보존. **저장 FIFO는 제거**하고 intent_classifier가 산출한 `reference_turns`로 참조 범위를 제한 (B 블록 상한 3) |
| Track-SQL (NAACL 2025) | History Schema Store로 이전 턴 테이블/컬럼 누적 | TurnSnapshot에 selected_tables/columns 보존 |
| TRUST-SQL (arXiv 2025) | Verified Schema Knowledge만 보존, full schema 주입 기각 | 검증된 테이블 메타만 스냅샷에 포함 |
| SParC (ACL 2019) | 4종 컨텍스트 관계: Theme-Entity(48%), Refinement(33%), Theme-Property(10%), Answer-Refinement(8%) | 라우팅 카테고리 설계 근거 |

### 2.2 산업 패턴

| 출처 | 패턴 | 적용 |
|------|------|------|
| JetBrains Research (2025) | Observation Masking > LLM Summarization (비용 -52%, 성능 동등) | 스냅샷은 코드로 추출, LLM 요약 미사용 |
| Anthropic Context Engineering (2025) | Tool Result Clearing + JIT Retrieval + Structured Note-taking | raw 결과 제거, RAG는 매 턴 재조회, 스냅샷은 구조화 |
| LangGraph Dual-Channel State | Persistent(누적) vs Ephemeral(턴 내 소멸) 분리 | turn_snapshots=Persistent, reasoning=Ephemeral |
| LangGraph Command 패턴 | `Command(update=..., goto=...)` 로 상태 주입 + 라우팅 동시 처리 | continue_orchestrator 구현 방식 |

---

## 3. 설계안

### 3.1 TurnSnapshot — 턴 완료 시 구조화 보존

**생성 시점**: `format_response` 직후 (새 노드 `save_turn_snapshot`)
**생성 방식**: 코드로 state 필드에서 **직접 추출** (추가 LLM 호출 0건 — JetBrains Observation Masking 패턴)
**보존 위치**: `PipelineState.turn_snapshots` (세션 지속, turn_reset 대상 아님)
**보존 개수**: **무제한 누적** (세션 전체). 저장 단계 FIFO 제한을 두지 않는다.

> **변경 근거 (2026-04-18)**: 이전 안은 CoE-SQL(NAACL 2024) "4턴 초과 시 성능 저하" 연구를 근거로 FIFO 4개 제한을 두었으나, 실사용에서 사용자가 "아까 **처음** 뽑았던 거" 같이 **오래된 턴을 명시 참조**하는 케이스가 존재한다. 저장 단계에서 4개로 잘라내면 해당 턴을 복원 불가 → FIFO는 `save_turn_snapshot`의 잘못된 위치의 책임이었다.
>
> **올바른 책임 배치**:
>
> - **저장 단계(save_turn_snapshot)**: 제한 없이 누적. 세션·DB 모두 전 턴 보존.
> - **프롬프트 주입 단계(continue_orchestrator)**: intent_classifier가 산출한 `reference_turns`(관련 T 라벨 배열, 상한 3)로 필터링 → 해당 턴만 상세 주입. CoE-SQL의 "4턴" 경험치는 이 주입 한도의 참고값으로 활용.
>
> 이로써 "모든 이전 턴 참조 가능" + "프롬프트 토큰 효율" 양립.

#### 설계 원칙 — "사용자가 이어가는 턴" 관점

CONTINUE 턴은 사용자가 이전 답변을 보고 이어가는 턴이다. 따라서 스냅샷은:

1. **사용자 발화·어시스턴트 답변 텍스트는 ConversationHistory가 전담**한다. 스냅샷은
   중복 저장하지 않고 `user_message_seq`를 키로 ConversationHistory와 1:1 매핑된다.
2. **사용자가 볼 수 없는 시스템 내부 상태 중 CONTINUE 재주입에 꼭 필요한 것만 보존**한다
   (intent, 실행 SQL 및 설명, result_data, visualization, 선택 테이블·코드 메타, INFER 시그널).
3. **풀 메타 객체로 보존**한다. `selected_tables`·`explored_codes`는 이름이 아닌 MongoDB 메타
   전체 객체로 저장한다 — 세션 내에서는 state 풀 객체 복사, DB 복원 시에는 이름으로 재조회
   하여 풀 객체를 채운다. CONTINUE 재주입 시 매번 재탐색하지 않기 위함.
4. **JIT retrieval·에페머럴 정보는 미보존** (RAG knowledge, reasoning scratchpad, full SQL
   assumptions).
5. **사용자 명확화 Q&A(ASK)는 ConversationHistory에 `[명확화]` 태그로 존재**하므로 스냅샷은
   자동 추론(INFER) 시그널만 보존한다 (중복 제거).

#### TurnSnapshot 필드 (9개)

```python
class TurnSnapshot(BaseModel):
    # ── 매핑 키 ──
    user_message_seq: int               # T{n} ↔ 스냅샷 매핑 키
                                         # (해당 user 턴 메시지의 checkpoint_dc_messages.seq)

    # ── 라우팅/재실행 ──
    intent: IntentType                  # present 시 _route_after_execution 오라우팅 방지 (C2)
    generated_sql: str | None           # reason.validated_sql — revise 모드 기준 SQL
    sql_explanation: str                # reason.sql_explanation — 구조화된 설명

    # ── 사용자가 본 구조화 데이터 (rows 제외 — JIT hydration) ──
    result_data: dict | None            # formatter._build_result_data 결과에서 rows 제외
                                         # 보존: columns, column_formats, total_count, displayed_count
                                         # rows는 checkpoint_dc_messages.metadata.result_data.rows 단일 원천
                                         # present/analyze 진입 시 오케스트레이터가 해당 턴만 JIT fetch
    visualization: dict | None          # Visualization 전체 (chart_type, config, series…)

    # ── CONTINUE 재사용을 위한 풀 메타 ──
    selected_tables: list[TableMeta]    # reason.explored_tables 중 SELECTED 된 풀 객체
                                         # (세션: 복사 / DB 복원: lookup_table_meta 동시 fan-out)
    explored_codes: dict[str, CodeMeta] # SQL에서 사용된 코드 컬럼명 → CodeMeta 풀 객체
                                         # (세션: 복사 / DB 복원: lookup_code_meta 동시 fan-out)
                                         # dict 시맨틱 유지 이유: 하류 노드(sql_generator·formatter·
                                         # context_interpreter 등)가 이미 `reason.explored_codes`를
                                         # `dict[str, CodeMeta]`로 소비 중이므로 변환 계층 불필요

    # ── 자동 추론 시그널 ──
    inferred_signals: list[dict]        # 자동 추론(INFER)만 보존
                                         # — ASK는 ConversationHistory [명확화] 태그로 커버
                                         # — intent_classifier "연속으로 해석" INFER는 제외 (I4, 아래)
```

**사용자 발화·답변은 별도 필드 없음 — ConversationHistory 경로로 참조**:

| 필요 데이터 | 조회 경로 |
| --- | --- |
| 해당 턴 user_query | `conversation_history.get_user_message(seq=user_message_seq).content` |
| 해당 턴 assistant_response | `conversation_history.get_assistant_message_after(seq=user_message_seq).content` |

**제외 필드와 근거**:

| 제외 필드 | 근거 |
| --- | --- |
| user_query / assistant_response | ConversationHistory 에 이미 존재 — 중복 저장 제거 |
| turn_id | 세션 한정 ULID. 재시작 시 소실. `user_message_seq`로 식별 대체 |
| processed_query | intent_classifier가 다음 턴에 continue_context 재생성 — 중복 |
| sql_assumptions | 대부분 `sql_explanation` 본문에 포함됨 — 별도 보존 효용 낮음 |
| inferred_knowledge | Anthropic JIT retrieval 원칙 — 매 턴 fresh 조회가 더 정확 |
| analysis_result (구조 객체) | assistant_response 마크다운에 `build_analysis_report`로 통합 렌더 — 중복 |
| result_summary | assistant_response 첫 줄/요약 섹션과 동일 — 중복 |
| result_row_count | `result_data.total_count`에서 도출 가능 — 파생 |
| resolved_signals (ASK 포함) | ASK는 ConversationHistory `[명확화]` 태그로 주입됨 — INFER만 보존 |
| intent_classifier "연속으로 해석" INFER | 매 CONTINUE 턴마다 생성되므로 스냅샷에 축적 시 매 턴 반복 노출. `source_node == "intent_classifier"`인 INFER는 `inferred_signals` 구성 시 필터 제외 (I4) |

#### 두 가지 생성 경로

**세션 내 생성** (턴 완료 직후, `save_turn_snapshot` 노드):

`state.reason.explored_tables`·`state.reason.explored_codes`는 이미 풀 메타 객체 리스트이므로
SELECTED·USED 필터만 적용하여 **그대로 복사**한다. MongoDB 재조회 불필요.

**DB 복원** (재접속 시, `TurnSnapshotStore.restore_from_db`):

`checkpoint_dc_messages` + metadata에서 읽어오는 것은 **이름/코드 컬럼명뿐**이므로 복원 로직이
`src/agents/nodes/reason/tools.py`의 기존 단건 함수(`lookup_table_meta`·`lookup_code_meta`)를
`asyncio.gather`로 **동시 fan-out** 호출하여 풀 메타를 채운다. 별도 mongo 배치 API를 만들지
않고 기존 도구를 재사용한다.

```python
import asyncio
from src.agents.nodes.reason.tools import lookup_table_meta, lookup_code_meta

async def restore_from_db(
    pool, thread_id: str, limit: int | None = None,
) -> list[TurnSnapshot]:
    # limit=None이면 전체 복원. 호출측에서 명시 제한할 수도 있으나 기본은 무제한.
    rows = await fetch_last_successful_turns(pool, thread_id, limit)
    # 전체 누적 턴에서 이름 집합을 수집 → 유니크로 중복 호출 제거
    all_tables = {name for r in rows for name in _table_names(r.metadata)}
    all_codes  = {col  for r in rows for col  in _code_columns(r.executed_sql)}

    # 동시 fan-out — 실패는 턴 단위가 아니라 "해당 이름"만 풀 메타 누락 처리
    table_results = await asyncio.gather(
        *[lookup_table_meta(n) for n in all_tables],
        return_exceptions=True,
    )
    code_results = await asyncio.gather(
        *[lookup_code_meta(col) for col in all_codes],
        return_exceptions=True,
    )
    table_index = _index_results(all_tables, table_results)  # 실패 시 해당 키 누락
    code_index  = _index_results(all_codes, code_results)
    # lookup_*_meta 반환은 list[dict] (tools.py 현행 시그니처)이므로
    # _build_snapshot 내부에서 TableMeta.from_meta(dict)·CodeMeta(**dict) 변환 1회 수행
    return [_build_snapshot(r, table_index, code_index) for r in rows]
```

> **부분 복원(Partial Hydration) 원칙**: 특정 테이블/코드 메타 1~2건이 조회 실패해도
> **해당 턴 스냅샷 자체는 유지**한다. 누락된 풀 객체만 제외하고 나머지로 CONTINUE를 지원한다.
> 전체 턴이 일괄 실패하거나 핵심 테이블(SQL의 FROM 테이블)이 전부 누락된 경우에만 해당 턴을
> 제외하고 경고 로그를 남긴다. 최악의 경우 CONTINUE 판정이 fallback(전체 재탐색)으로 빠진다.

#### 공통 복원 쿼리 조건

복원 대상 턴을 고르는 기본 조건은 다음과 같다. **실패/취소/비데이터 턴은 복원 대상에서 제외**
하여 CONTINUE가 잘못된 기준 SQL로 판단하지 않게 한다.

```sql
WHERE thread_id = :thread_id
  AND role = 'assistant'
  AND message_type = 'normal'
  AND status = 'success'
  AND executed_sql IS NOT NULL
ORDER BY seq DESC
LIMIT :limit
```

`user_message_seq`는 복원된 assistant 턴의 직전 user 턴(`role='user' AND message_type='normal'`)의
`seq`로 매핑한다.

#### DB 복원 가능성 (9개 필드 전부 복원 가능)

| # | 필드 | DB 원천 / 복원 방법 | 복원 |
| --- | --- | --- | --- |
| 1 | user_message_seq | 직전 user `normal` 메시지의 `seq` | ✅ |
| 2 | intent | `intent` 컬럼 (문자열 → IntentType) | ✅ |
| 3 | generated_sql | `executed_sql` | ✅ |
| 4 | sql_explanation | `sql_explanation` | ✅ |
| 5 | result_data (rows 제외) | `metadata.result_data` 중 columns/column_formats/total_count/displayed_count만 복원. `rows`는 **보존하되 지연 fetch** — present/analyze 진입 시 오케스트레이터가 해당 턴의 `metadata.result_data.rows`를 JIT 조회하여 `state.sql_result`로 hydrate | ✅ |
| 6 | visualization | `metadata.visualization` | ✅ |
| 7 | selected_tables | 이름 = `metadata.process_summary.context.tables[*].name` (used=true). 풀 메타 = **`lookup_table_meta` 동시 fan-out** | ✅ |
| 8 | explored_codes | 코드 컬럼명 = `executed_sql` 파싱 결과. 풀 메타 = **`lookup_code_meta` 동시 fan-out** | ✅ |
| 9 | inferred_signals | `metadata.process_summary.ai_decisions.inferences` (단, `source_node == "intent_classifier"` INFER는 제외) | ✅ (아래 1줄 확장 후) |

#### 필요한 코드 변경 — process_summary_builder 1줄 추가

[process_summary_builder.py:196-201](src/services/process_summary_builder.py#L196-L201)의 inferences 구성에
`reasoning` 필드를 추가한다:

```python
inferences.append({
    "question": s.question,
    "value": s.inferred_value or "",
    "reasoning": s.reasoning or "",   # ← 추가 (1줄)
    "source_node": s.source_node,
})
```

**근거**: [clarification_context.py:75-81](src/agents/utils/clarification_context.py#L75-L81)의
INFER 렌더 포맷(`- {question} → {inferred_value} (근거: {reasoning})`)이 그대로 재구성되어
CONTINUE 재주입 품질이 온전해진다.

> **설계 판단**: runner.py metadata 확장은 불필요하다. 기존 `content`, `executed_sql`,
> `sql_explanation`, `metadata.{result_data, visualization, process_summary}` + MongoDB 재조회로
> 전부 커버되며, process_summary_builder의 1줄 추가만으로 복원 품질이 확보된다.

#### 세션 한정/비보존 항목

- **turn_id**: 원본 ULID는 재시작 시 소실. `user_message_seq`가 식별자 역할 대체.
- **추론 scratchpad, RAG knowledge_items, SQL 전체 assumptions**: 설계상 보존하지 않음
  (토큰 비용 ↔ CONTINUE 효용 저울질 결과).
- **result_data.rows**: 스냅샷 객체에 보관하지 않는다. `checkpoint_dc_messages.metadata.result_data.rows`를
  **단일 원천**으로 삼고, present/analyze 진입 시 오케스트레이터가 해당 턴의 `seq`로 **1회 JIT 조회**하여
  `state.sql_result`에 hydrate. 이유는 체크포인터 JSONB 용량 방어(§체크포인트 용량 원칙) — 스냅샷은
  노드 완료마다 writes 테이블에 누적 기록되므로 rows를 포함하면 세션당 수 MB가 빠르게 증가한다.
  `rows` 보존 상한은 `ui_result_max_rows` (기본 500). 초과 행은 별도 결과 캐시 레이어(Redis)가 필요하며
  현 스코프 밖이다.

#### 오케스트레이터 주입 형태

상세 구조는 §3.2.1 "continue_orchestrator INPUT 구조"에서 다룬다. 본 절은 **보존 스키마**에 집중한다.

### 3.2 Continue Orchestrator — 라우팅 전담

**위치**: `intent_classifier` 직후, `normalize_query` 이전
**역할**: CONTINUE 판정 후 route 결정 + handoff_note 작성 (라우터·지시문 작성자)
**LLM 호출**: 1회 (3 필드 출력: route / handoff_note / reasoning — §3.2.3)

**정규화·분류 통합은 기각** (§3.2.5 팩트 분석 결과, +41% 토큰 페널티):

- 오케스트레이터에 책임 과다(분류·해석·라우팅 혼재)
- reference_snapshot 주입 타이밍이 CONTINUE 판정 이전으로 당겨져 NEW 턴에서도 토큰 낭비
- 유지보수: 한쪽 수정이 다른 쪽 품질을 흔듦

```text
intent_classifier
  ├─ [NEW]       → normalize_query → reasoning_preparer → ...
  └─ [CONTINUE]  → continue_orchestrator
                     ├─ present  → formatter         (SQL·rows 재사용)
                     ├─ revise   → normalize_query(+스냅샷) → reasoning_preparer(시드)
                     ├─ analyze  → analyzer          (기존 rows 해석)
                     └─ fresh    → intent_classifier (신규 처리, 재진입 상한 1)
```

#### 3.2.1 continue_orchestrator INPUT 구조

**목적**: orchestrator LLM이 `(route, handoff_note, reasoning)` 3 필드(§3.2.3)를 판정·생성하는 데 필요한 최소 정보를 **구조화된 3 블록**으로 주입한다.

**호출 시점 제약**: `intent_classifier` 직후 분기 진입. 이 시점에 현재 턴의 `reason.*` / `sql_result` / `visualization` / `normalized_query` 등은 모두 비어있다(turn_reset 직후). 이전 턴 데이터는 반드시 `turn_snapshots` 또는 `conversation_history` 경유.

**입력 축약 원칙**:

1. **관련 턴만 주입** — intent_classifier가 산출한 `reference_turns: list[str]`(예: `["T2", "T4"]`, 상한 3)에 해당하는 T 블록만 전체 상세 렌더. 무관 T는 주입 제외.
2. **해석 섹션 선두 배치** — route 판정 단서(`intent`, `reference_turns`, `needs_analyzer`, `pending_signals`)는 프롬프트 앞에 두어 LLM이 초점을 먼저 맞추게 한다.
3. **T 블록 내부 subsection 분리** — 각 T는 `── 대화 ──`(UI 재현)와 `── 시스템 처리 내역 ──`(SQL·매핑·rows 샘플)로 나눠 역할을 명시. 시각화·결과 메타는 "시스템 처리 내역"에만 배치(사용자 발화와 섞지 않음).
4. **inferred_signals 전문 및 AI 추론 원문 제외** — orchestrator는 route/hint 결정자이므로 과거 추론 내부 상태는 필요 없음. 하류 노드가 snapshot에서 직접 참조.
5. **rows 샘플은 JIT 1~3행** — snapshot은 rows를 보유하지 않음(§3.1). orchestrator 프롬프트 조립 시 `checkpoint_dc_messages.metadata.result_data.rows` 상단 1~3행만 가져와 "결과 성격 파악용"으로 인라인 삽입. 풀 hydration은 여전히 하류 노드 책임.
6. **컨벤션 3단 계층** — 블록(`## A./B./C.` H2) > 소그룹(`### 한글` H3) · 턴(`▶ T_n`) > subsection(`── 텍스트 ──`). H2는 프로젝트 다수파(recovery/sql/context system.txt), 턴은 헤더 아닌 리스트 헤드라인으로 처리해 H3 소그룹과 레이어 충돌을 방지한다.

##### 프롬프트 구조 (3 블록)

블록 경계는 `## A.`·`## B.`·`## C.` markdown H2로 표기한다. 시스템 프롬프트 `[TASK]`/`[RULES]`/`[EXAMPLES]` 대괄호 최상위 섹션과 기호가 달라 "지시 vs 데이터" 경계가 명확하다. 계층 기호 정책:

| 레벨 | 기호 | 예 |
| --- | --- | --- |
| 블록 | `## A.` / `## B.` / `## C.` (H2) | `## A. 해석 (intent_classifier 산출 — 판정 단서)` |
| A 블록 내부 소그룹 | `### 한글 제목` (H3) | `### 질의 유형·범주`, `### 판정 근거` |
| B 블록 내부 턴 헤드라인 | `▶ T_n` (들여쓰기 + 삼각형) | `▶ T2` |
| subsection | `── 텍스트 ──` 이중 하이픈 | `── 대화 ──`, `── 시스템 처리 내역 ──` |

`▶ T_n`은 markdown 헤더가 아니어서 `###` 소그룹과 동일 레이어 오해를 만들지 않는다. 턴이 B 블록 하위 컨테이너임을 기호 차별화로 보장.

```text
## A. 해석 (intent_classifier 산출 — 판정 단서)

### 질의 유형·범주
- 질의 유형: 데이터 추출 (data_extraction)
- 질의 범주: {query_category}
- 맥락 결합 발화: {preprocessed_input}

### 판정 근거
- 재집계 필요: 아니오 (needs_analyzer: false)
- 연속 처리 메모: {continue_context}    ← 비어있으면 행 생략

### 연속성 — 참조 턴
- 참조 턴: ["T2", "T4"]                  ← 빈 배열이면 `(없음 — latest_t_label 폴백 또는 fresh)`

### 모호성 신호                          ← pending_signals 없으면 소그룹 전체 생략
- {question} → {inferred_value} ({source_node})

### 분석 요건                            ← analysis_query 없으면 소그룹 전체 생략
- 분석 초점: {analysis_query}

## B. 관련 턴 블록 (reference_turns 필터 적용, 오래된 → 최근)

▶ T2
── 대화 ──
사용자: 2024년 3월 여신 실행금액 상품별로 뽑아줘
시스템: 상품별 여신 실행금액 12건 조회 완료 (담보 1.2억 등)
── 시스템 처리 내역 ──
시각화: bar_chart
SQL: SELECT PROD_CD, SUM(EXEC_AMT) FROM LN_ACCT ... GROUP BY PROD_CD
테이블:
- LN_ACCT(여신계좌) — 컬럼: PROD_CD(상품코드), EXEC_AMT(실행금액), EXEC_YM(실행년월), REGION_CD(지역코드)
- PROD_MST(상품마스터) — 컬럼: PROD_CD(상품코드), PROD_NM(상품명)
코드: LN_PROD_CD=상품코드(01=담보, 02=신용, 03=보증)
결과 샘플 (상위 3 / 총 12행):
  상품명   | 실행금액   | 건수
  담보대출 | 120000000 | 45
  신용대출 | 82000000  | 31
  보증대출 | 45000000  | 20

▶ T4
── 대화 ──
...
── 시스템 처리 내역 ──
...

## C. 현재 발화

사용자: 지역별로 쪼개줘
```

##### 필드별 조립 규칙 (주입 데이터 원천)

| 블록 | 소그룹/subsection | 항목 | 원천 | 비고 |
| --- | --- | --- | --- | --- |
| A | 질의 유형·범주 | 질의 유형 | `state.intent` | 한글 라벨 + `(영문 원값)` 병기 |
| A | 질의 유형·범주 | 질의 범주 | `state.query_category` | 공백이면 `(미지정)` |
| A | 질의 유형·범주 | 맥락 결합 발화 | `state.preprocessed_input` | 필수 |
| A | 판정 근거 | 재집계 필요 | `state.needs_analyzer` | `예/아니오 (needs_analyzer: bool)` 병기. 참고용, override 금지 |
| A | 판정 근거 | 연속 처리 메모 | `state.continue_context` | 공백이면 행 생략 |
| A | 연속성 — 참조 턴 | 참조 턴 | `state.reference_turns` | 빈 배열이면 `(없음 — latest_t_label 폴백 또는 fresh)` |
| A | 모호성 신호 | 항목 | `state.pending_signals` 중 INFER/ASK | 빈 리스트면 소그룹 생략 |
| A | 분석 요건 | 분석 초점 | `state.analysis_query` | 공백이면 소그룹 생략 |
| B | 턴 헤드라인 | `▶ T_n` | `ConversationHistory.label_of(seq)` | H3 아님(리스트 헤드라인) |
| B | `── 대화 ──` | 본문 | `ConversationHistory.render_for_llm(only=reference_turns)` | **새 API** (§3.2.2) |
| B | `── 시스템 처리 내역 ──` | 시각화 | `snapshot.visualization.chart_type` | 없으면 `(없음)`. 대화 subsection에 넣지 않음 |
| B | `── 시스템 처리 내역 ──` | SQL | `snapshot.generated_sql` | 1줄 압축. 비데이터 턴은 `(없음 — 비데이터 턴)` |
| B | `── 시스템 처리 내역 ──` | 테이블 | `snapshot.selected_tables[*]` | 다줄 리스트 `- TB명(한글명) — 컬럼: col(한글), ...` (컬럼 최대 6개) |
| B | `── 시스템 처리 내역 ──` | 코드 | `snapshot.explored_codes` | `컬럼명=설명(01=라벨, ...)`, 없으면 `(없음)` |
| B | `── 시스템 처리 내역 ──` | 결과 메타 | `snapshot.result_data.columns/total_count` | |
| B | `── 시스템 처리 내역 ──` | 결과 샘플 | `checkpoint_dc_messages.metadata.result_data.rows[:N]` JIT | N=1~3, 컬럼 5개 상한 |
| C | — | 현재 발화 | `state.user_input` | `사용자: {원문}` |

##### 제외 필드와 근거

| 제외 필드 | 근거 |
| --- | --- |
| `conversation_history` 전체 렌더 | `preprocessed_input`이 이미 맥락 결합. 관련 T만 B 블록에서 렌더하므로 전체 주입은 3중 노출 유발 |
| `snapshot.inferred_signals` 원문 | `sql_explanation`에 추론 결과가 이미 반영됨. 원문은 판단 혼란 + 토큰 낭비. 하류 노드(sql_generator)가 필요 시 직접 참조 |
| `snapshot.sql_explanation` 전체 | 한 줄 요약만 대화 subsection에 포함. 전체 본문은 하류 노드 소관 |
| `reason.*` / `sql_result` / `visualization` 등 현재 턴 리셋 필드 | 시점상 비어있음 |
| 비관련 T의 snapshot | `reference_turns` 필터 기준으로 제외 |

##### reference_turns 폴백 규칙

- intent_classifier가 `reference_turns`를 빈 배열로 반환한 경우:
  - `turn_snapshots`이 존재하면 → `ConversationHistory.latest_t_label()`을 단독 참조로 사용 (최근 턴)
  - `turn_snapshots`이 비어있으면 → orchestrator는 `fallback` route로 강제 다운그레이드 (참조 대상 없음)
- intent_classifier가 존재하지 않는 T 라벨을 반환한 경우:
  - `ConversationHistory.has_turn()=False` → 해당 라벨 제외 후 나머지로 진행. 전부 실패 시 위와 동일 fallback.

##### 토큰 관리 지침

- B 블록 1개 턴 ≈ 14~26줄 (시각화 1줄 + SQL 1~2줄 + 테이블 2~5줄 + 코드 0~2줄 + 결과 샘플 3~5줄 + 대화 3~6줄). `reference_turns` 상한 3 → B 블록 최대 42~78줄.
- A 블록 ≈ 12~18줄 (### H3 소그룹 3~5개). C 블록 1줄.
- 전체 ≈ 55~100줄 (~1,400~2,400 토큰). 폐쇄망 70B 모델도 안정 처리 가능.
- rows 샘플은 **최대 3행 × 너무 넓은 결과는 컬럼 5개까지만** 잘라서 삽입. 추가 행/컬럼은 `... (+N행, +M컬럼)`으로 생략 표기.
- 테이블 컬럼 나열은 테이블당 최대 5개 (`col1(한글1), col2(한글2), ...`). 초과 시 `... (+N컬럼)` 생략.

#### 3.2.2 ConversationHistory 필터 API (신규)

`20260417-conversation-history-class-design.md` §4의 공개 API에 **관련 T 필터 렌더**를 추가한다.

```python
class ConversationHistory(BaseModel):
    ...

    def render_for_llm(
        self,
        only: list[str] | None = None,   # ← 추가: ["T2", "T4"] 같은 T 라벨 필터
    ) -> str:
        """only가 지정되면 해당 T 라벨에 속한 메시지만 렌더. None이면 전체."""
```

- `only=None` 경로: 기존 동작 유지 (intent_classifier가 사용).
- `only=["T2", "T4"]` 경로: continue_orchestrator가 사용. 해당 T에 속한 user/clarification/assistant 메시지만 순서 유지하며 렌더.
- 내부 구현: T 라벨링 시 이미 각 메시지가 `t_label`을 보유하므로 단순 필터 1회.
- 존재하지 않는 라벨은 조용히 스킵 (로깅만).

이 API가 20260417 설계 §4에 미정의였던 "확장 지점" 중 본 오케스트레이터가 요구하는 첫 소비 API이다.

#### 3.2.3 continue_orchestrator OUTPUT 구조 (3 필드)

**목표**: route 선정 + handoff_note 작성 + reasoning trace, 그 이상은 담지 않는다.

```json
{
  "route": "present | revise | analyze | fresh",
  "handoff_note": "다음 노드 LLM이 읽는 지시문 (0~200자, 1~3문장)",
  "reasoning": "판정 근거 (0~500자, 1~3문장, CoT·trace용)"
}
```

**세 필드 책임 분해**:

| 필드 | 책임 | 주 소비자 |
| --- | --- | --- |
| `route` | 하류 노드 결정(goto 분기) | pipeline edge |
| `handoff_note` | 하류 LLM이 재추론/재포맷/재분석 시 참조할 자연어 지시 | sql_generator, formatter, analyzer, query_normalizer |
| `reasoning` | 판정 근거 로깅, 규제·감사 trace, 오판 시 원인 분석 | trace_log, evaluator |

**제거된 필드**: CHANGELOG #2 참조. `reference_turn_seq`는 코드측에서 `ConversationHistory.seq_for_label()`로 결정적 변환, `updated_intent`는 `route`로 흡수.

**Pydantic v2 모델** (`src/agents/models/continue_orchestrator_output.py` 신설):

```python
class ContinueRoute(str, Enum):
    PRESENT = "present"
    REVISE = "revise"
    ANALYZE = "analyze"
    FRESH = "fresh"

class ContinueOrchestratorOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    route: ContinueRoute
    handoff_note: str = Field(default="", max_length=200)
    reasoning: str = Field(default="", max_length=500)
```

`extra="forbid"`로 구 필드(`reference_turn_seq`·`updated_intent`·`continue_hint`) 자발 추가를 ValidationError로 조기 검출.

#### 3.2.4 Route별 handoff_note 작성 가이드

각 route에서 handoff_note가 담아야 할 내용·패턴·금지 사항. 프롬프트 [EXAMPLES]의 설계 기준이 된다.

##### present — 형식·시각화 변경 (SQL·rows 동일)

| 시나리오 | 좋은 handoff_note | 금지 |
| --- | --- | --- |
| 시각화 전환 ("막대그래프로") | "기존 결과를 막대그래프로 재포맷" | SQL 편집 지시 |
| 포맷 전환 ("엑셀로") | "기존 결과를 엑셀로 출력" | 값 재계산 지시 |
| 기 조회된 rows 내 정렬 변경 | "기존 rows를 금액 내림차순으로 재정렬 표시" | SQL ORDER BY 추가·변경 |
| 요약↔상세 토글 | "result_data를 요약 버전으로 재렌더" | 신규 컬럼 추가 |

##### revise — SQL 편집

| 시나리오 | 좋은 handoff_note | 금지 |
| --- | --- | --- |
| WHERE 추가 ("서울만") | "WHERE 조건에 `REGION_CD = '11'` 추가 (코드 11=서울)" | 모호한 조건("서울 어딘가") |
| GROUP BY 변경 | "GROUP BY PROD_CD → GROUP BY EXEC_YM으로 축 변경, 집계는 SUM(LN_EXEC_AMT) 유지" | 여러 축 동시 변경 시 번호 미부여 |
| 집계 함수 변경 | "COUNT(*) → SUM(LN_EXEC_AMT)로 측정치 변경" | 테이블 신규 탐색 지시 |
| 복합 편집 | "① WHERE 조건에 2024년 추가 ② SELECT에 BAL_AMT 추가" | 번호 없는 다중 편집 |
| 테이블 신규 조인 | "연체율 계산 위해 LN_DELQ_HST 조인 필요 (DELQ_AMT/LN_EXEC_AMT 비율)" | 조인 키·비율식 누락 |

##### analyze — 기존 rows 해석

| 시나리오 | 좋은 handoff_note | 금지 |
| --- | --- | --- |
| 원인 분석 ("왜 1위야?") | "PROD_NM 기준 1위 상품의 LN_EXEC_AMT 비중·특이점 분석, 재조회 없이 기존 rows만 활용" | "추가로 조회" 같은 재실행 지시 |
| 이상치 탐지 | "기존 rows에서 평균 대비 3σ 이상 이탈 케이스 식별" | 신규 필터·기간 확장 |
| 추세 | "EXEC_YM 축 기준 월별 증감률·변곡점 서술" | 기간 확장 지시 |
| 인사이트·시사점 | "비중 상위 3개 상품의 공통점 추출, 업무적 시사점 1~2문장" | 존재하지 않는 컬럼 인용 |

##### fresh — 이전 턴 무관

| 시나리오 | 좋은 handoff_note | 금지 |
| --- | --- | --- |
| 주제 이탈 | `""` (빈 문자열) | 이전 턴 참조·언급 |
| 모호한 발화 → 신규 처리 | `""` 또는 `"신규 질의로 처리"` 짧은 메모 | 긴 배경 설명·가능성 나열 |

**공통 규칙**:

- 200자 이내, 1~3문장
- 구체 컬럼명·코드값 포함 권장 (revise·analyze에서 특히 중요)
- 이전 turn_snapshot을 전제하지 않음 — 하류 노드가 `turn_snapshots` + `reference_turns`로 직접 조회
- 사용자 발화 원문 반복 금지 — 하류 노드도 `state.user_input`을 받음
- fresh에서는 짧을수록 좋음 (긴 메모는 하류에 무의미한 노이즈)

#### 3.2.5 intent_classifier 흡수안 검토 (기각)

orchestrator를 독립 노드로 두는 대신 intent_classifier가 CONTINUE 판정 시 `route`·`handoff_note`까지 동시 산출하도록 통합하는 안을 검토했다. **팩트 기반 분석 결과 기각.**

##### 흡수안 장점

- LLM 호출 절감: 분리 평균 1.3회 / 흡수 1.0회 (0.3회 감소)
- 상태 전이 단순화
- 평균 지연 3.5% 개선 (1,408ms → 1,358ms 추정)

##### 흡수안 단점 (결정적)

| 항목 | 수치·현상 | 영향 |
| --- | --- | --- |
| 입력 토큰 증가 | 분리 평균 6,080 / 흡수 평균 8,580 (**+41%**) | 397B 토큰 비용 상승, 컨텍스트 효율 저하 |
| 프롬프트 책임 폭증 | intent_classifier 출력 8 필드 → 11 필드 (+route/handoff_note/reasoning) | 판정 품질 저하 위험, few-shot 증설 필요 |
| reference_snapshot 주입 타이밍 | CONTINUE 판정 전에도 B 블록 풀 주입 필요 | NEW 턴에서도 불필요한 토큰 낭비 |
| 유지보수 | 단일 프롬프트에 "분류·해석·라우팅" 혼재 | 한쪽 수정이 다른 쪽 품질 흔듦 |

##### 결론

- 토큰 비용 증가(+41%)가 지연 이득(3.5%)의 **약 10배 규모**이므로 분리 유지가 우세
- 분리 구조가 Qwen3.5 397B의 장문 프롬프트 판정 안정성에도 유리 (단일 책임 원칙)

##### 재평가 트리거 (향후)

- CONTINUE/NEW 비율 ≥ 60% 상태가 2주 이상 지속
- 체감 지연이 비즈니스 임계를 넘어서는 경우
- 397B → 더 큰 모델(예: GPT OSS 120B 이상) 승격으로 토큰 단가·컨텍스트 페널티 감소 시

### 3.3 라우팅 카테고리 3+1 (검토 반영: revise로 SQL 편집 통합)

> **변경 근거**: 오케스트레이터(397B LLM)가 "기존 메타로 충분한가"를 메타 없이 판단하기 어려움.
> 모든 SQL 편집을 `revise`로 통합하고 reasoning_preparer가 스냅샷의 `selected_tables`(풀 메타)를 시드로 활용하여
> 기존 테이블로 충분하면 빠르게 완료, 부족하면 자동 추가 탐색.
> (CoE-SQL, MMSQL 모두 이 구분을 두지 않음)

| 카테고리 (확정 명칭) | 진입점 | 정규화 | 메타 재검색 | SQL 재실행 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `present` | formatter 직행 | X | X | **스킵** (`result_data` 재사용) | SQL/결과 동일, 표현·형식·시각화만 변경 |
| `revise` | normalize_query → reasoning_preparer | O | 시드 기반 자동 | O | SQL 편집 (조건/정렬/그룹/집계/테이블 변경) |
| `analyze` | analyzer 직행 | X | X | **스킵** (`result_data` 재사용) | 기존 결과 1000행으로 분석/해석 |
| `fresh` | intent_classifier 재진입(상한 1) | O | O | O | 이전 턴 무관 신규 처리, 재진입 상한 1회 |

> **변경 근거 (result_data 스냅샷화로 승격)**: `result_data`가 스냅샷에 최대 1000행까지 보존되므로
> `present`/`analyze`는 execute_sql을 스킵해 직행 가능. 이전 설계("상위 5행만 있어 부족 → 재실행")는
> 9개 필드 기준에서 더 이상 유효하지 않음. 자세한 내용 §5.4, §5.5 참조.
>
> **fresh 재진입 안전장치**: `fresh`가 intent_classifier를 재호출하면 이론상 다시 CONTINUE 판정이 나올 수 있다.
> `continue_orchestrator` 재진입 횟수를 trace_log로 카운트하여 상한 1회 초과 시 `error_end`로 강제 종료한다
> (§3.5 `_count_orchestrator_reentry`).

### 3.4 케이스별 매핑

#### present (시각화/형식/다시/그대로)

| 자연어 질의 | 핵심 시그널 |
| --- | --- |
| "막대그래프로 보여줘" | 시각화 형식 지정 |
| "파이차트로 바꿔줘" | 시각화 변경 |
| "엑셀로 다운로드" | 출력 형식 변경 |
| "다시 한번 보여줘" | 동일 재실행 (SQL 재실행 없음) |
| "첫 번째 질문 결과 다시 보여줘" | 과거 턴 참조 + 재표시 |
| "이미 나온 결과를 요약만 해줘" | 동일 rows 재포맷 |

#### revise (추가/빼고/바꿔/변경/조건/기간/기준/정렬/같이/합쳐서)

> 기존 MODIFY_SQL + ENRICH 통합. reasoning_preparer가 스냅샷 시드로 탐색 범위 자동 조절.
> **LIMIT/ORDER BY 추가·변경도 revise** (SQL 재실행 수반). present와의 경계는 §3.2.4 참조.

| 자연어 질의 | SQL 편집 내용 | 기존 메타 충분 여부 |
| --- | --- | --- |
| "거기에 2023년 조건 추가해줘" | WHERE 추가 | O (시드로 충분) |
| "서울 지역만 필터링해줘" | WHERE 추가 | O |
| "상위 5개만 (SQL LIMIT 추가)" | LIMIT 추가 | O |
| "금액 기준으로 정렬해줘" | ORDER BY 추가 | O |
| "대전지점 빼고 보여줘" | WHERE NOT 추가 | O |
| "월별로 나눠서 보여줘" | GROUP BY 변경 | O |
| "건수 말고 금액으로" | SELECT/measures 변경 | O (같은 테이블) |
| "잔액도 같이 보여줘" | SELECT 추가 | △ (자동 판단) |
| "연체율도 붙여줘" | JOIN 추가 | X (자동 추가 탐색) |
| "전월 대비 증감도 보여줘" | 기간 확장 + 산출식 (B 블록에 전월 미포함 시) | X (자동 추가 탐색) |
| "같은 조건으로 여신도 뽑아줘" | 테이블 전환 | X (자동 추가 탐색) |

#### analyze (왜/원인/분석/추세/패턴/의미/인사이트)

| 자연어 질의 | 분석 내용 |
| --- | --- |
| "왜 대전지점이 1위야?" | 결과 해석 |
| "특이점 있어?" | 이상치 탐지 |
| "추세가 어때?" | 트렌드 분석 |
| "이걸 보고서로 정리해줘" | 결과 재포맷 + 인사이트 |
| "전월 대비 어때?" (B 블록에 전월 rows 포함) | 기존 rows로 비교 해석 |

> **analyze/revise 경계 규칙**: B 블록 rows에 비교·분석 대상 데이터가 **이미 포함**되어 있으면 analyze,
> **포함되어 있지 않으면** revise. 불확실 시 revise 우선 (SQL 재실행이 analyze 오판보다 안전).

#### fresh (이전 턴 무관)

| 자연어 질의 | 판정 근거 |
| --- | --- |
| 주제가 완전히 바뀐 질문 | reference_turns 빈 배열, 이전 스냅샷과 무관 |
| 지시대명사만 있고 참조 불분명 | 다운그레이드 (fresh handoff_note는 빈 문자열) |

### 3.5 상태 복원 전략 (검토 반영: 단일 저장소 + 노드별 조회)

> **제약 2 적용**: 오케스트레이터는 `reference_turns`(T 라벨 리스트)만 주입.
> 각 하류 노드가 `state.turn_snapshots`에서 해당 라벨로 필터해 필요한 스냅샷을 직접 읽는다.
> **`reference_snapshot` 중간 필드는 두지 않는다** — 데이터 복제 방지, intent_classifier 산출물(T 라벨)이 단일 진실.

오케스트레이터의 Command(update)에 포함하는 것:

```python
Command(
    update={
        "route": route,                       # ContinueRoute: present / revise / analyze / fresh
        "handoff_note": handoff_note,         # 수정/되돌림/분석/재포맷 자연어 지시 (§3.2.4)
                                              # 예(revise): "WHERE 조건에 REGION_CD='11' 추가"
                                              # 예(analyze): "PROD_NM 1위 상품의 비중·특이점 분석"
                                              # 예(present): "기존 결과를 막대그래프로 재포맷"
                                              # 예(fresh): "" (빈 문자열)
        "reference_turns": reference_turns,   # list[str] — intent_classifier 산출 T 라벨 그대로 전달
                                              # fresh에서는 [] 허용
        "intent": primary_snapshot.intent,    # ★ present 오라우팅 방지 (C2) — 대표 턴의 intent
    },
    goto=target_node,                         # §3.3 진입점과 일치
)
```

> **대표 스냅샷 규칙**: `reference_turns`는 오래된→최근 순서. 하류 노드가 단일 기준점이 필요할 때는 **마지막 항목(`[-1]`) = 가장 최근 참조 턴**을 대표로 사용한다.

**route → goto 매핑**:

| route | goto | 하류 처리 |
| --- | --- | --- |
| `present` | `formatter` | `result_data`/`visualization`을 SQLResult로 hydration 후 재포맷 |
| `revise` | `reasoning_preparer` | 스냅샷 시드 + `handoff_note`를 기반으로 재추론 |
| `analyze` | `analyzer` | `result_data` hydration 후 분석만 수행 |
| `fresh` | `intent_classifier` | 신규 처리, 재진입 상한 1회 (아래 `_count_orchestrator_reentry`) |

> **검토 반영 (C2)**: `intent` 미복원 시 `_route_after_execution`(pipeline.py:334)이
> `IntentType.UNKNOWN`으로 판단하여 analyzer 진입 불가. 반드시 Command(update)에 포함.
>
> **검토 반영 (9개 필드 반영)**: `reason=ReasoningState(...)` 전체 객체 복원은 **제거**.
> 이유는 두 가지:
> (1) 스냅샷의 `selected_tables`·`explored_codes`가 이미 풀 메타 객체이므로 reasoning_preparer가
> 시드로 받아 자연스럽게 활용 가능 (별도 ReasoningState 구성 불필요).
> (2) `reason` 전체 교체는 W1 리스크(필드 전체 덮어쓰기)가 있어 부작용 범위가 크다.
> 하류 노드가 `state.turn_snapshots` + `state.reference_turns`로 직접 조회하는 방식으로 통일한다.

#### 공통 조회 헬퍼

하류 노드는 다음 헬퍼로 대표 스냅샷을 꺼낸다 (상세 구현은 `state.py` 확장 또는 공통 util로).

```python
def primary_reference_snapshot(
    state: PipelineState, history: ConversationHistory,
) -> TurnSnapshot | None:
    """reference_turns 중 가장 최근(`[-1]`) T 라벨에 해당하는 스냅샷을 반환."""
    if not state.reference_turns or not state.turn_snapshots:
        return None
    target_seq = history.seq_of(state.reference_turns[-1])
    if target_seq is None:
        return None
    return next(
        (s for s in state.turn_snapshots if s.user_message_seq == target_seq),
        None,
    )
```

#### 각 노드가 읽는 정보 (9개 필드 기준, 대표 스냅샷 = `primary_reference_snapshot(state, history)`)

| 노드 | 읽는 정보 | 출처 |
| --- | --- | --- |
| sql_generator | 이전 SQL, sql_explanation, 편집 지시 | `대표.generated_sql` + `대표.sql_explanation` + `state.handoff_note` |
| reasoning_preparer | 이전 선택 테이블 풀 메타 (시드) | `대표.selected_tables` |
| context_interpreter | 이전 코드 컬럼 풀 메타 (시드) | `대표.explored_codes` |
| query_normalizer | 되돌림/수정 지시 | `state.handoff_note` (conversation_history·스냅샷 직접 참조 X) |
| formatter (present) | 이전 rows·columns·visualization | `대표.result_data` + `대표.visualization` + `state.handoff_note` |
| analyzer | 이전 rows + 분석 지시 | `대표.result_data` + `state.handoff_note` |

#### 재진입 안전장치 `_count_orchestrator_reentry`

fresh 경로가 intent_classifier를 재호출해 다시 CONTINUE 판정이 날 수 있다. trace_log에서 continue_orchestrator 노드 레이블 횟수를 세어 상한(1회) 초과 시 `error_end`로 강제 종료한다.

#### sql_generator 동작 원칙 (revise)

> "SQL generator가 이미 이전 턴의 executed_sql을 받은 후에는 어느 정도 확정된 상태에서
> 추론해나가면 될 것" (사용자 설계 지시)

- 수정 모드 프롬프트는 **"확정된 기준 SQL + handoff_note"** 구조
- 전체 스키마 재탐색 금지 — 이전 SQL을 정답 뼈대로 삼아 handoff_note 부분만 편집
- 명확화/AI 추론의 되돌림은 **orchestrator가 handoff_note에 정리**해서 전달
  (sql_generator가 conversation_history를 역으로 해석하지 않음)

#### orchestrator 책임 (명시적)

스냅샷·대화이력에서 하류 노드가 알아야 할 내용을 **orchestrator가 단일 자연어 handoff_note로 정리**해 `state.handoff_note`에 주입한다. 하류 노드는 conversation_history를 직접 참조하지 않고, **`turn_snapshots`에서 `reference_turns`로 조회한 스냅샷 + `handoff_note`**만으로 동작한다.

- 수정 지시 (revise): "WHERE 조건에 `REGION_CD = '11'` 추가 (코드 11=서울)"
- 되돌림 지시 (revise): "이전 턴 INFER '2024년 전체'를 되돌려 2024년 1월로 제한"
- 명확화 재조정 (revise): "이전 명확화에서 '예금신규 건수'로 답변했으나 이번 질의는 금액 기준"
- 재포맷 지시 (present): "기존 결과를 막대그래프로 재포맷"
- 분석 지시 (analyze): "PROD_NM 1위 상품의 LN_EXEC_AMT 비중·특이점 분석, 재조회 없이 기존 rows만 활용"

route별 작성 가이드·금지 사항은 §3.2.4 참조. 이 방식으로 하류 노드는 단순해지고, orchestrator의 "해석 정리" 책임만 분명해진다.

### 3.6 파이프라인 라우팅 변경 (검토 반영)

모든 SQL 편집을 `revise`로 통합하여 `_route_after_normalize`에서 sql_generator 직행이 불필요해짐.
`revise`는 reasoning_preparer를 경유하되, 스냅샷 시드로 탐색을 단축한다:

```python
def _route_after_normalize(state: PipelineState) -> str:
    if state.pending_signals:
        return "clarification_handler"
    # revise / fresh(재진입) 모두 reasoning_preparer 경유 (시드 유무만 차이)
    return "reasoning_preparer"
```

> 기존 대비 변경 최소화: `_route_after_normalize`는 현재와 동일하게 reasoning_preparer로 보냄.
> 차이는 reasoning_preparer가 대표 스냅샷(`primary_reference_snapshot(state, history).selected_tables`)을 읽어 초기 지식으로
> 시드하는 것뿐이므로 라우팅 복잡도 증가 없음.

---

## 4. 그래프 복잡도 타당성 검증 (리서치 결과)

### 4.1 결론: 정상적으로 복잡하다

| 비교 대상 | 분기 수 | 구조 |
|----------|--------|------|
| MMSQL (arXiv 2024) | 4-way | Answerable/Unanswerable/Ambiguous/Improper |
| Interactive-T2S (arXiv 2024) | 5-action | SearchColumn/SearchValue/FindPath/ExecuteSQL/Done |
| Multi-Agent-Text2SQL (GitHub) | 4분기, 8노드 | NL2SQL + 검증 루프 |
| **Data Copilot (현재 설계)** | **4-way** | present / revise / analyze / fresh |

Anthropic 공식 지침: "분류가 정확하고 하류 경로가 고정될 때는 라우팅 사용. 에이전트는 필요한 단계 수를 예측할 수 없는 개방형 문제에 사용." CONTINUE 라우팅은 경로가 사전에 알려진 고정 유형이므로 **정적 라우팅이 정답**.

### 4.2 서브그래프 분리는 불필요

LangGraph 공식 서브그래프 추출 조건 3가지:
1. 멀티에이전트 격리 (독립 상태) → 해당 없음 (동일 State 공유)
2. 코드 재사용 → 해당 없음 (CONTINUE 전용)
3. 팀 분리 개발 → 해당 없음

CONTINUE는 기존 파이프라인의 **다른 진입점으로 라우팅**되는 것이지 독립 그래프가 아님.
→ Command(update+goto)로 메인 그래프 내에서 처리하는 현재 접근이 적절.

### 4.3 구조적 설계 제약 2가지 (위험 → 제약으로 승격)

**제약 1 (MUST): continue_orchestrator에서 정적 엣지 금지**

LangGraph에서 `add_edge("A", "B")`가 있는 상태에서 노드 A가 `Command(goto="C")`를 반환하면,
B와 C가 **모두 실행**된다 (GitHub Issue #6248).

→ **구현 규칙**: continue_orchestrator 노드에는 `add_edge()` / `add_conditional_edges()`를
사용하지 않는다. 모든 라우팅을 `Command(update=..., goto=...)` 반환값으로만 처리한다.
이렇게 하면 orchestrator가 반환하는 Command가 유일한 라우팅 경로가 된다.

```python
# ✅ 올바른 구현 — orchestrator 노드에서 Command 반환
async def continue_orchestrator(state: PipelineState) -> Command:
    # ... 라우팅 결정 ...
    return Command(
        update={"route": route, "handoff_note": handoff_note, **snapshot_fields},
        goto=target_node,
    )

# ❌ 금지 — 정적 엣지와 Command(goto) 혼용
graph.add_edge("continue_orchestrator", "normalize_query")  # 이거 있으면 goto와 충돌
```

**제약 2 (SHOULD): 단일 저장소 + 노드별 조회**

경로마다 다른 필드를 복원하면 테스트 경우의 수 5배 증가 (5경로 × N필드 조합).

→ **구현 규칙**: 오케스트레이터는 `reference_turns`(T 라벨 리스트)만 state에 주입. 실제 스냅샷 데이터는 **`turn_snapshots` 단일 저장소**에 이미 존재. 각 하류 노드가 공통 헬퍼로 대표 스냅샷을 꺼내 필요한 필드만 읽는다. 오케스트레이터의 책임은 "어떤 턴 라벨을 쓸까" + "어디로 보낼까" 2가지만 남는다.

```python
# 오케스트레이터: T 라벨 리스트만 주입 (경로와 무관하게 동일)
Command(
    update={
        "route": ContinueRoute.REVISE,
        "handoff_note": "WHERE 조건에 REGION_CD='11' 추가 (코드 11=서울)",
        "reference_turns": ["T3"],            # intent_classifier 산출 T 라벨 그대로
        "intent": primary_snapshot.intent,
    },
    goto="reasoning_preparer",
)

# sql_generator: 공통 헬퍼로 대표 스냅샷 조회 후 필요한 것만 읽기
snap = primary_reference_snapshot(state, history)
prev_sql = snap.generated_sql if snap else None
```

### 4.4 정적 라우팅 vs 동적 에이전트 라우팅

| 관점 | 정적 (현재 설계) | 동적 (ReAct) |
|------|----------------|-------------|
| 예측 가능성 | O (테스트 용이) | X (디버그 어려움) |
| 비용 | LLM 1회 (라우팅) | LLM N회 (매 스텝 판단) |
| 유연성 | 새 카테고리 → 코드 수정 | 자동 적응 |
| 은행 도메인 적합성 | **높음** (감사 추적 가능) | 낮음 (비결정적) |

→ 정적 라우팅 유지가 올바른 결정.

---

## 5. 미해결 질문 (검토 반영 후 업데이트)

### 5.1 ~~MODIFY_SQL vs ENRICH 경계~~ → 해결: `revise`로 통합

- ✅ **결정**: 모든 SQL 편집을 `revise`로 통합. reasoning_preparer가 스냅샷 시드로 탐색 범위 자동 조절.
- 근거: 오케스트레이터가 메타 없이 "기존 메타 충분 여부"를 판단하기 비현실적.
  CoE-SQL, MMSQL 모두 이 구분을 두지 않음.

### 5.2 sql_generator 수정 모드 (revise)

- **방향 결정**: 기존 프롬프트에 조건부 섹션 append (프롬프트 분리 X).
- 기존 `fix_section` 패턴(sql_generator.py)과 동일한 방식.
- `대표 스냅샷.generated_sql` + `handoff_note`를 "수정 모드" 섹션으로 추가.

```python
# sql_generator.py 확장 예시 (_build_agentic_prompt 내)
snap = primary_reference_snapshot(state, history)
if snap and state.handoff_note:
    revise_section = REVISE_MODE_TEMPLATE.format(
        previous_sql=snap.generated_sql,
        handoff_note=state.handoff_note,
    )
    prompt += f"\n\n{revise_section}"
```

### 5.3 폐쇄망 LLM 대응 (Qwen3.5 397B)

- 카테고리 4-way(present/revise/analyze/fresh)로 명시하여 분류 부담 경감
- 프롬프트 내 스냅샷은 구조화 요약(§3.2.1 B 블록) + rows 1~3행만 포함 (전체 스냅샷 주입 금지)
- JSON 파싱 실패 시 **정규식 기반 백업 파싱** 추가 (기존 `_parse_sql_response` fallback 패턴 참조)
- `LLMNode.CONTINUE_ORCHESTRATOR` 추가하여 thinking 모드 대응 (Qwen3.5)

### 5.4 `analyze` 처리 방식 (9개 필드 반영으로 변경)

- ✅ **결정**: `analyzer` 직행, SQL 재실행 스킵.
- 근거: 대표 스냅샷 `result_data`에 최대 1000행이 보존되므로 "특이점/추세/해석"
  질의에 충분. execute_sql 경유는 불필요한 DB 부하.
- analyzer는 `state.sql_result`를 읽는 기존 인터페이스를 유지하되, orchestrator가
  `primary_reference_snapshot(state, history).result_data`를 `state.sql_result`로 hydration.
- **analyzer의 이전 분석 결과/conversation_history 참조 필요성**: 다음 설계 단계에서
  결정 (케이스별로 필요·불필요가 갈림 — 예: "이전 분석과 비교해줘" vs "이 결과 해석해줘").

### 5.5 `present` 처리 방식 (9개 필드 반영으로 변경)

- ✅ **결정**: `formatter` 직행, SQL 재실행 스킵 (초기 구현부터).
- 근거: 대표 스냅샷 `result_data`(1000행) + `visualization`이 보존되므로
  "시각화 바꿔줘/엑셀로 다운로드/다시 보여줘"에 필요한 재료가 이미 전부 존재.
- formatter는 `state.sql_result` + `state.visualization`을 읽는 기존 인터페이스 유지.
  orchestrator가 대표 스냅샷 필드를 state로 hydration (`primary_reference_snapshot(state, history)`).
- 기존 Redis 캐싱 전제는 제거 (1000행 상한 내 스냅샷으로 충분). 1000행 초과 케이스는
  별도 결과 캐시 레이어가 필요한 별도 과제 (현 스코프 밖, §3.1 참조).

### 5.6 ~~build_clarification_context 확장~~ → 철회

- ✅ **결정**: 확장 불필요. orchestrator `handoff_note`가 대체 역할 수행.
- 근거: (1) ASK 명확화는 ConversationHistory `[명확화]` 태그로 이미 보존되어
  intent_classifier가 다음 턴 프롬프트에서 참조 가능. (2) INFER 되돌림·수정 지시는
  orchestrator가 `handoff_note` 자연어 문장으로 정리해 하류 노드에 전달.
- sql_generator·query_normalizer 모두 conversation_history나 스냅샷을 직접 참조하지
  않음. **공통 헬퍼로 꺼낸 대표 스냅샷** + `handoff_note`만으로 동작하는 단순한 구조 유지.

---

## 6. 설계 검토 결과 (2026-04-17)

code-reviewer 서브에이전트에 의한 비판적 검토 수행. 전 항목 코드 대조 검증 완료.

### 반영 완료 (Critical 3건)

| # | 이슈 | 반영 내용 |
|---|------|----------|
| C1 | MODIFY_SQL/ENRICH 통합 | §3.3에서 4-way로 확정. 모든 SQL 편집을 `revise`로 통합, reasoning_preparer 시드 활용 |
| C2 | present intent 미복원 → 오라우팅 (구 RERUN) | §3.5 Command(update)에 `intent` 필드 포함 명시 |
| C3 | 폐쇄망 LLM 3-in-1 불안정 | §5.3 구조화 요약 주입, 4-way 카테고리로 정돈, 정규식 fallback 파싱 |

### 반영 완료 (Warning 6건)

| # | 이슈 | 반영 내용 |
|---|------|----------|
| W1 | ReasoningState 전체 교체 | §3.5 전체 객체 구성 예시 추가 |
| W2 | present SQL 재실행 불필요 (구 RERUN) | §5.5 초기 구현부터 스킵 확정 (`result_data` 1000행 스냅샷 보존으로 승격) |
| W3 | analyze sql_result 부재 (구 ANALYZE_ONLY) | §5.4 `result_data` 1000행 스냅샷으로 execute_sql 스킵 확정 (승격) |
| W4 | build_clarification_context 확장 | §5.6 **철회** — orchestrator `handoff_note`가 대체. Step 5에서도 제외 |
| W5 | Step 3/4 병렬 불가 | §7 Step 3+4 통합으로 수정 |
| W6 | turn_reset 초기화 코드 누락 | Step 2에 3개 필드 초기값 명시 |

### 반영 완료 (Info 4건)

| # | 이슈 | 반영 내용 |
|---|------|----------|
| I1 | selected_columns 실용성 | §3.1 `selected_tables: list[TableMeta]` + `explored_codes: list[CodeMeta]` 풀 메타 객체로 진화. 세션 복사 / DB 재조회 경로 명시 |
| I2 | intent 스냅샷 누락 | §3.1 `intent: IntentType` 필드 추가 |
| I3 | save_turn_snapshot 예외 처리 | Step 6에 "예외 시 삼키고 경고 로그만" 명시 |
| I4 | 비데이터 턴 스킵 | Step 6에 "`validated_sql` 없으면 스킵" 명시 |

---

## 7. 구현 수정 영역 (파이프라인 흐름순)

아래는 CONTINUE Orchestrator 구현을 위해 수정/생성해야 하는 파일을 **파이프라인 실행 순서**대로 나열한 것이다.

### Step 1: 데이터 모델 정의

| 파일 | 변경 | 설명 |
|------|------|------|
| `src/agents/models/snapshot.py` | **신규** | `TurnSnapshot` Pydantic 모델 정의 (§3.1, 9개 필드). `TableMeta`·`CodeMeta` 풀 객체 참조 (임포트) |
| `src/services/turn_snapshot_store.py` | **신규** | `TurnSnapshotStore` 서비스. (1) 세션 복사: `from_state(state)` — state 필드에서 직접 추출, (2) DB 복원: `restore_from_db(pool, mongo_repo, thread_id, limit=None)` — checkpoint_dc_messages 읽어 MongoDB 배치 재조회로 풀 메타 복원 (§3.1 예시 코드). FIFO 제한 없음 (CHANGELOG #6) |
| `src/agents/state/state.py` | 수정 | `turn_snapshots: list[TurnSnapshot]` 필드 추가 (turn_reset 대상 아님 — 세션 지속). `reference_turns: list[str]` 필드 추가 (turn_reset 대상 — intent_classifier 산출 T 라벨). `route: str` 필드 추가 (turn_reset 대상). `handoff_note: str` 필드 추가 (turn_reset 대상). **`reference_snapshot` 중간 필드는 두지 않음** (§3.5) — 하류는 공통 헬퍼 `primary_reference_snapshot(state, history)`로 `turn_snapshots`에서 직접 조회 |
| `src/models/enums.py` | 수정 | `ContinueRoute` Enum 추가 (PRESENT, REVISE, ANALYZE, FRESH — §3.2.3), `QueryStatus.CONTINUE_DETECTED` 추가 |
| `src/agents/models/continue_orchestrator_output.py` | **신규** | `ContinueOrchestratorOutput` Pydantic v2 (frozen, extra="forbid"). 3 필드: route/handoff_note/reasoning. §3.2.3 계약 |

### Step 2: 턴 리셋 보호

| 파일 | 변경 | 설명 |
|------|------|------|
| `src/agents/state/state.py` | 수정 | `turn_reset_updates()`에 3개 필드 초기화 명시 추가: `"reference_turns": []`, `"route": ""`, `"handoff_note": ""`. `turn_snapshots`는 나열하지 않음 (세션 지속) |

### Step 3+4: Intent Classifier 분기 + Orchestrator 노드 (동시 커밋)

> **검토 반영**: Step 3과 4는 pipeline.py를 동시에 수정하므로 분리 불가. 동일 PR로 처리.

| 파일 | 변경 | 설명 |
|------|------|------|
| `src/agents/nodes/interpret/intent_classifier.py` | 수정 | CONTINUE 판정 시 `status`를 `QueryStatus.CONTINUE_DETECTED`로 설정 |
| `src/agents/nodes/interpret/continue_orchestrator.py` | **신규** | CONTINUE 라우팅 전담 노드. LLM 1회 호출로 3 필드 출력(§3.2.3): (1) route (2) handoff_note (3) reasoning. `Command(update=..., goto=...)` 반환 (제약 1 준수) |
| `resources/prompts/interpret/continue_orchestrator_system.txt` | **신규** | 오케스트레이터 시스템 프롬프트 (3 블록 INPUT §3.2.1 + 4-way route + route별 handoff_note 가이드 §3.2.4) |
| `resources/prompts/interpret/continue_orchestrator_user.txt` | **신규** | 사용자 프롬프트 템플릿 — `{interpretation_block}` / `{reference_turns_block}` / `{current_utterance}` placeholder (§3.2.1) |
| `src/agents/nodes/thinking_modes.py` | 수정 | `LLMNode.CONTINUE_ORCHESTRATOR` 추가 |
| `src/agents/graph/pipeline.py` | 수정 | (1) `_route_after_intent_classifier`에 `CONTINUE_DETECTED` 분기 추가, (2) `continue_orchestrator` 노드 등록 (**정적 엣지 금지** — Command(goto)만 사용), (3) `_VALID_RETURN_TARGETS` frozenset에 `"continue_orchestrator"` 추가 — CONTINUE 턴에서 명확화 발생 시 `clarification_handler`가 오케스트레이터로 복귀 가능하도록 (I3) |

### Step 5: 하류 노드 스냅샷 참조 지원

> **원칙 (§3.5)**: 하류 노드는 `conversation_history`·원본 `resolved_signals`를 직접
> 참조하지 않는다. 공통 헬퍼 `primary_reference_snapshot(state, history)`로 꺼낸
> **대표 스냅샷** + `state.handoff_note`만으로 동작한다.

| 파일 | 변경 | 설명 |
| --- | --- | --- |
| `src/agents/nodes/reason/sql_generator.py` | 수정 | `primary_reference_snapshot(state, history)`로 대표 스냅샷 조회 → 존재 시 `snap.generated_sql` + `state.handoff_note`를 "수정 모드" 섹션으로 프롬프트에 append (기존 `fix_section` 패턴). 전체 스키마 재탐색 금지 — 이전 SQL을 확정 기준으로 삼아 handoff_note 부분만 편집 |
| `src/agents/nodes/reason/reasoning_preparer.py` | 수정 | `primary_reference_snapshot(state, history).selected_tables`(풀 메타)를 초기 탐색 시드로 주입 (`revise` 경로 탐색 단축). 시드로 충분하면 그대로 종료, 부족하면 자동 추가 탐색 |
| `src/agents/nodes/reason/context_interpreter.py` | 수정 | `primary_reference_snapshot(state, history).explored_codes`(`dict[str, CodeMeta]`)를 초기 지식(knowledge_items)으로 주입. 기존 `reason.explored_codes` dict 시맨틱과 그대로 호환 |
| `src/agents/nodes/present/analyzer.py` | 수정 | `analyze` 경로 — orchestrator가 `primary_reference_snapshot(state, history).result_data`를 `state.sql_result`로 hydration한 상태에서 분석 수행 (분석 결과/대화이력 추가 필요성은 다음 설계 단계에서 결정) |
| `src/agents/nodes/present/formatter.py` | 수정 | `present` 경로 — 대표 스냅샷의 `result_data` + `visualization`을 `state.sql_result`·`state.visualization`으로 hydration한 상태에서 재포맷/시각화 변경 처리 |

### Step 6: 턴 완료 시 스냅샷 저장 (가장 먼저 배포 가능)

> **검토 반영**: Step 1에만 의존하므로 가장 먼저 구현·배포 가능.
> 오케스트레이터 완성 전에도 스냅샷 데이터가 축적되어 디버깅/검증에 유리.

| 파일 | 변경 | 설명 |
| --- | --- | --- |
| `src/agents/nodes/present/save_turn_snapshot.py` | **신규** | `formatter` 직후 실행. `TurnSnapshotStore.from_state(state)`로 9개 필드 스냅샷 생성 후 `turn_snapshots`에 append (무제한 누적, CHANGELOG #6). 아래 스킵 규칙 적용: (1) `reason.validated_sql` 없으면 스킵 (비데이터 턴), (2) `state.route == "present"` 이면 **스킵** — 동일 결과 재표현이라 새 스냅샷 생성 불필요, 참조 턴이 그대로 최신 스냅샷으로 유지됨, (3) `state.route in {"revise", "analyze", "fresh"}` 이면 정상 저장 (`revise`는 SQL이 바뀌므로 새 턴, `analyze`는 분석 내용이 assistant 응답에 누적되므로 별도 스냅샷, `fresh`는 신규 턴). `reason.explored_tables`(list)와 `reason.explored_codes`(dict)에서 풀 메타 그대로 복사. INFER 필터링 시 `source_node == "intent_classifier"` 제외 (I4). 예외 시 삼키고 경고 로그만 (사용자 응답 차단 방지) |
| `src/services/process_summary_builder.py` | 수정 | `_build_ai_decision_dict` inferences 구성에 `reasoning: s.reasoning or ""` 1줄 추가 (DB 복원 경로에서 INFER 근거 보존, §3.1) |
| `src/agents/graph/runner.py` | 수정 | user 메시지 `insert_message` 호출 후 반환받는 `seq`를 `state.current_user_message_seq`(신규 필드) 등으로 전파. `save_turn_snapshot` 이 스냅샷 생성 시 `user_message_seq` 매핑 키로 사용. state 필드 이름과 채번 타이밍은 구현 PR에서 최종 확정 |
| `src/agents/graph/pipeline.py` | 수정 | 기존 `formatter → END`를 `formatter → save_turn_snapshot → END`로 변경 |

### Step 7: 세션 재접속 시 스냅샷 복원

> **목적**: DB 재접속 후에도 CONTINUE 경로가 동작하도록 복원 경로 구축.

| 파일 | 변경 | 설명 |
| --- | --- | --- |
| `src/main.py` | 수정 | WebSocket 세션 재개 시 기존 `ConversationHistory` 로드 직후 `TurnSnapshotStore.restore_from_db(pool, mongo_repo, thread_id, limit=None)` 호출해 `state.turn_snapshots` hydrate. MongoDB 배치 재조회로 풀 메타 복원 (§3.1). FIFO 제거 후 무제한 누적이지만 서비스 운영 중 성능 저하 감지 시 limit 상한 재도입 가능 |

### Step 8: 테스트

| 파일 | 변경 | 설명 |
| --- | --- | --- |
| `tests/auto/unit/test_continue_orchestrator.py` | **신규** | 오케스트레이터 3 필드 출력(§3.2.3) + 4-way 경로 판정 (present/revise/analyze/fresh × 주요 케이스) + fresh 재진입 상한 |
| `tests/auto/unit/test_turn_snapshot.py` | **신규** | 스냅샷 9개 필드 생성/무제한 누적/비데이터 턴 스킵/present 경로 스킵 |
| `tests/auto/unit/test_turn_snapshot_store.py` | **신규** | `from_state` 세션 복사, `restore_from_db` MongoDB 배치 재조회, 조회 실패 턴 제외 |
| `tests/auto/integration/test_continue_flow.py` | **신규** | present/revise/analyze 경로별 E2E (execute_sql 스킵 확인 포함) + fresh 재진입 상한 확인 |

### 의존성 그래프 (구현 순서, 검토 반영)

```
Step 1 (모델+Store) ─┬─ Step 2 (리셋 보호)
                      ├─ Step 3+4 통합 (intent 분기 + 오케스트레이터 + pipeline)
                      ├─ Step 6 (스냅샷 저장) ★ 가장 먼저 배포 가능
                      └─ Step 7 (세션 재접속 복원)

Step 5 (하류 노드) ← Step 3+4 완료 후
Step 8 (테스트) ← 전체 완료 후
```

**권장 배포 순서**: Step 1+2 → Step 6 (스냅샷 축적 시작) → Step 7 (복원 경로) → Step 3+4 → Step 5 → Step 8

---

## 8. 참고 자료

### 학술 논문
- CoE-SQL: In-Context Learning for Multi-Turn Text-to-SQL (NAACL 2024) — arXiv:2405.02712
- Track-SQL: Dual-Extractive Modules for Multi-turn Text-to-SQL (NAACL 2025)
- TRUST-SQL: Tool-Integrated Multi-Turn RL for Text-to-SQL (arXiv 2025) — arXiv:2603.16448
- SParC: Cross-Domain Semantic Parsing in Context (ACL 2019)
- MMSQL: Evaluating and Enhancing LLMs for Multi-turn Text-to-SQL (arXiv 2024) — arXiv:2412.17867
- Interactive-T2S: Multi-Turn Interactions for Text-to-SQL (arXiv 2024) — arXiv:2408.11062

### 산업 패턴/문서
- Anthropic: Building Effective Agents (2025) — 6개 패턴, 복잡도 원칙
- Anthropic: Effective Context Engineering for AI Agents (2025)
- Anthropic: Multi-Agent Research System (2025) — lead+subagent 구조
- JetBrains: Cutting Through the Noise — Smarter Context Management (Dec 2025)
- LangGraph: Command API — 동적 라우팅 + 상태 주입
- LangGraph: Subgraphs Documentation — 서브그래프 추출 조건
- LangGraph Memory Overview — Persistent vs Ephemeral 분리
- Snowflake Cortex Analyst Follow-up Query Support (Nov 2024)
- Multi-Agent-Text2SQL-System (GitHub, azain47) — 8노드 NL2SQL 구현
