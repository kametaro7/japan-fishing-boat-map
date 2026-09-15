#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公式サイトの取得に失敗したもの（work/official/index.json の state=unreachable）を、システムの curl で確かめ直す。

  python3 tools/check_links.py [--all]

Python（LibreSSL 2.8.3）では TLS で失敗しても、curl なら開けるサイトがあるため。結果は work/official/linkcheck.json:
  {website: {"verdict": "alive" | "dead" | "unknown", "code": HTTPコード, "exit": curlの終了コード, "final": 最終URL}}
dead（名前解決できない・接続拒否・404/410・失効ドメインへの転送）は build.py がリンクを外す。
"""
import concurrent.futures
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, load_json, save_json, host_of  # noqa: E402

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36'
OUT = os.path.join(WORK, 'official', 'linkcheck.json')
PARKING_HOSTS = ('sedoparking.com', 'dan.com', 'afternic.com', 'hugedomains.com', 'onamae.com', 'parkingcrew.net', 'bodis.com')


def check(url):
    cmd = ['curl', '-sS', '-L', '--max-redirs', '6', '-o', '/dev/null', '-m', '30', '-A', UA,
           '-H', 'Accept: text/html,application/xhtml+xml,*/*;q=0.8', '-H', 'Accept-Language: ja,en;q=0.8',
           '-w', '%{http_code} %{url_effective}', url]
    res = None
    for _ in range(2):
        p = subprocess.run(cmd, capture_output=True, text=True)
        out = (p.stdout or '').strip().split(' ', 1)
        code = int(out[0]) if out and out[0].isdigit() else 0
        final = out[1] if len(out) > 1 else url
        res = {'code': code, 'exit': p.returncode, 'final': final}
        if p.returncode not in (28, 35, 56):  # タイムアウト・TLS失敗・受信失敗は1回だけやり直す
            break
    res['verdict'] = verdict_of(res)
    return url, res


def dns_resolves(host):
    """公開DNS（8.8.8.8）で A レコードが引けるか。"""
    if not host:
        return False
    p = subprocess.run(['dig', '+short', '+time=3', '+tries=2', '@8.8.8.8', host, 'A'], capture_output=True, text=True)
    return any(re.match(r'^\d+\.\d+\.\d+\.\d+$', line.strip()) for line in (p.stdout or '').splitlines())


ERROR_PAGE = re.compile(r'^https?://(error\.fc2\.com|[^/]+/(404|403|error|notfound|not_found)(\.html?)?(\?|$))', re.I)


def verdict_of(res):
    code, ex, final = res.get('code') or 0, res.get('exit'), res.get('final') or ''
    if any(host_of(final).endswith(h) for h in PARKING_HOSTS) or ERROR_PAGE.match(final):
        return 'dead'  # パーキング・ホスティング側のエラーページへの転送（削除済みの FC2 など）
    if ex in (6, 7) or code in (404, 410):
        return 'dead'  # 名前解決できない・接続拒否・ページが無い
    if ex == 0 and 200 <= code < 400:
        return 'alive'
    if ex == 0 and code in (401, 403, 429, 503):
        return 'alive'  # ボット除けや一時的な制限（ブラウザでは開ける可能性が高い）
    return 'unknown'


def main():
    idx = load_json(os.path.join(WORK, 'official', 'index.json'), []) or []
    targets = {x['website'] for x in idx if x.get('website') and ('--all' in sys.argv or x.get('state') == 'unreachable')}
    # トップページだけの確認（tools/validate_sites.py）で取得できなかったサイトも対象
    for url, res in (load_json(os.path.join(WORK, 'official', 'validate.json'), {}) or {}).items():
        if '--all' in sys.argv or res.get('state') == 'unreachable':
            targets.add(url)
    targets = sorted(targets)
    results = load_json(OUT, {}) or {}
    todo = [u for u in targets if u not in results]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for i, (url, res) in enumerate(ex.map(check, todo), 1):
            results[url] = res
            if i % 50 == 0:
                save_json(OUT, results, indent=1)
    if '--recheck-dns' in sys.argv:
        # 並列実行中に名前解決が一時的に失敗したものを拾い直す: 公開DNS（8.8.8.8）で引けるドメインだけ、直列で curl し直す
        again = [u for u, r in results.items() if r.get('exit') == 6 and dns_resolves(host_of(u))]
        for u in again:
            _, res = check(u)
            results[u] = res
        print('rechecked %d urls whose domain resolves via 8.8.8.8' % len(again))
    for res in results.values():  # 判定規則を変えたときに、保存済みの結果にも適用し直す
        res['verdict'] = verdict_of(res)
    save_json(OUT, results, indent=1)
    c = {}
    for u in targets:
        v = results.get(u, {}).get('verdict')
        c[v] = c.get(v, 0) + 1
    print('checked %d (new %d): %s' % (len(targets), len(todo), c))


if __name__ == '__main__':
    main()
