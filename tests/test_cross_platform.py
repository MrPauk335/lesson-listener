"""Focused release checks for core behavior and offscreen desktop startup."""

import contextlib
import datetime
import json
import logging
import os
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


_ORIGINAL_DATA_DIR = os.environ.get("LESSON_LISTENER_DATA_DIR")
_TEST_DATA_DIR = tempfile.TemporaryDirectory(prefix="lesson-listener-tests-")
os.environ["LESSON_LISTENER_DATA_DIR"] = _TEST_DATA_DIR.name
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
            stack.enter_context(mock.patch.object(core, "LOG_FILE", str(Path(data_dir) / "lesson.log")))
            os.chdir(data_dir)
            try:
                yield data_dir
            finally:
                os.chdir(previous_cwd)


class CoreReleaseTests(unittest.TestCase):
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


class DesktopReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.qt_app.setQuitOnLastWindowClosed(False)

    def _construct_controller(self):
        controller = desktop_app.LessonListenerApp(show=False, audio=False)
        self.addCleanup(self._dispose_controller, controller)
        return controller

    @staticmethod
    def _dispose_controller(controller):
        for name in ("_save_timer", "_sched_timer", "level_timer"):
            timer = getattr(controller, name, None)
            if timer is not None:
                timer.stop()
        if controller.tray is not None:
            controller.tray.hide()
        controller.widget.close()

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


def tearDownModule():
    if _ORIGINAL_DATA_DIR is None:
        os.environ.pop("LESSON_LISTENER_DATA_DIR", None)
    else:
        os.environ["LESSON_LISTENER_DATA_DIR"] = _ORIGINAL_DATA_DIR
    _TEST_DATA_DIR.cleanup()


if __name__ == "__main__":
    unittest.main()
