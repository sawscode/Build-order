import io
import zipfile
import requests
from flask import Flask, request, jsonify, render_template

from mgz.model import parse_match
from mgz.fast.enums import Age as AgeEnum

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

AOE2NET_BASE = "https://aoe2.net/api"

# Base training times in ms at 1.0x speed.
# Queue-depth filter uses these to decide when a slot opens up.
TRAIN_MS = {
    83: 25000,   # Villager
    74: 21000,   # Militia
    75: 21000,   # Man-at-Arms
    77: 21000,   # Long Swordsman
    567: 21000,  # Champion
    93: 22000,   # Spearman
    358: 22000,  # Pikeman
    359: 22000,  # Halberdier
    448: 30000,  # Scout Cavalry
    37: 30000,   # Light Cavalry
    441: 30000,  # Hussar
    38: 30000,   # Knight
    283: 30000,  # Cavalier
    569: 30000,  # Paladin
    4:  35000,   # Archer
    24: 27000,   # Crossbowman
    492: 35000,  # Arbalester
    7:  22000,   # Skirmisher
    6:  22000,   # Elite Skirmisher
    8:  35000,   # Longbowman
    751: 35000,  # Eagle Scout
    753: 35000,  # Eagle Warrior
    125: 51000,  # Monk
    13: 40000,   # Fishing Ship
    17: 36000,   # Trade Cog
    128: 50000,  # Trade Cart
    35: 36000,   # Battering Ram
    422: 36000,  # Capped Ram
    280: 46000,  # Mangonel
    279: 30000,  # Scorpion
    40: 75000,   # Cataphract
    329: 22000,  # Camel Rider
}
QUEUE_MAX = 10


def ms_to_mmss(ms):
    if ms is None:
        return None
    s = int(ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def parse_replay(record_bytes):
    """
    Parse a replay using mgz.model for accurate data:
    - Names resolved from the official dataset (no hardcoded lookup tables)
    - Age-up COMPLETION times from game-engine CHAT events (outcomes, not inputs)
    - Game-speed-aware queue depth filtering to suppress spam-clicks
    """
    handle = io.BytesIO(record_bytes)
    match = parse_match(handle)

    speed_mult = (match.speed_id or 100) / 100.0

    # Age-up completion times come from CHAT operations the game engine writes
    # when a research finishes — these are outcomes, not player button presses.
    # AgeEnum: DARK_AGE=1, FEUDAL_AGE=2, CASTLE_AGE=3, IMPERIAL_AGE=4
    age_completions = {}   # pid -> {age_value -> ms}
    for uptime in match.uptimes:
        if not uptime.player:
            continue
        pid = uptime.player.number
        if pid not in age_completions:
            age_completions[pid] = {}
        ms = int(uptime.timestamp.total_seconds() * 1000)
        av = uptime.age.value if hasattr(uptime.age, 'value') else int(uptime.age)
        age_completions[pid][av] = ms

    # Per-building production queue state.
    # Key: building instance_id (from object_ids in the action).
    # Value: sorted list of unit finish times (ms).
    bqueues = {}   # pid -> bkey -> [finish_ms, ...]

    def try_queue(pid, bkey, unit_id, amount, now_ms):
        if pid not in bqueues:
            bqueues[pid] = {}
        if bkey not in bqueues[pid]:
            bqueues[pid][bkey] = []
        q = bqueues[pid][bkey]
        base = TRAIN_MS.get(unit_id, 30000)
        train_ms = int(base / speed_mult)
        # Evict finished slots
        while q and q[0] <= now_ms:
            q.pop(0)
        slots = QUEUE_MAX - len(q)
        if slots <= 0:
            return 0   # queue full — spam click, discard
        actual = min(int(amount) if amount else 1, slots)
        for _ in range(actual):
            prev = q[-1] if q else now_ms
            q.append(prev + train_ms)
        return actual

    feudal_click = {}   # pid -> ms (when player initiated the research)
    castle_click = {}   # pid -> ms
    build_orders = {p.number: [] for p in match.players}

    for inp in match.inputs:
        if not inp.player:
            continue
        pid = inp.player.number
        if pid not in build_orders:
            continue

        time_ms = int(inp.timestamp.total_seconds() * 1000)
        before_castle = pid not in castle_click

        # ── Unit training ──────────────────────────────────────────────────────
        if inp.type == 'Queue' and before_castle:
            unit_name = inp.param                          # resolved from dataset
            unit_id   = inp.payload.get('unit_id')
            amount    = inp.payload.get('amount') or 1
            # object_ids = building instance IDs; each building gets `amount` units
            object_ids = inp.payload.get('object_ids') or []
            bkeys = object_ids if object_ids else [f'p{pid}_0']

            total = 0
            for bkey in bkeys:
                total += try_queue(pid, bkey, unit_id, amount, time_ms)

            if total > 0 and (unit_name or unit_id):
                build_orders[pid].append({
                    'time_ms':  time_ms,
                    'time_str': ms_to_mmss(time_ms),
                    'type':     'unit',
                    'name':     unit_name or f'Unit #{unit_id}',
                    'amount':   total,
                })

        # ── Building placement ────────────────────────────────────────────────
        elif inp.type in ('Build', 'Reseed') and before_castle:
            name = inp.param
            if name:
                build_orders[pid].append({
                    'time_ms':  time_ms,
                    'time_str': ms_to_mmss(time_ms),
                    'type':     'building',
                    'name':     'Reseed Farm' if inp.type == 'Reseed' else name,
                    'amount':   1,
                })

        # ── Technology research ───────────────────────────────────────────────
        elif inp.type == 'Research' and before_castle:
            tech_name = inp.param
            tech_id   = inp.payload.get('technology_id')
            is_age    = tech_id in (101, 102, 103)

            if tech_name or tech_id:
                build_orders[pid].append({
                    'time_ms':  time_ms,
                    'time_str': ms_to_mmss(time_ms),
                    'type':     'age' if is_age else 'tech',
                    'name':     tech_name or f'Tech #{tech_id}',
                    'amount':   1,
                })

            if tech_id == 101 and pid not in feudal_click:
                feudal_click[pid] = time_ms
            if tech_id == 102 and pid not in castle_click:
                castle_click[pid] = time_ms

    players_out = []
    for p in match.players:
        pid  = p.number
        comp = age_completions.get(pid, {})
        # 2 = FEUDAL_AGE, 3 = CASTLE_AGE in the Age enum
        feudal_done_ms = comp.get(2) or comp.get(AgeEnum.FEUDAL_AGE.value
                                                   if hasattr(AgeEnum, 'FEUDAL_AGE') else 2)
        castle_done_ms = comp.get(3) or comp.get(AgeEnum.CASTLE_AGE.value
                                                   if hasattr(AgeEnum, 'CASTLE_AGE') else 3)
        fclick = feudal_click.get(pid)
        cclick = castle_click.get(pid)

        players_out.append({
            'player_number':   pid,
            'name':            p.name,
            'civilization':    p.civilization,
            'civilization_id': p.civilization_id,
            # Click = when the player initiated the research
            'feudal_click_ms':  fclick,
            'feudal_click_str': ms_to_mmss(fclick),
            'feudal_clicked':   fclick is not None,
            # Done = when the research actually completed (game-engine event)
            'feudal_done_ms':   feudal_done_ms,
            'feudal_done_str':  ms_to_mmss(feudal_done_ms),
            'castle_click_ms':  cclick,
            'castle_click_str': ms_to_mmss(cclick),
            'castle_clicked':   cclick is not None,
            'castle_done_ms':   castle_done_ms,
            'castle_done_str':  ms_to_mmss(castle_done_ms),
            'build_order':      build_orders.get(pid, []),
        })

    players_out.sort(key=lambda p: (
        p['feudal_click_ms'] is None,
        p['feudal_click_ms'] or float('inf'),
    ))

    return {
        'players':  players_out,
        'speed':    match.speed,
        'map_name': match.map.name if match.map else None,
        'duration': str(match.duration) if match.duration else None,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def is_zip(data):
    return len(data) >= 4 and data[:4] == b'PK\x03\x04'


def extract_record(file_bytes):
    if is_zip(file_bytes):
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            name = next(
                (n for n in zf.namelist()
                 if n.lower().endswith('.aoe2record') or n.lower().endswith('.mgz')),
                None
            )
            if not name:
                raise ValueError("ZIP contains no .aoe2record file")
            return zf.read(name)
    return file_bytes


def resolve_profile(username):
    url = f"{AOE2NET_BASE}/search?game=aoe2de&search={requests.utils.quote(username)}"
    try:
        r = requests.get(url, timeout=10)
    except requests.exceptions.Timeout:
        raise RuntimeError("aoe2.net timed out")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Cannot reach aoe2.net")
    if r.status_code != 200:
        raise RuntimeError(f"aoe2.net returned HTTP {r.status_code}")
    lb = r.json().get('leaderboard', [])
    if not lb:
        raise LookupError(f"No player found for '{username}'")
    return lb[0]['profile_id'], lb[0].get('name', username)


def fetch_latest_match(profile_id):
    url = f"{AOE2NET_BASE}/player/matches?game=aoe2de&profile_id={profile_id}&count=10"
    try:
        r = requests.get(url, timeout=10)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        raise RuntimeError(f"aoe2.net unreachable: {e}")
    if r.status_code != 200:
        raise RuntimeError(f"aoe2.net returned HTTP {r.status_code}")
    matches = r.json()
    if not matches:
        raise LookupError("No matches found")
    return next((m for m in matches if len(m.get('players', [])) == 2), matches[0])


def extract_replay_url(match):
    replay = match.get('replay')
    if isinstance(replay, dict):
        u = replay.get('url') or replay.get('download_url')
        if u:
            return u
    elif isinstance(replay, str) and replay.startswith('http'):
        return replay
    for key in ('replay_url', 'download_url'):
        u = match.get(key)
        if u and str(u).startswith('http'):
            return u
    return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    raw = request.files['file'].read()
    try:
        record = extract_record(raw)
        return jsonify(parse_replay(record))
    except Exception as e:
        return jsonify({'error': 'parse_failed', 'detail': str(e)}), 422


@app.route('/api/search', methods=['POST'])
def search():
    body = request.get_json(force=True, silent=True) or {}
    username = str(body.get('username') or '').strip()
    if not username:
        return jsonify({'error': 'username required'}), 400

    try:
        profile_id, display_name = resolve_profile(username)
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 502

    try:
        match = fetch_latest_match(profile_id)
    except (LookupError, RuntimeError) as e:
        return jsonify({'error': str(e)}), (404 if isinstance(e, LookupError) else 502)

    url = extract_replay_url(match)
    if not url:
        return jsonify({
            'error':   'no_replay_url',
            'message': f"Match found for {display_name} but no replay URL available. "
                       "Please upload the .aoe2record file manually.",
            'match':   {
                'match_id': match.get('match_id'),
                'players': [{'name': p.get('name')} for p in match.get('players', [])],
            },
        }), 200

    try:
        raw = requests.get(url, timeout=30).content
        record = extract_record(raw)
        return jsonify(parse_replay(record))
    except Exception as e:
        return jsonify({'error': str(e)}), 502


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
