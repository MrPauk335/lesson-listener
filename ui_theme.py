"""Тема, іконки та базові віджети для Lesson Listener (PySide6)."""
import os
import tempfile

from PySide6.QtCore import (Property, QAbstractAnimation, QEasingCurve, QPropertyAnimation, QRectF, Qt)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QLabel, QSizePolicy

# ---------------------------------------------------------------- палітра
BG = "#15171d"
BG_CARD = "#1b1e27"
BG_HOVER = "#262a36"
BORDER = "#2b2f3b"
BORDER_SOFT = "#262a35"
TEXT = "#e9ebf2"
MUTED = "#8d93a6"
MUTED_LIGHT = "#aab1c6"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#7ba0ff"
ACCENT_PRESSED = "#4a77e6"
ACCENT_SOFT = "#243357"
OK = "#3ddc97"
WARN = "#ffb454"
ERR = "#ff5d6c"

# ---------------------------------------------------------------- іконки (Segoe MDL2 Assets)
ICON_FONT = "Segoe MDL2 Assets"
GLYPH = {
    "mic": "\uE720",
    "globe": "\uE774",
    "speaker": "\uE767",
    "pin": "\uE718",
    "copy": "\uE8C8",
    "gear": "\uE713",
    "minimize": "\uE921",
    "chevron_down": "\uE70D",
    "chevron_up": "\uE70E",
    "refresh": "\uE72C",
    "bolt": "\uE945",
    "delete": "\uE74D",
    "save": "\uE74E",
    "forward": "\uE72A",
    "add": "\uE710",
    "coffee": "\uEB52",
    "education": "\uE7BE",
    "pause": "\uE769",
    "play": "\uE768",
    "camera": "\uE722",
    "crop": "\uE7A8",
    "note": "\uE8A5",
    "notebook": "\uE7BC",
    "folder": "\uE8B7",
    "calendar": "\uE787",
    "close": "\uE711",
}


def glyph_icon(glyph: str, color: str = MUTED_LIGHT, color_on: str = None, size: int = 16) -> QIcon:
    """Іконка з гліфа Segoe MDL2: чіткий рендер на HiDPI, окремий колір для стану checked."""
    icon = QIcon()
    scale = 2
    for state, col in ((QIcon.State.Off, color), (QIcon.State.On, color_on or color)):
        pm = QPixmap(size * scale, size * scale)
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont(ICON_FONT)
        font.setPixelSize(size)
        p.setFont(font)
        p.setPen(QColor(col))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, GLYPH.get(glyph, glyph))
        p.end()
        icon.addPixmap(pm, QIcon.Mode.Normal, state)
        icon.addPixmap(pm, QIcon.Mode.Active, state)
    return icon


_ICON_CACHE = {}


def tray_icon(color: str = ACCENT) -> QIcon:
    """Іконка застосунку: заокруглений квадрат з градієнтом і звуковою хвилею (без залежності від шрифтів)."""
    if color in _ICON_CACHE:
        return _ICON_CACHE[color]
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64, 128):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0.0, QColor(color).lighter(125))
        grad.setColorAt(1.0, QColor(color).darker(120))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.28, s * 0.28)
        p.setBrush(QColor("#ffffff"))
        heights = [0.26, 0.50, 0.70, 0.50, 0.26]
        bw = s * 0.095
        gap = s * 0.07
        total = len(heights) * bw + (len(heights) - 1) * gap
        x = (s - total) / 2
        for h in heights:
            bh = s * h
            p.drawRoundedRect(QRectF(x, (s - bh) / 2, bw, bh), bw / 2, bw / 2)
            x += bw + gap
        p.end()
        icon.addPixmap(pm)
    _ICON_CACHE[color] = icon
    return icon


def app_icon() -> QIcon:
    return tray_icon(ACCENT)


def set_topmost(widget, on: bool):
    """Перемикає «поверх усіх вікон» через WinAPI — без миготіння й перестворення вікна."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        hwnd = wintypes.HWND(int(widget.winId()))
        after = wintypes.HWND(-1 if on else -2)  # HWND_TOPMOST / HWND_NOTOPMOST
        user32.SetWindowPos(hwnd, after, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # NOSIZE|NOMOVE|NOACTIVATE
    except Exception:
        pass


# ---------------------------------------------------------------- віджети
class Toggle(QAbstractButton):
    """Сучасний перемикач-«таблетка» з плавною анімацією."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._knob = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._on_toggled)

    def _get_knob(self):
        return self._knob

    def _set_knob(self, v):
        self._knob = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def _on_toggled(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._anim.state() == QAbstractAnimation.State.Running:
            k = self._knob
        else:
            k = 1.0 if self.isChecked() else 0.0
        off, on = QColor("#3a4052"), QColor(ACCENT)
        col = QColor(
            int(off.red() + (on.red() - off.red()) * k),
            int(off.green() + (on.green() - off.green()) * k),
            int(off.blue() + (on.blue() - off.blue()) * k),
        )
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        d = h - 6
        x = 3 + k * (w - d - 6)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, 3, d, d))


class ElidedLabel(QLabel):
    """Однорядковий напис, який обрізається з «…» і не розпирає вікно."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(10)
        self._apply()

    def setFullText(self, text: str):
        self._full = text or ""
        self.setToolTip(self._full)
        self._apply()

    def setText(self, text: str):
        self.setFullText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()

    def _apply(self):
        fm = self.fontMetrics()
        QLabel.setText(self, fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width() - 2)))


# ---------------------------------------------------------------- стилі
def _ensure_chevron() -> str:
    """Малює стрілку для випадаючих списків і повертає шлях до PNG."""
    d = os.path.join(tempfile.gettempdir(), "lesson_listener_ui")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "chevron_down.png")
    pm = QPixmap(48, 48)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(MUTED), 4.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawLine(14, 19, 24, 29)
    p.drawLine(24, 29, 34, 19)
    p.end()
    pm.save(path, "PNG")
    return path.replace("\\", "/")


def build_stylesheet() -> str:
    chev = _ensure_chevron()
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 13px; color: {TEXT}; }}
QDialog, QMessageBox, QInputDialog {{ background: {BG}; }}
QToolTip {{ background: #232634; color: {TEXT}; border: 1px solid #343949; padding: 5px 8px; }}
QLabel {{ background: transparent; }}
QLabel#Muted {{ color: {MUTED}; font-size: 12px; }}
QLabel#Question {{ color: {MUTED_LIGHT}; font-size: 12px; }}
QLabel#Status {{ color: {MUTED}; font-size: 11px; }}
QLabel#Time {{ color: {OK}; font-size: 11px; font-weight: 600; }}
QLabel#Section {{ color: {MUTED}; font-size: 11px; font-weight: 700; padding-top: 8px; }}
QFrame#Inner {{ background: {BG_CARD}; border: 1px solid {BORDER_SOFT}; border-radius: 12px; }}

QPushButton {{ background: transparent; border: none; border-radius: 8px; padding: 6px 10px; }}
QPushButton:hover {{ background: {BG_HOVER}; }}
QPushButton:disabled {{ color: {MUTED}; }}
QPushButton#Icon {{ padding: 0; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; border-radius: 7px; }}
QPushButton#Icon:checked {{ background: {ACCENT_SOFT}; }}
QPushButton#IconRecess:checked {{ background: #452405; border: 1px solid #d97706; border-radius: 7px; }}
QPushButton#Seg {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 7px 8px; color: {MUTED_LIGHT}; }}
QPushButton#Seg:hover {{ background: {BG_HOVER}; }}
QPushButton#Seg:checked {{ background: {ACCENT_SOFT}; border: 1px solid #3f5fb5; color: #ffffff; }}
QPushButton#Primary {{ background: {ACCENT}; color: #ffffff; font-size: 14px; font-weight: 600; border-radius: 12px; padding: 11px 14px; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#Primary:disabled {{ background: #2a2f3d; color: {MUTED}; }}
QPushButton#Accent {{ background: {ACCENT}; color: #ffffff; font-weight: 600; padding: 7px 14px; }}
QPushButton#Accent:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Soft {{ background: {BG_CARD}; border: 1px solid {BORDER}; padding: 7px 12px; }}
QPushButton#Soft:hover {{ background: {BG_HOVER}; }}
QPushButton#Soft:checked {{ background: {ACCENT_SOFT}; border-color: #3f5fb5; }}
QPushButton#Danger {{ background: {BG_CARD}; border: 1px solid {BORDER}; color: {ERR}; padding: 7px 12px; }}
QPushButton#Danger:hover {{ background: #2c1f26; }}
QPushButton#Ghost {{ color: {MUTED_LIGHT}; font-size: 12px; padding: 5px 9px; }}
QPushButton#Link {{ color: {MUTED}; font-size: 12px; text-align: left; padding: 2px 4px; }}
QPushButton#Link:hover {{ color: {TEXT}; background: transparent; }}
QDialogButtonBox QPushButton, QMessageBox QPushButton {{ background: {BG_CARD}; border: 1px solid {BORDER}; padding: 6px 18px; min-width: 70px; }}

QLineEdit {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 10px; selection-background-color: #2f4a8f; }}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QPlainTextEdit, QTextEdit {{ background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px 8px; selection-background-color: #2f4a8f; }}
QPlainTextEdit#Aloud, QTextBrowser#Aloud {{ background: transparent; border: none; padding: 0; font-size: 15px; font-weight: 500; color: #ffffff; }}
QTextBrowser#Deep {{ background: {BG_CARD}; border: 1px solid {BORDER_SOFT}; border-radius: 10px; padding: 8px; font-size: 12px; color: {MUTED_LIGHT}; }}

QComboBox {{ combobox-popup: 0; background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px 28px 6px 10px; min-height: 18px; }}
QComboBox:hover {{ border: 1px solid #3a4052; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url({chev}); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {BG_CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT}; selection-color: #ffffff; outline: 0; padding: 4px; }}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding-left: 6px; }}
QComboBox#Subject {{ background: transparent; border: none; font-weight: 600; font-size: 14px; padding: 4px 22px 4px 6px; }}
QComboBox#Subject:hover {{ background: {BG_HOVER}; border-radius: 8px; }}

QProgressBar {{ background: {BG_HOVER}; border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: {OK}; border-radius: 2px; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ffffff; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #343949; border-radius: 3px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #454c61; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 10px 16px; margin-right: 2px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QMenu {{ background: {BG_CARD}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
"""
