#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公式サイトからの抽出結果（work/enrich/*.json）の数字を、取得した原文（work/official/pages）で機械的に照合する。

  python3 tools/verify_enrich.py            # 照合して work/enrich/_verify.json に報告
  python3 tools/verify_enrich.py --apply    # 原文に見つからない price / depart / return を null にする（修正担当の後の仕上げ）

料金は「9000」「9,000」「9.000」「9千」「1万」「1万5千」「1.5万」などの表記を、時刻は「5:30」「05:30」「5時30分」「5時半」「5時」を探す。
"""
import glob
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import WORK, load_json, save_json  # noqa: E402


def norm_text(t):
    t = unicodedata.normalize('NFKC', t)
    # 区切りを先に消す（空白を先に消すと「20,000 1名」→「20,0001名」になって区切りと認識できない）
    t = re.sub(r'(?<=\d)[ \t]*[,.，．、][ \t]*(?=\d{3}(?!\d))', '', t)  # 9,000 / 9.000 / 9、000 / 100 , 000 → 9000
    t = re.sub(r'[\s_]+', '', t)  # 表の整形で入る「午後 _ 2:00」の _ も消す
    t = re.sub(r'(?<=\d);(?=\d{2})', ':', t)  # 「7;30~13;30」
    return t.upper()


def time_in_text(v, text):
    """時刻 v（HH:MM）が原文にあるか。表記のゆれに加え、「5~6時出港」「午後13~17時」のような範囲の端も認める。"""
    if any(f in text for f in time_forms(v)):
        return True
    h, m = [int(x) for x in v.split(':')]
    if m:
        return False
    if h == 12 and '正午' in text:
        return True
    for hh in [h] + ([h - 12] if h >= 13 else []):
        for mt in re.finditer(r'(?<!\d)(%d[~〜～\-]\d{1,2}|\d{1,2}[~〜～\-]%d)時' % (hh, hh), text):
            # 12時間表記の範囲（「6~10時」を夕方と読む等）は、直前に午後・PM・夜・夕があるときだけ認める
            if hh == h or re.search(r'午後|PM|夜|夕', text[max(0, mt.start() - 6):mt.start()]):
                return True
    return False


def price_forms(p):
    forms = {str(p)}
    man, rest = divmod(p, 10000)
    sen, rest2 = divmod(rest, 1000)
    if p % 1000 == 0:
        if man and sen:
            forms.add('%d万%d千' % (man, sen))
            if sen == 5:
                forms.add('%d.5万' % man)
        elif man:
            forms.add('%d万' % man)
        elif sen:
            forms.add('%d千' % sen)
    if p % 10000 and p > 10000 and rest2 == 0 and sen:
        forms.add('%d.%d万' % (man, sen))
    if man and rest:
        forms.add('%d万%d' % (man, rest))  # 1万4000円
    # 漢数字（「お一人様一万円」「一万五千円」）
    kd = '〇一二三四五六七八九'
    if p % 1000 == 0 and man < 10:
        k = (kd[man] + '万' if man else '') + (kd[sen] + '千' if sen else '')
        if k:
            forms.add(k)
    return forms


def time_forms(hhmm):
    h, m = [int(x) for x in hhmm.split(':')]
    forms = {'%d:%02d' % (h, m), '%02d:%02d' % (h, m), '%d時%d分' % (h, m), '%d時%02d分' % (h, m)}
    if m:
        forms.add('%d時%02d' % (h, m))  # 4時15出船
    if m == 30:
        forms.add('%d時半' % h)
    if m == 0:
        forms.add('%d時' % h)
    if h == 24:  # 「夜中12時」「午前0時」
        forms.update({'夜中12時', '午前0時', '深夜0時', '0:%02d' % m, '24時'})
    if 12 <= h < 24:  # 午後・PM 表記（正午は「PM0:00」「午後0時」とも書く）。「3:00pm」「夕方6時」「夜8時」も
        for hh in ([h - 12] if h > 12 else [12, 0]):
            forms.update({'午後%d時' % hh, '午後%d:%02d' % (hh, m), 'PM%d時' % hh, 'PM%d:%02d' % (hh, m),
                          '%d:%02dPM' % (hh, m), '夕方%d時' % hh, '夜%d時' % hh, '夜中%d時' % hh})
    elif h < 12:
        forms.update({'午前%d時' % h, '午前%d:%02d' % (h, m), 'AM%d時' % h, 'AM%d:%02d' % (h, m), '%d:%02dAM' % (h, m)})
    if h < 6:  # 深夜の「25:00」「26時」表記
        forms.update({'%d:%02d' % (h + 24, m), '%d時' % (h + 24)})
    if h >= 13:
        # 「2時沖上がり」「沖上がり2時30分」のような12時間表記は、沖上がり・帰港・出船などの語と隣り合っているときだけ同じ時刻とみなす
        # （語の無い「2時」だけでは、無関係な数字で誤って裏付けてしまうので認めない）
        h12 = h - 12
        clock = {'%d:%02d' % (h12, m), ('%d時' % h12) if m == 0 else ('%d時%02d分' % (h12, m))}
        if m:
            clock.add('%d時%02d' % (h12, m))
        if m == 30:
            clock.add('%d時半' % h12)
        for kw in ('沖上がり', '沖揚がり', '沖あがり', '帰港', '納竿', '上がり', '出船', '出航', '集合'):
            for c in clock:
                forms.update({kw + c, c + kw, c + '頃' + kw, kw + c + '頃'})
    return forms


def check_site(e, text):
    # サイト側の区切りの誤記（「¥6,3000」= 63,000円、「¥10,0000」= 100,000円）も照合できるよう、
    # 数字の間の区切りをすべて消した版でも探す（厳密な版で見つからなかったときの予備）
    loose = re.sub(r'(?<=\d)[,.，．、](?=\d)', '', text)
    flags = []
    for i, p in enumerate(e.get('plans') or []):
        if not isinstance(p, dict):
            continue
        if isinstance(p.get('price'), int) and not any(f in text or f in loose for f in price_forms(p['price'])):
            flags.append({'plan': i, 'field': 'price', 'value': p['price'], 'name': p.get('name')})
        for fld in ('depart', 'return'):
            v = p.get(fld)
            if isinstance(v, str) and re.match(r'^\d{2}:\d{2}$', v) and not time_in_text(v, text):
                flags.append({'plan': i, 'field': fld, 'value': v, 'name': p.get('name')})
    return flags


def main():
    apply = '--apply' in sys.argv
    # 修正担当が原文と照合して「正しい」と確認済みの値（機械照合では表記の都合で見つからないもの）は報告しない
    # 形式: {key: [[plan添字, field, value, 根拠のメモ], ...]}
    confirmed = load_json(os.path.join(WORK, 'enrich', '_confirmed.json'), {}) or {}
    report, n_sites, n_plans, n_flags = {}, 0, 0, 0
    for path in sorted(glob.glob(os.path.join(WORK, 'enrich', 'batch_*.json'))):
        data = load_json(path, []) or []
        changed = False
        for e in data:
            key = e.get('key')
            page = load_json(os.path.join(WORK, 'official', 'pages', '%s.json' % key), {}) or {}
            text = norm_text('\n'.join(p.get('text', '') for p in page.get('pages', [])))
            n_sites += 1
            n_plans += len(e.get('plans') or [])
            flags = check_site(e, text)
            ok = {(c[0], c[1], str(c[2])) for c in confirmed.get(key, [])}
            flags = [f for f in flags if (f['plan'], f['field'], str(f['value'])) not in ok]
            if flags:
                report[key] = {'file': os.path.basename(path), 'flags': flags}
                n_flags += len(flags)
                if apply:
                    for f in flags:
                        e['plans'][f['plan']][f['field']] = None
                    changed = True
        if apply and changed:
            save_json(path, data, indent=1)
    save_json(os.path.join(WORK, 'enrich', '_verify.json'), report, indent=1)
    print('sites %d, plans %d, flagged values %d in %d sites%s'
          % (n_sites, n_plans, n_flags, len(report), ' (nulled)' if apply else ''))


if __name__ == '__main__':
    main()
