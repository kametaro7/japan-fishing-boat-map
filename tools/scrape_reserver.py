#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RESERVER (https://reserver.co.jp/) 湖・ダムのバス釣りガイド クローラ

対象: ガイド（/guid/<slug>/）のみ。レンタルボート店（/shop/）は遊漁船ではないので対象外。
列挙: sitemap.xml の /guid/<slug>/（review/input-review を除く）と、全国ガイド一覧
      /guid/ ・ /guid/?p=1 ・ ... のカード（TEL はカード側にしか無い）。
      sitemap にあって一覧に無いガイドは stale=true。
詳細: /guid/<slug>/ … 見出し・ガイド種別・料金（1〜3名, 税込）・時間目安・対応フィールド・コメント、
      パンくず（県・メインのフィールド）、サイドバー（Instagram/X/YouTube/その他のリンク）。
      住所・座標は無い（lat/lon は null、port にフィールド名）。
除外: 「おかっぱり」（岸釣り）ガイドは船ではないので出力しない（--keep-shore で残す）。
個人名: 代表者の個人名は出力しない。見出しが個人名だけなら「<湖>のバス釣りガイド」（ページの h2）、
        屋号と個人名が並ぶ見出しは個人名の部分を落とす。kana・プロフィール文・メールは出さない。
作法: 1ホスト直列・間隔 --interval 秒（既定1.0, 下限0.8）・429/503 は指数バックオフ(最大5回)・
      生HTMLは work/cache/reserver/ に保存し再実行時はキャッシュを使う（--refresh で再取得）。
      robots.txt の Disallow（/shop/<id>/info/ 等, /chokainput/）は叩かない。予約フォームは送らない。

使い方:
  python3 tools/scrape_reserver.py                  # 全件
  python3 tools/scrape_reserver.py --limit 10       # 一覧の先頭10件
  python3 tools/scrape_reserver.py --ids nakata,uentsu --out work/sources/reserver.sample.json
"""
from __future__ import print_function

import argparse
import datetime
import json
import os
import re
import sys
import time
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

BASE = "https://reserver.co.jp"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "work", "cache", "reserver")
LOG_PATH = os.path.join(ROOT, "work", "logs", "reserver.log")
OUT_PATH = os.path.join(ROOT, "work", "sources", "reserver.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

PREF_NAMES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
# URL の /area/<romaji>/ → 県名（パンくずの文字が取れないときの予備）
PREF_ROMA = {
    "hokkaido": "北海道", "aomori": "青森県", "iwate": "岩手県", "miyagi": "宮城県", "akita": "秋田県",
    "yamagata": "山形県", "fukushima": "福島県", "ibaraki": "茨城県", "tochigi": "栃木県", "gumma": "群馬県",
    "gunma": "群馬県", "saitama": "埼玉県", "chiba": "千葉県", "tokyo": "東京都", "kanagawa": "神奈川県",
    "niigata": "新潟県", "toyama": "富山県", "ishikawa": "石川県", "fukui": "福井県", "yamanashi": "山梨県",
    "nagano": "長野県", "gifu": "岐阜県", "shizuoka": "静岡県", "aichi": "愛知県", "mie": "三重県",
    "shiga": "滋賀県", "kyoto": "京都府", "osaka": "大阪府", "hyogo": "兵庫県", "nara": "奈良県",
    "wakayama": "和歌山県", "tottori": "鳥取県", "shimane": "島根県", "okayama": "岡山県",
    "hiroshima": "広島県", "yamaguchi": "山口県", "tokushima": "徳島県", "kagawa": "香川県",
    "ehime": "愛媛県", "kochi": "高知県", "fukuoka": "福岡県", "saga": "佐賀県", "nagasaki": "長崎県",
    "kumamoto": "熊本県", "oita": "大分県", "miyazaki": "宮崎県", "kagoshima": "鹿児島県", "okinawa": "沖縄県",
}

SNS_HOSTS = ("instagram.com", "facebook.com", "fb.com", "fb.me", "twitter.com", "x.com",
             "youtube.com", "youtu.be", "tiktok.com", "line.me", "lin.ee", "threads.net")
# 公式サイトとして採用しないホスト（掲載サイト自身・地図・カレンダー/予約の汎用サービス・通販など）
NOT_WEBSITE_HOSTS = ("reserver.co.jp", "google.com", "google.co.jp", "goo.gl", "maps.app.goo.gl",
                     "freecalend.com", "amazon.co.jp", "amazon.com", "rakuten.co.jp", "yahoo.co.jp",
                     "chowari.jp", "tsuree.jp", "jalan.net", "asoview.com", "gmail.com",
                     "fants.jp")  # fants.jp は有料ファンコミュニティ（公式サイトではない）

# 屋号らしさを示す語（これを含まない見出しは個人名とみなす）
BIZ_RE = re.compile(r"ガイド|ｶﾞｲﾄﾞ|GUIDE|SERVICE|SEVICE|サービス|フィッシング|FISHING|BASS|バス|釣り|釣|専門|"
                    r"ワールド|ガレージ|PROJECT|TRAIL|JFG|MTM|PON|ホルモン|チャーリー", re.I)
FIELD_SUFFIX_RE = re.compile(r"(湖|沼|ダム|川|池|潟|浦|ダム\(湖\))$")
# 屋号の後ろに付く一般語（これだけが残ったら屋号として弱いので湖名を前に付ける）
GENERIC_BIZ_RE = re.compile(r"^(?:バス|ブラックバス)?(?:フィッシング|釣り)?(?:ガイド)?(?:サービス)?$|"
                            r"^(?:BASS\s*)?(?:FISHING\s*)?(?:GUIDE\s*)?(?:SERVICE)?$", re.I)
KANJI_NAME_RE = re.compile(r"^[一-龥々〆ヶ]{2,5}$")
KANJI_KANA_NAME_RE = re.compile(r"^[一-龥々]{1,3}[ァ-ヶー]{2,5}$")
GLUED_NAME_RE = re.compile(r"^([一-龥々]{4,5})((?:バス|ブラックバス)?(?:フィッシング|釣り)?ガイド(?:サービス)?)$")


# ---------------------------------------------------------------- utilities

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Logger(object):
    def __init__(self, path):
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        self.f = open(path, "a", encoding="utf-8")

    def __call__(self, msg):
        line = "%s %s" % (now_str(), msg)
        self.f.write(line + "\n")
        self.f.flush()
        print(line)
        sys.stdout.flush()


def nfkc(s):
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[​-‍﻿]", "", s)


def clean_text(s):
    s = nfkc(s).replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def one_line(s):
    return re.sub(r"\s+", " ", clean_text(s)).strip()


def atomic_write(path, data, binary=False):
    tmp = path + ".tmp"
    if binary:
        with open(tmp, "wb") as f:
            f.write(data)
    else:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
    os.replace(tmp, path)


def host_of(u):
    m = re.match(r"^https?://([^/?#]+)", u or "", re.I)
    if not m:
        return ""
    h = m.group(1).lower().split("@")[-1].split(":")[0]
    return h[4:] if h.startswith("www.") else h


def host_in(h, hosts):
    return any(h == x or h.endswith("." + x) for x in hosts)


def is_sns(u):
    return host_in(host_of(u), SNS_HOSTS)


def tel_display(s):
    s = nfkc(s)
    s = re.sub(r"[()\s.・]", "-", s)
    s = re.sub(r"[‐‑–—―ー−]", "-", s)
    for m in re.finditer(r"(?<!\d)0\d{1,4}-*\d{1,4}-*\d{3,4}(?!\d)", s):
        d = re.sub(r"\D", "", m.group(0))
        if len(d) not in (10, 11):
            continue
        txt = re.sub(r"-+", "-", m.group(0)).strip("-")
        if "-" in txt:
            return txt
        if len(d) == 11 and re.match(r"^0[5789]0", d):
            return "%s-%s-%s" % (d[:3], d[3:7], d[7:])
        return d  # 固定電話は市外局番の区切りが数字だけでは決まらない
    return None


def yen(s):
    try:
        return int(re.sub(r"[^\d]", "", s))
    except ValueError:
        return None


def hhmm(h, m=None):
    h = int(h)
    m = int(m) if m else 0
    if not (0 <= h <= 24 and 0 <= m < 60):
        return ""
    return "%02d:%02d" % (h, m)


TIME_RANGE_RE = re.compile(
    r"(AM|PM|am|pm|午前|午後)?\s*(\d{1,2})\s*(?:時(?!間)|:)\s*(\d{1,2})?\s*分?\s*(?:頃|ごろ)?\s*(?:集合)?[^\d~〜～\-ー－―]{0,12}?"
    r"[~〜～\-ー－―]\s*(?:日没|日の入り?)?\s*(AM|PM|am|pm|午前|午後)?\s*(\d{1,2})\s*(?:時(?!間)|:)\s*(\d{1,2})?")
# 「午前6時集合～日没1時間前頃まで」のように開始だけ書かれている形
START_ONLY_RE = re.compile(
    r"(AM|PM|am|pm|午前|午後)?\s*(\d{1,2})\s*(?:時|:)\s*(\d{1,2})?\s*分?\s*(?:頃|ごろ)?\s*(?:集合|出船|出発|スタート)|"
    r"(?:集合|出船|出発)\s*[/:：]?\s*(AM|PM|am|pm|午前|午後)?\s*(\d{1,2})\s*(?:時|:)\s*(\d{1,2})?")


def _h12(ampm, h):
    h = int(h)
    if ampm and ampm.upper() in ("PM", "午後") and h < 12:
        h += 12
    return h


def time_ranges(s):
    """'07時~17時' '6時30分 BASE CAMP 集合～16時終了' '7:00〜12:00' 'AM7:00～PM4:00' → [(dep, ret), ...]"""
    s = nfkc(s)
    out = []
    for m in TIME_RANGE_RE.finditer(s):
        dep = hhmm(_h12(m.group(1), m.group(2)), m.group(3))
        ret = hhmm(_h12(m.group(4), m.group(5)), m.group(6))
        if dep and ret and (dep, ret) not in out:
            out.append((dep, ret))
    return out


def start_times(s):
    """開始時刻だけの記述 → ['06:00', ...]（重複なし）"""
    s = nfkc(s)
    out = []
    for m in START_ONLY_RE.finditer(s):
        if m.group(2):
            t = hhmm(_h12(m.group(1), m.group(2)), m.group(3))
        else:
            t = hhmm(_h12(m.group(4), m.group(5)), m.group(6))
        if t and t not in out:
            out.append(t)
    return out


# ---------------------------------------------------------------- fetcher

class Fetcher(object):
    def __init__(self, log, interval=1.0, refresh=False):
        self.log = log
        self.interval = max(0.8, interval)
        self.refresh = refresh
        self.last = 0.0
        self.n_net = 0
        self.n_cache = 0
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        })
        if not os.path.isdir(CACHE):
            os.makedirs(CACHE)
        self.robots = self._load_robots()

    def _load_robots(self):
        path = os.path.join(CACHE, "robots.txt")
        if not os.path.exists(path) or self.refresh:
            self._wait()
            r = self.s.get(BASE + "/robots.txt", timeout=60)
            self.last = time.time()
            self.n_net += 1
            atomic_write(path, r.content, binary=True)
        rp = robotparser.RobotFileParser()
        with open(path, encoding="utf-8", errors="replace") as f:
            rp.parse(f.read().splitlines())
        return rp

    def _wait(self):
        dt = time.time() - self.last
        if dt < self.interval:
            time.sleep(self.interval - dt)

    def get(self, url, cache_name):
        # type: (str, str) -> Tuple[Optional[str], str]
        """returns (text or None, how) how in cache|cache-missing|net|missing|httpNNN|fail"""
        if not self.robots.can_fetch("*", url):
            raise RuntimeError("robots.txt disallows %s" % url)
        path = os.path.join(CACHE, cache_name)
        miss = path + ".missing"
        if not self.refresh:
            if os.path.exists(path):
                self.n_cache += 1
                with open(path, "rb") as f:
                    return f.read().decode("utf-8", errors="replace"), "cache"
            if os.path.exists(miss):
                self.n_cache += 1
                return None, "cache-missing"
        err = ""
        for attempt in range(6):
            self._wait()
            try:
                r = self.s.get(url, timeout=60, allow_redirects=True)
            except requests.RequestException as e:
                self.last = time.time()
                err = repr(e)
                if attempt >= 5:
                    break
                wait = min(300, 5 * (2 ** attempt))
                self.log("WARN %s error %s; retry in %ss" % (url, err[:120], wait))
                time.sleep(wait)
                continue
            self.last = time.time()
            self.n_net += 1
            if r.status_code in (429, 503) or r.status_code >= 500:
                if attempt >= 5:
                    err = "http%d" % r.status_code
                    break
                wait = min(600, 10 * (2 ** attempt))
                ra = r.headers.get("Retry-After")
                if ra and ra.isdigit():
                    wait = max(wait, int(ra))
                self.log("WARN %s HTTP %d; backoff %ss" % (url, r.status_code, wait))
                time.sleep(wait)
                continue
            if r.status_code in (404, 410) or r.url.split("#")[0].rstrip("/") != url.split("#")[0].rstrip("/"):
                atomic_write(miss, json.dumps({"url": url, "final": r.url, "status": r.status_code,
                                               "at": now_str()}, ensure_ascii=False))
                return None, "missing"
            if r.status_code != 200:
                return None, "http%d" % r.status_code
            atomic_write(path, r.content, binary=True)
            return r.content.decode("utf-8", errors="replace"), "net"
        self.log("ERROR %s gave up: %s" % (url, err))
        return None, "fail"


# ---------------------------------------------------------------- listing

GUID_LOC_RE = re.compile(r"<loc>\s*https://reserver\.co\.jp/guid/([^/<\s]+)/\s*</loc>")


def sitemap_slugs(fetcher, log):
    # type: (Fetcher, Logger) -> List[str]
    xml, how = fetcher.get(BASE + "/sitemap.xml", "sitemap.xml")
    if xml is None:
        log("WARN sitemap.xml unavailable (%s)" % how)
        return []
    slugs = []
    for m in GUID_LOC_RE.finditer(xml):
        s = m.group(1)
        if s not in slugs:
            slugs.append(s)
    return slugs


def parse_index(html):
    soup = BeautifulSoup(html, "lxml")
    cards = []
    for g in soup.select("div.guidList"):
        a = g.select_one('a.guidList-reserve-btn[href^="/guid/"]')
        if a is None:
            for x in g.find_all("a", href=True):
                if re.match(r"^/guid/[^/?#]+/$", x["href"]):
                    a = x
                    break
        if a is None:
            continue
        m = re.match(r"^/guid/([^/?#]+)/", a["href"])
        if not m:
            continue
        title_el = g.select_one(".guidList-titlebox-title")
        tags = [one_line(t.get_text()) for t in g.select(".guidList-titlebox .c-tag")]
        kind = [t for t in tags if "ガイド" in t]
        fields = [t for t in tags if "ガイド" not in t]
        tel_a = g.select_one('a[href^="tel:"]')
        tel = one_line(tel_a.get_text()) if tel_a else ""
        if tel_a and not tel:
            tel = tel_a["href"][4:]
        cards.append({
            "slug": m.group(1),
            "title": one_line(title_el.get_text()) if title_el else "",
            "kind": kind[0] if kind else "",
            "fields": fields,
            "tel": tel,
        })
    pages = set()
    for x in soup.select("nav.c-pagenation a[href]"):
        pm = re.search(r"[?&]p=(\d+)", x["href"])
        if pm:
            pages.add(int(pm.group(1)))
    return cards, pages


def crawl_index(fetcher, log):
    # type: (Fetcher, Logger) -> List[dict]
    cards = []  # type: List[dict]
    seen = set()
    todo = [0]
    done = set()
    while todo:
        p = todo.pop(0)
        if p in done:
            continue
        done.add(p)
        url = BASE + "/guid/" + ("?p=%d" % p if p else "")
        name = "guid_index.html" if p == 0 else "guid_index_p%d.html" % p
        html, how = fetcher.get(url, name)
        if html is None:
            log("ERROR index p=%d failed (%s)" % (p, how))
            continue
        page_cards, pages = parse_index(html)
        new = 0
        for c in page_cards:
            if c["slug"] not in seen:
                seen.add(c["slug"])
                cards.append(c)
                new += 1
        log("index p=%d cards=%d new=%d (%s)" % (p, len(page_cards), new, how))
        if new == 0 and p > 0:
            continue
        for q in sorted(pages):
            if q not in done and q not in todo and q < 200:
                todo.append(q)
    return cards


# ---------------------------------------------------------------- detail

def strip_personal_name(title, h2, port, fields):
    """見出しから個人名を落とした表示名を返す。"""
    t = one_line(title)
    protect = set(nfkc(f) for f in fields if f)
    if port:
        protect.add(nfkc(port))
    tokens = [x for x in re.split(r"[\s【】「」『』\[\]()（）]+", t) if x]
    if not tokens:
        return h2 or ""
    if not any(BIZ_RE.search(x) for x in tokens):
        return h2 or ("%sのバス釣りガイド" % port if port else "")
    s = t
    prev_biz = False
    for x in tokens:
        if x in protect or FIELD_SUFFIX_RE.search(x):
            prev_biz = False
            continue
        if not BIZ_RE.search(x):
            if KANJI_NAME_RE.match(x) or KANJI_KANA_NAME_RE.match(x):
                s = s.replace(x, " ", 1)  # 個人名（氏・名・氏名）
            prev_biz = False
            continue
        m = GLUED_NAME_RE.match(x)
        if m and not FIELD_SUFFIX_RE.search(m.group(1)) and m.group(1) not in protect:
            # 「芳賀龍平ガイドサービス」→「ガイドサービス」。直前が屋号ならつなげる（「釣り吉ホルモンガイドサービス」）
            if prev_biz:
                s = re.sub(r"\s*" + re.escape(x), m.group(2), s, count=1)
            else:
                s = s.replace(x, m.group(2), 1)
            prev_biz = False
            continue
        prev_biz = True
    s = re.sub(r"[(（【「『\[]\s*[)）】」』\]]", " ", s)  # 空になった括弧
    s = re.sub(r"\s+", " ", s).strip()
    bm = re.match(r"^[(（【「](.+?)[)）】」](.*)$", s)
    if bm:
        s = (bm.group(1) + bm.group(2)).strip()  # 「(ラガーガイドサービス)」「(ふなっしー)ガイド」
    s = re.sub(r"\s*【\s*(.+?)\s*】\s*", r" \1 ", s).strip()  # 「JFG【ジュン フィッシング ガイドサービス】」
    core = re.sub(r"\s+", "", s)
    if not core:
        return h2 or ("%sのバス釣りガイド" % port if port else "")
    rest = core
    for f in sorted(protect, key=len, reverse=True):
        rest = rest.replace(re.sub(r"\s+", "", f), "")
    if (not rest or GENERIC_BIZ_RE.match(rest)) and port and port not in core:
        s = "%s %s" % (port, s)  # 「ガイドサービス」だけでは区別できないので湖名を前に付ける
    return s


YEN_NUM = r"(\d{1,3}(?:[,.]\d{3})+|\d{4,})"  # 「20.000円」のようにピリオド区切りもある
PAX_RE = re.compile(r"(\d)\s*(?:名|人)\s*(?:様)?\s*[:：]?\s*" + YEN_NUM + r"\s*円")
ADULT_RE = re.compile(r"大人\s*[:：]?\s*" + YEN_NUM + r"\s*円")
LABEL_PRICE_RE = re.compile(r"([^\s:：/・◆★●〇○■□◇]{1,20}?(?:プラン|コース))\s*[:：]\s*" + YEN_NUM + r"\s*円")
PLAN_KW_RE = re.compile(r"半日|ハーフ|午前|午後|ウィンター|ウインター|冬|プラン|コース|ガイド|の部|タイム|期間|シーズン")
# 見出しとして優先する語（「午前(7時〜12時)」のような時刻行より「河口湖ハーフガイド」を選ぶ）
STRONG_KW_RE = re.compile(r"半日|ハーフ|ウィンター|ウインター|冬季|冬期|限定|料金|プラン|コース|ガイド|タイム|の部")
BAD_HEAD_RE = re.compile(r"https?://|@|\d{2,4}-\d{2,4}-\d{3,4}|\d{10,11}|キャンセル|^料金$")
# 見出しではなく案内文（「ガイド2回以上の経験者のみ受付しております。」「携帯に連絡して…」）
SENTENCE_HEAD_RE = re.compile(r"受付|連絡|予約|お願い|ください|下さい|ます。?$|です。?$|。$")
SEASON_RE = re.compile(r"(\d{1,2}\s*月[^()（）]{0,12}?\d{1,2}\s*月(?:末|中旬|上旬|下旬)?)")


def price_items(line):
    """1行から料金を拾う → [(label, yen)]。label は '1名' '大人' 'プラン名'。"""
    s = nfkc(line)
    items = []
    for m in PAX_RE.finditer(s):
        items.append(("%s名" % m.group(1), yen(m.group(2))))
    if not items:
        for m in ADULT_RE.finditer(s):
            items.append(("大人", yen(m.group(1))))
    if not items:
        for m in LABEL_PRICE_RE.finditer(s):
            items.append((m.group(1), yen(m.group(2))))
    return [(a, b) for a, b in items if b and 1000 <= b <= 500000]


def clean_head(s):
    s = nfkc(s)
    s = re.split(r"・・・|\.\.\.|…", s)[0]  # 「■6時間 通常コース・・・（説明文）」
    s = re.sub(r"^[\s・◆★●〇○■□◇①-⑳\-*※]+", "", s)
    s = re.sub(r"[:：、,。\s]+$", "", s)
    s = re.sub(r"の場合は?$", "", s)
    return s.strip()


def comment_plans(comment, src_url, targets, main_key):
    """コメント欄の「半日」「冬季」などの料金ブロックを plans にする（確実に読めるものだけ）。"""
    lines = [one_line(x) for x in clean_text(comment).split("\n")]
    lines = [x for x in lines if x]
    plans = []
    heads = []  # 直前の非料金行
    i = 0
    while i < len(lines):
        line = lines[i]
        items = price_items(line)
        if not items:
            heads.append(line)
            heads = heads[-3:]
            i += 1
            continue
        # 行内に見出しがある形「・ウィンタープラン（1月～2月末）：1名様27,000円/...」
        first = PAX_RE.search(nfkc(line)) or ADULT_RE.search(nfkc(line))
        inline_head = clean_head(nfkc(line)[:first.start()]) if first else ""
        block = list(items)
        block_lines = [line]
        j = i + 1
        if not inline_head:
            while j < len(lines):
                nxt = price_items(lines[j])
                if not nxt or LABEL_PRICE_RE.search(nfkc(lines[j])) and not PAX_RE.search(nfkc(lines[j])):
                    # 「大人/中学生/小学生以下」の続き行（料金の無い行）は読み飛ばす
                    if re.match(r"^(中学生|小学生|子供|子ども|お子様|幼児)", nfkc(lines[j])):
                        j += 1
                        continue
                    break
                block.extend(nxt)
                block_lines.append(lines[j])
                j += 1
        label_price = LABEL_PRICE_RE.search(nfkc(line)) and not PAX_RE.search(nfkc(line)) and not ADULT_RE.search(nfkc(line))
        if label_price:
            # 「1日プラン:25000円」「半日プラン:18000円」は1行1プラン
            for lab, val in items:
                name = clean_head(lab)
                if val == main_key[0] and re.search(r"1日|一日|フル|通常", name):
                    continue  # 本体の料金欄と同じ内容
                p = make_plan(name, val, "%s円" % "{:,}".format(val), "", "", "", src_url, targets)
                if p:
                    plans.append(p)
            heads = []
            i += 1
            continue
        head = inline_head
        time_src = inline_head
        if not head:
            cands = [h for h in heads if not BAD_HEAD_RE.search(nfkc(h)) and not nfkc(h).startswith("※")
                     and not SENTENCE_HEAD_RE.search(clean_head(h))]
            strong = [h for h in cands if STRONG_KW_RE.search(nfkc(h))]
            weak = [h for h in cands if PLAN_KW_RE.search(nfkc(h)) and not time_ranges(h)]
            if strong:
                head = clean_head(strong[-1])
            elif weak:
                head = clean_head(weak[-1])
            elif cands and len(cands[-1]) <= 30 and not time_ranges(cands[-1]):
                head = clean_head(cands[-1])
            time_src = " ".join(heads)
        heads = []
        i = j
        if not head or len(head) > 40 or BAD_HEAD_RE.search(head):
            continue
        first_price = None
        for lab, val in block:
            if lab in ("1名", "大人"):
                first_price = val
                break
        if first_price is None:
            first_price = block[0][1]
        ptxt = " / ".join("%s %s円" % (lab, "{:,}".format(val)) for lab, val in block)
        season = ""
        sm = SEASON_RE.search(nfkc(head) + " " + nfkc(time_src))
        if sm:
            season = re.sub(r"\s+", "", sm.group(1))
        rng = time_ranges(time_src)
        name = re.sub(r"[(（][^)）]*\d+\s*月[^)）]*[)）]", "", head).strip() or head
        if len(rng) >= 2:
            # 「午前（7時〜12時） あるいは 午後（13時〜18時）」は便ごとに分ける
            words = re.findall(r"(午前|午後|朝|夕方?|ナイト)", nfkc(time_src))
            for k, (dep, ret) in enumerate(rng):
                nm = name + ("（%s）" % words[k] if k < len(words) else "（%d）" % (k + 1))
                p = make_plan(nm, first_price, ptxt, dep, ret, season, src_url, targets)
                if p:
                    plans.append(p)
        else:
            dep, ret = rng[0] if rng else ("", "")
            p = make_plan(name, first_price, ptxt, dep, ret, season, src_url, targets)
            if p:
                plans.append(p)
    uniq = []
    for p in plans:
        if p not in uniq:
            uniq.append(p)
    return uniq


def make_plan(name, price, price_text, dep, ret, season, src_url, targets):
    if not name:
        return None
    return {
        "name": name[:40],
        "kind": "ガイド",
        "targets": list(targets),
        "price": price,
        "price_text": price_text,
        "depart": dep or "",
        "return": ret or "",
        "meet": "",
        "season": season or "",
        "days": "",
        "includes": "",
        "url": src_url,
    }


SCHEDULE_RE = re.compile(r"営業日|定休日|休業日|土日|平日|祝日")


def schedule_lines(comment):
    out = []
    for x in clean_text(comment).split("\n"):
        x = one_line(x)
        if not x or not SCHEDULE_RE.search(x) or BAD_HEAD_RE.search(x) or "円" in x:
            continue
        x = re.sub(r"^[\s・◆★●〇○■□◇※]+", "", x)
        if len(x) > 60:
            continue
        if x not in out:
            out.append(x)
    s = " / ".join(out)
    return s[:150]


def instagram_profile(u, text):
    """埋め込み投稿なら '(@handle)' からプロフィールURLを作る。プロフィールURLはそのまま。"""
    m = re.match(r"^https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]{1,30})/?(?:[?#].*)?$", u or "")
    if m and m.group(1) not in ("p", "reel", "tv", "explore", "stories"):
        return "https://www.instagram.com/%s/" % m.group(1)
    hm = re.search(r"\(@([A-Za-z0-9._]{1,30})\)", text or "")
    if hm:
        return "https://www.instagram.com/%s/" % hm.group(1)
    return None


def norm_sns(u):
    u = nfkc(u).strip()
    if not re.match(r"^https?://", u, re.I):
        return None
    h = host_of(u)
    if host_in(h, ("twitter.com", "x.com")):
        m = re.match(r"^https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/([A-Za-z0-9_]{1,30})", u)
        if m and m.group(1).lower() not in ("intent", "share", "home", "i", "search"):
            return "https://x.com/%s" % m.group(1)
        return None
    if host_in(h, ("youtube.com",)):
        m = re.match(r"^https?://(?:www\.|m\.)?youtube\.com/((?:@|channel/|c/|user/)[^/?#\s]+)", u)
        return ("https://www.youtube.com/%s" % m.group(1)) if m else None  # 動画単体は載せない
    if host_in(h, ("instagram.com",)):
        return instagram_profile(u, "")
    if host_in(h, ("facebook.com", "fb.com", "line.me", "lin.ee", "tiktok.com", "threads.net")):
        return re.sub(r"[?#].*$", "", u) if "line.me/R/ti" not in u else u
    return None


def parse_detail(html, slug, card=None, fetched=None, keep_shore=False):
    # type: (str, str, Optional[dict], Optional[str], bool) -> Tuple[Optional[dict], str]
    card = card or {}
    soup = BeautifulSoup(html, "lxml")
    main = soup.select_one("main.main") or soup.select_one("main")
    if main is None:
        return None, "no-main"
    h1 = main.select_one("h1")
    if h1 is None:
        return None, "no-h1"
    src_url = "%s/guid/%s/" % (BASE, slug)
    title = one_line(h1.get_text())
    h2_el = main.select_one("h2.c-title._title-md") or main.select_one("h2")
    h2 = one_line(h2_el.get_text()) if h2_el else ""
    if "ガイド" not in h2:
        h2 = ""

    kind_el = main.select_one(".guidInfo-titlebox-tag") or main.select_one(".c-titlebox .c-tag")
    kind = one_line(kind_el.get_text()) if kind_el else (card.get("kind") or "")
    if "おかっぱり" in kind and not keep_shore:
        return None, "shore"

    info = []  # [(label, value)]
    for li in main.select(".guidInfo-tagList li"):
        t_el = li.select_one(".c-tagList-item-title")
        label = one_line(t_el.get_text()) if t_el else ""
        full = one_line(li.get_text(" "))
        val = full[len(label):].strip() if label and full.startswith(label) else full
        info.append((label, val))

    # パンくず: 県・メインのフィールド
    pref, main_field = "", ""
    for a in soup.select(".breadcrumbs a[href]"):
        href = a["href"]
        nm = one_line(a.get_text())
        m2 = re.search(r"/area/([a-z]+)/([^/]+)/?$", href)
        m1 = re.search(r"/area/([a-z]+)/?$", href)
        if m2:
            main_field = nm
            pref = pref or PREF_ROMA.get(m2.group(1), "")
        elif m1:
            pref = nm if nm in PREF_NAMES else PREF_ROMA.get(m1.group(1), "")

    fields = []
    price_rows = []
    time_text = ""
    for label, val in info:
        if label.startswith("料金"):
            price_rows.append(val)
        elif label.startswith("時間"):
            time_text = val
        elif "フィールド" in label:
            fields = [one_line(x) for x in re.split(r"\s*/\s*|、", val) if one_line(x)]
    if not fields:
        fields = list(card.get("fields") or [])
    port = main_field or (fields[0] if fields else "")
    if port and port not in fields:
        fields.insert(0, port)

    targets = ["ブラックバス"] if ("バス釣り" in h2 or "バス" in title or "BASS" in title.upper()) else []

    # 本体の料金（人数別）
    pax = []
    for row in price_rows:
        for m in PAX_RE.finditer(nfkc(row)):
            pax.append((int(m.group(1)), yen(m.group(2)), row))
    plans = []
    rng = time_ranges(time_text)
    dep, ret = rng[0] if rng else ("", "")
    main_price = None
    if pax:
        pax.sort(key=lambda x: x[0])
        main_price = pax[0][1] if pax[0][0] == 1 else None
        ptxt = " / ".join(re.sub(r"\s+", "", nfkc(r)).replace("：", " ").replace(":", " ") for _, _, r in pax)
        ptxt = ptxt + "（税込）"
        plans.append({
            "name": nfkc(kind) if kind else "ガイド",
            "kind": "ガイド",
            "targets": list(targets),
            "price": main_price,
            "price_text": ptxt,
            "depart": dep,
            "return": ret,
            "meet": "",
            "season": "",
            "days": "",
            "includes": "",
            "url": src_url,
        })

    dd = main.select_one(".guidInfo-description-detail")
    comment = dd.get_text("\n") if dd else ""
    if plans and not dep:
        # 時間目安の欄が無いときは、コメントの「6時集合」「出船/AM7:00」が1通りだけなら出船時刻にする
        rng_c = time_ranges(comment)
        st = start_times(comment)
        if len(rng_c) == 1 and not st or len(rng_c) == 1 and st == [rng_c[0][0]]:
            dep, ret = rng_c[0]
            plans[0]["depart"], plans[0]["return"] = dep, ret
        elif not rng_c and len(st) == 1:
            dep = st[0]
            plans[0]["depart"] = dep
    plans.extend(comment_plans(comment, src_url, targets, (main_price, dep, ret)))

    # SNS / 公式サイト（サイドバーはガイドごとの内容）
    sns = []
    websites = []

    def add_sns(u):
        if u and u not in sns:
            sns.append(u)

    aside = soup.select_one("aside.sideNav") or soup
    for bq in aside.select(".c-sideInsta blockquote"):
        add_sns(instagram_profile(bq.get("data-instgrm-permalink", ""), bq.get_text(" ")))
    for a in aside.select(".c-sideInsta a[href], .c-sideTwitter a[href], .c-sideYoutube a[href]"):
        u = a["href"]
        if "instagram.com" in u:
            add_sns(instagram_profile(u, a.get_text(" ")))
        else:
            add_sns(norm_sns(u))
    other = aside.find(lambda t: t.name in ("p", "h2", "h3") and "その他のリンク" in t.get_text())
    if other is not None:
        for a in other.find_all_next("a", href=True):
            if a.find_parent("aside") is not aside:
                break
            u = nfkc(a["href"]).strip()
            if not re.match(r"^https?://", u, re.I):
                continue
            if is_sns(u):
                add_sns(norm_sns(u))
            elif not host_in(host_of(u), NOT_WEBSITE_HOSTS):
                websites.append(u)
    for m in re.finditer(r"https?://[^\s<>\"'　、。）)]+", nfkc(comment)):
        u = m.group(0)
        if is_sns(u):
            add_sns(norm_sns(u))
        elif not host_in(host_of(u), NOT_WEBSITE_HOSTS):
            websites.append(u)
    sns = [u for u in sns if u]
    website = websites[0] if websites else None

    tel = tel_display(card.get("tel") or "")

    name = strip_personal_name(title, h2, port, fields)

    desc = "湖・ダムのバス釣りガイド" if targets else "湖・ダムの釣りガイド"
    if kind:
        desc += "（%s）" % kind
    if fields:
        desc += "。対応フィールド: " + "・".join(fields)
    if time_text:
        desc += "。時間目安 " + time_text
    if len(desc) > 100:
        desc = desc[:99] + "…"

    rec = {
        "src": "reserver",
        "src_id": slug,
        "src_url": src_url,
        "name": name,
        "kana": "",
        "pref": pref or None,
        "city": None,
        "address": None,
        "port": port or None,
        "lat": None,
        "lon": None,
        "tel": tel,
        "website": website,
        "sns": sns,
        "types": ["ガイド"],
        "targets": targets,
        "methods": [],
        "holidays": "",
        "facilities": [],
        "capacity": None,
        "access": "",
        "description": desc,
        "plans": plans,
        "schedule_text": schedule_lines(comment),
        "fetched": fetched or datetime.date.today().isoformat(),
    }
    return rec, "ok"


# ---------------------------------------------------------------- main

def save(records, out_path):
    d = os.path.dirname(out_path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    atomic_write(out_path, json.dumps(records, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description="reserver.co.jp guide crawler")
    ap.add_argument("--limit", type=int, default=0, help="一覧の先頭N件だけ")
    ap.add_argument("--ids", default="", help="指定slugのみ (例: nakata,uentsu)")
    ap.add_argument("--out", default=OUT_PATH, help="出力JSON")
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    ap.add_argument("--interval", type=float, default=1.0, help="リクエスト間隔(秒, 下限0.8)")
    ap.add_argument("--keep-shore", action="store_true", help="おかっぱり（岸釣り）ガイドも出力する")
    ap.add_argument("--log", default=LOG_PATH)
    args = ap.parse_args()

    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    log = Logger(args.log)
    f = Fetcher(log, interval=args.interval, refresh=args.refresh)
    log("START reserver out=%s limit=%s ids=%s refresh=%s" % (out_path, args.limit, args.ids or "-", args.refresh))

    cards = crawl_index(f, log)
    card_by = {c["slug"]: c for c in cards}
    sm = sitemap_slugs(f, log)
    order = [c["slug"] for c in cards] + [s for s in sm if s not in card_by]
    stale = set(s for s in sm if s not in card_by)
    log("listing: index=%d sitemap=%d union=%d stale(sitemap-only)=%d index-only=%d" % (
        len(cards), len(sm), len(order), len(stale), len([c for c in card_by if c not in set(sm)])))

    if args.ids:
        want = [x.strip() for x in re.split(r"[,\s]+", args.ids) if x.strip()]
        slugs = want
    else:
        slugs = order
        if args.limit:
            slugs = slugs[:args.limit]

    total = len(slugs)
    records = []
    stats = {"net": 0, "cache": 0, "missing": 0, "fail": 0, "parse_err": 0, "shore": 0}
    t0 = time.time()
    for n, slug in enumerate(slugs, 1):
        url = "%s/guid/%s/" % (BASE, slug)
        cache_name = "guid_%s.html" % re.sub(r"[^A-Za-z0-9_.-]", "_", slug)
        html, how = f.get(url, cache_name)
        if html is None:
            if how in ("missing", "cache-missing"):
                stats["missing"] += 1
                log("MISSING %s" % slug)
            else:
                stats["fail"] += 1
                log("ERROR slug=%s %s" % (slug, how))
        else:
            stats["net" if how == "net" else "cache"] += 1
            path = os.path.join(CACHE, cache_name)
            fetched = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
            try:
                rec, why = parse_detail(html, slug, card_by.get(slug), fetched, args.keep_shore)
            except Exception as e:  # noqa
                rec, why = None, "exception %r" % e
            if rec is None:
                if why == "shore":
                    stats["shore"] += 1
                    log("SKIP %s おかっぱり（岸釣り）ガイド" % slug)
                else:
                    stats["parse_err"] += 1
                    log("ERROR parse slug=%s: %s" % (slug, why))
            else:
                if slug in stale:
                    rec["stale"] = True
                records.append(rec)
        if n % 100 == 0:
            save(records, out_path)
        if n % 10 == 0 or n == total:
            log("detail %d/%d records=%d net=%d cache=%d missing=%d fail=%d parse_err=%d shore=%d elapsed=%ds" % (
                n, total, len(records), stats["net"], stats["cache"], stats["missing"], stats["fail"],
                stats["parse_err"], stats["shore"], time.time() - t0))
    save(records, out_path)
    log("requests net=%d cache=%d" % (f.n_net, f.n_cache))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
