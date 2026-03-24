# 기술 리서치 방법론

## 출처 표기 형식

모든 기술적 판단, 수치, 권고안에 반드시 출처를 명시:
- 논문: 저자(연도). "제목". 학회/저널. URL
- 구현사례: 조직명(연도). "제목". URL
- 기술문서: 플랫폼명. "문서 제목". URL (접근일)

## 리서치 수행 절차

### Step 1: 질문 구조화
모호한 목표를 리서치 질문으로 분해.

### Step 2: 계층적 서치 전략
- Tier 1 (최우선): 동료 심사 논문 (arXiv cs.CL/cs.DB, ACL, VLDB, SIGMOD)
- Tier 2: 검증된 기술 문서 (AI 기업 블로그, GitHub stars 500+)
- Tier 3: 보조 자료 (기술 미디어, 컨퍼런스 발표)

### Step 3: 비교 분석
수집 자료를 표 형식으로 구조화 (방식, 정확도, 비용, 구현 복잡도, 한계, 출처).

### Step 4: 프로젝트 특화 인사이트 추출
일반 조사를 이 프로젝트 맥락으로 번역.

## 산출물 형식

```
docs/research/
├── YYYYMMDD-<주제>.md    # 리서치 보고서
└── references.bib        # 참고문헌 (BibTeX)
```

## 품질 자가 점검
- [ ] 모든 수치에 출처가 있는가?
- [ ] Tier 1 출처 최소 3개 포함?
- [ ] 기각된 대안과 이유 명시?
- [ ] 한국어/한국 비즈니스 도메인 특화 내용 포함?
- [ ] 불확실/상충 결과 솔직하게 명시?

## NL-to-SQL 핵심 참고 자료

### 벤치마크 데이터셋
- Spider: Yu et al. (2018). cross-domain text-to-SQL
- BIRD: Li et al. (2023). NeurIPS 2023

### 핵심 방법론 논문
- DIN-SQL (2023): 분해 기반 in-context learning
- DAIL-SQL (2023): 데모 선택 최적화
- MAC-SQL (2024): 멀티 에이전트 협업
- CHESS (2024): 맥락 강화 스키마 링킹

### 실무 구현 가이드
- Defog SQLCoder: https://github.com/defog-ai/sqlcoder
- Vanna.AI: https://github.com/vanna-ai/vanna
