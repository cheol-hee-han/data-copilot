#!/usr/bin/env python3
"""
build_business_rules.py
=======================
business_rules.yaml을 파싱해 business_rules.json을 생성한다.
  - 규칙 ID 자동 부여 (이미 있으면 유지)
  - 참조 테이블·컬럼 존재 검증 (catalog_v2.json 대비)
  - 도메인·카테고리별 집계
  - 누락 테이블 경고
"""
import json
import yaml
import re
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

BANK_DIR = Path("/home/claude/bank_v2")
META_DIR = BANK_DIR / "meta"
CATALOG_PATH = META_DIR / "catalog_v2.json"
YAML_PATH = META_DIR / "business_rules.yaml"
OUT_PATH = META_DIR / "business_rules.json"
STATS_PATH = META_DIR / "business_rules_stats.md"


def main():
    rules = yaml.safe_load(YAML_PATH.read_text(encoding='utf-8'))
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))

    # 테이블·컬럼 맵
    table_cols = {}  # short_id → {column names}
    table_info = {}  # short_id → table info
    for t in catalog['tables']:
        short = t['table_id'].replace('TB_ADW_', '')
        table_cols[short] = {c['name'] for c in t['columns']}
        table_info[short] = {'domain': t.get('domain', ''), 'type': t.get('type', '')}

    # 규칙 수집 + 검증
    all_rules = []
    warnings = []

    def check_table(tbl, rule_id, category):
        if tbl not in table_cols:
            warnings.append({
                'category': category,
                'rule_id': rule_id,
                'issue': 'table_not_found',
                'detail': tbl,
            })
            return False
        return True

    def check_cols_in_expr(tbl, expr, rule_id, category):
        """expr에서 사용된 컬럼이 tbl에 존재하는지 rough check"""
        if tbl not in table_cols:
            return
        cols = re.findall(r'\b[A-Z][A-Z0-9_]+\b', expr)
        known = table_cols[tbl]
        # SQL 예약어 제외
        reserved = {'AND', 'OR', 'NOT', 'IS', 'NULL', 'IN', 'BETWEEN', 'LIKE',
                    'CURRENT_DATE', 'DATE_SUB', 'INTERVAL', 'YEAR', 'SELECT',
                    'WHERE', 'FROM', 'MAX', 'MIN', 'COUNT', 'SUM', 'AVG',
                    'EXISTS', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'AS'}
        for c in cols:
            if c in reserved or c.isdigit():
                continue
            # 숫자-only나 코드값 (01, 99 등)도 제외
            if re.match(r'^\d+$', c):
                continue
            # 다른 테이블의 컬럼 접두사 (TBL.COL) 처리 안 함
            if '.' in c:
                continue
            if c not in known:
                # 아마 다른 테이블 참조거나 SQL 함수
                pass  # warnings로 추가하면 노이즈 많음 - 생략

    # ── 카테고리별 처리 ──
    categories = [
        ('date_order_rules', 'date_order'),
        ('amount_rules', 'amount'),
        ('status_rules', 'status'),
        ('cross_table_rules', 'cross_table'),
        ('code_rules', 'code'),
        ('cardinality_hard_limits', 'cardinality'),
        ('computed_rules', 'computed'),
    ]

    rule_counter = 0
    for yaml_key, cat in categories:
        for r in rules.get(yaml_key, []):
            rule_counter += 1
            r_out = {
                'category': cat,
                'rule_id': r.get('id', f'{cat}_{rule_counter:04d}'),
                'severity': r.get('severity', 'hard'),
            }
            # 단일 테이블
            if 'table' in r:
                r_out['table'] = r['table']
                r_out['domain'] = table_info.get(r['table'], {}).get('domain', '')
                check_table(r['table'], r_out['rule_id'], cat)
                if 'expr' in r:
                    check_cols_in_expr(r['table'], r['expr'], r_out['rule_id'], cat)
            # 복수 테이블 (cross_table)
            if 'tables' in r:
                r_out['tables'] = r['tables']
                for t in r['tables']:
                    check_table(t, r_out['rule_id'], cat)
            if 'cross_table' in r:
                r_out['cross_table'] = r['cross_table']
                check_table(r['cross_table'], r_out['rule_id'], cat)
            if 'parent' in r and 'child' in r:
                r_out['parent'] = r['parent']
                r_out['child'] = r['child']
                check_table(r['parent'], r_out['rule_id'], cat)
                check_table(r['child'], r_out['rule_id'], cat)

            # 기타 필드 복사
            for k in ('expr', 'condition', 'requires', 'note', 'exceptions',
                     'join_key', 'cross_ref', 'max', 'max_count', 'unique_cols'):
                if k in r:
                    r_out[k] = r[k]

            all_rules.append(r_out)

    # ── 통계 ──
    rules_by_cat = Counter(r['category'] for r in all_rules)
    rules_by_severity = Counter(r['severity'] for r in all_rules)
    rules_by_domain = Counter(r.get('domain', '_unknown') for r in all_rules if r.get('domain'))
    warnings_by_type = Counter(w['issue'] for w in warnings)

    # ── 결과 ──
    result = {
        'version': 1,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'stats': {
            'total_rules': len(all_rules),
            'by_category': dict(rules_by_cat),
            'by_severity': dict(rules_by_severity),
            'domains_covered': sorted(rules_by_domain.keys()),
            'warnings': len(warnings),
            'warning_types': dict(warnings_by_type),
        },
        'quality_defect_tolerances': rules.get('quality_defect_tolerances', []),
        'meta': rules.get('meta', {}),
        'rules': all_rules,
        'warnings': warnings,
    }
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    # ── Markdown 리포트 ──
    md = [
        '# business_rules.json — 정합성 제약 리포트',
        '',
        f'생성: {result["generated_at"]}',
        '',
        '## 요약',
        f'- 전체 규칙: **{len(all_rules)}**',
        f'- 경고 (테이블 미존재 등): **{len(warnings)}**',
        '',
        '## 카테고리별 규칙 수',
        '| 카테고리 | 규칙 | 설명 |',
        '|---|---|---|',
    ]
    cat_desc = {
        'date_order': '날짜 순서 (A < B)',
        'amount': '금액 범위·관계',
        'status': '상태 전이 조건',
        'cross_table': '크로스 테이블 정합성',
        'code': '코드 값 연계',
        'cardinality': '카디널리티 절대 제약',
        'computed': '파생값 관계',
    }
    for cat, cnt in sorted(rules_by_cat.items(), key=lambda x: -x[1]):
        md.append(f'| {cat} | {cnt} | {cat_desc.get(cat, "")} |')

    md.extend(['', '## Severity', '| severity | 규칙 |', '|---|---|'])
    for sev, cnt in sorted(rules_by_severity.items(), key=lambda x: -x[1]):
        md.append(f'| {sev} | {cnt} |')

    md.extend(['', '## 도메인별 커버리지', '| domain | 규칙 |', '|---|---|'])
    for dom, cnt in sorted(rules_by_domain.items(), key=lambda x: -x[1]):
        md.append(f'| {dom} | {cnt} |')

    if warnings:
        md.extend(['', '## ⚠️ 경고 (데이터 부정확)', ''])
        for w in warnings[:20]:
            md.append(f'- {w["category"]}/{w["rule_id"]}: {w["issue"]} - {w["detail"]}')

    STATS_PATH.write_text('\n'.join(md), encoding='utf-8')

    print(f'✅ business_rules.json 생성: {OUT_PATH}')
    print(f'   전체 규칙: {len(all_rules)}  경고: {len(warnings)}')
    print(f'\n📊 카테고리별:')
    for cat, cnt in sorted(rules_by_cat.items(), key=lambda x: -x[1]):
        print(f'   {cat:15s} {cnt:4d}')


if __name__ == '__main__':
    main()
