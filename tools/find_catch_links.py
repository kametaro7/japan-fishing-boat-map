#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""船宿の公式サイトから「釣果」のページへのリンクを探す。

  python3 tools/find_catch_links.py [--workers 8] [--limit N]

入力: data/detail/*.json の website（tools/build.py の出力）
出力: work/official/catch.json  {url_key(公式サイト): {site, url, text, kind, how, ...}}
      kind: site（公式サイト内のページ）/ blog（外部のブログ）/ sns / catchsite（釣果サイト）/ self（公式サイト自体が釣果ブログ）
取得は tools/fetch_official.py の fetch（キャッシュ work/cache/official/、robots.txt、1ホスト直列・1秒間隔）を使う。

選び方:
- トップページ（フレームの中身も）のリンクを、文字に「釣果」があるか・URL が choka/catch などか・ブログか、で点数を付ける
- 点の高い順に実際に開き、パーキング・エラーでなく、文字が「釣果」なら開ければ採用、そうでなければ本文に釣果の記述
  （「釣果」「○匹」「○cm」など）が3つ以上あるときだけ採用する
- 日付入りの記事1本（「9月13日の釣果」）は一覧ではないので、ブログの一覧・カテゴリのページを優先し、記事しか無ければ使わない
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, WORK, save_json, url_key  # noqa: E402
from fetch_official import PARKED, SKIP_EXT, fetch, page_text  # noqa: E402

OUT = os.path.join(WORK, 'official', 'catch.json')
LOG = os.path.join(WORK, 'logs', 'catch.log')

TEXT_STRONG = re.compile(r'釣果|ちょうか|チョウカ|釣れてます|釣れ情報')
TEXT_EN = re.compile(r'\bcatch(es)?\b|fishing\s*report|\bresults?\b|chou?ka', re.I)
HREF_CATCH = re.compile(r'chou?ka|tyou?ka|cyou?ka|chouka|catch|fishing[-_]?report|turika|tsurika|/results?\b|result\.|%e9%87%a3%e6%9e%9c', re.I)
TEXT_BLOG = re.compile(r'ブログ|blog|日記|日誌|最新情報|釣り情報|釣況|釣行記|レポート|report', re.I)
BLOG_HOST = re.compile(r'livedoor\.biz|ameblo\.jp|\.fc2\.com|hatenablog|hatena\.ne\.jp|livedoor\.(jp|blog)|blog\.goo\.ne\.jp|exblog\.jp|seesaa\.net|jugem\.jp|note\.com|wordpress\.com|blogspot\.|amebaownd\.com|cocolog|blog\.jp|webnode|jimdo', re.I)
# 公式サイトそのものがブログのサービス（ホームページ作成サービスの web.fc2.com・jimdo などは含めない）
BLOG_SITE = re.compile(r'\.livedoor\.biz$|(^|\.)ameblo\.jp$|^blog\d*\.fc2\.com$|\.blog\d*\.fc2\.com$|hatenablog|hatena\.ne\.jp|blog\.livedoor\.jp|livedoor\.blog|'
                       r'blog\.goo\.ne\.jp|exblog\.jp|seesaa\.net|jugem\.jp|(^|\.)note\.com$|blogspot\.|cocolog-nifty\.com|\.blog\.jp$', re.I)
SNS_HOST = re.compile(r'(^|\.)(instagram\.com|facebook\.com|fb\.com|twitter\.com|x\.com|threads\.net|youtube\.com|youtu\.be|tiktok\.com|line\.me|lin\.ee)$', re.I)
# 釣果を載せている外部サイト（店舗ごとのページへのリンクなら採る）
CATCH_SITES = {
    'fishing-v.jp': '釣りビジョン', 'funaduri.jp': '船釣り.jp', 'theboat.jp': 'THE BOAT', 'tsurimaru.jp': 'つり丸',
    'anglers.jp': 'アングラーズ', 'chowari.jp': '釣割', 'tsuree.jp': 'つりー', 'point-i.jp': '釣具のポイント',
    'tsurisoku.com': 'つりそく', 'gyo.ne.jp': '釣果情報', 'turi-ba.com': '釣り場',
}
# 記事1本のURL（ブログの一覧・カテゴリではない）
POST_URL = re.compile(r'QBlog-\d{8}|[?&][\w-]*-\d{8}(-\d+)?$|instagram\.com/(p|reel|reels|tv)/|facebook\.com/.*/(posts|photos|videos)/|/permalink\.php|/status(es)?/\d|blog-entry-\d+|/entry-\d+\.html|/archives/\d+|[?&]p=\d+\b|/\d{4}/\d{2}/\d{2}/|/posts/\d{5,}|/\d{4}-\d{2}-\d{2}|/entry/\d{4}/', re.I)
# ブログの操作用ページ・共有ボタンなど（釣果の一覧ではない）
EXCLUDE_URL = re.compile(r'privacy|policy|kiyaku|terms|kozin|kojin|entrylist|imagelist|/page-\d+\.html|official\.ameba|auth\.|reader\.do|signup|login|/profile|/follow|'
                         r'/intent/|sharer|/share|line\.me/R/msg|/feed\b|/rss|\.xml$|/comment|/trackback|/admin|wp-login', re.I)
# ブログの「カテゴリ・テーマ」の一覧（公式サイトがブログのとき、釣果だけをまとめたページ）
CATEGORY_URL = re.compile(r'blog-category-\d+|/theme-?\d*|/category/|[?&]cat=|/tag/|/categories/|/archives?/category', re.I)
PRIVACY_TEXT = re.compile(r'個人情報|プライバシー|規約|免責|著作権')
# URLだけで釣果のページと分かるもの（釣割系テンプレートの catch.html は一覧を JavaScript で読み込むので本文では確かめられない）
URL_CATCH_STRONG = re.compile(r'chou?ka|tyou?ka|cyou?ka|/catch(\.html?|\.php|/|$)', re.I)
# ブログサービスそのもの（フッターの広告リンクなど）。ユーザーのブログではない
PLATFORM_ROOT = re.compile(r'^(www\.)?(jugem\.jp|wordpress\.com|ameblo\.jp|ameba\.jp|fc2\.com|blog\.fc2\.com|hatenablog\.com|hatena\.ne\.jp|'
                           r'livedoor\.com|livedoor\.blog|blog\.livedoor\.jp|note\.com|seesaa\.net|exblog\.jp|blogger\.com|blogspot\.com|goo\.ne\.jp|blog\.goo\.ne\.jp|'
                           r'cocolog-nifty\.com|jimdo\.com|wix\.com|amebaownd\.com|naturum\.ne\.jp)$', re.I)
SUBDOMAIN_PLATFORM = re.compile(r'^(www\.)?(jugem\.jp|wordpress\.com|fc2\.com|blog\.fc2\.com|hatenablog\.com|seesaa\.net|exblog\.jp|'
                                r'blogspot\.com|blogger\.com|cocolog-nifty\.com|jimdo\.com|wix\.com|amebaownd\.com|naturum\.ne\.jp)$', re.I)
OTHER_BUSINESS_TEXT = re.compile(r'釣具|ショップ|shop|店|協会|組合|漁協|連合|観光|ホテル|旅館|市役所|町役場', re.I)
DEAD_URL = re.compile(r'error|404|not[-_]?found', re.I)
DATED_TEXT = re.compile(r'\d{1,2}\s*月\s*\d{1,2}\s*日|20\d\d\s*[./年]\s*\d{1,2}|\d{1,2}/\d{1,2}(?!\d)')
EVIDENCE = re.compile(r'釣果|\d+\s*(匹|尾|杯|枚|本|cm|ｃｍ|㎝|センチ|kg|ｋｇ|キロ)|釣れ|ヒット|竿頭|トップ\s*\d', re.I)


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + msg + '\n')


def norm(u):
    u = re.sub(r'^https?://(www\.)?', '', (u or '').strip(), flags=re.I)
    u = re.sub(r'/(index|default|top|home)\.(html?|php|asp)$', '', u, flags=re.I)
    return u.rstrip('/').lower()


def host(u):
    return urlparse(u).netloc.lower().split(':')[0]


def anchors(base, html, depth=0):
    """(URL, 文字) の一覧。フレーム・iframe の中身（同じサイト）もたどる。"""
    soup = BeautifulSoup(html, 'html.parser')
    out = []
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not href or href.startswith(('mailto:', 'tel:', 'javascript:')):
            continue
        u = urljoin(base, href)
        if not u.startswith('http') or SKIP_EXT.search(u):
            continue
        text = ' '.join([a.get_text(' ', strip=True), a.get('title') or ''] + [i.get('alt') or '' for i in a.find_all('img')])
        ctx = a.parent.get_text(' ', strip=True) if a.parent is not None else ''
        out.append((u, re.sub(r'\s+', ' ', text).strip()[:80], re.sub(r'\s+', ' ', ctx).strip()[:120]))
    # 画像地図（area）も
    for a in soup.find_all('area', href=True):
        u = urljoin(base, a['href'].strip())
        if u.startswith('http'):
            out.append((u, (a.get('alt') or a.get('title') or '')[:80], ''))
    if depth < 2:
        frames = [urljoin(base, f['src']) for f in soup.find_all(['frame', 'iframe'], src=True)]
        for fu in [f for f in frames if host(f) == host(base)][:4]:
            f2, h2 = fetch(fu)
            if h2:
                out += anchors(f2, h2, depth + 1)
    return out


def tidy(u):
    return re.sub(r'^(https://[^/]+):443(?=/|$)|^(http://[^/]+):80(?=/|$)', lambda m: m.group(1) or m.group(2), u)


def ameba_fix(u):
    """profile.ameba.jp/ameba/<id> → ameblo.jp/<id>/（プロフィールではなくブログへ）"""
    m = re.match(r'https?://(?:profile\.ameba\.jp/ameba|(?:www\.)?ameba\.jp/profile/general)/([\w-]+)', u)
    return 'https://ameblo.jp/%s/' % m.group(1) if m else u


def score(u, text, site_top, ctx=''):
    path = urlparse(u).path + '?' + urlparse(u).query
    s = 0
    if TEXT_STRONG.search(text):
        s += 10
    elif TEXT_EN.search(text):
        s += 5
    elif SNS_HOST.search(host(u)) and TEXT_STRONG.search(ctx) and len(ctx) <= 80:
        s += 8  # 「釣果はインスタグラムで発信中」の横のアイコン
    if HREF_CATCH.search(path):
        s += 6
    if TEXT_BLOG.search(text):
        s += 3
    if s == 0 or EXCLUDE_URL.search(u) or POST_URL.search(path) or POST_URL.search(u) or PRIVACY_TEXT.search(text):
        return 0  # 記事1本・ブログの操作用ページは使わない
    if BLOG_HOST.search(host(u)) and host(u) != host(site_top):
        s += 2
    if DATED_TEXT.search(text):
        s -= 4
    if '過去' in text:
        s -= 3
    if re.search(r'(?<!\d)20(0\d|1\d|2[0-3])(?!\d)', urlparse(u).path + ' ' + text):
        s -= 4
    if norm(u) == norm(site_top):
        s -= 10
    return s


def kind_of(u, site):
    h = host(u)
    if SNS_HOST.search(h):
        return 'sns'
    for d in CATCH_SITES:
        if h == d or h.endswith('.' + d):
            return 'catchsite'
    if h == host(site) or h.replace('www.', '') == host(site).replace('www.', ''):
        return 'site'
    return 'blog'


def check(u, text, kind, allow_blog_link=True):
    """開いて確かめる。(採用してよいか, 理由, 本文の釣果の記述の数, 最終URL)"""
    final, html = fetch(u)
    if not html:
        return False, 'open_failed', 0, final
    if DEAD_URL.search(urlparse(final).path) and not DEAD_URL.search(urlparse(u).path):
        return False, 'dead', 0, final  # エラーページへ転送された（掲載終了など）
    title, body = page_text(html)
    if len(body) < 1500 and PARKED.search(body):
        return False, 'parked', 0, final
    ev = len(EVIDENCE.findall(body))
    path = urlparse(u).path + '?' + urlparse(u).query
    external_blog = kind == 'blog' and BLOG_HOST.search(host(u))
    if kind == 'blog' and (SUBDOMAIN_PLATFORM.search(host(u)) or PLATFORM_ROOT.search(host(u)) and len(urlparse(u).path.strip('/')) < 3
                           or 'ref=' in urlparse(u).query):
        return False, 'platform', 0, final  # ブログサービス自体のページ・広告
    if kind == 'blog' and not external_blog and not urlparse(u).path.strip('/') and not TEXT_STRONG.search(text):
        return False, 'portal_top', ev, final  # 別のサイトのトップ（釣果ポータルなど）は船宿のページではない
    if kind == 'blog' and OTHER_BUSINESS_TEXT.search(text) and not TEXT_STRONG.search(text):
        return False, 'other_business', ev, final
    if TEXT_STRONG.search(text):
        return True, 'text', ev, final
    if TEXT_STRONG.search(title):
        return True, 'title', ev, final
    if URL_CATCH_STRONG.search(path):
        return True, 'url', ev, final
    if allow_blog_link and external_blog and re.search(r'ブログ|blog|日誌|日記', text, re.I) and not OTHER_BUSINESS_TEXT.search(text):
        # 公式サイトから「ブログ」「船長日誌」と案内している外部ブログ（Ameba などは本文を JavaScript で出すので数えられない）
        return True, 'blog_link', ev, final
    if ev >= 3 and (kind != 'blog' or external_blog and allow_blog_link):
        return True, 'evidence', ev, final
    return False, 'weak_evidence', ev, final


def find(site):
    final, html = fetch(site)
    if not html:
        return {'site': site, 'state': 'no_top'}
    seen, cands = set(), []
    for u, text, ctx in anchors(final, html):
        if host(u) == host(final) and '#' in u and norm(u.split('#')[0]) == norm(final):
            continue  # ページ内の見出しへのリンク（「釣果はFacebookで」と書いてあるだけのことがある）
        u = tidy(ameba_fix(u.split('#')[0]))
        key = norm(u)
        s = score(u, text, final, ctx)
        if s > 0 and not TEXT_STRONG.search(text) and SNS_HOST.search(host(u)) and TEXT_STRONG.search(ctx):
            text = (text + ' ' + ctx).strip()[:80]  # まわりの文で「釣果」と分かる SNS
        if s <= 0 or key in seen:
            continue
        seen.add(key)
        cands.append((s, u, text))
    cands.sort(key=lambda c: -c[0])
    if BLOG_SITE.search(host(final)):
        cands = [c for c in cands if TEXT_STRONG.search(c[2]) and CATEGORY_URL.search(c[1])]
        if not cands:
            # 公式サイトがブログで、釣果だけのカテゴリが無い → 公式サイト自体が釣果ブログかどうか（本文の釣果の記述）
            ev = len(EVIDENCE.findall(page_text(html)[1]))
            return {'site': site, 'state': 'self', 'kind': 'self', 'how': 'blog_site', 'evidence': ev}
    n_blog_links = sum(1 for _, u, t in cands if host(u) != host(final) and BLOG_HOST.search(host(u))
                       and re.search(r'ブログ|blog|日誌|日記', t, re.I) and not TEXT_STRONG.search(t))
    tried = []
    for allow in (False, n_blog_links <= 1):
        for s, u, text in cands[:5]:
            k = kind_of(u, final)
            if k == 'sns' or k == 'catchsite':
                # SNS は開けない（ログインが要る）、釣果サイトは店のページへのリンクなら採る。どちらも文字に「釣果」があるときだけ
                pq = urlparse(u).path.strip('/') + '?' + urlparse(u).query
                specific = (k == 'sns' and urlparse(u).path.strip('/')
                            or k == 'catchsite' and (re.search(r'\d|[?&]\w+=', pq) or urlparse(u).path.strip('/').count('/') >= 1))
                if not (TEXT_STRONG.search(text) and specific):
                    tried.append([u, 'not_explicit'])
                    continue
                if k == 'sns':
                    return {'site': site, 'state': 'ok', 'url': u, 'text': text, 'kind': k, 'how': 'text', 'score': s}
            ok, why, ev, fin = check(u, text, k, allow)
            tried.append([u, why])
            if ok:
                if norm(fin) == norm(final) and '#' not in u:
                    return {'site': site, 'state': 'self', 'url': u, 'text': text, 'kind': 'self', 'how': why, 'evidence': ev}
                return {'site': site, 'state': 'ok', 'url': u, 'text': text, 'kind': k, 'how': why, 'evidence': ev, 'score': s}
    return {'site': site, 'state': 'none', 'tried': tried, 'n_cands': len(cands)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()
    sites = {}
    for f in glob.glob(os.path.join(ROOT, 'data', 'detail', '*.json')):
        for b in json.load(open(f, encoding='utf-8')).values():
            if b.get('website'):
                sites.setdefault(b['website'], []).append(b['id'])
    todo = sorted(sites)
    if args.limit:
        todo = todo[:args.limit]
    log('start %d sites' % len(todo))
    out, done = {}, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(find, s): s for s in todo}
        for fu in concurrent.futures.as_completed(futs):
            s = futs[fu]
            try:
                r = fu.result()
            except Exception as e:  # 1サイトの失敗で全体を止めない
                r = {'site': s, 'state': 'error', 'error': '%s: %s' % (type(e).__name__, e)}
            r['boats'] = sites[s]
            out[url_key(s) or s] = r
            done += 1
            if done % 100 == 0:
                save_json(OUT, out)
                states = {}
                for v in out.values():
                    states[v['state']] = states.get(v['state'], 0) + 1
                log('%d/%d %s' % (done, len(todo), json.dumps(states, ensure_ascii=False)))
    save_json(OUT, out)
    states = {}
    for v in out.values():
        states[v['state']] = states.get(v['state'], 0) + 1
    log('DONE %d sites %s' % (len(out), json.dumps(states, ensure_ascii=False)))
    print('DONE', len(out), states)


if __name__ == '__main__':
    main()
