# Validator 체크 구조 재설계 — 전문가 리뷰 종합 결론

> 작성일: 2026-04-13
> 근거: Expert A (SQL 검증 아키텍처 전문가) + Expert B (LLM-as-Judge 프롬프트 전문가) 4라운드 비판적 리뷰 종합
> 상태: 최종 권고안

---

## 1. 종합 판단 요약

| 결정 항목 | 최종 권고 | 채택 근거 |
|-----------|-----------|-----------|
| **체크 수** | 9개 → **8개** | measure 제거 + order_limit 축소, 나머지 유지 |
| **직렬화** | **선택지 B** (validator 전용, NormalizedQuery 직접) | serialize_decomp_slots은 validator만 사용 → 이중 경로 불필요 |
| **few-shot** | **7개 예시** (RATIO 케이스 추가) | 체크 구조 변경 전면 반영 필수 |

---

## 2. 체크 구조 최종안: 8개

### 2.1 구성

| 새 번호 | 체크 이름 | 이전 번호 | 변경 내용 |
|---------|-----------|-----------|-----------|
| 1 | filter 반영 | 2 | **보강**: position(PRE_AGG/POST_AGG), note(IMPLICIT) 추가 |
| 2 | group_by 반영 | 3 | **보강**: PARTITION role 포함 |
| 3 | order_rank 반영 | 4 (축소) | **축소**: SORT/RANK/LIMIT만. direction/limit/by 복원 |
| 4 | 미확인 값 사용 | 5 | 변경 없음 |
| 5 | dead_end 반복 | 6 | 변경 없음 |
| 6 | 논리적 정합성 | 7 (보강) | **보강**: measure 흡수 + DELTA/CUMULATIVE 흡수, 7 CoT 서브항목 |
| 7 | DB 실행 결과 | 8 | 변경 없음 |
| 8 | 코드 명칭 동반 | 9 | 변경 없음 |

### 2.2 제거/흡수 항목

| 현행 체크 | 처리 | 흡수 위치 |
|-----------|------|-----------|
| 체크 1 (measure 반영) | **제거** | 체크 6 서브항목 ③ "지표 산출 방식" |
| 체크 4 중 DELTA/DELTA_RATE/CUMULATIVE | **흡수** | 체크 6 서브항목 ④ "결과 가공" |

### 2.3 체크 6 (논리적 정합성) 서브항목 구조

CoT 방식으로 순서대로 판단하도록 프롬프트에 명시:

```
① 기간 반영 — 사용자 기간 표현이 WHERE 절에 올바르게 적용되었는가
② 질의 유형 반영 — RANK/TREND/COMPARE 등 SQL 구조 요건 충족되었는가
③ 지표 산출 방식 — 요청한 지표(건수/금액/비율/증감 등)가 SQL에 올바르게 표현되었는가
④ 결과 가공 — 증감(DELTA)/변화율(DELTA_RATE)/누적(CUMULATIVE)이 SQL 구조에 반영되었는가
⑤ 대상 반영 — 사용자 언급 대상(예금/대출 등)이 적절한 테이블에서 조회되는가
⑥ AI 추론 교차검증 — reasoning_decisions와 SQL 로직이 일관되는가
⑦ 0건 판정 — 0건인 경우 4개 fail 조건 중 해당 여부 확인
```

---

## 3. 각 쟁점별 판단 근거

### 3.1 체크 4 처리: SORT/RANK/LIMIT 독립 유지 (Expert A 채택)

**Expert A**: SORT/RANK/LIMIT만 분리하여 독립 체크로 잔존. DELTA/CUMULATIVE만 체크 6으로.
**Expert B**: 전체 흡수 → 체크 6 서브항목으로.

**채택 근거**:

1. **데이터 복원 전제**: P0에서 decomposition에 direction/limit/by를 복원하면, SORT/RANK/LIMIT 패턴은 `ORDER BY {direction} LIMIT {limit}` 구조와 1:1 대조 가능 → Expert B의 "근거 부실" 논거가 해소됨
2. **금융 도메인 빈도**: "상위 N건" 순위 질의는 은행 업무에서 매우 빈번 (실적 순위, 거래량 순위, 잔액 순위 등)
3. **Qwen3.5 스캐폴딩**: ORDER BY+LIMIT 존재 여부는 단순 구조 대조 → 독립 체크가 LLM에게 명확한 지시
4. **이질적 modifier 해소**: DELTA/CUMULATIVE를 분리하면 "order_limit에 ORDER BY와 무관한 타입 혼재" 문제가 해결됨

**반례 대응**: Expert B가 지적한 "direction 손실로 오판" 위험은 P0 데이터 복원으로 해결. 복원 후 체크 3은 `RANK, DESC, limit=10, by=잔액` 형태의 명확한 데이터를 받게 됨.

### 3.2 체크 9 처리: 독립 유지 (Expert B 채택)

**Expert A**: 체크 8과 통합하여 "execution_and_output"으로.
**Expert B**: 독립 유지.

**채택 근거**:

1. **역할 분리**: 체크 7(DB 실행)은 "SQL이 실행 가능한가", 체크 8(코드 명칭)은 "결과가 UX 친화적인가" → 판단 기준이 다름
2. **fix_instruction 명확성**: 코드 명칭 누락은 항상 local_fix이며 수정 지시가 명확 ("코드 컬럼에 명칭 JOIN 추가"). DB 실행 실패와 합치면 failure_classification이 모호해질 위험
3. **비용 대비 효과**: 독립 체크 1개 유지 비용(JSON 필드 1개, 프롬프트 2~3줄) vs 감지 혼합의 위험 → 유지가 안전

### 3.3 직렬화: 선택지 B (Expert B 채택)

**Expert A**: 선택지 D (하이브리드 — serialize_decomp_slots 개선 + validator 보조 블록)
**Expert B**: 선택지 B (validator 전용, NormalizedQuery 직접 참조)

**채택 근거**:

1. **핵심 사실**: `serialize_decomp_slots()`는 **sql_validator에서만 사용** (sql_generator는 이미 별도 경로). Expert A의 "다른 소비자에도 혜택"이 성립하지 않음
2. **정보 손실 근본 해결**: NormalizedQuery에서 직접 추출하면 중간 변환(_build_decomposition_from_normalized) 손실 문제가 원천 차단
3. **이중 경로 회피**: 선택지 D는 "기존 경로 개선 + 보조 블록 추가" → 결국 두 경로 유지보수. 선택지 B는 validator 전용 단일 경로
4. **영향 범위 국한**: sql_validator.py에 전용 직렬화 함수만 추가. _build_decomposition_from_normalized은 그대로 유지 (recovery_agent 등이 참조)

**직렬화 블록 설계 방향**:

```
## 질의 정규화 요약

의도: AGGREGATE
대상 지표:
  - 건수 [RAW] (집계: COUNT)
  - 연체율 [RATIO] — 산출: 연체대출잔액 합계 / 총대출잔액 합계
필터 조건:
  - 기간: GTE "이번 달" [PRE_AGG]
  - 신규: IMPLICIT [PRE_AGG] — 대출 상태가 '신규'인 건만 포함
그룹핑:
  - 지점 [GROUP]
  - 영업점 [PARTITION]
결과 가공:
  - RANK DESC limit=10 by=잔액
```

특징:
- null/빈 배열 노이즈 완전 제거 (값 없으면 해당 섹션 생략)
- measure_type, note, position, direction, by 모두 포함
- 체크 1(filter), 체크 2(group_by), 체크 3(order_rank)이 참조할 데이터를 정확하게 제공
- 체크 6(논리적 정합성)이 전체 맥락을 한눈에 파악 가능

---

## 4. 구현 계획

### Phase 1: 직렬화 전환 (P0)

**영향 파일**: `src/agents/nodes/reason/sql_validator.py`

- validator 전용 직렬화 함수 추가: `_serialize_normalized_for_validation(nq: NormalizedQuery) -> str`
- NormalizedQuery에서 직접 추출하여 위 포맷으로 직렬화
- 기존 `serialize_decomp_slots()` 호출 제거
- 기존 5개 플레이스홀더({measures}, {filters}, {group_by}, {order_limit}, {output_hint}) → 1개 {normalized_summary} 블록으로 통합

**영향 파일**: `src/utils/llm/prompt.py`
- `serialize_decomp_slots()` → 사용처 없으면 제거, 있으면 deprecated 표시

**예상 공수**: 1일

### Phase 2: 프롬프트 재작성 (P1)

**영향 파일**: `resources/prompts/reason/sql_validator_system.txt`

- [RULES] 체크 정의: 9개 → 8개 재작성
  - 체크 1(measure_reflected) 제거
  - 체크 4(order_limit_reflected) → order_rank_reflected로 축소 (SORT/RANK/LIMIT만)
  - 체크 7(logical_consistency) → 7개 CoT 서브항목 추가
- [HALLUCINATION_GUARD] 업데이트: 흡수된 관점 반영
- [EXAMPLES] 전면 재설계: 7개 예시 (RATIO 케이스 추가)
- [OUTPUT_CONTRACT] JSON 스키마 업데이트: 8개 체크 키
- [CONTEXT] 플레이스홀더 업데이트: {normalized_summary} 단일 블록
- [TASK] 재강조 항목에 "체크 6에서 지표 산출 방식과 결과 가공 반드시 확인" 추가

**예상 공수**: 2~3일

### Phase 3: 하류 코드 업데이트 (P2)

**영향 파일**:
- `src/services/insight_builder.py` (509~517행, 1031~1039행): 체크 키 → 한국어 라벨 매핑 업데이트
- `src/agents/nodes/reason/sql_validator.py`: _validate_layer2b 결과 파싱 업데이트

**예상 공수**: 0.5일

### Phase 4: 검증 (P3)

- 기존 validator 테스트 케이스 → 새 체크 구조에 맞게 업데이트
- 핵심 테스트 케이스:
  - RATIO 질의 (연체율): 체크 6 ③ 정확도
  - RANK 질의 (상위 N건): 체크 3 정확도 (direction/limit 대조)
  - DELTA 질의 (전월 대비 증감): 체크 6 ④ 정확도
  - AGGREGATE 단순 질의: 체크 6 ③이 COUNT/SUM 불일치 감지하는지
- false positive 비교: RATIO/DELTA 질의에서 현행 대비 오판 감소 확인

**예상 공수**: 1~2일

### 총 예상 공수: 5~7일

---

## 5. 체크 키 매핑 (현재 → 최종안)

| 현재 키 | 현재 번호 | 최종 키 | 최종 번호 | 비고 |
|---------|----------|---------|----------|------|
| measure_reflected | 1 | — | — | 제거 → 체크 6 ③ |
| filters_reflected | 2 | filters_reflected | 1 | position/note 보강 |
| group_by_reflected | 3 | group_by_reflected | 2 | PARTITION role 추가 |
| order_limit_reflected | 4 | order_rank_reflected | 3 | SORT/RANK/LIMIT만, direction/limit/by 복원 |
| no_unconfirmed_values | 5 | no_unconfirmed_values | 4 | 변경 없음 |
| no_dead_end_repeat | 6 | no_dead_end_repeat | 5 | 변경 없음 |
| logical_consistency | 7 | logical_consistency | 6 | 7 CoT 서브항목 보강 |
| db_execution | 8 | db_execution | 7 | 변경 없음 |
| code_name_paired | 9 | code_name_paired | 8 | 변경 없음 |

---

## 6. 리스크와 완화

| 리스크 | 심각도 | 완화 방안 |
|--------|--------|-----------|
| 체크 6 과부하 → measure 불일치 누락 | 중 | 7 CoT 서브항목으로 구조화 + [TASK]에 재강조 + P3 AGGREGATE 집중 테스트 |
| 체크 3(order_rank) 데이터 복원 불완전 | 중 | P0에서 direction/limit/by 복원을 체크 3과 함께 검증 |
| Qwen3.5 JSON 출력 안정성 | 중 | 9→8개로 필드 감소. OUTPUT_CONTRACT에 boolean 타입 명시, 한국어 출력 지시 |
| few-shot 예시 불일치 | 높 | P1에서 예시 전면 재설계 필수. 예시-CONTRACT 형식 일관성 검증 |
| insight_builder 매핑 불일치 | 낮 | P2에서 2곳 동시 업데이트 |
| fix_instruction 구체성 저하 | 중 | 체크 6 FAIL 시 "어떤 서브항목에서 불일치인지 명시" 의무화 |

---

## 7. Expert A/B 채택 총괄

| 쟁점 | Expert A 주장 | Expert B 주장 | 채택 | 이유 |
|------|--------------|--------------|------|------|
| 체크 4 처리 | SORT/RANK/LIMIT 독립 유지 | 전체 흡수 | **A** | 데이터 복원 후 구조 대조 가능, 금융 도메인 빈도 높음 |
| 체크 9 처리 | 체크 8과 통합 | 독립 유지 | **B** | 역할 분리 명확, fix_instruction 구체성 보존 |
| 직렬화 | 선택지 D (하이브리드) | 선택지 B (전용) | **B** | 유일 소비자=validator, 이중 경로 불필요, 정보 손실 근본 해결 |
| 체크 6 보강 | 서브섹션 추가 | 7 CoT 서브항목 | **B** | CoT 구조가 Qwen3.5에서 순차 판단 강제에 효과적 |
| few-shot 수 | 미명시 (최소 3개 갱신) | 7개 예시 (RATIO 추가) | **B** | RATIO/DELTA 커버리지 필수 |
| 구현 순서 | P0→P1→P2→P3→P4 | 직렬화→체크→예시 순서 | **A** | 단계적 검증 가능한 A의 구체적 우선순위가 실용적 |
