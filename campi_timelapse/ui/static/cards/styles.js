// Body styles and card "types" for the Cards tab. A label maps to a body style (the procedural car's shape) by model
// name, then by generic label, then by the YOLO class; the style picks the card's type (frame colour + energy icon).

const BY_MODEL = {
  pickup: ['F-150', 'Ranger', 'Maverick', 'Silverado 1500', 'Colorado', 'Sierra 1500', 'Canyon', '1500', 'Tacoma',
    'Tundra', 'Ridgeline', 'Frontier', 'Titan', 'Santa Cruz', 'Gladiator', 'R1T', 'Cybertruck'],
  hdpickup: ['F-250 Super Duty', 'Silverado 2500HD', 'Sierra 2500HD', '2500'],
  suv: ['RAV4', 'CR-V', 'HR-V', 'Escape', 'Bronco Sport', 'Equinox', 'Trax', 'Trailblazer', 'Blazer', 'Terrain',
    'Cherokee', 'Compass', 'Rogue', 'Kicks', 'Murano', 'Tucson', 'Kona', 'Venue', 'Ioniq 5', 'Seltos', 'Sportage',
    'Soul', 'Forester', 'Crosstrek', 'CX-30', 'CX-5', 'CX-50', 'Tiguan', 'ID.4', 'Model Y', 'X3', 'GLC', 'Q5', 'NX',
    'RDX', 'XT5', 'Lyriq', 'Nautilus', 'Envista', 'Encore GX', 'XC60', 'Macan', 'Outlander', 'Mustang Mach-E', 'EV6',
    'Passport', 'Grand Cherokee', 'Highlander', 'Santa Fe', 'Sorento', 'Outback', 'Acadia', 'Edge'],
  bigsuv: ['Grand Highlander', '4Runner', 'Sequoia', 'Pilot', 'Explorer', 'Expedition', 'Tahoe', 'Suburban', 'Traverse',
    'Yukon', 'Durango', 'Pathfinder', 'Armada', 'Palisade', 'Telluride', 'Ascent', 'CX-90', 'Atlas', 'Model X', 'X5',
    'X7', 'GLE', 'Q7', 'RX', 'GX', 'TX', 'MDX', 'Escalade', 'Navigator', 'Aviator', 'Enclave', 'QX60', 'XC90',
    'Cayenne', 'R1S'],
  offroad: ['Wrangler', 'Bronco', 'G-Class'],
  minivan: ['Sienna', 'Odyssey', 'Pacifica', 'Carnival'],
  van: ['Transit van', 'Express van', 'ProMaster van', 'Sprinter van'],
  hatch: ['Prius', 'Golf GTI', 'Mazda3', 'Impreza', 'Mirage', 'Versa', 'Integra'],
  coupe: ['Mustang', 'Camaro', 'Challenger', 'Corvette', '911', 'MX-5 Miata'],
};
const GENERIC = {
  'dump truck': 'boxtruck', 'concrete mixer truck': 'boxtruck', 'semi truck': 'boxtruck', 'box truck': 'boxtruck',
  'flatbed truck': 'boxtruck', 'garbage truck': 'boxtruck', 'tow truck': 'boxtruck', 'school bus': 'bus',
  'city bus': 'bus', excavator: 'boxtruck', 'wheel loader': 'boxtruck', 'skid steer loader': 'boxtruck',
  motorcycle: 'moto', 'pickup truck': 'pickup', suv: 'suv', van: 'van', sedan: 'sedan',
};
const EV = /tesla|ioniq|ev6|id\.4|mach-e|lyriq|rivian|cybertruck|model [3ysx]|leaf|bolt/i;
const LOOKUP = new Map(Object.entries(BY_MODEL).flatMap(([s, ms]) => ms.map((m) => [m.toLowerCase(), s])));

export function bodyStyle(item) {
  const model = (item.model || '').toLowerCase();
  if (model && LOOKUP.has(model)) return LOOKUP.get(model);
  const g = GENERIC[(item.label || '').toLowerCase()];
  if (g) return g;
  const l = (item.label || '').toLowerCase();
  for (const [k, s] of Object.entries(GENERIC)) if (l.includes(k)) return s;
  if (item.yolo_class === 'bus') return 'bus';
  if (item.yolo_class === 'truck') return 'pickup';
  return 'sedan';
}

// Card types, Pokémon-energy style: frame gradient (light -> deep), accent, and a glyph for the energy circle.
export const TYPES = {
  commuter: { name: 'Commuter', light: '#cfe4ff', deep: '#2f6fd6', accent: '#1d4ea3', glyph: 'drop' },
  family: { name: 'Family', light: '#d8f5c9', deep: '#3c9a3a', accent: '#24712a', glyph: 'leaf' },
  hauler: { name: 'Hauler', light: '#f8dcc0', deep: '#c0622b', accent: '#8d3f14', glyph: 'fist' },
  speed: { name: 'Speed', light: '#ffd3c4', deep: '#e0402c', accent: '#a5200f', glyph: 'flame' },
  city: { name: 'City', light: '#ead6ff', deep: '#9150d0', accent: '#64289e', glyph: 'eye' },
  cargo: { name: 'Cargo', light: '#eeeeea', deep: '#9a9d98', accent: '#5f625d', glyph: 'star' },
  trail: { name: 'Trail', light: '#f1e2bf', deep: '#a77b36', accent: '#6f4f1c', glyph: 'mountain' },
  volt: { name: 'Volt', light: '#fff4b8', deep: '#e8b90f', accent: '#9a7600', glyph: 'bolt' },
  heavy: { name: 'Heavy', light: '#dde5ec', deep: '#64798d', accent: '#3a4b5c', glyph: 'gear' },
};
const STYLE_TYPE = {
  sedan: 'commuter', hatch: 'city', coupe: 'speed', suv: 'family', bigsuv: 'family', offroad: 'trail',
  pickup: 'hauler', hdpickup: 'hauler', minivan: 'cargo', van: 'cargo', boxtruck: 'heavy', bus: 'heavy', moto: 'speed',
};
export const STYLE_NAMES = {
  sedan: 'Sedan', hatch: 'Hatchback', coupe: 'Coupe', suv: 'Crossover', bigsuv: 'SUV', offroad: 'Off-roader',
  pickup: 'Pickup', hdpickup: 'Heavy-duty pickup', minivan: 'Minivan', van: 'Van', boxtruck: 'Work truck', bus: 'Bus',
  moto: 'Motorcycle',
};

export function cardType(item, style = bodyStyle(item)) {
  if (EV.test(item.label || '')) return { key: 'volt', ...TYPES.volt };
  const k = STYLE_TYPE[style] || 'commuter';
  return { key: k, ...TYPES[k] };
}

// Finish -> rarity symbol + display names (the API's `finishes` carries the cut-offs and labels; these are the glyphs).
export const FINISH_ORDER = ['sir', 'fullart', 'holo', 'reverse', 'plain'];
export const FINISH_SHORT = { sir: 'SIR', fullart: 'Full art', holo: 'Holo', reverse: 'Reverse holo', plain: 'Plain' };
