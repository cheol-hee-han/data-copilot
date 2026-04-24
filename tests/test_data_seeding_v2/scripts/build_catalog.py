#!/usr/bin/env python3
"""
build_catalog.py
================
모든 주제영역 MD 파일을 파싱해 catalog_v2.json을 생성한다.
fk_rules.yaml 규칙을 적용해 누락된 FK를 자동 주입한다.

산출물:
  - catalog_v2.json       : 1,442 테이블 × 12,331 컬럼 구조화 (+ FK 보강)
  - unmatched_columns.tsv : FK 규칙 매칭 실패 컬럼 리포트 (규칙 보완용)
  - fk_stats.md           : FK 보강 전후 통계 리포트
"""
import re
import json
import yaml
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

BANK_DIR = Path("/home/claude/bank_v2")
META_DIR = BANK_DIR / "meta"
RULES_PATH = META_DIR / "fk_rules.yaml"
CATALOG_PATH = META_DIR / "catalog_v2.json"
UNMATCHED_PATH = META_DIR / "unmatched_columns.tsv"
STATS_PATH = META_DIR / "fk_stats.md"

# ───────────────────────────────────────────────────────────────
# 정규표현식
# ───────────────────────────────────────────────────────────────
RE_TABLE_HEADER = re.compile(r'^## (TB_ADW_[A-Z0-9]+)\s*$', re.M)
RE_ATTR_ROW = re.compile(r'^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*$')
RE_COL_ROW = re.compile(
    r'^\|\s*(\d+)\s*\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*\|\s*'
    r'([^|]*?)\s*\|\s*([A-Z]+)\s*\|\s*([^|]*?)\s*\|\s*'
    r'([YN])\s*\|\s*([YN])\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$'
)
RE_FK_INLINE = re.compile(r'FK\s*[→>]\s*([A-Z][A-Z0-9_]+)\.?([A-Z_]*)?')

# ───────────────────────────────────────────────────────────────
# 유틸
# ───────────────────────────────────────────────────────────────
def parse_attr_block(lines):
    """속성 메타 테이블 파싱 (| 속성 | 값 |)"""
    attrs = {}
    for ln in lines:
        m = RE_ATTR_ROW.match(ln)
        if not m:
            continue
        k, v = m.group(1).strip(), m.group(2).strip()
        if k in {'속성', '---'}:
            continue
        attrs[k] = v
    return attrs


def extract_description_block(text):
    """
    [테이블 설명] 아래의 ``` 블록 전체를 추출해 태그별로 분리.
    태그: [엔티티정의], [대상내], [대상외], [특이사항]
    """
    m = re.search(r'\[테이블 설명\].*?```(.*?)```', text, re.S)
    if not m:
        return {}
    body = m.group(1).strip()
    tags = {'entity_def': '', 'scope_in': '', 'scope_out': '', 'notes': ''}
    current = None
    for line in body.splitlines():
        s = line.strip()
        if s == '[엔티티정의]':
            current = 'entity_def'
            continue
        elif s == '[대상내]':
            current = 'scope_in'
            continue
        elif s == '[대상외]':
            current = 'scope_out'
            continue
        elif s == '[특이사항]':
            current = 'notes'
            continue
        if current:
            tags[current] += line + '\n'
    return {k: v.strip() for k, v in tags.items() if v.strip()}


def parse_column_row(ln):
    """컬럼 정의 테이블 한 행 파싱"""
    m = RE_COL_ROW.match(ln)
    if not m:
        return None
    seq, name, name_kor, dtype, length, nullable, pk, common_def, detail = m.groups()
    # 공통정의/상세정의에서 FK 패턴 추출
    fk_match = RE_FK_INLINE.search(common_def) or RE_FK_INLINE.search(detail)
    fk_explicit = None
    if fk_match:
        ref_table = fk_match.group(1)
        ref_column = fk_match.group(2) if fk_match.group(2) else ''
        fk_explicit = {'table': ref_table, 'column': ref_column or None}
    return {
        'seq': int(seq),
        'name': name.strip(),
        'name_kor': name_kor.strip(),
        'type': dtype.strip(),
        'length': length.strip() or None,
        'nullable': nullable == 'Y',
        'pk': pk == 'Y',
        'definition': common_def.strip(),
        'detail': detail.strip() if detail.strip() != '상동' else None,
        'fk_explicit': fk_explicit,
    }


def parse_table_block(block_text, file_name):
    """테이블 단위 블록을 파싱"""
    # 테이블 ID
    m_header = RE_TABLE_HEADER.search(block_text)
    if not m_header:
        return None
    table_id = m_header.group(1)

    # 속성 메타 라인 추출 (첫 ``` 이전까지 | ... | 라인)
    lines_before_code = []
    for ln in block_text.splitlines():
        if ln.startswith('```'):
            break
        lines_before_code.append(ln)
    attrs = parse_attr_block(lines_before_code)

    # 테이블 설명 태그
    desc = extract_description_block(block_text)

    # 컬럼 정의 추출
    columns = []
    # 전체 테이블 내에서 컬럼 행만 뽑기
    for ln in block_text.splitlines():
        col = parse_column_row(ln)
        if col:
            columns.append(col)

    domain = attrs.get('도메인', '').strip()
    if not domain:
        # 테이블 ID에서 추출 (TB_ADW_CSC001M → CSC)
        m_dom = re.match(r'TB_ADW_([A-Z]+)\d+[MLHSCPGDT]', table_id)
        domain = m_dom.group(1) if m_dom else ''

    return {
        'table_id': table_id,
        'table_kor': attrs.get('테이블한글명', ''),
        'subject_area': attrs.get('주제영역', ''),
        'domain': domain,
        'type': (attrs.get('유형', '') or '').split()[0],
        'load_freq': attrs.get('적재주기', ''),
        'source_system': attrs.get('원천시스템', ''),
        'expected_rows': attrs.get('예상건수', ''),
        'file': file_name,
        **desc,
        'pk': [c['name'] for c in columns if c['pk']],
        'columns': columns,
    }


# ───────────────────────────────────────────────────────────────
# FK 규칙 엔진
# ───────────────────────────────────────────────────────────────
class FKRuleEngine:
    def __init__(self, rules_yaml):
        self.rules = rules_yaml
        # 빠른 룩업 테이블 구축
        self.exclusions = set(rules_yaml['exclusions']['column_names'])

        # Tier 1 글로벌: alias → ref
        self.tier1 = {}
        for rule in rules_yaml['tier1_global']:
            ref = rule['references']
            self.tier1[rule['main']] = ref
            for a in rule.get('aliases', []):
                self.tier1[a] = ref

        # Tier 2 마스터: alias → ref (+ self-reference 마커)
        self.tier2 = {}
        self.tier2_self_ref = set()
        for rule in rules_yaml['tier2_master']:
            ref = rule['references']
            self.tier2[rule['main']] = ref
            for a in rule.get('aliases', []):
                self.tier2[a] = ref
            for a in rule.get('self_ref_aliases', []):
                self.tier2[a] = ref
                self.tier2_self_ref.add(a)

        # Tier 3 도메인 분기
        self.tier3 = rules_yaml['tier3_domain_specific']

        # Tier 3 별칭:
        #  - dict 형태 {name, references}: 도메인 무관 직접 매핑
        #  - string 형태: 해당 Tier 3 rule의 분기를 그대로 적용 (ORIG_TRN_NO → TRN_NO처럼)
        self.tier3_alias_direct = {}    # alias → 참조 (direct)
        self.tier3_alias_same_as = {}   # alias → 원본 컬럼명 (분기 공유)
        for t3 in self.tier3:
            for alias in t3.get('aliases_tier3', []) or []:
                if isinstance(alias, dict):
                    self.tier3_alias_direct[alias['name']] = alias['references']
                elif isinstance(alias, str):
                    self.tier3_alias_same_as[alias] = t3['column']

        # Special
        self.special = {}
        for rule in rules_yaml['special_references']:
            ref = rule.get('references')
            if ref:
                self.special[rule['column']] = ref

        # 통계
        self.stats = Counter()

    def resolve(self, column_name, domain, explicit_fk):
        """
        컬럼에 대해 FK 참조를 결정한다.
        return: (fk_dict or None, source_tag)
          source_tag: 'explicit' | 'excluded' | 'tier1' | 'tier2' | 'tier2_self_ref'
                     | 'tier3' | 'special' | 'unmatched'
        """
        # 0. 명시적 FK (MD에 직접 표기됨)
        if explicit_fk:
            self.stats['explicit'] += 1
            return ({
                'table': explicit_fk['table'],
                'column': explicit_fk['column'] or self._infer_ref_column(explicit_fk['table']),
                'source': 'explicit'
            }, 'explicit')

        # 1. Exclusions
        if column_name in self.exclusions:
            self.stats['excluded'] += 1
            return (None, 'excluded')

        # 2. Tier 1 글로벌
        if column_name in self.tier1:
            ref = self.tier1[column_name]
            tbl, col = ref.split('.')
            self.stats['tier1'] += 1
            return ({'table': tbl, 'column': col, 'source': 'tier1'}, 'tier1')

        # 3. Tier 2 마스터
        if column_name in self.tier2:
            ref = self.tier2[column_name]
            tbl, col = ref.split('.')
            source = 'tier2_self_ref' if column_name in self.tier2_self_ref else 'tier2'
            self.stats[source] += 1
            return ({'table': tbl, 'column': col, 'source': source}, source)

        # 4. Tier 3 도메인 분기 (원본 컬럼명 또는 same_as 별칭)
        effective_col = self.tier3_alias_same_as.get(column_name, column_name)
        for t3 in self.tier3:
            if effective_col == t3['column']:
                for rule in t3['rules']:
                    if domain in rule['domains'] and rule.get('references'):
                        tbl, col = rule['references'].split('.')
                        tag = 'tier3_alias' if column_name != effective_col else 'tier3'
                        self.stats[tag] += 1
                        return ({'table': tbl, 'column': col, 'source': tag}, tag)

        # 4-b. Tier 3 직접 매핑 별칭 (도메인 무관)
        if column_name in self.tier3_alias_direct:
            tbl, col = self.tier3_alias_direct[column_name].split('.')
            self.stats['tier3_direct'] += 1
            return ({'table': tbl, 'column': col, 'source': 'tier3_direct'}, 'tier3_direct')

        # 5. Special
        if column_name in self.special:
            ref = self.special[column_name]
            tbl, col = ref.split('.')
            self.stats['special'] += 1
            return ({'table': tbl, 'column': col, 'source': 'special'}, 'special')

        # 6. 매칭 실패
        self.stats['unmatched'] += 1
        return (None, 'unmatched')

    @staticmethod
    def _infer_ref_column(ref_table):
        """참조 테이블이 명시된 경우 PK 컬럼을 추론 (기본값)"""
        # TB_ADW 접두 제거
        if ref_table.startswith('TB_ADW_'):
            ref_table = ref_table[7:]
        return None  # 상세 결정은 catalog에서 table_id로 조회 가능


# ───────────────────────────────────────────────────────────────
# 메인
# ───────────────────────────────────────────────────────────────
def main():
    # 규칙 로드
    with open(RULES_PATH, encoding='utf-8') as f:
        rules_yaml = yaml.safe_load(f)
    engine = FKRuleEngine(rules_yaml)

    # MD 파일 순회
    md_files = sorted(
        [p for p in BANK_DIR.glob('*.md')
         if p.name not in {'00_README_PLAN.md', '01_FORMAT_SPEC.md', '02_MASTER_CATALOG.md'}]
    )

    tables = []
    unmatched = []  # (table_id, domain, column_name, name_kor, definition)
    excluded_log = []

    for md_path in md_files:
        text = md_path.read_text(encoding='utf-8')
        # 테이블 블록 분리
        # `## TB_ADW_XXX` 이전까지를 잘라내고, 각 header 이후 다음 header 이전까지
        positions = [(m.start(), m.group(1)) for m in RE_TABLE_HEADER.finditer(text)]
        for i, (start, _) in enumerate(positions):
            end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
            block = text[start:end]
            tbl = parse_table_block(block, md_path.name)
            if not tbl:
                continue

            # FK 적용
            for col in tbl['columns']:
                fk, tag = engine.resolve(col['name'], tbl['domain'], col.pop('fk_explicit'))
                # Self-loop 방지: 자기 테이블의 PK 컬럼을 자기 자신이 참조하는 경우
                if fk:
                    src_short = tbl['table_id'].replace('TB_ADW_', '')
                    if fk['table'] == src_short and fk['column'] == col['name']:
                        fk = None
                        tag = 'self_loop_skipped'
                        engine.stats[tag] = engine.stats.get(tag, 0) + 1
                        # tier1/2/3/explicit 집계에서 차감
                col['fk'] = fk
                if tag == 'unmatched' and not col['pk']:
                    # PK는 FK가 없어도 정상
                    unmatched.append({
                        'table_id': tbl['table_id'],
                        'domain': tbl['domain'],
                        'column': col['name'],
                        'name_kor': col['name_kor'],
                        'definition': col['definition'][:80],
                    })
                if tag == 'excluded':
                    excluded_log.append({
                        'table_id': tbl['table_id'],
                        'column': col['name'],
                    })

            tables.append(tbl)

    # ── catalog_v2.json 저장
    total_cols = sum(len(t['columns']) for t in tables)
    fk_cols = sum(1 for t in tables for c in t['columns'] if c.get('fk'))
    catalog = {
        'version': 2,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'stats': {
            'tables': len(tables),
            'columns_total': total_cols,
            'fk_resolved': fk_cols,
            'fk_coverage_pct': round(fk_cols / total_cols * 100, 2),
            'breakdown': dict(engine.stats),
        },
        'tables': tables,
    }
    CATALOG_PATH.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    # ── unmatched_columns.tsv 저장 (규칙 보완용)
    lines = ['table_id\tdomain\tcolumn\tname_kor\tdefinition_preview']
    for r in unmatched:
        lines.append('\t'.join([r['table_id'], r['domain'], r['column'],
                                 r['name_kor'], r['definition']]))
    UNMATCHED_PATH.write_text('\n'.join(lines), encoding='utf-8')

    # ── fk_stats.md 리포트
    top_unmatched = Counter([r['column'] for r in unmatched]).most_common(30)
    md = [
        '# FK 보강 통계 리포트',
        f'\n생성: {catalog["generated_at"]}\n',
        '## 요약',
        f'- 전체 테이블: **{len(tables):,}**',
        f'- 전체 컬럼: **{total_cols:,}**',
        f'- FK 해결 컬럼: **{fk_cols:,}** ({catalog["stats"]["fk_coverage_pct"]}%)',
        f'- 적용 전 FK 표기: 738 (6.0%) → **적용 후: {fk_cols:,}**',
        '',
        '## Source별 분포',
        '| source | count |',
        '|---|---|',
    ]
    for src, cnt in sorted(engine.stats.items(), key=lambda x: -x[1]):
        md.append(f'| {src} | {cnt:,} |')
    md.extend([
        '',
        '## 미매칭(unmatched) Top 30 컬럼',
        '| 컬럼명 | 빈도 |',
        '|---|---|',
    ])
    for col, cnt in top_unmatched:
        md.append(f'| `{col}` | {cnt} |')
    md.append('')
    md.extend([
        '## 주제영역별 FK 커버리지',
        '| domain | 컬럼 | FK | 커버율 |',
        '|---|---|---|---|',
    ])
    dom_stats = defaultdict(lambda: [0, 0])
    for t in tables:
        for c in t['columns']:
            dom_stats[t['domain']][0] += 1
            if c.get('fk'):
                dom_stats[t['domain']][1] += 1
    for dom in sorted(dom_stats.keys()):
        tot, fk = dom_stats[dom]
        md.append(f'| {dom} | {tot:,} | {fk:,} | {round(fk/tot*100,1)}% |')

    STATS_PATH.write_text('\n'.join(md), encoding='utf-8')

    # 콘솔 출력
    print(f'✅ catalog_v2.json 생성: {CATALOG_PATH}')
    print(f'   테이블: {len(tables):,}  컬럼: {total_cols:,}  FK해결: {fk_cols:,} ({catalog["stats"]["fk_coverage_pct"]}%)')
    print(f'📊 fk_stats.md 리포트: {STATS_PATH}')
    print(f'⚠️  unmatched_columns.tsv: {UNMATCHED_PATH} ({len(unmatched):,}건)')
    print(f'\n📈 Source별 분포:')
    for src, cnt in sorted(engine.stats.items(), key=lambda x: -x[1]):
        print(f'   {src:20s} {cnt:6,}')


if __name__ == '__main__':
    main()
