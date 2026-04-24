# Validator 체크 구조 재설계 + Decomposition 직렬화 개선

> 작성일: 2026-04-13
> 상태: 검토 중

## 배경

sql_validator는 생성된 SQL이 사용자 의도를 올바르게 반영했는지 8개 체크로 검증한다.
그런데 체크 1~4가 참조하는 `query_decomposition` 데이터가 정규화 원본 대비 심각하게 손실되어 있어서
오판(잘못된 FAIL)이 발생하거나 검증 자체가 불가능한 상태이다.

## 현황 분석

### 데이터 흐름

```
NormalizedQuery (8슬롯 전체)
  ↓ _build_decomposition_from_normalized()  ← 여기서 정보 손실
reason.query_decomposition (축소된 dict)
  ↓ serialize_decomp_slots()                ← json.dumps (가독성 나쁨)
validator 프롬프트의 {measures}, {filters}, ...
```

### 정보 손실 상세

| 정규화 원본 필드 | decomposition 전달 여부 | validator 영향 |
|---|---|---|
| measures.measure_type (RAW/RATIO/WINDOW) | X 손실 | RATIO 지표 검증 불가 |
| measures.note (산출식) | X 손실 | 산출식 정확성 검증 불가 |
| filters.position (PRE_AGG/POST_AGG) | X 손실 | HAVING 조건을 WHERE로 오판 |
| filters.note (비즈니스 규칙) | X 손실 | IMPLICIT 필터 맥락 손실 |
| dimensions.role (PARTITION/DISPLAY) | X GROUP만 전달 | PARTITION BY 검증 불가 |
| modifiers.direction (ASC/DESC) | X 손실 | 정렬 방향 검증 불가 |
| modifiers.by + limit 합쳐짐 | 부분 손실 | limit 있으면 by 무시됨 |
| intent, time, entities, ambiguities | X 미전달 | 체크 7이 원문만으로 추론해야 함 |

### 직렬화 문제

- `serialize_decomp_slots()`가 `json.dumps()`로 raw JSON 출력
- `null`, `false`, 빈 배열 `[]` 등 노이즈 그대로 노출
- Qwen3.5 같은 소형 모델에서 해석 정확도 저하

### 현재 체크 8개 평가

| 체크 | 설명 | 참조 데이터 | 판단 |
|------|------|------------|------|
| 1. measure 반영 | SQL 집계함수 확인 | decomposition.measures | 체크 7 흡수 가능 |
| 2. filter 반영 | WHERE 조건 확인 | decomposition.filters | **독립 유지** (조건 누락 감지) |
| 3. group_by 반영 | GROUP BY 확인 | decomposition.group_by | **독립 유지** (GROUP BY 누락 감지) |
| 4. order_limit 반영 | ORDER BY/LIMIT 확인 | decomposition.order_limit | 체크 7 흡수 가능 |
| 5. 미확인 값 | 코드값/컬럼 검증 | confirmed_terms, code_mappings | **필수 유지** |
| 6. dead_end 반복 | 실패 패턴 반복 | dead_ends | **필수 유지** |
| 7. 논리적 정합성 | 원문 의도 대조 | original_query + 전체 컨텍스트 | **필수 유지 + 보강** |
| 8. DB 실행 결과 | 실행 성공 여부 | db_execution_result | **필수 유지** |

### 체크 1 흡수 근거

- EXTRACT 질의: measures 빈 배열 → 검증 대상 없음
- RATIO 질의: agg_function=NONE, 산출식 note 손실 → 검증 불가
- AGGREGATE 질의: 체크 7의 "질의 유형 반영" 관점에서 동일하게 감지 가능
- measure_type 손실로 "적절한 집계함수" 판정 자체가 부정확

### 체크 4 흡수 근거

- order_limit에 SORT/RANK/DELTA/DELTA_RATE/CUMULATIVE 등 이질적 modifier 혼재
- DELTA/DELTA_RATE는 ORDER BY/LIMIT과 무관 (서브쿼리/윈도우함수 영역)
- direction, by 손실로 "상위/하위", "기준 지표" 검증 불가
- 오히려 잘못된 decomposition이 오판 유발 위험

### 체크 2, 3 유지 근거

- 체크 2 (filter): "사용자가 말한 조건이 빠짐없이 SQL에 반영되었는가"는 체크 7과 다른 관점. 코드값 필터, 기간 필터, 암묵적 조건 누락은 흔한 오류
- 체크 3 (group_by): "지점별"이라고 했는데 GROUP BY 없이 전체 집계하는 오류는 흔함. 구조적 검증으로 명시적 스캐폴딩 필요

## 개선 방향

### 1. 체크 구조 재편 (8개 → 6개)

```
[유지]
  체크 1: filter 반영 (현 체크 2)
  체크 2: group_by 반영 (현 체크 3)
  체크 3: 미확인 값 사용 (현 체크 5)
  체크 4: dead_end 반복 (현 체크 6)
  체크 5: 논리적 정합성 (현 체크 7, 보강) ← 체크 1,4 관점 흡수
  체크 6: DB 실행 결과 (현 체크 8)

[흡수 → 체크 5에 통합]
  현 체크 1 (measure 반영) → 체크 5 논리적 정합성 내 "지표/산출 방식" 관점
  현 체크 4 (order_limit 반영) → 체크 5 논리적 정합성 내 "결과 가공/정렬" 관점
```

### 2. Decomposition 직렬화 개선

#### 선택지 A: serialize_decomp_slots 가독성 개선
- 현행 json.dumps → 사람/LLM 가독성 좋은 텍스트 포맷
- _build_decomposition_from_normalized에 누락 필드 추가
- 영향 범위: prompt.py, reasoning_preparer.py

#### 선택지 B: validator 전용 직렬화 (state.normalized_query 직접 사용)
- serialize_decomp_slots 폐기, NormalizedQuery에서 직접 직렬화
- 체크 2, 3에 필요한 정보만 정확하게 전달
- 영향 범위: sql_validator.py에 전용 함수 추가

#### 선택지 C: decomposition 폐기, NormalizedQuery 요약 블록으로 대체
- 체크 1~4 슬롯별 플레이스홀더 제거
- NormalizedQuery 전체를 하나의 "정규화 요약" 블록으로 전달
- 체크 2, 3이 참조할 데이터를 이 블록에서 제공

### 3. 체크 7 (논리적 정합성) 보강 포인트

현재 검증 포인트에 추가해야 할 관점:
- **지표/산출 방식**: 사용자가 요청한 지표(건수/금액/비율)가 SQL에 올바른 방식으로 표현되었는가
- **결과 가공**: 순위/증감/변화율/누적 등 결과 가공이 SQL 구조에 반영되었는가
- **정렬/제한**: 상위 N건, 정렬 방향이 의도대로인가

## 영향 범위

- `resources/prompts/reason/sql_validator_system.txt` — 프롬프트 재작성
- `src/agents/nodes/reason/sql_validator.py` — _validate_layer2b, 직렬화
- `src/utils/llm/prompt.py` — serialize_decomp_slots (선택지에 따라)
- `src/agents/nodes/reason/reasoning_preparer.py` — _build_decomposition (선택지에 따라)
- 테스트 — validator 관련 테스트 업데이트

## 미결 사항

- [ ] 체크 2(filter)에 position(WHERE/HAVING) 정보를 어떻게 전달할지
- [ ] 체크 3(group_by)에 PARTITION role을 포함할지, GROUP만 유지할지
- [ ] 직렬화 선택지 A/B/C 중 최적안 결정
- [ ] 체크 7 보강 시 프롬프트 토큰 증가량 vs Qwen3.5 성능 트레이드오프
- [ ] 기존 체크 번호 변경에 따른 하류 코드 영향 (fix_instruction 생성 로직 등)
