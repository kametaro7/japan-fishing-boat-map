#!/usr/bin/env python3
"""gap25: 滋賀県（琵琶湖）の地域一覧からガイド船・遊漁船を拾う。

使い方:
  python3 tools/scrape_gap25.py fetch URL [URL...]   # キャッシュ付き取得（パスを表示）
  python3 tools/scrape_gap25.py build [--refresh]     # 取得（キャッシュ優先）→解析→ work/sources/gap25.json

作法: 1ホスト直列・1秒間隔・robots.txt に従う・キャッシュ work/cache/gap25/（--refresh で再取得）
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "work", "cache", "gap25")
LOG = os.path.join(ROOT, "work", "logs", "gap25.log")
OUT = os.path.join(ROOT, "work", "sources", "gap25.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REFRESH = "--refresh" in sys.argv
FETCHED = "2026-09-15"
PREF = "滋賀県"
_last = {}
_robots = {}


# ---------------------------------------------------------------- fetch
def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + ".html")


def _raw(url):
    host = urllib.parse.urlsplit(url).netloc
    wait = 1.0 - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
    delay = 2
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                _last[host] = time.time()
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            _last[host] = time.time()
            if e.code in (429, 503) and attempt < 4:
                time.sleep(delay)
                delay *= 2
                continue
            return e.code, b""
        except Exception as e:  # noqa
            _last[host] = time.time()
            if attempt < 1:
                time.sleep(delay)
                continue
            return 0, str(e).encode()
    return 0, b""


def allowed(url):
    sp = urllib.parse.urlsplit(url)
    base = f"{sp.scheme}://{sp.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        st, body = _raw(base + "/robots.txt")
        if st == 200 and not body.lstrip().lower().startswith(b"<"):
            rp.parse(body.decode("utf-8", "replace").splitlines())
        else:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def fetch(url):
    os.makedirs(CACHE, exist_ok=True)
    p = cache_path(url)
    if os.path.exists(p) and not REFRESH:
        return open(p, "rb").read()
    if not allowed(url):
        log(f"robots disallow {url}")
        return None
    st, body = _raw(url)
    log(f"{st} {len(body)} {url}")
    if st == 200:
        with open(p, "wb") as f:
            f.write(body)
        return body
    return None


def decode(b):
    m = re.search(rb'charset=["\']?([\w-]+)', b[:3000], re.I)
    enc = (m.group(1).decode().lower() if m else "utf-8")
    if enc in ("shift_jis", "sjis", "x-sjis", "shift-jis"):
        enc = "cp932"
    return b.decode(enc, "replace")


def page(url):
    b = fetch(url)
    return decode(b) if b else None


# ---------------------------------------------------------------- helpers
def lines_of(t):
    """HTML → 行のリスト。リンクは [URL] として残し、セル区切りはタブにする。"""
    s = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", "", t, flags=re.S | re.I)
    s = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>', r"[\1] ", s)
    s = re.sub(r"</t[dh]>", "\t", s)
    s = re.sub(r"</tr>|<br\s*/?>|</p>|</h\d>|</li>|</div>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ")
    return [x.strip() for x in s.split("\n") if x.strip()]


def cell_text(c):
    c = re.sub(r"<br\s*/?>", " ", c, flags=re.I)
    c = html.unescape(re.sub(r"<[^>]+>", "", c)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", c).strip()


ZEN = str.maketrans("０１２３４５６７８９－", "0123456789-")


def norm_addr(a):
    return re.sub(r"\s+", " ", (a or "").translate(ZEN)).strip() or None


def nospace(s):
    return re.sub(r"[\s　]", "", s or "")


CITY_RE = re.compile(r"^滋賀県((?:[^\s]{1,4}?郡[^\s]{1,4}?[町村])|(?:[^\s]{1,4}?市))")


def city_of(addr):
    m = CITY_RE.match(addr or "")
    return m.group(1) if m else None


OUT_CITY_RE = re.compile(r"^(.{2,3}?[都道府県])((?:[^\s]{1,4}?郡)|(?:[^\s]{1,4}?[市区町村]))")


def out_city(addr):
    m = OUT_CITY_RE.match(addr or "")
    return (m.group(1) + m.group(2)) if m else None


def reg_key(reg):
    """登録番号 → (都道府県, 数字)。「岐阜県第0030号」と「岐阜県第30号」を同一視する。"""
    reg = nospace(reg).translate(ZEN)
    m = re.match(r"(.+?[都道府県])第?([\d-]+)号?", reg)
    if not m:
        return reg
    num = m.group(2)
    return (m.group(1), int(num) if num.isdigit() else num)


def kata2hira(s):
    s = nospace(s)
    if not s or not re.fullmatch(r"[ァ-ヶー・]+", s):
        return None
    return "".join(chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヶ" else ch for ch in s)


def rec(src_id, src_url, name, **kw):
    r = {
        "src": "gap25", "src_id": src_id, "src_url": src_url, "name": name, "kana": None,
        "pref": PREF, "city": None, "address": None, "port": None, "lat": None, "lon": None,
        "tel": None, "website": None, "sns": [], "types": ["ガイド"], "targets": [], "methods": [],
        "holidays": "", "facilities": [], "capacity": None, "access": "", "description": "",
        "plans": [], "schedule_text": "", "fetched": FETCHED,
    }
    r.update({k: v for k, v in kw.items() if v is not None})
    if r["address"] and not r["city"]:
        r["city"] = city_of(r["address"])
    return r


# ---------------------------------------------------------------- 滋賀県 ビワマス承認遊漁船リスト
# 琵琶湖海区漁業調整委員会「ビワマス釣りの承認を受けた遊漁船（フィッシングガイド船）の一覧」
# 列: 承認番号/登録番号/代表者/営業所名/営業所住所/営業所電話番号/使用船舶/業務主任者
# 代表者・業務主任者（個人名）は出力しない。営業所名が代表者と同じ（個人名）なら船名を name にする。
R78_PAGE = "https://www.pref.shiga.lg.jp/kaiku/17338.html"
R78_PDF = "https://www.pref.shiga.lg.jp/documents/17338/5627971_1.pdf"   # 令和8年8月5日時点
R67_PAGE = "https://www.pref.shiga.lg.jp/kaiku/oshirase/340859.html"  # R6-7シーズン（更新 2025-12-11）
COLS = ["no", "reg", "rep", "office", "addr", "tel", "boat", "chief"]


def rows_r78():
    fetch(R78_PAGE)
    if not fetch(R78_PDF):
        return []
    import pdfplumber
    rows = []
    with pdfplumber.open(cache_path(R78_PDF)) as pdf:
        for pg in pdf.pages:
            for tb in pg.extract_tables():
                for row in tb:
                    cells = [re.sub(r"\s*\n\s*", " ", c or "").strip() for c in row]
                    if len(cells) == 8 and re.fullmatch(r"Y\d+", cells[0]):
                        rows.append(dict(zip(COLS, cells)))
    return rows


def rows_r67():
    t = page(R67_PAGE)
    if not t:
        return []
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S | re.I):
        cells = [cell_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if len(cells) == 9 and re.fullmatch(r"Y\d+", cells[0]):
            # R6-7 は郵便番号の列がある
            d = dict(zip(["no", "reg", "rep", "office", "postal", "addr", "tel", "boat", "chief"], cells))
            rows.append(d)
    return rows


def personal(row):
    return nospace(row["office"]) == nospace(row["rep"])


def group_rows(rows):
    groups, order = {}, []
    for r in rows:
        k = (nospace(r["office"]), re.sub(r"\D", "", r["tel"]))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(r)
    return [groups[k] for k in order]


def biwamasu_rec(rows, season, src_url, stale=False, notes=None):
    r0 = rows[0]
    boats = []
    for r in rows:
        if r["boat"] not in boats:
            boats.append(r["boat"])
    regs = []
    for r in rows:
        if r["reg"] not in regs:
            regs.append(r["reg"])
    name = "・".join(boats) if personal(r0) else r0["office"]
    addr = norm_addr(r0["addr"])
    desc = [f"ビワマス引縄釣の承認遊漁船（{season}シーズン、承認番号{'・'.join(r['no'] for r in rows)}）",
            f"登録: {'・'.join(regs)}"]
    if not personal(r0):
        desc.append(f"使用船舶: {'・'.join(boats)}")
    kw = {}
    if addr and addr.startswith("滋賀県"):
        kw["address"] = addr
    else:
        oc = out_city(addr)
        if oc:
            desc.append(f"営業所は{oc}")
    if notes:
        desc.extend(notes)
    src_id = f"shiga-biwamasu-{season.lower().replace('-', '')}:" + "+".join(r["no"] for r in rows)
    rec_ = rec(src_id, src_url, name, port="琵琶湖", tel=r0["tel"], targets=["ビワマス"],
               methods=["引縄釣"], description="。".join(desc), **kw)
    if stale:
        rec_["stale"] = True
    return rec_


def build_biwamasu():
    r78, r67 = rows_r78(), rows_r67()
    log(f"biwamasu R7-8 rows={len(r78)} R6-7 rows={len(r67)}")
    out = []
    keys78 = {reg_key(r["reg"]) for r in r78}
    for g in group_rows(r78):
        notes = []
        for old in r67:
            if reg_key(old["reg"]) in {reg_key(r["reg"]) for r in g}:
                if nospace(old["office"]) != nospace(g[0]["office"]) and not personal(old):
                    n = f"R6-7の営業所名: {old['office']}"
                    if n not in notes:
                        notes.append(n)
                if re.sub(r"\D", "", old["tel"]) != re.sub(r"\D", "", g[0]["tel"]):
                    n = f"R6-7の電話: {old['tel']}"
                    if n not in notes:
                        notes.append(n)
        out.append(biwamasu_rec(g, "R7-8", R78_PDF, notes=notes))
    # R6-7 にだけ載っていた業者（今季の承認リストに無い）→ stale
    old_only = [r for r in r67 if reg_key(r["reg"]) not in keys78]
    for g in group_rows(old_only):
        out.append(biwamasu_rec(g, "R6-7", R67_PAGE, stale=True))
    return out


# ---------------------------------------------------------------- 一般社団法人ビワマスプロガイド協会
ASSOC = "https://biwamasu-pro.com/boatandcaptain/"
# 個人名を含む公式サイト/SNS は出力しない（ブランド名のアカウントだけ残す）
ASSOC_SITE_SKIP = {"https://kentasekine.com/", "https://www.ccn3.aitai.ne.jp/~yasuda-t/"}
ASSOC_SNS_KEEP = {"biwamasu_ryoushi", "garagerosso", "onebiteonefishe", "ychaaasea", "dubhandf",
                  "kunis_biwako", "dub_hand_m", "brightliver9", "crokyo_guideservice",
                  "ranger155biwako", "biwa_masu_oru_oran"}


def build_assoc():
    t = page(ASSOC)
    if not t:
        return []
    m = re.search(r"<main.*?</main>", t, re.S)
    L = lines_of(m.group(0) if m else t)
    starts = [i for i in range(len(L) - 2) if L[i + 2].startswith("船長")]
    out = []
    for n, i in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else next(
            (k for k in range(i, len(L)) if L[k].startswith("〒")), len(L))
        blk = L[i:end]
        name, kana = blk[0], blk[1]
        captains = []
        port = addr = website = tel = None
        sns = []
        k = 2
        while k < len(blk):
            x = blk[k]
            if x.startswith("船長"):
                captains.append(re.sub(r"（.*?）", "", x.split("\t", 1)[1]).strip())
            elif x.startswith("マリーナ"):
                port = x.split("\t", 1)[1].strip()
                if k + 1 < len(blk) and blk[k + 1].startswith("滋賀県"):
                    addr = norm_addr(blk[k + 1])
                    k += 1
            elif x.startswith("HP"):
                u = re.search(r"\[(https?://[^\]]+)\]", x)
                if u:
                    website = u.group(1)
            elif x.startswith("予約"):
                u = re.search(r"\[tel:([\d-]+)\]", x)
                if u:
                    tel = u.group(1)
            elif re.match(r"\[https?://[^\]]+\]\s*(Instagram|Facebook|Link)$", x):
                u = re.match(r"\[(https?://[^\]]+)\]", x).group(1)
                h = re.search(r"instagram\.com/([^/?]+)", u)
                if h and h.group(1) in ASSOC_SNS_KEEP and u not in sns:
                    sns.append(u)
            elif not captains or port:
                pass
            else:  # 2人目以降の船長（個人名）→ 読み捨て
                captains.append(re.sub(r"（.*?）", "", x).strip())
            k += 1
        if nospace(name) in {nospace(c) for c in captains}:
            log(f"assoc skip personal-name entry #{n + 1}")
            continue
        if website and "instagram.com" in website:
            h = re.search(r"instagram\.com/([^/?]+)", website)
            if h and h.group(1) in ASSOC_SNS_KEEP and website not in sns:
                sns.append(website)
            website = None
        if website in ASSOC_SITE_SKIP:
            website = None
        desc = "一般社団法人ビワマスプロガイド協会の所属ボート"
        if port:
            desc += f"（{port}）"
        out.append(rec(f"biwamasu-pro:{n + 1}:{name}", ASSOC, name, kana=kata2hira(kana), port=port,
                       address=addr, tel=tel, website=website, sns=sns, targets=["ビワマス"],
                       description=desc))
    return out


# ---------------------------------------------------------------- ミックバスクラブ（ガイド一覧ページ）
MIC = "https://www.micbassclub.com/guide.php"


def build_mic():
    t = page(MIC)
    if not t:
        return []
    L = lines_of(t)
    prices = {"1名様": [], "2名様": [], "3名様": []}
    for x in L:
        m = re.match(r"([123]名様)\t[￥¥]([\d,]+)", x)
        if m:
            prices[m.group(1)].append(int(m.group(2).replace(",", "")))
    n_guides = sum(1 for x in L if x == "ガイド料")
    plans = []
    for k, v in prices.items():
        if not v:
            continue
        lo, hi = min(v), max(v)
        txt = f"{lo:,}円" if lo == hi else f"{lo:,}〜{hi:,}円"
        plans.append({"name": f"バス釣りガイド（{k}）", "kind": "仕立", "targets": ["ブラックバス"],
                      "price": None, "price_text": f"{k} {txt}（ガイドにより異なる）", "depart": None,
                      "return": None, "meet": "", "season": "", "days": "", "includes": "", "url": MIC})
    tel = "077-579-0570" if "077-579-0570" in t else None
    addr = "滋賀県大津市苗鹿3丁目227" if "滋賀県大津市苗鹿3丁目227" in t else None
    lat = lon = None
    m = re.search(r"maps/\?q=([\d.]+),([\d.]+)", t)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
    return [rec("micbassclub:guide", MIC, "ミックバスクラブ", address=addr, port="琵琶湖（南湖）",
                lat=lat, lon=lon, tel=tel, website="https://www.micbassclub.com/",
                targets=["ブラックバス"], plans=plans,
                schedule_text="ガイド時間はおおむね7:00〜17:00（ガイド・季節により変動、予約時に確認）",
                description=f"琵琶湖のバスアングラーズマリーナ。所属プロガイド{n_guides}名によるバス釣りガイド。予約は電話")]


# ---------------------------------------------------------------- main
def build():
    out = []
    for name, fn in [("biwamasu", build_biwamasu), ("assoc", build_assoc), ("mic", build_mic)]:
        rs = fn()
        log(f"{name}: {len(rs)} records")
        print(name, len(rs))
        out.extend(rs)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"done {len(out)}/{len(out)} → {OUT}")
    print("total", len(out), OUT)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args and args[0] == "fetch":
        for u in args[1:]:
            b = fetch(u)
            print(cache_path(u) if b else f"FAILED {u}")
    elif args and args[0] == "build":
        build()
    else:
        print(__doc__)
