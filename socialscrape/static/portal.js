const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const asset = path => `/assets/${path}?v=${encodeURIComponent(document.documentElement.dataset.assetVersion || '')}`;
const icon = name => `<svg class="icon" aria-hidden="true"><use href="${asset('brand/icons.svg')}#${name}"></use></svg>`;
const platforms = ['instagram', 'tiktok', 'youtube'];
const platformIcon = platform => platforms.includes(platform) ? `<img class="platform-icon" data-platform="${platform}" src="${asset(`brand/platforms/${platform}.svg`)}" alt="" width="20" height="20">` : '';
const platformLabel = platform => `<span class="platform-label">${platformIcon(platform)}<span>${esc(platformName(platform))}</span></span>`;
const platformFromPath = path => String(path).split('/')[2];
const platformList = () => platforms.map(platform => `<span class="badge platform-badge">${platformLabel(platform)}</span>`).join('');
const fmt = time => time ? new Date(time * 1000).toLocaleString() : 'Never';
const number = value => Number(value).toLocaleString();
const mask = key => key ? `${key.prefix}••••••••••••` : 'No API key yet';
const state = {keys:[], spec:null, language:'curl', drafts:{}, platform:null, endpoint:null, selectedKey:'', docSearch:'', renderId:0, renderedPage:null, overviewParts:{}};
const {pendingButton, reveal, cancelReveals} = window.PortalLoading;
const reads = {};
const cacheKey = name => `socialscrape:${PORTAL.email}:${name}`;
function cacheRead(name, maxAge = 5 * 60_000) {
  try { const item=JSON.parse(sessionStorage.getItem(cacheKey(name))); return item && Date.now()-item.at<maxAge ? item.value : null; } catch { return null; }
}
function cacheWrite(name, value) { try { sessionStorage.setItem(cacheKey(name), JSON.stringify({at:Date.now(), value})); } catch {} }

async function api(url, options = {}) {
  const headers = {...options.headers, 'x-csrf-token':PORTAL.csrf};
  if (options.body && typeof options.body !== 'string') {
    headers['content-type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const read = !options.method || options.method === 'GET';
  const controller = read && !options.signal ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), 30000) : null;
  let response;
  try { response = await fetch(url, {...options, headers, ...(controller ? {signal:controller.signal} : {})}); }
  catch (error) { throw Error(controller?.signal.aborted ? 'This page took too long to load. Check your connection, then try again.' : 'Could not reach the dashboard. Check your connection, then try again.'); }
  finally { clearTimeout(timeout); }
  if (response.status === 401) { location.href = '/login'; throw Error('Sign in to continue.'); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw Error(typeof data.detail === 'string' ? data.detail : data.message || `Request failed (${response.status}). Try again.`);
  }
  return response.json();
}

function toast(message) {
  const element = document.createElement('div');
  element.className = 'toast';
  element.innerHTML = icon('check');
  element.append(document.createTextNode(message));
  $('#toasts').append(element);
  while ($('#toasts').children.length > 3) $('#toasts').firstChild.remove();
  setTimeout(() => element.remove(), 4000);
}
function layout(title, lede, body, badge = '') {
  return `<div class="page-head"><div><h1>${title}</h1><p class="lede">${lede}</p></div>${badge ? `<span class="badge">${badge}</span>` : ''}</div>${body}`;
}
function emptyState(glyph, title, copy, action = '') {
  return `<div class="empty">${icon(glyph)}<h3>${title}</h3><p>${copy}</p>${action}</div>`;
}
function tableRegion(label, content) {
  return `<div class="table-wrap" role="region" aria-label="${label}" tabindex="0">${content}</div>`;
}
function showError(error, selector = '#action-error') {
  const element = $(selector);
  if (!element) return;
  element.hidden = false;
  element.textContent = error.message || String(error);
}
function headerValue(id, value) {
  const element = document.getElementById(id);
  element.textContent = value; element.dataset.loaded = 'true';
  element.setAttribute('aria-busy', 'false'); element.removeAttribute('title');
}
function headerFailure(id, label, error) {
  const element = document.getElementById(id);
  if (!element.dataset.loaded) element.textContent = `${label} unavailable`;
  element.setAttribute('aria-busy', 'false'); element.title = error.message;
}
function loadKeys({cached = false} = {}) {
  const apply = data => {
    state.keys = data.keys;
    const active = data.keys.filter(key => !key.revoked_at);
    headerValue('top-key', `${number(active.length)} active ${active.length === 1 ? 'key' : 'keys'}`);
    return data.keys;
  };
  const saved = cached && cacheRead('keys');
  if (saved) return Promise.resolve(apply(saved));
  if (!reads.keys) {
    $('#top-key').setAttribute('aria-busy','true');
    reads.keys = api('/portal/api/keys').then(data => { cacheWrite('keys', data); return apply(data); }).finally(() => { reads.keys = null; });
  }
  return reads.keys;
}
function loadUsage({cached = false} = {}) {
  const apply = data => {
    headerValue('top-requests', `${number(data.total)} ${data.total === 1 ? 'request' : 'requests'}`);
    return data;
  };
  const saved = cached && cacheRead('usage', 60_000);
  if (saved) return Promise.resolve(apply(saved));
  if (!reads.usage) {
    $('#top-requests').setAttribute('aria-busy','true');
    reads.usage = api('/portal/api/usage').then(data => { cacheWrite('usage', data); return apply(data); }).finally(() => { reads.usage = null; });
  }
  return reads.usage;
}

async function overview({retryFailedOnly = false} = {}) {
  const names = ['keys','usage','coverage'], loaders = [loadKeys,loadUsage,() => api('/meta/coverage')];
  const results = await Promise.allSettled(names.map((name, index) => retryFailedOnly && state.overviewParts[name]?.status === 'fulfilled' ? state.overviewParts[name].value : loaders[index]()));
  results.forEach((result, index) => { state.overviewParts[names[index]] = result.status === 'fulfilled' ? result : {...result, value:state.overviewParts[names[index]]?.value}; });
  const keys = state.overviewParts.keys.value, usage = state.overviewParts.usage.value, coverage = state.overviewParts.coverage.value;
  const active = keys?.filter(key => !key.revoked_at);
  const failure = name => state.overviewParts[name].status === 'rejected' ? `<div class="metric-load-error"><p class="help">${state.overviewParts[name].value ? 'Showing the last loaded value. Could not refresh.' : 'Could not load this information.'}</p><button class="text-link" data-retry-overview>Try again</button></div>` : '';
  return layout('Overview', 'Your social data workspace. Everything you need to start building.', `
    <div class="metrics-grid">
      <section class="card"><div class="card-title">${icon('chart-bar')}Total requests</div>${usage ? `<div class="stat">${number(usage.total)}</div><div class="stat-label">All time · ${PORTAL.role === 'owner' ? 'Workspace' : 'Your account'}</div>` : ''}${failure('usage')}</section>
      <section class="card"><div class="card-title">${icon('shield-check')}Active API keys</div>${active ? `<div class="stat">${number(active?.length)}</div><div class="stat-label">${PORTAL.role === 'owner' ? 'Across your workspace' : 'Created by you'}</div>` : ''}${failure('keys')}</section>
      <section class="card"><div class="card-title">${icon('globe')}Supported endpoints</div>${coverage ? `<div class="stat">${number(coverage.implemented)}<span class="muted small"> / ${number(coverage.total)}</span></div>` : ''}${failure('coverage')}<div class="stat-label platform-logos">${platforms.map(platform => platformLabel(platform)).join('')}</div></section>
    </div>
    <div class="grid">
      <section class="card quick-start"><div class="eyebrow">Start building</div><h2>From endpoint to integration.</h2><p class="muted">Explore social data with a request, then take the code into your app.</p><div class="steps"><div class="step"><span class="step-number">1</span><div><h3>Create an API key</h3><p>Give each integration its own revocable credential.</p></div></div><div class="step"><span class="step-number">2</span><div><h3>Try an endpoint</h3><p>Choose a platform and send a request in the playground.</p></div></div><div class="step"><span class="step-number">3</span><div><h3>Make it yours</h3><p>Copy a cURL, Node.js, or Python snippet into your project.</p></div></div></div><a class="primary" href="/app/playground">Open playground ${icon('arrow-right')}</a></section>
      <div class="stack"><section class="card"><div class="card-head"><h2>Your API access</h2>${icon('shield-check')}</div><div class="keybox"><code>${esc(keys ? mask(active?.[0]) : 'Key status unavailable')}</code></div><p class="help">${!keys ? 'Could not load your key status. Open API keys to try again.' : active?.length ? 'Pass your key in the x-api-key header. Full secrets are only shown when you create them.' : 'Create your first API key to connect an integration. You can explore the playground with your workspace session.'}</p><a class="ghost" style="margin-top:20px" href="/app/keys">${!keys ? 'Open API keys' : active?.length ? 'Manage API keys' : 'Create API key'} ${icon('arrow-right')}</a></section><section class="card"><div class="card-head"><h2>One API. Three platforms.</h2>${icon('globe')}</div><p class="help">Read public social data through a consistent API. Endpoint availability depends on each platform.</p><div class="platforms">${platformList()}</div><a class="text-link small" style="display:inline-block;margin-top:20px" href="/app/docs">Explore the documentation</a></section></div>
    </div>`);
}

async function keysPage() {
  const keys = await loadKeys();
  return layout('API keys', 'Separate credentials for each person, app, and integration.', `
    <section class="card"><form id="key-form" class="row"><label class="field">Key name<input id="key-name" name="name" placeholder="e.g. Production app" required maxlength="80" autocomplete="off" aria-describedby="key-hint"></label><button class="primary" type="submit">${icon('plus')}Create API key</button></form><p class="help" id="key-hint" style="margin-top:12px">Your new secret is shown once. Save it before closing the dialog.</p><div id="action-error" class="field-error" role="alert" hidden></div></section>
    <div class="table-caption"><h2>Workspace credentials</h2><span>${number(keys.length)} ${keys.length === 1 ? 'key' : 'keys'}</span></div>
    ${keys.length ? tableRegion('API keys', `<table><thead><tr><th scope="col">Name</th><th scope="col">Key</th><th scope="col">Owner</th><th scope="col">Created</th><th scope="col">Last used</th><th scope="col">Status / action</th></tr></thead><tbody>${keys.map(key => `<tr><td>${esc(key.name)}</td><td><code>${esc(mask(key))}</code></td><td>${esc(key.email)}</td><td>${fmt(key.created_at)}</td><td>${fmt(key.last_used_at)}</td><td>${key.revoked_at ? '<span class="badge bad">Revoked</span>' : `<button class="danger" data-revoke="${key.id}" aria-label="Revoke ${esc(key.name)}">Revoke</button>`}</td></tr>`).join('')}</tbody></table>`) : `<section class="card">${emptyState('shield-check', 'Your first integration starts here', 'Name a key above to create a credential for your app.')}</section>`}`);
}

async function team() {
  if (PORTAL.role !== 'owner') return layout('Team', 'A private workspace, shared by invitation.', `<section class="card">${emptyState('users-three', 'You’re part of the team', 'Ask your workspace owner to invite or manage members.')}</section>`);
  const data = await api('/portal/api/team');
  return layout('Team', 'Give your team a private place to work with social data.', `
    <section class="card"><form class="row" id="invite-form"><label class="field">Email address<input id="invite-email" name="email" type="email" placeholder="teammate@company.com" required autocomplete="email" aria-describedby="invite-hint"></label><button class="primary" type="submit">${icon('plus')}Create invitation</button></form><p class="help" id="invite-hint" style="margin-top:12px">Invitation links expire after 7 days. Share the generated link privately.</p><div class="field-error" id="action-error" role="alert" hidden></div></section>
    <div class="table-caption"><h2>Workspace members</h2><span>${number(data.members.length)} ${data.members.length === 1 ? 'member' : 'members'}</span></div>
    ${tableRegion('Workspace members', `<table><thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">API keys</th><th scope="col">Joined</th><th scope="col">Last sign-in</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${data.members.map(member => `<tr><td>${esc(member.email)}</td><td><span class="badge">${member.role === 'owner' ? 'Owner' : 'Member'}</span></td><td>${number(member.key_count)}</td><td>${fmt(member.created_at)}</td><td>${fmt(member.last_login_at)}</td><td>${member.role === 'member' ? `<button class="danger" data-remove="${member.id}" aria-label="Remove ${esc(member.email)}">Remove</button>` : ''}</td></tr>`).join('')}</tbody></table>`)}`);
}

async function settingsPage() {
  if (PORTAL.role !== 'owner') return layout('Platform settings', 'Credentials and network access by provider.', `<section class="card">${emptyState('shield-check', 'Managed by your workspace owner', 'Ask your owner to update platform credentials and proxies.')}</section>`);
  const data = await api('/portal/api/settings/platforms');
  const names = {instagram:'Instagram', tiktok:'TikTok', youtube:'YouTube'};
  const list = (platform, kind, items) => items.length ? `<ol class="credential-pool">${items.map(item => `<li><div><strong>${item.position === 1 ? 'Primary' : `Backup ${item.position - 1}`}</strong><span>${kind === 'proxy' ? 'Proxy credentials saved privately' : 'Account cookies saved privately'} · ${esc(fmt(item.updated_at))}</span></div><div class="pool-actions">${kind === 'proxy' ? `<button class="ghost" type="button" data-test-proxy data-platform="${platform}" data-entry="${esc(item.id)}">Test</button>` : ''}${item.position > 1 ? `<button class="ghost" type="button" data-move-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}" data-direction="up" aria-label="Move ${kind} up">↑</button>` : ''}${item.position < items.length ? `<button class="ghost" type="button" data-move-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}" data-direction="down" aria-label="Move ${kind} down">↓</button>` : ''}<button class="danger" type="button" data-remove-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}">Remove</button></div></li>`).join('')}</ol>` : `<div class="empty compact">No ${kind === 'proxy' ? 'proxies' : 'cookie accounts'} configured.</div>`;
  const cards = Object.entries(names).map(([platform, name]) => {
    const item = data[platform], proxies = item.proxy, cookies = item.cookies;
    return `<section class="card platform-card"><div class="card-head"><div><div class="eyebrow">Provider</div><h2>${platformLabel(platform)}</h2></div><span class="badge ${proxies.length || cookies.length ? 'good' : ''}">${proxies.length || cookies.length ? `${proxies.length} prox${proxies.length === 1 ? 'y' : 'ies'} · ${cookies.length} account${cookies.length === 1 ? '' : 's'}` : 'Anonymous'}</span></div>
      <div class="setting-section"><div class="setting-copy"><h3>Proxy pool</h3><p class="muted">Tried in order only for retryable network, block, login-wall, or rejected-session failures.</p>${list(platform,'proxy',proxies)}</div>${proxies.length < 5 ? `<form class="platform-setting-form stack compact-setting-form" data-platform="${platform}" data-kind="proxy"><label class="field">Add ${proxies.length ? `Backup ${proxies.length}` : 'primary'} proxy<input name="value" type="password" required autocomplete="off" spellcheck="false" placeholder="http://username:password@host:port"><span class="help">Values are encrypted and never shown again.</span></label><div class="field-error" role="alert" hidden></div><button class="primary" type="submit">Add proxy</button></form>` : `<div class="inline-note pool-full">${icon('check')}<span>Proxy pool full · 5 of 5 configured</span></div>`}</div>
      <div class="setting-section"><div class="setting-copy"><h3>Account cookie pool</h3><p class="muted">Each retry uses one complete cookie jar; cookies from different accounts are never merged.</p>${list(platform,'cookies',cookies)}</div><form class="platform-setting-form stack" data-platform="${platform}" data-kind="cookies"><label class="field">Add ${cookies.length ? `Backup ${cookies.length}` : 'primary'} account cookies<input name="file" type="file" required accept=".txt,text/plain"><span class="help">Dedicated throwaway account, Netscape cookies.txt, maximum 2 MiB. Up to five.</span></label><div class="field-error" role="alert" hidden></div><button class="primary" type="submit">Add account cookies</button></form></div></section>`;
  }).join('');
  return layout('Platform settings', 'Configure ordered backup pools independently for Instagram, TikTok, and YouTube.', `<div class="platform-settings">${cards}</div><div class="inline-note settings-note">${icon('shield-check')}<span>Attempt 1 uses both primaries. Later attempts advance to the next configured proxy and cookie account, reusing the last entry on the shorter list. Bad input and missing content are never retried.</span></div>`);
}

async function requestHistory() {
  const data = await api('/portal/api/history?limit=200');
  return layout('Request history', 'Inspect API activity, response status, and timing.', `
    <div class="table-caption"><span>Latest ${number(data.requests.length)} requests · ${PORTAL.role === 'owner' ? 'Workspace' : 'Your account'}</span><span>Times shown in ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}</span></div>
    ${data.requests.length ? tableRegion('API request history', `<table><thead><tr><th scope="col">Time</th><th scope="col">API key</th><th scope="col">Endpoint</th><th scope="col">Response</th><th scope="col">Duration</th><th scope="col">Cache</th></tr></thead><tbody>${data.requests.map(request => `<tr class="history-row" tabindex="0" data-history-id="${request.id}" aria-label="Open response from ${esc(fmt(request.created_at))}"><td>${fmt(request.created_at)}</td><td>${esc(request.prefix || 'Workspace session')}</td><td><div class="route-identity">${platformIcon(platformFromPath(request.path))}<code>${esc(request.method)} ${esc(request.path)}${request.query ? '?' + esc(request.query) : ''}</code></div></td><td><span class="badge ${request.status < 400 ? 'good' : 'bad'}">${request.status} ${request.status < 400 ? 'OK' : 'Error'}</span>${request.fallback ? ' <span class="badge">via ScrapeCreators</span>' : ''}</td><td>${number(request.duration_ms)} ms</td><td>${request.cached ? 'Hit' : 'Miss'}</td></tr>`).join('')}</tbody></table>`) : `<section class="card">${emptyState('clock', 'Your requests will appear here', 'Send a request in the playground to inspect its response and timing.', '<a class="primary" href="/app/playground">Open playground</a>')}</section>`}`);
}

async function showHistoryDetail(id) {
  if (state.historyRequest?.id === id) return;
  const request = {id}; state.historyRequest = request;
  openDialog({title:'Request response', description:'Loading the stored response…', body:`<div class="history-detail-loading"><div class="history-facts" aria-hidden="true">${Array.from({length:3}, () => '<div><div class="skeleton sk-label"></div><div class="skeleton sk-line"></div></div>').join('')}</div><div class="response-skeleton history-response" aria-hidden="true">${'<span class="skeleton sk-line"></span>'.repeat(8)}</div></div>`, footer:'<button class="ghost" data-dialog-close>Close</button>'});
  const body = $('#dialog .modal-body'); body.setAttribute('aria-busy','true');
  $('#loading-status').textContent = 'Loading stored response…';
  try {
    const detail = await api(`/portal/api/history/${id}`);
    if (state.historyRequest !== request || !$('#dialog').open) return;
    let formatted = detail.response || '';
    try { ResponsePreview.parseLossless(formatted); formatted = ResponsePreview.indent(formatted.trim()); } catch {}
    const route = detail.path + (detail.query ? `?${detail.query}` : '');
    const overlay = body.querySelector('.history-detail-loading');
    const content = document.createElement('div');
    content.innerHTML = `<dl class="history-facts"><div><dt>Time</dt><dd>${esc(fmt(detail.created_at))}</dd></div><div><dt>API key</dt><dd>${esc(detail.prefix || 'Workspace session')}</dd></div><div><dt>Request</dt><dd><code>${esc(route)}</code></dd></div></dl>${detail.response_truncated ? '<div class="inline-note">The stored response was capped at 2 MiB.</div>' : ''}<pre class="code history-response" id="history-response" tabindex="0"></pre>`;
    content.querySelector('pre').textContent = formatted || 'No response body was stored for this request.';
    $('#dialog-title').textContent = `${detail.method} ${detail.path}`;
    $('#dialog-description').textContent = `${detail.status} · ${number(detail.duration_ms)} ms · ${detail.cached ? 'Cache hit' : 'Live'}`;
    body.prepend(content); body.setAttribute('aria-busy','false');
    overlay.classList.add('loading-overlay'); reveal(content, overlay);
    $('#dialog .modal-foot').insertAdjacentHTML('beforeend', `<button class="primary" data-copy="history-response">${icon('copy')}Copy response</button>`);
    $('#loading-status').textContent = `Stored response loaded. Status ${detail.status}.`;
  } catch (error) {
    if (state.historyRequest !== request || !$('#dialog').open) return;
    $('#dialog-description').textContent = 'The stored response could not load.';
    body.querySelector('.history-detail-loading').innerHTML = `<div class="history-response"><div class="form-error" role="alert">${esc(error.message)}</div><button class="ghost" data-history-retry="${esc(id)}">${icon('arrows-clockwise')}Try again</button></div>`;
    body.setAttribute('aria-busy','false');
    $('#loading-status').textContent = 'Stored response could not load. Try again when you are ready.';
  } finally { if (state.historyRequest === request) state.historyRequest = null; }
}

async function usage() {
  const data = await loadUsage();
  const total = data.daily.reduce((sum, day) => sum + day.total, 0), max = Math.max(...data.daily.map(day => day.total), 1);
  const labels = data.daily.map((_, index) => { const date = new Date(); date.setDate(date.getDate() - 29 + index); return date.toLocaleDateString(undefined, {month:'short', day:'numeric'}); });
  const series = [['instagram','Instagram'],['tiktok','TikTok'],['youtube','YouTube']];
  const legend = `<ul class="chart-legend" aria-label="Platforms">${series.map(([key]) => `<li><span class="legend-swatch ${key}" aria-hidden="true"></span>${platformLabel(key)}</li>`).join('')}</ul>`;
  const chart = data.daily.map((day, index) => { const title=`${labels[index]} — Total ${day.total}; Instagram ${day.instagram}; TikTok ${day.tiktok}; YouTube ${day.youtube}`; return `<div class="bar-stack" tabindex="0" role="img" aria-label="${esc(title)}" style="height:${Math.max(1,day.total/max*100)}%" title="${esc(title)}">${series.map(([key]) => day[key] ? `<span class="bar-segment ${key}" style="height:${day[key]/day.total*100}%"></span>` : '').join('')}</div>`; }).join('');
  return layout('Usage', 'Understand your API traffic over the last 30 days.', `
    <section class="card"><div class="card-head"><div><h2>Request volume</h2><span class="chart-summary">${number(total)} requests · ${PORTAL.role === 'owner' ? 'Workspace' : 'Your account'} · Last 30 days${data.fallback_count > 0 ? ` · ${number(data.fallback_count)} via ScrapeCreators` : ''}</span></div><span class="badge">30 days</span></div>
    ${total ? `${legend}<div class="chart" aria-label="Stacked daily requests by platform. Exact counts are in the table below.">${chart}</div><div class="chart-labels"><span>${labels[0]}</span><span>${labels[14]}</span><span>${labels[29]}</span></div>` : emptyState('chart-bar', 'No requests in this period', 'Usage appears here when you call an API endpoint.', '<a class="ghost" href="/app/playground">Make your first request</a>')}
    <details><summary>${icon('chart-bar')}View daily data</summary><p class="help">Rolling 24-hour periods ending now. Date labels use your local timezone.</p>${tableRegion('Daily request counts by platform', `<table><thead><tr><th scope="col">Period ending</th><th scope="col">${platformLabel('instagram')}</th><th scope="col">${platformLabel('tiktok')}</th><th scope="col">${platformLabel('youtube')}</th><th scope="col">Total</th></tr></thead><tbody>${data.daily.map((day,index) => `<tr><td>${labels[index]}</td><td>${number(day.instagram)}</td><td>${number(day.tiktok)}</td><td>${number(day.youtube)}</td><td><strong>${number(day.total)}</strong></td></tr>`).join('')}</tbody></table>`)}</details></section>
    <section class="card" style="margin-top:24px"><h2>Most used endpoints</h2><p class="help">Last 30 days · Up to 10 routes</p>${data.routes.map(route => `<div class="param"><strong class="route-identity">${platformIcon(platformFromPath(route.path))}<code>${esc(route.path)}</code></strong><small>${number(route.count)} requests</small></div>`).join('') || emptyState('globe', 'No endpoint activity yet', 'Run a request to see your most used routes.')}</section>`);
}

async function spec() {
  if (state.spec) return state.spec;
  if (!reads.spec) reads.spec = api('/openapi.json').then(schema => (state.spec = schema)).finally(() => { reads.spec = null; });
  return reads.spec;
}
function endpoints(schema) {
  return Object.entries(schema.paths).filter(([path, item]) => item.get && platforms.includes(platformFromPath(path))).map(([path, item]) => ({path, op:item.get, platform:platformFromPath(path)}));
}
function platformName(platform) { return {youtube:'YouTube', instagram:'Instagram', tiktok:'TikTok'}[platform] || platform; }
async function playground() {
  const [schema] = await Promise.all([spec(), loadKeys()]);
  const groups = [...new Set(endpoints(schema).map(endpoint => endpoint.platform))];
  const selected = groups.includes(state.platform) ? state.platform : groups[0];
  return layout('Playground', 'Explore an endpoint. Make a request. Take the code with you.', `
    <div class="playground"><section class="card playground-config"><div class="section-label">Configure request</div><form class="form-stack" id="request-form"><div class="field"><span id="platform-label">Platform</span><input type="hidden" id="pg-platform" value="${selected}"><button type="button" class="platform-trigger" id="platform-trigger" popovertarget="platform-menu" aria-haspopup="listbox" aria-expanded="false" aria-controls="platform-menu" aria-labelledby="platform-label platform-selected"><span id="platform-selected">${platformLabel(selected)}</span>${icon('caret-down')}</button><div id="platform-menu" class="platform-menu" popover="auto" role="listbox" aria-labelledby="platform-label">${groups.map(group => `<button type="button" class="platform-option" role="option" data-platform-choice="${group}" aria-selected="${selected === group}" tabindex="-1">${platformLabel(group)}${icon('check')}</button>`).join('')}</div></div><label class="field">Endpoint<select id="pg-endpoint"></select></label><label class="field">Run as<select id="pg-key"><option value="">Workspace session</option>${state.keys.filter(key => !key.revoked_at).map(key => `<option value="${key.id}" ${String(key.id) === state.selectedKey ? 'selected' : ''}>${esc(key.name)} · ${esc(key.prefix)}</option>`).join('')}</select><span class="help">Snippets use your integration’s API key.</span></label><div id="pg-params"></div><div class="inline-note" id="endpoint-note" hidden></div><div class="field-error" id="request-error" role="alert" hidden></div><button class="primary" type="submit" id="run-request">${icon('play')}Run request</button></form></section>
    <div class="request-workbench"><section class="card"><div class="card-head"><div class="request-info"><span id="request-platform" aria-hidden="true"></span><span class="method">GET</span><code id="request-path"></code></div><button class="icon-button" data-copy="snippet" aria-label="Copy code snippet" title="Copy snippet">${icon('copy')}</button></div><div class="code-tabs" role="tablist" aria-label="Code language">${[['curl','cURL'],['node','Node.js'],['python','Python']].map(([lang, label]) => `<button id="tab-${lang}" role="tab" aria-controls="snippet" aria-selected="${state.language === lang}" tabindex="${state.language === lang ? 0 : -1}" data-language="${lang}">${label}</button>`).join('')}</div><pre class="code" id="snippet" role="tabpanel" tabindex="0" aria-labelledby="tab-${state.language}" style="margin-top:16px"></pre></section>
    <section class="card response"><div class="card-head"><h2>Response</h2><div class="row"><span class="badge" id="response-status" role="status">Ready</span><button class="icon-button" data-copy="response-raw" aria-label="Copy raw JSON response" title="Copy raw JSON" hidden>${icon('copy')}</button></div></div><div id="response"><div class="response-placeholder"><div>${icon('compass')}<strong>Your response starts here</strong><p>Choose an endpoint and run a request to see the result.</p></div></div></div></section></div></div>`);
}
function saveDraft() {
  const responseView = $('#response [data-response-view][aria-selected=true]')?.dataset.responseView;
  if (responseView && state.request?.path === state.endpoint) {
    const response = state.request.pending || state.request.error ? state.request.previous : state.request;
    if (response) response.view = responseView;
  }
  if (!state.endpoint || !$('#pg-params')?.children.length) return;
  state.drafts[state.endpoint] = Object.fromEntries([...document.querySelectorAll('[data-param]')].map(input => [input.dataset.param, input.value]));
}
function fillEndpoints() {
  saveDraft();
  const platform = $('#pg-platform').value;
  state.platform = platform;
  const available = endpoints(state.spec).filter(endpoint => endpoint.platform === platform);
  $('#pg-endpoint').innerHTML = available.map(endpoint => `<option value="${esc(endpoint.path)}" ${state.endpoint === endpoint.path ? 'selected' : ''}>${esc(endpoint.op.summary || endpoint.path)}${endpoint.op.tags?.includes('not supported') ? ' · Not supported' : ''}</option>`).join('');
  fillParams();
}
function fillParams() {
  saveDraft();
  state.endpoint = $('#pg-endpoint').value;
  const operation = state.spec.paths[state.endpoint].get;
  $('#request-path').textContent = state.endpoint;
  $('#request-platform').innerHTML = platformIcon(platformFromPath(state.endpoint));
  const parameters = (operation.parameters || []).filter(param => param.in !== 'header');
  const renderParam = (param, index) => {
    const type = param.schema?.type;
    const label = `<span class="field-label"><code>${esc(param.name)}</code>${param.required ? '<span class="badge required">Required</span>' : '<span class="help">Optional</span>'}</span>`;
    const value = state.drafts[state.endpoint]?.[param.name] || '';
    const options = param.schema?.enum || (type === 'boolean' ? ['true', 'false'] : null);
    const hint = param.description || (type === 'integer' ? 'Enter a whole number.' : '');
    const control = options ? `<select id="param-${index}" data-param="${esc(param.name)}" ${param.required ? 'required' : ''} aria-describedby="param-hint-${index}"><option value="">${param.required ? 'Choose a value' : 'Default'}</option>${options.map(option => `<option value="${esc(option)}" ${String(option) === value ? 'selected' : ''}>${esc(option)}</option>`).join('')}</select>` : `<input id="param-${index}" data-param="${esc(param.name)}" type="${type === 'integer' || type === 'number' ? 'number' : 'text'}" ${type === 'integer' ? 'step="1"' : ''} ${param.required ? 'required' : ''} value="${esc(value)}" placeholder="${esc(param.schema?.default ?? (param.name === 'url' ? 'https://…' : ''))}" aria-describedby="param-hint-${index}">`;
    return `<label class="field" for="param-${index}">${label}${control}<span class="help" id="param-hint-${index}">${esc(hint)}</span></label>`;
  };
  const required = parameters.filter(param => param.required);
  const optional = parameters.filter(param => !param.required);
  $('#pg-params').innerHTML = required.map(renderParam).join('') + (optional.length ? `<details class="advanced"><summary>${icon('caret-down')}Optional parameters <span class="badge">${optional.length}</span></summary><div class="parameter-fields">${optional.map((param, index) => renderParam(param, required.length + index)).join('')}</div></details>` : '');
  const unsupported = operation.tags?.includes('not supported');
  $('#endpoint-note').hidden = !unsupported;
  $('#endpoint-note').innerHTML = `${icon('warning-circle')}<span>This endpoint is part of the compatibility API but is not implemented. Running it returns 501. <a href="/app/docs#${encodeURIComponent(state.endpoint)}">View details</a></span>`;
  $('#request-error').hidden = true;
  if (state.request?.path === state.endpoint) paintRequest();
  else {
    $('#response').innerHTML = emptyState('compass', 'Your response starts here', 'Choose an endpoint and run a request to see the result.');
    $('#response').setAttribute('aria-busy','false');
    $('#response-status').className = 'badge'; $('#response-status').textContent = 'Ready';
    $('[data-copy="response-raw"]').hidden = true;
  }
  updateSnippet();
}
function params() {
  const query = new URLSearchParams();
  document.querySelectorAll('[data-param]').forEach(input => { if (input.value) query.set(input.dataset.param, input.value); });
  return query;
}
function updateSnippet() {
  if (!$('#pg-endpoint')) return;
  const query = params();
  const url = location.origin + $('#pg-endpoint').value + (query.size ? '?' + query : '');
  const snippets = {
    curl:`curl '${url}' \\\n  -H 'x-api-key: YOUR_API_KEY'`,
    node:`const response = await fetch(${JSON.stringify(url)}, {\n  headers: { 'x-api-key': process.env.SOCIALSCRAPE_API_KEY }\n});\n\nconst data = await response.json();\nconsole.log(data);`,
    python:`import os\nimport requests\n\nresponse = requests.get(\n    ${JSON.stringify(url)},\n    headers={"x-api-key": os.environ["SOCIALSCRAPE_API_KEY"]},\n)\nprint(response.json())`
  };
  $('#snippet').textContent = snippets[state.language];
  $('#snippet').setAttribute('aria-labelledby', `tab-${state.language}`);
  document.querySelectorAll('[data-language]').forEach(tab => {
    const selected = tab.dataset.language === state.language;
    tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
  });
  saveDraft();
}
function paintRequest() {
  const request = state.request, form = $('#request-form');
  if (!form || !request || $('#pg-endpoint').value !== request.path) return;
  const area = $('#response'), badge = $('#response-status'), button = $('#run-request'), copy = $('[data-copy="response-raw"]');
  const mount = response => {
    area.innerHTML = '<div class="response-content"></div>';
    const content = area.firstChild;
    ResponsePreview.mount(content, response);
    return content;
  };
  area.setAttribute('aria-busy', String(request.pending));
  form.querySelectorAll('input, select, #platform-trigger').forEach(field => { field.disabled = request.pending; });
  if (request.pending) {
    const done = pendingButton(button, 'Running request…');
    if (done) request.releases.add(done);
    badge.className = 'badge'; badge.textContent = request.slow ? 'Still running' : 'Running';
    copy.hidden = true;
    if (request.previous) {
      if (!area.querySelector('#response-raw')) mount(request.previous);
      if (!area.querySelector('.previous-response')) area.insertAdjacentHTML('afterbegin', '<p class="previous-response">Showing your previous response while this request runs.</p>');
    } else if (!area.querySelector('.response-skeleton')) {
      area.innerHTML = `<div class="response-skeleton" aria-hidden="true">${'<i class="skeleton sk-line"></i>'.repeat(8)}</div>`;
    }
    return;
  }
  const skeleton = area.querySelector('.response-skeleton')?.cloneNode(true);
  if (request.text !== undefined || request.previous) {
    const content = request.error && area.querySelector('#response-raw') ? area.querySelector('.response-content') : mount(request.text !== undefined ? request : request.previous);
    if (request.error) {
      if (!area.querySelector('.previous-response')) area.insertAdjacentHTML('afterbegin', '<p class="previous-response"></p>');
      area.querySelector('.previous-response').textContent = 'Your previous response is shown below.';
    }
    if (skeleton && !request.error) { skeleton.classList.add('loading-overlay'); area.append(skeleton); reveal(content, skeleton); }
    copy.hidden = false;
  } else { area.innerHTML = emptyState('globe', 'No response received', 'Check your connection and run the request again. Your request fields are preserved.'); copy.hidden = true; }
  badge.className = `badge ${request.error || !request.ok ? 'bad' : 'good'}`;
  badge.textContent = request.error ? 'Connection failed' : `${request.status} ${request.ok ? 'OK' : 'Error'} · ${number(request.duration)} ms`;
  if (request.error) showError(Error(request.error), '#request-error');
}
async function runRequest() {
  if (state.request?.pending) return;
  updateSnippet();
  const query = params(), path = $('#pg-endpoint').value, keyId = $('#pg-key').value;
  const last = state.request?.path === path ? state.request : null;
  const previous = last?.text !== undefined ? {path:last.path,status:last.status,text:last.text,view:last.view} : last?.previous;
  const request = {path, pending:true, releases:new Set(), previous, started:performance.now()};
  state.request = request;
  $('#request-error').hidden = true;
  $('#platform-menu')?.hidePopover();
  paintRequest();
  $('#loading-status').textContent = 'Request running. Waiting for the API response.';
  const slow = setTimeout(() => { request.slow = true; paintRequest(); }, 10000);
  try {
    const response = await fetch(path + (query.size ? '?' + query : ''), {headers:{'x-portal-key-id':keyId}});
    request.text = await response.text();
    request.status = response.status; request.ok = response.ok;
    request.duration = Math.round(performance.now() - request.started);
    $('#loading-status').textContent = `Request finished with status ${response.status}.`;
  } catch {
    request.error = 'Could not read the API response. Check your connection, then run the request again. Your request fields are preserved.';
    $('#loading-status').textContent = 'The response could not be received. Your request fields are preserved.';
  } finally {
    clearTimeout(slow); request.pending = false;
    for (const done of request.releases) done();
    request.releases.clear();
    paintRequest();
    loadUsage().catch(error => headerFailure('top-requests','Usage',error));
  }
}

async function docs() {
  const schema = await spec(), all = endpoints(schema), groups = [...new Set(all.map(endpoint => endpoint.platform))];
  return `<div class="docs"><aside class="docs-nav" aria-label="API reference"><label class="field"><span class="sr-only">Search endpoints</span><input type="search" class="search-input" id="doc-search" placeholder="Search endpoints…"></label><a class="docs-link" href="/app/docs" data-doc-intro>Introduction</a>${groups.map(group => `<div class="docs-group" data-group><h3>${platformLabel(group)}</h3>${all.filter(endpoint => endpoint.platform === group).map(endpoint => `<a class="docs-link" data-doc="${esc(endpoint.path + ' ' + endpoint.op.summary)}" href="/app/docs#${encodeURIComponent(endpoint.path)}"><span class="method">GET</span><span>${esc(endpoint.op.summary || endpoint.path)}</span></a>`).join('')}</div>`).join('')}<p id="docs-no-results" class="help" hidden>No matching endpoints. Try another search.</p></aside><article class="doc-content" id="doc-main"></article></div>`;
}
function filterDocs(query) {
  const terms = query.toLowerCase().trim().split(/\s+/); let matches = 0;
  document.querySelectorAll('[data-doc]').forEach(link => { link.hidden = !terms.every(term => link.dataset.doc.toLowerCase().includes(term)); if (!link.hidden) matches++; });
  document.querySelectorAll('[data-group]').forEach(group => { group.hidden = !group.querySelector('[data-doc]:not([hidden])'); });
  $('#docs-no-results').hidden = matches > 0;
}
function selectDoc() {
  let path = '';
  try { path = decodeURIComponent(location.hash.slice(1)); } catch {}
  if (state.spec.paths[path]?.get) showEndpoint(path); else showIntro();
  document.querySelectorAll('.docs-link').forEach(link => {
    const current = path ? link.hash === location.hash : link.hasAttribute('data-doc-intro');
    if (current) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
}
function showIntro() {
  $('#doc-main').innerHTML = `<div class="eyebrow">Developer reference</div><h1>Social Scrape API</h1><p class="lede">Public social data through one private API.</p><div class="platforms doc-platforms">${platformList()}</div><section class="card"><h2>Start with a request</h2><p class="help">Send a member API key in the <code>x-api-key</code> header. Create and revoke credentials from API keys.</p><pre class="code" tabindex="0">curl '${esc(location.origin)}/v1/tiktok/profile?handle=tiktok' \\\n  -H 'x-api-key: YOUR_API_KEY'</pre><a class="ghost" style="margin-top:20px" href="/app/playground">Open playground ${icon('arrow-right')}</a></section><h2>Authentication</h2><p class="muted">Use a separate key for each person or integration. Secrets are shown once when created, and can be revoked independently.</p><h2>Response format</h2><p class="muted">Successful calls include <code>success: true</code> and <code>credits_charged: 0</code>. Errors include an error code and a message explaining what happened.</p><h2>Caching</h2><p class="muted">Set <code>cache_max_age</code> to <code>12h</code>, <code>1d</code>, or <code>7d</code> to accept a cached response. Leave it out to fetch fresh data.</p><h2>Endpoint availability</h2><p class="muted">The reference includes the full compatibility API. Routes outside the supported extraction boundary return <code>501</code> with a reason. Platform restrictions may require cookies or a proxy.</p>`;
}
function showEndpoint(path) {
  const operation = state.spec.paths[path].get, parameters = operation.parameters || [];
  const unsupported = operation.tags?.includes('not supported');
  $('#doc-main').innerHTML = `<div class="eyebrow platform-heading">${platformLabel(platformFromPath(path))} API</div><div class="endpoint-title"><span class="method">GET</span><h1>${esc(operation.summary || path)}</h1></div><p class="lede"><code>${esc(path)}</code></p>${unsupported ? '<div class="inline-note"><span class="badge warn">Not supported</span><span>This compatibility route is not implemented through yt-dlp. Requests return 501.</span></div>' : ''}<section class="card"><div class="card-head"><h2>Request</h2><button class="icon-button" data-copy="doc-snippet" aria-label="Copy request snippet">${icon('copy')}</button></div><pre class="code" id="doc-snippet" tabindex="0">curl '${esc(location.origin + path)}' \\\n  -H 'x-api-key: YOUR_API_KEY'</pre></section><h2>Parameters</h2>${parameters.map(param => `<div class="param"><strong><code>${esc(param.name)}</code>${param.required ? '<span class="badge required">Required</span>' : '<span class="help">Optional</span>'}<span class="help">${esc(param.schema?.type || '')}</span></strong><small>${esc(param.description || '')}</small></div>`).join('') || '<p class="muted">No parameters.</p>'}<h2>Responses</h2>${Object.entries(operation.responses || {}).map(([code, response]) => `<div class="param"><strong><span class="badge ${Number(code) < 400 ? 'good' : ''}">${code}</span>${esc(response.description)}</strong></div>`).join('')}`;
}

let dialogTrigger;
function closeDialog() { $('#dialog').close(); }
function openDialog({title, description, body, footer}) {
  const dialog = $('#dialog');
  const alreadyOpen = dialog.open;
  if (!alreadyOpen) dialogTrigger = document.activeElement;
  dialog.innerHTML = `<header class="modal-head"><div><h2 id="dialog-title">${esc(title)}</h2><p id="dialog-description">${esc(description)}</p></div><button class="icon-button" data-dialog-close aria-label="Close dialog">${icon('x')}</button></header><div class="modal-body">${body}<div class="field-error" id="dialog-error" role="alert" hidden></div></div><footer class="modal-foot">${footer}</footer>`;
  if (!alreadyOpen) dialog.showModal();
  else dialog.querySelector('[data-dialog-close]').focus({preventScroll:true});
}
function showSecret(title, description, value, isSecret) {
  openDialog({title, description, body:`<label class="field">${isSecret ? 'API key' : 'Invitation link'}<textarea class="secret" id="new-secret" readonly spellcheck="false">${esc(value)}</textarea></label><p class="help" id="copy-feedback" style="margin-top:12px">${isSecret ? 'Keep this key private. It will not be shown again.' : 'Share this link privately with the invited person.'}</p>`, footer:`<button class="ghost" data-dialog-close>Done</button><button class="primary" data-copy="new-secret">${icon('copy')}Copy ${isSecret ? 'API key' : 'link'}</button>`});
}
function confirmAction(title, description, confirmLabel, action) {
  openDialog({title, description, body:'<p class="help">This takes effect immediately for the selected account or credential.</p>', footer:`<button class="ghost" data-dialog-close autofocus>Cancel</button><button class="danger" id="confirm-action">${esc(confirmLabel)}</button>`});
  $('#confirm-action').addEventListener('click', async event => {
    const button = event.currentTarget;
    const done = pendingButton(button, confirmLabel.startsWith('Revoke') ? 'Revoking…' : 'Removing…');
    if (!done) return;
    try { await action(); await render(); closeDialog(); toast(({'Revoke key':'API key revoked.', 'Remove member':'Member removed.', 'Remove proxy':'Saved proxy removed.'})[confirmLabel] || 'Change saved.'); }
    catch (error) { showError(error, '#dialog-error'); }
    finally { done(); }
  });
}
async function copyElement(id) {
  const element = document.getElementById(id);
  if (!element) return;
  const text = element.value ?? element.textContent;
  try { await navigator.clipboard.writeText(text); toast('Copied to clipboard.'); }
  catch {
    if (id === 'new-secret') {
      element.focus(); element.select(); $('#copy-feedback').textContent = 'Copy is unavailable. Select and copy the text above manually.';
    } else {
      if (element.hidden) document.querySelector(`[role=tab][aria-controls="${id}"]`)?.click();  // e.g. Raw JSON behind Preview
      const range = document.createRange(); range.selectNodeContents(element);
      window.getSelection().removeAllRanges(); window.getSelection().addRange(range);
      toast('Select and copy the highlighted text manually.');
    }
  }
}
async function submitCreation(form) {
  const button = form.querySelector('button[type=submit]');
  const done = pendingButton(button, form.id === 'key-form' ? 'Creating key…' : 'Creating invite…');
  if (!done) return;
  $('#action-error').hidden = true;
  try {
    if (form.id === 'key-form') {
      const data = await api('/portal/api/keys', {method:'POST', body:{name:$('#key-name').value.trim()}});
      await render();
      showSecret('Your API key is ready', 'Save this secret now. It is only shown once.', data.key, true);
    } else {
      const data = await api('/portal/api/invites', {method:'POST', body:{email:$('#invite-email').value.trim()}});
      showSecret('Invitation created', 'This invitation link expires in 7 days.', data.url, false);
      form.reset();
    }
  } catch (error) { showError(error); }
  finally { done(); }
}

async function testPlatformProxy(button) {
  const label = button.textContent;
  button.disabled = true; button.textContent = 'Testing…';
  try {
    const result = await api(`/portal/api/settings/platforms/${button.dataset.platform}/proxy/${button.dataset.entry}/test`, {method:'POST', timeout:25000});
    const detail = result.ok ? `Working · ${result.exit_ip || 'exit IP unavailable'} · ${number(result.latency_ms)} ms` : `${result.blocked ? 'Blocked by provider' : 'Connection failed'}${result.status ? ` · HTTP ${result.status}` : ''} · ${number(result.latency_ms)} ms`;
    toast(detail);
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}

async function savePlatformSetting(form) {
  const button = form.querySelector('button[type=submit]'), error = form.querySelector('.field-error');
  const platform = form.dataset.platform, kind = form.dataset.kind;
  const done = pendingButton(button, kind === 'proxy' ? 'Saving proxy…' : 'Saving cookies…');
  if (!done) return;
  error.hidden = true;
  try {
    let value;
    if (kind === 'cookies') {
      const file = form.elements.file.files[0];
      if (!file) throw Error('Choose a cookies.txt file.');
      if (file.size > 2 * 1024 * 1024) throw Error('The cookies.txt file must be no larger than 2 MiB.');
      value = await file.text();
    } else value = form.elements.value.value.trim();
    await api(`/portal/api/settings/platforms/${platform}/${kind}`, {method:'POST', body:{value}});
    await render(); toast(`${kind === 'proxy' ? 'Proxy' : 'Cookies'} saved for ${platformName(platform)}.`);
  } catch (err) { error.textContent = err.message; error.hidden = false; }
  finally { done(); }
}

async function movePlatformSetting(button) {
  const done = pendingButton(button, 'Updating order…', true);
  if (!done) return;
  const card = button.closest('section.card');
  card.querySelector('[data-pool-error]')?.remove();
  try {
    await api(`/portal/api/settings/platforms/${button.dataset.platform}/${button.dataset.moveSetting}/${button.dataset.entry}/move`, {method:'POST', body:{direction:button.dataset.direction}});
    await render(); toast('Order updated.');
  } catch (error) { const message = document.createElement('div'); message.className = 'field-error'; message.dataset.poolError = ''; message.setAttribute('role','alert'); message.textContent = error.message; card.append(message); }
  finally { done(); }
}

function hidePopovers() {
  document.querySelectorAll('.account-popup:popover-open, .platform-menu:popover-open').forEach(popup => popup.hidePopover());
}
function positionPopup(popup, trigger, kind) {
  const anchor = trigger.getBoundingClientRect(), inset = 12, gap = 8;
  if (kind === 'platform') popup.style.width = `${anchor.width}px`;
  const rect = {width:popup.offsetWidth, height:popup.offsetHeight};
  let left = anchor.left, top = anchor.bottom + gap;
  if (kind === 'account' && innerWidth > 760) {
    left = anchor.right + gap;
    top = anchor.bottom - rect.height;
  } else if (kind === 'account' || top + rect.height > innerHeight - inset) {
    top = anchor.top - rect.height - gap;
  }
  popup.style.left = `${Math.max(inset, Math.min(left, innerWidth - rect.width - inset))}px`;
  popup.style.top = `${Math.max(inset, Math.min(top, innerHeight - rect.height - inset))}px`;
}
function initAccountMenus() {
  const role = PORTAL.role === 'owner' ? 'Workspace owner' : 'Team member';
  document.querySelectorAll('[data-account-trigger]').forEach(trigger => {
    const popup = document.getElementById(trigger.getAttribute('popovertarget'));
    const identity = `<span class="person-avatar" aria-hidden="true">${esc(PORTAL.email.slice(0,1).toUpperCase())}</span><span class="account-copy"><strong>${role}</strong><small title="${esc(PORTAL.email)}">${esc(PORTAL.email)}</small></span>`;
    trigger.innerHTML = `${identity}${icon('dots-three')}`;
    popup.innerHTML = `<div class="account-menu-header"><span class="person-avatar" aria-hidden="true">${esc(PORTAL.email.slice(0,1).toUpperCase())}</span><span class="account-copy"><strong id="${popup.id}-title">${role}</strong><small title="${esc(PORTAL.email)}">${esc(PORTAL.email)}</small></span><button type="button" class="account-theme-toggle" data-theme-toggle aria-label="Switch theme">${icon(document.documentElement.dataset.theme === 'light' ? 'moon' : 'sun')}</button></div><div class="account-separator" role="separator"></div>${PORTAL.role === 'owner' ? `<a href="/app/settings" class="account-action" data-account-action><span class="account-action-icon">${icon('gear-six')}</span><span>Settings</span>${icon('caret-right')}</a><div class="account-separator" role="separator"></div>` : ''}<form method="post" action="/logout"><button type="submit" class="account-action sign-out" data-account-action><span class="account-action-icon">${icon('sign-out')}</span><span>Log out</span></button></form>`;
    popup.addEventListener('toggle', event => {
      const open = event.newState === 'open';
      trigger.setAttribute('aria-expanded', String(open));
      if (open) positionPopup(popup, trigger, 'account');
      else if (popup.contains(document.activeElement)) trigger.focus();
    });
    popup.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); popup.hidePopover(); trigger.focus(); }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const actions = [...popup.querySelectorAll('button, a')];
      let index = actions.indexOf(document.activeElement);
      index = event.key === 'Home' ? 0 : event.key === 'End' ? actions.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + actions.length) % actions.length;
      actions[index].focus();
    });
    trigger.addEventListener('keydown', event => {
      if (!['ArrowUp','ArrowDown'].includes(event.key)) return;
      event.preventDefault(); popup.showPopover();
      const actions = [...popup.querySelectorAll('button, a')];
      actions[event.key === 'ArrowUp' ? actions.length - 1 : 0].focus();
    });
  });
}
function initPlatformPicker() {
  const trigger = $('#platform-trigger'), popup = $('#platform-menu');
  let openingEdge;
  popup.addEventListener('toggle', event => {
    const open = event.newState === 'open';
    trigger.setAttribute('aria-expanded', String(open));
    if (open) {
      positionPopup(popup, trigger, 'platform');
      const choices = [...popup.querySelectorAll('[role=option]')];
      const choice = openingEdge === 'last' ? choices.at(-1) : openingEdge === 'first' ? choices[0] : popup.querySelector('[aria-selected=true]');
      choice.focus(); openingEdge = null;
    }
  });
  trigger.addEventListener('keydown', event => {
    if (!['ArrowDown','ArrowUp'].includes(event.key)) return;
    event.preventDefault(); openingEdge = event.key === 'ArrowUp' ? 'last' : 'first'; popup.showPopover();
  });
  popup.addEventListener('click', event => {
    const choice = event.target.closest('[data-platform-choice]');
    if (!choice) return;
    $('#pg-platform').value = choice.dataset.platformChoice;
    $('#platform-selected').innerHTML = platformLabel(choice.dataset.platformChoice);
    popup.querySelectorAll('[role=option]').forEach(option => option.setAttribute('aria-selected', String(option === choice)));
    fillEndpoints(); popup.hidePopover(); trigger.focus();
  });
  popup.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.preventDefault(); popup.hidePopover(); trigger.focus(); return; }
    if (event.key === 'Tab') { popup.hidePopover(); return; }
    if (!['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const choices = [...popup.querySelectorAll('[role=option]')];
    let index = choices.indexOf(document.activeElement);
    index = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length;
    choices[index].focus();
  });
}
window.addEventListener('resize', () => {
  if (innerWidth > 760 && $('#nav-dialog').open) {
    document.querySelectorAll('#nav-dialog .account-popup:popover-open').forEach(popup => popup.hidePopover());
    $('#nav-dialog').close();
  }
  document.querySelectorAll('.account-popup:popover-open, .platform-menu:popover-open').forEach(popup => {
    const trigger = document.querySelector(`[popovertarget="${popup.id}"]`);
    if (trigger.getBoundingClientRect().width) positionPopup(popup, trigger, popup.classList.contains('account-popup') ? 'account' : 'platform');
    else popup.hidePopover();
  });
});


function page() {
  const route = location.pathname.split('/')[2] || 'overview';
  return ['playground','keys','team','history','usage','docs','settings','fallback'].includes(route) ? route : 'overview';
}
function loadingShape(current) {
  const template = document.querySelector(`template[data-loading-page="${current}"]`);
  return template.innerHTML;
}
function focusSelector(element) {
  if (!element || !$('#view').contains(element)) return null;
  if (element.id) return `#${CSS.escape(element.id)}`;
  if (element.dataset.entry) return `[data-entry="${CSS.escape(element.dataset.entry)}"][data-direction="${CSS.escape(element.dataset.direction || '')}"]`;
  const form = element.closest('form');
  if (form?.id) return `#${CSS.escape(form.id)} ${element.tagName.toLowerCase()}${element.name ? `[name="${CSS.escape(element.name)}"]` : '[type=submit]'}`;
  if (form?.dataset.platform) return `form[data-platform="${form.dataset.platform}"][data-kind="${form.dataset.kind}"] ${element.tagName.toLowerCase()}${element.name ? `[name="${CSS.escape(element.name)}"]` : '[type=submit]'}`;
  return null;
}
function commitView(html, current, refresh) {
  const view = $('#view'), skeleton = view.querySelector('.page-skeleton'), focus = refresh ? focusSelector(document.activeElement) : null;
  const overlay = skeleton?.cloneNode(true);
  view.innerHTML = `<div class="view-frame"><div class="view-content">${html}</div></div>`;
  view.setAttribute('aria-busy', 'false');
  const content = view.querySelector('.view-content');
  if (overlay) { overlay.classList.add('loading-overlay'); overlay.querySelector('.loading-caption')?.remove(); view.firstChild.append(overlay); reveal(content, overlay); }
  state.renderedPage = current;
  if (focus) (view.querySelector(focus) || view).focus({preventScroll:true});
}
async function render({retryFailedOnly = false} = {}) {
  const renderId = ++state.renderId, current = page(), view = $('#view');
  const refresh = state.renderedPage === current && !!view.querySelector('.view-content');
  cancelReveals();
  view.querySelector('[data-refresh-note]')?.remove();
  if (!refresh) { state.renderedPage = null; view.innerHTML = loadingShape(current); }
  else view.insertAdjacentHTML('afterbegin', '<div class="refresh-note" data-refresh-note role="status"><span class="busy-spinner" aria-hidden="true"></span>Refreshing… Your current content stays available.</div>');
  view.setAttribute('aria-busy','true');
  $('#route-progress').hidden = false;
  const loadingName = view.querySelector('h1')?.textContent || 'page';
  document.title = `${loadingName} · Social Scrape`;
  $('#loading-status').textContent = `Loading ${loadingName.toLowerCase()}…`;
  const slow = setTimeout(() => { if (renderId === state.renderId) { const caption = view.querySelector('.loading-caption, [data-refresh-note]'); if (caption) caption.textContent = 'Still loading. You can keep using the navigation.'; } }, 10000);
  if (!retryFailedOnly || state.overviewParts.keys?.status !== 'fulfilled') loadKeys().catch(error => headerFailure('top-key','API keys',error));
  if (!retryFailedOnly || state.overviewParts.usage?.status !== 'fulfilled') loadUsage().catch(error => headerFailure('top-requests','Usage',error));
  document.querySelectorAll('.nav a').forEach(link => {
    if (link.dataset.page === current) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  const views = {overview, playground, keys:keysPage, team, history:requestHistory, usage, docs, settings:settingsPage, fallback:FallbackPage.render};
  try {
    const html = await views[current]({retryFailedOnly});
    if (renderId !== state.renderId) return;
    commitView(html, current, refresh);
    if (current === 'playground') { fillEndpoints(); initPlatformPicker(); paintRequest(); }
    if (current === 'docs') { $('#doc-search').value = state.docSearch; filterDocs(state.docSearch); selectDoc(); }
    const title = view.querySelector('h1')?.textContent || 'Social Scrape';
    document.title = `${title} · Social Scrape`;
    const count = [...(view.querySelector('.table-caption')?.children || [])].map(element => element.textContent.trim()).join('. ');
    $('#loading-status').textContent = `${title} loaded.${count ? ` ${count}.` : ''}${current === 'overview' && Object.values(state.overviewParts).some(part => part.status === 'rejected') ? ' Some information could not load. Use Try again to retry those sections.' : ''}`;
  } catch (error) {
    if (renderId !== state.renderId) return;
    if (refresh) {
      const note = view.querySelector('[data-refresh-note]');
      note.setAttribute('role','alert');
      note.innerHTML = `<span>Could not refresh. Your last loaded content is still available. ${esc(error.message)}</span><button class="text-link" id="retry-page">Try again</button>`;
    } else {
      const heading = view.querySelector('.page-head')?.outerHTML || `<div class="page-head"><h1>${esc(loadingName)}</h1></div>`;
      commitView(`${heading}<section class="card"><div class="form-error" role="alert">${esc(error.message)}</div><button class="ghost" id="retry-page" style="margin-top:20px">${icon('arrows-clockwise')}Try again</button></section>`, current, false);
      state.renderedPage = null;
    }
    $('#loading-status').textContent = `${loadingName} could not ${refresh ? 'refresh' : 'load'}. Try again when you are ready.`;
  } finally {
    clearTimeout(slow);
    if (renderId === state.renderId) { view.setAttribute('aria-busy','false'); $('#route-progress').hidden = true; }
  }
}
async function go(path) {
  saveDraft();
  hidePopovers();
  const nav = $('#nav-dialog'); if (nav.open) nav.close();
  const url = new URL(path, location.origin), oldPage = page();
  window.history.replaceState({...window.history.state, scrollY:window.scrollY}, '');
  window.history.pushState({}, '', url.pathname + url.search + url.hash);
  if (oldPage === 'docs' && page() === 'docs') selectDoc(); else await render();
  $('#view').focus({preventScroll:true}); window.scrollTo(0,0);
}

function startupFailure(error) {
  const view = $('#view');
  view.setAttribute('aria-busy','false'); $('#route-progress').hidden = true;
  view.innerHTML = layout('Could not load this page', 'Your workspace is still here.', `<section class="card"><div class="form-error" role="alert">${esc(error.message || 'The dashboard could not start. Reload to try again.')}</div><a class="ghost" style="margin-top:20px" href="${esc(location.pathname + location.search + location.hash)}">${icon('arrows-clockwise')}Reload page</a></section>`);
  for (const [id,label] of [['top-key','API keys'],['top-requests','Usage']]) headerFailure(id,label,error);
  $('#loading-status').textContent = 'The dashboard could not start. Reload the page to try again.';
}
function startPortal() {
initAccountMenus();
$('.content-foot .footer-platforms').innerHTML = platforms.map(platform => `<span role="img" aria-label="${platformName(platform)}">${platformLabel(platform)}</span>`).join('');
const mobileNav = $('#nav').cloneNode(true); mobileNav.removeAttribute('id'); $('#mobile-nav').append(mobileNav);
if (PORTAL.role !== 'owner') document.querySelectorAll('[data-page="settings"], [data-page="fallback"]').forEach(link => link.remove());
$('#mobile-menu').addEventListener('click', () => $('#nav-dialog').showModal());
$('#dialog').addEventListener('close', () => {
  state.historyRequest = null;
  cancelReveals();
  $('#dialog').replaceChildren();
  const fallback = $('#view').querySelector('input, button, a') || $('#view');
  (dialogTrigger?.isConnected && dialogTrigger !== document.body ? dialogTrigger : fallback).focus({preventScroll:true});
});
for (const dialog of [$('#dialog'), $('#nav-dialog')]) dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
});
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="/app"]');
  if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); go(link.getAttribute('href')); return; }
  const language = event.target.closest('[data-language]');
  if (language) { state.language = language.dataset.language; updateSnippet(); }
  const copy = event.target.closest('[data-copy]'); if (copy) copyElement(copy.dataset.copy);
  if (event.target.closest('[data-dialog-close]')) closeDialog();
  const historyRetry = event.target.closest('[data-history-retry]');
  if (historyRetry) { showHistoryDetail(historyRetry.dataset.historyRetry); return; }
  const historyRow = event.target.closest('[data-history-id]');
  if (historyRow) { showHistoryDetail(historyRow.dataset.historyId); return; }
  const revoke = event.target.closest('[data-revoke]');
  if (revoke) { const key = state.keys.find(item => String(item.id) === revoke.dataset.revoke); confirmAction('Revoke this API key?', `“${key?.name || 'This key'}” will stop working. Integrations using it will lose access.`, 'Revoke key', () => api(`/portal/api/keys/${revoke.dataset.revoke}`, {method:'DELETE'})); }
  const remove = event.target.closest('[data-remove]');
  if (remove) confirmAction('Remove this team member?', `${remove.closest('tr').firstElementChild.textContent} will lose workspace access. Their sessions and API keys will stop working.`, 'Remove member', () => api(`/portal/api/team/${remove.dataset.remove}`, {method:'DELETE'}));
  const testProxy = event.target.closest('[data-test-proxy]');
  if (testProxy) testPlatformProxy(testProxy);
  const moveSetting = event.target.closest('[data-move-setting]');
  if (moveSetting) movePlatformSetting(moveSetting);
  const removeSetting = event.target.closest('[data-remove-setting]');
  if (removeSetting) { const kind=removeSetting.dataset.removeSetting, platform=removeSetting.dataset.platform, entry=removeSetting.dataset.entry; confirmAction(`Remove this ${platformName(platform)} ${kind === 'proxy' ? 'proxy' : 'cookie account'}?`, 'The remaining entries keep their current order.', `Remove ${kind === 'proxy' ? 'proxy' : 'account'}`, () => api(`/portal/api/settings/platforms/${platform}/${kind}/${entry}`, {method:'DELETE'})); }
  if (event.target.closest('#retry-page')) render();
  if (event.target.closest('[data-retry-overview]')) render({retryFailedOnly:true});
});
document.addEventListener('submit', event => {
  if (['key-form','invite-form','request-form'].includes(event.target.id) || event.target.matches('.platform-setting-form')) {
    event.preventDefault();
    if (event.target.id === 'request-form') runRequest();
    else if (event.target.matches('.platform-setting-form')) savePlatformSetting(event.target);
    else submitCreation(event.target);
  }
});
document.addEventListener('input', event => {
  if (event.target.matches('[data-param]')) updateSnippet();
  if (event.target.id === 'doc-search') { state.docSearch = event.target.value; filterDocs(event.target.value); }
});
document.addEventListener('change', event => {
  if (event.target.id === 'pg-platform') fillEndpoints();
  if (event.target.id === 'pg-endpoint') fillParams();
  if (event.target.id === 'pg-key') state.selectedKey = event.target.value;
  if (event.target.matches('[data-param]')) updateSnippet();
});
document.addEventListener('keydown', event => {
  const historyRow = event.target.closest('[data-history-id]');
  if (historyRow && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); showHistoryDetail(historyRow.dataset.historyId); return; }
  if (!event.target.matches('[data-language]') || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll('[data-language]')];
  let index = tabs.indexOf(event.target);
  index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  state.language = tabs[index].dataset.language; updateSnippet(); tabs[index].focus();
});
window.addEventListener('popstate', async event => { saveDraft(); await render(); window.scrollTo(0, event.state?.scrollY || 0); });
window.addEventListener('hashchange', () => { if (page() === 'docs' && $('#doc-main')) selectDoc(); });
loadKeys({cached:true}).catch(error => headerFailure('top-key','API keys',error));
loadUsage({cached:true}).catch(error => headerFailure('top-requests','Usage',error));
render().catch(startupFailure);
}
try { startPortal(); } catch (error) { startupFailure(error); }
