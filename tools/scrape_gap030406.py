#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
地域の釣り船一覧（岩手・宮城・山形）の取り込み（src="gap030406"）

全国の予約・掲載サイトに少ない東北3県の釣り船を、地域の一覧ページから取り込む。
調査メモ: work/discovery/gap030406.md

取り込む一覧（2026-09-15 時点で確認。どれも1ページ）
  prdse           おさかな釣りする人。「宮城県 おすすめ釣り船・遊漁船一覧 まとめ情報」
                  https://prdse.net/fishing-boat/
                  エリア別の表（漁船名/出船場所/トイレ/女性子供/料金/URL）＋船ごとの表
                  （出船場所/電話番号/ターゲット/ホームページURL）。表と船ごとの表は同じ順。
                  料金列はエリアごとにほぼ同じ額が並ぶ目安表記なので plans にしない。
  mankitsuya      自然満喫屋「釣り船のご紹介」 https://www.mankitsuya.jp/turibunesyoukai.htm
                  鶴岡・酒田・新潟・秋田エリアの遊漁船カード（携帯/出船港/釣り物/HP・ブログ・Instagram）
  sakata-annaijo  酒田遊漁船・酒田釣り船 案内所 http://yuugyosen.com/
                  船ごとの表（WEBサイト/釣果・ブログ/予約状況/料金/問合せ先）
  sakata-tobishima 酒田観光物産協会「磯釣り、船釣り」（飛島） https://sakata-kankou.com/spot/30023
                  磯渡し・船釣りのある宿 3軒（遊覧船だけの宿は無い）
  minamisanriku-utatsu 南三陸町観光協会「南三陸町歌津地区でフィッシング♪」（2020-03-26 掲載）
                  https://www.m-kankou.jp/archives/program/program-234522/  → 全件 stale=true

個人名は出力しない: 「船長」欄・問合せ先の氏名は読まない。氏名そのものの URL
（Instagram の氏名アカウント、氏名入りのブログ URL）は website/sns に入れない（PERSONAL_URLS）。

src_id = "<一覧の短い名前>:<一覧の中の位置や船の id>"、src_url = 一覧の URL。

使い方
  python3 tools/scrape_gap030406.py              # 全一覧（キャッシュがあれば再取得しない）
  python3 tools/scrape_gap030406.py --only prdse,mankitsuya --out /tmp/x.json
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
import urllib.robotparser
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import nfkc, tel_display, host_of, host_in, clean_url, NOT_OFFICIAL_HOSTS, SNS_HOSTS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "gap030406")
OUT_DEFAULT = os.path.join(ROOT, "work", "sources", "gap030406.json")
LOG_PATH = os.path.join(ROOT, "work", "logs", "gap030406.log")
SRC = "gap030406"
FETCHED = "2026-09-15"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0

SOURCES = [
    ("prdse", "https://prdse.net/fishing-boat/"),
    ("mankitsuya", "https://www.mankitsuya.jp/turibunesyoukai.htm"),
    ("sakata-annaijo", "http://yuugyosen.com/"),
    ("sakata-tobishima", "https://sakata-kankou.com/spot/30023"),
    ("minamisanriku-utatsu", "https://www.m-kankou.jp/archives/program/program-234522/"),
]

# 掲載サイト・予約アプリ・短縮 URL など、船宿の公式サイトとして扱わないホスト（common の一覧に追加）
EXTRA_NOT_OFFICIAL = ("tsurimaru.jp", "tsuritaro-fishing.com", "anglers.jp", "tol-app.jp", "x.gd",
                      "calendar.google.com", "profile.ameba.jp")
# 個人の氏名そのものの URL（website/sns に入れない）
PERSONAL_URLS = (
    "instagram.com/hoshikawa.yuudai4",
    "instagram.com/yuuklnakano",
    "instagram.com/tadashi0525",
    "ameblo.jp/fumiaki-saitou",
    "ameblo.jp/tomoya0903t",
    "blog.goo.ne.jp/alwayshiguchihayato",
    "blog.goo.ne.jp/makino-sentyo",
)

_last = {}
_robots = {}
_log = None


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(line)
    if _log:
        _log.write(line + "\n")
        _log.flush()


# ---------------------------------------------------------------- 取得（キャッシュ・robots・間隔）
def cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".html")


def robots_ok(url):
    p = urlsplit(url)
    base = "%s://%s" % (p.scheme, p.netloc)
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=60)
            ctype = r.headers.get("content-type", "")
            rp.parse(r.text.splitlines() if r.status_code == 200 and "html" not in ctype else [])
        except requests.RequestException:
            rp.parse([])
        _robots[base] = rp
        _last[p.netloc] = time.time()
    return _robots[base].can_fetch("*", url)


def fetch(url, refresh=False):
    path = cache_path(url)
    if os.path.exists(path) and not refresh:
        with open(path, "rb") as f:
            return f.read()
    if not robots_ok(url):
        raise RuntimeError("robots.txt disallow: " + url)
    host = urlsplit(url).netloc
    for attempt in range(5):
        wait = MIN_INTERVAL - (time.time() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        _last[host] = time.time()
        if r.status_code in (429, 503):
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(r.content)
        return r.content
    raise RuntimeError("too many retries: " + url)


def soup_of(url, refresh):
    raw = fetch(url, refresh)
    return BeautifulSoup(raw.decode("utf-8", "ignore"), "html.parser")


# ---------------------------------------------------------------- 正規化の小道具
def txt(node):
    if node is None:
        return ""
    s = node.get_text(" ") if hasattr(node, "get_text") else str(node)
    return re.sub(r"\s+", " ", nfkc(s)).strip()


def squeeze_cjk(s):
    """「韋 駄 天」のような和文字間の空白を詰める。"""
    return re.sub(r"(?<=[^\x00-\x7f])\s+(?=[^\x00-\x7f])", "", s)


def kata_to_hira(s):
    s = nfkc(s).replace(" ", "")
    if not s or not re.fullmatch(r"[ァ-ヶー]+", s):
        return None
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def split_reading(name):
    """「遊漁船 SHOW TIME（ショータイム）」→ (本体, 読み)。括弧内がかな/カナのときだけ読みとする。"""
    name = nfkc(name)
    m = re.match(r"^(.*?)\s*[（(〈<]\s*([^）)〉>]+?)\s*[）)〉>]\s*$", name)
    if not m:
        return name, None
    inner = m.group(2)
    hira = kata_to_hira(inner)
    if hira is None and re.fullmatch(r"[ぁ-ゖー\s]+", inner):
        hira = inner.replace(" ", "")
    return m.group(1).strip(), hira


PREFIXES = ("えびす屋釣り具/遊漁船 ", "フィッシングガイド船 ", "釣り船 酒田 ", "酒田市遊漁船 ", "酒田 遊漁船 ",
            "酒田遊漁船 ", "遊漁船 ", "つり船 ", "釣り船 ", "釣船 ")


def strip_prefix(name):
    for p in PREFIXES:
        if name.startswith(p) and len(name) > len(p):
            return name[len(p):].strip()
    if name.startswith("釣り船") and len(name) > 3 and not name[3].isspace():
        return name[3:]
    return name


def is_personal(u):
    low = (u or "").lower()
    return any(p in low for p in PERSONAL_URLS)


def classify_url(u):
    """→ ("website"|"sns"|None, url)"""
    u = clean_url(u or "")
    if not u or is_personal(u):
        return None, None
    h = host_of(u)
    if host_in(h, SNS_HOSTS):
        return "sns", u
    if host_in(h, NOT_OFFICIAL_HOSTS) or host_in(h, EXTRA_NOT_OFFICIAL):
        return None, None
    path = re.sub(r"^https?://[^/]+", "", u).strip("/")
    if h in ("blog.livedoor.jp", "ameblo.jp", "blog.goo.ne.jp") and not path:
        return None, None  # サービスのトップだけ（船のページではない）
    return "website", u


def all_tels(s):
    out = []
    for part in re.split(r"[/／、,]|\s{2,}", nfkc(s)):
        t = tel_display(part)
        if t and t not in out:
            out.append(t)
    return out


def record(**kw):
    r = {
        "src": SRC, "src_id": None, "src_url": None, "name": None, "kana": None, "pref": None,
        "city": None, "address": None, "port": None, "lat": None, "lon": None, "tel": None,
        "website": None, "sns": [], "types": [], "targets": [], "methods": [], "holidays": "",
        "facilities": [], "capacity": None, "access": "", "description": "", "plans": [],
        "schedule_text": "", "fetched": FETCHED,
    }
    r.update(kw)
    return r


TEMPLATE_TARGETS = "マダイ、青物、タチウオ、サワラ、ソイ、メバル、アイナメ、ヒラメ、ホウボウ、マダラ、シーバス、ヒラメ"
METHOD_WORDS = ("ジギング", "テンヤ", "五目", "リレー", "エサ釣り", "餌釣り", "ルアー")


def parse_targets(s):
    s = nfkc(s)
    s = re.sub(r"[（(][^）)]*[）)]", "、", s)
    out = []
    for it in re.split(r"[、,・/]", s):
        it = it.strip()
        it = re.sub(r"(など|等)$", "", it).strip()
        if "泳がせ" in it:
            it = it.split("泳がせ")[-1]
        for w in METHOD_WORDS:
            it = it.replace(w, "")
        it = re.sub(r"^追波湾", "", it).strip()
        if not it or "要" in it or "作業" in it or "あれば" in it or it in ("沖釣り", "釣り"):
            continue
        if it not in out:
            out.append(it)
    return out


# ---------------------------------------------------------------- prdse（宮城）
def parse_prdse(url, refresh):
    soup = soup_of(url, refresh)
    summary = []
    for table in soup.find_all("table"):
        ths = [txt(th) for th in table.find_all("th")]
        if not ths or ths[0] != "漁船名":
            continue
        for tr in (table.find("tbody") or table).find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            summary.append({"name": txt(tds[0]), "port": txt(tds[1]), "toilet": txt(tds[2])})
    h2s = soup.find_all("h2")
    h2_text = [txt(h) for h in h2s]
    start = next(i for i, t in enumerate(h2_text) if "釣り船情報詳細" in t)
    end = next(i for i, t in enumerate(h2_text) if t == "まとめ")
    detail_h2 = set(id(h) for h in h2s[start:end])
    details = []
    for h3 in soup.find_all("h3"):
        if "area-heading" in (h3.get("class") or []):
            continue
        prev = h3.find_previous("h2")
        if prev is None or id(prev) not in detail_h2:
            continue
        table = h3.find_next("table")
        if table is None or table.find_previous("h3") is not h3:
            continue
        fields = {}
        for tr in table.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th is None or td is None:
                continue
            a = td.find("a")
            fields[txt(th)] = (txt(td), a.get("href") if a else None)
        if "出船場所" in fields:
            details.append((txt(h3), fields))
    log("prdse: summary rows=%d detail sections=%d" % (len(summary), len(details)))
    if len(summary) != len(details):
        raise RuntimeError("prdse: 表と船ごとの表の件数が一致しない")
    out = []
    for i, ((head, f), row) in enumerate(zip(details, summary), 1):
        body, kana = split_reading(head)
        name = strip_prefix(nfkc(row["name"]))
        loc = f.get("出船場所", ("", None))[0]
        m = re.search(r"宮城県\s*(?:\S+?郡)?\s*(\S+?[市町村])", loc)
        city = m.group(1) if m else None
        tels = all_tels(f.get("電話番号", ("", None))[0])
        tgt_raw = next((v[0] for k, v in f.items() if k.startswith("ターゲット")), "")
        targets = [] if tgt_raw.startswith(TEMPLATE_TARGETS) else parse_targets(tgt_raw)
        website, sns = None, []
        kind, u = classify_url(f.get("ホームページURL", ("", None))[1])
        if kind == "website":
            website = u
        elif kind == "sns":
            sns.append(u)
        desc = []
        if len(tels) > 1:
            desc.append("別の電話: " + " / ".join(tels[1:]))
        if loc:
            desc.append("出船場所: " + loc)
        out.append(record(
            src_id="prdse:%d" % i, src_url=url, name=name, kana=kana, pref="宮城県", city=city,
            port=nfkc(row["port"]) or None, tel=tels[0] if tels else None, website=website, sns=sns,
            targets=targets, facilities=["トイレ"] if row["toilet"] == "〇" else [],
            description="。".join(desc)[:100],
        ))
    return out


# ---------------------------------------------------------------- 自然満喫屋（山形・新潟・秋田）
AREA_PREF = (("鶴岡", "山形県"), ("酒田", "山形県"), ("新潟", "新潟県"), ("秋田", "秋田県"))


def parse_mankitsuya(url, refresh):
    soup = soup_of(url, refresh)
    out = []
    for sec in soup.find_all("section", class_="area-section"):
        area = txt(sec.find("h2"))
        pref = next((p for k, p in AREA_PREF if k in area), None)
        if pref is None:
            continue
        area_key = sec.get("data-area") or re.sub(r"\W", "", area)
        for n, art in enumerate(sec.find_all("article", class_="boat-card"), 1):
            raw = txt(art.find("h3"))
            m = re.match(r"^.*?-\s*(.+?)\s*-$", raw)  # Kouyumaru -剛雄丸-
            body, kana = split_reading(m.group(1) if m else raw)
            name = squeeze_cjk(body)
            rows = {}
            for row in art.find_all("div", class_="row"):
                lab, val = row.find("div", class_="label"), row.find("div", class_="value")
                if lab is not None and val is not None:
                    rows[txt(lab)] = txt(val)
            tels = all_tels(rows.get("携帯") or rows.get("電話") or "")
            port_raw = rows.get("出船港", "")
            city = None
            mc = re.search(r"(\S+?市)", port_raw)
            if mc:
                city = mc.group(1).replace(pref, "")
            port = port_raw.replace(pref, "").replace(city or "\0", "")
            port = re.sub(r"より出船$", "", port).strip() or None
            website, blog, sns = None, None, []
            for a in art.find_all("a", class_="mini-link"):
                label = txt(a)
                kind, u = classify_url(a.get("href"))
                if kind == "sns":
                    sns.append(u)
                elif kind == "website":
                    if "HP" in label:
                        website = u
                    elif blog is None:
                        blog = u
            fish = rows.get("釣り物", "")
            out.append(record(
                src_id="mankitsuya:%s-%d" % (area_key, n), src_url=url, name=name, kana=kana, pref=pref,
                city=city, port=port, tel=tels[0] if tels else None, website=website or blog, sns=sns,
                description=("釣り物: " + fish)[:100] if fish else "",
            ))
    return out


# ---------------------------------------------------------------- 酒田遊漁船案内所（山形）
YEN = re.compile(r"^[¥￥]\s*([\d,]+)\s*(?:[~〜～]\s*(?:[\d,]+)?)?\s*(.*)$")


def parse_sakata_annaijo(url, refresh):
    soup = soup_of(url, refresh)
    anchors = []
    for a in soup.select('th a[href^="#"]'):
        k = a.get("href")[1:]
        if k and k not in anchors:
            anchors.append(k)
    sections = {}
    for sec in soup.find_all("section", id=True):
        if sec["id"] in anchors:
            sections[sec["id"]] = sec  # 同じ id が2回あれば後（Detail 側）を使う
    log("sakata-annaijo: list anchors=%d detail sections=%d" % (len(anchors), len(sections)))
    out = []
    for key in anchors:
        sec = sections.get(key)
        if sec is None:
            log("  warn: no detail section for #" + key)
            continue
        body, kana = split_reading(txt(sec.find("h3")))
        name = squeeze_cjk(strip_prefix(body))
        web, blog, tels, plans, price_page = None, None, [], [], ""
        for tr in sec.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th is None or td is None:
                continue
            head = txt(th)
            if "WEBサイト" in head:
                for a in td.find_all("a"):
                    if "WEBサイト" in txt(a):
                        web = a.get("href")
                    elif "釣果" in txt(a):
                        blog = a.get("href")
            elif head == "料金":
                for li in td.find_all("li"):
                    a = li.find("a")
                    if a is not None:
                        if "料金" in txt(a):
                            price_page = clean_url(a.get("href")) or ""
                        continue
                    line = txt(li)
                    m = YEN.match(line)
                    if not m:
                        continue
                    label = m.group(2).strip()
                    val = int(m.group(1).replace(",", ""))
                    if re.search(r"貸切|貸し切り|チャーター", label):
                        kind, price = "仕立", None
                    elif re.search(r"乗合|乗り合い", label):
                        kind, price = "乗合", val
                    else:
                        kind, price = "", (val if val < 30000 else None)
                    plans.append({"name": label, "kind": kind, "targets": [], "price": price,
                                  "price_text": line, "depart": "", "return": "", "meet": "",
                                  "season": "", "days": "", "includes": "", "url": ""})
            elif head == "問合せ先":
                for li in td.find_all("li"):
                    for t in all_tels(txt(li)):
                        if t not in tels:
                            tels.append(t)
        if price_page:
            for p in plans:
                p["url"] = price_page
        website, sns = None, []
        for cand in (web, blog):
            kind, u = classify_url(cand)
            if kind == "sns" and u not in sns:
                sns.append(u)
            elif kind == "website" and website is None:
                website = u
        out.append(record(
            src_id="sakata-annaijo:" + key, src_url=url, name=name, kana=kana, pref="山形県",
            tel=tels[0] if tels else None, website=website, sns=sns, plans=plans,
            description=("別の電話: " + " / ".join(tels[1:])) if len(tels) > 1 else "",
        ))
    return out


# ---------------------------------------------------------------- 酒田観光「磯釣り、船釣り」（飛島）
def parse_sakata_tobishima(url, refresh):
    soup = soup_of(url, refresh)
    text = nfkc(soup.get_text("\n"))
    addr = None
    m = re.search(r"住所\s*\n\s*(山形県\S+)", text)
    if m:
        addr = m.group(1)
    out = []
    for n, m in enumerate(re.finditer(r"^\s*(\S+?)\s*([★◆●]+)\s*TEL\s*([0-9\-‐]+)\s*$", text, re.M), 1):
        name, marks, tel = m.group(1), m.group(2), m.group(3)
        kinds = []
        if "★" in marks:
            kinds.append("磯渡し")
        if "◆" in marks:
            kinds.append("船釣り")
        if "●" in marks:
            kinds.append("遊覧船")
        if not ("★" in marks or "◆" in marks):
            continue  # 遊覧船だけの宿は対象外
        out.append(record(
            src_id="sakata-tobishima:%d" % n, src_url=url, name=name, pref="山形県", city="酒田市",
            address=addr, tel=tel_display(tel), types=["磯渡し"] if "★" in marks else [],
            description="飛島の宿（%sあり）" % "・".join(kinds),
        ))
    return out


# ---------------------------------------------------------------- 南三陸町観光協会「歌津地区でフィッシング」
def parse_utatsu(url, refresh):
    soup = soup_of(url, refresh)
    # NFKC は「…」を "..." にするので、区切りとして戻す
    text = nfkc(soup.get_text("\n")).replace("...", "…")
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    posted = m.group(0) if m else None
    i = text.find("【")
    j = text.find("道具レンタル", i)
    body = text[i:j if j > 0 else None]
    out = []
    for n, m in enumerate(re.finditer(r"【([^】]+)】(.*?)(?=【|\Z)", body, re.S), 1):
        name, blk = m.group(1).strip(), m.group(2)
        fish = re.search(r"■魚種一例[::]?(.*?)(?=■料金)", blk, re.S)
        targets = []
        if fish:
            for line in fish.group(1).splitlines():
                if "…" in line:
                    for t in parse_targets(line.split("…", 1)[1]):
                        if t not in targets:
                            targets.append(t)
        price = re.search(r"■料金…\s*(.+)", blk)
        plans, types = [], []
        if price and "要問合せ" not in price.group(1):
            pt = price.group(1).strip()
            pm = re.search(r"([\d,]+)円", pt)
            kind = "乗合" if re.search(r"乗り合い|乗合", pt) else ""
            if kind:
                types.append(kind)
            plans.append({"name": pt, "kind": kind, "targets": targets, "price": int(pm.group(1).replace(",", "")) if pm else None,
                          "price_text": pt, "depart": "", "return": "", "meet": "", "season": "", "days": "",
                          "includes": "", "url": ""})
        tm = re.search(r"■問合せ先…\s*(.+)", blk)
        tels = all_tels(tm.group(1)) if tm else []
        um = re.search(r"■URL\s*[::…]*\s*(\S+)", blk)
        website = None
        if um:
            u = um.group(1)
            if not u.startswith("http"):
                u = "http://" + u
            kind, u = classify_url(u)
            website = u if kind == "website" else None
        desc = ["歌津地区の釣り船（観光協会の%s掲載記事）" % (posted or "")]
        if len(tels) > 1:
            desc.append("別の電話: " + " / ".join(tels[1:]))
        if "乗り合いは基本的にいたしません" in blk:
            desc.append("乗合は基本的にしない")
        out.append(record(
            src_id="minamisanriku-utatsu:%d" % n, src_url=url, name=name, pref="宮城県", city="南三陸町",
            tel=tels[0] if tels else None, website=website, types=types, targets=targets, plans=plans,
            description="。".join(desc)[:100], stale=True,
        ))
    return out


PARSERS = {
    "prdse": parse_prdse,
    "mankitsuya": parse_mankitsuya,
    "sakata-annaijo": parse_sakata_annaijo,
    "sakata-tobishima": parse_sakata_tobishima,
    "minamisanriku-utatsu": parse_utatsu,
}


def main():
    global _log
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    only = set(x for x in args.only.split(",") if x)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    _log = open(LOG_PATH, "a", encoding="utf-8")
    todo = [(k, u) for k, u in SOURCES if not only or k in only]
    records, counts = [], {}
    for done, (key, url) in enumerate(todo, 1):
        recs = PARSERS[key](url, args.refresh)
        counts[key] = len(recs)
        records.extend(recs)
        log("done %d/%d %s -> %d records" % (done, len(todo), key, len(recs)))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out + ".tmp", "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    os.replace(args.out + ".tmp", args.out)
    log("wrote %s (%d records) %s" % (args.out, len(records), json.dumps(counts, ensure_ascii=False)))


if __name__ == "__main__":
    main()
