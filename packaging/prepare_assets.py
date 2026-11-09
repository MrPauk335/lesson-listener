"""Build anonymous distribution defaults without changing the user's source data."""
import json
from pathlib import Path
import shutil


def public_profile(profile):
    return {
        'id': profile['id'], 'name': profile['name'],
        'teacher_name': '', 'teacher_style': '', 'meet_url': '',
        'permanent_rules': [], 'cumulative_terms': [], 'past_lessons': [],
    }


def prepare(root):
    root = Path(root)
    target = root / 'build/release-assets'
    shutil.rmtree(target, ignore_errors=True)
    (target / 'profiles').mkdir(parents=True)
    for source in (root / 'profiles').glob('*.json'):
        profile = public_profile(json.loads(source.read_text(encoding='utf-8')))
        (target / 'profiles' / source.name).write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(root / 'schedule.json', target / 'schedule.json')
    config = json.loads((root / 'config.template.json').read_text(encoding='utf-8'))
    for key in config:
        if key.endswith('_api_key') or key in ('student_name', 'proxy'):
            config[key] = ''
    config['autostart'] = False
    (target / 'config.template.json').write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    return target


if __name__ == '__main__':
    print(prepare(Path(__file__).resolve().parent.parent))
