# PostgreSQL 기반 대화 이력 관리 — LangGraph 네이티브 구현 설계

- **작성일**: 2026-04-05
- **상태**: 설계 완료, 구현 대기
- **참조 전략**: `docs/strategy-proposals/checkpointer-multi-turn/01-strategy.md`
- **참조 상세설계**: `docs/strategy-proposals/checkpointer-multi-turn/02-detailed-design.md`
- **참조 DDL**: `resources/connectors/postgres/init_checkpointer_ddl.sql`
- **참조 리서치**: `docs/research/20260405-postgresql-conversation-history.md`
- **영향 범위**: `checkpointer.py`, `runner.py`, `main.py`, `session/store.py`, `config.py`

---

## 1. 핵심 원칙: Checkpointer = 단일 진실 공급원

### 1.1 설계 철학

`01-strategy.md` §2.5의 핵심 결정을 따른다:

> **"Checkpointer = 그래프 상태의 단일 진실 공급원"**

LangGraph의 `AsyncPostgresSaver`가 파이프라인 상태를 PostgreSQL에 자동 영속화하므로,
파이프라인 상태를 별도 테이블에 이중 저장하지 않는다.

단, `get_state_history()` API는 모든 체크포인트를 메모리에 로딩하여 **심각한 성능 문제**가
있으므로(체크포인트 1개 500KB~1.2MB × 160개 = 수백 MB), 대화 텍스트 조회/UI 복원용으로
경량 TEXT 테이블 `checkpoint_dc_turn_texts`를 별도로 둔다.
금융 감사 조회는 같은 테이블에 `mask_pii()` PostgreSQL 함수를 적용하여 처리한다.

### 1.2 아키텍처 (TO-BE)

```
[사용자 질의]
      |
      v
[LangGraph Pipeline]
      |
      +──(자동)──────> AsyncPostgresSaver                                ← 유일한 상태 저장소
      |                bdptbl.checkpoints / checkpoint_blobs               파이프라인 상태 + 대화 맥락
      |                thread_id = session_id                               interrupt/resume, 오류 복구
      |                (search_path=bdptbl 으로 스키마 유도)                 get_state_history() → 대화 이력
      |
      +──(명시적)────> checkpoint_dc_turn_texts                  ← 경량 대화 이력 + 감사 통합
      |                TEXT 기반 (역직렬화 불필요)                          UI 과거 대화 복원
      |                content + metadata(SVG, trace, insight, SQL)        LLM 대화 맥락 전달
      |                감사 조회 시 mask_pii() PG 함수 적용                 금융 감사 대응
      |
      +──(명시적)────> checkpoint_dc_session_index               ← user_id ↔ thread_id 매핑
                       사용자별 세션 목록 조회용
```

### 1.3 네이밍 규칙

**스키마**: `bdptbl` (폐쇄망 공용 스키마, 다른 업무 테이블과 공존)

**체크포인터 테이블**: 라이브러리 기본 이름 그대로 사용 (`checkpoints`, `checkpoint_blobs` 등)
**커스텀 테이블**: `checkpoint_dc_` prefix (`checkpoint_dc_turn_texts`, `checkpoint_dc_session_index`)

| 테이블 | 생성 주체 | prefix | 비고 |
|--------|----------|--------|------|
| `bdptbl.checkpoint_migrations` | `setup()` 자동 | 없음 (라이브러리 기본) | `search_path=bdptbl` 로 스키마 유도 |
| `bdptbl.checkpoints` | `setup()` 자동 | 〃 | 〃 |
| `bdptbl.checkpoint_blobs` | `setup()` 자동 | 〃 | 〃 |
| `bdptbl.checkpoint_writes` | `setup()` 자동 | 〃 | 〃 |
| `checkpoint_dc_turn_texts` | 수동 DDL | `checkpoint_dc_` | 대화 이력 + 감사 통합 (TEXT, UI 복원, 감사 조회) |
| `checkpoint_dc_session_index` | 수동 DDL | `checkpoint_dc_` | user_id → thread_id 매핑 |
| `mask_pii()` | 수동 DDL | — | PII 마스킹 PostgreSQL 함수 (감사 조회용) |

**설계 근거:**

- `AsyncPostgresSaver`는 테이블명이 **하드코딩**되어 있어 prefix 커스터마이징 불가
- `search_path=bdptbl,public` 을 connection 옵션으로 설정하면
  `setup()`이 `bdptbl` 스키마에 기본 이름으로 테이블을 생성한다
- 커스텀 테이블은 `checkpoint_dc_` prefix로 체크포인터 패밀리(`checkpoint_*`)에
  속하면서도 `_dc_`로 Data Copilot 사용자 정의임을 명시한다
- **스키마 접근 통일**: 체크포인터·커스텀 SQL 모두 `search_path`에 의존하여 테이블명만 사용.
  SQL 코드에서 `bdptbl.` 스키마 접두어를 명시하지 않는다

```python
# checkpointer.py — search_path로 bdptbl 스키마 유도
connection_kwargs = {
    "autocommit": True,
    "prepare_threshold": 0,
    "row_factory": dict_row,
    "password": db.password,
    "options": "-c search_path=bdptbl,public",
}
```

### 1.4 기각된 대안과 `turn_texts` 도입 근거

#### 기각: 하이브리드 2-계층 (Checkpointer + conversation_turns에 상태 이중 저장)

| 기각 이유 | 상세 |
|----------|------|
| **이중 저장** | 같은 **파이프라인 상태**를 checkpointer와 별도 테이블에 중복 기록 → 불일치 위험 |
| **동기화 부담** | 두 시스템 간 정합성 유지 로직 필요 (트랜잭션 경계 불일치) |
| **불필요한 복잡도** | `ConversationRepository` 별도 구현 → 유지보수 부담 증가 |

#### 기각: `get_state_history()`로 대화 이력 직접 조회

| 기각 이유 | 상세 |
|----------|------|
| **메모리 폭발** | 모든 체크포인트를 순차 역직렬화 (500KB~1.2MB × 160개 = 80~192MB) |
| **불필요한 오버헤드** | 대화 텍스트 몇 줄을 위해 전체 PipelineState(컨텍스트, SQL 결과 등)를 로딩 |
| **동시 사용자 확장 불가** | 사용자 수 × 수백 MB → 서버 OOM 위험 |

#### 채택: `checkpoint_dc_turn_texts` (경량 TEXT 테이블)

| 채택 근거 | 상세 |
|----------|------|
| **역직렬화 불필요** | TEXT 컬럼 → SQL SELECT 한 줄로 즉시 조회 (수 KB) |
| **이중 저장이 아님** | 체크포인터는 파이프라인 상태, turn_texts는 대화 텍스트 + UI 복원 데이터 — 성격이 다름 |
| **UI 완전 복원** | metadata JSONB에 SVG, 추론흐름, 인사이트, SQL 포함 → 과거 세션을 원래 경험 그대로 재현 |
| **운영 활용** | 이슈 트래킹 컬럼(status, error_type, exit_node, model_id)으로 1차 장애 진단 가능 |
| **감사 통합** | 감사 조회 시 `mask_pii()` PG 함수를 SELECT에 적용 — 별도 감사 테이블 불필요 |

#### 기각: 감사 전용 테이블 분리 (`checkpoint_dc_agent_actions`)

| 기각 이유 | 상세 |
|----------|------|
| **PII가 유일한 차이** | 저장 시 마스킹 대신 조회 시 `mask_pii()` 함수로 해결 가능 |
| **INSERT-only RLS 과잉** | DB 계정 권한(DELETE/UPDATE 미부여)으로 충분, RLS는 운영 경직화 |
| **SQL 해시만 저장은 무의미** | 감사에서 "어떤 쿼리를 실행했는지"가 핵심 — 해시로는 추적 불가 |
| **이중 저장/동기화** | 같은 정보를 두 테이블에 기록 → 불일치 위험, 구현 복잡도 증가 |

---

## 2. 체크포인터가 제공하는 대화 이력 기능

### 2.1 LangGraph 공식 API 활용

| API | 용도 | 반환 |
|-----|------|------|
| `app.aget_state(config)` | 최신 상태 조회 (현재 턴) | `StateSnapshot` — values, next, tasks |
| `app.aget_state_history(config)` | 전체 체크포인트 이력 | `AsyncIterator[StateSnapshot]` — 모든 노드 실행 시점의 스냅샷 |
| `app.aget_state(config, subgraphs=True)` | 서브그래프 포함 상태 | 중첩 그래프의 내부 상태까지 조회 |

### 2.2 대화 이력 재구성: `checkpoint_dc_turn_texts`

> **`get_state_history()` 사용을 기각한다.**
> 이 API는 해당 thread의 모든 체크포인트(노드 실행 시점 스냅샷)를
> 메모리에 순차 로딩한다. 체크포인트 1개 = 전체 PipelineState(500KB~1.2MB).
> 16개 노드 × 10턴 = ~160개 → **80~192MB 메모리 로딩**.
> 대화 텍스트 몇 줄을 위해 수백 MB를 역직렬화하는 것은 비합리적이다.

대신 경량 TEXT 테이블 `checkpoint_dc_turn_texts`에 턴별 메시지를 저장하고,
단순 SQL SELECT로 대화 이력을 조회한다.

#### DDL

```sql
CREATE TABLE checkpoint_dc_turn_texts (
    -- 식별
    thread_id     TEXT NOT NULL,                -- checkpointer thread_id (= session_id)
    turn_seq      SMALLINT NOT NULL,            -- 턴 순번 (INSERT 시 원자적 채번, §2.2.1 참조)
    turn_id       UUID NOT NULL DEFAULT gen_random_uuid(),  -- 외부 참조용 고유 ID (REST API 경로 식별자)

    -- 대화 내용
    role          TEXT NOT NULL
                  CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,                -- 사용자 입력 또는 포맷팅된 응답 전문 (마크다운 포함)

    -- 감사 (5W: Where — Who는 session_index JOIN)
    client_ip     INET,                         -- Where (접속 IP)
    user_agent    TEXT,                         -- Where (브라우저/클라이언트)

    -- 대화 분류
    turn_type     TEXT NOT NULL DEFAULT 'normal'
                  CHECK (turn_type IN ('normal', 'clarification', 'error')),
    intent        TEXT,                         -- IntentType (EXTRACT, AGGREGATE 등)

    -- 운영 메트릭
    token_count   INT,                          -- LLM 토큰 사용량 (input + output)
    latency_ms    INT,                          -- 해당 턴 전체 처리 소요 시간

    -- 이슈 트래킹
    request_id    TEXT,                         -- 서버 로그·감사 테이블 교차 조회용
    status        TEXT NOT NULL DEFAULT 'success'
                  CHECK (status IN ('success', 'failure', 'cancelled', 'timeout')),
    error_type    TEXT,                         -- 에러 분류 (LLM_TIMEOUT, SQL_VALIDATION_FAIL 등)
    error_message TEXT,                         -- 간략 에러 메시지 (1줄, 스택트레이스 아님)
    exit_node     TEXT,                         -- 마지막 실행 노드 (sql_generator 등)
    model_id      TEXT,                         -- 사용된 LLM 모델 (solar-pro-2-70b 등)
    trace_id      TEXT,                         -- LangSmith run_id (상세 추적 링크)

    -- UI 사용자 액션 (사후 UPDATE)
    is_liked      BOOLEAN,                      -- 좋아요: NULL=미평가, true=좋아요, false=싫어요
    liked_at      TIMESTAMPTZ,                  -- 좋아요/싫어요 클릭 시각
    is_downloaded BOOLEAN NOT NULL DEFAULT false, -- 결과 다운로드(엑셀 등) 여부
    downloaded_at TIMESTAMPTZ,                  -- 최초 다운로드 시각

    -- UI 복원 + 확장용
    metadata      JSONB DEFAULT '{}',           -- 아래 §2.2.2 참조

    -- 시간
    base_ymd      CHAR(8) NOT NULL DEFAULT to_char(now(), 'YYYYMMDD'),  -- 파티션 키 (§3.4 참조)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (thread_id, turn_seq, base_ymd)
) PARTITION BY RANGE (base_ymd);

-- 파티션 생성 예시 (월 단위)
CREATE TABLE checkpoint_dc_turn_texts_202604
    PARTITION OF checkpoint_dc_turn_texts
    FOR VALUES FROM ('20260401') TO ('20260501');

-- 인덱스 (파티션 테이블에 자동 전파 — 각 파티션에 로컬 인덱스 생성)
CREATE INDEX ON checkpoint_dc_turn_texts (turn_id);          -- REST API turn_id 단독 조회용 (비UNIQUE, §2.2.5 참조)
CREATE INDEX ON checkpoint_dc_turn_texts (thread_id, created_at);
CREATE INDEX ON checkpoint_dc_turn_texts (status, created_at DESC);
CREATE INDEX ON checkpoint_dc_turn_texts (request_id);
CREATE INDEX ON checkpoint_dc_turn_texts (is_liked) WHERE is_liked IS NOT NULL;
```

#### 설계 근거

| 결정 | 이유 |
|------|------|
| TEXT 기반 저장 | 역직렬화 불필요, SQL SELECT만으로 즉시 조회 |
| 메시지 단위 1행 | user/assistant 각각 1행 — 명확화 턴 등 비대칭 대화에 유연 |
| `content`에 마크다운 포함 | formatter가 생성한 최종 응답 전문 — LLM 맥락 전달에 그대로 사용 |
| `metadata` JSONB | UI 복원에 필요한 비텍스트 데이터(SVG, 추론흐름, 인사이트, SQL) 수용. §2.2.3 참조 |
| PII 마스킹 안 함 | LLM 맥락 전달용이므로 마스킹 시 맥락 훼손, 접근 제어는 GRANT로 처리 |
| 감사 테이블 통합 | 별도 감사 테이블 없이 감사 필드(client_ip 등) 포함. Who(user_id)는 `session_index` JOIN으로 해결. 감사 조회 시 `mask_pii()` PG 함수 적용 |
| `user_id`/`user_dept` 제거 | 세션 내에서 불변이므로 `session_index`에만 저장하여 매 행 중복 방지. 감사 조회 시 `session_index` JOIN |
| 원자적 `turn_seq` 채번 | INSERT 서브쿼리로 `MAX(turn_seq)+1`을 원자 실행 (§2.2.1). PK 제약이 최종 안전망 |
| `base_ymd` 파티션 | 월 단위 `PARTITION BY RANGE (base_ymd)`. 5년 보관 후 `DROP PARTITION`으로 테이블 잠금 없이 즉시 정리. `DELETE`보다 수십~수백 배 빠르고 VACUUM 불필요 |
| `turn_id` 비유니크 인덱스 | 파티션 테이블에서 `turn_id` 단독 조회 시 `CREATE INDEX ON checkpoint_dc_turn_texts (turn_id)` (비유니크)로 처리. UUID 특성상 사실상 유일하며, 파티션 수가 적은 환경(월 단위 60개)에서 인덱스 병합 비용 미미. REST API `PATCH /turns/{turn_id}/*` 엔드포인트가 이 패턴 사용 |
| UI 액션 별도 컬럼 | `is_liked`, `is_downloaded`는 사후 UPDATE되는 구조화된 사용자 액션 → metadata(INSERT 시 확정되는 비정형 데이터)와 성격이 다름. B-tree 인덱스로 운영 분석(좋아요 비율, 다운로드율) 직접 지원 |
| `is_liked` 3-state | NULL=미평가, true=좋아요, false=싫어요 — BOOLEAN 2값보다 "아직 평가 안 함"을 구분 가능. partial index(`WHERE is_liked IS NOT NULL`)로 평가된 턴만 효율적 조회 |
| `*_at` 타임스탬프 | 좋아요·다운로드 시각 기록 → 사용자 행동 패턴 분석, 응답 품질 개선 피드백 루프에 활용 |

#### `metadata` JSONB 구조 (role='assistant' 행)

과거 세션을 열었을 때 **UI를 원래 경험 그대로 복원**하기 위한 데이터:

```jsonc
{
    // ── UI 복원용 (PipelineResult 필드 대응) ──

    // 추론 흐름 (진행 중 실시간 갱신 → 완료 후 최종 상태 저장)
    "trace_log": [
        {"node": "context_collector", "action": "컨텍스트 수집", "detail": "3건"},
        {"node": "sql_generator", "action": "SQL 생성", "detail": "SELECT ..."},
        {"node": "sql_validator", "action": "SQL 검증", "detail": "통과"}
    ],

    // 전구 버튼 → 통찰 정보
    "insight": {
        "table_selection_reason": "고객원장(TB_CUST) 선택 - 신규등록일 컬럼 존재",
        "analysis_summary": "전월 대비 12% 증가 추세"
    },

    // SVG 차트
    "visualization": {
        "svg_code": "<svg>...</svg>",
        "chart_type": "bar",
        "title": "부서별 신규 고객"
    },

    // SQL 실행 결과 요약 (rows 원본은 재실행으로 획득)
    "sql_result": {
        "columns": ["부서", "신규고객", "전월대비"],
        "row_count": 15
    },

    // 수행 SQL (성공 시)
    "executed_sql": "SELECT dept_nm, COUNT(*) AS cnt ... FROM tb_cust WHERE ...",

    // 명확화 요청 (turn_type='clarification' 시)
    "clarification": {
        "question": "어떤 기준으로 조회할까요?",
        "options": ["부서별", "월별", "상품별"]
    },

    // ── 운영 메트릭 (이슈 트래킹 보조) ──
    "retry_count": 2,
    "context_sources_hit": ["mongo", "qdrant"],
    "node_durations_ms": {
        "context_collector": 320,
        "sql_generator": 1500,
        "sql_validator": 200
    },
    "validation_errors": ["missing JOIN condition"]
}
```

#### `metadata` 저장 전략과 미래 AI 활용 (§2.2.3)

**현재 전략**: metadata JSONB에 UI 복원 데이터를 통합 저장한다. 이 구조는 다음 이유로 유효하다:

1. **검색 가능한 필드는 이미 독립 컬럼**: `content`(자연어 텍스트), `intent`(의도 분류), `status`(실행 결과) 등 AI가 맥락 판단에 사용할 핵심 필드는 JSONB 안이 아닌 독립 컬럼
2. **metadata 내부 데이터는 "표현용"**: SVG, trace_log, 인사이트 등은 같은 턴의 결과를 다양한 형태로 보여주는 데이터이지, 검색 조건이 아님
3. **`executed_sql`은 예외적 검색 대상**: SQL 텍스트로 이력을 찾는 유스케이스가 있으나, `metadata->>'executed_sql'` JSONB 경로 연산자로 직접 조회 가능

**향후 AI History Tool 확장 시 검색 전략:**

| 검색 유형 | 대상 컬럼 | 방법 | 시점 |
| -------- | -------- | ---- | ---- |
| 의도 기반 | `intent` | 정확 매칭 (`WHERE intent = 'EXTRACT'`) | 현재 가능 |
| 키워드 기반 | `content` | `pg_trgm` GIN 인덱스 + `ILIKE` 또는 `ts_vector` 전문검색 | 필요 시 인덱스 추가 |
| SQL 패턴 기반 | `metadata->>'executed_sql'` | JSONB 경로 연산 + `ILIKE` | 현재 가능 (느림), 필요 시 `generated column` + 인덱스 |
| 유사 질의 기반 | `content` | 임베딩 벡터 + Qdrant (이미 SQL 이력 임베딩 인프라 존재) | 확장 시 |

> **설계 판단**: 현 시점에서 `executed_sql`을 독립 컬럼으로 승격하지 않는다.
> AI History Tool이 구체화되면, **generated column** (`AS metadata->>'executed_sql' STORED`)과
> 인덱스를 ALTER TABLE로 무중단 추가할 수 있으므로, 사전 최적화보다 실제 요구에 맞춰 확장한다.

#### `metadata` 필드 ↔ PipelineResult 매핑

| PipelineResult 필드 | metadata 키 | 용도 |
|---------------------|-------------|------|
| `trace_log` | `trace_log` | 추론 흐름 (실시간 갱신 후 최종 스냅샷) |
| `insight` | `insight` | 전구 버튼 통찰 정보 |
| `visualization` | `visualization` | SVG 차트 + 유형 + 제목 |
| `sql_result` | `sql_result` | 컬럼명 + 행 수 (rows 제외) |
| (reason.validated_sql) | `executed_sql` | 성공 시 수행된 SQL 전문 |
| `clarification_request` | `clarification` | 명확화 질문 + 선택지 |

#### 대화 이력 조회 (LLM 맥락 전달용)

```python
async def get_conversation_history(
    pool: Any,
    session_id: str,
) -> list[dict[str, str]]:
    """checkpoint_dc_turn_texts에서 대화 이력을 조회한다.

    TEXT 기반이므로 역직렬화 없이 즉시 반환된다.
    LLM 맥락 전달용: content(텍스트)만 추출한다.
    """
    async with pool.connection() as conn:
        rows = await conn.execute(
            """
            SELECT role, content
            FROM checkpoint_dc_turn_texts
            WHERE thread_id = %(thread_id)s
            ORDER BY turn_seq
            """,
            {"thread_id": session_id},
        )
        return [{"role": r["role"], "content": r["content"]} for r in rows]
```

#### UI 과거 대화 복원: 2-tier 로딩 전략 (§2.2.4)

> **설계 판단**: 과거 세션을 UI에서 열 때 **전체 턴의 content는 즉시 로드**하되,
> **metadata(SVG, trace_log 등)는 턴별 지연 로드**한다.
>
> **근거**:
>
> - 은행 직원의 세션은 보통 5~30턴. content만이면 전체 10~50KB → 페이지네이션 불필요
> - metadata의 SVG 차트는 턴당 100~500KB 가능. 20턴 × 300KB = 6MB를 초기에 전송하면 과도
> - 사용자가 과거 대화를 열면 "어떤 질문을 했었지?" 확인이 우선이지, 모든 차트를 동시에 볼 필요는 없음
> - 스크롤 기반 페이지네이션은 채팅 UI에서 "위로 스크롤하면 이전 대화가 나오는" UX가 자연스럽지만,
>   5~30턴 규모에서는 복잡도 대비 이득이 없음

```python
# ── Tier 1: 전체 턴 content + 경량 필드 (즉시 로드) ──

async def get_session_turns_for_ui(
    pool: Any,
    session_id: str,
) -> list[dict[str, Any]]:
    """UI에서 과거 세션을 열 때 전체 턴의 경량 데이터를 반환한다.

    metadata(SVG 등)는 포함하지 않는다 → Tier 2로 개별 로드.
    """
    async with pool.connection() as conn:
        rows = await conn.execute(
            """
            SELECT turn_id, turn_seq, role, content, turn_type,
                   is_liked, is_downloaded, status, created_at
            FROM checkpoint_dc_turn_texts
            WHERE thread_id = %(thread_id)s
            ORDER BY turn_seq
            """,
            {"thread_id": session_id},
        )
        return [dict(r) for r in rows]


# ── Tier 2: 특정 턴의 metadata (지연 로드) ──

async def get_turn_metadata(
    pool: Any,
    turn_id: str,
) -> dict[str, Any] | None:
    """특정 턴의 metadata를 반환한다.

    UI에서 사용자가 특정 턴의 상세(차트, 추론흐름, SQL)를 볼 때 호출.
    """
    async with pool.connection() as conn:
        row = await conn.execute(
            """
            SELECT metadata
            FROM checkpoint_dc_turn_texts
            WHERE turn_id = %(turn_id)s
            """,
            {"turn_id": turn_id},
        )
        result = row.fetchone()
        return result["metadata"] if result else None
```

#### UI 사용자 액션 UPDATE

좋아요/싫어요, 다운로드는 사용자 클릭 시 사후 UPDATE된다.
`turn_id`에 비유니크 인덱스가 있으므로 `WHERE turn_id = ?`로 직접 조회한다.
UUID 특성상 사실상 유일하며, 파티션 수가 적어 인덱스 병합 비용이 미미하다.

```python
async def toggle_like(
    pool: Any,
    turn_id: str,
    is_liked: bool | None,
) -> dict[str, Any] | None:
    """턴에 좋아요/싫어요를 설정하거나 해제한다.

    Returns:
        업데이트된 턴 정보 dict. turn_id가 존재하지 않으면 None.
    """
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            UPDATE checkpoint_dc_turn_texts
            SET is_liked = %(is_liked)s,
                liked_at = CASE WHEN %(is_liked)s IS NOT NULL THEN now() ELSE NULL END
            WHERE turn_id = %(turn_id)s
            """,
            {"turn_id": turn_id, "is_liked": is_liked},
        )
        if result.rowcount == 0:
            return None
        return {"turn_id": turn_id, "is_liked": is_liked}


async def mark_downloaded(pool: Any, turn_id: str) -> dict[str, Any] | None:
    """턴의 결과를 다운로드했음을 기록한다 (최초 1회만 시각 기록)."""
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            UPDATE checkpoint_dc_turn_texts
            SET is_downloaded = true,
                downloaded_at = COALESCE(downloaded_at, now())
            WHERE turn_id = %(turn_id)s
            """,
            {"turn_id": turn_id},
        )
        if result.rowcount == 0:
            return None
        return {"turn_id": turn_id, "is_downloaded": True}
```

##### §2.2.5 turn_id 조회 전략

> **문제**: 파티션 테이블에서 `WHERE turn_id = ?` 단독 조회 시 파티션 프루닝이 불가하여
> 모든 파티션의 인덱스를 탐색(append 계획)해야 한다.
>
> **해결**: `CREATE INDEX ON checkpoint_dc_turn_texts (turn_id)` 비유니크 인덱스를 생성한다.
> UUID v4 특성상 사실상 유일하므로 각 파티션 인덱스에서 0~1건만 매칭된다.
> 월 단위 60개 파티션 환경에서 인덱스 병합 비용은 미미하며 (파티션당 B-tree 탐색 1회),
> 별도 매핑 테이블 없이 단순한 `WHERE turn_id = ?`로 직접 조회/UPDATE가 가능하다.
>
> **UNIQUE 인덱스 불가 사유**: PostgreSQL 파티션 테이블에서 UNIQUE 인덱스는
> 반드시 파티션 키(`base_ymd`)를 포함해야 한다. turn_id 단독 UNIQUE는 불가하므로
> 비유니크 인덱스를 사용하되, UUID 충돌 확률(2^122 중 1)로 실질적 유일성이 보장된다.

#### 저장 예시

```
thread_id  | turn_seq | role      | content                    | turn_type     | status  | is_liked | is_downloaded | base_ymd | exit_node        | metadata
-----------+----------+-----------+----------------------------+---------------+---------+----------+---------------+----------+------------------+-------------------
sess-a1b2  | 1        | user      | 이번 달 신규 고객 수 알려줘  | normal        | success | NULL     | false         | 20260405 | -                | {}
sess-a1b2  | 2        | assistant | 📊 신규 고객은 총 1,234명... | normal        | success | true     | true          | 20260405 | result_finalizer | {trace_log, insight, visualization, sql_result, executed_sql, ...}
sess-a1b2  | 3        | assistant | 어떤 기준으로 조회할까요?...  | clarification | success | NULL     | false         | 20260405 | readiness_gate   | {clarification: {question, options}}
sess-a1b2  | 4        | user      | 부서별로 나눠줘             | clarification | success | NULL     | false         | 20260405 | -                | {}
sess-a1b2  | 5        | assistant | 부서별 현황입니다...         | normal        | success | true     | false         | 20260405 | result_finalizer | {trace_log, insight, visualization, sql_result, executed_sql, ...}
```

### 2.3 사용자별 세션 목록: `session_index`

체크포인터는 `user_id` 컬럼이 없으므로 사용자별 세션 관리가 불가하다.
경량 매핑 테이블 `checkpoint_dc_session_index`로 해결한다.

```sql
CREATE TABLE checkpoint_dc_session_index (
    thread_id    TEXT PRIMARY KEY,           -- checkpointer thread_id (= session_id)
    user_id      TEXT NOT NULL,              -- SSO 연동 전에는 "anonymous"
    user_dept    TEXT,                       -- 부서
    title        TEXT,                       -- 세션 제목 (첫 질의 요약, 선택적)
    is_archived  BOOLEAN NOT NULL DEFAULT false,  -- soft delete (§8.3 DELETE 엔드포인트)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON checkpoint_dc_session_index (user_id, last_active DESC)
    WHERE is_archived = false;
```

**설계 근거:**
- 대화 내용은 체크포인터에만 저장 → 이중 저장 없음
- `user_id` → `thread_id` 매핑만 관리하므로 테이블이 극히 가볍다
- "김과장의 세션 목록" → `session_index WHERE user_id` → 각 `thread_id`로 `turn_texts` 조회

**사용자별 세션 목록 조회:**

```sql
-- 사용자의 최근 세션 목록 (아카이브 제외)
SELECT thread_id, title, last_active
FROM checkpoint_dc_session_index
WHERE user_id = %(user_id)s
  AND is_archived = false
ORDER BY last_active DESC
LIMIT 20;
```

**세션 등록 시점:** `runner.py`의 `run_pipeline()` 시작 시 upsert.
user_id는 SSO 연동 전 `"anonymous"`, title은 첫 질의 앞 50자로 자동 생성.

```python
# turn_text_store.py에 추가
async def upsert_session_index(
    pool: Any,
    *,
    thread_id: str,
    user_id: str = "anonymous",
    user_dept: str | None = None,
    title: str | None = None,
) -> None:
    """세션 인덱스를 등록하거나 last_active를 갱신한다."""
    async with pool.connection() as conn:
        await conn.execute("""
            INSERT INTO checkpoint_dc_session_index
                (thread_id, user_id, user_dept, title)
            VALUES (%(thread_id)s, %(user_id)s, %(user_dept)s, %(title)s)
            ON CONFLICT (thread_id) DO UPDATE
            SET last_active = now()
        """, {
            "thread_id": thread_id,
            "user_id": user_id,
            "user_dept": user_dept,
            "title": title,
        })
```

```python
# runner.py run_pipeline() 시작부에서 호출
from src.services.turn_text_store import upsert_session_index

pool = get_connector_manager().checkpointer_pool
title = user_input[:50] + ("..." if len(user_input) > 50 else "")
await upsert_session_index(
    pool,
    thread_id=session_id,
    user_id="anonymous",       # SSO 연동 전 고정값
    title=title,
)
```

---

## 3. 감사 조회 전략

### 3.1 통합 설계

별도 감사 테이블을 두지 않고 `checkpoint_dc_turn_texts` 하나로 통합한다.
감사 조회 시 `mask_pii()` PostgreSQL 함수를 SELECT에서 직접 호출하여 PII를 마스킹한다.

| 요건 | 충족 방법 |
|------|----------|
| DBA/감사팀 SQL 직접 조회 | `checkpoint_dc_turn_texts`는 TEXT 기반 → SQL로 즉시 조회 가능 |
| PII 보호 | 조회 시 `mask_pii()` PG 함수 적용 (저장 시 마스킹 아님) |
| SQL 원문 추적 | `metadata->>'executed_sql'`에 원문 저장, 제출 시 마스킹 |
| 5W 감사 추적 | `session_index.user_id`(Who), `content`(What), `created_at`(When), `client_ip`(Where), `intent`(Why) |
| 변경 제어 | 앱 계정에 SELECT/INSERT/UPDATE 부여, DELETE는 관리자 계정만. UI 액션(좋아요, 다운로드) UPDATE를 허용하되, 핵심 감사 컬럼(content, client_ip 등)의 변경은 애플리케이션 코드에서 통제 |

### 3.2 `mask_pii()` PostgreSQL 함수

```sql
CREATE OR REPLACE FUNCTION mask_pii(text_input TEXT)
RETURNS TEXT AS $$
BEGIN
    RETURN regexp_replace(
        regexp_replace(
            regexp_replace(
                regexp_replace(text_input,
                    '\d{6}-[1-4]\d{6}', '***-*******', 'g'),        -- 주민번호
                '\d{3,6}-\d{2,6}-\d{4,8}', '***-**-****', 'g'),     -- 계좌번호
            '\d{4}-\d{4}-\d{4}-\d{4}', '****-****-****-****', 'g'),  -- 카드번호
        '01[0-9]-\d{3,4}-\d{4}', '010-****-****', 'g');              -- 전화번호
END;
$$ LANGUAGE plpgsql IMMUTABLE;
```

### 3.3 감사 조회 예시

```sql
-- 특정 사용자의 최근 활동 (PII 마스킹 적용, session_index JOIN)
SELECT t.thread_id, t.turn_seq, t.role,
       mask_pii(t.content) AS content,
       s.user_id, s.user_dept, t.client_ip,
       t.status, t.intent, t.created_at
FROM checkpoint_dc_turn_texts t
JOIN checkpoint_dc_session_index s ON s.thread_id = t.thread_id
WHERE s.user_id = %(user_id)s
  AND t.created_at > now() - interval '30 days'
ORDER BY t.created_at DESC;

-- 실패 건 추적 (PII 마스킹 + 에러 정보)
SELECT t.thread_id, t.turn_seq,
       mask_pii(t.content) AS content,
       t.error_type, t.error_message, t.exit_node, t.model_id
FROM checkpoint_dc_turn_texts t
WHERE t.status = 'failure'
  AND t.created_at > now() - interval '7 days'
ORDER BY t.created_at DESC;

-- 수행 SQL 조회 (PII 마스킹 적용)
SELECT t.thread_id, s.user_id, t.created_at,
       mask_pii(t.metadata->>'executed_sql') AS executed_sql,
       (t.metadata->'sql_result'->>'row_count')::int AS row_count
FROM checkpoint_dc_turn_texts t
JOIN checkpoint_dc_session_index s ON s.thread_id = t.thread_id
WHERE t.role = 'assistant'
  AND t.metadata->>'executed_sql' IS NOT NULL
  AND t.created_at > now() - interval '30 days';
```

### 3.4 보관 정책 (파티션 기반)

| 데이터 | 보관 기간 | 정리 방식 | 근거 |
| ------ | -------- | --------- | ---- |
| `checkpoint_dc_turn_texts` | 5년 | `DROP PARTITION` (월 단위) | 금융거래 기록 보관. DELETE 대비 수십~수백 배 빠르고 VACUUM 불필요 |
| `checkpoints` (체크포인터) | 30~90일 | `DELETE` (배치) | 파이프라인 상태 복구용 (장기 불필요) |
| `checkpoint_dc_session_index` | turn_texts 파티션 삭제 후 고아 정리 | `DELETE WHERE NOT EXISTS` | turn_texts가 없는 세션 제거 |

**파티션 정리 예시:**

```sql
-- 2021년 4월 파티션 삭제 (5년 경과)
DROP TABLE IF EXISTS checkpoint_dc_turn_texts_202104;

-- 고아 session_index 정리 (관리자 계정)
DELETE FROM checkpoint_dc_session_index s
WHERE NOT EXISTS (
    SELECT 1 FROM checkpoint_dc_turn_texts t
    WHERE t.thread_id = s.thread_id
);
```

**파티션 자동 생성:**

향후 파티션은 `pg_partman` 또는 배치 스크립트로 선행 생성한다.
예: 매월 1일에 3개월 후 파티션을 미리 생성.

```sql
-- 배치: 3개월 후 파티션 선행 생성
CREATE TABLE IF NOT EXISTS checkpoint_dc_turn_texts_202607
    PARTITION OF checkpoint_dc_turn_texts
    FOR VALUES FROM ('20260701') TO ('20260801');
```

---

## 4. PII 마스킹 전략

### 4.1 원칙: 저장은 원문, 마스킹은 조회 시

| | 설명 |
|--|------|
| **저장** | `checkpoint_dc_turn_texts`에 원문 그대로 저장 (마스킹 안 함) |
| **LLM 맥락 조회** | 원문 그대로 사용 (마스킹 시 맥락 훼손) |
| **감사 조회** | `mask_pii()` PostgreSQL 함수를 SELECT에서 호출 |
| **접근 제어** | 앱 계정은 SELECT/INSERT만 GRANT, 감사팀은 별도 role |

### 4.2 마스킹 대상 패턴

| 패턴 | 예시 | 마스킹 결과 |
|------|------|------------|
| 주민번호 | `860101-1234567` | `***-*******` |
| 계좌번호 | `110-123-456789` | `***-**-****` |
| 카드번호 | `1234-5678-9012-3456` | `****-****-****-****` |
| 전화번호 | `010-1234-5678` | `010-****-****` |

> `mask_pii()` 함수 DDL은 §3.2 참조

---

## 5. 구현 상세

### 5.1 Phase 1: 체크포인터 기반 대화 이력 (현재 구현 확장)

> **전략문서 Phase 1과 동일 — 이미 대부분 구현 완료.**

#### 현재 완료된 항목 (확인 필요)

| 항목 | 파일 | 상태 |
|------|------|------|
| `CheckpointerConfig` + `DbConnectionInfo` | [config.py](src/config.py) | 구현됨 |
| `create_checkpointer()` async context manager | [checkpointer.py](src/agents/graph/checkpointer.py) | 구현됨 |
| `create_app(checkpointer=)` 주입 | [pipeline.py](src/agents/graph/pipeline.py) | 구현됨 |
| `thread_id = session_id` config 주입 | [runner.py](src/agents/graph/runner.py:121-126) | 구현됨 |
| lifespan checkpointer 초기화 | [main.py](src/main.py:94-98) | 구현됨 |
| interrupt 감지 + `Command(resume=)` | [runner.py](src/agents/graph/runner.py:128-164) | 구현됨 |
| `_collect_src_types()` msgpack allowlist | [checkpointer.py](src/agents/graph/checkpointer.py:104-127) | 구현됨 |

#### Phase 1 잔여 작업

**1-1. DDL 파일 생성** — `resources/connectors/postgres/init_checkpoint_dc_tables_ddl.sql`

> DDL이 없으면 turn_text_store.py가 동작할 수 없으므로 Phase 1에서 먼저 생성한다.

§2.2(turn_texts) + §2.3(session_index) + §3.2(mask_pii 함수)의 DDL을 단일 파일로 생성.

**1-2. 대화 이력 조회/저장 함수** — `src/services/turn_text_store.py` (신규)

`checkpoint_dc_turn_texts` 테이블에 대한 CRUD. §2.2 참조.

> **pool 직접 전달 패턴 채택 근거:**
> 프로젝트의 노드 계층은 `get_connector_manager()` 싱글턴을 내부 호출하지만,
> turn_text_store는 서비스 계층이므로 pool을 파라미터로 받는다.
> 이유: (1) 테스트 시 mock pool 주입 용이, (2) 노드가 아닌 runner/main에서
> 호출되므로 싱글턴 의존도를 낮추는 것이 적절.

##### §2.2.1 원자적 turn_seq 채번

> **이전 설계(기각)**: `get_next_turn_seq()`로 SELECT MAX + 1 → 별도 INSERT.
> 두 문이 별도 커넥션에서 실행되므로, 재시도·타임아웃 후 재요청 시 동일 seq 채번 위험.
>
> **채택**: INSERT 서브쿼리로 단일 SQL 원자 채번. PK `(thread_id, turn_seq, base_ymd)` 제약이
> 최종 안전망 역할 — 만약 충돌 시 DB가 즉시 거부.

```python
"""경량 대화 이력 저장소.

checkpoint_dc_turn_texts 테이블에 턴별 메시지와 UI 복원 데이터를 저장하고,
LLM 맥락 전달 / UI 과거 대화 복원 / 감사 조회에 사용한다.

turn_seq 채번:
    INSERT 서브쿼리로 DB 레벨 원자적 채번 (MAX(turn_seq) + 1).
    SELECT + INSERT 분리 시 race condition 가능 → 단일 SQL로 해결.

실패 버퍼:
    save_turn() 실패 시 인메모리 _pending_turns 버퍼에 보관하고,
    다음 save_turn() 호출 시 함께 저장을 재시도한다.
    서비스 중단 없이 이력 누락을 최소화하는 best-effort 전략.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Json

logger = logging.getLogger(__name__)

# ── 실패 버퍼: 세션별 미저장 턴 보관 ──
_pending_turns: dict[str, list[dict[str, Any]]] = {}


async def save_turn(
    pool: Any,
    *,
    thread_id: str,
    role: str,
    content: str,
    client_ip: str | None = None,
    user_agent: str | None = None,
    turn_type: str = "normal",
    intent: str | None = None,
    token_count: int | None = None,
    latency_ms: int | None = None,
    request_id: str | None = None,
    status: str = "success",
    error_type: str | None = None,
    error_message: str | None = None,
    exit_node: str | None = None,
    model_id: str | None = None,
    trace_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    """턴을 저장한다. turn_seq는 DB 레벨 원자적 채번.

    실패 시 _pending_turns 버퍼에 보관하고 다음 호출 시 재시도.
    """
    turn_data = {
        "thread_id": thread_id, "role": role, "content": content,
        "client_ip": client_ip, "user_agent": user_agent,
        "turn_type": turn_type, "intent": intent,
        "token_count": token_count, "latency_ms": latency_ms,
        "request_id": request_id, "status": status,
        "error_type": error_type, "error_message": error_message,
        "exit_node": exit_node, "model_id": model_id,
        "trace_id": trace_id,
        "metadata": Json(metadata or {}),
    }

    # 이전 실패 턴 + 현재 턴을 모아서 저장
    pending = _pending_turns.pop(thread_id, [])
    pending.append(turn_data)

    saved_count = 0
    try:
        async with pool.connection() as conn:
            for turn in pending:
                await conn.execute(
                    """
                    INSERT INTO checkpoint_dc_turn_texts (
                        thread_id, turn_seq, role, content,
                        client_ip, user_agent,
                        turn_type, intent, token_count, latency_ms,
                        request_id, status, error_type, error_message,
                        exit_node, model_id, trace_id, metadata
                    ) VALUES (
                        %(thread_id)s,
                        COALESCE(
                            (SELECT MAX(turn_seq) + 1
                             FROM checkpoint_dc_turn_texts
                             WHERE thread_id = %(thread_id)s),
                            1
                        ),
                        %(role)s, %(content)s,
                        %(client_ip)s, %(user_agent)s,
                        %(turn_type)s, %(intent)s, %(token_count)s, %(latency_ms)s,
                        %(request_id)s, %(status)s, %(error_type)s, %(error_message)s,
                        %(exit_node)s, %(model_id)s, %(trace_id)s, %(metadata)s
                    )
                    """,
                    turn,
                )
                saved_count += 1
    except Exception:
        # 미저장 턴을 버퍼에 다시 보관 (다음 호출 시 재시도)
        unsaved = pending[saved_count:]
        if unsaved:
            _pending_turns[thread_id] = unsaved
        logger.warning(
            "턴 저장 실패 — %d/%d건 저장, %d건 버퍼 보관",
            saved_count, len(pending), len(unsaved),
            exc_info=True,
        )
        raise  # 호출자의 try/except에서 처리
```

- `get_conversation_history()` — §2.2 "대화 이력 조회" 참조
- `get_session_turns_for_ui()` — §2.2 "UI 과거 대화 복원: 2-tier 로딩" 참조
- `get_turn_metadata()` — §2.2 "Tier 2: 특정 턴의 metadata" 참조

**1-3. Checkpointer pool 공유: 별도 dc_pool 불필요**

> **이전 설계(기각)**: `ConnectorManager._dc_pool`로 별도 `AsyncConnectionPool` 생성.
> 같은 `history_db`에 pool 2개 → 커넥션 낭비, `max_connections` 초과 위험.
>
> **채택**: `create_checkpointer()`가 생성한 pool을 `ConnectorManager`에 주입하여 재사용.
> 체크포인터와 커스텀 SQL 모두 `search_path=bdptbl,public`에 의존하여 스키마를 해결한다.
> 동일 pool, 동일 `search_path` — 테이블명만으로 일관되게 접근.

```python
# src/connectors/manager.py — checkpointer pool 주입

class ConnectorManager:
    # 기존 필드...
    _checkpointer_pool: AsyncConnectionPool | None = None

    def set_checkpointer_pool(self, pool: AsyncConnectionPool) -> None:
        """checkpointer가 생성한 pool을 주입받는다.

        main.py lifespan에서 create_checkpointer() 후 호출.
        turn_text_store 등 커스텀 테이블 접근에 이 pool을 재사용한다.
        """
        self._checkpointer_pool = pool

    @property
    def checkpointer_pool(self) -> AsyncConnectionPool:
        """checkpoint_dc_* 테이블 접근용 pool (checkpointer와 공유)."""
        if self._checkpointer_pool is None:
            raise RuntimeError("checkpointer_pool 미주입 — set_checkpointer_pool() 먼저 호출")
        return self._checkpointer_pool
```

```python
# main.py lifespan — pool 주입 시점
async with create_checkpointer(settings.checkpointer, settings.history_db) as checkpointer:
    manager = get_connector_manager()
    manager.set_checkpointer_pool(checkpointer.pool)   # pool 공유
    await manager.connect_all()

    get_compiled_app(checkpointer=checkpointer)
    yield

    await manager.disconnect_all()
    # checkpointer pool은 create_checkpointer() context manager가 종료 시 정리
```

> **`checkpointer.pool` 접근 가능 여부**: `AsyncPostgresSaver`는 내부에
> `self.conn` (pool 또는 단일 커넥션)을 보유한다. `create_checkpointer()`에서
> pool을 직접 생성하여 `AsyncPostgresSaver.from_conn_string()` 대신
> `AsyncPostgresSaver(conn=pool)` 패턴을 사용하면, pool 참조를 외부에서도 유지할 수 있다.
> 이미 현재 `checkpointer.py`에서 이 패턴을 사용 중이다.

**1-4. runner.py 시그니처 변경 + 턴 저장 호출**

```python
async def run_pipeline(
    user_input: str,
    session_id: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    client_ip: str | None = None,     # 추가
    user_agent: str | None = None,    # 추가
    on_event: OnEventCallback | None = None,
) -> PipelineResult:
```

턴 저장 시점과 흐름:

> **주의**: `app.ainvoke()` 반환값(dict)과 `_build_result()` 반환값(PipelineResult)을
> 별도 변수로 구분해야 한다. 현재 runner.py는 `result`를 재할당하므로
> `raw_state` / `pipeline_result`로 명확히 분리한다.
>
> **방어 원칙**: `save_turn()` 실패가 파이프라인 결과 반환을 막아서는 안 된다.
> 이력 저장은 부가 기능이므로, 실패 시 버퍼에 보관하고 결과는 정상 반환한다.
> 실패한 턴은 다음 `save_turn()` 호출 시 함께 재시도된다 (§2.2.1).

```python
    # ── 정상 완료 시 (§5 끝부분) ──
    raw_state = result                              # dict — ainvoke() 반환값 보존
    pipeline_result = _build_result(handler, raw_state)  # PipelineResult

    # 턴 저장 (실패해도 파이프라인 결과에 영향 없음)
    # user_turn_saved 플래그로 에러 핸들러에서 중복 저장 방지
    user_turn_saved = False
    try:
        from src.services.turn_text_store import save_turn
        manager = get_connector_manager()
        pool = manager.checkpointer_pool

        # user 턴 저장
        await save_turn(
            pool,
            thread_id=session_id, role="user", content=user_input,
            client_ip=client_ip, user_agent=user_agent,
            turn_type="normal", request_id=session_id,
        )
        user_turn_saved = True

        # assistant 턴 저장 (metadata에 UI 복원 데이터)
        reason = raw_state.get("reason")
        await save_turn(
            pool,
            thread_id=session_id, role="assistant", content=pipeline_result.response,
            client_ip=client_ip, user_agent=user_agent,
            turn_type="normal",
            intent=str(raw_state.get("intent", "")),
            latency_ms=int(handler.trace.total_duration_ms),
            request_id=session_id,
            status="success",
            exit_node=handler.trace.node_path[-1] if handler.trace.node_path else None,
            model_id=settings.llm_model,
            trace_id=handler._run_id,  # TODO: public run_id 프로퍼티 추가 권장
            metadata={
                "trace_log": [e.model_dump() for e in pipeline_result.trace_log],
                "insight": pipeline_result.insight,
                "visualization": pipeline_result.visualization.model_dump(),
                "sql_result": {
                    "columns": pipeline_result.sql_result.columns,
                    "row_count": pipeline_result.sql_result.row_count,
                },
                "executed_sql": reason.validated_sql if reason else None,
            },
        )
    except Exception:
        logger.warning("턴 저장 실패 — 버퍼 보관, 파이프라인 결과는 정상 반환", exc_info=True)

    return pipeline_result
```

**명확화(interrupt) 흐름 턴 기록:**

```python
    # ── interrupt 발생 시 (clarification_data is not None) ──

    # assistant 명확화 질문 턴 저장
    await save_turn(
        pool,
        thread_id=session_id, role="assistant", content=question,
        turn_type="clarification",
        status="success", exit_node="readiness_gate",
        metadata={"clarification": clarification_data},
    )

    return PipelineResult(response=question, awaiting_clarification=True, ...)

    # ── interrupt 재개 시 (is_interrupt_pending) ──
    # user 명확화 응답 턴 저장 (재개 전)
    await save_turn(
        pool,
        thread_id=session_id, role="user", content=sanitized.text,
        turn_type="clarification",
    )

    result = await app.ainvoke(Command(resume=sanitized.text), ...)
```

**에러 턴 기록 (user턴 중복 방지 + assistant 에러 턴):**

> `user_turn_saved` 플래그로 정상 경로에서 이미 저장된 user턴을 에러 핸들러에서 다시 저장하지 않는다.
> 파이프라인 초반 에러(user턴 미저장 상태)에서만 user턴을 함께 기록한다.

```python
    # run_pipeline() 최상위 try/except에서
    # user_turn_saved는 정상 경로 try 블록 상단에서 초기화 (위 코드 참조)
    except Exception as e:
        try:
            pool = get_connector_manager().checkpointer_pool
            # user 턴 — 정상 경로에서 아직 미저장인 경우만 기록
            if not user_turn_saved:
                await save_turn(
                    pool,
                    thread_id=session_id, role="user", content=user_input,
                    client_ip=client_ip, user_agent=user_agent,
                    turn_type="error", request_id=session_id,
                )
            # assistant 에러 응답 턴
            await save_turn(
                pool,
                thread_id=session_id, role="assistant",
                content="처리 중 오류가 발생했습니다.",
                turn_type="error", status="failure",
                error_type=type(e).__name__,
                error_message=str(e)[:500],
            )
        except Exception:
            logger.warning("에러 턴 저장 실패", exc_info=True)
        raise
```

**1-5. main.py WebSocket 핸들러에서 SessionStore 대체**

```python
# 기존: SessionStore에서 이력 조회
# history = await store.get_history(session_id)

# 변경: checkpoint_dc_turn_texts에서 이력 조회
from src.services.turn_text_store import get_conversation_history
manager = get_connector_manager()
history = await get_conversation_history(manager.checkpointer_pool, session_id)

# main.py → run_pipeline() 호출 시 WebSocket 요청 정보 전달
# 주의: WebSocket 핸들러이므로 request가 아닌 websocket 객체 사용
result = await run_pipeline(
    user_input, session_id,
    client_ip=websocket.client.host if websocket.client else None,
    user_agent=websocket.headers.get("user-agent"),
)
```

**1-6. session_index title 생성**

세션 제목은 첫 질의의 앞 50자를 자동 사용한다:

```python
title = user_input[:50] + ("..." if len(user_input) > 50 else "")
```

**1-7. 직렬화 라운드트립 테스트 작성**

`tests/unit/test_state_serialization.py` — `02-detailed-design.md` §1.6의 테스트 코드 구현:

- `test_pipeline_state_checkpoint_roundtrip`: PipelineState 체크포인터 왕복
- `test_reasoning_state_serialization`: ReasoningState 중첩 모델 직렬화
- `test_annotated_reducer_with_checkpoint`: `Annotated[list, operator.add]` reducer 동작
- `test_allowed_msgpack_modules`: 커스텀 타입 allowlist 검증

---

### 5.2 Phase 2: seed 스크립트 + dc_pool lifespan 관리

> DDL 파일은 Phase 1(1-1)에서 이미 생성. Phase 2는 개발환경 seed 및 pool 관리를 정비한다.

**2-1. seed_postgres.py에 Data Copilot 테이블 초기화 추가**

```python
# devtools/scripts/seed_postgres.py에 추가

def setup_checkpoint_dc_tables(conn) -> None:
    """Data Copilot 수동 테이블(checkpoint_dc_turn_texts, checkpoint_dc_session_index) + mask_pii() 함수를 초기화한다."""
    ddl_path = RESOURCES_DIR / "connectors" / "postgres" / "init_checkpoint_dc_tables_ddl.sql"
    if ddl_path.exists():
        conn.execute(ddl_path.read_text(encoding="utf-8"))
        logger.info("checkpoint_dc_turn_texts, checkpoint_dc_session_index, mask_pii() 초기화 완료")
```

**2-2. dc_pool lifespan 관리** — ConnectorManager 내부에서 관리

> dc_pool은 Phase 1(1-3)에서 ConnectorManager 내부에 추가한다.
> main.py lifespan에서 별도로 pool을 생성하지 **않는다** (이중 관리 방지).
> `connect_all()` 시 자동 생성, `disconnect_all()` 시 자동 종료.

```python
# main.py lifespan — dc_pool 별도 관리 불필요
async with create_checkpointer(settings.checkpointer, settings.history_db) as checkpointer:
    manager = get_connector_manager()
    await manager.connect_all()  # dc_pool 포함하여 모든 커넥터 연결

    get_compiled_app(checkpointer=checkpointer)
    yield

    await manager.disconnect_all()  # dc_pool 포함하여 모든 커넥터 종료
```

---

### 5.3 Phase 3: SessionStore 점진적 축소

> `01-strategy.md` §2.5: "장기적으로 SessionStore를 경량 SessionIndex로 축소"

#### 현재 SessionStore 메서드 ↔ Phase 3 처리 매핑

<!-- markdownlint-disable MD060 -->

| 메서드 | Phase 3 처리 | 대체 수단 | 비고 |
|---|---|---|---|
| `get_history()` | 3-1에서 제거 | `turn_text_store.get_conversation_history()` | WebSocket + REST 양쪽 |
| `append_history()` | 3-2에서 제거 | `turn_text_store.save_turn()` (runner.py 호출) | WebSocket + REST 양쪽 |
| `get_clarification()` | 3-3에서 완전 제거 | checkpointer interrupt 패턴 | 이미 deprecated |
| `set_clarification()` | 3-3에서 완전 제거 | checkpointer interrupt 패턴 | 이미 deprecated |
| `ensure_session()` | 3-5에서 이관 | `session_index` upsert | runner.py에서 처리 |
| `clear_session()` | 3-5에서 재정의 | 체크포인터 상태만 초기화 (아래 참조) | turn_texts는 보존 |
| `connect()` / `disconnect()` | 3-5에서 제거 | ConnectorManager dc_pool | |
| `health_check()` | 3-5에서 제거 | ConnectorManager health_check_all | |

<!-- markdownlint-enable MD060 -->

#### `/reset` 동작 재정의

> **제약**: turn_texts는 금융 감사용 데이터이므로 DELETE 권한이 없다.
> `/reset` 시 체크포인터 상태(interrupt/resume)만 초기화하고,
> turn_texts 이력은 보존한다. 사용자에게는 "새 대화가 시작됩니다"로 안내.

```python
# Phase 3에서의 /reset 구현
if command == "/reset":
    # 체크포인터 상태 초기화 (새 thread_id 발급)
    new_session_id = str(uuid.uuid4())
    # turn_texts는 보존 — 감사 이력 불변
    await websocket.send_json({
        "type": "system",
        "message": "대화가 초기화되었습니다.",
        "new_session_id": new_session_id,
    })
```

#### `HistoryEntryType` enum 전환

현재 main.py가 `HistoryEntryType.QUERY`, `RESPONSE`, `CLARIFICATION`을 사용한다.
Phase 3에서 `append_history()` 제거 시 이 enum의 사용처도 함께 제거된다.
turn_texts의 `turn_type` 컬럼(`'normal'`, `'clarification'`, `'error'`)이
유사한 역할을 하므로, 별도 Enum 정의 없이 DDL CHECK 제약으로 충분하다.

| 단계 | 작업 | 파일 |
|------|------|------|
| 3-1 | `store.get_history()` → `get_conversation_history()` 전환 | `main.py` (WebSocket `_run_ws_pipeline` + REST `query_endpoint` + `/history` 명령) |
| 3-2 | `store.append_history()` 호출 제거 (save_turn이 대체) | `main.py` (WebSocket `_run_ws_pipeline` + REST `query_endpoint`) |
| 3-3 | `SessionStore`에서 clarify 관련 메서드 제거 | `session/store.py` |
| 3-4 | `conversation_history` 파라미터 제거 | `runner.py` |
| 3-5 | `SessionStore` → 경량 `SessionIndex`로 축소 (세션 존재 확인만) | `session/store.py`, `HistoryEntryType` 제거 |

**3-1 ~ 3-2 구현 상세 (실제 main.py 기준):**

```python
# ── _run_ws_pipeline (변경 전) ──
store = get_session_store()
pipeline_result = await run_pipeline(
    data, session_id,
    conversation_history=await store.get_history(session_id),
    on_event=on_event,
)
# finally 블록에서 user 턴 저장
await store.append_history(session_id, {"role": "user", "content": mask_pii(data), "type": HistoryEntryType.QUERY})
# pipeline 완료 후 assistant 턴 저장
await store.append_history(session_id, {"role": "assistant", "content": masked_response, "type": ...})

# ── _run_ws_pipeline (변경 후) ──
# SessionStore 호출 완전 제거 — save_turn()이 runner.py에서 처리
pipeline_result = await run_pipeline(
    data, session_id,
    client_ip=websocket.client.host if websocket.client else None,
    user_agent=websocket.headers.get("user-agent"),
    on_event=on_event,
)

# ── query_endpoint (변경 전) ──
pipeline_result = await run_pipeline(
    user_input, session_id,
    conversation_history=await store.get_history(session_id),
)
await store.append_history(session_id, {"role": "user", ...})
await store.append_history(session_id, {"role": "assistant", ...})

# ── query_endpoint (변경 후) ──
# REST에서도 동일하게 SessionStore 호출 제거
pipeline_result = await run_pipeline(
    user_input, session_id,
    client_ip=request.client.host if request.client else None,
    user_agent=request.headers.get("user-agent"),
)
```

---

### 5.4 Phase 4: Thread TTL + 체크포인트 정리

> `01-strategy.md` Phase 4, `config.py`의 `thread_ttl_days: int = 30`

**4-1. TTL 정리 스크립트** — `devtools/scripts/cleanup_checkpoints.py`

```python
"""오래된 체크포인트를 정리하는 배치 스크립트.

CheckpointerConfig.thread_ttl_days 기준으로
만료된 thread의 체크포인트를 삭제한다.

사용법:
    python -m devtools.scripts.cleanup_checkpoints
    # 또는 pg_cron으로 매일 실행
"""
import asyncio
from datetime import datetime, timedelta

from src.config import settings


async def cleanup_expired_threads() -> int:
    """만료된 thread의 체크포인트를 삭제한다.

    Returns:
        삭제된 thread 수.
    """
    if settings.checkpointer.thread_ttl_days == 0:
        return 0  # 무제한

    cutoff = datetime.utcnow() - timedelta(days=settings.checkpointer.thread_ttl_days)

    # checkpoint_writes → checkpoint_blobs → checkpoints 순서로 삭제
    # (FK 제약 준수)
    queries = [
        """
        DELETE FROM checkpoint_writes
        WHERE thread_id IN (
            SELECT DISTINCT thread_id FROM checkpoints
            WHERE (checkpoint::jsonb->>'ts')::timestamptz < %(cutoff)s
        )
        """,
        """
        DELETE FROM checkpoint_blobs
        WHERE thread_id IN (
            SELECT DISTINCT thread_id FROM checkpoints
            WHERE (checkpoint::jsonb->>'ts')::timestamptz < %(cutoff)s
        )
        """,
        """
        DELETE FROM checkpoints
        WHERE (checkpoint::jsonb->>'ts')::timestamptz < %(cutoff)s
        """,
    ]
    # ... pool 생성 + 쿼리 실행
```

**4-2. turn_texts 파티션 기반 정리** — DROP PARTITION으로 즉시 삭제

> **이전 설계(기각)**: `DELETE FROM ... WHERE created_at < cutoff`.
> 대용량 테이블에서 DELETE → 대량 WAL, 테이블 잠금, VACUUM 필요.
>
> **채택**: `base_ymd` 월 단위 파티션 → `DROP TABLE` 으로 즉시 삭제.
> DELETE 대비 수십~수백 배 빠르고 VACUUM 불필요. 관리자 계정으로 실행.

```python
async def cleanup_expired_turn_texts(conn: Any) -> list[str]:
    """5년 초과 turn_texts 파티션을 삭제한다.

    Returns:
        삭제된 파티션 테이블명 리스트.
    """
    cutoff_ym = (datetime.utcnow() - timedelta(days=365 * 5)).strftime("%Y%m")

    # 현재 존재하는 파티션 중 만료된 것 조회
    rows = await conn.execute(
        """
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'bdptbl'
          AND tablename LIKE 'checkpoint_dc_turn_texts_%'
          AND substring(tablename FROM '\\d{6}$') < %(cutoff_ym)s
        """,
        {"cutoff_ym": cutoff_ym},
    )
    dropped = []
    for row in rows:
        await conn.execute(f"DROP TABLE IF EXISTS bdptbl.{row['tablename']}")
        dropped.append(row["tablename"])
    return dropped
```

**4-3. session_index 정리** — turn_texts가 없는 세션은 session_index도 제거

```python
async def cleanup_orphan_sessions(conn: Any) -> int:
    """turn_texts가 모두 삭제된 세션의 session_index를 정리한다."""
    result = await conn.execute("""
        DELETE FROM checkpoint_dc_session_index s
        WHERE NOT EXISTS (
            SELECT 1 FROM checkpoint_dc_turn_texts t
            WHERE t.thread_id = s.thread_id
        )
    """)
    return result.rowcount
```

**4-4. pg_cron 등록 (폐쇄망에서 가능한 경우)**

```sql
-- 매일 02:00에 30일 초과 체크포인트 정리
SELECT cron.schedule(
    'cleanup-checkpoints',
    '0 2 * * *',
    $$DELETE FROM checkpoint_writes WHERE thread_id IN (
        SELECT DISTINCT thread_id FROM checkpoints
        WHERE (checkpoint::jsonb->>'ts')::timestamptz < now() - interval '30 days'
    )$$
);

-- 매월 1일 03:00에 만료 파티션 확인 및 삭제 (5년 초과)
-- 주의: 동적 DDL은 pg_cron에서 직접 실행이 제한적이므로,
-- Python 배치 스크립트를 crontab/systemd timer로 실행하는 것을 권장.
-- 대안: pgAgent 또는 PL/pgSQL DO 블록 사용.

-- 매월 1일 04:00에 고아 session_index 정리
SELECT cron.schedule(
    'cleanup-orphan-sessions',
    '0 4 1 * *',
    $$DELETE FROM checkpoint_dc_session_index s
      WHERE NOT EXISTS (
          SELECT 1 FROM checkpoint_dc_turn_texts t
          WHERE t.thread_id = s.thread_id
      )$$
);

-- 매월 1일 01:00에 3개월 후 파티션 선행 생성
SELECT cron.schedule(
    'create-future-partition',
    '0 1 1 * *',
    $$DO $$
    DECLARE
        future_ym TEXT := to_char(now() + interval '3 months', 'YYYYMM');
        next_ym   TEXT := to_char(now() + interval '4 months', 'YYYYMM');
        tbl_name  TEXT := 'checkpoint_dc_turn_texts_' || future_ym;
    BEGIN
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS bdptbl.%I PARTITION OF bdptbl.checkpoint_dc_turn_texts FOR VALUES FROM (%L) TO (%L)',
            tbl_name, future_ym || '01', next_ym || '01'
        );
    END $$$$
);
```

---

## 6. 파일별 변경 매트릭스

| 파일 | Phase | 변경 유형 | 내용 |
| ---- | ----- | --------- | ---- |
| 신규: `init_checkpoint_dc_tables_ddl.sql` | 1 | 생성 | `checkpoint_dc_turn_texts` (파티션) + `checkpoint_dc_session_index` + `mask_pii()` 함수 DDL |
| `checkpointer.py` | 1 | 수정 | `search_path=bdptbl,public` 설정, pool 참조 외부 노출 |
| 신규: `turn_text_store.py` | 1 | 생성 | `save_turn()` (원자적 채번 + 실패 버퍼), `get_conversation_history()`, `get_session_turns_for_ui()`, `get_turn_metadata()`, `toggle_like()`, `mark_downloaded()` |
| `connectors/manager.py` | 1 | 수정 | `set_checkpointer_pool()` / `checkpointer_pool` 프로퍼티 추가 (checkpointer pool 공유) |
| `runner.py` | 1 | 수정 | 시그니처 확장(client_ip 등) + 턴 저장 호출 + 에러 시 user+assistant 함께 기록 |
| `main.py` | 1+2 | 수정 | SessionStore → turn_texts 이력 전환, run_pipeline 호출 시 HTTP 정보 전달, checkpointer pool 주입 |
| `utils/tracker/callback_handler.py` | 1 | 수정 | `run_id` public 프로퍼티 추가 (현재 `_run_id` private만 존재) |
| 신규: `test_state_serialization.py` | 1 | 생성 | 직렬화 라운드트립 테스트 |
| 신규: `routers/sessions.py` | 1 | 생성 | REST API 라우터 (§8 참조) |
| 신규: `services/session_service.py` | 1 | 생성 | 세션/턴 비즈니스 로직 (§8.5 참조) |
| 신규: `models/api/session_models.py` | 1 | 생성 | Pydantic 요청/응답 모델 (§8.6 참조) |
| `seed_postgres.py` | 2 | 수정 | `setup_checkpoint_dc_tables()` + 파티션 자동 생성 추가 |
| `session/store.py` | 3 | 수정 | clarify 메서드 완전 제거, `get_history`/`append_history` 제거, `HistoryEntryType` 제거 → SessionIndex로 축소 |
| `main.py` (슬래시 명령) | 3 | 수정 | `/history` → `get_conversation_history()` 전환, `/reset` → 새 session_id 발급으로 재정의 |
| `main.py` (REST `/api/query`) | 3 | 수정 | `store.get_history()`/`append_history()` 제거, `run_pipeline()` 호출 시 client_ip/user_agent 전달 |
| 신규: `cleanup_checkpoints.py` | 4 | 생성 | Thread TTL 정리 스크립트 (체크포인터 30~90일 DELETE, turn_texts DROP PARTITION, 고아 session_index) |

---

## 7. 구현 순서 및 의존 관계

```
Phase 1 (대화 이력 + 체크포인터)      ← 현재 인프라 대부분 완료, 잔여 작업만
  │  1-1. DDL 파일 생성 (turn_texts 파티션 + session_index + mask_pii)
  │  1-2. turn_text_store.py 구현 (원자적 채번 + 실패 버퍼 + 2-tier 조회)
  │  1-3. Checkpointer pool 공유 (ConnectorManager.set_checkpointer_pool)
  │  1-4. runner.py 시그니처 확장 + 턴 저장 + 에러 시 user턴 함께 기록
  │  1-5. main.py에서 SessionStore → turn_texts 전환 + HTTP 정보 전달
  │  1-6. session_index title 자동 생성
  │  1-7. REST API (routers/sessions.py + session_service.py + session_models.py)
  │  1-8. 직렬화 라운드트립 테스트
  │
  ├──→ Phase 2 (seed + 파티션 관리)    ← Phase 1 완료 후
  │      2-1. seed_postgres.py 확장 (DDL + 파티션 자동 생성)
  │      2-2. (dc_pool 별도 관리 제거 — checkpointer pool 공유)
  │
  ├──→ Phase 3 (SessionStore 축소)    ← Phase 1 안정화 후
  │      3-1~3-2. add_message/get_history 호출 제거
  │      3-3. clarify 메서드 제거
  │      3-4. conversation_history 파라미터 제거
  │      3-5. SessionIndex로 축소
  │
  └──→ Phase 4 (TTL 정리)            ← Phase 1~2 안정화 후
         4-1. 체크포인터 30~90일 DELETE
         4-2. turn_texts 만료 파티션 DROP
         4-3. 고아 session_index 정리
         4-4. pg_cron + 파티션 선행 생성 배치
```

**우선순위**:
- Phase 1: **높음** (기존 코드 대부분 완료, 테스트만 추가)
- Phase 2: **높음** (금융 감사 요건 — 규정 준수 필수)
- Phase 3: **중간** (기능에 영향 없음, 기술 부채 정리)
- Phase 4: **낮음** (운영 안정화 후)

---

## 8. REST API 설계: 세션/턴 관리 엔드포인트

### 8.1 설계 원칙

| 원칙 | 설명 |
| ---- | ---- |
| **계층 분리** | Router(HTTP 변환) → Service(비즈니스 로직) → Store(DB 접근). 각 계층은 하위 계층만 의존 |
| **의미 일관성** | REST 리소스명 = DB 테이블 개념 매핑. `sessions` = `session_index`, `turns` = `turn_texts` |
| **2-tier 로딩** | 턴 목록은 content만(Tier 1), metadata는 개별 요청(Tier 2). §2.2.4 참조 |
| **서비스 무중단** | 이력 API 장애가 파이프라인 실행을 차단하지 않음 |
| **확장성** | 향후 북마크, 공유, 태그 등 UI 액션 추가 시 동일 패턴으로 확장 가능 |

### 8.2 엔드포인트 목록

```
GET    /api/sessions                      → 사용자의 세션 목록
GET    /api/sessions/{session_id}         → 특정 세션의 턴 목록 (Tier 1: content만)
GET    /api/turns/{turn_id}/metadata      → 특정 턴의 metadata (Tier 2: 지연 로드)
PATCH  /api/turns/{turn_id}/like          → 좋아요/싫어요 토글
PATCH  /api/turns/{turn_id}/download      → 다운로드 기록
DELETE /api/sessions/{session_id}         → 세션 아카이브 (soft delete)
```

### 8.3 상세 설계

#### `GET /api/sessions` — 세션 목록 조회

```
Query Params:
  user_id: str (필수, SSO 연동 전 "anonymous")
  limit: int = 20
  offset: int = 0

Response 200:
{
    "sessions": [
        {
            "session_id": "sess-a1b2",
            "title": "이번 달 신규 고객 수 알려줘...",
            "last_active": "2026-04-05T14:30:00+09:00",
            "created_at": "2026-04-05T14:00:00+09:00"
        }
    ],
    "total_count": 42
}
```

#### `GET /api/sessions/{session_id}` — 세션 턴 목록 (Tier 1)

> metadata(SVG, trace_log)를 제외한 경량 턴 데이터만 반환.
> 전체 턴을 한 번에 반환 (페이지네이션 없음 — §2.2.4 근거 참조).

```
Response 200:
{
    "session_id": "sess-a1b2",
    "title": "이번 달 신규 고객 수 알려줘...",
    "turns": [
        {
            "turn_id": "uuid-1234",
            "turn_seq": 1,
            "role": "user",
            "content": "이번 달 신규 고객 수 알려줘",
            "turn_type": "normal",
            "status": "success",
            "is_liked": null,
            "is_downloaded": false,
            "has_metadata": false,
            "created_at": "2026-04-05T14:00:00+09:00"
        },
        {
            "turn_id": "uuid-5678",
            "turn_seq": 2,
            "role": "assistant",
            "content": "📊 신규 고객은 총 1,234명입니다...",
            "turn_type": "normal",
            "status": "success",
            "is_liked": true,
            "is_downloaded": true,
            "has_metadata": true,
            "created_at": "2026-04-05T14:00:05+09:00"
        }
    ]
}
```

> **`has_metadata` 필드**: 프론트엔드가 Tier 2 요청 필요 여부를 판단.
> `metadata != '{}'::jsonb`이면 `true`. user 턴은 보통 `false`.

#### `GET /api/turns/{turn_id}/metadata` — 턴 metadata (Tier 2)

```
Response 200:
{
    "turn_id": "uuid-5678",
    "metadata": {
        "trace_log": [...],
        "insight": {...},
        "visualization": {"svg_code": "<svg>...</svg>", ...},
        "sql_result": {"columns": [...], "row_count": 15},
        "executed_sql": "SELECT ..."
    }
}

Response 404:
{
    "detail": "턴을 찾을 수 없습니다."
}
```

#### `PATCH /api/turns/{turn_id}/like` — 좋아요 토글

```
Request Body:
{
    "is_liked": true    // true=좋아요, false=싫어요, null=취소
}

Response 200:
{
    "turn_id": "uuid-5678",
    "is_liked": true,
    "liked_at": "2026-04-05T14:30:00+09:00"
}
```

#### `PATCH /api/turns/{turn_id}/download` — 다운로드 기록

```
Request Body: (없음)

Response 200:
{
    "turn_id": "uuid-5678",
    "is_downloaded": true,
    "downloaded_at": "2026-04-05T14:35:00+09:00"
}
```

#### `DELETE /api/sessions/{session_id}` — 세션 아카이브

> turn_texts는 감사용이므로 삭제하지 않는다.
> `session_index`에서 soft delete (또는 아카이브 플래그) 처리하여 목록에서 제외.

```
Response 200:
{
    "session_id": "sess-a1b2",
    "archived": true
}
```

> `session_index.is_archived` 플래그(DDL §2.3)로 soft delete 처리.
> `GET /api/sessions` 조회 시 `WHERE is_archived = false` 조건 적용.

### 8.4 이력 누락 시 UX 처리 (§1-3)

이력 저장 실패가 서비스를 중단시키지 않으되, 사용자가 과거 대화를 열 때 적절한 안내가 필요하다.

| 시나리오 | 감지 방법 | UX 처리 |
| -------- | --------- | ------- |
| 전체 정상 | turns 배열 정상 반환 | 그대로 표시 |
| 일부 턴 누락 (user만 있고 assistant 없음) | turn_seq 간격 확인 또는 `role` 연속성 검증 | 해당 턴 위치에 "응답이 기록되지 않았습니다" 안내 표시 |
| 세션 전체 이력 없음 | turns 빈 배열 | "이전 대화를 불러올 수 없습니다. 새 대화를 시작해주세요" 안내 |
| DB 연결 실패 | API 500 에러 | "일시적으로 이전 대화를 불러올 수 없습니다. 잠시 후 다시 시도해주세요" |

**프론트엔드 처리 가이드:**

```typescript
// 턴 목록 조회 후 연속성 검증
function validateTurnContinuity(turns: Turn[]): Turn[] {
    const enriched: Turn[] = [];
    for (const turn of turns) {
        enriched.push(turn);
        // user 턴 다음에 assistant 턴이 없으면 누락 안내 삽입
        if (turn.role === 'user') {
            const nextTurn = turns.find(t => t.turn_seq === turn.turn_seq + 1);
            if (!nextTurn || nextTurn.role !== 'assistant') {
                enriched.push({
                    role: 'system',
                    content: '응답이 기록되지 않았습니다.',
                    turn_type: 'gap',
                });
            }
        }
    }
    return enriched;
}
```

### 8.5 계층 구조 및 파일 매핑

```
src/
├── routers/
│   └── sessions.py          ← Router 계층: HTTP 요청/응답 변환
│                                GET /api/sessions, GET /api/sessions/{id}
│                                GET /api/turns/{id}/metadata
│                                PATCH /api/turns/{id}/like, PATCH /api/turns/{id}/download
│                                DELETE /api/sessions/{id}
│
├── services/
│   ├── turn_text_store.py    ← Store 계층: DB 접근 (save_turn, get_*, toggle_like 등)
│   └── session_service.py    ← Service 계층: 비즈니스 로직 조합
│                                세션 목록 + 턴 조회를 조합하여 API 응답 구성
│                                has_metadata 판정, 연속성 검증 등
│
└── models/
    └── api/
        └── session_models.py ← Pydantic 모델: 요청/응답 스키마
                                 SessionListResponse, SessionDetailResponse,
                                 TurnResponse, TurnMetadataResponse,
                                 LikeRequest, LikeResponse 등
```

**계층별 책임:**

| 계층 | 파일 | 책임 | 의존 |
| ---- | ---- | ---- | ---- |
| Router | `routers/sessions.py` | HTTP 파싱, 인증 확인, 에러 코드 변환 | Service |
| Service | `services/session_service.py` | 비즈니스 로직 조합 (세션 + 턴 조합, has_metadata 판정) | Store |
| Store | `services/turn_text_store.py` | 단일 테이블 CRUD, 원자적 채번, 실패 버퍼 | Pool (DB) |
| Model | `models/api/session_models.py` | 요청/응답 스키마 정의, 검증 | 없음 |

> **Service 계층 도입 근거**: Router가 Store를 직접 호출하면,
> `has_metadata` 판정, 턴 연속성 검증, session_index + turn_texts 조인 등의
> 조합 로직이 Router에 누출된다. Service 계층이 이를 캡슐화하여
> Router는 HTTP 변환만, Store는 DB 접근만 담당한다.

### 8.6 Pydantic 모델 정의

```python
# src/models/api/session_models.py

from pydantic import BaseModel, Field
from datetime import datetime


class SessionSummary(BaseModel):
    """세션 목록의 개별 항목."""
    session_id: str
    title: str | None
    last_active: datetime
    created_at: datetime


class SessionListResponse(BaseModel):
    """GET /api/sessions 응답."""
    sessions: list[SessionSummary]
    total_count: int


class TurnSummary(BaseModel):
    """세션 상세의 개별 턴 (Tier 1 — metadata 제외)."""
    turn_id: str
    turn_seq: int
    role: str
    content: str
    turn_type: str
    status: str
    is_liked: bool | None = None
    is_downloaded: bool = False
    has_metadata: bool = False
    created_at: datetime


class SessionDetailResponse(BaseModel):
    """GET /api/sessions/{session_id} 응답."""
    session_id: str
    title: str | None
    turns: list[TurnSummary]


class TurnMetadataResponse(BaseModel):
    """GET /api/turns/{turn_id}/metadata 응답 (Tier 2)."""
    turn_id: str
    metadata: dict


class LikeRequest(BaseModel):
    """PATCH /api/turns/{turn_id}/like 요청."""
    is_liked: bool | None = Field(
        ..., description="true=좋아요, false=싫어요, null=취소",
    )


class LikeResponse(BaseModel):
    """PATCH /api/turns/{turn_id}/like 응답."""
    turn_id: str
    is_liked: bool | None
    liked_at: datetime | None


class DownloadResponse(BaseModel):
    """PATCH /api/turns/{turn_id}/download 응답."""
    turn_id: str
    is_downloaded: bool
    downloaded_at: datetime | None
```

---

## 9. 전략 문서와의 정합성

> 이전 §8 → §9로 이동. REST API 설계(§8) 추가에 따른 번호 조정.

| 전략 문서 결정 | 이 문서의 구현 | 비고 |
|---------------|---------------|------|
| §2.1 "history_db에 체크포인트 공존" | `bdptbl` 스키마 통일, 커스텀 테이블은 `checkpoint_dc_` prefix | 별도 DB/스키마 불필요 |
| §2.5 "Checkpointer = 단일 진실 공급원" | 파이프라인 상태는 체크포인터, 대화 텍스트는 `checkpoint_dc_turn_texts` | `get_state_history()` 성능 문제로 경량 TEXT 테이블 분리 |
| §2.5 "SessionStore → 경량 SessionIndex로 축소" | Phase 3에서 점진적 축소 | clarify 상태는 체크포인터가 대체 |
| Phase 1 Core Checkpointer | §5.1 — 이미 구현 완료 | 테스트만 잔여 |
| Phase 2 Unified Clarification | interrupt/resume 이력이 체크포인터에 자동 저장 | 별도 이력 테이블 불필요 |
| Phase 3 세션 관리 통합 | SessionStore.get_history() → turn_texts 전환 | §5.3과 동기화 |
| Phase 4 Thread TTL | cleanup_checkpoints.py | thread_ttl_days=30 설정 사용 |

---

## 10. 검증 계획

### 10.1 Phase 1 검증

| 테스트 | 검증 내용 |
|--------|----------|
| `test_pipeline_state_checkpoint_roundtrip` | PipelineState 체크포인터 직렬화/역직렬화 |
| `test_save_turn_and_get_history` | `save_turn()` 후 `get_conversation_history()`가 정확한 이력 반환 |
| `test_get_session_turns_for_ui` | UI 복원 조회 시 metadata(trace_log, visualization, insight, executed_sql) 포함 확인 |
| `test_interrupt_resume_history` | interrupt → resume 후 이력에 명확화 Q&A 포함 (turn_type='clarification') |
| `test_multi_turn_history` | 다중턴 대화 후 전체 이력 순서 정확성 |
| `test_turn_texts_metadata_structure` | assistant 턴의 metadata가 PipelineResult 필드와 정확히 매핑되는지 검증 |
| `test_turn_texts_error_tracking` | 실패 턴의 status, error_type, error_message, exit_node 기록 검증 |

### 10.2 Phase 2 검증

| 테스트 | 검증 내용 |
|--------|----------|
| `test_mask_pii_function` | PostgreSQL `mask_pii()` 함수가 주민번호/계좌/카드/전화번호 패턴 마스킹 |
| `test_audit_query_with_mask_pii` | 감사 조회 시 `mask_pii(content)` 적용 결과 정확성 |
| `test_executed_sql_in_metadata` | 성공 턴의 `metadata->>'executed_sql'`에 SQL 원문 저장 확인 |
| `test_audit_5w_fields` | user_id, client_ip, user_agent, intent, created_at 기록 확인 |

---

## 11. 코드 리뷰 결과 반영 사항

> 리뷰 보고서: `docs/reviews/code/20260405-postgres-conversation-history-design-review-report.md`
> 1차 반영(2026-04-05) + 2차 비판적 재검토 결과를 통합 정리.

<!-- markdownlint-disable MD060 -->

### 11.1 설계문서 반영 완료

| ID | 이슈 | 반영 내용 | 반영 위치 |
|----|------|----------|----------|
| R-01~03 | `handler.elapsed_ms`, `last_node_name`, `run_id` 미존재 | 실제 API인 `handler.trace.total_duration_ms`, `handler.trace.node_path[-1]`, `handler._run_id`로 수정. `run_id` public 프로퍼티 추가를 매트릭스에 반영. | §5.1 1-4, §6 |
| R-04 | `settings.llm.model_id` 미존재 | `settings.llm_model`(flat 필드)로 수정 | §5.1 1-4 |
| Y-05 | `result` 변수 섀도잉 | `raw_state` / `pipeline_result`로 분리, 주의 블록 추가 | §5.1 1-4 |
| Y-06 | dc_pool 이중 관리 | lifespan 별도 생성 제거, ConnectorManager 내부 관리로 통일 | §5.2 2-2 |
| Y-07 | Phase 3 변경 전/후 코드가 실제 main.py와 불일치 | 실제 main.py 코드 기준으로 WebSocket(`_run_ws_pipeline`) + REST(`query_endpoint`) 양쪽 재작성 | §5.3 |
| Y-08 | WebSocket에서 `request` 객체 접근 불가 | `websocket.client.host`, `websocket.headers.get("user-agent")`로 수정, `current_user.id` → `"anonymous"` | §5.1 1-5 |
| Y-13 | `save_turn(pool, ...)` 패턴이 프로젝트 패턴과 불일치 | pool 직접 전달 유지 결정, 설계 근거(테스트 용이성, DI)를 문서에 명시 | §5.1 1-2 |
| Y-14 | `save_turn()` 실패 시 파이프라인 결과 반환 차단 위험 | `try/except` 방어 패턴 + 방어 원칙 블록 추가 | §5.1 1-4 |
| Y-15 | 변경 매트릭스 누락 파일 | `callback_handler.py`, `/history` 명령, REST `/api/query` 추가 | §6 |
| Y-17 | Phase 3 SessionStore 축소 범위 불명확 | 메서드별 처리/대체 수단 매핑 테이블 추가, `/reset` 동작 재정의(새 session_id 발급, turn_texts 보존) | §5.3 |
| Y-18 | `/history` 슬래시 명령 전환 미언급 | Phase 3-1 작업 범위에 명시, §6 매트릭스에 추가 | §5.3, §6 |
| G-16 | REST `/api/query` 엔드포인트 전환 누락 | Phase 3 변경 전/후 코드에 REST 경로 추가, §6 매트릭스에 추가 | §5.3, §6 |
| G-19 | `HistoryEntryType` enum 전환 계획 없음 | Phase 3-5에서 enum 제거 명시, DDL CHECK 제약으로 충분함을 기술 | §5.3, §6 |

### 11.2 미반영 (구현 시 대응)

| ID | 이슈 | 미반영 사유 |
|----|------|-----------|
| G-09 | `current_user.id` 인증 미구현 | 인증 체계는 이 설계의 범위 밖. Phase 1에서는 `"anonymous"` 고정으로 충분. SSO 연동은 별도 작업으로 분리. |
| Y-10 | DDL `turn_id`와 PipelineState `turn_id` 관계 | 의도적 분리. DB `turn_id`는 외부 참조용 자동 채번, PipelineState `turn_id`는 파이프라인 내부 추적용. 교차 참조가 필요하면 `request_id`(=session_id) 사용. 설계 변경 불필요. |
| Y-11 | metadata 운영 메트릭(`retry_count`, `node_durations_ms`) 추출 방법 미정의 | 핸들러 API가 아직 제공하지 않는 데이터. Phase 1에서는 확실히 채울 수 있는 필드만 저장, 운영 메트릭은 핸들러 확장 후 점진적으로 채움. 현시점에서 코드 예시를 강제하면 허구가 됨. |
| G-12 | Phase 번호와 단계 번호 일관성 | 이슈 없음 확인. 조치 불필요. |
| Y-15 일부 | `session/__init__.py`, `models/enums.py`, `.env.example` 누락 | `session/__init__.py`는 SessionStore 축소(Phase 3) 시 자연스럽게 변경되므로 별도 기재 불필요. `models/enums.py`에 turn_type Enum 추가는 DDL CHECK 제약으로 충분하여 불필요. `.env.example`은 `history_db` 설정을 공유하므로 추가 환경변수 없음. |

### 11.3 미반영 항목 상세 참고

아래 항목들은 설계 변경이 불필요하다고 판단하여 미반영하였으나,
구현 시 참고할 수 있도록 배경과 대응 방향을 기록한다.

#### G-09. 인증 체계 미구현 (`current_user.id`)

리뷰에서는 설계문서가 `current_user.id`를 참조하지만 현재 코드에 인증 체계가
없다고 지적했다. 지적 자체는 정확하나, 인증 체계 구현은 대화 이력 설계의 범위 밖이다.

**현재 대응:**

- DDL의 `user_id` 컬럼은 `DEFAULT 'anonymous'`로 정의
- `save_turn()`의 `user_id` 파라미터도 기본값 `"anonymous"`
- §5.1 1-5에서 `user_id="anonymous"` 고정으로 이미 수정 완료

**향후 SSO 연동 시:**

- main.py에서 인증 미들웨어가 `current_user` 객체를 제공하면
  `run_pipeline(user_id=current_user.id, user_dept=current_user.dept)`로 전달
- `save_turn()`과 DDL은 변경 불필요 — 파라미터만 바꾸면 됨

#### Y-10. DDL `turn_id`와 PipelineState `turn_id`의 관계

DDL의 `turn_id UUID DEFAULT gen_random_uuid()`와 PipelineState의
`turn_id: str = Field(default_factory=lambda: str(uuid4()))`가
별개의 값을 가지는 점이 지적되었다. 이는 의도적 분리이다.

| turn_id | 용도 | 생성 시점 | 생성 주체 |
|---------|------|----------|----------|
| DDL `turn_id` | 외부 참조용 고유 식별자 (API 응답, 감사 보고서) | INSERT 시 | PostgreSQL |
| PipelineState `turn_id` | 파이프라인 내부 실행 추적 (체크포인터 상태 식별) | `run_pipeline()` 시작 시 | runner.py |

**교차 참조가 필요한 경우:**

- `request_id` 컬럼(= session_id)으로 연결: turn_texts의 `request_id`와
  체크포인터의 `thread_id`가 동일한 session_id를 가리킴
- PipelineState의 `turn_id`를 turn_texts에도 저장하고 싶다면,
  `save_turn()`에 `pipeline_turn_id` 파라미터를 추가하고
  `metadata` JSONB에 포함하면 됨 (DDL 변경 불필요)

#### Y-11. metadata 운영 메트릭 추출 방법

metadata JSONB에 정의된 운영 메트릭 중 일부는 현재 핸들러 API가 제공하지 않는다.

| metadata 키 | 추출 가능 | 데이터 출처 |
|-------------|----------|-----------|
| `trace_log` | O | `PipelineResult.trace_log` |
| `insight` | O | `PipelineResult.insight` |
| `visualization` | O | `PipelineResult.visualization` |
| `sql_result` | O | `PipelineResult.sql_result` |
| `executed_sql` | O | `raw_state.get("reason").validated_sql` |
| `clarification` | O | `clarification_data` (interrupt 페이로드) |
| `retry_count` | X | `handler.trace.sql.retry_count` 확장 필요 |
| `context_sources_hit` | X | 핸들러 context_retrieval 기록 가공 필요 |
| `node_durations_ms` | X | `handler.trace.nodes` 가공 필요 |
| `validation_errors` | X | `handler.trace.sql.validation_errors` 확장 필요 |

**Phase 1 대응:**

- O 표시 항목만 저장, X 항목은 빈 값(미포함) 허용
- JSONB이므로 키가 없어도 기존 행에 영향 없음
- 핸들러 API 확장 후 `save_turn()` 호출부에서 점진적으로 채움

**핸들러 확장 방향 (참고):**

```python
# callback_handler.py에 추가 프로퍼티 예시
@property
def retry_count(self) -> int:
    return self._trace.sql.retry_count if self._trace.sql else 0

@property
def node_durations(self) -> dict[str, float]:
    return {n.node_name: n.duration_ms for n in self._trace.nodes}
```

#### Y-15 일부. 변경 매트릭스 추가 파일 검토 결과

리뷰에서 `session/__init__.py`, `models/enums.py`, `.env.example` 3개 파일이
변경 매트릭스에 누락되었다고 지적했으나, 각각 불필요하다고 판단했다.

| 파일 | 미반영 근거 |
|------|-----------|
| `session/__init__.py` | `get_session_store` 팩토리는 Phase 3에서 SessionStore를 SessionIndex로 축소할 때 자연스럽게 변경됨. Phase 3 자체가 §5.3에 충분히 기술되어 있으므로 별도 매트릭스 항목 불필요. |
| `models/enums.py` | `turn_type`과 `status`를 Python Enum으로 정의하는 것은 DDL의 CHECK 제약과 이중 관리. 문자열 상수로 충분하며, Enum이 필요해지면 그때 추가해도 DDL/코드 변경 없음. |
| `.env.example` | dc_pool은 기존 `history_db` 설정(DSN, password)을 그대로 공유. pool_min/pool_max는 ConnectorManager 내부에 하드코딩(min=1, max=5)하며, 설정 외부화가 필요해지면 `CheckpointerConfig`에 필드 추가. |

<!-- markdownlint-enable MD060 -->
