"""Package the native PyInstaller directory without copying private runtime data."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

VERSION = '1.1.0-beta.1'
ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-freeze', action='store_true')
    args = parser.parse_args()
    if not args.skip_freeze:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
                        str(ROOT / 'packaging/LessonListener.spec')], cwd=ROOT, check=True)
    bundle = ROOT / 'dist/LessonListener'
    if not (bundle / 'LessonListener').is_file():
        raise SystemExit('Missing native executable: run PyInstaller first.')
    out = ROOT / 'release'
    out.mkdir(exist_ok=True)
    archive = out / f'LessonListener-{VERSION}-linux-x86_64.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(bundle, arcname='LessonListener')
    stage = ROOT / 'build/deb'
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(bundle, stage / 'opt/lesson-listener')
    control = stage / 'DEBIAN'
    control.mkdir()
    (control / 'control').write_text(
        'Package: lesson-listener\n'
        'Version: 1.1.0~beta.1\n'
        'Section: education\nPriority: optional\nArchitecture: amd64\n'
        'Maintainer: Lesson Listener contributors\n'
        'Depends: libc6 (>= 2.35), libportaudio2, libegl1, libopengl0, libxkbcommon-x11-0, '
        'libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-shape0, '
        'libxcb-xinerama0, libxcb-render-util0\n'
        'Description: Lesson Listener audio, image and text study assistant\n', encoding='utf-8')
    desktop = stage / 'usr/share/applications/lesson-listener.desktop'
    desktop.parent.mkdir(parents=True)
    shutil.copy2(ROOT / 'packaging/lesson-listener.desktop', desktop)
    icon = stage / 'usr/share/icons/hicolor/256x256/apps/lesson-listener.png'
    icon.parent.mkdir(parents=True)
    shutil.copy2(ROOT / 'icon.png', icon)
    link = stage / 'usr/bin/lesson-listener'
    link.parent.mkdir(parents=True)
    link.symlink_to('/opt/lesson-listener/LessonListener')
    subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage),
                    str(out / f'LessonListener-{VERSION}-linux-amd64.deb')], check=True)
    print(archive)


if __name__ == '__main__':
    main()
