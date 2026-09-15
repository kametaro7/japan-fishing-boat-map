#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自治体・組合などの「代替一覧」クローラ（src="regalt"）

都道府県が遊漁船業者登録簿を公表していない県について、代わりに使える公開一覧
（組合の会員名簿・漁協/観光協会の釣り船一覧など）を取り込む。
候補は work/discovery/registry_alt_sources.json（19件）＋東京湾遊漁船業協同組合。
取り込まなかった URL と理由は work/discovery/regalt.md。

取り込む一覧（2026-09-15 時点で確認）
  03 岩手県遊漁船業協会「会員紹介」 area/kuji|miyako|kamaishi|ofunato（表）＋会員詳細ページ
       表: 船名（船主名）/住所/電話/HP・SNS/水洗トイレ/バッテリー電源/魚群探知機/レンタル品
       詳細: 登録番号・船名・読み・市町村・船の大きさ・定員・連絡先・主な釣り魚・主な漁場・
             乗船場所（Google マップ埋め込みの座標つき）・料金・設備・HP/SNS
       「代表者名」「船主」・表の括弧内（船主名）は個人名なので読まない。
  13/14/12 東京湾遊漁船業協同組合「釣り船、屋形船を探す」 /shop/（17店。屋形船だけの店は除く）
  13 小笠原村観光協会「釣り」（3事業者。乗合/チャーター料金・定員）＋各事業者ページ（HP/SNS）
  19 山中湖漁協「遊漁券販売・ボート貸出店」のうち「ドーム船」取扱店（貸しボート/遊漁券のみの店は除く）
  20 野尻湖漁協「わかさぎ釣り屋形船店一覧（釣り船組合）」
  24 三重県「来県延期協力金 協力事業者一覧（遊漁船）」令和2年8月3日現在 PDF → 全件 stale=true
       （個人名が営業所名になっている行は出力しない）
  35 周防大島観光協会「釣り・フィッシング」カテゴリのうち遊漁船・釣り体験の船（キャンプ場等は除く）
  43 天草Webの駅「釣り船特集」の釣り船一覧（各船の MyHp ページ・お問い合わせページから住所/電話）

src_id = "<県コード>:<一覧の短い名前>:<一覧での行番号>"、src_url = 一覧の URL。

使い方
  python3 tools/scrape_regalt.py                       # 全一覧
  python3 tools/scrape_regalt.py --only iwate,tokyowan --out work/sources/regalt.sample.json
  --refresh でキャッシュを無視して再取得、--log でログの出力先を変更
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
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (PREFS, PREF_CODE, nfkc, tel_display, norm_tel, host_of, host_in,  # noqa: E402
                    clean_address, NOT_OFFICIAL_HOSTS, SNS_HOSTS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "regalt")
OUT_DEFAULT = os.path.join(ROOT, "work", "sources", "regalt.json")
LOG_DEFAULT = os.path.join(ROOT, "work", "logs", "regalt.log")
SUMMARY_PATH = os.path.join(ROOT, "work", "logs", "regalt.summary.json")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒（SPEC: 0.8秒以上、1ホスト直列）
FETCHED = "2026-09-15"

# robots.txt（2026-09-15 取得）の Disallow。対象ページはどれも該当しない。
DISALLOW = {
    "iwate-yuugyosen.net": ("/__kanri/wp-admin/",),
    "www.ogasawaramura.com": ("/prod/wp-admin/",),
    "suo-oshima-kanko.net": ("/wp/wp-admin/",),
    "nojiriko-gyokyo.com": ("/wp-admin/",),
    "www.pref.mie.lg.jp": ("/TOPICS/200809027610.pdf", "/a/"),
}

PORTAL_HOSTS = tuple(NOT_OFFICIAL_HOSTS) + (
    "yamanakako.info", "iwate-yuugyosen.net", "ogasawaramura.com", "suo-oshima-kanko.net",
    "nojiriko-gyokyo.com", "lake-yamanaka.net", "tokyowan-yugyosen.or.jp",
)

PUBLIC_KEYS = ["src", "src_id", "src_url", "name", "kana", "pref", "city", "address", "port", "lat", "lon",
               "tel", "website", "sns", "types", "targets", "methods", "holidays", "facilities", "capacity",
               "access", "description", "plans", "schedule_text", "fetched"]

DESIGNATED = ("札幌市", "仙台市", "さいたま市", "千葉市", "横浜市", "川崎市", "相模原市", "新潟市", "静岡市",
              "浜松市", "名古屋市", "京都市", "大阪市", "堺市", "神戸市", "岡山市", "広島市", "北九州市",
              "福岡市", "熊本市")

MIE_MUNIS = ("四日市市", "志摩市", "尾鷲市", "大紀町", "鳥羽市", "紀北町", "南伊勢町", "鈴鹿市", "熊野市",
             "伊勢市", "明和町", "津市", "松阪市", "桑名市", "御浜町", "紀宝町", "度会町", "川越町", "木曽岬町",
             "玉城町", "多気町", "大台町", "伊賀市", "名張市", "亀山市", "いなべ市", "菰野町", "朝日町", "東員町")

METHOD_WORDS = ("タイラバ", "鯛ラバ", "ジギング", "SLJ", "スーパーライトジギング", "ティップラン", "エギング",
                "イカメタル", "キャスティング", "泳がせ", "落とし込み", "五目", "テンヤ", "胴突き", "サビキ",
                "トローリング", "バチコン", "ルアー", "フライ", "餌釣り", "エサ釣り")
FISH_WORDS = ("マダイ", "真鯛", "ブリ", "ハマチ", "カンパチ", "ヒラマサ", "ヒラメ", "シイラ", "サワラ", "アオリイカ",
              "タチウオ", "アジ", "サバ", "カワハギ", "メバル", "カサゴ", "アマダイ", "クロムツ", "マグロ", "カツオ",
              "ロウニンアジ", "カスミアジ", "カマス", "ワカサギ", "ソイ", "アイナメ", "カレイ", "タラ")

_last_req = {}
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_stats = {"requests": 0, "cache_hits": 0, "errors": 0}
_log_path = [LOG_DEFAULT]
_person_names = set()  # 出力に紛れ込んでいないか最後に確かめる個人名（ログにも出さない）


def log(msg):
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    d = os.path.dirname(_log_path[0])
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(_log_path[0], "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------- 取得

def cache_path(url):
    ext = os.path.splitext(urlsplit(url).path)[1].lower()
    if ext not in (".pdf", ".xlsx", ".xls"):
        ext = ".html"
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ext)


def fetch(url, refresh=False, binary=False, allow_fail=False):
    """キャッシュ優先。ホストごとに直列・MIN_INTERVAL 秒間隔。429/503 は指数バックオフ（最大5回）。
    allow_fail=True のとき 200 以外は None（.err に記録し、--refresh まで再要求しない）。"""
    parts = urlsplit(url)
    for d in DISALLOW.get(parts.netloc, ()):
        if parts.path.startswith(d):
            raise ValueError("robots.txt Disallow: %s" % url)
    p = cache_path(url)
    err_p = p + ".err"
    if not refresh:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            _stats["cache_hits"] += 1
            with open(p, "rb") as f:
                body = f.read()
            return body if binary else body.decode("utf-8", "replace")
        if allow_fail and os.path.exists(err_p):
            return None
    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    delay = 5.0
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last_req.get(parts.netloc, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_req[parts.netloc] = time.time()
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
            _stats["errors"] += 1
            if allow_fail:
                with open(err_p, "w") as f:
                    f.write("%d %s\n" % (r.status_code, FETCHED))
                log("WARN HTTP %d (skip) %s" % (r.status_code, url))
                return None
            raise RuntimeError("HTTP %d: %s" % (r.status_code, url))
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, p)
        if os.path.exists(err_p):
            os.remove(err_p)
        return r.content if binary else r.content.decode("utf-8", "replace")
    raise RuntimeError("unreachable")


def soup_of(html):
    s = BeautifulSoup(html, "lxml")
    for t in s(["script", "style", "noscript", "svg"]):
        t.decompose()
    return s


# ---------------------------------------------------------------- 正規化ヘルパ

def clean(s):
    s = nfkc(s)
    s = re.sub(r"[​﻿︎️]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def lines_of(node):
    out = []
    for ln in node.get_text("\n").split("\n"):
        ln = clean(ln)
        if ln:
            out.append(ln)
    return out


def new_record(pref, list_short, idx, list_url):
    code = "%02d" % PREF_CODE[pref]
    rec = {k: None for k in PUBLIC_KEYS}
    rec.update({"src": "regalt", "src_id": "%s:%s:%d" % (code, list_short, idx), "src_url": list_url,
                "pref": pref, "sns": [], "types": [], "targets": [], "methods": [], "holidays": "",
                "facilities": [], "access": "", "description": "", "plans": [], "schedule_text": "",
                "fetched": FETCHED})
    return rec


def short(s, n=100):
    s = clean(s)
    return s if len(s) <= n else s[:n - 1] + "…"


def uniq(xs):
    out = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out


def city_of(addr):
    """住所から市区町村（郡は落とす。政令市は区まで）。"""
    a = clean(addr or "")
    for p in PREFS:
        if a.startswith(p):
            a = a[len(p):]
            break
    a = re.sub(r"^[^\s\d]{1,5}?郡", "", a)
    for c in DESIGNATED:
        if a.startswith(c):
            m = re.match(re.escape(c) + r"([^\s\d]{1,4}?区)", a)
            return c + (m.group(1) if m else "")
    m = re.match(r"([^\s\d]{1,6}?[市区町村])", a)
    return m.group(1) if m else None


def personal_fb(u):
    """facebook の個人プロフィール（first.last.NN 形式や profile.php）は出さない。"""
    h = host_of(u)
    if not host_in(h, ("facebook.com", "fb.com", "instagram.com")):  # instagram の first.last 形式も同様
        return False
    path = urlsplit(u).path.strip("/")
    return bool(re.match(r"^[a-z]+\.[a-z]+(\.\d+)?$", path, re.I) or path.startswith("profile.php"))


def pick_links(urls, allow_hosts=()):
    """(website, sns[]) を選ぶ。掲載元・ポータル・地図は website にしない。"""
    website, sns = None, []
    for u in urls:
        u = clean(u)
        if not re.match(r"^https?://", u, re.I):
            continue
        h = host_of(u)
        if host_in(h, SNS_HOSTS):
            if personal_fb(u) or "sharer" in u or "/share" in u:
                continue
            sns.append(u)
            continue
        if host_in(h, allow_hosts):
            website = website or u
            continue
        if host_in(h, PORTAL_HOSTS):
            continue
        website = website or u
    return website, uniq(sns)


def parse_yen(s):
    s = nfkc(s)
    m = re.search(r"(\d{1,3}(?:[,.]\d{3})+|\d+)\s*円", s) or re.search(r"¥\s*(\d{1,3}(?:[,.]\d{3})+|\d+)", s)
    if not m:
        return None
    return int(re.sub(r"[,.]", "", m.group(1)))


def label_before_price(t):
    """「1日¥15,000~」→「1日」、「男性11,000円」→「男性」。価格の前の語（無ければ空）。"""
    t = nfkc(t)
    m = re.search(r"¥\s*\d|\d{1,3}(?:[,.]\d{3})+\s*円|\d{1,3}(?:[,.]\d{3})+|\d+\s*円", t)
    if not m:
        return ""
    return clean(t[:m.start()]).strip(":：~〜/ ")


def join_ordinal(name):
    """「第七 栄福丸」→「第七栄福丸」"""
    return re.sub(r"^(第[一二三四五六七八九十百〇\d]+)\s+", r"\1", name)


TIME_RANGE = re.compile(r"(\d{1,2})\s*:\s*(\d{2})\s*[~〜\-－ー―]\s*(\d{1,2})\s*:\s*(\d{2})")


def time_ranges(s):
    return [("%02d:%s" % (int(a), b), "%02d:%s" % (int(c), d)) for a, b, c, d in TIME_RANGE.findall(nfkc(s))]


def words_in(text, vocab):
    t = nfkc(text or "")
    out = []
    for w in vocab:
        if w in t:
            out.append("マダイ" if w == "真鯛" else ("タイラバ" if w == "鯛ラバ" else w))
    return uniq(out)


def split_list(s):
    s = clean(s)
    s = re.sub(r"(など|等)$", "", s)
    return uniq([x.strip() for x in re.split(r"[、，,・/／\s]+", s) if x.strip()])


# ---------------------------------------------------------------- 03 岩手県遊漁船業協会

IWATE_AREAS = [
    ("iwate-kuji", "https://iwate-yuugyosen.net/area/kuji/", "久慈エリア"),
    ("iwate-miyako", "https://iwate-yuugyosen.net/area/miyako/", "宮古エリア"),
    ("iwate-kamaishi", "https://iwate-yuugyosen.net/area/kamaishi", "釜石エリア"),
    ("iwate-ofunato", "https://iwate-yuugyosen.net/area/ofunato", "大船渡エリア"),
]
IWATE_INDEX = "https://iwate-yuugyosen.net/member/"
IWATE_FLAGS = ["水洗トイレ", "バッテリー電源", "魚群探知機", "レンタル品"]


def iwate_boat_names(raw):
    """表/詳細の船名セル → [船名...]（括弧内＝読み・船主名・隻数は除く）。"""
    s = clean(raw)
    s = re.sub(r"[（(][^（）()]*[）)]", " ", s)
    s = re.sub(r"[（(].*$", "", s)
    s = re.sub(r"\s+\d+(\.\d+)?\s*t.*$", "", s)  # 「広進丸 17t、12名 …」
    parts = [clean(x) for x in re.split(r"\s*(?:、|／|/|｜|\||・)\s*", s)]
    res = []
    for p in parts:
        toks = p.split(" ")
        if len(toks) > 1 and all(t.endswith("丸") for t in toks):  # 「大満丸 第十八大満丸」
            res.extend(toks)
        elif p:
            res.append(join_ordinal(p))
    return uniq(res)


def parse_iwate(refresh, summary):
    out = []
    try:
        idx_html = fetch(IWATE_INDEX, refresh)
        idx_links = uniq([a["href"].rstrip("/") for a in soup_of(idx_html).find_all("a", href=True)
                          if "/member/" in a["href"] and not a["href"].rstrip("/").endswith("/member")])
    except Exception as e:  # noqa
        log("WARN iwate index: %s" % e)
        idx_links = []
    total_rows = 0
    area_links = []
    for list_short, url, area in IWATE_AREAS:
        s = soup_of(fetch(url, refresh))
        table = s.find("table")
        rows = table.find_all("tr")[1:] if table else []
        total_rows += len(rows)
        log("iwate %s: %d rows" % (list_short, len(rows)))
        for i, tr in enumerate(rows, 1):
            tds = tr.find_all("td")
            if len(tds) < 3:
                log("WARN iwate %s row %d: %d cells" % (list_short, i, len(tds)))
                continue
            a = tds[0].find("a", href=True)
            sp = tds[0].find("span")
            if sp:
                raw_owner = clean(sp.get_text(" "))
                for nm in re.split(r"[・※]", raw_owner.strip("（）()")):
                    nm = re.sub(r"\s+", "", nm)
                    if 2 <= len(nm) <= 8 and not re.search(r"[0-9休交換船]", nm):
                        _person_names.add(nm)
                paused = bool(re.search(r"お休み|休業|休止", raw_owner))
                sp.extract()
            else:
                paused = False
            table_names = iwate_boat_names(tds[0].get_text(" "))
            rec = new_record("岩手県", list_short, i, url)
            addr = clean(tds[1].get_text(" "))
            addr = re.sub(r"[（(][\sぁ-ゖー・]+[）)]", "", addr).strip()
            if addr:
                rec["address"] = clean_address("岩手県" + addr if not addr.startswith("岩手県") else addr)
            rec["tel"] = tel_display(tds[2].get_text(" "))
            links = [x["href"] for x in tds[3].find_all("a", href=True)] if len(tds) > 3 else []
            facilities = []
            for j, flag in enumerate(IWATE_FLAGS):
                if len(tds) > 4 + j and "◯" in tds[4 + j].get_text():
                    facilities.append(flag)
            detail = {}
            if a:
                area_links.append(a["href"].rstrip("/"))
                try:
                    detail = parse_iwate_detail(fetch(a["href"], refresh))
                except Exception as e:  # noqa
                    log("WARN iwate detail %s: %s" % (a["href"], e))
            names = detail.get("names") or table_names
            if not names:
                log("WARN iwate %s row %d: no name" % (list_short, i))
                continue
            rec["name"] = names[0]
            rec["kana"] = detail.get("kana") if len(names) == 1 else None
            rec["city"] = detail.get("city") or city_of(rec["address"])
            if not rec["tel"]:
                rec["tel"] = tel_display(detail.get("連絡先", ""))
            rec["port"] = detail.get("port")
            rec["lat"], rec["lon"] = detail.get("lat"), detail.get("lon")
            links += detail.get("links", [])
            rec["website"], rec["sns"] = pick_links(links)
            rec["targets"] = split_list(detail.get("主な釣り魚", ""))
            rec["methods"] = words_in(detail.get("特徴", ""), METHOD_WORDS)
            fac_dd = [x for x in split_list(detail.get("設備", "")) if len(x) <= 12]
            if detail.get("レンタル品") and "レンタル品" not in facilities:
                facilities.append("レンタル品")
            rec["facilities"] = uniq(facilities + fac_dd)
            rec["capacity"] = detail.get("capacity")
            acc = []
            if detail.get("乗船場所"):
                acc.append("乗船場所: " + detail["乗船場所"])
            if detail.get("駐車場所") and detail.get("駐車場所") != detail.get("乗船場所"):
                acc.append("駐車場所: " + detail["駐車場所"])
            rec["access"] = short(" / ".join(acc), 120)
            rec["schedule_text"] = detail.get("schedule_text", "")
            price_txt = detail.get("料金", "")
            if price_txt and "円" in nfkc(price_txt):
                per = bool(re.search(r"1人|一人|お一人|1名|一名|大人", nfkc(price_txt)))
                charter = bool(re.search(r"貸切|チャーター|仕立|1隻|一隻", price_txt))
                rec["plans"].append({
                    "name": "料金", "kind": "仕立" if charter and not per else ("乗合" if "乗合" in price_txt else ""),
                    "targets": [], "price": parse_yen(price_txt) if per else None,
                    "price_text": short(("1隻 " if charter and not per else "") + price_txt, 80),
                    "depart": None, "return": None, "meet": "", "season": "", "days": "", "includes": "",
                    "url": a["href"] if a else None})
            desc = ["岩手県遊漁船業協会 会員（%s）" % area]
            if detail.get("reg_no"):
                desc.append("登録番号 %s" % detail["reg_no"])
            if len(names) > 1:
                desc.append("船: " + "・".join(names))
            if detail.get("船の大きさ"):
                desc.append(detail["船の大きさ"])
            if detail.get("主な漁場"):
                desc.append("漁場: " + detail["主な漁場"])
            if paused:
                desc.append("一覧に一時休業の注記あり")
            rec["description"] = short("。".join(desc))
            out.append(rec)
    missing = [u for u in idx_links if u not in area_links]
    summary["iwate"] = {"list_rows": total_rows, "index_members": len(idx_links),
                        "index_not_in_area_tables": len(missing), "records": len(out)}
    if missing:
        log("WARN iwate: %d members in index but not in area tables" % len(missing))
    return out


def parse_iwate_detail(html):
    s = soup_of(html)
    main = s.find("main") or s.body
    d = {"links": []}
    h4 = None
    for h in main.find_all("h4"):
        if h.find("strong"):
            h4 = h
            break
    if h4:
        spans = h4.find_all("span")
        for sp in spans:
            t = clean(sp.get_text(" "))
            m = re.search(r"登録番号\s*[・:：]?\s*(.+)$", t)
            if m:
                d["reg_no"] = re.sub(r"\s+", "", m.group(1))
            elif re.match(r"^[（(][\sぁ-ゖァ-ヺー・、]+[）)]$", t):
                d["kana"] = re.sub(r"[（()）\s]", "", t)
        d["names"] = iwate_boat_names(h4.find("strong").get_text(" "))
    ul = main.find("ul")
    if ul and ul.find("p"):
        c = clean(ul.find("p").get_text(" "))
        if re.search(r"[市町村]$", c):
            d["city"] = c
    for dl in main.find_all("dl"):
        dt, dd = dl.find("dt"), dl.find("dd")
        if not dt or not dd:
            continue
        key = clean(dt.get_text(" ")).lstrip("■")
        if key in ("代表者名",):
            for nm in re.split(r"[・、]", re.sub(r"[（(].*?[）)]", "", clean(dd.get_text(" ")))):
                nm = re.sub(r"\s+", "", nm)
                if 2 <= len(nm) <= 8:
                    _person_names.add(nm)
            continue
        ifr = dd.find("iframe")
        if ifr and key == "乗船場所":
            m = re.search(r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)", ifr.get("src", ""))
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
                if 38.0 < lat < 41.0 and 140.5 < lon < 142.5:
                    d["lat"], d["lon"] = round(lat, 6), round(lon, 6)
        if key in ("HP/SNS", "HP／SNS"):  # clean() は NFKC なので「／」→「/」
            d["links"] = [x["href"] for x in dd.find_all("a", href=True)]
            continue
        for x in dd.find_all(["div", "iframe"]):
            x.extract()
        d[key] = clean(dd.get_text(" "))
    cap = [int(x) for x in re.findall(r"(\d+)\s*名", nfkc(d.get("定員", "")))]
    d["capacity"] = max(cap) if cap else None
    board = d.get("乗船場所", "")
    m = re.search(r"([^\s、,，（()）:：]{1,12}?(?:漁港|港))", nfkc(board))
    port = m.group(1) if m else None
    if port:
        rest = re.sub(r"^.*(?:[市町村郡]|字)", "", port)  # 「大船渡市三陸町吉浜字根白漁港」→「根白漁港」
        if re.match(r"^.+(?:漁港|港)$", rest) and not re.match(r"^漁?港$", rest):
            port = rest
    d["port"] = port
    m = re.search(r"出船時間\s*[:：]?\s*(.+)$", board)
    if m:
        d["schedule_text"] = short("出船時間: " + m.group(1), 80)
        d["乗船場所"] = clean(board[:m.start()]).rstrip("、,")
    return d


# ---------------------------------------------------------------- 東京湾遊漁船業協同組合

TOKYOWAN_URL = "https://www.tokyowan-yugyosen.or.jp/shop/"


def parse_tokyowan(refresh, summary):
    s = soup_of(fetch(TOKYOWAN_URL, refresh))
    uls = s.select("#shopList ul")
    rows = [u for u in uls if "head" not in (u.get("class") or [])]
    out, excluded = [], []
    for i, ul in enumerate(rows, 1):
        d1 = ul.select_one(".d1")
        if not d1:
            continue
        label = clean(d1.get_text(" "))
        m = re.match(r"^(\S+)\s+(.+)$", label)
        district, name = (m.group(1), m.group(2)) if m else ("", label)
        fishing = bool(ul.select_one(".d5 img"))
        yakata = bool(ul.select_one(".d6 img"))
        if not fishing:
            excluded.append({"row": i, "name": name, "reason": "屋形船のみ（釣り船のアイコンなし）"})
            log("EXCLUDE tokyowan row %d %s: yakata only" % (i, name))
            continue
        d2 = ul.select_one(".d2")
        place = clean(d2.select_one(".place").get_text(" ")) if d2 and d2.select_one(".place") else ""
        if d2 and d2.select_one(".place"):
            d2.select_one(".place").extract()
        addr_lines = lines_of(d2) if d2 else []
        addr = addr_lines[0] if addr_lines else ""
        note = " ".join(addr_lines[1:])
        m = re.match(r"^(.*?\d[\d\-]*)\s*[（(](.+)[）)]$", addr)
        if m:
            addr, note = m.group(1), (m.group(2) + " " + note).strip()
        pref = next((p for p in PREFS if addr.startswith(p)), None)
        if not pref:
            log("WARN tokyowan row %d: no pref in address" % i)
            continue
        rec = new_record(pref, "tokyowan", i, TOKYOWAN_URL)
        rec["name"] = name
        rec["address"] = clean_address(addr)
        rec["city"] = city_of(addr)
        rec["tel"] = tel_display(ul.select_one(".d3").get_text(" ")) if ul.select_one(".d3") else None
        a = d1.find("a", href=True)
        rec["website"], rec["sns"] = pick_links([a["href"]] if a else [])
        station = " / ".join(lines_of(ul.select_one(".d4"))) if ul.select_one(".d4") else ""
        acc = []
        if place:
            acc.append("%s: %s%s" % (place, rec["address"], ("（%s）" % note) if note else ""))
        if station:
            acc.append("最寄り駅: " + station)
        rec["access"] = short(" / ".join(acc), 120)
        rec["description"] = short("東京湾遊漁船業協同組合 組合員（%s）。釣り船%s" % (district, "・屋形船" if yakata else ""))
        out.append(rec)
    summary["tokyowan"] = {"list_rows": len(rows), "records": len(out), "excluded": excluded}
    log("tokyowan: rows=%d records=%d excluded=%d" % (len(rows), len(out), len(excluded)))
    return out


# ---------------------------------------------------------------- 13 小笠原村観光協会

OGASAWARA_URL = "https://www.ogasawaramura.com/play/activity/sea/fishing/"


def ogasawara_plans(lines, kind, detail_url):
    plans = []
    for ln in lines:
        t = nfkc(ln)
        price = parse_yen(t)
        tr = time_ranges(t)
        if price is None:
            if tr and plans:
                base = plans[-1]
                if base["depart"] is None:
                    base["depart"], base["return"] = tr[0]
                    extra = tr[1:]
                else:
                    extra = tr
                for dep, ret in extra:
                    p = dict(base)
                    p["depart"], p["return"] = dep, ret
                    plans.append(p)
            continue
        label = label_before_price(t) or ("乗合" if kind == "乗合" else "チャーター")
        plans.append({
            "name": label, "kind": kind, "targets": [],
            "price": price if kind == "乗合" else None,
            "price_text": short(("1隻 " if kind == "仕立" else "") + clean(ln), 80),
            "depart": None, "return": None, "meet": "", "season": "", "days": "", "includes": "",
            "url": detail_url})
    return plans


def parse_ogasawara(refresh, summary):
    s = soup_of(fetch(OGASAWARA_URL, refresh))
    for t in s(["header", "footer", "nav"]):
        t.decompose()
    main = s.find("main") or s.body
    anchors = []
    for a in main.find_all("a", href=True):
        h = a["href"]
        if re.search(r"ogasawaramura\.com/play/(?!activity/)[^/]+/?$", h):
            nm = clean(a.get_text(" "))
            if nm and all(nm != x[0] for x in anchors):
                anchors.append((nm, h))
    lines = lines_of(main)
    out = []
    names = [x[0] for x in anchors]
    for i, (nm, href) in enumerate(anchors, 1):
        try:
            start = lines.index(nm)
        except ValueError:
            log("WARN ogasawara: name line not found %s" % nm)
            continue
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if lines[j] in names or lines[j].startswith("宿泊予約状況"):
                end = j
                break
        block = lines[start + 1:end]
        rec = new_record("東京都", "ogasawara", i, OGASAWARA_URL)
        m = re.match(r"^(?:小笠原)?(?:父島|母島)?\s*(\S+?丸)\s+([ぁ-ゖー]+)$", nm)
        if m:
            rec["name"], rec["kana"] = m.group(1), m.group(2)
        else:
            rec["name"] = nm
        rec["city"] = "小笠原村"
        sec, secs = None, {"乗合": [], "仕立": [], "定員": [], "tel": []}
        for ln in block:
            if ln.startswith("乗合い料金"):
                sec = "乗合"
                continue
            if ln.startswith("チャーター料金"):
                sec = "仕立"
                continue
            if ln.startswith("定員"):
                sec = "定員"
                continue
            if sec is None:
                secs["tel"].append(ln)
            else:
                secs[sec].append(ln)
        rec["tel"] = tel_display(" ".join(secs["tel"]))
        cap = re.findall(r"(\d+)\s*名", nfkc(" ".join(secs["定員"])))
        rec["capacity"] = int(cap[0]) if cap else None
        rec["plans"] = ogasawara_plans(secs["乗合"], "乗合", href) + ogasawara_plans(secs["仕立"], "仕立", href)
        rec["types"] = uniq([p["kind"] for p in rec["plans"]])
        links, dtext = [], ""
        try:
            ds = soup_of(fetch(href, refresh))
            for t in ds(["header", "footer", "nav"]):
                t.decompose()
            dm = ds.find("main") or ds.body
            dl = lines_of(dm)
            cut = next((k for k, x in enumerate(dl) if x.startswith("宿泊予約状況") or x == "TOUR"), len(dl))
            dtext = " ".join(dl[:cut])
            for a in dm.find_all("a", href=True):
                hh = a["href"]
                if host_of(hh) in ("hahajima.com", "blog.vill.ogasawara.tokyo.jp", "visitogasawara.com",
                                   "letsgo-ogasawara.com", "tokyo-islands.com", "shimapo.com", "ogasawara-info.jp",
                                   "tokyo-park.or.jp"):
                    break  # ここから下はサイト共通のリンク集
                links.append(hh)
        except Exception as e:  # noqa
            log("WARN ogasawara detail %s: %s" % (href, e))
        rec["website"], rec["sns"] = pick_links(links)
        rec["methods"] = words_in(dtext, METHOD_WORDS)
        rec["description"] = "小笠原村観光協会「釣り」掲載の釣り船事業者"
        out.append(rec)
    summary["ogasawara"] = {"list_rows": len(anchors), "records": len(out)}
    log("ogasawara: rows=%d records=%d" % (len(anchors), len(out)))
    return out


# ---------------------------------------------------------------- 19 山中湖漁協

YAMANAKA_URL = "https://www.lake-yamanaka.net/fishing"
YAMANAKA_SHOP = re.compile(
    r"^(?P<name>.+?)(?P<hp>HP)?(?P<addr>(?:山梨県)?(?:南都留郡)?山中湖村?.+?)☎?"
    r"(?P<tel>[0-9\-−ー－‐]{9,})マップ:?(?P<map>\d+)番\[(?P<svc>[^\]]*)\]")


def parse_yamanaka(refresh, summary):
    s = soup_of(fetch(YAMANAKA_URL, refresh))
    els = s.find_all(attrs={"data-testid": "richTextElement"})
    started = False
    shops = []
    for e in els:
        tx = re.sub(r"[\s​﻿︎️]+", "", e.get_text(""))
        if not started:
            if "黒色のお店" in tx or "マップの見方" in tx:
                started = True
            continue
        if "コンビニ" in tx:
            break
        links = [a["href"] for a in e.find_all("a", href=True)]
        segs = [x for x in re.split(r"_{3,}", nfkc(tx)) if x.strip()]
        parsed = []
        for seg in segs:
            m = YAMANAKA_SHOP.match(seg)
            if not m:
                log("WARN yamanaka unparsed segment: %s" % seg[:60])
                continue
            parsed.append(m.groupdict())
        n_hp = sum(1 for p in parsed if p["hp"])
        if n_hp == len(links):
            k = 0
            for p in parsed:
                if p["hp"]:
                    p["link"] = links[k]
                    k += 1
        else:
            log("WARN yamanaka: HP links %d != HP labels %d (website skipped for this block)" % (len(links), n_hp))
        shops.extend(parsed)
    out, excluded = [], 0
    for i, p in enumerate(shops, 1):
        svc = re.sub(r"\s+", "", p["svc"])
        if "ドーム船" not in svc:
            excluded += 1
            continue
        rec = new_record("山梨県", "yamanaka", i, YAMANAKA_URL)
        rec["name"] = p["name"]
        addr = p["addr"]
        if not addr.startswith("山梨県"):
            addr = "山梨県" + ("" if addr.startswith("南都留郡") else "南都留郡") + addr
        addr = addr.replace("山中湖平野", "山中湖村平野") if "山中湖村" not in addr else addr
        rec["address"] = clean_address(addr)
        rec["city"] = "山中湖村"
        tel = re.sub(r"^00(?=\d)", "0", nfkc(p["tel"]))
        if tel != nfkc(p["tel"]):
            log("NOTE yamanaka %s: tel leading 00 fixed" % rec["src_id"])
        rec["tel"] = tel_display(tel)
        rec["website"], rec["sns"] = pick_links([p["link"]] if p.get("link") else [])
        rec["description"] = short("山中湖漁協の遊漁券販売・ボート貸出店一覧でドーム船取扱店（%s）。マップ%s番" % (svc, p["map"]))
        out.append(rec)
    summary["yamanaka"] = {"list_rows": len(shops), "records": len(out), "excluded_no_dome": excluded}
    log("yamanaka: shops=%d dome=%d excluded=%d" % (len(shops), len(out), excluded))
    return out


# ---------------------------------------------------------------- 20 野尻湖漁協

NOJIRI_URL = "https://nojiriko-gyokyo.com/wakasagi_yakata"


def parse_nojiri(refresh, summary):
    html = fetch(NOJIRI_URL, refresh)
    s = soup_of(html)
    title = clean(s.title.get_text(" ")) if s.title else ""
    page_txt = clean(s.body.get_text(" "))
    wakasagi = bool(re.search(r"わかさぎ|ワカサギ", title + " " + page_txt[:3000]))
    common_fac = []
    if "全船暖房" in page_txt:
        common_fac.append("暖房")
    if "トイレ完備" in page_txt:
        common_fac.append("トイレ")
    table = s.find("table")
    rows = table.find_all("tr") if table else []
    out = []
    for i, tr in enumerate(rows, 1):
        th, tds = tr.find("th"), tr.find_all("td")
        if not th or not tds:
            continue
        a = th.find("a", href=True)
        name = clean(th.get_text(" "))
        info = lines_of(tds[0])
        rec = new_record("長野県", "nojiri", i, NOJIRI_URL)
        rec["name"] = name
        for ln in info:
            if ln.startswith("住所"):
                addr = re.sub(r"^住所\s*[:：]\s*", "", ln)
                if addr.startswith("信濃町"):
                    addr = "長野県上水内郡" + addr
                rec["address"] = clean_address(addr)
                rec["city"] = "信濃町" if "信濃町" in addr else city_of(addr)
            elif ln.startswith("電話"):
                rec["tel"] = tel_display(ln)
            elif ln.startswith("定員"):
                cap = re.findall(r"(\d+)\s*名", nfkc(ln))
                rec["capacity"] = int(cap[0]) if cap else None
        rec["website"], rec["sns"] = pick_links([a["href"]] if a else [])
        svc = [re.sub(r"^[・･]\s*", "", x) for x in (lines_of(tds[2]) if len(tds) > 2 else []) if x != "---"]
        rec["facilities"] = uniq(common_fac + svc)
        rec["targets"] = ["ワカサギ"] if wakasagi else []
        rec["description"] = "野尻湖漁協「屋形船店一覧（釣り船組合）」掲載の%s屋形船" % ("わかさぎ釣り" if wakasagi else "釣り")
        out.append(rec)
    summary["nojiri"] = {"list_rows": len(rows), "records": len(out)}
    log("nojiri: rows=%d records=%d" % (len(rows), len(out)))
    return out


# ---------------------------------------------------------------- 24 三重県 R2 協力事業者一覧

MIE_URL = "https://www.pref.mie.lg.jp/common/content/000904685.pdf"
PERSON_LIKE = re.compile(r"^[一-龥々]{1,4}[ 　][一-龥々ぁ-ゖ]{1,4}$")


def parse_mie(refresh, summary):
    import io
    import pdfplumber
    body = fetch(MIE_URL, refresh, binary=True)
    rows = []
    unparsed = []
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for page in pdf.pages:
            for ln in (page.extract_text() or "").split("\n"):
                ln = ln.strip()
                m = re.match(r"^(\d+)\s+(\S+)\s+(.+?)・\s*三重\s*(\d{4})$", ln)
                if m:
                    rows.append(m.groups())
                elif re.match(r"^\d+\s", ln):
                    unparsed.append(ln)
    for ln in unparsed:
        log("WARN mie unparsed line: %s" % ln[:40])
    nums = [int(r[0]) for r in rows]
    out, person_rows = [], []
    for no, city_cell, name, reg in rows:
        no = int(no)
        city = next((c for c in MIE_MUNIS if city_cell.startswith(c)), None)
        if city is None:
            log("WARN mie row %d: unknown municipality" % no)
        elif city != city_cell:
            log("NOTE mie row %d: municipality cell has extra chars, used %s" % (no, city))
        name = join_ordinal(clean(name))
        if PERSON_LIKE.match(name) and not re.search(r"丸|船|釣|渡|港|会社|水産|商店|漁協|組合|屋|荘|館|企画|事務所", name):
            person_rows.append(no)
            log("EXCLUDE mie row %d: office name is a personal name" % no)
            continue
        rec = new_record("三重県", "mie-r2kyoryoku", no, MIE_URL)
        rec["name"] = name
        rec["city"] = city
        rec["description"] = "三重県「来県延期協力金 協力事業者一覧（遊漁船）」令和2年8月3日現在に掲載。遊漁船業登録番号 三重%s" % reg
        rec["stale"] = True
        out.append(rec)
    summary["mie"] = {"list_rows": len(rows), "max_row_no": max(nums) if nums else 0,
                      "missing_row_nos": sorted(set(range(1, (max(nums) if nums else 0) + 1)) - set(nums)),
                      "records": len(out), "excluded_personal_name_rows": person_rows}
    log("mie: rows=%d max_no=%s records=%d excluded=%d" % (len(rows), max(nums) if nums else 0, len(out),
                                                           len(person_rows)))
    return out


# ---------------------------------------------------------------- 35 周防大島観光協会

SUO_URL = "https://suo-oshima-kanko.net/spot/spot_category/recreation/fishing"


def parse_suo(refresh, summary):
    s = soup_of(fetch(SUO_URL, refresh))
    spots = []
    for a in s.find_all("a", href=True):
        h = a["href"]
        if re.search(r"suo-oshima-kanko\.net/spot/(?!spot_)[^/]+/?$", h):
            t = clean(a.get_text(" "))
            if t and h not in [x[1] for x in spots]:
                spots.append((t, h))
    out, excluded = [], []
    for i, (label, href) in enumerate(spots, 1):
        name = clean(re.split(r"\s{1}(?=[ぁ-ゖ]{4,}|[^\x00-\x7f]*[！!。])", label)[0])
        name = re.sub(r"\s+(大島|久賀|橘|東和)地区.*$", "", name)
        if re.search(r"キャンプ|野営|旅行村", label):
            excluded.append({"row": i, "name": name, "reason": "キャンプ場"})
            continue
        try:
            ds = soup_of(fetch(href, refresh))
        except Exception as e:  # noqa
            log("WARN suo detail %s: %s" % (href, e))
            continue
        for t in ds(["header", "footer", "nav"]):
            t.decompose()
        dm = ds.find("main") or ds.body
        dl = lines_of(dm)
        try:
            k_info = dl.index("基本情報")
        except ValueError:
            k_info = len(dl)
        k_end = next((k for k in range(k_info, len(dl)) if dl[k] in ("お知らせ", "TOPICS")), len(dl))
        body_lines, info_lines = dl[:k_info], dl[k_info:k_end]
        body_txt = " ".join(body_lines)
        if not re.search(r"遊漁船|釣り体験|乗合|乗り合い|釣行", body_txt):
            excluded.append({"row": i, "name": name, "reason": "釣り客を乗せる船の記載なし"})
            continue
        rec = new_record("山口県", "suo-oshima", i, SUO_URL)
        ttl = clean(ds.title.get_text(" ")) if ds.title else ""
        ttl = clean(re.split(r"\s*[|｜]\s*|\s+[-–]\s+", ttl)[0]) if ttl else ""
        rec["name"] = ttl if ttl and "観光協会" not in ttl else name
        info = {}
        for k in range(len(info_lines) - 1):
            if info_lines[k] in ("住所", "TEL", "駐車場", "WEB", "営業時間") and info_lines[k] not in info:
                vals = []
                for x in info_lines[k + 1:]:
                    if x in ("住所", "TEL", "駐車場", "WEB", "SNS", "マップ", "MAP", "営業時間", "予約受付"):
                        break
                    vals.append(x)
                info[info_lines[k]] = " ".join(vals)
        if info.get("住所"):
            rec["address"] = clean_address(info["住所"])
            rec["city"] = city_of(rec["address"])
        rec["tel"] = tel_display(info.get("TEL", ""))
        links = []
        idx_info = None
        for a in dm.find_all("a", href=True):
            if clean(a.get_text(" ")) in ("GOOGLE MAPで見る",):
                idx_info = True
                break
            links.append(a["href"])
        rec["website"], rec["sns"] = pick_links([u for u in links if host_of(u) not in ("town.suo-oshima.lg.jp",)])
        # 料金（見出し＝プラン/クルージング/乗合/チャーターの短い行、小見出し＝半日/1日/N時間/コース の短い行）
        # 釣りのプラン見出しがあるページでは、釣り以外（遊覧クルーズ等）の見出しの料金は取らない。
        section, sub = "", ""
        fishing_sections = any(len(x) <= 30 and "釣" in x and re.search(r"プラン|チャーター|乗合|乗り合い|コース", x)
                               for x in body_lines)
        for ln in body_lines:
            t = nfkc(ln)
            if t.startswith(("✔", "※", "・")):
                continue
            price = parse_yen(t)
            if price is None:
                if len(ln) <= 30 and "。" not in ln:
                    if re.search(r"プラン|クルージング|クルーズ", ln) or (
                            re.search(r"乗合|乗り合い|チャーター|貸切", ln) and not re.search(r"半日|1日|一日|時間", t)):
                        section, sub = ln, ""
                    elif re.search(r"半日|1日|一日|\d+時間|コース", t):
                        sub = ln
                continue
            if len(ln) > 60:
                continue
            if fishing_sections and not re.search(r"釣|チャーター|乗合|乗り合い", section):
                continue
            label = label_before_price(t)
            ctx = " ".join([section, sub, ln])
            charter = bool(re.search(r"貸切|チャーター", ln)) or (
                bool(re.search(r"貸切|チャーター", section + sub)) and not re.search(r"乗合|乗り合い", ln))
            per = (not charter) and bool(re.search(r"乗合|乗り合い|一名|お一人|1人|一人|大人|男性|女性|お子様|小人", ctx))
            if label and re.search(r"プラン|チャーター|貸切|乗合|乗り合い", label):
                pname = label
            else:
                pname = " ".join(x for x in (section, sub) if x) or label or "料金"
            rec["plans"].append({
                "name": short(pname, 40), "kind": "仕立" if charter else ("乗合" if per else ""),
                "targets": [], "price": price if per else None,
                "price_text": short(("1隻 " if charter else "") + ln, 80),
                "depart": None, "return": None, "meet": "", "season": "", "days": "", "includes": "",
                "url": href})
        rec["types"] = uniq([p["kind"] for p in rec["plans"] if p["kind"]])
        sched = [ln for ln in body_lines + info_lines if time_ranges(ln) and len(ln) <= 40]
        rec["schedule_text"] = short(" / ".join(uniq(sched)), 100)
        sent = " ".join(x for x in body_lines if re.search(r"対象魚|狙えます|釣れ", x))
        rec["targets"] = words_in(sent, FISH_WORDS)
        rec["methods"] = words_in(body_txt, METHOD_WORDS)
        if "あり" in info.get("駐車場", ""):
            rec["access"] = "駐車場あり"
        for x in body_lines:
            m = re.match(r"^船長\s*(\S+\s?\S*?)（", x)
            if m:
                _person_names.add(re.sub(r"\s+", "", m.group(1)))
        rec["description"] = "周防大島観光協会「釣り・フィッシング」掲載"
        out.append(rec)
    summary["suo"] = {"list_rows": len(spots), "records": len(out), "excluded": excluded}
    log("suo: spots=%d records=%d excluded=%d" % (len(spots), len(out), len(excluded)))
    return out


# ---------------------------------------------------------------- 43 天草Webの駅

AMAKUSA_URL = "https://www.amakusa-web.jp/WebEki/Pub/MyHpMatome.aspx?matomeId=1"
AMAKUSA_ADDR = re.compile(r"(?:熊本県)?(?:天草市|上天草市|天草郡苓北町|苓北町)[^\s　。、,]*?\d[\d\-−－ー番地の]*")


def parse_amakusa(refresh, summary):
    s = soup_of(fetch(AMAKUSA_URL, refresh))
    boats = []
    for a in s.find_all("a", href=True):
        m = re.match(r"^https?://hp\.amakusa-web\.jp/(a\d+)/MyHp/Pub/?$", a["href"])
        if m and a.find_parent("div", class_="mBox") is not None:
            label = clean(a.get_text(" "))
            if label and m.group(1) not in [b[2] for b in boats]:
                boats.append((label, a["href"], m.group(1)))
    out = []
    for i, (label, href, aid) in enumerate(boats, 1):
        toks = label.split(" ")
        name = toks[-1] if len(toks) > 2 and toks[-1].endswith("丸") else label
        rec = new_record("熊本県", "amakusa", i, AMAKUSA_URL)
        rec["name"] = name
        rec["website"] = href
        texts = []
        contact_url = None
        try:
            ms = soup_of(fetch(href, refresh))
            texts.append(clean(ms.body.get_text(" ")))
            for a in ms.find_all("a", href=True):
                if clean(a.get_text(" ")) == "お問い合わせ" and "Free.aspx" in a["href"]:
                    contact_url = urljoin(href, a["href"])
                    break
        except Exception as e:  # noqa
            log("WARN amakusa %s: %s" % (href, e))
        if contact_url:
            try:
                ch = fetch(contact_url, refresh, allow_fail=True)
                if ch:
                    ct = clean(soup_of(ch).body.get_text(" "))
                    texts.insert(0, ct)
                    for m in re.finditer(r"船長\s*([一-龥々]{1,4}\s?[一-龥々]{1,4})", ct):
                        _person_names.add(re.sub(r"\s+", "", m.group(1)))
            except Exception as e:  # noqa
                log("WARN amakusa contact %s: %s" % (contact_url, e))
        alltxt = " ".join(texts)
        m = AMAKUSA_ADDR.search(nfkc(alltxt))
        if m:
            addr = m.group(0)
            rec["address"] = clean_address(addr if addr.startswith("熊本県") else "熊本県" + addr)
            rec["city"] = city_of(rec["address"])
        else:
            m2 = re.search(r"(上天草市|天草市|苓北町)", alltxt)
            rec["city"] = m2.group(1) if m2 else None
        tm = re.search(r"(?:携帯|電話|TEL|Tel|tel)\s*[:：]?\s*(0[\d０-９\-－ー−]{9,13})", nfkc(alltxt))
        if tm:
            rec["tel"] = tel_display(tm.group(1))
        else:  # ラベルの無い番号は、ページ内に1つだけのときに限る（釣果記事中の別番号を拾わない）
            cands = uniq([norm_tel(x) for x in re.findall(r"0\d{1,4}-\d{1,4}-\d{3,4}", nfkc(alltxt))])
            rec["tel"] = tel_display(alltxt) if len(cands) == 1 else None
        rec["description"] = "天草Webの駅「釣り船特集」掲載の釣り船"
        out.append(rec)
    summary["amakusa"] = {"list_rows": len(boats), "records": len(out)}
    log("amakusa: boats=%d records=%d" % (len(boats), len(out)))
    return out


# ---------------------------------------------------------------- 出力

LISTS = [
    ("iwate", parse_iwate),
    ("tokyowan", parse_tokyowan),
    ("ogasawara", parse_ogasawara),
    ("yamanaka", parse_yamanaka),
    ("nojiri", parse_nojiri),
    ("mie", parse_mie),
    ("suo", parse_suo),
    ("amakusa", parse_amakusa),
]


def scrub_person_names(records):
    """個人名が出力に紛れていないか確かめ、見つかった値は消す（ログには名前を出さない）。"""
    names = [n for n in _person_names if len(n) >= 3]
    hits = 0
    for rec in records:
        for k in ("name", "kana", "address", "access", "description", "schedule_text"):
            v = rec.get(k)
            if isinstance(v, str) and v:
                vv = re.sub(r"\s+", "", v)
                if any(n in vv for n in names):
                    hits += 1
                    log("WARN person name found in %s.%s (value cleared)" % (rec["src_id"], k))
                    rec[k] = None if k in ("name", "kana", "address") else ""
        for p in rec.get("plans", []):
            vv = re.sub(r"\s+", "", p.get("price_text") or "")
            if any(n in vv for n in names):
                hits += 1
                p["price_text"] = ""
    return hits


def save_json(path, data):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="一覧キー（%s）をカンマ区切りで" % ",".join(k for k, _ in LISTS))
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--log", default=LOG_DEFAULT)
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    args = ap.parse_args()
    _log_path[0] = os.path.abspath(args.log)
    out_path = os.path.abspath(args.out)
    only = set(x.strip() for x in args.only.split(",") if x.strip())
    lists = [(k, f) for k, f in LISTS if not only or k in only]
    log("start out=%s lists=%s" % (out_path, ",".join(k for k, _ in lists)))
    records, summary = [], {}
    for n, (key, fn) in enumerate(lists, 1):
        try:
            recs = fn(args.refresh, summary)
        except Exception as e:  # noqa
            log("ERROR list %s: %s" % (key, e))
            summary[key] = {"error": str(e)}
            recs = []
        records.extend(recs)
        log("progress %d/%d lists, records=%d" % (n, len(lists), len(records)))
        save_json(out_path, [{k: r[k] for k in PUBLIC_KEYS + (["stale"] if r.get("stale") else [])}
                             for r in records])
    hits = scrub_person_names(records)
    seen = set()
    for r in records:
        if r["src_id"] in seen:
            log("WARN duplicate src_id %s" % r["src_id"])
        seen.add(r["src_id"])
    order = [k for k, _ in LISTS]

    def sort_key(r):
        code, lst, row = r["src_id"].split(":")
        li = next((n for n, k in enumerate(order) if lst.startswith(k)), len(order))
        return (code, li, lst, int(row))
    records.sort(key=sort_key)
    final = [{k: r[k] for k in PUBLIC_KEYS + (["stale"] if r.get("stale") else [])} for r in records]
    save_json(out_path, final)
    by_pref = {}
    for r in final:
        by_pref[r["pref"]] = by_pref.get(r["pref"], 0) + 1
    summary["_total"] = {"records": len(final), "by_pref": by_pref, "person_name_hits": hits,
                         "requests": _stats["requests"], "cache_hits": _stats["cache_hits"],
                         "http_errors": _stats["errors"]}
    if not only:
        save_json(SUMMARY_PATH, summary)
    log("done records=%d by_pref=%s requests=%d cache_hits=%d errors=%d person_name_hits=%d" % (
        len(final), json.dumps(by_pref, ensure_ascii=False), _stats["requests"], _stats["cache_hits"],
        _stats["errors"], hits))


if __name__ == "__main__":
    main()
