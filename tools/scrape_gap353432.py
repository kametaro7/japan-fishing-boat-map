#!/usr/bin/env python3
"""gap353432: 山口県・広島県・島根県の地域一覧から釣り船（遊漁船・渡船・瀬渡し）を取り込む。

一覧:
  hamada-kanko  浜田市観光協会「浜で釣りを楽しもう」渡船・遊漁船の表 https://kankou-hamada.or.jp/content/fishing/
  e-oki         隠岐の島旅（隠岐ジオパーク推進機構）体験予約「船釣り」ほか https://www.e-oki.net/experience/7918/ ほか
  ononavi       尾道観光協会 おのなび「釣り・遊漁船・潮干狩り」 https://www.ononavi.jp/spots/?category[]=45
  bft-hiroshima 釣具店員の釣りブロ「広島の遊漁船一覧リスト」 https://best-fishing-tackle.com/hiroshima-fishing-boat-list/
  sanook        Sanook Fishing「おすすめ釣り船一覧」（島根シロイカ・広島ハマチ・山口クロマグロ）
  turinet       釣船名鑑 turinet.com（島根・広島・山口の掲載船の詳細ページ。過去3年間釣果登録なし＝stale）

作法: キャッシュ work/cache/gap353432/（URLのsha1）、1ホスト直列・1秒間隔、429/503は指数バックオフ。
代表者・船長の個人名は出力しない（ブログカードの船長名、メールアドレス、個人名を含むURLは読み飛ばす）。
使い方:
  python3 tools/scrape_gap353432.py            # 全一覧を解析して work/sources/gap353432.json を出力
  python3 tools/scrape_gap353432.py fetch URL  # 1URLを取得してキャッシュのパスを表示（調査用）
  --refresh で再取得
"""
import hashlib, html, json, os, re, subprocess, sys, time, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'work/cache/gap353432')
OUT = os.path.join(ROOT, 'work/sources/gap353432.json')
LOG = os.path.join(ROOT, 'work/logs/gap353432.log')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')
FETCHED = '2026-09-15'
SRC = 'gap353432'
REFRESH = '--refresh' in sys.argv
os.makedirs(CACHE, exist_ok=True)
os.makedirs(os.path.dirname(LOG), exist_ok=True)
_last = {}


def log(msg):
    line = time.strftime('%H:%M:%S ') + msg
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + '.html')


def fetch(url):
    """URLを取得（キャッシュ優先）。バイト列を返す。"""
    fn = cache_path(url)
    if os.path.exists(fn) and not REFRESH:
        return open(fn, 'rb').read()
    host = re.sub(r'^https?://([^/]+).*', r'\1', url)
    code = ''
    for attempt in range(6):
        wait = 1.0 - (time.time() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()
        r = subprocess.run(['curl', '-sL', '-m', '60', '-A', UA, '-o', fn + '.part',
                            '-w', '%{http_code}', url], capture_output=True, text=True)
        code = r.stdout.strip()
        if code == '200':
            os.replace(fn + '.part', fn)
            return open(fn, 'rb').read()
        if code in ('429', '503'):
            time.sleep(2 ** attempt)
            continue
        break
    if os.path.exists(fn + '.part'):
        os.remove(fn + '.part')
    raise RuntimeError(f'fetch failed {code} {url}')


def page(url):
    return fetch(url).decode('utf-8', 'replace')


ZW = dict.fromkeys(map(ord, '​‎‏﻿'), None)


def clean(s):
    s = html.unescape(re.sub(r'<[^>]+>', '', s)).translate(ZW)
    s = s.replace('　', ' ').replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def text_lines(s):
    s = re.sub(r'(?is)<(script|style|noscript).*?</\1>', '', s)
    s = re.sub(r'(?i)<br\s*/?>', '\n', s)
    t = html.unescape(re.sub(r'<[^>]+>', '\n', s)).translate(ZW)
    return [re.sub(r'[ \t　]+', ' ', x).strip() for x in t.split('\n') if x.strip()]


def z2h(s):
    """NFKC＋数字の間のダッシュ類を「-」に（カタカナの長音「ー」は数字の間だけ置き換える）"""
    s = unicodedata.normalize('NFKC', s or '').translate(ZW)
    return re.sub(r'(?<=\d)[‐‑‒–—―−ー－](?=\d)', '-', s).strip()


def fw2ascii(s):
    """全角英数記号だけ半角に（Ⅱなどはそのまま）"""
    return ''.join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in s)


TEL_RE = re.compile(r'0\d{1,4}-\d{1,4}-\d{3,4}')
PREFS = ('島根県', '広島県', '山口県')
SNS_HOSTS = ('instagram.com', 'facebook.com', 'twitter.com', 'x.com/', 'line.me', 'lin.ee', 'youtube.com')
# 掲載サイト・予約プラットフォーム（船宿の公式サイトではない）
NOT_OFFICIAL = ('select-type.com', 'fishing-v.jp', 'point-i.jp', 'tsuree.jp', 'tsurifune.com',
                'amazon.co.jp', 'amzn.to', 'link.amazon', 'a8.net')
# 終了済みのホームページサービス
DEAD_HOSTS = ('geocities.jp', 'homepage2.nifty.com', 'www1.ocn.ne.jp', 'www3.ocn.ne.jp', 'www4.ocn.ne.jp',
              'h5.dion.ne.jp')
# 個人名を含むURL（出力しない）
PERSONAL_URL = ('makotoyuji.com', '~naoji.m', '~wada4430')


def base(**kw):
    rec = {
        'src': SRC, 'src_id': None, 'src_url': None, 'name': None, 'kana': None,
        'pref': None, 'city': None, 'address': None, 'port': None,
        'lat': None, 'lon': None, 'tel': None, 'website': None, 'sns': [],
        'types': [], 'targets': [], 'methods': [], 'holidays': '', 'facilities': [],
        'capacity': None, 'access': '', 'description': '', 'plans': [],
        'schedule_text': '', 'fetched': FETCHED,
    }
    rec.update(kw)
    rec['description'] = (rec['description'] or '')[:100]
    return rec


def plan(name, kind, price, price_text, url, **kw):
    p = {'name': name, 'kind': kind, 'targets': [], 'price': price, 'price_text': price_text,
         'depart': '', 'return': '', 'meet': '', 'season': '', 'days': '', 'includes': '', 'url': url}
    p.update(kw)
    return p


def classify_link(href, rec_sns):
    """公式サイトなら URL を返す。SNS は rec_sns に追加。掲載サイト等は None。"""
    if not href or not href.startswith('http'):
        return None
    if any(h in href for h in SNS_HOSTS):
        if href not in rec_sns:
            rec_sns.append(href)
        return None
    if any(h in href for h in NOT_OFFICIAL + DEAD_HOSTS + PERSONAL_URL):
        return None
    return href


def types_from(s):
    t = []
    if '乗合' in s or '乗り合' in s:
        t.append('乗合')
    if '仕立' in s or 'チャーター' in s or '貸切' in s:
        t.append('仕立')
    if '渡船' in s or '瀬渡' in s:
        t.append('渡船')
    return t


def split_list(s):
    s = re.sub(r'など$', '', s.strip())
    return list(dict.fromkeys(x.strip() for x in re.split(r'[・、,]', s) if x.strip()))


# ---------------------------------------------------------------- 浜田市観光協会
HAMADA = 'https://kankou-hamada.or.jp/content/fishing/'


def scrape_hamada():
    s = page(HAMADA)
    i, j = s.find('◆渡船'), s.find('◆遊漁船')
    assert 0 < i < j
    recs, bykey = [], {}
    for kind, seg in (('渡船', s[i:j]), ('遊漁船', s[j:s.find('</table>', j)])):
        for tr in re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', seg):
            tds = re.findall(r'(?is)<td[^>]*>(.*?)</td>', tr)
            if len(tds) != 5:
                continue
            area = clean(tds[0])
            link = re.search(r'href="([^"]+)"', tds[1])
            name = clean(tds[1])
            tels = TEL_RE.findall(z2h(clean(tds[2])))
            place_main = clean(re.sub(r'(?is)<span[^>]*>.*?</span>', '', tds[3]))
            place_paren = re.findall(r'(?is)<span[^>]*>（(.*?)）</span>', tds[3])
            last = clean(tds[4])
            key = (name, tels[0] if tels else None)
            rec = bykey.get(key)
            if rec is None:
                sns = []
                website = classify_link(link.group(1) if link else None, sns)
                port, address = None, None
                if re.search(r'(港|漁港)$', place_main) and len(place_main) <= 8:
                    port = place_main
                cand = [place_main] + [clean(p) for p in place_paren]
                for c in cand:
                    c = z2h(c)
                    if c.startswith('浜田市') and re.search(r'\d', c):
                        address = '島根県' + c
                        break
                access = place_main + ''.join('（%s）' % clean(p) for p in place_paren)
                desc = ['地区: ' + area]
                if len(tels) > 1:
                    extra = clean(tds[2])
                    note = '（予約）' if '予約' in extra else ''
                    desc.append('TEL2: ' + ' / '.join(tels[1:]) + note)
                rec = base(src_id=f'hamada-kanko:{name}', src_url=HAMADA, name=name, pref='島根県', city='浜田市',
                           address=address, port=port, tel=(tels[0] if tels else None), website=website, sns=sns,
                           access='乗船場所: ' + access, description='／'.join(desc))
                rec['_desc'] = desc
                bykey[key] = rec
                recs.append(rec)
            if kind == '渡船':
                if '渡船' not in rec['types']:
                    rec['types'].append('渡船')
                rec['_desc'].append('渡船先: ' + last)
            elif last and last != '─':
                rec['_desc'].append('漁法: ' + last)
                if not re.search(r'[（(]', last):
                    items = [re.sub(r'(など|ほか|他)$', '', x.strip()) for x in re.split(r'[、・]', last)]
                    rec['methods'] = list(dict.fromkeys(rec['methods'] + [
                        x for x in items if re.search(r'釣|ジギング|キャスティング|ティップラン|タイラバ|落とし込み|イカメタル', x)]))
    for r in recs:
        r['description'] = '／'.join(r.pop('_desc'))[:100]
    log(f'hamada total {len(recs)}')
    return recs


# ---------------------------------------------------------------- 隠岐の島旅 e-oki.net
EOKI = 'https://www.e-oki.net/experience/'


def scrape_eoki():
    recs = []
    # 7918 船釣り（隠岐の島町）: おき得乗船券の対象船一覧（エリア別・船名・電話）
    url = EOKI + '7918/'
    lines = text_lines(page(url))
    area, n = None, 0
    extra_8068 = None
    for ln in lines:
        m = re.fullmatch(r'-(.+?エリア)-', ln)
        if m:
            area = m.group(1)
            continue
        m = re.fullmatch(r'(\S+?)\s+(0\d{1,4}-\d{1,4}-\d{3,4})', ln)
        if m and area:
            n += 1
            recs.append(base(src_id=f'e-oki:7918:{m.group(1)}', src_url=url, name=m.group(1), pref='島根県',
                             city='隠岐郡隠岐の島町', tel=m.group(2),
                             description=f'地区: {area}（隠岐の島町）／おき得乗船券の対象船。料金は狙う魚により相談'))
        if ln == '基本情報':
            break
    assert n == 8, n
    # 8068 芯盛丸の体験ページ（公式サイト・料金）
    url2 = EOKI + '8068/'
    p = page(url2)
    assert '芯盛丸' in p and '080-2099-1127' in p and '10,000円/人' in p
    for r in recs:
        if r['name'] == '芯盛丸':
            r['website'] = 'https://sites.google.com/view/issinsuisan-shinseimaru/'
            r['description'] = ('地区: 加茂エリア（隠岐の島町）／船釣り体験（加茂湾沖）・カゴ上げ体験・白イカ釣り体験（夜間）')
            r['access'] = '集合場所: 加茂周辺（詳細は電話で）'
            r['plans'] = [
                plan('舟釣り（1日）', '', None, '1日 10,000円～', url2),
                plan('舟釣り（半日）', '', None, '半日 6,000円～', url2),
                plan('白イカ釣り', '乗合', 10000, '10,000円/人', url2, depart='18:30', **{'return': '22:00'}),
            ]
    # 6348 船釣り（遊漁船）海士町: ㈱3set
    url3 = EOKI + '6348/'
    p = page(url3)
    assert '3set' in p and '30,000円（3名）' in p and '菱浦港' in p
    recs.append(base(
        src_id='e-oki:6348', src_url=url3, name='3set', pref='島根県', city='隠岐郡海士町', port='菱浦港',
        facilities=['トイレ', 'クーラーボックス'],
        access='乗船場所: 海士町菱浦港岸壁／支払い: 菱浦港キンニャモニャセンター1階',
        description='海士町の遊漁船（㈱3set）。マダイ・イシダイ・ヒラマサ・イサキ等。6歳以上、1～6名。予約は予約コントロールセンター 050-3172-1521',
        targets=['マダイ', 'イシダイ', 'ヒラマサ', 'イサキ'],
        plans=[plan('船釣り体験（3時間）', '', None, '30,000円（3名）～（漁具レンタル含む）', url3),
               plan('遊漁船（5～12時間）', '', None, '45,000円（3名5時間）～', url3),
               plan('漁具レンタル（1名分）', '', None, '2,500円', url3)],
    ))
    # 8057 船チャーター 釣り・フリープランクルーズ（ジオリゾートシンフォニー）
    url4 = EOKI + '8057/'
    p = page(url4)
    assert '08512-7-4009' in p and '卯敷' in p
    m = re.search(r'query=([\d.]+)%2C([\d.]+)', p)
    recs.append(base(
        src_id='e-oki:8057', src_url=url4, name='ジオリゾートシンフォニー', pref='島根県', city='隠岐郡隠岐の島町',
        address='島根県隠岐郡隠岐の島町卯敷1004', tel='08512-7-4009', types=['仕立'],
        sns=['https://www.instagram.com/georesortsymphony/?hl=ja'],
        lat=float(m.group(1)) if m else None, lon=float(m.group(2)) if m else None,
        description='船チャーター（釣り・フリープランクルーズ）。五目・大物・ジギング等、ガイド付き可。遊漁は要問合せ。コテージ宿泊可',
    ))
    log(f'e-oki total {len(recs)}')
    return recs


# ---------------------------------------------------------------- おのなび（尾道観光協会）
ONONAVI = 'https://www.ononavi.jp/spots/detail.html?detail_id='


def scrape_ononavi():
    recs = []
    url = ONONAVI + '860'
    p = page(url)
    assert '和丸観光' in p and '090-1682-0179' in p and '最終更新日：2020-04-23' in p
    recs.append(base(
        src_id='ononavi:860', src_url=url, name='和丸観光', kana='かずまるかんこう', pref='広島県', city='尾道市',
        address='広島県尾道市正徳町25-1（吉和活魚センター内）', port='吉和漁港', tel='090-1682-0179',
        types=['乗合', '仕立'], holidays='不定休',
        targets=['マダイ', 'アコウ', 'メバル', 'タチウオ', 'キス', 'アジ', 'フグ'],
        access='船乗り場: 吉和漁港（JR尾道駅から車で約10分）／駐車場無料（普通車7台、マイクロバス2台）',
        description='尾道・しまなみ海道周辺の遊漁船。掲載ページの最終更新日 2020-04-23',
        plans=[plan('乗り合船', '乗合', 12000, '12,000円／1名', url, season='通年'),
               plan('遊漁船（貸切）', '仕立', None, '1隻 80,000円（10名まで。以降10,000円／1名）、お土産付きは＋20,000円／1隻', url,
                    season='5月～10月ごろ')],
        stale=True,
    ))
    url = ONONAVI + '810'
    p = page(url)
    assert '遊漁船 海正丸' in p and '080-2897-3528' in p and 'onomichi-kaiseimaru.com' in p
    recs.append(base(
        src_id='ononavi:810', src_url=url, name='海正丸', kana='かいせいまる', pref='広島県', city='尾道市',
        address='広島県尾道市向島町11953 島居マリン', tel='080-2897-3528', website='http://onomichi-kaiseimaru.com',
        types=['乗合', '仕立'], holidays='不定休', facilities=['キャビン', 'トイレ'],
        access='島居マリン（尾道駅から車で25分）／新浜フェリー乗り場（広島県尾道市新浜2-13 新浜港）からの出発も可',
        description='しまなみ海道を中心にした遊漁船（乗り合い・チャーター）。定置網観光・海水浴送迎・遊覧も相談可',
    ))
    log(f'ononavi total {len(recs)}')
    return recs


# ---------------------------------------------------------------- 釣具店員の釣りブロ（広島）
BFT = 'https://best-fishing-tackle.com/hiroshima-fishing-boat-list/'


def scrape_bft():
    s = page(BFT)
    i = s.find('広島県の遊漁船/福山')
    j = s.find('<h2', s.find('広島県の遊漁船/広島市内周辺エリアをまとめました'))
    body = s[s.rfind('<h2', 0, i):]
    recs, n = [], 0
    section = ''
    for m in re.finditer(r'(?is)<h2[^>]*>(.*?)</h2>|<h3[^>]*>(.*?)</h3>(.*?)(?=<h[23][ >])', body):
        if m.group(1) is not None:
            section = clean(m.group(1))
            if 'まとめ' in section:
                break
            continue
        title, chunk = clean(m.group(2)), m.group(3)
        tbl = re.search(r'(?is)<table[^>]*>(.*?)</table>', chunk)
        if not tbl or '釣りもの' not in tbl.group(1):
            continue
        n += 1
        head = [clean(x) for x in re.findall(r'(?is)<thead>.*?<th[^>]*>(.*?)</th>', tbl.group(1))[:1]]
        rows = dict((clean(a), clean(b)) for a, b in re.findall(r'(?is)<tr><th[^>]*>(.*?)</th><td[^>]*>(.*?)</td></tr>', tbl.group(1)))
        para = re.search(r'(?is)<p class="wp-block-paragraph">(.*?)</p>', chunk)
        ptxt = clean(para.group(1)) if para else ''
        card = re.search(r'(?is)<a class="p-blogCard__title" href="([^"]+)"[^>]*>(.*?)</a>', chunk)
        excerpt = re.search(r'(?is)<span class="p-blogCard__excerpt">(.*?)</span>', chunk)
        name = re.sub(r'^(遊漁船|釣り船)\s+', '', title)
        kana = None
        if head:
            mk = re.search(r'[（(]([ぁ-んー]+)[）)]$', head[0]) or re.fullmatch(r'([ぁ-んー]+)', head[0])
            if mk:
                kana = mk.group(1)
        sns = []
        website = None
        pre_table = chunk[:tbl.start()]
        hrefs = ([card.group(1)] if card else []) + re.findall(r'href="([^"]+)"', pre_table)
        hrefs += re.findall(r'(?is)<(?:p|div)[^>]*>\s*(https?://[^<\s]+)\s*</(?:p|div)>', pre_table)
        for h in hrefs:
            w = classify_link(h, sns)
            if w and 'best-fishing-tackle.com' not in w and website is None:
                website = w
        cardtext = z2h(clean(excerpt.group(1)) if excerpt else '') + ' ' + z2h(clean(card.group(2)) if card else '')
        tels = TEL_RE.findall(cardtext)
        address = None
        ma = re.search(r'([一-龥]{1,4}市[一-龥ぁ-んァ-ン]+\d[\d\-]*)', z2h(clean(excerpt.group(1))) if excerpt else '')
        if ma:
            address = '広島県' + ma.group(1)
        area = ''
        mm = re.search(r'広島県(.+?)(?:エリア|地区)', ptxt)
        if mm:
            area = mm.group(1)
        desc = []
        if area:
            desc.append('エリア: ' + area)
        else:
            desc.append('区分: ' + section.replace('広島県の遊漁船/', ''))
        if rows.get('釣りもの'):
            desc.append('釣りもの: ' + rows['釣りもの'])
        if card and 'select-type.com' in card.group(1):
            desc.append('予約はSelectType')
        plans = []
        if rows.get('料金') and rows['料金'] != '不明':
            plans.append(plan('料金の目安（掲載元）', '', None, rows['料金'], BFT))
        recs.append(base(
            src_id=f'bft-hiroshima:{n}:{name}', src_url=BFT, name=name, kana=kana, pref='広島県',
            address=address, tel=(tels[0] if tels else None), website=website, sns=sns,
            description='／'.join(desc), plans=plans,
        ))
    log(f'bft total {len(recs)}')
    return recs


# ---------------------------------------------------------------- Sanook Fishing
SANOOK = [
    ('sanook-shimane', 'https://sanook-fishing.com/boat-shimane-shiroika/', '島根県'),
    ('sanook-hiroshima', 'https://sanook-fishing.com/boat-hiroshima-warasa/', '広島県'),
    ('sanook-yamaguchi', 'https://sanook-fishing.com/boat-yamaguchi-kuromaguro/', '山口県'),
]
PORT_END = re.compile(r'(港|漁港|マリーナ|ボートパーク.*|桟橋|岸壁|波止|サービス|マリンプラザしまね)$')


def split_name(raw):
    raw = fw2ascii(raw.translate(ZW)).strip()
    m = re.fullmatch(r'(.*?)[（(]([ぁ-んー]+)[）)]', raw)
    if m:
        return m.group(1).strip(), m.group(2)
    m = re.fullmatch(r'(.*?)[（(][ぁ-んー]+[）)](丸)', raw)
    if m:
        return m.group(1) + m.group(2), None
    m = re.fullmatch(r'(.*?)[（(][A-Za-z\- ]+[）)]', raw)
    if m:
        return m.group(1).strip(), None
    m = re.fullmatch(r'([A-Za-z0-9 ]+)[（(].+[）)]', raw)  # TABIBITO（イカ釣りイカへん？）
    if m:
        return m.group(1).strip(), None
    return raw, None


def scrape_sanook():
    recs = []
    for key, url, pref in SANOOK:
        s = page(url)
        i = s.find('<section class="content')
        # 「関連トピックス」は目次にも出るので、見出し（h2）の位置で切る
        mj = re.search(r'<h2[^>]*>\s*(?:<[^>]+>\s*)*関連トピックス', s[i:])
        j = i + mj.start() if mj else len(s)
        body = s[i:j]
        area, n = '', 0
        for m in re.finditer(r'(?is)<h2[^>]*>(.*?)</h2>|<table[^>]*>(.*?)</table>', body):
            if m.group(1) is not None:
                area = clean(m.group(1))
                continue
            rows = re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', m.group(2))
            if not rows:
                continue
            head = re.findall(r'(?is)<td[^>]*>(.*?)</td>', rows[0])
            if len(head) < 2:
                continue
            f = {}
            for r in rows[1:]:
                cells = re.findall(r'(?is)<td[^>]*>(.*?)</td>', r)
                for k in range(0, len(cells) - 1, 2):
                    f[clean(cells[k]).replace(' ', '')] = cells[k + 1]
            if '住所' not in f:
                continue
            n += 1
            raw_name = clean(head[0])
            name, kana = split_name(raw_name)
            link = re.search(r'href="([^"]+)"', head[0])
            sns = []
            website = classify_link(link.group(1) if link else None, sns)
            tels = TEL_RE.findall(z2h(clean(head[1])))
            ad = z2h(clean(f['住所']))
            parts = ad.split(' ', 1)
            address, rest = parts[0], (parts[1].strip() if len(parts) > 1 else '')
            access = []
            mp = re.match(r'^(.*?)[(（](.*?)[)）]$', address)
            if mp:
                address = mp.group(1)
                access.append(mp.group(2))
            if not address.startswith(PREFS) and re.match(r'^[^\d]{1,4}[市郡]', address):
                address = pref + address
            port = None
            if rest:
                tok = rest.split(' ', 1)
                if PORT_END.search(tok[0]):
                    port = tok[0]
                    if len(tok) > 1:
                        access.append(tok[1])
                else:
                    access.append(rest)
            biko = clean(f.get('備考', ''))
            holidays = ''
            first = biko.split('・', 1)[0]
            if re.search(r'休|営業', first):
                holidays = first
            yoyaku = clean(f.get('予約方法', '')).replace('📩', '').replace('📶', '').replace('💻', '')
            desc = ['地区: ' + area] if area else []
            if raw_name != name:
                desc.append('掲載名: ' + raw_name)
            if yoyaku:
                desc.append('予約: ' + yoyaku)
            desc.append(biko)
            recs.append(base(
                src_id=f'{key}:{n}:{name}', src_url=url, name=name, kana=kana, pref=pref,
                address=address if address.startswith(PREFS) else None, port=port,
                tel=(tels[0] if tels else None), website=website, sns=sns,
                types=types_from(clean(f.get('種別', ''))),
                methods=split_list(clean(f.get('釣り方', ''))),
                targets=split_list(clean(f.get('対象魚', ''))),
                holidays=holidays, access='／'.join(a for a in access if a),
                description='／'.join(d for d in desc if d),
            ))
        log(f'{key} total {n}')
    return recs


# ---------------------------------------------------------------- 釣船名鑑 turinet.com
TURINET = 'http://www.turinet.com/'
TURINET_LIST = TURINET + 'index.php?area_src=330'  # 表示は全国一覧（1274件）
AREA_PREF = {'島根': '島根県', '広島': '広島県', '（山口）日本海': '山口県', '（山口）瀬戸内海': '山口県'}


def scrape_turinet():
    s = page(TURINET_LIST)
    s = re.sub(r'(?is)<(script|style|select).*?</\1>', '', s)
    rows = []
    for tr in re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', s):
        tds = re.findall(r'(?is)<td[^>]*>(.*?)</td>', tr)
        if len(tds) < 3:
            continue
        area = clean(tds[0])
        if area not in AREA_PREF:
            continue
        href = re.search(r'href="([^"]+\.php)"', tds[2])
        rows.append((area, clean(tds[1]).rstrip('、'), href.group(1)))
    log(f'turinet rows {len(rows)}')
    recs = []
    for k, (area, port, href) in enumerate(rows, 1):
        url = TURINET + href
        p = page(url)
        lines = text_lines(p)
        t = '\n'.join(lines)
        mname = re.search(r'\[([^\]\n]+)\]\n\[連絡先\]', t)
        name = mname.group(1).strip() if mname else None
        tel = re.search(r'TEL:([0-9\-]*)', t)
        mob = re.search(r'携帯:([0-9\-]*)', t)
        tel = tel.group(1) if tel and tel.group(1) else None
        mob = mob.group(1) if mob and mob.group(1) else None
        gyo = re.search(r'\n業種\n([^\n]*)', t)
        nav = re.search(r'\nカーナビ\n([^\n]*)', t)
        hp = re.search(r'<a href="(https?://[^"]+)"[^>]*>\s*釣船及び釣果の詳細情報', p)
        stale = '過去３年間釣果登録がありません' in p
        # 料金表: itemBox の div が 4 つずつ（釣り物等/時期/料金等/備考）
        seg = p[p.find('<dt>釣り物等</dt>'):p.find('概要', p.find('<dt>釣り物等</dt>'))]
        cells = [clean(x) for x in re.findall(r'(?is)<div class="itemBox[^"]*">(.*?)</div>', seg)]
        plans = []
        for q in range(0, len(cells) - 3, 4):
            item, season, fee, note = cells[q:q + 4]
            if not item:
                continue
            note = note.replace('詳細はお問合せ下さい', '').strip()
            price = None
            mp = re.fullmatch(r'(?:乗合)?\s*(\d{1,3}(?:,\d{3})+|\d+)円(?:/人)?', fee)
            if mp:
                price = int(mp.group(1).replace(',', ''))
            kind = '仕立' if re.search(r'仕立|貸切|チャーター', item) else ('乗合' if '乗合' in item else '')
            dep = ret = ''
            mt = re.findall(r'(\d{1,2}):(\d\d)〜(\d{1,2}):(\d\d)', note)
            if len(mt) == 1:
                dep, ret = '%02d:%s' % (int(mt[0][0]), mt[0][1]), '%02d:%s' % (int(mt[0][2]), mt[0][3])
            pt = fee + (' ／ ' + note if note else '')
            plans.append(plan(item, kind, price, pt, url, season=('' if season == '-' else season),
                              depart=dep, **{'return': ret}))
        address, access = None, ''
        if nav:
            navt = z2h(html.unescape(nav.group(1)))
            if navt.startswith(PREFS) or navt.startswith('広島市'):
                parts = navt.split(' ', 1)
                address = parts[0] if parts[0].startswith(PREFS) else '広島県' + parts[0]
                if address.startswith('山口県柳井神代'):
                    address = address.replace('山口県柳井', '山口県柳井市')
                if len(parts) > 1 and parts[1].strip():
                    access = 'カーナビ: ' + navt
        pref = AREA_PREF[area]
        if address and address[:3] != pref:
            # 掲載区分と別の県の住所（みのり: 山口瀬戸内海の区分で広島市の住所）は address に入れない
            access = 'カーナビ: ' + z2h(html.unescape(nav.group(1)))
            address = None
        website, sns = None, []
        desc = ['区分: ' + area]
        if hp:
            website = classify_link(hp.group(1), sns)
            if not website and any(h in hp.group(1) for h in DEAD_HOSTS):
                desc.append('掲載のHPはサービス終了')
        if mob and mob != tel:
            desc.append('携帯: ' + mob)
        if stale:
            desc.append('掲載元で過去3年間釣果登録なし')
        disp = name
        if name == 'ZEEL?':  # 掲載ページで「Ⅱ」が文字化け
            disp = 'ZEEL2'
            desc.append('掲載名は文字化け（ZEEL?）')
        rec = base(src_id=f'turinet:{href[:-4]}', src_url=url, name=disp, pref=pref, address=address,
                   port=port or None, tel=tel or mob, website=website, sns=sns,
                   types=types_from(gyo.group(1) if gyo else ''), access=access,
                   description='／'.join(desc), plans=plans)
        if stale:
            rec['stale'] = True
        recs.append(rec)
        if k % 10 == 0:
            log(f'turinet done {k}/{len(rows)}')
    log(f'turinet total {len(recs)}')
    return recs


def main():
    recs = []
    for fn in (scrape_hamada, scrape_eoki, scrape_ononavi, scrape_bft, scrape_sanook, scrape_turinet):
        recs += fn()
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(recs, f, ensure_ascii=False, indent=1)
    ids = [r['src_id'] for r in recs]
    assert len(ids) == len(set(ids)), 'src_id duplicated'
    log(f'total {len(recs)} -> {OUT}')


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'fetch':
        for u in sys.argv[2:]:
            if u.startswith('--'):
                continue
            try:
                fetch(u)
                print('OK', cache_path(u), u)
            except Exception as e:
                print('ERR', e)
    else:
        main()
