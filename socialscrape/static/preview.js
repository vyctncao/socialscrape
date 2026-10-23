/* The playground's response preview: a readable summary of an API response (a reel's views, likes, saves and
   audio, a profile, a list of videos, comments, a transcript) with the raw JSON one tab away. Each endpoint maps
   its response onto a few shared views; any other JSON gets a summary of its top-level fields. Images and links
   come from the platforms, so only http(s) URLs are used, and links open in a new tab without a referrer. */
const ResponsePreview = (() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sprite = () => `/assets/brand/icons.svg?v=${encodeURIComponent(typeof document === 'undefined' ? '' : document.documentElement.dataset.assetVersion || '')}`;
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="${sprite()}#${name}"></use></svg>`;
  const LIST_LIMIT = 24;
  const FIELD_LIMIT = 500;

  // ---- Reading values --------------------------------------------------------------------------------------
  const get = (value, path) => path.split('.').reduce((node, key) => node == null ? undefined : node[key], value);
  const present = value => value !== undefined && value !== null && value !== '';
  const pick = (...values) => values.find(present);
  const text = value => typeof value === 'string' ? value.trim() : typeof value === 'number' && Number.isFinite(value) ? String(value) : '';
  const list = value => Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : [];
  function count(value) {
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value === 'string' && /^\d+$/.test(value.trim())) return Number(value);
    return null;
  }
  // YouTube's display counts: "1,008 videos" is exact, "523M subscribers" is rounded.
  function textCount(value) {
    const match = text(value).match(/^([\d][\d.,]*)\s*([KMB])?\b/i);
    if (!match) return null;
    if (!match[2]) return {value:Number(match[1].replace(/\D/g, '')), approx:false};
    return {value:Math.round(parseFloat(match[1].replace(/,/g, '')) * {K:1e3, M:1e6, B:1e9}[match[2].toUpperCase()]), approx:true};
  }
  const imageUrl = value => typeof value === 'string' && /^https:\/\/[^\s"'<>\\]+$/i.test(value) ? value : null;
  const linkUrl = value => typeof value === 'string' && /^https?:\/\/[^\s"'<>\\]+$/i.test(value) ? value : null;
  // The first candidate that is a usable link or image, so a rejected value can't hide a good fallback.
  const pickUrl = (...values) => values.find(linkUrl);
  const pickImage = (...values) => values.find(imageUrl);
  const compact = value => new Intl.NumberFormat(undefined, {notation:'compact', maximumFractionDigits:1}).format(value);
  const exact = value => Number(value).toLocaleString();
  const plural = (n, one, many = `${one}s`) => `${exact(n)} ${n === 1 ? one : many}`;

  function toDate(value, unit = 's') {
    if (!present(value)) return null;
    let date = null;
    if (typeof value === 'number' || /^\d+$/.test(String(value))) {
      const n = Number(value);
      if (n > 0) date = new Date(unit === 'ms' || n > 1e12 ? n : n * 1000);
    } else if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}/.test(value)) date = new Date(value);
    return date && !Number.isNaN(date.getTime()) ? date : null;
  }
  // An exact date (unix time or ISO), or the platform's own wording ("5 days ago") when the date is an estimate.
  function when(value, wording) {
    const date = toDate(value);
    if (date) return {label:date.toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'}), iso:date.toISOString(), title:date.toLocaleString()};
    const words = text(wording);
    return words ? {label:words} : null;
  }
  const approxWhen = (iso, wording) => text(wording) ? {label:text(wording)} : when(iso);
  const stamp = seconds => Number(seconds) === 0 ? '0:00' : clock(seconds);
  function clock(seconds) {
    const total = Math.round(Number(seconds));
    if (!Number.isFinite(total) || total <= 0) return '';
    const h = Math.floor(total / 3600), m = Math.floor(total % 3600 / 60), s = total % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  }

  /* Views share one model:
     media   {type, url, thumb, shape:'vertical'|'wide'|'square', title, caption, author, created, duration, stats, audio, facts, note,
              tags, sections}: `tags` render as chips, `sections` are finished blocks (galleries, chapters, comments)
     author  {name, handle, avatar, verified, url, followers}
     stat    {key, label, value, always, approx}: value null means the response didn't include it; `always` keeps
             its tile visible as "Not returned" where the platform normally reports it
     profile {name, handle, avatar, verified, bio, url, links, stats, facts, badges, status, sections} */
  const STAT = {views:['Views','eye'], plays:['Plays','play'], likes:['Likes','heart'], comments:['Comments','chat-circle'], shares:['Shares','paper-plane-tilt'], saves:['Saves','bookmark-simple'], replies:['Replies','chat-circle'], followers:['Followers','users-three'], following:['Following','user-circle'], posts:['Posts','image-square'], videos:['Videos','film-strip'], subscribers:['Subscribers','users-three'], total_likes:['Likes','heart'], total_views:['Views','eye'], viewers:['Viewers','eye'], items:['Videos','film-strip']};
  const stat = (key, value, {label, always = false, approx = false} = {}) => ({key, label:label || STAT[key][0], value:count(value), always, approx});
  function textStat(key, value, options = {}) {
    const parsed = textCount(value);
    return stat(key, parsed ? parsed.value : null, {...options, approx:parsed ? parsed.approx : false});
  }

  // ---- Rendering -------------------------------------------------------------------------------------------
  const statIcon = key => icon((STAT[key] || [, 'chart-bar'])[1]);
  function metricTiles(stats, label = 'Engagement') {
    const shown = stats.filter(item => item && (item.value !== null || item.always));
    if (!shown.length) return '';
    return `<dl class="metric-tiles" aria-label="${esc(label)}">${shown.map(item => {
      const head = `<dt>${statIcon(item.key)}${esc(item.label)}</dt>`;
      if (item.value === null) return `<div class="metric">${head}<dd><strong aria-hidden="true">—</strong><small>Not returned</small></dd></div>`;
      const short = compact(item.value), full = exact(item.value);
      if (item.approx) return `<div class="metric">${head}<dd><strong>${short}</strong><small>Approximate</small></dd></div>`;
      return `<div class="metric">${head}<dd>${short === full ? `<strong>${full}</strong>` : `<strong aria-hidden="true">${short}</strong><small>${full}</small>`}</dd></div>`;
    }).join('')}</dl>`;
  }
  function inlineStats(stats) {
    const shown = stats.filter(item => item && item.value !== null);
    return shown.length ? `<span class="inline-stats">${shown.map(item => `<span title="${esc(`${item.approx ? 'About ' : ''}${exact(item.value)} ${item.label.toLowerCase()}`)}">${statIcon(item.key)}<span class="sr-only">${esc(item.label)}: </span>${compact(item.value)}</span>`).join('')}</span>` : '';
  }
  const at = handle => handle ? `@${String(handle).replace(/^@/, '')}` : '';
  const verifiedMark = '<span class="verified" title="Verified">' + icon('check-circle') + '<span class="sr-only">Verified</span></span>';
  function avatar(src, name, size = '') {
    const url = imageUrl(src);
    const initial = esc(([...text(name).replace(/^@/, '')][0] || '?').toUpperCase());
    return `<span class="preview-avatar ${size}" aria-hidden="true">${initial}${url ? `<img src="${esc(url)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : ''}</span>`;
  }
  const external = (url, label, className = '') => linkUrl(url) ? `<a${className ? ` class="${className}"` : ''} href="${esc(linkUrl(url))}" target="_blank" rel="noopener noreferrer">${label}</a>` : label;
  function byline(author, extra = '') {
    if (!author || !(author.name || author.handle)) return '';
    const handle = at(author.handle), name = text(author.name) || handle;
    const meta = [handle && handle !== name ? esc(handle) : '', author.followers != null ? `${compact(author.followers)} followers` : '', extra].filter(Boolean).join(' · ');
    return `<div class="byline">${avatar(author.avatar, name)}<div><span class="byline-name">${external(author.url, esc(name))}${author.verified ? verifiedMark : ''}</span>${meta ? `<span class="byline-meta">${meta}</span>` : ''}</div></div>`;
  }
  const time = moment => moment ? (moment.iso ? `<time datetime="${esc(moment.iso)}" title="${esc(moment.title)}">${esc(moment.label)}</time>` : esc(moment.label)) : '';
  function thumb(item, size = '') {
    const src = imageUrl(item.thumb), url = linkUrl(item.url);
    const alt = src ? text(item.alt) : '', hidden = alt ? '' : ' aria-hidden="true"';
    const inner = `${icon(item.glyph || 'image-square')}${src ? `<img src="${esc(src)}" alt="${esc(alt)}" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : ''}`;
    const className = `media-thumb ${item.shape || 'wide'} ${size}`.trim();
    return url ? `<a class="${className}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" tabindex="-1"${hidden}>${inner}</a>` : `<span class="${className}"${hidden}>${inner}</span>`;
  }
  // Escapes text, then makes its web addresses clickable. It walks whitespace-separated words once, because
  // captions are untrusted and a backtracking pattern can stall the page on input like "https://" + many dots.
  function richText(value) {
    return esc(value).split(/(\s+)/).map(word => {
      if (!/^https?:\/\//i.test(word)) return word;
      const entity = word.search(/&(?:quot|#39|lt|gt);/);  // a quote or bracket ends the address
      const candidate = entity < 0 ? word : word.slice(0, entity);
      let end = candidate.length;
      while (end > 0 && '.,!?;:)]'.includes(candidate[end - 1])) end--;
      const url = candidate.slice(0, end);
      if (!/^https?:\/\/[^/]/i.test(url)) return word;
      return `<a class="text-link" href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>${word.slice(end)}`;
    }).join('');
  }
  // Long text collapses behind a "Show more" button. Each one gets an id unique across mounts, and the button names
  // what it expands ("Show more of the caption for …"), since a list can hold many.
  let collapsibleCount = 0;
  function caption(value, className = 'preview-caption', subject = 'this text') {
    const body = text(value);
    if (!body) return '';
    if (body.length <= 280 && body.split('\n').length <= 5) return `<p class="${className}">${richText(body)}</p>`;
    const id = `preview-text-${++collapsibleCount}`;
    const about = subject.length > 90 ? `${subject.slice(0, 90)}…` : subject;
    return `<p class="${className}" id="${id}" data-collapsible>${richText(body)}</p><button class="text-button" type="button" data-expand data-subject="${esc(about)}" aria-controls="${id}" aria-expanded="false" aria-label="${esc(`Show more of ${about}`)}">Show more</button>`;
  }
  const captionFor = (kind, name) => text(name) ? `the ${kind} for ${text(name)}` : `the ${kind}`;
  function audioRow(audio) {
    if (!audio || !(text(audio.title) || text(audio.artist))) return '';
    const parts = [text(audio.artist) ? esc(audio.artist) : '', audio.duration ? esc(audio.duration) : ''].filter(Boolean).join(' · ');
    const badge = audio.original === true ? '<span class="badge">Original audio</span>' : '';
    const id = present(audio.id) ? `<span>ID <code>${esc(audio.id)}</code></span>` : '';
    return `<div class="audio-row"><span class="audio-glyph">${icon('waveform')}</span><div><span class="audio-label">Audio</span><strong>${external(audio.url, esc(text(audio.title) || 'Untitled audio'))}</strong>${parts ? `<span class="byline-meta">${parts}</span>` : ''}</div>${badge || id ? `<div class="audio-tags">${badge}${id}</div>` : ''}</div>`;
  }
  // [{label, value}] with value as plain text, or {label, html} for markup that is already escaped.
  function facts(rows) {
    const shown = rows.filter(row => row && (row.html || present(row.value)));
    return shown.length ? `<dl class="fact-list">${shown.map(row => `<div><dt>${esc(row.label)}</dt><dd>${row.html || esc(row.value)}</dd></div>`).join('')}</dl>` : '';
  }
  const openLink = (url, label) => linkUrl(url) ? `<a class="text-link open-link" href="${esc(linkUrl(url))}" target="_blank" rel="noopener noreferrer">${esc(label)}${icon('arrow-square-out')}</a>` : '';
  const empty = (glyph, message) => `<div class="preview-empty">${icon(glyph)}<p>${esc(message)}</p></div>`;
  const section = (title, badge = '') => `<div class="list-head"><h3>${esc(title)}</h3>${badge}</div>`;
  // A titled block, left out entirely when it has nothing to show.
  const block = (title, html, badge = '') => html ? `<section class="preview-section">${section(title, badge)}${html}</section>` : '';
  const countBadge = n => `<span class="badge">${exact(n)}</span>`;
  function chips(values) {
    const shown = [...new Set((values || []).map(text).filter(Boolean))];
    return shown.length ? `<ul class="chip-list">${shown.slice(0, 40).map(value => `<li class="badge">${esc(value)}</li>`).join('')}</ul>` : '';
  }
  // Image-first items (carousel slides, recent posts, highlights) as a grid of linked tiles.
  function galleryTile(item) {
    const url = linkUrl(item.url), label = text(item.title) || text(item.caption).split('\n')[0] || text(item.type);
    const name = url ? `<a class="gallery-label" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label || 'Open')}</a>` : label ? `<span class="gallery-label">${esc(label)}</span>` : '';
    return `<li class="gallery-tile">${thumb({...item, shape:item.shape || 'square'})}${name}${item.type && label !== item.type ? `<span class="byline-meta">${esc([item.type, item.duration].filter(Boolean).join(' · '))}</span>` : ''}${inlineStats(item.stats || [])}${(item.facts || []).filter(row => row && present(row.value)).map(row => `<span class="byline-meta gallery-fact">${esc(row.label)}: ${esc(row.value)}</span>`).join('')}</li>`;
  }
  function gallery(items) {
    if (!items.length) return '';
    const shown = items.slice(0, LIST_LIMIT), rest = items.slice(LIST_LIMIT);
    return `<ul class="media-gallery">${shown.map(galleryTile).join('')}</ul>${rest.length ? `<template data-rest>${rest.map(galleryTile).join('')}</template><button class="ghost show-all" type="button" data-show-all>Show all ${exact(items.length)}</button>` : ''}`;
  }
  // Every scalar field of a response, flattened, so nothing is only reachable through the raw JSON.
  const humanKey = key => String(key).replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_+/g, ' ').trim();
  function fieldRows(body, limit = Infinity) {
    const rows = [], stack = [[body, []]];
    while (stack.length && rows.length < limit) {
      const [value, path] = stack.pop();
      if (Array.isArray(value)) {
        if (value.every(item => item === null || typeof item !== 'object')) { if (value.some(present)) rows.push({path, value:value.filter(present).join(', ')}); continue; }
        for (let index = value.length - 1; index >= 0; index--) stack.push([value[index], [...path, String(index + 1)]]);
      } else if (value && typeof value === 'object') {
        const entries = Object.entries(value).filter(([key]) => path.length || !ENVELOPE.has(key));
        for (let index = entries.length - 1; index >= 0; index--) stack.push([entries[index][1], [...path, entries[index][0]]]);
      } else if (present(value)) rows.push({path, value});
    }
    return rows;
  }
  function fieldList(rows) {
    const cell = value => {
      if (typeof value === 'boolean') return value ? 'Yes' : 'No';
      const words = String(value);
      if (linkUrl(words)) return `<a class="text-link" href="${esc(words)}" target="_blank" rel="noopener noreferrer">${esc(words.length > 80 ? `${words.slice(0, 80)}…` : words)}</a>`;
      return esc(words.length > 300 ? `${words.slice(0, 300)}…` : words);
    };
    const row = item => `<div><dt>${esc(item.path.map(humanKey).join(' › '))}</dt><dd>${cell(item.value)}</dd></div>`;
    const shown = rows.slice(0, FIELD_LIMIT), rest = rows.slice(FIELD_LIMIT);
    return `<dl class="fact-list">${shown.map(row).join('')}</dl>${rest.length ? `<template data-rest>${rest.map(row).join('')}</template><button class="ghost show-all" type="button" data-show-all>Show all ${exact(rows.length)} fields</button>` : ''}`;
  }
  // `lazy` only checks that there is a field at all: the rows are walked and rendered once, when the disclosure is
  // first opened (see mount), since a list response can have thousands.
  function allFields(body, {lazy = false} = {}) {
    if (lazy) return fieldRows(body, 1).length ? `<details class="all-fields" data-all-fields><summary>${icon('caret-down')}All fields</summary></details>` : '';
    const rows = fieldRows(body);
    return rows.length ? `<details class="all-fields"><summary>${icon('caret-down')}All fields <span class="badge">${exact(rows.length)}</span></summary>${fieldList(rows)}</details>` : '';
  }


  function mediaCard(item, platform) {
    const meta = [item.type ? `<span class="badge">${esc(item.type)}</span>` : '', item.created ? `<span>${icon('calendar-blank')}${time(item.created)}</span>` : '', item.duration ? `<span>${icon('clock')}${esc(item.duration)}</span>` : '', item.note ? `<span>${esc(item.note)}</span>` : ''].filter(Boolean).join('');
    return `<article class="media-card">${thumb(item)}<div class="media-body">${byline(item.author)}${item.title ? `<h3 class="media-title">${esc(item.title)}</h3>` : ''}${caption(item.caption, 'preview-caption', captionFor('caption', item.title || at(item.author?.handle) || item.author?.name))}${chips(item.tags)}${meta ? `<div class="media-meta">${meta}</div>` : ''}${openLink(item.url, `Open on ${platform}`)}</div></article>${metricTiles(item.stats || [])}${audioRow(item.audio)}${facts(item.facts || [])}${(item.sections || []).join('')}`;
  }
  function profileCard(profile, platform) {
    const handle = at(profile.handle), name = text(profile.name) || handle || 'Profile';
    const links = [...new Set((profile.links || []).map(linkUrl).filter(Boolean))];
    const status = profile.status ? `<span class="badge ${profile.status.tone || ''}">${esc(profile.status.label)}</span>` : '';
    const badges = (profile.badges || []).filter(Boolean).map(label => `<span class="badge">${esc(label)}</span>`).join('');
    return `<article class="profile-head">${avatar(profile.avatar, name, 'xl')}<div class="media-body"><div><h3 class="media-title">${esc(name)}${profile.verified ? verifiedMark : ''}${status}${badges}</h3>${handle && handle !== name ? `<span class="byline-meta">${esc(handle)}</span>` : ''}</div>${caption(profile.bio, 'preview-caption', captionFor('bio', name))}${links.length ? `<div class="media-meta">${links.map(url => `<a class="text-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, ''))}</a>`).join('')}</div>` : ''}${openLink(profile.url, `Open on ${platform}`)}</div></article>${metricTiles(profile.stats || [], 'Profile statistics')}${facts(profile.facts || [])}${(profile.sections || []).join('')}`;
  }
  function itemRow(item) {
    const who = item.author?.byName ? text(item.author.name) || at(item.author.handle) : at(item.author?.handle) || text(item.author?.name);
    const meta = [esc(who), item.type ? esc(item.type) : '', time(item.created), item.duration ? esc(item.duration) : '', item.note ? esc(item.note) : ''].filter(Boolean).join(' · ');
    const firstLine = text(item.caption).split('\n')[0];
    const title = text(item.title) || firstLine || [item.type || 'Item', item.code].filter(Boolean).join(' ');
    const rest = text(item.title) ? text(item.caption) : text(item.caption).slice(firstLine.length).trim();
    const details = (item.facts || []).filter(row => row && present(row.value)).map(row => `${esc(row.label)} <code>${esc(row.value)}</code>`).join(' · ');
    const url = linkUrl(item.url);
    return `<li class="item-row">${item.round ? avatar(item.thumb, title) : thumb(item, 'small')}<div class="item-body">${url ? `<a class="item-title" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>` : `<span class="item-title">${esc(title)}</span>`}${meta ? `<span class="byline-meta">${meta}</span>` : ''}${caption(rest, 'item-caption', captionFor('caption', title))}${item.audio && text(item.audio.title) ? `<span class="byline-meta">${icon('waveform')}${esc(item.audio.title)}${text(item.audio.artist) ? ` · ${esc(item.audio.artist)}` : ''}</span>` : ''}${inlineStats(item.stats || [])}${details ? `<span class="byline-meta">${details}</span>` : ''}</div></li>`;
  }
  function commentRow(comment) {
    const name = at(comment.author?.handle) || text(comment.author?.name) || 'Unknown account';
    const meta = [time(comment.created), comment.likes != null ? `<span>${icon('heart')}<span class="sr-only">Likes: </span>${compact(comment.likes)}</span>` : '', comment.replies ? `<span>${plural(comment.replies, 'reply', 'replies')}</span>` : ''].filter(Boolean).join('<span aria-hidden="true">·</span>');
    return `<li class="comment-row">${avatar(comment.author?.avatar, name)}<div class="item-body"><span class="byline-name">${external(comment.author?.url, esc(name))}${comment.author?.verified ? verifiedMark : ''}${comment.creator ? '<span class="badge">Creator</span>' : ''}</span>${text(comment.text) ? `<p class="comment-text">${esc(comment.text)}</p>` : ''}${meta ? `<span class="comment-meta">${meta}</span>` : ''}</div></li>`;
  }
  function itemList(items, {noun = 'item', nouns, render = itemRow, total, more, heading = true} = {}) {
    nouns = nouns || `${noun}s`;
    const title = total != null && total > items.length ? `${exact(items.length)} of ${exact(total)} ${nouns}` : plural(items.length, noun, nouns);
    const head = heading ? section(title, more ? '<span class="badge">More available</span>' : '') : '';
    if (!items.length) return `${head}${empty('magnifying-glass', `No ${nouns} in this response.`)}`;
    const shown = items.slice(0, LIST_LIMIT), rest = items.slice(LIST_LIMIT);
    return `${head}<ul class="item-list">${shown.map(render).join('')}</ul>${rest.length ? `<template data-rest>${rest.map(render).join('')}</template><button class="ghost show-all" type="button" data-show-all>Show all ${exact(items.length)} ${esc(nouns)}</button>` : ''}`;
  }
  function nextPage(param, value, more = true) {
    const token = text(value);
    if (!token || !more || more === '0') return '';
    return `<div class="inline-note">${icon('arrow-right')}<span>More results are available. Pass <code>${esc(param)}</code> from the raw response to get the next page${token.length <= 40 ? `: <code class="cursor-value">${esc(token)}</code>` : ''}.</span></div>`;
  }
  function transcriptView(segments, {language, plain, missing}) {
    const badge = language ? `<span class="badge">${esc(language)}</span>` : '';
    if (segments.length) return `${section(`Transcript · ${plural(segments.length, 'line')}`, badge)}<ol class="transcript">${segments.map(line => `<li><span class="timestamp">${esc(line.time || '')}</span><span>${esc(line.text)}</span></li>`).join('')}</ol>`;
    if (text(plain)) return `${section('Transcript', badge)}<p class="transcript-text">${esc(plain)}</p>`;
    return empty('closed-captioning', missing || 'No transcript is available for this video.');
  }
  function errorView(status, body) {
    const message = text(body?.message) || text(body?.detail) || 'The request did not succeed.';
    const code = text(body?.error);
    return `<div class="preview-error" role="alert">${icon('warning-circle')}<div><strong>${esc([status, code ? code.replace(/_/g, ' ') : 'Request failed'].filter(Boolean).join(' · '))}</strong><p>${esc(message)}</p></div></div>`;
  }
  // Top-level fields of any JSON object, for responses without a tailored view.
  const ENVELOPE = new Set(['success', 'credits_charged', 'credits_remaining', 'cached', 'cached_at']);
  function summary(body) {
    const rows = [];
    for (const [key, value] of Object.entries(body)) {
      if (ENVELOPE.has(key) || rows.length >= 24) continue;
      if (Array.isArray(value)) rows.push({label:key, value:plural(value.length, 'item')});
      else if (value && typeof value === 'object') rows.push({label:key, value:plural(Object.keys(value).length, 'field')});
      else if (typeof value === 'boolean') rows.push({label:key, value:value ? 'Yes' : 'No'});
      else if (present(value)) rows.push({label:key, value:String(value).length > 160 ? `${String(value).slice(0, 160)}…` : String(value)});
    }
    return rows.length ? `${section('Response fields')}${facts(rows)}` : empty('file-text', 'This response has no fields to preview.');
  }

  // ---- Instagram -------------------------------------------------------------------------------------------
  const igProfileUrl = handle => text(handle) ? `https://www.instagram.com/${encodeURIComponent(text(handle).replace(/^@/, ''))}/` : null;
  const igAuthor = user => {
    if (!user || typeof user !== 'object') return null;
    const handle = pick(user.username, user.handle);
    const hd = list(user.hd_profile_pic_versions);
    return {name:pick(user.full_name, user.name), handle, avatar:pickImage(get(user, 'hd_profile_pic_url_info.url'), hd.length ? hd[hd.length - 1].url : null, user.profile_pic_url), verified:user.is_verified === true, url:igProfileUrl(handle), followers:count(pick(user.follower_count, get(user, 'edge_followed_by.count')))};
  };
  function igType(m) {
    if (m.product_type === 'clips' || m.type === 'reel' || /\/reels?\//.test(text(m.url))) return 'Reel';
    if (m.product_type === 'carousel_container' || m.media_type === 8 || /Sidecar/.test(m.__typename || '')) return 'Carousel';
    if (m.is_video === true || m.media_type === 2 || /Video/.test(m.__typename || '')) return 'Video';
    if (/story/i.test(text(m.type))) return text(m.type);
    return 'Photo';
  }
  const igCaption = m => pick(get(m, 'edge_media_to_caption.edges.0.node.text'), typeof m.caption === 'string' ? m.caption : get(m, 'caption.text'));
  const igHashtags = value => [...new Set((text(value).match(/#[\p{L}\p{M}\p{N}_]+/gu) || []))];
  const igMentions = value => [...new Set((text(value).match(/@[A-Za-z0-9._]+/g) || []))];
  function igBestImage(m) {
    const candidates = list(get(m, 'image_versions2.candidates'));
    if (candidates.length) return candidates.reduce((best, candidate) => (Number(candidate.width) || 0) * (Number(candidate.height) || 0) > (Number(best.width) || 0) * (Number(best.height) || 0) ? candidate : best, candidates[0]).url;
    const resources = list(m.thumbnail_resources);
    if (resources.length) return resources.reduce((best, candidate) => (Number(candidate.config_width) || 0) * (Number(candidate.config_height) || 0) > (Number(best.config_width) || 0) * (Number(best.config_height) || 0) ? candidate : best, resources[0]).src;
    return pick(m.thumbnail_src, m.display_url, m.display_uri, get(m, 'cover_media.cropped_image_version.url'));
  }
  function igAudio(m) {
    const music = m.clips_music_attribution_info || m.music_metadata;
    if (!music || typeof music !== 'object') return null;
    return {title:pick(music.song_name, music.title, get(music, 'music_info.music_asset_info.title'), get(music, 'original_sound_info.original_audio_title')), artist:pick(music.artist_name, music.artist, get(music, 'music_info.music_asset_info.display_artist'), get(music, 'original_sound_info.ig_artist.username')), original:pick(music.uses_original_audio, !!music.original_sound_info), id:pick(music.audio_id, music.music_canonical_id, get(music, 'music_info.music_asset_info.audio_asset_id'))};
  }
  function igStats(m, video = /Reel|Video/.test(igType(m))) {
    return [
      m.video_view_count != null && stat('views', m.video_view_count),
      stat('plays', pick(m.video_play_count, m.ig_play_count, m.play_count), {always:video}),
      stat('likes', pick(get(m, 'edge_media_preview_like.count'), get(m, 'edge_liked_by.count'), m.like_count), {always:true}),
      stat('comments', pick(get(m, 'edge_media_to_parent_comment.count'), get(m, 'edge_media_preview_comment.count'), get(m, 'edge_media_to_comment.count'), m.comment_count), {always:true}),
      stat('shares', pick(m.share_count, m.reshare_count)), stat('saves', pick(m.save_count, m.saved_count)),
    ].filter(Boolean);
  }
  function igMedia(m, forcedType) {
    const type = forcedType || igType(m), code = pick(m.shortcode, m.code), captionText = igCaption(m), video = /Reel|Video/.test(type);
    const owner = m.owner || m.user;
    return {type, code, shape:video || /story/i.test(type) ? 'vertical' : 'square',
      url:pickUrl(m.url, code ? `https://www.instagram.com/${type === 'Reel' ? 'reel' : 'p'}/${encodeURIComponent(code)}/` : null),
      thumb:igBestImage(m), alt:m.accessibility_caption, caption:captionText, author:igAuthor(owner), created:when(pick(m.taken_at_timestamp, m.taken_at, m.created_at)), duration:clock(m.video_duration), stats:igStats(m, video), audio:igAudio(m), tags:igHashtags(captionText)};
  }
  function igTagged(m) {
    const users = list(get(m, 'edge_media_to_tagged_user.edges')).map(edge => get(edge, 'node.user'))
      .concat(list(m.usertags?.in).map(tag => tag.user)).filter(Boolean);
    return [...new Map(users.map(user => [text(user.username), user])).values()];
  }
  function igCarousel(m) {
    return list(get(m, 'edge_sidecar_to_children.edges')).map(edge => edge.node).filter(Boolean)
      .concat(list(m.carousel_media)).map((child, index) => {
        const item = igMedia(child, child.is_video || child.media_type === 2 ? 'Video' : 'Photo');
        item.title = `Slide ${index + 1}`;
        item.url = null;  // a slide's shortcode has no page of its own
        item.facts = [{label:'Size', value:igDimensions(child)}, {label:'Tagged', value:igTagged(child).map(user => at(user.username)).join(', ')}, {label:'Alt text', value:child.accessibility_caption}];
        return item;
      });
  }
  function igComment(c) {
    const owner = c.owner || c.user || {}, replies = count(pick(get(c, 'edge_threaded_comments.count'), c.child_comment_count));
    return {text:c.text, created:when(c.created_at), likes:count(pick(get(c, 'edge_liked_by.count'), c.comment_like_count, c.like_count)), replies, author:igAuthor(owner), creator:c.is_author === true || c.is_creator === true, pinned:c.is_pinned === true, liked:c.viewer_has_liked === true, id:pick(c.id, c.pk)};
  }
  function igCommentRow(comment) {
    const name = at(comment.author?.handle) || text(comment.author?.name) || 'Unknown account';
    const meta = [time(comment.created), comment.likes != null ? `<span>${icon('heart')}<span class="sr-only">Likes: </span>${compact(comment.likes)}</span>` : '', comment.replies != null ? `<span>${plural(comment.replies, 'reply', 'replies')}</span>` : ''].filter(Boolean).join('<span aria-hidden="true">·</span>');
    const badges = [comment.creator ? 'Creator' : '', comment.pinned ? 'Pinned' : '', comment.liked ? 'Liked by viewer' : ''].filter(Boolean).map(value => `<span class="badge">${esc(value)}</span>`).join('');
    return `<li class="comment-row">${avatar(comment.author?.avatar, name)}<div class="item-body"><span class="byline-name">${external(comment.author?.url, esc(name))}${comment.author?.verified ? verifiedMark : ''}${badges}</span>${text(comment.text) ? `<p class="comment-text">${esc(comment.text)}</p>` : ''}${meta ? `<span class="comment-meta">${meta}</span>` : ''}</div></li>`;
  }
  function igPreviewComments(m) {
    const edges = list(get(m, 'edge_media_to_parent_comment.edges')).length ? list(get(m, 'edge_media_to_parent_comment.edges')) : list(get(m, 'edge_media_preview_comment.edges'));
    const comments = edges.map(edge => igComment(edge.node || edge)).filter(comment => text(comment.text));
    const total = count(pick(get(m, 'edge_media_to_parent_comment.count'), get(m, 'edge_media_preview_comment.count')));
    return comments.length ? block('Comments preview', itemList(comments, {noun:'comment', render:igCommentRow, total, heading:false}), total != null ? countBadge(total) : countBadge(comments.length)) : '';
  }
  function igDimensions(m) {
    const width = pick(get(m, 'dimensions.width'), m.original_width, get(m, 'image_versions2.candidates.0.width'), get(m, 'video_versions.0.width'));
    const height = pick(get(m, 'dimensions.height'), m.original_height, get(m, 'image_versions2.candidates.0.height'), get(m, 'video_versions.0.height'));
    return width && height ? `${width} × ${height}` : '';
  }
  function igMediaView(m, platform) {
    const item = igMedia(m), children = igCarousel(m), tagged = igTagged(m), mentions = igMentions(item.caption);
    const collaborators = list(m.coauthor_producers).concat(list(m.invited_coauthor_producers)).map(igAuthor).filter(Boolean);
    const music = m.clips_music_attribution_info || {};
    item.facts = [
      {label:'Location', html:get(m, 'location.name') ? external(present(get(m, 'location.id')) ? `https://www.instagram.com/explore/locations/${encodeURIComponent(get(m, 'location.id'))}/` : null, esc(get(m, 'location.name'))) : ''}, {label:'Location ID', value:get(m, 'location.id')}, {label:'Media ID', value:pick(m.id, m.pk)}, {label:'Shortcode', value:pick(m.shortcode, m.code)},
      {label:'Resolution', value:igDimensions(m)}, {label:'Accessibility', value:m.accessibility_caption},
      {label:'Tagged accounts', value:tagged.map(user => at(user.username)).join(', ')}, {label:'Mentioned accounts', value:mentions.join(', ')},
      {label:'Collaborators', value:collaborators.map(user => at(user.handle) || user.name).join(', ')},
      {label:'Paid partnership', value:m.is_paid_partnership === true ? 'Yes' : ''}, {label:'Audio available', value:m.has_audio === false ? 'No' : ''},
      {label:'Audio status', value:music.should_mute_audio === true ? pick(music.should_mute_audio_reason, 'Muted by Instagram') : ''},
    ];
    item.sections = [
      children.length ? block('Carousel', gallery(children), countBadge(children.length)) : '',
      tagged.length ? block('Tagged accounts', itemList(tagged.map(user => ({type:'Account', round:true, title:pick(user.full_name, at(user.username)), thumb:user.profile_pic_url, url:igProfileUrl(user.username), author:{handle:user.username}, note:user.is_verified ? 'Verified' : ''})), {noun:'account', heading:false}), countBadge(tagged.length)) : '',
      igPreviewComments(m),
    ];
    return mediaCard(item, platform);
  }
  function igProfileRow(user) {
    return {type:'Account', round:true, title:pick(user.full_name, at(user.username)), thumb:pickImage(user.profile_pic_url_hd, user.profile_pic_url), url:igProfileUrl(user.username), author:{handle:user.username}, note:[user.is_verified ? 'Verified' : '', user.is_private ? 'Private' : ''].filter(Boolean).join(' · ')};
  }
  const ADAPTERS = {
    '/v1/instagram/post': (body, platform) => {
      const m = get(body, 'data.xdt_shortcode_media');
      return m && igMediaView(m, platform);
    },
    '/v1/instagram/profile': (body, platform) => {
      const user = get(body, 'data.user');
      if (!user) return null;
      const posts = list(get(user, 'edge_owner_to_timeline_media.edges')).map(edge => edge.node).filter(Boolean);
      const related = list(get(user, 'edge_related_profiles.edges')).map(edge => edge.node).filter(Boolean);
      const links = list(user.bio_links), address = user.business_address_json || {};
      const badges = [user.is_business_account ? 'Business' : '', user.is_professional_account && !user.is_business_account ? 'Professional' : '', user.category_name].filter(Boolean);
      const sections = [
        posts.length ? block('Recent posts', gallery(posts.map(post => igMedia(post))), countBadge(posts.length)) : '',
        related.length ? block('Related accounts', itemList(related.map(igProfileRow), {noun:'account', heading:false}), countBadge(related.length)) : '',
        links.length ? block('Bio links', `<ul class="item-list">${links.map(link => `<li class="item-row"><div class="item-body">${external(link.url, esc(text(link.title) || text(link.url)), 'item-title')}${text(link.link_type) ? `<span class="byline-meta">${esc(link.link_type)}</span>` : ''}</div></li>`).join('')}</ul>`, countBadge(links.length)) : '',
      ];
      return profileCard({name:user.full_name, handle:user.username, avatar:pickImage(user.profile_pic_url_hd, user.profile_pic_url), verified:user.is_verified === true, bio:pick(get(user, 'biography_with_entities.raw_text'), user.biography), url:igProfileUrl(user.username), links:[...links.map(link => link.url), user.external_url], badges,
        status:user.is_private ? {label:'Private'} : null,
        stats:[stat('followers', get(user, 'edge_followed_by.count'), {always:true}), stat('following', get(user, 'edge_follow.count'), {always:true}), stat('posts', get(user, 'edge_owner_to_timeline_media.count'), {always:true}), stat('videos', get(user, 'edge_felix_video_timeline.count'))],
        facts:[{label:'Account ID', value:user.id}, {label:'Facebook ID', value:user.fbid}, {label:'Pronouns', value:Array.isArray(user.pronouns) ? user.pronouns.join(', ') : user.pronouns}, {label:'Category', value:user.category_name}, {label:'Address', value:[address.street_address, address.city_name, address.zip_code].filter(Boolean).join(', ')}, {label:'Business email', value:user.business_email}, {label:'Business phone', value:user.business_phone_number}], sections}, platform);
    },
    '/v1/instagram/profile/post-count': (body, platform) => profileCard({handle:body.handle, url:igProfileUrl(body.handle), stats:[stat('posts', body.media_count, {always:true, approx:body.is_estimated === true})], facts:[{label:'Count quality', value:body.is_estimated ? 'Estimated from Instagram’s rounded figure' : 'Exact'}]}, platform),
    '/v2/instagram/user/posts': body => {
      const items = list(body.items).map(m => igMedia(m));
      return `${byline(igAuthor(body.user))}${itemList(items, {noun:'post', total:count(body.num_results), more:!!body.next_max_id})}${nextPage('next_max_id', body.next_max_id)}`;
    },
    '/v1/instagram/user/reels': body => {
      const items = list(body.items).map(m => igMedia(m, 'Reel'));
      const parent = body.user || list(body.items)[0]?.owner || list(body.items)[0]?.user;
      return `${byline(igAuthor(parent))}${itemList(items, {noun:'reel', more:!!body.max_id})}${nextPage('max_id', body.max_id)}`;
    },
    '/v1/instagram/user/highlights': body => {
      const highlights = list(body.highlights), owner = highlights[0]?.owner;
      return `${byline(igAuthor(owner))}${itemList(highlights.map(h => ({type:'Highlight', title:h.title, thumb:pickImage(get(h, 'cover_media_cropped_thumbnail.url'), get(h, 'cover_media.thumbnail_src')), shape:'square', url:h.id ? `https://www.instagram.com/stories/highlights/${encodeURIComponent(String(h.id).replace(/^highlight:/, ''))}/` : null, author:igAuthor(h.owner), note:h.id ? `ID ${h.id}` : ''})), {noun:'highlight'})}`;
    },
    '/v1/instagram/user/highlight/detail': body => {
      const items = list(body.items).map(story => igMedia(story, story.media_type === 2 || story.video_versions ? 'Video story' : 'Photo story'));
      const cover = get(body, 'cover_media.cropped_image_version');
      const head = {type:'Highlight', shape:'square', title:body.title, thumb:pickImage(cover?.url, get(body, 'cover_media.full_image_version.url')), author:igAuthor(body.user), created:when(body.created_at), facts:[{label:'Highlight ID', value:body.id}, {label:'Stories', value:body.media_count}, {label:'Pinned highlight', value:body.is_pinned_highlight === true ? 'Yes' : ''}, {label:'Archived', value:body.is_archived === true ? 'Yes' : ''}], sections:[items.length ? block('Stories', gallery(items), countBadge(items.length)) : '']};
      return mediaCard(head, 'Instagram');
    },
    '/v1/instagram/search/popular': body => {
      const posts = list(body.posts).map(m => igMedia(m));
      const sources = Array.isArray(get(body, 'description.source_uris')) ? get(body, 'description.source_uris') : [];
      const info = `${caption(get(body, 'description.plain_text'), 'preview-caption', 'the topic description')}${chips(body.suggested_terms)}${sources.length ? block('Sources', `<ul class="item-list">${sources.map(url => `<li class="item-row"><div class="item-body">${external(url, esc(url), 'item-title')}</div></li>`).join('')}</ul>`, countBadge(sources.length)) : ''}`;
      return `${section(text(body.title) || `Results for “${text(body.query)}”`, body.total_media_count != null ? `<span class="badge">${compact(body.total_media_count)} posts${body.total_media_count >= 1000 ? ' · approximate' : ''}</span>` : '')}${info}${itemList(posts, {noun:'post', more:!!body.cursor})}${nextPage('cursor', body.cursor)}`;
    },
    '/v1/instagram/search/hashtag': body => {
      const posts = list(body.posts).map(m => igMedia(m));
      return `${section(`#${text(body.hashtag).replace(/^#/, '')}`, body.media_type ? `<span class="badge">${esc(body.media_type)}</span>` : '')}${itemList(posts, {noun:'post', more:!!body.cursor})}${nextPage('cursor', body.cursor)}`;
    },
    '/v2/instagram/reels/search': body => {
      const reels = list(body.reels).map(m => igMedia(m, 'Reel'));
      return `${itemList(reels, {noun:'reel', more:!!body.cursor})}${nextPage('page', body.page, body.has_more)}`;
    },
    '/v2/instagram/post/comments': body => {
      const comments = list(body.comments).map(igComment);
      return `${itemList(comments, {noun:'comment', render:igCommentRow, more:!!body.cursor})}${nextPage('cursor', body.cursor)}`;
    },
    '/v2/instagram/media/transcript': body => {
      const parts = list(body.transcripts);
      if (!parts.length) return empty('closed-captioning', 'No transcript is available for this post.');
      return parts.map((part, index) => `${facts([{label:'Media ID', value:part.id}, {label:'Shortcode', value:part.shortcode}])}${transcriptView([], {plain:part.text, language:parts.length > 1 ? `Video ${index + 1}` : '', missing:'No speech was found in this video.'})}`).join('');
    },
  };

  // ---- TikTok ----------------------------------------------------------------------------------------------
  // App responses use snake_case and millisecond durations; collection responses use camelCase and seconds.
  const ttImage = value => typeof value === 'string' ? value : pick(get(value, 'url_list.0'), get(value, 'urlList.0'));
  const ttHandle = handle => text(handle).replace(/^@/, '');
  const ttProfileUrl = handle => ttHandle(handle) ? `https://www.tiktok.com/@${encodeURIComponent(ttHandle(handle))}` : null;
  const ttWebLink = value => linkUrl(value) || (/^[\w.-]+\.[a-z]{2,}(?:[/?#]|$)/i.test(text(value)) ? linkUrl(`https://${text(value)}`) : null);
  const ttBool = (value, yes, no = '') => value === true || value === 1 ? yes : value === false || value === 0 ? no : '';
  const ttCount = (...values) => pick(...values.map(count));
  function ttAuthor(a) {
    if (!a || typeof a !== 'object') return null;
    const handle = pick(a.unique_id, a.uniqueId), followers = ttCount(a.follower_count, a.followerCount);
    return {name:a.nickname, handle, avatar:pickImage(ttImage(a.avatar_larger), a.avatarLarger, ttImage(a.avatar_medium), a.avatarMedium, ttImage(a.avatar_thumb), a.avatarThumb), verified:a.verified === true, url:ttProfileUrl(handle), followers};
  }
  function ttHashtags(v) {
    return [...list(v.text_extra).map(tag => tag.hashtag_name), ...list(v.textExtra).map(tag => tag.hashtagName), ...list(v.cha_list).map(tag => tag.cha_name), ...list(v.challenges).map(tag => tag.title)].filter(Boolean).map(tag => `#${text(tag).replace(/^#/, '')}`);
  }
  function ttMentions(v) {
    return [...list(v.text_extra).filter(tag => tag.type === 0 || tag.user_id).map(tag => pick(tag.user_unique_id, tag.unique_id)), ...list(v.textExtra).filter(tag => tag.type === 0 || tag.userId).map(tag => pick(tag.userUniqueId, tag.uniqueId))].filter(Boolean).map(at);
  }
  function ttAudio(music) {
    if (!music || typeof music !== 'object') return null;
    return {title:music.title, artist:pick(music.author, music.authorName, music.owner_nickname), original:pick(music.is_original, music.is_original_sound, music.original), id:pick(music.id_str, music.mid, present(music.id) ? music.id : null), duration:clock(music.duration)};
  }
  function ttSlides(photo) {
    return list(pick(photo?.images, photo?.imageList)).map((slide, index) => ({type:`Slide ${index + 1}`, shape:'vertical', thumb:pickImage(ttImage(slide.display_image), ttImage(slide.displayImage), ttImage(slide.thumbnail)), facts:[]}));
  }
  function ttVideo(v, {web = false} = {}) {
    const app = v.statistics || {}, statsV2 = v.statsV2 || {}, stats = v.stats || {};
    const author = ttAuthor(v.author), music = v.music || v.added_sound_music_info;
    const photo = v.image_post_info || v.imagePost;
    const id = pick(v.aweme_id, v.id);
    const value = (appKey, webKey) => pick(app[appKey], statsV2[webKey], stats[webKey]);
    const slides = ttSlides(photo);
    const videoDuration = get(v, 'video.duration');
    const height = get(v, 'video.height'), width = get(v, 'video.width');
    const reposts = value('repost_count', 'repostCount'), downloads = value('download_count', 'downloadCount');
    return {
      type:photo ? 'Photo post' : 'Video', shape:'vertical',
      url:pickUrl(v.url, v.share_url, v.shareUrl, author?.handle && id ? `https://www.tiktok.com/@${encodeURIComponent(author.handle)}/${photo ? 'photo' : 'video'}/${id}` : null),
      thumb:pickImage(ttImage(get(v, 'video.cover')), ttImage(get(v, 'video.origin_cover')), get(v, 'video.cover'), get(v, 'video.originCover'), ttImage(get(photo, 'image_post_cover.display_image')), ttImage(get(photo, 'images.0.display_image')), get(photo, 'cover.imageURL.urlList.0')),
      caption:pick(v.desc, v.content_desc), author,
      created:when(pick(v.create_time, v.createTime)),
      duration:photo ? '' : clock(web ? videoDuration : count(videoDuration) != null ? Number(videoDuration) / 1000 : null),
      stats:[stat('views', value('play_count', 'playCount'), {always:true}), stat('likes', value('digg_count', 'diggCount'), {always:true}), stat('comments', value('comment_count', 'commentCount'), {always:true}), stat('shares', value('share_count', 'shareCount'), {always:true}), stat('saves', value('collect_count', 'collectCount'), {always:true}), present(reposts) && stat('shares', reposts, {label:'Reposts'}), present(downloads) && stat('saves', downloads, {label:'Downloads'})].filter(Boolean),
      audio:ttAudio(music), tags:ttHashtags(v),
      facts:[{label:'Region', value:v.region}, {label:'Caption language', value:pick(v.desc_language, v.textLanguage, v.title_language)}, {label:'Resolution', value:width && height ? `${width} × ${height}` : ''}, {label:'Video ID', value:id}, {label:'Mentions', value:ttMentions(v).join(', ')}, {label:'Pinned', value:ttBool(pick(v.is_top, v.is_pinned_item), 'Yes')}, {label:'Advertisement', value:ttBool(pick(v.is_ad, v.isAd, v.is_ads), 'Yes')}, {label:'Paid content', value:ttBool(v.is_paid_content, 'Yes')}],
      sections:slides.length ? [block('Slides', gallery(slides), countBadge(slides.length))] : [],
    };
  }
  function ttVideoView(v, platform) {
    const item = ttVideo(v);
    const a = v.author || {};
    if (item.author) item.author.followers = ttCount(a.follower_count, a.followerCount);
    item.facts.push({label:'Author ID', value:pick(a.uid, a.id)}, {label:'Author posts', value:pick(a.aweme_count, get(v, 'authorStats.videoCount'))}, {label:'Author following', value:pick(a.following_count, get(v, 'authorStats.followingCount'))});
    return mediaCard(item, platform);
  }
  function ttParent(v, title) {
    const author = ttAuthor(v?.author);
    if (!author) return '';
    return block(title, byline(author) + caption(v.author?.signature, 'help', captionFor('bio', author.name || at(author.handle))));
  }
  function ttMusicCard(music, platform) {
    const id = pick(music.id_str, music.mid, present(music.id) ? music.id : null);
    const artist = pick(music.author, music.authorName, music.owner_nickname);
    const matched = music.matched_song || {};
    const tags = [...list(music.tag_list).map(tag => pick(tag.name, tag.title)), ...(Array.isArray(music.theme_tags) ? music.theme_tags : []).map(tag => typeof tag === 'string' ? tag : pick(tag?.name, tag?.title))];
    return mediaCard({type:'Sound', glyph:'waveform', shape:'square', thumb:pickImage(ttImage(music.cover_large), music.coverLarge, ttImage(music.cover_medium), music.coverMedium, ttImage(music.cover_thumb), music.coverThumb), title:music.title, author:{name:artist, handle:music.owner_handle, url:ttProfileUrl(music.owner_handle)}, created:when(music.create_time), duration:clock(pick(music.duration, music.video_duration)), tags,
      stats:[stat('items', pick(music.user_count, music.video_count), {label:'Videos'})],
      facts:[{label:'Sound ID', value:id}, {label:'Audio type', value:pick(music.is_original, music.is_original_sound, music.original) === true ? 'Original audio' : pick(music.is_original, music.is_original_sound, music.original) === false ? 'Licensed sound' : ''}, {label:'Album', value:music.album}, {label:'Language', value:music.language}, {label:'Matched track', value:matched.title}, {label:'Matched artist', value:matched.author}, {label:'Commerce music', value:ttBool(music.is_commerce_music, 'Yes')}, {label:'Copyrighted', value:ttBool(music.isCopyrighted, 'Yes')}]}, platform);
  }
  Object.assign(ADAPTERS, {
    '/v2/tiktok/video': (body, platform) => body.aweme_detail && ttVideoView(body.aweme_detail, platform),
    '/v3/tiktok/profile/videos': body => {
      const videos = list(body.aweme_list), items = videos.map(v => ttVideo(v));
      return `${ttParent(videos[0], 'Profile')}${itemList(items, {noun:'video', more:!!body.has_more})}${nextPage('max_cursor', body.max_cursor, body.has_more)}`;
    },
    '/v1/tiktok/song/videos': (body, platform) => {
      const videos = list(body.aweme_list), music = videos[0]?.music || videos[0]?.added_sound_music_info;
      return `${music ? block('Sound', ttMusicCard(music, platform)) : ''}${itemList(videos.map(v => ttVideo(v)), {noun:'video', more:!!body.has_more})}${nextPage('cursor', body.cursor, body.has_more)}`;
    },
    '/v1/tiktok/collection/videos': body => {
      const videos = list(body.videos);
      return `${section('Collection', body.collection_id ? `<span class="badge">ID ${esc(body.collection_id)}</span>` : '')}${ttParent(videos[0], 'First creator')}${itemList(videos.map(v => ttVideo(v, {web:true})), {noun:'video', more:!!body.has_more})}${nextPage('cursor', body.max_cursor, body.has_more)}`;
    },
    '/v1/tiktok/song': (body, platform) => body.music_info && ttMusicCard(body.music_info, platform),
    '/v1/tiktok/profile': (body, platform) => {
      const user = body.user || {}, stats = body.stats || {}, commerce = user.commerceUserInfo || {};
      const badges = [user.privateAccount ? 'Private' : '', commerce.commerceUser ? pick(commerce.category, 'Business account') : '', user.isOrganization ? 'Organization' : '', user.ttSeller ? 'TikTok Shop seller' : ''];
      const tabs = user.profileTab || {}, sections = [];
      const recent = list(body.itemList).map(v => ttVideo(v, {web:true}));
      if (recent.length) sections.push(block('Recent posts', gallery(recent), countBadge(recent.length)));
      return profileCard({name:user.nickname, handle:user.uniqueId, avatar:pickImage(user.avatarLarger, user.avatarMedium, user.avatarThumb), verified:user.verified === true, bio:user.signature, url:ttProfileUrl(user.uniqueId), links:[ttWebLink(get(user, 'bioLink.link'))], badges,
        stats:[stat('followers', stats.followerCount, {always:true}), stat('following', stats.followingCount, {always:true}), stat('total_likes', pick(stats.heartCount, stats.heart), {always:true}), stat('videos', stats.videoCount, {always:true}), stat('likes', stats.diggCount, {label:'Liked videos'}), stat('following', stats.friendCount, {label:'Friends'})],
        facts:[{label:'Joined', html:time(when(user.createTime))}, {label:'Language', value:user.language}, {label:'User ID', value:user.id}, {label:'Secure user ID', value:user.secUid}, {label:'Favorites', value:user.openFavorite ? 'Public' : present(user.openFavorite) ? 'Private' : ''}, {label:'Profile tabs', value:[tabs.showMusicTab && 'Music', tabs.showQuestionTab && 'Q&A', tabs.showPlayListTab && 'Playlists'].filter(Boolean).join(', ')}], sections}, platform);
    },
    '/v1/tiktok/profile/region': (body, platform) => profileCard({handle:body.handle, url:pickUrl(body.profile_url, ttProfileUrl(body.handle)), facts:[{label:'Region', value:body.region}]}, platform),
    '/v1/tiktok/user/live': (body, platform) => {
      const user = body.liveRoomUserInfo || {}, room = body.liveRoom || {};
      const roomStats = room.liveRoomStats || {};
      const roomCard = body.is_live && Object.keys(room).length ? mediaCard({type:'Live now', shape:'square', thumb:pickImage(room.coverUrl, room.squareCoverImg), title:room.title, author:ttAuthor(user), url:ttProfileUrl(user.uniqueId) ? `${ttProfileUrl(user.uniqueId)}/live` : null, created:when(room.startTime), stats:[stat('viewers', roomStats.userCount, {always:true}), stat('views', roomStats.enterCount, {label:'Entries'})], facts:[{label:'Room ID', value:user.roomId}, {label:'Stream ID', value:room.streamId}, {label:'Subscribers only', value:ttBool(room.liveSubOnly, 'Yes', 'No')}, {label:'Paid event', value:get(room, 'paidEvent.paid_type') ? 'Yes' : present(get(room, 'paidEvent.paid_type')) ? 'No' : ''}]}, platform) : '';
      return profileCard({name:user.nickname, handle:user.uniqueId, avatar:pickImage(user.avatarLarger, user.avatarMedium, user.avatarThumb), verified:user.verified === true, bio:user.signature, url:ttProfileUrl(user.uniqueId), status:body.is_live ? {label:'Live now', tone:'good'} : {label:'Not live'}, stats:[stat('followers', user.followerCount, {always:true}), stat('following', user.followingCount, {always:true})], facts:[{label:'User ID', value:user.id}, {label:'Live room ID', value:user.roomId}], sections:roomCard ? [block('Live room', roomCard)] : []}, platform);
    },
    '/v1/tiktok/live': (body, platform) => mediaCard({type:{live:'Live now', ended:'Ended'}[body.status] || text(body.status) || 'Live', shape:'square', thumb:body.cover_url, title:body.title, author:body.owner && {name:body.owner.display_name, handle:body.owner.username, url:ttProfileUrl(body.owner.username)}, url:body.owner?.username ? `${ttProfileUrl(body.owner.username)}/live` : null,
      stats:[stat('viewers', body.viewer_count, {always:true}), stat('likes', body.like_count)], facts:[{label:'Room ID', value:body.room_id}, {label:'Owner ID', value:body.owner?.id}]}, platform),
    '/v1/tiktok/video/transcript': body => {
      const source = text(body.transcript), cues = /^WEBVTT/.test(source) ? vtt(source) : [];
      const context = facts([{label:'Video ID', value:body.id}, {label:'Video', html:body.url ? external(body.url, esc(body.url), 'text-link') : ''}, {label:'Language', value:pick(body.language, body.transcript_language)}]);
      return `${context}${transcriptView(cues, {language:pick(body.language, body.transcript_language), plain:cues.length ? '' : source, missing:'This video has no captions to transcribe.'})}`;
    },
  });
  function vtt(source) {
    const cues = [];
    for (const cueBlock of source.replace(/\r/g, '').split(/\n{2,}/)) {
      const lines = cueBlock.split('\n'), timing = lines.findIndex(line => line.includes('-->'));
      if (timing < 0) continue;
      const words = lines.slice(timing + 1).join(' ').replace(/<[^>]*>/g, '').trim();
      if (words) cues.push({time:lines[timing].split('-->')[0].trim().replace(/\.\d+$/, '').replace(/^00:(?=\d\d:)/, '').replace(/^0(?=\d:)/, ''), text:words});
    }
    return cues;
  }

  // ---- YouTube ---------------------------------------------------------------------------------------------
  const ytChannelUrl = channel => {
    if (!channel || typeof channel !== 'object') return null;
    const direct = pick(channel.url, channel.channelUrl);
    if (linkUrl(direct)) return direct;
    return text(channel.id) ? `https://www.youtube.com/channel/${encodeURIComponent(text(channel.id))}` : null;
  };
  const ytAuthor = channel => channel && typeof channel === 'object' ? {
    name:pick(channel.title, channel.name),
    handle:/^[\w.\-\p{L}\p{M}]+$/u.test(text(channel.handle)) ? channel.handle : null,
    avatar:pickImage(channel.thumbnail, channel.avatarUrl),
    verified:channel.isVerified === true,
    url:ytChannelUrl(channel),
    followers:count(pick(channel.subscriberCount, channel.followers)),
    byName:true,
  } : null;
  const YT_TYPE = {video:'Video', short:'Short', live:'Live stream', playlist:'Playlist', channel:'Channel'};
  const ytBadges = value => Array.isArray(value) ? value.map(item => typeof item === 'string' ? item : pick(item?.label, item?.text)).filter(Boolean) : [];
  const ytCountStat = (key, exactValue, shown, options = {}) => {
    if (exactValue == null) return textStat(key, shown, options);
    const displayed = textCount(shown);
    // A compact display is only the source of the number when it resolves to the same rounded value. Detailed
    // endpoints can return an exact integer beside compact text (for example 2.4M and 2,460,322 comments).
    const approx = !!displayed?.approx && displayed.value === count(exactValue);
    return stat(key, exactValue, {...options, approx});
  };
  const ytVideoUrl = v => pick(v.url, v.videoUrl, v.id ? `https://www.youtube.com/watch?v=${encodeURIComponent(v.id)}` : null);
  function ytVideo(v) {
    // Live and upcoming streams say “1.2K watching” or “30 waiting”; neither is a lifetime view count.
    const audience = v.viewCountInt == null && /watching|waiting/i.test(text(v.viewCountText));
    const badges = ytBadges(v.badges);
    const type = v.type === 'live' ? (badges.some(b => /upcoming/i.test(b)) ? 'Upcoming stream' : badges.some(b => /^live$/i.test(b)) ? 'Live stream' : 'Stream') : YT_TYPE[v.type] || text(v.type) || 'Video';
    return {
      type, shape:v.type === 'short' ? 'vertical' : 'wide', url:ytVideoUrl(v), thumb:v.thumbnail,
      title:v.title, caption:v.description, author:ytAuthor(v.channel),
      created:approxWhen(pick(v.publishedTime, v.publishDate), pick(v.publishedTimeText, v.publishDateText)),
      duration:pick(text(v.lengthText), text(v.durationFormatted).replace(/^00:/, ''), clock(pick(v.lengthSeconds, v.lengthInSeconds, count(v.durationMs) / 1000))),
      note:[audience ? text(v.viewCountText) : '', ...badges].filter(Boolean).join(' · '),
      stats:[ytCountStat('views', v.viewCountInt, audience ? null : v.viewCountText)],
      facts:[{label:'Video ID', value:v.id}],
    };
  }
  function ytPlaylist(p) {
    return {type:'Playlist', shape:'wide', url:pickUrl(p.playlistUrl, p.url, p.id ? `https://www.youtube.com/playlist?list=${encodeURIComponent(p.id)}` : null), thumb:p.thumbnail, title:p.title, author:ytAuthor(p.channel), stats:[stat('items', p.videoCount, {label:'Videos'})], facts:[{label:'Playlist ID', value:p.id}]};
  }
  function ytChannelRow(c) {
    return {type:'Channel', round:true, url:ytChannelUrl(c), thumb:pickImage(c.thumbnail, c.thumbnailUrl), title:pick(c.title, c.name), caption:c.description, author:c.handle ? {handle:c.handle} : null,
      stats:[ytCountStat('subscribers', c.subscriberCount, c.subscriberCountText)], facts:[{label:'Channel ID', value:c.id}]};
  }
  const ytImageGallery = (urls, title = 'Images') => {
    const items = (urls || []).map((url, index) => imageUrl(url) ? {type:`Image ${index + 1}`, thumb:url, shape:'square'} : null).filter(Boolean);
    return block(title, gallery(items), countBadge(items.length));
  };
  function ytPost(p) {
    const images = Array.isArray(p.images) ? p.images : p.image ? [p.image] : [];
    const attached = p.video && typeof p.video === 'object' ? ytVideo(p.video) : null;
    const original = p.originalPost && typeof p.originalPost === 'object' ? ytPost(p.originalPost) : null;
    const sections = [
      images.length > 1 ? ytImageGallery(images) : '',
      attached ? block('Attached video', itemList([attached], {noun:'video', heading:false})) : '',
      original ? block('Shared post', mediaCard(original, 'YouTube')) : '',
    ];
    return {type:original ? 'Shared post' : 'Community post', shape:attached && !images.length ? 'wide' : 'square',
      url:pickUrl(p.url, p.id ? `https://www.youtube.com/post/${encodeURIComponent(p.id)}` : null),
      thumb:pickImage(p.image, images[0], attached?.thumb), caption:p.content, author:ytAuthor(p.channel),
      created:approxWhen(p.publishedTime, p.publishedTimeText),
      stats:[ytCountStat('likes', p.likeCount, p.likeCountText, {always:true})],
      facts:[{label:'Post ID', value:p.id}, {label:'Images', value:images.length ? String(images.length) : ''}], sections};
  }
  const ytComment = c => ({
    text:c.content, created:approxWhen(c.publishedTime, c.publishedTimeText), likes:count(get(c, 'engagement.likes')),
    replies:count(get(c, 'engagement.replies')), creator:get(c, 'author.isCreator') === true,
    author:{name:get(c, 'author.name'), avatar:get(c, 'author.avatarUrl'), verified:get(c, 'author.isVerified') === true, url:get(c, 'author.channelUrl')},
  });
  function ytChapters(items) {
    const chapters = list(items);
    if (!chapters.length) return '';
    return block('Chapters', `<ol class="transcript">${chapters.map(chapter => `<li><span class="timestamp">${esc(pick(chapter.startTimeText, clock(count(chapter.startMs) / 1000)) || '')}</span><span>${esc(text(chapter.title) || 'Untitled chapter')}</span></li>`).join('')}</ol>`, countBadge(chapters.length));
  }
  function ytLinks(items) {
    const links = list(items).map(item => ({text:pick(item.text, item.title, item.url), url:item.url})).filter(item => linkUrl(item.url));
    if (!links.length) return '';
    return block('Description links', `<ul class="item-list">${links.map(item => `<li class="item-row"><span class="audio-glyph">${icon('link')}</span><div class="item-body">${external(item.url, esc(item.text), 'item-title')}</div></li>`).join('')}</ul>`, countBadge(links.length));
  }
  function ytCaptions(items) {
    const tracks = list(items);
    if (!tracks.length) return '';
    return block('Captions and subtitles', `<ul class="item-list">${tracks.map(track => {
      const name = pick(get(track, 'name.simpleText'), track.trackName, track.languageCode, 'Unnamed track');
      const meta = [track.languageCode, track.kind === 'asr' ? 'Auto-generated' : '', track.isTranslatable === true ? 'Translatable' : ''].filter(Boolean).join(' · ');
      return `<li class="item-row"><span class="audio-glyph">${icon('closed-captioning')}</span><div class="item-body"><span class="item-title">${esc(name)}</span>${meta ? `<span class="byline-meta">${esc(meta)}</span>` : ''}</div></li>`;
    }).join('')}</ul>`, countBadge(tracks.length));
  }
  function ytCollaborators(items) {
    const channels = list(items);
    if (!channels.length) return '';
    return block('Collaborators', itemList(channels.map(channel => ({type:'Channel', round:true, thumb:pickImage(channel.thumbnail, channel.avatarUrl), title:pick(channel.title, channel.name, at(channel.handle)), url:ytChannelUrl(channel), author:channel.handle ? {handle:channel.handle} : null})), {noun:'channel', heading:false}), countBadge(channels.length));
  }
  function ytReplay(replay) {
    if (!replay || typeof replay !== 'object') return '';
    const markers = list(replay.markers), ranges = list(replay.ranges);
    const peak = markers.reduce((best, marker) => count(marker.intensityScoreNormalized) > count(best?.intensityScoreNormalized) ? marker : best, null);
    const peakMs = pick(peak?.startMillis, ranges[0]?.peakMillis);
    const rows = [
      {label:'Heatmap buckets', value:markers.length ? String(markers.length) : ''},
      {label:'Peak replay moment', value:peakMs != null ? stamp(count(peakMs) / 1000) : ''},
      {label:'Highlighted ranges', value:ranges.length ? String(ranges.length) : ''},
    ];
    const rangeList = ranges.length ? `<ul class="item-list">${ranges.map(range => `<li class="item-row"><span class="audio-glyph">${icon('play')}</span><div class="item-body"><span class="item-title">${esc(text(range.label) || 'Highlighted range')}</span><span class="byline-meta">${esc([stamp(count(range.startMillis) / 1000), stamp(count(range.endMillis) / 1000)].filter(Boolean).join('–'))}</span></div></li>`).join('')}</ul>` : '';
    return block('Most replayed', facts(rows) + rangeList);
  }
  const ytVideos = (items, noun, body) => itemList(list(items).map(ytVideo), {noun, more:!!body.continuationToken}) + nextPage('continuationToken', body.continuationToken);
  Object.assign(ADAPTERS, {
    '/v1/youtube/video': (body, platform) => {
      const durationMs = count(body.durationMs), video = ytVideo(body);
      video.type = YT_TYPE[body.type] || 'Video';
      video.duration = durationMs != null ? clock(durationMs / 1000) : text(body.durationFormatted).replace(/^00:/, '');
      video.stats = [
        ytCountStat('views', body.viewCountInt, body.viewCountText, {always:true}),
        ytCountStat('likes', body.likeCountInt, body.likeCountText, {always:true}),
        ytCountStat('comments', body.commentCountInt, body.commentCountText, {always:true}),
      ];
      const keywords = Array.isArray(body.keywords) ? body.keywords : [];  // listed in their own block below
      video.facts = [
        {label:'Video ID', value:body.id}, {label:'Genre', value:body.genre},
        {label:'Published', value:video.created ? '' : body.publishDateText},
        {label:'Paid promotion', value:typeof body.isPaidPromotion === 'boolean' ? (body.isPaidPromotion ? 'Yes' : 'No') : ''},
      ];
      video.sections = [block('Keywords', chips(keywords), keywords.length ? countBadge(keywords.length) : ''), ytLinks(body.descriptionLinks), ytChapters(body.chapters), ytCollaborators(body.collaborators), ytCaptions(body.captionTracks), ytReplay(body.most_replayed),
        block('Watch next', itemList(list(body.watchNextVideos).map(ytVideo), {noun:'video', heading:false}), list(body.watchNextVideos).length ? countBadge(list(body.watchNextVideos).length) : '')];
      return mediaCard(video, platform);
    },
    '/v1/youtube/channel': (body, platform) => {
      const sources = list(get(body, 'avatar.image.sources'));
      const links = [...(Array.isArray(body.links) ? body.links.map(link => typeof link === 'string' ? link : link?.url) : []), body.twitter, body.instagram, body.store];
      const tags = typeof body.tags === 'string' ? body.tags.split(',').map(tag => tag.trim()).filter(Boolean) : Array.isArray(body.tags) ? body.tags : [];
      return profileCard({name:body.name, handle:pick(body.handle, text(body.channel).match(/\/@([^/?#]+)/)?.[1]),
        avatar:sources.length ? [...sources].sort((a, b) => (count(b.width) || 0) - (count(a.width) || 0))[0].url : null,
        bio:body.description, url:pickUrl(body.channel, body.channelId ? `https://www.youtube.com/channel/${encodeURIComponent(body.channelId)}` : null), links,
        stats:[ytCountStat('subscribers', body.subscriberCount, body.subscriberCountText, {always:true}), textStat('videos', body.videoCountText, {always:true}), textStat('total_views', body.viewCountText, {always:true})],
        facts:[{label:'Joined', value:text(body.joinedDateText).replace(/^Joined\s+/i, '')}, {label:'Country', value:body.country}, {label:'Business email', value:body.email}, {label:'Channel ID', value:body.channelId}],
        sections:[block('Topics', chips(tags), tags.length ? countBadge(tags.length) : '')]}, platform);
    },
    '/v1/youtube/channel-videos': body => ytVideos(body.videos, 'video', body),
    '/v1/youtube/channel/shorts': body => ytVideos(body.shorts, 'short', body),
    '/v1/youtube/channel/lives': body => ytVideos(body.lives, 'stream', body),
    '/v1/youtube/search/hashtag': body => ytVideos(body.videos, 'video', body),
    '/v1/youtube/channel/playlists': body => itemList(list(body.playlists).map(ytPlaylist), {noun:'playlist', more:!!body.continuationToken}) + nextPage('continuationToken', body.continuationToken),
    '/v1/youtube/channel/community-posts': body => itemList(list(body.posts).map(ytPost), {noun:'post', more:!!body.continuationToken}) + nextPage('continuationToken', body.continuationToken),
    '/v1/youtube/community-post': (body, platform) => mediaCard(ytPost(body), platform),
    '/v1/youtube/playlist': body => {
      const videos = list(body.videos).map(ytVideo), owner = ytAuthor(body.owner);
      return `${section(text(body.title) || 'Playlist', body.totalVideos != null ? `<span class="badge">${plural(count(body.totalVideos) ?? videos.length, 'video')}</span>` : '')}${byline(owner)}${facts([{label:'Playlist owner ID', value:get(body, 'owner.id')}])}${itemList(videos, {noun:'video', total:count(body.totalVideos), more:!!body.continuationToken})}${nextPage('continuationToken', body.continuationToken)}`;
    },
    '/v1/youtube/search': body => {
      const groups = [['Videos', list(body.videos).map(ytVideo), 'video'], ['Shorts', list(body.shorts).map(ytVideo), 'short'], ['Live', list(body.lives).map(ytVideo), 'stream'], ['Channels', list(body.channels).map(ytChannelRow), 'channel'], ['Playlists', list(body.playlists).map(ytPlaylist), 'playlist'],
        ...list(body.shelves).map(shelf => [text(shelf.title) || 'Shelf', list(shelf.items).map(item => item.type === 'playlist' ? ytPlaylist(item) : item.type === 'channel' ? ytChannelRow(item) : ytVideo(item)), 'result'])].filter(([, items]) => items.length);
      if (!groups.length) return empty('magnifying-glass', 'No results for this search.');
      return groups.map(([title, items, noun]) => `<div class="preview-group">${section(title, `<span class="badge">${exact(items.length)}</span>`)}${itemList(items, {noun, heading:false})}</div>`).join('') + nextPage('continuationToken', body.continuationToken);
    },
    '/v1/youtube/video/comments': body => {
      const threads = list(body.comments).filter(comment => text(comment.repliesContinuationToken)).length;
      const replies = threads ? `<div class="inline-note">${icon('chat-circle')}<span>${plural(threads, 'comment has', 'comments have')} replies. Pass a comment’s <code>repliesContinuationToken</code> as <code>continuationToken</code> to <code>/v1/youtube/video/comment/replies</code> to load them.</span></div>` : '';
      return itemList(list(body.comments).map(ytComment), {noun:'comment', render:commentRow, more:!!body.continuationToken}) + replies + nextPage('continuationToken', body.continuationToken);
    },
    '/v1/youtube/video/comment/replies': body => itemList(list(body.comments).map(ytComment), {noun:'reply', nouns:'replies', render:commentRow, more:!!body.continuationToken}) + nextPage('continuationToken', body.continuationToken),
    '/v1/youtube/video/transcript': body => `${facts([{label:'Video ID', value:body.videoId}, {label:'Type', value:body.type}, {label:'Video', html:openLink(body.url, 'Open on YouTube')}])}${transcriptView(list(body.transcript).map(line => ({time:pick(line.startTimeText, clock(count(line.startMs) / 1000)), text:text(line.text)})).filter(line => line.text), {language:body.language, plain:body.transcript_only_text})}`,
    '/v1/youtube/video/sponsors': body => {
      const video = body.video || {}, detection = body.sponsorDetection || {}, sponsors = list(body.suspectedSponsors);
      const rows = sponsors.map(sponsor => {
        const evidence = list(sponsor.evidence), sources = [...new Set(evidence.map(item => text(item.source).replace(/_/g, ' ')).filter(Boolean))];
        const website = text(sponsor.website), websiteHtml = linkUrl(website) ? external(website, esc(website), 'text-link') : esc(website);
        return `<li class="comment-row"><span class="audio-glyph">${icon('shield-check')}</span><div class="item-body"><span class="byline-name">${esc(sponsor.name || 'Unnamed sponsor')}${sponsor.confidence ? `<span class="badge">${esc(sponsor.confidence)} confidence</span>` : ''}</span>${website ? `<span class="byline-meta">${websiteHtml}</span>` : ''}${evidence.map(item => text(item.text) ? `<p class="comment-text">${esc(item.text)}</p>` : '').join('')}${evidence.length ? `<span class="comment-meta">${plural(evidence.length, 'piece')} of evidence${sources.length ? ` · ${esc(sources.join(', '))}` : ''}</span>` : ''}</div></li>`;
      }).join('');
      const status = text(detection.status).replace(/_/g, ' ');
      return `${byline(ytAuthor(video.channel), video.isPaidPromotion ? 'Paid promotion disclosed' : '')}${video.title ? `<h3 class="media-title">${external(video.url, esc(video.title))}</h3>` : ''}${facts([{label:'Video ID', value:video.id}, {label:'Detection status', value:status}, {label:'Transcript available', value:detection.transcriptAvailable === true ? 'Yes' : detection.transcriptAvailable === false ? 'No' : ''}, {label:'Detection methods', value:Array.isArray(detection.methods) ? detection.methods.join(', ').replace(/_/g, ' ') : ''}])}${section(sponsors.length ? plural(sponsors.length, 'suspected sponsor') : 'Sponsors')}${sponsors.length ? `<ul class="item-list">${rows}</ul>` : empty('shield-check', 'No suspected sponsors were found in this response.')}${text(detection.note) ? `<p class="help">${esc(detection.note)}</p>` : ''}`;
    },
  });

  // ---- Building and mounting -------------------------------------------------------------------------------
  function build(path, status, body, {lazy = false} = {}) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
    if (status >= 400 || body.success === false) return errorView(status, body);
    const platform = {instagram:'Instagram', tiktok:'TikTok', youtube:'YouTube'}[String(path).split('/')[2]] || 'the platform';
    let html = '';
    try { html = ADAPTERS[path]?.(body, platform) || ''; } catch (error) { console.warn('The preview could not read this response.', error); }
    return (html || summary(body)) + allFields(body, {lazy});
  }
  // Indents JSON text without parsing it into numbers, so IDs beyond 2^53 keep every digit.
  function indent(json) {
    const out = [];
    let depth = 0, inString = false;
    const pad = () => `\n${'  '.repeat(depth)}`;
    for (let i = 0; i < json.length; i++) {
      const c = json[i];
      if (inString) { out.push(c); if (c === '\\') out.push(json[++i]); else if (c === '"') inString = false; continue; }
      if (c === '"') { inString = true; out.push(c); }
      else if (c === '{' || c === '[') {
        let next = i + 1;
        while (next < json.length && /\s/.test(json[next])) next++;
        if (json[next] === ']' || json[next] === '}') { out.push(c + json[next]); i = next; } else { depth++; out.push(c + pad()); }
      } else if (c === '}' || c === ']') { depth--; out.push(pad() + c); }
      else if (c === ',') out.push(c + pad());
      else if (c === ':') out.push(': ');
      else if (!/\s/.test(c)) out.push(c);
    }
    return out.join('');
  }
  // JSON.parse, except that integers too large for a JavaScript number arrive as strings with every digit.
  function parseLossless(json) {
    const out = [];
    let inString = false, from = 0;
    for (let i = 0; i < json.length; i++) {
      const c = json[i];
      if (inString) { if (c === '\\') i++; else if (c === '"') inString = false; continue; }
      if (c === '"') { inString = true; continue; }
      if (c !== '-' && (c < '0' || c > '9')) continue;
      let end = i + 1;
      while (end < json.length && /[\d.eE+-]/.test(json[end])) end++;
      const token = json.slice(i, end);
      if (/^-?\d{16,}$/.test(token) && !Number.isSafeInteger(Number(token))) { out.push(json.slice(from, i), `"${token}"`); from = end; }
      i = end - 1;
    }
    out.push(json.slice(from));
    return JSON.parse(out.join(''));
  }
  function select(root, next, focus) {
    root.querySelectorAll('[data-response-view]').forEach(tab => {
      const selected = tab.dataset.responseView === next;
      tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    });
    root.querySelector('#response-preview').hidden = next !== 'preview';
    root.querySelector('#response-raw').hidden = next !== 'raw';
  }
  // Fills the response area: Preview and Raw JSON tabs for a JSON object, the raw text alone otherwise.
  function mount(root, {path, status, text: raw, view = 'preview'}) {
    let body;
    try { body = parseLossless(raw); } catch { body = undefined; }
    const html = body === undefined ? null : build(path, status, body, {lazy:true});
    const pretty = body === undefined ? raw : indent(raw.trim());
    if (html === null) {
      root.innerHTML = '<pre class="code" id="response-raw" tabindex="0" aria-label="API response"></pre>';
      root.firstChild.textContent = pretty;
      return;
    }
    root.innerHTML = `<div class="code-tabs response-tabs" role="tablist" aria-label="Response format"><button type="button" role="tab" id="response-tab-preview" aria-controls="response-preview" data-response-view="preview">Preview</button><button type="button" role="tab" id="response-tab-raw" aria-controls="response-raw" data-response-view="raw">Raw JSON</button></div><div class="response-preview" id="response-preview" role="tabpanel" tabindex="0" aria-labelledby="response-tab-preview">${html}</div><pre class="code" id="response-raw" role="tabpanel" tabindex="0" aria-labelledby="response-tab-raw"></pre>`;
    root.querySelector('#response-raw').textContent = pretty;
    root.querySelector('[data-all-fields]')?.addEventListener('toggle', event => {
      const details = event.target;
      if (!details.open || details.querySelector('.fact-list')) return;
      const rows = fieldRows(body);
      details.querySelector('summary').insertAdjacentHTML('beforeend', ` <span class="badge">${exact(rows.length)}</span>`);
      details.insertAdjacentHTML('beforeend', fieldList(rows));
    });
    select(root, view === 'raw' ? 'raw' : 'preview', false);
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('click', event => {
      const tab = event.target.closest('[data-response-view]');
      if (tab) select(tab.closest('#response'), tab.dataset.responseView, false);
      const expand = event.target.closest('[data-expand]');
      if (expand) {
        const open = expand.getAttribute('aria-expanded') !== 'true', action = open ? 'Show less' : 'Show more';
        (document.getElementById(expand.getAttribute('aria-controls')) || expand.previousElementSibling).classList.toggle('expanded', open);
        expand.setAttribute('aria-expanded', String(open)); expand.textContent = action;
        expand.setAttribute('aria-label', `${action} of ${expand.dataset.subject || 'this text'}`);
      }
      const showAll = event.target.closest('[data-show-all]');
      if (showAll) {
        const rest = showAll.previousElementSibling, items = rest.previousElementSibling;
        const first = rest.content.firstElementChild;
        first.tabIndex = -1;
        items.append(rest.content); rest.remove(); showAll.remove();
        first.focus();
      }
    });
    document.addEventListener('keydown', event => {
      if (!event.target.matches('[data-response-view]') || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 'preview' : event.key === 'End' ? 'raw' : event.target.dataset.responseView === 'preview' ? 'raw' : 'preview';
      select(event.target.closest('#response'), next, true);
    });
    // Platform image links expire; a broken image leaves its placeholder glyph or initial showing.
    document.addEventListener('error', event => {
      if (event.target.tagName === 'IMG' && event.target.closest('.response-preview')) event.target.remove();
    }, true);
  }

  return {mount, build, indent, parseLossless};
})();
if (typeof module !== 'undefined') module.exports = ResponsePreview;
