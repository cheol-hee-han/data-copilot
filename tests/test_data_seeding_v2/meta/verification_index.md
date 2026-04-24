# 99. 시딩 검증 쿼리 인덱스

생성: 2026-04-22T13:11:16

## 개요

본 쿼리 세트는 `business_rules.json` 79개 규칙 + FK 무결성 + 분포 통계를 검증한다.
자동 생성된 SQL: `meta/verification_queries.sql`

## 실행 방법

```bash
psql -h localhost -U bank -d bank_v2 -f meta/verification_queries.sql > verification_report.txt
```

## 섹션 구성

| # | 섹션 | 내용 |
|---|---|---|
| 1 | 1. 기본 검증 | 행 수, 빈 테이블, 볼륨 편차 |
| 2 | 2. FK 참조 무결성 | 1426 엣지 중 핵심 20개 마스터 대상 orphan 검증 |
| 3 | 3. 날짜 순서 제약 | 25 규칙 |
| 4 | 4. 금액 범위·관계 | 19 규칙 |
| 5 | 5. 상태 전이 | 10 규칙 |
| 6 | 6. 크로스 테이블 정합성 | 9 규칙 |
| 7 | 7. 코드 값 연계 | 8 규칙 |
| 8 | 8. 카디널리티 절대 제약 | 4 규칙 |
| 9 | 9. 파생값 관계 | 4 규칙 |
| 10 | 10. 품질결함 비율 검증 | V2 의도적 결함 주입 비율 확인 |
| 11 | 11. 분포 통계 검증 | 성별/대출유형/통화 등 분포 샘플링 |
| 12 | 12. 요약 리포트 | 전체 규칙 통합 대시보드 |

## 통과 기준

- **severity=hard (72개)**: 위반 수 0이어야 함
- **severity=soft (7개)**: 위반 비율 5% 이하 권장
- **FK orphan**: 0.5% 이하 (V2 quality_defect 허용)
- **중복 행**: 지정 테이블만 0.2% 이내
- **음수 금액**: 0.1% 이내

## 품질 결함 허용치

`business_rules.json.quality_defect_tolerances` 참조:
- **missing_fk**: 0.005 — FK 참조 결손 최대 0.5%
- **date_inconsistency**: 0.002 — 날짜 순서 위반 최대 0.2% (의도적)
- **amount_negative**: 0.001 — 음수 금액 0.1%
- **code_unknown**: 0.003 — enum 외 코드값 0.3%
- **duplicate_row**: 0.002 — 

## 재생성

규칙 변경 시 재생성:
```bash
python3 scripts/build_verification_sql.py
```