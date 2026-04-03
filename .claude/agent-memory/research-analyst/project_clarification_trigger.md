---
name: 명확화 트리거 기준 및 파이프라인 위치 확정
description: NL-to-SQL 명확화 결정 로직의 집중형 단일 노드 구조 근거 및 AmbiSQL 7종 분류 체계 적용 확정
type: project
---

집중형 단일 노드(`clarification_handler`) 구조가 학술 근거(Sphinteract VLDB 2025, EIG arXiv 2507.06467)와 LangGraph 공식 권고와 일치함을 확인.

**트리거 기준**:
- AmbiSchema / AmbiValue / AmbiIntent → 명확화 질문 필수
- AmbiRef → 기본값 제안 후 확인
- SELECT/WHERE 컬럼 모호성 → 명확화 대신 복수 해석 SQL 반환 (PRACTIQ NAACL 2025)
- AmbiFallacy → 불가능 이유 + 대안 제시
- 신뢰도 임계값 단독 트리거 → 기각 (폐쇄망 LLM 로그확률 부정확)

**핵심 수치**:
- Sphinteract: 명확화 적용 시 최대 +42% 정확도 향상 (BIRD 벤치마크), 평균 2.18회 상호작용 최적
- EIG: Spider 모호 집합 76.6% vs 기준치 72.1%
- PRACTIQ: 모호 쿼리에서 Claude 3.5 Sonnet 71.27% (정상 쿼리 79.21%)

**LangGraph 공식 권고**: 정적 interrupt_before/interrupt_after는 HITL 워크플로 비권고. 동적 interrupt() + 단일 노드 내 루프 패턴 권장.

**Why**: 분산형은 interrupt 재실행 시 인덱스 정합성 위험, 명확화 컨텍스트 분산으로 중복 질문 발생 가능성.

**How to apply**: AmbiSQL 7종 분류 체계를 clarification_handler 노드의 LLM 프롬프트에 명시. DTE 패턴으로 "왜 질문하는지" 사용자에게 자연어 설명 포함. IT 비전문가 대상이므로 테이블명 직접 노출 금지.

**보고서 위치**: `docs/research/20260331-clarification-determination-in-nl2sql-agents.md`
