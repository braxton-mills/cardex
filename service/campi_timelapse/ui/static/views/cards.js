// Cards: every car model on a Pokémon-style trading card with a live 3D model. The finish (plain, reverse holo, holo,
// full art, special illustration rare) comes from how rarely that model is seen (/api/cards). Drag a card to tilt it
// and watch the foil; click one to inspect it full screen. Rendering lives in ../cards/ (three.js, one WebGL context).
import {
  api, counter, empty, fill, fmt, h, icon, iconButton, noSightings, offBanner, prefs, qs, reduceMotion, selection, sightingsOff,
  stagger, tabs, toast, viewHead,
} from '../lib.js';
import { FINISH_ORDER, FINISH_SHORT } from '../cards/styles.js';

const SHOWS = [['caught', 'Caught'], ['all', 'All'], ['uncaught', 'Not yet']];
const SORTS = [['number', 'Set number'], ['rarest', 'Rarest first'], ['count', 'Most seen'], ['recent', 'Recently seen']];
const RANK = Object.fromEntries(FINISH_ORDER.map((f, i) => [f, i]));

export async function mount(el, _params, ctx) {
  const [st, c] = await Promise.all([api('/api/status').catch(() => null), api('/api/cards').catch((e) => ({ error: e }))]);
  const head = viewHead();
  el.append(head);
  if (noSightings(st)) { el.append(sightingsOff()); return { unmount() {} }; }
  if (c.error) { el.append(empty('Could not load the cards', c.error.message)); return { unmount() {} }; }
  const banner = offBanner(st);
  if (banner) el.append(banner);

  let show = prefs.get('cardsShow', 'caught');
  let sort = prefs.get('cardsSort', 'rarest');
  let only = prefs.get('cardsFinish', null);
  let scanned = prefs.get('cardsScanned', false);
  const meshes = c.items.filter((i) => i.mesh).length;
  const byFinish = Object.fromEntries(FINISH_ORDER.map((f) => [f, c.items.filter((i) => i.finish === f).length]));
  const finishInfo = Object.fromEntries(c.finishes.map((f) => [f.finish, f]));

  // ---- header: set completion, finish counts, renderer
  const gpuLine = h('span', { class: 'muted' }, 'Starting the renderer…');
  const presetSel = h('select', { class: 'pill-select', title: 'Render quality', onchange: (e) => engine?.setPreset(e.target.value) });
  el.append(h('section', { class: 'panel cards-head' },
    h('div', { class: 'ch-set' },
      h('span', { class: 'cap' }, 'Campi set 1'),
      h('h2', {}, h('b', {}, counter('cards-caught', c.caught)), h('span', { class: 'muted' }, ` / ${c.set_total} cards`)),
      h('div', { class: 'progress' }, h('div', { class: 'bar', style: { width: `${c.set_total ? (c.caught / c.set_total) * 100 : 0}%` } }))),
    h('div', { class: 'ch-finishes' }, FINISH_ORDER.map((f) => h('button', {
      class: `fchip f-${f} ${only === f ? 'on' : ''}`,
      title: `${finishInfo[f]?.label || FINISH_SHORT[f]}${finishInfo[f]?.max_pct != null ? ` — rarest ${Math.round(finishInfo[f].max_pct * 100)}% of models seen twice+` : f === 'sir' ? ' — seen exactly once' : ''}`,
      onclick: () => { only = only === f ? null : f; prefs.set('cardsFinish', only); draw(); syncChips(); },
    }, h('i', {}), h('span', {}, FINISH_SHORT[f]), h('b', { class: 'num' }, String(byFinish[f]))))),
    h('div', { class: 'ch-gpu' }, icon('chip', 16), gpuLine, presetSel)));
  const syncChips = () => el.querySelectorAll('.fchip').forEach((b) => b.classList.toggle('on', b.classList.contains(`f-${only}`)));

  // no native image/text drag: it would cancel the pointer drag that tilts a card
  const grid = h('div', { class: 'tcard-grid', ondragstart: (e) => e.preventDefault() });
  el.append(grid);

  // ---- engine (three.js is only loaded for this tab)
  let engine = null;
  try {
    const { CardEngine, PRESETS } = await import('../cards/engine.js');
    engine = new CardEngine({
      main: document.querySelector('#main'), preset: prefs.get('cardsPreset', null), reduceMotion,
      onPresetChange: (k) => prefs.set('cardsPreset', k),
    });
    engine.total = c.set_total;
    engine.scanned = scanned;
    fill(presetSel, Object.entries(PRESETS).map(([k, p]) => h('option', { value: k, selected: k === engine.presetKey }, p.label)));
    fill(gpuLine, 'Rendering on ', h('b', {}, engine.gpu.raw.replace(/^ANGLE \(|\)$/g, '').split(',').slice(-2, -1)[0]?.trim() || engine.gpu.raw));
    engine.attachGrid(grid, (item) => item && open(item));
    if (ctx.isHidden()) engine.pause();
  } catch (e) {
    console.error(e);
    grid.classList.add('no-gl');
    fill(gpuLine, `3D off: ${e.message}`);
    presetSel.remove();
  }

  // ---- HUD (G)
  const hud = h('div', { class: 'cards-hud', hidden: !prefs.get('cardsHud', false) });
  document.body.append(hud);
  if (engine) engine.onStats = (s) => {
    if (hud.hidden) return;
    fill(hud, h('b', {}, `${s.fps.toFixed(0)} fps`), ` · ${s.preset} · ${s.mode}`, h('br'), s.gpu, h('br'),
      `${s.calls} draws · ${(s.triangles / 1000).toFixed(0)}k tris · ${s.px}`, h('br'),
      `${s.cards} cards built · ${s.visible} near view · ${s.textures} tex`);
  };

  function draw() {
    fill(head,
      tabs(SHOWS, (k) => show === k, (k) => { show = k; prefs.set('cardsShow', k); draw(); }),
      h('div', { class: 'grow' }),
      meshes ? h('label', { class: 'pill-toggle', title: `${meshes} model(s) have a 3D scan made from the best photo` },
        h('input', { type: 'checkbox', checked: scanned, onchange: (e) => { scanned = e.target.checked; prefs.set('cardsScanned', scanned); if (engine) { engine.scanned = scanned; draw(); } } }),
        h('span', {}, `Scanned models (${meshes})`)) : null,
      h('select', { class: 'pill-select', onchange: (e) => { sort = e.target.value; prefs.set('cardsSort', sort); draw(); } },
        SORTS.map(([k, l]) => h('option', { value: k, selected: sort === k }, l))));
    let items = c.items.filter((i) => (show === 'all' || (show === 'caught' ? i.count > 0 : !i.count)) && (!only || i.finish === only));
    if (sort === 'rarest') items = [...items].sort((a, b) => (RANK[a.finish] ?? 9) - (RANK[b.finish] ?? 9) || a.count - b.count || a.number - b.number);
    if (sort === 'count') items = [...items].sort((a, b) => b.count - a.count);
    if (sort === 'recent') items = [...items].sort((a, b) => (b.last_seen_at || '').localeCompare(a.last_seen_at || ''));
    engine?.clear();
    const nodes = items.map(tile);
    fill(grid, ...stagger(nodes));
    if (!items.length) grid.append(empty('No cards here yet', only ? `No ${FINISH_SHORT[only]} cards with this filter.` : null));
    nodes.forEach((n, i) => engine?.register(n, items[i]));
    selection.set(nodes, { open: (n) => open(items[nodes.indexOf(n)]) });
  }

  function tile(i) {
    const name = i.generic ? i.label : (i.model || i.label);
    return h('article', {
      class: `tcard f-${i.finish || 'none'}`, tabindex: '-1',
      'aria-label': `${i.label}: ${i.count ? `${FINISH_SHORT[i.finish]}, seen ${i.count} times` : 'not seen yet'}`,
    },
    h('div', { class: 'tc-fallback' },
      i.cover?.crop ? h('img', { src: i.cover.crop, loading: 'lazy', alt: '', draggable: 'false' }) : h('div', { class: 'tc-nocrop' }, icon('car', 40)),
      h('div', { class: 'tc-name' }, h('span', { class: 'cap' }, i.generic ? 'type' : i.make || ''), h('b', {}, name)),
      h('div', { class: 'tc-meta' }, h('span', {}, i.finish ? FINISH_SHORT[i.finish] : 'not yet'), h('b', { class: 'num' }, `${i.count}×`))));
  }

  // ---- inspector
  let insp = null;
  function open(item) {
    if (!item) return;
    if (!engine) { if (item.count) location.hash = `#/sightings${qs({ label: item.label })}`; return; }
    close();
    engine.openInspector(item, { scanned });
    const finish = item.count ? item.finish : null;
    // input layer (below the WebGL canvas, which is click-through) + chrome layer (above the canvas)
    const stage = h('div', { class: 'cards-insp' });
    const ui = h('div', { class: 'cards-insp-ui', role: 'dialog', 'aria-label': `${item.label} card` });
    const autoBtn = iconButton('play', 'Auto tilt (A)', () => { engine.insp.auto = !engine.insp.auto; autoBtn.classList.toggle('on', engine.insp.auto); });
    autoBtn.classList.toggle('on', !!engine.insp?.auto);
    const modelBtn = item.mesh ? h('button', {
      class: 'pill-btn', title: 'Switch between the procedural model and the 3D scan',
      onclick: () => { const c3 = engine.insp.card; c3.setCar(c3.carKind === 'scanned' ? 'procedural' : 'scanned'); modelBtn.textContent = c3.carKind === 'scanned' ? 'Scan' : 'Procedural'; },
    }, scanned ? 'Scan' : 'Procedural') : null;
    ui.append(
      h('div', { class: 'ci-top' },
        h('div', { class: 'ci-title' },
          h('span', { class: `fbadge f-${finish || 'none'}` }, finish ? (finishInfo[finish]?.label || FINISH_SHORT[finish]) : 'Not caught yet'),
          h('h2', {}, item.label)),
        h('div', { class: 'grow' }),
        modelBtn, autoBtn,
        iconButton('refresh', 'Flip (F or double-click)', () => engine.flip()),
        iconButton('x', 'Close (Esc)', close)),
      h('div', { class: 'ci-bottom' },
        h('div', { class: 'ci-stats' },
          stat('Seen', `${item.count}×`), stat('First', item.first_seen_at ? fmt.date(item.first_seen_at) : '—'),
          stat('Last', item.last_seen_at ? `${fmt.date(item.last_seen_at)} ${fmt.hm(item.last_seen_at)}` : '—'),
          stat('Paint', item.color ? `${item.color.name}${item.color.known ? '' : ' (guess)'}` : '—'),
          item.year_range ? stat('Years', item.year_range) : null),
        h('div', { class: 'grow' }),
        h('span', { class: 'muted ci-hint' }, 'Drag to tilt · double-click to flip · scroll to zoom · click outside or Esc to close'),
        item.count ? h('a', { class: 'pill-btn', href: `#/sightings${qs({ label: item.label })}` }, 'Sightings', icon('arrowRight', 16)) : null));
    document.body.append(stage, ui);
    engine.attachInspector(stage, close);
    requestAnimationFrame(() => { stage.classList.add('in'); ui.classList.add('in'); });
    insp = { stage, ui };
  }
  const stat = (k, v) => h('div', {}, h('span', { class: 'cap' }, k), h('b', {}, v));
  function close() {
    if (!insp) return;
    insp.stage.remove();
    insp.ui.remove();
    insp = null;
    engine?.closeInspector();
  }

  const onKey = (e) => {
    const t = e.target;
    if (t instanceof HTMLInputElement || t instanceof HTMLSelectElement || e.ctrlKey || e.altKey || e.metaKey) return;
    const k = e.key.toLowerCase();
    if (insp && e.key === 'Escape') { close(); e.stopImmediatePropagation(); return; }
    if (k === 'g') { hud.hidden = !hud.hidden; prefs.set('cardsHud', !hud.hidden); }
    if (insp && k === 'f') engine?.flip();
    if (insp && k === 'a' && engine?.insp) engine.insp.auto = !engine.insp.auto;
  };
  window.addEventListener('keydown', onKey, true);

  draw();
  if (engine && !c.caught) toast('No cars caught yet: the cards fill in as sightings come in.');
  return {
    unmount() {
      window.removeEventListener('keydown', onKey, true);
      close();
      hud.remove();
      engine?.dispose();
    },
    onVisible(v) { if (!engine) return; if (v) engine.resume(); else engine.pause(); },
  };
}
