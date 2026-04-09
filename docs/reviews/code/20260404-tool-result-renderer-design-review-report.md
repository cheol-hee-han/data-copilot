# Tool Result Renderer 설계서 리뷰 보고서

- 대상: `docs/working/tool-result-renderer-design.md`
- 리뷰 일자: 2026-04-04
- 리뷰어: Code Reviewer Agent
- 리뷰 초점: SQL 생성 정확도 기여도, 설계 누락/오류, 멀티 라운드 일관성

---

## 1. 빠진 부분 (Missing)

### 1.1. [RED] search_table_meta enrichment의 Phase 2 대체 전략 미기술

**현행**: fetcher의 Phase 2(`_observe_all_date_distributions`, `_sample_unsampled_tables`)가 **모든** candidate_table에 대해 날짜분포와 샘플을 자동 보완한다. 이 Phase 2는 스텝과 무관하게 전체 테이블을 순회한다.

**설계서**: search_table_meta의 enrichment가 "샘플+날짜분포 포함"이라고 명시(SS3.2 표)하지만, Phase 2의 **전체 순회** 로직을 어떻게 대체하는지 기술하지 않는다.

**문제**: search_use_cases의 `_fetch_use_case_related_metas`로 발견된 테이블은 search_table_meta 스텝을 통해 들어온 것이 아니다. 이 테이블들에 대한 샘플/날짜분포는 누가 수집하는가? fetcher가 state를 건드리지 않는다면, 이 enrichment가 step.raw_result에도 포함되지 않고 Phase 2도 사라지므로 **관찰 데이터가 유실**된다.

**제안**: Phase 2 대체 전략을 명시적으로 설계해야 한다. 선택지:
- (A) search_use_cases의 enrichment에서 관련 테이블의 샘플/날짜분포까지 포함
- (B) interpreter가 state에 적재한 후 별도 "관찰 보완" 스텝을 삽입
- (C) Phase 2를 fetcher에 유지하되 step.raw_result가 아닌 별도 enrichment_result로 저장


### 1.2. [RED] 수정 대상 파일 목록(SS9) 누락

설계서의 SS9 수정 대상 목록에서 다음 파일이 **누락**되어 있다:

| 누락 파일 | 참조 내용 |
|-----------|----------|
| `src/utils/tracker/callback_handler.py` | `candidate_tables` 문자열 참조 (L839-840) |
| `src/utils/tracker/visualizer.py` | `candidate_tables` 문자열 참조 (L235, L352) |
| `src/agents/state/__init__.py` | `CandidateTable` re-export |

테스트 파일은 SS9에서 "테스트 파일" 한 줄로 퉁쳤지만, 실제 영향 범위는 최소 6개 파일이다:
- `tests/auto/unit/test_recovery_agent.py`
- `tests/auto/e2e/test_agentic_core.py`
- `tests/auto/e2e/test_agentic_e2e.py`
- `tests/auto/e2e/test_agentic_flow_trace.py`
- `tests/auto/unit/test_three_aspect_enrichment.py`
- `tests/manual/e2e/test_agentic_real_e2e.py`

특히 `callback_handler.py`와 `visualizer.py`는 **문자열 키**("candidate_tables")로 접근하므로 정적 분석(IDE 리네임)에서 놓치기 쉽다. `__init__.py`의 re-export 누락은 외부에서 `from src.agents.state import CandidateTable` 패턴을 사용하는 코드가 있으면 import 실패를 유발한다.


### 1.3. [YEL] interpreter의 biz_manuals/biz_terms/code_map 판정 후 state 적재 절차 미기술

설계서 SS3.4에서 interpreter가 gatekeeper로서 "판정 결과에 따라 state 필드에 적재"한다고 하지만, 구체적으로 다음이 미기술이다:

- explored_biz_manuals의 SelectionStatus 마킹 절차
- explored_biz_terms의 SelectionStatus 마킹 절차
- code_map의 적재 방식 (현행 fetcher가 직접 적재 -> interpreter가 판정 후 적재로 변경?)

현행 interpreter는 테이블과 use_case만 판정하고 biz_manuals/biz_terms/code_map은 판정 대상이 아니다. 설계서가 이를 확장하려는 의도인지, 기존 방식을 유지하는지 명확하지 않다.


### 1.4. [YEL] recovery_agent의 execution_plan 교체 시 이전 raw_result 메모리 영향 미분석

설계서 SS3.1에서 "recovery_agent가 execution_plan을 새로 생성하면 이전 스텝의 raw_result도 자연스럽게 교체됨"이라고 하지만, raw_result에 대형 데이터(전체 컬럼 목록, 샘플 데이터, enrichment 테이블 메타)가 포함되면 **GC 전까지 메모리에 상주**한다.

현행 구조에서는 도구 결과가 state 필드에 구조화되어 적재되므로 원본 raw 데이터는 함수 스코프 종료 시 해제된다. 새 구조에서는 ExecutionStep.raw_result가 state의 일부이므로 파이프라인 전체 생명주기 동안 유지된다.

**제안**: interpreter가 state 적재를 완료한 후 `step.raw_result = None`으로 해제하는 명시적 정리 단계를 설계에 포함.


### 1.5. [YEL] seen_tables의 state 읽기 허용 여부 불명확

SS3.3에서 `seen_tables: set`으로 중복 방지하고 "이전 라운드의 explored_tables도 seen_tables 초기값으로 포함"이라고 했지만, fetcher는 "state 안 건드림" 원칙을 따른다. explored_tables(state)를 **읽어서** seen_tables를 초기화해야 하는데, 이것은 "state에서 읽기"에 해당한다.

**제안**: "fetcher는 state를 읽기만 하고 쓰지 않는다(Read-Only)"로 원칙을 명확화.


---

## 2. 잘못 설계된 부분 (Incorrect)

### 2.1. [RED] ExecutionStep.raw_result의 타입이 Any인 것은 Pydantic 직렬화/deep copy와 충돌

```python
class ExecutionStep(BaseModel):
    raw_result: Any = None  # 도구별 자유 구조
```

PipelineState는 Pydantic BaseModel이고 LangGraph의 state 관리에 사용된다.

1. **LangGraph state checkpoint**: JSON 직렬화를 시도할 때 비표준 객체(MongoDB ObjectId 등)가 포함되면 실패
2. **model_copy(deep=True)**: 현행 코드에서 노드 진입마다 `reason.model_copy(deep=True)` 수행(knowledge_fetcher.py L490, knowledge_interpreter.py L94, recovery_agent.py L70, sql_generator.py L140). raw_result에 대형 데이터가 있으면 **매 노드 진입마다 deep copy 비용** 발생
3. **설계서의 접근 표기 불일치**: `step.raw_result.use_cases`(속성 접근)라고 표기했지만, dict라면 `step.raw_result["use_cases"]`여야 한다

**제안**: 도구별 TypedDict 또는 Union 타입 정의. 최소한 `dict[str, Any] | list[Any] | None`으로 제한. interpreter 처리 완료 후 `raw_result = None` 설정으로 deep copy 비용 완화.


### 2.2. [RED] search_use_cases enrichment 테이블과 search_table_meta 테이블의 중복 렌더링

**설계서 흐름**:
1. fetcher: search_use_cases 실행 -> enrichment로 관련 테이블 메타 조회 -> step.raw_result.tables에 저장
2. fetcher: search_table_meta 실행 -> step.raw_result.tables에 저장
3. interpreter: 각 스텝의 raw_result를 **별도 블록**으로 렌더링

**문제**: 동일한 테이블(예: TB_LOAN_EXEC)이 search_use_cases 블록과 search_table_meta 블록에 **2회** 렌더링된다. LLM은 이 두 출처의 동일 테이블을 어떻게 통합 판정하는가?

- selected/rejected 판정 시 어느 블록 기준인지 혼란
- 토큰 낭비 (동일 테이블의 전체 컬럼이 2번 출력)
- "위와 동일 -- 중복 생략" 패턴이 **같은 스텝 내**에서만 적용되고 **스텝 간**에는 적용되지 않음

**제안**: 렌더링 시 스텝 간 테이블 중복을 감지하여 두 번째 출현부터 "Step N에서 이미 표시 -- 생략"으로 처리. 또는 enrichment 테이블은 "컨텍스트 보조" 역할이므로 별도 판정 대상이 아님을 명시.


### 2.3. [YEL] 관찰 데이터 섹션 제거(SS11.6)와 Phase 2 자동 수집의 교집합 미처리

SS11.6은 현행 `_serialize_table_observations`를 제거하고 스텝 블록으로 대체한다고 하지만:

- **Phase 2 자동 수집 결과**: `_observe_all_date_distributions`와 `_sample_unsampled_tables`는 실행 계획의 스텝이 아니라 fetcher 내부 로직. 이 결과는 어떤 step에도 속하지 않으므로 step.raw_result로 렌더링할 수 없다.
- **스텝 기반 수집 결과**: get_sample_rows, get_date_distribution 스텝으로 명시적 실행된 결과는 step.raw_result에 있다.

Phase 2를 완전히 제거하지 않는 한, 관찰 데이터 섹션 제거는 Phase 2 결과의 정보 유실을 초래한다.


---

## 3. 리스크/우려 (Risks)

### 3.1. [RED] Level 1 fallback의 종합 판정이 구현 블로커가 될 수 있다

SS11.2에서 인정한 대로, Level 1에서 "insight 한 문장 + knowledge_updates"만으로 종합 판정(selected/rejected)을 수행하면 정보 손실이 크다.

**구체적 위험**:
- 유사 테이블 비교 판정에는 두 테이블의 컬럼 구조, 날짜 분포, 샘플 데이터를 **동시에** 볼 수 있어야 한다
- 폐쇄망 모델(Solar Pro 2 70B)에서 Level 1의 다중 호출이 일관성을 해칠 수 있다

**제안**: Phase 4 구현 전에 실제 프롬프트 토큰 시뮬레이션을 수행하여 Level 1의 필요 빈도를 파악. 빈도가 낮다면 Level 1은 "에러 로그 + 가능한 범위에서 판정" 수준의 degraded mode로 단순화.


### 3.2. [RED] 멀티 라운드에서 explored_tables의 SELECTED/REJECTED 충돌

**시나리오**:
1. Round 1: interpreter가 TB_LOAN_MASTER를 SELECTED로 explored_tables에 적재
2. recovery_agent가 새 execution_plan 수립
3. Round 2: fetcher가 새 스텝 실행, search_table_meta에서 TB_LOAN_MASTER가 다시 발견
4. interpreter가 Round 2에서 TB_LOAN_MASTER를 다시 판정

**미설계 사항**:
- Round 1에서 SELECTED된 테이블이 Round 2에서 REJECTED될 수 있는가?
- 같은 테이블이 explored_tables에 **중복 적재**되는가, 기존 항목을 업데이트하는가?
- fetcher의 seen_tables로 중복 조회는 방지되지만, enrichment로 들어온 테이블과 직접 검색 테이블의 관계는?

현행 코드에서는 `candidate_tables.extend(new_tables)`로 단순 추가하고, `_merge_llm_inferred_fields`가 같은 이름의 기존 항목을 in-place 업데이트한다. 새 설계에서 이 merge 전략의 연속성이 미기술.

**제안**: explored_tables의 중복 관리 전략을 명시:
- (A) 테이블명 기준 upsert (후판정 우선)
- (B) 라운드별 별도 적재 후 최종 판정 시 병합
- (C) 이전 라운드 SELECTED는 recovery에서 명시적으로 재검토 대상으로 전환


### 3.3. [YEL] search_use_cases enrichment의 토큰 폭발 위험

유사 SQL 5건이 각각 3개 테이블을 사용하면 최대 15개 테이블의 전체 컬럼 정보가 raw_result에 포함. 100컬럼 테이블 15개 = 1,500개 컬럼 정보가 search_use_cases 한 스텝의 렌더링에 포함.

SS2.4의 "축소하지 않음" 원칙과 결합하면, search_use_cases 스텝 하나만으로도 Level 1 전환이 필요해질 수 있다. Level 1에서도 이 단일 스텝이 토큰 한도를 초과하면 처리 불가.

**제안**: search_use_cases enrichment 테이블의 컬럼은 "SQL에서 실제 사용된 컬럼 + PK"로 한정. 전체 컬럼이 필요하면 search_table_meta 스텝으로 별도 조회 유도.


### 3.4. [YEL] 폐쇄망 LLM에서 판단 가이드의 효과 불확실

SS11.4에서 인정한 대로, "확인하세요", "판단하세요" 수준의 가이드는 Solar Pro 2 70B에서 빈약한 insight를 생성할 위험이 크다. insight 품질 저하 -> discovered_facts 품질 저하 -> recovery_agent 재계획 품질 저하 -> 전체 파이프라인 정확도 저하의 연쇄 효과.

**제안**: 판단 가이드를 "If-Then" 패턴으로 구체화:
```
-> 날짜 분포에서 최근 10건의 간격이 일정하면 '일별 적재', 월말만 있으면 '월말 스냅샷'으로 판정하세요.
   시간 조건이 이 범위 밖이면 '시간 조건 불일치'로 보고하세요.
```


### 3.5. [GRN] ExecutionStep.raw_result에 JSON 비호환 타입 포함 가능성

Pydantic v2는 `Any` 타입 필드에 대해 검증을 수행하지 않지만, `model_dump()`나 JSON 직렬화에서 MongoDB `ObjectId`, `datetime` 등 비호환 타입이 문제가 될 수 있다. fetcher에서 str 변환을 보장하는 규칙이 필요.


---

## 4. 개선 제안 (Recommendations)

### 4.1. [HIGH] Phase 2 대체 전략을 명시적으로 설계

현행 Phase 2의 전체 순회 로직은 "모든 테이블에 샘플과 날짜분포가 있어야 비교 판정이 가능하다"는 전제에 기반한다. 이 전제는 SQL 생성 정확도에 직접 기여하므로 제거할 수 없다.

**권장 방안**: fetcher에 Phase 2를 유지하되, 결과를 state가 아닌 별도의 `enrichment_results: dict[str, Any]` 필드에 저장. interpreter가 이를 읽어 step 렌더링에 병합한 후 state에 적재.

### 4.2. [HIGH] raw_result의 메모리/직렬화 문제에 대한 방어 조치

1. interpreter가 state 적재를 완료한 후 `step.raw_result = None` 설정
2. `raw_result` 타입을 `dict[str, Any] | list[Any] | None`으로 제한
3. MongoDB `ObjectId` 등 비직렬화 객체는 fetcher에서 str 변환 보장

### 4.3. [HIGH] 멀티 라운드 테이블 중복 관리 전략 확정

- explored_tables에 테이블명 기준 upsert 전략 채택 권장
- selection_status는 최신 판정으로 덮어쓰기 (recovery 시나리오 대응)
- 이전 SELECTED 테이블이 재판정되면 로그에 변경 이력 기록

### 4.4. [MED] search_use_cases enrichment 테이블의 컬럼 범위 제한

"SQL에서 실제 참조된 컬럼 + PK + 질의 관련 키워드 매칭 컬럼"으로 제한하여 토큰 효율성 확보. 전체 컬럼이 필요하면 search_table_meta 스텝으로 별도 조회 유도.

### 4.5. [MED] Level 1 설계를 단순화

Level 1을 "스텝별 개별 + 종합 판정"이 아니라 "대형 스텝만 분리 + 나머지는 배치"로 변경:
- 렌더링 후 각 블록의 토큰 추정
- 단일 블록이 임계값 초과 시 해당 블록만 개별 호출
- 나머지 블록은 Level 0 배치에 포함
- 종합 판정은 Level 0 배치 결과 + 개별 호출 insight를 합쳐서 1회 수행

이 방식이 "전체 Level 1 전환"보다 구현 복잡도와 정보 손실 모두 적다.

### 4.6. [MED] 네이밍 변경을 별도 PR로 분리

CandidateTable -> TableEntry, candidate_tables -> explored_tables 변경은 최소 12개 소스 파일 + 6개 테스트 파일 + 2개 tracker 파일에 영향. 렌더러 로직 변경과 동시에 진행하면 리뷰와 디버깅이 어렵다. Phase 1을 별도 PR로 분리하여 네이밍 변경만 먼저 병합할 것을 권장.

### 4.7. [LOW] dead field 제거 시 하위 호환성 확인

`is_inferred`, `conflicted_bounce_count`, `last_verdict` 제거 전에:
- Redis/checkpoint에 저장된 기존 state가 있는지 확인
- Pydantic v2의 `model_validate`에서 unknown field를 허용하는지 확인
- 필요 시 deprecated 표시 후 다음 릴리즈에서 제거

---

## 5. SQL 생성 정확도에 대한 종합 평가

### 5.1. 긍정적 기여

1. **purpose + result 동시 제공**: LLM이 "왜 이 도구를 실행했고 결과가 뭔지" 한 블록에서 파악. 현행의 교차 참조 부담 해소. **정확도 향상에 직접 기여**.
2. **biz_terms/biz_manuals 누락 해소**: 현행에서 LLM이 보지 못하던 용어사전/매뉴얼 결과가 포함. **계수산출식/업무 규칙 기반 SQL 정확도 향상**.
3. **use_case 테이블 컨텍스트**: 유사 SQL의 테이블 구조를 함께 보여줌으로써 조인 패턴 재활용 가능성 판단이 정확해짐.
4. **날짜 적재 주기 판단**: recent_values 10건으로 일별/월별/영업일 판단 가능. **시간 조건 필터의 정확도 향상**.
5. **전체 컬럼 노출**: 현행의 축소된 컬럼 정보 대비 LLM이 적합 컬럼을 더 정확히 선택 가능.

### 5.2. 주의 필요

1. **토큰 폭발로 인한 Level 1 전환 빈도가 높으면** 오히려 비교 판정 품질이 현행보다 하락할 수 있다.
2. **폐쇄망 모델에서 긴 컨텍스트 처리 능력이 부족하면** 전체 컬럼 노출이 역효과.
3. **Phase 2 대체가 불완전하면** 샘플/날짜분포 없는 테이블이 판정 대상에 포함되어 오판 유발.

### 5.3. 결론

설계의 핵심 방향(스텝 단위 블록 조립, 정보 완전성, gatekeeper 패턴)은 SQL 생성 정확도 향상에 **실질적으로 기여**한다. 다만 위에서 식별한 Phase 2 대체 전략, 멀티 라운드 중복 관리, Level 1 설계의 세 가지 Gap이 해소되지 않으면 구현 시 블로커 또는 정확도 저하 요인이 된다.

---

## 부록: 사용자 질문에 대한 직접 답변

### Q1. 파이프라인 흐름 일관성

fetcher -> step.raw_result -> interpreter -> state 필드 흐름은 **대부분의 케이스에서 동작**하지만, 다음 예외 케이스가 문제:
- Phase 2 자동 수집 결과(스텝에 속하지 않는 관찰 데이터)가 흐름에서 탈락
- search_use_cases의 암묵적 enrichment 테이블이 별도 스텝 없이 raw_result에 포함되어 "한 스텝에 여러 도구의 결과가 혼합"

### Q2. enrichment 데이터의 생명주기

search_use_cases의 enrichment 테이블이 step.raw_result에 저장된 후:
1. interpreter가 렌더링하여 LLM에 전달
2. LLM 판정 후 explored_tables에 적재
3. recovery_agent가 execution_plan을 교체하면 step.raw_result 소멸
4. 하지만 explored_tables에 이미 적재되어 있으므로 데이터는 보존

**다음 라운드**: explored_tables에 있는 SELECTED 테이블은 유지. REJECTED도 유지(SS3.4). 새 라운드의 interpreter 배치에 "이전 라운드 결과" 형태로 포함되는지는 미기술.

### Q3. Round 간 충돌/중복 처리

**미설계**. 현행 코드의 `candidate_tables.extend()`는 중복 추가를 허용하고, `_merge_llm_inferred_fields`가 같은 이름 기준으로 in-place 업데이트한다. 새 설계에서 이 전략의 연속성이 보장되는지 확인 필요. 상세는 Risk 3.2 참조.

### Q4. 네이밍 변경 영향 범위

SS9 목록에서 **3개 파일 누락** 확인 (callback_handler.py, visualizer.py, `__init__.py`). 상세는 Missing 1.2 참조.

### Q5. Level 1 fallback의 블로커 여부

현시점에서 **구현 블로커는 아니다**. Phase 1-3을 Level 0 전용으로 먼저 구현하고, Phase 4에서 Level 1을 추가하는 순서이므로, Level 1 설계가 미확정이어도 핵심 기능은 동작한다. 다만 토큰 초과 시 graceful degradation이 없으므로 프로덕션 배포 전에 확정 필요.

### Q6. 관찰 데이터 섹션 제거와 중복

Phase 2에서 자동 수집된 샘플/날짜분포와 get_sample_rows/get_date_distribution 스텝의 결과가 **중복**된다. Phase 2를 완전히 제거하면 "스텝으로 명시되지 않은 테이블"의 관찰 데이터가 유실된다. 상세는 Incorrect 2.3 참조.
