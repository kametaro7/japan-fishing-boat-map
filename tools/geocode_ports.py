"""港・乗り場の名前から座標を引く（OSM Nominatim）。掲載元が事業者の住所しか持っていない船宿を、港の位置に置き直すために使う。

対象は「同じ県・同じ港のほかの船宿の座標が無く、住所から求めた位置しか無い」港。
引いた座標は、都道府県名が一致し、その県のポリゴン（work/geo/japan.geojson）の中か5km以内のものだけ採用する
（「鷹巣漁港 福井県」で福岡県の道路が返るなど、名前だけの一致が起きるため）。

  python3 tools/geocode_ports.py            # work/geocode/ports.json に追記（既に引いたものは飛ばす）
  python3 tools/geocode_ports.py --refresh  # 引き直す
  python3 tools/geocode_ports.py --verify   # 引いた座標を点検し、疑わしいものを外す（build.py が読む前に必ず実行）
  python3 tools/geocode_ports.py --osm      # OSM の港一覧（work/geo/osm_ports.json）→ work/geocode/ports_osm.json
"""
import glob
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from build import port_key  # noqa: E402
from coastline import coast_km, in_pref  # noqa: E402
from common import dist_km, nfkc  # noqa: E402

OUT = os.path.join(ROOT, 'work/geocode/ports.json')
UA = 'japan-fishing-boat-map/1.0 (https://github.com/kametaro7/japan-fishing-boat-map)'
INTERVAL = 1.2
OK_CLASS = ('place', 'natural', 'man_made', 'waterway', 'leisure', 'landuse', 'amenity', 'boundary', 'building')
LAKE_RE = re.compile(r'湖|ダム|沼|池|貯水池')


def targets():
    """住所からしか位置を出せていない港（同じ港のほかの船の座標が無いもの）を集める。"""
    boats = {}
    for f in glob.glob(os.path.join(ROOT, 'data/detail/*.json')):
        boats.update(json.load(open(f, encoding='utf-8')))
    site = {}
    for b in boats.values():
        if b.get('geo') == 'site' and b.get('port'):
            site.setdefault((b['pref'], port_key(b['port'])), []).append(b['id'])
    out = {}
    for b in boats.values():
        if not b.get('port') or b.get('geo') not in ('town', 'city'):
            continue
        key = (b['pref'], port_key(b['port']))
        if len(site.get(key, [])) >= 2:
            continue  # 港の座標はほかの船から求められる
        out.setdefault('%s|%s' % key, {'pref': b['pref'], 'port': nfkc(b['port']), 'city': b.get('city'), 'n': 0})['n'] += 1
    return out


def search(q):
    url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode(
        {'format': 'json', 'limit': '5', 'countrycodes': 'jp', 'accept-language': 'ja', 'q': q})
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as fp:
        return json.load(fp)


def pick(results, pref):
    for r in results:
        if pref not in r.get('display_name', ''):
            continue
        if r.get('class') not in OK_CLASS:
            continue
        lat, lon = float(r['lat']), float(r['lon'])
        if not in_pref(pref, lat, lon, 5.0):
            continue
        return {'lat': lat, 'lon': lon, 'class': r.get('class'), 'type': r.get('type'), 'name': r.get('display_name')}
    return None


# OSM の種類ごとの信頼度。港・水域・地名なら名前の一致だけで採れるが、建物や道路は取り違えが多い
TIER1 = (('natural', ''), ('man_made', ''), ('waterway', ''), ('leisure', 'marina'), ('leisure', 'slipway'),
         ('amenity', 'ferry_terminal'), ('landuse', 'harbour'), ('landuse', 'port'))


def tier_of(g):
    c, t = g.get('class'), g.get('type')
    if any(c == a and (b == '' or t == b) for a, b in TIER1):
        return 1
    return 2 if c == 'place' else 3


def verify(argv):
    """引いた港の座標を点検し、採用できないものを ports_rejected.json へ移す。"""
    cache = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    boats = {}
    for f in glob.glob(os.path.join(ROOT, 'data/detail/*.json')):
        boats.update(json.load(open(f, encoding='utf-8')))
    users = {}
    for b in boats.values():
        if b.get('port'):
            users.setdefault('%s|%s' % (b['pref'], port_key(b['port'])), []).append((b['lat'], b['lon']))
    kept, rejected = {}, {}
    for key, g in cache.items():
        if not isinstance(g, dict):
            kept[key] = None
            continue
        pref, pkey = key.split('|', 1)
        lat, lon = g['lat'], g['lon']
        tier = tier_of(g)
        near_users = min([dist_km(lat, lon, a, b) for a, b in users.get(key, [])] or [0.0])
        coast = coast_km(lat, lon)
        water_name = bool(LAKE_RE.search(g.get('name') or '')) or bool(re.search(r'川|橋|運河', pkey))
        why = None
        if not in_pref(pref, lat, lon, 1.0):
            why = '%s の外' % pref
        elif tier == 3 and len(pkey) <= 2:
            why = '短い港名で建物・道路に一致（%s/%s）' % (g.get('class'), g.get('type'))
        elif tier == 3 and (coast > 5 and not water_name or near_users > 20):
            why = '建物・道路に一致（海岸から%.0fkm, 掲載位置から%.0fkm）' % (coast, near_users)
        elif tier == 2 and (coast > 10 and not water_name or near_users > 50):
            why = '地名に一致（海岸から%.0fkm, 掲載位置から%.0fkm）' % (coast, near_users)
        elif coast > 20 and not water_name:
            why = '海岸から%.0fkm' % coast
        if why:
            rejected[key] = dict(g, reason=why)
            kept[key] = None
        else:
            # display_name から市区町村を取り出しておく（build.py が船宿の市区町村と突き合わせる）
            # display_name は「港名, 町名, 市町村, 郡, 県, 日本」の順なので、後ろから市区町村を探す
            city = None
            for part in reversed([x.strip() for x in (g.get('name') or '').split(',')]):
                if part != pref and re.search(r'[市区町村]$', part) and not part.endswith('郡'):
                    city = part
                    break
            kept[key] = dict(g, tier=tier, coast_km=round(coast, 1), city=city)
    json.dump(kept, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    json.dump(rejected, open(OUT.replace('ports.json', 'ports_rejected.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    ok = sum(1 for v in kept.values() if v)
    print('採用 %d / 引けたもの %d（外した %d件）' % (ok, ok + len(rejected), len(rejected)))
    for k, v in list(rejected.items())[:30]:
        print('  外す %-26s %.4f,%.4f %s' % (k[:26], v['lat'], v['lon'], v['reason']))


# OSM の港の種類（信頼できる順）
OSM_KIND = ('harbour', 'seamark:type', 'landuse', 'leisure', 'amenity')


def build_osm(argv):
    """Overpass で取った日本の港（work/geo/osm_ports.json）を、港名 → 候補座標の辞書にする。

    離島は都道府県ポリゴン（japan.geojson）に入っていないものがあり県を決められないので、
    県では引かず「港名が一致する候補のうち、その船宿の位置から最も近いもの」を build.py 側で選ぶ。
    """
    src = os.path.join(ROOT, 'work/geo/osm_ports.json')
    if not os.path.exists(src):
        raise SystemExit('work/geo/osm_ports.json がありません（work/tmp/fetch_osm_ports.py で取得）')
    out = {}
    for el in json.load(open(src, encoding='utf-8')).values():
        tags = el.get('tags') or {}
        name = nfkc(tags.get('name:ja') or el['name'])
        key = port_key(name)
        if len(key) < 2:
            continue
        kind = next((tags[k] for k in OSM_KIND if tags.get(k)), '')
        out.setdefault(key, []).append({'lat': el['lat'], 'lon': el['lon'], 'name': name, 'kind': kind})
    # 同じ場所を指す重複（港のノードとエリアなど）は1つにまとめる
    for key, items in out.items():
        uniq = []
        for it in items:
            if not any(dist_km(it['lat'], it['lon'], u['lat'], u['lon']) < 1.5 for u in uniq):
                uniq.append(it)
        out[key] = uniq
    dst = os.path.join(ROOT, 'work/geocode/ports_osm.json')
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('港名 %d種類 / 地点 %d件 -> %s' % (len(out), sum(len(v) for v in out.values()), dst))
    for k in ('三角東', '隼人', '幌武意', '上浦', '二名津', '東幡豆'):
        print('  ', k, out.get(k))


def main():
    if '--osm' in sys.argv:
        build_osm(sys.argv)
        return
    if '--verify' in sys.argv:
        verify(sys.argv)
        return
    refresh = '--refresh' in sys.argv
    cache = {} if refresh else (json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {})
    tg = targets()
    todo = [(k, v) for k, v in sorted(tg.items(), key=lambda kv: -kv[1]['n']) if k not in cache]
    print('対象の港 %d（うち未取得 %d）' % (len(tg), len(todo)), flush=True)
    found = 0
    for i, (key, v) in enumerate(todo, 1):
        port, pref, city = v['port'], v['pref'], v['city'] or ''
        queries = ['%s %s' % (port, pref)]
        if not re.search(r'港|漁港|マリーナ|川|湖', port):
            queries.append('%s港 %s' % (port, pref))
        if city:
            queries.append('%s %s %s' % (port, city, pref))
        hit = None
        for q in queries:
            try:
                res = search(q)
            except Exception as e:
                print('  ! %s: %s' % (q, e), flush=True)
                res = []
            time.sleep(INTERVAL)
            hit = pick(res, pref)
            if hit:
                hit['query'] = q
                break
        cache[key] = hit
        found += bool(hit)
        if hit:
            print('%4d/%d %-28s -> %.4f,%.4f %s/%s' % (i, len(todo), key[:28], hit['lat'], hit['lon'], hit['class'], hit['type']), flush=True)
        else:
            print('%4d/%d %-28s -> なし' % (i, len(todo), key[:28]), flush=True)
        if i % 10 == 0:
            json.dump(cache, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    json.dump(cache, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('DONE 取得 %d / %d（辞書 %d件）' % (found, len(todo), len(cache)), flush=True)


main()
