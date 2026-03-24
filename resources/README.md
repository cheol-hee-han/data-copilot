# resources/ — 리소스 파일 디렉토리

> 앱이 실행 시 읽는 비-Python 리소스 파일(프롬프트, 도메인 사전, ES 설정, 평가 데이터 등)을 관리합니다.
> 폐쇄망 배포 시 이 디렉토리의 파일만 수정하면 됩니다.
> `.gitignore`에 등록되어 있으므로 이 README를 제외한 파일은 Git에 포함되지 않습니다.

## 로딩 원칙

- `resources/` 에 파일이 **있으면** → 해당 파일의 설정을 사용
- `resources/` 에 파일이 **없으면** → 코드 내장 기본값으로 정상 동작
- `resources/` 디렉토리 자체가 없어도 시스템은 정상 동작합니다

## 디렉토리 구조

```
resources/
├── prompts/                         # 프롬프트 오버라이드 (#3, #12)
│   ├── intent_classification.txt    #   의도 분류
│   ├── clarification.txt            #   명확화 질문
│   ├── sql_generation.txt           #   SQL 생성 (Few-shot 포함)
│   ├── result_formatting.txt        #   결과 포맷팅
│   ├── data_analysis.txt            #   데이터 분석
│   ├── table_enrichment.txt         #   테이블 설명 보강
│   ├── visualization_judgment.txt   #   시각화 판단
│   └── visualization_svg.txt        #   SVG 생성
│
├── domain/                          # 도메인 지식
│   ├── finance_terms.yaml              # (#6)  도메인 용어 사전
│   ├── business_categories.yaml       # (#7)  카테고리→domain_cd
│   ├── similar_tables.yaml          # (#8)  유사 테이블 그룹
│   ├── example_codes.yaml             # (#10) 코드값 한글 매핑
│   └── stopwords.yaml                # (#11) 불용어 목록
│
├── security/                        # 보안 설정
│   └── pii_columns.yaml             # (#9)  PII 컬럼 정의
│
├── visualization/                   # 시각화 설정
│   └── chart_config.yaml            # (#17) 폰트·색상
│
├── elasticsearch/                   # ES 한글 검색
│   ├── user_dictionary.txt          # (#5)  nori 사용자 사전
│   └── synonyms.txt                 # (#5)  동의어 사전
│
├── evaluation/                      # 평가 데이터 (직접 생성)
│   ├── golden_queries.json          # (#13) 골든셋
│   └── test_queries.json            # (#13) 테스트셋
│
├── data/                            # 시딩 데이터 (직접 생성)
│   ├── table_meta.json              # (#16) ES 테이블 메타
│   ├── code_meta.json               # (#16) ES 코드 메타
│   ├── biz_manuals.json             # (#14) 업무 매뉴얼
│   └── sql_history.json             # (#14) SQL 이력
│
└── README.md                        # 이 파일
```

## 기동 환경 (.env)

인프라 접속, LLM 프로바이더 등 환경별 값은 `.env` 파일로 관리합니다.

| 항목 | .env 키 | 대상 # |
|------|---------|--------|
| LLM 프로바이더 | `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_BASE_URL` | #1 |
| 임베딩 모델 | `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `FASTEMBED_CACHE_PATH` | #2, #19 |
| LLM 재시도 | `LLM_PARSE_MAX_RETRY` | #4 |
| LangSmith | `LANGSMITH_ENABLED` | #15 |

`.env.example` 파일을 복사하여 `.env`를 생성한 후 값을 수정하세요.

## 커스터마이징 순서

1. `.env.example` → `.env` 복사 후 인프라 설정
2. `resources/domain/finance_terms.yaml` — 실제 테이블/컬럼/코드값으로 교체
3. `resources/security/pii_columns.yaml` — 실제 PII 컬럼명으로 교체
4. `resources/domain/example_codes.yaml` — 실제 코드 체계로 교체
5. `resources/prompts/sql_generation.txt` — 실제 DB 기반 Few-shot 교체
6. 나머지 파일은 필요에 따라 순차 커스터마이징

## 참고 문서

- `docs/customization-targets.md` — 전체 19개 항목 상세 설명
