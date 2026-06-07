// SunSide — Pyodide-powered SPA
// Lädt Pyodide + sunside-Paket im Browser. Alle HTTP-Calls (Nominatim, Overpass,
// Open-Meteo) laufen in JS; Python rechnet nur.

const PYODIDE_VERSION = "0.27.2";
const SUNSIDE_FILES = [
  "sunside/__init__.py",
  "sunside/models.py",
  "sunside/flight.py",
  "sunside/browser_api.py",
  "sunside/weather.py",
  "sunside/sun_analysis/__init__.py",
  "sunside/sun_analysis/analyzer.py",
  "sunside/sun_analysis/calculator.py",
  "sunside/sun_analysis/sampler.py",
  "sunside/scenic/__init__.py",
  "sunside/scenic/models.py",
  "sunside/scenic/poi_query.py",
  "sunside/scenic/poi_scorer.py",
  "sunside/scenic/analyzer.py",
];

// ---- DOM ----
const $ = (id) => document.getElementById(id);
const els = {
  form: $("form"), provider: $("provider"), providerHint: $("provider-hint"),
  emptyState: $("empty-state"),
  origin: $("origin"), destination: $("destination"),
  originLabel: $("origin-label"), destinationLabel: $("destination-label"),
  originSuggestions: $("origin-suggestions"), destinationSuggestions: $("destination-suggestions"),
  previewMap: $("preview-map"),
  gpxField: $("gpx-field"), gpx: $("gpx"),
  gtfsField: $("gtfs-field"),
  gtfsFeed: $("gtfs-feed"),
  gtfsBoard: $("gtfs-board"),
  gtfsAlight: $("gtfs-alight"),
  gtfsBoardSuggestions: $("gtfs-board-suggestions"),
  gtfsAlightSuggestions: $("gtfs-alight-suggestions"),
  gtfsTrip: $("gtfs-trip"),
  gtfsHint: $("gtfs-hint"),
  od: $("origin-destination"),
  depDate: $("dep-date"), depTime: $("dep-time"),
  durationField: $("duration-field"), duration: $("duration"), durationValue: $("duration-value"),
  interval: $("interval"), weather: $("weather"), intensity: $("intensity"),
  terrain: $("terrain"), converged: $("converged"), scenic: $("scenic"),
  submit: $("submit"), status: $("status"),
  result: $("result"), resultSide: $("result-side"), resultPct: $("result-pct"),
  resultMeta: $("result-meta"), recommendation: $("recommendation"),
  metricInterval: $("metric-interval"), metricSegments: $("metric-segments"),
  metricClouds: $("metric-clouds"), metricSun: $("metric-sun"),
  metricTunnel: $("metric-tunnel"), metricTerrain: $("metric-terrain"),
  tracePanel: $("trace-panel"), traceTable: $("trace-table")?.querySelector("tbody"),
  segTable: $("segments-table").querySelector("tbody"),
  runtimePill: $("runtime-pill"),
  installBtn: $("install-btn"),
  updateBanner: $("update-banner"),
  updateBtn: $("update-btn"),
  updateDismiss: $("update-dismiss"),
  offlinePill: $("offline-pill"),
};

let pyodide = null;
let map = null;
let routeLayer = null;
let segmentLayer = null;
let previewMap = null;
let previewLayer = null;
let scenicLayer = null;
let scenicSearchLayer = null;
let _scenicSearchTimer = null;
let _originCoords = null;
let _lastResult = null;
let _lastPref = null;
let _lastScenic = null;
const TERRAIN_COORD_LIMIT = 8000;
const placeCache = new Map();
const optionState = { origin: [], destination: [] };
const selectedPlace = { origin: null, destination: null };
const activeSuggestion = { origin: -1, destination: -1 };

function describeError(err) {
  if (!err) return "unbekannter Fehler";
  if (typeof err === "string") return err;
  // Pyodide / Emscripten ErrnoError: errno 44 = ENOENT, 20 = EEXIST, 13 = EACCES, 28 = ENOSPC
  if (err && err.name === "ErrnoError") {
    const map = { 13: "EACCES (Zugriff verweigert)", 17: "EEXIST", 20: "EEXIST (existiert)", 28: "ENOSPC (kein Platz)", 44: "ENOENT (nicht gefunden)" };
    return `ErrnoError ${err.errno} ${map[err.errno] || ""} — ${err.message || ""}`.trim();
  }
  if (err instanceof Error) return err.message + (err.stack ? `\n${err.stack.split("\n").slice(0, 3).join("\n")}` : "");
  try { return JSON.stringify(err); } catch { return String(err); }
}

// ---- Helpers ----
// Expose resolved places so the favorites UI (inline script) can save/restore coords
window._sunsideGetPlaces = () => ({
  origin:      selectedPlace.origin      ? { ...selectedPlace.origin }      : null,
  destination: selectedPlace.destination ? { ...selectedPlace.destination } : null,
});
window._sunsideSetPlaces = ({ origin, destination }) => {
  if (origin)      selectedPlace.origin      = origin;
  if (destination) selectedPlace.destination = destination;
};
window._sunsideResolvePlace = async (name) => {
  const results = await searchPlaces(name, { stationOnly: false, limit: 3 });
  return results[0] ?? null;
};

function setStatus(msg, kind = "muted") {
  if (!msg) { els.status.classList.add("hidden"); return; }
  els.status.className = `status ${kind}`;
  els.status.textContent = msg;
  els.status.classList.remove("hidden");
}

function setSubmit(text, enabled) {
  els.submit.textContent = text;
  els.submit.disabled = !enabled;
}

let _calcTimer = null;
function startCalcTimer() {
  const t0 = Date.now();
  _calcTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    els.submit.textContent = `Berechne… ${s}s`;
  }, 1000);
}
function stopCalcTimer() {
  if (_calcTimer) { clearInterval(_calcTimer); _calcTimer = null; }
}

function prefersReducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

function isCoarsePointer() {
  return window.matchMedia?.("(pointer: coarse)").matches === true;
}

function isStandalonePwa() {
  return window.matchMedia?.("(display-mode: standalone)").matches === true
    || window.navigator.standalone === true;
}

function formatInterval(metres) {
  if (metres == null || Number.isNaN(Number(metres))) return "-";
  const value = Number(metres);
  if (value >= 1000) return `${(value / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} km`;
  return `${Math.round(value).toLocaleString("de-DE")} m`;
}

function formatPct(value) {
  return value == null ? "-" : `${Number(value).toFixed(0)} %`;
}

function analysisInterval() {
  if (!els.interval || els.interval.value === "auto" || els.converged.checked) return null;
  return Number.parseInt(els.interval.value, 10);
}

/**
 * Returns the local timezone offset string (e.g. "+02:00" or "-05:00") for a
 * given local date/time. Using the actual date/time (not "now") is important
 * because DST may differ between today and the chosen departure date.
 */
function localTzOffset(year, month, day, hour, minute) {
  // new Date(year, month-1, ...) creates the date in the *local* timezone, so
  // getTimezoneOffset() gives the correct DST-aware offset for that moment.
  const d = new Date(year, month - 1, day, hour, minute, 0);
  const totalMinutes = -d.getTimezoneOffset(); // positive = east of UTC
  const sign = totalMinutes >= 0 ? "+" : "-";
  const absMin = Math.abs(totalMinutes);
  const hh = Math.floor(absMin / 60).toString().padStart(2, "0");
  const mm = (absMin % 60).toString().padStart(2, "0");
  return `${sign}${hh}:${mm}`;
}

// Initialize date/time fields
(function initDateTime() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  els.depDate.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  els.depTime.value = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
})();

els.duration.addEventListener("input", () => {
  els.durationValue.textContent = parseFloat(els.duration.value).toFixed(1);
});

els.converged.addEventListener("change", () => {
  els.interval.disabled = els.converged.checked;
});
els.interval.disabled = els.converged.checked; // sync on page load

// ---- Location button ----
const _locateBtn = $("locate-btn");
const _LOCATE_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="8" opacity="0.3"/></svg>`;
const _CLEAR_ICON  = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;

function _clearLocatedOrigin() {
  _originCoords = null;
  els.origin.value = "";
  els.origin.readOnly = false;
  selectedPlace.origin = null;
  if (_locateBtn) {
    _locateBtn.classList.remove("active");
    _locateBtn.title = "Aktuellen Standort verwenden";
    _locateBtn.innerHTML = _LOCATE_ICON;
  }
}

function _setLocatedOrigin(lat, lon) {
  _originCoords = { lat, lon };
  els.origin.value = "Mein Standort";
  els.origin.readOnly = true;
  selectedPlace.origin = { lat, lon, display_name: "Mein Standort" };
  if (_locateBtn) {
    _locateBtn.classList.add("active");
    _locateBtn.title = "Standort löschen";
    _locateBtn.innerHTML = _CLEAR_ICON;
  }
  refreshSuggestionsAndPreview();
}

_locateBtn?.addEventListener("click", () => {
  if (_originCoords) { _clearLocatedOrigin(); return; }
  if (!navigator.geolocation) {
    setStatus("Geolocation wird von diesem Browser nicht unterstützt.", "error");
    return;
  }
  _locateBtn.classList.add("loading");
  _locateBtn.disabled = true;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      _locateBtn.classList.remove("loading");
      _locateBtn.disabled = false;
      _setLocatedOrigin(pos.coords.latitude, pos.coords.longitude);
    },
    (err) => {
      _locateBtn.classList.remove("loading");
      _locateBtn.disabled = false;
      const msgs = { 1: "Standortzugriff verweigert.", 2: "Standort konnte nicht ermittelt werden.", 3: "Standortabfrage Timeout." };
      setStatus(msgs[err.code] || "Standortfehler.", "error");
    },
    { timeout: 10000, maximumAge: 60000 },
  );
});

// ---- Clear button ----
function clearForm() {
  _clearLocatedOrigin();
  els.origin.value = "";
  els.destination.value = "";
  selectedPlace.origin = null;
  selectedPlace.destination = null;
  hideSuggestions();
  if (els.gtfsBoard) els.gtfsBoard.value = "";
  if (els.gtfsAlight) els.gtfsAlight.value = "";
  gtfsState.boardStop = null;
  gtfsState.alightStop = null;
  gtfsState.trip = null;
  if (els.gtfsTrip) { els.gtfsTrip.innerHTML = "<option>Erst Halt und Datum wählen</option>"; els.gtfsTrip.disabled = true; }
  if (previewLayer) { previewLayer.remove(); previewLayer = null; }
  els.origin.dispatchEvent(new Event("input"));
  els.destination.dispatchEvent(new Event("input"));
}
$("clear-btn")?.addEventListener("click", clearForm);

els.provider.addEventListener("change", updateProviderUI);
function updateProviderUI() {
  const v = els.provider.value;
  selectedPlace.origin = null;
  selectedPlace.destination = null;
  hideSuggestions();
  els.gpxField.classList.toggle("hidden", v !== "gpx");
  if (els.gtfsField) els.gtfsField.classList.toggle("hidden", v !== "gtfs");
  els.od.classList.toggle("hidden", v === "gpx" || v === "gtfs");
  els.durationField.classList.toggle("hidden", v !== "straight");
  els.previewMap.classList.toggle("hidden", v === "gpx" || v === "gtfs");
  if (v === "gtfs") ensureGtfsFeeds();

  const labels = {
    osm: ["Startbahnhof", "Zielbahnhof", "Berlin", "Muenchen"],
    road: ["Startort", "Zielort", "Berlin ZOB", "Muenchen ZOB"],
    car: ["Startort", "Zielort", "Berlin", "Muenchen"],
    straight: ["Startort oder Flughafen", "Zielort oder Flughafen", "Berlin BER", "Muenchen Flughafen"],
  }[v];
  if (labels) {
    els.originLabel.textContent = labels[0];
    els.destinationLabel.textContent = labels[1];
    els.origin.placeholder = labels[2];
    els.destination.placeholder = labels[3];
    // Placeholders update dynamically; no auto-fill of real values
  }

  els.providerHint.textContent = ({
    osm: "Gleisgeometrie via OSM Overpass.",
    road: "Busrouting via OSRM.",
    car: "Strassenrouting via OSRM.",
    gtfs: "Konkrete Fahrt aus dem GTFS-API-Endpoint.",
    straight: "Grosskreis zwischen zwei geocodierten Punkten.",
    gpx: "Eigene GPX-Route mit Zeitmodell.",
  })[v];

  // Hide locate button in GTFS/GPX modes (no coordinate origin possible there)
  if (_locateBtn) _locateBtn.hidden = (v === "gtfs" || v === "gpx");
  if ((v === "gtfs" || v === "gpx") && _originCoords) _clearLocatedOrigin();

  // Only refresh if user already typed something (avoid hammering Nominatim on init)
  if (els.origin.value.trim().length >= 3 || els.destination.value.trim().length >= 3) {
    refreshSuggestionsAndPreview();
  }
}
updateProviderUI();

const debouncedRefreshSuggestions = debounce(refreshSuggestionsAndPreview, 600);
els.origin.addEventListener("input", () => {
  if (_originCoords) _clearLocatedOrigin();
  selectedPlace.origin = null;
  debouncedRefreshSuggestions();
});
els.destination.addEventListener("input", () => {
  selectedPlace.destination = null;
  debouncedRefreshSuggestions();
});
els.origin.addEventListener("focus", () => showSuggestions("origin"));
els.destination.addEventListener("focus", () => showSuggestions("destination"));
els.origin.addEventListener("keydown", (event) => handleSuggestionKey(event, "origin"));
els.destination.addEventListener("keydown", (event) => handleSuggestionKey(event, "destination"));
document.addEventListener("click", (event) => {
  if (!event.target.closest(".autocomplete")) hideSuggestions();
});

const refreshMapSizes = debounce(() => {
  if (previewMap) previewMap.invalidateSize();
  if (map) map.invalidateSize();
}, 180);
window.addEventListener("orientationchange", refreshMapSizes);
window.visualViewport?.addEventListener("resize", refreshMapSizes);

// ---- HTTP wrappers (CORS-friendly public endpoints) ----
function normalizeText(value) {
  return value.toLowerCase()
    .replaceAll("ß", "ss")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[—,]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function stationQueries(query) {
  const normalized = normalizeText(query);
  if (["bahnhof", "hauptbahnhof", "hbf", "station"].some((word) => normalized.includes(word))) {
    return [query];
  }
  return [query, `${query} Hauptbahnhof`, `${query} Bahnhof`, `${query} station`];
}

function looksLikeStation(item) {
  const category = item.category || item.class || "";
  const type = item.type || "";
  const display = normalizeText(item.display_name || "");
  if (category === "railway") return ["station", "halt", "tram_stop", "subway_entrance"].includes(type);
  if (category === "public_transport") return ["station", "stop_position", "platform"].includes(type);
  return ["bahnhof", "hauptbahnhof", "station"].some((token) => display.includes(token));
}

function significantTokens(query) {
  const ignored = new Set(["bahnhof", "hauptbahnhof", "hbf", "station", "train", "railway"]);
  return normalizeText(query).split(" ").filter((token) => token.length >= 3 && !ignored.has(token));
}

function stationScore(query, item) {
  const haystack = normalizeText(`${item.name || ""} ${item.display_name || ""}`);
  const tokens = significantTokens(query);
  let score = Number(item.importance || 0) * 10;
  if (tokens.every((token) => haystack.includes(token))) score += 80;
  if (haystack.includes("hauptbahnhof") || haystack.includes(" hbf")) score += 40;
  if (haystack.includes("deutschland") || haystack.includes("osterreich") || haystack.includes("schweiz")) score += 20;
  if ((item.category || item.class) === "railway") score += 15;
  if (item.type === "station") score += 10;
  return score;
}

function labelForPlace(item) {
  const name = item.namedetails?.name || item.name || item.display_name || "Ort";
  return name !== item.display_name ? `${name} — ${item.display_name}` : item.display_name;
}

async function _fetchNominatim(searchQuery, { stationOnly, limit }) {
  const url = new URL("https://nominatim.openstreetmap.org/search");
  url.search = new URLSearchParams({
    q: searchQuery,
    format: "jsonv2",
    limit: String(stationOnly ? Math.max(limit, 30) : limit),
    addressdetails: "1",
    namedetails: "1",
  }).toString();
  const r = await fetch(url, { headers: { "Accept": "application/json" } });
  if (!r.ok) throw new Error(`Geocoding-Fehler: HTTP ${r.status}`);
  return r.json();
}

async function searchPlaces(query, { stationOnly = false, limit = 8 } = {}) {
  if (query.trim().length < 3) return [];
  const cacheKey = `${stationOnly}:${limit}:${query.trim().toLowerCase()}`;
  if (placeCache.has(cacheKey)) return placeCache.get(cacheKey);

  const queries = stationOnly ? stationQueries(query) : [query];
  const seen = new Set();
  const results = [];
  const tokens = significantTokens(query);

  for (const searchQuery of queries) {
    // Only fire follow-up queries (Hauptbahnhof, etc.) if first returned nothing
    if (results.length > 0 && stationOnly) break;

    const data = await _fetchNominatim(searchQuery, { stationOnly, limit });
    for (const item of data) {
      if (stationOnly && !looksLikeStation(item)) continue;
      const haystack = normalizeText(`${item.name || ""} ${item.display_name || ""}`);
      if (stationOnly && tokens.length && !tokens.some((token) => haystack.includes(token))) continue;
      const key = `${item.display_name}|${Number(item.lat).toFixed(5)}|${Number(item.lon).toFixed(5)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      results.push({
        ...item,
        lat: parseFloat(item.lat),
        lon: parseFloat(item.lon),
        label: labelForPlace(item),
      });
    }
  }
  if (stationOnly) results.sort((a, b) => stationScore(query, b) - stationScore(query, a));
  const sliced = results.slice(0, limit);
  placeCache.set(cacheKey, sliced);
  return sliced;
}

async function geocode(query, { stationOnly = false } = {}) {
  const data = await searchPlaces(query, { stationOnly, limit: 8 });
  if (!data.length) throw new Error(`${stationOnly ? "Station" : "Ort"} nicht gefunden: ${query}`);
  return [data[0].lat, data[0].lon];
}

async function resolveEndpoint(input, side) {
  const selected = selectedPlace[side];
  if (selected && [selected.label, selected.display_name, selected.name].includes(input)) return selected;

  const options = optionState[side] || [];
  const exact = options.find((item) => item.label === input || item.display_name === input || item.name === input);
  if (exact) return exact;
  const stationOnly = els.provider.value === "osm";
  const candidates = await searchPlaces(input, { stationOnly, limit: 8 });
  if (!candidates.length) throw new Error(`${stationOnly ? "Station" : "Ort"} nicht gefunden: ${input}`);
  return candidates[0];
}

function suggestionTarget(side) {
  return side === "origin" ? els.originSuggestions : els.destinationSuggestions;
}

function inputForSide(side) {
  return side === "origin" ? els.origin : els.destination;
}

function renderSuggestionList(side, items, { visible = true } = {}) {
  const target = suggestionTarget(side);
  target.innerHTML = "";
  activeSuggestion[side] = -1;

  if (!items.length && inputForSide(side).value.trim().length >= 3) {
    const empty = document.createElement("div");
    empty.className = "suggestion-empty";
    empty.textContent = els.provider.value === "osm" ? "Keine passende Station gefunden" : "Kein passender Ort gefunden";
    target.appendChild(empty);
    target.classList.toggle("hidden", !visible);
    return;
  }

  for (const [index, item] of items.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion";
    button.setAttribute("role", "option");
    button.dataset.index = String(index);

    const title = document.createElement("span");
    title.className = "suggestion-title";
    title.textContent = item.name || item.label;

    const meta = document.createElement("span");
    meta.className = "suggestion-meta";
    meta.textContent = item.display_name || item.label;

    button.append(title, meta);
    button.addEventListener("mousedown", (event) => {
      event.preventDefault();
      chooseSuggestion(side, item);
    });
    // Touch: use touchend so the list can scroll freely; only select on tap (< 10 px movement)
    let touchStartY = 0;
    button.addEventListener("touchstart", (event) => {
      touchStartY = event.touches[0].clientY;
    }, { passive: true });
    button.addEventListener("touchend", (event) => {
      if (Math.abs(event.changedTouches[0].clientY - touchStartY) < 10) {
        event.preventDefault();
        chooseSuggestion(side, item);
      }
    });
    target.appendChild(button);
  }

  target.classList.toggle("hidden", !visible || items.length === 0);
}

function chooseSuggestion(side, item) {
  const input = inputForSide(side);
  selectedPlace[side] = item;
  input.value = item.label;
  optionState[side] = [item, ...optionState[side].filter((candidate) => candidate !== item)];
  hideSuggestions(side);
  if (isCoarsePointer()) input.blur();
  refreshSuggestionsAndPreview({ showLists: false });
}

function showSuggestions(side) {
  const target = suggestionTarget(side);
  if (target.children.length) target.classList.remove("hidden");
}

function hideSuggestions(side = null) {
  const sides = side ? [side] : ["origin", "destination"];
  for (const currentSide of sides) {
    suggestionTarget(currentSide).classList.add("hidden");
    activeSuggestion[currentSide] = -1;
    updateActiveSuggestion(currentSide);
  }
  // Also close GTFS stop suggestions when hiding all
  if (!side) {
    els.gtfsBoardSuggestions?.classList.add("hidden");
    els.gtfsAlightSuggestions?.classList.add("hidden");
  }
}

function handleSuggestionKey(event, side) {
  const options = optionState[side] || [];
  if (!options.length) return;

  if (event.key === "ArrowDown") {
    event.preventDefault();
    activeSuggestion[side] = (activeSuggestion[side] + 1) % options.length;
    updateActiveSuggestion(side);
    showSuggestions(side);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    activeSuggestion[side] = (activeSuggestion[side] - 1 + options.length) % options.length;
    updateActiveSuggestion(side);
    showSuggestions(side);
  } else if (event.key === "Enter" && activeSuggestion[side] >= 0) {
    event.preventDefault();
    chooseSuggestion(side, options[activeSuggestion[side]]);
  } else if (event.key === "Escape") {
    hideSuggestions(side);
  }
}

function updateActiveSuggestion(side) {
  const buttons = suggestionTarget(side).querySelectorAll(".suggestion");
  buttons.forEach((button, index) => {
    button.classList.toggle("active", index === activeSuggestion[side]);
  });
}

async function refreshSuggestionsAndPreview(options = {}) {
  const showLists = options.showLists !== false;
  if (els.provider.value === "gpx") return;
  const stationOnly = els.provider.value === "osm";
  try {
    // Sequential to avoid Nominatim rate-limit (1 req/s policy)
    const originOptions = await searchPlaces(els.origin.value.trim(), { stationOnly, limit: 8 });
    const destinationOptions = await searchPlaces(els.destination.value.trim(), { stationOnly, limit: 8 });
    optionState.origin = originOptions;
    optionState.destination = destinationOptions;
    renderSuggestionList("origin", originOptions, { visible: showLists && document.activeElement === els.origin });
    renderSuggestionList("destination", destinationOptions, { visible: showLists && document.activeElement === els.destination });
    const originPreview = selectedPlace.origin || originOptions[0];
    const destinationPreview = selectedPlace.destination || destinationOptions[0];
    if (originPreview && destinationPreview) {
      renderPreview([originPreview.lat, originPreview.lon], [destinationPreview.lat, destinationPreview.lon]);
    }
  } catch (err) {
    console.warn("preview lookup failed", err);
  }
}

function debounce(fn, delay) {
  let handle;
  return (...args) => {
    clearTimeout(handle);
    handle = setTimeout(() => fn(...args), delay);
  };
}

function renderPreview(start, end) {
  els.previewMap.classList.remove("hidden");
  if (!previewMap) {
    previewMap = L.map("preview-map", { zoomControl: false, attributionControl: false, preferCanvas: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      updateWhenIdle: true,
      keepBuffer: 1,
    }).addTo(previewMap);
  }
  if (previewLayer) previewLayer.remove();
  const dashed = els.provider.value !== "straight";
  const color = (els.provider.value === "road" || els.provider.value === "car") ? "#16a34a" : "#2563eb";
  previewLayer = L.layerGroup([
    L.marker(start),
    L.marker(end),
    L.polyline([start, end], { color, weight: 4, opacity: 0.75, dashArray: dashed ? "8,8" : null }),
  ]).addTo(previewMap);
  previewMap.fitBounds(L.latLngBounds([start, end]), { padding: [18, 18] });
  setTimeout(() => previewMap.invalidateSize(), 0);
}

async function fetchOsrmRoute(start, end) {
  const url = new URL(`https://router.project-osrm.org/route/v1/driving/${start[1]},${start[0]};${end[1]},${end[0]}`);
  url.search = new URLSearchParams({ overview: "full", geometries: "geojson" }).toString();
  const r = await fetch(url);
  if (!r.ok) throw new Error(`OSRM HTTP ${r.status}`);
  const data = await r.json();
  const route = data.routes?.[0];
  if (!route?.geometry?.coordinates?.length) throw new Error("Keine Strassenroute gefunden.");
  return {
    coordinates: route.geometry.coordinates.map(([lon, lat]) => [lat, lon]),
    duration: Number(route.duration || 0),
  };
}

async function legacyGeocode(query) {
  const url = new URL("https://nominatim.openstreetmap.org/search");
  url.search = new URLSearchParams({
    q: query, format: "jsonv2", limit: "1", addressdetails: "0",
  }).toString();
  const r = await fetch(url, { headers: { "Accept": "application/json" } });
  if (!r.ok) throw new Error(`Geocoding ${query}: HTTP ${r.status}`);
  const data = await r.json();
  if (!data.length) throw new Error(`Ort nicht gefunden: ${query}`);
  return [parseFloat(data[0].lat), parseFloat(data[0].lon)];
}

// ---- GTFS API client (PWA-only) ----
function gtfsApiBase() {
  // Order: ?gtfs_api=... in URL > localStorage > window.SUNSIDE_GTFS_API > auto-detect > /api
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("gtfs_api");
  if (fromQuery) {
    localStorage.setItem("sunside_gtfs_api", fromQuery);
    return fromQuery.replace(/\/$/, "");
  }
  const stored = localStorage.getItem("sunside_gtfs_api");
  if (stored) return stored.replace(/\/$/, "");
  if (window.SUNSIDE_GTFS_API) return String(window.SUNSIDE_GTFS_API).replace(/\/$/, "");
  // Local dev: frontend on :8000, API on :8001
  if (window.location.port === "8000") return "http://localhost:8001/api";
  return "/api";
}

const GTFS_FEED_LABELS = {
  db:      "Deutsche Bahn",
  oebb:    "ÖBB",
  flixbus: "Flixbus",
};
function gtfsFeedLabel(name) {
  return GTFS_FEED_LABELS[name] || name;
}

let _gtfsFeedsLoaded = false;
function autoSelectGtfsFeed() {
  const BUS_FEEDS = ["flixbus", "bus"];
  const gtfsMode = document.getElementById("provider")?.dataset?.gtfsMode;
  const isBusMode = gtfsMode === "road";  // road = bus; car has no GTFS
  for (const opt of Array.from(els.gtfsFeed.options)) {
    const isBusFeed = BUS_FEEDS.some(k => opt.value.toLowerCase().includes(k));
    if (isBusMode ? isBusFeed : !isBusFeed) { els.gtfsFeed.value = opt.value; return; }
  }
}
async function ensureGtfsFeeds() {
  if (_gtfsFeedsLoaded) { autoSelectGtfsFeed(); return; }
  const base = gtfsApiBase();
  els.gtfsHint.className = "status info";
  els.gtfsHint.textContent = `Verbinde zu ${base} …`;
  try {
    const r = await fetch(`${base}/gtfs/feeds`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const body = await r.json();
    if (!body.feeds || !body.feeds.length) {
      els.gtfsHint.className = "status warn";
      els.gtfsHint.textContent = "Keine GTFS-Feeds am Endpoint vorhanden.";
      return;
    }
    while (els.gtfsFeed.firstChild) els.gtfsFeed.removeChild(els.gtfsFeed.firstChild);
    for (const f of body.feeds) {
      const opt = document.createElement("option");
      opt.value = f.name;
      opt.textContent = gtfsFeedLabel(f.name);
      els.gtfsFeed.appendChild(opt);
    }
    autoSelectGtfsFeed();
    els.gtfsHint.className = "status muted";
    els.gtfsHint.textContent = `${body.feeds.length} Feed(s) via ${base}`;
    _gtfsFeedsLoaded = true;
    refreshGtfsTrips();
  } catch (err) {
    // If a stored custom endpoint fails, clear it and retry once with auto-detection
    const stored = localStorage.getItem("sunside_gtfs_api");
    if (stored) {
      localStorage.removeItem("sunside_gtfs_api");
      const fallback = window.location.port === "8000" ? "http://localhost:8001/api" : "/api";
      els.gtfsHint.className = "status warn";
      els.gtfsHint.textContent = `Gespeicherter Endpoint fehlgeschlagen, versuche ${fallback} …`;
      try {
        const r2 = await fetch(`${fallback}/gtfs/feeds`);
        if (!r2.ok) throw new Error(`HTTP ${r2.status}`);
        const body2 = await r2.json();
        if (!body2.feeds || !body2.feeds.length) {
          els.gtfsHint.className = "status warn";
          els.gtfsHint.textContent = "Keine GTFS-Feeds am Endpoint vorhanden.";
          return;
        }
        while (els.gtfsFeed.firstChild) els.gtfsFeed.removeChild(els.gtfsFeed.firstChild);
        for (const f of body2.feeds) {
          const opt = document.createElement("option");
          opt.value = f.name;
          opt.textContent = gtfsFeedLabel(f.name);
          els.gtfsFeed.appendChild(opt);
        }
        els.gtfsHint.className = "status muted";
        els.gtfsHint.textContent = `${body2.feeds.length} Feed(s) via ${fallback}`;
        _gtfsFeedsLoaded = true;
        refreshGtfsTrips();
        return;
      } catch (err2) {
        els.gtfsHint.className = "status error";
        els.gtfsHint.textContent = `Fehler: ${fallback}/gtfs/feeds — ${err2.message}`;
        return;
      }
    }
    els.gtfsHint.className = "status error";
    els.gtfsHint.textContent = `Fehler: ${base}/gtfs/feeds — ${err.message}`;
  }
}

async function gtfsSearchStops(feed, q) {
  if (!q || q.length < 2) return [];
  const r = await fetch(`${gtfsApiBase()}/gtfs/${encodeURIComponent(feed)}/stops?q=${encodeURIComponent(q)}`);
  if (!r.ok) throw new Error(`stops HTTP ${r.status}`);
  return (await r.json()).stops || [];
}

async function gtfsFindTrips(feed, board, alight, dateIso) {
  const r = await fetch(`${gtfsApiBase()}/gtfs/${encodeURIComponent(feed)}/trips?board=${encodeURIComponent(board)}&alight=${encodeURIComponent(alight)}&date=${encodeURIComponent(dateIso)}`);
  if (!r.ok) throw new Error(`trips HTTP ${r.status}`);
  return (await r.json()).trips || [];
}

async function gtfsFetchRoute(feed, tripId, board, alight, serviceDate) {
  const r = await fetch(`${gtfsApiBase()}/gtfs/${encodeURIComponent(feed)}/route?trip_id=${encodeURIComponent(tripId)}&board=${encodeURIComponent(board)}&alight=${encodeURIComponent(alight)}&service_date=${encodeURIComponent(serviceDate)}`);
  if (!r.ok) throw new Error(`route HTTP ${r.status}`);
  return (await r.json()).points || [];
}

const gtfsState = { boardStop: null, alightStop: null, trip: null };

function setupGtfsAutocomplete(input, suggestionsEl, key) {
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      gtfsState[key] = null;
      const q = input.value.trim();
      if (q.length < 2 || !els.gtfsFeed.value) return suggestionsEl.classList.add("hidden");
      try {
        const stops = await gtfsSearchStops(els.gtfsFeed.value, q);
        suggestionsEl.innerHTML = "";
        for (const s of stops.slice(0, 12)) {
          const div = document.createElement("div");
          div.className = "suggestion";
          div.textContent = s.name;
          div.addEventListener("click", () => {
            input.value = s.name;
            gtfsState[key] = s;
            suggestionsEl.classList.add("hidden");
            refreshGtfsTrips();
          });
          suggestionsEl.appendChild(div);
        }
        suggestionsEl.classList.toggle("hidden", !stops.length);
      } catch (err) {
        console.error("gtfs stop search:", err);
      }
    }, 250);
  });
  input.addEventListener("blur", () => setTimeout(() => suggestionsEl.classList.add("hidden"), 200));
}

async function refreshGtfsTrips() {
  if (!gtfsState.boardStop || !gtfsState.alightStop) {
    els.gtfsTrip.innerHTML = "<option>Erst Halt + Datum waehlen</option>";
    els.gtfsTrip.disabled = true;
    return;
  }
  const dateIso = els.depDate.value;
  els.gtfsTrip.disabled = true;
  els.gtfsTrip.innerHTML = "<option>Lade...</option>";
  try {
    const trips = await gtfsFindTrips(
      els.gtfsFeed.value,
      gtfsState.boardStop.stop_id,
      gtfsState.alightStop.stop_id,
      dateIso,
    );
    if (!trips.length) {
      els.gtfsTrip.innerHTML = "<option>Keine Verbindung an diesem Tag</option>";
      gtfsState.trip = null;
      return;
    }
    els.gtfsTrip.innerHTML = "";
    for (const t of trips) {
      const opt = document.createElement("option");
      opt.value = t.trip_id;
      opt.dataset.payload = JSON.stringify(t);
      opt.textContent = t.label || `${t.trip_id} ab ${t.board_departure}`;
      els.gtfsTrip.appendChild(opt);
    }
    els.gtfsTrip.disabled = false;
    gtfsState.trip = trips[0];
    els.gtfsTrip.onchange = () => {
      const sel = els.gtfsTrip.options[els.gtfsTrip.selectedIndex];
      gtfsState.trip = sel ? JSON.parse(sel.dataset.payload) : null;
    };
  } catch (err) {
    els.gtfsTrip.innerHTML = `<option>Fehler: ${describeError(err)}</option>`;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  if (els.gtfsBoard) setupGtfsAutocomplete(els.gtfsBoard, els.gtfsBoardSuggestions, "boardStop");
  if (els.gtfsAlight) setupGtfsAutocomplete(els.gtfsAlight, els.gtfsAlightSuggestions, "alightStop");
  if (els.depDate) els.depDate.addEventListener("change", refreshGtfsTrips);
});

const OVERPASS_URLS = [
  "https://overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
];

async function fetchOverpassRail(start, end, bufferDeg = 0.25) {
  const south = Math.min(start[0], end[0]) - bufferDeg;
  const north = Math.max(start[0], end[0]) + bufferDeg;
  const west = Math.min(start[1], end[1]) - bufferDeg;
  const east = Math.max(start[1], end[1]) + bufferDeg;
  const area = (north - south) * (east - west);
  if (area > 3.0) {
    throw new Error(
      `Strecke zu lang für OSM-Modus (${area.toFixed(1)} °²). ` +
      "Für Fernzüge bitte 'Fahrplan (GTFS)' wählen — oder 'Flugzeug (Großkreis)' als Näherung."
    );
  }
  const query = `
    [out:json][timeout:40];
    (
      way["railway"~"^(rail|light_rail|subway|tram)$"](${south},${west},${north},${east});
    );
    (._;>;);
    out body;
  `;
  let lastErr = null;
  for (const url of OVERPASS_URLS) {
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: "data=" + encodeURIComponent(query),
      });
      if (!r.ok) {
        lastErr = new Error(`Overpass HTTP ${r.status}`);
        if (![429, 502, 503, 504].includes(r.status)) break;
        continue;
      }
      return await r.json();
    } catch (err) {
      lastErr = err;
    }
  }
  throw new Error(`Overpass nicht erreichbar: ${lastErr?.message || lastErr}`);
}

async function fetchScenicPOIs(queryParams) {
  const allElements = [];
  const seenIds = new Set();
  for (let i = 0; i < queryParams.length; i++) {
    const qp = queryParams[i];
    const query = qp.query;
    setStatus(`Lade Sehenswürdigkeiten… (${i + 1}/${queryParams.length})`, "info");
    let fetched = false;
    for (const url of OVERPASS_URLS) {
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 15000);
        const r = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: "data=" + encodeURIComponent(query),
          signal: ctrl.signal,
        });
        clearTimeout(timer);
        if (!r.ok) continue;
        const data = await r.json();
        for (const el of (data.elements || [])) {
          if (!seenIds.has(el.id)) {
            seenIds.add(el.id);
            allElements.push(el);
          }
        }
        fetched = true;
        break;
      } catch { /* try next mirror */ }
    }
    if (!fetched) console.warn(`Scenic POI chunk ${i + 1} failed`);
    if (i < queryParams.length - 1) await new Promise(r => setTimeout(r, 1200));
  }
  return allElements;
}

async function fetchWeatherSamples(samples) {
  // samples: [{lat, lon, hour_iso}] — group by (lat, lon) so we minimize calls.
  const grouped = new Map();
  for (const s of samples) {
    const k = `${s.lat},${s.lon}`;
    if (!grouped.has(k)) grouped.set(k, { lat: s.lat, lon: s.lon, dates: new Set() });
    grouped.get(k).dates.add(s.hour_iso.slice(0, 10));
  }
  const out = [];
  for (const g of grouped.values()) {
    const dates = [...g.dates].sort();
    const url = new URL("https://api.open-meteo.com/v1/forecast");
    url.search = new URLSearchParams({
      latitude: String(g.lat), longitude: String(g.lon),
      hourly: "cloud_cover", timezone: "UTC",
      start_date: dates[0], end_date: dates[dates.length - 1],
    }).toString();
    try {
      const r = await fetch(url);
      if (!r.ok) continue;
      const data = await r.json();
      const times = data?.hourly?.time || [];
      const cloud = data?.hourly?.cloud_cover || [];
      for (let i = 0; i < times.length; i++) {
        out.push({
          lat: g.lat, lon: g.lon,
          hour_iso: times[i],
          cloud_cover_pct: cloud[i],
        });
      }
    } catch { /* ignore one location */ }
  }
  return out;
}

function chunked(items, size) {
  const chunks = [];
  for (let i = 0; i < items.length; i += size) chunks.push(items.slice(i, i + size));
  return chunks;
}

async function fetchElevationSamples(coords) {
  const out = [];
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  for (const [i, chunk] of chunked(coords, 100).entries()) {
    if (i > 0) await sleep(200);
    const url = new URL("https://api.open-meteo.com/v1/elevation");
    url.search = new URLSearchParams({
      latitude: chunk.map((c) => c.lat).join(","),
      longitude: chunk.map((c) => c.lon).join(","),
    }).toString();
    let r = await fetch(url);
    if (r.status === 429) {
      await sleep(3000);
      r = await fetch(url);
    }
    if (!r.ok) throw new Error(`Hoehendaten HTTP ${r.status}`);
    const data = await r.json();
    const elevations = data?.elevation || [];
    chunk.forEach((coord, index) => {
      const elevation = elevations[index];
      if (elevation != null) out.push({ lat: coord.lat, lon: coord.lon, elevation });
    });
  }
  return out;
}

// ---- Pyodide bootstrap ----
async function loadSunside() {
  setStatus("Lade Pyodide…", "info");
  els.runtimePill.textContent = "Pyodide lädt…";
  pyodide = await loadPyodide({
    indexURL: `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`,
  });

  setStatus("Installiere Python-Pakete (astral, gpxpy)…", "info");
  await pyodide.loadPackage("micropip");
  await pyodide.runPythonAsync(`
import micropip
await micropip.install(['astral', 'gpxpy'])
  `);

  setStatus("Lade SunSide-Module…", "info");

  // Robust dir creation: walk the prefix of every file path and mkdir
  // each segment under /home/pyodide. Avoids relying on FS.mkdirTree
  // (not always available) and on relative-path resolution quirks.
  const ROOT = "/home/pyodide";
  function ensureDirs(relPath) {
    const parts = relPath.split("/");
    parts.pop(); // drop filename
    let cur = ROOT;
    for (const seg of parts) {
      cur += "/" + seg;
      try { pyodide.FS.mkdir(cur); } catch (e) {
        // EEXIST (errno 20) is fine; rethrow others
        if (!e || e.errno !== 20) throw new Error(`mkdir ${cur}: ${e?.message || e?.errno || e}`);
      }
    }
  }

  for (const f of SUNSIDE_FILES) {
    const r = await fetch(f);
    if (!r.ok) throw new Error(`Modul fehlt: ${f} (HTTP ${r.status})`);
    const text = await r.text();
    ensureDirs(f);
    pyodide.FS.writeFile(`${ROOT}/${f}`, text);
  }

  await pyodide.runPythonAsync(`
import sys, os
os.chdir('${ROOT}')
if '${ROOT}' not in sys.path:
    sys.path.insert(0, '${ROOT}')
from sunside import browser_api
  `);

  setStatus("Bereit.", "muted");
  setSubmit("Berechnen", true);
  els.runtimePill.textContent = `Pyodide ${PYODIDE_VERSION} bereit`;
  setTimeout(() => setStatus("", null), 2000);
}

// ---- Run analysis ----
els.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!pyodide) return;
  els.result.classList.add("hidden");
  els.emptyState.classList.remove("hidden");
  setSubmit("Berechne… 0s", false);
  startCalcTimer();

  try {
    const provider = els.provider.value;
    // Build a timezone-aware ISO string so astral receives the correct local
    // time, not UTC. Without the offset astral would mis-calculate sun position
    // by up to ±12 h (typically 2 h off in Central Europe summer = "night" at 18:30).
    const [depYear, depMonth, depDay] = els.depDate.value.split("-").map(Number);
    const [depHour, depMinute] = els.depTime.value.split(":").map(Number);
    const tzOffset = localTzOffset(depYear, depMonth, depDay, depHour, depMinute);
    const departureIso = `${els.depDate.value}T${els.depTime.value}:00${tzOffset}`;
    const travelHours = parseFloat(els.duration.value);
    const intervalM = analysisInterval();
    const useConverged = els.converged.checked;

    // Build route in Python ----------------------------------------------
    let pyPoints;
    if (provider === "gpx") {
      const file = els.gpx.files[0];
      if (!file) throw new Error("Bitte GPX-Datei auswählen.");
      setStatus("Lese GPX…", "info");
      const text = await file.text();
      pyodide.globals.set("__gpx_text", text);
      pyodide.globals.set("__departure", departureIso);
      pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_gpx_route(__gpx_text, __departure)
points
      `);
    } else if (provider === "gtfs") {
      if (!gtfsState.trip) throw new Error("Bitte zuerst eine Fahrt auswaehlen.");
      setStatus("Lade Fahrplan-Polyline...", "info");
      const apiPoints = await gtfsFetchRoute(
        els.gtfsFeed.value,
        gtfsState.trip.trip_id,
        gtfsState.trip.board_stop_id,
        gtfsState.trip.alight_stop_id,
        gtfsState.trip.service_date,
      );

      if (apiPoints.length < 20) {
        // No shape data (e.g. DB) — try OSM Overpass for real rail geometry
        const start = [apiPoints[0].lat, apiPoints[0].lon];
        const end = [apiPoints[apiPoints.length - 1].lat, apiPoints[apiPoints.length - 1].lon];
        try {
          setStatus("Kein Shape-Daten — lade OSM-Gleise...", "info");
          const overpass = await fetchOverpassRail(start, end);
          pyodide.globals.set("__overpass", overpass);
          pyodide.globals.set("__sl", start[0]); pyodide.globals.set("__so", start[1]);
          pyodide.globals.set("__el", end[0]);   pyodide.globals.set("__eo", end[1]);
          pyodide.globals.set("__departure", apiPoints[0].timestamp);
          setStatus("Baue Bahn-Route (Dijkstra)…", "info");
          pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_rail_route_from_overpass(
    __overpass.to_py(), __sl, __so, __el, __eo,
    __departure,
)
points
          `);
        } catch {
          // Full-route Overpass failed — try segment-by-segment (good for regional trains)
          try {
            setStatus(`Lade OSM-Gleise segmentweise… (ca. ${Math.round(apiPoints.length * 1.5)}s)`, "info");
            const segData = [];
            for (let i = 0; i < apiPoints.length - 1; i++) {
              const s = [apiPoints[i].lat, apiPoints[i].lon];
              const e = [apiPoints[i + 1].lat, apiPoints[i + 1].lon];
              const buf = 0.08;
              const area = (Math.abs(e[0] - s[0]) + 2 * buf) * (Math.abs(e[1] - s[1]) + 2 * buf);
              let ov = null;
              if (area <= 2.0) {
                try { ov = await fetchOverpassRail(s, e, buf); } catch {}
              }
              segData.push({
                overpass_json: ov ? JSON.stringify(ov) : null,
                start_lat: s[0], start_lon: s[1],
                end_lat: e[0], end_lon: e[1],
                departure: apiPoints[i].timestamp,
                arrival: apiPoints[i + 1].timestamp,
              });
            }
            pyodide.globals.set("__seg_data", segData);
            pyPoints = await pyodide.runPythonAsync(`
points = browser_api.build_route_from_osm_segments(__seg_data.to_py())
points
            `);
          } catch {
            // Absolute fallback: stop-to-stop straight line with GTFS timestamps
            const coords = apiPoints.map(p => [p.lat, p.lon]);
            const t0 = new Date(apiPoints[0].timestamp).getTime();
            const tN = new Date(apiPoints[apiPoints.length - 1].timestamp).getTime();
            const durationSeconds = Math.max(60, (tN - t0) / 1000);
            pyodide.globals.set("__coords", coords);
            pyodide.globals.set("__departure", apiPoints[0].timestamp);
            pyodide.globals.set("__duration_seconds", durationSeconds);
            pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_polyline_route(__coords.to_py(), __departure, __duration_seconds)
points
            `);
          }
        }
      } else {
        // Feed has shape data (OeBB, Flixbus) — use directly
        const coords = apiPoints.map(p => [p.lat, p.lon]);
        const t0 = new Date(apiPoints[0].timestamp).getTime();
        const tN = new Date(apiPoints[apiPoints.length - 1].timestamp).getTime();
        const durationSeconds = Math.max(60, (tN - t0) / 1000);
        pyodide.globals.set("__coords", coords);
        pyodide.globals.set("__departure", apiPoints[0].timestamp);
        pyodide.globals.set("__duration_seconds", durationSeconds);
        pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_polyline_route(__coords.to_py(), __departure, __duration_seconds)
points
        `);
      }
    } else {
      const origin = _originCoords ? "Mein Standort" : els.origin.value.trim();
      const destination = els.destination.value.trim();
      if ((!_originCoords && !origin) || !destination) throw new Error("Start und Ziel angeben.");

      let start, end;
      if (_originCoords) {
        start = [_originCoords.lat, _originCoords.lon];
        setStatus("Suche Ziel…", "info");
        const destinationPlace = await resolveEndpoint(destination, "destination");
        end = [destinationPlace.lat, destinationPlace.lon];
      } else {
        setStatus("Suche Start/Ziel…", "info");
        const [originPlace, destinationPlace] = await Promise.all([
          resolveEndpoint(origin, "origin"),
          resolveEndpoint(destination, "destination"),
        ]);
        start = [originPlace.lat, originPlace.lon];
        end = [destinationPlace.lat, destinationPlace.lon];
      }

      if (provider === "osm") {
        setStatus("Lade OSM-Gleise…", "info");
        try {
          const overpass = await fetchOverpassRail(start, end);
          pyodide.globals.set("__overpass", overpass);
          pyodide.globals.set("__sl", start[0]); pyodide.globals.set("__so", start[1]);
          pyodide.globals.set("__el", end[0]);   pyodide.globals.set("__eo", end[1]);
          pyodide.globals.set("__departure", departureIso);
          setStatus("Baue Bahn-Route (Dijkstra)…", "info");
          pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_rail_route_from_overpass(
    __overpass.to_py(), __sl, __so, __el, __eo,
    __departure,
)
points
          `);
        } catch {
          // Strecke zu lang für Overpass — Grosskreis als Fallback, Hinweis auf GTFS
          setStatus("Strecke zu lang für OSM (>~150 km Luftlinie). Für Fernzüge bitte GTFS nutzen. Berechne mit Großkreis…", "warn");
          pyodide.globals.set("__sl", start[0]); pyodide.globals.set("__so", start[1]);
          pyodide.globals.set("__el", end[0]);   pyodide.globals.set("__eo", end[1]);
          pyodide.globals.set("__departure", departureIso);
          pyodide.globals.set("__th", travelHours);
          pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_great_circle_route(__sl, __so, __el, __eo, __departure, __th)
points
          `);
        }
      } else if (provider === "road" || provider === "car") {
  setStatus("Lade Strassenroute (OSRM)…", "info");
  const roadRoute = await fetchOsrmRoute(start, end);
  pyodide.globals.set("__coords", roadRoute.coordinates);
  pyodide.globals.set("__departure", departureIso);
  pyodide.globals.set("__duration_seconds", roadRoute.duration || travelHours * 3600);
  pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_polyline_route(__coords.to_py(), __departure, __duration_seconds)
points
  `);
      } else {
  // great-circle / flight
        pyodide.globals.set("__sl", start[0]); pyodide.globals.set("__so", start[1]);
        pyodide.globals.set("__el", end[0]);   pyodide.globals.set("__eo", end[1]);
        pyodide.globals.set("__departure", departureIso);
        pyodide.globals.set("__th", travelHours);
        pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_great_circle_route(__sl, __so, __el, __eo, __departure, __th)
points
        `);
      }
    }

    // Warn if convergence + long route
    if (useConverged) {
      const routeKm = await pyodide.runPythonAsync(`
round(browser_api._route_length_m(points) / 1000)
      `);
      if (routeKm > 150) {
        setStatus(`Konvergenzmodus aktiv (${routeKm} km) — das dauert 1–3 Minuten. Bitte warten.`, "info");
        await new Promise(r => setTimeout(r, 80)); // let the DOM paint the message
      }
    }

    // Optional weather pre-fetch ----------------------------------------
    let weatherSamples = null;
    if (els.weather.checked) {
      setStatus("Hole Wetterdaten (Open-Meteo)…", "info");
      const hourSamples = await pyodide.runPythonAsync(`
samples = browser_api.hour_samples_for_route(points)
samples
      `);
      const samplesJs = hourSamples.toJs({ dict_converter: Object.fromEntries });
      weatherSamples = await fetchWeatherSamples(samplesJs);
    }

    // Optional terrain pre-fetch ----------------------------------------
    let terrainSamples = null;
    if (els.terrain.checked) {
      setStatus("Ermittle Hoehenpunkte…", "info");
      pyodide.globals.set("__interval_m", intervalM || 0);
      pyodide.globals.set("__converged", useConverged ? 1 : 0);
      const terrainCoordsPy = await pyodide.runPythonAsync(`
coords = browser_api.elevation_points_for_route(
    points,
    interval_m=None if __interval_m <= 0 else int(__interval_m),
    converged=bool(__converged),
)
coords
      `);
      const terrainCoords = terrainCoordsPy.toJs({ dict_converter: Object.fromEntries });
      if (terrainCoords.length > TERRAIN_COORD_LIMIT) {
        throw new Error(
          `Gelaendeabfrage zu gross (${terrainCoords.length.toLocaleString("de-DE")} Hoehenpunkte). ` +
          "Bitte groesseres Messintervall waehlen oder Konvergenzmodus deaktivieren."
        );
      }
      if (terrainCoords.length) {
        setStatus(`Hole Hoehendaten (${terrainCoords.length.toLocaleString("de-DE")} Punkte)…`, "info");
        terrainSamples = await fetchElevationSamples(terrainCoords);
      } else {
        terrainSamples = [];
      }
    }

    // Run analysis -------------------------------------------------------
    setStatus("Analysiere Sonnenstand…", "info");
    pyodide.globals.set("__weather", weatherSamples);
    pyodide.globals.set("__terrain", terrainSamples);
    pyodide.globals.set("__interval_m", intervalM || 0);
    pyodide.globals.set("__use_intensity", els.intensity.checked ? 1 : 0);
    pyodide.globals.set("__converged", useConverged ? 1 : 0);
    const resultPy = await pyodide.runPythonAsync(`
result = browser_api.run_analysis(
    points,
    interval_m=None if __interval_m <= 0 else int(__interval_m),
    weather_samples=__weather,
    use_intensity=bool(__use_intensity),
    terrain_samples=__terrain,
    converged=bool(__converged),
)
result
    `);
    const result = resultPy.toJs({ dict_converter: Object.fromEntries });

    // Render sun result immediately ----------------------------------------
    result.scenic = null;
    _lastResult = result;
    _lastPref = els.form.pref.value;
    _lastScenic = null;
    renderResult(result, _lastPref);
    if (result.terrain_warning) setStatus(result.terrain_warning, "warn");
    else setStatus("", null);

    // Scenic POI analysis — runs async after sun result is shown ----------
    if (els.scenic && els.scenic.checked) {
      renderScenicLoading(true);
      (async () => {
        try {
          const isFlight = els.provider.value === "straight";
          pyodide.globals.set("__is_flight", isFlight ? 1 : 0);
          const queryParamsPy = await pyodide.runPythonAsync(`
browser_api.scenic_query_params(points, is_flight=bool(__is_flight))
          `);
          const queryParams = queryParamsPy.toJs({ dict_converter: Object.fromEntries });
          if (queryParams.length) {
            const poiElements = await fetchScenicPOIs(queryParams);
            setStatus("Analysiere Aussichtsseite…", "info");
            pyodide.globals.set("__scenic_pois", poiElements);
            const scenicPy = await pyodide.runPythonAsync(`
browser_api.run_scenic_analysis(points, __scenic_pois.to_py(), is_flight=bool(__is_flight))
            `);
            const scenicResult = scenicPy.toJs({ dict_converter: Object.fromEntries });
            _lastScenic = scenicResult;
            renderScenic(scenicResult);
          } else {
            _lastScenic = null;
            renderScenic(null);
          }
        } catch (err) {
          console.warn("scenic analysis failed:", err);
          _lastScenic = null;
          renderScenic(null);
        } finally {
          renderScenicLoading(false);
          setStatus("", null);
        }
      })();
    }
  } catch (err) {
    console.error("analysis failed:", err);
    setStatus(`Fehler: ${describeError(err)}`, "error");
  } finally {
    stopCalcTimer();
    setSubmit("Berechnen", true);
  }
});

// ---- Render ----
function renderResult(result, pref) {
  els.emptyState.classList.add("hidden");
  els.result.classList.remove("hidden");
  document.querySelector(".ss-workspace")?.classList.add("has-result");
  const segmentCount = result.segments?.length || 0;
  const trace = result.convergence_trace || [];

  if (result.is_night) {
    els.resultSide.textContent = "Nacht";
    els.resultPct.textContent = "—";
    els.recommendation.style.borderTopColor = "#475569";
  } else {
    els.recommendation.style.borderTopColor = "";
    if (pref === "Schatten") {
      els.resultSide.textContent = result.shade_side;
      els.resultPct.textContent = `≈ ${result.shade_pct.toFixed(0)} %`;
    } else {
      els.resultSide.textContent = result.sun_side;
      els.resultPct.textContent = `≈ ${result.sun_pct.toFixed(0)} %`;
    }
  }

  const meta = [
    formatInterval(result.auto_interval_m),
    `${segmentCount.toLocaleString("de-DE")} Segmente`,
  ];
  if (trace.length) meta.push(`${trace.length} Iterationen`);
  if (result.weather_adjusted && result.mean_cloud_cover_pct != null) meta.push(`${result.mean_cloud_cover_pct.toFixed(0)} % Wolken`);
  if (result.intensity_adjusted && result.mean_sun_elevation != null) meta.push(`${result.mean_sun_elevation.toFixed(1)}° Sonnenhoehe`);
  if (result.low_direct_sun) meta.push("geringe direkte Sonne");
  els.resultMeta.textContent = meta.join(" · ");

  els.metricInterval.textContent = formatInterval(result.auto_interval_m);
  els.metricSegments.textContent = segmentCount.toLocaleString("de-DE");
  els.metricClouds.textContent = result.weather_adjusted ? formatPct(result.mean_cloud_cover_pct) : "Aus";
  els.metricSun.textContent = result.intensity_adjusted && result.mean_sun_elevation != null
    ? `${result.mean_sun_elevation.toFixed(1)}°`
    : "Normal";
  els.metricTunnel.textContent = formatPct(result.tunnel_pct || 0);
  els.metricTerrain.textContent = result.terrain_adjusted ? formatPct(result.terrain_pct || 0) : "Aus";

  renderMap(result);
  if (result.scenic !== null) renderScenic(result.scenic);
  else if (!els.scenic?.checked) {
    const sw = document.getElementById("scenic-result");
    if (sw) sw.classList.add("hidden");
  }
  renderTrace(trace);
  renderTable(result.segments);
  renderSegmentBar(result.segments);

  // Update segment bar total label
  const segTotalEl = document.getElementById("seg-bar-total");
  if (segTotalEl) segTotalEl.textContent = `${segmentCount.toLocaleString("de-DE")} PUNKTE`;

  // Notify UI layer
  window.dispatchEvent(new CustomEvent("sunside:result", { detail: { result, pref } }));

  els.result.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
}

const SCENIC_CAT_LABELS = {
  peak: "Gipfel", glacier: "Gletscher", water: "See", coastline: "Küste",
  river: "Fluss", castle: "Burg", ruins: "Ruine", heritage: "UNESCO",
  city: "Stadt", viewpoint: "Aussichtspunkt",
};
const SCENIC_CAT_COLORS = {
  peak: "#6d5c4e", glacier: "#7eb8d8", water: "#2563eb", coastline: "#2563eb",
  river: "#2563eb", castle: "#9a6324", ruins: "#9a6324", heritage: "#b45309",
  city: "#475569", viewpoint: "#10845f",
};
const SCENIC_CAT_ABBR = {
  peak: "G", glacier: "GL", water: "S", coastline: "K",
  river: "F", castle: "B", ruins: "R", heritage: "U",
  city: "ST", viewpoint: "A",
};

function renderScenicLoading(show) {
  const wrap = document.getElementById("scenic-result");
  if (!wrap) return;
  if (show) {
    wrap.classList.remove("hidden");
    document.getElementById("scenic-side").textContent = "…";
    document.getElementById("scenic-pct").textContent = "";
    document.getElementById("scenic-pois").textContent = "Sehenswürdigkeiten werden geladen";
    document.getElementById("scenic-poi-count").textContent = "";
    const bar = document.getElementById("scenic-bar");
    if (bar) { bar.textContent = ""; bar.style.opacity = "0.4"; }
    const listEl = document.getElementById("scenic-poi-list");
    if (listEl) listEl.textContent = "";
    const details = wrap.querySelector("details");
    if (details) details.open = false;
    // Pulse animation on the card
    const card = wrap.querySelector("div");
    if (card) card.style.opacity = "0.6";
  } else {
    const card = wrap.querySelector("div");
    if (card) card.style.opacity = "";
    const bar = document.getElementById("scenic-bar");
    if (bar) bar.style.opacity = "";
  }
}

function renderScenic(scenic) {
  const wrap = document.getElementById("scenic-result");
  if (!wrap) return;
  wrap.classList.remove("hidden");

  if (scenicLayer) { scenicLayer.remove(); scenicLayer = null; }

  if (!scenic || scenic.poi_count === 0) {
    document.getElementById("scenic-side").textContent = "—";
    document.getElementById("scenic-pct").textContent = "";
    document.getElementById("scenic-pois").textContent = "Keine Sehenswürdigkeiten auf der Strecke gefunden. Nutze die Suche unten.";
    document.getElementById("scenic-poi-count").textContent = "0 POIs";
    const bar = document.getElementById("scenic-bar");
    if (bar) bar.textContent = "";
    const listEl = document.getElementById("scenic-poi-list");
    if (listEl) listEl.textContent = "";
    return;
  }

  document.getElementById("scenic-side").textContent = scenic.scenic_side;
  document.getElementById("scenic-pct").textContent = `≈ ${scenic.scenic_pct.toFixed(0)} %`;
  document.getElementById("scenic-poi-count").textContent = `${scenic.poi_count} POIs`;

  const topNames = (scenic.top_pois || [])
    .filter(p => p.name)
    .slice(0, 5)
    .map(p => `${p.name} (${SCENIC_CAT_LABELS[p.category] || p.category})`)
    .join(", ");
  document.getElementById("scenic-pois").textContent = topNames || "Diverse Sehenswürdigkeiten";

  // Scenic segment bar
  const bar = document.getElementById("scenic-bar");
  if (bar) {
    while (bar.firstChild) bar.removeChild(bar.firstChild);
    const segs = scenic.segments || [];
    if (segs.length) {
      const maxScore = Math.max(...segs.map(s => s.left_score + s.right_score), 0.01);
      for (const s of segs) {
        const total = s.left_score + s.right_score;
        const cell = document.createElement("div");
        cell.style.flex = "1";
        const intensity = Math.min(1, total / maxScore);
        if (s.left_score > s.right_score) {
          cell.style.background = `rgba(37, 99, 235, ${0.2 + 0.8 * intensity})`;
        } else if (s.right_score > s.left_score) {
          cell.style.background = `rgba(232, 93, 4, ${0.2 + 0.8 * intensity})`;
        } else {
          cell.style.background = "var(--bg-alt)";
        }
        bar.appendChild(cell);
      }
    }
  }

  // POI list
  const listEl = document.getElementById("scenic-poi-list");
  if (listEl) {
    listEl.textContent = "";
    const pois = scenic.top_pois || [];
    if (pois.length) {
      const scrollWrap = document.createElement("div");
      scrollWrap.style.cssText = "overflow-x:auto;-webkit-overflow-scrolling:touch";
      const table = document.createElement("table");
      table.style.cssText = "min-width:420px;width:100%;font-size:12px;border-collapse:collapse";
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      headRow.style.cssText = "text-align:left;color:var(--ink-dim);font-size:10px;text-transform:uppercase;letter-spacing:0.05em";
      for (const h of ["", "Name", "Kategorie", "Seite", "Entfernung"]) {
        const th = document.createElement("th");
        th.style.padding = "4px 6px";
        if (h === "Entfernung") th.style.textAlign = "right";
        th.textContent = h;
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      for (const poi of pois) {
        const catLabel = SCENIC_CAT_LABELS[poi.category] || poi.category;
        const catColor = SCENIC_CAT_COLORS[poi.category] || "#64748b";
        const catAbbr = SCENIC_CAT_ABBR[poi.category] || "?";
        const sideColor = poi.side === "links" ? "#2563eb" : "#e85d04";
        const distKm = poi.distance_m >= 1000
          ? `${(poi.distance_m / 1000).toFixed(1)} km`
          : `${Math.round(poi.distance_m)} m`;
        const tr = document.createElement("tr");
        tr.style.borderTop = "1px solid var(--border)";
        const tdIcon = document.createElement("td");
        tdIcon.style.cssText = "padding:6px;width:28px";
        const badge = document.createElement("span");
        badge.style.cssText = `display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:4px;font-size:9px;font-weight:700;letter-spacing:0.02em;color:#fff;background:${catColor}`;
        badge.textContent = catAbbr;
        tdIcon.appendChild(badge);
        const tdName = document.createElement("td");
        tdName.style.cssText = "padding:6px;font-weight:500";
        tdName.textContent = poi.name || "—";
        const tdCat = document.createElement("td");
        tdCat.style.cssText = "padding:6px;color:var(--ink-dim);white-space:nowrap";
        tdCat.textContent = catLabel;
        const tdSide = document.createElement("td");
        tdSide.style.cssText = "padding:6px;white-space:nowrap";
        const sideSpan = document.createElement("span");
        sideSpan.style.cssText = `color:${sideColor};font-weight:600`;
        sideSpan.textContent = poi.side;
        tdSide.appendChild(sideSpan);
        const tdDist = document.createElement("td");
        tdDist.style.cssText = "padding:6px;text-align:right;color:var(--ink-dim);white-space:nowrap";
        tdDist.textContent = distKm;
        tr.append(tdIcon, tdName, tdCat, tdSide, tdDist);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      scrollWrap.appendChild(table);
      listEl.appendChild(scrollWrap);
    } else {
      listEl.textContent = "Keine benannten Sehenswürdigkeiten gefunden.";
    }
  }

  // POI markers on map
  if (scenicLayer) { scenicLayer.remove(); scenicLayer = null; }
  if (map && scenic.top_pois) {
    const markers = [];
    for (const poi of scenic.top_pois) {
      const color = poi.side === "links" ? "#2563eb" : "#e85d04";
      const catLabel = SCENIC_CAT_LABELS[poi.category] || poi.category;
      const label = poi.name || catLabel;
      const marker = L.circleMarker([poi.lat, poi.lon], {
        radius: 5, color, fillColor: color, fillOpacity: 0.8, weight: 1.5,
      }).bindTooltip(`${label} · ${catLabel} · ${poi.side}`, { direction: "top" });
      markers.push(marker);
    }
    if (markers.length) scenicLayer = L.layerGroup(markers).addTo(map);
  }
}

// ---- Scenic POI search ----

function clearScenicSearch() {
  const input = document.getElementById("scenic-search");
  const clearBtn = document.getElementById("scenic-search-clear");
  const resultEl = document.getElementById("scenic-search-result");
  const sugBox = document.getElementById("scenic-search-suggestions");
  if (input) input.value = "";
  if (clearBtn) clearBtn.classList.add("hidden");
  if (resultEl) { resultEl.textContent = ""; resultEl.classList.add("hidden"); }
  if (sugBox) sugBox.classList.add("hidden");
  if (scenicSearchLayer) { scenicSearchLayer.remove(); scenicSearchLayer = null; }
}

function initScenicSearch() {
  const input = document.getElementById("scenic-search");
  const sugBox = document.getElementById("scenic-search-suggestions");
  const clearBtn = document.getElementById("scenic-search-clear");
  if (!input || !sugBox) return;

  if (clearBtn) {
    clearBtn.addEventListener("click", clearScenicSearch);
  }

  input.addEventListener("input", () => {
    clearTimeout(_scenicSearchTimer);
    const q = input.value.trim();
    if (clearBtn) clearBtn.classList.toggle("hidden", q.length === 0);
    if (q.length < 2) { sugBox.classList.add("hidden"); return; }
    _scenicSearchTimer = setTimeout(async () => {
      try {
        const results = await searchPlaces(q, { stationOnly: false, limit: 5 });
        sugBox.textContent = "";
        if (!results.length) { sugBox.classList.add("hidden"); return; }
        for (const r of results) {
          const item = document.createElement("div");
          item.style.cssText = "padding:8px 12px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--border)";
          item.textContent = r.display_name || r.name || "?";
          item.addEventListener("mouseenter", () => { item.style.background = "var(--bg-alt)"; });
          item.addEventListener("mouseleave", () => { item.style.background = ""; });
          item.addEventListener("click", () => {
            input.value = r.display_name || r.name;
            sugBox.classList.add("hidden");
            if (clearBtn) clearBtn.classList.remove("hidden");
            runScenicSearch(parseFloat(r.lat), parseFloat(r.lon), r.display_name || r.name);
          });
          sugBox.appendChild(item);
        }
        sugBox.classList.remove("hidden");
      } catch { sugBox.classList.add("hidden"); }
    }, 350);
  });

  input.addEventListener("blur", () => {
    setTimeout(() => sugBox.classList.add("hidden"), 200);
  });
}

async function runScenicSearch(lat, lon, name) {
  if (!pyodide) return;
  const resultEl = document.getElementById("scenic-search-result");
  if (!resultEl) return;

  try {
    pyodide.globals.set("__poi_lat", lat);
    pyodide.globals.set("__poi_lon", lon);
    const resPy = await pyodide.runPythonAsync(`
browser_api.locate_poi_on_route(points, __poi_lat, __poi_lon)
    `);
    const res = resPy.toJs({ dict_converter: Object.fromEntries });

    if (!res.found) {
      resultEl.textContent = "Keine Route geladen.";
      resultEl.classList.remove("hidden");
      return;
    }

    const sideColor = res.side === "links" ? "#2563eb" : "#e85d04";
    const distKm = res.distance_m >= 1000
      ? `${(res.distance_m / 1000).toFixed(1)} km`
      : `${Math.round(res.distance_m)} m`;
    const fmtTime = (iso) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    const closest = fmtTime(res.time);
    const visFrom = fmtTime(res.vis_start_time);
    const visTo = fmtTime(res.vis_end_time);
    const visRange = visFrom === visTo ? closest : `${visFrom} – ${visTo}`;

    resultEl.textContent = "";
    resultEl.classList.remove("hidden");

    const card = document.createElement("div");
    card.style.cssText = "background:var(--bg-alt);border-radius:8px;padding:12px";

    const title = document.createElement("div");
    title.style.cssText = "font-weight:600;font-size:14px;margin-bottom:8px";
    title.textContent = name;
    card.appendChild(title);

    const grid = document.createElement("div");
    grid.style.cssText = "display:grid;grid-template-columns:1fr 1fr;gap:6px 16px;font-size:13px";

    const items = [
      ["Seite", res.side, `color:${sideColor};font-weight:700`],
      ["Entfernung", distKm, ""],
      ["Sichtbar", visRange, ""],
      ["Nächster Punkt", closest, ""],
    ];
    for (const [label, value, style] of items) {
      const lbl = document.createElement("span");
      lbl.style.color = "var(--ink-dim)";
      lbl.textContent = label;
      const val = document.createElement("span");
      val.style.cssText = style;
      val.textContent = value;
      grid.append(lbl, val);
    }
    card.appendChild(grid);
    resultEl.appendChild(card);

    // Highlight on map
    if (scenicSearchLayer) { scenicSearchLayer.remove(); scenicSearchLayer = null; }
    if (map) {
      const layers = [];
      // POI marker
      layers.push(L.circleMarker([lat, lon], {
        radius: 7, color: "#b42318", fillColor: "#b42318", fillOpacity: 0.9, weight: 2,
      }).bindTooltip(name, { permanent: true, direction: "top", offset: [0, -10] }));
      // Visibility segment highlight
      if (res.vis_start_lat != null) {
        layers.push(L.polyline(
          [[res.vis_start_lat, res.vis_start_lon], [res.lat, res.lon], [res.vis_end_lat, res.vis_end_lon]],
          { color: "#b42318", weight: 6, opacity: 0.5, dashArray: "8 6" }
        ));
      }
      // Line from route to POI
      layers.push(L.polyline(
        [[res.lat, res.lon], [lat, lon]],
        { color: "#94a3b8", weight: 1.5, opacity: 0.6, dashArray: "4 4" }
      ));
      scenicSearchLayer = L.layerGroup(layers).addTo(map);
      map.fitBounds(L.latLngBounds([[res.lat, res.lon], [lat, lon]]).pad(0.3));
    }
  } catch (err) {
    console.warn("scenic search failed:", err);
    resultEl.textContent = "Suche fehlgeschlagen.";
    resultEl.classList.remove("hidden");
  }
}

// Initialize scenic search when DOM is ready
initScenicSearch();

// ---- Saved trips ----

const SAVED_TRIPS_KEY = "sunside_saved_trips";

function getSavedTrips() {
  try {
    return JSON.parse(localStorage.getItem(SAVED_TRIPS_KEY) || "[]");
  } catch { return []; }
}

function saveTripToStorage(trip) {
  const trips = getSavedTrips();
  trips.unshift(trip);
  if (trips.length > 20) trips.length = 20;
  try {
    localStorage.setItem(SAVED_TRIPS_KEY, JSON.stringify(trips));
  } catch (e) {
    // localStorage full — remove oldest trips until it fits
    while (trips.length > 1) {
      trips.pop();
      try { localStorage.setItem(SAVED_TRIPS_KEY, JSON.stringify(trips)); return; } catch {}
    }
    console.warn("localStorage full, could not save trip");
  }
}

function deleteSavedTrip(id) {
  const trips = getSavedTrips().filter(t => t.id !== id);
  localStorage.setItem(SAVED_TRIPS_KEY, JSON.stringify(trips));
  renderSavedTrips();
}

function saveCurrentTrip() {
  if (!_lastResult) return;

  const route = document.getElementById("boarding-route")?.textContent || "";
  const mode = document.getElementById("boarding-mode")?.textContent || "";
  const time = document.getElementById("boarding-time")?.textContent || "";
  const pref = _lastPref || "Schatten";

  if (!route || route === "— → —") return;

  // Downsample polyline for storage (keep max ~500 points for a smooth map line)
  const rawPoly = _lastResult.polyline || [];
  const polyStep = rawPoly.length > 500 ? Math.ceil(rawPoly.length / 500) : 1;
  const thinPolyline = polyStep === 1 ? rawPoly : rawPoly.filter((_, i) => i % polyStep === 0 || i === rawPoly.length - 1);

  // Thin out segments for storage — keep essential fields only
  const thinSegments = (_lastResult.segments || []).map(s => ({
    lat: s.lat, lon: s.lon, sun_side: s.sun_side,
    sun_elevation: s.sun_elevation, bearing: s.bearing,
    sun_azimuth: s.sun_azimuth,
    timestamp: s.time || s.timestamp,  // needed for scenic search on reload
    ...(s.cloud_cover != null ? { cloud_cover: s.cloud_cover } : {}),
    ...(s.terrain_shade != null ? { terrain_shade: s.terrain_shade } : {}),
    ...(s.effective_weight != null ? { effective_weight: s.effective_weight } : {}),
  }));

  // Thin scenic — keep top_pois and segment scores
  let thinScenic = null;
  if (_lastScenic && _lastScenic.poi_count > 0) {
    thinScenic = {
      scenic_side: _lastScenic.scenic_side,
      scenic_pct: _lastScenic.scenic_pct,
      other_side: _lastScenic.other_side,
      other_pct: _lastScenic.other_pct,
      poi_count: _lastScenic.poi_count,
      top_pois: (_lastScenic.top_pois || []).map(p => ({
        lat: p.lat, lon: p.lon, name: p.name, category: p.category,
        side: p.side, distance_m: p.distance_m, weight: p.weight,
      })),
      segments: (_lastScenic.segments || []).map(s => ({
        left_score: s.left_score, right_score: s.right_score,
      })),
    };
  }

  const trip = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    route, mode, time, pref,
    savedAt: new Date().toISOString(),
    origin: document.getElementById("origin")?.value || "",
    destination: document.getElementById("destination")?.value || "",
    provider: document.getElementById("provider")?.value || "",
    // Full result data for re-rendering
    result: {
      is_night: _lastResult.is_night || false,
      shade_side: _lastResult.shade_side,
      shade_pct: _lastResult.shade_pct,
      sun_side: _lastResult.sun_side,
      sun_pct: _lastResult.sun_pct,
      auto_interval_m: _lastResult.auto_interval_m,
      weather_adjusted: _lastResult.weather_adjusted || false,
      intensity_adjusted: _lastResult.intensity_adjusted || false,
      mean_cloud_cover_pct: _lastResult.mean_cloud_cover_pct,
      mean_sun_elevation: _lastResult.mean_sun_elevation,
      low_direct_sun: _lastResult.low_direct_sun || false,
      terrain_adjusted: _lastResult.terrain_adjusted || false,
      terrain_pct: _lastResult.terrain_pct || 0,
      tunnel_pct: _lastResult.tunnel_pct || 0,
      terrain_warning: _lastResult.terrain_warning || null,
      polyline: thinPolyline,
      segments: thinSegments,
      convergence_trace: [],  // don't store trace
      scenic: null,
    },
    scenic: thinScenic,
  };

  saveTripToStorage(trip);
  renderSavedTrips();

  // Visual feedback on save button
  const btn = document.getElementById("save-trip-btn");
  if (btn) {
    btn.style.color = "var(--amber, #f2b705)";
    btn.title = "Gespeichert!";
    setTimeout(() => { btn.style.color = ""; btn.title = "Reise speichern"; }, 2000);
  }
}

function renderSavedTrips() {
  const section = document.getElementById("saved-trips");
  const list = document.getElementById("saved-trips-list");
  if (!section || !list) return;

  const trips = getSavedTrips();
  if (!trips.length) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  list.textContent = "";

  for (const trip of trips) {
    const card = document.createElement("div");
    card.style.cssText = "background:var(--paper);border-radius:10px;padding:12px 14px;margin-bottom:8px;box-shadow:var(--shadow);cursor:pointer;border:1px solid var(--border);display:flex;align-items:center;gap:12px";

    const info = document.createElement("div");
    info.style.cssText = "flex:1;min-width:0";

    const routeLine = document.createElement("div");
    routeLine.style.cssText = "font-weight:600;font-size:14px;margin-bottom:2px";
    routeLine.textContent = trip.route;
    info.appendChild(routeLine);

    const meta = document.createElement("div");
    meta.style.cssText = "font-size:12px;color:var(--ink-dim)";

    // Extract side/pct from stored result object (new format) or fallback to old text fields
    const r = trip.result;
    let side, pct;
    if (r) {
      if (r.is_night) { side = "Nacht"; pct = "—"; }
      else if (trip.pref === "Schatten") { side = r.shade_side; pct = `≈ ${r.shade_pct?.toFixed?.(0) ?? Math.round(r.shade_pct)} %`; }
      else { side = r.sun_side; pct = `≈ ${r.sun_pct?.toFixed?.(0) ?? Math.round(r.sun_pct)} %`; }
    } else {
      side = trip.side || "—"; pct = trip.pct || "";
    }
    const sideColor = side === "links" ? "#2563eb" : side === "rechts" ? "#e85d04" : "#475569";

    const prefSpan = document.createElement("span");
    prefSpan.textContent = `${trip.pref}: `;
    const sideSpan = document.createElement("span");
    sideSpan.style.cssText = `color:${sideColor};font-weight:600`;
    sideSpan.textContent = side;
    const restSpan = document.createElement("span");
    restSpan.textContent = ` ${pct} · ${trip.mode} · ${trip.time}`;
    meta.append(prefSpan, sideSpan, restSpan);

    if (trip.scenic && trip.scenic.scenic_side) {
      const scenicSpan = document.createElement("div");
      scenicSpan.style.cssText = "font-size:11px;color:var(--ink-dim);margin-top:2px";
      scenicSpan.textContent = `Aussicht: ${trip.scenic.scenic_side} ≈ ${Math.round(trip.scenic.scenic_pct)} %`;
      meta.appendChild(scenicSpan);
    }
    info.appendChild(meta);

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.setAttribute("aria-label", "Löschen");
    delBtn.style.cssText = "background:none;border:none;cursor:pointer;color:var(--ink-dim);padding:4px;font-size:14px;line-height:1;flex-shrink:0";
    delBtn.textContent = "×";
    delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSavedTrip(trip.id);
    });

    card.appendChild(info);
    card.appendChild(delBtn);
    card.addEventListener("click", () => loadSavedTrip(trip));
    list.appendChild(card);
  }
}

function loadSavedTrip(trip) {
  // Fill form inputs
  if (trip.origin) { const el = document.getElementById("origin"); if (el) el.value = trip.origin; }
  if (trip.destination) { const el = document.getElementById("destination"); if (el) el.value = trip.destination; }

  // Set boarding info
  document.getElementById("boarding-route").textContent = trip.route;
  document.getElementById("boarding-mode").textContent = trip.mode;
  document.getElementById("boarding-time").textContent = trip.time;

  if (trip.result) {
    // New format: re-render with full data
    _lastResult = trip.result;
    _lastPref = trip.pref || "Schatten";
    _lastScenic = trip.scenic || null;
    renderResult(trip.result, _lastPref);
    if (trip.scenic) renderScenic(trip.scenic);
    else {
      const sw = document.getElementById("scenic-result");
      if (sw) sw.classList.add("hidden");
    }

    // Inject route points into Pyodide so scenic search works without re-analysing
    if (pyodide && trip.result.segments?.length) {
      const segs = trip.result.segments;
      pyodide.globals.set("__saved_segs", pyodide.toPy(segs));
      pyodide.runPythonAsync(`
from sunside.models import RoutePoint
from datetime import datetime, timezone
_segs = __saved_segs.to_py()
points = [
    RoutePoint(
        lat=float(s["lat"]),
        lon=float(s["lon"]),
        timestamp=datetime.fromisoformat(s["timestamp"]) if s.get("timestamp")
                  else datetime.now(timezone.utc),
    )
    for s in _segs
]
      `.trim()).catch(err => console.warn("Could not restore points in Pyodide:", err));
    }
  } else {
    // Legacy format (old text-only saves): show what we have
    const resultSection = document.getElementById("result");
    if (resultSection) resultSection.classList.remove("hidden");
    document.getElementById("empty-state")?.classList.add("hidden");
    const sideEl = document.getElementById("result-side");
    const pctEl = document.getElementById("result-pct");
    const metaEl = document.getElementById("result-meta");
    if (sideEl) sideEl.textContent = trip.side || "—";
    if (pctEl) pctEl.textContent = trip.pct || "";
    if (metaEl) metaEl.textContent = trip.meta || "";
  }

  // Scroll to result
  const resultSection = document.getElementById("result");
  resultSection?.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Wire up save button
document.getElementById("save-trip-btn")?.addEventListener("click", saveCurrentTrip);

// Render saved trips on load
renderSavedTrips();

function renderSegmentBar(segments) {
  const container = document.getElementById("seg-bar");
  if (!container) return;
  while (container.firstChild) container.removeChild(container.firstChild);
  if (!segments?.length) return;

  const maxCells = 80;
  const step = Math.max(1, Math.floor(segments.length / maxCells));
  const cells = [];
  for (let i = 0; i < segments.length; i += step) cells.push(segments[i]);

  for (const s of cells) {
    const cell = document.createElement("div");
    cell.style.flex = "1";
    cell.style.background = segmentColor(segmentKey(s));
    container.appendChild(cell);
  }
}

function segmentKey(segment) {
  if (segment.terrain_shaded) return "terrain";
  return segment.sun_side || "night";
}

function segmentColor(key) {
  return ({
    links: "#2563eb",
    rechts: "#e85d04",
    night: "#475569",
    tunnel: "#1f2937",
    terrain: "#10845f",
  })[key] || "#64748b";
}

function segmentLabel(key) {
  return ({
    links: "Sonne links",
    rechts: "Sonne rechts",
    night: "Nacht",
    tunnel: "Tunnel",
    terrain: "Gelaendeschatten",
  })[key] || key;
}

function renderMap(result) {
  if (!map) {
    map = L.map("map", { preferCanvas: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "© OpenStreetMap",
      updateWhenIdle: true,
      keepBuffer: 1,
    }).addTo(map);
  }
  if (routeLayer) routeLayer.remove();
  if (segmentLayer) segmentLayer.remove();

  const polyline = result.polyline.map((p) => [p[0], p[1]]);
  routeLayer = L.polyline(polyline, { color: "#94a3b8", weight: 3, opacity: 0.35 }).addTo(map);

  const layers = [];
  const segments = result.segments || [];
  let currentKey = null;
  let currentCoords = [];

  function addRun(key, coords) {
    if (coords.length < 2) return;
    layers.push(L.polyline(coords, {
      color: segmentColor(key),
      weight: 5,
      opacity: 0.88,
      lineCap: "round",
      lineJoin: "round",
    }).bindTooltip(segmentLabel(key)));
  }

  for (let index = 0; index < segments.length; index++) {
    const segment = segments[index];
    const key = segmentKey(segment);
    const start = [segment.lat, segment.lon];
    const end = index + 1 < segments.length
      ? [segments[index + 1].lat, segments[index + 1].lon]
      : (polyline[polyline.length - 1] || start);

    if (currentKey === null) {
      currentKey = key;
      currentCoords = [start];
    } else if (key !== currentKey) {
      currentCoords.push(start);
      addRun(currentKey, currentCoords);
      currentKey = key;
      currentCoords = [start];
    }
    currentCoords.push(end);
  }
  addRun(currentKey, currentCoords);

  if (polyline.length) {
    layers.push(L.circleMarker(polyline[0], { radius: 6, color: "#10845f", fillColor: "#10845f", fillOpacity: 1, weight: 2 }).bindTooltip("Start"));
    layers.push(L.circleMarker(polyline[polyline.length - 1], { radius: 6, color: "#b42318", fillColor: "#b42318", fillOpacity: 1, weight: 2 }).bindTooltip("Ziel"));
  }

  segmentLayer = L.layerGroup(layers).addTo(map);
  if (polyline.length > 1) map.fitBounds(routeLayer.getBounds(), { padding: [28, 28] });
  window._sunsideMap = map;
  setTimeout(() => map.invalidateSize(), 0);
}

function appendCells(row, values) {
  for (const value of values) {
    const cell = document.createElement("td");
    cell.textContent = value;
    row.appendChild(cell);
  }
}

function renderTrace(trace) {
  if (!els.tracePanel || !els.traceTable) return;
  els.tracePanel.classList.toggle("hidden", !trace.length);
  els.traceTable.innerHTML = "";
  for (const entry of trace) {
    const tr = document.createElement("tr");
    appendCells(tr, [
      formatInterval(entry.interval_m),
      Number(entry.segments || 0).toLocaleString("de-DE"),
      entry.sun_side || "-",
      entry.sun_pct == null ? "-" : `${Number(entry.sun_pct).toFixed(1)} %`,
    ]);
    els.traceTable.appendChild(tr);
  }
}

function renderTable(segments) {
  const tbody = els.segTable;
  tbody.innerHTML = "";
  for (const s of segments || []) {
    const tr = document.createElement("tr");
    const time = new Date(s.time).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    appendCells(tr, [
      time,
      `${s.bearing.toFixed(0)}°`,
      `${s.sun_azimuth.toFixed(0)}°`,
      `${s.sun_elevation.toFixed(0)}°`,
      s.intensity_factor == null ? "-" : Number(s.intensity_factor).toFixed(2),
      s.cloud_cover_pct == null ? "-" : `${Math.round(s.cloud_cover_pct)} %`,
      s.sun_factor == null ? "-" : Number(s.sun_factor).toFixed(2),
      s.sun_side,
      s.terrain_shaded ? "ja" : "",
    ]);
    tbody.appendChild(tr);
  }
}

// ---- PWA install prompt ----
let deferredPrompt = null;
function hideInstallPrompt() {
  deferredPrompt = null;
  els.installBtn.classList.remove("show");
}
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  if (isStandalonePwa()) return;
  deferredPrompt = e;
  els.installBtn.classList.add("show");
});
els.installBtn.addEventListener("click", async () => {
  if (!deferredPrompt) return;
  els.installBtn.classList.remove("show");
  deferredPrompt.prompt();
  deferredPrompt = null;
});
window.addEventListener("appinstalled", hideInstallPrompt);
const standaloneModeQuery = window.matchMedia?.("(display-mode: standalone)");
standaloneModeQuery?.addEventListener?.("change", () => {
  if (isStandalonePwa()) hideInstallPrompt();
});
if (isStandalonePwa()) hideInstallPrompt();

// ---- Service worker (with update flow) ----
let updateRegistration = null;
function showUpdateBanner(reg) {
  updateRegistration = reg;
  if (els.updateBanner) els.updateBanner.hidden = false;
}
function hideUpdateBanner() {
  if (els.updateBanner) els.updateBanner.hidden = true;
}
els.updateBtn?.addEventListener("click", () => {
  const waiting = updateRegistration?.waiting;
  if (!waiting) { window.location.reload(); return; }
  waiting.postMessage({ type: "SKIP_WAITING" });
});
els.updateDismiss?.addEventListener("click", hideUpdateBanner);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", async () => {
    try {
      const reg = await navigator.serviceWorker.register("./sw.js", { updateViaCache: "none" });

      // If a worker is already waiting on first load, surface the prompt.
      if (reg.waiting && navigator.serviceWorker.controller) showUpdateBanner(reg);

      reg.addEventListener("updatefound", () => {
        const sw = reg.installing;
        if (!sw) return;
        sw.addEventListener("statechange", () => {
          if (sw.state === "installed" && navigator.serviceWorker.controller) {
            showUpdateBanner(reg);
          }
        });
      });

      // Periodic update check (hourly) for long-running PWA sessions.
      setInterval(() => reg.update().catch(() => {}), 60 * 60 * 1000);
    } catch (err) {
      console.warn("SW failed:", err);
    }
  });

  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading) return;
    reloading = true;
    window.location.reload();
  });
}

// ---- Online/Offline awareness ----
function updateOnlineStatus() {
  if (!els.offlinePill) return;
  els.offlinePill.hidden = navigator.onLine;
}
window.addEventListener("online", updateOnlineStatus);
window.addEventListener("offline", updateOnlineStatus);
updateOnlineStatus();

// ---- Deep-link / shortcut params ----
try {
  const params = new URLSearchParams(window.location.search);
  const pref = params.get("pref");
  if (pref === "Sonne" || pref === "Schatten") {
    const radio = document.querySelector(`input[name="pref"][value="${pref}"]`);
    if (radio) radio.checked = true;
  }
} catch {}

// ---- Boot ----
loadSunside().catch((err) => {
  console.error("loadSunside failed:", err);
  setStatus(`Initialisierung fehlgeschlagen: ${describeError(err)}`, "error");
  els.runtimePill.textContent = "Pyodide Fehler";
});
