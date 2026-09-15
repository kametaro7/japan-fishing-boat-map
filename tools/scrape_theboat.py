#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE BOAT (https://theboat.jp/) crawler for 全国釣り船マップ.

Enumerates /boats/<slug> from sitemap-boats.xml, fetches each boat page
(HTML only; /api/ is Disallowed in robots.txt and never touched), plus the
public /boats list pages (for the site's own prefecture label and service kind),
and normalizes into the SPEC "掲載サイトレコード" array.

THE BOAT has no plans / prices (it tells users to check the official site),
so plans is always [].

Usage:
  python3 tools/scrape_theboat.py                    # all boats
  python3 tools/scrape_theboat.py --limit 50
  python3 tools/scrape_theboat.py --ids amc-meister,kurokawamaru-hak --out work/sources/theboat.sample.json
  python3 tools/scrape_theboat.py --refresh          # ignore cache and re-fetch
  python3 tools/scrape_theboat.py --offline          # cache only, no network
"""
from __future__ import print_function

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

BASE = "https://theboat.jp"
SRC = "theboat"
FETCHED = "2026-09-15"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", SRC)
LOG_PATH = os.path.join(ROOT, "work", "logs", SRC + ".log")
DEFAULT_OUT = os.path.join(ROOT, "work", "sources", SRC + ".json")
EXTRA_OUT = os.path.join(ROOT, "work", "tmp", SRC, "theboat_extra.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # seconds between network requests (SPEC: >= 0.8)
TIMEOUT = 60
MAX_RETRY = 5

PREFS = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
PREF_RE = re.compile("^(" + "|".join(PREFS) + ")")
PREF_PAREN_RE = re.compile("（(" + "|".join(PREFS) + ")）")

ROMAJI_PREF = {
    "hokkaido": "北海道", "aomori": "青森県", "iwate": "岩手県", "miyagi": "宮城県", "akita": "秋田県",
    "yamagata": "山形県", "fukushima": "福島県", "ibaraki": "茨城県", "tochigi": "栃木県", "gunma": "群馬県",
    "saitama": "埼玉県", "chiba": "千葉県", "tokyo": "東京都", "kanagawa": "神奈川県", "niigata": "新潟県",
    "toyama": "富山県", "ishikawa": "石川県", "fukui": "福井県", "yamanashi": "山梨県", "nagano": "長野県",
    "gifu": "岐阜県", "shizuoka": "静岡県", "aichi": "愛知県", "mie": "三重県", "shiga": "滋賀県",
    "kyoto": "京都府", "osaka": "大阪府", "hyogo": "兵庫県", "nara": "奈良県", "wakayama": "和歌山県",
    "tottori": "鳥取県", "shimane": "島根県", "okayama": "岡山県", "hiroshima": "広島県",
    "yamaguchi": "山口県", "tokushima": "徳島県", "kagawa": "香川県", "ehime": "愛媛県", "kochi": "高知県",
    "fukuoka": "福岡県", "saga": "佐賀県", "nagasaki": "長崎県", "kumamoto": "熊本県", "oita": "大分県",
    "miyazaki": "宮崎県", "kagoshima": "鹿児島県", "okinawa": "沖縄県",
}
# listed-<pref>-<hash> / directory-<pref>-<hash>: the site grouped these by prefecture.
SLUG_PREF_RE = re.compile(r"^(?:listed|directory)-([a-z]+)-[0-9a-f]{6,}$")

# Area / sub-area labels that lie entirely inside one prefecture (exact match only).
# Multi-prefecture seas (東京湾, 相模湾, 伊勢湾, 瀬戸内海, 玄界灘, 山陰, 若狭湾 ...) are deliberately absent.
AREA_PREF = {
    "伊豆半島": "静岡県", "駿河湾": "静岡県", "東伊豆": "静岡県", "西伊豆": "静岡県", "南伊豆": "静岡県",
    "南伊豆・下田": "静岡県", "沼津・戸田": "静岡県", "清水": "静岡県", "焼津": "静岡県", "御前崎": "静岡県",
    "三浦半島": "神奈川県", "湘南": "神奈川県", "横須賀・観音崎": "神奈川県", "金沢八景": "神奈川県",
    "横浜・本牧": "神奈川県", "小田原・真鶴": "神奈川県", "川崎沖": "神奈川県",
    "外房": "千葉県", "内房": "千葉県", "南房総": "千葉県", "勝浦・鴨川": "千葉県", "館山・南房総": "千葉県",
    "銚子": "千葉県", "銚子・飯岡": "千葉県", "木更津・富津": "千葉県", "船橋・千葉": "千葉県",
    "波崎・神栖": "茨城県", "鹿島旧港": "茨城県", "鹿島新港": "茨城県", "大洗": "茨城県", "那珂湊": "茨城県",
    "伊豆諸島": "東京都", "深川・有明": "東京都", "羽田・城南": "東京都", "大井沖": "東京都",
    "知多半島": "愛知県", "渥美・伊良湖": "愛知県", "鳥羽・伊雑": "三重県", "志摩・七里御瀬": "三重県",
    "明石・神戸": "兵庫県", "小豆島・高松": "香川県", "広島・呉": "広島県",
    "博多・糸島": "福岡県", "宗像・神湊": "福岡県", "唐津・呼子": "佐賀県",
    "新潟・上越": "新潟県", "富山・氷見": "富山県", "能登・金沢": "石川県", "福井": "福井県",
    "小樽・積丹": "北海道", "苫小牧・室蘭": "北海道", "函館・道南": "北海道",
    "茨城南": "茨城県", "茨城北": "茨城県", "伊豆南": "静岡県", "伊豆東": "静岡県", "伊豆西": "静岡県",
    "東伊豆（熱海・伊東・宇佐美・稲取）": "静岡県",
}
# Labels the site shows in the "port" slot that are really sea areas / districts, not ports.
SUBAREA_LABELS = set([
    "東京湾", "相模湾", "駿河湾", "外房", "内房", "南房総", "湘南", "三浦半島", "伊豆半島", "伊豆南", "伊豆東",
    "伊豆西", "茨城南", "茨城北", "勝浦・鴨川", "横須賀・観音崎", "小田原・真鶴", "沼津・戸田", "南伊豆・下田",
    "波崎・神栖", "東伊豆（熱海・伊東・宇佐美・稲取）",
])
TEMPLATE_DESC_RE = re.compile(
    r"(の船宿として掲載されています|を拠点とする海の遊漁船・船宿です。公式の釣果情報"
    r"|を出港地として案内する船宿です。出船日や乗船条件は|の船宿情報。.{0,30}の釣果や連絡先を確認できます)")
# "三浦半島（東）" style labels: a district plus a direction
DIR_SUFFIX_RE = re.compile(r"（[東西南北中]{1,2}部?）$")
CATCH_PAGE_RE = re.compile(r"^(https?://[^/]+/)(?:category/Choka/?|catch\.html?|choka\.html?|chouka\.html?)$", re.I)

SERVICE_KINDS = [("boat", None), ("rental_boat", "レンタルボート"), ("ferry", "渡船"),
                 ("raft", "筏・カセ"), ("inland", "陸っぱり・川")]

# Hosts that are listing/aggregator/manufacturer sites, never the boat's own website.
NON_OFFICIAL_HOSTS = [
    "theboat.jp", "shimano.com", "daiwa.com", "google.com", "google.co.jp", "goo.gl", "g.page",
    "chowari.jp", "gyo.ne.jp", "fishing-v.jp", "funayado.info", "funayado.com", "tsurinews.jp",
    "anglers.jp", "jalan.net", "asoview.com", "activityjapan.com", "hotpepper.jp", "travel.rakuten.co.jp",
    "item.rakuten.co.jp", "search.rakuten.co.jp", "search.yahoo.co.jp", "map.yahoo.co.jp", "loco.yahoo.co.jp",
    "shopping.yahoo.co.jp", "tripadvisor.jp", "tripadvisor.com", "tsuri-dougu.com", "fishing-japan.jp",
    "fish-master.jp", "tsurihack.com", "turi-ba.net", "tsuriho.com", "sotoasobi.net",
    "thesand.jp", "navitime.co.jp", "mapion.co.jp", "itp.ne.jp", "ekiten.jp",
]
SNS_HOSTS = [
    "instagram.com", "facebook.com", "fb.com", "twitter.com", "x.com", "youtube.com", "youtu.be",
    "tiktok.com", "line.me", "lin.ee", "threads.net",
]

_last_request = [0.0]
_stats = {"net": 0, "cache": 0, "errors": 0}


# ---------------------------------------------------------------- utilities
def log(msg):
    line = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


class NotFound(Exception):
    pass


def fetch(session, url, refresh=False, offline=False):
    """Return text of url, using the on-disk cache. Serial, rate-limited, with backoff."""
    path = cache_path(url)
    if not refresh and os.path.exists(path) and os.path.getsize(path) > 0:
        _stats["cache"] += 1
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    if offline:
        raise NotFound("not cached (offline): " + url)
    delay = 5.0
    for attempt in range(MAX_RETRY + 1):
        wait = MIN_INTERVAL - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException as e:
            if attempt >= MAX_RETRY:
                raise
            log("WARN network error %s (%s); retry in %.0fs" % (url, e.__class__.__name__, delay))
            time.sleep(delay)
            delay *= 2
            continue
        _stats["net"] += 1
        if r.status_code == 200:
            r.encoding = "utf-8"
            text = r.text
            tmp = path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)
            return text
        if r.status_code in (404, 410):
            raise NotFound("%d %s" % (r.status_code, url))
        if r.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRY:
            ra = r.headers.get("Retry-After")
            w = delay
            if ra and ra.isdigit():
                w = max(delay, float(ra))
            log("WARN HTTP %d %s; backoff %.0fs (attempt %d/%d)" % (r.status_code, url, w, attempt + 1, MAX_RETRY))
            time.sleep(w)
            delay *= 2
            continue
        raise RuntimeError("HTTP %d %s" % (r.status_code, url))
    raise RuntimeError("gave up " + url)


def write_json_atomic(path, data):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def clean(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def host_of(url):
    try:
        h = urlparse(url).netloc.lower()
    except Exception:
        return ""
    h = h.split("@")[-1].split(":")[0]
    return h


def host_matches(h, domains):
    for d in domains:
        if h == d or h.endswith("." + d):
            return True
    return False


# ---------------------------------------------------------------- enumerate
def list_boat_slugs(session, refresh, offline):
    xml = fetch(session, BASE + "/sitemap-boats.xml", refresh=refresh, offline=offline)
    urls = re.findall(r"<loc>\s*(https://theboat\.jp/boats/[^<\s]+)\s*</loc>", xml)
    slugs = []
    seen = set()
    for u in urls:
        slug = unquote(u.rsplit("/boats/", 1)[1]).strip("/")
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)
    return slugs


def list_page_url(kind, page):
    return "%s/boats?area=all&species=&service_kind=%s&q=&page=%d" % (BASE, kind, page)


def parse_list_rows(html):
    s = BeautifulSoup(html, "lxml")
    rows = []
    for a in s.select("a.tb-row"):
        href = a.get("href") or ""
        if not href.startswith("/boats/"):
            continue
        slug = unquote(href.split("/boats/", 1)[1].split("?")[0]).strip("/")
        sub = a.select_one(".tb-row-sub")
        parts = [p.strip() for p in (sub.get_text(" ", strip=True) if sub else "").split("・")]
        rows.append((slug, [p for p in parts if p]))
    m = re.search(r"(\d+)\s*/\s*(\d+)ページ", s.get_text(" ", strip=True))
    pages = int(m.group(2)) if m else 1
    return rows, pages


def crawl_list_pages(session, refresh, offline):
    """slug -> {"list_pref": ..., "kind": ...} from the public /boats list (HTML)."""
    info = {}
    for kind, label in SERVICE_KINDS:
        if kind == "boat":
            k = ""  # all boats; gives the prefecture label shown in the list
        else:
            k = kind
        page, pages = 1, 1
        while page <= pages:
            url = list_page_url(k, page)
            try:
                html = fetch(session, url, refresh=refresh, offline=offline)
            except NotFound as e:
                log("WARN list page missing: %s" % e)
                break
            rows, pages = parse_list_rows(html)
            for slug, parts in rows:
                d = info.setdefault(slug, {"list_pref": None, "kinds": []})
                # list row separator is " ・ " but names like 勝浦・鴨川 also contain ・,
                # so only trust a leading token that is exactly a prefecture name.
                if parts and parts[0] in PREFS and d["list_pref"] is None:
                    d["list_pref"] = parts[0]
                if label and label not in d["kinds"]:
                    d["kinds"].append(label)
            page += 1
        log("list %s: %d pages" % (kind if k else "all", pages))
    return info


# ---------------------------------------------------------------- detail parse
def city_from_address(addr, pref):
    if not addr or not pref or not addr.startswith(pref):
        return None
    rest = addr[len(pref):]
    special = ["四日市市", "廿日市市", "野々市市", "市川市", "市原市", "大町市", "十日町市", "村上市",
               "東村山市", "武蔵村山市", "羽村市", "大村市", "村山市", "町田市", "上市町", "余市郡余市町"]
    for sp in special:
        if rest.startswith(sp):
            return sp
    m = re.match(r"^([^市区町村郡]{1,6}郡[^市区町村]{1,6}[町村])", rest)
    if m:
        return m.group(1)
    if pref == "東京都":
        m = re.match(r"^([^市区町村]{1,5}区)", rest)
        if m:
            return m.group(1)
    m = re.match(r"^([^市区町村]{1,6}市)", rest)
    if m:
        return m.group(1)
    m = re.match(r"^([^市区町村]{1,6}[区町村])", rest)
    if m:
        return m.group(1)
    return None


def norm_tel(t):
    if not t:
        return None
    t = t.replace("tel:", "").strip()
    t = t.translate(str.maketrans("０１２３４５６７８９－ー（）", "0123456789--()"))
    t = re.sub(r"[()\s]", "-", t).strip("-")
    t = re.sub(r"-+", "-", t)
    digits = re.sub(r"\D", "", t)
    if not (10 <= len(digits) <= 11) or not digits.startswith("0"):
        return None
    return t


def get_ld_business(soup):
    for sc in soup.find_all("script", type="application/ld+json"):
        txt = sc.string or sc.get_text() or ""
        try:
            data = json.loads(txt)
        except Exception:
            continue
        graph = data.get("@graph") if isinstance(data, dict) else None
        items = graph if isinstance(graph, list) else [data]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "LocalBusiness":
                return it
    return {}


def facts_dl(soup):
    """#boat-access dl.boat-facts: dt text -> dd element."""
    out = {}
    acc = soup.find(id="boat-access")
    if not acc:
        return out
    for div in acc.select("dl.boat-facts > div"):
        dt, dd = div.find("dt"), div.find("dd")
        if dt and dd:
            out.setdefault(dt.get_text(strip=True), dd)
    return out


def classify_url(url):
    """-> ('website'|'sns'|None, normalized url)"""
    if not url or not re.match(r"^https?://", url):
        return None, None
    h = host_of(url)
    if not h:
        return None, None
    if host_matches(h, SNS_HOSTS):
        return "sns", url
    if host_matches(h, NON_OFFICIAL_HOSTS):
        return None, url
    # the site often links the boat's own catch-report page; keep the same site's top page
    m = CATCH_PAGE_RE.match(url)
    if m:
        url = m.group(1)
    return "website", url


def parse_boat(html, slug, list_info):
    soup = BeautifulSoup(html, "lxml")
    biz = get_ld_business(soup)
    facts = facts_dl(soup)
    extra = {"src_id": slug}

    # name
    h1 = soup.find(id="b-name")
    name = clean(h1.get_text()) if h1 else None
    if not name:
        name = clean(biz.get("name"))

    # area / port label (server-rendered "東京湾 · 平潟湾")
    area = port_label = None
    ba = soup.find(id="b-area")
    if ba:
        parts = [clean(p) for p in ba.get_text().split("·")]
        parts = [p for p in parts if p]
        if parts:
            area = parts[0]
        if len(parts) >= 2:
            port_label = parts[1]
    if not area:
        asv = biz.get("areaServed") or {}
        if isinstance(asv, dict):
            area = clean(asv.get("name"))
    extra["area"] = area
    extra["port_label"] = port_label

    port = None
    pref_from_port = None
    if port_label:
        m = PREF_PAREN_RE.search(port_label)
        if m:
            pref_from_port = m.group(1)
        port = clean(PREF_PAREN_RE.sub("", port_label))
        base = DIR_SUFFIX_RE.sub("", port or "")
        if port and (port == area or port in SUBAREA_LABELS or
                     (base != port and (base in SUBAREA_LABELS or base in AREA_PREF))):
            extra["port_is_area"] = True
            port = None

    # address (掲載住所)
    address = None
    dd = facts.get("掲載住所")
    if dd is not None:
        for a in dd.find_all("a"):
            a.decompose()
        address = clean(dd.get_text(" "))
    if not address:
        adr = biz.get("address") or {}
        if isinstance(adr, dict):
            address = clean(adr.get("streetAddress"))
    if address:
        address = address.replace("日本、", "").strip()
        if not PREF_RE.match(address):
            # the site sometimes stores only a municipality; keep as-is but it cannot give pref
            extra["address_no_pref"] = True

    # tel
    tel = None
    dd = facts.get("電話番号")
    if dd is not None:
        a = dd.find("a", href=re.compile(r"^tel:"))
        tel = norm_tel(a["href"] if a else dd.get_text(strip=True))
    if not tel:
        tel = norm_tel(biz.get("telephone"))

    # website: only the site's explicit "公式サイト" link (CTA button / facts row / ld url)
    cands = []
    for a in soup.select('a[data-track="cta_site_click"]'):
        cands.append(a.get("href"))
    dd = facts.get("公式サイト")
    if dd is not None:
        for a in dd.find_all("a", href=True):
            cands.append(a["href"])
    if biz.get("url"):
        cands.append(biz.get("url"))
    website = None
    sns = []
    rejected = []
    for u in cands:
        u = (u or "").strip()
        kind, nu = classify_url(u)
        if kind == "website" and not website:
            website = nu
        elif kind == "sns" and nu not in sns:
            sns.append(nu)
        elif kind is None and nu and nu not in rejected:
            rejected.append(nu)
    for u in (biz.get("sameAs") or []):
        kind, nu = classify_url(u)
        if kind == "sns" and nu not in sns:
            sns.append(nu)
    if rejected:
        extra["rejected_site_urls"] = rejected

    # listing source (e.g. シマノ 探見丸搭載船情報) -- informational only
    dd = facts.get("情報の出典")
    if dd is not None:
        extra["listing_source"] = clean(dd.get_text(" "))
        extra["listing_source_urls"] = [a["href"] for a in dd.find_all("a", href=True)]

    # geo
    lat = lon = None
    geo = biz.get("geo") or {}
    if isinstance(geo, dict):
        try:
            lat = float(geo.get("latitude"))
            lon = float(geo.get("longitude"))
            if not (20.0 <= lat <= 46.5 and 122.0 <= lon <= 154.5):
                lat = lon = None
        except (TypeError, ValueError):
            lat = lon = None

    # targets: species with catches in the last 30 days
    targets = []
    sp = soup.find(id="boat-species")
    if sp:
        for a in sp.select("article h3 a"):
            t = clean(a.get_text())
            if t and t not in targets:
                targets.append(t)
    lt = soup.find(id="boat-latest")
    if lt:
        for a in lt.select("a.boat-catch-fish"):
            t = clean(a.get_text())
            if t and t not in targets:
                targets.append(t)
        latest = lt.find("time", attrs={"datetime": True})
        if latest:
            extra["latest_catch"] = latest["datetime"]
        extra["catch_source_hosts"] = sorted(set(host_of(a["href"]) for a in lt.select("a.hist-link[href]")))

    # description (site's own short blurb; skip the auto template for manufacturer lists)
    desc = None
    bd = soup.find(id="b-desc")
    d0 = clean(bd.get_text()) if bd else clean(biz.get("description"))
    if d0 and not TEMPLATE_DESC_RE.search(d0):
        desc = d0 if len(d0) <= 100 else d0[:99] + "…"

    # pref: address > port label "（〇〇県）" > list page label > slug > single-prefecture area
    pref = None
    pref_src = None
    if address:
        m = PREF_RE.match(address)
        if m:
            pref, pref_src = m.group(1), "address"
    li = list_info.get(slug) or {}
    if not pref and pref_from_port:
        pref, pref_src = pref_from_port, "port"
    if not pref:
        m = SLUG_PREF_RE.match(slug)
        if m and m.group(1) in ROMAJI_PREF:
            pref, pref_src = ROMAJI_PREF[m.group(1)], "slug"
    if not pref and li.get("list_pref"):
        pref, pref_src = li["list_pref"], "list"
    if not pref:
        for lab in (area, port_label):
            if not lab:
                continue
            b = DIR_SUFFIX_RE.sub("", lab)
            if lab in AREA_PREF:
                pref, pref_src = AREA_PREF[lab], "area"
                break
            if b in AREA_PREF:
                pref, pref_src = AREA_PREF[b], "area"
                break
        if not pref and area and area.startswith("北海道"):
            pref, pref_src = "北海道", "area"
    extra["pref_src"] = pref_src
    # consistency check between independent pref signals
    signals = set(x for x in [
        PREF_RE.match(address).group(1) if address and PREF_RE.match(address) else None,
        pref_from_port, li.get("list_pref"),
        ROMAJI_PREF.get(SLUG_PREF_RE.match(slug).group(1)) if SLUG_PREF_RE.match(slug) else None,
    ] if x)
    if len(signals) > 1:
        extra["pref_conflict"] = sorted(signals)

    if address and pref and not address.startswith(pref) and not PREF_RE.match(address):
        # e.g. "葛飾区" only: prefix the prefecture only when pref came from a hard signal
        if pref_src in ("port", "list", "slug"):
            address = pref + address
    city = city_from_address(address, pref) if address else None

    rec = {
        "src": SRC,
        "src_id": slug,
        "src_url": "%s/boats/%s" % (BASE, slug),
        "name": name,
        "kana": None,
        "pref": pref,
        "city": city,
        "address": address,
        "port": port,
        "lat": lat,
        "lon": lon,
        "tel": tel,
        "website": website,
        "sns": sns,
        "types": list(li.get("kinds") or []),
        "targets": targets,
        "methods": [],
        "holidays": None,
        "facilities": [],
        "capacity": None,
        "access": None,
        "description": desc,
        "plans": [],
        "schedule_text": "",
        "fetched": FETCHED,
    }
    return rec, extra


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="only the first N boats of the sitemap")
    ap.add_argument("--ids", default="", help="comma separated slugs (or /boats/ URLs)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--refresh", action="store_true", help="re-fetch even if cached")
    ap.add_argument("--offline", action="store_true", help="use cache only")
    ap.add_argument("--no-list", action="store_true", help="skip /boats list pages (pref label, service kind)")
    args = ap.parse_args()

    for d in (CACHE_DIR, os.path.dirname(LOG_PATH), os.path.dirname(EXTRA_OUT)):
        if not os.path.isdir(d):
            os.makedirs(d)
    out = os.path.abspath(args.out)

    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8",
    })

    log("START theboat crawl out=%s limit=%s ids=%s refresh=%s" % (out, args.limit, bool(args.ids), args.refresh))
    all_slugs = list_boat_slugs(session, args.refresh, args.offline)
    if args.ids:
        slugs = []
        for x in args.ids.split(","):
            x = x.strip()
            if not x:
                continue
            if "/boats/" in x:
                x = x.split("/boats/", 1)[1]
            slugs.append(unquote(x).strip("/"))
    else:
        slugs = all_slugs[: args.limit] if args.limit else all_slugs
    total = len(slugs)
    log("sitemap boats=%d target=%d" % (len(all_slugs), total))

    list_info = {}
    if not args.no_list:
        try:
            list_info = crawl_list_pages(session, args.refresh, args.offline)
        except Exception as e:
            log("WARN list pages failed: %r (continuing without)" % (e,))
    log("list info for %d boats" % len(list_info))

    extra_path = EXTRA_OUT if out == os.path.abspath(DEFAULT_OUT) else EXTRA_OUT.replace(".json", ".partial-run.json")
    records, extras, failed = [], [], []
    t0 = time.time()
    for i, slug in enumerate(slugs, 1):
        url = "%s/boats/%s" % (BASE, slug)
        try:
            html = fetch(session, url, refresh=args.refresh, offline=args.offline)
            rec, extra = parse_boat(html, slug, list_info)
            if not rec.get("name"):
                raise ValueError("no name parsed")
            records.append(rec)
            extras.append(extra)
        except NotFound as e:
            failed.append({"src_id": slug, "error": str(e)})
            log("SKIP %s: %s" % (slug, e))
        except Exception as e:
            _stats["errors"] += 1
            failed.append({"src_id": slug, "error": repr(e)})
            log("ERROR %s: %r" % (slug, e))
        if i % 20 == 0 or i == total:
            el = time.time() - t0
            log("progress %d/%d records=%d net=%d cache=%d errors=%d elapsed=%.0fs"
                % (i, total, len(records), _stats["net"], _stats["cache"], len(failed), el))
        if i % 100 == 0:
            write_json_atomic(out, records)
            write_json_atomic(extra_path, {"extras": extras, "failed": failed})
            log("saved %d records -> %s" % (len(records), out))

    write_json_atomic(out, records)
    write_json_atomic(extra_path, {"extras": extras, "failed": failed})
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
