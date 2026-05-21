import io
import zipfile
import requests
from flask import Flask, request, jsonify, render_template

try:
    from mgz.fast.header import parse as parse_header
    from mgz.fast import operation
    try:
        from mgz.fast import meta as _meta_fn
        _HAS_META = True
    except ImportError:
        _HAS_META = False
        _meta_fn = None
    try:
        from mgz.fast.enums import Operation, Action
    except ImportError:
        from mgz.fast import Operation, Action
    MGZ_AVAILABLE = True
except ImportError:
    MGZ_AVAILABLE = False

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

AOE2NET_BASE = "https://aoe2.net/api"
FEUDAL_AGE_TECH_ID = 101


def ms_to_mmss(ms):
    total_secs = ms // 1000
    return f"{total_secs // 60}:{total_secs % 60:02d}"


def _get(obj, *keys, default=None):
    """Try multiple key/attribute names on dict-like or construct Container."""
    for key in keys:
        try:
            val = obj[key]
            if val is not None:
                return val
        except (KeyError, TypeError, IndexError):
            pass
        try:
            val = getattr(obj, key, None)
            if val is not None:
                return val
        except Exception:
            pass
    return default


def build_player_map(header_data):
    """Extract {player_number: {name, civilization_id}} from parsed header."""
    player_map = {}

    # DE path: header_data.de.players
    try:
        de = _get(header_data, 'de')
        if de:
            de_players = _get(de, 'players', default=[])
            for p in (de_players or []):
                num = _get(p, 'number', 'player_id', 'slot')
                if not isinstance(num, int) or num <= 0:
                    continue
                raw = _get(p, 'name', default=b'')
                name = raw.decode('utf-8', errors='replace').rstrip('\x00') if isinstance(raw, bytes) else str(raw or '')
                civ = _get(p, 'civ', 'civilization_id', 'civilization', default=0)
                player_map[num] = {
                    'name': name.strip() or f'Player {num}',
                    'civilization_id': int(civ) if civ else 0,
                }
            if player_map:
                return player_map
    except Exception:
        pass

    # Fallback: header_data.players
    try:
        players = _get(header_data, 'players', default=[])
        for p in (players or []):
            num = _get(p, 'player_id', 'number', 'slot')
            if not isinstance(num, int) or num <= 0:
                continue
            raw = _get(p, 'name', default=b'')
            name = raw.decode('utf-8', errors='replace').rstrip('\x00') if isinstance(raw, bytes) else str(raw or '')
            civ = _get(p, 'civ', 'civilization_id', 'civilization', default=0)
            player_map[num] = {
                'name': name.strip() or f'Player {num}',
                'civilization_id': int(civ) if civ else 0,
            }
    except Exception:
        pass

    return player_map


def parse_replay(record_bytes):
    if not MGZ_AVAILABLE:
        raise RuntimeError("mgz library not installed. Run: pip install mgz")

    data = io.BytesIO(record_bytes)

    try:
        header_data = parse_header(data)
    except Exception as e:
        raise RuntimeError(f"Header parse failed: {e}")

    player_map = build_player_map(header_data)

    if _HAS_META:
        try:
            _meta_fn(data)
        except Exception:
            pass

    current_time_ms = 0
    feudal_times = {}       # player_num (int) -> ms
    unknown_feudal_ms = []  # ms for RESEARCH events where player_id was absent
    file_size = len(record_bytes)

    while data.tell() < file_size:
        try:
            op_type, payload = operation(data)
        except (EOFError, RuntimeError, Exception):
            break

        try:
            if op_type is Operation.SYNC:
                inc = payload[0] if isinstance(payload, (list, tuple)) else _get(payload, 'time_increment', 'time', default=0)
                current_time_ms += int(inc or 0)

            elif op_type is Operation.ACTION:
                action_type, action_data = payload
                if action_type is Action.RESEARCH:
                    ad = action_data if hasattr(action_data, 'get') else {}
                    tech_id = ad.get('technology_id') if ad else _get(action_data, 'technology_id')
                    player_num = ad.get('player_id') if ad else _get(action_data, 'player_id')

                    if tech_id == FEUDAL_AGE_TECH_ID:
                        if isinstance(player_num, int) and player_num > 0:
                            feudal_times.setdefault(player_num, current_time_ms)
                        else:
                            unknown_feudal_ms.append(current_time_ms)
        except Exception:
            continue

    # Assign any unknown-player feudal times to unfilled player slots (sorted by time)
    if unknown_feudal_ms and player_map:
        filled = set(feudal_times.keys())
        unfilled = sorted(set(player_map.keys()) - filled)
        unknown_feudal_ms.sort()
        for pnum, t in zip(unfilled, unknown_feudal_ms):
            feudal_times[pnum] = t

    # If we still have no player_map entries, create placeholders from feudal_times
    if not player_map:
        for pnum in sorted(feudal_times.keys()):
            player_map[pnum] = {'name': f'Player {pnum}', 'civilization_id': 0}

    results = []
    for pnum, pinfo in sorted(player_map.items()):
        feudal_ms = feudal_times.get(pnum)
        results.append({
            'player_number': pnum,
            'name': pinfo['name'],
            'civilization_id': pinfo['civilization_id'],
            'feudal_time_ms': feudal_ms,
            'feudal_time_formatted': ms_to_mmss(feudal_ms) if feudal_ms is not None else None,
            'feudal_clicked': feudal_ms is not None,
        })

    results.sort(key=lambda r: (
        not r['feudal_clicked'],
        r['feudal_time_ms'] if r['feudal_time_ms'] is not None else float('inf'),
    ))

    version_str = str(_get(header_data, 'version', default='unknown'))
    return {'players': results, 'version': version_str}


def is_zip(data):
    return len(data) >= 4 and data[:4] == b'PK\x03\x04'


def extract_from_zip(file_bytes):
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
        names = zf.namelist()
        record_name = next((n for n in names if n.lower().endswith('.aoe2record')), None)
        if not record_name:
            raise ValueError("ZIP does not contain a .aoe2record file")
        return zf.read(record_name)


def resolve_profile(username):
    url = f"{AOE2NET_BASE}/search?game=aoe2de&search={requests.utils.quote(username)}"
    try:
        resp = requests.get(url, timeout=10)
    except requests.exceptions.Timeout:
        raise RuntimeError("aoe2.net timed out — try again later")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Cannot reach aoe2.net — check network")

    if resp.status_code != 200:
        raise RuntimeError(f"aoe2.net search returned HTTP {resp.status_code}")

    data = resp.json()
    leaderboard = data.get('leaderboard', [])
    if not leaderboard:
        raise LookupError(f"No player found for '{username}'")

    player = leaderboard[0]
    return player['profile_id'], player.get('name', username)


def fetch_latest_match(profile_id):
    url = f"{AOE2NET_BASE}/player/matches?game=aoe2de&profile_id={profile_id}&count=10"
    try:
        resp = requests.get(url, timeout=10)
    except requests.exceptions.Timeout:
        raise RuntimeError("aoe2.net timed out")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Cannot reach aoe2.net")

    if resp.status_code != 200:
        raise RuntimeError(f"aoe2.net matches returned HTTP {resp.status_code}")

    matches = resp.json()
    if not matches:
        raise LookupError("No matches found for this player")

    for m in matches:
        if len(m.get('players', [])) == 2:
            return m
    return matches[0]


def extract_replay_url(match):
    replay_obj = match.get('replay')
    if isinstance(replay_obj, dict):
        url = replay_obj.get('url') or replay_obj.get('download_url')
        if url:
            return url
    elif isinstance(replay_obj, str) and replay_obj.startswith('http'):
        return replay_obj

    for key in ('replay_url', 'download_url'):
        val = match.get(key)
        if val and isinstance(val, str) and val.startswith('http'):
            return val
    return None


def download_replay(url):
    try:
        resp = requests.get(url, timeout=30)
    except requests.exceptions.Timeout:
        raise RuntimeError("Replay download timed out")
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Replay download failed: {e}")

    if resp.status_code != 200:
        raise RuntimeError(f"Failed to download replay: HTTP {resp.status_code}")

    raw = resp.content
    if is_zip(raw):
        return extract_from_zip(raw)
    return raw


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/search', methods=['POST'])
def search_and_analyze():
    data = request.get_json()
    if not data or not str(data.get('username', '')).strip():
        return jsonify({'error': 'username required'}), 400

    username = str(data['username']).strip()

    try:
        profile_id, display_name = resolve_profile(username)
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 502

    try:
        match = fetch_latest_match(profile_id)
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 502

    replay_url = extract_replay_url(match)
    if not replay_url:
        return jsonify({
            'error': 'no_replay_url',
            'message': (
                f"Match found for {display_name}, but aoe2.net does not provide "
                "a replay download URL. Please upload the .aoe2record or .zip file manually."
            ),
            'match': {
                'match_id': match.get('match_id'),
                'players': [
                    {'name': p.get('name'), 'profile_id': p.get('profile_id')}
                    for p in match.get('players', [])
                ],
            },
        }), 200

    try:
        record_bytes = download_replay(replay_url)
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 502

    try:
        result = parse_replay(record_bytes)
    except RuntimeError as e:
        return jsonify({'error': 'parse_failed', 'detail': str(e)}), 422

    return jsonify(result)


@app.route('/api/upload', methods=['POST'])
def upload_and_analyze():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'No file selected'}), 400

    file_bytes = f.read()

    try:
        record_bytes = extract_from_zip(file_bytes) if is_zip(file_bytes) else file_bytes
    except (ValueError, zipfile.BadZipFile) as e:
        return jsonify({'error': 'parse_failed', 'detail': str(e)}), 422

    try:
        result = parse_replay(record_bytes)
    except RuntimeError as e:
        return jsonify({'error': 'parse_failed', 'detail': str(e)}), 422

    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
