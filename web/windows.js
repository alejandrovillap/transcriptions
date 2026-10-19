// Windows build: UI improvements layered on the author's app.js without editing it.
// They re-apply after every re-render (MutationObserver) and do nothing if the markup they
// expect isn't there, so an upstream change can at worst switch one of them off.
//   1. One-click recording: only Start, Language and Call audio stay visible; the rest goes
//      into a collapsible "Options" row with a one-line summary (open state remembered).
//   2. Settings dialog in tabs: Audio · Recording · AI · Project.
//   3. ES / EN switch next to the theme button (i18n.js).
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const store = {
    get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (_) { return d; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* */ } },
  };
  const T = s => (window.I18N ? I18N.tr(s) : s);

  // ── 1. Control strip: options collapsed ────────────────────────────────────
  function optionsSummary(box) {
    const parts = [];
    const eng = $('#engSel', box);
    if (eng) parts.push(eng.options[eng.selectedIndex]?.text || '');
    const spk = $('#spkSel', box);
    if (spk) parts.push(`${T('Speakers')}: ${spk.options[spk.selectedIndex]?.text || ''}`.toLowerCase().replace(/^./, c => c.toUpperCase()));
    const min = $('#minInp', box);
    if (min) parts.push(`${min.value} min`);
    if ($('#audioChk', box)?.checked) parts.push(T('Save audio'));
    if ($('#autoChk', box)?.checked) parts.push(T('Auto frames'));
    return parts.filter(Boolean).join(' · ');
  }

  function enhanceControl() {
    const ctl = $('#control');
    const group = ctl && $('.opt-group', ctl);
    if (!group || group.dataset.win || !$('#recBtn.rec-btn', ctl)) return;
    group.dataset.win = '1';
    const keep = [$('#langSeg', group)?.closest('.opt'), $('#sysChk', group)?.closest('.opt')].filter(Boolean);
    const rest = [...group.children].filter(el => !keep.includes(el));
    if (!rest.length) return;
    const det = document.createElement('details');
    det.className = 'win-more';
    det.open = store.get('vr-opts-open', '0') === '1';
    det.innerHTML = `<summary><span class="win-more-t">${T('Options')}</span><span class="win-more-s"></span></summary><div class="win-more-body"></div>`;
    rest.forEach(el => $('.win-more-body', det).appendChild(el));
    group.appendChild(det);
    const upd = () => { $('.win-more-s', det).textContent = optionsSummary(det); };
    upd();
    det.addEventListener('change', upd);
    det.addEventListener('toggle', () => store.set('vr-opts-open', det.open ? '1' : '0'));
  }

  // ── 2. Settings dialog in tabs ─────────────────────────────────────────────
  function enhanceSettings(modal) {
    const body = $('.modal-body', modal);
    if (!body || body.dataset.win || !$('#setEngine', body)) return;
    body.dataset.win = '1';
    const row = id => $(`#${id}`, body)?.closest('.frow');
    const fsOf = re => $$('fieldset', body).find(f => re.test($('legend', f)?.textContent || ''));
    const tabs = [
      { k: 'audio', name: 'Audio', rows: [row('setMic'), row('setOut')] },
      { k: 'rec', name: 'Recording', rows: [row('setPy'), row('setL1')] },
      { k: 'ai', name: 'AI', rows: fsOf(/^(AI|IA)$/) ? $$('.frow', fsOf(/^(AI|IA)$/)) : [] },
      { k: 'proj', name: 'Project', rows: fsOf(/^(Project|Proyecto)/) ? $$('.frow', fsOf(/^(Project|Proyecto)/)) : [],
        label: $('legend', fsOf(/^(Project|Proyecto)/) || document.createElement('i'))?.textContent },
    ].filter(t => t.rows.filter(Boolean).length);
    if (tabs.length < 2) return;
    const bar = document.createElement('div');
    bar.className = 'win-tabs';
    const panes = document.createElement('div');
    for (const t of tabs) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'win-tab';
      b.dataset.k = t.k;
      b.textContent = T(t.name);
      bar.appendChild(b);
      const pane = document.createElement('div');
      pane.className = 'win-pane';
      pane.dataset.k = t.k;
      if (t.label) pane.insertAdjacentHTML('beforeend', `<div class="win-pane-label"></div>`), pane.firstChild.textContent = t.label;
      t.rows.filter(Boolean).forEach(r => pane.appendChild(r));
      panes.appendChild(pane);
    }
    $$('fieldset', body).forEach(f => { if (!$('.frow', f)) f.remove(); });
    body.prepend(panes);
    body.prepend(bar);
    const show = k => {
      $$('.win-tab', bar).forEach(b => b.classList.toggle('on', b.dataset.k === k));
      $$('.win-pane', panes).forEach(p => p.hidden = p.dataset.k !== k);
      store.set('vr-settings-tab', k);
    };
    bar.addEventListener('click', e => { const b = e.target.closest('.win-tab'); if (b) show(b.dataset.k); });
    const last = store.get('vr-settings-tab', 'audio');
    show(tabs.some(t => t.k === last) ? last : tabs[0].k);
  }

  // ── 3. ES / EN switch ──────────────────────────────────────────────────────
  function addLangSwitch() {
    const theme = $('#themeBtn');
    if (!theme || $('#langBtn') || !window.I18N) return;
    const b = document.createElement('button');
    b.id = 'langBtn';
    b.className = 'icon-btn win-lang';
    b.textContent = I18N.lang === 'es' ? 'ES' : 'EN';
    b.title = I18N.lang === 'es' ? 'Switch the interface to English' : 'Cambiar la interfaz a español';
    b.onclick = () => I18N.setLang(I18N.lang === 'es' ? 'en' : 'es');
    theme.before(b);
  }

  function run() {
    enhanceControl();
    const modal = $('.modal');
    if (modal) enhanceSettings(modal);
  }

  addLangSwitch();
  if (window.I18N) I18N.start();
  run();
  new MutationObserver(run).observe(document.body, { childList: true, subtree: true });
})();
