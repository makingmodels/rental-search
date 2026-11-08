# Two Keys

Private local rental search for Mansa and Ash. Run from this folder:

```bash
python app.py
```

Open http://127.0.0.1:8765. Sign in as `mansa` or `ash` using the shared password supplied by the owners. Locally the server binds to localhost only. Both accounts share the same SQLite database on this computer; it is not synced to other devices. The supplied short password is suitable only for a private local prototype, not an internet-facing deployment. Set `FLAT_PASSWORD` in the process environment to change it. Use a long `FLAT_PASSWORD` before deploying.

The app reuses the supplied `flat-hunter-main` portal adapters. They need Python's `curl_cffi` package for OnTheMarket, Zoopla and OpenRent; Rightmove uses the standard library. The app searches once on startup and every six hours while running, or when the search button is pressed. Each run reads up to `SCAN_PAGES` search pages per portal (four by default, about 96 cards per portal), refreshes listings it already has straight from the search card without refetching their pages, skips cards whose pin is clearly outside your postcode areas before requesting any detail page, and fetches at most `DETAIL_LIMIT` new listing pages per run (80 by default). Both constants are at the top of `scan()`'s section in `app.py`; raise them for wider coverage at the cost of a longer scan. A full first run takes a few minutes; later runs are quicker because known listings only need a card refresh. Portal layouts, terms and access restrictions change; individual failures are shown in Search status. Check the portals' terms before using the scraper. The default search is a five-mile radius around Canary Wharf, not all of London, and the postcode files then narrow it to the areas you chose. A portal being blocked is not evidence that it has no listings.

TfL calls use the Unified API. Set `TFL_APP_KEY` in the process environment or in the local `.env` file; TfL recommends registration at https://api.tfl.gov.uk/. Routes are checked when opening a listing and pressing the TfL button; the default is the next weekday at 08:30 to Revolut's YY London, E14 5HX, and UCL Bloomsbury, WC1E 6BT. Verify that these are the right destinations. Approximate listing pins and routes cannot establish eligibility. The 15-minute station walk threshold is an assumption. TfL's first transit stop may be a bus stop, so a route does not necessarily establish a walkable rail station.

EPC search uses the official government API. Set `EPC_API_TOKEN` in the process environment or in the local `.env` file after registering at https://get-energy-performance-data.communities.gov.uk/. The `.env` file is ignored by Git; keep it private. Process environment values take precedence, and the server must be restarted after changes. A postcode can return multiple properties; select the matching certificate yourself and enter its rating. Council and council-tax band are entered after verification; the supplied CSV is used as an annual table, with no claim about its financial year or discounts. Energy ranges are rough illustrative monthly assumptions for a typical two-person flat, not tariffs or forecasts. A total is shown only when rent, borough, tax band and EPC rating are known; it excludes water, broadband and other costs.

The three supplied postcode lists (`postcodes.csv`, `postcodes (1).csv`, `postcodes (2).csv`) define the areas you would live in. `python map_postcodes.py` resolves every postcode's local authority with postcodes.io (cached in `postcode_boroughs.json`) and writes an enriched copy beside each original, `"<name> - boroughs.csv"`, adding the borough and the annual Band A–H figures from the supplied tax table. The combined result, `postcode_boroughs.csv`, is what the app reads; the originals are never modified. Re-run the script after adding postcodes; only new ones are looked up.

A listing is placed in your areas by its full postcode when the advert gives one, otherwise by its map pin within 400 m of one of your chosen postcodes. Its borough then comes from that postcode, so council tax and all bands are filled in automatically, and the app shows the full Band A–H table for the borough. A full postcode from an unwelcome district, or a pin more than 400 m from every chosen postcode, excludes the listing as outside your areas; an approximate portal pin near the edge stays in Needs checking rather than being excluded. Portal pins and addresses can be wrong, so confirm the postcode before acting on a cost.

Listings are rejected only when a disqualifying fact is explicitly known. Floor area is no longer collected or shown: most portals omit it, so it is not part of the assessment or ranking. Unknown floor level, living room, floorplan, station walking time or commute remains in Needs checking. The heuristic ranking favours lower rent, space, shorter Revolut commutes and, secondarily, shorter UCL commutes. Listing dates and price changes are not guaranteed portal history; price drops are tracked only after the app has observed the listing. Estate status and aesthetics require a human review; no image model is configured. Source content can be inaccurate. No enquiries are sent.

The animation uses the supplied `Apartment construction.json`. The browser loads Lottie, Leaflet, OpenStreetMap tiles and Google Fonts from external CDNs; their availability affects animation, map or fonts, not server data. `rentals.sqlite3` contains personal shortlist data; back it up privately and do not commit it.

Tests: `python -m unittest test_app`.

## Deploy (Render)

`render.yaml` at the repository root defines one always-on web service with a 1 GB disk for the database.

1. Push this folder to a private GitHub repository. `.gitignore` keeps `.env` and every `*.sqlite3` file out.
2. In Render choose New, then Blueprint, and pick the repository.
3. When asked, enter `FLAT_PASSWORD` (use a new long one), `TFL_APP_KEY` and `EPC_API_TOKEN`. `ANTHROPIC_API_KEY` and `PROXY_URL` are optional.
4. Share the `onrender.com` address. The first search starts on boot; the database on the disk starts empty.

Hosted mode: `HOST`, `PORT` and `DATA_DIR` come from the environment, cookies are `Secure`, the Render address is the trusted origin, and sign-in is limited to five failed attempts per address in 15 minutes.
