# 커넥터 시스템 정체성 기반 리팩토링 설계

작성일: 2026-04-11
작성자: 한철희
상태: 검토 완료(조건부 승인) → 보완 반영 (rev.3)
선행 논의: [20260411-schema-pushdown-filter-design.md](./20260411-schema-pushdown-filter-design.md)
검토 반영 이력:

- rev.2: api-integrator 서브에이전트 리뷰 Blocker 2 + Major 5 + Minor 5 반영
- rev.3: 구현 구체성 검증 결과 갭 9건 확정 (아래 "rev.3 확정 사항" 참조)

## 배경

### 관측된 복잡성
서버 기동 중 "FORCED 모드: 타겟 외 테이블 제외" 로그 대량 출력 이슈 조사 중, 라우팅 레이어에 식별자가 세 겹으로 중첩되어 있음을 발견:

| 레이어 | 값 예시 | 역할 |
|---|---|---|
| (A) 시스템 코드 | `ADW`/`BDP`/`CRP`/`DEV` | 업무 의미 |
| (B) 커넥터 키 | `sybase`/`impala`/`oracle`/`test_db` | 내부 라우팅 |
| (C) SQL dialect | `tsql`/`hive`/`oracle`/`postgres` | sqlglot·프롬프트 선택 |

(A)↔(B) 변환을 위한 `db_source_code_map`, `target_connector_key` 프로퍼티, `parse_db_source` 등의 부가 장치가 이 이중 명명 때문에 생김. 또한 `deployment_mode`(external/internal)라는 네 번째 축이 "ADW가 어느 물리 커넥터로 가는가"를 우회 표현하여 `get_query_db`, `_internal_connectors`, `_REQUIRED_CONNECTORS` 등 4곳에서 반복 분기.

### 식별된 중복/혼란 포인트
1. `parse_db_source` 로직이 [manager.py:302](../../src/connectors/manager.py#L302)와 [target_db_resolver.py:36](../../src/services/target_db_resolver.py#L36) 두 곳에 재구현
2. `TableMeta.db_source`(커넥터 키)와 `TableMeta.schema_name`(물리 스키마) 이중 필드 — 동일 시스템을 두 이름으로 참조
3. `_internal_connectors()`가 이름과 달리 external 전용 `test_db`를 섞어서 반환
4. `get_query_db` 우선순위에서 `db_source` 인자가 external 분기보다 앞 — 외부망에서 경고 로그 유발
5. `deployment_mode` 분기가 실질적으로 "ADW→test_db vs ADW→sybase" 매핑의 proxy
6. `DEV` 시스템 코드는 업무 시스템이 아니라 외부망 물리 백엔드 선택을 시스템 코드 레이어로 끌어올린 누수

### 근본 원인
시스템 코드(업무 의미)와 커넥터 키(물리 백엔드)를 **서로 다른 어휘**로 다루면서 양자를 매핑하는 코드가 여러 지점에 흩어졌기 때문. 또한 외부망 테스트 환경을 `DEV`라는 별도 시스템 코드로 모델링하여 "업무 시스템 레이어"에 테스트 인프라 개념이 섞여 들어감.

## 설계 목표

1. **식별자 단일화** — 시스템 코드 = 커넥터 이름. 중간 매핑 테이블 소멸
2. **테스트 인프라 누수 제거** — `DEV` 시스템 코드, `deployment_mode` 개념, 외부망 전용 프로퍼티·필드 모두 제거
3. **identity 기본, override 예외** — 폐쇄망은 설정 없이 동작, 외부망 테스트만 한 줄 override
4. **확장성 유지** — 향후 BDP/CRP 요구사항이 추가될 때 매핑 한 줄만 추가하면 즉시 활성
5. **DBMS 방언 축 분리 유지** — `DatabaseConnector.dialect`는 sqlglot/프롬프트 축으로 존속

## 최종 모델

### 커넥터 = 시스템 정체성 1:1

```
ADWConnector   → 업무 시스템 ADW (폐쇄망: Sybase IQ 드라이버)
BDPConnector   → 업무 시스템 BDP (폐쇄망: Impala 드라이버)
CRPConnector   → 업무 시스템 CRP (폐쇄망: Oracle 드라이버)
TESTConnector  → 외부망 테스트 전용 (PostgreSQL 드라이버)
```

**인프라 커넥터는 분리된 범주**로 항상 on:
- `MongoConnector` (테이블/코드/용어 메타)
- `QdrantConnector` (업무 매뉴얼 + SQL 이력)
- `PostgresConnector` (공통 메타 DB, 체크포인터, SQL 이력 영속화)

### 매핑 = identity 기본 + 선택적 override

```python
# settings (config.py)
system_db_overrides: dict[str, str] = {}
# 폐쇄망 ADW만 운영: {}                            (비워두면 identity)
# 외부망 테스트:    {"ADW": "TEST"}
# 폐쇄망 ADW+BDP:  {}                            (identity: ADW→ADW, BDP→BDP)
```

**해석 규칙**:
```python
def resolve_system_connector(system_code: str) -> str:
    return settings.system_db_overrides.get(system_code, system_code)
```

### 식별자가 단 하나로 수렴
- 테이블명 접두사 `TB_ADW_*` → 시스템 코드 `ADW` (슬라이스 한 줄)
- `TableMeta.db_source` = `"ADW"` (schema_name `"ADWOWN"`와 접두사 일치)
- `ReasoningState.target_db` = `"ADW"` (시스템 코드 그대로)
- `ConnectorManager.get_query_db(reason)` → `ADWConnector` 또는 override 적용 시 `TESTConnector`

중간에 sybase/impala/oracle/test_db 라는 제3의 어휘가 **등장하지 않음**.

## 상세 설계

### 1. 파일·클래스 rename

| 이전 | 이후 | 비고 |
|---|---|---|
| `src/connectors/impl/sybase_connector.py` | `src/connectors/impl/adw_connector.py` | `SybaseIQConnector` → `ADWConnector` |
| `src/connectors/impl/impala_connector.py` | `src/connectors/impl/bdp_connector.py` | `ImpalaConnector` → `BDPConnector` |
| `src/connectors/impl/oracle_connector.py` | `src/connectors/impl/crp_connector.py` | `OracleConnector` → `CRPConnector` |
| `src/connectors/impl/postgres_connector.TestDBConnector` | `src/connectors/impl/test_connector.TESTConnector` | **파일 분리 필수** (★m-3 승격) |
| `src/connectors/impl/postgres_connector.PostgresConnector` | 그대로 유지 | 공통 메타 DB 인프라 |

**각 클래스의 `dialect` 속성 유지**:
- `ADWConnector.dialect = "tsql"` (Sybase IQ)
- `BDPConnector.dialect = "hive"` (Impala)
- `CRPConnector.dialect = "oracle"`
- `TESTConnector.dialect = "postgres"`

`get_sql_generator_system(dialect)` 경로는 변경 없음 — 이 축은 건드리지 않음.

### 2. `src/config.py` — 설정 단순화

**제거** (rev.2: M-5, m-5 반영):

- `deployment_mode: str`
- `db_source_code_map: dict`
- `target_connector_key` 프로퍼티
- `target_db_code: str = "DEV"` 기본값 (기본을 `"ADW"`로 교체)
- `enabled_connectors: set` (infra는 상수화)
- `restrict_connectors_to_target: bool` (★m-5: 신규 모델에서는 `_db_connectors` 생성 자체가 필요한 것만 만들므로 무의미)
- `_validate_target_db_code` model_validator ([config.py:392-407](../../src/config.py#L392-L407)) — 재작성 (★B-2)

**추가**:
```python
# 업무 DB 시스템 override — 외부망 테스트 환경 전환용.
# 비어 있으면 identity 매핑 (ADW→ADW, BDP→BDP, CRP→CRP).
# 외부망 테스트 환경에서만 {"ADW": "TEST"} 로 설정.
system_db_overrides: dict[str, str] = {}

# push-down 필터링용 시스템 → 스키마명 매핑
target_db_schema_map: dict[str, str] = {
    "ADW": "ADWOWN",
    "BDP": "BDPOWN",
    "CRP": "CRPOWN",
}

# 강제 타깃 시스템 (미지정이면 SELECTED 테이블 기반 동적 결정)
target_db_code: str = "ADW"
```

**파생 프로퍼티**:

```python
@property
def target_schema(self) -> str:
    """target_db_code 에 매핑된 schema_name (push-down 필터용)."""
    return self.target_db_schema_map.get(self.target_db_code, "")

def resolve_system_connector(self, system_code: str) -> str:
    """시스템 코드 → 실제 커넥터 이름 (override 적용).

    외부망: {"ADW":"TEST"} 로 override → "TEST" 반환.
    폐쇄망: {} → identity, "ADW" 반환.
    """
    return self.system_db_overrides.get(system_code, system_code)
```

**재작성된 validator** (★B-2 해소):

```python
@model_validator(mode="after")
def _validate_target_db_code(self) -> "Settings":
    """target_db_code 가 알려진 시스템 코드인지 검증.

    rev.2: db_source_code_map 삭제에 따라 target_db_schema_map 의
    키 집합을 단일 진실원으로 사용한다. 입력은 대문자 정규화.
    """
    if self.target_db_code:
        normalized = self.target_db_code.strip().upper()
        known = set(self.target_db_schema_map.keys())
        if normalized not in known:
            raise ValueError(
                f"target_db_code='{self.target_db_code}' 는 "
                f"target_db_schema_map 키 집합 {sorted(known)} 에 없습니다."
            )
        self.target_db_code = normalized

    # system_db_overrides 값 검증: 알려진 커넥터명만 허용
    _KNOWN_CONNECTORS = {"ADW", "BDP", "CRP", "TEST"}
    for sys_code, conn_name in self.system_db_overrides.items():
        if conn_name not in _KNOWN_CONNECTORS:
            raise ValueError(
                f"system_db_overrides['{sys_code}']='{conn_name}' 는 "
                f"알려진 커넥터 {sorted(_KNOWN_CONNECTORS)} 에 없습니다."
            )
    return self
```

### 3. `src/connectors/manager.py` — 라우팅 단순화

**커넥터 인스턴스화** (인프라 + 업무):
```python
def __init__(self, use_dummy: bool = True) -> None:
    self._use_dummy = use_dummy
    self._connected = False

    # ── 인프라 커넥터 (항상 존재) ──
    self.mongo = MongoConnector(use_dummy=use_dummy)
    self.qdrant = QdrantConnector(use_dummy=use_dummy)
    self.postgres = PostgresConnector(use_dummy=use_dummy)

    # ── 업무 DB 커넥터 (매핑에 등장하는 것만 생성) ──
    self._db_connectors: dict[str, DatabaseConnector] = {}
    self._init_system_db_connectors(use_dummy)

def _init_system_db_connectors(self, use_dummy: bool) -> None:
    """target_db_schema_map 에 등록된 시스템을 순회하여
    override 적용 후 실제 커넥터만 lazy 생성.

    rev.2 (m-1, m-2): target_db_schema_map 은 '알려진 시스템 코드의
    단일 진실원' 역할을 겸한다. 시스템 추가는 이 dict + factory 두 곳에만
    등록하면 된다.
    """
    required_names: set[str] = set()
    for sys_code in settings.target_db_schema_map.keys():
        required_names.add(
            settings.resolve_system_connector(sys_code),
        )

    factory = {
        "ADW":  lambda: ADWConnector(use_dummy=use_dummy),
        "BDP":  lambda: BDPConnector(use_dummy=use_dummy),
        "CRP":  lambda: CRPConnector(use_dummy=use_dummy),
        "TEST": lambda: TESTConnector(use_dummy=use_dummy),
    }
    for name in required_names:
        if name in factory:
            self._db_connectors[name] = factory[name]()
```

**주의 (m-1)**: 현재 설계는 `target_db_schema_map` 에 등록된 **모든 시스템의 커넥터를 생성**한다. 예컨대 폐쇄망 .env 에서 `target_db_schema_map` 에 BDP/CRP 가 모두 있으면 실제 운영 타깃이 ADW 하나여도 BDP/CRP 커넥터가 생성·connect 시도된다. "사용하지 않는 커넥터는 아예 생성하지 않기"가 필요하면 `target_db_schema_map` 에서 주석 처리하면 된다 (factory 는 영향 없음). 이것이 `restrict_connectors_to_target` 을 제거한 근거이기도 함.

**라우팅**:
```python
def get_query_db(
    self,
    reason: ReasoningState | None = None,
    db_source: str = "",
) -> DatabaseConnector:
    """시스템 코드를 커넥터로 해석한다.

    우선순위:
      1. db_source 인자 직접 지정
      2. reason.target_db (readiness_gate 결정)
      3. 단일 매핑 자동 선택 (폴백)
    """
    system = db_source or (
        reason.target_db if reason and reason.target_db else ""
    )
    if system:
        real = settings.resolve_system_connector(system)
        conn = self._db_connectors.get(real)
        if conn is not None:
            return conn
        logger.warning(
            "알 수 없는 system code — 폴백",
            requested=system, resolved=real,
        )

    # 폴백: 매핑이 단일이면 자동 선택
    if len(self._db_connectors) == 1:
        return next(iter(self._db_connectors.values()))

    raise RuntimeError(
        "업무 DB 커넥터를 결정할 수 없습니다",
    )

@staticmethod
def parse_db_source(table_name: str) -> str:
    """테이블명 → 시스템 코드 (슬라이스 한 줄).

    rev.2 (m-2): target_db_schema_map 을 '알려진 시스템 코드 목록'으로
    겸용한다. 신규 시스템 추가 시 이 dict 에 엔트리를 넣으면 parse 도
    자동 인식.

    TB_ADW_CSC101M → "ADW"
    TB_BDP_LCT001L → "BDP"
    """
    parts = table_name.upper().split("_")
    if len(parts) >= 3 and parts[0] == "TB":
        code = parts[1]
        if code in settings.target_db_schema_map:
            return code
    return ""
```

**삭제**:

- `_deployment` 필드
- `_sybase_db`/`_impala_db`/`_oracle_db` 어트리뷰트
- `_internal_connectors()` 메서드 (→ `self._db_connectors` dict로 통합)
- `_lookup_connector()` (→ `get_query_db` 내 1줄로 흡수)
- `external` 특수 분기 전체
- `connect_all` 의 FORCED 스킵 블록 ([manager.py:159-175](../../src/connectors/manager.py#L159-L175)) (★M-1: `_db_connectors` 생성 자체가 필요한 것만 만드므로 "비대상 스킵" 개념이 불필요)

**`connect_all` / `disconnect_all` / `health_check_all` 수정** (★M-2):

```python
async def connect_all(self) -> None:
    if self._connected:
        return
    # 인프라
    for name, attr in [
        ("mongodb", "mongo"),
        ("qdrant", "qdrant"),
        ("postgres", "postgres"),
    ]:
        await getattr(self, attr).connect()
    # 업무 DB (생성된 것만)
    for conn in self._db_connectors.values():
        await conn.connect()
    if not self._use_dummy:
        await self._warmup_reranker()
    self._connected = True
    logger.info(
        "커넥터 초기화 완료",
        db_connectors=sorted(self._db_connectors.keys()),
    )

async def disconnect_all(self) -> None:
    for attr in ("mongo", "qdrant", "postgres"):
        await getattr(self, attr).disconnect()
    for conn in self._db_connectors.values():
        await conn.disconnect()
    self._connected = False

async def health_check_all(self) -> dict[str, bool]:
    """반환 키 규격 (M-4):
    인프라 = {"mongodb","qdrant","postgres"},
    업무 DB = self._db_connectors.keys() (예: {"ADW"}, {"TEST"}, {"ADW","BDP"}).
    """
    timeout = settings.health_check_timeout
    targets: list[tuple[str, BaseConnector]] = [
        ("mongodb", self.mongo),
        ("qdrant", self.qdrant),
        ("postgres", self.postgres),
    ]
    for name, conn in self._db_connectors.items():
        targets.append((name, conn))

    async def _safe(name: str, conn: BaseConnector) -> tuple[str, bool]:
        try:
            return name, await asyncio.wait_for(
                conn.health_check(), timeout=timeout,
            )
        except Exception:
            return name, False

    results = await asyncio.gather(*[_safe(n, c) for n, c in targets])
    return dict(results)
```

### 4. `src/services/target_db_resolver.py` — 중복 제거

**변경**:
```python
# _PRIORITY: 시스템 코드 기준 (복수 시스템 혼재 시 우선순위)
# 대용량 분석 → 기간계 → DW 순서
_PRIORITY: tuple[str, ...] = ("BDP", "CRP", "ADW")

def _table_db_source(ct: TableMeta) -> str:
    """TableMeta.db_source 를 그대로 반환.

    from_meta 가 이미 parse_db_source 로 태깅했으므로 추가 파싱 불필요.
    """
    return ct.db_source
```

**삭제**:
- `code_map` 파라미터 전체 (settings 참조 소멸)
- 테이블명 재파싱 로직 ([line 36-46](../../src/services/target_db_resolver.py#L36-L46))

**FORCED 분기 단순화**:
```python
if settings.target_db_code:
    target_system = settings.target_db_code  # 변환 없음
    chosen, dropped = [], []
    for ct in reason.explored_tables:
        if ct.selection_status != SelectionStatus.SELECTED:
            continue
        if ct.db_source == target_system:
            chosen.append(ct.table_name)
        else:
            dropped.append((ct.table_name, ct.db_source or "unknown"))
    return TargetDbDecision(
        status=TargetDbStatus.FORCED,
        target=target_system,
        chosen_tables=chosen,
        dropped_tables=dropped,
        decision_rationale=(
            f"운영 설정에 의해 '{target_system}' 시스템만 사용합니다."
        ),
    )
```

### 5. `src/agents/nodes/reason/context_retriever.py` — 어휘 정렬 (★Blocker 반영)

**대상**:

- [`_forced_target_db()`](../../src/agents/nodes/reason/context_retriever.py#L51-L59)
- [`_filter_by_forced_target()`](../../src/agents/nodes/reason/context_retriever.py#L62-L83)
- 호출 지점: [line 384](../../src/agents/nodes/reason/context_retriever.py#L384), [line 727](../../src/agents/nodes/reason/context_retriever.py#L727)
- [`_qualify_table_in_input`의 parse_db_source+get_query_db 호출](../../src/agents/nodes/reason/context_retriever.py#L164-L165)

**변경 방향**:
```python
def _forced_target_db() -> str:
    """FORCED 모드에서 허용되는 시스템 코드를 반환한다.

    rev.2: settings.target_connector_key(구 어휘, 커넥터 키) 대신
    settings.target_db_code(시스템 코드) 를 직접 반환한다.
    """
    if not settings.target_db_code:
        return ""
    return settings.target_db_code


def _filter_by_forced_target(
    tables: list[TableMeta],
) -> list[TableMeta]:
    """FORCED 모드에서 타겟 외 시스템 테이블을 제거한다.

    rev.2: TableMeta.db_source 가 이제 시스템 코드이므로 비교 로직 그대로.
    어휘 변경(sybase→ADW)만으로 자동 정합.
    """
    target = _forced_target_db()
    if not target:
        return tables
    kept, dropped = [], []
    for t in tables:
        if not t.db_source or t.db_source == target:
            kept.append(t)
        else:
            dropped.append(t)
    if dropped:
        logger.info(
            "FORCED 모드: 타겟 외 테이블 제외",
            target=target,
            dropped=[(t.table_name, t.db_source) for t in dropped],
        )
    return kept
```

**`_qualify_table_in_input`** (line 164-165): 변경 불필요. `parse_db_source`가 시스템 코드를 반환하고 `get_query_db(db_source=...)`도 시스템 코드를 받으므로 **어휘 변경만으로 자동 정합**. 본 문서에 "영향 받음" 으로만 기재.

**중요**: `_filter_by_forced_target` 함수 자체는 PR1에서 **유지**한다. 완전 삭제는 PR2(push-down 필터링)에서 수행. PR1에서는 내부 구현이 시스템 코드 어휘와 자동 정합되도록 하는 것이 목표.

### 6. `src/agents/state/state.py` — 의미 정렬

**변경**:

- `TableMeta.db_source` 의미: 커넥터 키 → **시스템 코드** (`"ADW"`/`"BDP"`/`"CRP"`)
- `from_meta` 내 `parse_db_source` 호출은 유지 (반환값만 시스템 코드로 바뀜)
- docstring 갱신

**자연 정렬**: `db_source="ADW"` + `schema_name="ADWOWN"` → 접두사 일치로 디버깅 시 가독성 확보.

### 7. `src/main.py` — 필수 커넥터 집합 (★M-4 반영)

```python
_REQUIRED_INFRA: set[str] = {"mongodb", "qdrant", "postgres"}

async def _check_required_connectors(manager: ConnectorManager) -> None:
    status = await manager.health_check_all()
    # 인프라는 항상 필수
    missing_infra = [n for n in _REQUIRED_INFRA if not status.get(n)]
    # 업무 DB는 매핑에 등장하는 것 전부 필수
    required_db = set(manager._db_connectors.keys())
    missing_db = [n for n in required_db if not status.get(n)]
    if missing_infra or missing_db:
        raise RuntimeError(
            f"필수 커넥터 연결 실패: "
            f"infra={missing_infra}, db={missing_db}",
        )
```

**health_check_all 반환 dict 키 규격 (M-4)**:

- 인프라 커넥터: `"mongodb"`, `"qdrant"`, `"postgres"` (변화 없음)
- 업무 DB 커넥터: `_db_connectors` dict 의 **키 그대로** 사용 → `"ADW"`, `"BDP"`, `"CRP"`, `"TEST"` 중 매핑에 등장하는 것
- 구 어휘(`"sybase"`, `"impala"`, `"oracle"`, `"test_db"`)는 반환 dict에서 사라짐
- `health_check_all` 내부에서 `self._db_connectors.items()` 를 순회하여 `(name, conn.health_check())` 튜플을 생성

### 8. push-down 필터 설계 (follow-up)

본 리팩토링 이후 [push-down 필터 설계](./20260411-schema-pushdown-filter-design.md)의 대부분이 자동 정돈됨:
- `target_connector_key` 프로퍼티 호출 불필요 — `target_db_code`가 곧 시스템 코드
- `target_db_schema_map[target_db_code]` 직접 참조
- `_filter_by_forced_target` 사후 필터 제거
- `mongo.search_table_meta(schema_names=[...])` push-down 주입

**권장**: 본 리팩토링(PR1) 완료 후 push-down 필터(PR2)를 별도 작은 PR로 진행.

## 설정 시나리오

### 폐쇄망 — ADW만 운영
```env
# system_db_overrides 미설정 (기본값 {})
TARGET_DB_CODE=ADW
```
→ identity: `ADW → ADWConnector(Sybase IQ)`. 매핑에 등장하지 않는 BDP/CRP 커넥터는 인스턴스화조차 안 함.

### 폐쇄망 — ADW + BDP 운영 (요구사항 추가 시)
```env
# system_db_overrides 미설정
TARGET_DB_CODE=ADW  # 또는 ""로 동적 결정
```
+ [config.py](../../src/config.py) 의 `target_db_schema_map` 에 `BDP` 이미 등록되어 있으므로 `BDPConnector`도 자동 생성.

### 외부망 테스트 — PostgreSQL 재사용
```env
SYSTEM_DB_OVERRIDES={"ADW":"TEST"}
TARGET_DB_CODE=ADW
```
→ `ADW` 요청이 `TESTConnector(PostgreSQL)`로 라우팅. 다른 설정 변경 없음.

### 폐쇄망 전환 PR
- `SYSTEM_DB_OVERRIDES` 환경변수 제거 (또는 `{}`)
- 코드 변경 0줄
- `.env` 1줄 삭제로 완료

## 영향 범위

### rename (파일·클래스)
- `src/connectors/impl/sybase_connector.py` → `adw_connector.py` (+ class rename)
- `src/connectors/impl/impala_connector.py` → `bdp_connector.py` (+ class rename)
- `src/connectors/impl/oracle_connector.py` → `crp_connector.py` (+ class rename)
- `src/connectors/impl/postgres_connector.py` 내 `TestDBConnector` → `test_connector.TESTConnector` (파일 분리)

### 수정 파일

- `src/config.py` — deployment_mode/db_source_code_map/target_connector_key/enabled_connectors/restrict_connectors_to_target 제거, `_validate_target_db_code` model_validator 재작성, system_db_overrides/target_db_schema_map/resolve_system_connector 추가
- `src/connectors/manager.py` — `__init__`/`connect_all`/`disconnect_all`/`health_check_all`/`get_query_db`/`parse_db_source` 재작성, `_internal_connectors`/`_lookup_connector`/`_deployment`/FORCED 스킵 로직 제거
- `src/services/target_db_resolver.py` — `_PRIORITY` 값 재정렬·`_table_db_source` 시그니처 축소·FORCED 분기 정리
- `src/agents/state/state.py` — `TableMeta.db_source` 의미 변경 (docstring만)
- `src/agents/nodes/reason/context_retriever.py` — `_forced_target_db()` 본문 변경, `_filter_by_forced_target` 로깅 필드 어휘 유지(변경 없음), `_qualify_table_in_input` 어휘 자동 정합(무변경)
- `src/main.py` — `_REQUIRED_CONNECTORS` → `_REQUIRED_INFRA` + 업무 DB 동적 확장
- `.env.example`, `.env` — `SYSTEM_DB_OVERRIDES` 설명 추가, `DEPLOYMENT_MODE`/`DB_SOURCE_CODE_MAP`/`ENABLED_CONNECTORS`/`RESTRICT_CONNECTORS_TO_TARGET` 제거, `TARGET_DB_CODE=DEV` → `TARGET_DB_CODE=ADW`

### 갱신 문서
- `docs/architecture/architecture.md`
- `docs/guides/migration-guide.md`
- `docs/guides/customization-targets.md`
- `.claude/rules/financial-domain.md` (데이터 소스 참조 우선순위 중 DB 식별자 어휘)

### 테스트 영향
- `tests/auto/unit/test_connector_manager.py:189-190` — `manager._deployment` 참조 제거
- `tests/auto/unit/test_connectors.py` — 커넥터 클래스명 반영
- 골든셋 트레이스 로그의 `db_source` 값이 `"sybase"` → `"ADW"`로 기록됨 (과거 로그는 그대로 남음, 의미 있는 비교만 주의)
- E2E 기동 테스트: `.env`에서 `SYSTEM_DB_OVERRIDES={"ADW":"TEST"}`로 외부망 동작 검증

## 이상 케이스 / 확인 사항

1. **같은 DBMS를 쓰는 복수 시스템** (forward-looking): 예컨대 미래에 ADW와 XDW가 둘 다 Sybase IQ를 사용한다면 `ADWConnector`와 `XDWConnector` 두 클래스가 Sybase 드라이버 초기화 코드를 중복 보유. 해결: DBMS별 공통 헬퍼 모듈(`_sybase_driver.py`)로 1줄 위임. **현재 요구사항에는 해당 없음** — 문서에 주석만 남김.

2. **프롬프트 방언 mismatch** (기존 한계, 변화 없음): 외부망 테스트는 `TESTConnector.dialect = "postgres"`이므로 postgres용 sql_generator 프롬프트를 사용. 실제 폐쇄망 ADW는 `tsql` 프롬프트. 이 차이는 리팩토링 이전에도 존재했고 본 PR에서 해결 범위 아님 — 별도 이슈로 분리.

3. **시스템 코드 enum화 여부**: 현재는 dict 키 + raw str. 장기적으로 `SystemCode(StrEnum)` 도입 고려 가능하나 본 PR 범위 밖.

4. **신규 시스템 추가 워크플로**: (a) `src/connectors/impl/<xxx>_connector.py` 작성, (b) `factory` dict 및 `target_db_schema_map`에 등록, (c) 골든셋·프롬프트 확인. 이 3단계로 축소됨.

5. **인프라 PostgreSQL ↔ 업무 TESTConnector 혼동 주의**: 동일 PostgreSQL 인스턴스지만 논리적으로 다른 용도. `PostgresConnector`(인프라: 메타 DB/체크포인터)와 `TESTConnector`(업무: 테스트 백엔드)는 **별도 클래스·별도 커넥션 풀** 유지. 파일 분리 필수 (★m-3).

6. **`resolve_system_connector` 테스트 편의성** (★m-4): 현재 설계는 `settings.resolve_system_connector(code)` 인스턴스 메서드. 테스트에서 override 교체는 `Settings(system_db_overrides={"ADW":"TEST"})` 로 새 인스턴스 생성하여 격리. 전역 singleton 몽키패치는 지양 — 테스트 헬퍼에서 `Settings()` 를 직접 생성하여 `ConnectorManager(settings=...)` 의존성 주입으로 테스트하는 것이 깔끔. 현재 매니저가 `settings` 를 전역 import 하고 있으므로 필요 시 주입 리팩토링을 PR1 에 포함하거나 테스트에서 `monkeypatch.setattr(settings, "system_db_overrides", ...)` 로 우회 가능.

7. **`target_db_code=""` 동적 경로에서의 override 적용 지점** (★Q3 주석): 향후 외부망에서 `target_db_code=""` 로 동적 결정 모드를 쓸 경우, `resolve_target_db()` 는 `TableMeta.db_source` (시스템 코드) 기반으로 `TargetDbDecision.target` 을 시스템 코드로 채운다. 이후 `get_query_db()` 내부의 `resolve_system_connector()` 호출이 override 를 적용하여 실제 커넥터로 라우팅. **override 적용 지점은 `get_query_db` 단 한 곳**이며 resolver 는 시스템 코드 어휘만 다룬다.

## 마이그레이션 순서 (PR1)

1. 본 설계 문서 리뷰 (design-review 서브에이전트)
2. 파일·클래스 rename (git mv + 참조 일괄 치환)
3. `config.py` 설정 정리 + 신규 필드
4. `manager.py` 재작성 (커넥터 인스턴스화 + get_query_db)
5. `target_db_resolver.py` 정리
6. `state.py` docstring·의미 갱신
7. `main.py` 필수 커넥터 로직 변경
8. `.env.example` 및 문서 갱신
9. 단위 테스트 수정/추가
10. 로컬 외부망 기동 → 질의 end-to-end 검증
11. 기존 push-down 설계 문서([20260411-schema-pushdown-filter-design.md](./20260411-schema-pushdown-filter-design.md))를 재작성하여 PR2로 분리

## 테스트 전략

- **단위**: `test_connector_manager.py`에서 `resolve_system_connector` identity/override 2케이스, `get_query_db` 라우팅 케이스(reason 기반/db_source 기반/단일 폴백/미해석 에러)
- **회귀**: 기존 단위 테스트 중 `_deployment`/`_internal_connectors` 참조 제거
- **E2E**: `SYSTEM_DB_OVERRIDES={"ADW":"TEST"}` 설정으로 기동 → 샘플 질의 성공 → 이후 이 설정 제거해도(identity) 기동이 `ADWConnector` 미구현/드라이버 없음으로 fail-fast 되는지 확인(의도된 동작)
- **추적**: `db_source` 로그 필드 값이 `"ADW"`로 바뀌었는지 확인 (프론트엔드 파이프라인 뷰어 영향 여부 점검)

## 확정된 결정 (rev.2 — 검토 답변 반영)

1. **파일 rename**: 파일까지 rename 확정. `git mv` + 참조 일괄 치환. `git log --follow` 로 히스토리 추적 유지.
2. **`enabled_connectors` 완전 제거**: 확정. infra 3종은 `_REQUIRED_INFRA` 상수로 고정, 업무 DB 는 `target_db_schema_map` + `system_db_overrides` 에서 파생.
3. **`target_db_code = "ADW"` FORCED 유지**: 확정. 동적 결정 경로는 향후 복수 시스템 환경에서만 의미가 있고, override 적용 지점은 `get_query_db` 한 곳으로 고정 (위 이상 케이스 7번 참조).
4. **PR1/PR2 분리**: 확정. 본 문서 = PR1. push-down 필터는 별도 PR.
5. **`target_db_code=DEV` vs `TARGET_DB_CODE=ADW`** (사용자 논의 추가): 업무 층(`ADW`)과 물리 층(`TEST`)을 `system_db_overrides` 로 분리. `target_db_code="TEST"` 는 리팩토링 전제를 파괴하므로 **금지**. 외부망 .env 는 `TARGET_DB_CODE=ADW` + `SYSTEM_DB_OVERRIDES={"ADW":"TEST"}` 조합을 사용한다.

## rev.3 확정 사항 (구현 구체성 보완)

구현 직전 구체성 검증에서 드러난 9건의 갭을 다음과 같이 확정한다.

1. **드라이버 settings 필드명 유지**: `sybase_*`, `impala_*`, `oracle_*`, `test_db_*` 필드명은 **드라이버/물리 축**이므로 그대로 유지. 클래스명만 시스템 정체성으로 rename (`ADWConnector`가 `settings.sybase_host`를 읽는 것은 정상). `.env` 체크리스트의 `ADW_HOST/PORT/...` 예시는 `SYBASE_HOST/PORT/...`로 정정해 구현한다.

2. **Neo4j 커넥터 유지 (주석)**: Mongo/Qdrant와 같은 범주의 컨텍스트용 인프라 커넥터로 `ConnectorManager.__init__` 에 인스턴스화 코드 유지하되 `connect_all`/`disconnect_all` 루프에서는 **주석 처리**. 향후 요구사항 확정 시 주석만 해제.

3. **Hive 커넥터 파일 유지 (주석)**: `src/connectors/impl/hive_connector.py` 는 삭제하지 않고 파일·클래스 그대로 둔다. 향후 BDP 2차/신규 업무 시스템으로 붙을 가능성이 있는 "대기 상태"의 업무 DB 커넥터 자원. `ConnectorManager` 의 `factory` dict 에도 참고용 주석으로 1줄 남긴다.

4. **(취소)** `system_prompts.py` 의 `sybase/impala/oracle` 참조는 dialect → 프롬프트 파일명 매핑이므로 변경 불필요. 갭 아님.

5. **테스트 수정**: 구현자 재량. 기존 `test_connector_manager.py` / `test_config.py` / `test_connectors.py` / `test_state_helpers.py` 의 어휘 정합 및 dead assertion 삭제·대체를 구현과 함께 처리.

6. **`_PRIORITY` 및 AMBIGUOUS 자동 선정 제거**: `target_db_resolver._PRIORITY` 상수와 AMBIGUOUS 자동 선정 로직을 **제거**. 현재 요구사항에서 `target_db_code=""` 동적 경로 자체가 실질 dead code 이며, 복수 시스템 혼재가 향후 요구사항으로 들어올 경우 자동 drop 대신 사용자 명확화가 안전하다. 복수 DB 혼재 시 `TargetDbDecision(status=AMBIGUOUS, target="", decision_rationale="복수 업무 시스템에 걸친 질의는 지원하지 않습니다. 단일 시스템으로 범위를 좁혀 주세요.")` 를 반환하여 호출부가 사용자 명확화 플로우로 라우팅. `state.py` L343 docstring의 `(impala>oracle>sybase)` 문구는 자연 소멸 (AMBIGUOUS 설명 자체를 "복수 시스템 혼재 시 명확화 요청"으로 재작성).

7. **docstring 어휘 정렬**: `state.py` L343 등 주석·docstring의 구 어휘는 위 6번 변경에 흡수. 별도 작업 항목으로 두지 않는다.

8. **`TESTConnector` 완전 전환**: 클래스명은 `TESTConnector` 로 확정. `TestDBConnector` 이름과 `__test__ = False` 마커는 **모두 제거** (pytest 의 `python_classes=Test*` 글롭은 대소문자 민감이라 `TESTConnector` 는 자동 수집 대상이 아님).

9. **`_CONNECTORS` 레지스트리 상수 삭제**: [manager.py:54-60](../../src/connectors/manager.py#L54-L60) 의 `_CONNECTORS: list[tuple[str, str]]` 상수를 **완전 삭제**. `enabled_connectors` 폐지로 "config 이름 ↔ attribute 이름" 이중 명명의 존재 이유가 사라짐. 인프라 커넥터는 `connect_all`/`disconnect_all`/`health_check_all` 내부에서 하드코딩 루프로 처리:

    ```python
    # connect_all 내부
    for attr in ("mongo", "qdrant", "postgres"):
        await getattr(self, attr).connect()
    # await self.neo4j.connect()  # 향후 활성화 시 주석 해제
    ```

    `_REQUIRED_INFRA` 상수 (main.py) 와 정합: `{"mongodb", "qdrant", "postgres"}` (neo4j 는 활성화 시 추가).

---

## 폐쇄망 이전 체크리스트 (PR1 완료 후)

PR1 을 머지한 뒤 **폐쇄망으로 이전할 때 수정해야 하는 지점**을 모아둔다. 코드 변경은 원칙적으로 없고, **설정/환경 변수만** 건드린다.

### 1. `.env` (필수 — 실질적으로 여기만 바꿈)

```diff
# ── 업무 DB 라우팅 ──
- SYSTEM_DB_OVERRIDES={"ADW":"TEST"}   # 외부망: TEST 커넥터로 우회
+ # SYSTEM_DB_OVERRIDES 제거 또는 {} — 폐쇄망은 identity 기본

TARGET_DB_CODE=ADW                      # 동일 (변경 없음)

# ── 인프라 DB ──
# MongoDB / Qdrant / PostgreSQL(공통) 접속 정보를 폐쇄망 주소로 교체
MONGO_HOST=<폐쇄망 MongoDB 주소>
QDRANT_HOST=<폐쇄망 Qdrant 주소>
POSTGRES_DB_HOST=<폐쇄망 공통 메타 DB 주소>

# ── 업무 DB (Sybase IQ) ──
# ADWConnector 가 읽는 접속 정보 신규 기재
ADW_HOST=<Sybase IQ 주소>
ADW_PORT=<...>
ADW_USER=<LDAP 계정>
ADW_PASSWORD=<...>
ADW_DATABASE=<...>

# ── 외부망 전용 설정 삭제 ──
- TEST_DB_HOST=...
- TEST_DB_PORT=...
- TEST_DB_NAME=...
- TEST_DB_USER=...
- TEST_DB_PASSWORD=...

# ── LLM 폐쇄망 엔드포인트 ──
LLM_PROVIDER=openai_compatible
OPENAI_BASE_URL=<폐쇄망 LLM 게이트웨이>
LLM_MODEL=<Solar Pro 2 70B 또는 후속 모델>

# ── 외부망 전용 트래킹 비활성화 ──
LANGSMITH_ENABLED=false
```

### 2. `src/config.py` (선택 — 복수 시스템 요구사항 추가 시에만)

현재 `target_db_schema_map` 에는 `ADW`/`BDP`/`CRP` 가 모두 등록되어 있으므로 **ADW 단독 운영이면 수정 불필요**. BDP·CRP 운영이 시작되는 시점에는 어차피 별도 작업이므로 본 체크리스트 범위 밖.

### 3. 시딩 (폐쇄망 타겟 DB)

- 폐쇄망 Sybase IQ에는 이미 `TB_ADW_*` 테이블이 `ADWOWN` 스키마로 존재 → 시딩 불필요.
- 인프라 DB (MongoDB 메타 / Qdrant 이력·매뉴얼 / PostgreSQL 공통 메타) 는 폐쇄망 환경에 맞게 별도 시딩/이관 필요 (본 리팩토링과 무관, 기존 절차 유지).

### 4. 실행 시 자동으로 일어나는 일 (확인만)

- `settings.system_db_overrides == {}` → `resolve_system_connector("ADW")` 이 `"ADW"` 를 반환 (identity)
- `ConnectorManager._init_system_db_connectors` 가 `target_db_schema_map` 의 `ADW` 를 보고 `ADWConnector(use_dummy=False)` 를 생성
- `connect_all` 이 `ADWConnector.connect()` 호출 → Sybase IQ 접속
- 질의 처리 중 `reason.target_db = "ADW"` → `get_query_db()` → `ADWConnector` 반환
- `sql_generator` 가 `ADWConnector.dialect = "tsql"` 을 읽어 Sybase IQ 전용 프롬프트 선택
- `TableMeta.db_source = "ADW"`, `schema_name = "ADWOWN"` — 외부망과 동일 어휘

**즉 코드·로직 변경 0줄, `.env` 한 묶음 교체로 전환 완료**.

### 5. 검증 스크립트

- `python -m src.main` 기동 → 로그에 `커넥터 초기화 완료 db_connectors=['ADW']` 확인
- 샘플 질의 실행 → `reason.target_db="ADW"` + `dialect="tsql"` 로 SQL 생성되는지 트레이스 확인
- health_check 엔드포인트 → `{"mongodb": true, "qdrant": true, "postgres": true, "ADW": true}`

### 6. 주의 / 예외 사항

- **프롬프트 방언 전환 검증**: 외부망 개발 중에는 postgres 프롬프트로 SQL을 생성해 왔으므로, 폐쇄망 첫 전환 시 tsql(Sybase IQ) 프롬프트의 실제 품질을 **별도 골든셋으로 재검증** 필요. 이건 본 리팩토링의 책임이 아니고 기존 한계 그대로.
- **복수 시스템 추가 시**: `target_db_schema_map` 에 BDP/CRP 가 이미 있으면 자동으로 해당 커넥터 생성·connect 시도 → 폐쇄망에서 BDP·CRP 접속 정보가 `.env` 에 없으면 기동 실패. 단독 운영 기간에는 `target_db_schema_map` 에서 BDP/CRP 를 주석 처리해두거나, `.env` 에 접속 정보를 임시값으로 채워도 health_check 에서 걸러지도록 설계할지 결정 필요 (별도 이슈).
- **골든셋 트레이스 비교**: 외부망/폐쇄망 모두 `db_source="ADW"` 로 기록되므로 동일 축 비교 가능. 물리 백엔드 차이는 dialect 값으로만 구분.
