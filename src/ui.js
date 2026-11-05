"use strict";
var _a, _b, _c, _d;
if (!Array.prototype.flatMap) Object.defineProperty(Array.prototype, "flatMap", { value: function(fn, ctx) {
  return this.reduce((out, x, i) => out.concat(fn.call(ctx, x, i, this)), []);
}, configurable: true, writable: true });
if (!Element.prototype.replaceChildren) Element.prototype.replaceChildren = function(...items) {
  while (this.firstChild) this.removeChild(this.firstChild);
  for (const item of items) this.appendChild(typeof item === "string" ? document.createTextNode(item) : item);
};
const $ = (id) => document.getElementById(id);
let csrf = (_a = document.querySelector('input[name="csrf"]')) == null ? void 0 : _a.value;
const searchInput = $("series-search");
function searchIsHidden() {
  return localStorage.getItem("dubs-hide-search") === "1";
}
function syncSearchVisibility() {
  const hidden = searchIsHidden() || tab === "settings";
  const wrapper = searchInput == null ? void 0 : searchInput.closest(".search");
  if (wrapper) wrapper.hidden = hidden;
  if (searchInput) searchInput.hidden = hidden;
  const toggle = $("show-search");
  if (toggle) toggle.setAttribute("aria-checked", String(!searchIsHidden()));
}
(_b = $("show-search")) == null ? void 0 : _b.addEventListener("click", () => {
  const hidden = !searchIsHidden();
  localStorage.setItem("dubs-hide-search", hidden ? "1" : "0");
  if (hidden && searchInput) searchInput.value = "";
  syncSearchVisibility();
  render();
  toast(hidden ? "Строка поиска скрыта" : "Строка поиска включена");
});
function mediaDetailsOn() {
  return localStorage.getItem("dubs-hide-media-details") !== "1";
}
const mediaSwitch = $("media-details-switch");
function syncMediaSwitch() {
  if (mediaSwitch) mediaSwitch.setAttribute("aria-checked", String(mediaDetailsOn()));
}
mediaSwitch == null ? void 0 : mediaSwitch.addEventListener("click", () => {
  const next = !mediaDetailsOn();
  localStorage.setItem("dubs-hide-media-details", next ? "0" : "1");
  syncMediaSwitch();
  render();
  toast(next ? "Качество и озвучка показываются" : "Компактный вид: качество и озвучка скрыты");
});
syncMediaSwitch();
const voices = [...document.querySelectorAll(".show")];
let tab = "released", data = null, loading = false, calendarMode = false, month = "", day = "", dialogEpisode = null, torrents = [];
const searchCache = /* @__PURE__ */ new Map();
const busy = /* @__PURE__ */ new Set();
const bulkBusy = /* @__PURE__ */ new Set();
const bulkWatchBusy = /* @__PURE__ */ new Set();
const watchedEpisodes = /* @__PURE__ */ new Set();
const openReleasedShows = /* @__PURE__ */ new Set();
let toastTimer;
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== void 0) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
}
function toast(text) {
  const n = $("episode-status");
  n.textContent = text;
  n.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => n.hidden = true, 12e3);
}
async function json(url, options2 = {}) {
  const r = await fetch(url, { credentials: "same-origin", ...options2 });
  if (r.status === 401) {
    location.assign("/");
    throw new Error("Войдите заново");
  }
  let d;
  try {
    d = await r.json();
  } catch (e) {
    throw new Error(r.status === 429 ? "Слишком много запросов. Подождите немного" : r.status === 403 ? "Сеанс страницы устарел. Обновите страницу и повторите действие" : "Сервис временно недоступен");
  }
  if (!r.ok) throw new Error(d.message || "Не удалось выполнить запрос");
  return d;
}
async function action(op, episode = 0, selection = "") {
  const session = await json("/api/session");
  csrf = session.csrf;
  return json("/api/action", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ csrf, op, episode: String(episode), selection }) });
}
async function job(id, onUpdate) {
  for (let i = 0; i < 300; i++) {
    const r = await json("/api/jobs/" + id);
    if (onUpdate) onUpdate(r);
    if (["done", "error", "waiting", "downloading", "importing", "paused", "cancelled"].includes(r.state)) {
      if (r.state === "error") throw new Error(r.message);
      return r;
    }
    await new Promise((resolve) => setTimeout(resolve, 2e3));
  }
  throw new Error("Поиск продолжается в фоне. Откройте окно чуть позже");
}
function date(raw) {
  if (!raw) return "Дата не объявлена";
  return raw.slice(0, 10).split("-").reverse().join(".");
}
function relative(e) {
  const target = e.calendarDate || (e.airDate || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(target)) return "";
  const today = (/* @__PURE__ */ new Date()).toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
  const days = Math.round((Date.parse(target + "T00:00:00Z") - Date.parse(today + "T00:00:00Z")) / 864e5);
  if (days > 0) return "через " + days + " дн.";
  if (days === 0) return "сегодня";
  return Math.abs(days) + " дн. назад";
}
function number(e) {
  var _a2, _b2;
  return "S" + String((_a2 = e.season) != null ? _a2 : 0).padStart(2, "0") + "E" + String((_b2 = e.number) != null ? _b2 : 0).padStart(2, "0");
}
function cover(sid) {
  const img = node("img");
  img.className = "cover";
  img.src = "/cover/" + sid + ".jpg";
  img.alt = "";
  img.loading = "lazy";
  img.width = 105;
  img.height = 68;
  return img;
}
function matches(text) {
  return text.toLocaleLowerCase("ru").includes(searchInput.value.trim().toLocaleLowerCase("ru"));
}
function count(n, unit) {
  $("visible-count").textContent = n;
  if ($("count-unit")) $("count-unit").textContent = unit;
}
function tools(parent) {
  const box = node("div", void 0, "episodes-tools");
  box.append(node("small", (data == null ? void 0 : data.updated) ? "MyShows · " + new Date(data.updated * 1e3).toLocaleString("ru-RU") : "Получаем данные MyShows…"));
  const button = node("button", "Обновить", "quiet");
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const r = await action("refresh");
      await job(r.job);
      await load(true);
      toast("MyShows обновлён");
    } catch (e) {
      toast(e.message);
    } finally {
      button.disabled = false;
    }
  });
  box.append(button);
  parent.append(box);
  if (data == null ? void 0 : data.error) parent.append(node("p", data.error, "episode-note"));
}
async function load(force = false) {
  if (loading) return;
  if (data && !force) {
    render();
    return;
  }
  loading = true;
  try {
    data = await json("/api/episodes");
    render();
  } catch (e) {
    toast(e.message);
  } finally {
    loading = false;
  }
}
let pendingEpisode = null, pendingSection = null;
function findEpisode(id) {
  for (const g of (data == null ? void 0 : data.released) || []) for (const e of g.episodes) if (e.id === id) return e;
  for (const g of (data == null ? void 0 : data.upcoming) || []) for (const e of g.episodes) if (e.id === id) return e;
  return null;
}
function focusEpisode(id) {
  pendingEpisode = null;
  if (!id) {
    toast("Некорректный адрес серии. Откройте журнал событий в настройках");
    return;
  }
  const row = document.querySelector('[data-episode-id="' + id + '"]');
  if (!row) {
    toast("Серии нет в списке вышедших. Журнал событий — в настройках");
    return;
  }
  const card = row.closest("details");
  if (card && !card.open) card.open = true;
  row.tabIndex = -1;
  row.scrollIntoView({ block: "center" });
  row.focus({ preventScroll: true });
  const e = findEpisode(id);
  if (e && e.localMedia && e.localMedia.status === "stale") toast("Локальная копия: данные файла устарели, файл мог быть заменён");
  else if (e && e.localMedia && e.localMedia.status !== "verified") toast("Локальная копия: метаданные файла не подтверждены");
}
function route() {
  const raw = location.hash.slice(1);
  const q = raw.indexOf("?");
  const hash = q < 0 ? raw : raw.slice(0, q);
  const query = new URLSearchParams(q < 0 ? "" : raw.slice(q + 1));
  tab = ["voice", "released", "upcoming", "settings"].includes(hash) ? hash : hash.startsWith("show-") ? "voice" : "released";
  for (const link of document.querySelectorAll("[data-tab]")) link.classList.toggle("active", link.dataset.tab === tab);
  for (const [id, name] of [["voice-view", "voice"], ["released-view", "released"], ["upcoming-view", "upcoming"], ["settings", "settings"]]) $(id).hidden = tab !== name;
  $("empty-search").hidden = true;
  $("view-title").textContent = { voice: "Озвучка", released: "Вышедшие серии", upcoming: "Будет", settings: "Настройки" }[tab];
  const counter = $("visible-count").closest(".counter");
  if (counter) counter.hidden = tab === "settings";
  syncSearchVisibility();
  document.querySelector(".sync-line").hidden = tab !== "voice";
  if (tab === "voice") {
    filterVoices();
    if (hash.startsWith("show-")) {
      const target = $(hash);
      if (target) target.open = true;
    }
  } else if (tab === "settings") {
    count("", "");
    if (query.get("section") === "events") pendingSection = "events";
    loadEventsSettings();
    if (pendingSection === "events") {
      pendingSection = null;
      setTimeout(() => {
        const panel = $("events-panel");
        if (!panel) return;
        panel.tabIndex = -1;
        panel.scrollIntoView({ block: "start" });
        panel.focus({ preventScroll: true });
        openJournal();
      }, 50);
    }
  } else {
    const ep = query.get("episode");
    pendingEpisode = ep === null ? null : /^[1-9][0-9]{0,9}$/.test(ep) ? Number(ep) : 0;
    load();
  }
}
function filterVoices() {
  let visible = 0;
  for (const row of voices) {
    row.hidden = !matches(row.dataset.search);
    if (!row.hidden) visible++;
  }
  count(visible, "сериалов");
  $("empty-search").hidden = visible !== 0 || tab !== "voice";
}
function mediaInfo(e) {
  var _a2;
  const m = (_a2 = e == null ? void 0 : e.localMedia) == null ? void 0 : _a2;
  if (!m || typeof m !== "object") return null;
  return {
    status: m.status === "verified" || m.status === "stale" ? m.status : "unknown",
    quality: m.quality && typeof m.quality === "object" ? m.quality : null,
    tracks: Array.isArray(m.audioTracks) ? m.audioTracks.filter((t) => t && typeof t === "object") : [],
    verifiedAt: typeof m.verifiedAt === "number" && m.verifiedAt > 0 ? m.verifiedAt : null,
    source: m.source === "jellyfin" || m.source === "sonarr" ? m.source : null
  };
}
// Only canonical studio metadata participates; raw tags and filenames are not evidence.
function studioAbbreviation(value) {
  const name = String(value || "").toLowerCase().replace(/\s+/g, " ").replace(/\s*\/\s*/g, " / ").trim();
  const aliases = [
    ["К-Б", "кураж-бамбей", "кураж-бомбей"], ["КвК", "кубик в кубе"],
    ["Nova", "novamedia", "novafilm", "novamedia / novafilm"], ["RHS", "red head sound"],
    ["Lost", "lostfilm"], ["HDr", "hdrezka studio", "hdrezka18+", "hdrezka 18+", "hdrezka"],
    ["Alex", "alexfilm"], ["VP", "viruse project", "virus project"],
    ["DMS", "dragon money studio", "dms"], ["RUd", "rudub"],
    ["LE-P", "le-production"], ["Cold", "coldfilm"],
    ["SNDK", "сыендук", "syenduk", "sndk", "сыендук / syenduk / sndk"]
  ];
  for (const row of aliases) if (row.slice(1).includes(name)) return row[0];
  return "?";
}
function mediaVoice(info, episode) {
  const configNode = $("media-badge-config");
  const config = configNode ? JSON.parse(configNode.getAttribute("data-config")) : { studios: [], preferred: {} };
  const actual = info.tracks.map((t) => studioAbbreviation(t.studio));
  const present = (studio) => studio && info.tracks.some((t) => t.studio && (
    String(t.studio).toLowerCase() === String(studio).toLowerCase() ||
    (studioAbbreviation(studio) !== "?" && studioAbbreviation(t.studio) === studioAbbreviation(studio))));
  const preferred = config.preferred[String(episode.seriesId)];
  if (present(preferred)) return studioAbbreviation(preferred);
  for (const studio of config.studios) {
    const abbreviation = studioAbbreviation(studio);
    if (present(studio)) return abbreviation;
  }
  // Without a ranking, a single known studio is unambiguous. Multiple are unknown.
  const known = actual.filter((value, index) => value !== "?" && actual.indexOf(value) === index);
  return known.length === 1 ? known[0] : "?";
}
function mediaShort(info, episode) {
  if (info.status === "stale") return "Устарело";
  if (info.status !== "verified") return "Не подтверждено";
  const q = info.quality || {};
  // The localMedia schema supplies a trusted label, not a resolution field.
  // Nominal labels retain scan type and outrank cropped stream dimensions.
  const label = String(q.label || "").match(/\b([1-9][0-9]{2,4})[pi]\b/i);
  const resolution = label ? label[0] : typeof q.height === "number" && Number.isInteger(q.height) && q.height > 0 && q.height < 100000 ? q.height + "p" : "Проверено";
  return resolution + " " + mediaVoice(info, episode);
}
function channelLabel(channels) {
  if (channels === 6) return "5.1";
  if (channels === 2) return "2.0";
  if (channels === 1) return "моно";
  return typeof channels === "number" && channels > 0 ? String(channels) : "";
}
function mediaDetails(e) {
  if (!mediaDetailsOn()) return null;
  const info = mediaInfo(e);
  if (!info) return null;
  const id = "media-panel-" + e.id;
  const toggle = node("button", mediaShort(info, e), "media-badge is-" + info.status);
  toggle.type = "button";
  toggle.setAttribute("aria-label", toggle.textContent + ": качество и аудиодорожки");
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", id);
  const panel = node("div", void 0, "media-panel");
  panel.id = id;
  panel.hidden = true;
  const note = info.status === "stale" ? "Данные файла устарели: файл мог быть заменён. Обновите список позже." : info.status === "unknown" ? "Метаданные файла не подтверждены." : "";
  if (note) panel.append(node("p", note, "media-note"));
  const q = info.quality;
  if (q && typeof q.label === "string" && q.label) {
    const parts = [q.label];
    if (typeof q.width === "number" && q.width > 0 && typeof q.height === "number" && q.height > 0) parts.push(q.width + "×" + q.height);
    if (q.codec) parts.push(String(q.codec));
    if (q.hdr) parts.push(String(q.hdr));
    if (q.source) parts.push("источник: " + (q.source === "jellyfin" ? "Jellyfin" : "Sonarr"));
    panel.append(node("div", "Видео: " + parts.join(" · "), "media-line"));
  } else panel.append(node("div", "Видео: данные недоступны", "media-line"));
  if (info.verifiedAt) panel.append(node("div", "Файл проверен " + new Date(info.verifiedAt * 1e3).toLocaleString("ru-RU"), "media-line"));
  if (!info.tracks.length) panel.append(node("div", "Аудиодорожки: неизвестны", "media-line"));
  else {
    const list = node("ul", void 0, "media-tracks");
    for (const t of info.tracks) {
      const bits = [t.label || t.languageLabel || t.language || "Дорожка " + (t.index != null ? Number(t.index) + 1 : list.children.length + 1)];
      if (t.codec && !bits[0].toLowerCase().includes(String(t.codec).toLowerCase())) bits.push(String(t.codec));
      const ch = channelLabel(t.channels);
      if (ch && !bits[0].includes(ch)) bits.push(ch);
      if (t.isDefault === true) bits.push("по умолчанию");
      const li = node("li", bits.join(" · "));
      if (t.title) li.append(node("span", "тег дорожки: " + t.title, "media-track-title"));
      li.append(node("span", t.studio ? "студия: " + t.studio : "студия не указана в метаданных файла", "media-studio" + (t.studio ? "" : " is-unknown")));
      list.append(li);
    }
    panel.append(node("div", "Аудиодорожки: " + info.tracks.length, "media-line"), list);
  }
  toggle.addEventListener("click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  });
  return { toggle, panel };
}
function controls(e, text) {
  var _a2, _b2;
  const box = node("div", void 0, "episode-actions");
  const media = mediaDetails(e);
  const task = taskControls(e, box);
  const failed = currentTask(e)?.state === "error";
  if (((_a2 = currentTask(e)) == null ? void 0 : _a2.state) === "done") e.hasFile = true;
  if (e.future) {
    const scheduled = ((_b2 = data == null ? void 0 : data.scheduled) == null ? void 0 : _b2[String(e.id)]) || (!failed && task);
    const plan = node("button", scheduled ? "Запланировано" : "Запланировать", scheduled ? "quiet" : "");
    plan.type = "button";
    plan.disabled = !!scheduled;
    plan.addEventListener("click", () => downloadEpisode(e, "", plan));
    box.append(plan);
    if (task) box.append(task);
    return box;
  }
  const download = e.hasFile && e.jellyfinUrl ? node("a", "▶ В медиатеке", "library-link") : node("button", e.hasFile ? "▶ В медиатеке" : "Скачать", e.hasFile ? "library-link" : "");
  if (download.tagName === "A") {
    download.href = e.jellyfinUrl;
    download.target = "_blank";
    download.rel = "noopener noreferrer";
    download.addEventListener("click", (event) => openPlayer(event, e));
  } else {
    download.type = "button";
    download.disabled = e.hasFile;
    download.addEventListener("click", () => downloadEpisode(e, "", download));
    if (e.hasFile) download.title = "Jellyfin ещё не сопоставил эпизод. Обновите данные позже";
  }
  const find = node("button", "Найти", "quiet");
  find.type = "button";
  find.disabled = !e.sonarrId;
  find.addEventListener("click", () => openSearch(e));
  box.append(task || download);
  if (task && failed) box.append(download);
  if (media) box.append(media.toggle);
  box.append(find);
  if (media) box.append(media.panel);
  const watched = node("button", "✓", "watch-button");
  watched.type = "button";
  watched.title = "Отметить просмотренной в MyShows";
  watched.setAttribute("aria-label", "Отметить " + number(e) + " просмотренной в MyShows");
  watched.addEventListener("click", async () => {
    if (watchedEpisodes.has(e.id)) return;
    watched.disabled = true;
    watched.classList.add("is-watched");
    watched.setAttribute("aria-pressed", "true");
    watchedEpisodes.add(e.id);
    setTimeout(() => render(), 180);
    try {
      const r = await action("watch", e.id);
      await job(r.job);
      toast("Отмечено в MyShows. Автоочистка удалит просмотренный файл при следующей сверке");
    } catch (error) {
      watchedEpisodes.delete(e.id);
      render();
      toast(error.message);
    }
  });
  box.append(watched);
  return box;
}
function duration(sec) {
  if (sec === null || sec === void 0 || sec < 0) return "пока неизвестно";
  return sec < 60 ? Math.ceil(sec) + " сек." : Math.ceil(sec / 60) + " мин.";
}
function progressRing(value) {
  const wrap = node("button", void 0, "download-ring");
  wrap.type = "button";
  const progress = node("progress");
  progress.max = 100;
  progress.setAttribute("aria-label", "Загружено");
  if (value !== void 0) progress.value = value;
  wrap.append(progress);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 44 44");
  svg.setAttribute("aria-hidden", "true");
  for (const cls of ["ring-track", "ring-fill"]) {
    const circle = document.createElementNS(ns, "circle");
    for (const [k, v] of Object.entries({ cx: 22, cy: 22, r: 18, fill: "none", "stroke-width": 5 })) circle.setAttribute(k, v);
    circle.setAttribute("class", cls);
    if (cls === "ring-fill") {
      circle.setAttribute("stroke-dasharray", "113.1");
      circle.setAttribute("stroke-dashoffset", String(113.1 * (1 - (value != null ? value : 0) / 100)));
      circle.setAttribute("transform", "rotate(-90 22 22)");
    }
    svg.append(circle);
  }
  wrap.append(svg, node("span", value === void 0 ? "…" : Math.round(value) + "%", "ring-label"));
  return wrap;
}
function currentTask(e) {
  var _a2;
  return (_a2 = data == null ? void 0 : data.actions) == null ? void 0 : _a2[String(e.id)];
}
const openJobDetails = new Set();
function taskControls(e, actions) {
  const task = currentTask(e);
  if (!task || ["done", "cancelled"].includes(task.state)) return null;
  const box = node("span", void 0, "download-task");
  const cancel = node("button", "Отменить", "quiet");
  cancel.type = "button";
  cancel.addEventListener("click", async () => {
    cancel.disabled = true;
    try {
      const r = await action("cancel", e.id);
      await job(r.job);
      delete data.actions[String(e.id)];
      if (data.scheduled) delete data.scheduled[String(e.id)];
      render();
      toast("Заказ отменён. Файлы сохранены");
    } catch (error) {
      toast(error.message);
      cancel.disabled = false;
    }
  });
  if (task.state !== "error") box.append(cancel);
  const ring = progressRing(task.percent);
  ring.id = "job-toggle-" + e.id;
  const panel = node("div", void 0, "job-details");
  panel.id = "job-details-" + e.id;
  panel.hidden = !openJobDetails.has(String(e.id));
  ring.setAttribute("aria-label", "Состояние загрузки " + number(e) + ": " + (task.percent === void 0 ? "…" : Math.round(task.percent) + "%"));
  ring.setAttribute("aria-expanded", String(!panel.hidden));
  ring.setAttribute("aria-controls", panel.id);
  ring.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    if (panel.hidden) openJobDetails.delete(String(e.id));
    else openJobDetails.add(String(e.id));
    ring.setAttribute("aria-expanded", String(!panel.hidden));
  });
  box.append(ring);
  let label = task.state === "queued" ? "В очереди" : task.message || "Выполняется";
  if (task.state === "downloading") label += " · ETA " + duration(task.eta) + (task.rate ? " · " + (task.rate / 1048576).toFixed(1) + " МБ/с" : "");
  panel.append(node("div", label));
  actions.append(panel);
  return box;
}
async function downloadEpisode(e, selection, button) {
  var _a2, _b2;
  if (busy.has(e.id)) return;
  busy.add(e.id);
  button.disabled = true;
  button.textContent = e.future ? "Сохраняем…" : "Отправляем…";
  try {
    const r = await action("download", e.id, selection);
    (_a2 = data.actions) != null ? _a2 : data.actions = {};
    data.actions[String(e.id)] = { job: r.job, state: "queued", message: "Задание принято" };
    if (e.future) {
      (_b2 = data.scheduled) != null ? _b2 : data.scheduled = {};
      data.scheduled[String(e.id)] = { job: r.job };
    }
    render();
    if (selection) $("torrent-dialog").close();
    toast(e.future ? "Запланировано. Поиск начнётся после выхода серии" : "Задание принято. Выполняем поиск раздачи");
  } catch (error) {
    toast(error.message);
    button.disabled = false;
    button.textContent = e.future ? "Запланировать" : "Скачать";
  } finally {
    busy.delete(e.id);
  }
}
function bulkButton(group) {
  const active = group.episodes.filter((e) => {
    var _a2;
    return ["queued", "running", "waiting", "downloading", "importing", "paused", "cancelling"].includes((_a2 = currentTask(e)) == null ? void 0 : _a2.state);
  });
  const remaining = group.episodes.filter((e) => {
    var _a2;
    return !watchedEpisodes.has(e.id) && !e.future && !e.hasFile && !["queued", "running", "waiting", "downloading", "importing", "paused", "done", "cancelling"].includes((_a2 = currentTask(e)) == null ? void 0 : _a2.state);
  });
  const cancelling = active.length > 0;
  const complete = !cancelling && !remaining.length;
  const button = node("button", bulkBusy.has(group.seriesId) ? "Отправляем…" : cancelling ? "Отменить" : complete ? "Всё заказано" : "Скачать всё", "bulk-download" + (complete ? " is-complete" : ""));
  button.type = "button";
  button.disabled = complete || bulkBusy.has(group.seriesId);
  button.title = cancelling ? "Отменить все активные заказы этого сериала" : "Заказать все вышедшие непросмотренные серии этого сериала";
  button.setAttribute("aria-label", (cancelling ? "Отменить все заказы: " : "Скачать все непросмотренные серии: ") + group.title);
  button.addEventListener("click", async (event) => {
    var _a2;
    event.preventDefault();
    event.stopPropagation();
    if (bulkBusy.has(group.seriesId)) return;
    bulkBusy.add(group.seriesId);
    button.disabled = true;
    button.textContent = cancelling ? "Отменяем…" : "Отправляем…";
    try {
      const response = await action(cancelling ? "cancel_all" : "download_all", (cancelling ? active : remaining)[0].id);
      (_a2 = data.actions) != null ? _a2 : data.actions = {};
      for (const task of response.jobs) {
        if (cancelling) data.actions[String(task.episode)] = { ...data.actions[String(task.episode)], state: "cancelling", message: "Отменяем заказ…" };
        else data.actions[String(task.episode)] = { job: task.job, state: "queued", message: "Задание принято в очередь" };
      }
      toast(cancelling ? "Отменяем все активные заказы сериала. Скачанные файлы сохранятся" : response.jobs.length ? "Принято серий: " + response.jobs.length + ". Ищем общие раздачи сезонов" : "Все серии уже скачаны или заказаны");
    } catch (error) {
      toast(error.message);
    } finally {
      bulkBusy.delete(group.seriesId);
      render();
    }
  });
  return button;
}
function bulkControls(group) {
  const box = node("div", void 0, "card-actions");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "Все вышедшие непросмотренные серии: " + group.title);
  box.append(bulkButton(group));
  const find = node("button", "Найти всё", "quiet");
  find.type = "button";
  find.addEventListener("click", () => openSearch({ ...group.episodes[0], bulk: true, seriesTitle: group.title }));
  const watch = node("button", "✓", "watch-button");
  watch.type = "button";
  watch.disabled = bulkWatchBusy.has(group.seriesId);
  watch.title = "Отметить все вышедшие непросмотренные серии в MyShows";
  watch.setAttribute("aria-label", watch.title + ": " + group.title);
  watch.addEventListener("click", async () => {
    if (bulkWatchBusy.has(group.seriesId)) return;
    bulkWatchBusy.add(group.seriesId);
    watch.disabled = true;
    watch.classList.add("is-watched");
    try {
      const r = await action("watch_all", group.episodes[0].id);
      const done = await job(r.job, (progress) => {
        for (const id of progress.watched || []) watchedEpisodes.add(id);
        render();
      });
      for (const id of done.watched || []) watchedEpisodes.add(id);
      render();
      toast("Все серии отмечены в MyShows. Просмотренные файлы удалит автоочистка");
    } catch (error) {
      toast(error.message);
      data = await json("/api/episodes");
      render();
    } finally {
      bulkWatchBusy.delete(group.seriesId);
      render();
    }
  });
  box.append(find, watch);
  return box;
}
async function downloadSelection(e, key, button) {
  var _a2;
  if (!e.bulk) return downloadEpisode(e, key, button);
  button.disabled = true;
  try {
    const r = await action("download_all", e.id, key);
    (_a2 = data.actions) != null ? _a2 : data.actions = {};
    for (const task of r.jobs) data.actions[String(task.episode)] = { job: task.job, state: "queued", message: "Задание принято" };
    $("torrent-dialog").close();
    render();
    toast("Принято серий из выбранной раздачи: " + r.jobs.length);
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
}
function renderReleased() {
  const parent = $("released-view");
  for (const item of parent.querySelectorAll("details")) {
    if (item.open) openReleasedShows.add(item.dataset.seriesId);
    else openReleasedShows.delete(item.dataset.seriesId);
  }
  parent.replaceChildren();
  tools(parent);
  let total = 0;
  for (const group of (data == null ? void 0 : data.released) || []) {
    const eps = group.episodes.filter((e) => !watchedEpisodes.has(e.id) && matches(group.title + " " + e.title + " " + number(e)));
    if (!eps.length) continue;
    total += eps.length;
    const details = node("details", void 0, "episode-show");
    details.dataset.seriesId = String(group.seriesId);
    details.open = openReleasedShows.has(String(group.seriesId));
    const summary = node("summary");
    summary.append(cover(group.seriesId));
    const copy = node("div", void 0, "show-copy");
    copy.append(node("strong", group.title, "show-title"), node("div", eps.length + " непросмотренных серий", "show-meta"));
    summary.append(copy, node("span", void 0, "chevron"));
    details.append(summary);
    for (const e of eps) {
      const row = node("div", void 0, "episode-line");
      row.dataset.episodeId = String(e.id);
      row.append(node("span", number(e), "episode-number"));
      const text = node("div");
      text.append(node("div", e.title, "episode-title"));
      let info = date(e.airDate) + " · " + relative(e);
      if (typeof e.comments === "number") info += " · " + e.comments + " комм.";
      if (e.rating > 0) info += " · ★ " + Number(e.rating).toFixed(1);
      text.append(node("div", info, "episode-details"));
      if (!e.sonarrId) text.append(node("div", "Нумерация не сопоставлена с Sonarr", "episode-note"));
      row.append(text, controls(e, text));
      details.append(row);
    }
    const card = node("div", void 0, "episode-card");
    card.append(details, bulkControls(group));
    parent.append(card);
  }
  if (!total) parent.append(node("p", (data == null ? void 0 : data.updated) ? "Новых непросмотренных серий нет" : "Загружаем список MyShows…", "muted-empty"));
  parent.append(node("p", "Отметка просмотра передаётся в MyShows. Просмотренные файлы удаляются вашей автоматической очисткой.", "episode-note"));
  count(total, "серий");
  if (pendingEpisode !== null) focusEpisode(pendingEpisode);
}
function calendarGrid(parent, items) {
  if (!month) return;
  const [year, m] = month.split("-").map(Number);
  const grid = node("div", void 0, "calendar-grid");
  for (const label of ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]) grid.append(node("small", label));
  const first = (new Date(year, m - 1, 1).getDay() + 6) % 7;
  for (let i = 0; i < first; i++) grid.append(node("span"));
  const days = new Date(year, m, 0).getDate();
  const counts = /* @__PURE__ */ new Map();
  for (const e of items) {
    const key = e.calendarDate;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  for (let i = 1; i <= days; i++) {
    const key = month + "-" + String(i).padStart(2, "0");
    const n = counts.get(key) || 0;
    const b = node("button", i);
    b.type = "button";
    b.disabled = !n;
    b.classList.toggle("has-events", !!n);
    b.classList.toggle("selected", day === key);
    if (n) b.append(node("span", n + " сер."));
    b.addEventListener("click", () => {
      day = day === key ? "" : key;
      renderUpcoming();
    });
    grid.append(b);
  }
  parent.append(grid);
}
function renderUpcoming() {
  const parent = $("upcoming-view");
  parent.replaceChildren();
  tools(parent);
  const all = ((data == null ? void 0 : data.upcoming) || []).flatMap((g) => g.episodes).filter((e) => matches(e.seriesTitle + " " + e.title + " " + number(e)));
  const months = [...new Set(all.map((e) => e.calendarDate.slice(0, 7)))].sort();
  if (!months.includes(month)) month = months[0] || "";
  const toolbar = node("div", void 0, "calendar-controls");
  const select = node("select");
  select.setAttribute("aria-label", "Месяц расписания");
  for (const key of months) {
    const option = node("option", (/* @__PURE__ */ new Date(key + "-02T12:00:00")).toLocaleDateString("ru-RU", { month: "long", year: "numeric" }));
    option.value = key;
    option.selected = key === month;
    select.append(option);
  }
  select.addEventListener("change", () => {
    month = select.value;
    day = "";
    renderUpcoming();
  });
  const toggle = node("button", calendarMode ? "Список" : "Календарь", "quiet");
  toggle.type = "button";
  toggle.addEventListener("click", () => {
    calendarMode = !calendarMode;
    day = "";
    renderUpcoming();
  });
  toolbar.append(select, toggle);
  if (day) {
    const reset = node("button", "Все даты", "quiet");
    reset.type = "button";
    reset.addEventListener("click", () => {
      day = "";
      renderUpcoming();
    });
    toolbar.append(reset);
  }
  parent.append(toolbar);
  const monthItems = all.filter((e) => e.calendarDate.startsWith(month));
  if (calendarMode) calendarGrid(parent, monthItems);
  const visible = monthItems.filter((e) => !day || e.calendarDate === day).sort((a, b) => a.calendarDate.localeCompare(b.calendarDate) || a.seriesTitle.localeCompare(b.seriesTitle, "ru") || a.number - b.number);
  let prev = "";
  for (const e of visible) {
    if (e.calendarDate !== prev) {
      parent.append(node("h3", date(e.calendarDate), "calendar-day-title"));
      prev = e.calendarDate;
    }
    const row = node("article", void 0, "upcoming-line");
    row.dataset.episodeId = String(e.id);
    const d = /* @__PURE__ */ new Date(e.calendarDate + "T12:00:00");
    const dayLabel = node("div", d.getDate(), "day-number");
    dayLabel.append(node("small", d.toLocaleDateString("ru-RU", { weekday: "short" })));
    const text = node("div");
    text.append(node("div", e.seriesTitle, "upcoming-title"), node("div", number(e) + " · " + e.title, "upcoming-subtitle"), node("div", relative(e), "episode-details"));
    row.append(dayLabel, cover(e.seriesId), text, controls(e, text));
    parent.append(row);
  }
  if (!visible.length) parent.append(node("p", (data == null ? void 0 : data.updated) ? "MyShows пока не объявил новые серии на выбранную дату" : "Загружаем расписание MyShows…", "muted-empty"));
  parent.append(node("p", "Это даты выхода из MyShows. Нужная озвучка может появиться позже. Заказ ожидает подходящую раздачу.", "episode-note"));
  count(visible.length, "серий");
}
function render() {
  const focusedJob = document.activeElement?.matches(".download-ring") ? document.activeElement.id : null;
  if (tab === "released") renderReleased();
  else if (tab === "upcoming") renderUpcoming();
  else if (tab === "voice") filterVoices();
  if (focusedJob) $(focusedJob)?.focus({ preventScroll: true });
}
function bytes(value) {
  if (!Number.isFinite(Number(value))) return "—";
  return (Number(value) / 1073741824).toFixed(2) + " ГБ";
}
function options(id, values, label) {
  const select = $(id);
  const previous = select.value;
  select.replaceChildren();
  const any = node("option", label);
  any.value = "";
  select.append(any);
  for (const v of [...new Set(values)].sort((a, b) => String(a).localeCompare(String(b), "ru", { numeric: true }))) {
    const o = node("option", v);
    o.value = v;
    select.append(o);
  }
  if ([...select.options].some((o) => o.value === previous)) select.value = previous;
}
function rowsChanged(rows, warnings = []) {
  torrents = rows;
  options("torrent-season", rows.flatMap((x) => x.seasons), "Все сезоны");
  options("torrent-quality", rows.map((x) => x.quality), "Любое качество");
  options("torrent-voice", rows.flatMap((x) => x.voices), "Любая озвучка");
  $("torrent-filters").hidden = false;
  if (warnings.length) $("torrent-status").textContent = warnings.join(". ");
  renderTorrents();
}
function renderTorrents() {
  var _a2, _b2;
  const words = $("torrent-keywords").value.toLocaleLowerCase("ru").trim();
  const season = $("torrent-season").value, quality = $("torrent-quality").value, voice = $("torrent-voice").value;
  const eligible = $("torrent-allowed").checked;
  let rows = torrents.filter((r) => (!words || r.title.toLocaleLowerCase("ru").includes(words)) && (!season || r.seasons.includes(Number(season))) && (!quality || r.quality === quality) && (!voice || r.voices.includes(voice)) && (!eligible || r.allowed));
  const by = $("torrent-sort").value, dir = $("torrent-direction").value === "asc" ? 1 : -1;
  const value = (r) => by === "published" ? r.published ? Date.parse(r.published) : null : r[by];
  rows.sort((a, b) => {
    const av = value(a), bv = value(b);
    const am = av === null || av === void 0 || typeof av === "number" && !Number.isFinite(av), bm = bv === null || bv === void 0 || typeof bv === "number" && !Number.isFinite(bv);
    if (am || bm) return Number(am) - Number(bm);
    return (typeof av === "string" ? av.localeCompare(bv, "ru") : Number(av) - Number(bv)) * dir;
  });
  const parent = $("torrent-results");
  parent.replaceChildren();
  for (const r of rows) {
    const row = node("article", void 0, "torrent-row");
    const content = node("div");
    content.append(node("div", r.title, "torrent-title"));
    const meta = node("div", void 0, "torrent-meta");
    meta.append(node("span", r.indexer, "source-badge"), document.createTextNode(r.quality + " · " + bytes(r.size) + " · Сиды: " + r.seeders + " · Личеры: " + ((_a2 = r.leechers) != null ? _a2 : "—") + " · Скачивания: " + ((_b2 = r.downloads) != null ? _b2 : "—") + " · " + date(r.published)));
    content.append(meta, node("div", r.voices.join(" / "), "torrent-meta"));
    if (!r.allowed) content.append(node("div", (r.rejections || []).join("; "), "torrent-why"));
    const button = node("button", "Скачать");
    button.type = "button";
    button.disabled = !r.allowed;
    button.addEventListener("click", () => downloadSelection(dialogEpisode, r.key, button));
    row.append(content, button);
    parent.append(row);
  }
  if (!rows.length) parent.append(node("p", "Нет раздач с такими фильтрами", "muted-empty"));
  $("torrent-status").textContent = "Показано " + rows.length + " из " + torrents.length + " раздач";
}
async function openSearch(e) {
  dialogEpisode = e;
  torrents = [];
  searchStarted = Date.now();
  searchProgress = null;
  searchRunning = true;
  $("torrent-results").replaceChildren();
  $("torrent-filters").hidden = true;
  $("torrent-dialog-title").textContent = e.seriesTitle + " · " + (e.bulk ? "Все вышедшие непросмотренные серии" : number(e));
  $("torrent-status").textContent = "Ищем на подключённых трекерах. Ответы могут занять несколько минут…";
  if (!$("torrent-dialog").open) $("torrent-dialog").showModal();
  const cacheId = (e.bulk ? "bulk-" : "") + e.id;
  const cached = searchCache.get(cacheId);
  if (cached && Date.now() - cached.at < 6e5) {
    searchRunning = false;
    updateSearchProgress();
    rowsChanged(cached.rows, cached.warnings);
    return;
  }
  try {
    const r = await action(e.bulk ? "search_all" : "search", e.id);
    let last = 0;
    const result = await job(r.job, (progress) => {
      var _a2;
      if (dialogEpisode !== e) return;
      searchProgress = progress;
      updateSearchProgress();
      if (((_a2 = progress.rows) == null ? void 0 : _a2.length) && progress.rows.length !== last) {
        last = progress.rows.length;
        rowsChanged(progress.rows, progress.warnings);
      }
    });
    searchCache.set(cacheId, { at: Date.now(), rows: result.rows || [], warnings: result.warnings || [] });
    if (dialogEpisode === e) {
      searchRunning = false;
      updateSearchProgress();
      rowsChanged(result.rows || [], result.warnings);
    }
  } catch (error) {
    searchRunning = false;
    updateSearchProgress();
    $("torrent-status").textContent = error.message;
  }
}
(_c = $("close-torrents")) == null ? void 0 : _c.addEventListener("click", () => $("torrent-dialog").close());
for (const id of ["torrent-keywords", "torrent-season", "torrent-quality", "torrent-voice", "torrent-sort", "torrent-direction", "torrent-allowed"]) (_d = $(id)) == null ? void 0 : _d.addEventListener(id === "torrent-keywords" ? "input" : "change", renderTorrents);
searchInput == null ? void 0 : searchInput.addEventListener("input", render);
window.addEventListener("hashchange", route);
route();
let searchStarted = 0, searchProgress = null, searchRunning = false;
function updateSearchProgress() {
  var _a2;
  const p = $("search-progress");
  if (!p) return;
  p.hidden = !searchRunning;
  if (!searchRunning) return;
  const elapsed = Math.floor((Date.now() - searchStarted) / 1e3), info = searchProgress == null ? void 0 : searchProgress.search;
  const progress = p.querySelector("progress");
  if (info == null ? void 0 : info.total) {
    progress.max = info.total;
    progress.value = info.completed;
  } else progress.removeAttribute("value");
  p.querySelector("span").textContent = (info ? "Ответили " + info.completed + " / " + info.total + " · " : "В очереди / ожидаем ответ · ") + "Прошло " + duration(elapsed) + " · ETA " + ((info == null ? void 0 : info.eta) != null ? "≈ " + duration(info.eta) : "пока неизвестно") + (((_a2 = info == null ? void 0 : info.active) == null ? void 0 : _a2.length) ? " · " + info.active.join(", ") : "");
}
setInterval(updateSearchProgress, 1e3);
let refreshingActions = false;
setInterval(async () => {
  if (!data || refreshingActions || document.hidden) return;
  refreshingActions = true;
  try {
    const latest = await json("/api/episodes");
    const changed = JSON.stringify(latest.actions) !== JSON.stringify(data.actions) || JSON.stringify(latest.scheduled) !== JSON.stringify(data.scheduled);
    data.actions = latest.actions;
    data.scheduled = latest.scheduled;
    if (changed) render();
  } catch (e) {
  } finally {
    refreshingActions = false;
  }
}, 5e3);
const playerNames = { auto: "Автоматически", swiftfin: "Swiftfin", fladder: "Fladder", streamyfin: "Streamyfin", findroid: "Findroid / Findroid Dubs", infuse: "Infuse · ссылки на серии (8.4.7+)", browser: "В браузере" };
const playerSelect = $("playback-client");
if (playerSelect) {
  const ios = /iPhone|iPad|DubsNative.*iOS/.test(navigator.userAgent);
  const android = /Android/.test(navigator.userAgent);
  const choices = ["auto", ...ios ? ["swiftfin", "fladder", "streamyfin", "infuse"] : android ? ["findroid", "fladder", "streamyfin"] : ["swiftfin", "findroid", "fladder", "streamyfin", "infuse"], "browser"];
  for (const value of choices) {
    const o = node("option", playerNames[value]);
    o.value = value;
    playerSelect.append(o);
  }
  const unavailable = node("option", "Jellyfin Mobile — нет перехода к конкретной серии");
  unavailable.disabled = true;
  playerSelect.append(unavailable);
  const saved = localStorage.getItem("dubs-player") || "auto";
  playerSelect.value = choices.includes(saved) ? saved : "auto";
  const help = () => {
    $("playback-help").textContent = "Выбор сохраняется только на этом устройстве. Подключите тот же сервер и аккаунт в выбранном клиенте. Для выбора клиента в приложении нужен Dubs1.1+. Swiftfin1.5 совместим с iOS17; для Infuse нужен TMDB ID сериала. Streamyfin/Findroid используют текущий подключённый сервер.";
  };
  playerSelect.addEventListener("change", () => {
    localStorage.setItem("dubs-player", playerSelect.value);
    help();
    toast("Клиент сохранён: " + playerNames[playerSelect.value]);
  });
  help();
}
async function openPlayer(event, e) {
  const choice = localStorage.getItem("dubs-player") || "auto";
  if (choice === "auto") return;
  event.preventDefault();
  if (/DubsNative\/1\.0/.test(navigator.userAgent)) {
    toast("Обновите приложение Dubs до1.1 для выбора плеера");
    return;
  }
  try {
    const links = await json("/api/player-links/" + e.id);
    let target = links[choice];
    if (!target) throw Error("Для этой серии нет данных для выбранного клиента. Выберите другой клиент");
    if (choice === "browser") {
      if (/DubsNative/.test(navigator.userAgent)) target = "dubs-browser://open?url=" + encodeURIComponent(target);
      else {
        location.assign(target);
        return;
      }
    }
    location.href = target;
  } catch (error) {
    toast(error.message);
  }
}
const EVENT_TYPES = ["download_started", "torrent_complete", "library_ready", "new_episode", "upgrade_started", "upgrade_result"];
const EVENT_LABELS = { download_started: "Начало загрузки", torrent_complete: "Раздача скачана", library_ready: "Появилось в медиатеке", new_episode: "Новая серия", upgrade_started: "Ежедневная проверка замены", upgrade_result: "Результат замены" };
const NATIVE_APP = /DubsNative|DubsTV/.test(navigator.userAgent);
let eventsSettings = null, eventsSaving = false, eventsPolling = false, journalBefore = null, journalLoading = false;
function storeGet(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (e) {
    return fallback;
  }
}
function storeSet(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {}
}
function syncEventsControls(s) {
  const master = $("events-account-enabled");
  if (!master) return;
  const types = Array.isArray(s == null ? void 0 : s.types) ? s.types : [];
  master.disabled = eventsSaving;
  master.setAttribute("aria-checked", String((s == null ? void 0 : s.enabled) === true));
  for (const t of EVENT_TYPES) {
    const sw = document.querySelector('[data-event-type="' + t + '"]');
    if (sw) {
      sw.disabled = eventsSaving;
      sw.setAttribute("aria-checked", String(types.includes(t)));
    }
  }
  const titles = $("events-show-titles");
  if (titles) {
    titles.disabled = eventsSaving;
    titles.setAttribute("aria-checked", String((s == null ? void 0 : s.showTitles) === true));
  }
}
async function loadEventsSettings() {
  if (!$("events-account-enabled")) return;
  try {
    const d = await json("/api/notifications/settings");
    eventsSettings = d;
    syncEventsControls(d);
  } catch (e) {
    const status = $("events-status");
    if (status) status.textContent = "Настройки событий недоступны: " + e.message;
  }
}
async function postEventsSettings() {
  if (eventsSaving) return;
  eventsSaving = true;
  for (const sw of document.querySelectorAll("#events-account-enabled, #events-types .preference-switch")) sw.disabled = true;
  const types = EVENT_TYPES.filter((t) => {
    const sw = document.querySelector('[data-event-type="' + t + '"]');
    return sw && sw.getAttribute("aria-checked") === "true";
  });
  const body = new URLSearchParams({
    csrf,
    enabled: $("events-account-enabled").getAttribute("aria-checked") === "true" ? "true" : "false",
    types: JSON.stringify(types),
    show_titles: $("events-show-titles").getAttribute("aria-checked") === "true" ? "true" : "false"
  });
  try {
    const session = await json("/api/session");
    csrf = session.csrf;
    body.set("csrf", csrf);
    const d = await json("/api/notifications/settings", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body });
    eventsSettings = d;
    syncEventsControls(d);
    toast("Настройки событий аккаунта сохранены");
  } catch (e) {
    toast(e.message);
    await loadEventsSettings();
  } finally {
    eventsSaving = false;
    if (eventsSettings) syncEventsControls(eventsSettings);
  }
}
for (const sw of document.querySelectorAll("#events-account-enabled, #events-types .preference-switch")) {
  sw.addEventListener("click", () => {
    if (sw.disabled) return;
    sw.setAttribute("aria-checked", String(sw.getAttribute("aria-checked") !== "true"));
    postEventsSettings();
  });
}
const deviceSwitch = $("events-device-enabled");
if (deviceSwitch) {
  const note = $("events-device-note");
  const request = $("events-notify-request");
  if (NATIVE_APP) {
    deviceSwitch.hidden = true;
    if (request) request.hidden = true;
    if (note) note.textContent = "Уведомления на этом устройстве включаются в приложении Dubs. Переключатели выше задают типы событий для всего аккаунта.";
  } else if (!("Notification" in window)) {
    deviceSwitch.disabled = true;
    if (note) note.textContent = "Этот браузер не поддерживает уведомления. Приложение Dubs может показывать события в фоне.";
  } else if (Notification.permission === "denied") {
    deviceSwitch.disabled = true;
    if (note) note.textContent = "Уведомления запрещены в настройках браузера. Разрешите их для этого сайта или используйте приложение Dubs.";
  } else {
    deviceSwitch.setAttribute("aria-checked", String(storeGet("dubs-events-device", "off") === "on"));
    if (Notification.permission === "default" && request) {
      request.hidden = false;
      request.addEventListener("click", async () => {
        request.disabled = true;
        try {
          const result = await Notification.requestPermission();
          if (result === "granted") {
            storeSet("dubs-events-device", "on");
            deviceSwitch.setAttribute("aria-checked", "true");
            request.hidden = true;
            toast("Браузерные уведомления разрешены. События показываются, пока панель открыта");
          } else toast("Уведомления не разрешены. Дайте доступ позже или используйте приложение Dubs");
        } finally {
          request.disabled = false;
        }
      });
    }
    deviceSwitch.addEventListener("click", () => {
      if (deviceSwitch.disabled) return;
      const next = deviceSwitch.getAttribute("aria-checked") !== "true";
      if (next && Notification.permission !== "granted") {
        if (request && !request.hidden) request.focus();
        toast("Сначала разрешите уведомления в браузере");
        return;
      }
      storeSet("dubs-events-device", next ? "on" : "off");
      deviceSwitch.setAttribute("aria-checked", String(next));
      toast(next ? "События показываются, пока панель открыта" : "Уведомления на этом устройстве отключены");
    });
  }
}
function eventTime(ev) {
  try {
    return new Date(ev.created * 1e3).toLocaleString("ru-RU");
  } catch (e) {
    return "";
  }
}
function journalItem(ev) {
  const li = node("li", void 0, "journal-item");
  const head = node("div", void 0, "journal-head");
  head.append(node("span", EVENT_LABELS[ev.type] || "Событие", "journal-type is-" + (EVENT_LABELS[ev.type] ? ev.type : "unknown")));
  if (ev.created) {
    const time = node("time", eventTime(ev), "journal-time");
    try {
      time.setAttribute("datetime", new Date(ev.created * 1e3).toISOString());
    } catch (e) {}
    head.append(time);
  }
  li.append(head);
  if (ev.title) li.append(node("div", ev.title, "journal-title"));
  if (ev.message) li.append(node("div", ev.message, "journal-message"));
  if (Number.isSafeInteger(ev.episodeId) && ev.episodeId > 0 && ev.episodeId <= 9999999999) {
    const link = node("a", "Перейти к серии", "quiet journal-link");
    link.href = "#released?episode=" + ev.episodeId;
    li.append(link);
  }
  return li;
}
function openJournal() {
  const box = $("events-journal");
  if (!box) return;
  box.hidden = false;
  const toggle = $("events-journal-toggle");
  if (toggle) toggle.setAttribute("aria-expanded", "true");
  if (journalBefore === null) loadJournalPage();
}
async function loadJournalPage() {
  if (journalLoading) return;
  journalLoading = true;
  const list = $("events-journal-list");
  const older = $("events-journal-older");
  const empty = $("events-journal-empty");
  try {
    const suffix = journalBefore === null ? "" : "&before=" + journalBefore;
    const d = await json("/api/notifications/history?limit=30" + suffix);
    const items = (Array.isArray(d.events) ? d.events : []).filter((ev) => ev && Number.isFinite(ev.id));
    if (list) {
      if (journalBefore === null) list.replaceChildren();
      for (const ev of items) list.append(journalItem(ev));
    }
    if (empty) empty.hidden = !!(list && list.children.length);
    if (older) older.hidden = !(d.nextBefore && items.length);
    journalBefore = d.nextBefore || void 0;
  } catch (e) {
    toast(e.message);
  } finally {
    journalLoading = false;
  }
}
{
  const toggle = $("events-journal-toggle");
  toggle == null ? void 0 : toggle.addEventListener("click", () => {
    const box = $("events-journal");
    if (!box) return;
    if (box.hidden) openJournal();
    else {
      box.hidden = true;
      toggle.setAttribute("aria-expanded", "false");
    }
  });
  const older = $("events-journal-older");
  older == null ? void 0 : older.addEventListener("click", loadJournalPage);
}
function eventsEligible(ev, s) {
  return !!(s && s.enabled === true && Array.isArray(s.types) && s.types.includes(ev.type));
}
function announceEvent(ev, s) {
  if (NATIVE_APP) return;
  if (storeGet("dubs-events-device", "off") !== "on") return;
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  const showTitles = !!(s && s.showTitles === true);
  try {
    const instance = storeGet("dubs-events-instance", null);
    const n = new Notification(showTitles && ev.title ? ev.title : "Dubs", { body: EVENT_LABELS[ev.type] || "Событие медиатеки", tag: "dubs-event-" + instance + "-" + ev.id });
    n.addEventListener("click", () => {
      n.close();
      if (instance !== storeGet("dubs-events-instance", null)) return;
      location.hash = Number.isSafeInteger(ev.episodeId) && ev.episodeId > 0 && ev.episodeId <= 9999999999 ? "#released?episode=" + ev.episodeId : "#settings?section=events";
      window.focus();
    });
  } catch (e) {}
}
function validEventInstance(value) { return typeof value === "string" && /^[a-fA-F0-9]{32}$/.test(value); }
function applyEvents(d) {
  if (!d || typeof d !== "object") return;
  const instance = validEventInstance(d.instanceId) ? d.instanceId : null;
  if (instance === null || !Number.isSafeInteger(d.cursor) || d.cursor < 0) return;
  const previous = storeGet("dubs-events-instance", null);
  const rebase = instance !== previous;
  if (rebase) { storeSet("dubs-events-seen", []); const list = $("events-journal-list"); if (list) list.replaceChildren(); journalBefore = null; }
  if (instance !== null) storeSet("dubs-events-instance", instance);
  if (d.cursor != null) storeSet("dubs-events-cursor", Number(d.cursor));
  if (d.settings) {
    eventsSettings = d.settings;
    syncEventsControls(d.settings);
  }
  const seen = new Set(storeGet("dubs-events-seen", []));
  const fresh = (Array.isArray(d.events) ? d.events : []).filter((ev) => ev && Number.isFinite(ev.id) && !seen.has(ev.id));
  for (const ev of fresh) seen.add(ev.id);
  storeSet("dubs-events-seen", [...seen].slice(-200));
  if (rebase) return;
  const list = $("events-journal-list");
  const box = $("events-journal");
  if (list && box && !box.hidden && fresh.length && journalBefore === void 0) {
    for (const ev of fresh.slice().reverse()) list.prepend(journalItem(ev));
    const empty = $("events-journal-empty");
    if (empty) empty.hidden = !!list.children.length;
  }
  for (const ev of fresh) if (eventsEligible(ev, d.settings)) announceEvent(ev, d.settings);
}
async function pollEvents() {
  if (eventsPolling || document.hidden) return;
  if (!$("events-panel")) return;
  eventsPolling = true;
  try {
    if (storeGet("dubs-events-cursor", null) === null) {
      const d = await json("/api/notifications/history?limit=1");
      storeSet("dubs-events-instance", validEventInstance(d.instanceId) ? d.instanceId : null);
      storeSet("dubs-events-cursor", Number(d.cursor) || 0);
      storeSet("dubs-events-seen", []);
      return;
    }
    const d = await json("/api/notifications/events?after=" + storeGet("dubs-events-cursor", 0) + "&limit=100");
    applyEvents(d);
  } catch (e) {
  } finally {
    eventsPolling = false;
  }
}
setInterval(pollEvents, 6e4);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) pollEvents();
});
pollEvents();
