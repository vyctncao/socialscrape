const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const asset = path => `/assets/${path}?v=${encodeURIComponent(document.documentElement.dataset.assetVersion || '')}`;
const icon = name => `<svg class="icon" aria-hidden="true"><use href="${asset('brand/icons.svg')}#${name}"></use></svg>`;
const platforms = ['instagram', 'tiktok', 'youtube'];
const platformIcon = platform => platforms.includes(platform) ? `<img class="platform-icon" data-platform="${platform}" src="${asset(`brand/platforms/${platform}.svg`)}" alt="" width="20" height="20">` : '';
const platformLabel = platform => `<span class="platform-label">${platformIcon(platform)}<span>${esc(platformName(platform))}</span></span>`;
const platformFromPath = path => String(path).split('/')[2];
const platformList = () => platforms.map(platform => `<span class="badge platform-badge">${platformLabel(platform)}</span>`).join('');
const dateFormatter = new Intl.DateTimeFormat(undefined, {year:'numeric', month:'short', day:'numeric', hour:'numeric', minute:'2-digit', timeZoneName:'short'});
const fmt = (time, empty = 'Unavailable') => time ? dateFormatter.format(new Date(time * 1000)) : empty;
const number = value => Number(value).toLocaleString();
const plural = (count, word, many = `${word}s`) => `${number(count)} ${count === 1 ? word : many}`;
const mask = key => key ? `${key.prefix}••••••••••••` : 'No API key yet';
const state = {keys:[], spec:null, language:'curl', drafts:{}, platform:null, endpoint:null, selectedKey:'', docSearch:'', renderId:0, renderedPage:null, overviewParts:{}};
const {pendingButton, reveal, cancelReveals} = window.PortalLoading;
const reads = {};
const leaveGuards = new Set();
const addLeaveGuard = fn => (leaveGuards.add(fn), () => leaveGuards.delete(fn));
const canLeave = () => [...leaveGuards].every(guard => guard());
function normalizeSchema(param) {
  const outer = param?.schema || param || {}, branches = outer.anyOf || outer.oneOf;
  const effective = branches?.filter(branch => branch.type !== 'null');
  const branch = effective?.length === 1 ? effective[0] : {};
  const keys = ['type','enum','default','example','minimum','maximum','minLength','maxLength','pattern','format','items'];
  return Object.fromEntries(keys.flatMap(key => branch[key] !== undefined || outer[key] !== undefined ? [[key, branch[key] ?? outer[key]]] : []));
}
window.normalizeSchema = normalizeSchema;

async function api(url, options = {}) {
  const {timeout: explicitTimeout, signal: callerSignal, _csrfRetried, ...fetchOptions} = options;
  const headers = {...fetchOptions.headers, 'x-csrf-token':PORTAL.csrf};
  if (fetchOptions.body && typeof fetchOptions.body !== 'string') { headers['content-type'] = 'application/json'; fetchOptions.body = JSON.stringify(fetchOptions.body); }
  const read = !fetchOptions.method || fetchOptions.method === 'GET', duration = explicitTimeout ?? (read ? 30000 : null);
  const controller = duration !== null || callerSignal ? new AbortController() : null;
  let timedOut = false, timer = null;
  const relayAbort = () => controller?.abort(callerSignal.reason);
  if (callerSignal) { if (callerSignal.aborted) relayAbort(); else callerSignal.addEventListener('abort', relayAbort, {once:true}); }
  if (duration !== null) timer = setTimeout(() => { timedOut = true; controller.abort(); }, duration);
  let response;
  try { response = await fetch(url, {...fetchOptions, headers, ...(controller ? {signal:controller.signal} : {})}); }
  catch (cause) {
    if (timedOut) { const error = Error(`This request timed out after ${Math.round(duration / 1000)} seconds.`); error.timeout = true; throw error; }
    if (callerSignal?.aborted) throw callerSignal.reason ?? cause;
    throw Error('Could not reach the dashboard. Check your connection, then try again.', {cause});
  } finally { clearTimeout(timer); callerSignal?.removeEventListener('abort', relayAbort); }
  if (response.status === 401) { location.href = '/login'; throw Error('Sign in to continue.'); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 403 && [data.message, data.detail].includes('Invalid CSRF token') && !_csrfRetried) {
      try {
        const fresh = await fetch('/portal/api/me').then(result => result.ok ? result.json() : Promise.reject());
        PORTAL.csrf = fresh.csrf;
        return api(url, {...options, _csrfRetried:true});
      } catch { throw Error('Your session changed. Reload this page, then try again.'); }
    }
    const error = Error(typeof data.detail === 'string' ? data.detail : data.message || `Request failed (${response.status}). Try again.`);
    error.status = response.status; error.data = data; throw error;
  }
  return response.json();
}

const toastQueue = [], visibleToasts = new Set();
function showQueuedToast(item) {
  const element = document.createElement('div'), assertive = ['error','warning'].includes(item.type);
  item.element = element; element.className = `toast ${item.type}`; element.setAttribute('role', assertive ? 'alert' : 'status'); element.setAttribute('aria-live', assertive ? 'assertive' : 'polite');
  const glyph = {success:'check',info:'info',warning:'warning-circle',error:'warning-circle'}[item.type];
  element.innerHTML = `${icon(glyph)}<span>${esc(item.message)}</span>`;
  const close = () => { if (!visibleToasts.delete(item)) return; element.remove(); if (toastQueue.length) showQueuedToast(toastQueue.shift()); };
  if (item.persistent || item.type === 'error') { const button=document.createElement('button'); button.className='toast-close icon-button'; button.setAttribute('aria-label','Dismiss notification'); button.innerHTML=icon('x'); button.addEventListener('click',close); element.append(button); }
  $('#toasts').append(element); visibleToasts.add(item);
  if (!item.persistent && item.type !== 'error') setTimeout(close, 4000);
}
function toast(message, {type = 'success', persistent = false} = {}) {
  const item = {message:String(message), type, persistent};
  if (type === 'success') { const duplicate=[...visibleToasts,...toastQueue].find(existing=>existing.type === type && existing.original === item.message); if (duplicate) { duplicate.count=(duplicate.count || 1)+1; duplicate.message=`${duplicate.original} (${duplicate.count})`; if (duplicate.element) duplicate.element.querySelector('span').textContent=duplicate.message; return; } item.original=item.message; }
  if (visibleToasts.size < 3) showQueuedToast(item); else toastQueue.push(item);
}

function layout(title, lede, body, badge = '') {
  return `<div class="page-head"><div><h1>${title}</h1><p class="lede">${lede}</p></div>${badge ? `<span class="badge">${badge}</span>` : ''}</div>${body}`;
}
function emptyState(glyph, title, copy, action = '', level = 3) {
  const heading = level === 2 ? 'h2' : 'h3';
  return `<div class="empty">${icon(glyph)}<${heading}>${title}</${heading}><p>${copy}</p>${action}</div>`;
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
function loadKeys() {
  if (!reads.keys) reads.keys = api('/portal/api/keys').then(data => (state.keys = data.keys)).finally(() => { reads.keys = null; });
  return reads.keys;
}
function loadUsage() {
  if (!reads.usage) reads.usage = api('/portal/api/usage').finally(() => { reads.usage = null; });
  return reads.usage;
}
// Related views share one sidebar destination; each tab keeps its own URL for Back and sharing.
const sections = {
  playground:[['playground', '/app/playground', 'Request builder'], ['tests', '/app/tests', 'Endpoint tests']],
  usage:[['usage', '/app/usage', 'Analytics'], ['history', '/app/history', 'Request history']],
  settings:[['settings', '/app/settings', 'Proxies & accounts'], ['fallback', '/app/fallback', 'Fallback']]
};
const sectionOf = current => Object.keys(sections).find(name => sections[name].some(([page]) => page === current)) || current;
function pageTabs(current) {
  const name = sectionOf(current);
  return `<nav class="page-tabs" aria-label="${{playground:'Playground', usage:'Usage', settings:'Settings'}[name]} sections">${sections[name].map(([page, href, label]) => `<a href="${href}"${page === current ? ' aria-current="page"' : ''}>${label}</a>`).join('')}</nav>`;
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
      <section class="card"><div class="card-title">${icon('globe')}Supported endpoints</div>${coverage ? `<div class="stat">${number(coverage.implemented)}<span class="muted small"> / ${number(coverage.total)}</span></div>` : ''}${failure('coverage')}${coverage ? '<div class="stat-label">Across Instagram, TikTok and YouTube</div>' : ''}</section>
    </div>
    <div class="grid">
      <section class="card quick-start"><div class="eyebrow">Start building</div><h2>From endpoint to integration.</h2><p class="muted">Explore social data with a request, then take the code into your app.</p><div class="steps"><div class="step"><span class="step-number">1</span><div><h3>Create an API key</h3><p>Give each integration its own revocable credential.</p></div></div><div class="step"><span class="step-number">2</span><div><h3>Try an endpoint</h3><p>Choose a platform and send a request in the playground.</p></div></div><div class="step"><span class="step-number">3</span><div><h3>Make it yours</h3><p>Copy a cURL, Node.js, or Python snippet into your project.</p></div></div></div><a class="primary" href="/app/playground">Open playground ${icon('arrow-right')}</a></section>
      <div class="stack"><section class="card"><div class="card-head"><h2>Your API access</h2>${icon('shield-check')}</div><div class="keybox"><code>${esc(keys ? mask(active?.[0]) : 'Key status unavailable')}</code></div><p class="help">${!keys ? 'Could not load your key status. Open API keys to try again.' : active?.length ? 'Pass your key in the x-api-key header. Full secrets are only shown when you create them.' : 'Create your first API key to connect an integration. You can explore the playground with your workspace session.'}</p><a class="ghost" style="margin-top:20px" href="/app/keys">${!keys ? 'Open API keys' : active?.length ? 'Manage API keys' : 'Create API key'} ${icon('arrow-right')}</a></section><section class="card"><div class="card-head"><h2>One API. Three platforms.</h2>${icon('globe')}</div><p class="help">Read public social data through a consistent API. Endpoint availability depends on each platform.</p><div class="platforms">${platformList()}</div><a class="text-link small standalone-action" href="/app/docs">Explore the documentation</a></section></div>
    </div>`);
}

async function keysPage() {
  const keys = await loadKeys();
  return layout('API keys', 'Separate credentials for each person, app, and integration.', `
    <section class="card"><form id="key-form" class="row"><label class="field">Key name<input id="key-name" name="name" placeholder="e.g. Production app" required maxlength="80" autocomplete="off" aria-describedby="key-hint key-name-error"></label><button class="primary" type="submit">${icon('plus')}Create API key</button></form><div class="field-error" id="key-name-error" role="alert" hidden></div><p class="help" id="key-hint" style="margin-top:12px">Your new secret is shown once. Save it before closing the dialog.</p><div id="action-error" class="field-error" role="alert" hidden></div></section>
    <div class="table-caption"><h2>Workspace credentials</h2><span>${number(keys.length)} ${keys.length === 1 ? 'key' : 'keys'}</span></div>
    ${keys.length ? tableRegion('API keys', `<table><thead><tr><th scope="col">Name</th><th scope="col">Key</th><th scope="col">Owner</th><th scope="col">Created</th><th scope="col">Last used</th><th scope="col">Status / action</th></tr></thead><tbody>${keys.map(key => `<tr><td>${esc(key.name)}</td><td><code>${esc(mask(key))}</code></td><td>${esc(key.email)}</td><td>${fmt(key.created_at)}</td><td>${fmt(key.last_used_at, 'Not used yet')}</td><td>${key.revoked_at ? '<span class="badge bad">Revoked</span>' : `<button class="danger" data-revoke="${key.id}" aria-label="Revoke ${esc(key.name)}">Revoke</button>`}</td></tr>`).join('')}</tbody></table>`) : `<section class="card">${emptyState('shield-check', 'Your first integration starts here', 'Name a key above to create a credential for your app.', '', 2)}</section>`}`);
}

async function team() {
  if (PORTAL.role !== 'owner') return layout('Team', 'A private workspace, shared by invitation.', `<section class="card">${emptyState('users-three', 'You’re part of the team', 'Ask your workspace owner to invite or manage members.', '', 2)}</section>`);
  const data = await api('/portal/api/team');
  return layout('Team', 'Give your team a private place to work with social data.', `
    <section class="card"><form class="row" id="invite-form"><label class="field">Email address<input id="invite-email" name="email" type="email" placeholder="teammate@company.com" required autocomplete="email" aria-describedby="invite-hint invite-email-error"></label><button class="primary" type="submit">${icon('plus')}Create invitation</button></form><div class="field-error" id="invite-email-error" role="alert" hidden></div><p class="help" id="invite-hint" style="margin-top:12px">Invitation links expire after 7 days. Share the generated link privately.</p><div class="field-error" id="action-error" role="alert" hidden></div></section>
    <div class="table-caption"><h2>Workspace members</h2><span>${number(data.members.length)} ${data.members.length === 1 ? 'member' : 'members'}</span></div>
    ${tableRegion('Workspace members', `<table><thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">API keys</th><th scope="col">Joined</th><th scope="col">Last sign-in</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${data.members.map(member => `<tr><td>${esc(member.email)}</td><td><span class="badge">${member.role === 'owner' ? 'Owner' : 'Member'}</span></td><td>${number(member.key_count)}</td><td>${fmt(member.created_at)}</td><td>${fmt(member.last_login_at, 'Not signed in yet')}</td><td>${member.role === 'member' ? `<button class="danger" data-remove="${member.id}" aria-label="Remove ${esc(member.email)}">Remove</button>` : ''}</td></tr>`).join('')}</tbody></table>`)}`);
}

const settingsLede = 'Choose how requests reach each platform, and what happens when one fails here.';
function ownerOnly(title) {
  return layout(title, settingsLede, `<section class="card">${emptyState('shield-check', 'Managed by your workspace owner', 'Ask your owner to update proxies, accounts and the fallback.', '', 2)}</section>`);
}
function poolLabel(kind, count) {
  if (!count) return kind === 'proxy' ? 'No proxies' : 'No signed-in accounts';
  return `${count} ${kind === 'proxy' ? (count === 1 ? 'proxy' : 'proxies') : (count === 1 ? 'signed-in account' : 'signed-in accounts')}`;
}
function poolPosition(position) { return position === 1 ? 'Primary' : `Backup ${position - 1}`; }
function poolList(platform, kind, items) {
  const noun = kind === 'proxy' ? 'proxy' : 'signed-in account';
  return items.length ? `<ol class="manager-list">${items.map(item => `<li data-entry="${esc(item.id)}"><div class="manager-position"><strong>${poolPosition(item.position)}</strong><span>Updated ${esc(fmt(item.updated_at))}</span>${kind === 'proxy' ? '<span data-proxy-status role="status"></span>' : ''}</div><div class="manager-actions">${kind === 'proxy' ? `<button class="ghost" type="button" data-test-proxy data-platform="${platform}" data-entry="${esc(item.id)}">Test</button>` : ''}${item.position > 1 ? `<button class="icon-button" type="button" data-move-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}" data-direction="up" aria-label="Move ${platformName(platform)} ${noun} ${poolPosition(item.position)} up to ${poolPosition(item.position - 1)}"><span aria-hidden="true">↑</span></button>` : ''}${item.position < items.length ? `<button class="icon-button" type="button" data-move-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}" data-direction="down" aria-label="Move ${platformName(platform)} ${noun} ${poolPosition(item.position)} down to ${poolPosition(item.position + 1)}"><span aria-hidden="true">↓</span></button>` : ''}<button class="danger" type="button" data-remove-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}" aria-label="Remove ${platformName(platform)} ${noun} ${poolPosition(item.position)}">Remove</button></div></li>`).join('')}</ol>` : emptyState(kind === 'proxy' ? 'globe' : 'user-circle', kind === 'proxy' ? 'No proxies configured' : 'No signed-in accounts configured', kind === 'proxy' ? 'Add a primary proxy below.' : 'Add a dedicated throwaway signed-in account below.');
}
function managerFocus(entry, action) {
  return () => { const row=$(`#dialog .manager-list li[data-entry="${CSS.escape(String(entry))}"]`); return row?.querySelector(action) || row?.querySelector('[data-move-setting]') || row?.querySelector('[data-remove-setting]') || row?.querySelector('[data-test-proxy]') || $('#dialog .manager-form input, #dialog [data-dialog-close]'); };
}
async function openPlatformManager(platform, kind, trigger = document.activeElement, initialFocus = null) {
  const data = await api('/portal/api/settings/platforms'), items = data[platform][kind];
  dialogTrigger = trigger;
  const name = platformName(platform), isProxy = kind === 'proxy', full = items.length >= 5, noun = isProxy ? 'proxy' : 'signed-in account';
  const input = isProxy ? '<input name="value" type="password" required autocomplete="off" spellcheck="false" placeholder="http://username:password@host:port" aria-describedby="manager-input-help manager-input-error">' : '<input name="file" type="file" required accept=".txt,text/plain" aria-describedby="manager-input-help manager-input-error">';
  const form = full ? `<div class="inline-note">${icon('check')}<span>Pool full · 5 of 5 configured</span></div>` : `<form class="platform-setting-form manager-form" data-platform="${platform}" data-kind="${kind}"><label class="field">Add ${items.length ? `Backup ${items.length}` : 'primary'} ${noun}${input}<span class="help" id="manager-input-help">${isProxy ? 'Credentials are encrypted and never shown again.' : 'Upload a Netscape cookies.txt file from a dedicated throwaway account, maximum 2 MiB.'}</span></label><div class="field-error" id="manager-input-error" role="alert" hidden></div><button class="primary" type="submit">Add ${noun}</button></form>`;
  openDialog({title:`${name} ${isProxy ? 'proxies' : 'signed-in accounts'}`, description:`Ordered ${isProxy ? 'network' : 'signed-in account'} pool · ${items.length} of 5 configured`, body:`<div class="manager-content" data-platform="${platform}" data-kind="${kind}">${poolList(platform,kind,items)}<div class="field-error manager-pool-error" id="manager-pool-error" role="alert" hidden></div><div class="manager-add">${form}</div></div>`, footer:'<button class="ghost" data-dialog-close>Done</button>', initialFocus});
}

async function settingsPage() {
  if (PORTAL.role !== 'owner') return ownerOnly('Settings');
  const data = await api('/portal/api/settings/platforms');
  const cards = ['instagram','tiktok','youtube'].map(platform => { const item=data[platform]; return `<section class="card provider-summary"><div class="provider-summary-head"><h2>${platformLabel(platform)}</h2><span class="badge ${item.proxy.length || item.cookies.length ? 'good' : ''}">${item.proxy.length || item.cookies.length ? 'Configured' : 'Not configured'}</span></div><div class="provider-setting-row"><div>${icon('globe')}<span><strong>Proxy pool</strong><small>${poolLabel('proxy',item.proxy.length)}</small></span></div><button class="ghost" data-manage-platform="${platform}" data-manage-kind="proxy">Manage proxies</button></div><div class="provider-setting-row"><div>${icon('user-circle')}<span><strong>Signed-in accounts</strong><small>${poolLabel('cookies',item.cookies.length)}</small></span></div><button class="ghost" data-manage-platform="${platform}" data-manage-kind="cookies">Manage signed-in accounts</button></div></section>`; }).join('');
  return layout('Settings', settingsLede, `${pageTabs('settings')}<div class="provider-summary-grid">${cards}</div><div class="inline-note settings-note">${icon('shield-check')}<span>Secrets stay encrypted. Open a manager only when you need to test, reorder, add, or remove an entry.</span></div>`);
}

const usageLede = 'Traffic, timing and stored responses for your API requests.';
async function requestHistory() {
  const data = await api('/portal/api/history?limit=200');
  const queryPreview = query => { const limit=180, shortened=query.length > limit; return `<span class="history-query">${esc(query.slice(0,limit))}${shortened ? `…<span class="sr-only"> Query shortened; view details for the complete request</span>` : ''}</span>`; };
  return layout('Usage', usageLede, `${pageTabs('history')}
    <div class="table-caption"><span>Latest ${plural(data.requests.length, 'request')} · ${PORTAL.role === 'owner' ? 'Workspace' : 'Your account'}</span><span>Times shown in ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}</span></div>
    ${data.requests.length ? tableRegion('API request history', `<table><thead><tr><th scope="col">Time</th><th scope="col">Authentication</th><th scope="col">Endpoint</th><th scope="col">Response</th><th scope="col">Duration</th><th scope="col">Source</th><th scope="col">Actions</th></tr></thead><tbody>${data.requests.map(request => { const time=fmt(request.created_at); return `<tr class="history-row"><td>${time}</td><td>${esc(request.prefix || 'Signed-in dashboard session')}</td><td><div class="route-identity">${platformIcon(platformFromPath(request.path))}<span><code>${esc(request.method)} ${esc(request.path)}</code>${request.query ? queryPreview(request.query) : ''}</span></div></td><td><span class="badge ${request.status < 400 ? 'good' : 'bad'}">${request.status} ${request.status < 400 ? 'OK' : 'Error'}</span>${request.fallback ? ' <span class="badge">via ScrapeCreators</span>' : ''}</td><td>${number(request.duration_ms)} ms</td><td>${request.cached ? 'Cached' : 'Live'}</td><td class="history-action"><button class="icon-button" type="button" data-history-id="${request.id}" aria-label="View details for request at ${esc(time)}">${icon('arrow-right')}</button></td></tr>`; }).join('')}</tbody></table>`) : `<section class="card">${emptyState('clock', 'Your requests will appear here', 'Send a request in the playground to inspect its response and timing.', '<a class="primary" href="/app/playground">Open playground</a>', 2)}</section>`}`);
}

async function showHistoryDetail(id) {
  if (state.historyRequest?.id === id) return;
  const request = {id}; state.historyRequest = request;
  openDialog({title:'Request response', description:'Loading the stored response…', body:`<div class="history-detail-loading"><div class="history-facts" aria-hidden="true">${Array.from({length:3}, () => '<div><div class="skeleton sk-label"></div><div class="skeleton sk-line"></div></div>').join('')}</div><div class="response-skeleton history-response" aria-hidden="true">${'<span class="skeleton sk-line"></span>'.repeat(8)}</div></div>`, footer:'<button class="ghost" data-dialog-close>Close</button>'});
  const dialog=$('#dialog'), body = $('#dialog .modal-body'); dialog.classList.add('history-dialog'); body.setAttribute('aria-busy','true');
  $('#loading-status').textContent = 'Loading stored response…';
  try {
    const detail = await api(`/portal/api/history/${id}`);
    if (state.historyRequest !== request || !dialog.open) return;
    let formatted = detail.response || '';
    try { ResponsePreview.parseLossless(formatted); formatted = ResponsePreview.indent(formatted.trim()); } catch {}
    const route = detail.path + (detail.query ? `?${detail.query}` : ''), hasBody=(detail.has_response ?? Boolean(detail.response)) && Boolean(detail.response);
    const overlay = body.querySelector('.history-detail-loading'), content = document.createElement('div');
    const response = hasBody ? '<pre class="code history-response" id="history-response" tabindex="0"></pre>' : emptyState('file-text', detail.response_pruned ? 'Stored response cleared' : 'Response body was empty', detail.response_pruned ? 'Responses are kept for 14 days; the request details above remain available.' : 'This request completed without a response body.', '', 3);
    content.innerHTML = `<dl class="history-facts"><div><dt>Time</dt><dd>${esc(fmt(detail.created_at))}</dd></div><div><dt>Authentication</dt><dd>${esc(detail.prefix || 'Signed-in dashboard session')}</dd></div><div><dt>Request</dt><dd><code>${esc(route)}</code></dd></div></dl>${detail.response_truncated ? '<div class="inline-note">The stored response was capped at 2 MiB.</div>' : ''}${response}`;
    if (hasBody) content.querySelector('pre').textContent = formatted;
    $('#dialog-title').textContent = `${detail.method} ${detail.path}`;
    $('#dialog-description').textContent = `${detail.status} · ${number(detail.duration_ms)} ms · ${detail.cached ? 'Cached' : 'Live'}`;
    body.prepend(content); body.setAttribute('aria-busy','false'); overlay.classList.add('loading-overlay'); reveal(content, overlay);
    if (hasBody) $('#dialog .modal-foot').insertAdjacentHTML('beforeend', `<button class="primary" data-copy="history-response">${icon('copy')}Copy response</button>`);
    $('#loading-status').textContent = `Stored response loaded. Status ${detail.status}.`;
  } catch (error) {
    if (state.historyRequest !== request || !dialog.open) return;
    $('#dialog-description').textContent = 'The stored response could not load.';
    body.querySelector('.history-detail-loading').innerHTML = `<div class="history-response"><div class="form-error" role="alert">${esc(error.message)}</div><button class="ghost" data-history-retry="${esc(id)}">${icon('arrows-clockwise')}Try again</button></div>`;
    body.setAttribute('aria-busy','false'); $('#loading-status').textContent = 'Stored response could not load. Try again when you are ready.';
  } finally { if (state.historyRequest === request) state.historyRequest = null; }
}

async function usage() {
  const data = await loadUsage();
  const total = data.daily.reduce((sum, day) => sum + day.total, 0), max = Math.max(...data.daily.map(day => day.total), 1);
  const labels = data.daily.map((_, index) => { const date = new Date(); date.setDate(date.getDate() - 29 + index); return date.toLocaleDateString(undefined, {month:'short', day:'numeric'}); });
  const series = [['instagram','Instagram'],['tiktok','TikTok'],['youtube','YouTube'],['other','Other']];
  const legend = `<ul class="chart-legend" aria-label="Platforms">${series.map(([key,label]) => `<li><span class="legend-swatch ${key}" aria-hidden="true"></span>${label}</li>`).join('')}</ul>`;
  const chart = data.daily.map(day => `<div class="bar-stack" style="height:${Math.max(1,day.total/max*100)}%">${series.map(([key]) => day[key] ? `<span class="bar-segment ${key}" style="height:${day[key]/day.total*100}%"></span>` : '').join('')}</div>`).join('');
  return layout('Usage', usageLede, `${pageTabs('usage')}
    <section class="card"><div class="card-head"><div><h2>Request volume</h2><span class="chart-summary">${plural(total, 'request')} · ${PORTAL.role === 'owner' ? 'Workspace' : 'Your account'} · Last 30 days${data.fallback_count > 0 ? ` · ${number(data.fallback_count)} via ScrapeCreators` : ''}</span></div></div>
    ${total ? `${legend}<p class="sr-only">Stacked daily requests by platform. Use View daily data for exact Instagram, TikTok, YouTube, Other, and total counts.</p><div class="chart" aria-hidden="true">${chart}</div><div class="chart-labels"><span>${labels[0]}</span><span>${labels[14]}</span><span>${labels[29]}</span></div>` : emptyState('chart-bar', 'No requests in this period', 'Usage appears here when you call an API endpoint.', '<a class="ghost" href="/app/playground">Make your first request</a>')}
    <details><summary>${icon('chart-bar')}View daily data</summary><p class="help">Rolling 24-hour periods ending now. Date labels use your local timezone.</p>${tableRegion('Daily request counts by platform', `<table><thead><tr><th scope="col">Period ending</th>${series.map(([,label])=>`<th scope="col">${label}</th>`).join('')}<th scope="col">Total</th></tr></thead><tbody>${data.daily.map((day,index) => `<tr><td>${labels[index]}</td>${series.map(([key])=>`<td>${number(day[key])}</td>`).join('')}<td><strong>${number(day.total)}</strong></td></tr>`).join('')}</tbody></table>`)}</details></section>
    <section class="card" style="margin-top:24px"><h2>Most used endpoints</h2><p class="help">Last 30 days · Up to 10 routes</p>${data.routes.map(route => `<div class="param route-row"><strong class="route-identity">${platformIcon(platformFromPath(route.path))}<code>${esc(route.path)}</code></strong><span>${plural(route.count, 'request')}</span></div>`).join('') || emptyState('globe', 'No endpoint activity yet', 'Run a request to see your most used routes.')}</section>`);
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
const playgroundLede = 'Try one endpoint at a time, or test them all at once.';
async function playground() {
  const [schema] = await Promise.all([spec(), loadKeys()]);
  const all = endpoints(schema), groups = [...new Set(all.map(endpoint => endpoint.platform))];
  const linked = new URLSearchParams(location.search).get('endpoint'), linkedEndpoint = all.find(endpoint => endpoint.path === linked);
  if (linkedEndpoint) { state.endpoint = linkedEndpoint.path; state.platform = linkedEndpoint.platform; }
  const selected = groups.includes(state.platform) ? state.platform : groups[0];
  return layout('Playground', playgroundLede, `${pageTabs('playground')}
    <div class="playground"><section class="card playground-config"><h2 class="section-label">Configure request</h2><form class="form-stack" id="request-form" novalidate><div class="field"><span id="platform-label">Platform</span><input type="hidden" id="pg-platform" value="${selected}"><button type="button" class="platform-trigger" id="platform-trigger" popovertarget="platform-menu" aria-haspopup="listbox" aria-expanded="false" aria-controls="platform-menu" aria-labelledby="platform-label platform-selected"><span id="platform-selected">${platformLabel(selected)}</span>${icon('caret-down')}</button><div id="platform-menu" class="platform-menu" popover="auto" role="listbox" aria-labelledby="platform-label">${groups.map(group => `<button type="button" class="platform-option" role="option" data-platform-choice="${group}" aria-selected="${selected === group}" tabindex="-1">${platformLabel(group)}${icon('check')}</button>`).join('')}</div></div><label class="field">Endpoint<select id="pg-endpoint"></select></label><label class="field">Authentication<select id="pg-key"><option value="">Signed-in dashboard session</option>${state.keys.filter(key => !key.revoked_at).map(key => `<option value="${key.id}" ${String(key.id) === state.selectedKey ? 'selected' : ''}>${esc(key.name)} · ${esc(key.prefix)}</option>`).join('')}</select><span class="help">This selection affects only the in-dashboard run. Copied snippets require an API key.</span></label><div id="pg-params"></div><div class="inline-note" id="endpoint-note" hidden></div><div class="field-error" id="request-error" role="alert" hidden></div><div class="request-wait" id="request-wait" role="status" hidden></div><button class="primary" type="submit" id="run-request">${icon('play')}Run request</button></form></section>
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
  $('#pg-endpoint').innerHTML = available.map(endpoint => `<option value="${esc(endpoint.path)}" ${state.endpoint === endpoint.path ? 'selected' : ''}>${esc(endpoint.op.tags?.includes('not supported') ? (endpoint.op.summary || endpoint.path).replace(/ \(not supported\)$/i, '') : (endpoint.op.summary || endpoint.path))}${endpoint.op.tags?.includes('not supported') ? ' · Not supported' : ''}</option>`).join('');
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
    const schema = normalizeSchema(param), type = schema.type;
    const label = `<span class="field-label"><code>${esc(param.name)}</code>${param.required ? '<span class="badge required">Required</span>' : '<span class="help">Optional</span>'}</span>`;
    const value = state.drafts[state.endpoint]?.[param.name] || '';
    const options = schema.enum || (type === 'boolean' ? ['true', 'false'] : null);
    const hint = param.name === 'url' ? 'Enter the full post, video, channel, or profile link, starting with https://.' : param.description || (type === 'integer' ? 'Enter a whole number.' : '');
    const constraints = `${schema.minimum !== undefined ? ` min="${esc(schema.minimum)}"` : ''}${schema.maximum !== undefined ? ` max="${esc(schema.maximum)}"` : ''}${schema.minLength !== undefined ? ` minlength="${esc(schema.minLength)}"` : ''}${schema.maxLength !== undefined ? ` maxlength="${esc(schema.maxLength)}"` : ''}${schema.pattern ? ` pattern="${esc(schema.pattern)}"` : ''}`;
    const control = options ? `<select id="param-${index}" data-param="${esc(param.name)}" ${param.required ? 'required' : ''} aria-describedby="param-hint-${index}"><option value="">${param.required ? 'Choose a value' : 'Default'}</option>${options.map(option => `<option value="${esc(option)}" ${String(option) === value ? 'selected' : ''}>${esc(option)}</option>`).join('')}</select>` : `<input id="param-${index}" data-param="${esc(param.name)}" type="${param.name === 'url' ? 'url' : type === 'integer' || type === 'number' ? 'number' : 'text'}" ${type === 'integer' ? 'step="1"' : ''}${constraints} ${param.required ? 'required' : ''} value="${esc(value)}" placeholder="${esc(schema.default ?? (param.name === 'url' ? 'https://…' : ''))}" aria-describedby="param-hint-${index}">`;
    return `<label class="field" for="param-${index}">${label}${control}<span class="help" id="param-hint-${index}">${esc(hint)}</span></label>`;
  };
  const required = parameters.filter(param => param.required);
  const optional = parameters.filter(param => !param.required);
  $('#pg-params').innerHTML = required.map(renderParam).join('') + (optional.length ? `<details class="advanced"><summary>${icon('caret-down')}Optional parameters <span class="badge">${optional.length}</span></summary><div class="parameter-fields">${optional.map((param, index) => renderParam(param, required.length + index)).join('')}</div></details>` : '');
  const unsupported = operation.tags?.includes('not supported');
  $('#endpoint-note').hidden = !unsupported;
  $('#endpoint-note').innerHTML = `${icon('warning-circle')}<span>This endpoint is part of the compatibility API but is not implemented. Running it returns 501. <a href="/app/docs#${encodeURIComponent(state.endpoint)}">View details</a></span>`;
  $('#request-error').hidden = true;
  const run=$('#run-request'); run.innerHTML = unsupported ? `${icon('play')}Send test request (returns 501)` : `${icon('play')}Run request`;
  if (state.request?.path === state.endpoint) paintRequest();
  else {
    $('#response').innerHTML = emptyState('compass', 'Your response starts here', 'Choose an endpoint and run a request to see the result.');
    $('#response').setAttribute('aria-busy','false');
    $('#response-status').className = 'badge'; $('#response-status').textContent = 'Ready';
    $('[data-copy="response-raw"]').hidden = true;
  }
  updateSnippet();
}
function clearParamErrors(input) {
  const fields = input ? [input] : [...document.querySelectorAll('[data-param]')];
  fields.forEach(field => {
    field.removeAttribute('aria-invalid');
    const id = `param-error-${field.id.slice(6)}`, error = document.getElementById(id);
    if (error) error.remove();
    field.setAttribute('aria-describedby', [...new Set((field.getAttribute('aria-describedby') || '').split(/\s+/).filter(item => item && item !== id))].join(' '));
  });
}
function paramError(input, message) {
  const id = `param-error-${input.id.slice(6)}`;
  let error = document.getElementById(id);
  if (!error) { error=document.createElement('span'); error.id=id; error.className='field-error param-error'; error.setAttribute('role','alert'); input.parentElement.append(error); }
  error.textContent=message; input.setAttribute('aria-invalid','true');
  input.setAttribute('aria-describedby', [...new Set(`${input.getAttribute('aria-describedby') || ''} ${id}`.trim().split(/\s+/))].join(' '));
}
function validateParams() {
  clearParamErrors(); let first;
  document.querySelectorAll('[data-param]').forEach(input => {
    let message='';
    if (!input.checkValidity()) message = input.validity.valueMissing ? 'Enter a value before sending the request.' : input.dataset.param === 'url' ? 'Enter a full link, starting with https://.' : input.validationMessage;
    if (input.dataset.param === 'url' && input.value) { try { if (new URL(input.value).protocol !== 'https:') message='Enter a full link, starting with https://.'; } catch { message='Enter a full link, starting with https://.'; } }
    if (message) { paramError(input,message); first ||= input; }
  });
  first?.focus(); return !first;
}
function mapResponseErrors(request) {
  if (request.status !== 400 || request.text === undefined) return;
  let envelope; try { envelope=JSON.parse(request.text); } catch { return; }
  const fields=[...document.querySelectorAll('[data-param]')], messages=new Map();
  if (envelope.error === 'unsupported_url') messages.set('url', envelope.message || 'Enter a URL supported by this endpoint.');
  String(envelope.message || '').split(';').map(value=>value.trim()).forEach(fragment => {
    const field=fields.find(input=>fragment.startsWith(`${input.dataset.param}:`));
    if (field) messages.set(field.dataset.param, fragment.slice(field.dataset.param.length + 1).trim());
  });
  let first;
  messages.forEach((message,name) => { const field=fields.find(input=>input.dataset.param === name); if (field) { paramError(field,message); first ||= field; } });
  first?.focus();
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
    const retained = (request.error || request.stopped) && request.previous;
    if (retained) {
      if (!area.querySelector('.previous-response')) area.insertAdjacentHTML('afterbegin', '<p class="previous-response"></p>');
      area.querySelector('.previous-response').textContent = 'No new response was received; your previous response is shown below.';
    }
    if (skeleton && !request.error && !request.stopped) { skeleton.classList.add('loading-overlay'); area.append(skeleton); reveal(content, skeleton); }
    copy.hidden = false;
    const copyName=retained ? 'Copy previous raw JSON response' : 'Copy raw JSON response'; copy.setAttribute('aria-label',copyName); copy.title=copyName;
  } else { area.innerHTML = emptyState('globe', 'No response received', 'Check your connection and run the request again. Your request fields are preserved.'); copy.hidden = true; }
  badge.className = `badge ${request.error || !request.ok ? 'bad' : 'good'}`;
  badge.textContent = request.error ? 'Connection failed' : `${request.status} ${request.ok ? 'OK' : 'Error'} · ${number(request.duration)} ms`;
  if (request.error) showError(Error(request.error), '#request-error');
}
async function runRequest() {
  if (state.request?.pending || !validateParams()) return;
  updateSnippet(); clearParamErrors();
  const query = params(), path = $('#pg-endpoint').value, keyId = $('#pg-key').value;
  const last = state.request?.path === path ? state.request : null;
  const previous = last?.text !== undefined ? {path:last.path,status:last.status,text:last.text,view:last.view} : last?.previous;
  const request = {path, pending:true, releases:new Set(), previous, started:performance.now(), controller:new AbortController()};
  state.request = request; $('#request-error').hidden = true; $('#request-wait').hidden = true;
  $('#platform-menu')?.hidePopover(); paintRequest();
  $('#loading-status').textContent = 'Request running. Waiting for the API response.';
  const elapsed = () => Math.max(10, Math.round((performance.now() - request.started) / 1000));
  let ticker;
  const slow = setTimeout(() => { if (state.request !== request || !request.pending) return; request.slow = true; const wait=$('#request-wait'); wait.hidden=false; wait.innerHTML=`<span><span class="request-elapsed" aria-hidden="true">Waiting ${elapsed()} seconds. </span>Request is taking longer than expected. Stopping ends this browser wait but may not stop upstream work.</span><button class="ghost" type="button" data-stop-request>Stop waiting</button>`; ticker=setInterval(()=>{ if (state.request === request && request.pending) wait.querySelector('.request-elapsed').textContent=`Waiting ${elapsed()} seconds. `; },1000); paintRequest(); }, 10000);
  try {
    const response = await fetch(path + (query.size ? '?' + query : ''), {headers:{'x-portal-key-id':keyId}, signal:request.controller.signal});
    if (state.request !== request) return;
    request.text = await response.text(); request.status = response.status; request.ok = response.ok;
    request.duration = Math.round(performance.now() - request.started);
    $('#loading-status').textContent = `Request finished with status ${response.status}.`;
  } catch (error) {
    if (state.request !== request) return;
    if (error.name === 'AbortError') { request.stopped=true; $('#loading-status').textContent='Stopped waiting. Your request fields and previous response are preserved.'; }
    else { request.error = 'Could not read the API response. Check your connection, then run the request again. Your request fields are preserved.'; $('#loading-status').textContent = 'The response could not be received. Your request fields are preserved.'; }
  } finally {
    clearTimeout(slow); clearInterval(ticker);
    if (state.request !== request) return;
    request.pending = false; $('#request-wait').hidden = true;
    for (const done of request.releases) done(); request.releases.clear(); paintRequest();
    mapResponseErrors(request);
    if (request.stopped) { const badge=$('#response-status'); badge.className='badge'; badge.textContent='Stopped waiting'; $('#run-request').focus(); }
  }
}

function safeDecodeHash(hash) {
  try { return {ok:true, value:decodeURIComponent(String(hash).replace(/^#/,''))}; } catch { return {ok:false, value:String(hash).replace(/^#/,'')}; }
}
function docSearchText(endpoint) {
  const operation = endpoint.op, values = [endpoint.path, operation.summary, operation.description, ...(operation.tags || [])];
  for (const param of operation.parameters || []) { const schema=normalizeSchema(param); values.push(param.name,param.description,...(schema.enum || [])); }
  values.push(...Object.values(operation.responses || {}).map(response => response.description));
  return values.filter(value => value !== undefined && value !== null).join(' ').toLowerCase();
}
async function docs() {
  const schema = await spec(), all = endpoints(schema), groups = [...new Set(all.map(endpoint => endpoint.platform))];
  return `<div class="docs"><aside class="docs-nav" aria-label="API reference"><details class="docs-disclosure"${innerWidth > 760 ? ' open' : ''}><summary>Browse endpoints</summary><div class="docs-nav-content"><label class="field"><span class="sr-only">Search endpoints</span><input type="search" class="search-input" id="doc-search" placeholder="Search endpoints…"></label><span class="sr-only" id="docs-search-status" role="status" aria-live="polite" aria-atomic="true"></span><a class="docs-link" href="/app/docs" data-doc-intro>Introduction</a>${groups.map((group,index) => `<div class="docs-group" data-group><span class="docs-group-label" id="docs-group-${index}">${platformLabel(group)}</span><div role="group" aria-labelledby="docs-group-${index}">${all.filter(endpoint => endpoint.platform === group).map(endpoint => `<a class="docs-link" data-doc="${esc(docSearchText(endpoint))}" data-doc-path="${esc(endpoint.path)}" href="/app/docs#${encodeURIComponent(endpoint.path)}"><span class="method">GET</span><span>${esc(endpoint.op.summary || endpoint.path)}</span><span class="docs-current-note" hidden>Current · outside search</span></a>`).join('')}</div></div>`).join('')}<p id="docs-no-results" class="help" hidden>No matching endpoints. Try another search.</p></div></details></aside><article class="doc-content" id="doc-main"></article></div>`;
}
let docsSearchTimer;
function filterDocs(query, {announce = false, immediate = false} = {}) {
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean), current = document.querySelector('[data-doc-path][aria-current="page"]'); let matches = 0;
  document.querySelectorAll('[data-doc]').forEach(link => {
    const match = terms.every(term => link.dataset.doc.includes(term)), outside = link === current && terms.length && !match;
    link.hidden = !match && !outside; link.querySelector('.docs-current-note').hidden = !outside; if (match) matches++;
  });
  document.querySelectorAll('[data-group]').forEach(group => { group.hidden = !group.querySelector('[data-doc]:not([hidden])'); });
  $('#docs-no-results').hidden = matches > 0;
  if (announce) { clearTimeout(docsSearchTimer); const update=()=>{ const status=$('#docs-search-status'); if (status) status.textContent=matches ? `${plural(matches,'endpoint')} found` : 'No matching endpoints'; }; if (immediate) update(); else docsSearchTimer=setTimeout(update,200); }
}
function selectDoc({focus = false} = {}) {
  const decoded = safeDecodeHash(location.hash), path = decoded.value, hasHash = location.hash.length > 1;
  if (!hasHash) showIntro(); else if (decoded.ok && state.spec.paths[path]?.get) showEndpoint(path); else showDocNotFound(path);
  document.querySelectorAll('.docs-link').forEach(link => {
    const linkPath = safeDecodeHash(link.hash).value, current = hasHash ? link.hasAttribute('data-doc-path') && linkPath === path : link.hasAttribute('data-doc-intro');
    if (current) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  filterDocs(state.docSearch);
  const title = $('#doc-main h1')?.textContent || 'Documentation'; document.title = `${title} · Social Scrape`;
  if (focus) {
    if (innerWidth <= 760) $('.docs-disclosure')?.removeAttribute('open');
    const heading=$('#doc-main h1'); heading?.setAttribute('tabindex','-1'); heading?.focus({preventScroll:true}); heading?.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  }
}
function showIntro() {
  $('#doc-main').innerHTML = `<div class="eyebrow">Developer reference</div><h1>Social Scrape API</h1><p class="lede">Public social data through one private API.</p><div class="platforms doc-platforms">${platformList()}</div><section class="card"><h2>Start with a request</h2><p class="help">Send a member API key in the <code>x-api-key</code> header. Create and revoke credentials from API keys.</p><pre class="code" tabindex="0">curl '${esc(location.origin)}/v1/tiktok/profile?handle=tiktok' \\
  -H 'x-api-key: YOUR_API_KEY'</pre><a class="ghost" style="margin-top:20px" href="/app/playground">Open playground ${icon('arrow-right')}</a></section><h2>Authentication</h2><p class="muted">Use a separate key for each person or integration. Secrets are shown once when created, and can be revoked independently.</p><h2>Response format</h2><p class="muted">Successful calls include <code>success: true</code> and <code>credits_charged: 0</code>. Errors include an error code and a message explaining what happened.</p><h2>Caching</h2><p class="muted">Set <code>cache_max_age</code> to <code>12h</code>, <code>1d</code>, or <code>7d</code> to accept a cached response. Leave it out to fetch fresh data.</p><h2>Endpoint availability</h2><p class="muted">The reference includes the full compatibility API. Routes outside the supported extraction boundary return <code>501</code> with a reason. Platform restrictions may require cookies or a proxy.</p>`;
}
function shellQuote(value) { return `'${String(value).replace(/'/g, `'\\''`)}'`; }
function docRequestUrl(path, parameters) {
  const url=new URL(path,location.origin), query=new URLSearchParams();
  for (const param of parameters.filter(param => param.in === 'query' && param.required)) { const schema=normalizeSchema(param), value=param.example ?? schema.example ?? schema.default ?? `YOUR_${String(param.name || schema.type || 'VALUE').toUpperCase().replace(/\W+/g,'_')}`; query.append(param.name,value); }
  url.search=query.toString(); return url.href;
}
function parameterType(schema) { return schema.type === 'array' ? `array of ${schema.items?.type || 'values'}` : schema.type || 'value'; }
function parameterMetadata(param) {
  const schema=normalizeSchema(param), items=[];
  if (schema.enum?.length) items.push(`<span><b>Choices:</b> ${schema.enum.map(value => `<code>${esc(value)}</code>`).join(', ')}</span>`);
  if (schema.default !== undefined) items.push(`<span><b>Default:</b> <code>${esc(schema.default)}</code></span>`);
  const example=param.example ?? schema.example; if (example !== undefined) items.push(`<span><b>Example:</b> <code>${esc(example)}</code></span>`);
  if (schema.format) items.push(`<span><b>Format:</b> ${esc(schema.format)}</span>`);
  const labels={minimum:'Minimum',maximum:'Maximum',minLength:'Minimum length',maxLength:'Maximum length',pattern:'Pattern'};
  for (const [key,label] of Object.entries(labels)) if (schema[key] !== undefined) items.push(`<span><b>${label}:</b> <code>${esc(schema[key])}</code></span>`);
  if (schema.items?.enum?.length) items.push(`<span><b>Item choices:</b> ${schema.items.enum.map(value => `<code>${esc(value)}</code>`).join(', ')}</span>`);
  return items.length ? `<span class="param-meta">${items.join('')}</span>` : '';
}
function showEndpoint(path) {
  const operation = state.spec.paths[path].get, parameters = operation.parameters || [], unsupported = operation.tags?.includes('not supported'), command=`curl ${shellQuote(docRequestUrl(path,parameters))} \\\n  -H 'x-api-key: YOUR_API_KEY'`;
  $('#doc-main').innerHTML = `<div class="eyebrow platform-heading">${platformLabel(platformFromPath(path))} API</div><div class="endpoint-title"><span class="method">GET</span><h1>${esc(operation.summary || path)}</h1></div><p class="lede"><code>${esc(path)}</code></p>${unsupported ? '<div class="inline-note"><span class="badge warn">Not supported</span><span>This compatibility route is not implemented through yt-dlp. Requests return 501.</span></div>' : ''}${unsupported ? '<p class="help">This route cannot run in the playground.</p>' : `<a class="ghost docs-playground-link" href="/app/playground?endpoint=${encodeURIComponent(path)}">Open in playground ${icon('arrow-right')}</a>`}<section class="card"><div class="card-head"><h2>Request</h2><button class="icon-button" data-copy="doc-snippet" aria-label="Copy request snippet">${icon('copy')}</button></div><pre class="code" id="doc-snippet" tabindex="0">${esc(command)}</pre></section><h2>Parameters</h2>${parameters.map(param => { const schema=normalizeSchema(param); return `<div class="param"><strong><code>${esc(param.name)}</code>${param.required ? '<span class="badge required">Required</span>' : '<span class="help">Optional</span>'}<span class="help">${esc(parameterType(schema))}</span></strong>${param.description ? `<small>${esc(param.description)}</small>` : ''}${parameterMetadata(param)}</div>`; }).join('') || '<p class="muted">No parameters.</p>'}<h2>Responses</h2>${Object.entries(operation.responses || {}).map(([code, response]) => `<div class="param"><strong><span class="badge ${Number(code) < 400 ? 'good' : ''}">${esc(code)}</span>${esc(response.description)}</strong></div>`).join('')}`;
}
function showDocNotFound(path) {
  $('#doc-main').innerHTML=`<div class="eyebrow">Developer reference</div><h1>Endpoint not found</h1><p class="lede">No documented endpoint matches <code>${esc(path || location.hash.slice(1))}</code>.</p><section class="card"><h2>Find an endpoint</h2><p class="help">Search the endpoint list or return to the API introduction.</p><div class="row"><button class="ghost" data-focus-doc-search>Search endpoints</button><a class="text-link" href="/app/docs">Back to introduction</a></div></section>`;
}

let dialogTrigger, dialogGuard, dialogRestoreFocus;
function closeDialog({force = false} = {}) { if (!force && dialogGuard && !dialogGuard()) return false; $('#dialog').close(); return true; }
function openDialog({title, description, body, footer, guard = null, restoreFocus = null, initialFocus = null}) {
  const dialog = $('#dialog'), alreadyOpen = dialog.open;
  if (!alreadyOpen) dialogTrigger = document.activeElement;
  dialogGuard = guard; dialogRestoreFocus = restoreFocus;
  dialog.innerHTML = `<header class="modal-head"><div><h2 id="dialog-title">${esc(title)}</h2><p id="dialog-description">${esc(description)}</p></div><button class="icon-button" data-dialog-close aria-label="Close dialog">${icon('x')}</button></header><div class="modal-body">${body}<div class="field-error" id="dialog-error" role="alert" hidden></div></div><footer class="modal-foot">${footer}</footer>`;
  if (!alreadyOpen) dialog.showModal();
  const focusTarget = typeof initialFocus === 'function' ? initialFocus() : initialFocus ? dialog.querySelector(initialFocus) : alreadyOpen ? dialog.querySelector('[data-dialog-close]') : null;
  focusTarget?.focus({preventScroll:true});
}
function showSecret(title, description, value, isSecret) {
  if (!isSecret) { delete $('#dialog').dataset.oneTimeSecret; openDialog({title, description, body:`<label class="field">Invitation link<textarea class="secret" id="new-secret" readonly spellcheck="false">${esc(value)}</textarea></label><p class="help" id="copy-feedback" style="margin-top:12px">Share this link privately with the invited person.</p>`, footer:`<button class="ghost" data-dialog-close>Done</button><button class="primary" data-copy="new-secret">${icon('copy')}Copy link</button>`}); return; }
  let saved = false, warning = false;
  const body = `<label class="field">API key<textarea class="secret" id="new-secret" readonly spellcheck="false">${esc(value)}</textarea></label><p class="help" id="copy-feedback" style="margin-top:12px">Keep this key private. It will not be shown again.</p>`;
  const showWarning = () => { if (!warning) { warning = true; openDialog({title:'Save this API key before closing', description:'This key cannot be shown again after you close this dialog.', body:'<p class="help">Go back to copy the key, or confirm that you saved it somewhere secure.</p>', footer:'<button class="ghost" id="back-to-key">Back to key</button><button class="danger" id="confirm-key-saved">I saved this key</button>', guard}); $('#back-to-key').addEventListener('click', () => { warning=false; open(); }); $('#confirm-key-saved').addEventListener('click', () => { saved=true; closeDialog({force:true}); }); } return false; };
  const guard = () => saved || showWarning();
  const open = () => openDialog({title, description, body, footer:`<button class="ghost" data-dialog-close>Done</button><button class="primary" data-copy="new-secret">${icon('copy')}Copy API key</button>`, guard});
  open();
  $('#dialog').dataset.oneTimeSecret = 'true';
  $('#dialog').addEventListener('secretcopied', () => { saved = true; }, {once:true});
}
function successorResolver(button, selector) {
  const row=button.closest('tr'), rows=[...row.parentElement.children], index=rows.indexOf(row);
  const descriptor = candidate => { const action=candidate?.querySelector(selector); return action ? [selector, Object.values(action.dataset)[0]] : null; };
  const next=descriptor(rows[index+1]), previous=descriptor(rows[index-1]);
  return () => { for (const item of [next,previous]) { if (!item) continue; const found=[...document.querySelectorAll(item[0])].find(action=>Object.values(action.dataset).includes(item[1])); if (found) return found; } return $('#view .table-caption h2, #view h1'); };
}
function confirmAction(title, description, confirmLabel, action, {restoreFocus, staleMessage} = {}) {
  openDialog({title, description, body:'<p class="help">This takes effect immediately for the selected account or credential.</p>', footer:`<button class="ghost" data-dialog-close autofocus>Cancel</button><button class="danger" id="confirm-action">${esc(confirmLabel)}</button>`});
  $('#confirm-action').addEventListener('click', async event => {
    const button = event.currentTarget, done = pendingButton(button, confirmLabel.startsWith('Revoke') ? 'Revoking…' : 'Removing…'); if (!done) return;
    try {
      const data = await action();
      if ((confirmLabel === 'Revoke key' && data.revoked !== true) || (confirmLabel === 'Remove member' && data.removed !== true)) throw Object.assign(Error(staleMessage), {status:404});
      await render(); dialogRestoreFocus=restoreFocus; closeDialog({force:true}); toast(({'Revoke key':'API key revoked.','Remove member':'Member removed.','Remove proxy':'Saved proxy removed.','Remove account':'Saved account removed.','Remove fallback key':'ScrapeCreators API key removed.'})[confirmLabel] || 'Change saved.');
    } catch (error) {
      if (error.status === 404 && staleMessage) { await render(); dialogRestoreFocus=restoreFocus; closeDialog({force:true}); toast(staleMessage, {type:'info'}); }
      else showError(error, '#dialog-error');
    } finally { done(); }
  });
}
async function copyElement(id) {
  const element = document.getElementById(id); if (!element) return;
  const text = element.value ?? element.textContent;
  try { await navigator.clipboard.writeText(text); if (id === 'new-secret' && $('#dialog').dataset.oneTimeSecret) $('#dialog').dispatchEvent(new Event('secretcopied')); toast('Copied to clipboard.'); }
  catch {
    if (id === 'new-secret') { element.focus(); element.select(); $('#copy-feedback').textContent = 'Copy is unavailable. Select and copy the text above manually.'; }
    else { if (element.hidden) document.querySelector(`[role=tab][aria-controls="${id}"]`)?.click(); const range=document.createRange(); range.selectNodeContents(element); window.getSelection().removeAllRanges(); window.getSelection().addRange(range); toast('Select and copy the highlighted text manually.', {type:'info'}); }
  }
}
function fieldError(input, message) {
  const target = document.getElementById(`${input.id}-error`); input.setAttribute('aria-invalid','true'); target.textContent=message; target.hidden=false; input.focus();
}
async function submitCreation(form) {
  const input = form.id === 'key-form' ? $('#key-name') : $('#invite-email'), error = document.getElementById(`${input.id}-error`);
  input.removeAttribute('aria-invalid'); error.hidden=true; $('#action-error').hidden=true;
  if (form.id === 'key-form' && !input.value.trim()) { fieldError(input, 'Enter a name for this API key.'); return; }
  const button=form.querySelector('button[type=submit]'), done=pendingButton(button, form.id === 'key-form' ? 'Creating key…' : 'Creating invitation…'); if (!done) return;
  try {
    if (form.id === 'key-form') { const data=await api('/portal/api/keys',{method:'POST',body:{name:input.value.trim()}}); await render(); showSecret('Your API key is ready','Save this secret now. It is only shown once.',data.key,true); }
    else { const data=await api('/portal/api/invites',{method:'POST',body:{email:input.value.trim()}}); showSecret('Invitation created','This invitation link expires in 7 days.',data.url,false); form.reset(); }
  } catch (errorValue) { if ([409,422].includes(errorValue.status)) fieldError(input,errorValue.message); else showError(errorValue); }
  finally { done(); }
}

async function testPlatformProxy(button) {
  const done = pendingButton(button, 'Testing…');
  if (!done) return;
  try {
    const result = await api(`/portal/api/settings/platforms/${button.dataset.platform}/proxy/${button.dataset.entry}/test`, {method:'POST', timeout:25000});
    const detail = result.ok ? `Working · ${result.exit_ip || 'exit IP unavailable'} · ${number(result.latency_ms)} ms` : `${result.blocked ? 'Blocked by provider' : 'Connection failed'}${result.status ? ` · HTTP ${result.status}` : ''} · ${number(result.latency_ms)} ms`;
    const status = button.closest('li').querySelector('[data-proxy-status]'); status.textContent = detail; toast(result.ok ? 'Proxy test completed.' : 'Proxy test failed.', {type:result.ok ? 'success' : 'error'});
  } catch (error) { const message = error.timeout ? 'Proxy test timed out after 25 seconds. Check the proxy and try again.' : error.message; button.closest('li').querySelector('[data-proxy-status]').textContent=message; toast(message, {type:'error', persistent:true}); }
  finally { done(); }
}

async function savePlatformSetting(form) {
  const button = form.querySelector('button[type=submit]'), error = form.querySelector('.field-error'), input = form.querySelector('input');
  const platform = form.dataset.platform, kind = form.dataset.kind;
  const done = pendingButton(button, kind === 'proxy' ? 'Saving proxy…' : 'Saving signed-in account…');
  if (!done) return;
  error.hidden = true; input.removeAttribute('aria-invalid');
  try {
    let value;
    if (kind === 'cookies') {
      const file = form.elements.file.files[0];
      if (!file) throw Object.assign(Error('Choose a cookies.txt file to add this signed-in account.'), {field:true});
      if (file.size > 2 * 1024 * 1024) throw Object.assign(Error('Choose a cookies.txt file no larger than 2 MiB.'), {field:true});
      value = await file.text();
    } else { value = form.elements.value.value.trim(); if (!value) throw Object.assign(Error('Enter a complete proxy URL.'), {field:true}); }
    const data = await api(`/portal/api/settings/platforms/${platform}/${kind}`, {method:'POST', body:{value}});
    await openPlatformManager(platform, kind, dialogTrigger, managerFocus(data.id, '[data-move-setting], [data-remove-setting], [data-test-proxy]'));
    toast(`${kind === 'proxy' ? 'Proxy' : 'Signed-in account'} saved for ${platformName(platform)}.`);
  } catch (err) { error.textContent = err.message; error.hidden = false; if (err.field || [400,422].includes(err.status)) { input.setAttribute('aria-invalid','true'); input.focus(); } }
  finally { done(); }
}

async function movePlatformSetting(button) {
  const done = pendingButton(button, 'Updating order…', true);
  if (!done) return;
  const {platform, entry, direction, moveSetting:kind} = button.dataset;
  try {
    const error = $('#dialog #manager-pool-error'); if (error) error.hidden = true;
    const data = await api(`/portal/api/settings/platforms/${platform}/${kind}/${entry}/move`, {method:'POST', body:{direction}});
    if (data.moved !== true) throw Object.assign(Error('The order did not change.'), {status:404});
    await openPlatformManager(platform, kind, dialogTrigger, managerFocus(entry, `[data-direction="${direction}"]`));
    toast('Order updated.');
  } catch (error) {
    if (error.status === 404) { await openPlatformManager(platform,kind,dialogTrigger,managerFocus(entry,`[data-direction="${direction}"]`)); toast('The order did not change. The current list has been refreshed.',{type:'info'}); }
    else { const target=$('#dialog #manager-pool-error'); if (target) { target.textContent=error.message; target.hidden=false; } else toast(error.message,{type:'error'}); }
  } finally { done(); }
}

function restoreManagerSnapshot(snapshot, focusEntry) {
  const dialog=$('#dialog'); dialog.innerHTML=snapshot.html; dialog.querySelector('.modal-body').scrollTop=snapshot.scrollTop; dialogGuard=null;
  dialog.querySelector(`[data-remove-setting][data-entry="${CSS.escape(String(focusEntry))}"]`)?.focus({preventScroll:true});
}
function removePlatformSetting(button) {
  const {removeSetting:kind, platform, entry} = button.dataset, body=$('#dialog .modal-body');
  const rows=[...$('#dialog').querySelectorAll('.manager-list li')], index=rows.indexOf(button.closest('li'));
  const next=rows[index+1]?.dataset.entry, previous=rows[index-1]?.dataset.entry;
  const snapshot={html:$('#dialog').innerHTML, scrollTop:body?.scrollTop || 0};
  const cancel=() => { restoreManagerSnapshot(snapshot,entry); return false; };
  openDialog({title:`Remove this ${platformName(platform)} ${kind === 'proxy' ? 'proxy' : 'signed-in account'}?`, description:'The remaining entries keep their current order.', body:'<p class="help">This takes effect immediately for this credential.</p>', footer:`<button class="ghost" data-dialog-close autofocus>Cancel</button><button class="danger" id="confirm-setting-removal">Remove ${kind === 'proxy' ? 'proxy' : 'signed-in account'}</button>`, guard:cancel});
  $('#confirm-setting-removal').addEventListener('click', async event => { const done=pendingButton(event.currentTarget,'Removing…'); if (!done) return; try { const data=await api(`/portal/api/settings/platforms/${platform}/${kind}/${entry}`,{method:'DELETE'}); if (data.removed !== true) throw Object.assign(Error('This platform setting was already removed.'),{status:404}); dialogGuard=null; await openPlatformManager(platform,kind,dialogTrigger,managerFocus(next || previous || '', '[data-remove-setting]')); toast(`${kind === 'proxy' ? 'Saved proxy' : 'Signed-in account'} removed.`); } catch(error) { if (error.status === 404) { dialogGuard=null; await openPlatformManager(platform,kind,dialogTrigger,managerFocus(next || previous || '', '[data-remove-setting]')); toast('This entry was already removed. The current list has been refreshed.',{type:'info'}); } else showError(error,'#dialog-error'); } finally { done(); } });
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
  return ['playground','keys','team','history','usage','docs','tests','settings','fallback'].includes(route) ? route : 'overview';
}
function loadingShape(current) {
  const key = PORTAL.role === 'member' && ['team','settings','fallback'].includes(current) ? `${current}member` : current;
  const template = document.querySelector(`template[data-loading-page="${key}"]`);
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
function viewTitle(view) {
  return view.querySelector('.page-tabs [aria-current=page]')?.textContent || view.querySelector('[data-loading-title]')?.dataset.loadingTitle || view.querySelector('h1')?.textContent;
}
function commitView(html, current, refresh) {
  const view = $('#view'), skeleton = view.querySelector('.page-skeleton'), focus = refresh ? focusSelector(document.activeElement) : null;
  const overlay = skeleton?.cloneNode(true);
  view.innerHTML = `<div class="view-frame"><div class="view-content">${html}</div></div>`;
  view.setAttribute('aria-busy', 'false');
  const content = view.querySelector('.view-content');
  content.querySelectorAll('.field select').forEach(select => { if (!select.parentElement.classList.contains('select-control')) { const wrapper=document.createElement('span'); wrapper.className='select-control'; select.before(wrapper); wrapper.append(select); } });
  if (overlay) { overlay.classList.add('loading-overlay'); overlay.querySelector('.loading-caption')?.remove(); view.firstChild.append(overlay); reveal(content, overlay); }
  state.renderedPage = current;
  if (!refresh) leaveGuards.clear();
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
  const loadingName = viewTitle(view) || 'page';
  document.title = `${loadingName} · Social Scrape`;
  $('#loading-status').textContent = `Loading ${loadingName.toLowerCase()}…`;
  const slow = setTimeout(() => { if (renderId === state.renderId) { const caption = view.querySelector('.loading-caption, [data-refresh-note]'); if (caption) caption.textContent = 'Still loading. You can keep using the navigation.'; } }, 10000);
  document.querySelectorAll('.nav a').forEach(link => {
    if (link.dataset.page === sectionOf(current)) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  const views = {overview, playground, keys:keysPage, team, history:requestHistory, usage, docs, tests:EndpointTests.render, settings:settingsPage, fallback:FallbackPage.render};
  try {
    const html = await views[current]({retryFailedOnly});
    if (renderId !== state.renderId) return;
    commitView(html, current, refresh);
    if (current === 'playground') { fillEndpoints(); initPlatformPicker(); paintRequest(); }
    if (current === 'docs') { $('#doc-search').value = state.docSearch; filterDocs(state.docSearch); selectDoc(); }
    if (current === 'fallback') FallbackPage.mount?.();
    const title = viewTitle(view) || 'Social Scrape';
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
let scrollTimer, committedUrl = location.href;
function saveScroll() { history.replaceState({...history.state, scrollY:window.scrollY}, ''); }
function postNavigation(scrollY = 0) { $('#view').focus({preventScroll:true}); window.scrollTo(0, scrollY); }
async function go(path) {
  const url = new URL(path, location.href), current = new URL(location.href);
  if (url.pathname === current.pathname && url.search === current.search && url.hash === current.hash) { postNavigation(window.scrollY); return; }
  if (!canLeave()) return;
  saveDraft(); hidePopovers(); saveScroll();
  const nav = $('#nav-dialog'); if (nav.open) nav.close();
  const oldPage = page(); history.pushState({scrollY:0}, '', url.pathname + url.search + url.hash);
  if (oldPage === 'docs' && page() === 'docs') { selectDoc({focus:true}); committedUrl = location.href; return; } else await render();
  committedUrl = location.href; postNavigation(0);
}


function startupFailure(error) {
  const view = $('#view');
  view.setAttribute('aria-busy','false'); $('#route-progress').hidden = true;
  view.innerHTML = layout('Could not load this page', 'Your workspace is still here.', `<section class="card"><div class="form-error" role="alert">${esc(error.message || 'The dashboard could not start. Reload to try again.')}</div><a class="ghost" style="margin-top:20px" href="${esc(location.pathname + location.search + location.hash)}">${icon('arrows-clockwise')}Reload page</a></section>`);
  $('#loading-status').textContent = 'The dashboard could not start. Reload the page to try again.';
}
function startPortal() {
history.scrollRestoration = 'manual'; history.replaceState({...history.state, scrollY:history.state?.scrollY ?? window.scrollY}, '');
initAccountMenus();
if (PORTAL.role !== 'owner') document.querySelectorAll('#nav [data-owner-only]').forEach(link => link.remove());
const mobileNav=$('#nav').cloneNode(true); mobileNav.removeAttribute('id'); mobileNav.querySelectorAll('[aria-describedby]').forEach(control=>control.removeAttribute('aria-describedby')); mobileNav.querySelectorAll('.control-tooltip').forEach(tooltip=>tooltip.remove()); $('#mobile-nav').append(mobileNav);
const mobileMenu=$('#mobile-menu'), navDialog=$('#nav-dialog');
mobileMenu.addEventListener('click', () => { navDialog.showModal(); mobileMenu.setAttribute('aria-expanded','true'); });
navDialog.addEventListener('close', () => { mobileMenu.setAttribute('aria-expanded','false'); if (innerWidth <= 760) mobileMenu.focus({preventScroll:true}); });
$('#dialog').addEventListener('close', () => {
  state.historyRequest = null;
  delete $('#dialog').dataset.oneTimeSecret; $('#dialog').classList.remove('history-dialog');
  cancelReveals();
  $('#dialog').replaceChildren();
  const fallback = $('#view').querySelector('input, button, a') || $('#view');
  const restored = dialogRestoreFocus?.();
  (restored?.isConnected ? restored : dialogTrigger?.isConnected && dialogTrigger !== document.body ? dialogTrigger : fallback).focus({preventScroll:true});
  dialogGuard = null; dialogRestoreFocus = null;
});
$('#dialog').addEventListener('cancel', event => { if (dialogGuard && !dialogGuard()) event.preventDefault(); });
for (const dialog of [$('#dialog'), $('#nav-dialog')]) dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const rect = dialog.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog === $('#dialog') ? closeDialog() : dialog.close();
});
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="/app"]');
  if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) { event.preventDefault(); go(link.getAttribute('href')); return; }
  const language = event.target.closest('[data-language]');
  if (language) { state.language = language.dataset.language; updateSnippet(); }
  const copy = event.target.closest('[data-copy]'); if (copy) copyElement(copy.dataset.copy);
  if (event.target.closest('[data-dialog-close]')) closeDialog();
  if (event.target.closest('[data-stop-request]')) state.request?.controller?.abort();
  const historyRetry = event.target.closest('[data-history-retry]');
  if (historyRetry) { showHistoryDetail(historyRetry.dataset.historyRetry); return; }
  const historyRow = event.target.closest('[data-history-id]');
  if (historyRow) { showHistoryDetail(historyRow.dataset.historyId); return; }
  const revoke = event.target.closest('[data-revoke]');
  if (revoke) { const key=state.keys.find(item=>String(item.id)===revoke.dataset.revoke), restoreFocus=successorResolver(revoke,'[data-revoke]'); confirmAction('Revoke this API key?', `Revoking “${key?.name || 'This key'}” cannot be undone. Integrations using it will lose access. To restore access, create a new key and update those integrations.`, 'Revoke key', () => api(`/portal/api/keys/${revoke.dataset.revoke}`, {method:'DELETE'}), {restoreFocus, staleMessage:'This API key was already revoked or removed. The list has been refreshed.'}); }
  const remove = event.target.closest('[data-remove]');
  if (remove) { const restoreFocus=successorResolver(remove,'[data-remove]'); confirmAction('Remove this team member?', `${remove.closest('tr').firstElementChild.textContent} will lose workspace access. Their sessions and API keys will stop working.`, 'Remove member', () => api(`/portal/api/team/${remove.dataset.remove}`, {method:'DELETE'}), {restoreFocus, staleMessage:'This member was already removed. The list has been refreshed.'}); }
  const managePlatform = event.target.closest('[data-manage-platform]');
  if (managePlatform) { openPlatformManager(managePlatform.dataset.managePlatform, managePlatform.dataset.manageKind, managePlatform).catch(error => toast(error.message, {type:'error'})); return; }
  const testProxy = event.target.closest('[data-test-proxy]');
  if (testProxy) testPlatformProxy(testProxy);
  const moveSetting = event.target.closest('[data-move-setting]');
  if (moveSetting) movePlatformSetting(moveSetting);
  const removeSetting = event.target.closest('[data-remove-setting]');
  if (removeSetting) removePlatformSetting(removeSetting);
  if (event.target.closest('#retry-page')) render();
  if (event.target.closest('[data-retry-overview]')) render({retryFailedOnly:true});
  if (event.target.closest('[data-focus-doc-search]')) { const search=$('#doc-search'); $('.docs-disclosure')?.setAttribute('open',''); search?.focus(); }
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
  if (event.target.matches('[data-param]')) { clearParamErrors(event.target); updateSnippet(); }
  if (['key-name','invite-email'].includes(event.target.id)) { event.target.removeAttribute('aria-invalid'); const error=document.getElementById(`${event.target.id}-error`); if (error) error.hidden=true; }
  if (event.target.closest('.manager-form')) { const input=event.target.closest('.manager-form').querySelector('input'), error=$('#manager-input-error'); input.removeAttribute('aria-invalid'); if (error) error.hidden=true; }
  if (event.target.id === 'doc-search') { state.docSearch = event.target.value; filterDocs(event.target.value, {announce:true, immediate:!event.target.value}); }
});
document.addEventListener('change', event => {
  if (event.target.id === 'pg-platform') fillEndpoints();
  if (event.target.id === 'pg-endpoint') fillParams();
  if (event.target.id === 'pg-key') state.selectedKey = event.target.value;
  if (event.target.matches('[data-param]')) { clearParamErrors(event.target); updateSnippet(); }
});
document.addEventListener('keydown', event => {
  if (!event.target.matches('[data-language]') || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll('[data-language]')];
  let index = tabs.indexOf(event.target);
  index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  state.language = tabs[index].dataset.language; updateSnippet(); tabs[index].focus();
});
window.addEventListener('scroll', () => { clearTimeout(scrollTimer); scrollTimer=setTimeout(saveScroll,150); }, {passive:true});
window.addEventListener('pagehide', saveScroll);
window.addEventListener('popstate', async event => { if (!canLeave()) { history.pushState({...history.state, scrollY:window.scrollY}, '', committedUrl); return; } saveDraft(); await render(); committedUrl=location.href; postNavigation(event.state?.scrollY ?? 0); });
window.addEventListener('hashchange', () => { if (page() === 'docs' && $('#doc-main')) selectDoc({focus:true}); });
render().catch(startupFailure);
}
try { startPortal(); } catch (error) { startupFailure(error); }
