const CSB_BASE = "https://backend.commanderspellbook.com";
const SCRYFALL_NAMED = "https://api.scryfall.com/cards/named?exact=";
const CACHE_KEY = "scryfall_cache_v1";
const MAX_LOG = 4000;

const defaultConfig = {
  version: 1,
  curated_lists: {
    fast_mana: ["Sol Ring", "Mana Crypt", "Mana Vault", "Grim Monolith"],
    unconditional_tutors: ["Demonic Tutor", "Vampiric Tutor", "Enlightened Tutor"],
    free_interaction: ["Force of Will", "Force of Negation", "Fierce Guardianship", "Swan Song"],
    staple_board_wipes: ["Wrath of God", "Supreme Verdict", "Damnation", "Toxic Deluge"],
    staple_stax: ["Smokestack", "Winter Orb", "Stasis", "Rule of Law"],
    combo_enablers: ["Underworld Breach", "Dockside Extortionist"],
    mill_staples: ["Bruvac the Grandiloquent", "Maddening Cacophony", "Mesmeric Orb", "Mindcrank", "Ruin Crab", "Fractured Sanity"],
    wheel_staples: ["Wheel of Fortune", "Windfall", "Wheel of Misfortune", "Reforge the Soul"],
    aristocrats_staples: ["Blood Artist", "Zulaport Cutthroat", "Cruel Celebrant", "Bastion of Remembrance"],
    tokens_staples: ["Doubling Season", "Anointed Procession", "Parallel Lives", "Mondrak, Glory Dominus"],
    lifegain_staples: ["Soul Warden", "Soul's Attendant", "Authority of the Consuls", "Ajani's Pridemate"],
    reanimator_staples: ["Reanimate", "Animate Dead", "Necromancy", "Dance of the Dead"]
  },
  regex_rules: {
    TutorAny: ["search your library for a card"],
    Tutor: ["search your library for", "search .*library for"],
    TutorCreature: ["search your library for a creature card"],
    TutorRestricted: ["search your library for an? (artifact|enchantment|instant|sorcery|land) card"],
    Counterspell: ["counter target", "countered"],
    SpotRemoval: ["destroy target", "exile target", "deals? \\d+ damage to target"],
    BoardWipe: ["destroy all", "exile all", "destroy each"],
    Recursion: ["return target .* from your graveyard", "return .* card from your graveyard"],
    Mill: ["mill", "put the top .* cards? of .* library into .* graveyard", "puts? the top .* cards? of .* library into .* graveyard"],
    Discard: ["target player discards", "each opponent discards", "each player discards", "discard a card"],
    Wheel: ["discard .* hand, then draw", "each player discards .* and draws", "then draws? that many cards"],
    Aristocrats: ["whenever .* dies, .* loses? \\d+ life", "whenever .* dies, you gain \\d+ life", "sacrifice a creature:"],
    Tokens: ["create .* token", "create one or more", "populate"],
    Lifegain: ["gain \\d+ life", "you gain life", "whenever you gain life"],
    Reanimator: ["return target creature card from your graveyard to the battlefield", "return target creature card from a graveyard to the battlefield", "return target creature from your graveyard to the battlefield", "put target creature card from a graveyard onto the battlefield"],
    Draw: ["draw (?:one|two|three|[0-9]+) card", "draw cards", "draw a card"],
    Loot: ["draw .* then discard", "draw a card, then discard a card"],
    RampLand: ["search your library for a land card", "put a land card onto the battlefield"],
    Ritual: ["add (?:\\w+ )?mana", "add \\w+ to your mana pool"],
    SacOutlet: ["sacrifice .*:"],
    Stax: ["players? can'?t", "skip your untap step", "tax", "costs? \\d+ more"],
    Hatebear: ["noncreature spells? cost", "activated abilities? of artifacts? can'?t"],
    ComboPiece: ["if you control .* then", "if you have .* you win", "infinite"],
    ComboEnabler: ["you may cast from your graveyard", "cast cards? from your graveyard"],
    ProduceMana: ["tap: add", "add .*mana"]
  },
  regex_negative: {
    Draw: ["you may draw"],
    Tutor: ["may search for a basic land card"],
    ProduceMana: ["add mana equal to"]
  }
};

const ui = {
  input: document.getElementById("deckInput"),
  analyze: document.getElementById("analyzeBtn"),
  clear: document.getElementById("clearBtn"),
  demo: document.getElementById("demoBtn"),
  download: document.getElementById("downloadBtn"),
  progress: document.getElementById("progressFill"),
  status: document.getElementById("statusText"),
  log: document.getElementById("logText"),
  deckName: document.getElementById("deckName"),
  commanderName: document.getElementById("commanderName"),
  commanderImg: document.getElementById("commanderImg"),
  matchup: document.getElementById("matchupText"),
  bars: document.getElementById("bars"),
  comboList: document.getElementById("comboList")
};

let config = defaultConfig;
let lastReport = null;

function log(message) {
  const time = new Date().toLocaleTimeString();
  const line = `[${time}] ${message}`;
  const existing = ui.log.textContent || "";
  ui.log.textContent = (existing + "\n" + line).trim().slice(-MAX_LOG);
  ui.log.scrollTop = ui.log.scrollHeight;
  console.log(line);
}

function setStatus(text) {
  ui.status.textContent = text;
}

function setProgress(current, total) {
  if (!total) {
    ui.progress.style.width = "0%";
    return;
  }
  ui.progress.style.width = `${Math.min(100, Math.round((current / total) * 100))}%`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(url, options = {}, retries = 4) {
  let backoff = 400;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    try {
      const resp = await fetch(url, options);
      if (resp.status === 429 || resp.status >= 500) {
        await sleep(backoff);
        backoff *= 2;
        continue;
      }
      const data = await resp.json();
      return data;
    } catch (err) {
      await sleep(backoff);
      backoff *= 2;
    }
  }
  throw new Error(`Failed request: ${url}`);
}

function loadCache() {
  try {
    return JSON.parse(localStorage.getItem(CACHE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveCache(cache) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(cache));
  } catch {
    // ignore
  }
}

async function loadConfig() {
  try {
    const resp = await fetch("./labels_config.json");
    if (resp.ok) {
      config = await resp.json();
      log("Loaded labels_config.json");
      return;
    }
  } catch {
    // ignore
  }
  config = defaultConfig;
  log("Using built-in config defaults");
}

function normalizeOracleText(card) {
  let text = "";
  if (card.oracle_text) {
    text = card.oracle_text;
  } else if (card.card_faces) {
    text = card.card_faces.map((f) => f.oracle_text || "").join("\n//\n");
  }
  return text.replace(/\([^)]*\)/g, "").replace(/\s+/g, " ").trim().toLowerCase();
}

function cleanCardName(line) {
  let name = line.trim();
  name = name.replace(/\s+\(.*\)\s*\d+$/, "");
  name = name.replace(/\s+\[.*\]\s*\d+$/, "");
  name = name.replace(/\s+\*?F\*?$/, "");
  return name.trim();
}

function parsePlainDeck(text) {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const counts = new Map();
  let deckName = "Untitled";
  const commanders = [];
  let section = "main";

  for (const line of lines) {
    const low = line.toLowerCase();
    if (low === "commander" || low === "commanders") {
      section = "commander";
      continue;
    }
    if (["mainboard", "main deck", "maindeck", "main"].includes(low)) {
      section = "main";
      continue;
    }
    if (["sideboard", "maybeboard", "maybe board", "may be board"].includes(low)) {
      section = "ignore";
      continue;
    }
    if (low.startsWith("name:") || low.startsWith("deck:")) {
      deckName = line.split(":", 2)[1].trim();
      continue;
    }
    if (low.startsWith("commander:") || low.startsWith("commanders:")) {
      const val = line.split(":", 2)[1];
      val.split(",").map((v) => v.trim()).filter(Boolean).forEach((c) => commanders.push(c));
      section = "main";
      continue;
    }
    if (/^[a-z ].*\(\d+\)$/.test(low)) continue;
    if (low.startsWith("//") || low.startsWith("#")) continue;
    if (section === "ignore") continue;

    let qty = 1;
    let name = "";
    const m1 = line.match(/^(\d+)x?\s+(.+)$/i);
    if (m1) {
      qty = parseInt(m1[1], 10);
      name = cleanCardName(m1[2]);
    } else {
      const m2 = line.match(/^(.+?)\s+x(\d+)$/i);
      if (m2) {
        name = cleanCardName(m2[1]);
        qty = parseInt(m2[2], 10);
      } else {
        name = cleanCardName(line);
      }
    }
    if (!name) continue;
    counts.set(name, (counts.get(name) || 0) + qty);
    if (section === "commander" && !commanders.includes(name)) commanders.push(name);
  }

  return { name: deckName, commanders, cards: Object.fromEntries(counts) };
}

function extractFirstUrl(text) {
  const match = text.match(/https?:\/\/[^\s]+/);
  return match ? match[0] : null;
}

function deckFromCardListResponse(data, fallbackName = "Untitled") {
  const main = data.main || [];
  const commanders = (data.commanders || []).map((c) => c.card);
  const counts = new Map();
  for (const entry of main) {
    const name = entry.card;
    const qty = entry.quantity || 1;
    counts.set(name, (counts.get(name) || 0) + qty);
  }
  for (const cmd of commanders) {
    if (!counts.has(cmd)) counts.set(cmd, 1);
  }
  return { name: fallbackName, commanders, cards: Object.fromEntries(counts) };
}

async function detectAndResolveDeck(text) {
  const trimmed = text.trim();
  const url = extractFirstUrl(trimmed);
  if (url) {
    try {
      log(`Resolving URL via Commander Spellbook: ${url}`);
      const data = await fetchJson(`${CSB_BASE}/card-list-from-url?url=${encodeURIComponent(url)}`);
      return deckFromCardListResponse(data, "Untitled");
    } catch (err) {
      log("URL resolution failed, falling back to local parsing");
    }
  }
  return parsePlainDeck(trimmed);
}

function compileRegexes(obj) {
  const out = {};
  for (const [label, patterns] of Object.entries(obj || {})) {
    out[label] = patterns.map((p) => new RegExp(p, "i"));
  }
  return out;
}

function labelCard(card, name) {
  const labels = new Map();
  const curated = config.curated_lists || {};
  const compiled = compileRegexes(config.regex_rules || {});
  const compiledNeg = compileRegexes(config.regex_negative || {});

  for (const [listName, names] of Object.entries(curated)) {
    if (names.some((n) => n.toLowerCase() === name.toLowerCase())) {
      labels.set(listName, { label: listName, confidence: 0.95, evidence: `curated:${listName}` });
    }
  }

  const typeLine = (card.type_line || "").toLowerCase();
  const oracle = normalizeOracleText(card);
  const keywords = (card.keywords || []).map((k) => k.toLowerCase());
  const cmc = card.cmc;
  const produced = card.produced_mana || [];
  const legalities = card.legalities || {};

  if (typeLine.includes("land") && !typeLine.includes("creature")) labels.set("IsLand", { label: "IsLand", confidence: 0.85, evidence: "type_line:land" });
  if (typeLine.includes("creature")) labels.set("IsCreature", { label: "IsCreature", confidence: 0.85, evidence: "type_line:creature" });
  if (typeLine.includes("legendary")) labels.set("IsLegendary", { label: "IsLegendary", confidence: 0.9, evidence: "type_line:legendary" });
  if (legalities.commander === "legal") labels.set("CommanderLegal", { label: "CommanderLegal", confidence: 0.8, evidence: "legalities:commander" });
  if (typeof cmc === "number") labels.set(`CMC:${Math.trunc(cmc)}`, { label: `CMC:${Math.trunc(cmc)}`, confidence: 0.7, evidence: "cmc" });
  for (const kw of keywords) labels.set(`HasKeyword:${kw}`, { label: `HasKeyword:${kw}`, confidence: 0.8, evidence: "keywords" });

  const producesMana = produced.length > 0 || (oracle.includes("add") && oracle.includes("mana"));
  if (producesMana) labels.set("ProducesMana", { label: "ProducesMana", confidence: 0.8, evidence: "produced_mana/oracle" });
  if (typeLine.includes("artifact")) {
    if (producesMana) labels.set("ManaRock", { label: "ManaRock", confidence: 0.88, evidence: "artifact+produces_mana" });
    else labels.set("Artifact", { label: "Artifact", confidence: 0.7, evidence: "type_line:artifact" });
  }
  if (typeLine.includes("creature") && producesMana) labels.set("ManaDork", { label: "ManaDork", confidence: 0.88, evidence: "creature+produces_mana" });

  for (const [lab, patterns] of Object.entries(compiled)) {
    if (patterns.some((p) => p.test(oracle))) {
      const negs = compiledNeg[lab] || [];
      if (negs.some((n) => n.test(oracle))) continue;
      const existing = labels.get(lab);
      if (!existing || existing.confidence < 0.6) labels.set(lab, { label: lab, confidence: 0.6, evidence: `regex:${lab}` });
    }
  }

  return Array.from(labels.values());
}

function aggregateDeck(labelsByCard, quantities) {
  const counts = {};
  for (const [name, labels] of Object.entries(labelsByCard)) {
    const qty = quantities[name] || 1;
    for (const label of labels) {
      counts[label.label] = (counts[label.label] || 0) + qty;
    }
  }
  const total = Object.values(quantities).reduce((a, b) => a + b, 0) || 1;
  const percentages = {};
  for (const [k, v] of Object.entries(counts)) {
    percentages[k] = Math.round((v / total) * 1000) / 10;
  }
  return { counts, percentages, total };
}

function deriveArchetypes(aggregate) {
  const counts = aggregate.counts || {};
  const s = (k) => counts[k] || 0;
  const ramp = s("ManaRock") + s("ManaDork") + s("RampLand") + s("fast_mana") + s("Ritual");
  const tutors = s("TutorAny") + s("TutorCreature") + s("Tutor") + s("TutorRestricted") + s("unconditional_tutors");
  const interaction = s("Counterspell") + s("SpotRemoval") + s("BoardWipe") + s("free_interaction");
  const draw = s("Draw") + s("Loot");
  const stax = s("staple_stax") + s("Stax") + s("Hatebear");
  const recursion = s("Recursion");
  const combo = s("ComboPiece") + s("ComboEnabler") + s("combo_enablers");
  const total = aggregate.total || 1;
  const pct = (x) => Math.round((x / total) * 1000) / 10;
  const others = Math.max(0, total - (ramp + tutors + interaction + draw + stax + recursion + combo));
  return {
    Ramp: pct(ramp),
    Tutors: pct(tutors),
    Interaction: pct(interaction),
    Draw: pct(draw),
    Stax: pct(stax),
    Recursion: pct(recursion),
    Combo: pct(combo),
    Other: pct(others)
  };
}

function matchupAnalysis(aggregate) {
  const counts = aggregate.counts || {};
  const scores = {
    Aggro: (counts.IsCreature || 0) - ((counts.Counterspell || 0) + (counts.BoardWipe || 0)),
    Control: (counts.Counterspell || 0) + (counts.BoardWipe || 0) + (counts.free_interaction || 0),
    Combo: (counts.Tutor || 0) + (counts.TutorAny || 0) + (counts.fast_mana || 0) + (counts.ManaRock || 0),
    Engine: (counts.Draw || 0) + (counts.Recursion || 0)
  };
  const max = Math.max(...Object.values(scores), 1);
  for (const k of Object.keys(scores)) scores[k] = scores[k] / max;
  const sorted = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  const best = sorted[0][0];
  const worst = sorted[sorted.length - 1][0];
  return {
    scores,
    strong_against: [best],
    weak_against: [worst],
    because: `High ${best} signals and lower ${worst} signals based on labels.`
  };
}

function getImageUrl(card) {
  if (card.image_uris) return card.image_uris.normal || card.image_uris.large;
  if (card.card_faces && card.card_faces[0] && card.card_faces[0].image_uris) {
    return card.card_faces[0].image_uris.normal || card.card_faces[0].image_uris.large;
  }
  return null;
}

async function fetchScryfallCard(name, cache) {
  const key = name.toLowerCase();
  if (cache[key]) return cache[key];
  const url = `${SCRYFALL_NAMED}${encodeURIComponent(name)}`;
  const data = await fetchJson(url);
  cache[key] = data;
  saveCache(cache);
  await sleep(80);
  return data;
}

async function fetchSpellbookCombos(quantities, commanders) {
  const main = [];
  for (const [name, qty] of Object.entries(quantities)) {
    if (commanders.includes(name)) continue;
    main.push({ card: cleanCardName(name), quantity: qty });
  }
  const payload = {
    main: main.slice(0, 600),
    commanders: commanders.slice(0, 12).map((c) => ({ card: cleanCardName(c), quantity: 1 }))
  };
  const data = await fetchJson(`${CSB_BASE}/find-my-combos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  let result = data.results;
  if (Array.isArray(result)) result = result[0] || {};
  if (!result || !result.included) return { included: [] };
  return result;
}

function renderBars(percentages) {
  const hideExact = new Set(["CommanderLegal", "ProducesMana", "IsLand", "IsCreature", "IsLegendary", "Artifact"]);
  const hidePrefixes = ["CMC:", "HasKeyword:"];
  const entries = Object.entries(percentages)
    .filter(([k]) => !hideExact.has(k) && !hidePrefixes.some((p) => k.startsWith(p)))
    .sort((a, b) => b[1] - a[1]);

  ui.bars.innerHTML = "";
  for (const [label, pct] of entries) {
    const row = document.createElement("div");
    row.className = "bar-row";

    const nameEl = document.createElement("div");
    nameEl.className = "bar-label";
    nameEl.textContent = label;

    const track = document.createElement("div");
    track.className = "bar-track";
    const fill = document.createElement("div");
    fill.className = "bar-fill";
    fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    track.appendChild(fill);

    const pctEl = document.createElement("div");
    pctEl.className = "bar-pct";
    pctEl.textContent = `${pct.toFixed(1)}%`;

    row.appendChild(nameEl);
    row.appendChild(track);
    row.appendChild(pctEl);
    ui.bars.appendChild(row);
  }
}

function renderCombos(combos) {
  ui.comboList.innerHTML = "";
  const included = combos.included || [];
  if (!included.length) {
    ui.comboList.textContent = "No combos found.";
    return;
  }
  for (const combo of included.slice(0, 50)) {
    const id = combo.id;
    const text = combo.description || combo.notes || id;
    const link = document.createElement("a");
    link.href = `https://commanderspellbook.com/combo/${id}`;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `${id}: ${text}`;
    ui.comboList.appendChild(link);
  }
}

function renderMatchup(matchup) {
  ui.matchup.textContent = `Strong Against: ${(matchup.strong_against || []).join(", ") || "-"}\n` +
    `Weak Against: ${(matchup.weak_against || []).join(", ") || "-"}\n` +
    `Notes: ${matchup.because || "-"}`;
}

function downloadReport(report) {
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${report.deck_name || "deck"}_labels.json`;
  a.click();
  URL.revokeObjectURL(url);
}

async function analyze() {
  ui.analyze.disabled = true;
  ui.download.disabled = true;
  setStatus("Resolving deck");
  setProgress(0, 1);

  const text = ui.input.value.trim();
  if (!text) {
    setStatus("No input");
    ui.analyze.disabled = false;
    return;
  }

  const deck = await detectAndResolveDeck(text);
  log(`Deck resolved: ${deck.name}`);

  const quantities = deck.cards || {};
  const commanders = deck.commanders || [];
  const names = Object.keys(quantities);

  const cache = loadCache();
  const labelsByCard = {};
  const cardReports = [];

  let idx = 0;
  for (const name of names) {
    idx += 1;
    setStatus(`Fetching ${idx}/${names.length}: ${name}`);
    setProgress(idx, names.length);
    const card = await fetchScryfallCard(name, cache);
    const labels = labelCard(card, name);
    labelsByCard[name] = labels;
    cardReports.push({
      name,
      quantity: quantities[name] || 1,
      scryfall: {
        type_line: card.type_line,
        cmc: card.cmc,
        keywords: card.keywords,
        produced_mana: card.produced_mana,
        oracle_text: normalizeOracleText(card)
      },
      labels
    });
  }

  const aggregate = aggregateDeck(labelsByCard, quantities);
  const derived = deriveArchetypes(aggregate);
  const matchup = matchupAnalysis(aggregate);

  let combos = { included: [] };
  try {
    setStatus("Querying Commander Spellbook");
    combos = await fetchSpellbookCombos(quantities, commanders);
  } catch {
    log("Commander Spellbook lookup failed.");
  }

  const report = {
    deck_name: deck.name,
    commanders,
    cards: cardReports,
    aggregate,
    derived,
    matchup,
    commander_spellbook: combos
  };

  lastReport = report;
  ui.download.disabled = false;

  const cmd = commanders[0];
  if (cmd) {
    const cmdCard = await fetchScryfallCard(cmd, cache);
    const img = getImageUrl(cmdCard);
    if (img) ui.commanderImg.src = img;
  }

  ui.deckName.textContent = `Deck: ${deck.name}`;
  ui.commanderName.textContent = `Commander: ${commanders.join(", ") || "-"}`;
  renderBars(aggregate.percentages || {});
  renderCombos(combos);
  renderMatchup(matchup);

  setStatus("Done");
  setProgress(0, 1);
  ui.analyze.disabled = false;
}

ui.clear.addEventListener("click", () => {
  ui.input.value = "";
});

ui.demo.addEventListener("click", () => {
  ui.input.value = "Commander\n1 Captain N'ghathrod\n\nMainboard\n1 Sol Ring\n1 Mesmeric Orb\n1 Mindcrank\n1 Ruin Crab\n1 Maddening Cacophony\n1 Windfall\n1 Reanimate\n1 Rhystic Study\n1 Island\n1 Swamp\n";
});

ui.analyze.addEventListener("click", () => {
  analyze().catch((err) => {
    log(`Error: ${err.message}`);
    setStatus("Error");
    ui.analyze.disabled = false;
  });
});

ui.download.addEventListener("click", () => {
  if (lastReport) downloadReport(lastReport);
});

loadConfig();
