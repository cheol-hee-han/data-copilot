# 지식 저장소 추가·변경·삭제 개발자 매뉴얼

**작성일:** 2026-03-19 (2026-04 ES/LangSmith 제거 반영)
**대상 독자:** Data Copilot 개발·운영 담당자
**목적:** 도메인 지식·보조 정보를 제공하는 저장소가 추가/변경/삭제될 때의 코드 수정 가이드

> **⚠️ 2026-04 구조 변경 반영 필요**
> - ElasticSearch는 **제거**되었다. 본 문서의 ES 관련 기술 상세(커넥터 코드, `ES_*` 환경변수,
>   `elasticsearch_connector.py`, `resources/connectors/elasticsearch/`, `devtools/docker/elasticsearch/`,
>   `seed_elasticsearch.py`)는 **역사적 참조**로만 유효하다.
> - 테이블/컬럼 메타·코드 메타·용어사전은 **MongoDB**(`mongo_connector.py`), 과거 SQL 이력은
>   **Qdrant**(`sql_history` 컬렉션, 하이브리드 + Reranker)로 이전되었다.
> - LangSmith 연동은 제거되었고 관측성은 `src/utils/tracker/`가 담당한다.
> - "ES" 관련 시나리오(§5-A, §5-C 등)와 `ElasticSearchConnector`/`self.es` 코드 예시는
>   현재 구현과 일치하지 않으므로 MongoDB/Qdrant 기준으로 읽어야 한다.

---

## 목차

1. [현재 아키텍처 이해](#1-현재-아키텍처-이해)
2. [저장소 추가 (신규 데이터소스 연동)](#2-저장소-추가-신규-데이터소스-연동)
3. [저장소 변경 (기존 데이터소스 수정)](#3-저장소-변경-기존-데이터소스-수정)
4. [저장소 삭제 (데이터소스 제거)](#4-저장소-삭제-데이터소스-제거)
5. [시나리오별 예제](#5-시나리오별-예제)

---

## 1. 현재 아키텍처 이해

### 1-1. 데이터 흐름 전체 구조

```text
                            ┌──────────────────────────────────────┐
                            │      search_context_assembler.py              │
                            │      (병렬 수집 오케스트레이터)       │
                            │                                      │
사용자 질의 ──→ 파이프라인 ──→│  asyncio.gather(                     │
                            │    _fetch_table_metas()    ← ES      │
                            │    _fetch_report_sqls()    ← ES      │
                            │    _fetch_past_sqls()      ← 이력DB  │
                            │    _fetch_manual_refs()     ← Qdrant │
                            │    _fetch_code_meta()       ← ES     │
                            │  )                                   │
                            │         │                            │
                            │         ▼                            │
                            │  ContextInfo (통합 결과)              │
                            └────────────┬─────────────────────────┘
                                         │
                                         ▼
                            ┌─────────────────────────┐
                            │  sql_generator.py        │
                            │  (LLM 프롬프트 조립)     │
                            │                          │
                            │  _build_table_info()     │ ← ContextInfo.table_metas
                            │  _build_report_sqls()    │ ← ContextInfo.report_sqls
                            │  _build_past_sqls()      │ ← ContextInfo.past_sqls
                            │  _build_manual_refs()    │ ← ContextInfo.manual_references
                            │  _build_domain_terms()   │ ← ContextInfo.domain_terms
                            └─────────────────────────┘
```

### 1-2. 수정 대상 파일 레이어 맵

```text
저장소 추가·변경·삭제 시 수정이 필요한 파일을 레이어별로 정리한다.
각 레이어의 역할과 의존 관계를 이해해야 누락 없이 수정할 수 있다.

┌─────────────────────────────────────────────────────────────────────┐
│ Layer 1: 설정 (config)                                              │
│                                                                     │
│  src/config.py         — 접속 정보 (호스트, 포트, 인증)             │
│  .env / .env.example   — 환경변수 값                                │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 2: 커넥터 (connector)                                         │
│                                                                     │
│  src/connectors/base.py              — 추상 인터페이스              │
│  src/connectors/<new>_connector.py   — 신규 커넥터 구현             │
│  src/connectors/manager.py           — 커넥터 등록·생명주기 관리    │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 2.5: 검색 쿼리 전략 (query strategy) ← 2026-03-20 추가       │
│                                                                     │
│  src/services/search_query_builder.py     — 소스별 최적화 쿼리 생성      │
│  src/services/domain/finance_terms.py  — 도메인 용어 사전 (150+개)    │
│  신규 소스 추가 시 search_query_builder에도 소스별 쿼리 생성 로직을       │
│  추가해야 검색 품질이 유지된다.                                     │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 3: 컨텍스트 수집 (knowledge)                                  │
│                                                                     │
│  src/services/search_context_assembler.py    — 병렬 수집 + ContextInfo 조립 │
│  ※ search_query_builder가 생성한 소스별 쿼리를 각 _fetch_xxx()에 전달    │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 4: 상태 모델 (state)                                          │
│                                                                     │
│  src/agents/state/state.py                 — ContextInfo 필드 정의        │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 5: SQL 생성 프롬프트 (prompts + nodes)                        │
│                                                                     │
│  src/agents/nodes/sql_generator.py          — 프롬프트에 컨텍스트 주입     │
│  src/agents/nodes/prompts/system_prompts.py — SQL_GENERATION_RULES 템플릿  │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 6: 인프라 (docker)                                            │
│                                                                     │
│  devtools/docker/elasticsearch/Dockerfile — nori 플러그인 포함 ES 이미지 │
│  devtools/docker/docker-compose.dev.yml  — build + image 설정           │
│  devtools/scripts/seed_elasticsearch.py  — nori analyzer 적용 인덱스    │
│  devtools/scripts/seed_qdrant.py         — fastembed 임베딩 시딩         │
│  ※ Qdrant 임베딩 모델: paraphrase-multilingual-MiniLM-L12-v2      │
│  ※ 시딩과 조회에서 반드시 동일 모델 사용 (불일치 시 유사도 엉망)   │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 7: 테스트                                                     │
│                                                                     │
│  tests/unit/test_connectors.py       — 커넥터 단위 테스트           │
│  tests/integration/test_pipeline_e2e.py — E2E 파이프라인 테스트     │
│  tests/test_search_query_builder.py        — 쿼리 전략 단위 테스트        │
│  tests/test_search_query_builder.py   — Docker 대상 Live 테스트      │
│  tests/test_golden_set_context_quality.py — 골든셋 90건 E2E         │
│  tests/test_qdrant_vector_search.py  — 벡터 검색 품질 테스트        │
├─────────────────────────────────────────────────────────────────────┤
│ Layer 8: 문서                                                       │
│                                                                     │
│  docs/architecture/architecture.md                — 아키텍처 설계문서            │
│  .env.example                        — 환경변수 예시                │
└─────────────────────────────────────────────────────────────────────┘
```

### 1-3. 현재 등록된 저장소 목록

| 저장소 | 커넥터 클래스 | 역할 | 컨텍스트 필드 |
|--------|---------------|------|---------------|
| ElasticSearch (테이블 메타) | `ElasticSearchConnector` | 테이블/컬럼 스키마 검색 | `ContextInfo.table_metas` |
| ElasticSearch (보고서 SQL) | 같은 커넥터 | 기존 보고서 SQL 참조 | `ContextInfo.report_sqls` |
| ElasticSearch (코드 메타) | 같은 커넥터 | 코드값 매핑 조회 | `ContextInfo.domain_terms` |
| PostgreSQL (이력 DB) | `HistoryDBConnector` | 과거 SQL 실행 이력 | `ContextInfo.past_sqls` |
| Qdrant | `QdrantConnector` | 업무 매뉴얼 벡터 검색 | `ContextInfo.manual_references` |

---

## 2. 저장소 추가 (신규 데이터소스 연동)

### 개요

```text
신규 지식 저장소를 추가할 때는 아래 7단계를 순서대로 수행한다.
각 단계가 빠지면 컴파일 오류이거나, 런타임에서 신규 소스가 무시된다.

  Step 1 → config.py         : 접속 정보 추가
  Step 2 → base.py (선택)     : 새 인터페이스 필요 시 추가
  Step 3 → 커넥터 파일        : 커넥터 클래스 구현
  Step 4 → manager.py        : 커넥터 등록
  Step 5 → state.py          : ContextInfo 필드 추가
  Step 6 → search_context_assembler.py: 수집 함수 + gather 등록
  Step 7 → sql_generator.py  : 프롬프트 주입 함수 추가
           system_prompts.py : 프롬프트 템플릿에 플레이스홀더 추가

  + 테스트 작성
  + 문서 업데이트
```

---

### Step 1. 접속 정보 추가 — `src/config.py`, `.env.example`

```python
# ── src/config.py ──
# Settings 클래스에 신규 저장소 접속 정보를 추가한다.
# 환경변수명은 대문자 스네이크 케이스, 프리픽스로 저장소 종류를 붙인다.

class Settings(BaseSettings):
    # ... 기존 설정 ...

    # ── 신규 저장소 예시: 프로그램 소스 코드 DB ──
    # 프로그램 소스 코드에서 SQL 사용 패턴을 참조하기 위한 저장소.
    program_db_host: str = "localhost"
    program_db_port: int = 5432
    program_db_name: str = "program_db"
    program_db_user: str = "program_ro"
    program_db_password: str = ""
```

```dotenv
# ── .env.example ──
# 신규 저장소의 접속 정보 플레이스홀더를 추가한다.
# 실 환경에서는 .env 파일에 실 값을 채운다.

# 프로그램 소스 코드 DB
PROGRAM_DB_HOST=localhost
PROGRAM_DB_PORT=5432
PROGRAM_DB_NAME=program_db
PROGRAM_DB_USER=program_ro
PROGRAM_DB_PASSWORD=your-password
```

---

### Step 2. 베이스 인터페이스 검토 — `src/connectors/base.py`

```text
현재 제공되는 추상 클래스 3종 중 적합한 것을 선택한다.
새로운 유형이 필요한 경우에만 추가한다.

  BaseConnector    — 최소 인터페이스 (connect, disconnect, health_check)
  SearchConnector  — 검색 기능 (search 메서드 추가)
  DatabaseConnector — DB 쿼리 (execute_query 메서드 추가)
```

```python
# ── src/connectors/base.py ──
# 예: 파일 시스템 기반 저장소라면 새 인터페이스가 필요할 수 있다.
# 기존 인터페이스로 충분하면 이 단계는 건너뛴다.

class FileConnector(BaseConnector):
    """파일 시스템 기반 커넥터 인터페이스."""

    @abstractmethod
    async def read_file(self, path: str) -> str:
        """파일 내용을 읽는다."""

    @abstractmethod
    async def search_files(self, query: str) -> list[dict[str, Any]]:
        """파일을 검색한다."""
```

---

### Step 3. 커넥터 구현 — `src/connectors/<new>_connector.py`

```python
# ── src/connectors/program_connector.py ──
# 신규 커넥터 파일을 생성한다.
# 반드시 기존 커넥터와 동일한 패턴을 따른다:
#   1) Dummy 데이터 상수 정의
#   2) 클래스에 use_dummy 파라미터
#   3) 각 메서드에서 self._use_dummy 분기
#   4) 실패 시 빈 값 반환 (다른 소스에 영향 없게)

"""프로그램 소스 코드 커넥터.

프로그램 소스 코드에서 SQL 사용 패턴을 검색한다.
Dummy 모드에서는 내장된 샘플 데이터를 반환한다.
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.base import SearchConnector
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ── Dummy 데이터 ──
# 개발·테스트용 더미 데이터.
# 실 환경에서는 use_dummy=False로 전환하여 실 DB를 조회한다.
DUMMY_PROGRAM_SQLS = [
    {
        "program_id": "PGM001",
        "program_name": "월간 고객 현황 보고서",
        "sql_snippet": "SELECT ... FROM TB_CUST_INFO WHERE ...",
        "description": "고객 유형별 월간 집계",
    },
    # ... 추가 더미 데이터 ...
]


class ProgramConnector(SearchConnector):
    """프로그램 소스 코드 커넥터 (Dummy 모드 지원)."""

    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._client: Any = None

    async def connect(self) -> None:
        """연결 초기화."""
        if self._use_dummy:
            logger.info("프로그램 DB Dummy 모드로 초기화")
            return

        # 실 연결 구현
        # from sqlalchemy.ext.asyncio import create_async_engine
        # url = f"postgresql+asyncpg://..."
        # self._client = create_async_engine(url)
        logger.info("프로그램 DB 연결 완료")

    async def disconnect(self) -> None:
        """연결 종료."""
        if self._client:
            await self._client.dispose()

    async def health_check(self) -> bool:
        """연결 상태 확인."""
        if self._use_dummy:
            return True
        try:
            # 실 연결 확인 로직
            return True
        except Exception:
            return False

    async def search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """프로그램 소스에서 SQL 사용 패턴을 검색한다."""
        return await self.search_program_sql(query)

    async def search_program_sql(self, query: str) -> list[dict[str, Any]]:
        """프로그램 소스 코드에서 관련 SQL을 검색한다."""
        if self._use_dummy:
            query_lower = query.lower()
            return [
                p for p in DUMMY_PROGRAM_SQLS
                if any(
                    word in f"{p['program_name']} {p['description']}".lower()
                    for word in query_lower.split()
                )
            ] or DUMMY_PROGRAM_SQLS[:3]

        # 실 DB 검색 구현
        return []
```

**주의사항:**
- 클래스명은 `XxxConnector` 패턴을 따른다
- `use_dummy` 파라미터는 반드시 포함한다 (개발/운영 모드 전환)
- Dummy 데이터는 모듈 상단 상수로 정의한다
- 실 연결 실패 시 `health_check()`가 `False`를 반환하도록 한다

---

### Step 4. 커넥터 매니저 등록 — `src/connectors/manager.py`

```python
# ── src/connectors/manager.py ──
# ConnectorManager에 신규 커넥터를 추가한다.
# 3곳을 수정해야 한다: __init__, connect_all, disconnect_all, health_check_all

from src.connectors.program_connector import ProgramConnector  # 추가

class ConnectorManager:
    def __init__(self, use_dummy: bool = True) -> None:
        self._use_dummy = use_dummy
        self._connected = False
        self.es = ElasticSearchConnector(use_dummy=use_dummy)
        self.info_db = InfoDBConnector(use_dummy=use_dummy)
        self.history_db = HistoryDBConnector(use_dummy=use_dummy)
        self.qdrant = QdrantConnector(use_dummy=use_dummy)
        self.program = ProgramConnector(use_dummy=use_dummy)  # 추가

    async def connect_all(self) -> None:
        if self._connected:
            return
        logger.info("전체 커넥터 초기화 시작")
        await self.es.connect()
        await self.info_db.connect()
        await self.history_db.connect()
        await self.qdrant.connect()
        await self.program.connect()  # 추가
        self._connected = True
        logger.info("전체 커넥터 초기화 완료")

    async def disconnect_all(self) -> None:
        await self.es.disconnect()
        await self.info_db.disconnect()
        await self.history_db.disconnect()
        await self.qdrant.disconnect()
        await self.program.disconnect()  # 추가
        self._connected = False

    async def health_check_all(self) -> dict[str, bool]:
        return {
            "elasticsearch": await self.es.health_check(),
            "info_db": await self.info_db.health_check(),
            "history_db": await self.history_db.health_check(),
            "qdrant": await self.qdrant.health_check(),
            "program": await self.program.health_check(),  # 추가
        }
```

**체크포인트:** `health_check_all`에 추가하지 않으면 `/health` API에서 신규 저장소 상태가 누락된다.

---

### Step 5. 상태 모델 필드 추가 — `src/agents/state/state.py`

```python
# ── src/agents/state/state.py ──
# ContextInfo에 신규 소스의 결과를 담을 필드를 추가한다.
# 필드명은 소스의 역할을 명확히 드러내는 이름으로 짓는다.

class ContextInfo(BaseModel):
    """컨텍스트 수집 결과."""

    table_metas: list[TableMeta] = Field(default_factory=list)
    past_sqls: list[str] = Field(default_factory=list)
    report_sqls: list[str] = Field(default_factory=list)
    manual_references: list[str] = Field(default_factory=list)
    domain_terms: dict[str, str] = Field(default_factory=dict)
    table_disambiguation_guide: str = ""

    # ── 신규 필드 ──
    # 프로그램 소스에서 참조된 SQL 패턴 목록.
    # SQL 생성 시 기존 프로그램에서 사용된 쿼리 패턴을 참고한다.
    program_sqls: list[str] = Field(default_factory=list)  # 추가
```

**주의:** `Field(default_factory=list)` 또는 `= ""`로 기본값을 반드시 지정한다. 기존 코드에서 이 필드 없이 생성된 `ContextInfo` 인스턴스가 오류 없이 동작해야 하기 때문이다.

---

### Step 6. 컨텍스트 수집 서비스 — `src/services/search_context_assembler.py`

```python
# ── src/services/search_context_assembler.py ──
# 3곳을 수정한다:
#   (a) 수집 함수 추가
#   (b) asyncio.gather에 등록
#   (c) ContextInfo 조립 시 필드 할당

# ── (a) 수집 함수 추가 ──
# 기존 _fetch_xxx 함수와 동일한 패턴을 따른다.
# 실패 시 빈 목록을 반환하여 다른 소스에 영향을 주지 않는다.
async def _fetch_program_sqls(query: str) -> list[str]:
    """프로그램 소스에서 관련 SQL 패턴을 검색한다.

    실패 시 빈 목록을 반환하여 다른 소스 수집에 영향을 주지 않는다.
    """
    try:
        manager = get_connector_manager()
        results = await manager.program.search_program_sql(query)
        return [r.get("sql_snippet", "") for r in results if r.get("sql_snippet")]
    except Exception as e:
        logger.warning("프로그램 SQL 검색 실패, 빈 목록으로 폴백", error=str(e))
        return []


# ── (b) asyncio.gather에 등록 ──
# collect_context() 함수 내 asyncio.gather 호출에 추가한다.
# 기존 소스와 병렬로 수집되므로 전체 수집 시간에 거의 영향이 없다.
async def collect_context(query: str) -> ContextInfo:
    logger.info("컨텍스트 병렬 수집 시작", query=query[:80])

    (
        table_metas,
        report_sqls,
        past_sqls,
        manual_refs,
        domain_terms,
        program_sqls,     # 추가
    ) = await asyncio.gather(
        _fetch_table_metas(query),
        _fetch_report_sqls(query),
        _fetch_past_sqls(query),
        _fetch_manual_refs(query),
        _fetch_code_meta(),
        _fetch_program_sqls(query),  # 추가
    )

    # ... 기존 보강 로직 ...

    # ── (c) ContextInfo 조립 시 필드 할당 ──
    context = ContextInfo(
        table_metas=table_metas,
        past_sqls=past_sqls,
        report_sqls=report_sqls,
        manual_references=manual_refs,
        domain_terms=domain_terms,
        table_disambiguation_guide=disambiguation_guide,
        program_sqls=program_sqls,  # 추가
    )

    return context
```

**핵심 원칙:**
- 각 수집 함수는 `try/except`로 감싸서 **개별 실패가 전체를 막지 않도록** 한다
- `asyncio.gather`에 추가하면 자동으로 병렬 수집된다
- 수집 함수의 반환 타입은 `ContextInfo` 필드 타입과 일치해야 한다

---

### Step 7. SQL 생성 프롬프트 주입 — `sql_generator.py` + `system_prompts.py`

```python
# ── src/agents/nodes/sql_generator.py ──
# (a) 프롬프트 빌더 함수 추가
# (b) system_prompt 조립 시 호출

# ── (a) 빌더 함수 추가 ──
# 기존 _build_xxx 함수와 동일한 패턴.
# 데이터가 없으면 "(없음)" 텍스트를 반환하여 프롬프트가 깨지지 않게 한다.
def _build_program_sqls(state: PipelineState) -> str:
    """프로그램 참조 SQL을 프롬프트용 문자열로 변환한다."""
    if not state.context.program_sqls:
        return "(참고할 프로그램 SQL 없음)"
    return "\n".join(f"- {sql}" for sql in state.context.program_sqls[:5])
```

```python
# ── src/agents/nodes/prompts/system_prompts.py ──
# SQL_GENERATION_RULES 템플릿에 신규 소스용 플레이스홀더를 추가한다.
# LLM이 이 정보를 참고하여 SQL을 생성한다.

SQL_GENERATION_RULES = """\
...기존 내용...

[참고 보고서 SQL]
{report_sqls}

[참고 과거 SQL 이력]
{past_sqls}

[참고 프로그램 SQL]
{program_sqls}

[업무 매뉴얼 참고]
{manual_refs}

...이하 기존 내용...
"""
```

```python
# ── src/agents/nodes/sql_generator.py ──
# (b) system_prompt 조립 시 호출
# SQL_GENERATION_RULES.format() 호출에 신규 인자를 추가한다.

system_prompt = SQL_GENERATION_RULES.format(
    table_info=_build_table_info(state),
    report_sqls=_build_report_sqls(state),
    past_sqls=_build_past_sqls(state),
    program_sqls=_build_program_sqls(state),   # 추가
    manual_refs=_build_manual_refs(state),
    domain_context=domain_context,
    domain_terms=_build_domain_terms(state),
    validation_feedback_section=_build_validation_feedback_section(state),
)
```

**주의:** `SQL_GENERATION_RULES` 템플릿에 `{program_sqls}` 플레이스홀더를 넣었는데 `.format()` 호출에서 누락하면 `KeyError`가 발생한다. 반드시 양쪽을 동시에 수정한다.

---

### Step 8. 테스트 작성

```python
# ── tests/unit/test_connectors.py ──
# 신규 커넥터의 Dummy 모드 테스트를 추가한다.

from src.connectors.program_connector import ProgramConnector

@pytest.mark.asyncio
async def test_program_health_check():
    """프로그램 커넥터 Dummy 헬스체크."""
    conn = ProgramConnector(use_dummy=True)
    await conn.connect()
    assert await conn.health_check()

@pytest.mark.asyncio
async def test_program_search():
    """프로그램 SQL 검색."""
    conn = ProgramConnector(use_dummy=True)
    await conn.connect()
    results = await conn.search_program_sql("고객")
    # Dummy 데이터가 반환되는지 확인
    assert len(results) > 0
```

```python
# ── tests/unit/test_search_context_assembler.py (신규 또는 기존 확장) ──
# search_context_assembler에서 신규 소스가 수집되는지 확인한다.

@pytest.mark.asyncio
async def test_collect_context_includes_program_sqls():
    """컨텍스트 수집 결과에 프로그램 SQL이 포함되는지 확인."""
    from src.services.search_context_assembler import collect_context
    context = await collect_context("고객 현황")
    # program_sqls 필드가 존재하고 리스트인지 확인
    assert isinstance(context.program_sqls, list)
```

---

### Step 9. 컨텍스트 수집 노드 trace 반영 — `src/agents/nodes/context_collector.py`

```python
# ── src/agents/nodes/context_collector.py ──
# trace 로그에 신규 소스 수집 건수를 추가한다.
# 사용자가 /api/query?include_trace=true 로 추론 과정을 볼 때 표시된다.

async def collect_context_node(state: PipelineState) -> dict:
    context = await collect_context(state.preprocessed_input)

    logger.info(
        "컨텍스트 수집 완료",
        tables=len(context.table_metas),
        past_sqls=len(context.past_sqls),
        report_sqls=len(context.report_sqls),
        manuals=len(context.manual_references),
        program_sqls=len(context.program_sqls),  # 추가
    )

    # trace 상세에 신규 소스 포함
    # ...
    if context.program_sqls:
        detail_parts.append(f"프로그램SQL {len(context.program_sqls)}건")  # 추가
```

---

### 전체 수정 파일 체크리스트 (추가)

| 순서 | 파일 | 수정 내용 |
|------|------|-----------|
| 1 | `src/config.py` | 접속 정보 필드 추가 |
| 2 | `.env.example` | 환경변수 플레이스홀더 추가 |
| 3 | `src/connectors/base.py` | (필요 시) 새 인터페이스 추가 |
| 4 | `src/connectors/<new>_connector.py` | 커넥터 클래스 신규 작성 |
| 5 | `src/connectors/manager.py` | `__init__`, `connect_all`, `disconnect_all`, `health_check_all`에 추가 |
| 6 | `src/agents/state/state.py` | `ContextInfo`에 필드 추가 |
| 7 | `src/services/search_context_assembler.py` | `_fetch_xxx` 함수 + `gather` + `ContextInfo` 조립 |
| 8 | `src/agents/nodes/sql_generator.py` | `_build_xxx` 함수 + `.format()` 인자 추가 |
| 9 | `src/agents/nodes/prompts/system_prompts.py` | `SQL_GENERATION_RULES`에 플레이스홀더 추가 |
| 10 | `src/agents/nodes/context_collector.py` | trace 로그에 수집 건수 추가 |
| 11 | `tests/unit/test_connectors.py` | Dummy 모드 테스트 추가 |
| 12 | `docs/architecture/architecture.md` | 아키텍처 구성도 업데이트 |

---

## 3. 저장소 변경 (기존 데이터소스 수정)

### 3-1. 접속 정보 변경 (호스트, 포트, 인증)

```text
영향 범위: config.py, .env만 수정.
코드 변경 없이 .env 파일의 값만 바꾸면 된다.
```

```dotenv
# .env 수정 예: ES 클러스터가 이전된 경우
ES_HOST=10.xx.xx.xx      # 새 ES 호스트
ES_PORT=9200
ES_USER=new_user          # 새 계정
ES_PASSWORD=new_password
```

```bash
# 변경 후 검증 — health check로 연결 확인
python -m uv run python -c "
import asyncio
from src.connectors.manager import get_connector_manager
async def test():
    m = get_connector_manager(use_dummy=False)
    await m.connect_all()
    print(await m.health_check_all())
asyncio.run(test())
"
```

---

### 3-2. 인덱스명·컬렉션명·테이블명 변경

```text
ES 인덱스명이나 Qdrant 컬렉션명이 바뀌면 해당 커넥터 파일을 수정한다.
인덱스명은 커넥터 내부에 하드코딩되어 있으므로 해당 문자열을 찾아 교체한다.
```

| 저장소 | 현재 인덱스/컬렉션명 | 위치 |
|--------|---------------------|------|
| ES 테이블 메타 | `"table_meta"` | `elasticsearch_connector.py` L203-L209 |
| ES 보고서 SQL | `"report_sql"` | `elasticsearch_connector.py` L225-L231 |
| ES 코드 메타 | `"code_meta"` | `elasticsearch_connector.py` L243-L246 |
| Qdrant 매뉴얼 | `"business_manual"` | `qdrant_connector.py` L140-L146 (주석 내) |

```python
# 예: ES 인덱스명이 "table_meta" → "dw_table_layout"으로 변경된 경우
# elasticsearch_connector.py에서 해당 문자열을 교체한다.

# 수정 전
resp = await self._client.search(
    index="table_meta",  # ← 이 값을 변경
    ...
)

# 수정 후
resp = await self._client.search(
    index="dw_table_layout",  # ← 새 인덱스명
    ...
)
```

**권장 개선:** 인덱스명을 `config.py`로 외부화하면 코드 수정 없이 `.env`에서 변경 가능하다.

```python
# src/config.py에 추가 (권장)
class Settings(BaseSettings):
    # ...
    es_index_table_meta: str = "table_meta"
    es_index_report_sql: str = "report_sql"
    es_index_code_meta: str = "code_meta"
    qdrant_collection_manual: str = "business_manual"
```

---

### 3-3. 데이터 스키마 변경 (필드 추가·삭제·이름 변경)

```text
저장소의 문서/레코드 필드가 변경되면, 해당 필드를 읽는 코드를 모두 수정해야 한다.
영향 범위는 커넥터 → search_context_assembler → (필요시) state.py 순이다.
```

#### 예: ES 테이블 메타에 `"owner"` 필드가 추가된 경우

```python
# 1) state.py — TableMeta에 필드 추가
class TableMeta(BaseModel):
    table_name: str
    table_description: str = ""
    columns: list[ColumnMeta] = Field(default_factory=list)
    update_cycle: str = ""
    enriched_description: str = ""
    owner: str = ""  # 추가: 테이블 관리 부서

# 2) search_context_assembler.py — _fetch_table_metas에서 파싱
table_metas.append(
    TableMeta(
        table_name=t["table_name"],
        table_description=t.get("table_description", ""),
        columns=columns,
        update_cycle=t.get("update_cycle", ""),
        owner=t.get("owner", ""),  # 추가
    )
)

# 3) sql_generator.py — 프롬프트에 포함할 경우
def _build_table_info(state: PipelineState) -> str:
    for table in state.context.table_metas:
        lines.append(f"\n### {table.table_name} - {table.table_description}")
        if table.owner:
            lines.append(f"관리부서: {table.owner}")  # 추가
```

---

### 3-4. 커넥터 기술 교체 (예: ES → OpenSearch)

```text
동일 역할의 저장소가 다른 기술로 교체되는 경우.
커넥터 인터페이스(base.py)를 유지하면서 구현체만 교체한다.
```

| 수정 파일 | 수정 내용 |
|-----------|-----------|
| `src/connectors/elasticsearch_connector.py` | `AsyncElasticsearch` → `AsyncOpenSearch` 교체, 메서드 시그니처 차이 대응 |
| `pyproject.toml` | `elasticsearch` → `opensearch-py` 패키지 교체 |
| `src/config.py` | (필요 시) 인증 방식 변경 |

```text
상위 레이어(search_context_assembler, sql_generator, state)는 수정 불필요.
SearchConnector 인터페이스의 search() 반환 형식이 동일하면 된다.
이것이 인터페이스 분리의 이점이다.
```

---

### 3-5. Dummy 데이터 변경

```text
Dummy 데이터는 개발·테스트 목적이므로 실 데이터와 구조가 동일해야 한다.
실 데이터 스키마가 변경되면 Dummy 데이터도 함께 수정한다.
```

| Dummy 데이터 | 위치 |
|-------------|------|
| `DUMMY_TABLE_META` | `elasticsearch_connector.py` L18-L111 |
| `DUMMY_REPORT_SQLS` | `elasticsearch_connector.py` L114-L130 |
| `DUMMY_CODE_META` | `elasticsearch_connector.py` L133-L138 |
| `DUMMY_SQL_HISTORY` | `postgres_connector.py` L94-L125 |
| `_generate_dummy_data()` | `postgres_connector.py` L21-L90 |
| `DUMMY_MANUALS` | `qdrant_connector.py` L18-L82 |

---

## 4. 저장소 삭제 (데이터소스 제거)

### 개요

```text
저장소를 삭제할 때는 추가의 역순으로 수행한다.
누락되면 사용되지 않는 코드가 남거나, 없는 커넥터를 호출하여 오류가 발생한다.

  Step 1 → system_prompts.py   : 프롬프트 플레이스홀더 제거
  Step 2 → sql_generator.py    : 빌더 함수 + .format() 인자 제거
  Step 3 → search_context_assembler.py  : 수집 함수 + gather 제거 + ContextInfo 조립 정리
  Step 4 → state.py            : ContextInfo 필드 제거
  Step 5 → manager.py          : 커넥터 등록 제거
  Step 6 → 커넥터 파일          : 파일 삭제
  Step 7 → config.py           : 접속 정보 제거
  Step 8 → .env.example        : 환경변수 플레이스홀더 제거

  + 테스트 제거/수정
  + 문서 업데이트
```

---

### 예: Qdrant (업무 매뉴얼) 저장소를 제거하는 경우

#### Step 1. 프롬프트 템플릿 — `src/agents/nodes/prompts/system_prompts.py`

```python
# SQL_GENERATION_RULES에서 업무 매뉴얼 섹션을 제거한다.

# 삭제할 부분:
# [업무 매뉴얼 참고]
# {manual_refs}
```

#### Step 2. SQL 생성 노드 — `src/agents/nodes/sql_generator.py`

```python
# (a) _build_manual_refs() 함수 전체 삭제

# (b) SQL_GENERATION_RULES.format()에서 manual_refs 인자 제거
system_prompt = SQL_GENERATION_RULES.format(
    table_info=_build_table_info(state),
    report_sqls=_build_report_sqls(state),
    past_sqls=_build_past_sqls(state),
    domain_context=domain_context,
    domain_terms=_build_domain_terms(state),
    validation_feedback_section=_build_validation_feedback_section(state),
    # manual_refs=_build_manual_refs(state),  ← 삭제
)
```

#### Step 3. 컨텍스트 수집 서비스 — `src/services/search_context_assembler.py`

```python
# (a) _fetch_manual_refs() 함수 전체 삭제

# (b) asyncio.gather에서 제거
(
    table_metas,
    report_sqls,
    past_sqls,
    # manual_refs,      ← 삭제
    domain_terms,
) = await asyncio.gather(
    _fetch_table_metas(query),
    _fetch_report_sqls(query),
    _fetch_past_sqls(query),
    # _fetch_manual_refs(query),  ← 삭제
    _fetch_code_meta(),
)

# (c) ContextInfo 조립에서 제거
context = ContextInfo(
    table_metas=table_metas,
    past_sqls=past_sqls,
    report_sqls=report_sqls,
    # manual_references=manual_refs,  ← 삭제
    domain_terms=domain_terms,
    table_disambiguation_guide=disambiguation_guide,
)
```

#### Step 4. 상태 모델 — `src/agents/state/state.py`

```python
# ContextInfo에서 필드 제거
class ContextInfo(BaseModel):
    table_metas: list[TableMeta] = Field(default_factory=list)
    past_sqls: list[str] = Field(default_factory=list)
    report_sqls: list[str] = Field(default_factory=list)
    # manual_references: list[str] = Field(default_factory=list)  ← 삭제
    domain_terms: dict[str, str] = Field(default_factory=dict)
    table_disambiguation_guide: str = ""
```

#### Step 5. 커넥터 매니저 — `src/connectors/manager.py`

```python
# __init__, connect_all, disconnect_all, health_check_all에서 qdrant 관련 코드 삭제
# import 문도 함께 삭제

# from src.connectors.qdrant_connector import QdrantConnector  ← 삭제
```

#### Step 6. 커넥터 파일 삭제

```bash
# 커넥터 파일 자체를 삭제한다.
rm src/connectors/qdrant_connector.py
```

#### Step 7. 설정 제거 — `src/config.py`

```python
# Qdrant 관련 설정 필드 삭제
# qdrant_host: str = "localhost"   ← 삭제
# qdrant_port: int = 6333          ← 삭제
```

#### Step 8. 환경변수 제거 — `.env.example`

```dotenv
# Qdrant 관련 환경변수 삭제
# QDRANT_HOST=localhost    ← 삭제
# QDRANT_PORT=6333         ← 삭제
```

#### Step 9. 테스트 제거

```python
# tests/unit/test_connectors.py에서 Qdrant 테스트 삭제
# test_qdrant_search_manual() 삭제

# context_collector_node trace에서 manual_references 참조 제거
# src/agents/nodes/context_collector.py의 trace 로그에서 manuals 관련 코드 삭제
```

#### Step 10. 의존성 패키지 제거 (선택)

```bash
# Qdrant 클라이언트 패키지가 더 이상 필요 없으면 의존성에서 제거한다.
python -m uv remove qdrant-client
```

---

### 삭제 시 전체 수정 파일 체크리스트

| 순서 | 파일 | 수정 내용 |
|------|------|-----------|
| 1 | `src/agents/nodes/prompts/system_prompts.py` | 프롬프트 플레이스홀더 및 섹션 제거 |
| 2 | `src/agents/nodes/sql_generator.py` | 빌더 함수 삭제, `.format()` 인자 제거 |
| 3 | `src/agents/nodes/context_collector.py` | trace 로그에서 관련 항목 제거 |
| 4 | `src/services/search_context_assembler.py` | 수집 함수, `gather`, `ContextInfo` 조립 정리 |
| 5 | `src/agents/state/state.py` | `ContextInfo` 필드 제거 |
| 6 | `src/connectors/manager.py` | 커넥터 등록 4곳 제거 + import 제거 |
| 7 | `src/connectors/<xxx>_connector.py` | 파일 삭제 |
| 8 | `src/config.py` | 접속 정보 필드 제거 |
| 9 | `.env.example` | 환경변수 플레이스홀더 제거 |
| 10 | `tests/unit/test_connectors.py` | 관련 테스트 삭제 |
| 11 | `pyproject.toml` | (선택) 미사용 패키지 제거 |
| 12 | `docs/architecture/architecture.md` | 아키텍처 구성도 업데이트 |

---

## 5. 시나리오별 예제

### 시나리오 A: 규제 문서 검색용 별도 ES 인덱스 추가

```text
배경: 금감원 규제 문서가 별도 ES 인덱스("regulation_docs")에 색인되었다.
      기존 ES 커넥터에 검색 메서드만 추가하면 되므로 신규 커넥터는 불필요하다.
```

| 단계 | 작업 |
|------|------|
| 커넥터 | `ElasticSearchConnector`에 `search_regulation_docs()` 메서드 추가 |
| ContextInfo | `regulation_refs: list[str]` 필드 추가 |
| search_context_assembler | `_fetch_regulation_docs()` 함수 + `gather` 등록 |
| sql_generator | `_build_regulation_refs()` + 프롬프트 섹션 추가 |
| config.py | 변경 없음 (기존 ES 접속 정보 재사용) |
| manager.py | 변경 없음 (기존 ES 커넥터 재사용) |

```python
# elasticsearch_connector.py — 메서드 추가만
async def search_regulation_docs(self, query: str) -> list[dict[str, Any]]:
    """금감원 규제 문서를 검색한다."""
    if self._use_dummy:
        return [{"title": "규제 예시", "content": "..."}]

    resp = await self._client.search(
        index="regulation_docs",
        body={"query": {"multi_match": {"query": query, "fields": ["*"]}}, "size": 5},
    )
    return [hit["_source"] for hit in resp["hits"]["hits"]]
```

---

### 시나리오 B: 이력 DB를 PostgreSQL → MongoDB로 교체

```text
배경: SQL 이력 저장소가 PostgreSQL에서 MongoDB로 마이그레이션되었다.
      커넥터 내부 구현만 교체하고, 인터페이스(search_similar_sql)는 유지한다.
```

| 단계 | 작업 |
|------|------|
| 커넥터 | `HistoryDBConnector` 내부를 Motor(async MongoDB) 기반으로 교체 |
| config.py | `history_db_*` 필드를 MongoDB 접속 정보로 변경 |
| pyproject.toml | `asyncpg` → `motor` 패키지 교체 |
| search_context_assembler | 변경 없음 (`search_similar_sql()` 인터페이스 동일) |
| sql_generator | 변경 없음 |
| state.py | 변경 없음 |

```text
핵심: SearchConnector 또는 DatabaseConnector 인터페이스를 유지하면
      상위 레이어(search_context_assembler, sql_generator)는 수정하지 않아도 된다.
```

---

### 시나리오 C: Qdrant를 제거하고 ES 기반 벡터 검색으로 통합

```text
배경: ES 8.x의 kNN 벡터 검색으로 업무 매뉴얼 검색을 통합한다.
      Qdrant를 제거하고 ES 커넥터에 벡터 검색 메서드를 추가한다.
```

| 단계 | 작업 |
|------|------|
| ES 커넥터 | `search_manual_vector()` 메서드 추가 |
| Qdrant 커넥터 | 삭제 (4장 절차 따름) |
| search_context_assembler | `_fetch_manual_refs()`가 `manager.es.search_manual_vector()` 호출로 변경 |
| manager.py | Qdrant 제거, ES는 그대로 |
| ContextInfo | `manual_references` 필드 유지 (소스만 변경, 의미 동일) |
| config.py | Qdrant 설정 삭제, ES 설정에 벡터 인덱스명 추가 |

```text
핵심: ContextInfo.manual_references의 의미와 타입이 동일하면
      sql_generator.py와 프롬프트는 수정하지 않아도 된다.
      데이터의 "소스"가 바뀌었을 뿐, "역할"은 동일하기 때문이다.
```

---

### 시나리오 D: Redis 캐시를 지식 소스로 확장

```text
배경: 자주 묻는 질의의 SQL 결과를 Redis에 캐싱하고,
      동일/유사 질의 시 캐시된 SQL을 참고 소스로 활용한다.
```

| 단계 | 작업 |
|------|------|
| 커넥터 | 기존 Redis 접속 정보 재사용, 캐시 조회 메서드 추가 |
| ContextInfo | `cached_sqls: list[str]` 필드 추가 |
| search_context_assembler | `_fetch_cached_sqls()` 함수 + `gather` 등록 |
| sql_generator | `_build_cached_sqls()` + 프롬프트 섹션 추가 |
| manager.py | Redis 커넥터가 이미 있으면 재사용, 없으면 추가 |

---

## 부록: 설계 원칙 요약

### 인터페이스 분리 원칙

```text
각 레이어는 아래 계약(contract)만 지키면 독립적으로 교체할 수 있다:

  커넥터 → search_context_assembler : 반환 타입 (list[dict], list[str], dict)
  search_context_assembler → state  : ContextInfo 필드 타입
  state → sql_generator    : 필드 존재 여부 및 타입
  sql_generator → prompts  : 플레이스홀더 이름 일치

한 레이어의 내부 구현이 바뀌어도, 이 계약이 유지되면
상위 레이어는 수정하지 않아도 된다.
```

### 장애 격리 원칙

```text
모든 데이터소스 수집 함수는 try/except로 감싸야 한다.
한 소스의 장애가 전체 파이프라인을 중단시키지 않도록
실패 시 빈 값([], {}, "")을 반환한다.

  async def _fetch_xxx(query: str) -> list[str]:
      try:
          ...
      except Exception as e:
          logger.warning("xxx 검색 실패, 빈 목록으로 폴백", error=str(e))
          return []  # ← 절대로 예외를 상위로 전파하지 않는다
```

### Dummy 모드 필수 원칙

```text
모든 커넥터는 use_dummy=True 모드를 반드시 지원해야 한다.
이유:
  1. 폐쇄망 이관 전 개발 환경에서 외부 인프라 없이 테스트 가능
  2. CI/CD 파이프라인에서 외부 의존성 없이 자동 테스트 가능
  3. 신규 개발자가 환경 설정 없이 즉시 개발 착수 가능

Dummy 데이터는 실 데이터와 동일한 구조(필드명, 타입)를 유지한다.
```
