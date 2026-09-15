#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gap0205: 青森県・秋田県の地域一覧から釣り船を取り込む（src="gap0205"）

全国の予約・掲載サイトに少ない青森・秋田の釣り船を、地域寄りの一覧から集める。
（2026-09-15 時点で確認。詳細は work/discovery/gap0205.md）

取り込む一覧
  tsurip  遊漁船情報つりっぷ（tsurip.com）カテゴリ「青森県」（5ページ）「秋田県」
          → 各記事: 電話・出港場所（港名＋〒住所）・魚・釣り方・乗合/仕立の料金・HP/SNS・地図(pb)
  magurop マグロ遊漁船情報まぐろっぷ（magurop.com。つりっぷと同じ運営）カテゴリ「青森県」（4ページ）「秋田県」
  sanook  SANOOK FISHING「青森県/秋田県の クロマグロ・マダイ・ブリ 釣り おすすめ釣り船一覧」6記事（2025年11月）
          → 表: 船名(公式サイトリンク)/電話/種別/予約方法/住所＋港/釣り方/対象魚/備考。記事間で同じ船は1件にまとめる
  fiship  釣り船情報 Fiship.jp の /aomori /akita の「釣り船・釣り宿」（各船ページ）
          → 最新のお知らせが5年以上前（または無し）の船は stale=true
  kitanoyado 大間町「北の宿」の釣り船ページ（第17喜安丸。更新日不明の旧式ページ → stale=true）

個人名（船長名・代表者名）は読まない。Facebook の個人プロフィールURL（/people/ や 名.姓.数字）も出さない。

使い方
  python3 tools/scrape_gap0205.py                 # 取得（キャッシュ優先）→ work/sources/gap0205.json
  python3 tools/scrape_gap0205.py --refresh       # キャッシュを無視して再取得
  python3 tools/scrape_gap0205.py --only sanook,fiship --out work/sources/gap0205.sample.json
  python3 tools/scrape_gap0205.py get URL [...]   # 1ページだけキャッシュ付き取得
"""
from __future__ import print_function

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import sys
import time
import urllib.robotparser
from urllib.parse import urlsplit, urljoin, unquote

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (nfkc, to_pref, norm_tel, tel_display, host_of, host_in, clean_url,  # noqa: E402
                    is_sns_url, norm_name, NOT_OFFICIAL_HOSTS, SNS_HOSTS, SHARED_HOSTS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "gap0205")
OUT_DEFAULT = os.path.join(ROOT, "work", "sources", "gap0205.json")
LOG_DEFAULT = os.path.join(ROOT, "work", "logs", "gap0205.log")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒（SPEC: 0.8秒以上、1ホスト直列）
FETCHED = "2026-09-15"
STALE_BEFORE = "2021-09-15"  # これより古い情報しか無い一覧は stale
REFRESH = False
_last = {}
_robots = {}
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_logf = None


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, file=sys.stderr)
    if _logf:
        _logf.write(line + "\n")
        _logf.flush()


# ---------------------------------------------------------------- 取得
def cache_path(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    host = urlsplit(url).netloc.replace(":", "_")
    return os.path.join(CACHE_DIR, host, h + ".html")


def _wait(host):
    dt = time.time() - _last.get(host, 0)
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last[host] = time.time()


def allowed(url):
    sp = urlsplit(url)
    base = "%s://%s" % (sp.scheme, sp.netloc)
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            _wait(sp.netloc)
            r = _session.get(base + "/robots.txt", timeout=60)
            if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                rp.parse(r.text.splitlines())
            else:
                rp.parse([])
        except requests.RequestException:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def fetch(url, log=None):
    """キャッシュがあれば読む。無ければ robots を確認して取得し保存。bytes を返す（失敗は None）。"""
    p = cache_path(url)
    if os.path.exists(p) and not REFRESH:
        with open(p, "rb") as f:
            return f.read()
    if not allowed(url):
        if log:
            log("robots disallow: %s" % url)
        return None
    host = urlsplit(url).netloc
    for i in range(5):
        _wait(host)
        try:
            r = _session.get(url, timeout=60)
        except requests.RequestException as e:
            if log:
                log("error %s: %s" % (url, e))
            time.sleep(2 ** i)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if log:
                log("HTTP %s retry %d %s" % (r.status_code, i + 1, url))
            time.sleep(2 ** (i + 1))
            continue
        if r.status_code != 200:
            if log:
                log("HTTP %s %s" % (r.status_code, url))
            return None
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(r.content)
        return r.content
    return None


def soup_of(url):
    b = fetch(url, log=log)
    return BeautifulSoup(b, "html.parser") if b else None


# ---------------------------------------------------------------- 共通の整形
TARGET_PREFS = ("青森県", "秋田県")
BBOX = (38.8, 41.7, 139.4, 141.9)  # 青森・秋田の範囲（lat_min, lat_max, lon_min, lon_max）
EXCLUDE_LINK_HOSTS = ("tsurip.com", "magurop.com", "sanook-fishing.com", "fiship.jp", "a8.net", "amazon.co.jp",
                      "amzn.to", "moshimo.com", "valuecommerce.com", "afi-b.com", "pay.line.me", "paypay.ne.jp",
                      "timetreeapp.com", "1091.co.jp", "tsurimaru.jp", "yugyosen.com", "yugyosen-navi.com",
                      "fishing-station.jp", "tsurisoku.com", "blogs.yahoo.co.jp", "form1.fc2.com", "wp.me",
                      "lg.jp", "go.jp", "bokun.io")
LINK_SNS_HOSTS = SNS_HOSTS + ("lit.link", "linktr.ee", "profile.ameba.jp")
FB_PERSONAL = re.compile(r"facebook\.com/(people/|profile\.php|[a-z]+\.[a-z]+(\.\d+)?/?$)", re.I)
PORT_WORD = re.compile(r"(港|マリーナ|ボートパーク|係留|船溜|船留|桟橋|バース|岸壁|漁協|運河|ポンツーン)")


def one_line(s):
    """住所・本文用: NFKC、空白をまとめ、日本語どうしの間の空白は詰める。"""
    s = nfkc(s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", s)
    return s.strip()


def clean_name(s):
    """船名用: NFKC と空白の整理だけ（日本語の間の空白は残す）。"""
    return re.sub(r"\s+", " ", nfkc(s)).strip()


def lines_of(node):
    return [x.strip() for x in node.get_text("\n").split("\n") if x.strip()]


def real_tel(s):
    d = norm_tel(s)
    if not d or re.fullmatch(r"0+", d):
        return None
    if re.fullmatch(r"0[789]0\d{8}", d):
        return "%s-%s-%s" % (d[:3], d[3:7], d[7:])
    return tel_display(s)


def city_of(address):
    a = nfkc(address or "")
    p = to_pref(a)
    if not p:
        return None
    rest = a[len(p):] if a.startswith(p) else a
    m = re.match(r"(.+?郡.+?[町村]|.+?市|.+?[町村])", rest)
    return m.group(1) if m else None


def classify_links(urls):
    """外部リンクを公式サイト候補と SNS に分ける。"""
    web, sns = [], []
    for u in urls:
        if not u:
            continue
        if u.startswith("//"):
            u = "https:" + u
        u = clean_url(u)
        if not u:
            continue
        h = host_of(u)
        if not h or host_in(h, EXCLUDE_LINK_HOSTS) or host_in(h, NOT_OFFICIAL_HOSTS):
            continue
        if host_in(h, LINK_SNS_HOSTS) or is_sns_url(u):
            if FB_PERSONAL.search(u) or "/groups/" in u:
                continue
            if u not in sns:
                sns.append(u)
        elif u not in web:
            web.append(u)
    own = [u for u in web if not host_in(host_of(u), SHARED_HOSTS)]  # 独自ドメインを優先、無ければブログ等
    return (own + [u for u in web if u not in own]), sns


KANA_TAIL = re.compile(r"\s*[（(]([ぁ-んァ-ヶー・ ]+)[)）]\s*$")


def split_kana(name):
    m = KANA_TAIL.search(name)
    if not m or not name[:m.start()].strip():
        return name, None
    kana = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in m.group(1).replace(" ", ""))
    return name[:m.start()].strip(), kana


def split_list(s, seps="、,・/／"):
    s = nfkc(s)
    parts = re.split("[%s]" % re.escape(seps), s)
    out = []
    for p in parts:
        p = re.sub(r"(など|等|ほか)$", "", p.strip()).strip()
        if p and p not in out:
            out.append(p)
    return out


def clean_port(p):
    p = re.sub(r"\s*[(（][^()（）]*[)）]\s*$", "", nfkc(p)).strip()
    return p or None


def hhmm(h, m):
    return "%02d:%02d" % (int(h), int(m or 0))


TIME_RANGE = re.compile(r"(?:AM|PM|午前|午後)?\s*(\d{1,2})\s*[:：時]\s*(\d{2})?\s*分?\s*[〜～~ー－\-]\s*"
                        r"(?:AM|PM|午前|午後)?\s*(\d{1,2})\s*[:：時]\s*(\d{2})?")


def price_of(text, kind):
    t = nfkc(text)
    if kind == "仕立" or ("隻" in t and "人" not in t):
        return None
    m = re.search(r"([\d,]{4,})\s*円\s*(?:\(税込\))?\s*/\s*(?:1\s*人|人|1\s*名)", t)
    if not m:
        m = re.search(r"(?:1人|一人|1名|おひとり様?)\s*([\d,]{4,})\s*円", t)
    if not m:
        return None
    v = int(m.group(1).replace(",", ""))
    return v if 1000 <= v <= 100000 else None


def plan(name, kind, text, url, targets=None, depart=None, ret=None):
    t = one_line(text)
    if len(t) > 240:
        t = t[:239] + "…"
    return {"name": name, "kind": kind, "targets": targets or [], "price": price_of(text, kind),
            "price_text": t, "depart": depart, "return": ret, "meet": "", "season": "", "days": "",
            "includes": "", "url": url}


def kind_of(label):
    if "乗合" in label or "乗り合" in label or "相乗" in label:
        return "乗合"
    if "仕立" in label or "貸切" in label or "チャーター" in label:
        return "仕立"
    if "渡船" in label or "磯釣" in label:
        return "渡船"
    return ""


def record(**kw):
    rec = {"src": "gap0205", "src_id": None, "src_url": None, "name": None, "kana": None, "pref": None,
           "city": None, "address": None, "port": None, "lat": None, "lon": None, "tel": None, "website": None,
           "sns": [], "types": [], "targets": [], "methods": [], "holidays": "", "facilities": [],
           "capacity": None, "access": "", "description": "", "plans": [], "schedule_text": "",
           "fetched": FETCHED}
    rec.update(kw)
    if re.search(r"渡船|瀬渡", rec["name"] or "") and "渡船" not in rec["types"]:
        rec["types"] = rec["types"] + ["渡船"]
    return rec


# ---------------------------------------------------------------- つりっぷ / まぐろっぷ（WordPress SWELL）
NAME_PREFIX = re.compile(r"^(青森県今別町津軽海峡前奥平部漁港|マグロキャスティング日本海秋田沖マグロ釣り船|青森の釣り船|青森のつり船|"
                         r"青森釣り船|大間釣り船|青森小泊港|小泊マグロ遊漁船|青森\s*釣船|遊漁船\s*久六島)\s*")


def wp_category_posts(first_url, host):
    s = soup_of(first_url)
    if not s:
        return []
    pages = [first_url]
    nums = [int(m) for a in s.find_all("a", href=True)
            for m in re.findall(re.escape(first_url) + r"page/(\d+)/", a["href"])]
    for n in range(2, (max(nums) if nums else 1) + 1):
        pages.append(first_url + "page/%d/" % n)
    posts = []
    for i, pu in enumerate(pages):
        ps = s if i == 0 else soup_of(pu)
        if not ps:
            continue
        for a in ps.select("main .p-postList a[href], .l-mainContent .p-postList a[href]"):
            h = a["href"]
            if h.startswith("https://%s/" % host) and "/category/" not in h and "/page/" not in h and h not in posts:
                posts.append(h)
    log("%s: %d pages, %d posts" % (first_url, len(pages), len(posts)))
    return posts


def parse_wp_post(url, site, list_pref):
    s = soup_of(url)
    if not s:
        return None
    pc = s.select_one(".post_content")
    h1 = s.select_one("h1.c-postTitle__ttl") or s.select_one("h1")
    if not pc or not h1:
        return None
    for bad in pc.select(".p-adBox, script, style, noscript"):
        bad.decompose()
    name = clean_name(h1.get_text())
    for _ in range(2):
        name = NAME_PREFIX.sub("", name).strip()
    name, kana = split_kana(name)

    # 日付（投稿・更新・表の「最終更新日」「現在の情報です」）
    dates = []
    for t in s.select(".p-articleHead time, .p-articleMetas.-top time"):
        if t.get("datetime"):
            dates.append(t["datetime"][:10])
    for fc in pc.select("figcaption"):
        for m in re.finditer(r"(\d{4})/(\d{2})/(\d{2})", fc.get_text()):
            dates.append("%s-%s-%s" % m.groups())
    last = max(dates) if dates else None

    meta = s.select_one(".p-articleMetas.-top") or s.select_one(".p-articleMetas")
    cats = [clean_name(a.get_text()) for a in meta.select(".c-categoryList__link")] if meta else []

    # 電話（ダミーの 000-0000-0000 は除く）
    tels = []
    for a in pc.select('a[href^="tel:"]'):
        t = real_tel(unquote(a["href"][4:]))
        if t and t not in tels:
            tels.append(t)
    if not tels:
        for ln in lines_of(pc):
            if ln.startswith("電話"):
                t = real_tel(ln)
                if t and t not in tels:
                    tels.append(t)

    text = "\n".join(lines_of(pc))
    reg = re.search(r"遊漁船登録\s*(\S{1,6}?第\s*[0-9]+\s*号)", nfkc(text))

    # 表（魚・釣り方・乗合・仕立・トイレ…）
    targets, methods, types, facilities, plans = [], [], [], [], []
    for tr in pc.select("table tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        label = one_line(cells[0].get_text(" "))
        vlines = [x for x in lines_of(cells[1]) if not x.startswith("※料金は")]
        value = " / ".join(vlines)
        if not value:
            continue
        if label in ("魚", "釣り物", "対象魚", "魚種"):
            targets = split_list(value.replace(" / ", "、"))
        elif "釣り方" in label:
            methods = split_list(value.replace(" / ", "、"))
        elif kind_of(label):
            k = kind_of(label)
            if k not in types:
                types.append(k)
            tm = TIME_RANGE.search(nfkc(value))
            plans.append(plan(label, k, value, url,
                              depart=hhmm(tm.group(1), tm.group(2)) if tm else None,
                              ret=hhmm(tm.group(3), tm.group(4)) if tm else None))
        elif "トイレ" in label and not re.search(r"(なし|無し|無)$", value):
            facilities.append("トイレ")

    # 出港場所（見出しの後ろの段落: 港名 / 〒住所 の繰り返し）
    ports, addrs = [], []
    for hd in pc.find_all(["h2", "h3", "h4"]):
        if "出港場所" not in hd.get_text():
            continue
        for sib in hd.find_next_siblings():
            if sib.name in ("h2", "h3", "h4"):
                break
            if sib.name not in ("p", "div"):
                continue
            prev = None
            for ln in lines_of(sib):
                ln = nfkc(ln)
                if ln.startswith("※"):
                    continue
                if ln in ("漁港", "港", "マリーナ") and prev and not PORT_WORD.search(prev):
                    ln = prev + ln  # 「象潟」「漁港」と行が割れている
                prev = ln
                if ln.startswith("〒") or to_pref(ln):
                    a = one_line(re.sub(r"^〒?\s*\d{3}-?\d{4}\s*", "", ln))
                    if to_pref(a) and a not in addrs:
                        addrs.append(a)
                elif PORT_WORD.search(ln) and len(ln) <= 40 and not ln.startswith(("(", "（")) and ln not in ports:
                    ports.append(ln)
        break
    address = addrs[0] if addrs else None
    port = clean_port(ports[0]) if ports else None
    cat_ports = [c.split(" ", 1)[1] for c in cats if " " in c and to_pref(c)]
    if not port and cat_ports:
        port = clean_port(cat_ports[0])

    pref = to_pref(address) if address else None
    if pref and pref not in TARGET_PREFS:
        log("skip (pref %s): %s" % (pref, url))
        return None
    if not pref:
        cp = [to_pref(c) for c in cats if to_pref(c) in TARGET_PREFS]
        pref = cp[0] if cp else list_pref
    cat_city = None
    for c in cats:
        if to_pref(c) == pref:
            cat_city = city_of(c.split(" ")[0])
            if cat_city:
                break
    city = city_of(address) or cat_city
    if address and not city_of(address) and cat_city:
        # 「秋田県本荘マリーナ」のように市町村が無い → 港名として使い、住所は県＋市町村にする
        if not port:
            port = clean_port(address[len(pref):])
        address = pref + cat_city
    if not address and city:
        address = pref + city

    # 地図（Google マップ埋め込み pb の中心座標。範囲外は捨てる）
    lat = lon = None
    for fr in pc.select("iframe"):
        m = re.search(r"!2d([\d.]+)!3d([\d.]+)", fr.get("data-src") or fr.get("src") or "")
        if m:
            la, lo = float(m.group(2)), float(m.group(1))
            if BBOX[0] <= la <= BBOX[1] and BBOX[2] <= lo <= BBOX[3]:
                lat, lon = round(la, 6), round(lo, 6)
            break

    links = [a["href"] for a in pc.select("a[href]") if not a["href"].startswith(("tel:", "mailto:", "#"))]
    web, sns = classify_links(links)

    desc = []
    if reg:
        desc.append("遊漁船登録 " + reg.group(1).replace(" ", ""))
    if len(tels) > 1:
        desc.append("電話(ほか) " + "、".join(tels[1:]))
    access = "出港場所: " + "／".join(ports) if len(ports) > 1 else ""
    slug = unquote(urlsplit(url).path.strip("/"))
    rec = record(src_id="%s:%s" % (site, slug), src_url=url, name=name, kana=kana, pref=pref, city=city,
                 address=address, port=port, lat=lat, lon=lon, tel=tels[0] if tels else None,
                 website=web[0] if web else None, sns=sns, types=types, targets=targets, methods=methods,
                 facilities=facilities, access=access, description="。".join(desc), plans=plans)
    if last and last < STALE_BEFORE:
        rec["stale"] = True
    rec["_last"] = last
    return rec


# ---------------------------------------------------------------- SANOOK FISHING
SANOOK_ARTICLES = [
    ("青森県", "https://sanook-fishing.com/boat-aomori-kuromaguro/"),
    ("青森県", "https://sanook-fishing.com/boat-aomori-madai/"),
    ("青森県", "https://sanook-fishing.com/boat-aomori-buri/"),
    ("秋田県", "https://sanook-fishing.com/boat-akita-kuromaguro/"),
    ("秋田県", "https://sanook-fishing.com/boat-akita-madai/"),
    ("秋田県", "https://sanook-fishing.com/boat-akita-buri/"),
]
TYPE_MAP = [("乗合", "乗合"), ("仕立", "仕立"), ("チャーター", "仕立"), ("渡船", "渡船"), ("瀬渡", "渡船")]


def sanook_name(raw):
    name = clean_name(raw)
    name = re.sub(r"[（(][^）)]*(就航|\d{4}年)[^）)]*[)）]", "", name)
    name = re.sub(r"※.*$", "", name)
    name = re.sub(r"(?<=\S)[（(][ぁ-ん]+[)）](?=\S)", "", name)  # 昭(しょう)丸 → 昭丸
    return split_kana(name.strip())


def parse_sanook():
    rows = []
    for pref, url in SANOOK_ARTICLES:
        s = soup_of(url)
        if not s:
            continue
        date = None
        m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", s.get_text(" "))
        if m:
            date = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
        n = 0
        for tb in s.find_all("table"):
            trs = tb.find_all("tr")
            if len(trs) < 4:
                continue
            first = trs[0].find_all("td")
            if len(first) < 2:
                continue
            raw_name = clean_name(first[0].get_text())
            if not raw_name:
                continue
            href = [a["href"] for a in first[0].find_all("a", href=True)]
            fields = {}
            for tr in trs[1:]:
                tds = tr.find_all("td")
                for k in range(0, len(tds) - 1, 2):
                    fields[one_line(tds[k].get_text(" ")).replace(" ", "")] = tds[k + 1]
            if "住所" not in fields:
                continue
            n += 1
            name, kana = sanook_name(raw_name)
            rows.append({"pref": pref, "url": url, "date": date, "name": name, "kana": kana,
                         "href": href, "tel": real_tel(first[-1].get_text(" ")), "fields": fields})
        log("sanook %s: %d tables (%s)" % (url, n, date))

    groups = collections.OrderedDict()
    for r in rows:
        groups.setdefault((r["pref"], norm_name(r["name"])), []).append(r)
    # 同じ電話番号が別の船名でより多く使われている場合、その番号は採らない（記事中の転記ミス対策）
    tel_names = collections.defaultdict(collections.Counter)
    for key, rs in groups.items():
        for r in rs:
            if r["tel"]:
                tel_names[norm_tel(r["tel"])][key] += 1

    out = []
    for key, rs in groups.items():
        votes = collections.Counter()
        for r in rs:
            if r["tel"]:
                owners = tel_names[norm_tel(r["tel"])]
                if owners[key] >= max(owners.values()):
                    votes[r["tel"]] += 1
        tel = votes.most_common(1)[0][0] if votes else None
        r0 = next((r for r in rs if r["tel"] == tel), rs[0])  # 採った電話番号が載っている記事を出典にする
        f0 = r0["fields"]

        toks = [nfkc(t) for t in re.split(r"[\u3000 ]+", f0["住所"].get_text(" ").strip()) if t.strip()]
        address, port = (toks[0] if toks else None), None
        if len(toks) > 1:
            idx = [i for i in range(1, len(toks)) if PORT_WORD.search(toks[i]) and not re.search(r"または|から", toks[i])]
            if idx:
                port = clean_port(toks[idx[-1]])
                address += "".join(toks[1:idx[-1]])
            else:
                address += "".join(toks[1:])
        if address:
            address = one_line(address)
            if not to_pref(address):
                address = key[0] + address
        pref = to_pref(address) or key[0]

        types, targets, methods, notes = [], [], [], []
        for r in rs:
            f = r["fields"]
            if "種別" in f:
                st = one_line(f["種別"].get_text(""))
                for word, t in TYPE_MAP:
                    if word in st and t not in types:
                        types.append(t)
                extra = re.findall(r"(釣船あっせん|[^()（）]*のみ)", st)
                for x in extra:
                    if x and x not in notes:
                        notes.append(x)
            if "対象魚" in f:
                for x in split_list(one_line(f["対象魚"].get_text("")), "・、,"):
                    if x not in targets:
                        targets.append(x)
            if "釣り方" in f:
                for x in split_list(one_line(f["釣り方"].get_text("")), "・、,"):
                    if x not in methods:
                        methods.append(x)
        note = one_line(f0["備考"].get_text("")) if "備考" in f0 else ""
        holidays, access, rest = "", [], list(notes)
        for tok in re.split(r"[・]|\s*※", note):
            tok = tok.strip()
            if not tok:
                continue
            if not holidays and re.search(r"(定休|休)$", tok) and len(tok) <= 12:
                holidays = tok
            elif re.search(r"(駅|空港|IC|インター)(から|より)", tok):
                access.append(tok)
            else:
                rest.append(tok)
        hrefs = []
        for r in rs:
            hrefs += r["href"]
        web, sns = classify_links(hrefs)
        desc = "、".join(rest)
        if len(desc) > 100:
            desc = desc[:99] + "…"
        kana = next((r["kana"] for r in rs if r["kana"]), None)
        pshort = "aomori" if pref == "青森県" else "akita"
        rec = record(src_id="sanook-%s:%s" % (pshort, key[1] or r0["name"]), src_url=r0["url"], name=r0["name"],
                     kana=kana, pref=pref, city=city_of(address), address=address, port=port, tel=tel,
                     website=web[0] if web else None, sns=sns, types=types, targets=targets, methods=methods,
                     holidays=holidays, access="、".join(access), description=desc)
        rec["_last"] = r0["date"]
        out.append(rec)
    return out


# ---------------------------------------------------------------- Fiship.jp
FISHIP_LISTS = [("青森県", "https://fiship.jp/aomori"), ("秋田県", "https://fiship.jp/akita")]
FISHIP_LABELS = ("電話", "名称", "代表者", "所在地", "最寄IC", "駐車場", "公式HP", "ジャンル", "プラン名")


def parse_fiship():
    out = []
    for pref, lurl in FISHIP_LISTS:
        s = soup_of(lurl)
        if not s:
            continue
        ships = []
        for a in s.find_all("a", href=True):
            if re.fullmatch(r"/ship/(\d+)/", a["href"]):
                u = urljoin(lurl, a["href"])
                if u not in ships:
                    ships.append(u)
        log("fiship %s: %d ships" % (lurl, len(ships)))
        for u in ships:
            ps = soup_of(u)
            if not ps:
                continue
            for t in ps(["script", "style"]):
                t.decompose()
            ttl = clean_name(ps.title.get_text() if ps.title else "")
            lines = [x.strip() for x in ps.get_text("\n").split("\n") if x.strip()]
            f = {}
            for i, ln in enumerate(lines):
                if ln in FISHIP_LABELS and ln not in f:
                    vals = []
                    for v in lines[i + 1:]:
                        if v in FISHIP_LABELS:
                            break
                        vals.append(v)
                        if ln not in ("ジャンル",):
                            break
                    f[ln] = vals
            name = clean_name((f.get("名称") or [""])[0])  # 「代表者」は読まない
            if not name:
                continue
            m = re.match(r"^(.+?[都道府県])(.+)の釣り船「", ttl)
            port = m.group(2) if m else None
            loc = one_line(" ".join(f.get("所在地") or []))
            address = one_line(loc[:-len(port)]) if port and loc.endswith(port) else loc
            genre = " ".join(f.get("ジャンル") or [])
            types = []
            for word, t in (("乗り合い", "乗合"), ("チャーター", "仕立"), ("仕立", "仕立"), ("渡船", "渡船")):
                if word in genre and t not in types:
                    types.append(t)
            methods = [g.strip() for g in re.split(r"\s*,\s*", genre)
                       if g.strip() and not re.search(r"乗り合い|チャーター|仕立|渡船", g)]
            # プラン表: 「概要（料金など）」の後ろに 名前/時間/概要 の3行ずつ、船名の行で終わる
            plans = []
            if "概要（料金など）" in lines:
                k = lines.index("概要（料金など）") + 1
                while k + 2 < len(lines):
                    # 表の終わり（船名の行・「○○の最新釣果」・フッター）で止める
                    if any(x == name or x.endswith("の最新釣果") or x.startswith("©") for x in lines[k:k + 3]):
                        break
                    pname, ptime, psum = lines[k], lines[k + 1], lines[k + 2]
                    kind = kind_of(pname) or kind_of(psum)
                    dep = ret = None
                    tm = TIME_RANGE.search(nfkc(ptime).replace("AM", "").replace("PM", ""))
                    if tm:
                        h1, m1, h2, m2 = tm.groups()
                        if "PM" in ptime.split("～")[-1] and int(h2) < 12:
                            h2 = str(int(h2) + 12)
                        if int(h1) <= 23 and int(h2) <= 23:
                            dep, ret = hhmm(h1, m1), hhmm(h2, m2)
                    p = plan(pname, kind, psum, u, depart=dep, ret=ret)
                    if ptime != "要確認":
                        p["price_text"] = one_line("%s（%s）" % (p["price_text"], ptime))
                    plans.append(p)
                    k += 3
            news = [datetime.date(2000 + int(a), int(b), int(c)).isoformat()
                    for a, b, c in re.findall(r"(\d{2})年(\d{2})月(\d{2})日", ps.get_text(" "))]
            last = max(news) if news else None
            hp = (f.get("公式HP") or [None])[0]
            web, sns = classify_links([hp])
            acc = []
            if f.get("最寄IC"):
                acc.append("最寄IC: " + one_line(f["最寄IC"][0]))
            if f.get("駐車場"):
                acc.append(one_line(f["駐車場"][0]))
            rec = record(src_id="fiship:%s" % re.search(r"/ship/(\d+)/", u).group(1), src_url=u, name=name,
                         pref=to_pref(address) or pref, city=city_of(address), address=address, port=port,
                         tel=real_tel(" ".join(f.get("電話") or [])), website=web[0] if web else None, sns=sns,
                         types=types, methods=methods, access="、".join(acc), plans=plans)
            if not last or last < STALE_BEFORE:
                rec["stale"] = True
            rec["_last"] = last
            out.append(rec)
    return out


# ---------------------------------------------------------------- 北の宿（大間町）
KITANOYADO = "http://kitanoyado.shichihuku.com/fune.html"


def parse_kitanoyado():
    s = soup_of(KITANOYADO)
    if not s:
        return []
    t = nfkc(s.get_text("\n"))
    m = re.search(r"(第\s*\d+\s*\S+?丸)", t)
    if not m:
        return []
    tel = re.search(r"TEL\s*:\s*([\d-]+)", t)
    mob = re.search(r"携帯\s*:\s*([\d-]+)", t)
    plans = []
    for pm in re.finditer(r"(\d{1,2}):(\d{2})から\s*(\d+)時間で\s*([\d,]+)円", t):
        h, mi, dur, yen = pm.groups()
        plans.append(plan("%s:%s から %s時間" % (h, mi, dur), "", "%s時間で%s円" % (dur, yen), KITANOYADO,
                          depart=hhmm(h, mi), ret=hhmm((int(h) + int(dur)) % 24, mi)))
    nm = re.search(r"夜一晩\s*\((\d{1,2}):(\d{2})\s*[〜～~]\s*(\d{1,2}):(\d{2})\)\s*([\d,]+)円", t)
    if nm:
        plans.append(plan("夜一晩", "", "夜一晩 %s円" % nm.group(5), KITANOYADO,
                          depart=hhmm(nm.group(1), nm.group(2)), ret=hhmm(nm.group(3), nm.group(4))))
    for p in plans:
        p["price"] = None  # 1人か1隻か書かれていない
    targets = []
    for a, b in re.findall(r"(マグロ、ブリ、ヒラメ、メバル)|春の魚は([^\n]+)", t):
        for x in split_list((a or b).replace("時々", ""), "、"):
            if x not in targets:
                targets.append(x)
    rec = record(src_id="kitanoyado:%s" % m.group(1).replace(" ", ""), src_url=KITANOYADO,
                 name=m.group(1).replace(" ", ""), pref="青森県", city="下北郡大間町", address="青森県下北郡大間町",
                 tel=real_tel(tel.group(1)) if tel else None, targets=targets, methods=["一本釣り"],
                 description=("携帯 %s。大間町の民宿「北の宿」の釣り船ページ" % real_tel(mob.group(1))) if mob else "",
                 plans=plans)
    rec["stale"] = True  # 更新日の記載が無い旧式ページ
    rec["_last"] = None
    return [rec]


# ---------------------------------------------------------------- main
def main():
    global REFRESH, _logf
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--log", default=LOG_DEFAULT)
    ap.add_argument("--only", default="tsurip,magurop,sanook,fiship,kitanoyado")
    args = ap.parse_args()
    REFRESH = args.refresh
    os.makedirs(os.path.dirname(args.log), exist_ok=True)
    _logf = open(args.log, "a", encoding="utf-8")
    only = set(args.only.split(","))
    log("start gap0205 only=%s" % sorted(only))

    recs = []
    wp = [("tsurip", "tsurip.com", "青森県", "https://tsurip.com/category/touhoku/aomoriken/"),
          ("tsurip", "tsurip.com", "秋田県", "https://tsurip.com/category/touhoku/akitaken/"),
          ("magurop", "magurop.com", "青森県", "https://magurop.com/category/aomoriken/"),
          ("magurop", "magurop.com", "秋田県", "https://magurop.com/category/akitaken/")]
    for site, host, pref, cat in wp:
        if site not in only:
            continue
        posts = wp_category_posts(cat, host)
        seen = {r["src_url"] for r in recs}
        for i, pu in enumerate(posts, 1):
            if pu in seen:
                continue
            r = parse_wp_post(pu, site, pref)
            if r:
                recs.append(r)
            if i % 10 == 0 or i == len(posts):
                log("%s %s: %d/%d" % (site, pref, i, len(posts)))
        save(recs, args.out)
    if "sanook" in only:
        recs += parse_sanook()
    if "fiship" in only:
        recs += parse_fiship()
    if "kitanoyado" in only:
        recs += parse_kitanoyado()
    save(recs, args.out)
    c = collections.Counter((r["src_id"].split(":")[0], r["pref"]) for r in recs)
    log("done %d records -> %s %s" % (len(recs), args.out, dict(c)))


def save(recs, path):
    out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in recs]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "get":
        for u in sys.argv[2:]:
            b = fetch(u, log=lambda m: print(m, file=sys.stderr))
            print(len(b) if b else None, cache_path(u), u)
    else:
        main()
