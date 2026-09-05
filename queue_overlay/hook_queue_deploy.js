'use strict';

/* Emit one early queue event per deployment action. */

const LIB = 'libg.so';
const TICK = 0x8d97a0;
const RECORD_CONSTRUCTOR = 0xd309a8;
const RECORD_TTL_MS = 6000;
const DEDUPE_TTL_MS = 3000;

let holder = ptr(0);
const records = {};
const seen = {};

function readable(p) {
  try { return p && !p.isNull() && (p.readU8(), true); } catch (_) { return false; }
}

function p64(p, off) {
  try { return readable(p) ? p.add(off).readPointer() : ptr(0); } catch (_) { return ptr(0); }
}

function i32(p, off) {
  try { return readable(p) ? p.add(off).readS32() : null; } catch (_) { return null; }
}

function clock() {
  const ui = p64(holder, 0xa8);
  const value = readable(ui) ? ui.add(0x220).readFloat() : null;
  return value !== null && value >= 0 && value < 1000 ? value : null;
}

function tileCenter(value) {
  return Number.isInteger(value) ? Math.round((value - 500) / 1000) + 0.5 : null;
}

function snapshot(record) {
  const action = p64(record, 0x38);
  const x = i32(record, 0x28);
  const y = i32(record, 0x2c);
  return {
    action_ptr: action.toString(),
    card_id: i32(action, 0x40),
    target_raw: { x, y },
    target_tile_center: { x: tileCenter(x), y: tileCenter(y) },
  };
}

function emit(event) {
  send(JSON.stringify({ ...event, t_ms: Date.now() }));
}

setImmediate(function () {
  const module = Process.findModuleByName(LIB);
  if (!module) return emit({ event: 'error', error: 'libg_not_loaded' });

  Interceptor.attach(module.base.add(TICK), {
    onEnter(args) { if (readable(args[0])) holder = args[0]; },
  });

  Interceptor.attach(module.base.add(RECORD_CONSTRUCTOR), {
    onEnter(args) { this.record = args[0]; },
    onLeave() {
      if (!readable(this.record)) return;
      records[this.record.toString()] = { record: this.record, created_t_ms: Date.now() };
    },
  });

  setInterval(function () {
    const now = Date.now();
    for (const [key, rec] of Object.entries(records)) {
      if (now - rec.created_t_ms > RECORD_TTL_MS) {
        delete records[key];
        continue;
      }
      const state = snapshot(rec.record);
      if (state.action_ptr === '0x0' || state.card_id === null
          || state.target_raw.x === 0 && state.target_raw.y === 0) continue;
      const dedupeKey = state.action_ptr + ':' + state.target_raw.x + ':' + state.target_raw.y;
      if (!seen[dedupeKey] || now - seen[dedupeKey] > DEDUPE_TTL_MS) {
        seen[dedupeKey] = now;
        emit({ event: 'queue_deploy', battle_clock: clock(), ...state });
      }
      delete records[key];
    }
    for (const [key, t] of Object.entries(seen)) if (now - t > DEDUPE_TTL_MS) delete seen[key];
  }, 10);

  emit({ event: 'queue_deploy_ready', constructor: 'libg+0xd309a8', poll_interval_ms: 10 });
});

setInterval(function keepAlive() {}, 1000);
