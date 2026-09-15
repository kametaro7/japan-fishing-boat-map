# -*- coding: utf-8 -*-
"""build.py / geocode.py で共有する正規化の関数。"""
import json
import os
import re
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, 'work')

PREFS = ['北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県', '群馬県',
         '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県', '山梨県', '長野県',
         '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県',
         '鳥取県', '島根県', '岡山県', '広島県', '山口県', '徳島県', '香川県', '愛媛県', '高知県', '福岡県',
         '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県', '鹿児島県', '沖縄県']
PREF_CODE = {p: i + 1 for i, p in enumerate(PREFS)}
PREF_SHORT = {p[:-1] if p != '北海道' else p: p for p in PREFS}


def load_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (IOError, ValueError):
        return default


def save_json(path, obj, indent=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent, separators=(',', ':') if indent is None else None)
    os.replace(tmp, path)


def nfkc(s):
    return unicodedata.normalize('NFKC', str(s or '')).strip()


def to_pref(s):
    """文字列から都道府県の正式名を得る（無ければ None）。"""
    s = nfkc(s)
    if not s:
        return None
    for p in PREFS:
        if s.startswith(p):
            return p
    for short, p in PREF_SHORT.items():
        if s.startswith(short):
            return p
    return None


def _tel_match(s):
    s = re.sub(r'[()\s.・]', '-', nfkc(s))
    s = re.sub(r'[‐‑–—―ー−]', '-', s)
    for m in re.finditer(r'(?<!\d)0\d{1,4}-*\d{1,4}-*\d{3,4}(?!\d)', s):
        d = re.sub(r'\D', '', m.group(0))
        if len(d) in (10, 11) and d[1] != '0':  # 00 で始まるもの（掲載元の埋め草 0000000000 など）は日本の番号ではない
            return m.group(0).strip('-'), d
    return None


def norm_tel(s):
    """電話番号を数字だけにする（名寄せ用）。日本の番号として妥当でなければ None。"""
    m = _tel_match(s)
    return m[1] if m else None


def tel_display(s):
    """表示用の電話番号。市外局番の区切りは元の表記を尊重する（数字だけからは正しく区切れないため）。"""
    m = _tel_match(s)
    if not m:
        return None
    txt = re.sub(r'-+', '-', m[0])
    return txt if '-' in txt else m[1]


# 掲載サイトなど「船宿の公式サイトではない」ホスト
NOT_OFFICIAL_HOSTS = ('chowari.jp', 'tsuree.jp', 'theboat.jp', 'castingnet.jp', 'funaduri.jp', 'fishing-v.jp',
                      'kanpari.jp', 'google.com', 'google.co.jp', 'goo.gl', 'maps.app.goo.gl', 'yahoo.co.jp',
                      'asoview.com', 'jalan.net', 'rakuten.co.jp', 'airtrip.jp', 'veltra.com', 'tabelog.com')
SNS_HOSTS = ('facebook.com', 'fb.com', 'instagram.com', 'twitter.com', 'x.com', 'line.me', 'lin.ee',
             'youtube.com', 'youtu.be', 'tiktok.com', 'threads.net')
# 1つのホストに多数の利用者がいるサービス（パスやサブドメインまで見ないと同一視できない）
SHARED_HOSTS = ('ameblo.jp', 'fc2.com', 'blog.goo.ne.jp', 'wixsite.com', 'jimdo.com', 'jimdofree.com', 'jimdosite.com',
                'blogspot.com', 'hatenablog.com', 'hatenablog.jp', 'plala.or.jp', 'biglobe.ne.jp', 'ocn.ne.jp',
                'sakura.ne.jp', 'select-type.com', 'livedoor.jp', 'livedoor.blog', 'seesaa.net', 'exblog.jp',
                'crayonsite.net', 'goope.jp', 'shopinfo.jp', 'business.site', 'peraichi.com', 'xrea.com',
                'nifty.com', 'so-net.ne.jp', 'dion.ne.jp', 'coocan.jp', 'eonet.ne.jp', 'odn.ne.jp', 'infoseek.co.jp',
                'blogs.yahoo.co.jp', 'note.com', 'wordpress.com', 'weebly.com', 'studio.site', 'webnode.jp',
                'on.omisenomikata.jp', 'hp.gogo.jp', 'fishing-v.jp', 'rakuten.ne.jp', 'mapion.co.jp')


def host_of(u):
    m = re.match(r'^https?://([^/?#]+)', u or '', re.I)
    if not m:
        return ''
    h = m.group(1).lower().split('@')[-1].split(':')[0]
    return h[4:] if h.startswith('www.') else h


def host_in(h, hosts):
    return any(h == x or h.endswith('.' + x) for x in hosts)


def clean_url(u):
    u = nfkc(u)
    if not re.match(r'^https?://', u, re.I):
        return None
    u = re.sub(r'#.*$', '', u)
    u = re.sub(r'[?&](utm_[a-z]+|fbclid|gclid)=[^&]*', '', u)
    return u


def is_official_url(u):
    h = host_of(u)
    return bool(h) and not host_in(h, NOT_OFFICIAL_HOSTS) and not host_in(h, SNS_HOSTS)


def is_sns_url(u):
    return host_in(host_of(u), SNS_HOSTS)


def url_key(u):
    """同じ公式サイトかどうかを判定するキー。"""
    u = clean_url(u)
    if not u:
        return None
    h = host_of(u)
    path = re.sub(r'^https?://[^/]+', '', u, flags=re.I)
    path = re.sub(r'\?.*$', '', path)
    path = re.sub(r'/(index|default|top|home)\.(html?|php|asp)$', '/', path, flags=re.I).rstrip('/')
    segs = [s for s in path.split('/') if s]
    if host_in(h, SHARED_HOSTS):
        return h + ('/' + segs[0].lower() if segs else '')
    if not segs or '.' in segs[0]:
        return h
    return h + '/' + segs[0].lower()


_KNUM = {'〇': 0, '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}


def _kanji_int(s):
    total, cur = 0, 0
    for ch in s:
        if ch in _KNUM:
            cur = cur * 10 + _KNUM[ch] if cur and s.find('十') < 0 and s.find('百') < 0 else _KNUM[ch]
        elif ch == '十':
            total += (cur or 1) * 10
            cur = 0
        elif ch == '百':
            total += (cur or 1) * 100
            cur = 0
    return total + cur


GENERIC_WORDS = ['株式会社', '有限会社', '合同会社', '合資会社', '一般社団法人', '(株)', '(有)', '(同)',
                 '釣り船', '釣船', 'つり船', 'つりぶね', '遊漁船', '遊漁', '船宿', '釣宿', '渡船', '瀬渡し', '釣り宿']


# 「第十八八竜丸」は「十八」だけを数として読む（[一-九十百]+ で読むと船名の「八」まで数に含めていた）
_KANJI_NO = re.compile(r'第((?:[一二三四五六七八九]?百)?(?:[一二三四五六七八九]?十)?[一二三四五六七八九〇零]?)')
# 名寄せで同じ字とみなす異体字・旧字と古い仮名（ひらがなにした後で当てる）
_NAME_VARIANTS = str.maketrans({'龍': '竜', '﨑': '崎', '嵜': '崎', '髙': '高', '濵': '浜', '濱': '浜', '澤': '沢', '嶋': '島',
                                '嶌': '島', '邊': '辺', '邉': '辺', '廣': '広', '冨': '富', '眞': '真', '惠': '恵', '榮': '栄',
                                '國': '国', 'ゑ': 'え', 'ゐ': 'い', 'ゖ': 'け', 'ゕ': 'か', '○': '〇', '◯': '〇'})


def norm_name(s):
    """名寄せ用の船宿名キー。"""
    s = nfkc(s)
    s = re.sub(r'[(（][^)）]{0,20}[)）]$', '', s)  # 末尾の（港名）など
    for w in GENERIC_WORDS:
        s = s.replace(w, '')
    s = _KANJI_NO.sub(lambda m: '第' + str(_kanji_int(m.group(1))) if m.group(1) else m.group(0), s)
    s = re.sub(r'第(\d+)', r'\1', s)
    s = re.sub(r'[\s・･\-‐－ー―~〜()（）「」【】\[\]『』"\'.,、。!！?？/／&＆]', '', s)
    s = ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s)
    return s.translate(_NAME_VARIANTS).lower()


def dist_km(la1, lo1, la2, lo2):
    import math
    r = math.pi / 180
    x = (lo2 - lo1) * r * math.cos((la1 + la2) / 2 * r)
    y = (la2 - la1) * r
    return math.sqrt(x * x + y * y) * 6371.0


def clean_address(a):
    a = nfkc(a)
    a = re.sub(r'〒?\s*\d{3}-?\d{4}\s*', '', a)
    a = re.sub(r'\s+', ' ', a).strip()
    return a


def address_queries(address, pref=None, city=None, port=None):
    """ジオコーディングの問い合わせ候補を詳しい順に返す。"""
    out = []
    a = clean_address(address)
    if a:
        if pref and not to_pref(a):
            a = pref + a
        a = a.split(' ')[0] if to_pref(a.split(' ')[0]) else a.replace(' ', '')
        out.append(a)
        t = town_level(a)
        if t != a and len(t) > 4:
            out.append(t)
    if pref and city:
        c = nfkc(city)
        base = c if to_pref(c) else pref + c
        if port:
            out.append(base + nfkc(port))
        out.append(base)
    seen, res = set(), []
    for q in out:
        if q and q not in seen:
            seen.add(q)
            res.append(q)
    return res


def town_level(address):
    """番地以下を落とした住所（登録簿の個人事業者の住所表示用）。"""
    a = clean_address(address)
    m = re.search(r'[0-9]|[一二三四五六七八九十百]+(?:丁目|番地?|号)|[〇一二三四五六七八九]{2,}', a)
    if m and m.start() >= 4:
        return a[:m.start()].rstrip('字大-ー 　')
    return a
