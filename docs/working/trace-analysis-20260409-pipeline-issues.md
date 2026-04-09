# Trace 분석: 2026-04-09 파이프라인 전체 이슈 분석

- **분석 대상**: 2026-04-09 실행된 2건의 질의 트레이스
- **분석일**: 2026-04-09
- **분석 범위**: Reasoning 실패 패턴, SQL 생성/검증 오류, UI/WebSocket 문제, sqlglot 포맷 경고

---

## 분석 대상 파일

| 파일 | 경로 |
|------|------|
| 앱 로그 | `logs/app.log` |
| 에러 로그 | `logs/error.log` |
| 질의1 텔레메트리 | `logs/traces/trace_telemetry_20260409_anonymous_session-1775670490267_22bec0dec2df.json` |
| 질의1 리포트 | `logs/traces/trace_report_20260409_anonymous_session-1775670490267_22bec0dec2df.md` |
| 질의1 리즈닝 | `logs/traces/trace_reasoning_20260409_anonymous_session-1775670490267_22bec0dec2df.md` |
| 질의2 텔레메트리 | `logs/traces/trace_telemetry_20260409_anonymous_session-1775579942931_cdb7e0083025.json` |
| 질의2 리포트 | `logs/traces/trace_report_20260409_anonymous_session-1775579942931_cdb7e0083025.md` |
| 질의2 리즈닝 | `logs/traces/trace_reasoning_20260409_anonymous_session-1775579942931_cdb7e0083025.md` |

---

## 질의 요약

| 구분 | 질의1 (70490267) | 질의2 (79942931) |
|------|------------------|------------------|
| **질의** | "연령대별 남자 평균 여신 잔액 알려줘" | "이번년도 예금신규 top 10 지점 알려줘" |
| **결과** | ❌ 실패 (error_end) | ⚠️ SQL 성공 (10건), UI 표시 실패 |
| **소요** | 334.0s | 103.3s |
| **LLM 호출** | 45회 / 384,714 tok | 22회 / 179,110 tok |
| **의도 분류** | data_analysis (95%) | data_extraction (95%) |
| **SQL 시도** | 4회, 전부 실패 | 4회, 마지막 통과 |

---

## 1. sqlglot TO_CHAR 포맷 경고

### 현상

유사 SQL 이력에 포함된 `TO_CHAR(OPEN_DT, 'YYYY-MM')` 구문을 sqlglot으로 transpile할 때 경고 발생:

```
Argument 'format' is not supported for expression 'ToChar' when targeting Dialect.
```

### 발생 위치

- **코드**: `src/utils/truncate.py:56-58` — `format_sql()` 함수
  ```python
  results = sqlglot.transpile(
      sql, read=_dialect, write=_dialect, pretty=True,
  )
  ```
- **호출 지점**:
  - `src/agents/nodes/reason/sql_generator.py:330` — SQL 생성 로그
  - `src/agents/nodes/present/sql_executor.py:55` — SQL 실행 로그
- **데이터 출처**: Qdrant sql_history 컬렉션의 유사 SQL에 `TO_CHAR` 사용 SQL 다수 존재
  - `trace_telemetry_*_cdb7e0083025.json` 내 use_case SQL 10건 이상에서 확인

### 영향도

- **LOW** — 로그/트레이스 기록용 pretty-print에서만 발생, SQL 실행에 영향 없음
- `format_sql()`은 실패 시 원본 SQL 그대로 반환하므로 기능 장애 아님
- 단, 콘솔에 반복 warning 출력되어 로그 가독성 저하

### 원인

sqlglot이 postgres dialect에서 `TO_CHAR(column, 'format')` 구문의 2번째 인자(format string)를 정식 지원하지 않음.

---

## 2. UI: 테이블 미렌더링 (데이터는 성공적으로 조회)

### 현상

질의2 "이번년도 예금신규 top 10 지점"에서 SQL 실행 성공(10건), 텍스트 요약만 표시되고 데이터 테이블이 화면에 나타나지 않음.

### 로그 근거

```
# logs/app.log:4030-4038
[02:55:42.887] SQL 실행 성공
  sql: SELECT T2.BLNG_BRCD AS 지점코드, T2.BR_NM AS 지점명, COUNT(*) AS 신규계좌개설건수
       FROM ADWOWN.TB_ADW_DEP201P T1
       INNER JOIN ADWOWN.TB_ADW_COM001M T2 ON T1.BLNG_BRCD = T2.BLNG_BRCD
       WHERE T1.OPEN_DT >= DATE_TRUNC('year', CURRENT_DATE)
       GROUP BY T2.BLNG_BRCD, T2.BR_NM
       ORDER BY COUNT(*) DESC LIMIT 10
  row_count: 10
  latency_ms: 5.6

[02:55:42.893] 결과 포맷팅 시작
[02:55:42.894] 결과 포맷팅 완료
  response_length: 33          ← 텍스트 요약만의 길이 (정상 — result_data는 별도 필드)
```

### 데이터 전달 구조

서버 측 전송 흐름 (`src/main.py:461-496`):

1. **`stream.start`** (line 462-466): `{"type":"stream","action":"start","label":"답변 작성 중"}`
2. **`stream.chunk`** (line 468-472): `{"type":"stream","action":"chunk","text":"총 10건 조회되었으며..."}` — 33자 텍스트
3. **`stream.end`** (line 477-496):
   ```json
   {
     "type": "stream",
     "action": "end",
     "status": "success",
     "result_data": { "columns": [...], "rows": [...], ... }
   }
   ```
   - `result_data`는 `stream.end` 메시지에 포함 (line 489-490)

프론트엔드 수신 (`static/embedded.html`):

- **line 1885-1886**: `if(data.result_data) m2.resultData = data.result_data;`
- **line 1888-1895**: DOM에서 `.msg-bubble` 슬롯을 찾아 `renderResultTable()` 호출

### 근본 원인 분석 (서버+프론트엔드 협업 결과)

#### 원인 1 (추정): `stream.end` 메시지가 프론트엔드에 도달하지 않음

> **[비판적 검토 보정]**: 화면에 텍스트 요약이 표시되므로 `stream.chunk`는 정상 전송됨.
> 따라서 경로 A(`_ws_closed`)와 경로 B(조기 return)는 **이번 건에서는 가능성이 낮음**.
> 가장 유력한 시나리오는 **경로 C(JSON 직렬화 실패)** 또는 **원인 3(프론트엔드 예외)**.
> 단, 서버 로그에 stream.end 전송 성공/실패 기록이 없어 **단정 불가** — 브라우저 콘솔 로그 확인 필요.

**서버 측 — `_safe_send()` 실패 경로** (`src/main.py:396-401`):

```python
async def _safe_send(msg: dict[str, Any]) -> bool:
    ...
    try:
        await websocket.send_json(msg)
        return True
    except (WebSocketDisconnect, RuntimeError):  # ← TypeError 미포함!
        _ws_closed = True
```

- **경로 A — `_ws_closed` 플래그** (`main.py:446-447`): 파이프라인 실행 중 WebSocket이 끊기면 `_ws_closed=True`가 설정되어, 파이프라인 완료 후 `stream.start/chunk/end` **전체가 스킵**됨. ⚠️ **이번 건에서는 stream.chunk 전송 성공이 확인되어 가능성 낮음**
- **경로 B — 조기 return** (`main.py:462-473`): `stream.start` 또는 `stream.chunk` 전송 실패 시 `return`으로 함수 종료 → **stream.end 전송 없이 함수 종료**. ⚠️ **이번 건에서는 stream.chunk 성공이므로 해당 없음**
- **경로 C — JSON 직렬화 실패** (`main.py:396`): `result_data`에 `datetime`/`Decimal` 등 비직렬화 타입 포함 시 `TypeError` 발생 → `_safe_send()`가 `TypeError`를 catch하지 않아 상위로 전파 → stream.end 누락. ⚠️ **이번 건에서 가장 유력한 서버 측 시나리오**

**로그 증거**: 파이프라인 완료(02:55:43) → WebSocket 종료(02:56:29) 사이 **46초 갭**, 이 구간에 stream.end 전송 성공/실패 로그 없음 (`_safe_send` 성공 시 로그 없고, 실패 시 debug 레벨이라 출력 안 됨). **stream.end가 전송되었는지 여부를 로그만으로는 판단할 수 없음.**

#### 원인 2 (추정): `_cancelled` 플래그 오염 — `stream.end` 무시

> **[비판적 검토 보정]**: 이 시나리오는 사용자가 실제로 중지 버튼을 눌렀을 때만 발생함.
> 이번 건에서 사용자가 중지 버튼을 눌렀는지는 **확인 불가**. 구조적 결함으로서는 유효하나
> 이번 사건의 직접 원인인지는 **단정 불가**.
> 참고: `forceFinish()` → `_ffc()` 호출 → `_cur=null` 설정은 수행되지만,
> `setBusy(false)`는 호출되지 않음.

**프론트엔드 — cancelled 가드** (`static/embedded.html` line 1813):

```javascript
if(_cancelled){
    if((data.type==='stream'&&data.action==='end')||data.type==='response'){
        _cancelled=false;  // 플래그만 해제
    }
    return;  // ← stream.end 메시지 전체를 무시하고 return!
}
```

사용자가 중지 버튼을 누르면 `forceFinish()` (line 1959)가 `_cancelled=true`로 설정. `forceFinish()`는 내부적으로 `_ffc()`를 호출하여 `_cur=null`을 설정하지만 `setBusy(false)`는 호출하지 않음. 이후 서버가 이미 처리를 완료했더라도 `stream.end`가 약간 지연되어 도착하면:
- `_cancelled=false`만 설정하고 즉시 `return`
- `_cur`이 이미 null이므로 line 1916의 `setBusy(false)`도 도달 불가
- `renderResultTable()` 미호출 → 테이블 안 보임
- `IC2.setBusy(false)` 미호출 → 중지 버튼 잔류

#### 원인 3 (추정 — 유력): `handleStream` end 블록 내 예외 발생

**프론트엔드 — onmessage catch** (`static/embedded.html` line 1995):

```javascript
ws.onmessage=function(e){
    try{ED.handle(JSON.parse(e.data));}
    catch(err){console.error(err);}  // ← setBusy(false) 미호출!
};
```

`handleStream` end 블록(line 1879-1917) 실행 중 어떤 예외든 발생하면:
- `catch`에서 `console.error`만 실행
- `renderResultTable` 이전/도중에 예외 → 테이블 미렌더링
- `IC2.setBusy(false)` (line 1916) 도달 전 예외 → 버튼 잔류

**증거**: 텍스트 요약은 보임(`SE.finalize` line 1881에서 렌더링) → `.msg-bubble` 존재 확인됨. 이후 line 1882-1916 사이에서 예외 발생 시 텍스트만 보이고 테이블/버튼은 미처리.

> **[비판적 검토 보정]**: stream.chunk 전송이 확인된 상황에서 서버 측 경로 A/B보다
> 이 원인(프론트엔드 예외) 또는 원인 1의 경로 C(JSON 직렬화 실패)가 더 유력함.
> **정확한 근본 원인 특정을 위해 브라우저 콘솔 로그 확인 및 동일 조건 재현 테스트 필요.**

### result_data 생성은 정상

- `src/agents/nodes/present/formatter.py:51-72` — `_build_result_data()`는 `columns`와 `rows`가 빈 리스트가 아닌 한 정상 생성
- 로그에서 `row_count: 10` 확인 → `result_data`는 서버에서 정상 생성됨
- `response_length: 33`은 `build_summary_line()`의 텍스트 요약 길이이며 `result_data`와 독립적 (정상)

### 영향도

- **HIGH** — 사용자가 조회 성공한 데이터를 볼 수 없음

---

## 3. UI: 중지 버튼이 서버 완료 후에도 활성 상태 유지

### 현상

서버에서 파이프라인 완료 후에도 프론트엔드의 중지(■) 버튼이 전송(➤) 버튼으로 복원되지 않음.

### 로그 근거

```
# logs/app.log
[02:55:43.463] [79942931] 파이프라인 실행 완료     ← 서버 완료
[02:56:29.341] WebSocket 연결 종료                  ← 46초 후 연결 종료
  session_id: session-1775579942931
```

서버 완료 ~ WebSocket 종료 사이 **46초 갭** — stream.end 전송 성공/실패 로그 없음. **이 갭만으로는 stream.end가 전송되었는지 여부를 판단할 수 없음** (추정).

### 버튼 상태 전환 메커니즘

- **setBusy(true)**: `.cancel` 클래스 추가, 정지 아이콘 표시 (`static/embedded.html` line 1975-1977)
- **setBusy(false)**: `.cancel` 클래스 제거, 전송 아이콘 복원 (line 1979-1981)

`setBusy(false)` 호출 경로 전체 목록:

| 라인 | 위치 | 조건 |
|------|------|------|
| 1800 | `_sbt()` (30초 타임아웃) | `_cur` 존재 시 타이머 만료 |
| 1855 | `handleLegacy` 완료 | legacy 응답 처리 |
| **1916** | **`handleStream` end** | **`data.action==='end' && _cur`** |
| 1947 | `handleError` | error 메시지 수신 |
| 2205 | 세션 삭제 시 | 활성 세션 삭제 |
| 2241 | 세션 전환 시 | 다른 세션으로 전환 |
| 2469 | 전송 실패 시 | `CN.send()` 반환값 false |

### 근본 원인 분석 (서버+프론트엔드 협업 결과)

#2(테이블 미렌더링)과 **동일 근본 원인 (추정)**. `stream.end` 미수신/미처리 시 `setBusy(false)`가 호출되지 않음.

> **[비판적 검토 보정]**: §2와 동일하게, 정확한 원인은 브라우저 콘솔 로그 없이 단정 불가.
> 아래 구조적 결함은 이번 건의 직접 원인 여부와 무관하게 **수정이 필요한 방어 누락**임.

추가로 발견된 **프론트엔드 측 구조적 결함 3건**:

#### 결함 1: `ws.onclose`에서 `setBusy(false)` 미호출 (`static/embedded.html` line 1996)

```javascript
ws.onclose=function(){
    if(_intentionalClose){_intentionalClose=false;return;}
    setStatus('disconnected');_setOffline(true);
    if(att<5){setTimeout(connect,1000*Math.pow(2,att));att++;}
    else{RD.showBanner('error','서버 연결이 끊어졌습니다...');}
};
```

WebSocket 연결이 끊어지면 `stream.end`를 받을 수 없는데, `setBusy(false)`를 호출하지 않음 → 30초 타임아웃(`_sbt`) 만료까지 중지 버튼이 남아있음.

#### 결함 2: `_cancelled` 가드에서 UI cleanup 누락 (`static/embedded.html` line 1813)

```javascript
if(_cancelled){
    if((data.type==='stream'&&data.action==='end')||data.type==='response'){
        _cancelled=false;
    }
    return;  // ← _cur=null, setBusy(false) 없이 return
}
```

`stream.end` 수신 시 `_cancelled=false`만 설정하고 `return` → `setBusy(false)` 미호출. 단, `forceFinish()` → `_ffc()` 호출 시 `_cur`은 이미 null로 설정됨. **이 결함은 사용자가 중지 버튼을 눌렀을 경우에만 해당** (이번 건에서는 확인 불가).

#### 결함 3: `onmessage` catch 블록에서 UI 복원 없음 (`static/embedded.html` line 1995)

```javascript
ws.onmessage=function(e){
    try{ED.handle(JSON.parse(e.data));}
    catch(err){console.error(err);}  // ← setBusy(false) 미호출
};
```

`handleStream` end 블록 내 어디서든 예외 발생 → `setBusy(false)` 미도달.

### 서버 측 구조적 결함 2건

#### 결함 4: `_safe_send()` 예외 처리 범위 불충분 (`src/main.py:396-401`)

```python
except (WebSocketDisconnect, RuntimeError):  # TypeError 미포함
```

`TypeError`(JSON 직렬화 실패), `ConnectionResetError`, `OSError` 등이 `_safe_send`를 뚫고 나가면 `_run_ws_pipeline` 전체가 예외로 종료되며 stream.end 누락.

#### 결함 5: stream.end 전송이 try-finally로 보장되지 않음 (`src/main.py:462-496`)

```python
# 현재: 각 단계 실패 시 return으로 종료
if not await _safe_send(stream_start): return   # stream.end 전송 안 됨
if not await _safe_send(stream_chunk): return   # stream.end 전송 안 됨
if not await _safe_send(end_msg): return        # 여기서야 시도
```

`stream.start`나 `stream.chunk` 전송 실패 시 `stream.end`가 전송되지 않음. 프론트엔드는 stream.end를 기다리며 영원히 대기. ⚠️ **이번 건에서는 stream.chunk 전송이 성공했으므로 이 경로가 이번 사건의 원인일 가능성은 낮음. 그러나 구조적 결함으로서 수정 필요.**

### 46초 갭의 원인 (추정)

> **[비판적 검토 보정]**: 이 46초 갭은 **양방향 해석 가능**하므로 단정 불가.
> stream.end가 전송되었지만 프론트엔드에서 예외로 무시되었을 수도 있고,
> stream.end 전송 자체가 실패했을 수도 있음. 로그에 어느 쪽 증거도 없음.

02:55:43 ~ 02:56:29 사이 46초의 가능한 시나리오:
- **시나리오 A**: 서버 종료 시그널로 WebSocket이 닫히면서 stream.end 전송 실패
- **시나리오 B**: stream.end는 정상 전송되었으나 프론트엔드에서 예외/cancelled 가드에 의해 무시됨
- **시나리오 C**: stream.end 직렬화 실패(TypeError)로 전송 자체 미수행
- 02:56:29 시점에 "WebSocket 연결 종료", "Checkpointer 리소스 정리", "서버 종료"가 연달아 기록 (`logs/app.log:4047-4052`)

### 영향도

- **HIGH** — 사용자가 대화가 아직 진행 중이라고 오인, 추가 질의 불가

---

## 4. Reasoning 실패 분석: 질의1 "연령대별 남자 평균 여신 잔액"

> 경로: intent_classifier → normalize_query → preparer → retriever → interpreter → gate → **REPLAN** → ... → **error_end**
>
> — `trace_reasoning_*_22bec0dec2df.md:4`

### 4.1 Round 1 (H1): 초기 탐색 — readiness 47% → replan

**리즈닝 파일 line 58-155**

| 단계 | 내용 |
|------|------|
| Preparer | KI 3건: measure:잔액, filter:성별=['남성'], filter:계좌상태=['유효'] — 전부 UNRESOLVED |
| Retriever | search_use_cases + search_table_meta 실행 |
| Interpreter | TB_ADW_LNB301M **SELECTED** (여신잔액), 나머지 4건 REJECTED |
| | **인사이트**: "연령대 및 성별 정보를 위한 테이블이 누락. 고객 정보 테이블 확인 필요" |
| | KI 갱신: 잔액→PROBABLE, 성별→**CANDIDATE**, 계좌상태→PROBABLE |
| Gate | knowledge=0/3, tables=10, score=47% → **replan** |

**문제 A: 고객 테이블 미발견**
- 여신(LNB301M)은 찾았으나, 성별/연령대를 가진 고객 마스터(CSC101M)는 초기 키워드 `"연령대 성별 대출 잔액 평균_여신_잔액"`로 검색했을 때 상위 10건에 포함되지 않음

### 4.2 Round 2 (H2): 고객 마스터 탐색 — SQL 생성 거부

**리즈닝 파일 line 156-351**

| 단계 | 내용 |
|------|------|
| Recovery | 가설: "고객 마스터에 연령/성별 존재" → search_table_meta("고객 정보") |
| Interpreter | TB_ADW_CSC101M **SELECTED** — GNDR_DCD(성별), AGE_GRP_CD(연령대) 확인 |
| Gate | readiness 70% → **generate_sql** |
| Generator | **SQL 생성 거부** — GNDR_DCD/AGE_GRP_CD/LN_STCD 코드값 매핑 없음 |

**문제 B: 코드값 매핑 실패 (TYPE-2 불완전성)**
- `error.log:6-12`: GNDR_DCD(성별) 코드값 매핑 없음, AGE_GRP_CD(연령대) 의미 불명, LN_STCD(여신상태) 유효 코드값 매핑 없음
- MongoDB 코드 메타에 GNDR_DCD 상세 코드값이 **미등록** → `lookup_code_meta("GNDR_DCD")` 빈 결과

### 4.3 Round 3 (H3): 코드 메타 직접 조회 — readiness 56% → replan

**리즈닝 파일 line 456-550**

| 단계 | 내용 |
|------|------|
| Recovery | 가설: "코드 테이블로 정의되어 있다" → lookup_code_meta(GNDR_DCD, LN_STCD) + search_biz_terms("연령대 코드") |
| Retriever | lookup_code_meta(GNDR_DCD) → **코드값 상세 누락** |
| | lookup_code_meta(LN_STCD) → '01: 정상' 확인 → filter:정상여신 **CONFIRMED** |
| Gate | knowledge=1/5, score=56% → **replan** |

**문제 C: GNDR_DCD 코드 메타 부재**
- LN_STCD는 코드 메타에서 '01=정상' 확인 성공
- GNDR_DCD는 코드 메타 자체에 등록되지 않아 lookup 실패 → KI가 CANDIDATE에 정체

### 4.4 Round 4 (H4): 컬럼 프로파일링 시도 — SQL 0건 반환

**리즈닝 파일 line 555-900**

| 단계 | 내용 |
|------|------|
| Recovery | 가설: "실제 컬럼에 의미 있는 값 존재" → get_column_values(GNDR_DCD, AGE_GRP_CD, LN_STCD) |
| Interpreter | **get_column_values 결과가 KI 갱신에 반영되지 않음** — KI 상태 변화 없음 |
| Gate | knowledge=1/5, score=56% → replan_count=3이므로 **generate_sql** 진입 |
| Generator | SQL 생성 성공: `WHERE A.STD_DT = '20260409' AND B.GNDR_DCD = '1' AND A.LN_STCD = '01'` |
| Validator | L1 PASS, L2a PASS, **L3 FAIL (0건)**, L2b FAIL (structural) |

**문제 D: STD_DT 하드코딩**
- `error.log:15-18`: STD_DT='20260409' 하드코딩 → 해당 날짜에 데이터 미적재 → 0건
- 정보계 DB는 통상 전일 기준 적재이므로 당일 날짜로는 항상 0건
- Validator 피드백: "MAX(STD_DT) 서브쿼리를 사용하라" — Recovery가 이를 반영 못함

**문제 E: get_column_values 결과 미반영**
- 도구 실행은 성공했으나 Interpreter가 결과를 KI 상태 갱신에 사용하지 않음
- 리즈닝 파일 line 727-732: KI 상태가 Round 3과 동일 (filter:남자 여전히 CANDIDATE)

### 4.5 Round 5+ : 교착 루프 → error_end

**리즈닝 파일 line 900 이후 (동일 패턴 반복)**

- 같은 테이블(LNB301M + CSC101M), 같은 미해소 항목(GNDR_DCD 코드값)
- SQL 생성 → `GNDR_DCD='1'` + `STD_DT='20260409'` → 0건 반환 → 반복
- `error.log:20-88`: 동일 structural 실패 8회 반복
- 최종 `error_end` — 45회 LLM 호출, 384K 토큰 소진

**문제 F: Recovery 교착 루프 탈출 실패**
- dead_end 기록에 이전 실패 사유가 누적되지만, 새로운 전략 도출 없이 동일 패턴 반복
- STD_DT 문제와 GNDR_DCD 문제가 동시에 존재하여 둘 다 해결 못하는 교착

---

## 5. Reasoning 실패 분석: 질의2 "이번년도 예금신규 top 10 지점"

> 경로: intent_classifier → normalize_query → preparer → retriever → interpreter → gate → **REPLAN** → ... → result_finalizer → executesql → formatresponse
>
> — `trace_reasoning_*_cdb7e0083025.md:4`

### 5.1 Round 1 (H1): 초기 탐색 — readiness 0% → replan

**리즈닝 파일 line 58-160**

| 단계 | 내용 |
|------|------|
| Preparer | KI 1건: measure:신규 (UNRESOLVED) |
| Retriever | search_use_cases + search_table_meta 실행 |
| Interpreter | **10개 테이블 전부 REJECTED** |
| | 인사이트: "TB_ADW_DEP201P의 OPEN_DT 컬럼이 가장 적합" — 그러나 **SELECTED 안 함** |
| Gate | knowledge=0/1, tables=10, score=**0%** → **replan** |

**문제 G: Interpreter가 "적합"이라 분석하면서 SELECTED 안 하는 모순**
- 리즈닝 파일 line 128-131: 인사이트에서 DEP201P가 적합하다고 명시
- 리즈닝 파일 line 113-121: 그런데 테이블 선정에서 10건 전부 ❌ REJECTED
- DEP201P는 유사 SQL 이력에서 참조된 테이블이지 search_table_meta 결과에 포함된 테이블이 아니어서, Interpreter가 SELECTED 대상으로 간주하지 않은 것으로 추정

### 5.2 Round 2 (H2): 계좌 마스터 탐색 — SQL 생성 거부

**리즈닝 파일 line 200-320**

| 단계 | 내용 |
|------|------|
| Recovery | 가설: "계좌 마스터에 OPEN_DT 존재" → search_table_meta("계좌 마스터") |
| Interpreter | TB_ADW_DEA203M, DEA206M 등 확인, 다시 **전부 REJECTED** |
| | 인사이트: "DEP201P에 OPEN_DT 존재" 반복 언급 |
| Gate | readiness 70% (replan_count=1로 threshold 하향) → **generate_sql** |
| Generator | **SQL 생성 거부** — 예금 신규 테이블/지점 테이블 미확인 |

**문제 H: 반복 REJECTED에도 불구하고 readiness가 70%로 진입**
- `error.log:89-94`: "예금 신규 데이터를 관리하는 테이블 미확인", "지점 정보 테이블 미확인"
- replan_count=1이 되면서 threshold가 낮아져 generate_sql 진입했으나, 실제 정보는 부족

### 5.3 Round 3 (H3): 부점 마스터 발견 — SQL L1 실패

**리즈닝 파일 line 400-550**

| 단계 | 내용 |
|------|------|
| Recovery | 가설: "계좌마스터 + 부점 마스터 존재" → search_table_meta("계좌마스터") + search_table_meta("부점") |
| Interpreter | TB_ADW_COM001M **SELECTED** (부점 마스터) |
| | KI: table:TB_ADW_DEA203M → **CONFIRMED**, table:TB_ADW_COM001M → **CONFIRMED** |
| Generator | SQL 생성: `FROM TB_ADW_DEA203M T1 JOIN TB_ADW_COM001M T2 ...` |
| Validator | **L1 FAIL: "미확인 테이블: TB_ADW_DEA203M"** |

**문제 I: CONFIRMED 테이블이 SELECTED 목록에 없어서 Validator에서 거부**
- `error.log:95-96`: "사용할 테이블 목록에 없는 테이블"
- KI에서 CONFIRMED로 확정했지만, Interpreter의 SELECTED 목록에는 DEA203M이 없음
- Validator는 SELECTED 목록의 테이블만 허용 → CONFIRMED ≠ SELECTED 불일치

**두 번째 시도에서도 생성 거부:**
- `error.log:97-102`: "DEP계좌마스터 테이블은 제시된 테이블 목록에 없음"
- `error.log:103-108`: "소속부점코드(BLNG_BRCD) 조인 관계 확인 불가"

### 5.4 Round 4+ : 최종 성공 — DEP201P + COM001M

**리즈닝 파일 이후 / app.log:4030-4035**

- 추가 탐색 라운드에서 DEP201P가 SELECTED 목록에 최종 포함
- 최종 SQL: `DEP201P JOIN COM001M ON BLNG_BRCD` + `DATE_TRUNC('year', CURRENT_DATE)` + `LIMIT 10`
- 실행 성공: 10건, 5.6ms

**중간 실패도 STD_DT 문제 동반:**
- `error.log:111-115`: "고정된 STD_DT 조건으로 인해 데이터 조회에 실패함"
- 최종 성공 SQL에서는 `DATE_TRUNC('year', CURRENT_DATE)` 사용으로 해결

---

## 6. 구조적 이슈 종합

### CRITICAL

| # | 영역 | 문제 | 근거 |
|---|------|------|------|
| C1 | Interpreter | 인사이트에서 "적합"이라 분석한 테이블을 SELECTED 안 함 | 질의2 trace_reasoning line 113-131 |
| C2 | Interpreter/Validator | CONFIRMED KI 테이블이 SELECTED 목록에 자동 반영되지 않아 Validator에서 거부 | 질의2 error.log line 95-96, trace_reasoning line 466-469 |
| C3 | SQL Generator | STD_DT 하드코딩(당일 날짜) → 0건 반복, MAX(STD_DT) 서브쿼리 전략 미적용 | 질의1 error.log line 15-18, 질의2 error.log line 111-115 |
| C4 | Recovery Agent | 동일 실패 패턴 교착 루프 — 새 전략 없이 동일 탐색 반복 | 질의1 error.log line 6-88 (동일 패턴 8회) |

### HIGH

| # | 영역 | 문제 | 근거 |
|---|------|------|------|
| H1 | 코드 메타 | GNDR_DCD 등 핵심 코드값이 MongoDB에 미등록 (TYPE-2 불완전성) | 질의1 trace_reasoning line 522, error.log line 6-12 |
| H2 | Interpreter | get_column_values 도구 결과가 KI 상태 갱신에 반영 안 됨 | 질의1 trace_reasoning line 727-732 vs line 510-516 |
| H3 | UI (stream.end) | result_data 포함된 stream.end 메시지가 프론트엔드에서 정상 처리 안 됨 → 테이블 미표시 | app.log line 4038 (response_length:33), 캡처 증거 |
| H4 | UI (stream.end) | stream.end 미수신/미처리 → setBusy(false) 미호출 → 중지 버튼 잔류 | app.log line 4046 vs 4047 (46초 갭), 캡처 증거 |

### MEDIUM

| # | 영역 | 문제 | 근거 |
|---|------|------|------|
| M1 | 리소스 | 질의1: 45 LLM 호출/385K tok (5.5분), 질의2: 22 호출/179K tok — 단순 질의 대비 과다 | trace_report 각 파일 line 1-8 |
| M2 | Readiness Gate | replan_count 증가 시 threshold 하향으로 정보 부족 상태에서 generate_sql 진입 | 질의2 trace_reasoning line 285-292 (readiness 70%, knowledge 0/1) |

### LOW

| # | 영역 | 문제 | 근거 |
|---|------|------|------|
| L1 | sqlglot | TO_CHAR format 인자 transpile 경고 | src/utils/truncate.py:56-58, trace_telemetry_*_cdb7e0083025.json 내 use_case SQL |

---

## 7. 개선 방향

### Reasoning 관련

| 이슈 | 개선 방향 |
|------|-----------|
| C1, C2 | Interpreter에서 인사이트 분석 결과와 SELECTED 판정 간의 일관성 강화. CONFIRMED KI 테이블을 SELECTED에 자동 포함하는 로직 추가 |
| C3 | SQL Generator 프롬프트에 STD_DT 처리 전략 명시: `WHERE STD_DT = (SELECT MAX(STD_DT) FROM table)` 패턴 의무화 |
| C4 | Recovery 교착 감지: 동일 실패 사유 3회 이상 반복 시 사용자 명확화 질문으로 탈출 |
| H1 | MongoDB 코드 메타에 GNDR_DCD, AGE_GRP_CD 등 핵심 코드값 등록 (seed 데이터 보강) |
| H2 | get_column_values 결과를 Interpreter가 KI 상태 갱신에 명시적으로 반영하도록 프롬프트/로직 수정 |
| L1 | `format_sql()`에서 sqlglot transpile 시 `unsupported_messages="ignore"` 옵션 추가 또는 warning suppress |

### UI/WebSocket 관련

#### 서버 측 수정 (`src/main.py`)

| 우선순위 | 대상 | 현재 문제 | 수정 방향 |
|----------|------|-----------|-----------|
| 🔴 | `_safe_send()` (line 396-401) | `except (WebSocketDisconnect, RuntimeError)` — `TypeError` 등 미포함 | `except Exception`으로 확장하여 JSON 직렬화 실패 등 모든 예외를 포괄 |
| 🔴 | stream.end 전송 보장 (line 462-496) | stream.start/chunk 실패 시 `return` → stream.end 미전송 | stream.start 전송 후에는 stream.end를 반드시 시도하는 구조로 변경 |
| 🟡 | stream.end 전송 로그 (line 477-496) | stream.end 전송 성공/실패에 대한 로그 없음 | `_safe_send(end_msg)` 결과를 INFO 레벨로 기록 |
| 🟡 | `_safe_send` 로그 레벨 (line 401) | 실패 시 `logger.debug` → 운영 환경에서 보이지 않음 | `logger.warning`으로 상향 |
| 🟡 | 에러 발생 시 stream.end (line 580-604) | `except Exception` 블록에서 error 메시지만 전송, stream.end 미전송 | error 메시지와 함께 `{"type":"stream","action":"end","status":"error"}` 전송 |

#### 프론트엔드 수정 (`static/embedded.html`)

| 우선순위 | 대상 | 현재 문제 | 수정 방향 |
|----------|------|-----------|-----------|
| 🔴 | `_cancelled` 가드 (line 1813) | stream.end 수신 시 `_cancelled=false`만 설정 후 `return` → `setBusy(false)`, `_cur=null` 미실행 | cancelled 가드 내에서 stream.end 수신 시 `_cur=null; IC2.setBusy(false);`도 실행 |
| 🔴 | `ws.onclose` (line 1996) | WebSocket 끊김 시 `setBusy(false)` 미호출 → 30초 타임아웃까지 버튼 잔류 | `onclose` 핸들러에서 `IC2.setBusy(false)` 호출 추가 |
| 🔴 | `ws.onmessage` catch (line 1995) | `ED.handle` 예외 시 `console.error`만 실행 → UI 잠금 | catch 블록에서 `IC2.setBusy(false)` 호출 추가 |
| 🟡 | `renderResultTable` 디버그 (line 1678) | slot/rd 누락 시 무음 반환 → 디버깅 불가 | `console.warn` 추가하여 렌더링 실패 원인 추적 가능하게 |
| 🟡 | `RD.render`의 innerHTML 전체 교체 (line 1184) | `bub.innerHTML = mdRender(msg.text)` → 이후 `render` 재호출 시 append된 테이블 파괴 가능 | resultData/processSummary를 render 내부에서 통합 렌더링하거나, 테이블을 별도 컨테이너에 배치 |

---

## 8. 분석 한계 및 검증 권고

> 본 문서의 §2, §3 근본 원인 분석은 서버 로그와 코드 정적 분석에 기반한 **추정**입니다.
> 정확한 근본 원인 특정을 위해 아래 검증 작업이 필요합니다.

### 필요 검증

| # | 검증 항목 | 목적 |
|---|-----------|------|
| V1 | **브라우저 콘솔 로그 확인** | stream.end가 프론트엔드에 도착했는지, 도착 후 예외가 발생했는지 확인 |
| V2 | **동일 조건 재현 테스트** | 질의2와 동일 쿼리("이번년도 예금신규 top 10 지점")를 재실행하여 테이블 렌더링/버튼 복원 재현 여부 확인 |
| V3 | **stream.end 전송 로그 추가 후 재테스트** | `_safe_send(end_msg)` 성공/실패를 INFO 레벨로 기록하여 서버 측 전송 여부 확정 |
| V4 | **result_data 직렬화 테스트** | 실제 SQL 실행 결과의 `result_data`에 비직렬화 타입(`datetime`, `Decimal` 등)이 포함되는지 단위 테스트 |

### 분석 신뢰도 요약

| 섹션 | 결론 신뢰도 | 비고 |
|------|-------------|------|
| §1 sqlglot 경고 | **확정** | 코드에서 직접 확인 가능 |
| §2 테이블 미렌더링 | **추정** | 3개 원인 시나리오 중 특정 불가 — V1, V2 필요 |
| §3 버튼 잔류 | **추정** | §2와 동일 근본 원인 — V1, V3 필요 |
| §4-5 Reasoning 실패 | **확정** | trace/error.log에서 전 과정 추적 가능 |
| §6-7 구조적 결함/수정 방향 | **확정** | 코드 정적 분석으로 확인, 이번 건 직접 원인 여부와 무관하게 수정 필요 |
