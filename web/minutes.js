// Minutes tab (Windows build): formal meeting minutes written by minutes.py.
// Loaded after app.js and uses its helpers ($, api, esc, st, sessUrl, ICON, toast, fail, relTime,
// fmtDur); app.js only calls Minutes.load / render / onMessage, so upstream merges stay easy.
window.Minutes = (() => {
  const S = { versions: [], job: null, sel: null, forKey: '' };
  const k = () => (st.project && st.sid) ? `${st.project}/${st.sid}` : '';
  const done = () => S.versions.filter(v => v.status === 'done');
  let timer = null;

  async function load() {
    const key = k();
    if (!key) { S.versions = []; S.job = null; S.forKey = ''; return render(); }
    if (key !== S.forKey) { S.sel = null; S.forKey = key; }
    try {
      const d = await api('GET', sessUrl() + '/minutes');
      if (k() !== key) return;
      S.versions = d.versions || [];
      S.job = d.job;
    } catch (_) { S.versions = []; S.job = null; }
    render();
  }

  async function run() {
    try {
      S.job = await api('POST', sessUrl() + '/minutes');
      render();
    } catch (e) { fail(e); }
  }

  function badge() {
    const b = $('#minutesBadge');
    if (!b) return;
    const d = done();
    b.textContent = S.job && S.job.status === 'running' ? '…' : (d.length ? `m${d[d.length - 1].v}` : '');
  }

  function render() {
    badge();
    if (st.tab !== 'minutes') return;
    const tb = $('#minToolbar'), wrap = $('#minFrameWrap'), frame = $('#minFrame'),
      empty = $('#minEmpty'), card = $('#minJobCard');
    const running = S.job && S.job.status === 'running';
    const canRun = !!st.claude && !!st.session;
    const runBtn = `<button class="beautify-btn" id="minRun" style="border-radius:10px" ${canRun && !running ? '' : 'disabled'}
      title="${st.claude ? 'Write the formal minutes of this meeting' : 'Claude Code CLI not found'}">${ICON.sparkle}${done().length ? 'New minutes' : 'Write minutes'}</button>`;

    // progress card
    if (running) {
      card.classList.remove('hidden');
      const steps = S.job.steps.slice(-8);
      card.innerHTML = `<div class="jc-head"><div style="flex:1"><div class="jc-title">Writing minutes m${S.job.v}</div>
          <div class="jc-sub">from the ${esc(S.job.source)} · ${S.job.lines || 0} transcript lines</div></div>
          <span class="mono" id="minElapsed" style="font-size:20px">${fmtDur(Date.now() / 1000 - S.job.started)}</span>
          <button class="cancel-x" id="minCancel" title="Cancel">${ICON.x}</button></div>
        <div class="bar"><i></i></div>
        <ul class="steps">${steps.map((s, i) => {
          const cur = i === steps.length - 1;
          return `<li class="${cur ? 'cur' : ''}">${cur ? '<span class="spinner"></span>' : '<svg viewBox="0 0 12 12" class="ok"><path d="M2.5 6.2l2.2 2.2 4.8-4.8"/></svg>'}<span class="st mono">${fmtDur(s.t)}</span><span class="sx">${esc(s.text)}</span></li>`;
        }).join('')}</ul>`;
      $('#minCancel').onclick = () => api('DELETE', sessUrl() + '/minutes').catch(fail);
      if (!timer) timer = setInterval(() => {
        const el = $('#minElapsed');
        if (el && S.job && S.job.status === 'running') el.textContent = fmtDur(Date.now() / 1000 - S.job.started);
        else { clearInterval(timer); timer = null; }
      }, 1000);
    } else {
      card.classList.add('hidden');
    }

    const vs = done();
    if (!vs.length) {
      tb.innerHTML = '';
      wrap.classList.add('hidden');
      frame.removeAttribute('src');
      const last = S.versions[S.versions.length - 1];
      const failed = last && last.status === 'failed' ? `<p style="color:var(--rec)">⚠ m${last.v} failed: ${esc((last.error || '').slice(0, 200))}</p>` : '';
      empty.classList.toggle('hidden', running);
      empty.innerHTML = `<div class="art">${ICON.doc}</div><h3>No minutes yet</h3>
        <p>A formal minute in the language of the meeting: attendees, topics, decisions, action items with owner and due date, risks, open questions and next steps. It uses the beautified version when there is one, otherwise the transcript.</p>
        ${failed}${st.session ? `<div style="margin-top:10px">${runBtn}</div>` : ''}`;
      const b = $('#minRun'); if (b) b.onclick = run;
      return;
    }

    empty.classList.add('hidden');
    wrap.classList.remove('hidden');
    const sel = vs.find(v => v.v === S.sel) || vs[vs.length - 1];
    S.sel = sel.v;
    const stat = (n, one, many) => n != null ? `<span><b>${n}</b> ${n === 1 ? one : many}</span>` : '';
    tb.innerHTML = `
      <select class="field" id="minSel">${[...vs].reverse().map(v => `<option value="${v.v}" ${v.v === sel.v ? 'selected' : ''}>m${v.v} · ${esc(relTime(v.finished || v.created))}</option>`).join('')}</select>
      <div class="doc-meta">
        ${sel.lang ? `<span>${esc(sel.lang.toUpperCase())}</span>` : ''}
        ${stat(sel.decisions, 'decision', 'decisions')}${stat(sel.actions, 'action item', 'action items')}${stat(sel.risks, 'risk', 'risks')}
        <span>from ${esc(sel.source || '')}</span>
        ${sel.duration ? `<span>${fmtDur(sel.duration)} to write</span>` : ''}
      </div>
      <div class="spacer"></div>
      ${runBtn.replace('style="border-radius:10px"', 'style="height:30px;border-radius:8px;font-size:12.5px"')}
      ${sel.docs_url ? `<a class="ghost-btn small" href="${esc(sel.docs_url)}" target="_blank" title="Copy saved in the project's docs/ folder">${ICON.doc}docs/ copy</a>` : ''}
      <a class="ghost-btn small" href="${esc(sel.url)}" target="_blank">${ICON.open}Open</a>`;
    $('#minSel').onchange = e => { S.sel = +e.target.value; render(); };
    const b = $('#minRun'); if (b) b.onclick = run;
    const src = `${sel.url}?v=${encodeURIComponent(sel.finished || sel.v)}`;
    if (frame.getAttribute('src') !== src) frame.setAttribute('src', src);
  }

  function onMessage(m) {
    if (`${m.project}/${m.sid}` !== k()) {
      if (m.status === 'done' && m.steps && m.steps.length && m.steps[m.steps.length - 1].text === 'Finished')
        toast(`Minutes ready · ${m.project} · ${sidLabel(m.sid)}`, 'ok');
      return;
    }
    const was = S.job && S.job.status;
    S.job = m;
    if (was === 'running' && m.status !== 'running') {
      if (m.status === 'done') { toast(`Minutes ready · m${m.v}`, 'ok', 6000); S.sel = m.v; }
      else if (m.status === 'failed') toast(`Minutes failed: ${m.error || ''}`, 'error', 8000);
      load();
      return;
    }
    render();
  }

  return { load, render, onMessage };
})();
