# recovery_agent 출력 스키마 scaffolding 적용 검토

## 배경

SKILL.md §5.4 "출력 스키마를 통한 사고 scaffolding" 원칙이 신설됨. thinking ON 노드의
"깊은 재해석" 실패를 출력 JSON 필드 분할로 강제하는 기법.

analyzer 재작업에서 `initial_reading` / `insights` 두 필드로 "표면 수치 → 숨은 패턴"의
2단계 사고를 강제하는 방식이 도입되었음. §5.4 작성 시 recovery_agent에도
`surface_symptom` / `root_cause_diagnosis` 분할 적용 가능성을 가설로 언급했으나,
**즉시 적용하지 않고 별도 검토를 남기기로** 결정.

## 왜 즉시 적용하지 않는가

- recovery_agent는 analyzer와 달리 이미 복잡한 출력 스키마(action, execution_plan,
  new_hypothesis, failure_reasons 등)를 갖고 있음. 필드 추가는 파서·그래프 라우팅까지
  영향.
- "표면 증상 vs 근본 원인"이 실패 유형별로 얼마나 분리 가능한지 경험적으로 검증 안 됨.
  컬럼 미확인·코드값 불일치 같은 단순 실패에서는 두 필드가 거의 같은 내용이 되어
  오히려 scaffolding이 무의미할 수 있음.
- reasoning_summary(§5.2)가 이미 사후 검증용으로 들어가 있어 "깊이 부족" 증거가
  analyzer만큼 명확하지 않음.

## 적용 검토 시 확인할 것

1. **실패 케이스 현황 수집**: recovery_agent 출력 로그에서 "같은 실패 원인을 반복
   제시"하거나 "표면 해결책만 제안"한 사례가 실제로 관측되는가? 몇 %?
2. **실패 유형별 분리 유효성**: 각 failure_type에 대해 surface_symptom /
   root_cause_diagnosis가 분리된 정보를 담을 수 있는지 3~5개 실제 케이스로 검토.
3. **기존 필드와의 충돌**: failure_reasons·assumptions와 의미 중복 없는지 확인.
   필드 수 증가가 파싱 안정성에 미치는 영향.
4. **분리 강제의 부작용**: 단순 실패에서도 억지로 두 단계로 쪼개면 오히려 hallucination을
   유도하지 않는가(§5.4 "의미 차이 없는 필드 분리 금지" 원칙).
5. **그래프 라우팅 영향**: root_cause_diagnosis를 기반으로 replan 분기 조건을 더
   섬세하게 만들 수 있는가? 아니면 단순 기록용에 그치는가?

## 작업 항목 (적용 결정 시)

- [ ] recovery_agent 실제 출력 로그에서 깊이 부족 패턴 수집·정량화
- [ ] 실패 유형별 두 필드 분리 예시 3~5개 수동 작성
- [ ] `resources/prompts/reason/recovery_agent_system.txt` 스키마 확장
- [ ] `recovery_agent` 노드 응답 파서 확장
- [ ] 그래프 라우팅 조건 재검토 (root_cause 기반 세분화 여부)
- [ ] 골든셋 회귀 테스트로 분리 전후 성능 비교

## 참고

- `.claude/skills/prompt-engineer/SKILL.md` §5.4
- `resources/prompts/present/analyzer_system.txt` (scaffolding 적용 레퍼런스)
- `resources/prompts/reason/recovery_agent_system.txt` (대상 파일)
