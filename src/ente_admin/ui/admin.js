// ente-admin UI, admin sections: Overview summary, Locker, Notifications, Server (setup,
// web apps, museum.yaml, storage and backups) and the account drawer's extras. Uses the
// helpers in app.js (h, api, dialogs). Same rule: data goes in with textContent only.
"use strict";

const VIEWS = [["overview", "Overview"], ["accounts", "Accounts"], ["locker", "Locker"],
  ["notifications", "Notifications"], ["server", "Server"], ["audit", "Audit log"]];
const adm = { view: "overview", cache: {}, notifyTab: "upcoming", linkFilter: { q: "", app: "all", state: "active" },
  histFilter: { q: "", status: "", kind: "" } };
const enc = encodeURIComponent;
const isAdmin = () => !!(state.me && state.me.admin);

function views() {
  if (!isAdmin() || !state.cfg) return null;
  const v = ["overview", "accounts"];
  if (state.cfg.modules.includes("locker")) v.push("locker");
  if (state.cfg.notifications) v.push("notifications");
  if (state.cfg.portal) v.push("server", "audit");
  return v;
}
function setView(v, push = true) {
  const list = views();
  if (!list) return;
  adm.view = list.includes(v) ? v : "overview";
  if (push && location.hash.slice(1) !== adm.view) history.replaceState(null, "", `#${adm.view}`);
  applyView();
  refreshView();
  window.scrollTo({ top: 0 });
}
function applyView() {
  const list = views();
  const nav = $("#sections");
  const v = list ? adm.view : null;
  for (const el of document.querySelectorAll("[data-views]")) el.classList.toggle("off", !!v && !el.dataset.views.split(" ").includes(v));
  nav.hidden = !list;
  if (!list) return;
  nav.replaceChildren(...VIEWS.filter(([id]) => list.includes(id)).map(([id, label]) => h("a", {
    href: `#${id}`, class: "sec", "aria-current": id === v ? "page" : null,
    onclick: (e) => { e.preventDefault(); setView(id); },
  }, label, id === "notifications" && notifyBadge() ? h("span", { class: "dot", title: "failed emails in the last 24 h" }) : null)));
}
// The instance a per-server view shows: the selected tab, else the first that has data
function inst() {
  if (state.tab !== "all") return state.tab;
  const names = (state.cfg.instances || []).map((i) => i.name);
  const pg = (state.cfg.instances || []).find((i) => i.source === "postgres");
  return pg ? pg.name : names[0];
}
function instNote() {
  const many = (state.cfg.instances || []).length > 1;
  return many ? h("span", { class: "muted small" }, `Server: ${inst()}`, state.tab === "all" ? " (pick another in the tabs above)" : "") : null;
}
function renderViews() { applyView(); if (isAdmin()) renderHome(); }

// cached GETs (per path), shared by the views; `max` seconds old at most
async function get(path, max = 30) {
  const c = adm.cache[path];
  if (c && Date.now() - c.at < max * 1000) return c.r;
  const r = await api(path);
  adm.cache[path] = { at: Date.now(), r };
  return r;
}
function drop(prefix) { for (const k of Object.keys(adm.cache)) if (k.startsWith(prefix)) delete adm.cache[k]; }
function refreshView() {
  if (!isAdmin()) return;
  if (adm.view === "locker") renderLocker();
  else if (adm.view === "notifications") renderNotify();
  else if (adm.view === "server") renderServer();
  else if (adm.view === "overview") loadHome();
}

// ---- small building blocks ----
const panel = (title, ...kids) => h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, title)), ...kids);
function panelWith(title, right, ...kids) {
  return h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, title), right), ...kids);
}
const loading = (what) => h("p", { class: "muted loading" }, `Loading ${what}…`);
const errorBox = (r) => h("p", { class: "msg error" }, (r.data && r.data.error) || `HTTP ${r.status}`);
const badge = (cls, text, title) => h("span", { class: `badge ${cls}`, title: title || null }, text);
function okBadge(ok, yes = "ok", no = "missing", na = "optional") {
  return ok === true ? badge("ok", yes) : ok === false ? badge("warn", no) : badge("", na);
}
function table(head, rows, empty = "Nothing here.") {
  if (!rows.length) return h("p", { class: "empty muted" }, empty);
  return h("div", { class: "table-wrap" }, h("table", { class: "plain" },
    h("thead", {}, h("tr", {}, head.map((c) => (typeof c === "string" ? h("th", { scope: "col" }, c) : c)))),
    h("tbody", {}, rows)));
}
function meter(v, max, label) {
  const pct = max ? Math.min(100, (v / max) * 100) : 0;
  const fill = h("div", { class: `fill${pct > 95 ? " danger" : pct > 80 ? " warn" : ""}` });
  fill.style.width = `${pct}%`;
  return h("div", { class: "usage" }, h("div", { class: "track" }, fill), h("small", {}, label));
}
function search(placeholder, value, oninput) {
  const el = h("input", { type: "search", placeholder, value, autocomplete: "off", spellcheck: "false" });
  el.addEventListener("input", () => oninput(el.value));
  return h("label", { class: "search" }, h("span", { class: "sr-only" }, placeholder), el);
}
function select(label, options, value, onchange) {
  const el = h("select", { "aria-label": label }, options.map(([v, t]) => h("option", { value: v, selected: v === value ? true : null }, t)));
  el.addEventListener("change", () => onchange(el.value));
  return el;
}
function snapOf(name) { return state.data.find((d) => d.instance === name) || {}; }
function feature(name, f) { return (snapOf(name).features || {})[f]; }
function grantNote(f) {
  return h("div", { class: "note" }, h("b", {}, "Not available: "), f.reason || "",
    h("details", {}, h("summary", {}, "Grant to enable it"), h("pre", { class: "code" }, f.grant)));
}
function typedConfirm(word, btn) {
  const c = h("input", { class: "confirm", autocomplete: "off", spellcheck: "false" });
  btn.disabled = true;
  c.addEventListener("input", () => { btn.disabled = c.value.trim().toLowerCase() !== word; });
  return [field(`Type “${word}” to confirm`, c), c];
}
const when = (t) => h("span", { title: t ? new Date(t * 1000).toLocaleString() : "" }, ago(t));
const dueIn = (t) => {
  const d = t - Date.now() / 1000;
  if (d <= 0) return "due now";
  return d < 86400 ? `in ${Math.max(1, Math.round(d / 3600))} h` : `in ${Math.round(d / 86400)} days`;
};
function notifyBadge() {
  const o = adm.cache["/api/admin/notify/overview"];
  if (!o || !o.r.ok) return false;
  return Object.values(o.r.data.instances).some((i) => (i.last_24h.failed || 0) > 0);
}

// ---- Overview: the summary cards ----
async function loadHome() {
  if (!isAdmin()) return;
  const jobs = [];
  if (state.cfg.notifications) jobs.push(get("/api/admin/notify/overview", 60));
  await Promise.all(jobs);
  renderHome();
}
function card(title, view, ...kids) {
  return h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, title),
    view ? h("a", { href: `#${view}`, class: "more", onclick: (e) => { e.preventDefault(); setView(view); } }, "Open →") : null), ...kids);
}
function stat(label, value, cls) { return h("div", { class: `stat ${cls || ""}` }, h("b", {}, value), h("small", {}, label)); }
function renderHome() {
  const sec = $("#home");
  if (!isAdmin()) { sec.hidden = true; return; }
  const rows = accounts();
  const cards = [];
  // Locker
  if (state.cfg.modules.includes("locker") && rows.some((a) => a.locker_items !== null && a.locker_items !== undefined)) {
    const users = rows.filter((a) => a.locker_items > 0);
    const near = rows.filter((a) => a.locker_item_limit && (a.locker_items / a.locker_item_limit > 0.8 || a.locker_bytes / a.locker_byte_limit > 0.8));
    cards.push(card("Locker", "locker", h("div", { class: "stats" }, stat("people use it", num(users.length)),
      stat("items", num(sum(rows, "locker_items"))), stat("stored", bytes(sum(rows, "locker_bytes"))),
      stat("share links", num(sum(rows, "locker_links")))),
    near.length ? h("p", { class: "msg warn" }, `${near.length} account${near.length === 1 ? " is" : "s are"} near a Locker limit`) : null));
  }
  // Notifications
  const no = adm.cache["/api/admin/notify/overview"];
  if (state.cfg.notifications && no && no.r.ok) {
    const items = Object.entries(no.r.data.instances).map(([name, i]) => h("div", { class: "line" },
      h("span", {}, h("b", {}, name), " ", modeBadge(i.mode)),
      h("span", { class: "muted small" }, `${i.last_7d.sent || 0} sent · ${i.last_7d.failed || 0} failed · ${i.due} due · `
        + `${i.scheduled_7d} in 7 days`)));
    cards.push(card("Notifications", "notifications", ...items));
  }
  // Health
  if (state.cfg.portal && Object.keys(portal.health).length) {
    cards.push(card("Health", "server", ...Object.values(portal.health).map((x) => h("div", { class: "line" },
      h("b", {}, x.instance), x.ok ? badge("ok", `online · ${x.ms} ms`) : badge("warn", "unreachable", x.error),
      x.data_error ? badge("warn", "data error", x.data_error) : null))));
  }
  // Recent changes
  if (state.cfg.portal) {
    const recent = portal.audit.slice(0, 5);
    cards.push(card("Recent changes", "audit", recent.length ? h("ul", { class: "recent" }, recent.map((e) => h("li", {},
      h("span", {}, h("b", {}, e.action), " ", h("span", { class: "muted" }, e.account_email || e.instance)),
      h("span", { class: "muted small" }, `${e.actor} · ${ago(e.at)}`, e.result !== "ok" ? " · " : "", e.result !== "ok" ? badge("warn", e.result) : null))))
      : h("p", { class: "muted" }, "Nothing recorded yet.")));
  }
  sec.replaceChildren(...cards);
  sec.hidden = !cards.length;
}

// ---- Locker ----
async function renderLocker() {
  const sec = $("#locker");
  const name = inst();
  sec.hidden = false;
  const rows = accounts().filter((a) => a.instance === name || state.tab === "all" && a.instance === name);
  const f = feature(name, "locker");
  const parts = [];
  const head = h("div", { class: "view-head" }, h("div", {}, h("h1", {}, "Locker"),
    h("p", { class: "muted" }, "Ente Locker stores documents, notes and secrets, end-to-end encrypted. "
      + "This shows counts and sizes only: never names or contents.")), instNote());
  parts.push(head);
  if (!f) parts.push(h("p", { class: "note" }, `${name}: Locker usage needs the postgres source (docs/POSTGRES.md).`));
  else if (!f.ok) parts.push(grantNote(f));
  if (f && f.ok) {
    const users = rows.filter((a) => a.locker_items > 0);
    parts.push(h("div", { class: "kpis" },
      tile("People using it", num(users.length), `of ${rows.length} accounts`),
      tile("Items", num(sum(rows, "locker_items")), `${num(sum(rows, "locker_collections"))} collections`),
      tile("Stored", bytes(sum(rows, "locker_bytes")), "documents and files"),
      tile("Share links", num(sum(rows, "locker_links")), "active Locker links"),
      tile("Trusted contacts", num(sum(rows, "trusted_contacts")), `${num(sum(rows, "legacy_kits"))} legacy kits`)));
    const sorted = rows.slice().sort((x, y) => (y.locker_items || 0) - (x.locker_items || 0));
    parts.push(panel("Usage and limits", table(["Account", "Tier", "Items", "Size", "Links", "Shared", "Inheritance"],
      sorted.map((a) => {
        const tr = h("tr", { tabindex: "0" },
          h("td", {}, h("div", { class: "acct" }, h("span", { class: "email" }, a.email ?? a.label), h("span", { class: "label" }, a.label))),
          h("td", {}, a.locker_tier ? badge(a.locker_tier === "paid" ? "ok" : "", a.locker_tier) : "—"),
          h("td", {}, meter(a.locker_items || 0, a.locker_item_limit, `${num(a.locker_items)} of ${num(a.locker_item_limit)}`)),
          h("td", {}, meter(a.locker_bytes || 0, a.locker_byte_limit, `${bytes(a.locker_bytes)} of ${bytes(a.locker_byte_limit)}`)),
          h("td", { class: "num" }, num(a.locker_links)),
          h("td", { class: "small" }, a.shares_out === null || a.shares_out === undefined ? "—" : `${a.shares_out} out · ${a.shares_in} in`),
          h("td", { class: "small" }, a.trusted_contacts === null || a.trusted_contacts === undefined ? "—"
            : `${a.trusted_contacts} contact${a.trusted_contacts === 1 ? "" : "s"} · trusted by ${a.trusted_by}`
              + (a.legacy_kits ? ` · ${a.legacy_kits} kit${a.legacy_kits === 1 ? "" : "s"}` : "")));
        tr.addEventListener("click", () => openDrawer(a));
        tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openDrawer(a); });
        return tr;
      }), "No accounts."),
    h("p", { class: "muted small" }, "Museum fixes the limits in its code: free accounts get 100 items and 1 GB, paid ones 1,000 items "
      + "and 10 GB. On a self-hosted server an account counts as paid while its plan is valid or it has extra storage; to move "
      + "someone to the paid tier, extend their plan (Accounts → the account → Manage). The dashboard can't change the limits.")));
  }
  const linksBox = h("div", {}, loading("links"));
  const appsBox = h("div", {}, loading("web apps"));
  parts.push(panelWith("Share links", h("span", { class: "muted small" }, "album, Locker and file links"), linksBox));
  parts.push(panelWith("Locker web apps", h("button", { class: "btn", type: "button", onclick: () => configDialog(name, { focus: ["share", "locker"] }) }, "museum.yaml change…"), appsBox));
  sec.replaceChildren(...parts);
  const [lr, ar] = await Promise.all([get(`/api/admin/links?instance=${enc(name)}`), get(`/api/admin/apps?instance=${enc(name)}`, 60)]);
  linksBox.replaceChildren(lr.ok ? linksTable(name, lr.data) : errorBox(lr));
  appsBox.replaceChildren(ar.ok ? lockerApps(ar.data) : errorBox(ar));
}
function tile(label, value, sub) {
  return h("div", { class: "kpi" }, h("span", { class: "label" }, label), h("span", { class: "value" }, value), sub ? h("span", { class: "sub" }, sub) : null);
}
function linksTable(name, d) {
  const f = d.features && d.features.links;
  if (f && !f.ok) return grantNote(f);
  const box = h("div");
  const draw = () => {
    const lf = adm.linkFilter, q = lf.q.toLowerCase();
    const rows = d.links.filter((l) => (lf.app === "all" || (lf.app === "locker" ? l.app === "locker" : l.app !== "locker"))
      && (lf.state === "all" || l.state === lf.state) && (!q || `${l.email ?? ""} ${l.label ?? ""} ${l.id}`.toLowerCase().includes(q)));
    const tools = h("div", { class: "toolbar" }, search("Filter links", lf.q, (v) => { lf.q = v; draw(); }),
      select("App", [["all", "All apps"], ["locker", "Locker"], ["photos", "Photos"]], lf.app, (v) => { lf.app = v; draw(); }),
      select("State", [["active", "Active"], ["all", "Any state"], ["expired", "Expired"], ["revoked", "Revoked"]], lf.state, (v) => { lf.state = v; draw(); }),
      h("span", { class: "muted small" }, `${rows.length} of ${d.links.length}`));
    const body = table(["Owner", "Link", "Created", "Expires", "Options", "State", ""], rows.map((l) => h("tr", {},
      h("td", {}, h("div", { class: "acct" }, h("span", {}, l.email ?? "—"), h("span", { class: "label" }, l.label ?? ""))),
      h("td", {}, `${l.app === "locker" ? "Locker" : "Photos"} ${l.kind === "album" ? (l.app === "locker" ? "collection" : "album") : "file"}`,
        h("span", { class: "label block mono" }, `#${l.id}`)),
      h("td", {}, day(l.created)),
      h("td", {}, l.valid_till ? day(l.valid_till) : "never"),
      h("td", { class: "small" }, [l.device_limit ? `${l.device_limit} devices` : "any device", l.download ? "download" : "no download",
        l.collect ? "collect" : null, l.join ? "join" : null].filter(Boolean).join(" · ")),
      h("td", {}, badge(l.state === "active" ? "ok" : l.state === "revoked" ? "warn" : "", l.state)),
      h("td", {}, l.state === "active" && d.can_revoke ? h("button", { class: "btn danger small", type: "button", onclick: () => revokeDialog(name, l) }, "Revoke") : null))),
    d.links.length ? "No links match." : "No public links on this server.");
    box.replaceChildren(...[tools, body, d.can_revoke ? null : h("p", { class: "muted small" },
      "Revoking needs the optional write role (WRITE_DSN, docs/POSTGRES.md). Owners can always remove their links in the app.")].filter(Boolean));
  };
  draw();
  return box;
}
function revokeDialog(name, l) {
  if (!unlocked(name)) return lockedDialog(name, "revoke a link");
  const btn = h("button", { class: "btn danger solid", type: "submit" }, "Revoke link");
  const [cf, c] = typedConfirm("revoke", btn);
  const msg = msgBox();
  const form = h("form", { class: "dlg-form" },
    h("p", {}, "Anyone with this link loses access within a minute. The owner can make a new link; this one can't be turned back on here."),
    h("dl", { class: "facts tight" }, h("dt", {}, "Owner"), h("dd", {}, l.email ?? l.label ?? "—"), h("dt", {}, "Link"),
      h("dd", {}, `${l.app} ${l.kind} #${l.id}`), h("dt", {}, "Created"), h("dd", {}, day(l.created))),
    cf, msg, h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"), btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true, "Revoking…");
    const r = await api("/api/admin/links/revoke", { instance: name, kind: l.kind, id: l.id, confirm: c.value });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    say(msg, `Revoked. ${r.data.note || ""}`, "ok");
    form.querySelector(".row.end").replaceChildren(h("button", { class: "btn primary", type: "button", onclick: closeDialog }, "Close"));
    drop("/api/admin/links"); load(); loadPortal(); renderLocker();
  });
  openDialog("Revoke a share link", form);
}
function lockedDialog(name, what) {
  openDialog("Unlock first", h("div", { class: "dlg-form" },
    h("p", {}, `To ${what}, unlock ${name} with an Ente admin sign-in first.`),
    h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"),
      h("button", { class: "btn primary", type: "button", onclick: () => unlockDialog(name) }, `Unlock ${name}`))));
}
function probeBadge(p) {
  if (!p) return badge("", "not set");
  return p.ok ? badge("ok", `answers · ${p.ms} ms`) : badge("warn", p.status ? `HTTP ${p.status}` : "no answer", p.error);
}
function lockerApps(d) {
  const pick = d.apps.filter((a) => ["locker", "share", "accounts"].includes(a.app));
  const l = d.locker;
  const lines = [
    h("p", {}, l.web_app && l.answers ? "✅ The Locker web app answers." : l.web_app ? "⚠️ The Locker web app is configured but doesn't answer."
      : "The Locker web app isn't configured here (ENTE_ADMIN_<N>_APPS locker=…). The mobile and desktop apps work without it."),
    h("p", {}, l.share_links_url ? (l.share_app_answers ? `✅ Share links point to ${l.share_links_url}, which answers.`
      : `⚠️ Share links point to ${l.share_links_url}, which doesn't answer.`)
      : d.config_readable ? "⚠️ museum.yaml has no apps.public-locker: Locker share links point to Ente's own share.ente.com, which can't open them. Set it with “museum.yaml change…”."
        : "Share links use museum.yaml's apps.public-locker; mount museum.yaml (MUSEUM_CONFIG_FILE) to check it."),
  ];
  return h("div", {}, ...lines, table(["App", "For", "URL", "Status"], pick.map((a) => h("tr", {},
    h("td", {}, h("b", {}, a.app)), h("td", { class: "small" }, a.about), h("td", { class: "mono small" }, a.url || "—"),
    h("td", {}, probeBadge(a.probe))))));
}

// ---- museum.yaml change: preview diff -> operator applies -> verify ----
async function configDialog(name, { focus = [] } = {}) {
  const [cr, ar] = await Promise.all([get(`/api/admin/config?instance=${enc(name)}`, 10), get(`/api/admin/apps?instance=${enc(name)}`, 60)]);
  if (!cr.ok) return openDialog("museum.yaml", errorBox(cr));
  const cfg = cr.data;
  const urls = Object.fromEntries((ar.ok ? ar.data.apps : []).map((a) => [a.app, a.url]));
  const names = Object.keys(cfg.yaml_apps);
  const order = [...focus.filter((x) => names.includes(x)), ...names.filter((x) => !focus.includes(x))];
  const inputs = {};
  const appFields = order.map((app) => {
    const now = cfg.readable ? cfg.apps[app] : undefined;
    const el = h("input", { type: "url", placeholder: urls[app] || "https://…", value: "", autocomplete: "off", spellcheck: "false" });
    inputs[app] = el;
    return field(`apps.${cfg.yaml_apps[app]}`, el, now ? `now: ${now}` : cfg.readable ? "not set" : (urls[app] ? `suggested: ${urls[app]}` : null));
  });
  const admins = h("input", { placeholder: "account ids, comma separated", autocomplete: "off", spellcheck: "false",
    value: cfg.readable ? cfg.admins.join(", ") : "" });
  const reg = select("disable-registration", [["", "leave as is"], ["true", "true: no new sign-ups"], ["false", "false: anyone can sign up"]], "", () => {});
  const out = h("div", { class: "preview" });
  const msg = msgBox();
  const btn = h("button", { class: "btn primary", type: "submit" }, "Preview the change");
  const readReq = () => {
    const apps = {};
    for (const [k, el] of Object.entries(inputs)) if (el.value.trim()) apps[k] = el.value.trim();
    const a = admins.value.trim();
    const curAdmins = cfg.readable ? cfg.admins.join(", ") : "";
    return { instance: name, apps, admins: a && a !== curAdmins ? a.split(/[\s,]+/).filter(Boolean) : null,
      disable_registration: reg.value === "" ? null : reg.value === "true" };
  };
  const form = h("form", { class: "dlg-form" },
    h("p", { class: "muted" }, "The dashboard never writes museum.yaml. It builds the change, shows a diff (secrets hidden), "
      + "you apply it where you keep museum.yaml and restart museum, then Verify checks the result."),
    h("details", { open: true }, h("summary", {}, "Web apps"), h("div", { class: "grid2" }, ...appFields)),
    h("details", {}, h("summary", {}, "Admins and sign-ups"), field("internal.admins", admins,
      cfg.readable ? "the accounts that may use museum's admin API" : "museum.yaml isn't mounted: enter the full list"),
    field("internal.disable-registration", reg)),
    out, msg, h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Close"), btn));
  let step = "preview", sent = null;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (step === "preview") {
      sent = readReq();
      busy(btn, true, "Building…");
      const r = await api("/api/admin/config/propose", sent);
      busy(btn, false);
      if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
      say(msg, "");
      const d = r.data;
      out.replaceChildren(...[h("h4", {}, d.diff ? "Diff against museum.yaml" : "Add this to museum.yaml"),
        h("pre", { class: "code diff" }, ...(d.diff || d.snippet).split("\n").map((ln) => h("span", {
          class: ln.startsWith("+") && !ln.startsWith("+++") ? "add" : ln.startsWith("-") && !ln.startsWith("---") ? "del" : null }, `${ln}\n`))),
        d.diff ? h("details", {}, h("summary", {}, "Just the snippet"), h("pre", { class: "code" }, d.snippet)) : null,
        h("ol", { class: "steps" }, d.steps.map((s) => h("li", {}, s)))].filter(Boolean));
      step = "verify";
      btn.textContent = "I've applied it: verify";
      return;
    }
    busy(btn, true, "Checking…");
    const r = await api("/api/admin/config/verify", sent);
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    out.append(h("h4", {}, r.data.ok ? "✅ Verified" : "⚠️ Not there yet"), h("ul", { class: "checks" }, r.data.checks.map((c) => h("li", {},
      c.ok === true ? "✅ " : c.ok === false ? "❌ " : "• ", c.check, c.detail ? h("span", { class: "muted" }, ` (${c.detail})`) : null))));
    if (!r.data.ok) say(msg, "If you just changed it: secrets and config maps can take a minute to update, and museum must be restarted.", "warn");
    drop("/api/admin/apps"); drop("/api/admin/config");
  });
  openDialog(`${name}: museum.yaml change`, form);
}

// ---- Notifications ----
const MODE_TEXT = { off: "off", shadow: "shadow: recorded, not sent", send: "sending" };
function modeBadge(m) { return badge(m === "send" ? "ok" : m === "shadow" ? "info" : "", MODE_TEXT[m] || m); }
const KIND_TEXT = { inactivity: "Backup reminder", share: "Album shared", uploads: "New photos", test: "Test" };
function kindLabel(e) { return e.kind === "inactivity" && e.level ? `${KIND_TEXT.inactivity} · ${e.level} days` : KIND_TEXT[e.kind] || e.kind; }
function statusBadge(s, title) {
  return badge({ sent: "ok", failed: "warn", shadow: "info", suppressed: "" }[s] || "", s === "shadow" ? "shadow (not sent)" : s, title);
}

async function renderNotify() {
  const sec = $("#notify");
  sec.hidden = false;
  const name = inst();
  const top = h("div", { class: "view-head" }, h("div", {}, h("h1", {}, "Notifications"),
    h("p", { class: "muted" }, "Emails self-hosted Ente doesn't send: backup reminders after 15 and 30 days without activity, "
      + "albums shared with someone, and new photos in shared albums.")), instNote());
  const head = h("div", {}, loading("notifications"));
  const tabs = h("div", { class: "subtabs", role: "tablist" });
  const body = h("div", { class: "panel" }, loading(""));
  sec.replaceChildren(top, head, tabs, body);
  const [ov, sm] = await Promise.all([get("/api/admin/notify/overview", 15), get(`/api/admin/notify/smtp?instance=${enc(name)}`, 60)]);
  if (!ov.ok) { head.replaceChildren(errorBox(ov)); body.replaceChildren(); return; }
  const o = ov.data.instances[name], st = ov.data.settings.instances[name];
  if (!o || !st.supported) { head.replaceChildren(h("p", { class: "note" }, `${name}: notifications need the postgres source.`)); body.replaceChildren(); return; }
  head.replaceChildren(notifyHead(name, o, st, sm));
  const TABS = [["upcoming", "Upcoming"], ["history", "History"], ["suppressed", "Suppressed"], ["templates", "Templates"], ["settings", "Settings"]];
  tabs.replaceChildren(...TABS.map(([id, label]) => h("button", { type: "button", role: "tab", "aria-selected": String(adm.notifyTab === id),
    onclick: () => { adm.notifyTab = id; renderNotify(); } }, label)));
  body.replaceChildren(loading(adm.notifyTab));
  const fn = { upcoming: notifyUpcoming, history: notifyHistory, suppressed: notifySuppressed, templates: notifyTemplates, settings: notifySettings }[adm.notifyTab];
  body.replaceChildren(...[await fn(name, ov.data)].flat());
}
function notifyHead(name, o, st, sm) {
  const health = sm.ok ? sm.data.health : null;
  const smtp = sm.ok ? sm.data.smtp : st.smtp;
  const mode = h("div", { class: "seg", role: "group", "aria-label": "Mode" }, ["off", "shadow", "send"].map((m) => h("button", {
    type: "button", "aria-pressed": String(o.mode === m), onclick: () => (o.mode === m ? null : modeDialog(name, o.mode, m)) }, m)));
  const lr = o.last_run;
  return h("div", { class: "kpis notify-kpis" },
    h("div", { class: "kpi wide-sm" }, h("span", { class: "label" }, "Mode"), mode, h("span", { class: "sub" },
      o.mode === "shadow" ? "works everything out and records it, sends nothing" : o.mode === "send" ? "emails go out" : "nothing runs",
      st.config_mode !== o.mode ? ` (config says ${st.config_mode})` : ""),
    lr ? h("span", { class: "sub" }, lr.ok ? `checked ${ago(lr.at)}` : h("span", { class: "msg error" }, `last check failed: ${lr.error}`)) : null),
    tile("Sent, 7 days", num(o.last_7d.sent || 0), `${o.last_24h.sent || 0} in 24 h${o.last_7d.shadow ? ` · ${o.last_7d.shadow} shadow` : ""}`),
    h("div", { class: `kpi${(o.last_7d.failed || 0) ? " bad" : ""}` }, h("span", { class: "label" }, "Failed, 7 days"),
      h("span", { class: "value" }, num(o.last_7d.failed || 0)), h("span", { class: "sub" }, "retried every minute")),
    tile("Reminders", num(o.due), `due now · ${o.scheduled_7d} in 7 days · ${o.suppressed} suppressed`),
    h("div", { class: "kpi wide-sm" }, h("span", { class: "label" }, "Email (SMTP)"),
      h("span", { class: "value small-value" }, smtp.configured ? `${smtp.host}:${smtp.port}` : "not set"),
      h("span", { class: "sub" }, !smtp.configured ? "ENTE_ADMIN_SMTP_HOST and _FROM" : health ? (health.ok ? badge("ok", `answers · ${health.ms} ms`)
        : badge("warn", "fails", health.error)) : "", smtp.configured ? ` ${smtp.security}${smtp.auth ? " · login" : ""} · from ${smtp.from}` : ""),
      h("div", { class: "row" }, h("button", { class: "btn small", type: "button", disabled: smtp.configured ? null : true,
        onclick: async (e) => { busy(e.target, true, "Checking…"); drop("/api/admin/notify/smtp"); await get(`/api/admin/notify/smtp?instance=${enc(name)}&force=1`, 0); renderNotify(); } }, "Check"),
      h("button", { class: "btn small primary", type: "button", disabled: smtp.configured ? null : true, onclick: () => testDialog(name, "test") }, "Send a test email"))),
  );
}
function modeDialog(name, from, to) {
  const btn = h("button", { class: `btn ${to === "send" ? "danger solid" : "primary"}`, type: "submit" }, `Switch to ${to}`);
  const msg = msgBox();
  const extra = [];
  let c = null;
  if (to === "send") {
    const [cf, el] = typedConfirm("send emails", btn);
    c = el;
    extra.push(h("p", { class: "msg warn" }, "⚠️ Real emails go to the people on this server from the next check (about a minute). "
      + "If another tool still sends these emails, turn it off first, or import its state (Settings) so nobody gets them twice."), cf);
  }
  const form = h("form", { class: "dlg-form" }, h("p", {}, `Notifications for ${name}: ${from} → ${to}.`),
    h("p", { class: "muted" }, to === "shadow" ? "Shadow works everything out and records it in History as “shadow”, without sending."
      : to === "off" ? "Nothing is checked or sent. History stays." : ""), ...extra, msg,
    h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"), btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true);
    const r = await api("/api/admin/notify/settings", { instance: name, mode: to, confirm: c ? c.value : null });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    closeDialog(); drop("/api/admin/notify"); loadPortal(); renderNotify();
  });
  openDialog("Notification mode", form);
}

async function notifyUpcoming(name) {
  const r = await get(`/api/admin/notify/upcoming?instance=${enc(name)}`, 30);
  if (!r.ok) return errorBox(r);
  const d = r.data;
  const q = h("div", { class: "toolbar" }, h("span", { class: "muted small" },
    `Reminders after ${d.thresholds.join(" and ")} days without activity, sent between ${d.window.from}:00 and ${d.window.until}:00 `
    + `(${d.in_window ? "now inside the window" : "now outside the window: due ones wait"}). Using the account again resets them.`));
  const STATE = { due: ["warn", "due"], scheduled: ["", "scheduled"], done: ["ok", "all sent"], suppressed: ["", "suppressed"] };
  return [q, table(["Account", "Last active", "Sent this absence", "Next", "", ""], d.upcoming.map((u) => h("tr", {},
    h("td", {}, h("div", { class: "acct" }, h("span", { class: "email" }, u.email ?? "—"), h("span", { class: "label" }, u.account))),
    h("td", {}, when(u.last_active), h("small", { class: "muted block" }, `${Math.floor(u.days_inactive)} days`)),
    h("td", {}, u.sent.length ? h("div", { class: "badges" }, u.sent.map((x) => badge("ok", `${x} d`))) : h("span", { class: "na" }, "—")),
    h("td", {}, u.next ? [`${u.next} days`, h("small", { class: "muted block" }, u.state === "suppressed" ? "suppressed"
      : `${dueIn(u.due_at)} · ${day(u.due_at)}`)] : "—"),
    h("td", {}, badge(...STATE[u.state]), u.suppressed ? h("small", { class: "muted block" },
      u.suppressed.until ? `until ${day(u.suppressed.until)}` : "until removed") : null),
    h("td", { class: "acts-cell" }, h("button", { class: "btn small", type: "button", onclick: () => sendDialog(name, u, d.thresholds) }, "Send…"),
      u.state === "suppressed" ? h("button", { class: "btn small", type: "button", onclick: () => unsuppress(name, u.account, u.suppressed.kind) }, "Unsuppress")
        : h("button", { class: "btn small", type: "button", onclick: () => suppressDialog(name, u.account, u.email) }, "Suppress")))),
  "No accounts.")];
}
function sendDialog(name, u, thresholds) {
  const lvl = select("Reminder", thresholds.map((t) => [String(t), `${t}-day reminder`]), String(u.next || thresholds[0]), () => {});
  const frame = h("div", { class: "preview-box" });
  const msg = msgBox();
  const btn = h("button", { class: "btn primary", type: "submit" }, "Send now");
  const showPreview = async () => {
    const t = `inactivity-${lvl.value}`;
    const r = await api("/api/admin/notify/preview", { instance: name, template: t, account: u.account, level: Number(lvl.value) });
    frame.replaceChildren(r.ok ? previewFrame(r.data) : errorBox(r));
  };
  lvl.addEventListener("change", showPreview);
  const form = h("form", { class: "dlg-form" },
    h("p", {}, h("b", {}, u.email || u.account), h("span", { class: "muted" }, ` · last active ${ago(u.last_active)}`)),
    field("Which reminder", lvl, "Counts as that reminder for this absence, so the schedule won't send it again."),
    frame, msg, h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"), btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true, "Sending…");
    const r = await api("/api/admin/notify/send", { instance: name, account: u.account, level: Number(lvl.value) });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    const ev = r.data.event;
    say(msg, ev.status === "sent" ? "Sent." : ev.status === "shadow" ? "Recorded in shadow mode (not sent)." : `Failed: ${ev.error}`, ev.status === "failed" ? "error" : "ok");
    form.querySelector(".row.end").replaceChildren(h("button", { class: "btn primary", type: "button", onclick: closeDialog }, "Close"));
    drop("/api/admin/notify"); loadPortal(); renderNotify();
  });
  openDialog("Send a backup reminder", form);
  showPreview();
}
function previewFrame(p) {
  const f = h("iframe", { class: "mail-frame", sandbox: "", title: "Email preview", src: `/api/admin/notify/preview.html?id=${enc(p.id)}`, referrerpolicy: "no-referrer" });
  return h("div", { class: "mail" }, h("div", { class: "mail-head" }, h("div", {}, h("small", { class: "muted" }, "To "), p.to),
    h("div", {}, h("small", { class: "muted" }, "Subject "), h("b", {}, p.subject))), f,
  h("details", {}, h("summary", {}, "Plain-text version"), h("pre", { class: "code" }, p.text)));
}
function suppressDialog(name, account, email) {
  const kind = select("Kind", [["inactivity", "Backup reminders"], ["share", "Album shared"], ["uploads", "New photos"], ["*", "All notifications"]], "inactivity", () => {});
  const days = h("input", { type: "number", min: "1", max: "3650", placeholder: "until removed", inputmode: "numeric" });
  const reason = h("input", { maxlength: "200", placeholder: "optional", autocomplete: "off" });
  const msg = msgBox();
  const btn = h("button", { class: "btn primary", type: "submit" }, "Suppress");
  const form = h("form", { class: "dlg-form" }, h("p", {}, h("b", {}, email || account)), field("Don't send", kind),
    field("For how many days", days, "Empty: until you remove it."), field("Reason", reason), msg,
    h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"), btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true);
    const r = await api("/api/admin/notify/suppress", { instance: name, account, kind: kind.value, days: days.value ? Number(days.value) : null, reason: reason.value });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    closeDialog(); drop("/api/admin/notify"); loadPortal();
    if (adm.view === "notifications") renderNotify(); else if (state.open) openDrawer(state.open);
  });
  openDialog("Suppress notifications", form);
}
async function unsuppress(name, account, kind) {
  const r = await api("/api/admin/notify/unsuppress", { instance: name, account, kind });
  if (!r.ok) alert(r.data.error || `HTTP ${r.status}`);
  drop("/api/admin/notify"); loadPortal();
  if (adm.view === "notifications") renderNotify(); else if (state.open) openDrawer(state.open);
}

async function notifyHistory(name) {
  const r = await get(`/api/admin/notify/events?instance=${enc(name)}&limit=1000`, 15);
  if (!r.ok) return errorBox(r);
  const box = h("div");
  const hf = adm.histFilter;
  const draw = () => {
    const q = hf.q.toLowerCase();
    const rows = r.data.events.filter((e) => (!hf.status || e.status === hf.status) && (!hf.kind || e.kind === hf.kind)
      && (!q || `${e.email ?? ""} ${e.account ?? ""} ${e.subject ?? ""} ${e.actor ?? ""}`.toLowerCase().includes(q)));
    const tools = h("div", { class: "toolbar" }, search("Filter", hf.q, (v) => { hf.q = v; draw(); }),
      select("Status", [["", "Any status"], ["sent", "Sent"], ["failed", "Failed"], ["shadow", "Shadow"], ["suppressed", "Suppressed"]], hf.status, (v) => { hf.status = v; draw(); }),
      select("Kind", [["", "Any kind"], ["inactivity", "Backup reminders"], ["share", "Album shared"], ["uploads", "New photos"], ["test", "Tests"]], hf.kind, (v) => { hf.kind = v; draw(); }),
      h("span", { class: "muted small" }, `${rows.length} of ${r.data.events.length}`));
    box.replaceChildren(tools, table(["When", "To", "What", "How", "Result", ""], rows.map((e) => h("tr", {},
      h("td", {}, when(e.at)),
      h("td", {}, h("div", { class: "acct" }, h("span", {}, e.email ?? "—"), h("span", { class: "label" }, e.account ?? ""))),
      h("td", {}, kindLabel(e), h("small", { class: "muted block" }, e.subject || "")),
      h("td", { class: "small" }, e.trigger === "auto" ? "scheduled" : `${e.trigger} by ${e.actor}`, e.ref ? ` (of #${e.ref})` : ""),
      h("td", {}, statusBadge(e.status, e.error), e.error ? h("small", { class: "msg error block" }, e.error) : null),
      h("td", {}, e.trigger !== "test" && e.status !== "suppressed" ? h("button", { class: "btn small", type: "button", onclick: () => resendDialog(e) }, "Resend") : null))),
    r.data.events.length ? "Nothing matches." : "Nothing sent yet."));
  };
  draw();
  return box;
}
function resendDialog(e) {
  const msg = msgBox();
  const btn = h("button", { class: "btn primary", type: "submit" }, "Resend");
  const form = h("form", { class: "dlg-form" }, h("p", {}, `Send “${e.subject}” again to ${e.email || e.account}?`),
    h("p", { class: "muted" }, "Same template and values as the original, to the account's current address. In shadow mode it's only recorded."),
    msg, h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"), btn));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    busy(btn, true, "Sending…");
    const r = await api("/api/admin/notify/resend", { event: e.id });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    const x = r.data.event;
    say(msg, x.status === "sent" ? "Sent." : x.status === "shadow" ? "Recorded (shadow mode)." : `Failed: ${x.error}`, x.status === "failed" ? "error" : "ok");
    form.querySelector(".row.end").replaceChildren(h("button", { class: "btn primary", type: "button", onclick: closeDialog }, "Close"));
    drop("/api/admin/notify"); loadPortal(); renderNotify();
  });
  openDialog("Resend", form);
}
async function notifySuppressed(name) {
  const r = await get(`/api/admin/notify/suppressions?instance=${enc(name)}`, 10);
  if (!r.ok) return errorBox(r);
  const emails = Object.fromEntries(accounts().map((a) => [a.label, a.email]));
  return [h("p", { class: "muted small" }, "Accounts that don't get some notifications. Add one from Upcoming or the account's panel."),
    table(["Account", "What", "Until", "Reason", "By", ""], r.data.suppressions.map((s) => h("tr", {},
      h("td", {}, h("div", { class: "acct" }, h("span", {}, emails[s.account] ?? "—"), h("span", { class: "label" }, s.account))),
      h("td", {}, s.kind === "*" ? "everything" : KIND_TEXT[s.kind]),
      h("td", {}, s.until ? (s.until < Date.now() / 1000 ? badge("", "expired") : day(s.until)) : "until removed"),
      h("td", { class: "small" }, s.reason || "—"), h("td", { class: "small" }, `${s.actor} · ${ago(s.at)}`),
      h("td", {}, h("button", { class: "btn small", type: "button", onclick: () => unsuppress(name, s.account, s.kind) }, "Remove")))),
    "Nobody is suppressed.")];
}

async function notifyTemplates(name) {
  const r = await get("/api/admin/notify/templates", 10);
  if (!r.ok) return errorBox(r);
  const ORIGIN = { edited: ["ok", "edited here"], file: ["info", "from the templates folder"], "built-in": ["", "built-in"] };
  return [h("p", { class: "muted small" }, "Edited templates override the templates folder (ENTE_ADMIN_TEMPLATES_DIR), which overrides "
    + "the built-in ones. Reminders use inactivity-<days> if it exists, then inactivity-last for the last one, then inactivity."),
  table(["Template", "Subject", "Source", ""], r.data.templates.map((t) => h("tr", {},
    h("td", {}, h("b", { class: "mono" }, t.id), h("small", { class: "muted block" }, KIND_TEXT[t.kind] || t.kind)),
    h("td", { class: "small" }, t.subject),
    h("td", {}, badge(...(ORIGIN[t.origin] || ["", t.origin]))),
    h("td", { class: "acts-cell" }, h("button", { class: "btn small", type: "button", onclick: () => templateDialog(name, t) }, "Edit"),
      h("button", { class: "btn small", type: "button", onclick: () => testDialog(name, t.id) }, "Test"))))),
  h("div", { class: "row" }, h("button", { class: "btn", type: "button", onclick: () => newTemplateDialog(name, r.data.templates) }, "Add a template for one threshold…"))];
}
function newTemplateDialog(name, list) {
  const days = h("input", { type: "number", min: "1", max: "365", inputmode: "numeric", required: true });
  const btn = h("button", { class: "btn primary", type: "submit" }, "Continue");
  const form = h("form", { class: "dlg-form" }, h("p", { class: "muted" }, "A separate reminder text for one threshold, e.g. a stronger one at 30 days."),
    field("Days", days), h("div", { class: "row end" }, btn));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const id = `inactivity-${days.value}`;
    const base = list.find((t) => t.id === id) || list.find((t) => t.id === "inactivity");
    templateDialog(name, { ...base, id, origin: "new" });
  });
  openDialog("New reminder template", form);
}
function templateDialog(name, t) {
  const subj = h("input", { value: t.subject, maxlength: "250", spellcheck: "true" });
  const text = h("textarea", { rows: "7", spellcheck: "true" }, t.text);
  const html = h("textarea", { rows: "10", class: "mono", spellcheck: "false" }, t.html);
  const vars = h("details", {}, h("summary", {}, "Placeholders"), h("dl", { class: "vars" },
    Object.entries(t.variables || {}).flatMap(([k, v]) => [h("dt", { class: "mono" }, `\${${k}}`), h("dd", {}, v)])),
  h("p", { class: "muted small" }, "Images: src=\"cid:logo.png\" embeds that file from the templates folder; cid:random:art-*.png picks one at random."));
  const frame = h("div", { class: "preview-box" });
  const msg = msgBox();
  const save = h("button", { class: "btn primary", type: "submit" }, "Save");
  const preview = h("button", { class: "btn", type: "button" }, "Preview");
  const reset = t.origin === "edited" ? h("button", { class: "btn danger", type: "button" }, "Reset to default") : null;
  const draft = () => ({ subject: subj.value, text: text.value, html: html.value });
  preview.addEventListener("click", async () => {
    busy(preview, true, "Rendering…");
    const r = await api("/api/admin/notify/preview", { instance: name, template: t.id, draft: draft() });
    busy(preview, false);
    frame.replaceChildren(r.ok ? previewFrame(r.data) : errorBox(r));
  });
  if (reset) reset.addEventListener("click", async () => {
    if (!confirm(`Remove the edited ${t.id} and go back to the folder or built-in version?`)) return;
    const r = await api("/api/admin/notify/template-reset", { id: t.id });
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    closeDialog(); drop("/api/admin/notify/templates"); loadPortal(); renderNotify();
  });
  const form = h("form", { class: "dlg-form" }, field("Subject", subj), field("Plain text", text), field("HTML", html,
    "Optional. Without <html> it goes inside the built-in layout. Values are escaped."), vars, frame, msg,
  h("div", { class: "row end" }, reset, preview, save));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(save, true, "Saving…");
    const r = await api("/api/admin/notify/template", { id: t.id, ...draft() });
    busy(save, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    say(msg, "Saved. It's used from the next email on.", "ok");
    drop("/api/admin/notify/templates"); loadPortal();
  });
  openDialog(`Template ${t.id}`, form);
  $("#dlg").classList.add("wide");
  $("#dlg").addEventListener("close", () => $("#dlg").classList.remove("wide"), { once: true });
}
function testDialog(name, tid) {
  const to = h("input", { type: "email", required: true, value: (state.me && state.me.email) || "", autocomplete: "email", spellcheck: "false" });
  const acct = select("Account data", [["", "sample values"], ...accounts().filter((a) => a.instance === name).map((a) => [a.label, a.email || a.label])], "", () => {});
  const msg = msgBox();
  const btn = h("button", { class: "btn primary", type: "submit" }, "Send test");
  const form = h("form", { class: "dlg-form" }, h("p", { class: "muted" }, `Sends ${tid} to the address below only, whatever the mode. Recorded in History as a test.`),
    field("Send to", to), tid !== "test" ? field("Fill it with", acct, "An account's real values (its address isn't used), or samples.") : null, msg,
    h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"), btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true, "Sending…");
    const r = await api("/api/admin/notify/test", { instance: name, template: tid, to: to.value.trim(), account: acct.value || null });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    const ev = r.data.event;
    say(msg, ev.status === "sent" ? `Sent to ${ev.email}.` : `Failed: ${ev.error}`, ev.status === "sent" ? "ok" : "error");
    drop("/api/admin/notify"); loadPortal();
  });
  openDialog("Send a test email", form);
}
async function notifySettings(name, ov) {
  const st = ov.settings;
  const thr = h("input", { value: st.thresholds.join(", "), autocomplete: "off", spellcheck: "false" });
  const from = h("input", { type: "number", min: "0", max: "24", value: String(st.window.from) });
  const until = h("input", { type: "number", min: "0", max: "24", value: String(st.window.until) });
  const kinds = Object.fromEntries(Object.entries(KIND_TEXT).filter(([k]) => k !== "test").map(([k]) => {
    const el = h("input", { type: "checkbox" }); el.checked = !!st.kinds[k]; return [k, el];
  }));
  const server = h("input", { value: st.instances[name].server_name, maxlength: "60" });
  const msg = msgBox();
  const btn = h("button", { class: "btn primary", type: "submit" }, "Save");
  const form = h("form", { class: "dlg-form settings" },
    field("Reminder thresholds (days)", thr, "Comma separated, e.g. 15, 30. Applies to every server."),
    h("div", { class: "grid2" }, field("Send reminders from (hour)", from), field("until (hour)", until)),
    h("fieldset", { class: "checks-row" }, h("legend", {}, "Send"), Object.entries(kinds).map(([k, el]) => h("label", {}, el, ` ${KIND_TEXT[k]}`))),
    field(`Server name in emails (${name})`, server),
    msg, h("div", { class: "row end" }, btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const t = thr.value.split(/[\s,]+/).filter(Boolean).map(Number);
    busy(btn, true, "Saving…");
    const r = await api("/api/admin/notify/settings", { instance: name, thresholds: t, window: { from: Number(from.value), until: Number(until.value) },
      kinds: Object.fromEntries(Object.entries(kinds).map(([k, el]) => [k, el.checked])), server_name: server.value.trim() });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    say(msg, "Saved.", "ok");
    drop("/api/admin/notify"); loadPortal();
  });
  const file = h("input", { type: "file", accept: "application/json,.json" });
  const imsg = msgBox();
  file.addEventListener("change", async () => {
    const f = file.files[0];
    if (!f) return;
    let data;
    try { data = JSON.parse(await f.text()); } catch { return say(imsg, "That isn't JSON.", "error"); }
    if (!confirm(`Replace ${name}'s notification state (who was reminded, which shares were announced) with this file's?`)) return;
    const r = await api("/api/admin/notify/import", { instance: name, data });
    say(imsg, r.ok ? `Imported: ${Object.entries(r.data.imported).map(([k, v]) => `${k} ${v === true ? "✓" : v}`).join(", ")}` : r.data.error || `HTTP ${r.status}`, r.ok ? "ok" : "error");
    drop("/api/admin/notify"); loadPortal();
  });
  return [form, h("div", { class: "import" }, h("h3", {}, "Take over from ente-notify"),
    h("p", { class: "muted small" }, "If a script already sends these emails, import its notified.json so nobody gets a reminder twice, then switch it off and this to “send”."),
    field("notified.json", file), imsg)];
}

// ---- Server: setup, web apps, storage and backups ----
async function renderServer() {
  const sec = $("#server");
  sec.hidden = false;
  const name = inst();
  const setupBox = h("div", {}, loading("the checklist"));
  const appsBox = h("div", {}, loading("web apps"));
  const storeBox = h("div", {}, loading("storage"));
  sec.replaceChildren(
    h("div", { class: "view-head" }, h("div", {}, h("h2", {}, `${name}: setup and health`)), instNote()),
    panelWith("Setup checklist", h("span", { class: "muted small" }, "what the dashboard can reach, and how to add the rest"), setupBox),
    panelWith("Web apps", h("button", { class: "btn", type: "button", onclick: () => configDialog(name) }, "museum.yaml change…"), appsBox),
    panelWith("Storage and backups", h("button", { class: "btn", type: "button", onclick: () => backupDialog(name) }, "Record a backup check…"), storeBox));
  const [s, a, st] = await Promise.all([get(`/api/admin/setup?instance=${enc(name)}`, 30), get(`/api/admin/apps?instance=${enc(name)}`, 60),
    get(`/api/admin/storage?instance=${enc(name)}`, 120)]);
  setupBox.replaceChildren(s.ok ? h("ul", { class: "checklist" }, s.data.items.map((i) => h("li", {},
    h("div", { class: "row" }, okBadge(i.ok), h("b", {}, i.item), i.detail ? h("span", { class: "muted small" }, i.detail) : null),
    i.fix ? h("details", {}, h("summary", {}, "How to enable it"), h("pre", { class: "code" }, i.fix)) : null))) : errorBox(s));
  appsBox.replaceChildren(a.ok ? table(["App", "For", "URL", "In museum.yaml", "Status"], a.data.apps.map((x) => h("tr", {},
    h("td", {}, h("b", {}, x.app)), h("td", { class: "small" }, x.about), h("td", { class: "mono small" }, x.url || "—"),
    h("td", { class: "mono small" }, x.yaml_key ? (a.data.config_readable ? (x.in_museum_yaml || "not set") : "?") : "—"),
    h("td", {}, probeBadge(x.probe))))) : errorBox(a));
  storeBox.replaceChildren(st.ok ? storageView(st.data) : errorBox(st));
}
function storageView(d) {
  const f = d.features && d.features.storage;
  if (f && !f.ok) return grantNote(f);
  const most = Math.max(0, ...d.copies.map((c) => c.copies));
  return h("div", {},
    h("p", { class: "muted small" }, "Where museum says each file's encrypted objects are stored, from its object_keys table. "
      + "Backups of those buckets and of the database happen outside Ente: record your checks below."),
    table(["Bucket (datacenter)", "Objects", "Size", "By app"], d.datacenters.map((x) => h("tr", {},
      h("td", { class: "mono" }, x.datacenter), h("td", { class: "num" }, num(x.objects)), h("td", {}, bytes(x.bytes)),
      h("td", { class: "small" }, Object.entries(x.apps).map(([k, v]) => `${k}: ${num(v.objects)} · ${bytes(v.bytes)}`).join("; ")))),
    "No stored objects yet."),
    d.copies.length > 1 || d.under_replicated ? h("p", { class: d.under_replicated ? "msg warn" : "muted small" },
      `${num(d.under_replicated)} object${d.under_replicated === 1 ? " has" : "s have"} fewer than ${most} copies `
      + `(${d.copies.map((c) => `${c.copies} cop${c.copies === 1 ? "y" : "ies"}: ${num(c.objects)}`).join(", ")}).`) : null,
    h("h3", {}, "Backup checks"),
    table(["When", "What", "Result", "Note", "By"], (d.checks || []).map((c) => h("tr", {},
      h("td", {}, when(c.at)), h("td", {}, c.params.what), h("td", {}, badge(c.params.result === "ok" ? "ok" : "warn", c.params.result)),
      h("td", { class: "small" }, c.params.note || "—"), h("td", { class: "small" }, c.actor))),
    "No checks recorded. After testing a restore, record it here so the next person knows when it last worked."));
}
function backupDialog(name) {
  const what = h("input", { required: true, maxlength: "100", placeholder: "e.g. restored last night's database dump to a test server" });
  const res = select("Result", [["ok", "Worked"], ["problem", "Found a problem"]], "ok", () => {});
  const note = h("textarea", { rows: "3", maxlength: "500" });
  const msg = msgBox();
  const btn = h("button", { class: "btn primary", type: "submit" }, "Record");
  const form = h("form", { class: "dlg-form" }, h("p", { class: "muted" }, "Recorded in the audit log. The dashboard doesn't run backups or restores."),
    field("What was checked", what), field("Result", res), field("Note", note), msg,
    h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: closeDialog }, "Cancel"), btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true);
    const r = await api("/api/admin/backups/check", { instance: name, what: what.value.trim(), result: res.value, note: note.value.trim() });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    closeDialog(); drop("/api/admin/storage"); loadPortal(); renderServer();
  });
  openDialog("Record a backup check", form);
}

// ---- the account drawer: Locker, sharing, notifications, devices ----
function drawerExtras(a) {
  if (!isAdmin()) return [];
  const out = [];
  if (a.locker_items !== null && a.locker_items !== undefined) {
    out.push(h("h3", {}, "Locker"), h("dl", { class: "facts tight" },
      h("dt", {}, "Items"), h("dd", {}, `${num(a.locker_items)} of ${num(a.locker_item_limit)}`),
      h("dt", {}, "Size"), h("dd", {}, `${bytes(a.locker_bytes)} of ${bytes(a.locker_byte_limit)}`),
      h("dt", {}, "Tier"), h("dd", {}, a.locker_tier || "—"),
      h("dt", {}, "Collections"), h("dd", {}, num(a.locker_collections))));
  }
  if (a.shares_out !== null && a.shares_out !== undefined || a.album_links !== null && a.album_links !== undefined) {
    out.push(h("h3", {}, "Sharing"), h("dl", { class: "facts tight" },
      h("dt", {}, "Albums shared with others"), h("dd", {}, num(a.shares_out)),
      h("dt", {}, "Shared with them"), h("dd", {}, num(a.shares_in)),
      h("dt", {}, "Public album links"), h("dd", {}, num(a.album_links)),
      h("dt", {}, "Locker links"), h("dd", {}, num(a.locker_links)),
      h("dt", {}, "Trusted contacts"), h("dd", {}, a.trusted_contacts === null || a.trusted_contacts === undefined ? "—" : `${a.trusted_contacts} · trusted by ${a.trusted_by}`),
      h("dt", {}, "Legacy kits"), h("dd", {}, num(a.legacy_kits))));
  }
  const inst0 = (state.cfg.instances || []).find((i) => i.name === a.instance) || {};
  if (inst0.write_role && a.devices && a.devices.length) {
    out.push(h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => devicesDialog(a) }, "Sign out a device…")));
  }
  if (state.cfg.notifications) {
    const box = h("div", {}, h("p", { class: "muted" }, "Loading…"));
    out.push(h("h3", {}, "Notifications"), box);
    (async () => {
      const [ev, up] = await Promise.all([api(`/api/admin/notify/events?instance=${enc(a.instance)}&account=${enc(a.label)}&limit=8`),
        get(`/api/admin/notify/upcoming?instance=${enc(a.instance)}`, 30)]);
      const u = up.ok ? up.data.upcoming.find((x) => x.account === a.label) : null;
      box.replaceChildren(...[
        u ? h("p", { class: "small" }, u.state === "suppressed" ? "Suppressed. " : u.next ? `Next: ${u.next}-day reminder ${dueIn(u.due_at)}. ` : "All reminders sent for this absence. ",
          u.sent.length ? `Sent this absence: ${u.sent.map((x) => `${x} d`).join(", ")}.` : "") : null,
        ev.ok && ev.data.events.length ? h("ul", { class: "recent" }, ev.data.events.map((e) => h("li", {},
          h("span", {}, kindLabel(e)), h("span", {}, statusBadge(e.status, e.error), " ", h("small", { class: "muted" }, ago(e.at))))))
          : h("p", { class: "muted small" }, ev.ok ? "No notifications yet." : (ev.data.error || "")),
        u ? h("div", { class: "row" }, h("button", { class: "btn small", type: "button", onclick: () => sendDialog(a.instance, u, up.data.thresholds) }, "Send a reminder…"),
          u.state === "suppressed" ? h("button", { class: "btn small", type: "button", onclick: () => unsuppress(a.instance, a.label, u.suppressed.kind) }, "Unsuppress")
            : h("button", { class: "btn small", type: "button", onclick: () => suppressDialog(a.instance, a.label, a.email) }, "Suppress…")) : null].filter(Boolean));
    })();
  }
  return out;
}
async function devicesDialog(a) {
  if (!unlocked(a.instance)) return lockedDialog(a.instance, "sign out a device");
  const r = await api(`/api/admin/sessions?instance=${enc(a.instance)}&account=${enc(a.label)}`);
  if (!r.ok) return openDialog("Devices", errorBox(r));
  const msg = msgBox();
  const list = h("ul", { class: "devices" }, r.data.sessions.map((s) => h("li", {},
    h("div", {}, h("div", {}, s.device), h("small", {}, `${s.app} · signed in ${day(s.signed_in)} · last seen ${ago(s.last_seen)}`)),
    h("button", { class: "btn danger small", type: "button", onclick: () => signOut(a, s, msg) }, "Sign out"))));
  openDialog(`Devices of ${a.email || a.label}`, h("div", { class: "dlg-form" },
    h("p", { class: "muted" }, "Signing out ends that session within a minute; the person can sign in again. Use it for a lost or old device."), list, msg));
}
async function signOut(a, s, msg) {
  const word = "sign out";
  const btn = h("button", { class: "btn danger solid", type: "submit" }, "Sign out");
  const [cf, c] = typedConfirm(word, btn);
  const form = h("form", { class: "dlg-form" }, h("p", {}, `${s.device} (${s.app}), signed in ${day(s.signed_in)}.`), cf, msg,
    h("div", { class: "row end" }, h("button", { class: "btn", type: "button", onclick: () => devicesDialog(a) }, "Back"), btn));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    busy(btn, true);
    const r = await api("/api/admin/sessions/signout", { instance: a.instance, account: a.label, session: s.id, confirm: c.value });
    busy(btn, false);
    if (!r.ok) return say(msg, r.data.error || `HTTP ${r.status}`, "error");
    say(msg, `Signed out. ${r.data.note}`, "ok");
    form.querySelector(".row.end").replaceChildren(h("button", { class: "btn primary", type: "button", onclick: closeDialog }, "Close"));
    loadPortal(); setTimeout(load, 1500);
  });
  openDialog("Sign out a device", form);
}

// ---- audit summaries for the new actions ----
function auditSummary(e) {
  const p = e.params || {};
  switch (e.action) {
    case "notify-send": return `${p.level}-day reminder: ${e.after ? e.after.status : ""}`;
    case "notify-resend": return `resent #${p.event}: ${e.after ? e.after.status : ""}`;
    case "notify-test": return `${p.template} test to ${e.account_email}: ${e.after ? e.after.status : ""}`;
    case "notify-suppress": return `${p.kind === "*" ? "all" : p.kind} ${p.days ? `for ${p.days} days` : "until removed"}${p.reason ? ` (${p.reason})` : ""}`;
    case "notify-unsuppress": return p.kind === "*" ? "all" : p.kind;
    case "notify-settings": return Object.entries(p).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("; ");
    case "notify-template": case "notify-template-reset": return p.id;
    case "notify-import": return Object.entries(p).map(([k, v]) => `${k} ${v}`).join(", ");
    case "revoke-link": return `${p.kind} link #${p.id}`;
    case "sign-out-device": return e.before ? `${e.before.device} (${e.before.app})` : "";
    case "backup-check": return `${p.what}: ${p.result}${p.note ? ` (${p.note})` : ""}`;
    case "museum-config-proposed": case "museum-config-verified":
      return [p.apps && Object.keys(p.apps).length ? `apps: ${Object.keys(p.apps).join(", ")}` : null, p.admins ? "admins" : null,
        p.disable_registration !== null && p.disable_registration !== undefined ? `disable-registration: ${p.disable_registration}` : null].filter(Boolean).join("; ") + (e.error ? ` (${e.error})` : "");
    default: return null;
  }
}

window.addEventListener("hashchange", () => setView(location.hash.slice(1), false));
document.addEventListener("DOMContentLoaded", () => {
  const v = location.hash.slice(1);
  if (v) adm.view = v;
  // the instance tabs drive the per-server views too
  $("#instances").addEventListener("click", () => setTimeout(refreshView, 0));
});
