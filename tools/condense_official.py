#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""取得済みの公式サイト本文（work/official/pages/*.json）から、料金・出船時刻・定休日まわりの行だけを抜き出して短くする。

  python3 tools/condense_official.py

出力: work/official/condensed/<key>.txt（抽出担当が読む材料。1サイト最大 MAX_SITE 文字）
      work/official/condensed_index.json（キー・文字数・「円」の有無）
抽出を担当するエージェントの読み込み量を減らすためのもの。数字の検証は tools/verify_enrich.py が原文（pages）で行う。
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, load_json, save_json  # noqa: E402

KEY = re.compile(r'円|￥|¥|料金|運賃|乗船料|乗合|乗り合い|仕立|貸切|貸し切り|チャーター|出船|出航|沖上がり|沖揚がり|帰港|集合|受付|定休|休業|休み|'
                 r'\d{1,2}[:：]\d{2}|\d{1,2}時|名様|お一人|1名|１名|大人|子供|小学生|女性|エサ|餌|氷|シーズン|期間|釣り物|釣物|ターゲット|便|午前|午後|夜|半日|一日|1日')
CONTEXT = 2
MAX_PAGE = 3500
MAX_SITE = 7000
HEAD = 300


def condense_text(text):
    lines = text.split('\n')
    keep = set()
    for i, line in enumerate(lines):
        if KEY.search(line):
            for j in range(max(0, i - CONTEXT), min(len(lines), i + CONTEXT + 1)):
                keep.add(j)
    out, prev = [], -2
    for i in sorted(keep):
        if i != prev + 1 and out:
            out.append('…')
        line = lines[i]
        out.append(line if len(line) <= 300 else line[:300] + '…')
        prev = i
    return '\n'.join(out)


def main():
    out_dir = os.path.join(WORK, 'official', 'condensed')
    os.makedirs(out_dir, exist_ok=True)
    index = {x['key']: x for x in (load_json(os.path.join(WORK, 'official', 'index.json'), []) or [])}
    rows, total = [], 0
    for path in sorted(glob.glob(os.path.join(WORK, 'official', 'pages', '*.json'))):
        d = load_json(path, {}) or {}
        key = d.get('key')
        if not key or index.get(key, {}).get('state') not in ('ok', None):
            continue
        pages = d.get('pages') or []
        names = ' / '.join('%s（%s %s）' % (b.get('name'), b.get('pref') or '', b.get('port') or '') for b in d.get('boats', []))
        parts = ['# key: %s' % key, '# website: %s' % d.get('website'), '# 船宿: %s' % names]
        if pages:
            parts.append('## 冒頭（%s）' % pages[0].get('title', ''))
            parts.append(pages[0]['text'][:HEAD])
        size = sum(len(p) for p in parts)
        for p in pages:
            c = condense_text(p['text'])[:MAX_PAGE]
            if not c.strip():
                continue
            block = '## %s（%s）\n%s' % (p['url'], p.get('title', ''), c)
            if size + len(block) > MAX_SITE:
                block = block[:max(0, MAX_SITE - size)]
                if len(block) < 200:
                    break
            parts.append(block)
            size += len(block)
        text = '\n'.join(parts)
        with open(os.path.join(out_dir, key + '.txt'), 'w', encoding='utf-8') as f:
            f.write(text)
        rows.append({'key': key, 'chars': len(text), 'has_yen': '円' in text})
        total += len(text)
    save_json(os.path.join(WORK, 'official', 'condensed_index.json'), rows, indent=1)
    print('condensed %d sites, total %d chars (avg %d), with 円 %d' % (len(rows), total, total // max(1, len(rows)), sum(r['has_yen'] for r in rows)))


if __name__ == '__main__':
    main()
