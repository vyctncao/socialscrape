window.FallbackPage = (() => {
  const triggers = [
    ['unsupported', 'Unsupported', 'Endpoints and options this server can’t serve (501), such as TikTok comments or Instagram pagination.'],
    ['errors', 'Errors', 'Blocks, rate limits, timeouts and network failures (500, 502, 503).'],
    ['restricted', 'Restricted', 'Private, login-gated, age- or geo-restricted content (403).'],
    ['not_found', 'Not found', 'Content this server couldn’t find (404).']
  ];
  const platforms = [['instagram', 'Instagram'], ['tiktok', 'TikTok'], ['youtube', 'YouTube'], ['facebook', 'Facebook'], ['google', 'Google']];
  let baseline = '', dirty = false, removeGuard = null;

  function checkRow(group, value, label, description, checked) {
    const id = `fallback-${group}-${value}`;
    return `<label class="fallback-check" for="${id}"><input id="${id}" name="${group}" type="checkbox" value="${value}" ${checked ? 'checked' : ''} aria-describedby="${id}-help"><span><strong>${label}</strong><small id="${id}-help">${description}</small></span></label>`;
  }
  function status(data) {
    if (!data.enabled) return ['Paused', 'warn'];
    if (!data.api_key.configured) return ['Needs an API key', 'warn'];
    if (!data.triggers.length && !data.platforms.length) return ['Choose conditions and platforms', 'warn'];
    if (!data.triggers.length) return ['Choose a condition', 'warn'];
    if (!data.platforms.length) return ['Choose a platform', 'warn'];
    return ['On', 'good'];
  }
  function ownerPage(data) {
    const [label, badgeClass] = status(data);
    const keyAction = data.api_key.configured ? 'Replace key' : 'Save key';
    const keyNote = data.api_key.source === 'environment'
      ? `The server’s <code>SOCIALSCRAPE_SCRAPECREATORS_API_KEY</code> is in use. A key saved here takes its place.`
      : data.api_key.updated_at ? `Saved ${esc(fmt(data.api_key.updated_at))}. Encrypted and never shown again.` : 'Encrypted and never shown again after you save it.';
    const optionsNote = data.options_source === 'dashboard' && data.options_updated_at ? `Saved ${esc(fmt(data.options_updated_at))}.` : 'Using the server’s environment defaults until you save changes here.';
    return layout('Settings', settingsLede, `${pageTabs('fallback')}
      <div class="settings-stack fallback-settings">
        <section class="card fallback-card"><div class="settings-card-head"><div><h2>ScrapeCreators API key</h2><p class="help">Used only when an eligible request fails here.</p></div><span class="badge ${data.api_key.configured ? 'good' : 'warn'}">${data.api_key.configured ? 'Configured' : 'Not configured'}</span></div>
          <form id="fallback-key-form" class="stack fallback-key-form"><label class="field" for="fallback-key">${data.api_key.configured ? 'Replacement API key' : 'API key'}<input id="fallback-key" name="value" type="password" required maxlength="512" autocomplete="off" spellcheck="false" aria-describedby="fallback-key-help fallback-key-error"><span class="help" id="fallback-key-help">Paste the key without spaces. ${keyNote}</span></label><div class="field-error" id="fallback-key-error" role="alert" hidden></div><div class="fallback-actions"><button class="primary" type="submit">${keyAction}</button>${data.api_key.source === 'dashboard' ? '<button class="danger" type="button" data-remove-fallback-key>Remove key</button>' : ''}</div></form>
        </section>
        <section class="card fallback-card"><div class="settings-card-head"><div><h2>When to fall back</h2><p class="help">Requests stay here unless a selected condition occurs on a selected platform.</p></div><span class="badge ${badgeClass}">${label}</span></div>
          <form id="fallback-options-form" class="fallback-form">
            <label class="fallback-switch" for="fallback-enabled"><span><strong>Use ScrapeCreators when a request fails</strong><small>Turn this off to pause the fallback without losing the choices below.</small></span><input id="fallback-enabled" name="enabled" type="checkbox" value="enabled" role="switch" ${data.enabled ? 'checked' : ''}><i aria-hidden="true"></i></label>
            <div class="fallback-dependent">
              <fieldset class="fallback-fieldset" aria-describedby="fallback-triggers-error"><legend>Fall back when</legend><div class="fallback-checks">${triggers.map(([value, name, copy]) => checkRow('triggers', value, name, copy, data.triggers.includes(value))).join('')}</div><div class="field-error" id="fallback-triggers-error" role="alert" hidden></div></fieldset>
              <fieldset class="fallback-fieldset" aria-describedby="fallback-platforms-error"><legend>Platforms</legend><div class="fallback-platforms">${platforms.map(([value, name]) => checkRow('platforms', value, name, `Allow fallback for ${name} requests.`, data.platforms.includes(value))).join('')}</div><div class="field-error" id="fallback-platforms-error" role="alert" hidden></div></fieldset>
            </div>
            <p class="help">Fallback starts only when an API key, condition, and platform are configured. ${optionsNote}</p><div class="field-error" id="fallback-options-error" role="alert" hidden></div><div class="fallback-save-row"><button class="primary fallback-save" type="submit">Save options</button><span class="fallback-dirty" role="status" hidden>Unsaved changes</span></div>
          </form>
        </section>
        <div class="inline-note fallback-note">${icon('arrows-clockwise')}<span>Fallback requests spend ScrapeCreators credits; provider-reported errors cost none, while timeout and network outcomes may still have been charged. Successful answers are cached like any other response, so <code>cache_max_age</code> avoids paying twice. Responses served by ScrapeCreators carry <code>x-socialscrape-fallback: scrapecreators</code> and are marked in Request history.</span></div>
      </div>`);
  }
  async function renderPage() {
    if (PORTAL.role !== 'owner') return ownerOnly('Settings');
    return ownerPage(await api('/portal/api/settings/fallback'));
  }
  function values(form = $('#fallback-options-form')) {
    if (!form) return '';
    const checked = name => [...form.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value).sort();
    return JSON.stringify({enabled:form.elements.enabled.checked, triggers:checked('triggers'), platforms:checked('platforms')});
  }
  function setDirty(next) {
    dirty = next; const status=$('.fallback-dirty'); if (status) status.hidden=!dirty;
    if (dirty) window.addEventListener('beforeunload', beforeUnload); else window.removeEventListener('beforeunload', beforeUnload);
  }
  function beforeUnload(event) { event.preventDefault(); event.returnValue=''; }
  function confirmDiscard() { if (!dirty) return true; const discard=window.confirm('Discard your unsaved fallback changes?'); if (discard) setDirty(false); return discard; }
  function mount() {
    removeGuard?.(); removeGuard=null; setDirty(false);
    const form=$('#fallback-options-form'); if (!form) { baseline=''; return; }
    baseline=values(form); removeGuard=addLeaveGuard(confirmDiscard);
  }
  async function submit(form, url, body, success) {
    const button = form.querySelector('button[type="submit"]'), options=form.id === 'fallback-options-form', error=$(options ? '#fallback-options-error' : '#fallback-key-error'), input=form.querySelector('input');
    const done = pendingButton(button, 'Saving…');
    if (!done) return;
    error.hidden = true; input?.removeAttribute('aria-invalid');
    try { await api(url, {method:'PUT', body}); if (options) setDirty(false); await render(); toast(success); }
    catch (err) {
      const group=options && ['triggers','platforms'].includes(err.data?.field) ? err.data.field : null, target=group ? $(`#fallback-${group}-error`) : error;
      target.textContent=err.message; target.hidden=false;
      if (group) { const first=form.querySelector(`input[name="${group}"]`); first?.setAttribute('aria-invalid','true'); first?.focus(); }
      else if (!options && [400,422].includes(err.status)) { input.setAttribute('aria-invalid','true'); input.focus(); }
    } finally { done(); }
  }

  document.addEventListener('submit', event => {
    if (event.target.id === 'fallback-key-form') {
      event.preventDefault(); const input=event.target.elements.value;
      if (!input.value.trim()) { fieldError(input,'Enter your ScrapeCreators API key.'); return; }
      submit(event.target, '/portal/api/settings/fallback/api-key', {value:input.value.trim()}, 'ScrapeCreators API key saved.');
    }
    if (event.target.id === 'fallback-options-form') {
      event.preventDefault(); const checked = name => [...event.target.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value);
      submit(event.target, '/portal/api/settings/fallback', {enabled:event.target.elements.enabled.checked, triggers:checked('triggers'), platforms:checked('platforms')}, 'Fallback options saved.');
    }
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-remove-fallback-key]')) return;
    confirmAction('Remove the ScrapeCreators API key?', 'Requests will stop falling back unless an environment key is configured.', 'Remove fallback key', () => api('/portal/api/settings/fallback/api-key', {method:'DELETE'}));
  });
  document.addEventListener('input', event => {
    if (event.target.id === 'fallback-key') { event.target.removeAttribute('aria-invalid'); $('#fallback-key-error').hidden=true; }
  });
  document.addEventListener('change', event => {
    const form=event.target.closest('#fallback-options-form'); if (!form) return;
    setDirty(values(form) !== baseline);
    const group=event.target.name; if (['triggers','platforms'].includes(group)) { const error=$(`#fallback-${group}-error`); if (error) error.hidden=true; form.querySelectorAll(`input[name="${group}"]`).forEach(input => input.removeAttribute('aria-invalid')); }
    $('#fallback-options-error').hidden=true;
  });

  return {render:renderPage, mount, hasUnsavedChanges:() => dirty, confirmDiscard};
})();
