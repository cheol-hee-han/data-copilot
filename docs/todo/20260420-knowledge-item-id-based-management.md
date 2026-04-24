# KnowledgeItem id 기반 관리 전환 + INSERT 차단 구현 계획

> 작성일: 2026-04-20
> 대상: nl-sql-developer / prompt-engineer 서브에이전트
> 원본 논의: 본 문서는 대화를 통해 합의된 설계를 단독 구현 가능한 수준으로 정리한 것이다.

## 목차

- [1. 배경 & 문제](#1-배경--문제) — N-02 사례로 드러난 INSERT 경로 오용과 key 표기 충돌
- [2. 설계 결정 요약](#2-설계-결정-요약) — 합의된 3가지 방침
- [3. `KnowledgeItem` 필드 재정의](#3-knowledgeitem-필드-재정의) — id / key / value 역할 분리
- [4. 변경 범위 전수 조사](#4-변경-범위-전수-조사) — 수정 대상 파일 목록
- [5. 구현 단계별 체크리스트](#5-구현-단계별-체크리스트) — P1 → P2 순서로 실행
- [6. 프롬프트 수정 상세](#6-프롬프트-수정-상세) — 파일별 before/after
- [7. LLM 실수 대응 매트릭스](#7-llm-실수-대응-매트릭스) — 8가지 시나리오와 가드
- [8. 테스트 계획](#8-테스트-계획) — 유닛 + 회귀 E2E
- [9. 배포 전략](#9-배포-전략) — 단일 PR vs 단계적 롤아웃
- [10. 하지 않을 것](#10-하지-않을-것) — 합의 범위 밖 명시

---

## 1. 배경 & 문제

### 1.1 N-02 장애 사례

질의: `지점이 몇 개야?` (E2E 시나리오 N-02)

- 기대: `replan_count ≤ 1`, 단순 COUNT 질의
- 실제: `replan_count=4`, recovery_agent 루프 4회 후 생성 성공
- trace 분석 결과:
  1. context_interpreter가 iter 2에서 `measure:지점수` (공백 없는 key)를 **신규 생성** — 기존 `measure:지점 수`(공백 포함)와 별개 항목으로 취급됨
  2. 동일 interpreter가 `filter:BR_DCD='02'`를 **business-implicit 파생 필터**로 INSERT — 사용자 질의에 없는 조건
  3. 두 INSERT 모두 `is_critical=True` 기본값으로 생성되어 `readiness_gate` 차단 요인이 됨
  4. `_dedup_knowledge_items`는 exact string match만 수행 → 공백 차이 구분 못함

### 1.2 근본 원인 3층

| 층 | 원인 |
|---|---|
| L1 — 데이터 모델 | `KnowledgeItem.key`가 semantic label("measure:지점 수")이면서 동시에 식별자로 쓰임 → 표기 차이(공백, 대소문자 등)로 중복 발생 |
| L2 — 프롬프트 | context_interpreter_system.txt L35는 "unresolved_items의 key를 그대로 사용"을 지시하지만 LLM이 위반. 또한 L36-39의 3조건 gate가 INSERT를 허용함 |
| L3 — 개념 혼동 | KI의 정체성("사용자 명시 의도의 검증 대상")이 프롬프트/코드에 명시되지 않아, LLM이 탐색 중 발견한 business-implicit 조건까지 KI로 올려 게이트를 차단 |

### 1.3 왜 지금 해결해야 하는가

- E2E 2026Q2 실행에서 Minor 이상 WARN/FAIL 중 다수가 "과도한 recovery" 패턴
- LLM 업그레이드 시(Solar Pro 2 70B → Qwen3.5 397B/GPT OSS 120B) 프롬프트·표기 준수율은 오를 수 있으나, **모델 의존성 없는 구조적 가드**가 없으면 재발 가능
- 추가 기능 개발 전 지식 관리의 정체성을 고정해야 이후 변경이 안정적

---

## 2. 설계 결정 요약

### 2.1 KI는 "사용자 명시 의도의 검증 장치"

정규화(`reasoning_preparer._initialize_knowledge_items`)에서 파생된 slot만 KI seed 대상. 탐색 중 발견되는 business-implicit 조건은 KI 대상이 **아님**.

### 2.2 탐색 중 INSERT 금지, UPDATE만 허용

- context_interpreter의 `knowledge_updates`는 **기존 KI의 `id`로만** 참조해 갱신
- 존재하지 않는 id 참조 → 해당 update 폐기 + warning log
- INSERT는 rule-based로 차단 (프롬프트 규약 + 파싱 가드 이중화)

### 2.3 식별자와 라벨 분리 — `id` 기반 관리

- `id` (예: `K1`, `K2`) — 불변 식별자, 프로그램 참조 + LLM 응답 ref
- `key` (예: `measure:지점 수`) — display label, LLM에게 "무엇을 해소하나" 설명
- `value` (예: `COUNT(DISTINCT BLNG_BRCD)`) — 해소된 SQL 표현

### 2.4 명시적으로 **하지 않는** 것

- `discovered_facts` 구조 변경 (현행 `list[str]`과 tool-insight 단일 의미 유지)
- INSERT 리다이렉트 채널 신설 (거부된 INSERT는 warning log 후 폐기)
- entity / dimension seed 추가 (entity는 탐색 키워드 역할 유지, dimension은 sql_generator가 group_by 구성에서 사용)
- `is_critical` 기본값 변경 (seed에서만 KI가 생기면 critical 기본값으로도 게이트 오차단 없음)

---

## 3. `KnowledgeItem` 필드 재정의

### 3.1 필드 역할표

| 필드 | 역할 | 예시 | 수정 주체 | 수정 가능 시점 |
|---|---|---|---|---|
| `id` | 식별자 (primary) | `"K1"`, `"K2"` | `_initialize_knowledge_items` | seed 시 1회 |
| `key` | display label (`"{role}:{term}"`) | `"measure:지점 수"` | `_initialize_knowledge_items` | seed 시 1회 (불변) |
| `value` | 해소된 SQL 표현 | `"COUNT(DISTINCT BLNG_BRCD)"` | context_interpreter | 탐색 중 갱신 |
| `status` | 확신도 상태 enum | `CANDIDATE`, `CONFIRMED` 등 | context_interpreter | 탐색 중 갱신 (승격 규칙 준수) |
| `confidence` | 수치 confidence | `0.9` | context_interpreter | 탐색 중 갱신 |
| `source` | 해소 근거의 도구명 | `"search_table_meta"` | context_interpreter | 탐색 중 갱신 |
| `evidence` | 상세 근거 리스트 | `["BLNG_BRCD 컬럼 확인"]` | context_interpreter | 탐색 중 누적 |
| `is_critical` | 게이트 차단 항목 여부 | `True` (기본) | `_initialize_knowledge_items` | seed 시 1회 |

### 3.2 `state.py` 변경

**파일**: `src/agents/state/state.py` L99-121

```python
class KnowledgeItem(BaseModel):
    """탐색 과정에서 축적되는 개별 지식 단위.

    id와 key의 역할 분리:
      - id: 불변 식별자. LLM 응답의 knowledge_updates가 참조하는 대상.
      - key: display label ("{role}:{term}"). 사람/LLM이 "무엇을 해소하나" 이해하기 위한 설명.
      - value: 탐색 결과 해소된 SQL 표현/코드값.

    seed 이후 id와 key는 불변. LLM은 id로만 기존 항목을 갱신하며 신규 생성 불가.
    """

    id: str = ""   # ← 기존 knowledge_id 리네이밍 (primary identifier)
    key: str = ""  # display label, "{role}:{term}" 포맷 (role: measure/filter/dimension/entity/output/table)
    value: str = ""
    confidence: float = 0.0
    status: ConfidenceStatus = ConfidenceStatus.UNRESOLVED
    source: str = ""
    evidence: list[str] = Field(default_factory=list)
    is_critical: bool = True

    def promote(
        self, new_status: ConfidenceStatus, value: str,
        confidence: float, source: str, evidence: str,
    ) -> None:
        """지식 항목의 상태를 승격한다."""
        self.status = new_status
        self.value = value
        self.confidence = confidence
        self.source = source
        self.evidence.append(evidence)
```

**변경점**: `knowledge_id` → `id` 리네이밍. Pydantic 직렬화 호환을 위해 `Field(alias="knowledge_id")` 추가는 **하지 않는다** — 단일 PR로 일괄 변경.

### 3.3 관련 필드 — `DeadEnd.related_knowledge_keys`

**파일**: `src/agents/state/state.py` L373

```python
class DeadEnd(BaseModel):
    ...
    related_knowledge_ids: list[str] = Field(default_factory=list)  # ← 기존 related_knowledge_keys 리네이밍
```

**근거**: DeadEnd가 "어떤 KI 관련 실패인지"를 추적하는데, key 대신 id로 참조해야 표기 차이 문제 없음.

---

## 4. 변경 범위 전수 조사

### 4.1 필드명 치환 대상

`ki.knowledge_id` → `ki.id` 치환:

| 파일 | 라인 | 수정 내용 |
|---|---|---|
| `src/agents/state/state.py` | 110 | 필드 정의 `knowledge_id` → `id` |
| `src/agents/nodes/reason/reasoning_preparer.py` | 73 | `ki.knowledge_id = f"K{i + 1}"` → `ki.id = f"K{i + 1}"` |
| `src/agents/nodes/reason/reasoning_preparer.py` | 132 | `f"{ki.knowledge_id}: ..."` → `f"{ki.id}: ..."` |
| `src/agents/nodes/reason/context_interpreter.py` | 247 | 동일 |
| `src/agents/nodes/reason/sql_generator.py` | 324 | 동일 |

`de.related_knowledge_keys` → `de.related_knowledge_ids` 치환:

| 파일 | 라인 | 수정 내용 |
|---|---|---|
| `src/agents/state/state.py` | 373 | 필드 정의 |
| `src/agents/nodes/reason/recovery_agent.py` | 416, 427 | DeadEnd 생성 시 `related_knowledge_keys=` → `related_knowledge_ids=` |
| `src/agents/nodes/reason/recovery_agent.py` | 524 | `if ki.key in de.related_knowledge_keys` → `if ki.id in de.related_knowledge_ids` |

### 4.2 dedup 로직 변경

**파일**: `src/agents/nodes/reason/context_interpreter.py` L1400-1414

**현재 시그니처 유지 필수**: `(knowledge_items: list) -> None` + in-place 변형 (`knowledge_items[:] = [...]`). 호출부는 [context_interpreter.py:216-217](src/agents/nodes/reason/context_interpreter.py#L216-L217)에서 반환값을 사용하지 않으므로 반환값 시그니처로 바꾸면 dedup 결과가 반영되지 않음.

```python
# BEFORE (실제 현행 — L1400-1414)
def _dedup_knowledge_items(knowledge_items: list) -> None:
    """같은 key의 KI가 여러 건이면 최고 confidence 항목만 유지한다."""
    best_ki: dict[str, int] = {}
    for i, ki in enumerate(knowledge_items):
        if ki.key in best_ki:
            existing_idx = best_ki[ki.key]
            if ki.confidence > knowledge_items[existing_idx].confidence:
                best_ki[ki.key] = i
        else:
            best_ki[ki.key] = i

    keep_indices = set(best_ki.values())
    knowledge_items[:] = [
        ki for i, ki in enumerate(knowledge_items) if i in keep_indices
    ]

# AFTER
def _dedup_knowledge_items(knowledge_items: list) -> None:
    """id 기반 dedup. 동일 id에 대해 더 높은 confidence/status 를 유지. in-place 변형."""
    best_ki: dict[str, int] = {}
    for i, ki in enumerate(knowledge_items):
        if not ki.id:
            # id 없는 항목 (이론상 발생 안 함 — seed에서 반드시 채번) → 건너뜀
            continue
        if ki.id in best_ki:
            existing_idx = best_ki[ki.id]
            if ki.confidence > knowledge_items[existing_idx].confidence:
                best_ki[ki.id] = i
        else:
            best_ki[ki.id] = i

    keep_indices = set(best_ki.values())
    knowledge_items[:] = [
        ki for i, ki in enumerate(knowledge_items) if i in keep_indices
    ]
```

### 4.3 INSERT 차단 가드 신설

**대상 두 곳 모두 동일 가드 적용**:

1. `src/agents/nodes/reason/context_interpreter.py` **L596-608** (스텝별 해석 — `knowledge_updates` 추출)
2. `src/agents/nodes/reason/context_interpreter.py` **L648-662** (`_parse_batch_result` — 배치 응답 파싱)

두 사이트 모두 `KnowledgeItem(key=ku.get("key", ""), ...)` 패턴으로 LLM 응답 dict를 KnowledgeItem으로 변환하므로 **같은 INSERT 루트**. 한 쪽만 가드하면 배치 경로로 우회됨.

**공통 가드 함수 신설** (코드 중복 방지):

```python
def _build_knowledge_update(
    ku: dict,
    existing_id_set: set[str],
    default_source: str,
) -> KnowledgeItem | None:
    """LLM 응답 dict → KnowledgeItem 변환 + INSERT 차단 가드.

    Returns None 이면 호출부는 해당 update 를 폐기한다.
    정규화 규칙(E7/E8 대응): 대문자화 + 공백/소괄호 제거.
    """
    raw_id = (ku.get("id") or "").upper().strip().strip("()").strip()

    # Guard 1: id 누락
    if not raw_id:
        logger.warning(
            "knowledge_updates: id missing — dropped. raw=%s",
            truncate_log(str(ku), 200),
        )
        return None

    # Guard 2: 형식 오류 (K숫자가 아님)
    if not raw_id.startswith("K") or not raw_id[1:].isdigit():
        logger.warning(
            "knowledge_updates: malformed id %r — dropped", ku.get("id"),
        )
        return None

    # Guard 3: 존재하지 않는 id (INSERT 시도)
    if raw_id not in existing_id_set:
        logger.warning(
            "knowledge_updates: unknown id %s — dropped (INSERT blocked). raw=%s",
            raw_id, truncate_log(str(ku), 200),
        )
        return None

    # UPDATE 수용 — key/is_critical 은 병합 단계에서 기존 KI 값으로 보존
    return KnowledgeItem(
        id=raw_id,
        key="",
        value=ku.get("value", ""),
        confidence=ku.get("confidence", 0.5),
        status=ku.get("new_status", ConfidenceStatus.CANDIDATE),
        source=ku.get("source", default_source),
        evidence=[ku.get("evidence", "")],
        is_critical=False,
    )
```

**L596-608 재배선** (스텝별 해석):

```python
existing_id_set = {ki.id for ki in knowledge_items}
for ku in step_result.get("knowledge_updates", []):
    item = _build_knowledge_update(ku, existing_id_set, "Level1해석")
    if item is not None:
        all_knowledge_updates.append(item)
```

**L648-662 재배선** (`_parse_batch_result`):

```python
def _parse_batch_result(
    data: dict,
    existing_id_set: set[str],  # ← 신규 인자
) -> BatchInterpretResult:
    knowledge_updates: list[KnowledgeItem] = []
    for interp in data.get("interpretations", []):
        for ku in interp.get("knowledge_updates", []):
            item = _build_knowledge_update(ku, existing_id_set, "배치해석")
            if item is not None:
                knowledge_updates.append(item)
    return BatchInterpretResult(
        interpretations=data.get("interpretations", []),
        knowledge_updates=knowledge_updates,
    )
```

**호출부 수정**: `_parse_batch_result` 호출 지점에서 `existing_id_set = {ki.id for ki in reason.knowledge_items}` 인자 전달. 호출 경로는 [context_interpreter.py](src/agents/nodes/reason/context_interpreter.py) 내부 Level2 배치 LLM 호출의 `parse_fn` 바인딩이므로 구현자가 클로저로 주입하거나 functools.partial 사용.

**병합 로직 추가 필요**: `_dedup_knowledge_items` 이후 또는 그와 병행하여, UPDATE 전용 항목(key가 빈 상태)을 기존 KI와 병합할 때 기존 KI의 `key`와 `is_critical`을 보존한다. 아래 함수 신설:

```python
def _merge_updates_into_items(
    existing: list[KnowledgeItem],
    updates: list[KnowledgeItem],
) -> list[KnowledgeItem]:
    """UPDATE 항목을 기존 KI에 병합. id로 매칭.

    - 기존 KI의 key, is_critical은 보존
    - value, status, confidence, source, evidence는 UPDATE로 덮어씀
    - evidence는 append 누적
    """
    by_id = {ki.id: ki for ki in existing}
    for upd in updates:
        base = by_id.get(upd.id)
        if not base:
            continue
        base.value = upd.value or base.value
        if _should_promote(base.status, upd.status):
            base.status = upd.status
        base.confidence = max(base.confidence, upd.confidence)
        if upd.source:
            base.source = upd.source
        for ev in upd.evidence:
            if ev and ev not in base.evidence:
                base.evidence.append(ev)
    return existing
```

기존 파싱 흐름이 `_dedup_knowledge_items`에 의존하던 부분을 이 병합 로직으로 재배선. **구현자 재량**으로 기존 흐름과 어떻게 통합할지 결정하되, 최종 결과는 "기존 KI 목록 크기 불변, 갱신만 반영"이어야 한다.

### 4.4 프롬프트 렌더러 변경

**`_serialize_unresolved_items`** (context_interpreter.py L345-375): 각 항목 앞에 `(K1) ` prefix 추가. 상세는 [§6](#6-프롬프트-수정-상세) 참조.

**`state.format_confirmed_text`** (state.py L693-705): 동일하게 `(K1)` prefix 추가. (※ 이전 초안의 `format_confirmed_knowledge_text`는 오기. 실제 메서드명은 `format_confirmed_text`.)

**recovery_agent.py**: L911-956, L1233 등 `ki.key` 렌더 위치에 prefix 추가.

**reasoning_preparer.py:132, context_interpreter.py:247, sql_generator.py:324**: tracker 이벤트 내부 문자열이므로 현행 유지 가능 (`f"{ki.id}: {ki.key} ..."` 이미 id 포함).

### 4.5 key 기반 로직 보존 (변경 없음)

`ki.key.partition(":")` 혹은 `ki.key.removeprefix("table:")` 같이 **role 추출**에 key를 쓰는 코드는 그대로 둔다. key 포맷 `"{role}:{term}"` 규약은 유지되며, 변경은 "식별에 key를 쓰지 않는다"는 의미. role 판별은 key prefix 그대로 사용.

**해당 위치** (변경 없음):

- `context_interpreter.py:353` `prefix, _, label = ki.key.partition(":")`
- `context_interpreter.py:1374, 1376, 1427, 1429` `ki.key.startswith(_TABLE_KEY_PREFIX)` 등
- `result_finalizer.py:101, 103` `ki.key.removeprefix("table:")`, `ki.key.startswith("table:")`
- `recovery_agent.py:911, 936` `prefix, _, _ = ki.key.partition(":")`

### 4.6 readiness_gate / confidence_scorer 검증

**파일**: `src/agents/nodes/reason/readiness_gate.py:267`

```python
unresolved = [
    ki.key for ki in reason.knowledge_items  # 이대로 OK — 로깅/실패 기록 용도
    if ki.status in (ConfidenceStatus.UNRESOLVED, ConfidenceStatus.CONFLICTED)
]
```

이 `unresolved`는 실패 타입 판정/로깅에만 쓰이므로 key 그대로 둔다.

**파일**: `src/services/confidence_scorer.py:124, 166`

```python
items = [ki for ki in reason.knowledge_items if ki.is_critical]
```

`is_critical` 기반 필터링이므로 key/id와 무관. 변경 없음.

**`recovery_agent.py:395, 397, 538`**: `ki.is_critical` 기반 필터링. key와 무관, 변경 없음.

---

## 5. 구현 단계별 체크리스트

전체를 단일 PR로 진행 (배포 전략 §9 참조). 아래 순서로 파일 편집.

### 5.1 데이터 모델 변경

- [ ] `src/agents/state/state.py`
  - [ ] `KnowledgeItem.knowledge_id` → `id`
  - [ ] `KnowledgeItem` docstring에 id/key/value 역할 명시 (본 문서 §3.1 표 요약)
  - [ ] `DeadEnd.related_knowledge_keys` → `related_knowledge_ids`
  - [ ] `format_confirmed_text` 렌더에 `(id)` prefix (※ 실제 메서드명은 `format_confirmed_text`; 초기 초안의 `format_confirmed_knowledge_text`는 오기)

### 5.2 seed 단계

- [ ] `src/agents/nodes/reason/reasoning_preparer.py`
  - [ ] L73: `ki.knowledge_id` → `ki.id`
  - [ ] L132: tracker 이벤트 문자열 (동일)
  - [ ] `_initialize_knowledge_items`는 seed에서 이미 `id`를 채번하도록 순서 정렬 (현재 L72-74에서 후속 할당 → 함수 내부로 이동 가능하나 필수 아님)

### 5.3 interpreter 변경

- [ ] `src/agents/nodes/reason/context_interpreter.py`
  - [ ] L247: tracker 이벤트 문자열
  - [ ] L345-375 `_serialize_unresolved_items`: 각 줄에 `(id) ` prefix
  - [ ] `_build_knowledge_update` 공통 가드 함수 신설 (§4.3)
  - [ ] L596-608: 스텝별 해석 루트 — 공통 가드로 교체 (§4.3)
  - [ ] L648-662 `_parse_batch_result`: 배치 루트 — 공통 가드로 교체 + `existing_id_set` 인자 추가 (§4.3) **누락 시 INSERT 우회 발생**
  - [ ] `_parse_batch_result` 호출부: `existing_id_set={ki.id for ki in reason.knowledge_items}` 바인딩 (closure 또는 functools.partial)
  - [ ] `_merge_updates_into_items` 신설 (§4.3)
  - [ ] L1400-1414 `_dedup_knowledge_items`: id 기반 + **in-place 계약 유지** (§4.2)
  - [ ] `_dedup_knowledge_items`와 `_merge_updates_into_items` 호출 흐름 재배선

### 5.4 recovery_agent 변경

- [ ] `src/agents/nodes/reason/recovery_agent.py`
  - [ ] L395: 변수명과 값 표현식 동시 변경. `unresolved_keys = [ki.key for ki in reason.knowledge_items ...]` → `unresolved_ids = [ki.id for ki in reason.knowledge_items ...]` (key→id 값 변경 필수 — id 기반 매칭이 목적이므로 변수명만 바꾸고 값은 `ki.key`로 두면 L524 비교가 깨짐)
  - [ ] L416, 427: DeadEnd 생성 시 `related_knowledge_ids=unresolved_ids`
  - [ ] L524: `if ki.id in de.related_knowledge_ids`
  - [ ] L911-956: 프롬프트 렌더 각 줄에 `(id) ` prefix
  - [ ] L1233: 미해소 용어 로깅 — id 포함

### 5.5 sql_generator / sql_validator

- [ ] `src/agents/nodes/reason/sql_generator.py` L324: tracker 이벤트 문자열 (이미 id 포함, 유지)
- [ ] `src/agents/nodes/reason/sql_validator.py`: KI 렌더 위치 점검 (prefix 적용 확인)
- [ ] `src/agents/state/state.py` `format_confirmed_knowledge_text`: sql_generator가 이 함수로 KI를 렌더링하므로 여기 prefix 추가

### 5.6 result_finalizer

- [ ] `src/agents/nodes/reason/result_finalizer.py`: key 기반 table 추출 유지 (변경 없음), 단 `ki.knowledge_id` 참조가 있으면 `ki.id`로 수정

### 5.7 프롬프트 수정

§6 참조. 아래 파일 수정:

- [ ] `resources/prompts/reason/context_interpreter_system.txt`
- [ ] `resources/prompts/reason/recovery_agent_system.txt`
- [ ] `resources/prompts/reason/sql_generator_system.txt`
- [ ] `resources/prompts/reason/sql_generator_system_oracle.txt`
- [ ] `resources/prompts/reason/sql_generator_system_postgres.txt`
- [ ] `resources/prompts/reason/sql_generator_system_impala.txt`
- [ ] `resources/prompts/reason/sql_generator_system_sybase_iq.txt`
- [ ] `resources/prompts/reason/sql_validator_system.txt`

### 5.8 유닛/E2E 테스트

**필드 리네이밍에 따라 파손되는 기존 테스트 (사전 수정 필수)**:

- [ ] `tests/auto/unit/test_cancel.py:328` — `knowledge_id="K1"` kwarg → `id="K1"`
- [ ] `tests/auto/unit/test_recovery_agent.py:57` — factory fixture `knowledge_id=kid` kwarg → `id=kid`
- [ ] `tests/auto/unit/test_process_summary_builder.py:356, 361, 369` — `knowledge_id="K1"` kwarg + `result["_knowledge_items"][0]["knowledge_id"]` 어설션 → `id`
- [ ] `tests/auto/unit/test_turn_snapshot_store.py:585, 591, 601, 617, 684, 715` — dict key `"knowledge_id"` 및 `items[0].knowledge_id` 어설션 → `"id"` / `items[0].id`

**신규·보강 테스트**:

- [ ] `tests/auto/unit/test_reasoning_preparer.py` — id 채번 검증 보강
- [ ] `tests/auto/unit/test_context_interpreter.py` (있으면) — INSERT 차단 + UPDATE 경로 + unknown id 시나리오. **두 INSERT 사이트(L596-608, L648-662) 모두 커버**.
- [ ] 신규: `tests/auto/unit/test_knowledge_item_merge.py` — `_merge_updates_into_items` 단위 검증
- [ ] E2E 회귀: `tests/manual/e2e/run_scenarios.py`로 N-02 재실행. `replan_count ≤ 1` 복구 확인

---

## 6. 프롬프트 수정 상세

### 6.1 공통 원칙

1. **`(K1)` 표기**는 프롬프트 표시용만. LLM 응답 JSON의 `id` 필드에는 **소괄호 없이** `"K1"`.
2. unresolved_items / 확인된 지식 항목 / 미해소 용어 등 KI를 나열하는 모든 섹션은 `(id) ` prefix 통일.
3. `knowledge_updates` 스키마에서 `key` 필드 **삭제**, `id` 필드 **required**.

### 6.2 `context_interpreter_system.txt`

**L28, L35 수정**:

```diff
- - unresolved_items가 끝까지 남아도 억지로 해소하지 않는다.
+ - unresolved_items가 끝까지 남아도 억지로 해소하지 않는다. unresolved_items 범위 밖의 항목을 신규로 만들지 않는다.

- - 미해소 지식 항목을 도구 결과로 해소할 수 있으면, unresolved_items의 key를 그대로 사용하여 knowledge_updates에 포함한다.
+ - 미해소 지식 항목을 도구 결과로 해소할 수 있으면, unresolved_items의 id(예: K1)를 knowledge_updates[*].id에 그대로 사용한다. id 외의 필드로 항목을 식별하지 않는다.
```

**L36-39 3조건 수정**:

```diff
- - knowledge_updates는 아래 3조건을 **모두** 만족할 때만 생성한다:
-     a) unresolved_items와 직접 관련됨
-     b) SQL 생성에 실제 사용 가능함
-     c) 구체적인 SQL 표현(value)으로 변환 가능함
+ - knowledge_updates는 아래 3조건을 **모두** 만족할 때만 생성한다:
+     a) knowledge_updates[*].id 가 unresolved_items에 존재하는 id
+     b) SQL 생성에 실제 사용 가능함
+     c) 구체적인 SQL 표현(value)으로 변환 가능함
+ - 이 외의 경우(신규 개념 발견 등)는 knowledge_updates 가 아닌 해당 스텝의 insight 문장으로 기록한다.
```

**`## 미해결 지식 항목 (unresolved_items)` 섹션 L634-639 수정**:

```diff
## 미해결 지식 항목 (unresolved_items)

{unresolved_items}

- 도구 결과에서 해당 개념을 해소할 수 있는 정보를 발견하면, 해당 항목의 key를 그대로 사용하여 knowledge_updates에 포함하세요.
+ 각 항목은 `(id) key — status` 형식으로 제시됩니다. 예: `(K1) measure:지점 수 — UNRESOLVED`.
+ 도구 결과에서 해당 개념을 해소할 수 있는 정보를 발견하면, 해당 항목의 **id만** knowledge_updates[*].id 로 사용하세요. 소괄호는 표시용이며 응답에는 포함하지 않습니다 (예: id="K1").
+ unresolved_items에 없는 id나 새로운 개념을 knowledge_updates에 넣지 마세요. 신규 개념은 insight 문장으로만 기록합니다.
```

**직렬화 예시 (renderer 출력 포맷)**:

```
(K1) measure:지점 수 — UNRESOLVED
     역할: SELECT 절 출력항목 확인
(K2) filter:연체일수=30 — CANDIDATE
     조건식 후보: OVDU_DY_CN >= 30
     후보 판단 사유: 컬럼 정의 확인 (출처: search_table_meta)
```

**[OUTPUT_CONTRACT] 스키마 L577-608 수정** — `knowledge_updates[*]`의 `"key"`, `"is_critical"` 필드 제거 + `"id"` required 추가:

```diff
  {
    "interpretations": [
      {
        "tool_name": "...",
        "tool_input": "...",
        "insight": "...",
        "knowledge_updates": [
          {
-           "key": "...",
+           "id": "K1",
            "value": "...",
            "confidence": 0.7,
            "new_status": "CANDIDATE | PROBABLE | CONFIRMED | CONFLICTED",
            "source": "...",
-           "evidence": "...",
-           "is_critical": true
+           "evidence": "..."
          }
        ],
        ...
      }
    ]
  }
```

**L615 "필드 설명" 수정**:

```diff
- - knowledge_updates: unresolved_items 해소에 기여하는 항목만. 관찰 도구 스텝도 포함 가능.
+ - knowledge_updates: unresolved_items 해소에 기여하는 항목만. 반드시 `id`(K1/K2 등)로 기존 unresolved_items 를 지정하며, 신규 개념을 임의로 만들지 않는다. `is_critical`/`key`는 seed 시 확정된 값이 보존되므로 응답에 포함하지 않는다.
```

**예시 JSON (L222, L230, L249, L312, L328, L391, L478, L521, L540, L583 전체 수정)**:

```diff
- "knowledge_updates": [
-   {"key": "measure:지점 수", "value": "COUNT(DISTINCT BLNG_BRCD)", "new_status": "CONFIRMED", "confidence": 0.9, "source": "search_table_meta", "evidence": "BLNG_BRCD 컬럼 확인"}
- ]
+ "knowledge_updates": [
+   {"id": "K1", "value": "COUNT(DISTINCT BLNG_BRCD)", "new_status": "CONFIRMED", "confidence": 0.9, "source": "search_table_meta", "evidence": "BLNG_BRCD 컬럼 확인"}
+ ]
```

**각 예시 상단 "미해소 지식 항목:" 블록도 `(id)` prefix 형식으로 통일**.

**`## 출력 스키마` 등 스키마 명세 섹션이 있다면** `knowledge_updates[*]`의 필드 목록에서 `key` 제거, `id` (required) 명시.

**신규 위반 사례 퓨샷 추가** (기존 "잘못된 출력" 섹션 주변 L140-170):

```markdown
### 위반 — 존재하지 않는 id 참조
- 입력 unresolved_items: (K1) measure:지점 수 — UNRESOLVED
- 잘못된 출력:
  ```json
  {"knowledge_updates": [{"id": "K2", "value": "...", "new_status": "CONFIRMED"}]}
  ```
- 올바른 처리: K2는 존재하지 않는다. 새 개념은 insight 문장에만 기록한다.

### 위반 — id 누락
- 잘못된 출력:
  ```json
  {"knowledge_updates": [{"key": "measure:지점 수", "value": "..."}]}
  ```
- 올바른 출력:
  ```json
  {"knowledge_updates": [{"id": "K1", "value": "..."}]}
  ```

### 위반 — id를 소괄호 포함으로 응답
- 잘못된 출력: `{"id": "(K1)", ...}`
- 올바른 출력: `{"id": "K1", ...}` — 소괄호는 프롬프트 표시용이며 JSON에는 포함하지 않는다.

### 위반 — business-implicit 조건을 KI로 등록
- 상황: "지점이 몇 개야?" 질의에서 도구 결과 "BR_DCD='02'가 지점 유형"을 발견
- 잘못된 출력:
  ```json
  {"knowledge_updates": [{"id": "K_NEW", "key": "filter:BR_DCD='02'", "new_status": "CONFIRMED"}]}
  ```
- 올바른 처리: 사용자 질의에 없던 파생 조건은 KI가 아니다. 해당 스텝의 insight 문장에 "지점 유형 코드 BR_DCD='02' 관찰"로 기록한다.
```

### 6.3 `recovery_agent_system.txt`

**L458-463**:

```diff
## 아직 확인되지 않은 정보 (unresolved_items)

- {unresolved_items}
+ 각 항목은 `(id) key — status` 형식입니다.
+
+ {unresolved_items}
+
+ 탐색 계획에서 이들을 참조할 때 id(예: K1)를 기준으로 사용합니다.
```

**L140, L159-170 위반 사례 블록 업데이트**:

```diff
- - unresolved_items에 없는 항목을 임의로 만들어 해결 대상으로 삼지 않는다
+ - unresolved_items에 없는 id/항목을 임의로 만들어 해결 대상으로 삼지 않는다
```

**응답 JSON에서 `missing_terms`가 key 문자열을 담던 부분이 있다면 id로 변경** (실제 스키마는 recovery_agent.py 확인 필요):

- recovery_agent의 출력 스키마가 knowledge item을 참조하는 구조라면 id 기준으로 변경
- 구현자가 추가 조사 필요

### 6.4 `sql_generator_system*.txt` (5개 variant)

**`## 지식 항목 (확정 / 추정)` 섹션**:

```diff
  ## 지식 항목 (확정 / 추정)

  {confirmed_knowledge}

- 지식 항목 끝의 "— 확정" / "— 추정"은 근거 강도 표시이다.
+ 각 항목은 `(id) key: value (source) — 확정|추정` 형식으로 제시된다.
+ "— 확정" / "— 추정"은 근거 강도 표시이다.
```

**`state.py:format_confirmed_knowledge_text` 출력 포맷 조정**:

```python
# BEFORE
f"- {ki.key}: {ki.value} ({ki.source}) — "

# AFTER
f"- ({ki.id}) {ki.key}: {ki.value} ({ki.source}) — "
```

### 6.5 `sql_validator_system.txt` L680

**`## 확인된 지식 항목`** 섹션이 format_confirmed_knowledge_text를 재사용하면 자동 반영. 별도 렌더 로직이 있으면 동일하게 `(id)` prefix 추가.

---

## 7. LLM 실수 대응 매트릭스

| # | 실수 유형 | 발생 예시 | 대응 | 구현 위치 |
|---|---|---|---|---|
| E1 | 존재하지 않는 id 참조 | `"id": "K99"` (KI가 3개뿐) | update 폐기 + warning log | `context_interpreter.py` INSERT 가드 |
| E2 | id 필드 누락 | `{"key":"measure:지점 수"}` | update 폐기 + warning log | 동일 |
| E3 | id 대신 semantic string | `"id": "measure:지점 수"` | 존재하지 않는 id와 동일 처리 | 동일 |
| E4 | 동일 id 중복 응답 | K1 updates 2회 | `_merge_updates_into_items`에서 순차 병합 (후자가 우선, evidence는 누적) | 병합 함수 |
| E5 | 없던 개념 임의 해소 주장 | 새 key + id:null | E2와 동일 폐기 | INSERT 가드 |
| E6 | 승격 규칙 점프 | UNRESOLVED→CONFIRMED 한 번에 (단일 근거) | `_should_promote`에서 단일 source CONFIRMED 상한 강제 (기존 로직 활용) | 병합 함수 |
| E7 | id 오탈자 (소문자) | `"id": "k1"` | `.upper().strip()` 정규화 후 매칭 | INSERT 가드 L4 |
| E8 | id에 소괄호 포함 | `"id": "(K1)"` | `.strip("()")` 정규화 후 매칭 | INSERT 가드 L4 보강 |

**가드 정규화 코드**:

```python
target_id = (ku.get("id") or "").upper().strip().strip("()").strip()
if not target_id.startswith("K") or not target_id[1:].isdigit():
    logger.warning("knowledge_updates: malformed id %r — dropped", ku.get("id"))
    continue
```

---

## 8. 테스트 계획

### 8.1 유닛 테스트

**신규 `tests/auto/unit/test_knowledge_item_id_management.py`**:

```python
async def test_insert_blocked_unknown_id():
    """unresolved_items에 없는 id 사용 시 update 폐기되는지 확인."""
    # Given: KI 2개 seeded (K1, K2)
    # When: LLM이 {"id": "K99", ...} 응답
    # Then: state.knowledge_items 크기 불변, K99 미반영, warning 로그

async def test_insert_blocked_missing_id():
    """id 누락 시 update 폐기."""

async def test_insert_blocked_semantic_key_as_id():
    """id에 semantic key가 들어오면 폐기."""

async def test_update_accepts_existing_id():
    """유효한 id로 UPDATE 시 value/status/confidence 반영."""

async def test_update_preserves_key_and_is_critical():
    """UPDATE가 기존 ki.key, ki.is_critical을 보존."""

async def test_update_normalizes_id_case():
    """'k1' → 'K1'로 정규화 후 매칭."""

async def test_update_normalizes_id_parentheses():
    """'(K1)' → 'K1'로 정규화."""

async def test_dedup_by_id_not_key():
    """동일 id 중복 응답은 병합, 표기 차이 key는 영향 없음."""

async def test_promotion_rules_single_source():
    """단일 source로 UNRESOLVED→CONFIRMED 한방 점프 차단."""

async def test_dead_end_references_id():
    """DeadEnd.related_knowledge_ids가 id로 기록되는지 확인."""
```

### 8.2 회귀 E2E

**대상 시나리오** (tests/reports/e2e_2026Q2):

- N-02 "지점이 몇 개야?" — 기대 `replan_count ≤ 1`
- C-02 "여신 정보 보여줘" — intent_mismatch WARN 해소 여부 재확인
- M-01 ~ M-04 — 기존 PASS 상태 유지 (회귀 없음)
- 복잡 질의 중 표본 5건 (기존 PASS 시나리오) — KI 갱신 정상 동작 확인

**실행 방법**:

```bash
python -m tests.manual.e2e.run_scenarios --suite 2026Q2 --id N-02
python -m tests.manual.e2e.run_scenarios --suite 2026Q2 --id C-02
# ...
```

**판정 기준**:

- FAIL 수가 변경 전보다 증가하지 않을 것
- N-02 replan_count 감소 확인
- 신규 warning 로그에 "INSERT blocked", "unknown id" 패턴 기록되는지 확인

### 8.3 로그 관찰 체크

실행 중 다음 로그를 수집하여 `docs/todo/20260420-knowledge-item-id-impl-log.md`에 보고:

- `KI INSERT blocked` 발생 건수와 context (어떤 질의에서 몇 번)
- `knowledge_updates: unknown id` 발생 건수
- 정상 UPDATE 횟수 / 전체 interpreter 호출 수

---

## 9. 배포 전략

### 단일 PR 권장

현재 **E2E 정확도 측정 진행 중**이라 정상 케이스도 불안정한 상태. 3단계 롤아웃(호환성 유지)은 오히려 회귀 감지 어렵게 만듦. 단일 PR에 모두 넣고 E2E로 검증.

### PR 범위

1. state 모델 필드 리네이밍
2. seed/interpreter/recovery/sql_generator 코드 일괄 수정
3. 프롬프트 8개 파일 수정
4. 유닛/E2E 테스트 추가·수정
5. 위 §8.3 로그 관찰 결과 요약 README 업데이트

### Postgres 영속화 이관 (과거 턴 스냅샷 대응)

**배경**: [process_summary_builder.py:176-178](src/services/process_summary_builder.py#L176-L178) 이 `knowledge_items`를 `model_dump(mode="json")` 후 `conversation_history.metadata.process_summary.context._knowledge_items` 경로로 Postgres JSONB에 저장한다. [turn_snapshot_store.py:637-668](src/services/turn_snapshot_store.py#L637-L668) 에서 `KnowledgeItem.model_validate(raw)`로 복원.

**영향**: KnowledgeItem은 `model_config` 미지정이라 Pydantic v2 기본 `extra='ignore'` 적용. 필드 리네이밍 후 과거 JSONB 의 `"knowledge_id"` 값은 **silent drop** 되고 신규 `id` 필드는 기본값 `""`로 복원. 파싱 자체는 실패하지 않으나 REGENERATE 렌더링에서 **과거 턴 K-id 정보 손실**.

**대응 선택지**:

| 옵션 | 내용 | 적용 조건 |
| --- | --- | --- |
| A. 마이그레이션 불필요 | 로컬 dev 환경 한정이므로 DB 초기화 또는 그대로 수용 (과거 K-id 유실 허용) | 운영·공유 환경 아님, 이전 턴 복원이 중요하지 않을 때 |
| B. SQL 마이그레이션 | 일회성 UPDATE 로 JSONB 필드명 치환 | `UPDATE conversation_history SET metadata = jsonb_set(...)` 스크립트 작성 |
| C. `Field(alias="knowledge_id")` | Pydantic alias 로 양쪽 수용 | **본 PR은 A를 기본 선택**. B/C 는 운영 반영 시 재검토 |

**본 PR 결정**: **옵션 A 채택** (§10 이미 명시 — snapshot 디렉토리/DB 초기화로 대응). 운영망 배포 시 B 스크립트를 별도 PR 로 준비하되 본 PR 범위 밖. 구현자는 dev DB 시드를 재실행하거나 `TRUNCATE conversation_history`로 초기화.

**검증**: 마이그레이션 없이 신규 턴 몇 건 실행 후 `SELECT metadata->'process_summary'->'context'->'_knowledge_items' FROM conversation_history` 에서 `"id"` 필드가 채워지는지 확인.

### 롤백 계획

문제 발생 시 PR revert 단일 action. 상태 파일 호환성 이슈는 없음 (런타임 state는 in-memory, snapshot store는 Pydantic 모델 변경 시 재설정 필요 — §10 참조).

### 프롬프트 버전

프롬프트 파일에 버전 주석이 있으면 bump:

```
# version: 2026.04.20-id-based
```

---

## 10. 하지 않을 것 (합의 범위 밖)

아래 항목은 본 구현에서 **건드리지 않는다**. 추후 별도 논의:

1. `discovered_facts` 구조 변경 (현행 `list[str]` 유지)
2. INSERT 리다이렉트 채널 신설 (거부된 INSERT는 warning log + 폐기)
3. entity / dimension slot의 KI seed 추가 (entity는 탐색 키워드 역할 유지)
4. `is_critical` 기본값 변경 (True 유지, seed 시점만 지정)
5. CandidateTable.entity_scope 필드 신설 (설계 리뷰 제안 있으나 본 PR 범위 아님)
6. readiness_gate 임계치 변경 (0.65 / 0.55 유지)
7. Pydantic 모델 snapshot 역호환 처리 (필드 리네이밍으로 기존 snapshot 로드 실패 가능. 로컬 dev 상태이므로 **snapshot 디렉토리 초기화로 대응**, 운영 호환성 고려 불필요)

---

## 부록 A. 전수 치환 요약 (grep 기준)

### A.1 `ki.knowledge_id` → `ki.id`

```
src/agents/state/state.py:110                        (필드 정의)
src/agents/nodes/reason/reasoning_preparer.py:73     (채번)
src/agents/nodes/reason/reasoning_preparer.py:132    (tracker)
src/agents/nodes/reason/context_interpreter.py:247   (tracker)
src/agents/nodes/reason/sql_generator.py:324         (tracker)
```

### A.2 `de.related_knowledge_keys` → `de.related_knowledge_ids`

```
src/agents/state/state.py:373
src/agents/nodes/reason/recovery_agent.py:416, 427, 524
```

### A.3 프롬프트 파일

```
resources/prompts/reason/context_interpreter_system.txt
resources/prompts/reason/recovery_agent_system.txt
resources/prompts/reason/sql_generator_system.txt
resources/prompts/reason/sql_generator_system_oracle.txt
resources/prompts/reason/sql_generator_system_postgres.txt
resources/prompts/reason/sql_generator_system_impala.txt
resources/prompts/reason/sql_generator_system_sybase_iq.txt
resources/prompts/reason/sql_validator_system.txt
```

### A.4 변경 없음 (key 기반 role 추출 유지)

```
src/agents/nodes/reason/context_interpreter.py:353, 1374, 1376, 1427, 1429
src/agents/nodes/reason/result_finalizer.py:101, 103
src/agents/nodes/reason/recovery_agent.py:911, 936
src/agents/nodes/reason/readiness_gate.py:267 (unresolved 로깅)
src/services/confidence_scorer.py:124, 166 (is_critical 필터)
```

---

## 부록 B. 구현자 확인 절차

1. 본 문서 전체 통독
2. `docs/agent-memory/nl-sql-developer/MEMORY.md` 기존 KI 관련 메모 확인 (충돌 없는지)
3. `.claude/rules/holistic-thinking.md` 6관점 점검 후 작업 개시
4. §5 체크리스트 순서대로 편집
5. §8 테스트 실행, 로그 수집
6. PR 생성 전 본 문서 §10 "하지 않을 것" 위반 없는지 자가 검토
7. PR 설명에 §8.3 로그 관찰 요약 첨부
