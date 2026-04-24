# 99. Verification SQL — 시딩 결과 검증 가이드

본 문서는 **시딩 완료 후 데이터 정합성을 검증**하는 방법을 정의한다.
`business_rules.json`의 79개 규칙 + FK 무결성 + 분포 통계를 전수 검증한다.

---

## 1. 자동 생성된 SQL

실제 실행 쿼리는 **자동 생성**된다:

```bash
python3 scripts/build_verification_sql.py
```

**산출물:**
- `meta/verification_queries.sql` — 1,237 라인, 약 301 쿼리 
- `meta/verification_index.md` — 쿼리 인덱스

business_rules.json 규칙이 변경되면 스크립트 재실행으로 SQL 재생성.

---

## 2. 실행 방법

### 2.1 전체 실행

```bash
# PostgreSQL에 연결해 전체 검증 실행
psql -h localhost -U bank -d bank_v2 -v ON_ERROR_STOP=0 \
     -f meta/verification_queries.sql > verification_report.txt 2>&1

# violations 0이 아닌 쿼리만 추출
grep -B1 "violations" verification_report.txt | awk '$1 != "0"' > failures.txt
```

### 2.2 섹션별 실행

SQL은 12 섹션으로 구분됨:

| 섹션 | 내용 | 예상 쿼리 수 |
|---|---|---|
| 1. 기본 검증 | 행 수, 빈 테이블, 스키마 | 3 |
| 2. FK 참조 무결성 | Top 20 마스터 기반 orphan | ~100 |
| 3. 날짜 순서 | date_order 규칙 25개 | 25 |
| 4. 금액 범위·관계 | amount 규칙 19개 | 19 |
| 5. 상태 전이 | status 규칙 10개 | 10 |
| 6. 크로스 테이블 | 합산·참조 9개 | 9 |
| 7. 코드 값 연계 | code 규칙 8개 | 8 |
| 8. 카디널리티 제약 | unique/max 4개 | 4 |
| 9. 파생값 관계 | NPL·CIR 등 4개 | 4 |
| 10. 품질결함 비율 | V2 의도적 결함 | 3 |
| 11. 분포 통계 | 성별·통화 등 샘플 | 5 |
| 12. 요약 리포트 | 전체 통합 대시보드 | 1 |

---

## 3. 대표 쿼리 샘플

### 3.1 FK 참조 무결성

```sql
-- DPG001M.CSN → CSC001M.CSN orphan 체크
SELECT 'FK_ORPHAN_DPG001M_CSN' AS check_name, COUNT(*) AS violations
FROM DPG001M c
WHERE c.CSN IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM CSC001M p WHERE p.CSN = c.CSN);
```

### 3.2 날짜 순서

```sql
-- 대출 실행일 < 만기일
SELECT 'lnb001m_date_03' AS check_name, COUNT(*) AS violations
FROM LNB001M
WHERE NOT (EXEC_YMD < MAT_YMD);
```

### 3.3 금액 제약

```sql
-- 대출 잔액 ≤ 실행금액
SELECT 'lnb001m_amt_02' AS check_name, COUNT(*) AS violations
FROM LNB001M
WHERE NOT (LON_BAL <= LN_AMT);

-- 연체 원금 ≤ 대출 잔액 (cross-table)
SELECT 'lno001m_amt_02' AS check_name, COUNT(*) AS violations
FROM LNO001M a JOIN LNB001M b USING (LON_NO)
WHERE NOT (LNO001M.OVDU_PRN_AMT <= LNB001M.LON_BAL);
```

### 3.4 상태 전이

```sql
-- 해지 계좌(04)는 잔액 0
SELECT 'dpg001m_stat_03' AS check_name, COUNT(*) AS violations
FROM DPG001M
WHERE (LDGR_SCD = '04') AND NOT (BAL = 0);
```

### 3.5 크로스 테이블 (합산 정합성)

```sql
-- 정기예금 잔액 = 일별잔액 최신값
SELECT 'DPF_BAL_CONSISTENCY' AS check_name, COUNT(*) AS violations
FROM DPF001M d
WHERE d.BAL != COALESCE((
    SELECT BAL FROM DPF003P p
    WHERE p.ACN = d.ACN
    ORDER BY BASE_YMD DESC LIMIT 1
), 0);
```

### 3.6 품질결함 (V2 의도적 위반)

```sql
-- CSC006M 중복 행 (허용 0.2%)
SELECT 'DUPLICATE_ROWS_CSC006M' AS check_name,
       COUNT(*) - COUNT(DISTINCT (CSN, CONTACT_TCD, HP_NO, TEL_NO, EMAIL)) AS dup_count,
       COUNT(*) AS total_rows
FROM CSC006M;
```

---

## 4. 통과 기준

### 4.1 hard severity (72개 규칙)
- **모든 쿼리에서 `violations = 0` 필수**
- 1건이라도 위반 시 시딩 실패로 간주

### 4.2 soft severity (7개 규칙)
- **5% 이하 위반 허용**
- 초과 시 경고, 반드시 실패는 아님

### 4.3 V2 품질결함 허용치

| 결함 유형 | 허용치 | 적용 |
|---|---|---|
| missing_fk | 0.5% | FK orphan |
| date_inconsistency | 0.2% | 날짜 순서 |
| amount_negative | 0.1% | 음수 금액 |
| code_unknown | 0.3% | enum 외 코드 |
| duplicate_row | 0.2% | 지정 테이블만 |

이 허용치 **이하라면** V2의 의도적 품질 결함 주입이 정상적으로 된 것.
**이상이면** 시딩 로직 오류 (결함이 너무 많이 주입됨).
**0%이면** 결함 주입이 전혀 안 됨 (v2 특성 미구현).

---

## 5. 검증 워크플로 (권장)

```
┌───────────────────────────────────────────────────┐
│ 1. 시딩 완료                                      │
│    (98_DATA_GENERATION_V2.md 참조)                │
└───────────────────────────────────────────────────┘
                    ↓
┌───────────────────────────────────────────────────┐
│ 2. verification_queries.sql 실행                  │
│    → verification_report.txt 생성                │
└───────────────────────────────────────────────────┘
                    ↓
┌───────────────────────────────────────────────────┐
│ 3. 결과 분류                                      │
│    - hard 위반 > 0 → 시딩 롤백 필요              │
│    - soft 위반 > 5% → 경고, 검토                 │
│    - 품질결함 초과 → 결함 주입 로직 조정         │
│    - 품질결함 부족 → 결함 주입 강화              │
└───────────────────────────────────────────────────┘
                    ↓
┌───────────────────────────────────────────────────┐
│ 4. 최종 통과 → 시딩 완료 선언                    │
│    → 에이전트 테스트 환경으로 승격                │
└───────────────────────────────────────────────────┘
```

---

## 6. 재생성

규칙 변경 시 SQL 재생성:

```bash
# 1. business_rules.yaml 수정
vim meta/business_rules.yaml

# 2. JSON 재생성 + 검증
python3 scripts/build_business_rules.py

# 3. SQL 재생성
python3 scripts/build_verification_sql.py

# 4. 재실행
psql -h localhost -U bank -d bank_v2 -f meta/verification_queries.sql
```

---

## 7. 참고 사항

- **성능**: 1,425만 행 대상 전체 검증은 약 3~5분 (prototype 프로파일)
- **병렬 실행**: 섹션별로 분할해 병렬 실행 가능 (스크립트 분할 필요)
- **증분 검증**: 특정 테이블만 재시딩 시 관련 섹션만 선택 실행 가능
- **SQL 확장**: 복잡한 집계 규칙은 수동으로 SQL 작성 필요 (현재 예시만 제공)

---

**이전 문서**: [98_DATA_GENERATION_V2.md](98_DATA_GENERATION_V2.md) — 시딩 운영 가이드
