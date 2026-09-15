# -*- coding: utf-8 -*-
"""gap4241: 長崎県・佐賀県の地域の釣り船一覧を取り込む。

一覧:
  goto      五島の島たび【公式】（五島市）フィッシング  https://goto.nagasaki-tabinet.com/fishing
  gotofeat  同 釣り特集「五島の釣り事情」の遊漁船・瀬渡し  https://goto.nagasaki-tabinet.com/feature/fishing6
  ikinavi   壱岐釣りナビ「壱岐の船釣り料金比較9選」       https://ikiisland-concierge.com/useful/fishing-boat/overall-fishing-boat/
  tsushima  対馬観光物産協会 体験（釣り）                  https://www.tsushima-net.org/experience/<slug>
  navitime  NAVITIME「長崎県/佐賀県のつり船」              https://www.navitime.co.jp/category/0101018002/42/

作法: 1ホスト直列・1秒間隔・robots.txt に従う・work/cache/gap4241/ にキャッシュ（--refresh で再取得）。
出力: work/sources/gap4241.json
"""
import hashlib
import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.robotparser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, WORK, nfkc, norm_tel, tel_display, save_json  # noqa: E402

SRC = 'gap4241'
CACHE = os.path.join(WORK, 'cache', SRC)
LOG = os.path.join(WORK, 'logs', SRC + '.log')
OUT = os.path.join(WORK, 'sources', SRC + '.json')
FETCHED = '2026-09-15'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
REFRESH = '--refresh' in sys.argv
INTERVAL = 1.0

_last = {}
_robots = {}


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + msg
    print(line)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def _raw_get(url):
    host = urllib.parse.urlparse(url).netloc
    wait = INTERVAL - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    delay = 2
    for attempt in range(6):
        _last[host] = time.time()
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'ja'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 5:
                time.sleep(delay)
                delay *= 2
                continue
            return e.code, e.read() if hasattr(e, 'read') else b''
        except Exception as e:  # noqa: BLE001
            if attempt < 2:
                time.sleep(delay)
                delay *= 2
                continue
            log('ERROR %s %s' % (url, e))
            return None, b''
    return None, b''


def allowed(url):
    p = urllib.parse.urlparse(url)
    base = '%s://%s' % (p.scheme, p.netloc)
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        st, body = fetch(base + '/robots.txt', check=False)
        if st == 200 and body:
            rp.parse(body.decode('utf-8', 'ignore').splitlines())
        else:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def fetch(url, check=True):
    """(status, bytes) を返す。キャッシュ優先。"""
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.sha1(url.encode('utf-8')).hexdigest()
    path = os.path.join(CACHE, key + '.html')
    meta = os.path.join(CACHE, key + '.url')
    if os.path.exists(path) and not REFRESH:
        with open(path, 'rb') as f:
            return 200, f.read()
    if check and not allowed(url):
        log('SKIP robots %s' % url)
        return None, b''
    st, body = _raw_get(url)
    log('GET %s %s %d' % (st, url, len(body or b'')))
    if st == 200:
        with open(path, 'wb') as f:
            f.write(body)
        with open(meta, 'w', encoding='utf-8') as f:
            f.write(url + '\n')
    return st, body


def get_text(url):
    st, body = fetch(url)
    return body.decode('utf-8', 'ignore') if st == 200 else ''


def strip_tags(s):
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'<[^>]+>', '', s)
    return htmlmod.unescape(s).replace('\u3000', ' ').strip()


def page_lines(t):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', '', t, flags=re.S | re.I)
    s = htmlmod.unescape(re.sub(r'<[^>]+>', '\n', t))
    return [ln.strip() for ln in s.split('\n') if ln.strip()]


PREF_RE = re.compile(r'(長崎県|佐賀県)')


def city_of(addr):
    a = nfkc(addr)
    m = re.match(r'(?:長崎県|佐賀県)(.+?郡.+?[町村]|.+?市)', a)
    return m.group(1) if m else None


def blank(**kw):
    r = {
        'src': SRC, 'src_id': None, 'src_url': None, 'name': None, 'kana': None, 'pref': None, 'city': None,
        'address': None, 'port': None, 'lat': None, 'lon': None, 'tel': None, 'website': None, 'sns': [],
        'types': [], 'targets': [], 'methods': [], 'holidays': '', 'facilities': [], 'capacity': None,
        'access': '', 'description': '', 'plans': [], 'schedule_text': '', 'fetched': FETCHED,
    }
    r.update(kw)
    return r


SNS_HOSTS = ('instagram.com', 'facebook.com', 'twitter.com', 'x.com', 'line.me', 'lin.ee', 'youtube.com', 'tiktok.com')


def split_site(urls):
    web, sns = None, []
    for u in urls:
        if not u or not u.startswith('http'):
            continue
        if any(h in u for h in SNS_HOSTS):
            if u not in sns:
                sns.append(u)
        elif web is None:
            web = u
    return web, sns


def yen(s):
    """'5,000円' '3万円' '1日チャーター 20,000円' → 整数。書式が曖昧なら None。"""
    s = nfkc(s)
    m = re.search(r'(\d+(?:\.\d+)?)\s*万\s*円', s)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.search(r'(\d{1,3}(?:,\d{3})+|\d+)\s*円', s)
    if m:
        return int(m.group(1).replace(',', ''))
    return None


def short(s, n=100):
    s = re.sub(r'\s+', ' ', strip_tags(s or ''))
    return s if len(s) <= n else s[:n - 1] + '…'


PER_PERSON = re.compile(r'円\s*～?\s*[/／]\s*1?(?:人|名)|(?<!\d)1\s*(?:人|名)(?!以上|まで|乗)|一人|お一人|おひとり|大人')


def price_plans(text, url):
    """料金欄のテキスト → plans。※で始まる行は直前のプランの注記、金額の無い短い行は見出しとして扱う。"""
    plans, head = [], ''
    for raw in text.split('\n'):
        ln = nfkc(raw).strip().lstrip('・').strip()
        if not ln:
            continue
        if ln.startswith(('※', '*', '→')):
            if plans:
                plans[-1]['price_text'] += ' ' + ln
            continue
        amount = yen(ln)
        if amount is None:
            m = re.search(r'[：:]\s*(\d{1,3}(?:,\d{3})+)\s*$', ln)
            amount = int(m.group(1).replace(',', '')) if m else None
        if amount is None:
            if re.match(r'^[【\[■]', ln) or len(ln) <= 30:
                head = re.sub(r'[【】\[\]■]', ' ', ln).strip()
            continue
        ctx = head + ' ' + ln
        if PER_PERSON.search(ln):
            kind, price = ('乗合' if re.search(r'乗合|乗り合い', ctx) else ''), amount
        elif re.search(r'乗合|乗り合い', ln):
            kind, price = '乗合', amount
        elif re.search(r'貸切|チャーター|仕立|1隻|一隻|\d+名まで|\d+名乗り', ctx):
            kind, price = '仕立', amount
        else:
            kind, price = '', None
        name = (head + ' ' + ln).strip() if head else ln
        if any(p['name'] == name for p in plans):
            continue
        plans.append({'name': short(name, 60), 'kind': kind, 'targets': [], 'price': price, 'price_text': ln,
                      'depart': '', 'return': '', 'meet': '', 'season': '', 'days': '', 'includes': '', 'url': url})
    return plans


# ---------------------------------------------------------------- 五島の島たび
GOTO_LIST = 'https://goto.nagasaki-tabinet.com/fishing'
# 他ソースの types 表記（乗合/仕立/渡船）に合わせる。「船釣り遊漁船」だけでは乗合/仕立が分からないので付けない
GOTO_TYPES = {'磯釣り渡船': '渡船'}


def goto_records():
    out = []
    t = get_text(GOTO_LIST)
    ids = []
    for i in re.findall(r'href="(?:https://goto\.nagasaki-tabinet\.com)?/fishing/(\d+)"', t):
        if i not in ids:
            ids.append(i)
    log('goto list %d' % len(ids))
    for n, i in enumerate(ids, 1):
        url = 'https://goto.nagasaki-tabinet.com/fishing/%s' % i
        h = get_text(url)
        if not h:
            continue
        name = strip_tags(re.search(r'<title>(.*?)\|', h, flags=re.S).group(1))
        rows = {}
        for th, td in re.findall(r'<tr>\s*<th>(.*?)</th>\s*<td>(.*?)</td>\s*</tr>', h, flags=re.S):
            rows.setdefault(strip_tags(th), td)
        def dd_tags(label):
            vals = []
            for dd in re.findall(r'<dt[^>]*>%s</dt>\s*<dd[^>]*>(.*?)</dd>' % label, h, flags=re.S):
                for a in re.findall(r'<a[^>]*>(.*?)</a>', dd, flags=re.S):
                    v = strip_tags(a)
                    if v and v not in vals:
                        vals.append(v)
            return vals
        cats = dd_tags('カテゴリー')
        areas = dd_tags('エリア')
        if cats and all(c == '釣具店' for c in cats):
            log('goto skip tackle shop %s' % name)
            continue
        lines = page_lines(h)
        kana, desc = None, ''

        def nz(s):
            return re.sub(r'\s+', '', nfkc(s))
        stop = lines.index('エリア') if 'エリア' in lines else len(lines)
        idx = [k for k in range(stop) if nz(lines[k]) == nz(name)]
        if idx:
            k = idx[-1] + 1
            if k < stop and re.fullmatch(r'[ぁ-んゔー・\s]+', lines[k]):
                kana = nz(lines[k])
                k += 1
            elif re.fullmatch(r'[ぁ-んゔー・\s]+', name):
                kana = nz(name)
            if k < stop and lines[k] not in ('カテゴリー', 'エリア', 'スライドショーを見る'):
                desc = short(lines[k])
        addr = strip_tags(rows.get('住所', '')) or None
        if addr:
            addr = re.sub(r'^〒?\d{3}-\d{4}\s*', '', nfkc(addr)).strip()
            if re.fullmatch(r'(長崎県|佐賀県)?', addr):
                addr = None
        tel_raw = strip_tags(rows.get('電話番号', '')) or strip_tags(rows.get('予約先：電話番号', ''))
        links = re.findall(r'href="([^"]+)"', rows.get('ウェブサイト', ''))
        web, sns = split_site([htmlmod.unescape(u) for u in links])
        targets = [x.strip() for x in re.split(r'[、,，/・\n]', strip_tags(rows.get('対象魚', ''))) if x.strip()]
        methods = [x.strip() for x in strip_tags(rows.get('釣り方', '')).split('\n') if x.strip()]
        cap = re.search(r'(\d+)', nfkc(strip_tags(rows.get('最大定員', ''))))
        plans = price_plans(strip_tags(rows.get('料金（目安）', '')), url)
        lat = lon = None
        m = re.search(r'!2d(1[23]\d\.\d+)!3d(3[0-5]\.\d+)', h)
        if m:
            lon, lat = float(m.group(1)), float(m.group(2))
        types = [GOTO_TYPES[c] for c in cats if c in GOTO_TYPES]
        if any(p['kind'] == '仕立' for p in plans) and '仕立' not in types:
            types.append('仕立')
        desc = desc or ('カテゴリー: ' + '・'.join(cats) if cats else '')
        fac = []
        for key in ('全長', '重量'):
            if rows.get(key):
                fac.append('%s %s' % (key, strip_tags(rows[key])))
        out.append(blank(
            src_id='goto:%s' % i, src_url=url, name=nfkc(name), kana=kana, pref='長崎県', city='五島市',
            address=addr, port=strip_tags(rows.get('乗船場所', '')) or None, lat=lat, lon=lon,
            tel=tel_display(tel_raw), website=web, sns=sns, types=types, targets=targets, methods=methods,
            capacity=int(cap.group(1)) if cap else None, description=desc, plans=plans,
            schedule_text=('エリア: ' + '・'.join(areas)) if areas else '',
            facilities=[]))
        log('goto %d/%d %s' % (n, len(ids), name))
    return out


# ---------------------------------------------------------------- 五島市 釣り特集（遊漁船・瀬渡し）
GOTO_FEAT = 'https://goto.nagasaki-tabinet.com/feature/fishing6'


def gotofeat_records():
    out = []
    lines = page_lines(get_text(GOTO_FEAT))
    # 見出しは特集INDEXと本文に2回ずつ出るので、本文側（最後の出現）を使う
    def last(s):
        return len(lines) - 1 - lines[::-1].index(s)
    try:
        a = last('五島でオフショアを楽しませてくれる遊漁船！')
        b = last('オフショアでは楽しめない駆け引きを楽しめるなら瀬渡し！')
        c = last('釣りの聖地を守りましょう！')
        if not a < b < c:
            raise ValueError
    except ValueError:
        log('gotofeat: section not found')
        return out
    seen = set()
    # 遊漁船: 「船名」「電話：...」の2行
    for k in range(a + 1, b):
        m = re.match(r'電話[：:]\s*(.+)', lines[k])
        if m and not lines[k - 1].startswith(('電話', '船名')):
            name = lines[k - 1]
            key = (name, norm_tel(m.group(1)))
            if key in seen:
                continue
            seen.add(key)
            out.append(blank(src_id='gotofeat:遊漁船:%s' % name, src_url=GOTO_FEAT, name=name, pref='長崎県',
                             city='五島市', tel=tel_display(m.group(1)), types=[],
                             description='五島市公式釣り特集で「オフショアの遊漁船」として紹介'))
    # 瀬渡し: 「〇エリア」「船名：...」「電話：...」
    area = ''
    for k in range(b + 1, c):
        ln = lines[k]
        if re.fullmatch(r'\S+エリア', ln):
            area = ln
        m = re.match(r'船名[：:]\s*(.+)', ln)
        if m and k + 1 < c:
            t = re.match(r'電話[：:]\s*(.+)', lines[k + 1])
            if not t:
                continue
            tels = [x for x in re.split(r'[/／]', t.group(1)) if norm_tel(x)]
            out.append(blank(src_id='gotofeat:瀬渡し:%s' % m.group(1).strip(), src_url=GOTO_FEAT,
                             name=m.group(1).strip(), pref='長崎県', city='五島市',
                             tel=tel_display(tels[0]) if tels else None, types=['渡船'],
                             schedule_text=('五島市の%s' % area) if area else '',
                             description=('他の電話: ' + ' / '.join(x.strip() for x in tels[1:])) if len(tels) > 1 else ''))
    log('gotofeat %d' % len(out))
    return out


# ---------------------------------------------------------------- 壱岐釣りナビ
IKI_URL = 'https://ikiisland-concierge.com/useful/fishing-boat/overall-fishing-boat/'


def _table_rows(tbl):
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', tbl, flags=re.S):
        cells = [re.sub(r'\s*\n\s*', ' ', strip_tags(c)).strip()
                 for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, flags=re.S)]
        rows.append(cells)
    return rows


def iki_records():
    out = []
    h = get_text(IKI_URL)
    if not h:
        return out
    body = re.sub(r'<script.*?</script>|<style.*?</style>', '', h, flags=re.S)
    a = body.find('壱岐の遊漁船')
    a = body.find('一覧', a)
    z = body.find('>まとめ<', a)
    seg = body[a:z]
    # 各船の区切り = 「[住所]」を含むブロック。見出しは h2/h3/h4
    heads = [(m.start(), strip_tags(m.group(2))) for m in re.finditer(r'<h([234])[^>]*>(.*?)</h\1>', seg, flags=re.S)]
    heads = [(p, re.sub(r'\s+', ' ', t)) for p, t in heads
             if t and not re.search(r'特徴|参考価格|人気の釣り物|体験レポート|釣果', t)]
    for n, (pos, title) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(seg)
        blk = seg[pos:end]
        txt = '\n'.join(page_lines(blk))
        addr = re.search(r'\[住所\]\s*(.+)', txt)
        tel = re.search(r'\[電話番号\]\s*(.+)', txt)
        name = re.sub(r'[\[［].*?[\]］]', '', title).strip()
        name = re.sub(r'\s*[\(（]せいは[\)）]', '', name)
        if not addr and not tel:
            log('ikinavi skip (no addr/tel) %s' % name)
            continue
        address = re.sub(r'^〒?\s*\d{3}-\d{4}\s*', '', nfkc(addr.group(1)).replace('\u200b', '')).strip() if addr else None
        links = [htmlmod.unescape(u) for u, lt in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', blk, flags=re.S)
                 if '公式HP' in strip_tags(lt) or 'ご利用はこちら' in strip_tags(lt)]
        links = [u for u in links if 'ikiisland-concierge.com' not in u]
        web, sns = split_site(links)
        facilities = []
        for key in ('送迎', 'トイレ', 'レンタル釣具', '貸切・チャーター', '道具不要のプラン'):
            m = re.search(r'\[%s\]\s*(.+)' % re.escape(key), txt)
            if m:
                facilities.append('%s: %s' % (key, m.group(1).strip()))
        plans = []
        for tbl in re.findall(r'<table[^>]*>(.*?)</table>', blk, flags=re.S):
            for cells in _table_rows(tbl):
                if len(cells) < 3 or cells[0].startswith('ジャンル'):
                    continue
                pname, fish, ptxt = cells[0], cells[1], cells[2]
                people = cells[3] if len(cells) > 3 else ''
                season = cells[4] if len(cells) > 4 else ''
                kind, price = '', None
                m = re.search(r'([\d,]+)\s*円\s*[～~]?\s*/\s*人', nfkc(ptxt))
                if m and re.fullmatch(r'\d{1,3}(?:,\d{3})*|\d+', m.group(1)):
                    kind, price = '乗合', int(m.group(1).replace(',', ''))
                elif re.search(r'乗合|乗り合い', people) and yen(ptxt) and not re.search(r'[～~]', ptxt):
                    kind, price = '乗合', yen(ptxt)
                elif re.search(r'チャーター|貸切', ptxt + people) and not re.search(r'\d\s*[～~]\s*\d', nfkc(ptxt)):
                    kind, price = '仕立', yen(ptxt)
                plans.append({
                    'name': pname, 'kind': kind,
                    'targets': [x for x in re.split(r'[・、\s]+', re.sub(r'[（(].*?[)）]', '', fish)) if x],
                    'price': price, 'price_text': (ptxt + (' / ' + people if people else '')).strip(),
                    'depart': '', 'return': '', 'meet': '', 'season': season if season not in ('–', '-', 'ー') else '',
                    'days': '', 'includes': '', 'url': IKI_URL})
        out.append(blank(src_id='ikinavi:%s' % name, src_url=IKI_URL, name=name, pref='長崎県', city='壱岐市',
                         address=address, tel=tel_display(tel.group(1)) if tel else None, website=web, sns=sns,
                         types=[k for k in ('乗合', '仕立') if any(p['kind'] == k for p in plans)
                                or (k == '仕立' and re.search(r'\[貸切・チャーター\]\s*可', txt))],
                         facilities=facilities, plans=plans))
        log('ikinavi %s' % name)
    return out


# ---------------------------------------------------------------- 対馬観光物産協会
TS_BASE = 'https://www.tsushima-net.org'
TS_SITEMAP = TS_BASE + '/sitemap-dynamic/sitemap-dynamic-ZXhwZXJpZW5jZS86c2x1Zw.xml'
BOAT_WORDS = re.compile(r'遊漁船|釣り船|釣船|渡船|瀬渡|ジギング|キャスティング|船釣り|乗合|乗り合い|チャーター')


def _nuxt(h):
    m = re.search(r'id="__NUXT_DATA__">(.*?)</script>', h, flags=re.S)
    if not m:
        return None
    arr = json.loads(m.group(1))
    wrappers = {'Reactive', 'ShallowReactive', 'Ref', 'ShallowRef', 'EmptyRef', 'EmptyShallowRef', 'Set', 'Map'}

    def res(i, depth=0):
        if not isinstance(i, int) or i < 0 or i >= len(arr) or depth > 8:
            return None
        v = arr[i]
        if isinstance(v, dict):
            return {k: res(x, depth + 1) for k, x in v.items()}
        if isinstance(v, list):
            if len(v) == 2 and isinstance(v[0], str) and v[0] in wrappers:
                return res(v[1], depth + 1)
            return [res(x, depth + 1) for x in v]
        return v
    return arr, res


def _strings(o, acc):
    if isinstance(o, str):
        acc.append(o)
    elif isinstance(o, dict):
        for v in o.values():
            _strings(v, acc)
    elif isinstance(o, list):
        for v in o:
            _strings(v, acc)
    return acc


def tsushima_records():
    out = []
    sm = get_text(TS_SITEMAP)
    slugs = []
    for loc in re.findall(r'<loc>([^<]+)</loc>', sm):
        s = urllib.parse.unquote(loc.strip()).rsplit('/experience/', 1)[-1]
        if s and '/' not in s and s not in slugs:
            slugs.append(s)
    log('tsushima slugs %d' % len(slugs))
    for n, slug in enumerate(slugs, 1):
        url = TS_BASE + '/experience/' + urllib.parse.quote(slug)
        h = get_text(url)
        if not h:
            continue
        parsed = _nuxt(h)
        if not parsed:
            continue
        arr, res = parsed
        item = None
        for i, v in enumerate(arr):
            if isinstance(v, dict) and 'slug' in v and 'title' in v and isinstance(v.get('slug'), int) \
                    and arr[v['slug']] == slug:
                item = res(i)
                break
        if not item:
            continue
        own = {k: v for k, v in item.items() if isinstance(v, str)}
        title = nfkc(own.get('title', ''))
        allstr = _strings(item, [])
        is_fishing = 'fishing' in allstr or '釣り' in allstr
        text = ' '.join(strip_tags(s) for s in own.values())
        if not is_fishing or not BOAT_WORDS.search(title + ' ' + text):
            continue
        tel = next((v for v in own.values() if re.fullmatch(r'\s*0\d{1,4}-\d{1,4}-\d{3,4}\s*', v)), None)
        addr = next((nfkc(v) for v in own.values() if v.strip().lstrip('〒').strip()[:0] == '' and '長崎県' in v
                     and len(v) < 80 and '<' not in v), None)
        if addr:
            addr = re.sub(r'^〒?\s*\d{3}-\d{4}\s*', '', addr).strip()
        urls = [v.strip() for v in own.values() if v.strip().startswith('http')
                and not re.search(r'storage\.googleapis|google\.com/maps', v)]
        web, sns = split_site(urls)
        lat = lon = None
        for v in own.values():
            m = re.search(r'!2d(1[23]\d\.\d+)!3d(3[0-5]\.\d+)', v)
            if m:
                lon, lat = float(m.group(1)), float(m.group(2))
        body = strip_tags(own.get('body', ''))
        price_txt = [strip_tags(v) for v in own.values() if '円' in v and v != own.get('body')]
        plans = []
        for pt in price_txt:
            for p in price_plans(pt, url):
                if not any(q['name'] == p['name'] and q['price_text'] == p['price_text'] for q in plans):
                    plans.append(p)
        name = re.sub(r'\s+', ' ', title).strip()
        stale = False
        m = re.search(r'※[^※]*休止中.*$', name)
        if m:
            stale = True
            body = '掲載元に「%s」の表記。%s' % (m.group(0).lstrip('※'), body)
            name = name[:m.start()].strip()
        types = []
        if re.search(r'乗合|乗り合い', text):
            types.append('乗合')
        if re.search(r'チャーター|貸切', text) or any(p['kind'] == '仕立' for p in plans):
            types.append('仕立')
        if re.search(r'瀬渡|渡船', text):
            types.append('渡船')
        holidays = next((strip_tags(v) for v in own.values()
                         if re.search(r'定休|不定休|休業日', v) and len(v) < 60 and '<' not in v), '')
        access = next((strip_tags(v) for v in own.values()
                       if re.search(r'車で\d|徒歩\d', v) and len(v) < 100 and '<' not in v), '')
        rec = blank(src_id='tsushima:%s' % slug, src_url=url, name=name, pref='長崎県', city='対馬市',
                    address=addr, tel=tel_display(tel) if tel else None, website=web, sns=sns, lat=lat, lon=lon,
                    types=types, holidays=holidays, access=access, description=short(body), plans=plans)
        if stale:
            rec['stale'] = True
        out.append(rec)
        log('tsushima %d/%d %s' % (n, len(slugs), title))
    return out


# ---------------------------------------------------------------- NAVITIME つり船
NAVI = [('長崎県', 'https://www.navitime.co.jp/category/0101018002/42/'),
        ('佐賀県', 'https://www.navitime.co.jp/category/0101018002/41/')]


def existing_tels():
    tels = set()
    for code in ('41', '42'):
        path = os.path.join(ROOT, 'data', 'detail', code + '.json')
        try:
            d = json.load(open(path, encoding='utf-8'))
        except (IOError, ValueError):
            continue
        for b in d.values():
            t = norm_tel(b.get('tel') or '')
            if t:
                tels.add(t)
    return tels


def navitime_records():
    out = []
    known = existing_tels()
    for pref, base in NAVI:
        url, page = base, 1
        while url:
            h = get_text(url)
            if not h:
                break
            lines = page_lines(h)
            for k, ln in enumerate(lines):
                if ln == '住所' and k + 3 < len(lines) and lines[k + 2] == '電話番号':
                    name, addr, tel = lines[k - 1], nfkc(lines[k + 1]), lines[k + 3]
                    tags = []
                    for x in lines[k + 4:k + 20]:
                        if x.startswith('©'):
                            break
                        if x.startswith('#'):
                            tags.append(x[1:])
                    access = next((x for x in lines[k + 4:k + 8] if x.startswith(('たびら', '駅')) or '徒歩' in x), '')
                    if 'つり船' not in tags and norm_tel(tel) not in known:
                        log('navitime skip (tag %s) %s' % ('/'.join(tags), name))
                        continue
                    if re.search(r'ボートハウス|貸しボート|レンタルボート', name):
                        log('navitime skip (boat rental) %s' % name)
                        continue
                    out.append(blank(src_id='navitime:%s:%s' % (pref[:2], name), src_url=url, name=name, pref=pref,
                                     city=city_of(addr), address=addr, tel=tel_display(tel), access=access))
            nxt = re.search(r'href="([^"]*/category/0101018002/\d+/\?page=%d)"' % (page + 1), h)
            page += 1
            url = urllib.parse.urljoin(base, htmlmod.unescape(nxt.group(1))) if nxt else None
        log('navitime %s done (%d total)' % (pref, len(out)))
    return out


def main():
    only = [a for a in sys.argv[1:] if not a.startswith('--')]
    parts = [('goto', goto_records), ('gotofeat', gotofeat_records), ('ikinavi', iki_records),
             ('tsushima', tsushima_records), ('navitime', navitime_records)]
    recs = []
    for key, fn in parts:
        if only and key not in only:
            continue
        got = fn()
        log('%s: %d records' % (key, len(got)))
        recs.extend(got)
        save_json(OUT + '.partial', recs, indent=1)
    for r in recs:
        if r.get('address') and not r['address'].startswith(r['pref']):
            if not PREF_RE.match(r['address']):
                r['address'] = r['pref'] + r['address']
        if r.get('address') and not r.get('city'):
            r['city'] = city_of(r['address'])
    save_json(OUT, recs, indent=1)
    if os.path.exists(OUT + '.partial'):
        os.remove(OUT + '.partial')
    log('done %d records -> %s' % (len(recs), OUT))


if __name__ == '__main__':
    main()
