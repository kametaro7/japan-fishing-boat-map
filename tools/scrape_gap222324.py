# -*- coding: utf-8 -*-
"""gap222324: 静岡・愛知・三重の地域一覧（観光協会・漁協・地域釣りポータル等）から釣り船を取り込む。

キャッシュ: work/cache/gap222324/  ログ: work/logs/gap222324.log
1ホスト直列・1秒間隔・robots.txt に従う。

  python3 tools/scrape_gap222324.py fetch URL [URL ...]   # 取得してキャッシュパスを表示
  python3 tools/scrape_gap222324.py build [--refresh]      # 一覧を解析して work/sources/gap222324.json を出力
"""
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import NOT_OFFICIAL_HOSTS, SNS_HOSTS, clean_url, host_in, host_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'work', 'cache', 'gap222324')
LOG = os.path.join(ROOT, 'work', 'logs', 'gap222324.log')
OUT = os.path.join(ROOT, 'work', 'sources', 'gap222324.json')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
INTERVAL = 1.0
REFRESH = '--refresh' in sys.argv
FETCHED = '2026-09-15'

_host_lock = defaultdict(threading.Lock)
_host_last = {}
_robots = {}


# ---------------------------------------------------------------- 取得
def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + msg
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')
    print(line, file=sys.stderr)


def cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode('utf-8')).hexdigest() + '.html')


def _raw_get(url):
    host = urllib.parse.urlsplit(url).netloc
    with _host_lock[host]:
        wait = INTERVAL - (time.time() - _host_last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        err = None
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ja,en;q=0.5'})
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                    status = r.status
                _host_last[host] = time.time()
                return status, body
            except urllib.error.HTTPError as e:
                _host_last[host] = time.time()
                if e.code in (429, 503):
                    time.sleep(2 ** attempt * 2)
                    err = e
                    continue
                return e.code, e.read() if hasattr(e, 'read') else b''
            except Exception as e:  # noqa
                _host_last[host] = time.time()
                err = e
                time.sleep(2 ** attempt)
        raise err


def allowed(url):
    sp = urllib.parse.urlsplit(url)
    base = '%s://%s' % (sp.scheme, sp.netloc)
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            status, body = _raw_get(base + '/robots.txt')
            txt = body.decode('utf-8', 'replace') if status == 200 else ''
            if '<html' in txt[:500].lower():  # robots.txt 無しで HTML が返るサイトは許可扱い
                txt = ''
            rp.parse(txt.splitlines())
        except Exception as e:  # noqa
            log('robots error %s %s' % (base, e))
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def decode(body):
    m = re.search(rb'charset=["\']?([A-Za-z0-9_\-]+)', body[:3000])
    enc = m.group(1).decode().lower() if m else 'utf-8'
    if enc in ('shift_jis', 'sjis', 'x-sjis', 'shift-jis'):
        enc = 'cp932'
    try:
        return body.decode(enc)
    except (UnicodeDecodeError, LookupError):
        for e in ('utf-8', 'cp932', 'euc-jp'):
            try:
                return body.decode(e)
            except UnicodeDecodeError:
                pass
        return body.decode('utf-8', 'replace')


def fetch(url):
    """キャッシュ優先で HTML テキストを返す。取れなければ None。"""
    os.makedirs(CACHE, exist_ok=True)
    p = cache_path(url)
    if os.path.exists(p) and not REFRESH:
        with open(p, 'rb') as f:
            return decode(f.read())
    if not allowed(url):
        log('robots disallow %s' % url)
        return None
    try:
        status, body = _raw_get(url)
    except Exception as e:  # noqa
        log('fetch error %s %s' % (url, e))
        return None
    if status != 200:
        log('HTTP %s %s' % (status, url))
        return None
    with open(p, 'wb') as f:
        f.write(body)
    log('fetched %s (%d bytes)' % (url, len(body)))
    return decode(body)


def fetch_many(urls):
    """ホストごとに直列、ホスト間は並列で取得する。"""
    by_host = defaultdict(list)
    for u in urls:
        by_host[urllib.parse.urlsplit(u).netloc].append(u)
    res = {}

    def run(us):
        for u in us:
            res[u] = fetch(u)
    ths = [threading.Thread(target=run, args=(us,)) for us in by_host.values()]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return res


# ---------------------------------------------------------------- 共通の整形
def nfkc(s):
    return unicodedata.normalize('NFKC', str(s or '')).strip()


def strip_tags(s):
    s = re.sub(r'(?is)<br\s*/?>', '\n', s or '')
    return html.unescape(re.sub(r'<[^>]+>', '', s))


def clean_name(s):
    s = re.sub(r'\s+', ' ', nfkc(strip_tags(s))).strip()
    # 「勝 丸」「鯛 屋」のように1文字ずつ空けた表記は詰める
    if re.fullmatch(r'[\u3040-\u30ff\u4e00-\u9fff々](?: +[\u3040-\u30ff\u4e00-\u9fff々])+', s):
        s = s.replace(' ', '')
    return s


def name_key(s):
    return re.sub(r'[\s・()（）「」【】]', '', nfkc(s))


TEL_RE = re.compile(r'0\d{1,4}-\d{1,4}-\d{3,4}')


def tel_fmt(s):
    s = re.sub(r'[ー－−‐―–]', '-', nfkc(s))
    m = TEL_RE.search(s)
    return m.group(0) if m else None


def digits(s):
    return re.sub(r'\D', '', s or '')


CITY_RE = re.compile(r'^(?:静岡県|愛知県|三重県)(?:[^\d市町村]{1,4}郡)?(四日市市|[^\d]{1,5}?[市町村])')


def city_of(addr):
    m = CITY_RE.match(addr or '')
    return m.group(1) if m else None


EXTRA_NOT_OFFICIAL = ('tsurisoku.com', '1091.co.jp', 'turinet.com', 'fishing-station.jp', 'tsurimaru.jp',
                      'yugyosen.com', 'point-i.jp', 'ishiguro-gr.com', 'imakey-fishing.com', 'rgr.jp')


def set_link(r, u, base=None):
    if not u:
        return
    u = html.unescape(u.strip())
    if base:
        u = urllib.parse.urljoin(base, u)
    u = clean_url(u)
    if not u:
        return
    h = host_of(u)
    if host_in(h, SNS_HOSTS):
        if u not in r['sns']:
            r['sns'].append(u)
        return
    if host_in(h, NOT_OFFICIAL_HOSTS) or host_in(h, EXTRA_NOT_OFFICIAL) or 'rep_tsuri_view' in u:
        return  # 掲載サイト・釣果ポータル・地図は公式サイトにしない
    if not r['website']:
        r['website'] = u


def base_rec(key, n, url, name, pref):
    return {'src': 'gap222324', 'src_id': '%s:%s' % (key, n), 'src_url': url, 'name': name, 'kana': None,
            'pref': pref, 'city': None, 'address': None, 'port': None, 'lat': None, 'lon': None, 'tel': None,
            'website': None, 'sns': [], 'types': [], 'targets': [], 'methods': [], 'holidays': '',
            'facilities': [], 'capacity': None, 'access': '', 'description': '', 'plans': [],
            'schedule_text': '', 'fetched': FETCHED}


def page_text_digits(t):
    """電話番号照合用：ページ本文を NFKC にして数字だけを残す。"""
    return digits(nfkc(re.sub(r'<[^>]+>', ' ', t)))


# ---------------------------------------------------------------- 一覧1: 近畿釣り情報（imakey-fishing.com）三重
IMAKEY = 'https://imakey-fishing.com/info/'
IMAKEY_PAGES = ([('boat_mie/boat_%s.html' % a, []) for a in ('toba', 'shima', 'minamiise', 'kihoku', 'owase', 'kumano')]
                + [('ferry_mie/ferry_%s.html' % a, ['渡船'])
                   for a in ('toba', 'shima', 'minamiise', 'taiki', 'kihoku', 'owase', 'kumano', 'yokkaichi')]
                + [('fishing_boat_mie.html', [])])


def parse_imakey():
    recs, seen, raw = [], {}, 0
    for path, types in IMAKEY_PAGES:
        url = IMAKEY + path
        t = fetch(url)
        if not t:
            log('imakey missing %s' % url)
            continue
        title = nfkc(strip_tags(re.search(r'(?s)<title>(.*?)</title>', t).group(1))).split('|')[0].strip()
        date = (re.findall(r'20\d\d-\d\d-\d\d', t) or [''])[0]
        dig = page_text_digits(t)
        stem = path.split('/')[-1].replace('.html', '')
        n = 0
        for tab in re.findall(r'(?s)<table.*?</table>', t):
            for tr in re.findall(r'(?s)<tr(?:\s[^>]*)?>(.*?)</tr>', tab):
                cells = re.findall(r'(?s)<t[hd][^>]*>(.*?)</t[hd]>', tr)
                if len(cells) < 5 or clean_name(cells[0]) == '船名':
                    continue
                n += 1
                raw += 1
                name = clean_name(cells[0])
                kana = None
                m = re.match(r'^(.*?)[(（]([ぁ-んー]+)[)）]$', name)
                if m:
                    name, kana = m.group(1).strip(), m.group(2)
                if re.search(r'釣り?堀|レンタルボート|貸しボート', name):  # 釣り堀・貸しボート店は除外
                    log('imakey excluded %s (%s)' % (name, path))
                    continue
                tel = tel_fmt(strip_tags(cells[1]))
                addr = nfkc(strip_tags(cells[2])) or None
                assert tel is None or digits(tel) in dig, (url, name, tel)
                key = (name_key(name), digits(tel))
                if key in seen:
                    r = seen[key]
                    for ty in types:
                        if ty not in r['types']:
                            r['types'].append(ty)
                    continue
                pref = '三重県' if (addr or '').startswith('三重県') else None
                assert pref, (url, name, addr)
                r = base_rec('imakey-' + stem, n, url, name, pref)
                r['kana'] = kana
                r['tel'] = tel
                r['address'] = addr
                r['city'] = city_of(addr)
                for c in (cells[0], cells[4]):
                    for h in re.findall(r'href="([^"]+)"', c):
                        set_link(r, h)
                r['types'] = list(types)
                r['description'] = '近畿釣り情報「%s」%sに掲載' % (title, ('（%s 更新）' % date) if date else '')
                seen[key] = r
                recs.append(r)
    log('imakey rows=%d records=%d' % (raw, len(recs)))
    return recs


# ---------------------------------------------------------------- 一覧2: 南知多町観光協会「ふらっと南知多」釣り
MINAMICHITA = 'http://minamichita-kk.com/exper/fishing'


def parse_minamichita():
    t = fetch(MINAMICHITA)
    sec = t[t.find('釣り船のご案内'):t.find('class="des"')]
    recs = []
    for n, m in enumerate(re.finditer(r'(?s)<li><img[^>]*alt="([^"]*)"[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a></li>', sec), 1):
        port, href, name = nfkc(m.group(1)), m.group(2), clean_name(m.group(3))
        r = base_rec('minamichita', n, MINAMICHITA, name, '愛知県')
        r['city'] = '南知多町'
        r['port'] = port or None
        set_link(r, href)
        r['description'] = '南知多町観光協会「ふらっと南知多」釣りページの「釣り船のご案内」（%s）に掲載' % port
        recs.append(r)
    log('minamichita records=%d' % len(recs))
    return recs


# ---------------------------------------------------------------- 一覧3: 東海の釣り情報（b.rgr.jp）静岡・愛知・三重のリンク集
RGR = {'sz': ('https://b.rgr.jp/d/sz.shtml', '静岡県', '静岡の釣り情報'),
       'ai': ('https://b.rgr.jp/d/ai.shtml', '愛知県', '愛知の釣り情報'),
       'me': ('https://b.rgr.jp/d/me.shtml', '三重県', '三重の釣り情報')}
RGR_TYPE = {'船': None, '舟': None, '釣り船': None, 'ゲーム': None, 'シーバス': None, '黒鯛': None,
            '乗り合い': '乗合', '磯': '磯渡し', '磯渡し': '磯渡し', '磯上げ': '磯渡し', '渡': '渡船',
            '霞一文字渡': '渡船', '筏': '筏', '筏釣り': '筏', 'カセ': 'カセ', 'ル': 'ルアー', 'ルアー': 'ルアー',
            'ルアー船': 'ルアー', 'ジギング船': 'ジギング', 'ガイド': 'ガイド', 'ガイド船': 'ガイド'}
RGR_RENT = {'貸', '貸し', '貸ボート', '貸しボート', 'レンタル', '掘', '堀', '釣り掘り'}
RGR_TARGET = {'シーバス': 'シーバス', '黒鯛': '黒鯛', 'ワカサギ': 'ワカサギ'}
RGR_EXCLUDE_NAMES = {'南伊豆遊漁船組合'}  # 組合サイトへのリンク（船ではない）。組合の一覧は minamiizu で取り込む


def parse_rgr(key):
    url, pref, label = RGR[key]
    t = fetch(url)
    s = t.find('<h4>渡船・釣り')
    sec = t[s:t.find('<h4>', s + 10)]
    recs, seen, area, n, excluded = [], set(), '', 0, []
    for m in re.finditer(r'(?s)<h5>([^<]*)</h5>|<p>(.*?)</p>', sec):
        if m.group(1) is not None:
            area = re.sub(r'\s+', '', nfkc(m.group(1)))
            continue
        a = re.match(r'(?s)\s*<a href="([^"]+)"[^>]*>(.*?)</a>(.*)', m.group(2))
        if not a:
            continue
        n += 1
        href, name = a.group(1), clean_name(a.group(2))
        rest = strip_tags(re.sub(r'(?s)<a[^>]*>.*?</a>', '', a.group(3)))
        rest = re.sub(r'[()（）\s]', '', nfkc(rest))
        parts = [p for p in rest.split('・') if p]
        code = parts[0] if parts else ''
        toks = [x for x in code.split('/') if x]
        type_toks = [x for x in toks if x in RGR_TYPE or x in RGR_RENT]
        locs = [x for x in toks if x not in RGR_TYPE and x not in RGR_RENT and x not in RGR_TARGET]
        if name in RGR_EXCLUDE_NAMES or (type_toks and all(x in RGR_RENT for x in type_toks)):
            excluded.append('%s(%s)' % (name, code))
            continue
        k = (name_key(name), href)
        if k in seen:
            continue
        seen.add(k)
        r = base_rec('rgr-' + key, n, url, name, pref)
        r['port'] = locs[-1] if locs else None
        for x in toks:
            ty = RGR_TYPE.get(x)
            if ty and ty not in r['types']:
                r['types'].append(ty)
            if x in RGR_TARGET and RGR_TARGET[x] not in r['targets']:
                r['targets'].append(RGR_TARGET[x])
        set_link(r, href)
        r['description'] = '「%s」（b.rgr.jp）渡船・釣り船リンク集%sに掲載%s' % (
            label, ('「%s」' % area) if area else '', ('（%s）' % code) if code else '')
        recs.append(r)
    log('rgr-%s rows=%d records=%d excluded=%s' % (key, n, len(recs), ', '.join(excluded)))
    return recs


# ---------------------------------------------------------------- 一覧4: 大井川港漁協「大井川港遊漁船について」所属船名簿
OIGAWA = 'http://www.oigawako-gyokyo.com/boat/'
OIGAWA_RULES = ['4月から9月までの操業は、午前6時より午後1時までとする。', '10月の操業は、午前6時30分より午後1時までとする。',
                '11月より3月までの操業は、午前7時より午後1時までとする。']


def parse_oigawa():
    t = fetch(OIGAWA)
    body = t[t.find('<body'):]
    s = body.find('所属船名簿')
    tab = re.search(r'(?s)<table>.*?</table>', body[s:]).group(0)
    dig = page_text_digits(t)
    for rule in OIGAWA_RULES:
        assert rule in body, rule
    recs, cur, n = [], None, 0
    for tr in re.findall(r'(?s)<tr>(.*?)</tr>', tab):
        ths = re.findall(r'(?s)<th[^>]*>(.*?)</th>', tr)
        tds = re.findall(r'(?s)<td[^>]*>(.*?)</td>', tr)
        if not ths or clean_name(ths[0]) == '船名':
            continue
        boat = clean_name(ths[0])
        if not tds:  # rowspan：前の行と同じ事業者の2隻目
            cur['_boats'].append(boat)
            continue
        n += 1
        # tds = [氏名(出力しない), 住所, 連絡先, HP他]
        addr = nfkc(strip_tags(tds[1]))
        contact = nfkc(strip_tags(tds[2]))
        land, mobile = None, None
        for line in contact.splitlines():
            tel = tel_fmt(line)
            if not tel:
                continue
            if '携帯' in line:
                mobile = mobile or tel
            else:
                land = land or tel
        r = base_rec('oigawa', n, OIGAWA, boat, '静岡県')
        r['address'] = addr
        r['city'] = city_of(addr)
        r['port'] = '大井川港'
        r['tel'] = land or mobile
        for x in (land, mobile):
            assert x is None or digits(x) in dig, (boat, x)
        r['_mobile'] = mobile if land else None
        for h in re.findall(r'href="([^"]+)"', tds[3]):
            set_link(r, h)
        r['schedule_text'] = '組合の申し合わせ: 操業は4〜9月 6:00〜13:00、10月 6:30〜13:00、11〜3月 7:00〜13:00'
        r['_boats'] = [boat]
        cur = r
        recs.append(r)
    for r in recs:
        desc = '大井川港漁業協同組合「大井川港遊漁船について」所属船名簿に掲載'
        if len(r['_boats']) > 1:
            desc += '。所属船: ' + '、'.join(r['_boats'])
        if r['_mobile']:
            desc += '。携帯 ' + r['_mobile']
        r['description'] = desc
        del r['_boats'], r['_mobile']
    log('oigawa records=%d' % len(recs))
    return recs


# ---------------------------------------------------------------- 一覧5: 南伊豆遊漁船組合「南伊豆出港の遊漁船組合登録 全釣り船」
MINAMIIZU = 'http://www.j-office.jp/izu-turibune/turibune/index.htm'


def parse_minamiizu():
    t = fetch(MINAMIIZU)
    assert '局番は' in t and '0558' in t
    dig = page_text_digits(t)
    recs, ports, n = [], [], 0
    for tr in re.findall(r'(?is)<tr>(.*?)</tr>', t):
        if re.search(r'(?i)<table', tr):
            continue
        tds = re.findall(r'(?is)<td[^>]*>(.*?)</td>', tr)
        texts = [re.sub(r'\s+', '', nfkc(strip_tags(x))) for x in tds]
        heads = [re.match(r'^<(.+?)出船>$', x) for x in texts]
        if any(heads):
            ports = [h.group(1) if h else None for h in heads]
            continue
        if len(tds) < 3:
            continue
        for g in range(len(tds) // 3):
            name, cap, tel = texts[g * 3], texts[g * 3 + 1], texts[g * 3 + 2]
            local = re.sub(r'[ー－−‐―]', '-', tel)
            if not re.fullmatch(r'\d{2}-\d{4}', local) or not name or name in ('0', '船名'):
                continue
            assert digits(local) in dig, (name, tel)
            n += 1
            r = base_rec('minamiizu', n, MINAMIIZU, name, '静岡県')
            r['city'] = '南伊豆町'
            r['port'] = ports[g] if g < len(ports) else None
            r['tel'] = '0558-' + local  # ページ上部に「局番は 0558 です」
            r['capacity'] = int(cap) if cap.isdigit() and int(cap) > 0 else None
            for h in re.findall(r'(?i)href="([^"]+)"', tds[g * 3]):
                set_link(r, h, base=MINAMIIZU)
            r['description'] = '南伊豆遊漁船組合「南伊豆出港の遊漁船組合登録 全釣り船」に掲載（人数 %s）' % cap
            r['stale'] = True
            recs.append(r)
    log('minamiizu records=%d' % len(recs))
    return recs


# ---------------------------------------------------------------- 一覧6: いとう漁協「釣り船情報」
ITO = 'http://www.soitoshigyokyo.jf-net.ne.jp/fune.html'
ITO_PLANS = {
    '久志丸': [dict(name='料金', kind='', targets=[], price=12000, price_text='1人：12000円~')],
    '田中丸': [dict(name='料金', kind='', targets=[], price=None, price_text='3時間4人 23,000円')],
    'ドルフィン丸': [dict(name='料金', kind='', targets=[], price=None,
                      price_text='4時間 4人迄 50,000円 (1人追加毎に7,000円)')],
    '庄吉丸': [dict(name='金目・アコウ 乗合', kind='乗合', targets=['金目', 'アコウ'], price=20000,
                 price_text='１名20,000円、２名から16,000円（氷付）'),
            dict(name='夜イカ・カサゴ・カワハギ・ワラサ・タイ五目 乗合', kind='乗合',
                 targets=['夜イカ', 'カサゴ', 'カワハギ', 'ワラサ', 'タイ五目'], price=10000, price_text='10,000円（氷付）')],
    '青木丸': [dict(name='仕立（4人まで・4時間）', kind='仕立', targets=[], price=None,
                 price_text='4人まで・4時間 25,000円(餌、道具、氷含)'),
            dict(name='夜釣り（仕立）', kind='仕立', targets=[], price=None, price_text='夜釣り：32,000円')],
}
ITO_TARGET_DROP = {'他', 'その他', 'ほか', '季節物', 'いろいろ'}


def parse_ito():
    t = fetch(ITO)
    s = t.find('釣り船情報(リンク)')
    sec = t[s:]
    dig = page_text_digits(t)
    flat = re.sub(r'\s+', '', nfkc(strip_tags(sec)))
    recs, n = [], 0
    for tab in re.findall(r'(?s)<table id="fish">(.*?)</table>', sec):
        h5 = re.search(r'(?s)<h5>(.*?)</h5>', tab)
        if not h5:
            continue
        head = nfkc(strip_tags(h5.group(1)))
        m = re.match(r'^■\s*(.+?)\s*[(（][^)）]*[)）]+\s*[-−ー]+\s*(.+?)\s*[-−ー]+$', head)
        assert m, head
        n += 1
        name, port = clean_name(m.group(1)), m.group(2)  # 括弧内の個人名は出力しない
        r = base_rec('ito', n, ITO, name, '静岡県')
        r['city'] = '伊東市'
        r['port'] = port
        th2 = re.search(r'(?s)<th class="th02">(.*?)</th>', tab)
        for h in re.findall(r'href="([^"]+)"', th2.group(1) if th2 else ''):
            set_link(r, h)
        body = nfkc(strip_tags(re.search(r'(?s)<td class="td01">(.*?)</td>', tab).group(1)))
        tm = re.search(r'電話(?:・FAX共)?\s*[(（]\s*([0-9\-]+)\s*[)）]', body)
        tel = tel_fmt(tm.group(1)) if tm else tel_fmt(body)
        r['tel'] = tel
        assert tel and digits(tel) in dig, (name, tel)
        mm = re.search(r'携帯\s*[(（]\s*([0-9\-]+)\s*[)）]', body)
        if mm and tm:
            r['description'] = 'いとう漁協「釣り船情報」（%s）に掲載。携帯 %s' % (port, tel_fmt(mm.group(1)))
        else:
            r['description'] = 'いとう漁協「釣り船情報」（%s）に掲載' % port
        for b in re.findall(r'\[([^\]]+)\]', body):
            b = re.sub(r'[（(][^）)]*[）)]', '', b)
            for x in re.split(r'[、,/／]', b):
                x = re.sub(r'^[春夏秋冬]\s*[:：]', '', x.strip())
                x = re.sub(r'(などいろいろ|など|他)$', '', x).strip()
                if x and x not in ITO_TARGET_DROP and x not in r['targets']:
                    r['targets'].append(x)
        if '仕立' in body:
            r['types'].append('仕立')
        if '乗合' in body:
            r['types'].append('乗合')
        cm = re.search(r'(\d+)名まで', body)
        if cm:
            r['capacity'] = int(cm.group(1))
        for p in ITO_PLANS.get(name, []):
            assert re.sub(r'\s+', '', nfkc(p['price_text'])) in flat, (name, p['price_text'])
            plan = {'name': p['name'], 'kind': p['kind'], 'targets': p['targets'], 'price': p['price'],
                    'price_text': p['price_text'], 'depart': None, 'return': None, 'meet': '', 'season': '',
                    'days': '', 'includes': '', 'url': ITO}
            r['plans'].append(plan)
        r['stale'] = True
        recs.append(r)
    log('ito records=%d' % len(recs))
    return recs


# ---------------------------------------------------------------- 照合と出力
PREF_CODE = {'静岡県': '22', '愛知県': '23', '三重県': '24'}
NAME_PREFIX = re.compile(r'^(釣船|釣り船|釣り舟|釣舟|ルアー船|遊漁船|三重)\s*')


def name_variants(name):
    k = name_key(name)
    v = {k, name_key(NAME_PREFIX.sub('', nfkc(name)))}
    v |= {x.replace('釣船', '丸') for x in list(v)}
    return {x for x in v if x}


def existing_index():
    idx = {}
    for pref, code in PREF_CODE.items():
        d = json.load(open(os.path.join(ROOT, 'data', 'detail', code + '.json'), encoding='utf-8'))
        tels, names = set(), set()
        for b in d.values():
            srcs = {x.get('src') for x in (b.get('links') or [])}
            if srcs and srcs <= {'gap222324'}:
                continue  # この出力から地図に入っただけの船宿は「既存」として数えない
            if digits(b.get('tel')):
                tels.add(digits(b.get('tel')))
            names |= name_variants(b.get('name') or '')
        idx[pref] = (tels, names)
    return idx


def is_known(r, idx):
    tels, names = idx[r['pref']]
    ds = {digits(r['tel'])} | {digits(x) for x in TEL_RE.findall(r['description'] or '')}
    if any(d and d in tels for d in ds):
        return True
    return bool(name_variants(r['name']) & names)


def build():
    lists = [('imakey', parse_imakey), ('minamichita', parse_minamichita),
             ('rgr-sz', lambda: parse_rgr('sz')), ('rgr-ai', lambda: parse_rgr('ai')),
             ('rgr-me', lambda: parse_rgr('me')), ('oigawa', parse_oigawa),
             ('minamiizu', parse_minamiizu), ('ito', parse_ito)]
    recs, summary = [], {}
    for i, (key, fn) in enumerate(lists, 1):
        rs = fn()
        recs.extend(rs)
        log('progress %d/%d %s' % (i, len(lists), key))
        summary[key] = rs
    idx = existing_index()
    # 一覧をまたいだ同一船のまとまり（同じ県で電話か名前が一致）を数えて、新規の見込みを出す
    groups = []
    for r in recs:
        keys = {('t', r['pref'], digits(r['tel']))} if r['tel'] else set()
        keys |= {('n', r['pref'], v) for v in name_variants(r['name'])}
        hit = [g for g in groups if g['keys'] & keys]
        if hit:
            g = hit[0]
            for h in hit[1:]:
                g['keys'] |= h['keys']
                g['recs'] += h['recs']
                groups.remove(h)
        else:
            g = {'keys': set(), 'recs': []}
            groups.append(g)
        g['keys'] |= keys
        g['recs'].append(r)
    new_groups = [g for g in groups if not any(is_known(r, idx) for r in g['recs'])]
    stats = {}
    for key, rs in summary.items():
        stats[key] = {'records': len(rs), 'new_est': sum(1 for r in rs if not is_known(r, idx)),
                      'stale': sum(1 for r in rs if r.get('stale'))}
    stats['_total'] = {'records': len(recs), 'groups': len(groups), 'new_groups': len(new_groups),
                       'new_groups_nonstale': sum(1 for g in new_groups if any(not r.get('stale') for r in g['recs'])),
                       'by_pref_new': {p: sum(1 for g in new_groups if g['recs'][0]['pref'] == p) for p in PREF_CODE}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    with open(os.path.join(ROOT, 'work', 'logs', 'gap222324.summary.json'), 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    log('wrote %s (%d records)' % (OUT, len(recs)))
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if args and args[0] == 'fetch':
        r = fetch_many(args[1:])
        for u in args[1:]:
            print(('OK  ' if r.get(u) else 'NG  ') + cache_path(u) + '  ' + u)
    elif args and args[0] == 'build':
        build()
    else:
        print(__doc__)
