---
name: finance_terms_status
description: src/services/domain/finance_terms.py 현황 — 카테고리 구성, 용어 수, 함수 목록
type: project
---

도메인 사전 파일: src/services/domain/finance_terms.py

**Why:** 금융 전문 용어와 DB 스키마 매핑을 중앙 관리하여 SQL 생성 LLM이 정확한 테이블/컬럼/조건을 참조하도록 함.

**How to apply:** 신규 용어 추가 시 이 파일에 DomainTerm 항목을 추가하고 카테고리를 준수할 것.

## 카테고리 구성 (9개)

| 카테고리 | 주요 내용 |
|---------|----------|
| 고객     | 신규/개인/기업/VIP/휴면/탈퇴 고객 |
| 여신     | 대출 유형, 자산건전성(5단계), 연체등급, 충당금, 만기 |
| 수신     | 요구불/저축성, MMDA, CD, RP, 금리, 만기 |
| 거래     | 입금/출금/이체 |
| 카드     | 신용/체크카드, 이용금액/건수, 할부, 카드론 |
| 외환     | 환율, 외화예금, 해외송금, 환전 |
| 금융지표 | NIM, BIS비율, LCR, ROA, ROE, 연체율, NPL비율, 예대율 |
| 조직     | 지점/영업점 |
| 시간     | 당월/전월, 분기, 반기, 전년동기/동월, 직전영업일, 회계연도 등 |

## 등록 용어 수

2026-03-18 기준 약 80개 용어 등록 (기존 30개 + 보강 50개)

## 공개 함수 목록

- lookup_terms(query) → list[DomainTerm]: 자연어에서 매칭 용어 검색
- get_terms_by_category(category) → list[DomainTerm]: 카테고리별 조회
- get_all_categories() → list[str]: 전체 카테고리 목록
- format_domain_context(terms) → str: 단일 블록 프롬프트 포맷
- format_domain_context_grouped(terms) → str: 카테고리별 그룹 프롬프트 포맷

## 코딩 규칙

- 줄 길이 79자 이하 (E501 준수) — condition 문자열은 괄호+문자열 연결로 분리
- 복잡한 포맷 로직은 헬퍼 함수(_group_terms_by_category, _format_single_term_grouped)로 분리 (S3776 준수)
- 향후 추가될 테이블(카드/외환/펀드)은 table_name에 예상 테이블명 기재 후 확인 필요
