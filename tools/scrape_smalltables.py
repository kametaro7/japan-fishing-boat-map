#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小さな一覧3つ（イシグロ / 日刊スポーツ / 釣太郎）をまとめて取り込むクローラ

出力（src ごとに別ファイル）
  work/sources/ishiguro.json   src="ishiguro"
  work/sources/nikkan.json     src="nikkan"
  work/sources/tsuttarou.json  src="tsuttarou"
ログ: work/logs/smalltables.log（最終行 "DONE <件数> records"）
生HTML: work/cache/smalltables/<sha1(URL)>.html

サイト構造（2026-09-15 時点で確認）
(a) 釣具のイシグロ 釣り船・船宿一覧 https://www.ishiguro-gr.com/enjoy/funayado/
  - robots.txt: * は /core/ のみ Disallow。1ページ、div.contentsMain に「h3（県＋地域）＋table」×20。
  - 列 = 港名 / 船名 / HP。先頭行は見出し。詳細ページ・電話・住所は無い。
  - pref は h3 の先頭（"京都" は 京都府）。city は h3 の地域が「〇〇市/町/村」のときだけ（"蒲郡市・西尾市" は入れない）。
  - HP 欄が「※公式のHPはありません」等 → website=null。リンク文字が「…Facebook」でも href を使う。
(b) 日刊スポーツ 釣り宿情報
  - 関東＆東北 /leisure/fishing/tokyo/tokyo-fishingshop.html
      列 = ブロック / 地区(2列) / 屋号 / 住所 / 連絡先（電話<br>URL）。rowspan あり。
      tr の class でブロック判定: funazuri（船釣り）・tohoku（東北）は船宿、iso_tsuriguten（磯・釣具店）は
      他の行と電話が重複しない店だけ採用、kosen（湖川＝湖のボート店・アユ）は除外。
      電話の「★」は「HPでの予約番号」の印なので落とす。
  - 関西 /leisure/fishing/osaka/osaka-fishingshop.html
      列 = 地区(県) / 店名 / 分類（乗合・船・磯・筏カセ・波止・ルアー・アユ・川池）/ 所在地 / 連絡先。
      採用: 分類に 乗合/船/筏カセ を含む、または店名に「渡船」を含む。それ以外（漁協のアユ・川池、オトリ店、
      海づり公園、波止のみの店、磯のみで渡船屋と分からない店）は除外。
      types: 乗合→乗合、磯→渡船、筏カセ→筏・カセ（「船」は乗合/仕立の区別が無いので types に入れない）。
      所在地に県名が無ければ地区（県）を前に付ける。同じ店名・住所の2行（FCビッグワン）は1件に統合。
  - 北海道 /leisure/fishing/hokkaido/hokkaido-fishingshop.html（釣り宿: 店名/分類/所在地/泊/連絡先）
      ＋ /hokkaido-fs-fune.html（船釣りの釣果表: 「港・船名＝〇〇船長」＋電話だけ）。
      店名の「＝〇〇船長」は個人名なので出力しない。分類が「船」のみ、または店名に 丸/釣船 を含むものを採用
      （釣具店・フィッシュランド等の「河川湖沼・海・船」は除外）。同じ電話の重複行は統合。
      釣果表にしか無い船は電話と港だけのレコードとして追加。hokkaido-fsshop.html?id= は中身が空なので読まない。
  - robots.txt: * は /ajaxlib/ のみ Disallow。
(c) 釣太郎 https://tsuttarou.info/（WordPress、robots.txt は /wp-admin/ のみ）
  - 渡船情報 /渡船・遊漁船情報/: h2（エリア）→ p.is-style-icon_*（「地名　渡船名」）→ figure.wp-block-table
      行 = 渡船エリア / 電話番号 / 渡船料金 / 集合場所、出船港 / 備考 / 渡船屋ホームページ
      渡船料金 → plans（kind=渡船）。備考の「半夜釣り（15時～21時）5000円」「通し釣り（16時～翌5時）」→ 追加 plan。
      表の無い「営業終了」告知（しょらさん渡船: 渡船は終了・カセは営業）は types=筏・カセ で1件にする。
  - 遊漁船情報 /遊漁船情報/: p（「地名　船名」）→ table（主な釣り物/電話番号/料金/出船場所/備考/ホームページ）
      料金「乗合13000円 エサ氷付き、深海15000円」→ 金額ごとに plan。弁当・延長・レンタル等は料金の付記として扱う。
  - pref は全件 和歌山県（南紀のエリア別掲載）。
共通
  - lat/lon は無い（null）。website は公式サイトのみ。掲載/釣果ポータル（つりそく・入れ食い・みんなが・絶好調・
    gyo.ne.jp 等。他クローラと同じ扱い）は入れない。SNS は sns へ。個人名入りの SNS アカウントURLは入れない。

使い方
  python3 tools/scrape_smalltables.py                                 # 全件（3ファイル）
  python3 tools/scrape_smalltables.py --limit 15 --out work/sources/smalltables.sample.json --log work/logs/smalltables.sample.log
      --limit は src ごとの先頭 N 件。--out を指定すると3ソース分を1ファイルにまとめて書く（"{src}" を含めば src ごと）
  python3 tools/scrape_smalltables.py --sources nikkan,tsuttarou
  python3 tools/scrape_smalltables.py --ids 'tsuttarou:渡船/鹿島丸渡船,ishiguro:静岡県 東部/沼津港/城'
  --refresh でキャッシュを無視して再取得
"""
from __future__ import print_function

import argparse
import datetime
import hashlib
import os
import re
import sys
import time
from urllib.parse import quote, unquote, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (PREFS, nfkc, tel_display, norm_tel, norm_name, host_of, host_in, save_json,  # noqa: E402
                    NOT_OFFICIAL_HOSTS, SNS_HOSTS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "smalltables")
SRC_DIR = os.path.join(ROOT, "work", "sources")
LOG_DEFAULT = os.path.join(ROOT, "work", "logs", "smalltables.log")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒（SPEC: 0.8秒以上）。ホストごとに直列
FETCHED = "2026-09-15"

ISHIGURO_URL = "https://www.ishiguro-gr.com/enjoy/funayado/"
NIKKAN_BASE = "https://www.nikkansports.com/leisure/fishing/"
NIKKAN_KANTO = NIKKAN_BASE + "tokyo/tokyo-fishingshop.html"
NIKKAN_KANSAI = NIKKAN_BASE + "osaka/osaka-fishingshop.html"
NIKKAN_HOKKAIDO = NIKKAN_BASE + "hokkaido/hokkaido-fishingshop.html"
NIKKAN_HOKKAIDO_FUNE = NIKKAN_BASE + "hokkaido/hokkaido-fs-fune.html"
TSUTTAROU_TOSEN = "https://tsuttarou.info/渡船・遊漁船情報/"
TSUTTAROU_YUGYO = "https://tsuttarou.info/遊漁船情報/"

SOURCES = [
    ("ishiguro", [ISHIGURO_URL]),
    ("nikkan", [NIKKAN_KANTO, NIKKAN_KANSAI, NIKKAN_HOKKAIDO, NIKKAN_HOKKAIDO_FUNE]),
    ("tsuttarou", [TSUTTAROU_TOSEN, TSUTTAROU_YUGYO]),
]
DISALLOW = {
    "www.ishiguro-gr.com": ("/core/",),
    "www.nikkansports.com": ("/ajaxlib/",),
    "tsuttarou.info": ("/wp-admin/",),
}

# 公式サイトとして扱わないホスト（掲載/予約/釣果ポータル・地図など。scrape_tsuriyaro.py 等と揃える）
PORTAL_HOSTS = tuple(NOT_OFFICIAL_HOSTS) + (
    "ishiguro-gr.com", "nikkansports.com", "tsuttarou.info", "tsurisoku.com", "1091.co.jp", "turinet.com",
    "zekkouchou.com", "minnaga.com", "gyo.ne.jp", "bakucho.net", "gurenavi.jp", "tsurimaru.jp", "yugyosen.com",
    "fishing-station.jp", "itp.ne.jp", "mapion.co.jp", "tsuri-info.jp", "turi100.jp", "e-turibune.com",
)
# 楽天ブログは公式ブログとして扱う（rakuten.co.jp は NOT_OFFICIAL_HOSTS に含まれるため個別に許可）
BLOG_ALLOW_HOSTS = ("plaza.rakuten.co.jp",)
# 個人名（氏名）を含む SNS アカウント URL（出力しない）
PERSONAL_SNS_PATHS = ("instagram.com/shota_masuda__119", "instagram.com/hiroyuki_440", "instagram.com/yu_shi.shiba")

PUBLIC_KEYS = ["src", "src_id", "src_url", "name", "kana", "pref", "city", "address", "port", "lat", "lon",
               "tel", "website", "sns", "types", "targets", "methods", "holidays", "facilities", "capacity",
               "access", "description", "plans", "schedule_text", "fetched"]

_last_req = {}
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_stats = {"requests": 0, "cache_hits": 0}
_log_path = [LOG_DEFAULT]


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    d = os.path.dirname(_log_path[0])
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(_log_path[0], "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------- 取得

def cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


def fetch(url, refresh=False):
    """キャッシュ優先で取得。ホストごとに直列・間隔 MIN_INTERVAL 秒・429/503 は指数バックオフ（最大5回）。"""
    parts = urlsplit(url)
    if any(unquote(parts.path).startswith(d) for d in DISALLOW.get(parts.netloc, ())):
        raise ValueError("robots.txt Disallow: %s" % url)
    p = cache_path(url)
    if not refresh and os.path.exists(p) and os.path.getsize(p) > 0:
        _stats["cache_hits"] += 1
        with open(p, "rb") as f:
            return f.read().decode("utf-8", "replace")
    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    req_url = urlunsplit((parts.scheme, parts.netloc, quote(unquote(parts.path)), parts.query, ""))
    delay = 5.0
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last_req.get(parts.netloc, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_req[parts.netloc] = time.time()
        try:
            r = _session.get(req_url, timeout=60)
            _stats["requests"] += 1
        except requests.RequestException as e:
            if attempt >= 5:
                raise
            log("WARN fetch error %s (%s) retry in %.0fs" % (url, e, delay))
            time.sleep(delay)
            delay *= 2
            continue
        if r.status_code in (429, 503):
            if attempt >= 5:
                raise RuntimeError("HTTP %d after retries: %s" % (r.status_code, url))
            ra = r.headers.get("Retry-After")
            w = float(ra) if ra and ra.isdigit() else delay
            log("WARN HTTP %d %s backoff %.0fs" % (r.status_code, url, w))
            time.sleep(w)
            delay *= 2
            continue
        if r.status_code != 200:
            raise RuntimeError("HTTP %d: %s" % (r.status_code, url))
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, p)
        return r.content.decode("utf-8", "replace")
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------- 正規化ヘルパ

def clean(s):
    s = nfkc(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def cut(s, n=100):
    s = clean(s)
    return s if len(s) <= n else s[:n - 1].rstrip(" 、,/") + "…"


def strip_reading(s):
    """「串本町安指（あざし）」「空風（そらかぜ）」の読み仮名を落とす。"""
    return clean(re.sub(r"\s*[（(][ぁ-んァ-ヶー・\s]+[）)]", "", nfkc(s)))


def clean_addr(s):
    """住所の正規化（NFKC で直らない数字間の ― ‐ などをハイフンに）。"""
    a = clean(s)
    a = re.sub(r"(?<=\d)\s*[―‐‑–—−ｰー－]\s*(?=\d)", "-", a)
    return a


def pref_from_short(s):
    s = clean(s)
    if not s:
        return None
    for p in PREFS:
        if s == p or (p != "北海道" and s == p[:-1]):
            return p
    return None


def pref_prefix(s):
    s = clean(s)
    for p in PREFS:
        if s.startswith(p):
            return p
    return None


CITY_RE = re.compile(r"^((?:[^\s市区町村郡]{1,6}郡)?(?:[^\s市区町村郡]{1,5}市[^\s市区町村郡]{1,4}区|[^\s市区町村郡]{1,6}[市町村]))")


def city_from_address(addr):
    a = clean(addr)
    p = pref_prefix(a)
    if not p:
        return None
    rest = a[len(p):]
    if p == "東京都":
        m = re.match(r"^([^\s市区町村郡]{1,4}区)", rest)
        if m:
            return m.group(1)
    m = CITY_RE.match(rest)
    return m.group(1) if m else None


DROP_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|fref|hc_location|hc_ref|pnref|ref|locale|hl|__tn__)$")


def clean_link(u):
    u = (u or "").strip()
    if not re.match(r"^https?://", u, re.I):
        return None
    u = re.sub(r"#.*$", "", u)
    parts = urlsplit(u)
    query = parts.query
    if query:
        query = urlencode([(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if not DROP_PARAMS.match(k)])
    path = parts.path or "/"
    h = host_of(u)
    # サブドメイン型ブログの記事/カテゴリURL → ブログのトップ（他ソースと url_key を揃えるため）
    if host_in(h, SUBDOMAIN_BLOG_HOSTS) and h.count(".") >= 2 and not h.startswith(("blog.", "plaza.")):
        path, query = "/", ""
    # 公式サイト内の釣果/カテゴリのページ → サイトのトップ
    elif re.match(r"^/(category|catch|choka)(/|$)", path, re.I):
        path, query = "/", ""
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


SUBDOMAIN_BLOG_HOSTS = ("exblog.jp", "hamazo.tv", "jugem.jp", "fc2.com", "hatenablog.com", "hatenablog.jp",
                        "amebaownd.com", "jimdofree.com", "jimdo.com", "livedoor.biz", "naturum.ne.jp", "sblo.jp",
                        "i-ra.jp", "blogspot.com")


def classify_link(u):
    """→ ('website'|'sns'|'portal'|'personal', url)"""
    u = clean_link(u)
    if not u:
        return None, None
    h = host_of(u)
    if host_in(h, SNS_HOSTS):
        low = unquote(u).lower()
        if any(x in low for x in PERSONAL_SNS_PATHS):
            return "personal", u
        return "sns", u
    if host_in(h, BLOG_ALLOW_HOSTS):
        return "website", u
    if host_in(h, PORTAL_HOSTS):
        return "portal", u
    return "website", u


def pick_links(urls, rec_id):
    website, sns, dropped = None, [], []
    for raw in urls:
        kind, u = classify_link(raw)
        if not kind:
            continue
        if kind == "website" and not website:
            website = u
        elif kind == "sns" and u not in sns:
            sns.append(u)
        elif kind in ("portal", "personal"):
            dropped.append("%s %s" % (kind, u))
    if dropped:
        log("LINK dropped %s: %s" % (rec_id, "; ".join(dropped)))
    return website, sns


def first_tel(s):
    s = nfkc(s).replace("★", "")
    return tel_display(s)


def new_record(src, src_id, src_url, name):
    rec = {k: None for k in PUBLIC_KEYS}
    rec.update({"src": src, "src_id": src_id, "src_url": src_url, "name": name, "sns": [], "types": [],
                "targets": [], "methods": [], "facilities": [], "plans": [], "fetched": FETCHED})
    return rec


def public(rec):
    return dict((k, rec.get(k)) for k in PUBLIC_KEYS)


def cell_text(td, sep="/"):
    return clean(td.get_text(sep, strip=True)) if td is not None else ""


def cell_links(td):
    return [a["href"].strip() for a in td.find_all("a", href=True) if a["href"].strip()] if td is not None else []


def expand_rows(table):
    """rowspan/colspan を展開して [(tr, [td, ...]), ...] を返す。"""
    out, pend = [], {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        row, ci, col = [], 0, 0
        while ci < len(cells) or col in pend:
            if col in pend:
                td, n = pend[col]
                row.append(td)
                if n <= 1:
                    del pend[col]
                else:
                    pend[col] = (td, n - 1)
                col += 1
                continue
            td = cells[ci]
            ci += 1
            rs = int(td.get("rowspan", 1) or 1)
            cs = int(td.get("colspan", 1) or 1)
            for _ in range(cs):
                row.append(td)
                if rs > 1:
                    pend[col] = (td, rs - 1)
                col += 1
        out.append((tr, row))
    return out


# ---------------------------------------------------------------- 料金・時刻

PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{3,6})\s*円")
ADDON_RE = re.compile(r"弁当|延長|レンタル|駐車|貸し|貸竿|竿|追加|子供|小学生|中学生|女性|同乗|見学")
INCL_RE = re.compile(r"^[\s/、,]*([（(]?[^/、,。（）()]*?(?:込み|込|付き|付|含む)[）)]?)")


def plan_template(name="", kind=""):
    return {"name": name, "kind": kind, "targets": [], "price": None, "price_text": "", "depart": None,
            "return": None, "meet": "", "season": "", "days": "", "includes": "", "url": None}


def parse_prices(text, default_name, default_kind):
    """料金テキスト → plans。金額ごとに1プラン。弁当・延長等の付記は直前のプランの price_text に寄せる。"""
    t = clean(text).replace("，", ",")
    ms = list(PRICE_RE.finditer(t))
    if not ms:
        return []
    plans = []
    single = sum(1 for i, m in enumerate(ms) if not ADDON_RE.search(_label_before(t, ms, i)[1])) <= 1
    for i, m in enumerate(ms):
        _, label = _label_before(t, ms, i)
        nxt = ms[i + 1].start() if i + 1 < len(ms) else len(t)
        after = t[m.end():nxt]
        inc = INCL_RE.match(after)
        includes = clean(inc.group(1)).strip("（）()") if inc else ""
        price = int(m.group(1).replace(",", ""))
        seg = clean("%s %s円 %s" % (label, m.group(1), inc.group(1) if inc else ""))
        if ADDON_RE.search(label) and plans:
            plans[-1]["_addons"].append(seg)
            continue
        name = re.sub(r"【[^】]*】", "", label)
        name = clean(re.sub(r"^(料金|乗合\s*)?\s*(1名様?|1人|お一人様?|大人)?\s*|[:：は]$", "", name)).strip(":：")
        kind = default_kind or ("乗合" if "乗合" in label else "")
        p = plan_template(name or default_name, kind)
        p["price"] = price
        p["includes"] = includes
        p["_seg"] = seg
        p["_addons"] = []
        plans.append(p)
    for p in plans:
        if single and len(plans) == 1:
            p["price_text"] = cut(t, 100)
        else:
            p["price_text"] = cut(" ".join([p["_seg"]] + p["_addons"]), 100)
        del p["_seg"]
        del p["_addons"]
    return plans


def _label_before(t, ms, i):
    """i 番目の金額の直前のラベル（前の金額の付記部分を除く）。"""
    start = ms[i - 1].end() if i > 0 else 0
    between = t[start:ms[i].start()]
    if i > 0:
        inc = INCL_RE.match(between)
        if inc:
            between = between[inc.end():]
    # 文区切りの後ろだけをラベルにする
    parts = re.split(r"[/、,。]", between)
    label = clean(parts[-1]) if parts else ""
    label = label.strip("（）()※+＋ ")
    return start, label


def hm(s):
    s = clean(s)
    m = re.match(r"^翌?\s*(\d{1,2})\s*[:：]\s*(\d{2})$", s)
    if m:
        return "%02d:%s" % (int(m.group(1)), m.group(2))
    m = re.match(r"^翌?\s*(\d{1,2})時(半|(\d{1,2})分)?$", s)
    if m:
        mm = 30 if m.group(2) == "半" else int(m.group(3) or 0)
        return "%02d:%02d" % (int(m.group(1)), mm)
    return None


NIGHT_RE = re.compile(r"(半夜釣り|通し釣り|夜釣り)(?:あり)?[（(]([^）)]{2,20})[）)]\s*([\d,]{3,7}円)?")


def night_plans(note, kind):
    """備考の「夏季は半夜釣り（15時～21時）5000円」「通し釣り（16時～翌5時）」→ plans"""
    t = clean(note)
    plans = []
    together = re.search(r"ともに\s*([\d,]{3,7})円", t)
    for sent in re.split(r"[。/]", t):
        for m in NIGHT_RE.finditer(sent):
            rng = re.split(r"[～〜~\-ー]", m.group(2))
            p = plan_template(m.group(1), kind)
            if len(rng) == 2:
                p["depart"], p["return"] = hm(rng[0]), hm(rng[1])
            if m.group(3):
                p["price"] = int(re.sub(r"\D", "", m.group(3)))
            elif together and together.start() > t.find(sent):
                p["price"] = int(together.group(1).replace(",", ""))
            p["price_text"] = cut(m.group(0) if m.group(3) or not p["price"] else "%s ともに%s円" % (m.group(0), together.group(1)), 100)
            if "夏季" in sent[:m.start()] or "夏場" in sent[:m.start()]:
                p["season"] = "夏季"
            plans.append(p)
    return plans


# ---------------------------------------------------------------- (a) イシグロ

def parse_ishiguro(html, url):
    soup = BeautifulSoup(html, "lxml")
    main = soup.find(class_="contentsMain") or soup
    recs, excluded = [], []
    for table in main.find_all("table"):
        h3 = table.find_previous("h3")
        heading = clean(h3.get_text(" ", strip=True)) if h3 else ""
        head_parts = heading.split(" ", 1)
        pref = pref_from_short(head_parts[0])
        region = head_parts[1] if len(head_parts) > 1 else ""
        city = None
        if region and "・" not in region:
            m = re.match(r"^(\S+?[市町村])", region)
            city = m.group(1) if m else None
        if not pref:
            log("WARN ishiguro: 県が分からない見出し %r" % heading)
        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) < 3:
                continue
            port, name = cell_text(tds[0], " "), cell_text(tds[1], " ")
            if re.sub(r"\s", "", name) == "船名" or re.sub(r"\s", "", port) == "港名" or not name:
                continue
            sid = "%s/%s/%s" % (heading, port, name)
            if re.search(r"湖$", port) and re.search(r"釣船店|貸(し)?ボート|ボートハウス", name):
                excluded.append((sid, "湖の貸しボート"))
                continue
            rec = new_record("ishiguro", sid, url, strip_reading(name))
            rec["pref"] = pref
            rec["city"] = city
            rec["port"] = None if pref_prefix(port) else (strip_reading(port) or None)
            rec["website"], rec["sns"] = pick_links(cell_links(tds[2]), sid)
            if not cell_links(tds[2]) and cell_text(tds[2]):
                log("NOTE no link %s: %s" % (sid, cell_text(tds[2])))
            recs.append(rec)
    return recs, excluded


# ---------------------------------------------------------------- (b) 日刊スポーツ

def contact_parts(td):
    """連絡先セル → (電話テキスト, [URL])"""
    txt = td.get_text("\n", strip=True) if td is not None else ""
    lines = [clean(x) for x in txt.split("\n") if clean(x)]
    links = cell_links(td)
    tel_lines = [x for x in lines if not re.match(r"^https?://", x)]
    return " ".join(tel_lines), links


def parse_nikkan_kanto(html, url):
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    recs, excluded, pending_iso = [], [], []
    for tr, row in expand_rows(table):
        cls = tr.get("class") or []
        if "head" in cls or len(row) < 6:
            continue
        block = cell_text(row[0], "")
        contact_td, addr_td, name_td, port_td = row[-1], row[-2], row[-3], row[-4]
        area = cell_text(row[-5], "") if len(row) >= 7 else ""
        name = clean(name_td.get_text(" ", strip=True))
        port = clean(port_td.get_text("", strip=True))
        sid = "関東東北/%s/%s" % (port, name)
        if "kosen" in cls or block == "湖川":
            excluded.append((sid, "湖川（湖のボート店・アユ）"))
            continue
        if re.search(r"釣具|つり具", name):
            excluded.append((sid, "釣具店"))
            continue
        tel_txt, links = contact_parts(contact_td)
        rec = new_record("nikkan", sid, url, strip_reading(name))
        rec["address"] = clean_addr(addr_td.get_text("", strip=True)) or None
        rec["pref"] = pref_prefix(rec["address"] or "") or None
        rec["city"] = city_from_address(rec["address"] or "")
        rec["port"] = port or None
        rec["tel"] = first_tel(tel_txt)
        rec["website"], rec["sns"] = pick_links(links, sid)
        rec["_area"] = area
        if "iso_tsuriguten" in cls:
            pending_iso.append(rec)
            continue
        recs.append(rec)
    tels = set(norm_tel(r["tel"] or "") for r in recs if r.get("tel"))
    for rec in pending_iso:
        if rec.get("tel") and norm_tel(rec["tel"]) in tels:
            excluded.append((rec["src_id"], "磯・釣具店ブロックで電話が船宿の行と同じ（同一店の別名義）"))
            continue
        recs.append(rec)
    return recs, excluded


KANSAI_TYPES = [("乗合", "乗合"), ("磯", "渡船"), ("筏カセ", "筏・カセ")]


def parse_nikkan_kansai(html, url):
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    recs, excluded = [], []
    by_key = {}
    for tr, row in expand_rows(table):
        cls = tr.get("class") or []
        if "head" in cls or len(row) < 5:
            continue
        area, name, cats = cell_text(row[0], ""), clean(row[1].get_text(" ", strip=True)), cell_text(row[2], "")
        addr = clean_addr(row[3].get_text("", strip=True))
        tel_txt, links = contact_parts(row[4])
        pref = pref_from_short(area)
        sid = "関西/%s/%s" % (area, name)
        cat_set = set(x for x in re.split(r"[・/]", cats) if x)
        boat = bool(cat_set & {"乗合", "船", "筏カセ"}) or "渡船" in name
        if not boat:
            excluded.append((sid, "分類=%s（船宿以外）" % cats))
            continue
        if addr and not pref_prefix(addr) and pref:
            addr = pref + addr
        rec = new_record("nikkan", sid, url, strip_reading(name))
        rec["pref"] = pref_prefix(addr) or pref
        rec["address"] = addr or None
        rec["city"] = city_from_address(addr)
        rec["tel"] = first_tel(tel_txt)
        rec["website"], rec["sns"] = pick_links(links, sid)
        rec["types"] = [t for k, t in KANSAI_TYPES if k in cat_set]
        if "ルアー" in cat_set:
            rec["methods"] = ["ルアー"]
        note = re.search(r"[（(]([^）)]+)[）)]", tel_txt)
        rec["_tel_note"] = note.group(1) if note else ""
        key = (norm_name(name), addr)
        if key in by_key:
            # 同じ店名・所在地の2行（例 ＦＣビッグワン: 仕立船・乗合 と 磯 で電話とHPが別）→ 1件にまとめ、2つ目の連絡先は description へ
            base = by_key[key]
            first = "%s: %s" % (base["_tel_note"] or "連絡先", base.get("tel") or "")
            second = "%s: %s" % (rec["_tel_note"] or "別の連絡先", rec.get("tel") or "")
            if rec.get("website") and rec["website"] != base.get("website"):
                second += " %s" % rec["website"]
            base["description"] = cut("%s / %s" % (first, second), 100)
            for t in rec["types"]:
                if t not in base["types"]:
                    base["types"].append(t)
            log("MERGE %s (same name+address, tel %s)" % (sid, rec["tel"]))
            continue
        by_key[key] = rec
        recs.append(rec)
    # 別の県の店と電話が一致する行は掲載側の誤記とみて電話を落とす（例 久保渡船＝石倉渡船の番号）
    seen = {}
    for rec in recs:
        t = norm_tel(rec.get("tel") or "")
        if not t:
            continue
        if t in seen and seen[t]["pref"] != rec["pref"]:
            log("WARN tel duplicated across prefs, dropped from %s (same as %s): %s" % (rec["src_id"], seen[t]["src_id"], rec["tel"]))
            rec["tel"] = None
        else:
            seen.setdefault(t, rec)
    return recs, excluded


def split_hokkaido_name(s):
    """「八雲・佳栄丸＝木村船長」→ ('八雲', '佳栄丸')。船長名は捨てる。"""
    s = clean(s)
    s = re.sub(r"[=＝].*$", "", s)
    if "・" in s:
        area, name = s.split("・", 1)
        return clean(area), clean(name)
    return None, s


def parse_nikkan_hokkaido(html, url, fune_html, fune_url):
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    recs, excluded = [], []
    by_tel = {}
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        raw, cats, addr, stay = cell_text(tds[0], ""), cell_text(tds[1], ""), clean_addr(tds[2].get_text("", strip=True)), cell_text(tds[3], "")
        area, name = split_hokkaido_name(raw)
        sid = "北海道/%s/%s" % (area or "", name)
        cat_set = set(x for x in cats.split("・") if x)
        if "船" not in cat_set or (cat_set != {"船"} and not re.search(r"丸|釣船|つり船", name)):
            excluded.append((sid, "分類=%s（釣具店など）" % cats))
            continue
        tel = first_tel(cell_text(tds[4], " "))
        t = norm_tel(tel or "")
        if t and t in by_tel:
            base = by_tel[t]
            if not base.get("address") and addr:
                base["address"] = "北海道" + addr if not addr.startswith("北海道") else addr
                base["city"] = city_from_address(base["address"])
            log("MERGE %s -> %s (same tel)" % (sid, base["src_id"]))
            continue
        rec = new_record("nikkan", sid, url, name)
        rec["pref"] = "北海道"
        if addr:
            rec["address"] = addr if addr.startswith("北海道") else "北海道" + addr
            rec["city"] = city_from_address(rec["address"])
        rec["port"] = area
        rec["tel"] = tel
        rec["types"] = []
        if stay == "○":
            rec["facilities"] = ["宿泊"]
        recs.append(rec)
        if t:
            by_tel[t] = rec
    # 釣果表（船）
    if fune_html:
        fs = BeautifulSoup(fune_html, "lxml")
        for td in fs.find_all("td", class_="fsHotel"):
            lines = [clean(x) for x in td.get_text("\n", strip=True).split("\n") if clean(x)]
            if not lines:
                continue
            area, name = split_hokkaido_name(lines[0])
            tel = first_tel(" ".join(lines[1:]))
            sid = "北海道/%s/%s" % (area or "", name)
            t = norm_tel(tel or "")
            if t and t in by_tel:
                continue
            if re.search(r"釣具|つり具|フィッシュランド", name):
                continue
            key = norm_name(re.sub(r"^第\d+", "", nfkc(name)))
            cands = [r for r in recs if norm_name(re.sub(r"^第\d+", "", nfkc(r["name"]))) == key]
            if cands:
                if tel and cands[0].get("tel") != tel:
                    log("NOTE hokkaido tel differs %s: 釣り宿=%s 釣果表=%s" % (cands[0]["src_id"], cands[0].get("tel"), tel))
                continue
            rec = new_record("nikkan", sid, fune_url, name)
            rec["pref"] = "北海道"
            rec["port"] = area
            rec["tel"] = tel
            recs.append(rec)
            log("ADD from catch table %s" % sid)
    return recs, excluded


# ---------------------------------------------------------------- (c) 釣太郎

PORT_WORD_RE = re.compile(r"([^\s、。,（）()は/:：]+?(?:漁港|魚港|港))")


def port_from_text(s):
    # 「戎（えびす）漁港」は読みを落として連結、「南部（みなべ）堺西港」は区切りにする
    t = re.sub(r"[（(][ぁ-んァ-ヶー]+[）)](?=(?:漁港|魚港|港))", "", clean(s))
    t = re.sub(r"[（(][ぁ-んァ-ヶー]+[）)]", " ", t)
    for m in PORT_WORD_RE.finditer(t):
        w = m.group(1)
        if w in ("漁港", "魚港", "出港", "乗船港", "港") or w.endswith("出港"):
            continue
        w = re.sub(r"^.+?[市町](?=.+港$)", "", w)
        w = re.sub(r"^(出港|集合)", "", w)
        if len(w) >= 2 and w not in ("漁港", "魚港"):
            return w
    return None


def split_title(s):
    """「みなべ町埴田　鹿島丸渡船」→ (地名, 名前)"""
    t = strip_reading(s)
    parts = t.split(" ")
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


METHOD_WORDS = re.compile(r"釣り?$|釣メイン|ジギング|エギング|アジング|キャスティング|フカセ|アンダーベイト|のませ|ノマセ|泳がせ|"
                          r"ティップラン|天秤|SLJ|深海|バチコン|ルアー|エサ釣")


def split_targets(s):
    t = clean(s)
    t = re.sub(r"など.*$", "", t)
    targets, methods = [], []
    for w in re.split(r"[・、,/]", t):
        w = clean(w)
        if not w or w in ("色々",):
            continue
        w = {"真鯛": "マダイ"}.get(w, w)
        dst = methods if METHOD_WORDS.search(w) else targets
        if w not in dst:
            dst.append(w)
    return targets, methods


def table_dict(fig):
    d = {}
    for tr in fig.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        d[clean(tds[0].get_text("", strip=True))] = tds[1]
    return d


def get_cell(d, *keys):
    for k in keys:
        for dk, td in d.items():
            if dk.startswith(k):
                return td
    return None


def parse_tsuttarou(html, url, page_kind):
    soup = BeautifulSoup(html, "lxml")
    root = soup.find(class_="post_content") or soup
    recs, excluded = [], []
    area_h2 = ""
    title, pending_closed = None, None
    for el in root.find_all(["h2", "p", "figure"]):
        if el.find_parent("figure") or el.find_parent("table"):
            continue
        if el.name == "h2":
            area_h2 = clean(el.get_text(" ", strip=True))
            continue
        if el.name == "p":
            txt = clean(el.get_text(" ", strip=True))
            if not txt:
                continue
            cls = " ".join(el.get("class") or [])
            if pending_closed is not None and re.match(r"^https?://", txt):
                pending_closed["website"], pending_closed["sns"] = pick_links(cell_links(el) or [txt], pending_closed["src_id"])
                pending_closed = None
                continue
            if "is-style-icon" in cls:
                title = txt
                if "営業終了" in txt:
                    m = re.match(r"^(.*?)\s*((?:\d{4}年)?\d{1,2}月\d{1,2}日.*営業終了.*)$", txt)
                    head, note = (m.group(1), m.group(2)) if m else (txt, "")
                    loc, name = split_title(head)
                    sid = "%s/%s" % ("渡船" if page_kind == "tosen" else "遊漁船", name)
                    rec = new_record("tsuttarou", sid, url, name)
                    rec["pref"] = "和歌山県"
                    mc = re.match(r"^(\S+?[市町村])", loc)
                    rec["city"] = mc.group(1) if mc else None
                    rec["port"] = port_from_text(re.sub(r"^[^（(]*[（(]|[）)].*$", "", loc)) if "（" in loc or "(" in loc else None
                    if "カセは営業" in note:
                        rec["types"] = ["筏・カセ"]
                    rec["description"] = cut("渡船は%s" % note.replace("にて営業終了", "で営業終了").replace(" カセは営業しております。", "（カセは営業）"), 100)
                    rec["_area"] = area_h2
                    recs.append(rec)
                    pending_closed = rec
                    title = None
                    log("NOTE closed notice %s: %s" % (sid, note))
                continue
            continue
        if el.name == "figure" and el.find("table"):
            if not title:
                log("WARN tsuttarou: 名前の無い表 %s" % url)
                continue
            loc, name = split_title(title)
            title = None
            d = table_dict(el)
            sid = "%s/%s" % ("渡船" if page_kind == "tosen" else "遊漁船", name)
            rec = new_record("tsuttarou", sid, url, name)
            rec["pref"] = "和歌山県"
            mc = re.match(r"^(\S+?[市町村])", loc)
            rec["city"] = mc.group(1) if mc else None
            rec["_area"] = area_h2
            tel_td = get_cell(d, "電話")
            rec["tel"] = first_tel(cell_text(tel_td, " "))
            hp_td = get_cell(d, "渡船屋ホームページ", "ホームページ")
            rec["website"], rec["sns"] = pick_links(cell_links(hp_td), sid)
            note = cell_text(get_cell(d, "備考"), "/")
            meet_td = get_cell(d, "集合場所", "出船場所")
            meet = cell_text(meet_td, " ")
            loc_paren = re.search(r"[（(]([^）)]*港)[）)]", loc)
            rec["port"] = port_from_text(meet) or (loc_paren.group(1) if loc_paren else None)
            if meet and pref_prefix(meet) and not rec["port"]:
                rec["address"] = clean_addr(meet.replace(" ", ""))
                rec["city"] = rec["city"] or city_from_address(rec["address"])
            if meet:
                rec["access"] = cut(meet, 100)
            if page_kind == "tosen":
                rec["types"] = ["渡船"]
                area = cell_text(get_cell(d, "渡船エリア"), " ")
                if area:
                    rec["description"] = cut("渡船エリア: %s" % area, 100)
                fee = cell_text(get_cell(d, "渡船料金"), " ")
                plans = parse_prices(fee, "渡船", "渡船")
                plans += night_plans(note, "渡船")
            else:
                targets, methods = split_targets(cell_text(get_cell(d, "主な釣り物"), "・"))
                rec["targets"], rec["methods"] = targets, methods
                fee = cell_text(get_cell(d, "料金"), "/")
                plans = parse_prices(fee, "", "")
                if not plans and fee:
                    rec["description"] = cut("料金: %s" % fee, 100)
                if any(p["kind"] == "乗合" for p in plans):
                    rec["types"] = ["乗合"]
                # 備考のうち掲載サイト側の感想（「〜さんです」「面白い」）は入れない
                facts = [clean(x) for x in re.split(r"(?<=。)|/", note)
                         if clean(x) and not re.search(r"さんです|面白い|おすすめ|オススメ", x)]
                if facts:
                    rec["description"] = cut(clean("%s %s" % (rec.get("description") or "", " ".join(facts))), 100)
            rec["plans"] = plans
            hol = re.search(r"(\d{1,2}月)は休船", note)
            if hol:
                rec["holidays"] = "%sは休船" % hol.group(1)
            frags = [clean(x) for x in re.split(r"[。/]", note) if clean(x)]
            sched = []
            for i, s in enumerate(frags):
                if not re.search(r"休船|早出|遅出|出船|島割り|磯割り|隔日", s):
                    continue
                if s.endswith("が") and i + 1 < len(frags):  # 「島割りは奇数日が/カツオ、…」のように次の行に続く
                    s = "%s %s" % (s, frags[i + 1])
                sched.append(s)
            if sched:
                rec["schedule_text"] = cut("。".join(sched), 100)
            recs.append(rec)
    return recs, excluded


# ---------------------------------------------------------------- main

def dedupe_ids(recs):
    seen = {}
    for r in recs:
        if r["src_id"] in seen:
            seen[r["src_id"]] += 1
            r["src_id"] = "%s(%d)" % (r["src_id"], seen[r["src_id"]])
        else:
            seen[r["src_id"]] = 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="src ごとの先頭 N 件だけ")
    ap.add_argument("--ids", default="", help="'src:src_id' または src_id をカンマ区切りで")
    ap.add_argument("--sources", default="", help="ishiguro,nikkan,tsuttarou のうち対象")
    ap.add_argument("--out", default="", help="出力先。省略時は work/sources/<src>.json。{src} を含まなければ1ファイルにまとめる")
    ap.add_argument("--log", default=LOG_DEFAULT)
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    args = ap.parse_args()
    _log_path[0] = os.path.abspath(args.log)
    ids = set(clean(x) for x in args.ids.split(",") if clean(x))
    only = set(clean(x) for x in args.sources.split(",") if clean(x))
    sources = [(k, urls) for k, urls in SOURCES if not only or k in only]
    total_pages = sum(len(u) for _, u in sources)
    log("start out=%s limit=%s ids=%d sources=%s" % (args.out or "work/sources/<src>.json", args.limit, len(ids),
                                                     ",".join(k for k, _ in sources)))

    def out_path(src):
        if not args.out:
            return os.path.join(SRC_DIR, "%s.json" % src)
        return os.path.abspath(args.out.replace("{src}", src))

    per_src = not args.out or "{src}" in args.out  # False なら --out の1ファイルにまとめる
    all_recs, done_pages, last_saved = [], 0, 0
    summary = []
    for src, urls in sources:
        pages = {}
        for u in urls:
            try:
                pages[u] = fetch(u, args.refresh)
            except Exception as e:  # noqa
                log("ERROR fetch %s: %s" % (u, e))
                pages[u] = None
            done_pages += 1
            log("progress %d/%d pages (%s) requests=%d cache=%d" % (done_pages, total_pages, u, _stats["requests"], _stats["cache_hits"]))
        recs, excluded = [], []
        try:
            if src == "ishiguro":
                if pages[ISHIGURO_URL]:
                    r, x = parse_ishiguro(pages[ISHIGURO_URL], ISHIGURO_URL)
                    recs += r
                    excluded += x
            elif src == "nikkan":
                if pages[NIKKAN_KANTO]:
                    r, x = parse_nikkan_kanto(pages[NIKKAN_KANTO], NIKKAN_KANTO)
                    recs += r
                    excluded += x
                if pages[NIKKAN_KANSAI]:
                    r, x = parse_nikkan_kansai(pages[NIKKAN_KANSAI], NIKKAN_KANSAI)
                    recs += r
                    excluded += x
                if pages[NIKKAN_HOKKAIDO]:
                    r, x = parse_nikkan_hokkaido(pages[NIKKAN_HOKKAIDO], NIKKAN_HOKKAIDO,
                                                 pages.get(NIKKAN_HOKKAIDO_FUNE), NIKKAN_HOKKAIDO_FUNE)
                    recs += r
                    excluded += x
            elif src == "tsuttarou":
                for u, kind in ((TSUTTAROU_TOSEN, "tosen"), (TSUTTAROU_YUGYO, "yugyo")):
                    if pages[u]:
                        r, x = parse_tsuttarou(pages[u], u, kind)
                        recs += r
                        excluded += x
        except Exception as e:  # noqa
            import traceback
            log("ERROR parse %s: %s\n%s" % (src, e, traceback.format_exc()))
        for sid, why in excluded:
            log("EXCLUDE %s:%s (%s)" % (src, sid, why))
        dedupe_ids(recs)
        if ids:
            recs = [r for r in recs if r["src_id"] in ids or "%s:%s" % (src, r["src_id"]) in ids]
        if args.limit:
            recs = recs[:args.limit]
        log("%s: %d records (excluded %d)" % (src, len(recs), len(excluded)))
        summary.append("%s=%d" % (src, len(recs)))
        all_recs.extend(recs)
        if per_src:
            save_json(out_path(src), [public(r) for r in recs], indent=1)
        elif len(all_recs) - last_saved >= 100:
            save_json(out_path(src), [public(r) for r in all_recs], indent=1)
            last_saved = len(all_recs)
    if not per_src:
        save_json(out_path(""), [public(r) for r in all_recs], indent=1)
    log("records %s" % " ".join(summary))
    log("requests=%d cache_hits=%d" % (_stats["requests"], _stats["cache_hits"]))
    log("DONE %d records" % len(all_recs))


if __name__ == "__main__":
    main()
