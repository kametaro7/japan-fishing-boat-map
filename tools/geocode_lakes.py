#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""座標の無い「湖・ダム・沼・潟・川」のガイド船（RESERVER など）の位置を、OpenStreetMap Nominatim で引く。

  python3 tools/geocode_lakes.py

国土地理院の住所検索は湖やダムの名前を引けないため。Nominatim の利用規約に従い、1秒以上の間隔・User-Agent 明示・結果のキャッシュをする。
結果は work/geocode/lakes.json に「<都道府県>|<港（湖）名>」→ {lat, lon, name, class, type}。見つからないものは null。
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
from common import WORK, load_json, save_json, nfkc, to_pref  # noqa: E402

OUT = os.path.join(WORK, 'geocode', 'lakes.json')
UA = 'japan-fishing-boat-map/1.0 (personal map project; contact via github.com/kametaro7)'
WATER = re.compile(r'湖|ダム|沼|潟|川|池|貯水池')
OK_CLASSES = {('natural', 'water'), ('water', 'lake'), ('water', 'reservoir'), ('landuse', 'reservoir'), ('waterway', 'river'),
              ('waterway', 'dam'), ('man_made', 'dam'), ('natural', 'wetland'), ('water', 'river'), ('water', 'pond')}


def query(q):
    url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(
        {'q': q, 'format': 'jsonv2', 'countrycodes': 'jp', 'limit': 5, 'accept-language': 'ja'})
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode('utf-8'))


def main():
    cache = load_json(OUT, {}) or {}
    wanted = set()
    for path in glob.glob(os.path.join(WORK, 'sources', '*.json')):
        base = os.path.basename(path)
        if base.startswith('_') or '.sample' in base or '_' in base.split('.')[0]:
            continue
        for r in load_json(path, []) or []:
            if not isinstance(r, dict) or r.get('lat') not in (None, ''):
                continue
            port, pref = nfkc(r.get('port')), to_pref(r.get('pref'))
            if port and pref and WATER.search(port) and not re.search(r'港|漁港|マリーナ', port):
                wanted.add((pref, port))
    # OSM での名前が違うもの（見つからなかったときに別名でも引く）
    aliases = {'八郎潟': ['八郎潟調整池', '八郎潟残存湖'], '神流湖': ['下久保ダム'], '七色ダム': ['七色貯水池', '七色湖']}
    todo = [w for w in sorted(wanted) if '%s|%s' % w not in cache or (cache['%s|%s' % w] is None and w[1] in aliases)]
    print('lakes/dams/rivers to look up: %d (cached %d)' % (len(todo), len(wanted) - len(todo)))
    for pref, port in todo:
        name = re.sub(r'[（(].*?[)）]', '', port)
        hit = None
        names = [name] + aliases.get(name, [])
        for q in [x for n in names for x in ('%s %s' % (n, pref), n)]:
            try:
                res = query(q)
            except Exception as e:
                print('  error', q, e)
                res = []
            time.sleep(1.2)
            for x in res:
                if (x.get('category'), x.get('type')) in OK_CLASSES and pref in (x.get('display_name') or ''):
                    hit = {'lat': round(float(x['lat']), 5), 'lon': round(float(x['lon']), 5), 'name': x.get('name'),
                           'class': x.get('category'), 'type': x.get('type'), 'display': x.get('display_name')}
                    break
            if hit:
                break
        cache['%s|%s' % (pref, port)] = hit
        print('  %s %s -> %s' % (pref, port, '%s (%s/%s) %.4f,%.4f' % (hit['name'], hit['class'], hit['type'], hit['lat'], hit['lon']) if hit else 'not found'))
        save_json(OUT, cache, indent=1)
    save_json(OUT, cache, indent=1)
    print('done: found %d / %d' % (sum(1 for v in cache.values() if v), len(cache)))


if __name__ == '__main__':
    main()
