#!/usr/bin/env python3
"""
build_distributions.py
======================
catalog_v2.json의 12,285 컬럼 각각에 값 분포 스펙(generator + params)을 매핑한다.
distributions.yaml 규칙을 적용해 자동 추론하며, 매칭 순서는:

  1. specific_overrides         (테이블.컬럼 정확 매칭)
  2. audit_columns              (ETCL_* 컬럼)
  3. fk_columns                 (catalog의 fk 필드 존재 시 → fk generator)
  4. enum_columns               (catalog definition에 'NN:...' 패턴 있음)
  5. global_name_rules          (컬럼명 정확 매칭)
  6. suffix_patterns            (접미사 매칭 + 주제영역 스케일)
  7. prefix_patterns            (접두사 매칭)
  8. type_defaults              (타입 + 길이 조합)

산출물:
  - meta/distributions.json     : 각 테이블.컬럼 → 분포 스펙
  - meta/distributions_stats.md : 매칭 통계 리포트
  - meta/dist_unmatched.tsv     : 매칭 실패 컬럼
"""
import json
import yaml
import re
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

BANK_DIR = Path("/home/claude/bank_v2")
META_DIR = BANK_DIR / "meta"
CATALOG_PATH = META_DIR / "catalog_v2.json"
RULES_PATH = META_DIR / "distributions.yaml"
OUT_PATH = META_DIR / "distributions.json"
STATS_PATH = META_DIR / "distributions_stats.md"
UNMATCHED_PATH = META_DIR / "dist_unmatched.tsv"

RE_ENUM = re.compile(r'\b(\d{2}|\d{1}|[YN]):[^\s,|]+')


def extract_enum_values(definition):
    """컬럼 정의에서 '01:개인 02:법인' 같은 enum 값 추출"""
    if not definition:
        return None
    matches = RE_ENUM.findall(definition)
    if not matches:
        return None
    # 'NN:값' 패턴을 파싱
    pairs = []
    pattern = re.compile(r'([0-9YN]{1,2}):([^\s,|]+)')
    for m in pattern.finditer(definition):
        pairs.append({'code': m.group(1), 'label': m.group(2)})
    # 중복 제거 (코드 기준)
    seen = set()
    uniq = []
    for p in pairs:
        if p['code'] in seen:
            continue
        seen.add(p['code'])
        uniq.append(p)
    return uniq if uniq else None


class DistributionEngine:
    def __init__(self, rules):
        self.rules = rules
        self.stats = Counter()

        # 빠른 룩업
        self.audit_cols = {r['column']: r for r in rules['audit_columns']}

        self.global_names = {}
        for rule in rules['global_name_rules']:
            for col in rule['columns']:
                self.global_names[col] = rule

        # 접미사·접두사 규칙
        self.suffix_rules = rules['suffix_patterns']
        self.prefix_rules = rules.get('prefix_patterns', [])

        # 타입 기본값 (키 정규화: NUMERIC_18,2, CHAR_1 등)
        self.type_defaults = rules['type_defaults']

        # 오버라이드
        self.overrides = rules.get('specific_overrides', {})

    def _type_key(self, col):
        """타입 + 길이를 키로 정규화 (type_defaults 매칭용)"""
        t, ln = col['type'], col.get('length') or ''
        ln = ln.strip()
        if t in ('NUMERIC',):
            return f'NUMERIC_{ln}'
        if t in ('CHAR',):
            return f'CHAR_{ln}'
        return t

    def resolve(self, table_short, col):
        """컬럼에 대해 분포 스펙을 결정한다."""
        name = col['name']
        definition = col.get('definition', '') or ''

        # 0. 오버라이드 (테이블.컬럼)
        if table_short in self.overrides and name in self.overrides[table_short]:
            spec = {**self.overrides[table_short][name], 'source': 'override'}
            self.stats['override'] += 1
            return spec

        # 1. 감사 컬럼
        if name in self.audit_cols:
            spec = {**self.audit_cols[name], 'source': 'audit'}
            self.stats['audit'] += 1
            return spec

        # 2. FK 컬럼 → fk generator
        if col.get('fk'):
            spec = {
                'generator': 'fk',
                'references': col['fk'],
                'null_ratio': self.rules['seeding_defaults']['null_ratio_default'] if col.get('nullable') else 0,
                'source': 'fk'
            }
            self.stats['fk'] += 1
            return spec

        # 3. Enum 컬럼 (definition에서 추출)
        enum_values = extract_enum_values(definition)
        if enum_values and len(enum_values) >= 2:
            spec = {
                'generator': 'enum_weighted',
                'values': [v['code'] for v in enum_values],
                'labels': [v['label'] for v in enum_values],
                'weights_hint': 'uniform',  # 실제 비중은 수동 조정 필요
                'null_ratio': 0.02 if col.get('nullable') else 0,
                'source': 'enum'
            }
            self.stats['enum'] += 1
            return spec

        # 4. 전역 이름 규칙
        if name in self.global_names:
            rule = self.global_names[name]
            spec = {**{k: v for k, v in rule.items() if k != 'columns'}, 'source': 'global_name'}
            self.stats['global_name'] += 1
            return spec

        # 5. 접미사 패턴
        for sfx_rule in self.suffix_rules:
            sfx = sfx_rule['suffix']
            if name.endswith(sfx) or name == sfx.lstrip('_'):
                # 타입 힌트 체크
                type_hint = sfx_rule.get('type_hint')
                if type_hint and col['type'] not in type_hint:
                    continue
                spec = {k: v for k, v in sfx_rule.items()
                        if k not in ('suffix', 'type_hint', 'domain_scale')}
                spec['source'] = 'suffix'
                spec['matched_suffix'] = sfx

                # 주제영역 스케일 적용 (_AMT 등)
                domain_scale = sfx_rule.get('domain_scale')
                if domain_scale:
                    # table_short에서 도메인 추출 (CSC001M → CSC)
                    dom_match = re.match(r'([A-Z]+)\d', table_short)
                    dom = dom_match.group(1) if dom_match else ''
                    if dom in domain_scale:
                        scale = domain_scale[dom]
                        spec['default_mean'] = scale
                        spec['default_std'] = scale * 1.5

                if col.get('nullable') and 'null_ratio' not in spec:
                    spec['null_ratio'] = self.rules['seeding_defaults']['null_ratio_default']
                self.stats['suffix'] += 1
                return spec

        # 6. 접두사 패턴 (일부만)
        for pfx_rule in self.prefix_rules:
            if pfx_rule.get('passthrough'):
                continue
            if name.startswith(pfx_rule['prefix']):
                spec = {k: v for k, v in pfx_rule.items() if k not in ('prefix',)}
                spec['source'] = 'prefix'
                self.stats['prefix'] += 1
                return spec

        # 7. 타입 기본값
        tkey = self._type_key(col)
        if tkey in self.type_defaults:
            spec = {**self.type_defaults[tkey], 'source': 'type_default'}
            self.stats['type_default'] += 1
            return spec

        # 매칭 실패
        self.stats['unmatched'] += 1
        return None


def main():
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    rules = yaml.safe_load(RULES_PATH.read_text(encoding='utf-8'))
    engine = DistributionEngine(rules)

    tables = catalog['tables']
    out_tables = []
    unmatched = []

    for t in tables:
        short = t['table_id'].replace('TB_ADW_', '')
        columns_out = []
        for col in t['columns']:
            spec = engine.resolve(short, col)
            col_out = {
                'name': col['name'],
                'type': col['type'],
                'length': col.get('length'),
                'pk': col.get('pk'),
                'nullable': col.get('nullable'),
            }
            if spec:
                col_out['distribution'] = spec
            else:
                unmatched.append({
                    'table': short,
                    'column': col['name'],
                    'type': col['type'],
                    'length': col.get('length') or '',
                    'name_kor': col.get('name_kor', ''),
                    'definition': (col.get('definition') or '')[:80]
                })
            columns_out.append(col_out)
        out_tables.append({
            'table_id': t['table_id'],
            'table_kor': t.get('table_kor', ''),
            'domain': t.get('domain', ''),
            'expected_rows': t.get('expected_rows', ''),
            'columns': columns_out,
        })

    result = {
        'version': 1,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'seeding_defaults': rules['seeding_defaults'],
        'stats': {
            'tables': len(out_tables),
            'columns_total': sum(len(t['columns']) for t in out_tables),
            'columns_with_dist': sum(1 for t in out_tables for c in t['columns'] if 'distribution' in c),
            'unmatched': len(unmatched),
            'breakdown': dict(engine.stats),
        },
        'quality_defects': rules.get('quality_defects', []),
        'tables': out_tables,
    }
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    # unmatched TSV
    lines = ['table\tcolumn\ttype\tlength\tname_kor\tdefinition']
    for r in unmatched:
        lines.append('\t'.join([r['table'], r['column'], r['type'], r['length'],
                                r['name_kor'], r['definition']]))
    UNMATCHED_PATH.write_text('\n'.join(lines), encoding='utf-8')

    # 리포트
    top_unmatched = Counter(r['column'] for r in unmatched).most_common(30)
    total = result['stats']['columns_total']
    matched = result['stats']['columns_with_dist']

    md = [
        '# distributions.json 생성 리포트',
        '',
        f'생성: {result["generated_at"]}',
        '',
        '## 요약',
        f'- 전체 컬럼: **{total:,}**',
        f'- 분포 매핑 성공: **{matched:,}** ({round(matched/total*100, 2)}%)',
        f'- 매칭 실패: **{len(unmatched):,}** ({round(len(unmatched)/total*100, 2)}%)',
        '',
        '## 매칭 Source 분포',
        '| source | count | % |',
        '|---|---|---|',
    ]
    for src, cnt in sorted(engine.stats.items(), key=lambda x: -x[1]):
        pct = round(cnt / total * 100, 2)
        md.append(f'| {src} | {cnt:,} | {pct}% |')
    md.extend(['', '## 미매칭 Top 30 컬럼', '| 컬럼 | 빈도 |', '|---|---|'])
    for col, cnt in top_unmatched:
        md.append(f'| `{col}` | {cnt} |')
    STATS_PATH.write_text('\n'.join(md), encoding='utf-8')

    print(f'✅ distributions.json 생성: {OUT_PATH}')
    print(f'   매칭: {matched:,} / {total:,} ({round(matched/total*100, 2)}%)')
    print(f'   미매칭: {len(unmatched):,}')
    print(f'\n📊 Source별:')
    for src, cnt in sorted(engine.stats.items(), key=lambda x: -x[1]):
        print(f'   {src:15s} {cnt:6,}')


if __name__ == '__main__':
    main()
