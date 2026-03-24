# 벤치마크 지표 및 가이드

## 벤치마크 지표 체계

### 정확도
- sql_accuracy: 골든셋 기반 SQL 정확도 (%) — 목표: >= 90%
- execution_match: 실행 결과 일치율 (%)

### 응답 시간
- p50_latency: 중간값 (ms)
- p95_latency: 95 백분위 (ms) — 목표: <= 5000ms
- p99_latency: 99 백분위 (ms)

### 비용
- avg_tokens_per_request: 요청당 평균 토큰 수
- cost_per_1000_requests: 1000 요청당 API 비용 ($) — 목표: < $5

### 안정성
- error_rate: 오류 응답 비율 (%) — 목표: < 1%
- timeout_rate: 타임아웃 비율 (%)

## A/B 테스트 실행 방법

두 가지 프롬프트/설정을 동일한 테스트셋으로 비교:
1. variant_a_config, variant_b_config 준비
2. 동일 테스트 케이스로 각각 실행
3. 정확도, 응답시간, 비용, 오류율 비교
4. 통계적 유의성 판단 후 채택 결정

## 벤치마크 보고서 형식

```markdown
# 벤치마크 보고서 - YYYY-MM-DD

## A/B 테스트: <테스트 ID>

| 지표 | Variant A (baseline) | Variant B (new) | 변화 |
|------|---------------------|-----------------|------|
| SQL 정확도 | 85.2% | 89.7% | +4.5% ✅ |
| p95 응답시간 | 3,200ms | 3,450ms | +250ms ⚠️ |
| 1000건 비용 | $3.20 | $3.85 | +$0.65 ⚠️ |

## 결론
(채택 권장 여부 + 근거)
```

## 산출물 위치

```
benchmarks/
├── configs/
├── results/YYYYMMDD.json
└── reports/weekly-benchmark-YYYYMMDD.md
```
