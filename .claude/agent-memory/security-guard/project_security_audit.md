---
name: 2026-04-17 보안 감사 결과
description: Data Copilot 금융 AI 에이전트 보안 감사 — 폐쇄망 배포 전 전수 점검
type: project
---

# 2026-04-17 보안 감사

2026-03-18 이후 변경분 포함 2026-04-17 재감사 수행.

**Why:** 폐쇄망 배포 전 최종 보안 점검. 이전 감사 수정 완료 항목 퇴보 여부 및 신규 이슈 확인.

**How to apply:** 추가 기능 개발 시 아래 잔존 위험 항목이 퇴보하지 않도록 확인.

## 이전 감사(2026-03-18) 수정 항목 — 퇴보 없음 확인

- 프롬프트 인젝션 패턴 82개 유지 (한국어·간접·영어 확장)
- normalize_unicode() 선처리 — 전각 우회 차단 유지
- FORBIDDEN_SQL_PATTERNS 공유 상수 (security.py ↔ sql_safety_checker.py)
- session_id 검증 `^[a-zA-Z0-9_\-]{1,128}$` 유지 (WebSocket + REST)
- PII 컬럼 18개 (sql_safety_checker) + 마스킹 컬럼 15개 유지
- 글로벌 예외 핸들러 — 내부 정보 미노출 유지

## 신규 확인 취약점 (2026-04-17)

### Critical

(없음)

### High

1. result_data(SQL 결과 행) PII 마스킹 미적용 — formatter.py의 `_build_result_data` → stream.end result_data, /api/download 로 전달. 응답 텍스트(masked_response)만 mask_pii() 적용, rows dict 자체는 미적용. 주민번호 등 PII 컬럼은 sql_safety_checker에서 SQL 생성 시 차단하나 컬럼명 변형·별칭(ALIAS) 우회 가능성 존재

### Medium

2. CORS allow_origins=["*"] — 운영 배포 시 구체 도메인으로 교체 필요 (주석으로 명시되어 있으나 미조치)
3. Content-Security-Policy(CSP) / Strict-Transport-Security(HSTS) 헤더 미설정 — SecurityHeadersMiddleware에 X-Content-Type-Options·X-Frame-Options·Referrer-Policy만 있음
4. Rate Limiting 미적용 — WebSocket·REST API 모두 DoS 위험
5. 사용자 인증(Authentication) 미구현 — 모든 엔드포인트 무인증 접근 가능
6. /api/sessions·/api/messages 등 세션 라우터에 session_id 형식 검증 일부 미적용 (get_session, delete_session, toggle_like, mark_download 파라미터 미검증)
7. /api/download — 다운로드 데이터(rows)에 PII 마스킹 미적용

### Low

8. WebSocket ws:// — 폐쇄망 배포 시 wss:// 필수 (nginx TLS 종단 처리 필요)
9. pii_masking_enabled=False 설정 가능 — 운영 시 False 설정 방지 가이드 필요
10. eval_trace JSON — SQL 실행 결과 샘플(rows[0]) 포함, PII 포함 가능성

## 잔존 위험 (이전 감사부터 지속)

- Rate Limiting 미적용 (Medium으로 유지)
- WebSocket wss:// 미설정 (Low)
- 사용자 인증 미적용 (Medium)
- PII_COLUMNS 목록 분산 (sql_validator.py vs security.py) — pii_columns.yaml 통합으로 개선

## 감사 문서 위치

에이전트 텍스트 출력으로 전달됨 (docs/security/ 별도 파일 없음)
