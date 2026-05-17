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
  terrain: $("terrain"), converged: $("converged"),
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
let _originCoords = null;
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
      `Strecke zu lang für OSM-Modus (Gebiet ${area.toFixed(1)} °² > 3 °²). ` +
      "Bitte 'Flugzeug (Grosskreis)' oder 'Auto/Bus' waehlen, oder eine GPX-Datei hochladen."
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
  setSubmit("Berechne…", false);

  try {
    const provider = els.provider.value;
    const departureIso = `${els.depDate.value}T${els.depTime.value}:00`;
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
            setStatus("Lade OSM-Gleise segmentweise…", "info");
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
          // Strecke zu lang für Overpass — Grosskreis als Fallback
          setStatus("OSM-Gleise nicht verfügbar (Strecke zu lang) — verwende Großkreis.", "warn");
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

    renderResult(result, els.form.pref.value);
    if (result.terrain_warning) setStatus(result.terrain_warning, "warn");
    else setStatus("", null);
  } catch (err) {
    console.error("analysis failed:", err);
    setStatus(`Fehler: ${describeError(err)}`, "error");
  } finally {
    setSubmit("Berechnen", true);
  }
});

// ---- Render ----
function renderResult(result, pref) {
  els.emptyState.classList.add("hidden");
  els.result.classList.remove("hidden");
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
