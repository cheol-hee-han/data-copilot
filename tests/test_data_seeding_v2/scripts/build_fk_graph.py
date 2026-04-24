#!/usr/bin/env python3
"""
build_fk_graph.py
=================
catalog_v2.json에서 FK 엣지를 추출해 테이블 생성 순서 DAG를 생성한다.

핵심 개념:
  - src가 ref를 참조한다 ⇒ ref를 먼저 생성해야 src 생성 가능
  - 위상 정렬(Kahn's algorithm)로 레벨별 생성 순서 결정
  - Self-reference는 DAG에서 제외 (시딩 시 2-pass 처리)
  - 순환 참조는 경고

산출물:
  - meta/fk_graph.json       : 위상 정렬 + 레벨별 그룹 + 엣지 목록
  - meta/fk_graph_stats.md   : DAG 통계 리포트
"""
import json
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

BANK_DIR = Path("/home/claude/bank_v2")
META_DIR = BANK_DIR / "meta"
CATALOG_PATH = META_DIR / "catalog_v2.json"
GRAPH_PATH = META_DIR / "fk_graph.json"
STATS_PATH = META_DIR / "fk_graph_stats.md"


def main():
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    tables = catalog['tables']

    # ── 테이블 매핑 (TB_ADW_XXX ↔ XXX) ──
    table_by_short = {}
    for t in tables:
        short = t['table_id'].replace('TB_ADW_', '')
        table_by_short[short] = t
    all_tables = set(table_by_short.keys())

    # ── 엣지 수집 ──
    edges = defaultdict(set)       # src → set of ref_tables
    edge_details = []              # [{from, to, via_column, source}]
    self_refs = []                 # [{table, column}]
    missing_refs = []              # FK가 가리키는 테이블이 catalog에 없음

    for t in tables:
        src = t['table_id'].replace('TB_ADW_', '')
        for c in t['columns']:
            fk = c.get('fk')
            if not fk:
                continue
            ref = fk['table']
            # Self-reference (DAG 제외)
            if ref == src:
                self_refs.append({
                    'table': src,
                    'column': c['name'],
                    'via_ref_column': fk.get('column'),
                    'source': fk.get('source')
                })
                continue
            # 참조 대상 부재
            if ref not in table_by_short:
                missing_refs.append({
                    'from': src,
                    'to': ref,
                    'column': c['name'],
                    'source': fk.get('source')
                })
                continue
            edges[src].add(ref)
            edge_details.append({
                'from': src,
                'to': ref,
                'via_column': c['name'],
                'via_ref_column': fk.get('column'),
                'source': fk.get('source')
            })

    # ── 역 그래프 + 입력 차수 ──
    reverse_edges = defaultdict(set)   # ref → set of src들
    for src, refs in edges.items():
        for ref in refs:
            reverse_edges[ref].add(src)

    # ── 상호 참조 감지 (A↔B) → Deferred FK로 분류 ──
    # 정당한 쌍둥이 관계(STR/CTR 등)는 시딩 시 2-pass로 처리
    mutual_pairs = set()
    for src, refs in edges.items():
        for ref in refs:
            if src in edges.get(ref, set()) and src != ref:
                # 정렬된 튜플로 중복 방지
                mutual_pairs.add(tuple(sorted([src, ref])))

    # Deferred edges: 각 상호 참조 쌍에서 한 방향만 제거
    # 규칙: table_id 알파벳순 "뒤"에서 "앞"으로 가는 엣지를 deferred로 분류
    #  → 즉 "앞"이 먼저 생성되고, "뒤"가 나중에 생성되며 deferred 업데이트
    deferred_edges = []
    edges_filtered = {src: set(refs) for src, refs in edges.items()}
    for a, b in mutual_pairs:
        # a < b (알파벳)이므로 a가 먼저 생성. b → a 엣지는 유지, a → b 엣지는 deferred
        # (b가 생성된 후 a의 b 참조 컬럼을 UPDATE)
        if b in edges_filtered.get(a, set()):
            edges_filtered[a].discard(b)
            # deferred edge detail 찾기
            for e in edge_details:
                if e['from'] == a and e['to'] == b:
                    deferred_edges.append(e)

    # Deferred 제거 후 역그래프·입력차수 재계산
    reverse_edges_f = defaultdict(set)
    in_degree = defaultdict(int)
    for src, refs in edges_filtered.items():
        in_degree[src] = len(refs)
        for ref in refs:
            reverse_edges_f[ref].add(src)
    reverse_edges = reverse_edges_f

    # ── 위상 정렬 (Kahn's algorithm, 레벨별) ──
    # Level 0 = 아무것도 참조하지 않음 (마스터 테이블)
    # Level N = Level 0~N-1을 참조
    levels = []
    processed = set()
    in_degree_work = {t: in_degree.get(t, 0) for t in all_tables}
    current_level_tables = sorted([t for t in all_tables if in_degree_work[t] == 0])

    while current_level_tables:
        levels.append(current_level_tables)
        next_level = set()
        for t in current_level_tables:
            processed.add(t)
            for dependent in reverse_edges.get(t, set()):
                if dependent in processed:
                    continue
                in_degree_work[dependent] -= 1
                if in_degree_work[dependent] == 0:
                    next_level.add(dependent)
        current_level_tables = sorted(next_level)

    # 남은 테이블 = 순환 참조에 포함
    cyclic_tables = sorted(all_tables - processed)

    # ── 고립 테이블 (참조 없고 피참조 없음) ──
    isolated_tables = sorted([
        t for t in all_tables
        if len(edges.get(t, set())) == 0 and len(reverse_edges.get(t, set())) == 0
    ])

    # ── Top 피참조/참조 테이블 ──
    most_referenced = sorted(
        [(t, len(refs)) for t, refs in reverse_edges.items()],
        key=lambda x: -x[1]
    )[:25]
    most_referencing = sorted(
        [(t, len(refs)) for t, refs in edges.items()],
        key=lambda x: -x[1]
    )[:25]

    # ── Missing refs 집계 (참조 대상 테이블별) ──
    missing_targets = Counter(r['to'] for r in missing_refs).most_common(25)

    # ── 위상 순서 평탄화 ──
    topological_order = [t for lv in levels for t in lv]

    # ── JSON 저장 ──
    graph = {
        'version': 1,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'stats': {
            'tables': len(all_tables),
            'fk_column_count': len(edge_details),
            'unique_table_edges': sum(len(r) for r in edges.values()),
            'deferred_edges': len(deferred_edges),
            'mutual_pairs': len(mutual_pairs),
            'self_references': len(self_refs),
            'missing_references': len(missing_refs),
            'unique_missing_targets': len(set(r['to'] for r in missing_refs)),
            'levels': len(levels),
            'isolated_tables': len(isolated_tables),
            'cyclic_tables': len(cyclic_tables),
        },
        'levels': [
            {'level': i, 'count': len(lv), 'tables': lv}
            for i, lv in enumerate(levels)
        ],
        'topological_order': topological_order,
        'cyclic_tables': cyclic_tables,
        'isolated_tables': isolated_tables,
        'self_references': self_refs,
        'deferred_edges': deferred_edges,
        'mutual_pairs': [list(p) for p in sorted(mutual_pairs)],
        'missing_references_sample': missing_refs[:50],
        'edges': edge_details,
    }
    GRAPH_PATH.write_text(
        json.dumps(graph, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    # ── Markdown 리포트 ──
    md = [
        '# fk_graph.json — 테이블 생성 DAG 리포트',
        '',
        f'생성: {graph["generated_at"]}',
        '',
        '## 핵심 요약',
        f'- 전체 테이블: **{len(all_tables):,}**',
        f'- 유니크 테이블간 FK 엣지: **{graph["stats"]["unique_table_edges"]:,}**',
        f'- FK 컬럼 수: **{len(edge_details):,}**',
        f'- 의존성 레벨: **{len(levels)}**',
        f'- 고립 테이블: **{len(isolated_tables):,}** ({round(len(isolated_tables)/len(all_tables)*100,1)}%)',
        f'- 순환 참조 테이블: **{len(cyclic_tables):,}**',
        f'- Self-reference 컬럼: **{len(self_refs):,}**',
        f'- 상호참조 쌍 (deferred): **{len(mutual_pairs):,}**',
        f'- Deferred 엣지 (2-pass 시딩): **{len(deferred_edges):,}**',
        f'- 참조 대상 누락: **{len(missing_refs):,}** ({len(set(r["to"] for r in missing_refs))}개 고유 테이블)',
        '',
    ]

    if mutual_pairs:
        md.extend([
            '## 🔁 상호참조 쌍 (Deferred FK, 2-pass 시딩 대상)',
            '',
            '| 먼저 생성 | 나중에 생성 | 비고 |',
            '|---|---|---|',
        ])
        for a, b in sorted(mutual_pairs):
            md.append(f'| `{a}` | `{b}` | 쌍둥이 관계, 2-pass 업데이트 필요 |')
        md.append('')

    md.extend([
        '## 레벨별 테이블 분포',
        '| level | 테이블 수 | 대표 (최대 8개) |',
        '|---|---|---|',
    ])
    for i, lv in enumerate(levels):
        sample = ', '.join(lv[:8])
        if len(lv) > 8:
            sample += f', ... (+{len(lv)-8})'
        md.append(f'| {i} | {len(lv):,} | {sample} |')

    md.extend([
        '',
        '## Top 25 피참조 테이블 (중심 마스터)',
        '| 테이블 | 피참조 수 |',
        '|---|---|',
    ])
    for t, cnt in most_referenced:
        md.append(f'| `{t}` | {cnt} |')

    md.extend([
        '',
        '## Top 25 참조 테이블 (FK 많이 가진)',
        '| 테이블 | 외래참조 수 |',
        '|---|---|',
    ])
    for t, cnt in most_referencing:
        md.append(f'| `{t}` | {cnt} |')

    if cyclic_tables:
        md.extend([
            '',
            '## ⚠️ 순환 참조 테이블 (DAG 불가)',
            f'총 **{len(cyclic_tables)}개** — 시딩 시 수동 순서 결정 또는 FK 제약 분리 필요',
            '',
        ])
        for t in cyclic_tables[:30]:
            md.append(f'- `{t}`')

    if missing_refs:
        md.extend([
            '',
            f'## ⚠️ 참조 대상 누락 ({len(missing_refs):,}건)',
            'FK가 가리키는 테이블이 catalog에 없음 → FK 규칙 오탐 의심',
            '',
            '### 누락 대상 테이블 Top 25',
            '| 누락 테이블 | 참조 건수 |',
            '|---|---|',
        ])
        for t, cnt in missing_targets:
            md.append(f'| `{t}` | {cnt} |')

    STATS_PATH.write_text('\n'.join(md), encoding='utf-8')

    # ── 콘솔 ──
    print(f'✅ fk_graph.json 생성: {GRAPH_PATH}')
    print(f'   테이블: {len(all_tables):,}  엣지: {graph["stats"]["unique_table_edges"]:,}  레벨: {len(levels)}')
    print(f'   고립: {len(isolated_tables):,}  순환: {len(cyclic_tables):,}  Self-ref: {len(self_refs):,}')
    print(f'   누락 참조: {len(missing_refs):,}')
    print(f'\n📊 리포트: {STATS_PATH}')


if __name__ == '__main__':
    main()
