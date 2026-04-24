# Trace 다운로드 버튼 — 대화이력 복원 시 누락 수정

> **작성일**: 2026-04-07
> **관련 항목**: UI 개선 계획 1.3 (Trace/Report 파일 다운로드)
> **심각도**: 기능 결함 — 세션 복원 시 trace 다운로드 버튼이 표시되지 않음
> **리뷰 상태**: 코드 리뷰 완료 — Critical 1건, Warning 2건 반영

---

## 1. 현상

- 실시간 대화 중에는 trace 다운로드 버튼이 정상 표시됨
- 세션 종료 후 대화이력을 다시 열면 trace 다운로드 버튼이 사라짐

## 2. 원인 분석

### 실시간 흐름 (정상)

```text
handler.save()
  → trace_files: [{name, filename}, ...]
    → PipelineResult.trace_files
      → main.py: stream.end WS 메시지에 trace_files 포함
        → UI: ED.handleStream() → msg.traceFiles 저장 → 버튼 활성화
```

### 복원 흐름 (결함)

```text
GET /api/sessions/{id}
  → session_service.get_session_detail()
    → turn_text_store.get_session_turns_for_ui()  ← trace_files 미조회
      → TurnSummary                                ← trace_files 필드 없음
        → UI: loadSession() → MS.create()          ← traceFiles 미전달
          → 버튼 display:none 유지
```

### 누락 지점 3곳

| #   | 위치                          | 누락 내용                                                        |
| --- | ----------------------------- | ---------------------------------------------------------------- |
| 1   | `runner.py` line 403-432      | `save_turn()` 호출 시 `metadata`에 `trace_files` 미포함          |
| 2   | API 응답 모델                 | `TurnSummary`에 `trace_files` 필드 없음                          |
| 3   | UI 복원 로직                  | `loadSession()`에서 `traceFiles` 옵션 미전달                     |

## 3. 설계 방침

### 기존 패턴과의 일관성

기존 코드는 **2-tier 로딩** 패턴을 사용한다:

- **Tier 1** (경량 목록): `get_session_turns_for_ui()` → 기본 필드 + `has_metadata` 플래그
- **Tier 2** (상세): `get_turn_metadata()` → `metadata` JSONB 전체 반환

`trace_files`는 `[{name, filename}, ...]` 형태의 경량 데이터(수십 바이트)이므로
`is_liked`, `is_downloaded`, `has_metadata`와 동일 레벨로 **Tier 1에 포함**해도 부담 없음.

### trace 파일 주기적 삭제 대응

trace 파일은 디스크에서 주기적으로 삭제된다.
DB의 `metadata.trace_files`에 파일명이 남아 있어도 실제 파일은 없을 수 있다.

UI에서 graceful 처리 (서버 404 핸들링):

- `/api/traces/{filename}` 엔드포인트는 이미 파일 미존재 시 404 반환 (`sessions.py` line 170-171)
- UI 다운로드 핸들러에서 404 응답 시 "파일이 만료되어 다운로드할 수 없습니다" 토스트 + 버튼 비활성화
- 복원 시 `trace_files`가 있으면 버튼을 일단 표시, 클릭 시 서버 응답으로 가용성 판단
- DB 정리는 불필요: metadata JSONB 안의 수십 바이트이므로 파일 삭제 시 DB를 따로 갱신할 필요 없음

## 4. 변경 대상 모듈 (5개 파일)

| #   | 파일                               | 변경 내용                                                                         | 변경량 |
| --- | ---------------------------------- | --------------------------------------------------------------------------------- | ------ |
| 1   | `src/agents/graph/runner.py`       | `metadata`에 `trace_files` 추가 저장                                              | 1줄    |
| 2   | `src/services/turn_text_store.py`  | `get_session_turns_for_ui()` SQL에서 `metadata->'trace_files'` 추출               | 1줄    |
| 3   | `src/models/api/session_models.py` | `TurnSummary`에 `trace_files` 필드 추가                                           | 3줄    |
| 4   | `src/services/session_service.py`  | `TurnSummary` 생성 시 `trace_files` 매핑 (JSONB 타입 변환 포함)                   | 3줄    |
| 5   | `static/embedded.html`             | `MS.create()`에 `traceFiles` 필드 추가 + `_downloadTraceFile()` fetch 방식 전환   | ~15줄  |

## 5. 상세 변경

### 5.1 runner.py — metadata에 trace_files 저장

위치: `save_turn()` 호출의 `metadata` dict (line 403-432)

```python
# 변경 전
metadata={
    "trace_log": [...],
    "insight": pipeline_result.insight,
    "visualization": ...,
    "sql_result": {...},
    "executed_sql": ...,
},

# 변경 후
metadata={
    "trace_log": [...],
    "insight": pipeline_result.insight,
    "visualization": ...,
    "sql_result": {...},
    "executed_sql": ...,
    "trace_files": pipeline_result.trace_files,  # ← 추가
},
```

실행 순서 확인 완료 (리뷰에서 검증):

1. `_build_result()` 내부에서 `handler.save()` (line 500) → `trace_files` 생성
2. `PipelineResult(..., trace_files=trace_files)` 반환 (line 511-522)
3. `save_turn()` (line 379-433)에서 `pipeline_result.trace_files`에 접근 가능

따라서 `metadata`에서 로컬 변수 `trace_files`가 아닌 `pipeline_result.trace_files`를 참조해야 한다:

```python
"trace_files": pipeline_result.trace_files,  # pipeline_result 경유 참조
```

### 5.2 turn_text_store.py — Tier 1 SQL에 trace_files 추출

위치: `get_session_turns_for_ui()` (line 180-186)

```sql
-- 변경 전
SELECT turn_id::text AS turn_id, turn_seq, role, content,
       turn_type, is_liked, feedback, is_downloaded, status,
       created_at,
       (metadata != '{}'::jsonb) AS has_metadata
FROM checkpoint_dc_turn_texts
WHERE thread_id = %(thread_id)s
ORDER BY turn_seq

-- 변경 후
SELECT turn_id::text AS turn_id, turn_seq, role, content,
       turn_type, is_liked, feedback, is_downloaded, status,
       created_at,
       (metadata != '{}'::jsonb) AS has_metadata,
       metadata->'trace_files' AS trace_files
FROM checkpoint_dc_turn_texts
WHERE thread_id = %(thread_id)s
ORDER BY turn_seq
```

> JSONB `->` 연산자는 JSONB 타입을 반환. psycopg3 (프로젝트 사용 중: `psycopg[binary]>=3.1.0`)의
> jsonb 어댑터가 Python `list`로 자동 변환한다. `trace_files` 키가 없으면 `NULL` → 파이썬에서 `None`.
> 단, 안전을 위해 `session_service.py`에서 타입 방어 코드를 추가한다 (섹션 5.4 참조).

### 5.3 session_models.py — TurnSummary 필드 추가

위치: `TurnSummary` 클래스 (line 43-56)

```python
class TurnSummary(BaseModel):
    # ... 기존 필드 ...
    has_metadata: bool = False
    created_at: datetime
    trace_files: list[dict[str, str]] = Field(
        default_factory=list,
        description="Trace 파일 목록 [{name, filename}, ...]",
    )
```

### 5.4 session_service.py — 매핑 추가 (JSONB 타입 방어 포함)

위치: `get_session_detail()` 내 TurnSummary 생성 (line 73-86)

```python
import json as _json

# trace_files: psycopg3는 JSONB를 list로 자동 변환하지만,
# 환경에 따라 문자열로 반환될 수 있으므로 방어적으로 처리
_raw_tf = t.get("trace_files")
_trace_files = (
    _json.loads(_raw_tf) if isinstance(_raw_tf, str)
    else _raw_tf or []
)

TurnSummary(
    # ... 기존 매핑 ...
    has_metadata=t["has_metadata"],
    created_at=t["created_at"],
    trace_files=_trace_files,  # ← 추가
)
```

### 5.5 embedded.html — MS.create 필드 추가 + 복원 로직 + 파일 만료 처리

#### 5.5.1 MS.create()에 traceFiles 필드 등록

위치: `MS.create()` 내 msg 객체 초기화 (line 895-909)

현재 `MS.create()`에 `traceFiles` 프로퍼티가 없다. 실시간 흐름에서는 `MS.update()`로
나중에 추가하므로 동작하지만, 복원 시 `MS.create(..., {traceFiles: [...]})` 호출 시
msg 객체에 포함되지 않는다.

```javascript
// msg 객체 초기화에 추가 (line 909 부근, restored 앞에)
traceFiles: x.traceFiles || [],  // ← 추가
restored: x.restored || false
```

> 이 필드가 등록되면, `RD.render()` (line 1208)에서 `msg.traceFiles`를 자동 감지하여
> `renderTraceDownload()`를 호출하므로, `loadSession()`에서 별도의 버튼 활성화 코드가 불필요하다.

#### 5.5.2 loadSession() 복원 시 traceFiles 전달

위치: `loadSession()` 내 `MS.create()` 호출 (line 2056-2064)

```javascript
// 변경 전
var msg = MS.create(t.role, t.content, {
    turnId: t.turn_id || null,
    // ...
    restored: true
});

// 변경 후
var msg = MS.create(t.role, t.content, {
    turnId: t.turn_id || null,
    // ...
    traceFiles: t.trace_files || [],  // ← 추가
    restored: true
});
// RD.render(msg)가 내부적으로 renderTraceDownload()를 호출하므로 별도 처리 불필요
```

#### 5.5.3 _downloadTraceFile() — anchor 방식에서 fetch 방식으로 전환

위치: `_downloadTraceFile()` 함수 (line 1569-1574)

현재 `<a>` 태그 직접 클릭 방식이므로 HTTP 404 응답을 감지할 수 없다.
`fetch()` + blob 방식으로 전면 교체하여 파일 만료 시 사용자에게 안내한다.

```javascript
// 변경 전
function _downloadTraceFile(filename){
    var a=document.createElement('a');
    a.href=API_BASE+'/traces/'+encodeURIComponent(filename);
    a.download=filename;
    document.body.appendChild(a);a.click();a.remove();
}

// 변경 후
function _downloadTraceFile(filename){
    fetch(API_BASE+'/traces/'+encodeURIComponent(filename))
    .then(function(resp){
        if(resp.status===404){
            toast('분석 리포트가 만료되어 다운로드할 수 없습니다.');
            return null;
        }
        if(!resp.ok){
            toast('다운로드에 실패했습니다.');
            return null;
        }
        return resp.blob();
    })
    .then(function(blob){
        if(!blob)return;
        var url=URL.createObjectURL(blob);
        var a=document.createElement('a');
        a.href=url;a.download=filename;
        document.body.appendChild(a);a.click();a.remove();
        URL.revokeObjectURL(url);
    })
    .catch(function(){
        toast('다운로드에 실패했습니다.');
    });
}
```

## 6. 검증 체크리스트

- [ ] 실시간 대화 후 trace 버튼 정상 표시 (기존 동작 유지)
- [ ] 세션 종료 → 재접속 → 대화이력 열기 → trace 버튼 표시
- [ ] trace 파일이 디스크에서 삭제된 경우 → 클릭 시 만료 토스트
- [ ] trace 파일이 없는 턴 (user 턴, 에러 턴) → 버튼 미표시
- [ ] `metadata`에 `trace_files` 키가 없는 기존 데이터 → 정상 동작 (빈 배열 fallback)
- [ ] JSONB `->` 반환 타입 검증: psycopg3에서 `list[dict]`로 변환되는지 확인
- [ ] 명확화 턴, 에러 턴 등 비정상 턴에서 trace 버튼 미표시 확인

## 7. 고려사항

| 항목                 | 설명                                                                                                                        |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 기존 데이터 호환     | 이 변경 이전에 저장된 턴은 `metadata.trace_files`가 없음. `or []` fallback으로 처리                                         |
| DB 마이그레이션      | 불필요. 기존 `metadata` JSONB 컬럼에 키만 추가되는 형태                                                                     |
| 성능 영향            | Tier 1 SQL에 `metadata->'trace_files'` 추가. JSONB 단일 키 추출은 인덱스 없이도 무시할 수 있는 비용                          |
| JSONB 타입 변환      | psycopg3 (`>=3.1.0`) 사용 중. jsonb 어댑터가 Python list로 자동 변환하나, `session_service.py`에서 `isinstance(str)` 방어 포함 |
| 실행 순서            | `_build_result()` 내에서 `handler.save()` → `PipelineResult` 생성 → `save_turn()` 순서. `pipeline_result.trace_files` 접근 가능 (리뷰 검증 완료) |
| 다운로드 방식 전환   | `_downloadTraceFile()`을 anchor 방식에서 fetch+blob 방식으로 전환. 404 핸들링 가능해지나, 함수 동작 방식 자체가 변경됨에 유의  |
| 명확화/에러 턴       | trace 다운로드 불필요. 해당 경로의 `save_turn()`에서는 `trace_files`를 metadata에 포함하지 않으므로 자동 제외                  |
