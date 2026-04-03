# 명확화 질문 결정 전략: LLM 기반 NL-to-SQL 에이전트 파이프라인

**작성일**: 2026-03-31
**작성자**: Research Analyst Agent
**대상 시스템**: Data Copilot (은행 NL-to-SQL 에이전트)
**연구 목적**: LangGraph 멀티스텝 파이프라인에서 명확화 질문 결정 로직의 위치(집중형 vs 분산형)와 트리거 기준에 대한 검증된 근거 수집

---

## 요약 (Executive Summary)

| 질문 | 결론 |
|------|------|
| 명확화 노드 구조 | **집중형 단일 노드** 권고 (분산형 기각) |
| 트리거 기준 | **분류 기반 + EIG 방식** (신뢰도 임계값 단독 사용 기각) |
| 명확화 횟수 | 평균 **2.18회** 상호작용이 최적 (Sphinteract, VLDB 2025) |
| 정확도 향상 효과 | 명확화 적용 시 최대 **+42%** 정확도 향상 (BIRD 벤치마크) |
| 한국어/금융 도메인 특이사항 | AmbiSchema·AmbiValue 유형이 금융 메타 불완전성과 직결됨 |

---

## 1. 배경 및 연구 범위

### 1.1 연구 질문

이 보고서는 다음 네 가지 질문에 답한다.

1. 명확화 로직은 단일 전용 노드에 집중해야 하는가, 파이프라인 전체에 분산해야 하는가?
2. LangGraph 공식 HITL 패턴은 무엇인가?
3. NL-to-SQL 논문들은 언제 명확화를 요청할지 어떻게 결정하는가?
4. 프로덕션 시스템(Uber QueryGPT 등)은 모호한 쿼리를 어떻게 처리하는가?

### 1.2 이 프로젝트의 특수 조건

- 사용자: IT 지식 없는 은행 일반 직원
- 도메인: 금융 전문 용어, 유사 테이블 다수, 불완전한 컬럼 메타
- 배포: 폐쇄망, 오픈소스 LLM (Solar Pro 2 70B / Qwen3.5)
- 기존 구현: `clarification_handler` 단일 노드 + Strategy 패턴 (2026-03-30 확정)

---

## 2. 명확화 노드 구조: 집중형 vs 분산형

### 2.1 학술 근거

**AmbiSQL (arXiv 2508.15276, 2025)**은 2단계 파이프라인에서 명확화를 분산 배치하는 설계를 채택했다. Stage 1에서 모호성 감지 및 질문 생성, Stage 2에서 사용자 응답 처리 후 재검증하는 구조다. 그러나 AmbiSQL의 분산 구조는 **반복 정제(iterative refinement)** 목적이며, 명확화 결정 로직 자체가 분산된 것이 아니라 명확화-정제 루프가 반복되는 구조다.

**Sphinteract (VLDB 2025, Zhao et al.)**는 SRA(Summarize-Review-Ask) 패러다임을 통해 명확화 결정을 **단일 리뷰 단계에 집중**시킨다. LLM이 후보 SQL들을 요약하고, 분기점을 검토하며, 가장 가치 있는 질문을 선택하는 과정을 단일 모듈에서 수행한다. 이 방식으로 BIRD 벤치마크에서 최대 +42% 정확도 향상을 달성했다.

**EIG 기반 시스템 (arXiv 2507.06467, 2025)**은 후보 SQL 분포 위에서 기대정보이득(Expected Information Gain)을 계산해 가장 정보가치가 높은 모호성 하나를 선택하는 방식이다. 이는 본질적으로 **집중형** 의사결정 구조다. Spider 모호 부분집합에서 76.6% 실행 정확도를 달성했으며 분산형 기준치(72.1% Max Probability)를 상회했다.

### 2.2 LangGraph 공식 권고

LangGraph 공식 문서(docs.langchain.com)는 정적 `interrupt_before` / `interrupt_after` 방식을 **사람-개입 워크플로에 권장하지 않음**으로 명시한다. 대신 노드 내부에서 조건부로 호출하는 동적 `interrupt()` 함수를 권장하며, 반복 명확화 패턴으로는 **단일 노드 내 루프** 구조를 예시로 제시한다:

```python
# LangGraph 공식 권장 패턴 (iterative clarification)
while True:
    answer = interrupt(prompt)
    if valid(answer):
        break
    prompt = "다시 입력해 주세요: " + error_message
```

이 패턴은 관련 로직을 단일 노드에 집중시키고, 여러 회 반복을 루프로 처리한다. 여러 노드에 분산 배치하면 인덱스 기반 재실행 순서 보장 문제가 발생하므로 단일 노드 집중이 더 안전하다.

### 2.3 프로덕션 사례: Uber QueryGPT

Uber QueryGPT(2024, Uber Engineering Blog)는 **Intent Agent → Table Agent → SQL Agent** 의 집중형 라우팅 아키텍처를 채택한다. 명확화는 Table Agent 단계에서 중간 결과(선택된 테이블 목록)를 사용자에게 제시해 확인/편집하는 방식으로 **한 지점에서 집중적으로** 처리된다. 추가로 Prompt Enhancer가 모호한 입력을 LLM 전달 전에 전처리한다.

### 2.4 결론: 집중형 단일 노드 권고

| 기준 | 집중형 | 분산형 |
|------|--------|--------|
| LangGraph 공식 권고 | O (루프 패턴) | X (정적 interrupt 비권장) |
| 디버깅·감사 추적 | 단일 진입점으로 용이 | 여러 노드 추적 필요 |
| 상태 관리 | 명확화 이력 한 곳에 관리 | 상태 전파 복잡 |
| Sphinteract SRA 모델 | 단일 Review 단계 | - |
| 기각 이유 (분산형) | - | interrupt 재실행 시 인덱스 정합성 위험, 명확화 컨텍스트 분산으로 중복 질문 발생 가능 |

**기각된 대안**: 각 파이프라인 노드(스키마 검색, SQL 생성, 검증)마다 개별 interrupt를 배치하는 분산 구조. 기각 이유: LangGraph의 interrupt 재실행 특성상 인덱스 기반 매칭이 필요하므로 노드별 분산 시 재실행 순서 보장이 어렵고, 동일 불확실성에 대한 중복 질문이 발생할 수 있다.

---

## 3. 명확화 트리거 기준: 언제 질문할 것인가

### 3.1 모호성 분류 체계 (AmbiSQL Taxonomy)

AmbiSQL(arXiv 2508.15276)은 NL-to-SQL 모호성을 두 축으로 분류한다. 이 분류는 금융 도메인 적용에 직접 매핑 가능하다.

**데이터베이스 기인 모호성 (DB-sourced)**

| 유형 | 정의 | 금융 도메인 예시 |
|------|------|-----------------|
| AmbiSchema | 테이블·컬럼 참조 불명확 | "여신 잔액"이 `LOAN_BALANCE` vs `CREDIT_BALANCE` 중 어느 테이블인지 불명확 |
| AmbiValue | DB에 존재하지 않는 값 | "VIP 고객"이라는 단어가 DB 코드값과 매핑 안 됨 |
| AmbiIntent | 연산 방식 불명확 | "이번 달 여신"이 신규 실행 건수인지 잔액인지 불명확 |

**LLM 기인 모호성 (LLM-sourced)**

| 유형 | 정의 | 금융 도메인 예시 |
|------|------|-----------------|
| AmbiSource | DB 조회 vs 상식 추론 경계 | "연체율" 산출식을 DB에서 가져올지 일반식으로 계산할지 |
| AmbiContext | 추론 근거 부족 | 코드값 정의 없이 `STATUS_CD = '02'`의 의미 추론 |
| AmbiFallacy | 모순된 전제 | "지난 3년간 월별 데이터" 요청 + 기간 3개월 테이블만 존재 |
| AmbiRef | 시간·공간 기준 불명확 | "최근 실적"이 이번 달인지 이번 분기인지 불명확 |

### 3.2 트리거 기준 비교

**방식 A: 분류 기반 트리거 (권고)**

AmbiSQL과 Sphinteract가 채택한 방식. LLM에게 위의 분류 체계와 대표 예시를 제공한 프롬프트로 모호성 유형을 식별하게 한다. 각 유형별 처리 정책을 사전 정의한다:

- AmbiSchema → 명확화 질문 필수
- AmbiValue → 유사 코드값 제안 후 확인
- AmbiIntent → 선택지 제시 (최대 3개)
- AmbiRef → 기본값 제안 후 확인 ("이번 달로 처리하면 맞나요?")
- AmbiFallacy → 불가능 이유 설명 후 가능한 대안 제시

**방식 B: EIG(기대정보이득) 기반 트리거**

EIG 시스템(arXiv 2507.06467)이 채택한 방식. n개의 후보 SQL을 생성하고, 각 모호성 요소의 정보이득 `I(Xi;Y) = H(Y) - H(Y|Xi)`를 계산해 가장 높은 요소 하나만 질문한다. Spider 모호 집합에서 73.4%→76.6% 실행 정확도 향상. 단, 후보 SQL n개 생성이 필요해 LLM 호출 비용이 증가한다.

**방식 C: 신뢰도 임계값 단독 (기각)**

단일 생성 결과의 신뢰도 점수가 임계값 이하일 때 질문하는 방식. TrustSQL(arXiv 2403.15879)이 연구했으나, 신뢰도 점수가 캘리브레이션 안 된 LLM에서는 불신뢰하며 임계값 튜닝에 도메인별 골든셋이 필요하다. 기각 이유: 폐쇄망 중간규모 모델(Solar Pro 2 70B)에서 로그확률 기반 신뢰도 점수가 부정확함.

### 3.3 DTE 프레임워크 (ACL 2023 Findings)

Wang et al. (2023, arXiv 2212.08902)의 DTE(Detect-Then-Explain)는 모호성을 6개 카테고리로 분류하고 탐지-설명 2단계를 거친다. 탐지 단계에서 모호 여부를 이진 분류하고, 설명 단계에서 어떤 부분이 왜 모호한지 사용자에게 설명한다. 이 방식은 "왜 질문하는지"를 사용자에게 투명하게 전달해 사용자 경험을 개선한다. 금융 IT 지식이 없는 사용자 대상 시스템에 특히 적합하다.

### 3.4 불필요한 명확화 억제 기준 (PRACTIQ)

PRACTIQ(NAACL 2025)는 모든 모호성에 질문하는 것이 최선이 아님을 보인다. Claude 3.5 Sonnet 기준 모호/무응답 가능 쿼리에서 71.27% 정확도 달성으로 명확화 후에도 8포인트 gap이 존재한다. 핵심 발견: **Ambiguous SELECT Column, Ambiguous WHERE Column 유형은 복수 해석을 모두 포함하는 SQL을 반환하는 것이 명확화보다 효과적**이다. 즉, "어떤 컬럼을 원하세요?"를 묻는 대신 가능한 컬럼을 모두 SELECT하는 방식이 사용자 편의에 더 낫다.

---

## 4. NL-to-SQL 논문별 접근법 요약

### 4.1 Sphinteract (VLDB 2025)

- **출처**: Zhao et al., VLDB 2025 (Tier 1 - VLDB는 데이터베이스 최상위 학술지)
- **접근법**: SRA 패러다임 — 후보 SQL 요약(Summarize) → 분기점 검토(Review) → 질문 선택(Ask)
- **파이프라인 위치**: 명확화 결정이 단일 Review 단계에 집중
- **성능**: KaggleDBQA + BIRD에서 최대 **+42% 정확도 향상**, 평균 **2.18회 상호작용**
- **장점**: 오픈엔드 피드백(45% 발생)과 객관식 모두 수용
- **단점**: SRA 단계 자체가 추가 LLM 호출 필요

### 4.2 AmbiSQL (arXiv 2508.15276, 2025)

- **출처**: arXiv 2025, 아직 동료심사 미완료 (Tier 2)
- **접근법**: 7종 모호성 분류 체계 + In-context Learning으로 자동 탐지 + 객관식 명확화 질문
- **파이프라인 위치**: 분산형 (2단계 반복 루프)
- **성능**: 정량 수치 미제공 (정성적 개선 보고)
- **장점**: 분류 체계가 구체적이어서 프롬프트 설계에 직접 활용 가능
- **단점**: 정확도 수치 없음, 반복 루프로 지연 가능성

### 4.3 EIG 기반 Interactive Text-to-SQL (arXiv 2507.06467, 2025)

- **출처**: arXiv 2025 (Tier 2)
- **접근법**: n개 후보 SQL 생성 → 기대정보이득 계산 → 최고 이득 모호성만 질문
- **파이프라인 위치**: 집중형 (후보 생성 후 단일 결정 단계)
- **성능**: Spider 모호 집합 76.6% (vs Max Probability 기준치 72.1%), AmbiQT Column 58.95%
- **장점**: 이론적 최적성, 질문 횟수 최소화
- **단점**: n개 SQL 생성 비용, 폐쇄망 중간규모 모델에서 다양한 후보 생성 품질 불확실

### 4.4 DTE - Know What I Don't Know (ACL Findings 2023)

- **출처**: Wang et al., ACL 2023 Findings (Tier 1)
- **접근법**: 6개 카테고리 분류 후 탐지(detect)-설명(explain) 2단계, 약지도 학습
- **파이프라인 위치**: 집중형 (독립 탐지 모듈)
- **장점**: 사용자에게 "왜 모르는지" 설명 제공 → IT 비전문가에게 적합
- **단점**: 미세조정(fine-tuning) 필요, 금융 도메인 데이터로 재학습 권장

### 4.5 PRACTIQ (NAACL 2025)

- **출처**: NAACL 2025 (Tier 1)
- **접근법**: 명확화 vs 복수 해석 SQL 반환 vs 불응답의 3방향 분기
- **파이프라인 위치**: 집중형 (쿼리 유형 분류 후 처리 정책 결정)
- **핵심 발견**: SELECT/WHERE 컬럼 모호성은 명확화 대신 복수 해석 SQL이 효과적
- **성능**: Claude 3.5 Sonnet 71.27% (모호/불응답), 79.21% (정상 쿼리)

---

## 5. 프로덕션 시스템 사례

### 5.1 Uber QueryGPT (2024)

- **출처**: Uber Engineering Blog (Tier 3 - 엔지니어링 블로그)
- **접근법**: Intent Agent (도메인 분류) → Table Agent (테이블 선택 후 사용자 ACK) → SQL Agent
- **명확화 위치**: Table Agent 단계에서 중간 확인(테이블 목록 제시) — 집중형
- **추가 장치**: Prompt Enhancer가 모호한 5단어 입력을 전처리 (명확화 전 보강)
- **성능 지표**: 월 140,000 시간 절약 (SQL 작성 시간 기준), 정확도 지표는 미공개
- **특이사항**: 명확화를 "질문"이 아닌 "중간 결과 확인"으로 구현 — 사용자 부담 감소

### 5.2 Google BigQuery + Gemini NL2SQL (2024)

- **출처**: Google Cloud Blog
- **접근법**: Gemini Flash 1.5를 라우팅 에이전트로 사용, 쿼리 복잡도 분류 후 모호성 체크
- **명확화 위치**: 라우팅 에이전트 단계에서 집중 처리
- **특이사항**: 벡터 임베딩 + 시맨틱 검색으로 스키마 그라운딩 선행 후 모호성 판단

---

## 6. 종합 권고사항

### 6.1 이 프로젝트(Data Copilot)에 적용할 아키텍처

현재 구현된 `clarification_handler` 단일 노드 + Strategy 패턴(2026-03-30 확정)은 연구 결과와 **일치한다**. 추가로 다음을 반영할 것을 권고한다.

**트리거 판단 로직 강화**

```
명확화 요청 결정 순서:
1. AmbiSchema/AmbiValue/AmbiIntent → 명확화 질문 필수
2. AmbiRef (시간 기준 불명확) → 기본값 제안 후 확인 ("이번 달 기준으로 처리할까요?")
3. Ambiguous SELECT/WHERE Column → 명확화 대신 복수 해석 포함 SQL 반환 (PRACTIQ)
4. AmbiFallacy → 불가능 이유 설명 + 대안 제시
5. 신뢰도 임계값 단독 트리거 → 사용하지 않음
```

**질문 수 제한**

- Sphinteract: 평균 2.18회가 최적
- 이 프로젝트 `.claude/rules/user-interaction.md`: 이미 "최대 2-3개" 규정 — 연구 결과와 일치

**사용자 설명 포함 (DTE 패턴)**

"무엇을 확인하는지" 뿐 아니라 "왜 확인이 필요한지"를 자연어로 설명. 예:
- 좋은 예: "이번 달 여신 잔액을 조회하려고 하는데, 정보계에 유사한 테이블이 두 개 있어서 확인이 필요합니다: 1) 일별 잔액 테이블 2) 월말 기준 잔액 테이블 — 어느 쪽이 필요하신가요?"
- 나쁜 예: "LOAN_BAL_D와 LOAN_BAL_M 중 어떤 테이블을 사용할까요?"

**폐쇄망 모델 대응**

Solar Pro 2 70B / Qwen3.5 환경에서:
- AmbiSQL의 7종 분류 체계를 시스템 프롬프트에 명시적으로 포함
- 예시(few-shot) 3개 이상 포함 (AmbiSQL의 In-context Learning 방식)
- EIG 방식은 n개 후보 SQL 생성이 필요하므로 폐쇄망 제약(지연, 비용)을 고려해 **선택적 적용** 권고

### 6.2 기각된 대안 요약

| 대안 | 기각 이유 |
|------|----------|
| 파이프라인 노드별 분산 interrupt | LangGraph interrupt 재실행 특성상 인덱스 정합성 위험, 중복 질문 발생 가능 |
| 신뢰도 임계값 단독 트리거 | 폐쇄망 중간규모 LLM의 로그확률 신뢰도 부정확, 도메인별 임계값 튜닝 골든셋 필요 |
| SELECT/WHERE 컬럼 모호성 → 명확화 질문 | PRACTIQ: 복수 해석 SQL 반환이 더 효과적 |
| 매 단계 사용자 확인 (Uber의 Table ACK 모방) | 은행 일반 직원 대상으로 UI 부담 과다, 테이블명 노출로 IT 용어 노출 문제 |

---

## 7. 출처 목록

### Tier 1 논문 (동료심사 완료)

1. **Sphinteract**: Zhao et al., "Sphinteract: Resolving Ambiguities in NL2SQL Through User Interaction," VLDB 2025. https://dl.acm.org/doi/10.14778/3717755.3717772
2. **DTE / Know What I Don't Know**: Wang et al., "Know What I don't Know: Handling Ambiguous and Unanswerable Questions for Text-to-SQL," ACL Findings 2023. https://arxiv.org/abs/2212.08902
3. **PRACTIQ**: "A Practical Conversational text-to-SQL dataset with Ambiguous and Unanswerable Queries," NAACL 2025. https://aclanthology.org/2025.naacl-long.13.pdf
4. **NL2SQL is a solved problem... Not!**: Floratou et al., CIDR 2024. https://www.cidrdb.org/cidr2024/papers/p74-floratou.pdf
5. **NL2SQL State of the Art**: Luo et al., VLDB 2025 Survey. https://www.vldb.org/pvldb/vol18/p5466-luo.pdf

### Tier 2 논문 (arXiv, 동료심사 미완료)

6. **AmbiSQL**: "Interactive Ambiguity Detection and Resolution for Text-to-SQL," arXiv 2508.15276 (2025). https://arxiv.org/html/2508.15276
7. **EIG Interactive Text-to-SQL**: "Interactive Text-to-SQL via Expected Information Gain for Disambiguation," arXiv 2507.06467 (2025). https://arxiv.org/html/2507.06467
8. **Confidence Estimation**: "Confidence Estimation for Text-to-SQL in Large Language Models," arXiv 2508.14056 (2025). https://arxiv.org/pdf/2508.14056

### Tier 3 (기술 블로그 / 공식 문서)

9. **LangGraph HITL 공식 문서**: LangChain. https://docs.langchain.com/oss/python/langgraph/interrupts
10. **LangGraph interrupt() 도입 블로그**: LangChain Blog. https://blog.langchain.com/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt/
11. **Uber QueryGPT**: Uber Engineering Blog (2024). https://www.uber.com/blog/query-gpt/
12. **Agentic LLM Pipelines for Spatio-Temporal Text-to-SQL**: arXiv 2510.25997 (2025). https://arxiv.org/html/2510.25997

---

## 부록: 금융 도메인 명확화 트리거 예시 매핑

| 사용자 입력 | 모호성 유형 | 권고 처리 |
|------------|------------|----------|
| "이번 달 여신 현황 알려줘" | AmbiIntent (잔액? 신규? 건수?) | 선택지 3개 제시 |
| "VIP 고객 목록 뽑아줘" | AmbiValue (VIP 코드값 불명확) | 시스템에서 조회 가능 코드값 제시 후 확인 |
| "연체율 계산해줘" | AmbiSource (산출식 어디서?) | 업무 매뉴얼 조회 후 산출식 확인 요청 |
| "최근 3개월 실적" | AmbiRef (기준일 불명확) | "오늘 기준 최근 3개월(1월~3월)로 처리할까요?" |
| "작년 vs 올해 비교" | AmbiRef + AmbiIntent | 기간 확인 + 지표 확인 순차 질문 |
| "지점별 대출 금액" | AmbiSchema (테이블 유사 여러 개) | 용도·갱신주기 안내 후 선택 요청 |
| "주요 컬럼 다 보여줘" | Ambiguous SELECT Column | 명확화 대신 주요 컬럼 전체 포함 SQL 생성 (PRACTIQ) |
