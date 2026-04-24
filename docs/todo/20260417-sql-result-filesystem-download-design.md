# SQL 결과 파일시스템 기반 다운로드 설계서

> **작성일**: 2026-04-17 (3차 개정)
> **목적**: 멀티워커 불안전한 인메모리 SQL 결과 캐시를 제거하고
>           trace 파일과 동일한 파일시스템 기반 전달 패턴으로 전환.
>           CSV + XLSX 이중 포맷 지원, 포맷 선택은 프론트 localStorage.

---

## 1. 배경

### 1.1 현행 구조의 한계

| 요소 | 위치 | 문제 |
|---|---|---|
| `_sql_result_cache` | `src/main.py:790` (모듈-레벨 dict) | 워커별 독립 → 멀티워커 배포 시 `/api/download` 라우팅이 다른 워커로 가면 404 |
| `_cache_sql_result()` | `src/main.py:794` | 전체 rows 를 메모리에 상주 |
| `/api/download` | `src/main.py:837` | CSV/JSON 변환을 요청 시점에 수행 (CPU 중복) |

### 1.2 폐쇄망 배포 가정

- L4 로드밸런서 hash 방식으로 세션 sticky — **같은 노드 유지**
- 다중 노드로 확장 시나리오 없음 → **공유 스토리지 불필요**
- 파일 retention 은 시스템 운영팀에서 **별도 관리**

### 1.3 참조 패턴

trace 파일 전달 흐름 ([routers/sessions.py:172-207](src/routers/sessions.py#L172-L207))이
동일한 문제(대용량·멀티워커·다운로드)를 이미 해결 중. 패턴 재사용.

---

## 2. 설계 목표

1. **멀티워커 안전**: 인메모리 상태 제거
2. **메모리 단기 점유**: sql_executor 반환 직후 full rows 해제 (Option C 축소 전략)
3. **UI 응답성**: 미리보기(≤1000건)만 전송, 전체는 파일 링크
4. **포맷 유연성**: CSV + XLSX 지원, 프론트 localStorage 기반 선택
5. **감사 추적**: 파일 참조를 message metadata 에 저장 + 다운로드 액세스 로그
6. **보안**: 경로 순회 방어, CSV/XLSX 인젝션 방어
7. **운영 가능성**: 서버 전체 다운로드 ON/OFF 설정, 디렉토리·상한 환경변수 조절

---

## 3. 설계안

### 3.1 상한 설정 (환경변수)

| 설정 | 기본값 | 용도 |
|---|---|---|
| `sql_result_download_enabled` | `True` (신규) | **서버 전체** 다운로드 기능 ON/OFF. False 시 파일 저장·엔드포인트·UI 모두 비활성 |
| `ui_result_max_rows` | **1000** (기존 500 → 상향) | UI 미리보기 행 수. 은행 업무 UX에서 500→1000 은 페이지당 체감 차이 크지 않으나, 정렬·필터 시 유효 샘플 확대 효과 |
| `sql_result_file_max_rows` | **1_000_000** (신규) | 결과 파일 저장 시 상한. 초과 시 잘라 저장 + `file_truncated=true` |
| `sql_result_output_dir` | `"logs/sql_results"` (신규) | 결과 파일 저장 디렉토리 |
| `sql_result_default_format` | `"csv"` (신규) | localStorage 값 부재/비정상 시 fallback |

**XLSX 행 상한**: Excel 시트 제한 **1,048,576 행** (헤더 1행 포함 → 실 데이터 1,048,575 행).
`sql_result_file_max_rows` 가 이보다 크게 설정되면 xlsx 저장 실패 → 기동 시점 validator 로 차단.

### 3.2 저장 포맷 — CSV + XLSX 이중 지원

사용자가 선택한 **하나의 포맷만 저장** (둘 다 저장 시 디스크·CPU 낭비).

| 포맷 | 인코딩/포맷 | 용도 |
|---|---|---|
| CSV | UTF-8 BOM (`utf-8-sig`), `QUOTE_ALL`, CRLF, `,` 구분자 | 범용·경량. 대용량 우수 |
| XLSX | xlsxwriter `constant_memory=True` 스트림 쓰기, `strings_to_formulas=False` | Excel 직접 열기. 서식·타입 보존 |

**포맷 결정 흐름**:

```
프론트 localStorage['pref-download-format']  → WebSocket 쿼리 payload.download_format
                                                        ↓
sql_executor 실행 시 state.download_format 참조:
  ├─ "csv"    → {uuid}.csv  저장
  ├─ "xlsx"   → {uuid}.xlsx 저장
  └─ 없음/불량 → settings.sql_result_default_format (기본 "csv")
```

**의존성**: `xlsxwriter` (순수 Python, 폐쇄망 반입 OK). `pyproject.toml` 추가.

### 3.3 CSV/XLSX 인젝션 방어 (정규식 확장)

**차단 대상 prefix**: `=`, `+`, `-`, `@`, `\t`, `\r` (OWASP 확장판)

**숫자·금융 데이터 예외 허용** (제약 최소화):

```python
NUMERIC_RE = re.compile(
    r"^-?("
    r"\d+(\.\d+)?"                    # 정수·소수        (-123, -12.34)
    r"|\d{1,3}(,\d{3})+(\.\d+)?"      # 쉼표 천 단위     (-1,234,567.89)
    r")([eE][+-]?\d+)?%?$"            # 지수·백분율       (-1.5e10, -12.5%)
)
PAREN_NEG_RE = re.compile(r"^\(\d+(\.\d+)?\)$")   # 회계 음수       ((500.00))
```

**판정 로직**:

```python
def sanitize(val: str) -> str:
    if not val or not val.startswith(('=', '+', '-', '@', '\t', '\r')):
        return val
    if NUMERIC_RE.match(val) or PAREN_NEG_RE.match(val):
        return val                    # 숫자/금융 정상 유지
    return "'" + val                  # 수식/식별자 중립화
```

**정상 유지 예시**: `-1000`, `-12.5%`, `1,234,567`, `1.5e10`, `(500.00)`, `2026-04-17` (`-`로 시작 아님)
**차단 예시**: `=SUM(A1)`, `+cmd|'/c calc'`, `@evil.com`, `\tpayload`

**XLSX 의 2중 방어 — 역할 구분**:

| 방어 장치 | 막는 벡터 |
|---|---|
| `strings_to_formulas=False` | xlsxwriter 가 `=...` 문자열을 수식으로 해석하는 것 자체를 차단 |
| `sanitize()` + `'` prefix | Excel 자체가 `@`/`+`/`-`/`\t`/`\r` 로 시작하는 값을 자동 수식 취급하는 것을 차단 |

중복이 아닌 **상호보완**. 각자 다른 경로를 막음.

**한계**: `-A1` (`-` 로 시작하는 비숫자 식별자) 는 prefix 적용 → `'-A1` 로 저장됨. 금융 데이터에 이런 형태가 있다면 테스트 시 확인 필요.

### 3.4 파일명 규칙

`{message_uuid}.{ext}` — `ext` 는 `csv` 또는 `xlsx`

- message_uuid 는 **pipeline 진입 시점 선생성** (§3.5 참조) 후 save_message 에도 그 값 사용
- 같은 message_uuid 재쿼리 시 동일 포맷이면 overwrite
- 사용자가 localStorage 포맷을 `csv` → `xlsx` 로 바꾼 뒤 같은 message_uuid 가 재사용되는 경우: 두 확장자 파일 병존 가능. retention 주체가 확장자 무관하게 uuid 기반 정리하면 문제 없음

### 3.5 저장 위치 — `sql_executor` 노드 내부 (Option C 축소 전략)

**변경 이유**: `_execute_and_finalize` 는 `app.ainvoke()` 로 그래프 전체를 실행한 뒤 결과를 받는 구조 — SQL 실행 시점에 개입할 수 없음 ([runner.py:305-323](src/agents/graph/runner.py#L305-L323)). 따라서 파일 쓰기는 **실제 SQL 실행이 일어나는 [sql_executor](src/agents/nodes/present/sql_executor.py) 노드 내부**에서 수행.

**흐름**:

```
┌──────────────────────────────────────────────────────┐
│ pipeline 진입 (runner._execute_and_finalize 앞단)     │
│   ↓                                                   │
│ message_uuid 선생성 → initial_state 에 주입            │
│   ↓                                                   │
│ app.ainvoke(initial_state, ...)                       │
│   ↓                                                   │
│   ┌ sql_executor 노드 ─────────────────────────────┐ │
│   │ 1. connector 로 SQL 실행 → rows: list[dict]     │ │
│   │ 2. settings.sql_result_download_enabled 체크    │ │
│   │    False 면 3 스킵                              │ │
│   │ 3. 파일 저장 (raw list 전달, Pydantic 전):       │ │
│   │    fmt = state.download_format or default       │ │
│   │    file_meta = await write_sql_result(          │ │
│   │        rows, columns, state.message_uuid,       │ │
│   │        output_dir=..., max_rows=file_max,       │ │
│   │        fmt=fmt,                                 │ │
│   │    )  # writer 가 내부 슬라이스 + file_truncated │ │
│   │ 4. SQLResult 생성 (이미 UI 축소):                │ │
│   │    total = len(rows)                            │ │
│   │    state.sql_result = SQLResult(                │ │
│   │      rows=rows[:ui_max],                        │ │
│   │      row_count=total,                           │ │
│   │      total_row_count=total,                     │ │
│   │      ui_truncated=(total > ui_max),             │ │
│   │      file_truncated=file_meta["file_truncated"],│ │
│   │    )                                            │ │
│   │    state.sql_result_file_meta = {**file_meta,   │ │
│   │      "total_row_count": total,                  │ │
│   │      "ui_truncated": total > ui_max}            │ │
│   │ 5. rows (full list) 는 함수 종료로 GC           │ │
│   └─────────────────────────────────────────────────┘│
│   ↓                                                   │
│ formatter/analyzer/visualizer 노드 (rows[:ui_max] 표본)│
│   ↓                                                   │
│ _execute_and_finalize 종료 → save_message             │
│   (state.message_uuid 주입, §3.16)                    │
└──────────────────────────────────────────────────────┘
```

**message_uuid 선생성**:

- 기존: `save_message` 내부에서 DB `RETURNING` 으로 uuid 생성
- 변경: runner 진입 지점에서 `uuid.uuid4().hex` 생성 → state 에 주입 + save_message 가 state 의 uuid 사용 (§3.16 시그니처 변경)
- 이유: sql_executor 가 파일명으로 쓰려면 그 시점에 uuid 가 확정되어 있어야 함
- 3개 호출부 영향 범위는 §3.17 참조

**Option C 축소 전략 상세**:

- sql_executor 반환 시점에 **full rows 를 SQLResult 에 담지 않음** (rows 는 이미 slice)
- analyzer/visualizer/formatter 는 `state.sql_result.rows` (≤1000행) 로 진행
- 대부분의 분석은 DB 측 `GROUP BY`·`LIMIT` 으로 이미 집계된 결과 → 1000행 미만이 일반적
- 전체 통계(평균·합계 등)를 요구하는 분석은 **현재 구조에서 DB 재쿼리로 처리되는 전제**
- ⚠️ 만약 향후 "원본 10만 행에서 통계 도출" 요구가 생기면 Option A (checkpointer 직렬화 제외) 로 재설계 필요 — 현 범위 외

**Pydantic 변이 이슈 없음**: 파일 쓰기는 raw `list[dict]` 단계에서 수행, `SQLResult` 는 slice 된 rows 로 처음부터 생성.

### 3.6 에러 경로 처리 정책

| 실패 지점 | 파일 저장 | metadata 기록 | 이유 |
|---|---|---|---|
| 스키마 검색·SQL 생성·명확화 | ❌ | 없음 | sql_executor 미진입 |
| SQL 실행 실패 (connector exception) | ❌ | 없음 | raw rows 없음 |
| SQL 실행 성공, 이후 포맷터/분석 실패 | ✅ | 정상 레코드 | **SQL 결과는 유효 자산** — 다운로드 가치 유지 |
| SQL 실행 성공, 파일 쓰기 실패 (OSError/권한) | warn 로그 | **실패 레코드** | 감사 추적 보존 |
| 다운로드 전역 OFF (`sql_result_download_enabled=False`) | ❌ | 없음 | 의도된 차단 |

**파일 쓰기 실패 시 metadata 실패 레코드**:

```json
{
  "sql_result_files": [{
    "name": null,
    "format": "xlsx",
    "row_count": 12345,
    "total_row_count": 12345,
    "size_bytes": null,
    "ui_truncated": false,
    "file_truncated": false,
    "error": "PermissionError: [Errno 13] Permission denied: 'logs/sql_results/...'"
  }]
}
```

- 은행 감사 요건상 "데이터 추출 시도 + 추출 실패" 기록 필수 (`.claude/rules/data-security.md`)
- UI 는 `download_ready` 미전송 (사용자에게 다운로드 버튼 숨김)

**파일 쓰기 성공 but metadata 저장 실패**:
- DB 일시 장애 시 발생 가능
- 현 설계: 파일은 디스크에 존재하나 download_ready 미전송 → **orphan 파일**
- retention 정책으로만 정리 (즉시 삭제는 별도 복잡도)

### 3.7 비동기 I/O

`asyncio.to_thread(_write_sync)` 로 처리.

- 이벤트 루프 블로킹 방지
- 신규 의존성 불필요 (`aiofiles` 미도입)
- 기존 패턴 (`connectors/impl/qdrant_connector.py` 등) 과 일관
- **취소 전파**: 파이프라인 cancel 시 `to_thread` 는 즉시 취소되지 않음 — 진행 중인 쓰기 완료. orphan 파일은 retention 으로 정리
- **대용량 XLSX**: 100만 행 변환 수십 초 가능성. 1차 릴리즈는 **동기 await** 로 진행하고 UI P95 응답시간 모니터링 후 필요 시 background task 분리 (후속 개선)

### 3.8 다운로드 엔드포인트

`GET /api/sql-results/{filename}?user_id=<requester>`

- 파일명 검증: `Path(filename).name` + `resolve().relative_to(output_dir)` (trace 엔드포인트와 동일)
- 확장자 허용: `.csv`, `.xlsx` 만
- 다운로드 전역 OFF 시: 404
- 접근 제어: §3.13 참조
- 반환: `FileResponse` (Content-Disposition: attachment)
- MIME 타입:
  - csv → `text/csv; charset=utf-8`
  - xlsx → `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`

**감사 로그**:

```python
logger.info(
    "sql_result_download",
    extra={
        "user_id": requester,
        "filename": filename,
        "format": ext,
        "message_uuid": message_uuid,
        "client_ip": request.client.host,
        "access_result": "success" | "denied_403" | "not_found_404",
    },
)
```

- 은행 감사 요건(`.claude/rules/data-security.md`)에 따라 **모든 다운로드 액세스** 로그 필수
- 실패 포함 전수 기록

### 3.9 WebSocket 이벤트 변경

**Before**:
```json
{
  "type": "download_ready",
  "session_id": "...",
  "row_count": 1234,
  "formats": ["csv", "json"],
  "message_uuid": "..."
}
```

**After**:
```json
{
  "type": "download_ready",
  "session_id": "...",
  "row_count": 1234,
  "total_row_count": 1234567,
  "download_url": "/api/sql-results/<message_uuid>.csv",
  "format": "csv",
  "file_truncated": false,
  "message_uuid": "..."
}
```

**송출 타이밍**: `stream.end` 이벤트 **직후** 동일 WebSocket 세션에 별도 이벤트로 송출. 파일 쓰기 성공 후 main.py 레벨에서 발행.
`ui_truncated` 는 `stream.end` payload 의 `truncation_notice` (§3.14) 로 이미 전달되므로 download_ready 에는 포함 안 함.

### 3.10 사용자 다운로드 포맷 설정 — 프론트 localStorage

**저장 위치**: 프론트엔드 localStorage.

**Key**: `pref-download-format` (기존 `pref-streaming`, `pref-sql-display` 등과 동일 `pref-*` 네이밍 패턴)
**값**: `"csv"` 또는 `"xlsx"` (기본값 `"csv"`)

**서버 저장소·PATCH 엔드포인트·DDL 마이그레이션 일체 없음**.

**전달 흐름**:

```
프론트:
  format = localStorage.getItem('pref-download-format') || 'csv'
  ws.send({
    type: "query",
    message: "...",
    settings: {
      download_format: format,     // 신규
      streaming: streamingPref,    // 기존 pref-streaming
      ...
    }
  })

서버:
  payload["settings"]["download_format"]
    → initial_state["download_format"]
    → sql_executor 에서 참조
```

**Fallback 계층**:
1. `state["download_format"]` 값이 `"csv"` / `"xlsx"` 중 하나면 사용
2. 값 없음/invalid → `settings.sql_result_default_format` (`.env` 기본 `"csv"`)

**UI 설정 진입점**: 기존 설정 패널(`pref-streaming` 토글 등이 있는 영역)에 드롭다운 추가:

```
[다운로드 형식 ▼] CSV | Excel (.xlsx)
```

**브라우저/PC 변경 시**: localStorage 초기화 → 기본값(`csv`) 로 복귀. 은행 업무 PC 고정 환경에서는 드문 케이스.

### 3.11 message_store metadata 통합

기존 `metadata.trace_files` 와 동일 방식으로 `sql_result_files` 추가:

```json
{
  "trace_files": [{"name": "...json", "...": "..."}],
  "sql_result_files": [{
    "name": "<uuid>.csv",
    "format": "csv",
    "row_count": 1234,
    "total_row_count": 1234567,
    "size_bytes": 89012,
    "ui_truncated": true,
    "file_truncated": false,
    "error": null
  }]
}
```

세션 복원 시 과거 턴의 다운로드 링크·잘림 안내 복원 가능.

### 3.12 디렉토리 생성 & .gitignore

- `sql_result_output_dir` 환경변수 조정 가능 (기본 `"logs/sql_results"`)
- `lifespan` 시작 시 `Path(settings.sql_result_output_dir).mkdir(parents=True, exist_ok=True)`
- 권한 문제 시 기동 시점에 명확한 에러 로그
- `.gitignore` 에 `logs/sql_results/` 추가

### 3.13 소유자 user_id 검증 (SSO 미도입 잠정안)

**현재 방안**: 쿼리 파라미터 `?user_id=<requester>` 검증

- message_uuid → thread_id → session_index.user_id 조회
- 불일치 시 403
- 비용: DB SELECT 1회 (<5ms)

**한계 (명시)**:

- 현 SSO 미도입 상태에서 `?user_id=` 는 클라이언트 위조 가능 → **강한 방어선 아님**
- 정상 UI 경로를 벗어난 접근에 대한 **1차 억제책**에 불과
- TODO 주석으로 "SSO 도입 시 강화 대상" 명시

**SSO 도입 후 강화 (후속)**:

- Option A: HMAC 서명 토큰 — `?token=hmac_sha256(message_uuid + user_id, secret)`
- Option B: 인증 미들웨어가 주입한 `request.state.user_id` 로 교체
- Option C: 단기 TTL 서명 URL (1시간 유효)

SSO 연동 설계 시 A/B/C 중 별도 결정.

### 3.14 잘림 안내 메시지 — formatter 책임

**목적**: 사용자가 데이터가 잘렸는지 즉시 인지하고, 필요 시 조건을 추가하도록 유도.

**데이터 흐름**:

```
sql_executor:
  SQLResult(total_row_count, ui_truncated, file_truncated)
    ↓
formatter 노드:
  truncation_notice = build_notice(total, ui_trunc, file_trunc)
  FormattedResponse.truncation_notice: str | None
    ↓
WebSocket stream.end payload 에 truncation_notice 포함
    ↓
프론트: 응답 본문 하단에 별도 회색 박스로 렌더
    ↓
message_store metadata 에 보존 → 세션 복원 시 동일 표시
```

**분기 문구** (은행 임직원 톤, `.claude/rules/user-interaction.md` 준수):

`download_available` = `settings.sql_result_download_enabled and 파일 쓰기 성공` 을 formatter 에 전달하여 다운로드 관련 문구 포함 여부를 동적 결정.

| # | 조건 | 문구 |
|---|---|---|
| ① | `ui_truncated=true, file_truncated=false, download_available=true` | "전체 **1,234,567건** 중 미리보기로 상위 **1,000건**을 표시합니다. 전체 데이터는 다운로드 버튼을 이용해 주세요." |
| ② | `ui_truncated=true, file_truncated=true, download_available=true` | "전체 **1,234,567건** 중 미리보기 **1,000건**, 다운로드 파일에 상위 **1,000,000건**이 포함됩니다. 조건(기간·지점·상품 등)을 추가하면 더 정확한 데이터 추출이 가능합니다." |
| ③ | `ui_truncated=true, download_available=false` | "전체 **1,234,567건** 중 미리보기로 상위 **1,000건**만 표시합니다. 전체 결과가 필요하면 조건(기간·지점·상품 등)을 좁혀 다시 조회해 주세요." |
| ④ | `ui_truncated=false` | `truncation_notice = None` — UI 안내 블록 미표시 |

숫자는 천 단위 쉼표 포맷팅, 금액이 아니므로 단위 변환 없음.

**note**: ③ 은 두 하위 케이스를 통합 — (a) `sql_result_download_enabled=false` 운영 차단, (b) 파일 쓰기 실패로 다운로드 버튼 제공 불가. 사용자에게 같은 메시지로 노출.

### 3.15.1 PipelineState 신규 필드 (file_meta 전달 경로)

[src/agents/state/state.py](src/agents/state/state.py) `PipelineState` 에 3개 필드 추가:

```python
# ── SQL 결과 다운로드 (신규) ──
# W: runner (payload.settings.download_format 또는 default)  R: sql_executor
download_format: str = ""          # "csv" | "xlsx" | "" (fallback)
# W: runner (진입 시 uuid4 선생성)  R: sql_executor (파일명), save_message (INSERT)
message_uuid: str = ""
# W: sql_executor (write_sql_result 반환값)  R: formatter (truncation_notice),
#     runner (PipelineResult.sql_result_files 조립)
sql_result_file_meta: dict[str, Any] | None = None
```

**흐름**:

```
runner 진입:
  message_uuid = uuid4().hex
  download_format = payload.get("settings", {}).get("download_format", "")
  initial_state = PipelineState(message_uuid=..., download_format=..., ...)
    ↓
sql_executor:
  file_meta = await write_sql_result(..., message_uuid=state.message_uuid,
                                     fmt=state.download_format or default)
  state.sql_result_file_meta = {**file_meta, total_row_count, ui_truncated}
  state.sql_result = SQLResult(rows=rows[:ui_max], total_row_count=...,
                               ui_truncated=..., file_truncated=...)
    ↓
formatter:
  # state.sql_result 의 3개 필드 + state.sql_result_file_meta["error"] 로 분기
  truncation_notice = build_notice(
      total=state.sql_result.total_row_count,
      ui_trunc=state.sql_result.ui_truncated,
      file_trunc=state.sql_result.file_truncated,
      download_available=(
          settings.sql_result_download_enabled
          and state.sql_result_file_meta is not None
          and state.sql_result_file_meta.get("error") is None
      ),
  )
    ↓
runner (turn 종료):
  save_message(..., message_uuid=state.message_uuid,
               metadata={"sql_result_files": [state.sql_result_file_meta]})
```

### 3.16 save_message 시그니처 변경 (message_uuid 외부 주입)

**배경**: sql_executor 가 파일명(`<uuid>.csv`)으로 쓰려면 **SQL 실행 시점에 uuid 가 확정**되어야 함. 현재 [save_message](src/services/message_store.py#L42-L65) 는 DB `RETURNING message_uuid` 로 사후 생성 → sql_executor 가 참조 불가능.

**시그니처 변경**:

```python
async def save_message(
    pool: Any,
    *,
    thread_id: str,
    role: str,
    content: str,
    message_uuid: str | None = None,   # ← 신규. None 이면 기존 동작(DB 생성)
    client_ip: str | None = None,
    ... (나머지 기존 동일)
) -> tuple[str, int] | None:
```

**INSERT SQL 수정** ([message_store.py:107-133](src/services/message_store.py#L107-L133)):

```sql
INSERT INTO checkpoint_dc_messages (
    thread_id, seq, message_uuid, role, content, ...
) VALUES (
    %(thread_id)s,
    COALESCE(...),                                   -- seq 채번 동일
    COALESCE(%(message_uuid)s::uuid, gen_random_uuid()),  -- ← 주입 시 그 값, 없으면 DB 생성
    %(role)s, %(content)s, ...
)
RETURNING message_uuid::text, seq
```

- 주입된 uuid 가 기존 레코드와 충돌하면 UNIQUE 제약 위반 → runner 쪽에서 매 턴 신규 uuid4 생성이므로 실제 충돌 불가
- 기존 호출부(uuid 미주입)는 파라미터 기본값 `None` → 동작 변화 없음

### 3.17 runner.py 3개 호출부 영향 범위

[runner.py](src/agents/graph/runner.py) 에서 `save_message` 가 **3개 경로**에서 호출됨. 각 경로마다 `message_uuid` 선생성·주입 필요:

| 번호 | 라인 | 경로 | uuid 생성 시점 | sql_executor 진입 여부 |
| --- | --- | --- | --- | --- |
| 1 | [L352-376](src/agents/graph/runner.py#L352-L376) | clarification (명확화 질문) | 진입 시 생성해도 **sql_executor 는 실행 안 됨** | ❌ 파일 저장 스킵 |
| 2 | [L405-422](src/agents/graph/runner.py#L405-L422) | normal (정상 응답) | 진입 시 생성 → sql_executor 파일명 사용 | ⭕ |
| 3 | [L527-540](src/agents/graph/runner.py#L527-L540) | error (에러 종료) | 진입 시 생성 → sql_executor 가 성공했다면 파일 존재 | 경로별 다름 |

**단일 지점 생성 권장**: `_execute_and_finalize` **최초 진입부**에서 `message_uuid = uuid.uuid4().hex` 1회 생성 → `initial_state.message_uuid` 주입.
이후 3개 경로는 모두 `state.message_uuid` 를 `save_message(message_uuid=...)` 로 전달.

**user_message 저장**: `role="user"` 쪽은 별도 uuid (sql 파일명과 무관). 기존대로 DB 생성 허용 (`message_uuid=None`).

### 3.18 WebSocket payload 스키마 호환 (flat → nested)

**현행** ([main.py:625-629](src/main.py#L625-L629)):

```python
parsed = json.loads(raw)
user_text = parsed.get("text", raw)
client_streaming = bool(parsed.get("streaming", False))   # ← flat
```

**변경 후 — 이중 호환 파싱**:

```python
parsed = json.loads(raw)
user_text = parsed.get("text", raw)

# settings 블록 우선, 없으면 flat key fallback (구 클라이언트 호환)
_settings = parsed.get("settings") or {}
client_streaming = bool(
    _settings.get("streaming", parsed.get("streaming", False))
)
download_format = str(_settings.get("download_format", "")).lower()
if download_format not in ("csv", "xlsx"):
    download_format = ""   # fallback → sql_result_default_format
```

**프론트 신규 스키마**:

```json
{
  "type": "query",
  "text": "...",
  "settings": {
    "streaming": true,
    "download_format": "xlsx"
  }
}
```

**호환 정책**:

- 프론트/백엔드 동시 배포(§7.3) 원칙이나, WebSocket 재접속 timing 등으로 구 payload 가 1턴 섞여 들어올 수 있음 → flat fallback 으로 안전하게 흡수
- 신규 프론트가 `streaming` 을 flat + nested 양쪽으로 보내지 **않음** (nested 만). 서버는 nested 우선 읽기
- `download_format` 은 flat fallback 없음 (신규 필드이므로 구 클라 미지원 당연)
- 1~2 릴리즈 후 flat fallback 제거 예정 (TODO 주석)

### 3.19 서버 전체 다운로드 ON/OFF

**설정**: `sql_result_download_enabled: bool = True` (§3.1)

**False 일 때 동작**:

| 위치 | 동작 |
|---|---|
| sql_executor | 파일 저장 스킵 (§3.5 흐름도 step 2) |
| `download_ready` | 미전송 |
| `truncation_notice` | "다운로드 버튼을 이용해 주세요" 문구 제외 (동적 생성) |
| `GET /api/sql-results/{filename}` | 404 |
| 프론트 | WebSocket 으로 `download_ready` 를 받지 못하면 버튼 미표시 (기존 로직 유지) |

**용도**: 폐쇄망 운영 중 감사 정책·사고 대응 등으로 **즉시 반출 차단**이 필요한 경우.

---

## 4. 구현 단계

### 4.1 신규 파일

**`src/services/sql_result_writer.py`**:

```python
from typing import Literal, TypedDict

class SqlFileMeta(TypedDict):
    """write_sql_result 반환 타입 — 파일 레벨 메타데이터."""
    name: str | None               # 성공: "<uuid>.csv" / 실패: None
    format: Literal["csv", "xlsx"]
    row_count: int                 # 성공: 저장 행 수(≤max_rows) / 실패: 시도 행 수
    size_bytes: int | None         # 실패 시 None
    file_truncated: bool           # len(rows) > max_rows 인 경우 True
    error: str | None              # 성공 시 None


async def write_sql_result(
    rows: list[dict[str, Any]],
    columns: list[str],
    message_uuid: str,
    *,
    output_dir: Path,
    max_rows: int,
    fmt: Literal["csv", "xlsx"],
) -> SqlFileMeta:
    """SQL raw rows 를 지정 포맷으로 저장하고 파일 레벨 메타데이터를 반환한다.

    호출자(sql_executor)가 이 반환값에 ui_truncated / total_row_count 를 추가하여
    최종 message_store metadata 레코드를 조립한다.
    """
```

내부 분기: `_write_csv_sync()`, `_write_xlsx_sync()` + 공통 `sanitize()`.

**sql_executor 의 최종 조립**:

```python
file_meta: SqlFileMeta = await write_sql_result(rows, ..., fmt=fmt)
meta_record = {
    **file_meta,
    "total_row_count": total,
    "ui_truncated": total > ui_max,
}
# state.sql_result_file_meta = meta_record  → formatter 가 참조
# runner 가 turn 종료 시 PipelineResult.sql_result_files 로 전달
```

**신규 파일 없음** (이전 설계의 `session_preferences.py`, `routers/user.py` 는 **localStorage 전환으로 불필요**).

### 4.2 수정 파일

| 파일 | 변경 |
|---|---|
| `pyproject.toml` | `xlsxwriter` 의존성 추가 |
| `src/config.py` | `sql_result_download_enabled`, `ui_result_max_rows` 500→1000, `sql_result_output_dir`, `sql_result_file_max_rows`, `sql_result_default_format` 추가 + xlsx 행 상한 validator |
| `src/models/result.py` `SQLResult` | `total_row_count: int`, `ui_truncated: bool`, `file_truncated: bool` 필드 추가 |
| `src/agents/models/response.py` `FormattedResponse` | `truncation_notice: str \| None` 필드 추가 |
| `src/agents/models/response.py` `PipelineResult` | `sql_result_files: list[dict]` 필드 추가 |
| [src/agents/state/state.py](src/agents/state/state.py) `PipelineState` | 신규 필드 3개: `download_format: str`, `message_uuid: str`, `sql_result_file_meta: dict \| None` — file_meta 가 sql_executor → formatter 로 흘러가는 경로 (§3.16) |
| [src/agents/graph/runner.py](src/agents/graph/runner.py) | pipeline 진입 시점에 `message_uuid` 선생성 → `initial_state` 에 주입. **3개 호출부**(L357/L411/L540) 모두 state 의 uuid 사용 (§3.17) |
| [src/services/message_store.py:42-65](src/services/message_store.py#L42-L65) `save_message()` | **시그니처 변경**: `message_uuid: str \| None = None` 파라미터 추가. None 일 때 기존처럼 DB 생성, 주입 시 해당 uuid 로 INSERT. INSERT SQL 및 RETURNING 절 수정 (§3.17). metadata 에 `sql_result_files` 포함 (성공/실패 양쪽) |
| [src/agents/nodes/present/sql_executor.py](src/agents/nodes/present/sql_executor.py) | SQL 실행 후 `write_sql_result` 호출 (`sql_result_download_enabled` 체크). SQLResult 생성 시 신규 필드 채움. `state.sql_result_file_meta` 에 file_meta 저장 (formatter 전달용) |
| [src/agents/nodes/present/formatter.py](src/agents/nodes/present/formatter.py) | `state.sql_result_file_meta` 와 `state.sql_result.{ui_truncated, file_truncated, total_row_count}` 참조하여 `truncation_notice` 생성 (§3.14) |
| [src/main.py](src/main.py) (WebSocket 핸들러) | payload flat→nested **호환 파싱** (§3.18). `settings.download_format` → state 전달. stream.end 에 truncation_notice 포함. 파일 저장 결과 기반 download_ready 발행 |
| `src/routers/sessions.py` | `GET /api/sql-results/{filename}` 엔드포인트 + 감사 로그 |
| `static/embedded.html` (또는 해당 JS) | `pref-download-format` localStorage 설정 UI. WebSocket 쿼리 payload.settings.download_format 포함 (streaming 도 settings 로 이동). `/api/download` POST 제거 → download_url GET 직접 링크. truncation_notice 렌더링 |
| `.env.example` | 신규 설정 5건 (download_enabled 포함) 추가 |
| `.gitignore` | `logs/sql_results/` 추가 |

### 4.3 삭제 대상

| 위치 | 대상 |
|---|---|
| `src/main.py:789-816` | `_sql_result_cache`, `_MAX_CACHE`, `_cache_sql_result()` |
| `src/main.py:819-877` | `DownloadRequest`, `POST /api/download` |
| `src/main.py:581` | `_cache_sql_result()` 호출 |
| Frontend | `/api/download` POST 코드 |

---

## 5. 엣지 케이스

| 케이스 | 처리 |
|---|---|
| row_count = 0 | 파일 저장 스킵, download_ready 미전송 (현행 유지) |
| 명확화 대기 턴 | sql_executor 미진입이므로 자동 스킵 |
| 파일 쓰기 실패 (OSError/디스크 풀) | warn 로그, metadata 실패 레코드 저장(§3.6), download_ready 미전송, 파이프라인 성공 처리 |
| rows > file_max_rows | 잘라 저장, `file_truncated=true` UI 반영 |
| rows > 1,048,575 & xlsx 요청 | `sql_result_file_max_rows` 상한에서 이미 차단. 설정 오류 시 기동 validation |
| 동시 동일 message_uuid | 충돌 불가 (uuid 고유성 보장) — 재쿼리 시 overwrite |
| 포맷 변경(csv↔xlsx) 후 같은 uuid 재쿼리 | 두 확장자 파일 병존 가능. retention 이 uuid 기준 정리하면 무문제 |
| 파일명 경로 순회 시도 | 400 반환 |
| 지원하지 않는 확장자 요청 | 400 반환 (`.csv`, `.xlsx` 외) |
| 파일 부재 (삭제됨) | 404 반환 + 감사 로그 |
| 다른 사용자의 파일 접근 | 403 반환 + 감사 로그 |
| DB 조회 실패 (PG 장애) | 500 반환 + warn 로그 (보수적으로 접근 차단) |
| localStorage 값 없음/비정상 | `sql_result_default_format` fallback |
| 다운로드 전역 OFF | 파일 저장·엔드포인트·UI 일괄 비활성 |
| 파이프라인 cancel 중 파일 쓰기 | to_thread 는 완료까지 진행, 부분 파일 발생 가능 → retention 정리 |
| analyzer 가 전체 rows 필요 시 | 현재 구조 전제: DB 재집계. 만약 raw 분석이 필요한 시나리오 발생 시 Option A 재설계 (§3.5 말미 주의) |

---

## 6. 보안

### 6.1 경로 순회

trace 엔드포인트 ([routers/sessions.py:181-193](src/routers/sessions.py#L181-L193)) 로직 재사용.
`Path(filename).name` 검증 후 `resolve().relative_to(output_dir)` 체크.

### 6.2 CSV/XLSX 인젝션

- CSV: sanitize + `'` prefix (§3.3)
- XLSX: sanitize + `strings_to_formulas=False` **상호보완** (§3.3 — 둘이 다른 벡터 차단)

### 6.3 PII

`settings.pii_masking_enabled=false` 기본값 유지 → 원본 그대로 저장
(은행 감사 요건, `.claude/rules/data-security.md` 참조).

### 6.4 접근 제어

message_uuid → thread_id → session_index.user_id 검증 (§3.13).
SSO 미도입 상태의 1차 억제책. 도입 후 HMAC/세션 기반 강화.

### 6.5 감사 로그

- 다운로드 엔드포인트 호출 시 전수 로그 (§3.8): user_id, filename, format, client_ip, access_result
- 파일 쓰기 실패 시 metadata 에 실패 레코드 저장 (§3.6): "시도했으나 실패" 추적 가능
- PII 마스킹 없이 저장하므로 감사 요건 더 엄격 적용

---

## 7. 호환성

### 7.1 Breaking Changes

- `POST /api/download` 제거 → 프론트엔드 동시 전환 필요
- `download_ready` 이벤트 스키마 변경 (`formats` 배열 → `download_url`/`format` 단수 + `file_truncated`)
- `stream.end` payload 에 `truncation_notice` 필드 추가 (프론트 신규 렌더링)

### 7.2 세션 복원

이전 턴(구 스키마) 복원 시 metadata 에 `sql_result_files` 없음 → 다운로드 버튼 비활성화.
과거 데이터 마이그레이션 없음 (로그성 데이터).

### 7.3 배포 순서

프론트/백엔드 동시 배포 필요 (스키마 변경 breaking). 폐쇄망 단일 번들 배포이므로 운영 부담 적음.

---

## 8. 테스트 계획

| 시나리오 | 검증 |
|---|---|
| CSV 정상 저장 | `logs/sql_results/<uuid>.csv` 생성, row_count 일치, UTF-8 BOM 헤더 |
| XLSX 정상 저장 | `<uuid>.xlsx` 생성, Excel 에서 정상 열림, 한글 깨짐 없음 |
| localStorage 포맷 반영 | payload.settings.download_format=csv → csv 생성 / xlsx → xlsx 생성 |
| fallback 동작 | payload 미지정 → `sql_result_default_format` 적용 |
| 다운로드 전역 OFF | `sql_result_download_enabled=false` → 파일 없음, 엔드포인트 404 |
| CSV 포맷 세부 | 모든 필드 `"..."` 감쌈, 개행·콤마 포함 값 보존, CRLF |
| 인젝션 방어 (확장) | `=SUM(A1)` → `'=SUM(A1)`, `\tpayload` → `'\tpayload`, `-123` → `-123` (유지) |
| 금융 숫자 예외 | `-1,234.56`, `1.5e10`, `(500.00)`, `-12.5%` 모두 prefix 없이 저장 |
| UI 미리보기 잘림 | row_count=2000 → UI rows 1000, `ui_truncated=true`, 안내 문구 표시 |
| 파일 잘림 | row_count=1,500,000 → 파일 1,000,000, `file_truncated=true`, 파일 안내 문구 |
| 에러 경로 — SQL 성공 후 포맷터 실패 | 파일 유효·다운로드 가능, `truncation_notice` 없음/있음 정상 |
| 에러 경로 — SQL 실행 실패 | 파일 생성 안 됨, metadata 없음 |
| 파일 쓰기 실패 | 권한 오류 모사 → metadata 실패 레코드 저장, download_ready 미전송 |
| 경로 순회 | `/api/sql-results/../../etc/passwd` → 400 |
| 확장자 필터 | `/api/sql-results/xxx.exe` → 400 |
| 파일 부재 | 존재하지 않는 uuid → 404 + 감사 로그 |
| 다른 사용자 접근 | user_id 불일치 → 403 + 감사 로그 |
| 감사 로그 | 성공/403/404 모든 케이스에서 sql_result_download 로그 기록 |
| 멀티워커 | 워커A 생성 파일을 워커B 에서 다운로드 가능 (같은 노드 가정) |
| XLSX 행 상한 위반 | `sql_result_file_max_rows=2_000_000` 설정 시 기동 실패 |
| message_uuid 일관성 | sql_executor 저장 파일명 = save_message 후 DB 레코드 message_uuid 일치 |
| 세션 복원 | 과거 턴의 sql_result_files + truncation_notice 복원, 다운로드 링크 동작 |

---

## 9. 결정 사항 (사용자 승인 로그)

### 9.1 1차 승인 (2026-04-17 오전)

| # | 항목 | 결정 |
|---|---|---|
| 1 | CSV 인젝션 방어 prefix | 적용, 숫자 예외 |
| 2 | 디렉토리 자동 생성 | 적용, 경로 설정 가능 |
| 3 | `.gitignore` 추가 | 적용 |
| 4 | message_store metadata 통합 | 적용 |
| 5 | 소유자 user_id 검증 | 적용 (SSO 전 잠정) |

### 9.2 2차 승인 (2026-04-17 오후, 리뷰 반영)

| # | 항목 | 결정 |
|---|---|---|
| 6 | CSV 정규식 확장 | `\t`/`\r` + 쉼표·지수·백분율·괄호음수 예외 |
| 7 | XLSX 포맷 추가 | xlsxwriter 의존성 |
| 8 | SSO 강화 옵션 A/B/C | 후속 과제 분리 |

### 9.3 3차 승인 (2026-04-17 저녁, 아키텍처 정합성 재검증 반영)

| # | 항목 | 결정 |
|---|---|---|
| 9 | 파일 쓰기 위치 | **`sql_executor` 노드 내부** (기존 `_execute_and_finalize` 취소, 아키텍처 불가능) |
| 10 | message_uuid 선생성 | pipeline 진입 시점에 uuid4 생성 → state 주입 |
| 11 | Raw rows 축소 전략 | **Option C** (sql_executor 반환 시점에 축소, full rows GC) |
| 12 | 분석 정확도 전제 | DB 측 집계 결과가 대부분. raw 분석 수요 발생 시 Option A 재설계 |
| 13 | 사용자 포맷 설정 | **프론트 localStorage** (`pref-download-format`) — 서버 DB 저장 없음 |
| 14 | `user_preferences` 테이블·PATCH API | **전면 취소** (localStorage 전환) |
| 15 | 서버 전체 다운로드 ON/OFF | `sql_result_download_enabled` 설정 추가 |
| 16 | 잘림 안내 메시지 | formatter 가 생성, `FormattedResponse.truncation_notice` 필드, 3가지 분기 문구 |
| 17 | 감사 로그 | 다운로드 엔드포인트 전수 로그 + 파일 쓰기 실패 metadata 레코드 |
| 18 | XLSX 이중 방어 역할 | `strings_to_formulas=False` + sanitize 는 상호보완 (중복 아님) |
| 19 | download_ready 송출 타이밍 | `stream.end` 직후 별도 이벤트 |
| 20 | 대용량 XLSX 동기/비동기 | 1차 릴리즈는 동기 await, 실측 후 background task 분리 |

### 9.4 4차 승인 (2026-04-17 저녁, 구현 착수 전 코드 정합 교정)

| 번호 | 항목 | 결정 |
| --- | --- | --- |
| 21 | 노드 경로 교정 | `sql_tool*.py` → `sql_executor.py`, `formatter*.py` → 실제 경로로 전면 교정 |
| 22 | `save_message` 시그니처 변경 | `message_uuid: str \| None` 파라미터 추가, INSERT SQL 에 `COALESCE(%(message_uuid)s::uuid, gen_random_uuid())` 적용 (§3.16) |
| 23 | runner.py 3개 호출부 명시 | clarification/normal/error 경로별 영향 범위 표 (§3.17). **단일 진입부 생성** 권장 |
| 24 | `PipelineState` 신규 필드 3개 | `download_format`, `message_uuid`, `sql_result_file_meta` (§3.15.1) — sql_executor → formatter → runner 로 흘러가는 경로 명시 |
| 25 | Writer 반환 `SqlFileMeta` TypedDict | `dict[str, Any]` → 명시적 TypedDict (§4.1). 정적 타입 검증 + 실수 방지 |
| 26 | WebSocket payload flat→nested 호환 | `settings.*` 우선 + flat `streaming` fallback (§3.18). 1~2 릴리즈 후 fallback 제거 |

---

## 10. 후속 작업 (본 설계 범위 외)

- 파일 retention 정책 (cron/systemd timer) — 운영팀 담당
- 대용량 결과 스트리밍 다운로드 (Range 요청 지원)
- **SSO 연동 + 접근 제어 강화** (HMAC 토큰 / 세션 기반 / TTL 서명 URL)
- 이번 쿼리만 다른 포맷 요청하는 on-demand 변환 엔드포인트 — 수요 확인 후
- raw rows 기반 심층 분석 시나리오 발생 시 Option A (checkpointer 직렬화 제외) 재설계
- 대용량 XLSX background task 분리 + 지연 download_ready
- Parquet 등 분석 포맷 지원 — 수요 확인 후
