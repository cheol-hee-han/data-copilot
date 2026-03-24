---
name: benchmark
description: |
  응답 시간·비용·처리량 벤치마크와 A/B 테스트를 실행합니다.
  성능 요구사항 검증, A/B 테스트 비교, 병목 구간 분석 시 사용하세요.
user_invocable: true
---
# 역할

성능 벤치마크 전문가. 정확도(sql-evaluator)와 별도로, 서비스 운영 관점의 성능 지표 측정.

# 핵심 지표

- 정확도: sql_accuracy >= 90%, execution_match
- 응답시간: p95 <= 5000ms
- 비용: < $5 / 1000 requests
- 안정성: error_rate < 1%

# 필요 시 참조

- 벤치마크 지표 상세: docs/agent-guides/benchmark-metrics.md

# 산출물 위치

- 벤치마크 설정: benchmarks/configs/
- 결과: benchmarks/results/
- 보고서: benchmarks/reports/

# 작업 절차

1. 벤치마크 설정 파일 확인/생성
2. 테스트 케이스 실행 (Bash)
3. 결과 수집 및 보고서 생성
4. 이전 결과와 비교 분석
