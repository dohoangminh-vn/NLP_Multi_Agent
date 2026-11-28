/* Giao diện giáo viên – Hệ thống đa tác tử soạn giáo án Tin học 10.
 *
 * Vanilla JS, không cần build. Hợp đồng API/SSE: README.md, mục "Hợp đồng API & SSE".
 * Nguyên tắc an toàn: mọi dữ liệu từ máy chủ đều được đưa vào DOM bằng
 * textContent / createTextNode (không dùng innerHTML với dữ liệu).
 */
'use strict';

/* ------------------------------------------------------------------ *
 * Hằng số & dữ liệu dự phòng
 * ------------------------------------------------------------------ */

const API = {
  health: '/api/health',
  meta: '/api/meta',
  start: '/api/lesson/start',
  resume: '/api/lesson/resume',
  demo: '/api/demo/stream?speed=1',
  exportDocx: (threadId) => '/api/lesson/' + encodeURIComponent(threadId) + '/export',
};

const FALLBACK_LESSONS = [
  { bai: 16, title: 'Ngôn ngữ lập trình bậc cao và Python', sgk_pages: [86, 90] },
  { bai: 17, title: 'Biến và lệnh gán', sgk_pages: [91, 96] },
  { bai: 18, title: 'Các lệnh vào ra đơn giản', sgk_pages: [97, 100] },
  { bai: 19, title: 'Câu lệnh rẽ nhánh if', sgk_pages: [101, 104] },
  { bai: 20, title: 'Câu lệnh lặp for', sgk_pages: [105, 107] },
  { bai: 21, title: 'Câu lệnh lặp while', sgk_pages: [108, 110] },
  { bai: 22, title: 'Kiểu dữ liệu danh sách', sgk_pages: [111, 114] },
  { bai: 23, title: 'Một số lệnh làm việc với dữ liệu danh sách', sgk_pages: [115, 118] },
  { bai: 24, title: 'Xâu kí tự', sgk_pages: [119, 122] },
  { bai: 25, title: 'Một số lệnh làm việc với xâu kí tự', sgk_pages: [123, 126] },
  { bai: 26, title: 'Hàm trong Python', sgk_pages: [127, 130] },
  { bai: 27, title: 'Tham số của hàm', sgk_pages: [131, 135] },
  { bai: 28, title: 'Phạm vi của biến', sgk_pages: [136, 140] },
  { bai: 29, title: 'Nhận biết lỗi chương trình', sgk_pages: [141, 144] },
];

const FALLBACK_SCOPE_MODES = [
  { value: '45m_theory', label: '45p Lý thuyết', minutes: 45 },
  { value: '45m_practice', label: '45p Thực hành', minutes: 45 },
  { value: '90m_full', label: '90p Trọn bài', minutes: 90 },
];

const DEFAULT_LESSON = 20;
const RATIO_KEYS = ['basic', 'standard', 'advanced'];
const TIERS = ['Cơ bản', 'Đạt chuẩn', 'Nâng cao'];
const DEMO_MIN_GAP_MS = 450; // nhịp tối thiểu giữa các sự kiện demo bị dồn sau khi tạm dừng HITL

const NODE_META = {
  retriever: { title: 'Truy xuất SGK (Hybrid RAG)', short: 'RAG' },
  profiler: { title: 'Phân tích hồ sơ lớp học', short: 'HS' },
  outline_planner: { title: 'Lập khung hoạt động', short: 'KH' },
  human_review: { title: 'Giáo viên duyệt khung (HITL)', short: 'GV' },
  detail_designer: { title: 'Thiết kế chi tiết & Phiếu học tập', short: 'TK' },
  critic: { title: 'Kiểm định AST + Sandbox + Critic', short: 'KĐ' },
  export: { title: 'Đóng gói giáo án', short: 'XU' },
};

const SCORE_LABELS = [
  ['faithfulness', 'Độ trung thành nguồn (Faithfulness)'],
  ['citation_precision', 'Độ chính xác trích dẫn'],
  ['yccd_coverage', 'Độ phủ YCCĐ'],
  ['differentiation', 'Mức phân hoá 3 phiếu'],
];

const STEP_LABELS = [
  ['step1_transfer', 'Bước 1. Chuyển giao nhiệm vụ'],
  ['step2_execute', 'Bước 2. Thực hiện nhiệm vụ'],
  ['step3_report', 'Bước 3. Báo cáo, thảo luận'],
  ['step4_conclude', 'Bước 4. Kết luận, nhận định'],
];

/* ------------------------------------------------------------------ *
 * Tiện ích thuần (không phụ thuộc DOM – có thể kiểm thử bằng Node)
 * ------------------------------------------------------------------ */

/**
 * Bộ phân tích SSE tăng dần. Gọi push(text) với từng đoạn văn bản đã giải mã
 * (có thể cắt ngang giữa dòng / giữa sự kiện), end() khi luồng đóng.
 * onEvent nhận {event, data} với data là chuỗi thô (các dòng data: nối bằng \n).
 */
function createSSEParser(onEvent) {
  let buffer = '';
  let eventType = '';
  let dataLines = [];
  let sawFirstChunk = false;

  function dispatch() {
    if (dataLines.length === 0) {
      eventType = '';
      return;
    }
    const ev = { event: eventType || 'message', data: dataLines.join('\n') };
    eventType = '';
    dataLines = [];
    onEvent(ev);
  }

  function processLine(line) {
    if (line === '') {
      dispatch();
      return;
    }
    if (line.charAt(0) === ':') return; // dòng chú thích / keep-alive
    const idx = line.indexOf(':');
    let field;
    let value;
    if (idx === -1) {
      field = line;
      value = '';
    } else {
      field = line.slice(0, idx);
      value = line.slice(idx + 1);
      if (value.charAt(0) === ' ') value = value.slice(1);
    }
    if (field === 'event') eventType = value;
    else if (field === 'data') dataLines.push(value);
    // "id", "retry" và trường lạ: bỏ qua.
  }

  return {
    push(text) {
      if (!text) return;
      if (!sawFirstChunk) {
        sawFirstChunk = true;
        if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
      }
      buffer += text;
      let start = 0;
      for (let i = 0; i < buffer.length; i++) {
        const c = buffer.charAt(i);
        if (c !== '\n' && c !== '\r') continue;
        // "\r" ở cuối bộ đệm: chưa biết có "\n" theo sau không → chờ đoạn kế tiếp.
        if (c === '\r' && i + 1 >= buffer.length) break;
        processLine(buffer.slice(start, i));
        if (c === '\r' && buffer.charAt(i + 1) === '\n') i++;
        start = i + 1;
      }
      buffer = buffer.slice(start);
    },
    end() {
      if (buffer.length) {
        processLine(buffer.replace(/\r$/, ''));
        buffer = '';
      }
      dispatch(); // khoan dung: phát nốt sự kiện cuối nếu máy chủ không gửi dòng trống kết thúc
    },
  };
}

/**
 * Đọc một Response SSE (fetch) đến hết, gọi `handler(ev)` tuần tự cho từng
 * sự kiện. handler có thể trả về Promise – việc đọc tiếp sẽ chờ handler xong
 * (dùng để "tạm dừng" luồng tại điểm HITL).
 */
async function consumeSSE(response, handler) {
  if (!response.body || typeof response.body.getReader !== 'function') {
    throw new Error('Trình duyệt không hỗ trợ đọc luồng dữ liệu (ReadableStream).');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  const queue = [];
  const parser = createSSEParser((ev) => queue.push(ev));
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
      while (queue.length) await handler(queue.shift());
    }
    parser.push(decoder.decode());
    parser.end();
    while (queue.length) await handler(queue.shift());
  } finally {
    try { reader.releaseLock(); } catch (_) { /* đã giải phóng */ }
  }
}

/** Đặt `key` = value, hai khoá còn lại chia phần còn lại theo tỉ lệ hiện có; tổng luôn = 100. */
function rebalanceRatios(ratios, key, value) {
  const v = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
  const others = RATIO_KEYS.filter((k) => k !== key);
  const a = others[0];
  const b = others[1];
  const remaining = 100 - v;
  const sumOthers = (ratios[a] || 0) + (ratios[b] || 0);
  let na;
  if (sumOthers <= 0) na = Math.floor(remaining / 2);
  else na = Math.round((remaining * (ratios[a] || 0)) / sumOthers);
  na = Math.max(0, Math.min(remaining, na));
  const out = {};
  out[key] = v;
  out[a] = na;
  out[b] = remaining - na;
  return out;
}

function sumRatios(r) {
  return RATIO_KEYS.reduce((s, k) => s + (Number(r[k]) || 0), 0);
}

function parseJSONSafe(text) {
  if (text == null || text === '') return {};
  try {
    return JSON.parse(text);
  } catch (_) {
    return { _raw: text };
  }
}

function asArray(x) {
  return Array.isArray(x) ? x : [];
}

/** Mảng các object (bỏ phần tử null / không phải object). */
function objs(x) {
  return asArray(x).filter((o) => o && typeof o === 'object');
}

function normalizeTier(s) {
  return String(s || '').normalize('NFC').trim().toLowerCase();
}

function formatPct(x) {
  const n = Number(x);
  if (x == null || !Number.isFinite(n)) return '—';
  return Math.round(n * 100) + '%';
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function makeAbortError() {
  const err = new Error('Đã dừng phiên.');
  err.name = 'AbortError';
  return err;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { createSSEParser, consumeSSE, rebalanceRatios, sumRatios, parseJSONSafe };
}

/* ------------------------------------------------------------------ *
 * Tiện ích DOM
 * ------------------------------------------------------------------ */

const $ = (id) => document.getElementById(id);

/** Tạo phần tử. Chuỗi con → text node (an toàn XSS). */
function h(tag, props) {
  const node = document.createElement(tag);
  if (props) {
    Object.keys(props).forEach((k) => {
      const v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = String(v);
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    });
  }
  for (let i = 2; i < arguments.length; i++) appendChild(node, arguments[i]);
  return node;
}

function appendChild(node, child) {
  if (child == null || child === false) return;
  if (Array.isArray(child)) {
    child.forEach((c) => appendChild(node, c));
    return;
  }
  node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** Văn bản có đoạn `code` trong dấu backtick → các node text + <code>, vẫn an toàn. */
function richText(str) {
  const parts = String(str == null ? '' : str).split(/`([^`]+)`/);
  return parts.map((p, i) => (i % 2 === 1 ? h('code', { class: 'inline-code' }, p) : p));
}

function textOrDash(s) {
  return s == null || String(s).trim() === '' ? '—' : String(s);
}

function badge(text, kind, title) {
  return h('span', { class: 'badge badge--' + (kind || 'muted'), title: title || null }, text);
}

/* ------------------------------------------------------------------ *
 * Trạng thái ứng dụng
 * ------------------------------------------------------------------ */

const state = {
  lessons: FALLBACK_LESSONS.slice(),
  scopeModes: FALLBACK_SCOPE_MODES.slice(),
  ratios: { basic: 30, standard: 50, advanced: 20 },
  busy: false,
  run: null, // phiên hiện tại (xem newRun)
};

function newRun(mode) {
  return {
    id: Date.now() + Math.random(),
    mode, // 'live' | 'demo'
    threadId: null,
    lessonBai: null,
    targetMinutes: 45,
    request: null,
    abort: new AbortController(),
    cancelled: false,
    failed: false,
    final: null,
    approvedOutline: null, // outline GV đã duyệt nhưng chưa gửi /resume
    hitlConfirmedAt: 0,
    paceMs: 0,
    lastDispatch: 0,
    cards: [],
    criticRounds: [],
    hitl: null,
  };
}

/* ------------------------------------------------------------------ *
 * Thông báo
 * ------------------------------------------------------------------ */

function showAlert(kind, title, message) {
  const box = h('div', { class: 'alert alert--' + kind, role: kind === 'error' ? 'alert' : 'status' },
    h('div', { class: 'alert__body' },
      h('strong', { class: 'alert__title' }, title),
      message ? h('span', { class: 'alert__msg' }, ' ' + message) : null),
    h('button', {
      type: 'button', class: 'alert__close', 'aria-label': 'Đóng thông báo',
      onclick: () => box.remove(),
    }, '×'));
  $('alerts').appendChild(box);
  box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  return box;
}

function clearAlerts() {
  clear($('alerts'));
}

/* ------------------------------------------------------------------ *
 * Khởi tạo: health, meta, form cấu hình
 * ------------------------------------------------------------------ */

async function fetchJSON(url, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs || 5000);
  try {
    const resp = await fetch(url, { signal: ctrl.signal, headers: { Accept: 'application/json' } });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.json();
  } finally {
    clearTimeout(timer);
  }
}

async function loadHealth() {
  const chip = $('health-chip');
  try {
    const data = await fetchJSON(API.health, 4000);
    chip.className = 'chip chip--ok';
    chip.textContent = 'Máy chủ sẵn sàng · LLM: ' + textOrDash(data && data.llm);
  } catch (_) {
    chip.className = 'chip chip--warn';
    chip.textContent = 'Không kết nối được máy chủ';
  }
}

async function loadMeta() {
  const note = $('meta-note');
  try {
    const data = await fetchJSON(API.meta, 5000);
    const lessons = asArray(data && data.lessons).filter((l) => l && Number.isInteger(Number(l.bai)));
    const modes = asArray(data && data.scope_modes).filter((m) => m && m.value);
    if (!lessons.length) throw new Error('Danh sách bài trống');
    state.lessons = lessons.map((l) => ({ bai: Number(l.bai), title: l.title || '', sgk_pages: l.sgk_pages }));
    if (modes.length) {
      state.scopeModes = modes.map((m) => ({
        value: String(m.value),
        label: m.label || String(m.value),
        minutes: Number(m.minutes) || (String(m.value).indexOf('90') === 0 ? 90 : 45),
      }));
    }
    note.hidden = true;
  } catch (_) {
    state.lessons = FALLBACK_LESSONS.slice();
    state.scopeModes = FALLBACK_SCOPE_MODES.slice();
    note.textContent = 'Không tải được danh sách từ máy chủ (/api/meta) – đang dùng danh sách mặc định Bài 16–29.';
    note.hidden = false;
  }
  renderLessonOptions();
  renderScopeOptions();
}

function lessonLabel(l) {
  let s = 'Bài ' + l.bai + (l.title ? ' – ' + l.title : '');
  const p = l.sgk_pages;
  if (Array.isArray(p) && p.length === 2) s += ' (tr. ' + p[0] + '–' + p[1] + ')';
  return s;
}

function findLesson(bai) {
  return state.lessons.find((l) => Number(l.bai) === Number(bai)) || null;
}

function renderLessonOptions() {
  const sel = $('lesson-select');
  const prev = Number(sel.value) || DEFAULT_LESSON;
  clear(sel);
  state.lessons
    .filter((l) => l.bai >= 16 && l.bai <= 29)
    .forEach((l) => {
      const opt = document.createElement('option');
      opt.value = String(l.bai);
      opt.textContent = lessonLabel(l);
      sel.appendChild(opt);
    });
  if (findLesson(prev)) sel.value = String(prev);
  updateStartButton();
}

function renderScopeOptions() {
  const sel = $('scope-select');
  const prev = sel.value || '45m_theory';
  clear(sel);
  state.scopeModes.forEach((m) => {
    const opt = document.createElement('option');
    opt.value = m.value;
    opt.textContent = m.label + ' (' + m.minutes + ' phút)';
    sel.appendChild(opt);
  });
  if (state.scopeModes.some((m) => m.value === prev)) sel.value = prev;
  updateTargetMinutes();
}

function currentScope() {
  const v = $('scope-select').value;
  return state.scopeModes.find((m) => m.value === v) || state.scopeModes[0] || FALLBACK_SCOPE_MODES[0];
}

function updateTargetMinutes() {
  $('target-minutes-label').textContent = currentScope().minutes + ' phút';
}

function renderRatios() {
  RATIO_KEYS.forEach((k) => {
    const v = state.ratios[k];
    $('ratio-' + k).value = String(v);
    $('ratio-' + k + '-out').textContent = v + '%';
    $('ratio-bar-' + k).style.width = v + '%';
  });
  const total = sumRatios(state.ratios);
  $('ratio-total').textContent = total + '%';
  const err = $('ratio-error');
  if (total !== 100) {
    err.textContent = 'Tổng tỉ lệ ba nhóm phải bằng 100% (hiện tại ' + total + '%).';
    err.hidden = false;
  } else {
    err.hidden = true;
  }
  updateStartButton();
}

function validateConfig() {
  const bai = Number($('lesson-select').value);
  if (!Number.isInteger(bai) || bai < 16 || bai > 29) return 'Vui lòng chọn một bài từ 16 đến 29.';
  const total = sumRatios(state.ratios);
  if (total !== 100) return 'Không thể bắt đầu: tổng tỉ lệ phân hoá đang là ' + total + '%, cần đúng 100%.';
  return null;
}

function updateStartButton() {
  const problem = validateConfig();
  const startBtn = $('start-btn');
  const errEl = $('start-error');
  startBtn.disabled = state.busy || !!problem;
  if (problem && !state.busy) {
    errEl.textContent = problem;
    errEl.hidden = false;
  } else {
    errEl.hidden = true;
  }
  $('demo-btn').disabled = state.busy;
  $('cancel-btn').hidden = !state.busy;
}

function buildLessonRequest() {
  const bai = Number($('lesson-select').value);
  const lesson = findLesson(bai);
  const scope = currentScope();
  const r = state.ratios;
  return {
    target_bai: bai,
    topic_name: lesson ? lesson.title : '',
    scope_mode: scope.value,
    target_minutes: scope.minutes === 90 ? 90 : 45,
    basic_ratio: r.basic / 100,
    standard_ratio: r.standard / 100,
    advanced_ratio: r.advanced / 100,
    lab_constraints: $('lab-constraints').value.trim(),
    bypass_hitl: $('bypass-hitl').checked,
  };
}

function setBusy(busy) {
  state.busy = busy;
  const form = $('config-form');
  Array.prototype.forEach.call(form.querySelectorAll('select, input, textarea'), (el) => {
    el.disabled = busy;
  });
  updateStartButton();
}

/* ------------------------------------------------------------------ *
 * Live Execution Trace
 * ------------------------------------------------------------------ */

const STATUS_TEXT = {
  running: 'Đang chạy',
  done: 'Hoàn tất',
  waiting: 'Chờ giáo viên duyệt',
  red: 'KHÔNG ĐẠT',
  green: 'ĐẠT',
  error: 'Lỗi',
  stopped: 'Đã dừng',
};

const STATUS_PILL = {
  running: 'pill--running',
  done: 'pill--done',
  waiting: 'pill--waiting',
  red: 'pill--red',
  green: 'pill--green',
  error: 'pill--red',
  stopped: 'pill--muted',
};

function setRunStatus(status, text) {
  const el = $('run-status');
  el.className = 'pill ' + (STATUS_PILL[status] || 'pill--muted');
  el.textContent = text || STATUS_TEXT[status] || status;
}

function createCard(run, node, label) {
  const meta = NODE_META[node] || { title: node || 'Bước', short: '•' };
  const statusEl = h('span', { class: 'pill' });
  const bodyEl = h('div', { class: 'trace-card__body' });
  const labelEl = h('span', { class: 'trace-card__label' }, label && label !== meta.title ? label : '');
  const el = h('li', { class: 'trace-card', 'data-node': node },
    h('div', { class: 'trace-card__marker', 'aria-hidden': 'true' }, meta.short),
    h('div', { class: 'trace-card__main' },
      h('div', { class: 'trace-card__head' },
        h('div', { class: 'trace-card__titles' },
          h('span', { class: 'trace-card__title' }, meta.title),
          labelEl),
        statusEl),
      bodyEl));
  $('trace-list').appendChild(el);
  const card = { node, el, statusEl, bodyEl, labelEl, status: null, closed: false, verdict: null, hitlRound: false, startSeen: false, endSeen: false };
  run.cards.push(card);
  setCardStatus(card, 'running');
  el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  return card;
}

function setCardStatus(card, status) {
  card.status = status;
  card.el.classList.remove('is-running', 'is-done', 'is-waiting', 'is-red', 'is-green', 'is-error', 'is-stopped');
  card.el.classList.add('is-' + status);
  card.statusEl.className = 'pill ' + (STATUS_PILL[status] || 'pill--muted');
  clear(card.statusEl);
  if (status === 'running') card.statusEl.appendChild(h('span', { class: 'spinner', 'aria-hidden': 'true' }));
  card.statusEl.appendChild(document.createTextNode(STATUS_TEXT[status] || status));
}

function setCardLabel(card, label) {
  const meta = NODE_META[card.node];
  card.labelEl.textContent = label && (!meta || label !== meta.title) ? label : '';
}

function findOpenCard(run, node) {
  for (let i = run.cards.length - 1; i >= 0; i--) {
    const c = run.cards[i];
    if (c.node === node && !c.closed) return c;
  }
  return null;
}

function lastCard(run, node) {
  for (let i = run.cards.length - 1; i >= 0; i--) if (run.cards[i].node === node) return run.cards[i];
  return null;
}

function onNodeStart(run, data) {
  const node = data.node || 'unknown';
  if (node === 'human_review') {
    // Thẻ HITL do UI tự tạo khi nhận hitl_required: máy chủ có thể gửi node_start sau đó → dùng lại.
    const c = lastCard(run, 'human_review');
    if (c && c.hitlRound && !c.startSeen) {
      c.startSeen = true;
      setCardLabel(c, data.label);
      return;
    }
  }
  const open = findOpenCard(run, node);
  // Bỏ qua node_start lặp cho cùng một thẻ vẫn đang chạy / chờ (chưa có kết luận).
  if (open && (open.status === 'running' || open.status === 'waiting') && !open.verdict) {
    open.startSeen = true;
    setCardLabel(open, data.label);
    return;
  }
  if (open) open.closed = true;
  const card = createCard(run, node, data.label);
  card.startSeen = true;
}

function onNodeEnd(run, data) {
  const node = data.node || 'unknown';
  const summary = data.summary && typeof data.summary === 'object' ? data.summary : {};
  let card = findOpenCard(run, node);
  if (!card && node === 'human_review') {
    const c = lastCard(run, 'human_review');
    if (c && c.hitlRound && !c.endSeen) card = c; // không nhân đôi thẻ HITL
  }
  if (!card) card = createCard(run, node, data.label);
  if (data.label) setCardLabel(card, data.label);
  card.endSeen = true;

  if (card.hitlRound) {
    // Trạng thái thẻ HITL do thao tác của GV quyết định; chỉ cập nhật nội dung nếu có khung mới.
    if (summary.outline) {
      clear(card.bodyEl);
      appendChild(card.bodyEl, renderOutlineSummary(summary.outline));
    }
    if (card.status !== 'waiting') card.closed = true;
    return;
  }
  card.closed = true;

  if (node === 'critic') {
    if (!card.verdict && summary.status) renderCriticVerdict(run, card, summary);
    if (!card.verdict) setCardStatus(card, 'done');
    return;
  }
  clear(card.bodyEl);
  appendChild(card.bodyEl, renderNodeSummary(run, node, summary));
  setCardStatus(card, 'done');
}

function onCritic(run, data) {
  let card = findOpenCard(run, 'critic');
  if (card && card.verdict) {
    card.closed = true;
    card = null;
  }
  if (!card) card = createCard(run, 'critic', data.label || null);
  renderCriticVerdict(run, card, data);
}

/* ---------- Tóm tắt từng node ---------- */

function kv(label, value) {
  return h('div', { class: 'kv' }, h('span', { class: 'kv__k' }, label), h('span', { class: 'kv__v' }, value));
}

function renderNodeSummary(run, node, s) {
  switch (node) {
    case 'retriever': return renderRetrieverSummary(s);
    case 'profiler': return renderProfilerSummary(run, s);
    case 'outline_planner': return s.outline ? renderOutlineSummary(s.outline) : renderGenericSummary(s);
    case 'detail_designer': return renderDesignerSummary(s);
    case 'export': return h('p', { class: 'trace-text' }, s.ready ? 'Đã sẵn sàng xuất file Word (.docx) chuẩn CV 5512.' : 'Đang chuẩn bị file xuất.');
    case 'human_review': return s.outline ? renderOutlineSummary(s.outline) : renderGenericSummary(s);
    default: return renderGenericSummary(s);
  }
}

function citationShort(c) {
  return 'Bài ' + textOrDash(c.bai_so) + ' · tr.' + textOrDash(c.page_number) + (c.section_title ? ' · ' + c.section_title : '');
}

function renderRetrieverSummary(s) {
  const chunks = objs(s.chunks);
  const n = s.n_chunks != null ? s.n_chunks : chunks.length;
  return [
    h('p', { class: 'trace-text' },
      'Tìm thấy ', h('strong', null, String(n)), ' đoạn trích SGK',
      s.rewrites != null ? ' · ' + s.rewrites + ' lần viết lại truy vấn' : ''),
    chunks.length ? h('div', { class: 'chip-list' },
      chunks.map((c) => h('span', { class: 'chip chip--source', title: textOrDash(c.quoted_text) }, citationShort(c)))) : null,
  ];
}

function renderProfilerSummary(run, s) {
  const p = s.profile && typeof s.profile === 'object' ? s.profile : {};
  if (p.target_bai) run.lessonBai = Number(p.target_bai) || run.lessonBai;
  if (p.target_minutes) run.targetMinutes = Number(p.target_minutes) || run.targetMinutes;
  const tiers = objs(p.tiers);
  return [
    h('div', { class: 'kv-grid' },
      p.target_bai != null ? kv('Bài', String(p.target_bai)) : null,
      p.target_minutes != null ? kv('Thời lượng', p.target_minutes + ' phút') : null,
      p.emphasis ? kv('Định hướng', p.emphasis) : null),
    tiers.length ? h('div', { class: 'chip-list' },
      tiers.map((t) => h('span', { class: 'chip chip--tier' },
        textOrDash(t.tier) + (t.ratio != null ? ' ' + formatPct(t.ratio) : '') +
        (asArray(t.bloom).length ? ' · ' + asArray(t.bloom).join(', ') : '')))) : null,
    asArray(p.lab_notes).length ? h('ul', { class: 'plain-list' }, asArray(p.lab_notes).map((n) => h('li', null, String(n)))) : null,
  ];
}

function renderOutlineSummary(o) {
  const titles = asArray(o.activity_titles);
  const mins = asArray(o.activity_minutes);
  const total = mins.reduce((a, b) => a + (Number(b) || 0), 0);
  return [
    o.objective_summary ? h('p', { class: 'trace-text' }, o.objective_summary) : null,
    titles.length ? h('ol', { class: 'outline-list' },
      titles.map((t, i) => h('li', null, h('span', null, String(t)), h('span', { class: 'outline-list__min' }, textOrDash(mins[i]) + ' phút')))) : null,
    titles.length ? h('p', { class: 'trace-text muted' }, 'Tổng: ' + total + ' phút') : null,
  ];
}

function renderDesignerSummary(s) {
  return h('div', { class: 'trace-row' },
    s.revision ? badge('Revision Mode', 'warn', 'Designer sửa bản nháp theo phản hồi của Critic') : badge('Bản nháp đầu', 'info'),
    h('span', { class: 'trace-text' },
      'Vòng ' + textOrDash(s.iteration) + ' · ' + textOrDash(s.n_activities) + ' hoạt động · ' + textOrDash(s.n_worksheets) + ' phiếu học tập'));
}

function renderGenericSummary(s) {
  const keys = Object.keys(s || {}).filter((k) => s[k] == null || typeof s[k] !== 'object');
  if (!keys.length) return null;
  return h('div', { class: 'kv-grid' }, keys.map((k) => kv(k, textOrDash(s[k]))));
}

/* ---------- Thẻ Critic đỏ / xanh ---------- */

function issueList(title, items, kind) {
  const arr = asArray(items);
  if (!arr.length) return null;
  return h('div', { class: 'issues issues--' + kind },
    h('div', { class: 'issues__title' }, title + ' (' + arr.length + ')'),
    h('ul', { class: 'issues__list' }, arr.map((x) => h('li', null, richText(x)))));
}

function scoreGrid(scores, targetMinutes) {
  const sc = scores && typeof scores === 'object' ? scores : {};
  const rows = SCORE_LABELS.map(([k, label]) => {
    const v = Number(sc[k]);
    const ok = Number.isFinite(v);
    const width = ok ? Math.max(0, Math.min(100, v * 100)) : 0;
    return h('div', { class: 'score' },
      h('div', { class: 'score__top' }, h('span', { class: 'score__label' }, label), h('span', { class: 'score__val' }, formatPct(sc[k]))),
      h('div', { class: 'meter' }, h('span', { class: 'meter__fill', style: 'width:' + width + '%' })));
  });
  if (sc.total_minutes != null) {
    const tm = Number(sc.total_minutes);
    const ok = !targetMinutes || tm === Number(targetMinutes);
    rows.push(h('div', { class: 'score' },
      h('div', { class: 'score__top' },
        h('span', { class: 'score__label' }, 'Tổng thời lượng'),
        h('span', { class: 'score__val ' + (ok ? 'text-ok' : 'text-bad') },
          tm + (targetMinutes ? ' / ' + targetMinutes : '') + ' phút'))));
  }
  return h('div', { class: 'scores' }, rows);
}

function renderCriticVerdict(run, card, d) {
  const green = String(d.status || '').toLowerCase() === 'green';
  const iteration = d.iteration != null ? d.iteration : run.criticRounds.length + 1;
  card.verdict = green ? 'green' : 'red';
  setCardStatus(card, card.verdict);
  card.statusEl.textContent = 'Vòng ' + iteration + ' · ' + (green ? 'ĐẠT' : 'KHÔNG ĐẠT');

  const prevRed = run.criticRounds.some((r) => r.status === 'red');
  run.criticRounds.push({ iteration, status: card.verdict });
  renderCriticProgress(run);

  const checks = h('div', { class: 'trace-row' },
    badge(d.ast_pass ? 'AST PASS' : 'AST FAIL', d.ast_pass ? 'ok' : 'bad'),
    badge(d.sandbox_pass ? 'Sandbox PASS' : 'Sandbox FAIL', d.sandbox_pass ? 'ok' : 'bad'));

  clear(card.bodyEl);
  if (green) {
    appendChild(card.bodyEl, [
      h('p', { class: 'verdict verdict--green' },
        prevRed
          ? 'Designer đã tự sửa thành công – mọi kiểm định đều đạt ở vòng ' + iteration + '.'
          : 'Mọi kiểm định đều đạt ngay ở vòng ' + iteration + '.'),
      checks,
      issueList('Lưu ý', d.warnings, 'warn'),
      scoreGrid(d.scores, run.targetMinutes),
    ]);
  } else {
    appendChild(card.bodyEl, [
      h('p', { class: 'verdict verdict--red' }, 'Vòng ' + iteration + ': phát hiện lỗi – chuyển Designer sang Revision Mode để tự sửa.'),
      checks,
      issueList('Cổng kiểm định không đạt', d.gate_failures, 'bad'),
      issueList('AST Guard – vượt phạm vi chương trình', d.ast_violations, 'bad'),
      issueList('Sandbox – test case lỗi', d.sandbox_failures, 'bad'),
      issueList('Lưu ý', d.warnings, 'warn'),
      scoreGrid(d.scores, run.targetMinutes),
    ]);
  }
}

function renderCriticProgress(run) {
  const wrap = $('critic-progress');
  const items = $('critic-progress-items');
  clear(items);
  run.criticRounds.forEach((r, i) => {
    if (i > 0) items.appendChild(h('span', { class: 'progress-strip__arrow', 'aria-hidden': 'true' }, '→'));
    items.appendChild(h('span', { class: 'round round--' + r.status },
      'Vòng ' + r.iteration + ' · ' + (r.status === 'green' ? 'Xanh' : 'Đỏ')));
  });
  wrap.hidden = run.criticRounds.length === 0;
}

function closeRunningCards(run, status) {
  run.cards.forEach((c) => {
    if (c.status === 'running' || c.status === 'waiting') {
      setCardStatus(c, status);
      c.closed = true;
    }
  });
}

/* ------------------------------------------------------------------ *
 * HITL – duyệt khung (UC-03)
 * ------------------------------------------------------------------ */

function onHitlRequired(run, data) {
  if (data.thread_id) run.threadId = data.thread_id;
  if (data.target_minutes) run.targetMinutes = Number(data.target_minutes) || run.targetMinutes;
  if (data.profile && data.profile.target_bai) run.lessonBai = Number(data.profile.target_bai) || run.lessonBai;
  updateSessionInfo(run);

  let card = findOpenCard(run, 'human_review');
  if (!card) card = createCard(run, 'human_review', null);
  card.hitlRound = true;
  setCardStatus(card, 'waiting');
  clear(card.bodyEl);
  card.bodyEl.appendChild(h('p', { class: 'trace-text' }, 'Hệ thống tạm dừng. Thầy/cô duyệt khung hoạt động ở khung bên dưới.'));
  setRunStatus('waiting');

  const outline = data.outline && typeof data.outline === 'object' ? data.outline : {};
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      hitlFinish(run, false);
      reject(makeAbortError());
    };
    if (run.abort.signal.aborted) {
      onAbort();
      return;
    }
    run.abort.signal.addEventListener('abort', onAbort, { once: true });
    run.hitl = {
      original: outline,
      target: run.targetMinutes,
      card,
      confirm: (edited) => {
        run.abort.signal.removeEventListener('abort', onAbort);
        run.approvedOutline = edited;
        run.hitlConfirmedAt = Date.now();
        card.closed = true;
        clear(card.bodyEl);
        appendChild(card.bodyEl, renderOutlineSummary(edited));
        setCardStatus(card, 'done');
        card.statusEl.textContent = 'Đã duyệt';
        setRunStatus('running');
        hitlFinish(run, true);
        // Demo: luồng vẫn mở → các sự kiện sau đã bị dồn khi chờ, phát lại có nhịp.
        if (run.mode === 'demo') run.paceMs = DEMO_MIN_GAP_MS;
        resolve();
      },
    };
    renderHitlForm(run, outline);
  });
}

function renderHitlForm(run, outline) {
  const section = $('hitl-section');
  section.hidden = false;
  section.classList.remove('is-confirmed');
  $('hitl-state').className = 'pill pill--waiting';
  $('hitl-state').textContent = 'Chờ duyệt';
  $('hitl-demo-note').hidden = run.mode !== 'demo';
  $('hitl-objective').value = outline.objective_summary || '';
  $('hitl-objective').disabled = false;

  const titles = asArray(outline.activity_titles);
  const mins = asArray(outline.activity_minutes);
  const nRows = Math.max(titles.length, mins.length, 4);
  const actWrap = $('hitl-activities');
  clear(actWrap);
  for (let i = 0; i < nRows; i++) {
    const titleInput = h('input', {
      type: 'text', class: 'input', 'data-role': 'title', 'aria-label': 'Tên hoạt động ' + (i + 1),
    });
    titleInput.value = titles[i] != null ? String(titles[i]) : '';
    const minInput = h('input', {
      type: 'number', class: 'input input--num', min: '1', max: '90', step: '1', inputmode: 'numeric',
      'data-role': 'minutes', 'aria-label': 'Số phút hoạt động ' + (i + 1),
    });
    minInput.value = mins[i] != null ? String(mins[i]) : '';
    titleInput.addEventListener('input', updateHitlValidation);
    minInput.addEventListener('input', updateHitlValidation);
    actWrap.appendChild(h('div', { class: 'hitl-table__row', role: 'row' },
      h('span', { class: 'hitl-table__idx' }, String(i + 1)), titleInput, minInput));
  }

  const goals = asArray(outline.worksheet_goals);
  const goalsWrap = $('hitl-goals');
  clear(goalsWrap);
  const nGoals = Math.max(goals.length, 3);
  for (let i = 0; i < nGoals; i++) {
    const ta = h('textarea', { class: 'input textarea', rows: '2', 'data-role': 'goal', 'aria-label': 'Mục tiêu phiếu ' + (i + 1) });
    ta.value = goals[i] != null ? String(goals[i]) : '';
    goalsWrap.appendChild(h('div', { class: 'hitl-goal' },
      h('span', { class: 'hitl-goal__tier tier-' + i }, TIERS[i] || 'Phiếu ' + (i + 1)), ta));
  }

  $('hitl-confirm').disabled = false;
  $('hitl-reset').disabled = false;
  updateHitlValidation();
  section.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

function readHitlForm() {
  const rows = Array.prototype.slice.call($('hitl-activities').querySelectorAll('.hitl-table__row'));
  const titles = [];
  const minutes = [];
  let invalid = null;
  rows.forEach((row, i) => {
    const t = row.querySelector('[data-role="title"]').value.trim();
    const raw = row.querySelector('[data-role="minutes"]').value.trim();
    const m = Number(raw);
    if (!t && !raw) return; // bỏ qua hàng trống hoàn toàn
    if (!t) invalid = invalid || 'Hoạt động ' + (i + 1) + ' chưa có tên.';
    if (!raw || !Number.isInteger(m) || m <= 0) invalid = invalid || 'Thời lượng hoạt động ' + (i + 1) + ' phải là số nguyên dương.';
    titles.push(t);
    minutes.push(Number.isFinite(m) ? Math.round(m) : 0);
  });
  if (!titles.length) invalid = invalid || 'Khung bài cần ít nhất một hoạt động.';
  const goals = Array.prototype.map.call($('hitl-goals').querySelectorAll('[data-role="goal"]'), (ta) => ta.value.trim())
    .filter((g) => g !== '');
  return {
    outline: {
      objective_summary: $('hitl-objective').value.trim(),
      activity_titles: titles,
      activity_minutes: minutes,
      worksheet_goals: goals,
      is_approved: true,
    },
    total: minutes.reduce((a, b) => a + b, 0),
    invalid,
  };
}

function updateHitlValidation() {
  const run = state.run;
  if (!run || !run.hitl) return;
  const { total, invalid } = readHitlForm();
  const target = Number(run.hitl.target) || 45;
  const totalEl = $('hitl-total');
  totalEl.textContent = total + ' / ' + target + ' phút';
  const mismatch = total !== target;
  totalEl.className = 'hitl-total ' + (mismatch ? 'text-bad' : 'text-ok');
  const warn = $('hitl-warning');
  let msg = invalid;
  if (!msg && mismatch) {
    const diff = total - target;
    msg = 'Tổng thời lượng ' + total + ' phút ' + (diff > 0 ? 'vượt ' + diff : 'thiếu ' + (-diff)) +
      ' phút so với chuẩn ' + target + ' phút. Vui lòng cân đối lại trước khi xác nhận.';
  }
  warn.textContent = msg || '';
  warn.hidden = !msg;
  $('hitl-confirm').disabled = !!msg;
}

function hitlFinish(run, confirmed) {
  const section = $('hitl-section');
  Array.prototype.forEach.call(section.querySelectorAll('input, textarea, button'), (el) => {
    el.disabled = true;
  });
  section.classList.add('is-confirmed');
  const st = $('hitl-state');
  st.className = 'pill ' + (confirmed ? 'pill--done' : 'pill--muted');
  st.textContent = confirmed ? 'Đã xác nhận' : 'Đã huỷ';
  if (!confirmed && run.hitl && run.hitl.card) {
    setCardStatus(run.hitl.card, 'stopped');
    run.hitl.card.closed = true;
  }
  run.hitl = null;
}

function onHitlConfirm() {
  const run = state.run;
  if (!run || !run.hitl) return;
  const { outline, invalid, total } = readHitlForm();
  const target = Number(run.hitl.target) || 45;
  if (invalid || total !== target) {
    updateHitlValidation();
    return;
  }
  run.hitl.confirm(outline);
}

function onHitlReset() {
  const run = state.run;
  if (!run || !run.hitl) return;
  renderHitlForm(run, run.hitl.original);
}

/* ------------------------------------------------------------------ *
 * Trình xem giáo án (UC-04)
 * ------------------------------------------------------------------ */

function onFinal(run, data) {
  run.final = data;
  if (data.thread_id) run.threadId = data.thread_id;
  closeRunningCards(run, 'done');
  updateSessionInfo(run);
  renderPlan(run, data);
  const passed = data.critic_report && data.critic_report.is_passed;
  setRunStatus(passed === false ? 'red' : 'green', passed === false ? 'Hoàn tất (còn lỗi)' : 'Hoàn tất');
}

function inferLessonBai(run, plan) {
  if (run.lessonBai) return run.lessonBai;
  if (run.request) return run.request.target_bai;
  for (const a of objs(plan.activities)) {
    for (const c of objs(a.citations)) if (c && c.bai_so) return Number(c.bai_so);
  }
  return null;
}

function renderPlan(run, data) {
  const plan = data.plan && typeof data.plan === 'object' ? data.plan : {};
  const report = data.critic_report && typeof data.critic_report === 'object' ? data.critic_report : null;
  const verification = objs(data.verification);
  const bai = inferLessonBai(run, plan);
  const lesson = bai ? findLesson(bai) : null;

  $('plan-section').hidden = false;
  $('plan-title').textContent = 'Giáo án ' + (bai ? 'Bài ' + bai : '') + (lesson && lesson.title ? ' – ' + lesson.title : '');
  const totalMin = objs(plan.activities).reduce((s, a) => s + (Number(a.duration_minutes) || 0), 0);
  $('plan-subtitle').textContent = 'SGK Tin học 10 – Kết nối tri thức · ' + totalMin + ' phút · Mã phiên: ' + textOrDash(run.threadId);
  $('download-btn').disabled = !run.threadId;

  // Thống kê nhanh
  const stats = $('plan-stats');
  clear(stats);
  const nPassWs = verification.filter((v) => v.ast_pass && v.sandbox_pass).length;
  appendChild(stats, [
    statTile('Kết quả Critic', report ? (report.is_passed ? 'ĐẠT' : 'CHƯA ĐẠT') : '—', report ? (report.is_passed ? 'ok' : 'bad') : null),
    statTile('Số vòng Designer ↔ Critic', textOrDash(data.iterations)),
    statTile('Phiếu qua AST + Sandbox', verification.length ? nPassWs + '/' + verification.length : '—',
      verification.length ? (nPassWs === verification.length ? 'ok' : 'bad') : null),
    statTile('Độ chính xác trích dẫn', report ? formatPct(report.citation_precision) : '—'),
    statTile('Tổng thời lượng', (report && report.total_minutes_checked ? report.total_minutes_checked : totalMin) + ' phút'),
  ]);
  if (report && report.revision_instructions && !report.is_passed) {
    stats.appendChild(h('p', { class: 'note note--warn stats__note' }, 'Hướng dẫn sửa còn lại: ', report.revision_instructions));
  }

  // Tabs
  const tabs = [{ id: 'plan', label: 'Giáo án 5512', render: () => renderPlanTab(plan) }];
  const worksheets = objs(plan.worksheets);
  const used = new Set();
  TIERS.forEach((tier, i) => {
    let idx = worksheets.findIndex((w, j) => !used.has(j) && normalizeTier(w.tier) === normalizeTier(tier));
    if (idx === -1 && worksheets[i] && !used.has(i) && TIERS.every((t) => normalizeTier(t) !== normalizeTier(worksheets[i].tier))) idx = i;
    if (idx !== -1) used.add(idx);
    const ws = idx !== -1 ? worksheets[idx] : null;
    tabs.push({
      id: 'ws' + i,
      label: 'Phiếu ' + tier,
      tierClass: 'tier-' + i,
      render: () => renderWorksheetTab(ws, ws ? findVerification(verification, ws, idx) : null, tier),
    });
  });
  worksheets.forEach((ws, j) => {
    if (used.has(j)) return;
    tabs.push({ id: 'wsx' + j, label: 'Phiếu ' + textOrDash(ws.tier), render: () => renderWorksheetTab(ws, findVerification(verification, ws, j), ws.tier) });
  });
  renderTabs(tabs);
  $('plan-section').scrollIntoView({ block: 'start', behavior: 'smooth' });
}

function findVerification(list, ws, index) {
  const byTier = list.find((v) => normalizeTier(v.tier) === normalizeTier(ws.tier));
  return byTier || list[index] || null;
}

function statTile(label, value, kind) {
  return h('div', { class: 'stat' + (kind ? ' stat--' + kind : '') },
    h('span', { class: 'stat__label' }, label), h('span', { class: 'stat__value' }, value));
}

function renderTabs(tabs) {
  const tablist = $('plan-tabs');
  const panels = $('plan-panels');
  clear(tablist);
  clear(panels);
  const buttons = [];
  const panelEls = [];
  tabs.forEach((t, i) => {
    const btn = h('button', {
      type: 'button', role: 'tab', class: 'tab' + (t.tierClass ? ' tab--' + t.tierClass : ''),
      id: 'tab-' + t.id, 'aria-controls': 'panel-' + t.id, 'aria-selected': i === 0 ? 'true' : 'false',
      tabindex: i === 0 ? '0' : '-1',
    }, t.label);
    const panel = h('div', { role: 'tabpanel', class: 'tabpanel', id: 'panel-' + t.id, 'aria-labelledby': 'tab-' + t.id });
    panel.hidden = i !== 0;
    appendChild(panel, t.render());
    btn.addEventListener('click', () => select(i));
    btn.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        e.preventDefault();
        const n = (i + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        select(n);
        buttons[n].focus();
      }
    });
    buttons.push(btn);
    panelEls.push(panel);
    tablist.appendChild(btn);
    panels.appendChild(panel);
  });
  function select(n) {
    buttons.forEach((b, j) => {
      b.setAttribute('aria-selected', j === n ? 'true' : 'false');
      b.tabIndex = j === n ? 0 : -1;
      panelEls[j].hidden = j !== n;
    });
  }
}

function section(title, ...children) {
  return h('section', { class: 'doc-section' }, h('h3', { class: 'doc-section__title' }, title), children);
}

function docField(label, value) {
  return h('div', { class: 'doc-field' },
    h('div', { class: 'doc-field__label' }, label),
    h('div', { class: 'doc-field__value prewrap' }, textOrDash(value)));
}

function renderPlanTab(plan) {
  const acts = objs(plan.activities);
  return h('div', { class: 'doc' },
    section('I. Mục tiêu',
      docField('1. Về kiến thức', plan.knowledge_goals),
      docField('2. Về năng lực chung', plan.general_competencies),
      docField('3. Về năng lực Tin học', plan.cs_competencies),
      docField('4. Về phẩm chất', plan.qualities)),
    section('II. Thiết bị dạy học và học liệu',
      h('div', { class: 'doc-field__value prewrap' }, textOrDash(plan.teaching_materials))),
    section('III. Tiến trình dạy học',
      acts.length ? acts.map((a, i) => renderActivity(a, i)) : h('p', { class: 'muted' }, 'Chưa có hoạt động.')));
}

function renderActivity(a, i) {
  const cits = objs(a.citations);
  const detail = h('div', { class: 'citation-detail', hidden: true });
  let openIdx = -1;
  const badges = cits.map((c, j) => {
    const btn = h('button', {
      type: 'button', class: 'citation-badge', 'aria-expanded': 'false',
      title: 'Xem đoạn trích gốc trong SGK',
    }, '📖 SGK, Bài ' + textOrDash(c.bai_so) + ', tr.' + textOrDash(c.page_number));
    btn.addEventListener('click', () => {
      badges.forEach((b) => b.setAttribute('aria-expanded', 'false'));
      if (openIdx === j) {
        openIdx = -1;
        detail.hidden = true;
        return;
      }
      openIdx = j;
      btn.setAttribute('aria-expanded', 'true');
      clear(detail);
      appendChild(detail, [
        h('div', { class: 'citation-detail__head' },
          h('strong', null, textOrDash(c.doc_name)),
          h('span', { class: 'muted' }, ' · Bài ' + textOrDash(c.bai_so) + ' · Trang ' + textOrDash(c.page_number))),
        h('div', { class: 'citation-detail__section' }, 'Mục: ', h('strong', null, textOrDash(c.section_title))),
        h('blockquote', { class: 'citation-detail__quote prewrap' }, textOrDash(c.quoted_text)),
      ]);
      detail.hidden = false;
    });
    return btn;
  });

  const stepRows = STEP_LABELS.map(([k, label]) =>
    h('tr', null, h('th', { scope: 'row' }, label), h('td', { class: 'prewrap' }, textOrDash(a[k]))));

  return h('article', { class: 'activity' },
    h('header', { class: 'activity__head' },
      h('h4', { class: 'activity__title' }, 'Hoạt động ' + (i + 1) + ': ' + textOrDash(a.activity_name)),
      h('span', { class: 'badge badge--info' }, textOrDash(a.duration_minutes) + ' phút')),
    cits.length ? h('div', { class: 'citation-row' }, h('span', { class: 'citation-row__label' }, 'Nguồn:'), badges) :
      h('div', { class: 'citation-row' }, badge('Chưa có trích dẫn SGK', 'warn')),
    detail,
    docField('a) Mục tiêu', a.goal),
    docField('b) Nội dung', a.content),
    docField('c) Sản phẩm', a.expected_product),
    h('div', { class: 'doc-field' },
      h('div', { class: 'doc-field__label' }, 'd) Tổ chức thực hiện'),
      h('div', { class: 'table-wrap' }, h('table', { class: 'table table--steps' }, h('tbody', null, stepRows)))));
}

function codeBlock(code, emptyText) {
  if (code == null || String(code).trim() === '') return h('p', { class: 'muted' }, emptyText || '(Không có)');
  return h('pre', { class: 'code' }, h('code', null, String(code)));
}

function renderWorksheetTab(ws, ver, tier) {
  if (!ws) return h('p', { class: 'muted' }, 'Giáo án không có phiếu học tập mức ' + tier + '.');
  const tests = objs(ws.test_cases);

  const badges = h('div', { class: 'trace-row' });
  if (ws.bloom_level) badges.appendChild(badge('Bloom: ' + ws.bloom_level, 'info'));
  if (ver) {
    badges.appendChild(badge(ver.ast_pass ? 'AST PASS' : 'AST FAIL', ver.ast_pass ? 'ok' : 'bad', 'Kiểm tra phạm vi cú pháp theo SGK'));
    const n = Number(ver.n_passed) || 0;
    const total = Number(ver.total) || 0;
    const ok = ver.sandbox_pass && n === total;
    badges.appendChild(badge('Sandbox ' + n + '/' + total, ok ? 'ok' : 'bad', 'Số test case chạy đúng trong sandbox'));
  } else {
    badges.appendChild(badge('Chưa có kết quả kiểm định', 'muted'));
  }

  const solutionWrap = h('div', { class: 'solution', hidden: true },
    h('h4', { class: 'sub-title' }, 'Đáp án (solution_code)'),
    codeBlock(ws.solution_code, '(Chưa có đáp án)'),
    h('h4', { class: 'sub-title' }, 'Kết quả chạy Sandbox'),
    renderCaseResults(ver));
  const toggle = h('button', { type: 'button', class: 'btn btn--ghost', 'aria-expanded': 'false' }, 'Hiện đáp án & kết quả Sandbox');
  toggle.addEventListener('click', () => {
    const show = solutionWrap.hidden;
    solutionWrap.hidden = !show;
    toggle.setAttribute('aria-expanded', show ? 'true' : 'false');
    toggle.textContent = show ? 'Ẩn đáp án' : 'Hiện đáp án & kết quả Sandbox';
  });

  return h('div', { class: 'worksheet' },
    h('div', { class: 'worksheet__head' },
      h('h3', { class: 'worksheet__title' }, 'Phiếu học tập – ' + textOrDash(ws.tier)),
      badges),
    ver ? issueList('AST Guard', ver.ast_messages, 'bad') : null,
    ver ? issueList('Cảnh báo AST', ver.ast_warnings, 'warn') : null,
    h('h4', { class: 'sub-title' }, 'Đề bài'),
    h('p', { class: 'prewrap worksheet__prompt' }, textOrDash(ws.prompt_text)),
    h('h4', { class: 'sub-title' }, 'Code khởi đầu (starter_code)'),
    codeBlock(ws.starter_code),
    h('h4', { class: 'sub-title' }, 'Bộ test (' + tests.length + ')'),
    tests.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
      h('thead', null, h('tr', null, h('th', null, '#'), h('th', null, 'Đầu vào (stdin)'), h('th', null, 'Kết quả mong đợi'), h('th', null, 'Loại'))),
      h('tbody', null, tests.map((t, i) => h('tr', null,
        h('td', null, String(i + 1)),
        h('td', null, h('pre', { class: 'cell-code' }, String(t.stdin_data == null ? '' : t.stdin_data))),
        h('td', null, h('pre', { class: 'cell-code' }, String(t.expected_stdout == null ? '' : t.expected_stdout))),
        h('td', null, t.is_edge_case ? badge('Biên', 'warn') : badge('Thường', 'muted'))))))) :
      h('p', { class: 'muted' }, 'Chưa có test case.'),
    h('div', { class: 'actions-row actions-row--start' }, toggle),
    solutionWrap);
}

function renderCaseResults(ver) {
  const cases = ver ? objs(ver.cases) : [];
  if (!cases.length) return h('p', { class: 'muted' }, 'Chưa có kết quả chạy từng test case.');
  return h('div', { class: 'table-wrap' }, h('table', { class: 'table' },
    h('thead', null, h('tr', null,
      h('th', null, '#'), h('th', null, 'Đầu vào'), h('th', null, 'Mong đợi'), h('th', null, 'Thực tế'), h('th', null, 'Trạng thái'))),
    h('tbody', null, cases.map((c, i) => {
      const pass = String(c.status || '').toUpperCase() === 'PASS';
      const idx = c.index != null ? Number(c.index) + 1 : i + 1;
      return h('tr', { class: pass ? '' : 'row-bad' },
        h('td', null, String(idx), c.is_edge_case ? h('span', { class: 'muted' }, ' (biên)') : null),
        h('td', null, h('pre', { class: 'cell-code' }, String(c.stdin == null ? '' : c.stdin))),
        h('td', null, h('pre', { class: 'cell-code' }, String(c.expected == null ? '' : c.expected))),
        h('td', null, h('pre', { class: 'cell-code' }, String(c.actual == null ? '' : c.actual))),
        h('td', null, badge(textOrDash(c.status), pass ? 'ok' : 'bad')));
    }))));
}

/* ------------------------------------------------------------------ *
 * Tải file Word (UC-05)
 * ------------------------------------------------------------------ */

function filenameFromDisposition(header) {
  if (!header) return null;
  const star = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(header);
  if (star) {
    try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')); } catch (_) { /* tiếp tục */ }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/.exec(header);
  return plain ? plain[1].trim() : null;
}

async function onDownload() {
  const run = state.run;
  if (!run || !run.threadId) {
    showAlert('error', 'Chưa có giáo án để tải.', 'Vui lòng chạy một phiên soạn giáo án trước.');
    return;
  }
  const btn = $('download-btn');
  const oldText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Đang tạo file…';
  try {
    const resp = await fetch(API.exportDocx(run.threadId));
    if (!resp.ok) throw new Error(await describeHttpError(resp));
    const blob = await resp.blob();
    const name = filenameFromDisposition(resp.headers.get('Content-Disposition')) ||
      'giao_an_' + (run.lessonBai ? 'bai' + run.lessonBai : run.threadId) + '.docx';
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: name, class: 'visually-hidden' });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  } catch (err) {
    showAlert('error', 'Không tải được file Word.', err && err.message ? err.message : String(err));
  } finally {
    btn.disabled = false;
    btn.textContent = oldText;
  }
}

/* ------------------------------------------------------------------ *
 * Điều phối phiên: start / demo → (HITL) → resume → final
 * ------------------------------------------------------------------ */

async function describeHttpError(resp) {
  let detail = '';
  try {
    const text = await resp.text();
    const j = parseJSONSafe(text);
    if (j && j.detail != null) {
      if (Array.isArray(j.detail)) {
        detail = j.detail.map((d) => (d && d.msg ? (asArray(d.loc).slice(-1)[0] || '') + ': ' + d.msg : JSON.stringify(d))).join('; ');
      } else {
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
      }
    } else if (j && j.message) {
      detail = String(j.message);
    } else if (text) {
      detail = text.slice(0, 300);
    }
  } catch (_) { /* bỏ qua */ }
  return 'HTTP ' + resp.status + (resp.statusText ? ' ' + resp.statusText : '') + (detail ? ' – ' + detail : '');
}

async function openStream(url, init) {
  const resp = await fetch(url, init);
  if (!resp.ok) throw new Error(await describeHttpError(resp));
  return resp;
}

function updateSessionInfo(run) {
  const modeEl = $('session-mode');
  modeEl.className = 'chip ' + (run.mode === 'demo' ? 'chip--warn' : 'chip--info');
  modeEl.textContent = run.mode === 'demo' ? 'Demo offline (mock)' : 'Phiên trực tiếp';
  $('session-thread').textContent = run.threadId ? 'Mã phiên: ' + run.threadId : '';
}

function resetRunUI(run) {
  clearAlerts();
  $('empty-state').hidden = true;
  $('trace-section').hidden = false;
  clear($('trace-list'));
  clear($('critic-progress-items'));
  $('critic-progress').hidden = true;
  $('hitl-section').hidden = true;
  $('plan-section').hidden = true;
  clear($('plan-tabs'));
  clear($('plan-panels'));
  clear($('plan-stats'));
  updateSessionInfo(run);
  setRunStatus('running');
}

async function handleSSEEvent(run, ev) {
  if (state.run !== run) return; // phiên cũ – bỏ qua
  if (run.paceMs) {
    const wait = run.paceMs - (Date.now() - run.lastDispatch);
    if (wait > 0) await sleep(wait);
    if (run.abort.signal.aborted) throw makeAbortError();
  }
  run.lastDispatch = Date.now();
  const parsed = parseJSONSafe(ev.data);
  const data = parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : { _raw: ev.data };
  try {
    switch (ev.event) {
      case 'session':
        if (data.thread_id) run.threadId = data.thread_id;
        updateSessionInfo(run);
        break;
      case 'node_start':
        onNodeStart(run, data);
        break;
      case 'node_end':
        onNodeEnd(run, data);
        break;
      case 'critic':
        onCritic(run, data);
        break;
      case 'hitl_required':
        await onHitlRequired(run, data);
        break;
      case 'final':
        onFinal(run, data);
        break;
      case 'error':
        run.failed = true;
        closeRunningCards(run, 'error');
        setRunStatus('error');
        showAlert('error', 'Máy chủ báo lỗi:', data.message || data._raw || 'Lỗi không xác định.');
        break;
      case 'done':
        if (run.final) closeRunningCards(run, 'done');
        break;
      case '_resume_marker':
        break; // chỉ có trong file mock – không phải sự kiện thật
      default:
        if (typeof console !== 'undefined') console.debug('Sự kiện SSE không xác định:', ev.event, data);
    }
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    // Lỗi hiển thị một sự kiện (dữ liệu lạ) không được làm đứt luồng.
    if (typeof console !== 'undefined') console.error('Lỗi xử lý sự kiện', ev.event, err);
    showAlert('error', 'Không hiển thị được sự kiện "' + ev.event + '":', err && err.message ? err.message : String(err));
  }
}

async function runSession(mode) {
  if (state.busy) return;
  const run = newRun(mode);
  if (mode === 'live') {
    const problem = validateConfig();
    if (problem) {
      updateStartButton();
      return;
    }
    run.request = buildLessonRequest();
    run.lessonBai = run.request.target_bai;
    run.targetMinutes = run.request.target_minutes;
  }
  if (state.run) state.run.abort.abort();
  state.run = run;
  resetRunUI(run);
  setBusy(true);

  const handler = (ev) => handleSSEEvent(run, ev);
  const signal = run.abort.signal;
  try {
    let resp;
    if (mode === 'demo') {
      resp = await openStream(API.demo, { headers: { Accept: 'text/event-stream' }, signal, cache: 'no-store' });
    } else {
      resp = await openStream(API.start, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify(run.request),
        signal,
        cache: 'no-store',
      });
    }
    await consumeSSE(resp, handler);

    // Luồng /start đóng sau hitl_required → gửi /resume với khung đã duyệt.
    // (Luồng demo có thể vẫn mở sau HITL; khi đó final đã đến và không cần /resume.)
    let guard = 0;
    while (run.approvedOutline && !run.final && !run.failed && guard < 5) {
      guard++;
      const outline = run.approvedOutline;
      run.approvedOutline = null;
      run.paceMs = 0;
      if (!run.threadId) throw new Error('Thiếu thread_id để tiếp tục phiên sau khi duyệt.');
      resp = await openStream(API.resume, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ thread_id: run.threadId, outline }),
        signal,
        cache: 'no-store',
      });
      await consumeSSE(resp, handler);
    }

    if (!run.final && !run.failed && state.run === run) {
      closeRunningCards(run, 'stopped');
      setRunStatus('error', 'Kết thúc bất thường');
      showAlert('error', 'Luồng dữ liệu kết thúc trước khi có giáo án hoàn chỉnh.', 'Vui lòng thử lại.');
    }
  } catch (err) {
    if (state.run === run) {
      if (err && err.name === 'AbortError') {
        run.cancelled = true;
        closeRunningCards(run, 'stopped');
        setRunStatus('stopped');
        showAlert('info', 'Đã dừng phiên.', 'Có thể cấu hình lại và bắt đầu phiên mới.');
      } else {
        run.failed = true;
        closeRunningCards(run, 'error');
        setRunStatus('error');
        const msg = err && err.message ? err.message : String(err);
        const network = !!err && err.name === 'TypeError';
        showAlert('error', network ? 'Lỗi kết nối tới máy chủ:' : 'Lỗi:', msg);
      }
    }
  } finally {
    if (run.hitl) hitlFinish(run, false);
    if (state.run === run) setBusy(false);
  }
}

function onCancel() {
  if (state.run) state.run.abort.abort();
}

/* ------------------------------------------------------------------ *
 * Khởi động
 * ------------------------------------------------------------------ */

function init() {
  RATIO_KEYS.forEach((k) => {
    $('ratio-' + k).addEventListener('input', (e) => {
      state.ratios = rebalanceRatios(state.ratios, k, e.target.value);
      renderRatios();
    });
  });
  $('scope-select').addEventListener('change', updateTargetMinutes);
  $('lesson-select').addEventListener('change', updateStartButton);
  $('config-form').addEventListener('submit', (e) => {
    e.preventDefault();
    runSession('live');
  });
  $('demo-btn').addEventListener('click', () => runSession('demo'));
  $('cancel-btn').addEventListener('click', onCancel);
  $('hitl-confirm').addEventListener('click', onHitlConfirm);
  $('hitl-reset').addEventListener('click', onHitlReset);
  $('download-btn').addEventListener('click', onDownload);

  renderLessonOptions();
  renderScopeOptions();
  renderRatios();
  loadMeta();
  loadHealth();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
}
