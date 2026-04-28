// SunSide — Pyodide-powered SPA
// Lädt Pyodide + sunside-Paket im Browser. Alle HTTP-Calls (Nominatim, Overpass,
// Open-Meteo) laufen in JS; Python rechnet nur.

const PYODIDE_VERSION = "0.27.2";
const SUNSIDE_FILES = [
  "sunside/__init__.py",
  "sunside/models.py",
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
  origin: $("origin"), destination: $("destination"),
  originLabel: $("origin-label"), destinationLabel: $("destination-label"),
  originSuggestions: $("origin-suggestions"), destinationSuggestions: $("destination-suggestions"),
  previewMap: $("preview-map"),
  gpxField: $("gpx-field"), gpx: $("gpx"),
  od: $("origin-destination"),
  depDate: $("dep-date"), depTime: $("dep-time"),
  durationField: $("duration-field"), duration: $("duration"), durationValue: $("duration-value"),
  weather: $("weather"),
  submit: $("submit"), status: $("status"),
  result: $("result"), resultSide: $("result-side"), resultPct: $("result-pct"),
  resultMeta: $("result-meta"), recommendation: $("recommendation"),
  segTable: $("segments-table").querySelector("tbody"),
  runtimePill: $("runtime-pill"),
  installBtn: $("install-btn"),
};

let pyodide = null;
let map = null;
let routeLayer = null;
let segmentLayer = null;
let previewMap = null;
let previewLayer = null;
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

els.provider.addEventListener("change", updateProviderUI);
function updateProviderUI() {
  const v = els.provider.value;
  selectedPlace.origin = null;
  selectedPlace.destination = null;
  hideSuggestions();
  els.gpxField.classList.toggle("hidden", v !== "gpx");
  els.od.classList.toggle("hidden", v === "gpx");
  els.durationField.classList.toggle("hidden", v === "gpx");
  els.previewMap.classList.toggle("hidden", v === "gpx");

  const labels = {
    osm: ["Startbahnhof", "Zielbahnhof", "Berlin", "München"],
    road: ["Startort", "Zielort", "Berlin ZOB", "München ZOB"],
    straight: ["Startort oder Flughafen", "Zielort oder Flughafen", "Berlin BER", "München Flughafen"],
  }[v];
  if (labels) {
    els.originLabel.textContent = labels[0];
    els.destinationLabel.textContent = labels[1];
    els.origin.placeholder = labels[2];
    els.destination.placeholder = labels[3];
    if (!els.origin.value.trim()) els.origin.value = labels[2];
    if (!els.destination.value.trim()) els.destination.value = labels[3];
  }

  els.providerHint.textContent = ({
    osm: "OSM nutzt echte Gleisgeometrie — am besten für Strecken bis ca. 150 km (z. B. München–Nürnberg). Für längere Strecken Luftlinie wählen.",
    road: "Auto/Bus nutzt OSRM-Strassenrouting. Gut fuer direkte Auto- und Fernbusstrecken wie Flixbus ohne Zwischenhalte.",
    straight: "Flugzeug nutzt eine Luftlinie zwischen zwei geocodierten Orten oder Flughäfen.",
    gpx: "Eigene .gpx-Datei hochladen (z. B. von Komoot, Strava, BRouter).",
  })[v];

  // Only refresh if user already typed something (avoid hammering Nominatim on init)
  if (els.origin.value.trim().length >= 3 || els.destination.value.trim().length >= 3) {
    refreshSuggestionsAndPreview();
  }
}
updateProviderUI();

const debouncedRefreshSuggestions = debounce(refreshSuggestionsAndPreview, 600);
els.origin.addEventListener("input", () => {
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
    target.appendChild(button);
  }

  target.classList.toggle("hidden", !visible || items.length === 0);
}

function chooseSuggestion(side, item) {
  selectedPlace[side] = item;
  inputForSide(side).value = item.label;
  optionState[side] = [item, ...optionState[side].filter((candidate) => candidate !== item)];
  hideSuggestions(side);
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
    previewMap = L.map("preview-map", { zoomControl: false, attributionControl: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(previewMap);
  }
  if (previewLayer) previewLayer.remove();
  const dashed = els.provider.value !== "straight";
  const color = els.provider.value === "road" ? "#16a34a" : "#2563eb";
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
      "Bitte 'Luftlinie' oder 'Auto/Bus' wählen, oder eine GPX-Datei hochladen."
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
  setSubmit("Berechne…", false);

  try {
    const provider = els.provider.value;
    const departureIso = `${els.depDate.value}T${els.depTime.value}:00`;
    const travelHours = parseFloat(els.duration.value);

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
    } else {
      const origin = els.origin.value.trim();
      const destination = els.destination.value.trim();
      if (!origin || !destination) throw new Error("Start und Ziel angeben.");

      setStatus("Suche Start/Ziel…", "info");
      const [originPlace, destinationPlace] = await Promise.all([
        resolveEndpoint(origin, "origin"),
        resolveEndpoint(destination, "destination"),
      ]);
      const start = [originPlace.lat, originPlace.lon];
      const end = [destinationPlace.lat, destinationPlace.lon];

      if (provider === "osm") {
        setStatus("Lade OSM-Gleise…", "info");
        const overpass = await fetchOverpassRail(start, end);
        pyodide.globals.set("__overpass", overpass);
        pyodide.globals.set("__sl", start[0]); pyodide.globals.set("__so", start[1]);
        pyodide.globals.set("__el", end[0]);   pyodide.globals.set("__eo", end[1]);
        pyodide.globals.set("__departure", departureIso);
        pyodide.globals.set("__th", travelHours);
        setStatus("Baue Bahn-Route (Dijkstra)…", "info");
        pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_rail_route_from_overpass(
    __overpass.to_py(), __sl, __so, __el, __eo,
    __departure, travel_hours=__th,
)
points
        `);
      } else if (provider === "road") {
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
  // straight / flight
        pyodide.globals.set("__sl", start[0]); pyodide.globals.set("__so", start[1]);
        pyodide.globals.set("__el", end[0]);   pyodide.globals.set("__eo", end[1]);
        pyodide.globals.set("__departure", departureIso);
        pyodide.globals.set("__th", travelHours);
        pyPoints = await pyodide.runPythonAsync(`
points = browser_api.make_straight_route(__sl, __so, __el, __eo, __departure, __th)
points
        `);
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

    // Run analysis -------------------------------------------------------
    setStatus("Analysiere Sonnenstand…", "info");
    pyodide.globals.set("__weather", weatherSamples);
    const resultPy = await pyodide.runPythonAsync(`
result = browser_api.run_analysis(points, weather_samples=__weather)
result
    `);
    const result = resultPy.toJs({ dict_converter: Object.fromEntries });

    renderResult(result, els.form.pref.value);
    setStatus("", null);
  } catch (err) {
    console.error("analysis failed:", err);
    setStatus(`Fehler: ${describeError(err)}`, "error");
  } finally {
    setSubmit("Berechnen", true);
  }
});

// ---- Render ----
function renderResult(result, pref) {
  els.result.classList.remove("hidden");

  // recommendation
  if (result.is_night) {
    els.resultSide.textContent = "Nachtfahrt";
    els.resultPct.textContent = "Keine Sonnenempfehlung nötig.";
    els.resultMeta.textContent = "";
    els.recommendation.style.background = "#1e293b";
    els.recommendation.style.color = "#f1f5f9";
  } else {
    els.recommendation.style.background = "";
    els.recommendation.style.color = "";
    if (pref === "Schatten") {
      els.resultSide.textContent = result.shade_side;
      els.resultPct.textContent = `≈ ${result.shade_pct.toFixed(0)} % im Schatten`;
    } else {
      els.resultSide.textContent = result.sun_side;
      els.resultPct.textContent = `≈ ${result.sun_pct.toFixed(0)} % in der Sonne`;
    }
    const km = (result.auto_interval_m >= 1000)
      ? `${(result.auto_interval_m / 1000).toFixed(1)} km`
      : `${result.auto_interval_m} m`;
    let meta = `Messintervall: ${km} · ${result.segments.length} Segmente`;
    if (result.weather_adjusted && result.mean_cloud_cover_pct != null) {
      meta += ` · ${result.mean_cloud_cover_pct.toFixed(0)} % Wolken Ø`;
    }
    if (result.low_direct_sun) meta += " · viel Bewölkung — Sitzseite weniger entscheidend";
    els.resultMeta.textContent = meta;
  }

  renderMap(result);
  renderTable(result.segments);
  els.result.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderMap(result) {
  if (!map) {
    map = L.map("map");
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap",
    }).addTo(map);
  }
  if (routeLayer) routeLayer.remove();
  if (segmentLayer) segmentLayer.remove();

  const polyline = result.polyline.map((p) => [p[0], p[1]]);
  routeLayer = L.polyline(polyline, { color: "#2563eb", weight: 4, opacity: 0.7 }).addTo(map);

  segmentLayer = L.layerGroup(
    result.segments.map((s) => {
      const color = s.sun_side === "rechts" ? "#f97316" : (s.sun_side === "links" ? "#2563eb" : "#6b7280");
      return L.circleMarker([s.lat, s.lon], {
        radius: 4, color, fillColor: color, fillOpacity: 0.85, weight: 1,
      }).bindTooltip(`${new Date(s.time).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} · ${s.sun_side}`);
    })
  ).addTo(map);

  map.fitBounds(routeLayer.getBounds(), { padding: [20, 20] });
}

function renderTable(segments) {
  const tbody = els.segTable;
  tbody.innerHTML = "";
  for (const s of segments) {
    const tr = document.createElement("tr");
    const time = new Date(s.time).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    tr.innerHTML = `
      <td>${time}</td>
      <td>${s.bearing.toFixed(0)}</td>
      <td>${s.sun_azimuth.toFixed(0)}</td>
      <td>${s.sun_elevation.toFixed(0)}</td>
      <td>${s.cloud_cover_pct == null ? "–" : Math.round(s.cloud_cover_pct)}</td>
      <td>${s.sun_factor.toFixed(2)}</td>
      <td>${s.sun_side}</td>
    `;
    tbody.appendChild(tr);
  }
}

// ---- PWA install prompt ----
let deferredPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredPrompt = e;
  els.installBtn.classList.add("show");
});
els.installBtn.addEventListener("click", async () => {
  if (!deferredPrompt) return;
  els.installBtn.classList.remove("show");
  deferredPrompt.prompt();
  deferredPrompt = null;
});

// ---- Service worker ----
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch((err) => console.warn("SW failed:", err));
  });
}

// ---- Boot ----
loadSunside().catch((err) => {
  console.error("loadSunside failed:", err);
  setStatus(`Initialisierung fehlgeschlagen: ${describeError(err)}`, "error");
  els.runtimePill.textContent = "Pyodide Fehler";
});
