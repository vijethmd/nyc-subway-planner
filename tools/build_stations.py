#!/usr/bin/env python3
"""Build the network dataset embedded in NYCSubwayPlanner.html.

Downloads MTA New York City Transit's official open data, the static subway GTFS feed and the
"MTA Subway Stations" table, and derives everything from it:

- stations: one per MTA station complex (Times Sq-42 St with 42 St-Port Authority, Fulton St…),
  keeping each platform group's own position and platform direction labels
  ("Uptown & The Bronx", "Manhattan"…)
- service patterns: every distinct stop sequence a route runs, with how often it runs in each
  time band (weekday rush, midday, evening, Saturday, Sunday, late night) and its timetabled
  stop-to-stop times
- transfers: in-station connections and the two free out-of-system transfers, with MTA's
  minimum transfer times

    python3 tools/build_stations.py                         # download, rewrite the data block in place
    python3 tools/build_stations.py --gtfs feed.zip --stations stations.csv   # reuse saved copies
"""
import csv, datetime, io, json, math, os, re, statistics, subprocess, sys, urllib.request, zipfile
from collections import Counter, defaultdict

GTFS_URL = 'https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip'
STATIONS_URL = 'https://data.ny.gov/resource/39hk-dx4f.csv?$limit=2000'

# Time bands. A pattern's frequency in each band is measured over the band's core hours (minutes
# after midnight on the GTFS service day), away from the changeovers; the planner picks the band
# for the current New York time (weekday rush 6:00–9:30 and 15:30–19:30, midday, evening from
# 19:30, Saturday, Sunday, and late night from midnight to 6:00).
BANDS = [
  ('rush', ['Weekday'], [(390, 540), (990, 1110)]),     # 6:30–9:00, 16:30–18:30
  ('midday', ['Weekday'], [(600, 900)]),                # 10:00–15:00
  ('evening', ['Weekday'], [(1230, 1410)]),             # 20:30–23:30
  ('sat', ['Saturday'], [(480, 1320)]),                 # 8:00–22:00
  ('sun', ['Sunday'], [(480, 1320)]),
  ('night', ['Weekday', 'Saturday', 'Sunday'], [(60, 300)]),  # 1:00–5:00, every night
]
# A pattern is part of a band's regular service if it averages at least a train an hour in
# that band (or 0.6 in the rush band, where peak-direction services such as the Z run in only one
# of the two peaks); anything rarer only feeds the "not trains to…" platform advice.
REGULAR_PER_HOUR = {'rush': 0.6}
ADVICE_MIN_TRIPS = 2

# Diamond bullets: peak-direction express variants.
DIAMOND = {'6X': '6', '7X': '7', 'FX': 'F'}
# Labelled on the network map even when zoomed out: hubs far enough apart not to collide.
# (MTA complex ids: Times Sq, 14 St-Union Sq, Fulton St, Atlantic Av-Barclays Ctr, 161 St-Yankee
# Stadium, Jamaica Center, Flushing-Main St, Coney Island, Jackson Hts, 125 St (A B C D), St George.)
MAJOR = {'611', '602', '628', '617', '604', '278', '447', '58', '616', '153', '501'}

# Complexes better known by another order of their parts.
NAMES = {'624': ['World Trade Center', 'Chambers St', 'Park Place', 'Cortlandt St']}

def fetch(url):
  try:
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'nyc-subway-planner'}), timeout=180).read()
  except Exception as e:
    if 'CERTIFICATE_VERIFY_FAILED' not in str(e): raise
    # Some Python installs lack CA certificates; curl uses the system store.
    return subprocess.run(['curl', '-sfL', '-m', '180', '-A', 'nyc-subway-planner', url], capture_output=True, check=True).stdout

def hav(a, b):
  R = 6371; la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
  x = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
  return 2 * R * math.asin(math.sqrt(x))

def dash(name):
  # MTA signs separate the parts of a station name with an en dash: "Times Sq–42 St".
  return re.sub(r'(?<=\S)-(?=\S)', '–', name)

def minutes(t):
  h, m, s = map(int, t.split(':'))
  return h * 60 + m + s / 60

def main():
  arg = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
  zbytes = open(arg('--gtfs'), 'rb').read() if arg('--gtfs') else fetch(GTFS_URL)
  sbytes = open(arg('--stations'), 'rb').read() if arg('--stations') else fetch(STATIONS_URL)
  z = zipfile.ZipFile(io.BytesIO(zbytes))
  table = lambda n: list(csv.DictReader(io.TextIOWrapper(z.open(n), encoding='utf-8-sig')))
  feed = table('feed_info.txt')[0]
  routes = {r['route_id']: r for r in table('routes.txt')}
  gstops = {r['stop_id']: r for r in table('stops.txt')}
  trips = {r['trip_id']: r for r in table('trips.txt')}
  info = {r['gtfs_stop_id']: r for r in csv.DictReader(io.StringIO(sbytes.decode('utf-8-sig')))}

  # ── Service patterns ──
  # Each day type uses every service calendar valid on the build date (kept within the feed's
  # dates): the base Weekday/Saturday/Sunday timetables plus date-ranged ones such as the
  # Rockaway Park Shuttle's weekend service.
  today = datetime.date.today().strftime('%Y%m%d')
  ref = min(max(today, feed['feed_start_date']), feed['feed_end_date'])
  day_of = {}
  for c in table('calendar.txt'):
    if not c['start_date'] <= ref <= c['end_date']: continue
    day_of[c['service_id']] = 'Weekday' if c['monday'] == '1' else 'Saturday' if c['saturday'] == '1' else 'Sunday'
  print(f'// service calendars on {ref}: ' + ', '.join(sorted(day_of)), file=sys.stderr)
  times = defaultdict(list)
  for r in table('stop_times.txt'):
    times[r['trip_id']].append((int(r['stop_sequence']), r['stop_id'], r['arrival_time'], r['departure_time']))
  pats = {}
  for tid, st in times.items():
    t = trips[tid]
    day = day_of.get(t['service_id'])
    if not day: continue
    st.sort()
    stops = tuple(s[1][:-1] for s in st)
    key = (t['route_id'], st[0][1][-1], stops)       # direction from the platform id: N or S
    p = pats.setdefault(key, {'n': Counter(), 'hops': [[] for _ in stops[1:]], 'shape': Counter(), 'day': Counter()})
    dep = minutes(st[0][3]) % 1440
    for name, days, wins in BANDS:
      if day in days and any(a <= dep < b for a, b in wins): p['n'][name] += 1
    p['day'][day] += 1
    p['shape'][t['shape_id']] += 1
    # Each hop runs from one departure to the next (so dwell time counts), ending on arrival.
    for i in range(1, len(st)):
      p['hops'][i - 1].append(minutes(st[i][3] if i < len(st) - 1 else st[i][2]) - minutes(st[i - 1][3]))

  hours = {name: sum(b - a for a, b in wins) / 60 * len(days) for name, days, wins in BANDS}
  def regular(p):
    return [p['n'][name] >= REGULAR_PER_HOUR.get(name, 1.0) * hours[name] for name, *_ in BANDS]
  # A short working (a contiguous part of another pattern of the same route that runs whenever
  # it does) adds nothing to routing; it is kept for platform advice only.
  def contains(big, small):
    n = len(small)
    return any(big[i:i + n] == small for i in range(len(big) - n + 1))
  keys = list(pats)
  routing, advice = [], []
  for k in keys:
    p = pats[k]; reg = regular(p)
    if not any(reg):
      if max(p['n'].values(), default=0) >= ADVICE_MIN_TRIPS: advice.append(k)
      continue
    covered = any(k2 != k and k2[:2] == k[:2] and len(k2[2]) > len(k[2]) and contains(k2[2], k[2])
                  and all(r2 or not r1 for r1, r2 in zip(reg, regular(pats[k2]))) for k2 in keys)
    (advice if covered else routing).append(k)

  # ── Stations: one per complex ──
  used = {s for k in routing + advice for s in k[2]}
  complexes = defaultdict(list)
  for sid in sorted(used):
    complexes[info[sid]['complex_id']].append(sid)
  # Route letters per platform group, from the patterns that serve it.
  serving = defaultdict(set)
  for k in routing: [serving[s].add(DIAMOND.get(k[0], k[0])) for s in k[2]]

  # Distances: straight lines scaled per route by the GTFS track shapes' length.
  shapes = defaultdict(list)
  for r in table('shapes.txt'):
    shapes[r['shape_id']].append((int(r['shape_pt_sequence']), float(r['shape_pt_lat']), float(r['shape_pt_lon'])))
  pos = {s: (float(gstops[s]['stop_lat']), float(gstops[s]['stop_lon'])) for s in used}
  kroute = defaultdict(list)
  for k in routing:
    sh = shapes.get(pats[k]['shape'].most_common(1)[0][0])
    if not sh: continue
    sh.sort(); L = sum(hav(a[1:], b[1:]) for a, b in zip(sh, sh[1:]))
    S = sum(hav(pos[a], pos[b]) for a, b in zip(k[2], k[2][1:]))
    if S > 0.5 and 1 <= L / S < 1.6: kroute[k[0]].append(L / S)
  kr = {r: round(statistics.median(v), 3) for r, v in kroute.items()}

  # ── Output ──
  out = ['  // ' + f"MTA GTFS feed {feed.get('feed_version', '')}, valid {feed.get('feed_start_date')}–{feed.get('feed_end_date')}, timetables as of {ref}"]
  out.append('  bands:' + json.dumps([b[0] for b in BANDS]) + ',')
  out.append('  routes:[')
  order = sorted({k[0] for k in routing}, key=lambda r: int(routes[r]['route_sort_order'] or 99))
  for r in order:
    ro = routes[r]
    rec = {'id': r, 'bullet': DIAMOND.get(r, ro['route_short_name']), 'name': ro['route_long_name'], 'color': '#' + ro['route_color'],
           'tc': '#' + (ro['route_text_color'] or 'FFFFFF'), 'k': kr.get(r, 1.1)}
    if r in DIAMOND: rec['diamond'] = 1
    out.append('    ' + json.dumps(rec, separators=(',', ':')) + ',')
  out.append('  ],')
  out.append('  // Stop sequences in travel order; d = N/S platform side; t = timetabled minutes per hop;')
  out.append('  // w = average wait per band where the pattern is regular service (0 = not running);')
  out.append('  // a = 1 for patterns kept only for platform advice, with b = bands they run in.')
  out.append('  patterns:[')
  n_by_route = Counter()
  def emit(k, adv):
    p = pats[k]; n_by_route[k[0]] += 1
    rec = {'id': f'{k[0]}{k[1]}{n_by_route[k[0]]}', 'r': k[0], 'd': k[1], 'seq': list(k[2])}
    if adv:
      rec['a'] = 1
      rec['b'] = sum(1 << i for i, (name, *_) in enumerate(BANDS) if p['n'][name] >= ADVICE_MIN_TRIPS)
    else:
      rec['t'] = [round(statistics.median(h), 1) for h in p['hops']]
      rec['w'] = [round(min(15, max(1.5, 30 * hours[name] / p['n'][name])), 1) if reg else 0
                  for (name, *_), reg in zip(BANDS, regular(p))]
    out.append('    ' + json.dumps(rec, separators=(',', ':')) + ',')
  for k in sorted(routing, key=lambda k: (order.index(k[0]), k[1], -len(k[2]))): emit(k, False)
  for k in sorted(advice, key=lambda k: (k[0], k[1], -len(k[2]))):
    if k[0] in order: emit(k, True)
  out.append('  ],')
  out.append('  // Platform groups: [name, lat, lng, north label, south label, complex id].')
  out.append('  stops:{')
  for sid in sorted(used):
    i = info[sid]
    out.append(f'    {json.dumps(sid)}:' + json.dumps([dash(i['stop_name']), round(pos[sid][0], 6), round(pos[sid][1], 6),
                 i['north_direction_label'], i['south_direction_label'], i['complex_id']], ensure_ascii=False, separators=(',', ':')) + ',')
  out.append('  },')
  out.append('  // MTA minimum transfer times in seconds between platform groups; "osi" marks the two free')
  out.append('  // out-of-system transfers (tap again at the turnstile).')
  tr = []
  for r in table('transfers.txt'):
    a, b = r['from_stop_id'], r['to_stop_id']
    if a in used and b in used and a != b:
      osi = info[a]['complex_id'] != info[b]['complex_id']
      tr.append([a, b, int(r['min_transfer_time'] or 180)] + (['osi'] if osi else []))
  out.append('  transfers:' + json.dumps(tr, separators=(',', ':')) + ',')
  ends = {k[2][-1] for k in routing} | {k[2][0] for k in routing}
  out.append('  stations:[')
  for cid, members in sorted(complexes.items(), key=lambda kv: dash(info[kv[1][0]]['stop_name'])):
    # Name the complex after its parts, the busiest first: "Times Sq–42 St / 42 St–Port Authority Bus Terminal".
    members.sort(key=lambda s: -len(serving[s]))
    names = NAMES.get(cid) or list(dict.fromkeys(dash(info[s]['stop_name']) for s in members))
    lat = sum(pos[s][0] for s in members) / len(members); lng = sum(pos[s][1] for s in members) / len(members)
    rec = {'id': cid, 'name': ' / '.join(names), 'lat': round(lat, 6), 'lng': round(lng, 6), 'stops': members,
           'boro': info[members[0]]['borough']}
    if cid in MAJOR: rec['major'] = 1
    if any(s in ends for s in members): rec['terminal'] = 1
    spread = max(hav(pos[a], pos[b]) for a in members for b in members)
    if spread > 0.35: print(f'// wide complex {rec["name"]}: {spread * 1000:.0f} m', file=sys.stderr)
    out.append('    ' + json.dumps(rec, ensure_ascii=False, separators=(',', ':')) + ',')
  out.append('  ]')
  print(f'// {len(routing)} routing patterns, {len(advice)} advice-only, {len(complexes)} stations, {len(tr)} transfers', file=sys.stderr)
  print('// k per route: ' + ' '.join(f'{r}={v}' for r, v in sorted(kr.items())), file=sys.stderr)

  html = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'NYCSubwayPlanner.html')
  src = open(html, encoding='utf-8').read()
  begin, end = '  // BEGIN GENERATED by tools/build_stations.py\n', '  // END GENERATED\n'
  a, b = src.index(begin) + len(begin), src.index(end)
  open(html, 'w', encoding='utf-8').write(src[:a] + '\n'.join(out) + '\n' + src[b:])
  print(f'// written to {html}', file=sys.stderr)

main()
