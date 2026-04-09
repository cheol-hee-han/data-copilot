# Trace Download Feature - Code Review Report

**Date**: 2026-04-07
**Scope**: `turn_text_store.py`, `callback_handler.py`, `sessions.py`, `response.py`, `main.py`, `embedded.html`
**Focus**: 보안 (경로 순회), 타입 안정성, 에러 처리, 이벤트 리스너 정리, XSS

---

## Critical (RED)

### CR-01: trace 다운로드 엔드포인트에 인증/인가 없음

**File**: `src/routers/sessions.py:142-177`

현재 `GET /api/traces/{filename}` 엔드포인트에 어떠한 인증도 적용되어 있지 않다. trace 파일에는 사용자 입력, 생성된 SQL, 실행 결과 요약, 세션/턴 ID, 사용자 ID 등 민감한 정보가 포함된다. 경로 순회 방어는 잘 구현되어 있으나, 파일명만 알면 누구나 다운로드 가능한 상태이다.

**Risk**: 파일명 패턴이 `trace_telemetry_{date}_{userId}_{sessionId}_{turnId}.json`으로 예측 가능하므로, brute force로 다른 사용자의 trace를 열람할 수 있다.

**Recommendation**:
1. 최소한 세션 기반 인증을 추가하여 본인 세션의 trace만 다운로드 가능하도록 제한
2. 파일명에 사용자 ID/세션 ID를 포함시키고, 요청자의 세션과 매칭되는지 검증
3. 또는 trace 파일 목록을 DB에 저장하고 turn_id 기반으로 접근 제어

```python
# 예시: 최소한의 파일명-세션 매칭 검증
@router.get("/traces/{filename}")
async def download_trace(
    filename: str,
    user_id: str = Query("anonymous"),
):
    # ... 기존 경로 순회 방어 ...
    # 파일명에서 user_id 추출하여 요청자와 매칭
    if f"_{user_id}_" not in safe_name:
        raise HTTPException(403, "접근 권한이 없습니다.")
```

---

### CR-02: 경로 순회 방어에 symlink 기반 우회 가능성

**File**: `src/routers/sessions.py:159-166`

`filepath.is_file()` 체크 후 `filepath.resolve().relative_to(trace_dir.resolve())`로 검증하는 순서에 TOCTOU(Time-of-check-time-of-use) 이슈가 있다. 또한 `trace_dir` 자체가 상대 경로(`logs/traces`)이므로, CWD에 따라 예기치 않은 디렉토리를 참조할 수 있다.

**Recommendation**: resolve 검증을 is_file 체크보다 먼저 수행하고, resolve된 경로로 FileResponse를 반환한다.

```python
trace_dir = Path(settings.eval_tracker_output_dir).resolve()
filepath = (trace_dir / safe_name).resolve()

# resolve 후 경로 검증을 먼저 수행
if not filepath.is_relative_to(trace_dir):
    raise HTTPException(400, "잘못된 파일 경로입니다.")

if not filepath.is_file():
    raise HTTPException(404, "파일을 찾을 수 없습니다.")

return FileResponse(path=filepath, ...)
```

Note: `Path.is_relative_to()`는 Python 3.9+에서 사용 가능하며, 기존의 `try/except ValueError` 패턴보다 명확하다.

---

## Warning (YELLOW)

### WN-01: clarification/error 경로에서 trace_files 누락

**File**: `src/agents/graph/runner.py:314, 346-353, 447`

`handler.save()` 반환값이 `list[dict[str, str]]`로 변경되었으나, 두 경로에서 반환값을 사용하지 않는다:

1. **Clarification 경로** (line 314): `handler.save(turn_id=_turn_id)` 호출 후 반환값을 버리고, line 346의 `PipelineResult`에 `trace_files`를 설정하지 않음
2. **Error 경로** (line 447): `handler.save()` 반환값을 버림

명확화 요청 시에도 trace가 생성될 수 있으므로, 사용자에게 디버깅 목적의 trace 파일을 제공하지 못한다.

**Recommendation**:
```python
# clarification 경로 (line 314)
trace_files = handler.save(turn_id=_turn_id)
# ...
clarification_result = PipelineResult(
    response=question,
    awaiting_clarification=True,
    clarification_request=clarification_data,
    trace_files=trace_files,  # 추가
)
```

---

### WN-02: _downloadTraceFile에서 filename에 대한 클라이언트측 검증 부재

**File**: `static/embedded.html:1527-1532`

`_downloadTraceFile(filename)`은 서버에서 받은 `trace_files[].filename` 값을 그대로 URL에 사용한다. `encodeURIComponent`로 URL 인코딩은 하고 있어 XSS 직접 위험은 낮으나, `a.download = filename`에서는 인코딩 없이 원본 filename을 그대로 사용한다.

서버측에서 trace 파일명을 생성하므로 실질적 위험은 낮지만, 방어적 프로그래밍 관점에서 클라이언트에서도 파일명 유효성을 검증하는 것이 좋다.

**Recommendation**:
```javascript
function _downloadTraceFile(filename){
  // 디렉토리 구분자 포함 여부 방어적 검증
  if(!filename || /[\/\\]/.test(filename)) return;
  var a=document.createElement('a');
  a.href=BASE+'/traces/'+encodeURIComponent(filename);
  a.download=filename;
  document.body.appendChild(a);a.click();a.remove();
}
```

---

### WN-03: trace-dropdown 다중 생성 시 이벤트 리스너 누적 가능성

**File**: `static/embedded.html:1520-1525`

드롭다운 외부 클릭 핸들러(`closeDd`)는 드롭다운이 제거될 때 `removeEventListener`로 정리된다. 이 구현 자체는 정상적으로 동작한다. 그러나 빠른 연속 클릭 시 `setTimeout(0)` 내부의 `addEventListener`가 등록되기 전에 새 드롭다운이 생성되면, 이전 리스너가 정리되지 않은 채 남을 수 있다.

**Recommendation**: `setTimeout` 대신 `requestAnimationFrame`을 사용하거나, 모듈 수준에서 현재 활성 드롭다운 참조를 관리한다.

```javascript
var _activeTraceDropdown = null;
function _onTraceDownloadClick(msgId, btn) {
  // 기존 활성 드롭다운 정리
  if (_activeTraceDropdown) {
    _activeTraceDropdown.remove();
    _activeTraceDropdown = null;
  }
  // ... 드롭다운 생성 ...
  _activeTraceDropdown = dd;
}
```

---

### WN-04: trace_files 필드 타입이 느슨함

**File**: `src/agents/models/response.py:46-49`

`trace_files: list[dict[str, str]]`는 구조가 명시적이지 않다. 실제로는 항상 `{"name": str, "filename": str}` 형태이지만, 타입 힌트만으로는 이를 보장할 수 없다.

**Recommendation**: 전용 모델을 정의하여 타입 안정성을 확보한다.

```python
class TraceFileInfo(BaseModel):
    """생성된 trace 파일 정보."""
    name: str = Field(description="파일 표시명 (예: '텔레메트리 (JSON)')")
    filename: str = Field(description="다운로드용 파일명")

class PipelineResult(BaseModel):
    # ...
    trace_files: list[TraceFileInfo] = Field(default_factory=list)
```

이 변경 시 `callback_handler.py`의 `save()` 메서드 반환 타입도 `list[TraceFileInfo]`로 맞춰야 한다. 또는 `callback_handler`는 dict를 반환하고 `runner.py`에서 변환하는 방법도 가능하다.

---

### WN-05: SQL CASE WHEN에서 boolean 파라미터 바인딩 - DB 드라이버 호환성

**File**: `src/services/turn_text_store.py:235, 243`

```sql
CASE WHEN %(has_like)s THEN now() ELSE NULL END
```

`has_like`에 Python `bool`을 바인딩하고 `CASE WHEN <bool>` 형태로 사용한다. psycopg3에서는 Python `bool`이 PostgreSQL `boolean`으로 올바르게 변환되므로 현재는 정상 동작한다.

그러나 프로젝트의 폐쇄망 배포 대상이 Sybase IQ/Impala인 점을 고려하면, 이 패턴은 DB 드라이버에 따라 호환성 문제가 발생할 수 있다. 이전 방식(`%(is_liked)s IS NOT NULL`)이 더 DB 독립적이었다.

**Recommendation**: 현재 PostgreSQL 전용이므로 당장 문제는 없으나, 마이그레이션 가이드에 이 패턴을 기록해두는 것을 권장한다. 또는 DB 독립적인 표현으로 변경한다:

```sql
CASE WHEN %(is_liked)s IS NOT NULL THEN now() ELSE NULL END
```

이 경우 `has_like` 파라미터가 불필요해지므로 파라미터 dict도 단순화된다.

---

## Info (GREEN)

### IN-01: media_type 매핑이 하드코딩됨

**File**: `src/routers/sessions.py:168-172`

현재 `.json`과 그 외(`.md`)만 처리하지만, `callback_handler.py`의 `save()`에서 향후 다른 형식(HTML 보고서 등)이 추가될 수 있다. 

**Recommendation**: 확장 시점에 suffix-to-media_type 매핑을 dict로 관리하면 좋다. 현재 수준에서는 충분하다.

---

### IN-02: trace 파일명에 user_id가 포함되어 있어 정보 노출

**File**: `src/utils/tracker/callback_handler.py:787`

```python
prefix = f"{date_str}_{user_id}_{sid}_{tid}"
```

파일명 자체에 `user_id`가 포함되므로, trace 파일이 외부에 노출될 경우 사용자 식별 정보가 함께 유출된다. CR-01의 인증 이슈와 결합하면 위험이 증가한다.

---

### IN-03: download-trace 버튼 초기 display:none 패턴 일관성

**File**: `static/embedded.html:962`

`download-csv` 버튼과 `download-trace` 버튼 모두 `style="display:none"`으로 시작하여 조건부 표시하는 패턴이 일관적이다. 기존 패턴을 잘 따르고 있다.

---

### IN-04: encodeURIComponent 사용으로 URL 인젝션 방어 충분

**File**: `static/embedded.html:1529`

`encodeURIComponent(filename)`으로 파일명을 인코딩하여 URL 구성에 사용하므로, URL 기반 인젝션은 차단된다. `textContent`로 드롭다운 항목 텍스트를 설정하므로 DOM XSS도 방어되어 있다.

---

## Summary

| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 2 | 인증 부재, TOCTOU 경로 검증 순서 |
| Warning  | 5 | trace_files 누락 경로, 타입 느슨함, 이벤트 리스너, DB 호환성 |
| Info     | 4 | media_type 하드코딩, 파일명 정보 노출, 일관성 확인 |

### Priority Action Items

1. **(Must)** CR-01: trace 엔드포인트에 최소한의 접근 제어 추가
2. **(Must)** CR-02: resolve 검증 순서 수정 (TOCTOU 방어)
3. **(Should)** WN-01: clarification 경로에서 trace_files 전달
4. **(Should)** WN-04: TraceFileInfo 모델 정의로 타입 강화
5. **(Nice)** WN-05: 폐쇄망 마이그레이션 가이드에 boolean 바인딩 패턴 기록
