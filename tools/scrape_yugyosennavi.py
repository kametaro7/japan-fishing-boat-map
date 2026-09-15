#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""遊漁船NAVI (https://yugyosen-navi.com/) 釣り船クローラ

列挙: WordPress 標準サイトマップ wp-sitemap.xml → wp-sitemap-posts-post-N.xml の <loc>（船ページ 153 件前後）。
      カテゴリ一覧 (/category/...) や /list/ は静的 HTML に記事が出ない（JS 描画）ので使わない。
詳細: /<slug>/ を1件ずつ取得。
      table.tb-yado（船名/所在地/TEL/メール/定休日/駐車場/宿泊/サービス/船の設備/その他）、
      .acf-map .marker[data-lat][data-lng]（ACF Google Map。無い船もある）、
      .fish-info li（釣物）、.rate-info（料金の自由記述 → 行単位のヒューリスティックで plans 化）、
      #post_meta_top の分類（エリア=都道府県 / category2=乗合・仕立・時間帯 / category3=魚種）。
作法: 1ホスト直列・間隔 --interval 秒（既定1.0, 下限0.8）・429/503 は指数バックオフ(最大5回)・
      生HTMLは work/cache/yugyosennavi/ に保存し再実行時はキャッシュを使う（--refresh で再取得）。
      robots.txt は /wp-admin/ のみ Disallow。

使い方:
  python3 tools/scrape_yugyosennavi.py                   # 全件
  python3 tools/scrape_yugyosennavi.py --limit 20        # サイトマップ先頭20件
  python3 tools/scrape_yugyosennavi.py --ids akimaru-kumamoto,onion --out work/sources/yugyosennavi.sample.json
  python3 tools/scrape_yugyosennavi.py --refresh-list    # サイトマップだけ取り直す
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
from bs4 import BeautifulSoup, NavigableString

SRC = "yugyosennavi"
BASE = "https://yugyosen-navi.com"
HOST = "yugyosen-navi.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "work", "cache", SRC)
LOG_PATH = os.path.join(ROOT, "work", "logs", SRC + ".log")
OUT_PATH = os.path.join(ROOT, "work", "sources", SRC + ".json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
SAVE_EVERY = 100

PREF_NAMES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

SNS_HOSTS = ("instagram.com", "facebook.com", "fb.com", "fb.me", "twitter.com", "x.com",
             "youtube.com", "youtu.be", "tiktok.com", "line.me", "lin.ee", "threads.net",
             "note.com", "ameblo.jp", "band.us")
# 公式サイトとしては扱わないホスト（掲載サイト自身・他の掲載/予約ポータル・地図など）
NOT_WEBSITE_HOSTS = ("yugyosen-navi.com", "google.com", "google.co.jp", "goo.gl", "g.page",
                     "maps.app.goo.gl", "chowari.jp", "tsuree.jp", "funaduri.jp", "fishing-v.jp",
                     "yugyosen.com", "point-i.jp", "anglers.jp", "gyo.ne.jp", "jalan.net",
                     "rakuten.co.jp", "asoview.com", "activityjapan.com", "reserver.co.jp",
                     "fishing-station.jp", "tsurisoku.com", "castingnet.jp", "theboat.jp",
                     "wordpress.org", "twitter.com", "paypay.ne.jp")

CITY_EXCEPTIONS = [
    "四日市市", "廿日市市", "市川市", "市原市", "野々市市", "町田市", "大町市", "十日町市",
    "村上市", "村山市", "東村山市", "武蔵村山市", "羽村市", "大村市", "田村市", "北村山郡",
    "西村山郡", "東村山郡", "中新川郡上市町", "余市郡余市町", "市貝町", "上市町", "余市町",
    "玉村町", "木曽郡上松町",
]

PORT_RE = re.compile(r"(漁港|港|マリーナ|ハーバー|桟橋|岸壁|船着き?場|乗船場|乗り場|埠頭|ふ頭|泊地|船溜まり?)$")

METHOD_WORDS = [
    "スーパーライトジギング", "ライトジギング", "ジギング", "SLJ", "タイラバ", "鯛ラバ", "アマラバ",
    "ティップランエギング", "ティップラン", "エギング", "イカメタル", "キャスティング", "一つテンヤ",
    "ひとつテンヤ", "テンヤ", "落とし込み", "泳がせ", "ボートロック", "コマセ", "胴付き", "胴突き",
    "サビキ", "天秤", "フカセ", "夜焚き", "トローリング", "インチク", "タイカブラ", "ルアー釣り",
    "エサ釣り", "餌釣り", "ライトゲーム", "五目釣り", "カットウ",
]
METHOD_CANON = {"鯛ラバ": "タイラバ", "ひとつテンヤ": "一つテンヤ", "胴突き": "胴付き", "餌釣り": "エサ釣り"}
METHOD_RE = re.compile("|".join(re.escape(w) for w in sorted(METHOD_WORDS, key=len, reverse=True)))


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


ZW_RE = re.compile(u"[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")


def clean_text(s):
    s = ZW_RE.sub("", s or "")
    s = s.replace(u"　", " ").replace(u"\xa0", " ")
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


def uniq(seq):
    res = []
    for x in seq:
        if x and x not in res:
            res.append(x)
    return res


def atomic_write(path, data, binary=False):
    tmp = path + ".tmp"
    if binary:
        with open(tmp, "wb") as f:
            f.write(data)
    else:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
    os.replace(tmp, path)


def slug_of(url):
    m = re.match(r"^https?://[^/]+/([^/?#]+)/?$", url.strip())
    return m.group(1) if m else ""


def cache_name_for_slug(slug):
    return "detail_" + re.sub(r"[^A-Za-z0-9_.-]", "_", slug) + ".html"


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

    def get(self, url, cache_name, refresh=None):
        # type: (str, str, Optional[bool]) -> Tuple[Optional[str], str, str]
        """returns (html or None, how, final_url) how in cache|cache-missing|net|missing|httpNNN|fail"""
        if not self.robots.can_fetch("*", url):
            raise RuntimeError("robots.txt disallows %s" % url)
        refresh = self.refresh if refresh is None else refresh
        path = os.path.join(CACHE, cache_name)
        miss = path + ".missing"
        meta = path + ".final"
        if not refresh:
            if os.path.exists(path):
                self.n_cache += 1
                final = url
                if os.path.exists(meta):
                    with open(meta, encoding="utf-8") as f:
                        final = f.read().strip() or url
                with open(path, "rb") as f:
                    return f.read().decode("utf-8", errors="replace"), "cache", final
            if os.path.exists(miss):
                self.n_cache += 1
                return None, "cache-missing", url
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
                err = "http%d" % r.status_code
                if attempt >= 5:
                    break
                wait = min(600, 10 * (2 ** attempt))
                ra = r.headers.get("Retry-After")
                if ra and ra.isdigit():
                    wait = max(wait, int(ra))
                self.log("WARN %s HTTP %d; backoff %ss" % (url, r.status_code, wait))
                time.sleep(wait)
                continue
            final = r.url.split("#")[0]
            # WordPress のスラッグ変更リダイレクトは同じ船として受ける。トップ等への転送は掲載終了扱い
            redirected_away = final.rstrip("/") != url.rstrip("/") and (
                HOST not in final or final.rstrip("/") == BASE or not slug_of(final))
            if r.status_code in (404, 410) or redirected_away:
                atomic_write(miss, json.dumps({"url": url, "final": r.url, "status": r.status_code,
                                               "at": now_str()}, ensure_ascii=False))
                return None, "missing", final
            if r.status_code != 200:
                return None, "http%d" % r.status_code, final
            atomic_write(path, r.content, binary=True)
            if final.rstrip("/") != url.rstrip("/"):
                atomic_write(meta, final)
            elif os.path.exists(meta):
                os.remove(meta)
            return r.content.decode("utf-8", errors="replace"), "net", final
        self.log("ERROR %s gave up: %s" % (url, err))
        return None, "fail", url


# ---------------------------------------------------------------- listing (sitemap)

LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


def crawl_sitemap(fetcher, log, refresh_list=False):
    # type: (Fetcher, Logger, bool) -> List[str]
    rf = True if refresh_list else None
    xml, how, _ = fetcher.get(BASE + "/wp-sitemap.xml", "wp-sitemap.xml", refresh=rf)
    if xml is None:
        log("ERROR sitemap index failed (%s)" % how)
        return []
    subs = [u for u in LOC_RE.findall(xml) if re.search(r"/wp-sitemap-posts-post-\d+\.xml$", u)]
    urls = []  # type: List[str]
    for sm in subs:
        name = sm.rsplit("/", 1)[-1]
        x, how2, _ = fetcher.get(sm, name, refresh=rf)
        if x is None:
            log("ERROR %s failed (%s)" % (sm, how2))
            continue
        locs = LOC_RE.findall(x)
        log("sitemap %s: %d urls (%s)" % (name, len(locs), how2))
        for u in locs:
            u = u.replace("&amp;", "&")
            if slug_of(u) and u not in urls:
                urls.append(u)
    return urls


# ---------------------------------------------------------------- parsing helpers

PHONE_RE = re.compile(r"(0\d{1,4}-\d{1,4}-\d{3,4}|0\d{9,10})")


def norm_tel(s):
    s = nfkc(s)
    s = re.sub(u"[‐‑‒–—―ー−]", "-", s)
    s = re.sub(r"\(\s*(\d{2,5})\s*\)", r"-\1-", s)
    s = s.replace(" ", "")
    m = PHONE_RE.search(s)
    if not m:
        return ""
    t = m.group(1).strip("-")
    if "-" not in t and len(t) == 11 and t[:3] in ("090", "080", "070", "050"):
        t = "%s-%s-%s" % (t[:3], t[3:7], t[7:])
    elif "-" not in t and len(t) == 10 and t.startswith("0120"):
        t = "%s-%s-%s" % (t[:4], t[4:7], t[7:])
    return t


def host_of(url):
    m = re.match(r"^https?://([^/:?#]+)", url.strip(), re.I)
    return m.group(1).lower() if m else ""


def host_in(url, hosts):
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in hosts)


def is_sns(url):
    return host_in(url, SNS_HOSTS)


def extract_city(addr, pref):
    rest = addr[len(pref):] if pref and addr.startswith(pref) else addr
    if not rest:
        return ""
    for ex in CITY_EXCEPTIONS:
        if rest.startswith(ex):
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
        mi = re.match(r"^.{1,3}島(.{1,4}[町村])$", c)
        if pref == "東京都" and mi:
            return mi.group(1)
        return c
    return ""


def strip_unbalanced(s):
    """片側だけの括弧を取る（例: 「…1029-9中野漁協前）」）"""
    for op, cl in (("（", "）"), ("(", ")"), ("【", "】"), ("「", "」")):
        if s.count(cl) > s.count(op):
            idx = s.rfind(cl)
            s = s[:idx] + s[idx + 1:]
        if s.count(op) > s.count(cl):
            idx = s.rfind(op)
            s = s[:idx] + s[idx + 1:]
    return s.strip()


ACCESS_LIKE_RE = re.compile(r"お車|徒歩|インター|下車|駅から|駅より|バス停|から約|より約")
ADDR_START_RE = re.compile(u"^[一-龥々ヶケノ]{1,10}?[都道府県市区町村郡]")
BOAT_WORD_RE = re.compile(r"遊漁船|民宿|釣り?船|渡船|ゲストハウス")


def parse_address(raw, pref_hint, name=""):
    """所在地 → (address, pref, port, access)。「熊本県天草市深海町761 深海漁港」→ 住所と港に分ける。
    所在地欄にアクセス案内しか無い場合は address を空にして 4 番目に返す"""
    a = one_line(raw)
    a = re.sub(r"^〒?\s*\d{3}\s*[-－ー]\s*\d{4}\s*", "", a)
    a = strip_unbalanced(a)
    if not a or not re.search(u"[一-龥ぁ-んァ-ン々]", a):
        return "", pref_hint, "", ""
    if ACCESS_LIKE_RE.search(a) and not ADDR_START_RE.match(a):
        return "", pref_hint, "", a
    port = ""
    # 空白区切りの末尾トークンが港名なら port へ（「青島港内」→ 青島港）。船名・「民宿・遊漁船」は捨てる
    toks = a.split(" ")
    keep = []
    for i, t in enumerate(toks):
        t2 = t.strip("()（）")
        t3 = re.sub(r"港内$", "港", t2)
        if i > 0 and PORT_RE.search(t3) and not re.search(r"\d", t3) and len(t3) <= 20:
            if not port:
                port = t3
            continue
        if i > 0 and not re.search(r"\d", t2) and ((name and name in t2) or BOAT_WORD_RE.search(t2)):
            continue
        keep.append(t)
    a = "".join(keep)
    # 括弧書きの港名「…番地（深海漁港）」
    if not port:
        m = re.search(r"[（(]([^()（）]{1,20}?(?:漁港|港|マリーナ|桟橋|岸壁))[)）]$", a)
        if m:
            port = m.group(1)
            a = a[:m.start()].strip()
    # 末尾の読み仮名・道案内「(ほしかこう)」「（入ってすぐ正面）」「…信号渡ってすぐ」
    a = re.sub(u"[（(]([ぁ-んー]+|[^()（）]*(?:すぐ|正面|手前|向かい|隣|横)[^()（）]*)[)）]$", "", a).strip()
    a = re.sub(u"(?:の)?(?:信号|交差点)(?:を)?(?:渡って|入って)?すぐ.*$", "", a).strip()
    pref = ""
    for p in PREF_NAMES:
        if a.startswith(p):
            pref = p
            break
    if not pref and pref_hint:
        # 県名が無ければカテゴリの県を付ける。「熊本天草市」→ 熊本県天草市、「大分市…」→ 大分県大分市、
        # 「岡県北九州市」（福の脱字）→ 福岡県北九州市
        short = pref_hint[:-1] if pref_hint != "北海道" else pref_hint
        if a.startswith(short) and not a.startswith(pref_hint) and a[len(short):len(short) + 1] not in ("市", "郡"):
            a = pref_hint + a[len(short):]
        elif len(pref_hint) >= 3 and a.startswith(pref_hint[1:]):
            a = pref_hint + a[len(pref_hint) - 1:]
        else:
            a = pref_hint + a
        pref = pref_hint
    return a, pref, port, ""


TIME_RE = re.compile(
    r"(午前|午後|AM|PM|am|pm)?\s*(\d{1,2})\s*(?::|時(?!間))\s*(\d{1,2}|半)?\s*分?")


def find_times(s):
    """returns list of (HH:MM, start, end) ・s は NFKC 済みを想定"""
    res = []
    for m in TIME_RE.finditer(s):
        ap, h, mi = m.group(1), int(m.group(2)), m.group(3)
        if ":" not in m.group(0) and "時" not in m.group(0):
            continue
        # 「2021/6/1」「1:1」等の誤検出を避ける: コロン表記は分2桁のみ
        if ":" in m.group(0) and (mi is None or len(mi) != 2):
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
        if h == 24:
            h = 0
        res.append(("%02d:%02d" % (h, mm), m.start(), m.end()))
    return res


AMOUNT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*万\s*(?:(\d)\s*千|(\d{1,4}))?\s*円?"
    r"|(\d{1,3})\s*千\s*円"
    r"|[¥\\]\s*(\d{1,3}(?:,\d{3})+|\d{3,6})(?![\d.,])"
    r"|(\d{1,3}(?:[,.]\d{3})+|\d+)\s*円"
    r"|(?<![\d.,/])(\d{1,3}(?:,\d{3})+)(?![\d.,])"
    r"|(?<![\d.,])(\d{4,6})\s*-(?!\s*\d)")


def parse_amounts(s):
    """金額(円)の (value, start, end) 列。s は NFKC 済み"""
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
        pre = s[max(0, m.start() - 2):m.start()]
        if re.search(r"\d[.,]$", pre):
            out.append((None, m.start(), m.end()))
            continue
        digits = re.sub(r"[,.]", "", num)
        out.append((int(digits), m.start(), m.end()))
    return out


def norm_for_parse(s):
    s = nfkc(s)
    s = s.replace("〜", "~").replace("～", "~")
    s = re.sub(r"(\d)、(\d{3})(?!\d)", r"\1,\2", s)          # ４０、０００円
    s = re.sub(r"([¥\\]\s*\d{1,3})\.(\d{3})(?![\d])", r"\1,\2", s)  # ¥84.000
    return s


BLOCK_TAGS = ["p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tbody",
              "thead", "dl", "dt", "dd", "section", "blockquote", "figure", "figcaption", "pre", "hr"]


def block_lines(el):
    """要素を行リストに（<br>/ブロック要素で改行、表は行ごとに「セル | セル」）"""
    if el is None:
        return []
    el = BeautifulSoup(str(el), "lxml")
    for tr in el.find_all("tr"):
        cells = [one_line(c.get_text(" ")) for c in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        tr.replace_with(NavigableString("\n" + " | ".join(cells) + "\n"))
    for br in el.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for t in el.find_all(BLOCK_TAGS):
        t.insert_before(NavigableString("\n"))
        t.append(NavigableString("\n"))
    text = clean_text(el.get_text())
    lines = []
    for ln in text.split("\n"):
        ln = one_line(ln).strip("| ")
        if ln:
            lines.append(ln)
    return lines


# ---------------------------------------------------------------- plan extraction (rate-info)

KIND_NORIAI_RE = re.compile(r"乗り?合い?|乗りあい|のりあい")
KIND_SHITATE_RE = re.compile(r"チャーター|貸し?切り?|仕立て?|[1１一]\s*隻|/\s*隻|[1１一]\s*艘|/\s*艘")
PERSON_RE = re.compile(r"お?ひとり様?|お?一人様?|[1１一]\s*名様?|[1１一]\s*人(?!乗)|/\s*(人|名)|大人|[1１一]人あたり|一名|人\s*(?=[¥\\￥]?\s*\d)")
SKIP_LINE_RE = re.compile(
    r"追加|増し|増す|割引|引き(?!換)|キャンセル|レンタル|貸し?道具|貸竿|タックル|セット|仕掛け?代|"
    r"エサ代|餌代|駐車|中学生|小学生|高校生|子供|子ども|こども|お子様|小人|幼児|未就学|学生|会員|延長|送迎|"
    r"宿泊|保険|手数料|販売|クーラー|ライフジャケット|ロッド|リール|竿|燃料|サーチャージ|弁当|"
    r"クーポン|返金|振込|予約金|デポジット|修理|紛失|中乗り|サポート|素泊|朝食|夕食|食事|ごとに|毎に")
ADDON_RE = re.compile(r"特価|割引|OFF|off|販売|個目以降|円引|貸出|貸し出し|加工|[/／]\s*(匹|本)|なくなったら|プラス|学割")
SKIP_HEADER_RE = re.compile(
    r"レンタル|貸し?道具|タックル|キャンセル|氷|販売|注意|支払|駐車|オプション|宿泊|送迎|持ち物|"
    r"お願い|ルール|規約|禁止|について$|案内$|お知らせ|食事|民宿料金|クルージング|ウォッチング|海上タクシー|観光")
PLANISH_RE = re.compile(r"プラン|便|コース|乗合|乗り合|チャーター|仕立|貸切|釣り$|狙い|五目")
LABEL_ONLY_RE = re.compile(r"^(料金|乗船料金?|ご?利用料金|時間|出船時間|出港時間|出航時間|場所|釣り場|ポイント|"
                           r"エリア|定員|人数|期間|時期|シーズン|内容|対象魚?|ターゲット|集合|集合時間|備考|"
                           r"料金\(税込\)|価格|金額)[:：]?$")
GENERIC_TITLE_RE = re.compile(r"^(乗船|ご?利用|遊漁|釣り?船)?(料金|料金表|料金案内|料金について|料金プラン|価格|プラン|"
                              r"ご利用料金|乗船料)(\(税込\)|（税込）)?$")
TIMEISH_RE = re.compile(r"出船|出港|出航|集合|時間|便|~|-|夏季|冬季|朝|昼|夜|午前|午後|日の出|日没")
DAYS_RE = re.compile(r"平日|土日祝日?|土日|休日|週末|祝日")
BULLETS = u"・●■◆◇□○◎★☆▼▽►▶◉♦◎✓✔ 　"
PAIRS = {u"【": u"】", u"『": u"』", u"「": u"」", u"[": u"]", u"［": u"］", u"<": u">", u"＜": u"＞",
         u"(": u")", u"（": u"）", u"〈": u"〉", u"《": u"》"}


def strip_enclosing(t):
    t = t.strip(BULLETS + u"–—―‐-")
    if len(t) >= 2 and t[0] in PAIRS and t[-1] == PAIRS[t[0]]:
        inner = t[1:-1]
        if not any(ch in inner for ch in list(PAIRS.keys()) + list(PAIRS.values())):
            return inner.strip()
    return t


def classify_kind(s):
    """テキスト中の業種語 → '乗合' / '仕立' / ''（両方あれば先に出た方）"""
    mn = KIND_NORIAI_RE.search(s)
    ms = KIND_SHITATE_RE.search(s)
    if mn and ms:
        return "乗合" if mn.start() < ms.start() else "仕立"
    if mn:
        return "乗合"
    if ms:
        return "仕立"
    return ""


def pure_kind_header(s):
    """「乗合」「チャーター」「乗り合い」「仕立 半日プラン」のような業種見出し → (kind, 残りの見出し)"""
    t = strip_enclosing(s)
    m = re.match(r"^(乗り?合い?(?:船|便)?|乗りあい|チャーター(?:船|便)?|貸し?切り?(?:船|便)?|仕立て?(?:船|便)?)"
                 r"(?:\s+|[:：・]|$)(.*)$", t)
    if not m:
        return None
    kind = classify_kind(m.group(1))
    rest = m.group(2).strip(u"・:：　 ")
    if rest and (parse_amounts(norm_for_parse(rest)) or len(rest) > 30 or re.search(r"[。！!]$", rest)):
        return None
    return kind, rest


def split_kind_segments(line_n):
    """1行に「乗合：10,000円/人 貸切：70,000円/隻」「10,000円／1名様 60,000円／チャーター」のように
    複数業種と金額がある場合に分割"""
    amts = parse_amounts(line_n)
    if len(amts) < 2:
        return [line_n]
    pos = [m.start() for m in re.finditer(r"乗り?合い?|チャーター|貸し?切り?|仕立て?", line_n)]
    if len(pos) >= 2:
        segs = []
        starts = [0] + pos[1:]
        for i, st in enumerate(starts):
            en = starts[i + 1] if i + 1 < len(starts) else len(line_n)
            seg = line_n[st:en].strip(" /／、,")
            if seg:
                segs.append(seg)
        if all(parse_amounts(sg) for sg in segs):
            return segs
    # 金額の直後の「／区分」で分ける（少なくとも1つが チャーター/貸切/隻 のときだけ）
    quals = []
    for (v, a0, a1) in amts:
        mq = re.match(r"^\s*[/／]\s*([^\s/／|]{1,15})", line_n[a1:])
        quals.append(mq.group(1) if mq else "")
    if all(quals) and any(classify_kind(q) == "仕立" for q in quals):
        prefix = line_n[:amts[0][1]]
        segs = []
        for i, (v, a0, a1) in enumerate(amts):
            en = amts[i + 1][1] if i + 1 < len(amts) else len(line_n)
            segs.append((prefix + line_n[a0:en]).strip(" /／、,"))
        return segs
    return [line_n]


def is_header_line(raw, n):
    if parse_amounts(n) or find_times(n):
        return False
    if " | " in raw:
        return False
    t = strip_enclosing(raw)
    if not t or len(t) > 32:
        return False
    if re.match(r"^[※＊*注]|^(但し|ただし|なお|尚)", t):
        return False
    if LABEL_ONLY_RE.match(nfkc(t)):
        return False
    if re.match(r"^[(（].*[)）]$", t):
        return False
    bracketed = bool(re.search(u"[【】『』]", raw))
    if not bracketed:
        if re.search(u"[。！!？?、,はがをにでてもへとや]$", t):
            return False
        if re.search(r"\d+\s*(名|人)様?(より|から|以上|まで|迄)|出港|出船(?!時間)|~$", nfkc(t)):
            return False
    return True


def clean_label(label):
    t = nfkc(label)
    t = re.split(r"[=＝]", t)[0]
    t = re.sub(r"[¥\\￥]", "", t)
    t = re.sub(r"[(（][^()（）]*(?:名|人|税|込|付)[^()（）]*[)）]", "", t)
    if t.count("(") > t.count(")"):
        t = t[:t.rfind("(")]
    t = re.sub(r"(ご?利用|乗船)?料金|価格|金額", "", t)
    t = t.strip(u" :：・/／|-~〜～　、,")
    t = re.sub(r"の$", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def is_generic_label(lab):
    rest = re.sub(r"乗り?合い?|乗りあい|チャーター便?|貸し?切り?|仕立て?|大人|お?ひとり様?|お?一人様?|"
                  r"\d+\s*(名|人)(様|乗り)?(まで|迄|から|より|~)?|一名|平日|土日祝日?|土日|休日|週末|祝日|便|1日", "", lab)
    return not rest.strip(" ・/")


def set_price(p, val, kind, per_person, pt):
    if len(pt) > 80:
        pt = pt[:79] + u"…"
    if val is None or val < 1000 or val > 2000000:
        p["price"] = None
    elif kind == "仕立":
        p["price"] = val
        if not re.search(r"隻|艘|チャーター|貸し?切|仕立", pt):
            pt = "1隻 " + pt
    elif not kind and not per_person and val >= 50000:
        p["price"] = None
    else:
        p["price"] = val
    p["price_text"] = pt


def make_plan(src_url):
    return {"name": "", "kind": "", "targets": [], "price": None, "price_text": "", "depart": "",
            "return": "", "meet": "", "season": "", "days": "", "includes": "", "url": src_url}


def extract_plans(lines, src_url, species):
    # type: (List[str], str, List[str]) -> List[dict]
    """料金欄の自由記述を行単位で読む。
    見出し（【近海プラン】/『太刀魚釣り』/短い行）→ プラン名、業種見出し（乗合/チャーター）→ kind、
    金額行 → 1プラン。レンタル・キャンセル等の節や子供料金・追加料金の行は採らない。
    時刻は金額行の中か、同じ節の「時間 5:30～」のような行から採る（注記内の時刻は使わない）。"""
    plans = []  # type: List[dict]
    st = {"title": "", "kind": "", "skip": False, "plans": [], "time": None, "meet": "", "narr": 0,
          "cols": [], "label": ""}

    def new_section(title, kind=None, skip=False):
        st["title"] = title
        if kind is not None:
            st["kind"] = kind
        st["skip"] = skip
        st["plans"] = []
        st["time"] = None
        st["meet"] = ""
        st["narr"] = 0
        st["cols"] = []

    for raw in lines:
        n = norm_for_parse(raw)
        if len(raw) > 140:
            continue
        amts_line = parse_amounts(n)
        # 集合時刻（どの行でも）
        mm = re.search(r"出[船港航](時刻|時間)?の?\s*(\d{1,3})\s*分前", n)
        if mm and not re.match(r"^[※＊*]?\s*(キャンセル)", n):
            st["meet"] = "出船%s分前" % mm.group(2)
            for p in st["plans"]:
                if not p["meet"]:
                    p["meet"] = st["meet"]
        cells = [c.strip() for c in raw.split(" | ")] if " | " in raw else []
        # 表の見出し行（金額なし）
        if cells and not amts_line:
            pk = pure_kind_header(cells[0])
            if pk is not None:
                new_section(cells[0].strip(), kind=pk[0])
            st["cols"] = cells
            continue
        # 業種見出し
        if not amts_line and not cells:
            pk = pure_kind_header(raw)
            if pk is not None:
                new_section(pk[1], kind=pk[0])
                continue
        if is_header_line(raw, n):
            t = strip_enclosing(raw)
            if SKIP_HEADER_RE.search(t) and not PLANISH_RE.search(t):
                new_section(t, skip=True)
            else:
                k = classify_kind(t)
                new_section("" if GENERIC_TITLE_RE.match(nfkc(t)) else t, kind=(k or None))
            continue
        if LABEL_ONLY_RE.match(n.strip(u"・ ")):
            st["label"] = n
            continue
        if st["skip"]:
            continue
        if not amts_line:
            label = st["label"]
            st["label"] = ""
            if re.match(r"^[※＊*]", n) or re.search(r"予約|連絡|キャンセル|受付|電話|問い?合わせ|までに|締切", n):
                continue
            ts = find_times(n)
            if ts and (TIMEISH_RE.search(n) or re.search(r"時間|出[船港航]", label)):
                dep = ts[0][0]
                ret = ""
                if len(ts) >= 2 and re.search(r"[~\-]", n[ts[0][2]:ts[1][1]]):
                    ret = ts[1][0]
                # 「出船 5:00～7:00」は出船時刻の幅（帰港時刻ではない）
                if ret and re.match(u"^[・●■◆◇□○◎★☆]?\\s*(出船|出港|出航)", n) and not re.search(u"帰|納竿|終了|沖上が", n):
                    ret = ""
                if st["time"] is None:
                    st["time"] = (dep, ret)
                for p in st["plans"]:
                    if not p["depart"]:
                        p["depart"], p["return"] = dep, ret
            elif (re.match(r"^[(（].*[)）]$", n.strip()) or re.search(r"時期|シーズン|期間", label)) \
                    and re.search(r"\d{1,2}\s*月", n):
                if st["plans"] and not st["plans"][-1]["season"]:
                    st["plans"][-1]["season"] = n.strip("()（） ")
            elif len(n) > 30:
                st["narr"] += 1
            continue
        # ここから金額行
        st["label"] = ""
        if re.match(r"^[※＊*]", n):
            continue
        # 表で1行に金額セルが複数（列見出し = 乗合/チャーター・平日/土日祝・釣り方）→ 列ごとに1プラン
        if cells and st["cols"] and len(st["cols"]) == len(cells):
            c0n = norm_for_parse(cells[0])
            amt_cells = [ci for ci, c in enumerate(cells) if ci > 0 and parse_amounts(norm_for_parse(c))]
            if len(amt_cells) >= 2 and not parse_amounts(c0n):
                if SKIP_LINE_RE.search(c0n) or ADDON_RE.search(c0n):
                    continue
                row_label = clean_label(c0n)
                for ci in amt_cells:
                    cn = norm_for_parse(cells[ci])
                    val = parse_amounts(cn)[0][0]
                    if val is not None and val < 1000:
                        continue
                    col_raw = st["cols"][ci]
                    col = norm_for_parse(col_raw)
                    if SKIP_LINE_RE.search(col) or ADDON_RE.search(cn) or \
                            SKIP_LINE_RE.search(re.sub(r"[(（][^()（）]*(?:付|込)[^()（）]*[)）]", "", cn)):
                        continue
                    kind = classify_kind(cn) or classify_kind(col) or classify_kind(c0n) or st["kind"]
                    per_person = bool(PERSON_RE.search(cn) or PERSON_RE.search(col) or PERSON_RE.search(c0n))
                    col_label = clean_label(re.sub(r"\d{1,2}:\d{2}\s*~?\s*(納竿)?\s*(\d{1,2}:\d{2})?", "", col))
                    if not row_label or is_generic_label(row_label):
                        name = " ".join(x for x in (st["title"] or "乗船料金", row_label) if x)
                    else:
                        name = row_label
                    if col_label and not is_generic_label(col_label) and col_label not in name:
                        name = name + " " + col_label
                    p = make_plan(src_url)
                    p["kind"] = kind
                    p["name"] = name.strip()[:60]
                    set_price(p, val, kind, per_person, one_line("%s %s %s" % (cells[0], col_raw, cells[ci])))
                    ts = find_times(col) or find_times(cn)
                    if ts:
                        p["depart"] = ts[0][0]
                        if len(ts) >= 2 and re.search(r"[~\-]", col[ts[0][2]:ts[1][1]]):
                            p["return"] = ts[1][0]
                    elif st["time"] is not None:
                        p["depart"], p["return"] = st["time"]
                    if st["meet"]:
                        p["meet"] = st["meet"]
                    md = DAYS_RE.search(col) or DAYS_RE.search(c0n)
                    if md:
                        p["days"] = md.group(0)
                    inc = [x for x in re.findall(r"[(（]([^()（）]*(?:付|込)[^()（）]*)[)）]", cn)
                           if not re.fullmatch(r"(消費)?税込み?(価格)?", x.strip())]
                    if inc:
                        p["includes"] = inc[0].strip()
                    hay = p["name"] + " " + c0n + " " + col
                    p["targets"] = [sp for sp in species if sp and sp in hay]
                    plans.append(p)
                    st["plans"].append(p)
                if len(plans) >= 40:
                    break
                continue
        segs = split_kind_segments(n)
        for seg in segs:
            samts = parse_amounts(seg)
            if not samts:
                continue
            val, a0, a1 = samts[0]
            if val is not None and val < 1000:
                continue
            head = seg[:a1]
            rest = seg[a1:]
            mpar = re.match(r"^\s*[(（][^()（）]*[)）]", rest)
            if mpar:
                head += mpar.group(0)
                rest = rest[len(mpar.group(0)):]
            mq = re.match(r"^\s*[/／]\s*[^\s/／|]{1,15}", rest)
            if mq:
                head += mq.group(0)
            if SKIP_LINE_RE.search(re.sub(r"[(（][^()（）]*(?:付|込)[^()（）]*[)）]", "", head)):
                continue
            if ADDON_RE.search(seg) or re.search(r"[+＋]\s*[¥\\]?\s*$", seg[:a0]):
                continue
            if re.search(r"レンタル|SET|セット|タックル|販売|食事|^(餌|エサ|コマセ|仕掛け)(?!釣)", st["title"], re.I):
                continue
            kind = classify_kind(head)
            per_person = bool(PERSON_RE.search(head))
            if not kind:
                kind = st["kind"]
                # 前の「チャーター」見出しを引きずった小さい金額は1隻料金ではない
                if kind == "仕立" and val is not None and val < 20000:
                    kind = ""
            p = make_plan(src_url)
            p["kind"] = kind
            lab = clean_label(seg[:a0])
            first_cell = ""
            if cells:
                c0 = norm_for_parse(cells[0])
                if not parse_amounts(c0):
                    first_cell = clean_label(c0)
                    lab = first_cell
            title = st["title"]
            if title and st["narr"] >= 2 and not st["plans"]:
                title = ""
                st["title"] = ""
            if is_generic_label(lab):
                name = title or ("乗船料金 " + lab if lab else "乗船料金")
            elif cells:
                name = lab
            elif title:
                name = "%s %s" % (title, lab)
            else:
                name = lab
            p["name"] = name.strip()[:60]
            pt = one_line((seg if len(segs) > 1 else raw).replace(" | ", " "))
            set_price(p, val, kind, per_person, pt)
            ts = find_times(seg)
            if ts:
                p["depart"] = ts[0][0]
                if len(ts) >= 2 and re.search(r"[~\-]", seg[ts[0][2]:ts[1][1]]):
                    p["return"] = ts[1][0]
            elif st["time"] is not None:
                p["depart"], p["return"] = st["time"]
            if st["meet"]:
                p["meet"] = st["meet"]
            md = DAYS_RE.search(seg[:a0])
            if not md and cells and st["cols"]:
                # 表: 最初の金額が入っている列の見出し（平日/土日祝など）
                for ci, c in enumerate(cells):
                    if parse_amounts(norm_for_parse(c)):
                        if ci < len(st["cols"]):
                            md = DAYS_RE.search(st["cols"][ci])
                        break
            if md:
                p["days"] = md.group(0)
            inc = [x for x in re.findall(r"[(（]([^()（）]*(?:付|込)[^()（）]*)[)）]", seg)
                   if not re.fullmatch(r"(消費)?税込み?(価格)?", x.strip())]
            if inc:
                p["includes"] = inc[0].strip()
            hay = p["name"] + " " + seg
            p["targets"] = [sp for sp in species if sp and sp in hay]
            plans.append(p)
            st["plans"].append(p)
        if len(plans) >= 40:
            break
    return plans


# ---------------------------------------------------------------- detail parsing

def table_rows(soup):
    rows = {}  # type: Dict[str, object]
    for tr in soup.select("table.tb-yado tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th is None or td is None:
            continue
        k = one_line(th.get_text())
        if k and k not in rows:
            rows[k] = td
    return rows


def td_text(td):
    if td is None:
        return ""
    return "\n".join(block_lines(td))


def td_list(td):
    if td is None:
        return []
    lis = [one_line(li.get_text()) for li in td.find_all("li")]
    if lis:
        return uniq(lis)
    return uniq([x.strip() for x in re.split(r"[、，,/／・\n]+", td_text(td)) if x.strip()])


URL_RE = re.compile(r"https?://[^\s<>\"'　、，,（）()「」]+")


def collect_urls(*els):
    urls = []
    for el in els:
        if el is None:
            continue
        for a in el.find_all("a", href=True):
            urls.append(a["href"].strip())
        for u in URL_RE.findall(el.get_text(" ")):
            urls.append(u.strip())
    res = []
    for u in urls:
        u = u.rstrip(".。)")
        if re.match(r"^https?://", u, re.I) and u not in res:
            res.append(u)
    return res


CAT3_PREFIX_RE = re.compile(r"^(\d+位)")
CAT3_SUFFIX_RE = re.compile(r"(\(一人平均[\d.]+匹\)|平均[\d.]+cm)$")


def clean_cat3(t):
    t = CAT3_PREFIX_RE.sub("", t.strip())
    t = CAT3_SUFFIX_RE.sub("", t).strip()
    return t


def extract_kana(name):
    """「Onion（オニオン）」「遊漁船天城（てんじょう）」「STAY DREAM-ステイドリーム-」→ (name, kana)"""
    m = re.match(u"^(.+?)\s*[（(]\s*([ぁ-んァ-ヶー・\s]+)\s*[)）]\s*$", name)
    if not m:
        m = re.match(u"^(.+?)\s*[-－ー―]\s*([ぁ-んァ-ヶー・\s]+?)\s*[-－―]\s*$", name)
    if m and re.search(r"[A-Za-z一-龥]", m.group(1)):
        kana = kata_to_hira(re.sub(r"[\s・]", "", m.group(2)))
        return m.group(1).strip(), kana
    return name, ""


DESC_SKIP_RE = re.compile(r"船長|代表|オーナー|社長|申します|はじめまして|初めまして|こんにちは|こんばんは|"
                          r"みなさん|皆さん|皆様|予約開始|支払|キャンセル|電話|問い?合わせ")

SPECIES_VOCAB = set(u"""マダイ タイ チダイ キダイ レンコダイ クロダイ チヌ イシダイ イシガキダイ イシガキ アマダイ キンメダイ キンメ
メダイ マトウダイ ヒラマサ ブリ ハマチ ワラサ イナダ メジロ ヤズ カンパチ 青物 サワラ サゴシ タチウオ ヒラメ マゴチ カレイ
アジ マアジ サバ マサバ イサキ メバル カサゴ アラカブ ガラカブ オニカサゴ アコウ キジハタ オオモンハタ オウモンハタ アカハタ
マハタ アオハタ ハタ クエ アラ 根魚 シーバス スズキ ヒラスズキ キス シロギス キスゴ カワハギ フグ トラフグ アオリイカ ミズイカ
ケンサキイカ ヤリイカ スルメイカ アカイカ イカ マダコ タコ イイダコ カツオ マグロ キハダ クロマグロ シイラ カジキ アカムツ
ノドグロ クロ グレ メジナ ホウボウ アナゴ マダラ タラ ソイ ホッケ アイナメ""".split())
SPECIES_CANON = {u"真鯛": u"マダイ", u"鯛": u"タイ", u"太刀魚": u"タチウオ", u"剣先イカ": u"ケンサキイカ",
                 u"剣先": u"ケンサキイカ", u"赤イカ": u"アカイカ", u"真アジ": u"マアジ", u"真サバ": u"マサバ",
                 u"平政": u"ヒラマサ", u"鰤": u"ブリ", u"鮃": u"ヒラメ", u"黒鯛": u"クロダイ", u"甘鯛": u"アマダイ"}
SPECIES_STOP = set(u"ターゲット ゲスト フラットフィッシュ ロックフィッシュ ルアー エサ オフショア ボート".split())


def species_from_fishinfo(el):
    """.fish-info の li。通常は1魚種1項目。説明文や「釣り方：魚、魚」の羅列が入っている船は魚種だけ拾う"""
    out = []
    if el is None:
        return out
    for li in (el.select("li") or [el]):
        txt = one_line(li.get_text(" "))
        if not txt:
            continue
        if len(txt) <= 12 and not re.search(u"[\\d。！!？?、,，：:（(／/・]", txt):
            if not METHOD_RE.search(txt):
                out.append(SPECIES_CANON.get(txt, txt))
            continue
        for line in block_lines(li):
            ln = norm_for_parse(line)
            if parse_amounts(ln):
                continue
            listy = bool(re.search(u"[、,/・]", ln))
            if not listy and re.search(u"[。!?]$", ln):
                continue
            if ":" in ln:
                ln = ln.split(":")[-1]
            ln = re.sub(r"[(（][^()（）]*[)）]", " ", ln)
            toks = [x for x in re.split(u"[、,/・\\s…]+", ln) if x]
            for tok in toks:
                tok = re.sub(u"^(その他|他|主な)", "", tok)
                tok = re.sub(u"(など|等|類)$", "", tok).strip()
                if not tok:
                    continue
                if tok in SPECIES_CANON:
                    out.append(SPECIES_CANON[tok])
                elif tok in SPECIES_VOCAB:
                    out.append(tok)
                elif listy and len(toks) >= 2 and re.match(u"^[ァ-ヶー]{3,8}$", tok) and not METHOD_RE.search(tok) \
                        and tok not in SPECIES_STOP and not re.search(u"(ング|プラン|コース|スタイル)$", tok):
                    out.append(tok)
    return uniq(out)


SHARED_PATH_HOSTS = ("ameblo.jp", "sites.google.com", "peraichi.com", "blog.goo.ne.jp", "blog.livedoor.jp")


ASSET_HOSTS = ("googleusercontent.com", "imgur.com", "twimg.com", "cdninstagram.com", "fbcdn.net",
               "stat.ameba.jp", "user.ameba.jp", "gstatic.com", "wp.com", "cloudfront.net")


def is_asset_url(u):
    """CDN・画像ホスティング上のファイル（公式サイトの根拠にしない）。自ドメイン上の画像は公式サイトの根拠として使う"""
    h = host_of(u)
    return h.startswith("cdn.") or host_in(u, ASSET_HOSTS)


def norm_website(u):
    """公式サイトはトップ（オリジン）に揃える。パスで区切る共有ホストはそのまま"""
    m = re.match(r"^(https?://[^/?#]+)", u.strip(), re.I)
    if not m or host_in(u, SHARED_PATH_HOSTS):
        return u
    return m.group(1) + "/"


def short_desc(other, services):
    t = one_line(other)
    t = URL_RE.sub("", t)
    t = re.sub(r"(LINE|ライン)\s*(ID)?\s*[:：]?\s*@?[A-Za-z0-9_.@-]*", "", t, flags=re.I)
    t = re.sub(r"支払い?方法\s*[:：].*$", "", t)
    t = re.sub(r"【[^】]{0,20}】", "", t)
    t = t.strip(" ・/／")
    first = ""
    for s in re.findall(r"[^。！!？?]+[。！!？?]*", t)[:8]:
        s = s.strip(u" ・/／:：")
        if len(s) < 8 or DESC_SKIP_RE.search(s) or len(re.findall(u"[一-龥ぁ-んァ-ヶ]", s)) < 5:
            continue
        first = s
        break
    if len(first) > 60:
        first = first[:59] + u"…"
    parts = []
    if first:
        parts.append(first)
    if services:
        parts.append("サービス: " + "・".join(services))
    d = " ／ ".join(parts)
    if len(d) > 100:
        d = d[:99] + "…"
    return d


def parse_detail(html, src_url, fetched=None):
    soup = BeautifulSoup(html, "lxml")
    slug = slug_of(src_url)
    title_el = soup.select_one("#post_title")
    title = one_line(title_el.get_text()) if title_el else ""
    rows = table_rows(soup)
    if not rows and not title:
        return None

    # 分類
    cat_pref = ""
    slots = []
    kinds_cat = []
    species_cat = []
    for a in soup.select("#post_meta_top a"):
        cls = " ".join(a.get("class") or [])
        t = one_line(a.get_text())
        if "cat-category2" in cls:
            if t.startswith("乗合"):
                kinds_cat.append("乗合")
            elif t.startswith("仕立"):
                kinds_cat.append("仕立")
            elif re.match(r"^(午前|午後|夜間)", t):
                slots.append(t)
        elif "cat-category3" in cls:
            c = clean_cat3(t)
            if c:
                species_cat.append(c)
        elif "cat-category4" in cls:
            continue
        elif "cat-category" in cls:
            m = re.search(r"(北海道|東京都|京都府|大阪府|.{2,3}県)", t)
            if m and m.group(1) in PREF_NAMES and not cat_pref:
                cat_pref = m.group(1)

    name_raw = one_line(td_text(rows.get("船名"))) or title
    name, kana = extract_kana(name_raw)

    address, pref, port, addr_access = parse_address(td_text(rows.get("所在地")), cat_pref, name)
    if not pref:
        pref = cat_pref
    city = extract_city(address, pref) if address else ""

    tel = norm_tel(td_text(rows.get("TEL")))
    holidays = one_line(td_text(rows.get("定休日")))
    parking = one_line(td_text(rows.get("駐車場")))
    lodging = one_line(td_text(rows.get("宿泊")))
    services = td_list(rows.get("サービス"))
    facilities = td_list(rows.get("船の設備"))
    other_td = rows.get("その他")
    other = td_text(other_td)

    lat = lon = None
    mk = soup.select_one(".access-info .marker[data-lat][data-lng]") or soup.select_one(".acf-map .marker[data-lat]")
    if mk is not None:
        try:
            la, lo = float(mk.get("data-lat")), float(mk.get("data-lng"))
            if 20.0 <= la <= 46.6 and 122.0 <= lo <= 154.0:
                lat, lon = round(la, 7), round(lo, 7)
        except (TypeError, ValueError):
            pass

    fish_el = soup.select_one(".fish-info")
    targets = uniq(species_from_fishinfo(fish_el) + species_cat)

    rate_el = soup.select_one(".rate-info")
    rate_lines = block_lines(rate_el)
    fish_lines = block_lines(fish_el)
    species_vocab = sorted(uniq(targets), key=len, reverse=True)
    plans = extract_plans(rate_lines, src_url, species_vocab)
    if not plans and any(" | " in l for l in rate_lines):
        # 1セルに長文と複数金額が入った表（行が長すぎて読めない）→ セルを行に展開して読み直す
        flat = []
        for l in rate_lines:
            flat.extend([c.strip() for c in l.split(" | ") if c.strip()])
        plans = extract_plans(flat, src_url, species_vocab)
    if not plans and any(parse_amounts(norm_for_parse(l)) for l in fish_lines):
        # 料金を「釣物」欄に書いている船
        plans = extract_plans(fish_lines, src_url, species_vocab)

    seen = set()
    uniq_plans = []
    for p in plans:
        key = (p["name"], p["kind"], p["price"], p["price_text"], p["depart"], p["return"])
        if key not in seen:
            seen.add(key)
            uniq_plans.append(p)
    plans = uniq_plans

    types = []
    for k in kinds_cat + [p["kind"] for p in plans]:
        if k in ("乗合", "仕立") and k not in types:
            types.append(k)

    urls = collect_urls(other_td, rate_el, fish_el)
    website = None
    sns = []
    cands = []
    for u in urls:
        if is_sns(u):
            if u not in sns:
                sns.append(u)
        elif not host_in(u, NOT_WEBSITE_HOSTS) and "." in host_of(u) and not is_asset_url(u):
            cands.append(u)
    if cands:
        # 複数ホストがあれば多く出てくるホストを公式とみなす（例: 料金欄のプランリンク×3 と造船所リンク×1）
        cnt = {}
        for el in (other_td, rate_el, fish_el):
            if el is None:
                continue
            for a in el.find_all("a", href=True):
                if is_asset_url(a["href"].strip()):
                    continue
                h = host_of(a["href"].strip())
                cnt[h] = cnt.get(h, 0) + 1
        best = sorted(range(len(cands)), key=lambda i: (-cnt.get(host_of(cands[i]), 0), i))[0]
        website = norm_website(cands[best])

    text_all = norm_for_parse(other + "\n" + "\n".join(rate_lines) + "\n" + "\n".join(fish_lines))
    methods = []
    for m in METHOD_RE.finditer(text_all):
        w = METHOD_CANON.get(m.group(0), m.group(0))
        if w not in methods:
            methods.append(w)

    capacity = None
    caps = [int(x) for x in re.findall(r"定員\s*[:：]?\s*(\d{1,3})\s*(?:名|人)", text_all)]
    if caps:
        capacity = max(caps)

    acc = []
    if addr_access:
        acc.append("アクセス: " + (addr_access if len(addr_access) <= 80 else addr_access[:79] + u"…"))
    if parking:
        acc.append("駐車場: " + parking)
    if lodging:
        acc.append("宿泊: " + lodging)
    access = " ／ ".join(acc)

    schedule_text = ""
    if slots:
        schedule_text = "出船時間帯: " + "・".join(uniq(slots))

    return {
        "src": SRC,
        "src_id": slug,
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
        "facilities": facilities,
        "capacity": capacity,
        "access": access,
        "description": short_desc(other, services),
        "plans": plans,
        "schedule_text": schedule_text,
        "fetched": fetched or datetime.date.today().isoformat(),
    }


# ---------------------------------------------------------------- main

def save(records, out_path):
    d = os.path.dirname(out_path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    atomic_write(out_path, json.dumps(records, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description="yugyosen-navi.com crawler")
    ap.add_argument("--limit", type=int, default=0, help="サイトマップの先頭N件だけ")
    ap.add_argument("--ids", default="", help="スラッグ（またはURL）をカンマ区切りで指定")
    ap.add_argument("--out", default=OUT_PATH, help="出力JSON")
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    ap.add_argument("--refresh-list", action="store_true", help="サイトマップだけ再取得")
    ap.add_argument("--interval", type=float, default=1.0, help="リクエスト間隔(秒, 下限0.8)")
    ap.add_argument("--log", default=LOG_PATH)
    args = ap.parse_args()

    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    log = Logger(args.log)
    f = Fetcher(log, interval=args.interval, refresh=args.refresh)
    log("START %s out=%s limit=%s ids=%s refresh=%s" % (SRC, out_path, args.limit, args.ids or "-", args.refresh))

    sitemap_urls = crawl_sitemap(f, log, refresh_list=args.refresh_list)
    log("sitemap: %d boat pages" % len(sitemap_urls))
    if args.ids:
        urls = []
        for part in re.split(r"[,\s]+", args.ids.strip()):
            if not part:
                continue
            if part.startswith("http"):
                u = part if part.endswith("/") else part + "/"
            else:
                u = "%s/%s/" % (BASE, part.strip("/"))
            if u not in urls:
                urls.append(u)
    else:
        urls = list(sitemap_urls)
        if args.limit:
            urls = urls[:args.limit]
    in_sitemap = set(u.rstrip("/") for u in sitemap_urls)

    total = len(urls)
    records = []
    stats = {"net": 0, "cache": 0, "missing": 0, "fail": 0, "noparse": 0}
    t0 = time.time()
    for i, url in enumerate(urls, 1):
        slug = slug_of(url)
        cname = cache_name_for_slug(slug)
        html, how, final = f.get(url, cname)
        if html is None:
            if how in ("missing", "cache-missing"):
                stats["missing"] += 1
            else:
                stats["fail"] += 1
            log("skip %s (%s)" % (url, how))
        else:
            stats["cache" if how == "cache" else "net"] += 1
            path = os.path.join(CACHE, cname)
            fetched = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
            try:
                rec = parse_detail(html, final if final else url, fetched)
            except Exception as e:  # noqa
                log("ERROR parse %s: %r" % (url, e))
                rec = None
            if rec is None:
                stats["noparse"] += 1
                log("WARN no boat info on %s" % url)
            else:
                if in_sitemap and url.rstrip("/") not in in_sitemap and final.rstrip("/") not in in_sitemap:
                    rec["stale"] = True
                records.append(rec)
        if i % 10 == 0 or i == total:
            el = time.time() - t0
            log("progress %d/%d records=%d net=%d cache=%d missing=%d fail=%d (%.0fs)" % (
                i, total, len(records), stats["net"], stats["cache"], stats["missing"], stats["fail"], el))
        if i % SAVE_EVERY == 0:
            save(records, out_path)
    save(records, out_path)
    log("stats %s" % json.dumps(stats))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
