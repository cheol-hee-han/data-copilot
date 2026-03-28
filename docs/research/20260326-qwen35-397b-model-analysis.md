# Qwen3.5-397B-A17B 모델 분석 리서치

**작성일**: 2026-03-26
**작성자**: Research Analyst Agent
**리서치 범위**: Qwen3.5 모델 패밀리 전체, 특히 397B 플래그십 모델의 폐쇄망 NL-to-SQL 적용 가능성

---

## Executive Summary

Qwen3.5-397B-A17B는 **실존하는 모델**이다. Alibaba Qwen 팀이 2026년 2월 16일 출시한 MoE(Mixture-of-Experts) 아키텍처 기반 플래그십 오픈웨이트 모델로, 397B 전체 파라미터 중 추론 시 17B만 활성화한다. 코딩·에이전틱 태스크에서 상업 모델에 근접한 성능을 보이나, 한국어 추론 편향(영어 내부 처리) 및 높은 환각률(88%)이 폐쇄망 금융 NL-to-SQL 환경에서의 적용에 주요 리스크로 식별된다.

**권고**: 폐쇄망 소형 모델 후보로 Qwen3.5 시리즈(특히 27B~35B dense 모델)는 검토 가치가 있으나, 397B MoE 플래그십은 GPU 8장 이상 요구로 폐쇄망 단일 서버 배포에 실질적 제약이 크다. 한국어 금융 특화 파인튜닝 없이 프로덕션 투입은 권장하지 않는다.

---

## 1. Qwen 모델 패밀리 개요

### 1.1 계보

| 세대 | 출시 시기 | 주요 특징 |
|------|-----------|-----------|
| Qwen2.5 | 2024년 하반기 | 29개 언어, dense + MoE |
| Qwen3 | 2025년 4~5월 | 119개 언어, thinking/non-thinking 통합 |
| Qwen3.5 | 2026년 2월 16일 | 201개 언어, 멀티모달(비전+비디오), 하이브리드 아키텍처 |

### 1.2 Qwen3.5 전체 모델 라인업

**Dense 모델** (Hugging Face 공개 확인):
- Qwen3.5-0.8B
- Qwen3.5-2B
- Qwen3.5-4B
- Qwen3.5-9B
- Qwen3.5-27B

**MoE 모델**:
- Qwen3.5-35B-A3B (35B 전체, 3B 활성화)
- Qwen3.5-397B-A17B (397B 전체, 17B 활성화) ← 플래그십

**Qwen3 (전 세대) 라인업** (비교 참조):
- Dense: 0.6B / 1.7B / 4B / 8B / 14B / 32B
- MoE: 30B-A3B / 235B-A22B

---

## 2. Qwen3.5-397B-A17B 아키텍처 상세

### 2.1 핵심 구조

| 항목 | 값 |
|------|----|
| 아키텍처 타입 | Hybrid MoE (Gated DeltaNet + Sparse MoE) |
| 전체 파라미터 | 397B |
| 활성화 파라미터 (추론 시) | 17B |
| 레이어 수 | 60 |
| Hidden Dimension | 4,096 |
| 토큰 어휘 크기 | 248,320 (패딩 포함) |

**레이어 배치**: `15 × (3 × (Gated DeltaNet → MoE) → 1 × (Gated Attention → MoE))`

이는 기존 Transformer-only 구조와 다른 **하이브리드 선형 어텐션** 설계다. Gated DeltaNet은 선형 복잡도(O(n)) 레이어로 긴 문맥 처리 효율을 높이고, 전통적 Attention은 4개 레이어마다 한 번씩 등장한다.

### 2.2 MoE 라우팅

| 항목 | 값 |
|------|----|
| 총 Expert 수 | 512 |
| 토큰당 활성화 Expert | 10 라우팅 + 1 공유 = 11개 |
| Expert Intermediate Dim | 1,024 |

### 2.3 어텐션 설정

- **Gated DeltaNet**: V 헤드 64개, QK 헤드 16개, 헤드 차원 128
- **Gated Attention**: Q 헤드 32개, KV 헤드 2개, 헤드 차원 256 (GQA)
- **위치 임베딩**: RoPE (회전 차원 64)

### 2.4 컨텍스트 윈도우

| 모드 | 길이 |
|------|------|
| 네이티브 | 262,144 토큰 (~262K) |
| YaRN RoPE 확장 | 최대 1,010,000 토큰 (~1M) |

> **중요**: 공식 권고는 "thinking 모드를 유지하려면 컨텍스트 최소 128K 이상"이다. 작은 컨텍스트 창으로 서빙하면 thinking 능력이 저하된다.

---

## 3. 주요 벤치마크 성능

### 3.1 공식 발표 벤치마크 (Qwen3.5-397B-A17B)

| 벤치마크 | 점수 | 카테고리 |
|----------|------|----------|
| AIME26 | 91.3 | 수학 추론 |
| GPQA Diamond | 88.4 | 대학원 수준 추론 |
| MMLU | 88.5 | 일반 지식 |
| MMLU-Pro | 87.8 | 전문 지식 |
| MathVista | 90.3 | 시각적 수학 |
| LiveCodeBench v6 | 83.6% | 코딩 |
| SWE-bench Verified | 76.4% | 소프트웨어 엔지니어링 |
| IFBench | 76.5 | 명령 따르기 |
| BFCL v4 | 72.9 | 함수 호출 |
| BrowseComp | 78.6 | 웹 에이전트 |
| Terminal-Bench 2 | 52.5 | 터미널 에이전트 |
| OmniDocBench v1.5 | 90.8 | 문서 이해 |
| MMMU | 85.0 | 멀티모달 이해 |
| Video-MME | 87.5 | 비디오 이해 |

출처: [Digital Applied - Qwen 3.5 Benchmarks Guide](https://www.digitalapplied.com/blog/qwen-3-5-agentic-ai-benchmarks-guide)

### 3.2 경쟁 모델 비교 (Artificial Analysis Intelligence Index)

| 모델 | Intelligence Index | 비고 |
|------|-------------------|------|
| GLM-5 | 50 | 1위 (오픈웨이트) |
| Kimi K2.5 | 47 | 2위 |
| Qwen3.5-397B-A17B | 45 | 3위 (오픈웨이트) |

- Qwen3.5는 이전 세대 Qwen3-235B 대비 GDPval-AA ELO **+361점** 향상
- 에이전틱 코딩(TerminalBench Hard) **+27%p**
- 과학적 추론(HLE) **+12%p**
- 명령 따르기(IFBench) **+28%p**

출처: [Artificial Analysis - Qwen3.5-397B-A17B Everything You Need to Know](https://artificialanalysis.ai/articles/qwen3-5-397b-a17b-everything-you-need-to-know)

### 3.3 기존 발표의 비교 주장에 대한 검증 주의사항

Qwen 공식 블로그 및 일부 미디어는 "GPT-5.2, Claude Opus 4.5 대비 80% 카테고리에서 우위"를 주장하나, Artificial Analysis의 독립 검증 기사는 "independent verification is still underway"라고 명시했다. 공식 발표 수치를 그대로 수용하지 말 것.

---

## 4. 기능 지원 현황

### 4.1 구조화 출력 (Structured Output / JSON Mode)

- **지원 여부**: Yes, 다만 방식에 차이 있음
- vLLM 서빙 시 `--enable-auto-tool-choice`, `--tool-call-parser qwen3_coder` 플래그로 도구 호출 파서 활성화 필요
- Alibaba DashScope API는 JSON mode 공식 지원
- Together AI, OpenRouter 등 외부 API 공급자도 JSON mode 지원

**실무 주의사항**: 커뮤니티 보고에 따르면 중첩 JSON 구조나 복잡한 스키마에서 모델이 추가 텍스트(` ```json `)를 삽입하거나 파싱 오류를 발생시키는 사례가 있다. 파싱 재시도 로직 필수.

출처: [Alibaba Cloud - Qwen Structured Output](https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output)

### 4.2 함수 호출 / Tool Use

- **지원 여부**: Yes
- OpenAI 호환 API 형식으로 tool_choice 지원
- MCP(Model Context Protocol) 서버 연동 가능 (Qwen-Agent 프레임워크)
- BFCL v4 벤치마크 72.9점으로 중간 수준 (Claude Sonnet 대비 낮음 추정)

### 4.3 Thinking Mode

- **기본값**: 활성화 (기본 On)
- `<think>...</think>` 태그 내부에서 체인-오브-쏘트 수행 후 최종 응답 반환
- `enable_thinking=False` 또는 시스템 프롬프트로 비활성화 가능
- **폐쇄망 적용 시 고려**: thinking 토큰이 출력 길이를 크게 늘려 지연 시간 증가. 간단한 SQL 생성 태스크에서는 비활성화 검토 필요

---

## 5. SQL 생성 능력 평가

### 5.1 Qwen 계열의 Text-to-SQL 벤치마크

직접적인 Qwen3.5-397B의 SQL 벤치마크 논문은 2026-03-26 기준 미발표 상태. Qwen2.5 계열 데이터로 추정:

| 모델 | 데이터셋 | Execution Accuracy |
|------|----------|-------------------|
| Qwen2.5-Coder-7B (SPS-SQL) | Spider dev | 81.7% |
| Qwen2.5-Coder-7B (SPS-SQL) | Spider test | 82.1% |
| Qwen2.5-Coder-32B | Text-to-SQL benchmark | 95.73% |
| Arctic-ExCoT-32B (Qwen2.5 기반 파인튜닝) | BIRD-dev | SOTA (전 세대 대비 +11%p) |

출처: [SPS-SQL 논문 - ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0167865525001497), [Snowflake Arctic-ExCoT](https://www.snowflake.com/en/engineering-blog/arctic-text2sql-excot-sql-generation-accuracy/)

### 5.2 SQL 생성 강점

- 코딩 능력(LiveCodeBench 83.6%)은 높은 수준으로, SQL 문법 정확도는 양호할 것으로 추정
- Qwen2.5-Coder 계열의 BIRD 데이터셋 SOTA 성취는 검증된 사실
- 스키마 링킹(테이블-컬럼 선택) 전략에 결과가 민감: 프롬프트 엔지니어링이 정확도에 결정적

### 5.3 SQL 생성 약점 (폐쇄망 금융 환경 관점)

| 약점 | 근거 | 리스크 수준 |
|------|------|-------------|
| 복잡한 금융 계수산출식 추론 | 공개 벤치마크 미확인 | 높음 |
| Sybase IQ / Impala 방언 SQL | 학습 데이터 희소 추정 | 높음 |
| 스키마가 불완전한 환경 | 메타 설명 의존도 높음 | 중간 |
| thinking 모드 비활성화 시 정확도 저하 | 공식 문서 경고 | 중간 |

---

## 6. 한국어 이해 능력

### 6.1 공식 지원 현황

- Qwen3.5: 201개 언어 지원 (Qwen3의 119개에서 확대)
- 어휘 크기 248,320 토큰에 한국어 서브워드 포함

### 6.2 알려진 구조적 약점 (논문 근거)

**출처**: [Making Qwen3 Think in Korean with Reinforcement Learning (arXiv:2508.10355, 2025-08-15)](https://arxiv.org/abs/2508.10355)

이 논문(Dnotitia Inc.)은 Qwen3 14B 베이스 모델의 핵심 결함을 실험으로 증명했다:

> "한국어 문제가 주어져도 내부 추론은 영어로 수행(English-biased reasoning)하고, 결과만 한국어로 번역하는 방식을 취한다. 이 과정에서 한국어 특유의 수치 표현('15만원')을 잘못 해석하는 오류가 발생했다. ('150,000원'을 '1,500,000원'으로 오인)"

| 평가 항목 | 결과 |
|-----------|------|
| KMMLU (베이스 모델) | 58.5~58.6% |
| KMMLU (SFT 후) | 60.04% (+1.5%p) |
| KMMLU (SFT + RL 후) | 유지 |
| 내부 추론 언어 | 영어 편향 (한국어 입력에도) |

**이 결함은 Qwen3.5-397B에서도 구조적으로 유사하게 존재할 가능성이 높다.** 대규모 학습 데이터 불균형(영어 >> 한국어) 때문이며, 크기를 키운다고 자동 해소되지 않는다.

### 6.3 폐쇄망 금융 도메인 한국어 리스크

- 금융 용어(BIS비율, 연체율, 기준금리 등)의 한국어 문맥 이해: **검증 필요**
- 금액 단위 표현 혼동(만원, 억원, 조원): **확인된 취약점 클래스**
- 날짜/기간 표현("이번 달", "전분기말"): 한국어 특화 파인튜닝 없으면 오해석 가능

---

## 7. 인프라 요구사항 및 폐쇄망 적용 가능성

### 7.1 하드웨어 요구

| 구성 | 최소 사양 |
|------|-----------|
| GPU 수 | 8장 (Tensor Parallel) |
| GPU 메모리 | A100 80GB × 8 또는 동급 |
| 컨텍스트 262K | 위 구성 필요 |

> Qwen3.5-35B-A3B (35B MoE, 3B 활성화)는 단일 A100 80GB 1~2장으로 운용 가능하여 폐쇄망 실용성이 더 높음.

### 7.2 서빙 프레임워크

| 프레임워크 | 특징 | 권장 용도 |
|------------|------|-----------|
| vLLM | OpenAI 호환 API, tool call 지원 | 일반 목적 |
| SGLang | 고성능, Mamba Radix Cache | 고처리량 |
| KTransformers | 하이브리드 CPU/GPU 오프로딩 | 메모리 제약 환경 |
| Unsloth | 로컬 양자화 실행 | 단일 GPU 추론 |
| Ollama | 로컬 간편 실행 (양자화) | 개발/테스트 |

### 7.3 추론 효율화

- **MTP(Multi-Token Prediction) 투기적 디코딩**: 저지연 interactive 워크로드에서 TPOT 감소 (단, 처리량 감소)
- **thinking 모드 비활성화**: SQL 생성 등 단순 태스크에서 지연 시간 대폭 감소
- **양자화**: GGUF Q4_K_M 등으로 메모리 요구 대폭 감소, 정확도 trade-off 있음

### 7.4 라이선스

Apache 2.0 — 상업적 사용, 수정, 재배포 모두 허용. 폐쇄망 오프라인 배포에 법적 제약 없음.

---

## 8. 경쟁 모델과의 비교 (폐쇄망 NL-to-SQL 관점)

| 항목 | Qwen3.5-397B-A17B | Claude Sonnet (현재 기준) | GPT-4o |
|------|-------------------|--------------------------|--------|
| 오픈웨이트 | Yes (Apache 2.0) | No (API Only) | No (API Only) |
| 폐쇄망 배포 | 가능 (GPU 8장) | 불가 | 불가 |
| 한국어 추론 편향 | 영어 내부처리 확인 | 없음 | 없음 |
| 환각률 (AA Index) | -32 (88% 수준) | 낮음 | 낮음 |
| JSON 구조화 출력 | 지원 (재시도 필요) | 안정적 | 안정적 |
| SQL 생성 (코딩 벤치) | 83.6% (LiveCodeBench) | 92% (HumanEval 추정) | 높음 |
| Tool Use / Function Call | 72.9 (BFCL v4) | 높음 | 높음 |
| 컨텍스트 윈도우 | 262K (최대 1M) | 200K | 128K |
| GPU 요구 | A100 × 8 | N/A | N/A |

**폐쇄망 소형 모델 대안 후보** (실용성 기준):
1. **Qwen3.5-35B-A3B**: MoE, 3B 활성화, A100 1~2장
2. **Qwen3.5-27B**: Dense, A100 1장 (fp16 기준 ~54GB)
3. **Qwen3-32B**: Dense, 이전 세대, 검증 자료 풍부

---

## 9. 기각된 대안 및 이유

| 대안 | 기각 이유 |
|------|-----------|
| Qwen3.5-397B-A17B를 폐쇄망 직접 배포 | GPU 8장 요구, 단일 서버에 비현실적 |
| thinking 모드 항상 활성화 | 간단한 SQL 태스크에서 지연 과대, 비용 낭비 |
| 파인튜닝 없이 한국어 금융 도메인 적용 | 영어 내부추론 편향 + 금액 단위 오인식 확인 |
| Qwen2.5-7B로 전체 파이프라인 담당 | NL2SQL 정확도 54.5% (파인튜닝 전 36%) — 금융 도메인 기준 부족 |

---

## 10. 권고사항

### 폐쇄망 LLM 선정

1. **단기 (현재 개발 단계)**: Claude Sonnet API 유지 — 한국어 정확도, JSON 안정성, 환각률 모두 우위
2. **폐쇄망 전환 시 1순위 후보**: Qwen3.5-27B (Dense, GPU 1장) 또는 Qwen3.5-35B-A3B (MoE, GPU 1~2장)
3. **대규모 서버 확보 가능 시**: Qwen3.5-397B-A17B (thinking 비활성화 기본값, 재시도 파서 필수)

### 필수 보완 조치 (Qwen3.5 사용 시)

- 한국어 금융 특화 파인튜닝 (KMMLU + 금융 도메인 데이터) 선행 필요
- JSON 파싱 재시도 로직 구현 (최소 2회 retry, error 시 더 엄격한 포맷 프롬프트 재시도)
- SQL 생성 시 thinking 모드 비활성화 + 검증 레이어(SQLGlot) 필수
- 금액 단위 표현("만원", "억원") 명시적 파싱 규칙 추가

---

## 참고문헌

### Tier 1 (논문)

1. Qwen Team, Alibaba Cloud (2025-05-15). **Qwen3 Technical Report**. arXiv:2505.09388. https://arxiv.org/abs/2505.09388

2. Dnotitia Inc. (2025-08-15). **Making Qwen3 Think in Korean with Reinforcement Learning**. arXiv:2508.10355. https://arxiv.org/abs/2508.10355

3. Park et al. (2021). **KLUE: Korean Language Understanding Evaluation**. arXiv:2105.09680. https://arxiv.org/abs/2105.09680

4. SPS-SQL 연구팀 (2025). **SPS-SQL: Enhancing Text-to-SQL generation on small-scale LLMs with pre-synthesized queries**. Pattern Recognition Letters (ScienceDirect). https://www.sciencedirect.com/science/article/abs/pii/S0167865525001497

5. Snowflake AI Research (2025). **Arctic Text2SQL: ExCoT for Execution-Guided Chain-of-Thought Optimization**. https://www.snowflake.com/en/engineering-blog/arctic-text2sql-excot-sql-generation-accuracy/

### Tier 2 (공식 문서 / 기술 블로그)

6. Qwen Team (2026-02-16). **Qwen3.5: Towards Native Multimodal Agents** (공식 블로그). https://qwen.ai/blog?id=qwen3.5

7. Hugging Face Model Card. **Qwen/Qwen3.5-397B-A17B**. https://huggingface.co/Qwen/Qwen3.5-397B-A17B

8. GitHub. **QwenLM/Qwen3.5**. https://github.com/QwenLM/Qwen3.5

9. NVIDIA NIM. **qwen3.5-397b-a17b Model Card**. https://build.nvidia.com/qwen/qwen3.5-397b-a17b/modelcard

10. Alibaba Cloud. **Qwen Structured Output Guide**. https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output

### Tier 3 (분석 미디어)

11. Artificial Analysis (2026). **Qwen3.5-397B-A17B: Everything You Need to Know**. https://artificialanalysis.ai/articles/qwen3-5-397b-a17b-everything-you-need-to-know

12. Digital Applied (2026). **Qwen 3.5: 397B MoE Benchmarks, Pricing & Complete Guide**. https://www.digitalapplied.com/blog/qwen-3-5-agentic-ai-benchmarks-guide
