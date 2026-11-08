const $ = (selector) => document.querySelector(selector);
const state = { user: null, listings: [], boroughs: [], criteria: null, view: 'all', sort: 'score', scan: null, active: null, overviewMap: null, detailMap: null, lastFocus: null };
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = (value) => value == null ? 'Unknown' : '£' + Math.round(value).toLocaleString('en-GB');
const range = (pair) => !pair ? 'Unknown' : pair[0] === pair[1] ? money(pair[0]) : money(pair[0]) + '–' + money(pair[1]);
const mid = (pair) => pair ? (pair[0] + pair[1]) / 2 : Infinity;
const safeUrl = (url) => { try { const parsed = new URL(url); return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : ''; } catch { return ''; } };
const shortTitle = (item) => (item.address || item.title || 'Address not shown').split(',').slice(0, 2).join(', ');
const label = (item) => item.assessment.status === 'match' ? 'Match' : 'Needs checking';
const also = (item) => (item.also || []).map(o => o.label).join(', ');
const FURNISH = {furnished: 'Furnished', flexible: 'Furnished or unfurnished', 'part furnished': 'Part furnished', unfurnished: 'Unfurnished'};
const SOURCE = {estimate: 'Zoopla estimate from EPC records', listing: 'from the advert', description: 'from the description', floorplan: 'from the floorplan'};
const person = (user) => user ? user[0].toUpperCase() + user.slice(1) : '';
const other = () => state.user === 'ash' ? 'Mansa' : 'Ash';
const groupIds = (item) => [item.id, ...(item.also || []).map(o => o.id)];
const liked = (item) => item.liked_by.includes(state.user) || (item.saved && !item.liked_by.length);  // pre-likes saves have no owner
const when = (iso) => new Date(iso).toLocaleString('en-GB', {day:'numeric', month:'short', hour:'2-digit', minute:'2-digit'});
const avatar = (user) => ['mansa', 'ash'].includes(user) ? `<img class="avatar" src="/avatar-${user}.svg" alt="" title="${person(user)}">` : '';
const motion = () => !matchMedia('(prefers-reduced-motion: reduce)').matches && window.Motion;
function ago(iso) {
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  return mins < 1 ? 'just now' : mins < 60 ? `${mins} min ago` : mins < 1440 ? `${Math.round(mins / 60)} h ago` : `${Math.round(mins / 1440)} days ago`;
}
const isNew = (item) => !!state.newSince && item.first_seen > state.newSince;
const LINK_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1.2 1.2M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2"/></svg>';
const linkButton = (item) => safeUrl(item.url) ? `<button class="bin link" data-link="${esc(safeUrl(item.url))}" aria-label="Copy link to the advert" title="Copy link">${LINK_ICON}</button>` : '';
const RULES = [['require_furnished', 'Furnished'], ['exclude_basement', 'No basement'], ['require_living_room', 'Separate living room'], ['require_floor_level', 'Floor level stated'], ['require_floorplan', 'Floorplan'], ['require_in_area', 'Chosen postcodes']];

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.error || 'Something went wrong. Please try again.');
  return result;
}
function toast(message, undo) {
  const el = $('#toast'); el.innerHTML = esc(message) + (undo ? ' <button class="toast-undo" type="button">Undo</button>' : '');
  if (undo) el.querySelector('button').addEventListener('click', () => { el.classList.remove('show'); undo(); });
  el.classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove('show'), undo ? 6000 : 3200);
}
function showApp() { $('#login').classList.toggle('hidden', !!state.user); $('#app').classList.toggle('hidden', !state.user); if (state.user) { $('#account-name').textContent = state.user; $('#account-avatar').src = `/avatar-${state.user}.svg`; refresh(); } else welcome(); }
function welcome() {
  if (welcome.done) return; welcome.done = true;
  if (window.lottie && !matchMedia('(prefers-reduced-motion: reduce)').matches) window.lottie.loadAnimation({container:$('#handshake'),renderer:'svg',loop:true,autoplay:true,path:'/handshake.json'});
  const m = motion(); if (!m) return;
  document.body.classList.add('js-motion');
  m.animate('.feature-list li', {opacity: [0, 1], y: [14, 0]}, {delay: m.stagger(0.08, {startDelay: 0.15}), duration: 0.7, ease: [0.22, 1, 0.36, 1]});
  m.animate('.login-panel', {opacity: [0, 1], y: [10, 0]}, {duration: 0.6, ease: [0.22, 1, 0.36, 1]});
}
async function refresh() {
  try {
    const data = await api('/api/listings');
    const first = !state.listings.length; Object.assign(state, {lastScan: data.last_scan, newSince: data.new_since, listings: data.listings, boroughs: data.boroughs, scan: data.scan, areas: data.areas, criteria: data.criteria, reader: data.floorplan_reader});
    render(first); if (data.scan.running) setTimeout(refresh, 2500);
  } catch (error) { toast(error.message); }
}

function renderCriteria() {
  const c = state.criteria; if (!c) return;
  $('#criteria-open').title = [`${money(c.max_price)} a month`, `${c.beds} beds`, `under ${c.max_work_minutes} min to Revolut`, ...RULES.filter(([key]) => c[key]).map(([, text]) => text)].join(' · ');
  const form = $('#criteria-form');
  if (!$('#criteria-dialog').open) for (const [key, value] of Object.entries(c)) { const input = form.elements[key]; if (!input) continue; if (input.type === 'checkbox') input.checked = value; else input.value = value; }
}
function toggleCriteria(open) {
  const dialog = $('#criteria-dialog');
  if (open) { renderCriteria(); dialog.showModal(); dialog.querySelector('input').focus(); } else dialog.close();
}

function sortListings(list) {
  if (state.sort === 'total') list.sort((a,b) => mid(a.costs.total) - mid(b.costs.total));
  if (state.sort === 'value') list.sort((a,b) => (a.extracted.price_per_sqm ?? Infinity) - (b.extracted.price_per_sqm ?? Infinity));
  if (state.sort === 'new') list.sort((a,b) => b.first_seen.localeCompare(a.first_seen));
  return list;
}
function render(animate = false) {
  const eligible = state.listings.filter(item => item.assessment.status !== 'excluded' && !item.binned);
  const saved = eligible.filter(item => item.saved);
  const shortlist = state.view === 'saved';
  const shown = sortListings(shortlist ? saved : eligible.filter(item => !item.saved));  // shortlisted flats leave Discover
  $('#saved-count').textContent = saved.length;
  $('#count-all').textContent = shown.length;
  $('#section-title').textContent = shortlist ? 'Shortlist' : 'Listings';
  $('#map-count').textContent = eligible.length + ' listings';
  $('#empty').classList.toggle('hidden', shown.length > 0);
  if (!shown.length) {
    $('#empty h3').textContent = shortlist ? 'Nothing saved yet' : 'No listings yet';
    $('#empty p').textContent = shortlist ? 'Save a listing with the heart. You both share one shortlist.' : 'Run a search to pull listings from the portals, or relax a criterion.';
    $('#empty-scan').classList.toggle('hidden', shortlist);
  }
  $('#listings').classList.toggle('shortlist', shortlist);
  $('#listings').innerHTML = shown.map(shortlist ? shortCard : card).join('');
  const m = motion(); if (animate && m) m.animate([...document.querySelectorAll('#listings > article')].slice(0, 12), {opacity: [0, 1], y: [18, 0]}, {delay: m.stagger(0.04, {startDelay: 0.05}), type: 'spring', bounce: 0.15, duration: 0.6});
  $('#last-scan').textContent = state.lastScan ? `Last searched ${ago(state.lastScan)}, ${when(state.lastScan)}` : 'No search run yet';
  $('#scan-status').textContent = state.scan?.message || 'Idle.';
  $('#status-dot').classList.toggle('busy', !!state.scan?.running);
  $('#source-status').innerHTML = Object.entries(state.scan?.sources || {}).map(([name, result]) => `<p class="source-row"><strong>${esc(name)}</strong> ${esc(result)}</p>`).join('');
  $('#scan').disabled = !!state.scan?.running;
  $('#scan').textContent = state.scan?.running ? 'Searching…' : 'Search now';
  renderCriteria();
  renderOverview(eligible);
}
const HEART = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20.5s-7.5-4.6-9.2-9.4C1.6 7.6 4 4.5 7.3 4.5c2 0 3.5 1.1 4.7 2.8 1.2-1.7 2.7-2.8 4.7-2.8 3.3 0 5.7 3.1 4.5 6.6-1.7 4.8-9.2 9.4-9.2 9.4z" stroke-linejoin="round"/></svg>';
const BIN_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/></svg>';
function heart(item, extra = '') { const on = liked(item); return `<button class="heart ${on ? 'saved' : ''} ${extra}" data-save="${esc(item.id)}" aria-pressed="${on}" aria-label="${on ? 'Remove your like' : 'Save to shortlist'}">${HEART}</button>`; }
function binButton(item) { return `<button class="bin" data-bin="${esc(item.id)}" aria-label="Bin this listing" title="Bin this listing">${BIN_ICON}</button>`; }
function notesHtml(item) {
  const note = n => `<li class="note ${n.user === state.user ? 'mine' : ''}">${avatar(n.user)}<div><span class="note-who">${esc(person(n.user))}, ${esc(when(n.created))}</span><p>${esc(n.body)}</p></div></li>`;
  const ns = item.notes;
  const thread = !ns.length ? '' : ns.length <= 2 ? `<ul class="notes">${ns.map(note).join('')}</ul>`
    : `<ul class="notes">${note(ns[0])}</ul><details class="more-notes"><summary>${ns.length - 1} more notes</summary><ul class="notes">${ns.slice(1).map(note).join('')}</ul></details>`;
  return `${thread}<form class="note-form" data-note="${esc(item.id)}"><input name="body" maxlength="1000" required aria-label="Add a note" placeholder="Add a note for ${other()}"><button class="secondary" type="submit">Add note</button></form>`;
}
function shortCard(item, index) {
  const image = safeUrl(item.photo || (item.photos || [])[0]);
  const likes = item.liked_by.length ? `<span class="avatar-stack">${item.liked_by.map(avatar).join('')}</span>Liked by ${esc(item.liked_by.map(person).join(' and '))}` : 'Saved earlier';
  return `<article class="short-card" style="--i:${Math.min(index, 12)}">
    <button class="short-thumb" data-open="${esc(item.id)}" aria-label="Open details for ${esc(shortTitle(item))}">${image ? `<img src="${esc(image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<span class="photo-empty">No photo</span>'}</button>
    <div class="short-main">
      <div class="short-top"><button class="listing-title" data-open="${esc(item.id)}">${esc(shortTitle(item))}</button><div class="title-tools">${heart(item, 'inline')}${linkButton(item)}${binButton(item)}</div></div>
      <p class="price">${money(item.price)}<span>/mo</span></p>
      <p class="liked-by">${likes}</p>
      ${notesHtml(item)}
    </div>
  </article>`;
}
function card(item, index) {
  const image = safeUrl(item.photo || (item.photos || [])[0]);
  const date = item.listed ? new Date(item.listed) : null;
  const age = date && !isNaN(date) ? Math.floor((Date.now() - date.getTime()) / 86400000) : null;
  const priceDrop = item.previous_price && item.price < item.previous_price;
  const x = item.extracted;
  const meta = [x.sqm ? `${x.sqm} m²${x.price_per_sqm ? ` · ${money(x.price_per_sqm)}/m²` : ''}` : 'Size not stated',
    item.work_minutes != null ? `<span class="${item.work_minutes >= state.criteria.max_work_minutes ? 'warn' : ''}">Revolut ${esc(item.work_minutes)} min</span>` : 'Commute pending',
    item.ucl_minutes != null ? `UCL ${esc(item.ucl_minutes)} min` : '', FURNISH[x.furnishing] || ''].filter(Boolean);
  const footnote = priceDrop ? 'Reduced from ' + money(item.previous_price) : age >= 21 ? age + ' days on the market' : item.assessment.missing.length ? item.assessment.missing.length + (item.assessment.missing.length === 1 ? ' detail to check' : ' details to check') : 'Meets every criterion';
  return `<article class="listing-card" style="--i:${Math.min(index, 12)}">
    <button class="listing-photo" data-open="${esc(item.id)}" aria-label="Open details for ${esc(shortTitle(item))}">${image ? `<img src="${esc(image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '<span class="photo-empty">No photo</span>'}<span class="badges">${isNew(item) ? '<span class="photo-label new">New</span>' : ''}${item.assessment.status === 'match' ? '<span class="photo-label match">Match</span>' : ''}</span></button>
    ${heart(item)}
    <div class="card-top"><button class="listing-title" data-open="${esc(item.id)}">${esc(shortTitle(item))}</button><div class="price">${money(item.price)}<span>/mo</span><small>(≈ ${esc(range(item.costs.total))} all-in)</small></div></div>
    <p class="listing-address">${esc(item.source_label || item.source)}${also(item) ? ' + ' + esc(also(item)) : ''}</p>
    <p class="listing-meta">${meta.join('<span class="sep" aria-hidden="true">·</span>')}</p>
    ${x.flag.length ? `<p class="flag">${esc(x.flag[0])}</p>` : ''}
    <div class="card-bottom"><span>${esc(footnote)}</span><span class="card-tools">${linkButton(item)}${binButton(item)}<button class="details-link" data-open="${esc(item.id)}">Details</button></span></div>
  </article>`;
}
function mapTiles(map) { L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors'}).addTo(map); map.attributionControl?.setPrefix('<a href="https://leafletjs.com" target="_blank" rel="noopener noreferrer">Leaflet</a>'); }
function renderOverview(items) {
  if (!window.L || $('#app').classList.contains('hidden')) return;
  if (!state.overviewMap) { state.overviewMap = L.map('overview-map', {zoomControl:false, scrollWheelZoom:false}).setView([51.515,-0.07], 11); mapTiles(state.overviewMap); state.markerLayer = L.layerGroup().addTo(state.overviewMap); }
  state.markerLayer.clearLayers();
  items.filter(i => Number.isFinite(i.lat) && Number.isFinite(i.lon)).forEach(i => L.circleMarker([i.lat,i.lon], {radius:6, color:'#fffefc', weight:2, fillColor:i.assessment.status === 'match' ? '#2f7a3d' : '#0f3e17', fillOpacity:1}).bindTooltip(esc(shortTitle(i))).on('click', () => openDetail(i.id)).addTo(state.markerLayer));
}
async function save(id) {
  const item = state.listings.find(i => i.id === id); if (!item) return;
  const mine = liked(item);
  try {
    await api('/api/save', {ids: groupIds(item), saved: !mine});
    item.liked_by = mine ? item.liked_by.filter(u => u !== state.user) : [...item.liked_by, state.user].sort();
    item.saved = item.liked_by.length > 0; render();
    const modalHeart = $('#modal-save'); if (state.active === id && modalHeart) modalHeart.outerHTML = heart(item, 'inline" id="modal-save');
    document.querySelectorAll(`[data-save="${CSS.escape(id)}"]`).forEach(el => el.classList.add('pop'));
    toast(mine ? (item.saved ? 'Removed your like' : 'Removed from shortlist') : 'Saved to shortlist');
  } catch (error) { toast(error.message); }
}
async function bin(id, binned) {
  const item = state.listings.find(i => i.id === id); if (!item) return;
  try {
    await api('/api/bin', {ids: groupIds(item), binned});
    const el = $(`#listings [data-bin="${CSS.escape(id)}"]`)?.closest('article'), m = motion();
    if (binned && el && m) await m.animate(el, {opacity: 0, scale: 0.94, filter: 'blur(2px)'}, {duration: 0.22}).finished;
    item.binned = binned;
    if (binned && state.active === id) closeDetail();
    render(!binned); if (binned) toast('Listing binned', () => bin(id, false));
  } catch (error) { toast(error.message); }
}
async function addNote(form) {
  const item = state.listings.find(i => i.id === form.dataset.note); if (!item) return;
  const body = String(new FormData(form).get('body') || '').trim(); if (!body) return;
  try { const result = await api('/api/note', {ids: groupIds(item), body}); item.notes = [...item.notes, result.note]; render(); toast('Note added'); }
  catch (error) { toast(error.message); }
}

function costHtml(cost) {
  if (!cost.total) return '<p>Rent is not stated, so there is no estimate yet.</p>';
  const rows = [['Council tax', cost.council_monthly, `Band ${cost.band}${cost.borough ? ', ' + cost.borough : ', median of your boroughs'}`],
    ['Energy', cost.energy, cost.bills_included ? 'Bills included' : `EPC ${cost.epc}, illustrative range`],
    ['Water and broadband', cost.water_wifi, cost.bills_included ? 'Bills included' : 'Fixed allowance']];
  return `<table class="cost-table"><tbody>${rows.map(([name, value, note]) => `<tr><th>${name}<small>${esc(note)}</small></th><td>${Array.isArray(value) ? range(value) : money(value)}</td></tr>`).join('')}</tbody><tfoot><tr><th>All-in, per month</th><td>${range(cost.total)}</td></tr></tfoot></table>${cost.assumed.length ? `<p class="assumed">Estimate assumes ${esc(cost.assumed.join(', '))}. Enter the real figures below to tighten it.</p>` : ''}`;
}
function areaHtml(item) {
  const loc = item.location || {};
  if (loc.in_area === true) return `In your chosen postcodes${loc.borough ? ', ' + esc(loc.borough) : ''}${loc.basis === 'postcode' ? ' (' + esc(loc.postcode) + ')' : loc.distance_m != null ? ` (pin about ${loc.distance_m} m from ${esc(loc.nearest)})` : ''}.`;
  if (loc.in_area === null) return 'Postcode area not confirmed. Check the full postcode before going further.';
  return `Outside your chosen postcodes${loc.nearest ? `. Nearest is ${esc(loc.nearest)}, about ${(loc.distance_m / 1000).toFixed(1)} km away` : ''}.`;
}
function bandTable(borough, bands, current) {
  if (!borough || !bands || !Object.keys(bands).length) return '';
  const keys = 'ABCDEFGH'.split('');
  const cell = (b, value) => `<td class="${b === current ? 'current' : ''}">${value == null ? '–' : money(value)}</td>`;
  return `<table class="band-table"><caption>${esc(borough)} council tax before discounts</caption><thead><tr><th>Band</th>${keys.map(b => `<th class="${b === current ? 'current' : ''}">${b}</th>`).join('')}</tr></thead><tbody><tr><th>Year</th>${keys.map(b => cell(b, bands[b])).join('')}</tr><tr><th>Month</th>${keys.map(b => cell(b, bands[b] == null ? null : bands[b] / 12)).join('')}</tr></tbody></table>`;
}
function options(values, current, empty) { return `<option value="">${empty}</option>` + values.map(v => `<option value="${esc(v)}" ${v === current ? 'selected' : ''}>${esc(v)}</option>`).join(''); }
const list = (items, empty) => items.length ? `<ul>${items.map(v => `<li>${esc(v)}</li>`).join('')}</ul>` : `<p class="muted">${empty}</p>`;
function factRows(item) {
  const x = item.extracted, c = item.costs;
  const rows = [['Floor area', x.sqm ? `${x.sqm} m² (${SOURCE[x.sqm_source]})${x.price_per_sqm ? `, ${money(x.price_per_sqm)} per m²` : ''}` : 'Not stated'],
    ['EPC rating', x.epc ? `${x.epc} (${SOURCE[x.epc_source] || 'entered'})` : `Not stated, D assumed`],
    ['Council tax band', x.tax_band ? `${x.tax_band} (${SOURCE[x.tax_source] || 'entered'})` : `Not stated, D assumed`],
    ['Furnishing', FURNISH[x.furnishing] || 'Not stated'], ['Bathrooms', x.bathrooms ?? (x.pro.includes('Two bathrooms') ? 'Two, from the description' : 'Not stated')],
    ['Borough', c.borough || 'Not known']];
  return `<dl class="facts">${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>`;
}
function termsHtml(item) {
  const terms = [...item.assessment.terms].sort((a, b) => b.points - a.points);
  return `<ul class="terms">${terms.map(t => `<li><span>${esc(t.label)}</span><strong class="${t.points >= 0 ? 'up' : 'down'}">${t.points >= 0 ? '+' : '−'}${Math.abs(t.points)}</strong></li>`).join('')}</ul><p class="muted">Best-fit score ${item.assessment.score}. An average flat scores 100.</p>`;
}
function floorplanHtml(item) {
  if (!(item.floorplans || []).length) return '<p class="muted">This advert has no floorplan.</p>';
  const a = item.floorplan_analysis;
  return `<div id="floorplan-area">${a ? analysisHtml(a) : ''}<button class="secondary" id="show-floorplan">Show floorplan</button></div>`;
}
function analysisHtml(a) {
  const bits = [a.total_sqm ? `${Math.round(a.total_sqm)} m² in total` : '', a.second_bedroom_sqm ? `second bedroom about ${a.second_bedroom_sqm.toFixed(1)} m²` : '', a.separate_living_room === true ? 'separate living room' : a.separate_living_room === false ? 'no separate living room' : '', a.bathrooms ? `${a.bathrooms} bathroom${a.bathrooms > 1 ? 's' : ''}` : ''].filter(Boolean);
  return `<div class="analysis"><strong>Read from the floorplan</strong><p>${esc(a.summary || '')}</p>${bits.length ? `<p>${esc(bits.join(', '))}.</p>` : ''}</div>`;
}
function openDetail(id) {
  const item = state.listings.find(i => i.id === id); if (!item) return;
  if (!state.active) state.lastFocus = document.activeElement;
  state.active = id;
  if (state.detailMap) { state.detailMap.remove(); state.detailMap = null; }
  const image = safeUrl(item.photo || (item.photos || [])[0]);
  const listingUrl = safeUrl(item.url);
  const x = item.extracted, checks = [...item.assessment.excluded, ...item.assessment.missing];
  $('#modal-body').innerHTML = `${image ? `<img class="modal-photo" src="${esc(image)}" alt="Advertised photo of ${esc(shortTitle(item))}" referrerpolicy="no-referrer">` : ''}
    <p class="modal-kicker"><span class="photo-label ${item.assessment.status}">${esc(label(item))}</span> ${esc(item.source_label || item.source)}${also(item) ? ' + ' + esc(also(item)) : ''}</p>
    <div class="title-row"><h2 id="modal-title">${esc(shortTitle(item))}</h2><span class="title-tools"><span class="info"><button class="info-button" type="button" aria-label="Why it ranks here" aria-describedby="rank-tip">i</button><span class="tip" role="tooltip" id="rank-tip"><strong>Why it ranks here</strong>${termsHtml(item)}</span></span>${heart(item, 'inline" id="modal-save')}${linkButton(item)}${binButton(item)}</span></div><p class="modal-address">${esc(item.address || '')}</p><p class="modal-area">${areaHtml(item)}</p>
    <p class="modal-price">${money(item.price)} <span>/ month</span> <small>(≈ ${esc(range(item.costs.total))} all-in)</small></p>
    <div class="modal-actions">${listingUrl ? `<a href="${esc(listingUrl)}" target="_blank" rel="noopener noreferrer" class="primary">Open on ${esc(item.source_label || 'portal')}</a>${(item.also || []).filter(o => safeUrl(o.url)).map(o => `<a href="${esc(safeUrl(o.url))}" target="_blank" rel="noopener noreferrer">${esc(o.label)}</a>`).join('')}` : ''}<button data-copy="message">Copy viewing message</button></div>
    ${x.flag.length ? `<div class="flags">${x.flag.map(f => `<p class="flag">${esc(f)}</p>`).join('')}</div>` : ''}
    <div class="detail-grid"><section><h3>Pros</h3>${list(x.pro, 'Nothing notable in the advert.')}</section><section><h3>Cons</h3>${list(x.con, 'None mentioned in the advert.')}</section></div>
    <h3>Against your criteria</h3>${list(checks, 'Meets every criterion.')}
    <h3>Key facts</h3>${factRows(item)}
    <h3>Floorplan</h3>${floorplanHtml(item)}
    <h3>Commute</h3><div id="detail-map" class="detail-map"></div><div class="modal-actions"><button id="check-journey">Recheck TfL journeys</button></div><div id="journey-results" class="journey-results">${routeHtml(item.journeys)}</div>
    <h3>Monthly cost</h3><div id="cost-result">${costHtml(item.costs)}</div>
    <details class="fix-costs"><summary>Correct council tax or EPC</summary><div id="band-table">${bandTable(item.borough, item.council_bands, x.tax_band)}</div><form id="facts-form" class="facts-form"><label>Council<select name="borough">${options(state.boroughs, item.borough, 'Select borough')}</select></label><label>Council tax band<select name="tax_band">${options('ABCDEFGH'.split(''), item.tax_band, 'Not known')}</select></label><label>EPC rating<select name="epc">${options('ABCDEFG'.split(''), item.epc, 'Not known')}</select></label><button class="secondary" type="submit">Save</button></form><form id="epc-form" class="epc-search"><input name="postcode" placeholder="Full postcode" aria-label="Full postcode" required><button class="secondary">Find EPC certificates</button></form><div id="epc-results" class="epc-results"></div></details>
    ${item.description ? `<details class="advert"><summary>Advert text</summary><p>${esc(item.description)}</p></details>` : ''}
    <h3>History</h3><p class="muted">First seen ${esc(new Date(item.first_seen).toLocaleDateString('en-GB'))}. ${item.listed ? 'Advertised ' + esc(new Date(item.listed).toLocaleDateString('en-GB')) + '.' : ''} ${item.previous_price && item.price < item.previous_price ? 'Previously ' + money(item.previous_price) + '.' : ''}</p>`;
  $('#modal').classList.remove('hidden'); document.body.style.overflow = 'hidden';
  requestAnimationFrame(() => $('#modal').classList.add('open'));
  $('#close-modal').focus(); $('.modal-content').scrollTop = 0;
  if (window.L && Number.isFinite(item.lat) && Number.isFinite(item.lon)) {
    state.detailMap = L.map('detail-map', {scrollWheelZoom:false}).setView([item.lat,item.lon], 12); mapTiles(state.detailMap);
    L.circleMarker([item.lat,item.lon], {radius:7,color:'#0f3e17',fillColor:'#0f3e17',fillOpacity:1}).addTo(state.detailMap).bindTooltip('Listing location (may be approximate)');
    drawRoutes(item.journeys);
    setTimeout(() => state.detailMap?.invalidateSize(), 250);
  } else $('#detail-map').innerHTML = '<p class="muted" style="padding:20px">No map position for this listing.</p>';
}
function routeHtml(routes) {
  if (!routes || !Object.keys(routes).length) return '<p class="muted">Commutes have not been checked yet.</p>';
  return Object.entries(routes).map(([name, route]) => `<p><strong>${name === 'work' ? 'Revolut' : 'UCL Bloomsbury'}: ${esc(route.minutes)} minutes</strong><br>${route.legs.map(leg => esc(leg.summary) + (leg.duration != null ? ` (${esc(leg.duration)} min)` : '')).join(' → ')}</p>`).join('');
}
function drawRoutes(routes) {
  if (!state.detailMap || !routes) return;
  Object.entries(routes).forEach(([name, route]) => route.legs.forEach(leg => { if (!leg.path) return; try { const points = typeof leg.path === 'string' ? JSON.parse(leg.path) : leg.path; if (Array.isArray(points) && points.length) L.polyline(points, {color:name === 'work' ? '#0f3e17' : '#577d91', weight:4, opacity:.8}).addTo(state.detailMap); } catch { /* TfL occasionally returns no drawable line. */ } }));
}
function closeDetail() {
  $('#modal').classList.remove('open'); document.body.style.overflow = ''; state.active = null;
  setTimeout(() => { if (state.active) return; $('#modal').classList.add('hidden'); if (state.detailMap) { state.detailMap.remove(); state.detailMap = null; } }, 220);
  state.lastFocus?.focus?.();
}
async function copy(value) { try { await navigator.clipboard.writeText(value); toast(value.startsWith('http') ? 'Link copied' : 'Copied to clipboard'); } catch { toast('Clipboard unavailable. Please copy it by hand.'); } }
async function floorplan(target, item, read) {
  target.disabled = true; target.textContent = read ? 'Reading room sizes…' : 'Loading floorplan…';
  try {
    const result = await api('/api/floorplan', {id: item.id, read});
    if (result.analysis) { item.floorplan_analysis = result.analysis; }
    const reader = !result.analysis ? (state.reader ? '<button class="secondary" id="read-floorplan">Read room sizes</button>' : '') : '';
    $('#floorplan-area').innerHTML = `${result.analysis ? analysisHtml(result.analysis) : ''}<div class="floorplans">${result.floorplans.map(url => `<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer"><img src="${esc(safeUrl(url))}" alt="Floorplan" loading="lazy" referrerpolicy="no-referrer"></a>`).join('')}</div>${reader}`;
    if (read) await refresh();
  } catch (error) { toast(error.message); target.disabled = false; target.textContent = read ? 'Read room sizes' : 'Show floorplan'; }
}

$('#login-form').addEventListener('submit', async e => { e.preventDefault(); $('#login-error').textContent = ''; try { const result = await api('/api/login', Object.fromEntries(new FormData(e.target))); state.user = result.user; e.target.reset(); showApp(); } catch (error) { $('#login-error').textContent = error.message; } });
$('#logout').addEventListener('click', async () => { try { await api('/api/logout', {}); state.user = null; showApp(); } catch (error) { toast(error.message); } });
for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', () => { state.view = button.dataset.view; document.querySelectorAll('[data-view]').forEach(el => el.classList.toggle('active', el === button)); render(true); window.scrollTo({top: Math.min(scrollY, $('.results-heading').offsetTop - 80), behavior: 'smooth'}); });
$('#sort').addEventListener('change', e => { state.sort = e.target.value; render(true); });
$('#criteria-open').addEventListener('click', () => toggleCriteria(true));
$('#criteria-dialog').addEventListener('click', e => { if (e.target === e.currentTarget) toggleCriteria(false); });
$('#criteria-cancel').addEventListener('click', () => toggleCriteria(false));
$('#criteria-form').addEventListener('submit', async e => {
  e.preventDefault(); const form = e.target, data = {};
  for (const input of form.elements) if (input.name) data[input.name] = input.type === 'checkbox' ? input.checked : Number(input.value);
  try { const result = await api('/api/criteria', data); state.criteria = result.criteria; toggleCriteria(false); await refresh(); toast('Criteria saved'); } catch (error) { toast(error.message); }
});
async function startScan() { try { await api('/api/scan', {}); await refresh(); } catch (error) { toast(error.message); } }
$('#scan').addEventListener('click', startScan); $('#empty-scan').addEventListener('click', startScan);
$('#close-modal').addEventListener('click', closeDetail); $('.modal-shade').addEventListener('click', closeDetail);
document.addEventListener('keydown', e => {
  if (!state.active) return;
  if (e.key === 'Escape') closeDetail();
  if (e.key === 'Tab') { // keep focus inside the open panel
    const focusable = [...$('.modal-content').querySelectorAll('a[href],button:not([disabled]),input,select,summary')];
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
document.addEventListener('animationend', e => e.target.classList?.remove('pop'));
document.addEventListener('click', async e => {
  const target = e.target.closest('button'); if (!target) return;
  if (target.dataset.open) openDetail(target.dataset.open);
  if (target.dataset.save) save(target.dataset.save);
  if (target.dataset.bin) bin(target.dataset.bin, true);
  if (target.dataset.link) { copy(target.dataset.link); target.classList.add('pop'); }
  const item = state.listings.find(i => i.id === state.active);
  if (!item) return;
  if (target.dataset.copy === 'message') copy(`Hi, I've just seen your property at ${shortTitle(item)} and would love to arrange a viewing. We are two 22-year-old women: one is a working professional and the other is a medical student at UCL. Please let us know if the property is still available and when we might be able to view it. Thank you!`);
  if (target.id === 'show-floorplan') floorplan(target, item, false);
  if (target.id === 'read-floorplan') floorplan(target, item, true);
  if (target.id === 'check-journey') {
    target.disabled = true; target.textContent = 'Checking TfL…';
    try { const result = await api('/api/journey', {id:item.id}); if (Object.keys(result.journeys).length) { item.journeys = result.journeys; $('#journey-results').innerHTML = routeHtml(result.journeys); drawRoutes(result.journeys); } if (Object.keys(result.errors).length) $('#journey-results').insertAdjacentHTML('beforeend', `<p class="muted">Could not get: ${esc(Object.entries(result.errors).map(([key,value]) => key + ': ' + value).join('; '))}.</p>`); await refresh(); } catch (error) { toast(error.message); } finally { target.disabled = false; target.textContent = 'Recheck TfL journeys'; }
  }
});
document.addEventListener('submit', async e => {
  if (e.target.dataset.note) { e.preventDefault(); return addNote(e.target); }
  if (e.target.id === 'facts-form') { e.preventDefault(); try { const result = await api('/api/facts', {id:state.active, ...Object.fromEntries(new FormData(e.target))}); $('#cost-result').innerHTML = costHtml(result.costs); $('#band-table').innerHTML = bandTable(result.borough, result.council_bands, result.tax_band); await refresh(); toast('Cost details saved'); } catch (error) { toast(error.message); } }
  if (e.target.id === 'epc-form') { e.preventDefault(); const resultEl = $('#epc-results'); resultEl.textContent = 'Searching certificates…'; try { const result = await api('/api/epc', {id:state.active, ...Object.fromEntries(new FormData(e.target))}); resultEl.innerHTML = result.results.length ? result.results.map(row => `<p>${esc([row.addressLine1,row.addressLine2,row.addressLine3].filter(Boolean).join(', '))}: ${esc(row.currentEnergyEfficiencyBand || row.currentEnergyRating || row.energyRating || 'rating not shown')}, ${esc(row.registrationDate || row.lodgementDate || '')}</p>`).join('') : 'No certificates found. Check the postcode.'; } catch (error) { resultEl.textContent = error.message; } }
});
(async function init() { if (window.lottie && !matchMedia('(prefers-reduced-motion: reduce)').matches) window.lottie.loadAnimation({container:$('#animation'),renderer:'svg',loop:true,autoplay:true,path:'/animation.json'}); try { const result = await api('/api/me'); state.user = result.user; showApp(); } catch (error) { $('#login').classList.remove('hidden'); welcome(); $('#login-error').textContent = error.message; } finally { $('#preloader').classList.add('hidden'); } })();
