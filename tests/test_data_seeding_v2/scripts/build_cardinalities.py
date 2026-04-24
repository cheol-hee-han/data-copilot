#!/usr/bin/env python3
"""
build_cardinalities.py
======================
cardinalities.yaml을 적용해 각 테이블의 시딩 볼륨을 계산한다.

계산 순서 (fk_graph.json의 위상 정렬 활용):
  1. 기준 테이블 (base_volumes) - scale_factor 적용
  2. special_volumes 오버라이드
  3. big_table_caps 오버라이드
  4. FK 관계 역산 (부모 볼륨 × cardinality)
  5. domain_defaults 기반 fallback

산출물:
  - meta/cardinalities.json      : 각 테이블 시딩 볼륨 + 관계 정보
  - meta/cardinalities_stats.md  : 레벨별 시딩 볼륨 리포트
"""
import json
import yaml
import math
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

BANK_DIR = Path("/home/claude/bank_v2")
META_DIR = BANK_DIR / "meta"
CATALOG_PATH = META_DIR / "catalog_v2.json"
GRAPH_PATH = META_DIR / "fk_graph.json"
RULES_PATH = META_DIR / "cardinalities.yaml"
OUT_PATH = META_DIR / "cardinalities.json"
STATS_PATH = META_DIR / "cardinalities_stats.md"


def expected_cardinality(cardinality):
    """카디널리티 정의에서 평균 자식 개수 기댓값 계산."""
    d = cardinality['distribution']
    if d == 'fixed':
        return cardinality['value']
    if d == 'bernoulli':
        return cardinality['p']
    if d == 'poisson':
        lam = cardinality['lambda']
        zi = cardinality.get('zero_inflation', 0)
        # 0-inflated Poisson 기댓값: (1-zi) * lambda
        return (1 - zi) * lam
    if d == 'uniform':
        return (cardinality.get('min', 0) + cardinality.get('max', 1)) / 2
    # 볼륨 정책 참조
    if d in ('use_big_table_cap', 'period_grain', 'period_parent'):
        return None
    return 1.0


def main():
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    graph = json.loads(GRAPH_PATH.read_text(encoding='utf-8'))
    rules = yaml.safe_load(RULES_PATH.read_text(encoding='utf-8'))

    profile_name = rules.get('default_profile', 'prototype')
    profile = rules['profiles'][profile_name]
    scale = profile['scale_factor']

    # ── 테이블 맵 ──
    table_map = {}  # short_id → {domain, type, expected_rows}
    for t in catalog['tables']:
        short = t['table_id'].replace('TB_ADW_', '')
        table_map[short] = {
            'domain': t.get('domain', ''),
            'type': t.get('type', ''),
            'expected_rows_str': t.get('expected_rows', ''),
        }

    all_tables = list(table_map.keys())

    # ── 레벨 맵 (fk_graph.json) ──
    level_of = {}
    for lv_info in graph['levels']:
        for tid in lv_info['tables']:
            level_of[tid] = lv_info['level']

    # ── 명시적 관계 맵 ──
    relationships = rules.get('relationships', [])
    rel_by_parent_child = {}  # (parent, child) → cardinality
    child_parents_explicit = defaultdict(list)  # child → [parents] (명시된 관계만)
    for r in relationships:
        key = (r['parent'], r['child'])
        rel_by_parent_child[key] = r
        child_parents_explicit[r['child']].append(r['parent'])

    # ── FK 그래프에서 모든 부모-자식 관계 수집 (명시+암묵) ──
    all_child_parents = defaultdict(set)  # child → {parents}
    for edge in graph['edges']:
        # edge: from=src, to=ref. src가 ref를 참조 = src가 자식, ref가 부모
        all_child_parents[edge['from']].add(edge['to'])

    # 타입별 기본 카디널리티 (명시되지 않은 FK 관계용)
    default_cardinality_by_child_type = {
        'M': {'distribution': 'bernoulli', 'p': 0.3},    # 마스터: 부모의 30%가 자식 보유
        'L': {'distribution': 'poisson', 'lambda': 5, 'zero_inflation': 0.3},    # 로그
        'H': {'distribution': 'poisson', 'lambda': 2.0, 'min': 1},               # 이력
        'S': {'distribution': 'fixed', 'value': 1},                              # 마트 스냅샷
        'P': {'distribution': 'poisson', 'lambda': 3},                            # 스냅샷
        'C': {'distribution': 'fixed', 'value': 1},                               # 코드
    }

    # ── 볼륨 계산 ──
    volumes = {}
    volume_source = {}    # 어디서 결정됐는지
    policy_notes = {}

    # 1. 특수 볼륨 (원본 유지 테이블 - 스케일 미적용)
    special = rules.get('special_volumes', {}).get(profile_name, {})
    for tbl, vol in special.items():
        if tbl not in table_map:
            continue  # catalog에 없는 테이블 skip
        volumes[tbl] = vol
        volume_source[tbl] = 'special'

    # 2. 대용량 테이블 상한 (고정)
    caps = rules.get('big_table_caps', {}).get(profile_name, {})
    for tbl, vol in caps.items():
        if tbl not in table_map:
            continue
        volumes[tbl] = vol
        volume_source[tbl] = 'big_table_cap'

    # 3. 기준 볼륨 (scale 적용)
    for tbl, base_vol in rules['base_volumes'].items():
        if tbl not in table_map:
            continue
        if tbl in volumes:
            continue  # 이미 special/cap으로 설정됨
        volumes[tbl] = max(1, round(base_vol * scale))
        volume_source[tbl] = 'base'

    # 4. FK 관계 기반 역산 (위상 정렬 순서대로)
    topo_order = graph['topological_order']
    for tbl in topo_order:
        if tbl in volumes:
            continue
        # 4-a. 명시적 관계 우선
        explicit_parents = child_parents_explicit.get(tbl, [])
        if explicit_parents:
            # 여러 부모가 있으면 가장 큰 볼륨의 부모 기준 (주된 관계)
            parent_volumes = []
            for p in explicit_parents:
                if p in volumes:
                    key = (p, tbl)
                    r = rel_by_parent_child[key]
                    card = r['cardinality']
                    if card['distribution'] == 'use_big_table_cap':
                        continue  # 별도 처리됨
                    ec = expected_cardinality(card)
                    if ec is None:
                        continue
                    parent_volumes.append((p, round(volumes[p] * ec)))
            if parent_volumes:
                best_parent, best_vol = max(parent_volumes, key=lambda x: x[1])
                volumes[tbl] = max(1, best_vol)
                volume_source[tbl] = f'derived_explicit:{best_parent}'
                continue

        # 4-b. 암묵 FK 엣지 기반 자동 추론
        implicit_parents = all_child_parents.get(tbl, set())
        if implicit_parents:
            ttype = table_map[tbl]['type']
            default_card = default_cardinality_by_child_type.get(
                ttype, {'distribution': 'poisson', 'lambda': 1.0}
            )
            ec = expected_cardinality(default_card)
            if ec is not None:
                parent_vols = [(p, volumes[p]) for p in implicit_parents if p in volumes]
                if parent_vols:
                    best_parent, parent_vol = max(parent_vols, key=lambda x: x[1])
                    volumes[tbl] = max(1, round(parent_vol * ec))
                    volume_source[tbl] = f'derived_implicit:{best_parent}'
                    continue

        # 5. 도메인 기본값 (테이블 타입 기반)
        ttype = table_map[tbl]['type']
        domain_defaults = rules.get('domain_defaults', {})

        if re.match(r'[A-Z]+\d+H$', tbl):  # 이력
            # 가장 가까운 M 테이블 추정 (같은 prefix)
            prefix_match = re.match(r'([A-Z]+)(\d+)H', tbl)
            if prefix_match:
                prefix = prefix_match.group(1)
                # 같은 prefix의 001M 찾기
                parent_candidate = f'{prefix}001M'
                if parent_candidate in volumes:
                    lam = domain_defaults['history'].get('lambda', 2.5)
                    volumes[tbl] = max(1, round(volumes[parent_candidate] * lam))
                    volume_source[tbl] = f'history_default:{parent_candidate}'
                    continue

        if ttype == 'S':  # 스냅샷 마트
            # 월 스냅샷: 부점(100) × 12개월 = 1200
            # 일 스냅샷: 부점(100) × 30일 = 3000
            if '일자' in tbl or 'D' in tbl[-3:]:
                default = 3000
            elif '월' in tbl or 'Y' in tbl[-3:]:
                default = 1200
            elif '분기' in tbl:
                default = 400
            else:
                default = 500
            volumes[tbl] = default
            volume_source[tbl] = 'mart_default'
            continue

        if ttype == 'P':  # 스냅샷 (일별 등)
            # 기본 30일치
            volumes[tbl] = 5000
            volume_source[tbl] = 'snapshot_default'
            continue

        # 기본값: 부모 없고 타입 불명 → 작은 수
        # L(Log) 테이블이면 medium 로그
        if ttype == 'L':
            volumes[tbl] = 2000
            volume_source[tbl] = 'log_default'
            continue

        # 최후 기본값
        volumes[tbl] = 500
        volume_source[tbl] = 'fallback'

    # ── 관계 상세 정보 ──
    relationship_details = []
    for r in relationships:
        p, c = r['parent'], r['child']
        ec = expected_cardinality(r['cardinality'])
        relationship_details.append({
            'parent': p,
            'child': c,
            'via_column': r['via_column'],
            'cardinality': r['cardinality'],
            'expected_per_parent': ec,
            'parent_volume': volumes.get(p),
            'child_volume': volumes.get(c),
            'note': r.get('note', '')
        })

    # ── 레벨별 집계 ──
    level_volumes = defaultdict(lambda: {'count': 0, 'records': 0})
    for tbl, vol in volumes.items():
        lv = level_of.get(tbl, -1)
        level_volumes[lv]['count'] += 1
        level_volumes[lv]['records'] += vol

    # ── 도메인별 집계 ──
    domain_volumes = defaultdict(lambda: {'count': 0, 'records': 0})
    for tbl, vol in volumes.items():
        dom = table_map[tbl]['domain']
        domain_volumes[dom]['count'] += 1
        domain_volumes[dom]['records'] += vol

    # ── JSON 저장 ──
    result = {
        'version': 1,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'profile': profile_name,
        'profile_config': profile,
        'scale_factor': scale,
        'base_date': profile['base_date'],
        'stats': {
            'total_tables': len(all_tables),
            'tables_with_volume': len(volumes),
            'total_records': sum(volumes.values()),
            'levels': len(graph['levels']),
            'relationships_defined': len(relationships),
        },
        'volumes': [
            {
                'table': tbl,
                'volume': volumes[tbl],
                'source': volume_source[tbl],
                'level': level_of.get(tbl, -1),
                'domain': table_map[tbl]['domain'],
                'type': table_map[tbl]['type'],
                'original_expected': table_map[tbl]['expected_rows_str'],
            }
            for tbl in sorted(all_tables, key=lambda x: (level_of.get(x, 99), x))
        ],
        'relationships': relationship_details,
        'level_summary': [
            {'level': lv, 'tables': info['count'], 'total_records': info['records']}
            for lv, info in sorted(level_volumes.items())
        ],
        'domain_summary': [
            {'domain': d, 'tables': info['count'], 'total_records': info['records']}
            for d, info in sorted(domain_volumes.items(), key=lambda x: -x[1]['records'])
        ],
    }
    OUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    # ── 리포트 ──
    total_records = sum(volumes.values())
    md = [
        f'# cardinalities.json — 시딩 볼륨 계산 리포트 ({profile_name})',
        '',
        f'생성: {result["generated_at"]}',
        f'프로파일: **{profile_name}** (scale={scale})',
        f'기준일: {profile["base_date"]}',
        '',
        '## 전체 요약',
        f'- 테이블: **{len(all_tables):,}**',
        f'- 총 시딩 레코드: **{total_records:,}** 행',
        f'- 정의된 관계: **{len(relationships)}**',
        '',
        '## Volume Source 분포',
        '| source | tables | 설명 |',
        '|---|---|---|',
    ]
    source_counter = defaultdict(int)
    for s in volume_source.values():
        # 'derived_from:XXX'는 'derived'로 묶기
        key = s.split(':')[0]
        source_counter[key] += 1
    src_desc = {
        'base': '기준 볼륨 × scale',
        'special': '특수 오버라이드 (코드/조직 등)',
        'big_table_cap': '대용량 상한 적용',
        'derived_from': 'FK 관계 역산',
        'history_default': '이력 테이블 기본값',
        'mart_default': '마트 기본값',
        'snapshot_default': '스냅샷 기본값',
        'log_default': '로그 기본값',
        'fallback': '최후 기본값 (500)',
    }
    for src, cnt in sorted(source_counter.items(), key=lambda x: -x[1]):
        md.append(f'| {src} | {cnt:,} | {src_desc.get(src, "")} |')

    md.extend([
        '',
        '## 레벨별 시딩 볼륨 (시딩 순서)',
        '| level | 테이블 수 | 총 레코드 | 비고 |',
        '|---|---|---|---|',
    ])
    for lv in sorted(level_volumes.keys()):
        info = level_volumes[lv]
        md.append(f'| {lv} | {info["count"]:,} | {info["records"]:,} | |')

    md.extend([
        '',
        '## 도메인별 시딩 볼륨 Top 30',
        '| domain | 테이블 | 레코드 | 비중 |',
        '|---|---|---|---|',
    ])
    for d in sorted(domain_volumes.keys(), key=lambda x: -domain_volumes[x]['records'])[:30]:
        info = domain_volumes[d]
        pct = round(info['records'] / total_records * 100, 1) if total_records else 0
        md.append(f'| {d} | {info["count"]:,} | {info["records"]:,} | {pct}% |')

    md.extend([
        '',
        '## Top 30 최대 볼륨 테이블',
        '| 테이블 | 볼륨 | source | level | domain |',
        '|---|---|---|---|---|',
    ])
    top_tables = sorted(volumes.items(), key=lambda x: -x[1])[:30]
    for tbl, vol in top_tables:
        md.append(f'| `{tbl}` | {vol:,} | {volume_source[tbl]} | {level_of.get(tbl, "-")} | {table_map[tbl]["domain"]} |')

    STATS_PATH.write_text('\n'.join(md), encoding='utf-8')

    print(f'✅ cardinalities.json 생성: {OUT_PATH}')
    print(f'   프로파일: {profile_name} (scale={scale})')
    print(f'   테이블: {len(all_tables):,}  총 레코드: {total_records:,}')
    print(f'\n📊 Source별:')
    for src, cnt in sorted(source_counter.items(), key=lambda x: -x[1]):
        print(f'   {src:20s} {cnt:6,}')


if __name__ == '__main__':
    main()
