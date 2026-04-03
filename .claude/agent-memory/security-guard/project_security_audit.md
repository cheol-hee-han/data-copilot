---
name: 2026-03-18 보안 감사 결과
description: Data Copilot 금융 AI 에이전트 보안 감사 — 발견 취약점 11건 전건 수정 완료
type: project
---

2026-03-18 보안 감사 수행 및 코드 수정 완료.

**Why:** 은행 금융 데이터를 다루는 NL-to-SQL 파이프라인 특성상 SQL 인젝션·프롬프트 인젝션·PII 노출이 최우선 위협임.

**How to apply:** 추가 기능 개발 시 아래 완료 항목이 퇴보하지 않도록 확인.

## 수정 완료 항목

### src/utils/security.py
- 프롬프트 인젝션 패턴 9개 → 34개 (한국어 9개, 간접 인젝션 5개, 영어 확장 11개 추가)
- `_normalize_unicode()` 함수 추가 — NFKC 정규화로 전각 문자 우회 방어
- 계좌번호 정규식 정밀화 — 하이픈 포함 형식만 매칭하여 오탐 감소
- `_make_masked()` 개선 — 구분자(하이픈·공백) 보존, 숫자만 마스킹
- `validate_sql_safety()` — WITH(CTE) 허용, 시간지연·파일I/O·주석 패턴 추가

### src/agents/nodes/interpret/preprocessor.py
- SQL 인젝션 패턴 4개 → 13개 (블록주석, 서브쿼리 내 DML, 시간지연, 파일I/O 등)
- `--` 패턴을 줄끝 한정에서 위치 무관으로 수정
- `detect_prompt_injection()` 호출 추가 (기존 누락)
- `_normalize_unicode()` 선처리 추가
- 로그 기록 시 `mask_pii()` 적용

### src/agents/nodes/sql_validator.py
- FORBIDDEN_PATTERNS 5개 → 21개 (시간지연 4종, 파일I/O 4종, 주석 2종, UNION, xp_, CALL 추가)
- WITH(CTE) 쿼리 허용 (기존 SELECT만 허용 → SELECT 또는 WITH)
- PII_COLUMNS 9개 → 18개 (계좌번호 5종, 주민번호 변형, 외국인등록번호 추가)
- MASKING_COLUMNS 8개 → 15개 (CUST_NM 등 추가)
- `_normalize_unicode()` 적용 후 검증 실행
- `_MSG_TIME_DELAY` 상수로 중복 문자열 제거

### src/main.py
- `_is_valid_session_id()` 추가 — `^[a-zA-Z0-9_\-]{1,128}$` 패턴 검증
- WebSocket 연결 시 session_id 검증, 실패 시 code=1008로 즉시 종료
- REST API session_id도 동일 검증 적용
- 세션 저장 시 `mask_pii()` 적용 (평문 PII 메모리 보관 방지)
- 미사용 import 제거 (StaticFiles, field_validator)

## 잔존 위험 (미수정)
- Rate Limiting 미적용 (DoS 위험)
- WebSocket이 ws:// — 프로덕션에서 wss:// 필요
- 사용자 인증 미적용
- PII_COLUMNS 목록이 sql_validator.py와 security.py에 분산

## 감사 문서 위치
`docs/reviews/design/20260321-security-audit.md`
