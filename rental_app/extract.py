"""Facts, pros and cons read from an advert: structured portal fields first, then the free text.

Portals put the same fact in different places (Rightmove sizings and living costs, Zoopla ntsInfo,
OnTheMarket key info, OpenRent's table, or only the description), so every reader below checks all of them.
"""
import re

SQFT_PER_SQM = 10.764
SQM_RANGE = (25, 200)  # a believable two-bed; rejects "16,000 sq ft of amenities" and typos

SQFT_RE = re.compile(r'(\d{1,2},\d{3}|\d{3,4})(?:\.\d+)?\s*(?:sq\.?\s*f(?:ee)?t\.?|sqft|square\s+f(?:ee|oo)t|ft²|ft2)', re.I)
SQM_RE = re.compile(r'(\d{2,3}(?:\.\d+)?)\s*(?:sq\.?\s*m(?:etres|eters)?\.?|sqm|m²|m2\b|square\s+met(?:re|er)s)', re.I)
# The rating letter must be a capital on its own, so "EPC available" or "EPC is a ..." never reads as a band.
EPC_RE = re.compile(r'(?i:\b(?:EPC|energy\s+(?:performance\s+|efficiency\s+)?(?:certificate\s+)?rating))(?i:\s*(?:rating|band|grade))?'
                    r'\s*(?i:of|is|=|:|-|–|—)?\s*(?i:band\s*)?([A-G])(?![A-Za-z])')
TAX_RES = (re.compile(r'(?i:council\s*tax)[^.\n]{0,20}?(?i:band)\s*(?i:is|of)?\s*[:\-–=]?\s*([A-H])(?![A-Za-z])'),
           re.compile(r'(?i:council\s*tax)\s*[:\-–=]\s*([A-H])(?![A-Za-z])'))

# Keyword bank. key: what the ranking reads; kind: pro, con or flag. Parking is deliberately absent.
SIGNALS = [
    ('refurbished', 'pro', 'Recently refurbished', r'\b(?:newly|recently|fully|just|freshly)\s+(?:refurbished|renovated|decorated|redecorated|modernised)\b|\brefurbished\b|\bnew[- ]build\b|\bbrand[- ]new\b'),
    ('second_bathroom', 'pro', 'Two bathrooms', r'\b(?:two|2|second)\s+(?:double\s+)?(?:bathrooms?|baths?|shower\s?rooms?)\b|\b2\s*bed\s*/\s*2\s*bath\b'),
    ('ensuite', 'pro', 'En-suite', r'\ben[- ]?suite\b'),
    ('balcony', 'pro', 'Private balcony or terrace', r'\b(?:private\s+)?(?:balcony|balconies|terrace|winter\s+garden)\b'),
    ('garden', 'pro', 'Private garden', r'\b(?:private|own|rear|south[- ]facing|west[- ]facing)\s+(?:garden|patio)\b|\bgarden\s+(?:flat|apartment)\b|\bpatio\b'),
    ('quiet', 'pro', 'Quiet street', r'\b(?:quiet|peaceful|tranquil)\b'),
    ('amenities', 'pro', 'Shops and cafés close by', r'\blocal amenities\b|\b(?:moments?|minutes?|short walk|doorstep|steps)\b[^.]{0,30}\b(?:shops|amenities|supermarkets?|restaurants|caf[eé]s)\b'),
    ('station', 'pro', 'Close to a station', r"\b(?:moments?|minutes?|short walk|stone'?s throw|close|near|walking distance)\b[^.]{0,30}\b(?:station|tube|DLR|overground|elizabeth line)\b"),
    ('modern_kitchen', 'pro', 'Modern kitchen', r'\b(?:modern|new|newly fitted|contemporary|fully[- ]fitted|integrated)\s+(?:open[- ]plan\s+)?kitchen\b'),
    ('spacious', 'pro', 'Described as spacious', r'\b(?:spacious|generous(?:ly)?[- ]sized?|large|well[- ]proportioned)\b'),
    ('dishwasher', 'pro', 'Dishwasher', r'\bdish\s?washer\b'),
    ('light', 'pro', 'Bright, good natural light', r'\b(?:bright|light[- ]filled|natural light|dual[- ]aspect|floor[- ]to[- ]ceiling windows)\b'),
    ('bills_included', 'pro', 'Bills included', r'\b(?:all\s+)?bills\s+(?:are\s+)?included\b|\binclusive of (?:all )?bills\b'),
    ('poor_light', 'con', 'Limited natural light', r'\b(?:limited|little|poor|low)\s+(?:natural\s+)?light\b'),
    ('noisy', 'con', 'Busy road, railway or bar nearby', r'\b(?:main|busy)\s+road\b|\boverlook(?:s|ing)\s+(?:the\s+)?(?:railway|train line|tracks)\b|\b(?:above|over)\s+(?:a\s+)?(?:shop|pub|bar|restaurant)\b'),
    ('tired', 'con', 'Dated kitchen or bathroom', r'\b(?:dated|tired|in need of (?:updating|modernisation|refurbishment)|requires? (?:updating|modernisation))\b'),
    ('couples', 'flag', 'Advert says “ideal for couples”: the second bedroom may only fit a desk', r'\b(?:ideal|perfect|suit(?:ed|able|s)?|great)\s+(?:for\s+)?(?:a\s+)?(?:professional\s+)?couples?\b|\bcouples?\s+only\b'),
    ('small_second', 'flag', 'Second bedroom described as single, box room or study', r'\b(?:single|box|small)\s+(?:second\s+)?bedroom\b|\bbox\s?room\b|\bsecond bedroom\b[^.]{0,25}\b(?:single|study|office)\b'),
    ('no_sharers', 'flag', 'Advert says it is not suitable for sharers', r'\bnot\s+suitable\s+for\s+sharers\b|\bno\s+sharers\b'),
]
COMPILED = [(key, kind, label, re.compile(pattern, re.I)) for key, kind, label, pattern in SIGNALS]
COMMUNAL_RE = re.compile(r'\bcommunal\s+(?:roof\s+)?(?:gardens?|terraces?|courtyards?)\b', re.I)


def advert_text(item):
    return ' \n'.join([item.get('title') or '', item.get('description') or '', ' \n'.join(item.get('features') or [])])


def fact_map(item):
    """'Label: value' facts as a lower-case dict; bare facts (OnTheMarket's 'Furnished') map to themselves."""
    facts = {}
    for fact in item.get('facts') or []:
        label, separator, value = str(fact).partition(':')
        facts[label.strip().lower()] = value.strip() if separator else label.strip()
    return facts


def area_from_text(text):
    """First believable floor area in square metres, whichever unit the advert used."""
    candidates = [(m.start(), float(m.group(1).replace(',', '')) / SQFT_PER_SQM) for m in SQFT_RE.finditer(text)]
    candidates += [(m.start(), float(m.group(1))) for m in SQM_RE.finditer(text)]
    for _, sqm in sorted(candidates):
        if SQM_RANGE[0] <= sqm <= SQM_RANGE[1]: return round(sqm)
    return None


def floor_area(item, facts):
    analysis = item.get('floorplan_analysis') or {}
    if analysis.get('total_sqm') and SQM_RANGE[0] <= analysis['total_sqm'] <= SQM_RANGE[1]:
        return round(analysis['total_sqm']), 'floorplan'
    sizings = item.get('sizings') or []
    if sqm := sizing_sqm(s for s in sizings if not s.get('estimate')): return sqm, 'listing'
    for label in ('floor area', 'size', 'property size', 'internal area'):
        if label in facts and (sqm := area_from_text(facts[label])): return sqm, 'listing'
    if sqm := area_from_text(advert_text(item)): return sqm, 'description'
    # A portal's own estimate (Zoopla derives one from EPC records) only when the advert gives nothing.
    if sqm := sizing_sqm(s for s in sizings if s.get('estimate')): return sqm, 'estimate'
    return None, None


def sizing_sqm(sizings):
    for sizing in sizings:
        size = sizing.get('maximumSize') or sizing.get('minimumSize') or 0
        sqm = size if sizing.get('unit') == 'sqm' else size / SQFT_PER_SQM if sizing.get('unit') == 'sqft' else 0
        if SQM_RANGE[0] <= sqm <= SQM_RANGE[1]: return round(sqm)
    return None


def first_match(patterns, *texts):
    for text in texts:
        for pattern in patterns:
            found = pattern.search(text or '')
            if found: return found.group(1).upper()
    return None


def epc_rating(item, facts):
    if item.get('epc'): return item['epc'], 'listing'
    for label in ('epc rating', 'epc', 'energy rating', 'energy performance rating'):
        value = (facts.get(label) or '').strip().upper()[:1]
        if value and value in 'ABCDEFG': return value, 'listing'
    found = first_match([EPC_RE], ' \n'.join(item.get('facts') or []), advert_text(item))
    return (found, 'description') if found else (None, None)


def tax_band(item, facts):
    if item.get('tax_band'): return item['tax_band'], 'listing'
    for label in ('council tax band', 'council tax', 'council tax, if payable'):
        found = re.search(r'(?:band\s*)?\b([A-H])\b', facts.get(label) or '', re.I)
        if found: return found.group(1).upper(), 'listing'
    found = first_match(TAX_RES, ' \n'.join(item.get('facts') or []), advert_text(item))
    return (found, 'description') if found else (None, None)


def furnishing(item, facts):
    """'furnished', 'flexible', 'part furnished', 'unfurnished' or None."""
    candidates = [(item.get('lettings') or {}).get('furnishType') or '', facts.get('furnishing') or '', facts.get('furnished') or '',
                  ' '.join(key for key in facts if 'furnish' in key)]
    for text in candidates + [advert_text(item)]:
        text = text.lower()
        if not text: continue
        if re.search(r"furnished\s+or\s+unfurnished|unfurnished\s+or\s+furnished|furnished\s*/\s*unfurnished|flexible|optional furnish|furnishing optional|tenant'?s? choice", text): return 'flexible'
        if re.search(r'\bpart(?:ly|ially)?[- ]furnished\b', text): return 'part furnished'
        if re.search(r'\bunfurnished\b', text): return 'unfurnished'
        if re.search(r'\bfurnished\b', text): return 'furnished'
    return None


def bathrooms(item, facts):
    analysis = item.get('floorplan_analysis') or {}
    for value in (analysis.get('bathrooms'), item.get('bathrooms'), facts.get('bathrooms')):
        try:
            if value not in (None, ''): return int(value)
        except (TypeError, ValueError): pass
    return None


def signals(item, facts):
    text = COMMUNAL_RE.sub(' ', advert_text(item))
    found = {key: label for key, kind, label, pattern in COMPILED if pattern.search(text)}
    if (facts.get('bills included') or '').lower() == 'yes': found['bills_included'] = 'Bills included'
    if (facts.get('bills included') or '').lower() == 'no': found.pop('bills_included', None)
    if (facts.get('garden') or '').lower() == 'yes': found['garden'] = 'Private garden'
    analysis = item.get('floorplan_analysis') or {}
    if analysis.get('second_bedroom_sqm') and analysis['second_bedroom_sqm'] < 7.5:
        found['small_second'] = f"Floorplan shows a second bedroom of about {analysis['second_bedroom_sqm']:.1f} m²: single-room size"
    return found


def extract(item):
    """Everything the ranking, the criteria and the cost estimate need from one advert."""
    facts = fact_map(item)
    sqm, sqm_source = floor_area(item, facts)
    epc, epc_source = epc_rating(item, facts)
    band, band_source = tax_band(item, facts)
    found = signals(item, facts)
    baths = bathrooms(item, facts)
    if baths and baths >= 2: found['second_bathroom'] = 'Two bathrooms'
    analysis = item.get('floorplan_analysis') or {}
    return {'sqm': sqm, 'sqm_source': sqm_source, 'epc': epc, 'epc_source': epc_source, 'tax_band': band, 'tax_source': band_source,
            'furnishing': furnishing(item, facts), 'bathrooms': baths, 'signals': found,
            'separate_living_room': analysis.get('separate_living_room'), 'has_text': bool((item.get('description') or '').strip())}


def kinds(found):
    by_kind = {'pro': [], 'con': [], 'flag': []}
    lookup = {key: kind for key, kind, _, _ in SIGNALS}
    for key, label in found.items(): by_kind[lookup[key]].append(label)
    return by_kind
