---
name: project_qwen35_model_analysis
description: Qwen3.5-397B-A17B 모델 분석 리서치 (2026-03-26): 모델 실존 확인, MoE 아키텍처, 한국어 취약점, 폐쇄망 배포 제약, 보고서 위치 포함
type: project
---

Qwen3.5-397B-A17B 모델 리서치 완료. 모델은 실존하며 2026-02-16 출시.

**Why:** 폐쇄망 LLM 후보 평가를 위해 사용자가 요청.

**How to apply:** 향후 폐쇄망 LLM 선정 논의 시 아래 결론을 기준점으로 사용.

## 핵심 결론

- 397B-A17B는 MoE (총 397B, 추론 시 17B 활성화), GPU 8장(A100 80GB) 필요 — 폐쇄망 단일 서버 배포 비현실적
- 컨텍스트 262K (최대 1M), Apache 2.0 라이선스
- 한국어 내부 추론 영어 편향 확인됨 (arXiv:2508.10355) — 금액 단위 오인식 사례 존재
- 환각률 높음: Artificial Analysis AA-Omniscience Index -32 (경쟁사 Kimi K2.5 -11, GLM-5 -1 대비 불리)
- JSON 구조화 출력: 지원하나 중첩 구조에서 재시도 필요
- 폐쇄망 실용 후보: Qwen3.5-27B (Dense, GPU 1장) 또는 Qwen3.5-35B-A3B (MoE, GPU 1~2장)

## 보고서

`docs/research/20260326-qwen35-397b-model-analysis.md`
