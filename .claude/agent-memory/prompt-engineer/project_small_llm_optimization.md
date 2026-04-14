---
name: 중대형 LLM(Qwen3.5 MoE) 프롬프트 최적화 원칙
description: Qwen3.5-397B-A17B(MoE, 활성 17B) 대상 프롬프트 설계 핵심 원칙 (GPT-3.5급 구 원칙 대체)
type: project
---

# 중대형 LLM(Qwen3.5 MoE) 프롬프트 최적화 원칙

타겟 모델: Qwen3.5-397B-A17B (MoE, 활성 파라미터 17B), 폐쇄망 배포

**Why:** MoE expert routing 특성상 긴 규칙 리스트 중간이 무시됨. thinking 모드가 Text2SQL에서 오히려 불리하다는 커뮤니티 평가 있음.

**How to apply:** 신규 프롬프트 작성 및 기존 프롬프트 수정 시 아래 원칙 준수.

## 규칙 총량 제한

- system 지시사항(번호 있는 규칙 목록) 7개 이내
- 규칙이 많으면 MoE 중간 구간 규칙이 dead zone이 됨
- 초과 규칙은 few-shot 예시로 흡수

## thinking 모드 전략

- 계획수립형(sql_generator, recovery_agent): OFF — `/no_think` user 말미 삽입
  - temp=0.7, top_p=0.8, presence_penalty=1.5
  - 이유: Text2SQL은 구조화된 검색 문제, thinking이 불리. context 소비 비효율.
- 분석판정형(context_interpreter, query_normalizer, sql_validator): ON
  - temp=0.6, top_p=0.95
  - 이유: 경계 케이스 판단에는 추론이 유리
- 단순형(intent_classifier, viz_judgment): OFF
  - temp=0.7, top_p=0.8

## JSON 안정성

- 출력 명령은 단 하나의 문장: "JSON 객체 하나만 출력한다. 마크다운 코드블록(```), 설명, 주석 금지."
- 필드 description은 스키마 외부에 주석으로 달지 않음 (값으로 오인 패턴)
- vLLM v0.9.1 미만: enable_thinking=False + guided_json 조합 금지 (버그 #18819)
- 코드 레이어: Pydantic v2로 status 필드 enum 검증 필수

## 금지 규칙 긍정 형태 전환

- "INSERT/UPDATE/DELETE 절대 금지" → "SELECT 문만 작성한다"

## user 말미 재강조 (3가지만)

1. JSON 출력 명령 1줄
2. dialect 규칙 (동적 값)
3. fail 판단 핵심 원칙: "정확성을 보장할 수 없으면 fail"

고정 규칙(PII, 보안, 환각방지)은 system에서만 1회 — 재강조 시 혼란 유발

## Few-shot 전략

- 최적: 4개 (success 2개, fail 2개)
- fail 케이스 마지막 배치 (recency bias 활용)
- "코드값 없음 → fail" 패턴을 마지막 예시로 고정

## 기각된 패턴

- thinking 상시 ON + 전체 스키마 주입 + system 규칙 집중: MoE dead zone + context 낭비로 역효과
