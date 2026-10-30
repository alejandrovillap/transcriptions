// Windows build: UI improvements layered on the author's app.js without editing it.
// They re-apply after every re-render (MutationObserver) and do nothing if the markup they
// expect isn't there, so an upstream change can at worst switch one of them off.
//   1. One-click recording: only Start, Language and Call audio stay visible; the rest goes
//      into a collapsible "Options" row with a one-line summary (open state remembered).
//   2. Settings dialog in tabs: Audio · Recording · AI · Project.
//   3. ES / EN switch next to the theme button (i18n.js).
//   4. Download menu.  5. Session actions: rename, move to another project.
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

  // ── 4. Download menu: transcript (.txt), annotated (.html), minutes (.html) ──
  const DL_ICON = '<svg viewBox="0 0 16 16" class="ico" style="width:14px;height:14px"><path d="M8 2.5v7.5M4.8 7l3.2 3.2L11.2 7M3 12.5h10"/></svg>';

  function addDownloadButton() {
    const head = $('.main-panel .panel-head');
    if (!head || $('#dlBtn')) return;
    const b = document.createElement('button');
    b.id = 'dlBtn';
    b.type = 'button';
    b.className = 'ghost-btn small win-dl';
    b.title = T('Download the transcript, the annotated version or the minutes');
    b.innerHTML = `${DL_ICON}<span>${T('Download')}</span>`;
    head.appendChild(b);
    const menu = document.createElement('div');
    menu.className = 'menu hidden';
    menu.id = 'dlMenu';
    document.body.appendChild(menu);
    b.onclick = () => openDownloads(b, menu);
  }

  async function openDownloads(anchor, menu) {
    if (typeof st === 'undefined' || !st.project || !st.sid) return;
    const base = `/api/projects/${encodeURIComponent(st.project)}/sessions/${encodeURIComponent(st.sid)}`;
    let a = {};
    try { a = await (await fetch(base + '/downloads')).json(); } catch (_) { /* offline */ }
    const item = (ok, kind, title, sub) => ok
      ? `<a class="mi" href="${base}/download/${kind}" download><span class="mi-main"><div class="mi-title">${title}</div><div class="mi-sub">${sub}</div></span></a>`
      : `<div class="mi" style="opacity:.5;cursor:default"><span class="mi-main"><div class="mi-title">${title}</div><div class="mi-sub">${T('Not generated yet')}</div></span></div>`;
    menu.innerHTML =
      item(a.transcript, 'transcript', T('Transcript'), T('Plain text (.txt), every line with its time')) +
      item(a.beautified, 'beautified', `${T('Annotated version')}${a.beautified ? ` v${a.beautified}` : ''}`, T('HTML with the screenshots inside; opens in any browser')) +
      item(a.minutes, 'minutes', `${T('Minutes')}${a.minutes ? ` m${a.minutes}` : ''}`, T('HTML; open it and print or save as PDF / Word'));
    menu.onclick = e => { if (e.target.closest('a.mi')) setTimeout(() => window.closeMenus && closeMenus(), 50); };
    if (window.openMenu) openMenu(menu, anchor, 'right');
  }

  function syncDownloadButton() {
    const b = $('#dlBtn');
    if (b) b.hidden = typeof st === 'undefined' || !st.sid || !st.session;
  }

  // ── 5. Session actions: rename, move to another project (session_admin_api.py) ──
  function addSessionActions() {
    const crumb = $('#sessionBtn');
    if (!crumb || $('#sessActBtn')) return;
    const b = document.createElement('button');
    b.id = 'sessActBtn';
    b.type = 'button';
    b.className = 'icon-btn win-sess-act';
    b.title = T('Rename the session or move it to another project');
    b.textContent = '⋯';
    crumb.after(b);
    const menu = document.createElement('div');
    menu.className = 'menu hidden';
    menu.id = 'sessActMenu';
    document.body.appendChild(menu);
    b.onclick = () => {
      if (typeof st === 'undefined' || !st.sid) return;
      menu.innerHTML =
        `<button class="mi" data-act="rename"><span class="mi-main"><div class="mi-title">${T('Rename…')}</div><div class="mi-sub">${T('Give the call a name you will recognize')}</div></span></button>` +
        `<button class="mi" data-act="move"><span class="mi-main"><div class="mi-title">${T('Move to another project…')}</div><div class="mi-sub">${T('Takes the transcript, versions, minutes and screenshots along')}</div></span></button>`;
      openMenu(menu, b);
    };
    menu.onclick = e => {
      const a = e.target.closest('[data-act]'); if (!a) return;
      closeMenus();
      (a.dataset.act === 'rename' ? renameDialog : moveDialog)();
    };
  }

  function renameDialog() {
    const cur = (st.session && st.session.title) || '';
    openModal(`
      <div class="modal-head"><h2>${T('Rename session')}</h2><p>${esc(sidLabel(st.sid))}</p></div>
      <div class="modal-body"><div class="frow"><label for="rnTitle">${T('Name')}</label><input class="field" id="rnTitle" maxlength="120" value="${esc(cur)}" placeholder="${T('e.g. Weekly sync with the client')}"></div></div>
      <div class="modal-foot"><button class="ghost-btn" data-close>${T('Cancel')}</button><button class="primary-btn" id="rnGo">${T('Save')}</button></div>`,
    root => {
      const inp = $('#rnTitle', root); inp.focus(); inp.select();
      const go = async () => {
        try {
          const r = await api('POST', sessUrl() + '/rename', { title: inp.value });
          closeModal();
          if (st.session) st.session.title = r.title;
          await loadSessions();
          $('#sessionName').textContent = r.title || sidLabel(st.sid);
          toast(T('Session renamed'), 'ok');
        } catch (e) { fail(e); }
      };
      $('#rnGo', root).onclick = go;
      inp.onkeydown = e => { if (e.key === 'Enter') go(); };
      $('[data-close]', root).onclick = closeModal;
    });
  }

  function moveDialog() {
    const others = (st.projects || []).filter(p => p.name !== st.project);
    const label = (st.session && st.session.title) || sidLabel(st.sid);
    openModal(`
      <div class="modal-head"><h2>${T('Move to another project')}</h2><p><b>${esc(label)}</b> · ${esc(st.project)} →</p></div>
      <div class="modal-body">
        <div class="frow"><label for="mvSel">${T('Project')}</label><select class="field" id="mvSel">
          ${others.map(p => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join('')}
          <option value="__new">${T('New project…')}</option></select></div>
        <div class="frow" id="mvNewRow" hidden><label for="mvNew">${T('Name')}</label><input class="field" id="mvNew" placeholder="${T('e.g. acme-discovery')}"></div>
      </div>
      <div class="modal-foot"><button class="ghost-btn" data-close>${T('Cancel')}</button><button class="primary-btn" id="mvGo">${T('Move')}</button></div>`,
    root => {
      const sel = $('#mvSel', root), row = $('#mvNewRow', root), inp = $('#mvNew', root);
      const sync = () => { row.hidden = sel.value !== '__new'; if (!row.hidden) inp.focus(); };
      sel.onchange = sync; sync();
      $('#mvGo', root).onclick = async () => {
        const target = sel.value === '__new' ? inp.value.trim().replace(/\s+/g, '-') : sel.value;
        if (!target) return;
        const btn = $('#mvGo', root); btn.disabled = true;
        try {
          const r = await api('POST', sessUrl() + '/move', { project: target });
          closeModal();
          await loadProjects();
          await selectProject(r.project, { keepSession: true });
          await selectSession(r.sid);
          toast(`${T('Moved to')} “${r.project}”`, 'ok');
        } catch (e) { btn.disabled = false; fail(e); }
      };
      $('[data-close]', root).onclick = closeModal;
    });
  }

  function syncSessionActions() {
    const b = $('#sessActBtn');
    if (b) b.hidden = typeof st === 'undefined' || !st.sid || !st.session;
  }

  function run() {
    addDownloadButton();
    syncDownloadButton();
    addSessionActions();
    syncSessionActions();
    enhanceControl();
    const modal = $('.modal');
    if (modal) enhanceSettings(modal);
  }

  addLangSwitch();
  if (window.I18N) I18N.start();
  run();
  new MutationObserver(run).observe(document.body, { childList: true, subtree: true });
})();
