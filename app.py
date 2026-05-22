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

# Unit costs: (food, wood, gold, stone)
UNIT_COSTS = {
    83: (50, 0, 0, 0),      # Villager
    74: (60, 0, 0, 0),      # Militia
    75: (60, 0, 0, 0),      # Man-at-Arms
    77: (60, 20, 0, 0),     # Long Swordsman
    567: (60, 20, 0, 0),    # Champion
    93: (60, 0, 0, 0),      # Spearman
    358: (75, 0, 0, 0),     # Pikeman
    359: (75, 0, 0, 0),     # Halberdier
    448: (80, 0, 0, 0),     # Scout Cavalry
    37: (80, 0, 0, 0),      # Light Cavalry
    441: (80, 0, 0, 0),     # Hussar
    38: (80, 0, 0, 0),      # Knight
    283: (80, 0, 0, 0),     # Cavalier
    569: (80, 0, 0, 0),     # Paladin
    4: (45, 25, 0, 0),      # Archer
    24: (40, 25, 0, 0),     # Crossbowman
    492: (40, 25, 0, 0),    # Arbalester
    7: (35, 0, 0, 0),       # Skirmisher
    6: (35, 0, 0, 0),       # Elite Skirmisher
    8: (45, 25, 0, 0),      # Longbowman
    751: (40, 0, 0, 0),     # Eagle Scout
    753: (40, 0, 0, 0),     # Eagle Warrior
    125: (0, 0, 100, 50),   # Monk
    13: (75, 0, 0, 0),      # Fishing Ship
    17: (100, 0, 0, 0),     # Trade Cog
    128: (80, 0, 0, 0),     # Trade Cart
    35: (160, 0, 0, 0),     # Battering Ram
    422: (160, 0, 0, 0),    # Capped Ram
    280: (160, 0, 0, 0),    # Mangonel
    279: (100, 0, 0, 0),    # Scorpion
    40: (100, 0, 0, 0),     # Cataphract
    329: (100, 0, 0, 0),    # Camel Rider
}

# Building costs: (food, wood, gold, stone)
BUILDING_COSTS = {
    70: (0, 30, 0, 0),      # House
    12: (0, 100, 0, 50),    # Barracks
    10: (0, 100, 0, 50),    # Archery Range
    86: (0, 100, 0, 50),    # Stable
    50: (0, 75, 0, 0),      # Farm
    18: (0, 100, 0, 0),     # Blacksmith
    562: (0, 100, 0, 0),    # Lumber Camp
    584: (0, 100, 0, 0),    # Mining Camp
    30: (0, 100, 0, 0),     # Monastery
    84: (0, 100, 0, 0),     # Market
    68: (0, 75, 0, 0),      # Mill
    45: (0, 100, 0, 0),     # Dock
    71: (0, 200, 0, 0),     # Town Center
    82: (0, 200, 0, 200),   # Castle
    598: (0, 0, 0, 0),      # Outpost
}

# Technology costs: (food, wood, gold, stone)
TECH_COSTS = {
    101: (500, 0, 0, 0),    # Feudal Age
    102: (800, 0, 200, 0),  # Castle Age
    103: (1000, 0, 400, 0), # Imperial Age
}

# Gather rates (per-minute at 1.0x, per villager)
# Will be converted to per-millisecond during simulation
GATHER_RATES = {
    'wood': 25.0,           # /min
    'food': 20.0,           # /min (average of farm/forage/hunt)
    'gold': 28.0,           # /min
    'stone': 26.0,          # /min
}


class ResourceState:
    """Simulate per-resource balances over time."""
    def __init__(self, speed_mult=1.0, start_food=200, start_wood=200, start_gold=0, start_stone=0):
        self.food = start_food
        self.wood = start_wood
        self.gold = start_gold
        self.stone = start_stone
        self.time_ms = 0
        self.speed_mult = speed_mult

        # Gather rates in per-ms (convert from per-minute)
        self.food_rate = GATHER_RATES['food'] / 60000.0 * speed_mult
        self.wood_rate = GATHER_RATES['wood'] / 60000.0 * speed_mult
        self.gold_rate = GATHER_RATES['gold'] / 60000.0 * speed_mult
        self.stone_rate = GATHER_RATES['stone'] / 60000.0 * speed_mult

        # Villager counts per resource
        self.food_villagers = 0
        self.wood_villagers = 0
        self.gold_villagers = 0
        self.stone_villagers = 0

        # Timeseries calibration data (from match)
        self.timeseries_points = []  # (time_ms, total_res)

    def set_timeseries(self, player):
        """Extract timeseries points for calibration."""
        for row in player.timeseries:
            t_ms = int(row.timestamp.total_seconds() * 1000)
            total = row.total_resources
            self.timeseries_points.append((t_ms, total))

    def advance_to(self, time_ms):
        """Advance time and accumulate resources from gathering."""
        if time_ms <= self.time_ms:
            return
        dt = time_ms - self.time_ms
        self.food += self.food_rate * self.food_villagers * dt
        self.wood += self.wood_rate * self.wood_villagers * dt
        self.gold += self.gold_rate * self.gold_villagers * dt
        self.stone += self.stone_rate * self.stone_villagers * dt
        self.time_ms = time_ms

    def add_villager_gather(self, resource_type):
        """Assign a villager to gather a resource (from Gather action)."""
        if resource_type == 'wood':
            self.wood_villagers += 1
        elif resource_type == 'gold':
            self.gold_villagers += 1
        elif resource_type == 'stone':
            self.stone_villagers += 1
        elif resource_type in ('food', 'farm', 'forage', 'hunt'):
            self.food_villagers += 1

    def can_afford(self, food_cost, wood_cost, gold_cost, stone_cost):
        """Check if player can afford given costs."""
        return (self.food >= food_cost and
                self.wood >= wood_cost and
                self.gold >= gold_cost and
                self.stone >= stone_cost)

    def spend(self, food_cost, wood_cost, gold_cost, stone_cost):
        """Deduct costs (when action completes)."""
        self.food -= food_cost
        self.wood -= wood_cost
        self.gold -= gold_cost
        self.stone -= stone_cost
        # Clamp to 0 (in case of overspend from approximation)
        self.food = max(0, self.food)
        self.wood = max(0, self.wood)
        self.gold = max(0, self.gold)
        self.stone = max(0, self.stone)

    def total_resources(self):
        """Current combined total resources."""
        return self.food + self.wood + self.gold + self.stone


class PopulationState:
    """Track population and population cap."""
    def __init__(self):
        self.pop = 5     # Start with 1 TC (5 pop cap) + food for ~5 villagers
        self.pop_cap = 5 # Town Center gives 5 pop
        self.houses = 1  # Implicit starting house (TC)

    def add_house(self):
        """House placed adds 5 to pop cap."""
        self.houses += 1
        self.pop_cap += 5

    def add_unit(self, unit_id):
        """Unit trained takes up pop (most units are 1 pop)."""
        # Most military units are 1 pop, siege varies, but all are ≤5
        self.pop += 1

    def can_train(self):
        """Check if pop cap allows training."""
        return self.pop < self.pop_cap

    def get_pop_string(self):
        """Return pop/cap string."""
        return f"{self.pop}/{self.pop_cap}"


def ms_to_mmss(ms):
    if ms is None:
        return None
    s = int(ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def infer_gather_type(param_name):
    """Infer resource type from Gather action parameter name."""
    if not param_name:
        return None
    p = param_name.lower()
    if 'tree' in p or 'forester' in p:
        return 'wood'
    if 'gold' in p or 'gold mine' in p:
        return 'gold'
    if 'stone' in p or 'quarry' in p or 'stone mine' in p:
        return 'stone'
    if any(x in p for x in ['farm', 'forage', 'berry', 'deer', 'boar', 'sheep', 'hunt', 'fish']):
        return 'food'
    return None


def parse_replay(record_bytes):
    """
    Parse replay with full game state simulation:
    - Resource tracking per food/wood/gold/stone
    - Population tracking for unit training
    - Affordability checks for queue/build/research actions
    - Accurate age-up times from game engine
    """
    handle = io.BytesIO(record_bytes)
    match = parse_match(handle)

    speed_mult = (match.speed_id or 100) / 100.0

    # Age-up completion times from game engine
    age_completions = {}
    for uptime in match.uptimes:
        if not uptime.player:
            continue
        pid = uptime.player.number
        if pid not in age_completions:
            age_completions[pid] = {}
        ms = int(uptime.timestamp.total_seconds() * 1000)
        av = uptime.age.value if hasattr(uptime.age, 'value') else int(uptime.age)
        age_completions[pid][av] = ms

    # Initialize resource and population states
    resource_states = {}
    population_states = {}
    for p in match.players:
        resource_states[p.number] = ResourceState(speed_mult=speed_mult)
        resource_states[p.number].set_timeseries(p)
        population_states[p.number] = PopulationState()

    # Per-building production queue state
    bqueues = {}

    def try_queue(pid, bkey, unit_id, amount, now_ms):
        if pid not in bqueues:
            bqueues[pid] = {}
        if bkey not in bqueues[pid]:
            bqueues[pid][bkey] = []
        q = bqueues[pid][bkey]
        base = TRAIN_MS.get(unit_id, 30000)
        train_ms = int(base / speed_mult)
        while q and q[0] <= now_ms:
            q.pop(0)
        slots = QUEUE_MAX - len(q)
        if slots <= 0:
            return 0
        actual = min(int(amount) if amount else 1, slots)
        for _ in range(actual):
            prev = q[-1] if q else now_ms
            q.append(prev + train_ms)
        return actual

    feudal_click = {}
    feudal_pop = {}
    castle_click = {}
    build_orders = {p.number: [] for p in match.players}

    for inp in match.inputs:
        if not inp.player:
            continue
        pid = inp.player.number
        if pid not in build_orders:
            continue

        time_ms = int(inp.timestamp.total_seconds() * 1000)
        before_castle = pid not in castle_click

        # Advance resource and population state to this time
        res = resource_states[pid]
        pop = population_states[pid]
        res.advance_to(time_ms)

        # ── Gather (villager assignment) ──────────────────────────────────
        if inp.type == 'Gather':
            gather_type = infer_gather_type(inp.param)
            if gather_type:
                res.add_villager_gather(gather_type)

        # ── Unit training ─────────────────────────────────────────────────
        elif inp.type == 'Queue' and before_castle:
            unit_name = inp.param
            unit_id = inp.payload.get('unit_id')
            amount = inp.payload.get('amount') or 1
            object_ids = inp.payload.get('object_ids') or []
            bkeys = object_ids if object_ids else [f'p{pid}_0']

            # Check affordability
            cost = UNIT_COSTS.get(unit_id, (50, 0, 0, 0))
            total_cost = (cost[0] * amount, cost[1] * amount, cost[2] * amount, cost[3] * amount)

            total = 0
            if res.can_afford(*total_cost) and pop.can_train():
                for bkey in bkeys:
                    total += try_queue(pid, bkey, unit_id, amount, time_ms)

            if total > 0 and (unit_name or unit_id):
                res.spend(*total_cost)
                for _ in range(total):
                    pop.add_unit(unit_id)
                build_orders[pid].append({
                    'time_ms':  time_ms,
                    'time_str': ms_to_mmss(time_ms),
                    'type':     'unit',
                    'name':     unit_name or f'Unit #{unit_id}',
                    'amount':   total,
                })

        # ── Building placement ────────────────────────────────────────────
        elif inp.type in ('Build', 'Reseed') and before_castle:
            name = inp.param
            if name:
                # Check affordability
                building_id = inp.payload.get('building_id')
                cost = BUILDING_COSTS.get(building_id, (0, 75, 0, 0))
                if res.can_afford(*cost):
                    res.spend(*cost)
                    # Track houses for pop cap
                    if building_id == 70:
                        pop.add_house()
                    build_orders[pid].append({
                        'time_ms':  time_ms,
                        'time_str': ms_to_mmss(time_ms),
                        'type':     'building',
                        'name':     'Reseed Farm' if inp.type == 'Reseed' else name,
                        'amount':   1,
                    })

        # ── Technology research ───────────────────────────────────────────
        elif inp.type == 'Research' and before_castle:
            tech_name = inp.param
            tech_id = inp.payload.get('technology_id')
            is_age = tech_id in (101, 102, 103)

            # Check affordability
            cost = TECH_COSTS.get(tech_id, (500, 0, 0, 0))
            if res.can_afford(*cost):
                res.spend(*cost)
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
                    feudal_pop[pid] = pop.get_pop_string()
                if tech_id == 102 and pid not in castle_click:
                    castle_click[pid] = time_ms

    players_out = []
    for p in match.players:
        pid = p.number
        comp = age_completions.get(pid, {})
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
            'feudal_click_ms':  fclick,
            'feudal_click_str': ms_to_mmss(fclick),
            'feudal_clicked':   fclick is not None,
            'feudal_pop':       feudal_pop.get(pid, '?/?'),
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
