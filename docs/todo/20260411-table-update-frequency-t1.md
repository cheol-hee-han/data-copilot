# 테이블 갱신주기(T-1) 처리 설계

## 배경

정보계 DB는 테이블마다 적재주기가 다름 (실시간 / T-1 / T-2 / 월배치).
현재 sql_generator는 이 정보를 모른 채 `CURRENT_DATE`를 그대로 사용 →
"이번 달 여신" 같은 질의에서 T-1 테이블이면 기준일 오차로 데이터 누락 발생 가능.

## 현재 상태

| 항목 | 상태 | 위치 |
|---|---|---|
| 갱신주기 메타 필드 | 없음 | `devtools/scripts/seed_mongodb.py:110` dpasset_table 스키마에 `update_frequency` 미존재 |
| confirmed_terms 전달 경로 | 있음 (단, 주기 미포함) | `src/agents/nodes/reason/sql_generator.py:627` `format_confirmed_text()` |
| 날짜 프로파일링 도구 | 있음 (lag 판정 X) | `src/agents/nodes/reason/tools.py:409` `get_date_distribution`, `detect_date_pattern` |
| SQL 예시 T-1 보정 | 없음 | 모든 예시가 `CURRENT_DATE` / `GETDATE()` 무보정 사용 |

## 설계 방향

두 경로를 **정적 선언 + 동적 관찰**로 역할 분리.

### 1. 정적 선언 (메타 기반) — 1순위

- `dpasset_table`에 필드 추가:
  - `update_frequency`: REALTIME / T-1 / T-2 / MONTHLY
  - `base_date_column`: 기준일자 컬럼명
- `context_retriever`가 테이블 확정 시 `KnowledgeItem.value`에 갱신주기 포함
- `format_confirmed_text()`가 "갱신: T-1" 토큰 직렬화
- sql_generator `[RULES]`에 "갱신주기 T-N인 테이블은 기준일 = `CURRENT_DATE - N`" 1줄 규칙 추가

비용: 메타 1회 시딩 + 규칙 1줄. 신뢰도 높음.

### 2. 동적 관찰 (프로파일링) — 2순위 / 폴백

- `get_date_distribution`의 `recent_values` 최댓값 vs `{current_date}` 차이로 lag 추정
- `detect_date_pattern()`에 `estimated_lag_days` 필드 추가
- 메타에 갱신주기가 없거나 신뢰도 낮을 때만 사용

### 3. 충돌 해결

- 메타(선언) > 프로파일(관찰)
- 프로파일 lag이 메타와 2일 이상 차이나면 경고 로그 + recovery 라우팅 고려

## 작업 순서

1. `dpasset_table` 스키마에 `update_frequency`, `base_date_column` 추가 + seed 데이터 보강
2. `KnowledgeItem` → `format_confirmed_text()` 경로에 갱신주기 노출
3. sql_generator `[RULES]`에 T-N 보정 규칙 1줄 + 예시 1개에 T-1 보정 반영 (선택)
4. (이후) `detect_date_pattern`에 lag 추정 폴백 추가

1~3만으로도 근본 해결 가능.

## 관련 논의

- `{current_date}` 주입값은 **LLM 추론용**(자연어 날짜 해석)으로 유지, SQL 본문은 DB 함수 사용 원칙은 변경 없음
- T-1 보정은 `{current_date}` 주입으로는 해결 불가 — 테이블별 메타가 단일 진실원
