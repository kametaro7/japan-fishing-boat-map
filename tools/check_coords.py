"""地図に出している位置の点検。海岸線から離れた場所にある船宿と、同じ港のほかの船から離れた船宿を一覧にする。

  python3 tools/check_coords.py            # 3km以上内陸のものを表示し work/tmp/coord_check.json に保存
  python3 tools/check_coords.py 5          # 閾値を変える

湖・ダム・川・橋の乗り場と内陸県（琵琶湖や霞ヶ浦のガイド船）は海の岸から離れていて当然なので対象外。
東京の荒川・隅田川のように、海から離れた川に係留している船宿は結果に残るので、港名を見て判断する。
"""
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import port_key  # noqa: E402
from coastline import coast_km  # noqa: E402
from common import ROOT, WORK, dist_km, load_json, nfkc  # noqa: E402

INLAND_PREFS = {'栃木県', '群馬県', '埼玉県', '山梨県', '長野県', '岐阜県', '滋賀県', '奈良県'}
WATER_RE = re.compile(r'湖|ダム|沼|池|貯水池|川|渓|用水|橋')


def load_boats():
    boats = {}
    for f in glob.glob(os.path.join(ROOT, 'data/detail/*.json')):
        boats.update(json.load(open(f, encoding='utf-8')))
    return boats


def main():
    limit = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    boats = load_boats()
    # 都道府県界のポリゴンには小さい離島（伊豆諸島など）が入っていないので、海岸線の代わりに OSM の港も見る
    osm = load_json(os.path.join(WORK, 'geocode', 'ports_osm.json'), {}) or {}
    osm_pts = [(c['lat'], c['lon']) for v in osm.values() for c in v]
    site = defaultdict(list)
    for b in boats.values():
        if b.get('geo') == 'site' and b.get('port'):
            site[(b['pref'], port_key(b['port']))].append((b['lat'], b['lon'], b['id']))
    rows = []
    for b in boats.values():
        port = nfkc(b.get('port'))
        if b['pref'] in INLAND_PREFS or WATER_RE.search(port) or WATER_RE.search(b['name']):
            continue
        d = coast_km(b['lat'], b['lon'])
        if d < limit:
            continue
        near_port = min([dist_km(b['lat'], b['lon'], la, lo) for la, lo in osm_pts
                         if abs(la - b['lat']) < 0.1 and abs(lo - b['lon']) < 0.1] or [1e9])
        if near_port < 3:
            continue  # 近くに港がある（離島など、海岸線のデータに無いだけ）
        pts = [(la, lo) for la, lo, i in site.get((b['pref'], port_key(port)), []) if i != b['id']]
        pd = None
        if len(pts) >= 2:
            lats = sorted(p[0] for p in pts)
            lons = sorted(p[1] for p in pts)
            pd = round(dist_km(b['lat'], b['lon'], lats[len(lats) // 2], lons[len(lons) // 2]), 1)
        rows.append({'km': round(d, 1), 'port_km': pd, 'id': b['id'], 'name': b['name'], 'pref': b['pref'],
                     'city': b.get('city'), 'port': b.get('port'), 'address': b.get('address'), 'geo': b.get('geo'),
                     'lat': b['lat'], 'lon': b['lon'],
                     'srcs': sorted({l['src'] for l in b.get('links') or []}) or (['registry'] if b.get('registry') else [])})
    rows.sort(key=lambda r: -r['km'])
    out = os.path.join(ROOT, 'work/tmp/coord_check.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(rows, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    kind = Counter('登録簿のみ' if r['srcs'] == ['registry'] else ('港名あり' if r['port'] else '港名なし') for r in rows)
    print('%.0fkm以上内陸: %d件 / %d件中（%s）' % (limit, len(rows), len(boats), dict(kind)))
    print('geo別', Counter(r['geo'] for r in rows).most_common())
    for r in rows[:40]:
        print(' %5.1fkm %-20s %-5s %-12s %-14s %-6s %s' % (r['km'], r['name'][:20], r['pref'], r['city'] or '',
                                                           (r['port'] or '')[:14], r['geo'], ','.join(r['srcs'])[:26]))


main()
