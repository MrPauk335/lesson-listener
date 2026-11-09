# Third-party components

Desktop binaries bundle Python and Qt/PySide6 with Python packages from
`requirements.txt`. Android uses Android platform APIs (no third-party runtime
framework). Each component retains its own license; inclusion does not change it.

- Python: https://docs.python.org/3/license.html
- Qt/PySide6: https://doc.qt.io/qtforpython-6/licenses.html
- QtAwesome and bundled icon fonts: https://github.com/spyder-ide/qtawesome/blob/master/LICENSE.txt
- NumPy: https://numpy.org/doc/stable/license.html
- Requests: https://github.com/psf/requests/blob/main/LICENSE
- psutil: https://github.com/giampaolo/psutil/blob/master/LICENSE
- sounddevice/PortAudio: https://python-sounddevice.readthedocs.io/en/latest/about.html
- PyAudioWPatch: https://github.com/s0d3s/PyAudioWPatch/blob/master/LICENSE.txt
- proc-tap: https://pypi.org/project/proc-tap/
- pywin32: https://github.com/mhammond/pywin32/blob/main/License.txt
- keyboard: https://github.com/boppreh/keyboard/blob/master/LICENSE.txt
- PyInstaller bootloader exception: https://pyinstaller.org/en/stable/license.html

The desktop distribution includes the installed package license/metadata directories
where supplied by those packages. Qt shared libraries remain dynamically linked.
Source code and build instructions for this application are distributed in the same
GitHub repository as these releases. Qt/PySide source is available from the Qt project
at https://download.qt.io/official_releases/QtForPython/ .
