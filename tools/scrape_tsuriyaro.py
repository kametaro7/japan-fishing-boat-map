#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
釣り野郎 (https://fishing-station.jp/) クローラ

サイト構造（2026-09-15 時点で確認）
- WordPress。robots.txt は /wp-admin/ のみ Disallow。
- 詳細ページは sitemap.xml → post-sitemap.xml / post-sitemap2.xml / post-sitemap3.xml に 2,724件
  （URL は /<県slug>-<店slug>/）。
- 詳細ページ: #post_meta_top に .cat-category（都道府県）と .cat-category2（遊び方: 遊漁船/瀬渡し/
  かせ船/イカダ渡し/曳き船/レンタルボート/釣り堀/カヤック/手漕ぎボート/渓流釣り/釣り公園、複数可）。
  .post_content の表に 名前/住所/URL/電話(またはお問い合わせ)/料金/特徴。料金セルは箇条書き・入れ子の表など自由形式。
  地図は Google Maps embed の pb（!1d 表示幅m !2d 経度 !3d 緯度 = 地図の中心）。
- 一覧 /category2/<遊び方>/page/N/（10件/頁）のカードにも全カテゴリが載る。
  → 船宿系でない遊び方（釣り堀・レンタルボート等）の一覧だけ先に取り、船宿系カテゴリを1つも持たない投稿は
    詳細を取得せずに除外する（リクエスト節約）。それ以外は詳細ページのカテゴリで最終判定。
- 出力するのは 遊漁船/瀬渡し/かせ船/イカダ渡し/曳き船 のいずれかを持つ投稿だけ。

座標: 場所カード付きの埋め込み地図は、カードを避けるため中心が店の位置から西へ「約0.13×表示幅(!1d)」ずれる
      （緯度はほぼ一致）。他ソースと電話番号で突き合わせた41件で確認: 3.3km幅→約0.3km、13km幅→約1.7km、
      26km幅→約3.5km、200km幅→約26km。ずれない地図もあるので補正はせず、!1d <= 7000m（東西誤差 1km 未満）
      の場合だけ採用。それ以外は null（後段でジオコーディング）。

使い方
  python3 tools/scrape_tsuriyaro.py                       # 全件
  python3 tools/scrape_tsuriyaro.py --limit 30            # 先頭から船宿30件
  python3 tools/scrape_tsuriyaro.py --sample 40 --out work/sources/tsuriyaro.sample.json  # 県ごとに分散して40件
  python3 tools/scrape_tsuriyaro.py --ids wakayama-komatsutosen,oita-rentaruboto-2
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
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup, NavigableString

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "tsuriyaro")
OUT_DEFAULT = os.path.join(ROOT, "work", "sources", "tsuriyaro.json")
LOG_PATH = os.path.join(ROOT, "work", "logs", "tsuriyaro.log")
TMP_DIR = os.path.join(ROOT, "work", "tmp", "tsuriyaro")

BASE = "https://fishing-station.jp/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒（SPEC: 0.8秒以上）
FETCHED = "2026-09-15"
MAP_MAX_SPAN = 7000.0  # pb の !1d（m）がこれ以下のときだけ中心座標を採用（下の docstring 参照）

BOAT_CATS = ["遊漁船", "瀬渡し", "かせ船", "イカダ渡し", "曳き船"]
TOSEN_CATS = ["瀬渡し", "かせ船", "イカダ渡し", "曳き船"]
NONBOAT_CATS = ["レンタルボート", "釣り堀", "カヤック", "手漕ぎボート", "渓流釣り", "釣り公園"]

PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県",
         "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県",
         "山梨県", "長野県", "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府",
         "兵庫県", "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県",
         "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県",
         "鹿児島県", "沖縄県"]
PREF_SHORT = [p if p == "北海道" else p[:-1] for p in PREFS]
SEIREI = ["横浜市", "川崎市", "相模原市", "千葉市", "さいたま市", "静岡市", "浜松市", "名古屋市", "京都市",
          "大阪市", "堺市", "神戸市", "岡山市", "広島市", "北九州市", "福岡市", "熊本市", "新潟市",
          "仙台市", "札幌市"]

# 掲載サイト・予約サイト・まとめサイトなど（船宿の公式サイトではない）
PORTAL_HOSTS = [
    "fishing-station.jp", "a8.net", "google.com", "google.co.jp", "goo.gl", "g.page", "maps.app.goo.gl",
    "chowari.jp", "tsuree.jp", "theboat.jp", "castingnet.jp", "funaduri.jp", "fishing-v.jp", "kanpari.jp",
    "yahoo.co.jp", "asoview.com", "jalan.net", "rakuten.co.jp", "airtrip.jp", "veltra.com", "tabelog.com",
    "tsurimaru.jp", "yugyosen.com", "point-i.jp", "gurenavi.jp", "tsurisoku.com", "nikkansports.com",
    "ishiguro-gr.com", "yugyosen-navi.com", "reserver.co.jp", "tsuttarou.info", "tsuri-navi.jp",
    "tokyowan-yugyosen.or.jp", "activityjapan.com", "itp.ne.jp", "mapion.co.jp", "hotpepper.jp",
    "zekkouchou.com", "minnaga.com", "tsuri-info.jp", "turi100.jp", "e-turibune.com", "gyo.ne.jp",
    "fishbank.jp", "anglers.jp", "tsurinews.jp", "fishing.ne.jp", "kaishu-wakayama.com", "tsuri.ne.jp",
]
SNS_HOSTS = ["facebook.com", "fb.com", "instagram.com", "twitter.com", "x.com", "line.me", "lin.ee",
             "youtube.com", "youtu.be", "tiktok.com", "threads.net"]
# 多数の利用者がいるホスト（トップだけのURLは公式サイトとして意味をなさない）
SHARED_HOSTS = ["ameblo.jp", "fc2.com", "goope.jp", "wixsite.com", "jimdo.com", "jimdofree.com",
                "jimdosite.com", "blogspot.com", "hatenablog.com", "livedoor.jp", "seesaa.net",
                "amebaownd.com", "shopinfo.jp", "business.site", "peraichi.com", "crayonsite.net",
                "wordpress.com", "weebly.com", "studio.site", "webnode.jp", "hp.gogo.jp"]

_last_req = [0.0]
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_stats = {"requests": 0, "cache_hits": 0}
_log_path = [LOG_PATH]


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    with open(_log_path[0], "a", encoding="utf-8") as f:
        f.write(line + "\n")


def cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


class NotFound(Exception):
    pass


def fetch(url, refresh=False):
    """キャッシュ優先で取得。1ホスト直列・間隔 MIN_INTERVAL 秒・429/503 は指数バックオフ（最大5回）。"""
    if "/wp-admin/" in url:
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
        if r.status_code in (404, 410):
            raise NotFound("HTTP %d: %s" % (r.status_code, url))
        if r.status_code != 200:
            raise RuntimeError("HTTP %d: %s" % (r.status_code, url))
        body = r.content
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, p)
        return body.decode("utf-8", "replace")
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------- 文字列ヘルパ

def clean(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("​", "").replace("﻿", "").replace(" ", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def kata2hira(s):
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in s)


def host_of(url):
    m = re.match(r"^https?://([^/:?#]+)", url or "", re.I)
    if not m:
        return ""
    h = m.group(1).lower()
    return h[4:] if h.startswith("www.") else h


def host_in(h, hosts):
    return any(h == x or h.endswith("." + x) for x in hosts)


def classify_url(url):
    """'official' / 'sns' / 'portal' / None"""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return None
    h = host_of(url)
    if not h or "." not in h:
        return None
    if host_in(h, SNS_HOSTS):
        path = re.sub(r"^https?://[^/]+", "", url)
        seg = [x for x in re.split(r"[/?#]", path) if x]
        if not seg or seg[0].lower() in ("share", "sharer", "sharer.php", "intent", "hashtag", "search", "p",
                                         "reel", "explore", "watch", "embed", "plugins", "dialog"):
            return None
        return "sns"
    if host_in(h, PORTAL_HOSTS):
        return "portal"
    if host_in(h, SHARED_HOSTS):
        path = re.sub(r"^https?://[^/]+", "", url)
        if not [x for x in re.split(r"[/?#]", path) if x] and h.count(".") <= 1 + (1 if h.endswith(".jp") and
                                                                                   not h.endswith("co.jp") else 0):
            return None
        if h in ("r.goope.jp", "goope.jp", "ameblo.jp", "fc2.com", "wixsite.com") and \
                not [x for x in re.split(r"[/?#]", path) if x]:
            return None
    return "official"


# ---------------------------------------------------------------- 住所

def split_address(addr):
    """住所 → (pref, city)。"""
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
                m2 = re.match(r"^(%s[^\d\-]{1,4}?区)" % re.escape(sc), rest)
                city = m2.group(1) if m2 else sc
                break
        if not city:
            m = re.match(r"^(.{1,6}?市)", rest)
            if m:
                city = m.group(1)
            elif pref == "東京都":
                m = re.match(r"^(?:[^\d]{1,3}?島(?=.{1,4}?[町村]))?(.{1,4}?[区町村])", rest)
                city = m.group(1) if m else None
            else:
                m = re.match(r"^(.{1,6}?[町村])", rest)
                city = m.group(1) if m else None
    return pref, city


PORT_TOKEN_RE = re.compile(r"([^\s\d,、()・/]{1,12}?(?:漁港|港|マリーナ|ボートパーク))"
                           r"(?![区町通丁目市\d一二三四五六七八九十]|[東西南北中新本上下](?:[\d一二三四五六七八九十町丁]|$))")
PORT_SUFFIX_ONLY_RE = re.compile(r"(?:漁港|港|マリーナ|ボートパーク)")


def find_port(text):
    """テキスト中の港名トークン（住所の市区町村部分は落とす）。無ければ None。"""
    for m in PORT_TOKEN_RE.finditer(text or ""):
        t = m.group(1)
        if re.search(r"港区|港町|港南|港北|空港", t):
            continue
        t = re.sub(r"^.*[都道府県市区町村郡](?=.)", "", t)   # 「大洗町磯浜町大洗港」→「大洗港」
        t = re.sub(r"^(?:大字|字)", "", t)
        t = re.sub(r"^.*?(?:係留場|桟橋|岸壁|地先)", "", t)
        t = re.sub(r"^(.{2,4})\1", r"\1", t)                 # 「育波育波漁港」→「育波漁港」
        if len(t) < 2 or PORT_SUFFIX_ONLY_RE.fullmatch(t):
            continue
        return t
    return None


def norm_address(raw, cat_pref):
    """住所セル → (address, port)"""
    lines = [clean(x) for x in (raw or "").split("\n") if clean(x)]
    if not lines:
        return None, None
    a = lines[0]
    a = re.split(r"(?:TEL|Tel|tel|電話|営業時間|定休日|駐車場[:：]|アクセス|MAP|地図)", a)[0]
    a = re.sub(r"〒?\s*\d{3}\s*-\s*\d{4}", "", a).strip()
    port = None
    # （三津浜港）（弘漁港内）など
    for m in re.finditer(r"\(([^()]{1,20})\)", a):
        p2 = find_port(m.group(1))
        if p2 and port is None:
            port = p2
    a = re.sub(r"\([^()]{0,30}\)", "", a)
    a = re.sub(r"\s*\([^()]*$", "", a)  # 閉じ括弧の無い注記「(筏川沿い」
    a = re.sub(r"(?:より|から|にて)\s*(?:出船|出港|出航|発着)\S*$|\s*(?:出船|出港|発着)(?:場所)?$", "", a)
    # 空白区切りの後ろ側が乗り場の説明なら住所から外す
    toks = a.split(" ")
    kept = []
    for i, t in enumerate(toks):
        if i > 0 and re.search(r"桟橋|ボートパーク|駐車場|乗り?場|集合|付近|前$|内$|裏$|横$|隣$|沿い|そば", t):
            p2 = find_port(t)
            if p2 and port is None:
                port = p2
            continue
        kept.append(t)
    a = "".join(kept)
    a = re.sub(r"付近$", "", a)
    a = a.strip(" 、,。")
    if not a:
        return None, port
    if not any(a.startswith(p) for p in PREFS):
        short = None
        for p, sh in zip(PREFS, PREF_SHORT):
            if p != "北海道" and a.startswith(sh) and not a.startswith(p):
                short = (p, sh)
                break
        if short:
            p, sh = short
            if a[len(sh):len(sh) + 1] in ("市", "町", "村", "区", "郡"):
                # 「鹿児島市…」「静岡市…」は県名の略ではなく市名
                a = (cat_pref if cat_pref and cat_pref != p and a[len(sh)] != "市" else p) + a
            else:
                a = p + a[len(sh):]
        elif cat_pref:
            a = cat_pref + a
    if port is None:
        port = find_port(a[3:])
    return a, port


def norm_tel(s):
    s = clean(s)
    s = re.sub(r"[‐‑–—―ー−]", "-", s)
    # 1) ハイフン区切りの番号（セルに複数あれば先頭）
    for m in re.finditer(r"(?<![\d-])0\d{1,4}\s*-\s*\d{1,4}\s*-\s*\d{3,4}(?![\d-])", s):
        t = re.sub(r"\s", "", m.group(0))
        if len(re.sub(r"\D", "", t)) in (10, 11):
            return t
    # 2) 0XX(XXX)XXXX
    for m in re.finditer(r"(?<![\d-])(0\d{1,4})\s*\(\s*(\d{1,4})\s*\)\s*(\d{3,4})(?![\d-])", s):
        t = "-".join(m.groups())
        if len(re.sub(r"\D", "", t)) in (10, 11):
            return t
    # 3) 連続数字
    for m in re.finditer(r"(?<![\d-])0\d{9,10}(?![\d-])", s):
        return m.group(0)
    return None


# ---------------------------------------------------------------- 魚種・釣り方

def _kw(s):
    return r"(?<![ァ-ヶー])(?:%s)(?![ァ-ヶー])" % s


FISH_PATTERNS = [
    ("オニカサゴ|鬼カサゴ", "オニカサゴ"), ("アカムツ|ノドグロ|のどぐろ", "アカムツ"),
    ("キンメダイ|金目鯛|" + _kw("キンメ"), "キンメダイ"), ("アコウダイ", "アコウダイ"), ("アマダイ|甘鯛", "アマダイ"),
    ("クロダイ|黒鯛|" + _kw("チヌ"), "クロダイ"), ("イシダイ|石鯛", "イシダイ"), ("イシガキダイ", "イシガキダイ"),
    ("アオリイカ|障泥烏賊", "アオリイカ"), ("ケンサキイカ|剣先イカ|マルイカ|アカイカ", "ケンサキイカ"),
    ("ヤリイカ|槍イカ", "ヤリイカ"), ("スルメイカ|" + _kw("スルメ"), "スルメイカ"), ("コウイカ|スミイカ|墨イカ", "コウイカ"),
    ("タチウオ|太刀魚", "タチウオ"), ("ヒラマサ|平政", "ヒラマサ"), ("カンパチ|間八", "カンパチ"),
    ("ヒラスズキ", "ヒラスズキ"), ("シーバス|" + _kw("スズキ") + "|鱸", "シーバス"),
    ("ロウニンアジ|" + r"(?<![A-Za-z])GT(?![A-Za-z])", "ロウニンアジ"), ("シマアジ", "シマアジ"),
    ("マダイ|真鯛|" + _kw("タイ(?:ラバ)?") + "|(?<![金甘黒石花目])鯛", "マダイ"),
    ("ワラサ", "ワラサ"), ("イナダ", "イナダ"), ("ハマチ", "ハマチ"), (_kw("ブリ") + "|鰤", "ブリ"),
    ("サワラ|鰆", "サワラ"), ("ヒラメ|鮃|平目", "ヒラメ"), ("マゴチ|" + _kw("コチ"), "マゴチ"),
    ("カレイ|鰈", "カレイ"), ("シロギス|" + _kw("キス") + "|鱚", "シロギス"), ("メバル|眼張", "メバル"),
    ("カサゴ|笠子", "カサゴ"), ("アイナメ", "アイナメ"), ("クロソイ|" + _kw("ソイ"), "ソイ"),
    ("キジハタ|" + _kw("アコウ"), "キジハタ"), ("アカハタ", "アカハタ"), (_kw("クエ"), "クエ"),
    ("ハタハタ", "ハタハタ"), (_kw("ハタ"), "ハタ"), ("イサキ|イサギ", "イサキ"), ("メジナ|" + _kw("グレ"), "メジナ"),
    ("ウマヅラハギ|ウマヅラ|ウマズラ", "ウマヅラハギ"), ("カワハギ|皮剥", "カワハギ"), (_kw("フグ") + "|河豚", "フグ"),
    (_kw("ハゼ"), "ハゼ"), ("マダコ|真蛸", "マダコ"), ("イイダコ", "イイダコ"), (_kw("タコ") + "|蛸", "タコ"),
    ("キハダ", "キハダ"), ("クロマグロ|本マグロ", "クロマグロ"), ("マグロ|鮪|トンジギ", "マグロ"),
    ("カツオ|鰹", "カツオ"), ("シイラ|鱪", "シイラ"), ("マダラ|" + _kw("タラ") + "|鱈", "マダラ"), ("ホッケ", "ホッケ"),
    ("サクラマス", "サクラマス"), (_kw("サケ") + "|秋鮭|鮭", "サケ"), (_kw("マス"), "マス"),
    (_kw("アジ(?:ング)?") + "|鯵|鰺", "アジ"), (_kw("サバ") + "|鯖", "サバ"), (_kw("イワシ"), "イワシ"),
    ("カマス", "カマス"), ("サヨリ", "サヨリ"), ("アナゴ|穴子", "アナゴ"), ("イシモチ", "イシモチ"),
    ("ホウボウ", "ホウボウ"), (_kw("ムツ"), "ムツ"), ("グルクン", "グルクン"), ("アカジン", "アカジン"),
    ("ワカサギ", "ワカサギ"), ("オコゼ", "オコゼ"), ("カジキ", "カジキ"), (_kw("メダイ"), "メダイ"),
    (_kw("イカ(?:メタル)?") + "|烏賊", "イカ"), ("青物", "青物"), ("根魚|ロックフィッシュ", "根魚"),
    ("五目", "五目釣り"),
]
FISH_RES = [(re.compile(p), c) for p, c in FISH_PATTERNS]

METHOD_PATTERNS = [
    ("タイラバ|鯛ラバ", "タイラバ"), ("スーパーライトジギング|SLJ", "SLJ"), ("ジギング", "ジギング"),
    ("ティップラン", "ティップラン"), ("エギング", "エギング"), ("イカメタル", "イカメタル"), ("オモリグ", "オモリグ"),
    ("テンヤ", "テンヤ"), ("泳がせ|ノマセ|のませ", "泳がせ"), ("コマセ|ビシ釣り", "コマセ"),
    ("キャスティング", "キャスティング"), ("トローリング", "トローリング"), ("落とし込み", "落とし込み"),
    ("サビキ", "サビキ"), ("エビング", "エビング"), ("アジング", "アジング"), ("メバリング", "メバリング"),
    ("夜焚き", "夜焚き"), ("フカセ", "フカセ"), ("磯釣り", "磯釣り"), ("かかり釣り|掛かり釣り", "かかり釣り"),
    ("筏釣り|イカダ釣り|いかだ釣り", "筏釣り"), ("カセ釣り", "カセ釣り"), ("エサ釣り|餌釣り", "エサ釣り"),
    ("ルアー", "ルアー"), ("電動", "電動リール"), ("流し釣り", "流し釣り"), ("ひとつテンヤ|一つテンヤ", "テンヤ"),
]
METHOD_RES = [(re.compile(p), c) for p, c in METHOD_PATTERNS]


def find_fish(text):
    t = clean(text)
    out = []
    for rx, canon in FISH_RES:
        def _sub(m):
            if canon not in out:
                out.append(canon)
            return "＿" * len(m.group(0))
        t = rx.sub(_sub, t)
    return out


def find_methods(text):
    t = clean(text)
    out = []
    for rx, canon in METHOD_RES:
        if rx.search(t) and canon not in out:
            out.append(canon)
    return out


# ---------------------------------------------------------------- 料金セル → プラン

AMT_RE = re.compile(
    r"(?P<man>\d+(?:\.\d+)?)\s*万\s*(?:(?P<sen>\d)\s*千)?\s*(?P<rest>\d{1,4})?\s*円"
    r"|(?P<sen2>\d{1,3})\s*千\s*円"
    r"|[¥\\]\s*(?P<yen>\d{1,3}(?:,\d{3})+|\d{3,7})(?![\d,])\s*円?"
    r"|(?P<en>\d{1,3}(?:,\d{3})+|\d{3,7})\s*円"
    r"|(?P<bare>(?<![\d,.])\d{1,3}(?:,\d{3})+)(?![\d,])(?:\s*-(?!\s*\d))?"
    r"|(?P<dash>(?<![\d,.])\d{4,6})\s*-(?!\s*\d)")
SUFFIX_RE = re.compile(
    r"(?:\s*(?:円|~|〜|-|。|税込み?|税別|税抜き?|程度|前後|/\s*[^\s/()]{1,10}|\([^()]{0,30}\)))*")
# 金額の直後（空白を挟まない）の「増し/引き/追加」。「54,000円 追加1名につき…」は次の句なので対象外
TAIL_EXTRA_RE = re.compile(r"^円?(?:増|引|割|UP|アップ|プラス|追加|\s*/\s*(?:1|一)?\s*(?:人|名)\s*増)")

PP_RE = re.compile(
    r"/\s*(?:お|御)?(?:1|一)?\s*(?:人|名)(?!\s*(?:まで|迄|以下|以上|から|より|増))"
    r"|(?<![\d,])(?:お|御)?(?:1|一)\s*(?:人|名)\s*様?(?!\s*(?:まで|迄|以下|以上|から|より|[~〜\-]\s*\d|増|追加|で|分|乗船|の場合))"
    r"|おひとり|お一人|大人|男性|一般")
CH_RE = re.compile(
    r"貸\s*切|貸し\s*切|チャーター|仕\s*立|一船|(?<![\d第])1\s*船(?!長)|/\s*1?\s*(?:隻|艘|船)|(?<!\d)(?:1|一)\s*(?:隻|艘)"
    r"|\d+\s*(?:人|名)\s*様?\s*(?:まで|迄)|定員\s*\d+\s*(?:人|名)?\s*(?:まで|迄)")
CH_SECTION_RE = re.compile(r"貸\s*切|貸し\s*切|チャーター|仕\s*立|一船|/\s*隻")
NORI_RE = re.compile(r"乗\s*合|乗り\s*合|のりあい")
TOSEN_RE = re.compile(r"渡船|瀬渡|磯渡|磯釣り|筏|イカダ|いかだ|カセ|かせ|テトラ|堤防|波止|沖堤|一文字|渡し")
EXTRA_RE = re.compile(r"増し|増え|増す|増ごと|増毎|追加|プラス|割引|引き|延長|超える|超えた|超過|越え|以上は|加算|キャンセル|最低|差額|割増"
                      r"|磯替|再渡船|再迎え|場所替")
OPT_RE = re.compile(
    r"貸し?竿|貸道具|貸し道具|レンタル|タックル|ロッド|リール|弁当|食事|昼食|朝食|夕食|宿泊|素泊|民宿|エサ代|餌代|^エサ|^餌|^氷"
    r"|駐車|入漁|入場|延長|キャンセル|燃料|手数料|保険|送迎|送り迎え|ライフジャケット|救命|桶|バケツ|クーラー"
    r"|(?<!ルアー)ボート(?!パーク|フカセ|釣|エギング|シーバス|ゲーム|ジギング|アジング|ロック|フィッシング)"
    r"|馬力|カヤック|シャワー|会員|年会費|スタンプ|仕掛け?代|仕掛け?セット|オモリ代|入会|写真|土産|捌き|さばき|加工|発送"
    r"|イソメ|オキアミ|コマセ代|ジグ|ルアー代|ルアーレンタル|解禁|セット|ゲーム代|施設利用|清掃|ライセンス|リ-ル|食堂")
OPT_SECTION_RE = re.compile(r"レンタル|オプション|キャンセル|貸し?竿|貸し?道具|その他の?料金|追加料金|延長|割引|注意事項|持ち物")
CHILD_RE = re.compile(r"子供|こども|子ども|小人|小学|中学|高校|学生|幼児|未就学|女性|シニア|レディース|キッズ|ジュニア|\d+\s*歳|婦人"
                      r"|カップル|ペア|ファミリー|家族")
ADULT_RE = re.compile(r"一般|大人|男性|高校生以上|中学生以上")
KANJI_DIGITS = str.maketrans("二三四五六七八九", "23456789")
GROUP_ROW_RE = re.compile(r"^\d+(?:\s*[~〜\-]\s*\d+)?\s*(?:人|名)\s*様?\s*[:：]")
GROUP_PREFIX_RE = re.compile(r"^\s*\d+(?:\s*[~〜\-]\s*\d+)?\s*(?:人|名)\s*様?\s*[:：]?\s*$")
GROUP_N_RE = re.compile(r"(?<![\d~〜\-.,])(\d{1,2})\s*(?:人|名)\s*様?\s*(?:乗り)?(?!\s*(?:以上|以下|増|追加|から|より|[~〜\-]))")
PP_ADJ_RE = re.compile(r"(?:(?<!\d)(?:お|御)?(?:1|一)\s*(?:人|名)\s*様?|おひとり様?|お一人様?)\s*(?:あたり|当たり|につき)?"
                       r"\s*[:：/]?\s*\(?\s*$")
PP_ADJ_SUF_RE = re.compile(r"\s*円?\s*(?:\((?:税込|税別|税抜)[^()]*\))?\s*/\s*(?:お|御)?(?:1|一)?\s*(?:人|名)")
HEADER_RE = re.compile(r"^(?:[◎●■◆◇□★☆▼▽▲△○〇♦【<＜《\[『]|[①-⑳]|[・･]|[A-ZＡ-Ｚ]\s|\d+\.\s)")
NEUTRAL_RE = re.compile(r"^(?:※|\*|＊|\((?:税込|税別|税抜)[^()]*\)$|\([^()]{0,20}\)$)")
GENERIC_LABEL_RE = re.compile(r"^(?:平日|土日祝?日?|土・日・祝日?|土日・祝日|休日|祝日|特日|乗合|乗り合い|乗合い|仕立て?|貸切り?|"
                              r"貸し切り|チャーター|料金|船代|乗船料金?|基本料金|通常料金|1日|半日|一日|終日)$")

TIME_RE = re.compile(r"(午前|午後|AM|PM|am|pm|朝|夕方|夜|深夜|昼)?\s*(\d{1,2})\s*(?::|時(?!間))\s*(\d{1,2}|半)?\s*分?")


def amount_value(m):
    g = m.groupdict()
    try:
        if g.get("man"):
            v = float(g["man"]) * 10000
            if g.get("sen"):
                v += int(g["sen"]) * 1000
            elif g.get("rest"):
                v += int(g["rest"])
            return int(round(v))
        if g.get("sen2"):
            return int(g["sen2"]) * 1000
        for k in ("yen", "en", "bare", "dash"):
            if g.get(k):
                return int(g[k].replace(",", ""))
    except ValueError:
        return None
    return None


def norm_money_line(s):
    s = clean(s)
    s = re.sub(r"(?<=\d)\s*[、.]\s*(?=\d{3}(?!\d))", ",", s)  # ５、０００円 / 13.000円
    s = re.sub(r"(?<=\d),\s+(?=\d{3}(?!\d))", ",", s)
    return s


def cell_lines(td):
    """セル → 行のリスト（入れ子の表は1行1レコードに平坦化）"""
    frag = BeautifulSoup(str(td), "html.parser")
    for tr in reversed(frag.find_all("tr")):
        cells = tr.find_all(["th", "td"], recursive=False)
        txt = " ".join(clean(c.get_text(" ")) for c in cells) if cells else clean(tr.get_text(" "))
        tr.replace_with(NavigableString("\n" + txt + "\n"))
    for br in frag.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for el in frag.find_all(["p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dt", "dd",
                             "table", "tbody", "thead", "section", "blockquote"]):
        el.insert_before(NavigableString("\n"))
        el.insert_after(NavigableString("\n"))
    lines = []
    for ln in frag.get_text("").split("\n"):
        ln = norm_money_line(ln)
        if not ln:
            continue
        # 「11,000」「円（6名まで）」のように金額と円が別行
        if lines and re.match(r"^円", ln) and re.search(r"\d$", lines[-1]):
            lines[-1] = lines[-1] + ln
            continue
        lines.append(ln)
    return lines


def find_times(s):
    res = []
    for m in TIME_RE.finditer(s):
        ap, h, mi = m.group(1), int(m.group(2)), m.group(3)
        if ":" not in m.group(0) and "時" not in m.group(0):
            continue
        pre = s[max(0, m.start() - 1):m.start()]
        if re.match(r"[\d,.:/]", pre):
            continue
        post = s[m.end():m.end() + 1]
        if ":" in m.group(0) and (mi is None or mi == "") :
            continue
        if post and re.match(r"[\d]", post):
            continue
        mm = 30 if mi == "半" else int(mi) if mi else 0
        if ap in ("午後", "PM", "pm", "夕方", "夜") and h < 12:
            h += 12
        if h > 29 or mm > 59:
            continue
        res.append(("%02d:%02d" % (h, mm), m.start(), m.end()))
    return res


def _mins(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def parse_times(t, time_col=None):
    """テキスト → (depart, return, meet)"""
    ts = find_times(t)
    dep = ret = meet = None
    if not ts:
        return None, None, None
    sunrise = bool(re.search(r"日の出|夜明け|日出|薄明", t))
    for i, (hm, st, en) in enumerate(ts):
        before = t[max(0, st - 7):st]
        after = t[en:en + 5]
        if re.search(r"集合|受付|集まり|受け付け", before) or re.match(r"\s*(?:集合|受付)", after):
            meet = meet or hm
            continue
        if re.search(r"帰港|入港|帰着|終了|沖上が?り|沖揚が?り|着岸|戻り|帰り", before) or \
                re.match(r"\s*(?:頃)?\s*(?:まで|迄|帰港|入港|頃?帰|終了|沖上|着)", after):
            if dep is None and i > 0 and ts[i - 1][0] == dep:
                pass
            ret = ret or hm
            continue
        if re.search(r"出船|出港|出航|出発|スタート|開始", before) or re.match(r"\s*(?:頃)?\s*(?:出船|出港|出航|発|スタート|から|より)", after):
            if dep is None:
                dep = hm
                continue
        if dep is None and ret is None:
            if time_col == "meet":
                meet = meet or hm
            elif sunrise and re.search(r"[~〜\-ー]\s*$", before):
                ret = hm
            else:
                dep = hm
        elif ret is None and dep is not None:
            prev_en = ts[i - 1][2]
            between = t[prev_en:st]
            if re.fullmatch(r"\s*(?:頃|ごろ)?\s*(?:出船|出港|出航)?\s*[~〜\-ー−]\s*(?:帰港|入港)?\s*:?\s*", between):
                ret = hm
    if dep and ret:
        span = (_mins(ret) - _mins(dep)) % (24 * 60)
        if span < 120:
            ret = None  # 「5:00〜6:00出船」のような出船時刻の幅
    return dep, ret, meet


def strip_label(s):
    s = clean(s)
    s = re.sub(r"^[)\]]+\s*[、,]?\s*", "", s)
    s = re.sub(r"^[◎●■◆◇□★☆▼▽▲△○〇♦・･\-→>＞]+\s*", "", s)
    s = re.sub(r"^[①-⑳]\s*", "", s)
    s = re.sub(r"(?:/\s*)?(?<!\d)(?:お|御)?(?:1|一)\s*(?:人|名)\s*様?\s*(?:あたり|当たり|につき)?", " ", s)
    s = re.sub(r"^\((?=[^)]*$)", "", s)
    s = re.sub(r"おひとり様?|お一人様?", " ", s)
    s = re.sub(r"(?:料金|代金|価格)\s*(?:は)?\s*[:：]?\s*$", "", s)
    s = re.sub(r"[\s:：→…・\-~〜=＝/]+$", "", s)
    s = re.sub(r"^[\s:：→…・\-~〜=＝/]+", "", s)
    s = re.sub(r"[\s(\[:：、,]+$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def name_candidates(ctx):
    out = []
    for c in ctx:
        if c.startswith("※") or find_times(c) or len(c) > 30 or c.endswith("。"):
            continue
        if re.match(r"^\(?(?:\d|乗船人数|お一人|おひとり|税|円|詳|ご予約|予約)", c):
            continue
        if re.search(r"お問い?合わ?せ|ください|下さい|致します|いたします", c):
            continue
        # 表の見出し行「業種 釣り物 出船時刻 料金 備考」
        if len(re.findall(r"料金|備考|釣り?物|出船時[刻間]|業種|時間|内容|プラン名", c)) >= 3:
            continue
        lab = strip_label(c)
        if lab and len(lab) >= 2 and not re.fullmatch(r"\(.*\)", lab):
            out.append(lab)
    return out


def parse_price_cell(td, cats, page_title=""):
    """料金セル → (plans, schedule_text, price_lines)"""
    lines = cell_lines(td)
    tosen_only = bool(set(cats) & set(TOSEN_CATS)) and "遊漁船" not in cats
    group_rows = [ln for ln in lines if GROUP_ROW_RE.match(ln)]
    group_table = len(group_rows) >= 2

    plans = []
    ctx = []
    section = None
    time_col = None
    last_header = ""
    cur_group_hdr = ""
    attach_group = []
    attach_ok = False
    global_times = []
    sched = []
    group_summary = []

    for ln in lines:
        segs = line_segments(ln)
        if not segs:
            if re.search(r"集合時間", ln) and re.search(r"料金|釣り?物|備考", ln):
                time_col = "meet"
            elif re.search(r"出船時間|出港時間", ln) and re.search(r"料金|釣り?物|備考", ln):
                time_col = "depart"
            has_time = bool(find_times(ln)) or bool(re.search(r"日の出|夜明け", ln))
            is_header = bool(HEADER_RE.match(ln))
            if has_time and attach_ok and attach_group and not is_header:
                d, r, mt = parse_times(ln, time_col)
                used = False
                for p in attach_group:
                    if not p["depart"] and not p["return"] and (d or r):
                        p["depart"], p["return"] = d, r
                        used = True
                    if mt and not p["meet"]:
                        p["meet"] = mt
                        used = True
                if used:
                    continue
            if has_time and len(ln) <= 60 and ln not in sched:
                sched.append(ln)
                if not plans:
                    global_times.append(ln)
            if NEUTRAL_RE.match(ln):
                ctx.append(ln)
                continue
            attach_ok = False
            # セクション
            if len(ln) <= 30 or is_header:
                if CH_SECTION_RE.search(ln):
                    section = "仕立"
                elif NORI_RE.search(ln):
                    section = "乗合"
                elif OPT_SECTION_RE.search(ln):
                    section = "opt"
                elif TOSEN_RE.search(ln):
                    section = "渡船"
                elif is_header:
                    section = None
            cands = name_candidates([ln])
            if cands and (is_header or not ctx or len(ln) <= 20):
                last_header = cands[0] if is_header or not last_header else last_header
            ctx.append(ln)
            continue

        # ---- 金額を含む行
        created = []
        near = [c for c in ctx if not c.startswith("※")][-2:]
        ctx_names = name_candidates(ctx)
        ctx_name = " ".join(ctx_names[:2]) if ctx_names else ""
        if ctx_names:
            last_header = ctx_names[0]
        lh = re.match(r"^\s*[◎●■◆◇□★☆▼▽▲△○〇♦・･【\[]*\s*([^:：\d¥\\()【】\[\]]{2,20}?)\s*[】\]]?\s*[:：]", ln)
        line_head = strip_label(lh.group(1)) if lh else ""
        line_opt = False
        # 人数別の表の見出し（「4時間コース」のように数字始まりでも可）
        raw_hdr = [c for c in ctx if not c.startswith("※") and len(c) <= 30 and not NEUTRAL_RE.match(c)
                   and not re.search(r"お問い?合|ください|下さい", c)]
        if raw_hdr:
            cur_group_hdr = strip_label(raw_hdr[-1])
        for si, sg in enumerate(segs):
            v = sg["value"]
            # 前の金額の閉じ括弧「)、」の残りを落とす
            prefix = re.sub(r"^\s*[)\]]\s*[、,]?\s*", "", sg["prefix"])
            suffix = sg["suffix"]
            # 「(追加1名様ごとに5,000円)」のような注記は1人料金の判定に使わない
            # 「(エサ お一人様1000円)」のように括弧内に別の金額があるものも除く
            suffix_pp = re.sub(r"\([^()]*(?:増|追加|割|引き|超|越|\d\s*円|エサ|餌|氷|レンタル)[^()]*\)", "", suffix)
            tail = ln[sg["amt_end"]:sg["amt_end"] + 8]
            if v is None:
                continue
            if section == "opt":
                continue
            lab = strip_label(prefix)
            meaningful = bool(lab) and not re.fullmatch(r"[\s()（）:：]*", lab)
            if group_table and GROUP_PREFIX_RE.match(prefix) and not PP_RE.search(re.sub(r"\([^()]*\)", "", suffix_pp)):
                group_summary.append((cur_group_hdr or last_header, clean(prefix + sg["amount"]), v))
                continue
            if TAIL_EXTRA_RE.match(tail) or EXTRA_RE.search(prefix) or \
                    re.search(r"\+\s*$|\d\s*(?:個|本|枚|杯|kg)\s*$|^\s*※\s*(?:エサ|餌|氷|仕掛)", prefix) or \
                    re.match(r"\s*円?\s*/\s*(?:パック|個|本|枚|杯|袋|kg)", suffix):
                continue
            last_ctx = near[-1] if near else ""
            if OPT_RE.search(lab) or (not meaningful and OPT_RE.search(strip_label(last_ctx))):
                line_opt = True
                continue
            # 「レンタルタックルは電動一式2,000円、タイラバ、ジギング1,000円となります。」の後半もオプション
            if line_opt and not (NORI_RE.search(prefix + suffix) or CH_RE.search(prefix) or PP_ADJ_RE.search(prefix)):
                continue
            if (CHILD_RE.search(prefix) and not ADULT_RE.search(prefix)) or \
                    (not meaningful and CHILD_RE.search(last_ctx) and not ADULT_RE.search(last_ctx)):
                continue
            in_paren = prefix.count("(") > prefix.count(")")
            local = prefix + " " + suffix
            nori_local = bool(NORI_RE.search(local))
            pp_adj = bool(PP_ADJ_RE.search(prefix) or PP_ADJ_SUF_RE.match(suffix_pp))
            if in_paren and not (nori_local or pp_adj or CH_RE.search(prefix)):
                continue
            pp_local = bool(PP_RE.search(prefix) or PP_RE.search(suffix_pp))
            ch_local = bool(CH_RE.search(prefix) or CH_RE.search(suffix))
            pp_near = (not meaningful) and any(PP_RE.search(c) for c in near)
            ch_near = (not meaningful) and any(CH_SECTION_RE.search(c) for c in near)
            nori = nori_local or section == "乗合" or any(NORI_RE.search(c) for c in near)
            tosen = bool(TOSEN_RE.search(prefix)) or section == "渡船" or any(TOSEN_RE.search(c) for c in near) \
                or tosen_only

            kind = None
            per_person = False
            if pp_adj and not (section == "仕立" and prefix.lstrip().startswith("(")):
                per_person = True
            elif ch_local and not nori_local:
                kind = "仕立"
            elif pp_local:
                if section == "仕立" and prefix.lstrip().startswith("(") and not nori_local:
                    continue
                per_person = True
            elif pp_near and section != "仕立" and not ch_near:
                per_person = True
            elif section == "仕立" or ch_near:
                kind = "仕立"
            elif nori or tosen:
                per_person = True
            elif v >= 30000:
                kind = "仕立"
            elif 1000 <= v <= 20000:
                per_person = True
            else:
                continue

            # 「6名 50,000円」「9名様 99000円」「カセ一艘 2人乗り 10,000円」は人数分の合計（1隻料金）
            # 括弧内の「(1人 10,500円)」は内訳なので、括弧の外に1人料金の表記があるかで判定
            pp_outside = bool(PP_RE.search(re.sub(r"\([^()]*\)", " ", prefix + " " + suffix_pp)))
            if per_person and not (pp_outside or pp_adj or nori_local):
                pg = re.sub(r"\([^()]*\)", " ", prefix).translate(KANJI_DIGITS)
                gms = list(GROUP_N_RE.finditer(pg))
                if gms:
                    n = int(gms[-1].group(1))
                    boatish = re.search(r"艘|隻|貸\s*切|貸し\s*切|チャーター|仕\s*立",
                                        " ".join(near + [last_header, cur_group_hdr, prefix]))
                    if n >= 2 and (boatish or (v >= 20000 and v >= 3000 * n)):
                        per_person = False
                        kind = "仕立"
            # 1人料金の明示が無い 3万円超は1隻料金とみなす（「湾口/60,000円」）
            if per_person and v > 30000 and not (pp_local or pp_adj):
                per_person = False
                kind = "仕立"

            if per_person:
                if not (1000 <= v <= 100000):
                    continue
                kind = "乗合" if nori else ("渡船" if tosen else "")
                price = v
            else:
                if v < 10000:
                    continue
                price = None

            # 名前
            name = lab
            head = line_head if si > 0 else ""
            if not meaningful or "。" in name or len(name) > 30:
                name = head or ctx_name or last_header
            elif GENERIC_LABEL_RE.match(name) or re.match(r"^\d", name):
                base = head or ctx_name or last_header
                if base and base not in name:
                    name = base + " " + name
            name = clean(name)[:40]

            seg_text = clean(prefix + sg["amount"] + sg["suffix"])
            seg_text = re.sub(r"^[◎●■◆◇□★☆▼▽▲△○〇♦・･\-]+\s*", "", seg_text)
            if seg_text.count("(") > seg_text.count(")"):
                seg_text += ")"
            if kind == "仕立" and not re.search(r"隻|船|艘|チャーター|貸\s*切|貸し\s*切|仕\s*立", seg_text):
                price_text = "1隻 " + seg_text
            else:
                price_text = seg_text
            if not name and kind == "仕立":
                name = "仕立"

            # 時刻: 自分の行 → 直前の文脈 → （後で）後続行 / 全体
            own = ln if len(segs) == 1 else (prefix + " " + suffix)
            d, r, mt = parse_times(own, time_col)
            if not (d or r):
                for c in reversed(ctx):
                    d2, r2, m2 = parse_times(c, time_col)
                    if d2 or r2:
                        d, r = d2, r2
                        mt = mt or m2
                        break
            season = ""
            sm = re.search(r"\d{1,2}月\s*(?:上旬|中旬|下旬|頃|末)?\s*[~〜\-]\s*\d{1,2}月\s*(?:上旬|中旬|下旬|頃|末)?",
                           " ".join(ctx[-3:]) + " " + ln)
            if sm:
                season = clean(sm.group(0))
            dm = re.search(r"平日|土日祝日?|土・日・祝日?|土日・祝日|土日|休日|祝日", prefix)
            inc = ""
            im = re.search(r"\(([^()]*(?:付|込|別)[^()]*)\)", suffix)
            if im:
                inc = clean(im.group(1))[:40]
            p = {
                "name": name,
                "kind": kind or "",
                "targets": find_fish(name + " " + " ".join(ctx[-4:]) + " " + prefix),
                "price": price,
                "price_text": price_text[:80],
                "depart": d,
                "return": r,
                "meet": mt,
                "season": season,
                "days": dm.group(0) if dm else "",
                "includes": inc,
                "url": None,
            }
            plans.append(p)
            created.append(p)
        # 改行が無く金額の後ろに次の見出しが続く「¥10,000~¥15,000■チャーター(貸し切り船)」
        new_ctx = []
        last = segs[-1]
        trailing = ln[last["amt_end"] + len(last["suffix"]):]
        hm = re.search(r"[■◆●◎【▼★☆□◇]\s*([^■◆●◎【▼★☆□◇]{1,20})$", trailing)
        if hm:
            t = hm.group(1)
            if CH_SECTION_RE.search(t):
                section = "仕立"
            elif NORI_RE.search(t):
                section = "乗合"
            elif OPT_SECTION_RE.search(t):
                section = "opt"
            elif TOSEN_RE.search(t):
                section = "渡船"
            else:
                section = None
            new_ctx = [clean(hm.group(0))]
        ctx = new_ctx
        if created:
            attach_group = created
            attach_ok = True

    # 行の前にあった全体の時刻（例: 渡船時間は日の出〜夕方4時まで）を、時刻の無いプランに適用
    if global_times:
        d, r, mt = parse_times(" ".join(global_times[:1]))
        for p in plans:
            if not p["depart"] and not p["return"] and (d or r):
                p["depart"], p["return"] = d, r
            if mt and not p["meet"]:
                p["meet"] = mt
    if group_summary:
        grouped = []
        for hdr, txt, gv in group_summary:
            if grouped and grouped[-1][0] == hdr:
                grouped[-1][1].append(txt)
                grouped[-1][2].append(gv)
            else:
                grouped.append((hdr, [txt], [gv]))
        for hdr, txts, vals in grouped:
            plans.append({"name": ((hdr + " ") if hdr else "") + "人数別料金",
                          "kind": "仕立" if max(vals) >= 20000 else "", "targets": find_fish(hdr or ""),
                          "price": None, "price_text": " / ".join(txts)[:80], "depart": None, "return": None,
                          "meet": None, "season": "", "days": "", "includes": "", "url": None})

    # 重複除去
    uniq, seen = [], set()
    for p in plans:
        k = (p["name"], p["price"], p["kind"], p["depart"], p["price_text"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq[:30], " / ".join(sched)[:120], lines


def line_segments(line):
    ms = []
    for m in AMT_RE.finditer(line):
        pre2 = line[max(0, m.start() - 2):m.start()]
        if re.search(r"\d[.,]$", pre2):
            continue
        # 時刻・日付・距離・号数などを金額と誤認しない（「円」「¥」の無い裸の数字だけ対象）
        if (m.group("bare") or m.group("dash")) and re.match(r"\s*(?:m|M|号|匹|本|kg|g|cm|mm|名|人|時|分|%|馬力|t|トン)",
                                                              line[m.end():m.end() + 3]):
            continue
        ms.append((amount_value(m), m.start(), m.end()))
    segs = []
    prev_end = 0
    prev_amt_end = None
    for v, st, en in ms:
        if st < prev_end:
            continue
        if prev_amt_end is not None and segs and re.fullmatch(r"\s*[~〜\-ー]\s*", line[prev_amt_end:st]):
            sm = SUFFIX_RE.match(line, en)
            segs[-1]["suffix"] = line[segs[-1]["amt_end"]:sm.end()]
            prev_end = sm.end()
            continue
        # 「1人/9000~10000円」: 円の付かない下限を拾い、下限を金額にする
        rm = re.search(r"(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{3,7})\s*[~〜]\s*$", line[prev_end:st])
        if rm:
            lo = int(rm.group(1).replace(",", ""))
            if v is not None and 500 <= lo < v:
                v = lo
                st = prev_end + rm.start(1)
        sm = SUFFIX_RE.match(line, en)
        suf_end = sm.end() if sm else en
        # 「~」で終わる suffix は範囲の途中なので次の金額を取り込めるよう戻す
        segs.append({"value": v, "prefix": line[prev_end:st], "amount": line[st:en],
                     "suffix": line[en:suf_end], "amt_end": en})
        prev_end = suf_end
        prev_amt_end = en
        if re.search(r"[~〜\-]\s*$", line[en:suf_end]):
            prev_end = en + len(re.sub(r"[~〜\-]\s*$", "", line[en:suf_end]))
    return segs


# ---------------------------------------------------------------- 詳細ページ

def parse_listing(html):
    s = BeautifulSoup(html, "lxml")
    cards = {}
    for li in s.select("#post_list2 li.article"):
        a = li.find("a", href=True)
        if not a:
            continue
        cards[a["href"]] = [clean(x.get_text()) for x in li.find_all(class_="cat-category2")]
    m = re.search(r"([\d,]+)件中\s*(\d+)\D+(\d+)件", clean(s.get_text(" ")))
    if m:
        total = int(m.group(1).replace(",", ""))
        per = max(1, int(m.group(3)) - int(m.group(2)) + 1)
        maxpage = (total + per - 1) // per
    else:
        pages = [int(x) for x in re.findall(r"/page/(\d+)/", html)]
        maxpage = max(pages) if pages else 1
    return cards, maxpage


def parse_map(pc):
    for ifr in pc.find_all("iframe"):
        src = ifr.get("src") or ifr.get("data-src") or ""
        if "google" not in src or "map" not in src:
            continue
        src = unquote(src)
        m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", src)
        if m:
            return float(m.group(1)), float(m.group(2)), 0.0
        m = re.search(r"!1d([\d.]+)!2d(-?\d+\.\d+)!3d(-?\d+\.\d+)", src)
        if m:
            return float(m.group(3)), float(m.group(2)), float(m.group(1))
        m = re.search(r"[?&](?:q|ll|center)=(-?\d+\.\d+),\s*(-?\d+\.\d+)", src)
        if m:
            return float(m.group(1)), float(m.group(2)), None
    return None, None, None


def title_description(title, name):
    t = clean(title)
    t = re.sub(r"\s*\((?:[^()]*?(?:%s)[^()]*)\)\s*$" % "|".join(map(re.escape, PREF_SHORT)), "", t)
    parts = re.split(r"\s+[-–‐―—−ー－]\s*|\s*[–‐―—−]\s+", t, 1)
    desc = ""
    if len(parts) == 2:
        desc = parts[1]
    elif name and t.startswith(name + " "):
        desc = t[len(name) + 1:]
    desc = clean(desc)
    if len(desc) > 100:
        desc = desc[:99] + "…"
    return desc


def parse_detail(html, url, slug):
    s = BeautifulSoup(html, "lxml")
    title_el = s.find(id="post_title")
    title = clean(title_el.get_text(" ")) if title_el else clean(s.title.get_text() if s.title else "")
    cats, cat_pref = [], None
    for a in s.select("#post_meta_top a"):
        cl = a.get("class") or []
        t = clean(a.get_text())
        if "cat-category2" in cl:
            if t and t not in cats:
                cats.append(t)
        elif "cat-category" in cl and t in PREFS and not cat_pref:
            cat_pref = t
    pc = s.find(class_="post_content")
    rows = {}
    if pc:
        for tbl in pc.find_all("table"):
            body = tbl.find("tbody", recursive=False) or tbl
            trs = body.find_all("tr", recursive=False)
            labels = [clean(tr.find(["th", "td"]).get_text()) for tr in trs if tr.find(["th", "td"])]
            if not ({"名前", "住所", "料金"} & set(labels)):
                continue
            for tr in trs:
                cells = tr.find_all(["th", "td"], recursive=False)
                if len(cells) < 2:
                    continue
                lab = clean(cells[0].get_text())
                if lab and lab not in rows:
                    rows[lab] = cells[1]
            break

    def row(*names):
        for n in names:
            if n in rows:
                return rows[n]
        return None

    name_td = row("名前", "店名", "船名", "名称")
    name = clean(name_td.get_text(" ")) if name_td else ""
    if not name:
        name = clean(re.split(r"\s+[-–‐―—−ー－]\s*|\s*[–‐―—−]\s+", title, 1)[0])
    kana = ""
    km = re.match(r"^(.*?)\s*\(([ぁ-んァ-ヶー・\s]+)\)$", name)
    if km:
        name, kana = clean(km.group(1)), kata2hira(clean(km.group(2))).replace(" ", "")

    addr_td = row("住所", "所在地")
    address, port = (None, None)
    if addr_td:
        address, port = norm_address(addr_td.get_text("\n"), cat_pref)
    if not port:
        pm = re.search(r"([^\s\d,、()・/]{1,8}?(?:漁港|港))(?:から|より|発|出港|出船|を拠点)", title)
        if pm:
            port = find_port(pm.group(1))
    pref, city = split_address(address)
    if not pref:
        pref = cat_pref

    tel_td = row("電話", "お問い合わせ", "お問合せ", "TEL", "電話番号", "連絡先")
    tel = norm_tel(tel_td.get_text(" ")) if tel_td else None

    website, sns, portals = None, [], []
    url_td = row("URL", "HP", "ホームページ", "公式サイト", "Webサイト")
    cand_urls = []
    if url_td:
        for a in url_td.find_all("a", href=True):
            cand_urls.append(a["href"].strip())
        for u in re.findall(r"https?://[^\s<>\"'()（）]+", url_td.get_text(" ")):
            if u not in cand_urls:
                cand_urls.append(u)
    for u in cand_urls:
        c = classify_url(u)
        if c == "official" and website is None:
            website = u
        elif c == "sns" and u not in sns:
            sns.append(u)
        elif c == "portal":
            portals.append(u)
    # 本文中（最新釣果の埋め込み等）の SNS
    choka_links = []
    if pc:
        in_choka = False
        for el in pc.find_all(["h2", "h3", "a"]):
            if el.name in ("h2", "h3"):
                in_choka = "釣果" in el.get_text()
                continue
            href = (el.get("href") or "").strip()
            c = classify_url(href)
            if c == "sns":
                if href not in sns:
                    sns.append(href)
            elif in_choka and c == "official" and "a8.net" not in href:
                choka_links.append(href)
    website_from_choka = False
    if website is None and choka_links:
        website = choka_links[0]
        website_from_choka = True

    price_td = row("料金", "料金表", "料金・時間")
    plans, schedule_text, price_lines = ([], "", [])
    if price_td is not None:
        plans, schedule_text, price_lines = parse_price_cell(price_td, cats, title)

    feat_td = row("特徴")
    facilities = []
    if feat_td is not None:
        for ln in cell_lines(feat_td):
            for it in re.split(r"[、,/]| {2,}", ln):
                it = clean(it)
                if it and len(it) <= 20 and "。" not in it and it not in facilities:
                    facilities.append(it)

    intro = []
    if pc:
        for el in pc.children:
            if getattr(el, "name", None) in ("div", "table") and el.find("table") is not None or \
                    getattr(el, "name", None) == "table":
                break
            if getattr(el, "name", None) == "p":
                t = clean(el.get_text(" "))
                if t:
                    intro.append(t)
    other_text = " ".join(clean(td.get_text(" ")) for k, td in rows.items()
                          if k not in ("名前", "住所", "URL", "電話", "お問い合わせ", "料金", "特徴"))
    all_text = " ".join([title, name, " ".join(price_lines), " ".join(facilities), " ".join(intro), other_text])

    targets = []
    for t in find_fish(title + " " + " ".join(price_lines) + " " + " ".join(intro) + " " + other_text) + \
            [t for p in plans for t in p["targets"]]:
        if t not in targets:
            targets.append(t)
    methods = find_methods(all_text)

    types = []
    if set(cats) & set(TOSEN_CATS):
        types.append("渡船")
    for p in plans:
        if p["kind"] in ("乗合", "仕立", "渡船") and p["kind"] not in types:
            types.append(p["kind"])
    ptxt = " ".join(price_lines)
    if NORI_RE.search(ptxt) and not re.search(r"乗\s*合(?:い)?(?:船)?(?:は|の)?(?:なし|無し|ありません|行って(?:い|お)りません)",
                                               ptxt) and "乗合" not in types:
        types.append("乗合")
    if CH_SECTION_RE.search(ptxt) and "仕立" not in types:
        types.append("仕立")
    if set(cats) & {"レンタルボート", "手漕ぎボート"}:
        types.append("レンタルボート")

    capacity = None
    cm = re.search(r"定員\s*[:：]?\s*(\d{1,3})\s*(?:名|人)", all_text)
    if cm:
        capacity = int(cm.group(1))
    holidays = ""
    hm = re.search(r"(?:定休日|休船日|休業日)\s*[:：は]?\s*([^\s。、]{1,20})", all_text)
    if hm:
        holidays = clean(hm.group(1))
    access = ""
    acc_td = row("アクセス", "交通")
    if acc_td is not None:
        access = clean(acc_td.get_text(" "))[:100]

    lat, lon, span = (None, None, None)
    if pc:
        lat, lon, span = parse_map(pc)
    map_ok = lat is not None and span is not None and span <= MAP_MAX_SPAN and 20 <= lat <= 46.5 and 122 <= lon <= 154.5
    updated = None
    tm = s.find("time", class_="entry-date")
    if tm is not None:
        updated = (tm.get("datetime") or "")[:10] or None

    rec = {
        "src": "tsuriyaro",
        "src_id": slug,
        "src_url": url,
        "name": name,
        "kana": kana,
        "pref": pref,
        "city": city,
        "address": address,
        "port": port,
        "lat": round(lat, 6) if map_ok else None,
        "lon": round(lon, 6) if map_ok else None,
        "tel": tel,
        "website": website,
        "sns": sns,
        "types": types,
        "targets": targets,
        "methods": methods,
        "holidays": holidays,
        "facilities": facilities,
        "capacity": capacity,
        "access": access,
        "description": title_description(title, name),
        "plans": plans,
        "schedule_text": schedule_text,
        "fetched": FETCHED,
        "_cats": cats,
        "_cat_pref": cat_pref,
        "_title": title,
        "_map": [lat, lon, span],
        "_portals": portals,
        "_website_from_choka": website_from_choka,
        "_updated": updated,
        "_has_table": bool(rows),
    }
    return rec


def is_boat(cats, title, name):
    if set(cats) & set(BOAT_CATS):
        return True
    if cats:
        return False
    t = title + " " + name
    if re.search(r"釣り?堀|つり堀|つりぼり|管理釣り?場|レンタルボート|貸し?ボート|釣り公園|釣具", t):
        return False
    return bool(re.search(r"丸|遊漁船|釣り?船|渡船|瀬渡し|チャーター|筏|イカダ|カセ", t))


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


def slug_of(url):
    m = re.match(r"^https?://fishing-station\.jp/([^/?#]+)/?$", url)
    return unquote(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="船宿レコードN件で止める")
    ap.add_argument("--ids", default="", help="slug（例 wakayama-komatsutosen）または詳細URLをカンマ区切りで")
    ap.add_argument("--sample", type=int, default=0, help="県slugごとに分散させてN件の詳細を取る")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--log", default=LOG_PATH, help="ログファイル（サンプル実行は別ファイルに）")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    _log_path[0] = os.path.abspath(args.log)

    for d in (CACHE_DIR, os.path.dirname(_log_path[0]), TMP_DIR, os.path.dirname(os.path.abspath(args.out))):
        if not os.path.isdir(d):
            os.makedirs(d)

    ids = [x.strip() for x in args.ids.split(",") if x.strip()]
    ids = [slug_of(x) or x.strip("/") for x in ids]
    full_run = not (args.limit or ids or args.sample)
    log("START tsuriyaro out=%s limit=%s ids=%d sample=%s" % (args.out, args.limit, len(ids), args.sample))

    # 1) sitemap
    idx = fetch(BASE + "sitemap.xml", args.refresh)
    sitemaps = [u for u in re.findall(r"<loc>([^<]+)</loc>", idx) if re.search(r"/post-sitemap\d*\.xml$", u)]
    if not sitemaps:
        sitemaps = [BASE + "post-sitemap.xml", BASE + "post-sitemap2.xml", BASE + "post-sitemap3.xml"]
    urls = []
    for sm in sitemaps:
        for u in re.findall(r"<loc>([^<]+)</loc>", fetch(sm, args.refresh)):
            u = u.strip()
            if slug_of(u) and u not in urls:
                urls.append(u)
    log("sitemaps=%d detail urls=%d" % (len(sitemaps), len(urls)))

    # 2) 船宿系でない遊び方の一覧（カードに全カテゴリが載る）
    cards = {}
    for cat in NONBOAT_CATS:
        base = BASE + "category2/" + quote(cat).lower() + "/"
        try:
            html = fetch(base, args.refresh)
        except NotFound:
            log("WARN listing not found: %s" % cat)
            continue
        c1, maxpage = parse_listing(html)
        cards.update(c1)
        for pg in range(2, maxpage + 1):
            try:
                cp, _ = parse_listing(fetch(base + "page/%d/" % pg, args.refresh))
                cards.update(cp)
            except NotFound:
                break
            except Exception as e:  # noqa
                log("WARN listing %s page %d: %s" % (cat, pg, e))
        log("listing %s pages=%d cards(total)=%d" % (cat, maxpage, len(cards)))

    excluded = []
    candidates = []
    for u in urls:
        cc = cards.get(u)
        if cc and not (set(cc) & set(BOAT_CATS)):
            excluded.append({"url": u, "cats": cc, "reason": "listing"})
        else:
            candidates.append(u)
    log("listing-excluded=%d candidates=%d" % (len(excluded), len(candidates)))

    if ids:
        by_slug = dict((slug_of(u), u) for u in urls)
        sel = []
        for i in ids:
            u = by_slug.get(i) or (BASE + i + "/")
            sel.append(u)
        candidates = sel
    elif args.sample:
        groups = {}
        order = []
        for u in candidates:
            g = slug_of(u).split("-")[0]
            if g not in groups:
                groups[g] = []
                order.append(g)
            groups[g].append(u)
        order.sort()
        sel = []
        k = 0
        while len(sel) < args.sample and any(len(groups[g]) > k for g in order):
            for g in order:
                if len(groups[g]) > k and len(sel) < args.sample:
                    # 各県の中でも散らす
                    lst = groups[g]
                    sel.append(lst[(k * 7) % len(lst)] if (k * 7) % len(lst) not in [((j * 7) % len(lst)) for j in range(k)] else lst[k])
            k += 1
        candidates = list(dict.fromkeys(sel))

    # 3) 詳細
    records, debug = [], []
    total = len(candidates)
    last_saved = 0
    errors = 0
    for i, u in enumerate(candidates):
        slug = slug_of(u) or u
        try:
            html = fetch(u, args.refresh)
        except NotFound as e:
            log("WARN %s" % e)
            errors += 1
            continue
        except Exception as e:  # noqa
            log("ERROR detail %s: %s" % (u, e))
            errors += 1
            continue
        try:
            rec = parse_detail(html, u, slug)
        except Exception as e:  # noqa
            log("ERROR parse %s: %s" % (u, e))
            errors += 1
            continue
        if not is_boat(rec["_cats"], rec["_title"], rec["name"]):
            excluded.append({"url": u, "cats": rec["_cats"], "reason": "detail", "name": rec["name"]})
        else:
            records.append(rec)
            debug.append({"src_id": slug, "cats": rec["_cats"], "title": rec["_title"], "map": rec["_map"],
                          "portals": rec["_portals"], "website_from_choka": rec["_website_from_choka"],
                          "updated": rec["_updated"], "has_table": rec["_has_table"]})
        if (i + 1) % 20 == 0 or i + 1 == total:
            log("progress %d/%d records=%d excluded=%d errors=%d requests=%d cache=%d" % (
                i + 1, total, len(records), len(excluded), errors, _stats["requests"], _stats["cache_hits"]))
        if len(records) - last_saved >= 100:
            save_json(args.out, [public(x) for x in records])
            last_saved = len(records)
        if args.limit and len(records) >= args.limit:
            break

    save_json(args.out, [public(x) for x in records])
    tag = "full" if full_run else os.path.splitext(os.path.basename(args.out))[0]
    save_json(os.path.join(TMP_DIR, "debug_%s.json" % tag), debug)
    save_json(os.path.join(TMP_DIR, "excluded_%s.json" % tag), excluded)
    log("requests=%d cache_hits=%d errors=%d excluded=%d" % (_stats["requests"], _stats["cache_hits"], errors,
                                                             len(excluded)))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
