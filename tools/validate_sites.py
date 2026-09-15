#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公式サイトのトップページだけを取得して、失効・乗っ取り・無関係のサイトでないかを判定する。

  python3 tools/validate_sites.py

料金ページまで取る tools/fetch_official.py は「料金の無い船宿」だけが対象なので、料金を予約サイトなどから
得ている船宿の公式サイトは確認されていなかった（例: 釣り野郎に載っている esa-tosen.com がギャンブル系スパムに）。
対象: data/detail/*.json の website のうち、work/official/pages にまだ無いもの。
結果: work/official/validate.json  {website: {state: ok|suspect|thin|unreachable, reason, final, title}}
build.py は suspect のリンクを外す。unreachable は tools/check_links.py が curl で確かめ直す。
取得・キャッシュ・判定の規則は fetch_official.py と共通（1ホスト直列・1秒間隔・robots.txt）。
"""
import concurrent.futures
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, WORK, load_json, save_json, url_key  # noqa: E402
import fetch_official as fo  # noqa: E402

OUT = os.path.join(WORK, 'official', 'validate.json')
LOG = os.path.join(WORK, 'logs', 'validate.log')


def log(msg):
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + msg + '\n')


def run(site):
    final, html = fo.fetch(site['website'])
    if not html:
        return site, {'state': 'unreachable', 'reason': '取得できませんでした', 'final': final}
    title, text = fo.page_text(html)
    state, reason = fo.assess({'pages': [{'url': final, 'title': title, 'text': text}], 'boats': site['boats']})
    return site, {'state': state, 'reason': reason, 'final': final, 'title': (title or '')[:80]}


def main():
    have = set()
    for p in glob.glob(os.path.join(WORK, 'official', 'pages', '*.json')):
        d = load_json(p, {}) or {}
        if d.get('website'):
            have.add(url_key(d['website']))
    sites = {}
    for p in glob.glob(os.path.join(ROOT, 'data', 'detail', '*.json')):
        for bid, b in (load_json(p, {}) or {}).items():
            w = b.get('website')
            k = url_key(w) if w else None
            if not k or k in have:
                continue
            sites.setdefault(k, {'website': w, 'boats': []})['boats'].append({'id': bid, 'name': b.get('name')})
    out = load_json(OUT, {}) or {}
    # --reassess: 判定規則を変えたときに、キャッシュ済みの HTML から全サイトを判定し直す（ネット取得はキャッシュに無い分だけ）
    todo = [s for s in sites.values() if '--reassess' in sys.argv or s['website'] not in out]
    if '--reassess' in sys.argv:
        # 前回の判定で build.py がリンクを外したサイトは data/detail に出てこないので、保存済みの判定からも拾って判定し直す
        linked = {s['website'] for s in sites.values()}
        todo += [{'website': u, 'boats': []} for u in out if u not in linked]
    log('start: %d sites to validate (%d not in pages, %d already validated)' % (len(todo), len(sites), len(sites) - len(todo)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for i, (site, res) in enumerate(ex.map(run, todo), 1):
            out[site['website']] = res
            if i % 50 == 0:
                save_json(OUT, out, indent=1)
                log('progress %d/%d' % (i, len(todo)))
    save_json(OUT, out, indent=1)
    counts = {}
    for s in sites.values():
        st = out.get(s['website'], {}).get('state')
        counts[st] = counts.get(st, 0) + 1
    log('DONE %d sites validated: %s' % (len(sites), counts))
    print('validated %d sites: %s' % (len(sites), counts))


if __name__ == '__main__':
    main()
