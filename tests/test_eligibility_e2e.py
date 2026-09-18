"""Exercise profile import and saved-listing rescoring through the public CLI."""

import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from monitor import cli, config
from monitor.database import Database
from monitor.models import Opportunity


class EligibilityWorkflowTests(unittest.TestCase):
    def test_import_edit_and_dashboard_preserve_citizenship_and_rescore(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ('config', 'dashboard', 'data'):
                (root / folder).mkdir()
            for name in ('profile.json', 'sources.json'):
                shutil.copy2(project / 'config' / name, root / 'config' / name)
            for name in ('template.html', 'styles.css', 'app.js'):
                shutil.copy2(project / 'dashboard' / name, root / 'dashboard' / name)
            database_path = root / 'data' / 'opportunities.sqlite3'
            database = Database(database_path)
            database.initialize()
            source = json.loads((root / 'config' / 'sources.json').read_text())['sources'][0]
            database.sync_source(source)
            for identifier, title, eligibility in (
                ('grfp', 'NSF GRFP', ''),
                ('masters', 'Research internship', 'Must be pursuing a BS or MS degree.'),
                ('phd', 'Research internship', 'Must be pursuing a PhD.'),
            ):
                database.upsert_opportunity(Opportunity(
                    source['id'], identifier, title, 'Example', 'https://example.com/' + identifier,
                    eligibility=eligibility,
                ))
            database.close()
            imported = root / 'import.json'
            imported.write_text(json.dumps({'version': 1, 'name': 'Doctoral research', 'candidate': {
                'stage': 'phd_student', 'filter_nationality': True, 'citizenships': ['India'],
                'permanent_residencies': [], 'us_national': False,
            }}))
            environment = {key: '' for key in (
                'OPPORTUNITY_RADAR_PROFILE', 'OPPORTUNITY_MONITOR_PROFILE',
                'OPPORTUNITY_RADAR_SOURCES', 'OPPORTUNITY_MONITOR_SOURCES',
                'OPPORTUNITY_RADAR_CURATED_PATH', 'OPPORTUNITY_MONITOR_CURATED_PATH',
            )}
            output, errors = io.StringIO(), io.StringIO()
            with (patch.object(config, 'PROJECT_ROOT', root),
                  patch('monitor.profile._lifecycle_lock_path', return_value=root / '.lifecycle'),
                  patch.dict(os.environ, environment), redirect_stdout(output), redirect_stderr(errors)):
                def run(arguments):
                    output.seek(0)
                    output.truncate(0)
                    self.assertEqual(cli.main(arguments), 0, errors.getvalue())
                    return output.getvalue()

                run(['profile', 'import', '--file', str(imported)])
                editor = json.loads(run(['profile', 'show', '--json']))
                self.assertEqual(editor['candidate']['citizenships'], ['IN'])
                self.assertTrue(editor['candidate']['filter_nationality'])
                editor['matching']['base_score'] = 80
                payload = root / 'editor.json'
                payload.write_text(json.dumps(editor))
                run(['profile', 'apply', '--file', str(payload)])

                def listing_states():
                    database = Database(database_path)
                    try:
                        return {row['external_id']: row['tier'] for row in database.connection.execute(
                            'SELECT external_id, tier FROM opportunities')}
                    finally:
                        database.close()

                self.assertEqual(listing_states()['grfp'], 'skip')
                self.assertEqual(listing_states()['masters'], 'skip')
                self.assertNotEqual(listing_states()['phd'], 'skip')
                editor = json.loads(run(['profile', 'show', '--json']))
                editor['candidate']['permanent_residencies'] = ['United States']
                payload.write_text(json.dumps(editor))
                run(['profile', 'apply', '--file', str(payload)])
                self.assertNotEqual(listing_states()['grfp'], 'skip')
                html = (root / 'dashboard' / 'index.html').read_text()
                data = json.loads(re.search(r'<script id="opportunity-data"[^>]*>(.*?)</script>',
                                            html, re.DOTALL).group(1))
                self.assertEqual(data['settings']['profile_editor']['candidate']['permanent_residencies'], ['US'])
                self.assertIn('Filter by citizenship eligibility', html)
                self.assertEqual((root / 'config' / 'profiles.local.json').stat().st_mode & 0o777, 0o600)
