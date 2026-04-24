# fk_graph.json — 테이블 생성 DAG 리포트

생성: 2026-04-22T11:58:57

## 핵심 요약
- 전체 테이블: **1,441**
- 유니크 테이블간 FK 엣지: **1,386**
- FK 컬럼 수: **1,426**
- 의존성 레벨: **8**
- 고립 테이블: **274** (19.0%)
- 순환 참조 테이블: **0**
- Self-reference 컬럼: **3**
- 상호참조 쌍 (deferred): **3**
- Deferred 엣지 (2-pass 시딩): **3**
- 참조 대상 누락: **0** (0개 고유 테이블)

## 🔁 상호참조 쌍 (Deferred FK, 2-pass 시딩 대상)

| 먼저 생성 | 나중에 생성 | 비고 |
|---|---|---|
| `AML002L` | `CSK008L` | 쌍둥이 관계, 2-pass 업데이트 필요 |
| `AML003L` | `CSK009L` | 쌍둥이 관계, 2-pass 업데이트 필요 |
| `PFP001M` | `PFP014M` | 쌍둥이 관계, 2-pass 업데이트 필요 |

## 레벨별 테이블 분포
| level | 테이블 수 | 대표 (최대 8개) |
|---|---|---|
| 0 | 300 | AML005M, AML010M, AML012M, CLN041L, CLN042M, CMG004M, CMI004M, CMI016M, ... (+292) |
| 1 | 79 | CMI001M, CMI026C, CMO003M, CMO008M, CMO010M, CMS002M, CMS006M, DPF022L, ... (+71) |
| 2 | 217 | CLN003M, CLN011M, CLN049M, CLN050M, CMI003M, CMI005M, CMI006H, CMI007M, ... (+209) |
| 3 | 38 | AML014M, CMI002H, CMI008H, CMI009M, CMI010H, CMI011M, CMI012M, CMI013H, ... (+30) |
| 4 | 247 | AML001M, AML002L, AML003L, AML004M, AML006L, AML007M, AML008M, AML009M, ... (+239) |
| 5 | 226 | AML013M, CLN002M, CLN012M, CLN013L, CLN015M, CLN018M, CLN019M, CLN032M, ... (+218) |
| 6 | 301 | CLN004L, CLN006L, CLN007M, CLN008M, CLN009M, CLN010H, CLN014M, CLN016L, ... (+293) |
| 7 | 33 | CLN005L, CLN031L, DPB007L, DPB012L, DPB013L, DPB014L, DPB020H, DPD025L, ... (+25) |

## Top 25 피참조 테이블 (중심 마스터)
| 테이블 | 피참조 수 |
|---|---|
| `CSC001M` | 298 |
| `CMI001M` | 180 |
| `PFP001M` | 97 |
| `DPG001M` | 83 |
| `CMI007M` | 76 |
| `LNB001M` | 72 |
| `CLN002M` | 39 |
| `RPC001M` | 31 |
| `LNM001M` | 27 |
| `LNK001M` | 27 |
| `CLN001M` | 27 |
| `SLE021M` | 27 |
| `LNH001M` | 25 |
| `LNW001M` | 24 |
| `LNO001M` | 22 |
| `DPF001M` | 20 |
| `LNJ001M` | 19 |
| `SLE001L` | 19 |
| `FXR001M` | 19 |
| `DPN001M` | 18 |
| `TRS001M` | 18 |
| `LNG001M` | 16 |
| `DRV001M` | 16 |
| `DPD001M` | 14 |
| `DPY001M` | 14 |

## Top 25 참조 테이블 (FK 많이 가진)
| 테이블 | 외래참조 수 |
|---|---|
| `DPN001M` | 5 |
| `DPG001M` | 4 |
| `DPF001M` | 4 |
| `DPD001M` | 4 |
| `DPD016M` | 4 |
| `DPD021M` | 4 |
| `DPB001M` | 4 |
| `DPY001M` | 4 |
| `LNB001M` | 4 |
| `LNB011L` | 4 |
| `CLN002M` | 4 |
| `CSC009H` | 3 |
| `CSK003M` | 3 |
| `CSK008L` | 3 |
| `CSK009L` | 3 |
| `PFR003M` | 3 |
| `DPG020M` | 3 |
| `DPG022L` | 3 |
| `DPG030L` | 3 |
| `LNB005L` | 3 |
| `LNK001M` | 3 |
| `CLN004L` | 3 |
| `CLN028M` | 3 |
| `CLN033L` | 3 |
| `SLE001L` | 3 |