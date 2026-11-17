"""Evidence-based English driver assignment and run-override commands."""
import re
from drivers import assign_driver, apply_overrides, resolve_vehicles, driver_key

NUMBER_WORDS = dict(zip('one two three four five six seven eight nine ten'.split(), range(1, 11)))
ORDINALS = 'first second third fourth fifth sixth seventh eighth ninth tenth'.split()
NUMBER = r'(?:\d+|' + '|'.join(NUMBER_WORDS) + r')'
TRUCK_NUMBERS = re.compile(r'\b(?:truck|vehicle|van)s?\s*(?:number\s*|#\s*)?('
                           + NUMBER + r'(?:\s*(?:,|and|&)\s*' + NUMBER + r')*)\b', re.I)
ALL_TRUCKS = re.compile(r'\b(?:all|every)\s+(?:the\s+)?(?:trucks?|vehicles?)\b|\b(?:whole|entire)\s+fleet\b', re.I)
DEFAULT_STYLE = re.compile(r'\b(?:default|usual|normally|override)\b|\bown\s+(?:driving\s+)?style\b', re.I)


def needs_clarification(text):
    text = text.replace('\u2019', "'")
    polite = re.match(r'(?:please\b|(?:can|could|would|will)\s+you\b)', text.strip(), re.I)
    information = re.match(r'^(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?'
                           r'(?:tell\s+me|explain|recommend|suggest|describe|compare|evaluate|assess)\b', text.strip(), re.I)
    return bool(information or re.search(r"\b(?:do not|don't|never|not|instead of|rather than|if|unless|whether)\b", text, re.I)
                or (not polite and (text.rstrip().endswith('?') or re.match(
                    r'(?:what|why|how|would|does|is|will|should|do|are|could|can)\b', text.strip(), re.I))))


def named(text, name):
    return bool(name and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I))


def targets(text, fleet, include_drivers=True):
    numbers = [int(n) if n.isdigit() else NUMBER_WORDS[n.lower()]
               for group in TRUCK_NUMBERS.findall(text)
               for n in re.findall(NUMBER, group, re.I)]
    ordinals = re.findall(r'\b(' + '|'.join(ORDINALS) + r')\s+(?:truck|vehicle)\b', text, re.I)
    for ordinal in ordinals:
        index = ORDINALS.index(ordinal.lower())
        if index >= len(fleet):
            raise ValueError('That truck does not exist in the current fleet')
        numbers.append(fleet[index]['id'])
    if numbers:
        chosen = [v for v in fleet if v['id'] in numbers]
        if len(chosen) != len(set(numbers)):
            raise ValueError('That truck does not exist in the current fleet')
        return chosen
    hits = [v for v in fleet if named(text, v.get('name'))
            or (include_drivers and named(text, v.get('driver')))]
    return hits


def validate_model_command(raw, utterance, fleet, profiles, style_in):
    """Ground a structured LLM operation without re-parsing its sentence grammar.

    The model supplies the operation, value and IDs; code checks the quoted
    request, identities and constraints, then uses the same fleet mutations as
    the UI and offline commands. It never accepts a model-generated fleet.
    """
    if needs_clarification(utterance):
        raise ValueError('Confirm the truck assignment or run style as a direct instruction')
    evidence = str(raw.get('evidence') or '').strip(' \t\r\n.,;:!?"')
    normalize = lambda s: re.sub(r'\s+', ' ', s).casefold()
    if not evidence or normalize(evidence) not in normalize(utterance):
        raise ValueError('Driver changes must quote your exact request')
    if raw.get('op', 'set') != 'set' or any(raw.get(k) is not None for k in ('value_number', 'value_bool', 'value_list')):
        raise ValueError('Driver operations require a text value and a set operation')
    ids = raw.get('vehicle_ids')
    if (not isinstance(ids, list) or not ids or any(type(i) is not int or i <= 0 for i in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError('Name the truck or trucks for this driver operation')
    fleet = resolve_vehicles(fleet, profiles)
    if not set(ids) <= {v['id'] for v in fleet}:
        raise ValueError('That truck does not exist in the current fleet')
    axis = raw.get('axis')
    chosen = fleet if axis == 'vehicle_style' and ALL_TRUCKS.search(evidence) else targets(
        evidence, fleet, include_drivers=axis == 'vehicle_style')
    if {v['id'] for v in chosen} != set(ids):
        raise ValueError('The truck IDs must match the trucks named in your request')
    value = str(raw.get('value_text') or '').strip()
    if axis == 'driver_assignment':
        key = driver_key(value)
        profile = profiles.get(key)
        if not profile or not named(evidence, profile['name']):
            raise ValueError('Name one saved driver to assign. Save a new driver first if needed.')
        if len(ids) != 1:
            raise ValueError('A driver can only be assigned to one truck per run')
        return {'axis': 'driver_assignments', 'value': assign_driver(fleet, ids[0], key, profiles),
                'label': f"{profile['name']} assigned to {chosen[0].get('name', 'Truck ' + str(ids[0]))} ({profile.get('driving_style', 'normal')})"}
    if axis != 'vehicle_style':
        raise ValueError('Unknown driver operation')
    style = value.lower()
    if re.search(r'\b(?:or|either)\b', evidence, re.I):
        raise ValueError('Choose one driving style and name its target trucks')
    if style == 'default':
        if not DEFAULT_STYLE.search(evidence):
            raise ValueError('Say that the truck should return to its driver default')
        style = None
    elif style not in ('eco', 'normal', 'aggressive') or style_in(evidence) != style:
        raise ValueError('Specify Eco, Normal or Aggressive, or describe the requested driving style')
    elif len(set(re.findall(r'\b(?:eco|normal|aggressive)\b', evidence.lower()))) > 1:
        raise ValueError('Choose one driving style for this run')
    overrides = [{'vehicle_id': i, 'driving_style_override': style} for i in ids]
    apply_overrides(fleet, overrides, profiles)
    return {'axis': 'vehicle_style_overrides', 'value': overrides,
            'label': ', '.join(f'Truck {i}' for i in ids) + ' → ' + (style or 'driver default') + ' for this run'}


def parse_command(text, fleet, profiles):
    """Return a concrete change, a profile write, a question, or None.

    Only known command clauses are accepted. Other phrasings go through LLM
    extraction and validate_model_command instead of this grammar.
    """
    text = text.strip()
    save = re.fullmatch(r'(?:please\s+)?(?:remember|save|register|add)\s+(?:driver\s+)?(.+?)\s+as\s+(?:an?\s+)?(eco|normal|aggressive)(?:\s+driver|\s+driving\s+style)?[.!]?', text, re.I)
    if save:
        return {'profile': {'name': save[1].strip(), 'driving_style': save[2].lower(), 'evidence': text}}
    if re.fullmatch(r'(?:list|show)(?:\s+all|\s+saved)?\s+drivers[.!?]?', text, re.I):
        return {'list': True}
    if needs_clarification(text):
        return None
    clause = re.sub(r'^(?:(?:can|could|would)\s+you\s+|please\s+)', '', text, flags=re.I)
    is_assignment = bool(re.search(r'\b(?:assign(?:ed|ing)?|reassign(?:ed|ing)?|allocate(?:d)?)\b|\bdriv(?:es|ing)\s+(?:truck|vehicle)|\bdriven\s+by\b', clause, re.I))
    style_match = re.search(r'\b(eco|normal|aggressive)\b', clause, re.I)
    reset = bool(re.search(r'(?:use|restore|reset|clear).*?(?:driver default|driver.s default|override)', clause, re.I))
    is_override = (style_match or reset) and re.search(r'\b(?:use|set|switch|change|make|restore|reset|clear)\b', clause, re.I)
    if not is_assignment and not is_override:
        return None
    if is_override and len(set(re.findall(r'\b(eco|normal|aggressive)\b', clause.lower()))) > 1:
        raise ValueError('Choose one driving style for this run: Eco, Normal or Aggressive.')
    fleet = resolve_vehicles(fleet, profiles)
    chosen = targets(clause, fleet, include_drivers=not is_assignment)
    if is_assignment:
        drivers = [(key, p) for key, p in profiles.items() if named(clause, p['name'])]
        if len(drivers) != 1:
            raise ValueError('Name one saved driver to assign. Save a new driver first if needed.')
        if len(chosen) != 1:
            raise ValueError('Which truck should this driver operate? Say "Assign Wang to Truck 2."')
        key, p = drivers[0]
        updated = assign_driver(fleet, chosen[0]['id'], key, profiles)
        return {'axis': 'driver_assignments', 'value': updated,
                'label': f"{p['name']} assigned to {chosen[0].get('name', 'Truck ' + str(chosen[0]['id']))} ({p.get('driving_style', 'normal')})"}
    if re.search(r'\b(?:all|every)\s+(?:trucks?|vehicles?)\b', clause, re.I):
        chosen = fleet
    if len(chosen) != 1 and not re.search(r'\b(?:all|every)\b', clause, re.I):
        raise ValueError('Which truck should use this run override? Name its truck number or assigned driver.')
    style = None if reset else style_match[1].lower()
    overrides = [{'vehicle_id': v['id'], 'driving_style_override': style} for v in chosen]
    apply_overrides(fleet, overrides, profiles)
    return {'axis': 'vehicle_style_overrides', 'value': overrides,
            'label': ', '.join(f"Truck {v['id']}" for v in chosen) + ' → ' + (style or 'driver default') + ' for this run'}
