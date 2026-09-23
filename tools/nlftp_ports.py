"""国土数値情報の漁港（C09）・港湾（C02）から「県＋港名 → 座標」の辞書を作る。

出典: 国土数値情報（漁港データ C09-06・港湾データ C02-14）国土交通省。次で取得して展開しておく。
  mkdir -p work/geo && cd work/geo
  curl -sA Mozilla/5.0 -o C09.zip https://nlftp.mlit.go.jp/ksj/gml/data/C09/C09-06/C09-06_GML.zip && unzip -oq C09.zip -d C09
  curl -sA Mozilla/5.0 -o C02.zip https://nlftp.mlit.go.jp/ksj/gml/data/C02/C02-14/C02-14_GML.zip && unzip -oq C02.zip -d C02

  python3 tools/nlftp_ports.py   # → work/geocode/ports_jp.json
"""
import json
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build import port_key  # noqa: E402
from common import PREFS, WORK, nfkc  # noqa: E402

C09 = os.path.join(WORK, 'geo/C09/C09-06_FishingPort')
C02 = os.path.join(WORK, 'geo/C02/C02-14_GML/C02-14-g_PortAndHarbor')
OUT = os.path.join(WORK, 'geocode/ports_jp.json')


def read_dbf(path):
    data = open(path, 'rb').read()
    n_rec, hdr_len, rec_len = struct.unpack('<IHH', data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        fields.append((data[pos:pos + 11].split(b'\x00')[0].decode('cp932', 'replace'), data[pos + 16]))
        pos += 32
    rows = []
    for i in range(n_rec):
        p, row = hdr_len + i * rec_len + 1, {}
        for name, flen in fields:
            row[name] = data[p:p + flen].decode('cp932', 'replace').strip()
            p += flen
        rows.append(row)
    return rows


def read_points(path):
    data = open(path, 'rb').read()
    pos, pts = 100, []
    while pos < len(data):
        _, clen = struct.unpack('>II', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + clen * 2]
        if struct.unpack('<I', body[:4])[0] == 1:
            x, y = struct.unpack('<dd', body[4:20])
            pts.append((y, x))
        else:
            pts.append(None)
        pos += 8 + clen * 2
    return pts


def add(out, pref, name, lat, lon, kind, city=None):
    """港名（括弧付きはその中身を落としたものも）で登録する。"""
    keys = {port_key(nfkc(name)), port_key(re.sub(r'[（(].*?[)）]', '', nfkc(name)))}
    for k in keys:
        if len(k) < 2:
            continue
        items = out.setdefault('%s|%s' % (pref, k), [])
        if not any(abs(i['lat'] - lat) < 0.01 and abs(i['lon'] - lon) < 0.01 for i in items):
            items.append({'lat': lat, 'lon': lon, 'name': nfkc(name), 'kind': kind, 'city': nfkc(city or '').split('.')[0] or None})


def main():
    out = {}
    rows, pts = read_dbf(C09 + '.dbf'), read_points(C09 + '.shp')
    n09 = 0
    for r, p in zip(rows, pts):
        if p and r.get('C09_006') and r.get('C09_002'):
            add(out, r['C09_006'], r['C09_002'], p[0], p[1], '漁港', r.get('C09_008'))
            n09 += 1
    rows, pts = read_dbf(C02 + '.dbf'), read_points(C02 + '.shp')
    n02 = 0
    for r, p in zip(rows, pts):
        code = (r.get('C02_003') or '')[:2]
        if p and code.isdigit() and 1 <= int(code) <= 47 and r.get('C02_005'):
            add(out, PREFS[int(code) - 1], r['C02_005'], p[0], p[1], '港湾')  # C02_007 は港湾管理者名なので市町村には使わない
            n02 += 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('漁港 %d件・港湾 %d件 → 港名 %d種類 (%s)' % (n09, n02, len(out), OUT))
    for k in ('熊本県|三角東', '鹿児島県|隼人', '北海道|幌武意', '大分県|上浦', '愛媛県|二名津', '愛知県|東幡豆', '福井県|鷹巣'):
        print('  ', k, out.get(k))


main()
