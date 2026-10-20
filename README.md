# NYC Subway Journey Planner

Unofficial trip planner for the New York City subway: every route in the five boroughs, including
the shuttles, the rush-hour diamond expresses and the Staten Island Railway. It's the New York
counterpart of the Tokyo Subway, London Tube and Namma Metro planners, in the same single-file format.

Enter where you're starting (GPS, an address, a ZIP code, a landmark or a station) and pick a
destination station. The planner:

- Plans with the service running right now: weekday rush hours, middays, evenings, Saturday,
  Sunday or late night, so it knows the 5 ends at Bowling Green at weekends, the B and W run
  weekdays only, and late-night trains make local stops
- Names every route that will get you there ("Take the 2 or 3"), the platform as the station signs
  it ("Uptown & The Bronx", "Manhattan", "Coney Island"), and whether that's the express or the local
- Warns about branch trains to let pass ("Not A trains to Ozone Park–Lefferts Blvd") and says where
  to change when no through train runs (the late-night Lefferts shuttle at Rockaway Blvd, the
  Rockaway Park Shuttle at Broad Channel)
- Explains each change: across the platform at express stops, through big complexes like
  Times Sq–42 St or Fulton St, and the two free out-of-system transfers
- Links the Staten Island Railway to Manhattan with the free Staten Island Ferry
- Shows total time, arrival time (New York time) and the fare: $3.00 for any subway trip, with the
  weekly cap

## Run

It's a static page. Serve the folder and open `NYCSubwayPlanner.html`:

```bash
python3 -m http.server 4621
```

GPS only works on `localhost` or HTTPS.

## Address search

Without a key, place search uses OpenStreetMap (Nominatim). For Google Places results,
copy `config.example.js` to `config.js` and add a Maps Platform key with Places API (New)
and Maps JavaScript API enabled. `config.js` is gitignored.

## Deployment

`netlify.toml` publishes the planner as `index.html` and writes `config.js` from the
`GOOGLE_MAPS_API_KEY` environment variable when it's set; without it the site uses
OpenStreetMap search.

## Network data

The data block in the HTML (between `BEGIN GENERATED` and `END GENERATED`) is written by
`tools/build_stations.py` from MTA New York City Transit's open data:

```bash
python3 tools/build_stations.py
```

- **Subway GTFS feed** (`rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip`): every distinct stop
  pattern each route runs, how often it runs in each time band, timetabled stop-to-stop times,
  MTA's minimum transfer times, and track shapes (used to scale straight-line distances).
- **MTA Subway Stations table** (data.ny.gov): station complexes and the direction labels on
  each platform.

Unlike the Tokyo and London planners, which take station positions from OpenStreetMap, this one uses
MTA's GTFS: it is the official source and carries what OpenStreetMap doesn't (stop patterns by
time of day, timetables and transfer times).

A pattern counts as regular service in a band if it averages at least a train an hour there (0.6
in the rush band, for peak-direction services like the Z); rarer and shorter workings are kept only
to warn about trains that go elsewhere. Waits are half the time between trains of all the patterns
that would do. Each run uses the service calendars valid on the build date, so rebuild when MTA
publishes a new feed (the current one runs to 31 October 2026). Use `--gtfs feed.zip --stations
stations.csv` to rebuild from saved copies.

## Fares

In `METRO.fares` at the top of the script, checked against mta.info (October 2026):

- $3.00 per ride (since 4 January 2026), reduced fare $1.50, paid by tapping a contactless card,
  phone or OMNY card. MetroCards can no longer be bought or refilled.
- Weekly cap: $35 in any 7 days (12 paid rides; then free), $17.50 reduced.
- Free changes between trains; one free transfer to or from a local bus within 2 hours.
- Free out-of-system transfers: Lexington Av/59 St ⇄ Lexington Av/63 St, Junius St ⇄ Livonia Av.
- Staten Island Railway fares are collected only at St George and Tompkinsville; the ferry is free.

## Limitations

- Regular timetable only: planned work (common at weekends and late at night) isn't included, and
  holidays are treated as normal days.
- Not covered: buses, PATH, LIRR, Metro-North, NYC Ferry and the JFK AirTrain.
- Times are timetabled times plus average waits, so they're estimates. Always follow the signs in
  the station.
