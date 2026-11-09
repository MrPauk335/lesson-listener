"""
Lesson Listener — розумний AI-суфлер для онлайн-уроків та пар.
Сучасний інтерфейс на PySide6 (Qt): компактний плаваючий віджет + іконка в системному треї.
Бекенд (звук, Gemini, профілі, контекст уроку) — у core.py.

Запуск:  run.bat   або   pythonw app.py   (додай --tray, щоб стартувати прихованим у треї)
"""
import datetime
import logging
import re
import sys
import threading
import time

import numpy as np
from PySide6.QtCore import (QBuffer, QByteArray, QIODevice, QObject, QPoint, QRect, QRectF,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QActionGroup, QColor, QCursor, QGuiApplication, QKeySequence, QPainter,
                           QPalette, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QScrollArea, QSizeGrip, QSlider, QSystemTrayIcon, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

import ui_theme as T
from core import (ANSWER_MODELS, BG_MODELS, DEFAULT_BELLS, DEFAULT_CONFIG, DEFAULT_TIMETABLE,
                  FALLBACK_CHAIN, GEMINI_ANSWER_MODELS, GROQ_MODELS, HOTKEY, MAX_RING_SECONDS,
                  OPENROUTER_MODELS, PROVIDERS, TARGET_SR, GeminiEngine, LectureManager, LessonContext, ProfileManager,
                  ScheduleManager, UniversalRecorder, aloud_to_html, audio_to_wav_bytes, clean_math,
                  detect_subject_from_text, detect_subject_from_windows, ensure_start_menu_shortcut,
                  find_chrome_exe, find_school_chrome_profile, get_active_window_title,
                  is_lesson_already_open, is_windows_autostart_enabled, load_config, open_lesson_link, open_meet_in_school_profile,
                  open_windows_sound_routing, parse_teacher_text_to_pairs, save_config,
                  set_windows_autostart, speak_text)

M = 14        # поле навколо картки під тінь
RADIUS = 16

STATE_INFO = {
    # стан: (підказка, колір крапки у віджеті, колір іконки в треї)
    "ok": ("Слухаю", T.OK, T.ACCENT),
    "busy": ("Готую відповідь…", T.WARN, T.WARN),
    "nokey": ("Не задано API-ключ Gemini", T.ERR, T.ERR),
    "nosrc": ("Немає джерела звуку", T.MUTED, "#6b7184"),
    "recess": ("Перерва · Прослуховування на паузі", T.WARN, T.WARN),
}


def normalize_status_text(text: str) -> str:
    """Видаляє випадкові дублі емодзі та нормалізує рядок статусу."""
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r'^(?:☕\s*)+', '☕ ', t)
    t = re.sub(r'^(?:▶️\s*)+', '▶️ ', t)
    t = re.sub(r'^(?:⚠️\s*)+', '⚠️ ', t)
    t = re.sub(r'^(?:⚡\s*)+', '⚡ ', t)
    return t.strip()
CATS = [
    ("cable", "bolt", "Кабель (без Discord)"),
    ("browser", "globe", "Браузер"),
    ("mic", "mic", "Мікрофон"),
    ("system", "speaker", "Динаміки"),
]
CAT_LABEL = {c: label for c, _, label in CATS}
DEFAULT_PLACEHOLDER = f"Натисни {HOTKEY.upper()}, коли вчитель закінчить питання — відповідь з'явиться тут"


class Bridge(QObject):
    """Сигнали для безпечної передачі результатів із робочих потоків в UI-потік."""
    answer_ready = Signal(dict, float)
    worker_error = Signal(str)
    bg_updated = Signal()
    hotkey = Signal()
    snip_hotkey = Signal()
    slide_detected = Signal(str)


class ScreenSnipper(QWidget):
    """Повноекранний напівпрозорий оверлей для виділення області екрана (задачі, тесту, формули)."""
    snippet_captured = Signal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # Визначаємо активний монітор за положенням курсора
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.primaryScreen()
        for s in QGuiApplication.screens():
            if s.geometry().contains(cursor_pos):
                screen = s
                break
        self._screen = screen
        geo = screen.geometry()
        self.setGeometry(geo)

        # Миттєвий знімок екрана
        self._screenshot = screen.grabWindow(0)
        self._origin = None
        self._current = None

    def _to_physical_rect(self, rect: QRect) -> QRect:
        """Переводить логічні координати виділення миші у фізичні пікселі знімка (High-DPI)."""
        dpr = self._screen.devicePixelRatio()
        sx = int(round(rect.x() * dpr))
        sy = int(round(rect.y() * dpr))
        sw = int(round(rect.width() * dpr))
        sh = int(round(rect.height() * dpr))
        return QRect(sx, sy, sw, sh).intersected(self._screenshot.rect())

    def paintEvent(self, event):
        painter = QPainter(self)
        # Малюємо заморожений екран точно на весь віджет (масштабується під логічний розмір екрана)
        painter.drawPixmap(self.rect(), self._screenshot)
        # Напівпрозоре затемнення фону
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        if self._origin and self._current:
            rect = QRect(self._origin, self._current).normalized()
            if not rect.isEmpty():
                src_rect = self._to_physical_rect(rect)
                painter.drawPixmap(rect, self._screenshot, src_rect)
                pen = QPen(QColor("#38bdf8"), 2, Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.drawRect(rect)
                txt = f"{rect.width()} × {rect.height()} px — відпусти або Enter для розв'язку (Esc скасувати)"
                painter.setPen(QColor("#ffffff"))
                painter.drawText(rect.left() + 6, max(22, rect.top() - 6), txt)
        else:
            painter.setPen(QColor("#ffffff"))
            painter.drawText(20, 35, "🎯 Затисни ліву кнопку миші та виділи рамкою завдання / тест • Esc для скасування")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.pos()
            self._current = event.pos()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.close()

    def mouseMoveEvent(self, event):
        if self._origin:
            self._current = event.pos()
            self.update()

    def _finish_capture(self, end_pos):
        if not self._origin:
            return
        rect = QRect(self._origin, end_pos).normalized()
        self._origin = None
        self.close()
        if rect.width() > 15 and rect.height() > 15:
            src_rect = self._to_physical_rect(rect)
            cropped = self._screenshot.copy(src_rect)
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            cropped.save(buf, "JPEG", 90)
            self.snippet_captured.emit(bytes(ba))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._origin:
            self._finish_capture(event.pos())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._origin and self._current:
                self._finish_capture(self._current)


class DragHeader(QWidget):
    """Шапка віджета: перетягування вікна нативним системним рухом (без лагів)."""

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            e.accept()
        else:
            super().mousePressEvent(e)


# ======================================================================
#  Плаваючий віджет
# ======================================================================
class FloatingWidget(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self._mini = bool(ctrl.cfg.get("mini_mode", False))
        self._full_h = int(ctrl.cfg.get("win_h", 560))
        self.setWindowTitle("Lesson Listener")
        self.setWindowIcon(T.app_icon())
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(self._reset_copy_btn)
        self._geom_timer = QTimer(self)
        self._geom_timer.setSingleShot(True)
        self._geom_timer.setInterval(600)
        self._geom_timer.timeout.connect(self._persist_geometry)
        self._build()
        self.set_mini(self._mini, initial=True)
        self.restore_geometry()
        self.setWindowOpacity(max(0.5, min(1.0, ctrl.cfg.get("widget_opacity", 100) / 100.0)))

    # ------------------------------------------------------------ побудова
    def _icon_btn(self, glyph, tip, slot, checkable=False):
        b = QPushButton()
        b.setObjectName("Icon")
        b.setIcon(T.glyph_icon(glyph, T.MUTED_LIGHT, T.ACCENT_HOVER))
        b.setIconSize(QSize(16, 16))
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(slot)
        return b

    def _build(self):
        c = self.ctrl
        root = QVBoxLayout(self)
        root.setContentsMargins(M + 14, M + 10, M + 14, M + 8)
        root.setSpacing(8)

        # ---- шапка
        self.header = DragHeader()
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(2)
        self.dot = QLabel()
        self.dot.setFixedSize(10, 10)
        hl.addWidget(self.dot)
        hl.addSpacing(6)
        self.subject = QComboBox()
        self.subject.setObjectName("Subject")
        self.subject.setCursor(Qt.CursorShape.PointingHandCursor)
        self.subject.setToolTip("Предмет")
        self.subject.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.subject.setMaximumWidth(230)
        self.subject.activated.connect(self._on_subject_activated)
        hl.addWidget(self.subject)
        hl.addStretch(1)
        self.btn_new = self._icon_btn("refresh", "Новий урок — очистити буфер звуку й тему",
                                      lambda *_: c.on_start_new_lesson())
        self.btn_meet = self._icon_btn("globe", "Відкрити урок (Google Meet / Zoom / посилання)",
                                       lambda *_: c.open_current_meet(force=True))
        self.btn_snip = self._icon_btn("camera", "Показати область екрана (задачу / слайд / тест) — F9",
                                       lambda *_: c.start_screen_snip())
        self.btn_notes = self._icon_btn("education", "Конспект уроку (відкрити записи та збережені слайди)",
                                        lambda *_: c.open_current_lecture())
        self.btn_recess = self._icon_btn("coffee", "Перерва / Пауза — вимкнути прослуховування на зміні",
                                         lambda *_: c.toggle_recess(), checkable=True)
        self.btn_recess.setObjectName("IconRecess")
        self.btn_mini = self._icon_btn("chevron_up", "Міні-режим", lambda *_: self.set_mini(not self._mini))
        self.btn_pin = self._icon_btn("pin", "Поверх усіх вікон", lambda on=False: c.set_topmost(bool(on)),
                                      checkable=True)
        self.btn_pin.setChecked(bool(c.cfg.get("topmost", True)))
        self.btn_gear = self._icon_btn("gear", "Пам'ять уроку та налаштування", lambda *_: c.open_panel())
        self.btn_hide = self._icon_btn("minimize", "Сховати в трей (Esc)", lambda *_: self.hide_widget())
        for b in (self.btn_new, self.btn_meet, self.btn_snip, self.btn_notes, self.btn_recess, self.btn_mini, self.btn_pin, self.btn_gear, self.btn_hide):
            hl.addWidget(b)
        root.addWidget(self.header)

        self.lbl_banner = T.ElidedLabel()
        self.lbl_banner.setObjectName("Muted")
        root.addWidget(self.lbl_banner)

        # ---- банер виявлення слайда / дошки на екрані
        self.slide_bar = QFrame()
        self.slide_bar.setObjectName("SlideBar")
        self.slide_bar.setStyleSheet("""
            QFrame#SlideBar {
                background: rgba(14, 165, 233, 0.16);
                border: 1px solid #38bdf8;
                border-radius: 8px;
            }
        """)
        sbl = QHBoxLayout(self.slide_bar)
        sbl.setContentsMargins(10, 6, 8, 6)
        sbl.setSpacing(8)

        self.lbl_slide_icon = QLabel()
        self.lbl_slide_icon.setPixmap(T.glyph_icon("camera", "#38bdf8", size=15).pixmap(15, 15))
        sbl.addWidget(self.lbl_slide_icon)

        self.lbl_slide_text = QLabel("Вчитель демонструє слайд / дошку")
        self.lbl_slide_text.setStyleSheet("color: #f1f5f9; font-size: 11px; font-weight: 500;")
        self.lbl_slide_text.setWordWrap(True)
        sbl.addWidget(self.lbl_slide_text, 1)

        self.btn_slide_capture = QPushButton("📸 Скріншот")
        self.btn_slide_capture.setStyleSheet("""
            QPushButton {
                background: #0284c7;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: 600;
                font-size: 11px;
            }
            QPushButton:hover { background: #0369a1; }
        """)
        self.btn_slide_capture.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_slide_capture.clicked.connect(lambda: c.accept_slide_capture())
        sbl.addWidget(self.btn_slide_capture)

        self.btn_slide_dismiss = QPushButton("✕")
        self.btn_slide_dismiss.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94a3b8;
                border: none;
                font-size: 13px;
                padding: 2px 4px;
            }
            QPushButton:hover { color: #f87171; }
        """)
        self.btn_slide_dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_slide_dismiss.clicked.connect(lambda: c.dismiss_slide_prompt())
        sbl.addWidget(self.btn_slide_dismiss)

        self.slide_bar.hide()
        root.addWidget(self.slide_bar)

        self._slide_timer = QTimer(self)
        self._slide_timer.setSingleShot(True)
        self._slide_timer.setInterval(22000)
        self._slide_timer.timeout.connect(self.hide_slide_prompt)

        # ---- картка відповіді
        self.card = QFrame()
        self.card.setObjectName("Inner")
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(14, 12, 14, 10)
        cl.setSpacing(6)
        self.lbl_question = QLabel("Запитання вчителя")
        self.lbl_question.setObjectName("Question")
        self.lbl_question.setWordWrap(True)
        cl.addWidget(self.lbl_question)
        self.txt_aloud = QTextBrowser()
        self.txt_aloud.setObjectName("Aloud")
        self.txt_aloud.setOpenExternalLinks(False)
        self.txt_aloud.setFrameShape(QFrame.Shape.NoFrame)
        self.txt_aloud.setMinimumHeight(115)
        self.txt_aloud.setPlaceholderText(DEFAULT_PLACEHOLDER)
        pal = self.txt_aloud.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(T.MUTED))
        self.txt_aloud.setPalette(pal)
        cl.addWidget(self.txt_aloud, 1)
        row = QHBoxLayout()
        self.lbl_time = QLabel("")
        self.lbl_time.setObjectName("Time")
        row.addWidget(self.lbl_time)
        row.addStretch(1)
        self.btn_copy = QPushButton("  Копіювати")
        self.btn_copy.setObjectName("Ghost")
        self.btn_copy.setIcon(T.glyph_icon("copy", T.MUTED_LIGHT, size=14))
        self.btn_copy.setIconSize(QSize(14, 14))
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_copy.clicked.connect(lambda *_: self._copy_clicked())
        row.addWidget(self.btn_copy)
        cl.addLayout(row)
        root.addWidget(self.card, 3)

        # ---- пояснення (розгортається)
        self.btn_more = QPushButton("  Пояснення та діалог класу")
        self.btn_more.setObjectName("Link")
        self.btn_more.setCheckable(True)
        self.btn_more.setIcon(T.glyph_icon("chevron_down", T.MUTED, T.MUTED, 12))
        self.btn_more.setIconSize(QSize(12, 12))
        self.btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_more.toggled.connect(self._on_more_toggled)
        root.addWidget(self.btn_more)
        self.txt_deep = QTextBrowser()
        self.txt_deep.setObjectName("Deep")
        self.txt_deep.setMinimumHeight(110)
        self.txt_deep.hide()
        root.addWidget(self.txt_deep, 2)

        # ---- швидке текстове питання
        self.quick_row = QWidget()
        ql = QHBoxLayout(self.quick_row)
        ql.setContentsMargins(0, 0, 0, 0)
        ql.setSpacing(6)
        self.ent_quick = QLineEdit()
        self.ent_quick.setPlaceholderText("Або напиши питання й натисни Enter…")
        self.ent_quick.returnPressed.connect(self._send_quick)
        ql.addWidget(self.ent_quick, 1)
        self.btn_send = self._icon_btn("forward", "Відповісти на текстове питання", lambda *_: self._send_quick())
        ql.addWidget(self.btn_send)
        root.addWidget(self.quick_row)

        # ---- головна кнопка
        self.btn_answer = QPushButton(f"  Відповісти  ·  {HOTKEY.upper()}")
        self.btn_answer.setObjectName("Primary")
        self.btn_answer.setIcon(T.glyph_icon("bolt", "#ffffff", size=16))
        self.btn_answer.setIconSize(QSize(16, 16))
        self.btn_answer.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_answer.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_answer.clicked.connect(lambda *_: c.on_trigger_answer())
        root.addWidget(self.btn_answer)

        # ---- джерело звуку
        self.seg_row = QWidget()
        sl = QHBoxLayout(self.seg_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        self.seg_group = QButtonGroup(self)
        self.seg_group.setExclusive(True)
        self.seg_buttons = {}
        for cat, glyph, label in CATS:
            b = QPushButton(" " + label)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setIcon(T.glyph_icon(glyph, T.MUTED_LIGHT, "#ffffff", 15))
            b.setIconSize(QSize(15, 15))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda *_, k=cat: c.switch_category(k))
            self.seg_group.addButton(b)
            self.seg_buttons[cat] = b
            sl.addWidget(b, 1)
        root.addWidget(self.seg_row)

        # ---- статус-рядок
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.lbl_status = T.ElidedLabel("Запускаюсь…")
        self.lbl_status.setObjectName("Status")
        bottom.addWidget(self.lbl_status, 1)
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedSize(60, 4)
        self.level_bar.setToolTip("Рівень звуку")
        bottom.addWidget(self.level_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lbl_buf = QLabel("0 с")
        self.lbl_buf.setObjectName("Status")
        self.lbl_buf.setMinimumWidth(30)
        self.lbl_buf.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_buf.setToolTip("Скільки секунд звуку зараз у буфері")
        bottom.addWidget(self.lbl_buf)
        bottom.addWidget(QSizeGrip(self), 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        root.addLayout(bottom)

    # ------------------------------------------------------------ малювання картки з м'якою тінню
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.rect()).adjusted(M, M, -M, -M)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(M, 0, -1):
            a = int(20 * (1 - i / M) ** 1.5) + 1
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(card.adjusted(-i, -i + 2, i, i), RADIUS + i, RADIUS + i)
        p.setBrush(QColor(T.BG))
        p.setPen(QPen(QColor(T.BORDER), 1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)

    # ------------------------------------------------------------ геометрія / видимість
    def restore_geometry(self):
        c = self.ctrl.cfg
        w = max(400, min(int(c.get("win_w", 460)), 900))
        h = max(300, min(int(c.get("win_h", 560)), 1000))
        self._full_h = h
        self.resize(w, 100 if self._mini else h)
        x, y = c.get("win_x"), c.get("win_y")
        if x is not None and y is not None and QGuiApplication.screenAt(QPoint(int(x) + 40, int(y) + 40)):
            self.move(int(x), int(y))
        else:
            geo = QGuiApplication.primaryScreen().availableGeometry()
            self.move(geo.right() - w - 24, geo.top() + 60)

    def moveEvent(self, e):
        super().moveEvent(e)
        if self.isVisible():
            self._geom_timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.isVisible():
            self._geom_timer.start()

    def _persist_geometry(self):
        if not self.isVisible():
            return
        cfg = self.ctrl.cfg
        cfg["win_x"], cfg["win_y"], cfg["win_w"] = self.x(), self.y(), self.width()
        if not self._mini:
            cfg["win_h"] = self.height()
            self._full_h = self.height()
        self.ctrl.save_soon()

    def showEvent(self, e):
        super().showEvent(e)
        T.set_topmost(self, bool(self.ctrl.cfg.get("topmost", True)))

    def show_widget(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_widget(self):
        self.hide()

    def toggle_visibility(self):
        if self.isVisible():
            self.hide_widget()
        else:
            self.show_widget()

    def closeEvent(self, e):
        e.ignore()
        self.hide_widget()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide_widget()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------ режими
    def set_mini(self, on: bool, initial: bool = False):
        if on and not self._mini and not initial:
            self._full_h = self.height()
        self._mini = on
        for w in (self.btn_more, self.quick_row, self.seg_row):
            w.setVisible(not on)
        if on:
            self.txt_deep.hide()
        else:
            self.txt_deep.setVisible(self.btn_more.isChecked())
        self.btn_mini.setIcon(T.glyph_icon("chevron_down" if on else "chevron_up", T.MUTED_LIGHT, T.ACCENT_HOVER))
        self.btn_mini.setToolTip("Розгорнути" if on else "Міні-режим")
        self.ctrl.cfg["mini_mode"] = on
        if not initial:
            self.ctrl.save_soon()
            QTimer.singleShot(0, self._apply_mode_size)

    def _apply_mode_size(self):
        self.layout().activate()
        self.resize(self.width(), 1 if self._mini else self._full_h)

    def _on_more_toggled(self, on):
        self.btn_more.setIcon(T.glyph_icon("chevron_up" if on else "chevron_down", T.MUTED, T.MUTED, 12))
        self.txt_deep.setVisible(on and not self._mini)

    # ------------------------------------------------------------ дії користувача
    def _on_subject_activated(self, idx):
        data = self.subject.itemData(idx)
        if data == "__add__":
            self.ctrl.prompt_new_subject()
        elif data:
            self.ctrl._switch_to_subject(data)

    def _send_quick(self):
        text = self.ent_quick.text().strip()
        if text and self.ctrl.on_quick_text_answer(text):
            self.ent_quick.clear()

    def _copy_clicked(self):
        if self.ctrl.copy_aloud_text():
            self.btn_copy.setText("  Скопійовано")
            self._copy_timer.start(1300)

    def _reset_copy_btn(self):
        self.btn_copy.setText("  Копіювати")

    # ------------------------------------------------------------ оновлення вигляду (викликає контролер)
    def set_state(self, state):
        text, col, _ = STATE_INFO[state]
        self.dot.setStyleSheet(f"background:{col}; border-radius:5px;")
        self.dot.setToolTip(text)

    def set_busy(self, busy: bool):
        self.btn_answer.setEnabled(not busy)
        self.btn_answer.setText("  Слухаю та рахую…" if busy else f"  Відповісти  ·  {HOTKEY.upper()}")

    def set_status(self, text: str, warn: bool = False):
        clean = normalize_status_text(text)
        self.lbl_status.setStyleSheet(f"color:{T.WARN if warn else T.MUTED};")
        self.lbl_status.setFullText(clean)

    def show_slide_prompt(self, desc: str):
        txt = f"📸 Слайд: {desc}. Зберегти в конспект?" if desc else "📸 Вчитель демонструє слайд. Зберегти в конспект?"
        self.lbl_slide_text.setText(txt)
        self.slide_bar.show()
        self._slide_timer.start()

    def hide_slide_prompt(self):
        self.slide_bar.hide()
        self._slide_timer.stop()

    def set_level(self, level: int, seconds: float):
        self.level_bar.setValue(level)
        self.lbl_buf.setText(f"{seconds:.0f} с")

    def set_category(self, cat):
        b = self.seg_buttons.get(cat)
        if b is not None:
            b.setChecked(True)

    def refresh_banner(self):
        c = self.ctrl
        if getattr(c, "is_recess", False):
            self.lbl_banner.setStyleSheet(f"color:{T.WARN}; font-weight:600;")
            self.lbl_banner.setFullText("☕ ПЕРЕРВА · Прослуховування на паузі (квота не витрачається)")
            return
        self.lbl_banner.setStyleSheet(f"color:{T.MUTED}; font-weight:normal;")
        sub = c.active_profile.get("name", "Урок")
        teacher = c.active_profile.get("teacher_name", "")
        t_str = f" · {teacher}" if teacher else ""
        self.lbl_banner.setFullText(f"{sub}{t_str}  ·  Тема: {c.context.topic}")

    def set_recess(self, on: bool):
        self.btn_recess.blockSignals(True)
        self.btn_recess.setChecked(on)
        self.btn_recess.blockSignals(False)
        self.btn_recess.setToolTip("Перерва активна — натисни ☕ щоб відновити урок" if on else
                                   "Перерва / Пауза — вимкнути прослуховування на зміні")
        if on:
            self.btn_answer.setText("⏸️  ПЕРЕРВА  ·  Натисни ☕ щоб відновити")
            self.lbl_buf.setText("Пауза")
            self.level_bar.setValue(0)
        else:
            self.btn_answer.setText(f"  Відповісти  ·  {HOTKEY.upper()}")
        self.refresh_banner()

    def sync_subjects(self):
        box = self.subject
        box.blockSignals(True)
        box.clear()
        for name, sid in self.ctrl.prof_mgr.list_subjects():
            box.addItem(name, sid)
        box.addItem("＋ Додати предмет…", "__add__")
        idx = box.findData(self.ctrl.active_profile.get("id"))
        box.setCurrentIndex(idx if idx >= 0 else 0)
        box.blockSignals(False)

    def set_answer(self, question, who, aloud, details, dt):
        self._has_active_answer = True
        self.lbl_question.setText(f"{question}  ·  {who}" if who else (question or "—"))
        self._raw_aloud = clean_math(aloud)
        self.txt_aloud.setHtml(aloud_to_html(aloud))
        self.txt_deep.setPlainText(clean_math(details or aloud))
        self.lbl_time.setText(f"⚡ {dt:.1f} с")

    def clear_answer(self, message="—"):
        self._has_active_answer = False
        self.lbl_question.setText(message)
        self._raw_aloud = ""
        self.txt_aloud.clear()
        self.txt_deep.clear()
        self.lbl_time.setText("")


# ======================================================================
#  Панель «Пам'ять уроку та налаштування»
# ======================================================================
class SettingsPanel(QDialog):
    def __init__(self, ctrl):
        super().__init__(None)
        self.ctrl = ctrl
        self.setWindowTitle("Lesson Listener — пам'ять і налаштування")
        self.setWindowIcon(T.app_icon())
        self.setMinimumSize(520, 540)
        self.resize(580, 720)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(ctrl.cfg.get("topmost", True)))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        lay.addWidget(self.tabs)
        self.tabs.addTab(self._scroll(self._build_memory_tab()), "Пам'ять уроку")
        self.tabs.addTab(self._scroll(self._build_schedule_tab()), "📅 Розклад")
        self.tabs.addTab(self._scroll(self._build_settings_tab()), "Налаштування")

    # ---------------- helpers
    @staticmethod
    def _scroll(inner):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(inner)
        return sa

    @staticmethod
    def _section(text):
        lab = QLabel(text.upper())
        lab.setObjectName("Section")
        return lab

    @staticmethod
    def _card():
        f = QFrame()
        f.setObjectName("Inner")
        return f

    def _toggle_row(self, layout, text, key, default=True):
        row = QHBoxLayout()
        lab = QLabel(text)
        lab.setWordWrap(True)
        row.addWidget(lab, 1)
        tg = T.Toggle(bool(self.ctrl.cfg.get(key, default)))
        tg.toggled.connect(lambda v, k=key: self.ctrl.set_option(k, bool(v)))
        row.addWidget(tg)
        layout.addLayout(row)
        return tg

    # ---------------- вкладка «Пам'ять»
    def _build_memory_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 12, 18, 16)
        v.setSpacing(8)

        v.addWidget(self._section("Поточний урок"))
        live = self._card()
        g = QGridLayout(live)
        g.setContentsMargins(14, 12, 14, 12)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(8)
        self.lv = {}
        for r, (key, label) in enumerate([("topic", "Тема"), ("style", "Стиль вчителя"),
                                          ("terms", "Терміни"), ("events", "Хід уроку")]):
            k = QLabel(label)
            k.setObjectName("Muted")
            val = QLabel("—")
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(k, r, 0, Qt.AlignmentFlag.AlignTop)
            g.addWidget(val, r, 1)
            self.lv[key] = val
        g.setColumnStretch(1, 1)
        v.addWidget(live)

        v.addWidget(self._section("Конспект та записи уроку"))
        card_notes = self._card()
        cn_lay = QHBoxLayout(card_notes)
        cn_lay.setContentsMargins(14, 10, 14, 10)
        cn_lay.setSpacing(10)
        btn_open_lec = QPushButton("📄 Відкрити конспект уроку")
        btn_open_lec.setObjectName("Primary")
        btn_open_lec.clicked.connect(lambda *_: self.ctrl.open_current_lecture())
        cn_lay.addWidget(btn_open_lec)
        btn_open_dir = QPushButton("📁 Папка всіх конспектів (lectures/)")
        btn_open_dir.setObjectName("Soft")
        btn_open_dir.clicked.connect(lambda *_: self.ctrl.open_lectures_folder())
        cn_lay.addWidget(btn_open_dir)
        v.addWidget(card_notes)

        v.addWidget(self._section("Викладач та постійні вимоги"))
        card = self._card()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 12)
        cv.setSpacing(8)
        row = QHBoxLayout()
        self.ent_teacher = QLineEdit()
        self.ent_teacher.setPlaceholderText("Ім'я викладача")
        row.addWidget(self.ent_teacher, 1)
        btn_t = QPushButton("Зберегти ім'я")
        btn_t.setObjectName("Soft")
        btn_t.clicked.connect(lambda *_: self.ctrl.save_teacher_name(self.ent_teacher.text()))
        row.addWidget(btn_t)
        btn_imp = QPushButton("📋 Імпорт зі Щоденника")
        btn_imp.setObjectName("Accent")
        btn_imp.clicked.connect(self._open_import_teachers_dialog)
        row.addWidget(btn_imp)
        cv.addLayout(row)

        row_m = QHBoxLayout()
        self.ent_meet = QLineEdit()
        self.ent_meet.setPlaceholderText("Google Meet уроку (https://meet.google.com/...)")
        row_m.addWidget(self.ent_meet, 1)
        btn_m_save = QPushButton("Зберегти Meet")
        btn_m_save.setObjectName("Soft")
        btn_m_save.clicked.connect(lambda *_: self.ctrl.save_meet_url(self.ent_meet.text()))
        row_m.addWidget(btn_m_save)
        btn_m_open = QPushButton("🚀 Відкрити")
        btn_m_open.setObjectName("Accent")
        btn_m_open.clicked.connect(lambda *_: self.ctrl.open_current_meet(force=True))
        row_m.addWidget(btn_m_open)
        cv.addLayout(row_m)
        self.txt_rules = QPlainTextEdit()
        self.txt_rules.setPlaceholderText("Правила та стиль відповідей — кожне з нового рядка")
        self.txt_rules.setFixedHeight(88)
        cv.addWidget(self.txt_rules)
        btn_r = QPushButton("Зберегти правила")
        btn_r.setObjectName("Soft")
        btn_r.clicked.connect(lambda *_: self.ctrl.save_rules(self.txt_rules.toPlainText()))
        cv.addWidget(btn_r, 0, Qt.AlignmentFlag.AlignRight)
        v.addWidget(card)

        v.addWidget(self._section("Архів минулих уроків"))
        row2 = QHBoxLayout()
        b_arch = QPushButton("  Зберегти поточний урок")
        b_arch.setObjectName("Soft")
        b_arch.setIcon(T.glyph_icon("save", T.MUTED_LIGHT, size=14))
        b_arch.clicked.connect(lambda *_: self.ctrl.on_archive_lesson())
        row2.addWidget(b_arch)
        row2.addStretch(1)
        b_clear = QPushButton("  Очистити архів")
        b_clear.setObjectName("Danger")
        b_clear.setIcon(T.glyph_icon("delete", T.ERR, size=14))
        b_clear.clicked.connect(lambda *_: self.ctrl.on_clear_archive())
        row2.addWidget(b_clear)
        v.addLayout(row2)
        self.txt_past = QTextBrowser()
        self.txt_past.setObjectName("Deep")
        self.txt_past.setMinimumHeight(170)
        v.addWidget(self.txt_past, 1)
        return page

    def _open_import_teachers_dialog(self):
        from PySide6.QtWidgets import QDialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Імпорт вчителів зі списку Щоденника")
        dlg.setFixedSize(540, 430)
        dv = QVBoxLayout(dlg)
        dv.setContentsMargins(16, 16, 16, 16)
        dv.setSpacing(10)

        lbl = QLabel("Вставте текст таблиці зі щоденника («Список вчителів та предметів»):")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-weight:600; color:{T.TEXT};")
        dv.addWidget(lbl)

        txt = QPlainTextEdit()
        txt.setPlaceholderText("Предмет\nВчитель\nАлгебра\nЗмійова Тетяна Вікторівна\nБіологія\nЛопух Людмила Леонідівна\n...")
        dv.addWidget(txt, 1)

        btn_box = QHBoxLayout()
        btn_run = QPushButton("  Розпарсити та зберегти")
        btn_run.setObjectName("Accent")
        btn_run.setIcon(T.glyph_icon("save", "#ffffff", size=14))
        btn_cancel = QPushButton("Скасувати")
        btn_cancel.setObjectName("Soft")
        btn_cancel.clicked.connect(dlg.reject)

        def _do_parse():
            raw = txt.toPlainText().strip()
            if not raw:
                return
            pairs = parse_teacher_text_to_pairs(raw)
            if not pairs:
                self.ctrl.set_status("⚠️ Не вдалося розпізнати пари Предмет-Вчитель", warn=True)
                return
            res = self.ctrl.prof_mgr.apply_teacher_pairs(pairs)
            self.load_profile_fields()
            self.ctrl.widget.sync_subjects()
            self.ctrl.set_status(f"✅ Успішно оновлено {len(res)} вчителів із щоденника!")
            dlg.accept()

        btn_run.clicked.connect(_do_parse)
        btn_box.addStretch(1)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_run)
        dv.addLayout(btn_box)
        dlg.exec()

    # ---------------- вкладка «📅 Розклад»
    def _update_sched_status_lbl(self):
        if hasattr(self, "lbl_sched_status") and hasattr(self.ctrl, "schedule_mgr"):
            st = self.ctrl.schedule_mgr.get_status()
            if not st.get("enabled", True):
                self.lbl_sched_status.setText("Розклад вимкнено в налаштуваннях")
                self.lbl_sched_status.setStyleSheet(f"color:{T.MUTED}; font-size:14px; font-weight:600;")
            elif st.get("is_recess"):
                self.lbl_sched_status.setText(st.get("status_text", "☕ Перерва"))
                self.lbl_sched_status.setStyleSheet(f"color:{T.WARN}; font-size:14px; font-weight:600;")
            else:
                self.lbl_sched_status.setText(st.get("status_text", "Триває урок"))
                self.lbl_sched_status.setStyleSheet(f"color:{T.OK}; font-size:14px; font-weight:600;")

    def _build_schedule_tab(self):
        c = self.ctrl
        sm = c.schedule_mgr
        data = sm.data
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 12, 18, 16)
        v.setSpacing(12)

        # 1. Поточний статус
        v.addWidget(self._section("Поточний стан за розкладом"))
        card_st = self._card()
        cst = QVBoxLayout(card_st)
        cst.setContentsMargins(14, 12, 14, 12)
        cst.setSpacing(6)
        self.lbl_sched_status = QLabel("Завантаження статусу…")
        self.lbl_sched_status.setObjectName("Aloud")
        self.lbl_sched_status.setWordWrap(True)
        cst.addWidget(self.lbl_sched_status)
        v.addWidget(card_st)

        # 2. Тумблери автоматизації
        v.addWidget(self._section("Розумне авто-визначення уроку та перерви"))
        card_tg = self._card()
        ctg = QVBoxLayout(card_tg)
        ctg.setContentsMargins(14, 12, 14, 12)
        ctg.setSpacing(8)

        def _sched_tg(text, key, default=True):
            row = QHBoxLayout()
            lab = QLabel(text)
            lab.setWordWrap(True)
            row.addWidget(lab, 1)
            tg = T.Toggle(bool(data.get(key, default)))
            def _on_t(val):
                data[key] = bool(val)
                sm.save()
                c._check_schedule_tick()
                self._update_sched_status_lbl()
            tg.toggled.connect(_on_t)
            row.addWidget(tg)
            ctg.addLayout(row)
            return tg

        _sched_tg("Увімкнути розумний розклад дзвінків та уроків", "enabled")
        _sched_tg("Авто-вмикання перерви (☕ пауза запису на зміні)", "auto_recess")
        _sched_tg("Авто-перемикання предмета за розкладом дзвінків", "auto_switch_subject")
        _sched_tg("Авто-відкриття уроку за розкладом (Meet / Zoom / Сферум)", "auto_open_meet")
        _sched_tg("Зчитувати предмет із заголовка Meet / Zoom / браузера", "detect_from_windows")
        _sched_tg("ШІ-розпізнавання перерви та предмета з голосу вчителя", "detect_from_audio")
        _sched_tg("Голосове сповіщення про початок уроку (назва та час)", "speak_lesson_start")
        v.addWidget(card_tg)

        # 3. Розклад предметів на тиждень
        v.addWidget(self._section("Розклад предметів на тиждень (1–7 уроки)"))
        card_tt = self._card()
        ctt = QVBoxLayout(card_tt)
        ctt.setContentsMargins(14, 12, 14, 12)
        ctt.setSpacing(8)

        days_ukr = [
            ("Понеділок", "Monday"),
            ("Вівторок", "Tuesday"),
            ("Середа", "Wednesday"),
            ("Четвер", "Thursday"),
            ("П'ятниця", "Friday"),
        ]

        self.tt_day_tabs = QTabWidget()
        self.tt_day_tabs.setDocumentMode(True)
        self.combo_matrix = {}

        sub_list = [("", "— (вікно / немає уроку)")] + [(sid, name) for name, sid in c.prof_mgr.list_subjects()]

        for day_ukr, day_key in days_ukr:
            d_widget = QWidget()
            dv = QVBoxLayout(d_widget)
            dv.setContentsMargins(8, 8, 8, 8)
            dv.setSpacing(6)
            day_lessons = data.get("timetable", {}).get(day_key, [])
            for num in range(1, 9):
                h = QHBoxLayout()
                lbl_num = QLabel(f"{num}-й урок:")
                lbl_num.setFixedWidth(65)
                h.addWidget(lbl_num)
                cb = QComboBox()
                cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
                for sid, sname in sub_list:
                    cb.addItem(sname, sid)
                cur_sid = day_lessons[num - 1] if 0 <= num - 1 < len(day_lessons) else ""
                idx = cb.findData(cur_sid)
                cb.setCurrentIndex(idx if idx >= 0 else 0)
                h.addWidget(cb, 1)
                dv.addLayout(h)
                self.combo_matrix[(day_key, num)] = cb
            self.tt_day_tabs.addTab(d_widget, day_ukr)

        ctt.addWidget(self.tt_day_tabs)

        btn_save_sched = QPushButton("  Зберегти розклад уроків")
        btn_save_sched.setObjectName("Accent")
        btn_save_sched.setIcon(T.glyph_icon("save", "#ffffff", size=14))
        def _save_all_sched():
            tt = data.setdefault("timetable", {})
            for day_ukr, day_key in days_ukr:
                lessons = []
                for num in range(1, 9):
                    cb = self.combo_matrix.get((day_key, num))
                    lessons.append(cb.currentData() if cb else "")
                tt[day_key] = lessons
            sm.save()
            c.set_status("📅 Розклад уроків успішно збережено ✔")
            c._check_schedule_tick()
            self._update_sched_status_lbl()
        btn_save_sched.clicked.connect(_save_all_sched)
        ctt.addWidget(btn_save_sched, 0, Qt.AlignmentFlag.AlignRight)
        v.addWidget(card_tt)

        v.addStretch(1)
        self._update_sched_status_lbl()
        return page

    # ---------------- вкладка «Налаштування»
    def _build_settings_tab(self):
        c = self.ctrl
        cfg = c.cfg
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(18, 12, 18, 16)
        v.setSpacing(12)

        # 0. Вибір провайдера
        v.addWidget(self._section("Активний ШІ-провайдер для уроку"))
        card_p = self._card()
        cp = QVBoxLayout(card_p)
        cp.setContentsMargins(14, 12, 14, 12)
        cp.setSpacing(8)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Основний рушій"), 1)
        self.cmb_prov = QComboBox()
        for pid, pname in PROVIDERS.items():
            self.cmb_prov.addItem(pname, pid)
        idx_p = self.cmb_prov.findData(cfg.get("active_provider", "gemini"))
        self.cmb_prov.setCurrentIndex(idx_p if idx_p >= 0 else 0)
        self.cmb_prov.currentIndexChanged.connect(lambda: c.set_option("active_provider", self.cmb_prov.currentData()))
        prow.addWidget(self.cmb_prov)
        cp.addLayout(prow)
        v.addWidget(card_p)

        # 0.5. Регіон та мова суфлера
        v.addWidget(self._section("Мова підказок та регіональна адаптація"))
        card_lang = self._card()
        clang = QVBoxLayout(card_lang)
        clang.setContentsMargins(14, 12, 14, 12)
        clang.setSpacing(8)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("Мова відповідей та термінів"), 1)
        self.cmb_lang = QComboBox()
        self.cmb_lang.addItem("Авто (визначати за мовою уроку)", "auto")
        self.cmb_lang.addItem("Русский (RU) — адаптация для РФ/СНГ", "ru")
        self.cmb_lang.addItem("Українська (UK) — за замовчуванням", "uk")
        cur_lang = cfg.get("language", "auto")
        idx_lang = self.cmb_lang.findData(cur_lang)
        self.cmb_lang.setCurrentIndex(idx_lang if idx_lang >= 0 else 0)
        self.cmb_lang.currentIndexChanged.connect(lambda: c.set_option("language", self.cmb_lang.currentData()))
        lrow.addWidget(self.cmb_lang)
        clang.addLayout(lrow)
        hint_lang = QLabel("У режимі RU відповіді формуються російською, підтримуються предмети програми РФ (русский язык, литература, история, обществознание тощо).")
        hint_lang.setObjectName("Muted")
        hint_lang.setWordWrap(True)
        clang.addWidget(hint_lang)
        v.addWidget(card_lang)

        # 1. OpenRouter (Nemotron 3 Ultra 550B, Gemma 4, etc.)
        v.addWidget(self._section("OpenRouter (Nemotron 3 Ultra 550B · Gemma 4 · Free)"))
        card_or = self._card()
        cor = QVBoxLayout(card_or)
        cor.setContentsMargins(14, 12, 14, 12)
        cor.setSpacing(8)
        row_or = QHBoxLayout()
        self.ent_or_key = QLineEdit(cfg.get("openrouter_api_key", ""))
        self.ent_or_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ent_or_key.setPlaceholderText("API-ключ OpenRouter (sk-or-v1-...)")
        row_or.addWidget(self.ent_or_key, 1)
        btn_or_save = QPushButton("Зберегти")
        btn_or_save.setObjectName("Accent")
        btn_or_save.clicked.connect(lambda *_: self.ctrl.on_save_openrouter_key(self.ent_or_key.text()))
        row_or.addWidget(btn_or_save)
        cor.addLayout(row_or)
        self.lbl_or_state = QLabel()
        cor.addWidget(self.lbl_or_state)
        mrow_or = QHBoxLayout()
        mrow_or.addWidget(QLabel("Модель OpenRouter"), 1)
        self.cmb_or_model = QComboBox()
        self.cmb_or_model.setEditable(True)
        self.cmb_or_model.addItems(OPENROUTER_MODELS)
        self.cmb_or_model.setCurrentText(cfg.get("openrouter_model", OPENROUTER_MODELS[0]))
        self.cmb_or_model.setMinimumWidth(230)
        self.cmb_or_model.activated.connect(lambda *_: c.set_option("openrouter_model", self.cmb_or_model.currentText().strip()))
        self.cmb_or_model.lineEdit().editingFinished.connect(lambda: c.set_option("openrouter_model", self.cmb_or_model.currentText().strip()))
        mrow_or.addWidget(self.cmb_or_model)
        cor.addLayout(mrow_or)
        v.addWidget(card_or)

        # 2. Groq Cloud (Qwen 3.8 / Whisper Turbo)
        v.addWidget(self._section("Groq Cloud (Qwen 3.8 · Whisper Turbo < 1 с)"))
        card_gr = self._card()
        cgr = QVBoxLayout(card_gr)
        cgr.setContentsMargins(14, 12, 14, 12)
        cgr.setSpacing(8)
        row_gr = QHBoxLayout()
        self.ent_groq_key = QLineEdit(cfg.get("groq_api_key", ""))
        self.ent_groq_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ent_groq_key.setPlaceholderText("API-ключ Groq (gsk_...)")
        row_gr.addWidget(self.ent_groq_key, 1)
        btn_gr_save = QPushButton("Зберегти")
        btn_gr_save.setObjectName("Accent")
        btn_gr_save.clicked.connect(lambda *_: self.ctrl.on_save_groq_key(self.ent_groq_key.text()))
        row_gr.addWidget(btn_gr_save)
        cgr.addLayout(row_gr)
        self.lbl_groq_state = QLabel()
        cgr.addWidget(self.lbl_groq_state)
        mrow_gr = QHBoxLayout()
        mrow_gr.addWidget(QLabel("Модель Groq"), 1)
        self.cmb_groq_model = QComboBox()
        self.cmb_groq_model.setEditable(True)
        self.cmb_groq_model.addItems(GROQ_MODELS)
        self.cmb_groq_model.setCurrentText(cfg.get("groq_model", GROQ_MODELS[0]))
        self.cmb_groq_model.setMinimumWidth(230)
        self.cmb_groq_model.activated.connect(lambda *_: c.set_option("groq_model", self.cmb_groq_model.currentText().strip()))
        self.cmb_groq_model.lineEdit().editingFinished.connect(lambda: c.set_option("groq_model", self.cmb_groq_model.currentText().strip()))
        mrow_gr.addWidget(self.cmb_groq_model)
        cgr.addLayout(mrow_gr)
        v.addWidget(card_gr)

        # 3. Google Gemini (AI Studio)
        v.addWidget(self._section("Google Gemini (Пряме аудіо · 500 RPD на Lite)"))
        card = self._card()
        cv = QVBoxLayout(card)
        cv.setContentsMargins(14, 12, 14, 12)
        cv.setSpacing(8)
        row = QHBoxLayout()
        self.ent_key = QLineEdit(cfg.get("gemini_api_key", ""))
        self.ent_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ent_key.setPlaceholderText("API-ключ Gemini")
        row.addWidget(self.ent_key, 1)
        self.btn_showkey = QPushButton("Показати")
        self.btn_showkey.setObjectName("Soft")
        self.btn_showkey.setCheckable(True)
        self.btn_showkey.toggled.connect(self._on_showkey)
        row.addWidget(self.btn_showkey)
        btn_save = QPushButton("Зберегти")
        btn_save.setObjectName("Accent")
        btn_save.clicked.connect(lambda *_: self.ctrl.on_save_key(self.ent_key.text()))
        row.addWidget(btn_save)
        cv.addLayout(row)
        self.lbl_keystate = QLabel()
        cv.addWidget(self.lbl_keystate)
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Модель Gemini"), 1)
        self.cmb_model = QComboBox()
        self.cmb_model.setEditable(True)
        self.cmb_model.addItems(GEMINI_ANSWER_MODELS)
        self.cmb_model.setCurrentText(cfg.get("answer_model", GEMINI_ANSWER_MODELS[0]))
        self.cmb_model.setMinimumWidth(230)
        self.cmb_model.activated.connect(lambda *_: c.set_option("answer_model", self.cmb_model.currentText().strip()))
        self.cmb_model.lineEdit().editingFinished.connect(
            lambda: c.set_option("answer_model", self.cmb_model.currentText().strip()))
        mrow.addWidget(self.cmb_model)
        cv.addLayout(mrow)
        prow_proxy = QHBoxLayout()
        prow_proxy.addWidget(QLabel("Проксі для РФ / обходу блокувань (HTTP/SOCKS)"), 1)
        self.ent_proxy = QLineEdit(cfg.get("proxy", ""))
        self.ent_proxy.setPlaceholderText("напр. http://127.0.0.1:10809 (v2ray, clash, vpn)")
        self.ent_proxy.setMinimumWidth(230)
        self.ent_proxy.editingFinished.connect(lambda: c.set_option("proxy", self.ent_proxy.text().strip()))
        prow_proxy.addWidget(self.ent_proxy)
        cv.addLayout(prow_proxy)
        hint_ru = QLabel("ℹ️ Якщо Gemini заблоковано в РФ (код 403), суфлер автоматично перемикається на OpenRouter або Groq.")
        hint_ru.setObjectName("Muted")
        hint_ru.setWordWrap(True)
        cv.addWidget(hint_ru)
        v.addWidget(card)

        v.addWidget(self._section("Робота суфлера"))
        card2 = self._card()
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(14, 12, 14, 12)
        c2.setSpacing(10)
        self.tg_bg = self._toggle_row(c2, "Фонове прослуховування (аналіз теми й стилю кожні 30 с)",
                                      "background_listening")
        self.tg_copy = self._toggle_row(c2, "Одразу копіювати відповідь «Сказати вголос» у буфер обміну",
                                        "auto_copy_answer")
        self.tg_notify = self._toggle_row(c2, "Показувати відповідь сповіщенням у треї, коли віджет сховано",
                                          "notify_on_answer")
        self.tg_top = self._toggle_row(c2, "Віджет поверх усіх вікон", "topmost")
        self.tg_autostart = self._toggle_row(c2, "Автозапуск при старті Windows (тихий запуск у трей)", "autostart", default=True)
        self.tg_autostart.toggled.connect(lambda v: set_windows_autostart(bool(v)))
        self.tg_recess = self._toggle_row(c2, "Режим перерви (пауза звуку й ШІ на зміні)", "recess_dummy")
        self.tg_recess.toggled.connect(lambda on: c.toggle_recess(bool(on)))
        srow = QHBoxLayout()
        lab_s = QLabel("Тривалість зрізу питання")
        srow.addWidget(lab_s, 1)
        self.sld_sec = QSlider(Qt.Orientation.Horizontal)
        self.sld_sec.setRange(5, 30)
        self.sld_sec.setFixedWidth(170)
        self.lbl_sec = QLabel()
        self.lbl_sec.setMinimumWidth(36)
        self.lbl_sec.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sld_sec.valueChanged.connect(self._on_sec_changed)
        srow.addWidget(self.sld_sec)
        srow.addWidget(self.lbl_sec)
        c2.addLayout(srow)
        hint = QLabel("Рекомендовано 20–25 с: вчитель може спочатку питати інших учнів.")
        hint.setObjectName("Muted")
        c2.addWidget(hint)
        nrow = QHBoxLayout()
        nrow.addWidget(QLabel("Моє ім'я (як кличе вчитель)"), 1)
        self.ent_name = QLineEdit(cfg.get("student_name", ""))
        self.ent_name.setPlaceholderText("порожньо — остання репліка")
        self.ent_name.setFixedWidth(210)
        self.ent_name.editingFinished.connect(lambda: c.set_option("student_name", self.ent_name.text().strip()))
        nrow.addWidget(self.ent_name)
        c2.addLayout(nrow)
        btn_start_menu = QPushButton("📌 Оновити ярлик у меню «Пуск» (Start Menu)")
        btn_start_menu.setObjectName("Soft")
        btn_start_menu.setToolTip("Створює ярлик у меню Пуск Windows для запуску та закріплення на плитках")
        btn_start_menu.clicked.connect(lambda *_: self._on_recreate_shortcut())
        c2.addWidget(btn_start_menu)
        v.addWidget(card2)

        v.addWidget(self._section("Вигляд"))
        card3 = self._card()
        c3 = QHBoxLayout(card3)
        c3.setContentsMargins(14, 12, 14, 12)
        c3.addWidget(QLabel("Прозорість віджета"), 1)
        self.sld_op = QSlider(Qt.Orientation.Horizontal)
        self.sld_op.setRange(50, 100)
        self.sld_op.setFixedWidth(170)
        self.lbl_op = QLabel()
        self.lbl_op.setMinimumWidth(36)
        self.lbl_op.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.sld_op.valueChanged.connect(self._on_opacity_changed)
        c3.addWidget(self.sld_op)
        c3.addWidget(self.lbl_op)
        v.addWidget(card3)

        v.addWidget(self._section("Джерело звуку"))
        card4 = self._card()
        c4 = QHBoxLayout(card4)
        c4.setContentsMargins(14, 12, 14, 12)
        self.cmb_dev = QComboBox()
        self.cmb_dev.activated.connect(lambda idx: c.start_source(idx))
        c4.addWidget(self.cmb_dev, 1)
        b_scan = QPushButton("Оновити")
        b_scan.setObjectName("Soft")
        b_scan.clicked.connect(lambda *_: c.refresh_sources())
        c4.addWidget(b_scan)
        v.addWidget(card4)

        btn_route = QPushButton("🎛️ Розділити звук у Windows (Chrome ➔ Кабель, Discord ➔ Динаміки)")
        btn_route.setObjectName("Soft")
        btn_route.setToolTip("Відкриває системні налаштування виводу додатків Windows для 100% ізоляції від Discord")
        btn_route.clicked.connect(lambda *_: open_windows_sound_routing())
        v.addWidget(btn_route)

        v.addStretch(1)
        return page

    # ---------------- обробники
    def _on_showkey(self, on):
        self.ent_key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self.btn_showkey.setText("Сховати" if on else "Показати")

    def _on_sec_changed(self, v):
        self.lbl_sec.setText(f"{v} с")
        self.ctrl.set_option("listen_seconds", int(v))

    def _on_opacity_changed(self, v):
        self.lbl_op.setText(f"{v}%")
        self.ctrl.set_option("widget_opacity", int(v))

    def _on_recreate_shortcut(self):
        res = ensure_start_menu_shortcut()
        if res:
            self.ctrl.set_status("Ярлик у меню «Пуск» створено ✔")
        else:
            self.ctrl.set_status("Не вдалося створити ярлик у меню «Пуск»", warn=True)

    # ---------------- оновлення вмісту
    def refresh_all(self):
        self.refresh_live()
        self.load_profile_fields()
        self.sync_options()
        self.refresh_key_state()
        self.refresh_devices()
        self._update_sched_status_lbl()

    def refresh_live(self):
        ctx = self.ctrl.context
        self.lv["topic"].setText(ctx.topic)
        self.lv["style"].setText(ctx.teacher_style)
        self.lv["terms"].setText(", ".join(ctx.key_terms) if ctx.key_terms else "поки не зафіксовано")
        self.lv["events"].setText(ctx.recent_events[-1] if ctx.recent_events else "Початок уроку")

    def load_profile_fields(self):
        prof = self.ctrl.active_profile
        self.ent_teacher.setText(prof.get("teacher_name", ""))
        if hasattr(self, "ent_meet"):
            self.ent_meet.setText(prof.get("meet_url", ""))
        self.txt_rules.setPlainText("\n".join(prof.get("permanent_rules", [])) or prof.get("teacher_style", ""))
        self.refresh_archive()

    def refresh_archive(self):
        past = self.ctrl.active_profile.get("past_lessons", [])
        if not past:
            self.txt_past.setPlainText("В архіві поки немає збережених уроків.\n"
                                       "Натисни «Зберегти поточний урок» наприкінці пари.")
            return
        lines = []
        for item in reversed(past):
            lines.append(f"{item.get('date', '—')}  ·  {item.get('topic', '—')}\n{item.get('summary', '—')}\n")
        self.txt_past.setPlainText("\n".join(lines))

    def sync_options(self):
        cfg = self.ctrl.cfg
        for tg, key, default in ((self.tg_bg, "background_listening", True), (self.tg_copy, "auto_copy_answer", True),
                                 (self.tg_notify, "notify_on_answer", True), (self.tg_top, "topmost", True)):
            tg.blockSignals(True)
            tg.setChecked(bool(cfg.get(key, default)))
            tg.blockSignals(False)
            tg.update()
        self.tg_recess.blockSignals(True)
        self.tg_recess.setChecked(bool(getattr(self.ctrl, "is_recess", False)))
        self.tg_recess.blockSignals(False)
        self.tg_recess.update()
        self.sld_sec.blockSignals(True)
        self.sld_sec.setValue(int(cfg.get("listen_seconds", 25)))
        self.sld_sec.blockSignals(False)
        self.lbl_sec.setText(f"{self.sld_sec.value()} с")
        self.sld_op.blockSignals(True)
        self.sld_op.setValue(int(cfg.get("widget_opacity", 100)))
        self.sld_op.blockSignals(False)
        self.lbl_op.setText(f"{self.sld_op.value()}%")
        if hasattr(self, "cmb_lang"):
            self.cmb_lang.blockSignals(True)
            idx_l = self.cmb_lang.findData(cfg.get("language", "auto"))
            self.cmb_lang.setCurrentIndex(idx_l if idx_l >= 0 else 0)
            self.cmb_lang.blockSignals(False)
        if hasattr(self, "ent_proxy"):
            self.ent_proxy.setText(cfg.get("proxy", ""))
        if hasattr(self, "tg_autostart"):
            self.tg_autostart.blockSignals(True)
            self.tg_autostart.setChecked(is_windows_autostart_enabled())
            self.tg_autostart.blockSignals(False)
            self.tg_autostart.update()

    def refresh_key_state(self):
        gemini_ok = bool(self.ctrl.cfg.get("gemini_api_key"))
        or_ok = bool(self.ctrl.cfg.get("openrouter_api_key"))
        groq_ok = bool(self.ctrl.cfg.get("groq_api_key"))

        self.lbl_keystate.setText("✔ Ключ активний (500 RPD доступно)" if gemini_ok else "⚠️ Ключ не задано")
        self.lbl_keystate.setStyleSheet(f"color:{T.OK if gemini_ok else T.ERR}; font-size:12px; font-weight:600;")

        if hasattr(self, "lbl_or_state"):
            self.lbl_or_state.setText("✔ Ключ OpenRouter активний" if or_ok else "⚠️ Ключ OpenRouter не вказано")
            self.lbl_or_state.setStyleSheet(f"color:{T.OK if or_ok else T.MUTED}; font-size:12px; font-weight:600;")

        if hasattr(self, "lbl_groq_state"):
            self.lbl_groq_state.setText("✔ Ключ Groq активний" if groq_ok else "⚠️ Ключ Groq не вказано")
            self.lbl_groq_state.setStyleSheet(f"color:{T.OK if groq_ok else T.MUTED}; font-size:12px; font-weight:600;")

    def refresh_devices(self):
        cb = self.cmb_dev
        cb.blockSignals(True)
        cb.clear()
        for name, _ in self.ctrl.sources:
            cb.addItem(name)
        idx = self.ctrl.current_source_idx
        cb.setCurrentIndex(idx if 0 <= idx < cb.count() else -1)
        cb.blockSignals(False)

    def showEvent(self, e):
        super().showEvent(e)
        T.set_topmost(self, bool(self.ctrl.cfg.get("topmost", True)))


# ======================================================================
#  Контролер застосунку (логіка + трей)
# ======================================================================
class LessonListenerApp(QObject):
    def __init__(self, show: bool = True, audio: bool = True):
        super().__init__()
        self.cfg = load_config()
        self.audio_enabled = audio
        self.prof_mgr = ProfileManager()
        self.active_profile = self.prof_mgr.get(self.cfg.get("active_subject", "algebra"))
        self.context = LessonContext(self.active_profile)
        self.rec = UniversalRecorder()
        self.gemini = GeminiEngine(self.cfg)
        self.gemini.thinking = self.cfg.get("thinking_level") or None
        self.busy = False
        self.is_recess = False
        self.bg_active = True
        self.sources = []
        self.current_source_idx = -1
        self.panel = None
        self.tray = None
        self._down = False

        self.bridge = Bridge()
        self.bridge.answer_ready.connect(self._apply_answer)
        self.bridge.worker_error.connect(self._on_worker_error)
        self.bridge.bg_updated.connect(self._refresh_live_views)
        self.bridge.hotkey.connect(self.on_trigger_answer)
        self.bridge.snip_hotkey.connect(self.start_screen_snip)
        self.bridge.slide_detected.connect(self.on_slide_detected)
        self._snipper = None

        self.lecture_mgr = LectureManager()
        self._last_slide_prompt_time = 0.0
        self._last_slide_topic = ""
        self._current_slide_desc = ""

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(lambda: save_config(self.cfg))

        self.widget = FloatingWidget(self)
        self.widget.sync_subjects()
        self.widget.refresh_banner()
        self._build_tray()
        self._refresh_state()

        self.schedule_mgr = ScheduleManager()
        self._auto_recess_active = False
        self._opened_meets_today = set()
        self._spoken_lessons_today = set()
        try:
            ensure_start_menu_shortcut()
            if self.cfg.get("autostart", True):
                set_windows_autostart(True)
        except Exception:
            pass

        self._sched_timer = QTimer(self)
        self._sched_timer.setInterval(5000)
        self._sched_timer.timeout.connect(self._check_schedule_tick)
        self._sched_timer.start()
        QTimer.singleShot(1000, self._check_schedule_tick)

        self.level_timer = QTimer(self)
        self.level_timer.setInterval(80)
        self.level_timer.timeout.connect(self._tick)
        self.level_timer.start()

        if audio:
            self.widget.set_status("Шукаю звукові пристрої…")
            self.setup_hotkey()
            QTimer.singleShot(0, self._init_audio)
            threading.Thread(target=self._background_listener_loop, daemon=True).start()
        else:
            self.widget.set_status("Звук вимкнено")
        if show:
            self.widget.show_widget()

    # ------------------------------------------------------------ допоміжне
    def save_soon(self):
        self._save_timer.start()

    def set_status(self, text, warn=False):
        self.widget.set_status(text, warn)

    def set_option(self, key, value):
        self.cfg[key] = value
        if hasattr(self.gemini, "cfg"):
            self.gemini.cfg[key] = value
        if key == "student_name":
            self.gemini.student_name = value
        elif key == "answer_model" and value:
            self.gemini.answer_model = value
        elif key == "topmost":
            self.set_topmost(bool(value))
            return
        elif key == "widget_opacity":
            self.widget.setWindowOpacity(max(0.5, min(1.0, value / 100.0)))
        self.save_soon()

    def set_topmost(self, on):
        self.cfg["topmost"] = bool(on)
        self.save_soon()
        T.set_topmost(self.widget, on)
        self.widget.btn_pin.blockSignals(True)
        self.widget.btn_pin.setChecked(on)
        self.widget.btn_pin.blockSignals(False)
        if self.panel is not None:
            if self.panel.isVisible():
                T.set_topmost(self.panel, on)
            self.panel.sync_options()
        if self.tray is not None:
            self.act_top.blockSignals(True)
            self.act_top.setChecked(on)
            self.act_top.blockSignals(False)

    def _refresh_state(self):
        if self.is_recess:
            st = "recess"
        elif not self.gemini.is_ready():
            st = "nokey"
        elif self.busy:
            st = "busy"
        elif self.audio_enabled and self.current_source_idx < 0:
            st = "nosrc"
        else:
            st = "ok"
        self.widget.set_state(st)
        if self.tray is not None:
            text, _, tray_col = STATE_INFO[st]
            self.tray.setIcon(T.tray_icon(tray_col))
            self.tray.setToolTip(f"Lesson Listener — {self.active_profile.get('name', '')}\n{text}")

    def toggle_recess(self, on: bool = None):
        if on is None:
            on = not self.is_recess
        self.is_recess = bool(on)
        self.widget.set_recess(self.is_recess)
        if hasattr(self, "act_recess") and self.act_recess is not None:
            self.act_recess.blockSignals(True)
            self.act_recess.setChecked(self.is_recess)
            self.act_recess.blockSignals(False)
        if self.panel is not None and hasattr(self.panel, "tg_recess"):
            self.panel.tg_recess.blockSignals(True)
            self.panel.tg_recess.setChecked(self.is_recess)
            self.panel.tg_recess.blockSignals(False)
        self._refresh_state()
        if self.is_recess:
            logging.info("Увімкнено режим перерви (пауза слухання).")
            self.set_status("☕ Перерва: прослуховування призупинено (квота не витрачається)")
            if self.tray is not None and not self.widget.isVisible():
                self.tray.showMessage("Lesson Listener", "☕ Увімкнено режим перерви: звук і ШІ на паузі",
                                      QSystemTrayIcon.MessageIcon.Information, 2500)
        else:
            self.rec.clear()
            logging.info("Перерву завершено — урок відновлено.")
            self.set_status("▶️ Урок відновлено · Буфер звуку очищено, слухаю вчителя")
            if self.tray is not None and not self.widget.isVisible():
                self.tray.showMessage("Lesson Listener", "▶️ Урок відновлено: суфлер слухає вчителя",
                                      QSystemTrayIcon.MessageIcon.Information, 2500)

    def _check_schedule_tick(self):
        if not hasattr(self, "schedule_mgr") or not self.schedule_mgr.data.get("enabled", True):
            return

        st = self.schedule_mgr.get_status(prof_mgr=self.prof_mgr)
        if not st.get("enabled", True):
            return

        # 1. Авто-вмикання / вимикання перерви за розкладом дзвінків
        if self.schedule_mgr.data.get("auto_recess", True):
            if st["is_recess"] and not self.is_recess:
                self._auto_recess_active = True
                self.toggle_recess(True)
                self.set_status(st['status_text'])
            elif not st["is_recess"] and self.is_recess and getattr(self, "_auto_recess_active", False):
                self._auto_recess_active = False
                self.toggle_recess(False)
                self.set_status(f"▶️ Урок розпочався: {st['status_text']}")

        # 2. Авто-перемикання предмета за розкладом уроків
        sched_sub = st.get("subject_id") if st.get("is_school_time") and not st.get("is_recess") else ""
        if self.schedule_mgr.data.get("auto_switch_subject", True) and not self.is_recess:
            if sched_sub and sched_sub != self.active_profile.get("id"):
                if sched_sub in self.prof_mgr.profiles:
                    logging.info(f"Авто-перемикання предмета за розкладом: {sched_sub}")
                    self.switch_subject(sched_sub)
                    self.set_status(f"📅 Розклад: {self.active_profile.get('name')}")

        # 3. Детекція предмета із вікон (тільки якщо в розкладі вікно або активний дзвінок Meet/Zoom)
        if self.schedule_mgr.data.get("detect_from_windows", True) and not self.is_recess:
            try:
                if not sched_sub:
                    # Якщо за розкладом зараз немає уроку — шукаємо по відкритих вікнах
                    win_sub = detect_subject_from_windows()
                    if win_sub and win_sub != self.active_profile.get("id") and win_sub in self.prof_mgr.profiles:
                        logging.info(f"Розпізнано предмет із вікон: {win_sub}")
                        self.switch_subject(win_sub)
                        self.set_status(f"🌐 Вікно уроку: {self.active_profile.get('name')}")
                else:
                    # Якщо урок є в розкладі, фонові вкладки (наприклад від минулих уроків)
                    # НЕ повинні перебивати розклад! Перевіряємо тільки якщо активне вікно — це дзвінок
                    fg_title = get_active_window_title()
                    tl = fg_title.lower() if fg_title else ""
                    if any(call_app in tl for call_app in ["meet", "zoom", "teams", "дистанц"]):
                        fg_sub = detect_subject_from_text(fg_title)
                        if fg_sub and fg_sub != self.active_profile.get("id") and fg_sub in self.prof_mgr.profiles:
                            logging.info(f"Активний дзвінок заміни: {fg_sub}")
                            self.switch_subject(fg_sub)
                            self.set_status(f"🌐 Дзвінок уроку: {self.active_profile.get('name')}")
            except Exception:
                pass

        # 4. Оновлення банера у віджеті та показ наступного уроку
        if hasattr(self.widget, "lbl_banner"):
            sub_name = self.active_profile.get("name", "")
            topic = self.context.topic
            time_txt = normalize_status_text(st.get("status_text", ""))
            next_txt = st.get("next_text", "")
            if self.is_recess:
                self.widget.lbl_banner.setText(time_txt or "☕ ПЕРЕРВА")
                if not getattr(self.widget, "_has_active_answer", False):
                    self.widget.lbl_question.setText(f"☕ Зараз перерва (ще {st.get('rem_min', 0)} хв)")
                    self.widget.txt_aloud.setHtml(
                        f"<div style='font-size:14px; color:#cbd5e1; line-height:1.55; font-family:system-ui, sans-serif;'>"
                        f"Зараз триває перерва.<br>"
                        f"👉 <b style='color:#38bdf8;'>Наступний урок:</b> <span style='color:#ffffff; font-weight:600;'>{next_txt}</span>"
                        f"</div>"
                    )
            elif time_txt:
                if next_txt:
                    self.widget.lbl_banner.setText(f"{sub_name} (до {st.get('end', '')}) · 👉 {next_txt}")
                else:
                    self.widget.lbl_banner.setText(f"{sub_name} · {time_txt} · {topic}")
            else:
                self.widget.lbl_banner.setText(f"{sub_name} · Тема: {topic}")

        # 5. Авто-відкриття посилання на урок (Meet / Zoom / Сферум)
        if self.schedule_mgr.data.get("auto_open_meet", True) and not self.is_recess and st.get("is_school_time"):
            self.open_current_meet(force=False)

        # 6. Голосове сповіщення про початок уроку (назва предмета і час)
        if self.schedule_mgr.data.get("speak_lesson_start", True) and not self.is_recess and st.get("is_school_time"):
            today_str = datetime.date.today().isoformat()
            sub_id = st.get("subject_id") or self.active_profile.get("id")
            lesson_num = st.get("lesson_num", 0)
            speak_key = (today_str, lesson_num, sub_id)
            if speak_key not in self._spoken_lessons_today:
                self._spoken_lessons_today.add(speak_key)
                prof = self.prof_mgr.get(sub_id) if hasattr(self, "prof_mgr") else self.active_profile
                sub_name = prof.get("name", sub_id) if prof else self.active_profile.get("name", sub_id)
                meet_url = prof.get("meet_url", "") if prof else ""

                # Якщо учень вже зайшов на урок (Meet або Zoom вже відкриті) — не озвучувати
                if is_lesson_already_open(meet_url, sub_id, sub_name):
                    logging.info(f"Учень вже зайшов на урок {sub_name} ({meet_url}). Оголошення початку уроку пропущено.")
                else:
                    # Перевіряємо, чи урок розпочався щойно (протягом останніх 3 хвилин)
                    start_str = st.get("start", "")
                    should_speak = True
                    if start_str and ":" in start_str:
                        try:
                            now = datetime.datetime.now()
                            sh, sm = map(int, start_str.split(":"))
                            diff_min = (now.hour * 60 + now.minute) - (sh * 60 + sm)
                            # Якщо урок розпочався понад 3 хвилини тому — учень вже займається, не перебиваємо
                            if diff_min > 3:
                                should_speak = False
                                logging.info(f"Урок {sub_name} розпочався {diff_min} хв тому. Оголошення початку уроку пропущено.")
                        except Exception:
                            pass

                    if should_speak:
                        start_time = st.get("start") or datetime.datetime.now().strftime("%H:%M")
                        lang = self.cfg.get("language", "auto")
                        if lang == "uk":
                            speech = f"Початок уроку: {sub_name}. Час {start_time}."
                        else:
                            speech = f"Начало урока: {sub_name}. Время {start_time}."
                        logging.info(f"Оголошення початку уроку: {speech}")
                        speak_text(speech, lang=lang)

    def _set_busy(self, busy):
        self.busy = busy
        self.widget.set_busy(busy)
        self._refresh_state()

    def _refresh_live_views(self):
        self.widget.refresh_banner()
        if self.panel is not None:
            self.panel.refresh_live()

    def _tick(self):
        if not self.widget.isVisible():
            return
        if self.is_recess:
            self.widget.lbl_buf.setText("Пауза")
            self.widget.level_bar.setValue(0)
            return
        try:
            self.widget.set_level(min(100, int(self.rec.level * 400)),
                                  min(self.rec.available_seconds(), MAX_RING_SECONDS))
            self.rec.level *= 0.8
        except Exception:
            pass

    def copy_to_clipboard(self, text: str):
        QGuiApplication.clipboard().setText(text)

    def copy_aloud_text(self):
        val = getattr(self.widget, "_raw_aloud", "").strip() or self.widget.txt_aloud.toPlainText().strip()
        if val:
            self.copy_to_clipboard(val)
            self.set_status("Скопійовано в буфер ✔")
            return True
        return False

    # ------------------------------------------------------------ трей
    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logging.warning("Системний трей недоступний — працюю без іконки.")
            return
        self.tray = QSystemTrayIcon(T.tray_icon(T.ACCENT), self)
        menu = QMenu()
        self.tray_menu = menu

        self.act_toggle = menu.addAction("Показати віджет")
        self.act_toggle.triggered.connect(lambda *_: self.widget.toggle_visibility())
        act_answer = menu.addAction(f"Відповісти зараз  ({HOTKEY.upper()})")
        act_answer.triggered.connect(lambda *_: self.on_trigger_answer())
        act_meet = menu.addAction("🌐 Відкрити Google Meet уроку")
        act_meet.triggered.connect(lambda *_: self.open_current_meet(force=True))

        self.act_recess = menu.addAction("☕ Перерва (пауза на зміні)")
        self.act_recess.setCheckable(True)
        self.act_recess.setChecked(self.is_recess)
        self.act_recess.triggered.connect(lambda on: self.toggle_recess(bool(on)))
        menu.addSeparator()

        self.menu_src = menu.addMenu("Джерело звуку")
        self.src_group = QActionGroup(self.menu_src)
        self.src_group.setExclusive(True)
        self.src_actions = {}
        for cat, _, label in CATS:
            a = self.menu_src.addAction(label)
            a.setCheckable(True)
            self.src_group.addAction(a)
            a.triggered.connect(lambda *_, k=cat: self.switch_category(k))
            self.src_actions[cat] = a

        self.menu_subj = menu.addMenu("Предмет")
        self.menu_subj.aboutToShow.connect(self._fill_subject_menu)

        act_lecture = menu.addAction("📚 Конспект уроку")
        act_lecture.triggered.connect(lambda *_: self.open_current_lecture())
        act_lectures_dir = menu.addAction("📁 Папка конспектів (lectures/)")
        act_lectures_dir.triggered.connect(lambda *_: self.open_lectures_folder())

        act_new = menu.addAction("Новий урок")
        act_new.triggered.connect(lambda *_: self.on_start_new_lesson())
        self.act_top = menu.addAction("Поверх усіх вікон")
        self.act_top.setCheckable(True)
        self.act_top.setChecked(bool(self.cfg.get("topmost", True)))
        self.act_top.toggled.connect(lambda on: self.set_topmost(bool(on)))
        menu.addSeparator()
        act_panel = menu.addAction("Пам'ять і налаштування…")
        act_panel.triggered.connect(lambda *_: self.open_panel())
        menu.addSeparator()
        act_quit = menu.addAction("Вийти")
        act_quit.triggered.connect(lambda *_: self.quit_app())
        menu.aboutToShow.connect(lambda: self.act_toggle.setText(
            "Сховати віджет" if self.widget.isVisible() else "Показати віджет"))

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.widget.toggle_visibility()

    def _fill_subject_menu(self):
        m = self.menu_subj
        m.clear()
        cur = self.active_profile.get("id")
        for name, sid in self.prof_mgr.list_subjects():
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(sid == cur)
            a.triggered.connect(lambda *_, s=sid: self._switch_to_subject(s))

    def _sync_tray_source(self, cat):
        if self.tray is not None and cat in self.src_actions:
            self.src_actions[cat].setChecked(True)

    def open_panel(self, tab=None):
        if self.panel is None:
            self.panel = SettingsPanel(self)
        self.panel.refresh_all()
        if tab is not None:
            self.panel.tabs.setCurrentIndex(tab)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    # ------------------------------------------------------------ звук
    def setup_hotkey(self):
        ok = False
        try:
            import keyboard
            keyboard.add_hotkey(HOTKEY, self.bridge.hotkey.emit)
            keyboard.add_hotkey("F9", self.bridge.snip_hotkey.emit)
            ok = True
        except Exception as e:
            logging.warning(f"Глобальну гарячу клавішу не вдалося зареєструвати: {e}")
        if not ok:
            QShortcut(QKeySequence(HOTKEY.upper()), self.widget).activated.connect(self.on_trigger_answer)
            QShortcut(QKeySequence("F9"), self.widget).activated.connect(self.start_screen_snip)

    def _init_audio(self):
        self.refresh_sources()
        idx = self._pick_source_index(self.cfg.get("audio_category", "mic"))
        if idx is None and self.sources:
            idx = 0
        if idx is not None:
            self.start_source(idx)
        else:
            self.set_status("⚠️ Не знайдено жодного джерела звуку", warn=True)
        self._refresh_state()

    def refresh_sources(self):
        try:
            self.sources = self.rec.list_sources()
        except Exception as e:
            logging.error(f"Не вдалося отримати список джерел звуку: {e}", exc_info=True)
            self.sources = []
        cur_name = self.cfg.get("audio_device", "")
        self.current_source_idx = next((i for i, (n, _) in enumerate(self.sources) if n == cur_name), -1) \
            if self.rec.pa_stream is not None or self.rec.proctap_stream is not None else -1
        if self.panel is not None:
            self.panel.refresh_devices()

    def _pick_source_index(self, cat):
        saved = self.cfg.get("audio_device", "")
        first = fifine = None
        for i, (name, data) in enumerate(self.sources):
            if data.get("category") != cat:
                continue
            if name == saved:
                return i
            if first is None:
                first = i
            if cat == "mic" and fifine is None and "fifine" in name.lower():
                fifine = i
        return fifine if fifine is not None else first

    def start_source(self, idx):
        if not (0 <= idx < len(self.sources)):
            return False
        name, data = self.sources[idx]
        try:
            self.rec.start(data)
        except Exception as e:
            logging.error(f"Помилка активації аудіопристрою: {e}", exc_info=True)
            self.set_status(f"⚠️ Помилка аудіопристрою: {e}", warn=True)
            self._refresh_state()
            return False
        self.current_source_idx = idx
        cat = data.get("category", "mic")
        self.cfg["audio_category"] = cat
        self.cfg["audio_device"] = name
        self.save_soon()
        self.widget.set_category(cat)
        self._sync_tray_source(cat)
        if self.panel is not None:
            self.panel.refresh_devices()
        logging.info(f"Аудіоджерело активовано: {name}")
        self.set_status(f"Слухаю: {name}")
        self._refresh_state()
        return True

    def switch_category(self, cat):
        if cat == "browser" or not self.sources:
            self.refresh_sources()  # браузер могли відкрити вже після запуску
        idx = self._pick_source_index(cat)
        if idx is None:
            hint = " — відкрий браузер і натисни ще раз" if cat == "browser" else ""
            self.set_status(f"⚠️ Пристроїв «{CAT_LABEL.get(cat, cat)}» не знайдено{hint}", warn=True)
            self.widget.set_category(self.cfg.get("audio_category"))
            return
        if idx == self.current_source_idx:
            self.widget.set_category(cat)
            return  # те саме джерело — не скидаємо буфер
        self.start_source(idx)

    # ------------------------------------------------------------ фоновий аналіз уроку
    def _background_listener_loop(self):
        while self.bg_active:
            time.sleep(self.cfg.get("background_interval_sec", 30))
            if self.is_recess or not self.cfg.get("background_listening", True) or not self.gemini.is_ready() or self.busy:
                continue
            audio = self.rec.get_last(20)
            if len(audio) < TARGET_SR * 4:
                continue
            rms = float(np.sqrt(np.mean(audio * audio)))
            if rms < 0.0018:
                continue
            wav_data = audio_to_wav_bytes(audio, TARGET_SR)
            analysis = self.gemini.analyze_background(wav_data, self.context)
            if analysis and analysis.get("has_speech", True):
                self.context.update_from_bg(
                    topic=analysis.get("topic"),
                    style=analysis.get("teacher_style"),
                    terms=analysis.get("key_terms"),
                    summary=analysis.get("summary"),
                    transcript=analysis.get("transcript"),
                )
                self.bridge.bg_updated.emit()

                # Автоматичний запис тез у конспект уроку
                sub_id = self.active_profile.get("id", "general")
                sub_name = self.active_profile.get("name", sub_id)
                teach = self.active_profile.get("teacher_name", "")
                if analysis.get("summary"):
                    self.lecture_mgr.log_summary_entry(
                        sub_id,
                        analysis["summary"],
                        terms=analysis.get("key_terms"),
                        subject_name=sub_name,
                        teacher_name=teach
                    )

                # Детекція показу слайда / дошки викладачем (з дебаунсом)
                if analysis.get("screen_slide_shown") and not self.is_recess:
                    topic = (analysis.get("slide_topic") or "").strip().lower()
                    curr_t = time.time()
                    is_same_topic = bool(topic and topic == self._last_slide_topic)
                    time_since = curr_t - self._last_slide_prompt_time
                    if is_same_topic and time_since < 420:
                        pass  # Той самий слайд — не докучаємо щонайменше 7 хвилин
                    elif not is_same_topic and time_since < 150:
                        pass  # Не частіше ніж раз на 2.5 хвилини
                    else:
                        self._last_slide_topic = topic
                        self._last_slide_prompt_time = curr_t
                        slide_desc = analysis.get("slide_topic") or "Слайд / дошка"
                        self.bridge.slide_detected.emit(slide_desc)

                # ШІ-детекція перерви та предмета з голосу вчителя
                if hasattr(self, "schedule_mgr") and self.schedule_mgr.data.get("detect_from_audio", True):
                    if analysis.get("is_recess") is True and not self.is_recess:
                        logging.info("ШІ розпізнав оголошення перерви вчителем!")
                        self._auto_recess_active = True
                        QTimer.singleShot(0, lambda: self.toggle_recess(True))
                        QTimer.singleShot(0, lambda: self.set_status("☕ ШІ: вчитель оголосив перерву · Звук на паузі"))

                    det_sub = analysis.get("detected_subject")
                    if det_sub and det_sub in self.prof_mgr.profiles and det_sub != self.active_profile.get("id"):
                        logging.info(f"ШІ розпізнав зміну предмета у звуці: {det_sub}")
                        QTimer.singleShot(0, lambda s=det_sub: self.switch_subject(s))
                        QTimer.singleShot(0, lambda: self.set_status(f"🎯 ШІ: виявлено урок {self.active_profile.get('name')}"))

    # ------------------------------------------------------------ відповіді
    def on_quick_text_answer(self, q: str) -> bool:
        q = (q or "").strip()
        if not q or self.busy:
            return False
        if not self.gemini.is_ready():
            self.set_status("⚠️ API-ключ Gemini не налаштовано — відкрий ⚙ Налаштування", warn=True)
            return False
        self._set_busy(True)
        self.set_status(f"Обдумую питання: {q}")

        def _worker():
            t0 = time.time()
            try:
                res = self.gemini.answer_text_question(q, self.context.get_prompt_context())
                dt = time.time() - t0
                if "error" in res:
                    logging.error(f"Gemini повернув помилку: {res['error']}")
                    self.bridge.worker_error.emit(res["error"])
                    return
                logging.info(f"Текстова відповідь отримана за {dt:.2f}с: {(res.get('say_aloud') or '')[:80]}...")
                self.bridge.answer_ready.emit(res, dt)
            except Exception as e:
                logging.error(f"Збій у _text_worker: {e}", exc_info=True)
                self.bridge.worker_error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def start_screen_snip(self):
        """Відкриває напівпрозорий оверлей для виділення області екрана (задачі, тесту, формули)."""
        if self.is_recess:
            self.set_status("⚠️ Зараз увімкнено перерву! Натисни ☕ або ▶️ щоб відновити урок", warn=True)
            return
        if self.busy:
            logging.warning("Скріншот скасовано: суфлер уже обробляє інший запит.")
            return
        if not self.gemini.is_ready():
            self.set_status("⚠️ API-ключ не налаштовано — відкрий ⚙ Налаштування", warn=True)
            return

        self._snipper = ScreenSnipper()
        self._snipper.snippet_captured.connect(self.on_screen_snippet_captured)
        self._snipper.show()
        self._snipper.raise_()
        self._snipper.activateWindow()

    def on_screen_snippet_captured(self, img_bytes: bytes):
        """Обробка виділеного фрагмента екрана: надсилання в Gemini Vision з урахуванням аудіо."""
        if not img_bytes:
            return
        self._set_busy(True)
        self.set_status("🔍 Аналізую виділену область екрана (завдання / тест)…")

        wav_bytes = None
        try:
            audio = self.rec.get_last(15)
            if len(audio) >= TARGET_SR * 2:
                rms = float(np.sqrt(np.mean(audio * audio)))
                if rms > 0.0015:
                    wav_bytes = audio_to_wav_bytes(audio, TARGET_SR)
        except Exception as e:
            logging.warning(f"Не вдалося зняти аудіо з буфера для Vision: {e}")

        def _worker():
            t0 = time.time()
            try:
                ctx_str = self.context.get_prompt_context()
                res = self.gemini.answer_vision_question(img_bytes, ctx_str, wav_bytes=wav_bytes)
                dt = time.time() - t0
                if "error" in res:
                    logging.error(f"Vision повернув помилку: {res['error']}")
                    self.bridge.worker_error.emit(res["error"])
                    return
                logging.info(f"Відповідь Vision отримана за {dt:.2f}с: {(res.get('say_aloud') or '')[:80]}...")
                self.bridge.answer_ready.emit(res, dt)
                # Додаємо вирізаний скріншот у конспект
                try:
                    s_id = self.active_profile.get("id", "general")
                    s_nm = self.active_profile.get("name", s_id)
                    t_nm = self.active_profile.get("teacher_name", "")
                    self.lecture_mgr.save_slide_screenshot(
                        subject_id=s_id,
                        img_bytes_or_pixmap=img_bytes,
                        description=f"Завдання з екрана (F9): {res.get('question', '')}",
                        subject_name=s_nm,
                        teacher_name=t_nm
                    )
                except Exception as le:
                    logging.warning(f"Не вдалося додати скріншот у конспект: {le}")
            except Exception as e:
                logging.error(f"Збій у vision worker: {e}", exc_info=True)
                self.bridge.worker_error.emit(str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def on_slide_detected(self, desc: str):
        if self.is_recess:
            return
        self._current_slide_desc = desc
        self.widget.show_slide_prompt(desc)

    def accept_slide_capture(self):
        """Користувач дозволив скріншот: ховаємо плашку, приховуємо вікно на 140мс, зберігаємо в конспект."""
        self.widget.hide_slide_prompt()
        desc = self._current_slide_desc or "Слайд презентації / дошка"
        sub_id = self.active_profile.get("id", "general")
        sub_name = self.active_profile.get("name", sub_id)
        teacher = self.active_profile.get("teacher_name", "")

        self.widget.hide()
        QApplication.processEvents()

        def _do_grab():
            try:
                screen = QGuiApplication.primaryScreen()
                pm = screen.grabWindow(0)
                saved = self.lecture_mgr.save_slide_screenshot(
                    subject_id=sub_id,
                    img_bytes_or_pixmap=pm,
                    description=desc,
                    subject_name=sub_name,
                    teacher_name=teacher
                )
                logging.info(f"Скріншот дошки збережено: {saved}")
            except Exception as e:
                logging.error(f"Помилка створення скріншота дошки: {e}")
            finally:
                self.widget.show()
                self.set_status("📸 Скріншот додано в конспект уроку!")

        QTimer.singleShot(140, _do_grab)

    def dismiss_slide_prompt(self):
        """Користувач відхилив скріншот: ховаємо плашку і не турбуємо до наступного слайда."""
        self.widget.hide_slide_prompt()
        self._last_slide_prompt_time = time.time()

    def open_current_lecture(self):
        sub_id = self.active_profile.get("id", "general")
        sub_name = self.active_profile.get("name", sub_id)
        teacher = self.active_profile.get("teacher_name", "")
        self.lecture_mgr.open_lecture(sub_id, sub_name, teacher)

    def open_lectures_folder(self):
        self.lecture_mgr.open_lectures_folder()

    def on_trigger_answer(self):
        if self.is_recess:
            self.set_status("⚠️ Зараз увімкнено перерву! Натисни ☕ або ▶️ щоб відновити урок", warn=True)
            return
        if self.busy:
            logging.warning(f"{HOTKEY.upper()} натиснуто, але суфлер уже генерує відповідь.")
            return
        if not self.gemini.is_ready():
            logging.warning(f"{HOTKEY.upper()} натиснуто, але API-ключ Gemini не налаштовано.")
            self.set_status("⚠️ API-ключ Gemini не налаштовано — відкрий ⚙ Налаштування", warn=True)
            return

        listen_s = self.cfg.get("listen_seconds", 25)
        audio = self.rec.get_last(listen_s)
        if len(audio) < TARGET_SR * 3:
            self.set_status("⚠️ Буфер звуку надто малий (<3 с) — зачекай, поки вчитель договорить питання", warn=True)
            return
        rms = float(np.sqrt(np.mean(audio * audio)))
        cur_src = self.sources[self.current_source_idx][0] if 0 <= self.current_source_idx < len(self.sources) else "?"
        logging.info(f"{HOTKEY.upper()} натиснуто. Джерело: {cur_src}, Довжина: {len(audio)/TARGET_SR:.1f}с, "
                     f"Гучність (RMS): {rms:.5f}")
        if rms < 0.0015:
            logging.warning(f"У звуці тиша (RMS {rms:.5f} < 0.0015).")
            self.set_status("⚠️ У звуці тиша — перевір джерело (мікрофон / браузер / динаміки)", warn=True)
            return

        # Очищуємо буфер після взяття зрізу, щоб попереднє запитання не змішувалося з новим
        self.rec.clear()
        self._set_busy(True)
        self.set_status("Розпізнаю аудіо й формулюю відповідь українською…")
        threading.Thread(target=self._audio_answer_worker, args=(audio,), daemon=True).start()

    def _audio_answer_worker(self, audio):
        t0 = time.time()
        try:
            ctx_str = self.context.get_prompt_context()
            wav_bytes = audio_to_wav_bytes(audio, TARGET_SR)
            logging.info(f"Відправка {len(wav_bytes)} байт аудіо в Gemini ({self.gemini.answer_model})...")
            res = self.gemini.answer_audio_question(wav_bytes, ctx_str)
            dt = time.time() - t0
            if "error" in res:
                logging.error(f"Gemini повернув помилку: {res['error']}")
                self.bridge.worker_error.emit(res["error"])
                return
            logging.info(
                f"Відповідь від Gemini отримана за {dt:.2f}с:\n"
                f"  ХТО: {res.get('who')}\n"
                f"  ЗАПИТАННЯ: {res.get('question')}\n"
                f"  СКАЗАТИ ВГОЛОС: {res.get('say_aloud')}\n"
                f"  ДІАЛОГ: {res.get('dialogue')}"
            )
            self.bridge.answer_ready.emit(res, dt)
        except Exception as e:
            logging.error(f"Збій у _audio_answer_worker: {e}", exc_info=True)
            self.bridge.worker_error.emit(str(e))

    def _on_worker_error(self, err_msg: str):
        self._set_busy(False)
        logging.error(f"Помилка відповіді: {err_msg}")
        self.set_status(f"⚠️ Помилка запиту: {err_msg}", warn=True)

    def _apply_answer(self, res: dict, dt: float):
        self._set_busy(False)
        q = res.get("question", "Запитання з уроку")
        who = res.get("who", "")
        dlg = res.get("dialogue", "")
        aloud = res.get("say_aloud", "")
        deep = res.get("deep", "")
        terms = res.get("terms", "")

        self.context.register_qa(q, aloud)
        # Запис у конспект уроку
        try:
            sub_id = self.active_profile.get("id", "general")
            sub_name = self.active_profile.get("name", sub_id)
            teacher = self.active_profile.get("teacher_name", "")
            self.lecture_mgr.log_qa_entry(
                subject_id=sub_id,
                question=q,
                answer=aloud,
                who=who,
                explanation=deep,
                subject_name=sub_name,
                teacher_name=teacher
            )
        except Exception as e:
            logging.warning(f"Не вдалося записати Q&A в конспект: {e}")
        details = ""
        if dlg:
            details += f"ХІД ОПИТУВАННЯ В КЛАСІ:\n{dlg}\n\n"
        if deep:
            details += f"ОБҐРУНТУВАННЯ:\n{deep}\n\n"
        if terms:
            details += f"ТЕРМІНИ:\n{terms}\n"
        self.widget.set_answer(q, who, aloud, details, dt)

        if self.cfg.get("auto_copy_answer", True) and aloud:
            self.copy_to_clipboard(aloud)
            self.set_status(f"✔ Відповідь скопійовано в буфер за {dt:.1f} с")
        else:
            self.set_status(f"Відповідь готова за {dt:.1f} с")

        if self.tray is not None and not self.widget.isVisible() and self.cfg.get("notify_on_answer", True) and aloud:
            self.tray.showMessage(q[:60] or "Відповідь", aloud, QSystemTrayIcon.MessageIcon.NoIcon, 9000)

    # ------------------------------------------------------------ уроки, предмети, пам'ять
    def on_start_new_lesson(self):
        """Чіткий поділ уроків: очищення буфера звуку і контексту."""
        self.rec.clear()
        self.context.reset_lesson()
        self._refresh_live_views()
        self.widget.clear_answer("Новий урок розпочато — очікую перше запитання вчителя")
        self.set_status("Новий урок розпочато · буфер звуку й тему скинуто")
        logging.info(f"Розпочато новий урок: {self.active_profile.get('name')}")

    def prompt_new_subject(self):
        name, ok = QInputDialog.getText(self.widget, "Новий предмет",
                                        "Назва предмета (наприклад, Зарубіжна література):")
        name = (name or "").strip()
        if ok and name:
            sub_id = re.sub(r"\W+", "_", name.lower()).strip("_") or f"subject_{int(time.time())}"
            self.prof_mgr.save({
                "id": sub_id,
                "name": name,
                "teacher_name": "",
                "teacher_style": "Академічний стиль, українська мова",
                "permanent_rules": ["Відповідати українською мовою"],
                "cumulative_terms": [],
                "past_lessons": [],
            })
            self.prof_mgr.load_all()
            self._switch_to_subject(sub_id)
        else:
            self.widget.sync_subjects()

    def switch_subject(self, sub_id: str):
        return self._switch_to_subject(sub_id)

    def _switch_to_subject(self, sub_id: str):
        self.active_profile = self.prof_mgr.get(sub_id)
        self.cfg["active_subject"] = sub_id
        save_config(self.cfg)
        # Очищуємо аудіобуфер при зміні предмета, щоб не змішувати уроки
        self.rec.clear()
        self.context.set_profile(self.active_profile)
        self.widget.sync_subjects()
        self._refresh_live_views()
        if self.panel is not None:
            self.panel.load_profile_fields()
        self._refresh_state()
        self.set_status(f"Перемкнено на: {self.active_profile.get('name')} (буфер звуку очищено)")
        logging.info(f"Перемкнено на предмет: {self.active_profile.get('name')}")

    def save_teacher_name(self, name: str):
        self.active_profile["teacher_name"] = (name or "").strip()
        self.prof_mgr.save(self.active_profile)
        self.widget.refresh_banner()
        self.set_status(f"Викладача збережено: {self.active_profile['teacher_name']} ✔")

    def save_meet_url(self, url: str):
        self.active_profile["meet_url"] = (url or "").strip()
        self.prof_mgr.save(self.active_profile)
        self.set_status("Google Meet збережено в профіль ✔")

    def open_current_meet(self, force=False):
        """Відкриває посилання (Google Meet / Zoom / Сферум) поточного уроку."""
        st = self.schedule_mgr.get_status(prof_mgr=self.prof_mgr) if hasattr(self, "schedule_mgr") else {}
        sub_id = st.get("subject_id") or self.active_profile.get("id")
        prof = self.prof_mgr.get(sub_id) if hasattr(self, "prof_mgr") else self.active_profile
        meet_url = prof.get("meet_url", "")
        if not meet_url:
            if force:
                self.set_status("⚠️ Для цього предмета ще не вказано посилання на Meet / Zoom", warn=True)
            return False

        today_key = (datetime.date.today().isoformat(), st.get("lesson_num", 0), sub_id)
        if not force and today_key in self._opened_meets_today:
            return False

        sub_name = prof.get("name", sub_id) if prof else sub_id

        # Перевірка: якщо учень вже зайшов на урок (вкладка Meet або Zoom відкрита) — не відкривати повторно
        if not force and is_lesson_already_open(meet_url, sub_id, sub_name):
            self._opened_meets_today.add(today_key)
            logging.info(f"Урок {sub_name} ({meet_url}) вже відкрито в браузері/Zoom. Повторне відкриття пропущено.")
            return False

        self._opened_meets_today.add(today_key)
        res = open_lesson_link(meet_url, self.cfg.get("chrome_profile"))
        if res:
            sub_name = prof.get("name", sub_id)
            self.set_status(f"🚀 Відкрито урок ({sub_name})")
            if self.tray is not None and not self.widget.isVisible():
                self.tray.showMessage(
                    "Lesson Listener",
                    f"Урок розпочався! Відкрито посилання: {sub_name}",
                    QSystemTrayIcon.MessageIcon.Information,
                    3500
                )
        return res

    def save_rules(self, raw: str):
        lines = [line.strip() for line in (raw or "").split("\n") if line.strip()]
        self.active_profile["permanent_rules"] = lines
        self.active_profile["teacher_style"] = lines[0] if lines else "Академічний"
        self.prof_mgr.save(self.active_profile)
        self.context.teacher_style = self.active_profile["teacher_style"]
        self._refresh_live_views()
        self.set_status("Правила вчителя збережено в профіль ✔")

    def on_archive_lesson(self):
        summary = " ".join(re.sub(r"^\[\d+:\d+:\d+\]\s*", "", e) for e in self.context.recent_events[-4:])
        if not summary:
            summary = f"Пройдено матеріал за темою {self.context.topic}"
        self.prof_mgr.add_lesson_archive(self.active_profile["id"], self.context.topic, summary)
        if self.panel is not None:
            self.panel.refresh_archive()
        self.set_status(f"Урок «{self.context.topic}» збережено в архів предмета ✔")

    def on_clear_archive(self):
        answer = QMessageBox.question(
            self.panel or self.widget, "Очищення архіву",
            f"Дійсно очистити збережені минулі уроки для «{self.active_profile.get('name')}»?")
        if answer == QMessageBox.StandardButton.Yes:
            self.active_profile["past_lessons"] = []
            self.prof_mgr.save(self.active_profile)
            if self.panel is not None:
                self.panel.refresh_archive()
            self.set_status("Архів минулих уроків очищено ✔")

    def on_save_key(self, key: str):
        key = (key or "").strip()
        self.cfg["gemini_api_key"] = key
        self.gemini.set_config(key, self.cfg.get("answer_model", "gemini-3.5-flash-lite"),
                               self.cfg.get("background_model", "gemini-3.5-flash-lite"))
        save_config(self.cfg)
        if self.panel is not None:
            self.panel.refresh_key_state()
        self._refresh_state()
        self.set_status("API-ключ Gemini збережено ✔" if key else "⚠️ Ключ порожній",
                        warn=not key)

    def on_save_openrouter_key(self, key: str):
        key = (key or "").strip()
        self.cfg["openrouter_api_key"] = key
        if hasattr(self.gemini, "cfg"):
            self.gemini.cfg["openrouter_api_key"] = key
        save_config(self.cfg)
        if self.panel is not None:
            self.panel.refresh_key_state()
        self._refresh_state()
        self.set_status("Ключ OpenRouter збережено ✔" if key else "Ключ OpenRouter очищено")

    def on_save_groq_key(self, key: str):
        key = (key or "").strip()
        self.cfg["groq_api_key"] = key
        if hasattr(self.gemini, "cfg"):
            self.gemini.cfg["groq_api_key"] = key
        save_config(self.cfg)
        if self.panel is not None:
            self.panel.refresh_key_state()
        self._refresh_state()
        self.set_status("Ключ Groq збережено ✔" if key else "Ключ Groq очищено")

    # ------------------------------------------------------------ завершення
    def shutdown(self):
        if self._down:
            return
        self._down = True
        self.bg_active = False
        try:
            save_config(self.cfg)
        except Exception:
            pass
        try:
            self.rec.stop()
        except Exception:
            pass
        if self.audio_enabled:
            try:
                import keyboard
                keyboard.unhook_all()
            except Exception:
                pass
        try:
            if self.tray is not None:
                self.tray.hide()
            self.widget.hide()
            if self.panel is not None:
                self.panel.hide()
        except Exception:
            pass

    def quit_app(self):
        self.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def on_close(self):
        self.quit_app()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # живемо в треї, поки не натиснуть «Вийти»
    app.setApplicationName("Lesson Listener")
    app.setStyle("Fusion")
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:
        pass
    app.setStyleSheet(T.build_stylesheet())
    app.setWindowIcon(T.app_icon())
    ctrl = LessonListenerApp(show="--tray" not in sys.argv)
    app.aboutToQuit.connect(ctrl.shutdown)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
