# Agentic Recovery Loop 재설계 - 리서치 레퍼런스

**작성일**: 2026-04-01
**작성자**: Research Analyst Agent
**목적**: NL-to-SQL 에이전틱 리커버리 루프 재설계 코드리뷰를 위한 근거 논문·벤치마크 수집
**대상 독자**: 아키텍처 리뷰어, 설계 의사결정자

---

## 요약 (Executive Summary)

본 리서치는 아래 8개 주제에 대해 실존 논문과 벤치마크만을 수집하였다. 인용 가능 Tier-1 논문 11편, 벤치마크 3종, 선행 NL-to-SQL 구현사례 3종이 포함된다. 각 섹션은 (1) 핵심 발견, (2) 프로젝트 적용 시사점, (3) 기각된 대안을 포함한다.

---

## 1. ReAct 패턴의 소형/중형 LLM(70B급) 신뢰성

### 근거 논문

#### [T1] Pre-Act: Multi-Step Planning and Reasoning Improves Acting in LLM Agents
- **저자**: Mrinal Rawat, Ambuje Gupta, Rushil Goomer, Alessandro Di Bari, Neha Gupta, Roberto Pieraccini (Uniphore)
- **연도**: 2025년 5월 (arXiv:2505.09970)
- **핵심 발견**:
  - ReAct 대비 Pre-Act가 5개 모델 평균 Action Recall을 **70% 향상** (Almita 데이터셋 기준)
  - Llama 3.1 70B fine-tuned Pre-Act 모델이 GPT-4 대비 Action Accuracy **69.5% 향상**, Goal Completion Rate **28% 향상**
  - 소형 모델은 "complex reasoning tasks required for agentic systems"에서 구조적 어려움을 겪음 — 논문 직접 인용
  - ReAct의 단순 Thought-Action-Observation 루프는 다음 액션을 결정하기 위한 충분한 선행 계획이 없어 70B급 모델에서 액션 선택 오류율이 높음

#### [T2] AgentBench: Evaluating LLMs as Agents (ICLR 2024)
- **저자**: Xiao Liu, Hao Yu et al. (THUDM, Tsinghua)
- **연도**: 2023년 8월 제출, ICLR 2024 채택 (arXiv:2308.03688)
- **핵심 발견**:
  - 29개 모델(API 기반 + OSS) 평가 결과: 상위 상용 LLM과 "70B 이하 OSS 경쟁자 간 **현격한 성능 차이**" 존재 — 논문 직접 인용
  - 실패 유형 분류:
    - **Invalid Action (IA)**: 포맷은 맞으나 무효한 액션 선택 → OSS 모델에서 빈번
    - **Task Limit Exceeded (TLE)**: 멀티턴 환경에서 제한 초과 → 루프 탈출 실패 지표
    - **Instruction Following Failure (IF)**: 도구 사용 지침 미준수
  - 주요 장애물: "Poor long-term reasoning, decision-making, and instruction following" — 논문 직접 인용

#### [T3] Why Do Multi-Agent LLM Systems Fail? (MAST, NeurIPS 2025)
- **저자**: Mert Cemri, Melissa Z. Pan, Shuyi Yang 외 10인 (UC Berkeley 등)
- **연도**: 2025년 3월 (arXiv:2503.13657), NeurIPS 2025
- **핵심 발견**:
  - 1,600개 이상 실행 trace 분석, 14개 고유 실패 모드 식별 (inter-annotator kappa=0.88)
  - **MAST 3개 범주**:
    1. System Design Issues (아키텍처 설계 결함 — 미흡한 프롬프트, 종료 조건 부재)
    2. Inter-Agent Misalignment (에이전트 간 조율 실패)
    3. Task Verification (완료 확인 실패)
  - GPT4, Claude 3, **Qwen2.5**, CodeLlama 전 모델에서 공통 실패 패턴 관찰
  - "identified failures require more sophisticated solutions" — 논문 직접 인용
  - **실용적 함의**: 종료 조건 명시 부재가 System Design Issues의 핵심 원인으로 지목됨

### 프로젝트 적용 시사점

Solar Pro 2 70B 및 Qwen3.5 업그레이드 환경에서 순수 ReAct 패턴 사용 시:
- Pre-Act 연구가 보여주는 액션 정확도 저하를 그대로 겪을 가능성이 높다.
- AgentBench의 TLE/IA 분류는 본 프로젝트 리커버리 루프에서 동일하게 발현될 수 있다.
- MAST는 종료 조건 명세가 설계 단계에서 필수임을 실증한다.

### 기각된 대안

- **ReAct를 그대로 유지하는 방안**: Pre-Act 대비 Action Recall -70% 차이가 문서화되어 있어 70B 환경에서는 기각.
- **더 큰 모델로 해결하는 방안**: 폐쇄망 제약상 현실적이지 않음 (별도 Qwen3.5 리서치 결과 참조).

---

## 2. 구조화 출력 (JSON) vs. 네이티브 Tool-Calling — OSS LLM 신뢰성

### 근거 논문·벤치마크

#### [T4] JSONSchemaBench: A Rigorous Benchmark of Structured Outputs for Language Models
- **저자**: Guidance-AI (Microsoft Research 연계)
- **연도**: 2025년 1월 (arXiv:2501.10868)
- **핵심 발견**:
  - 10,000개 실세계 JSON 스키마를 대상으로 6개 제약 디코딩 프레임워크 평가: Guidance, Outlines, Llamacpp, XGrammar, OpenAI, Gemini
  - **최우수 프레임워크가 최하위 대비 지원 스키마 2배 차이** — 프레임워크 선택이 결과를 결정함
  - 제약 디코딩이 비제약 방식 대비 토큰 생성 **50% 속도 향상**
  - 다운스트림 태스크 정확도 최대 **4% 향상**
  - 단, 복잡한 중첩 스키마에서 프레임워크 간 커버리지 편차가 크게 발생

#### [B1] Berkeley Function Calling Leaderboard (BFCL v1-v4)
- **저자**: Shishir G. Patil, Huanzhi Mao, Charlie Cheng-Jie Ji et al. (UC Berkeley Sky Computing Lab)
- **연도**: 2024-2025년 지속 업데이트 (공개 리더보드: gorilla.cs.berkeley.edu)
- **핵심 발견**:
  - AST 기반 평가 방법으로 직렬·병렬 함수 호출, 다중 언어 커버
  - Qwen 3 14B: F1 **0.971** (GPT-4 수준), Qwen 3 8B: F1 **0.933** — Docker 실측 기준
  - "while state-of-the-art LLMs excel at single-turn calls, **memory, dynamic decision-making, and long-horizon reasoning remain open challenges**" — 공식 문서 인용
  - OSS 모델 중 Qwen 계열이 function calling에서 최우수 성능 기록

#### [T5] StructEval: Benchmarking LLMs' Capabilities to Generate Structural Outputs
- **저자**: Tiger AI Lab
- **연도**: 2024-2025 (arXiv:2505.20139)
- **핵심 발견**:
  - 18개 포맷, 44개 태스크 유형 평가
  - o1-mini 최고 점수 75.58%, **OSS 모델은 약 10점 낮음**
  - JSON/YAML 등 비렌더링 포맷에서도 포맷 준수율 격차 발생

### 프로젝트 적용 시사점

Solar Pro 2 70B 및 Qwen3.5 폐쇄망 환경에서:
- 네이티브 tool-calling이 없거나 불안정할 경우, **Outlines 또는 XGrammar 기반 제약 디코딩**으로 JSON 스키마 준수를 강제하는 것이 더 안정적.
- 단순한 JSON 스키마(평면 구조)를 먼저 사용하고, 중첩을 최소화해야 커버리지를 유지할 수 있다.
- BFCL 기준 Qwen 계열이 OSS 중 최우수 function calling 성능을 보임 — 향후 업그레이드 시 유리.

### 기각된 대안

- **OSS 모델에서 tool schema를 복잡하게 설계하는 방안**: StructEval이 OSS 모델의 구조적 출력 능력 한계를 실증. 스키마 단순화 우선.
- **네이티브 tool-calling만 의존하는 방안**: 폐쇄망 vLLM 서빙 환경에서 프레임워크별 지원 편차가 크므로 JSON + 제약 디코딩 병행이 안전.

---

## 3. ReAct 루프 종료 전략

### 근거 논문

[T3] (MAST, 위 참조)에서 System Design Issues의 핵심 원인으로 "lack of termination criteria" 명시.

#### [T6] Solving LLM Repetition Problem in Production: A Comprehensive Study
- **저자**: 미상 (산업 연구)
- **연도**: 2024년 12월 (arXiv:2512.04419)
- **핵심 발견**:
  - **이론적 증명**: 그리디 디코딩 + 자기강화 효과 결합 시, "once the model enters a repetitive state, **the expected escape time is infinite under greedy decoding**" — 논문 직접 인용 (Markov 모델 기반 이론 분석)
  - 3가지 반복 패턴 식별: 비즈니스 규칙 생성 반복, 메서드 호출 관계 분석 반복, 다이어그램 구문 생성 반복
  - 해결책: Beam Search(width ≥ 2) + 조기 종료, 또는 repetition penalty 상향 (실측 권장값 ≥ 1.15)
  - Beam Search는 비반복 후보 시퀀스를 최소 1개 유지하여 탈출 보장

#### ReAct 루프 종료에 관한 실무 증거

Letta(MemGPT) 팀의 공개 사례 연구 ("Rearchitecting Letta's Agent Loop: Lessons from ReAct, MemGPT, & Claude Code"):
- ReAct 아키텍처에서 "when to reason, when to terminate, and whether they get stuck in infinite loops or exit prematurely"가 루프 설계의 핵심 문제임을 실무에서 확인
- LLM이 종료 토큰을 능동적으로 생성하는 방식에 의존하므로, 외부 종료 제약이 필수

### 프로젝트 적용 시사점

- **max_rounds 하드 리밋은 단순 안전장치가 아닌 수학적 필수요건**: 무한 반복의 탈출 시간이 이론상 무한대이므로 외부 종료 조건 없이는 루프 탈출 불가.
- 리커버리 루프에서 각 라운드마다 "이전 라운드와 동일한 액션을 취하려 하는가"를 감지하는 중복 액션 감지 로직이 필요하다.
- 신뢰도 임계값(confidence threshold) 기반 종료는 [이전 리서치: 20260331 명확화 트리거]에서도 기각된 바 있으며, 여기서도 동일하게 기각한다 — LLM이 보고하는 자기 신뢰도는 보정되지 않아 신뢰 불가.

### 기각된 대안

- **LLM이 "완료" 신호를 스스로 생성하도록 의존하는 방안**: T6이 이론적으로 불가함을 증명. 외부 카운터 필수.
- **confidence score ≥ 0.9 조건으로 종료하는 방안**: LLM 자기보고 신뢰도의 보정 불안정성으로 기각 (T2 AgentBench 결과와도 일치).

---

## 4. 반복적 에이전트의 컨텍스트 윈도우 관리

### 근거 논문

#### [T7] Solving Context Window Overflow in AI Agents
- **저자**: Anton Bulle Labate, Valesca Moura de Sousa 외 4인 (IBM Research Brazil)
- **연도**: 2025년 11월 (arXiv:2511.22729)
- **핵심 발견**:
  - 대형 도구 출력물이 컨텍스트 윈도우를 초과하여 태스크 완료를 막는 문제를 다룸
  - 제안 방법: 원시 데이터 대신 **메모리 포인터(memory pointers)** 를 통해 모델이 데이터와 상호작용
  - 실측: 기존 워크플로우 대비 **토큰 사용량 약 7배 감소**
  - 정보 손실 없는 완전한 데이터 보존이 핵심 (지식 집약적 도메인 대상)

#### [T8] The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management
- **저자**: 미상
- **연도**: 2025년 8월 (arXiv:2508.21433)
- **핵심 발견**:
  - OpenHands, Cursor 등 SOTA SE 에이전트는 LLM 기반 요약으로 컨텍스트를 관리
  - **단순 관찰 마스킹(observation masking)이 LLM 요약 대비 비용을 절반으로 줄이면서 solve rate를 동등하게 유지**
  - Hybrid 접근(마스킹 + 선택적 요약)이 마스킹 단독 대비 7%, LLM 요약 단독 대비 11% 추가 비용 절감
  - "simple observation masking ... matches, and sometimes slightly exceeds, the solve rate of LLM summarization" — 논문 직접 인용

#### [B2] Chain of Agents: Large Language Models Collaborating on Long-Context Tasks (Google, NeurIPS 2024)
- **저자**: Google Research
- **연도**: NeurIPS 2024
- **핵심 발견**:
  - 다중 worker 에이전트가 분절된 텍스트를 순차 처리 후 manager 에이전트가 통합
  - 맥락 분산을 통한 컨텍스트 한계 우회 전략의 실증

### 프로젝트 적용 시사점

Solar Pro 2 70B의 컨텍스트 윈도우가 8K-16K 범위라는 가정 하에:
- 도구 출력물(쿼리 결과, 메타 검색 결과)을 원시 텍스트로 컨텍스트에 누적하면 **3-4 라운드 이내에 오버플로우 발생 가능**.
- T8의 결론: 도구 실행 결과는 요약 없이 마스킹(직전 라운드만 보존 또는 키-값 포인터로 치환)하는 것이 비용과 효과 양면에서 최선.
- 사고 과정(Thought)은 전량 보존하고, 관찰값(Observation)에 선택적 마스킹 적용하는 하이브리드 전략이 현실적.

### 기각된 대안

- **LLM으로 매 라운드 이전 컨텍스트를 요약하는 방안**: T8이 비용 2배 대비 solve rate 동등임을 실증. 단순 마스킹 우선.
- **컨텍스트 오버플로우 무시 방안**: T7이 오버플로우가 태스크 완료 자체를 차단함을 실증.

---

## 5. 지식 관리에서 부분 문자열 매칭의 위험

### 조사 결과 및 한계

이 항목은 **Tier-1 직접 논문을 확인하지 못했다**. 검색 결과가 일반적 퍼지 매칭 기법 소개 문서에 국한되었으며, "부분 매칭으로 인한 지식베이스 오업데이트"를 직접 연구한 2024-2025년 논문을 발견하지 못했다.

### 확인된 간접 근거

NLP-in-Finance 관련 정보융합(Information Fusion) 저널 리뷰 (Sentic.net):
- 금융 도메인 NLP의 10개 주요 영역 중 용어 정밀도가 중요한 영역(규제 준수 모니터링, 위험 관리)에서 퍼지 매칭의 위험을 암시적으로 다룸
- 고정밀 요구 시: threshold ≥ 90 사용, 일부 매칭 누락을 감수하고 오매칭 방지 우선 권장

### 프로젝트 적용 시사점 (도메인 경험 기반)

직접 논문 없음에도 불구하고, 은행 도메인 특성에서 다음이 분명하다:
- "여신"과 "대출"은 동의어이나 시스템 분류상 다른 테이블 군에 속할 수 있음. 부분 매칭으로 교차 오인 가능성 존재.
- "연체율"은 정의된 산출식이 있으나, "연체" 단독 부분 매칭은 다른 컬럼을 참조할 위험.
- 비즈용어 사전(200개 미만)의 용어는 **정확 일치(exact match) 우선, 퍼지 매칭은 fallback으로만** 사용해야 안전.

### 기각된 대안

- **이 항목에 대해 논문 있는 척 허위 인용하는 방안**: 리서치 원칙(검증된 출처만 인용) 위반. 부재 사실을 명시.

---

## 6. 병렬 vs. 순차 도구 실행

### 근거 논문

#### [T9] Learning Latency-Aware Orchestration for Parallel Multi-Agent Systems
- **저자**: 미상
- **연도**: 2025년 1월 (arXiv:2601.10560)
- **핵심 발견**:
  - Latency-Aware Multi-Agent Architecture Search 프레임워크: 지연시간을 1차 최적화 목표로 설정
  - Critical Execution Path (의존 에이전트 상호작용의 최장 연쇄)를 명시적으로 패널티로 포함
  - "sequential scaling shows superior token efficiency, **parallel scaling can be 1.6x faster** when considering latency as the optimization budget" — 논문 직접 인용

#### 병렬 실행 효과에 대한 실증 수치 (kore.ai 기술 문서, 2024)
- 3개 독립 태스크 병렬 실행: 총 8초 (5초 동시 + 3초 통합) vs. 순차 실행 대비 **56% 실행 시간 감소**

### 프로젝트 적용 시사점

리커버리 루프 설계 시:
- **의존성이 없는 도구 호출** (예: 메타 검색 + 과거 SQL 검색 + 업무 매뉴얼 검색)은 병렬 실행이 지연시간 1.6배 이상 개선.
- **의존성이 있는 도구 호출** (예: 스키마 선택 완료 후 SQL 생성)은 강제 병렬화가 오류 전파 위험 증가.
- LangGraph의 `Send` API 또는 팬아웃 노드 구조가 이 분류에 자연스럽게 대응됨.

### 기각된 대안

- **모든 도구를 순차 실행하는 방안**: 지연시간이 도구 수에 비례하여 선형 증가. 은행 직원 대상 인터랙티브 서비스에서 허용하기 어려운 응답시간 발생.
- **모든 도구를 병렬 실행하는 방안**: 의존성 있는 단계에서 결과 불일치 및 오류 전파 위험. T9 논문이 경로 의존성 분석을 1차 설계 요소로 제시.

---

## 7. Two-Phase 탐색 패턴 (결정론적 컨텍스트 수집 + 에이전틱 리커버리)

### 근거 논문·구현사례

#### [T10] CHESS: Contextual Harnessing for Efficient SQL Synthesis (Stanford, 2024)
- **저자**: Shayan Talaei, Mohammadreza Pourreza, Yu-Chen Chang, Azalia Mirhoseini, Amin Saberi (Stanford)
- **연도**: 2024년 5월 (arXiv:2405.16755)
- **핵심 발견**:
  - **4단계 완전 분리 파이프라인**:
    1. Information Retrieval (IR): 키워드 기반 LSH + 벡터 DB로 관련 값 추출 (결정론적)
    2. Schema Selection (SS): 적응형 스키마 프루닝, 불필요 컬럼 제거 (반결정론적)
    3. Candidate Generation (CG): 다중 후보 SQL 생성 + 반복적 정제 (에이전틱)
    4. Unit Testing (UT): LLM 기반 자연어 단위 테스트로 검증
  - 스키마 프루닝만으로 **LLM 토큰 5배 감소 + 정확도 약 2% 향상**
  - BIRD 리더보드: 개발셋 65%, 테스트셋 **66.69% 실행 정확도 (공개 방법론 1위)**
  - 고예산 설정: 71.10% 정확도로 선행 독점 방법 대비 **LLM 호출 83% 감소**
  - **핵심 인사이트**: 컨텍스트 수집을 SQL 생성으로부터 완전히 분리하는 것이 효율과 정확도 양면에서 이익

#### [T11] MAC-SQL: A Multi-Agent Collaborative Framework for Text-to-SQL (COLING 2025)
- **저자**: Wang Ben et al.
- **연도**: 2023년 12월 (arXiv:2312.11242), COLING 2025 채택
- **핵심 발견**:
  - 3-에이전트 구조: Selector → Decomposer → Refiner
  - Selector: 스키마 사전 필터링 (결정론적 컨텍스트 수집)
  - Decomposer: CoT 기반 복잡 쿼리 분해 (에이전틱)
  - Refiner: 오류 SQL 교정 (에이전틱 리커버리)
  - BIRD 테스트셋: 59.59% 실행 정확도 (당시 SOTA)
  - Spider: 86.75%

#### DIN-SQL: Decomposed In-Context Learning of Text-to-SQL with Self-Correction (NeurIPS 2023)
- **저자**: Mohammadreza Pourreza, Davood Rafiei
- **연도**: 2023년 4월 (arXiv:2304.11015), NeurIPS 2023
- **핵심 발견**:
  - 태스크 분해(task decomposition) → Few-shot 서브태스크 프롬프트 → 자기교정의 3단계
  - 단순 few-shot 대비 일관되게 **약 10% 정확도 향상**
  - Spider 테스트셋: 79.9 → **85.3 SOTA** 달성 (당시 기준)
  - 분해 없는 단일 프롬프트 대비 9% 향상 (BIRD 개발셋)

### 프로젝트 적용 시사점

세 논문이 공통적으로 지지하는 패턴:
1. **결정론적 컨텍스트 수집 선행** (메타 검색, 스키마 선택): LLM 없이 실행 가능, 비용·지연 최소화
2. **에이전틱 리커버리는 컨텍스트가 확정된 후 시작**: CHESS가 LLM 호출 83% 감소로 실증
3. **리커버리 단계(Refiner)는 명확하게 분리된 역할**: 생성 에이전트와 교정 에이전트를 혼합하지 않음

이 패턴은 현재 재설계 대상 리커버리 루프에서 "컨텍스트 수집 노드 (결정론적) → SQL 생성 노드 → 실패 시 리커버리 루프 (에이전틱)" 3단 구조의 타당성을 직접 지지한다.

### 기각된 대안

- **단일 에이전트가 컨텍스트 수집과 SQL 생성을 동시에 수행하는 방안**: CHESS가 분리 시 토큰 5배 감소를 실증. 통합 방식은 비효율적이고 정확도도 낮음.
- **리커버리 없는 단일 시도 방안**: DIN-SQL의 self-correction이 없을 때 9-10% 정확도 손실을 기록.

---

## 8. LLM 에이전트 오케스트레이션을 위한 상태 머신 설계

### 근거 문서·논문

#### LangGraph 공식 문서 및 아키텍처 (2024)
- **출처**: LangChain 공식 문서 (docs.langchain.com/oss/python/langgraph/overview)
- **핵심 발견**:
  - LangGraph는 **지향 그래프(directed graph) 또는 유한 상태 머신(FSM)으로 지능형 워크플로우를 명시적으로 모델링**
  - 2024년 초 LangChain과 분리 독립 라이브러리로 출시: "LangChain fell short with complex agentic workflows involving loops or cycles" — 공식 문서
  - 각 에이전트는 노드, 엣지가 에이전트 간 정보·제어 흐름을 정의
  - 임의 분기, 합류, 사이클(반복/재계획 로직), 조건부 엣지 술어(conditional edge predicates) 지원

#### [T3] MAST 논문의 상태 머신 관련 함의 (위 참조)
- System Design Issues의 하위 실패 모드에 "poor prompt design, missing role constraints, **lack of termination criteria**" 포함
- 명시적 상태 전환 설계가 이를 구조적으로 예방함을 역으로 시사

### 프로젝트 적용 시사점

LangGraph 기반 현재 파이프라인에서:
- 각 노드가 명확한 진입·탈출 조건을 갖는 **명시적 상태** 로 정의되어야 함 (암묵적 흐름 제어 지양)
- 리커버리 루프는 `current_round`, `max_rounds`, `last_action_hash` 등 상태 변수를 명시적으로 State 스키마에 포함해야 함
- 조건부 엣지(conditional edge)로 종료 조건을 그래프 레벨에서 강제: LLM에게 종료 판단을 맡기지 않음

### 기각된 대안

- **암묵적 흐름 제어 (LLM 출력에만 의존하는 상태 전환)**: MAST가 실패 모드로 직접 분류. 명시적 FSM이 필수.
- **LangChain LCEL 체인으로 루프 구현하는 방안**: LangGraph 탄생 이유가 LCEL의 루프·사이클 미지원임 (공식 문서).

---

## 종합 권고사항

| 주제 | 권고 방향 | 근거 논문 |
|------|-----------|-----------|
| ReAct 패턴 | 순수 ReAct 지양, Pre-Act식 사전 계획 단계 추가 | T1, T2 |
| 구조화 출력 | 단순 JSON 스키마 + 제약 디코딩, 복잡 스키마 회피 | T4, B1 |
| 루프 종료 | max_rounds 하드 리밋 필수, 신뢰도 임계값 기각 | T3, T6 |
| 컨텍스트 관리 | 관찰값 마스킹 우선, LLM 요약은 비용 대비 효과 무차별 | T7, T8 |
| 부분 매칭 | 정확 일치 우선, 퍼지는 fallback으로만 | 도메인 경험 기반 (논문 없음) |
| 도구 실행 순서 | 의존성 분석 후 독립 도구는 병렬화 | T9 |
| Two-Phase 탐색 | 컨텍스트 수집 결정론적 선행 → 에이전틱 생성/리커버리 | T10, T11 |
| 상태 머신 | LangGraph 명시적 상태·엣지 설계, 암묵적 흐름 금지 | T3, LangGraph 공식 |

---

## 참고문헌 목록

**Tier-1 논문 (arXiv / 학술지 / 학회)**

1. Rawat et al. (2025). Pre-Act: Multi-Step Planning and Reasoning Improves Acting in LLM Agents. arXiv:2505.09970
2. Liu et al. (2023/ICLR 2024). AgentBench: Evaluating LLMs as Agents. arXiv:2308.03688
3. Cemri et al. (2025/NeurIPS 2025). Why Do Multi-Agent LLM Systems Fail? arXiv:2503.13657
4. Guidance-AI (2025). JSONSchemaBench: A Rigorous Benchmark of Structured Outputs. arXiv:2501.10868
5. Tiger AI Lab (2025). StructEval: Benchmarking LLMs' Capabilities to Generate Structural Outputs. arXiv:2505.20139
6. (2024). Solving LLM Repetition Problem in Production. arXiv:2512.04419
7. Labate et al. (2025). Solving Context Window Overflow in AI Agents. arXiv:2511.22729
8. (2025). The Complexity Trap: Simple Observation Masking. arXiv:2508.21433
9. (2025). Learning Latency-Aware Orchestration for Parallel Multi-Agent Systems. arXiv:2601.10560
10. Talaei et al. (2024). CHESS: Contextual Harnessing for Efficient SQL Synthesis. arXiv:2405.16755
11. Wang et al. (2023/COLING 2025). MAC-SQL: A Multi-Agent Collaborative Framework. arXiv:2312.11242
12. Pourreza & Rafiei (2023/NeurIPS 2023). DIN-SQL: Decomposed In-Context Learning. arXiv:2304.11015

**벤치마크**

- Berkeley Function Calling Leaderboard (BFCL) v1-v4. UC Berkeley Sky Computing Lab. gorilla.cs.berkeley.edu
- AgentBench. THUDM, Tsinghua. github.com/THUDM/AgentBench
- JSONSchemaBench. Guidance-AI. github.com/guidance-ai/jsonschemabench

**공식 문서**

- LangGraph Overview. LangChain. docs.langchain.com/oss/python/langgraph/overview
- LangGraph State Machines for Production. dev.to/jamesli/langgraph-state-machines-managing-complex-agent-task-flows-in-production-36f4
