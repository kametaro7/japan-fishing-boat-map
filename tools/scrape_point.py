#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
釣具のポイント「釣り船・釣宿情報」(https://www.point-i.jp/boats) クローラ

列挙: 一覧 /boats, /boats?page=N（1ページ15件・24ページ前後）
      ＋ 県フィルタ /boats?prefecture_id=NN(&page=N) で ID→都道府県 の対応を得る
詳細: /boats/<id>（WordPress 記事。h3見出し＋p の新形式 / 【ラベル】表の2018年形式 が混在）
      Google マップ埋め込み (embed?pb=...!2d<lon>!3d<lat>) を座標として採用
作法: 1ホスト直列・間隔 >= 1.0 秒・429/5xx は指数バックオフ(最大5回)・
      生HTMLは work/cache/point/<sha1(url)>.html（再実行時はキャッシュ、--refresh で再取得）
出力: work/sources/point.json（SPEC「掲載サイトレコード」の配列、src="point"）
ログ: work/logs/point.log（progress done/total、最終行 DONE <n> records）

使い方:
  python3 tools/scrape_point.py                       # 全件
  python3 tools/scrape_point.py --limit 30
  python3 tools/scrape_point.py --ids 3249965,1984 --out work/sources/point.sample.json
  python3 tools/scrape_point.py --per-pref 2 --out work/sources/point.sample.json
  python3 tools/scrape_point.py --refresh            # キャッシュを使わず再取得
Python 3.9 互換。
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
import warnings

warnings.filterwarnings("ignore")

import requests  # noqa: E402
from bs4 import BeautifulSoup, NavigableString, Comment  # noqa: E402

try:
    from urllib import robotparser
except ImportError:  # pragma: no cover
    import robotparser  # type: ignore

BASE = "https://www.point-i.jp"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "point")
DEFAULT_OUT = os.path.join(ROOT, "work", "sources", "point.json")
DEFAULT_LOG = os.path.join(ROOT, "work", "logs", "point.log")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒 (SPEC は 0.8 以上)
FETCHED = "2026-09-15"

PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
         "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
         "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
         "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
         "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]
SHORT_PREF = {}
for _p in PREFS:
    SHORT_PREF[_p] = _p
    if _p != "北海道":
        SHORT_PREF[_p[:-1]] = _p
# タイトル先頭の地域名（県名でないもの）
REGION_PREF = {"北九州": "福岡県", "博多": "福岡県", "玄界灘": None, "関門海峡": None, "山陰": None}

CITY_EXCEPTIONS = ["四日市市", "廿日市市", "市川市", "市原市", "野々市市", "町田市", "大町市", "十日町市",
                   "村上市", "村山市", "東村山市", "武蔵村山市", "羽村市", "大村市", "田村市", "上市町", "余市町",
                   "玉村町", "市貝町"]

SNS_HOSTS = ("instagram.com", "facebook.com", "fb.com", "fb.me", "twitter.com", "x.com", "youtube.com",
             "youtu.be", "tiktok.com", "line.me", "lin.ee", "threads.net", "note.com")
# 公式サイトではないホスト（掲載サイト・予約サイト・地図など）
NOT_OFFICIAL_HOSTS = ("point-i.jp", "chowari.jp", "tsuree.jp", "theboat.jp", "castingnet.jp", "funaduri.jp",
                      "tsurisoku.com", "fishing-station.jp", "gurenavi.jp", "reserver.co.jp", "1091.co.jp",
                      "fishing-v.jp", "kanpari.jp", "google.com", "google.co.jp", "goo.gl", "maps.app.goo.gl",
                      "yahoo.co.jp", "asoview.com", "jalan.net", "rakuten.co.jp", "airtrip.jp", "veltra.com",
                      "tabelog.com", "amazonaws.com", "anglers.jp", "fishing-labo.net", "tsurinews.jp")
# 掲載サイト自身の SNS アカウント
OWN_SNS = ("tsuri.point", "point_twinfo", "point_tsuri", "pointtsuri", "point.tsuri", "tsuripoint")

FISH = ["マダイ", "真鯛", "チダイ", "レンコダイ", "レンコ", "クロダイ", "チヌ", "イシダイ", "石鯛", "イシガキダイ",
        "キダイ", "アマダイ", "キンメダイ", "メダイ", "ヒラマサ", "ブリ", "ハマチ", "メジロ", "ワラサ", "イナダ",
        "ヤズ", "カンパチ", "ネリゴ", "シマアジ", "青物", "サワラ", "サゴシ", "タチウオ", "太刀魚", "アジ", "鯵",
        "サバ", "イワシ", "シロギス", "キス", "カワハギ", "ウマヅラ", "カサゴ", "ガシラ", "アラカブ", "メバル",
        "ソイ", "アコウ", "キジハタ", "オオモンハタ", "アカハタ", "マハタ", "ハタ", "クエ", "アラ", "根魚", "ヒラメ",
        "カレイ", "マゴチ", "コチ", "スズキ", "シーバス", "イサキ", "アオリイカ", "ケンサキイカ", "スルメイカ",
        "ヤリイカ", "コウイカ", "モンゴウイカ", "イカ", "マダコ", "タコ", "マグロ", "キハダ", "カツオ", "シイラ",
        "トラフグ", "フグ", "アカムツ", "ノドグロ", "クロムツ", "ムツ", "グレ", "クロ", "メジナ", "尾長", "イトヨリ",
        "ホウボウ", "ヒラソ", "ハモ", "アナゴ", "カマス", "タイ", "鯛", "五目", "底物", "底もの", "ロックフィッシュ",
        "サメ", "ベラ", "オニカサゴ", "ウッカリカサゴ", "マトウダイ", "ハガツオ", "スマ", "ヨコワ", "ブリ",
        # 追加（地方名・漢字表記）
        "マアジ", "マルアジ", "真アジ", "ヒラスズキ", "コシナガ", "キンメ", "金目鯛", "ホゴ", "イイダコ", "飯蛸", "黒鯛",
        "石鯛", "真鯛", "甘鯛", "赤ムツ", "黒ムツ", "アカイカ", "シロイカ", "ミズイカ", "水イカ", "真イカ", "甲イカ",
        "剣先イカ", "ヤリイカ", "マイカ", "ツバス", "サゴシ", "アイナメ", "アブラメ", "ハゼ", "マハゼ", "メッキ", "GT",
        "ロウニンアジ", "カスミアジ", "タマン", "ミーバイ", "アカジン", "スジアラ", "バラハタ", "オナガ", "口太",
        "ヒラアジ", "カマス", "サヨリ", "ボラ", "コノシロ", "ハタハタ", "マダラ", "タラ", "メヌケ", "アカモク",
        "ホッケ", "アイゴ", "バリ", "イスズミ", "コロダイ", "フエフキダイ", "ヒメダイ", "オオクチイシナギ", "イシナギ",
        "カンダイ", "コブダイ", "ハマフエフキ", "ヒラメ", "鮃", "鰤", "鰆", "平政", "鯵", "鯖", "鱚", "鱸", "鮪", "鰹",
        "烏賊", "蛸", "太刀", "鯛", "タチ", "アオリ", "ケンサキ", "スルメ", "ヒラス", "ネイゴ", "ヤイト", "オオニベ",
        "ニベ", "シログチ", "グチ", "イトヨリダイ", "レンコ鯛", "連子鯛", "マハタ", "クロソイ", "ムラソイ", "タケノコメバル",
        "ウスメバル", "ハチビキ", "アカバ", "アオハタ", "ホウキハタ", "オオモンハタ", "ヤガラ", "サクラマス", "ワカシ",
        "タカバ", "ギザミ", "チカメキントキ", "キントキ", "アブラボウズ", "ウマズラハギ", "バショウカジキ", "カジキ",
        "アオナ", "関さば", "関サバ", "関あじ", "関アジ"]
FISH_SORTED = sorted(set(FISH), key=len, reverse=True)
METHOD_WORDS = ["タイラバ", "鯛ラバ", "ジギング", "スロージギング", "ライトジギング", "SLJ", "TSLJ", "イカメタル",
                "オモリグ", "エギング", "ティップラン", "一つテンヤ", "ひとつテンヤ", "テンヤ", "泳がせ", "のませ",
                "呑ませ", "サビキ", "キャスティング", "フカセ", "フカセ釣り", "カゴ釣り", "カゴ", "ルアー", "胴突き",
                "胴付き", "胴付", "落とし込み", "天秤", "テンビン", "ビシ", "コマセ", "トローリング", "手釣り", "ぶっこみ",
                "ブッコミ", "ウキ釣り", "インチク", "バチコン", "メタルスッテ", "スッテ", "カットウ", "エサ釣り", "餌釣り",
                "船タコ", "タコエギ", "ひとつテンヤ", "穴釣り", "紀州釣り", "かご釣り", "ジグ", "テンヤ釣り",
                "落し込み", "かかり釣り", "胴突", "アコラバ", "チニング", "ボートチニング", "ボートロック", "フラットゲーム"]
METHOD_SUFFIX = sorted(set(METHOD_WORDS), key=len, reverse=True)

SEASON_WORDS = ("春", "夏", "秋", "冬", "通年", "年中", "周年", "春夏", "秋冬", "春～夏", "秋～冬", "オールシーズン")


# ----------------------------------------------------------------------------
# ログ
# ----------------------------------------------------------------------------
class Logger(object):
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)

    def __call__(self, msg):
        line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line)
        sys.stdout.flush()


# ----------------------------------------------------------------------------
# 取得 (キャッシュ・直列・バックオフ・robots)
# ----------------------------------------------------------------------------
class Fetcher(object):
    def __init__(self, log, refresh=False, interval=MIN_INTERVAL):
        self.log = log
        self.refresh = refresh
        self.interval = max(0.8, interval)
        self.sess = None
        self.new_session()
        self.last = 0.0
        self.n_net = 0
        self.n_cache = 0
        self.refreshed = set()
        self.robots = None
        if not os.path.isdir(CACHE_DIR):
            os.makedirs(CACHE_DIR)

    def new_session(self):
        if self.sess is not None:
            try:
                self.sess.close()
            except Exception:  # noqa
                pass
        self.sess = requests.Session()
        self.sess.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        })

    def load_robots(self):
        txt = self.get(BASE + "/robots.txt", check_robots=False) or ""
        rp = robotparser.RobotFileParser()
        rp.parse(txt.splitlines())
        self.robots = rp

    def allowed(self, url):
        if self.robots is None:
            return True
        try:
            return self.robots.can_fetch(UA, url)
        except Exception:  # noqa
            return True

    @staticmethod
    def cache_path(url):
        return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")

    def get(self, url, check_robots=True):
        """本文(str)を返す。404/410 なら None。取得失敗は例外。"""
        p = self.cache_path(url)
        miss = p + ".404"
        use_cache = (not self.refresh) or (url in self.refreshed)
        if use_cache:
            if os.path.exists(p):
                self.n_cache += 1
                with open(p, "rb") as f:
                    return f.read().decode("utf-8", "replace")
            if os.path.exists(miss):
                self.n_cache += 1
                return None
        if check_robots and not self.allowed(url):
            self.log("ROBOTS disallow %s" % url)
            return None
        delay = 5.0
        for attempt in range(6):
            wait = self.interval - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.sess.get(url, timeout=60)
            except requests.RequestException as e:
                self.last = time.time()
                if attempt >= 5:
                    raise
                self.log("WARN %s %s: %s (retry in %.0fs)" % (url, e.__class__.__name__, str(e)[:160], delay))
                self.new_session()
                time.sleep(delay)
                delay *= 2
                continue
            self.last = time.time()
            self.n_net += 1
            if r.status_code == 200:
                tmp = p + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(r.content)
                os.replace(tmp, p)
                self.refreshed.add(url)
                return r.content.decode("utf-8", "replace")
            if r.status_code in (404, 410):
                open(miss, "w").close()
                self.refreshed.add(url)
                return None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                ra = r.headers.get("Retry-After")
                w = delay
                if ra and ra.isdigit():
                    w = max(w, float(ra))
                self.log("WARN %s HTTP %d (backoff %.0fs)" % (url, r.status_code, w))
                time.sleep(w)
                delay *= 2
                continue
            raise RuntimeError("HTTP %d %s" % (r.status_code, url))
        raise RuntimeError("giving up %s" % url)


# ----------------------------------------------------------------------------
# 文字列ユーティリティ
# ----------------------------------------------------------------------------
def nfkc(s):
    # ゼロ幅文字（「​市来港」）も除く
    return re.sub(r"[​-‏⁠﻿]", "", unicodedata.normalize("NFKC", s or ""))


def clean(s):
    if s is None:
        return ""
    s = s.replace("\xa0", " ").replace("　", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def one_line(s):
    return re.sub(r"\s+", " ", clean(s)).strip()


def uniq(seq):
    out = []
    seen = set()
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def kata2hira(s):
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def host_of(url):
    m = re.match(r"^https?://([^/:?#]+)", (url or "").strip(), re.I)
    if not m:
        return ""
    h = m.group(1).lower()
    return h[4:] if h.startswith("www.") else h


def host_in(h, hosts):
    return any(h == x or h.endswith("." + x) for x in hosts)


def is_sns(url):
    return host_in(host_of(url), SNS_HOSTS)


def is_official_candidate(url):
    h = host_of(url)
    return bool(h) and "." in h and not host_in(h, NOT_OFFICIAL_HOSTS) and not host_in(h, SNS_HOSTS)


def is_own_sns(url):
    u = (url or "").lower()
    return any(("/" + o) in u for o in OWN_SNS)


PHONE_RE = re.compile(r"(?<![\d])(0\d{1,4}-?\d{1,4}-?\d{3,4})(?![\d])")


def norm_tel(s):
    s = nfkc(s)
    s = re.sub(r"[‐‑‒–—―ー−]", "-", s)
    s = re.sub(r"\(\s*(\d{2,5})\s*\)", r"-\1-", s)
    s = re.sub(r"(?<=\d)\s+(?=\d)", "-", s)
    for m in PHONE_RE.finditer(s):
        t = m.group(1).strip("-")
        d = re.sub(r"\D", "", t)
        if len(d) not in (10, 11):
            continue
        if len(d) == 11 and not d.startswith(("090", "080", "070", "050", "0120")):
            continue
        if "-" not in t and len(d) == 11 and d[:3] in ("090", "080", "070", "050"):
            t = "%s-%s-%s" % (d[:3], d[3:7], d[7:])
        return t
    return None


def to_pref(s):
    s = nfkc(s).strip()
    if not s:
        return None
    for p in PREFS:
        if s.startswith(p):
            return p
    for short, p in SHORT_PREF.items():
        if s.startswith(short):
            return p
    return None


def extract_city(addr, pref):
    rest = addr[len(pref):] if pref and addr.startswith(pref) else addr
    if not rest:
        return ""
    for ex in CITY_EXCEPTIONS:
        if rest.startswith(ex):
            return ex
    m = re.match(r"^([^市区町村郡\d]{1,6}郡[^市区町村\d]{1,6}?[町村])", rest)
    if m:
        return m.group(1)
    m = re.match(r"^([^市区町村郡\d]{1,7}?市[^市区町村\d]{1,5}?区)", rest)
    if m and len(m.group(1)) <= 9:
        return m.group(1)
    m = re.match(r"^([^市区町村郡\d]{1,7}?[市区町村])", rest)
    if m:
        return m.group(1)
    return ""


# ----------------------------------------------------------------------------
# 列挙
# ----------------------------------------------------------------------------
def parse_cards(html):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for li in soup.select("li.card__outer--sub-page"):
        a = li.find("a", href=True)
        if a is None:
            continue
        m = re.match(r"^(?:https://www\.point-i\.jp)?/boats/(\d+)/?$", a["href"].strip())
        if not m:
            continue
        t = li.select_one(".card__title")
        tag = li.select_one(".card__tag")
        d = li.select_one(".card__date")
        out.append({
            "id": m.group(1),
            "title": one_line(t.get_text()) if t else "",
            "tag": one_line(tag.get_text()) if tag else "",
            "date": one_line(d.get_text()) if d else "",
        })
    return out


def last_page(html, extra=""):
    pat = r"/boats\?page=(\d+)" + (r"&(?:amp;)?" + re.escape(extra) if extra else r"(?![&\d])")
    nums = [int(x) for x in re.findall(pat, html)]
    return max(nums) if nums else 1


def enumerate_all(fx, log):
    first = fx.get(BASE + "/boats") or ""
    cards = parse_cards(first)
    lp = last_page(first)
    for n in range(2, lp + 1):
        h = fx.get("%s/boats?page=%d" % (BASE, n)) or ""
        cs = parse_cards(h)
        cards.extend(cs)
        lp2 = last_page(h)
        if lp2 > lp:  # 途中で件数が増えた場合
            lp = lp2
    by_id = {}
    order = []
    for c in cards:
        if c["id"] not in by_id:
            by_id[c["id"]] = c
            order.append(c["id"])
    # 県フィルタ → ID ごとの都道府県
    soup = BeautifulSoup(first, "lxml")
    opts = []
    for o in soup.select("select#prefecture_id option"):
        v = (o.get("value") or "").strip()
        if v.isdigit() and int(v) > 0:
            opts.append((int(v), one_line(o.get_text())))
    pref_of = {}
    for pid, pname in opts:
        pname = to_pref(pname) or (PREFS[pid - 1] if 1 <= pid <= 47 else pname)
        u1 = "%s/boats?prefecture_id=%d" % (BASE, pid)
        h = fx.get(u1) or ""
        cs = parse_cards(h)
        lpp = last_page(h, "prefecture_id=%d" % pid)
        for n in range(2, lpp + 1):
            hh = fx.get("%s/boats?page=%d&prefecture_id=%d" % (BASE, n, pid)) or ""
            cs.extend(parse_cards(hh))
        for c in cs:
            if c["id"] in pref_of and pref_of[c["id"]] != pname:
                log("WARN id %s in 2 pref filters: %s / %s" % (c["id"], pref_of[c["id"]], pname))
                continue
            pref_of[c["id"]] = pname
            if c["id"] not in by_id:  # 一覧に無いが県フィルタにある
                by_id[c["id"]] = c
                order.append(c["id"])
    log("enumerate: list pages=%d cards=%d unique=%d, pref filters=%d mapped=%d"
        % (lp, len(cards), len(order), len(opts), len(pref_of)))
    return order, by_id, pref_of


# ----------------------------------------------------------------------------
# 詳細ページ: ラベル付きフィールドへ線形化
# ----------------------------------------------------------------------------
LABELS = [
    (r"船名|船宿名|屋号", "name"),
    (r"出船形態|営業形態|業態|形態", "type"),
    (r"出港地|出船場所|出港場所|出港|乗船場所|集合場所", "port"),
    (r"釣り場|釣場|漁場|ポイント", "grounds"),
    (r"釣り物|釣物|釣魚|対象魚|ターゲット|主な釣り物|季節別釣り物", "targets"),
    (r"釣り方|釣法", "methods"),
    (r"駐車場", "parking"),
    (r"住所|所在地", "address"),
    (r"代表者|代表|船長|船長名|船頭", "owner"),
    (r"TEL|TEL/FAX|TEL・FAX|TEL&FAX|電話|電話番号|連絡先|問い合わせ|問合せ|お問い合わせ", "tel"),
    (r"設備|船内設備", "facilities"),
    (r"料金|料金表|乗船料|料金・スペック|料金・連絡先", "price"),
    (r"全長|船の長さ", "length"),
    (r"定員|乗船定員|最大定員", "capacity"),
    (r"総トン数|トン数", "tonnage"),
    (r"船長からのコメント|コメント|船長より|船長から一言|ひとこと|一言|PR|紹介", "comment"),
    (r"ホームページ|HP|URL|公式サイト|WEB|ウェブサイト", "website"),
    (r"SNS", "sns"),
    (r"備考|その他|注意事項", "notes"),
    (r"関連ページ", "related"),
    (r"定休日|休日|休業日|休船日", "holidays"),
    (r"出船時間|出船時刻|営業時間|出港時間", "time"),
    (r"地図|アクセス|MAP", "map"),
    (r"スペック|船舶|船舶情報", "section"),
]
LABEL_RES = [(re.compile(r"^(?:%s)$" % pat), key) for pat, key in LABELS]
INLINE_LABEL_RE = re.compile(r"【([^】]{1,12})】")


def label_of(text):
    t = nfkc(text or "").upper()
    t = re.sub(r"[\s【】\[\]≪≫《》<>■□●○◆◇▼▽★☆:：]", "", t)
    if not t or len(t) > 12:
        return None
    for rx, key in LABEL_RES:
        if rx.match(t):
            return key
    return None


class Fields(object):
    def __init__(self):
        self.items = []  # [key, [lines], [(href, text, line_text)]]
        self.iframes = []
        self.cur = None

    def start(self, key):
        self.cur = [key, [], []]
        self.items.append(self.cur)

    def add_text(self, text):
        if self.cur is None:
            self.start("_pre")
        for ln in clean(text).split("\n"):
            ln = ln.strip()
            if ln:
                self.cur[1].append(ln)

    def add_link(self, href, text, ctx):
        if self.cur is None:
            self.start("_pre")
        self.cur[2].append((href, text, ctx))

    def get(self, key):
        lines = []
        for k, ls, _ in self.items:
            if k == key:
                lines.extend(ls)
        return lines

    def links(self, key=None):
        out = []
        for k, _, lk in self.items:
            if key is None or k == key:
                out.extend(lk)
        return out

    def has(self, key):
        return any(k == key for k, _, _ in self.items)


def node_text(tag):
    """<br> と block 要素を改行にしたテキスト。"""
    d = BeautifulSoup(str(tag), "lxml")
    for br in d.find_all("br"):
        br.replace_with("\n")
    for b in d.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"]):
        b.insert_after("\n")
    return clean(d.get_text())


def add_block(fields, tag):
    for ifr in tag.find_all("iframe"):
        fields.iframes.append(ifr.get("src") or "")
    text = node_text(tag)
    # 行頭の【ラベル】で分割（2018年形式の一部）
    parts = []
    pos = 0
    for m in INLINE_LABEL_RE.finditer(text):
        key = label_of(m.group(1))
        if key is None:
            continue
        parts.append((None, text[pos:m.start()]))
        parts.append((key, ""))
        pos = m.end()
    parts.append((None, text[pos:]))
    for key, t in parts:
        if key is not None:
            fields.start(key)
        elif t.strip():
            fields.add_text(t)
    for a in tag.find_all("a", href=True):
        href = a["href"].strip()
        par = a.find_parent(["p", "td", "li", "div", "span"])
        ctx = one_line(par.get_text(" ")) if par is not None else ""
        fields.add_link(href, one_line(a.get_text()), ctx)


BLOCK_TAGS = ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "figure", "blockquote",
              "section", "dl", "dt", "dd", "center", "tbody", "thead")


def walk(fields, node):
    for ch in list(node.children):
        if isinstance(ch, Comment):
            continue
        if isinstance(ch, NavigableString):
            if str(ch).strip():
                fields.add_text(str(ch))
            continue
        name = ch.name
        if name in ("script", "style", "noscript", "img"):
            continue
        if name == "iframe":
            fields.iframes.append(ch.get("src") or "")
            continue
        if name == "table":
            for tr in ch.find_all("tr"):
                cells = tr.find_all(["td", "th"], recursive=False)
                if len(cells) >= 2:
                    key = label_of(cells[0].get_text())
                    if key:
                        fields.start(key)
                        for c in cells[1:]:
                            add_block(fields, c)
                        continue
                for c in cells:
                    add_block(fields, c)
            continue
        text = one_line(ch.get_text(" "))
        has_iframe = ch.find("iframe") is not None
        if not has_iframe and text and len(text) <= 14:
            key = label_of(text)
            if key:
                fields.start(key)
                continue
        # <p><strong>料金</strong><br>本文</p> のような先頭ラベル
        if name in ("p", "div", "td", "li") and not has_iframe:
            first = None
            for c in ch.children:
                if isinstance(c, NavigableString) and not str(c).strip():
                    continue
                first = c
                break
            if first is not None and getattr(first, "name", None) in ("strong", "b", "span") \
                    and label_of(first.get_text()):
                fields.start(label_of(first.get_text()))
                first.extract()
                add_block(fields, ch)
                continue
        if name in ("div", "section", "blockquote", "center", "ul", "ol", "tbody") and \
                ch.find(["h1", "h2", "h3", "h4", "table", "p"]) is not None:
            walk(fields, ch)
            continue
        add_block(fields, ch)


def parse_latlon(srcs):
    for src in srcs:
        s = src or ""
        if "google" not in s:
            continue
        la = lo = None
        m = re.search(r"!2d(-?\d+(?:\.\d+)?)!3d(-?\d+(?:\.\d+)?)", s)
        if m:
            lo, la = float(m.group(1)), float(m.group(2))
        else:
            m = re.search(r"[?&](?:ll|q|center|sll)=(-?\d+\.\d+)(?:,|%2C)\s*(-?\d+\.\d+)", s)
            if m:
                la, lo = float(m.group(1)), float(m.group(2))
        if la is not None and 20.0 <= la <= 46.5 and 122.0 <= lo <= 154.5:
            return round(la, 6), round(lo, 6)
    return None, None


# ----------------------------------------------------------------------------
# 項目ごとの正規化
# ----------------------------------------------------------------------------
def split_items(text):
    t = nfkc(text)
    t = re.sub(r"[◯○●◎■□◆◇※★☆♪]", " ", t)
    return [x.strip(" -~〜～:：。.") for x in re.split(r"[・、,，/／\n\s;；]+", t) if x.strip(" -~〜～:：。.")]


def season_label(s):
    s = nfkc(s)
    return bool(re.search(r"(春|夏|秋|冬|通年|年中|周年|シーズン|\d+月|\d+~\d+)", s))


def parse_targets(lines):
    """釣り物欄 → (targets, methods_from_targets)"""
    targets, methods = [], []
    for ln in lines:
        t = nfkc(ln)
        if re.search(r"(です|ます|ません|ください)[。!]?$", t) and len(t) > 12:
            continue
        # ≪春≫ 等の季節ラベルは捨て、＜イカメタル船＞ のような見出しは中身を残す
        def br(m):
            inner = m.group(1)
            return " " if season_label(inner) and len(inner) <= 12 else " %s " % inner
        t = re.sub(r"[≪《<\[【]([^≫》>\]】]{0,20})[≫》>\]】]", br, t)
        t = re.sub(r"[(（]([^)）]{0,20})[)）]", lambda m: " " if season_label(m.group(1)) else " %s " % m.group(1), t)
        t = re.sub(r"(昼|夜|朝|午前|午後)の部[:：]?", " ", t)
        t = re.sub(r"\d{1,2}[:：]\d{2}", " ", t)
        for tok in split_items(t):
            tok = re.sub(r"(など|等|他|その他|全般|各種|狙い|釣り|船)$", "", tok)
            tok = re.sub(r"^(その他|他)$", "", tok)
            if not tok or tok in SEASON_WORDS or re.search(r"\d", tok) or season_label(tok) and len(tok) <= 4:
                continue
            if tok in ("メイン", "中心", "盆明けまで", "昼", "夜", "日中", "磯", "沖", "船", "磯釣り", "船釣り", "夜釣り"):
                continue
            # 魚種＋釣り方（キハダキャスティング、青物ジギング、イカの泳がせ）
            hit_m = None
            for mw in METHOD_SUFFIX:
                if tok == mw:
                    hit_m = (mw, "")
                    break
                if tok.endswith(mw):
                    hit_m = (mw, tok[: -len(mw)].rstrip("のノ"))
                    break
            if hit_m:
                methods.append(hit_m[0])
                if hit_m[1]:
                    tok = hit_m[1]
                else:
                    continue
            tok = re.sub(r"(メイン|中心|のみ|限定|狙い|便|船)$", "", tok)
            # 語中の釣り方語は methods へ移して取り除く（タチウオテンヤ→タチウオ）
            for mw in METHOD_SUFFIX:
                if len(mw) >= 3 and mw in tok:
                    methods.append(mw)
                    tok = tok.replace(mw, " ")
            # 魚種辞書に当たる語だけを採る（「壱岐方面」「近海」「季節の釣り物」等は捨てる）
            if tok.strip() in GENERIC_TARGETS:
                targets.append(tok.strip())
                continue
            targets.extend(fish_in(tok.replace("イカダ", " ").replace("筏", " ")))
    return uniq(targets), uniq(methods)


GENERIC_TARGETS = ("青物", "根魚", "五目", "底物", "底もの", "ロックフィッシュ", "回遊魚", "大物", "高級魚", "小物",
                   "フラットフィッシュ", "イカ類", "ハタ類", "タコ類")


def fish_in(tok):
    """語を左から走査し、各位置で最長の魚種名を採る（コマセマダイ→マダイ、佐伯ブリ→ブリ）。"""
    out = []
    i = 0
    while i < len(tok):
        hit = None
        for f in FISH_SORTED:
            if tok.startswith(f, i):
                hit = f
                break
        if hit:
            out.append(hit)
            i += len(hit)
        else:
            i += 1
    return out


def parse_methods(lines):
    out = []
    for ln in lines:
        t = nfkc(ln)
        if len(t) > 40 and re.search(r"(です|ます)", t):
            continue
        t = re.sub(r"[≪《<\[【]([^≫》>\]】]{0,20})[≫》>\]】]", " ", t)
        t = re.sub(r"[(（][^)）]{0,8}[)）]", " ", t)
        t = re.sub(r"[()（）]", " ", t)  # 「瀬渡し(フカセ・遠投カゴ…)」の長い括弧は中身を列挙として残す
        for tok in split_items(t):
            tok = re.sub(r"(など|等|他)$", "", tok)
            if not tok or len(tok) > 14 or re.search(r"\d", tok):
                continue
            if tok in ("その他", "磯釣り", "船釣り"):
                continue
            out.append(tok)
    return uniq(out)


def split_outside_parens(t):
    """括弧の外側だけで区切る（「電源(12V/24V)」「氷(1日、チャーター時は有り)」を割らない）。"""
    out, buf, depth = [], [], 0
    for ch in t:
        if ch in "(（":
            depth += 1
        elif ch in ")）" and depth:
            depth -= 1
        if depth == 0 and (ch in "・、,，/／。!！\n" or ch.isspace()):
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return out


def parse_facilities(lines):
    out = []
    for ln in lines:
        t = nfkc(ln)
        t = re.sub(r"[【\[≪《<][^】\]≫》>]{0,12}[】\]≫》>]", " ", t)  # 【装備一覧】【トイレ】などの見出し
        for tok in split_outside_parens(t):
            tok = tok.strip(" ※-")
            if not tok or len(tok) > 20:
                continue
            if re.search(r"(ください|ご利用|ご予約|電話|お問い合わせ|あります|できます|いただけ|左側|右側)", tok):
                continue
            if tok in ("有", "有り", "あり", "完備", "無", "なし"):
                continue
            out.append(tok)
    return uniq(out)[:30]


def parse_capacity(lines):
    t = nfkc(" ".join(lines))
    m = re.search(r"(\d{1,3})\s*(?:名|人)", t)
    if m:
        v = int(m.group(1))
        return v if 0 < v < 200 else None
    m = re.match(r"^\s*(\d{1,3})\s*$", t)
    if m:
        v = int(m.group(1))
        return v if 0 < v < 200 else None
    return None


def owner_tokens(lines):
    toks = []
    for ln in lines:
        t = nfkc(ln)
        t = re.sub(r"[(（][^)）]*[)）]", " ", t)
        t = re.sub(r"(船長|代表|船頭|オーナー|様)", " ", t)
        parts = [p for p in re.split(r"\s+", t) if len(p) >= 2]
        toks.extend(parts)
        joined = "".join(parts)
        if len(joined) >= 2:
            toks.append(joined)
        if parts and len(parts[0]) >= 3 and re.match(r"^[一-龥々]+$", parts[0]):
            toks.append(parts[0][:2])
    return uniq(toks)


def safe_sentences(lines, owner):
    txt = nfkc(" ".join(lines))
    sents = [s.strip() for s in re.split(r"(?<=[。!?！？♪])\s*|\s{2,}", txt) if s.strip()]
    out = []
    for s in sents:
        if any(o in s for o in owner):
            continue
        if re.search(r"(船長の|代表の|船頭の|私)", s):
            continue
        out.append(s)
    return out


# 金額: ¥13,000 / 13,000円 / 13.000円（ピリオド区切りの誤記）/ 1.5万円 / 料金欄の「70,000～」（円なし・3桁区切り）
AMOUNT_RE = re.compile(
    r"¥\s*(?P<y>\d{1,3}(?:[,.]\d{3})+|\d{3,7})(?![\d,])(?:\s*円)?"
    r"|(?P<e>\d{1,3}(?:[,.]\d{3})+|\d{3,7})\s*円"
    r"|(?P<m>\d+(?:\.\d+)?)\s*万\s*(?P<m2>\d{1,4})?\s*円"
    r"|(?<![\d.,])(?P<b>\d{1,3}(?:,\d{3})+)(?![\d,])(?=\s*(?:[~〜～(（/]|$))")
KIND_SHITATE_RE = re.compile(r"(チャーター|仕立|貸切|貸し切|1隻|一隻|1艘|貸船)")
KIND_NORIAI_RE = re.compile(r"(乗合|乗り合い|乗合船)")
KIND_TOSEN_RE = re.compile(r"(瀬渡し|渡船|渡し|磯|波止|沖堤|一文字|堤防|筏|イカダ|カセ)")
PER_PERSON_RE = re.compile(r"(お?\s*(?:1|一)\s*(?:人|名)\s*様?(?!\s*(?:増|追加|プラス|~|〜|～))|/\s*(?:人|名)|大人|男性|おひとり|お一人)")
# 餌・弁当・レンタル等の付帯料金の見出し（以後の少額の行は付帯料金とみなす）
BAIT_RE = re.compile(r"(エサ(?!釣)|餌(?!釣)|オキアミ|ボイル|弁\s*当|レンタル|貸竿|貸し竿|ライフジャケット|駐車|参観|見学)")
EXTRA_RE = re.compile(r"(レンタル|貸竿|貸し竿|タックル|駐車|仮眠|弁当|弁\s+当|保険|追加|増える|増す|増し|増毎|プラス|UP|アップ|"
                      r"オキアミ|ボイル|参観|見学|同伴|エサ等|餌等|撒き餌|まき餌|集魚剤|"
                      r"割増|割引|子供|子ども|小学生|中学生|高校生|学生|女性|キャンセル|延長|燃料|サーチャージ|"
                      r"予約金|手数料|氷代|エサ代|餌代|氷\s*\d|エサ\s*\d|餌\s*\d|ライフジャケット|クーラー|送迎)")
PERSON_EXTRA_RE = re.compile(r"(追加|増える|増す|増し|増毎|プラス|UP|アップ|割増|超える|以上|以降)")
TAIL_RE = re.compile(r"^\s*(?:(?:[~〜～\-ー]\s*)(?:¥\s*[\d,.]+|[\d,.]+\s*円|\d{1,3}(?:,\d{3})+(?![\d,]))?)?\s*(?:[(（][^()（）]*[)）]\s*)*"
                     r"(?:税込|税別|込み|/\s*(?:人|名|隻))?\s*(?:[(（][^()（）]*[)）])?")
BRACKET_SEG_RE = re.compile(r"(?=[【＜<≪《■●◆])")
TIME_RE = re.compile(r"(午前|午後|AM|PM|am|pm)?\s*(\d{1,2})\s*(?::|時(?!間))\s*(\d{1,2}|半)?\s*分?")


def find_times(s):
    s = nfkc(s)
    res = []
    for m in TIME_RE.finditer(s):
        if ":" not in m.group(0) and "時" not in m.group(0):
            continue
        ap, h, mi = m.group(1), int(m.group(2)), m.group(3)
        mm = 30 if mi == "半" else (int(mi) if mi else 0)
        if ap and ap.lower() in ("午後", "pm") and h < 12:
            h += 12
        if h > 24 or mm > 59:
            continue
        res.append(("%02d:%02d" % (h % 24, mm), m.start(), m.end()))
    return res


def amount_value(m):
    for g in ("y", "e", "b"):
        if m.group(g):
            return int(re.sub(r"[,.]", "", m.group(g)))
    try:
        v = float(m.group("m")) * 10000 + (int(m.group("m2")) if m.group("m2") else 0)
        return int(round(v))
    except (ValueError, TypeError):
        return None


def kind_of_text(t):
    if KIND_SHITATE_RE.search(t):
        return "仕立"
    if KIND_NORIAI_RE.search(t):
        return "乗合"
    if KIND_TOSEN_RE.search(t):
        return "渡船"
    return None


def plan_name(label, prefix):
    p = nfkc(prefix)
    p = re.sub(r"^[\s/・]*[(（][^)）]*[)）]", "", p)  # 「/(2人より出船)※1名様乗船」の先頭の注記
    p = p.replace("※", " ")
    p = re.sub(r"^\s*から", "", p)
    p = re.sub(r"(お\s*(?:1|一)\s*(?:人|名)\s*様?|(?:1|一)\s*(?:人|名)\s*(?:様|あたり|につき)?|おひとり様?|大人|¥)", " ", p)
    p = re.sub(r"^[\s・:：、,。\-~〜～※)）/]+|[\s・:：、,。\-~〜～※(（/]+$", "", p)
    p = re.sub(r"\s+", " ", p).strip()
    lab = nfkc(label or "").strip()
    if lab and p and p != lab:
        name = "%s %s" % (lab, p)
    else:
        name = lab or p
    return name[:40]


def parse_price_field(lines, default_kind, boat_targets, src_url):
    plans = []
    ctx_kind = default_kind
    pending_label = ""
    extra_ctx = False
    for raw in lines:
        line = nfkc(raw).strip()
        if not line:
            continue
        amts = list(AMOUNT_RE.finditer(line))
        if not amts:
            k = kind_of_text(line)
            if BAIT_RE.search(line) and len(line) <= 30:
                extra_ctx = True
            elif k or re.match(r"^\s*[【＜<≪《■●◆]", line):
                extra_ctx = False
            if k and len(line) <= 30:
                ctx_kind = k
            if len(line) <= 30 and not line.startswith("※"):
                pl = re.sub(r"^[(（]([^)）]{1,20})[)）]\s*", r"\1 ", line)
                pending_label = pl.strip("・:： ")
            continue
        # 行頭の業態語（「乗合 近海ジギング・タイラバ 13,000円」）は続く行の見出しとしても効かせる
        mh = re.match(r"^\s*[【＜<≪《■●◆・]?\s*(乗合い?|乗り合い|チャーター|仕立て?|貸切|貸し切り|渡船料?)", line)
        if mh:
            ctx_kind = kind_of_text(mh.group(1)) or ctx_kind
        # 行内を【】＜＞ 等の見出しで分割
        segs = [s for s in BRACKET_SEG_RE.split(line) if s.strip()]
        for seg in segs:
            label = ""
            body = seg
            m = re.match(r"^\s*[【＜<≪《]([^】＞>≫》]{1,30})[】＞>≫》]\s*", seg)
            if m:
                label = m.group(1)
                body = seg[m.end():]
            else:
                m = re.match(r"^\s*[■●◆]\s*([^\s:：]{1,20})[\s:：]", seg)
                if m:
                    label = m.group(1)
                    body = seg[m.end():]
            if not label and pending_label:
                label = pending_label
            seg_kind = kind_of_text(label) if label else None
            prev_kind = None
            amts = list(AMOUNT_RE.finditer(body))
            consumed_until = 0
            prev_end = 0
            for am in amts:
                if am.start() < consumed_until:
                    continue
                val = amount_value(am)
                prefix = body[prev_end:am.start()]
                tail_m = TAIL_RE.match(body[am.end():])
                tail = tail_m.group(0) if tail_m else ""
                end = am.end() + len(tail)
                consumed_until = end
                chunk = (prefix + body[am.start():am.end()] + tail).strip()
                prev_end = end
                own = prefix + tail
                if BAIT_RE.search(own):
                    extra_ctx = True
                elif kind_of_text(prefix):
                    extra_ctx = False
                # 餌・弁当の見出しの後の少額行（「(生Mサイズ)1,150円」）は付帯料金
                if extra_ctx and val is not None and val < 3000 and not kind_of_text(prefix + label):
                    continue
                # 「遠方のポイントは+3,000円」「(通し釣りは1000円」のような加算・注記の金額は前のプランの注記へ
                if re.search(r"(\+|プラス)\s*¥?\s*$", prefix) or \
                        (val is not None and val < 5000 and prefix.count("(") > prefix.count(")")):
                    if plans:
                        pt = plans[-1]["price_text"]
                        add = chunk.strip(" 、,")
                        if add and add not in pt:
                            plans[-1]["price_text"] = (pt + " " + add).strip()[:160]
                    continue
                if EXTRA_RE.search(prefix) or (EXTRA_RE.search(own) and not kind_of_text(prefix)):
                    if plans and PERSON_EXTRA_RE.search(chunk) and not re.search(r"(レンタル|駐車|子供|子ども|小学生|女性)", chunk):
                        pt = plans[-1]["price_text"]
                        add = chunk.strip(" 、,")
                        if add and add not in pt:
                            plans[-1]["price_text"] = (pt + " " + add).strip()
                    continue
                # 「エリアにより1,000円～2,000円プラス」のように金額の後ろに加算語が来るもの
                if re.match(r"^(?:\s*[~〜～\-]\s*(?:¥\s*)?[\d,]+\s*円?)?(プラス|増し|UP|アップ|割増|加算)", body[am.end():]):
                    if plans:
                        pt = plans[-1]["price_text"]
                        add = (prefix + body[am.start():am.end() + 20]).strip(" 、,")
                        mk = re.search(r"(プラス|増し|追加|UP|アップ|割増|加算)", add)
                        if mk:
                            add = add[:mk.end()]
                        if add and add not in pt:
                            plans[-1]["price_text"] = (pt + " " + add).strip()[:160]
                    continue
                if val is None or val < 500 or val > 1000000:
                    continue
                # 「8,000円/1名 女性」のように区分が金額の後ろにある子供・女性料金は捨てる
                if plans and re.match(r"^[\s/]*(?:(?:1|一)\s*(?:名|人)\s*様?)?[\s/]*(女性|子供|子ども|小学生|中学生|高校生|学生|シニア|小人)"
                                      r"(?!\S{0,10}?\s*[:：]?\s*[¥\d])", body[end:]):
                    continue
                kind = kind_of_text(prefix) or kind_of_text(tail) or seg_kind or prev_kind or ctx_kind
                if kind is None and plans and re.match(r"^[\s・]*(平日|土日|休日|祝日|土・日|日祝|金・土|日~木|繁忙期|閑散期)",
                                                        nfkc(prefix or label)):
                    kind = plans[-1]["kind"]  # 「チャーター 平日…／土日祝…」の続き
                if kind is None and PER_PERSON_RE.search(chunk):
                    kind = "乗合"
                prev_kind = kind
                per_boat = bool(re.search(r"(1隻|一隻|/\s*隻|貸切|チャーター)", chunk)) or \
                    (val > 30000 and bool(re.search(r"\d+\s*名様?まで", chunk)))  # 「乗合 ¥90,000(5名まで)」
                per_group = bool(re.search(r"(家族|ファミリー|グループ|団体)", prefix + tail))
                hourly = bool(re.search(r"(?:1|一)\s*時間\s*[:：]?\s*¥?\s*$", prefix))
                if per_group or hourly:
                    price = None
                elif kind in ("乗合", "渡船"):
                    price = val if not per_boat and val <= 100000 else None
                elif kind == "仕立":
                    price = val if val >= 5000 else None  # 「チャーター 定員8名 1000円」等の不整合は価格にしない
                else:
                    price = val if (not per_boat and val <= 30000) else None
                name = plan_name(label, prefix)
                times = find_times(seg)
                depart = ret = None
                if times:
                    depart = times[0][0]
                    if len(times) >= 2 and re.search(r"[~〜～\-]", seg[times[0][2]:times[1][1]] or ""):
                        ret = times[1][0]
                season = ""
                ms = re.search(r"(\d{1,2}月(?:上旬|中旬|下旬|頃)?\s*[~〜～\-]\s*\d{1,2}月(?:上旬|中旬|下旬|頃|末)?)",
                               nfkc(label) + " " + seg)
                if ms:
                    season = ms.group(1)
                days = ""
                md = re.search(r"(平日|土日祝|土日|休日|祝日)", label + " " + prefix)
                if md:
                    days = md.group(1)
                ptxt = chunk if not label or label in chunk else ("%s %s" % (label, chunk))
                tg = [t for t in boat_targets if t and t in (label + seg)]
                plans.append({
                    "name": name or (kind or ""),
                    "kind": kind,
                    "targets": tg,
                    "price": price,
                    "price_text": ptxt[:120],
                    "depart": depart,
                    "return": ret,
                    "meet": "",
                    "season": season,
                    "days": days,
                    "includes": "",
                    "url": src_url,
                })
        pending_label = ""
    # 同一内容の重複を除く
    out, seen = [], set()
    for p in plans:
        k = (p["name"], p["kind"], p["price"], p["price_text"])
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def clean_address(lines, pref):
    for ln in lines:
        a = nfkc(ln).strip()
        if not a or a.startswith("※") or a.startswith("http"):
            continue
        a = re.sub(r"〒?\s*\d{3}\s*-\s*\d{4}", "", a).strip()
        a = re.sub(r"^(住所|所在地)\s*[:：]?\s*", "", a)
        if not re.search(r"[一-龥々ぁ-んァ-ン]", a):
            continue
        a = re.split(r"\s*(?:※|\s)駐車場", a)[0].strip()  # 「愛南町中泊942 駐車場は中泊港」
        if not a or re.search(r"(駐車場|お問い合わせ|ください)", a):
            continue
        a = re.sub(r"[(（][^)）]*[)）]$", "", a).strip()
        a = re.sub(r"\s+", "", a)
        a = re.sub(r"[‐‑‒–—―ー−](?=\d)", "-", a)
        short = [s for s in SHORT_PREF if a.startswith(s)]
        s_short = max(short, key=len) if short else ""
        if s_short and s_short not in PREFS and a[len(s_short):len(s_short) + 1] == "市":
            # 「長崎市為石町…」は県名の略記ではなく市名 → 県名を前に付ける
            a = SHORT_PREF[s_short] + a
        elif pref and not to_pref(a):
            a = pref + a
        elif s_short and not any(a.startswith(p) for p in PREFS):
            a = SHORT_PREF[s_short] + a[len(s_short):]
        return a
    return ""


PORT_SUFFIX_RE = re.compile(r"(漁港|港|マリーナ|ハーバー|桟橋|岸壁|波止場|船着場|フィッシャリーナ)$")


def port_of(text, pref, city):
    t = nfkc(text).strip()
    if not t:
        return ""
    # 「兵庫県加古郡播磨町古宮 古宮漁港」「長崎市 為石漁港」→ 空白区切りの最後の港名トークンを優先
    toks = [x.strip("、,・()（）") for x in re.split(r"\s+", t) if x.strip("、,・()（）")]
    if len(toks) >= 2:
        for tok in toks:
            if PORT_SUFFIX_RE.search(tok) and 2 <= len(tok) <= 12 and not re.search(r"\d", tok) \
                    and not to_pref(tok) and not re.search(r"[市郡]", tok[:-1]):
                return tok
    t = re.sub(r"\s+", "", t)
    if pref and t.startswith(pref):
        t = t[len(pref):]
    else:
        for s in sorted(SHORT_PREF, key=len, reverse=True):
            # 大分県の「佐賀関金山港」の「佐賀」は県名ではない → レコードの県と一致するときだけ外す
            if t.startswith(s) and len(t) > len(s) + 1 and t[len(s)] != "市" and (not pref or SHORT_PREF[s] == pref):
                t = t[len(s):]
                break
    c = extract_city(t, None)
    if c and len(t) > len(c):
        t = t[len(c):]
    m = re.search(r"([^、,/・()（）]{1,12}?(?:漁港|港|マリーナ|ハーバー|桟橋|岸壁|波止場|船着場|フィッシャリーナ))", t)
    if m:
        p = m.group(1)
        m2 = re.match(r"^.{1,6}?[町村](.{2,}(?:漁港|港|マリーナ|ハーバー|桟橋|岸壁|波止場|船着場|フィッシャリーナ))$", p)
        if m2:
            p = m2.group(1)
        return p
    return t if len(t) <= 20 and not re.search(r"\d", t) else ""


def name_from_title(title):
    t = one_line(nfkc(title))
    region = ""
    m = re.match(r"^【([^】]+)】\s*", t)
    if m:
        region = m.group(1)
        t = t[m.end():]
        # 【長崎】遊漁船 凪 / 【兵庫】神戸須磨 釣り船 純栄丸 / 【広島】遊漁船スカイマリン
        t = re.sub(r"^(?:\S{0,6}?\s)?(?:(?:遊漁船|釣り船|釣船)(?:\s+|(?=[A-Za-zァ-ヶ一-龥]))|(?:瀬渡船|瀬渡し船|渡船)\s+)(?=\S)",
                   "", t)
    else:
        # 「山陰渡船 千鳥丸」「神戸須磨 釣り船 純栄丸」「遊漁船スカイマリン」
        m = re.match(r"^(\S{0,6}?)\s?(遊漁船|瀬渡船|瀬渡し船|渡船|釣り船|釣船)(?:\s+|(?=[A-Za-zァ-ヶ]))(?=\S)", t)
        if m:
            region = m.group(1)
            t = t[m.end():]
    kana = ""
    mk = re.match(r"^(.+?)\s*[(（]([ァ-ヶー・\s]{2,})[)）]$", t)  # 「This branch(ディスブランチ)」
    if mk:
        t, kana = mk.group(1), kata2hira(re.sub(r"[\s・]", "", mk.group(2)))
    parts = t.split(" ")
    if len(parts) >= 2 and re.match(r"^[ぁ-んァ-ンー]+$", parts[-1]):
        kana = kata2hira(parts[-1])
        t = " ".join(parts[:-1])
    return t.strip(), kana, region


# ----------------------------------------------------------------------------
# レコード組み立て
# ----------------------------------------------------------------------------
NONBOAT_RE = re.compile(r"(釣り堀|釣堀|海上釣堀|釣具店|レンタルボート|貸しボート|民宿|旅館|ホテル)")


def parse_detail(html, bid, card, pref_map):
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1.article__heading") or soup.find("h1")
    title = one_line(h1.get_text()) if h1 else (card or {}).get("title", "")
    art = soup.select_one(".article__text")
    if art is None:
        return None, "no article"
    f = Fields()
    walk(f, art)
    src_url = "%s/boats/%s" % (BASE, bid)

    t_name, kana, region = name_from_title(title)
    fname = nfkc(" ".join(f.get("name")[:1])).strip()
    fname = re.sub(r"^(遊漁船|渡船|釣り船|釣船)\s*", "", fname) if len(fname) > 4 else fname
    name = t_name or fname
    if fname and t_name:
        a, b = re.sub(r"\s", "", fname), re.sub(r"\s", "", t_name)
        if a in b and len(a) < len(b) and len(a) >= 2:
            name = fname

    # 都道府県
    addr_lines = f.get("address")
    port_lines = f.get("port")
    pref = None
    addr_pref = None
    for ln in addr_lines:
        addr_pref = to_pref(re.sub(r"^〒?\s*\d{3}\s*[-－]\s*\d{4}\s*", "", nfkc(ln)))
        if addr_pref:
            break
    port_pref = to_pref(" ".join(port_lines[:1]))
    map_pref = pref_map.get(bid)
    reg_pref = to_pref(region) if region else None
    if not reg_pref and region in REGION_PREF:
        reg_pref = REGION_PREF[region]
    if not reg_pref and region:
        reg_pref = to_pref(region[:3]) or to_pref(region[:2])
    pref = addr_pref or map_pref or port_pref or reg_pref
    conflict = ""
    if addr_pref and map_pref and addr_pref != map_pref:
        conflict = "%s(address) vs %s(filter)" % (addr_pref, map_pref)

    address = clean_address(addr_lines, pref)
    city = extract_city(address, pref) if address else ""
    port_text = " ".join(port_lines[:1])
    if not city and port_text:
        pt = nfkc(re.sub(r"\s+", "", port_text))
        pt = pt[len(pref):] if pref and pt.startswith(pref) else pt
        city = extract_city(pt, None)
    port = port_of(port_text, pref, city)

    lat, lon = parse_latlon(f.iframes)

    # 電話
    tel = None
    for ln in f.get("tel"):
        s = nfkc(ln)
        if re.search(r"FAX", s, re.I) and not re.search(r"(TEL|電話|携帯|℡)", s, re.I):
            continue
        tel = norm_tel(s)
        if tel:
            break
    if not tel:
        for ln in f.get("notes") + f.get("_pre"):
            s = nfkc(ln)
            if re.search(r"(TEL|電話|携帯|℡)", s, re.I):
                tel = norm_tel(s)
                if tel:
                    break

    # website / sns
    website = None
    sns = []
    for href, text, ctx in f.links():
        if is_sns(href) and not is_own_sns(href):
            sns.append(href)
    for href, text, ctx in f.links("website"):
        if is_official_candidate(href):
            website = href
            break
    if not website:
        for ln in f.get("website"):
            m = re.search(r"(https?://[\w\-./?%&=~#:+@]+)", nfkc(ln))
            if m and is_official_candidate(m.group(1)):
                website = m.group(1)
                break
    if not website:
        for key in ("notes", "tel", "related", "comment", "_pre"):
            for href, text, ctx in f.links(key):
                if not is_official_candidate(href):
                    continue
                c = nfkc(ctx)
                if re.search(r"(HP|ホームページ|公式|WEB|サイト|ブログ)", c, re.I) and not re.search(r"(フォーム|問い合わせ|予約)", nfkc(text)):
                    website = href
                    break
            if website:
                break
    sns = uniq(sns)

    # 業態
    type_text = nfkc(" ".join(f.get("type")))
    tag = (card or {}).get("tag", "")
    price_lines = f.get("price")
    ptxt_all = nfkc(" ".join(price_lines))
    types = []
    if re.search(r"(遊漁|釣り船|釣船)", type_text + tag + title) and KIND_NORIAI_RE.search(ptxt_all):
        types.append("乗合")
    if KIND_NORIAI_RE.search(type_text):
        types.append("乗合")
    if KIND_SHITATE_RE.search(type_text + " " + ptxt_all):
        types.append("仕立")
    if re.search(r"(瀬渡|渡船|渡し)", type_text + tag + title):
        types.append("渡船")
    default_kind = None
    tt = type_text + " " + tag
    if re.search(r"(瀬渡|渡船)", tt) and not re.search(r"遊漁", tt):
        default_kind = "渡船"
    elif not type_text and re.search(r"(瀬渡|渡船)", title) and not re.search(r"遊漁", title + tag):
        default_kind = "渡船"

    targets, m_from_t = parse_targets(f.get("targets"))
    methods = uniq(parse_methods(f.get("methods")) + m_from_t)

    plans = parse_price_field(price_lines, default_kind, targets, src_url)
    for p in plans:
        if p["kind"] in ("乗合", "仕立", "渡船") and p["kind"] not in types:
            types.append(p["kind"])
    types = uniq(types)

    owner = owner_tokens(f.get("owner"))

    holidays = ""
    hl = [nfkc(x).strip("・※ ") for x in f.get("holidays")]
    for ln in f.get("notes"):
        s = nfkc(ln).strip("・※ ")
        if re.search(r"(定休|休み|休業|休船|休日)", s) and len(s) <= 60 and not any(o in s for o in owner):
            hl.append(s)
    holidays = "／".join(uniq(hl))[:80]

    facilities = parse_facilities(f.get("facilities"))
    capacity = parse_capacity(f.get("capacity"))

    acc = []
    pk = [nfkc(x) for x in f.get("parking") if not nfkc(x).startswith("※詳しく")]
    if pk:
        acc.append("駐車場: " + " ".join(pk)[:80])
    access = "／".join(acc)

    desc = ""
    for s in safe_sentences(f.get("comment"), owner):
        if re.match(r"^(はじめまして|初めまして|はじめはして|こんにちは|こんばんは)", s) or len(s) < 8:
            continue
        if re.search(r"(\d{2,4}-\d{2,4}-\d{3,4}|http)", s):
            continue
        desc = s if len(s) <= 100 else s[:99] + "…"
        break

    sched = []
    for key in ("time", "comment", "notes", "targets", "price"):
        for s in safe_sentences(f.get(key), owner):
            if find_times(s) and re.search(r"(出船|出港|帰港|沖上がり|沖上り|便|の部|集合|受付|まで|~|〜|～)", s):
                if re.search(r"(電話|TEL|受付時間|対応可能|営業時間|問い合わせ)", s, re.I):
                    continue
                sched.append(s[:80])
    schedule_text = "／".join(uniq(sched))[:200]

    spec = []
    for key, lab in (("length", "全長"), ("tonnage", "総トン数")):
        v = one_line(nfkc(" ".join(f.get(key))))
        if v:
            spec.append("%s%s" % (lab, v[:40]))
    if spec and not desc:
        desc = ""
    rec = {
        "src": "point",
        "src_id": bid,
        "src_url": src_url,
        "name": name,
        "kana": kana,
        "pref": pref,
        "city": city or None,
        "address": address or None,
        "port": port or None,
        "lat": lat, "lon": lon,
        "tel": tel,
        "website": website,
        "sns": sns,
        "types": types,
        "targets": targets,
        "methods": methods,
        "holidays": holidays,
        "facilities": facilities + [s for s in spec if s not in facilities],
        "capacity": capacity,
        "access": access,
        "description": desc,
        "plans": plans,
        "schedule_text": schedule_text,
        "fetched": FETCHED,
    }
    info = {"conflict": conflict, "keys": [k for k, _, _ in f.items], "title": title}
    return rec, info


def save(path, recs):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(recs, fp, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="point-i.jp boats crawler")
    ap.add_argument("--limit", type=int, default=0, help="一覧の先頭 N 件だけ")
    ap.add_argument("--ids", default="", help="ID（カンマ区切り）")
    ap.add_argument("--per-pref", type=int, default=0, help="県フィルタごとに先頭 N 件（サンプル用）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    ap.add_argument("--interval", type=float, default=MIN_INTERVAL)
    args = ap.parse_args()

    out = args.out if os.path.isabs(args.out) else os.path.join(os.getcwd(), args.out)
    logp = args.log if os.path.isabs(args.log) else os.path.join(os.getcwd(), args.log)
    log = Logger(logp)
    fx = Fetcher(log, refresh=args.refresh, interval=args.interval)
    log("START point out=%s limit=%s ids=%s per_pref=%s refresh=%s"
        % (out, args.limit, args.ids or "-", args.per_pref, args.refresh))
    fx.load_robots()

    order, cards, pref_map = enumerate_all(fx, log)
    listed = set(order)
    ids = list(order)
    if args.ids:
        ids = [x.strip() for x in args.ids.split(",") if x.strip().isdigit()]
    elif args.per_pref:
        byp = {}
        for i in order:
            byp.setdefault(pref_map.get(i, "-"), []).append(i)
        ids = []
        for p in sorted(byp):
            ids.extend(byp[p][: args.per_pref])
    if args.limit:
        ids = ids[: args.limit]
    total = len(ids)
    log("targets: %d boats" % total)

    recs = []
    fails = []
    t0 = time.time()
    last_log = 0.0
    for i, bid in enumerate(ids, 1):
        url = "%s/boats/%s" % (BASE, bid)
        try:
            html = fx.get(url)
            if html is None:
                log("MISS %s (404)" % url)
            else:
                rec, info = parse_detail(html, bid, cards.get(bid), pref_map)
                if rec is None:
                    log("SKIP %s (%s)" % (url, info))
                else:
                    card = cards.get(bid) or {}
                    if NONBOAT_RE.search(card.get("tag", "") + " " + info["title"]):
                        log("SKIP %s (not a boat: %s / %s)" % (url, info["title"], card.get("tag", "")))
                    else:
                        if bid not in listed:
                            rec["stale"] = True
                        if info["conflict"]:
                            log("WARN %s pref conflict %s" % (bid, info["conflict"]))
                        recs.append(rec)
        except Exception as e:  # noqa
            fails.append(bid)
            log("ERR %s %s: %s" % (bid, e.__class__.__name__, e))
        now = time.time()
        if i == total or i % 10 == 0 or now - last_log > 60:
            last_log = now
            log("progress %d/%d boats, records=%d, plans=%d, net=%d, cache=%d, elapsed=%.0fs"
                % (i, total, len(recs), sum(len(x["plans"]) for x in recs), fx.n_net, fx.n_cache, now - t0))
        if i % 100 == 0:
            save(out, recs)
    save(out, recs)
    if fails:
        log("FAILED (%d): %s" % (len(fails), ",".join(fails)))
    log("DONE %d records" % len(recs))


if __name__ == "__main__":
    main()
