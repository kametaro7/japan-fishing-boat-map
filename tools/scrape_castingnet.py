#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
castingnet（キャスティング船釣り予約 https://reserve.castingnet.jp/）クローラ

サイト構造（2026-09-15 調査）
- 釣割(chowari) のホワイトラベル。文字コード EUC-JP（euc_jis_2004 で復号）。
- robots.txt: User-agent:* は /admin/ のみ Disallow。sitemap.xml は chowari の汎用ページのみで船宿一覧は無い。
- 船宿一覧: search.php（こだわり検索）。GET の page 指定は無視されるため、検索フォームと同じ
  POST（sort=direct&direct=e = 所在地が東から、の決定的な並び）で 15件/ページ を全ページ取得する。
  件数が合わなければ都道府県別（pref=01..47）の検索で補完する。
- 船宿詳細: shipNNNNN.html（船名・所在地・HP・最寄IC/駅/駐車場・設備・最大定員・代表者コメント・
  予約プラン表）。存在しないIDは 302 → end.php。
  ※電話番号は HTML コメントアウトされ非表示のため取得しない（tel は null）。
- アクセス: shipNNNNNa.html（var m_la / m_lo に船宿マーカー座標、送迎の有無）。
- プラン詳細: popplanNNNNN-MMMMM.html（乗合/仕立・通常価格・釣割価格・料金に含まれるもの・
  ターゲット・出船/集合/帰港時刻・釣り方・備考）。
- 予約カレンダー(planNNNNN-MMMMM-YYYYMMDD.html)・予約フォーム・ログイン・地図の ajax は叩かない。

使い方
  python3 tools/scrape_castingnet.py                 # 全件
  python3 tools/scrape_castingnet.py --limit 30      # 先頭30件
  python3 tools/scrape_castingnet.py --ids 00057,00164 --out work/sources/castingnet.sample.json
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
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

SRC = "castingnet"
BASE = "https://reserve.castingnet.jp/"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CACHE_DIR = os.path.join(ROOT, "work", "cache", SRC)
LOG_PATH = os.path.join(ROOT, "work", "logs", SRC + ".log")
DEFAULT_OUT = os.path.join(ROOT, "work", "sources", SRC + ".json")
FETCHED = "2026-09-15"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 0.9  # 秒（SPEC: 0.8秒以上）
MAX_RETRY = 5

PREF_CODES = ["%02d" % i for i in range(1, 48)]
PREF_NAMES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]

SNS_DOMAINS = ("facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "youtu.be",
               "line.me", "lin.ee", "tiktok.com", "threads.net")
SELF_DOMAINS = ("castingnet.jp", "chowari.jp", "112.78.201.50")

METHOD_KEYWORDS = [
    "タイラバ", "ジギング", "スーパーライトジギング", "SLJ", "一つテンヤ", "ひとつテンヤ", "テンヤ",
    "ティップラン", "エギング", "イカメタル", "泳がせ", "落し込み", "落とし込み", "コマセ", "ビシ",
    "胴突き", "キャスティング", "ルアー", "インチク", "カブラ", "サビキ", "中深海", "深場", "夜焚き",
    "トローリング", "電動", "フカセ", "ライトタックル", "LT",
]
DAY_KEYWORDS = ["平日", "土日祝", "土日", "土曜", "日曜", "祝日", "金土", "週末"]


# ----------------------------------------------------------------------------
# 基盤: ログ・キャッシュ・HTTP
# ----------------------------------------------------------------------------

def log(msg):
    # type: (str) -> None
    line = "%s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


class Fetcher(object):
    def __init__(self, refresh=False, interval=MIN_INTERVAL):
        self.refresh = refresh
        self.interval = max(0.8, interval)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.5",
        })
        self.last = 0.0
        self.n_net = 0
        self.n_cache = 0
        if not os.path.isdir(CACHE_DIR):
            os.makedirs(CACHE_DIR)

    @staticmethod
    def key(url, data=None):
        # type: (str, Optional[Dict[str, str]]) -> str
        k = url
        if data is not None:
            k += "|POST|" + "&".join("%s=%s" % (a, data[a]) for a in sorted(data))
        return hashlib.sha1(k.encode("utf-8")).hexdigest()

    def _throttle(self):
        wait = self.last + self.interval - time.time()
        if wait > 0:
            time.sleep(wait)
        self.last = time.time()

    def get(self, url, data=None):
        # type: (str, Optional[Dict[str, str]]) -> Tuple[int, Optional[str]]
        """(status, text) を返す。status: 200 / 404(不存在・end.phpへのリダイレクト含む) / -1(失敗)"""
        h = self.key(url, data)
        path_html = os.path.join(CACHE_DIR, h + ".html")
        path_gone = os.path.join(CACHE_DIR, h + ".gone")
        if not self.refresh:
            if os.path.exists(path_html):
                self.n_cache += 1
                with open(path_html, "rb") as f:
                    return 200, decode(f.read())
            if os.path.exists(path_gone):
                self.n_cache += 1
                return 404, None
        delay = 2.0
        for attempt in range(MAX_RETRY + 1):
            self._throttle()
            self.n_net += 1
            try:
                if data is None:
                    r = self.session.get(url, timeout=60, allow_redirects=False)
                else:
                    r = self.session.post(url, data=data, timeout=60, allow_redirects=False)
            except requests.RequestException as e:
                log("WARN request error %s (%s) attempt=%d" % (url, e.__class__.__name__, attempt + 1))
                if attempt >= MAX_RETRY:
                    return -1, None
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                log("WARN HTTP %d %s attempt=%d backoff=%.0fs" % (r.status_code, url, attempt + 1, delay))
                if attempt >= MAX_RETRY:
                    return -1, None
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location", "")
                if "end.php" in loc or loc.rstrip("/") == BASE.rstrip("/"):
                    self._save(path_gone, (url + " -> " + loc).encode("utf-8"), h, url)
                    return 404, None
                log("WARN redirect %s -> %s (not followed)" % (url, loc))
                return -1, None
            if r.status_code == 404:
                self._save(path_gone, url.encode("utf-8"), h, url)
                return 404, None
            if r.status_code != 200:
                log("WARN HTTP %d %s" % (r.status_code, url))
                return -1, None
            self._save(path_html, r.content, h, url, data)
            return 200, decode(r.content)
        return -1, None

    @staticmethod
    def _save(path, content, h, url, data=None):
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, path)
        try:
            with open(os.path.join(CACHE_DIR, "index.tsv"), "a", encoding="utf-8") as f:
                f.write("%s\t%s\t%s\n" % (h, url, json.dumps(data, ensure_ascii=False) if data else ""))
        except Exception:
            pass


def decode(b):
    # type: (bytes) -> str
    for enc in ("euc_jis_2004",):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("euc_jis_2004", errors="replace")


# ----------------------------------------------------------------------------
# テキスト整形
# ----------------------------------------------------------------------------

Z2H = str.maketrans("０１２３４５６７８９：，", "0123456789:,")


def clean(s):
    # type: (Optional[str]) -> str
    if s is None:
        return ""
    s = s.replace("　", " ").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def cell_text(el):
    if el is None:
        return ""
    return clean(el.get_text(" ", strip=True))


def parse_time_leading(s):
    # type: (str) -> Optional[str]
    """文字列の先頭にある時刻だけを HH:MM で返す（"ご相談…通常は6:00前後" のような文は None）"""
    t = clean(s).translate(Z2H)
    m = re.match(r"^(?:午前|午後)?\s*(\d{1,2})\s*[:時]\s*(\d{2})?", t)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    if t.startswith("午後") and hh < 12:
        hh += 12
    if hh > 29 or mm > 59:
        return None
    return "%02d:%02d" % (hh, mm)


def split_targets(s):
    # type: (str) -> List[str]
    s = clean(s)
    if not s or s in ("-", "−", "なし"):
        return []
    out = []
    for p in re.split(r"[、,，・/／]", s):
        p = re.sub(r"(等|など|ほか|他)$", "", p.strip()).strip()
        p = re.sub(r"^[（(]|[）)]$", "", p).strip()
        if p and len(p) <= 20 and p not in out:
            out.append(p)
    return out


def uniq(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def short_desc(s, limit=100):
    # type: (str, int) -> str
    s = clean(s)
    s = s.strip("「」『』 ")
    if len(s) <= limit:
        return s
    cut = s[:limit]
    idx = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("!"), cut.rfind("♪"))
    if idx >= 30:
        return cut[:idx + 1]
    return cut[:limit - 1] + "…"


def yen(s):
    # type: (str) -> Optional[int]
    m = re.search(r"([\d,]+)\s*円", clean(s).translate(Z2H))
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def strip_note(s):
    # type: (str) -> str
    return clean(re.split(r"※同行者様全員", clean(s))[0])


# ----------------------------------------------------------------------------
# 一覧（search.php POST）
# ----------------------------------------------------------------------------

def search_form(page, pref=""):
    # type: (int, str) -> Dict[str, str]
    return {
        "pref": pref, "sort": "direct", "direct": "e", "popular": "", "price": "",
        "update": "", "inter": "", "trial": "", "mode": "", "calend": "1", "record": "0",
        "seed": "", "page": str(page),
    }


def parse_search(html):
    # type: (str) -> Tuple[int, int, List[str]]
    total = 0
    pages = 1
    m = re.search(r"([\d,]+)件</span>\s*中", html)
    if m:
        total = int(m.group(1).replace(",", ""))
    m = re.search(r"全(\d+)ページ", html)
    if m:
        pages = int(m.group(1))
    ids = []
    for x in re.findall(r'href="ship(\d{5})\.html', html):
        if x not in ids:
            ids.append(x)
    return total, pages, ids


def enumerate_ids(fx, limit=None):
    # type: (Fetcher, Optional[int]) -> List[str]
    url = BASE + "search.php"
    ids = []  # type: List[str]
    st, html = fx.get(url, search_form(1))
    if st != 200 or not html:
        raise RuntimeError("search.php の取得に失敗")
    total, pages, first = parse_search(html)
    ids.extend(first)
    log("list page 1/%d total=%d ids=%d" % (pages, total, len(ids)))
    for p in range(2, pages + 1):
        if limit and len(ids) >= limit:
            break
        st, html = fx.get(url, search_form(p))
        if st != 200 or not html:
            log("WARN list page %d failed" % p)
            continue
        _, _, got = parse_search(html)
        for x in got:
            if x not in ids:
                ids.append(x)
        log("list page %d/%d ids=%d" % (p, pages, len(ids)))
    if limit:
        return ids[:limit]
    if total and len(ids) < total:
        log("list: %d/%d unique; 都道府県別検索で補完" % (len(ids), total))
        for pc in PREF_CODES:
            st, html = fx.get(url, search_form(1, pc))
            if st != 200 or not html:
                continue
            _, ppages, got = parse_search(html)
            for p in range(2, ppages + 1):
                st2, h2 = fx.get(url, search_form(p, pc))
                if st2 == 200 and h2:
                    got.extend(parse_search(h2)[2])
            before = len(ids)
            for x in got:
                if x not in ids:
                    ids.append(x)
            if len(ids) > before:
                log("list pref=%s +%d ids=%d" % (pc, len(ids) - before, len(ids)))
    log("list done: %d ids (site total=%d)" % (len(ids), total))
    return ids


# ----------------------------------------------------------------------------
# 詳細の解析
# ----------------------------------------------------------------------------

def table_rows(soup):
    # type: (BeautifulSoup) -> Dict[str, object]
    """th が1つ・td が続く行を {th: td要素} で返す（先勝ち）。display:none の行は除外"""
    out = {}
    for tr in soup.find_all("tr"):
        style = (tr.get("style") or "").replace(" ", "")
        if "display:none" in style:
            continue
        ths = tr.find_all("th", recursive=False)
        tds = tr.find_all("td", recursive=False)
        if len(ths) == 1 and len(tds) >= 1:
            k = clean(ths[0].get_text("", strip=True))
            if k and k not in out:
                out[k] = tds[0]
    return out


def classify_url(href):
    # type: (str) -> Tuple[Optional[str], Optional[str]]
    """(website, sns) のどちらかを返す"""
    href = (href or "").strip()
    if not re.match(r"^https?://", href):
        return None, None
    host = re.sub(r"^https?://", "", href).split("/")[0].lower()
    if any(host == d or host.endswith("." + d) for d in SELF_DOMAINS):
        return None, None
    if any(host == d or host.endswith("." + d) for d in SNS_DOMAINS):
        return None, href
    return href, None


def parse_ship(sid, html):
    # type: (str, str) -> Optional[Dict[str, object]]
    soup = BeautifulSoup(html, "lxml")
    rows = table_rows(soup)
    if "釣り船名" not in rows and not soup.find("address"):
        return None
    rec = {
        "src": SRC, "src_id": sid, "src_url": BASE + "ship%s.html" % sid,
        "name": None, "kana": None, "pref": None, "city": None, "address": None, "port": None,
        "lat": None, "lon": None, "tel": None, "website": None, "sns": [],
        "types": [], "targets": [], "methods": [], "holidays": None, "facilities": [],
        "capacity": None, "access": "", "description": "", "plans": [], "schedule_text": "",
        "fetched": FETCHED,
    }  # type: Dict[str, object]

    # 船名・かな
    nm = cell_text(rows.get("釣り船名"))
    if not nm:
        h1 = soup.find("h1")
        nm = cell_text(h1)
    m = re.match(r"^(.*?)\s*[（(]([^（）()]*)[）)]\s*$", nm)
    if m:
        rec["name"] = clean(m.group(1))
        kana = clean(m.group(2))
        if kana and re.match(r"^[ぁ-ゖァ-ヺー・〜～\-－‐\s]+$", kana):
            rec["kana"] = kana
    else:
        rec["name"] = nm or None

    # 都道府県・市区町村・港（<address><strong>県</strong>市 <strong>港</strong>（かな）</address>）
    addr_el = soup.find("address")
    if addr_el is not None:
        strongs = addr_el.find_all("strong")
        if strongs:
            p = clean(strongs[0].get_text())
            rec["pref"] = p if p in PREF_NAMES else None
        if len(strongs) >= 2:
            rec["port"] = clean(strongs[1].get_text()) or None
            # 市区町村 = 1つ目と2つ目の strong の間のテキスト
            between = []
            node = strongs[0].next_sibling
            while node is not None and node is not strongs[1]:
                if isinstance(node, str):
                    between.append(node)
                else:
                    between.append(node.get_text())
                node = node.next_sibling
            city = clean("".join(between))
            rec["city"] = city or None

    # 所在地
    td = rows.get("所在地")
    if td is not None:
        parts = []
        for node in td.children:
            if getattr(node, "name", None) in ("a", "script"):
                continue
            if getattr(node, "name", None) == "br":
                parts.append(" ")
                continue
            parts.append(node if isinstance(node, str) else node.get_text(" "))
        addr = clean("".join(parts))
        port = rec["port"]
        if port and addr.endswith(port):
            addr = clean(addr[: -len(port)])
        if addr:
            if rec["pref"] and not addr.startswith(rec["pref"]):
                addr = rec["pref"] + addr
            rec["address"] = addr

    # ホームページ
    td = rows.get("ホームページ")
    if td is not None:
        hrefs = [a.get("href") for a in td.find_all("a", href=True)]
        if not hrefs:
            t = cell_text(td)
            if re.match(r"^https?://\S+$", t):
                hrefs = [t]
        for hf in hrefs:
            w, s = classify_url(hf)
            if w and not rec["website"]:
                rec["website"] = w
            if s and s not in rec["sns"]:
                rec["sns"].append(s)

    # 設備・最大定員（#tableShip2）
    spec = soup.find("table", id="tableShip2")
    if spec is not None:
        for th in spec.find_all("th"):
            if clean(th.get_text()) == "最大定員":
                tdc = th.find_next_sibling("td")
                mm = re.search(r"(\d+)", cell_text(tdc).translate(Z2H))
                if mm:
                    rec["capacity"] = int(mm.group(1))
        ul = spec.find("ul", class_="utilities")
        if ul is not None:
            fac = []
            for img in ul.find_all("img"):
                src = img.get("src") or ""
                alt = clean(img.get("alt"))
                if alt and "_off" not in src:
                    fac.append(alt)
            rec["facilities"] = uniq(fac)
    bikou = cell_text(rows.get("備考"))
    if "定休" in bikou:
        mh = re.search(r"[^。●※\s]*定休[^。●※]{0,30}", bikou)
        if mh:
            rec["holidays"] = clean(mh.group(0))

    # アクセス（最寄IC・最寄駅・駐車場。送迎は a ページで追記）
    acc = []
    ic = cell_text(rows.get("最寄インター"))
    if ic:
        acc.append("最寄IC: " + re.sub(r"\s*から\s*(\d+)\s*km", r"から\1km", ic))
    stn = cell_text(rows.get("最寄駅"))
    if stn:
        acc.append("最寄駅: " + stn)
    pk = cell_text(rows.get("駐車場"))
    if pk:
        acc.append("駐車場: " + pk)
    rec["access"] = " / ".join(acc)

    # 代表者コメント → description（100字以内）
    cm = soup.find(class_="ShipDtl-comment")
    if cm is not None:
        p = cm.find("p")
        rec["description"] = short_desc(cell_text(p if p is not None else cm))

    # 予約プラン表
    plans = []
    seen = set()
    for h3 in soup.find_all("h3", class_="ship--planname"):
        tr = h3.find_parent("tr")
        if tr is None:
            continue
        tds = tr.find_all("td", recursive=False)
        a = h3.find("a", href=True)
        href = a.get("href") if a is not None else None
        key = href or cell_text(h3)
        if key in seen:
            continue
        seen.add(key)
        icons = []
        if len(tds) > 1:
            for img in tds[1].find_all("img"):
                alt = clean(img.get("alt"))
                style = (img.get("style") or "").replace(" ", "")
                if alt in ("えさ釣り", "ルアー釣り") and "display:none" not in style:
                    icons.append(alt)
        plans.append({
            "row_kind": cell_text(tds[0]) if tds else "",
            "name": cell_text(h3),
            "row_price": cell_text(tds[2]) if len(tds) > 2 else "",
            "row_targets": cell_text(tds[4]) if len(tds) > 4 else "",
            "row_depart": cell_text(tds[5]) if len(tds) > 5 else "",
            "icons": icons,
            "href": href,
        })
    rec["_plan_rows"] = plans
    return rec


def parse_access(html):
    # type: (str) -> Dict[str, object]
    out = {"lat": None, "lon": None, "pickup": ""}  # type: Dict[str, object]
    la = re.search(r"var\s+m_la\s*=\s*'([\-\d.]*)'", html)
    lo = re.search(r"var\s+m_lo\s*=\s*'([\-\d.]*)'", html)
    try:
        if la and lo and la.group(1) and lo.group(1):
            lat, lon = float(la.group(1)), float(lo.group(1))
            if 20.0 <= lat <= 46.5 and 122.0 <= lon <= 154.5:
                out["lat"], out["lon"] = round(lat, 6), round(lon, 6)
    except ValueError:
        pass
    soup = BeautifulSoup(html, "lxml")
    rows = table_rows(soup)
    out["pickup"] = cell_text(rows.get("送迎"))
    return out


def parse_popplan(html):
    # type: (str) -> Dict[str, str]
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) == 2 and cells[0].name == "th" and cells[1].name == "td":
            k = clean(cells[0].get_text("", strip=True))
            k = re.sub(r"[（(]税込[）)]", "", k).strip()
            if k and k not in out:
                out[k] = cell_text(cells[1])
    return out


def build_plan(row, pop, url):
    # type: (Dict[str, object], Dict[str, str], Optional[str]) -> Dict[str, object]
    kind = clean(pop.get("乗合・仕立") or row.get("row_kind") or "")
    normal = strip_note(pop.get("通常価格", ""))
    sale = strip_note(pop.get("釣割価格", ""))
    base_txt = normal or sale or clean(row.get("row_price") or "")
    per_boat = "/隻" in base_txt
    price = None
    if base_txt and not per_boat and ("/人" in base_txt or kind == "乗合"):
        price = yen(base_txt)
    if per_boat:
        price_text = "1隻" + base_txt.replace("/隻", "", 1) + "（税込）"
        if sale and sale != normal and normal:
            price_text += " 釣割" + sale
    elif base_txt:
        price_text = base_txt + "（税込）"
        if sale and normal and sale != normal:
            price_text = "通常" + normal + " / 釣割" + sale + "（税込）"
    else:
        price_text = ""

    targets = split_targets(pop.get("ターゲット") or row.get("row_targets") or "")
    depart = parse_time_leading(pop.get("出船時刻", "")) or parse_time_leading(row.get("row_depart") or "")
    ret = parse_time_leading(pop.get("帰港予定時刻", ""))
    meet = clean(pop.get("集合時刻", ""))
    if len(meet) > 40:
        meet = meet[:40]
    name = clean(row.get("name") or "")
    days = ""
    for kw in DAY_KEYWORDS:
        if re.search(r"[◆◇【】＜＞<>［］\[\]｜|（）()★☆]\s*" + kw + r"|" + kw + r"\s*[◆◇【】＜＞<>［］\[\]｜|（）()★☆]", name):
            days = kw
            break
    methods = []
    how = clean(pop.get("釣り方", ""))
    mh = re.match(r"^(えさ釣り|ルアー釣り|えさ・ルアー釣り)", how)
    if mh:
        methods.append(mh.group(1))
    methods.extend(row.get("icons") or [])
    # プラン名の補足（＝…＝ や （…））は「〜での乗船もOK」等の許容手段なので除いてから照合
    core = re.sub(r"＝[^＝]*＝|（[^（）]*）|\([^()]*\)", " ", name)
    hits = [kw for kw in METHOD_KEYWORDS if kw in core]
    hits = [kw for kw in hits if not any(kw != o and kw in o for o in hits)]
    methods.extend(hits)
    return {
        "name": name,
        "kind": kind,
        "targets": targets,
        "price": price,
        "price_text": price_text,
        "depart": depart,
        "return": ret,
        "meet": meet,
        "season": "",
        "days": days,
        "includes": clean(pop.get("料金に含まれるもの", "")),
        "url": url,
        "_methods": uniq(methods),
    }


def scrape_ship(fx, sid):
    # type: (Fetcher, str) -> Optional[Dict[str, object]]
    st, html = fx.get(BASE + "ship%s.html" % sid)
    if st != 200 or not html:
        if st == 404:
            log("gone ship%s" % sid)
        else:
            log("WARN ship%s fetch failed" % sid)
        return None
    rec = parse_ship(sid, html)
    if rec is None:
        log("WARN ship%s: not a ship page" % sid)
        return None
    st, ah = fx.get(BASE + "ship%sa.html" % sid)
    if st == 200 and ah:
        acc = parse_access(ah)
        rec["lat"], rec["lon"] = acc["lat"], acc["lon"]
        if acc["pickup"]:
            rec["access"] = (rec["access"] + " / " if rec["access"] else "") + "送迎: " + acc["pickup"]
    plans = []
    for row in rec.pop("_plan_rows"):
        href = row.get("href")
        pop = {}  # type: Dict[str, str]
        url = None
        if href and re.match(r"^popplan\d{5}-\d{5}\.html$", href):
            url = BASE + href
            st, ph = fx.get(url)
            if st == 200 and ph:
                pop = parse_popplan(ph)
        plans.append(build_plan(row, pop, url))
    methods = []
    for p in plans:
        methods.extend(p.pop("_methods"))
    rec["plans"] = plans
    rec["types"] = [k for k in ("乗合", "仕立") if any(p["kind"] == k for p in plans)]
    rec["targets"] = uniq([t for p in plans for t in p["targets"]])
    rec["methods"] = uniq(methods)
    return rec


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def save_json(path, records):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="castingnet 船宿クローラ")
    ap.add_argument("--limit", type=int, default=None, help="先頭N件だけ")
    ap.add_argument("--ids", default=None, help="カンマ区切りの船宿ID（例 00057,00164）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="出力JSON")
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    ap.add_argument("--interval", type=float, default=MIN_INTERVAL, help="リクエスト間隔（秒, >=0.8）")
    args = ap.parse_args()

    for d in (CACHE_DIR, os.path.dirname(LOG_PATH)):
        if not os.path.isdir(d):
            os.makedirs(d)
    fx = Fetcher(refresh=args.refresh, interval=args.interval)
    out = os.path.abspath(args.out)
    log("START castingnet out=%s limit=%s ids=%s" % (out, args.limit, args.ids))

    if args.ids:
        ids = uniq([x.strip().zfill(5) for x in args.ids.split(",") if x.strip()])
    else:
        ids = enumerate_ids(fx, args.limit)
    if args.limit:
        ids = ids[: args.limit]
    total = len(ids)
    records = []
    t0 = time.time()
    for i, sid in enumerate(ids, 1):
        try:
            rec = scrape_ship(fx, sid)
        except Exception as e:  # 1件の解析失敗で止めない
            log("ERROR ship%s: %s: %s" % (sid, e.__class__.__name__, e))
            rec = None
        if rec is not None:
            records.append(rec)
        if i % 10 == 0 or i == total:
            el = time.time() - t0
            log("progress %d/%d records=%d net=%d cache=%d elapsed=%.0fs" % (i, total, len(records), fx.n_net, fx.n_cache, el))
        if i % 100 == 0:
            save_json(out, records)
            log("saved partial %d records -> %s" % (len(records), out))
    save_json(out, records)
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
