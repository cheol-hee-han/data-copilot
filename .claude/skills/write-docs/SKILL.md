---
name: write-docs
description: |
  개발·운영·사용자 문서를 일관된 형식으로 작성하고 최신 상태로 유지합니다.
  신규 기능 문서화, 마일스톤 완료 후 정리, 운영 가이드 작성 시 사용하세요.
user_invocable: true
---
# 역할

테크니컬 라이터. 개발자, 운영자, 비즈니스 사용자 각각을 위한 맞춤 문서를 작성.

# 사용법

```
/write-docs                                                           # 프로젝트 전체의 문서 정비
/write-docs docs/architecture/                                        # 디렉토리 전체 점검·정비
/write-docs docs/architecture/loan_architecture.md                    # 특정 파일 점검·정비
/write-docs docs/architecture/ docs/architecture/loan_architecture.md # 디렉토리 하위 전체와 특정 파일 점검·정비
/write-docs --check-only                                              # 정비 없이 부족한 곳만 리스트업
/write-docs --this                                                    # 현재 작업 중인 파일에 대해서만 점검·정비
```

# 핵심 원칙

- `docs/agent-guides/documentation-guide.md`의 의 문서 작성 원칙과 스타일 가이드 준수
- 대상 독자에 맞는 기술 수준으로 작성 (사용자 가이드는 비기술적, API 문서는 정확하게)
- 설계문서는 아키텍처 수준에서 구현이 변경되면 즉시 최신화
- 구성도는 가독성을 위해 되도록 Mermaid로 작성, 필요 시 ASCII 구성도도 병행하여 제공
- ASCII 구성도를 작성할 때 영문/숫자/특수문자는 반각 처리하고, 한글은 전각 처리하여 문자 폭 맞추기

# 문서 구조

**반드시 `docs/architecture/project-structure.md`의 "문서 (docs/)" 섹션을 정본(source of truth)으로 참조한다.**

| 디렉토리 | 용도 | 네이밍 규칙 |
| ---------- | ------ | ------------ |
| `agent-guides/` | AI 서브에이전트 참조 지침서·체크리스트·포맷 명세 | `{주제}.md` |
| `architecture/` | 전체적인 프로젝틍 아키텍처 설계 | `{주제}.md` |
| `data-generation-rules/` | 테스트 데이터 생성 시 불완전성·분포·품질 규칙 | `{NN}-{주제}.md` |
| `reviews/code/` | 코드 리뷰 보고서 | `YYYYMMDD-{주제}.md` |
| `reviews/design/` | 설계 리뷰, 보안 감사 등 시점별 평가 기록 | `YYYYMMDD-{주제}.md` |
| `design/` | 기능 설계 문서 | `{주제}.md` |
| `guides/` | 개발자·운영자 대상 실행 가이드 (환경 구성, 배포, 운영) | `{주제}-guide.md` |
| `strategy-proposals/` | 현재 설계의 문제 분석 + 구체적 개선안 제안서 | `{주제}-strategy.md` |

- 문서를 신규 생성하거나 이동할 때 위 디렉토리 분류에 맞는 위치에 배치한다
- 전체 프로젝트를 봤을 때, 프로젝트 산출물이 문서구조와 일치하지 않는 곳에 문서가 있으면 구조에 맞게 `docs/` 하위로 이동시키고 링크 업데이트
- **문서 생성·이동·삭제 후 반드시 `docs/architecture/project-structure.md`를 최신 상태로 갱신한다**


# 작업 절차

1. 문서화 대상 코드/기능을 Read로 먼저 확인
2. 기존 문서가 있으면 업데이트, 없으면 새로 생성
3. 관련 에이전트 산출물(설계서, 스키마 문서 등)을 참조하여 정합성 유지

# 문서 경로 변경 시 참조 동기화 절차

문서를 생성·이동·삭제·이름 변경한 경우 반드시 아래 절차를 수행한다:

1. **`docs/architecture/project-structure.md` 갱신** — 트리 구조와 디렉토리 설명 테이블을 최신 상태로 업데이트 (소스코드와 문서 모두 반영, 디렉토리뿐 아니라 실제 파일까지 구조에 포함하고 주석까지 최신화)
2. **프로젝트 내 깨진 참조 탐색 및 수정** — 변경된 경로를 Grep으로 검색하여 참조하는 모든 파일을 갱신
   ```
   # 이동/삭제된 파일의 기존 경로로 검색
   Grep: pattern="docs/<이전경로>" path=".claude/"
   Grep: pattern="docs/<이전경로>" path="docs/"
   Grep: pattern="docs/<이전경로>" path="src/"
   ``

3. **주요 참조 파일 목록** — 아래 파일들은 `docs/` 경로를 직접 참조하므로 우선 확인:
   - `.claude/CLAUDE.md` — 프로젝트 개요의 참조 문서 링크
   - `.claude/agents/*.md` — 에이전트 정의의 참조 문서 링크
   - `.claude/skills/*/SKILL.md` — 스킬 정의의 참조 문서 링크
   - `.claude/agent-memory/**/*.md` — 에이전트 메모리의 문서 경로
   - `docs/` 내 다른 문서의 상호 참조 링크
