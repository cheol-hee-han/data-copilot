# 유사 테이블 비교 기능 재설계

## 배경

- `table_comparison_system.txt` 프롬프트가 v2에서 생성되었으나 현재 미사용 (import만 존재)
- knowledge_interpreter가 배치 해석에서 selected/rejected를 동시 처리하고 있음
- 정보계 DB에 같은 도메인의 유사 테이블이 다수 존재하는 것이 프로젝트 핵심 도전
- state-architecture.md에서도 갭으로 인식: "비교 결과가 knowledge에 구조적으로 저장되지 않음"

## 현재 문제

1. knowledge_interpreter가 도구 결과 해석 + knowledge 승격 + 테이블 판정 + use_case 평가를 동시 수행 → 판단 부담 과중
2. 유사 테이블 비교에 특화된 기준(갱신주기, 스냅샷 vs 이력, 집계 수준)이 프롬프트에 없음
3. 유사 테이블 3개 이상 감지 시 비교 판정의 질이 떨어질 수 있음

## 제안: recovery_agent의 도구로 구현

### 핵심 컨셉

SQL 생성이 실패하거나 결과가 부정확할 때, recovery_agent가 **selected + rejected 포함 전체 후보 테이블**을 재점검하여 더 적합한 테이블을 도출하는 도구.

- 첫 번째 해석(knowledge_interpreter)에서 선택한 테이블로 SQL 생성이 실패한 경우, 처음에 rejected된 테이블이 실제로는 더 적합할 수 있음
- 예: TB_LOAN_MASTER(원장)를 선택했으나 0건 → 실제로는 TB_LOAN_DAILY_BAL(일별 잔액)이 적합
- recovery가 "왜 실패했는지"를 알고 있는 상태에서 전체 후보를 다시 비교하므로, 첫 해석보다 정확한 판정 가능

### 왜 recovery 도구인가

- recovery_agent가 이미 "뭐가 부족한지 진단 → 도구로 해소" 구조
- 실패 원인(dead_ends)을 알고 있는 상태에서 비교하므로, 단순 메타 비교보다 판정 질이 높음
- "유사 테이블 중 어떤 걸 써야 할지 모호" = UNRESOLVED 또는 CONFLICTED 상태
- knowledge_interpreter에 조건부 분기 추가보다 단순

### 구현 방안

1. **도구 추가**: `compare_tables(table_names)` — 후보 테이블 목록(selected+rejected)과 실패 사유를 입력받아 재비교 판정
2. **프롬프트 개선**: table_comparison_system.txt에 비교 특화 기준 추가
   - 갱신주기: 일배치 > 월배치 (최신성)
   - 데이터 범위: 질의 시간 조건을 포함하는 테이블 우선
   - 집계 수준: 건별 상세 질의 → 원장, 집계 질의 → 집계 테이블
   - 스냅샷 vs 이력: 특정 시점 → 스냅샷, 변화 추이 → 이력 테이블
   - 이전 실패 사유 반영: dead_ends의 테이블/조건 조합을 피하는 방향으로 판정
3. **입력 구조**: 전체 candidate_tables (selected+rejected) + dead_ends + 질의 분해
4. **출력 구조**: knowledge_interpreter와 동일하게 `[{"table_name": "...", "reason": "..."}]`
5. **예시 추가**: 유사 테이블 3개 비교 예시 최소 1개 (첫 선택 실패 → 재비교로 더 적합한 테이블 도출)
6. **recovery 프롬프트**: 도구 목록에 `compare_tables` 추가, 활용 시점 안내
   - "SQL 실행 실패/0건이고, 후보 테이블에 유사 테이블이 2개 이상이면 compare_tables로 재점검"

### 호출 흐름

```
1차: knowledge_interpreter → TB_A(selected), TB_B(rejected), TB_C(rejected)
    → sql_generator → TB_A로 SQL 생성 → 실패 (0건 또는 에러)
    → sql_validator → FAIL (structural)
    → recovery_agent 진입
    → 분석: "TB_A로 실패했고, 유사 테이블 TB_B, TB_C가 있다"
    → compare_tables(TB_A, TB_B, TB_C) 호출
        - 입력: 전체 후보 + 실패 사유 + 질의 시간 조건
        - 판정: "TB_B가 일별 잔액 테이블로 시간 조건에 더 적합"
    → TB_B를 selected로 전환, TB_A를 rejected로 전환
    → readiness_gate 재평가 → sql_generator 재시도
```

### 대안: knowledge_interpreter 내부 조건부 호출

- selected 중 같은 주제영역 테이블이 2개 이상이면 table_comparison LLM 자동 호출
- 장점: recovery 루프 없이 1차 해석에서 해소
- 단점: knowledge_interpreter의 복잡도 증가, 실패 사유 없이 비교하므로 판정 근거가 약함

## 우선순위

중 — 현재 knowledge_interpreter의 배치 판정으로도 동작은 하지만, 유사 테이블이 많은 실제 은행 데이터에서 정확도 개선 필요

## 관련 파일

- `resources/prompts/reason/table_comparison_system.txt` — 현재 미사용 프롬프트
- `src/agents/nodes/reason/knowledge_interpreter.py` — TABLE_COMPARISON_SYSTEM import만 존재
- `docs/architecture/architecture.md:559` — 유사 테이블 구분 전략 설명
- `docs/architecture/state-architecture.md:490` — 비교 결과 구조화 갭 인식
