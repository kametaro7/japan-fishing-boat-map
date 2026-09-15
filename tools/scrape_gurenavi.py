#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
グレナビ。(https://gurenavi.jp/) 瀬渡し船一覧 / 遊漁船一覧 クローラ

サイト構造（2026-09-15 時点で確認）
- WordPress。robots.txt の Disallow は /wp-admin/ /wp-includes/ のみ。
  sitemap.xml（misc/category/post）には一覧ページが出ないので、索引ページから辿る。
- 索引: /瀬渡し船一覧（16県へのリンク）、/遊漁船一覧（8県へのリンク）。
- 県ページ: div.entry-content に「h2（エリア名）＋ table.tablepress」の組が並ぶ。
  * 九州・沖縄型: th = 船名(colspan=2) / 主な魚種
      td.column-1 = <img>船名<br>港<br>電話、td.column-2 = HP / blog / FB ボタン、
      td.column-3 = 魚種（カンマ区切り。「クロ（メジナ,グレ）」のように括弧内にもカンマ）
  * 四国・本州型: th = 船名 / 船着場 / 船長名 / 連絡先
      船名セルに HP へのリンク。船着場は空、連絡先は愛媛の一部だけ。
      「船長名」は個人名なので読まない（出力しない）。
- 船ごとの詳細ページ・料金・出船時刻・座標は無い → plans=[]、lat/lon=null、address=null。
  表の内容は 2018 年頃の掲載のまま更新が少なく、リンク切れ・廃業が混じる。
- types: 瀬渡し一覧は ["渡船"]、遊漁船一覧は種別の記載が無いので []。
- 同じ県の瀬渡し一覧と遊漁船一覧の両方に同じ船（船名＋電話が一致）が載っている場合は
  1レコードにまとめる（src_url/src_id は索引順で先に出る瀬渡し側）。
- website: HP を優先し、無ければ blog（どちらも公式として扱う）。SNS は sns へ。
  入れない: 掲載/予約ポータル（つりそく・入れ食い・iタウンページ等）、サービス終了済みホスト
  （geocities.jp / blogs.yahoo.co.jp）、同じ第三者サイト配下を複数の船が共有しているページ
  （common.url_key が衝突し build.py の名寄せを誤らせるため）。
  個人名を含む Facebook 個人プロフィールURL（first.last.NN 形式）は sns に入れない。

使い方
  python3 tools/scrape_gurenavi.py                          # 全件
  python3 tools/scrape_gurenavi.py --limit 30               # 先頭30件
  python3 tools/scrape_gurenavi.py --pages 瀬渡し/長崎県,遊漁/福岡県 --out work/sources/gurenavi.sample.json
  python3 tools/scrape_gurenavi.py --ids '瀬渡し/長崎県/寿丸,遊漁/福岡県/ROSSO'
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
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (PREFS, nfkc, tel_display, norm_tel, norm_name, host_of, host_in, url_key,  # noqa: E402
                    NOT_OFFICIAL_HOSTS, SNS_HOSTS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "gurenavi")
OUT_DEFAULT = os.path.join(ROOT, "work", "sources", "gurenavi.json")
LOG_DEFAULT = os.path.join(ROOT, "work", "logs", "gurenavi.log")

BASE = "https://gurenavi.jp/"
INDEXES = [("瀬渡し", "瀬渡し船一覧"), ("遊漁", "遊漁船一覧")]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒（SPEC: 0.8秒以上）
FETCHED = "2026-09-15"

DISALLOW = ("/wp-admin/", "/wp-includes/")

# 船宿の公式サイトとして扱わないホスト（掲載/予約ポータル・電話帳・地図など）
PORTAL_HOSTS = tuple(NOT_OFFICIAL_HOSTS) + (
    "gurenavi.jp", "1091.co.jp", "tsurisoku.com", "itp.ne.jp", "fishing-motobu.com",
)
# サービス終了済み（リンク切れが確定している）ホスト
DEAD_HOSTS = ("geocities.jp", "geocities.co.jp", "blogs.yahoo.co.jp")

# 港名らしい語尾（セルに港以外の行が混じるときの選択用）
PORT_RE = re.compile(r"(港|漁港|船溜|船溜り|マリーナ|岸壁|桟橋|埠頭|ふ頭|波止場|船着場|浦)$")

# 船宿以外（釣り堀・釣り公園・筏/カセ専業）の判定
NON_BOAT_WORDS = ("釣り堀", "釣堀", "つり堀", "釣り公園", "海釣り公園")
RAFT_WORDS = ("筏", "イカダ", "いかだ", "カセ")
BOAT_WORDS = ("丸", "渡船", "瀬渡", "遊漁")

PUBLIC_KEYS = ["src", "src_id", "src_url", "name", "kana", "pref", "city", "address", "port", "lat", "lon",
               "tel", "website", "sns", "types", "targets", "methods", "holidays", "facilities", "capacity",
               "access", "description", "plans", "schedule_text", "fetched"]

_last_req = [0.0]
_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
_stats = {"requests": 0, "cache_hits": 0}
_log_path = [LOG_DEFAULT]


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

def page_url(path):
    """'瀬渡し船一覧/長崎県' → 'https://gurenavi.jp/瀬渡し船一覧/長崎県'（キャッシュキーは非エンコード形）"""
    return BASE + unquote(path).lstrip("/")


def cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


def fetch(url, refresh=False):
    """キャッシュ優先で取得。直列・間隔 MIN_INTERVAL 秒・429/503 は指数バックオフ（最大5回）。"""
    path = urlsplit(url).path
    if any(unquote(path).startswith(d) for d in DISALLOW):
        raise ValueError("robots.txt Disallow: %s" % url)
    p = cache_path(url)
    if not refresh and os.path.exists(p) and os.path.getsize(p) > 0:
        _stats["cache_hits"] += 1
        with open(p, "rb") as f:
            return f.read().decode("utf-8", "replace")
    if not os.path.isdir(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    parts = urlsplit(url)
    req_url = urlunsplit((parts.scheme, parts.netloc, quote(unquote(parts.path)), parts.query, ""))
    delay = 5.0
    for attempt in range(6):
        wait = MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
        try:
            r = _session.get(req_url, timeout=60)
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
            raise RuntimeError("HTTP %d: %s" % (r.status_code, url))
        body = r.content
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, p)
        return body.decode("utf-8", "replace")
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------- 正規化ヘルパ

def clean(s):
    s = nfkc(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def to_pref_exact(s):
    s = clean(s)
    for p in PREFS:
        if s == p:
            return p
    return None


DROP_PARAMS = re.compile(r"^(utm_\w+|fbclid|gclid|fref|hc_location|hc_ref|pnref|ref|__tn__|__xts__.*)$")


def clean_link(u):
    u = (u or "").strip()
    if not re.match(r"^https?://", u, re.I):
        return None
    u = re.sub(r"#.*$", "", u)
    parts = urlsplit(u)
    query = parts.query
    if query:
        q = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True)
             if not DROP_PARAMS.match(k) and not (re.match(r"^\d+$", k) and not v)]
        query = urlencode(q)
    path = parts.path
    # アメブロの記事URL → ブログのトップ
    if host_in(host_of(u), ("ameblo.jp",)):
        m = re.match(r"^(/[^/]+/)(entry-\d+\.html)?", path)
        if m:
            path = m.group(1)
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def classify_link(u):
    """'sns' / 'portal' / 'dead' / 'official' / None"""
    h = host_of(u)
    if not h:
        return None
    if host_in(h, SNS_HOSTS):
        return "sns"
    if host_in(h, DEAD_HOSTS):
        return "dead"
    if host_in(h, PORTAL_HOSTS):
        return "portal"
    return "official"


def personal_sns(u):
    """個人名入りの Facebook 個人プロフィール（例 facebook.com/taro.yamada.75）"""
    if not host_in(host_of(u), ("facebook.com", "fb.com")):
        return False
    path = urlsplit(u).path
    if re.match(r"^/(profile|people|pages)\b", path):
        return False  # profile.php?id=… は名前を含まない
    # Facebook が個人アカウントに自動で付ける「名.姓.数字」形式だけを個人とみなす
    # （tosen.daishinmaru のような事業者アカウントは残す）
    return bool(re.match(r"^/[A-Za-z]+\.[A-Za-z]+\.\d+/?$", path))


def split_targets(s):
    """'クロ（メジナ,グレ,）石鯛,真鯛' → ['クロ(メジナ・グレ)', '石鯛', '真鯛']
    括弧内のカンマでは切らず「・」に置き換え、閉じ括弧の直後に区切りが無くても切る。"""
    s = clean(s)
    if not s:
        return []
    out, buf, depth = [], [], 0
    for ch in s:
        if ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]" and depth > 0:
            depth -= 1
            while buf and buf[-1] in "・ ":
                buf.pop()
            buf.append(ch)
            if depth == 0:
                out.append("".join(buf))
                buf = []
        elif ch in ",、/" and depth == 0:
            out.append("".join(buf))
            buf = []
        elif ch in ",、" and depth > 0:
            buf.append("・")
        else:
            buf.append(ch)
    out.append("".join(buf))
    res = []
    for t in out:
        t = t.strip(" ・\t")
        if t and t not in res:
            res.append(t)
    return res


TEL_RE = re.compile(r"0\d{1,4}[-‐－―ー−(（)）\s]*\d{1,4}[-‐－―ー−(（)）\s]*\d{3,4}")


def looks_tel(line):
    return bool(norm_tel(line)) and bool(TEL_RE.search(nfkc(line)))


def cell_lines(td):
    for x in td.find_all(["img", "noscript", "script", "style"]):
        x.decompose()
    return [clean(x) for x in td.get_text("\n").split("\n") if clean(x)]


def is_non_boat(name):
    if any(w in name for w in NON_BOAT_WORDS):
        return True
    if any(w in name for w in RAFT_WORDS) and not any(w in name for w in BOAT_WORDS):
        return True
    return False


# ---------------------------------------------------------------- パーサ

def parse_index(html, index_path):
    s = BeautifulSoup(html, "lxml")
    c = s.find("div", class_="entry-content") or s
    out = []
    for a in c.find_all("a", href=True):
        href = unquote(urljoin(BASE, a["href"]))
        m = re.match(r"^https?://gurenavi\.jp/%s/([^/?#]+)/?$" % re.escape(index_path), href)
        if not m:
            continue
        pref = to_pref_exact(m.group(1))
        if pref and pref not in out:
            out.append(pref)
    return out


def header_labels(table):
    thead = table.find("thead")
    tr = thead.find("tr") if thead else table.find("tr")
    labels = []
    if not tr:
        return labels
    for th in tr.find_all(["th", "td"]):
        try:
            span = int(th.get("colspan") or 1)
        except ValueError:
            span = 1
        labels.extend([clean(th.get_text())] * span)
    return labels


def parse_page(html):
    """県ページ → 行 dict のリスト"""
    s = BeautifulSoup(html, "lxml")
    c = s.find("div", class_="entry-content")
    if c is None:
        return [], ["no entry-content"]
    rows, notes = [], []
    area = None
    n_tables = 0
    for el in c.find_all(["h2", "h3", "table"]):
        if el.find_parent("table") is not None:
            continue
        if el.name in ("h2", "h3"):
            area = clean(el.get_text())
            continue
        n_tables += 1
        labels = header_labels(el)
        if not labels or not labels[0].startswith("船名"):
            notes.append("unexpected header %s: %s" % (el.get("id"), labels))
        tbody = el.find("tbody") or el
        trs = tbody.find_all("tr", recursive=False) or tbody.find_all("tr")
        for tr in trs:
            if tr.find("th") and not tr.find("td"):
                continue  # 見出し行
            tds = tr.find_all("td", recursive=False)
            if not tds:
                continue
            row = parse_row(tds, labels)
            if row is None:
                continue
            row["area"] = area
            row["table"] = el.get("id")
            rows.append(row)
    if not n_tables:
        notes.append("no tables")
    return rows, notes


def parse_row(tds, labels):
    name, port, tel, links, targets = None, None, None, [], []
    extra = []
    seen_name_col = False
    for i, td in enumerate(tds):
        lab = labels[i] if i < len(labels) else ""
        if "船長" in lab or "代表" in lab:
            continue  # 個人名は読まない
        anchors = [(clean(a.get_text()), a.get("href")) for a in td.find_all("a", href=True)]
        if lab.startswith("船名") or (not lab and i == 0):
            links.extend(anchors)
            if seen_name_col:
                continue  # colspan=2 の2列目（リンクボタン）
            seen_name_col = True
            lines = cell_lines(td)
            if not lines:
                continue
            name = lines[0]
            others = []
            for ln in lines[1:]:
                if looks_tel(ln):
                    if not tel:
                        tel = tel_display(ln)
                    else:
                        extra.append(ln)
                else:
                    others.append(ln)
            if others:
                portish = [x for x in others if PORT_RE.search(x)]
                port = portish[0] if portish else others[0]
                extra.extend(x for x in others if x != port)
        elif "魚種" in lab:
            targets = split_targets(td.get_text(","))
            links.extend(anchors)
        elif "船着場" in lab or lab in ("港", "出船港"):
            t = clean(td.get_text(" "))
            if t:
                port = t
            links.extend(anchors)
        elif "連絡先" in lab or "電話" in lab or "TEL" in lab.upper():
            t = clean(td.get_text(" "))
            if t and not tel and norm_tel(t):
                tel = tel_display(t)
            links.extend(anchors)
        else:
            links.extend(anchors)
    if not name:
        return None
    return {"name": name, "port": port, "tel": tel, "links": links, "targets": targets, "extra": extra}


def pick_links(links):
    """[(text, href)] → (website, sns, dropped)"""
    hp, blog, other, sns, dropped = [], [], [], [], []
    for text, href in links:
        u = clean_link(href)
        if not u:
            continue
        c = classify_link(u)
        t = (text or "").lower()
        if c == "sns":
            if personal_sns(u):
                dropped.append(("personal-sns", "(facebook personal profile)"))
            elif u not in sns:
                sns.append(u)
        elif c in ("portal", "dead"):
            dropped.append((c, u))
        elif c == "official":
            if t in ("hp", "ホームページ", "home"):
                hp.append(u)
            elif "blog" in t or "ブログ" in t:
                blog.append(u)
            else:
                other.append(u)
    cands = hp + blog + other
    website = cands[0] if cands else None
    return website, sns, dropped


def make_record(row, kind, pref, url):
    website, sns, dropped = pick_links(row["links"])
    name = row["name"]
    return {
        "src": "gurenavi",
        "src_id": "%s/%s/%s" % (kind, pref, name),
        "src_url": url,
        "name": name,
        "kana": "",
        "pref": pref,
        "city": None,
        "address": None,
        "port": row["port"],
        "lat": None,
        "lon": None,
        "tel": row["tel"],
        "website": website,
        "sns": sns,
        "types": ["渡船"] if kind == "瀬渡し" else [],
        "targets": row["targets"],
        "methods": [],
        "holidays": "",
        "facilities": [],
        "capacity": None,
        "access": "",
        "description": "",
        "plans": [],
        "schedule_text": "",
        "fetched": FETCHED,
        "_area": row.get("area"),
        "_dropped": dropped,
        "_kind": kind,
        "_alt_ids": [],
    }


def public(rec):
    return dict((k, rec.get(k)) for k in PUBLIC_KEYS)


def save_json(path, data):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def merge_key(rec):
    return (rec["pref"], norm_name(rec["name"]), norm_tel(rec.get("tel") or "") or "")


def merge_into(base, rec):
    for t in rec["types"]:
        if t not in base["types"]:
            base["types"].append(t)
    for t in rec["targets"]:
        if t not in base["targets"]:
            base["targets"].append(t)
    for u in rec["sns"]:
        if u not in base["sns"]:
            base["sns"].append(u)
    if not base.get("website") and rec.get("website"):
        base["website"] = rec["website"]
    if not base.get("port") and rec.get("port"):
        base["port"] = rec["port"]
    if not base.get("tel") and rec.get("tel"):
        base["tel"] = rec["tel"]
    base["_alt_ids"].append(rec["src_id"])


def drop_shared_websites(records):
    """同じ url_key を別の船が共有している website（第三者サイト配下の紹介ページ）を外す。"""
    groups = {}
    for r in records:
        if r.get("website"):
            groups.setdefault(url_key(r["website"]), []).append(r)
    n = 0
    for k, rs in groups.items():
        boats = set((norm_name(r["name"]), norm_tel(r.get("tel") or "")) for r in rs)
        if len(boats) < 2:
            continue
        log("SHARED website key %s used by %d boats: %s -> dropped" % (
            k, len(rs), ", ".join(r["src_id"] for r in rs)))
        for r in rs:
            r["website"] = None
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="先頭 N 件だけ")
    ap.add_argument("--ids", default="", help="src_id（種別/県/船名）をカンマ区切りで")
    ap.add_argument("--pages", default="", help="対象ページ（瀬渡し/長崎県,遊漁/福岡県 のように）をカンマ区切りで")
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--log", default=LOG_DEFAULT)
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    args = ap.parse_args()
    _log_path[0] = os.path.abspath(args.log)
    args.out = os.path.abspath(args.out)

    ids = set(clean(x) for x in args.ids.split(",") if clean(x))
    only_pages = set(clean(x) for x in args.pages.split(",") if clean(x))
    log("start out=%s limit=%s ids=%d pages=%s" % (args.out, args.limit, len(ids), ",".join(sorted(only_pages)) or "all"))

    # 1) 索引 → 県ページの列挙（順序は索引順で固定）
    pages = []
    for kind, path in INDEXES:
        url = page_url(path)
        try:
            prefs = parse_index(fetch(url, args.refresh), path)
        except Exception as e:  # noqa
            log("ERROR index %s: %s" % (url, e))
            prefs = []
        log("index %s: %d prefs (%s)" % (path, len(prefs), ",".join(prefs)))
        for p in prefs:
            pages.append((kind, p, page_url("%s/%s" % (path, p))))
    if only_pages:
        pages = [pg for pg in pages if "%s/%s" % (pg[0], pg[1]) in only_pages]
    if ids:
        want = set("/".join(x.split("/")[:2]) for x in ids)
        pages = [pg for pg in pages if "%s/%s" % (pg[0], pg[1]) in want]
    total = len(pages)
    log("pages=%d" % total)

    # 2) 県ページ → レコード
    records = []
    by_key = {}
    by_name = {}  # (県, 正規化船名) → [rec]

    def find_cross_kind(cands, rec, kind):
        """瀬渡し一覧と遊漁船一覧で同じ船か：同県・同名で、電話／港／website のどれかが一致。
        （例 土肥釣りセンターは両一覧で電話の市外局番だけが違う）"""
        t = norm_tel(rec.get("tel") or "")
        w = url_key(rec["website"]) if rec.get("website") else None
        for b in cands:
            if b["_kind"] == kind:
                continue
            bt = norm_tel(b.get("tel") or "")
            if (t and bt and t == bt) or (not t and not bt):
                return b
            if rec.get("port") and b.get("port") and clean(rec["port"]) == clean(b["port"]):
                return b
            if w and b.get("website") and url_key(b["website"]) == w:
                return b
        return None

    seen_ids = set()
    excluded = []
    stats = {"rows": 0, "merged": 0, "dup_in_page": 0, "dropped_links": 0}
    last_saved = 0
    stop = False
    for i, (kind, pref, url) in enumerate(pages):
        try:
            html = fetch(url, args.refresh)
        except Exception as e:  # noqa
            log("ERROR page %s: %s" % (url, e))
            continue
        rows, notes = parse_page(html)
        for n in notes:
            log("WARN %s: %s" % (url, n))
        stats["rows"] += len(rows)
        n_new = 0
        for row in rows:
            rec = make_record(row, kind, pref, url)
            if ids and rec["src_id"] not in ids:
                continue
            if is_non_boat(rec["name"]):
                excluded.append(rec["src_id"])
                log("EXCLUDE non-boat %s" % rec["src_id"])
                continue
            if rec["_dropped"]:
                stats["dropped_links"] += len(rec["_dropped"])
                log("LINK dropped %s: %s" % (rec["src_id"], "; ".join("%s %s" % d for d in rec["_dropped"])))
            if row.get("extra"):
                log("NOTE extra lines %s: %s" % (rec["src_id"], " / ".join(row["extra"])))
            k = merge_key(rec)
            if rec["src_id"] in seen_ids:
                if k in by_key and by_key[k]["src_url"] == url:
                    # 同じページに同じ船（船名＋電話）が2行 → 重複として統合
                    stats["dup_in_page"] += 1
                    merge_into(by_key[k], rec)
                    continue
                # 同名の別の船 → 港（無ければ電話）を付けて区別
                rec["src_id"] = "%s(%s)" % (rec["src_id"], rec.get("port") or rec.get("tel") or len(records))
            base = find_cross_kind(by_name.get((pref, norm_name(rec["name"])), []), rec, kind)
            if base is not None:
                if norm_tel(base.get("tel") or "") != norm_tel(rec.get("tel") or "") and base.get("tel") and rec.get("tel"):
                    log("NOTE tel differs between lists %s: %s / %s" % (base["src_id"], base["tel"], rec["tel"]))
                merge_into(base, rec)
                stats["merged"] += 1
                log("MERGE %s -> %s" % (rec["src_id"], base["src_id"]))
                continue
            seen_ids.add(rec["src_id"])
            by_key.setdefault(k, rec)
            by_name.setdefault((pref, norm_name(rec["name"])), []).append(rec)
            records.append(rec)
            n_new += 1
            if args.limit and len(records) >= args.limit:
                stop = True
                break
        log("progress %d/%d pages (%s/%s: rows=%d new=%d) records=%d requests=%d cache=%d" % (
            i + 1, total, kind, pref, len(rows), n_new, len(records), _stats["requests"], _stats["cache_hits"]))
        if len(records) - last_saved >= 100:
            save_json(args.out, [public(x) for x in records])
            last_saved = len(records)
        if stop:
            break

    shared = drop_shared_websites(records)
    save_json(args.out, [public(x) for x in records])
    log("rows=%d merged(瀬渡し+遊漁)=%d dup_in_page=%d excluded=%d dropped_links=%d shared_website_dropped=%d" % (
        stats["rows"], stats["merged"], stats["dup_in_page"], len(excluded), stats["dropped_links"], shared))
    log("requests=%d cache_hits=%d" % (_stats["requests"], _stats["cache_hits"]))
    log("DONE %d records" % len(records))


if __name__ == "__main__":
    main()
