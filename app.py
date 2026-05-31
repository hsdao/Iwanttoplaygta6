"""
Bangkok Metro Pathfinder – simplified backend
Run: pip install -r requirements.txt
     python app.py
Then open: http://localhost:5000
"""

import json, math, heapq, os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder=".")
CORS(app)

# ── Load data ────────────────────────────────────────────────
_here = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_here, "data", "bangkok-metro-v2.json"), encoding="utf-8") as f:
    DATA = json.load(f)

# Resolve GeoJSON directory: env var > data/ subfolder > sibling frontend folder
def _find_geo_dir() -> str:
    env_override = os.environ.get("GEO_DATA_DIR")
    if env_override and os.path.isdir(env_override):
        return env_override
    local = os.path.join(_here, "data")
    if os.path.isfile(os.path.join(local, "railways.geojson")):
        return local
    sibling = os.path.join(_here, "..", "frontend", "public", "data")
    if os.path.isfile(os.path.join(sibling, "railways.geojson")):
        return os.path.normpath(sibling)
    raise FileNotFoundError(
        "GeoJSON files not found. Copy railways.geojson and stations.geojson into "
        f"{local!r}, or set the GEO_DATA_DIR environment variable."
    )

_geo_dir = _find_geo_dir()
with open(os.path.join(_geo_dir, "railways.geojson"), encoding="utf-8") as f:
    RAILWAYS_GEOJSON = json.load(f)
with open(os.path.join(_geo_dir, "stations.geojson"), encoding="utf-8") as f:
    STATIONS_GEOJSON = json.load(f)

STATIONS   = {s["id"]: s for s in DATA["stations"]}
LINES      = {l["id"]: l for l in DATA["lines"]}
SEGMENTS   = DATA["segments"]
EXCHANGES  = DATA["interchanges"]
SCHEDULES  = DATA["schedulePeriods"]

# Override station coordinates with accurate GeoJSON positions (stations.geojson is
# sourced from Overpass/OSM; bangkok-metro-v2.json has errors up to 10 km for some stations)
for _feat in STATIONS_GEOJSON["features"]:
    _sid = (_feat.get("properties") or {}).get("id")
    if _sid and _sid in STATIONS:
        _lng, _lat = _feat["geometry"]["coordinates"]
        STATIONS[_sid]["lat"] = _lat
        STATIONS[_sid]["lng"] = _lng

# stationId -> [lineId]
STATION_LINES: dict[str, list[str]] = {}
for sl in DATA["stationLines"]:
    STATION_LINES.setdefault(sl["stationId"], []).append(sl["lineId"])

# (lineId, fromStation, toStation) -> distanceKm  (bidirectional)
SEG_DIST: dict[tuple, float] = {}
for _seg in SEGMENTS:
    SEG_DIST[(_seg["lineId"], _seg["fromStation"], _seg["toStation"])] = _seg["distanceKm"]
    SEG_DIST[(_seg["lineId"], _seg["toStation"], _seg["fromStation"])] = _seg["distanceKm"]

# In-memory block rules: {(lineId, fromStation, toStation): reason_str}
BLOCKED: dict[tuple, str] = {}
# In-memory closed stations: {stationId: reason_str}
BLOCKED_STATIONS: dict[str, str] = {}

# ── Fare tables (Baht, based on published fare ranges 2024-2026) ──
# Index i = fare for (i+1) stations; last element is the cap.
# Sources: btsbangkok.com, mrtbangkok.com, thaiest.com
_FARE_TABLE: dict[str, list[int]] = {
    # BTS Green Line (Sukhumvit + Silom): 17–65 ฿ (max raised Nov 2025)
    # Approx. +3 ฿ per station from base 17 ฿, capped at 65 ฿
    "bts":    [17, 20, 23, 26, 29, 32, 35, 38, 41, 44, 47, 50, 53, 56, 59, 62, 65],

    # BTS Gold Line: flat 15 ฿
    "gold":   [15],

    # MRT Blue Line: 17–45 ฿ (Jul 2024 – Jul 2026)
    # Official formula: base 17 ฿; +1 ฿ at stops 2-7,9,11; +2 ฿ at stops 8,10,12+
    "blue":   [17, 18, 19, 20, 21, 22, 23, 25, 26, 28, 29, 31, 33, 35, 37, 39, 41, 43, 45],

    # MRT Purple Line: 14–42 ฿ (16 stations)
    # Approx. +2-3 ฿ per station from base 14 ฿
    "purple": [14, 17, 20, 23, 26, 29, 32, 35, 38, 41, 42, 42, 42, 42, 42, 42],

    # Airport Rail Link City Line: 15–45 ฿ (8 stations, +5 ฿/station)
    "arl":    [15, 20, 25, 30, 35, 40, 45],

    # SRT Red Line (Dark + Light): 14–42 ฿
    # Approx. +5 ฿ per station from base 14 ฿
    "red":    [14, 19, 24, 29, 34, 39, 42, 42, 42, 42],

    # MRT Yellow Line: 15–45 ฿ (23 stations), approx. +3 ฿ per station
    "yellow": [15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 45, 45, 45,
               45, 45, 45, 45, 45, 45, 45, 45, 45],

    # MRT Pink Line: 15–45 ฿ (30 stations + spur), approx. +3 ฿ per station
    "pink":   [15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 45, 45, 45, 45,
               45, 45, 45, 45, 45, 45, 45, 45, 45, 45, 45, 45, 45, 45, 45],
}
_LINE_FARE_GROUP: dict[str, str] = {
    "sukhumvit": "bts", "silom": "bts", "gold": "gold",
    "blue": "blue", "purple": "purple", "arl": "arl",
    "dark_red": "red", "light_red": "red",
    "yellow": "yellow", "pink": "pink", "pink_mt": "pink",
}
_OPERATOR_LABEL: dict[str, str] = {
    "bts": "BTS", "gold": "BTS Gold Line", "blue": "MRT Blue Line",
    "purple": "MRT Purple Line", "arl": "Airport Rail Link",
    "red": "SRT Red Line", "yellow": "MRT Yellow Line", "pink": "MRT Pink Line",
}
# BTS Sukhumvit + Silom share one combined ticket for a continuous journey
_BTS_SHARED = {"sukhumvit", "silom"}


def _line_fare(group: str, num_stations: int) -> int:
    table = _FARE_TABLE.get(group, _FARE_TABLE["bts"])
    return table[min(num_stations - 1, len(table) - 1)]


def calculate_fare(legs: list) -> dict:
    """Annotate each train leg with 'fare' (Baht) in-place and return summary."""
    train_legs = [l for l in legs if l["type"] == "train"]
    breakdown, total, i = [], 0, 0

    while i < len(train_legs):
        leg = train_legs[i]
        lid = leg["line_id"]
        fg  = _LINE_FARE_GROUP.get(lid, "bts")

        if lid in _BTS_SHARED:
            # Merge all consecutive BTS-shared legs into one ticket
            j, stations, names = i + 1, len(leg["stations"]) - 1, [leg["line_name"]]
            while j < len(train_legs) and train_legs[j]["line_id"] in _BTS_SHARED:
                stations += len(train_legs[j]["stations"]) - 1
                names.append(train_legs[j]["line_name"])
                j += 1
            fare = _line_fare("bts", stations)
            leg["fare"] = fare
            for k in range(i + 1, j):
                train_legs[k]["fare"] = 0   # included in combined BTS ticket
            breakdown.append({"operator": "BTS", "lines": names,
                               "stations": stations, "fare": fare})
            i = j
        else:
            stations = len(leg["stations"]) - 1
            fare = _line_fare(fg, stations)
            leg["fare"] = fare
            breakdown.append({"operator": _OPERATOR_LABEL.get(fg, fg),
                               "lines": [leg["line_name"]],
                               "stations": stations, "fare": fare})
            i += 1
        total += fare

    return {"total": total, "breakdown": breakdown}


# ── Geo helpers ──────────────────────────────────────────────
def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def nearest_stations(lat, lon, max_km=1.5):
    """Return [(dist_km, station_id)] sorted by distance."""
    result = []
    for sid, st in STATIONS.items():
        d = haversine_km(lat, lon, st["lat"], st["lng"])
        if d <= max_km:
            result.append((d, sid))
    return sorted(result)


# ── Schedule ─────────────────────────────────────────────────
def get_wait_times(hour: int) -> dict[str, float]:
    """Return {lineId: waitMin} for the given departure hour."""
    for period in SCHEDULES:
        if period["startHour"] <= hour < period["endHour"]:
            return {w["lineId"]: w["initialWaitMin"] for w in period["waitTimes"]}
    return {w["lineId"]: w["initialWaitMin"] for w in SCHEDULES[-1]["waitTimes"]}


# ── Graph builder ────────────────────────────────────────────
def is_blocked(line_id, frm, to) -> bool:
    return (line_id, frm, to) in BLOCKED or (line_id, to, frm) in BLOCKED

def is_station_closed(sid) -> bool:
    return sid in BLOCKED_STATIONS

def blocked_list():
    return [
        {"lineId": k[0], "fromStation": k[1], "toStation": k[2], "reason": v}
        for k, v in BLOCKED.items()
    ]

def blocked_stations_list():
    return [
        {"stationId": k, "name": STATIONS[k]["name"] if k in STATIONS else k, "reason": v}
        for k, v in BLOCKED_STATIONS.items()
    ]

def build_graph(hour: int, transfer_mult: float = 1.0,
                excluded_lines: set = None) -> dict[str, list]:
    """
    Time-expanded graph: node = 'stationId:lineId'
    Edge types:
      1. Same-line travel between adjacent stations
      2. Transfer at the same physical station (different lines)
      3. Interchange between two nearby stations
    transfer_mult > 1 penalises transfers (fewest-transfers mode).
    excluded_lines: set of lineIds the user does not want to travel on.
    """
    excl = excluded_lines or set()
    graph: dict[str, list] = {}
    waits = get_wait_times(hour)

    def add(u, v, cost):
        graph.setdefault(u, []).append((v, cost))

    # 1. Same-line edges
    for seg in SEGMENTS:
        if seg["lineId"] in excl:
            continue
        if is_blocked(seg["lineId"], seg["fromStation"], seg["toStation"]):
            continue
        if is_station_closed(seg["fromStation"]) or is_station_closed(seg["toStation"]):
            continue
        speed  = LINES[seg["lineId"]]["speedKmh"]
        travel = seg["distanceKm"] / speed * 60
        u = f"{seg['fromStation']}:{seg['lineId']}"
        v = f"{seg['toStation']}:{seg['lineId']}"
        add(u, v, travel)
        add(v, u, travel)

    # 2. Transfer edges (same station, different lines) — fixed 3 min
    for sid, lines in STATION_LINES.items():
        if is_station_closed(sid):
            continue
        active = [l for l in lines if l not in excl]
        if len(active) < 2:
            continue
        for i, la in enumerate(active):
            for lb in active[i + 1:]:
                add(f"{sid}:{la}", f"{sid}:{lb}", 3.0 * transfer_mult + waits.get(lb, 5))
                add(f"{sid}:{lb}", f"{sid}:{la}", 3.0 * transfer_mult + waits.get(la, 5))

    # 3. Interchange edges (different stations, walkable connection) — fixed 5 min
    for ix in EXCHANGES:
        sa, sb = ix["stationA"], ix["stationB"]
        if is_station_closed(sa) or is_station_closed(sb):
            continue
        for la in STATION_LINES.get(sa, []):
            if la in excl:
                continue
            for lb in STATION_LINES.get(sb, []):
                if lb in excl:
                    continue
                add(f"{sa}:{la}", f"{sb}:{lb}", 5.0 * transfer_mult + waits.get(lb, 5))
                add(f"{sb}:{lb}", f"{sa}:{la}", 5.0 * transfer_mult + waits.get(la, 5))

    return graph


# ── A* algorithm ─────────────────────────────────────────────
def heuristic(node: str, goal_lat: float, goal_lon: float) -> float:
    """
    Admissible heuristic: straight-line distance / 90 km/h (max metro speed).
    h(n) <= h*(n) because 90 km/h > actual metro speed and
    haversine <= actual travel distance  →  never over-estimates.
    """
    sid = node.split(":")[0]
    st  = STATIONS.get(sid)
    if not st:
        return 0.0
    return haversine_km(st["lat"], st["lng"], goal_lat, goal_lon) / 90 * 60

def astar(graph, start_nodes, goal_sids, goal_lat, goal_lon):
    """
    start_nodes : [(initial_cost, node_id), ...]
    goal_sids   : set of station ids that count as destination
    Returns (path_list, total_cost) or (None, None)
    """
    open_set  = []
    g_score   = {}
    came_from = {}

    for g0, node in start_nodes:
        f0 = g0 + heuristic(node, goal_lat, goal_lon)
        heapq.heappush(open_set, (f0, g0, node))
        g_score[node] = g0

    while open_set:
        f, g, cur = heapq.heappop(open_set)

        # Skip stale heap entries (lazy deletion pattern)
        if g > g_score.get(cur, math.inf):
            continue

        sid = cur.split(":")[0]
        if sid in goal_sids:
            path = []
            n = cur
            while n in came_from:
                path.append(n)
                n = came_from[n]
            path.append(n)
            path.reverse()
            return path, g

        for neighbor, cost in graph.get(cur, []):
            tg = g + cost
            if tg < g_score.get(neighbor, math.inf):
                g_score[neighbor]   = tg
                came_from[neighbor] = cur
                heapq.heappush(open_set,
                    (tg + heuristic(neighbor, goal_lat, goal_lon), tg, neighbor))

    return None, None


# ── Route formatting into legs ────────────────────────────────
def build_legs(path, from_lat, from_lon, to_lat, to_lon, walk_speed_kmh=5.0, waits=None):
    """
    Convert raw node path into structured legs matching original API format:
      walk  – đi bộ đến/từ ga
      train – đi trên một tuyến liên tiếp
      transfer – đổi tuyến

    waits: {lineId: boardingWaitMin} — each train leg includes its boarding wait
    so that the sum of all leg minutes equals total_minutes from A*.
    """
    waits = waits or {}
    legs = []
    station_names = {}

    def add_name(sid):
        if sid not in station_names:
            station_names[sid] = STATIONS[sid]["name"]

    if not path:
        return legs, station_names, {}

    # ── Walking leg to first station ──────────────────────────
    first_sid, first_lid = path[0].split(":", 1)
    first_st = STATIONS[first_sid]
    walk_dist = haversine_km(from_lat, from_lon, first_st["lat"], first_st["lng"])
    walk_min  = walk_dist / walk_speed_kmh * 60
    first_wait = (waits or {}).get(first_lid, 0)
    add_name(first_sid)
    legs.append({
        "type":        "walk",
        "from_lat":    from_lat,
        "from_lon":    from_lon,
        "to_station":  first_sid,
        "distance_km": round(walk_dist, 3),
        "minutes":     round(walk_min + first_wait, 1),
        "boarding_wait": first_wait,
    })

    # ── Train / transfer legs ─────────────────────────────────
    current_line   = first_lid
    current_train  = [first_sid]
    # Boarding wait for the current train segment (charged once per boarding).
    # The first train's wait comes from find_path's start_nodes; subsequent waits
    # come from transfer/interchange edges. Both are mirrored here for display.
    current_wait   = waits.get(first_lid, 0)

    def flush_train():
        if len(current_train) < 2:
            return
        lid   = current_line
        color = LINES[lid]["color"]
        mins  = 0
        for i in range(len(current_train) - 1):
            sa_id = current_train[i]
            sb_id = current_train[i + 1]
            sa = STATIONS[sa_id]
            sb = STATIONS[sb_id]
            d = SEG_DIST.get((lid, sa_id, sb_id),
                             haversine_km(sa["lat"], sa["lng"], sb["lat"], sb["lng"]))
            mins += d / LINES[lid]["speedKmh"] * 60
        legs.append({
            "type":         "train",
            "line_id":      lid,
            "line_name":    LINES[lid]["name"],
            "color":        color,
            "stations":     list(current_train),
            "minutes":      round(mins, 1),
            "boarding_wait": current_wait,
        })

    prev_sid  = first_sid
    prev_lid  = first_lid

    for node in path[1:]:
        sid, lid = node.split(":", 1)
        add_name(sid)

        if lid == prev_lid:
            # Continuing on same line
            current_train.append(sid)
        else:
            # Line changed → flush current train, add transfer
            flush_train()
            current_train = [sid]
            current_line  = lid
            current_wait  = waits.get(lid, 0)
            legs.append({
                "type":         "transfer",
                "station":      prev_sid,
                "to_station":   sid,
                "from_line":    prev_lid,
                "to_line":      lid,
                "from_line_name": LINES[prev_lid]["name"],
                "to_line_name": LINES[lid]["name"],
                "from_color":   LINES[prev_lid]["color"],
                "to_color":     LINES[lid]["color"],
                "minutes":      (3.0 if prev_sid == sid else 5.0) + waits.get(lid, 0),
            })

        prev_sid = sid
        prev_lid = lid

    flush_train()

    # ── Walking leg from last station ─────────────────────────
    last_sid = path[-1].split(":")[0]
    last_st  = STATIONS[last_sid]
    walk_dist2 = haversine_km(last_st["lat"], last_st["lng"], to_lat, to_lon)
    walk_min2  = walk_dist2 / walk_speed_kmh * 60
    add_name(last_sid)
    legs.append({
        "type":         "walk",
        "from_station": last_sid,
        "to_lat":       to_lat,
        "to_lon":       to_lon,
        "distance_km":  round(walk_dist2, 3),
        "minutes":      round(walk_min2, 1),
    })

    # Station coords for frontend map rendering
    station_coords = {sid: [STATIONS[sid]["lng"], STATIONS[sid]["lat"]]
                      for sid in station_names}

    return legs, station_names, station_coords


# ── Cross-period wait correction ─────────────────────────────
def adjust_waits_for_time(legs: list, dep_hour: int, dep_minute: int = 0) -> None:
    """Fix boarding waits in-place using the actual clock time at each boarding.

    build_legs() stamps every train leg with the wait from the departure
    hour's period.  If the journey crosses a period boundary (e.g. departs
    08:50, boards a connecting train at 09:05) that leg's wait is wrong.
    We walk the legs in order, track elapsed clock minutes, and look up the
    correct schedulePeriod for the actual boarding time.
    """
    elapsed = dep_hour * 60 + dep_minute
    for i, leg in enumerate(legs):
        if leg["type"] == "train":
            actual_hour = (elapsed // 60) % 24
            correct_wait = get_wait_times(actual_hour).get(leg["line_id"], 5)
            old_wait = leg["boarding_wait"]
            diff = correct_wait - old_wait
            if diff:
                leg["boarding_wait"] = correct_wait
                prev = legs[i - 1] if i > 0 else None
                if prev and prev["type"] in ("transfer", "walk"):
                    prev["minutes"] = round(prev["minutes"] + diff, 1)
                    if "boarding_wait" in prev:
                        prev["boarding_wait"] = round(prev.get("boarding_wait", 0) + diff, 1)
        elapsed += leg["minutes"]


# ── Route builder helper ─────────────────────────────────────
def _build_route(path, _total_min_graph, from_lat, from_lon, to_lat, to_lon,
                 walk_speed, waits, label="", dep_hour=12, dep_minute=0):
    """Build legs, fix cross-period waits, compute fare, return route dict."""
    legs, station_names, station_coords = build_legs(
        path, from_lat, from_lon, to_lat, to_lon, walk_speed, waits)
    adjust_waits_for_time(legs, dep_hour, dep_minute)
    total_min = sum(l["minutes"] for l in legs)
    transfers = sum(1 for l in legs if l["type"] == "transfer")
    fare_info = calculate_fare(legs)
    return {
        "total_minutes":  round(total_min),
        "transfers":      transfers,
        "fare":           fare_info,
        "legs":           legs,
        "station_names":  station_names,
        "station_coords": station_coords,
        "label":          label,
    }


# ── API routes ───────────────────────────────────────────────
@app.route("/api/find-path", methods=["POST"])
def find_path():
    body        = request.json
    from_lat    = float(body["from_lat"])
    from_lon    = float(body["from_lon"])
    to_lat      = float(body["to_lat"])
    to_lon      = float(body["to_lon"])
    hour           = int(body.get("hour", 12))
    minute         = int(body.get("minute", 0))
    max_walk       = float(body.get("max_walk_km", 10.0))
    mode           = body.get("mode", "fastest")
    walk_speed     = float(body.get("walk_speed_kmh", 5.0))
    excluded_lines = set(body.get("excluded_lines", []))

    transfer_mult = 10.0 if mode == "fewest_transfers" else 1.0

    def _open_stations(lat, lon, radius):
        return [(d, s) for d, s in nearest_stations(lat, lon, radius)
                if not is_station_closed(s)]

    # Destination walk radius: small so A* only targets nearby stations and
    # the final walk is short.  max_walk is kept for the start side (to
    # locate the user's nearest boarding station) and for fallback expansion.
    DEST_WALK_KM = 1.5

    starts = _open_stations(from_lat, from_lon, max_walk)
    ends   = _open_stations(to_lat,   to_lon,   DEST_WALK_KM)

    if not starts:
        return jsonify({"error": "Không có ga metro nào (đang mở) trong bán kính đi bộ từ điểm xuất phát."}), 400
    if not ends:
        return jsonify({"error": "Không có ga metro nào (đang mở) trong bán kính đi bộ từ điểm đến."}), 400

    start_dist, start_sid = starts[0]
    waits = get_wait_times(hour)

    # Enter graph at every non-excluded line at the start station
    start_walk_min = start_dist / walk_speed * 60
    start_nodes = [
        (start_walk_min + waits.get(lid, 5), f"{start_sid}:{lid}")
        for lid in STATION_LINES.get(start_sid, [])
        if lid not in excluded_lines
    ]
    if not start_nodes:
        return jsonify({"error": "Tất cả tuyến tại ga xuất phát đều bị loại trừ."}), 400

    # ── Primary path (multi-goal: nearby destination stations only) ──
    graph     = build_graph(hour, transfer_mult, excluded_lines)
    goal_sids = {s for _, s in ends}
    path, total_min_g = astar(graph, start_nodes, goal_sids, to_lat, to_lon)

    # ── Fallback: find the nearest reachable station beyond DEST_WALK_KM ──
    # Multi-goal A* would pick the station cheapest to REACH, not nearest to
    # the destination – so we iterate ext_ends nearest→farthest and stop at
    # the first station A* can actually reach.  This minimises the final walk.
    fallback_walk_km = 0.0
    orig_end_sids = {s for _, s in ends}   # already proven unreachable
    for factor in (3, 6):
        if path is not None:
            break
        ext_ends = _open_stations(to_lat, to_lon, DEST_WALK_KM * factor)
        for d_km, sid in ext_ends:
            if sid in orig_end_sids:
                continue   # skip – already unreachable in primary run
            trial, trial_g = astar(graph, start_nodes, {sid}, to_lat, to_lon)
            if trial is not None:
                path, total_min_g = trial, trial_g
                fallback_walk_km = round(d_km, 2)
                break

    if path is None:
        return jsonify({"error": "Không tìm được đường đi (mạng lưới bị gián đoạn hoặc quá nhiều tuyến bị loại trừ)."}), 404

    primary_label = "Nhanh nhất" if mode == "fastest" else "Ít đổi tàu"
    primary = _build_route(path, total_min_g, from_lat, from_lon,
                           to_lat, to_lon, walk_speed, waits, primary_label,
                           dep_hour=hour, dep_minute=minute)

    # ── Alternative path (opposite optimisation) ──────────────
    alt_mult  = 10.0 if mode == "fastest" else 1.0
    alt_graph = build_graph(hour, alt_mult, excluded_lines)
    alt_path, alt_total_g = astar(alt_graph, start_nodes, goal_sids, to_lat, to_lon)

    alternative = None
    if alt_path:
        if ([n.split(":")[0] for n in alt_path]
                != [n.split(":")[0] for n in path]):
            alt_label = "Ít đổi tàu" if mode == "fastest" else "Nhanh nhất"
            alternative = _build_route(alt_path, alt_total_g, from_lat, from_lon,
                                       to_lat, to_lon, walk_speed, waits, alt_label,
                                       dep_hour=hour, dep_minute=minute)

    return jsonify({**primary, "alternative": alternative,
                    "fallback_walk_km": fallback_walk_km})


@app.route("/api/schedule-periods")
def api_schedule_periods():
    return jsonify([
        {"startHour": p["startHour"], "endHour": p["endHour"]}
        for p in SCHEDULES
    ])

@app.route("/api/stations")
def api_stations():
    return jsonify(DATA["stations"])

@app.route("/api/lines")
def api_lines():
    return jsonify(DATA["lines"])

@app.route("/api/blocked")
def api_blocked():
    return jsonify(blocked_list())

@app.route("/api/block", methods=["POST"])
def api_block():
    b = request.json
    key = (b["lineId"], b["fromStation"], b["toStation"])
    BLOCKED[key] = b.get("reason", "").strip()
    return jsonify({"ok": True})

@app.route("/api/unblock", methods=["POST"])
def api_unblock():
    b = request.json
    BLOCKED.pop((b["lineId"], b["fromStation"], b["toStation"]), None)
    BLOCKED.pop((b["lineId"], b["toStation"],   b["fromStation"]), None)
    return jsonify({"ok": True})

@app.route("/api/blocked-stations")
def api_blocked_stations():
    return jsonify(blocked_stations_list())

@app.route("/api/block-station", methods=["POST"])
def api_block_station():
    b = request.json
    sid = b.get("stationId", "").strip()
    if not sid or sid not in STATIONS:
        return jsonify({"error": "Ga không hợp lệ"}), 400
    BLOCKED_STATIONS[sid] = b.get("reason", "").strip()
    return jsonify({"ok": True})

@app.route("/api/unblock-station", methods=["POST"])
def api_unblock_station():
    b = request.json
    BLOCKED_STATIONS.pop(b.get("stationId", ""), None)
    return jsonify({"ok": True})

@app.route("/data/railways.geojson")
def railways():
    return jsonify(RAILWAYS_GEOJSON)

@app.route("/data/stations.geojson")
def stations_geo():
    return jsonify(STATIONS_GEOJSON)

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

if __name__ == "__main__":
    print("Bangkok Metro Pathfinder  →  http://localhost:5000")
    app.run(debug=True, port=5000)
