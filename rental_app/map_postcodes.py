"""Resolve the supplied postcode lists to London boroughs and council-tax bands.

Run from this folder: python map_postcodes.py

Reads postcodes.csv, "postcodes (1).csv" and "postcodes (2).csv" from the workspace,
looks up each postcode's local authority with postcodes.io, joins the annual Band A-H
figures from the supplied borough table, and writes:

  "<source> - boroughs.csv"        next to each supplied list
  rental_app/postcode_boroughs.csv one combined lookup used by the app

The three supplied files and the tax table are never modified. Lookups are cached in
postcode_boroughs.json, so a re-run only asks about postcodes it has not seen.
Postcodes the lookup cannot place (for example non-geographic PO box districts) fall
back to the nearest mapped postcode within a kilometre and say so in `Match`.
"""
import csv
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
SOURCES = [WORKSPACE / 'postcodes.csv', WORKSPACE / 'postcodes (1).csv', WORKSPACE / 'postcodes (2).csv']
TAX_FILE = WORKSPACE / 'Borough-BandA-BandB-BandC-BandD-BandE-BandF-BandG-.csv'
COMBINED = ROOT / 'postcode_boroughs.csv'
CACHE = ROOT / 'postcode_boroughs.json'
BANDS = 'ABCDEFGH'
ENDPOINT = 'https://api.postcodes.io/postcodes'
COLUMNS = ['UNIT', 'LAT', 'LNG', 'Borough', 'Match'] + ['Band ' + b for b in BANDS]


def normalise(postcode):
    return ' '.join(str(postcode or '').upper().split())


def load_tax():
    with TAX_FILE.open(encoding='utf-8-sig', newline='') as f:
        return {row['Borough']: {b: row['Band ' + b] for b in BANDS} for row in csv.DictReader(f)}


def load_rows():
    """Every supplied row, in file order, with duplicates kept so each file stays complete."""
    rows = []
    for path in SOURCES:
        with path.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                try:
                    rows.append({'unit': normalise(row['UNIT']), 'lat': float(row['LAT']), 'lng': float(row['LNG']), 'source': path})
                except (KeyError, TypeError, ValueError):
                    continue
    return rows


def lookup(codes):
    """postcodes.io accepts at most 100 postcodes per request; failures retry, then stay unknown."""
    cache = json.loads(CACHE.read_text(encoding='utf-8')) if CACHE.exists() else {}
    pending = [c for c in dict.fromkeys(codes) if c not in cache]
    for start in range(0, len(pending), 100):
        batch = pending[start:start + 100]
        body = json.dumps({'postcodes': batch}).encode()
        for attempt in range(3):
            try:
                request = urllib.request.Request(ENDPOINT, data=body, headers={'Content-Type': 'application/json', 'User-Agent': 'TwoKeysRentalSearch/1.0'})
                with urllib.request.urlopen(request, timeout=30) as response:
                    results = json.load(response).get('result') or []
                for item in results:
                    found = item.get('result') or {}
                    cache[normalise(item.get('query'))] = found.get('admin_district')
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == 2:
                    print(f'Lookup failed for {len(batch)} postcodes: {exc}', flush=True)
                else:
                    time.sleep(2 * (attempt + 1))
        CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True), encoding='utf-8')
        print(f'Resolved {min(start + 100, len(pending))}/{len(pending)} new postcodes', flush=True)
        time.sleep(0.2)
    return cache


def distance_m(a, b):
    dx = (a['lng'] - b['lng']) * 111_320 * math.cos(math.radians(a['lat']))
    dy = (a['lat'] - b['lat']) * 110_574
    return math.hypot(dx, dy)


def nearest_borough(row, resolved):
    """Non-geographic postcodes still carry coordinates, so borrow the closest placed one."""
    best = min(resolved, key=lambda other: distance_m(row, other), default=None)
    if best is None or distance_m(row, best) > 1000:
        return None, 'No borough found'
    return best['borough'], f'Nearest mapped postcode ({best["unit"]})'


def enrich(rows, cache, tax):
    resolved = [{'unit': r['unit'], 'lat': r['lat'], 'lng': r['lng'], 'borough': cache[r['unit']]}
                for r in rows if cache.get(r['unit']) in tax]
    placed = {r['unit']: (r['borough'], 'Direct match') for r in resolved}
    output = []
    for row in rows:
        borough, match = placed.get(row['unit']) or (None, None)
        if borough is None:
            borough = cache.get(row['unit'])
            match = 'Borough not in the supplied tax table' if borough else None
        if borough is None:
            borough, match = nearest_borough(row, resolved)
        output.append({**row, 'borough': borough, 'match': match or 'No borough found',
                       'bands': tax.get(borough, {})})
    return output


def write(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({'UNIT': row['unit'], 'LAT': row['lat'], 'LNG': row['lng'], 'Borough': row['borough'] or '',
                             'Match': row['match'], **{'Band ' + b: row['bands'].get(b, '') for b in BANDS}})


def summarise(name, rows, tax):
    counts = {b: sum(1 for r in rows if r['borough'] == b) for b in tax}
    unmatched = sum(1 for r in rows if not r['borough'])
    borrowed = sum(1 for r in rows if r['match'].startswith('Nearest'))
    print(f'{name}: {len(rows)} rows, {sum(1 for c in counts.values() if c)} boroughs, {borrowed} placed by nearest postcode, {unmatched} unmatched')
    for borough, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        if count: print(f'  {borough}: {count}')


def main():
    tax = load_tax()
    rows = load_rows()
    cache = lookup([r['unit'] for r in rows])
    enriched = enrich(rows, cache, tax)
    for path in SOURCES:
        subset = [row for row in enriched if row['source'] == path]
        target = path.with_name(path.stem + ' - boroughs.csv')
        write(target, subset)
        summarise(target.name, subset, tax)
    write(COMBINED, enriched)
    known = sum(1 for row in enriched if row['borough'])
    print(f'Combined {COMBINED.name}: {len(enriched)} rows, {known} with a borough, {len(enriched) - known} unmatched')


if __name__ == '__main__':
    main()
