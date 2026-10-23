window.FallbackPage = (() => {
  const triggers = [
    ['unsupported', 'Unsupported', 'Endpoints and options this server can’t serve (501), such as TikTok comments or Instagram pagination.'],
    ['errors', 'Errors', 'Blocks, rate limits, timeouts and network failures (500, 502, 503).'],
    ['restricted', 'Restricted', 'Private, login-gated, age- or geo-restricted content (403).'],
    ['not_found', 'Not found', 'Content this server couldn’t find (404).']
  ];
  const platforms = [['instagram', 'Instagram'], ['tiktok', 'TikTok'], ['youtube', 'YouTube']];

  function checkRow(group, value, label, description, checked) {
    const id = `fallback-${group}-${value}`;
    return `<label class="fallback-check" for="${id}"><input id="${id}" name="${group}" type="checkbox" value="${value}" ${checked ? 'checked' : ''} aria-describedby="${id}-help"><span><strong>${label}</strong><small id="${id}-help">${description}</small></span></label>`;
  }
  function status(data) {
    if (data.active) return ['On', 'good'];
    if (!data.enabled) return ['Paused', 'warn'];
    if (!data.api_key.configured) return ['Needs an API key', 'warn'];
    return ['Off', ''];
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
            ${checkRow('enabled', 'enabled', 'Use ScrapeCreators when a request fails', 'Turn this off to pause the fallback without losing the choices below.', data.enabled)}
            <fieldset class="fallback-fieldset"><legend>Fall back when</legend><div class="fallback-checks">${triggers.map(([value, name, copy]) => checkRow('triggers', value, name, copy, data.triggers.includes(value))).join('')}</div></fieldset>
            <fieldset class="fallback-fieldset"><legend>Platforms</legend><div class="fallback-platforms">${platforms.map(([value, name]) => checkRow('platforms', value, name, `Allow fallback for ${name} requests.`, data.platforms.includes(value))).join('')}</div></fieldset>
            <p class="help">${optionsNote}</p><div class="field-error" id="fallback-options-error" role="alert" hidden></div><button class="primary fallback-save" type="submit">Save options</button>
          </form>
        </section>
        <div class="inline-note fallback-note">${icon('arrows-clockwise')}<span>Fallback requests spend ScrapeCreators credits; provider-reported errors cost none, while timeout and network outcomes may still have been charged. Successful answers are cached like any other response, so <code>cache_max_age</code> avoids paying twice. Responses served by ScrapeCreators carry <code>x-socialscrape-fallback: scrapecreators</code> and are marked in Request history.</span></div>
      </div>`);
  }
  async function renderPage() {
    if (PORTAL.role !== 'owner') return ownerOnly('Settings');
    return ownerPage(await api('/portal/api/settings/fallback'));
  }
  async function submit(form, url, body, success) {
    const button = form.querySelector('button[type="submit"]'), error = form.querySelector('.field-error');
    const done = pendingButton(button, 'Saving…');
    if (!done) return;
    error.hidden = true;
    try { await api(url, {method:'PUT', body}); await render(); toast(success); }
    catch (err) { error.textContent = err.message; error.hidden = false; }
    finally { done(); }
  }

  document.addEventListener('submit', event => {
    if (event.target.id === 'fallback-key-form') {
      event.preventDefault();
      submit(event.target, '/portal/api/settings/fallback/api-key', {value:event.target.elements.value.value.trim()}, 'ScrapeCreators API key saved.');
    }
    if (event.target.id === 'fallback-options-form') {
      event.preventDefault();
      const checked = name => [...event.target.querySelectorAll(`input[name="${name}"]:checked`)].map(input => input.value);
      submit(event.target, '/portal/api/settings/fallback', {enabled:event.target.elements.enabled.checked, triggers:checked('triggers'), platforms:checked('platforms')}, 'Fallback options saved.');
    }
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-remove-fallback-key]')) return;
    confirmAction('Remove the ScrapeCreators API key?', 'Requests will stop falling back unless an environment key is configured.', 'Remove fallback key', () => api('/portal/api/settings/fallback/api-key', {method:'DELETE'}));
  });
  document.addEventListener('change', event => {
    if (!event.target.closest('#fallback-options-form')) return;
    const error = $('#fallback-options-error');
    if (error) error.hidden = true;
  });

  return {render:renderPage};
})();
