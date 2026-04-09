# 코드리뷰 수정 계획 검증 보고서

> 검증일: 2026-04-06
> 대상: `docs/todo/20260406-code-review-action-plan.md`

---

## 1. 완전성 -- PASS

수정 대상 15건(#3~7, #9, #10, #14, #17~19, #23~27, #31)이 모두 설계안에 포함되어 있다.

## 2. 코드 정합성 -- 문제 3건

### [Warning] 1-1. #3 sql_executor.py 라인 번호 불일치

- 설계안: `L50-54`
- 실제: `L50-54` -- 일치. 코드 내용도 정확히 일치. PASS.

### [Warning] 3-1. #14 runner.py 함수 크기 불일치

- **요약문서 #14**: "약 170줄 단일 함수"
- **설계안 3-1**: "L62-371 (~310줄)"
- **실제**: `run_pipeline`은 L62-371 (310줄). 설계안이 정확하고 **요약문서의 "170줄"이 오류**.
- 영향: 설계안 자체에는 문제 없으나, 요약문서 정정 필요.

### [Critical] 2-1. #9 sql_validator.py failure_type 설정 방식과 설계안 불일치

- 설계안은 `updates["recovery_entry_source"] = "sql_validator"`를 반환 dict에 추가하라고 한다.
- **실제 코드**: `sql_validator.py`에서 failure_type은 `reason.failure_type = FailureType.XXX`로 **state를 직접 mutation**하고 있다 (L69, 82, 135, 156, 169, 190, 192, 212, 215 등). 반환 dict(`updates`)에 넣는 패턴이 **아니다**.
- 즉, `recovery_entry_source`만 반환 dict로 옮기면 failure_type과의 설정 패턴이 **불일치**한다. failure_type도 in-place mutation인데 recovery_entry_source만 반환값으로 바꾸는 것은 하프 리팩토링이 된다.
- **보완 필요**: (a) failure_type 설정도 함께 반환 dict로 이동하거나, (b) 최소한 이 불일치를 인지하고 후속 리팩토링 계획에 명시해야 한다.

### [Info] 1-2. #4 postgres_connector.py 라인 번호

- 설계안: `L60-67, 172-179`
- 실제: InfoDB `L60-67`, HistoryDB `L172-179` -- 정확히 일치. PASS.

### [Info] 1-3. #5 mongo_connector.py 라인 번호

- 설계안: `L145-149`
- 실제: `L145-149` -- 일치. PASS.

### [Info] 2-2. #10 cancel_store.py 라인 번호

- 설계안: `L80-87`
- 실제: `L80-87` -- 일치. PASS.

### [Info] 3-2. #17 format_context_warning 호출 0건

- grep 결과 `src/` 전체에서 호출부 0건 확인. 데드코드 삭제 판단 정확. PASS.

### [Info] 6-1. #31 LLM 호출 패턴

- `response_formatter.py:124`, `data_analyzer.py:156`에서 `client.messages.create()` 직접 호출 확인. PASS.

## 3. 설계 타당성 -- 문제 1건

### [Warning] #9 설계가 LangGraph 패턴을 완전히 해결하지 못함

pipeline.py L233-234, L252-254의 state mutation 제거는 올바른 방향이나, `sql_validator.py` 자체가 failure_type을 이미 in-place mutation으로 설정하는 패턴이므로, recovery_entry_source만 반환값으로 옮기는 것은 "라우팅 함수의 순수성"이라는 목표를 **부분적으로만** 달성한다. 라우팅 함수 자체는 순수해지지만, 근본 원인인 "노드가 state를 반환 대신 직접 수정"하는 패턴은 그대로 남는다.

## 4. 의존성/순서 -- PASS

Phase 간 의존성 정의는 적절하다. 특히 Phase 3의 #18이 #17 이후인 점, Phase 1의 #6/#7 순차 처리 등 합리적이다.

## 5. 누락 사항 -- 2건

### [Warning] #18 ES config 주석 처리 범위 미명시

설계안에 "ES 관련 config 필드 12개 + elasticsearch_connector + manager import를 주석 처리"라고 하지만, `manager.py`에서 `enabled_connectors`에 포함하는 코드와 `pyproject.toml`의 elasticsearch 의존성 처리는 "주의사항"으로만 언급되어 있고 구체적 라인/코드가 없다. 실행 시 누락 위험이 있다.

### [Info] #31 기존 재시도 패턴 미조사

설계안이 "프로젝트 내 기존 LLM 재시도 패턴이 있는지 확인 후"라고 하면서 실제 조사 결과를 제시하지 않았다. 선결 조건이 미해결 상태로 설계가 완료된 것이다.

---

## 요약

| 등급 | 건수 | 내용 |
|------|------|------|
| Critical | 1 | #9 sql_validator failure_type mutation 패턴과 설계안 불일치 |
| Warning | 3 | #14 요약문서 170줄 오기, #9 하프 리팩토링 문제, #18 범위 미명시 |
| Info | 1 | #31 선결 조건 미조사 |
| PASS | 10 | 나머지 항목 코드 정합성/설계 모두 정확 |
