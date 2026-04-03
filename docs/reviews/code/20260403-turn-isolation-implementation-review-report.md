# turn_id 턴 격리 구현 리뷰

- 일시: 2026-04-03
- 대상: resolved_signals 상태 오염 방지를 위한 turn_id 격리 구현 (7개 파일)
- 중점: 타입 일관성, 필터 누락, 체크포인트 안전성, 엣지 케이스

---

## 파일별 검증 결과 요약

| 파일 | 역할 | 판정 |
|------|------|------|
| `models/clarification.py` | AmbiguitySignal에 `turn_id: str \| None = None` 추가 | OK |
| `state/state.py` | PipelineState에 `turn_id: str = ""` 추가 | OK |
| `graph/runner.py` | 새 턴에서 `turn_id=str(uuid.uuid4())` 생성 | 주의사항 있음 |
| `nodes/interpret/clarification_handler.py` | 시그널에 turn_id 주입 | OK |
| `nodes/interpret/query_normalizer.py` | INFER 시그널 생성 시 turn_id 전달 | OK |
| `utils/clarification_context.py` | turn_id 기반 필터링 | 주의사항 있음 |
| `graph/pipeline.py` | `_route_after_clarify` turn_id 필터링 | OK |
| `nodes/interpret/context_classifier.py` (미변경) | 전체 세션 시그널 참조 | 설계 의도 확인 필요 |
| `nodes/present/formatter.py` (미변경) | `build_auto_resolved_notice` 호출만 | OK |

---

## 1. [OK] AmbiguitySignal turn_id 필드 (clarification.py:80)

```python
turn_id: str | None = None  # 소속 턴 식별자 (소비자 필터링용)
```

- `str | None` 기본값 `None`은 적절. 기존 직렬화 데이터와의 호환성을 보장한다.
- 체크포인터에 이미 저장된 시그널은 `turn_id=None`으로 역직렬화되므로, 필터에서 자동 제외된다.

---

## 2. [OK] PipelineState turn_id 필드 (state.py:567)

```python
turn_id: str = ""
```

- `str` 기본값 `""`는 runner.py에서 항상 UUID를 설정하므로 실행 중에 빈 문자열일 가능성은 매우 낮다.
- 주석 `W: runner (매 턴 uuid4 생성) R: clarification_context, pipeline 라우팅` -- 정확하고 일관성 있음.

---

## 3. [MEDIUM] 타입 불일치: `str | None` vs `str` 비교 (clarification_context.py, pipeline.py)

**위치**: `clarification_context.py:45`, `clarification_context.py:61`, `clarification_context.py:100`, `pipeline.py:339`

**현상**: 필터 조건이 `s.turn_id == state.turn_id` 패턴을 사용한다.

- `AmbiguitySignal.turn_id`는 `str | None` (기본값 `None`)
- `PipelineState.turn_id`는 `str` (기본값 `""`)

**분석**:

- 정상 경로: `state.turn_id`는 항상 UUID 문자열, `s.turn_id`도 clarification_handler가 주입하므로 UUID. 문제 없음.
- 체크포인터 역직렬화 시그널: `turn_id=None`인 레거시 시그널은 `None == "uuid..."` -> `False`로 자동 제외. 의도한 동작.
- 방어 분기(`if not tid: return ""`)가 있어 `state.turn_id == ""`인 경우도 조기 반환.

**결론**: 런타임 동작은 정확하지만, mypy --strict에서 `Optional[str] == str` 비교 관련 경고가 발생할 수 있다. 명시적 타입 가드를 추가하면 더 안전하다.

**권장 조치** (선택):
```python
# 현재
asks = [s for s in state.resolved_signals if s.decision == "ASK" and s.turn_id == tid]

# 명시적
asks = [s for s in state.resolved_signals if s.decision == "ASK" and s.turn_id is not None and s.turn_id == tid]
```

---

## 4. [OK] clarification_handler turn_id 주입 타이밍 (clarification_handler.py:118-122)

```python
# 0. 턴 ID 주입 -- 모든 시그널에 일괄 적용 (가드레일 보정 전)
for s in signals:
    s.turn_id = state.turn_id
```

**검증 항목**: interrupt() 호출 전에 turn_id가 주입되는가?

- turn_id 주입: **line 121-122** (step 0)
- guardrail 보정: **line 125-134** (step 1)
- INFER 반환 또는 interrupt 호출: **line 143-163** (step 2-3)

**결론**: turn_id 주입이 interrupt() 호출보다 **확실히 앞서** 실행된다. 체크포인터에 저장되는 시그널에 turn_id가 포함됨을 보장한다. 이 순서가 깨지면 resume 후 필터링이 실패하므로 순서가 매우 중요한데, 현재 구현은 정확하다.

---

## 5. [OK] query_normalizer turn_id 전달 (query_normalizer.py:162)

```python
turn_id=state.turn_id,
```

- AmbiguitySignal 생성자에서 직접 전달. clarification_handler를 거치지 않고 바로 `resolved_signals`에 기록되는 INFER 전용 경로이므로, 여기서 직접 설정하는 것이 정확하다.

---

## 6. [LOW] 빈 turn_id 방어 로직 (clarification_context.py:34-38, 91-96)

```python
tid = state.turn_id
if not tid:
    logger.warning("turn_id가 비어있음 -- runner.py에서 UUID 생성 누락 가능성")
    return ""
```

**분석**:

- 빈 문자열 반환(`""`)은 안전한 폴백. 프롬프트에 명확화 컨텍스트가 누락되지만 시스템은 크래시하지 않는다.
- 로거 warning은 운영 중 문제 감지에 도움.
- `build_auto_resolved_notice`에서도 동일 패턴 적용 -- 일관성 있음.

**경미한 우려**: 빈 turn_id 상태에서 프롬프트에 명확화 컨텍스트가 빠지면, LLM이 이전 명확화 결과를 모른 채 SQL을 생성할 수 있다. 단, runner.py에서 항상 UUID를 설정하므로 이 경로가 실행될 확률은 극히 낮다.

---

## 7. [OK] pipeline.py `_route_after_clarify` 필터링 (pipeline.py:337-340)

```python
current_signals = [
    s for s in state.resolved_signals
    if s.turn_id == state.turn_id
]
```

- 이전 턴 시그널의 `source_node`로 잘못된 노드로 라우팅되는 문제(상태 오염의 핵심 증상)를 정확히 차단.
- 빈 리스트 폴백(`return "context_classifier"`, line 350)도 안전.

---

## 8. [MEDIUM] context_classifier의 필터 미적용 -- 의도적 설계 확인 필요

### 8-a. `_build_clarification_history` (context_classifier.py:39)

```python
for signal in state.resolved_signals:
    if signal.source_node != "context_classifier":
        continue
```

- 전체 세션의 `context_classifier` 발생 시그널을 모두 순회한다. turn_id 필터 없음.
- **설계 의도 추정**: 연속 대화(CONTINUE) 판정 시 이전 턴의 명확화 맥락도 필요할 수 있으므로 의도적으로 전체 이력을 참조.
- **위험**: 세션이 길어지면(10+ 턴) 프롬프트가 불필요하게 비대해질 수 있다. 현재는 `context_classifier` 발생 시그널만 필터하므로 실질적 크기는 제한적.

### 8-b. `ask_count` 무한루프 방어 (context_classifier.py:109-112)

```python
ask_count = sum(
    1 for s in state.resolved_signals
    if s.decision == "ASK"
)
```

- 전체 세션의 ASK 횟수를 카운트. turn_id 필터 없음.
- **이것은 올바른 설계**: 무한루프 방어는 세션 전체 기준이어야 한다. 턴별로 카운트하면 매 턴 max_turns번 반복 가능해져 방어 효과가 무력화된다.

**권장**: 8-a에 대해 주석으로 "의도적으로 전체 세션 이력 참조" 임을 명시하면 향후 유지보수에 도움.

---

## 9. [OK] context_classifier AmbiguitySignal 생성 시 turn_id 미설정 (context_classifier.py:151)

```python
signal = AmbiguitySignal(
    source_node="context_classifier",
    decision="ASK",
    ...
)
# turn_id 설정 없음 -> None
```

- `pending_signals`에 담긴 후 `clarification_handler`가 `s.turn_id = state.turn_id`로 주입하므로 정확.
- 설계 원칙: "turn_id 주입 책임은 clarification_handler에 단일화" -- 일관성 있는 패턴.

---

## 10. [OK] formatter.py 미변경 확인

- `build_auto_resolved_notice(state)`만 호출. 해당 함수 내부에서 turn_id 필터링이 이미 적용됨.
- formatter 자체에서 `resolved_signals`를 직접 접근하지 않음. 정확.

---

## 11. [OK] 순환 import 검증

- `clarification_context.py`는 `TYPE_CHECKING` 가드 하에서 `PipelineState`를 import.
- `logger` import는 `src.utils.logger`에서 `get_logger`를 정상적으로 import (line 17, 22).
- 새로 추가된 코드에서 새 import는 없으므로 순환 import 위험 없음.

---

## 12. [OK] resolved_signals 소비처 누락 검증

codebase 전체 grep 결과, `resolved_signals`를 읽는 곳:

| 위치 | 필터 적용 | 판정 |
|------|-----------|------|
| `clarification_context.py:44,60,99` | turn_id 필터 적용 | OK |
| `pipeline.py:338` | turn_id 필터 적용 | OK |
| `context_classifier.py:39` | source_node 필터 (전체 세션, 의도적) | OK |
| `context_classifier.py:110` | 무한루프 방어 (전체 세션, 정확) | OK |
| `clarification_handler.py:145,177` | 쓰기 전용 (소비가 아닌 생산) | N/A |
| `query_normalizer.py:166` | 쓰기 전용 | N/A |
| `reasoning_preparer.py:172,199` | 주석만 (코드에서 직접 접근 없음) | N/A |
| `sql_generator.py:17` | 주석만 (`build_clarification_context` 경유) | N/A |

**결론**: 누락된 소비처 없음.

---

## 13. [LOW] interrupt resume 시 turn_id 미갱신 (runner.py:128-137)

```python
if is_interrupt_pending:
    result = await app.ainvoke(
        Command(resume=sanitized.text),
        config=run_config,
    )
```

- resume 경로에서 새 `turn_id`를 설정하지 않음.
- **이것은 올바른 동작**: interrupt/resume은 동일 턴의 연속이다. clarification_handler에서 이미 주입된 turn_id가 체크포인터에 저장되어 있으므로, resume 후에도 동일 turn_id로 필터링이 정상 작동한다.
- 만약 resume 시 새 turn_id를 발급하면 오히려 방금 생성한 시그널을 찾지 못하는 버그가 발생한다.

---

## 종합 판정

| 등급 | 건수 | 내용 |
|------|------|------|
| BLOCKER | 0 | - |
| HIGH | 0 | - |
| MEDIUM | 2 | #3 타입 불일치(mypy 경고 가능성), #8 context_classifier 필터 미적용 의도 주석 부재 |
| LOW | 2 | #6 빈 turn_id 폴백 시 프롬프트 누락, #13 resume 시 turn_id 미갱신(정확한 동작이나 주석 권장) |
| OK | 9 | 나머지 전체 항목 |

**전체 평가**: 구현이 정확하고 안전하다. 핵심 설계 결정(turn_id 주입 책임의 단일화, interrupt 전 주입 보장, 전체 세션 카운트 유지)이 올바르게 이루어졌다. BLOCKER/HIGH 이슈 없음. MEDIUM 2건은 mypy 경고 방지와 의도 문서화에 관한 것으로, 기능 정확성에는 영향 없다.
