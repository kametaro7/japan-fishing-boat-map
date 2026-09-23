/* 全国釣り船マップ — data/boats.js（tools/build.py が生成）で地図と一覧を描き、詳細は data/detail/NN.json から読む */
(function () {
  'use strict';

  const DATA = window.BOAT_DATA || { generated: '', sources: {}, targets: [], boats: [] };
  const PREFS = ['北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県', '群馬県',
    '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県', '山梨県', '長野県',
    '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県',
    '鳥取県', '島根県', '岡山県', '広島県', '山口県', '徳島県', '香川県', '愛媛県', '高知県', '福岡県',
    '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県', '鹿児島県', '沖縄県'];
  const REGIONS = [
    ['北海道', [1]], ['東北', [2, 3, 4, 5, 6, 7]], ['関東', [8, 9, 10, 11, 12, 13, 14]],
    ['中部', [15, 16, 17, 18, 19, 20, 21, 22, 23]], ['近畿', [24, 25, 26, 27, 28, 29, 30]],
    ['中国', [31, 32, 33, 34, 35]], ['四国', [36, 37, 38, 39]], ['九州', [40, 41, 42, 43, 44, 45, 46]], ['沖縄', [47]],
  ];
  const REGION_OF = {};
  REGIONS.forEach(([g, codes]) => codes.forEach((c) => { REGION_OF[c] = g; }));
  // tools/build.py の F_* と同じビット
  const F = { PLANS: 1, WEB: 2, BOOK: 4, NORIAI: 8, SHITATE: 16, TOSEN: 32, REG: 64, APPROX: 128, LISTED: 256 };
  const KINDS = [['n', '乗合', F.NORIAI], ['s', '仕立', F.SHITATE], ['t', '渡船・瀬渡し', F.TOSEN]];
  const KIND_FLAG = Object.fromEntries(KINDS.map((k) => [k[0], k[2]]));
  const CAPS = [7000, 10000, 13000, 16000];
  const CAT_LABEL = ['料金・プランあり', '掲載・公式サイトあり', '登録簿のみ'];
  const BASEMAPS = {
    light: { label: '淡色', url: 'https://tiles.openfreemap.org/styles/positron' },
    color: { label: 'カラー', url: 'https://tiles.openfreemap.org/styles/liberty' },
    dark: { label: 'ダーク', url: 'https://tiles.openfreemap.org/styles/dark' },
  };
  const JAPAN = [[122.6, 24.0], [146.0, 45.7]];
  const FISH_SHOWN = 18;
  const PAGE = 100;
  const PLANS_SHOWN = 6;

  const $ = (sel, root = document) => root.querySelector(sel);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 保存できない環境 */ } },
  };
  const isMobile = () => window.matchMedia('(max-width: 820px)').matches;
  const canHover = window.matchMedia('(hover: hover)').matches;

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function keyEl(cat) {
    const k = el('span', 'key c' + cat);
    k.setAttribute('aria-hidden', 'true');
    return k;
  }
  function extLink(href, cls, text) {
    const a = el('a', cls);
    a.href = href;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.append(el('span', null, text), el('span', 'ext', '↗'));
    return a;
  }
  const yen = (n) => Number(n).toLocaleString('ja-JP') + '円';
  // 料金の単位の判定（tools/build.py の price_unit と同じ規則）。1人あたりの明記を、貸切・隻などの語より優先する
  const PER_PERSON_RE = /\/人|\/1名|\/1人|1名様|1人|1名|一人|お一人|おひとり|人あたり/;
  const PER_PERSON_STRIP = /\/1名|\/1人|\/人|1名様|1人|1名|一人|お一人様?|おひとり様?|人あたり/g;
  const PER_BOAT_RE = /隻|貸切|貸し切り|仕立|チャーター|名様?まで|人まで|名迄|[0-9０-９一二三四五六七八九十]台/;
  const GROUP_RANGE_RE = /[0-9０-９]\s*名様?\s*[~〜～\-]\s*[0-9０-９]+\s*名/;  // 「1名様〜4名様 52,000円」は人数まとめの料金
  function priceUnit(p) {
    const t = p.price_text || '';
    if (p.kind === '仕立') return 'boat';
    if (p.kind === '乗合') return 'person';
    if (GROUP_RANGE_RE.test(t)) return 'boat';
    if (PER_PERSON_RE.test(t)) return 'person';
    if (PER_BOAT_RE.test(t)) return 'boat';
    return '';
  }
  function norm(s) {
    return String(s || '').normalize('NFKC').toLowerCase()
      .replace(/[ァ-ヶ]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0x60))
      .replace(/[\s・〜~\-－―ー()（）「」『』【】,、。.]/g, '');
  }
  const srcLabel = (k) => (DATA.sources[k] || [k])[0];
  const srcKind = (k) => (DATA.sources[k] || [k, ''])[1];

  // ---------------------------------------------------------------- data
  const TARGETS = DATA.targets || [];
  const boats = DATA.boats.map((r, idx) => {
    const [id, name, kana, lat, lon, pref, place, flags, price, tg] = r;
    const cat = flags & F.PLANS ? 0 : flags & (F.WEB | F.LISTED) ? 1 : 2;
    return {
      idx, id, name, kana, lat, lon, pref, place, flags, price, tg, cat,
      text: norm([name, kana, PREFS[pref - 1], place].join(' ')),
    };
  });
  const byId = new Map(boats.map((b) => [b.id, b]));
  // 一覧の並び: 情報の多い順（料金あり → 掲載あり → 登録簿のみ）、同じ種類の中は build.py の順（都道府県順）
  const ordered = boats.slice().sort((a, b) => a.cat - b.cat || a.idx - b.idx);
  const fishCount = TARGETS.map(() => 0);
  boats.forEach((b) => b.tg.forEach((t) => { fishCount[t] += 1; }));

  // ---------------------------------------------------------------- state
  const fresh = () => ({ q: '', fish: new Set(), kinds: new Set(), cap: 0, region: '', pref: 0, plans: false, web: false, reg: true });
  let state = fresh();
  let visible = [];
  let selected = null;
  let hovered = null;
  let listScroll = 0;
  let listLimit = PAGE;
  let fishExpanded = false;
  let touched = false;         // 利用者やプログラムが地図を動かしたら true（以後、画面サイズが変わっても全国表示へ戻さない）
  let skipMoveRender = false;  // 「一覧に戻る」で地図を戻すときの moveend では一覧を作り直さない
  let basemap = store.get('jfb.basemap');
  if (!BASEMAPS[basemap]) basemap = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';

  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    const s = fresh();
    s.q = h.get('q') || '';
    (h.get('f') || '').split(',').forEach((name) => { const i = TARGETS.indexOf(name); if (i >= 0) s.fish.add(i); });
    (h.get('k') || '').split('').forEach((k) => { if (KIND_FLAG[k]) s.kinds.add(k); });
    const cap = Number(h.get('p'));
    if (CAPS.includes(cap)) s.cap = cap;
    if (REGION_OF[Number(h.get('pf'))]) s.pref = Number(h.get('pf'));
    else if (REGIONS.some((r) => r[0] === h.get('g'))) s.region = h.get('g');
    s.plans = h.get('pl') === '1';
    s.web = h.get('w') === '1';
    s.reg = h.get('r') !== '0';
    const b = byId.get(h.get('b'));
    return { s, sel: b ? b.idx : null };
  }
  function writeHash() {
    const h = new URLSearchParams();
    if (selected != null) h.set('b', boats[selected].id);
    if (state.q) h.set('q', state.q);
    if (state.fish.size) h.set('f', [...state.fish].map((i) => TARGETS[i]).join(','));
    if (state.kinds.size) h.set('k', [...state.kinds].join(''));
    if (state.cap) h.set('p', String(state.cap));
    if (state.pref) h.set('pf', String(state.pref));
    if (state.region) h.set('g', state.region);
    if (state.plans) h.set('pl', '1');
    if (state.web) h.set('w', '1');
    if (!state.reg) h.set('r', '0');
    const str = h.toString();
    history.replaceState(null, '', str ? '#' + str : location.pathname + location.search);
  }

  // ignore: チップの件数を数えるときに外すグループ（'fish' / 'kinds' / 'cap' / 'area'）
  function matches(b, tokens, ignore) {
    if (state.plans && !(b.flags & F.PLANS)) return false;
    if (state.web && !(b.flags & F.WEB)) return false;
    if (!state.reg && b.cat === 2) return false;
    if (ignore !== 'kinds' && state.kinds.size && ![...state.kinds].some((k) => b.flags & KIND_FLAG[k])) return false;
    if (ignore !== 'cap' && state.cap && !(b.price && b.price <= state.cap)) return false;
    if (ignore !== 'area' && state.pref && b.pref !== state.pref) return false;
    if (ignore !== 'area' && state.region && REGION_OF[b.pref] !== state.region) return false;
    if (ignore !== 'fish' && state.fish.size && !b.tg.some((t) => state.fish.has(t))) return false;
    return tokens.every((t) => b.text.includes(t));
  }

  function apply(opts) {
    // 詳細を開いたまま検索欄やトグルを操作したら、結果が見えるように一覧へ戻す
    if (opts && opts.leaveDetail && selected != null) {
      selected = null;
      prevView = null;
      $('#detail').hidden = true;
      $('#browse').hidden = false;
      updateSelection();
    }
    const tokens = state.q.split(/[\s　]+/).map(norm).filter(Boolean);
    const prevCount = visible.length;
    visible = ordered.filter((b) => matches(b, tokens));
    listLimit = PAGE;
    renderControls();
    updateMapData();
    renderList();
    writeHash();
    // fit: true は常に。'auto' は検索語で件数が前より減り、500件以下になったときだけ
    // （語を消して件数が増えたときや、ほかの条件だけで少ないときに、利用者が動かした地図を引き戻さない）
    let fit = !!(opts && opts.fit === true);
    if (opts && opts.fit === 'auto' && tokens.length && visible.length && visible.length <= 500) {
      fit = visible.length < prevCount;
    }
    if (fit) {
      touched = true;
      fitVisible();
    }
  }

  // ---------------------------------------------------------------- panel
  const panel = $('#panel');

  function chip(label, count, pressed, onClick, key) {
    const b = el('button', 'chip' + (count === 0 && !pressed ? ' zero' : ''));
    b.type = 'button';
    if (key) b.dataset.key = key;
    b.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    b.append(el('span', null, label));
    if (count != null) b.append(el('span', 'n', count.toLocaleString('ja-JP')));
    b.addEventListener('click', onClick);
    return b;
  }
  function toggleIn(set, k) { if (set.has(k)) set.delete(k); else set.add(k); }

  function renderControls() {
    // チップを作り直してもキーボードのフォーカスを戻せるよう、押していたチップの鍵を覚えておく
    const ae = document.activeElement;
    const focusKey = ae && ae.dataset ? ae.dataset.key : null;
    const tokens = state.q.split(/[\s　]+/).map(norm).filter(Boolean);
    const base = {};
    // 各グループの件数は「そのグループ以外の今の条件」に合う船宿で数える（押したときに出る件数になるように）
    const countBase = (g) => base[g] || (base[g] = ordered.filter((b) => matches(b, tokens, g)));
    // 魚種は OR で絞るので、押していない魚種の件数は「押したら何件になるか」（今の選択との和集合）で数える
    const fishSel = state.fish;
    let fishM0 = 0;
    const fishExtra = TARGETS.map(() => 0);
    countBase('fish').forEach((b) => {
      if (fishSel.size && b.tg.some((t) => fishSel.has(t))) fishM0 += 1;
      else b.tg.forEach((t) => { fishExtra[t] += 1; });
    });
    const fish = $('#fishChips');
    fish.textContent = '';
    const fishIdx = TARGETS.map((_, i) => i).filter((i) => fishCount[i] > 0);
    // たたんだ状態では、今の条件で増える件数の多い魚種を優先して出す（押している魚種は必ず出す）
    const shownIdx = fishExpanded ? fishIdx
      : fishIdx.slice().sort((a, c) => fishExtra[c] - fishExtra[a] || fishCount[c] - fishCount[a]).filter((i, n) => n < FISH_SHOWN || fishSel.has(i));
    shownIdx.forEach((i) => {
      const pressed = fishSel.has(i);
      const fc = chip(TARGETS[i], pressed ? fishM0 : fishM0 + fishExtra[i], pressed, () => { toggleIn(state.fish, i); apply(); }, 'f:' + i);
      if (!pressed && fishExtra[i] === 0) fc.classList.add('zero');  // 押しても一覧が変わらない
      fish.append(fc);
    });
    const more = $('#moreFish');
    more.hidden = fishIdx.length <= FISH_SHOWN;
    more.textContent = fishExpanded ? '主な魚種だけ表示' : 'ほかの魚種も表示（全' + fishIdx.length + '種）';

    const kinds = $('#kindChips');
    kinds.textContent = '';
    KINDS.forEach(([k, label, flag]) => {
      // 乗船スタイルも OR なので、魚種と同じく「押したら何件になるか」で数える
      const kb = countBase('kinds');
      const inSel = (b) => state.kinds.size > 0 && [...state.kinds].some((s) => b.flags & KIND_FLAG[s]);
      const m0 = kb.filter(inSel).length;
      const extra = kb.filter((b) => (b.flags & flag) && !inSel(b)).length;
      const pressed = state.kinds.has(k);
      const kc = chip(label, pressed ? m0 : m0 + extra, pressed, () => { toggleIn(state.kinds, k); apply(); }, 'k:' + k);
      if (!pressed && extra === 0) kc.classList.add('zero');
      kinds.append(kc);
    });

    const caps = $('#capChips');
    caps.textContent = '';
    CAPS.forEach((c) => {
      const n = countBase('cap').filter((b) => b.price && b.price <= c).length;
      caps.append(chip('〜' + yen(c), n, state.cap === c, () => { state.cap = state.cap === c ? 0 : c; apply(); }, 'p:' + c));
    });

    const reg = $('#regionChips');
    reg.textContent = '';
    REGIONS.forEach(([g]) => {
      const n = countBase('area').filter((b) => REGION_OF[b.pref] === g).length;
      reg.append(chip(g, n, state.region === g, () => {
        state.region = state.region === g ? '' : g;
        state.pref = 0;
        apply({ fit: true });
      }, 'g:' + g));
    });
    const sel = $('#pref');
    if (!sel.options.length) {
      const counts = PREFS.map((_, i) => boats.filter((b) => b.pref === i + 1).length);
      const o0 = el('option', null, '都道府県を選ぶ');
      o0.value = '0';
      sel.append(o0);
      PREFS.forEach((p, i) => {
        const o = el('option', null, p + '（' + counts[i].toLocaleString('ja-JP') + '）');
        o.value = String(i + 1);
        if (!counts[i]) o.disabled = true;
        sel.append(o);
      });
    }
    sel.value = String(state.pref);

    document.querySelectorAll('[data-toggle]').forEach((b) => {
      b.setAttribute('aria-pressed', state[b.dataset.toggle] ? 'true' : 'false');
    });
    $('#optReg').checked = state.reg;
    const q = $('#q');
    if (document.activeElement !== q) q.value = state.q;

    const parts = [];
    if (state.fish.size) parts.push([...state.fish].map((i) => TARGETS[i]).join('・'));
    if (state.kinds.size) parts.push(KINDS.filter((k) => state.kinds.has(k[0])).map((k) => k[1]).join('・'));
    if (state.cap) parts.push('〜' + yen(state.cap));
    if (state.pref) parts.push(PREFS[state.pref - 1]);
    if (state.region) parts.push(state.region);
    if (!state.reg) parts.push('登録簿のみを除く');
    $('#filterSummary').textContent = parts.join(' / ');
    if (focusKey) {
      const f = document.querySelector('[data-key="' + focusKey + '"]');
      if (f) f.focus();
    }
  }

  function renderLegend() {
    const lg = $('#legend');
    lg.textContent = '';
    CAT_LABEL.forEach((label, c) => {
      const s = el('span');
      s.append(keyEl(c), el('span', null, label));
      lg.append(s);
    });
    const s = el('span');
    s.append(el('span', 'clu', '12'), el('span', null, '近くの件数（クリックで拡大）'));
    lg.append(s);
  }

  function inView() {
    // 地図が無い（WebGL 非対応・読み込み失敗）ときだけ全件。タイルやデータの読み込み中でも getBounds は使えるので、
    // loaded() を待たずに範囲で絞る（待つと setData の直後は必ず全件になっていた）
    if (!map.real) return visible;
    const bd = map.getBounds();
    return visible.filter((b) => bd.contains([b.lon, b.lat]));
  }

  function renderCount(n) {
    const c = $('#count');
    c.textContent = '';
    const left = el('span');
    left.append(el('b', null, n.toLocaleString('ja-JP')), document.createTextNode(' 件（地図の範囲）'));
    c.append(left, el('span', null, '条件に合う ' + visible.length.toLocaleString('ja-JP') + ' 件 / 全 ' + boats.length.toLocaleString('ja-JP') + ' 件'));
  }

  function renderList() {
    const list = $('#list');
    list.textContent = '';
    if (!boats.length) {
      renderCount(0);
      list.append(el('div', 'empty', '船宿のデータを読み込めませんでした。時間をおいてページを再読み込みしてください。'));
      return;
    }
    const items = inView();
    renderCount(items.length);
    if (!items.length) {
      const e = el('div', 'empty', visible.length ? '地図の範囲に条件に合う船宿がありません。地図を広げるか移動してください。' : '条件に合う船宿がありません。');
      if (!visible.length) {
        const b = el('button', 'linkbtn', '条件をリセット');
        b.type = 'button';
        b.addEventListener('click', () => { resetFilters(); $('#count').focus({ preventScroll: true }); });
        e.append(el('br'), b);
      }
      list.append(e);
      return;
    }
    const frag = document.createDocumentFragment();
    items.slice(0, listLimit).forEach((b) => {
      const btn = el('button', 'item');
      btn.type = 'button';
      btn.dataset.idx = b.idx;
      const main = el('span', 'main');
      main.append(el('span', 'name', b.name));
      const fish = b.tg.slice(0, 3).map((t) => TARGETS[t]).join('・');
      main.append(el('span', 'op', [PREFS[b.pref - 1] + (b.place ? ' ' + b.place : ''), fish].filter(Boolean).join(' · ')));
      btn.append(keyEl(b.cat), main);
      if (b.price) {
        const p = el('span', 'price');
        p.append(el('small', null, '乗合 '), document.createTextNode(yen(b.price)), el('small', null, '〜'));
        btn.append(p);
      }
      frag.append(btn);
    });
    list.append(frag);
    if (items.length > listLimit) {
      const more = el('button', 'list-more', 'さらに表示（残り ' + (items.length - listLimit).toLocaleString('ja-JP') + ' 件）');
      more.type = 'button';
      more.addEventListener('click', () => {
        const first = listLimit;
        listLimit += PAGE;
        const y = $('#scroll').scrollTop;
        renderList();
        $('#scroll').scrollTop = y;
        const rows = $('#list').querySelectorAll('.item');
        if (rows[first]) rows[first].focus({ preventScroll: true });  // 押したボタンは消えるので、追加された最初の項目へ
      });
      list.append(more);
    }
  }

  $('#list').addEventListener('click', (e) => {
    const b = e.target.closest('.item');
    if (b) select(Number(b.dataset.idx), { fit: true, fromList: true });
  });
  $('#list').addEventListener('mouseover', (e) => {
    const b = e.target.closest('.item');
    setHover(b ? Number(b.dataset.idx) : null);
  });
  $('#list').addEventListener('mouseleave', () => setHover(null));

  // ---------------------------------------------------------------- detail
  const detailCache = new Map();
  function loadDetail(pref) {
    const code = String(pref).padStart(2, '0');
    if (!detailCache.has(code)) {
      detailCache.set(code, fetch('data/detail/' + code + '.json').then((r) => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      }).catch((e) => { detailCache.delete(code); throw e; }));
    }
    return detailCache.get(code);
  }

  const KIND_ORDER = { '乗合': 0, '仕立': 1, '渡船': 2 };
  function planCard(p) {
    const c = el('div', 'plan');
    const head = el('div', 'plan-head');
    const nm = el('div', 'plan-name');
    if (p.kind) nm.append(el('span', 'tag', p.kind));
    nm.append(document.createTextNode(p.name || '（プラン名なし）'));
    head.append(nm);
    if (p.price || p.price_text) {
      const pr = el('div', 'plan-price', p.price ? yen(p.price) : '');
      const ptxt = p.price_text || '';
      const unit = priceUnit(p);
      if (p.price && unit === 'person') pr.append(el('small', null, '1人'));
      // 原文から「税込」と（「1人」を表示したときだけ）1人あたりの表記を消し、数字以外の但し書き（〜・税別・別途・人数など）が残るなら原文を添える
      let stripped = ptxt.replace(/（税込）|\(税込\)|税込/g, '');
      if (unit === 'person') stripped = stripped.replace(PER_PERSON_STRIP, '');
      const rest = stripped.replace(/[\d,，.．円¥￥\s]/g, '');
      if (ptxt && (!p.price || rest || stripped.replace(/[^\d]/g, '') !== String(p.price))) {
        pr.append(el('small', null, ptxt));
      }
      head.append(pr);
    }
    c.append(head);
    const meta = el('div', 'plan-meta');
    if (p.depart) meta.append(el('span', 'time', p.depart + ' 出船' + (p.return ? ' → ' + p.return + ' 帰港' : '')));
    if (p.targets && p.targets.length) meta.append(el('span', null, p.targets.join('・')));
    if (meta.childNodes.length) c.append(meta);
    const sub = [p.meet ? '集合: ' + p.meet : '', p.season ? '期間: ' + p.season : '', p.days ? '出船日: ' + p.days : '', p.includes ? '料金に含む: ' + p.includes : ''].filter(Boolean);
    if (sub.length || p.url) {
      const s = el('div', 'plan-sub', sub.join(' / '));
      if (p.url) {
        if (sub.length) s.append(document.createTextNode(' '));
        const a = el('a', null, srcLabel(p.src) + 'で見る ↗');
        a.href = p.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        s.append(a);
      }
      c.append(s);
    }
    return c;
  }

  function renderDetail(b) {
    const d = $('#detail');
    d.textContent = '';
    const back = el('button', 'back', '← 一覧に戻る');
    back.type = 'button';
    back.addEventListener('click', deselect);
    d.append(back);
    const cat = el('div', 'cat');
    cat.append(keyEl(b.cat), el('span', null, CAT_LABEL[b.cat]));
    d.append(cat, el('h2', null, b.name));
    if (b.kana && b.kana !== b.name) d.append(el('p', 'kana', b.kana));
    d.append(el('p', 'place', PREFS[b.pref - 1] + (b.place ? ' ' + b.place : '')));
    const body = el('div');
    body.append(el('p', 'loading', '詳しい情報を読み込んでいます…'));
    d.append(body);
    loadDetail(b.pref).then((all) => {
      if (selected !== b.idx) return;
      body.textContent = '';
      const rec = all[b.id];
      if (!rec) { body.append(el('p', 'box-note', 'この船宿の詳しい情報が見つかりませんでした。')); return; }
      fillDetail(body, b, rec);
    }).catch(() => {
      if (selected !== b.idx) return;
      body.textContent = '';
      const note = el('p', 'box-note', '詳しい情報を読み込めませんでした。通信状態を確認してください。 ');
      const retry = el('button', 'linkbtn', 'もう一度読み込む');
      retry.type = 'button';
      retry.addEventListener('click', () => {
        if (selected !== b.idx) return;
        renderDetail(b);
        const h = $('#detail h2');
        if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }  // 押したボタンは消えるので見出しへ
      });
      note.append(retry);
      body.append(note);
    });
  }

  function fillDetail(body, b, r) {
    const links = r.links || [];
    const booking = links.filter((l) => srcKind(l.src) === 'booking');
    const pills = el('div', 'pills');
    (r.types || []).forEach((t) => pills.append(el('span', 'pill', t)));
    if (booking.length) pills.append(el('span', 'pill yes', 'ネット予約あり'));
    if (r.registry && r.registry.length) pills.append(el('span', 'pill', '遊漁船業者登録あり'));
    if (pills.childNodes.length) body.append(pills);

    const actions = el('div', 'actions');
    if (r.website) actions.append(extLink(r.website, 'btn primary', '公式サイト'));
    const row = el('div', 'row');
    if (r.tel) {
      const a = el('a', 'btn');
      a.href = 'tel:' + r.tel.replace(/[^\d]/g, '');
      a.textContent = '📞 ' + r.tel;
      row.append(a);
    }
    // 同じ掲載元に2回載っている船宿（名寄せでまとめたもの）や Facebook が2つある船宿は、同じ名前のボタンが並ばないよう番号を付ける
    const seen = new Map();
    const label = (s) => { const n = (seen.get(s) || 0) + 1; seen.set(s, n); return n > 1 ? s + '（' + n + '）' : s; };
    booking.forEach((l) => row.append(extLink(l.url, 'btn' + (r.website ? '' : ' primary'), label(srcLabel(l.src) + 'で予約'))));
    if (row.childNodes.length) actions.append(row);
    const others = links.filter((l) => srcKind(l.src) !== 'booking');
    const sns = r.sns || [];
    if (others.length || sns.length) {
      const row2 = el('div', 'row');
      others.forEach((l) => row2.append(extLink(l.url, 'btn small', label(srcLabel(l.src) + 'の掲載ページ'))));
      sns.forEach((u) => {
        const host = (u.match(/^https?:\/\/(?:www\.)?([^/]+)/) || [])[1] || '';
        const name = /instagram/.test(host) ? 'Instagram' : /facebook|fb\.com/.test(host) ? 'Facebook' : /twitter|x\.com/.test(host) ? 'X' : /(^|\.)line\.me$|^lin\.ee$/.test(host) ? 'LINE' : /threads\.net$/.test(host) ? 'Threads' : /youtu/.test(host) ? 'YouTube' : /tiktok/.test(host) ? 'TikTok' : host;
        row2.append(extLink(u, 'btn small', label(name)));
      });
      actions.append(row2);
    }
    if (actions.childNodes.length) body.append(actions);

    const plans = (r.plans || []).slice().sort((x, y) =>
      (KIND_ORDER[x.kind] != null ? KIND_ORDER[x.kind] : 3) - (KIND_ORDER[y.kind] != null ? KIND_ORDER[y.kind] : 3) ||
      (x.price || 1e9) - (y.price || 1e9));
    const h = el('h3', null, 'プラン・料金・出船時刻');
    if (plans.length) h.append(el('span', 'n', plans.length + '件'));
    body.append(h);
    if (plans.length) {
      const box = el('div', 'plans');
      plans.forEach((p, i) => {
        const card = planCard(p);
        if (i >= PLANS_SHOWN) card.hidden = true;
        box.append(card);
      });
      if (plans.length > PLANS_SHOWN) {
        const more = el('button', 'linkbtn plans-more', 'すべてのプランを表示（ほか ' + (plans.length - PLANS_SHOWN) + ' 件）');
        more.type = 'button';
        more.addEventListener('click', () => {
          const shown = box.querySelectorAll('.plan[hidden]');
          shown.forEach((c) => { c.hidden = false; });
          if (shown[0]) { shown[0].tabIndex = -1; shown[0].focus({ preventScroll: true }); }  // 押したボタンは消えるので、出てきた最初のプランへ
          more.remove();
        });
        box.append(more);
      }
      body.append(box);
    } else {
      body.append(el('p', 'box-note', r.website
        ? '掲載元に料金・出船時刻の情報がありません。公式サイトで確認してください。'
        : r.tel ? '料金・出船時刻の情報がありません。電話で問い合わせてください。' : '料金・出船時刻の情報がありません。'));
    }
    if (r.schedule_text) body.append(el('p', 'box-note', r.schedule_text));

    const kv = el('dl', 'kv');
    const put = (k, v) => {
      if (v == null || v === '' || (Array.isArray(v) && !v.length)) return;
      kv.append(el('dt', null, k));
      const dd = el('dd');
      if (typeof v === 'string') dd.textContent = v; else if (Array.isArray(v)) dd.textContent = v.join('、'); else dd.append(v);
      kv.append(dd);
    };
    put('主な釣り物', r.targets);
    put('釣り方', r.methods);
    put('所在地', r.address);
    put('港', r.port);
    // 港が分からず住所から位置を出した船宿は、地図の印が港ではないことを書いておく
    put('地図の印', { town: '住所（事業所）のおおよその位置。乗船場所とは離れていることがあります',
      city: '市区町村のおおよその位置。乗船場所とは離れていることがあります' }[r.geo]);
    put('定休日', r.holidays);
    put('アクセス', r.access);
    put('設備', r.facilities);
    put('定員', r.capacity ? r.capacity + '名' : null);
    put('船名', r.boats);
    put('紹介', r.description);
    (r.registry || []).forEach((g) => {
      const dd = el('span');
      dd.append(document.createTextNode([g.pref, g.reg_no ? '登録番号 ' + g.reg_no : '', g.valid_until ? '（有効期限 ' + g.valid_until + '）' : ''].filter(Boolean).join(' ')));
      if (g.src_url) {
        dd.append(document.createTextNode(' '));
        const a = el('a', null, '登録簿' + (g.as_of ? '（' + g.as_of + '時点）' : '') + ' ↗');
        a.href = g.src_url; a.target = '_blank'; a.rel = 'noopener noreferrer';
        dd.append(a);
      }
      put('遊漁船業者登録', dd);
    });
    body.append(kv);

    const notes = ['料金・出船時刻・プランは掲載元から ' + (r.fetched || DATA.generated) + ' に取得した情報です。変わっていることがあるため、予約・乗船前に必ず船宿へ確認してください。'];
    if (b.flags & F.APPROX) notes.push('地図の位置は住所・港名・湖の名前などから求めたおおよその位置で、乗船場所とは異なることがあります。');
    body.append(el('p', 'note', notes.join(' ')));
  }

  // 詳細を開くときに地図を動かしたら、その前の中心・拡大率と表示件数を覚えておく（「一覧に戻る」で元の一覧に戻すため）
  let prevView = null;
  function select(idx, opts) {
    const o = Object.assign({ fit: true }, opts);
    const b = boats[idx];
    if (isMobile() && panel.classList.contains('peek')) setSheet('half');
    // スマホでは地図の範囲内でもシートの下に隠れることがあるので寄せる
    const willMove = map.real && (o.fit === true || (o.fit === 'ifNeeded' && (isMobile() || !map.getBounds().contains([b.lon, b.lat]))));
    if (selected == null) {
      listScroll = $('#scroll').scrollTop;
      prevView = willMove ? { center: map.getCenter(), zoom: map.getZoom(), limit: listLimit } : null;
    }
    selected = idx;
    renderDetail(b);
    $('#browse').hidden = true;
    $('#detail').hidden = false;
    $('#scroll').scrollTop = 0;
    tip.remove();
    updateSelection();
    if (willMove) {
      touched = true;
      // padding は地図に残って以後の fitBounds を狂わせる（スマホでは動かなくなる）ので、offset でシートの分だけ上へずらす
      map.easeTo({ center: [b.lon, b.lat], zoom: Math.max(map.getZoom(), 12), offset: mapOffset(), duration: 700 });
    }
    if (o.focus !== false) {
      const h = $('#detail h2');
      if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }  // 詳細が開いたことをキーボード・読み上げの利用者に伝える
    }
    writeHash();
  }
  function deselect() {
    if (selected == null) return;
    const was = selected;
    selected = null;
    $('#detail').hidden = true;
    $('#browse').hidden = false;
    if (prevView && map.real) {
      skipMoveRender = true;  // jumpTo の moveend で表示件数が戻されないように
      map.jumpTo({ center: prevView.center, zoom: prevView.zoom });
      setTimeout(() => { skipMoveRender = false; }, 0);
      listLimit = prevView.limit;
    }
    prevView = null;
    renderList();
    $('#scroll').scrollTop = listScroll;
    updateSelection();
    writeHash();
    const item = $('#list .item[data-idx="' + was + '"]');
    if (item) item.focus({ preventScroll: true });
    else $('#count').focus({ preventScroll: true });
  }

  function resetFilters() {
    state = fresh();
    $('#q').value = '';
    apply();
  }

  let qTimer = 0;
  let composing = false;
  const runSearch = (value) => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => {
      const v = value.trim();
      if (v === state.q) return;  // 空白を足しただけ・同じ語で確定し直しただけなら何もしない（地図を引き戻さない）
      state.q = v;
      apply({ fit: v ? 'auto' : false, leaveDetail: true });
    }, 180);
  };
  // 日本語入力の変換中は検索しない（未確定の「ちb」などで一覧や地図が飛ばないように）。確定したときに1回だけ検索する
  $('#q').addEventListener('compositionstart', () => { composing = true; clearTimeout(qTimer); });
  $('#q').addEventListener('compositionend', (e) => { composing = false; runSearch(e.target.value); });
  $('#q').addEventListener('input', (e) => {
    if (composing || e.isComposing) return;
    runSearch(e.target.value);
  });
  $('#q').addEventListener('focus', () => { if (isMobile() && !panel.classList.contains('full')) setSheet('full'); });
  document.querySelectorAll('[data-toggle]').forEach((b) => {
    b.addEventListener('click', () => { state[b.dataset.toggle] = !state[b.dataset.toggle]; apply({ leaveDetail: true }); });
  });
  $('#optReg').addEventListener('change', (e) => { state.reg = e.target.checked; apply(); });
  $('#pref').addEventListener('change', (e) => { state.pref = Number(e.target.value); state.region = ''; apply({ fit: !!state.pref }); });
  $('#moreFish').addEventListener('click', () => { fishExpanded = !fishExpanded; renderControls(); });
  $('#reset').addEventListener('click', resetFilters);

  const SHEETS = ['peek', 'half', 'full'];
  // 状態ごとに、実際にできる操作を読み上げる
  const SHEET_LABEL = {
    peek: 'パネル（小）。タップ・上矢印・上へドラッグで広げる',
    half: 'パネル（中）。タップ・上矢印で大きく、下矢印・下へドラッグで小さく',
    full: 'パネル（大）。タップ・下矢印・下へドラッグで中に戻す',
  };
  function setSheet(s) {
    panel.classList.remove('peek', 'half', 'full');
    panel.classList.add(s);
    $('#sheetHandle').setAttribute('aria-label', SHEET_LABEL[s]);
  }
  const sheetIndex = () => Math.max(0, SHEETS.findIndex((s) => panel.classList.contains(s)));
  const grow = () => setSheet(sheetIndex() === 2 ? 'half' : SHEETS[sheetIndex() + 1]);  // いちばん大きいときは中に戻す
  const handle = $('#sheetHandle');
  let dragY = null;
  handle.addEventListener('pointerdown', (e) => { dragY = e.clientY; if (handle.setPointerCapture) handle.setPointerCapture(e.pointerId); });
  handle.addEventListener('pointerup', (e) => {
    if (dragY == null) return;
    const dy = e.clientY - dragY;
    dragY = null;
    if (Math.abs(dy) < 10) { grow(); return; }  // タップ
    // ドラッグは、離した位置に最も近い段へ（小から上端まで引き上げれば大になる。style.css と同じ高さ）
    const vh = window.innerHeight;
    const low = vh <= 480;
    const tops = { peek: vh - (low ? 120 : 176), half: vh - Math.max(vh * 0.46, low ? 150 : 220), full: vh * 0.1 };
    let best = 'half';
    SHEETS.forEach((s) => { if (Math.abs(tops[s] - e.clientY) < Math.abs(tops[best] - e.clientY)) best = s; });
    setSheet(best);
  });
  handle.addEventListener('pointercancel', () => { dragY = null; });
  handle.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); grow(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSheet(SHEETS[Math.min(2, sheetIndex() + 1)]); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setSheet(SHEETS[Math.max(0, sheetIndex() - 1)]); }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && selected != null) deselect();
  });

  // ---------------------------------------------------------------- map
  // 地図ライブラリを読めない・WebGL が使えない端末でも一覧・検索・詳細は使えるよう、何もしない地図の代わりを置く
  function stubMap() {
    const noop = () => {};
    return {
      real: false,
      touchZoomRotate: { disableRotation: noop }, keyboard: { disableRotation: noop },
      addControl: noop, on: noop, once: noop, off: noop, getSource: () => undefined, getStyle: () => ({ layers: [] }),
      getBounds: () => null, getZoom: () => 5, getCenter: () => ({ lng: 137, lat: 37 }), easeTo: noop, jumpTo: noop,
      fitBounds: noop, setStyle: noop, queryRenderedFeatures: () => [], getCanvas: () => document.createElement('canvas'),
      project: () => ({ x: 0, y: 0 }), loaded: () => false, isStyleLoaded: () => false,
    };
  }
  let map;
  try {
    if (!window.maplibregl) throw new Error('maplibre-gl was not loaded');
    map = new maplibregl.Map({
      container: 'map',
      style: BASEMAPS[basemap].url,
      bounds: JAPAN,
      fitBoundsOptions: { padding: 20 },
      attributionControl: { compact: true },
      localIdeographFontFamily: '"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", "Meiryo", sans-serif',
      dragRotate: false,
      pitchWithRotate: false,
      maxPitch: 0,
    });
    map.real = true;
  } catch (err) {
    map = stubMap();
    $('#map').append(el('div', 'map-error', '地図を表示できません（地図の読み込みに失敗したか、この端末では WebGL が使えません）。一覧・検索・詳細はそのまま使えます。'));
  }
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
  window.__jfb = { map, boats, get state() { return state; }, get visible() { return visible; } };

  class ViewControl {
    onAdd() {
      const c = el('div', 'maplibregl-ctrl maplibregl-ctrl-group basemap-ctl');
      Object.entries(BASEMAPS).forEach(([k, v]) => {
        const b = el('button', null, v.label);
        b.type = 'button';
        b.dataset.basemap = k;
        b.title = '地図の見た目: ' + v.label;
        b.setAttribute('aria-pressed', k === basemap ? 'true' : 'false');
        b.addEventListener('click', () => setBasemap(k));
        c.append(b);
      });
      this.c = c;
      return c;
    }
    onRemove() { this.c.remove(); }
  }
  class HomeControl {
    onAdd() {
      const c = el('div', 'maplibregl-ctrl maplibregl-ctrl-group home-ctl');
      const b = el('button', null, '全国');
      b.type = 'button';
      b.title = '日本全体を表示';
      b.addEventListener('click', () => { touched = true; map.fitBounds(JAPAN, { padding: mapPadding(), duration: 600 }); });
      c.append(b);
      this.c = c;
      return c;
    }
    onRemove() { this.c.remove(); }
  }
  if (map.real) {
    map.addControl(new ViewControl(), 'top-right');
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new HomeControl(), 'top-right');
    const geo = new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: false }, fitBoundsOptions: { maxZoom: 11 } });
    geo.on('geolocate', () => { touched = true; });
    map.addControl(geo, 'top-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
  }

  const tip = map.real
    ? new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, maxWidth: '300px', className: 'tip-pop' })
    : { remove() { return this; }, setLngLat() { return this; }, setDOMContent() { return this; }, addTo() { return this; } };

  function mapPadding() {
    if (isMobile()) {
      // 高さのアニメーション中は getBoundingClientRect が古い高さを返すので、クラスから目標の高さを求める（style.css と同じ値）
      const vh = window.innerHeight;
      const low = vh <= 480;  // 横向きのスマホ
      const h = panel.classList.contains('peek') ? (low ? 120 : 176) : panel.classList.contains('full') ? vh * 0.9 : Math.max(vh * 0.46, low ? 150 : 220);
      return { top: 56, bottom: Math.max(0, Math.min(h + 24, vh * 0.7, vh - 56 - 120)), left: 24, right: 56 };  // 地図に最低120pxは残す
    }
    return { top: 48, bottom: 48, left: 48, right: 72 };
  }
  // easeTo 用: padding（地図に残ってしまう）の代わりに、見えている範囲の中心へずらす量
  function mapOffset() {
    const p = mapPadding();
    return [(p.left - p.right) / 2, (p.top - p.bottom) / 2];
  }
  // 端の数隻だけが大きく離れている（関東・東京の小笠原など）ときは、その数隻を外した範囲を返す。外すのは全体の1割まで
  function coreRange(vals) {
    const s = vals.slice().sort((a, b) => a - b), n = s.length, cap = Math.floor(n * 0.1);
    let lo = 0, hi = n - 1;
    for (let i = 0; i < cap; i++) if (s[i + 1] - s[i] >= 3) lo = i + 1;
    for (let i = n - 1; i > n - 1 - cap; i--) if (s[i] - s[i - 1] >= 3) hi = i - 1;
    return [s[lo], s[hi]];
  }
  function fitVisible() {
    if (!visible.length) return;
    const [ya, yb] = coreRange(visible.map((b) => b.lat)), [xa, xb] = coreRange(visible.map((b) => b.lon));
    let x1 = 180, y1 = 90, x2 = -180, y2 = -90;
    visible.forEach((b) => {
      if (b.lat < ya || b.lat > yb || b.lon < xa || b.lon > xb) return;
      if (b.lon < x1) x1 = b.lon; if (b.lon > x2) x2 = b.lon; if (b.lat < y1) y1 = b.lat; if (b.lat > y2) y2 = b.lat;
    });
    map.fitBounds([[x1, y1], [x2, y2]], { padding: mapPadding(), maxZoom: 12, duration: 700 });
  }

  function colors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    return {
      plan: v('--c-plan'), web: v('--c-web'), reg: v('--c-reg'),
      surface: v('--surface'), surface3: v('--surface-3'), ink: v('--ink'), ink2: v('--ink-2'),
      casing: basemap === 'dark' ? '#0e1419' : '#ffffff',
    };
  }

  const FC = (features) => ({ type: 'FeatureCollection', features });
  const feats = boats.map((b) => ({
    type: 'Feature', properties: { i: b.idx, c: b.cat, n: b.name }, geometry: { type: 'Point', coordinates: [b.lon, b.lat] },
  }));
  const pointsFC = () => FC(visible.map((b) => feats[b.idx]));
  const selFC = () => FC(selected == null ? [] : [feats[selected]]);
  const overlayReady = () => !!map.getSource('boats');

  function updateMapData() {
    if (overlayReady()) map.getSource('boats').setData(pointsFC());
  }
  function updateSelection() {
    if (overlayReady()) map.getSource('sel').setData(selFC());
  }
  function setHover(idx) {
    if (hovered === idx) return;
    hovered = idx;
    if (!overlayReady()) return;
    map.getSource('hover').setData(FC(idx == null ? [] : [feats[idx]]));
  }

  function tweakBasemap() {
    (map.getStyle().layers || []).forEach((l) => {
      if (l.type !== 'symbol') return;
      if (/shield|oneway|one_way|poi|highway|road/.test(l.id)) {
        map.setLayoutProperty(l.id, 'visibility', 'none');
        return;
      }
      const tf = l.layout && l.layout['text-field'];
      if (tf && JSON.stringify(tf).indexOf('name') >= 0) {
        map.setLayoutProperty(l.id, 'text-field', ['coalesce', ['get', 'name:ja'], ['get', 'name:nonlatin'], ['get', 'name']]);
      }
    });
    if (map.getLayer('water')) {
      if (basemap === 'light') map.setPaintProperty('water', 'fill-color', '#d4e2ea');
      if (basemap === 'dark') map.setPaintProperty('water', 'fill-color', '#17212b');
    }
  }

  function addOverlay() {
    const C = colors();
    map.addSource('boats', { type: 'geojson', data: pointsFC(), cluster: true, clusterMaxZoom: 11, clusterRadius: 44 });
    map.addSource('hover', { type: 'geojson', data: FC([]) });
    map.addSource('sel', { type: 'geojson', data: selFC() });
    const noCluster = ['!', ['has', 'point_count']];
    map.addLayer({ id: 'clusters', type: 'circle', source: 'boats', filter: ['has', 'point_count'],
      paint: {
        'circle-color': C.surface,
        'circle-opacity': 0.94,
        'circle-radius': ['step', ['get', 'point_count'], 11, 10, 14, 50, 17, 200, 21, 1000, 26],
        'circle-stroke-color': C.ink2,
        'circle-stroke-width': 1.5,
      } });
    map.addLayer({ id: 'cluster-count', type: 'symbol', source: 'boats', filter: ['has', 'point_count'],
      layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': ['Noto Sans Bold'], 'text-size': 11, 'text-allow-overlap': true },
      paint: { 'text-color': C.ink } });
    map.addLayer({ id: 'hover-ring', type: 'circle', source: 'hover',
      paint: { 'circle-radius': 11, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': C.ink, 'circle-stroke-width': 2, 'circle-stroke-opacity': 0.55 } });
    map.addLayer({ id: 'pts', type: 'circle', source: 'boats', filter: noCluster,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 4, 10, 5.5, 14, 7.5],
        'circle-color': ['match', ['get', 'c'], 0, C.plan, 1, C.web, C.surface],
        'circle-stroke-color': ['match', ['get', 'c'], 2, C.reg, C.casing],
        'circle-stroke-width': ['match', ['get', 'c'], 2, 2, 1.3],
      } });
    map.addLayer({ id: 'pts-label', type: 'symbol', source: 'boats', filter: noCluster, minzoom: 12.5,
      layout: { 'text-field': ['get', 'n'], 'text-font': ['Noto Sans Regular'], 'text-size': 11.5,
        'text-anchor': 'top', 'text-offset': [0, 0.75], 'text-optional': true, 'text-padding': 2, 'text-max-width': 9 },
      paint: { 'text-color': C.ink2, 'text-halo-color': C.surface, 'text-halo-width': 1.5 } });
    map.addLayer({ id: 'sel-ring', type: 'circle', source: 'sel',
      paint: { 'circle-radius': 12, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': C.ink, 'circle-stroke-width': 3 } });
    hovered = null;
  }

  map.on('style.load', () => {
    tweakBasemap();
    addOverlay();
  });

  function setBasemap(k) {
    if (k === basemap) return;
    basemap = k;
    store.set('jfb.basemap', k);
    document.documentElement.setAttribute('data-theme', k === 'dark' ? 'dark' : 'light');
    document.querySelectorAll('.basemap-ctl button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.basemap === k ? 'true' : 'false'));
    tip.remove();
    map.setStyle(BASEMAPS[k].url, { diff: false });
  }

  function hit(pt, pad) {
    if (!overlayReady()) return null;
    const box = [[pt.x - pad, pt.y - pad], [pt.x + pad, pt.y + pad]];
    const pts = map.queryRenderedFeatures(box, { layers: ['pts'] });
    if (pts.length) {
      // 重なっているときはカーソルに一番近いもの
      let best = pts[0], bd = Infinity;
      pts.forEach((f) => {
        const p = map.project(f.geometry.coordinates);
        const dd = (p.x - pt.x) ** 2 + (p.y - pt.y) ** 2;
        if (dd < bd) { bd = dd; best = f; }
      });
      return { kind: 'pt', idx: best.properties.i };
    }
    const cl = map.queryRenderedFeatures(box, { layers: ['clusters'] });
    if (cl.length) return { kind: 'cluster', f: cl[0] };
    return null;
  }

  map.on('mousemove', (e) => {
    if (!canHover) return;
    const h = hit(e.point, 6);
    map.getCanvas().style.cursor = h ? 'pointer' : '';
    if (h && h.kind === 'pt') {
      setHover(h.idx);
      const b = boats[h.idx];
      const box = el('div', 'tip');
      const row = el('div', 't-row');
      row.append(keyEl(b.cat), el('span', 't-name', b.name));
      box.append(row);
      const sub = [PREFS[b.pref - 1] + (b.place ? ' ' + b.place : ''), b.price ? '乗合 ' + yen(b.price) + '〜' : ''].filter(Boolean).join(' · ');
      box.append(el('div', 't-sub', sub));
      if (b.tg.length) box.append(el('div', 't-sub', b.tg.slice(0, 4).map((t) => TARGETS[t]).join('・')));
      tip.setLngLat([b.lon, b.lat]).setDOMContent(box).addTo(map);
    } else if (h && h.kind === 'cluster') {
      setHover(null);
      const box = el('div', 'tip');
      box.append(el('div', 't-name', h.f.properties.point_count.toLocaleString('ja-JP') + ' 件'));
      box.append(el('div', 'more', 'クリックで拡大'));
      tip.setLngLat(h.f.geometry.coordinates).setDOMContent(box).addTo(map);
    } else {
      setHover(null);
      tip.remove();
    }
  });
  map.getCanvas().addEventListener('mouseleave', () => { setHover(null); tip.remove(); });

  map.on('click', (e) => {
    tip.remove();
    touched = true;  // クラスタのクリックなどで移動した後に、画面サイズの変化で全国表示へ戻さない
    const h = hit(e.point, canHover ? 6 : 12);
    if (!h) return;
    if (h.kind === 'pt') { select(h.idx, { fit: 'ifNeeded' }); return; }
    const coords = h.f.geometry.coordinates;
    Promise.resolve(map.getSource('boats').getClusterExpansionZoom(h.f.properties.cluster_id))
      .then((z) => map.easeTo({ center: coords, zoom: Math.min(z + 0.3, 16), duration: 500 }))
      .catch(() => map.easeTo({ center: coords, zoom: map.getZoom() + 2, duration: 500 }));
  });

  let moveTimer = 0;
  map.on('moveend', () => {
    clearTimeout(moveTimer);
    if (skipMoveRender) return;
    moveTimer = setTimeout(() => { if (selected == null) { listLimit = PAGE; renderList(); } }, 120);
  });

  // ---------------------------------------------------------------- start
  const initial = readHash();
  state = initial.s;
  $('#subtitle').textContent = '釣り船・船宿・渡船 ' + boats.length.toLocaleString('ja-JP') + ' 件';
  $('#sourcesNote').textContent = '情報源: ' + Object.keys(DATA.sources).map(srcLabel).join('、') + '、各船宿の公式サイト。';
  if (isMobile()) {
    $('#filters').open = false;
    setSheet(SHEETS[sheetIndex()]);  // 起動時からハンドルに今の状態を読み上げさせる
  }
  renderLegend();
  $('#count').tabIndex = -1;  // 「一覧に戻る」で元の項目が無いときのフォーカス先
  apply();
  ['dragstart', 'zoomstart'].forEach((ev) => map.on(ev, (e) => { if (e.originalEvent) touched = true; }));
  const refit = () => {
    if (touched) return;
    if (selected != null) {
      map.easeTo({ center: [boats[selected].lon, boats[selected].lat], zoom: 12, offset: mapOffset(), duration: 0 });
      touched = true;  // 共有リンクの船宿へ寄せた後は、画面サイズが変わっても全国表示へ戻さない
    } else if (state.pref || state.region || (state.q && visible.length && visible.length <= 500)) fitVisible();
    else map.fitBounds(JAPAN, { padding: mapPadding(), duration: 0 });
  };
  map.on('resize', refit);
  if (initial.sel != null) select(initial.sel, { fit: false });
  map.once('load', () => { refit(); renderList(); });
  window.addEventListener('hashchange', () => {
    const h = readHash();
    state = h.s;
    apply();
    if (h.sel != null) select(h.sel, { fit: true });
    else {
      deselect();
      // 共有リンクの貼り付けなどで地域・都道府県・検索語が変わったら、初回表示と同じように地図を合わせる
      if (state.pref || state.region || (state.q && visible.length && visible.length <= 500)) { touched = true; fitVisible(); }
    }
  });
})();
