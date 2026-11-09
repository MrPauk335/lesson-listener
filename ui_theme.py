"""Тема, іконки та базові віджети для Lesson Listener (PySide6)."""
import functools
import os
import tempfile

from PySide6.QtCore import (Property, QAbstractAnimation, QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt)
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QLabel, QSizePolicy

try:
    import qtawesome as qta
except Exception:  # без QtAwesome працюємо на гліфах Segoe MDL2
    qta = None

# ---------------------------------------------------------------- палітра
BG = "#13151b"
BG_CARD = "#1a1d26"
BG_HOVER = "#252a37"
BORDER = "#2a2f3d"
BORDER_SOFT = "#232733"
TEXT = "#eceef5"
MUTED = "#8b91a5"
MUTED_LIGHT = "#aeb5c9"
ACCENT = "#5b8cff"
ACCENT_2 = "#8b5cf6"
ACCENT_HOVER = "#7ba0ff"
ACCENT_PRESSED = "#4a77e6"
ACCENT_SOFT = "#243357"
INFO = "#38bdf8"
OK = "#3ddc97"
WARN = "#ffb454"
ERR = "#ff5d6c"

# ---------------------------------------------------------------- іконки (Phosphor через QtAwesome)
QTA_ICON = {
    "mic": "ph.microphone",
    "globe": "ph.globe",
    "speaker": "ph.speaker-high",
    "pin": "ph.push-pin",
    "copy": "ph.copy",
    "gear": "ph.gear-six",
    "minimize": "ph.minus",
    "chevron_down": "ph.caret-down",
    "chevron_up": "ph.caret-up",
    "refresh": "ph.arrow-clockwise",
    "bolt": "ph.lightning",
    "delete": "ph.trash",
    "save": "ph.floppy-disk",
    "forward": "ph.paper-plane-right",
    "add": "ph.plus",
    "coffee": "ph.coffee",
    "education": "ph.graduation-cap",
    "pause": "ph.pause",
    "play": "ph.play",
    "camera": "ph.camera",
    "crop": "ph.crop",
    "note": "ph.file-text",
    "sheet": "ph.file-text",
    "notebook": "ph.notebook",
    "folder": "ph.folder-open",
    "calendar": "ph.calendar-blank",
    "close": "ph.x",
}

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


def _draw_vector_glyph(p: QPainter, glyph: str, s: float, col: str) -> bool:
    """Малює векторні іконки без залежності від шрифту (конспект-листок, зошит)."""
    pen = QPen(QColor(col), max(1.15, s * 0.088), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if glyph in ("note", "sheet"):
        # Листок паперу конспекту із загнутим правим верхнім куточком та рядками тексту
        pad = s * 0.12
        x = pad + s * 0.04
        y = pad
        w = s - pad * 2 - s * 0.08
        h = s - pad * 2
        fold = w * 0.38

        path = QPainterPath()
        path.moveTo(x, y)
        path.lineTo(x + w - fold, y)
        path.lineTo(x + w, y + fold)
        path.lineTo(x + w, y + h)
        path.lineTo(x, y + h)
        path.closeSubpath()
        p.drawPath(path)

        # Лінія загину куточка
        p.drawLine(QPointF(x + w - fold, y), QPointF(x + w - fold, y + fold))
        p.drawLine(QPointF(x + w - fold, y + fold), QPointF(x + w, y + fold))

        # Горизонтальні рядки тексту конспекту
        lx1 = x + w * 0.22
        lx2 = x + w * 0.78
        p.drawLine(QPointF(lx1, y + h * 0.44), QPointF(lx2, y + h * 0.44))
        p.drawLine(QPointF(lx1, y + h * 0.64), QPointF(lx2, y + h * 0.64))
        p.drawLine(QPointF(lx1, y + h * 0.82), QPointF(x + w * 0.58, y + h * 0.82))
        return True

    elif glyph == "notebook":
        # Зошит / блокнот із пружиною зліва та рядками тексту
        pad = s * 0.12
        x = pad + s * 0.08
        y = pad
        w = s - pad * 2 - s * 0.08
        h = s - pad * 2
        p.drawRoundedRect(QRectF(x, y, w, h), 1.5, 1.5)
        for ry in (y + h * 0.22, y + h * 0.44, y + h * 0.66, y + h * 0.86):
            p.drawLine(QPointF(x - s * 0.08, ry), QPointF(x + s * 0.05, ry))
        lx1 = x + w * 0.24
        lx2 = x + w * 0.78
        p.drawLine(QPointF(lx1, y + h * 0.32), QPointF(lx2, y + h * 0.32))
        p.drawLine(QPointF(lx1, y + h * 0.54), QPointF(lx2, y + h * 0.54))
        p.drawLine(QPointF(lx1, y + h * 0.75), QPointF(x + w * 0.60, y + h * 0.75))
        return True

    return False


@functools.lru_cache(maxsize=None)
def glyph_icon(glyph: str, color: str = MUTED_LIGHT, color_on: str = None, size: int = 16) -> QIcon:
    """Іконка (кешується): Phosphor через QtAwesome, інакше векторний рендер або гліф Segoe MDL2."""
    name = QTA_ICON.get(glyph) if qta is not None else None
    if name:
        try:
            on = color_on or color
            return qta.icon(name, color=color, color_active=color, color_on=on, color_on_active=on,
                            color_disabled=MUTED)
        except Exception:
            pass
    icon = QIcon()
    scale = 2
    for state, col in ((QIcon.State.Off, color), (QIcon.State.On, color_on or color)):
        pm = QPixmap(size * scale, size * scale)
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Спочатку перевіряємо власні чіткі векторні іконки (конспект, зошит)
        if not _draw_vector_glyph(p, glyph, size, col):
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


def set_style(widget, css: str):
    """setStyleSheet лише при зміні: кожен виклик змушує Qt заново парсити й полірувати стиль."""
    if widget.styleSheet() != css:
        widget.setStyleSheet(css)


def render_card(width: int, height: int, margin: int, radius: int, dpr: float = 1.0) -> QPixmap:
    """Попередньо малює картку вікна з м'якою тінню, щоб paintEvent лише копіював готовий піксмап."""
    pm = QPixmap(max(1, int(width * dpr)), max(1, int(height * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    card = QRectF(0, 0, width, height).adjusted(margin, margin, -margin, -margin)
    p.setPen(Qt.PenStyle.NoPen)
    for i in range(margin, 0, -1):
        a = int(22 * (1 - i / margin) ** 1.5) + 1
        p.setBrush(QColor(0, 0, 0, a))
        p.drawRoundedRect(card.adjusted(-i, -i + 2, i, i), radius + i, radius + i)
    grad = QLinearGradient(0, card.top(), 0, card.bottom())
    grad.setColorAt(0.0, QColor(BG).lighter(112))
    grad.setColorAt(0.35, QColor(BG))
    p.setBrush(grad)
    p.setPen(QPen(QColor(BORDER), 1))
    p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    p.end()
    return pm


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
QPushButton#Primary {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); color: #ffffff; font-size: 14px; font-weight: 600; border-radius: 12px; padding: 11px 14px; }}
QPushButton#Primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_HOVER}, stop:1 #a17cf8); }}
QPushButton#Primary:pressed {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_PRESSED}, stop:1 #7442e8); }}
QPushButton#Primary:disabled {{ background: #262b39; color: {MUTED}; }}
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
QPushButton#Icon:pressed, QPushButton#Seg:pressed, QPushButton#Soft:pressed, QPushButton#Ghost:pressed {{ background: #2e3445; }}
QFrame#SlideBar {{ background: rgba(56, 189, 248, 0.10); border: 1px solid rgba(56, 189, 248, 0.55); border-radius: 10px; }}
QLabel#SlideText {{ color: #f1f5f9; font-size: 11px; font-weight: 500; }}
QPushButton#SlideCapture {{ background: #0284c7; color: #ffffff; border-radius: 7px; padding: 4px 10px; font-weight: 600; font-size: 11px; }}
QPushButton#SlideCapture:hover {{ background: #0ea5e9; }}
QPushButton#SlideDismiss {{ padding: 0; border-radius: 6px; }}
QPushButton#SlideDismiss:hover {{ background: rgba(248, 113, 113, 0.18); }}
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
