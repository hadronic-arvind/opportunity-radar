#!/usr/bin/env python3
"""Audit anonymous portable profiles against cached public listings.

Reads the tracked catalog directly and never changes the active user profile.
Network collection is explicit, sequential, bounded by the normal adapters, and
cached so different personas and before/after comparisons reuse the same jobs.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from monitor import config
from monitor.coverage import effective_source_packs
from monitor.fetchers import fetch_source
from monitor.models import Opportunity
from monitor.profile import portable_profile_editor, _updated_local_profile
from monitor.scoring import score_opportunity


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('w', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def prepare_profile(payload, catalog):
    name, editor = portable_profile_editor(payload)
    profile = _updated_local_profile({}, editor)
    packs = effective_source_packs(profile, editor['selected_packs'], editor['coverage_preset'])
    sources = [source for source in catalog['sources'] if set(packs).intersection(source.get('packs', []))
               and source.get('auto_enable', True)]
    return name, profile, packs, sources


def cache_key(source):
    presentation = {'enabled', 'packs', 'domains', 'career_levels', 'regions', 'verified_at', 'support_level'}
    value = {key: value for key, value in source.items() if key not in presentation}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def audit(profiles_dir, output, collect=False, sample_size=10):
    if (any(os.environ.get(key) for key in os.environ
            if key.startswith(('OPPORTUNITY_RADAR_', 'OPPORTUNITY_MONITOR_')))
            or any((ROOT / 'config').glob('*.local.json'))
            or (ROOT / 'data/opportunities.sqlite3').is_symlink()
            or config.PROJECT_ROOT.resolve() != ROOT.resolve()):
        raise ValueError('Run the audit in a clean checkout without private configuration or environment overrides')
    catalog = json.loads((ROOT / 'config/sources.json').read_text())
    personas = {path.stem: prepare_profile(json.loads(path.read_text()), catalog)
                for path in sorted(profiles_dir.glob('*.json'))}
    if not personas:
        raise ValueError('No profile JSON files found')
    sources = {source['id']: source for _, _, _, selected in personas.values() for source in selected}
    cache = output / 'cache'
    fetched = {}
    now = datetime.now(timezone.utc)
    for index, (source_id, source) in enumerate(sorted(sources.items()), 1):
        path = cache / (source_id + '.json')
        entry = json.loads(path.read_text()) if path.exists() else None
        if entry and entry.get('key') != cache_key(source):
            entry = None
        if collect and entry:
            age = (now - datetime.fromisoformat(entry['fetched_at'])).total_seconds()
            if age > 86400 or entry.get('status') == 'error':
                entry = None
        if not entry and collect:
            try:
                result = fetch_source(source)
                entry = {'key': cache_key(source), 'fetched_at': now.isoformat(), 'status': result.status,
                         'items': [asdict(item) for item in result.opportunities], 'message': result.message}
            except Exception as error:
                entry = {'key': cache_key(source), 'fetched_at': now.isoformat(), 'status': 'error',
                         'items': [], 'message': str(error)}
            write_json(path, entry)
            time.sleep(0.15)
        fetched[source_id] = entry or {'status': 'not_fetched', 'items': [], 'message': 'Run with --fetch'}
        if index % 10 == 0 or index == len(sources):
            print('Sources {}/{}'.format(index, len(sources)), flush=True)
    report = {'generated_at': now.isoformat(), 'profiles': {}, 'sources': {
        key: {field: value for field, value in entry.items() if field not in {'items', 'key'}}
        for key, entry in fetched.items()}}
    for slug, (name, profile, packs, selected) in personas.items():
        visible, hidden, seen = [], 0, set()
        for source in selected:
            for record in fetched[source['id']]['items']:
                item = Opportunity(**record)
                identity = item.url.rstrip('/') or (item.source_id, item.external_id)
                if identity in seen:
                    continue
                seen.add(identity)
                score_opportunity(item, profile)
                if item.tier == 'skip':
                    hidden += 1
                    continue
                visible.append(item)
        visible.sort(key=lambda item: (-item.score, item.title, item.organization))
        summary = {'name': name, 'packs': packs, 'selected_sources': len(selected),
                   'healthy_sources': sum(fetched[source['id']]['status'] == 'ok' for source in selected),
                   'visible': len(visible), 'hidden': hidden,
                   'strong_matches': sum(item.score >= 65 for item in visible),
                   'organizations': len({item.organization for item in visible}),
                   'samples': [asdict(item) for item in visible[:sample_size]]}
        report['profiles'][slug] = summary
        write_json(output / (slug + '.json'), summary)
        print('{}: {} visible, {} strong, {} organizations'.format(slug, summary['visible'],
              summary['strong_matches'], summary['organizations']), flush=True)
    write_json(output / 'summary.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', type=Path, default=ROOT / 'examples/profiles')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fetch', action='store_true', help='Fetch missing or day-old public feed snapshots')
    args = parser.parse_args()
    audit(args.profiles, args.output, collect=args.fetch)


if __name__ == '__main__':
    main()
