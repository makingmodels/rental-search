"""A private, local rental shortlist. Run with: python app.py"""
import csv
import gzip
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
DB = Path(os.environ.get('DATA_DIR') or ROOT) / 'rentals.sqlite3'  # DATA_DIR points at the host's persistent disk


def load_local_credentials():
    env_file = ROOT / '.env'
    if not env_file.exists(): return
    for line in env_file.read_text(encoding='utf-8').splitlines():
        key, separator, value = line.partition('=')
        if separator and key.strip() in ('TFL_APP_KEY', 'EPC_API_TOKEN', 'ANTHROPIC_API_KEY', 'FLAT_PASSWORD'):
            os.environ.setdefault(key.strip(), value.strip())


load_local_credentials()
PASSWORD = os.environ.get('FLAT_PASSWORD', '')
if not PASSWORD: raise SystemExit('Set FLAT_PASSWORD in rental_app/.env or the host environment before starting.')
TFL_KEY = os.environ.get('TFL_APP_KEY', '')
EPC_TOKEN = os.environ.get('EPC_API_TOKEN', '')
DESTINATIONS = {'work': 'E14 5HX', 'ucl': 'WC1E 6BT'}
ENERGY = {'A': [70, 110], 'B': [90, 130], 'C': [110, 160], 'D': [135, 195], 'E': [170, 250], 'F': [220, 320], 'G': [280, 400]}
WATER_AND_WIFI = 70  # fixed monthly allowance for water and broadband
FLOORPLAN_MODEL = 'claude-haiku-5-5'  # cheapest vision model: reading room sizes off a plan is simple extraction
DETAIL_VERSION = 3  # bump when detail parsing improves; older stored listings are re-read on the next scan
DEFAULT_CRITERIA = {'max_price': 3000, 'beds': 2, 'max_work_minutes': 30, 'max_station_walk': 15,
                    'require_furnished': True, 'exclude_basement': True, 'require_living_room': True,
                    'require_floor_level': True, 'require_floorplan': True, 'require_in_area': True}
NUMBER_LIMITS = {'max_price': (500, 10000), 'beds': (1, 5), 'max_work_minutes': (5, 120), 'max_station_walk': (1, 60)}

# The bundled adapters are reused without modifying the original project.
config = types.ModuleType('config')
config.PHOTO_SIZE = 'size656x437'
sys.modules['config'] = config


def configure_search(price, beds):
    """Portal search URLs follow the editable criteria (the adapters read config at call time)."""
    config.SEARCH_URL = f'https://www.rightmove.co.uk/property-to-rent/find.html?locationIdentifier=STATION%5E1724&radius=5.0&minBedrooms={beds}&maxBedrooms={beds}&maxPrice={price}&sortType=6'
    config.OTM_SEARCH_URL = f'https://www.onthemarket.com/to-rent/{beds}-bed-property/canary-wharf/?radius=5.0&max-price={price}&sort-field=update_date'
    config.ZOOPLA_SEARCH_URL = f'https://www.zoopla.co.uk/to-rent/property/canary-wharf/?beds_min={beds}&beds_max={beds}&price_max={price}&price_frequency=per_month&radius=5&results_sort=newest_listings'
    config.OPENRENT_SEARCH_URL = f'https://www.openrent.co.uk/properties-to-rent/canary-wharf?term=Canary%20Wharf&prices_max={price}&bedrooms_min={beds}&bedrooms_max={beds}&within=5'
    config.SEARCH = {'centre': (51.5042, -0.0203), 'radius_miles': 5, 'min_price': 0, 'max_price': price, 'min_beds': beds, 'max_beds': beds}


configure_search(3000, 2)
sys.path.insert(0, str(WORKSPACE / 'flat-hunter-main' / 'flat-hunter-main'))
import sources
from extract import extract, kinds
from ranking import score as rank


def db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init():
    with db() as con:
        con.executescript('''CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY, data TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, previous_price REAL, saved INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS likes (listing TEXT NOT NULL, username TEXT NOT NULL, PRIMARY KEY (listing, username));
        CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, listing TEXT NOT NULL, username TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS binned (listing TEXT PRIMARY KEY, username TEXT NOT NULL, created TEXT NOT NULL);''')


SEED_FILE = ROOT / 'seed.json.gz'
SEED_TABLES = ('listings', 'meta', 'likes', 'notes', 'binned')  # sessions are never exported


def export_seed(path=SEED_FILE):
    with db() as con: tables = {t: [dict(row) for row in con.execute(f'SELECT * FROM {t}')] for t in SEED_TABLES}
    with gzip.open(path, 'wt', encoding='utf-8') as f: json.dump(tables, f)
    return {t: len(rows) for t, rows in tables.items()}


def import_seed(path=SEED_FILE):
    """Load a seed into an empty database (a fresh host disk); returns how many listings it added."""
    if not Path(path).exists(): return 0
    with db() as con:
        if con.execute('SELECT COUNT(*) FROM listings').fetchone()[0]: return 0
        with gzip.open(path, 'rt', encoding='utf-8') as f: tables = json.load(f)
        for table in SEED_TABLES:
            for row in tables.get(table, []):
                con.execute(f'INSERT OR REPLACE INTO {table} ({", ".join(row)}) VALUES ({", ".join("?" * len(row))})', tuple(row.values()))
    return len(tables.get('listings', []))


def marks():
    """Likes, notes and binned flags per listing id; shared by both accounts."""
    with db() as con:
        likes = con.execute('SELECT listing, username FROM likes ORDER BY username').fetchall()
        notes = con.execute('SELECT listing, username, body, created FROM notes ORDER BY created').fetchall()
        binned = {row['listing'] for row in con.execute('SELECT listing FROM binned')}
    liked = {}
    for row in likes: liked.setdefault(row['listing'], []).append(row['username'])
    noted = {}
    for row in notes: noted.setdefault(row['listing'], []).append({'user': row['username'], 'body': row['body'], 'created': row['created']})
    return liked, noted, binned


def listing_ids(data):
    """Every portal id behind one shown listing, so a like or bin covers all of its duplicates."""
    ids = data.get('ids') or [data.get('id')]
    if not isinstance(ids, list) or not 0 < len(ids) <= 10 or not all(isinstance(i, str) and 0 < len(i) <= 80 for i in ids): raise ValueError('Invalid listing')
    return ids


def parse_tax():
    with (WORKSPACE / 'Borough-BandA-BandB-BandC-BandD-BandE-BandF-BandG-.csv').open(encoding='utf-8-sig', newline='') as f:
        return {row['Borough']: {b: float(row['Band ' + b].replace('£', '').replace(',', '')) for b in 'ABCDEFGH'} for row in csv.DictReader(f)}


TAX = parse_tax()
AREAS_FILE = ROOT / 'postcode_boroughs.csv'
AREA_RADIUS_M = 400
POSTCODE_RE = re.compile(r'\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b')


def build_areas():
    """The chosen postcode areas, mapped to their borough and indexed on a coarse grid."""
    areas, grid = {}, {}
    with AREAS_FILE.open(encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if not row.get('Borough'): continue
            unit = ' '.join(row['UNIT'].upper().split())
            lat, lon = float(row['LAT']), float(row['LNG'])
            areas[unit] = {'borough': row['Borough'], 'lat': lat, 'lon': lon}
            grid.setdefault((int(lat * 50), int(lon * 50)), []).append(unit)
    return areas, grid


AREAS, AREA_GRID = build_areas()
AREA_DISTRICTS = {unit.split()[0] for unit in AREAS}


def metres(lat1, lon1, lat2, lon2):
    return math.hypot((lon1 - lon2) * 111_320 * math.cos(math.radians(lat1)), (lat1 - lat2) * 110_574)


def nearest_area(lat, lon):
    """Closest chosen postcode to a listing pin: a 7x7 grid window, then every area if far off."""
    try: lat, lon = float(lat), float(lon)
    except (TypeError, ValueError): return None
    row, column = int(lat * 50), int(lon * 50)
    best, units = None, []
    for dr in range(-3, 4):
        for dc in range(-3, 4):
            units.extend(AREA_GRID.get((row + dr, column + dc), ()))
    for unit in units or AREAS:
        area = AREAS[unit]
        distance = metres(lat, lon, area['lat'], area['lon'])
        if best is None or distance < best['distance_m']:
            best = {'postcode': unit, 'borough': area['borough'], 'distance_m': distance}
    return best


LOCATED = {}


def locate(item):
    """Match a listing to the chosen areas: its full postcode first, then its map pin."""
    cache_key = tuple(item.get(field) for field in ('address', 'title', 'lat', 'lon', 'pin_exact'))
    if cache_key in LOCATED: return LOCATED[cache_key]
    text = ' '.join(str(item.get(field) or '') for field in ('address', 'title')).upper()
    found = POSTCODE_RE.search(text)
    postcode = f'{found.group(1)} {found.group(2)}' if found else None
    if postcode in AREAS:
        location = {'postcode': postcode, 'borough': AREAS[postcode]['borough'], 'in_area': True, 'basis': 'postcode', 'nearest': postcode, 'distance_m': 0}
        LOCATED[cache_key] = location
        return location
    if postcode and postcode.split()[0] not in AREA_DISTRICTS:
        # A full postcode outside the chosen districts settles it, whatever the pin says.
        location = {'postcode': postcode, 'borough': None, 'in_area': False, 'basis': 'postcode', 'nearest': None, 'distance_m': None}
        LOCATED[cache_key] = location
        return location
    nearest = nearest_area(item.get('lat'), item.get('lon'))
    result = {'postcode': postcode, 'borough': nearest['borough'] if nearest else None, 'nearest': nearest['postcode'] if nearest else None,
              'distance_m': round(nearest['distance_m']) if nearest else None}
    if nearest and nearest['distance_m'] <= AREA_RADIUS_M: location = {**result, 'in_area': True, 'basis': 'pin'}
    # A portal's approximate pin cannot disqualify a flat at the edge of a chosen area.
    elif nearest and nearest['distance_m'] <= 1500 and item.get('pin_exact') is False: location = {**result, 'in_area': None, 'basis': 'approximate pin'}
    else: location = {**result, 'in_area': False, 'basis': 'pin'} if nearest else {**result, 'in_area': None, 'basis': None}
    if len(LOCATED) > 5000: LOCATED.clear()
    LOCATED[cache_key] = location
    return location


CHOSEN_BOROUGHS = sorted({area['borough'] for area in AREAS.values()} & set(TAX))


def median(values):
    values = sorted(values)
    middle = len(values) // 2
    return (values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2) if values else None


def costs(item, facts=None):
    """Rough all-in monthly cost. Unknown council tax band or EPC is assumed to be D, and an unknown borough
    uses the median of your chosen boroughs, so there is always an approximate figure."""
    facts = facts or extract(item)
    borough = item.get('borough') or (item.get('location') or {}).get('borough')
    band, epc = facts['tax_band'] or 'D', facts['epc'] or 'D'
    annual = TAX[borough].get(band) if borough in TAX else median([TAX[b][band] for b in CHOSEN_BOROUGHS if TAX[b].get(band)])
    monthly = annual / 12 if annual is not None else None
    bills_included = 'bills_included' in facts['signals']
    energy = [0, 0] if bills_included else ENERGY[epc]
    fixed = 0 if bills_included else WATER_AND_WIFI
    assumed = [label for label, known in (('council tax band D', facts['tax_band']), ('EPC D', facts['epc']), ('median borough council tax', borough in TAX)) if not known]
    total = None
    if item.get('price') is not None and monthly is not None:
        total = [round(item['price'] + monthly + energy[0] + fixed), round(item['price'] + monthly + energy[1] + fixed)]
    return {'council_monthly': monthly, 'energy': energy, 'water_wifi': fixed, 'bills_included': bills_included, 'total': total,
            'band': band, 'epc': epc, 'borough': borough if borough in TAX else None, 'assumed': assumed}


def criteria():
    with db() as con:
        row = con.execute("SELECT value FROM meta WHERE key='criteria'").fetchone()
    return {**DEFAULT_CRITERIA, **(json.loads(row['value']) if row else {})}


def clean_criteria(data):
    """Validate a criteria update from the browser; unknown keys are ignored."""
    result = {}
    for key, default in DEFAULT_CRITERIA.items():
        if key not in data: continue
        if isinstance(default, bool):
            if not isinstance(data[key], bool): raise ValueError(f'{key} must be true or false')
            result[key] = data[key]
        else:
            value = int(data[key])
            low, high = NUMBER_LIMITS[key]
            if not low <= value <= high: raise ValueError(f'{key} must be between {low} and {high}')
            result[key] = value
    return {**criteria(), **result}


BASEMENT_RE = re.compile(r'\b(?:basement|lower[ -]ground|semi[ -]basement)\s+(?:floor\s+)?(?:flat|apartment|maisonette)|\b(?:on|occupies|located on|situated on|set on)\s+the\s+(?:basement|lower[ -]ground)', re.I)
FLOOR_RE = re.compile(r'\b(?:ground|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|top|\d+(?:st|nd|rd|th))\s+floor\b', re.I)
SHARE_RE = re.compile(r'\b(?:house|flat)\s?share\b|\broom (?:to rent|in a shared)\b|\bdouble room in\b', re.I)
LIVING_RE = re.compile(r'\b(?:living room|reception room|lounge|living area|reception area|open.plan living|sitting room)\b', re.I)


def assess(item, rules=None, facts=None):
    """Known deal-breakers exclude a flat; a missing fact only marks it for checking."""
    rules, facts = rules or DEFAULT_CRITERIA, facts or extract(item)
    price, beds = item.get('price'), item.get('beds')
    text = ' '.join([item.get('description') or '', ' '.join(item.get('features') or [])])
    missing, excluded = [], []
    if price is None: missing.append('Rent not confirmed')
    elif price > rules['max_price']: excluded.append(f"Over £{rules['max_price']:,} pcm")
    elif price < rules['max_price'] * 0.55: missing.append('Rent looks unusually low: check it is the whole flat, per month')
    if SHARE_RE.search(' '.join([item.get('title') or '', text])): excluded.append('Room in a shared house, not a whole flat')
    if beds is None: missing.append('Bedroom count not confirmed')
    elif int(beds) != rules['beds']: excluded.append(f"Not a {rules['beds']}-bedroom flat")
    basement = rules['exclude_basement'] and BASEMENT_RE.search(text)
    if basement: excluded.append('Basement or lower ground stated')
    elif rules['require_floor_level'] and not FLOOR_RE.search(text) and not (item.get('floorplan_analysis') or {}).get('floor_level'):
        missing.append('Floor level not confirmed')
    if rules['require_living_room'] and facts['separate_living_room'] is not True:
        if facts['separate_living_room'] is False: excluded.append('Floorplan shows no separate living room')
        elif not LIVING_RE.search(text): missing.append('Living room not confirmed')
    if rules['require_floorplan'] and not item.get('floorplans'): missing.append('Floorplan missing')
    if rules['require_furnished']:
        if facts['furnishing'] == 'unfurnished': excluded.append('Unfurnished')
        elif facts['furnishing'] == 'part furnished': missing.append('Only part furnished: check what is included')
        elif facts['furnishing'] is None: missing.append('Furnishing not stated')
    work = item.get('work_minutes')
    if work is None: missing.append('Revolut journey not checked')
    # An approximate pin can be a few hundred metres out (about five minutes' walk), so only a near miss needs checking.
    elif not item.get('pin_exact') and work >= rules['max_work_minutes'] - 5: missing.append('Commute is close to the limit and uses an approximate pin')
    elif work >= rules['max_work_minutes']: excluded.append(f"Revolut commute is {rules['max_work_minutes']} minutes or more")
    walk = item.get('station_walk')
    if walk is None: missing.append('Station walking time not confirmed')
    elif walk > rules['max_station_walk']: excluded.append(f"Station walk is over {rules['max_station_walk']} minutes")
    if rules['require_in_area']:
        location = item.get('location') or locate(item)
        if location['in_area'] is False: excluded.append('Outside your chosen postcode areas')
        elif location['in_area'] is None: missing.append('Postcode area not confirmed')
    ranked = rank(item, facts, rules['max_price'])
    return {'status': 'excluded' if excluded else 'uncertain' if missing else 'match', 'missing': missing, 'excluded': excluded,
            'score': ranked['score'], 'terms': ranked['terms'], 'highlights': ranked['highlights']}


def same_flat(a, b):
    """Same rent and pins within ~150 m: the same flat advertised on another portal."""
    try:
        return a.get('price') is not None and a.get('price') == b.get('price') and abs(float(a['lat']) - float(b['lat'])) < 0.0014 and abs(float(a['lon']) - float(b['lon'])) < 0.0022
    except (KeyError, TypeError, ValueError):
        return False


def merge_duplicates(items):
    """Collapse cross-portal duplicates into one listing that links every source; the most complete record leads."""
    groups = []
    for item in sorted(items, key=lambda x: (not x['saved'], len(x['assessment']['missing']), -x['assessment']['score'])):
        group = next((g for g in groups if same_flat(g[0], item)), None)
        if group: group.append(item)
        else: groups.append([item])
    result = []
    for lead, *others in groups:
        group = [lead, *others]
        result.append({**lead, 'also': [{'id': o['id'], 'label': o.get('source_label') or o.get('source'), 'url': o.get('url')} for o in others],
                       'saved': any(g['saved'] for g in group), 'binned': any(g.get('binned') for g in group),
                       'liked_by': sorted({u for g in group for u in g.get('liked_by', [])}),
                       'notes': sorted((n for g in group for n in g.get('notes', [])), key=lambda n: n['created'])})
    return result


def meta_value(key):
    with db() as con: row = con.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else None


def rows(merge=True):
    with db() as con:
        records = con.execute('SELECT * FROM listings').fetchall()
    rules = criteria()
    liked, noted, binned = marks()
    result = []
    for row in records:
        item = json.loads(row['data'])
        item.update(id=row['id'], first_seen=row['first_seen'], last_seen=row['last_seen'], previous_price=row['previous_price'],
                    saved=bool(row['saved'] or liked.get(row['id'])), liked_by=liked.get(row['id'], []), notes=noted.get(row['id'], []), binned=row['id'] in binned)
        location = locate(item)
        item['location'] = location
        if not item.get('borough') and location['in_area'] and location['borough']:
            item.update(borough=location['borough'], borough_source='postcode list')
        elif item.get('borough') and not item.get('borough_source'):
            item['borough_source'] = 'entered'
        facts = extract(item)
        sqm = facts['sqm']
        item.update(extracted={**{k: v for k, v in facts.items() if k != 'signals'}, **kinds(facts['signals']),
                               'price_per_sqm': round(item['price'] / sqm, 1) if sqm and item.get('price') else None},
                    assessment=assess(item, rules, facts), costs=costs(item, facts), council_bands=TAX.get(item.get('borough') or '', {}))
        result.append(item)
    if merge: result = merge_duplicates(result)
    return sorted(result, key=lambda x: (-x['assessment']['score'], x['price'] or 999999))


def save_listing(item):
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    key = str(item['key'])
    with db() as con:
        old = con.execute('SELECT * FROM listings WHERE id=?', (key,)).fetchone()
        previous = old['previous_price'] if old else None
        if old:
            existing = json.loads(old['data'])
            old_price = existing.get('price')
            for field in ('borough', 'tax_band', 'epc', 'work_minutes', 'ucl_minutes', 'station_walk', 'journeys', 'floorplan_analysis'):
                if existing.get(field) is not None and item.get(field) is None: item[field] = existing[field]
            if old_price is not None and item.get('price') is not None and item['price'] < old_price: previous = old_price
        con.execute('INSERT INTO listings (id,data,first_seen,last_seen,previous_price) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,last_seen=excluded.last_seen,previous_price=excluded.previous_price',
                    (key, json.dumps(item, default=str), old['first_seen'] if old else now, now, previous))


scan_lock = threading.Lock()
scan_status = {'running': False, 'message': 'No search run yet.', 'sources': {}}
SCAN_PAGES = 4      # search pages read per portal (about 24 listings each); OpenRent counts its own chunks
DETAIL_LIMIT = 80   # new listing pages fetched per scan, so one scan stays a few minutes


def refresh_card(card):
    """Update a listing already stored from its search card, without refetching its page."""
    key = str(card['key'])
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with db() as con:
        row = con.execute('SELECT data, previous_price FROM listings WHERE id=?', (key,)).fetchone()
        if not row: return False
        item = json.loads(row['data'])
        previous = row['previous_price']
        if card.get('price') is not None and item.get('price') is not None and card['price'] < item['price']: previous = item['price']
        for field in ('price', 'listed', 'touched', 'address'):
            value = card.get(field)
            if value is not None: item[field] = value.isoformat() if hasattr(value, 'isoformat') else value
        con.execute('UPDATE listings SET data=?, last_seen=?, previous_price=? WHERE id=?', (json.dumps(item, default=str), now, previous, key))
    return True


def skip_detail(card):
    """A search-page card can already rule a listing out, saving its detail request."""
    return locate({'address': card.get('address'), 'lat': card.get('lat'), 'lon': card.get('lon'), 'pin_exact': True})['in_area'] is False


class OnTheMarket(sources.OnTheMarket):
    def pages(self, max_pages):
        for page in range(1, max_pages + 1):
            html = sources.browser_get(self.search_url() + (f'&page={page}' if page > 1 else ''))
            results = sources.next_data(html)['props']['pageProps']['initialReduxState']['results']
            listings = [self.card(p) for p in results['list'] if p.get('id') and p.get('details-url')]
            if not listings: return
            yield listings
            if page >= ((results.get('paginationControls') or {}).get('lastPage') or 99): return
            time.sleep(self.delay)


    def detail(self, card):
        detail, html = with_page(lambda: super(OnTheMarket, self).detail(card))
        prop = sources.next_data(html)['props']['pageProps']['initialReduxState']['property'] if html else {}
        return {**detail, 'sizings': detail.get('sizings') or otm_sizings(prop)}


class Rightmove(sources.Rightmove):
    """Adds what the shared adapter drops: council tax band, bathrooms and furnishing from the same page model."""
    def detail(self, card):
        captured, original = {}, sources.rightmove_page_model
        def capture(html):
            captured['model'] = original(html)
            return captured['model']
        sources.rightmove_page_model = capture  # safe: scans hold scan_lock, so only one detail fetch runs at a time
        try: detail = super().detail(card)
        finally: sources.rightmove_page_model = original
        page = (captured.get('model') or {}).get('propertyData') or {}
        facts = list(detail.get('facts') or [])
        band = (page.get('livingCosts') or {}).get('councilTaxBand')
        if band: facts.append(f'Council tax band: {band}')
        furnish = (detail.get('lettings') or {}).get('furnishType')
        if furnish: facts.append(f'Furnishing: {furnish}')
        return {**detail, 'facts': facts, 'bathrooms': page.get('bathrooms')}


def otm_sizings(prop):
    """OnTheMarket states floor area in its page model but the shared adapter drops it."""
    low, high = prop.get('minimumAreaSqM'), prop.get('maximumAreaSqM')
    return [{'unit': 'sqm', 'minimumSize': low or high, 'maximumSize': high or low}] if low or high else []


ZOOPLA_SIZE = re.compile(r'ingested[^{]{0,6}\{[^}]{0,8}sizeSqft[^0-9]{1,6}(\d+)')  # page data may be backslash-escaped


def zoopla_sizings(html):
    """Zoopla's sizeSqft is its own estimate (source 'comparables_epc'), so it is marked as one."""
    found = ZOOPLA_SIZE.search(html)
    return [{'unit': 'sqft', 'maximumSize': int(found.group(1)), 'estimate': True}] if found else []


def with_page(detail_call):
    """Run an adapter's detail() while keeping the raw page it fetched."""
    page, original = {}, sources.browser_get
    def keep(*args, **kwargs):
        page['html'] = original(*args, **kwargs)
        return page['html']
    sources.browser_get = keep  # safe: scans hold scan_lock, so only one detail fetch runs at a time
    try: return detail_call(), page.get('html', '')
    finally: sources.browser_get = original


class Zoopla(sources.Zoopla):
    def detail(self, card):
        detail, html = with_page(lambda: super(Zoopla, self).detail(card))
        return {**detail, 'sizings': detail.get('sizings') or zoopla_sizings(html)}


def scan():
    if not scan_lock.acquire(blocking=False): return
    scan_status.update(running=True, message='Searching the portals…', sources={})
    previous = meta_value('last_scan')  # listings first seen after the previous search count as new finds
    if previous:
        with db() as con: con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('new_since', previous))
    try:
        rules = criteria()
        configure_search(rules['max_price'], rules['beds'])
        with db() as con:
            versions = {row['id']: row['version'] for row in con.execute("SELECT id, json_extract(data, '$.detail_version') AS version FROM listings")}
        known = set(versions)
        fetched = 0
        for source in (Rightmove(), OnTheMarket(), Zoopla(), sources.OpenRent()):
            try:
                count = refreshed = reread = errors = cards_seen = pages_read = 0
                for page, cards in enumerate(source.pages(SCAN_PAGES), 1):
                    pages_read = page
                    scan_status['message'] = f'{source.label}: reading page {page}, {count} new listings so far…'
                    for card in cards:
                        cards_seen += 1
                        if card.get('price') is not None and card['price'] > rules['max_price']: continue
                        if card.get('beds') is not None and str(card['beds']) != str(rules['beds']): continue
                        if not source.in_area(card): continue
                        key = str(card.get('key'))
                        stale = key in versions and (versions[key] or 0) < DETAIL_VERSION
                        if key in known and not (stale and fetched < DETAIL_LIMIT):
                            if refresh_card(card): refreshed += 1
                            continue
                        if skip_detail(card) or fetched >= DETAIL_LIMIT: continue
                        try:
                            detail = source.detail(card)
                            item = {**card, **detail, 'source_label': source.label, 'detail_version': DETAIL_VERSION}
                            # Search cards normalise weekly rents to pcm; some detail pages (OnTheMarket) return the raw weekly figure.
                            if card.get('price') is not None: item['price'] = card['price']
                            for field in ('listed', 'touched'):
                                if hasattr(item.get(field), 'isoformat'): item[field] = item[field].isoformat()
                            # EPC, council tax, size and furnishing are read from the stored page by extract.py when listed.
                            save_listing(item)
                            if key in known: reread += 1
                            else: count += 1
                            known.add(key)
                            versions[key] = DETAIL_VERSION
                            fetched += 1
                        except Exception as exc:
                            errors += 1
                            print(f'{source.label} listing failed: {type(exc).__name__}: {exc}', flush=True)
                        time.sleep(source.delay)
                note = f'{count} new listings from {cards_seen} cards over {pages_read} search page' + ('s' if pages_read != 1 else '')
                if refreshed: note += f'; {refreshed} known listings refreshed'
                if reread: note += f'; {reread} older listings re-read for size, EPC and council tax'
                if errors: note += f'; {errors} detail errors'
                if fetched >= DETAIL_LIMIT: note += f'; new-listing limit of {DETAIL_LIMIT} reached, the rest wait for the next scan'
                scan_status['sources'][source.label] = note
            except Exception as exc:
                scan_status['sources'][source.label] = f'Unavailable: {type(exc).__name__}: {str(exc)[:100]}'
                print(f'{source.label} search failed: {type(exc).__name__}: {exc}', flush=True)
        scan_status['message'] = 'Checking TfL commutes…'
        check_commutes()
        scan_status['message'] = f'Search finished. Read up to {SCAN_PAGES} search pages per portal, at most {DETAIL_LIMIT} new listing pages per run; individual sites may block requests.'
        with db() as con: con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('last_scan', dt.datetime.now(dt.timezone.utc).isoformat()))
    finally:
        scan_status['running'] = False
        scan_lock.release()


def request_json(url, headers=None):
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'FlatSearchPersonal/1.0', **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def search_epc(postcode):
    url = 'https://api.get-energy-performance-data.communities.gov.uk/api/domestic/search?' + urllib.parse.urlencode({'postcode': postcode, 'page_size': 100})
    try:
        return request_json(url, {'Authorization': 'Bearer ' + EPC_TOKEN}).get('data', [])
    except urllib.error.HTTPError as exc:
        if exc.code == 404: return []
        raise


def journey(item, destination):
    if item.get('lat') is None or item.get('lon') is None: raise ValueError('Listing has no map coordinates')
    tomorrow = dt.date.today() + dt.timedelta(days=1)
    while tomorrow.weekday() >= 5: tomorrow += dt.timedelta(days=1)
    params = {'date': tomorrow.strftime('%Y%m%d'), 'time': '0830', 'timeIs': 'Departing', 'mode': 'tube,dlr,overground,elizabeth-line,national-rail,bus,walking'}
    if TFL_KEY: params['app_key'] = TFL_KEY
    frm = f"{item['lat']},{item['lon']}"
    url = 'https://api.tfl.gov.uk/Journey/JourneyResults/' + urllib.parse.quote(frm, safe=',') + '/to/' + urllib.parse.quote(destination) + '?' + urllib.parse.urlencode(params)
    data = request_json(url)
    journeys = [j for j in data.get('journeys', []) if any((leg.get('mode') or {}).get('id') != 'walking' for leg in j.get('legs', []))]
    if not journeys: raise ValueError('TfL returned no public transport journeys')
    best = min(journeys, key=lambda j: j['duration'])
    legs = [{'summary': re.sub('<[^>]*>', '', (leg.get('instruction') or {}).get('summary', 'Travel')), 'duration': leg.get('duration'), 'mode': (leg.get('mode') or {}).get('id'), 'path': (leg.get('path') or {}).get('lineString')} for leg in best.get('legs', [])]
    first_transit = next((leg for leg in legs if leg['mode'] != 'walking'), None)
    station_walk = (legs[0].get('duration') if legs and legs[0]['mode'] == 'walking' else 0) if first_transit and first_transit['mode'] in ('tube', 'dlr', 'overground', 'elizabeth-line', 'national-rail') else None
    return {'minutes': best['duration'], 'legs': legs, 'station_walk': station_walk, 'date': tomorrow.isoformat(), 'time': '08:30'}


def commute_changes(item):
    routes, errors = {}, {}
    for name, destination in DESTINATIONS.items():
        try: routes[name] = journey(item, destination)
        except (ValueError, urllib.error.URLError, TimeoutError, KeyError) as exc: errors[name] = str(exc)
    changes = {'journeys': routes} if routes else {}
    if 'work' in routes: changes.update(work_minutes=routes['work']['minutes'], station_walk=routes['work']['station_walk'])
    if 'ucl' in routes: changes['ucl_minutes'] = routes['ucl']['minutes']
    return routes, errors, changes


def check_commutes(limit=150):
    """Fill in TfL commutes for listings that have none, so ranking does not wait on manual clicks."""
    pending = [x for x in rows(merge=False) if not x.get('journeys') and x.get('lat') is not None and x['assessment']['status'] != 'excluded'][:limit]
    done = 0
    for item in pending:
        scan_status['message'] = f'Checking TfL commutes… {done}/{len(pending)}'
        routes, errors, changes = commute_changes(item)
        if changes: update_item(item['id'], changes)
        elif errors: print(f"TfL failed for {item['id']}: {errors}", flush=True)
        done += 1
        time.sleep(0.3)  # shortcut: fixed pacing keeps well under TfL's 500/min keyed limit; add backoff if 429s appear.


FLOORPLAN_PROMPT = '''This is a UK rental floorplan. Reply with one JSON object and nothing else:
{"total_sqm": number or null, "second_bedroom_sqm": number or null, "separate_living_room": true/false/null,
 "bathrooms": integer or null, "floor_level": string or null, "summary": "one plain sentence on the layout"}
Use the stated total area if printed (convert sq ft to m² by dividing by 10.764); otherwise null. Never guess.'''


def fetch_image(url):
    if not url.startswith('https://'): raise ValueError('Floorplan link is not https')
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=20) as response:
        kind = response.headers.get_content_type()
        data = response.read(5_000_001)
    if kind not in ('image/jpeg', 'image/png', 'image/gif', 'image/webp'): raise ValueError('Floorplan is not an image the reader accepts')
    if len(data) > 5_000_000: raise ValueError('Floorplan image is larger than 5 MB')
    return kind, data


def read_floorplan(url):
    """One small vision call, only when someone presses the button; the answer is stored so it is never paid for twice."""
    try: import anthropic
    except ImportError: raise ValueError('Floorplan reading needs the Anthropic SDK: run "pip install anthropic".')
    if not os.environ.get('ANTHROPIC_API_KEY'): raise ValueError('Floorplan reading needs ANTHROPIC_API_KEY in rental_app/.env.')
    kind, data = fetch_image(url)
    import base64
    try:
        response = anthropic.Anthropic().messages.create(
            model=FLOORPLAN_MODEL, max_tokens=1024, output_config={'effort': 'low'},
            messages=[{'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': kind, 'data': base64.standard_b64encode(data).decode()}},
                {'type': 'text', 'text': FLOORPLAN_PROMPT}]}])
    except anthropic.APIStatusError as exc: raise ValueError(f'Floorplan reader error {exc.status_code}: {exc.message}')
    except anthropic.APIConnectionError: raise ValueError('Could not reach the floorplan reader. Check the connection.')
    if response.stop_reason == 'refusal': raise ValueError('The floorplan reader declined this image.')
    text = next((block.text for block in response.content if block.type == 'text'), '')
    found = re.search(r'\{.*\}', text, re.S)
    if not found: raise ValueError('The floorplan reader returned no figures.')
    raw = json.loads(found.group(0))
    number = lambda v: float(v) if isinstance(v, (int, float)) and v > 0 else None
    return {'total_sqm': number(raw.get('total_sqm')), 'second_bedroom_sqm': number(raw.get('second_bedroom_sqm')),
            'separate_living_room': raw.get('separate_living_room') if isinstance(raw.get('separate_living_room'), bool) else None,
            'bathrooms': int(raw['bathrooms']) if isinstance(raw.get('bathrooms'), int) else None,
            'floor_level': str(raw['floor_level'])[:40] if raw.get('floor_level') else None,
            'summary': str(raw.get('summary') or '')[:300], 'model': FLOORPLAN_MODEL, 'url': url}


def update_item(identifier, changes):
    with db() as con:
        row = con.execute('SELECT data FROM listings WHERE id=?', (identifier,)).fetchone()
        if not row: raise ValueError('Listing not found')
        item = json.loads(row['data'])
        item.update(changes)
        con.execute('UPDATE listings SET data=? WHERE id=?', (json.dumps(item), identifier))
    return item


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args), flush=True)

    def send(self, status, payload, content_type='application/json; charset=utf-8', headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def user(self):
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie', ''))
        except Exception: return None
        token = cookie.get('flat_session')
        if not token: return None
        with db() as con:
            row = con.execute('SELECT username FROM sessions WHERE token=? AND expires>?', (hashlib.sha256(token.value.encode()).hexdigest(), time.time())).fetchone()
        return row['username'] if row else None

    def body(self):
        length = int(self.headers.get('Content-Length', '0'))
        if length > 16384: raise ValueError('Request too large')
        return json.loads(self.rfile.read(length) or b'{}')

    def client(self):
        # Behind Render's proxy the last X-Forwarded-For entry is the address the proxy saw; the rest can be forged.
        forwarded = [part.strip() for part in self.headers.get('X-Forwarded-For', '').split(',') if part.strip()]
        return forwarded[-1] if forwarded and os.environ.get('RENDER_EXTERNAL_URL') else self.client_address[0]

    def same_origin(self):
        origin = self.headers.get('Origin')
        return not origin or origin in trusted_origins()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/me': return self.send(200, {'user': self.user()})
        static = {'/': ('public/index.html', 'text/html; charset=utf-8'), '/app.js': ('public/app.js', 'application/javascript; charset=utf-8'), '/style.css': ('public/style.css', 'text/css; charset=utf-8'),
                  '/animation.json': ('public/animation.json', 'application/json'), '/handshake.json': ('public/handshake.json', 'application/json'),
                  '/avatar-mansa.svg': ('public/avatar-mansa.svg', 'image/svg+xml'), '/avatar-ash.svg': ('public/avatar-ash.svg', 'image/svg+xml')}
        if path in static:
            filename, kind = static[path]
            return self.send(200, (ROOT / filename).read_bytes(), kind)
        if not self.user(): return self.send(401, {'error': 'Sign in required'})
        if path == '/api/listings': return self.send(200, {'listings': rows(), 'scan': scan_status, 'boroughs': list(TAX), 'epc_configured': bool(EPC_TOKEN),
                                                          'criteria': criteria(), 'last_scan': meta_value('last_scan'), 'new_since': meta_value('new_since'), 'floorplan_reader': bool(os.environ.get('ANTHROPIC_API_KEY')),
                                                          'areas': {'postcodes': len(AREAS), 'boroughs': sorted({area['borough'] for area in AREAS.values()})}})
        self.send(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.same_origin(): return self.send(403, {'error': 'Invalid origin'})
        path = urllib.parse.urlsplit(self.path).path
        try:
            data = self.body()
            if path == '/api/login':
                username = str(data.get('username', '')).strip().lower()
                password = str(data.get('password', ''))
                address = self.client()
                if LOGINS.blocked(address): return self.send(429, {'error': 'Too many attempts. Try again in 15 minutes.'})
                if username not in ('mansa', 'ash') or not hmac.compare_digest(password.encode(), PASSWORD.encode()):
                    LOGINS.fail(address)
                    return self.send(401, {'error': 'Incorrect username or password'})
                token = secrets.token_urlsafe(32)
                with db() as con:
                    con.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
                    con.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), username, time.time() + 86400 * 7))
                return self.send(200, {'user': username}, headers={'Set-Cookie': cookie(token, 604800)})
            if not self.user(): return self.send(401, {'error': 'Sign in required'})
            if path == '/api/logout':
                jar = SimpleCookie(self.headers.get('Cookie', ''))
                if jar.get('flat_session'):
                    with db() as con: con.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(jar['flat_session'].value.encode()).hexdigest(),))
                return self.send(200, {'ok': True}, headers={'Set-Cookie': cookie('', 0)})
            if path == '/api/scan':
                if not scan_status['running']: threading.Thread(target=scan, daemon=True).start()
                return self.send(200, scan_status)
            if path == '/api/criteria':
                rules = clean_criteria(data)
                with db() as con: con.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('criteria', json.dumps(rules)))
                return self.send(200, {'criteria': rules})
            identifier = str(data.get('id', ''))
            if path == '/api/floorplan':
                with db() as con: row = con.execute('SELECT data FROM listings WHERE id=?', (identifier,)).fetchone()
                if not row: raise ValueError('Listing not found')
                item = json.loads(row['data'])
                plans = [url for url in item.get('floorplans') or [] if url.startswith('https://')]
                if not plans: raise ValueError('This advert has no floorplan')
                if data.get('read') and not item.get('floorplan_analysis'):
                    item = update_item(identifier, {'floorplan_analysis': read_floorplan(plans[0])})
                return self.send(200, {'floorplans': plans, 'analysis': item.get('floorplan_analysis')})
            user = self.user()
            if path == '/api/save':  # toggles this user's like; the shortlist holds anything either of you liked
                ids = listing_ids(data)
                with db() as con:
                    for i in ids:
                        if data.get('saved'): con.execute('INSERT OR IGNORE INTO likes VALUES (?,?)', (i, user))
                        else: con.execute('DELETE FROM likes WHERE listing=? AND username=?', (i, user))
                        others = con.execute('SELECT 1 FROM likes WHERE listing=?', (i,)).fetchone()
                        con.execute('UPDATE listings SET saved=? WHERE id=?', (1 if others else 0, i))
                return self.send(200, {'ok': True})
            if path == '/api/bin':
                ids = listing_ids(data)
                now = dt.datetime.now(dt.timezone.utc).isoformat()
                with db() as con:
                    for i in ids:
                        if data.get('binned'): con.execute('INSERT OR REPLACE INTO binned VALUES (?,?,?)', (i, user, now))
                        else: con.execute('DELETE FROM binned WHERE listing=?', (i,))
                return self.send(200, {'ok': True})
            if path == '/api/note':
                body = str(data.get('body', '')).strip()
                if not 0 < len(body) <= 1000: raise ValueError('Write a note of up to 1,000 characters')
                note = {'user': user, 'body': body, 'created': dt.datetime.now(dt.timezone.utc).isoformat()}
                with db() as con: con.execute('INSERT INTO notes (listing, username, body, created) VALUES (?,?,?,?)', (listing_ids(data)[0], user, body, note['created']))
                return self.send(200, {'note': note})
            if path == '/api/facts':
                borough, band, epc = data.get('borough'), data.get('tax_band'), data.get('epc')
                if borough and borough not in TAX or band and band not in 'ABCDEFGH' or epc and epc not in ENERGY: raise ValueError('Invalid cost details')
                item = update_item(identifier, {'borough': borough or None, 'tax_band': band or None, 'epc': epc or None, 'borough_source': 'entered' if borough else None})
                return self.send(200, {'costs': costs(item), 'borough': item.get('borough'), 'tax_band': item.get('tax_band'), 'council_bands': TAX.get(item.get('borough') or '', {})})
            if path == '/api/journey':
                item = next((x for x in rows(merge=False) if x['id'] == identifier), None)
                if not item: raise ValueError('Listing not found')
                routes, errors, changes = commute_changes(item)
                if changes: update_item(identifier, changes)
                return self.send(200, {'journeys': routes, 'errors': errors})
            if path == '/api/epc':
                if not EPC_TOKEN: return self.send(400, {'error': 'Set EPC_API_TOKEN to use the government EPC API.'})
                postcode = str(data.get('postcode', '')).strip()
                if not re.fullmatch(r'[A-Za-z0-9 ]{5,8}', postcode): raise ValueError('Enter a full postcode')
                return self.send(200, {'results': search_epc(postcode)})
            self.send(404, {'error': 'Not found'})
        except (ValueError, TypeError, json.JSONDecodeError) as exc: self.send(400, {'error': str(exc)})
        except (urllib.error.URLError, TimeoutError) as exc: self.send(502, {'error': f'External service unavailable: {exc}'})


class AttemptLimiter:
    """Failed sign-ins per address inside a sliding window; in memory, so a restart clears it."""
    def __init__(self, limit=5, window=15 * 60):
        self.limit, self.window, self.failures, self.lock = limit, window, {}, threading.Lock()

    def recent(self, key, now):
        return [t for t in self.failures.get(key, []) if now - t < self.window]

    def blocked(self, key, now=None):
        with self.lock: return len(self.recent(key, now or time.time())) >= self.limit

    def fail(self, key, now=None):
        now = now or time.time()
        with self.lock: self.failures = {**self.failures, key: [*self.recent(key, now), now]}


LOGINS = AttemptLimiter()


def trusted_origins():
    hosted = os.environ.get('RENDER_EXTERNAL_URL')  # Render sets this to the service's public https address
    return {'http://127.0.0.1:8765', 'http://localhost:8765', *([hosted.rstrip('/')] if hosted else [])}


def cookie(token, max_age):
    secure = '; Secure' if os.environ.get('RENDER_EXTERNAL_URL', '').startswith('https://') else ''
    return f'flat_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}'


def scheduled_scans():
    while True:
        scan()
        time.sleep(6 * 60 * 60)


if __name__ == '__main__':
    init()
    if '--export-seed' in sys.argv: raise SystemExit(print('Seed written:', export_seed()))
    if added := import_seed(): print(f'Loaded {added} listings from the seed', flush=True)
    threading.Thread(target=scheduled_scans, daemon=True).start()
    host, port = os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8765'))
    print(f'Flat search running at http://{host}:{port}', flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
