# Build on each target OS; PyInstaller does not cross-compile.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_all, copy_metadata

root = Path(SPECPATH).parent
windows = sys.platform == 'win32'
datas = [(str(root / 'profiles'), 'profiles'),
         (str(root / 'schedule.json'), '.'),
         (str(root / 'config.template.json'), '.'),
         (str(root / 'icon.png'), '.'),
         (str(root / 'icon.ico'), '.')]
datas += collect_data_files('qtawesome')
datas += [(str(root / 'packaging/THIRD_PARTY.md'), '.'),
          (str(root / 'packaging/licenses'), 'licenses')]
for distribution in ('PySide6-Essentials', 'shiboken6', 'QtAwesome', 'QtPy',
                     'numpy', 'requests', 'psutil', 'certifi'):
    datas += copy_metadata(distribution)
if not windows:
    datas += copy_metadata('sounddevice')
binaries = []
hiddenimports = []
if windows:
    for module in ('proctap', 'pyaudiowpatch'):
        data, bins, imports = collect_all(module)
        datas += data
        binaries += bins
        hiddenimports += imports
    hiddenimports += ['keyboard', 'pythoncom', 'win32com.client']
else:
    hiddenimports += ['sounddevice']

a = Analysis([str(root / 'app.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, hookspath=[],
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinter'], noarchive=False)
pyz = PYZ(a.pure)
if windows:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='LessonListener',
              console=False, icon=str(root / 'icon.ico'), upx=False,
              version=str(root / 'packaging' / 'windows-version.txt'))
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='LessonListener',
              console=True, upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, name='LessonListener', upx=False)
