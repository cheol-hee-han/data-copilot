# 프롬프트 포맷 통일 및 개선 잔여 작업

> 작성일: 2026-04-02
> 관련 작업: 프롬프트 파일 전수 검토 및 포맷 통일

## 완료된 작업 요약

| 파일 | 적용 내용 |
|------|-----------|
| `interpret/intent_classifier_system.txt` | `━━━` → `##`, `✓` → `-`, `■` 제거, 조건부 JSON 스키마 → 통합 스키마 |
| `interpret/query_normalizer_phase1_system.txt` | `━━━[슬롯 N]━━━` → `## 슬롯 N`, 섹션 제목 `[...]` → `##` |
| `interpret/query_normalizer_phase2_system.txt` | `━━━[...]━━━` → `##` |
| `present/analyzer_system.txt` | `[규칙]` → `## 규칙` 등 섹션 제목 통일 |
| `present/formatter_system.txt` | `[규칙]` → `## 규칙` 등 섹션 제목 통일 |
| `present/analyzer_viz_judgment_system.txt` | `━━━[...]━━━` → `##` |
| `present/analyzer_user.txt` | 충돌하는 3필드 출력 스키마 제거 (system의 4필드 스키마로 통합) |
| `reason/planner_system.txt` | 도구명 수정: `search_report_sql` 제거, `get_sample_data` → `get_sample_rows`, `get_date_distribution` 추가 |
| `reason/context_interpreter_system.txt` | 파일명 변경 (구 `context_explorer_batch_interpret.txt`) |
| `reason/미사용_recovery_planner_system.txt` | 미사용 접두사 추가 |
| `interpret/미사용_clarifier_system.txt` | 미사용 접두사 추가 |
| `interpret/미사용_clarifier_user.txt` | 미사용 접두사 추가 |

---

## 잔여 작업

### 1. `present/analyzer_viz_svg_system.txt` — 포맷 통일 미적용

6개 섹션의 `━━━[...]━━━` 패턴을 `## ...`로 변환 필요.

| 라인 | 현재 | 변경 후 |
|------|------|---------|
| 4-6 | `━━━[절대 규칙]━━━` | `## 절대 규칙` |
| 16-18 | `━━━[레이아웃 시스템]━━━` | `## 레이아웃 시스템` |
| 29-31 | `━━━[색상 시스템]━━━` | `## 색상 시스템` |
| 46-48 | `━━━[스케일링 규칙]━━━` | `## 스케일링 규칙` |
| 65-67 | `━━━[지원하는 시각화 유형]━━━` | `## 지원하는 시각화 유형` |
| 98-100 | `━━━[스타일 세부 규칙]━━━` | `## 스타일 세부 규칙` |

### 2. `interpret/query_normalizer_phase1_system.txt` — 특수문자 잔존

- **□ (체크박스)**: 154, 158, 161, 163번 라인의 `□` → `-`로 변환
- **■ (블릿)**: 258, 298, 341, 396번 라인의 `■ 예제 N:` → `### 예제 N:` 로 변환
- **━━━ (구분선)**: 437, 439번 라인에 잔존하는 `━━━` 구분선 제거 또는 `##`로 변환

### 3. `present/analyzer_viz_judgment_system.txt` — □ 잔존

- 70-72번 라인의 `□` 체크박스 → `-`로 변환

### 4. Reason 계층 프롬프트 포맷 검증

`reason/` 하위 활성 프롬프트들이 `##` 기반 포맷을 이미 사용하는지 전수 확인 필요:
- `planner_system.txt`
- `planner_user.txt`
- `context_interpreter_system.txt`
- `sql_generator_system.txt`
- `sql_generator_user.txt`
- `sql_validator_system.txt`
- `sql_validator_user.txt`
- `recovery_agent_system.txt`
- `recovery_agent_user.txt`

### 5. intent_classifier_system.txt — Few-shot 예제 정합성

통합 스키마로 변경했으나, Few-shot 예제들이 이전 조건부 스키마 형태의 출력을 보여줌.
통합 스키마에 맞게 Few-shot 출력 예제도 정렬 필요 (예: `clarification` 필드가 불필요한 케이스에서 `null`로 명시).

### 6. query_normalizer_phase1 — 8K 컨텍스트 대응 경량화 검토

현재 phase1 프롬프트는 매우 길어서 (400+ 라인) 폐쇄망 8K 모델에서 컨텍스트 압박 가능성 있음.
검토 사항:
- 슬롯별 설명 축약 가능 여부
- Few-shot 예제 수 조정 (4개 → 2개)
- phase1/phase2를 단일 패스로 합치는 방안 (소형 모델에서 2패스가 오히려 비효율적일 수 있음)

### 7. 미사용 프롬프트 정리 확인

현재 `미사용_` 접두사 부여 완료 파일:
- `interpret/미사용_clarifier_system.txt`
- `interpret/미사용_clarifier_user.txt`
- `interpret/미사용_history_resolver_system.txt`
- `interpret/미사용_intent_classifier_system.txt`
- `reason/미사용_recovery_planner_system.txt`

`system_prompts.py`에서 이 파일들을 참조하지 않는지 재확인 필요.
장기적으로 별도 `_deprecated/` 디렉토리로 이동하거나 삭제 검토.

---

## 포맷 통일 규칙 요약 (적용 기준)

| 용도 | 기존 | 통일 후 |
|------|------|---------|
| 시스템 프롬프트 구조 섹션 | `━━━[제목]━━━`, `[제목]` | `## 제목` |
| 하위 섹션 | `[하위 제목]` | `### 하위 제목` |
| 유저 프롬프트 데이터 필드 라벨 | `[사용자 요청]` | `[사용자 요청]` (유지) |
| 체크리스트 항목 | `✓`, `□` | `-` |
| 예제 번호 블릿 | `■ 예제 N:` | `### 예제 N:` |
| 구분선 | `━━━...━━━` | 제거 (## 제목으로 대체) |
