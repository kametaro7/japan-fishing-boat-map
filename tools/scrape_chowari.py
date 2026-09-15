#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
釣割 (https://www.chowari.jp/) クローラ

列挙: /allships/pref/01..47/ (全船リスト) ∪ sitemap*.xml の /ship/NNNNN/
      プランは sitemap の /ship/N/plan/M/ ∪ 船ページ内のプランリンク
取得: 1ホスト直列・間隔 >= 1.0 秒・キャッシュ (work/cache/chowari/<sha1>.html)
出力: work/sources/chowari.json (SPEC「掲載サイトレコード」の配列)
ログ: work/logs/chowari.log (done/total, 最終行 DONE <n> records)

使い方:
  python3 tools/scrape_chowari.py                 # 全件
  python3 tools/scrape_chowari.py --limit 30
  python3 tools/scrape_chowari.py --ids 00274,00007 --out work/sources/chowari.sample.json
  python3 tools/scrape_chowari.py --refresh       # キャッシュを使わず再取得
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
import warnings

warnings.filterwarnings("ignore")

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

BASE = "https://www.chowari.jp"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "chowari")
DEFAULT_OUT = os.path.join(ROOT, "work", "sources", "chowari.json")
DEFAULT_LOG = os.path.join(ROOT, "work", "logs", "chowari.log")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 1.0  # 秒 (SPEC は 0.8 以上)
FETCHED = "2026-09-15"

PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
         "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
         "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
         "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
         "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]

SNS_RE = re.compile(r"^https?://(?:[\w-]+\.)?(instagram\.com|facebook\.com|fb\.com|twitter\.com|x\.com|"
                    r"youtube\.com|youtu\.be|tiktok\.com|line\.me|lin\.ee|ameblo\.jp|note\.com)/", re.I)
OWN_SNS = ("chowari_jp", "chowarijp", "chowari.jp")


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
# 取得 (キャッシュ・直列・バックオフ)
# ----------------------------------------------------------------------------
class Fetcher(object):
    def __init__(self, log, refresh=False):
        self.log = log
        self.refresh = refresh
        self.sess = None
        self.new_session()
        self.last = 0.0
        self.n_net = 0
        self.n_cache = 0
        self.refreshed = set()
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
            # keep-alive の再利用で頻繁に切断されるため毎回接続し直す
            "Connection": "close",
        })

    @staticmethod
    def cache_path(url):
        return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")

    def get(self, url):
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
        delay = 3.0
        for attempt in range(6):
            wait = MIN_INTERVAL - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.sess.get(url, timeout=60)
            except requests.RequestException as e:
                self.last = time.time()
                self.n_err = getattr(self, "n_err", 0) + 1
                if attempt >= 5:
                    raise
                self.log("WARN %s %s: %s (retry in %.0fs)"
                         % (url, e.__class__.__name__, str(e)[:160], delay))
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
                os.rename(tmp, p)
                self.refreshed.add(url)
                return r.content.decode("utf-8", "replace")
            if r.status_code in (404, 410):
                open(miss, "w").close()
                self.refreshed.add(url)
                return None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                ra = r.headers.get("Retry-After")
                delay = max(delay, 10.0)
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
# 共通ユーティリティ
# ----------------------------------------------------------------------------
def clean(s):
    if s is None:
        return ""
    s = s.replace(" ", " ").replace("　", " ")
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


def zen2han(s):
    return s.translate(str.maketrans("０１２３４５６７８９：，", "0123456789:,"))


def is_hidden(tag):
    st = (tag.get("style") or "").replace(" ", "").lower()
    return "display:none" in st


def dl_pairs(container, dl_class_prefix):
    """container 内の <dl><dt>..</dt><dd>..</dd></dl> を (key, dd_tag, dl_tag) で返す。"""
    out = []
    if container is None:
        return out
    for dl in container.find_all("dl"):
        cls = " ".join(dl.get("class") or [])
        if dl_class_prefix and dl_class_prefix not in cls:
            continue
        dt = dl.find("dt")
        dd = dl.find("dd")
        if dt is None or dd is None:
            continue
        key = one_line(dt.get_text())
        out.append((key, dd, dl))
    return out


def dd_text(dd):
    d = BeautifulSoup(str(dd), "lxml")
    for br in d.find_all("br"):
        br.replace_with("\n")
    return clean(d.get_text())


# ----------------------------------------------------------------------------
# 列挙
# ----------------------------------------------------------------------------
def enumerate_all(fx, log):
    ships = set()
    plans = {}  # ship_id -> set(plan_id)
    idx = fx.get(BASE + "/sitemap.xml") or ""
    maps = re.findall(r"<loc>\s*(https://www\.chowari\.jp/sitemap\d*\.xml)\s*</loc>", idx)
    if not maps:
        maps = [BASE + "/sitemap%d.xml" % i for i in range(1, 6)]
    for sm in maps:
        x = fx.get(sm) or ""
        for sid in re.findall(r"<loc>\s*https://www\.chowari\.jp/ship/(\d+)/\s*</loc>", x):
            ships.add(sid)
        for sid, pid in re.findall(r"<loc>\s*https://www\.chowari\.jp/ship/(\d+)/plan/(\d+)/\s*</loc>", x):
            ships.add(sid)
            plans.setdefault(sid, set()).add(pid)
    n_sitemap = len(ships)
    allships = set()
    for n in range(1, 48):
        h = fx.get(BASE + "/allships/pref/%02d/" % n) or ""
        main = h.split('id="main', 1)[-1] if 'id="main' in h else h
        for sid in re.findall(r'href="(?:https://www\.chowari\.jp)?/ship/(\d+)/"', main):
            allships.add(sid)
    ships |= allships
    log("enumerate: sitemap ships=%d, allships=%d, union=%d, sitemap plans=%d"
        % (n_sitemap, len(allships), len(ships), sum(len(v) for v in plans.values())))
    return sorted(ships), plans


# ----------------------------------------------------------------------------
# 船ページ
# ----------------------------------------------------------------------------
def parse_latlon(html):
    found = {}
    for line in html.splitlines():
        s = line.strip()
        if s.startswith("//"):
            continue
        for m in re.finditer(r"var\s+([smp])_marker\s*=\s*\{\s*'lat'\s*:\s*'([-\d.]*)'\s*,\s*'lng'\s*:\s*'([-\d.]*)'", s):
            k, la, lo = m.group(1), m.group(2), m.group(3)
            try:
                la = float(la)
                lo = float(lo)
            except ValueError:
                continue
            if 20.0 <= la <= 46.5 and 122.0 <= lo <= 154.5 and k not in found:
                found[k] = (la, lo)
    for k in ("s", "m", "p"):  # 船着場 > 集合場所 > 駐車場
        if k in found:
            return found[k][0], found[k][1], k
    return None, None, None


def parse_ship(html, sid):
    soup = BeautifulSoup(html, "lxml")
    main = soup.find("main") or soup
    rec = {}
    h1 = main.select_one(".ship__header_title_name_txt")
    name = one_line(h1.get_text()) if h1 else ""
    kana_el = main.select_one(".ship__header_title_name_kana")
    kana = one_line(kana_el.get_text()) if kana_el else ""

    addr_items = [one_line(li.get_text()) for li in main.select(".ship__header_txt_address li")]
    pref = city = port = ""
    for a in main.select(".ship__header_txt_address li a"):
        href = a.get("href") or ""
        t = one_line(a.get_text())
        m = re.match(r"^/search/(\d{2})/(?:(\d+)/)?(?:(\d+)/)?$", href)
        if not m:
            continue
        if m.group(3):
            port = t
        elif m.group(2):
            city = t
        else:
            pref = t
    if not pref and addr_items:
        pref = addr_items[0]
    if pref and pref not in PREFS:
        cand = [p for p in PREFS if p.startswith(pref)]
        pref = cand[0] if len(cand) == 1 else pref

    info = {}
    detail = main.select_one("section.ship__detail")
    website = ""
    sns = []
    facilities = []
    for key, dd, dl in dl_pairs(detail, "ship__detail_list_item"):
        if key == "ホームページ":
            for a in dd.find_all("a"):
                href = (a.get("href") or "").strip()
                if href.startswith("http"):
                    website = href
                    break
            if not website:
                t = one_line(dd.get_text())
                if re.match(r"^https?://", t):
                    website = t
            continue
        if key == "設備":
            for li in dd.find_all("li"):
                if "off" in (li.get("class") or []):
                    continue
                t = one_line(li.get_text())
                if t:
                    facilities.append(t)
            continue
        if key == "所在地":
            d = BeautifulSoup(str(dd), "lxml")
            for sp in d.select(".ship__detail_list_item_txt_sup"):
                sp.decompose()
            info[key] = one_line(d.get_text())
            continue
        if is_hidden(dl):
            continue
        info[key] = dd_text(dd)

    # 住所: 「岡山県 倉敷市児島元浜町 元浜港」→ 県+市町村以下 (港名は外す)
    loc = info.get("所在地", "")
    address = ""
    if loc:
        s = loc
        if port and s.endswith(port):
            s = s[: -len(port)].strip()
        parts = s.split(" ", 1)
        if parts and parts[0] in PREFS:
            address = parts[0] + (parts[1].strip() if len(parts) > 1 else "")
        elif pref and not s.startswith(pref):
            address = pref + s.replace(" ", "", 1)
        else:
            address = s
        address = re.sub(r"^(%s)\s+" % "|".join(PREFS), r"\1", address)
    if address in PREFS:  # 県名だけなら住所なし扱い
        address = address

    # website / sns の振り分け
    def is_own(u):
        return bool(re.match(r"^https?://([\w-]+\.)*chowari\.jp", u, re.I))
    if website:
        if is_own(website):
            website = ""
        elif SNS_RE.match(website):
            sns.append(website)
            website = ""
    # 船ページ本文中の SNS リンク（サイト自身のアカウントは除外）
    scope = []
    for sel in ("section.ship__intro", "section.ship__detail", "section.ship__map"):
        el = main.select_one(sel)
        if el is not None:
            scope.append(el)
    for el in scope:
        for a in el.find_all("a"):
            href = (a.get("href") or "").strip()
            if SNS_RE.match(href) and not any(o in href for o in OWN_SNS):
                sns.append(href)
    sns = uniq(sns)

    # 電話（掲載されていないのが通常）
    tel = None
    for a in main.find_all("a"):
        href = a.get("href") or ""
        if href.startswith("tel:"):
            num = re.sub(r"[^\d-]", "", href[4:])
            if len(re.sub(r"\D", "", num)) >= 10:
                tel = num
                break

    cap = None
    m = re.search(r"(\d+)", zen2han(info.get("最大定員", "")))
    if m:
        cap = int(m.group(1))
        if cap <= 0:
            cap = None

    # アクセス
    acc = []
    for k in ("最寄インター", "最寄駅", "駐車場"):
        v = one_line(info.get(k, ""))
        if v and v not in ("無", "-"):
            acc.append("%s: %s" % (k, v))
        elif v == "無" and k == "駐車場":
            acc.append("駐車場: 無")
    mp = main.select_one("section.ship__map")
    if mp is not None:
        for key, dd, dl in dl_pairs(mp, "ship__map_common_transport_detail_list"):
            if key == "送迎":
                v = one_line(dd.get_text())
                if v and v != "無":
                    acc.append("送迎: %s" % v)
        pt = mp.select_one(".ship__map_place_txt")
        if pt is not None:
            v = one_line(pt.get_text())
            if v:
                acc.append(v)
    access = "／".join(acc)

    # 説明: 紹介文の冒頭1文（100字以内）
    desc = ""
    it = main.select_one(".ship__intro_txt")
    if it is not None:
        t = one_line(it.get_text())
        if t:
            first = re.split(r"(?<=[。！!？?])", t)[0].strip()
            if len(first) > 100:
                first = first[:99] + "…"
            desc = first

    holidays = ""
    for k in ("定休日", "休業日", "休船日"):
        if info.get(k):
            holidays = one_line(info[k])
            break

    lat, lon, lat_kind = parse_latlon(html)
    plan_ids = sorted(set(re.findall(r"/ship/%s/plan/(\d+)/" % re.escape(sid), html)))

    rec.update({
        "name": name, "kana": kana, "pref": pref or None, "city": city or None,
        "address": address or None, "port": port or None,
        "lat": lat, "lon": lon, "tel": tel, "website": website or None, "sns": sns,
        "holidays": holidays, "facilities": facilities, "capacity": cap,
        "access": access, "description": desc,
    })
    return rec, plan_ids, lat_kind


# ----------------------------------------------------------------------------
# プランページ
# ----------------------------------------------------------------------------
TIME_RE = re.compile(r"(?<!\d)([0-2]?\d|3[0-5])\s*[:：]\s*([0-5]\d)(?!\d)")


def times_in(text):
    """テキスト中の時刻を HH:MM で返す（24時以降は翌日表記なので 24 を引く）。"""
    t = zen2han(text or "")
    out = []
    for m in TIME_RE.finditer(t):
        h, mi = int(m.group(1)), int(m.group(2))
        if h >= 36:
            continue
        out.append("%02d:%02d" % (h % 24, mi))
    return out


def main_time_text(text):
    """出船/帰港時刻 dd の本体（※注記より前）。"""
    t = zen2han(one_line(text or ""))
    return t.split("※", 1)[0].strip()


def plan_times(text):
    """本体が時刻ならその時刻列。仕立の定型文「（通常はH:MM前後に出船しています）」も採る。
    「出船後7時間を目安」など時刻の無い本体は []（注記内の時刻からは推測しない）。"""
    body = main_time_text(text)
    if not body:
        return []
    m = re.search(r"通常は\s*(\d{1,2}\s*:\s*\d{2})\s*前後", body)
    if m:
        return times_in(m.group(1))
    if re.match(r"^\d{1,2}\s*:\s*\d{2}", body):
        return times_in(body)
    return []


def parse_price(text, kind):
    """通常価格（税込）の dd テキストから (price, price_text)"""
    t = one_line(text)
    if not t:
        return None, ""
    th = zen2han(t)
    m = re.search(r"([\d,]+)\s*円\s*/\s*(人|隻|名|艘)", th)
    if not m:
        m2 = re.search(r"([\d,]+)\s*円", th)
        if not m2:
            return None, t
        val = int(m2.group(1).replace(",", "") or 0)
        return (val if val > 0 else None), t + ("（税込）" if "税込" not in t else "")
    val = int(m.group(1).replace(",", "") or 0)
    unit = m.group(2)
    pt = t if "税込" in t else t + "（税込）"
    if val <= 0:
        return None, pt
    if unit in ("隻", "艘"):
        pt = "1隻" + re.sub(r"\s*円\s*/\s*[隻艘]", "円", pt, count=1)
        if kind == "仕立":
            return val, pt
        return None, pt
    return val, pt


def parse_plan(html, url):
    soup = BeautifulSoup(html, "lxml")
    main = soup.find("main") or soup
    h1 = main.select_one("h1.ship_plan__whole_unit_key_title")
    name = ""
    kind_h = ""
    if h1 is not None:
        st = h1.find("strong")
        sp = h1.find("span")
        name = one_line(st.get_text()) if st else one_line(h1.get_text())
        kind_h = one_line(sp.get_text()) if sp else ""
    info = {}
    lst = main.select_one(".ship_plan__whole_unit_detail_list")
    for key, dd, dl in dl_pairs(lst, "ship_plan__whole_unit_detail_list_item"):
        key = key.strip()
        if not key or key in info:
            continue
        if is_hidden(dl):
            continue
        info[key] = dd_text(dd)
    kind = one_line(info.get("乗合・仕立", "")) or kind_h
    if kind not in ("乗合", "仕立"):
        kind = kind_h if kind_h in ("乗合", "仕立") else kind

    targets = [one_line(li.get_text()) for li in main.select(".ship_plan__whole_unit_purpose_fishlist_item")]
    targets = uniq([t for t in targets if t])
    if not targets and info.get("ターゲット"):
        t = re.sub(r"など$", "", one_line(info["ターゲット"]))
        targets = uniq([x.strip() for x in re.split(r"[、,，/／]", t) if x.strip()])

    price_key = None
    for k in info:
        if k.startswith("通常価格"):
            price_key = k
            break
    price, price_text = (None, "")
    if price_key:
        price, price_text = parse_price(info[price_key], kind)

    dep_txt = one_line(info.get("出船時刻", ""))
    ret_txt = one_line(info.get("帰港予定時刻", ""))
    deps = plan_times(dep_txt)
    rets = plan_times(ret_txt)
    meet = one_line(info.get("集合時刻", ""))

    season = ""
    for k in ("期間", "開催期間", "実施期間", "シーズン", "出船期間"):
        if info.get(k):
            season = one_line(info[k])
            break
    days = ""
    for k in ("出船日", "開催日", "実施日", "出船曜日"):
        if info.get(k):
            days = one_line(info[k])
            break

    method = one_line(info.get("釣り方", "").split("\n")[0])
    method = re.split(r"[（(]", method)[0].strip() if method else ""

    base = {
        "name": name,
        "kind": kind or None,
        "targets": targets,
        "price": price,
        "price_text": price_text,
        "depart": None,
        "return": None,
        "meet": meet,
        "season": season,
        "days": days,
        "includes": one_line(info.get("料金に含まれるもの", "")),
        "url": url,
    }
    plans = []
    if len(deps) >= 2 and len(deps) == len(rets):
        # 複数便（午前/午後など）を分ける
        for i, (d, r) in enumerate(zip(deps, rets)):
            p = dict(base)
            p["depart"] = d
            p["return"] = r
            plans.append(p)
    else:
        p = dict(base)
        p["depart"] = deps[0] if len(deps) == 1 else None
        p["return"] = rets[0] if len(rets) == 1 else None
        if len(deps) >= 2:
            p["depart"] = deps[0]
        plans.append(p)
    extra = {"method": method, "dep_txt": dep_txt, "ret_txt": ret_txt, "keys": list(info.keys())}
    return plans, extra


# ----------------------------------------------------------------------------
# レコード組み立て
# ----------------------------------------------------------------------------
def build_record(fx, sid, sitemap_plans, log):
    url = "%s/ship/%s/" % (BASE, sid)
    html = fx.get(url)
    if html is None:
        log("MISS %s (404)" % url)
        return None
    if "ship__header_title_name_txt" not in html:
        log("SKIP %s (not a ship page)" % url)
        return None
    s, page_plans, lat_kind = parse_ship(html, sid)
    pids = sorted(set(page_plans) | set(sitemap_plans.get(sid, ())))
    plans = []
    methods = []
    for pid in pids:
        purl = "%s/ship/%s/plan/%s/" % (BASE, sid, pid)
        try:
            ph = fx.get(purl)
        except Exception as e:  # noqa
            log("ERR plan %s %s" % (purl, e))
            continue
        if ph is None or "ship_plan__whole_unit_detail_list" not in ph:
            continue
        ps, extra = parse_plan(ph, purl)
        plans.extend(ps)
        if extra["method"]:
            methods.append(extra["method"])
    rec = {
        "src": "chowari",
        "src_id": sid,
        "src_url": url,
        "name": s["name"],
        "kana": s["kana"],
        "pref": s["pref"],
        "city": s["city"],
        "address": s["address"],
        "port": s["port"],
        "lat": s["lat"], "lon": s["lon"],
        "tel": s["tel"],
        "website": s["website"],
        "sns": s["sns"],
        "types": uniq([p["kind"] for p in plans if p["kind"]]),
        "targets": uniq([t for p in plans for t in p["targets"]]),
        "methods": uniq(methods),
        "holidays": s["holidays"],
        "facilities": s["facilities"],
        "capacity": s["capacity"],
        "access": s["access"],
        "description": s["description"],
        "plans": plans,
        "schedule_text": "",
        "fetched": FETCHED,
    }
    return rec


def save(path, recs):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="先頭 N 件だけ")
    ap.add_argument("--ids", default="", help="船ID（カンマ区切り）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず再取得")
    args = ap.parse_args()

    out = args.out if os.path.isabs(args.out) else os.path.join(os.getcwd(), args.out)
    logp = args.log if os.path.isabs(args.log) else os.path.join(os.getcwd(), args.log)
    log = Logger(logp)
    fx = Fetcher(log, refresh=args.refresh)
    log("START chowari out=%s limit=%s ids=%s refresh=%s" % (out, args.limit, args.ids or "-", args.refresh))

    ships, sitemap_plans = enumerate_all(fx, log)
    if args.ids:
        want = [x.strip().zfill(5) for x in args.ids.split(",") if x.strip()]
        ships = want
    if args.limit:
        ships = ships[: args.limit]
    total = len(ships)
    est_plans = sum(len(sitemap_plans.get(s, ())) for s in ships)
    log("targets: %d ships, ~%d plan pages (sitemap)" % (total, est_plans))

    recs = []
    fails = []
    t0 = time.time()
    last_log = 0.0
    for i, sid in enumerate(ships, 1):
        try:
            r = build_record(fx, sid, sitemap_plans, log)
            if r is not None:
                recs.append(r)
        except Exception as e:  # noqa
            fails.append(sid)
            log("ERR ship %s %s: %s" % (sid, e.__class__.__name__, e))
        now = time.time()
        if i == total or i % 10 == 0 or now - last_log > 60:
            last_log = now
            log("progress %d/%d ships, records=%d, plans=%d, net=%d, cache=%d, elapsed=%.0fs"
                % (i, total, len(recs), sum(len(x["plans"]) for x in recs), fx.n_net, fx.n_cache, now - t0))
        if i % 100 == 0:
            save(out, recs)
    save(out, recs)
    if fails:
        log("FAILED ships (%d): %s" % (len(fails), ",".join(fails)))
    log("DONE %d records" % len(recs))


if __name__ == "__main__":
    main()
