# Formatter 재설계 — LLM 의존 축소 + 코드 기반 포맷팅

## 배경

- formatter LLM 호출이 `format_max_rows=50`행만 포맷팅 → SQL LIMIT 1000과 불일치, 나머지 950행은 원본 그대로
- SQL Generator가 한글 alias, 명칭 컬럼 포함 등 포맷팅 역할을 이미 상당 부분 수행
- 금액/날짜/건수 포맷팅은 코드로 전체 행에 일괄 적용 가능
- formatter LLM의 실질적 고유 역할: 결과 데이터 기반 요약 한줄 생성

## 목표

1. 코드 기반 포맷팅으로 전체 행에 일괄 적용 (50행 제한 해소)
2. formatter LLM 역할을 "결과 요약"으로 경량화
3. 토큰 절감 + 응답 속도 향상

## 구현 항목

### 1. 코드 기반 포맷팅 (response_formatter.py 확장)

`rows_to_markdown_table`의 `_fmt`를 컬럼 타입 인식 포맷터로 확장:

- **금액**: 컬럼명에 AMT/금액/잔액/합계 등 포함 시 → 조/억/만/원 단위 변환
- **날짜**: 컬럼명에 DT/DATE/YMD/일자 등 포함 시 → 한국어 변환
  - `20240315` → 2024년 3월 15일
  - `202403` → 2024년 3월
  - `2024-03-15` → 2024년 3월 15일
  - `2024-03` → 2024년 3월
  - `2024-03-15 14:30:00` → 2024년 3월 15일 14:30
  - `2024` → 2024년
- **건수**: 컬럼명에 건수/CNT/COUNT 포함 시 → `X,XXX건`
- **비율**: 컬럼명에 율/비율/RATE 포함 시 → `X.X%`
- **코드값 치환**: code_map + alias_map으로 코드값 → 한글명칭 직접 치환

### 2. formatter LLM 경량화

포맷팅된 테이블을 넘기고 "결과 요약 한줄"만 생성하도록 프롬프트 축소.
숫자/날짜/코드 변환 규칙은 프롬프트에서 제거 (코드가 처리하므로).

### 3. SQL prettier

`sqlglot.transpile(sql, pretty=True)`로 trace_summary 표시용 SQL 정렬.

### 4. SQL Generator explanation → state 저장

현재 `_parse_sql_response`에서 explanation을 파싱하지 않고 버리고 있음.
explanation을 ReasoningState에 저장하여 formatter에서 조회 설명으로 활용.

- `state.py`: ReasoningState에 `explanation: str = ""` 필드 추가
- `sql_generator.py`: `_parse_sql_response`에서 explanation 추출 + state 저장

## 변경 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `src/services/response_formatter.py` | `_fmt_amount`, `_fmt_date`, `_fmt_cell`, `_apply_code_map` 추가 |
| `src/agents/nodes/present/formatter.py` | 코드 기반 포맷팅 호출 + LLM 경량화 |
| `src/agents/nodes/reason/sql_generator.py` | `_parse_sql_response` 수정, explanation state 저장 |
| `src/agents/state/state.py` | ReasoningState에 `explanation` 필드 추가 |
| `src/utils/sqlglot_analyzer.py` | `prettify_sql` 함수 추가 |
| `resources/prompts/present/formatter_system.txt` | 숫자/날짜/코드 규칙 제거, 요약 생성에 집중 |

## 우선순위

중 — 현재도 동작하지만, 50행 제한 문제와 LLM 토큰 비효율이 존재

## 관련 파일

- `docs/reviews/design/20260403-prompt-quality-review.md` — FM-1(조치완료), FM-2(조치완료), FM-3(미처리)
- `docs/reviews/code/20260403-fm1-code-map-injection-review-report.md` — FM-1 코드 리뷰
