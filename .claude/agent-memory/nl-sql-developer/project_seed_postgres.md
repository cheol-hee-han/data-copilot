---
name: seed_postgres 전면 재작성 완료
description: standalone/scripts/seed_postgres.py의 신규 명명규칙 적용 및 572개 테이블 DDL 자동생성 구조
type: project
---

seed_postgres.py를 전면 재작성하여 신규 TB_ADW_* 명명규칙을 적용했다.

**Why:** 기존 파일은 구 명명규칙(TB_CUST_INFO 등)을 사용했고, requirements doc 섹션 5의 572개 테이블 카탈로그를 반영하지 않았다.

**How to apply:** 이 파일을 수정할 때는 아래 구조를 유지해야 한다.
- `parse_table_catalog()`: requirements doc 마크다운을 런타임 파싱하여 테이블 목록 추출
- `STAR_DDL`: 22개 ★ 테이블의 상세 DDL (딕셔너리)
- `_build_ddl()`: 비-★ 테이블 자동 DDL 생성 (PK_TYPE_MAP + _TYPE_EXTRA_COLS)
- PK_TYPE_MAP 딕셔너리 줄은 `# noqa: E501` 처리됨 (79자 초과 불가피)

**★ 테이블 22개:**
COM001M, COM002M, CSC101M, CSC102H, CSP103M,
DEP201P, DEP202S, LNB301M, LNB302M, CRD401M,
FXD501L, FXB502M, FND601P, FND602P, TRX701L,
INS803M, PNB904P, RSK1101M, MKT1201M, MKT1202M,
FIN1306S, WMB1401M

**TYPE 불완전성 재현 위치:**
- TYPE-2: CUS_GRD_CD(99/NULL), ACT_DCD(05/99), LN_STCD(0A), OVDU_GRD_CD(F/Z), CRD_DCD(04), TR_DCD(200~299/999), FX_DL_DCD(06)/CCY_CD(CNH), INS_DCD(E), PN_DCD(HYB), INVEST_PRFL_CD(0)
- TYPE-4: BAL_AMT vs TOT_BAL_AMT, CUS_GRD_CD vs MKT_GRD_CD, LN_EXC_AMT vs LN_APR_AMT, JOIN_DT vs RGST_DT, BAL_AMT vs EVAL_AMT
