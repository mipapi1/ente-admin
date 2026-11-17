// ente-admin UI. Plain DOM, no dependencies. Data is inserted with textContent / DOM
// nodes only (never innerHTML), so account data can't inject markup.
"use strict";

const $ = (s) => document.querySelector(s);
const SVG = "http://www.w3.org/2000/svg";
const state = { cfg: null, data: [], me: null, candidates: null, tab: "all", sort: { key: "used", dir: -1 },
  filter: "", quick: "all", open: null, timer: null };

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) el.append(k instanceof Node ? k : String(k));
  return el;
}
function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const k of kids) el.append(k);
  return el;
}

// ---- formatting ----
const nf = new Intl.NumberFormat();
function bytes(n) {
  if (n === null || n === undefined) return "—";
  const u = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)} ${u[i]}`;
}
const num = (n) => (n === null || n === undefined ? "—" : nf.format(n));
function ago(t) {
  if (!t) return "—";
  const d = Date.now() / 1000 - t;
  if (d < 90) return "just now";
  if (d < 3600) return `${Math.round(d / 60)} min ago`;
  if (d < 86400) return `${Math.round(d / 3600)} h ago`;
  if (d < 86400 * 60) return `${Math.round(d / 86400)} d ago`;
  return new Date(t * 1000).toLocaleDateString();
}
const day = (t) => (t ? new Date(t * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—");

// ---- charts ----
function sparkline(values, { w = 110, ht = 26, area = true, days = null, bytesPerDay = null } = {}) {
  const svg = s("svg", { viewBox: `0 0 ${w} ${ht}`, class: "spark", "aria-hidden": "true", preserveAspectRatio: "none" });
  if (!values || !values.length) return svg;
  const max = Math.max(1, ...values);
  const step = w / Math.max(1, values.length - 1);
  const pts = values.map((v, i) => [i * step, ht - 2 - (v / max) * (ht - 4)]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  if (area) svg.append(s("path", { d: `${d}L${w},${ht}L0,${ht}Z`, fill: "var(--bar-2)", opacity: ".35" }));
  svg.append(s("path", { d, fill: "none", stroke: "var(--bar)", "stroke-width": "1.6", "stroke-linejoin": "round" }));
  if (days) values.forEach((v, i) => {  // invisible columns, one per day, for the tooltip
    const x0 = Math.max(0, i * step - step / 2), x1 = Math.min(w, i * step + step / 2);
    const hit = s("rect", { x: x0.toFixed(1), y: 0, width: (x1 - x0).toFixed(1), height: ht, fill: "transparent", class: "hit" });
    tipData(hit, days[i], v, bytesPerDay && bytesPerDay[i], i === values.length - 1);
    svg.append(hit);
  });
  return svg;
}
function dayLabel(iso, today) {
  const t = new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return today ? `${t} (today)` : t;
}
function tipData(el, iso, n, b, today) {
  el.dataset.tipTitle = dayLabel(iso, today);
  el.dataset.tip = n ? `${nf.format(n)} file${n === 1 ? "" : "s"} added${b ? ` · ${bytes(b)}` : ""}` : "no uploads";
}
function barChart(el, days, values, width, bytesPerDay) {
  el.replaceChildren();
  // drawn at the element's real width so text isn't stretched; redrawn on resize
  const w = Math.max(240, Math.round(width || el.clientWidth || 420)), ht = 160, pad = 22;
  const svg = s("svg", { viewBox: `0 0 ${w} ${ht}`, width: w, height: ht });
  const max = Math.max(1, ...values);
  const bw = (w - pad) / values.length;
  values.forEach((v, i) => {
    const bh = (v / max) * (ht - pad - 8);
    // a full-height column per day takes the hover, so short bars are easy to point at
    const col = s("g", { class: "col", tabindex: "0" });
    col.append(s("rect", { x: (i * bw).toFixed(1), y: 0, width: bw.toFixed(1), height: ht - pad, fill: "transparent" }));
    col.append(s("rect", { x: (i * bw + 3).toFixed(1), y: (ht - pad - bh).toFixed(1), width: (bw - 6).toFixed(1),
      height: Math.max(bh, 1).toFixed(1), rx: "4", class: "bar", fill: i === values.length - 1 ? "var(--accent-2)" : "var(--bar)" }));
    tipData(col, days[i], v, bytesPerDay && bytesPerDay[i], i === values.length - 1);
    svg.append(col);
    if ((values.length - 1 - i) % Math.ceil(values.length / Math.max(2, Math.floor(w / 70))) === 0) {
      const label = new Date(days[i] + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
      svg.append(s("text", { x: (i * bw + bw / 2).toFixed(1), y: ht - 6, "text-anchor": "middle" }, label));
    }
  });
  el.append(svg);
  el.setAttribute("aria-label", `Files uploaded per day: ${days.map((d, i) => `${d} ${values[i]}`).join(", ")}`);
}

// ---- data ----
function visible() {
  return state.data.filter((d) => state.tab === "all" || d.instance === state.tab);
}
function accounts() {
  const rows = [];
  for (const inst of visible()) for (const a of inst.accounts) rows.push({ ...a, instance: inst.instance, days: inst.days });
  return rows;
}
function sum(rows, key) {
  let t = 0, any = false;
  for (const r of rows) if (typeof r[key] === "number") { t += r[key]; any = true; }
  return any ? t : null;
}

// ---- render ----
function renderTabs() {
  const nav = $("#instances");
  const tabs = [["all", "All"], ...state.data.map((d) => [d.instance, d.instance])];
  nav.replaceChildren(...tabs.map(([id, label]) => h("button", {
    type: "button", role: "tab", "aria-selected": String(state.tab === id),
    onclick: () => { state.tab = id; render(); },
  }, label)));
  nav.setAttribute("role", "tablist");
}

function renderNotes() {
  const box = $("#notes");
  const items = [];
  for (const d of visible()) {
    if (d.error) items.push(h("div", { class: "note error" }, `${d.instance}: ${d.error}`));
    for (const n of d.notes || []) items.push(h("div", { class: "note" }, `${d.instance}: ${n}`));
  }
  box.replaceChildren(...items);
}

function kpi(label, value, sub, extra, key) {
  const img = key && brand().kpi_images && brand().kpi_images[key];
  return h("div", { class: `kpi${img ? " has-art" : ""}` }, h("span", { class: "label" }, label),
    h("span", { class: "value" }, value), sub ? h("span", { class: "sub" }, sub) : null, extra || null,
    img ? h("img", { class: "kpi-art", src: img, alt: "" }) : null);
}

function dailyTotals(insts) {
  const ref = insts.find((d) => d.days);
  if (!ref) return null;
  const totals = new Array(ref.days.length).fill(0), sizes = new Array(ref.days.length).fill(0);
  let any = false, anySize = false;
  for (const d of insts) for (const a of d.accounts) {
    if (a.daily_uploads) { any = true; a.daily_uploads.forEach((v, i) => { totals[i] += v; }); }
    if (a.daily_bytes) { anySize = true; a.daily_bytes.forEach((v, i) => { sizes[i] += v; }); }
  }
  return any ? { days: ref.days, totals, sizes: anySize ? sizes : null } : null;
}

function renderOverview() {
  const sec = $("#overview");
  if (!state.cfg.modules.includes("overview")) return;
  const rows = accounts();
  const used = sum(rows, "used"), quota = sum(rows, "quota");
  const now = Date.now() / 1000;
  const active = rows.filter((r) => r.last_seen && now - r.last_seen < 86400).length;
  const uploading = rows.filter((r) => r.uploading).length;
  const recent = sum(rows, "recent_uploads");
  const devices = rows.reduce((t, r) => t + (r.devices ? r.devices.length : 0), 0);
  const series = dailyTotals(visible());
  const servers = new Set(rows.map((r) => r.instance)).size;
  sec.replaceChildren(
    kpi("Accounts", num(rows.length), `${active} active in the last 24 h${servers > 1 ? ` · ${servers} servers` : ""}`, null, "accounts"),
    kpi("Storage used", bytes(used), quota ? `of ${bytes(quota)} allotted (${Math.round((used / quota) * 100)}%)` : null,
      null, "storage"),
    kpi("Files", num(sum(rows, "files")), series ? `${nf.format(series.totals.at(-1))} today` : null,
      series ? sparkline(series.totals, { w: 200, ht: 30, days: series.days, bytesPerDay: series.sizes }) : null, "files"),
    kpi("Signed-in devices", num(devices), "sessions that aren't signed out", null, "devices"),
    kpi("Uploading now", uploading ? h("span", { class: "live" }, String(uploading)) : "0",
      !rows.some((r) => r.uploading !== null && r.uploading !== undefined) ? "needs the postgres source"
        : uploading && recent ? `${nf.format(recent)} files in the last 5 min` : "files added in the last 5 min",
      null, "uploading"),
  );
  sec.classList.toggle("go", uploading > 0);
  sec.hidden = false;
}

function renderActivity() {
  const sec = $("#activity");
  if (!state.cfg.modules.includes("activity")) return;
  const series = dailyTotals(visible());
  if (!series) { sec.hidden = true; return; }
  sec.hidden = false;
  barChart($("#activity-chart"), series.days, series.totals, null, series.sizes);
  const total = series.totals.reduce((a, b) => a + b, 0);
  $("#activity-total").textContent = `${nf.format(total)} ${total === 1 ? "file" : "files"}`;
}

function security(a) {
  const b = [];
  if (a.two_factor === true) b.push(h("span", { class: "badge ok" }, "2FA"));
  else if (a.two_factor === false) b.push(h("span", { class: "badge warn" }, "no 2FA"));
  if (a.email_mfa) b.push(h("span", { class: "badge ok" }, "email codes"));
  if (a.plan_expiry && a.plan_expiry < Date.now() / 1000) b.push(h("span", { class: "badge warn" }, "plan expired"));
  return b.length ? h("div", { class: "badges" }, b) : h("span", { class: "na" }, "—");
}

function usage(a) {
  if (a.used === null || a.used === undefined) return h("span", { class: "na" }, "—");
  const pct = a.quota ? Math.min(100, (a.used / a.quota) * 100) : null;
  const fill = h("div", { class: `fill${pct > 95 ? " danger" : pct > 85 ? " warn" : ""}` });
  fill.style.width = `${pct ?? 0}%`;
  return h("div", { class: "usage" }, h("div", { class: "track" }, fill),
    h("small", {}, a.quota ? `${bytes(a.used)} of ${bytes(a.quota)}` : bytes(a.used)));
}

// quick filters above the accounts table
const QUICK = [
  ["all", "All", () => true],
  ["no2fa", "No 2FA", (a) => a.two_factor === false],
  ["idle", "Idle 15+ days", (a) => a.last_seen && Date.now() / 1000 - a.last_seen > 15 * 86400],
  ["quota", "Near quota", (a) => a.quota && a.used / a.quota > 0.85],
  ["locker", "Uses Locker", (a) => a.locker_items > 0],
  ["shared", "Shares albums", (a) => a.shares_out > 0 || a.album_links > 0],
];
function renderQuick(all) {
  const box = $("#quick");
  box.replaceChildren(...QUICK.filter(([id]) => id !== "locker" || all.some((a) => a.locker_items !== null && a.locker_items !== undefined))
    .filter(([id]) => id !== "shared" || all.some((a) => a.shares_out !== null && a.shares_out !== undefined))
    .map(([id, label, fn]) => h("button", { type: "button", class: "qchip", "aria-pressed": String(state.quick === id),
      onclick: () => { state.quick = id; renderTable(); } }, label, h("span", { class: "n" }, String(all.filter(fn).length)))));
}
function renderTable() {
  const sec = $("#accounts");
  if (!state.cfg.modules.includes("accounts")) return;
  const f = state.filter.toLowerCase();
  const { key, dir } = state.sort;
  const all = accounts();
  renderQuick(all);
  const q = (QUICK.find(([id]) => id === state.quick) || QUICK[0])[2];
  const hasLocker = all.some((a) => a.locker_items !== null && a.locker_items !== undefined);
  for (const el of document.querySelectorAll(".col-locker")) el.hidden = !hasLocker;
  const rows = all.filter(q)
    .filter((a) => !f || `${a.email ?? ""} ${a.label} ${a.instance}`.toLowerCase().includes(f))
    .sort((x, y) => {
      const a = x[key], b = y[key];
      if (a === b) return 0;
      if (a === null || a === undefined) return 1;
      if (b === null || b === undefined) return -1;
      return (a > b ? 1 : -1) * dir;
    });
  const multi = state.tab === "all" && state.data.length > 1;
  $("#table tbody").replaceChildren(...rows.map((a) => {
    const tr = h("tr", { tabindex: "0", "aria-label": `Open ${a.email ?? a.label}` },
      h("td", {}, h("div", { class: "acct" }, h("span", { class: "email" }, a.email ?? a.label,
        a.own ? h("span", { class: "you" }, "you") : null),
        h("span", { class: "label" }, multi ? `${a.label} · ${a.instance}` : a.label))),
      h("td", {}, usage(a)),
      h("td", { class: "num" }, num(a.files)),
      h("td", { class: "num hide-sm" }, num(a.albums)),
      h("td", { class: "num hide-sm col-locker", hidden: !hasLocker }, num(a.locker_items)),
      h("td", { class: "hide-sm" }, a.daily_uploads
        ? sparkline(a.daily_uploads, { days: a.days, bytesPerDay: a.daily_bytes }) : h("span", { class: "na" }, "—")),
      h("td", {}, a.uploading ? h("span", { class: "live" }, "uploading") : ago(a.last_seen)),
      h("td", { class: "hide-sm" }, security(a)));
    tr.addEventListener("click", () => openDrawer(a));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDrawer(a); } });
    return tr;
  }));
  $("#empty").hidden = rows.length > 0;
  for (const b of document.querySelectorAll("th button")) {
    if (b.dataset.sort === key) b.setAttribute("aria-sort", dir > 0 ? "ascending" : "descending");
    else b.removeAttribute("aria-sort");
  }
  sec.hidden = false;
}

function openDrawer(a) {
  state.open = a;
  $("#drawer-title").replaceChildren(a.email ?? a.label, a.own ? h("span", { class: "you" }, "you") : "");
  $("#drawer-sub").textContent = `${a.label} · ${a.instance}`;
  const facts = [
    ["Storage", a.quota ? `${bytes(a.used)} of ${bytes(a.quota)}` : bytes(a.used)],
    ["Files", num(a.files)], ["Size of the files", bytes(a.file_bytes)],
    ["Albums", num(a.albums)], ["In trash", num(a.trash)],
    ["Last upload", a.uploading && a.recent_uploads ? `uploading: ${nf.format(a.recent_uploads)} files in 5 min` : ago(a.last_upload)],
    ["Last active", ago(a.last_seen)],
    ["Account created", day(a.created)], ["Plan valid until", day(a.plan_expiry)],
    ["Two-factor", a.two_factor === null || a.two_factor === undefined ? "—" : a.two_factor ? "on" : "off"],
  ];
  const body = [h("dl", { class: "facts" }, facts.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))];
  if (a.daily_uploads) {
    const inst = state.data.find((d) => d.instance === a.instance);
    const chart = h("div", { class: "chart", role: "img" });
    barChart(chart, inst.days, a.daily_uploads, Math.min(window.innerWidth, 460) - 40, a.daily_bytes);
    body.push(h("h3", {}, "Uploads, last 14 days"), chart);
  }
  if (state.cfg.modules.includes("devices")) {
    body.push(h("h3", {}, `Devices (${a.devices.length})`));
    body.push(a.devices.length ? h("ul", { class: "devices" }, a.devices.map((d) => h("li", {},
      h("div", {}, h("div", {}, d.device), h("small", {}, `${d.app} · signed in ${day(d.signed_in)}`)),
      h("small", {}, ago(d.last_seen))))) : h("p", { class: "muted" }, "No signed-in devices."));
  }
  if (state.cfg.sharing && a.shared_with) body.push(shareBox(a));
  if (typeof drawerExtras === "function") body.push(...drawerExtras(a));
  if (state.cfg.portal) body.push(manageBox(a));
  $("#drawer-body").replaceChildren(...body);
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  $("#drawer-close").focus();
}
function closeDrawer() {
  if (!state.open) return;
  state.open = null;
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
}

// ---- sharing (admins, with ENTE_ADMIN_SHARING) ----
function nameOf(user) {
  const c = (state.candidates || []).find((x) => x.user === user);
  return c && c.name !== user ? `${c.name} (${user})` : user;
}
function shareBox(a) {
  const msg = h("small", { class: "muted", "aria-live": "polite" });
  const chips = h("div", { class: "share-chips" }, h("span", { class: "chip-user fixed", title: "admins see every account" }, "admins"),
    a.shared_with.map((u) => h("span", { class: "chip-user" }, nameOf(u), h("button", {
      type: "button", "aria-label": `Stop sharing with ${u}`, title: `Stop sharing with ${u}`,
      onclick: () => { if (confirm(`Stop sharing this account with ${u}?`)) grant(false, a, u, msg); },
    }, "✕"))));
  let picker;
  if (state.candidates) {
    const avail = state.candidates.filter((c) => !a.shared_with.includes(c.user));
    picker = avail.length
      ? h("select", { "aria-label": "Share with" }, h("option", { value: "" }, "Choose a user…"),
        avail.map((c) => h("option", { value: c.user }, c.name !== c.user ? `${c.name} (${c.user})` : c.user)))
      : null;
    if (!avail.length) msg.textContent = "Everyone who may open this dashboard can already see this account.";
  } else {
    picker = h("input", { "aria-label": "Username to share with", placeholder: "username", autocomplete: "off", spellcheck: "false" });
  }
  const form = h("form", { class: "share-form" }, picker, picker ? h("button", { type: "submit" }, "Share") : null);
  form.addEventListener("submit", (e) => { e.preventDefault(); if (picker && picker.value.trim()) grant(true, a, picker.value.trim(), msg); });
  return h("div", { class: "share" }, h("h3", {}, "Visible to"), chips, form, msg);
}
async function grant(add, a, username, msg) {
  msg.textContent = add ? "Sharing…" : "Removing…";
  const r = await fetch("/api/grants", {
    method: "POST", headers: { "Content-Type": "application/json", "X-Ente-Admin": "1" },
    body: JSON.stringify({ add, username, instance: a.instance, account: a.label }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { msg.textContent = `Failed: ${j.error || r.status}`; return; }
  await load();
  const fresh = accounts().find((x) => x.instance === a.instance && x.label === a.label);
  if (fresh) openDrawer(fresh);
}

// ---- admin portal (ENTE_ADMIN_ENABLE_ACTIONS, admins only) ----
// The server keeps the Ente session; the browser only ever sees lock state, previews and
// results. Passwords and codes are sent once, in the unlock request, and never stored here.
const portal = { status: {}, health: {}, audit: [], filter: "", timer: null };

async function api(path, body) {
  const opts = body === undefined ? { redirect: "manual" } : {
    method: "POST", redirect: "manual", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", "X-Ente-Admin": "1" },
  };
  let r;
  try { r = await fetch(path, opts); } catch (e) { return { ok: false, status: 0, data: { error: `network: ${e.message}` } }; }
  if (sessionEnded(r)) return { ok: false, status: 401, data: { error: "signed out of the dashboard" } };
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && path.startsWith("/api/admin/") && /locked/.test(data.error || "")) loadPortal();
  return { ok: r.ok, status: r.status, data };
}
const unlocked = (inst) => !!(portal.status[inst] && portal.status[inst].unlocked);
function mins(s) {
  if (s <= 0) return "now";
  return s < 90 ? `${s} s` : `${Math.round(s / 60)} min`;
}

async function loadPortal({ health = false } = {}) {
  if (!state.cfg || !state.cfg.portal) return;
  const [st, au, he] = await Promise.all([api("/api/admin/status"), api("/api/admin/audit?limit=200"),
    health || !Object.keys(portal.health).length ? api("/api/admin/health") : null]);
  if (st.ok) portal.status = Object.fromEntries(st.data.instances.map((i) => [i.instance, i]));
  if (au.ok) portal.audit = au.data.entries;
  if (he && he.ok) portal.health = Object.fromEntries(he.data.instances.map((i) => [i.instance, i]));
  renderAdmin();
  // keep countdowns honest while anything is unlocked
  clearTimeout(portal.timer);
  if (Object.values(portal.status).some((s) => s.unlocked)) portal.timer = setTimeout(() => loadPortal(), 20_000);
}

function renderAdmin() {
  const sec = $("#admin");
  if (!state.cfg || !state.cfg.portal) { sec.hidden = true; $("#audit-panel").hidden = true; return; }
  sec.hidden = false;
  $("#audit-panel").hidden = false;
  const names = Object.keys(portal.status);
  $("#servers").replaceChildren(...names.map((inst) => {
    const st = portal.status[inst], he = portal.health[inst] || {};
    const health = he.ok === undefined ? h("span", { class: "muted" }, "checking…")
      : he.ok ? h("span", { class: "badge ok" }, `online · ${he.ms} ms`) : h("span", { class: "badge warn" }, "unreachable");
    const facts = h("dl", { class: "facts tight" },
      h("dt", {}, "Server"), h("dd", {}, health),
      h("dt", {}, "Version"), h("dd", { class: "mono" }, he.version || "—"),
      h("dt", {}, "Accounts"), h("dd", {}, he.accounts === undefined ? "—" : num(he.accounts)),
      h("dt", {}, "Data"), h("dd", {}, he.data_error ? h("span", { class: "badge warn" }, "error")
        : he.data_at ? `${he.source} · ${ago(he.data_at)}` : "—"));
    const lock = st.unlocked
      ? h("div", { class: "lock on" }, h("div", {}, h("b", {}, "🔓 Unlocked"), h("small", { class: "muted" },
        ` as ${st.email} · locks in ${mins(st.left)} if idle`),
      st.fresh_left > 0 ? h("small", { class: "muted block" }, `Sign-in-sensitive actions for ${mins(st.fresh_left)} more`)
        : h("small", { class: "muted block" }, "Turning off two-factor or deleting needs a new unlock")),
      h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => serverActions(inst) }, "Server tools"),
        h("button", { class: "btn", type: "button", onclick: () => lockInstance(inst) }, "Lock")))
      : h("div", { class: "lock" }, h("div", {}, h("b", {}, "🔒 Locked"),
        h("small", { class: "muted block" }, st.pending ? `A code was sent to ${st.pending}` : "Read-only until you sign in")),
      h("button", { class: "btn primary", type: "button", onclick: () => unlockDialog(inst) }, st.pending ? "Continue" : "Unlock"));
    return h("div", { class: "server" }, h("div", { class: "server-head" }, h("strong", {}, inst)), facts, lock);
  }));
  renderAudit();
  if (typeof renderHome === "function") renderHome();
}

function changeSummary(e) {
  if (typeof auditSummary === "function") { const t = auditSummary(e); if (t !== null) return t; }
  if (!e.before || !e.after) return e.error || "";
  const keys = ["plan_storage", "plan_expiry", "bonus_storage", "two_factor", "email_mfa", "email", "family"];
  const fmt = (k, v) => (v === null || v === undefined ? "—" : k.endsWith("storage") ? bytes(v)
    : k === "plan_expiry" ? day(v / 1e6) : k === "family" ? `${v.length} members` : typeof v === "boolean" ? (v ? "on" : "off") : String(v));
  return keys.filter((k) => JSON.stringify(e.before[k]) !== JSON.stringify(e.after[k]))
    .map((k) => `${k.replace(/_/g, " ")}: ${fmt(k, e.before[k])} → ${fmt(k, e.after[k])}`).join("; ");
}
function renderAudit() {
  const f = portal.filter.toLowerCase();
  const rows = portal.audit.filter((e) => !f || `${e.actor} ${e.instance} ${e.account_email ?? ""} ${e.action} ${e.result}`
    .toLowerCase().includes(f));
  $("#audit tbody").replaceChildren(...rows.map((e) => h("tr", {},
    h("td", { title: new Date(e.at * 1000).toLocaleString() }, ago(e.at)),
    h("td", {}, e.actor),
    h("td", {}, h("div", { class: "acct" }, h("span", {}, e.account_email || "—"), h("span", { class: "label" }, e.instance))),
    h("td", {}, e.action),
    h("td", { class: "hide-sm small" }, changeSummary(e)),
    h("td", {}, h("span", { class: `badge ${e.result === "ok" ? "ok" : "warn"}`, title: e.error || "" }, e.result)))));
  $("#audit-empty").hidden = rows.length > 0;
}

// ---- the dialog ----
const dlg = () => $("#dlg");
function openDialog(title, ...body) {
  $("#dlg-title").textContent = title;
  $("#dlg-body").replaceChildren(...body);
  if (!dlg().open) dlg().showModal();
  const first = $("#dlg-body").querySelector("input, select, button");
  if (first) first.focus();
}
function closeDialog() { if (dlg().open) dlg().close(); }
function field(label, input, help) {
  return h("label", { class: "field" }, h("span", {}, label), input, help ? h("small", { class: "muted" }, help) : null);
}
function busy(btn, on, text) {
  if (on) { btn.dataset.text = btn.textContent; btn.textContent = text || "Working…"; btn.disabled = true; }
  else { btn.textContent = btn.dataset.text || btn.textContent; btn.disabled = false; }
}
const msgBox = () => h("p", { class: "msg", role: "status", "aria-live": "polite" });
function say(box, text, kind) { box.textContent = text || ""; box.className = `msg ${kind || ""}`; }

// ---- unlock ----
function unlockDialog(inst) {
  const st = portal.status[inst] || {};
  if (st.pending) return codeStep(inst, st.pending, st.step === "totp");
  const emails = state.data.find((d) => d.instance === inst)?.accounts.map((a) => a.email).filter(Boolean) || [];
  const mine = emails.find((e) => state.me && state.me.email && e.toLowerCase() === state.me.email);
  const email = h("input", { type: "email", required: true, autocomplete: "username", list: "dlg-emails", value: mine || "",
    spellcheck: "false" });
  const list = h("datalist", { id: "dlg-emails" }, emails.map((e) => h("option", { value: e })));
  const btn = h("button", { class: "btn primary", type: "submit" }, "Email me a code");
  const msg = msgBox();
  const form = h("form", { class: "dlg-form" },
    h("p", { class: "muted" }, `Sign in with an account listed as admin in ${inst}'s museum.yaml. Museum emails it a code; `
      + "the session stays on this server and locks itself when idle."),
    field("Ente admin email", email), list, msg, h("div", { class: "row end" }, btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true, "Sending…");
    const r = await api("/api/admin/unlock/send", { instance: inst, email: email.value.trim() });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    await loadPortal();
    codeStep(inst, email.value.trim().toLowerCase(), false);
  });
  openDialog(`Unlock ${inst}`, form);
}
function codeStep(inst, email, totpNeeded) {
  const code = h("input", { inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9]*", maxlength: "6",
    required: !totpNeeded });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const totp = h("input", { inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9]*", maxlength: "6" });
  const totpField = field("Authenticator code", totp, "Two-factor is on for this account.");
  totpField.hidden = !totpNeeded;
  const codeField = field("Code from the email", code, `Sent to ${email}. Valid for 10 minutes.`);
  codeField.hidden = totpNeeded;
  const btn = h("button", { class: "btn primary", type: "submit" }, "Unlock");
  const msg = msgBox();
  const form = h("form", { class: "dlg-form" }, codeField,
    field("Ente password", pw, "Opens the session on this server; it isn't stored."), totpField, msg,
    h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: () => restartUnlock(inst) }, "Start over"), btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true, "Checking… (a few seconds)");
    const r = await api("/api/admin/unlock/verify", { instance: inst, code: code.value.trim(), password: pw.value, totp: totp.value.trim() });
    busy(btn, false);
    if (r.ok && r.data.step === "totp") {
      codeField.hidden = true; totpField.hidden = false; totp.required = true; totp.focus();
      return say(msg, "Enter the code from the authenticator app.", "");
    }
    if (!r.ok) {
      if (/code first|too many/.test(r.data.error || "")) await loadPortal();
      return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    }
    pw.value = "";
    closeDialog();
    await loadPortal({ health: true });
    if (state.open && state.open.instance === inst) openDrawer(state.open);
  });
  openDialog(`Unlock ${inst}`, form);
}
async function restartUnlock(inst) {
  await api("/api/admin/lock", { instance: inst });
  await loadPortal();
  unlockDialog(inst);
}
async function lockInstance(inst) {
  await api("/api/admin/lock", { instance: inst });
  await loadPortal();
  if (state.open && state.open.instance === inst) openDrawer(state.open);
}

// ---- account management in the drawer ----
function manageBox(a) {
  const box = h("div", { class: "manage" }, h("h3", {}, "Manage"));
  if (!unlocked(a.instance)) {
    box.append(h("p", { class: "muted" }, `Unlock ${a.instance} to change this account.`),
      h("button", { class: "btn primary", type: "button", onclick: () => unlockDialog(a.instance) }, `Unlock ${a.instance}`));
    return box;
  }
  const body = h("div", { class: "muted" }, "Loading the account from museum…");
  box.append(body);
  (async () => {
    const r = await api(`/api/admin/account?instance=${encodeURIComponent(a.instance)}&account=${encodeURIComponent(a.label)}`);
    if (!r.ok) { body.replaceChildren(h("p", { class: "msg error" }, r.data.error || `HTTP ${r.status}`)); return; }
    body.className = "";
    body.replaceChildren(...accountPanel(a, r.data));
  })();
  return box;
}
function accountPanel(a, d) {
  const x = d.account;
  const bonus = (x.bonuses || []).map((b) => `${bytes(b.storage)} ${b.type === "ADD_ON_SUPPORT" ? "extra" : b.type.toLowerCase()}`
    + (b.valid_till ? ` until ${day(b.valid_till / 1e6)}` : "")).join(", ");
  const facts = h("dl", { class: "facts tight" },
    h("dt", {}, "Plan"), h("dd", {}, `${bytes(x.plan_storage)} · ${x.product || "—"}`),
    h("dt", {}, "Plan valid until"), h("dd", {}, day(x.plan_expiry && x.plan_expiry / 1e6)),
    h("dt", {}, "Extra storage"), h("dd", {}, bonus || "none"),
    h("dt", {}, "Total / used"), h("dd", {}, `${bytes(x.quota)} / ${bytes(x.used)}`),
    h("dt", {}, "Two-factor"), h("dd", {}, x.two_factor ? "on" : "off"),
    h("dt", {}, "Email code at sign-in"), h("dd", {}, x.email_mfa ? "on" : "off"),
    h("dt", {}, "Family"), h("dd", {}, x.family ? `${x.family.length} members` : "—"),
    h("dt", {}, "Sessions"), h("dd", {}, num(x.sessions)));
  const groups = {};
  for (const act of d.actions) (groups[act.group] ||= []).push(act);
  const out = [facts];
  for (const [g, acts] of Object.entries(groups)) {
    out.push(h("div", { class: `act-group${g === "Danger zone" ? " danger-zone" : ""}` }, h("small", { class: "group" }, g),
      h("div", { class: "acts" }, acts.map((act) => h("button", {
        type: "button", class: `btn ${act.danger !== "normal" ? "danger" : ""}`, disabled: act.unavailable ? true : null,
        title: act.unavailable ? `Not available: ${act.unavailable}` : act.description,
        onclick: () => actionDialog(a, act),
      }, act.title)))));
  }
  return out;
}

// ---- one action: form -> preview -> confirm -> result ----
function input(f) {
  if (f.kind === "bool") {
    const el = h("input", { type: "checkbox" });
    el.checked = !!f.default;
    return el;
  }
  const attrs = { required: true, autocomplete: "off", spellcheck: "false" };
  if (f.kind === "int") Object.assign(attrs, { type: "number", min: f.min, max: f.max, step: "1", inputmode: "numeric" });
  if (f.kind === "date") Object.assign(attrs, { type: "date", min: new Date().toISOString().slice(0, 10), max: "2200-12-31" });
  if (f.kind === "email") Object.assign(attrs, { type: "email" });
  const el = h("input", attrs);
  if (f.default !== undefined && f.default !== null) el.value = String(f.default);
  return el;
}
function readParams(fields, inputs) {
  const p = {};
  fields.forEach((f, i) => {
    const el = inputs[i];
    p[f.name] = f.kind === "bool" ? el.checked : f.kind === "int" ? (el.value === "" ? "" : Number(el.value)) : el.value.trim();
  });
  return p;
}
function actionDialog(a, act) {
  const inputs = act.fields.map(input);
  const msg = msgBox();
  const preview = h("div", { class: "preview" });
  const next = h("button", { class: "btn primary", type: "submit" }, "Preview");
  const form = h("form", { class: "dlg-form" },
    h("p", { class: "muted" }, act.description),
    a ? h("p", {}, h("b", {}, a.email || a.label), h("span", { class: "muted" }, ` · ${a.instance}`)) : null,
    ...act.fields.map((f, i) => field(f.label, inputs[i], f.help)),
    preview, msg, h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"), next));
  const inst = a ? a.instance : act.instance;
  const target = a ? a.label : "";
  let planned = null;
  for (const el of inputs) el.addEventListener("input", () => { planned = null; preview.replaceChildren(); next.textContent = "Preview"; next.className = "btn primary"; next.disabled = false; });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const params = readParams(act.fields, inputs);
    if (!planned) {
      busy(next, true, "Checking…");
      const r = await api("/api/admin/preview", { instance: inst, action: act.name, account: target, params });
      busy(next, false);
      if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
      say(msg, "");
      planned = r.data;
      showPlan(preview, planned, next);
      return;
    }
    const confirmEl = preview.querySelector("input.confirm");
    busy(next, true, "Applying…");
    const r = await api("/api/admin/run", { instance: inst, action: act.name, account: target, params,
      confirm: confirmEl ? confirmEl.value : null });
    busy(next, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    for (const el of form.querySelectorAll("input, button")) el.disabled = true;
    say(msg, "Done. The change is in the audit log.", "ok");
    const close = h("button", { class: "btn primary", type: "button", onclick: closeDialog }, "Close");
    form.querySelector(".row.end").replaceChildren(close);
    close.focus();
    await Promise.all([load(), loadPortal()]);
    if (a && act.name !== "delete-user") {
      const fresh = accounts().find((x) => x.instance === a.instance && x.label === a.label);
      if (fresh) openDrawer(fresh);
    } else if (act.name === "delete-user") closeDrawer();
  });
  openDialog(act.title, form);
}
function showPlan(box, plan, btn) {
  const rows = plan.changes.map((c) => h("tr", { class: c.before === c.after ? "same" : null,
    title: c.before === c.after ? "unchanged" : null }, h("th", { scope: "row" }, c.what), h("td", {}, c.before || ""),
    h("td", { "aria-hidden": "true" }, "→"), h("td", { class: "after" }, c.after)));
  const parts = [h("h4", {}, "This will change"),
    h("table", { class: "changes" }, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Now"), h("th", {}, ""), h("th", {}, "After"))),
      h("tbody", {}, rows))];
  for (const w of plan.warnings || []) parts.push(h("p", { class: "msg warn" }, `⚠️ ${w}`));
  const danger = plan.danger !== "normal";
  btn.textContent = plan.danger === "irreversible" ? "Delete for good" : "Apply";
  btn.className = `btn ${danger ? "danger solid" : "primary"}`;
  if (plan.confirm) {
    const c = h("input", { class: "confirm", autocomplete: "off", spellcheck: "false" });
    btn.disabled = true;
    c.addEventListener("input", () => { btn.disabled = c.value.trim().toLowerCase() !== plan.confirm.toLowerCase(); });
    parts.push(field(`Type “${plan.confirm}” to confirm`, c));
    setTimeout(() => c.focus(), 0);
  }
  box.replaceChildren(...parts);
}

// ---- server tools (instance actions) ----
async function serverActions(inst) {
  const r = await api(`/api/admin/server-actions?instance=${encodeURIComponent(inst)}`);
  if (!r.ok) return openDialog(`${inst}: server tools`, h("p", { class: "msg error" }, r.data.error || `HTTP ${r.status}`));
  const he = portal.health[inst] || {};
  openDialog(`${inst}: server tools`,
    h("dl", { class: "facts tight" }, h("dt", {}, "Admin API"), h("dd", { class: "mono" }, he.api || "—"),
      h("dt", {}, "Version"), h("dd", { class: "mono" }, he.version || "—")),
    h("p", { class: "muted" }, "Server settings (admins, sign-ups, login codes) live in museum.yaml, managed in git."),
    h("div", { class: "acts" }, r.data.actions.map((act) => h("button", {
      class: "btn", type: "button", disabled: act.unavailable ? true : null, title: act.unavailable || act.description,
      onclick: () => actionDialog(null, { ...act, instance: inst }),
    }, act.title))),
    ...r.data.actions.filter((x) => x.unavailable).map((x) => h("p", { class: "muted small" }, `${x.title}: ${x.unavailable}`)));
}

// ---- branding, hero, ticker, user menu ----
const brand = () => (state.cfg && state.cfg.branding) || {};
function applyBranding() {
  const b = brand();
  const title = [b.title, b.badge].filter(Boolean).join(" ") || "Ente admin";
  document.title = title;
  $("#brand-name").textContent = title;
  if (b.favicon) { $("#favicon").href = b.favicon; $("#favicon").type = b.favicon.endsWith(".svg") ? "image/svg+xml" : ""; $("#brand-icon").src = b.favicon; }
  const root = document.documentElement.style;
  if (b.accent) { root.setProperty("--accent", b.accent); root.setProperty("--bar", b.accent); root.setProperty("--brand", b.accent); }
  if (b.accent_2) { root.setProperty("--accent-2", b.accent_2); root.setProperty("--brand-2", b.accent_2); }
  if (b.hero) {
    $("#hero-title").replaceChildren(b.title || "Ente", b.badge ? h("span", { class: "badge-tag" }, b.badge) : "");
    $("#hero-img").src = b.hero;
    $("#hero").hidden = false;
    document.body.classList.add("branded");
  }
  if (b.footer_text || b.footer_image) {
    $("#footer-text").textContent = b.footer_text || "";
    if (b.footer_image) { $("#footer-img").src = b.footer_image; $("#footer-img").hidden = false; }
    $("#footer").hidden = false;
    if (b.footer_text) $("#privacy").hidden = true;
  }
}
function renderHero(rows) {
  if ($("#hero").hidden) return;
  const admin = !state.me || state.me.admin;
  $("#hero-tagline").textContent = admin ? (brand().tagline || "") : "Your Ente accounts at a glance.";
  const live = rows.filter((r) => r.uploading).length;
  const t = Math.max(0, ...state.data.map((d) => d.generated_at || 0));
  const chips = [
    h("span", { class: "chip" }, `👥 ${rows.length} account${rows.length === 1 ? "" : "s"}`),
    h("span", { class: "chip" }, `🗄️ ${state.data.length} server${state.data.length === 1 ? "" : "s"}`),
    h("span", { class: "chip" }, live ? `🚀 ${live} uploading` : "😴 all quiet")];
  if (t) chips.push(h("span", { class: "chip" }, `⏱ ${new Date(t * 1000).toLocaleTimeString()}`));
  $("#chips").replaceChildren(...chips);
}
const shortName = (a) => (a.email ? a.email.split("@")[0] : a.label);
function renderTicker(rows) {
  const sec = $("#ticker");
  if (!state.cfg.modules.includes("ticker") || !rows.length) { sec.hidden = true; return; }
  const items = rows.slice().sort((x, y) => (y.uploading ? 1 : 0) - (x.uploading ? 1 : 0)).map((a) => a.uploading
    ? h("div", { class: "tick live-item" }, "🟢 ", h("b", {}, shortName(a)), " is uploading ",
      h("span", { class: "muted" }, a.recent_uploads ? `${nf.format(a.recent_uploads)} files in the last 5 min` : "right now"))
    : h("div", { class: "tick" }, "📷 ", h("b", {}, shortName(a)), " ",
      h("span", { class: "muted" }, `${num(a.files)} files · ${bytes(a.used)}`)));
  const sig = items.map((i) => i.textContent).join("|");
  if (sec.dataset.sig === sig) return;  // don't restart the scroll on every refresh
  sec.dataset.sig = sig;
  const track = $("#ticker-track");
  track.replaceChildren(...items);
  sec.hidden = false;
  // Scroll only when the list is wider than the strip; then show it twice so the scroll
  // loops seamlessly (the copy is hidden from screen readers)
  const scrolls = track.scrollWidth > sec.clientWidth;
  sec.classList.toggle("still", !scrolls);
  if (scrolls) {
    track.append(...items.map((i) => { const c = i.cloneNode(true); c.setAttribute("aria-hidden", "true"); return c; }));
    track.style.animationDuration = `${Math.max(20, items.length * 6)}s`;
  }
}
function renderMe() {
  const box = $("#me");
  if (!state.me || !state.me.user || state.me.user === "local" || box.dataset.user === state.me.user) return;
  box.dataset.user = state.me.user;
  const n = state.me.name || state.me.user;
  const ini = (n[0] || "?").toUpperCase();
  const menu = h("div", { class: "menu", id: "menu", role: "menu", hidden: true },
    h("div", { class: "menu-head" }, h("span", { class: "avatar big" }, ini),
      h("div", {}, h("b", {}, n), h("small", { class: "muted" }, state.me.user),
        h("span", { class: "role" }, state.me.admin ? "Admin" : "Viewer"))),
    state.cfg.logout_url ? h("a", { class: "menu-item out", role: "menuitem", href: state.cfg.logout_url }, "Log out") : null);
  const btn = h("button", { class: "me-btn", type: "button", "aria-haspopup": "menu", "aria-expanded": "false",
    "aria-controls": "menu", onclick: (e) => { e.stopPropagation(); toggleMenu(); } },
  h("span", { class: "avatar" }, ini), h("span", { class: "me-name" }, n));
  box.replaceChildren(btn, menu);
  box.hidden = false;
}
function toggleMenu(open) {
  const m = $("#menu"), b = $(".me-btn");
  if (!m) return;
  const o = open === undefined ? m.hidden : open;
  m.hidden = !o;
  b.setAttribute("aria-expanded", String(o));
  if (o && m.querySelector(".menu-item")) m.querySelector(".menu-item").focus();
}
function renderEmpty(rows) {
  const empty = !rows.length && state.me && !state.me.admin;
  $("#empty-state").hidden = !empty;
  if (empty) {
    $("#empty-text").textContent = `Ask an admin to share an account with your username (${state.me.user}).`;
    if (brand().empty_image) { $("#empty-img").src = brand().empty_image; $("#empty-img").hidden = false; }
    for (const id of ["#overview", "#activity", "#accounts", "#ticker"]) $(id).hidden = true;
  }
  return empty;
}

// ---- tooltip for chart columns (hover, keyboard focus, tap) ----
const tip = () => $("#tip");
function showTip(el) {
  const t = tip(), r = el.getBoundingClientRect();
  t.replaceChildren(h("b", {}, el.dataset.tipTitle), h("br"), el.dataset.tip);
  t.hidden = false;
  t.style.left = `${Math.min(window.innerWidth - 12, Math.max(12, r.left + r.width / 2))}px`;
  t.style.top = `${r.top}px`;
  for (const x of document.querySelectorAll(".col.sel, .hit.sel")) x.classList.remove("sel");
  el.classList.add("sel");
}
function hideTip() {
  tip().hidden = true;
  for (const x of document.querySelectorAll(".col.sel, .hit.sel")) x.classList.remove("sel");
}
const tipTarget = (e) => e.target.closest && e.target.closest("[data-tip]");

function render() {
  renderTabs(); renderNotes();
  const rows = accounts();
  renderHero(rows); renderMe();
  if (renderEmpty(rows)) return;
  renderOverview(); renderTicker(rows); renderActivity(); renderTable(); renderAdmin();
  if (typeof renderViews === "function") renderViews();
  const t = Math.max(0, ...state.data.map((d) => d.generated_at || 0));
  $("#updated").textContent = t ? `Updated ${new Date(t * 1000).toLocaleTimeString()}` : "";
}

// The login proxy's session ended: it answers with a redirect to its login page. Reload so
// the browser follows it (at most once a minute, in case something else is wrong).
function sessionEnded(r) {
  if (r.type !== "opaqueredirect" && r.status !== 401) return false;
  let last = 0;
  try { last = +sessionStorage.getItem("ente-admin-reload") || 0; } catch { /* ignore */ }
  if (Date.now() - last < 60_000) return false;
  try { sessionStorage.setItem("ente-admin-reload", String(Date.now())); } catch { /* ignore */ }
  location.reload();
  return true;
}

async function load() {
  const btn = $("#refresh");
  btn.classList.add("spin");
  try {
    if (!state.cfg) {
      const c = await fetch("/api/config", { redirect: "manual" });
      if (sessionEnded(c)) return;
      if (!c.ok) throw new Error(`HTTP ${c.status}`);
      state.cfg = await c.json();
      applyBranding();
      clearInterval(state.timer);
      state.timer = setInterval(load, Math.min(300, Math.max(15, state.cfg.refresh_seconds || 60)) * 1000);
    }
    const r = await fetch("/api/data", { redirect: "manual" });
    if (sessionEnded(r)) return;
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    state.data = d.instances;
    state.me = d.me || null;
    state.candidates = d.candidates === undefined ? null : d.candidates;
    if (state.tab !== "all" && !state.data.some((x) => x.instance === state.tab)) state.tab = "all";
    render();
    if (state.cfg.portal && !Object.keys(portal.status).length) loadPortal({ health: true });
    if (typeof refreshView === "function") refreshView();
  } catch (e) {
    $("#notes").replaceChildren(h("div", { class: "note error" }, `Couldn't load data: ${e.message}`));
  } finally {
    btn.classList.remove("spin");
  }
}

// ---- theme (remembered per browser; storage may be unavailable) ----
function setTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  try { t ? localStorage.setItem("ente-admin-theme", t) : localStorage.removeItem("ente-admin-theme"); } catch { /* ignore */ }
}
function toggleTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  setTheme(dark ? "light" : "dark");
}
try { const t = localStorage.getItem("ente-admin-theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* ignore */ }

document.addEventListener("DOMContentLoaded", () => {
  $("#refresh").addEventListener("click", load);
  $("#theme").addEventListener("click", toggleTheme);
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#dlg-close").addEventListener("click", closeDialog);
  $("#audit-filter").addEventListener("input", (e) => { portal.filter = e.target.value; renderAudit(); });
  $("#scrim").addEventListener("click", closeDrawer);
  $("#filter").addEventListener("input", (e) => { state.filter = e.target.value; renderTable(); });
  for (const b of document.querySelectorAll("th button")) b.addEventListener("click", () => {
    const k = b.dataset.sort;
    state.sort = state.sort.key === k ? { key: k, dir: -state.sort.dir } : { key: k, dir: k === "email" ? 1 : -1 };
    renderTable();
  });
  document.addEventListener("mouseover", (e) => { const el = tipTarget(e); if (el) showTip(el); });
  document.addEventListener("mouseout", (e) => {
    if (tipTarget(e) && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest("[data-tip]"))) hideTip();
  });
  document.addEventListener("focusin", (e) => { const el = tipTarget(e); if (el) showTip(el); else hideTip(); });
  document.addEventListener("click", (e) => {
    const el = tipTarget(e);
    if (el && !el.closest("tbody")) showTip(el); else hideTip();
    if (!(e.target.closest && e.target.closest("#menu"))) toggleMenu(false);
  });
  window.addEventListener("scroll", hideTip, { passive: true });
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) || dlg().open;
    if (e.key === "Escape" && dlg().open) return;  // the dialog closes itself
    if (e.key === "Escape") { closeDrawer(); toggleMenu(false); hideTip(); }
    else if (!typing && e.key === "/") { e.preventDefault(); if (typeof setView === "function") setView("accounts"); $("#filter").focus(); }
    else if (!typing && e.key === "r") load();
    else if (!typing && e.key === "t") toggleTheme();
  });
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderActivity, 150); });
  load();
});
