#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""船宿の公式サイトから、料金・プラン・出船時刻が載っていそうなページを取得して抽出用テキストにまとめる。

  python3 tools/fetch_official.py [--all] [--limit N] [--workers 8]

入力: data/detail/*.json（tools/build.py の出力）のうち website がある船宿。既定では料金・プランが無い船宿だけ（--all で全部）。
出力: work/official/pages/<key>.json  {key, website, boats:[{id,name,pref,tel}], pages:[{url,title,text}]}
      work/official/index.json      取得結果の一覧（ページ数・文字数・「円」を含むか）
生HTMLは work/cache/official/ に保存し、再実行時は再取得しない。1ホストにつき直列・1秒以上の間隔、robots.txt に従う。
"""
import concurrent.futures
import glob
import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.robotparser
from collections import defaultdict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, WORK, load_json, save_json, url_key, host_of, nfkc  # noqa: E402

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'
HEADERS = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8', 'Accept-Language': 'ja,en;q=0.8'}
CACHE = os.path.join(WORK, 'cache', 'official')
OUT = os.path.join(WORK, 'official', 'pages')
LOG = os.path.join(WORK, 'logs', 'official.log')
MAX_PAGES = 7
MAX_CHARS = 32000
INTERVAL = 1.0
# リンク文字列/URLの点数（料金に近いほど高い）
LINK_SCORES = [
    (re.compile(r'料金|料金表|運賃|乗船料|価格|price|fee|charge|ryokin|ryoukin|cost', re.I), 10),
    (re.compile(r'乗合|乗り合い|仕立|貸切|チャーター|プラン|plan|course|コース|メニュー|menu', re.I), 8),
    (re.compile(r'出船|出航|時間|スケジュール|schedule|time|予定|カレンダー|calendar', re.I), 6),
    (re.compile(r'釣り物|釣物|ターゲット|target|案内|ご利用|利用方法|システム|system|guide|info|予約|reserve|faq|よくある', re.I), 4),
]
SKIP_EXT = re.compile(r'\.(pdf|jpe?g|png|gif|webp|mp4|mov|zip|docx?|xlsx?)(\?|$)', re.I)

_host_locks = defaultdict(threading.Lock)
_host_last = {}
_robots = {}
_robots_lock = threading.Lock()
_log_lock = threading.Lock()


def log(msg):
    with _log_lock:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + msg + '\n')


def allowed(url):
    p = urlparse(url)
    base = '%s://%s' % (p.scheme, p.netloc)
    with _robots_lock:
        rp = _robots.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(base + '/robots.txt', headers=HEADERS, timeout=20)
            rp.parse(r.text.splitlines() if r.status_code == 200 and 'html' not in r.headers.get('content-type', '') else [])
        except requests.RequestException:
            rp.parse([])
        with _robots_lock:
            _robots[base] = rp
    try:
        return rp.can_fetch('*', url)
    except Exception:
        return True


ENC_ALIAS = {'shift_jis': 'cp932', 'sjis': 'cp932', 'x-sjis': 'cp932', 'shift-jis': 'cp932', 'windows-31j': 'cp932',
             'euc_jp': 'euc-jp', 'x-euc-jp': 'euc-jp'}


def decode(resp):
    """候補の文字コードで試し、かなが最も多く置換文字が最も少ないものを採る。
    （cp932 はほとんどのバイト列を「読めて」しまうので、先に成功したものを採ると EUC-JP のページが化ける）"""
    raw = resp.content
    cands = []
    m = re.search(rb'charset=["\']?([A-Za-z0-9_\-]+)', raw[:6000], re.I)
    if m:
        cands.append(m.group(1).decode('ascii', 'ignore').lower())
    if resp.encoding and resp.encoding.lower() != 'iso-8859-1':
        cands.append(resp.encoding.lower())
    cands += ['utf-8', 'cp932', 'euc-jp']
    best, best_score = None, None
    for e in cands:
        e = ENC_ALIAS.get(e, e)
        try:
            t = raw.decode(e, errors='replace')
        except LookupError:
            continue
        score = len(re.findall(r'[ぁ-んァ-ン]', t)) - 20 * t.count('�')
        if best_score is None or score > best_score:
            best, best_score = t, score
    return best if best is not None else raw.decode('utf-8', 'replace')


def fetch(url):
    """(最終URL, HTML) を返す。取得できなければ (url, None)。"""
    path = os.path.join(CACHE, hashlib.sha1(url.encode('utf-8')).hexdigest() + '.json')
    c = load_json(path)
    if c is not None:
        return c.get('final') or url, c.get('html')
    if not allowed(url):
        save_json(path, {'url': url, 'status': 'robots', 'html': None})
        return url, None
    host = urlparse(url).netloc
    html, final, status = None, url, None
    with _host_locks[host]:
        wait = _host_last.get(host, 0) + INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
            status, final = r.status_code, r.url
            ctype = r.headers.get('content-type', 'text/html')
            if r.status_code == 200 and ('html' in ctype or 'text' in ctype) and len(r.content) < 3000000:
                html = decode(r)
        except requests.RequestException as e:
            status = 'error: %s' % type(e).__name__
        _host_last[host] = time.time()
    if status in (429, 503) or (isinstance(status, str) and 'Timeout' in status):
        return final, None  # 一時的な失敗はキャッシュしない
    save_json(path, {'url': url, 'final': final, 'status': status, 'html': html})
    return final, html


def page_text(html):
    soup = BeautifulSoup(html, 'lxml')
    title = nfkc(soup.title.get_text()) if soup.title else ''
    for t in soup(['script', 'style', 'noscript', 'svg', 'form', 'select']):
        t.decompose()
    for img in soup.find_all('img'):
        alt = nfkc(img.get('alt'))
        img.replace_with(' [画像:%s] ' % alt if alt and len(alt) < 60 else ' ')
    for tr in soup.find_all('tr'):
        cells = [re.sub(r'\s+', ' ', c.get_text(' ', strip=True)) for c in tr.find_all(['td', 'th'])]
        tr.replace_with('\n' + ' | '.join(x for x in cells if x) + '\n')
    for br in soup.find_all('br'):
        br.replace_with('\n')
    for blk in soup.find_all(['p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'dt', 'dd', 'section', 'article', 'table', 'ul', 'ol']):
        blk.insert_before('\n')
        blk.insert_after('\n')
    lines, prev = [], None
    for line in soup.get_text().splitlines():
        line = re.sub(r'[ \t\u3000]+', ' ', nfkc(line)).strip()
        if line and line != prev:
            lines.append(line)
            prev = line
    return title, '\n'.join(lines)


def same_site(a, b):
    ha, hb = host_of(a), host_of(b)
    return ha == hb


def candidate_links(base_url, html):
    soup = BeautifulSoup(html, 'lxml')
    out = {}
    for tag in soup.find_all(['frame', 'iframe']):
        src = tag.get('src')
        if src:
            u = urljoin(base_url, src).split('#')[0]
            if same_site(u, base_url) and not SKIP_EXT.search(u):
                out[u] = max(out.get(u, 0), 20)  # フレーム構成の古いサイトは中身が別ページ
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if href.startswith(('mailto:', 'tel:', 'javascript:')):
            continue
        u = urljoin(base_url, href).split('#')[0]
        if not u.startswith('http') or not same_site(u, base_url) or SKIP_EXT.search(u) or u.rstrip('/') == base_url.rstrip('/'):
            continue
        label = nfkc(a.get_text(' ', strip=True)) + ' ' + nfkc(a.get('title')) + ' ' + ' '.join(nfkc(i.get('alt')) for i in a.find_all('img'))
        score = 0
        for pat, s in LINK_SCORES:
            if pat.search(label):
                score = max(score, s)
            elif pat.search(u):
                score = max(score, s - 2)
        if score:
            out[u] = max(out.get(u, 0), score)
    return sorted(out.items(), key=lambda kv: -kv[1])


def crawl_site(job):
    website = job['website']
    final, html = fetch(website)
    pages = []
    if not html:
        return job, pages
    title, text = page_text(html)
    pages.append({'url': final, 'title': title, 'text': text})
    queue = candidate_links(final, html)
    seen = {final.rstrip('/'), website.rstrip('/')}
    i = 0
    while queue and len(pages) < MAX_PAGES and i < 25:
        u, score = queue.pop(0)
        i += 1
        if u.rstrip('/') in seen:
            continue
        seen.add(u.rstrip('/'))
        f2, h2 = fetch(u)
        if not h2:
            continue
        t2, x2 = page_text(h2)
        pages.append({'url': f2, 'title': t2, 'text': x2})
        if score >= 20:  # フレームの中身からさらにリンクをたどる
            for v in candidate_links(f2, h2):
                if v[0].rstrip('/') not in seen:
                    queue.append(v)
            queue.sort(key=lambda kv: -kv[1])
    return job, pages


def trim(pages):
    """料金らしい記述を含むページを優先し、全体を MAX_CHARS に収める。"""
    def rank(p):
        t = p['text']
        return -(t.count('円') * 3 + len(re.findall(r'\d{1,2}[:：時]\d{0,2}', t)) + (50 if re.search(r'料金|乗合|仕立', t) else 0))
    top, rest = pages[:1], sorted(pages[1:], key=rank)
    out, total = [], 0
    for p in top + rest:
        room = MAX_CHARS - total
        if room < 500:
            break
        text = p['text'][:min(room, 14000)]
        out.append({'url': p['url'], 'title': p['title'], 'text': text})
        total += len(text)
    return out


def main():
    args = sys.argv[1:]
    limit = int(args[args.index('--limit') + 1]) if '--limit' in args else None
    workers = int(args[args.index('--workers') + 1]) if '--workers' in args else 8
    jobs = {}
    for path in sorted(glob.glob(os.path.join(ROOT, 'data', 'detail', '*.json'))):
        for bid, b in (load_json(path, {}) or {}).items():
            web = b.get('website')
            if not web or (b.get('plans') and '--all' not in args):
                continue
            key = hashlib.sha1((url_key(web) or web).encode('utf-8')).hexdigest()[:12]
            j = jobs.setdefault(key, {'key': key, 'website': web, 'boats': []})
            j['boats'].append({'id': bid, 'name': b.get('name'), 'pref': b.get('pref'), 'tel': b.get('tel'), 'port': b.get('port')})
    todo = [j for j in jobs.values() if not os.path.exists(os.path.join(OUT, j['key'] + '.json'))]
    if limit is not None:
        todo = todo[:limit]
    if '--index-only' in args:
        todo = []
    log('start: %d sites (%d total with website)' % (len(todo), len(jobs)))
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for job, pages in ex.map(crawl_site, todo):
            done += 1
            save_json(os.path.join(OUT, job['key'] + '.json'), dict(job, pages=trim(pages)), indent=1)
            if done % 25 == 0:
                log('progress %d/%d' % (done, len(todo)))
    index = []
    for path in sorted(glob.glob(os.path.join(OUT, '*.json'))):
        d = load_json(path, {}) or {}
        text = ''.join(p['text'] for p in d.get('pages', []))
        state, reason = assess(d)
        index.append({'key': d.get('key'), 'website': d.get('website'), 'boats': [b['id'] for b in d.get('boats', [])],
                      'name': (d.get('boats') or [{}])[0].get('name'), 'pages': len(d.get('pages', [])), 'chars': len(text),
                      'has_yen': '円' in text, 'state': state, 'reason': reason})
    save_json(os.path.join(WORK, 'official', 'index.json'), index, indent=1)
    st = defaultdict(int)
    for x in index:
        st[x['state']] += 1
    log('DONE %d sites fetched, index %d (with 円: %d) states=%s' % (done, len(index), sum(1 for x in index if x['has_yen']), dict(st)))


SPAM = re.compile(r'カジノ|バカラ|スロット|出会い系|アダルト|娱乐|博彩|官方网站|英国上市|彩票|betting|casino|viagra', re.I)
PARKED = re.compile(r'このドメインは|ドメインは売却|お名前\.com|ムームードメイン|ドメインパーキング|domain (is )?for sale|this domain|buy this domain|parked|Hosted by Sedo|Domain registration has expired|Click here to enter|Index of /|期限切れ|有効期限が切れ|ページが見つかりません|404 Not Found|アカウントが停止|サービスは終了|サービス終了のお知らせ', re.I)
RELEVANT = re.compile(r'釣|船|丸|乗合|仕立|出船|遊漁|フィッシング|漁|マリン|ボート|渡し|磯|沖')
# 船宿そのものの廃業の告知（「本日の営業は終了」のような一時的な表現は含めない）
CLOSED = re.compile(r'廃業(いた)?しました|閉業(いた)?しました|廃業のお知らせ|閉業のお知らせ')
# 兼業の一部だけの廃業（「※海上釣堀は閉業しました」で筏渡船は営業中、など）は船宿の廃業とみなさない
CLOSED_OTHER = re.compile(r'(釣り?堀|民宿|旅館|釣具|食堂|売店|店舗|支店|レストラン|カフェ|ショップ|キャンプ場?)[はをがも、・]?\s*(廃業|閉業)')
MAINT = re.compile(r'maintenance|メンテナンス中|ただいまメンテナンス', re.I)


def assess(d):
    """公式サイトとして使えるか: ok / suspect（乗っ取り・失効・無関係）/ unreachable（取得できず）。"""
    pages = d.get('pages') or []
    if not pages:
        return 'unreachable', '取得できませんでした'
    top = pages[0]['text'] + ' ' + pages[0].get('title', '')
    alltext = ' '.join(p['text'] + ' ' + p.get('title', '') for p in pages)
    kana = len(re.findall(r'[ぁ-んァ-ン]', top))
    title = pages[0].get('title', '') or ''
    if SPAM.search(top):
        return 'suspect', '無関係な広告・スパムの内容'
    head = title + ' ' + pages[0]['text'][:400]
    if CLOSED.search(head) and not CLOSED_OTHER.search(head):
        return 'closed', '公式サイトに廃業の告知'
    if len(top) < 1500 and PARKED.search(top):
        return 'suspect', 'ドメイン失効・停止の表示'
    if MAINT.search(title) or (len(top) < 400 and MAINT.search(top)):
        return 'thin', 'メンテナンス中の表示'
    if len(alltext.strip()) < 80:
        return 'thin', '本文がほとんど無い（画像だけ・フレーム・スクリプト描画など）'
    if top.count('�') > 20:
        return 'thin', '文字化けで読めない'
    if kana < 15 and len(top) > 200:
        if RELEVANT.search(title) and re.search(r'[ぁ-んァ-ン一-龥]', title):
            return 'thin', '本文がスクリプト描画で読めない（題名は釣り船のもの）'  # Wix などは本文を JS で描く
        return 'suspect', '日本語のページではない'
    if not RELEVANT.search(alltext):
        names = [nfkc(b.get('name')) for b in d.get('boats', []) if b.get('name')]
        if not any(n and n in alltext for n in names):
            return 'suspect', '釣り船に関する記述が無い'
    return 'ok', ''


if __name__ == '__main__':
    main()
