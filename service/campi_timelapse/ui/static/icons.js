// Inline stroke icons (24x24, currentColor). Drawn for this app; no icon font or CDN, so it works offline.
const P = {
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  today: '<path d="M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z"/>',
  sparkle: '<path d="M11 3l1.9 5.1L18 10l-5.1 1.9L11 17l-1.9-5.1L4 10l5.1-1.9z"/><path d="M18.5 15l.8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8z"/>',
  car: '<path d="M5 13l1.6-4.2A2 2 0 0 1 8.5 7.5h7a2 2 0 0 1 1.9 1.3L19 13"/><rect x="3.5" y="13" width="17" height="4.5" rx="1.5"/><path d="M6.5 17.5v2M17.5 17.5v2M7 15.2h.01M17 15.2h.01"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.8"/><rect x="13" y="4" width="7" height="7" rx="1.8"/><rect x="4" y="13" width="7" height="7" rx="1.8"/><rect x="13" y="13" width="7" height="7" rx="1.8"/>',
  cards: '<rect x="8.5" y="3" width="11.5" height="16" rx="2" transform="rotate(12 14.25 11)"/><rect x="4" y="4.5" width="11.5" height="16" rx="2"/><path d="M9.75 9l.9 1.9 2 .3-1.5 1.4.4 2-1.8-1-1.8 1 .4-2-1.5-1.4 2-.3z"/>',
  film: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M7.5 5v14M16.5 5v14M3 9.5h4.5M3 14.5h4.5M16.5 9.5H21M16.5 14.5H21"/>',
  live: '<circle cx="12" cy="12" r="2"/><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7M5.6 5.6a9 9 0 0 0 0 12.8M18.4 5.6a9 9 0 0 1 0 12.8"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 4v5h-5"/>',
  camera: '<rect x="3" y="7" width="18" height="13" rx="3"/><circle cx="12" cy="13.5" r="3.5"/><path d="M8.5 7l1.4-2.5h4.2L15.5 7"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="7" y="5.5" width="3.6" height="13" rx="1" fill="currentColor" stroke="none"/><rect x="13.4" y="5.5" width="3.6" height="13" rx="1" fill="currentColor" stroke="none"/>',
  prev: '<path d="M7 6v12"/><path d="M18 6.5v11L10 12z"/>',
  next: '<path d="M17 6v12"/><path d="M6 6.5v11l8-5.5z"/>',
  folder: '<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  star: '<path d="M12 3.8l2.5 5.2 5.7.7-4.2 3.9 1.1 5.7L12 16.5l-5.1 2.8 1.1-5.7-4.2-3.9 5.7-.7z"/>',
  eyeOff: '<path d="M3.5 3.5l17 17"/><path d="M10.4 6.2A9.4 9.4 0 0 1 12 6c5 0 9 6 9 6a16 16 0 0 1-2.9 3.4M6.6 7.7C4.4 9.3 3 12 3 12s4 6 9 6a8.7 8.7 0 0 0 3.5-.7"/><path d="M9.9 10a3 3 0 0 0 4.1 4.1"/>',
  eye: '<path d="M3 12s4-6 9-6 9 6 9 6-4 6-9 6-9-6-9-6z"/><circle cx="12" cy="12" r="2.8"/>',
  x: '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
  chevronDown: '<path d="M6.5 9.5l5.5 5.5 5.5-5.5"/>',
  maximize: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  volume: '<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
  mute: '<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  wifi: '<path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.6 15.9a5 5 0 0 1 6.8 0"/><circle cx="12" cy="19" r="1" fill="currentColor"/>',
  chip: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3.5V7M14 3.5V7M10 17v3.5M14 17v3.5M3.5 10H7M3.5 14H7M17 10h3.5M17 14h3.5"/>',
  drive: '<path d="M5 13l2.3-6.2A1.5 1.5 0 0 1 8.7 6h6.6a1.5 1.5 0 0 1 1.4.8L19 13"/><rect x="3.5" y="13" width="17" height="6" rx="2"/><path d="M7.5 16h.01"/>',
  gamepad: '<path d="M7.5 8.5h9a4.5 4.5 0 0 1 4.5 4.5v.5a3 3 0 0 1-5.3 1.9l-1.2-1.4h-5l-1.2 1.4A3 3 0 0 1 3 13.5V13a4.5 4.5 0 0 1 4.5-4.5z"/><path d="M8 11v3M6.5 12.5h3"/><circle cx="16" cy="12" r=".7" fill="currentColor"/>',
  arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  gemini: '<path d="M12 3c.6 4.6 3.4 8.4 9 9-5.6.6-8.4 4.4-9 9-.6-4.6-3.4-8.4-9-9 5.6-.6 8.4-4.4 9-9z"/>',
  bars: '<path d="M5 20V11M10 20V5M15 20v-7M20 20V9"/>',
  pin: '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0C18.5 15.4 12 21 12 21z"/><circle cx="12" cy="10" r="2.4"/>',
  layers: '<path d="M12 4l8.5 4.5L12 13 3.5 8.5z"/><path d="M3.5 12.5L12 17l8.5-4.5M3.5 16.5L12 21l8.5-4.5"/>',
  calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/>',
  tag: '<path d="M3.5 12.2V4.5a1 1 0 0 1 1-1h7.7l8.3 8.3a1.5 1.5 0 0 1 0 2.1l-6.1 6.1a1.5 1.5 0 0 1-2.1 0z"/><circle cx="8" cy="8" r="1.4"/>',
};

export function icon(name, size = 20, cls = '') {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('width', size);
  s.setAttribute('height', size);
  s.setAttribute('fill', 'none');
  s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '1.7');
  s.setAttribute('stroke-linecap', 'round');
  s.setAttribute('stroke-linejoin', 'round');
  s.setAttribute('aria-hidden', 'true');
  s.setAttribute('class', `ic ${cls}`.trim());
  s.innerHTML = P[name] || '';
  return s;
}
