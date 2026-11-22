"""Approved reference catalog for Krea evaluation.

Clients select opaque IDs, never filesystem paths. Consent is rechecked when listing, previewing, and queueing."""
import json
import re
from pathlib import Path

import config
import person_record


def root():
    return Path(config.STATE_DIR) / 'krea-reference-images'


def entries(user):
    if not config.OWNER_USER or user != config.OWNER_USER:
        return {}
    try:
        catalog = json.loads((root() / 'catalog.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    result = {}
    own_person = person_record.resolve(user)
    allowed_people = {}
    for key, item in catalog.items():
        person = item['person']
        if item.get('kind') == 'generated_character':
            # Generated controls are owned assets, not real-person consent records.
            # Only a completed, identity-free generation by this user qualifies.
            import p20_queue
            source_id = item.get('source_job', '')
            source = p20_queue.get(source_id) if isinstance(source_id, str) and re.fullmatch(r'[0-9a-f]{12}', source_id) else None
            if (item.get('owner') == user and source and source.get('user') == user
                    and source.get('status') == 'done' and source.get('use') == 'generic'
                    and source.get('image') == item.get('source_image')
                    and source.get('image') and not source.get('person')
                    and not source.get('lora_path') and not source.get('reference')
                    and not source.get('second_adapter')):
                result[key] = dict(item, use='generated')
            continue
        use = 'personal' if own_person == person else 'named'
        if person not in allowed_people:
            allowed_people[person] = person_record.check_use(person, use, requesting_user=user)[0]
        if allowed_people[person]:
            result[key] = dict(item, use=use)
    return result


def resolve(user, reference_id):
    item = entries(user).get(reference_id)
    if item is None:
        raise ValueError('Choose an available approved reference image.')
    return dict(item, id=reference_id)


def automatic(user, person):
    """Stable reference from the person's already-approved set; no new approval or quality claim."""
    matching = [(key, item) for key, item in entries(user).items() if item['person'] == person]
    if not matching:
        raise ValueError('No reference photos are available for that person.')
    key, item = min(matching, key=lambda pair: (not pair[1].get('preferred', False), pair[1]['filename'], pair[0]))
    return dict(item, id=key)


def choices(user):
    available = entries(user)
    names = {item['person']: (item.get('name') or 'Generated character')
             if item.get('kind') == 'generated_character' else
             person_record.record(item['person']).get('name') or item['person']
             for item in {v['person']: v for v in available.values()}.values()}
    return [{'id': key, 'person': item['person'],
             'name': names[item['person']],
             'filename': item['filename'], 'kind': item.get('kind', 'photo'),
             'preferred': item.get('preferred', False)}
            for key, item in available.items()]


def preview(user, reference_id):
    item = resolve(user, reference_id)
    # Stored filename is derived from the ID, never a client-supplied path.
    suffix = '.png' if item.get('kind') == 'generated_character' else '.jpg'
    return root() / (item['id'] + suffix)
