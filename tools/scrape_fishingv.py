#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
釣りビジョン 釣果・施設情報 (https://www.fishing-v.jp/choka/) クローラ

列挙: sitemap-choka.xml の choka_detail.php?s=N（休止・過去施設を含む）
      ∪ 都道府県別一覧 /choka/detail.php?pref=NN（&pageID=M、1ページ5件）のカードの s=N
      ※一覧は「直近約1週間の釣果レポート」のフィードで、同じ施設が何度も出る。
        ここに出た施設を掲載中 (stale=false)、出ない施設を stale=true とする。
取得: 1ホスト直列・間隔 >= 0.9 秒・キャッシュ (work/cache/fishingv/<sha1(url)>.html)
出力: work/sources/fishingv.json（SPEC「掲載サイトレコード」の配列 + stale / last_report / report_count）
      work/sources/fishingv_excluded.json（船宿・渡船・カセ/筏 以外として除外した施設と判定根拠）
ログ: work/logs/fishingv.log（progress done/total, KEEP/EXCL の判定根拠, 最終行 DONE <n> records）

使い方:
  python3 tools/scrape_fishingv.py                      # 全件
  python3 tools/scrape_fishingv.py --limit 30
  python3 tools/scrape_fishingv.py --ids 229,7 --out work/sources/fishingv.sample.json
  python3 tools/scrape_fishingv.py --refresh-lists      # sitemap と一覧だけ取り直す
  python3 tools/scrape_fishingv.py --refresh            # すべて取り直す
Python 3.9 互換。
"""
from __future__ import print_function

import argparse
import datetime
import hashlib
import json
import math
import os
import re
import sys
import time
import unicodedata
import warnings

warnings.filterwarnings("ignore")

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

BASE = "https://www.fishing-v.jp"
CHOKA = BASE + "/choka/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "work", "cache", "fishingv")
DEFAULT_OUT = os.path.join(ROOT, "work", "sources", "fishingv.json")
DEFAULT_LOG = os.path.join(ROOT, "work", "logs", "fishingv.log")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
MIN_INTERVAL = 0.9  # 秒 (SPEC は 0.8 以上)
FETCHED = "2026-09-15"

PREFS = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
         "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
         "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
         "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
         "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]

SNS_RE = re.compile(r"^https?://(?:[\w-]+\.)*(instagram\.com|facebook\.com|fb\.com|twitter\.com|x\.com|"
                    r"youtube\.com|youtu\.be|tiktok\.com|line\.me|lin\.ee|threads\.net|"
                    r"threads\.com)/", re.I)  # ブログ(ameblo 等)は SPEC により website 扱い
OWN_SNS = ("fishingvision", "fishing-v", "FISHINGVISION", "mmjdI8D")

METHOD_WORDS = ["ひとつテンヤ", "一つテンヤ", "テンヤ", "タイラバ", "ジギング", "ジグ", "ルアー", "キャスティング",
                "コマセ", "泳がせ", "エギング", "ティップラン", "イカメタル", "胴突き", "天秤", "落とし込み", "電動",
                "手釣り", "フカセ", "カゴ", "サビキ", "テンビン", "エサ釣り", "トローリング", "タイカブラ",
                "磯釣り", "筏釣り", "カセ釣り", "投げ釣り", "夜焚き", "スルメイカ"]


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
        })

    @staticmethod
    def cache_path(url):
        return os.path.join(CACHE_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")

    def get(self, url, refresh=None):
        """本文(str)を返す。404/410 なら None。取得失敗は例外。"""
        p = self.cache_path(url)
        miss = p + ".404"
        refresh = self.refresh if refresh is None else (refresh or self.refresh)
        use_cache = (not refresh) or (url in self.refreshed)
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


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "")


def uniq(seq):
    out = []
    seen = set()
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def cut(s, n):
    s = one_line(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def tag_text(tag):
    """<br> を改行にしてテキスト化"""
    if tag is None:
        return ""
    d = BeautifulSoup(str(tag), "lxml")
    for br in d.find_all("br"):
        br.replace_with("\n")
    return clean(d.get_text())


def kata2hira(s):
    s = nfkc(s)
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def is_own(u):
    return bool(re.match(r"^https?://([\w-]+\.)*fishing-v\.jp", u or "", re.I))


# ----------------------------------------------------------------------------
# 列挙
# ----------------------------------------------------------------------------
def list_url(pref, page):
    if page <= 1:
        return "%sdetail.php?pref=%d" % (CHOKA, pref)
    return "%sdetail.php?pref=%d&pageID=%d" % (CHOKA, pref, page)


def list_count(html):
    m = re.search(r"該当<span>\s*([\d,]+)\s*</span>件", html or "")
    if m:
        return int(m.group(1).replace(",", ""))
    return 0  # 0件の県は「エラー｜お探しの釣果情報は見つかりませんでした」ページ


def list_ids(html):
    soup = BeautifulSoup(html or "", "lxml")
    box = soup.find(id="shop_latest")
    out = []
    if box is None:
        return out
    for c in box.select("div.choka"):
        m = re.search(r"choka_detail\.php\?s=(\d+)", str(c))
        if m:
            out.append(m.group(1))
    return out


def enumerate_all(fx, log, refresh_lists):
    idx = fx.get(BASE + "/sitemap.xml", refresh=refresh_lists) or ""
    maps = [u for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", idx) if "sitemap-choka" in u]
    if not maps:
        maps = [BASE + "/sitemap-choka.xml"]
    sitemap_ids = set()
    for sm in maps:
        x = fx.get(sm, refresh=refresh_lists) or ""
        sitemap_ids |= set(re.findall(r"choka_detail\.php\?s=(\d+)", x))

    active = {}  # sid -> pref code
    n_cards = 0
    for n in range(1, 48):
        h = fx.get(list_url(n, 1), refresh=refresh_lists) or ""
        cnt = list_count(h)
        ids = list_ids(h) if cnt > 0 else []
        pages = int(math.ceil(cnt / 5.0)) if cnt > 0 else 0
        for pg in range(2, pages + 1):
            ids += list_ids(fx.get(list_url(n, pg), refresh=refresh_lists) or "")
        if cnt and len(ids) != cnt:
            log("WARN pref=%d count=%d but cards=%d" % (n, cnt, len(ids)))
        n_cards += len(ids)
        for sid in ids:
            active.setdefault(sid, n)
        if cnt:
            log("list pref=%02d reports=%d pages=%d shops=%d" % (n, cnt, pages, len(set(ids))))
    log("enumerate: sitemap=%d, list cards=%d, list shops=%d, list-only(not in sitemap)=%d, union=%d"
        % (len(sitemap_ids), n_cards, len(active), len(set(active) - sitemap_ids),
           len(sitemap_ids | set(active))))
    return sitemap_ids, active


# ----------------------------------------------------------------------------
# 詳細ページ
# ----------------------------------------------------------------------------
def shop_rows(soup):
    """#shop_data の <tr><th>key</th><td>..</td></tr> → [(key, td)]"""
    out = []
    sd = soup.find(id="shop_data")
    if sd is None:
        return out
    for tr in sd.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th is None or td is None:
            continue
        out.append((one_line(th.get_text()), td))
    return out


def icon_alts(html, key):
    """設備/サービスのアイコン alt（alt の前に全角空白があり HTML パーサで属性が壊れるので生 HTML から取る）"""
    m = re.search(r"<th[^>]*>\s*%s\s*</th>\s*<td[^>]*>(.*?)</td>" % re.escape(key), html, re.S)
    if not m:
        return []
    return uniq([a.strip() for a in re.findall(r'alt="([^"]+)"', m.group(1))])


def parse_latlon(html):
    m = re.search(r"var\s+maplatlng\s*=\s*(\[.*?\])\s*;", html, re.S)
    if not m:
        return None, None, None
    try:
        pts = json.loads(m.group(1))
    except ValueError:
        return None, None, None
    good = []
    for p in pts:
        try:
            la = float(p.get("latitude") or 0)
            lo = float(p.get("longitude") or 0)
        except (TypeError, ValueError):
            continue
        if 20.0 <= la <= 46.5 and 122.0 <= lo <= 154.5:
            good.append((str(p.get("type_cd") or ""), la, lo))
    if not good:
        return None, None, None
    # type_cd: 2=出港場所, 1(その他)=店舗/受付, 3=駐車場（ページ内の地図 JS の凡例より）
    for want in ("2", "1", "3"):
        for t, la, lo in good:
            if t == want:
                return la, lo, t
    return good[0][1], good[0][2], good[0][0]


def split_address(raw, pref, port):
    a = clean(raw).replace("\n", " ")
    a = re.sub(r"〒?\s*\d{3}\s*[-‐－ー]\s*\d{4}", "", a).strip()
    a = re.sub(r"\s+", " ", a)
    if port:
        a = re.sub(r"\s*%s$" % re.escape(port), "", a).strip()
    a = re.sub(r"\s+[^\s\d]{1,12}(?:漁港|港)$", "", a).strip()  # 末尾の「 垂水漁港」など
    p = None
    for x in PREFS:
        if a.startswith(x):
            p = x
            break
    if p is None and pref and a:
        a = pref + a
        p = pref
    if p:
        a = p + a[len(p):].lstrip()
    rest = a[len(p):] if p else a
    city = None
    if rest:
        i_gun = rest.find("郡", 1)
        i_shi = rest.find("市", 1)
        m = None
        if i_gun > 0 and (i_shi < 0 or i_gun < i_shi):
            m = re.match(r"^(.+?郡.+?[町村])", rest)
        elif i_shi > 0:
            j = i_shi + 1
            if rest[j:j + 1] == "市":  # 四日市市・廿日市市・野々市市
                j += 1
            city = rest[:j]
            m2 = re.match(r"^([^\s\d０-９]{1,4}?区)", rest[j:])
            if m2 and PREFS.index(p) + 1 in (1, 4, 11, 12, 14, 15, 22, 23, 26, 27, 28, 33, 34, 40, 43) if p else False:
                city += m2.group(1)
        else:
            m = re.match(r"^(.+?[区町村])", rest)
        if m:
            city = m.group(1)
    return (a or None), city


TIME_HM = r"(\d{1,2})\s*[:：]\s*(\d{2})"
TIME_JP = r"(\d{1,2})\s*時\s*(?:(\d{1,2})\s*分|(半))?"


def _hm(h, mi, half=None, wrap=False):
    h = int(h)
    mi = int(mi) if mi else (30 if half else 0)
    if mi > 59:
        return None
    if wrap:
        if h > 29:
            return None
        h %= 24
    elif h > 23:
        return None
    return "%02d:%02d" % (h, mi)


def find_time(text, words, wrap=False):
    t = nfkc(text or "")
    w = "(?:%s)" % words
    for pat in (TIME_HM + r"\s*(?:頃|ごろ|位|くらい)?\s*(?:に|から)?\s*" + w,
                w + r"\s*(?:時間|時刻|予定)?\s*(?:は|[:：])?\s*(?:AM|午前)?\s*" + TIME_HM):
        m = re.search(pat, t)
        if m:
            return _hm(m.group(1), m.group(2), wrap=wrap)
    for pat in (TIME_JP + r"\s*(?:頃|ごろ|位|くらい)?\s*(?:に|から)?\s*" + w,
                w + r"\s*(?:時間|時刻|予定)?\s*(?:は|[:：])?\s*(?:AM|午前)?\s*" + TIME_JP):
        m = re.search(pat, t)
        if m:
            return _hm(m.group(1), m.group(2), m.group(3), wrap=wrap)
    return None


DEP_WORDS = r"出船|出港|出航"
RET_WORDS = r"帰港|沖上がり|沖上り|着岸|納竿"


def plan_times(name, note):
    dep = find_time(name, DEP_WORDS) or find_time(note, DEP_WORDS)
    ret = find_time(name, RET_WORDS, wrap=True) or find_time(note, RET_WORDS, wrap=True)
    if not dep:
        for s in (name, note):
            t = nfkc(s or "")
            m = re.search(TIME_HM + r"\s*[~〜～\-－ー]\s*" + TIME_HM, t)
            if m:
                dep = _hm(m.group(1), m.group(2))
                ret = ret or _hm(m.group(3), m.group(4), wrap=True)
                break
            m = re.search(TIME_HM + r"\s*便", t) or re.search(r"(\d{1,2})\s*時\s*(?:(\d{1,2})\s*分)?\s*便", t)
            if m:
                dep = _hm(m.group(1), m.group(2))
                break
    return dep, ret


PER_BOAT_RE = re.compile(r"隻|艘|チャーター|貸切|貸し切り|仕立")
PER_PERSON_RE = re.compile(r"[1１一]\s*[人名]|お一人|おひとり|/人|/名|／人|／名|人|名様")


def parse_price(text, name):
    """料金セル → (price, price_text, per_boat)"""
    t = one_line(text)
    if not t:
        return None, "", False
    th = nfkc(t)
    th = re.sub(r"(?<![\d.])(\d{1,3})\.(\d{3})(?=\s*円)", r"\1,\2", th)  # 「13.200円」の誤記
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(万)?\s*円", th)
    if not m:
        m = re.search(r"[¥￥]\s*(\d{1,3}(?:,\d{3})+|\d{3,7})()", th)
    if not m:
        m = re.match(r"^\s*(\d{1,3}(?:,\d{3})+|\d{4,7})()\s*(?:[~〜～\-]|$|\(|（)", th)
    if not m:
        return None, t, False
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, t, False
    if m.group(2):
        val *= 10000
    val = int(round(val))
    around = th[max(0, m.start() - 8): m.end() + 6]
    per_person = bool(PER_PERSON_RE.search(around))
    per_boat = bool(re.search(r"隻|艘|チャーター|貸切|貸し切り", around)) or \
        (not per_person and bool(PER_BOAT_RE.search(nfkc(name) + " " + th)))
    if val < 1000 or val > 2000000:
        return None, t, per_boat
    if per_boat and not re.search(r"隻|艘", t):
        t = "1隻 " + t
    return val, t, per_boat


def season_text(months):
    s = set(months)
    if not s:
        return ""
    if len(s) == 12:
        return "通年"
    runs = []
    for mo in range(1, 13):
        prev = 12 if mo == 1 else mo - 1
        if mo in s and prev not in s:
            e = mo
            while (e % 12) + 1 in s:
                e = (e % 12) + 1
            runs.append((mo, e))
    runs.sort()
    return "、".join(("%d月" % a) if a == b else ("%d月〜%d月" % (a, b)) for a, b in runs)


def split_targets(td):
    items = []
    for line in tag_text(td).split("\n"):
        for x in re.split(r"[、,，/／]", line):
            x = x.strip(" 　・")
            if x and x not in ("-", "－", "ー"):
                items.append(x)
    return uniq(items)


def parse_plans(soup, url, fac_kind):
    box = soup.find(id="tsurimono")
    if box is None:
        return []
    tbl = box.find("table")
    if tbl is None:
        return []
    trs = tbl.find_all("tr")
    if not trs:
        return []
    ths = trs[0].find_all("th")
    heads = [one_line(th.get_text()).replace(" ", "") for th in ths]
    if not heads:
        return []
    i_name = heads.index("便名") if "便名" in heads else 0
    i_tgt = heads.index("対象魚") if "対象魚" in heads else 1
    i_price = heads.index("料金") if "料金" in heads else 2
    i_note = heads.index("備考") if "備考" in heads else len(heads) - 1
    months = {}
    for i, th in enumerate(ths):
        if "calendar" in (th.get("class") or []):
            try:
                months[i] = int(one_line(th.get_text()))
            except ValueError:
                pass
    plans = []
    for tr in trs[1:]:
        tds = tr.find_all("td")
        if len(tds) != len(heads):
            continue
        name = one_line(tds[i_name].get_text())
        targets = split_targets(tds[i_tgt])
        note = one_line(tag_text(tds[i_note]))
        price, price_text, per_boat = parse_price(tag_text(tds[i_price]), name)
        if not name and not targets and not price_text:
            continue
        on = [mo for i, mo in months.items() if "enable" in (tds[i].get("class") or [])]
        blob = nfkc(" ".join([name, price_text, note]))
        has_nori = bool(re.search(r"乗合|乗り合い", blob))
        if per_boat and not (has_nori and not re.search(r"隻|艘", blob)):
            kind = "仕立"
        elif has_nori:
            kind = "乗合"  # 「乗合/仕立」併記は料金が1人あたりなので乗合扱い
        elif re.search(r"仕立|チャーター|貸切|貸し切り", blob):
            kind = "仕立"
        elif fac_kind in ("渡船", "カセ・筏") and re.search(r"磯|筏|いかだ|カセ|渡", blob):
            kind = "渡船"
        else:
            kind = None
        dep, ret = plan_times(name, note)
        days = cut(note, 40) if re.search(r"曜|土日|平日|祝|毎日", note) else ""
        includes = cut(note, 60) if re.search(r"付|込|含", note) else ""
        mm = re.search(r"集合[^、。\s]{0,20}", nfkc(note))
        plans.append({
            "name": name,
            "kind": kind,
            "targets": targets,
            "price": price,
            "price_text": cut(price_text, 80),
            "depart": dep,
            "return": ret,
            "meet": mm.group(0) if mm else "",
            "season": season_text(on),
            "days": days,
            "includes": includes,
            "note": cut(note, 80),
            "url": url + "#fish-list",
        })
    return plans


# --- 施設種別の判定 -----------------------------------------------------------
NAME_POND = re.compile(r"釣り?堀|つり堀|つりぼり|海上釣|海上つり")
NAME_MANAGED = re.compile(r"管理釣|フィッシングエリア|フィッシングパーク|トラウト|ます釣|マス釣|鱒|ニジマス|渓流|キャンプ場|釣り?池|湖畔")
NAME_PARK = re.compile(r"公園|海づり|海釣り?施設|つり施設|釣り?施設|桟橋|フィッシングピア|釣りピア|海の駅|道の駅|釣り?センター|つりセンター|ランド")
NAME_SHOP = re.compile(r"釣具|釣り具|つり具|フィッシングショップ|プロショップ|ルアーショップ|餌店|エサ店|えさ店|餌屋|エサ屋|"
                       r"タックル|上州屋|キャスティング|イシグロ|フィッシングマックス|かめや|ブンブン|釣りエサ")
NAME_FERRY = re.compile(r"渡船|渡し|瀬渡|磯渡")
NAME_BOAT = re.compile(r"丸|船宿|釣船|釣り船|つり船|遊漁|チャーター|号|マリン|クルーズ|フィッシングガイド|ガイドサービス|船長")
TXT_BOAT = re.compile(r"乗合|乗り合い|仕立|出船|出港|渡船|瀬渡|磯渡|沖釣|船長|乗船|チャーター|遊漁船")
TXT_KASE = re.compile(r"カセ(?!ット)|筏|いかだ|イカダ")
TXT_NONBOAT = re.compile(r"入園|開園|閉園|入場料|釣台|釣り台|釣り?堀|海上釣|入漁料|釣り放題|管理釣|放流|桟橋から")
RENTAL = re.compile(r"レンタルボート|貸しボート|貸ボート|手漕ぎボート")


def classify(name, info, plans, freshwater):
    """(keep, kind, reason)"""
    nm = nfkc(name)
    text = nfkc(" ".join([info.get("紹介文", ""), info.get("料金形態", ""), info.get("備考", ""),
                          info.get("営業時間", ""), info.get("_guide", "")] +
                         [" ".join([p["name"], p["price_text"], p["note"]]) for p in plans]))
    boat_fac = one_line(info.get("船の設備", ""))
    if freshwater:
        return False, "淡水", "breadcrumb:sea=0"
    m = NAME_POND.search(nm)
    if m:
        return False, "釣り堀", "name:" + m.group(0)
    m = NAME_MANAGED.search(nm)
    if m:
        return False, "管理釣り場", "name:" + m.group(0)
    tk = TXT_KASE.search(text)
    tb = TXT_BOAT.search(text)
    tn = TXT_NONBOAT.search(text)
    m = NAME_PARK.search(nm)
    if m and not NAME_FERRY.search(nm):
        if tk and tb and not tn and "センター" in m.group(0):
            return True, "カセ・筏", "name:%s+text:%s,%s" % (m.group(0), tk.group(0), tb.group(0))
        return False, "釣り公園・施設", "name:" + m.group(0)
    if boat_fac:
        if NAME_FERRY.search(nm):
            return True, "渡船", "row:船の設備+name:" + NAME_FERRY.search(nm).group(0)
        return True, "船宿", "row:船の設備"
    m = NAME_SHOP.search(nm)
    if m:
        return False, "釣具店", "name:" + m.group(0)
    m = NAME_FERRY.search(nm)
    if m:
        return True, "渡船", "name:" + m.group(0)
    r = RENTAL.search(nm + " " + text)
    if r and not re.search(r"乗合|仕立|出船|遊漁船", text):
        return False, "レンタルボート", "text:" + r.group(0)
    if tk and (tb or not tn):
        return True, "カセ・筏", "text:%s%s" % (tk.group(0), ("," + tb.group(0)) if tb else "")
    mb = NAME_BOAT.search(nm)
    if mb and not tn:
        return True, "船宿", "name:" + mb.group(0)
    if tb and not tn:
        return True, "船宿", "text:" + tb.group(0)
    if mb and tb:
        return True, "船宿", "name:%s+text:%s (nonboat text:%s)" % (mb.group(0), tb.group(0), tn.group(0))
    return False, "不明", "no boat evidence" + ((" / nonboat text:" + tn.group(0)) if tn else "")


def parse_detail(html, sid, url):
    soup = BeautifulSoup(html, "lxml")
    info = {}
    tds = {}
    for key, td in shop_rows(soup):
        if key in info:
            continue
        info[key] = tag_text(td)
        tds[key] = td

    # 施設名（カナ）
    raw_name = one_line(info.get("施設名", ""))
    name, kana = raw_name, ""
    m = re.match(r"^(.*\S)\s*[（(]([ぁ-ゖァ-ヺー・\s　ｦ-ﾟ･]+)[)）]\s*$", raw_name)
    if m:
        name, kana = m.group(1).strip(), kata2hira(m.group(2)).strip()
    if not name:
        h2 = soup.find("h2", id="shop-detail")
        if h2 is not None:
            name = re.sub(r"の店舗情報$", "", one_line(h2.get_text()))

    # パンくず: 県 / エリア / 港
    bread = soup.select("ul.shop_bread_list li")
    bt = [one_line(li.get_text()) for li in bread]
    pref = bt[0] if bt and bt[0] in PREFS else None
    port = bt[2] if len(bt) >= 3 else None
    freshwater = False
    for a in soup.select("ul.shop_bread_list a"):
        if re.search(r"[?&]sea=0\b", a.get("href") or ""):
            freshwater = True

    address, city = split_address(info.get("住所", ""), pref, port)
    if not pref and address:
        for x in PREFS:
            if address.startswith(x):
                pref = x
                break

    # 電話
    tel = None
    td = tds.get("電話番号")
    if td is not None:
        cand = [a.get("href", "")[4:] for a in td.find_all("a") if (a.get("href") or "").startswith("tel:")]
        cand.append(td.get_text())
        for c in cand:
            mm = re.search(r"0\d{1,4}[-‐－(（]?\d{1,4}[-‐－)）]?\d{3,4}", nfkc(c))
            if mm and len(re.sub(r"\D", "", mm.group(0))) in (10, 11):
                tel = re.sub(r"[‐－(（)）]", "-", mm.group(0))
                break

    # 公式サイト / SNS
    website = None
    sns = []
    td = tds.get("リンク")
    if td is not None:
        for a in td.find_all("a"):
            href = (a.get("href") or "").strip()
            if not href.startswith("http") or is_own(href):
                continue
            if SNS_RE.match(href):
                sns.append(href)
            elif website is None:
                website = href
    sd = soup.find(id="shop_data")
    blobs = []
    if sd is not None:
        for a in sd.find_all("a"):
            blobs.append(a.get("href") or "")
        blobs += re.findall(r"https?://[^\s\"'<>（）()、。]+", sd.get_text(" "))
    for u in blobs:
        u = u.strip()
        if SNS_RE.match(u) and not any(o in u for o in OWN_SNS):
            sns.append(u)
    sns = uniq(sns)

    # 設備
    facilities = icon_alts(html, "設備")
    bf = one_line(info.get("船の設備", ""))
    if bf:
        for x in re.split(r"[、,，/／\n]|\s{1,}", info.get("船の設備", "")):
            x = x.strip(" ・")
            if 1 < len(x) <= 12 and not re.search(r"[:：]", x):
                facilities.append(x)
    facilities = uniq(facilities)

    # 釣行の流れ
    g = soup.find(id="guide_container")
    info["_guide"] = one_line(g.get_text(" ")) if g is not None else ""

    # アクセス
    acc = []
    mb = soup.find(id="map_box")
    if mb is not None:
        for p in mb.find_all("p"):
            t = one_line(re.sub(r"^\s*店舗住所", "", p.get_text(" ")))
            if t:
                acc.append(t)
    pk = one_line(info.get("駐車場", ""))
    if pk:
        acc.append("駐車場: " + pk)
    access = cut("／".join(acc), 300)

    desc = ""
    intro = clean(info.get("紹介文", ""))
    if intro:
        acc_s = ""
        for s in re.split(r"(?<=[。！!？?])|\n", intro):
            s = s.strip()
            if not s or re.fullmatch(r"[=＝\-－ー_＿~〜・*＊■□◆◇●○]+", s):
                continue
            acc_s += s
            if len(acc_s) >= 25:
                break
        desc = cut(acc_s, 100)

    holidays = cut(info.get("定休日", ""), 100)

    cap = None
    mm = re.search(r"定員\s*[:：]?\s*(\d{1,3})\s*[名人]", nfkc(" ".join([intro, bf, info.get("備考", ""),
                                                                 info.get("料金形態", "")])))
    if mm and int(mm.group(1)) > 0:
        cap = int(mm.group(1))

    lat, lon, lat_kind = parse_latlon(html)

    # 釣果の新しさ（一覧の stale 判定の補助）
    last_report = None
    report_count = None
    i0 = html.find("の釣果情報</h2>")
    i1 = html.find('id="shop-detail"')
    if i0 >= 0:
        seg = html[i0: i1 if i1 > i0 else i0 + 200000]
        md = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*\(", seg)
        if md:
            last_report = "%s-%02d-%02d" % (md.group(1), int(md.group(2)), int(md.group(3)))
        mc = re.search(r"全\s*([\d,]+)\s*件", re.sub(r"<[^>]+>", " ", seg))
        if mc:
            report_count = int(mc.group(1).replace(",", ""))

    return {
        "name": name, "kana": kana, "pref": pref, "city": city, "address": address, "port": port,
        "lat": lat, "lon": lon, "lat_kind": lat_kind, "tel": tel, "website": website, "sns": sns,
        "facilities": facilities, "capacity": cap, "access": access, "description": desc,
        "holidays": holidays, "info": info, "soup": soup, "freshwater": freshwater,
        "last_report": last_report, "report_count": report_count,
    }


def schedule_text(info):
    parts = []
    pf = one_line(info.get("料金形態", ""))
    if pf:
        parts.append("料金形態: " + cut(pf, 150))
    oh = one_line(info.get("営業時間", ""))
    if oh and re.search(r"\d", oh):
        parts.append("営業時間: " + cut(oh, 60))
    sents = []
    for k in ("_guide", "備考", "紹介文", "料金形態"):
        for s in re.split(r"(?<=[。！!])|\n|※", clean(info.get(k, ""))):
            s = one_line(s)
            if s and re.search(r"出船|出港|集合|帰港|沖上が", s) and s not in sents:
                sents.append(cut(s, 80))
    parts += sents[:2]
    return cut(" ／ ".join(parts), 300) if parts else ""


def text_types(info, plans, fac_kind):
    t = nfkc(" ".join([info.get("紹介文", ""), info.get("料金形態", ""), info.get("備考", ""), info.get("_guide", "")] +
                      [p["name"] + " " + p["price_text"] for p in plans]))
    out = []
    if re.search(r"乗合|乗り合い", t) or any(p["kind"] == "乗合" for p in plans):
        out.append("乗合")
    if re.search(r"仕立|チャーター|貸切|貸し切り", t) or any(p["kind"] == "仕立" for p in plans):
        out.append("仕立")
    if fac_kind in ("渡船", "カセ・筏") or re.search(r"渡船|瀬渡|磯渡", t):
        out.append("渡船")
    return out


def methods_of(info, plans):
    t = nfkc(" ".join([p["name"] for p in plans] + [info.get("料金形態", "")]))
    out = []
    for w in METHOD_WORDS:
        if w in t:
            out.append(w)
    # 「テンヤ」は「ひとつテンヤ」と重複させない
    if "テンヤ" in out and ("ひとつテンヤ" in out or "一つテンヤ" in out):
        out.remove("テンヤ")
    if "ジグ" in out and "ジギング" in out:
        out.remove("ジグ")
    return out


# ----------------------------------------------------------------------------
# レコード組み立て
# ----------------------------------------------------------------------------
def build_record(fx, sid, active, log):
    url = "%schoka_detail.php?s=%s" % (CHOKA, sid)
    html = fx.get(url)
    if html is None:
        log("MISS s=%s (404)" % sid)
        return None, None
    if "id=\"shop_data\"" not in html:
        t = re.search(r"<title>(.*?)</title>", html, re.S)
        log("MISS s=%s (no shop data: %s)" % (sid, one_line(t.group(1))[:40] if t else "-"))
        return None, None
    d = parse_detail(html, sid, url)
    info = d["info"]
    stale = sid not in active
    plans0 = parse_plans(d["soup"], url, None)
    keep, fac_kind, reason = classify(d["name"], info, plans0, d["freshwater"])
    log("%s s=%s %s kind=%s by=%s stale=%s" % ("KEEP" if keep else "EXCL", sid, d["name"], fac_kind, reason,
                                              "1" if stale else "0"))
    if not keep:
        return None, {
            "src": "fishingv", "src_id": sid, "src_url": url, "name": d["name"], "pref": d["pref"],
            "address": d["address"], "tel": d["tel"], "kind": fac_kind, "reason": reason, "stale": stale,
            "last_report": d["last_report"],
        }
    plans = parse_plans(d["soup"], url, fac_kind)
    rec = {
        "src": "fishingv",
        "src_id": sid,
        "src_url": url,
        "name": d["name"],
        "kana": d["kana"],
        "pref": d["pref"],
        "city": d["city"],
        "address": d["address"],
        "port": d["port"],
        "lat": d["lat"], "lon": d["lon"],
        "tel": d["tel"],
        "website": d["website"],
        "sns": d["sns"],
        "types": text_types(info, plans, fac_kind),
        "targets": uniq([t for p in plans for t in p["targets"]]),
        "methods": methods_of(info, plans),
        "holidays": d["holidays"],
        "facilities": d["facilities"],
        "capacity": d["capacity"],
        "access": d["access"],
        "description": d["description"],
        "plans": plans,
        "schedule_text": schedule_text(info),
        "fetched": FETCHED,
        "stale": stale,
        "last_report": d["last_report"],
        "report_count": d["report_count"],
    }
    return rec, None


def save(path, recs):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def excluded_path(out):
    base, ext = os.path.splitext(out)
    if base.endswith("fishingv"):
        return base + "_excluded" + ext
    return base + ".excluded" + ext if "fishingv" in os.path.basename(base) else base + "_fishingv_excluded" + ext


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="先頭 N 件だけ（掲載中→休止の順）")
    ap.add_argument("--ids", default="", help="施設ID s=（カンマ区切り）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--refresh", action="store_true", help="キャッシュを使わず全て再取得")
    ap.add_argument("--refresh-lists", action="store_true", help="sitemap と都道府県一覧だけ再取得")
    args = ap.parse_args()

    out = args.out if os.path.isabs(args.out) else os.path.join(os.getcwd(), args.out)
    logp = args.log if os.path.isabs(args.log) else os.path.join(os.getcwd(), args.log)
    exout = excluded_path(out)
    log = Logger(logp)
    fx = Fetcher(log, refresh=args.refresh)
    log("START fishingv out=%s limit=%s ids=%s refresh=%s refresh_lists=%s"
        % (out, args.limit, args.ids or "-", args.refresh, args.refresh_lists))

    sitemap_ids, active = enumerate_all(fx, log, args.refresh_lists)
    if args.ids:
        ids = uniq([x.strip() for x in args.ids.split(",") if x.strip().isdigit()])
    else:
        act = sorted(active, key=int)
        rest = sorted(sitemap_ids - set(active), key=int)
        ids = act + rest
    if args.limit:
        ids = ids[: args.limit]
    total = len(ids)
    log("targets: %d (listed=%d, stale=%d)" % (total, sum(1 for s in ids if s in active),
                                               sum(1 for s in ids if s not in active)))

    recs, excl, fails, misses = [], [], [], 0
    t0 = time.time()
    last_log = 0.0
    net0 = fx.n_net
    for i, sid in enumerate(ids, 1):
        try:
            r, x = build_record(fx, sid, active, log)
            if r is not None:
                recs.append(r)
            elif x is not None:
                excl.append(x)
            else:
                misses += 1
        except Exception as e:  # noqa
            fails.append(sid)
            log("ERR s=%s %s: %s" % (sid, e.__class__.__name__, e))
        now = time.time()
        if i == total or i % 25 == 0 or now - last_log > 60:
            last_log = now
            el = now - t0
            rate = el / i if i else 0
            log("progress %d/%d records=%d excluded=%d miss=%d err=%d plans=%d net=%d cache=%d elapsed=%.0fs eta=%.0fm"
                % (i, total, len(recs), len(excl), misses, len(fails), sum(len(x["plans"]) for x in recs),
                   fx.n_net - net0, fx.n_cache, el, rate * (total - i) / 60.0))
        if i % 100 == 0:
            save(out, recs)
            save(exout, excl)
    save(out, recs)
    save(exout, excl)
    if fails:
        log("FAILED ids (%d): %s" % (len(fails), ",".join(fails)))
    log("excluded=%d -> %s" % (len(excl), exout))
    log("DONE %d records" % len(recs))


if __name__ == "__main__":
    main()
