# Releases / Сборки

## 1.1.0-beta.1

This is a **preview**, not a feature-identical Android replacement for Windows.
Desktop keeps the existing Qt interface; Android is a separate native Java application
using the same subject profiles and schedule defaults. No Python installation is needed
for the packaged desktop binaries and no Python runtime is used on Android.

| Platform | Download | Requirements |
|---|---|---|
| Windows | `LessonListener-1.1.0-beta.1-windows-x86_64.exe` | Windows 10/11 x64; unsigned portable executable |
| Linux | `LessonListener-1.1.0-beta.1-linux-amd64.deb` | Ubuntu 22.04+/compatible Debian-based x86_64, graphical desktop |
| Linux portable | `LessonListener-1.1.0-beta.1-linux-x86_64.tar.gz` | Same runtime libraries as the `.deb`; extract and run `./LessonListener/LessonListener` |
| Android | `LessonListener-1.1.0-beta.1-android-debug.apk` | Android 8+; **debug-signed preview**, not a Play Store release |

Binaries do not contain API keys, personal config, recordings or local lecture notes.
Enter your own provider key after installation. Android requests microphone permission
only when recording; files are chosen/exported using the system document picker.
Input is sent to the selected provider only for an explicit request on Android.

### Platform differences

- Windows retains WASAPI loopback, process audio capture, global F8/F9, tray,
  Windows shortcuts and startup integration.
- Linux uses PortAudio input devices. For system audio, expose/select a PulseAudio
  or PipeWire monitor source using the desktop's audio tools. Per-process Windows
  capture is unavailable. F8/F9 work while the app has focus, without root.
  Screen capture/topmost/tray depend on desktop/compositor support; Wayland can
  restrict screen capture and always-on-top. No automatic Windows-style startup.
- Android uses foreground microphone recording (up to 45 seconds), text questions,
  imported images, provider settings and locally saved/exportable notes. It does not
  capture another app's audio, run a background listener, draw over other apps,
  offer global desktop hotkeys, or control browser profiles. See `android/README.md`
  for the exact supported workflow. Microphone recording stops when the app leaves
  the foreground. Do not expect desktop feature parity.
- Cloud requests require internet, a working model identifier and your own API key.
  Provider availability, costs and rate limits are outside the app's control.

### Installing

Windows: run the `.exe`; SmartScreen may warn because it is not Authenticode-signed.
Do not bypass organizational security policy. Linux: `sudo apt install ./LessonListener-*.deb`.
Android: open the APK and allow installation from that source if your device policy permits.
The debug certificate is for testing: a later production-signed app may require uninstalling
this preview first; export notes before uninstalling.

Packaged desktop writable state is separate from the executable: `%LOCALAPPDATA%/Lesson Listener`
on Windows; `${XDG_DATA_HOME:-~/.local/share}/lesson-listener` on Linux.
`LESSON_LISTENER_DATA_DIR` overrides this for testing/portable use. Back up this folder
before uninstalling. Windows source runs retain their legacy data next to `app.py`;
Linux copies missing legacy data on first run without overwriting existing user state.

## Rebuilding

Always build on the target OS. PyInstaller cannot cross-compile Windows from Linux.
Python 3.12, JDK 17, Gradle 8.13 and Android SDK 35 are used in
[`.github/workflows/build.yml`](.github/workflows/build.yml).
The workflow runs tests and publishes build artifacts for Windows, Linux and Android.

```sh
# Linux dependencies + venv (Debian/Ubuntu)
bash scripts/setup-linux.sh --system
source .venv/bin/activate
python -m unittest discover -s tests -v
python packaging/build_linux.py
```

```powershell
# Windows, Python 3.12
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r packaging/requirements-build.txt
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python -m PyInstaller --clean --noconfirm packaging/LessonListener.spec
# dist/LessonListener.exe
```

```sh
# Android; set ANDROID_HOME to your installed SDK (command-line tools)
sdkmanager 'platforms;android-35' 'build-tools;35.0.0'
cd android
gradle --no-daemon lintDebug testDebugUnitTest assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

Production distribution requires a privately managed Android signing key and Windows
code-signing certificate. Do not put either key/password in source control. This preview
uses Android's development certificate and has no Windows code-signing certificate.
These builds are not claimed bit-for-bit reproducible; dependency resolution and the
build toolchain are recorded by the CI logs. SHA-256 checksums accompany release assets.
