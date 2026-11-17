const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="/assets/brand/icons.svg#${name}"></use></svg>`;
const fmt = time => time ? new Date(time * 1000).toLocaleString() : 'Never';
const number = value => Number(value).toLocaleString();
const mask = key => key ? `${key.prefix}••••••••••••` : 'No API key yet';
const state = {keys:[], spec:null, language:'curl', drafts:{}, platform:null, endpoint:null, selectedKey:'', docSearch:'', renderId:0};

async function api(url, options = {}) {
  const headers = {...options.headers, 'x-csrf-token':PORTAL.csrf};
  if (options.body && typeof options.body !== 'string') {
    headers['content-type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(url, {...options, headers});
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
async function loadKeys() {
  const data = await api('/portal/api/keys');
  state.keys = data.keys;
  const active = data.keys.filter(key => !key.revoked_at);
  $('#top-key').textContent = `${number(active.length)} active ${active.length === 1 ? 'key' : 'keys'}`;
  return data.keys;
}
async function loadUsage() {
  const data = await api('/portal/api/usage');
  $('#top-requests').textContent = `${number(data.total)} ${data.total === 1 ? 'request' : 'requests'}`;
  return data;
}

async function overview() {
  const [keys, usage, coverage] = await Promise.all([loadKeys(), loadUsage(), api('/meta/coverage')]);
  const active = keys.filter(key => !key.revoked_at);
  return layout('Overview', 'Your social data workspace. Everything you need to start building.', `
    <div class="metrics-grid">
      <section class="card"><div class="card-title">${icon('chart-bar')}Total requests</div><div class="stat">${number(usage.total)}</div><div class="stat-label">All time · ${PORTAL.role === 'owner' ? 'Workspace' : 'Your account'}</div></section>
      <section class="card"><div class="card-title">${icon('shield-check')}Active API keys</div><div class="stat">${number(active.length)}</div><div class="stat-label">${PORTAL.role === 'owner' ? 'Across your workspace' : 'Created by you'}</div></section>
      <section class="card"><div class="card-title">${icon('globe')}Supported endpoints</div><div class="stat">${number(coverage.implemented)}<span class="muted small"> / ${number(coverage.total)}</span></div><div class="stat-label">YouTube, TikTok & Instagram</div></section>
    </div>
    <div class="grid">
      <section class="card quick-start"><div class="eyebrow">Start building</div><h2>From endpoint to integration.</h2><p class="muted">Explore social data with a request, then take the code into your app.</p><div class="steps"><div class="step"><span class="step-number">1</span><div><h3>Create an API key</h3><p>Give each integration its own revocable credential.</p></div></div><div class="step"><span class="step-number">2</span><div><h3>Try an endpoint</h3><p>Choose a platform and send a request in the playground.</p></div></div><div class="step"><span class="step-number">3</span><div><h3>Make it yours</h3><p>Copy a cURL, Node.js, or Python snippet into your project.</p></div></div></div><a class="primary" href="/app/playground">Open playground ${icon('arrow-right')}</a></section>
      <div class="stack"><section class="card"><div class="card-head"><h2>Your API access</h2>${icon('shield-check')}</div><div class="keybox"><code>${esc(mask(active[0]))}</code></div><p class="help">${active.length ? 'Pass your key in the x-api-key header. Full secrets are only shown when you create them.' : 'Create your first API key to connect an integration. You can explore the playground with your workspace session.'}</p><a class="ghost" style="margin-top:20px" href="/app/keys">${active.length ? 'Manage API keys' : 'Create API key'} ${icon('arrow-right')}</a></section><section class="card"><div class="card-head"><h2>One API. Three platforms.</h2>${icon('globe')}</div><p class="help">Read public social data through a consistent API. Endpoint availability depends on each platform.</p><div class="platforms"><span class="badge">YouTube</span><span class="badge">TikTok</span><span class="badge">Instagram</span></div><a class="text-link small" style="display:inline-block;margin-top:20px" href="/app/docs">Explore the documentation</a></section></div>
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
  const [teamData, email, inviteData] = await Promise.all([api('/portal/api/team'), api('/portal/api/settings/email'), api('/portal/api/invites')]);
  const emailStatus = email.error ? 'Needs attention' : email.configured ? `Connected · ${email.source === 'dashboard' ? 'dashboard' : 'server environment'}` : 'Off';
  const inviteHelp = email.configured ? `We’ll email the invitation from ${esc(email.sender)}. Links expire after 7 days.` : 'Email delivery is off, so you’ll get a link to share privately. Links expire after 7 days.';
  return layout('Team', 'Give your team a private place to work with social data.', `
    <section class="card"><form class="row" id="invite-form"><label class="field">Email address<input id="invite-email" name="email" type="email" placeholder="teammate@company.com" required autocomplete="email" aria-describedby="invite-hint"></label><button class="primary" type="submit">${icon('plus')}Create invitation</button></form><p class="help" id="invite-hint" style="margin-top:12px">${inviteHelp}${email.configured ? '' : ' <button class="text-button" type="button" data-email-settings>Set up email</button>'}</p><div class="field-error" id="action-error" role="alert" hidden></div></section>
    <section class="card email-card"><div class="card-head"><div><div class="eyebrow">Resend</div><h2>Email delivery</h2></div><span class="badge ${email.error ? 'warn' : email.configured ? 'good' : ''}">${emailStatus}</span></div>${email.error ? `<div class="form-error" role="alert">${esc(email.error)}. Save the settings again or remove them.</div>` : ''}${email.error ? '' : email.configured ? `<div class="email-details"><p><span class="muted">From</span><strong>${esc(email.sender)}</strong></p><p><span class="muted">Dashboard URL</span><strong>${esc(email.public_url)}</strong></p>${email.source === 'dashboard' ? `<p><span class="muted">Last updated</span><strong>${esc(fmt(email.updated_at))}</strong></p>` : ''}</div>` : '<p class="muted">Invitations and password reset links must be copied and shared manually.</p>'}<div class="field-error" id="email-action-error" role="alert" hidden></div><div class="row email-actions"><button class="ghost" type="button" data-email-settings>${email.configured ? 'Change settings' : 'Set up email'}</button>${email.configured ? '<button class="ghost" type="button" data-email-test>Send test email</button>' : ''}${email.source === 'dashboard' ? '<button class="danger" type="button" data-email-remove>Remove</button>' : ''}</div></section>
    ${inviteData.invites.length ? `<div class="table-caption"><h2>Pending invitations</h2><span>${number(inviteData.invites.length)} pending</span></div>${tableRegion('Pending invitations', `<table><thead><tr><th scope="col">Email</th><th scope="col">Invited by</th><th scope="col">Sent</th><th scope="col">Expires</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${inviteData.invites.map(invite => `<tr><td>${esc(invite.email)}</td><td>${esc(invite.invited_by || '—')}</td><td>${esc(fmt(invite.sent_at))}</td><td>${invite.expired ? '<span class="badge warn">Expired</span>' : esc(fmt(invite.expires_at))}</td><td><div class="table-actions"><button class="ghost" type="button" data-resend-invite="${esc(invite.email)}" aria-label="Resend invitation to ${esc(invite.email)}">Resend</button><button class="danger" type="button" data-revoke-invite="${esc(invite.email)}" aria-label="Revoke invitation for ${esc(invite.email)}">Revoke</button></div></td></tr>`).join('')}</tbody></table>`)}` : ''}
    <div class="table-caption"><h2>Workspace members</h2><span>${number(teamData.members.length)} ${teamData.members.length === 1 ? 'member' : 'members'}</span></div>
    ${tableRegion('Workspace members', `<table><thead><tr><th scope="col">Member</th><th scope="col">Role</th><th scope="col">API keys</th><th scope="col">Joined</th><th scope="col">Last sign-in</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${teamData.members.map(member => `<tr><td>${esc(member.email)}</td><td><span class="badge">${member.role === 'owner' ? 'Owner' : 'Member'}</span></td><td>${number(member.key_count)}</td><td>${esc(fmt(member.created_at))}</td><td>${esc(fmt(member.last_login_at))}</td><td>${member.role === 'member' ? `<div class="table-actions"><button class="ghost" type="button" data-reset-member="${esc(member.id)}" data-member-email="${esc(member.email)}" aria-label="Send password reset link to ${esc(member.email)}">Send reset link</button><button class="danger" data-remove="${esc(member.id)}" aria-label="Remove ${esc(member.email)}">Remove</button></div>` : ''}</td></tr>`).join('')}</tbody></table>`)}`);
}

function account() {
  return layout('Account', 'Manage your sign-in details and account security.', `<div class="stack account-stack"><section class="card"><div class="card-head"><h2>Account details</h2>${icon('user-circle')}</div><div class="email-details"><p><span class="muted">Email</span><strong>${esc(PORTAL.email)}</strong></p><p><span class="muted">Role</span><strong>${PORTAL.role === 'owner' ? 'Workspace owner' : 'Team member'}</strong></p></div></section><section class="card"><h2>Change password</h2><form class="form-stack" id="password-form"><label class="field">Current password<input name="current_password" type="password" required autocomplete="current-password"></label><label class="field">New password<input name="new_password" type="password" required minlength="12" autocomplete="new-password" aria-describedby="new-password-help"><span class="help" id="new-password-help">Use at least 12 characters. Other devices will be signed out.</span></label><div class="field-error" id="action-error" role="alert" hidden></div><button class="primary" type="submit">Change password</button></form></section></div>`);
}

async function settingsPage() {
  if (PORTAL.role !== 'owner') return layout('Platform settings', 'Credentials and network access by provider.', `<section class="card">${emptyState('shield-check', 'Managed by your workspace owner', 'Ask your owner to update platform credentials and proxies.')}</section>`);
  const data = await api('/portal/api/settings/platforms');
  const names = {instagram:'Instagram', tiktok:'TikTok', youtube:'YouTube'};
  const list = (platform, kind, items) => items.length ? `<ol class="credential-pool">${items.map(item => `<li><div><strong>${item.position === 1 ? 'Primary' : `Backup ${item.position - 1}`}</strong><span>${kind === 'proxy' ? 'Proxy credentials saved privately' : 'Account cookies saved privately'} · ${esc(fmt(item.updated_at))}</span></div><div class="pool-actions">${item.position > 1 ? `<button class="ghost" type="button" data-move-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}" data-direction="up" aria-label="Move ${kind} up">↑</button>` : ''}${item.position < items.length ? `<button class="ghost" type="button" data-move-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}" data-direction="down" aria-label="Move ${kind} down">↓</button>` : ''}<button class="danger" type="button" data-remove-setting="${kind}" data-platform="${platform}" data-entry="${esc(item.id)}">Remove</button></div></li>`).join('')}</ol>` : `<div class="empty compact">No ${kind === 'proxy' ? 'proxies' : 'cookie accounts'} configured.</div>`;
  const cards = Object.entries(names).map(([platform, name]) => {
    const item = data[platform], proxies = item.proxy, cookies = item.cookies;
    return `<section class="card platform-card"><div class="card-head"><div><div class="eyebrow">Provider</div><h2>${name}</h2></div><span class="badge ${proxies.length || cookies.length ? 'good' : ''}">${proxies.length || cookies.length ? `${proxies.length} prox${proxies.length === 1 ? 'y' : 'ies'} · ${cookies.length} account${cookies.length === 1 ? '' : 's'}` : 'Anonymous'}</span></div>
      <div class="setting-section"><div class="setting-copy"><h3>Proxy pool</h3><p class="muted">Tried in order only for retryable network, block, login-wall, or rejected-session failures.</p>${list(platform,'proxy',proxies)}</div><form class="platform-setting-form stack" data-platform="${platform}" data-kind="proxy"><label class="field">Add ${proxies.length ? `Backup ${proxies.length}` : 'primary'} proxy<input name="value" type="password" required autocomplete="off" spellcheck="false" placeholder="http://username:password@host:port"><span class="help">Up to five. Values are encrypted and never shown again.</span></label><div class="field-error" role="alert" hidden></div><button class="primary" type="submit">Add proxy</button></form></div>
      <div class="setting-section"><div class="setting-copy"><h3>Account cookie pool</h3><p class="muted">Each retry uses one complete cookie jar; cookies from different accounts are never merged.</p>${list(platform,'cookies',cookies)}</div><form class="platform-setting-form stack" data-platform="${platform}" data-kind="cookies"><label class="field">Add ${cookies.length ? `Backup ${cookies.length}` : 'primary'} account cookies<input name="file" type="file" required accept=".txt,text/plain"><span class="help">Dedicated throwaway account, Netscape cookies.txt, maximum 2 MiB. Up to five.</span></label><div class="field-error" role="alert" hidden></div><button class="primary" type="submit">Add account cookies</button></form></div></section>`;
  }).join('');
  return layout('Platform settings', 'Configure ordered backup pools independently for Instagram, TikTok, and YouTube.', `<div class="platform-settings">${cards}</div><div class="inline-note settings-note">${icon('shield-check')}<span>Attempt 1 uses both primaries. Later attempts advance to the next configured proxy and cookie account, reusing the last entry on the shorter list. Bad input and missing content are never retried.</span></div>`);
}

async function requestHistory() {
  const data = await api('/portal/api/history?limit=200');
  return layout('Request history', 'Inspect API activity, response status, and timing.', `
    <div class="table-caption"><span>Latest ${number(data.requests.length)} requests · ${PORTAL.role === 'owner' ? 'Workspace' : 'Your account'}</span><span>Times shown in ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}</span></div>
    ${data.requests.length ? tableRegion('API request history', `<table><thead><tr><th scope="col">Time</th><th scope="col">API key</th><th scope="col">Endpoint</th><th scope="col">Response</th><th scope="col">Duration</th><th scope="col">Cache</th></tr></thead><tbody>${data.requests.map(request => `<tr class="history-row" tabindex="0" data-history-id="${request.id}" aria-label="Open response from ${esc(fmt(request.created_at))}"><td>${fmt(request.created_at)}</td><td>${esc(request.prefix || 'Workspace session')}</td><td><code>${esc(request.method)} ${esc(request.path)}${request.query ? '?' + esc(request.query) : ''}</code></td><td><span class="badge ${request.status < 400 ? 'good' : 'bad'}">${request.status} ${request.status < 400 ? 'OK' : 'Error'}</span>${request.fallback ? ' <span class="badge">via ScrapeCreators</span>' : ''}</td><td>${number(request.duration_ms)} ms</td><td>${request.cached ? 'Hit' : 'Miss'}</td></tr>`).join('')}</tbody></table>`) : `<section class="card">${emptyState('clock', 'Your requests will appear here', 'Send a request in the playground to inspect its response and timing.', '<a class="primary" href="/app/playground">Open playground</a>')}</section>`}`);
}

async function showHistoryDetail(id) {
  const detail = await api(`/portal/api/history/${id}`);
  let formatted = detail.response || '';
  try { formatted = JSON.stringify(JSON.parse(formatted), null, 2); } catch {}
  const route = detail.path + (detail.query ? `?${detail.query}` : '');
  openDialog({title:`${detail.method} ${detail.path}`, description:`${detail.status} · ${number(detail.duration_ms)} ms · ${detail.cached ? 'Cache hit' : 'Live'}${detail.fallback ? ' · ScrapeCreators fallback' : ''}`, body:`<dl class="history-facts"><div><dt>Time</dt><dd>${esc(fmt(detail.created_at))}</dd></div><div><dt>API key</dt><dd>${esc(detail.prefix || 'Workspace session')}</dd></div><div><dt>Request</dt><dd><code>${esc(route)}</code></dd></div></dl>${detail.response_truncated ? '<div class="inline-note">The stored response was capped at 2 MiB.</div>' : ''}<pre class="code history-response" id="history-response" tabindex="0"></pre>`, footer:`<button class="ghost" data-dialog-close>Close</button><button class="primary" data-copy="history-response">${icon('copy')}Copy response</button>`});
  $('#history-response').textContent = formatted || 'No response body was stored for this request.';
}

async function usage() {
  const data = await loadUsage();
  const max = Math.max(...data.daily, 1), total = data.daily.reduce((sum, count) => sum + count, 0);
  const labels = data.daily.map((_, index) => { const date = new Date(); date.setDate(date.getDate() - 29 + index); return date.toLocaleDateString(undefined, {month:'short', day:'numeric'}); });
  return layout('Usage', 'Understand your API traffic over the last 30 days.', `
    <section class="card"><div class="card-head"><div><h2>Request volume</h2><span class="chart-summary">${number(total)} requests · ${PORTAL.role === 'owner' ? 'Workspace' : 'Your account'} · Last 30 days${data.fallback_count > 0 ? ` · ${number(data.fallback_count)} via ScrapeCreators` : ''}</span></div><span class="badge">30 days</span></div>
    ${total ? `<div class="chart" role="img" aria-label="${number(total)} requests in the last 30 days. Exact counts are in the daily data table below.">${data.daily.map(count => `<div class="bar" style="height:${Math.max(1, count / max * 100)}%" aria-hidden="true"></div>`).join('')}</div><div class="chart-labels"><span>${labels[0]}</span><span>${labels[14]}</span><span>${labels[29]}</span></div>` : emptyState('chart-bar', 'No requests in this period', 'Usage appears here when you call an API endpoint.', '<a class="ghost" href="/app/playground">Make your first request</a>')}
    <details><summary>${icon('chart-bar')}View daily data</summary><p class="help">Rolling 24-hour periods ending now. Date labels use your local timezone.</p>${tableRegion('Daily request counts', `<table><thead><tr><th scope="col">Period ending</th><th scope="col">Requests</th></tr></thead><tbody>${data.daily.map((count, index) => `<tr><td>${labels[index]}</td><td>${number(count)}</td></tr>`).join('')}</tbody></table>`)}</details></section>
    <section class="card" style="margin-top:24px"><h2>Most used endpoints</h2><p class="help">Last 30 days · Up to 10 routes</p>${data.routes.map(route => `<div class="param"><strong><code>${esc(route.path)}</code></strong><small>${number(route.count)} requests</small></div>`).join('') || emptyState('globe', 'No endpoint activity yet', 'Run a request to see your most used routes.')}</section>`);
}

async function spec() {
  if (!state.spec) state.spec = await api('/openapi.json');
  return state.spec;
}
function endpoints(schema) {
  return Object.entries(schema.paths).filter(([, item]) => item.get).map(([path, item]) => ({path, op:item.get, platform:path.split('/')[2]}));
}
function platformName(platform) { return {youtube:'YouTube', instagram:'Instagram', tiktok:'TikTok'}[platform] || platform; }
async function playground() {
  const schema = await spec(), groups = [...new Set(endpoints(schema).map(endpoint => endpoint.platform))];
  await loadKeys();
  return layout('Playground', 'Explore an endpoint. Make a request. Take the code with you.', `
    <div class="playground"><section class="card playground-config"><div class="section-label">Configure request</div><form class="form-stack" id="request-form"><label class="field">Platform<select id="pg-platform">${groups.map(group => `<option value="${esc(group)}" ${state.platform === group ? 'selected' : ''}>${platformName(group)}</option>`).join('')}</select></label><label class="field">Endpoint<select id="pg-endpoint"></select></label><label class="field">Run as<select id="pg-key"><option value="">Workspace session</option>${state.keys.filter(key => !key.revoked_at).map(key => `<option value="${key.id}" ${String(key.id) === state.selectedKey ? 'selected' : ''}>${esc(key.name)} · ${esc(key.prefix)}</option>`).join('')}</select><span class="help">Snippets use your integration’s API key.</span></label><div id="pg-params"></div><div class="inline-note" id="endpoint-note" hidden></div><div class="field-error" id="request-error" role="alert" hidden></div><button class="primary" type="submit" id="run-request">${icon('play')}Run request</button></form></section>
    <div class="request-workbench"><section class="card"><div class="card-head"><div class="request-info"><span class="method">GET</span><code id="request-path"></code></div><button class="icon-button" data-copy="snippet" aria-label="Copy code snippet" title="Copy snippet">${icon('copy')}</button></div><div class="code-tabs" role="tablist" aria-label="Code language">${[['curl','cURL'],['node','Node.js'],['python','Python']].map(([lang, label]) => `<button id="tab-${lang}" role="tab" aria-controls="snippet" aria-selected="${state.language === lang}" tabindex="${state.language === lang ? 0 : -1}" data-language="${lang}">${label}</button>`).join('')}</div><pre class="code" id="snippet" role="tabpanel" tabindex="0" aria-labelledby="tab-${state.language}" style="margin-top:16px"></pre></section>
    <section class="card response"><div class="card-head"><h2>Response</h2><div class="row"><span class="badge" id="response-status" role="status">Ready</span><button class="icon-button" data-copy="response-raw" aria-label="Copy raw JSON response" title="Copy raw JSON" hidden>${icon('copy')}</button></div></div><div id="response"><div class="response-placeholder"><div>${icon('compass')}<strong>Your response starts here</strong><p>Choose an endpoint and run a request to see the result.</p></div></div></div></section></div></div>`);
}
function saveDraft() {
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
async function runRequest() {
  const button = $('#run-request'), responseArea = $('#response'), status = $('#response-status');
  if (button.disabled) return;
  updateSnippet();
  button.disabled = true;
  button.innerHTML = `${icon('circle-notch')}Running request…`;
  $('#request-error').hidden = true;
  status.className = 'badge'; status.textContent = 'Running';
  responseArea.setAttribute('aria-busy', 'true');
  const copy = $('[data-copy="response-raw"]'); copy.hidden = true;
  const query = params(), path = $('#pg-endpoint').value, url = path + (query.size ? '?' + query : '');
  const keyId = $('#pg-key').value;
  const fields = [...$('#request-form').querySelectorAll('input, select')];
  fields.forEach(field => { field.disabled = true; });
  const started = performance.now();
  try {
    const response = await fetch(url, {headers:{'x-portal-key-id':keyId}});
    const text = await response.text();
    if (!responseArea.isConnected) return;
    ResponsePreview.mount(responseArea, {path, status:response.status, text});
    status.className = `badge ${response.ok ? 'good' : 'bad'}`;
    status.textContent = `${response.status} ${response.ok ? 'OK' : 'Error'} · ${number(Math.round(performance.now() - started))} ms`;
    copy.hidden = false;
    loadUsage().catch(() => { $('#top-requests').textContent = 'Usage could not refresh'; });
  } catch {
    if (!responseArea.isConnected) return;
    status.className = 'badge bad'; status.textContent = 'Connection failed';
    showError(Error('Could not reach the API. Check your connection, then run the request again.'), '#request-error');
  } finally {
    fields.forEach(field => { field.disabled = false; });
    button.disabled = false; button.innerHTML = `${icon('play')}Run request`;
    responseArea.setAttribute('aria-busy', 'false');
  }
}

async function docs() {
  const schema = await spec(), all = endpoints(schema), groups = [...new Set(all.map(endpoint => endpoint.platform))];
  return `<div class="docs"><aside class="docs-nav" aria-label="API reference"><label class="field"><span class="sr-only">Search endpoints</span><input type="search" class="search-input" id="doc-search" placeholder="Search endpoints…"></label><a class="docs-link" href="/app/docs" data-doc-intro>Introduction</a>${groups.map(group => `<div class="docs-group" data-group><h3>${platformName(group)}</h3>${all.filter(endpoint => endpoint.platform === group).map(endpoint => `<a class="docs-link" data-doc="${esc(endpoint.path + ' ' + endpoint.op.summary)}" href="/app/docs#${encodeURIComponent(endpoint.path)}"><span class="method">GET</span><span>${esc(endpoint.op.summary || endpoint.path)}</span></a>`).join('')}</div>`).join('')}<p id="docs-no-results" class="help" hidden>No matching endpoints. Try another search.</p></aside><article class="doc-content" id="doc-main"></article></div>`;
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
  $('#doc-main').innerHTML = `<div class="eyebrow">Developer reference</div><h1>SocialScrape API</h1><p class="lede">Public YouTube, TikTok, and Instagram data through one private API.</p><section class="card"><h2>Start with a request</h2><p class="help">Send a member API key in the <code>x-api-key</code> header. Create and revoke credentials from API keys.</p><pre class="code" tabindex="0">curl '${esc(location.origin)}/v1/tiktok/profile?handle=tiktok' \\\n  -H 'x-api-key: YOUR_API_KEY'</pre><a class="ghost" style="margin-top:20px" href="/app/playground">Open playground ${icon('arrow-right')}</a></section><h2>Authentication</h2><p class="muted">Use a separate key for each person or integration. Secrets are shown once when created, and can be revoked independently.</p><h2>Response format</h2><p class="muted">Successful calls include <code>success: true</code> and <code>credits_charged: 0</code>. Errors include an error code and a message explaining what happened.</p><h2>Caching</h2><p class="muted">Set <code>cache_max_age</code> to <code>12h</code>, <code>1d</code>, or <code>7d</code> to accept a cached response. Leave it out to fetch fresh data.</p><h2>Endpoint availability</h2><p class="muted">The reference includes the full compatibility API. Routes outside the supported extraction boundary return <code>501</code> with a reason. Platform restrictions may require cookies or a proxy.</p>`;
}
function showEndpoint(path) {
  const operation = state.spec.paths[path].get, parameters = operation.parameters || [];
  const unsupported = operation.tags?.includes('not supported');
  $('#doc-main').innerHTML = `<div class="eyebrow">${platformName(path.split('/')[2])} API</div><div class="endpoint-title"><span class="method">GET</span><h1>${esc(operation.summary || path)}</h1></div><p class="lede"><code>${esc(path)}</code></p>${unsupported ? '<div class="inline-note"><span class="badge warn">Not supported</span><span>This compatibility route is not implemented through yt-dlp. Requests return 501.</span></div>' : ''}<section class="card"><div class="card-head"><h2>Request</h2><button class="icon-button" data-copy="doc-snippet" aria-label="Copy request snippet">${icon('copy')}</button></div><pre class="code" id="doc-snippet" tabindex="0">curl '${esc(location.origin + path)}' \\\n  -H 'x-api-key: YOUR_API_KEY'</pre></section><h2>Parameters</h2>${parameters.map(param => `<div class="param"><strong><code>${esc(param.name)}</code>${param.required ? '<span class="badge required">Required</span>' : '<span class="help">Optional</span>'}<span class="help">${esc(param.schema?.type || '')}</span></strong><small>${esc(param.description || '')}</small></div>`).join('') || '<p class="muted">No parameters.</p>'}<h2>Responses</h2>${Object.entries(operation.responses || {}).map(([code, response]) => `<div class="param"><strong><span class="badge ${Number(code) < 400 ? 'good' : ''}">${code}</span>${esc(response.description)}</strong></div>`).join('')}`;
}

let dialogTrigger;
function closeDialog() { $('#dialog').close(); }
function openDialog({title, description, body, footer}) {
  const dialog = $('#dialog');
  dialogTrigger = document.activeElement;
  dialog.innerHTML = `<header class="modal-head"><div><h2 id="dialog-title">${esc(title)}</h2><p id="dialog-description">${esc(description)}</p></div><button class="icon-button" data-dialog-close aria-label="Close dialog">${icon('x')}</button></header><div class="modal-body">${body}<div class="field-error" id="dialog-error" role="alert" hidden></div></div><footer class="modal-foot">${footer}</footer>`;
  dialog.showModal();
  (dialog.querySelector('[autofocus]') || dialog.querySelector('.modal-body :is(input:not([type=hidden]), textarea, select)'))?.focus();
}
function showSecret(title, description, value, options = {}) {
  const settings = typeof options === 'boolean' ? (options ? {label:'API key', help:'Keep this key private. It will not be shown again.', copyLabel:'Copy API key'} : {label:'Invitation link', help:'Share this link privately with the invited person.', copyLabel:'Copy link'}) : options;
  openDialog({title, description, body:`${settings.warning ? `<div class="form-error secret-warning" role="alert">${esc(settings.warning)}</div>` : ''}<label class="field">${esc(settings.label || 'Link')}<textarea class="secret" id="new-secret" readonly spellcheck="false">${esc(value)}</textarea></label><p class="help" id="copy-feedback" style="margin-top:12px">${esc(settings.help || 'Share this link privately.')}</p>`, footer:`<button class="ghost" data-dialog-close>Done</button><button class="primary" data-copy="new-secret">${icon('copy')}${esc(settings.copyLabel || 'Copy link')}</button>`});
}
function confirmAction(title, description, confirmLabel, action) {
  openDialog({title, description, body:'<p class="help">This takes effect immediately for the selected account or credential.</p>', footer:`<button class="ghost" data-dialog-close autofocus>Cancel</button><button class="danger" id="confirm-action">${esc(confirmLabel)}</button>`});
  $('#confirm-action').addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true; button.textContent = 'Please wait…';
    try { const result = await action(); closeDialog(); await render(); if (confirmLabel === 'Send reset link') handleLinkResult(result, 'Password reset', 'Password reset link emailed to'); else toast(({'Revoke key':'API key revoked.', 'Remove member':'Member removed.', 'Remove proxy':'Saved proxy removed.', 'Revoke invitation':'Invitation revoked.', 'Remove email settings':'Email settings removed.'})[confirmLabel] || 'Change saved.'); }
    catch (error) { showError(error, '#dialog-error'); }
    finally { button.disabled = false; button.textContent = confirmLabel; }
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
function handleLinkResult(data, kind, successCopy) {
  if (data.emailed) { toast(`${successCopy} ${data.email}.`); return false; }
  const warning = data.email_error ? `The email could not be sent: ${data.email_error}. Share this link instead.` : '';
  showSecret(kind === 'Invitation' ? 'Invitation created' : 'Password reset link created', kind === 'Invitation' ? 'This invitation link expires in 7 days.' : 'This link works once and expires in 24 hours.', data.url, {label:kind === 'Invitation' ? 'Invitation link' : 'Reset link', help:kind === 'Invitation' ? 'Share this link privately with the invited person.' : 'Share this link privately with the member.', copyLabel:'Copy link', warning});
  return true;
}
async function submitCreation(form) {
  const button = form.querySelector('button[type=submit]');
  if (button.disabled) return;
  const label = button.innerHTML;
  button.disabled = true; button.textContent = 'Creating…'; $('#action-error').hidden = true;
  try {
    if (form.id === 'key-form') {
      const data = await api('/portal/api/keys', {method:'POST', body:{name:$('#key-name').value.trim()}});
      await render();
      showSecret('Your API key is ready', 'Save this secret now. It is only shown once.', data.key, true);
    } else {
      const data = await api('/portal/api/invites', {method:'POST', body:{email:$('#invite-email').value.trim()}});
      form.reset(); await render(); handleLinkResult(data, 'Invitation', 'Invitation emailed to');
    }
  } catch (error) { showError(error); }
  finally { button.disabled = false; button.innerHTML = label; }
}

function openEmailSettings(trigger) {
  const label = trigger?.textContent;
  if (trigger) { trigger.disabled = true; trigger.textContent = 'Loading…'; }
  api('/portal/api/settings/email').then(email => { const keep = email.source === 'dashboard' && email.configured; openDialog({title:email.configured ? 'Change email settings' : 'Set up email', description:'Connect Resend to send invitations and password reset links.', body:`<form class="form-stack" id="email-settings-form"><label class="field">${keep ? 'Replace API key' : 'Resend API key'}<input name="api_key" type="password" ${keep ? '' : 'required'} autocomplete="off" spellcheck="false" placeholder="re_…">${keep ? '<span class="help">Leave blank to keep the saved key.</span>' : ''}</label><label class="field">From address<input name="sender" required value="${esc(email.sender || '')}" placeholder="SocialScrape &lt;noreply@yourdomain.com&gt;"><span class="help">Use an address on a domain you’ve verified in Resend.</span></label><label class="field">Dashboard URL<input name="public_url" type="url" required value="${esc(email.public_url || location.origin)}"><span class="help">Links in emails point here.</span></label><div class="inline-note">${icon('shield-check')}<span>The API key is encrypted and never shown again.</span></div></form>`, footer:'<button class="ghost" data-dialog-close>Cancel</button><button class="primary" type="submit" form="email-settings-form">Save settings</button>'}); }).catch(error => showError(error, '#email-action-error')).finally(() => { if (trigger?.isConnected) { trigger.disabled = false; trigger.textContent = label; } });
}
async function saveEmailSettings(form) {
  const button = $(`button[form="${form.id}"]`), label = button.textContent;
  button.disabled = true; button.textContent = 'Saving…'; $('#dialog-error').hidden = true;
  try { await api('/portal/api/settings/email', {method:'PUT', body:{api_key:form.elements.api_key.value.trim(), sender:form.elements.sender.value.trim(), public_url:form.elements.public_url.value.trim()}}); closeDialog(); await render(); toast('Email settings saved.'); }
  catch (error) { showError(error, '#dialog-error'); }
  finally { button.disabled = false; button.textContent = label; }
}
async function changePassword(form) {
  const button = form.querySelector('button[type=submit]'), label = button.textContent;
  button.disabled = true; button.textContent = 'Changing…'; $('#action-error').hidden = true;
  try { const data = await api('/portal/api/account/password', {method:'POST', body:{current_password:form.elements.current_password.value, new_password:form.elements.new_password.value}}); form.reset(); toast(data.signed_out_sessions ? 'Password changed. Other sessions were signed out.' : 'Password changed.'); }
  catch (error) { showError(error); }
  finally { button.disabled = false; button.textContent = label; }
}
async function savePlatformSetting(form) {
  const button = form.querySelector('button[type=submit]'), error = form.querySelector('.field-error');
  if (button.disabled) return;
  const label = button.textContent, platform = form.dataset.platform, kind = form.dataset.kind;
  button.disabled = true; button.textContent = 'Saving…'; error.hidden = true;
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
  finally { button.disabled = false; button.textContent = label; }
}

function page() {
  const route = location.pathname.split('/')[2] || 'overview';
  return ['playground','keys','team','history','usage','docs','settings','fallback','account'].includes(route) ? route : 'overview';
}
async function render() {
  const renderId = ++state.renderId, current = page(), view = $('#view');
  view.setAttribute('aria-busy','true');
  document.querySelectorAll('.nav a').forEach(link => {
    if (link.dataset.page === current) link.setAttribute('aria-current','page'); else link.removeAttribute('aria-current');
  });
  const views = {overview, playground, keys:keysPage, team, history:requestHistory, usage, docs, settings:settingsPage, fallback:FallbackPage.render, account};
  try {
    const html = await views[current]();
    if (renderId !== state.renderId) return;
    view.innerHTML = html;
    if (current === 'playground') fillEndpoints();
    if (current === 'docs') { $('#doc-search').value = state.docSearch; filterDocs(state.docSearch); selectDoc(); }
    const title = view.querySelector('h1')?.textContent || 'SocialScrape';
    document.title = `${title} · SocialScrape`;
  } catch (error) {
    if (renderId !== state.renderId) return;
    view.innerHTML = layout('Could not load this page', 'Your workspace is still here. Try loading the page again.', `<section class="card"><div class="form-error" role="alert">${esc(error.message)}</div><button class="ghost" id="retry-page" style="margin-top:20px">${icon('arrows-clockwise')}Try again</button></section>`);
  } finally { if (renderId === state.renderId) view.setAttribute('aria-busy','false'); }
}
async function go(path) {
  saveDraft();
  const nav = $('#nav-dialog'); if (nav.open) nav.close();
  const url = new URL(path, location.origin), oldPage = page();
  window.history.replaceState({...window.history.state, scrollY:window.scrollY}, '');
  window.history.pushState({}, '', url.pathname + url.search + url.hash);
  if (oldPage === 'docs' && page() === 'docs') selectDoc(); else await render();
  $('#view').focus({preventScroll:true}); window.scrollTo(0,0);
}

$('#user-initial').textContent = PORTAL.email.slice(0,1).toUpperCase();
$('#user-role').textContent = PORTAL.role === 'owner' ? 'Workspace owner' : 'Team member';
const mobileNav = $('#nav').cloneNode(true); mobileNav.removeAttribute('id'); $('#mobile-nav').append(mobileNav);
if (PORTAL.role !== 'owner') document.querySelectorAll('[data-page="settings"], [data-page="fallback"]').forEach(link => link.remove());
$('#mobile-menu').addEventListener('click', () => $('#nav-dialog').showModal());
$('#dialog').addEventListener('close', () => {
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
  const historyRow = event.target.closest('[data-history-id]');
  if (historyRow) { dialogTrigger = historyRow; showHistoryDetail(historyRow.dataset.historyId).catch(error => toast(error.message)); return; }
  const revoke = event.target.closest('[data-revoke]');
  if (revoke) { const key = state.keys.find(item => String(item.id) === revoke.dataset.revoke); confirmAction('Revoke this API key?', `“${key?.name || 'This key'}” will stop working. Integrations using it will lose access.`, 'Revoke key', () => api(`/portal/api/keys/${revoke.dataset.revoke}`, {method:'DELETE'})); }
  const remove = event.target.closest('[data-remove]');
  if (remove) confirmAction('Remove this team member?', `${remove.closest('tr').firstElementChild.textContent} will lose workspace access. Their sessions and API keys will stop working.`, 'Remove member', () => api(`/portal/api/team/${remove.dataset.remove}`, {method:'DELETE'}));
  const emailSettings = event.target.closest('[data-email-settings]'); if (emailSettings) openEmailSettings(emailSettings);
  const emailTest = event.target.closest('[data-email-test]'); if (emailTest) { const label=emailTest.textContent; emailTest.disabled=true; emailTest.textContent='Sending…'; $('#email-action-error').hidden=true; api('/portal/api/settings/email/test',{method:'POST'}).then(data => toast(`Test email sent to ${data.to}.`)).catch(error => showError(error,'#email-action-error')).finally(() => { emailTest.disabled=false; emailTest.textContent=label; }); }
  const emailRemove = event.target.closest('[data-email-remove]'); if (emailRemove) confirmAction('Remove email settings?', 'Saved dashboard settings will be removed. Email falls back to server environment settings if they exist; otherwise email delivery turns off.', 'Remove email settings', () => api('/portal/api/settings/email',{method:'DELETE'}));
  const resend = event.target.closest('[data-resend-invite]'); if (resend) { const label=resend.textContent; resend.disabled=true; resend.textContent='Resending…'; api('/portal/api/invites',{method:'POST',body:{email:resend.dataset.resendInvite}}).then(async data => { await render(); handleLinkResult(data,'Invitation','Invitation resent to'); }).catch(error => showError(error)).finally(() => { if (resend.isConnected) { resend.disabled=false; resend.textContent=label; } }); }
  const revokeInvite = event.target.closest('[data-revoke-invite]'); if (revokeInvite) confirmAction('Revoke this invitation?', `${revokeInvite.dataset.revokeInvite} won’t be able to join with the current link.`, 'Revoke invitation', () => api(`/portal/api/invites?email=${encodeURIComponent(revokeInvite.dataset.revokeInvite)}`,{method:'DELETE'}));
  const reset = event.target.closest('[data-reset-member]'); if (reset) confirmAction('Send a password reset link?', `The link for ${reset.dataset.memberEmail} works once for 24 hours. Their current password keeps working until they set a new one. If email delivery is off, you’ll get a link to share.`, 'Send reset link', () => api(`/portal/api/team/${encodeURIComponent(reset.dataset.resetMember)}/password-reset`,{method:'POST'}));
  if (event.target.closest('#retry-page')) render();
  const moveSetting = event.target.closest('[data-move-setting]');
  if (moveSetting) { api(`/portal/api/settings/platforms/${moveSetting.dataset.platform}/${moveSetting.dataset.moveSetting}/${moveSetting.dataset.entry}/move`, {method:'POST', body:{direction:moveSetting.dataset.direction}}).then(render).catch(error => toast(error.message)); }
  const removeSetting = event.target.closest('[data-remove-setting]');
  if (removeSetting) { const kind=removeSetting.dataset.removeSetting, platform=removeSetting.dataset.platform, entry=removeSetting.dataset.entry; confirmAction(`Remove this ${platformName(platform)} ${kind === 'proxy' ? 'proxy' : 'cookie account'}?`, 'The remaining entries keep their current order.', `Remove ${kind === 'proxy' ? 'proxy' : 'account'}`, () => api(`/portal/api/settings/platforms/${platform}/${kind}/${entry}`, {method:'DELETE'})); }
});
document.addEventListener('submit', event => {
  if (['key-form','invite-form','request-form','email-settings-form','password-form'].includes(event.target.id) || event.target.matches('.platform-setting-form')) {
    event.preventDefault();
    if (event.target.id === 'request-form') runRequest();
    else if (event.target.id === 'email-settings-form') saveEmailSettings(event.target);
    else if (event.target.id === 'password-form') changePassword(event.target);
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
  if (historyRow && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); dialogTrigger = historyRow; showHistoryDetail(historyRow.dataset.historyId).catch(error => toast(error.message)); return; }
  if (!event.target.matches('[data-language]') || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...document.querySelectorAll('[data-language]')];
  let index = tabs.indexOf(event.target);
  index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  state.language = tabs[index].dataset.language; updateSnippet(); tabs[index].focus();
});
window.addEventListener('popstate', async event => { saveDraft(); await render(); window.scrollTo(0, event.state?.scrollY || 0); });
window.addEventListener('hashchange', () => { if (page() === 'docs' && $('#doc-main')) selectDoc(); });
render();
loadUsage().catch(() => { $('#top-requests').textContent = 'Usage could not load'; });
loadKeys().catch(() => { $('#top-key').textContent = 'View API keys'; });
