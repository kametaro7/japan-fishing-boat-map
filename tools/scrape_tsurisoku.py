#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""つりそく（釣場速報） https://www.tsurisoku.com/ 釣り船クローラ

列挙: 一覧 /allarea/page/N?fct=photo（1ページ10件、空ページまで。約195件）
      → 船名 / 電話 / 県＞エリア＞港 / 営業形態（乗合船・仕立船・磯渡し・筏・カセ…）/ 紹介 / 設備タグ
詳細: 一覧のリンク /f/<slug> は 302 で次のどちらかへ転送される（転送先を redir_<slug>.json にキャッシュ）。
  (a) 県サブドメインの船宿ページ https://<pref>.tsurisoku.com/<site>/
      → トップ（SNSリンク・地図）/ 料金 price/ / アクセス access/ を取得。
        住所・電話・定休日・かな・紹介・設備は、そのページ自身が認証なしで読む公開 JSON
        https://www.tsurisoku.com/wp-json/v2/site/profile/show/<id>/ と /site/data/show/<id>/ から取得。
        座標は Google マイマップ iframe の ll=（地図の中心点）。
  (b) 船宿の公式サイト（外部ドメイン）→ website=転送先。レコードは一覧の情報だけで作る。
  料金は画像のことが多い（img の alt に文字があれば使う）。出船カレンダー（Google カレンダー）と釣果は取らない。
除外: 営業形態が「釣具＆エサ」「釣り堀」だけの掲載（船宿以外）は出力しない（work/sources/tsurisoku_excluded.json に一覧）。
作法: 全ホスト合わせて直列・間隔 --interval 秒（既定1.0, 下限0.8）・429/503 は指数バックオフ(最大5回)・
      生データは work/cache/tsurisoku/ に保存し再実行時はキャッシュを使う（--refresh で再取得）・robots.txt 準拠。

使い方:
  python3 tools/scrape_tsurisoku.py                      # 全件
  python3 tools/scrape_tsurisoku.py --limit 30           # 一覧の先頭30件
  python3 tools/scrape_tsurisoku.py --ids kaishu,shachi --out work/sources/tsurisoku.sample.json
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

BASE = "https://www.tsurisoku.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "work", "cache", "tsurisoku")
LOG_PATH = os.path.join(ROOT, "work", "logs", "tsurisoku.log")
OUT_PATH = os.path.join(ROOT, "work", "sources", "tsurisoku.json")
EXCLUDED_PATH = os.path.join(ROOT, "work", "sources", "tsurisoku_excluded.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MAX_LIST_PAGES = 60

PREF_NAMES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
PREF_ROMAJI = [
    "hokkaido", "aomori", "iwate", "miyagi", "akita", "yamagata", "fukushima", "ibaraki", "tochigi", "gunma",
    "saitama", "chiba", "tokyo", "kanagawa", "niigata", "toyama", "ishikawa", "fukui", "yamanashi", "nagano",
    "gifu", "shizuoka", "aichi", "mie", "shiga", "kyoto", "osaka", "hyogo", "nara", "wakayama",
    "tottori", "shimane", "okayama", "hiroshima", "yamaguchi", "tokushima", "kagawa", "ehime", "kochi", "fukuoka",
    "saga", "nagasaki", "kumamoto", "oita", "miyazaki", "kagoshima", "okinawa",
]
ROMAJI_PREF = dict(zip(PREF_ROMAJI, PREF_NAMES))
SHORT_PREF = {}
for _n in PREF_NAMES:
    SHORT_PREF[_n] = _n
    if _n != "北海道":
        SHORT_PREF[_n[:-1]] = _n

SNS_HOSTS = ("instagram.com", "facebook.com", "fb.com", "fb.me", "twitter.com", "x.com",
             "youtube.com", "youtu.be", "tiktok.com", "line.me", "lin.ee", "line.naver.jp", "threads.net",
             "note.com")
# 公式サイトとして扱わないホスト（掲載サイト自身・予約ポータル・地図・広告など）
NOT_WEBSITE_HOSTS = ("tsurisoku.com", "google.com", "google.co.jp", "goo.gl", "googleusercontent.com",
                     "googlesyndication.com", "doubleclick.net", "googletagmanager.com", "apple.com",
                     "minnaga.com", "chowari.jp", "tsuree.jp", "gyo.ne.jp", "asoview.com", "jalan.net",
                     "rakuten.co.jp", "activityjapan.com", "veltra.com", "fishing-v.jp", "funaduri.jp",
                     "anglers.jp", "wordpress.org", "w.org", "gravatar.com", "adobe.com", "yahoo.co.jp",
                     "map.yahoo.co.jp", "navitime.co.jp", "mapion.co.jp", "anymind360.com",
                     "fishbank.jp", "tsurimaru.jp", "yugyosen.com", "fishing-station.jp", "point-i.jp",
                     "gurenavi.jp", "reserver.co.jp", "tsuri-hack.com", "tsurinews.jp", "hapitas.jp",
                     # 出船カレンダー/フォーム/予約システム/ふるさと納税LP/釣り情報メディア（公式サイトではない）
                     "freecalend.com", "forms.gle", "urkt.in", "dmc-aizu.com", "lurenewsr.com")

# 営業形態（一覧のカテゴリ） → types
CATEGORY_TYPE = {
    "乗合船": "乗合", "仕立船": "仕立", "磯渡し": "磯渡し", "波止渡船": "波止渡船",
    "筏": "筏", "カセ": "カセ", "レンタルボート": "レンタルボート",
    "釣り堀": "釣り堀", "釣具＆エサ": "釣具＆エサ",
}
BOAT_CATEGORIES = ("乗合船", "仕立船", "磯渡し", "波止渡船", "筏", "カセ", "レンタルボート")

CITY_EXCEPTIONS = [
    "四日市市", "廿日市市", "市川市", "市原市", "野々市市", "町田市", "大町市", "十日町市",
    "村上市", "村山市", "東村山市", "武蔵村山市", "羽村市", "大村市", "田村市", "北村山郡",
    "西村山郡", "東村山郡", "中新川郡上市町", "余市郡余市町", "市貝町", "上市町", "余市町",
    "玉村町", "木曽郡上松町",
]

# 釣り物（本文にこの表記がそのまま出てくる場合だけ採用。前後がカタカナの語の一部なら無視: タイラバ→×タイ）
FISH_WORDS = [
    "マダイ", "真鯛", "チダイ", "レンコダイ", "キダイ", "クロダイ", "チヌ", "キチヌ", "メジナ", "グレ", "オナガ",
    "イシダイ", "イシガキダイ", "アマダイ", "甘鯛", "キンメダイ", "イトヨリ", "タイ", "鯛",
    "ブリ", "メジロ", "ハマチ", "ツバス", "ワラサ", "イナダ", "ヒラマサ", "カンパチ", "青物", "サワラ", "サゴシ",
    "ヒラメ", "マゴチ", "カレイ", "マコガレイ", "アジ", "マアジ", "シマアジ", "サバ", "イワシ", "カマス", "サヨリ",
    "タチウオ", "太刀魚", "アオリイカ", "ケンサキイカ", "スルメイカ", "ヤリイカ", "コウイカ", "マルイカ", "ヒイカ",
    "イカ", "マダコ", "イイダコ", "タコ", "メバル", "カサゴ", "ガシラ", "オニカサゴ", "アコウ", "キジハタ",
    "オオモンハタ", "アカハタ", "マハタ", "ハタ", "クエ", "アラ", "ソイ", "アイナメ", "アカムツ", "ノドグロ",
    "ユメカサゴ", "イサキ", "シロギス", "キス", "カワハギ", "ウマヅラハギ", "トラフグ", "フグ", "ハゼ",
    "スズキ", "シーバス", "マグロ", "クロマグロ", "キハダ", "ビンチョウ", "ビンナガ", "トンボ", "ヨコワ",
    "カツオ", "シイラ", "ホウボウ", "ハモ", "アナゴ", "ベラ", "根魚",
]
METHOD_WORDS = [
    "タイラバ", "スーパーライトジギング", "SLJ", "ライトジギング", "ジギング", "ティップラン", "エギング",
    "イカメタル", "オモリグ", "ひとつテンヤ", "一つテンヤ", "テンヤ", "サビキ", "天秤", "胴付き", "フカセ",
    "カゴ釣り", "泳がせ", "のませ", "飲ませ", "キャスティング", "トンジギ", "タテ釣り", "落とし込み", "ウキ釣り",
    "エサ釣り", "ルアー", "アジング", "メバリング", "ライトゲーム", "電動", "五目", "夜焚き", "半夜",
]

KATA = "ァ-ヺー"


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
    if binary:
        with open(tmp, "wb") as f:
            f.write(data)
    else:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
    os.replace(tmp, path)


def host_of(url):
    m = re.match(r"^https?://([^/:?#]+)", (url or "").strip(), re.I)
    return m.group(1).lower() if m else ""


def host_in(h, hosts):
    return any(h == d or h.endswith("." + d) for d in hosts)


def truncate(s, n):
    s = one_line(s)
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------- fetcher

class Fetcher(object):
    """全ホスト合わせて直列。robots.txt はホストごとにキャッシュして判定。"""

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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        })
        if not os.path.isdir(CACHE):
            os.makedirs(CACHE)
        self.robots = {}

    def _wait(self):
        dt = time.time() - self.last
        if dt < self.interval:
            time.sleep(self.interval - dt)

    def _robots_for(self, url):
        m = re.match(r"^(https?://[^/]+)", url)
        origin = m.group(1)
        host = host_of(url)
        if host in self.robots:
            return self.robots[host]
        path = os.path.join(CACHE, "robots_%s.txt" % host)
        if not os.path.exists(path):
            text = ""
            for attempt in range(3):
                self._wait()
                try:
                    r = self.s.get(origin + "/robots.txt", timeout=60, allow_redirects=True)
                    self.last = time.time()
                    self.n_net += 1
                    text = r.text if r.status_code == 200 else ""
                    break
                except requests.RequestException as e:
                    self.last = time.time()
                    self.log("WARN robots %s: %r" % (host, e))
                    time.sleep(5 * (2 ** attempt))
            atomic_write(path, text)
        rp = robotparser.RobotFileParser()
        with open(path, encoding="utf-8", errors="replace") as f:
            rp.parse(f.read().splitlines())
        self.robots[host] = rp
        return rp

    def _request(self, url, allow_redirects):
        """returns (response or None, err)"""
        err = ""
        for attempt in range(6):
            self._wait()
            try:
                r = self.s.get(url, timeout=60, allow_redirects=allow_redirects)
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
            return r, ""
        self.log("ERROR %s gave up: %s" % (url, err))
        return None, err or "fail"

    def get(self, url, cache_name):
        # type: (str, str) -> Tuple[Optional[str], str]
        """ページ取得（リダイレクトで別URLに飛んだら missing 扱い）。
        returns (text or None, how) how in cache|cache-missing|net|missing|httpNNN|fail"""
        if not self._robots_for(url).can_fetch("*", url):
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
        r, err = self._request(url, allow_redirects=True)
        if r is None:
            return None, "fail"
        final = r.url.split("#")[0]
        moved = final.rstrip("/") != url.rstrip("/")
        if r.status_code == 404 or r.status_code == 410 or moved:
            atomic_write(miss, json.dumps({"url": url, "final": final, "status": r.status_code,
                                           "at": now_str()}, ensure_ascii=False))
            return None, "missing"
        if r.status_code != 200:
            return None, "http%d" % r.status_code
        atomic_write(path, r.content, binary=True)
        return r.content.decode("utf-8", errors="replace"), "net"

    def resolve(self, url, cache_name):
        # type: (str, str) -> Tuple[Optional[dict], str]
        """リダイレクト先を追わずに Location を記録する（/f/<slug> 用）"""
        if not self._robots_for(url).can_fetch("*", url):
            raise RuntimeError("robots.txt disallows %s" % url)
        path = os.path.join(CACHE, cache_name)
        if not self.refresh and os.path.exists(path):
            self.n_cache += 1
            with open(path, encoding="utf-8") as f:
                return json.load(f), "cache"
        r, err = self._request(url, allow_redirects=False)
        if r is None:
            return None, "fail"
        d = {"slug": url.rstrip("/").split("/")[-1], "status": r.status_code,
             "location": r.headers.get("Location"), "at": now_str()}
        if r.status_code == 200:
            atomic_write(os.path.join(CACHE, cache_name.replace(".json", ".html")), r.content, binary=True)
        atomic_write(path, json.dumps(d, ensure_ascii=False))
        return d, "net"


# ---------------------------------------------------------------- listing

def parse_listing(html):
    soup = BeautifulSoup(html, "lxml")
    items = []
    for box in soup.select(".search_listgroup .search_listbox"):
        a = box.select_one(".search_name a")
        if a is None or not a.get("href"):
            continue
        href = a["href"].strip()
        m = re.search(r"/(?:f|facility)/([^/?#]+)", href)
        if not m:
            continue
        tel_el = box.select_one(".search_tel")
        areas = []
        for aa in box.select(".search_area a"):
            km = re.search(r"/area/([^/?#]+)", aa.get("href", ""))
            areas.append({"label": one_line(aa.get_text()), "key": km.group(1) if km else ""})
        cat_el = box.select_one(".search_category")
        cats = [c.strip() for c in re.split(r"[・/／、]", one_line(cat_el.get_text()) if cat_el else "") if c.strip()]
        info_el = box.select_one(".search_infotext")
        tags = []
        for li in box.select(".search_conditiondata li"):
            t = one_line(li.get_text())
            if t and t not in tags:
                tags.append(t)
        items.append({
            "slug": m.group(1),
            "href": href,
            "name": one_line(a.get_text()),
            "tel": one_line(tel_el.get_text()) if tel_el else "",
            "areas": areas,
            "categories": cats,
            "info": clean_text(info_el.get_text("\n")) if info_el else "",
            "tags": tags,
        })
    return items


def crawl_listing(fetcher, log):
    # type: (Fetcher, Logger) -> List[dict]
    items = []  # type: List[dict]
    seen = set()
    for page in range(1, MAX_LIST_PAGES + 1):
        url = "%s/allarea/page/%d?fct=photo" % (BASE, page)
        html, how = fetcher.get(url, "list_%d.html" % page)
        if html is None:
            log("WARN listing page %d: %s" % (page, how))
            break
        page_items = parse_listing(html)
        new = 0
        for it in page_items:
            if it["slug"] not in seen:
                seen.add(it["slug"])
                it["list_page"] = page
                items.append(it)
                new += 1
        log("list page %d: %d items (%d new, total %d, %s)" % (page, len(page_items), new, len(items), how))
        if not page_items:
            break
    return items


# ---------------------------------------------------------------- parsing helpers

PHONE_RE = re.compile(r"(0\d{1,4}-\d{1,4}-\d{3,4}|0\d{9,10})")


def norm_tel(s):
    s = nfkc(s)
    s = re.sub(r"[‐‑‒–—―ー−]", "-", s)
    s = re.sub(r"\(\s*(\d{2,5})\s*\)", r"-\1-", s)
    s = s.replace(" ", "")
    m = PHONE_RE.search(s)
    if not m:
        return ""
    t = m.group(1).strip("-")
    if "-" not in t and len(t) == 11 and t[:3] in ("090", "080", "070", "050"):
        t = "%s-%s-%s" % (t[:3], t[3:7], t[7:])
    return t


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
    if m and len(m.group(1)) <= 9:
        return m.group(1)
    m = re.match(r"^([^市区町村郡]{1,7}?[市区町村])", rest)
    if m:
        return m.group(1)
    return ""


def normalize_address(raw, pref):
    a = one_line(nfkc(raw).replace("　", " "))
    a = re.sub(r"^〒?\s*\d{3}\s*-\s*\d{4}\s*", "", a)
    a = re.sub(r"^(住所|所在地)\s*[:：]\s*", "", a)
    a = a.strip(" ")
    # 番地の後ろの付記「(無料駐車場・待合所…があります)」「（〇〇の横）」は住所でないので落とす
    a = re.sub(r"\s*[（(][^（()）]*[）)]\s*$", "", a) if re.search(r"[\d一二三四五六七八九十丁目番地号]\s*[（(]", a) else a
    a = re.sub(r"\s*[※].*$", "", a).strip(" 、,")
    # 「自宅は〜」は事業所の住所として公開されたものではないので使わない
    if "自宅" in a:
        return ""
    # 「京都府→京都府舞鶴市…」「大阪府を大阪府阪南市…に設定し」のような入力ゴミ → 県名から始まる番地付きの部分だけ
    if re.search(r"(→|です|ます|。|設定|を)", a):
        segs = re.findall(r"((?:北海道|東京都|京都府|大阪府|[一-龥]{2,3}県)[^\s。、→をに]{2,40}?\d[\d\-－ー―]*)", a)
        if not segs:
            return ""
        a = segs[-1]
    a = re.sub(r"(?<=\d)[―ー−－‐](?=\d)", "-", a)
    a = re.sub(r"(\d)\s+[^\d\s].*$", r"\1", a)            # 番地の後ろの建物・目印の付記
    a = re.sub(r"(?<=[都道府県郡市区町村])[\s・]+", "", a)
    a = re.sub(r"(?<=[一-龥々])\s+(?=\d)", "", a)
    if not a or not re.search(r"[一-龥ぁ-んァ-ン々]", a):
        return ""
    if re.fullmatch(r"(なし|無し|未定|非公開|不明|-+)", a):
        return ""
    # 数字・丁目の直後に続く「（駐車場）」「〇〇港」等の付記は残す（住所の一部とは限らないが削ると誤りになるため）
    for p in PREF_NAMES:
        if a.startswith(p):
            return a
    for short, full in SHORT_PREF.items():
        if short != full and a.startswith(short) and not a.startswith(full):
            # 「和歌山市…」のような市名始まりと区別する（県庁所在地と同名の市）
            if re.match(re.escape(short) + r"(市|郡)", a) and short + "市" in a[:len(short) + 1]:
                break
            return full + a[len(short):]
    if pref:
        a = pref + a
    return a


TIME_RE = re.compile(r"(午前|午後|AM|PM|am|pm)?\s*(\d{1,2})\s*(?::|時(?!間))\s*(\d{1,2}|半)?\s*分?")


def find_times(s):
    """returns list of (HH:MM, start, end)（「5:30」「5時」「5時30分」だけ。「6時間」は除く）"""
    res = []
    for m in TIME_RE.finditer(s):
        ap, h, mi = m.group(1), int(m.group(2)), m.group(3)
        if ":" not in m.group(0) and "時" not in m.group(0):
            continue
        if mi == "半":
            mm = 30
        elif not mi:
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
    # 「12,000〜20,000円」の下限（円が後ろの数字にしか付かない範囲表記）
    r"|(\d{1,3}(?:,\d{3})+|\d{4,6})(?=\s*[〜~～\-]\s*(?:\d{1,3}(?:,\d{3})+|\d{4,6})\s*円)")


def parse_amounts(s):
    """金額(円)の抽出。s は NFKC 済みを想定。曖昧な表記は None"""
    s = re.sub(r"(\d)、(\d{3})(?!\d)", r"\1,\2", s)
    out = []
    for m in AMOUNT_RE.finditer(s):
        if m.group(1):
            # 「1万」の直後が円でも数字でもない（1万匹 等）なら金額でない
            tail = s[m.end():m.end() + 1]
            if not m.group(0).endswith("円") and not m.group(2) and not m.group(3):
                if tail not in ("円", "", " ", "(", "（", "/", "、", ",", "～", "〜", "~", "-"):
                    continue
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
        if m.group(7):
            out.append((int(m.group(7).replace(",", "")), m.start(), m.end()))
            continue
        num = m.group(6)
        pre = s[max(0, m.start() - 2):m.start()]
        if re.search(r"\d[.,]$", pre):
            out.append((None, m.start(), m.end()))
            continue
        # 「10.000円」→ 10000（ピリオド区切りの千位）
        digits = re.sub(r"[,.]", "", num)
        out.append((int(digits), m.start(), m.end()))
    return out


PER_BOAT_RE = re.compile(r"(隻|貸切|貸し切り|チャーター|仕立|艇|まで|迄)")
PER_PERSON_STRICT_RE = re.compile(
    r"(/\s*(?:1|一)?\s*(?:人|名)|@|お\s*(?:1|一)\s*人|(?:1|一)\s*(?:人|名)\s*(?:あたり|当たり|につき)?\s*$)")
PER_PERSON_RE = re.compile(
    r"(/\s*(?:1|一)?\s*(?:人|名)|@|(?:お|御)?\s*(?:1|一)\s*(?:人|名)\s*様?(?!\s*[～〜~\-増迄ま])|大人|男性)")


def plan_price(text, kind):
    """1人あたり基本料金（最初に出てくる金額）。1隻料金・人数別の仕立料金・曖昧表記は None"""
    amounts = parse_amounts(text)
    if not amounts:
        return None
    v, st, en = amounts[0]
    if v is None:
        return None
    before = text[max(0, st - 10):st]
    ctx = before + text[st:en] + text[en:en + 12]
    if kind == "仕立":
        if not PER_PERSON_STRICT_RE.search(before) and not re.match(r"\s*/\s*(?:1|一)?\s*(?:人|名)", text[en:en + 6]):
            return None
    else:
        if PER_BOAT_RE.search(ctx) and not PER_PERSON_RE.search(ctx):
            return None
    if v < 500 or v > 100000:
        return None
    return v


def _minutes(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def departures(text):
    """テキスト中の出船時刻 → [(depart, return, meet)]。時刻が無ければ []"""
    t = text
    meet = None
    deps = []
    for hm, st, en in find_times(t):
        pre = t[max(0, st - 8):st]
        if meet is None and re.search(r"(集合|受付|集合時間)\s*[:：]?\s*$", pre):
            meet = hm
        elif re.search(r"(帰港|沖上がり|納竿|終了)\s*[:：]?\s*$", pre):
            deps.append((hm, st, en, "ret"))
        else:
            deps.append((hm, st, en, "dep"))
    if not deps:
        return [(None, None, meet)] if meet else []
    pairs = []
    i = 0
    while i < len(deps):
        d = deps[i]
        if d[3] == "ret":
            if pairs and pairs[-1][1] is None:
                pairs[-1] = (pairs[-1][0], d[0])
            i += 1
            continue
        if i + 1 < len(deps):
            nxt = deps[i + 1]
            between = t[d[2]:nxt[1]]
            if re.fullmatch(r"\s*(?:頃|ごろ|位|くらい)?\s*(?:出船|出港)?\s*[～〜~\-ー−]\s*(?:帰港)?\s*", between) or nxt[3] == "ret":
                span = _minutes(nxt[0]) - _minutes(d[0])
                if span < 0:
                    span += 24 * 60
                pairs.append((d[0], nxt[0] if (span >= 180 or nxt[3] == "ret") else None))
                i += 2
                continue
        pairs.append((d[0], None))
        i += 1
    uniq = []
    for p in pairs:
        if p not in uniq:
            uniq.append(p)
    if len(uniq) > 1 and re.search(r"便|午前|午後|朝|昼|夜", t):
        return [(p[0], p[1], meet) for p in uniq]
    return [(uniq[0][0], uniq[0][1], meet)]


def match_words(text, words):
    """カタカナ語の一部（タイラバのタイ等）を除いて、出てくる順に語を返す"""
    t = nfkc(text)
    found = []  # (pos, word)
    taken = [False] * len(t)
    for w in sorted(set(words), key=lambda x: -len(x)):
        wn = nfkc(w)
        for m in re.finditer(re.escape(wn), t):
            st, en = m.start(), m.end()
            if any(taken[st:en]):
                continue
            if re.match(r"[%s]" % KATA, wn[0]) and st > 0 and re.match(r"[%s]" % KATA, t[st - 1]):
                continue
            if re.match(r"[%s]" % KATA, wn[-1]) and en < len(t) and re.match(r"[%s]" % KATA, t[en]):
                continue
            if re.match(r"[A-Za-z]", wn[0]) and ((st > 0 and re.match(r"[A-Za-z]", t[st - 1])) or
                                                 (en < len(t) and re.match(r"[A-Za-z]", t[en]))):
                continue
            for k in range(st, en):
                taken[k] = True
            found.append((st, w))
    found.sort()
    res = []
    for _, w in found:
        if w not in res:
            res.append(w)
    return res


_ROLE = r"(?:船長|キャプテン|代表者?|社長|店主|親方|オーナー|船頭|女将)"
NAME_SENT_RE = re.compile(
    r"(と申します|と申し|[一-龥]{1,4}\s?(?:船長|キャプテン|代表|社長|店主|親方|船頭|氏|さん)(?![一-龥])"
    r"|[A-Za-z]{2,12}\s?(?:船長|キャプテン)"
    r"|" + _ROLE + r"\s*(?:の|は|である|を務める|、|,|：|:)?\s*[一-龥]{2,4}\s?[一-龥]{0,3}\s*(?:さん|氏|です|が|と申|（|\()"
    r"|" + _ROLE + r"\s*(?:の|は|、|,|：|:)?\s*[一-龥]{1,4}[ 　][一-龥]{1,4}"
    r"|[（(][ぁ-んァ-ヶー]{2,8}[ 　][ぁ-んァ-ヶー]{2,8}[）)])")


def strip_personal(text):
    """代表者・船長の個人名を含みそうな文を落とす"""
    sents = re.split(r"(?<=[。！!？?\n])", text or "")
    keep = [s for s in sents if s.strip() and not NAME_SENT_RE.search(s)]
    return "".join(keep).strip()


def summarize(text, n=100):
    t = one_line(strip_personal(clean_text(text)))
    t = re.sub(r"\.{3,}$|…+$", "", t).strip()
    return truncate(t, n) if t else ""


LL_RES = [
    # 「&ehbc=2E312Fll=35.54,135.19」（& 抜け）や「ll=34.71&136.89」（区切りが &）の崩れた埋め込みも読む
    re.compile(r"(?:[?&;]|2E312F)ll=\s*(-?\d+\.\d+)\s*(?:,|%2C|&)\s*(-?\d+\.\d+)"),
    re.compile(r"!3d(-?\d+(?:\.\d+)?)!2d(-?\d+(?:\.\d+)?)"),
    re.compile(r"[?&;](?:q|center)=\s*(-?\d+(?:\.\d+)?)\s*(?:,|%2C)\s*(-?\d+(?:\.\d+)?)"),
]
LL_RE_LONLAT = re.compile(r"!2d(-?\d+(?:\.\d+)?)!3d(-?\d+(?:\.\d+)?)")


def map_latlon(html):
    if not html:
        return None
    srcs = re.findall(r"<iframe[^>]+src=[\"']([^\"']+)[\"']", html, flags=re.I)
    for src in srcs:
        if "google" not in src or "map" not in src:
            continue
        src = src.replace("&amp;", "&")
        for rx in LL_RES:
            m = rx.search(src)
            if m:
                la, lo = float(m.group(1)), float(m.group(2))
                if 20.0 <= la <= 46.6 and 122.0 <= lo <= 154.0:
                    return round(la, 7), round(lo, 7)
        m = LL_RE_LONLAT.search(src)
        if m:
            lo, la = float(m.group(1)), float(m.group(2))
            if 20.0 <= la <= 46.6 and 122.0 <= lo <= 154.0:
                return round(la, 7), round(lo, 7)
    return None


def main_content(soup):
    for sel in (".maincontents", ".contents_wrap", ".topinfo"):
        el = soup.select_one(sel)
        if el is not None:
            return el
    return soup.body or soup


def external_links(html):
    """トップページ本文中の外部リンク → (website candidates, sns)"""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "footer"]):
        t.decompose()
    sites, sns = [], []
    for a in soup.find_all("a", href=True):
        u = a["href"].strip()
        if not re.match(r"^https?://", u, re.I):
            continue
        h = host_of(u)
        if not h or "." not in h:
            continue
        if host_in(h, SNS_HOSTS):
            if "sharer" in u or "/share" in u or "intent/tweet" in u:
                continue
            if u not in sns:
                sns.append(u)
        elif not host_in(h, NOT_WEBSITE_HOSTS):
            label = one_line(a.get_text(" ")) + " " + " ".join(i.get("alt") or "" for i in a.find_all("img"))
            # 「〇〇オンラインショップ」「WEB予約」「乗船名簿」は船宿の公式サイトではない
            if re.search(r"(ショップ|shop|通販|予約|名簿|ふるさと納税)", label, re.I):
                continue
            if u not in sites:
                sites.append(u)
    return sites, sns


# ---------------------------------------------------------------- price page

KIND_TOKEN_RE = re.compile(
    r"[<＜【\[〔《]?\s*(乗\s*合\s*い?\s*船?|乗り合い船?|仕立て?船?|チャーター(?:船)?|貸\s*切り?|貸し切り)\s*[>＞】\]〕》]?")
NOTICE_RE = re.compile(r"(旧料金|改定|値上げ|キャンセル料|お知らせ|→)")


def price_segments(html):
    """料金ページ本文 → テキスト行（img の alt を含む。表は行ごと）"""
    soup = BeautifulSoup(html, "lxml")
    box = soup.select_one(".price_text") or soup.select_one(".maincontents .page_text")
    if box is None:
        return [], 0
    n_img = 0
    for img in box.find_all("img"):
        alt = one_line(img.get("alt") or "")
        n_img += 1
        img.replace_with("\n" + alt + "\n" if alt else "\n")
    for br in box.find_all("br"):
        br.replace_with("\n")
    for tr in box.find_all("tr"):
        cells = [one_line(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        tr.replace_with("\n" + " ".join(c for c in cells if c) + "\n")
    for blk in box.find_all(["p", "div", "li", "h2", "h3", "h4", "h5", "dt", "dd"]):
        blk.insert_before("\n")
        blk.insert_after("\n")
    lines = []
    for ln in box.get_text().split("\n"):
        ln = one_line(nfkc(ln))
        if ln:
            lines.append(ln)
    return lines, n_img


PER_HEAD_TRIM_RE = re.compile(
    r"[\s:：・/／,、]*((?:大人|男性|女性|子供|小人|お一人様?|お1人様?|1人|1名|一人|おひとり様?|料金|乗船料金?|"
    r"基本料金|価格|代金)[\s:：]*)+$")


# 乗船料金ではない行（貸し道具・エサ・駐車場・バッテリー等の物販/付帯料金）
GOODS_RE = re.compile(
    r"(レンタル|タックル|貸し?竿|貸し道具|竿受け|ロッド|リール|バッテリー|駐車|エサ|餌|オキアミ|イワシ|クリル|エビ|"
    r"ゴカイ|イソメ|袋|仕かけ|仕掛|ライフジャケット|弁当|スタンプ|会員|入会|保険|クーラー|氷\s*$|1台|1つ|1本|\d+\s*kg|"
    r"SET|セット|仮眠|コンロ|貸出|クランプ|1人分|生き餌|活き餌)")
# 2,000円未満で名前が生き餌・仕かけのものは物販
BAIT_SMALL_RE = re.compile(r"(生き|活き|サビキ|仕掛|仕かけ|イカナゴ|エサ|餌)")


def tidy_name(s):
    s = one_line(s)
    # 名前に混ざった時刻・「料金:」・「出船時間は問い合わせを」を落とす
    s = re.sub(r"\d{1,2}:\d{2}\s*(?:[~〜～\-]\s*\d{1,2}:\d{2})?", " ", s)
    s = re.sub(r"(出船時間は[^\s]*|料金\s*[:：]|^便\s*[:：])", " ", s)
    s = one_line(s).strip(" :：・、,/")
    # 対応の取れていない括弧を落とす
    for op, cl in (("(", ")"), ("（", "）")):
        if s.count(op) > s.count(cl):
            i = s.rfind(op)
            s = (s[:i] + s[i + 1:]).strip()
        elif s.count(cl) > s.count(op):
            i = s.find(cl)
            s = (s[:i] + s[i + 1:]).strip()
    return s.strip(" :：・、,/")


def build_plans(lines, src_url, default_kind, boat_targets):
    plans = []
    heading = ""
    for line in lines:
        amounts = parse_amounts(line)
        if not amounts:
            # 金額の無い短い行は次の行の見出し（例: 「トンジギ」「【午前便】」）
            if len(line) <= 24 and not NOTICE_RE.search(line) and not re.search(r"(あり|なし|完備|付き|OK|可能|です|ます)$", line):
                heading = line.strip("【】[]■●◆◇□○・ ")
            else:
                heading = ""
            continue
        if NOTICE_RE.search(line):
            continue
        toks = list(KIND_TOKEN_RE.finditer(line))
        chunks = []
        if toks:
            pre = line[:toks[0].start()]
            pre_am = parse_amounts(pre)
            if pre_am:
                # 「セミロング便 1人13000円 チャーターの場合+4000円」: 種別語より前にも金額がある
                chunks.append((default_kind, "", pre))
                title = tidy_name(PER_HEAD_TRIM_RE.sub("", pre[:pre_am[0][1]]))
            else:
                title = tidy_name(pre)
            for i, tk in enumerate(toks):
                end = toks[i + 1].start() if i + 1 < len(toks) else len(line)
                word = re.sub(r"\s", "", tk.group(1))
                kind = "乗合" if "乗" in word else "仕立"
                chunks.append((kind, title, line[tk.end():end]))
        else:
            chunks.append((default_kind, "", line))
        for kind, title, chunk in chunks:
            am = parse_amounts(chunk)
            if not am:
                continue
            # 「チャーターの場合+4000円」「1人+1000円」のような加算額は料金プランではない
            if re.match(r"\s*(の場合|の際|時は|は\s*[+＋])", chunk) or re.search(r"[+＋]\s*$", chunk[:am[0][1]]):
                continue
            head = chunk[:am[0][1]]
            head = PER_HEAD_TRIM_RE.sub("", head).strip(" :：・、,（(")
            if GOODS_RE.search(nfkc(" ".join([heading if not title else "", title, head]))) or \
                    re.match(r"\s*円?\s*/\s*\d+\s*kg", chunk[am[0][2]:]):
                continue
            name_parts = [x for x in (heading if not title else "", title, head) if x]
            name = tidy_name(truncate(" ".join(name_parts), 60))
            if re.fullmatch(r"(平日|土日祝?|祝日|大人|男性|女性|通常)?", name):
                name = ("%s %s" % (kind or "料金", name)).strip()
            price = plan_price(chunk, kind)
            # 「女性は1000円割引」は料金ではない / 安い生き餌・仕かけは物販
            if re.match(r"\s*円?\s*(割引|引き|OFF|オフ|値引)", chunk[am[0][2]:], re.I):
                continue
            if price is not None and price < 2000 and BAIT_SMALL_RE.search(nfkc(name)):
                continue
            ptxt = truncate(chunk.strip(" 、,"), 120)
            if kind == "仕立" and price is None and am[0][0]:
                ptxt = truncate("1隻%s円（%s）" % ("{:,}".format(am[0][0]), chunk.strip(" 、,")), 120)
            deps = departures(line) or [(None, None, None)]
            tg = [x for x in boat_targets if x in nfkc(name + " " + title)]
            for depart, ret, meet in deps:
                p = {
                    "name": name,
                    "kind": kind or "",
                    "targets": tg,
                    "price": price,
                    "price_text": ptxt,
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
        heading = ""
    return plans


SCHED_KEY_RE = re.compile(r"(出船|出港|集合|受付|帰港|沖上がり|午前便|午後便|夜便|半夜便|朝便|便)")


def schedule_sentences(texts):
    out = []
    for t in texts:
        for sent in re.split(r"(?<=[。！!\n])|\s{2,}", nfkc(t or "")):
            s = one_line(sent)
            if not s or not find_times(s) or not SCHED_KEY_RE.search(s):
                continue
            # 「出船確認は前日の19時にご連絡下さい」のような連絡・問合せの時刻は出船時刻ではない
            if re.search(r"(連絡|電話|確認|問い?合わ?せ|キャンセル|営業時間)", s):
                continue
            s = strip_personal(s)
            if s and s not in out:
                out.append(truncate(s, 80))
    return out


# ---------------------------------------------------------------- record

def pref_from(listing, subhost, address):
    for a in listing.get("areas") or []:
        p = SHORT_PREF.get(a["label"])
        if p:
            return p
        p = ROMAJI_PREF.get(a["key"])
        if p:
            return p
    if subhost and subhost in ROMAJI_PREF:
        return ROMAJI_PREF[subhost]
    for p in PREF_NAMES:
        if address and address.startswith(p):
            return p
    return ""


def port_from(listing):
    areas = [a["label"] for a in (listing.get("areas") or []) if a["label"] and not SHORT_PREF.get(a["label"])]
    if not areas:
        return ""
    last = areas[-1]
    parts = [p for p in re.split(r"[・/／]", last) if p]
    for p in reversed(parts):
        if re.search(r"(港|漁港|浦|湾)$", p):
            return p
    return last


def is_boat(listing):
    cats = listing.get("categories") or []
    if any(c in BOAT_CATEGORIES and c != "筏" for c in cats):
        return True
    if "筏" in cats:
        # 「歩いて渡れる海上釣り堀」「陸続きの筏」は船を出さない（遊漁船ではない）
        if "釣り堀" in cats or re.search(r"(歩いて渡れる|陸続き)", listing.get("info") or ""):
            return False
        return True
    return False


def parse_json(text):
    if not text:
        return {}
    try:
        d = json.loads(text)
    except ValueError:
        return {}
    if isinstance(d, list):
        d = d[0] if d and isinstance(d[0], dict) else {}
    return d if isinstance(d, dict) else {}


def listify(v):
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, dict):
                x = x.get("name") or x.get("label") or x.get("value") or ""
            x = one_line(str(x))
            if x:
                out.append(x)
        return out
    if isinstance(v, dict):
        return [one_line(str(x)) for x in v.values() if x]
    return [x for x in re.split(r"[、,，/／\s]+", one_line(str(v))) if x]


def html_to_lines(html):
    """topinfo の HTML 文字列 → テキスト行"""
    if not html:
        return []
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr)>", "\n", html)
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    return [one_line(nfkc(x)) for x in s.split("\n") if one_line(x)]


def join_time_headings(lines):
    """「出船時間」だけの行は次の行とつなぐ（次の行の時刻表記をスケジュールとして拾うため）"""
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if re.fullmatch(r"[【\[■●◆・]?\s*(出船|出港|集合|受付|帰港)(時間|時刻)?(について|の目安)?\s*[】\]]?\s*[:：]?", ln) and i + 1 < len(lines):
            out.append(ln.strip("【】[]■●◆・ :：") + ": " + lines[i + 1])
            i += 2
            continue
        out.append(ln)
        i += 1
    return out


ADDR_IN_TEXT_RE = re.compile(r"(?:住所|所在地)\s*[:：]?\s*(?:〒?\s*\d{3}\s*-\s*\d{4}\s*)?([^\s（(※、。]{5,40})")


def site_root_if_form(url):
    """公式サイトの問い合わせ/予約フォームのURLはサイトのトップにする"""
    m = re.match(r"^(https?://[^/?#]+)(/[^?#]*)?", url)
    if m and m.group(2) and re.search(r"(contact|inquiry|form|reserve|yoyaku|toiawase)", m.group(2), re.I):
        return m.group(1) + "/"
    return url


def build_record(listing, redir, pages, profile, data, fetched, topinfo=None):
    slug = listing["slug"]
    loc = (redir or {}).get("location") or ""
    lhost = host_of(loc)
    subhost = lhost.split(".")[0] if lhost.endswith(".tsurisoku.com") and lhost != "www.tsurisoku.com" else ""
    src_url = loc if subhost else "%s/f/%s" % (BASE, slug)

    address_raw = one_line(profile.get("address") or "")
    pref0 = pref_from(listing, subhost, "")
    address = normalize_address(address_raw, pref0) if address_raw else ""
    pref = pref0 or pref_from(listing, subhost, address)
    if address and pref and not address.startswith(pref):
        # 住所の県が一覧の県と違う → 住所側を信じる
        for p in PREF_NAMES:
            if address.startswith(p):
                pref = p
    city = extract_city(address, pref) if address else ""

    tel = norm_tel(profile.get("tel1") or "") or norm_tel(listing.get("tel") or "") or norm_tel(profile.get("tel2") or "")

    website = None
    sns = []
    if loc and lhost and not lhost.endswith("tsurisoku.com"):
        if host_in(lhost, SNS_HOSTS):
            sns.append(loc)
        elif not host_in(lhost, NOT_WEBSITE_HOSTS):
            website = loc
    for key in ("url", "hp", "homepage", "website", "site_url", "official_url", "blog"):
        v = profile.get(key) or data.get(key)
        if isinstance(v, str) and re.match(r"^https?://", v.strip()):
            h = host_of(v)
            if host_in(h, SNS_HOSTS):
                if v.strip() not in sns:
                    sns.append(v.strip())
            elif website is None and not host_in(h, NOT_WEBSITE_HOSTS):
                website = v.strip()
    for key in ("instagram", "facebook", "twitter", "x", "line", "youtube", "tiktok", "sns"):
        for v in listify(profile.get(key)) + listify(data.get(key)):
            if re.match(r"^https?://", v) and v not in sns:
                sns.append(v)
    top = pages.get("top") or ""
    if top:
        sites, s2 = external_links(top)
        for u in s2:
            if u not in sns:
                sns.append(u)
        if website is None and sites:
            website = sites[0]

    types = []
    for c in listing.get("categories") or []:
        t = CATEGORY_TYPE.get(c, c)
        if t and t not in types:
            types.append(t)

    info_text = clean_text(data.get("info") or "")
    list_info = listing.get("info") or ""
    price_lines, n_price_img = price_segments(pages["price"]) if pages.get("price") else ([], 0)
    price_text_all = "\n".join(price_lines)

    corpus = "\n".join([list_info, info_text, price_text_all])
    targets = match_words(corpus, FISH_WORDS)
    methods = match_words(corpus, METHOD_WORDS)

    facilities = []
    for t in (listing.get("tags") or []) + listify(data.get("service")):
        if t and t not in facilities:
            facilities.append(t)

    holidays = one_line(profile.get("holiday") or "")
    capacity = None
    m = re.search(r"定員\s*[:：]?\s*(\d{1,3})\s*(?:名|人)", nfkc(corpus))
    if m:
        capacity = int(m.group(1))

    access = ""
    access_full = ""
    if pages.get("access"):
        soup = BeautifulSoup(pages["access"], "lxml")
        pt = soup.select_one(".maincontents .page_text")
        if pt is not None:
            for t in pt.select(".access_address, h3"):
                t.decompose()
            access_full = strip_personal(clean_text(pt.get_text("\n")))
            access = truncate(access_full, 150)
    if not address and access_full:
        # プロフィールに住所が無いとき、アクセス欄の「住所：〜」だけを使う（駐車場の住所などは使わない）
        m = ADDR_IN_TEXT_RE.search(nfkc(access_full))
        if m:
            a2 = normalize_address(m.group(1), pref)
            if a2 and re.search(r"[市区町村郡]", a2):
                address = a2
                city = extract_city(address, pref)

    ll = map_latlon(pages.get("access")) or map_latlon(top)
    lat, lon = (ll if ll else (None, None))

    desc_src = list_info or info_text
    description = summarize(desc_src, 100)

    default_kind = ""
    if "乗合" in types and "仕立" not in types:
        default_kind = "乗合"
    elif "仕立" in types and "乗合" not in types:
        default_kind = "仕立"
    plans = build_plans(price_lines, (loc.rstrip("/") + "/price/") if subhost else src_url,
                        default_kind, targets)

    sched = schedule_sentences(join_time_headings(price_lines) + html_to_lines((topinfo or {}).get("text"))
                               + [info_text, list_info])
    schedule_text = truncate(" / ".join(sched), 160) if sched else ""

    kana = kata_to_hira(one_line(profile.get("kana") or ""))
    name = listing.get("name") or one_line(profile.get("name") or "")

    rec = {
        "src": "tsurisoku",
        "src_id": slug,
        "src_url": src_url,
        "name": name,
        "kana": kana,
        "pref": pref or None,
        "city": city or None,
        "address": address or None,
        "port": port_from(listing) or None,
        "lat": lat,
        "lon": lon,
        "tel": tel or None,
        "website": site_root_if_form(website) if website else None,
        "sns": sns,
        "types": types,
        "targets": targets,
        "methods": methods,
        "holidays": holidays,
        "facilities": facilities,
        "capacity": capacity,
        "access": access,
        "description": description,
        "plans": plans,
        "schedule_text": schedule_text,
        "fetched": fetched,
    }
    if subhost and not pages.get("top"):
        rec["stale"] = True
    return rec


# ---------------------------------------------------------------- main

def save(records, out_path):
    d = os.path.dirname(out_path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    atomic_write(out_path, json.dumps(records, ensure_ascii=False, indent=1))


def cache_date(name):
    p = os.path.join(CACHE, name)
    if os.path.exists(p):
        return datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat()
    return datetime.date.today().isoformat()


def crawl_detail(f, log, listing, stats):
    slug = listing["slug"]
    redir, how = f.resolve("%s/f/%s" % (BASE, slug), "redir_%s.json" % slug)
    if redir is None:
        stats["fail"] += 1
        log("ERROR resolve %s: %s" % (slug, how))
        return None
    pages = {}
    profile, data, topinfo = {}, {}, {}
    loc = redir.get("location") or ""
    h = host_of(loc)
    fetched_name = "redir_%s.json" % slug
    if redir.get("status") in (301, 302, 303, 307, 308) and h.endswith(".tsurisoku.com") and h != "www.tsurisoku.com":
        sub = h.split(".")[0]
        m = re.match(r"^https?://[^/]+/([^/?#]+)", loc)
        site = m.group(1) if m else slug
        root = "https://%s/%s/" % (h, site)
        for pg in ("top", "price", "access"):
            url = root if pg == "top" else root + pg + "/"
            name = "sub_%s_%s_%s.html" % (sub, site, pg)
            html, how = f.get(url, name)
            if html is None:
                if how not in ("missing", "cache-missing"):
                    stats["fail"] += 1
                    log("ERROR %s %s: %s" % (slug, url, how))
                continue
            pages[pg] = html
            if pg == "top":
                fetched_name = name
        sid = None
        for html in pages.values():
            m = re.search(r"/wp-json/v2/site/profile/show/(\d+)/", html)
            if m:
                sid = m.group(1)
                break
        if sid:
            # topinfo（トップのお知らせ欄）は出船時刻の記載があることが多い → schedule_text 用
            kinds = ("profile", "data", "topinfo") if "/site/topinfo/show/%s/" % sid in (pages.get("top") or "") \
                else ("profile", "data")
            for kind in kinds:
                url = "%s/wp-json/v2/site/%s/show/%s/" % (BASE, kind, sid)
                txt, how = f.get(url, "api_%s_%s.json" % (kind, sid))
                if txt is None and how not in ("missing", "cache-missing"):
                    stats["fail"] += 1
                    log("ERROR %s %s: %s" % (slug, url, how))
                d = parse_json(txt)
                if kind == "profile":
                    profile = d
                elif kind == "topinfo":
                    topinfo = d
                else:
                    data = d
        elif pages:
            log("WARN %s: site id not found" % slug)
    elif redir.get("status") == 200:
        log("WARN %s: /f/ returned 200 (no redirect); listing data only" % slug)
    elif redir.get("status") in (301, 302, 303, 307, 308):
        pass  # 外部の公式サイトへ転送
    else:
        log("WARN %s: /f/ status %s" % (slug, redir.get("status")))
    if not pages and redir.get("status") in (301, 302, 303, 307, 308) and h.endswith(".tsurisoku.com") \
            and h != "www.tsurisoku.com":
        # 県サブドメインのページ自体が船宿の公式サイトへ転送されている（例: hamachanmaru）→ 転送先を公式サイトとして使う
        sub = h.split(".")[0]
        m = re.match(r"^https?://[^/]+/([^/?#]+)", loc)
        miss = os.path.join(CACHE, "sub_%s_%s_top.html.missing" % (sub, m.group(1) if m else slug))
        if os.path.exists(miss):
            try:
                with open(miss, encoding="utf-8") as fh:
                    final = json.load(fh).get("final") or ""
            except ValueError:
                final = ""
            fh_host = host_of(final)
            if fh_host and not fh_host.endswith("tsurisoku.com"):
                redir = dict(redir)
                redir["location"] = re.sub(r"^(https?://[^/]+/?).*$", r"\1", final)
                log("INFO %s: subdomain page redirects to %s (official site)" % (slug, redir["location"]))
    stats["subpages" if pages else "listing_only"] += 1
    return build_record(listing, redir, pages, profile, data, cache_date(fetched_name), topinfo)


def main():
    ap = argparse.ArgumentParser(description="tsurisoku.com crawler")
    ap.add_argument("--limit", type=int, default=0, help="一覧の先頭N件（船宿のみ数える）")
    ap.add_argument("--ids", default="", help="slug をカンマ区切りで指定（例: kaishu,shachi）")
    ap.add_argument("--out", default=OUT_PATH, help="出力JSON")
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    ap.add_argument("--interval", type=float, default=1.0, help="リクエスト間隔(秒, 下限0.8)")
    ap.add_argument("--log", default=LOG_PATH)
    args = ap.parse_args()

    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    log = Logger(args.log)
    f = Fetcher(log, interval=args.interval, refresh=args.refresh)
    log("START tsurisoku out=%s limit=%s ids=%s refresh=%s" % (out_path, args.limit, args.ids or "-", args.refresh))

    items = crawl_listing(f, log)
    boats = [it for it in items if is_boat(it)]
    excluded = [it for it in items if not is_boat(it)]
    log("listing: %d entries, boats %d, excluded(non-boat) %d: %s" % (
        len(items), len(boats), len(excluded),
        ", ".join("%s[%s]" % (it["slug"], "・".join(it["categories"])) for it in excluded)))
    if args.ids:
        want = [x.strip() for x in args.ids.split(",") if x.strip()]
        by_slug = dict((it["slug"], it) for it in items)
        targets = []
        for s in want:
            if s in by_slug:
                if not is_boat(by_slug[s]):
                    log("WARN %s is non-boat (%s); included because --ids" % (s, "・".join(by_slug[s]["categories"])))
                targets.append(by_slug[s])
            else:
                log("WARN %s not in listing; fetching as stale" % s)
                targets.append({"slug": s, "name": "", "tel": "", "areas": [], "categories": [],
                                "info": "", "tags": [], "stale": True})
    else:
        targets = boats[:args.limit] if args.limit else boats
    if out_path == OUT_PATH:
        save([{"src_id": it["slug"], "name": it["name"], "categories": it["categories"],
               "area": " > ".join(a["label"] for a in it["areas"]), "tel": it["tel"]} for it in excluded],
             EXCLUDED_PATH)

    total = len(targets)
    records = []
    stats = {"subpages": 0, "listing_only": 0, "fail": 0, "parse_err": 0}
    t0 = time.time()
    for n, it in enumerate(targets, 1):
        try:
            rec = crawl_detail(f, log, it, stats)
        except RuntimeError:
            raise
        except Exception as e:  # noqa
            rec = None
            stats["parse_err"] += 1
            log("ERROR parse %s: %r" % (it["slug"], e))
        if rec is not None:
            if it.get("stale"):
                rec["stale"] = True
            if not rec.get("name"):
                log("WARN %s: no name, skipped" % it["slug"])
            else:
                records.append(rec)
        if n % 100 == 0:
            save(records, out_path)
        if n % 10 == 0 or n == total:
            log("detail %d/%d records=%d subpages=%d listing_only=%d fail=%d parse_err=%d net=%d cache=%d elapsed=%ds" % (
                n, total, len(records), stats["subpages"], stats["listing_only"], stats["fail"],
                stats["parse_err"], f.n_net, f.n_cache, time.time() - t0))
    save(records, out_path)
    log("requests: net=%d cache=%d" % (f.n_net, f.n_cache))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
