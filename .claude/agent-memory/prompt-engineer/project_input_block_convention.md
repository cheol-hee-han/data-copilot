---
name: continue_orchestrator INPUT 블록 재설계 결정
description: Multi-Turn CONTINUE Orchestrator 사용자 프롬프트 INPUT 포맷 확정 — 블록 기호·컬럼 포함·시각화 위치·한글 라벨 규칙
type: project
---

## 결정 사항

continue_orchestrator INPUT 블록 포맷을 `[A]/[B]/[C]` + `[T1]` → `<<A>>/<<B>>/<<C>>` + `T1 ▶` 로 변경.

**Why:** `[TAG]` 단일 대괄호는 시스템 프롬프트 최상위 섹션 기호와 동일해 LLM이 지시/데이터 구분 불가. 이중 꺾쇠 `<<TAG>>`로 데이터 블록 경계 기호를 분리. XML `<>` 단일 꺾쇠도 아님.

**How to apply:** 향후 continue_orchestrator_system.txt EXAMPLES 및 user.txt 포맷 수정 시 이 규칙 준수.

## 확정된 포맷 요약

- 블록 경계: `<<A>>`, `<<B>>`, `<<C>>`
- 턴 헤더: `T1 ▶` (상위 `<<B>>`의 하위임을 들여쓰기로 암시)
- 시각화 위치: `── 시스템 처리 내역 ──` 첫 항목 (대화 subsection에서 이동)
- 테이블: `- TB명(한글명) — 컬럼: col(한글), col(한글), ...` 다줄 리스트
- `<<A>>` 소그룹 순서: 질의 유형/범주 → 판정 근거 → 연속성(참조 턴) → 모호성(있을 때만) → 분석 요건(있을 때만)
- 한글 라벨 + (영문 원값) 병기: `질의 유형: 데이터 추출 (data_extraction)`

## 관련 파일

- `resources/prompts/interpret/continue_orchestrator_user.txt` — placeholder 이름 유지, 채워지는 포맷만 교체
- `resources/prompts/interpret/continue_orchestrator_system.txt` — EXAMPLES 섹션 7개 예시 일괄 교체 필요
- `docs/todo/20260416-multi-turn-continue-orchestrator-design.md` §3.2.1 — 설계문서 개정 포인트 확인됨 (2026-04-18)
