---
name: reranker_cpu_optimization_context
description: BGE-Reranker-v2-m3 CPU 최적화 리서치 수행 배경 및 핵심 결론 — 폐쇄망 배포, FlagEmbedding ONNX 미지원 확인
type: project
---

2026-03-21, bge-reranker-v2-m3 CPU 최적화 리서치 수행.

핵심 결론:
- FlagEmbedding FlagReranker는 ONNX 내장 지원 없음 → ORTModelForSequenceClassification으로 직접 래핑 필요
- ONNX O3 + INT8 동적 양자화 조합이 최고 투자 대비 효과 (3~4x)
- O4 최적화는 CPU에서 사용 불가 (GPU 전용 fp16)
- ProcessPoolExecutor는 50쌍 규모에서 역효과 (모델 가중치 ~2.3GB 복제)
- 사전 필터링(50→25쌍)이 단순하고 효과 큼

리서치 보고서 위치: docs/research/20260321-bge-reranker-cpu-optimization.md

**Why:** 폐쇄망(Sybase IQ/Impala, 로컬 LLM) 배포 타겟으로 GPU 없이 리랭킹 레이턴시 최소화 필요
**How to apply:** ONNX 변환 구현 시 이 보고서의 설정값(SessionOptions, 양자화 config) 직접 참조
