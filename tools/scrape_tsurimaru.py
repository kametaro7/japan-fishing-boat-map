#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""つり丸 (https://tsurimaru.jp/, 全国釣り船DB) 釣り船クローラ

列挙: 県別一覧 /area/<県コード>/?pg=N（1ページ10隻、見出しの「N 隻の船を表示」で総数）→ 船ID。
船:   /boat/<id>/ … かな・船名、県/市/港（見出しのリンク）、住所（〒付き, 無い船も多い）、TEL
      （tel: リンク）、釣りもの（タグ）、紹介文、Google Maps 埋め込みの q=lat,lon、
      「予約可能なツアー」（オンライン予約対応の船だけ本文に一覧がある）。
      公式サイト/SNS は写真の「引用元 : <a>」リンクから（ポータル・掲載サイトは除外）。
      名前が「テスト…」のダミー船・レンタルボートは出力しない。
ツアー: tour-sitemap*.xml の /tour/<id>/ を全件取得。ページの「<船名>(県市)の船釣りツアーです」の
      リンク /archives/boat/<id>/ で船に対応付ける（船ページにツアー一覧が出ない船が大半のため）。
      料金は「貸切 80000 円(税込) / 隻」「乗合 20000 円(税込) / 人」「料金備考 : …」。
      出船時刻はサイト上 JS カレンダー（外部 web.app）にしか無いので取得しない
      （ツアー名・説明に「6:00出船」等の明記がある場合だけ拾う）。
      一覧に出ていない船に紐づくツアーは、その船ページも取得して stale=true で出力する。
順序: 一覧 → 船ページ全件 → ツアー全件（途中でも船の基本情報が揃う）。
作法: 1ホスト直列・間隔 --interval 秒（既定1.0, 下限0.8）・429/503/5xx は指数バックオフ(最大5回)・
      生HTMLは work/cache/tsurimaru/ に gzip で保存し再実行時はキャッシュを使う（--refresh で再取得）。
      robots.txt は空（Disallow 無し）。予約フォーム・カレンダー(web.app)・admin-ajax は叩かない。
      代表者・船長の個人名は出力しない（紹介文は自己紹介系の文を落として100字以内に要約）。

使い方:
  python3 tools/scrape_tsurimaru.py                       # 全件（一覧→船→ツアー）
  python3 tools/scrape_tsurimaru.py --no-tours            # 船ページまで
  python3 tools/scrape_tsurimaru.py --limit 50            # 一覧の先頭50隻（ツアーはキャッシュ済みのみ）
  python3 tools/scrape_tsurimaru.py --ids 369,10903 --out work/sources/tsurimaru.sample.json
  python3 tools/scrape_tsurimaru.py --per-pref 1 --tour-sample 60 --out work/sources/tsurimaru.sample.json
"""
from __future__ import print_function

import argparse
import datetime
import gzip
import json
import math
import os
import re
import sys
import time
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib import robotparser

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import host_of, host_in, NOT_OFFICIAL_HOSTS, SNS_HOSTS, SHARED_HOSTS  # noqa: E402

BASE = "https://tsurimaru.jp"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "work", "cache", "tsurimaru")
LOG_PATH = os.path.join(ROOT, "work", "logs", "tsurimaru.log")
OUT_PATH = os.path.join(ROOT, "work", "sources", "tsurimaru.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
LIST_PAGE_SIZE = 10

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

PORT_RE = re.compile(r"(港|漁港|マリーナ|フィッシャリーナ|ハーバー|桟橋|岸壁|船着場|船着き場|渡船場|埠頭|ふ頭|泊地|みなと|波止場|乗り場|のりば)$")

# 釣りものタグの分解（「コマセマダイ」→ 釣り方 コマセ + 魚 マダイ）
METHOD_WORDS = sorted([
    "スーパーライトジギング", "スロージギング", "ライトジギング", "ジギング", "SLJ", "タイラバサビキ", "タイラバ",
    "ティップラン", "エギング", "イカメタル", "オモリグ", "バチコン", "ひとつテンヤ", "一つテンヤ", "パワーテンヤ",
    "テンヤ", "インチク", "キャスティング", "トップウォーター", "ルアー", "コマセ", "ビシ", "胴付き", "胴突き",
    "泳がせ", "落とし込み", "ライブベイト", "サビキ", "夜焚き", "電動リール", "電動", "中深海", "深海", "フカセ",
    "カゴ", "ライトゲーム", "一本釣り", "天秤", "手釣り", "夜釣り", "半夜", "ブッコミ", "投げ", "ひとつスッテ",
    "スッテ", "ワインド", "トローリング", "ヤエン", "ウキ", "フライ", "エサ", "餌", "ライト", "カットウ",
    "ボトム", "ジグ", "メタルジグ", "テンビン", "流し釣り", "かかり釣り", "掛かり釣り", "カセ", "筏", "磯",
], key=len, reverse=True)
SKIP_TAG_RE = re.compile(r"(体験|初心者|ファミリー|女性|子供|こども|キッズ|クルーズ|遊覧|観光)")
# 「釣り」を落とした残りの語 → (targets, methods)
TAG_ALIAS = {
    "かかり": ([], ["かかり釣り"]), "カカリ": ([], ["かかり釣り"]), "流し": ([], ["流し釣り"]),
    "ノマセ": ([], ["泳がせ"]), "のませ": ([], ["泳がせ"]), "呑ませ": ([], ["泳がせ"]), "タテ": ([], ["タテ釣り"]),
    "エビング": ([], ["エビング"]), "アジング": (["アジ"], ["アジング"]), "メバリング": (["メバル"], ["メバリング"]),
    "ショアジギ": ([], ["ショアジギング"]), "タコエギ": (["タコ"], ["タコエギ"]), "アコラバ": (["アコウ"], ["タイラバ"]),
    "タイカブラ": (["マダイ"], ["タイカブラ"]), "ボートロック": (["ロックフィッシュ"], []),
    "ナイトロック": (["ロックフィッシュ"], ["夜釣り"]), "ボートシーバス": (["シーバス"], []),
    "ナイトシーバス": (["シーバス"], ["夜釣り"]), "ナイトアジ": (["アジ"], ["夜釣り"]),
    "ナイトマダイ": (["マダイ"], ["夜釣り"]), "ナイト": ([], ["夜釣り"]), "夜イカ": (["イカ"], ["夜釣り"]),
    "LTアジ": (["アジ"], ["ライト"]), "白イカ": (["シロイカ"], []), "ボート": ([], []), "完全": ([], []),
}
NON_TARGET_TAG_RE = re.compile(r"(^[\d,.\s~〜]+$|方面|周辺|コース|プラン|定員|便$|チャーター|貸切|貸し切り|仕立|乗合|乗り合い)")

# 船宿ではない掲載（テスト用ダミー・レンタルボート）
EXCLUDE_NAME_RE = re.compile(r"^(テスト|てすと|test)|レンタルボート|貸しボート|貸ボート", re.I)

# 「引用元」リンクのうち公式サイトとして扱わないホスト（掲載/予約ポータル・地図など）
PORTAL_HOSTS = tuple(NOT_OFFICIAL_HOSTS) + (
    "tsurimaru.jp", "fishing-station.jp", "yugyosen.com", "point-i.jp", "gurenavi.jp", "tsurisoku.com",
    "nikkansports.com", "ishiguro-gr.com", "yugyosen-navi.com", "reserver.co.jp", "tsuttarou.info",
    "tokyowan-yugyosen.or.jp", "activityjapan.com", "itp.ne.jp", "mapion.co.jp", "hotpepper.jp", "minnaga.com",
    "tsuri-info.jp", "turi100.jp", "e-turibune.com", "gyo.ne.jp", "fishbank.jp", "anglers.jp", "tsurinews.jp",
    "fishing.ne.jp", "kaishu-wakayama.com", "1091.co.jp", "a8.net", "g.page", "yahoo.co.jp",
)


def classify_link(url):
    """'official' / 'sns' / None（ポータル・トップだけの共有ホスト等）"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return None
    h = host_of(url)
    if not h or "." not in h:
        return None
    path = re.sub(r"^https?://[^/]+", "", url)
    seg = [x for x in re.split(r"[/?#]", path) if x]
    if host_in(h, SNS_HOSTS):
        if not seg or seg[0].lower() in ("share", "sharer", "sharer.php", "intent", "hashtag", "search", "p",
                                         "reel", "explore", "watch", "embed", "plugins", "dialog"):
            return None
        return "sns"
    if h == "tsuri-navi.jp":  # ポータル本体（<slug>.tsuri-navi.jp は船宿サイト）
        return None
    if h == "zekkouchou.com":  # zekkouchou.com/<slug>/ は各船の「公式サイト」
        return "official" if seg else None
    if host_in(h, PORTAL_HOSTS):
        return None
    if host_in(h, SHARED_HOSTS) and not seg and h.count(".") <= 1:
        return None
    return "official"

FISH_WORDS = sorted(set([
    "マダイ", "タイ", "チダイ", "アジ", "マアジ", "サバ", "イサキ", "カワハギ", "ヒラメ", "カレイ", "アマダイ", "キンメダイ",
    "キンメ", "アカムツ", "ノドグロ", "クロムツ", "ムツ", "タチウオ", "ブリ", "ワラサ", "イナダ", "ハマチ", "メジロ", "サワラ",
    "サゴシ", "カンパチ", "ヒラマサ", "マグロ", "キハダ", "クロマグロ", "カツオ", "シイラ", "スズキ", "シーバス", "メバル",
    "カサゴ", "アコウ", "キジハタ", "オニカサゴ", "ハタ", "クエ", "アラ", "イシダイ", "グレ", "メジナ", "チヌ", "クロダイ",
    "アオリイカ", "ヤリイカ", "スルメイカ", "ケンサキイカ", "マルイカ", "コウイカ", "ムギイカ", "イカ", "タコ", "マダコ",
    "フグ", "ショウサイフグ", "トラフグ", "ハゼ", "キス", "シロギス", "アイナメ", "ソイ", "クロソイ", "ホッケ", "タラ", "マダラ",
    "サケ", "サクラマス", "ロックフィッシュ", "根魚", "青物", "五目", "ベニアコウ", "アブラボウズ", "ユメカサゴ", "イシモチ",
    "ホウボウ", "マハタ", "アカハタ", "オオモンハタ", "ガシラ", "タマン", "ミーバイ", "GT", "アカジン", "カマス", "ワカシ",
    "ショゴ", "メダイ", "アカイサキ", "イトヨリ", "レンコダイ", "ハモ", "アナゴ", "ウナギ", "ワカサギ", "カジキ", "サメ",
    "エソ", "ホタルイカ", "シロイカ", "マゴチ", "コチ", "ヒラスズキ", "イシガキダイ", "クロメジナ", "オオニベ", "ニベ",
]), key=len, reverse=True)


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
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in (s or ""))


def hira_to_kata(s):
    return "".join(chr(ord(c) + 0x60) if 0x3041 <= ord(c) <= 0x3096 else c for c in (s or ""))


def atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def parse_ids(spec):
    ids = []
    for part in re.split(r"[,\s]+", (spec or "").strip()):
        if not part:
            continue
        m = re.match(r"^(\d+)-(\d+)$", part)
        if m:
            ids.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            ids.append(int(part))
    seen, res = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            res.append(i)
    return res


def uniq(xs):
    res = []
    for x in xs:
        if x and x not in res:
            res.append(x)
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
            atomic_write(path, r.content if r.status_code == 200 else b"")
        rp = robotparser.RobotFileParser()
        with open(path, encoding="utf-8", errors="replace") as f:
            rp.parse(f.read().splitlines())
        return rp

    def _wait(self):
        dt = time.time() - self.last
        if dt < self.interval:
            time.sleep(self.interval - dt)

    def cached(self, cache_name):
        return os.path.exists(os.path.join(CACHE, cache_name + ".gz"))

    def cache_date(self, cache_name):
        p = os.path.join(CACHE, cache_name + ".gz")
        if os.path.exists(p):
            return datetime.date.fromtimestamp(os.path.getmtime(p)).isoformat()
        return datetime.date.today().isoformat()

    def get(self, url, cache_name, expect_path=None):
        # type: (str, str, Optional[str]) -> Tuple[Optional[str], str]
        """returns (html or None, how) how in cache|cache-missing|net|missing|httpNNN|fail"""
        if not self.robots.can_fetch("*", url):
            raise RuntimeError("robots.txt disallows %s" % url)
        path = os.path.join(CACHE, cache_name + ".gz")
        miss = os.path.join(CACHE, cache_name + ".missing")
        if not self.refresh:
            if os.path.exists(path):
                self.n_cache += 1
                with gzip.open(path, "rb") as f:
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
            final_path = re.sub(r"^https?://[^/]+", "", r.url.split("#")[0]).split("?")[0]
            if r.status_code in (404, 410) or (expect_path and final_path.rstrip("/") != expect_path.rstrip("/")):
                atomic_write(miss, json.dumps({"url": url, "final": r.url, "status": r.status_code,
                                               "at": now_str()}, ensure_ascii=False).encode("utf-8"))
                return None, "missing"
            if r.status_code != 200:
                return None, "http%d" % r.status_code
            atomic_write(path, gzip.compress(r.content, 6))
            return r.content.decode("utf-8", errors="replace"), "net"
        self.log("ERROR %s gave up: %s" % (url, err))
        return None, "fail"


# ---------------------------------------------------------------- listing

def parse_listing(html):
    """returns (count, [item])"""
    m = re.search(r'<span class="count">\s*(\d+)\s*</span>', html)
    count = int(m.group(1)) if m else None
    i = html.find('id="masonry"')
    j = html.find('class="pagination"', i if i >= 0 else 0)
    frag = html[i:j] if i >= 0 else ""
    items = []
    if frag:
        soup = BeautifulSoup(frag.split(">", 1)[1] if ">" in frag else frag, "lxml")
        for it in soup.select("div.boat.item[data-name]"):
            bid = it.get("data-name", "")
            if not bid.isdigit():
                continue
            h3 = it.select_one(".name-block h3")
            kp = it.select_one(".name-block p")
            loc = it.select_one(".location")
            items.append({
                "id": int(bid),
                "name": one_line(h3.get_text()) if h3 else "",
                "kana": one_line(kp.get_text()) if kp else "",
                "location": one_line(loc.get_text(" ")) if loc else "",
            })
    return count, items


def crawl_listing(fetcher, log, pref_codes=None):
    # type: (Fetcher, Logger, Optional[List[int]]) -> Tuple[List[dict], Dict[int, int]]
    codes = pref_codes or sorted(PREFS)
    items = []  # type: List[dict]
    seen = set()
    counts = {}  # type: Dict[int, int]
    pages_done = 0
    est_total_pages = 0
    for code in codes:
        pg = 1
        npages = 1
        got = 0
        while pg <= npages:
            url = "%s/area/%d/" % (BASE, code) + ("?pg=%d" % pg if pg > 1 else "")
            html, how = fetcher.get(url, "area_%02d_p%03d.html" % (code, pg), expect_path="/area/%d/" % code)
            pages_done += 1
            if html is None:
                log("ERROR listing pref=%d pg=%d failed (%s)" % (code, pg, how))
                break
            cnt, page_items = parse_listing(html)
            if pg == 1:
                counts[code] = cnt or 0
                npages = max(1, int(math.ceil((cnt or 0) / float(LIST_PAGE_SIZE))))
                est_total_pages += npages
            for it in page_items:
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                it["pref_code"] = code
                it["pref"] = PREFS[code]
                it["list_pg"] = pg
                items.append(it)
                got += 1
            if not page_items and (cnt or 0) > 0:
                log("WARN listing pref=%d pg=%d returned no items" % (code, pg))
                break
            pg += 1
        if got < counts.get(code, 0):
            log("WARN listing pref=%d(%s) collected %d < count %d" % (code, PREFS[code], got, counts.get(code, 0)))
        log("list %d/%d prefs pref=%d %s count=%s got=%d (pages so far %d, boats %d)" % (
            codes.index(code) + 1, len(codes), code, PREFS[code], counts.get(code), got, pages_done, len(items)))
    return items, counts


# ---------------------------------------------------------------- text helpers

PHONE_RE = re.compile(r"(0\d{1,4}-\d{1,4}-\d{3,4}|0\d{9,10})")


def norm_tel(s):
    s = nfkc(s)
    s = re.sub(r"[‐‑‒–—―ー−ｰ]", "-", s)
    s = re.sub(r"\(\s*(\d{2,5})\s*\)", r"-\1-", s)
    s = s.replace(" ", "")
    s = re.sub(r"^-+|(?<=[^\d])-+|-+(?=[^\d])", "", s)
    m = PHONE_RE.search(s)
    if not m:
        return ""
    t = m.group(1).strip("-")
    d = re.sub(r"\D", "", t)
    if len(d) not in (10, 11):
        return ""
    if "-" not in t:
        if len(d) == 11 and d[:3] in ("090", "080", "070", "050"):
            t = "%s-%s-%s" % (d[:3], d[3:7], d[7:])
        elif d.startswith("0120") and len(d) == 10:
            t = "%s-%s-%s" % (d[:4], d[4:7], d[7:])
        elif d.startswith("0800") and len(d) == 11:
            t = "%s-%s-%s" % (d[:4], d[4:7], d[7:])
    return t


def norm_addr_chars(a):
    a = nfkc(a)
    a = re.sub(r"(?<=\d)\s*[‐‑‒–—―ー−ｰ－-]\s*(?=\d)", "-", a)
    a = re.sub(r"\s+", " ", a).strip()
    return a


def split_tag(tag):
    """釣りものタグ → (targets, methods)"""
    t = nfkc(one_line(tag))
    if not t:
        return [], []
    if SKIP_TAG_RE.search(t):
        return [], []
    t = re.sub(r"(釣り|釣|つり)$", "", t).strip()
    if not t:
        return [], []
    targets, methods = [], []
    rest = t
    for w in METHOD_WORDS:
        if rest == w:
            methods.append(w)
            rest = ""
            break
        if rest.startswith(w) and len(rest) > len(w):
            methods.append(w)
            rest = rest[len(w):]
            break
        if rest.endswith(w) and len(rest) > len(w):
            methods.append(w)
            rest = rest[:-len(w)]
            break
    rest = re.sub(r"(釣り|釣|つり)$", "", rest).strip(" ・&＆/")
    if rest:
        if rest in TAG_ALIAS:
            targets.extend(TAG_ALIAS[rest][0])
            methods.extend(TAG_ALIAS[rest][1])
        elif rest in METHOD_WORDS:
            methods.append(rest)
        elif NON_TARGET_TAG_RE.search(rest):
            pass
        elif len(rest) <= 12:
            targets.append(rest)
    return targets, methods


def fish_in(text):
    """テキスト中の魚種（辞書）"""
    t = hira_to_kata(nfkc(text))
    found = []
    for w in FISH_WORDS:
        if w in t and not any(w in f for f in found):
            found.append(w)
    return found


INTRO_RE = re.compile(
    r"(船長|代表|オーナー|店主|キャプテン|女将|おかみ|スタッフ|はじめまして|初めまして|自己紹介|と申します|申します|"
    r"私|わたし|わたくし|僕|ぼく|俺|名前|出身|生まれ|[(（][ぁ-んァ-ン ・]+[)）])")


def summarize(text, limit=100):
    """紹介文 → 自己紹介・個人名を含みうる文を落として limit 字以内"""
    t = clean_text(text)
    if not t:
        return ""
    t = re.sub(r"https?://\S+", "", t)
    sents = re.split(r"(?<=[。！!？?\n])", t)
    kept = []
    for s in sents:
        s = one_line(s)
        if not s:
            continue
        if INTRO_RE.search(s):
            continue
        kept.append(s)
    out = ""
    for s in kept:
        if len(out) + len(s) <= limit:
            out += s
        else:
            if not out:
                out = s[:limit - 1] + "…"
            break
    return out


SCHEDULE_RE = re.compile(r"(出船|出港|出航|運航|運休|休船|定休|休業|営業日|営業時間|土日|平日|シーズン|期間)")


def schedule_sentences(text, limit=100):
    t = clean_text(text)
    out = ""
    for s in re.split(r"(?<=[。！!？?\n])", t):
        s = one_line(s)
        if not s or INTRO_RE.search(s) or not SCHEDULE_RE.search(s):
            continue
        if len(out) + len(s) <= limit:
            out += s
        else:
            break
    return out


HM = r"(?<![\d:])(\d{1,2})\s*(?::|時(?!間))\s*(\d{2}|半)?\s*分?\s*時?"  # 「14:00時帰港」も可


def _hm(h, m):
    h = int(h)
    mm = 30 if m == "半" else int(m or 0)
    if h > 24 or mm > 59:
        return None
    return "%02d:%02d" % (h, mm)


def times_from_text(text):
    """ツアー名・説明に明記された出船/帰港/集合時刻だけ拾う"""
    t = nfkc(text)
    dep = ret = meet = None
    m = re.search(HM + r"\s*(?:頃|ごろ)?\s*(?:に)?\s*(?:出船|出港|出航)", t) or \
        re.search(r"(?:出船|出港|出航)\s*(?:時間|時刻|予定)?\s*[:は]?\s*(?:AM|am|午前)?\s*" + HM, t)
    if m:
        dep = _hm(m.group(1), m.group(2))
    m = re.search(HM + r"\s*(?:頃|ごろ)?\s*(?:に)?\s*(?:帰港|沖上がり|沖揚がり|帰着)", t) or \
        re.search(r"(?:帰港|沖上がり|沖揚がり|帰着)\s*(?:時間|時刻|予定)?\s*[:は]?\s*" + HM, t)
    if m:
        ret = _hm(m.group(1), m.group(2))
    m = re.search(HM + r"\s*(?:頃|ごろ)?\s*(?:に)?\s*(?:集合|受付)", t) or \
        re.search(r"(?:集合|受付)\s*(?:時間|時刻)?\s*[:は]?\s*" + HM, t)
    if m:
        meet = _hm(m.group(1), m.group(2))
    if dep is None and ret is None:
        m = re.search(r"(?<!\d)(\d{1,2}):(\d{2})\s*[~〜～\-ー−]\s*(\d{1,2}):(\d{2})(?!\d)", t)
        if m:
            d, r = _hm(m.group(1), m.group(2)), _hm(m.group(3), m.group(4))
            if d and r:
                span = (int(r[:2]) * 60 + int(r[3:])) - (int(d[:2]) * 60 + int(d[3:]))
                if 120 <= span <= 16 * 60:
                    dep, ret = d, r
    return dep, ret, meet


def season_from_text(text):
    t = nfkc(text)
    m = re.search(r"(?<!\d)(\d{1,2})\s*月?\s*[~〜～\-ー−]\s*(\d{1,2})\s*月", t)
    if m and 1 <= int(m.group(1)) <= 12 and 1 <= int(m.group(2)) <= 12:
        return "%s〜%s月" % (int(m.group(1)), int(m.group(2)))
    return ""


def kind_of_label(label):
    s = nfkc(one_line(label))
    if "乗合" in s or "乗り合い" in s:
        return "乗合"
    if "貸切" in s or "仕立" in s or "チャーター" in s or "貸し切り" in s:
        return "仕立"
    return s[:10] or None


def kind_of_name(name):
    s = nfkc(name)
    has_nori = bool(re.search(r"乗合|乗り合い", s))
    has_shi = bool(re.search(r"貸切|貸し切り|仕立|チャーター", s))
    if has_nori and not has_shi:
        return "乗合"
    if has_shi and not has_nori:
        return "仕立"
    return None


def fmt_yen(v):
    return "{:,}".format(v)


def parse_price_block(label, price_txt, unit_txt):
    """('貸切', '48000 ~ 60000', '円(税込) / 隻') → plan fields"""
    kind = kind_of_label(label)
    s = nfkc(price_txt)
    s = re.sub(r"(?<=\d)[.,](?=\d{3}(?!\d))", "", s)  # 「54.000」「54,000」の桁区切り
    nums = [int(x) for x in re.findall(r"\d+", s)]
    nums = [n for n in nums if n > 0]
    unit_s = nfkc(unit_txt)
    unit = "隻" if re.search(r"/\s*(隻|艘|艇)", unit_s) else ("人" if re.search(r"/\s*(人|名)", unit_s) else "")
    tax = "(税込)" if "税込" in unit_s else ("(税別)" if ("税別" in unit_s or "税抜" in unit_s) else "")
    # 「1 ~ 54000」のような下限のダミー値は落とす（下限不明 → 「〜54,000円」、price は入れない）
    floor = 3000 if unit == "隻" else 500
    real = [n for n in nums if n >= floor]
    placeholder = len(real) < len(nums)
    nums = real
    if not nums:
        return {"kind": kind, "price": None, "price_text": ""}
    lo, hi = min(nums), max(nums)
    if placeholder:
        amount = "〜%s円" % fmt_yen(hi)
    else:
        amount = fmt_yen(lo) + "円" if lo == hi else "%s〜%s円" % (fmt_yen(lo), fmt_yen(hi))
    if unit == "隻":
        text = "1隻%s%s" % (amount, tax)
        if kind == "乗合":  # 乗合なのに1隻料金はあり得ないので price は入れない
            return {"kind": kind, "price": None, "price_text": text}
        kind = "仕立" if kind in (None, "仕立") or kind == label else kind
        price = lo if (3000 <= lo <= 2000000 and not placeholder) else None
        return {"kind": kind, "price": price, "price_text": text}
    text = "%s%s/人" % (amount, tax) if unit == "人" else "%s%s%s" % (amount, tax, unit_s.replace("円", "").replace("(税込)", "").strip())
    price = lo if (unit == "人" and 500 <= lo <= 200000 and not placeholder) else None
    if kind == "仕立" and unit == "人":
        # 貸切の1人あたり表記
        text = "貸切 " + text
    return {"kind": kind, "price": price, "price_text": text}


def make_plans(name, blocks, memo, desc, url, boat_targets):
    """1ツアー → plans（料金区分ごとに1件）"""
    name = one_line(name)
    alltxt = name + "\n" + (desc or "")
    dep, ret, meet = times_from_text(alltxt)
    season = season_from_text(name) or season_from_text(desc or "")
    tg = []
    norm_all = hira_to_kata(nfkc(alltxt))
    for t in boat_targets:
        if hira_to_kata(nfkc(t)) in norm_all:
            tg.append(t)
    for f in fish_in(name):
        if f not in tg and not any(f in x for x in tg):
            tg.append(f)
    memo = one_line(memo)
    base = {
        "name": name,
        "kind": None,
        "targets": tg,
        "price": None,
        "price_text": "",
        "depart": dep,
        "return": ret,
        "meet": meet or "",
        "season": season,
        "days": "",
        "includes": "",
        "url": url,
    }
    plans = []
    for label, ptxt, utxt in blocks:
        pb = parse_price_block(label, ptxt, utxt)
        p = dict(base)
        p["kind"] = pb["kind"]
        p["price"] = pb["price"]
        p["price_text"] = pb["price_text"]
        if memo and memo not in p["price_text"]:
            p["price_text"] = (p["price_text"] + "（" + memo + "）") if p["price_text"] else memo
        p["price_text"] = p["price_text"][:80]
        if p not in plans:
            plans.append(p)
    if not plans:
        if not name and not memo:
            return []
        p = dict(base)
        p["kind"] = kind_of_name(name)
        p["price_text"] = memo[:80]
        if not name:
            return []
        plans.append(p)
    return plans


def price_blocks(container):
    out = []
    for pw in container.select(".price-wrap"):
        lab = pw.select_one(".label")
        pr = pw.select_one(".price")
        det = pw.select_one(".detail")
        unit = ""
        if det is not None:
            unit = one_line(det.get_text(" "))
            if pr is not None:
                unit = unit.replace(one_line(pr.get_text(" ")), "", 1).strip()
        out.append((one_line(lab.get_text(" ")) if lab else "",
                    one_line(pr.get_text(" ")) if pr else "",
                    unit))
    return out


def memo_text(container):
    pm = container.select_one(".price-memo")
    if pm is None:
        return ""
    st = pm.find("strong")
    t = one_line(pm.get_text(" "))
    if st is not None:
        t = t.replace(one_line(st.get_text(" ")), "", 1).strip()
    return t.strip(" :：")


# ---------------------------------------------------------------- boat page

def _slice(html, start_marks, end_marks):
    i = -1
    for m in start_marks:
        i = html.find(m)
        if i >= 0:
            break
    if i < 0:
        return None
    j = -1
    for m in end_marks:
        j = html.find(m, i)
        if j >= 0:
            break
    return html[i:j] if j > i else html[i:]


LATLON_RE = re.compile(r"[?&]q=(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)")


def parse_boat(html, bid, listing=None, fetched=None):
    # type: (str, int, Optional[dict], Optional[str]) -> Optional[dict]
    listing = listing or {}
    frag = _slice(html, ['<div id="author-info"'], ['<section id="bf-breadcrumbs"', '<footer'])
    if frag is None:
        return None
    soup = BeautifulSoup(frag, "lxml")
    for x in soup(["script", "style", "svg", "noscript"]):
        x.decompose()
    meta = soup.select_one(".boat-meta")
    if meta is None:
        return None
    h1 = meta.select_one("h1")
    name = one_line(h1.get_text()) if h1 else listing.get("name", "")
    if not name:
        return None
    fk = meta.select_one(".furigana")
    kana = kata_to_hira(one_line(fk.get_text())) if fk else kata_to_hira(listing.get("kana", ""))

    pref = city = port = ""
    frags = []
    pref_code = None
    for a in meta.select("p a"):
        cls = a.get("class") or []
        href = a.get("href", "")
        txt = one_line(a.get_text())
        if "pref" in cls:
            m = re.search(r"/area/(\d+)/?$", href)
            if m and int(m.group(1)) in PREFS:
                pref_code = int(m.group(1))
                pref = PREFS[pref_code]
            elif txt in PREF_NAMES:
                pref = txt
        elif "city" in cls:
            city = txt
        elif "anchor" in cls and txt:
            depth3 = re.search(r"/area/\d+/[^/]+/[^/]+/?$", href)
            if depth3 and not port:
                port = txt
            elif not port and PORT_RE.search(txt) and not re.search(r"\d", txt):
                port = txt
            else:
                frags.append(txt)
    if not pref:
        pref = listing.get("pref", "")

    # 住所・TEL（地図ブロック）
    address = ""
    tel = ""
    for p in soup.select("#boat-map .address p"):
        t = one_line(p.get_text(" "))
        if t.startswith("住所"):
            a = re.sub(r"^住所\s*[:：]\s*", "", t)
            a = re.sub(r"〒\s*\d{3}\s*[-－ー−]?\s*\d{4}", "", nfkc(a))
            a = a.replace("〒", "").strip()
            address = a
        elif t.startswith("TEL"):
            tel = norm_tel(re.sub(r"^TEL\s*[:：]\s*", "", t))
    if not tel:
        for a in soup.select('a[href^="tel:"]'):
            tel = norm_tel(a.get("href", "")[4:]) or norm_tel(a.get_text())
            if tel:
                break

    address = norm_addr_chars(address)
    if address and not re.search(r"[一-龥ぁ-んァ-ン々]", address):
        address = ""
    addr_from = "map"
    if not address and frags:
        f0 = norm_addr_chars(frags[0])
        if re.search(r"[一-龥ぁ-んァ-ン々]", f0) and f0 != port:
            address = f0
            addr_from = "header"
    if address:
        if not any(address.startswith(p) for p in PREF_NAMES):
            if city and not address.startswith(city):
                address = city + address
            address = (pref or "") + address
        address = re.sub(r"^(%s)\s+" % "|".join(PREF_NAMES), r"\1", address)
    if address and addr_from == "header" and address in ((pref or "") + (city or ""), pref):
        address = ""
    if port and " " in port:
        # 「塩屋町ホ 塩屋漁港」→ 末尾の港名だけ
        last = port.split()[-1]
        if PORT_RE.search(last) and not re.search(r"\d", last):
            port = last
    if address and not port:
        # 「岩手県宮古市藤原3丁目 宮古港」→ 住所と港に分ける
        mp = re.search(r"\s+([^\s\d]+)$", address)
        if mp and PORT_RE.search(mp.group(1)):
            port = mp.group(1)
            address = address[:mp.start()].strip()

    # 公式サイト/SNS: 写真の「引用元」リンク（ポータル等は除外）
    website = None
    sns = []
    for a in soup.select(".via a[href]"):
        u = re.sub(r"#.*$", "", (a.get("href") or "").strip())
        c = classify_link(u)
        if c == "sns":
            u = u.split("?")[0]
            if u not in sns:
                sns.append(u)
        elif c == "official" and not website:
            website = u

    lat = lon = None
    bm = soup.select_one("#boat-map")
    src = ""
    if bm is not None:
        ifr = bm.find("iframe")
        if ifr is not None:
            src = ifr.get("data-src") or ifr.get("src") or ""
    m = LATLON_RE.search(src or "")
    if m:
        la, lo = float(m.group(1)), float(m.group(2))
        if 20.0 <= la <= 46.6 and 122.0 <= lo <= 154.0:
            lat, lon = round(la, 7), round(lo, 7)

    tags = [one_line(s.get_text()) for s in soup.select("#boat-tag .items span")]
    targets, methods = [], []
    for tg in tags:
        t1, m1 = split_tag(tg)
        targets.extend(t1)
        methods.extend(m1)
    targets = uniq(targets)
    methods = uniq(methods)

    content = soup.select_one("#boat-tag .content")
    intro = clean_text(content.get_text("\n")) if content is not None else ""
    description = summarize(intro, 100)
    schedule_text = schedule_sentences(intro, 100)
    holidays = ""
    mh = re.search(r"定休日?\s*[:：は]?\s*([^\n。、,]{1,20})", nfkc(intro))
    if mh and not INTRO_RE.search(mh.group(0)):
        holidays = one_line(mh.group(1))

    src_url = "%s/boat/%d/" % (BASE, bid)
    inline_plans = []
    for it in soup.select("#boat-tour .items > .item"):
        h3 = it.select_one("h3")
        tname = one_line(h3.get_text(" ")) if h3 else ""
        tid = None
        for a in it.find_all(["a", "button"]):
            for attr in ("href", "data-url", "value"):
                v = a.get(attr) or ""
                mt = re.search(r"tourId=(\d+)|/tour/(\d+)/", v)
                if mt:
                    tid = int(mt.group(1) or mt.group(2))
                    break
            if tid:
                break
        dw = it.select_one(".description-wrap p")
        desc = one_line(dw.get_text(" ")) if dw else ""
        url = "%s/tour/%d/" % (BASE, tid) if tid else src_url
        for p in make_plans(tname, price_blocks(it), memo_text(it), desc, url, targets):
            p["_tour_id"] = tid
            inline_plans.append(p)

    return {
        "src": "tsurimaru",
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
        "types": [],
        "targets": targets,
        "methods": methods,
        "holidays": holidays,
        "facilities": [],
        "capacity": None,
        "access": "",
        "description": description,
        "plans": inline_plans,
        "schedule_text": schedule_text,
        "fetched": fetched or datetime.date.today().isoformat(),
    }


# ---------------------------------------------------------------- tour page

def parse_tour(html, tid):
    """returns dict(boat_id, name, blocks, memo, desc) or None"""
    frag = _slice(html, ['<div id="tour" class="single"'], ['<div class="modal-badge-wrap"', '<section id="bf-breadcrumbs"', '<footer'])
    if frag is None:
        return None
    soup = BeautifulSoup(frag, "lxml")
    for x in soup(["script", "style", "svg", "noscript"]):
        x.decompose()
    box = soup.select_one("#tour")
    if box is None:
        return None
    bid = None
    bn = box.select_one(".boat-name a[href]")
    if bn is not None:
        m = re.search(r"/boat/(\d+)/", bn.get("href", ""))
        if m:
            bid = int(m.group(1))
    h1 = box.find("h1")
    name = one_line(h1.get_text(" ")) if h1 else ""
    dw = box.select_one(".description-wrap")
    desc = clean_text(dw.get_text("\n")) if dw is not None else ""
    return {
        "tour_id": tid,
        "boat_id": bid,
        "boat_name": one_line(bn.get_text()) if bn is not None else "",
        "name": name,
        "blocks": price_blocks(box),
        "memo": memo_text(box),
        "desc": desc,
    }


def load_tour_sitemaps(fetcher, log):
    # type: (Fetcher, Logger) -> List[int]
    html, how = fetcher.get(BASE + "/sitemap.xml", "sitemap.xml")
    if html is None:
        log("ERROR sitemap index failed (%s)" % how)
        return []
    maps = re.findall(r"(https://tsurimaru\.jp/tour-sitemap\d*\.xml)", html)
    ids = []
    seen = set()
    for u in uniq(maps):
        name = u.rsplit("/", 1)[1]
        x, how = fetcher.get(u, name)
        if x is None:
            log("ERROR %s failed (%s)" % (u, how))
            continue
        n = 0
        for m in re.finditer(r"https://tsurimaru\.jp/tour/(\d+)/", x):
            t = int(m.group(1))
            if t not in seen:
                seen.add(t)
                ids.append(t)
                n += 1
        log("sitemap %s: %d tours (%s)" % (name, n, how))
    return ids


# ---------------------------------------------------------------- assemble

def assemble(order, boats, tours_by_boat, stale_ids):
    """boats: id → parsed record (inline plans), tours_by_boat: id → [tour plan list]"""
    out = []
    for bid in order:
        b = boats.get(bid)
        if b is None:
            continue
        rec = dict(b)
        tour_plans = []
        tour_ids = set()
        names = set()
        for tp in tours_by_boat.get(bid, []):
            tour_ids.add(tp["_tour_id"])
            names.add(tp["name"])
            tour_plans.append(tp)
        plans = list(tour_plans)
        for p in b["plans"]:
            if p.get("_tour_id") and p["_tour_id"] in tour_ids:
                continue
            if not p.get("_tour_id") and p["name"] in names:
                continue
            plans.append(p)
        clean = []
        for p in plans:
            q = dict((k, v) for k, v in p.items() if not k.startswith("_"))
            if q not in clean:
                clean.append(q)
        rec["plans"] = clean
        rec["types"] = uniq([p["kind"] for p in clean if p.get("kind") in ("乗合", "仕立")])
        if bid in stale_ids:
            rec["stale"] = True
        out.append(rec)
    return out


def save(records, out_path):
    d = os.path.dirname(out_path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    atomic_write(out_path, json.dumps(records, ensure_ascii=False, indent=1).encode("utf-8"))


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="tsurimaru.jp crawler")
    ap.add_argument("--limit", type=int, default=0, help="一覧の先頭N隻だけ（ツアーはキャッシュ済みのみ使う）")
    ap.add_argument("--ids", default="", help="指定した船IDのみ (例: 369,10903,200-210)")
    ap.add_argument("--per-pref", type=int, default=0, help="サンプル用: 各県の一覧の先頭N隻")
    ap.add_argument("--tour-ids", default="", help="サンプル用: 指定ツアーIDを取得（その船も取得）")
    ap.add_argument("--tour-sample", type=int, default=0, help="サンプル用: サイトマップから等間隔にNツアー")
    ap.add_argument("--no-tours", action="store_true", help="ツアーページを取得しない")
    ap.add_argument("--out", default=OUT_PATH, help="出力JSON")
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    ap.add_argument("--interval", type=float, default=1.0, help="リクエスト間隔(秒, 下限0.8)")
    ap.add_argument("--log", default=LOG_PATH)
    args = ap.parse_args()

    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    log = Logger(args.log)
    f = Fetcher(log, interval=args.interval, refresh=args.refresh)
    sample_mode = bool(args.ids or args.per_pref or args.tour_ids or args.tour_sample or args.limit)
    log("START tsurimaru out=%s limit=%s ids=%s per_pref=%s tour_ids=%s tour_sample=%s no_tours=%s refresh=%s" % (
        out_path, args.limit, args.ids or "-", args.per_pref, args.tour_ids or "-", args.tour_sample,
        args.no_tours, args.refresh))
    t0 = time.time()

    # 1) listing（--ids だけのときは一覧のキャッシュがあれば使う）
    listing_by_id = {}  # type: Dict[int, dict]
    listed_order = []  # type: List[int]
    items, counts = crawl_listing(f, log)
    for it in items:
        listing_by_id[it["id"]] = it
        listed_order.append(it["id"])
    log("listing: %d boats (sum of counts %d)" % (len(listed_order), sum(counts.values())))
    listing_complete = len(listed_order) >= sum(counts.values()) * 0.98 and len(listed_order) > 0

    if args.ids or args.per_pref or args.tour_ids or args.tour_sample:
        boat_ids = parse_ids(args.ids)
        if args.per_pref:
            per = {}
            for bid in listed_order:
                c = listing_by_id[bid]["pref_code"]
                if per.get(c, 0) < args.per_pref:
                    per[c] = per.get(c, 0) + 1
                    boat_ids.append(bid)
        boat_ids = uniq(boat_ids)
    elif args.limit:
        boat_ids = listed_order[:args.limit]
    else:
        boat_ids = list(listed_order)

    boats = {}  # type: Dict[int, dict]
    order = []  # type: List[int]
    stale_ids = set()
    stats = {"net": 0, "cache": 0, "missing": 0, "fail": 0, "parse_err": 0, "excluded": 0}
    excluded = set()

    def fetch_boat(bid):
        url = "%s/boat/%d/" % (BASE, bid)
        name = "boat_%d.html" % bid
        html, how = f.get(url, name, expect_path="/boat/%d/" % bid)
        if html is None:
            if how in ("missing", "cache-missing"):
                stats["missing"] += 1
            else:
                stats["fail"] += 1
                log("ERROR boat id=%d %s" % (bid, how))
            return None
        stats["net" if how == "net" else "cache"] += 1
        try:
            rec = parse_boat(html, bid, listing_by_id.get(bid), f.cache_date(name))
        except Exception as e:  # noqa
            log("ERROR parse boat id=%d: %r" % (bid, e))
            rec = None
        if rec is None:
            stats["parse_err"] += 1
        elif EXCLUDE_NAME_RE.search(rec["name"]) or EXCLUDE_NAME_RE.search(rec.get("kana") or ""):
            if bid not in excluded:
                excluded.add(bid)
                stats["excluded"] += 1
                log("SKIP boat id=%d name=%s (テスト/レンタルボート)" % (bid, rec["name"]))
            return None
        return rec

    # 2) boats
    total = len(boat_ids)
    tb = time.time()
    for n, bid in enumerate(boat_ids, 1):
        rec = fetch_boat(bid)
        if rec is not None:
            boats[bid] = rec
            order.append(bid)
            if bid not in listing_by_id and listing_complete:
                stale_ids.add(bid)
        if n % 100 == 0:
            save(assemble(order, boats, {}, stale_ids), out_path)
        if n % 20 == 0 or n == total:
            el = time.time() - tb
            eta = el / n * (total - n) if n else 0
            log("boat %d/%d records=%d net=%d cache=%d missing=%d fail=%d parse_err=%d elapsed=%ds eta=%ds" % (
                n, total, len(boats), stats["net"], stats["cache"], stats["missing"], stats["fail"],
                stats["parse_err"], el, eta))
    save(assemble(order, boats, {}, stale_ids), out_path)

    # 3) tours
    tours_by_boat = {}  # type: Dict[int, List[dict]]
    tstats = {"net": 0, "cache": 0, "missing": 0, "fail": 0, "parse_err": 0, "no_boat": 0,
              "boat_missing": 0, "attached": 0, "stale_boats": 0, "skipped_uncached": 0}
    if not args.no_tours:
        all_tours = load_tour_sitemaps(f, log)
        log("tour sitemap: %d tours" % len(all_tours))
        if args.tour_ids or args.tour_sample:
            tour_ids = parse_ids(args.tour_ids)
            if args.tour_sample and all_tours:
                step = max(1, len(all_tours) // args.tour_sample)
                tour_ids += all_tours[::step][:args.tour_sample]
            tour_ids = uniq(tour_ids)
            allow_net = True
            allow_new_boats = True
        elif sample_mode:
            tour_ids = all_tours
            allow_net = False  # --limit/--ids/--per-pref: キャッシュ済みツアーだけ使う
            allow_new_boats = False
        else:
            tour_ids = all_tours
            allow_net = True
            allow_new_boats = True
        wanted = set(boat_ids)
        ttotal = len(tour_ids)
        tt = time.time()
        missing_boats = set()
        for n, tid in enumerate(tour_ids, 1):
            name = "tour_%d.html" % tid
            if not allow_net and not f.cached(name):
                tstats["skipped_uncached"] += 1
                html = None
                how = "skip"
            else:
                html, how = f.get("%s/tour/%d/" % (BASE, tid), name, expect_path="/tour/%d/" % tid)
            if html is None:
                if how in ("missing", "cache-missing"):
                    tstats["missing"] += 1
                elif how != "skip":
                    tstats["fail"] += 1
                    log("ERROR tour id=%d %s" % (tid, how))
            else:
                tstats["net" if how == "net" else "cache"] += 1
                try:
                    tr = parse_tour(html, tid)
                except Exception as e:  # noqa
                    log("ERROR parse tour id=%d: %r" % (tid, e))
                    tr = None
                if tr is None:
                    tstats["parse_err"] += 1
                elif tr["boat_id"] is None:
                    tstats["no_boat"] += 1
                else:
                    bid = tr["boat_id"]
                    if bid not in boats and bid not in missing_boats:
                        if allow_new_boats or (sample_mode and not allow_net and bid in wanted):
                            rec = fetch_boat(bid)
                            if rec is None:
                                missing_boats.add(bid)
                            else:
                                boats[bid] = rec
                                order.append(bid)
                                if bid not in listing_by_id and listing_complete:
                                    stale_ids.add(bid)
                                    tstats["stale_boats"] += 1
                    if bid in boats:
                        url = "%s/tour/%d/" % (BASE, tid)
                        for p in make_plans(tr["name"], tr["blocks"], tr["memo"], tr["desc"], url,
                                            boats[bid]["targets"]):
                            p["_tour_id"] = tid
                            tours_by_boat.setdefault(bid, []).append(p)
                        tstats["attached"] += 1
                    else:
                        tstats["boat_missing"] += 1
            if n % 100 == 0 and (tstats["net"] or n % 1000 == 0):
                save(assemble(order, boats, tours_by_boat, stale_ids), out_path)
            if n % 100 == 0 or n == ttotal:
                el = time.time() - tt
                eta = el / n * (ttotal - n) if n else 0
                log("tour %d/%d attached=%d net=%d cache=%d missing=%d fail=%d no_boat=%d boat_missing=%d "
                    "stale_boats=%d skipped_uncached=%d elapsed=%ds eta=%ds" % (
                        n, ttotal, tstats["attached"], tstats["net"], tstats["cache"], tstats["missing"],
                        tstats["fail"], tstats["no_boat"], tstats["boat_missing"], tstats["stale_boats"],
                        tstats["skipped_uncached"], el, eta))

    records = assemble(order, boats, tours_by_boat, stale_ids)
    save(records, out_path)
    n_plans = sum(len(r["plans"]) for r in records)
    log("summary: boats=%d stale=%d plans=%d boat_stats=%s tour_stats=%s" % (
        len(records), sum(1 for r in records if r.get("stale")), n_plans,
        json.dumps(stats), json.dumps(tstats)))
    log("requests: net=%d cache=%d elapsed=%ds" % (f.n_net, f.n_cache, time.time() - t0))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
