"""Best-fit score from the tournament questionnaire: 100 is an average, acceptable flat.

Each term is (label, points) so the card can say why a flat ranks where it does.
"""
REFERENCE = {'work_minutes': 20, 'ucl_minutes': 35, 'sqm': 60}  # an "average" flat; only differences from it score
BINARY = {  # tournament points for a stated feature or a stated problem
    'refurbished': 16, 'second_bathroom': 12, 'outdoor': 6, 'quiet': 3, 'amenities': 1, 'better_epc': 1,
    'poor_light': -15, 'noisy': -12, 'one_bathroom': -4, 'no_outdoor': -3, 'tired': -1,
}


def asymmetric(diff, per, gain, loss):
    """Points for a difference where saving and adding are weighted differently (diff < 0 is better)."""
    steps = abs(diff) / per
    return steps * gain if diff < 0 else -steps * loss


def terms(item, facts, budget):
    """The scored parts of a listing. Unknowns score nothing rather than guessing."""
    found, result = facts['signals'], []
    if item.get('price') is not None:
        saved = budget - item['price']
        result.append((f'£{abs(saved):,.0f} {"under" if saved >= 0 else "over"} budget', asymmetric(-saved, 100, 21, 22)))
    if item.get('work_minutes') is not None:
        result.append((f"{item['work_minutes']} min to Revolut", asymmetric(item['work_minutes'] - REFERENCE['work_minutes'], 10, 26, 25)))
    if item.get('ucl_minutes') is not None:
        result.append((f"{item['ucl_minutes']} min to UCL", asymmetric(item['ucl_minutes'] - REFERENCE['ucl_minutes'], 10, 4, 1)))
    if facts['sqm']:
        result.append((f"{facts['sqm']} m²", (facts['sqm'] - REFERENCE['sqm']) / 10 * 10))
    outdoor = 'balcony' in found or 'garden' in found
    second = 'second_bathroom' in found or 'ensuite' in found
    flags = {'refurbished': 'refurbished' in found, 'second_bathroom': second, 'outdoor': outdoor, 'quiet': 'quiet' in found,
             'amenities': 'amenities' in found, 'better_epc': facts['epc'] in ('A', 'B', 'C'),
             'poor_light': 'poor_light' in found, 'noisy': 'noisy' in found, 'tired': 'tired' in found,
             # Adverts nearly always mention a second bathroom or a balcony when there is one, so silence counts against.
             'one_bathroom': facts['has_text'] and not second, 'no_outdoor': facts['has_text'] and not outdoor}
    labels = {'refurbished': 'Refurbished', 'second_bathroom': 'Two bathrooms', 'outdoor': 'Private outdoor space', 'quiet': 'Quiet street',
              'amenities': 'Shops nearby', 'better_epc': f"EPC {facts['epc']}", 'poor_light': 'Limited light', 'noisy': 'Noise nearby',
              'tired': 'Dated finish', 'one_bathroom': 'One bathroom', 'no_outdoor': 'No outdoor space mentioned'}
    result += [(labels[key], BINARY[key]) for key, on in flags.items() if on]
    return result


def score(item, facts, budget):
    parts = terms(item, facts, budget)
    total = 100 + sum(points for _, points in parts)
    highlights = [label for label, points in sorted(parts, key=lambda p: -p[1]) if points >= 6][:2]
    return {'score': round(total, 1), 'terms': [{'label': label, 'points': round(points, 1)} for label, points in parts], 'highlights': highlights}
