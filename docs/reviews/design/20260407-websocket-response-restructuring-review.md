# WebSocket 응답 구조 개선 — 설계 검토 보고서

> **검토 대상**: `docs/todo/20260407-websocket-response-restructuring.md` 구현 계획
> **검토일**: 2026-04-07
> **심각도 등급**: [P0] 치명적 → [P1] 중대 → [P2] 개선 필요 → [P3] 제안

---

## 요약

전반적으로 잘 설계된 구조 개선안이다. 하이브리드(마크다운 + JSON) 접근과 동시 전환 전략은 합리적이며, 기존 코드에 대한 영향 분석도 충분하다. 아래 8건의 지적 사항 중 P0 없음, P1 2건은 구현 전 반드시 해소해야 한다.

| 심각도 | 건수 |
|--------|------|
| P0 치명적 | 0건 |
| P1 중대 | 2건 |
| P2 개선 필요 | 3건 |
| P3 제안 | 3건 |

---

## P1 — 중대 (구현 전 해소 필수)

### P1-1. `result_data.rows`에 코드값 변환(`apply_code_mappings`)이 누락됨

**현상**: 설계 문서 7-1의 `_build_result_data()`는 `sql_result.rows`를 그대로 사용한다. 그러나 현재 `formatter.py:69~73`에서 `apply_code_mappings()`을 호출하여 코드값("01" → "정상", "02" → "연체" 등)을 한글 명칭으로 변환한 뒤 마크다운 테이블에 반영하고 있다.

**영향**: `result_data.rows`에 원본 코드값이 그대로 들어가면 프론트엔드 테이블에 "01", "02" 같은 의미 없는 값이 표시된다. 금융 도메인에서 코드값은 매우 빈번하므로 사용자 경험에 직접적 타격.

**근거**: `response_formatter.py:212~244`의 `apply_code_mappings()`은 SQL alias와 코드 메타를 매핑하여 변환하는 fallback 로직이다. formatter에서만 호출되고 runner에서는 호출하지 않으므로, result_data의 rows는 변환되지 않은 원본.

**대안**: runner의 `_build_result_data()`에서 `apply_code_mappings()`을 호출하거나, formatter가 변환한 rows를 State에 저장하여 runner가 꺼내쓰도록 한다.

- **방안 A**: runner에서 `apply_code_mappings()` 호출 → `explored_codes`와 `validated_sql`에 대한 접근 필요. `result.get("reason").explored_codes`로 접근 가능하므로 실현 가능.
- **방안 B (권장)**: formatter가 이미 코드 변환된 rows를 생성하므로, **formatter에서 result_data를 조립**하고 State에 저장. runner는 `result.get("result_data")`를 그대로 PipelineResult에 넣기만 함.

방안 B가 권장되는 이유:
1. formatter가 이미 `detect_column_formats` + `apply_code_mappings` + `build_summary_line`을 호출하므로, result_data 조립에 필요한 모든 중간 결과가 이 노드에 있음
2. runner에서 `detect_column_formats`를 중복 호출하는 문제(P2-1)도 동시에 해소
3. 설계 문서의 "PipelineState에 UI 전용 필드를 추가하지 않는다" 원칙과 충돌하지만, `process_summary`도 이미 State에 추가하기로 했으므로 일관성 측면에서 오히려 적합

---

### P1-2. 세션 복원 시 구조화 데이터 유실 — 대화 이력의 실질적 퇴보

**현상**: 기존에는 `formatted_response`에 마크다운 테이블 + `<details>` 조회 과정이 포함되어 턴에 저장됐다. 세션 복원(loadSession) 시 `content`에서 마크다운을 렌더링하면 테이블과 조회 과정이 모두 표시됐다. 변경 후에는 `formatted_response`에 핵심 수치 요약만 남으므로, 복원된 메시지에는 테이블도 조회 과정도 표시되지 않는다.

**영향**: 사용자가 이전 대화로 돌아왔을 때, 새 메시지에서는 짧은 요약만 보이고 테이블은 사라진다. "결과 테이블이 있었는데 왜 안 보이지?"라는 혼란 발생. 이것은 기능 퇴보(regression)다.

**근거**: `static/embedded.html:2067~2079`의 `loadSession`은 `t.content`를 텍스트로 사용하여 `MS.create` → `RD.render` → `mdRender`로 렌더링한다. `result_data`/`process_summary`는 턴 저장 metadata에 포함되지 않으므로 복원 불가.

**대안**:

- **방안 A (권장)**: 턴 저장 metadata에 `result_data`와 `process_summary`를 포함한다. runner.py의 `save_turn` 호출부(403줄 metadata dict)에 추가. 프론트엔드의 `_onInsightClick` lazy-load 경로(1059줄)를 확장하여, metadata 로드 시 `result_data`/`process_summary`도 가져와 렌더링. 단, `result_data.rows`에 500행이 저장되므로 metadata 크기가 증가한다는 단점이 있다.
- **방안 B**: result_data는 metadata에 저장하되 rows는 제외(columns + column_formats만). 복원 시에는 테이블 없이 "N건 조회됨" 표시만 제공. 전체 데이터가 필요하면 다운로드 버튼으로 유도.
- **방안 C**: 전환 후에도 `formatted_response`에 마크다운 테이블을 유지하되 chunk로는 보내지 않음. 즉 `formatted_response`는 "텍스트+테이블"이지만, WS chunk에는 텍스트만 전송. 세션 복원 시에는 기존처럼 마크다운 렌더링. 구조화 렌더링은 새 메시지에서만 동작. → 서버 변경 최소화

방안 C가 가장 안전한 전환 전략이다. 다만 formatted_response에 마크다운 테이블을 남기면 "마크다운 제거"의 설계 의도와 충돌하므로, **방안 A를 기본으로 하되 rows는 displayed_count만큼만 저장**하는 것을 권장한다. 100행 이하면 metadata 크기 부담이 크지 않다.

---

## P2 — 개선 필요

### P2-1. `detect_column_formats` 중복 계산 (formatter + runner)

**현상**: 구현 계획에서 `detect_column_formats`를 formatter(64줄)와 runner의 `_build_result_data()` 양쪽에서 호출한다.

**영향**: SQL 파싱(`sqlglot`)을 2회 수행. 비용은 미미하지만 같은 입력에 대해 다른 결과가 나올 가능성은 0이므로 순수한 낭비.

**대안**: P1-1의 방안 B(formatter에서 result_data 조립)를 채택하면 자연히 해소된다. 만약 runner에서 조립해야 한다면, formatter가 `column_formats`를 State에 저장하는 것도 방법이지만, 복잡도 대비 이점이 적다. P1-1과 묶어서 해결 권장.

---

### P2-2. 테스트 깨짐 — `test_process_summary_appended`와 `test_format_data_extraction`

**현상**: 두 기존 테스트가 변경 후 실패한다.

1. `test_process_summary_appended` (test_format_response.py:134~153): `"<details>" in response and "조회 과정 요약" in response`를 assert. 변경 후 `<details>`가 `formatted_response`에서 제거되므로 실패.
2. `test_format_data_extraction` (test_format_response.py:80~109): `len(response) >= 50`과 `"강남지점" in response`를 assert. 마크다운 테이블 제거 후 summary_line만 남으면 "강남지점"이 포함되지 않을 수 있고, 길이도 50 미만이 될 수 있음.

**영향**: CI 파이프라인 실패. 테스트 수정을 계획에 포함해야 함.

**대안**: Phase 1에 테스트 수정 단계를 명시적으로 추가한다.

- `test_process_summary_appended` → `process_summary`가 dict 타입으로 return dict에 포함되는지 검증으로 변경
- `test_format_data_extraction` → `formatted_response`에 summary_line이 포함되고 마크다운 테이블(`|`)이 없음을 검증
- `test_process_summary_builder.py`의 모든 테스트 → 반환 타입이 `str`에서 `dict`로 변경되므로 전면 수정 필요

---

### P2-3. REST 응답에 `insight` 무조건 포함 — 기존 소비자 영향 검토 필요

**현상**: 설계 문서 7-5에서 REST `/api/query`에 `insight`를 새로 포함한다. 현재 REST 응답에는 `insight`가 없다.

**영향**: `insight`는 수십 KB가 될 수 있는 대형 dict (tables_used, reasoning_trail, validation_detail 등). 기존 REST 소비자(테스트 스크립트 등)가 응답 크기 변화에 민감할 수 있다.

**대안**: `insight` 포함 여부를 쿼리 파라미터(`include_insight=true`)로 제어한다. 기존의 `include_trace` 패턴과 동일. 기본값은 `false`로 하여 기존 소비자에 영향 없도록 함. `result_data`와 `process_summary`는 핵심 데이터이므로 항상 포함해도 무방.

---

## P3 — 제안

### P3-1. `result_data`와 `sql_result` 명명 혼동 가능성

**현상**: `PipelineResult`에 `sql_result`(SQLResult 모델, 다운로드 캐싱용)와 `result_data`(dict, UI 렌더링용)가 공존한다.

**영향**: 코드 리뷰 시 혼동 가능. "SQL 결과"를 다루는 두 필드의 차이가 즉각적으로 드러나지 않음.

**대안**: `result_data` → `ui_table_data`로 변경하면 UI 전용임이 명확해지지만, 설계 문서의 WS 스키마와 불일치가 발생하므로 현재 명명 유지가 낫다. description에 용도 차이를 명확히 기재하는 것으로 충분. 현행 유지 권장, 단 docstring 보강.

---

### P3-2. WebSocket 메시지 크기 — 500행 데이터의 GZip 미적용

**현상**: `result_data.rows`에 최대 500행이 포함된다. 컬럼이 10개이고 각 값이 평균 20바이트이면 약 100KB. JSON 직렬화 오버헤드 포함 시 150~300KB.

**영향**: FastAPI WebSocket은 기본적으로 GZip을 적용하지 않으므로 그대로 전송. 300KB 정도는 대부분 네트워크에서 문제없지만, 폐쇄망 환경에서 저대역 연결이라면 지연 가능.

**대안**: Phase 1에서는 현행대로 진행하되, `MAX_ROWS`를 config로 분리하여 환경별 조정 가능하게 한다. 향후 필요 시 WebSocket per-message compression 활성화 검토.

---

### P3-3. `formatCurrency` JS 구현과 Python `format_currency` 로직 이중 관리

**현상**: 프론트엔드에 JS `formatCurrency()`, 서버에 Python `format_currency()`가 각각 존재한다. 동일한 로직이 두 언어로 유지된다.

**영향**: 향후 포맷팅 규칙 변경 시 양쪽을 동시에 수정해야 함. 누락 시 서버(마크다운 요약)와 클라이언트(구조화 테이블)의 숫자 표시가 불일치.

**대안**: 
1. `result_data`에 이미 포맷된 문자열(formatted_rows)을 포함하는 방안 → 정렬/필터가 숫자가 아닌 문자열 기반이 되어 기능 제한
2. **권장**: 현행 유지(raw 숫자 전송 + 클라이언트 포맷팅). 단, 포맷팅 규칙 변경 시 양쪽 테스트에 포함되도록 테스트 케이스 추가. Python의 `format_currency` 테스트를 기준값으로 하고, JS 테스트에서 동일 입력-출력 검증.

---

## 검토 프레임 요약

### 가정 검증

| 가정 | 검증 결과 |
|------|----------|
| formatter가 마크다운 테이블 제거해도 다른 노드에 영향 없음 | **적합** — `format_report_table`은 formatter에서만 호출. 단, 테스트 2건 깨짐 (P2-2) |
| `process_summary`를 State에 추가하면 LangGraph merge 정상 동작 | **적합** — `dict | None` 타입은 기본 merge 정책(last-writer-wins)으로 안전. `Annotated[..., operator.add]` 같은 accumulator 필요 없음 |
| REST 소비자가 새 필드를 무시 | **확인 필요** — insight 크기에 의한 영향 (P2-3) |
| 과거 메시지 복원이 마크다운 폴백으로 동작 | **부분 적합** — 기존 메시지는 OK, 새 메시지는 테이블 없는 짧은 텍스트만 복원됨 (P1-2) |

### 실패 시나리오

| 시나리오 | 위험도 | 대응 |
|----------|--------|------|
| `detect_column_formats`에서 sqlglot 파싱 실패 | 낮음 | 이미 `{}`(빈 dict) 반환으로 폴백됨 |
| `apply_code_mappings` 미적용 상태의 코드값 노출 | **높음** | P1-1에서 해소 필요 |
| 500행 × 다수 컬럼의 대용량 JSON 전송 | 낮음 | P3-2에서 MAX_ROWS 설정화로 대응 |
| 세션 복원 시 빈 테이블 | **높음** | P1-2에서 해소 필요 |

### 대안 비교

| 항목 | 현재 계획 (runner 조립) | 대안 (formatter 조립) |
|------|------------------------|----------------------|
| 코드값 변환 | 누락 (P1-1) | 자연히 포함 |
| column_formats 중복 | 있음 (P2-1) | 없음 |
| State 필드 추가 | process_summary 1개 | result_data + process_summary 2개 |
| 설계 문서 원칙 | "State에 UI 필드 미추가" 준수 | 위반하지만 process_summary도 마찬가지 |
| 복잡도 | runner에 새 함수 추가 | formatter 수정 범위 증가 |

**판정**: formatter에서 result_data를 조립하는 방안이 P1-1, P2-1을 동시에 해소하며, 이미 process_summary도 같은 패턴(formatter → State → runner → PipelineResult)을 사용하므로 일관성 측면에서 우수.

### 프로젝트 계층 적합성

| 변경 | 위치 | 판정 |
|------|------|------|
| `process_summary` str→dict | `src/services/process_summary_builder.py` | **적합** — 기존 위치, 서비스 계층 |
| `PipelineResult` 필드 | `src/agents/models/response.py` | **적합** — 응답 모델 |
| `PipelineState` 필드 | `src/agents/state/state.py` | **적합** — Present 섹션 내 |
| `_build_result_data()` | runner vs formatter | formatter 권장 (P1-1 대안 B) |
| 프론트엔드 함수 | `static/embedded.html` 내 | **적합** — 기존 IIFE 패턴 내 |

### 명명 일관성

| 항목 | 기존 패턴 | 신규 | 판정 |
|------|----------|------|------|
| Python 필드 | snake_case (`sql_result`) | `result_data`, `process_summary` | **적합** |
| WS JSON 키 | snake_case (`turn_id`) | `result_data`, `process_summary` | **적합** |
| JS 변수 | camelCase (`turnId`) | `resultData`, `processSummary` | **적합** |
| CSS 클래스 | kebab-case (`ref-block`) | `result-table-wrap`, `process-summary-block` | **적합** |
| 함수명 (JS) | camelCase (`renderInsight`) | `renderResultTable`, `renderProcessSummary` | **적합** |

---

## 결론 및 권장 조치

| # | 심각도 | 항목 | 조치 |
|---|--------|------|------|
| 1 | P1 | 코드값 변환 누락 | result_data 조립을 formatter로 이동 |
| 2 | P1 | 세션 복원 시 데이터 유실 | 턴 metadata에 result_data/process_summary 포함 |
| 3 | P2 | column_formats 중복 | P1-1과 동시 해소 |
| 4 | P2 | 테스트 깨짐 | Phase 1에 테스트 수정 포함 |
| 5 | P2 | REST insight 크기 | `include_insight` 파라미터 추가 |
| 6 | P3 | 명명 혼동 | docstring 보강으로 충분 |
| 7 | P3 | WS 메시지 크기 | MAX_ROWS config 분리 |
| 8 | P3 | JS/Python 포맷팅 이중 관리 | 테스트 동기화로 대응 |

P1 2건을 반영한 뒤 구현 착수를 권장한다.
