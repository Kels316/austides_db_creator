// check-tides.mjs — inspect tide-db.js entries for a named bar
// Usage: node check-tides.mjs [bar name]
// Example: node check-tides.mjs "Noosa Bar"

import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const raw  = readFileSync(path.join(__dirname, 'tide-db.js'), 'utf8');
// Strip everything up to and including the assignment, then trailing semicolon
const start = raw.indexOf('window.TIDE_DB =');
const json = raw.slice(start).replace(/^window\.TIDE_DB\s*=\s*/, '').replace(/;\s*$/, '').trim();
const db   = JSON.parse(json);

const barName = process.argv[2] || 'Noosa Bar';
const stationId = db.barToStation[barName];
if (!stationId) {
  console.error(`Bar not found: "${barName}"`);
  console.log('Available bars:', Object.keys(db.barToStation).join(', '));
  process.exit(1);
}

const station  = db.stations[stationId];
const epochMs  = db.epoch_ms || 0;  // new format: minutes since epoch
const toMs     = epochMs ? (t) => epochMs + t * 60000 : (t) => t;

console.log(`\nBar       : ${barName}`);
console.log(`Station   : ${station.name}  (${stationId})`);
console.log(`Type      : ${station.type}${station.ref ? `  [ref: ${station.ref}]` : ''}`);
if (station.hw_factor) console.log(`Factors   : HW×${station.hw_factor}  LW×${station.lw_factor}`);
console.log(`DB covers : ${db.generated?.slice(0,10)} → ${db.valid_until?.slice(0,10)}`);
console.log(`Extremes  : ${station.extremes.length} total\n`);

// Print in AEST (UTC+10)
const TZ = 10 * 3600 * 1000;
const fmt = ms => {
  const d = new Date(ms + TZ);
  return d.toISOString().replace('T', ' ').slice(0, 16) + ' AEST';
};

// Show today's extremes
const now = Date.now();
const dayMs = 86400000;

const window48h = station.extremes
  .map(([t, h, w]) => ({ t: toMs(t), h, isHW: w === 1 }))
  .filter(e => e.t >= now - dayMs && e.t < now + 2 * dayMs);

console.log('Next 48 hrs extremes (AEST):');
window48h.slice(0, 12).forEach(e => {
  console.log(`  ${e.isHW ? 'HIGH' : 'LOW '} ${fmt(e.t)}  ${e.h.toFixed(3)} m`);
});

console.log('\nFirst 6 extremes in DB:');
station.extremes.slice(0, 6).forEach(([t, h, w]) => {
  console.log(`  ${w ? 'HIGH' : 'LOW '} ${fmt(toMs(t))}  ${h.toFixed(3)} m`);
});
