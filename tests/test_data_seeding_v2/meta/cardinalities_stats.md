# cardinalities.json — 시딩 볼륨 계산 리포트 (prototype)

생성: 2026-04-22T12:32:33
프로파일: **prototype** (scale=0.001)
기준일: 20261221

## 전체 요약
- 테이블: **1,441**
- 총 시딩 레코드: **14,257,023** 행
- 정의된 관계: **38**

## Volume Source 분포
| source | tables | 설명 |
|---|---|---|
| derived_implicit | 1,092 |  |
| fallback | 178 | 최후 기본값 (500) |
| mart_default | 74 | 마트 기본값 |
| log_default | 35 | 로그 기본값 |
| derived_explicit | 26 |  |
| base | 12 | 기준 볼륨 × scale |
| special | 10 | 특수 오버라이드 (코드/조직 등) |
| big_table_cap | 10 | 대용량 상한 적용 |
| history_default | 4 | 이력 테이블 기본값 |

## 레벨별 시딩 볼륨 (시딩 순서)
| level | 테이블 수 | 총 레코드 | 비고 |
|---|---|---|---|
| 0 | 300 | 238,819 | |
| 1 | 79 | 919,891 | |
| 2 | 217 | 101,641 | |
| 3 | 38 | 208,440 | |
| 4 | 247 | 2,715,157 | |
| 5 | 226 | 3,563,639 | |
| 6 | 301 | 2,416,428 | |
| 7 | 33 | 4,093,008 | |

## 도메인별 시딩 볼륨 Top 30
| domain | 테이블 | 레코드 | 비중 |
|---|---|---|---|
| SLE | 70 | 3,991,920 | 28.0% |
| CLN | 50 | 880,420 | 6.2% |
| EBS | 22 | 837,605 | 5.9% |
| DPG | 31 | 822,325 | 5.8% |
| LNB | 41 | 643,220 | 4.5% |
| FXC | 40 | 557,080 | 3.9% |
| EBB | 15 | 516,315 | 3.6% |
| LNH | 30 | 505,638 | 3.5% |
| EBM | 15 | 418,700 | 2.9% |
| DPB | 20 | 408,223 | 2.9% |
| DPD | 30 | 365,741 | 2.6% |
| CMG | 25 | 344,500 | 2.4% |
| FXD | 20 | 332,500 | 2.3% |
| CSK | 15 | 325,000 | 2.3% |
| MKT | 25 | 321,080 | 2.3% |
| CSC | 36 | 311,500 | 2.2% |
| FND | 20 | 198,535 | 1.4% |
| EBO | 15 | 193,000 | 1.4% |
| AML | 15 | 187,750 | 1.3% |
| LNK | 40 | 169,113 | 1.2% |
| CSI | 30 | 163,000 | 1.1% |
| DPF | 25 | 143,091 | 1.0% |
| NBA | 20 | 131,900 | 0.9% |
| FNS | 20 | 130,550 | 0.9% |
| DPY | 20 | 120,791 | 0.8% |
| LNM | 31 | 105,286 | 0.7% |
| LNJ | 20 | 85,575 | 0.6% |
| EBA | 10 | 84,050 | 0.6% |
| FXR | 25 | 83,279 | 0.6% |
| RPD | 20 | 75,882 | 0.5% |

## Top 30 최대 볼륨 테이블
| 테이블 | 볼륨 | source | level | domain |
|---|---|---|---|---|
| `LNH016L` | 428,750 | derived_implicit:LNB012L | 7 | LNH |
| `EBS013L` | 350,000 | derived_implicit:EBS001L | 6 | EBS |
| `CLN005L` | 350,000 | derived_implicit:CLN004L | 7 | CLN |
| `EBB012L` | 226,625 | derived_implicit:EBB003L | 6 | EBB |
| `SLE003L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE004L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE005L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE006L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE007L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE008L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE009L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE010L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE011L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE012L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE014L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE015L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE016L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE017L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE018L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE019L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `SLE020L` | 175,000 | derived_implicit:SLE001L | 7 | SLE |
| `CMG014L` | 122,500 | derived_implicit:CMG001L | 5 | CMG |
| `CSK008L` | 122,500 | derived_implicit:AML002L | 5 | CSK |
| `CSK009L` | 122,500 | derived_implicit:AML003L | 5 | CSK |
| `EBM008L` | 122,500 | derived_implicit:EBM002L | 5 | EBM |
| `MKT021L` | 122,500 | derived_implicit:MKT003L | 5 | MKT |
| `LNB012L` | 122,500 | derived_implicit:LNB011L | 6 | LNB |
| `DPB007L` | 104,248 | derived_implicit:DPB006L | 7 | DPB |
| `EBS001L` | 100,000 | big_table_cap | 5 | EBS |
| `CLN004L` | 100,000 | big_table_cap | 6 | CLN |