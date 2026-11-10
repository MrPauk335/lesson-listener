"""Focused release checks for core behavior and offscreen desktop startup."""

import contextlib
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import numpy
import numpy as np
import psutil
import requests


_ORIGINAL_DATA_DIR = os.environ.get("LESSON_LISTENER_DATA_DIR")
_ORIGINAL_XDG_DATA_HOME = os.environ.get("XDG_DATA_HOME")
_ORIGINAL_LOCALAPPDATA = os.environ.get("LOCALAPPDATA")
_TEST_DATA_DIR = tempfile.TemporaryDirectory(prefix="lesson-listener-tests-")
os.environ["LESSON_LISTENER_DATA_DIR"] = _TEST_DATA_DIR.name
os.environ["XDG_DATA_HOME"] = _TEST_DATA_DIR.name
os.environ["LOCALAPPDATA"] = _TEST_DATA_DIR.name
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Import a private source copy so module bootstrap cannot seed files in the checkout.
_ISOLATED_SOURCE = Path(_TEST_DATA_DIR.name) / "source"
_ISOLATED_SOURCE.mkdir()
shutil.copy2(Path(__file__).resolve().parents[1] / "core.py", _ISOLATED_SOURCE / "core.py")
shutil.copy2(Path(__file__).resolve().parents[1] / "config.template.json", _ISOLATED_SOURCE / "config.template.json")
shutil.copy2(Path(__file__).resolve().parents[1] / "schedule.json", _ISOLATED_SOURCE / "schedule.json")
shutil.copytree(Path(__file__).resolve().parents[1] / "profiles", _ISOLATED_SOURCE / "profiles")
sys.path.insert(0, str(_ISOLATED_SOURCE))

# Importing core should not create or append to a log in the checkout.
_ROOT_LOGGER = logging.getLogger()
_IMPORT_NULL_HANDLER = logging.NullHandler()
_ROOT_LOGGER.addHandler(_IMPORT_NULL_HANDLER)
try:
    import core
    import app as desktop_app
finally:
    _ROOT_LOGGER.removeHandler(_IMPORT_NULL_HANDLER)

from PySide6.QtWidgets import QApplication


def _schedule_file(directory, *, enabled=True):
    schedule = {
        "enabled": enabled,
        "bells": [
            {"num": 1, "start": "08:30", "end": "09:15"},
            {"num": 2, "start": "09:25", "end": "10:10"},
        ],
        "timetable": {"Monday": ["algebra", "geometry"]},
    }
    path = Path(directory) / "schedule.json"
    path.write_text(json.dumps(schedule), encoding="utf-8")
    return str(path)


def _release_qt_application(qt_app):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QFontDatabase
    import shiboken6

    for widget in qt_app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()
    QFontDatabase.removeAllApplicationFonts()
    qt_app.quit()
    shiboken6.delete(qt_app)
    QFontDatabase.removeAllApplicationFonts()


@contextlib.contextmanager
def _isolated_app_state():
    with tempfile.TemporaryDirectory(prefix="lesson-listener-ui-") as data_dir:
        profile_dir = Path(data_dir) / "profiles"
        profile_dir.mkdir()
        (profile_dir / "algebra.json").write_text(
            json.dumps({"id": "algebra", "name": "Алгебра", "teacher_name": ""}),
            encoding="utf-8",
        )
        schedule_path = _schedule_file(data_dir, enabled=False)
        previous_cwd = os.getcwd()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, {"LESSON_LISTENER_DATA_DIR": data_dir}))
            stack.enter_context(mock.patch.object(core, "CONFIG_FILE", str(Path(data_dir) / "config.json")))
            stack.enter_context(mock.patch.object(core, "PROFILES_DIR", str(profile_dir)))
            stack.enter_context(mock.patch.object(core, "SCHEDULE_FILE", schedule_path))
            stack.enter_context(mock.patch.object(core, "LECTURES_DIR", str(Path(data_dir) / "lectures")))
            stack.enter_context(mock.patch.object(core, "LOG_FILE", str(Path(data_dir) / "lesson.log")))
            os.chdir(data_dir)
            try:
                yield data_dir
            finally:
                os.chdir(previous_cwd)


class CoreReleaseTests(unittest.TestCase):
    def test_core_import_data_paths_stay_outside_checkout(self):
        repo_root = Path(__file__).resolve().parents[1]

        self.assertNotEqual(Path(core.BASE_DIR).resolve(), repo_root)
        self.assertEqual(Path(core.DATA_DIR).resolve(), Path(_TEST_DATA_DIR.name).resolve())
        self.assertTrue((Path(core.DATA_DIR) / "config.json").is_file())
        self.assertTrue((Path(core.DATA_DIR) / "schedule.json").is_file())
        self.assertTrue((Path(core.PROFILES_DIR) / "algebra.json").is_file())

    def test_schedule_reports_active_lesson_and_next_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = core.ScheduleManager(_schedule_file(directory))
            status = manager.get_status(datetime.datetime(2026, 11, 9, 8, 45))

        self.assertTrue(status["is_school_time"])
        self.assertFalse(status["is_recess"])
        self.assertEqual(status["subject_id"], "algebra")
        self.assertEqual(status["rem_min"], 30)
        self.assertEqual(status["next_subject_id"], "geometry")

    def test_schedule_reports_break_countdown_and_next_lesson(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = core.ScheduleManager(_schedule_file(directory))
            status = manager.get_status(datetime.datetime(2026, 11, 9, 9, 20))

        self.assertTrue(status["is_recess"])
        self.assertEqual(status["rem_min"], 5)
        self.assertEqual(status["next_lesson_num"], 2)
        self.assertEqual(status["next_subject_id"], "geometry")

    def test_disabled_schedule_does_not_report_school_time(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = core.ScheduleManager(_schedule_file(directory, enabled=False))
            status = manager.get_status(datetime.datetime(2026, 11, 9, 8, 45))

        self.assertEqual(status, {"enabled": False, "is_recess": False, "status_text": ""})

    def test_profile_save_reload_and_lesson_archive_round_trip(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(core, "PROFILES_DIR", directory):
            manager = core.ProfileManager()
            profile = {"id": "physics_test", "name": "Фізика", "teacher_name": "Пані Тест"}
            manager.save(profile)
            manager.add_lesson_archive("physics_test", "Рух", "Швидкість — це шлях за час.")

            restored = core.ProfileManager().get("physics_test")

        self.assertEqual(restored["name"], "Фізика")
        self.assertEqual(restored["teacher_name"], "Пані Тест")
        self.assertEqual(restored["past_lessons"][0]["topic"], "Рух")
        self.assertIn("шлях за час", restored["past_lessons"][0]["summary"])

    def test_answer_html_escapes_markup_and_keeps_simple_formatting(self):
        rendered = core.aloud_to_html("If x < 5 & **y**\nthen continue")

        self.assertIn("x &lt; 5 &amp; <b>y</b>", rendered)
        self.assertIn("<br>", rendered)
        self.assertNotIn("<script>", rendered)

    def test_cheatsheet_html_is_separate_and_escapes_untrusted_text(self):
        rendered = core.aloud_to_html("Answer.\n⚡ Шпаргалка: <img src=x> **x^2**")

        self.assertIn("⚡ <b>Шпаргалка:</b>", rendered)
        self.assertIn("&lt;img src=x&gt;", rendered)
        self.assertIn("<b>x²</b>", rendered)
        self.assertNotIn("<img src=x>", rendered)

    def test_windows_frozen_resources_seed_user_data_without_writing_bundle(self):
        repo_root = str(Path(__file__).resolve().parents[1])
        child = r'''
import json
import contextlib
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

import numpy
import psutil
import requests

def release_qt_application(qt_app):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QFontDatabase
    import shiboken6

    for widget in qt_app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qt_app.processEvents()
    QFontDatabase.removeAllApplicationFonts()
    qt_app.quit()
    shiboken6.delete(qt_app)
    QFontDatabase.removeAllApplicationFonts()

repo_root = sys.argv[1]
with tempfile.TemporaryDirectory(prefix="lesson-listener-frozen-") as root, contextlib.ExitStack() as cleanup:
    cleanup.callback(logging.shutdown)
    root = Path(root)
    source = root / "source"
    resources = root / "resources"
    bundle = root / "bundle"
    local_app_data = root / "local-app-data"
    source.mkdir()
    shutil.copy2(Path(repo_root) / "core.py", source / "core.py")
    sys.path.insert(0, str(source))
    (resources / "profiles").mkdir(parents=True)
    bundle.mkdir()
    (resources / "config.template.json").write_text('{"active_subject":"algebra"}', encoding="utf-8")
    (resources / "schedule.json").write_text('{"enabled":false}', encoding="utf-8")
    (resources / "profiles" / "algebra.json").write_text(
        '{"id":"algebra","name":"Алгебра"}', encoding="utf-8")
    executable = bundle / "Lesson Listener.exe"
    executable.touch()
    with mock.patch.object(sys, "platform", "win32"), \
         mock.patch.object(sys, "frozen", True, create=True), \
         mock.patch.object(sys, "_MEIPASS", str(resources), create=True), \
         mock.patch.object(sys, "executable", str(executable)), \
         mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}):
        import core

    expected_data = local_app_data / "Lesson Listener"
    assert Path(core.DATA_DIR) == expected_data, core.DATA_DIR
    assert json.loads(Path(core.CONFIG_FILE).read_text(encoding="utf-8"))["active_subject"] == "algebra"
    assert json.loads(Path(core.SCHEDULE_FILE).read_text(encoding="utf-8"))["enabled"] is False
    assert json.loads((Path(core.PROFILES_DIR) / "algebra.json").read_text(encoding="utf-8"))["name"] == "Алгебра"
    assert list(bundle.iterdir()) == [executable], "user data must not be written beside the frozen executable"

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import app as desktop_app

    qt_app = QApplication.instance() or QApplication([])
    cleanup.callback(release_qt_application, qt_app)
    qt_app.setQuitOnLastWindowClosed(False)
    with mock.patch.object(core, "pyaudio", None), \
         mock.patch.object(desktop_app.LessonListenerApp, "_ensure_shortcuts", return_value=None), \
         mock.patch.object(desktop_app.LessonListenerApp, "_init_audio", side_effect=AssertionError("audio initialized")), \
         mock.patch.object(desktop_app, "open_lesson_link") as open_lesson:
        controller = desktop_app.LessonListenerApp(show=False, audio=False)
        try:
            qt_app.processEvents()
            assert controller.audio_enabled is False
            assert controller.rec.pa is None
            assert controller.widget.lbl_status.toolTip() == "Звук вимкнено"
            open_lesson.assert_not_called()
        finally:
            for timer in controller.findChildren(QTimer):
                timer.stop()
            controller.shutdown()
            controller.rec.close()
            controller.widget.close()
            qt_app.processEvents()
'''
        child_env = os.environ.copy()
        child_env.pop("LESSON_LISTENER_DATA_DIR", None)
        completed = subprocess.run(
            [sys.executable, "-c", child, repo_root],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=child_env,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


class AtomicPersistenceTests(unittest.TestCase):
    def test_successful_save_replaces_json_and_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "config.json"
            core._write_json_atomically(target, {"subject": "Алгебра"})
            core._write_json_atomically(target, {"subject": "Фізика"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"subject": "Фізика"})
            self.assertEqual(list(target.parent.iterdir()), [target])
            if os.name != "nt":
                self.assertEqual(target.stat().st_mode & 0o077, 0)

    def test_serialization_failure_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "profile.json"
            target.write_bytes(b'{"original":true}')
            with self.assertRaises(TypeError):
                core._write_json_atomically(target, {"invalid": object()})
            self.assertEqual(target.read_bytes(), b'{"original":true}')
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_disk_flush_failure_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            target.write_bytes(b'{"original":true}')
            with mock.patch.object(core.os, "fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    core._write_json_atomically(target, {"replacement": True})
            self.assertEqual(target.read_bytes(), b'{"original":true}')
            self.assertEqual(list(target.parent.iterdir()), [target])

    def test_first_failed_save_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "config.json"
            with mock.patch.object(core.os, "replace", side_effect=PermissionError("read-only")):
                with self.assertRaises(PermissionError):
                    core._write_json_atomically(target, {"replacement": True})
            self.assertEqual(list(target.parent.iterdir()), [])

    def test_all_json_save_paths_keep_previous_data_on_replace_failure(self):
        for kind in ("config", "schedule", "profile"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / ("algebra.json" if kind == "profile" else f"{kind}.json")
                original = b'{"id":"algebra","name":"Original","enabled":false}'
                target.write_bytes(original)
                with mock.patch.object(core, "CONFIG_FILE", str(target)), mock.patch.object(
                    core, "PROFILES_DIR", directory
                ), mock.patch.object(core.os, "replace", side_effect=OSError("replace failed")), self.assertLogs(
                    level="ERROR"
                ):
                    if kind == "config":
                        core.save_config({"replacement": True})
                    elif kind == "schedule":
                        manager = core.ScheduleManager(str(target))
                        manager.data["enabled"] = True
                        manager.save()
                    else:
                        core.ProfileManager().save({"id": "algebra", "name": "Replacement"})
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(list(target.parent.iterdir()), [target])


class DesktopReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.qt_app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        _release_qt_application(cls.qt_app)
        cls.qt_app = None
        super().tearDownClass()

    def _construct_controller(self):
        with mock.patch.object(core, "pyaudio", None):
            controller = desktop_app.LessonListenerApp(show=False, audio=False)
        self.addCleanup(self._dispose_controller, controller)
        return controller

    @staticmethod
    def _dispose_controller(controller):
        from PySide6.QtCore import QTimer

        for timer in controller.findChildren(QTimer):
            timer.stop()
        controller.shutdown()
        controller.rec.close()
        controller.widget.close()
        QApplication.instance().processEvents()

    def test_desktop_constructs_offscreen_with_audio_disabled(self):
        with _isolated_app_state(), mock.patch.object(
            desktop_app.LessonListenerApp, "_ensure_shortcuts", return_value=None
        ), mock.patch.object(desktop_app, "open_lesson_link") as open_lesson:
            controller = self._construct_controller()
            self.qt_app.processEvents()

            self.assertFalse(controller.audio_enabled)
            self.assertIsNotNone(controller.widget)
            self.assertEqual(controller.widget.windowTitle(), "Lesson Listener")
            self.assertEqual(controller.widget.lbl_status.toolTip(), "Звук вимкнено")
            open_lesson.assert_not_called()

    def test_background_completion_is_dispatched_to_ui_thread(self):
        with _isolated_app_state(), mock.patch.object(
            desktop_app.LessonListenerApp, "_ensure_shortcuts", return_value=None
        ), mock.patch.object(desktop_app, "open_lesson_link") as open_lesson:
            controller = self._construct_controller()
            completed = threading.Event()
            callback_thread = []
            result = []
            ui_thread = threading.get_ident()

            controller._run_async(
                lambda: "done",
                lambda value: (result.append(value), callback_thread.append(threading.get_ident()), completed.set()),
            )

            deadline = time.monotonic() + 3
            while not completed.is_set() and time.monotonic() < deadline:
                self.qt_app.processEvents()
                time.sleep(0.01)

            self.assertTrue(completed.is_set(), "background completion was not delivered")
            self.assertEqual(result, ["done"])
            self.assertEqual(callback_thread, [ui_thread])
            open_lesson.assert_not_called()


class LinuxAudioBackendTests(unittest.TestCase):
    def _recorder_with_fake_stream(self):
        stream = mock.Mock()
        sounddevice = mock.Mock()
        sounddevice.InputStream.return_value = stream
        with mock.patch.object(core, "pyaudio", None), mock.patch.object(
            core, "sounddevice", sounddevice
        ), mock.patch.object(core.sys, "platform", "linux"):
            recorder = core.UniversalRecorder()
        self.addCleanup(recorder.close)
        return recorder, sounddevice, stream

    def test_linux_input_stream_is_started_once(self):
        recorder, sounddevice, stream = self._recorder_with_fake_stream()

        with mock.patch.object(core, "pyaudio", None), mock.patch.object(
            core, "sounddevice", sounddevice
        ), mock.patch.object(core.sys, "platform", "linux"):
            recorder.start({
                "category": "mic",
                "dev": {"index": 4, "defaultSampleRate": core.TARGET_SR, "maxInputChannels": 1},
            })

        sounddevice.InputStream.assert_called_once()
        stream.start.assert_called_once_with()
        self.assertIs(recorder.sd_stream, stream)

    def test_linux_stop_stops_and_closes_input_stream_once(self):
        recorder, sounddevice, stream = self._recorder_with_fake_stream()

        with mock.patch.object(core, "pyaudio", None), mock.patch.object(
            core, "sounddevice", sounddevice
        ), mock.patch.object(core.sys, "platform", "linux"):
            recorder.start({
                "category": "mic",
                "dev": {"index": 4, "defaultSampleRate": core.TARGET_SR, "maxInputChannels": 1},
            })
            recorder.stop()
            recorder.stop()

        stream.start.assert_called_once_with()
        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()
        self.assertIsNone(recorder.sd_stream)

    def test_linux_stream_start_failure_cleans_up_reference_and_stream(self):
        recorder, sounddevice, stream = self._recorder_with_fake_stream()
        stream.start.side_effect = RuntimeError("synthetic stream failure")

        with mock.patch.object(core, "pyaudio", None), mock.patch.object(
            core, "sounddevice", sounddevice
        ), mock.patch.object(core.sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "synthetic stream failure"):
                recorder.start({
                    "category": "mic",
                    "dev": {"index": 4, "defaultSampleRate": core.TARGET_SR, "maxInputChannels": 1},
                })

        stream.start.assert_called_once_with()
        stream.stop.assert_called_once_with()
        stream.close.assert_called_once_with()
        self.assertIsNone(recorder.sd_stream)

    def test_linux_callback_downmixes_stereo_and_resamples_to_target_rate(self):
        recorder, sounddevice, _ = self._recorder_with_fake_stream()
        frames = np.array(
            [[8192, 16384], [16384, 8192], [-8192, -16384], [-16384, -8192]],
            dtype=np.int16,
        )

        with mock.patch.object(core, "pyaudio", None), mock.patch.object(
            core, "sounddevice", sounddevice
        ), mock.patch.object(core.sys, "platform", "linux"):
            recorder.start({
                "category": "mic",
                "dev": {"index": 7, "defaultSampleRate": 32000, "maxInputChannels": 2},
            })
            sounddevice.InputStream.call_args.kwargs["callback"](frames, len(frames), None, None)

        np.testing.assert_allclose(recorder.get_last(1), [0.375, -0.375], atol=1e-7)
        self.assertAlmostEqual(recorder.available_seconds(), 2 / core.TARGET_SR)

    def test_linux_callback_interpolates_non_integer_sample_rate_ratio(self):
        recorder, sounddevice, _ = self._recorder_with_fake_stream()
        samples = np.array([0, 6000, 12000, 18000, 24000, 30000], dtype=np.int16)
        expected = np.interp(
            np.linspace(0, len(samples) - 1, 4),
            np.arange(len(samples)),
            samples.astype(np.float32) / 32768.0,
        )

        with mock.patch.object(core, "pyaudio", None), mock.patch.object(
            core, "sounddevice", sounddevice
        ), mock.patch.object(core.sys, "platform", "linux"):
            recorder.start({
                "category": "mic",
                "dev": {"index": 2, "defaultSampleRate": 24000, "maxInputChannels": 1},
            })
            sounddevice.InputStream.call_args.kwargs["callback"](samples, len(samples), None, None)

        np.testing.assert_allclose(recorder.get_last(1), expected, atol=1e-7)
        self.assertEqual(len(recorder.get_last(1)), 4)

    def test_clear_discards_callback_audio_and_resets_buffer_level(self):
        recorder, sounddevice, _ = self._recorder_with_fake_stream()
        samples = np.array([8192, 16384, -8192, -16384], dtype=np.int16)

        with mock.patch.object(core, "pyaudio", None), mock.patch.object(
            core, "sounddevice", sounddevice
        ), mock.patch.object(core.sys, "platform", "linux"):
            recorder.start({
                "category": "mic",
                "dev": {"index": 3, "defaultSampleRate": core.TARGET_SR, "maxInputChannels": 1},
            })
            sounddevice.InputStream.call_args.kwargs["callback"](samples, len(samples), None, None)

        self.assertEqual(len(recorder.get_last(1)), len(samples))
        self.assertGreater(recorder.level, 0)
        recorder.clear()

        self.assertEqual(len(recorder.get_last(1)), 0)
        self.assertEqual(recorder.available_seconds(), 0)
        self.assertEqual(recorder.level, 0)
def tearDownModule():
    from PySide6.QtGui import QFontDatabase

    QFontDatabase.removeAllApplicationFonts()
    logging.shutdown()
    for name, original in (
        ("LESSON_LISTENER_DATA_DIR", _ORIGINAL_DATA_DIR),
        ("XDG_DATA_HOME", _ORIGINAL_XDG_DATA_HOME),
        ("LOCALAPPDATA", _ORIGINAL_LOCALAPPDATA),
    ):
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original
    _TEST_DATA_DIR.cleanup()


if __name__ == "__main__":
    unittest.main()
