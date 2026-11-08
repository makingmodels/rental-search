import unittest

from extract import extract
from ranking import score


class ExtractTests(unittest.TestCase):
    def test_size_from_each_portal_shape(self):
        self.assertEqual(extract({'sizings': [{'unit': 'sqft', 'maximumSize': 700}]})['sqm'], 65)
        self.assertEqual(extract({'facts': ['Floor area: 62 sq. m.']})['sqm'], 62)
        self.assertEqual(extract({'description': 'Occupying 651 sq. ft., the flat'})['sqm'], 60)
        self.assertEqual(extract({'description': 'approx. 58m² of living space'})['sqm'], 58)
        # Amenity areas and typos are not the flat.
        self.assertIsNone(extract({'description': '16,000 sq ft of residents amenities'})['sqm'])

    def test_portal_estimate_only_when_the_advert_states_no_size(self):
        estimate = {'unit': 'sqft', 'maximumSize': 700, 'estimate': True}
        self.assertEqual((lambda x: (x['sqm'], x['sqm_source']))(extract({'sizings': [estimate]})), (65, 'estimate'))
        stated = extract({'sizings': [estimate], 'description': 'a generous 58m² flat'})
        self.assertEqual((stated['sqm'], stated['sqm_source']), (58, 'description'))

    def test_epc_and_council_tax_wherever_they_appear(self):
        cases = {'EPC- C DEPOSIT - £2019 COUNCIL TAX BAND- B': ('C', 'B'), 'includes an EPC rating of C, council tax band C': ('C', 'C'),
                 'EPC Rating = D. Council Tax: E': ('D', 'E'), 'EPC:C.': ('C', None)}
        for text, (epc, band) in cases.items():
            facts = extract({'description': text})
            self.assertEqual((facts['epc'], facts['tax_band']), (epc, band), text)
        self.assertIsNone(extract({'description': 'EPC available on request'})['epc'])
        self.assertEqual(extract({'facts': ['Council tax band: D']})['tax_band'], 'D')

    def test_furnishing(self):
        self.assertEqual(extract({'facts': ['Furnishing: Furnished']})['furnishing'], 'furnished')
        self.assertEqual(extract({'facts': ['Furnishing: At tenant choice']})['furnishing'], 'flexible')
        self.assertEqual(extract({'lettings': {'furnishType': 'Unfurnished'}})['furnishing'], 'unfurnished')
        self.assertEqual(extract({'description': 'Available furnished or unfurnished'})['furnishing'], 'flexible')

    def test_keyword_bank_and_flags(self):
        found = extract({'description': 'Newly refurbished, two bathrooms, private balcony. Ideal for a professional couple. Communal gardens.'})['signals']
        self.assertTrue({'refurbished', 'second_bathroom', 'balcony', 'couples'} <= set(found))
        self.assertNotIn('garden', found)
        self.assertNotIn('bills_included', extract({'description': 'Bills not included', 'facts': ['Bills Included: no']})['signals'])


class RankingTests(unittest.TestCase):
    def test_tournament_weights(self):
        base = {'price': 3000, 'work_minutes': 20, 'description': 'flat'}
        plain = score(base, extract(base), 3000)['score']
        cheaper = {**base, 'price': 2900}
        self.assertAlmostEqual(score(cheaper, extract(cheaper), 3000)['score'] - plain, 21)
        faster = {**base, 'work_minutes': 10}
        self.assertAlmostEqual(score(faster, extract(faster), 3000)['score'] - plain, 26)
        refurbished = {**base, 'description': 'refurbished flat'}
        self.assertAlmostEqual(score(refurbished, extract(refurbished), 3000)['score'] - plain, 16)

    def test_highlights_name_the_biggest_reasons(self):
        item = {'price': 2600, 'work_minutes': 12, 'description': 'Newly refurbished, two bathrooms'}
        self.assertEqual(score(item, extract(item), 3000)['highlights'], ['£400 under budget', '12 min to Revolut'])


if __name__ == '__main__':
    unittest.main()
