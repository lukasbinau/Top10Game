import json
import random
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from js import document, window
from pyodide.ffi import create_proxy


# ────────── DOM helpers ──────────

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


# ────────── Constants ──────────

MAX_ROUNDS = 10
MAX_TEAMS = 8

# Keep JS proxies alive (prevent GC)
PROXIES: list = []

# Debounce
_last_action_ts = 0.0


def allow_action(min_ms: int = 350) -> bool:
    global _last_action_ts
    now = time.time() * 1000.0
    if now - _last_action_ts < min_ms:
        return False
    _last_action_ts = now
    return True


# Team colour palette (one per team slot)
TEAM_COLORS = [
    "#5eead4",  # teal
    "#fb7185",  # pink
    "#a78bfa",  # purple
    "#fbbf24",  # amber
    "#34d399",  # green
    "#60a5fa",  # blue
    "#f97316",  # orange
    "#e879f9",  # fuchsia
]

# Emoji map for category cards
CATEGORY_EMOJIS: Dict[str, str] = {
    "Sports": "⚽",
    "Pop Culture": "🎬",
    "Science (Hard)": "🔬",
    "Science (Medium)": "🧪",
    "Science (Easy)": "🔭",
    "Geography 1": "🌍",
    "Geography 2": "🗺️",
    "Food & Drink": "🍕",
    "History": "📜",
    "Technology": "💻",
}


# ────────── Sound helpers ──────────

def play_sound(name: str):
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


# ────────── Data models ──────────

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
    completed_rounds: int = 0

    all_prompts: List[Prompt] = field(default_factory=list)
    prompts: List[Prompt] = field(default_factory=list)
    used_prompt_ids: set = field(default_factory=set)

    current_prompt: Optional[Prompt] = None
    current_team_idx: int = 0
    guesses: Dict[str, str] = field(default_factory=dict)
    current_lookup: Dict[str, int] = field(default_factory=dict)

    selected_pack: str = "__all__"


STATE = GameState()


# ────────── Loading prompts ──────────

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
        # Fallback: pull from ALL categories
        available = [p for p in STATE.all_prompts if p.id not in STATE.used_prompt_ids]
    if not available:
        # Ultimate fallback: reset
        STATE.used_prompt_ids = set()
        available = STATE.all_prompts[:]
    p = random.choice(available)
    STATE.used_prompt_ids.add(p.id)
    return p


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


# ────────── Phase management ──────────

PHASES = ["#setup-area", "#handoff-area", "#game-area", "#result-area"]


def show_phase(phase_id: str):
    """Show exactly one phase, hide the rest."""
    for pid in PHASES:
        show(pid, pid == phase_id)


# ────────── Category cards ──────────

def populate_category_cards():
    grid = qs("#category-grid")
    grid.innerHTML = ""

    # "All categories" – full width
    all_card = _make_cat_card("__all__", "🎲", "All Categories")
    all_card.classList.add("cat-card-wide")
    all_card.classList.add("selected")
    grid.appendChild(all_card)

    for pack in get_available_packs(STATE.all_prompts):
        emoji = CATEGORY_EMOJIS.get(pack, "❓")
        card = _make_cat_card(pack, emoji, pack)
        grid.appendChild(card)


def _make_cat_card(value: str, emoji: str, label: str):
    card = document.createElement("button")
    card.className = "cat-card"
    card.dataset.value = value
    card.innerHTML = f"<span class='cat-emoji'>{emoji}</span>{label}"

    def _click(evt=None):
        select_category(value)
    p = create_proxy(_click)
    PROXIES.append(p)
    card.addEventListener("pointerup", p)
    return card


def select_category(value: str):
    STATE.selected_pack = value
    cards = document.querySelectorAll(".cat-card")
    for i in range(cards.length):
        c = cards.item(i)
        if c.dataset.value == value:
            c.classList.add("selected")
        else:
            c.classList.remove("selected")
    play_sound("click")


# ────────── Progress dots ──────────

def render_dots(container_sel: str):
    el = qs(container_sel)
    el.innerHTML = ""
    for i in range(MAX_ROUNDS):
        d = document.createElement("div")
        d.className = "dot"
        if i < STATE.completed_rounds:
            d.classList.add("filled")
        elif i == STATE.completed_rounds:
            d.classList.add("current")
        el.appendChild(d)


# ────────── Hand-off screen ──────────

def show_handoff():
    team = STATE.teams[STATE.current_team_idx]
    color = TEAM_COLORS[STATE.current_team_idx % len(TEAM_COLORS)]

    qs("#handoff-team").innerText = team
    qs("#handoff-team").style.color = color
    qs("#handoff-round").innerText = f"Round {STATE.completed_rounds + 1} of {MAX_ROUNDS}"

    render_dots("#handoff-dots")
    show_phase("#handoff-area")


# ────────── Turn / guessing screen ──────────

def show_turn():
    team = STATE.teams[STATE.current_team_idx]
    color = TEAM_COLORS[STATE.current_team_idx % len(TEAM_COLORS)]

    qs("#turn-team").innerText = f"{team}'s Turn"
    qs("#turn-team").style.color = color
    qs("#turn-round").innerText = f"Round {STATE.completed_rounds + 1} of {MAX_ROUNDS}"

    render_dots("#turn-dots")

    qs("#prompt").innerText = STATE.current_prompt.prompt if STATE.current_prompt else "—"
    qs("#guess-input").value = ""

    # Close menu if open
    qs("#game-menu").classList.add("hidden")

    show_phase("#game-area")
    qs("#guess-input").focus()


# ────────── Scoreboard (overlay sheet) ──────────

def render_scoreboard():
    sb = qs("#scoreboard")
    sb.innerHTML = ""
    for team in sorted(STATE.teams, key=lambda t: STATE.scores.get(t, 0), reverse=True):
        score = STATE.scores.get(team, 0)
        row = document.createElement("div")
        row.className = "score-row"
        row.innerHTML = (
            f"<div><strong>{team}</strong></div>"
            f"<div><strong>{score}</strong> pts</div>"
        )
        sb.appendChild(row)


def show_scoreboard():
    render_scoreboard()
    qs("#sb-overlay").classList.remove("hidden")
    qs("#game-menu").classList.add("hidden")


# ────────── Reveal ──────────

def render_reveal(results: List[Tuple[str, str, int, Optional[int]]]):
    """
    results items: (team, guess, points_awarded, rank_or_None)
    """
    ra = qs("#result-area")
    ra.innerHTML = ""

    prompt = STATE.current_prompt
    if not prompt:
        ra.innerHTML = "<h2>Reveal</h2><p>No prompt loaded.</p>"
        show_phase("#result-area")
        return

    # ── Header ──
    header = document.createElement("div")
    header.className = "reveal-header"

    h2 = document.createElement("h2")
    h2.innerText = "Reveal"
    header.appendChild(h2)

    q = document.createElement("div")
    q.className = "reveal-question"
    q.innerHTML = (
        f"<div class='reveal-label'>Prompt</div>"
        f"<div class='reveal-prompt'>{prompt.prompt}</div>"
    )
    header.appendChild(q)

    callout = document.createElement("div")
    callout.className = "reveal-callout"
    callout.innerHTML = "Remember: <strong>#10 = 10 points</strong> (higher rank = more points)"
    header.appendChild(callout)
    ra.appendChild(header)

    # ── Grid ──
    grid = document.createElement("div")
    grid.className = "reveal-grid"
    ra.appendChild(grid)

    # Left panel: team results
    left = document.createElement("div")
    left.className = "reveal-panel"
    grid.appendChild(left)

    lh = document.createElement("h3")
    lh.innerText = "Team Results"
    left.appendChild(lh)

    list_el = document.createElement("div")
    list_el.className = "reveal-results"
    left.appendChild(list_el)

    def cls_for(points: int, rank) -> str:
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
        team_el.innerHTML = (
            f"<div class='team-name'>{team}</div>"
            f"<div class='team-guess'>{guess or '—'}</div>"
        )

        meta_el = document.createElement("div")
        meta_el.className = "reveal-meta"
        rank_text = f"#{rank}" if rank is not None else "Miss"
        meta_el.innerHTML = (
            f"<div class='badge badge-rank'>{rank_text}</div>"
            f"<div class='badge badge-points'>+{pts} pts</div>"
        )

        row.appendChild(team_el)
        row.appendChild(meta_el)
        list_el.appendChild(row)

    # Right panel: actual top 10
    right = document.createElement("div")
    right.className = "reveal-panel"
    grid.appendChild(right)

    rh = document.createElement("h3")
    rh.innerText = "Actual Top 10"
    right.appendChild(rh)

    topwrap = document.createElement("div")
    topwrap.className = "top10"
    right.appendChild(topwrap)

    for i, ans in enumerate(prompt.answers, start=1):
        item = document.createElement("div")
        item.className = "top10-item"
        if i == 10:
            item.classList.add("top10-ten")

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

    # ── Footer ──
    is_final = STATE.completed_rounds >= MAX_ROUNDS

    if not is_final:
        footer = document.createElement("div")
        footer.className = "reveal-footer"
        ra.appendChild(footer)

        btn = document.createElement("button")
        btn.className = "btn"
        btn.innerText = "Next Round ➜"
        footer.appendChild(btn)

        def _next(evt=None):
            if not allow_action():
                return
            next_round()

        p = create_proxy(_next)
        PROXIES.append(p)
        btn.addEventListener("pointerup", p)

    show_phase("#result-area")
    render_scoreboard()

    # Auto-transition to winner after last round
    if is_final:
        def _show_winner():
            show_winner_overlay()
        p = create_proxy(_show_winner)
        PROXIES.append(p)
        window.setTimeout(p, 3500)


# ────────── Scoring ──────────

def score_guess(guess: str) -> Tuple[int, Optional[int]]:
    g = normalize(guess)
    if not g:
        return 0, None
    rank = STATE.current_lookup.get(g)
    if rank is None:
        return 0, None
    return rank, rank  # points = rank position


# ────────── Winner overlay ──────────

def show_winner_overlay():
    stop_music()
    play_sound("gameWin")

    if not STATE.teams:
        return

    max_score = max(STATE.scores.get(t, 0) for t in STATE.teams)
    winners = [t for t in STATE.teams if STATE.scores.get(t, 0) == max_score]

    qs("#winner-trophy").innerText = "🏆"
    qs("#winner-title").innerText = "It's a Tie!" if len(winners) > 1 else "Winner!"
    qs("#winner-name").innerText = " & ".join(winners)
    qs("#winner-score").innerText = f"{max_score} pts"

    sb = qs("#winner-scoreboard")
    sb.innerHTML = ""
    for team in sorted(STATE.teams, key=lambda t: STATE.scores.get(t, 0), reverse=True):
        score = STATE.scores.get(team, 0)
        row = document.createElement("div")
        row.className = "winner-sb-row"
        if team in winners:
            row.classList.add("winner-sb-highlight")
        row.innerHTML = f"<span>{team}</span><span>{score} pts</span>"
        sb.appendChild(row)

    overlay = qs("#winner-overlay")
    overlay.classList.remove("hidden")
    overlay.offsetHeight  # force reflow for animation
    overlay.classList.add("visible")


# ────────── Team management ──────────

ADDED_TEAMS: List[str] = []


def add_team():
    inp = qs("#team-name-input")
    name = (inp.value or "").strip()
    if not name:
        return
    if len(ADDED_TEAMS) >= MAX_TEAMS:
        set_status(f"Max {MAX_TEAMS} teams.")
        return
    if name in ADDED_TEAMS:
        set_status(f'"{name}" already added.')
        return

    ADDED_TEAMS.append(name)
    play_sound("teamAdd")
    inp.value = ""
    inp.focus()
    render_team_list()
    update_start_visibility()


def remove_team(name: str):
    if name in ADDED_TEAMS:
        ADDED_TEAMS.remove(name)
    play_sound("teamRemove")
    render_team_list()
    update_start_visibility()


def render_team_list():
    container = qs("#team-list")
    container.innerHTML = ""
    for idx, t in enumerate(ADDED_TEAMS):
        chip = document.createElement("div")
        chip.className = "team-chip"
        color = TEAM_COLORS[idx % len(TEAM_COLORS)]
        chip.style.borderColor = f"{color}55"
        chip.style.background = f"{color}14"

        span = document.createElement("span")
        span.innerText = t
        chip.appendChild(span)

        btn = document.createElement("button")
        btn.className = "chip-x"
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

    inp = qs("#team-name-input")
    add_btn = qs("#add-team-btn")
    if len(ADDED_TEAMS) >= MAX_TEAMS:
        inp.disabled = True
        add_btn.disabled = True
    else:
        inp.disabled = False
        add_btn.disabled = False


# ────────── Game flow ──────────

def reset_game():
    STATE.teams = []
    STATE.scores = {}
    STATE.round_num = 0
    STATE.completed_rounds = 0
    STATE.used_prompt_ids = set()
    STATE.current_prompt = None
    STATE.current_team_idx = 0
    STATE.guesses = {}
    STATE.current_lookup = {}

    stop_music()
    ADDED_TEAMS.clear()

    # Hide overlays
    overlay = qs("#winner-overlay")
    overlay.classList.remove("visible")
    overlay.classList.add("hidden")
    qs("#sb-overlay").classList.add("hidden")

    show_phase("#setup-area")
    render_team_list()
    update_start_visibility()

    qs("#team-name-input").value = ""
    qs("#team-name-input").focus()


def start_game():
    if len(ADDED_TEAMS) < 2:
        set_status("Need at least 2 teams.")
        return

    apply_pack_filter()
    if not STATE.prompts:
        set_status("No prompts in this category.")
        return

    STATE.teams = list(ADDED_TEAMS)
    STATE.scores = {t: 0 for t in STATE.teams}
    STATE.round_num = 0
    STATE.completed_rounds = 0
    STATE.current_team_idx = 0
    STATE.guesses = {}

    play_sound("gameStart")
    start_music()
    next_round()


def next_round():
    STATE.round_num += 1
    STATE.current_prompt = pick_next_prompt()
    STATE.current_lookup = build_lookup(STATE.current_prompt)
    STATE.current_team_idx = 0
    STATE.guesses = {}

    play_sound("nextRound")
    show_handoff()


def submit_guess():
    if not STATE.current_prompt or not STATE.teams:
        return
    if not allow_action():
        return

    team = STATE.teams[STATE.current_team_idx]
    guess = qs("#guess-input").value.strip()
    STATE.guesses[team] = guess

    if STATE.current_team_idx < len(STATE.teams) - 1:
        # More teams to go → hand off
        play_sound("submitGuess")
        STATE.current_team_idx += 1
        show_handoff()
    else:
        # All teams done → score & reveal
        STATE.completed_rounds += 1

        results: List[Tuple[str, str, int, Optional[int]]] = []
        for t in STATE.teams:
            g = STATE.guesses.get(t, "")
            pts, rank = score_guess(g)
            STATE.scores[t] = STATE.scores.get(t, 0) + pts
            results.append((t, g, pts, rank))

        best_pts = max((pts for _, _, pts, _ in results), default=0)
        play_reveal_result(best_pts)
        render_reveal(results)


def skip_question():
    """Skip without counting toward 10 rounds."""
    if not STATE.current_prompt or not STATE.teams:
        return
    if not allow_action():
        return

    STATE.used_prompt_ids.add(STATE.current_prompt.id)
    STATE.current_prompt = pick_next_prompt()
    STATE.current_lookup = build_lookup(STATE.current_prompt)
    STATE.current_team_idx = 0
    STATE.guesses = {}

    play_sound("skip")
    show_handoff()


# ────────── Init ──────────

def init():
    try:
        STATE.all_prompts = load_prompts()
        STATE.prompts = STATE.all_prompts[:]

        populate_category_cards()

        # Start Game
        def _start(evt=None):
            if not allow_action():
                return
            start_game()
        p = create_proxy(_start); PROXIES.append(p)
        qs("#start-btn").addEventListener("pointerup", p)

        # Hand-off → Ready
        def _ready(evt=None):
            if not allow_action():
                return
            play_sound("click")
            show_turn()
        p = create_proxy(_ready); PROXIES.append(p)
        qs("#handoff-ready-btn").addEventListener("pointerup", p)

        # Submit Guess
        def _submit(evt=None):
            submit_guess()
        p = create_proxy(_submit); PROXIES.append(p)
        qs("#submit-btn").addEventListener("pointerup", p)

        # Enter key submits guess
        def _keydown_guess(evt):
            if evt.key == "Enter":
                submit_guess()
        p = create_proxy(_keydown_guess); PROXIES.append(p)
        qs("#guess-input").addEventListener("keydown", p)

        # Menu: Skip
        def _skip(evt=None):
            skip_question()
        p = create_proxy(_skip); PROXIES.append(p)
        qs("#skip-btn").addEventListener("pointerup", p)

        # Menu: View Scores
        def _scores(evt=None):
            show_scoreboard()
        p = create_proxy(_scores); PROXIES.append(p)
        qs("#scores-btn").addEventListener("pointerup", p)

        # Menu: New Game
        def _new(evt=None):
            if not allow_action():
                return
            reset_game()
        p = create_proxy(_new); PROXIES.append(p)
        qs("#new-game-btn").addEventListener("pointerup", p)

        # Winner overlay: New Game
        def _winner_new(evt=None):
            if not allow_action():
                return
            reset_game()
        p = create_proxy(_winner_new); PROXIES.append(p)
        qs("#winner-new-game-btn").addEventListener("pointerup", p)

        # Add Team button
        def _add_team(evt=None):
            if not allow_action():
                return
            add_team()
        p = create_proxy(_add_team); PROXIES.append(p)
        qs("#add-team-btn").addEventListener("pointerup", p)

        # Enter key adds team
        def _keydown_team(evt):
            if evt.key == "Enter":
                if allow_action():
                    add_team()
        p = create_proxy(_keydown_team); PROXIES.append(p)
        qs("#team-name-input").addEventListener("keydown", p)

        reset_game()
        set_status("Ready ✅")

    except Exception as e:
        set_status(f"INIT ERROR: {e}")
        raise
