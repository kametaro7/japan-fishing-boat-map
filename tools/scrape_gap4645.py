#!/usr/bin/env python3
"""gap4645: 鹿児島県・宮崎県の地域一覧から釣り船（遊漁船・瀬渡し）を取り込む。

一覧:
  beppu      別府釣具 BEPPU FISHING「釣り船情報」 https://www.beppu-fishing.co.jp/sys/regions/lists（地区一覧→船詳細、複数ページ）
  wiredfish  釣り情報サイト wiredFish「釣り船・瀬渡し船」 https://books-nekoya.jp/Fishing/boat/boat.html（1ページ）
  koshiki    甑島釣り船・瀬渡し船情報 http://www.koshikijima.net/fishing/fishing-boat.html（1ページ、Shift_JIS、2021-07更新＝stale）
  ace        フィッシングショップ・エース「釣り船、渡船情報」 https://fishingace.base.shop/p/00007（1ページ）
  nichinan   日南市観光協会「観光・体験」の遊漁船 https://www.kankou-nichinan.jp/tourisms/8784/ ほか

作法: キャッシュ work/cache/gap4645/（URLのsha1）、1ホスト直列・1秒間隔、429/503は指数バックオフ。
代表者・船長の個人名は出力しない（BEPPU の「船長」行・紹介文、甑島一覧の「船長」列、電話番号横の氏名は読み飛ばす）。
使い方: python3 tools/scrape_gap4645.py [--refresh]
"""
import hashlib, html, json, os, re, subprocess, sys, time, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, 'work/cache/gap4645')
OUT = os.path.join(ROOT, 'work/sources/gap4645.json')
LOG = os.path.join(ROOT, 'work/logs/gap4645.log')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')
FETCHED = '2026-09-15'
SRC = 'gap4645'
REFRESH = '--refresh' in sys.argv
os.makedirs(CACHE, exist_ok=True)
os.makedirs(os.path.dirname(LOG), exist_ok=True)
_last = {}


def log(msg):
    line = time.strftime('%H:%M:%S ') + msg
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def fetch(url):
    """URLを取得（キャッシュ優先）。バイト列を返す。"""
    fn = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + '.html')
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


def clean(s):
    s = html.unescape(re.sub(r'<[^>]+>', '', s))
    s = s.replace('　', ' ').replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def z2h(s):
    """全角数字・ハイフンを半角に（電話番号用）"""
    s = unicodedata.normalize('NFKC', s)
    return s.replace('－', '-').replace('ー', '-').replace('−', '-').strip()


def kata2hira(s):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s)


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
    return rec


def plan(name, kind, price, price_text, url):
    return {'name': name, 'kind': kind, 'targets': [], 'price': price, 'price_text': price_text,
            'depart': '', 'return': '', 'meet': '', 'season': '', 'days': '', 'includes': '', 'url': url}


# ---------------------------------------------------------------- beppu
BEPPU = 'https://www.beppu-fishing.co.jp'
BEPPU_MIYAZAKI = {'南郷', '日南', '都井岬'}
TEL_RE = re.compile(r'0\d{1,4}-\d{1,4}-\d{3,4}')
DEAD_HOSTS = ('blogs.yahoo.co.jp',)  # Yahoo!ブログは2019年終了


def beppu_targets(v):
    m = re.search(r'釣りもの[:：](.*)$', v)
    if not m:
        return []
    return [t.strip() for t in re.split(r'[、,，]', m.group(1)) if t.strip()]


def scrape_beppu():
    idx = fetch(BEPPU + '/sys/regions/lists').decode('utf-8', 'replace')
    regions = []
    for href, label in re.findall(r'href="(/sys/boats/lists/\d+)"[^>]*>\s*([^<]+?)\s*<', idx):
        if (href, label) not in regions:
            regions.append((href, label))
    log(f'beppu regions {len(regions)}')
    recs, seen_url, seen_key = [], set(), {}
    done = 0
    for href, label in regions:
        lst = fetch(BEPPU + href).decode('utf-8', 'replace')
        views = []
        for v in re.findall(r'href="(/sys/boats/view/\d+)"', lst):
            if v not in views:
                views.append(v)
        for v in views:
            url = BEPPU + v
            if url in seen_url:
                continue
            seen_url.add(url)
            p = fetch(url).decode('utf-8', 'replace')
            main = re.search(r'<main role="main">(.*?)</main>', p, re.S).group(1)
            raw_name = clean(re.search(r'<h3>(.*?)</h3>', main, re.S).group(1))
            rows = {}
            for k, val in re.findall(r'<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>', main, re.S):
                rows.setdefault(clean(k), clean(val))
            pc = re.search(r'<ol class="topicPath">(.*?)</ol>', p, re.S)
            area = ''
            if pc:
                items = [clean(x) for x in re.findall(r'<li>(.*?)</li>', pc.group(1), re.S)]
                if len(items) >= 3:
                    area = items[2]
            # 名前: 「名前 (かな)」「名前（かな）」→ kana へ。キャッチコピー・敬称は外す
            name, kana, port = raw_name, None, None
            m = re.match(r'^(.*?)\s*[（(]\s*([ぁ-んー０-９0-9]+)\s*[）)]$', name)
            if m:
                name, kana = m.group(1).strip(), unicodedata.normalize('NFKC', m.group(2))
            m = re.match(r'^(.*?)（([^）]*港)）$', name)
            if m:
                name, port = m.group(1).strip(), m.group(2)
            notes = []
            if name != raw_name and not kana and not port:
                notes.append('掲載名: ' + raw_name)
            if name.startswith('夢と心を結ぶ '):
                notes.append('掲載名: ' + raw_name)
                name = name.replace('夢と心を結ぶ ', '')
            name = re.sub(r'さん$', '', name)
            # 出航場所: 港名ならport、説明文ならaccess
            access = []
            dep = rows.get('出航場所', '')
            if dep:
                if len(dep) <= 12 and not re.search(r'[、。]|前|近く|より', dep):
                    port = port or dep
                else:
                    access.append('出航場所: ' + dep)
            # 住所: 市郡名から始まるものだけ address（県名を補う）
            address = None
            ad = unicodedata.normalize('NFKC', rows.get('住所', '')).strip()
            pref = '宮崎県' if label in BEPPU_MIYAZAKI else '鹿児島県'
            if ad:
                if ad.startswith(('鹿児島県', '宮崎県')):
                    address = ad
                elif re.match(r'^[^\s、。]{1,6}[市郡]', ad):
                    address = pref + ad
                else:
                    access.append(ad)
            # 電話: 番号だけ取り出す（横の氏名は捨てる）
            tels = TEL_RE.findall(z2h(rows.get('電話番号', '')))
            desc = []
            if area:
                desc.append('地区: ' + area + (('（' + rows['地区'] + '）') if rows.get('地区') else ''))
            if len(tels) > 1:
                desc.append('TEL2: ' + ' / '.join(tels[1:]))
            types, targets, methods, plans = [], [], [], []
            other = rows.get('その他', '')
            if rows.get('瀬渡し船') or '瀬渡し' in other or '瀬渡し' in raw_name:
                types.append('渡船')
            for k in ('瀬渡し船', '船釣り'):
                targets += beppu_targets(rows.get(k, ''))
            targets += beppu_targets(other)
            fr = rows.get('船釣り', '')
            if fr and not re.search(r'釣りもの|円', fr):
                methods += [t for t in re.split(r'[・、]', fr) if t]
            if other.startswith('ルアー'):
                methods.append('ルアー')
            if other and not re.search(r'釣りもの', other) and other not in ('瀬渡し船',):
                desc.append(other)
            # 料金: 表の「○○ ○円」行と船釣り欄の「半日○円」だけ
            for k, val in rows.items():
                mm = re.fullmatch(r'(\d[\d,]*)円(?:\((\d+)名迄\))?', val)
                if mm and k not in ('ー釣',) and '貸し竿' not in k:
                    price = int(mm.group(1).replace(',', ''))
                    if 'チャーター' in k:
                        plans.append(plan(k, '仕立', None, f'1隻 {val}', url))
                    else:
                        plans.append(plan(k, '乗合', price, f'{val}/人', url))
            for nm, pr, child in re.findall(r'(流し釣り|係留釣り)\s*半日(\d+)円\s*（(\d+)）', fr):
                plans.append(plan(f'{nm}（半日）', '乗合', int(pr), f'半日{pr}円（女性・子供{child}円）', url))
            mm = re.search(r'チャーター\s*半日\s*(\d+)円\s*/終日(\d+)円', fr)
            if mm:
                plans.append(plan('チャーター', '仕立', None, f'1隻 半日{mm.group(1)}円／終日{mm.group(2)}円', url))
            cap = None
            mm = re.fullmatch(r'(?:定員)?(\d+)名(?:\s*（.*）)?', unicodedata.normalize('NFKC', rows.get('定員', '')).replace('定員 ', '定員'))
            if mm:
                cap = int(mm.group(1))
            website = rows.get('サイトURL') or None
            if website and any(h in website for h in DEAD_HOSTS):
                desc.append('掲載のブログURLはサービス終了')
                website = None
            key = (name, tels[0] if tels else None)
            if key in seen_key:
                seen_key[key]['description'] = (seen_key[key]['description'] + '／地区: ' + area)[:100]
                continue
            rec = base(
                src_id='beppu:' + v.rsplit('/', 1)[1], src_url=url, name=name, kana=kana, pref=pref,
                address=address, port=port, tel=(tels[0] if tels else None), website=website,
                types=types, targets=list(dict.fromkeys(targets)), methods=list(dict.fromkeys(methods)),
                capacity=cap, access=' ／ '.join(access), description='／'.join(notes + desc)[:100], plans=plans,
            )
            seen_key[key] = rec
            recs.append(rec)
            done += 1
            if done % 10 == 0:
                log(f'beppu done {done}')
    log(f'beppu total {len(recs)}')
    return recs


# ---------------------------------------------------------------- wiredFish
WIRED = 'https://books-nekoya.jp/Fishing/boat/boat.html'


def scrape_wiredfish():
    s = fetch(WIRED).decode('utf-8', 'replace')
    s = re.sub(r'(?s)<!--.*?-->', '', s)
    token = re.compile(
        r'<h2 id="[^"]*">(?P<h2>.*?)</h2>|<h3 id="[^"]*">(?P<h3>.*?)</h3>|'
        r'<div style="text-align:center; color:#b80117;">(?P<name>.*?)</div>'
        r'<div style="text-align:left;"><p>(?P<body>.*?)</p></div>', re.S)
    pref = city = None
    recs, n = [], 0
    for m in token.finditer(s):
        if m.group('h2') is not None:
            h2 = clean(m.group('h2'))
            pref = '鹿児島県' if '鹿児島' in h2 else ('宮崎県' if '宮崎' in h2 else None)
            continue
        if m.group('h3') is not None:
            city = clean(m.group('h3'))
            continue
        if pref is None:
            continue
        n += 1
        raw_name = clean(m.group('name'))
        name, kana = raw_name, None
        mk = re.match(r'^(.*?)（([ぁ-んー]+)）$', raw_name)
        if mk:
            name, kana = mk.group(1).strip(), mk.group(2)
        body = m.group('body')
        fields = {}
        website = None
        tels = re.findall(r'href="tel:([^"]*)"', body)
        for a_href, a_text in re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', body, re.S):
            if a_href.startswith('tel:'):
                continue
            if 'books-nekoya.jp' in a_href or not a_href.startswith('http'):
                continue  # wiredFish 内の記事リンク
            website = website or a_href
        text = re.sub(r'<br\s*/?>', '\n', body)
        text = html.unescape(re.sub(r'<[^>]+>', '', text))
        key = None
        for line in text.split('\n'):
            line = line.replace('　', ' ').strip()
            mm = re.match(r'^(Port|Type|Area[^：:]*|TEL|Link)\s*[：:]\s*(.*)$', line)
            if mm:
                key = mm.group(1).strip()
                fields.setdefault(key, [])
                if mm.group(2).strip():
                    fields[key].append(mm.group(2).strip())
            elif key and line:
                fields[key].append(line)
        port = ' '.join(fields.get('Port', [])).strip() or None
        typ = ' '.join(fields.get('Type', []))
        types = []
        if '瀬渡し' in typ:
            types.append('渡船')
        if '貸切' in typ:
            types.append('仕立')
        area_parts = []
        for k, v in fields.items():
            if k.startswith('Area') and v:
                area_parts.append(''.join(v))
        desc = []
        if typ:
            desc.append('種別: ' + typ)
        tel_list = [t.strip() for t in tels if t.strip()]
        if len(tel_list) > 1:
            desc.append('TEL2: ' + ' / '.join(tel_list[1:]))
        if area_parts:
            desc.append('エリア: ' + ' '.join(area_parts))
        recs.append(base(
            src_id=f'wiredfish:{n}:{name}', src_url=WIRED, name=name, kana=kana, pref=pref, city=city,
            port=port, tel=(tel_list[0] if tel_list else None), website=website, types=types,
            description='／'.join(desc)[:100],
        ))
    log(f'wiredfish total {len(recs)}')
    return recs


# ---------------------------------------------------------------- koshikijima.net
KOSHIKI = 'http://www.koshikijima.net/fishing/fishing-boat.html'


def scrape_koshiki():
    s = fetch(KOSHIKI).decode('cp932', 'replace')
    i = s.find('甑　島　瀬　渡　し　船　一　覧　表')
    if i < 0:
        i = s.find('瀬渡し船')
    s = s[i:]
    recs, section, n = [], '', 0
    for tr in re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', s):
        cells = [clean(c) for c in re.findall(r'(?is)<td[^>]*>(.*?)</td>', tr)]
        if not cells:
            continue
        if len(cells) == 1:
            section = cells[0].strip('◆…・ ')
            if '釣具店' in section:
                break
            continue
        if len(cells) < 5 or ('船' in cells[0] and '名' in cells[0] and '定' in cells[1]):
            continue
        name = re.sub(r'^[・\s]+', '', cells[0])
        name = re.sub(r'\s*EIFUKUMARU$', '', name).strip()
        if re.fullmatch(r'[一-鿿]\s+丸', name):
            name = name.replace(' ', '')
        cap = None
        m = re.search(r'(\d+)', unicodedata.normalize('NFKC', cells[1]))
        if m:
            cap = int(m.group(1))
        port = cells[3] or None
        tel = z2h(cells[4]) or None
        note = cells[5] if len(cells) > 5 else ''
        note = '' if re.fullmatch(r'[・…\s]*', note) else note
        n += 1
        desc = [section] if section else []
        if note:
            desc.append('備考: ' + note)
        recs.append(base(
            src_id=f'koshikijima-net:{n}:{name}', src_url=KOSHIKI, name=name, pref='鹿児島県',
            port=port, tel=tel, types=['渡船'], capacity=cap,
            description='／'.join(desc)[:100], stale=True,
        ))
    log(f'koshiki total {len(recs)}')
    return recs


# ---------------------------------------------------------------- Fishing Shop Ace（日向市）
ACE = 'https://fishingace.base.shop/p/00007'
ACE_CITY = {'門川': '門川町', '土々呂': '延岡市', '南浦・島野浦': '延岡市', '北浦': '延岡市',
            '県北（日向・美々津）': '日向市'}
# 個人名を含むSNSアカウントは出力しない
ACE_SNS_SKIP = ('tatsushikonami', 'kifukumaru_hideki')


def scrape_ace():
    s = fetch(ACE).decode('utf-8', 'replace')
    s = re.sub(r'(?is)<(script|style).*?</\1>', '', s)
    s = re.sub(r'<br\s*/?>', '\n', s)
    s = re.sub(r'</(p|div|li|h\d)>', '\n', s)
    text = html.unescape(re.sub(r'<[^>]+>', ' ', s)).replace('　', ' ')
    i = text.find('瀬渡し船 ')
    j = text.find('連絡先など')
    body = text[i:j] if i >= 0 and j > i else text
    recs, section, n = [], '', 0
    for line in body.split('\n'):
        line = re.sub(r'[ \t]+', ' ', line).strip()
        if not line or line == '瀬渡し船':
            continue
        if not line.startswith('・'):
            section = line
            continue
        m = re.match(r'^・(?P<name>[^（(]+)[（(](?P<kana>[^）)]+)[）)]\s*(?P<rest>.*)$', line)
        if not m:
            continue
        rest = m.group('rest')
        urls = re.findall(r'https?://\S+', rest)
        tm = re.search(r'(?:TEL|☎)\s*[：:]\s*([0-9\-]+)\s*(.*)$', rest)
        tel = tm.group(1) if tm else None
        port = tm.group(2).strip() if tm and tm.group(2).strip() else None
        website, sns = None, []
        for u in urls:
            if 'instagram.com' in u or 'profile.ameba.jp' in u or 'facebook.com' in u:
                if not any(k in u for k in ACE_SNS_SKIP):
                    sns.append(u)
            elif website is None:
                website = u
        n += 1
        desc = ['地区: ' + section] if section else []
        digits = re.sub(r'\D', '', tel or '')
        if tel and (len(digits) < 10 or (digits[:3] in ('070', '080', '090') and len(digits) != 11)):
            desc.append('掲載元の電話番号は桁が不足')
        recs.append(base(
            src_id=f'fishingace:{n}:{m.group("name").strip()}', src_url=ACE,
            name=m.group('name').strip(), kana=kata2hira(m.group('kana').strip()), pref='宮崎県',
            city=ACE_CITY.get(section), port=port, tel=tel, website=website, sns=sns,
            types=['渡船'], description='／'.join(desc),
        ))
    log(f'ace total {len(recs)}')
    return recs


# ---------------------------------------------------------------- 日南市観光協会
def scrape_nichinan():
    recs = []
    # 8784 遊漁船 海響丸（公式サイトURLに個人名を含むため website は出力しない）
    url = 'https://www.kankou-nichinan.jp/tourisms/8784/'
    p = fetch(url).decode('utf-8', 'replace')
    assert '海響丸' in p and '070-2343-7090' in p and '６０００' in p
    pl = []
    for nm, dep, ret, hours, price in (('午前便', '06:00', '11:00', 5, 6000), ('午後便', '12:00', '17:00', 5, 6000),
                                       ('1日便', '06:00', '14:00', 8, 9000), ('夕方便', '16:00', '19:00', 3, 3000)):
        x = plan(nm, '乗合', price, f'{hours}時間 ￥{price}/1名（2名より出港）', url)
        x.update(depart=dep, **{'return': ret}, meet='出港15分前')
        pl.append(x)
    recs.append(base(
        src_id='nichinan-kanko:8784', src_url=url, name='海響丸', pref='宮崎県', city='日南市',
        port='目井津港', tel='070-2343-7090', types=['乗合'],
        methods=['ジギング', 'SLJ', 'ロックフィッシュゲーム', 'フラットフィッシュゲーム', '鯛ラバ'],
        holidays='時化日（営業日 月曜日～日曜日）',
        access='日南市南郷町目井津港「港の駅 めいつ」北側漁港',
        description='目井津港からスローピッチをメインにルアーフィッシングを案内。2名から出港、レンタルタックルあり（別料金）。',
        plans=pl,
    ))
    # 5224 観光フィッシング（マタウマリンサービス）
    url = 'https://www.kankou-nichinan.jp/tourisms/5224/'
    p = fetch(url).decode('utf-8', 'replace')
    assert '090-6421-4410' in p and 'matau.jimdo.com' in p
    recs.append(base(
        src_id='nichinan-kanko:5224', src_url=url, name='マタウマリンサービス', pref='宮崎県', city='日南市',
        address='宮崎県日南市大字西弁分字内江島913-1', tel='090-6421-4410', website='https://matau.jimdo.com/',
        access='集合場所は油津港周辺',
        description='観光フィッシング（ボートで沖に出てルアー・えさ釣り、1人から出船）と日南海岸観光クルージング。',
    ))
    log(f'nichinan total {len(recs)}')
    return recs


def main():
    recs = []
    for fn in (scrape_wiredfish, scrape_koshiki, scrape_ace, scrape_nichinan, scrape_beppu):
        recs += fn()
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(recs, f, ensure_ascii=False, indent=1)
    log(f'total {len(recs)} -> {OUT}')


if __name__ == '__main__':
    main()
