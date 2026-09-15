#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全国釣り船マップのデータ生成。

  python3 tools/build.py

入力: work/sources/<src>.json（掲載サイト。*.sample.json・_ で始まるもの・*_closed.json は除く）
      work/registry/NN.json（都道府県の遊漁船業者登録簿）
      work/geocode/gsi_cache.json（tools/geocode.py が作る住所→座標）
      data/overrides.json（手修正: drop / set / merge）
出力: data/boats.js（地図と一覧用の索引）、data/detail/NN.json（都道府県ごとの詳細）、work/build_report.txt
"""
import glob
import hashlib
import math
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ROOT, WORK, PREFS, PREF_CODE, load_json, save_json, nfkc, to_pref, norm_tel,  # noqa: E402
                    tel_display, clean_url, is_official_url, is_sns_url, url_key, norm_name, dist_km,
                    clean_address, address_queries, town_level)

TODAY = '2026-09-15'
STALE_BEFORE = '2025-03-15'

# 掲載元: キー → (表示名, 種類, 優先度)。優先度が高いほど名前・座標・住所を優先して採用する。
SOURCES = {
    'chowari': ('釣割', 'booking', 100),
    'castingnet': ('キャスティング船釣り予約', 'booking', 90),
    'tsuree': ('つりー', 'directory', 60),
    'funaduri': ('船釣り.jp', 'catch', 55),
    'theboat': ('THE BOAT', 'catch', 50),
    'registry': ('遊漁船業者登録簿', 'registry', 10),
}

KIND_MAP = [
    (r'乗合|乗り合い|のりあい', '乗合'),
    (r'仕立|貸切|貸し切り|チャーター|チャーター', '仕立'),
    (r'渡船|瀬渡|磯渡|筏|いかだ|カセ', '渡船'),
    (r'レンタルボート|貸しボート|貸ボート', 'レンタルボート'),
    (r'ガイド', 'ガイド'),
]

# 魚種名のゆれ（左の表記を右にまとめる）
FISH = {
    '真鯛': 'マダイ', '鯛': 'マダイ', 'タイ': 'マダイ', 'まだい': 'マダイ', '真だい': 'マダイ',
    '鯵': 'アジ', '鰺': 'アジ', 'あじ': 'アジ', 'マアジ': 'アジ', '真アジ': 'アジ', 'ライトアジ': 'アジ',
    '太刀魚': 'タチウオ', 'たちうお': 'タチウオ', 'タチ': 'タチウオ',
    '鰤': 'ブリ', 'ぶり': 'ブリ', '平政': 'ヒラマサ', '間八': 'カンパチ', '鰆': 'サワラ',
    '鮃': 'ヒラメ', 'ひらめ': 'ヒラメ', '鰈': 'カレイ', 'かれい': 'カレイ', 'マコガレイ': 'カレイ',
    '烏賊': 'イカ', 'いか': 'イカ', 'スルメ': 'スルメイカ', '鯣烏賊': 'スルメイカ', '障泥烏賊': 'アオリイカ',
    '剣先イカ': 'ケンサキイカ', 'マルイカ': 'ケンサキイカ', '墨イカ': 'コウイカ', 'スミイカ': 'コウイカ',
    '蛸': 'タコ', 'たこ': 'タコ', '真蛸': 'マダコ', '飯蛸': 'イイダコ',
    '鯖': 'サバ', 'さば': 'サバ', '鱚': 'シロギス', 'キス': 'シロギス', 'きす': 'シロギス',
    '甘鯛': 'アマダイ', 'あまだい': 'アマダイ', '鬼カサゴ': 'オニカサゴ', '笠子': 'カサゴ', '眼張': 'メバル',
    '鱸': 'シーバス', 'スズキ': 'シーバス', 'すずき': 'シーバス',
    '黒鯛': 'クロダイ', 'チヌ': 'クロダイ', 'グレ': 'メジナ', '石鯛': 'イシダイ', '伊佐木': 'イサキ', 'イサギ': 'イサキ',
    'ノドグロ': 'アカムツ', 'のどぐろ': 'アカムツ', '金目鯛': 'キンメダイ', 'キンメ': 'キンメダイ', '鮪': 'マグロ',
    '鰹': 'カツオ', '鱪': 'シイラ', '皮剥': 'カワハギ', '河豚': 'フグ', '鯊': 'ハゼ', '公魚': 'ワカサギ',
    'アコウ': 'キジハタ', 'あこう': 'キジハタ', '鱈': 'マダラ', 'タラ': 'マダラ', '鮭': 'サケ', '秋鮭': 'サケ',
    'ブラックバス': 'バス', 'ロックフィッシュ': '根魚', 'ロック': '根魚', '五目': '五目釣り', '沖五目': '五目釣り',
    '青物': '青物', 'ジギング': None, 'タイラバ': None, 'エギング': None, 'ルアー': None, '泳がせ': None,
}


def fish_list(v):
    items = v if isinstance(v, list) else re.split(r'[、,，/／・\s]+', nfkc(v))
    out = []
    for it in items:
        t = nfkc(it).strip('　 .。')
        m = re.match(r'^(.+?)\((.+?)\)$', t)  # 「アコウ(キジハタ)」「アジ(マアジ)」など
        if m:
            a, b = m.group(1), m.group(2)
            t = FISH.get(a) or FISH.get(b) or a
        t = re.sub(r'(など|等|ほか|他|各種|狙い|釣り)$', '', t) if t not in ('五目釣り',) else t
        if not t or len(t) > 12 or re.search(r'\d', t):
            continue
        t = FISH.get(t, t)
        if t and t not in out:
            out.append(t)
    return out


def text_list(v):
    if isinstance(v, list):
        items = v
    else:
        items = re.split(r'[、,，/／\s]+', nfkc(v))
    out = []
    for it in items:
        t = nfkc(it)
        if t and len(t) <= 30 and t not in out:
            out.append(t)
    return out


def kinds_of(v):
    out = []
    for t in (v if isinstance(v, list) else [v]):
        t = nfkc(t)
        for pat, k in KIND_MAP:
            if re.search(pat, t) and k not in out:
                out.append(k)
    return out


def hhmm(v):
    m = re.search(r'(\d{1,2})\s*[:：時]\s*(\d{2})?', nfkc(v))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 29 or mi > 59:
        return None
    if h >= 24:
        h -= 24  # 「25時出船」などの深夜表記
    return '%02d:%02d' % (h, mi)


def port_key(s):
    """港名の比較キー（「大原漁港」「大原港」「大原」を同じにする）。"""
    s = re.sub(r'[\s・()（）]', '', nfkc(s))
    s = re.sub(r'(漁港|港湾|港|マリーナ|桟橋|船着場|乗り場|乗船場)$', '', s)
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s)


def assign_port_coords(recs, report):
    """座標も住所も無いが港名があるレコードに、同じ県・同じ港のほかの船宿の位置（中央値）を与える。"""
    site, town = defaultdict(list), defaultdict(list)
    for r in recs:
        if r['pkey'] and r['pref'] and r['lat'] is not None:
            if r['geo'] == 'site':
                site[(r['pref'], r['pkey'])].append((r['lat'], r['lon']))
            elif r['geo'] == 'town' and r['src'] != 'registry':
                town[(r['pref'], r['pkey'])].append((r['lat'], r['lon']))
    lakes = load_json(os.path.join(WORK, 'geocode', 'lakes.json'), {}) or {}
    n = nl = 0
    for r in recs:
        if r['lat'] is None and r['pkey'] and r['pref']:
            pts = site.get((r['pref'], r['pkey'])) or town.get((r['pref'], r['pkey']))
            if pts:
                lats = sorted(p[0] for p in pts)
                lons = sorted(p[1] for p in pts)
                r['lat'], r['lon'], r['geo'] = lats[len(lats) // 2], lons[len(lons) // 2], 'port'
                n += 1
                continue
            # 湖・ダム・川のガイド（RESERVER など）は tools/geocode_lakes.py が OSM Nominatim で引いた位置
            g = lakes.get('%s|%s' % (r['pref'], nfkc(r['port'])))
            # 川は長いので、名前が港（乗り場）名と完全に一致するときだけ使う（「沼津内浦」→「西浦河内川」のような誤一致を捨てる）
            # （OSM で waterway=river と付いた「七色貯水池」のような貯水池は、名前が川で終わらないので使う）
            if (isinstance(g, dict) and g.get('type') == 'river' and nfkc(g.get('name')).endswith('川')
                    and nfkc(g.get('name')) != re.sub(r'[（(].*?[)）]', '', nfkc(r['port']))):
                g = None
            if isinstance(g, dict):
                r['lat'], r['lon'], r['geo'] = g['lat'], g['lon'], 'port'
                nl += 1
    report.append('coords from port gazetteer: %d, from lake/dam lookup: %d' % (n, nl))


def to_int(v):
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return int(v)
    d = re.sub(r'[^\d]', '', nfkc(v))
    return int(d) if d else None


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# 乗船料ではない金額（エサ・仕掛け・レンタル・仮眠・遠征の追加料金・延長・割引・破損など）。
# 料金表を自由記述から機械的に拾う掲載元（釣り野郎など）で「プラン」に混ざり、一覧の「○円〜」を不当に安く見せていた。
JUNK_WORDS = re.compile(r'レンタル|貸竿|貸し竿|竿|リール|仕掛|鉛|オモリ|錘|餌|エサ|えさ|氷|仮眠|駐車|破損|水没|紛失|送迎|探検丸|'
                        r'割引|値引|アップ|追加|延長|オプション|キャンセル|保険|手数料|目以降|遠征料|遠征の|燃料|サーチャージ|'
                        r'お立ち台|シャワー|宿泊|弁当|飲み物|クーラー|ライフジャケット|入漁|協力金|容量|インチク|サイズ')
# 渡船の追加料金に多い言い回し（「波止変え」「磯変更」「同日2回目」「早めのご利用の場合は」）。
# 「3名以上の場合は1人12,000円」「料金変更…10,000円」のような正規の料金にも出てくるので、渡船か3,000円未満のときだけ見る
FEE_WORDS_LOW = re.compile(r'変え|変更|替え|回目|の場合は|利用者は')
INCLUDED = re.compile(r'(付き?|込み?|含む|含み|サービス|無料)')
CHILD_FARE = re.compile(r'(小学生|中学生|中高生|中校生|高校生|子供|子ども|こども|女性|幼児|未就学|学生|シニア)\s*[:：]?\s*[¥￥]?\s*$')
# 渡船（筏・カセ・一文字・波止・磯へ渡すもの）は1,000〜2,000円台の料金が普通にある
FERRY_LIKE = re.compile(r'渡船|渡し|筏|イカダ|いかだ|カセ|かせ|一文字|波止|堤防|沖堤|磯')
# 船での釣り方・乗船形態を表す語。渡船もする業者でも、これらのプランの安い金額は渡船料金ではない（「ジギング 1,000円」は仕掛けの値段など）
BOAT_STYLE = re.compile(r'ジギング|ジグ|タイラバ|五目|乗合|乗り合い|仕立|貸切|チャーター|コマセ|泳がせ|テンヤ|エギング|ルアー|ティップラン|SLJ')
# 料金が構造化されている掲載元と、原文と照合済みの公式サイト抽出は対象外
VERIFIED_PLAN_SOURCES = {'official', 'chowari', 'castingnet', 'reserver'}


def is_junk_price(name, ptext, price, kind, ferry=False):
    s = nfkc(ptext)
    m = re.search(r'%s|%s' % (re.escape('{:,}'.format(price)), price), s)
    # 「エ サ ダンゴ 1,500円」のような字間の空白も詰めてから見る
    before = re.sub(r'\s+', '', s[max(0, m.start() - 18):m.start()]) if m else ''
    nm = re.sub(r'\s+', '', nfkc(name))
    jb = JUNK_WORDS.search(before)
    if jb and not INCLUDED.search(before[jb.end():jb.end() + 4]):
        return True
    if JUNK_WORDS.match(re.sub(r'^[【\[（(「『<＜※*＊]+', '', nm)):  # 「【仮眠部屋】」のような括弧始まりも
        return True
    if CHILD_FARE.search(before):
        return True
    boat_style = kind in ('乗合', '仕立') or bool(BOAT_STYLE.search(nm + before))
    ferry = kind == '渡船' or bool(FERRY_LIKE.search(nm + before)) or (ferry and not boat_style)
    if (ferry or price < 3000) and (FEE_WORDS_LOW.search(before) or FEE_WORDS_LOW.search(nm)):
        return True
    # 渡船・ガイド以外で2,500円未満は、乗船料としてはまず無い（子供料金や部分的な料金の誤読）
    if price < 2500 and not ferry and kind != 'ガイド':
        return True
    return False


def clean_plan(p, src, ferry=False):
    if not isinstance(p, dict):
        return None
    name = nfkc(p.get('name'))[:90]
    price = to_int(p.get('price'))
    if price is not None and not (300 <= price <= 600000):
        price = None
    kind = (kinds_of(p.get('kind')) or [None])[0] or (nfkc(p.get('kind'))[:10] or None)
    if price is not None and src not in VERIFIED_PLAN_SOURCES and is_junk_price(name, p.get('price_text'), price, kind, ferry):
        return None
    name = re.sub(r'^[、。，,.)）\]】」』\s※*＊・:：]+', '', name)  # 自由記述から切り出した名前の先頭のゴミ
    out = {
        'name': name or None,
        'kind': kind,
        'targets': fish_list(p.get('targets') or []),
        'price': price,
        'price_text': nfkc(p.get('price_text'))[:80] or None,
        'depart': hhmm(p.get('depart')),
        'return': hhmm(p.get('return')),
        'meet': nfkc(p.get('meet'))[:40] or None,
        'season': nfkc(p.get('season'))[:60] or None,
        'days': nfkc(p.get('days'))[:60] or None,
        'includes': nfkc(p.get('includes'))[:80] or None,
        'url': clean_url(p.get('url')),
        'src': src,
    }
    if not (out['name'] or out['price'] or out['price_text'] or out['depart']):
        return None
    return {k: v for k, v in out.items() if v not in (None, '', [])}


# ------------------------------------------------------------------ 読み込み
def registry_display_name(r):
    office = nfkc(r.get('office_name'))
    operator = nfkc(r.get('operator'))
    boats = [nfkc(b) for b in (r.get('boats') or []) if nfkc(b)]
    if office and office != operator:
        return office
    for b in boats:
        if re.search(r'丸|号|マリン|フィッシング', b):
            return b
    return office or operator or (boats[0] if boats else '')


def load_all(report):
    labels = load_json(os.path.join(WORK, 'sources', '_labels.json'), {}) or {}
    for k, v in labels.items():
        SOURCES[k] = tuple(v)
    raw = []
    paths = sorted(glob.glob(os.path.join(WORK, 'sources', '*.json')))
    if '--samples' in sys.argv:
        # 試作用: 全件版がまだ無い掲載元はサンプル版で代用する
        full = {os.path.basename(p)[:-5] for p in paths if '.sample' not in p}
        paths = [p for p in paths if '.sample' not in p or os.path.basename(p).split('.sample')[0] not in full]
    for path in paths:
        base = os.path.basename(path)
        if base.startswith('_') or base.endswith('_closed.json') or ('.sample' in base and '--samples' not in sys.argv):
            continue
        src = base.split('.')[0]  # chowari.json / chowari.sample.json → chowari
        if '_' in src:
            continue  # funaduri_closed / fishingv_excluded などの補助ファイル
        data = load_json(path, []) or []
        if isinstance(data, dict):
            data = data.get('records') or data.get('boats') or []
        n = 0
        for r in data:
            if isinstance(r, dict) and nfkc(r.get('name')):
                r = dict(r)
                r['src'] = src
                raw.append(r)
                n += 1
        report.append('source %-12s %6d records' % (src, n))
        if src not in SOURCES:
            SOURCES[src] = (src, 'directory', 40)
    for path in sorted(glob.glob(os.path.join(WORK, 'registry', '[0-9][0-9].json'))):
        code = os.path.basename(path)[:2]
        n = 0
        for r in load_json(path, []) or []:
            if not isinstance(r, dict):
                continue
            name = registry_display_name(r)
            if not name:
                continue
            pref = to_pref(r.get('pref')) or PREFS[int(code) - 1]
            raw.append({
                'src': 'registry',
                'src_id': '%s:%s:%d' % (code, nfkc(r.get('reg_no')), n),
                'src_url': r.get('src_url'),
                'name': name,
                'pref': pref,
                'address': r.get('address'),
                'tel': r.get('tel'),
                'boats': r.get('boats') or [],
                'registry': {
                    'pref': pref,
                    'reg_no': nfkc(r.get('reg_no')) or None,
                    'valid_until': nfkc(r.get('valid_until')) or None,
                    'as_of': nfkc(r.get('as_of')) or None,
                    'src_url': r.get('src_url'),
                },
            })
            n += 1
        report.append('registry %s %-6s %6d records' % (code, PREFS[int(code) - 1], n))
    return raw


GEO = {}
BAD_SITES = set()
CLOSED_SITES = set()


def load_bad_sites(report):
    idx = load_json(os.path.join(WORK, 'official', 'index.json'), []) or []
    for x in idx:
        if x.get('state') == 'suspect' and x.get('website'):
            BAD_SITES.add(url_key(x['website']))
        if x.get('state') == 'closed' and x.get('website'):
            CLOSED_SITES.add(url_key(x['website']))
    for url, res in (load_json(os.path.join(WORK, 'official', 'validate.json'), {}) or {}).items():
        if res.get('state') == 'closed':
            CLOSED_SITES.add(url_key(url))
    # 抽出担当が「失効・無関係」と判定したサイト（tools/official_to_source.py）
    for x in load_json(os.path.join(WORK, 'official', 'unusable.json'), []) or []:
        if x.get('website'):
            BAD_SITES.add(url_key(x['website']))
    # トップページだけで判定した乗っ取り・失効・無関係サイト（tools/validate_sites.py。料金を他から得ている船宿の分）
    for url, res in (load_json(os.path.join(WORK, 'official', 'validate.json'), {}) or {}).items():
        if res.get('state') == 'suspect':
            BAD_SITES.add(url_key(url))
    # curl でも開けなかったサイト（名前解決できない・接続拒否・404・パーキング。tools/check_links.py）
    for url, res in (load_json(os.path.join(WORK, 'official', 'linkcheck.json'), {}) or {}).items():
        if res.get('verdict') == 'dead':
            BAD_SITES.add(url_key(url))
    report.append('official site links dropped (hijacked/unrelated, dead in curl check, judged dead by extraction): %d' % len(BAD_SITES))


def geocode(r):
    pref = r.get('pref')
    for q in address_queries(r.get('address'), pref, r.get('city'), r.get('port')):
        g = GEO.get(q)
        if isinstance(g, dict):
            return g['lat'], g['lon'], g.get('level') or 'town'
    return None


def prep(r):
    src = r['src']
    o = {'src': src, 'prio': SOURCES.get(src, (src, '', 40))[2]}
    o['src_id'] = nfkc(r.get('src_id')) or hashlib.sha1(nfkc(r.get('src_url') or r.get('name')).encode()).hexdigest()[:10]
    o['src_url'] = clean_url(r.get('src_url'))
    o['name'] = re.sub(r'\s+', ' ', nfkc(r.get('name')))[:60]
    o['kana'] = nfkc(r.get('kana'))[:60] or None
    o['address'] = clean_address(r.get('address')) or None
    o['pref'] = to_pref(r.get('pref')) or to_pref(o['address'] or '') or to_pref(r.get('city'))
    if o['address'] and o['pref'] and not to_pref(o['address']):
        o['address'] = o['pref'] + o['address']
    o['city'] = nfkc(r.get('city')) or None
    o['port'] = nfkc(r.get('port')) or None
    o['tel'] = norm_tel(r.get('tel'))
    o['tel_disp'] = tel_display(r.get('tel'))
    web = clean_url(r.get('website'))
    sns = [clean_url(u) for u in (r.get('sns') or []) if clean_url(u)]
    if web and url_key(web) in BAD_SITES:
        web = None  # 失効・乗っ取り・無関係と判定された公式サイト（tools/fetch_official.py）
    if web and not is_official_url(web):
        if is_sns_url(web):
            sns.append(web)
        web = None
    o['website'] = web
    o['sns'] = list(dict.fromkeys(u for u in sns if is_sns_url(u)))
    lat, lon = to_float(r.get('lat')), to_float(r.get('lon'))
    if lat is not None and lon is not None and 20 <= lat <= 46.5 and 122 <= lon <= 154.5:
        o['lat'], o['lon'], o['geo'] = lat, lon, 'site'
    else:
        g = geocode(o)
        if g:
            o['lat'], o['lon'], o['geo'] = g[0], g[1], g[2]
        else:
            o['lat'] = o['lon'] = o['geo'] = None
    o['types'] = kinds_of(r.get('types') or [])
    o['targets'] = fish_list(r.get('targets') or [])
    o['methods'] = text_list(r.get('methods') or [])[:12]
    o['holidays'] = nfkc(r.get('holidays'))[:80] or None
    o['facilities'] = text_list(r.get('facilities') or [])[:20]
    o['capacity'] = to_int(r.get('capacity'))
    if o['capacity'] is not None and not (1 <= o['capacity'] <= 300):
        o['capacity'] = None
    o['access'] = nfkc(r.get('access'))[:160] or None
    o['description'] = nfkc(r.get('description'))[:160] or None
    # 渡船・筏の業者なら、種別の無いプランの安い料金も渡船料金として扱う（いかだ釣りの東海 などの料金が消えないように）
    ferry = '渡船' in kinds_of(r.get('types') or []) or bool(re.search(r'渡船|筏|イカダ|いかだ|カセ', nfkc(r.get('name'))))
    o['plans'] = [p for p in (clean_plan(p, src, ferry) for p in (r.get('plans') or [])) if p]
    for p in o['plans']:
        if p.get('kind') in ('乗合', '仕立', '渡船') and p['kind'] not in o['types']:
            o['types'].append(p['kind'])
    o['schedule_text'] = nfkc(r.get('schedule_text'))[:300] or None
    o['boats'] = [nfkc(b) for b in (r.get('boats') or []) if nfkc(b)][:10]
    o['registry'] = r.get('registry')
    o['fetched'] = nfkc(r.get('fetched')) or TODAY
    o['stale'] = bool(r.get('stale'))
    if src == 'fishingv':
        # 釣りビジョンの stale は「直近6日に釣果投稿が無い」の意味なので、最終投稿日で判断し直す（約1年半より前・投稿なしは掲載終了扱い）
        last = nfkc(r.get('last_report'))
        o['stale'] = not last or last < STALE_BEFORE
    o['pkey'] = port_key(o['port']) if o['port'] else ''
    o['nkey'] = norm_name(o['name'])
    o['ukey'] = url_key(web) if web else None
    return o


def fix_misplaced_sites(recs, report):
    """掲載元の座標が別の県の中を指しているもの（釣りビジョンの旭鱗丸が宮城県石巻、幸進丸が三重県志摩）を捨て、住所・港から求め直す。
    住所から求めた位置（town/city。国土地理院が県名込みで引いたもの）を県の目印にし、60km以内に自分の県の目印が無く、
    よその県の目印があるときだけ疑う（離島のように周りに目印が無いところは疑わない）。"""
    def cell(lat, lon):
        return int(lat / 0.5), int(lon / 0.5)
    marks = defaultdict(list)
    for r in recs:
        if r['geo'] in ('town', 'city') and r['pref'] and r['lat'] is not None:
            marks[cell(r['lat'], r['lon'])].append((r['lat'], r['lon'], r['pref']))
    names = []
    for r in recs:
        if r['geo'] != 'site' or not r['pref']:
            continue
        cy, cx = cell(r['lat'], r['lon'])
        same = other = False
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                for lat, lon, pref in marks.get((cy + dy, cx + dx), ()):
                    if dist_km(r['lat'], r['lon'], lat, lon) <= 60:
                        same = same or pref == r['pref']
                        other = other or pref != r['pref']
        if other and not same:
            names.append('%s(%s %s %.2f,%.2f)' % (r['name'], r['src'], r['pref'], r['lat'], r['lon']))
            g = geocode(r)
            r['lat'], r['lon'], r['geo'] = (g[0], g[1], g[2]) if g else (None, None, None)
    report.append('site coords inside another prefecture discarded: %d %s' % (len(names), ', '.join(names)))


# ------------------------------------------------------------------ 名寄せ
class DSU(object):
    def __init__(self, recs):
        self.p = list(range(len(recs)))
        self.srcs = [({(r['src'], r['src_id'])} if r['src'] != 'registry' else set()) for r in recs]
        self.names = [{r['nkey']} for r in recs]

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a, b, strong):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        sa = {s for s, _ in self.srcs[ra]}
        sb = {s for s, _ in self.srcs[rb]}
        if (sa & sb) and not (strong == 'force' or strong and self.names[ra] & self.names[rb]):
            return False  # 同じ掲載元の別レコードどうしは、電話と名前が両方一致しない限りまとめない（'force' は呼び出し側で表記ゆれを確かめ済み）
        self.p[rb] = ra
        self.srcs[ra] |= self.srcs[rb]
        self.names[ra] |= self.names[rb]
        return True


def exact_url(u):
    """公式サイトのページURLそのもの（共用ホストでも同じページなら同じ船宿とみなすための鍵）。"""
    u = clean_url(u) or ''
    u = re.sub(r'^https?://(www\.)?', '', u, flags=re.I)
    u = re.sub(r'/(index|default|top|home)\.(html?|php|asp)$', '', u, flags=re.I).rstrip('/')
    return u.lower()


def near(a, b, km):
    if a['lat'] is None or b['lat'] is None:
        return None
    return dist_km(a['lat'], a['lon'], b['lat'], b['lon']) <= km


_DAKUTEN = str.maketrans('がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽゔ', 'かきくけこさしすせそたちつてとはひふへほはひふへほう')


def similar_name(a, b):
    """同じ電話番号の2件が同じ船宿の表記ゆれか（「永宝丸」と「知床遊漁船永宝丸」、「第5嘉丸」と「嘉丸」、「ビッグボーイ」と「ビックボーイ」）。"""
    x, y = a['nkey'].translate(_DAKUTEN), b['nkey'].translate(_DAKUTEN)
    if not x or not y:
        return False
    s, l = (x, y) if len(x) <= len(y) else (y, x)
    return s == l or (len(s) >= 2 and s in l)


def bare_listing(r):
    """地域の一覧（gap*・regalt）にある、電話も公式サイトも無い掲載。同じ一覧に同じ船が港名違いで重複していることがある。
    THE BOAT の電話なし掲載は含めない（鴨居と久里浜の別々の五郎丸をつないでしまった）。"""
    return (r['src'].startswith('gap') or r['src'] == 'regalt') and not r['tel'] and not r['ukey']


def cluster(recs, overrides, report):
    dsu = DSU(recs)
    stats = Counter()
    by_tel, by_url, by_name, by_exact = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for i, r in enumerate(recs):
        if r['tel']:
            by_tel[r['tel']].append(i)
        if r['ukey']:
            by_url[r['ukey']].append(i)
        if r['website']:
            by_exact[exact_url(r['website'])].append(i)
        if r['nkey'] and r['pref']:
            by_name[(r['pref'], r['nkey'])].append(i)

    def shared(idxs):
        return len({recs[i]['nkey'] for i in idxs}) > 3

    # 1) 電話と名前が一致（住所から求めた町・市の位置は事業者の自宅などのことがあるので、同じ県なら遠くてもまとめる。
    #    県をまたいで遠いものは別の船宿: 秋田と沖縄のフェニックス、勝山と壱岐の宝生丸（壱岐側の電話が千葉の番号）など）
    rough = ('town', 'city')
    for tel, idxs in by_tel.items():
        for a in idxs:
            for b in idxs:
                ra, rb = recs[a], recs[b]
                if a < b and ra['nkey'] == rb['nkey'] and (
                        (ra['pref'] == rb['pref'] and (ra['geo'] in rough or rb['geo'] in rough)) or near(ra, rb, 80) is not False):
                    stats['tel+name'] += dsu.union(a, b, True)
    # 2) 電話が一致（組合などの共通番号は除く）
    for tel, idxs in by_tel.items():
        if shared(idxs):
            stats['tel_shared_skipped'] += 1
            continue
        for a in idxs:
            for b in idxs:
                if a < b and near(recs[a], recs[b], 60) is not False:
                    stats['tel'] += dsu.union(a, b, False)
    # 3a) 公式サイトのページURLが完全に一致（peraichi.com/landing_pages/... のような共用ホストは 3) の鍵が
    #     共有扱いで飛ばされるので、ページ単位で見る。同じページを多数の船宿が使っていれば同じく飛ばす）
    for key, idxs in by_exact.items():
        if len(idxs) < 2 or shared(idxs):
            continue
        for a in idxs:
            for b in idxs:
                if a < b and near(recs[a], recs[b], 60) is not False:
                    stats['url_exact'] += dsu.union(a, b, False)
    # 3) 公式サイトが一致
    for key, idxs in by_url.items():
        if shared(idxs):
            stats['url_shared_skipped'] += 1
            continue
        for a in idxs:
            for b in idxs:
                if a < b and near(recs[a], recs[b], 60) is not False:
                    stats['url'] += dsu.union(a, b, False)
    # 4) 同じ県で名前が一致し、近い（座標が無ければ市区町村が一致）
    for key, idxs in by_name.items():
        many = len(idxs) > 6
        for a in idxs:
            for b in idxs:
                if a >= b:
                    continue
                ra, rb = recs[a], recs[b]
                if ra['src'] == 'registry' and rb['src'] == 'registry':
                    continue  # 登録簿どうしは別事業者の同名がありうる
                same_port = bool(ra['pkey']) and ra['pkey'] == rb['pkey']
                # 一般語を除くと1文字になる名前（「釣り船皇」「つりぶねや」）は、同じ港か500m以内のときだけ
                short = len(ra['nkey']) < 2
                if short and not (same_port or near(ra, rb, 0.5) is True):
                    continue
                if (ra['tel'] and rb['tel'] and ra['tel'] != rb['tel']) or (ra['ukey'] and rb['ukey'] and ra['ukey'] != rb['ukey']):
                    # 同名でも電話・公式サイトが食い違うものは別の船宿（例: 羽田と船橋の同名店）。
                    # ただし同じ港なら、掲載元によって固定電話と携帯が違うだけのことが多い（明石恵比寿丸・網代渡船など）のでまとめる
                    if near(ra, rb, 0.5 if short else 1.5) is not True and (short or not same_port):
                        stats['name_conflict_skipped'] += 1
                        continue
                site = ra['geo'] == 'site' and rb['geo'] == 'site'
                ok = near(ra, rb, 5 if many else (12 if site else 25))
                if ok is None:
                    same_city = bool(ra['city']) and nfkc(ra['city']) == nfkc(rb['city'])
                    same_port = bool(ra['pkey']) and ra['pkey'] == rb['pkey']
                    ok = (not many) and (same_city or same_port)
                if ok:
                    # 同じ掲載元が同じ船を2回載せていることがある（THE BOAT の directory/listed、つり丸・遊漁船サーチの重複掲載）。
                    # 同名で同じ港（または500m以内。THE BOAT の座標は小数3桁で丸めてある）か同じ公式サイト、
                    # または片方が電話もサイトも無い3km以内の掲載（地域の一覧の重複など）なら、同じ掲載元どうしでもまとめる
                    bare = bare_listing(ra) or bare_listing(rb)
                    dup = (bool(ra['pkey']) and ra['pkey'] == rb['pkey'] or near(ra, rb, 0.5) is True
                           or bool(ra['ukey']) and ra['ukey'] == rb['ukey'] or bare and near(ra, rb, 3) is True)
                    stats['name'] += dsu.union(a, b, dup)
    # 5) 県が食い違う同名（THE BOAT は港の県を取り違えていることがある: 江戸川区の福の神丸が千葉県、横浜の鴨下丸が千葉県）。
    #    座標が3.5km以内で、電話・公式サイトが食い違わないものだけ
    by_nkey = defaultdict(list)
    for i, r in enumerate(recs):
        if len(r['nkey']) >= 2 and r['pref'] and r['lat'] is not None:
            by_nkey[r['nkey']].append(i)
    for key, idxs in by_nkey.items():
        for a in idxs:
            for b in idxs:
                ra, rb = recs[a], recs[b]
                if a >= b or ra['pref'] == rb['pref'] or (ra['src'] == 'registry' and rb['src'] == 'registry'):
                    continue
                if (ra['tel'] and rb['tel'] and ra['tel'] != rb['tel']) or (ra['ukey'] and rb['ukey'] and ra['ukey'] != rb['ukey']):
                    continue
                if near(ra, rb, 3.5):
                    stats['name_cross_pref'] += dsu.union(a, b, near(ra, rb, 0.5))
    # 6) 電話が一致し、名前が表記ゆれ（前置き・船番号・濁点）か公式サイトのページが同じなら、同じ掲載元どうしでもまとめる
    #    （つり丸・釣り野郎・地域の一覧が同じ船を「永宝丸」「知床遊漁船永宝丸」のように2回載せていることがある）。
    #    ほかの規則の結果を変えないよう最後に足すだけにする。離れた場所どうし（湖ごとに載っているガイドなど）は別の印のまま
    for tel, idxs in by_tel.items():
        roots = []  # 表記ゆれをまとめても4つ以上の名前がある番号は、組合などの共通番号として飛ばす（「瑞龍」「大型遊漁船 瑞龍」「瑞龍VII Suiryu」は1つ）
        for i in idxs:
            if not any(similar_name(recs[i], recs[j]) for j in roots):
                roots.append(i)
        if len(roots) > 3:
            continue
        for a in idxs:
            for b in idxs:
                ra, rb = recs[a], recs[b]
                if a >= b or near(ra, rb, 20) is False or ra['src'] == rb['src'] == 'reserver':
                    continue
                if similar_name(ra, rb) or (ra['website'] and rb['website'] and exact_url(ra['website']) == exact_url(rb['website'])):
                    stats['tel+similar'] += dsu.union(a, b, 'force')
    # 手修正の merge
    idx_of = {'%s:%s' % (r['src'], r['src_id']): i for i, r in enumerate(recs)}
    for pair in overrides.get('merge', []):
        ids = [idx_of.get(k) for k in pair]
        if None not in ids:
            for b in ids[1:]:
                dsu.union(ids[0], b, True)
    groups = defaultdict(list)
    for i in range(len(recs)):
        groups[dsu.find(i)].append(i)
    report.append('merge edges: ' + ', '.join('%s=%d' % kv for kv in sorted(stats.items())))
    return list(groups.values())


# ------------------------------------------------------------------ 統合
def first(rs, key):
    for r in rs:
        v = r.get(key)
        if v not in (None, '', []):
            return v
    return None


def merge(recs, idxs):
    rs = sorted((recs[i] for i in idxs), key=lambda r: (-r['prio'], r['src_id']))
    web = [r for r in rs if r['src'] != 'registry']
    reg = [r for r in rs if r['src'] == 'registry']
    primary = rs[0]
    b = {'id': 'b' + hashlib.sha1(('%s:%s' % (primary['src'], primary['src_id'])).encode()).hexdigest()[:9]}
    b['name'] = (web[0] if web else reg[0])['name']
    b['kana'] = first(rs, 'kana')
    # 県は多数決（THE BOAT は港の県を取り違えていることがある）。同数なら優先度の高い掲載元の県
    prefs = Counter(r['pref'] for r in rs if r['pref'])
    b['pref'] = max(prefs, key=lambda p: (prefs[p], -min(i for i, r in enumerate(rs) if r['pref'] == p))) if prefs else None
    b['city'] = first(rs, 'city')
    b['port'] = first(rs, 'port')
    addr = first(web, 'address')
    b['address'] = addr or (town_level(first(reg, 'address')) if first(reg, 'address') else None)
    loc = None
    for pool, geos in ((web, ('site',)), (web, ('port',)), (web, ('town',)), (reg, ('town',)), (web, ('city',)), (reg, ('city',))):
        loc = next((r for r in pool if r['geo'] in geos and r['lat'] is not None), None)
        if loc:
            break
    b['lat'], b['lon'], b['geo'] = (loc['lat'], loc['lon'], loc['geo']) if loc else (None, None, None)
    b['tel'] = first(web, 'tel_disp') or first(reg, 'tel_disp')
    b['website'] = first(rs, 'website')
    b['sns'] = list(dict.fromkeys(u for r in rs for u in r['sns']))[:5]
    b['types'] = list(dict.fromkeys(t for r in rs for t in r['types']))
    cnt = Counter()
    order = []
    for r in rs:
        for t in r['targets'] + [t for p in r['plans'] for t in p.get('targets', [])]:
            if t not in cnt:
                order.append(t)
            cnt[t] += 1
    b['targets'] = sorted(order, key=lambda t: (-cnt[t], order.index(t)))[:60]
    b['methods'] = list(dict.fromkeys(m for r in rs for m in r['methods']))[:12]
    for k in ('holidays', 'access', 'description', 'schedule_text'):
        b[k] = first(rs, k)
    b['facilities'] = list(dict.fromkeys(f for r in rs for f in r['facilities']))[:20]
    caps = [r['capacity'] for r in rs if r['capacity']]
    b['capacity'] = max(caps) if caps else None
    plans, seen = [], set()
    for r in rs:
        for p in r['plans']:
            k = (norm_name(p.get('name') or ''), p.get('price'), p.get('depart'), p.get('kind'))
            if k in seen:
                continue
            seen.add(k)
            plans.append(p)
    b['plans'] = plans[:40]
    links, seen_url = [], set()
    for r in web:
        if r['src'] == 'official':
            continue  # 公式サイトは「公式サイト」ボタンで出すので、掲載ページの一覧には入れない
        if r['src_url'] and r['src_url'] not in seen_url:
            seen_url.add(r['src_url'])
            links.append({'src': r['src'], 'url': r['src_url']})
    b['links'] = links
    b['boats'] = list(dict.fromkeys(x for r in reg for x in r['boats']))[:10]
    regs = []
    for r in reg:
        g = r.get('registry') or {}
        if g.get('reg_no') or g.get('src_url'):
            regs.append(g)
    b['registry'] = regs[:4]
    b['fetched'] = max(r['fetched'] for r in rs)
    b['members'] = ['%s:%s' % (r['src'], r['src_id']) for r in rs]
    return b


F_PLANS, F_WEB, F_BOOK, F_NORIAI, F_SHITATE, F_TOSEN, F_REG, F_APPROX, F_LISTED = 1, 2, 4, 8, 16, 32, 64, 128, 256


# 料金の単位の判定（assets/app.js の priceUnit と同じ規則）。1人あたりの明記を、貸切・隻などの語より優先する。
# 「台」は数字に続くときだけ（仙台・灯台・台数に当てない）。「1名様〜4名様 52,000円」のような人数の範囲はまとめ料金。
PER_PERSON = re.compile(r'/人|/1名|/1人|1名様|1人|1名|一人|お一人|おひとり|人あたり')
PER_BOAT = re.compile(r'隻|貸切|貸し切り|仕立|チャーター|名様?まで|人まで|名迄|[0-9一二三四五六七八九十]台')
GROUP_RANGE = re.compile(r'[0-9]\s*名様?\s*[~〜～\-]\s*[0-9]+\s*名')


def price_unit(p):
    t = nfkc(p.get('price_text'))
    kind = p.get('kind')
    if kind == '仕立':
        return 'boat'
    if kind == '乗合':
        return 'person'
    if GROUP_RANGE.search(t):
        return 'boat'
    if PER_PERSON.search(t):
        return 'person'
    if PER_BOAT.search(t):
        return 'boat'
    return ''


def min_price(b):
    """一覧の「乗合 ○円〜」と料金の絞り込みに使う値。乗合の1人あたり料金の最安値だけ。
    種別の無いプランは、船宿が乗合をしていて、1人あたりと書かれ、渡船・筏などの料金でないときだけ数える。"""
    ps = []
    noriai = '乗合' in (b.get('types') or [])
    for p in b['plans']:
        price = p.get('price')
        if not price or not (1000 <= price <= 80000) or price_unit(p) != 'person':
            continue
        if p.get('kind') != '乗合':
            if p.get('kind') or not noriai or FERRY_LIKE.search(nfkc(p.get('name')) + nfkc(p.get('price_text'))):
                continue
        ps.append(price)
    return min(ps) if ps else 0


def jitter(boats):
    """同じ座標に重なる船宿を小さならせん状にずらす（クラスタを最大まで拡大しても分かれるように）。"""
    groups = defaultdict(list)
    for b in boats:
        if b['lat'] is not None:
            groups[(round(b['lat'], 4), round(b['lon'], 4))].append(b)
    for g in groups.values():
        if len(g) < 2:
            continue
        g.sort(key=lambda b: b['id'])
        for i, b in enumerate(g):
            if i == 0:
                continue
            r = 0.00028 * math.sqrt(i)
            th = i * 2.39996
            b['lat'] = round(b['lat'] + r * math.sin(th), 6)
            b['lon'] = round(b['lon'] + r * math.cos(th) / math.cos(math.radians(b['lat'])), 6)


def main():
    report = ['build %s' % TODAY]
    overrides = load_json(os.path.join(ROOT, 'data', 'overrides.json'), {}) or {}
    GEO.update(load_json(os.path.join(WORK, 'geocode', 'gsi_cache.json'), {}) or {})
    load_bad_sites(report)
    raw = load_all(report)
    drop = set(overrides.get('drop', []))
    recs = [prep(r) for r in raw]
    recs = [r for r in recs if '%s:%s' % (r['src'], r['src_id']) not in drop]
    report.append('records total %d' % len(recs))
    fix_misplaced_sites(recs, report)
    assign_port_coords(recs, report)
    groups = cluster(recs, overrides, report)
    # 掲載終了（stale）のレコードは、ほかの掲載元と名寄せできたときだけ使う（単独では地図に出さない）
    n_groups = len(groups)
    groups = [g for g in groups if any(not recs[i]['stale'] for i in g)]
    report.append('stale-only clusters dropped: %d' % (n_groups - len(groups)))
    boats = [merge(recs, g) for g in groups]
    # 公式サイトのトップに廃業の告知がある船宿は地図に出さない。ただし予約サイトで受付中のものはリンクだけ外す
    kept, n_closed, n_closed_link = [], 0, 0
    for b in boats:
        if b['website'] and url_key(b['website']) in CLOSED_SITES:
            if any(SOURCES.get(l['src'], ('', ''))[1] == 'booking' for l in b['links']):
                b['website'] = None
                n_closed_link += 1
            else:
                n_closed += 1
                continue
        kept.append(b)
    boats = kept
    report.append('closure notice on official site: boats dropped %d, links removed (still bookable) %d' % (n_closed, n_closed_link))
    boats = [b for b in boats if b['id'] not in drop]
    for bid, fields in (overrides.get('set') or {}).items():
        for b in boats:
            if b['id'] == bid:
                b.update(fields)
    placed = [b for b in boats if b['lat'] is not None and b['pref']]
    unplaced = [b for b in boats if not (b['lat'] is not None and b['pref'])]
    jitter(placed)
    placed.sort(key=lambda b: (PREF_CODE.get(b['pref'], 99), -len(b['plans']), b['name']))

    tcount = Counter(t for b in placed for t in b['targets'])
    targets = [t for t, n in tcount.most_common() if n >= 3]
    tindex = {t: i for i, t in enumerate(targets)}
    src_keys = sorted(SOURCES, key=lambda k: -SOURCES[k][2])
    rows = []
    detail = defaultdict(dict)
    for b in placed:
        f = 0
        if any(p.get('price') or p.get('depart') for p in b['plans']):
            f |= F_PLANS
        if b['website']:
            f |= F_WEB
        if any(SOURCES.get(l['src'], ('', ''))[1] == 'booking' for l in b['links']):
            f |= F_BOOK
        if '乗合' in b['types']:
            f |= F_NORIAI
        if '仕立' in b['types']:
            f |= F_SHITATE
        if '渡船' in b['types']:
            f |= F_TOSEN
        if b['registry']:
            f |= F_REG
        if b['geo'] != 'site':
            f |= F_APPROX
        if b['links']:
            f |= F_LISTED
        place = ' '.join(x for x in [b['city'] or '', b['port'] or ''] if x) or (town_level(b['address'] or '')[len(b['pref']):] if b['address'] else '')
        rows.append([b['id'], b['name'], b['kana'] or '', round(b['lat'], 5), round(b['lon'], 5), PREF_CODE[b['pref']],
                     place[:30], f, min_price(b), [tindex[t] for t in b['targets'] if t in tindex]])  # 絞り込みに使うので切り捨てない（表示件数は app.js 側で抑える）
        d = {k: v for k, v in b.items() if v not in (None, '', []) and k not in ('members',)}
        detail['%02d' % PREF_CODE[b['pref']]][b['id']] = d

    # 詳細は一時ディレクトリに書いてから差し替える（書き換え中に配信サーバーや fetch_official.py が読んでも欠けないように）
    out_dir = os.path.join(ROOT, 'data', 'detail')
    tmp_dir, old_dir = out_dir + '.new', out_dir + '.old'
    for d in (tmp_dir, old_dir):
        if os.path.isdir(d):
            shutil.rmtree(d)
    os.makedirs(tmp_dir)
    for code, obj in detail.items():
        save_json(os.path.join(tmp_dir, code + '.json'), obj)
    if os.path.isdir(out_dir):
        os.rename(out_dir, old_dir)
    os.rename(tmp_dir, out_dir)
    if os.path.isdir(old_dir):
        shutil.rmtree(old_dir)
    import json
    # 実際に地図上の船宿へ情報を提供している掲載元だけを出典として出す
    used = set()
    for b in placed:
        used.update(l['src'] for l in b['links'])
        used.update(p['src'] for p in b['plans'] if p.get('src'))
        if b['registry']:
            used.add('registry')
        used.update(m.split(':')[0] for m in b['members'])
    payload = {
        'generated': TODAY,
        'sources': {k: [SOURCES[k][0], SOURCES[k][1]] for k in src_keys if k in used},
        'targets': targets,
        'boats': rows,
    }
    boats_js = os.path.join(ROOT, 'data', 'boats.js')
    with open(boats_js + '.tmp', 'w', encoding='utf-8') as fp:
        fp.write('window.BOAT_DATA=')
        json.dump(payload, fp, ensure_ascii=False, separators=(',', ':'))
        fp.write(';\n')
    os.replace(boats_js + '.tmp', boats_js)
    save_json(os.path.join(WORK, 'build_members.json'), {b['id']: b['members'] for b in boats})

    report.append('merged boats %d (placed %d, unplaced %d)' % (len(boats), len(placed), len(unplaced)))
    cat = Counter()
    for r in rows:
        cat['plans' if r[7] & F_PLANS else 'web' if r[7] & (F_WEB | F_LISTED) else 'registry_only'] += 1
    report.append('categories: ' + ', '.join('%s=%d' % kv for kv in cat.items()))
    multi = Counter(len({m.split(':')[0] for m in b['members']}) for b in boats)
    report.append('boats by number of distinct sources: ' + ', '.join('%d=%d' % kv for kv in sorted(multi.items())))
    geo = Counter(b['geo'] for b in placed)
    report.append('geo precision: ' + ', '.join('%s=%d' % kv for kv in geo.items()))
    pc = Counter(b['pref'] for b in placed)
    report.append('by prefecture: ' + ', '.join('%s=%d' % (p, pc.get(p, 0)) for p in PREFS))
    report.append('top targets: ' + ', '.join('%s=%d' % kv for kv in tcount.most_common(40)))
    usrc = Counter(m.split(':')[0] for b in unplaced for m in b['members'])
    report.append('unplaced by source: ' + ', '.join('%s=%d' % kv for kv in usrc.items()))
    with open(os.path.join(WORK, 'build_report.txt'), 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(report) + '\n')
    print('\n'.join(report))


if __name__ == '__main__':
    main()
