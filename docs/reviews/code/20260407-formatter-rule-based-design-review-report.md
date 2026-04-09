# Formatter Rule-Based 전환 설계안 비판적 검토

**리뷰 일자**: 2026-04-07
**리뷰 대상**: formatter LLM 호출 제거 -> rule-based 전환 설계안
**리뷰어**: Code Reviewer Agent

---

## 검토 요약

설계안의 핵심 방향(LLM 호출 제거, rule-based 전환)은 레이턴시 절감, 토큰 비용 제거, 결정론적 출력이라는 확실한 이점이 있다. 그러나 코드 수준의 구체적 검토에서 **7건의 설계 결함**과 **5건의 주의사항**을 식별하였다.

---

## Critical (적색)

### C-1. DATA_ANALYSIS 경로에서 analysis_result가 포맷팅에 반영되지 않는 기존 문제가 설계안에서도 미해결

**현황**: `format_response_node`는 `state.analysis_result`를 트래킹 이벤트(`dispatch_tracking_event`)의 로그용으로만 참조하고(formatter.py:127-129), 실제 `format_response()` LLM 호출에는 `sql_result`만 전달한다(formatter.py:69-75). 즉, `analyze_data_node`가 생성한 인사이트(summary, insights 리스트)는 현재 LLM이 암묵적으로 sql_result를 보고 재생성하는 형태이며, analysis_result를 직접 활용하지 않는다.

**설계안의 문제**: rule-based 전환 시 LLM이 sql_result를 자유롭게 해석해주던 부분이 사라지므로, DATA_ANALYSIS 경로에서 "핵심 수치 요약" 템플릿이 분석 인사이트를 전혀 반영할 수 없게 된다. 템플릿 기반 요약("총 N건 조회, {top_label}이(가) {top_value}로 가장 높습니다")은 단순 조회 결과에는 적합하나, 분석 인사이트(추이, 이상치, 비교 결론)를 표현할 수 없다.

**제안**: format_response_node에서 `state.analysis_result`가 존재하면 `analysis_result.summary`와 `analysis_result.insights`를 rule-based 요약 대신 직접 사용하는 분기를 추가해야 한다. analysis_result는 이미 LLM이 생성한 자연어이므로 추가 포맷팅이 불필요하다.

```python
# 제안 구조
if state.analysis_result and state.analysis_result.summary:
    summary_section = state.analysis_result.summary
    if state.analysis_result.insights:
        summary_section += "\n" + "\n".join(
            f"- {insight}" for insight in state.analysis_result.insights
        )
else:
    summary_section = _build_rule_based_summary(sql_result, alias_type_map)
```

---

### C-2. simple_responder -> format_response 경로에서 sql_result 부재 시 rule-based 포맷팅 실패

**현황**: pipeline.py:559에서 `simple_responder -> format_response` 엣지가 존재한다. simple_responder는 `formatted_response`를 이미 설정하고 `status=COMPLETED`로 반환한다(simple_responder.py:83-93). 그런데 format_response_node는 `state.sql_result`를 전제로 동작한다.

**설계안의 문제**: rule-based 전환 시 `rows_to_markdown_table(sql_result.columns, sql_result.rows)`를 호출하는데, simple_responder 경로에서는 sql_result가 None이다. 현재 LLM 기반에서도 동일한 문제가 잠재적으로 존재하지만, LLM은 "(조회 결과 없음)"을 받아 유연하게 처리한다. rule-based에서는 None 체크 없이 크래시할 가능성이 높다.

**제안**: format_response_node 진입 시 `state.status == QueryStatus.COMPLETED`이면 (simple_responder가 이미 응답을 완성) 포맷팅을 건너뛰고 `state.formatted_response`를 그대로 반환하는 가드 절을 추가해야 한다. 또는 파이프라인 그래프에서 simple_responder -> END 직결로 변경하는 것이 더 깔끔하다.

---

### C-3. 컬럼 타입 판별의 커버리지 부족 -- 한글 alias fallback이 실제 은행 도메인에서 불충분

**설계안**: 원본컬럼 접미사(`_AMT`, `_RT`, `_CNT`) -> 한글 alias fallback("금액", "건수" 등)

**문제점**:

1. **정보계 DB의 실제 접미사 패턴이 다양**: 은행 정보계에서 `_BAL`(잔액), `_QTY`(수량), `_RTO`(비율, `_RT`와 다름), `_PRC`(가격), `_TAMT`(총금액), `_SAMT`(소계금액) 등 접미사 변종이 많다. 설계안이 `_AMT`, `_RT`, `_CNT` 3개만 나열한 것은 커버리지가 낮다.

2. **파생 컬럼 비중이 높음**: 은행 업무 쿼리에서 `SUM(A.LOAN_AMT) AS 여신합계`, `A.COL1 / A.COL2 * 100 AS 비율`, `CASE WHEN ... END AS 구분` 같은 파생 컬럼이 전체의 30-50%를 차지한다. 이 경우 `extract_select_alias_map`에서 `orig_col=None`이 반환되어 한글 fallback에 의존하는데, "합계"는 금액인지 건수인지 모호하고, "비율"은 인식 가능하나 "비중"은 누락될 수 있다.

3. **금액 단위 결정 불가**: 접미사로 "이것은 금액이다"를 판별해도, 원 단위인지 천원 단위인지 백만원 단위인지 알 수 없다. 현재 formatter_system.txt의 규칙 3은 원 단위를 전제로 "1조 이상: X조 X,XXX억원"으로 변환하지만, 정보계 테이블에 따라 천원, 백만원 단위로 저장된 금액이 섞여 있을 수 있다.

**제안**:
- 접미사 패턴을 설정 파일(YAML/JSON)로 외부화하여 폐쇄망 배포 시 확장 가능하게 설계
- MongoDB 컬럼 메타에 `data_type_hint`(currency, rate, count, text) 필드가 있다면 이를 우선 참조
- 단위 문제는 현 단계에서 "원 단위" 전제를 문서화하고, 추후 컬럼 메타에 단위 정보 추가를 로드맵에 포함

---

## Warning (황색)

### W-1. process_summary_builder 별도 서비스 분리 vs response_formatter 통합 -- 책임 경계 불명확

**설계안**: `src/services/process_summary_builder.py`를 신규 서비스로 분리

**분석**: 현재 프로젝트에서 "조회 과정 요약"은 두 곳에서 서로 다른 형태로 존재한다:
- `format_trace_summary()` (trace.py) -- trace_log 기반, 노드별 action 나열
- `build_insight()` (insight_builder.py) -- State 전체 기반, UI 통찰 패널용 구조화 데이터

설계안의 "5단계 조회 과정 요약"은 insight_builder가 이미 수행하는 작업과 상당 부분 겹친다. insight_builder의 `_build_reasoning_trail`, `_build_step_timings`, `_build_caveats` 등이 의도분류/질의해석/활용정보/AI판단/SQL검증을 이미 커버한다.

**제안**: process_summary_builder를 신규 생성하기보다, insight_builder의 기존 데이터를 "마크다운 텍스트"로 렌더링하는 함수를 insight_builder에 추가하거나, response_formatter.py에 `render_insight_as_markdown(insight: dict) -> str` 함수를 추가하는 것이 중복을 피하고 유지보수 지점을 줄인다.

---

### W-2. build_auto_resolved_notice와 5단계 요약의 "AI판단" 섹션 중복

**현황**: `build_auto_resolved_notice`(clarification_context.py:96-125)는 INFER 시그널을 "조회 기준 안내:" 형태로 결과 상단에 표시한다. 설계안의 5단계 중 "AI판단" 섹션도 자동 추론 내용을 포함할 것이다.

**분석**: 사용자 관점에서 같은 정보가 "결과 상단"과 "조회 과정 요약의 4번째 단계"에 중복 노출되면 혼란을 유발한다. 현재도 formatter.py:89-91에서 infer_notice를 상단에 붙이고, trace_summary에서도 관련 내용이 나올 수 있어 잠재적 중복이 존재한다.

**제안**: 두 가지 중 하나를 선택해야 한다:
- (A) 상단 `infer_notice`를 유지하되, 5단계 요약의 "AI판단"에서는 INFER 시그널을 제외
- (B) 상단 `infer_notice`를 제거하고, 5단계 요약에 통합

(A)가 현재 UX를 유지하면서 중복을 제거하는 더 안전한 선택이다.

---

### W-3. formatter_system.txt/formatter_user.txt 삭제 시 영향 범위가 system_prompts.py에 파급

**현황**: system_prompts.py:131-132에서 `FORMATTER_SYSTEM`과 `FORMATTER_USER`를 모듈 임포트 시점에 로드한다. `load_text_required`는 파일이 없으면 예외를 발생시키므로, 프롬프트 파일 삭제 시 **앱 전체가 시작 불가**하다.

**제안**: 프롬프트 파일 삭제와 system_prompts.py에서 해당 변수 제거를 반드시 동시에 수행해야 한다. 변경 파일 목록에 `src/agents/nodes/system_prompts.py`가 누락되어 있다. 또한 formatter.py에서 `FORMATTER_SYSTEM`, `FORMATTER_USER` import 구문도 함께 제거해야 한다.

**변경 파일 목록 보정**:
| 파일 | 변경 |
|------|------|
| `src/agents/nodes/system_prompts.py` | FORMATTER_SYSTEM, FORMATTER_USER 변수 제거 |
| `src/agents/nodes/present/formatter.py` | FORMATTER_SYSTEM/USER import 제거 |

---

### W-4. 핵심 수치 요약 템플릿의 질의 유형 대응력 부족

**설계안 템플릿**:
- 단일행: "{metric_col}은(는) {value}입니다."
- 다중행: "총 N건 조회, {top_label}이(가) {top_value}로 가장 높습니다."

**문제 시나리오**:
1. **추이 질의** ("월별 여신 실적 추이"): 단순히 "1월이 가장 높습니다"로는 추이의 핵심(증가/감소/변동)을 전달할 수 없다.
2. **비교 질의** ("A지점과 B지점 수신 비교"): "A지점이 가장 높습니다"는 비교의 차이 규모를 전달하지 못한다.
3. **다중 지표 단일행** ("이번 달 여신 건수와 금액"): metric_col이 2개 이상일 때 "건수은(는) 342입니다"만으로 불충분하다.

**제안**: 질의 유형(state.query_category 또는 normalized_query 구조)에 따른 템플릿 분기를 설계해야 한다. 최소 4개 유형이 필요하다:
- SIMPLE: 단일/소수 지표 조회 -> 현재 템플릿
- RANKING: 순위/TOP-N -> "상위 3건: A(xxx), B(xxx), C(xxx)"
- TREND: 추이 -> "X월 대비 Y월 Z% 증가/감소"
- COMPARISON: 비교 -> "A가 B보다 Z만큼 높음"

다만, 이 수준의 템플릿 분기는 복잡도가 급증하므로, 초기 버전에서는 SIMPLE 템플릿만 구현하고 나머지는 "데이터 표만 제공 + 요약 생략"으로 폴백하는 것이 현실적이다.

---

### W-5. 코드값 변환의 필요성 재검토 -- SQL Generator 규칙 10번과의 관계

**현황**: formatter_system.txt:29에 "명칭 컬럼이 이미 결과에 포함되어 있으면 그대로 사용하세요"라는 조건부 지시가 있다. 이는 SQL Generator가 `LOAN_DCD`와 함께 `LOAN_DCD_NM`(명칭 컬럼)을 SELECT에 포함하도록 규칙 10번에서 지시하고 있기 때문이다.

**분석**: SQL Generator가 규칙을 잘 따르면 명칭 컬럼이 이미 결과에 포함되어 코드값 변환이 불필요하다. 그러나:
- 명칭 컬럼이 없는 코드성 컬럼(코드 테이블 자체가 없거나, SQL Generator가 누락한 경우)은 여전히 존재할 수 있다
- `explored_codes`에 매핑이 있으면 rule-based로 변환하는 것은 저비용이므로 방어적으로 유지할 가치가 있다

**제안**: 코드값 변환 로직을 유지하되, 현재 `_build_code_mappings`(formatter.py:159-191)와 `_serialize_code_map`(formatter.py:194-223)은 "프롬프트용 텍스트 직렬화"에 최적화되어 있으므로, rule-based 전환 시에는 "dict[str, dict[str, str]]" 형태(컬럼명 -> {코드 -> 명칭})로 반환하는 새 함수를 만들어 셀 단위 치환에 사용해야 한다.

---

## Info (녹색)

### I-1. trace_log 유지 결정은 합리적 -- 단, add_trace 호출의 오버헤드는 무시 가능 수준

**분석**: add_trace 호출부가 6개 노드에 11회 존재하며(grep 결과), 각 호출은 TraceEntry Pydantic 모델 1건 생성 + 리스트 복사(`[*state.trace_log, entry]`)이다. 파이프라인당 최대 15-20개 엔트리로, 메모리/CPU 오버헤드는 무시 가능하다.

insight_builder의 `_calc_total_elapsed`(insight_builder.py:281-301)와 `_build_step_timings`(insight_builder.py:304-367)이 trace_log의 timestamp에 의존하므로, trace_log 제거 시 insight 패널의 소요 시간 표시가 불가능해진다. format_trace_summary 호출만 제거하는 설계안의 접근은 적절하다.

---

### I-2. rows_to_markdown_table 확장 vs 신규 함수 -- 확장이 적절

**현황**: `rows_to_markdown_table`(response_formatter.py:36-74)은 이미 천 단위 쉼표, None 처리, max_rows 제한을 수행한다.

**제안**: 설계안의 "format_report_table"을 별도 함수로 만들기보다, `rows_to_markdown_table`에 `column_type_map: dict[str, str] | None = None` 파라미터를 추가하여 셀 단위 포맷팅(금액 단위 변환, 비율 % 접미사, 건수 "건" 접미사)을 확장하는 것이 기존 `format_result_for_prompt` 호출을 깨뜨리지 않으면서 중복을 방지한다.

```python
def rows_to_markdown_table(
    columns: list[str],
    rows: list[dict[str, Any]],
    max_rows: int = 100,
    column_type_map: dict[str, str] | None = None,  # 추가
) -> str:
```

---

### I-3. format_result_for_prompt는 rule-based 전환 후에도 유지 필요

**이유**: `format_result_for_prompt`는 analyzer의 LLM 호출(analyze_data)에서도 사용될 수 있고, 향후 다른 노드에서 SQL 결과를 프롬프트에 주입할 때 재사용된다. formatter의 LLM 호출을 제거하더라도 이 함수 자체는 삭제하면 안 된다.

---

### I-4. extract_select_alias_map의 SELECT * 미지원 -- 문서화 필요

**현황**: sqlglot_analyzer.py:183-214의 `extract_select_alias_map`은 `SELECT *`일 때 빈 dict를 반환한다. SQL Generator가 `SELECT *`를 생성하지 않도록 규칙에서 금지하고 있으므로 실제 문제가 되진 않지만, rule-based 포맷팅에서 alias_map이 비어 있으면 모든 컬럼이 text 타입으로 폴백되어 숫자 포맷팅이 전혀 적용되지 않는다.

**제안**: `extract_select_alias_map` 반환값이 비었을 때의 폴백 전략을 명시적으로 설계해야 한다. 예: sql_result.columns의 이름 자체에서 접미사 판별을 시도하는 2차 폴백.

---

### I-5. 변경 파일 목록 누락 정리

설계안에서 누락된 변경 파일 전체 목록:

| 누락된 파일 | 필요한 변경 |
|-------------|-------------|
| `src/agents/nodes/system_prompts.py` | FORMATTER_SYSTEM, FORMATTER_USER 변수 및 로딩 코드 제거 |
| `src/agents/nodes/present/formatter.py` | system_prompts import에서 FORMATTER_SYSTEM/USER 제거 |
| `src/services/response_formatter.py` | `format_response` async 함수 제거 (LLM 의존성 전체 제거) |
| `src/utils/llm.py` (간접) | formatter가 유일한 사용처가 아닌지 확인 필요 -- analyzer에서도 사용하므로 유지 |

---

## 종합 판정

| 등급 | 건수 | 핵심 |
|------|------|------|
| Critical | 3건 | DATA_ANALYSIS 경로 미처리, simple_responder 경로 크래시, 컬럼 타입 커버리지 |
| Warning | 5건 | 서비스 중복, 정보 중복, system_prompts 연쇄 영향, 템플릿 대응력, 코드값 변환 구조 |
| Info | 5건 | trace_log 유지 적절, 함수 확장 방식, format_result_for_prompt 유지, SELECT * 폴백, 변경 파일 보정 |

**결론**: C-1(DATA_ANALYSIS 경로)과 C-2(simple_responder 경로)를 해결하지 않으면 두 경로에서 런타임 오류 또는 정보 손실이 발생한다. C-3(컬럼 타입)은 초기 버전에서 "접미사 패턴을 설정 파일로 외부화 + 미인식 시 text 폴백"으로 대응 가능하다. W-1(process_summary_builder 중복)은 insight_builder와의 관계를 정리한 후 진행해야 불필요한 유지보수 지점 증가를 방지할 수 있다.
