#!/usr/bin/env python3
"""
Commander Deck Labeler

Single-file Tkinter app.
Dependencies: requests, Pillow
Run: python deck_labeler.py
"""
from __future__ import annotations

import json
import os
import queue
import random
import re
import threading
import time
import urllib.parse
from collections import Counter
from datetime import datetime

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:
    raise

try:
    import requests
except Exception:
    raise

try:
    from PIL import Image, ImageTk
except Exception:
    raise

ROOT = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(ROOT, "labels_config.json")
CACHE_PATH = os.path.join(ROOT, "scryfall_cache.json")
OUTPUT_DIR = os.path.join(ROOT, "output")

REQUESTS_TIMEOUT = 20


DEFAULT_CONFIG = {
    "version": 1,
    "curated_lists": {
        "fast_mana": ["Sol Ring", "Mana Crypt", "Mana Vault", "Grim Monolith"],
        "unconditional_tutors": ["Demonic Tutor", "Vampiric Tutor", "Enlightened Tutor"],
        "free_interaction": ["Force of Will", "Force of Negation", "Fierce Guardianship", "Swan Song"],
        "staple_board_wipes": ["Wrath of God", "Supreme Verdict", "Damnation", "Toxic Deluge"],
        "staple_stax": ["Smokestack", "Winter Orb", "Stasis", "Rule of Law"],
        "combo_enablers": ["Underworld Breach", "Dockside Extortionist"],
        "mill_staples": ["Bruvac the Grandiloquent", "Maddening Cacophony", "Mesmeric Orb", "Mindcrank", "Ruin Crab", "Fractured Sanity"],
        "wheel_staples": ["Wheel of Fortune", "Windfall", "Wheel of Misfortune", "Reforge the Soul"],
        "aristocrats_staples": ["Blood Artist", "Zulaport Cutthroat", "Cruel Celebrant", "Bastion of Remembrance"],
        "tokens_staples": ["Doubling Season", "Anointed Procession", "Parallel Lives", "Mondrak, Glory Dominus"],
        "lifegain_staples": ["Soul Warden", "Soul's Attendant", "Authority of the Consuls", "Ajani's Pridemate"],
        "reanimator_staples": ["Reanimate", "Animate Dead", "Necromancy", "Dance of the Dead"]
    },
    "regex_rules": {
        "TutorAny": ["search your library for a card"],
        "Tutor": ["search your library for", "search .*library for"],
        "TutorCreature": ["search your library for a creature card"],
        "TutorRestricted": ["search your library for an? (artifact|enchantment|instant|sorcery|land) card"],
        "Counterspell": ["counter target", "countered"],
        "SpotRemoval": ["destroy target", "exile target", r"deals? \d+ damage to target"],
        "BoardWipe": ["destroy all", "exile all", "destroy each"],
        "Recursion": ["return target .* from your graveyard", "return .* card from your graveyard"],
        "Mill": [
            "mill",
            r"put the top .* cards? of .* library into .* graveyard",
            r"puts? the top .* cards? of .* library into .* graveyard"
        ],
        "Discard": ["target player discards", "each opponent discards", "each player discards", "discard a card"],
        "Wheel": ["discard .* hand, then draw", "each player discards .* and draws", "then draws? that many cards"],
        "Aristocrats": ["whenever .* dies, .* loses? \\d+ life", "whenever .* dies, you gain \\d+ life", "sacrifice a creature:"],
        "Tokens": ["create .* token", "create one or more", "populate"],
        "Lifegain": ["gain \\d+ life", "you gain life", "whenever you gain life"],
        "Reanimator": ["return target creature card from your graveyard to the battlefield", "return target creature card from a graveyard to the battlefield", "return target creature from your graveyard to the battlefield", "put target creature card from a graveyard onto the battlefield"],
        "Draw": ["draw (?:one|two|three|[0-9]+) card", "draw cards", "draw a card"],
        "Loot": ["draw .* then discard", "draw a card, then discard a card"],
        "RampLand": ["search your library for a land card", "put a land card onto the battlefield"],
        "Ritual": [r"add (?:\w+ )?mana", r"add \w+ to your mana pool"],
        "SacOutlet": ["sacrifice .*:"],
        "Stax": ["players? can'?t", "skip your untap step", "tax", r"costs? \d+ more"],
        "Hatebear": ["noncreature spells? cost", "activated abilities? of artifacts? can'?t"],
        "ComboPiece": ["if you control .* then", "if you have .* you win", "infinite"],
        "ComboEnabler": ["you may cast from your graveyard", "cast cards? from your graveyard"],
        "ProduceMana": ["tap: add", "add .*mana"]
    },
    "regex_negative": {
        "Draw": ["you may draw"],
        "Tutor": ["may search for a basic land card"],
        "ProduceMana": ["add mana equal to"]
    }
}


def ensure_output_dir(path: str | None = None) -> str:
    path = path or OUTPUT_DIR
    os.makedirs(path, exist_ok=True)
    return path


def load_or_create_config(path: str = CONFIG_PATH) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CONFIG, f, indent=2)
    return DEFAULT_CONFIG


class HttpClient:
    def __init__(self, min_interval: float = 0.12):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def _wait_rate_limit(self) -> None:
        with self._lock:
            now = time.time()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.time()

    def get_json(self, url: str, retries: int = 5, status_q: queue.Queue | None = None) -> dict | None:
        backoff = 1.0
        for attempt in range(1, retries + 1):
            try:
                self._wait_rate_limit()
                if status_q:
                    status_q.put(("status", f"GET {url} (try {attempt})"))
                resp = requests.get(url, timeout=REQUESTS_TIMEOUT)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504):
                    time.sleep(backoff + random.random() * 0.2)
                    backoff *= 2
                    continue
                try:
                    return resp.json()
                except Exception:
                    return {"error": f"HTTP {resp.status_code}"}
            except requests.RequestException:
                time.sleep(backoff + random.random() * 0.2)
                backoff *= 2
        return {"error": "request_failed"}


class ScryfallCache:
    def __init__(self, path: str = CACHE_PATH):
        self.path = path
        self.lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {"version": 1, "cards": {}}
        else:
            self.data = {"version": 1, "cards": {}}

    def _key(self, name: str) -> str:
        return name.strip().lower()

    def get(self, name: str):
        with self.lock:
            return self.data.get("cards", {}).get(self._key(name))

    def set(self, name: str, obj: dict) -> None:
        with self.lock:
            self.data.setdefault("cards", {})[self._key(name)] = obj
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(self.data, f)
            except Exception:
                pass


class ScryfallClient:
    def __init__(self, http: HttpClient, cache: ScryfallCache):
        self.http = http
        self.cache = cache

    def fetch_card(self, name: str, status_q: queue.Queue | None = None) -> dict:
        cached = self.cache.get(name)
        if cached:
            return cached
        url = "https://api.scryfall.com/cards/named?exact=" + urllib.parse.quote(name)
        data = self.http.get_json(url, status_q=status_q) or {"error": "request_failed"}
        self.cache.set(name, data)
        return data


def normalize_oracle_text(card_json: dict) -> str:
    if not card_json:
        return ""
    if card_json.get("oracle_text"):
        txt = card_json.get("oracle_text", "")
    else:
        parts = [f.get("oracle_text", "") for f in card_json.get("card_faces", [])]
        txt = "\n//\n".join(parts)
    txt = re.sub(r"\([^)]*\)", "", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip().lower()


def get_card_image_url(card_json: dict) -> str | None:
    if card_json.get("image_uris"):
        return card_json["image_uris"].get("normal") or card_json["image_uris"].get("large")
    if card_json.get("card_faces"):
        face = card_json["card_faces"][0]
        if face.get("image_uris"):
            return face["image_uris"].get("normal") or face["image_uris"].get("large")
    return None


def clean_card_name(line: str) -> str:
    name = line.strip()
    name = re.sub(r"\s+\(.*\)\s*\d+$", "", name)
    name = re.sub(r"\s+\[.*\]\s*\d+$", "", name)
    name = re.sub(r"\s+\*?F\*?$", "", name)
    return name.strip()


def parse_plain_deck(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    counts = Counter()
    deck_name = "Untitled"
    commanders = []
    section = "main"

    for line in lines:
        low = line.lower()
        if low in {"commander", "commanders"}:
            section = "commander"
            continue
        if low in {"mainboard", "main deck", "maindeck", "main"}:
            section = "main"
            continue
        if low in {"sideboard", "maybeboard", "maybe board", "may be board"}:
            section = "ignore"
            continue
        if low.startswith("name:") or low.startswith("deck:"):
            deck_name = line.split(":", 1)[1].strip()
            continue
        if low.startswith("commander:") or low.startswith("commanders:"):
            _, val = line.split(":", 1)
            commanders = [v.strip() for v in val.split(",") if v.strip()]
            section = "main"
            continue
        if re.match(r"^[a-z ].*\(\d+\)$", low):
            continue
        if low.startswith("//") or low.startswith("#"):
            continue
        if section == "ignore":
            continue

        m = re.match(r"^(\d+)x?\s+(.+)$", line, re.I)
        if m:
            qty = int(m.group(1))
            name = clean_card_name(m.group(2))
            counts[name] += qty
            if section == "commander" and name not in commanders:
                commanders.append(name)
            continue
        m2 = re.match(r"^(.+?)\s+x(\d+)$", line, re.I)
        if m2:
            name = clean_card_name(m2.group(1))
            qty = int(m2.group(2))
            counts[name] += qty
            if section == "commander" and name not in commanders:
                commanders.append(name)
            continue

        name = clean_card_name(line)
        if name:
            counts[name] += 1
            if section == "commander" and name not in commanders:
                commanders.append(name)

    return {"name": deck_name, "commanders": commanders, "cards": dict(counts)}


def _extract_first_url(text: str, needle: str) -> str | None:
    m = re.search(r"https?://[^\s]+", text)
    if m and needle in m.group(0):
        return m.group(0)
    if needle in text:
        start = text.find(needle)
        return text[start - 8 if start >= 8 else 0 :].split()[0]
    return None


def _iter_cards_from_container(container):
    if isinstance(container, dict):
        for v in container.values():
            yield v
    elif isinstance(container, list):
        for v in container:
            yield v


def try_fetch_moxfield(url: str, http: HttpClient, status_q: queue.Queue | None = None) -> dict | None:
    parsed = urllib.parse.urlparse(url)
    deck_id = parsed.path.strip("/").split("/")[-1]
    candidates = [
        f"https://api.moxfield.com/v2/decks/{deck_id}",
        f"https://api.moxfield.com/decks/{deck_id}",
    ]
    for c in candidates:
        if status_q:
            status_q.put(("log", f"Trying Moxfield endpoint: {c}"))
        data = http.get_json(c, status_q=status_q)
        if not data or data.get("error"):
            continue
        name = data.get("name") or data.get("deckName") or f"Moxfield_{deck_id}"
        cards = {}
        commanders = []

        if data.get("commanders"):
            commanders = [c.get("name") for c in data.get("commanders", []) if c.get("name")]
        if data.get("boards"):
            main = data["boards"].get("mainboard") or data["boards"].get("main")
            if main and main.get("cards"):
                for entry in _iter_cards_from_container(main.get("cards")):
                    qty = entry.get("count", 1)
                    nm = (entry.get("card") or {}).get("name") or entry.get("name")
                    if nm:
                        cards[nm] = cards.get(nm, 0) + qty
        if not cards and data.get("sections"):
            for sec in data["sections"]:
                for entry in sec.get("cards", []):
                    qty = entry.get("count", 1)
                    nm = (entry.get("card") or {}).get("name") or entry.get("name")
                    if nm:
                        cards[nm] = cards.get(nm, 0) + qty
        if not cards and data.get("cards"):
            for entry in data.get("cards", []):
                qty = entry.get("count", 1)
                nm = (entry.get("card") or {}).get("name") or entry.get("name")
                if nm:
                    cards[nm] = cards.get(nm, 0) + qty

        if cards:
            return {"name": name, "commanders": commanders, "cards": cards}
    return None


def try_fetch_archidekt(url: str, http: HttpClient, status_q: queue.Queue | None = None) -> dict | None:
    parsed = urllib.parse.urlparse(url)
    deck_id = parsed.path.strip("/").split("/")[-1]
    candidates = [f"https://archidekt.com/api/decks/{deck_id}"]
    for c in candidates:
        if status_q:
            status_q.put(("log", f"Trying Archidekt endpoint: {c}"))
        data = http.get_json(c, status_q=status_q)
        if not data or data.get("error"):
            continue
        name = data.get("name") or f"Archidekt_{deck_id}"
        cards = {}
        commanders = []

        for slot in data.get("cards", []) or []:
            card = slot.get("card") or {}
            nm = card.get("name") or slot.get("cardName")
            qty = slot.get("quantity", 1)
            if nm:
                cards[nm] = cards.get(nm, 0) + qty

        if not cards:
            for slot in data.get("slots", []) or []:
                card = slot.get("card") or {}
                nm = card.get("name") or slot.get("cardName")
                qty = slot.get("quantity", 1)
                if nm:
                    cards[nm] = cards.get(nm, 0) + qty

        meta = data.get("metadata") or {}
        for ed in meta.get("commanderCards", []) or []:
            if ed.get("cardName"):
                commanders.append(ed.get("cardName"))

        if cards:
            return {"name": name, "commanders": commanders, "cards": cards}
    return None


def detect_and_resolve_deck(text: str, http: HttpClient, status_q: queue.Queue | None = None) -> dict:
    text = text.strip()
    url = _extract_first_url(text, "moxfield.com/decks/")
    if url:
        res = try_fetch_moxfield(url, http, status_q)
        if res:
            return res
    url = _extract_first_url(text, "archidekt.com/decks/")
    if url:
        res = try_fetch_archidekt(url, http, status_q)
        if res:
            return res
    return parse_plain_deck(text)


class LabelEngine:
    def __init__(self, config: dict):
        self.config = config
        self.compiled = {k: [re.compile(p, re.I) for p in v] for k, v in config.get("regex_rules", {}).items()}
        self.compiled_negative = {k: [re.compile(p, re.I) for p in v] for k, v in config.get("regex_negative", {}).items()}
        self.curated = {k: set(v) for k, v in config.get("curated_lists", {}).items()}

    def _add_label(self, labels: dict, label: str, confidence: float, evidence: str) -> None:
        if label not in labels or labels[label]["confidence"] < confidence:
            labels[label] = {"label": label, "confidence": confidence, "evidence": evidence}

    def label_card(self, card_json: dict, name: str) -> list:
        labels: dict = {}

        for list_name, names in self.curated.items():
            if any(name.lower() == nm.lower() for nm in names):
                self._add_label(labels, list_name, 0.95, f"curated:{list_name}")

        type_line = (card_json.get("type_line") or "").lower()
        oracle = normalize_oracle_text(card_json)
        keywords = [k.lower() for k in (card_json.get("keywords") or [])]
        cmc = card_json.get("cmc")
        produced = card_json.get("produced_mana") or []
        legalities = card_json.get("legalities") or {}

        if "land" in type_line and "creature" not in type_line:
            self._add_label(labels, "IsLand", 0.85, "type_line:land")
        if "creature" in type_line:
            self._add_label(labels, "IsCreature", 0.85, "type_line:creature")
        if "legendary" in type_line:
            self._add_label(labels, "IsLegendary", 0.9, "type_line:legendary")
        if legalities.get("commander") == "legal":
            self._add_label(labels, "CommanderLegal", 0.8, "legalities:commander")
        if isinstance(cmc, (int, float)):
            self._add_label(labels, f"CMC:{int(cmc)}", 0.7, "cmc")

        for kw in keywords:
            self._add_label(labels, f"HasKeyword:{kw}", 0.8, "keywords")

        produces_mana = bool(produced) or ("add" in oracle and "mana" in oracle)
        if produces_mana:
            self._add_label(labels, "ProducesMana", 0.8, "produced_mana/oracle")

        if "artifact" in type_line:
            if produces_mana:
                self._add_label(labels, "ManaRock", 0.88, "artifact+produces_mana")
            else:
                self._add_label(labels, "Artifact", 0.7, "type_line:artifact")
        if "creature" in type_line and produces_mana:
            self._add_label(labels, "ManaDork", 0.88, "creature+produces_mana")

        for lab, pats in self.compiled.items():
            if any(p.search(oracle) for p in pats):
                negs = self.compiled_negative.get(lab, [])
                if any(n.search(oracle) for n in negs):
                    continue
                self._add_label(labels, lab, 0.6, f"regex:{lab}")

        return list(labels.values())


def aggregate_deck(labels_by_card: dict, quantities: dict) -> dict:
    counts = Counter()
    for name, labs in labels_by_card.items():
        qty = quantities.get(name, 1)
        for l in labs:
            counts[l["label"]] += qty
    total_cards = sum(quantities.values()) or 1
    percentages = {k: round((v / total_cards) * 100, 2) for k, v in counts.items()}
    return {"counts": dict(counts), "percentages": percentages, "total": total_cards}


def derive_archetypes(aggregate_counts: dict) -> dict:
    counts = aggregate_counts.get("counts", {})

    def s(k):
        return counts.get(k, 0)

    ramp = s("ManaRock") + s("ManaDork") + s("RampLand") + s("fast_mana") + s("Ritual")
    tutors = s("TutorAny") + s("TutorCreature") + s("Tutor") + s("TutorRestricted") + s("unconditional_tutors")
    interaction = s("Counterspell") + s("SpotRemoval") + s("BoardWipe") + s("free_interaction")
    draw = s("Draw") + s("Loot")
    stax = s("staple_stax") + s("Stax") + s("Hatebear")
    recursion = s("Recursion")
    combo = s("ComboPiece") + s("ComboEnabler") + s("combo_enablers")
    total = aggregate_counts.get("total", 1) or 1

    def pct(x):
        return round((x / total) * 100, 1)

    others = max(0, total - (ramp + tutors + interaction + draw + stax + recursion + combo))
    return {
        "Ramp": pct(ramp),
        "Tutors": pct(tutors),
        "Interaction": pct(interaction),
        "Draw": pct(draw),
        "Stax": pct(stax),
        "Recursion": pct(recursion),
        "Combo": pct(combo),
        "Other": pct(others),
    }


def matchup_analysis(aggregate: dict) -> dict:
    counts = aggregate.get("counts", {})
    scores = {
        "Aggro": counts.get("IsCreature", 0) - (counts.get("Counterspell", 0) + counts.get("BoardWipe", 0)),
        "Control": counts.get("Counterspell", 0) + counts.get("BoardWipe", 0) + counts.get("free_interaction", 0),
        "Combo": counts.get("Tutor", 0) + counts.get("TutorAny", 0) + counts.get("fast_mana", 0) + counts.get("ManaRock", 0),
        "Engine": counts.get("Draw", 0) + counts.get("Recursion", 0),
    }
    maxscore = max(scores.values()) if scores else 1
    for k in scores:
        scores[k] = scores[k] / (maxscore or 1)
    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best = sorted_scores[0][0]
    worst = sorted_scores[-1][0]
    because = f"High {best} signals and lower {worst} signals based on labels."
    return {"scores": scores, "strong_against": [best], "weak_against": [worst], "because": because}


class Analyzer:
    def __init__(self, http: HttpClient, scryfall: ScryfallClient, labeler: LabelEngine):
        self.http = http
        self.scryfall = scryfall
        self.labeler = labeler

    def analyze(self, text: str, status_q: queue.Queue) -> dict:
        status_q.put(("status", "Resolving deck"))
        deck = detect_and_resolve_deck(text, self.http, status_q=status_q)
        name = deck.get("name") or "Untitled"
        status_q.put(("log", f"Deck resolved: {name}"))

        quantities = deck.get("cards", {})
        commanders = deck.get("commanders") or []
        unique_names = list(quantities.keys())

        status_q.put(("progress_init", len(unique_names)))

        labels_by_card = {}
        card_reports = []
        for idx, nm in enumerate(unique_names, start=1):
            status_q.put(("status", f"Fetching card {idx}/{len(unique_names)}: {nm}"))
            card_json = self.scryfall.fetch_card(nm, status_q=status_q)
            if card_json.get("object") == "error":
                status_q.put(("log", f"Scryfall error for {nm}: {card_json.get('details') or card_json}"))
            labs = self.labeler.label_card(card_json, nm)
            labels_by_card[nm] = labs
            card_reports.append({
                "name": nm,
                "quantity": quantities.get(nm, 1),
                "scryfall": {
                    "type_line": card_json.get("type_line"),
                    "cmc": card_json.get("cmc"),
                    "keywords": card_json.get("keywords"),
                    "produced_mana": card_json.get("produced_mana"),
                    "oracle_text": normalize_oracle_text(card_json),
                },
                "labels": labs,
            })
            status_q.put(("progress", idx))

        aggregate = aggregate_deck(labels_by_card, quantities)
        derived = derive_archetypes(aggregate)
        matchup = matchup_analysis(aggregate)

        return {
            "deck_name": name,
            "commanders": commanders,
            "cards": card_reports,
            "aggregate": aggregate,
            "derived": derived,
            "matchup": matchup,
        }


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Commander Deck Labeler")
        self.config = load_or_create_config()
        self.http = HttpClient()
        self.cache = ScryfallCache()
        self.scryfall = ScryfallClient(self.http, self.cache)
        self.label_engine = LabelEngine(self.config)
        self.analyzer = Analyzer(self.http, self.scryfall, self.label_engine)
        self.output_dir = ensure_output_dir()
        self.status_q: queue.Queue = queue.Queue()
        self.tkimg = None
        self._apply_dark_theme()
        self._build_ui()
        self._poll_status()

    def _apply_dark_theme(self) -> None:
        bg = "#0f1115"
        panel = "#161a22"
        text_bg = "#0f131a"
        text_fg = "#e6e8ef"
        muted = "#aab1c3"
        accent = "#3b82f6"

        self.root.configure(bg=bg)
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=bg, foreground=text_fg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=text_fg)
        style.configure("TButton", background=panel, foreground=text_fg, padding=6)
        style.map("TButton", background=[("active", "#1b2130")])
        style.configure("TProgressbar", troughcolor=panel, background=accent)

        self._colors = {
            "bg": bg,
            "panel": panel,
            "text_bg": text_bg,
            "text_fg": text_fg,
            "muted": muted,
            "accent": accent,
        }

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root)
        top.pack(fill=tk.BOTH, expand=True)
        self.input_text = tk.Text(top, height=8, bg=self._colors["text_bg"], fg=self._colors["text_fg"], insertbackground=self._colors["text_fg"])
        self.input_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        ctrl = ttk.Frame(top)
        ctrl.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)
        ttk.Button(ctrl, text="Analyze Deck", command=self.on_analyze).pack(fill=tk.X, pady=2)
        ttk.Button(ctrl, text="Clear", command=lambda: self.input_text.delete(1.0, tk.END)).pack(fill=tk.X, pady=2)
        ttk.Button(ctrl, text="Choose Output Folder", command=self.choose_output_folder).pack(fill=tk.X, pady=2)
        ttk.Button(ctrl, text="Load Demo Deck", command=self.load_demo).pack(fill=tk.X, pady=2)

        mid = ttk.Frame(self.root)
        mid.pack(fill=tk.X, padx=6, pady=6)
        self.progress = ttk.Progressbar(mid, mode="determinate")
        self.progress.pack(fill=tk.X)
        self.status_label = ttk.Label(mid, text="Idle", foreground=self._colors["muted"])
        self.status_label.pack(anchor=tk.W)

        log_frame = ttk.Frame(self.root)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.log_text = tk.Text(log_frame, height=6, bg=self._colors["text_bg"], fg=self._colors["text_fg"], insertbackground=self._colors["text_fg"])
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=log_scroll.set)

        summary = ttk.Frame(self.root)
        summary.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        summary_left = ttk.Frame(summary)
        summary_left.pack(side=tk.LEFT, fill=tk.Y, padx=6)
        summary_right = ttk.Frame(summary)
        summary_right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)

        self.cmd_img_label = ttk.Label(summary_left)
        self.cmd_img_label.pack()
        self.deck_label = ttk.Label(summary_left, text="Deck: ")
        self.deck_label.pack(anchor=tk.W)
        self.commanders_label = ttk.Label(summary_left, text="Commander: ")
        self.commanders_label.pack(anchor=tk.W)

        self.canvas = tk.Canvas(summary_right, width=380, height=230, bg=self._colors["bg"], highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.matchup_box = tk.Text(summary_right, height=6, width=52, bg=self._colors["text_bg"], fg=self._colors["text_fg"], insertbackground=self._colors["text_fg"])
        self.matchup_box.pack(fill=tk.X)

    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)

    def choose_output_folder(self) -> None:
        sel = filedialog.askdirectory(initialdir=self.output_dir)
        if sel:
            self.output_dir = sel
            self.log(f"Output folder set to {sel}")

    def load_demo(self) -> None:
        demo = """
Name: Demo Deck
Commander: Atraxa, Praetors' Voice
1 Sol Ring
1 Command Tower
1 Cultivate
1 Kodama's Reach
1 Demonic Tutor
1 Mana Vault
1 Birds of Paradise
1 Rhystic Study
1 Brainstorm
1 Swords to Plowshares
1 Wrath of God
1 Island
1 Forest
1 Mountain
"""
        self.input_text.delete(1.0, tk.END)
        self.input_text.insert(1.0, demo.strip())
        self.log("Loaded demo deck")

    def on_analyze(self) -> None:
        text = self.input_text.get(1.0, tk.END).strip()
        if not text:
            messagebox.showwarning("No deck", "Please paste a deck URL or plain decklist.")
            return
        t = threading.Thread(target=self._analyze_thread, args=(text,), daemon=True)
        t.start()

    def _analyze_thread(self, text: str) -> None:
        try:
            report = self.analyzer.analyze(text, self.status_q)
            deck_name = report.get("deck_name") or "deck"
            safe_name = re.sub(r"[^A-Za-z0-9_\- ]", "", deck_name) or "deck"
            ensure_output_dir(self.output_dir)
            out_path = os.path.join(self.output_dir, f"{safe_name}_labels.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            self.status_q.put(("log", f"Wrote report to {out_path}"))

            img = None
            commanders = report.get("commanders") or []
            if commanders:
                card_json = self.scryfall.fetch_card(commanders[0], status_q=self.status_q)
                img_url = get_card_image_url(card_json)
                if img_url:
                    try:
                        r = requests.get(img_url, timeout=REQUESTS_TIMEOUT)
                        if r.status_code == 200:
                            from io import BytesIO
                            img = Image.open(BytesIO(r.content))
                    except Exception:
                        img = None

            self.status_q.put(("result", {"report": report, "img": img, "output_file": out_path}))
        except Exception as e:
            self.status_q.put(("error", f"Error: {e}"))

    def _poll_status(self) -> None:
        try:
            while True:
                typ, val = self.status_q.get_nowait()
                if typ == "status":
                    self.status_label.config(text=val)
                elif typ == "log":
                    self.log(val)
                elif typ == "progress_init":
                    self.progress.config(maximum=val)
                    self.progress["value"] = 0
                elif typ == "progress":
                    self.progress["value"] = val
                elif typ == "result":
                    self._show_result(val)
                elif typ == "error":
                    self.status_label.config(text="Idle")
                    messagebox.showerror("Error", val)
                else:
                    pass
        except queue.Empty:
            pass
        self.root.after(200, self._poll_status)

    def _show_result(self, val: dict) -> None:
        report = val.get("report") or {}
        img = val.get("img")
        deck_name = report.get("deck_name") or ""
        commanders = report.get("commanders") or []
        derived = report.get("derived") or {}

        if img:
            img = img.copy()
            img.thumbnail((200, 280))
            self.tkimg = ImageTk.PhotoImage(img)
            self.cmd_img_label.config(image=self.tkimg)
        else:
            self.cmd_img_label.config(image="")

        self.deck_label.config(text=f"Deck: {deck_name}")
        self.commanders_label.config(text=f"Commander(s): {', '.join(commanders)}")

        self.canvas.delete("all")
        w = 380
        h = 18
        gap = 6
        y = 10
        for k, pct in derived.items():
            length = int((pct / 100.0) * w)
            self.canvas.create_rectangle(10, y, 10 + length, y + h, fill=self._colors["accent"])
            self.canvas.create_text(
                12 + length + 50,
                y + h / 2,
                anchor=tk.W,
                text=f"{k}: {pct}%",
                fill=self._colors["text_fg"],
            )
            y += h + gap

        self.matchup_box.delete(1.0, tk.END)
        mm = report.get("matchup") or {}
        self.matchup_box.insert(tk.END, f"Strong Against: {', '.join(mm.get('strong_against', []))}\n")
        self.matchup_box.insert(tk.END, f"Weak Against: {', '.join(mm.get('weak_against', []))}\n")
        self.matchup_box.insert(tk.END, f"Notes: {mm.get('because', '')}\n")
        self.status_label.config(text="Done")


def main() -> None:
    root = tk.Tk()
    App(root)
    readme = """
Commander Deck Labeler

Setup:
- Python 3.11+
- pip install requests Pillow

Usage:
- Paste a Moxfield or Archidekt deck URL, or paste a plain decklist into the textbox.
- Click 'Analyze Deck' to run labeling. Results saved to ./output by default.
- labels_config.json is created on first run if missing.
"""
    print(readme)
    root.mainloop()


if __name__ == "__main__":
    main()
