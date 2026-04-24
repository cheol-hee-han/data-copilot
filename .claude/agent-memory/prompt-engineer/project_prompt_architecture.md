---
name: 프롬프트 아키텍처 결정 사항
description: resources/prompts/ 하위 txt 파일 구조, 플레이스홀더 방식, 계층별 역할
type: project
---

모든 LLM 프롬프트는 `resources/prompts/{계층}/` 하위 txt 파일로 관리한다.
변수 치환은 `src/utils/llm/prompt.py`의 `render_prompt()` 함수로 `{variable}` 단일 중괄호 치환 방식을 사용한다.
reason 계층도 동일하게 `.replace()` 기반이므로 `.format()` / `{{double braces}}` 사용 금지.

**Why:** 프롬프트를 txt 파일로 분리하여 코드 수정 없이 프롬프트 튜닝이 가능하도록 중앙화.

**How to apply:** 노드 파일에 프롬프트 문자열을 직접 작성하지 말 것. 신규 프롬프트는 resources/prompts/ 하위 txt 파일로 추가.

## 파이프라인 계층별 파일 구조

### interpret/
- context_classifier_system.txt + context_classifier_user.txt — 연속대화 여부 + 의도 분류
- query_normalizer_phase1_system.txt + phase1_user.txt — 8-Slot 질의 분해
- query_normalizer_phase2_system.txt + phase2_user.txt — 교차 검증 (R1~R12)

### reason/
- planner_system.txt — 지식 항목 등록 + 탐색 실행계획 생성
- knowledge_interpreter_system.txt — 도구 결과 통합 분석 + 테이블 판정
- table_comparison_system.txt — 유사 테이블 선별
- sql_generator_system.txt — SQL 생성 ({fix_section} 포함)
- sql_generator_fix_section.txt — 재생성 시 주입되는 fix 섹션
- sql_validator_system.txt — 의미적 검증 (7개 체크포인트)
- recovery_agent_system.txt — 추가 탐색 에이전트 (multi-turn)

### present/
- analyzer_system.txt + analyzer_user.txt — 데이터 분석 인사이트
- analyzer_viz_judgment_system.txt + viz_judgment_user.txt — 시각화 유형 판단
- analyzer_viz_svg_system.txt — SVG 시각화 생성
- formatter_system.txt + formatter_user.txt — 조회 결과 보고서 포맷팅

## 메시지 구조 패턴

- interpret/present 계층: system=system.txt 내용, user=user.txt 내용 ({variable} 치환 후)
- reason 계층: system=system.txt 내용 ({variable} 치환), user는 노드마다 다름
- user.txt는 입력 데이터와 요청만 포함해야 함 (역할 정의는 system.txt에만)

## continue_orchestrator 프롬프트 구현 완료 (2026-04-17)

- 파일 경로:
  - `resources/prompts/interpret/continue_orchestrator_system.txt`
  - `resources/prompts/interpret/continue_orchestrator_user.txt`
- 블록 구조: [ROLE], [RULES], [HALLUCINATION_GUARD], [EXAMPLES], [OUTPUT_CONTRACT], [TASK]
- User 템플릿 3개 섹션: [이전 턴 스냅샷], [대화 이력], [사용자 발화]
  - 변수: {turn_snapshots_block}, {conversation_history}, {user_message}
- 출력 스키마 5개 필드: reference_turn_seq, route, continue_hint, updated_intent, reasoning
- route 4가지: rerun / modify / analyze_only / fallback
- 퓨샷 7개 시나리오: rerun(포맷변환), modify(조건추가), analyze_only, fallback(주제이탈), modify(오래된턴), rerun(시각화), fallback(스냅샷없음)
- system_prompts.py 등록: CONTINUE_ORCHESTRATOR_SYSTEM, CONTINUE_ORCHESTRATOR_USER (interpret/ 계층)
- 변수 치환 방식: .format() 단일 중괄호 (기존 패턴과 동일)

## 알려진 이슈

- recovery_agent_system.txt의 응답 형식 JSON이 {{double braces}}로 작성되어 있음 — 버그 가능성, 확인 필요
- analyzer_user.txt에 역할 정의가 포함되어 있음 — system/user 역할 분리 위반
- table_comparison의 selected/rejected 스키마가 knowledge_interpreter와 다름 (단일 문자열 vs 배열)
