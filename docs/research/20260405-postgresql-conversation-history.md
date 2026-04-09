# PostgreSQL 기반 대화 이력 관리 설계 리서치

**날짜**: 2026-04-05
**대상 프로젝트**: Data Copilot (NL-to-SQL 은행 AI 에이전트)
**리서치 범위**: LangGraph 체크포인터 vs 별도 대화 이력 저장소 설계

---

## 요약 (Executive Summary)

**핵심 결론**: AsyncPostgresSaver(체크포인터)만으로는 금융권 감사 추적 요건을 충족할 수 없다. 체크포인터는 파이프라인 실행 상태 복원을 위한 바이너리 스냅샷이고, 대화 이력은 사람이 읽을 수 있는(human-readable) SQL 쿼리 가능한 별도 테이블로 관리해야 한다.

**권고 아키텍처**: 하이브리드 2-계층 패턴
- **Layer 1**: AsyncPostgresSaver — 파이프라인 상태 복원, interrupt/resume (현행 유지)
- **Layer 2**: `conversation_turns` + `conversation_sessions` 테이블 — 감사 추적, 이력 조회, 분석

---

## 1. LangGraph 공식 대화 이력 관리 패턴

### 1.1 체크포인터의 역할

LangGraph 공식 문서에 따르면, 체크포인터는 두 가지 메모리 계층 중 단기 메모리(short-term memory)를 담당한다.

- **Checkpointer (단기 메모리)**: 단일 thread 범위 내 대화 컨텍스트를 유지. 파이프라인 실행 중간 상태 저장 및 복원.
- **Store (장기 메모리)**: 여러 thread 간(cross-thread) 정보 공유. 사용자 선호, 장기 지식 저장.

```
Checkpointer  →  thread_id 범위 내 (동일 대화)
Store         →  thread_id 경계를 초월 (사용자 간, 세션 간)
```

출처: [Memory overview - Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/memory), [Launching Long-Term Memory Support in LangGraph](https://blog.langchain.com/launching-long-term-memory-support-in-langgraph/)

### 1.2 `get_state()` / `get_state_history()` API

LangGraph는 체크포인터에 저장된 상태를 조회하는 API를 제공한다.

```python
config = {"configurable": {"thread_id": "user_session_123"}}

# 최신 상태 조회
snapshot = await graph.aget_state(config)
# snapshot.values["messages"] → 현재 메시지 목록
# snapshot.created_at         → 마지막 체크포인트 타임스탬프

# 전체 이력 조회 (최신 → 오래된 순)
async for state in graph.aget_state_history(config):
    print(state.values, state.metadata["step"])
```

**StateSnapshot 주요 필드**:
| 필드 | 타입 | 설명 |
|------|------|------|
| `values` | dict | 해당 체크포인트 시점의 State 전체 |
| `next` | tuple | 다음 실행 예정 노드 (빈 tuple = 완료) |
| `config` | dict | thread_id, checkpoint_id 포함 |
| `metadata` | dict | step 번호, 실행 소스 |
| `created_at` | str | ISO 8601 타임스탬프 |

출처: [Persistence - Docs by LangChain](https://docs.langchain.com/oss/python/langgraph/persistence)

**중요 한계**: `get_state_history()`는 대화 이력 "조회"는 가능하지만, 역직렬화 비용이 크다. 운영 환경에서 장기 thread의 전체 이력을 로딩하면 최대 4초 지연이 발생한 사례가 보고됨.

출처: [Internals of Langgraph Postgres Checkpointer](https://blog.lordpatil.com/posts/langgraph-postgres-checkpointer/)

### 1.3 Store (Cross-Thread Memory) — 대화 이력 적용 가능성

LangGraph Store는 JSON document store로, namespace + key 계층으로 데이터를 조직화한다.

```python
store.put(("user_profiles", user_id), "preferences", {"language": "ko"})
store.search(("user_profiles",), query="language preferences")
```

**평가**: Store는 사용자 선호·장기 지식 저장에 적합하다. 그러나 시계열 대화 이력(언제 무슨 말을 했는지)보다는 구조화된 지식 저장에 최적화되어 있다. 금융권 감사 추적(audit trail) 목적에는 적합하지 않다.

---

## 2. LangChain 공식 대화 이력 관리 패턴

### 2.1 `PostgresChatMessageHistory` DDL 분석

`langchain-postgres` 패키지의 `PostgresChatMessageHistory` 공식 구현체는 다음 테이블 구조를 사용한다.

```sql
-- PostgresChatMessageHistory가 내부적으로 생성하는 테이블 구조
CREATE TABLE {table_name} (
    id        BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    message   JSONB NOT NULL,           -- {type, data: {content, ...}}
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON {table_name} (session_id);
```

핵심 특징:
- `message` 컬럼이 JSONB → SQL로 직접 내용 조회 가능
- `session_id`로 세션 구분
- `id` 단조 증가(monotonically increasing)로 메시지 순서 보장
- `created_at`은 인터페이스에서 반환하지 않지만 DB에는 저장됨

출처: [PostgresChatMessageHistory API Reference](https://python.langchain.com/api_reference/postgres/chat_message_histories/langchain_postgres.chat_message_histories.PostgresChatMessageHistory.html), [Source code for langchain_postgres.chat_message_histories](https://api.python.langchain.com/en/latest/_modules/langchain_postgres/chat_message_histories.html)

### 2.2 `langgraph-checkpoint-postgres` vs `langchain-postgres` 역할 구분

| 패키지 | 역할 | 저장 형식 | SQL 조회 가능? |
|--------|------|-----------|----------------|
| `langgraph-checkpoint-postgres` | 파이프라인 실행 상태 저장/복원 | BYTEA(msgpack+JsonPlus) | 불가능 (binary) |
| `langchain-postgres` | 대화 메시지 이력 저장 | JSONB | 가능 |

**공식 입장**: LangChain 공식 문서는 두 컴포넌트를 별도로 분류한다. Checkpointer는 "state persistence"(상태 영속화), ChatMessageHistory는 "memory"(메모리 관리) 카테고리로 각각 다른 목적을 명시하고 있다.

출처: [langchain-postgres GitHub README](https://github.com/langchain-ai/langchain-postgres/blob/main/README.md), [Postgres | LangChain Docs](https://python.langchain.com/docs/integrations/memory/postgres_chat_message_history/)

### 2.3 LangGraph 환경에서 `PostgresChatMessageHistory` 사용 전략

LangGraph + ChatMessageHistory를 함께 사용하는 경우, 노드 내에서 명시적으로 이력을 기록하는 패턴이 일반적이다.

```python
from langchain_postgres import PostgresChatMessageHistory

async def record_turn_node(state: State, config: RunnableConfig) -> dict:
    thread_id = config["configurable"]["thread_id"]
    history = PostgresChatMessageHistory(
        table_name="conversation_turns",
        session_id=thread_id,
        async_connection=get_db_conn(),
    )
    await history.aadd_messages([
        HumanMessage(content=state["user_query"]),
        AIMessage(content=state["final_answer"]),
    ])
    return {}
```

**주의**: LangGraph에서는 checkpointer가 이미 메시지를 관리하므로, ChatMessageHistory와 중복 저장이 발생할 수 있다. 목적을 명확히 구분해야 한다.

---

## 3. PostgreSQL 대화 이력 테이블 설계 Best Practice

### 3.1 체크포인터 저장 방식의 근본적 한계

AsyncPostgresSaver의 `checkpoint_blobs` 테이블은 Python의 `msgpack + JsonPlusSerializer`로 직렬화된 바이너리를 BYTEA로 저장한다. 이 형식은:

1. **SQL로 조회 불가**: `SELECT * FROM checkpoint_blobs WHERE content LIKE '%대출%'` 불가능
2. **역직렬화 비용**: 긴 대화 이력을 Python 객체로 복원하면 최대 4초 지연 발생
3. **감사 불투명성**: DBA나 감사팀이 직접 내용을 확인할 수 없음
4. **파티셔닝 비효율**: 비즈니스 의미 없는 binary blob은 파티셔닝 혜택 제한적

출처: [Internals of Langgraph Postgres Checkpointer](https://blog.lordpatil.com/posts/langgraph-postgres-checkpointer/), [Checkpoint Implementations - DeepWiki](https://deepwiki.com/langchain-ai/langgraph/4.2-checkpoint-implementations)

### 3.2 권장 스키마 설계

Data Copilot 금융권 요건을 반영한 전체 스키마:

```sql
-- ============================================================
-- 스키마 분리: 운영 테이블과 감사 테이블 격리
-- ============================================================
CREATE SCHEMA IF NOT EXISTS copilot;
CREATE SCHEMA IF NOT EXISTS audit;

-- ============================================================
-- 세션 테이블: 대화 세션 메타데이터
-- ============================================================
CREATE TABLE copilot.conversation_sessions (
    session_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id       TEXT NOT NULL UNIQUE,   -- LangGraph thread_id와 1:1 매핑
    user_id         TEXT NOT NULL,
    user_dept       TEXT,                   -- 부서 (금융 도메인 감사 필요)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    turn_count      INT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived', 'deleted')),
    metadata        JSONB DEFAULT '{}'
);

CREATE INDEX idx_sessions_user_id   ON copilot.conversation_sessions (user_id);
CREATE INDEX idx_sessions_thread_id ON copilot.conversation_sessions (thread_id);
CREATE INDEX idx_sessions_created   ON copilot.conversation_sessions (created_at DESC);

-- ============================================================
-- 대화 턴 테이블: 실제 메시지 이력
-- 월별 파티셔닝 (대규모 운영 대비)
-- ============================================================
CREATE TABLE copilot.conversation_turns (
    turn_id         BIGSERIAL,
    session_id      UUID NOT NULL REFERENCES copilot.conversation_sessions(session_id),
    thread_id       TEXT NOT NULL,
    turn_seq        INT NOT NULL,           -- 세션 내 순서 (1, 2, 3...)
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,          -- 실제 메시지 내용 (PII 마스킹 후)
    content_masked  BOOLEAN NOT NULL DEFAULT FALSE,  -- PII 마스킹 여부 플래그
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- SQL 생성 관련 메타 (NL-to-SQL 특화)
    generated_sql   TEXT,                   -- 생성된 SQL (있는 경우)
    sql_masked      BOOLEAN DEFAULT FALSE,  -- SQL 내 PII 마스킹 여부
    execution_ms    INT,                    -- SQL 실행 시간 (ms)
    result_rows     INT,                    -- 반환 행 수
    -- 파이프라인 연결 메타
    checkpoint_id   TEXT,                   -- LangGraph checkpoint_id 참조
    pipeline_status TEXT,                   -- 파이프라인 최종 상태
    failure_type    TEXT,                   -- 실패 유형 (있는 경우)
    metadata        JSONB DEFAULT '{}'
) PARTITION BY RANGE (created_at);

-- 파티션 생성 (월별)
CREATE TABLE copilot.conversation_turns_2026_04
    PARTITION OF copilot.conversation_turns
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

CREATE TABLE copilot.conversation_turns_2026_05
    PARTITION OF copilot.conversation_turns
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

-- 파티션별 인덱스 (파티션 pruning 작동)
CREATE INDEX ON copilot.conversation_turns (session_id, turn_seq);
CREATE INDEX ON copilot.conversation_turns (thread_id);
CREATE INDEX ON copilot.conversation_turns (created_at DESC);

-- ============================================================
-- 감사 이력 테이블: 금융권 컴플라이언스 필수
-- 불변(immutable) — 수정/삭제 금지, 별도 파티셔닝
-- ============================================================
CREATE TABLE audit.agent_actions (
    action_id       BIGSERIAL,
    session_id      UUID,
    thread_id       TEXT,
    user_id         TEXT NOT NULL,
    action_type     TEXT NOT NULL,  -- 'query', 'sql_execute', 'clarification', 'cancel'
    action_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 금융 감사 필수 항목
    client_ip       INET,
    user_agent      TEXT,
    request_id      TEXT,           -- 요청 추적용 UUID
    -- 요청/응답 개요 (PII 제거된 버전)
    query_summary   TEXT,           -- 원본 질의 요약 (PII 마스킹)
    tables_accessed TEXT[],         -- 접근한 테이블 목록
    sql_hash        TEXT,           -- 실행 SQL의 SHA-256 해시 (내용 대신)
    row_count       INT,            -- 반환 행 수
    exec_ms         INT,
    -- 결과 상태
    status          TEXT NOT NULL,  -- 'success', 'failure', 'cancelled', 'clarification'
    failure_reason  TEXT,
    metadata        JSONB DEFAULT '{}'
) PARTITION BY RANGE (action_at);

CREATE TABLE audit.agent_actions_2026_04
    PARTITION OF audit.agent_actions
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

CREATE INDEX ON audit.agent_actions (user_id, action_at DESC);
CREATE INDEX ON audit.agent_actions (session_id);
CREATE INDEX ON audit.agent_actions (action_type, action_at DESC);

-- 감사 테이블 수정 방지 (Row-Level Security)
ALTER TABLE audit.agent_actions ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_insert_only ON audit.agent_actions
    FOR INSERT WITH CHECK (true);
-- SELECT는 감사팀 role에만 허용 (별도 GRANT 정책)
```

출처: [Production-Ready Audit Logs in PostgreSQL](https://medium.com/@sehban.alam/lets-build-production-ready-audit-logs-in-postgresql-7125481713d8), [PostgreSQL Table Partitioning](https://www.postgresql.org/docs/current/ddl-partitioning.html)

### 3.3 pg_partman을 활용한 파티션 자동화

수동 파티션 생성은 운영 부담이 크다. `pg_partman` 확장을 사용하면 월별 파티션 자동 생성 및 보관 처리를 자동화할 수 있다.

```sql
-- pg_partman 설정 예시 (월별 자동 파티셔닝)
SELECT partman.create_parent(
    p_parent_table := 'copilot.conversation_turns',
    p_control      := 'created_at',
    p_interval     := '1 month',
    p_premake      := 3  -- 3개월 분량 파티션 미리 생성
);

-- 보관 정책: 12개월 이후 파티션 아카이브
UPDATE partman.part_config
SET retention = '12 months', retention_keep_table = TRUE
WHERE parent_table = 'copilot.conversation_turns';
```

출처: [Time Partitioning with pg_partman](https://www.crunchydata.com/blog/time-partitioning-and-custom-time-intervals-in-postgres-with-pg_partman)

---

## 4. Checkpointer와 대화 이력의 관계 정리

### 4.1 이진 분류: 충분한 경우 vs 별도 테이블 필요

| 기준 | Checkpointer만으로 충분 | 별도 테이블 필요 |
|------|------------------------|-----------------|
| 다중턴 대화 유지 | O | |
| 파이프라인 interrupt/resume | O | |
| 특정 checkpoint로 time travel | O | |
| SQL로 대화 내용 검색 | | O |
| 감사팀이 직접 조회 | | O |
| 사용자별 이력 목록 UI | | O |
| 세션 통계/분석 | | O |
| PII 마스킹 이력 저장 | | O |
| 규정 준수 장기 보관 | | O |
| 서버 재시작 후 이력 유실 방지 | O | |

**결론**: 금융권 AI 에이전트에서 Checkpointer만으로 충분한 경우는 "개발/테스트 환경" 또는 "audit 요건이 없는 비규제 서비스"에 한정된다.

### 4.2 Checkpointer 저장 데이터의 불투명성 문제

현재 프로젝트의 `checkpointer.py`에서 `JsonPlusSerializer`를 사용하고 있으나, 이는 Python 객체를 msgpack으로 직렬화한 후 BYTEA로 저장한다. 메시지 리스트(`List[BaseMessage]`)는 `checkpoint_blobs` 테이블에 바이너리로 저장되어 SQL로 직접 조회가 불가능하다.

```sql
-- 현재 checkpointer 테이블에서는 이런 쿼리가 불가능함
SELECT * FROM checkpoint_blobs WHERE content::text LIKE '%대출 한도%';
-- → BYTEA 컬럼이므로 SQL 조회 불가
```

출처: [Checkpoint Implementations - DeepWiki](https://deepwiki.com/langchain-ai/langgraph/4.2-checkpoint-implementations)

### 4.3 하이브리드 패턴: Checkpointer + 별도 이력 테이블

생산 참조 구현에서 확인된 패턴은 다음과 같다. LangGraph 체크포인터로 실행 상태를 관리하고, 별도 경량 테이블로 UI 및 감사 목적의 이력을 관리한다.

```
[LangGraph Graph]
       |
       | 매 super-step마다 자동 저장
       v
[AsyncPostgresSaver]           [별도 기록 로직]
checkpoints                     conversation_sessions
checkpoint_blobs         ←→     conversation_turns
checkpoint_writes               audit.agent_actions
   (binary, 불투명)              (JSONB, human-readable)
```

핵심 원칙: **"what the model needs" vs "what the UI/audit needs"** 분리.

출처: [LangGraph Customizing Memory](https://focused.io/lab/customizing-memory-in-langgraph-agents-for-better-conversations), [Mastering LangGraph Checkpointing 2025](https://sparkco.ai/blog/mastering-langgraph-checkpointing-best-practices-for-2025)

---

## 5. 금융/엔터프라이즈 환경 특화 고려사항

### 5.1 감사 추적 (Audit Trail) 설계 원칙

금융기관 IT 컴플라이언스에서 감사 추적은 다음 5W를 기록해야 한다:
- **Who**: 사용자 ID, 부서, IP
- **What**: 요청 내용 요약, 접근 테이블, SQL 해시
- **When**: 정밀 타임스탬프 (TIMESTAMPTZ)
- **Where**: 클라이언트 IP, 접속 경로
- **Why**: 결과 상태, 실패 사유

**불변성 보장**: 감사 테이블은 INSERT만 허용하고 UPDATE/DELETE를 Row-Level Security로 차단해야 한다. PostgreSQL 트리거 기반 접근 방식은 애플리케이션 레이어 우회를 방지한다.

출처: [Production-Ready Audit Logs in PostgreSQL](https://medium.com/@sehban.alam/lets-build-production-ready-audit-logs-in-postgresql-7125481713d8), [PostgreSQL Audit Logging Best Practices](https://severalnines.com/blog/postgresql-audit-logging-best-practices/)

### 5.2 PII 마스킹 전략

은행 서비스에서 대화 이력 저장 시 PII 처리 계층:

```python
import re

# 레벨 1: 저장 전 Python 레이어 마스킹
PII_PATTERNS = {
    "rrn": (r"\d{6}-[1-4]\d{6}", "RRNNNNNN-NNNNNNN"),  # 주민등록번호
    "account": (r"\d{3,6}-\d{2,6}-\d{4,8}", "ACCOUNT-MASKED"),
    "phone": (r"01[0-9]-\d{3,4}-\d{4}", "PHONE-MASKED"),
}

def mask_pii(text: str) -> tuple[str, bool]:
    """PII를 마스킹하고 마스킹 발생 여부를 반환."""
    masked = text
    has_pii = False
    for pattern, replacement in PII_PATTERNS.values():
        if re.search(pattern, masked):
            masked = re.sub(pattern, replacement, masked)
            has_pii = True
    return masked, has_pii
```

**주의**: SQL 내 PII도 별도로 처리해야 한다. `WHERE 주민번호 = '900101-1234567'` 형태의 SQL이 이력에 저장되지 않도록 `content_masked` / `sql_masked` 플래그로 추적한다.

출처: [PostgreSQL Anonymizer](https://www.postgresql.org/about/news/postgresql-anonymizer-10-privacy-by-design-for-postgres-2452/), [Building privacy-first AI features](https://www.algolia.com/blog/engineering/building-privacy-first-ai-features)

### 5.3 데이터 보관 기간 (Retention Policy)

금융기관 데이터 보관 기준 (일반적 가이드라인):

| 데이터 유형 | 최소 보관 | 권장 보관 | 근거 |
|------------|----------|----------|------|
| 대화 이력 (conversation_turns) | 1년 | 3년 | 은행법 기반 고객 응대 기록 |
| 감사 이력 (audit.agent_actions) | 5년 | 7년 | 금융거래 기록 보관 의무 |
| SQL 실행 이력 | 3년 | 5년 | IT 감사 요건 |
| 세션 메타 (sessions) | 1년 | 3년 | 연계 이력과 동기화 |

**자동화 보관 정책 구현**:

```sql
-- pg_cron을 사용한 자동 아카이빙 (매월 1일 실행)
SELECT cron.schedule(
    'archive-old-turns',
    '0 2 1 * *',  -- 매월 1일 새벽 2시
    $$
    -- 3년 초과 데이터를 아카이브 테이블로 이동
    INSERT INTO copilot.conversation_turns_archive
        SELECT * FROM copilot.conversation_turns
        WHERE created_at < now() - INTERVAL '3 years';

    -- 이동 후 원본 삭제
    DELETE FROM copilot.conversation_turns
        WHERE created_at < now() - INTERVAL '3 years';
    $$
);
```

출처: [GDPR-Compliant Chatbot Guide](https://quickchat.ai/post/gdpr-compliant-chatbot-guide), [Security and GDPR in AI Agents](https://www.technovapartners.com/en/insights/security-gdpr-enterprise-ai-agents)

### 5.4 멀티 워커 환경 동시성

FastAPI + 복수 uvicorn 워커 환경에서의 동시성 전략:

1. **Connection Pool**: `AsyncConnectionPool(min_size=2, max_size=10)` — 현재 `checkpointer.py`에 적용됨. 대화 이력 테이블도 동일 pool 공유 가능.

2. **낙관적 잠금(Optimistic Locking)**: `conversation_sessions.turn_count` 업데이트 시 충돌 방지:
   ```sql
   UPDATE copilot.conversation_sessions
   SET turn_count = turn_count + 1, last_active_at = now()
   WHERE thread_id = $1;
   -- PostgreSQL 원자적 연산으로 race condition 없음
   ```

3. **upsert 패턴**: 세션 생성 시 중복 방지:
   ```sql
   INSERT INTO copilot.conversation_sessions (thread_id, user_id, ...)
   VALUES ($1, $2, ...)
   ON CONFLICT (thread_id) DO UPDATE
       SET last_active_at = EXCLUDED.last_active_at;
   ```

출처: [Build Enterprise-Ready AI Agents - Azure Postgres](https://techcommunity.microsoft.com/blog/adforpostgresql/build-enterprise-ready-ai-agents-with-the-new-azure-postgres-langchain--langgrap/4453420)

---

## 6. 기각된 대안과 그 이유

### 대안 A: Checkpointer만 사용 (현행 Redis 대체)
**기각 이유**: `checkpoint_blobs`가 BYTEA binary이므로 SQL 조회 불가. 감사팀이 `thread_id`와 Python 역직렬화 없이는 대화 내용 확인 불가. 금융권 감사 요건 미충족.

### 대안 B: `PostgresChatMessageHistory` 단독 사용
**기각 이유**: LangGraph 파이프라인 상태(interrupt/resume, 명확화 컨텍스트)를 별도로 관리해야 하는 추가 복잡도 발생. 이미 체크포인터가 동작 중인 환경에서 중복 저장.

### 대안 C: Redis 영속화 모드 (현행 유지)
**기각 이유**: Redis AOF/RDB는 PostgreSQL 수준의 트랜잭션 보장, 파티셔닝, Row-Level Security를 제공하지 않음. 서버 재시작 시 TTL 만료 이슈 잔존. 단일 장애점(SPOF).

### 대안 D: Store(InMemoryStore) + PostgreSQL 백엔드
**기각 이유**: LangGraph Store는 현재 공식 PostgreSQL 백엔드가 beta 상태. 시계열 대화 이력보다 구조화 지식 저장에 특화. 파티셔닝, 감사 로그 설계 자유도 낮음.

---

## 7. 최종 권고 아키텍처

### 7.1 권고 구조

```
[사용자 질의]
      |
      v
[FastAPI + WebSocket]
      |
      v
[LangGraph Pipeline]
      |
      +---(자동)---> AsyncPostgresSaver (checkpoints / checkpoint_blobs)
      |              역할: 파이프라인 상태, interrupt/resume
      |
      +---(명시적)-> conversation_sessions  (세션 메타)
      |              conversation_turns     (대화 턴, 월별 파티션)
      |              역할: 사용자 이력 조회, UI, 분석
      |
      +---(명시적)-> audit.agent_actions    (감사 추적, 월별 파티션)
                     역할: 금융권 컴플라이언스, 불변 기록
```

### 7.2 구현 포인트

1. **기록 시점**: LangGraph 파이프라인 완료 후 `result_finalizer` 노드 또는 별도 `history_recorder` 노드에서 기록.
2. **비동기 fire-and-forget 금지**: 감사 기록은 `await` 필수. 실패 시 파이프라인 실패로 처리.
3. **Connection Pool 공유**: 체크포인터와 동일 `AsyncConnectionPool` 공유 가능. 단, 체크포인터의 `autocommit=True` 설정과 분리하여 일반 트랜잭션 연결을 별도로 확보해야 한다.
4. **thread_id 연결**: `conversation_sessions.thread_id`와 `AsyncPostgresSaver`의 `thread_id`를 1:1 매핑하여 두 시스템 간 조회 연결.

### 7.3 최소 구현 우선순위

| 단계 | 구현 내용 | 우선순위 |
|------|-----------|----------|
| Phase 1 | `conversation_sessions` + `conversation_turns` 테이블 | 높음 (서버 재시작 이력 유실 방지) |
| Phase 2 | `audit.agent_actions` 감사 테이블 | 높음 (금융권 필수) |
| Phase 3 | 월별 파티셔닝 + pg_partman 설정 | 중간 (데이터 규모 확인 후) |
| Phase 4 | PII 마스킹 파이프라인 통합 | 높음 (출시 전 필수) |
| Phase 5 | 보관 정책 자동화 (pg_cron) | 낮음 (운영 안정화 후) |

---

## 참고 문헌

### 공식 문서
- [LangGraph Memory Overview](https://docs.langchain.com/oss/python/langgraph/memory)
- [LangGraph Persistence API](https://docs.langchain.com/oss/python/langgraph/persistence)
- [langchain-postgres GitHub README](https://github.com/langchain-ai/langchain-postgres/blob/main/README.md)
- [PostgresChatMessageHistory API Reference](https://python.langchain.com/api_reference/postgres/chat_message_histories/langchain_postgres.chat_message_histories.PostgresChatMessageHistory.html)
- [langgraph-checkpoint-postgres PyPI](https://pypi.org/project/langgraph-checkpoint-postgres/)
- [PostgreSQL Table Partitioning Official Docs](https://www.postgresql.org/docs/current/ddl-partitioning.html)

### 구현 분석
- [Internals of LangGraph Postgres Checkpointer](https://blog.lordpatil.com/posts/langgraph-postgres-checkpointer/)
- [Checkpoint Implementations - DeepWiki](https://deepwiki.com/langchain-ai/langgraph/4.2-checkpoint-implementations)
- [LangGraph Long-Term Memory Launch Blog](https://blog.langchain.com/launching-long-term-memory-support-in-langgraph/)
- [Building Conversational AI Agents - DEV.to](https://dev.to/irubtsov/building-conversational-ai-agents-that-remember-langgraph-postgres-checkpointing-and-the-future-gdl)
- [agent-service-toolkit GitHub](https://github.com/JoshuaC215/agent-service-toolkit)

### 금융/엔터프라이즈
- [Production-Ready Audit Logs in PostgreSQL](https://medium.com/@sehban.alam/lets-build-production-ready-audit-logs-in-postgresql-7125481713d8)
- [PostgreSQL Audit Logging Best Practices - Severalnines](https://severalnines.com/blog/postgresql-audit-logging-best-practices/)
- [Time Partitioning with pg_partman - Crunchy Data](https://www.crunchydata.com/blog/time-partitioning-and-custom-time-intervals-in-postgres-with-pg_partman)
- [GDPR-Compliant Chatbot Guide](https://quickchat.ai/post/gdpr-compliant-chatbot-guide)
- [PostgreSQL Anonymizer 1.0](https://www.postgresql.org/about/news/postgresql-anonymizer-10-privacy-by-design-for-postgres-2452/)
- [Build Enterprise-Ready AI Agents - Azure Postgres](https://techcommunity.microsoft.com/blog/adforpostgresql/build-enterprise-ready-ai-agents-with-the-new-azure-postgres-langchain--langgrap/4453420)
- [Building privacy-first AI features - Algolia](https://www.algolia.com/blog/engineering/building-privacy-first-ai-features)
