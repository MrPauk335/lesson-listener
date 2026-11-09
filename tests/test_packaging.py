import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'release_assets', Path(__file__).resolve().parents[1] / 'packaging/prepare_assets.py')
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


class DistributionPrivacyTests(unittest.TestCase):
    def test_only_anonymous_subject_identity_is_preserved(self):
        profile = assets.public_profile({
            'id': 'algebra', 'name': 'Алгебра', 'teacher_name': 'Example teacher',
            'meet_url': 'https://example.invalid/private-meeting',
            'teacher_style': 'Personal notes', 'past_lessons': [{'summary': 'Personal history'}],
            'permanent_rules': ['Private preferences'], 'unknown_future_field': 'private',
        })
        self.assertEqual(profile['id'], 'algebra')
        self.assertEqual(profile['name'], 'Алгебра')
        self.assertFalse(profile['teacher_name'])
        self.assertFalse(profile['meet_url'])
        self.assertFalse(profile['teacher_style'])
        self.assertEqual(profile['past_lessons'], [])
        self.assertEqual(profile['permanent_rules'], [])
        self.assertNotIn('unknown_future_field', profile)


if __name__ == '__main__':
    unittest.main()
