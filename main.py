import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from js import document, window
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


# ---------- Sound helpers ----------

def play_sound(name: str):
    """Play a synthesized SFX via the JS GameAudio engine."""
    try:
        window.GameAudio.play(name)
    except Exception:
        pass

def play_reveal_result(best_pts: int):
    try:
        window.GameAudio.playRevealResult(best_pts)
    except Exception:
        pass

def start_music():
    try:
        window.GameAudio.startMusic()
    except Exception:
        pass

def stop_music():
    try:
        window.GameAudio.stopMusic()
    except Exception:
        pass


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
    return sorted({p.category for p in prompts})


def populate_pack_dropdown():
    sel = qs("#pack-select")
    if not sel:
        return

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

    sel.value = "__all__"
    STATE.selected_pack = "__all__"


def apply_pack_filter():
    pack = STATE.selected_pack
    if pack == "__all__":
        STATE.prompts = STATE.all_prompts[:]
    else:
        STATE.prompts = [p for p in STATE.all_prompts if p.category == pack]
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
        k = normalize(ans.name)
        if k:
            lookup[k] = rank
        for al in ans.aliases:
            ak = normalize(al)
            if ak:
                lookup[ak] = rank
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

def render_reveal(results: List[Tuple[str, str, int, Optional[int]]]):
    """
    results: (team, guess, points_awarded, rank or None)
    Shows:
      - each team's guess + points
      - the full Top 10 list (from STATE.current_prompt.answers)
      - a Next Prompt button that starts the next round
    """
    ra = qs("#result-area")
    ra.innerHTML = ""

    prompt = STATE.current_prompt
    if not prompt:
        ra.innerHTML = "<h2>Reveal</h2><p>No prompt loaded.</p>"
        show("#result-area", True)
        return

    # Header
    title = document.createElement("h2")
    title.innerText = "Reveal"
    ra.appendChild(title)

    q = document.createElement("p")
    q.innerHTML = f"<strong>Prompt:</strong> {prompt.prompt}"
    ra.appendChild(q)

    # Team results table
    table = document.createElement("div")
    table.innerHTML = "<h3>Team guesses</h3>"
    ra.appendChild(table)

    rows = document.createElement("div")
    rows.className = "scoreboard"  # reuse styling
    table.appendChild(rows)

    for (team, guess, pts, rank) in results:
        rank_text = f"#{rank}" if rank is not None else "Not in Top 10"
        row = document.createElement("div")
        row.className = "score-row"
        row.innerHTML = (
            f"<div><strong>{team}</strong><br>"
            f"<span style='opacity:0.9'>{guess or '—'}</span></div>"
            f"<div><strong>+{pts}</strong> pts<br>"
            f"<span style='opacity:0.9'>{rank_text}</span></div>"
        )
        rows.appendChild(row)

    # Top 10 list
    top = document.createElement("div")
    top.innerHTML = "<div class='divider'></div><h3>Actual Top 10</h3>"
    ra.appendChild(top)

    ol = document.createElement("ol")
    for ans in prompt.answers:
        li = document.createElement("li")
        if ans.fact is not None and (prompt.fact_label or prompt.fact_unit):
            # e.g. "Population: 21.5M"
            label = prompt.fact_label or "Fact"
            unit = prompt.fact_unit or ""
            li.innerText = f"{ans.name} — {label}: {ans.fact}{unit}"
        else:
            li.innerText = ans.name
        ol.appendChild(li)
    top.appendChild(ol)

    # Next prompt button
    btn = document.createElement("button")
    btn.id = "next-round-btn"
    btn.className = "btn"
    btn.innerText = "Next prompt"
    ra.appendChild(btn)

    def _next(evt=None):
        if not allow_action():
            return
        show("#result-area", False)
        next_round()

    p = create_proxy(_next)
    PROXIES.append(p)
    btn.addEventListener("pointerup", p)

    # Show reveal screen, hide guessing screen
    show("#game-area", False)
    show("#result-area", True)
    render_scoreboard()

def render_reveal(results: List[Tuple[str, str, int, Optional[int]]]):
    """
    results items are: (team, guess, points_awarded, rank or None)
    rank is 1..10 if in Top 10 else None
    """

    ra = qs("#result-area")
    ra.innerHTML = ""

    prompt = STATE.current_prompt
    if not prompt:
        ra.innerHTML = "<h2>Reveal</h2><p>No prompt loaded.</p>"
        show("#game-area", False)
        show("#result-area", True)
        return

    # ---------- Header ----------
    header = document.createElement("div")
    header.className = "reveal-header"

    h2 = document.createElement("h2")
    h2.innerText = "Reveal"
    header.appendChild(h2)

    q = document.createElement("div")
    q.className = "reveal-question"
    q.innerHTML = f"<div class='reveal-label'>Prompt</div><div class='reveal-prompt'>{prompt.prompt}</div>"
    header.appendChild(q)

    callout = document.createElement("div")
    callout.className = "reveal-callout"
    callout.innerHTML = "Remember: <strong>#10 = 10 points</strong> (higher rank number = more points)"
    header.appendChild(callout)

    ra.appendChild(header)

    # ---------- Grid wrapper ----------
    grid = document.createElement("div")
    grid.className = "reveal-grid"
    ra.appendChild(grid)

    # ---------- Left: team results ----------
    left = document.createElement("div")
    left.className = "reveal-panel"
    grid.appendChild(left)

    lh = document.createElement("h3")
    lh.innerText = "Team results"
    left.appendChild(lh)

    list_el = document.createElement("div")
    list_el.className = "reveal-results"
    left.appendChild(list_el)

    # Helper to classify performance for styling
    def cls_for(points: int, rank: Optional[int]) -> str:
        if rank == 10 or points == 10:
            return "hit-perfect"
        if rank is not None and points >= 7:
            return "hit-great"
        if rank is not None and points >= 1:
            return "hit-good"
        return "hit-miss"

    for (team, guess, pts, rank) in results:
        row = document.createElement("div")
        row.className = f"reveal-row {cls_for(pts, rank)}"

        team_el = document.createElement("div")
        team_el.className = "reveal-team"
        team_el.innerHTML = f"<div class='team-name'>{team}</div><div class='team-guess'>{(guess or '—')}</div>"

        meta_el = document.createElement("div")
        meta_el.className = "reveal-meta"

        rank_text = f"#{rank}" if rank is not None else "Not in Top 10"
        meta_el.innerHTML = (
            f"<div class='badge badge-rank'>{rank_text}</div>"
            f"<div class='badge badge-points'>+{pts} pts</div>"
        )

        row.appendChild(team_el)
        row.appendChild(meta_el)
        list_el.appendChild(row)

    # ---------- Right: actual top 10 ----------
    right = document.createElement("div")
    right.className = "reveal-panel"
    grid.appendChild(right)

    rh = document.createElement("h3")
    rh.innerText = "Actual Top 10"
    right.appendChild(rh)

    topwrap = document.createElement("div")
    topwrap.className = "top10"
    right.appendChild(topwrap)

    # We want to visually emphasize #10 as the "max points" target
    # prompt.answers is expected in rank order 1..10
    for i, ans in enumerate(prompt.answers, start=1):
        item = document.createElement("div")
        item.className = "top10-item"
        if i == 10:
            item.classList.add("top10-ten")

        # Optional fact display
        fact_html = ""
        if ans.fact is not None and (prompt.fact_label or prompt.fact_unit):
            label = prompt.fact_label or "Fact"
            unit = prompt.fact_unit or ""
            fact_html = f"<div class='top10-fact'>{label}: {ans.fact}{unit}</div>"

        item.innerHTML = (
            f"<div class='top10-rank'>#{i}</div>"
            f"<div class='top10-name'>{ans.name}</div>"
            f"{fact_html}"
        )
        topwrap.appendChild(item)

    # ---------- Footer buttons ----------
    footer = document.createElement("div")
    footer.className = "reveal-footer"
    ra.appendChild(footer)

    btn = document.createElement("button")
    btn.id = "next-round-btn"
    btn.className = "btn"
    btn.innerText = "Next prompt"
    footer.appendChild(btn)

    def _next(evt=None):
        if not allow_action():
            return
        show("#result-area", False)
        next_round()

    p = create_proxy(_next)
    PROXIES.append(p)
    btn.addEventListener("pointerup", p)

    # Show reveal, hide game input
    show("#game-area", False)
    show("#result-area", True)

    # Keep scoreboard visible in the game area next time
    render_scoreboard()

# ---------- Scoring ----------

def score_guess(guess: str) -> Tuple[int, Optional[int]]:
    g = normalize(guess)
    if not g:
        return 0, None
    rank = STATE.current_lookup.get(g)
    if rank is None:
        return 0, None
    return rank, rank  # points = rank


# ---------- Team management ----------

ADDED_TEAMS: List[str] = []
MAX_TEAMS = 8

def add_team():
    inp = qs("#team-name-input")
    name = (inp.value or "").strip()
    if not name:
        return
    if len(ADDED_TEAMS) >= MAX_TEAMS:
        set_status(f"Maximum {MAX_TEAMS} teams allowed.")
        return
    if name in ADDED_TEAMS:
        set_status(f'"{name}" is already added.')
        return
    ADDED_TEAMS.append(name)
    play_sound("teamAdd")
    inp.value = ""
    inp.focus()
    render_team_list()
    update_start_visibility()
    set_status(f'Added "{name}". {len(ADDED_TEAMS)} team(s) so far.')

def remove_team(name: str):
    if name in ADDED_TEAMS:
        ADDED_TEAMS.remove(name)
    play_sound("teamRemove")
    render_team_list()
    update_start_visibility()
    set_status(f'Removed "{name}". {len(ADDED_TEAMS)} team(s) remaining.')

def render_team_list():
    container = qs("#team-list")
    container.innerHTML = ""
    for t in ADDED_TEAMS:
        chip = document.createElement("div")
        chip.className = "team-chip"

        span = document.createElement("span")
        span.innerText = t
        chip.appendChild(span)

        btn = document.createElement("button")
        btn.className = "team-chip-remove"
        btn.innerHTML = "&#10005;"
        btn.title = f"Remove {t}"

        def _remove(evt=None, team=t):
            remove_team(team)
        p = create_proxy(_remove)
        PROXIES.append(p)
        btn.addEventListener("pointerup", p)

        chip.appendChild(btn)
        container.appendChild(chip)

def update_start_visibility():
    btn = qs("#start-btn")
    if len(ADDED_TEAMS) >= 2:
        btn.classList.remove("hidden")
    else:
        btn.classList.add("hidden")
    # Disable input once max reached
    inp = qs("#team-name-input")
    add_btn = qs("#add-team-btn")
    if len(ADDED_TEAMS) >= MAX_TEAMS:
        inp.disabled = True
        add_btn.disabled = True
    else:
        inp.disabled = False
        add_btn.disabled = False


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

    stop_music()
    ADDED_TEAMS.clear()

    show("#setup-area", True)
    show("#game-area", False)
    show("#result-area", False)
    render_scoreboard()
    render_team_list()
    update_start_visibility()

    qs("#team-name-input").value = ""
    qs("#team-name-input").focus()
    set_status("Ready. Choose a pack, add teams, and press Start Game.")

def start_game():
    if len(ADDED_TEAMS) < 2:
        set_status("Need at least 2 teams. Add them above.")
        qs("#team-name-input").focus()
        return
    teams = list(ADDED_TEAMS)

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

    play_sound("gameStart")
    start_music()

    next_round()

def next_round():
    STATE.round_num += 1
    STATE.current_prompt = pick_next_prompt()
    STATE.current_lookup = build_lookup(STATE.current_prompt)

    STATE.current_team_idx = 0
    STATE.guesses = {}

    show("#result-area", False)
    show("#game-area", True)

    play_sound("nextRound")
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
        play_sound("submitGuess")
        STATE.current_team_idx += 1
        set_team_ui()
        set_status(f"{STATE.teams[STATE.current_team_idx]} to guess.")
    else:
        # Score then reveal
        results: List[Tuple[str, str, int, Optional[int]]] = []
        for t in STATE.teams:
            g = STATE.guesses.get(t, "")
            pts, rank = score_guess(g)
            STATE.scores[t] = STATE.scores.get(t, 0) + pts
            results.append((t, g, pts, rank))

        # Play reveal + hit sound (delayed in JS)
        best_pts = max((pts for _, _, pts, _ in results), default=0)
        play_reveal_result(best_pts)

        # Minimal reveal-less flow isn't requested here; keep existing reveal section hidden if you want.
        # For now, just show a simple message and move on is NOT desired; we keep current behavior (your reveal UI).
        # If you still want reveal, keep your render_reveal function from your current file.
        #
        # NOTE: If your current version has render_reveal, keep it. If not, this will just continue to next round.
        #
        try:
            from typing import cast
            render_reveal = cast(object, globals().get("render_reveal"))
            if callable(render_reveal):
                render_reveal(results)
                show("#game-area", False)
                set_status("Reveal shown. Press Next Round to continue.")
                render_scoreboard()
                return
        except Exception:
            pass

        # Fallback: no reveal available
        render_scoreboard()
        next_round()

def skip_question():
    """
    Option 1: Skip immediately, no reveal, no points, go to next round.
    Mark current prompt as used (already is when selected).
    """
    if not STATE.current_prompt or not STATE.teams:
        return
    if not allow_action():
        return

    skipped_id = STATE.current_prompt.id
    # Ensure it's considered used (it should be already, but safe)
    STATE.used_prompt_ids.add(skipped_id)

    # Clear any guesses and go to next round
    STATE.guesses = {}
    STATE.current_team_idx = 0

    set_status("Skipped. Loading next question…")
    play_sound("skip")
    next_round()


# ---------- Entry point ----------

def init():
    try:
        STATE.all_prompts = load_prompts()
        STATE.prompts = STATE.all_prompts[:]

        populate_pack_dropdown()

        # Dropdown change
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

        # Skip
        def _skip(evt=None):
            skip_question()
        p = create_proxy(_skip); PROXIES.append(p)
        qs("#skip-btn").addEventListener("pointerup", p)

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

        # Add team button
        def _add_team(evt=None):
            if not allow_action():
                return
            add_team()
        p = create_proxy(_add_team); PROXIES.append(p)
        qs("#add-team-btn").addEventListener("pointerup", p)

        # Enter on team name input adds a team
        def _keydown_team_input(evt):
            if evt.key == "Enter":
                if allow_action():
                    add_team()
        p = create_proxy(_keydown_team_input); PROXIES.append(p)
        qs("#team-name-input").addEventListener("keydown", p)

        reset_game()
        set_status("Ready ✅ Choose a pack, add teams, and press Start Game.")
    except Exception as e:
        set_status(f"INIT ERROR:\n{e}")
        raise