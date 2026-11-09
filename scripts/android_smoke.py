"""Exercise the installable Android preview without API credentials or external calls."""
import argparse
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

PACKAGE = 'org.lessonlistener.app'


def adb(*args, timeout=60):
    return subprocess.run(['adb', *args], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout).stdout


def hierarchy():
    adb('shell', 'uiautomator', 'dump', '/sdcard/lesson-window.xml')
    return ET.fromstring(adb('shell', 'cat', '/sdcard/lesson-window.xml'))


def click(text, scroll=False):
    for attempt in range(6 if scroll else 1):
        tree = hierarchy()
        for node in tree.iter('node'):
            if node.get('text') == text and node.get('enabled') == 'true':
                x1, y1, x2, y2 = map(int, re.findall(r'\d+', node.get('bounds')))
                adb('shell', 'input', 'tap', str((x1 + x2) // 2), str((y1 + y2) // 2))
                time.sleep(0.5)
                return
        if scroll:
            dimensions = adb('shell', 'wm', 'size').decode()
            width, height = map(int, re.findall(r'(\d+)x(\d+)', dimensions)[-1])
            adb('shell', 'input', 'swipe', str(width // 2), str(int(height * .85)),
                str(width // 2), str(int(height * .35)), '350')
            time.sleep(0.3)
    raise AssertionError(f'Visible enabled control not found: {text}')


def assert_text(fragment):
    assert any(fragment in node.get('text', '') for node in hierarchy().iter('node')), fragment


def capture(out, name):
    (out / f'{name}.png').write_bytes(adb('exec-out', 'screencap', '-p'))
    (out / f'{name}.xml').write_bytes(ET.tostring(hierarchy(), encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('apk')
    parser.add_argument('--out', default='android-evidence')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    adb('install', '-r', args.apk, timeout=120)
    adb('shell', 'pm', 'clear', PACKAGE)
    adb('shell', 'settings', 'put', 'global', 'window_animation_scale', '0')
    adb('shell', 'settings', 'put', 'global', 'transition_animation_scale', '0')
    adb('shell', 'settings', 'put', 'global', 'animator_duration_scale', '0')
    adb('shell', 'am', 'start', '-W', '-n', PACKAGE + '/.MainActivity', timeout=120)
    assert_text('Lesson Listener')
    capture(out, '01-lesson')
    click('Настройки')
    assert_text('API-ключ')
    capture(out, '02-settings')
    click('Заметки')
    assert_text('Пока нет сохранённых заметок')
    capture(out, '03-notes')
    click('Урок')
    click('Спросить ИИ', scroll=True)
    assert_text('Сначала напишите вопрос')
    capture(out, '04-validation')
    print('PASS: install, launch, lesson/settings/notes navigation, empty-question validation')


if __name__ == '__main__':
    main()
