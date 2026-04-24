#!/usr/bin/env python3
"""
build_verification_sql.py
=========================
business_rules.json + cardinalities.json + fk_graph.json을 활용해
시딩 후 검증용 SQL 쿼리 세트를 자동 생성한다.

산출물:
  - meta/verification_queries.sql  : 실행 가능한 SQL (PostgreSQL)
  - meta/verification_index.md     : 쿼리별 설명·예상 결과
"""
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BANK_DIR = Path("/home/claude/bank_v2")
META_DIR = BANK_DIR / "meta"
RULES_PATH = META_DIR / "business_rules.json"
CARD_PATH = META_DIR / "cardinalities.json"
GRAPH_PATH = META_DIR / "fk_graph.json"
CATALOG_PATH = META_DIR / "catalog_v2.json"
SQL_OUT = META_DIR / "verification_queries.sql"
INDEX_OUT = META_DIR / "verification_index.md"


def sql_header():
    return """-- ============================================================
-- 시딩 후 정합성 검증 쿼리 세트 (자동 생성)
-- 생성: {ts}
-- 
-- 실행 방식: 
--   psql -h <host> -U <user> -d bank_v2 -f verification_queries.sql > report.txt
--
-- 모든 쿼리는 violation 행 수를 반환 (0이면 정상)
-- hard severity 규칙은 반드시 0이어야 함
-- soft severity 규칙은 권장 허용치 내여야 함
-- ============================================================

SET search_path TO adw_v2;

""".format(ts=datetime.now().isoformat(timespec='seconds'))


def sec_header(title, comment=""):
    return f"\n-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n-- {title}\n{'-- ' + comment if comment else ''}\n-- ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"


def gen_basic_checks(cardinalities, graph):
    """1. 기본 검증: 행 수, 스키마"""
    s = sec_header("1. 기본 검증 (행 수 · 테이블 존재)")

    s += """
-- 1.1 전체 테이블 행 수 vs cardinalities.json 기대치
SELECT 'TABLE_ROW_COUNT' AS check_name, 
       schemaname, relname AS table_name, n_live_tup AS actual_rows
FROM pg_stat_user_tables
WHERE schemaname = 'adw_v2'
ORDER BY n_live_tup DESC
LIMIT 30;

-- 1.2 비어있는 테이블 (볼륨 0 또는 시딩 실패)
SELECT 'EMPTY_TABLE' AS check_name, relname AS table_name
FROM pg_stat_user_tables
WHERE schemaname = 'adw_v2' AND n_live_tup = 0
ORDER BY relname;

-- 1.3 볼륨 편차 검증 (expected vs actual > 20%)
-- (수동 비교 필요 - cardinalities.json의 volume 참조)

"""
    return s


def gen_fk_integrity(graph, catalog):
    """2. FK 참조 무결성 (DAG의 edge 전부 검증)"""
    s = sec_header("2. FK 참조 무결성 (fk_graph.json 엣지 기반)")

    # 테이블별 PK 맵 (fk target 컬럼을 알기 위해)
    pk_of = {}
    for t in catalog['tables']:
        short = t['table_id'].replace('TB_ADW_', '')
        pks = [c['name'] for c in t['columns'] if c.get('pk')]
        pk_of[short] = pks

    # 대표적인 FK 관계 Top 20만 (너무 많으면 SQL 거대)
    fk_edges = graph['edges']
    # 참조 테이블별로 그룹
    by_parent = defaultdict(list)
    for e in fk_edges:
        by_parent[e['to']].append(e)

    # Top 20 부모 (피참조 많은 순)
    top_parents = sorted(by_parent.keys(), key=lambda t: -len(by_parent[t]))[:20]

    s += "\n-- 2.1 핵심 마스터별 FK 참조 무결성 (orphan 찾기)\n"
    for parent in top_parents:
        edges = by_parent[parent][:5]  # 부모당 최대 5개 자식
        parent_pk_col = edges[0].get('via_ref_column') or (pk_of.get(parent) or ['?'])[0]
        for e in edges:
            child = e['from']
            col = e['via_column']
            s += f"""
-- {child}.{col} → {parent}.{parent_pk_col}
SELECT 'FK_ORPHAN_{child}_{col}' AS check_name, COUNT(*) AS violations
FROM {child} c
WHERE c.{col} IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.{parent_pk_col} = c.{col});
"""

    s += "\n-- 2.2 Deferred FK 2-pass 완료 여부 확인\n"
    for de in graph.get('deferred_edges', []):
        s += f"""
-- {de['from']}.{de['via_column']} → {de['to']}
SELECT 'DEFERRED_FK_{de["from"]}_{de["via_column"]}' AS check_name,
       COUNT(*) FILTER (WHERE {de['via_column']} IS NULL) AS null_count,
       COUNT(*) AS total
FROM {de['from']};
"""

    return s


def gen_date_order_checks(rules):
    """3. 날짜 순서 제약"""
    s = sec_header("3. 날짜 순서 제약 (date_order)")
    date_rules = [r for r in rules['rules'] if r['category'] == 'date_order']
    for r in date_rules:
        if 'table' not in r:
            continue
        tbl = r['table']
        expr = r.get('expr', '')
        cond = r.get('condition', '')
        if not expr:
            continue
        # SQL 변환: expr이 "A <= B" 형태
        neg_expr = expr.replace('<=', '>').replace('<', '>=').replace('>=', '<').replace('>', '<=')
        # 간단 변환이라 정확하진 않지만 기본 패턴 커버
        # 더 안전한 방식: NOT (expr)
        where = f"NOT ({expr})"
        if cond:
            where = f"({cond}) AND NOT ({expr})"
        s += f"""
-- {r['rule_id']}: {r.get('note', '')}
SELECT '{r["rule_id"]}' AS check_name, COUNT(*) AS violations
FROM {tbl}
WHERE {where};
"""
    return s


def gen_amount_checks(rules):
    """4. 금액 관계 제약"""
    s = sec_header("4. 금액 범위·관계 제약 (amount)")
    amt_rules = [r for r in rules['rules'] if r['category'] == 'amount']
    for r in amt_rules:
        expr = r.get('expr', '')
        cond = r.get('condition', '')
        if not expr:
            continue

        if 'cross_table' in r:
            # 크로스 테이블 검증
            tbl = r.get('table', '')
            ct = r['cross_table']
            jk = r.get('join_key', '?')
            s += f"""
-- {r['rule_id']}: {r.get('note', '')} [cross-table]
SELECT '{r["rule_id"]}' AS check_name, COUNT(*) AS violations
FROM {tbl} a JOIN {ct} b USING ({jk})
WHERE NOT ({expr});
"""
        else:
            tbl = r.get('table', '')
            if not tbl:
                continue
            where = f"NOT ({expr})"
            if cond:
                where = f"({cond}) AND NOT ({expr})"
            s += f"""
-- {r['rule_id']}: {r.get('note', '')}
SELECT '{r["rule_id"]}' AS check_name, COUNT(*) AS violations
FROM {tbl}
WHERE {where};
"""
    return s


def gen_status_checks(rules):
    """5. 상태 전이 제약"""
    s = sec_header("5. 상태 전이 제약 (status)")
    stat_rules = [r for r in rules['rules'] if r['category'] == 'status']
    for r in stat_rules:
        tbl = r.get('table', '')
        cond = r.get('condition', '')
        req = r.get('requires', '')
        if not all([tbl, cond, req]):
            continue
        s += f"""
-- {r['rule_id']}: {r.get('note', '')}
SELECT '{r["rule_id"]}' AS check_name, COUNT(*) AS violations
FROM {tbl}
WHERE ({cond}) AND NOT ({req});
"""
    return s


def gen_cross_table_checks(rules):
    """6. 크로스 테이블 정합성"""
    s = sec_header("6. 크로스 테이블 정합성 (cross_table)")
    ct_rules = [r for r in rules['rules'] if r['category'] == 'cross_table']
    for r in ct_rules:
        s += f"""
-- {r['rule_id']}: {r.get('note', '')}
-- Tables: {', '.join(r.get('tables', []))}
-- Expected: {r.get('expr', '')}
-- [MANUAL VERIFICATION - 복잡한 집계 로직 필요]
-- 예시 쿼리를 각 관계별로 수동 작성 필요.
"""
    # 주요 크로스 테이블 쿼리는 수동 작성
    s += """
-- 6.1 정기예금 잔액 정합성 (DPF001M vs DPF003P)
SELECT 'DPF_BAL_CONSISTENCY' AS check_name, COUNT(*) AS violations
FROM DPF001M d
WHERE d.BAL != COALESCE((
    SELECT BAL FROM DPF003P p
    WHERE p.ACN = d.ACN
    ORDER BY BASE_YMD DESC LIMIT 1
), 0);

-- 6.2 STR AML 이관 정합성
SELECT 'STR_AML_CONSISTENCY' AS check_name, COUNT(*) AS violations
FROM CSK008L c
WHERE c.ESCL_AML_YN = 'Y'
  AND NOT EXISTS (SELECT 1 FROM AML002L a WHERE a.SRC_CSK_STR_NO = c.STR_NO);

-- 6.3 CTR AML 이관 정합성
SELECT 'CTR_AML_CONSISTENCY' AS check_name, COUNT(*) AS violations
FROM CSK009L c
WHERE c.ESCL_AML_YN = 'Y'
  AND NOT EXISTS (SELECT 1 FROM AML003L a WHERE a.SRC_CSK_CTR_NO = c.CTR_NO);

-- 6.4 부점 수신 마트 vs 원장 합
SELECT 'MVP_BAL_CONSISTENCY' AS check_name, COUNT(*) AS violations
FROM MVP001S m
JOIN (
    SELECT MNGM_BRCD, SUM(BAL) AS total_bal
    FROM DPG001M
    WHERE LDGR_SCD IN ('01', '02')
    GROUP BY MNGM_BRCD
) d ON m.BRCD = d.MNGM_BRCD
WHERE m.BASE_YMD = (SELECT MAX(BASE_YMD) FROM MVP001S)
  AND ABS(m.EOD_BAL - d.total_bal) > 1;  -- 반올림 오차 허용
"""
    return s


def gen_code_checks(rules):
    """7. 코드 값 제약"""
    s = sec_header("7. 코드 값 연계 제약 (code)")
    code_rules = [r for r in rules['rules'] if r['category'] == 'code']
    for r in code_rules:
        tbl = r.get('table', '')
        cond = r.get('condition', '')
        req = r.get('requires', '')
        if not all([tbl, cond, req]):
            continue
        s += f"""
-- {r['rule_id']}: {r.get('note', '')}
SELECT '{r["rule_id"]}' AS check_name, COUNT(*) AS violations
FROM {tbl}
WHERE ({cond}) AND NOT ({req});
"""
    return s


def gen_cardinality_checks(rules):
    """8. 카디널리티 절대 제약"""
    s = sec_header("8. 카디널리티 절대 제약 (cardinality)")
    card_rules = [r for r in rules['rules'] if r['category'] == 'cardinality']
    for r in card_rules:
        if 'unique_cols' in r:
            tbl = r.get('table')
            cond = r.get('condition', '')
            ucols = r['unique_cols']
            where = f"WHERE {cond}" if cond else ""
            max_cnt = r.get('max_count', 1)
            s += f"""
-- {r['rule_id']}: {r.get('note', '')}
SELECT '{r["rule_id"]}' AS check_name, COUNT(*) AS violations
FROM (
    SELECT {', '.join(ucols)}, COUNT(*) AS cnt
    FROM {tbl} {where}
    GROUP BY {', '.join(ucols)}
    HAVING COUNT(*) > {max_cnt}
) x;
"""
        elif 'parent' in r and 'child' in r:
            parent = r['parent']
            child = r['child']
            cond = r.get('condition', '1=1')
            max_cnt = r.get('max', 1)
            # FK 컬럼 추정 (보통 parent의 PK명과 동일)
            s += f"""
-- {r['rule_id']}: {r.get('note', '')}
-- parent={parent}, child={child}, condition={cond}
-- [MANUAL: FK 컬럼명 확인 후 실행]
"""
    return s


def gen_computed_checks(rules):
    """9. 파생값 관계"""
    s = sec_header("9. 파생값 관계 (computed)")
    s += """
-- 9.1 NPL 잔액 정합성
SELECT 'NPL_COMPUTED' AS check_name, COUNT(*) AS violations
FROM MVN009S m
JOIN (
    SELECT SUM(BAL) AS npl_bal
    FROM RSK022M
    WHERE STAGE IN (2, 3)
) r ON TRUE
WHERE m.BASE_YMD = (SELECT MAX(BASE_YMD) FROM MVN009S)
  AND ABS(m.NPL_BAL - r.npl_bal) > 1;

-- 9.2 FNA029M CIR 범위 검증
SELECT 'CIR_COMPUTED' AS check_name, COUNT(*) AS violations
FROM FNA029M
WHERE CIR IS NOT NULL
  AND (CIR < 0 OR CIR > 200);  -- 비정상 범위

-- 9.3 RPI003M 적립비율
SELECT 'FUND_RTO_COMPUTED' AS check_name, COUNT(*) AS violations
FROM RPI003M
WHERE FUND_RTO IS NOT NULL
  AND (FUND_RTO < 30 OR FUND_RTO > 150);
"""
    return s


def gen_quality_defect_checks(rules):
    """10. 품질결함 허용 비율 검증"""
    s = sec_header("10. V2 품질결함 주입 비율 확인 (의도적 위반)")
    s += """
-- 10.1 미매칭 FK 비율 (허용 0.5%)
-- 각 FK별 orphan 비율을 합산해 전체 대비 확인
-- [레퍼런스: business_rules.json.quality_defect_tolerances]
SELECT 'MISSING_FK_RATIO' AS check_name,
       'See FK orphan queries above, ratio should be ~0.5%' AS note;

-- 10.2 중복 행 비율 (CSC006M 등 지정 테이블만)
SELECT 'DUPLICATE_ROWS_CSC006M' AS check_name,
       COUNT(*) - COUNT(DISTINCT (CSN, CONTACT_TCD, HP_NO, TEL_NO, EMAIL)) AS dup_count,
       COUNT(*) AS total_rows
FROM CSC006M;

-- 10.3 음수 금액 비율 (허용 0.1%)
SELECT 'NEGATIVE_AMT_SAMPLE' AS check_name,
       SUM(CASE WHEN LN_AMT < 0 THEN 1 ELSE 0 END) AS negatives,
       COUNT(*) AS total
FROM LNB001M;
"""
    return s


def gen_distribution_checks():
    """11. 분포 샘플링 검증"""
    s = sec_header("11. 주요 컬럼 분포 검증 (통계)")
    s += """
-- 11.1 고객 성별 분포 (기대 M 51% / F 49%)
SELECT 'GENDER_DIST' AS check_name, GEN_CD, COUNT(*) AS cnt
FROM CSC001M
GROUP BY GEN_CD;

-- 11.2 대출 상품 타입 분포
SELECT 'LOAN_TYPE_DIST' AS check_name, LON_TCD, COUNT(*) AS cnt
FROM LNB001M
GROUP BY LON_TCD
ORDER BY LON_TCD;

-- 11.3 카드 매출 금액 분포 (lognormal 평균 50,000)
SELECT 'SLE_AMT_DIST' AS check_name,
       MIN(TRN_AMT), MAX(TRN_AMT), AVG(TRN_AMT), STDDEV(TRN_AMT),
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY TRN_AMT) AS median
FROM SLE001L;

-- 11.4 외화 통화 분포 (기대 USD 18%, JPY 6%, ...)
SELECT 'FX_CCY_DIST' AS check_name, CCY_CD, COUNT(*) AS cnt
FROM DPY001M
GROUP BY CCY_CD
ORDER BY cnt DESC;

-- 11.5 부점별 고객 분포 (편향 체크)
SELECT 'BRANCH_CUSTOMER_DIST' AS check_name, MAIN_BRCD, COUNT(*) AS cnt
FROM CSC001M
GROUP BY MAIN_BRCD
ORDER BY cnt DESC
LIMIT 20;
"""
    return s


def gen_report_summary():
    """12. 요약 리포트 쿼리"""
    s = sec_header("12. 검증 요약 리포트")
    s += """
-- 모든 검증 쿼리를 UNION으로 실행하는 대시보드
-- (실제 사용 시 위의 각 쿼리들을 CTE로 묶어 한 번에 실행)
-- 아래는 예시만:
WITH violations AS (
    SELECT 'FK_CSN_DPG001M' AS rule, COUNT(*) AS cnt
    FROM DPG001M d WHERE NOT EXISTS (SELECT 1 FROM CSC001M c WHERE c.CSN = d.CSN)
    UNION ALL
    SELECT 'DATE_LNB_EXEC_MAT', COUNT(*) FROM LNB001M WHERE NOT (EXEC_YMD < MAT_YMD)
    UNION ALL
    SELECT 'AMT_LNB_BAL_LIMIT', COUNT(*) FROM LNB001M WHERE NOT (LON_BAL <= LN_AMT)
    -- ... 전체 79개 규칙
)
SELECT rule, cnt,
       CASE WHEN cnt = 0 THEN 'PASS' ELSE 'FAIL' END AS status
FROM violations
ORDER BY cnt DESC;
"""
    return s


def main():
    rules = json.loads(RULES_PATH.read_text(encoding='utf-8'))
    cardinalities = json.loads(CARD_PATH.read_text(encoding='utf-8'))
    graph = json.loads(GRAPH_PATH.read_text(encoding='utf-8'))
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))

    sql_parts = [
        sql_header(),
        gen_basic_checks(cardinalities, graph),
        gen_fk_integrity(graph, catalog),
        gen_date_order_checks(rules),
        gen_amount_checks(rules),
        gen_status_checks(rules),
        gen_cross_table_checks(rules),
        gen_code_checks(rules),
        gen_cardinality_checks(rules),
        gen_computed_checks(rules),
        gen_quality_defect_checks(rules),
        gen_distribution_checks(),
        gen_report_summary(),
    ]

    SQL_OUT.write_text('\n'.join(sql_parts), encoding='utf-8')

    # 인덱스 MD
    sections = [
        ('1. 기본 검증', '행 수, 빈 테이블, 볼륨 편차'),
        ('2. FK 참조 무결성', f'{len(graph["edges"])} 엣지 중 핵심 20개 마스터 대상 orphan 검증'),
        ('3. 날짜 순서 제약', f'{len([r for r in rules["rules"] if r["category"]=="date_order"])} 규칙'),
        ('4. 금액 범위·관계', f'{len([r for r in rules["rules"] if r["category"]=="amount"])} 규칙'),
        ('5. 상태 전이', f'{len([r for r in rules["rules"] if r["category"]=="status"])} 규칙'),
        ('6. 크로스 테이블 정합성', f'{len([r for r in rules["rules"] if r["category"]=="cross_table"])} 규칙'),
        ('7. 코드 값 연계', f'{len([r for r in rules["rules"] if r["category"]=="code"])} 규칙'),
        ('8. 카디널리티 절대 제약', f'{len([r for r in rules["rules"] if r["category"]=="cardinality"])} 규칙'),
        ('9. 파생값 관계', f'{len([r for r in rules["rules"] if r["category"]=="computed"])} 규칙'),
        ('10. 품질결함 비율 검증', 'V2 의도적 결함 주입 비율 확인'),
        ('11. 분포 통계 검증', '성별/대출유형/통화 등 분포 샘플링'),
        ('12. 요약 리포트', '전체 규칙 통합 대시보드'),
    ]

    md = [
        '# 99. 시딩 검증 쿼리 인덱스',
        '',
        f'생성: {datetime.now().isoformat(timespec="seconds")}',
        '',
        '## 개요',
        '',
        '본 쿼리 세트는 `business_rules.json` 79개 규칙 + FK 무결성 + 분포 통계를 검증한다.',
        '자동 생성된 SQL: `meta/verification_queries.sql`',
        '',
        '## 실행 방법',
        '',
        '```bash',
        'psql -h localhost -U bank -d bank_v2 -f meta/verification_queries.sql > verification_report.txt',
        '```',
        '',
        '## 섹션 구성',
        '',
        '| # | 섹션 | 내용 |',
        '|---|---|---|',
    ]
    for i, (title, desc) in enumerate(sections, 1):
        md.append(f'| {i} | {title} | {desc} |')

    md.extend([
        '',
        '## 통과 기준',
        '',
        '- **severity=hard (72개)**: 위반 수 0이어야 함',
        '- **severity=soft (7개)**: 위반 비율 5% 이하 권장',
        '- **FK orphan**: 0.5% 이하 (V2 quality_defect 허용)',
        '- **중복 행**: 지정 테이블만 0.2% 이내',
        '- **음수 금액**: 0.1% 이내',
        '',
        '## 품질 결함 허용치',
        '',
        '`business_rules.json.quality_defect_tolerances` 참조:',
    ])
    for qd in rules.get('quality_defect_tolerances', []):
        md.append(f'- **{qd["defect_type"]}**: {qd.get("max_ratio", "N/A")} — {qd.get("note", "")}')

    md.extend([
        '',
        '## 재생성',
        '',
        '규칙 변경 시 재생성:',
        '```bash',
        'python3 scripts/build_verification_sql.py',
        '```',
    ])

    INDEX_OUT.write_text('\n'.join(md), encoding='utf-8')

    # 통계 출력
    lines = SQL_OUT.read_text().count('\n')
    query_count = SQL_OUT.read_text().count('SELECT ')
    print(f'✅ verification_queries.sql 생성: {SQL_OUT}')
    print(f'   SQL 라인: {lines:,}  쿼리 수: ~{query_count}')
    print(f'📋 verification_index.md: {INDEX_OUT}')


if __name__ == '__main__':
    main()
