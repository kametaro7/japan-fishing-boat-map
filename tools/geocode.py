#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""座標の無いレコードの住所を、国土地理院の住所検索API（msearch.gsi.go.jp）で座標にする。

  python3 tools/geocode.py [--limit N]

結果は work/geocode/gsi_cache.json に「問い合わせ文字列 → {lat, lon, title, level}」で貯め、
tools/build.py はこのキャッシュだけを読む。見つからなかった問い合わせは null を記録する。
"""
import glob
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, PREFS, load_json, save_json, nfkc, to_pref, address_queries, town_level  # noqa: E402

CACHE = os.path.join(WORK, 'geocode', 'gsi_cache.json')
LOG = os.path.join(WORK, 'logs', 'geocode.log')
API = 'https://msearch.gsi.go.jp/address-search/AddressSearch?q='
INTERVAL = 0.35


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(time.strftime('%H:%M:%S ') + msg + '\n')


def gsi(q):
    url = API + urllib.parse.quote(q)
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 japan-fishing-boat-map'})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:  # 通信エラーは待って再試行
            log('retry %d %s: %s' % (attempt, q, e))
            time.sleep(2 ** attempt)
    return None


def common_prefix(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def pick(q, feats):
    """候補のうち、問い合わせと先頭が最も長く一致するものを選ぶ。"""
    pref = to_pref(q)
    best, best_n = None, -1
    for f in feats or []:
        try:
            title = nfkc(f['properties']['title'])
            lon, lat = f['geometry']['coordinates']
        except (KeyError, TypeError, ValueError):
            continue
        if pref and not title.startswith(pref):
            continue
        if len(title[len(pref or ''):]) < 2:
            continue  # 都道府県名だけの一致は位置として使えない（県の中心に置かれてしまう）
        n = common_prefix(title, q)
        if n > best_n:
            best, best_n = (title, float(lat), float(lon)), n
    if not best:
        return None
    title, lat, lon = best
    tl = town_level(q)
    rest = title[len(pref or ''):]
    if best_n >= len(tl) - 1:
        level = 'town'
    elif re.search(r'(市|郡.+?[町村]|区)$', rest) or re.fullmatch(r'.+?[市町村区]', rest or ''):
        level = 'city'
    else:
        level = 'town' if best_n >= len(pref or '') + 3 else 'city'
    return {'lat': round(lat, 6), 'lon': round(lon, 6), 'title': title, 'level': level}


def records():
    for path in sorted(glob.glob(os.path.join(WORK, 'sources', '*.json'))):
        base = os.path.basename(path)
        if base.startswith('_') or '.sample' in base or '_' in base.split('.')[0]:
            continue  # サンプル版と補助ファイル（funaduri_closed / fishingv_excluded など）は除く
        data = load_json(path, []) or []
        if isinstance(data, dict):
            data = data.get('records') or data.get('boats') or []
        for r in data:
            if isinstance(r, dict) and r.get('lat') in (None, '') and (r.get('address') or r.get('city')):
                yield r
    for path in sorted(glob.glob(os.path.join(WORK, 'registry', '[0-9][0-9].json'))):
        for r in load_json(path, []) or []:
            if isinstance(r, dict) and r.get('address'):
                yield r


def main():
    limit = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else None
    cache = load_json(CACHE, {}) or {}
    todo = []
    seen = set()
    for r in records():
        pref = to_pref(r.get('pref')) or to_pref(r.get('address'))
        qs = address_queries(r.get('address'), pref, r.get('city'), r.get('port'))
        if not qs:
            continue
        key = qs[0]
        if key in seen:
            continue
        seen.add(key)
        if any(isinstance(cache.get(q), dict) for q in qs) or all(q in cache for q in qs):
            continue
        todo.append(qs)
    if limit:
        todo = todo[:limit]
    log('start: %d addresses to geocode (cache %d)' % (len(todo), len(cache)))
    calls = 0
    for i, qs in enumerate(todo):
        for q in qs:
            if q in cache:
                if isinstance(cache[q], dict):
                    break
                continue
            feats = gsi(q)
            calls += 1
            time.sleep(INTERVAL)
            if feats is None:
                break  # 通信失敗は記録せず次回に回す
            cache[q] = pick(q, feats)
            if cache[q]:
                break
        if calls and calls % 100 == 0:
            save_json(CACHE, cache)
        if (i + 1) % 200 == 0:
            log('%d/%d' % (i + 1, len(todo)))
    save_json(CACHE, cache)
    log('DONE %d addresses, %d calls, cache %d' % (len(todo), calls, len(cache)))


if __name__ == '__main__':
    main()
