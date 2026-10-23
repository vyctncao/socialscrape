window.EndpointTests = (() => {
  // Results survive a reload in this browser; response bodies stay in memory only.
  const STORAGE_KEY = 'socialscrape.endpoint-tests.v1';
  const SETUP_ERRORS = new Set(['transcription_disabled', 'transcription_unavailable']);
  const OUTCOMES = {
    idle:['Not run', ''], queued:['Queued', ''], running:['Running', ''], passed:['Passed', 'good'], failed:['Failed', 'bad'],
    setup:['Needs setup', 'warn'], unsupported:['Not supported', ''], skipped:['Skipped', ''], stopped:['Stopped', '']
  };
  const COUNTED = ['passed', 'failed', 'setup', 'unsupported', 'skipped', 'stopped', 'idle'];
  const RETRYABLE = new Set(['failed', 'stopped', 'skipped']);
  const PLATFORMS = ['instagram', 'tiktok', 'youtube'];
  const model = {tests:[], fallback:null, filter:'all', allowFallback:false, results:{}, bodies:new Map(), finishedAt:null, stopped:false, run:null};

  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (saved?.results && typeof saved.results === 'object') {
      model.results = saved.results; model.finishedAt = saved.finishedAt ?? null; model.stopped = Boolean(saved.stopped);
    }
  } catch {}
  function persist() {
    const results = Object.fromEntries(Object.entries(model.results).filter(([, result]) => !['queued', 'running'].includes(result.outcome)));
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({results, finishedAt:model.finishedAt, stopped:model.stopped})); } catch {}
  }

  // Key paths as in the contracts: `videos[].id` is the id of any element of videos.
  function resolve(value, path) {
    let found = [value];
    for (const segment of path.split('.')) {
      const each = segment.endsWith('[]'), key = each ? segment.slice(0, -2) : segment, next = [];
      for (let item of found) {
        if (key) {
          if (!item || typeof item !== 'object' || Array.isArray(item) || !Object.hasOwn(item, key)) continue;
          item = item[key];
        }
        if (!each) next.push(item);
        else if (Array.isArray(item)) for (const element of item) next.push(element);
      }
      found = next;
    }
    return found;
  }
  const present = value => value !== null && value !== undefined && value !== '' && !(typeof value === 'object' && !Object.keys(value).length);
  function derived(text, path) {
    let data;
    try { data = JSON.parse(text); } catch { return null; }
    const value = resolve(data, path).find(item => (typeof item === 'string' && item) || (typeof item === 'number' && Number.isFinite(item)));
    return value === undefined ? null : String(value);
  }
  function judge(test, status, text) {
    let data = null;
    try { data = JSON.parse(text); } catch {}
    const body = data && typeof data === 'object' && !Array.isArray(data) ? data : {};
    if (status >= 200 && status < 300 && body.success === true) {
      const missing = test.expect.filter(path => !resolve(data, path).some(present));
      return missing.length ? {outcome:'failed', message:`The response has no ${missing.join(', ')}.`} : {outcome:'passed'};
    }
    const upstream = body.fallback && typeof body.fallback === 'object' ? ` ScrapeCreators failed too${body.fallback.error ? ` (${body.fallback.error})` : ''}.` : '';
    if (status === 501 && body.error === 'not_supported' && !test.implemented) return {outcome:'unsupported', message:upstream.trim()};
    if (status === 501 && SETUP_ERRORS.has(body.error)) return {outcome:'setup', code:body.error, message:body.message};
    if (status === 401) return {outcome:'failed', message:'Your session ended. Sign in again, then run the tests.'};
    if (status >= 200 && status < 300) return {outcome:'failed', message:data ? 'The response didn’t report success.' : 'The response wasn’t JSON.'};
    return {outcome:'failed', code:typeof body.error === 'string' ? body.error : '', message:(typeof body.message === 'string' && body.message ? body.message : `The API answered ${status}.`) + upstream};
  }

  const testFor = path => model.tests.find(test => test.path === path);
  const resultFor = path => model.results[path]?.outcome in OUTCOMES ? model.results[path] : {outcome:'idle'};
  const usable = test => Boolean(test) && resultFor(test.path).outcome === 'passed' && model.bodies.has(test.path);
  const scope = () => model.tests.filter(test => model.filter === 'all' || test.platform === model.filter);
  const pending = path => Boolean(model.run?.pending.has(path));
  const label = test => `${platformName(test.platform)} ${test.summary}`;

  function start(paths) {
    if (!paths.length || model.run?.controller.signal.aborted) return;
    const fresh = !model.run;
    const run = model.run ||= {controller:new AbortController(), allowFallback:model.allowFallback, lanes:{}, workers:new Set(), pending:new Set(), previous:{}, previousBodies:{}, ran:new Set(), total:0, done:0};
    const wanted = new Set(paths);
    const add = test => {
      if (!test || run.pending.has(test.path)) return;
      // A derived param's source runs first, in the same platform's queue, unless its last answer is still usable.
      for (const {source} of test.derive) if (wanted.has(source) || !usable(testFor(source))) add(testFor(source));
      run.pending.add(test.path);
      run.previous[test.path] ??= model.results[test.path];
      // Its last response belongs to its last result: none is shown until this run answers.
      run.previousBodies[test.path] ??= model.bodies.get(test.path);
      model.bodies.delete(test.path);
      (run.lanes[test.platform] ||= []).push(test);
      run.total++;
      setResult(test.path, {outcome:'queued'});
    };
    paths.forEach(path => add(testFor(path)));
    if (!run.total) { model.run = null; return; }
    for (const platform of Object.keys(run.lanes)) if (run.lanes[platform].length && !run.workers.has(platform)) work(run, platform);
    if (fresh) toast(`Running ${number(run.total)} endpoint ${run.total === 1 ? 'test' : 'tests'}.`);
    refresh();
  }
  function stop() {
    const run = model.run;
    if (!run) return;
    run.controller.abort();
    // Queued tests never started: they keep their last result.
    for (const lane of Object.values(run.lanes)) for (const test of lane.splice(0)) {
      run.pending.delete(test.path);
      run.total--;
      if (run.previous[test.path]) model.results[test.path] = run.previous[test.path]; else delete model.results[test.path];
      if (run.previousBodies[test.path]) model.bodies.set(test.path, run.previousBodies[test.path]);
      updateRow(test.path);
    }
    refresh();
  }
  async function work(run, platform) {
    run.workers.add(platform);
    while (run.lanes[platform].length && !run.controller.signal.aborted) {
      const test = run.lanes[platform].shift();
      await execute(test, run);
      run.ran.add(test.path);
      run.pending.delete(test.path);
      run.done++;
      const {outcome} = resultFor(test.path);
      if (['failed', 'setup'].includes(outcome)) announce(`${label(test)}: ${OUTCOMES[outcome][0].toLowerCase()}. ${number(run.done)} of ${number(run.total)} finished.`);
      updateRow(test.path);
      refresh();
    }
    run.workers.delete(platform);
    if (!run.workers.size && model.run === run) finish(run);
  }
  function finish(run) {
    model.run = null;
    model.stopped = run.controller.signal.aborted;
    if (run.done) model.finishedAt = Math.floor(Date.now() / 1000);
    persist();
    refresh();
    const counts = tally(model.tests.filter(test => run.ran.has(test.path)));
    toast(`${model.stopped ? 'Test run stopped' : 'Endpoint tests finished'}: ${['passed', 'failed', 'setup', 'unsupported', 'skipped', 'stopped'].filter(outcome => counts[outcome]).map(outcome => `${number(counts[outcome])} ${OUTCOMES[outcome][0].toLowerCase()}`).join(', ') || 'nothing ran'}.`);
  }
  async function execute(test, run) {
    const query = new URLSearchParams(test.params);
    for (const {param, source, pick} of test.derive) {
      const origin = testFor(source), value = usable(origin) ? derived(model.bodies.get(source).text, pick) : null;
      if (value === null) {
        setResult(test.path, {outcome:'skipped', at:Date.now(), message:usable(origin) ? `${origin.summary} returned no ${pick} to use.` : `Needs a passing ${origin ? origin.summary : source} test first.`});
        return;
      }
      query.set(param, value);
    }
    const url = test.path + (query.size ? `?${query}` : '');
    setResult(test.path, {outcome:'running'});
    const started = performance.now();
    try {
      const response = await fetch(url, {headers:run.allowFallback ? {} : {'x-socialscrape-fallback':'off'}, signal:run.controller.signal});
      const text = await response.text(), ms = Math.round(performance.now() - started);
      model.bodies.set(test.path, {text, url, status:response.status, ms});
      setResult(test.path, {...judge(test, response.status, text), status:response.status, ms, url, at:Date.now(), via:response.headers.get('x-socialscrape-fallback') === 'scrapecreators'});
      if (response.status === 401) stop();
    } catch {
      setResult(test.path, run.controller.signal.aborted
        ? {outcome:'stopped', at:Date.now(), message:'Stopped before the response arrived. The server may still finish this request.'}
        : {outcome:'failed', at:Date.now(), message:'Could not reach the API. Check your connection, then run the test again.'});
    }
  }
  function setResult(path, result) {
    model.results[path] = result;
    if (!['queued', 'running'].includes(result.outcome)) persist();
    updateRow(path);
  }

  function tally(tests) {
    const counts = Object.fromEntries(Object.keys(OUTCOMES).map(outcome => [outcome, 0]));
    tests.forEach(test => counts[resultFor(test.path).outcome]++);
    return counts;
  }
  function countList(counts) {
    return COUNTED.filter(outcome => counts[outcome]).map(outcome => `<li><span class="badge ${OUTCOMES[outcome][1]}">${number(counts[outcome])} ${OUTCOMES[outcome][0].toLowerCase()}</span></li>`).join('');
  }
  function statusPanel() {
    const tests = scope(), counts = tally(tests), run = model.run;
    if (run) return `<h2>Running endpoint tests</h2><p class="help">${number(run.done)} of ${number(run.total)} finished. Stopping cancels the tests that haven’t started; requests already sent may still finish on the server.</p><progress class="test-progress" max="${run.total}" value="${run.done}" aria-label="Tests finished">${number(run.done)} of ${number(run.total)}</progress>`;
    if (!tests.some(test => resultFor(test.path).outcome !== 'idle')) return `<h2>No tests run yet</h2><p class="help">Run ${tests.length === model.tests.length ? 'all' : 'these'} ${number(tests.length)} to check each endpoint with its preset parameters.</p>`;
    const when = model.finishedAt ? `${model.stopped ? 'Last run stopped' : 'Last run finished'} ${esc(fmt(model.finishedAt))}` : 'Latest results';
    return `<h2>${when}</h2><ul class="test-counts" aria-label="Results${model.filter === 'all' ? '' : ` for ${platformName(model.filter)}`}">${countList(counts)}</ul>`;
  }
  function runLabel() {
    const count = scope().length;
    return model.run ? `${icon('x')}Stop run` : `${icon('play')}Run ${number(count)} ${count === 1 ? 'test' : 'tests'}`;
  }
  function retryLabel() {
    const retry = scope().filter(test => RETRYABLE.has(resultFor(test.path).outcome));
    return {hidden:Boolean(model.run) || !retry.length, text:`Retry ${number(retry.length)} ${retry.every(test => resultFor(test.path).outcome === 'failed') ? 'failed' : 'unfinished'}`};
  }
  function announce(message) {
    const region = document.getElementById('test-announcer');
    if (region) region.textContent = message;
  }
  function refresh() {
    const panel = document.getElementById('test-status');
    if (!panel) return;
    panel.innerHTML = statusPanel();
    document.getElementById('test-run-all').innerHTML = runLabel();
    const retryButton = document.getElementById('test-retry'), retry = retryLabel();
    retryButton.hidden = retry.hidden; retryButton.textContent = retry.text;
    for (const platform of PLATFORMS) {
      const caption = document.querySelector(`[data-group-summary="${platform}"]`);
      if (caption) caption.textContent = groupSummary(model.tests.filter(test => test.platform === platform));
    }
  }
  function groupSummary(tests) {
    const counts = tally(tests);
    const parts = ['passed', 'failed', 'setup', 'unsupported'].filter(outcome => counts[outcome]).map(outcome => `${number(counts[outcome])} ${OUTCOMES[outcome][0].toLowerCase()}`);
    return `${number(tests.length)} ${tests.length === 1 ? 'endpoint' : 'endpoints'}${parts.length ? ` · ${parts.join(' · ')}` : ''}`;
  }
  function paramsCell(test) {
    const items = Object.entries(test.params).map(([name, value]) => `<code>${esc(name)}=${esc(value)}</code>`)
      .concat(test.derive.map(({param, source}) => `<span><code>${esc(param)}</code> <span class="help">from ${esc(testFor(source)?.summary || source)}</span></span>`));
    return `<div class="test-params">${items.join('') || '<span class="help">No parameters</span>'}${test.note ? `<small>${esc(test.note)}</small>` : ''}</div>`;
  }
  function resultCell(test) {
    const result = resultFor(test.path), [name, tone] = OUTCOMES[result.outcome] || OUTCOMES.idle;
    const spinner = result.outcome === 'running' ? icon('circle-notch') : '';
    const status = result.status && !['passed', 'unsupported'].includes(result.outcome) ? ` · ${result.status}` : '';
    const message = result.message || (!test.implemented && ['idle', 'unsupported'].includes(result.outcome) ? 'Not implemented on this server, which answers 501.' : '');
    const time = result.ms === undefined || ['queued', 'running'].includes(result.outcome) ? '' : `<span class="test-time">${number(result.ms)} ms</span>`;
    return `<div class="test-result"><span class="badge ${tone}">${spinner}${name}${status}</span>${time}${result.via ? '<span class="badge">via ScrapeCreators</span>' : ''}${result.code ? `<code>${esc(result.code)}</code>` : ''}${message ? `<small>${esc(message)}</small>` : ''}</div>`;
  }
  function row(test) {
    const path = esc(test.path), name = esc(label(test));
    return `<tr data-test-row="${path}"><td><div class="test-endpoint"><strong>${esc(test.summary)}</strong><code>${path.replaceAll('/', '/<wbr>')}</code></div></td><td>${paramsCell(test)}</td><td data-cell="result">${resultCell(test)}</td><td><div class="table-actions"><button class="ghost" type="button" data-test-run="${path}" aria-label="Run the ${name} test" aria-disabled="${pending(test.path)}">${icon('play')}Run</button><button class="icon-button" type="button" data-test-response="${path}" aria-label="View the ${name} response" title="View response" ${model.bodies.has(test.path) ? '' : 'hidden'}>${icon('file-text')}</button><a class="icon-button" href="/app/playground" data-test-playground="${path}" aria-label="Open ${name} in the playground" title="Open in playground">${icon('arrow-square-out')}</a></div></td></tr>`;
  }
  function updateRow(path) {
    const element = document.querySelector(`[data-test-row="${CSS.escape(path)}"]`), test = testFor(path);
    if (!element || !test) return;
    element.querySelector('[data-cell="result"]').innerHTML = resultCell(test);
    element.querySelector('[data-test-run]').setAttribute('aria-disabled', String(pending(path)));
    element.querySelector('[data-test-response]').hidden = !model.bodies.has(path);
  }

  async function renderPage() {
    if (!model.run) {
      const data = await api('/portal/api/endpoint-tests');
      model.tests = data.tests; model.fallback = data.fallback;
    }
    if (!model.fallback) model.allowFallback = false;
    // Within a platform, what this server implements comes first.
    const groups = PLATFORMS.map(platform => [platform, model.tests.filter(test => test.platform === platform).sort((a, b) => b.implemented - a.implemented)]).filter(([, tests]) => tests.length);
    const retry = retryLabel(), filters = [['all', 'All', model.tests.length], ...groups.map(([platform, tests]) => [platform, platformName(platform), tests.length])];
    const fallbackNote = model.fallback
      ? `<label class="test-option" for="test-allow-fallback"><input id="test-allow-fallback" type="checkbox" ${model.allowFallback ? 'checked' : ''} aria-describedby="test-fallback-help"><span><strong>Allow ScrapeCreators fallback</strong><small id="test-fallback-help">Off: tests check this server alone and spend no credits. On: unsupported and failing endpoints go to ScrapeCreators, as your integrations’ requests do, which can spend credits. Applies to the next run you start.</small></span></label>`
      : '<p class="help">The ScrapeCreators fallback is off, so tests check this server alone.</p>';
    return layout('Endpoint tests', 'Run every endpoint with preset parameters to see what’s working right now.', `
      <section class="card test-run" aria-label="Test run">
        <div class="test-run-head"><div class="test-status" id="test-status">${statusPanel()}</div><div class="test-run-actions"><button class="ghost" type="button" id="test-retry" ${retry.hidden ? 'hidden' : ''}>${retry.text}</button><button class="primary" type="button" id="test-run-all">${runLabel()}</button></div></div>
        <div class="test-run-options"><fieldset class="test-filter"><legend class="sr-only">Platform</legend>${filters.map(([value, name, count]) => `<label><input type="radio" name="test-platform" value="${value}" ${model.filter === value ? 'checked' : ''}><span>${name}</span><small>${number(count)}</small></label>`).join('')}</fieldset>${fallbackNote}</div>
        <p class="sr-only" id="test-announcer" role="status" aria-live="polite"></p>
        <p class="help">Each test is a live request with your workspace session, so it also appears in Request history. Platforms run side by side; each platform’s tests run one at a time.</p>
      </section>
      ${groups.map(([platform, tests]) => `<section class="test-group" data-test-group="${platform}" ${model.filter === 'all' || model.filter === platform ? '' : 'hidden'}><div class="table-caption"><h2>${platformName(platform)}</h2><span data-group-summary="${platform}">${groupSummary(tests)}</span></div>${tableRegion(`${platformName(platform)} endpoint tests`, `<table><thead><tr><th scope="col">Endpoint</th><th scope="col">Preset parameters</th><th scope="col">Result</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead><tbody>${tests.map(row).join('')}</tbody></table>`)}</section>`).join('')}`);
  }
  function showResponse(path) {
    const test = testFor(path), body = model.bodies.get(path), result = resultFor(path);
    if (!test || !body) return;
    let formatted = body.text;
    try { formatted = JSON.stringify(JSON.parse(body.text), null, 2); } catch {}
    openDialog({title:label(test), description:`${(OUTCOMES[result.outcome] || OUTCOMES.idle)[0]} · ${body.status} · ${number(body.ms)} ms${result.via ? ' · ScrapeCreators fallback' : ''}`, body:`<dl class="history-facts"><div><dt>Request</dt><dd><code>${esc(body.url)}</code></dd></div><div><dt>Must include</dt><dd>${test.expect.map(path => `<code>${esc(path)}</code>`).join(' ') || 'success: true'}</dd></div><div><dt>Checked</dt><dd>${esc(new Date(result.at).toLocaleString())}</dd></div></dl>${result.message ? `<div class="inline-note test-response-note">${esc(result.message)}</div>` : ''}<pre class="code history-response" id="test-response" tabindex="0"></pre>`, footer:`<button class="ghost" data-dialog-close>Close</button><button class="primary" data-copy="test-response">${icon('copy')}Copy response</button>`});
    document.getElementById('test-response').textContent = formatted || 'The response was empty.';
  }
  function prefill(test) {
    const values = {...test.params};
    for (const {param, source, pick} of test.derive) {
      const value = usable(testFor(source)) ? derived(model.bodies.get(source).text, pick) : null;
      if (value !== null) values[param] = value;
    }
    state.drafts[test.path] = values; state.platform = test.platform; state.endpoint = test.path;
  }

  document.addEventListener('click', event => {
    if (event.target.closest('#test-run-all')) { if (model.run) stop(); else start(scope().map(test => test.path)); return; }
    if (event.target.closest('#test-retry')) {
      start(scope().filter(test => RETRYABLE.has(resultFor(test.path).outcome)).map(test => test.path));
      document.getElementById('test-run-all')?.focus();
      return;
    }
    const one = event.target.closest('[data-test-run]');
    if (one) { if (one.getAttribute('aria-disabled') !== 'true') start([one.dataset.testRun]); return; }
    const view = event.target.closest('[data-test-response]');
    if (view) { showResponse(view.dataset.testResponse); return; }
    const link = event.target.closest('[data-test-playground]');
    if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && event.button === 0) {
      const test = testFor(link.dataset.testPlayground);
      if (test) prefill(test);
      // portal.js navigates links under /app; if it already has, don't navigate twice.
      if (!event.defaultPrevented) { event.preventDefault(); go(link.getAttribute('href')); }
      event.stopImmediatePropagation();
    }
  });
  document.addEventListener('change', event => {
    if (event.target.name === 'test-platform') {
      model.filter = event.target.value;
      document.querySelectorAll('[data-test-group]').forEach(group => { group.hidden = model.filter !== 'all' && group.dataset.testGroup !== model.filter; });
      refresh();
    }
    if (event.target.id === 'test-allow-fallback') model.allowFallback = event.target.checked;
  });

  return {render:renderPage};
})();
