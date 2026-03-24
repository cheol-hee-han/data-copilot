---
name: sqlglot_parsing_accuracy
description: SQLGlot 파싱 정확도 리서치 결과 — find_all 함정, 에러 레벨, 방언별 지원 현황 (2026-03-24)
type: project
---

SQLGlot 파싱 신뢰성 리서치 완료. 핵심 발견 3가지:

1. `find_all(exp.Table)`은 CTE 별칭을 실제 테이블로 오인한다 — `traverse_scope()` 필수
2. 기본 `error_level=WARN`은 파싱 실패를 silent하게 삼킨다 — `error_level=ErrorLevel.RAISE` 명시 필요
3. Impala는 공식 미지원 → `dialect="hive"` 매핑 (SELECT 표준 쿼리 95%+ 신뢰, `[hint]` 대괄호 힌트는 ParseError), Sybase IQ는 완전 미지원 → `dialect="tsql"` 우선(날짜함수/TOP N 처리), 실패 시 `dialect=None` 재시도, regex fallback

**Why:** Data Copilot의 SQL 파싱 레이어 신뢰성 확보 및 폐쇄망 배포(Impala/Sybase IQ) 대응 전략 수립 목적

**How to apply:** SQL 파싱 구현 코드 작성 시 위 3가지 패턴을 강제 적용. `find_all(exp.Table)` 코드 발견 시 즉시 지적.

보고서 위치: `docs/research/20260324-sqlglot-parsing-accuracy.md`
