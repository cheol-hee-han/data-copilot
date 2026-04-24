# 멀티 DB 라우팅 — target_db 단일 결정 설계

작성일: 2026-04-10
최종수정: 2026-04-10 (code-reviewer + pipeline-designer 전문가 리뷰 반영)
상태: 설계 확정, 구현 대기

## 1. 배경

폐쇄망 배포 후 업무 DB가 3종으로 확장되었다. **Cross-DB JOIN/조회는 지원하지 않는다**
(드라이버가 다르고, 각 DB는 독립된 네트워크 경계에 있음). 따라서 한 질의는 반드시
하나의 DB로 커밋되어야 한다.

| 시스템코드 | DB 종류 | 역할 | dialect |
| --- | --- | --- | --- |
| `TB_ADW_*` | Sybase IQ (정보계 DW) | 월/일 배치 집계·요약 | tsql |
| `TB_BDP_*` | Impala (빅데이터 플랫폼) | 이력성·트랜잭션 대용량 | hive |
| `TB_CRP_*` | Oracle (기간계) | 실시간 마스터 | oracle |

한 질의에서 여러 DB의 테이블이 동시에 탐색될 수 있고, SQL dialect가 서로 달라
**어느 DB로 커밋할지**를 한 곳에서 결정해야 한다.

## 2. 문제점 (현재 구조의 결함)

### 2.1 DB 결정 로직이 호출부에 산재

- `sql_generator.py`, `sql_validator.py`, `sql_executor.py`, `truncate.py`, `tools.py`가
  각자 `get_query_db()`를 호출
- 어떤 호출부는 `reason`을 넘기고, 어떤 호출부는 빈손으로 호출 → 항상 "adw" 폴백

### 2.2 확인된 버그

1. **`src/agents/nodes/reason/tools.py`** — TOOL_MAP 어댑터 4종
   (`_tool_get_sample_rows` 등, line 599~)이 내부 함수 호출 시 `db_source`를
   전달하지 않음. 도구는 `db_source` 파라미터를 받지만 어댑터가 드롭 → 항상 "adw"로 폴백.

2. **`src/agents/nodes/present/sql_executor.py:50`** — `manager.get_query_db()` (무인자).
   실행 단계에서 항상 "adw"로 라우팅.

3. **`src/utils/truncate.py:51`** — `get_query_db()` (무인자). 로그 포맷용 dialect가 틀림.

4. **`ConnectorManager._resolve_source_from_reason`** — `reason.explored_tables`
   **전부**를 순회. REJECTED/REFERENCE까지 포함해서 결정하므로 REFERENCE만 다른 DB인
   경우 엉뚱한 DB로 라우팅될 수 있음.

5. **`sql_generator.py:244~301` cross-DB 감지** — 이것도 `explored_tables` 전부 기준.
   SELECTED만 대상으로 해야 정확.

### 2.3 핵심 통찰

> **여러 DB의 테이블이 `explored_tables`에 섞이는 건 정상적이고 피할 수 없는 상황.**
> 에이전트는 탐색 중이라 어느 테이블을 실제로 쓸지 아직 모르니까요. 따라서 해법은
> "DB를 일찍 확정하는 게 아니라, **확정 시점을 명확히 분리**"하는 것.

### 2.4 사용자 특성 — 명확화 불가

사용자는 **IT 지식이 없는 은행 일반 직원**이다. "ADW와 CRP 중 어느 시스템에서 조회할까요?"
같은 DB 선택 질문은 답할 수 없다. 업무 용어("이번 달 여신 현황")로는 소통이 가능하지만,
시스템/DB 선택은 에이전트가 **추론으로 확정**하고 **결정 근거를 결과에 자연어로 설명**해야
한다. 명확화(clarification) 경로로 DB를 되묻지 않는다.

## 3. 설계 — "tag early, commit late"

### 3.1 5단계 흐름

```text
[Stage 1] 탐색 — context_retriever
  각 TableMeta.db_source를 prefix에서 파싱해 태깅.
  **이 동작은 이미 TableMeta.from_meta에서 수행 중 — 신규 추가 아님.**
  복수 DB 혼재 OK. SelectionStatus = CANDIDATE.
  (FORCED 모드에서는 target 외 DB 테이블을 승격 단계에서 필터.)

[Stage 2] 필터링 — context_interpreter
  도구 결과와 질의 의도를 근거로 SelectionStatus 갱신.
  SELECTED / REFERENCE / REJECTED 중 하나로 귀결.

[Stage 3] 커밋 — readiness_gate 내부, _finalize_phase 직후
  phase == GENERATING 으로 확정된 직후 인라인으로
  resolve_target_db(reason, settings) 호출 → TargetDbDecision 생성.
  SELECTED만 입력으로 사용. REFERENCE/REJECTED 제외.
  별도 노드 분리 금지 (LLM 호출 없는 순수 rule, state mutation 좁음).

[Stage 4] 저장 — reason.target_db 에 기록
  sql_generator / sql_validator / sql_executor / tools는
  reason.target_db 만 읽는다 (단일 진실원).
  결정 근거(decision_rationale)는 analyzer / process_summary_builder가
  사용자 응답에 포함.

[Stage 5] 무효화 — recovery_agent 재계획 시 리셋
  replan 시 reason.target_db = "", reason.target_db_decision = None
  → 다음 readiness_gate 재진입에서 Stage 3가 새 값을 도출.
  explored_tables 는 유지 (SELECTED가 재조정되는 루프).
```

### 3.2 자료형

```python
class TargetDbStatus(str, Enum):
    FORCED       = "forced"          # settings.target_db_code 고정 경로
    SINGLE       = "single"           # SELECTED 전부 단일 DB
    AMBIGUOUS    = "ambiguous"        # SELECTED에 복수 DB → 추론으로 1개 자동 확정
    NO_SELECTION = "no_selection"     # SELECTED 공집합

class TargetDbDecision(BaseModel):
    """DB 라우팅 결정 결과. Pydantic BaseModel — langgraph 체크포인터 직렬화용.

    프로젝트의 다른 state 타입(TableMeta, AmbiguitySignal 등)이 모두 BaseModel이므로
    dataclass 대신 BaseModel로 통일한다.
    """
    status: TargetDbStatus
    target: str = ""                    # "adw" | "bigdata" | "oracle" | ""
    chosen_tables: list[str] = Field(default_factory=list)
    dropped_tables: list[tuple[str, str]] = Field(default_factory=list)
    decision_rationale: str = ""        # 왜 이 DB를 선택했는지 자연어(사용자 노출용)
```

`ReasoningState`에 추가:

```python
target_db: str = ""                  # Stage 4에서 기록
target_db_decision: TargetDbDecision | None = None  # 디버깅·사용자 설명용
```

### 3.3 resolve_target_db 알고리즘

위치: **`src/services/target_db_resolver.py`** (신설 헬퍼 모듈).
`ConnectorManager._resolve_source_from_reason`은 **제거**되고 로직이 이곳으로 이전된다
(우선순위 규칙이 두 곳에 중복되지 않도록).

```text
signature: resolve_target_db(reason, settings) -> TargetDbDecision

0. settings.target_db_code 가 비어있지 않으면 → FORCED 경로 (§5)
1. SELECTED 테이블만 수집.
2. 없으면 → NO_SELECTION, target="" 반환 (readiness_gate 실패 취급).
3. 각 테이블의 db_source를 Set으로 모은다 (없으면 parse_db_source).
4. Set 크기 == 1 → SINGLE, 해당 값 + rationale("단일 DB 소속") 반환.
5. Set 크기 >= 2 → AMBIGUOUS:
   a. 우선순위 bigdata > oracle > adw 로 primary 선정.
   b. primary 외 테이블을 dropped_tables로 이동(SelectionStatus = REJECTED).
   c. decision_rationale 생성: 왜 primary를 선택했는지 + 어떤 대안이 있었는지.
   d. target = primary 반환.
```

**호출 지점**: `readiness_gate` 노드의 `_finalize_phase` 직후, `phase == GENERATING`으로
확정된 경우에만 1회 호출. 이미 `reason.target_db`가 있으면 스킵(멱등).

**우선순위 근거**:
- `bigdata`(BDP): 이력·트랜잭션의 원천. 상세 분석 질의는 여기에 답이 있을 확률이 가장 높음.
- `oracle`(CRP): 실시간 마스터. 정합성 필요 질의에서 우선.
- `adw`(ADW): 사전 집계·요약. 대체 가능한 경우 마지막.

우선순위는 "정보 해상도가 높은 쪽부터"라는 원칙. 이력(BDP)이면 요약(ADW)을 재현할 수
있지만, 요약에서 이력을 복원할 수는 없기 때문.

### 3.4 AMBIGUOUS 처리 정책 — 자동 확정 + 결과 설명

사용자(IT 비전문가)는 DB 선택 질문에 답할 수 없으므로 **명확화로 되묻지 않는다**. 대신:

1. **우선순위 규칙으로 자동 선정** (§3.3의 5단계)
2. **`decision_rationale`을 자연어로 생성** — 예:
   > "여신 이력 데이터(빅데이터 플랫폼)와 월말 요약 데이터(정보계 DW)가 모두 후보로
   > 올라왔습니다. 상세 집계가 가능한 이력 데이터를 기준으로 조회했습니다."
3. **analyzer가 사용자 응답에 포함** — 결과 설명부에 "어떤 데이터원에서 조회했는지 +
   다른 후보가 있었다는 사실"을 명시. 사용자가 결과를 보고 "다른 쪽으로 보고 싶다"고
   후속 질의하면 그때 재실행.

이 정책은 `.claude/rules/user-interaction.md`("결과에 어떤 조건으로 조회했는지 자연어
설명 포함")와 일치한다.

### 3.5 케이스 표

| 시나리오 | status | 처리 |
| --- | --- | --- |
| `settings.target_db_code` 설정됨 | FORCED | 해당 DB 고정, 불일치 테이블 드롭 |
| REFERENCE에만 다른 DB | SINGLE | REFERENCE 제외 후 단일 |
| SELECTED 단일 DB | SINGLE | 그대로 진행 |
| SELECTED 복수 DB | AMBIGUOUS | 우선순위로 자동 확정 + rationale 기록 |
| SELECTED 공집합 | NO_SELECTION | readiness_gate 되돌림 → 추가 탐색 |

## 4. 다중 테이블이 한 메타에 있는 경우

MongoDB 메타 한 건(예: "여신기본")에 3개 테이블이 함께 있을 수 있다.

```text
TB_ADW_LNB301M  (일배치 요약)
TB_BDP_LNB301L  (이력)
TB_CRP_LNB301M  (실시간 마스터)
```

- **Stage 1**: context_retriever가 3개 모두 TableMeta로 만들고 각각 db_source 태깅.
  모두 CANDIDATE.
- **Stage 2**: context_interpreter가 질의 의도를 보고 1~N개를 SELECTED로 승격.
  - "이번 달 신규 여신 건수" → BDP (이력성, 기간 질의)
  - "현재 여신 잔액" → CRP (실시간 스냅샷)
  - "월말 여신 요약 리포트" → ADW (사전 집계)
- **Stage 3**: resolve_target_db가 SELECTED 기준으로 결정. 여러 개가 SELECTED로
  올라와 있으면 §3.3·§3.4의 AMBIGUOUS 자동 확정 절차 적용.

context_interpreter가 "같은 비즈니스 엔티티를 다른 DB에서 본 3건 중 어느 것이
질의에 맞는가"를 판단할 수 있도록 프롬프트에서 명시 지침이 필요하다. 이렇게 하면 Stage
2에서 이미 SINGLE로 귀결되고, AMBIGUOUS는 진짜 모호한 케이스에만 발생한다.

## 5. FORCED 모드 — settings에 의한 고정 DB 지정

### 5.1 목적

배포 환경에 따라 "이 인스턴스는 항상 BDP만 본다"와 같이 단일 DB로 고정하고 싶은
요구가 있다. 개발/스테이징에서는 동적 라우팅을 그대로 쓰고, 특정 서비스 인스턴스에서만
설정으로 전환 가능해야 한다.

### 5.2 설정 구조 (`src/config.py`)

```python
class Settings(BaseSettings):
    # 시스템코드(3자리) → 내부 커넥터 키 매핑 — 단일 진실원
    db_source_code_map: dict[str, str] = {
        "ADW": "adw",       # Sybase IQ
        "BDP": "bigdata",   # Impala
        "CRP": "oracle",    # Oracle
    }

    # 고정 타겟 DB (3자리 시스템코드). 빈 값이면 동적 결정.
    target_db_code: str = ""   # "" | "ADW" | "BDP" | "CRP"

    # FORCED 모드에서 target 외 커넥터를 dummy로 유지할지 여부
    restrict_connectors_to_target: bool = False
```

- 기존 `ConnectorManager._DB_SOURCE_MAP`은 제거하고 `settings.db_source_code_map`만
  사용 (매핑이 두 군데 흩어지지 않음).
- `parse_db_source`도 settings를 읽도록 교체.
- 환경변수로 오버라이드 가능(`TARGET_DB_CODE=BDP`). `.env`만 바꿔 배포처별 전환.

### 5.3 부팅 시 검증

`src/config.py`의 `Settings` 클래스에 pydantic v2 `@model_validator(mode="after")`로 구현:

```python
@model_validator(mode="after")
def _validate_target_db_code(self) -> "Settings":
    if self.target_db_code and self.target_db_code not in self.db_source_code_map:
        raise ValueError(
            f"target_db_code={self.target_db_code!r} 가 "
            f"db_source_code_map 에 없음. 허용값: "
            f"{sorted(self.db_source_code_map.keys())}"
        )
    return self
```

- `target_db_code`가 비어있지 않은데 `db_source_code_map`에 없으면 즉시 fail-fast.
- `restrict_connectors_to_target=True`면 target 외 DB 커넥터는 실접속 스킵(dummy 유지) →
  운영 환경에서 불필요한 실커넥션 리소스 절감.

### 5.4 탐색 단계 조기 필터 (FORCED 전용)

FORCED 모드에서는 Stage 1에서 이미 가지치기:

- `context_retriever` 내부에 `_apply_forced_filter(tables, target_code)` 헬퍼를 추출.
  승격 직전 prefix 매칭으로 `db_source != target`인 테이블은 드롭.
  복잡도 상승을 억제하기 위해 **별도 함수로 분리**하여 단위 테스트 용이.
- 한 메타 레코드에 여러 DB 테이블이 묶인 경우(여신 LNB301 3종)에도 prefix 필터로 1개만 남음.
- 이득: 도구 호출·토큰·컨텍스트 낭비 없음.

동적 모드(빈 값)에서는 기존대로 전체 탐색. 두 경로는 `if settings.target_db_code:`
한 줄로 분기.

### 5.5 FORCED에서 SELECTED 공집합 처리

FORCED 모드로 인해 남은 테이블이 없으면 "고정 DB에 대응 테이블이 전혀 없음"이므로
readiness_gate 실패 취급 → `recovery_agent` 경로로 넘기고, 사용자에게
**"본 서비스는 {시스템명} 기준으로만 조회합니다. 해당 시스템에 관련 데이터가 없는
것으로 보입니다"** 식으로 설명한다.

## 6. 구현 체크리스트

### 6.1 자료형·헬퍼 신설

- [ ] **(1)** `ReasoningState`(Pydantic BaseModel)에 `target_db: str = ""`,
      `target_db_decision: TargetDbDecision | None = None` 추가.
- [ ] **(2)** `src/models/enums.py` 또는 `src/agents/state/state.py`에
      `TargetDbStatus`(str Enum), `TargetDbDecision`(**Pydantic BaseModel**) 정의.
      프로젝트의 다른 state 타입과 스타일 통일.
- [ ] **(3)** `src/services/target_db_resolver.py` **신설** —
      `resolve_target_db(reason, settings) -> TargetDbDecision` 구현.
      SELECTED 만 입력. `decision_rationale` 자연어 생성.
      기존 `ConnectorManager._resolve_source_from_reason` 로직을 이곳으로 이전.

### 6.2 호출부 단일화 (기존 로직 제거 포함)

- [ ] **(4)** `readiness_gate` 노드 `_finalize_phase` 직후,
      `phase == Phase.GENERATING` 일 때 `resolve_target_db` 인라인 호출.
      이미 `reason.target_db` 가 있으면 스킵(멱등).
- [ ] **(5)** `ConnectorManager.get_query_db` 수정:
      `reason.target_db`가 있으면 그 값을 **최우선** 사용.
      `_resolve_source_from_reason` **삭제** — 우선순위 로직 중복 방지.
      `_DB_SOURCE_MAP` 제거, `settings.db_source_code_map` 참조로 교체.
- [ ] **(6)** `sql_generator.py` 244~301 cross-DB 감지 로직 **삭제** →
      `reason.target_db_decision` 만 참조하여 dialect/prompt 선택.
      기존 코드가 생성하던 `AmbiguitySignal(INFER, ambiguity_type=TABLE,
      source_node="sql_generator")` 도 함께 제거.
- [ ] **(7)** `recovery_agent` replan 분기에서
      `reason.target_db = ""`, `reason.target_db_decision = None` 명시적 리셋.
      replan 루프에서 SELECTED가 재조정되면 다음 readiness_gate에서 새 값 도출.

### 6.3 버그 수정 (§2.2 5건 + 신규 2건)

- [ ] **(8)** `src/agents/nodes/reason/tools.py` TOOL_MAP 어댑터 4종
      (`_tool_get_sample_rows`, `_tool_get_column_values`, `_tool_get_column_profile`,
      `_tool_get_date_distribution`) — **`execute_tool` 시그니처 확장**
      `(tool_name, tool_input, *, reason, **kwargs)`. 어댑터는
      `reason.target_db`를 1순위, 없으면 `parse_db_source(table_name)` 폴백으로
      내부 함수에 `db_source` 전달. 단일 진실원 원칙 유지.
- [ ] **(9)** `src/agents/nodes/present/sql_executor.py:50` 무인자
      `get_query_db()` → `get_query_db(reason)` 로 수정.
- [ ] **(9a)** `src/utils/truncate.py:51` 무인자 `get_query_db()` → reason 전달.
- [ ] **(9b)** `src/agents/nodes/reason/context_retriever.py:125` 무인자 호출 수정.
- [ ] **(9c)** `src/agents/nodes/reason/sql_validator.py:84, 681` —
      reason.target_db 경로가 정상 작동하는지 리그레션 테스트.

### 6.4 FORCED 모드

- [ ] **(10)** `src/config.py` `Settings`에 3개 필드 추가:
      `db_source_code_map: dict[str, str]`,
      `target_db_code: str = ""`,
      `restrict_connectors_to_target: bool = False`.
      `@model_validator(mode="after")` 로 `target_db_code ∈ db_source_code_map` 검증.
- [ ] **(11)** `ConnectorManager.parse_db_source` 정적 메서드가
      `settings.db_source_code_map` 을 읽도록 변경 (기존 `_DB_SOURCE_MAP` 제거).
      `TableMeta.from_meta` 의 기존 호출 경로는 그대로 유지.
- [ ] **(12)** `resolve_target_db`에 FORCED 분기 + `TargetDbStatus.FORCED` 처리.
      FORCED에서 SELECTED 공집합이면 `FailureType.NO_TABLE` 경로로 위임.
- [ ] **(13)** `context_retriever`에 `_apply_forced_filter(tables, target_code)`
      헬퍼 추출 + 승격 직전 적용. 단위 테스트 용이.
- [ ] **(14)** `ConnectorManager.__init__` / `connect_all` 수정:
      `settings.restrict_connectors_to_target=True`면 target 외 DB 커넥터는
      dummy 인스턴스 유지 (실접속 스킵).

### 6.5 결정 근거 사용자 노출

- [ ] **(15)** `analyzer`가 `target_db_decision.decision_rationale`을 사용자 응답
      설명부에 포함. 빈 문자열이면 생략. AMBIGUOUS/FORCED에서 주로 노출.
- [ ] **(15a)** `src/services/process_summary_builder.py` 에도
      `decision_rationale` 소비 경로 추가 — "조회 과정 요약" 표시용.
- [ ] **(16)** `resources/prompts/present/analyzer_system.txt` 에
      "조회 시스템 선택 근거를 결과 설명에 포함" 지침 추가 (prompt-engineer 스킬 활용).

### 6.6 리그레션 방지

- [ ] **(17)** `recovery_agent` replan → 재진입 → target_db 재도출 단위 테스트.
- [ ] **(18)** FORCED 모드 + SELECTED 공집합 → `NO_TABLE` 전환 테스트.
- [ ] **(19)** AMBIGUOUS 자동 확정 + `decision_rationale` 생성 골든 케이스.

## 7. 후속 (별도 이슈)

- 골든셋 회귀 테스트: 4개 dialect별(postgres/tsql/hive/oracle) 최소 5 케이스씩 추가
- Oracle 커넥터 실기동 스모크 테스트 (폐쇄망 컷오버 전)
- `docs/architecture/architecture.md`에 3 DB 라우팅 + FORCED 모드 다이어그램 갱신
- `context_interpreter` 프롬프트에 "동일 엔티티 여러 DB 중 선택" 지침 추가
  (AMBIGUOUS 발생 빈도를 낮추기 위함)
- `decision_rationale` 문구 템플릿 정비 (너무 기술적이지 않게)
