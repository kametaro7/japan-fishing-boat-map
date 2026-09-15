# -*- coding: utf-8 -*-
"""gap404344: 福岡・熊本・大分の「地域の釣り船一覧」から掲載サイトレコードを作る。

使い方:
  python3 tools/scrape_gap404344.py fetch URL [URL...]   # キャッシュに取得するだけ（調査用）
  python3 tools/scrape_gap404344.py build [--refresh]      # 一覧を取得・解析して work/sources/gap404344.json を書く

取り込む一覧:
  tsuritaro  全国遊漁船検索サイト 釣りたろう（tsuritaro-fishing.com）の 福岡/熊本/大分 の県別検索結果＋各船の「基本情報」タブ
  t-island   天草宝島観光協会「施設・スポット検索 > 遊ぶ > 釣り」（熊本県天草市）
  munakata-jinoshima / munakata-oshima
             宗像市観光サイト「大島・地島での遊漁船」のチラシ PDF（画像のみ・2015年作成）。
             文字が取れないので、画像を拡大して目視で書き写した表を下の JINOSHIMA / OSHIMA に置いた（船長氏名は入れない）。

作法: 1ホスト直列・1秒間隔・robots.txt に従う・キャッシュ work/cache/gap404344/（index.tsv に URL とファイル名の対応）。
"""
import hashlib
import html as htmllib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
CACHE = os.path.join(ROOT, 'work', 'cache', 'gap404344')
LOG = os.path.join(ROOT, 'work', 'logs', 'gap404344.log')
OUT = os.path.join(ROOT, 'work', 'sources', 'gap404344.json')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
INTERVAL = 1.0
REFRESH = '--refresh' in sys.argv
FETCHED = '2026-09-15'
SRC = 'gap404344'

_last = {}
_robots = {}


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + msg
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def _wait(host):
    t = _last.get(host)
    if t is not None:
        d = INTERVAL - (time.time() - t)
        if d > 0:
            time.sleep(d)
    _last[host] = time.time()


def _raw_get(url):
    host = urllib.parse.urlsplit(url).netloc
    delay = 2.0
    for attempt in range(6):
        _wait(host)
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ja,en;q=0.8'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 5:
                log(f'{e.code} {url} backoff {delay}s')
                time.sleep(delay)
                delay *= 2
                continue
            return e.code, b''
        except Exception as e:  # noqa
            if attempt < 2:
                time.sleep(delay)
                continue
            log(f'ERR {url} {e}')
            return None, b''
    return None, b''


def allowed(url):
    sp = urllib.parse.urlsplit(url)
    base = f'{sp.scheme}://{sp.netloc}'
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        path = os.path.join(CACHE, 'robots', hashlib.sha1(base.encode()).hexdigest() + '.txt')
        if os.path.exists(path) and not REFRESH:
            txt = open(path, encoding='utf-8', errors='replace').read()
        else:
            code, body = _raw_get(base + '/robots.txt')
            txt = body.decode('utf-8', 'replace') if code == 200 else ''
            if '<html' in txt[:500].lower():
                txt = ''
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(txt)
        rp.parse(txt.splitlines())
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + '.html')


def fetch_bytes(url):
    path = cache_path(url)
    if os.path.exists(path) and not REFRESH:
        return open(path, 'rb').read()
    if not allowed(url):
        log(f'robots disallow {url}')
        return None
    code, body = _raw_get(url)
    if code != 200:
        log(f'HTTP {code} {url}')
        return None
    os.makedirs(CACHE, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(body)
    with open(os.path.join(CACHE, 'index.tsv'), 'a', encoding='utf-8') as f:
        f.write(f'{os.path.basename(path)}\t{url}\n')
    return body


def fetch(url, encoding=None):
    """URL の本文（str）を返す。キャッシュ優先。取れなければ None。"""
    body = fetch_bytes(url)
    if body is None:
        return None
    if encoding:
        return body.decode(encoding, 'replace')
    head = body[:3000].decode('ascii', 'replace').lower()
    m = re.search(r'charset=["\']?([a-z0-9_\-]+)', head)
    enc = m.group(1) if m else 'utf-8'
    if enc in ('shift_jis', 'sjis', 'x-sjis'):
        enc = 'cp932'
    try:
        return body.decode(enc, 'replace')
    except LookupError:
        return body.decode('utf-8', 'replace')


# ---------------------------------------------------------------- 共通の小道具

def nfkc(s):
    s = unicodedata.normalize('NFKC', str(s or ''))
    s = s.replace('​', '').replace('﻿', '')
    return s


def text_of(fragment, br=' / '):
    """HTML 断片をテキストにする。<br> だけを改行とみなし br でつなぐ（ソース上の改行は空白扱い）。"""
    t = re.sub(r'<br\s*/?>|</p>|</li>', '\x00', fragment or '', flags=re.I)
    t = re.sub(r'<[^>]+>', '', t)
    t = htmllib.unescape(t)
    t = nfkc(t)
    parts = [re.sub(r'\s+', ' ', x).strip() for x in t.split('\x00')]
    return br.join(x for x in parts if x)


def kata2hira(s):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s)


def empty_record(src_id, src_url, name, pref):
    return {
        'src': SRC, 'src_id': src_id, 'src_url': src_url, 'name': name, 'kana': None,
        'pref': pref, 'city': None, 'address': None, 'port': None, 'lat': None, 'lon': None,
        'tel': None, 'website': None, 'sns': [], 'types': [], 'targets': [], 'methods': [],
        'holidays': '', 'facilities': [], 'capacity': None, 'access': '', 'description': '',
        'plans': [], 'schedule_text': '', 'fetched': FETCHED,
    }


TARGET_PATTERNS = [
    ('アマダイ', r'アマダイ|甘鯛|アマ鯛'),
    ('レンコダイ', r'レンコダイ|レンコ鯛'),
    ('マダイ', r'マダイ|真鯛|(?<![アマレンコ小大])鯛(?!茶)|(?<![アマレンコ])タイ(?!ラバ|ム|プ|ミング|ヤ|ル|ト)'),
    ('ヒラメ', r'ヒラメ|平目'),
    ('ヒラマサ', r'ヒラマサ'),
    ('ブリ', r'(?<![カアサ])ブリ(?!ッジ)|鰤'),
    ('カンパチ', r'カンパチ'),
    ('サワラ', r'サワラ'),
    ('イサキ', r'イサキ'),
    ('アジ', r'アジ(?!ング|曽根)|(?<![a-zA-Z])鯵'),
    ('タチウオ', r'タチウオ|太刀魚'),
    ('ヤリイカ', r'ヤリイカ'),
    ('ケンサキイカ', r'ケンサキイカ|剣先イカ'),
    ('アオリイカ', r'アオリイカ'),
    ('イカ', r'(?<![リキオルメ])イカ(?!メタル)'),
    ('タコ', r'タコ(?!飯|めし)|マダコ|蛸'),
    ('カワハギ', r'カワハギ'),
    ('キス', r'キス(?!ト)'),
    ('メバル', r'メバル'),
    ('アラカブ', r'アラカブ|カサゴ'),
    ('根魚', r'根魚'),
    ('青物', r'青物'),
    ('クロ', r'(?<![イマ])クロ(?![ダムソメマ])|グレ(?!ード)'),
    ('イシダイ', r'イシダイ|石鯛'),
    ('チヌ', r'チヌ|クロダイ|黒鯛'),
    ('シーバス', r'シーバス|スズキ'),
    ('マグロ', r'マグロ|キハダ'),
    ('カレイ', r'カレイ'),
    ('アオナ', r'アオナ'),
    ('タカバ', r'タカバ'),
]
METHOD_PATTERNS = [
    ('タイラバ', r'タイラバ|鯛ラバ'),
    ('SLJ', r'SLJ|スーパーライトジギング'),
    ('ジギング', r'ジギング|スロジギ|ジグ'),
    ('ティップラン', r'ティップラン'),
    ('テンヤ', r'テンヤ'),
    ('イカメタル', r'イカメタル'),
    ('落とし込み', r'落とし込み|落し込み'),
    ('泳がせ', r'泳がせ'),
    ('キャスティング', r'キャスティング'),
    ('夜焚き', r'夜焚'),
    ('サビキ', r'サビキ'),
    ('ルアー', r'ルアー'),
    ('フカセ', r'フカセ'),
]


def extract_keywords(text, patterns):
    out = []
    t = nfkc(text)
    for label, pat in patterns:
        if re.search(pat, t) and label not in out:
            out.append(label)
    if 'イカ' in out and any(x in out for x in ('ヤリイカ', 'ケンサキイカ', 'アオリイカ')):
        # 「ヤリイカ」だけが当たっているときの汎用「イカ」は重ねない
        rest = re.sub(r'ヤリイカ|ケンサキイカ|剣先イカ|アオリイカ|イカメタル', '', t)
        if not re.search(r'(?<![リキオルメ])イカ', rest):
            out.remove('イカ')
    return out


NUM = r'(\d{1,3}(?:[,.]\d{3})+|\d{3,6})'


def first_yen(s):
    s = nfkc(s)
    m = re.search(r'[¥￥]\s*' + NUM + r'|' + NUM + r'\s*(?:円|～|〜|~)', s)
    if not m:
        return None
    v = int(re.sub(r'[,.]', '', m.group(1) or m.group(2)))
    return v if v >= 500 else None


def has_price(s):
    return bool(re.search(r'\d[\d,.]*\s*円|[¥￥]\s*\d', nfkc(s)))


# ---------------------------------------------------------------- 釣りたろう

TT_BASE = 'https://www.tsuritaro-fishing.com'
TT_PREFS = {'40': '福岡県', '43': '熊本県', '44': '大分県'}
PORT_WORDS = r'港|湊|船溜|ハーバー|マリーナ|広場|ポンツーン|桟橋|漁協'
# 所在地の書き方が住所になっていないもの（原文どおりの港名だけを port に入れる）
TT_OVERRIDE = {
    '108': {'address': None, 'port': '門司港 第二船溜', 'city': '北九州市'},   # 「北九州市門司港 / 第二船溜」
    '131': {'address': None, 'port': '三角東港'},                              # 「三角東港」
}


def tt_ids():
    out = []
    for code, pref in TT_PREFS.items():
        page = 1
        while True:
            url = f'{TT_BASE}/search?prefecture={code}' + (f'&page={page}' if page > 1 else '')
            s = fetch(url)
            if not s:
                break
            for sid in dict.fromkeys(re.findall(r'/ship/(\d+)"', s)):
                if sid not in [x[1] for x in out]:
                    out.append((code, sid, url))
            if not re.search(rf'prefecture={code}&amp;page={page + 1}\b', s):
                break
            page += 1
    return out


def split_name_kana(raw):
    name = nfkc(raw)
    name = re.sub(r'\s+', ' ', name).strip()
    kana = None
    pat = r'[（(]\s*([ァ-ヶぁ-ゖー・\s]+)\s*[）)]'
    m = re.search(pat + r'\s*$', name)
    if m:
        kana = re.sub(r'\s+', ' ', kata2hira(m.group(1))).strip()
        name = name[:m.start()].strip()
    name = re.sub(pat, ' ', name)
    name = re.sub(r'\s*船名\s*', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name, kana


def parse_tt_address(raw, pref):
    s = nfkc(raw).replace('​', '')
    s = re.sub(r'〒?\s*\d{3}-\d{4}', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    port = None
    m = re.search(r'出航場所\s*「([^」]+)」', s)
    if m:
        port = m.group(1).strip()
        s = s[:m.start()].strip()
    m = re.search(r'[（(]([^（）()]*(?:' + PORT_WORDS + r')[^（）()]*)[）)]\s*$', s)
    if m and not port:
        port = m.group(1).strip()
        s = s[:m.start()].strip()
    m = re.match(r'^(.*\S)\s+(\S*(?:漁港|港))$', s)
    if m and not port and re.search(r'[市町村区郡]', m.group(1)):
        port = m.group(2)
        s = m.group(1)
    addr = re.sub(r'\s+', '', s)
    if addr and not addr.startswith(pref):
        addr = pref + addr
    if not re.search(r'[市町村区郡]', addr):
        addr = None
    city = None
    if addr:
        m = re.match(re.escape(pref) + r'((?:[^市町村郡]{1,6}郡)?[^市町村]{1,6}?[市町村])', addr)
        if m:
            city = m.group(1)
    return addr, port, city


def tt_plans(pairs, url):
    plans, cur, sched = [], None, []
    for k, v in pairs:
        if k in ('乗合', '仕立（チャーター）', '仕立(チャーター)'):
            cur = {'name': v or k, 'kind': '乗合' if k == '乗合' else '仕立', 'targets': [], 'price': None,
                   'price_text': '', 'depart': None, 'return': None, 'meet': '', 'season': '', 'days': '',
                   'includes': '', 'url': url}
            plans.append(cur)
        elif k in ('業種', '特徴', '船長コメント', 'HP', 'ブログ', '所在地', '電話番号', 'その他'):
            cur = None
        elif cur is not None and k == '出港時間' and v:
            sched.append(f"{cur['kind']}: {v}")
        elif cur is not None and k == '料金' and v:
            cur['price_text'] = v[:200]
            cur['price'] = first_yen(v) if cur['kind'] == '乗合' else None
    plans = [p for p in plans if p['price_text'] and has_price(p['price_text'])]
    return plans, ' / '.join(sched)


def build_tsuritaro():
    from common import is_sns_url, clean_url
    recs = []
    for code, sid, list_url in tt_ids():
        pref = TT_PREFS[code]
        durl = f'{TT_BASE}/ship/{sid}'
        s = fetch(durl)
        d = fetch(f'{TT_BASE}/ship/data/{sid}')
        if not s or not d:
            continue
        m = re.search(r'class="ship-name">(.*?)</h4>', s, re.S)
        name, kana = split_name_kana(text_of(m.group(1)))
        seg = d[d.find('class="anker"'):d.find('<footer')]
        pairs = [(text_of(k), text_of(v)) for k, v in re.findall(r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>', seg, re.S)]
        info = {}
        for k, v in pairs:
            info.setdefault(k, v)
        r = empty_record(f'tsuritaro:{sid}', list_url, name, pref)
        r['kana'] = kana
        addr_raw = text_of(re.search(r'<dt[^>]*>\s*所在地\s*</dt>\s*<dd[^>]*>(.*?)</dd>', seg, re.S).group(1), br=' ') \
            if re.search(r'<dt[^>]*>\s*所在地\s*</dt>', seg) else ''
        r['address'], r['port'], r['city'] = parse_tt_address(addr_raw, pref)
        tel = re.sub(r'\D', '', nfkc(info.get('電話番号') or ''))
        r['tel'] = tel if len(tel) in (10, 11) and tel.startswith('0') else None
        types = []
        for t in re.split(r'[、,]', info.get('業種') or ''):
            t = t.strip()
            mapped = {'乗合': '乗合', '仕立（チャーター）': '仕立', '仕立(チャーター)': '仕立', '渡船・筏・フカセ': '渡船'}.get(t)
            if mapped and mapped not in types:
                types.append(mapped)
        r['types'] = types
        for key in ('HP', 'ブログ'):
            u = clean_url(re.search(r'https?://\S+', info.get(key) or '').group(0)) if re.search(r'https?://\S+', info.get(key) or '') else None
            if not u:
                continue
            if is_sns_url(u):
                if u not in r['sns']:
                    r['sns'].append(u)
            elif not r['website']:
                r['website'] = u
        plans, sched = tt_plans(pairs, f'{TT_BASE}/ship/data/{sid}')
        r['plans'] = plans
        r['schedule_text'] = sched[:300]
        kw_text = ' '.join([info.get('特徴') or '', info.get('料金') or '', info.get('備考') or '', info.get('船長コメント') or ''])
        r['targets'] = extract_keywords(kw_text, TARGET_PATTERNS)
        r['methods'] = extract_keywords(kw_text, METHOD_PATTERNS)
        other = info.get('その他') or ''
        mcap = re.search(r'定員\s*(\d{1,3})\s*[人名]', other)
        if mcap:
            r['capacity'] = int(mcap.group(1))
        if sid in TT_OVERRIDE:
            r.update(TT_OVERRIDE[sid])
        r['description'] = '全国遊漁船検索サイト「釣りたろう」掲載（詳細 ' + durl + '）'
        recs.append(r)
    return recs


# ---------------------------------------------------------------- 天草宝島観光協会

TI_LIST = 'https://www.t-island.jp/spot/category/enjoy?c%5B%5D=266'
TI_BOAT_NAME = r'丸|釣船|釣り船|瀬渡|遊漁|号'
TI_EXCLUDE_NAME = r'釣具|ゲストハウス|民宿イルカ館|RVパーク|仕切網|くらたけ海ホタル'
TI_OVERRIDE = {
    # 民宿の名前で載っているが、本文に「遊漁船の営業もあり…（釣り船ルスプラージャ）」とある
    '2156': {'name': '釣り船ルスプラージャ', 'description': '民宿光浜荘が営む遊漁船（イルカウォッチング・船釣り）', 'plans': [], 'include': True,
             'targets': ['マダイ']},
    '2848': {'name': 'マリンツーリスト牛深 パートナー号'},
    # 本文の「観光とんとこ漁（網漁体験）で捕れる生き物」は釣りの対象魚ではないので、釣りの対象として書かれた魚だけにする
    '2973': {'targets': ['マダイ', 'タチウオ', 'ブリ']},
}
TI_PLAN_SKIP = r'貸し竿|キャンプ|いさり火|バードウォッチング|保険|子供|大人|素泊|朝食|2食|ランチ|ちゃんぽん|タコ飯|とんとこ|宿泊|合宿|上限'


def ti_list_items():
    items, url, seen = [], TI_LIST, set()
    while url and url not in seen:
        seen.add(url)
        s = fetch(url)
        if not s:
            break
        for full, sid, block in re.findall(r'<a[^>]+href="(https://www\.t-island\.jp/spot/(\d+))"[^>]*>(.*?)</a>', s, re.S):
            title = re.search(r'type-grid__title">([^<]+)', block)
            if title and sid not in [x[0] for x in items]:
                items.append((sid, nfkc(htmllib.unescape(title.group(1))).strip(), url))
        nxt = re.findall(r'href="(https://www\.t-island\.jp/spot/category/enjoy/page/(\d+)\?c%5B0%5D=266)"', s)
        cur = int(re.search(r'/page/(\d+)', url).group(1)) if '/page/' in url else 1
        url = next((u for u, n in nxt if int(n) == cur + 1), None)
    return items


def ti_fields(s):
    seg = s[s.find('施設情報'):]
    pairs = re.findall(r'<(th|dt)[^>]*>(.*?)</\1>\s*<(td|dd)[^>]*>(.*?)</\3>', seg, re.S)
    out = {}
    for _, k, _, v in pairs:
        k = text_of(k)
        if k and k not in out:
            out[k] = v
    return out


def ti_plans(price_html, spot_name, url):
    lines = [nfkc(htmllib.unescape(re.sub(r'<[^>]+>', '', x))).strip() for x in re.split(r'<br\s*/?>|</p>|</li>|\n', price_html)]
    lines = [re.sub(r'\s+', ' ', x) for x in lines if x.strip()]
    plans, course, course_line = [], '', ''
    for ln in lines:
        mcourse = re.match(r'^[・★\s]*(.*?コース)', ln)
        if mcourse and '円' not in ln:
            course = mcourse.group(1).strip('★・ ')
            course_line = ln
            continue
        if '円' not in ln or re.search(TI_PLAN_SKIP, ln):
            continue
        mname = re.match(r'^[・★\s]*(.+?)\s*(?:一隻|基本|\d)', ln)
        name = (mname.group(1) if mname else ln).strip(' ・、')
        if re.search('瀬渡', spot_name) or re.search(r'堤防|磯|釣り$', name) and re.search('瀬渡|牛深', spot_name):
            kind = '渡船'
        elif re.search(r'一隻|名様まで|人まで', ln) or re.search(r'名様まで|人まで', course_line):
            kind = '仕立'
        else:
            kind = '乗合'
        price = first_yen(ln) if kind != '仕立' else None
        plans.append({'name': (course + ' ' + name).strip() if course else name, 'kind': kind, 'targets': extract_keywords(name, TARGET_PATTERNS),
                      'price': price, 'price_text': ln if kind != '仕立' else ('1隻 ' + ln if '隻' not in ln else ln),
                      'depart': None, 'return': None, 'meet': '', 'season': '', 'days': '', 'includes': '', 'url': url})
    return plans


def build_tisland():
    from common import clean_url, is_sns_url
    recs = []
    for sid, title, list_url in ti_list_items():
        ov = TI_OVERRIDE.get(sid, {})
        if not ov.get('include') and (not re.search(TI_BOAT_NAME, title) or re.search(TI_EXCLUDE_NAME, title)):
            continue
        url = f'https://www.t-island.jp/spot/{sid}'
        s = fetch(url)
        if not s:
            continue
        f = ti_fields(s)
        name = ov.get('name') or re.sub(r'^[㈲㈱]|^(有限会社|株式会社)\s*', '', text_of(f.get('名称') or title))
        r = empty_record(f't-island:{sid}', TI_LIST, name, '熊本県')
        addr = re.sub(r'〒?\s*\d{3}-\d{4}', '', text_of(f.get('所在地') or '', br=' ')).strip()
        addr = re.sub(r'\s+', '', addr)
        if addr:
            r['address'] = addr if addr.startswith('熊本県') else '熊本県' + addr
            mc = re.match(r'熊本県([^市町村]{1,5}[市町村])', r['address'])
            r['city'] = mc.group(1) if mc else None
        tel_txt = text_of(f.get('TEL') or '')
        mt = re.search(r'0\d{1,4}-?\d{1,4}-?\d{3,4}', tel_txt)
        r['tel'] = mt.group(0) if mt else None
        mu = re.search(r'https?://[^\s"<]+', f.get('URL') or '')
        if mu:
            u = clean_url(mu.group(0))
            if u and is_sns_url(u):
                r['sns'].append(u)
            elif u:
                r['website'] = u
        body = text_of(s[s.find('カテゴリー'):s.find('施設情報')]) + ' ' + text_of(f.get('料金') or '')
        r['targets'] = ov['targets'] if 'targets' in ov else extract_keywords(body, TARGET_PATTERNS)
        r['methods'] = extract_keywords(body, METHOD_PATTERNS)
        if 'plans' in ov:
            r['plans'] = ov['plans']
        else:
            r['plans'] = ti_plans(f.get('料金') or '', name, url)
        kinds = {p['kind'] for p in r['plans']}
        if re.search('瀬渡', name + body):
            r['types'].append('渡船')
        for k in ('乗合', '仕立'):
            if k in kinds and k not in r['types']:
                r['types'].append(k)
        if re.search(r'チャーター|貸切', text_of(f.get('料金') or '')) and '仕立' not in r['types']:
            r['types'].append('仕立')
        if ov.get('name') == '釣り船ルスプラージャ':
            r['types'] = []
        cap = re.search(r'(\d{1,3})\s*人まで', text_of(f.get('収容人数') or ''))
        if cap and not ov.get('include'):
            r['capacity'] = int(cap.group(1))
        hol = text_of(f.get('定休日') or '')
        if hol and not ov.get('include'):
            r['holidays'] = hol[:60]
        hours = text_of(f.get('営業時間') or '')
        if hours and not ov.get('include'):
            r['schedule_text'] = hours[:200]
        r['description'] = ov.get('description') or ''
        r['description'] = (r['description'] + '（天草宝島観光協会 スポット詳細 ' + url + '）').strip()
        area = re.search(r'data-taxonomy="area"[^>]*>([^<]+)', s)
        if area:
            r['access'] = '天草宝島観光協会のエリア: ' + nfkc(area.group(1)).strip()
        recs.append(r)
    return recs


# ---------------------------------------------------------------- 宗像市 大島・地島の遊漁船チラシ（書き写し）

MUNAKATA_PAGE = 'https://www.city.munakata.lg.jp/kanko/kiji0032398/index.html'
JINOSHIMA_PDF = 'https://www.city.munakata.lg.jp/kanko/kiji0032401/3_2401_1_jinoshima.pdf'
OSHIMA_PDF = 'https://www.city.munakata.lg.jp/kanko/kiji0032407/3_2407_1_20150710085648571.pdf'

# (番号, 船名, ふりがな, 連絡先❶, 連絡先❷, 対象魚, 釣り方)  番号はチラシの左上から右へ・上から下への順
JINOSHIMA = [
    (1, '宮地丸', 'みやじまる', '0940-62-1743', '090-8410-2487', ['ヤリイカ'], ['落とし込み', '夜焚き']),
    (2, '朝日丸', 'あさひまる', '0940-62-0806', '090-4983-7253', [], []),
    (3, '神宝丸', 'しんぽうまる', '0940-62-1269', '090-8668-5783', ['イカ', 'マダイ', 'ヒラメ'], []),
    (4, '厳島丸', 'いつくしままる', '0940-62-1749', '090-8410-2401', ['ヒラメ'], []),
    (5, '末吉丸', 'すえよしまる', '0940-62-0326', '090-7476-8312', ['根魚', 'レンコダイ', 'アマダイ', 'イカ'], []),
    (6, '昌富丸', 'しょうふまる', '0940-62-1260', '090-7474-9940', ['イカ'], []),
    (7, '金弥丸', 'きんやまる', '0940-62-0327', '090-7474-3232', ['アマダイ', 'レンコダイ', 'イカ'], []),
    (8, '壱岐丸', 'いきまる', '0940-62-0810', '090-7467-7527', ['マダイ', '青物'], []),
    (9, '大黒丸', 'だいこくまる', '0940-62-1744', '090-8837-6490', ['ヒラメ', 'イカ', 'マダイ'], []),
    (10, '東丸', 'あずままる', '0940-62-0804', '090-7386-9372', ['アオナ', 'アマダイ', 'レンコダイ', 'イカ'], []),
    (11, '金生丸', 'きんせいまる', '0940-62-1259', '090-7150-3077', ['アラカブ', 'ヒラメ', '青物', 'アマダイ', 'アオナ'], []),
    (12, '須賀丸', 'すがまる', '0940-62-0329', '090-7382-2213', ['アマダイ', 'レンコダイ', 'イカ'], []),
    (13, '蛭子丸', 'えびすまる', '0940-62-1745', '090-8837-6499', ['アマダイ', 'レンコダイ', 'ヒラメ', '青物', '根魚'], []),
    (14, '幸正丸', 'こうせいまる', '0940-62-2296', '090-4347-3404', ['マダイ', 'ヒラメ', 'イカ'], []),
    (15, '大福丸', 'だいふくまる', '0940-62-0336', '090-8766-8957', ['レンコダイ', 'アマダイ', 'イカ', '青物', 'マダイ'], []),
    (16, '金関丸', 'きんせきまる', '0940-62-1761', '090-7922-2027', ['イカ'], []),
    (17, '牧安丸', 'まきやすまる', '0940-62-1763', '080-1778-6806', ['アマダイ', 'レンコダイ', 'アジ', 'イサキ', 'ヒラメ'], []),
    (18, '愛宕丸', 'あたごまる', '0940-62-4712', '090-9570-3977', [], []),
    (19, '住吉丸', 'すみよしまる', '0940-62-1746', '090-4580-6315', ['ヒラメ', 'イカ', 'アジ', 'キス'], []),
    (20, '福寿丸', 'ふくじゅまる', '0940-62-1258', '090-7922-0255', ['アマダイ', 'レンコダイ', 'ヒラメ', '青物', 'イカ'], []),
    (21, '宝寿丸', 'ほうじゅまる', '0940-62-4311', '090-7297-5217', [], []),
    (22, '第三末吉丸', 'だいさんすえよしまる', '0940-62-1264', '090-7441-7725', ['アマダイ', 'レンコダイ', 'アオナ', 'タカバ'], []),
    (23, '住若丸', 'すみわかまる', '0940-62-1741', '080-5286-0434', ['カレイ', 'ヒラメ'], []),
    (24, '福栄丸', 'ふくえいまる', '0940-62-0815', '090-8396-6367', ['メバル', 'アラカブ', 'イカ', 'ヒラメ'], []),
    (25, '恵比須丸', 'えびすまる', '0940-62-0802', '090-7478-4984', ['アマダイ', 'レンコダイ', 'アオナ', 'タカバ', 'アラカブ', 'ヒラメ', 'イカ'], []),
]
# (番号, 船名, 連絡先, 対象魚, 釣り方, 追加項目)
OSHIMA = [
    (1, '第一大福丸', '090-9579-2123', [], [], {}),
    (2, '生漁丸', '090-2584-7094', [], [], {}),
    (3, '第三蛭子丸', '090-3198-4635', [], [], {'facilities': ['トイレ']}),
    (4, '新栄丸', '090-5738-9181', [], [], {}),
    (5, '友栄丸', '090-7168-1528', ['ヤリイカ'], ['夜焚き'], {}),
    (6, '明石丸', '090-4587-4543', [], ['落とし込み', 'サビキ'], {}),
    (7, '明生丸', '090-2500-5539', [], [], {}),
    (8, '海正丸', '090-4997-6973', ['イカ', 'マダイ', '青物', 'ヒラメ'], [], {}),
    (9, '第十八勇正丸 DREAM', '090-5384-1001', [], ['ルアー'], {'facilities': ['仮眠室', '電子レンジ']}),
    (10, '第二祐宝丸', '090-4589-0336', [], [], {'port': '神湊港'}),
    (11, '宮一丸', '090-3017-3500', [], [], {}),
    (12, '三社丸', '090-3013-5277', [], [], {}),
    (13, '第二蛭子丸', '090-3015-1896', ['レンコダイ', 'アマダイ', '根魚', 'ヤリイカ'], ['落とし込み'], {'types': ['乗合', '仕立']}),
    (14, '若潮丸', '090-8914-5977', [], ['テンヤ', 'タイラバ'], {}),
]


def build_munakata():
    # PDF を取得してキャッシュに残す（中身は画像なので、書き写した表を使う）
    for u in (MUNAKATA_PAGE, JINOSHIMA_PDF, OSHIMA_PDF):
        fetch_bytes(u)
    recs = []
    for no, name, kana, tel1, tel2, targets, methods in JINOSHIMA:
        r = empty_record(f'munakata-jinoshima:{no}', JINOSHIMA_PDF, name, '福岡県')
        r.update({'kana': kana, 'city': '宗像市', 'tel': tel2, 'targets': list(targets), 'methods': list(methods), 'stale': True,
                  'description': f'宗像市観光サイト掲載のチラシ「地島遊漁船で本格船釣りを!!」（2015年作成）に掲載。連絡先❶ {tel1}'})
        recs.append(r)
    for no, name, tel, targets, methods, extra in OSHIMA:
        r = empty_record(f'munakata-oshima:{no}', OSHIMA_PDF, name, '福岡県')
        r.update({'city': '宗像市', 'tel': tel, 'targets': list(targets), 'methods': list(methods), 'stale': True,
                  'description': '宗像市観光サイト掲載のチラシ「宗像大島遊漁船ガイド」（2015年作成）に掲載'})
        r.update(extra)
        recs.append(r)
    return recs


# ---------------------------------------------------------------- 既存データとの照合（見積もり）

def estimate(recs):
    from common import norm_name, norm_tel, PREF_CODE
    det = {}
    for r in recs:
        c = '%02d' % PREF_CODE[r['pref']]
        if c not in det:
            det[c] = json.load(open(os.path.join(ROOT, 'data', 'detail', c + '.json'), encoding='utf-8'))
    tel_idx, name_idx = {}, {}
    for c, d in det.items():
        for k, b in d.items():
            t = norm_tel(b.get('tel'))
            if t:
                tel_idx.setdefault(t, k)
            name_idx.setdefault((c, norm_name(b['name'])), k)
    by = {}
    for r in recs:
        c = '%02d' % PREF_CODE[r['pref']]
        t = norm_tel(r.get('tel'))
        hit = (t and tel_idx.get(t)) or name_idx.get((c, norm_name(r['name'])))
        r['_hit'] = hit
        key = r['src_id'].split(':')[0]
        e = by.setdefault(key, {'count': 0, 'new': 0, 'new_names': []})
        e['count'] += 1
        if not hit:
            e['new'] += 1
            e['new_names'].append(r['name'])
    return by


def build():
    recs = []
    recs += build_tsuritaro()
    recs += build_tisland()
    recs += build_munakata()
    by = estimate(recs)
    for r in recs:
        r.pop('_hit', None)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    log(f'wrote {len(recs)} records -> {OUT}')
    for k, v in by.items():
        log(f"  {k}: {v['count']} records, not in data/detail (tel/name) {v['new']}: {' / '.join(v['new_names'])}")
    return recs, by


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        return
    if args[0] == 'fetch':
        for u in args[1:]:
            s = fetch(u)
            print(u, None if s is None else len(s), cache_path(u))
    elif args[0] == 'build':
        build()


if __name__ == '__main__':
    main()
