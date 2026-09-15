#!/usr/bin/env python3
"""gap47: 沖縄県の地域一覧（観光協会・島のポータル・釣り船会など）から釣り船を拾う。

使い方:
  python3 tools/scrape_gap47.py fetch URL [URL...]   # キャッシュ付き取得（パスを表示）
  python3 tools/scrape_gap47.py build [--refresh]     # 取得（キャッシュ優先）→解析→ work/sources/gap47.json

作法: 1ホスト直列・1秒間隔・robots.txt に従う・キャッシュ work/cache/gap47/（--refresh で再取得）

使う一覧:
  kumekanko  久米島町観光協会「遊ぶ」の 釣り ジャンル（＋釣り船を扱うマリンショップ）
  yomitan    読谷釣り船会（読谷村漁協加入の船の紹介。Copyright 2012 のため stale）
  okiraku    沖楽（沖縄専門の予約ポータル）釣りカテゴリの掲載ショップ
  okistory   おきなわ物語（沖縄観光コンベンションビューロー）釣り・漁業体験カテゴリ
  zamami     座間味村観光協会 アクティビティ一覧
  site       伊是名・伊平屋（一覧が無い地域）の個別サイト
"""
import hashlib
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
CACHE = os.path.join(ROOT, "work", "cache", "gap47")
LOG = os.path.join(ROOT, "work", "logs", "gap47.log")
OUT = os.path.join(ROOT, "work", "sources", "gap47.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REFRESH = "--refresh" in sys.argv
FETCHED = "2026-09-15"
PREF = "沖縄県"
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
def strip_html(t):
    return re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>|<svg.*?</svg>",
                  "", t, flags=re.S | re.I)


def lines_of(t):
    s = re.sub(r"<br\s*/?>", "\n", strip_html(t), flags=re.I)
    s = re.sub(r"<[^>]+>", "\n", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
    out = []
    for x in s.split("\n"):
        x = re.sub(r"[ \t\r　]+", " ", x).strip()
        if x:
            out.append(x)
    return out


TEL_RE = re.compile(r"(0\d{1,4})[-‐－−ー]?(\d{1,4})[-‐－−ー](\d{3,4})")


def first_tel(s):
    if not s:
        return None
    s = s.replace("(", "").replace(")", "-").replace("（", "").replace("）", "-")
    m = TEL_RE.search(s)
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def to_int(s):
    d = re.sub(r"[^\d]", "", s or "")
    return int(d) if d else None


def zen2han(s):
    return (s or "").translate(str.maketrans("０１２３４５６７８９－", "0123456789-"))


CITY_RE = re.compile(r"(?:沖縄県)?\s*(?:(?:国頭郡|中頭郡|島尻郡|宮古郡|八重山郡)\s*)?([^\s\d０-９]{1,5}?[市町村])")


def city_of(addr):
    if not addr:
        return None
    m = CITY_RE.search(addr.replace("沖縄県", "", 1) if addr.startswith("沖縄県") else addr)
    return m.group(1) if m else None


def norm_addr(a):
    if not a:
        return None
    a = re.sub(r"^日本、", "", a.strip())
    a = re.sub(r"^〒?\s*\d{3}[-－]\d{4}\s*", "", a).strip()
    a = re.sub(r"\s*MAPCODE.*$", "", a)
    if not a:
        return None
    if not a.startswith("沖縄県"):
        a = "沖縄県" + a
    return a


def first_sentence(s, limit=100):
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(.+?[。！!])", s)
    s = m.group(1) if m else s
    return s[:limit] or None


def rec(src_id, src_url, name, **kw):
    r = {
        "src": "gap47", "src_id": src_id, "src_url": src_url, "name": name, "kana": None,
        "pref": PREF, "city": None, "address": None, "port": None, "lat": None, "lon": None,
        "tel": None, "website": None, "sns": [], "types": [], "targets": [], "methods": [],
        "holidays": None, "facilities": [], "capacity": None, "access": None, "description": None,
        "plans": [], "schedule_text": None, "fetched": FETCHED,
    }
    r.update({k: v for k, v in kw.items() if v is not None})
    if r["address"] and not r["city"]:
        r["city"] = city_of(r["address"])
    return r


def plan(name, price_text, kind="", price=None, url=None, depart=None, ret=None, targets=None):
    return {"name": name, "kind": kind, "targets": targets or [], "price": price,
            "price_text": price_text, "depart": depart, "return": ret, "meet": "", "season": "",
            "days": "", "includes": "", "url": url or ""}


SOCIAL = re.compile(r"instagram\.com|facebook\.com|line\.me|lin\.ee|twitter\.com|x\.com/|youtube\.com|tiktok\.com")


# ---------------------------------------------------------------- 久米島町観光協会
KUME_LIST = "https://www.kanko-kumejima.com/spot/play/"
# 一覧で「釣り」ジャンル、または紹介文で釣り船を扱うと明記しているもの
KUME_IDS = [179, 2111, 1609, 2078, 1761, 2112, 384, 119]
KUME_LABELS = ["ジャンル", "名称", "住所", "電話", "FAX", "ホームページ", "駐車場", "支払方法", "予算",
               "外国語対応", "その他", "地図"]
PLAN_SKIP = re.compile(r"レンタル|タックル|餌|エサ|解体|包装|オプション|使用料|入漁料|キャンセル|竿|防波堤|陸釣り")


def parse_kume(pid):
    url = f"https://www.kanko-kumejima.com/spot/play/{pid}/"
    t = page(url)
    if not t:
        return None
    L = lines_of(t)
    i = L.index("施設情報")
    j = L.index("一覧ページに戻る", i)
    info, cur = {}, None
    for x in L[i + 1:j]:
        if x in KUME_LABELS:
            cur = x
            info.setdefault(cur, [])
        elif cur:
            info[cur].append(x)
    name = " ".join(info.get("名称", []))
    # 本文（見出し〜施設情報）
    try:
        h = max(k for k in range(i) if L[k] == name)
    except ValueError:
        h = i
    body = L[h + 1:i]
    genre = " ".join(info.get("ジャンル", []))
    body = [x for x in body if x != genre]
    desc = first_sentence(body[0]) if body else None
    addr_txt = " ".join(info.get("住所", []))
    port = None
    m = re.search(r"拠点港[：:]\s*([^\s(（]+)", addr_txt)
    if m:
        port = m.group(1)
    elif re.search(r"(漁港|港)", addr_txt) and "沖縄県" not in addr_txt:
        port = re.split(r"[、,]", addr_txt)[0].strip()
    am = re.search(r"(沖縄県[^)）＜<]+)", addr_txt)
    address = am.group(1).strip() if am else None
    access = addr_txt if (port and not am) else None
    tel = first_tel(" ".join(info.get("電話", [])))
    hp = " ".join(info.get("ホームページ", [])).strip() or None
    exts = [x for x in re.findall(r'href="(https?://[^"\s]+)', strip_html(t)) if "kanko-kumejima" not in x]
    sns = []
    for x in exts:
        if re.search(r"kankoukyoukai\.kumejimatyou|kumejima_kanko|UCqdE89_UBycmyOuKeBZrtcQ", x):
            continue  # 観光協会サイト共通のSNS
        if SOCIAL.search(x) and "sharer" not in x and "share?" not in x and x not in sns:
            sns.append(x)
    website = hp if (hp and hp.startswith("http") and not SOCIAL.search(hp)) else None
    alltext = " ".join(body + [" ".join(v) for v in info.values()])
    types = ["仕立"] if re.search(r"チャーター|貸切", alltext) else []
    cap = re.search(r"最大定員[：:]?\s*(\d+)\s*名", zen2han(alltext))
    plans, prefix = [], ""
    for x in body:
        if x.startswith("■"):
            prefix = x.lstrip("■").split("(")[0].strip()
            continue
        if re.search(r"[￥¥][\d,]+|[\d,]{4,}\s*円", x) and not PLAN_SKIP.search(x):
            nm = re.split(r"[￥¥]", x)[0].strip("※ ")
            plans.append(plan((prefix + " " + nm).strip(), x, "仕立" if types else "", to_int(re.findall(r"[￥¥]([\d,]+)", x)[0]) if re.search(r"[￥¥][\d,]+", x) else None))
    if not plans:
        for x in info.get("予算", []):
            if PLAN_SKIP.search(x) or not re.search(r"\d", x) or "メニューによって異なる" in x:
                continue
            nums = re.findall(r"([\d,]{4,})\s*円", x)
            nm = re.sub(r"[【】]", "", x.split("】")[0]) if "】" in x else "予算（観光協会掲載）"
            plans.append(plan(nm, x, "仕立" if re.search(r"チャーター", x) else "", to_int(nums[0]) if nums else None))
    return rec(f"kumekanko:{pid}", url, name, address=address, port=port, tel=tel, website=website,
               sns=sns, types=types, capacity=int(cap.group(1)) if cap else None, access=access,
               description=desc, plans=plans, city="久米島町")


# ---------------------------------------------------------------- 読谷釣り船会
YOMI = "https://yomitan.info/"


def parse_yomitan():
    top = page(YOMI)
    price = page(YOMI + "price.html")
    contact = page(YOMI + "contact.html")
    access = page(YOMI + "accessmap.html")
    out = []
    if not top:
        return out
    boats = re.findall(r'<img src="img/boat_[^"]+"[^>]*alt="([^"]+)"', top)
    tel = None
    for src in (contact, price, top):
        if src:
            alts = " ".join(re.findall(r'alt="([^"]+)"', src))
            tel = first_tel(" ".join(lines_of(src))) or first_tel(re.sub(r"電話で", "", alts))
            if tel:
                break
    port = address = None
    if access:
        A = lines_of(access)
        for x in A:
            m = re.search(r"([一-龥]{1,4}漁港)", x)
            if m and "出港する" not in m.group(1):
                port = m.group(1)
                break
        m = re.search(r"〒?\d{3}-\d{4}\s*(沖縄県[^\s]+)", " ".join(A))
        address = m.group(1) if m else None
    plans = []
    schedule = None
    if price:
        P = lines_of(price)
        courses = re.findall(r"(\d時間コース|半日コース|1日コース)", " ".join(re.findall(r'alt="([^"]+)"', price)))
        k = P.index("魚場") if "魚場" in P else -1
        rows = []
        for idx in range(k + 1, len(P)):
            x = P[idx]
            if x.startswith("【ご注意】"):
                break
            if re.match(r"^[\d,]+円／人$", x):
                rows.append([x])
            elif rows:
                rows[-1].append(x)
        for n, r in enumerate(rows):
            times = " ".join(y for y in r[1:] if not y.startswith("港から") and not y.startswith("船長") and not y.startswith("ご提案"))
            hm = re.findall(r"(\d{1,2})：(\d{2})", times)
            nm = courses[n] if n < len(courses) else "コース"
            dep = f"{int(hm[0][0]):02d}:{hm[0][1]}" if hm else None
            ret = None
            if "帰港" in times and len(hm) >= 2:
                ret = f"{int(hm[1][0]):02d}:{hm[1][1]}"
            plans.append(plan(nm, r[0] + "（釣り道具・仕掛け・氷・エサ代込み）", "乗合", to_int(r[0]),
                              YOMI + "price.html", dep, ret))
            if "帰港" in times and len(hm) >= 4:  # 午前・午後の2便
                plans.append(plan(nm + "（午後）", r[0] + "（釣り道具・仕掛け・氷・エサ代込み）", "乗合", to_int(r[0]),
                                  YOMI + "price.html", f"{int(hm[2][0]):02d}:{hm[2][1]}", f"{int(hm[3][0]):02d}:{hm[3][1]}"))
            elif "帰港" not in times and hm:
                plans[-1]["days"] = times
        schedule = "乗合は2名から出船。出港15分前集合、前払い。"
    common = dict(port=port, address=address, tel=tel, stale=True, city="読谷村")
    out.append(rec("yomitan:会", YOMI, "読谷釣り船会", website=YOMI, types=["乗合"], plans=plans, schedule_text=schedule,
                   description="読谷村漁協に加入する釣り船の会。1時間コースから、手ぶら可。", **common))
    for b in boats:
        out.append(rec(f"yomitan:{b}", YOMI, b, description="読谷釣り船会の所属船（連絡先は会の窓口）", **common))
    return out


# ---------------------------------------------------------------- 沖楽（おきらく）
OKI_LIST = ["https://oki-raku.net/fishing/"] + [
    f"https://oki-raku.net/activity/search/?category_id%5B%5D=9&prefecture_id=47&order_id=1&adult_num=0&child_num=0&page={p}"
    for p in (2, 3)]
# 除外: 894 ジェットスキー釣り / 505 イカダ釣り（海上の釣り場）/ 555 カヤック釣り / 805 BBQ村 /
#       891 掲載プランが陸釣りのみ / 346 旅行会社（ツアー販売）
OKI_EXCLUDE = {"894", "505", "555", "805", "891", "346"}
FISH_PLAN = re.compile(r"釣|フィッシング|トローリング|ジギング|パヤオ")
PORT_RE = re.compile(r"([一-龥ァ-ヶー]{1,6}(?:漁港|フィッシャリーナ|マリーナ|港))")


def parse_okiraku():
    shop_ids = []
    for u in OKI_LIST:
        t = page(u)
        if not t:
            continue
        for sid in re.findall(r"/activity/shop/(\d+)/", t):
            if sid not in shop_ids:
                shop_ids.append(sid)
    out, done = [], 0
    for sid in shop_ids:
        done += 1
        if sid in OKI_EXCLUDE:
            continue
        url = f"https://oki-raku.net/activity/shop/{sid}/"
        t = page(url)
        if not t:
            continue
        log(f"okiraku {done}/{len(shop_ids)} shop {sid}")
        L = lines_of(t)
        name = re.search(r"<title>(.*?)のネット予約", t, re.S).group(1).strip()
        name = name.replace("　", " ")

        def after(label):
            if label not in L:
                return []
            j = L.index(label)
            vals = []
            for x in L[j + 1:j + 10]:
                if x in ("営業時間", "所在地", "取扱プラン", "開催地", "プラン一覧", "口コミ"):
                    break
                vals.append(x)
            return vals
        addr = " ".join(after("所在地"))
        meet = None
        if "ファミリーマート" in addr or "集合" in addr:
            meet, addr = addr, None
        addr = norm_addr(re.sub(r"^会社住所[：:]", "", addr)) if addr else None
        handled = [x.rstrip("/") for x in after("取扱プラン")]
        # プラン一覧（ショップページ内）
        plans, ports = [], []
        pidx = [k for k, x in enumerate(L) if x == "プラン一覧"]
        if pidx:
            block = []
            for x in L[pidx[-1] + 1:]:
                if x.startswith("{ el.") or x == "沖楽":
                    break
                block.append(x)
            titles, expect = [], True
            for x in block:
                if expect and not re.match(r"^¥", x):
                    titles.append([x, None])
                    expect = False
                elif re.match(r"^¥[\d,]+〜$", x) and titles and titles[-1][1] is None:
                    titles[-1][1] = x
                elif x == "プランを見る":
                    expect = True
            plan_urls = []
            for u2 in re.findall(r'href="(https://oki-raku.net/activity/detail-\d+/)"', t):
                if u2 not in plan_urls:
                    plan_urls.append(u2)
            for n, (title, pr) in enumerate(titles):
                if not pr or not FISH_PLAN.search(title):
                    continue
                kind = "仕立" if re.search(r"貸切|チャーター", title) else ("乗合" if re.search(r"乗り?合", title) else "")
                purl = plan_urls[n] if n < len(plan_urls) else ""
                plans.append(plan(title, pr + "（税込）", kind, to_int(pr), purl))
                ports += PORT_RE.findall(title)
        sidx = [k for k, x in enumerate(L) if x == "ショップ情報"]
        intro = " ".join(L[sidx[-1] + 1:sidx[-1] + 14]) if sidx else ""
        ports += PORT_RE.findall(intro.replace("堤防・漁港釣り", ""))
        ports = [p for p in ports if p not in ("漁港", "港")]
        ports = [p for p in ports if not re.search(r"空港|美ら海", p)]
        port = max(set(ports), key=ports.count) if ports else None
        types = []
        if any("チャーター" in h for h in handled) or any(p["kind"] == "仕立" for p in plans):
            types.append("仕立")
        if any(p["kind"] == "乗合" for p in plans):
            types.append("乗合")
        desc = None
        if sidx:
            for x in L[sidx[-1] + 1:sidx[-1] + 4]:
                if not re.search(r"(のご紹介|について)$", x) and len(x) >= 6:
                    desc = first_sentence(x)
                    break
        out.append(rec(f"okiraku:shop{sid}", url, name, address=addr, port=port, types=types,
                       access=("集合場所: " + meet) if meet else None, description=desc, plans=plans))
    return out


# ---------------------------------------------------------------- おきなわ物語
OKS_LIST = "https://www.okinawastory.jp/spot/list?category=67"
# 採用: 釣り船・釣りを扱うマリンショップ。除外: 釣堀/イカダ/釣具レンタル/ポータル/定置網・追込み漁体験/施設/民宿の漁業体験/自然体験
OKS_IDS = ["600001553", "600016061", "600008686", "600016859"]
OKS_LABELS = ["住所", "電話番号", "営業時間", "定休日", "料金", "アクセス", "駐車可能台数", "Wi-Fi有無", "クレジットカード",
              "支払方法", "バリアフリー", "備考", "表示エリア・カテゴリ・テーマ", "メニュー・料金", "所要時間", "予約",
              "駐車場", "休業日", "定休日・休業日"]


def parse_okistory():
    page(OKS_LIST)
    out = []
    for sid in OKS_IDS:
        url = "https://www.okinawastory.jp/spot/" + sid
        t = page(url)
        if not t:
            continue
        L = lines_of(t)
        name = re.search(r"<title>(.*?)\|", t, re.S).group(1).strip()
        info, cur = {}, None
        for x in L:
            if x in OKS_LABELS:
                cur = x
                info.setdefault(cur, [])
            elif cur and len(info[cur]) < 30:
                info[cur].append(x)
        addr = norm_addr(" ".join(info.get("住所", [])[:1]))
        tel = first_tel(" ".join(info.get("電話番号", [])[:1]))
        port = None
        if addr:
            pm = PORT_RE.search(addr)
            port = pm.group(1) if pm else None
        exts = []
        for x in re.findall(r'href="(https?://[^"\s]+)"', strip_html(t)):
            if re.search(r"okinawastory|visitokinawa|google|gstatic|cloudflare|mahaechan|UCjv5O8KPfgfjzZGAnQUYHIQ|share|twitter\.com/intent|line\.me/R", x):
                continue
            if x not in exts:
                exts.append(x)
        website = next((x for x in exts if not SOCIAL.search(x)), None)
        sns = [x for x in exts if SOCIAL.search(x)]
        plans = []
        if "メニュー・料金" in L:
            j = L.index("メニュー・料金")
            head = ""
            for x in L[j + 1:j + 40]:
                if x in OKS_LABELS or x.startswith("※料金や情報は"):
                    break
                hm = re.match(r"^[~〜★■◆【]+(.+?)[~〜★】]*$", x)
                pm = re.search(r"[￥¥]\s*([\d,]+)|([\d,]{4,})\s*円", x)
                if hm and not pm:
                    head = hm.group(1).strip()
                    continue
                if not pm or x.startswith("※"):
                    continue
                nm = re.split(r"[￥¥]|[\d,]{4,}\s*円", x)[0].strip("■・ ") or head
                if not re.search(r"釣|トローリング|ジギング|フィッシング", nm + head) or PLAN_SKIP.search(nm):
                    continue
                if any(p["price_text"] == x for p in plans):
                    continue
                plans.append(plan(nm, x, "仕立" if "チャーター" in nm else "", to_int(pm.group(1) or pm.group(2))))
        out.append(rec(f"okistory:{sid}", url, name, address=addr, tel=tel, port=port, website=website,
                       sns=sns, plans=plans))
    return out


# ---------------------------------------------------------------- 座間味村観光協会
ZAMAMI = "https://www.zamamitourism.com/activities"


def parse_zamami():
    t = page(ZAMAMI)
    if not t:
        return []
    dec = json.JSONDecoder()
    items = {}
    for m in re.finditer(r'"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})":\{', t):
        try:
            obj, _ = dec.raw_decode(t, m.end() - 1)
        except Exception:
            continue
        if isinstance(obj, dict) and "title" in obj and "link-akuteibitei-title" in obj:
            items[m.group(1)] = obj
    # 表示中のカード（名前→電話）: ページ本文のテキスト順
    L = lines_of(t)
    card_tel = {}
    for k, x in enumerate(L[:-1]):
        tt = first_tel(L[k + 1])
        if tt and re.fullmatch(r"[\d\-() ]+", L[k + 1].strip()):
            card_tel.setdefault(x, tt)
    out = []
    for o in items.values():
        cats = o.get("arraystring") or []
        rt = re.sub(r"<[^>]+>", "\n", o.get("richtext") or "")
        if not (any("釣り" in c for c in cats) or re.search(r"沖釣り|船釣り|釣り船", rt)):
            continue
        title = o["title"].strip()
        name = re.sub(r"^（運休）", "", title)
        tel = card_tel.get(title)
        if not tel:
            for f in ("text", "text1", "newField"):
                tel = first_tel(str(o.get(f) or ""))
                if tel:
                    break
        if not tel:
            wm = re.search(r"\+81\s*(\d{2})\s*(\d{4})\s*(\d{4})", rt)
            tel = f"0{wm.group(1)}-{wm.group(2)}-{wm.group(3)}" if wm else None
        ad = o.get("address") or {}
        loc = ad.get("location") or {}
        formatted = ad.get("formatted") or ""
        port = "座間味港" if "座間味港" in formatted else None
        access = None
        if "渡し船乗り場" in formatted:
            access = "集合: 座間味無人島渡し船乗り場"
        addr = None
        am = re.search(r"沖縄県[^\sA-Za-z]*座間味村[^\s]+", formatted)
        if am:
            addr = zen2han(am.group(0))
        url = o.get("url") or None
        types = ["仕立"] if re.search(r"貸切|貸し切り", rt) else []
        desc_lines = [x.strip() for x in rt.split("\n") if x.strip() and "Whatsapp" not in x]
        desc = first_sentence(desc_lines[0]) if desc_lines else None
        sched = next((x for x in desc_lines if "営業" in x and "終了" in x), None)
        out.append(rec(f"zamami:{name}", ZAMAMI, name, tel=tel, port=port, address=addr, city="座間味村",
                       lat=loc.get("latitude"), lon=loc.get("longitude"),
                       website=url if url and not SOCIAL.search(url) else None,
                       sns=[url] if url and SOCIAL.search(url) else [], types=types, access=access,
                       description=desc, schedule_text=sched))
    return out


# ---------------------------------------------------------------- 伊是名・伊平屋の個別サイト
def parse_sites():
    out = []
    # や〜ぐな〜
    u = "https://www.ya-guna.com/"
    t = page(u)
    if t:
        L = lines_of(t)
        tel = first_tel(L[L.index("TEL") + 1]) if "TEL" in L else None
        addr = L[L.index("所在地") + 1] if "所在地" in L else None
        out.append(rec("site:ya-guna", u, "や〜ぐな〜", tel=tel, address=norm_addr(addr), website=u,
                       city="伊是名村", description="伊是名島を拠点とする遊漁船（半日・1日利用）"))
    # H・Yマリン
    u = "https://peraichi.com/landing_pages/view/hymarineizena/"
    t = page(u)
    if t:
        S = " ".join(lines_of(t))
        tels = [f"{a}-{b}-{c}" for a, b, c in TEL_RE.findall(S)]
        plans = []
        for nm in ("半日チャーター", "１日チャーター"):
            m = re.search(nm + r"（([^）]+)）\s*([０-９\d,]+)円", S)
            if m:
                price = to_int(zen2han(m.group(2)))
                plans.append(plan(nm.replace("１", "1") + "（" + m.group(1) + "）", f"1隻{price:,}円", "仕立", price))
        out.append(rec("site:hymarineizena", u, "H・Yマリン", tel=tels[0] if tels else None,
                       port="仲田港" if "仲田港発着" in S else None, website=u, city="伊是名村",
                       types=["仕立"], plans=plans,
                       description="伊是名島のマリンアクティビティ。小型クルーザーのチャーター（沖釣り等）、伊是名⇔伊平屋の海上タクシー"))
    # MIUマリン
    u = "https://miumarine.wixsite.com/toseniheya"
    t = page(u)
    if t:
        S = " ".join(lines_of(t))
        cap = re.search(r"遊漁船の定員は\s*(\d+)\s*名", S)
        out.append(rec("site:miumarine", u, "MIUマリン", tel=first_tel(S), website=u, city="伊平屋村",
                       types=["仕立", "渡船"], capacity=int(cap.group(1)) if cap else None,
                       targets=[], methods=[x for x in ("タイラバ", "ライトキャスティング", "GTキャスティング") if x in S],
                       description="伊平屋の渡船＆遊漁船。伊平屋-伊是名間・沖堤防へのチャーター渡船と伊平屋近海の釣り"))
    return out


# ---------------------------------------------------------------- build
def build():
    page(KUME_LIST)
    recs = []
    for n, pid in enumerate(KUME_IDS, 1):
        r = parse_kume(pid)
        log(f"kumekanko {n}/{len(KUME_IDS)}")
        if r:
            recs.append(r)
    recs += parse_yomitan()
    recs += parse_okiraku()
    recs += parse_okistory()
    recs += parse_zamami()
    recs += parse_sites()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    log(f"build done {len(recs)} records")
    print(len(recs), "records ->", OUT)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args and args[0] == "fetch":
        for u in args[1:]:
            b = fetch(u)
            print(("OK " if b else "NG ") + (cache_path(u) if b else "") + " " + u)
    elif args and args[0] == "build":
        build()
    else:
        print(__doc__)
