#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公式サイトから抽出した結果（work/enrich/*.json）を、掲載サイトレコード形式の work/sources/official.json にまとめる。

  python3 tools/official_to_source.py

入力の各ファイルは配列で、要素は1サイト分:
  {"key": "<work/official/pages のキー>", "usable": true, "reason": "",
   "types": ["乗合"], "targets": ["マダイ"], "holidays": "", "schedule_text": "",
   "plans": [ SPEC の plan。url は根拠のページ ], "evidence_urls": ["..."]}
船宿名・電話・公式サイトURLは work/official/pages/<key>.json の値を使うので、build.py では
公式サイトURL・電話でもとの船宿と名寄せされる。usable=false（失効・無関係・料金情報なし）の結果は、
失効・無関係のときだけ work/official/unusable.json に記録し、build.py のリンク除外に使う。
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, load_json, save_json, nfkc  # noqa: E402

TODAY = '2026-09-15'


def main():
    out, bad, seen = [], [], set()
    n_in = 0
    for path in sorted(glob.glob(os.path.join(WORK, 'enrich', 'batch_*.json'))):
        for e in load_json(path, []) or []:
            n_in += 1
            key = e.get('key')
            if not key or key in seen:
                continue
            page = load_json(os.path.join(WORK, 'official', 'pages', key + '.json'), {}) or {}
            boats = page.get('boats') or []
            if not page.get('website') or not boats:
                continue
            seen.add(key)
            if not e.get('usable', True):
                if e.get('dead'):
                    bad.append({'key': key, 'website': page['website'], 'reason': e.get('reason')})
                continue
            plans = [p for p in (e.get('plans') or []) if isinstance(p, dict)]
            info = plans or e.get('schedule_text') or e.get('holidays') or e.get('targets')
            if not info:
                continue
            b = boats[0]
            # 県・港・電話は「元の船宿と名寄せするための鍵」として入れる（URL だけだと、共用ホストのサイトや、
            # 後の統合で別の公式サイトが代表になった船宿とまとまらない）。表示や船宿IDに使われないよう、
            # work/sources/_labels.json で公式サイトの優先度を最低（5）にしてある。
            out.append({
                'src': 'official',
                'src_id': key,
                'src_url': page['website'],
                'name': b.get('name'),
                'pref': b.get('pref'),
                'port': b.get('port'),
                'tel': b.get('tel'),
                'website': page['website'],
                'types': e.get('types') or [],
                'targets': e.get('targets') or [],
                'holidays': nfkc(e.get('holidays'))[:80],
                'schedule_text': nfkc(e.get('schedule_text'))[:300],
                'plans': plans,
                'fetched': TODAY,
            })
    save_json(os.path.join(WORK, 'sources', 'official.json'), out, indent=1)
    save_json(os.path.join(WORK, 'official', 'unusable.json'), bad, indent=1)
    print('enrich results %d -> official records %d (with plans %d), dead sites %d'
          % (n_in, len(out), sum(1 for r in out if r['plans']), len(bad)))


if __name__ == '__main__':
    main()
