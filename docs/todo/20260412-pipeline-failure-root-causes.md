# 파이프라인 실패 루트 원인 개선 계획

- 작성일: 2026-04-12
- 근거 트레이스: `logs/traces/trace_*_20260412_anonymous_session-1775928623223_6428b84c89e4.*`
- 근거 질의: "작년말대비 여신 2배 이상 증가한 고객 중 주담대 보유 고객 목록 알려줘"
- 결과: ❌ 실패(75.9s / LLM 14회 / 130k tok / give_up)

## 목차

- [0. 실패 시퀀스 요약](#0-실패-시퀀스-요약)
- [P0-1. 진단 도구 어댑터의 결과 파싱 버그 (CRITICAL)](#p0-1-진단-도구-어댑터의-결과-파싱-버그-critical)
- [P0-2. L2b 시맨틱 검증기의 DATE_TRUNC 할루시네이션 + 오분류 (CRITICAL)](#p0-2-l2b-시맨틱-검증기의-date_trunc-할루시네이션--오분류-critical)
- [P1-1. sample_rows null → "0행" 오표기로 인한 recovery 오판](#p1-1-sample_rows-null--0행-오표기로-인한-recovery-오판)
- [P1-2. `ConnectorManager.parse_db_source` 스키마 접두사 미처리](#p1-2-connectormanagerparse_db_source-스키마-접두사-미처리)
- [P2-1. 코드 메타 확정 정보가 knowledge status 에 반영 안 됨](#p2-1-코드-메타-확정-정보가-knowledge-status-에-반영-안-됨)
- [P2-2. `_apply_tool_result` early-return 이 텔레메트리 음영지대를 만듦](#p2-2-_apply_tool_result-early-return-이-텔레메트리-음영지대를-만듦)
- [작업 순서 제안 (코드 검증 후 조정됨)](#작업-순서-제안-코드-검증-후-조정됨)
- [회귀 검증](#회귀-검증)
- [관련 문서](#관련-문서)

## 0. 실패 시퀀스 요약

1. 1라운드에서 SQL 은 실질적으로 정답에 가깝게 생성됨 (L1/L2a PASS)
2. L3 실행 `row_count: 0`
3. L2b(LLM) 가 DATE_TRUNC 표현을 자기모순으로 false-positive 판정 →
   `failure_classification: structural`
4. recovery_agent 가 `get_date_distribution`, `get_column_values` 로
   진단을 시도했으나, 어댑터가 DB 결과를 모두 버림(아래 P0-1)
5. `sample_rows=null` 이 `"0행 (데이터 없음 또는 미조회)"` 로 렌더링되어
   recovery LLM 이 "전체 테이블 데이터 부재" 로 오판, 매 라운드 동일한
   교훈을 누적
6. H2/H3 가설이 전부 "기준일자 문제" 방향으로 편향 → GENERATION_FAILED
   3회 연속 반복 → give_up

**코드 메타·PG·Mongo·ES 시딩은 정상임을 확인했다.** 이번 실패는 데이터
부재가 아닌 파이프라인 내부 결함의 연쇄였다.

---

## P0-1. 진단 도구 어댑터의 결과 파싱 버그 (CRITICAL)

### 증상
`get_sample_rows`, `get_column_values`, `get_date_distribution`,
`get_column_profile` 이 DB 에서 실제로 행을 받아놓고도 호출자에게
빈 결과(`[]`/`{}`)만 반환.

### 로그 증거 (`logs/app.log` L3200–3219)
```
sql: SELECT DISTINCT BAL_DT FROM ADWOWN.TB_ADW_LNB341P ... → row_count: 30
도구 실행 완료  tool: get_date_distribution ... results: 0

sql: SELECT DISTINCT LN_DCD FROM ADWOWN.TB_ADW_LNB333M WHERE ... → row_count: 1
도구 실행 완료  tool: get_column_values ... results: 0
```
텔레메트리에도 `raw_result: null, insight: null` 로 기록됨.

### 근본 원인
`src/agents/nodes/reason/tools.py:325-334` 등에서 호출 결과를 아래와
같이 처리한다.

```python
result = await db.execute_query(sql)
if hasattr(result, "rows") and isinstance(result.rows, list):
    return [str(row.get(column_name, "")) for row in result.rows]
return []
```

그러나 `TESTConnector.execute_query` (`src/connectors/impl/test_connector.py:109-144`)
는 **`list[dict]` 를 평면적으로 반환**한다. `list` 에 `.rows` 속성이
없으므로 `hasattr` 는 False → 항상 `[]` 반환.

**버그 범위 (확정)** — 동일 패턴이 `tools.py` 의 **4개 함수** 전체에 존재:

- `get_sample_rows` (L244-250)
- `get_column_values` (L325-334)
- `get_column_profile` (L380-399)
- `get_date_distribution` (L447-456)

즉 `get_sample_rows` 도 **같은 버그**를 가진다. 이는 P1-1 의
"sample_rows=None" 현상을 설명하는 핵심 연결고리이며, P1-1 은 독립
이슈가 아니라 **P0-1 의 직접 귀결**이다. (초기 문서는
`get_sample_rows` 를 정상으로 오기재했으나 정정함.)

### 파장
- recovery_agent 가 "LN_DCD='02' 값 존재 여부" 를 영원히 확인할 수
  없음 → knowledge status 가 PROBABLE 에 고정 → L2b no_unconfirmed_values
  가 반복 FAIL
- "BAL_DT 분포 확인 불가" 로 판단 → H2/H3 가설이 모두 "기준일자 문제"
  로 편향
- `_apply_tool_result` 가 빈 결과일 때 early-return 하므로
  (`context_retriever.py:284`) `step.raw_result` 도 `null` 로 남음 →
  배치 해석 LLM 에도 정보가 전달되지 않음

### 개선안

1. **어댑터 즉시 수정** — `tools.py` 의 **4개 함수**(`get_sample_rows`,
   `get_column_values`, `get_column_profile`, `get_date_distribution`)
   에서 반환값 처리 로직을 커넥터 계약과 일치시킨다.

   ```python
   result = await db.execute_query(sql)  # list[dict]
   if not isinstance(result, list):
       return []
   return [str(row.get(column_name, "")) for row in result]
   ```

2. **커넥터 계약 명시** — `DatabaseConnector.execute_query` 의 타입
   힌트를 `list[dict[str, Any]]` 로 고정하고, docstring 에 "어떠한
   커넥터도 `.rows` 속성을 노출하지 않는다" 고 못박는다. 현재
   `hasattr(result, "rows")` 형태를 쓰는 호출부가 더 없는지
   `rg "hasattr\(.*rows" src/` 로 전수조사.

3. **회귀 테스트 추가** — `tests/unit/agents/nodes/reason/test_tools.py`
   에 `get_sample_rows` / `get_column_values` / `get_column_profile` /
   `get_date_distribution` 이 Dummy TESTConnector 에서 실제로 비어있지
   않은 값을 반환하는지 확인하는 케이스. 단위 테스트가 Dummy 데이터
   생성기 동작에 의존하지 않도록 fixture 로 `execute_query` 를 모킹.

4. **빈 결과 ≠ raw_result null** — `_apply_tool_result` 에서
   `result` 가 빈 list 인 경우에도 `step.raw_result = {"empty": True}`
   같이 **"호출은 성공, 데이터는 0건"** 신호를 남겨 recovery LLM 이
   "미조회" 와 구분할 수 있게 한다.

### 검증 기준
- 트레이스에서 `get_column_values` 호출 시 `raw_result` 가 null 이 아님
- recovery_agent 가 LN_DCD='02' 존재를 1회 내 확인
- 해당 질의 재실행 시 라운드 2 이내 SUCCESS

---

## P0-2. L2b 시맨틱 검증기의 DATE_TRUNC 할루시네이션 + 오분류 (CRITICAL)

### 증상 1 — 자기모순 fix_instruction
`logs/app.log:3145-3149` 원문:

> "logical_consistency": "질의 의도(작년말 대비 2배 이상 증가)와 달리
> '작년 말'을 **12월 31일이 아닌 '올해 초(1월 1일)'** 기준으로
> 계산하고 있어 논리적 오류가 있음"
>
> "fix_instruction": "'작년 말' 기준일을 **DATE_TRUNC('year',
> CURRENT_DATE) - INTERVAL '1 day' (올해 1월 1일 기준)이 아닌, 작년
> 12월 31일(DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 day')로**
> 명확히 수정하세요"

→ 완전히 동일한 식을 "틀렸다" 와 "맞다" 로 동시에 지칭. 생성된 SQL 은
`BAL_DT = DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 day'` 로
실제로는 `2025-12-31` 이 맞다.

### 증상 2 — row_count=0 을 structural 로 오분류
L2b checks 중 `no_dead_end_repeat=false`, `db_execution=false` 는
"실행 결과 0건" 을 근거로 한다. 그런데 최종 `failure_classification`
이 `structural` 로 떨어지면서 recovery 가 "SQL 구조/문법 수정"
방향으로 돈다. 실제로는 **값 부재 또는 필터 과다** 이슈이므로
semantic/data 분류여야 한다.

### 근본 원인

#### ⭐ 1차 원인 (추가 발견) — Layer3 0건이 L2b 에 "FAIL" 문자열로 주입됨

`src/agents/nodes/reason/sql_validator.py:545-550`:

```python
def _format_db_execution_result(layer3_result):
    if layer3_result["status"] == "PASS":
        return f"PASS ({row_count}건 반환)"
    return f"FAIL: {feedback}"   # ← 0건도 여기로 빠짐
```

Layer3 는 `row_count == 0` 일 때 `status="FAIL"` + `failure_type=EMPTY_RESULT`
로 리턴(L695-707). 이 결과가 L2b 프롬프트의 `{db_execution_result}`
자리에 **`"FAIL: 정상적으로 SQL을 생성하고 조회했으나 데이터가 0건입니다"`**
로 주입된다.

프롬프트 본문에는 "0건 자체를 실패로 판정하지 마라"(L57) 라고 적혀있지만,
입력 문자열이 `"FAIL:"` 으로 시작하는 이상 중소형 오픈소스 모델
(Solar Pro 2 70B, Qwen3.5 등 폐쇄망 타겟)은 이 토큰에 강하게 편향된다.
LLM 은 "뭐라도 실패 근거를 만들어야 한다" 는 압박 하에서 DATE_TRUNC
식을 억지로 트집잡는 **2차 할루시네이션**을 일으킨다.

#### 2차 원인

- L2b 프롬프트에 DATE_TRUNC 의미를 고정하는 few-shot 이 없음
- `failure_classification` 이 LLM 출력 문자열에만 의존해 structural /
  local_fix 둘 중 하나로 납작해짐 (`sql_validator.py:366-378`)

### 개선안

1. **⭐ `_format_db_execution_result` 3상태 개편 (최우선)** —
   프롬프트 수정 없이 LLM 편향을 제거하는 가장 효과적인 수정.

   ```python
   def _format_db_execution_result(layer3_result):
       status = layer3_result["status"]
       if status == "PASS":
           return f"SUCCESS ({layer3_result['row_count']}건 반환)"
       if layer3_result.get("failure_type") == FailureType.EMPTY_RESULT:
           return "SUCCESS (0건 — 실행은 성공, 결과 없음)"
       return f"ERROR: {layer3_result.get('feedback', '')}"
   ```

   이 수정만으로 이번 케이스의 DATE_TRUNC 할루시네이션은 대부분 사라질
   가능성이 높다. 회귀 검증 후 나머지 개선안 적용 여부를 재평가한다.

2. **L2b 프롬프트 보강** (`resources/prompts/reason/sql_validator_system.txt`)
   - PostgreSQL `DATE_TRUNC` 의미 고정:
     - `DATE_TRUNC('year', d)` = 해당 연도 1월 1일 00:00
     - `DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 day'` = **전년도
       12월 31일** (= "작년 말")
     - `DATE_TRUNC('year', CURRENT_DATE) + INTERVAL '1 year' - INTERVAL '1 day'`
       = 당해 12월 31일
   - few-shot 예시 2건 추가: "작년 말", "올해 초" 의 올바른 변환
   - 규칙: **같은 식을 "틀렸다/맞다" 로 동시에 지칭하면 즉시 자기검증
     실패로 간주하고 재생성** (JSON schema 에 `self_consistency_check`
     필드 추가 고려)

3. **failure_classification 재설계** (개선안 1번 적용 후 재평가)
   - 현재 구조: L2b LLM 이 `failure_classification` 문자열을 직접 출력 →
     `_build_layer2b_failure` 가 `local_fix` 아니면 `structural` 로만 분기.
   - 제안: `db_execution=false` 가 `row_count=0` 근거일 때는 **structural 금지**.
     아래 매핑을 결정론적으로 강제:

     | 트리거 | 분류 |
     |---|---|
     | L1/L2a parse·safety | `SQL_STRUCTURAL` |
     | `measure_reflected` / `filters_reflected` / `group_by_reflected` | `SEMANTIC_MISMATCH` |
     | `logical_consistency` 단독 | `SEMANTIC_LOGIC` |
     | `db_execution=false` + row_count=0 | `EMPTY_RESULT` (신규) |
     | `no_unconfirmed_values` | `KNOWLEDGE_GAP` |

   - **열린 결정사항**: 위 매핑 테이블과 LLM 의 `failure_classification`
     출력은 **중복**이다. 둘 중 하나로 통일해야 한다.
     - 옵션 A: 매핑 테이블(결정론적, 코드) 만 쓰고 LLM 출력 필드는 제거
     - 옵션 B: LLM 출력만 쓰되 프롬프트에 분류 기준을 촘촘하게 명시
     - 옵션 A 를 권장(결정론적·디버깅 용이).
   - `EMPTY_RESULT` 는 recovery_agent 가 "필터 조건 완화 → 값 존재 여부
     확인 → 점진적 재조립" 경로로 분기하도록 전용 전략을 부여
     (`20260405-failure-type-redesign.md` 와 정합 확보)

4. **자기모순 탐지 가드** — L2b 응답 파싱 후 `fix_instruction` 에 `A가 아닌 A`
   형태 (정규식 기반 간이 탐지) 가 포함되면 WARNING 로그 남기고 분류를
   `SEMANTIC_LOGIC` 로 강제 downgrade. 완벽하진 않지만 이번 같은 케이스는
   즉시 잡힌다. (개선안 1번으로 할루시네이션 자체가 사라지면 불필요할 수
   있으므로 회귀 검증 후 결정.)

### 검증 기준
- L2b unit test: 정답 SQL (`DATE_TRUNC('year', CURRENT_DATE) - INTERVAL '1 day'`)
  을 "작년 말" 로 인식
- 회귀 테스트: 동일 질의가 structural 재시도 없이 통과

---

## P1-1. sample_rows null → "0행" 오표기로 인한 recovery 오판

### 증상
recovery_agent 의 샘플 데이터 섹션이 매 라운드 아래처럼 렌더링됨.

```
► 샘플 데이터
  - TB_ADW_LNB301M: 0행 (데이터 없음 또는 미조회)
  - TB_ADW_LNB333M: 0행 (데이터 없음 또는 미조회)
  ...
```

이를 본 LLM 이 "전체 테이블의 샘플 데이터가 0건 → 데이터 적재 문제"
로 결론짓고, 그 결론이 `lessons_learned` 에 누적되어 후속 라운드의
가설을 전부 "기준일자·파티션 문제" 로 편향시킴.

### 근본 원인
`src/agents/nodes/reason/recovery_agent.py:945-965`:

```python
rows = ct.sample_rows
if rows:
    lines.append(f"- {ct.table_name}: {len(rows)}행 (컬럼: ...)")
else:
    lines.append(f"- {ct.table_name}: 0행 (데이터 없음 또는 미조회)")
```

`ct.sample_rows` 는 `list[dict] | None` (`state.py:254`). **`None`(미조회)**
과 **`[]`(0건)** 가 하나의 분기로 뭉뚱그려져 있다. 실제로 이번 세션에서
`sample_rows` 는 전부 `None` 이었다(호출 경로가 없었거나 실패).

### 개선안

1. **미조회 vs 0건 구분**
   ```python
   if rows is None:
       lines.append(f"- {ct.table_name}: (미조회)")
   elif not rows:
       lines.append(f"- {ct.table_name}: 0행 (조회 완료, 데이터 없음)")
   else:
       cols = list(rows[0].keys())[:5]
       lines.append(
           f"- {ct.table_name}: {len(rows)}행 (컬럼: {', '.join(cols)})",
       )
   ```

2. **프롬프트 가이드 추가** — recovery_agent 시스템 프롬프트에
   "`(미조회)` 는 샘플 조회를 호출한 적이 없다는 뜻이므로 데이터 부재
   근거로 사용하지 말 것" 명시.

3. **자동 샘플 조회 고려 (⏳ 득실 분석 대기)** — 선정된 테이블(SELECTED)
   에 대해 1라운드 진입 시점에 `get_sample_rows` 를 자동 1회 호출해
   `sample_rows` 필드를 채워두는 옵션.

   **장점**

   - LLM 이 탐색 단계에서 샘플 호출을 계획하지 않아도 sample_rows 가
     확보됨 → recovery 단계에서 "미조회" 공백 자체가 사라짐
   - sql_generator 프롬프트에 테이블 샘플이 항상 들어가 SQL 정확도 상승
     가능성

   **단점 / 리스크**

   - LLM 자율 탐색 설계와 충돌 — execution_plan 이 아닌 경로로 도구 호출
   - 대형 테이블에서 N개 동시 자동 호출 시 토큰·지연 폭증
   - P0-1 수정 후 LLM 이 필요 시 호출한 `get_sample_rows` 가 정상 작동
     하므로 자동화 필요성 자체가 감소할 수 있음
   - `context_retriever.py:106-111` 의 "sample_rows 있으면 스킵" 멱등
     가드와 의미가 뒤집힘

   **결정 포인트**: P0-1 수정 후 회귀 트레이스에서 recovery 가 실제로
   `get_sample_rows` 를 호출하는지, 호출 비율이 얼마인지 측정한 뒤
   채택 여부를 결정. 현 시점에서는 **보류**.

### 검증 기준
- 같은 질의 트레이스에서 recovery_agent 의 `lessons_learned` 에
  "데이터 부재" 가 등장하지 않음

---

## P1-2. `ConnectorManager.parse_db_source` 스키마 접두사 미처리

### 증상
현재는 단일 TEST 커넥터 환경에서 폴백이 먹어 드러나지 않지만,
ADW/BDP 이중화되면 즉시 실패한다.

### 근본 원인
`src/connectors/manager.py:279-284`:

```python
parts = table_name.upper().split("_")
if len(parts) >= 3 and parts[0] == "TB":
    code = parts[1]
    ...
```

`_tool_get_column_values` (`tools.py:623`) 등은 `raw_table =
"ADWOWN.TB_ADW_LNB333M"` 을 그대로 `parse_db_source` 에 넘긴다.
`"ADWOWN.TB_ADW_LNB333M".upper().split("_")` →
`["ADWOWN.TB", "ADW", "LNB333M"]` → `parts[0] != "TB"` →
`db_source=""`.

다중 커넥터 환경에서는 `get_query_db` 가
`RuntimeError("업무 DB 커넥터를 결정할 수 없습니다")` 를 던져 모든
DB-직접 도구가 실패한다.

### 개선안

1. **어댑터에서 `table_name` 만 전달** — `_tool_get_column_values`,
   `_tool_get_column_profile`, `_tool_get_date_distribution`,
   `_tool_get_sample_rows` 가 이미 `_split_qualified_name` 으로
   `(schema_name, table_name)` 을 얻고 있으므로, `parse_db_source`
   에는 `table_name` 을 넘기도록 수정.

   ```python
   schema_name, table_name = _split_qualified_name(raw_table)
   db_source = ConnectorManager.parse_db_source(table_name)
   ```

2. **방어적 보강** — `parse_db_source` 자체도 입력 정규화를 추가해
   이후 실수를 막는다.
   ```python
   name = table_name.upper().split(".")[-1]  # 스키마 제거
   parts = name.split("_")
   ```

3. **테스트** — `test_manager.py` 에 다음 케이스 추가:
   - `"ADWOWN.TB_ADW_LNB333M"` → `"ADW"`
   - `"TB_BDP_XXX001L"` → `"BDP"`
   - 알 수 없는 접두사 → `""`

### 검증 기준
- ADW/BDP 이중화 구성으로 변경해도 모든 진단 도구가 정상 라우팅

---

## P2-1. 코드 메타 확정 정보가 knowledge status 에 반영 안 됨

### 증상
`lookup_code_meta:LN_DCD` 가 `executed_tool_keys` 에 올라가 있음에도,
`filter:주택담보대출` 의 status 가 모든 라운드에서 PROBABLE 로
고정되어 있었다. CONFIRMED 로 올라가지 않으니 L2b `no_unconfirmed_values`
가 계속 FAIL 로 판정.

### 근본 원인 가설
1. `lookup_code_meta` 가 실제로 호출되지 않고 키만 기록됨 (enrichment
   경로 추정). 확인 필요.
2. 또는 호출은 되었으나 `context_interpreter` 가 코드 메타 성공을
   knowledge_item 승격으로 연결하는 규칙이 없음.

### 개선안

1. **실제 호출 여부 확인** — `logs/app.log` 에서
   `Mongo 코드 메타 조회` / `lookup_code_meta` 실행 로그가 남는지
   체크. 없다면 enrichment 경로에서 키만 기록되고 실행이 누락된
   버그다. `context_retriever._enrich_use_cases` 구간 점검.

2. **interpreter 승격 규칙** — `context_interpreter` 에서
   knowledge_item 의 조건식(예: `LN_DCD = '02'`)에 등장하는
   컬럼/값이 code_map 에 존재하면, 해당 knowledge_item 을
   PROBABLE → CONFIRMED 로 자동 승격. LLM 판단에만 맡기지 말고
   rule-based 보강.

3. **L2b no_unconfirmed_values 재정의** — "PROBABLE 도 허용.
   단, `dead_ends` 누적 1회 이상이면 CONFIRMED 요구" 로 완화해
   첫 라운드의 false-positive 를 줄인다. (단, 이 완화는 P0-1 수정
   후에 적용해야 함)

---

## P2-2. `_apply_tool_result` early-return 이 텔레메트리 음영지대를 만듦

### 증상
`context_retriever.py:284`:
```python
if not result:
    return
```
빈 결과일 때 `step.raw_result`, `step.insight` 모두 null 로 남는다.
배치 해석 LLM, 추후 리포트 렌더링, 디버깅 모두 "도구가 실행되었는지조차"
알 수 없다.

### 개선안
빈 결과도 구조화해서 기록한다.

```python
if not result:
    step.raw_result = {"status": "empty", "tool": step.tool}
    step.insight = "도구 호출은 성공했으나 결과가 비어있음"
    return
```

리포트 렌더러(trace_report) 도 `empty` 상태를 "조회 완료, 결과 없음"
으로 표기하도록 업데이트.

---

## 작업 순서 제안 (코드 검증 후 조정됨)

1. **P0-1** (tools.py 어댑터 **4개** + 회귀 테스트) — 가장 짧고 영향이 큼.
   초기 문서는 3개라고 썼으나 `get_sample_rows` 포함 **4개**가 맞음.
2. **P1-2** (parse_db_source 스키마 접두사) — P0-1 과 함께 묶어서 PR 1건.
3. **P0-2-A** (`_format_db_execution_result` 3상태 개편) — ⭐ 진짜 루트 원인.
   프롬프트 수정 없이 Layer3 0건을 L2b 에 "FAIL" 로 주입하는 현재 동작을
   제거한다. 3줄짜리 수정.
4. **회귀 트레이스 재측정** — 위 3개 PR 이후 동일 질의 재실행.
   이 결과로 P0-2-B, P1-1 개선안 3번, P2-2 의 필요성을 재평가.
5. **P1-1** (sample_rows null vs empty) — 방어 코딩 차원에서 유지.
   단 "개선안 3번 자동 조회"는 **득실 분석 대기**(4번 측정 후 결정).
6. **P0-2-B** (L2b 프롬프트 few-shot + `failure_classification` 매핑 통일) —
   4번 측정에서 여전히 할루시네이션이 남아있을 때만 적용.
7. **P2-1** (code meta 승격) — 로그 실측으로 가설 1/2 확정 후 수정.
8. **P2-2** (empty result 텔레메트리) — P0-1 수정 후 "빈 결과" 의 의미가
   진짜 0건으로 바뀌므로 이 시점에 적용.

## 회귀 검증

본 문서의 기준 질의("작년말대비 여신 2배 이상 증가한 고객 중 주담대
보유 고객 목록")를 **골든셋에 추가**하고, P0 2건 수정 후 1라운드 내
SUCCESS 가 나는지 확인한다. 실패 유형별 분포도 함께 측정해
`EMPTY_RESULT` 경로가 제대로 분기되는지 검증한다.

## 관련 문서

- `20260405-failure-type-redesign.md` — failure_type 재설계 (P0-2 와 정합)
- `20260408-recovery-context-enrichment.md` — recovery 컨텍스트 (P1-1 연관)
- `20260411-connector-system-identity-refactor.md` — 커넥터 식별자 (P1-2 연관)
