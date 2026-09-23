"""海岸線と都道府県ポリゴン（work/geo/japan.geojson）を使った位置の点検用ユーティリティ。

  work/geo/japan.geojson は都道府県界のポリゴン（出典: https://github.com/dataofjapan/land 、CC BY 4.0）。
  無ければ次で取得する:
    curl -sL -o work/geo/japan.geojson https://raw.githubusercontent.com/dataofjapan/land/master/japan.geojson
"""
import json
import math
import os

from common import WORK

LAND = os.path.join(WORK, 'geo', 'japan.geojson')
_cache = {}


def _load():
    if 'features' not in _cache:
        if not os.path.exists(LAND):
            raise SystemExit('work/geo/japan.geojson がありません。README の取得コマンドを実行してください。')
        _cache['features'] = json.load(open(LAND, encoding='utf-8'))['features']
    return _cache['features']


def _rings(feature):
    g = feature['geometry']
    polys = g['coordinates'] if g['type'] == 'MultiPolygon' else [g['coordinates']]
    return [ring for poly in polys for ring in poly]


def coast_segments():
    """海岸線の線分 [(lat1, lon1, lat2, lon2), ...]。2県で共有している辺（内陸の県境）は除く。"""
    if 'segs' not in _cache:
        count, rings = {}, []
        for f in _load():
            for ring in _rings(f):
                r = [(round(x, 7), round(y, 7)) for x, y in ring]
                rings.append(r)
                for a, b in zip(r, r[1:]):
                    key = (a, b) if a <= b else (b, a)
                    count[key] = count.get(key, 0) + 1
        segs = []
        for r in rings:
            for a, b in zip(r, r[1:]):
                key = (a, b) if a <= b else (b, a)
                if count[key] == 1:
                    segs.append((a[1], a[0], b[1], b[0]))
        _cache['segs'] = segs
        grid = {}
        for i, (la1, lo1, la2, lo2) in enumerate(segs):
            for la, lo in ((la1, lo1), (la2, lo2)):
                grid.setdefault((int(la * 2), int(lo * 2)), set()).add(i)
        _cache['grid'] = grid
    return _cache['segs']


def _seg_dist(lat, lon, segs):
    kx = 111.320 * math.cos(math.radians(lat))
    ky = 110.574
    px, py = lon * kx, lat * ky
    best = 1e9
    for la1, lo1, la2, lo2 in segs:
        ax, ay, bx, by = lo1 * kx, la1 * ky, lo2 * kx, la2 * ky
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        t = 0.0 if den == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / den))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def coast_km(lat, lon):
    """その地点から海岸線までのおおよその距離(km)。"""
    segs = coast_segments()
    grid = _cache['grid']
    cy, cx = int(lat * 2), int(lon * 2)
    idx, rng = set(), 1
    while not idx and rng <= 8:
        for dy in range(-rng, rng + 1):
            for dx in range(-rng, rng + 1):
                idx |= grid.get((cy + dy, cx + dx), set())
        rng += 1
    return _seg_dist(lat, lon, [segs[i] for i in idx]) if idx else 1e9


def _bboxes():
    if 'bbox' not in _cache:
        out = []
        for f in _load():
            xs = [x for ring in _rings(f) for x, y in ring]
            ys = [y for ring in _rings(f) for x, y in ring]
            out.append((f['properties']['nam_ja'], min(ys), min(xs), max(ys), max(xs), f))
        _cache['bbox'] = out
    return _cache['bbox']


def pref_of(lat, lon):
    """その地点を含む都道府県名（どこにも入らなければ None）。"""
    for name, s_, w_, n_, e_, f in _bboxes():
        if not (s_ <= lat <= n_ and w_ <= lon <= e_):
            continue
        for ring in _rings(f):
            inside = False
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) / (y2 - y1) * (x2 - x1):
                    inside = not inside
            if inside:
                return name
    return None


def in_pref(pref, lat, lon, margin_km=1.0):
    """その地点がその県の中か、県界から margin_km 以内か（島や埋立地は県界の外に出ることがある）。"""
    best = 1e9
    for f in _load():
        if f['properties']['nam_ja'] != pref:
            continue
        for ring in _rings(f):
            inside = False
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) / (y2 - y1) * (x2 - x1):
                    inside = not inside
            if inside:
                return True
            best = min(best, _seg_dist(lat, lon, [(y1, x1, y2, x2) for (x1, y1), (x2, y2) in zip(ring, ring[1:])]))
    return best <= margin_km


def nearest_pref(lat, lon, margin_km=5.0):
    """その地点を含む都道府県名。海の上（港の中や防波堤の外）なら、margin_km 以内で最も近い県。"""
    hit = pref_of(lat, lon)
    if hit:
        return hit
    best, name = margin_km, None
    for pname, s_, w_, n_, e_, f in _bboxes():
        if not (s_ - 0.2 <= lat <= n_ + 0.2 and w_ - 0.2 <= lon <= e_ + 0.2):
            continue
        for ring in _rings(f):
            d = _seg_dist(lat, lon, [(y1, x1, y2, x2) for (x1, y1), (x2, y2) in zip(ring, ring[1:])])
            if d < best:
                best, name = d, pname
    return name
