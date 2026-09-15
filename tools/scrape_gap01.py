#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gap01（北海道の地域担当）: 取込済みでない北海道の釣り船一覧を取得して work/sources/gap01.json に出力する。

対象の一覧（優先順。同じ船が複数の一覧に載っていれば1レコードにまとめ、先の一覧の src_id/src_url を使う）
  sanook    Sanook Fishing！「北海道のサケ（秋鮭）/クロマグロ/ブリ釣り・おすすめ釣り船一覧」（表形式・3記事、2025-10更新）
  tsuritaro 全国遊漁船検索サイト 釣りたろう「北海道」の検索結果（1ページ）＋各船の「基本情報・釣り物」
  magurop   マグロ遊漁船情報まぐろっぷ 北海道カテゴリ（2ページ）＋各記事
  tsurip    釣り船情報つりっぷ 北海道カテゴリ＋各記事

まとめ方（build.py は同じ src どうしを電話＋名前一致でしかまとめないので、ここで重複を消す）
  1) 電話番号（数字）が一致  2) 同じ市町村で船名の中核が一致  3) 同じ市町村で公式サイトURLが完全一致（同じ船団）
  住所・港・座標は市町村が食い違う掲載からは補わない。

作法: 1ホスト直列・1秒間隔・robots.txt に従う・work/cache/gap01/<sha1>.html にキャッシュ（--refresh で再取得）
使い方: python3 tools/scrape_gap01.py [--refresh]
"""
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.robotparser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'work', 'cache', 'gap01')
OUT = os.path.join(ROOT, 'work', 'sources', 'gap01.json')
LOG = os.path.join(ROOT, 'work', 'logs', 'gap01.log')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')
FETCHED = '2026-09-15'
STALE_BEFORE = '2021-09-15'  # 最終更新が5年以上前なら stale
INTERVAL = 1.0
REFRESH = '--refresh' in sys.argv

SANOOK = [
    ('sanook-akiaji', 'https://sanook-fishing.com/boat-hokkaido-akiaji/'),
    ('sanook-maguro', 'https://sanook-fishing.com/boat-hokkaido-kuromaguro/'),
    ('sanook-buri', 'https://sanook-fishing.com/boat-hokkaido-buri/'),
]
TSURITARO_LIST = 'https://www.tsuritaro-fishing.com/search?prefecture=1'
MAGUROP_CATS = ['https://magurop.com/category/hokkaido/', 'https://magurop.com/category/hokkaido/page/2/']
TSURIP_CATS = ['https://tsurip.com/category/hokkaido/']

SNS_HOSTS = ('instagram.com', 'facebook.com', 'twitter.com', 'x.com', 'youtube.com', 'youtu.be', 'line.me',
             'lin.ee', 'tiktok.com', 'threads.net')
NOT_SITE_HOSTS = ('sanook-fishing.com', 'tsuritaro-fishing.com', 'magurop.com', 'tsurip.com', 'a8.net',
                  'pinterest.com', 'google.com', 'goo.gl')
FISH = set('サクラマス ヒラメ ブリ ソイ クロソイ ホッケ タラ マダラ カレイ マガレイ クロガシラ イカ マイカ スルメイカ サケ '
           'アキアジ マグロ クロマグロ アブラコ アイナメ ガヤ メバル ヤナギノマイ アオゾイ シイラ カラフトマス オヒョウ '
           'サバ ニシン 根魚 五目 マダイ ワラサ ヒラマサ カンパチ サワラ キンキ メヌケ ロックフィッシュ'.split())
PRICE_RE = re.compile(r'¥\s*(\d{1,3}(?:,\d{3})+|\d{3,6})|(\d{1,3}(?:,\d{3})+|\d{3,6})\s*円')
GENERIC_NOTE = re.compile(r'※?\s*料金は変更になる場合もありますので必ず事前にご確認ください')

_last = {}
_robots = {}
_count = {'done': 0}


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


# ------------------------------------------------------------------ 取得
def _curl(url):
    host = urllib.parse.urlsplit(url).netloc
    tmp = os.path.join(CACHE, '.tmp_fetch')
    code = ''
    for attempt in range(6):
        wait = INTERVAL - (time.time() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        if os.path.exists(tmp):
            os.remove(tmp)
        r = subprocess.run(['curl', '-s', '-L', '-m', '60', '-A', UA, '-o', tmp, '-w', '%{http_code}', url],
                           capture_output=True, text=True)
        _last[host] = time.time()
        code = r.stdout.strip()
        if code in ('429', '503') and attempt < 5:
            time.sleep(2 ** attempt * 2)
            continue
        break
    body = open(tmp, 'rb').read() if os.path.exists(tmp) else b''
    return code, body


def _cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode('utf-8')).hexdigest() + '.html')


def fetch(url, robots=True):
    os.makedirs(CACHE, exist_ok=True)
    path = _cache_path(url)
    if os.path.exists(path) and not REFRESH:
        return open(path, encoding='utf-8', errors='replace').read()
    if robots and not allowed(url):
        log('robots disallow: ' + url)
        return None
    code, body = _curl(url)
    _count['done'] += 1
    if code != '200' or not body:
        log('HTTP %s %s' % (code, url))
        return None
    with open(path, 'wb') as f:
        f.write(body)
    return body.decode('utf-8', 'replace')


def allowed(url):
    p = urllib.parse.urlsplit(url)
    base = '%s://%s' % (p.scheme, p.netloc)
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        txt = fetch(base + '/robots.txt', robots=False) or ''
        rp.parse(txt.splitlines() if re.search(r'(?im)^\s*user-agent\s*:', txt) else [])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


# ------------------------------------------------------------------ 文字列
def nfkc(s):
    return unicodedata.normalize('NFKC', s or '').strip()


def lines_of(frag):
    frag = re.sub(r'<(script|style|noscript)\b.*?</\1>', '', frag or '', flags=re.S | re.I)
    frag = re.sub(r'<br\s*/?>|</p>|</li>|</dd>|</dt>|</tr>|</h\d>', '\n', frag, flags=re.I)
    frag = html.unescape(re.sub(r'<[^>]+>', '', frag))
    out = []
    for ln in frag.split('\n'):
        ln = re.sub(r'[ \t\u3000\xa0]+', ' ', ln).strip()
        if ln:
            out.append(ln)
    return out


def text(frag):
    return ' '.join(lines_of(frag))


def hira(s):
    s = nfkc(s).replace(' ', '')
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s)


def digits(s):
    return re.sub(r'\D', '', s or '')


def tel_of(s):
    s = re.sub(r'[‐‑–—―−ー－]', '-', nfkc(s))
    m = re.search(r'(?<!\d)(0\d{1,4}-\d{1,4}-\d{3,4}|0\d{9,10})(?!\d)', s)
    if m and len(digits(m.group(1))) in (10, 11):
        return m.group(1)
    return None


def city_of(addr):
    m = re.match(r'北海道(?:余市郡|[^市町村郡]+郡)?(余市町|[^市町村郡]+?[市町村])', nfkc(addr))
    return m.group(1) if m else None


def split_list(s):
    s = re.sub(r'など$|等$', '', nfkc(s))
    out = []
    for x in re.split(r'[・、,，/]', s):
        x = re.sub(r'\((要確認|夏季|冬季)\)', '', x).strip()
        if x and x not in out:
            out.append(x)
    return out


def host_of(u):
    return urllib.parse.urlsplit(u).netloc.lower()


def is_sns(u):
    h = host_of(u)
    return any(h == x or h.endswith('.' + x) for x in SNS_HOSTS)


def is_site(u):
    h = host_of(u)
    return u.startswith('http') and not is_sns(u) and not any(h == x or h.endswith('.' + x) for x in NOT_SITE_HOSTS)


def norm_addr(a):
    a = re.sub(r'[\u200b-\u200d\ufeff]', '', nfkc(a))
    a = re.sub(r'(?<=\d)[‐‑–—―−ー－](?=\d)', '-', a)
    a = re.sub(r'^〒?\s*\d{3}-?\d{4}\s*', '', a).strip()
    a = re.sub(r'^(北海道)+', '北海道', a)
    if a and not re.match(r'(北海道|青森県)', a):
        a = '北海道' + a
    return a or None


def pref_of(addr):
    return '青森県' if (addr or '').startswith('青森県') else '北海道'


def new_rec(src_id, src_url, name):
    return {'src': 'gap01', 'src_id': src_id, 'src_url': src_url, 'name': name, 'kana': None, 'pref': '北海道',
            'city': None, 'address': None, 'port': None, 'lat': None, 'lon': None, 'tel': None, 'website': None,
            'sns': [], 'types': [], 'targets': [], 'methods': [], 'holidays': '', 'facilities': [], 'capacity': None,
            'access': '', 'description': '', 'plans': [], 'schedule_text': '', 'fetched': FETCHED}


def new_plan(name, kind, url):
    return {'name': name, 'kind': kind, 'targets': [], 'price': None, 'price_text': '', 'depart': '', 'return': '',
            'meet': '', 'season': '', 'days': '', 'includes': '', 'url': url}


def kind_of(s):
    s = nfkc(s)
    if '乗合' in s or '乗り合' in s or '相乗' in s:
        return '乗合'
    if '仕立' in s or '貸切' in s or 'チャーター' in s:
        return '仕立'
    if '渡船' in s or '瀬渡' in s:
        return '渡船'
    return ''


def has_price(s):
    return bool(PRICE_RE.search(nfkc(s)))


def price_of(txt, kind):
    """1人あたりの料金が1つだけ書かれていれば整数で返す（範囲・1隻料金・2,000円未満の不自然な値は None）。"""
    t = nfkc(txt)
    if kind == '仕立' or '隻' in t or 'チャーター' in t:
        return None
    amounts = set((a or b).replace(',', '') for a, b in PRICE_RE.findall(t))
    if len(amounts) == 1 and not re.search(r'[～〜~]|から|以上', t):
        v = int(amounts.pop())
        return v if v >= 2000 else None
    return None


def hhmm(s):
    s = nfkc(s)
    m = re.search(r'(\d{1,2})\s*(?:時|:)\s*(\d{2})?', s)
    if not m:
        return None
    h = int(m.group(1))
    if re.search(r'PM|午後', s) and h < 12:
        h += 12
    if h > 23:
        return None
    return '%02d:%s' % (h, m.group(2) or '00')


# ------------------------------------------------------------------ Sanook Fishing！
SANOOK_LABELS = ('種別', '予約方法', '住所', '釣り方', '対象魚', '備考')


def parse_sanook(key, url):
    s = fetch(url)
    if not s:
        return []
    mod = re.search(r'"dateModified"\s*:\s*"(\d{4}-\d{2}-\d{2})', s)
    modified = mod.group(1) if mod else ''
    recs = []
    area = ''
    for m in re.finditer(r'<h2\b[^>]*>(.*?)</h2>|<table\b.*?</table>', s, flags=re.S):
        if m.group(1) is not None:
            area = text(m.group(1))
            continue
        tb = m.group(0)
        rows = re.findall(r'<tr\b.*?</tr>', tb, flags=re.S)
        if not rows:
            continue
        head = re.findall(r'<td\b.*?</td>', rows[0], flags=re.S)
        if len(head) < 2 or '📞' not in text(head[1]):
            continue
        name = re.sub(r'[⭐★☆]|※\s*HPなし', '', text(head[0])).strip()
        f = {}
        for row in rows[1:]:
            cells = re.findall(r'<td\b.*?</td>', row, flags=re.S)
            i = 0
            while i < len(cells) - 1:
                lab = re.sub(r'[\s\u3000]', '', text(cells[i]))
                if lab in SANOOK_LABELS:
                    f[lab] = cells[i + 1]
                    i += 2
                else:
                    i += 1
        t = nfkc(text(f.get('種別', '')))
        if re.search(r'レンタルボート|貸しボート|貸し?釣船', t) and not re.search(r'乗合|仕立|チャーター|瀬渡|渡船', t):
            log('sanook %s: skip %s (種別=%s)' % (key, name, t))
            continue
        r = new_rec('%s:%s' % (key, nfkc(name)), url, name)
        for h in re.findall(r'href="([^"]+)"', head[0]):
            h = html.unescape(h)
            if is_site(h):
                r['website'] = h
                break
            if is_sns(h):
                r['sns'].append(h)
        r['tel'] = tel_of(text(head[1]).replace('📞', ''))
        href_tel = [digits(h) for h in re.findall(r'href="tel:([^"]+)"', head[1])]
        if r['tel'] and href_tel and href_tel[0] != digits(r['tel']):
            log('WARN tel text/href mismatch %s %s text=%s href=%s' % (key, name, r['tel'], href_tel[0]))
        r['types'] = [k for k, pat in (('乗合', '乗合'), ('仕立', '仕立|チャーター'), ('渡船', '瀬渡|渡船')) if re.search(pat, t)]
        for h in re.findall(r'href="([^"]+)"', f.get('予約方法', '')):
            h = html.unescape(h)
            if is_sns(h) and h not in r['sns']:
                r['sns'].append(h)
        raw = html.unescape(re.sub(r'<[^>]+>', '', re.sub(r'<br\s*/?>', '\u3000', f.get('住所', ''))))
        parts = [p.strip() for p in re.split(r'[\u3000]+', raw) if p.strip()]
        notes = []
        if parts:
            r['address'] = norm_addr(parts[0])
            rest = [p for p in parts[1:] if not p.startswith('※')]
            notes += [nfkc(p) for p in parts[1:] if p.startswith('※')]
            if rest:
                r['port'] = nfkc(rest[0])
            elif r['address']:
                mm = re.search(r'\s(\S*(?:港|漁港|マリーナ|桟橋|船溜り?|埠頭))$', r['address'])
                if mm:
                    r['port'] = mm.group(1)
                    r['address'] = r['address'][:mm.start()].strip()
        r['pref'] = pref_of(r['address'])
        r['city'] = city_of(r['address'] or '')
        r['methods'] = split_list(text(f.get('釣り方', '')))
        r['targets'] = split_list(text(f.get('対象魚', '')))
        segs = []
        for b in [nfkc(x) for x in lines_of(f.get('備考', ''))]:
            segs += [x.strip() for x in re.split(r'(?=※)', b) if x.strip()]
        acc = []
        for x in segs:
            if x.startswith('※'):
                notes.append(x)
                continue
            for y in [y.strip() for y in x.split('・') if y.strip()]:
                if '休' in y and not r['holidays']:
                    r['holidays'] = y
                elif re.search(r'駅|空港|フェリー|車で|徒歩|バス', y):
                    acc.append(y)
                elif y.startswith('レンタルあり'):
                    r['facilities'].append('貸し道具')
                else:
                    notes.append(y)
        r['access'] = '・'.join(acc)
        r['description'] = ('%s。' % area + ' '.join(notes))[:100] if notes else ''
        r['stale'] = bool(modified) and modified < STALE_BEFORE
        r['as_of'] = modified
        recs.append(r)
    log('sanook %s: %d boats (modified %s)' % (key, len(recs), modified))
    return recs


# ------------------------------------------------------------------ 釣りたろう
def _dls(frag):
    return [(text(a), b) for a, b in re.findall(r'<dl[^>]*>\s*<dt>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>', frag, flags=re.S)]


def split_kana(title):
    m = re.match(r'^(.*?)\s*\(([ぁ-ゖァ-ヶー・\s]+)\)\s*$', nfkc(title))
    if m:
        return re.sub(r'\s+', ' ', m.group(1)).strip(), hira(m.group(2))
    return re.sub(r'\s+', ' ', nfkc(title)).strip(), None


def addr_port(s):
    s = nfkc(s)
    m = re.match(r'^(.*?)\s*\(([^()]+)\)\s*$', s)
    if m:
        return norm_addr(m.group(1)), m.group(2).strip()
    return norm_addr(s), None


def tsuritaro_plans(r, chunk_html, durl):
    pm = re.search(r'<dl class="plan-name">\s*<dt>(.*?)</dt>\s*<dd>(.*?)</dd>', chunk_html, flags=re.S)
    if not pm:
        return
    kind = kind_of(text(pm.group(1)))
    pname = nfkc(text(pm.group(2))) or nfkc(text(pm.group(1)))
    pd = dict(_dls(chunk_html[pm.end():]))
    # 料金: 「ラベル行 → 料金行」の組を取り出す
    items, fish, buf = [], [], []
    for x in [nfkc(x) for x in lines_of(pd.get('料金', ''))]:
        if has_price(x):
            m = re.match(r'^(.+?)\s*料金', x)
            items.append((m.group(1).strip() if m else ' '.join(buf), x))
            buf = []
        elif x in FISH:
            fish.append(x)
        else:
            buf.append(x)
    if not items:
        items = [('', '')]
    # 出港時間: 「午前便/午後便」など便ごとに分かれていれば便ごとのプランにする。季節で違う時刻などは schedule_text へ
    sched = [nfkc(x) for x in lines_of(pd.get('出港時間', ''))]
    entries, label = [], ''
    for ln in sched:
        lab = re.search(r'([^\s\d/]{1,4}便)', ln)
        if lab:
            label = lab.group(1)
        t = hhmm(ln)
        if t:
            entries.append((label, t))
            label = ''
    if len(entries) >= 2 and all(l for l, _ in entries):
        departs = entries
    elif entries and len({t for _, t in entries}) == 1:
        departs = [('', entries[0][1])]
    else:
        departs = [('', '')]
        if sched:
            st = '%s: %s' % (pname, ' '.join(sched))
            r['schedule_text'] = (r['schedule_text'] + ' / ' + st if r['schedule_text'] else st)[:150]
    for lab, pline in items:
        for dlab, dep in departs:
            nm = lab if (len(items) > 1 and lab) else pname
            if dlab:
                nm = '%s（%s）' % (nm, dlab)
            p = new_plan(nm, kind, durl)
            p['price_text'] = pline
            p['price'] = price_of(pline, kind) if pline else None
            p['targets'] = list(fish)
            p['depart'] = dep
            r['plans'].append(p)
    if kind and kind not in r['types']:
        r['types'].append(kind)


def parse_tsuritaro():
    s = fetch(TSURITARO_LIST)
    if not s:
        return []
    boxes = re.findall(r'<div class="ship-box">(.*?)<!-- /\.ship-box -->', s, flags=re.S)
    recs = []
    for n, b in enumerate(boxes, 1):
        m = re.search(r'<h4 class="ship-name"><a href="(https://www\.tsuritaro-fishing\.com/ship/(\d+))">(.*?)</a>', b, flags=re.S)
        if not m:
            continue
        sid = m.group(2)
        name, kana = split_kana(text(m.group(3)))
        r = new_rec('tsuritaro:%s' % sid, TSURITARO_LIST, name)
        r['kana'] = kana
        a = re.search(r'<p class="address">(.*?)</p>', b, flags=re.S)
        if a:
            r['address'], r['port'] = addr_port(text(a.group(1)))
        p = re.search(r'<p class="phone">(.*?)</p>', b, flags=re.S)
        if p:
            r['tel'] = tel_of(text(p.group(1)))
        d = dict(_dls(b))
        if '業種' in d:
            r['types'] = [k for k in (kind_of(x) for x in text(d['業種']).split('、')) if k]
        if '特徴' in d:
            r['description'] = ' '.join(lines_of(d['特徴']))[:100]
        # 基本情報・釣り物
        durl = 'https://www.tsuritaro-fishing.com/ship/data/%s' % sid
        ds = fetch(durl)
        if ds:
            i, j = ds.find('<ul class="wrap list-plan'), ds.find('<div class="wrap owner">')
            plan_html = ds[i:j] if i >= 0 and j > i else ''
            for chunk in plan_html.split('<div class="plan-body">')[1:]:
                tsuritaro_plans(r, chunk, durl)
            owner = ds[j:] if j >= 0 else ''
            blog = None
            for dt, dd in _dls(owner):
                hrefs = [html.unescape(h) for h in re.findall(r'href="(http[^"]+)"', dd)]
                if dt == '所在地':
                    addr, port = addr_port(text(dd))
                    r['address'] = addr or r['address']
                    r['port'] = port or r['port']
                elif dt == '電話番号':
                    r['tel'] = tel_of(text(dd)) or r['tel']
                elif dt == 'その他':
                    o = nfkc(text(dd))
                    if re.search(r'出港|出船|漁港', o):
                        r['schedule_text'] = (r['schedule_text'] + ' / ' + o if r['schedule_text'] else o)[:150]
                for h in hrefs:
                    if is_sns(h):
                        if h not in r['sns']:
                            r['sns'].append(h)
                    elif is_site(h):
                        if 'ブログ' in dt:
                            blog = blog or h
                        elif not r['website']:
                            r['website'] = h
            r['website'] = r['website'] or blog
        r['pref'] = pref_of(r['address'])
        r['city'] = city_of(r['address'] or '')
        recs.append(r)
        log('tsuritaro %d/%d %s' % (n, len(boxes), name))
    return recs


# ------------------------------------------------------------------ まぐろっぷ / つりっぷ（WordPress SWELL の同じ書式）
def parse_swell(key, host, cat_urls):
    urls = []
    for cu in cat_urls:
        s = fetch(cu)
        if not s:
            continue
        for card in re.findall(r'<li class="p-postList__item">(.*?)</li>', s, flags=re.S):
            hm = re.search(r'href="(https://%s/[^"]+/)"' % re.escape(host), card)
            if hm and '/category/' not in hm.group(1) and hm.group(1) not in urls:
                urls.append(hm.group(1))
    recs = []
    for n, u in enumerate(urls, 1):
        s = fetch(u)
        if not s:
            continue
        pc = s.find('<div class="post_content">')
        if pc < 0:
            continue
        share = s.find('よかったらシェア', pc)
        main_top = s.find('<main')
        region = s[main_top if main_top >= 0 else 0:share if share > 0 else len(s)]
        if not re.search(r'href="https://%s/category/hokkaido/' % re.escape(host), region):
            log('%s skip (not hokkaido) %s' % (key, u))
            continue
        body = s[pc:share if share > 0 else len(s)]
        body = re.sub(r'<div class="p-adBox.*?</div></div></div>', '', body, flags=re.S)
        # 船名: 構造化データ(LocalBusiness) → 記事タイトル
        name = None
        dp = re.search(r'data-props="([^"]+)"', s)
        if dp:
            try:
                name = json.loads(html.unescape(dp.group(1)))['structuredData']['name']
            except (ValueError, KeyError, TypeError):
                name = None
        if not name:
            tm = re.search(r'<h1[^>]*class="c-postTitle__ttl"[^>]*>(.*?)</h1>', s, flags=re.S)
            name = text(tm.group(1)) if tm else u
        name = re.sub(r'\s+', ' ', nfkc(name)).strip()
        slug = urllib.parse.unquote(u.rstrip('/').rsplit('/', 1)[-1])
        r = new_rec('%s:%s' % (key, slug), cat_urls[0], name)
        mod = re.search(r'"dateModified"\s*:\s*"(\d{4}-\d{2}-\d{2})', s)
        modified = mod.group(1) if mod else ''
        # 電話
        tels = []
        for h in re.findall(r'href="tel:([^"]+)"', body):
            t = tel_of(urllib.parse.unquote(h))
            if t and digits(t) not in [digits(x) for x in tels]:
                tels.append(t)
        alltext = nfkc(text(body))
        if not tels:
            t = tel_of(alltext)
            if t:
                tels.append(t)
        if tels:
            r['tel'] = tels[0]
        notes = []
        if len(tels) > 1:
            notes.append('電話(別番号) ' + ' '.join(tels[1:]))
        reg = re.search(r'遊漁船登録\s*(北海道|青森県)\s*(第?\s*\d+\s*号?)', alltext)
        if reg:
            notes.insert(0, '遊漁船登録 %s%s' % (reg.group(1), reg.group(2).replace(' ', '')))
        # 見出しごとの節
        parts = re.split(r'<h[2-4][^>]*>(.*?)</h[2-4]>', body, flags=re.S)
        secs = {}
        for i in range(1, len(parts) - 1, 2):
            secs.setdefault(text(parts[i]), parts[i + 1])
        # 表の行（左セル=項目、右セル=値）
        rows = []
        for tr in re.findall(r'<tr\b.*?</tr>', body, flags=re.S):
            cells = re.findall(r'<t[dh]\b.*?</t[dh]>', tr, flags=re.S)
            if len(cells) >= 2:
                rows.append((nfkc(text(cells[0])), cells[1]))
        asof = re.search(r'(\d{4})/(\d{2})/(\d{2})[^<]*現在の情報', body)
        if asof:
            modified = max(modified, '%s-%s-%s' % asof.groups())
        # 料金 → plans
        for lab, val in rows:
            if lab in ('魚', '釣り方', 'トイレ') or not (kind_of(lab) or has_price(text(val))):
                continue
            kind = kind_of(lab)
            pending, last = None, None
            for ln in [GENERIC_NOTE.sub('', nfkc(x)).strip() for x in lines_of(val)]:
                if not ln or 'レンタル' in ln:
                    continue
                if has_price(ln):
                    p = new_plan(pending or re.sub(r'\(.*?\)', '', lab).strip() or '料金', kind, u)
                    p['price_text'] = ln
                    p['price'] = price_of(ln, kind)
                    r['plans'].append(p)
                    last, pending = p, None
                elif last is not None and (ln.startswith('※') or re.search(r'名まで|名迄|人まで', ln)) and not pending:
                    last['price_text'] += ' ' + ln
                elif not ln.startswith('※'):
                    pending = ln
            if kind and kind not in r['types']:
                r['types'].append(kind)
        # 魚・釣り方・トイレ（節 or 表の行）
        kv = dict(rows)
        for k, v in secs.items():
            kv.setdefault(nfkc(k), v)
        if '魚' in kv:
            r['targets'] = split_list(text(kv['魚']))
        if '釣り方' in kv:
            r['methods'] = split_list(text(kv['釣り方']))
        if 'トイレ' in kv and nfkc(text(kv['トイレ'])).startswith('有'):
            r['facilities'].append('トイレ')
        # 出港場所
        dep = next((v for k, v in secs.items() if '出港場所' in k), None)
        if dep:
            for ln in lines_of(dep.split('<iframe')[0].split('<noscript')[0]):
                ln = nfkc(ln)
                if ln.startswith('※'):
                    continue
                if ln.startswith('〒') or re.match(r'(北海道|青森県)', ln):
                    r['address'] = r['address'] or norm_addr(ln)
                elif not r['port'] and len(ln) <= 40:
                    r['port'] = ln
        ll = re.search(r'!2d(\d+\.\d+)!3d(\d+\.\d+)', body)
        if ll:
            lon, lat = float(ll.group(1)), float(ll.group(2))
            if 41.0 <= lat <= 46.0 and 139.0 <= lon <= 146.5:
                r['lat'], r['lon'] = lat, lon
        # SNS・ホームページ
        blog = None
        for li in re.findall(r'<li[^>]*>(.*?)</li>', secs.get('SNS', ''), flags=re.S):
            lab = text(re.sub(r'<a\b.*?</a>', '', li, flags=re.S))
            for h in [html.unescape(x) for x in re.findall(r'href="(http[^"]+)"', li)]:
                if is_sns(h):
                    if h not in r['sns']:
                        r['sns'].append(h)
                elif is_site(h):
                    if 'ブログ' in lab:
                        blog = blog or h
                    elif 'ホームページ' in lab or 'HP' in lab or 'サイト' in lab:
                        r['website'] = r['website'] or h
        if not r['website']:
            for h in [html.unescape(x) for x in re.findall(r'href="(http[^"]+)"', body)]:
                if is_site(h) and 'youtube' not in h:
                    r['website'] = h
                    break
        r['website'] = r['website'] or blog
        if blog and blog != r['website'] and blog not in r['sns']:
            r['sns'].append(blog)
        r['pref'] = pref_of(r['address'])
        r['city'] = city_of(r['address'] or '')
        r['description'] = ' / '.join(notes)[:100]
        r['stale'] = bool(modified) and modified < STALE_BEFORE
        r['as_of'] = modified
        recs.append(r)
        log('%s %d/%d %s (%s)' % (key, n, len(urls), name, modified))
    return recs


# ------------------------------------------------------------------ まとめ
LISTF = ('sns', 'types', 'targets', 'methods', 'facilities')
GEOF = ('address', 'port', 'lat', 'lon', 'city')


def core_name(s):
    s = nfkc(s).lower()
    s = re.sub(r'\(.*?\)', '', s)
    return re.sub(r'高速|遊漁船|釣り?船|つり船|釣舟|北海道|[\W_]', '', s)


def exact_url(u):
    return re.sub(r'^https?://(www\.)?', '', (u or '').strip().lower()).rstrip('/')


def merge_into(a, b, note=None):
    geo_ok = not a.get('city') or not b.get('city') or a['city'] == b['city']
    for k, v in b.items():
        if k in ('src', 'src_id', 'src_url', 'fetched', 'src_urls', 'name', 'tel'):
            continue
        if k in GEOF and not geo_ok:
            continue
        if k in LISTF:
            a[k] = list(dict.fromkeys((a.get(k) or []) + (v or [])))
        elif k == 'plans':
            seen = {(p['name'], p['price_text'], p['depart']) for p in a['plans']}
            a['plans'] += [p for p in v if (p['name'], p['price_text'], p['depart']) not in seen]
        elif k == 'stale':
            a['stale'] = bool(a.get('stale')) and bool(v)
        elif k == 'as_of':
            a['as_of'] = max(a.get('as_of') or '', v or '')
        elif a.get(k) in (None, '', []) and v not in (None, '', []):
            a[k] = v
    if not a.get('tel') and b.get('tel'):
        a['tel'] = b['tel']
    if note:
        a['description'] = (a['description'] + ' / ' + note if a['description'] else note)[:160]
    for u in b.get('src_urls') or [b['src_url']]:
        if u not in a['src_urls']:
            a['src_urls'].append(u)


def main():
    all_recs = []
    for key, url in SANOOK:
        all_recs += parse_sanook(key, url)
    all_recs += parse_tsuritaro()
    all_recs += parse_swell('magurop', 'magurop.com', MAGUROP_CATS)
    all_recs += parse_swell('tsurip', 'tsurip.com', TSURIP_CATS)
    for r in all_recs:
        r['name'] = re.sub(r'\s+', ' ', nfkc(r['name'])).strip()
    # 1) 電話番号
    by_key, recs = {}, []
    for r in all_recs:
        d = digits(r.get('tel'))
        k = 'tel:' + d if d else 'name:%s:%s' % (r.get('city'), core_name(r['name']))
        if k in by_key:
            merge_into(by_key[k], r)
        else:
            r['src_urls'] = [r['src_url']]
            by_key[k] = r
            recs.append(r)
    # 2) 同じ市町村で船名の中核が一致 / 3) 同じ市町村で公式サイトURLが完全一致（同じ船団）
    changed = True
    while changed:
        changed = False
        for i in range(len(recs)):
            for j in range(i + 1, len(recs)):
                a, b = recs[i], recs[j]
                if not (a.get('city') and a['city'] == b.get('city')):
                    continue
                ca, cb = core_name(a['name']), core_name(b['name'])
                same_name = len(ca) >= 2 and ca == cb
                same_site = bool(a.get('website')) and exact_url(a['website']) == exact_url(b.get('website'))
                if not (same_name or same_site):
                    continue
                if same_name:
                    note = ('電話(別番号) %s' % b['tel']) if b.get('tel') and digits(b['tel']) != digits(a.get('tel')) else None
                else:
                    note = '同じ船団: %s%s' % (b['name'], (' ' + b['tel']) if b.get('tel') else '')
                log('merge %s <- %s (%s)' % (a['src_id'], b['src_id'], 'name' if same_name else 'site'))
                merge_into(a, b, note)
                del recs[j]
                changed = True
                break
            if changed:
                break
    out = []
    for r in recs:
        if not r.get('stale'):
            r.pop('stale', None)
        r.pop('as_of', None)
        out.append(r)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    log('done %d requests, %d raw records -> %d records written to %s' % (_count['done'], len(all_recs), len(out), OUT))


if __name__ == '__main__':
    main()
