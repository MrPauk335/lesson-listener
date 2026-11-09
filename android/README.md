# Lesson Listener for Android

Native Java Android app, package `org.lessonlistener.app` (`0.2.0`, version code `2`). It uses only Android platform APIs at runtime—no Python, PySide6, or third-party runtime libraries.

## Build

Install JDK 17, Android SDK platform/build tools for API 35, and Gradle 8.13. Set `ANDROID_HOME` (or `ANDROID_SDK_ROOT`) and accept the Android SDK licenses, then run from this directory:

```sh
gradle assembleDebug
gradle assembleRelease
```

The release variant has no signing configuration and produces an unsigned APK at `app/build/outputs/apk/release/app-release-unsigned.apk`. Sign it with the appropriate release key before distributing it. A Gradle wrapper distribution URL is recorded under `gradle/wrapper/gradle-wrapper.properties`; wrapper scripts/JAR are intentionally not bundled.

The Gradle `copyStudyAssets` task reads the repository's existing `../profiles/*.json` and `../schedule.json` and packages them as app assets. Build from the repository checkout so those source files are present.

## Use

- Choose a subject and optionally enter its lesson context; context is saved per subject on the device.
- Configure Gemini, OpenRouter, or Groq and an editable model name. Default text model: `gemini-2.5-flash` for Gemini, `google/gemini-2.5-flash` for OpenRouter, and `llama-3.3-70b-versatile` for Groq.
- Text questions are sent only when submitted. Image analysis uses the system document picker and supports Gemini/OpenRouter; Groq image analysis is not supported.
- Microphone recording is started only after the user taps the record button and grants Android's runtime microphone permission. It visibly records for up to 45 seconds and then sends the `.m4a` audio to Gemini using inline media. Audio is not supported through OpenRouter/Groq in this app.
- Save an answer to the Notes tab and export notes through Android's document-creation picker.
- The bundled weekly timetable is read-only; subject lesson links open in the user's browser/app.

API keys and local preferences use app-private storage and are excluded from cloud backup and device transfer. Requests go directly to the selected provider only after an explicit user action. Keys are not included in logs or exported notes.

## Android platform limits

This port does not capture audio from other applications or the system mix. Android does not provide this app with the Windows-specific system audio capture, global hotkeys, always-on-top desktop overlay, or Windows startup integration. The app does not attempt to simulate those features.
