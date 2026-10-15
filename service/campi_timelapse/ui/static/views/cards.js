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
  const meshes = c.items.filter((i) => i.mesh).length;
  const models = c.items.filter((i) => i.sketchfab).length;
  // preferred car: Sketchfab model, scan, or procedural; one this set has none of shows (and draws) procedural, so the
  // picker and the cards agree, without forgetting the preference for when models arrive
  let source = prefs.get('cardsCar', 'model');
  if ((source === 'model' && !models) || (source === 'scanned' && !meshes)) source = 'procedural';
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
    engine.source = source;
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
      models || meshes ? h('select', {
        class: 'pill-select', title: 'Which 3D car the cards show (cards without one show the procedural car)',
        onchange: (e) => { source = e.target.value; prefs.set('cardsCar', source); if (engine) { engine.source = source; draw(); } },
      }, [['model', `Sketchfab models (${models})`, models], ['scanned', `Photo scans (${meshes})`, meshes], ['procedural', 'Procedural cars', 1]]
        .filter(([, , n]) => n).map(([k, l]) => h('option', { value: k, selected: source === k }, l))) : null,
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
    engine.openInspector(item, { source });
    const finish = item.count ? item.finish : null;
    // input layer (below the WebGL canvas, which is click-through) + chrome layer (above the canvas)
    const stage = h('div', { class: 'cards-insp' });
    const ui = h('div', { class: 'cards-insp-ui', role: 'dialog', 'aria-label': `${item.label} card` });
    const autoBtn = iconButton('play', 'Auto tilt (A)', () => { engine.insp.auto = !engine.insp.auto; autoBtn.classList.toggle('on', engine.insp.auto); });
    autoBtn.classList.toggle('on', !!engine.insp?.auto);
    // the car: cycle through the kinds this card has; attribution for a Sketchfab model; the model picker
    const kinds = () => ['model', 'scanned', 'procedural'].filter((k) => k === 'procedural' || (k === 'model' ? item.sketchfab : item.mesh));
    const kindName = { model: 'Model', scanned: 'Scan', procedural: 'Procedural' };
    const credit = h('span', { class: 'ci-credit muted' });
    const modelBtn = h('button', {
      class: 'pill-btn', title: 'Switch the car: Sketchfab model, photo scan or procedural',
      onclick: () => { const c3 = engine.insp.card, ks = kinds(); c3.setCar(ks[(ks.indexOf(c3.carKind) + 1) % ks.length]); syncCar(); },
    });
    const syncCar = () => {
      const k = engine.insp?.card.carKind;
      modelBtn.textContent = kindName[k] || 'Procedural';
      modelBtn.hidden = kinds().length < 2;
      const m = item.sketchfab;
      fill(credit, k === 'model' && m ? ['3D model ', h('a', { href: m.page, target: '_blank', rel: 'noopener' }, m.name),
        ` by ${m.author}`, m.license ? [' · ', m.license_url ? h('a', { href: m.license_url, target: '_blank', rel: 'noopener' }, m.license) : m.license] : null] : []);
    };
    const pickBtn = !item.generic ? h('button', { class: 'pill-btn', title: 'Choose the Sketchfab model for this car', onclick: () => togglePicker() }, 'Choose model…') : null;
    ui.append(
      h('div', { class: 'ci-top' },
        h('div', { class: 'ci-title' },
          h('span', { class: `fbadge f-${finish || 'none'}` }, finish ? (finishInfo[finish]?.label || FINISH_SHORT[finish]) : 'Not caught yet'),
          h('h2', {}, item.label)),
        h('div', { class: 'grow' }),
        modelBtn, pickBtn, autoBtn,
        iconButton('refresh', 'Flip (F or double-click)', () => engine.flip()),
        iconButton('x', 'Close (Esc)', close)),
      h('div', { class: 'ci-bottom' },
        h('div', { class: 'ci-stats' },
          stat('Seen', `${item.count}×`), stat('First', item.first_seen_at ? fmt.date(item.first_seen_at) : '—'),
          stat('Last', item.last_seen_at ? `${fmt.date(item.last_seen_at)} ${fmt.hm(item.last_seen_at)}` : '—'),
          stat('Paint', item.color ? `${item.color.name}${item.color.known ? '' : ' (guess)'}` : '—'),
          item.year_range ? stat('Years', item.year_range) : null),
        h('div', { class: 'grow' }),
        credit,
        h('span', { class: 'muted ci-hint' }, 'Drag to tilt · double-click to flip · scroll to zoom · click outside or Esc to close'),
        item.count ? h('a', { class: 'pill-btn', href: `#/sightings${qs({ label: item.label })}` }, 'Sightings', icon('arrowRight', 16)) : null));
    document.body.append(stage, ui);
    engine.attachInspector(stage, close);
    requestAnimationFrame(() => { stage.classList.add('in'); ui.classList.add('in'); });
    insp = { stage, ui, picker: null, poll: null };
    syncCar();

    // ---- model picker: Sketchfab candidates (scored by `campi models`), orientation and paint fixes
    const route = `/api/cards/models/${encodeURIComponent(item.label)}`;
    async function togglePicker() {
      if (insp.picker) { closePicker(); return; }
      const panel = h('div', { class: 'ci-models', onpointerdown: (e) => e.stopPropagation(), onwheel: (e) => e.stopPropagation() });
      insp.picker = panel;
      ui.append(panel);
      fill(panel, h('div', { class: 'muted' }, 'Loading candidates…'));
      try { render(await api(route)); } catch (e) { fill(panel, h('div', { class: 'muted' }, `Could not load: ${e.message}`)); }
    }
    function closePicker() {
      clearTimeout(insp?.poll);
      insp?.picker?.remove();
      if (insp) insp.picker = null;
    }
    async function send(body) {
      try { render(await api(route, { method: 'POST', body })); } catch (e) { toast(e.message, 'error'); }
    }
    function render(r) {
      const panel = insp?.picker;
      if (!panel) return;
      const cur = r.current;
      // a model that changed (picked, rotated) or a paint fix: show it on the card now
      const changed = cur?.url?.split('?')[0] !== item.sketchfab?.url?.split('?')[0] || JSON.stringify(cur?.paint) !== JSON.stringify(item.sketchfab?.paint);
      item.sketchfab = cur;
      if (changed) { engine.insp?.card.setCar(cur ? 'model' : 'procedural'); syncCar(); }
      clearTimeout(insp.poll);
      if (r.pending) insp.poll = setTimeout(() => api(route).then(render).catch(() => {}), 4000);
      const inUse = cur?.uid || null;
      const status = r.pick?.rejected ? 'None: this card shows the procedural car.'
        : r.pending ? (r.enabled ? 'Queued: the models pass applies it within a minute.' : 'Queued: run campi models (or turn on [cards] models_enabled).')
          : cur ? `${cur.picked === 'user' ? 'Your pick' : 'Picked automatically'}.` : r.candidates.length ? 'No model yet.' : 'No candidates yet: run campi models.';
      const yaw = Number(r.pick?.yaw || 0);
      fill(panel,
        h('div', { class: 'cm-head' }, h('b', {}, '3D model'), h('div', { class: 'grow' }), iconButton('x', 'Close', closePicker)),
        h('p', { class: 'muted cm-status' }, status),
        h('div', { class: 'cm-actions' },
          cur ? [
            h('button', { class: 'pill-btn', title: 'Turn the model a quarter (when it sits sideways)', onclick: () => send({ yaw: (yaw + 90) % 360 }) }, 'Rotate 90°'),
            h('button', { class: 'pill-btn', title: 'Turn the model around (when it faces backwards)', onclick: () => send({ yaw: (yaw + 180) % 360 }) }, 'Flip'),
            h('button', { class: 'pill-btn', title: 'No Sketchfab model for this car: show the procedural one', onclick: () => send({ rejected: true }) }, 'None'),
          ] : null,
          r.pick && Object.keys(r.pick).length ? h('button', { class: 'pill-btn ghost', title: 'Forget your choices for this car', onclick: () => send({ clear: true }) }, 'Automatic') : null),
        cur?.materials?.length ? h('div', { class: 'cm-paint' }, h('span', { class: 'cap' }, 'Body paint (card colour)'),
          h('div', { class: 'cm-chips' }, cur.materials.map((name) => {
            const on = cur.paint.includes(name);
            return h('button', {
              class: `cm-chip ${on ? 'on' : ''}`, title: on ? 'Painted in the card colour' : 'Paint this part in the card colour',
              onclick: () => send({ paint: on ? cur.paint.filter((x) => x !== name) : [...cur.paint, name] }),
            }, name);
          }))) : null,
        h('span', { class: 'cap' }, `Candidates${r.searched_at ? ` · searched ${fmt.date(new Date(r.searched_at * 1000).toISOString())}` : ''}`),
        h('div', { class: 'cm-list' }, r.candidates.map((cand) => h('div', { class: `cm-cand ${cand.uid === inUse ? 'on' : ''}` },
          cand.thumbnail ? h('img', { src: cand.thumbnail, alt: '', loading: 'lazy', draggable: 'false' }) : h('div', { class: 'cm-noimg' }, icon('car', 28)),
          h('div', { class: 'cm-info' },
            h('a', { href: cand.url, target: '_blank', rel: 'noopener', title: 'Open on Sketchfab' }, cand.name),
            h('span', { class: 'muted' }, `${cand.author} · ${(cand.license || '').replace('CC Attribution', 'CC BY')}`),
            h('span', { class: 'muted' }, `${cand.faces ? `${Math.round(cand.faces / 1000)}k faces · ` : ''}score ${Math.round(cand.score)}`)),
          cand.uid === inUse ? h('span', { class: 'cm-use' }, 'In use')
            : h('button', { class: 'pill-btn', onclick: () => send({ uid: cand.uid }) }, r.pick?.uid === cand.uid ? 'Queued' : 'Use')))));
    }
  }
  const stat = (k, v) => h('div', {}, h('span', { class: 'cap' }, k), h('b', {}, v));
  function close() {
    if (!insp) return;
    clearTimeout(insp.poll);
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
