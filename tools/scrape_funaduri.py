#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
船釣り.jp (https://funaduri.jp/) クローラ

サイト構造（2026-09-15 時点で確認）
- 船宿ごとの詳細ページは無い。船宿情報は係留地グループページ
  area.cgi?group=<g> の <dl class="d0|d1" itemtype=SportsActivityLocation> に
  名前・カナ・トン数・公式HP・TEL・住所・最近/去年の釣り物・釣り座方式が載る。
- グループ一覧は area.cgi?area=all（77グループ）。pref.cgi?pref=all は都県別の
  全船宿リスト（都県見出し付き）で、グループページに無い宿の補完と県判定に使う。
- yadoeval.cgi は口コミ投稿フォームなので叩かない。/data/ は robots で Disallow。
- haigyou.cgi は掲載停止（廃業の可能性）リスト → work/sources/funaduri_closed.json
- 座標はサイト上に無い（地図は画像上のピクセル座標）→ lat/lon は null。
- 料金・出船時刻などプラン情報はサイトに無い → plans は空配列。

使い方
  python3 tools/scrape_funaduri.py                 # 全件
  python3 tools/scrape_funaduri.py --limit 30      # 先頭30件
  python3 tools/scrape_funaduri.py --groups oohara,kanazawa --out work/sources/funaduri.sample.json
  python3 tools/scrape_funaduri.py --ids '太東<>勘栄丸,大原<>富久丸'
  --refresh でキャッシュを無視して再取得
"""
from __future__ import print_function

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "funaduri")
OUT_DEFAULT = os.path.join(ROOT, "work", "sources", "funaduri.json")
CLOSED_OUT = os.path.join(ROOT, "work", "sources", "funaduri_closed.json")
LOG_PATH = os.path.join(ROOT, "work", "logs", "funaduri.log")

BASE = "https://funaduri.jp/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒（SPEC: 0.8秒以上）
FETCHED = "2026-09-15"

PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県",
         "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県",
         "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府",
         "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県",
         "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県",
         "鹿児島県", "沖縄県"]
SEIREI = ["横浜市", "川崎市", "相模原市", "千葉市", "さいたま市", "静岡市", "浜松市"]
# 県名が省略された住所の補完用（政令市は県が一意に決まる）
SEIREI_PREF = {"横浜市": "神奈川県", "川崎市": "神奈川県", "相模原市": "神奈川県", "千葉市": "千葉県",
               "さいたま市": "埼玉県", "静岡市": "静岡県", "浜松市": "静岡県"}

# 掲載/予約ポータルの船宿紹介ページ（公式サイトではない）
PORTAL_PATTERNS = [
    r"(^|\.)fishing-v\.jp$", r"(^|\.)chowari\.jp$", r"(^|\.)tsuree\.jp$", r"(^|\.)tsuri-info\.jp$",
    r"(^|\.)turi100\.jp$", r"(^|\.)marines-net\.co\.jp$", r"(^|\.)tsurisoku\.com$",
    r"(^|\.)e-turibune\.com$", r"(^|\.)gyo\.ne\.jp$", r"(^|\.)1091\.co\.jp$", r"(^|\.)ggnet\.co\.jp$",
    r"(^|\.)funaduri\.jp$", r"(^|\.)always-mankai\.sakura\.ne\.jp$", r"(^|\.)asoview\.com$",
    r"(^|\.)jalan\.net$", r"(^|\.)activityjapan\.com$", r"(^|\.)gyogyo\.jp$",
]
SNS_PATTERNS = [
    r"(^|\.)facebook\.com$", r"(^|\.)instagram\.com$", r"(^|\.)twitter\.com$", r"(^|\.)x\.com$",
    r"(^|\.)youtube\.com$", r"(^|\.)youtu\.be$", r"(^|\.)line\.me$", r"(^|\.)lin\.ee$",
    r"(^|\.)tiktok\.com$", r"^www\.ameba\.jp$",
]

SEAT_CLASS = {
    "g2z": "抽選",  # くじ引き・ジャンケンで釣り座
    "g2y": "予約順",
    "g2t": "先着順",
    "g2s": "仕立専門",
}

_last_req = [0.0]
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_stats = {"requests": 0, "cache_hits": 0}


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


def fetch(url, refresh=False):
    """キャッシュ優先で取得。直列・間隔 MIN_INTERVAL 秒・429/503 は指数バックオフ（最大5回）。"""
    if "/data/" in url:
        raise ValueError("robots.txt Disallow: %s" % url)
    p = cache_path(url)
    if not refresh and os.path.exists(p) and os.path.getsize(p) > 0:
        _stats["cache_hits"] += 1
        with open(p, "rb") as f:
            return f.read().decode("utf-8", "replace")
    delay = 5.0
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
        try:
            r = _session.get(url, timeout=60)
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
        body = r.content
        text = body.decode("utf-8", "replace")
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, p)
        return text
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------- 正規化ヘルパ

def clean(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def kata2hira(s):
    out = []
    for ch in s:
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:
            out.append(chr(o - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def host_of(url):
    m = re.match(r"^https?://([^/:?#]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


def classify_url(url):
    """'official' / 'sns' / 'portal' / None"""
    if not url or not re.match(r"^https?://", url, re.I):
        return None
    h = host_of(url)
    if not h or h in ("http:", "https:"):
        return None
    for pat in SNS_PATTERNS:
        if re.search(pat, h):
            return "sns"
    for pat in PORTAL_PATTERNS:
        if re.search(pat, h):
            return "portal"
    return "official"


def norm_url(url):
    url = (url or "").strip()
    if url in ("http://", "https://"):
        return ""
    return url


def complete_pref(addr):
    """県名が省略された住所に、政令市名から一意に決まる県名を付ける。"""
    if not addr:
        return addr
    for p in PREFS:
        if addr.startswith(p):
            return addr
    for sc, p in SEIREI_PREF.items():
        if addr.startswith(sc):
            return p + addr
    return addr


def split_address(addr):
    """住所 → (pref, city)。都道府県名が無ければ (None, None)。"""
    if not addr:
        return None, None
    pref = None
    for p in PREFS:
        if addr.startswith(p):
            pref = p
            break
    if not pref:
        return None, None
    rest = addr[len(pref):]
    city = None
    m = re.match(r"^(.{1,6}?郡.{1,6}?[町村])", rest)
    if m:
        city = m.group(1)
    else:
        for sc in SEIREI:
            if rest.startswith(sc):
                m2 = re.match(r"^(%s[^\d0-9\-]{1,4}?区)" % re.escape(sc), rest)
                city = m2.group(1) if m2 else sc
                break
        if not city:
            m = re.match(r"^(.{1,6}?市)", rest)
            if m:
                city = m.group(1)
            elif pref == "東京都":
                # 島しょ部は「八丈島八丈町」のように島名が前に付くことがある
                m = re.match(r"^(?:[^\d0-9]{1,3}?島(?=.{1,4}?[町村]))?(.{1,4}?[区町村])", rest)
                city = m.group(1) if m else None
            else:
                m = re.match(r"^(.{1,6}?[町村])", rest)
                city = m.group(1) if m else None
    return pref, city


def norm_tel(s):
    s = clean(s)
    s = re.sub(r"^(TEL|Tel|tel|電話)\s*[:：]?\s*", "", s)
    m = re.search(r"0\d{1,4}-\d{1,4}-\d{3,4}", s)
    if m:
        return m.group(0)
    m = re.search(r"0\d{9,10}", s.replace("-", ""))
    return m.group(0) if m else (s or None)


def norm_addr(s):
    s = clean(s)
    s = re.sub(r"^〒?\s*\d{3}-\d{4}\s*", "", s)
    s = s.replace(" ", "")
    return s or None


def short_desc(s):
    s = clean(s)
    if not s or s.startswith("[管理人コメント]") or s.startswith("【管理人"):
        return ""
    if len(s) > 100:
        s = s[:99] + "…"
    return s


# ---------------------------------------------------------------- パーサ

def parse_area_all(html):
    groups = []
    for g in re.findall(r"area\.cgi\?group=(\w+)", html):
        if g not in groups:
            groups.append(g)
    return groups


def parse_pref_all(html):
    """都県別一覧 → [{pref, port, name, href, desc, group}]"""
    s = BeautifulSoup(html, "lxml")
    out = []
    pref = None
    for el in s.find_all(["h2", "li"]):
        if el.name == "h2":
            t = clean(el.get_text())
            pref = None
            for p in PREFS:
                if t.startswith(p):
                    pref = p
            continue
        a2 = el.find("a", class_="a2")
        a1 = el.find("a", class_="a1")
        if not a2 or not a1:
            continue
        desc = el.find("span", class_="desc")
        gm = re.search(r"group=(\w+)", a1.get("href", ""))
        out.append({
            "pref": pref,
            "port": clean(a1.get_text()),
            "name": clean(a2.get_text()),
            "href": norm_url(a2.get("href")),
            "desc": clean(desc.get_text()) if desc else "",
            "group": gm.group(1) if gm else None,
        })
    return out


def parse_group(html, group, group_url):
    s = BeautifulSoup(html, "lxml")
    inmain = s.find(id="inmain") or s
    recs = []
    port_formal = None
    for el in inmain.find_all(["div", "dl"]):
        cls = el.get("class") or []
        if el.name == "div" and "portborder" in cls:
            t = clean(el.get_text())
            t = re.sub(r"^係留地[:：]\s*", "", t)
            t = re.sub(r"\s*周辺$", "", t)
            port_formal = t or None
            continue
        if el.name != "dl" or not (set(cls) & {"d0", "d1"}):
            continue
        rec = parse_dl(el, port_formal, group, group_url)
        if rec:
            recs.append(rec)
    return recs


def parse_dl(dl, port_formal, group, group_url):
    name_el = dl.find(itemprop="name")
    if not name_el:
        return None
    name = clean(name_el.get_text())
    g0 = dl.find("dd", class_="g0")
    area_name, port_short = None, None
    if g0:
        parts = [clean(x) for x in g0.get_text("\n").split("\n") if clean(x)]
        if parts:
            area_name = parts[0]
        if len(parts) > 1:
            port_short = parts[1]
    # 宿キー（yadoeval.cgi?yado=港<>船名）
    key = None
    for a in dl.find_all("a", href=True):
        m = re.search(r"yadoeval\.cgi\?yado=([^#&\"]+)", a["href"])
        if m:
            key = unquote(m.group(1))
            break
    if not key:
        key = "%s<>%s" % (port_short or port_formal or "", name)

    g2 = dl.find("dd", class_=re.compile(r"^g2"))
    seat = None
    if g2:
        for c in g2.get("class") or []:
            if c in SEAT_CLASS:
                seat = SEAT_CLASS[c]
    types = []
    if seat == "仕立専門":
        types = ["仕立"]
    elif seat in ("抽選", "予約順", "先着順"):
        types = ["乗合"]

    hp = dl.find("a", class_="yyhp")
    hp_url = norm_url(hp.get("href")) if hp else ""
    blog = dl.find("a", class_="tyokablog")
    blog_url = norm_url(blog.get("href")) if blog else ""
    kana_el = dl.find(itemprop="alternateName")
    kana = kata2hira(clean(kana_el.get_text())) if kana_el else ""

    website = None
    sns = []
    portal_urls = []
    for u in [hp_url, blog_url]:
        c = classify_url(u)
        if c == "official" and website is None and u == hp_url:
            website = u
        elif c == "sns":
            if u not in sns:
                sns.append(u)
        elif c == "portal":
            portal_urls.append(u)

    tel_el = dl.find(itemprop="telephone")
    addr_el = dl.find(itemprop="address")
    tel = norm_tel(tel_el.get_text()) if tel_el else None
    address = complete_pref(norm_addr(addr_el.get_text())) if addr_el else None
    pref, city = split_address(address)

    targets = []
    g3 = dl.find("dd", class_="g3")
    if g3:
        # 最近の釣り物（Lfish 外）→ 去年同時期（Lfish 内）の順
        recent, lastyear = [], []
        lf = g3.find(class_="Lfish")
        for a in g3.find_all("a", href=re.compile(r"fish\.cgi\?fish=")):
            t = clean(a.get_text())
            if not t:
                continue
            if lf is not None and a in lf.find_all("a"):
                lastyear.append(t)
            else:
                recent.append(t)
        for t in recent + lastyear:
            if t not in targets:
                targets.append(t)

    desc = ""
    ht = dl.find(class_="hptitle")
    if ht:
        for a in ht.find_all("a", href=True):
            if "yadoeval.cgi" in a["href"]:
                continue
            desc = short_desc(a.get_text())
            break

    return {
        "src": "funaduri",
        "src_id": key,
        "src_url": group_url,
        "name": name,
        "kana": kana,
        "pref": pref,
        "city": city,
        "address": address,
        "port": port_formal or port_short,
        "lat": None, "lon": None,
        "tel": tel,
        "website": website,
        "sns": sns,
        "types": types,
        "targets": targets,
        "methods": [],
        "holidays": "",
        "facilities": [],
        "capacity": None,
        "access": "",
        "description": desc,
        "plans": [],
        "schedule_text": "",
        "fetched": FETCHED,
        "_group": group,
        "_area": area_name,
        "_port_short": port_short,
        "_portal_urls": portal_urls,
        "_seat": seat,
    }


def parse_haigyou(html):
    s = BeautifulSoup(html, "lxml")
    ul = s.find(id="haigyoulist")
    out = []
    if not ul:
        return out
    for li in ul.find_all("li"):
        t = clean(li.get_text())
        if not t:
            continue
        parts = t.split(" ", 1)
        port, name = (parts[0], parts[1]) if len(parts) == 2 else ("", t)
        out.append({"src": "funaduri", "status": "掲載停止(廃業・営業停止の可能性)",
                    "port": port, "name": name, "src_url": BASE + "haigyou.cgi",
                    "fetched": FETCHED})
    return out


# ---------------------------------------------------------------- 出力

PUBLIC_KEYS = ["src", "src_id", "src_url", "name", "kana", "pref", "city", "address", "port",
               "lat", "lon", "tel", "website", "sns", "types", "targets", "methods", "holidays",
               "facilities", "capacity", "access", "description", "plans", "schedule_text",
               "fetched"]


def public(rec):
    return dict((k, rec.get(k)) for k in PUBLIC_KEYS)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def name_key(port, name):
    return (clean(port), clean(name).replace(" ", ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="先頭N件だけ")
    ap.add_argument("--ids", default="", help="src_id（港<>船名）をカンマ区切りで")
    ap.add_argument("--groups", default="", help="対象グループ(area.cgi?group=)をカンマ区切りで")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    for d in (CACHE_DIR, os.path.dirname(LOG_PATH), os.path.dirname(os.path.abspath(args.out))):
        if not os.path.isdir(d):
            os.makedirs(d)

    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    only_groups = [x.strip() for x in args.groups.split(",") if x.strip()]
    full_run = not (args.limit or ids or only_groups)
    log("START funaduri out=%s limit=%s ids=%d groups=%s" % (args.out, args.limit, len(ids),
                                                             ",".join(only_groups) or "all"))

    # 1) 一覧
    pref_list = parse_pref_all(fetch(BASE + "pref.cgi?pref=all", args.refresh))
    log("pref_all entries=%d" % len(pref_list))
    groups = parse_area_all(fetch(BASE + "area.cgi?area=all", args.refresh))
    for e in pref_list:  # 念のため一覧側のグループも足す
        if e["group"] and e["group"] not in groups:
            groups.append(e["group"])
    if only_groups:
        groups = [g for g in groups if g in only_groups] + [g for g in only_groups if g not in groups]
    log("groups=%d" % len(groups))

    # 廃業リスト
    closed = parse_haigyou(fetch(BASE + "haigyou.cgi", args.refresh))
    save_json(CLOSED_OUT, closed)
    closed_keys = set(name_key(c["port"], c["name"]) for c in closed)
    log("closed list=%d -> %s" % (len(closed), CLOSED_OUT))

    # pref_all 由来の県（名前+港 → 県）
    pa_index = {}
    for e in pref_list:
        pa_index.setdefault(name_key(e["port"], e["name"]), e)

    # 2) グループページ
    records = []
    seen = set()
    total = len(groups)
    last_saved = 0
    stop = False
    for i, g in enumerate(groups):
        url = BASE + "area.cgi?group=" + g
        try:
            html = fetch(url, args.refresh)
        except Exception as e:  # noqa
            log("ERROR group %s: %s" % (g, e))
            continue
        recs = parse_group(html, g, url)
        for r in recs:
            if r["src_id"] in seen:
                continue
            if ids and r["src_id"] not in ids:
                continue
            seen.add(r["src_id"])
            pk = name_key(r["_port_short"] or "", r["name"])
            if pk in closed_keys:
                continue
            pe = pa_index.get(pk)
            if pe:
                pe["_matched"] = True
            if not r["pref"] and pe and pe.get("pref"):
                r["pref"] = pe["pref"]
            if not r["description"] and pe:
                r["description"] = short_desc(pe["desc"])
            if not r["website"] and not r["sns"] and pe and pe["href"]:
                c = classify_url(pe["href"])
                if c == "official":
                    r["website"] = pe["href"]
                elif c == "sns":
                    r["sns"].append(pe["href"])
            records.append(r)
            if args.limit and len(records) >= args.limit:
                stop = True
                break
        log("progress %d/%d groups (%s: %d shops) records=%d requests=%d cache=%d" % (
            i + 1, total, g, len(recs), len(records), _stats["requests"], _stats["cache_hits"]))
        if len(records) - last_saved >= 100:
            save_json(args.out, [public(x) for x in records])
            last_saved = len(records)
        if stop:
            break

    # 3) グループページに無い宿を一覧から補完（全件モードのみ）
    if full_run:
        added = 0
        for e in pref_list:
            if e.get("_matched"):
                continue
            k = name_key(e["port"], e["name"])
            if k in closed_keys:
                continue
            sid = "%s<>%s" % (e["port"], e["name"])
            if sid in seen:
                continue
            seen.add(sid)
            c = classify_url(e["href"])
            records.append({
                "src": "funaduri", "src_id": sid, "src_url": BASE + "pref.cgi?pref=all",
                "name": e["name"], "kana": "", "pref": e["pref"], "city": None, "address": None,
                "port": e["port"], "lat": None, "lon": None, "tel": None,
                "website": e["href"] if c == "official" else None,
                "sns": [e["href"]] if c == "sns" else [],
                "types": [], "targets": [], "methods": [], "holidays": "", "facilities": [],
                "capacity": None, "access": "", "description": short_desc(e["desc"]),
                "plans": [], "schedule_text": "", "fetched": FETCHED,
            })
            added += 1
        log("pref_all-only entries added=%d" % added)

    # 4) 県名なし住所の補完：他レコードで学習した「市区町村 → 県」が一意なら付与
    city_pref = {}
    for r in records:
        if r.get("pref") and r.get("city"):
            city_pref.setdefault(r["city"], set()).add(r["pref"])
    fixed = 0
    for r in records:
        addr = r.get("address")
        if not addr or split_address(addr)[0]:
            continue
        for c in sorted(city_pref, key=len, reverse=True):
            if addr.startswith(c) and len(city_pref[c]) == 1:
                p = list(city_pref[c])[0]
                if r.get("pref") in (None, p):
                    r["address"] = p + addr
                    r["pref"], r["city"] = split_address(r["address"])
                    fixed += 1
                break
    if fixed:
        log("address pref completed by city map: %d" % fixed)

    # 5) 住所・TELが無い宿の県：同じ係留地グループの他の宿の県が一意ならそれを使う
    group_pref = {}
    for r in records:
        if r.get("_group") and r.get("pref"):
            group_pref.setdefault(r["_group"], set()).add(r["pref"])
    gfixed = 0
    for r in records:
        g = r.get("_group")
        if not r.get("pref") and g and len(group_pref.get(g, ())) == 1:
            r["pref"] = list(group_pref[g])[0]
            gfixed += 1
    if gfixed:
        log("pref completed by group: %d" % gfixed)

    save_json(args.out, [public(x) for x in records])
    log("requests=%d cache_hits=%d" % (_stats["requests"], _stats["cache_hits"]))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
