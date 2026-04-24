# DONE 처리 문서들의 잔여 후속 과제 정리

> 작성일: 2026-04-17
> 목적: `docs/todo/done/`로 이동된 설계 문서들 중, **목적은 달성됐으나** 문서 내에 명시된
> 미구현/미결정 항목 중 **구현할 가치가 있는 것**만 별도 추적한다.
> 신규 후속 작업이 발생하면 본 문서가 아니라 별도 todo 문서로 분리한다.

---

## 1. table-three-aspect-enrichment 잔여
> 출처: `done/20260326-table-three-aspect-enrichment.md` (구현 순서 §7)

- [ ] **유사 테이블 구분 골든셋 케이스 추가** — 3측면 비교 판정 회귀 검증용. 잔액일별/월별, 정상/연체 등 핵심 도메인 페어 5~10건.

---

## 2. context_explorer 배치 해석 잔여
> 출처: `done/20260327-context-explorer-batch-redesign.md` (§6 미결정 + §4 정리 + Step 6)

- [ ] **배치 프롬프트 토큰 상한 대응** — 12+개 테이블 동시 해석 시 토큰 초과 방어.
  - 옵션 A: tool_result 요약(컬럼명만, description 축약)
  - 옵션 B: 테이블 N개 초과 시 2회로 분할 배치
- [ ] **fallback 전략 정착** — 배치 실패 → 개별 LLM → rule-based 순차 시도(옵션 C) 명시화 및 회귀 테스트.
- [ ] **죽은 코드 정리** — 통합으로 불필요해진 함수 제거: `_find_comparison_groups`, `_run_table_comparison`, `_group_by_keyword`, `_group_by_prefix`. fallback 경로용 `_interpret_with_llm`은 유지.
- [ ] **Step 6 검증 시나리오 영구화** — Fast-Path / Cold Start / 배치 LLM 실패 케이스를 자동 회귀 테스트로.

---

## 3. pipeline core improvement 잔여
> 출처: `done/20260327-pipeline-core-improvement.md` (P1/P2)

- [ ] **개선 5 — 도전적 생성 임계 70%** — readiness 70% 이상이면 critical 미확정이어도 GENERATE 허용. 현재 코드 미적용 상태인지 재확인 필요.
- [ ] **개선 6 — replan 실효성 강화**
  - 금지 목록(dead_end 기반) 명시 전달
  - 2회 replan 후 점수 40% 이상이면 GENERATE 강제 진입
- [ ] **P2-7 — batch_interpret 소형 모델 최적화** — JSON 평탄화(nested→flat), few-shot 강화, 조인 경로 추론 별도 프롬프트 분리. Solar Pro 2 70B / Qwen3.5 397B에서 실측 후 결정.

---

## 4. preprocessed_input 분리 잔여
> 출처: `done/20260403-preprocessed-input-redesign.md` (§10 미결사항)

- [ ] **CONTINUE + DATA_ANALYSIS에서 reason 계층 스킵 라우팅** — "이 데이터 분석해줘" 같은 케이스에서 새 SQL이 불필요할 때 sql_generator 스킵 가능 여부 검토(별도 라우팅 이슈).
- [ ] **output_hint 시각화/분석 format 추가 검토** — 현재 SPEC_SHEET/SUMMARY/DETAIL_LIST/REPORT/COMPARISON/NONE에 CHART/ANALYSIS 추가 필요성 판단.
- [ ] **extraction_focus 모델별 품질 검증** — Solar Pro 2 70B / Qwen3.5 397B 환경에서 few-shot 6개의 실제 추출 정확도 측정.
- [ ] **analysis_query 트레이스 기록** — 디버깅용 trace 노출(현재 preprocessed_input만 노출 추정).

---

## 5. qwen35 프롬프팅 스킬 전략 잔여
> 출처: `done/20260410-qwen35-prompting-skill-strategy.md`

- [ ] **SKILL.md 모델별 샘플링 파라미터 권장값 정착** — temperature/top_p/top_k 기본 권장치를 모델별 표로 정리하여 prompt-engineer 스킬에서 즉시 참조 가능하게.
- [ ] **Qwen thinking 모드 ON/OFF 조건 가이드** — 어느 노드에서 thinking을 켤지 룰화(분류/판정 vs 생성/요약).

---

## 6. 스키마 push-down 필터링 잔여
> 출처: `done/20260411-schema-pushdown-filter-design.md` (§열린 질문)

- [ ] **비-FORCED 모드 멀티 DB 동작 회귀 검증** — `target_db_code` 미설정 시 schema_names=None 경로의 readiness_gate 단일 타겟 결정 흐름이 기존과 동일한지 시나리오 테스트.
- [ ] **`restrict_connectors_to_target` 디커플링** — push-down은 `target_db_code`만으로 활성화하고, `restrict_connectors_to_target`은 connect 레이어 전용으로 한정하는 코드 정리.
- [ ] **parse_db_source 외부망 오태깅 해소** — 외부망 `TB_ADW_*`가 `db_source="sybase"`로 잘못 태깅되는 문제. 단기는 push-down으로 가려지지만 `sql_executor.get_query_db` 잘못된 라우팅 가능성 검증 필요. 장기 대안: `TB_DEV_*` 명명 도입.
- [ ] **MongoDB `dpasset_table.schema_name` 인덱스** — 폐쇄망 수만 건 규모 대비 단일 또는 `(schema_name, name)` 복합 인덱스 추가.

---

## 우선순위 메모 (참고)

| 우선 | 항목 | 근거 |
|---|---|---|
| **P0** | §3 개선 6 (replan 강제 생성) | 무한 replan 루프 잔존 위험 |
| **P0** | §6 비-FORCED 모드 회귀 검증 | 멀티 DB 환경 잠재 회귀 |
| **P1** | §1 골든셋, §2 fallback 정착 | 회귀 안전망 |
| **P1** | §4 모델별 품질 검증, §5 샘플링 권장값 | 폐쇄망 모델 전환 대비 |
| **P2** | §2 죽은 코드 정리, §6 인덱스 | 점진적 정리 |
