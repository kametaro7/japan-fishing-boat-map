#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""つりー (https://tsuree.jp/) 釣り船クローラ

列挙: 一覧 /boats/search を POST の cur_page=0,30,60,... でページング（全国 1,778 件前後）。
詳細: /boats/detail/<id> を1件ずつ取得。「釣り物」表（業種/釣り物/出船時刻/料金/備考）は
      詳細ページ内の基本情報タブにあるので追加リクエスト不要。地図タブの
      google.maps.LatLng(lat, lon) を座標として採用。
作法: 1ホスト直列・間隔 --interval 秒（既定1.0, 下限0.8）・429/503 は指数バックオフ(最大5回)・
      生HTMLは work/cache/tsuree/ に保存し再実行時はキャッシュを使う（--refresh で再取得）。
      robots.txt の Disallow (/boats/*_form/, /boats/like/, /boats/boat_edit/ 等) は叩かない。

使い方:
  python3 tools/scrape_tsuree.py                 # 全件
  python3 tools/scrape_tsuree.py --limit 50      # 一覧の先頭50件
  python3 tools/scrape_tsuree.py --ids 1,100,200-210 --out work/sources/tsuree.sample.json
  python3 tools/scrape_tsuree.py --probe-gaps    # 一覧に無いIDも 1..最大ID+50 で確認
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

BASE = "https://tsuree.jp"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "work", "cache", "tsuree")
LOG_PATH = os.path.join(ROOT, "work", "logs", "tsuree.log")
OUT_PATH = os.path.join(ROOT, "work", "sources", "tsuree.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
PAGE_SIZE = 30

PREFS = {
    1: "北海道", 2: "青森県", 3: "岩手県", 4: "宮城県", 5: "秋田県", 6: "山形県", 7: "福島県",
    8: "茨城県", 9: "栃木県", 10: "群馬県", 11: "埼玉県", 12: "千葉県", 13: "東京都", 14: "神奈川県",
    15: "新潟県", 16: "富山県", 17: "石川県", 18: "福井県", 19: "山梨県", 20: "長野県", 21: "岐阜県",
    22: "静岡県", 23: "愛知県", 24: "三重県", 25: "滋賀県", 26: "京都府", 27: "大阪府", 28: "兵庫県",
    29: "奈良県", 30: "和歌山県", 31: "鳥取県", 32: "島根県", 33: "岡山県", 34: "広島県", 35: "山口県",
    36: "徳島県", 37: "香川県", 38: "愛媛県", 39: "高知県", 40: "福岡県", 41: "佐賀県", 42: "長崎県",
    43: "熊本県", 44: "大分県", 45: "宮崎県", 46: "鹿児島県", 47: "沖縄県",
}
PREF_NAMES = list(PREFS.values())
# 一覧の「北海道 / 頓別港」のような短縮県名 → 正式名
SHORT_PREF = {}
for _n in PREF_NAMES:
    SHORT_PREF[_n] = _n
    if _n != "北海道":
        SHORT_PREF[_n[:-1]] = _n

SNS_HOSTS = ("instagram.com", "facebook.com", "fb.com", "fb.me", "twitter.com", "x.com",
             "youtube.com", "youtu.be", "tiktok.com", "line.me", "lin.ee", "threads.net",
             "note.com")
NOT_WEBSITE_HOSTS = ("tsuree.jp",)

# 「市」「町」「村」を名前に含む市区町村（先頭の簡易正規表現で切れないもの）
CITY_EXCEPTIONS = [
    "四日市市", "廿日市市", "市川市", "市原市", "野々市市", "町田市", "大町市", "十日町市",
    "村上市", "村山市", "東村山市", "武蔵村山市", "羽村市", "大村市", "田村市", "北村山郡",
    "西村山郡", "東村山郡", "中新川郡上市町", "余市郡余市町", "市貝町", "上市町", "余市町",
    "玉村町", "木曽郡上松町",
]


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
    return unicodedata.normalize("NFKC", s or "")


def clean_text(s):
    s = (s or "").replace("　", " ").replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def one_line(s):
    return re.sub(r"\s+", " ", clean_text(s)).strip()


def kata_to_hira(s):
    out = []
    for ch in s or "":
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:
            out.append(chr(o - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def atomic_write(path, data, binary=False):
    tmp = path + ".tmp"
    mode = "wb" if binary else "w"
    if binary:
        with open(tmp, mode) as f:
            f.write(data)
    else:
        with open(tmp, mode, encoding="utf-8") as f:
            f.write(data)
    os.replace(tmp, path)


def parse_ids(spec):
    ids = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        m = re.match(r"^(\d+)-(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            ids.extend(range(a, b + 1))
        else:
            ids.append(int(part))
    seen = set()
    res = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            res.append(i)
    return res


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
        if not os.path.exists(path):
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

    def get(self, url, cache_name, data=None, referer=None):
        # type: (str, str, Optional[dict], Optional[str]) -> Tuple[Optional[str], str]
        """returns (html or None, how) how in cache|cache-missing|net|missing|httpNNN|fail"""
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
                headers = {}
                if referer:
                    headers["Referer"] = referer
                if data is None:
                    r = self.s.get(url, timeout=60, headers=headers, allow_redirects=True)
                else:
                    r = self.s.post(url, data=data, timeout=60, headers=headers, allow_redirects=True)
            except requests.RequestException as e:
                self.last = time.time()
                err = repr(e)
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
            if r.status_code == 404 or (data is None and r.url.split("#")[0].rstrip("/") != url.rstrip("/")):
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

def parse_listing(html):
    soup = BeautifulSoup(html, "lxml")
    total = None
    g = soup.select_one("h2.result_title span.green_name")
    if g:
        m = re.search(r"(\d[\d,]*)", g.get_text())
        if m:
            total = int(m.group(1).replace(",", ""))
    items = []
    for a in soup.select("table.search_result_table a.boat_name"):
        m = re.search(r"/boats/detail/(\d+)", a.get("href", ""))
        if not m:
            continue
        box = a.find_parent("td")
        port_icon = box.select_one("span.port_icon") if box else None
        tel_icon = box.select_one("span.tel1_icon") if box else None
        pref_s, port = "", ""
        if port_icon:
            parts = [p.strip() for p in port_icon.get_text().split("/", 1)]
            pref_s = parts[0]
            port = parts[1] if len(parts) > 1 else ""
        items.append({
            "id": int(m.group(1)),
            "name": one_line(a.get_text()),
            "pref": SHORT_PREF.get(pref_s, ""),
            "port": port,
            "tel": one_line(tel_icon.get_text()) if tel_icon else "",
        })
    return total, items


def crawl_listing(fetcher, log):
    # type: (Fetcher, Logger) -> List[dict]
    url = BASE + "/boats/search"
    items = []  # type: List[dict]
    seen = set()
    offset = 0
    total = None
    while True:
        html, how = fetcher.get(url, "search_%d.html" % offset,
                                data={"prefecture_data": "", "cur_page": str(offset), "free_word": ""},
                                referer=url)
        if html is None:
            log("ERROR listing offset=%d failed (%s)" % (offset, how))
            break
        t, page_items = parse_listing(html)
        if t is not None:
            total = t if total is None else max(total, t)
        new = 0
        for it in page_items:
            if it["id"] not in seen:
                seen.add(it["id"])
                items.append(it)
                new += 1
        npages = ((total or 0) + PAGE_SIZE - 1) // PAGE_SIZE
        if offset % (PAGE_SIZE * 10) == 0 or offset + PAGE_SIZE >= (total or 0):
            log("list %d/%d pages (boats %d/%s, %s)" % (offset // PAGE_SIZE + 1, npages, len(items), total, how))
        if not page_items:
            log("WARN listing offset=%d returned no items" % offset)
            break
        offset += PAGE_SIZE
        if total is None or offset >= total:
            break
    if total is not None and len(items) < total:
        log("WARN listing collected %d ids < total %d" % (len(items), total))
    return items


# ---------------------------------------------------------------- detail parsing

PHONE_RE = re.compile(r"(0\d{1,4}-\d{1,4}-\d{3,4}|0\d{9,10})")


def norm_tel(s):
    s = nfkc(s)
    s = re.sub(r"[‐‑‒–—―ー−]", "-", s)
    s = re.sub(r"\(\s*(\d{2,5})\s*\)", r"-\1-", s)  # 0185(33)2702 → 0185-33-2702
    s = s.replace(" ", "")
    m = PHONE_RE.search(s)
    if not m:
        return ""
    t = m.group(1).strip("-")
    if "-" not in t and len(t) == 11 and t[:3] in ("090", "080", "070", "050"):
        t = "%s-%s-%s" % (t[:3], t[3:7], t[7:])
    return t


def host_of(url):
    m = re.match(r"^https?://([^/:?#]+)", url.strip(), re.I)
    return m.group(1).lower() if m else ""


def is_sns(url):
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in SNS_HOSTS)


def valid_url(url):
    h = host_of(url)
    if not h or "." not in h:
        return False
    return not any(h == d or h.endswith("." + d) for d in NOT_WEBSITE_HOSTS)


def split_list(s):
    s = one_line(s)
    if not s:
        return []
    parts = re.split(r"[、，,/／]+", s)
    res = []
    for p in parts:
        p = p.strip(" ・")
        if p and p not in res:
            res.append(p)
    return res


def extract_city(addr, pref):
    rest = addr[len(pref):] if pref and addr.startswith(pref) else addr
    if not rest:
        return ""
    for ex in CITY_EXCEPTIONS:
        if rest.startswith(ex):
            # 郡 のみの例外は続く町村まで含める
            if ex.endswith("郡"):
                m = re.match(re.escape(ex) + r"([^市区町村]{1,6}[町村])", rest)
                return ex + m.group(1) if m else ex
            return ex
    m = re.match(r"^([^市区町村郡]{1,6}郡[^市区町村]{1,6}?[町村])", rest)
    if m:
        return m.group(1)
    m = re.match(r"^([^市区町村郡]{1,7}?市[^市区町村]{1,5}?区)", rest)
    if m and m.group(1) and len(m.group(1)) <= 9:
        return m.group(1)
    m = re.match(r"^([^市区町村郡]{1,7}?[市区町村])", rest)
    if m:
        c = m.group(1)
        # 東京都の島嶼部「八丈島八丈町」「三宅島三宅村」→ 町村名
        mi = re.match(r"^.{1,3}島(.{1,4}[町村])$", c)
        if pref == "東京都" and mi:
            return mi.group(1)
        return c
    return ""


def normalize_address(raw, pref):
    a = one_line(raw)
    a = re.sub(r"^〒?\s*\d{3}\s*[-－ー]\s*\d{4}\s*", "", a)
    a = a.strip(" 　")
    if not a:
        return ""
    if re.fullmatch(r"(なし|無し|未定|非公開|不明|未記入|未登録|[-－ー―・.。]+)", a):
        return ""
    if not re.search(r"[一-龥ぁ-んァ-ン々]", a):
        return ""
    if pref and not any(a.startswith(p) for p in PREF_NAMES):
        a = pref + a
    return a


TIME_RE = re.compile(
    r"(午前|午後|AM|PM|am|pm)?\s*(\d{1,2})\s*(?::|時(?!間))\s*(\d{1,2}|半)?\s*分?")


def find_times(s):
    """returns list of (HH:MM, start, end)"""
    s = nfkc(s)
    res = []
    for m in TIME_RE.finditer(s):
        ap, h, mi = m.group(1), int(m.group(2)), m.group(3)
        # 「5:30」「5時」「5時30分」だけ採用（「3名」「1隻」等を拾わない）
        if ":" not in m.group(0) and "時" not in m.group(0):
            continue
        if mi == "半":
            mm = 30
        elif mi is None or mi == "":
            mm = 0
        else:
            mm = int(mi)
        if ap and ap.lower() in ("午後", "pm") and h < 12:
            h += 12
        if h > 24 or mm > 59:
            continue
        res.append(("%02d:%02d" % (h, mm), m.start(), m.end()))
    return res


AMOUNT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*万\s*(?:(\d)\s*千|(\d{1,4}))?\s*円?"
    r"|(\d{1,3})\s*千\s*円"
    r"|[¥\\]\s*(\d{1,3}(?:,\d{3})+|\d{3,6})(?![\d.,])"
    r"|(\d{1,3}(?:[,.]\d{3})+|\d+)\s*円"
    r"|(?<![\d.,])(\d{1,3}(?:,\d{3})+)(?![\d.,])"          # 10,000 / 12,000- （円なし）
    r"|(?<![\d.,])(\d{4,6})\s*-(?!\s*\d)")                 # 9000- （円なし）


def parse_amounts(s):
    """料金テキストから金額(円)を抽出。曖昧な表記（1.1000円 など）は None を入れる"""
    s = nfkc(s)  # ￥ → ¥, 全角数字 → 半角, － → -
    s = re.sub(r"(\d)、(\d{3})(?!\d)", r"\1,\2", s)  # ４０、０００円
    out = []
    for m in AMOUNT_RE.finditer(s):
        if m.group(1):
            try:
                v = float(m.group(1)) * 10000
                if m.group(2):
                    v += int(m.group(2)) * 1000
                elif m.group(3):
                    v += int(m.group(3))
                out.append((int(round(v)), m.start(), m.end()))
            except ValueError:
                out.append((None, m.start(), m.end()))
            continue
        if m.group(4):
            out.append((int(m.group(4)) * 1000, m.start(), m.end()))
            continue
        if m.group(5):
            out.append((int(m.group(5).replace(",", "")), m.start(), m.end()))
            continue
        num = m.group(6) or m.group(7) or m.group(8)
        # 直前が数字＋ピリオドなど（例: 1.1000円 の "1000円"）は曖昧
        pre = s[max(0, m.start() - 2):m.start()]
        if re.search(r"\d[.,]$", pre):
            out.append((None, m.start(), m.end()))
            continue
        digits = re.sub(r"[,.]", "", num)
        out.append((int(digits), m.start(), m.end()))
    return out


PER_BOAT_RE = re.compile(r"(隻|貸切|貸し切り|チャーター|艇|まで|迄)")
# 1人あたりを明示する表記（仕立の行ではこれが無ければ price は入れない）
PER_PERSON_STRICT_RE = re.compile(
    r"(/\s*(?:1|一)?\s*(?:人|名)|@|お\s*(?:1|一)\s*人|(?:1|一)\s*(?:人|名)\s*(?:あたり|当たり|につき))")
# 乗合の行で 1隻表記と併記されていても 1人料金とみなせる表記（「1名～6名」「1人増」は除く）
PER_PERSON_RE = re.compile(
    r"(/\s*(?:1|一)?\s*(?:人|名)|@|(?:お|御)?\s*(?:1|一)\s*(?:人|名)\s*様?(?!\s*[～〜~\-増迄ま])|大人|男性)")


def plan_price(price_text, kind):
    """1人あたり基本料金（最初に出てくる金額）。1隻料金・人数別の仕立料金・曖昧表記は None"""
    t = nfkc(price_text)
    if not t:
        return None
    t = re.sub(r"(\d)、(\d{3})(?!\d)", r"\1,\2", t)
    amounts = parse_amounts(t)
    if not amounts:
        return None
    v, st, en = amounts[0]
    if v is None:
        return None
    ctx = t[max(0, st - 12):st] + t[st:en] + t[en:en + 14]
    if kind == "仕立":
        if not PER_PERSON_STRICT_RE.search(ctx):
            return None
    else:
        if PER_BOAT_RE.search(ctx) and not PER_PERSON_RE.search(ctx):
            return None
    if v < 500 or v > 100000:
        return None
    return v


def norm_kind(s):
    s = one_line(s)
    s2 = re.sub(r"[（(].*?[)）]", "", s).strip()
    if "乗合" in s2 or "乗り合い" in s2:
        return "乗合"
    if "仕立" in s2 or "貸切" in s2 or "チャーター" in s2:
        return "仕立"
    return s2


def _minutes(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def split_departures(dep_text):
    """出船時刻欄 → [(depart, return, meet)]
    - 「7:00～12:00」(4時間以上) は出船〜帰港。「3:00～6:00」(4時間未満) は出船時刻の幅とみなし return は入れない
    - 「集合 5:50」「受付 5:00」は meet
    - 「午前便 6:00 午後便 13:00」のように『便』の語があり時刻が複数なら plans を分ける
    - 季節による時刻違い（春夏 5:00～6:00 冬 6:00～7:00 等）は最初の時刻を採用"""
    t = nfkc(dep_text)
    meet = None
    dep_times = []
    for hm, st, en in find_times(t):
        if meet is None and re.search(r"(集合|受付)\s*$", t[max(0, st - 6):st]):
            meet = hm
        else:
            dep_times.append((hm, st, en))
    if not dep_times:
        return [(None, None, meet)]
    pairs = []
    i = 0
    while i < len(dep_times):
        d = dep_times[i]
        if i + 1 < len(dep_times):
            nxt = dep_times[i + 1]
            between = t[d[2]:nxt[1]]
            if re.fullmatch(r"\s*(?:頃|ごろ|位|くらい)?\s*[～〜~\-ー−]\s*(?:帰港)?\s*", between):
                span = _minutes(nxt[0]) - _minutes(d[0])
                pairs.append((d[0], nxt[0] if span >= 240 else None))
                i += 2
                continue
        pairs.append((d[0], None))
        i += 1
    if len(pairs) > 1 and "便" in t:
        uniq = []
        for p in pairs:
            if p not in uniq:
                uniq.append(p)
        return [(p[0], p[1], meet) for p in uniq]
    return [(pairs[0][0], pairs[0][1], meet)]


def parse_plans(soup, src_url, boat_targets):
    plans = []
    for tbl in soup.select("#tab_kihon_c .data-table_2 table"):
        rows = tbl.find_all("tr")
        if not rows:
            continue
        head = [one_line(th.get_text()) for th in rows[0].find_all("th")]
        if not head:
            continue
        col = {}
        for i, h in enumerate(head):
            col[h] = i
        for tr in rows[1:]:
            tds = tr.find_all("td")
            if not tds:
                continue

            def cell(label):
                i = col.get(label)
                if i is None or i >= len(tds):
                    return ""
                return one_line(tds[i].get_text(" "))

            kind = norm_kind(cell("業種"))
            name = cell("釣り物")
            dep = cell("出船時刻")
            price_raw = cell("料金")
            note = cell("備考")
            if not (name or price_raw or dep):
                continue
            price_text = price_raw
            if note:
                price_text = (price_raw + "（備考: %s）" % note) if price_raw else "（備考: %s）" % note
            price = plan_price(price_raw, kind)
            tg = [x for x in boat_targets if x and x in name]
            for depart, ret, meet in split_departures(dep):
                p = {
                    "name": name,
                    "kind": kind,
                    "targets": tg,
                    "price": price,
                    "price_text": price_text,
                    "depart": depart,
                    "return": ret,
                    "meet": meet or "",
                    "season": "",
                    "days": "",
                    "includes": "",
                    "url": src_url,
                }
                if p not in plans:
                    plans.append(p)
    return plans


LATLNG_RE = re.compile(r"google\.maps\.LatLng\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)")


def parse_detail(html, bid, listing=None, fetched=None):
    # type: (str, int, Optional[dict], Optional[str]) -> Optional[dict]
    listing = listing or {}
    soup = BeautifulSoup(html, "lxml")
    name_el = soup.select_one("h2.boat_main_title")
    if not name_el:
        return None
    src_url = "%s/boats/detail/%d" % (BASE, bid)
    name = one_line(name_el.get_text())
    kana_el = soup.select_one("h2.boat_main_title_post")
    kana = kata_to_hira(one_line(kana_el.get_text())) if kana_el else ""
    port_el = soup.select_one("h2.boat_main_title_pre")
    port = one_line(port_el.get_text()) if port_el else (listing.get("port") or "")

    pref = ""
    for a in soup.select("#breadcrumbs_cont a"):
        m = re.search(r"/boats/prefecture/(\d+)", a.get("href", ""))
        if m:
            pref = PREFS.get(int(m.group(1)), "") or SHORT_PREF.get(one_line(a.get_text()), "")
    if not pref:
        pref = listing.get("pref") or ""

    info = {}  # label -> td
    for tbl in soup.select(".data-table_3 table"):
        for tr in tbl.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th is None or td is None:
                continue
            label = re.sub(r"\s+", "", th.get_text())
            if label not in info:
                info[label] = td

    def txt(label):
        td = info.get(label)
        return clean_text(td.get_text("\n")) if td is not None else ""

    # TEL
    tel = ""
    for label in ("TEL1", "TEL", "TEL2", "電話", "電話番号"):
        if label in info:
            tel = norm_tel(txt(label))
            if tel:
                break
    if not tel and listing.get("tel"):
        tel = norm_tel(listing["tel"])

    # WEB / HP / ブログ
    urls_hp, urls_blog, urls_other = [], [], []
    for label, bucket in (("HP", urls_hp), ("ブログ", urls_blog), ("WEB", urls_other)):
        td = info.get(label)
        if td is None:
            continue
        found = False
        for a in td.find_all("a", href=True):
            href = a["href"].strip()
            cls = " ".join(a.get("class") or [])
            if label == "WEB":
                if "hp_icon" in cls:
                    urls_hp.append(href)
                elif "blog_icon" in cls:
                    urls_blog.append(href)
                else:
                    urls_other.append(href)
            else:
                bucket.append(href)
            found = True
        if not found and label != "WEB":
            m = re.search(r"https?://[^\s<>\"'　]+", td.get_text())
            if m:
                bucket.append(m.group(0))
    website = None
    sns = []
    for u in urls_hp + urls_blog + urls_other:
        u = u.strip()
        if not re.match(r"^https?://", u, re.I) or not valid_url(u):
            continue
        if is_sns(u):
            if u not in sns:
                sns.append(u)
        elif website is None:
            website = u

    types = []
    for p in split_list(txt("業種")):
        k = norm_kind(p)
        if k and k not in types:
            types.append(k)
    targets = split_list(txt("主なターゲット"))
    methods = split_list(txt("釣り方"))
    holidays = one_line(txt("定休日"))

    address = normalize_address(txt("所在地"), pref)
    city = extract_city(address, pref) if address else ""

    lat = lon = None
    m = LATLNG_RE.search(html)
    if m:
        la, lo = float(m.group(1)), float(m.group(2))
        if 20.0 <= la <= 46.6 and 122.0 <= lo <= 154.0:
            lat, lon = round(la, 7), round(lo, 7)

    car = one_line(txt("車の場合"))
    pub = one_line(txt("公共交通機関の場合"))
    acc = []
    if car:
        acc.append("車: " + car)
    if pub:
        acc.append("公共交通機関: " + pub)
    access = " ／ ".join(acc)

    feature = one_line(txt("特徴"))
    seat = one_line(txt("席順"))
    ice = one_line(txt("氷"))
    desc_parts = []
    if feature:
        desc_parts.append(feature if len(feature) <= 60 else feature[:59] + "…")
    if seat:
        desc_parts.append("席順: " + seat)
    if ice:
        desc_parts.append("氷: " + ice)
    description = " ／ ".join(desc_parts)
    if len(description) > 100:
        description = description[:99] + "…"

    plans = parse_plans(soup, src_url, targets)

    return {
        "src": "tsuree",
        "src_id": str(bid),
        "src_url": src_url,
        "name": name,
        "kana": kana,
        "pref": pref or None,
        "city": city or None,
        "address": address or None,
        "port": port or None,
        "lat": lat,
        "lon": lon,
        "tel": tel or None,
        "website": website,
        "sns": sns,
        "types": types,
        "targets": targets,
        "methods": methods,
        "holidays": holidays,
        "facilities": [],
        "capacity": None,
        "access": access,
        "description": description,
        "plans": plans,
        "schedule_text": "",
        "fetched": fetched or datetime.date.today().isoformat(),
    }


# ---------------------------------------------------------------- main

def save(records, out_path):
    d = os.path.dirname(out_path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    atomic_write(out_path, json.dumps(records, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description="tsuree.jp crawler")
    ap.add_argument("--limit", type=int, default=0, help="一覧の先頭N件だけ")
    ap.add_argument("--ids", default="", help="指定IDのみ (例: 1,100,200-210)")
    ap.add_argument("--out", default=OUT_PATH, help="出力JSON")
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    ap.add_argument("--interval", type=float, default=1.0, help="リクエスト間隔(秒, 下限0.8)")
    ap.add_argument("--probe-gaps", action="store_true", help="一覧に無いIDも 1..最大ID+50 で確認")
    ap.add_argument("--log", default=LOG_PATH)
    args = ap.parse_args()

    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    log = Logger(args.log)
    f = Fetcher(log, interval=args.interval, refresh=args.refresh)
    log("START tsuree out=%s limit=%s ids=%s refresh=%s" % (out_path, args.limit, args.ids or "-", args.refresh))

    listing_by_id = {}  # type: Dict[int, dict]
    if args.ids:
        ids = parse_ids(args.ids)
    else:
        items = crawl_listing(f, log)
        for it in items:
            listing_by_id[it["id"]] = it
        ids = [it["id"] for it in items]
        log("listing: %d boats, max id %s" % (len(ids), max(ids) if ids else None))
        if args.probe_gaps and ids:
            have = set(ids)
            extra = [i for i in range(1, max(ids) + 51) if i not in have]
            log("probe-gaps: %d ids" % len(extra))
            ids = ids + extra
        if args.limit:
            ids = ids[:args.limit]

    total = len(ids)
    records = []
    stats = {"net": 0, "cache": 0, "missing": 0, "fail": 0, "parse_err": 0}
    t0 = time.time()
    for n, bid in enumerate(ids, 1):
        url = "%s/boats/detail/%d" % (BASE, bid)
        cache_name = "detail_%d.html" % bid
        html, how = f.get(url, cache_name)
        if html is None:
            if how in ("missing", "cache-missing"):
                stats["missing"] += 1
            else:
                stats["fail"] += 1
                log("ERROR id=%d %s" % (bid, how))
        else:
            stats["net" if how == "net" else "cache"] += 1
            path = os.path.join(CACHE, cache_name)
            fetched = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
            try:
                rec = parse_detail(html, bid, listing_by_id.get(bid), fetched)
            except Exception as e:  # noqa
                rec = None
                log("ERROR parse id=%d: %r" % (bid, e))
            if rec is None:
                stats["parse_err"] += 1
            else:
                records.append(rec)
        if n % 100 == 0:
            save(records, out_path)
        if n % 20 == 0 or n == total:
            el = time.time() - t0
            log("detail %d/%d records=%d net=%d cache=%d missing=%d fail=%d parse_err=%d elapsed=%ds" % (
                n, total, len(records), stats["net"], stats["cache"], stats["missing"], stats["fail"],
                stats["parse_err"], el))
    save(records, out_path)
    log("requests: net=%d cache=%d" % (f.n_net, f.n_cache))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
