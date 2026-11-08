import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import app


class RankingTests(unittest.TestCase):
    def test_rejects_only_known_disqualifiers(self):
        self.assertEqual(app.assess({'price': 3001, 'beds': 2})['status'], 'excluded')
        self.assertEqual(app.assess({'price': 2900, 'beds': 2, 'description': 'basement flat'})['status'], 'excluded')
        self.assertEqual(app.assess({'price': 2900, 'beds': 2, 'work_minutes': 30, 'pin_exact': True})['status'], 'excluded')
        self.assertEqual(app.assess({'price': 2900, 'beds': 2})['status'], 'uncertain')

    def test_complete_flat_can_match(self):
        listing = {'price': 2750, 'beds': 2, 'address': 'Flat 1, Example Street, London, E1 0AA',
                   'description': 'Furnished first floor flat with separate living room',
                   'floorplans': ['x'], 'work_minutes': 20, 'pin_exact': True,
                   'station_walk': 10}
        self.assertEqual(app.assess(listing)['status'], 'match')

    def test_costs_use_user_csv_and_unknowns(self):
        costs = app.costs({'price': 2950, 'borough': 'Tower Hamlets', 'tax_band': 'F', 'epc': 'F'})
        self.assertAlmostEqual(costs['council_monthly'], 2654.57 / 12, places=2)
        self.assertEqual(costs['energy'], [220, 320])
        # Unknown band, EPC and borough fall back to D and the median of the chosen boroughs, plus £70 water and wifi.
        rough = app.costs({'price': 2950})
        self.assertEqual(rough['band'], 'D')
        self.assertEqual(rough['energy'], app.ENERGY['D'])
        self.assertEqual(rough['total'][0], round(2950 + rough['council_monthly'] + 135 + 70))
        self.assertEqual(len(rough['assumed']), 3)

    def test_approximate_pin_does_not_disqualify_commute(self):
        self.assertNotEqual(app.assess({'price': 2900, 'beds': 2, 'work_minutes': 45, 'pin_exact': False})['status'], 'excluded')


class DuplicateTests(unittest.TestCase):
    def listing(self, id, lat, price=2500, saved=False):
        item = {'id': id, 'price': price, 'beds': 2, 'lat': lat, 'lon': -0.02, 'saved': saved, 'source_label': id, 'url': 'https://x/' + id}
        return {**item, 'assessment': app.assess(item)}

    def test_same_flat_on_two_portals_merges(self):
        merged = app.merge_duplicates([self.listing('a', 51.5117), self.listing('b', 51.51178, saved=True)])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], 'b')  # saved copy leads so the heart stays on
        self.assertEqual(merged[0]['also'][0]['id'], 'a')

    def test_likes_notes_and_bin_follow_every_duplicate(self):
        a = {**self.listing('a', 51.5117), 'liked_by': ['ash'], 'binned': True, 'notes': [{'user': 'ash', 'body': 'later', 'created': '2'}]}
        b = {**self.listing('b', 51.51178), 'liked_by': ['mansa'], 'notes': [{'user': 'mansa', 'body': 'first', 'created': '1'}]}
        merged = app.merge_duplicates([a, b])[0]
        self.assertEqual(merged['liked_by'], ['ash', 'mansa'])
        self.assertTrue(merged['binned'])
        self.assertEqual([n['body'] for n in merged['notes']], ['first', 'later'])
        self.assertEqual(app.listing_ids({'ids': ['a', 'b']}), ['a', 'b'])
        self.assertRaises(ValueError, app.listing_ids, {'ids': 'a'})

    def test_different_price_or_place_stays_separate(self):
        self.assertEqual(len(app.merge_duplicates([self.listing('a', 51.51), self.listing('b', 51.51, price=2600)])), 2)
        self.assertEqual(len(app.merge_duplicates([self.listing('a', 51.51), self.listing('b', 51.53)])), 2)
        self.assertFalse(app.same_flat({'price': 1}, {'price': 1}))
        self.assertEqual(len(app.merge_duplicates([self.listing('a', 51.51, price=None), self.listing('b', 51.51, price=None)])), 2)


class PostcodeAreaTests(unittest.TestCase):
    def test_chosen_postcodes_resolve_to_boroughs_in_the_tax_table(self):
        plants = {'E1 0AA': 'Tower Hamlets', 'SE1 0AA': 'Southwark', 'EC1M 3HA': 'Camden', 'N1 8UY': 'Islington'}
        for postcode, borough in plants.items():
            location = app.locate({'address': f'Flat 1, Example Street, London, {postcode}'})
            self.assertEqual((location['borough'], location['in_area']), (borough, True), postcode)
        self.assertGreater(len(app.AREAS), 12000)
        self.assertTrue({area['borough'] for area in app.AREAS.values()} <= set(app.TAX))

    def test_borough_selects_the_matching_council_tax_row(self):
        self.assertAlmostEqual(app.costs({'price': 2500, 'borough': 'Camden', 'tax_band': 'D'})['council_monthly'], 2207.12 / 12, places=2)
        self.assertAlmostEqual(app.costs({'price': 2500, 'borough': 'Southwark', 'tax_band': 'D'})['council_monthly'], 1967.26 / 12, places=2)

    def test_pin_inside_a_chosen_area_counts(self):
        location = app.locate({'lat': 51.512497, 'lon': -0.052071, 'pin_exact': True})
        self.assertTrue(location['in_area'])
        self.assertEqual(location['borough'], 'Tower Hamlets')

    def test_listing_far_from_every_chosen_postcode_is_excluded(self):
        item = {'price': 2000, 'beds': 2, 'lat': 51.48572, 'lon': 0.06535, 'pin_exact': True}
        location = app.locate(item)
        self.assertFalse(location['in_area'])
        self.assertIn('Outside your chosen postcode areas', app.assess({**item, 'location': location})['excluded'])

    def test_postcode_from_an_unchosen_district_is_excluded(self):
        item = {'price': 2000, 'beds': 2, 'lat': 51.513393, 'lon': -0.0250991, 'pin_exact': False}
        location = app.locate({**item, 'address': 'School House, E14 3LX'})
        self.assertFalse(location['in_area'])
        self.assertIn('Outside your chosen postcode areas', app.assess({**item, 'location': location})['excluded'])

    def test_approximate_pin_near_an_edge_needs_checking(self):
        location = app.locate({'lat': 51.513393, 'lon': -0.0250991, 'pin_exact': False})
        self.assertIsNone(location['in_area'])
        self.assertIn('Postcode area not confirmed', app.assess({'price': 2000, 'beds': 2, 'location': location})['missing'])


class EPCTests(unittest.TestCase):
    def test_no_certificates_is_empty(self):
        error = HTTPError('https://example.org', 404, 'Not Found', None, None)
        with patch.object(app, 'request_json', side_effect=error):
            self.assertEqual(app.search_epc('E14 5HX'), [])

    def test_authentication_errors_are_not_hidden(self):
        error = HTTPError('https://example.org', 401, 'Unauthorized', None, None)
        with patch.object(app, 'request_json', side_effect=error):
            with self.assertRaises(HTTPError):
                app.search_epc('E14 9EA')


class PortalSizeTests(unittest.TestCase):
    def test_onthemarket_area_fields(self):
        self.assertEqual(app.otm_sizings({'minimumAreaSqM': 94, 'minimumAreaSqFt': 1012}), [{'unit': 'sqm', 'minimumSize': 94, 'maximumSize': 94}])
        self.assertEqual(app.otm_sizings({'minimumAreaSqM': 60, 'maximumAreaSqM': 64}), [{'unit': 'sqm', 'minimumSize': 60, 'maximumSize': 64}])
        self.assertEqual(app.otm_sizings({}), [])

    def test_zoopla_epc_estimate_in_escaped_page_data(self):
        html = r'x\"ingested\":{\"sizeSqft\":635,\"sizeSource\":\"comparables_epc\"}y'
        self.assertEqual(app.zoopla_sizings(html), [{'unit': 'sqft', 'maximumSize': 635, 'estimate': True}])
        self.assertEqual(app.zoopla_sizings('nothing here'), [])


class HostingTests(unittest.TestCase):
    def test_sign_in_attempts_are_limited_per_address(self):
        limiter = app.AttemptLimiter(limit=3, window=60)
        for _ in range(3): limiter.fail('1.2.3.4', now=100)
        self.assertTrue(limiter.blocked('1.2.3.4', now=110))
        self.assertFalse(limiter.blocked('5.6.7.8', now=110))
        self.assertFalse(limiter.blocked('1.2.3.4', now=161))  # failures age out

    def test_hosted_origin_is_trusted_and_cookie_is_secure(self):
        with patch.dict('os.environ', {'RENDER_EXTERNAL_URL': 'https://mna.onrender.com'}):
            self.assertIn('https://mna.onrender.com', app.trusted_origins())
            self.assertIn('Secure', app.cookie('abc', 60))
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(app.trusted_origins(), {'http://127.0.0.1:8765', 'http://localhost:8765'})
            self.assertNotIn('Secure', app.cookie('abc', 60))


class SeedTests(unittest.TestCase):
    def test_seed_carries_shortlist_and_notes_but_never_sessions(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:  # Windows keeps the SQLite files open
            folder = pathlib.Path(folder)
            with patch.object(app, 'DB', folder / 'old.sqlite3'):
                app.init()
                with app.db() as con:
                    con.execute("INSERT INTO listings VALUES ('1', '{}', 't0', 't1', NULL, 1)")
                    con.execute("INSERT INTO likes VALUES ('1', 'ash')")
                    con.execute("INSERT INTO notes (listing, username, body, created) VALUES ('1', 'mansa', 'hi', 't2')")
                    con.execute("INSERT INTO sessions VALUES ('secret', 'mansa', 9e12)")
                app.export_seed(folder / 'seed.json.gz')
            with patch.object(app, 'DB', folder / 'new.sqlite3'):
                app.init()
                self.assertEqual(app.import_seed(folder / 'seed.json.gz'), 1)
                self.assertEqual(app.import_seed(folder / 'seed.json.gz'), 0)  # only into an empty database
                with app.db() as con:
                    self.assertEqual(con.execute('SELECT username FROM likes').fetchall()[0][0], 'ash')
                    self.assertEqual(con.execute('SELECT body FROM notes').fetchall()[0][0], 'hi')
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)


class CriteriaTests(unittest.TestCase):
    def test_furnishing_rule_can_be_switched_off(self):
        item = {'price': 2500, 'beds': 2, 'description': 'Unfurnished second floor flat'}
        self.assertIn('Unfurnished', app.assess(item)['excluded'])
        self.assertNotIn('Unfurnished', app.assess(item, {**app.DEFAULT_CRITERIA, 'require_furnished': False})['excluded'])

    def test_edited_limits_apply(self):
        self.assertEqual(app.assess({'price': 3200, 'beds': 2}, {**app.DEFAULT_CRITERIA, 'max_price': 3500})['status'], 'uncertain')

    def test_bad_criteria_are_rejected(self):
        with self.assertRaises(ValueError): app.clean_criteria({'max_price': 50})
        with self.assertRaises(ValueError): app.clean_criteria({'require_furnished': 'yes'})


if __name__ == '__main__':
    unittest.main()
