import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from js import document
from pyodide.ffi import create_proxy


# ---------- DOM helpers ----------

def qs(sel: str):
    return document.querySelector(sel)

def show(sel: str, visible: bool):
    el = qs(sel)
    if visible:
        el.classList.remove("hidden")
    else:
        el.classList.add("hidden")

def set_status(msg: str):
    qs("#status").innerText = msg

def normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s)
    return s


# Keep JS proxies alive (critical!)
PROXIES = []

# Debounce to prevent double-submit on touch
_last_action_ts = 0.0
def allow_action(min_ms: int = 350) -> bool:
    global _last_action_ts
    now = time.time() * 1000.0
    if now - _last_action_ts < min_ms:
        return False
    _last_action_ts = now
    return True


# ---------- Data models ----------

@dataclass
class Answer:
    name: str
    fact: Optional[float] = None
    aliases: List[str] = field(default_factory=list)

@dataclass
class Prompt:
    id: str
    category: str
    prompt: str
    fact_label: str = ""
    fact_unit: str = ""
    answers: List[Answer] = field(default_factory=list)

@dataclass
class GameState:
    teams: List[str] = field(default_factory=list)
    scores: Dict[str, int] = field(default_factory=dict)
    round_num: int = 0

    all_prompts: List[Prompt] = field(default_factory=list)   # everything from file
    prompts: List[Prompt] = field(default_factory=list)       # filtered by pack
    used_prompt_ids: set = field(default_factory=set)

    current_prompt: Optional[Prompt] = None
    current_team_idx: int = 0
    guesses: Dict[str, str] = field(default_factory=dict)

    # normalized accepted strings -> rank (1..10)
    current_lookup: Dict[str, int] = field(default_factory=dict)

    # pack selection
    selected_pack: str = "__all__"


STATE = GameState()


# ---------- Loading prompts ----------

def load_prompts() -> List[Prompt]:
    with open("prompts.json", "r", encoding="utf-8") as f:
        raw = json.load(f)

    prompts: List[Prompt] = []
    for item in raw:
        answers: List[Answer] = []
        for a in item["answers"]:
            answers.append(Answer(
                name=a["name"],
                fact=a.get("fact", None),
                aliases=a.get("aliases", []),
            ))

        prompts.append(Prompt(
            id=item["id"],
            category=item.get("category", "Uncategorized"),
            prompt=item["prompt"],
            fact_label=item.get("fact_label", ""),
            fact_unit=item.get("fact_unit", ""),
            answers=answers,
        ))
    return prompts


def get_available_packs(prompts: List[Prompt]) -> List[str]:
    packs = sorted({p.category for p in prompts})
    return packs


def populate_pack_dropdown():
    sel = qs("#pack-select")
    if not sel:
        return

    # clear existing (keep the first "All" option if present)
    sel.innerHTML = ""
    opt_all = document.createElement("option")
    opt_all.value = "__all__"
    opt_all.text = "All categories"
    sel.appendChild(opt_all)

    for pack in get_available_packs(STATE.all_prompts):
        opt = document.createElement("option")
        opt.value = pack
        opt.text = pack
        sel.appendChild(opt)

    # default selection
    sel.value = "__all__"
    STATE.selected_pack = "__all__"


def apply_pack_filter():
    pack = STATE.selected_pack
    if pack == "__all__":
        STATE.prompts = STATE.all_prompts[:]
    else:
        STATE.prompts = [p for p in STATE.all_prompts if p.category == pack]

    # reset used prompts so rotation works within the selected pack
    STATE.used_prompt_ids = set()


def pick_next_prompt() -> Prompt:
    available = [p for p in STATE.prompts if p.id not in STATE.used_prompt_ids]
    if not available:
        STATE.used_prompt_ids = set()
        available = STATE.prompts[:]
    p = random.choice(available)
    STATE.used_prompt_ids.add(p.id)
    return p


# ---------- Per-round lookup (aliases) ----------

def build_lookup(prompt: Prompt) -> Dict[str, int]:
    lookup: Dict[str, int] = {}
    for idx, ans in enumerate(prompt.answers):
        rank = idx + 1
        key = normalize(ans.name)
        if key:
            lookup[key] = rank
        for al in ans.aliases:
            k = normalize(al)
            if k:
                lookup[k] = rank
    return lookup


# ---------- UI ----------

def render_scoreboard():
    sb = qs("#scoreboard")
    sb.innerHTML = ""
    for team in sorted(STATE.teams, key=lambda t: STATE.scores.get(t, 0), reverse=True):
        score = STATE.scores.get(team, 0)
        row = document.createElement("div")
        row.className = "score-row"
        row.innerHTML = f"<div><strong>{team}</strong></div><div><strong>{score}</strong> pts</div>"
        sb.appendChild(row)

def set_round_ui():
    qs("#round-pill").innerText = f"Round {STATE.round_num}"
    qs("#prompt").innerText = STATE.current_prompt.prompt if STATE.current_prompt else "—"
    set_team_ui()

def set_team_ui():
    if not STATE.teams:
        qs("#team-pill").innerText = "Team: —"
        return
    team = STATE.teams[STATE.current_team_idx]
    qs("#team-pill").innerText = f"Team: {team}"
    qs("#guess-input").value = ""
    qs("#guess-input").focus()

def format_fact(prompt: Prompt, fact_value) -> str:
    if fact_value is None:
        return ""
    unit = (prompt.fact_unit or "").strip()

    if isinstance(fact_value, float) and fact_value.is_integer():
        fact_str = str(int(fact_value))
    else:
        fact_str = str(fact_value)

    return f" ({fact_str} {unit})" if unit else f" ({fact_str})"

def render_reveal(results: List[Tuple[str, str, int, Optional[int]]]):
    show("#result-area", True)
    ra = qs("#result-area")

    prompt = STATE.current_prompt
    answers = prompt.answers if prompt else []

    ranked = "<ol class='list'>"
    for i, ans in enumerate(answers):
        suffix = format_fact(prompt, ans.fact) if prompt else ""
        ranked += f"<li><strong>#{i+1}</strong> — {ans.name}{suffix}</li>"
    ranked += "</ol>"

    rows = ""
    for team, guess, points, rank in results:
        if rank is None:
            rows += (
                f"<div class='score-row'>"
                f"<div><strong>{team}</strong><div class='k'>Guessed: {guess or '—'}</div></div>"
                f"<div><strong>0</strong> pts</div></div>"
            )
        else:
            rows += (
                f"<div class='score-row'>"
                f"<div><strong>{team}</strong><div class='k'>Guessed: {guess}</div></div>"
                f"<div><strong>{points}</strong> pts<br><span class='k'>(rank #{rank})</span></div></div>"
            )

    ra.innerHTML = f"""
      <h2 class="result-title">Reveal</h2>
      <p class="k">Official Top 10 list:</p>
      {ranked}
      <div class="divider"></div>
      <h3>Round results</h3>
      <div class="scoreboard">{rows}</div>
      <button id="next-round-btn" class="btn">Next Round</button>
    """

    def _next(evt=None):
        if not allow_action():
            return
        next_round()

    proxy = create_proxy(_next)
    PROXIES.append(proxy)
    qs("#next-round-btn").addEventListener("pointerup", proxy)


# ---------- Scoring ----------

def score_guess(guess: str) -> Tuple[int, Optional[int]]:
    g = normalize(guess)
    if not g:
        return 0, None
    rank = STATE.current_lookup.get(g)
    if rank is None:
        return 0, None
    return rank, rank  # points = rank


# ---------- Game Flow ----------

def reset_game():
    STATE.teams = []
    STATE.scores = {}
    STATE.round_num = 0
    STATE.used_prompt_ids = set()
    STATE.current_prompt = None
    STATE.current_team_idx = 0
    STATE.guesses = {}
    STATE.current_lookup = {}

    show("#setup-area", True)
    show("#game-area", False)
    show("#result-area", False)
    render_scoreboard()

    qs("#team-names").focus()
    set_status("Ready. Choose a category pack, enter team names, and press Start Game.")

def start_game():
    raw = qs("#team-names").value
    teams = [t.strip() for t in raw.split(",") if t.strip()]
    if len(teams) < 2:
        set_status("Need at least 2 teams. Example: Team A, Team B")
        qs("#team-names").focus()
        return

    # Apply pack filter at start (based on dropdown)
    apply_pack_filter()
    if len(STATE.prompts) == 0:
        set_status("No prompts in this pack. Pick another pack.")
        return

    STATE.teams = teams
    STATE.scores = {t: 0 for t in teams}
    STATE.round_num = 0
    STATE.current_team_idx = 0
    STATE.guesses = {}

    show("#result-area", False)
    show("#setup-area", False)
    show("#game-area", True)

    next_round()

def next_round():
    STATE.round_num += 1
    STATE.current_prompt = pick_next_prompt()
    STATE.current_lookup = build_lookup(STATE.current_prompt)

    STATE.current_team_idx = 0
    STATE.guesses = {}

    show("#result-area", False)
    show("#game-area", True)

    set_round_ui()
    render_scoreboard()
    set_status(f"Round {STATE.round_num} started. {STATE.teams[0]} to guess.")

def submit_guess():
    if not STATE.current_prompt or not STATE.teams:
        return
    if not allow_action():
        return

    team = STATE.teams[STATE.current_team_idx]
    guess = qs("#guess-input").value.strip()
    STATE.guesses[team] = guess

    if STATE.current_team_idx < len(STATE.teams) - 1:
        STATE.current_team_idx += 1
        set_team_ui()
        set_status(f"{STATE.teams[STATE.current_team_idx]} to guess.")
    else:
        results: List[Tuple[str, str, int, Optional[int]]] = []
        for t in STATE.teams:
            g = STATE.guesses.get(t, "")
            pts, rank = score_guess(g)
            STATE.scores[t] = STATE.scores.get(t, 0) + pts
            results.append((t, g, pts, rank))

        render_scoreboard()
        render_reveal(results)
        show("#game-area", False)
        set_status("Reveal shown. Press Next Round to continue.")


# ---------- Entry point (called from JS) ----------

def init():
    try:
        STATE.all_prompts = load_prompts()
        STATE.prompts = STATE.all_prompts[:]

        populate_pack_dropdown()

        # Dropdown change handler
        def _pack_change(evt=None):
            sel = qs("#pack-select")
            STATE.selected_pack = sel.value
            set_status(f"Selected pack: {sel.value}")
        p = create_proxy(_pack_change); PROXIES.append(p)
        qs("#pack-select").addEventListener("change", p)

        # Start Game
        def _start(evt=None):
            if not allow_action():
                return
            start_game()
        p = create_proxy(_start); PROXIES.append(p)
        qs("#start-btn").addEventListener("pointerup", p)

        # New Game
        def _new(evt=None):
            if not allow_action():
                return
            reset_game()
        p = create_proxy(_new); PROXIES.append(p)
        qs("#new-game-btn").addEventListener("pointerup", p)

        # Submit Guess
        def _submit(evt=None):
            submit_guess()
        p = create_proxy(_submit); PROXIES.append(p)
        qs("#submit-btn").addEventListener("pointerup", p)

        # Enter submits guess
        def _keydown_guess(evt):
            if evt.key == "Enter":
                submit_guess()
        p = create_proxy(_keydown_guess); PROXIES.append(p)
        qs("#guess-input").addEventListener("keydown", p)

        # Enter starts game from team names
        def _keydown_teams(evt):
            if evt.key == "Enter":
                if allow_action():
                    start_game()
        p = create_proxy(_keydown_teams); PROXIES.append(p)
        qs("#team-names").addEventListener("keydown", p)

        reset_game()
        set_status("Ready ✅ Choose a pack, enter team names, and press Start Game.")
    except Exception as e:
        set_status(f"INIT ERROR:\n{e}")
        raise
