# resources/

런타임에 사용되는 비코드 리소스. Python 코드(`src/`)와 분리하여 관리한다.

## 디렉토리 구조

```
resources/
  prompts/              LLM 프롬프트 (3계층)
  │  interpret/   (10)    질의 해석 — 의도분류, 정규화, 이력해소, 명확화
  │  reason/       (6)    추론 — 탐색, SQL생성, 검증, 재계획
  │  present/      (8)    표현 — 분석, 시각화, 포맷팅
  │
  connectors/           저장소 연결 쿼리 템플릿
  │  elasticsearch/ (3)   ES multi_match 쿼리 body
  │  mongodb/       (4)   MongoDB aggregation 참조 + 초기화
  │
  domain/          (7)  금융 도메인 지식 (용어사전, 동의어, 유사테이블, PII, 차트설정)
  evaluation/      (2)  평가 골든셋 (정확도 측정용)
```

## 설계 원칙

- **코드와 분리**: 프롬프트/쿼리 변경 시 Python 코드 수정 불필요
- **환경별 교체**: 폐쇄망 배포 시 이 디렉토리만 교체하여 소형 모델/다른 DB 대응
- **도메인 전문가 접근**: `src/` 진입 없이 프롬프트/용어 수정 가능
- **폴백 안전성**: 파일이 없어도 코드 내장 기본값으로 동작 (prompts 제외 — 필수)

## 로딩 방식

`src/utils/resource_loader.py`가 이 디렉토리에서 파일을 읽는다:

```python
from src.utils.resource_loader import load_text_required, load_yaml, load_json, load_es_query

prompt = load_text_required("prompts/reason/plan_system.txt")     # 필수 (없으면 에러)
terms = load_yaml("domain/business_dictionary.yaml", default={})   # 선택 (없으면 기본값)
body = load_es_query("connectors/elasticsearch/table_meta_query.json", query)
```

## 커스터마이징 우선순위

1. `.env` — 인프라 접속 정보 (LLM, DB, ES, Qdrant)
2. `domain/business_dictionary.yaml` — 실제 금융 용어로 교체
3. `security/pii_columns.yaml` — 실제 PII 컬럼명으로 교체
4. `prompts/reason/generate_sql_system.txt` — 실제 DB 기반 dialect/few-shot 조정
5. `prompts/present/visualization_svg.txt` — 차트 스타일 커스터마이징

## 참고 문서

- `docs/guides/customization-targets.md` — 전체 커스터마이징 항목 상세
- `docs/guides/migration-guide.md` — 폐쇄망 전환 가이드
