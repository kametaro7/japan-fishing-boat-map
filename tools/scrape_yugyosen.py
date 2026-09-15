#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
遊漁船サーチ (https://yugyosen.com/) クローラ

サイト構造（2026-09-15 時点で確認）
- robots.txt は "Disallow:"（空）で全許可。sitemap.xml は県ページ47件と船ページ547件のみ（九州・四国などが欠ける）。
- 県別一覧 /boat/<pref> に、その県の全船へのリンク（/boat/<pref>/c<市>/b<船ID>）がページ送りなしで並ぶ。
  47県の合計がトップの「現在 1144 艘」と一致する → 一覧を正とし、sitemap にだけある船は掲載終了の候補として取得を試みる
  （sitemap だけにある船は、2026-09-15 時点では中身の無いテンプレートページが返る → 出力しない）。
- 船ページ: 名前・かな・県/市/港（パンくず）、設備アイコン（grayscale = なし）、業種（乗合/チャーター）、所在地、エリア、
  電話番号、船長から一言、季節別ターゲット、その他情報（代表者名・最大定員・出港時間・釣り座・支払方法・釣り方・
  駐車場・貸タックル・全長・重量・氷・備考）、プランタブ（一部の船のみ。業種・ターゲット・時期・乗合料金/チャーター料金・
  集合時間・帰港時間）、アクセスタブ（〒・県＋市区町村より下の住所、アクセス文、Google Maps embed の pb=!2d<lon>!3d<lat>）。
- 公式サイト欄は無い。備考・船長から一言・プラン説明などの本文中の URL だけを website/sns に使う。
- 代表者名（個人名）は出力しない。HTML コメント内の旧「料金」欄は非表示なので使わない。
- /api/calls（電話タップ計測の POST）などの API は叩かない。

使い方
  python3 tools/scrape_yugyosen.py                          # 全件
  python3 tools/scrape_yugyosen.py --limit 30
  python3 tools/scrape_yugyosen.py --ids 1033,b226 --out work/sources/yugyosen.sample.json
      （--out が既定以外で --log 未指定なら work/logs/yugyosen.sample.log に出す）
  python3 tools/scrape_yugyosen.py --refresh                # キャッシュを使わず再取得
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
from bs4 import BeautifulSoup, Comment  # noqa: E402

BASE = "https://yugyosen.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "yugyosen")
OUT_DEFAULT = os.path.join(ROOT, "work", "sources", "yugyosen.json")
LOG_PATH = os.path.join(ROOT, "work", "logs", "yugyosen.log")
SAMPLE_LOG_PATH = os.path.join(ROOT, "work", "logs", "yugyosen.sample.log")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒（SPEC: 0.8秒以上）
FETCHED = "2026-09-15"

PREF_SLUGS = [
    ("hokkaido", "北海道"), ("aomori", "青森県"), ("iwate", "岩手県"), ("miyagi", "宮城県"), ("akita", "秋田県"),
    ("yamagata", "山形県"), ("fukushima", "福島県"), ("ibaraki", "茨城県"), ("tochigi", "栃木県"),
    ("gunma", "群馬県"), ("saitama", "埼玉県"), ("chiba", "千葉県"), ("tokyo", "東京都"), ("kanagawa", "神奈川県"),
    ("niigata", "新潟県"), ("toyama", "富山県"), ("ishikawa", "石川県"), ("fukui", "福井県"),
    ("yamanashi", "山梨県"), ("nagano", "長野県"), ("gifu", "岐阜県"), ("shizuoka", "静岡県"), ("aichi", "愛知県"),
    ("mie", "三重県"), ("shiga", "滋賀県"), ("kyoto", "京都府"), ("osaka", "大阪府"), ("hyogo", "兵庫県"),
    ("nara", "奈良県"), ("wakayama", "和歌山県"), ("tottori", "鳥取県"), ("shimane", "島根県"),
    ("okayama", "岡山県"), ("hiroshima", "広島県"), ("yamaguchi", "山口県"), ("tokushima", "徳島県"),
    ("kagawa", "香川県"), ("ehime", "愛媛県"), ("kochi", "高知県"), ("fukuoka", "福岡県"), ("saga", "佐賀県"),
    ("nagasaki", "長崎県"), ("kumamoto", "熊本県"), ("oita", "大分県"), ("miyazaki", "宮崎県"),
    ("kagoshima", "鹿児島県"), ("okinawa", "沖縄県"),
]
SLUG_PREF = dict(PREF_SLUGS)
PREFS = [p for _, p in PREF_SLUGS]

BOAT_PATH_RE = re.compile(r"^/boat/([a-z]+)/c(\d+)/b(\d+)$")

# 船宿（遊漁船）ではないと判断する名前（筏・釣り堀・貸しボート店など）
NON_BOAT_NAME_RE = re.compile(r"筏|いかだ|イカダ|釣り?堀|海上釣堀|管理釣|釣り公園|レンタルボート|貸し?ボート")

SNS_HOST_RE = re.compile(r"(^|\.)(facebook\.com|fb\.com|fb\.me|instagram\.com|twitter\.com|x\.com|youtube\.com|"
                         r"youtu\.be|tiktok\.com|line\.me|lin\.ee|threads\.net)$")
# 公式サイトとしては扱わない（掲載サイト自身・地図・予約/掲載ポータル・予約SaaS・短縮URL）
NOT_OFFICIAL_HOST_RE = re.compile(
    r"(^|\.)(yugyosen\.com|google\.com|google\.co\.jp|goo\.gl|g\.page|maps\.app\.goo\.gl|yahoo\.co\.jp|"
    r"chowari\.jp|tsuree\.jp|theboat\.jp|castingnet\.jp|funaduri\.jp|fishing-v\.jp|tsurimaru\.jp|gyogyo\.jp|"
    r"point-i\.jp|gurenavi\.jp|anglers\.jp|asoview\.com|jalan\.net|activityjapan\.com|rakuten\.co\.jp|"
    r"airtrip\.jp|veltra\.com|coubic\.com|airrsv\.net|reserva\.be|stores\.jp|select-type\.com|"
    r"hotpepper\.jp|bit\.ly|t\.co|amzn\.to|apple\.com|tsuri-navi\.jp|fishing-station\.jp|tsurisoku\.com)$")

_stats = {"net": 0, "cache": 0}
_last_req = [0.0]
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


def fetch(url, refresh=False):
    """キャッシュ優先。1ホスト直列・間隔 MIN_INTERVAL 秒・429/503 は指数バックオフ（最大5回）。"""
    p = cache_path(url)
    if not refresh and os.path.exists(p) and os.path.getsize(p) > 0:
        _stats["cache"] += 1
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
            _stats["net"] += 1
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
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, p)
        return body.decode("utf-8", "replace")
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------- 文字列ヘルパ

def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def clean(s):
    return re.sub(r"\s+", " ", nfkc(s)).strip()


def uniq(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def kata2hira(s):
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in s)


def host_of(url):
    m = re.match(r"^https?://([^/:?#]+)", url or "", re.I)
    if not m:
        return ""
    h = m.group(1).lower()
    return h[4:] if h.startswith("www.") else h


URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


def urls_in(text):
    out = []
    for m in URL_RE.finditer(nfkc(text)):
        u = m.group(0).rstrip(".,;:)!'")
        if len(u) > 10 and host_of(u):
            out.append(u)
    return out


def norm_tel(s):
    s = nfkc(s)
    s = re.sub(r"[‐‑–—―ー−－]", "-", s)
    m = re.search(r"(?<!\d)0\d{1,4}-\d{1,4}-\d{3,4}(?!\d)", s)
    if m and len(re.sub(r"\D", "", m.group(0))) in (10, 11):
        return m.group(0)
    for m in re.finditer(r"(?<!\d)0\d{9,10}(?!\d)", s.replace("-", "")):
        return m.group(0)
    return None


def to_int(s):
    m = re.search(r"\d+", nfkc(s).replace(",", ""))
    return int(m.group(0)) if m else None


def parse_price(s):
    """'13,000円/人' → 13000"""
    s = nfkc(s)
    m = re.search(r"(\d{1,3}(?:[,.]\d{3})+|\d+)\s*円", s)
    if not m:
        return None
    v = int(re.sub(r"[,.]", "", m.group(1)))
    return v if 500 <= v <= 2000000 else None


def hhmm_tokens(text):
    """文字列中の時刻 → [(start, end, 'HH:MM')]。午前/午後・時半・時分 に対応。"""
    s = nfkc(text)
    toks = []
    pat = re.compile(r"(午前|午後|AM|PM|am|pm)?\s*(\d{1,2})\s*(?::(\d{2})|時\s*(半|(\d{1,2})\s*分)?)(?!間)")
    for m in pat.finditer(s):
        h = int(m.group(2))
        mi = 0
        if m.group(3):
            mi = int(m.group(3))
        elif m.group(4) == "半":
            mi = 30
        elif m.group(5):
            mi = int(m.group(5))
        ap = m.group(1) or ""
        if ap in ("午後", "PM", "pm") and h < 12:
            h += 12
        if h > 23 or mi > 59:
            continue
        toks.append((m.start(), m.end(), "%02d:%02d" % (h, mi)))
    return toks


def single_time(text):
    """時刻が1つだけ（範囲でない）なら 'HH:MM'。"""
    toks = hhmm_tokens(text)
    if len(toks) != 1:
        return None
    return toks[0][2]


RANGE_BETWEEN_RE = re.compile(r"^\s*(?:[〜～~\-−ー－]|から|より|to)\s*(?:午前|午後|AM|PM)?\s*$")
RETURN_WORD_RE = re.compile(r"帰港|帰着|沖上が?り|戻り|終了|着$")
LABEL_RE = re.compile(r"(午前便|午後便|夕便|夜便|午前船|午後船|夕方便|夜船|ナイトリレー|ナイト便?|ナイト船|早朝便|半日便|1日便|一日便|ショート便|定期便)")
SEASON_RE = re.compile(r"(?:\d{1,2}\s*[~〜～\-]\s*)?\d{1,2}\s*月(?:\s*[・,、]\s*(?:\d{1,2}\s*[~〜～\-]\s*)?\d{1,2}\s*月)*(?:\s*[~〜～]\s*\d{1,2}\s*月)?|(?:春|夏|秋|冬)(?:\s*[、・,]\s*(?:春|夏|秋|冬))*|通年")


def parse_departs(text):
    """出港時間の記述 → (departs[(label, 'HH:MM', season)], return 'HH:MM' or None)。
    範囲（4:00〜7:00・4時〜6時 など、季節で変わる幅）は出船時刻の列挙ではないので使わない。
    「6~7月:5時頃/11~3月:7時頃」のような区切りごとの季節は season に入れる。"""
    s = nfkc(text)
    toks = hhmm_tokens(s)
    if not toks:
        return [], None
    for a, b in zip(toks, toks[1:]):
        if RANGE_BETWEEN_RE.match(s[a[1]:b[0]]):
            return [], None
    departs, ret = [], None
    for i, (st, en, t) in enumerate(toks):
        nxt = toks[i + 1][0] if i + 1 < len(toks) else len(s)
        prv = toks[i - 1][1] if i > 0 else 0
        after = s[en:nxt]
        before = s[prv:st]
        # 「17~18時頃」の 18時（範囲の終端）は出船時刻にしない
        if re.search(r"\d{1,2}\s*[~〜～\-]\s*$", s[max(0, st - 6):st]):
            continue
        # 「6:00~12:00」の範囲の始端
        if re.match(r"^\s*[~〜～\-]\s*\d", after):
            continue
        if RETURN_WORD_RE.search(after[:6]) or re.search(r"(帰港|沖上が?り|戻り)\s*[:：]?\s*$", before):
            ret = t
            continue
        seg_start = max(s.rfind("/", 0, st), s.rfind("／", 0, st), s.rfind(" ", 0, st) if "/" not in s else -1)
        seg = s[seg_start + 1:st] if seg_start >= prv else before
        lm = LABEL_RE.search(seg)
        sm = SEASON_RE.search(seg)
        departs.append((lm.group(1) if lm else "", t, clean(sm.group(0)) if sm else ""))
    seen, out = set(), []
    for lab, t, se in departs:
        if (t, se) not in seen:
            seen.add((t, se))
            out.append((lab, t, se))
    if len(out) > 8:
        return [], None
    return out, (ret if len(out) == 1 else None)


def split_list(s):
    s = nfkc(s)
    parts = re.split(r"[、,，・/／\n]+", s)
    out = []
    for p in parts:
        p = re.sub(r"(など|等|他)$", "", p.strip()).strip()
        if not p or re.search(r"任せ|各種|ご希望|ご相談|要相談|お問い?合", p) or len(p) > 25:
            continue
        out.append(p)
    return uniq(out)


def short_text(s, limit=100):
    s = re.sub(r"https?://\S+", "", nfkc(s))
    s = re.sub(r"[ \t　]+", " ", s)
    sents = [x.strip() for x in re.split(r"(?<=[。！!？?])|\n+", s) if x and x.strip()]
    out = ""
    for x in sents:
        cand = (out + ("" if not out or re.search(r"[。！!？?]$", out) else " ") + x) if out else x
        if len(cand) > limit:
            break
        out = cand
    if not out and sents:
        out = sents[0][:limit - 1] + "…"
    return out.strip()


# ---------------------------------------------------------------- 一覧

def parse_pref_list(html, slug):
    """県別一覧 → {bid: {path, name, city, port}}"""
    s = BeautifulSoup(html, "lxml")
    out = {}
    for a in s.find_all("a", href=True):
        m = BOAT_PATH_RE.match(a["href"])
        if not m or m.group(1) != slug:
            continue
        bid = m.group(3)
        name = clean(a.get_text())
        name = re.sub(r"の詳細を見る$", "", name)
        e = out.setdefault(bid, {"path": a["href"], "name": ""})
        if name and not e["name"]:
            e["name"] = name
    return out


def parse_sitemap_boats(xml):
    out = {}
    for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        path = re.sub(r"^https?://(www\.)?yugyosen\.com", "", u)
        m = BOAT_PATH_RE.match(path)
        if m:
            out[m.group(3)] = path
    return out


# ---------------------------------------------------------------- 詳細

def dl_pairs(root, dl_selector):
    pairs = []
    for dl in root.select(dl_selector):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue
        pairs.append((clean(dt.get_text()), dd.get_text("\n").strip()))
    return pairs


def parse_latlon(soup):
    ac = soup.find(id="Access")
    srcs = []
    if ac:
        srcs = [f.get("src") or "" for f in ac.find_all("iframe")]
    for src in srcs:
        la = lo = None
        m = re.search(r"!2d(-?\d+\.\d+)!3d(-?\d+\.\d+)", src)
        if m:
            lo, la = float(m.group(1)), float(m.group(2))
        else:
            m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", src) or \
                re.search(r"[?&](?:q|ll|center)=(-?\d+\.\d+),\s*(-?\d+\.\d+)", src) or \
                re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", src)
            if m:
                la, lo = float(m.group(1)), float(m.group(2))
        if la is not None and 20.0 <= la <= 46.5 and 122.0 <= lo <= 154.5:
            return round(la, 7), round(lo, 7)
    return None, None


def build_address(pref, city, rest):
    rest = clean(rest).replace(" ", "")
    if not rest:
        return None
    if pref and rest.startswith(pref):
        return rest
    if city and (rest.startswith(city) or city in rest[:len(city) + 8]):
        return (pref or "") + rest
    if re.match(r"^.{1,5}郡", rest):
        return (pref or "") + rest
    return (pref or "") + (city or "") + rest


def classify_links(texts):
    website, sns = None, []
    for t in texts:
        for u in urls_in(t):
            h = host_of(u)
            if SNS_HOST_RE.search(h):
                if u not in sns:
                    sns.append(u)
            elif NOT_OFFICIAL_HOST_RE.search(h):
                continue
            elif website is None:
                website = u
    return website, sns


def kind_of(s):
    s = nfkc(s)
    ks = []
    for part in re.split(r"[、,，/・\s]+", s):
        if not part:
            continue
        if re.search(r"乗合|乗り合い|乗合い", part):
            ks.append("乗合")
        elif re.search(r"チャーター|仕立|貸切|貸し切り", part):
            ks.append("仕立")
        elif "渡船" in part or "瀬渡" in part:
            ks.append("渡船")
    return uniq(ks)


def parse_plans(soup, src_url):
    pl = soup.find(id="Plan")
    if not pl:
        return []
    plans = []
    modals = pl.select("div.main-tab-plan-modal")
    summaries = []
    inner = pl.find(class_="main-tab-plan-inner")
    if inner:
        for blk in inner.find_all("div", recursive=False):
            h3 = blk.find("h3")
            if h3:
                summaries.append((clean(h3.get_text()), dict(dl_pairs(blk, "dl.main-tab-plan-item"))))
    items = []
    for i, m in enumerate(modals):
        nm = m.find(class_="plan-name")
        pairs = dict(dl_pairs(m, "dl.main-tab-plan-modal-content-item"))
        name = clean(nm.get_text()) if nm else (summaries[i][0] if i < len(summaries) else "")
        items.append((name, pairs))
    if not items:
        items = summaries
    for name, f in items:
        kinds = kind_of(f.get("業種", ""))
        targets = split_list(f.get("ターゲット", ""))
        season = clean(f.get("時期", ""))
        meet_raw = clean(f.get("集合時間", ""))
        ret_raw = clean(f.get("帰港時間", ""))
        p_nori = parse_price(f.get("乗合料金", ""))
        p_char = parse_price(f.get("チャーター料金", ""))
        summ = clean(f.get("料金", ""))
        if p_nori is None and p_char is None and summ:
            m1 = re.search(r"乗り?合い?\s*[:：]\s*([\d,.]+\s*円[^\s]*)", summ)
            m2 = re.search(r"チャーター\s*[:：]\s*([\d,.]+\s*円[^\s]*)", summ)
            p_nori = parse_price(m1.group(1)) if m1 else None
            p_char = parse_price(m2.group(1)) if m2 else None

        # 午前便/午後便 の書き分け（例: 「午前便/04:00 午後便/14:00」）
        sessions = []
        labs = LABEL_RE.findall(meet_raw)
        if len(labs) >= 2:
            for lab in labs:
                mm = re.search(re.escape(lab) + r"\s*[/／:：]?\s*([^\s午夕夜]+)", meet_raw)
                rr = re.search(re.escape(lab) + r"\s*[/／:：]?\s*([^\s午夕夜]+)", ret_raw)
                sessions.append((lab, mm.group(1) if mm else "", rr.group(1) if rr else ""))
        else:
            sessions.append(("", meet_raw, ret_raw))

        variants = []
        if p_nori is not None:
            variants.append(("乗合", p_nori, "%s円/人" % format(p_nori, ",")))
        if p_char is not None:
            variants.append(("仕立", p_char, "1隻%s円（チャーター）" % format(p_char, ",")))
        if not variants:
            k = kinds[0] if len(kinds) == 1 else None
            variants.append((k, None, ""))

        for lab, meet, ret in sessions:
            meet_t = single_time(meet)
            ret_t = single_time(ret)
            if ret_t and meet_t and ret_t <= meet_t:
                ret_t = None  # 「3時半」など午後の省略表記は判断できない
            for kind, price, ptxt in variants:
                pname = name
                if lab:
                    pname = "%s（%s）" % (name, lab)
                plans.append({
                    "name": pname,
                    "kind": kind,
                    "targets": targets,
                    "price": price,
                    "price_text": ptxt,
                    "depart": None,
                    "return": ret_t,
                    "meet": meet,
                    "season": season,
                    "days": "",
                    "includes": "",
                    "url": src_url,
                })
    return plans


NEG_RE = re.compile(r"なし|無し|ありません|していません|しておりません|不可|持参|各自|^無$|^無し|ご用意くださ|ご用意下さ")


def parse_boat(html, path):
    soup = BeautifulSoup(html, "lxml")
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    name_el = soup.find(class_="mainBoatName")
    if not name_el or not clean(name_el.get_text()):
        return None
    m = BOAT_PATH_RE.match(path)
    slug, bid = m.group(1), m.group(3)
    src_url = BASE + path
    name = clean(name_el.get_text())
    kana_el = soup.find(class_="mainBoatKana")
    kana = kata2hira(clean(kana_el.get_text())) if kana_el else ""

    pref = SLUG_PREF.get(slug)
    city = port = None
    pn = soup.find(class_="mainPortName")
    if pn:
        for a in pn.find_all("a", href=True):
            h = a["href"]
            t = clean(a.get_text())
            if re.match(r"^/boat/[a-z]+$", h):
                if t in PREFS:
                    pref = t
            elif re.match(r"^/boat/[a-z]+/c\d+$", h):
                city = t or None
            elif re.match(r"^/boat/[a-z]+/c\d+/p\d+$", h):
                port = t or None

    facilities = []
    for dl in soup.select("dl.main-summary-facility"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt:
            continue
        style = (dt.get("style") or "") + ((dd.get("style") or "") if dd else "")
        if "grayscale" in style:
            continue
        t = clean(dt.get_text())
        if t:
            facilities.append(t)

    info = dict((k, clean(v)) for k, v in dl_pairs(soup, "dl.main-summary-information-item"))
    other = dict(dl_pairs(soup, "dl.main-tab-information-other-item"))
    types = kind_of(info.get("業種", ""))
    tel = norm_tel(info.get("電話番号", ""))
    area = info.get("エリア", "")
    if not city:
        loc = info.get("所在地", "")
        if pref and loc.startswith(pref):
            city = loc[len(pref):] or None

    # 季節別ターゲット
    seasons = []
    for dl in soup.select("dl.main-tab-information-target-item"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if not dt or not dd:
            continue
        se = clean(dt.get_text())
        fish = split_list(dd.get_text())
        if se and fish:
            seasons.append((se, fish))
    targets = uniq([f for _, fs in seasons for f in fs])

    capacity = to_int(other.get("最大定員", ""))
    depart_raw = clean(other.get("出港時間", ""))
    methods = split_list(other.get("釣り方", "")) if other.get("釣り方") else []
    methods = uniq([re.sub(r"\s*\(.*?\)\s*$", "", x) if x.startswith("SLJ") else x for x in methods])
    parking = clean(other.get("駐車場", ""))
    rental = clean(other.get("貸タックル", ""))
    ice = clean(other.get("氷について", ""))
    note = other.get("備考", "")
    msg_el = soup.find(class_=re.compile(r"captainMessageInner"))
    msg = msg_el.get_text("\n").strip() if msg_el else ""

    if rental and not NEG_RE.search(rental):
        facilities.append("レンタルタックル")
    if parking and not re.search(r"なし|無し|ありません|^無$", parking):
        facilities.append("駐車場")
    facilities = uniq(facilities)

    # 住所・アクセス
    address, access_txt = None, ""
    ac = soup.find(id="Access")
    if ac:
        ps = ac.find_all("p", class_="main-tab-access-explain")
        if ps:
            raw = ps[0].get_text("\n")
            raw = re.sub(r"〒\s*\d{3}-?\d{4}", "", nfkc(raw)).replace("〒", "")
            raw = clean(raw)
            if pref and raw.startswith(pref):
                raw = raw[len(pref):]
            address = build_address(pref, city, raw)
        pres = [clean(x.get_text(" ")) for x in ac.find_all("pre")]
        access_txt = " ".join(x for x in pres if x)
    if parking:
        access_txt = (access_txt + " 駐車場: " + parking).strip()
    access_txt = access_txt[:200]
    lat, lon = parse_latlon(soup)

    plans = parse_plans(soup, src_url)
    for p in plans:
        targets = uniq(targets + p["targets"])
        if p["kind"] and p["kind"] not in types:
            types.append(p["kind"])

    # 出港時間 → 出船時刻ごとの plan（料金なし）
    departs, dep_ret = parse_departs(depart_raw) if depart_raw else ([], None)
    if departs and not any(p.get("depart") for p in plans):
        kind = types[0] if len(types) == 1 else None
        for lab, t, se in departs:
            hm = "%d:%s" % (int(t[:2]), t[3:])
            inner = "%s%s出船%s" % (lab + " " if lab else "", hm, " " + se if se else "")
            if kind:
                pname = "%s（%s）" % (kind, inner)
            elif types:
                pname = "%s（%s）" % ("/".join(types), inner)
            else:
                pname = inner
            plans.append({
                "name": pname, "kind": kind, "targets": [], "price": None, "price_text": "",
                "depart": t, "return": dep_ret, "meet": "", "season": se, "days": "", "includes": "",
                "url": src_url,
            })

    # schedule_text（出港時間・季節別ターゲットを短く）
    parts = []
    if depart_raw:
        parts.append("出港時間: " + depart_raw[:60])
    if seasons:
        ss = []
        for se, fs in seasons:
            ss.append("%s: %s%s" % (se, "、".join(fs[:5]), "など" if len(fs) > 5 else ""))
        parts.append("季節の対象魚 " + " / ".join(ss))
    schedule_text = "。".join(parts)[:300]

    website, sns = classify_links([note, msg, access_txt] +
                                  [x.get_text("\n") for x in soup.select("#Plan .plan-description, #Plan .main-tab-plan-modal-content-note")] +
                                  [v for m in soup.select("#Plan div.main-tab-plan-modal")
                                   for k, v in dl_pairs(m, "dl.main-tab-plan-modal-content-item") if k in ("予約方法", "備考")])

    desc = short_text(msg, 100)
    if not desc and area:
        desc = ("エリア: " + area)[:100]

    rec = {
        "src": "yugyosen",
        "src_id": bid,
        "src_url": src_url,
        "name": name,
        "kana": kana,
        "pref": pref,
        "city": city,
        "address": address,
        "port": port,
        "lat": lat, "lon": lon,
        "tel": tel,
        "website": website,
        "sns": sns,
        "types": types,
        "targets": targets,
        "methods": methods,
        "holidays": "",
        "facilities": facilities,
        "capacity": capacity,
        "access": access_txt,
        "description": desc,
        "plans": plans,
        "schedule_text": schedule_text,
        "fetched": FETCHED,
        # 以下は内部用（出力しない）
        "_area": area,
        "_ice": ice,
        "_rental": rental,
    }
    return rec


# ---------------------------------------------------------------- 出力

PUBLIC_KEYS = ["src", "src_id", "src_url", "name", "kana", "pref", "city", "address", "port",
               "lat", "lon", "tel", "website", "sns", "types", "targets", "methods", "holidays",
               "facilities", "capacity", "access", "description", "plans", "schedule_text",
               "fetched", "stale"]


def public(rec):
    return dict((k, rec.get(k)) for k in PUBLIC_KEYS if k in rec)


def save_json(path, data):
    d = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def dedupe_shared_coords(records):
    """別の市区町村の船と全く同じ座標（地図の貼り間違い・既定値の疑い）は null にする。"""
    by = {}
    for r in records:
        if r.get("lat") is not None:
            by.setdefault((round(r["lat"], 5), round(r["lon"], 5)), []).append(r)
    n = 0
    for k, rs in by.items():
        cities = set((r.get("pref"), r.get("city")) for r in rs)
        if len(rs) >= 2 and len(cities) >= 2:
            for r in rs:
                r["lat"] = r["lon"] = None
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="先頭N隻だけ")
    ap.add_argument("--ids", default="", help="船ID（1033 / b1033）をカンマ区切りで")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--log", default="", help="ログの出力先（既定: 全件は yugyosen.log、--out 指定時は yugyosen.sample.log）")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    global LOG_PATH
    out_abs = os.path.abspath(args.out)
    if args.log:
        LOG_PATH = os.path.abspath(args.log)
    elif out_abs != os.path.abspath(OUT_DEFAULT):
        LOG_PATH = SAMPLE_LOG_PATH
    args.out = out_abs

    for d in (CACHE_DIR, os.path.dirname(LOG_PATH)):
        if not os.path.isdir(d):
            os.makedirs(d)
    ids = [re.sub(r"^b", "", x.strip()) for x in args.ids.split(",") if x.strip()]
    log("START yugyosen out=%s limit=%s ids=%d refresh=%s" % (args.out, args.limit, len(ids), args.refresh))

    # 1) 列挙: 県別一覧（正）＋ sitemap（掲載終了候補）
    listed = {}
    for slug, pref in PREF_SLUGS:
        try:
            html = fetch(BASE + "/boat/" + slug, args.refresh)
        except Exception as e:  # noqa
            log("ERROR list %s: %s" % (slug, e))
            continue
        got = parse_pref_list(html, slug)
        for bid, e in got.items():
            listed.setdefault(bid, e)
    try:
        sm = parse_sitemap_boats(fetch(BASE + "/sitemap.xml", args.refresh))
    except Exception as e:  # noqa
        log("WARN sitemap: %s" % e)
        sm = {}
    sm_only = dict((b, p) for b, p in sm.items() if b not in listed)
    log("enumerate: listed=%d sitemap=%d sitemap_only=%d" % (len(listed), len(sm), len(sm_only)))

    targets = [(b, e["path"], False) for b, e in listed.items()] + [(b, p, True) for b, p in sm_only.items()]
    targets.sort(key=lambda x: (PREFS.index(SLUG_PREF.get(BOAT_PATH_RE.match(x[1]).group(1), "沖縄県")), int(x[0])))
    if ids:
        targets = [t for t in targets if t[0] in ids]
        missing = [i for i in ids if i not in set(t[0] for t in targets)]
        if missing:
            log("WARN ids not found in lists/sitemap: %s" % ",".join(missing))
    if args.limit:
        targets = targets[:args.limit]
    total = len(targets)
    log("targets=%d" % total)

    records, gone, excluded, errors = [], [], [], []
    last_saved = 0
    t0 = time.time()
    for i, (bid, path, is_sm_only) in enumerate(targets, 1):
        url = BASE + path
        try:
            html = fetch(url, args.refresh)
            rec = parse_boat(html, path)
        except Exception as e:  # noqa
            log("ERROR %s: %s" % (url, e))
            errors.append(bid)
            rec = None
        if rec is None:
            if bid not in errors:
                gone.append(bid)
                log("GONE (empty page) %s%s" % (url, " [sitemap only]" if is_sm_only else ""))
        elif NON_BOAT_NAME_RE.search(rec["name"]):
            excluded.append((bid, rec["name"]))
            log("EXCLUDE non-boat %s %s" % (url, rec["name"]))
        else:
            if is_sm_only:
                rec["stale"] = True
            records.append(rec)
        if i % 10 == 0 or i == total:
            log("progress %d/%d records=%d plans=%d net=%d cache=%d gone=%d excluded=%d elapsed=%.0fs" % (
                i, total, len(records), sum(len(r["plans"]) for r in records), _stats["net"], _stats["cache"],
                len(gone), len(excluded), time.time() - t0))
        if len(records) - last_saved >= 100:
            save_json(args.out, [public(r) for r in records])
            last_saved = len(records)

    n = dedupe_shared_coords(records)
    if n:
        log("coords shared across different cities -> null: %d" % n)
    save_json(args.out, [public(r) for r in records])
    log("summary: gone=%d excluded=%d errors=%d excluded_names=%s" % (
        len(gone), len(excluded), len(errors), ";".join("%s:%s" % x for x in excluded)))
    log("requests=%d cache_hits=%d" % (_stats["net"], _stats["cache"]))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
