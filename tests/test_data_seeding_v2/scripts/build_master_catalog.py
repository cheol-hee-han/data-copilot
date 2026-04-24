#!/usr/bin/env python3
"""
build_master_catalog.py
=======================
catalog_v2.json을 진실 기준으로 02_MASTER_CATALOG.md를 재생성한다.
Phase B 이후 drift(유형코드 변경, 신설 테이블 등)를 모두 반영한다.
"""
import json
import re
from pathlib import Path
from collections import defaultdict, OrderedDict
from datetime import datetime

BANK_DIR = Path("/home/claude/bank_v2")
CATALOG_PATH = BANK_DIR / "meta" / "catalog_v2.json"
OUT_PATH = BANK_DIR / "02_MASTER_CATALOG.md"


# 주제영역 그룹 정의 (00_README_PLAN.md 기반)
# 그룹 번호 → (제목, [도메인 prefix 리스트])
SECTION_GROUPS = [
    ('공통·조직·코드·시스템', ['CMI', 'CMO', 'CMS']),
    ('고객·CIF·신용평가', ['CSC', 'CSI', 'CSK']),
    ('상품·약관·금리', ['PFP', 'PFR', 'PFC']),
    ('수신', ['DPG', 'DPF', 'DPD', 'DPB', 'DPN', 'DPY']),
    ('여신', ['LNB', 'LNH', 'LNJ', 'LNC', 'LNK', 'LNW', 'LNO']),
    ('담보·보증', ['LNM', 'LNG']),
    ('카드 회원', ['CLN']),
    ('카드 매출·정산', ['SLE']),
    ('외환', ['FXC', 'FXR', 'FXD']),
    ('전자금융', ['EBB', 'EBM', 'EBA', 'EBO', 'EBS']),
    ('퇴직연금', ['RPC', 'RPD', 'RPI']),
    ('신탁·펀드', ['TRS', 'FND']),
    ('투자·파생', ['INV', 'DRV']),
    ('재무·결산', ['FNA', 'FNB', 'FNS']),
    ('리스크·규제', ['RSK', 'RPT', 'AML']),
    ('마케팅·CRM', ['MKT', 'CMG', 'NBA']),
    ('마트', ['MVP', 'MVN', 'MVC', 'MVF', 'MVB', 'MRC', 'MRP', 'MRO', 'MRR']),
]


def get_prefix(tid):
    m = re.match(r'([A-Z]+)\d', tid)
    return m.group(1) if m else ''


def main():
    catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    tables = catalog['tables']

    # 테이블을 prefix별로 그룹화
    by_prefix = defaultdict(list)
    for t in tables:
        short = t['table_id'].replace('TB_ADW_', '')
        pfx = get_prefix(short)
        by_prefix[pfx].append({
            'short': short,
            'name_kor': t.get('table_kor', ''),
        })

    # 각 prefix 내에서는 테이블 ID 순 정렬
    for pfx in by_prefix:
        by_prefix[pfx].sort(key=lambda x: x['short'])

    # 섹션별 총합 계산
    section_totals = []
    for title, prefixes in SECTION_GROUPS:
        count = sum(len(by_prefix.get(p, [])) for p in prefixes)
        section_totals.append(count)

    total = sum(section_totals)

    # MD 생성
    md = [
        '# 02. 마스터 테이블 카탈로그 (v2) — 자동 생성',
        '',
        f'**{total:,} 테이블** 전체 인벤토리 (이름 + 한글명).',
        f'catalog_v2.json에서 자동 재생성 ({datetime.now().isoformat(timespec="minutes")}).',
        '상세 컬럼 정의는 주제영역별 파일(`10_*` ~ `C8_*`)에서 확인.',
        '',
        '**유형코드:** M(Master) L(Log거래) H(History이력) S(Summary집계) P(snaP샷) C(Code) T(Task작업) G(loG) D(Detail)',
        '',
        '---',
        '',
    ]

    for (title, prefixes), subtotal in zip(SECTION_GROUPS, section_totals):
        sec_num = SECTION_GROUPS.index((title, prefixes)) + 1
        md.append(f'## {sec_num}. {title} ({subtotal})')
        md.append('')
        for pfx in prefixes:
            entries = by_prefix.get(pfx, [])
            if not entries:
                continue
            if len(prefixes) > 1:
                md.append(f'### {pfx} ({len(entries)})')
                md.append('')
            for e in entries:
                md.append(f'- `{e["short"]}` {e["name_kor"]}')
            md.append('')
        md.append('')

    # 요약 표
    md.append('---')
    md.append('')
    md.append('## 주제영역별 합계')
    md.append('')
    md.append('| # | 주제영역 | 계획 | 실제 |')
    md.append('|---|---|---|---|')

    # 원래 계획 카운트 (00_README_PLAN.md 기반)
    planned = [50, 80, 55, 145, 210, 45, 50, 70, 85, 75, 50, 45, 50, 90, 65, 70, 200]
    for i, ((title, prefixes), subtotal) in enumerate(zip(SECTION_GROUPS, section_totals)):
        plan = planned[i] if i < len(planned) else '-'
        md.append(f'| {i+1} | {title} ({"/".join(prefixes)}) | {plan} | {subtotal} |')

    planned_total = sum(planned)
    md.append(f'| | **합계** | **{planned_total:,}** | **{total:,}** |')
    md.append('')
    md.append(f'Phase B 신설 및 유형 재분류로 계획 {planned_total:,} → 실제 **{total:,}** ({"+" if total > planned_total else ""}{total - planned_total})')

    OUT_PATH.write_text('\n'.join(md), encoding='utf-8')
    print(f'✅ 02_MASTER_CATALOG.md 재생성 ({total:,} 테이블)')
    print(f'   크기: {OUT_PATH.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
