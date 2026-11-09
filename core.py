"""
Lesson Listener — бекенд без GUI: аудіозахоплення, Gemini API, профілі предметів і контекст уроку.
Інтерфейс (віджет + трей на PySide6) живе в app.py.
- Повна українська локалізація (промпти, відповіді).
- Чіткий поділ уроків, очищення аудіобуфера після кожного питання (без змішування тем).
- Швидке перемикання звуку: мікрофон / браузер / динаміки.
- Миттєва відповідь через Gemini Flash-Lite (1-2 сек).
"""
import collections
import ctypes
import datetime
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import psutil
import requests
import pyaudiowpatch as pyaudio

try:
    import proctap
    HAS_PROCTAP = True
except Exception:
    HAS_PROCTAP = False

try:
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TARGET_SR = 16000
MAX_RING_SECONDS = 45
HOTKEY = "f8"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
LOG_FILE = os.path.join(BASE_DIR, "lesson.log")

# Логування
logger = logging.getLogger()
logger.setLevel(logging.INFO)
if not logger.handlers:
    fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)
logging.info("=== Запуск Lesson Listener (UKR) ===")

PROVIDERS = {
    "gemini": "Google Gemini (Пряме аудіо · 500 RPD)",
    "openrouter": "OpenRouter (Nemotron 3 Ultra 550B · Gemma 4 · Free)",
    "groq": "Groq Cloud (Qwen 3.8 · GPT-OSS · Швидкість < 1 с)",
}

GEMINI_ANSWER_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.8-flash",
    "gemini-2.5-flash",
]

OPENROUTER_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
    "cohere/north-mini-code:free",
    "liquid/lfm-3-1.7b:free",
    "recursive/flint-v1.0:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
    "dots-studio/dots-3-note-preview:free",
]

GROQ_MODELS = [
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "openai/gpt-oss-safeguard-20b",
]

ANSWER_MODELS = GEMINI_ANSWER_MODELS
BG_MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
# Безкоштовний ліміт: 15 запитів/хв на модель — при 429 стрибаємо на модель з окремою квотою
FALLBACK_CHAIN = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash"]

DEFAULT_CONFIG = {
    "active_provider": "gemini",
    "gemini_api_key": "",
    "openrouter_api_key": "",
    "groq_api_key": "",
    "answer_model": "gemini-3.5-flash-lite",
    "background_model": "gemini-3.5-flash-lite",
    "openrouter_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
    "groq_model": "qwen/qwen3.8-27b",
    "background_interval_sec": 30,
    "background_listening": True,
    "auto_copy_answer": True,
    "topmost": True,
    "listen_seconds": 25,  # вчитель може спочатку питати інших учнів
    "student_name": "",
    "active_subject": "algebra",
    "audio_category": "cable",
    "language": "auto",    # auto / ru / uk
    "proxy": "",           # HTTP/SOCKS5 прокси для обхода блокировок Gemini в РФ
    "speak_lesson_start": True,  # Озвучивать голосом начало урока, предмет и время
    "autostart": True,           # Автозапуск при старте Windows
}

LESSON_APP_EXES = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "browser.exe": "Яндекс Браузер",
    "firefox.exe": "Mozilla Firefox",
    "opera.exe": "Opera",
    "brave.exe": "Brave",
    "zen.exe": "Zen Browser",
    "zoom.exe": "Zoom Meetings",
    "yandextelemost.exe": "Яндекс Телемост",
    "vkteams.exe": "VK Teams / Сферум",
}
BROWSER_EXES = LESSON_APP_EXES


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg.update(saved)
        except Exception:
            pass
    env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if env_key and not cfg.get("gemini_api_key"):
        cfg["gemini_api_key"] = env_key
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Помилка збереження config.json: {e}")


# --------------------------------------------------------------------------
# Розклад дзвінків та уроків (Авто-визначення уроку та перерви)
# --------------------------------------------------------------------------
SCHEDULE_FILE = os.path.join(BASE_DIR, "schedule.json")

DEFAULT_BELLS = [
    {"num": 1, "start": "08:30", "end": "09:15"},
    {"num": 2, "start": "09:25", "end": "10:10"},
    {"num": 3, "start": "10:20", "end": "11:05"},
    {"num": 4, "start": "11:30", "end": "12:15"},
    {"num": 5, "start": "12:40", "end": "13:25"},
    {"num": 6, "start": "13:45", "end": "14:30"},
    {"num": 7, "start": "14:40", "end": "15:25"},
    {"num": 8, "start": "15:35", "end": "16:20"},
]

DEFAULT_TIMETABLE = {
    "Monday": ["algebra", "algebra", "technologies", "chemistry", "chemistry", "geography", "geography", ""],
    "Tuesday": ["algebra", "geometry", "physics", "world_history", "pe", "english", "english", "stem"],
    "Wednesday": ["informatics", "law", "pe", "biology", "ukr_lang", "ukr_lit", "health", ""],
    "Thursday": ["ukr_lang", "pe", "biology", "history_ua", "civic_ed", "english", "english", ""],
    "Friday": ["algebra", "geometry", "ukr_lang", "ukr_lit", "physics", "foreign_lit", "art", ""],
    "Saturday": [],
    "Sunday": [],
}

SUBJECT_KEYWORDS = {
    "algebra": ["алгебр", "algebra", "змійова", "змиева"],
    "geometry": ["геометр", "geometry", "змійова", "змиева"],
    "physics": ["фізик", "физик", "physics", "алєксандрова", "александрова"],
    "chemistry": ["хімі", "хими", "chemistry", "малишев"],
    "biology": ["біолог", "биолог", "biology", "лопух"],
    "history": ["історі", "истори", "history"],
    "world_history": ["всесвітн", "всемирн", "яковлева"],
    "history_ua": ["історія україн", "україни", "яковлева"],
    "english": ["англійськ", "английск", "english", "кукса"],
    "ukr_lang": ["українськ", "укр. мова", "укр мова", "мова", "мазко"],
    "ukr_lit": ["українськ літ", "укр. літ", "укр літ", "мазко"],
    "foreign_lit": ["зарубіжн", "зарубежн", "макаренко", "гейне"],
    "informatics": ["інформатик", "информатик", "остапенко", "програмуван", "computer science", "it"],
    "technologies": ["технолог", "трудов", "труди", "твердохліб", "проєкт", "проект"],
    "geography": ["географ", "география", "geography", "антохіна", "антохина"],
    "law": ["правознав", "право", "конституц", "закон", "юриспруд", "яковлева"],
    "civic_ed": ["громадянськ", "громадян", "демократ", "яковлева"],
    "health": ["здоров", "безпек", "добробут", "ятковська", "ятковская"],
    "art": ["мистецтв", "искусств", "нарольська", "нарольская"],
    "entrepreneurship": ["підприємництв", "фінансова грамотність", "мелікян", "бизнес", "підприєм"],
    "pe": ["фізкультур", "фіз-р", "тищенко", "спорт"],
    "stem": ["stem", "стем", "малишев", "робототехн", "інженер"],
}

SUBJECT_NAME_TO_ID = {
    "алгебра": "algebra",
    "геометрія": "geometry",
    "фізика": "physics",
    "хімія": "chemistry",
    "біологія": "biology",
    "історія україни": "history_ua",
    "всесвітня історія": "world_history",
    "історія": "history",
    "англійська мова": "english",
    "англійська": "english",
    "українська мова": "ukr_lang",
    "укр мова": "ukr_lang",
    "укр. мова": "ukr_lang",
    "українська література": "ukr_lit",
    "укр література": "ukr_lit",
    "укр. літ": "ukr_lit",
    "зарубіжна література": "foreign_lit",
    "зарубіжна": "foreign_lit",
    "інформатика": "informatics",
    "технології": "technologies",
    "трудове навчання": "technologies",
    "географія": "geography",
    "правознавство": "law",
    "основи правознавства": "law",
    "громадянська освіта": "civic_ed",
    "здоров'я, безпека та добробут": "health",
    "основи здоров'я": "health",
    "мистецтво": "art",
    "підприємництво та фінансова грамотність": "entrepreneurship",
    "фінансова грамотність": "entrepreneurship",
    "фізична культура": "pe",
    "фізкультура": "pe",
    "stem": "stem",

    # Русские названия (Школы и ВУЗы РФ)
    "русский язык": "rus_lang",
    "русский": "rus_lang",
    "рус яз": "rus_lang",
    "рус. яз": "rus_lang",
    "литература": "rus_lit",
    "русская литература": "rus_lit",
    "лит-ра": "rus_lit",
    "история россии": "history_ru",
    "история рф": "history_ru",
    "обществознание": "civic_ed",
    "общество": "civic_ed",
    "всеобщая история": "world_history",
    "всемирная история": "world_history",
    "геометрия": "geometry",
    "физика": "physics",
    "химия": "chemistry",
    "биология": "biology",
    "география": "geography",
    "английский язык": "english",
    "английский": "english",
    "инглиш": "english",
    "информатика": "informatics",
    "инфа": "informatics",
    "икт": "informatics",
    "обж": "health",
    "обзр": "health",
    "основы безопасности жизнедеятельности": "health",
    "основы безопасности и защиты родины": "health",
    "технология": "technologies",
    "труд": "technologies",
    "физическая культура": "pe",
    "музыка": "art",
    "изо": "art",
    "право": "law",
    "экономика": "entrepreneurship",
}


def parse_teacher_text_to_pairs(raw_text: str) -> list:
    """Парсить текст із електронного щоденника зі списком вчителів.
    Підтримує формат через рядок (Предмет \\n Вчитель) або з роздільниками (табуляція, двокрапка, тире)."""
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    if not lines:
        return []
    if len(lines) >= 2 and lines[0].lower().startswith("предмет") and lines[1].lower().startswith("вчитель"):
        lines = lines[2:]

    pairs = []
    i = 0
    while i < len(lines):
        line = lines[i]
        matched = False
        for sep in ["\t", " - ", " – ", " — ", ":"]:
            if sep in line:
                p1, p2 = line.split(sep, 1)
                p1, p2 = p1.strip(), p2.strip()
                if p1 and p2 and not p1.lower().startswith("предмет"):
                    pairs.append((p1, p2))
                    matched = True
                    break
        if not matched:
            if i + 1 < len(lines):
                s = lines[i]
                t = lines[i + 1]
                if not s.lower().startswith("предмет") and not t.lower().startswith("вчитель"):
                    pairs.append((s, t))
                    i += 2
                    continue
            i += 1
        else:
            i += 1
    return pairs


def detect_subject_from_text(text: str) -> str:
    """Визначає ID предмета за заголовком вкладки Meet/Zoom або словами вчителя."""
    if not text:
        return ""
    t = text.lower()
    for sub_id, kws in SUBJECT_KEYWORDS.items():
        if any(kw in t for kw in kws):
            return sub_id
    return ""


def get_active_window_title() -> str:
    """Повертає заголовок поточного активного вікна користувача в Windows."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value.strip()
    except Exception:
        return ""


def get_all_matching_window_titles() -> list:
    """Збирає заголовки активного вікна та видимих вікон (включаючи вкладки додатків)."""
    titles = []
    active = get_active_window_title()
    if active:
        titles.append(active)
    try:
        user32 = ctypes.windll.user32
        def enum_cb(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    val = buf.value.strip()
                    if val and val not in titles:
                        titles.append(val)
            return True
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception:
        pass
    return titles


def open_windows_sound_routing() -> bool:
    """Відкриває системні налаштування гучності та пристроїв Windows (App volume)."""
    try:
        os.system("start ms-settings:apps-volume")
        return True
    except Exception as e:
        logging.error(f"Помилка відкриття налаштувань звуку Windows: {e}")
        return False


def detect_subject_from_windows() -> str:
    for title in get_all_matching_window_titles():
        sub = detect_subject_from_text(title)
        if sub:
            return sub
    return ""


def find_chrome_exe() -> str:
    """Знаходить шлях до виконуваного файлу Google Chrome."""
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    w = shutil.which("chrome")
    return w or "chrome"


def find_school_chrome_profile() -> str:
    """Знаходить директорію навчального профілю Chrome (наприклад, Profile 3)."""
    local_state_path = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Local State")
    if os.path.isfile(local_state_path):
        try:
            with open(local_state_path, "r", encoding="utf-8") as f:
                ls = json.load(f)
            cache = ls.get("profile", {}).get("info_cache", {})
            for pdir, info in cache.items():
                uname = (info.get("user_name") or "").lower()
                name = (info.get("name") or "").lower()
                if "suzirya" in uname or "suzirya" in name or "учебн" in name or "school" in name:
                    return pdir
        except Exception:
            pass
    return "Profile 3"


def open_meet_in_school_profile(meet_url: str, profile_dir: str = None) -> bool:
    """Відкриває посилання Google Meet безпосередньо в навчальному профілі Chrome."""
    if not meet_url:
        return False
    chrome_exe = find_chrome_exe()
    prof = profile_dir or find_school_chrome_profile()
    try:
        cmd = [chrome_exe, f"--profile-directory={prof}", meet_url]
        subprocess.Popen(cmd)
        logging.info(f"Відкрито Google Meet у профілі {prof}: {meet_url}")
        return True
    except Exception as e:
        logging.error(f"Помилка відкриття Google Meet: {e}")
        return False


def open_lesson_link(url: str, chrome_profile: str = None) -> bool:
    """Універсальне відкриття уроку: Google Meet (у шкільному профілі Chrome), Zoom, Яндекс Телемост, Сферум."""
    if not url:
        return False
    url = url.strip()

    # 1. Google Meet -> у навчальному профілі Chrome
    if "meet.google.com" in url:
        return open_meet_in_school_profile(url, chrome_profile)

    # 2. Zoom (посилання https://zoom.us/j/... або протокол zoommtg://)
    if "zoom.us" in url or url.startswith("zoommtg://"):
        try:
            os.startfile(url)
            logging.info(f"Запущено конференцію Zoom: {url}")
            return True
        except Exception as e:
            logging.error(f"Помилка запуску Zoom: {e}")

    # 3. Яндекс Телемост / Сферум / VK Teams / сторонні посилання
    try:
        os.startfile(url)
        logging.info(f"Відкрито навчальне посилання: {url}")
        return True
    except Exception as e:
        logging.error(f"Помилка відкриття сесії {url}: {e}")
        return False


def speak_text(text: str, lang: str = "ru"):
    """Озвучує текст через Windows SAPI або PowerShell SpeechSynthesizer у фоновому потоці."""
    if not text:
        return

    def _worker():
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                sp = win32com.client.Dispatch("SAPI.SpVoice")
                target_voice = None
                for v in sp.GetVoices():
                    desc = v.GetDescription().lower()
                    if lang in ("ru", "auto") and ("irina" in desc or "russian" in desc):
                        target_voice = v
                        break
                    elif lang == "uk" and ("ukrain" in desc or "polina" in desc or "ostap" in desc):
                        target_voice = v
                        break
                if target_voice:
                    sp.Voice = target_voice
                sp.Rate = 1
                sp.Speak(text)
                return
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            logging.debug(f"SAPI voice error: {e}")

        try:
            clean_t = text.replace('"', ' ').replace("'", " ")
            cmd = f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{clean_t}')"
            subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
        except Exception as e:
            logging.warning(f"PowerShell TTS error: {e}")

    threading.Thread(target=_worker, daemon=True).start()


def get_start_menu_shortcut_path() -> str:
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Lesson Listener.lnk")


def get_startup_shortcut_path() -> str:
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup\Lesson Listener.lnk")


def create_windows_shortcut(shortcut_path: str, target: str, arguments: str = "",
                            work_dir: str = "", icon_path: str = "", description: str = "") -> bool:
    try:
        os.makedirs(os.path.dirname(shortcut_path), exist_ok=True)
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        s = shell.CreateShortcut(shortcut_path)
        s.TargetPath = target
        s.Arguments = arguments
        s.WorkingDirectory = work_dir
        if icon_path and os.path.isfile(icon_path):
            s.IconLocation = icon_path
        if description:
            s.Description = description
        s.Save()
        return True
    except Exception as e:
        logging.warning(f"Помилка створення ярлика через COM: {e}")
        try:
            esc_target = target.replace('"', '`"')
            esc_args = arguments.replace('"', '`"')
            esc_dir = work_dir.replace('"', '`"')
            esc_icon = icon_path.replace('"', '`"')
            cmd = (
                f"$ws = New-Object -ComObject WScript.Shell; "
                f"$s = $ws.CreateShortcut('{shortcut_path}'); "
                f"$s.TargetPath = '{esc_target}'; "
                f"$s.Arguments = '{esc_args}'; "
                f"$s.WorkingDirectory = '{esc_dir}'; "
            )
            if icon_path and os.path.isfile(icon_path):
                cmd += f"$s.IconLocation = '{esc_icon}'; "
            if description:
                cmd += f"$s.Description = '{description}'; "
            cmd += "$s.Save()"
            res = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=8)
            return res.returncode == 0
        except Exception as e2:
            logging.error(f"Помилка створення ярлика через PowerShell: {e2}")
            return False


def is_windows_autostart_enabled() -> bool:
    return os.path.isfile(get_startup_shortcut_path())


def set_windows_autostart(enabled: bool) -> bool:
    startup_path = get_startup_shortcut_path()
    if enabled:
        py_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        if not os.path.isfile(py_exe):
            py_exe = sys.executable
        app_py = os.path.join(BASE_DIR, "app.py")
        icon = os.path.join(BASE_DIR, "icon.ico")
        return create_windows_shortcut(
            shortcut_path=startup_path,
            target=py_exe,
            arguments=f'"{app_py}" --tray',
            work_dir=BASE_DIR,
            icon_path=icon,
            description="Lesson Listener AutoStart"
        )
    else:
        try:
            if os.path.isfile(startup_path):
                os.remove(startup_path)
            return True
        except Exception as e:
            logging.error(f"Помилка видалення ярлика автозапуску: {e}")
            return False


def ensure_start_menu_shortcut() -> bool:
    lnk_path = get_start_menu_shortcut_path()
    py_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(py_exe):
        py_exe = sys.executable
    app_py = os.path.join(BASE_DIR, "app.py")
    icon = os.path.join(BASE_DIR, "icon.ico")
    return create_windows_shortcut(
        shortcut_path=lnk_path,
        target=py_exe,
        arguments=f'"{app_py}"',
        work_dir=BASE_DIR,
        icon_path=icon,
        description="Lesson Listener — Умный AI-суфлер для онлайн-уроков"
    )


class ScheduleManager:
    """Менеджер розкладу: відстежує поточний урок та автоматично вмикає перерву за часом."""
    def __init__(self, schedule_file: str = None):
        self.file_path = schedule_file or SCHEDULE_FILE
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.isfile(self.file_path):
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        data.setdefault("speak_lesson_start", True)
                        return data
            except Exception as e:
                logging.error(f"Помилка читання schedule.json: {e}")
        default_data = {
            "enabled": True,
            "auto_switch_subject": True,
            "auto_recess": True,
            "auto_open_meet": True,
            "detect_from_windows": True,
            "detect_from_audio": True,
            "speak_lesson_start": True,
            "bells": DEFAULT_BELLS,
            "timetable": DEFAULT_TIMETABLE,
        }
        self._save(default_data)
        return default_data

    def _save(self, data: dict = None):
        if data is not None:
            self.data = data
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Помилка збереження schedule.json: {e}")

    def save(self):
        self._save()

    def get_status(self, now=None, prof_mgr=None) -> dict:
        if not self.data.get("enabled", True):
            return {"enabled": False, "is_recess": False, "status_text": ""}

        if now is None:
            now = datetime.datetime.now()

        bells = self.data.get("bells", DEFAULT_BELLS)
        timetable = self.data.get("timetable", DEFAULT_TIMETABLE)

        cur_m = now.hour * 60 + now.minute
        day_name = now.strftime("%A")
        day_lessons = timetable.get(day_name, [])

        def _sub_name(sid):
            if not sid:
                return "—"
            if prof_mgr and hasattr(prof_mgr, "get"):
                p = prof_mgr.get(sid)
                return p.get("name", sid.capitalize())
            return sid.capitalize()

        def _teacher_str(sid):
            if prof_mgr and hasattr(prof_mgr, "get"):
                p = prof_mgr.get(sid)
                t = p.get("teacher_name", "")
                if t:
                    parts = t.split()
                    short = f"{parts[0]} " + "".join(f"{pt[0]}." for pt in parts[1:]) if len(parts) > 1 else t
                    return f" ({short})"
            return ""

        # 1. Перевірка триваючого уроку
        for i, b in enumerate(bells):
            parts_s = b["start"].split(":")
            parts_e = b["end"].split(":")
            s_m = int(parts_s[0]) * 60 + int(parts_s[1])
            e_m = int(parts_e[0]) * 60 + int(parts_e[1])
            num = b.get("num", i + 1)
            if s_m <= cur_m < e_m:
                sub_id = day_lessons[num - 1] if 0 <= num - 1 < len(day_lessons) else ""
                cur_name = _sub_name(sub_id)
                rem_min = e_m - cur_m

                # Інформація про НАСТУПНИЙ урок
                next_num = num + 1
                next_sub_id = day_lessons[next_num - 1] if 0 <= next_num - 1 < len(day_lessons) else ""
                next_start = bells[i + 1]["start"] if i + 1 < len(bells) else ""
                next_name = _sub_name(next_sub_id)
                next_teach = _teacher_str(next_sub_id)
                if next_sub_id and next_start:
                    next_text = f"Далі {next_num}-й урок: {next_name}{next_teach} о {next_start}"
                elif next_start:
                    next_text = f"Далі {next_num}-й урок о {next_start}"
                else:
                    next_text = "Останній урок на сьогодні"

                return {
                    "enabled": True,
                    "is_school_time": True,
                    "is_recess": False,
                    "lesson_num": num,
                    "subject_id": sub_id,
                    "subject_name": cur_name,
                    "start": b["start"],
                    "end": b["end"],
                    "rem_min": rem_min,
                    "next_lesson_num": next_num,
                    "next_subject_id": next_sub_id,
                    "next_subject_name": next_name,
                    "next_start": next_start,
                    "next_text": next_text,
                    "status_text": f"{num}-й урок (до {b['end']}, ще {rem_min} хв) · 👉 {next_text}",
                }

        # 2. Перевірка перерв між уроками
        if not bells:
            return {"enabled": True, "is_recess": False, "status_text": ""}

        p0 = bells[0]["start"].split(":")
        first_start = int(p0[0]) * 60 + int(p0[1])
        pend = bells[-1]["end"].split(":")
        last_end = int(pend[0]) * 60 + int(pend[1])

        if cur_m < first_start:
            rem = first_start - cur_m
            next_sub = day_lessons[0] if day_lessons else ""
            n_name = _sub_name(next_sub)
            n_teach = _teacher_str(next_sub)
            return {
                "enabled": True,
                "is_school_time": True,
                "is_recess": True,
                "lesson_num": 1,
                "subject_id": next_sub,
                "subject_name": n_name,
                "recess_until": bells[0]["start"],
                "rem_min": rem,
                "next_lesson_num": 1,
                "next_subject_id": next_sub,
                "next_subject_name": n_name,
                "next_start": bells[0]["start"],
                "next_text": f"1-й урок: {n_name}{n_teach} о {bells[0]['start']}",
                "status_text": f"☕ До початку уроків (ще {rem} хв) · 👉 Наступний: 1-й {n_name}{n_teach}",
            }

        if cur_m >= last_end:
            return {
                "enabled": True,
                "is_school_time": False,
                "is_recess": True,
                "lesson_num": 0,
                "subject_id": "",
                "subject_name": "",
                "recess_until": "",
                "rem_min": 0,
                "next_lesson_num": 0,
                "next_subject_id": "",
                "next_subject_name": "",
                "next_start": "",
                "next_text": "Усі уроки завершено",
                "status_text": "🌙 Уроки на сьогодні закінчилися",
            }

        for i in range(len(bells) - 1):
            pe = bells[i]["end"].split(":")
            ns = bells[i + 1]["start"].split(":")
            prev_end = int(pe[0]) * 60 + int(pe[1])
            next_start = int(ns[0]) * 60 + int(ns[1])
            if prev_end <= cur_m < next_start:
                rem = next_start - cur_m
                next_num = bells[i + 1].get("num", i + 2)
                next_sub = day_lessons[next_num - 1] if 0 <= next_num - 1 < len(day_lessons) else ""
                n_name = _sub_name(next_sub)
                n_teach = _teacher_str(next_sub)
                next_text = f"Наступний {next_num}-й урок: {n_name}{n_teach} о {bells[i + 1]['start']}"
                return {
                    "enabled": True,
                    "is_school_time": True,
                    "is_recess": True,
                    "lesson_num": next_num,
                    "subject_id": next_sub,
                    "subject_name": n_name,
                    "recess_until": bells[i + 1]["start"],
                    "rem_min": rem,
                    "next_lesson_num": next_num,
                    "next_subject_id": next_sub,
                    "next_subject_name": n_name,
                    "next_start": bells[i + 1]["start"],
                    "next_text": next_text,
                    "status_text": f"☕ Перерва до {bells[i + 1]['start']} (ще {rem} хв) · 👉 {next_text}",
                }

        return {"enabled": True, "is_recess": False, "status_text": ""}


def preprocess_audio(audio_np: np.ndarray, sample_rate: int = TARGET_SR) -> np.ndarray:
    """Розумне очищення аудіо перед відправкою:
    - прибирає DC-зміщення;
    - стискає довгі паузи тиші (>0.45 с) та обрізає тишу по краях;
    - нормалізує тихий голос вчителя до оптимальної гучності."""
    if audio_np is None or len(audio_np) < sample_rate // 2:
        return audio_np
    x = audio_np.astype(np.float32)
    x = x - float(np.mean(x))

    win = sample_rate // 20  # 50 мс вікно
    n_frames = len(x) // win
    if n_frames > 6:
        frames = x[: n_frames * win].reshape(n_frames, win)
        rms = np.sqrt(np.mean(frames * frames, axis=1))
        noise_floor = float(np.percentile(rms, 20))
        thr = min(0.012, max(0.002, noise_floor * 1.8))
        active = rms > thr
        if np.any(active):
            # Розширюємо активні ділянки на ±3 вікна (150 мс), щоб не різати закінчення слів
            kernel = np.ones(7, dtype=np.int32)
            keep = np.convolve(active.astype(np.int32), kernel, mode="same") > 0
            # Залишаємо також короткі паузи між репліками (до 4 вікон = 200 мс)
            kept_frames = []
            silent_run = 0
            for i, is_speech in enumerate(keep):
                if is_speech:
                    silent_run = 0
                    kept_frames.append(frames[i])
                else:
                    silent_run += 1
                    if silent_run <= 4:
                        kept_frames.append(frames[i])
            if kept_frames:
                tail = x[n_frames * win :]
                x = np.concatenate(kept_frames + ([tail] if len(tail) else []))

    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if 0.004 < peak < 0.85:
        x = x * min(6.0, 0.85 / peak)
    return np.clip(x, -1.0, 1.0)


def audio_to_wav_bytes(audio_np: np.ndarray, sample_rate: int = TARGET_SR, preprocess: bool = True) -> bytes:
    if preprocess:
        audio_np = preprocess_audio(audio_np, sample_rate)
    audio_int16 = (np.clip(audio_np, -1.0, 1.0) * 32767.0).astype(np.int16)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return bio.getvalue()


# --------------------------------------------------------------------------
# Форматування математичних виразів та тексту для читання вголос
# --------------------------------------------------------------------------
def clean_math(t: str) -> str:
    """Очищає сирий LaTeX, замінює на читабельні юнікод-символи (степені, корені, дроби, грецькі літери)."""
    if not t:
        return ""
    # Видалення обгорток LaTeX
    t = re.sub(r"\\\[(.*?)\\\]", r"\1", t, flags=re.DOTALL)
    t = re.sub(r"\\\((.*?)\\\)", r"\1", t)
    t = re.sub(r"\$\$?(.*?)\$\$?", r"\1", t)

    # Дроби: \frac{a}{b} -> (a / b)
    t = re.sub(r"\\frac\{([^}]+)\}\{([^}]+)\}", r"(\1 / \2)", t)

    # Корені: \sqrt{a^2 + b^2} -> √(a² + b²)
    t = re.sub(r"\\sqrt\{([^}]+)\}", r"√(\1)", t)
    t = re.sub(r"\\sqrt([0-9a-zA-Z])", r"√\1", t)

    # Верхні індекси / степені
    sup_map = {
        "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
        "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ", "x": "ˣ", "y": "ʸ"
    }
    t = re.sub(r"\^\{([0-9+\-=()nixy]+)\}", lambda m: "".join(sup_map.get(c, c) for c in m.group(1)), t)
    t = re.sub(r"\^([0-9+\-=nixy])", lambda m: sup_map.get(m.group(1), "^" + m.group(1)), t)

    # Нижні індекси
    sub_map = {
        "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
        "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎", "a": "ₐ", "e": "ₑ", "o": "ₒ", "x": "ₓ"
    }
    t = re.sub(r"_\{([0-9+\-=()aeox]+)\}", lambda m: "".join(sub_map.get(c, c) for c in m.group(1)), t)
    t = re.sub(r"_([0-9+\-=aeox])", lambda m: sub_map.get(m.group(1), "_" + m.group(1)), t)

    # Символи та оператори
    symbols = {
        r"\\cdot": "·",
        r"\\times": "×",
        r"\\pm": "±",
        r"\\mp": "∓",
        r"\\leq": "≤",
        r"\\geq": "≥",
        r"\\neq": "≠",
        r"\\approx": "≈",
        r"\\infty": "∞",
        r"\\in": "∈",
        r"\\notin": "∉",
        r"\\subset": "⊂",
        r"\\subseteq": "⊆",
        r"\\rightarrow": "→",
        r"\\Rightarrow": "⇒",
        r"\\Delta": "Δ",
        r"\\pi": "π",
        r"\\alpha": "α",
        r"\\beta": "β",
        r"\\gamma": "γ",
    }
    for k, v in symbols.items():
        t = re.sub(k, v, t)

    # Прибирання залишкових бекслешів перед літерами
    t = re.sub(r"\\([a-zA-Z]+)", r"\1", t)
    return t


def aloud_to_html(text: str) -> str:
    """Генерує стилізований HTML для віджета суфлера з виділенням шпаргалки."""
    cleaned = clean_math(text)
    parts = re.split(r"⚡\s*Шпаргалка\s*:\s*", cleaned, maxsplit=1)
    speech_part = parts[0].strip()
    cheatsheet_part = parts[1].strip() if len(parts) > 1 else ""

    speech_html = speech_part.replace("\n", "<br>")

    if cheatsheet_part:
        return (
            f"<div style='line-height:1.45; font-size:14px; color:#f1f5f9; font-weight:500; font-family:system-ui, sans-serif;'>"
            f"{speech_html}"
            f"</div>"
            f"<div style='margin-top:8px; padding:6px 10px; background:rgba(245, 158, 11, 0.15); border-left:3px solid #f59e0b; border-radius:6px; color:#fbbf24; font-size:12.5px; font-weight:600; line-height:1.4; font-family:system-ui, sans-serif;'>"
            f"⚡ <b>Шпаргалка:</b> {cheatsheet_part}"
            f"</div>"
        )
    return f"<div style='line-height:1.45; font-size:14px; color:#f1f5f9; font-weight:500; font-family:system-ui, sans-serif;'>{speech_html}</div>"


# --------------------------------------------------------------------------
# Менеджер предметів та постійної пам'яті уроків
# --------------------------------------------------------------------------
class ProfileManager:
    def __init__(self):
        os.makedirs(PROFILES_DIR, exist_ok=True)
        self.profiles = {}
        self.load_all()

    def load_all(self):
        self.profiles = {}
        for fn in os.listdir(PROFILES_DIR):
            if fn.endswith(".json"):
                fp = os.path.join(PROFILES_DIR, fn)
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        sub_id = data.get("id", os.path.splitext(fn)[0])
                        self.profiles[sub_id] = data
                except Exception as e:
                    logging.error(f"Помилка завантаження профілю {fn}: {e}")

    def get(self, sub_id: str) -> dict:
        if sub_id not in self.profiles:
            return {
                "id": sub_id,
                "name": sub_id.capitalize(),
                "teacher_name": "",
                "teacher_style": "Академічний стиль, відповіді українською мовою",
                "permanent_rules": ["Відповідати українською мовою", "Чітко та по суті"],
                "cumulative_terms": [],
                "past_lessons": []
            }
        return self.profiles[sub_id]

    def save(self, profile: dict):
        sub_id = profile.get("id", "default")
        self.profiles[sub_id] = profile
        fp = os.path.join(PROFILES_DIR, f"{sub_id}.json")
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error(f"Помилка збереження профілю {sub_id}: {e}")

    def add_lesson_archive(self, sub_id: str, topic: str, summary: str):
        prof = self.get(sub_id)
        now_str = time.strftime("%Y-%m-%d %H:%M")
        prof.setdefault("past_lessons", []).append({
            "date": now_str,
            "topic": topic,
            "summary": summary
        })
        self.save(prof)

    def list_subjects(self):
        return [(p.get("name", k), k) for k, p in self.profiles.items()]

    def apply_teacher_pairs(self, pairs: list) -> list:
        results = []
        for sub_name, teacher_name in pairs:
            s_clean = sub_name.strip()
            t_clean = teacher_name.strip()
            sub_id = SUBJECT_NAME_TO_ID.get(s_clean.lower())
            if not sub_id:
                for k, sid in SUBJECT_NAME_TO_ID.items():
                    if k in s_clean.lower() or s_clean.lower() in k:
                        sub_id = sid
                        break
            if not sub_id:
                sub_id = re.sub(r'[^a-zA-Z0-9_]', '', s_clean.lower().replace(' ', '_')) or "custom_subject"

            prof = self.get(sub_id)
            prof["name"] = s_clean
            prof["teacher_name"] = t_clean
            self.save(prof)

            parts = t_clean.split()
            if parts:
                surname = parts[0].lower()
                kws = SUBJECT_KEYWORDS.setdefault(sub_id, [])
                if surname not in kws:
                    kws.append(surname)

            results.append({"id": sub_id, "name": s_clean, "teacher": t_clean})
        return results


# --------------------------------------------------------------------------
# Контекст поточного уроку (ізоляція та поділ)
# --------------------------------------------------------------------------
class LessonContext:
    def __init__(self, profile: dict):
        self.lock = threading.RLock()
        self.profile = profile
        self.session_start = time.time()
        self._reset_locked()

    def set_profile(self, profile: dict):
        with self.lock:
            self.profile = profile
            self.session_start = time.time()
            self._reset_locked()

    def reset_lesson(self):
        with self.lock:
            self.session_start = time.time()
            self._reset_locked()

    def _reset_locked(self):
        self.topic = "Визначається під час уроку..."
        self.teacher_style = self.profile.get("teacher_style", "Лаконічність, точні наукові терміни")
        self.key_terms = list(self.profile.get("cumulative_terms", []))[-15:]
        self.recent_events = []
        self.transcript_log = []
        self.recent_qa = []
        self.last_update_ts = 0

    def register_qa(self, question: str, answer: str):
        with self.lock:
            q = (question or "").strip()
            a = (answer or "").strip()
            if q and a:
                self.recent_qa.append((q, a))
                if len(self.recent_qa) > 3:
                    self.recent_qa.pop(0)

    def update_from_bg(self, topic, style, terms, summary, transcript):
        with self.lock:
            if topic and topic.strip():
                self.topic = topic.strip()
            if style and style.strip():
                self.teacher_style = style.strip()
            if terms:
                for t in terms:
                    t = t.strip()
                    if t and t not in self.key_terms:
                        self.key_terms.append(t)
                        if t not in self.profile.setdefault("cumulative_terms", []):
                            self.profile["cumulative_terms"].append(t)
            now_str = time.strftime("%H:%M:%S")
            if summary and summary.strip():
                self.recent_events.append(f"[{now_str}] {summary.strip()}")
                if len(self.recent_events) > 10:
                    self.recent_events.pop(0)
            if transcript and transcript.strip():
                self.transcript_log.append(f"[{now_str}] {transcript.strip()}")
                if len(self.transcript_log) > 20:
                    self.transcript_log.pop(0)
            self.last_update_ts = time.time()

    def get_prompt_context(self) -> str:
        with self.lock:
            sub_name = self.profile.get("name", "Урок")
            teacher = self.profile.get("teacher_name", "")
            teacher_str = f" (Викладач: {teacher})" if teacher else ""
            rules_str = "; ".join(self.profile.get("permanent_rules", []))
            past_str = ""
            past_lessons = self.profile.get("past_lessons", [])
            if past_lessons:
                recent_past = past_lessons[-3:]
                past_str = "\nМАТЕРІАЛ МИНУЛИХ УРОКІВ ЦЬОГО ВЧИТЕЛЯ:\n" + "\n".join(
                    [f"- [{p['date']}] Тема: {p['topic']}. Суть: {p['summary']}" for p in recent_past]
                )

            terms_str = ", ".join(self.key_terms) if self.key_terms else "поки не зафіксовано"
            events_str = "\n".join(self.recent_events[-4:]) if self.recent_events else "Початок уроку"
            qa_str = ""
            if self.recent_qa:
                qa_lines = [f"- Питання: {q} -> Відповідь: {a}" for q, a in self.recent_qa[-2:]]
                qa_str = (
                    "\nПОПЕРЕДНІ ВІДПОВІДІ НА ЦЬОМУ УРОЦІ "
                    "(лише для розуміння уточнень типу 'а чому?', 'яка формула?'; НЕ відповідай на них повторно):\n"
                    + "\n".join(qa_lines)
                )

            return (
                f"ПРЕДМЕТ: {sub_name}{teacher_str}\n"
                f"ТЕМА УРОКУ: {self.topic}\n"
                f"ВИМОГИ ТА СТИЛЬ ВЧИТЕЛЯ: {self.teacher_style}\n"
                f"ПРАВИЛА: {rules_str or 'Відповідати українською, лаконічно, без зайвої води'}\n"
                f"{past_str}\n"
                f"ТЕРМІНИ: {terms_str}\n"
                f"ХІД ЦЬОГО УРОКУ:\n{events_str}{qa_str}"
            )


# --------------------------------------------------------------------------
# Движок Gemini API (Українські промпти + Каузальний розбір опитування класу)
# --------------------------------------------------------------------------
def _student_identity_clause(student_name: str = "", is_ru: bool = False) -> str:
    name = (student_name or "").strip()
    if is_ru:
        if name:
            return (
                f"Имя нашего ученика — «{name}» (учитывай звательные и уменьшительные формы). "
                f"Если учитель обращается к «{name}» — это вопрос нашему ученику. Если имя не названо — наш ученик тот, кого спросили ПОСЛЕДНИМ."
            )
        return "Имя нашего ученика не задано, поэтому наш ученик — тот, к кому учитель обращается ПОСЛЕДНИМ в диалоге."
    else:
        if name:
            return (
                f"Ім'я нашого учня — «{name}» (враховуй кличний відмінок, зменшувальні форми та схожі на слух варіанти). "
                f"Якщо вчитель звертається до «{name}» — це запитання нашому учню. Якщо ім'я не названо — наш учень той, кого спитали ОСТАННІМ."
            )
        return "Ім'я нашого учня не задано, тому наш учень — той, до кого вчитель звертається ОСТАННІМ у діалозі."


def build_audio_prompt(context_str: str, student_name: str = "", lang: str = "auto") -> str:
    """Промпт для аудіо із обов'язковим попереднім математичним аналізом (CoT) та авто-адаптацією під мову (RU / UK)."""
    is_ru = (lang == "ru") or (lang != "uk" and any(k in context_str.lower() for k in [
        "россия", "русск", "обществознан", "литератур", "обж", "обзр", "учитель", "класс", "москва", "рф"
    ]))
    who = _student_identity_clause(student_name, is_ru=is_ru)

    if is_ru:
        return (
            "Ты — сверхточный математический и предметный суфлёр для ученика прямо на онлайн-уроке.\n"
            "В аудиозаписи звучит опрос в классе: учитель решает задачу, может спрашивать других учеников, "
            "комментировать решение и в конце обращается к нашему ученику.\n\n"
            f"КОНТЕКСТ УРОКА И ПРЕДЫДУЩИЕ ЗАДАЧИ:\n{context_str}\n\n"
            "ЖЕЛЕЗНЫЕ ПРАВИЛА РАЗБОРА И ВЫЧИСЛЕНИЙ:\n"
            "1. ЯЗЫК ОТВЕТА — СТРОГО РУССКИЙ (грамотный русский литературный язык).\n"
            f"2. {who}\n"
            "3. СТРОГАЯ ФИЛЬТРАЦИЯ DISCORD И СТОРОННИХ ЧАТОВ:\n"
            "   В аудиозаписи параллельно могут звучать голоса друзей из Discord / игр / голосового чата (разговоры, смех, подколы, ругань, посторонние темы).\n"
            "   КАТЕГОРИЧЕСКИ ИГНОРИРУЙ ЛЮБЫЕ РЕПЛИКИ И ГОЛОСА ИЗ DISCORD!\n"
            "   Твоя цель — ИСКЛЮЧИТЕЛЬНО учитель (преподаватель), который ведёт урок, объясняет материал и задаёт вопросы классу.\n"
            "   Если учитель молчит, а говорят друзья в Discord — в поле [ВОПРОС] пиши: «Учитель молчит (в Discord посторонние разговоры)», а поле [СКАЗАТЬ ВСЛУХ] оставь пустым!\n"
            "4. СНАЧАЛА ДОСЛОВНО РАСШИФРУЙ ДИАЛОГ в поле [ДИАЛОГ]. Найди последний нерешённый вопрос учителя к нашему ученику.\n"
            "5. КАТЕГОРИЧЕСКОЕ ТАБУ НА ВОДУ И ОБЩИЕ ОПРЕДЕЛЕНИЯ:\n"
            "   НИКОГДА не пиши общих фраз вроде «Для нахождения промежутков знакопостоянства необходимо составить неравенство...» — за это учитель ставит двойку!\n"
            "   Учителю нужны КОНКРЕТНОЕ НЕРАВЕНСТВО / УРАВНЕНИЕ С ЧИСЛАМИ, ПОШАГОВЫЕ ДЕЙСТВИЯ И ТОЧНЫЙ РЕЗУЛЬТАТ!\n"
            "6. ПОШАГОВОЕ МАТЕМАТИЧЕСКОЕ ОБОСНОВАНИЕ (СДЕЛАЙ СНАЧАЛА В ПОЛЕ [ПОЯСНЕНИЕ]):\n"
            "   - Точно выпиши формулу функции/уравнения текущей задачи (например y = 2x - 6).\n"
            "   - ДЛЯ МОНОТОННОСТИ (ВОЗРАСТАЮЩАЯ ИЛИ УБЫВАЮЩАЯ):\n"
            "     * Посмотри на угловой коэффициент k (число перед x в y = kx + b):\n"
            "     * Если k > 0 (например k = 2) — ФУНКЦИЯ СТРОГО ВОЗРАСТАЮЩАЯ!\n"
            "     * Если k < 0 (например k = -3) — ФУНКЦИЯ СТРОГО УБЫВАЮЩАЯ!\n"
            "   - ДЛЯ ЗНАКОВ ПОСТОЯНСТВА И НЕРАВЕНСТВ:\n"
            "     * Выпиши неравенство: f(x) > 0 и f(x) < 0.\n"
            "     * Реши его по шагам: 1) перенос слагаемых, 2) деление на коэффициент (при делении на отрицательное число меняй знак на противоположный!).\n"
            "7. ПОЛЕ [СКАЗАТЬ ВСЛУХ] — ЭТО ПОШАГОВЫЙ ОТВЕТ ДЛЯ УЧЕНИКА:\n"
            "   Строка 1: Чёткий прямой ответ с числами задачи.\n"
            "   Строка 2-3 (Пошаговые действия):\n"
            "     1) <первое действие с числами>\n"
            "     2) <второе действие и полученный результат>\n"
            "   Последняя строка: ⚡ Шпаргалка: k = ..., b = ..., монотонность, нули функции.\n\n"
            "СТРОГИЙ ФОРМАТ ОТВЕТА (СНАЧАЛА ПОЯСНЕНИЕ, ЗАТЕМ СКАЗАТЬ ВСЛУХ):\n"
            "[ДИАЛОГ]: <реплики учителя и учеников>\n"
            "[КТО]: <кому адресован вопрос>\n"
            "[ВОПРОС]: <точный вопрос учителя>\n"
            "[ПОЯСНЕНИЕ]: <1. Формула. 2. Знак k. 3. Пошаговые действия с числами>\n"
            "[СКАЗАТЬ ВСЛУХ]: <пошаговый ответ с числами для произнесения учителю>\n"
            "[ТЕРМИНЫ]: <ключевые термины>"
        )

    return (
        "Ти — надточний математичний і предметний суфлер для учня прямо на онлайн-уроці.\n"
        "В аудіозаписі звучить опитування в класі: вчитель розв'язує задачу, може питати інших учнів, "
        "коментувати розв'язок і в кінці звертається до нашого учня.\n\n"
        f"КОНТЕКСТ УРОКУ ТА ПОПЕРЕДНІ ЗАДАЧІ:\n{context_str}\n\n"
        "ЖАЛІЗНІ ПРАВИЛА РОЗБОРУ ТА ОБЧИСЛЕНЬ:\n"
        "1. МОВА ВІДПОВІДІ — СУВОРО УКРАЇНСЬКА.\n"
        f"2. {who}\n"
        "3. СУВОРА ФІЛЬТРАЦІЯ DISCORD ТА СТОРОННІХ ЧАТІВ:\n"
        "   В аудіозаписі паралельно можуть лунати голоси друзів або тіммейтів із Discord / ігор / голосового чату (розмови про ігри, сміх, підколки, лайка, матюки, сторонні балачки).\n"
        "   КАТЕГОРИЧНО ІГНОРУЙ БУДЬ-ЯКІ РЕПЛІКИ ТА ГОЛОСИ З ДИСКОРДУ!\n"
        "   Твоя мета — ВИКЛЮЧНО вчитель (дорослий викладач), який веде урок, пояснює матеріал та ставить запитання учням класу.\n"
        "   Якщо вчитель мовчить, а говорять друзі в Discord — у полі [ЗАПИТАННЯ] пиши: «Вчитель мовчить (у Discord сторонні розмови)», а поле [СКАЗАТИ ВГОЛОС] залиш порожнім!\n"
        "4. СПОЧАТКУ ДОСЛІВНО РОЗШИФРУЙ ДІАЛОГ у полі [ДІАЛОГ]. Знайди останнє нерозв'язане питання вчителя до нашого учня.\n"
        "5. КАТЕГОРИЧНЕ ТАБУ НА ВОДУ ТА ЗАГАЛЬНІ ОЗНАЧЕННЯ:\n"
        "   НІКОЛИ не пиши загальних фраз типу «Для знаходження проміжків знакосталості необхідно скласти нерівність...» або «Щоб знайти нулі, треба прирівняти до нуля...» — за це вчитель ставить двійку!\n"
        "   Вчителю потрібні КОНКРЕТНА НЕРІВНІСТЬ / РІВНЯННЯ З ЧИСЛАМИ, ПОКРОКОВІ ДІЇ ТА ТОЧНИЙ РЕЗУЛЬТАТ!\n"
        "6. ПОКРОКОВЕ МАТЕМАТИЧНЕ ОБҐРУНТУВАННЯ (ЗРОБИ СПОЧАТКУ У ПОЛІ [ПОЯСНЕННЯ]):\n"
        "   - Точно випиши формулу функції/рівняння, про яку йде мова на уроці (наприклад y = 2x - 6 або y = 6 - 3x).\n"
        "   - ДЛЯ МОНОТОННОСТІ (ЗРОСТАЮЧА ЧИ СПАДНА):\n"
        "     * Подивись на кутовий коефіцієнт k (число перед x у y = kx + b):\n"
        "     * Якщо k > 0 (наприклад k = 2) — ФУНКЦІЯ СУВОРО ЗРОСТАЮЧА!\n"
        "     * Якщо k < 0 (наприклад k = -3) — ФУНКЦІЯ СУВОРО СПАДНА!\n"
        "     * Категорично заборонено плутати знак k!\n"
        "   - ДЛЯ ЗНАКІВ СТАЛОСТІ ТА НЕРІВНОСТЕЙ:\n"
        "     * Випиши саму нерівність: f(x) > 0 та f(x) < 0.\n"
        "     * Розв'яжи її по кроках: 1) перенесення чисел, 2) ділення на коефіцієнт (ПАМ'ЯТАЙ: при діленні на ВІД'ЄМНЕ число знак нерівності ЗМІНЮЄТЬСЯ на протилежний!).\n"
        "     * Запиши отриманий числовий інтервал (наприклад x < 2 або x in (-∞; 2)).\n"
        "7. ПОЛЕ [СКАЗАТИ ВГОЛОС] — ЦЕ ПОКРОКОВИЙ РОЗВ'ЯЗОК ДЛЯ УЧНЯ:\n"
        "   Воно повинно містити конкретні покрокові фрази, які учень читає вчителю:\n"
        "   Рядок 1: Чітка пряма відповідь (наприклад: «Ця функція є зростаючою, оскільки коефіцієнт k = 2 додатний» або «Складаємо нерівність: 6 - 3x > 0»).\n"
        "   Рядок 2-3 (Покроковий хід розв'язання):\n"
        "     1) <перша дія з числами>\n"
        "     2) <друга дія та отриманий результат>\n"
        "   Останній рядок: ⚡ Шпаргалка: k = ..., b = ..., монотонність, нулі функції, перетин з Oy.\n\n"
        "СТРОГИЙ ФОРМАТ ВІДПОВІДІ (СПОЧАТКУ ПОЯСНЕННЯ З РОЗРАХУНКОМ, ПОТІМ СКАЗАТИ ВГОЛОС):\n"
        "[ДІАЛОГ]: <репліки вчителя та учнів>\n"
        "[ХТО]: <кому адресовано запитання>\n"
        "[ЗАПИТАННЯ]: <точне запитання вчителя>\n"
        "[ПОЯСНЕННЯ]: <1. Формула функції. 2. Коефіцієнт k та його знак. 3. Покрокове розв'язання нерівності/рівняння>\n"
        "[СКАЗАТИ ВГОЛОС]: <покрокова відповідь з числами для читання вголос вчителю>\n"
        "[ТЕРМІНИ]: <ключові терміни>"
    )


def build_text_prompt(question_text: str, context_str: str, student_name: str = "", lang: str = "auto") -> str:
    """Промпт для текстового запитання або швидкого уточнення учня/вчителя."""
    is_ru = (lang == "ru") or (lang != "uk" and any(k in (context_str + question_text).lower() for k in [
        "россия", "русск", "обществознан", "литератур", "обж", "обзр", "учитель", "класс", "москва", "рф"
    ]))
    who = _student_identity_clause(student_name, is_ru=is_ru)

    if is_ru:
        return (
            "Ты — сверхточный математический и предметный суфлёр для ученика прямо на уроке.\n"
            "Ниже приведён вопрос учителя, ученика или короткое уточнение:\n"
            f"«{question_text}»\n\n"
            f"КОНТЕКСТ УРОКА И ПРЕДЫДУЩИЕ ЗАДАЧИ:\n{context_str}\n\n"
            "ЖЕЛЕЗНЫЕ ПРАВИЛА:\n"
            "1. ЯЗЫК ОТВЕТА — СТРОГО РУССКИЙ.\n"
            f"2. {who}\n"
            "3. НИКАКОЙ ВОДЫ: Никакой абстрактной теории! Давай КОНКРЕТНЫЕ ЧИСЛА, ПОШАГОВЫЙ РАСЧЁТ И НЕРАВЕНСТВА.\n"
            "4. МОНОТОННОСТЬ И ЗНАКИ:\n"
            "   - k > 0 -> ВОЗРАСТАЮЩАЯ. k < 0 -> УБЫВАЮЩАЯ.\n"
            "   - При делении на отрицательное число обязательно меняй знак неравенства на противоположный.\n"
            "5. ПОЛЕ [СКАЗАТЬ ВСЛУХ]:\n"
            "   - Прямой ответ с числами текущей задачи.\n"
            "   - Пошаговые действия решения: 1) ..., 2) ..., 3) ...\n"
            "   - ⚡ Шпаргалка: k = ..., b = ..., монотонность, нули.\n\n"
            "СТРОГИЙ ФОРМАТ:\n"
            "[ВОПРОС]: <суть вопроса>\n"
            "[ПОЯСНЕНИЕ]: <пошаговый расчёт, знак k, перенос слагаемых>\n"
            "[СКАЗАТЬ ВСЛУХ]: <пошаговое решение с числами для ответа учителю>\n"
            "[ТЕРМИНЫ]: <термины>"
        )

    return (
        "Ти — надточний математичний і предметний суфлер для учня прямо на уроці.\n"
        "Нижче наведено запитання вчителя, учня або коротке уточнення (наприклад: «чому дорівнює k?», «зростаюча чи спадна?», «яку нерівність скласти?»):\n"
        f"«{question_text}»\n\n"
        f"КОНТЕКСТ УРОКУ ТА ОСТАННІ ЗАДАЧІ:\n{context_str}\n\n"
        "ЖАЛІЗНІ ПРАВИЛА:\n"
        f"1. {who}\n"
        "2. ЖОДНОЇ ВОДИ: Ніякої абстрактної теорії! Давай КОНКРЕТНІ ЧИСЛА, ПОКРОКОВИЙ РОЗРАХУНОК ТА НЕРІВНОСТІ.\n"
        "3. МОНОТОННІСТЬ ТА ЗНАКИ:\n"
        "   - k > 0 -> ЗРОСТАЮЧА. k < 0 -> СПАДНА.\n"
        "   - При діленні на від'ємне число обов'язково змінюй знак нерівності на протилежний (> стає <).\n"
        "4. ПОЛЕ [СКАЗАТИ ВГОЛОС]:\n"
        "   - Пряма відповідь з числами поточної задачі.\n"
        "   - Покрокові дії розв'язку: 1) ..., 2) ..., 3) ...\n"
        "   - ⚡ Шпаргалка: k = ..., b = ..., монотонність, нулі.\n\n"
        "СТРОГИЙ ФОРМАТ (СПОЧАТКУ ПОЯСНЕННЯ, ПОТІМ СКАЗАТИ ВГОЛОС):\n"
        "[ЗАПИТАННЯ]: <суть запитання>\n"
        "[ПОЯСНЕННЯ]: <покроковий розрахунок, знак k, перенесення доданків>\n"
        "[СКАЗАТИ ВГОЛОС]: <покроковий розв'язок з числами для промовлення вчителю>\n"
        "[ТЕРМІНИ]: <терміни>"
    )


class GeminiEngine:
    """Універсальний мульти-провайдерний рушій:
    Підтримує:
    1. Google Gemini (прямий звук + 500 RPD на Lite моделях)
    2. OpenRouter (NVIDIA Nemotron 3 Ultra 550B, Gemma 4 31B, Nemotron Omni)
    3. Groq Cloud (Qwen 3.8, Whisper Large Turbo < 0.5s)
    З автоматичним переходом (fallback) при вичерпанні лімітів."""

    def __init__(self, api_key_or_cfg=None, answer_model="gemini-3.5-flash-lite", bg_model="gemini-3.5-flash-lite", student_name="", api_key="", **kwargs):
        self.session = requests.Session()
        self.thinking = None
        self.fallback = True
        self.cfg = {}
        if isinstance(api_key_or_cfg, dict):
            self.cfg = dict(api_key_or_cfg)
        else:
            actual_key = (api_key or api_key_or_cfg or "").strip()
            self.cfg = {
                "active_provider": "gemini",
                "gemini_api_key": actual_key,
                "answer_model": answer_model or "gemini-3.5-flash-lite",
                "background_model": bg_model or "gemini-3.5-flash-lite",
                "openrouter_model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                "groq_model": "qwen/qwen3.8-27b",
            }
        self.cfg.update(kwargs)
        proxy = self.cfg.get("proxy", "").strip() or os.environ.get("HTTPS_PROXY", "").strip() or os.environ.get("HTTP_PROXY", "").strip()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
            logging.info(f"Налаштовано проксі для API: {proxy}")
        self.student_name = student_name or self.cfg.get("student_name", "")

    def set_config(self, api_key: str, answer_model: str, bg_model: str, student_name: str = None, **kwargs):
        self.cfg["gemini_api_key"] = (api_key or "").strip()
        self.cfg["answer_model"] = answer_model
        self.cfg["background_model"] = bg_model
        if student_name is not None:
            self.cfg["student_name"] = student_name
            self.student_name = student_name
        self.cfg.update(kwargs)

    def is_ready(self) -> bool:
        prov = self.cfg.get("active_provider", "gemini")
        if prov == "openrouter":
            return bool(self.cfg.get("openrouter_api_key") and len(self.cfg["openrouter_api_key"]) > 5)
        elif prov == "groq":
            return bool(self.cfg.get("groq_api_key") and len(self.cfg["groq_api_key"]) > 5)
        # default gemini
        return bool(self.cfg.get("gemini_api_key") and len(self.cfg["gemini_api_key"]) > 10)

    # ---- 1. Google Gemini
    def _call_gemini(self, model: str, prompt: str, wav_bytes: bytes = None, timeout: int = 22,
                     max_tokens: int = 1500, _tried=None) -> str:
        key = self.cfg.get("gemini_api_key", "").strip()
        if not key:
            raise RuntimeError("API-ключ Gemini не налаштовано.")
        import base64
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        parts = []
        if wav_bytes and len(wav_bytes) > 0:
            b64_audio = base64.b64encode(wav_bytes).decode("utf-8")
            parts.append({"inline_data": {"mime_type": "audio/wav", "data": b64_audio}})
        parts.append({"text": prompt})

        gen_cfg = {"maxOutputTokens": max_tokens, "temperature": 0.1}
        if self.thinking:
            gen_cfg["thinkingConfig"] = {"thinkingLevel": self.thinking}
        payload = {"contents": [{"parts": parts}], "generationConfig": gen_cfg}

        try:
            resp = self.session.post(url, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as net_err:
            if self.fallback:
                tried = set(_tried or ()) | {model}
                nxt = next((m for m in FALLBACK_CHAIN if m not in tried), None)
                if nxt:
                    logging.warning(f"Gemini {model}: збій ({net_err}). Автоперехід на {nxt}...")
                    return self._call_gemini(nxt, prompt, wav_bytes=wav_bytes, timeout=timeout,
                                            max_tokens=max_tokens, _tried=tried)
            raise

        if resp.status_code == 400 and self.thinking and "thinking" in resp.text.lower():
            self.thinking = None
            gen_cfg.pop("thinkingConfig", None)
            resp = self.session.post(url, json=payload, timeout=timeout)

        if resp.status_code in (429, 500, 502, 503, 504) and self.fallback:
            tried = set(_tried or ()) | {model}
            nxt = next((m for m in FALLBACK_CHAIN if m not in tried), None)
            if nxt:
                logging.warning(f"Gemini {model}: HTTP {resp.status_code}. Автоперехід на {nxt}...")
                return self._call_gemini(nxt, prompt, wav_bytes=wav_bytes, timeout=timeout,
                                        max_tokens=max_tokens, _tried=tried)

        if resp.status_code != 200:
            err_text = resp.text
            logging.error(f"Gemini API HTTP {resp.status_code}: {err_text}")
            raise RuntimeError(f"Gemini HTTP {resp.status_code}: {err_text[:120]}")

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return "[СКАЗАТИ ВГОЛОС]: Не вдалося чітко розпізнати питання вчителя."
        content_parts = candidates[0].get("content", {}).get("parts", [])
        out_texts = [p.get("text", "") for p in content_parts if "text" in p]
        return "\n".join(out_texts).strip()

    # ---- 2. OpenRouter (Nemotron 3 Ultra 550B, Gemma 4, etc.)
    def _call_openrouter(self, model: str, prompt: str, timeout: int = 25, max_tokens: int = 1500) -> str:
        key = self.cfg.get("openrouter_api_key", "").strip()
        if not key:
            raise RuntimeError("API-ключ OpenRouter не вказано в налаштуваннях.")
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "HTTP-Referer": "https://lesson-listener.local",
            "X-Title": "Lesson Listener",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": max_tokens
        }
        resp = self.session.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            err_text = resp.text
            logging.error(f"OpenRouter HTTP {resp.status_code}: {err_text}")
            raise RuntimeError(f"OpenRouter HTTP {resp.status_code}: {err_text[:120]}")
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return "[СКАЗАТИ ВГОЛОС]: Порожня відповідь від OpenRouter."
        return choices[0].get("message", {}).get("content", "").strip()

    # ---- 3. Groq Cloud (Qwen 3.8 / Whisper Turbo)
    def _call_groq(self, model: str, prompt: str, timeout: int = 15, max_tokens: int = 1500) -> str:
        key = self.cfg.get("groq_api_key", "").strip()
        if not key:
            raise RuntimeError("API-ключ Groq не вказано в налаштуваннях.")
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": max_tokens
        }
        resp = self.session.post(url, headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            err_text = resp.text
            logging.error(f"Groq HTTP {resp.status_code}: {err_text}")
            raise RuntimeError(f"Groq HTTP {resp.status_code}: {err_text[:120]}")
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return "[СКАЗАТИ ВГОЛОС]: Порожня відповідь від Groq."
        return choices[0].get("message", {}).get("content", "").strip()

    def _transcribe_groq(self, wav_bytes: bytes) -> str:
        key = self.cfg.get("groq_api_key", "").strip()
        if not key:
            return ""
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {key}"}
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"model": "whisper-large-v3-turbo", "language": "uk"}
        resp = self.session.post(url, headers=headers, files=files, data=data, timeout=12)
        if resp.status_code == 200:
            return resp.json().get("text", "").strip()
        return ""

    def _call_api(self, model: str, prompt: str, wav_bytes: bytes = None, timeout: int = 22,
                  max_tokens: int = 1500, _tried=None) -> str:
        return self._call_gemini(model, prompt, wav_bytes=wav_bytes, timeout=timeout, max_tokens=max_tokens, _tried=_tried)

    def _parse_answer(self, text: str, default_question: str = "Запитання з уроку") -> dict:
        dlg_match = re.search(r"\[(?:ДІАЛОГ|ДИАЛОГ|СТЕНОГРАМА)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        who_match = re.search(r"\[(?:ХТО|КОМУ)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        q_match = re.search(r"\[(?:ЗАПИТАННЯ|ПИТАННЯ|ВОПРОС)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        ans_match = re.search(r"\[(?:СКАЗАТИ ВГОЛОС|СКАЗАТИ|ВІДПОВІДЬ|СКАЗАТЬ ВСЛУХ)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        deep_match = re.search(r"\[(?:ПОЯСНЕННЯ|ОБҐРУНТУВАННЯ|ОБОСНОВАНИЕ|ПОКРОКОВИЙ РОЗРАХУНОК)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        terms_match = re.search(r"\[(?:ТЕРМІНИ|ТЕРМИНЫ)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        cheat_match = re.search(r"\[(?:ШПАРГАЛКА|ПАРАМЕТРИ|ФАКТИ|ДОДАТКОВО)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)

        say_aloud = ans_match.group(1).strip() if ans_match else ""
        deep = deep_match.group(1).strip() if deep_match else ""
        if not say_aloud:
            lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("[")]
            say_aloud = deep or (lines[-1] if lines else text.strip())

        # Очищення від LaTeX/Markdown артефактів для плавного читання
        say_aloud = re.sub(r"[\*\$`]+", "", say_aloud)
        say_aloud = re.sub(r"_\{?(\d+)\}?", r"\1", say_aloud).strip()
        deep = re.sub(r"[\*\$`]+", "", deep).strip()

        # Якщо в say_aloud немає покрокових дій (1), 2)), а в deep вони є — додаємо покроковий хід
        if deep and any(token in deep for token in ["1)", "2)", "Крок", "Шаг", "•", "нерівн", "неравен", "звідси", "отсюда"]):
            if "1)" not in say_aloud and "Крок" not in say_aloud and "Шаг" not in say_aloud:
                is_ru = any(c in deep.lower() for c in "ыэёъ")
                step_hdr = "Пошаговое решение:" if is_ru else "Покроковий розв'язок:"
                say_aloud = f"{say_aloud}\n\n{step_hdr}\n{deep}"

        # Додаємо шпаргалку (k, b, нулі), якщо вона була окремим блоком
        if cheat_match:
            cheat_txt = re.sub(r"[\*\$`]+", "", cheat_match.group(1)).strip()
            if cheat_txt and "Шпаргалка" not in say_aloud:
                say_aloud = f"{say_aloud}\n⚡ Шпаргалка: {cheat_txt}"

        question = q_match.group(1).strip() if q_match else default_question
        say_aloud = clean_math(say_aloud)
        deep = clean_math(deep)

        return {
            "dialogue": dlg_match.group(1).strip() if dlg_match else "",
            "who": who_match.group(1).strip() if who_match else "",
            "question": question,
            "say_aloud": say_aloud,
            "deep": deep,
            "terms": terms_match.group(1).strip() if terms_match else "",
        }

    def answer_text_question(self, question_text: str, context_str: str) -> dict:
        prov = self.cfg.get("active_provider", "gemini")
        prompt = build_text_prompt(question_text, context_str, self.student_name)

        # 1. OpenRouter (Nemotron 3 Ultra 550B / Gemma 4 / etc.)
        if prov == "openrouter" and self.cfg.get("openrouter_api_key"):
            model = self.cfg.get("openrouter_model", "nvidia/nemotron-3-ultra-550b-a55b:free")
            try:
                text = self._call_openrouter(model, prompt, timeout=25, max_tokens=1500)
                return self._parse_answer(text, default_question=question_text)
            except Exception as e:
                logging.warning(f"OpenRouter збій ({e}), перевіряю аварійний резерв...")

        # 2. Groq (Qwen 3.8 / GPT-OSS)
        if prov == "groq" and self.cfg.get("groq_api_key"):
            model = self.cfg.get("groq_model", "qwen/qwen3.8-27b")
            try:
                text = self._call_groq(model, prompt, timeout=15, max_tokens=1500)
                return self._parse_answer(text, default_question=question_text)
            except Exception as e:
                logging.warning(f"Groq збій ({e}), перевіряю аварійний резерв...")

        # 3. Gemini (основний або аварійний фоллбек)
        if self.cfg.get("gemini_api_key"):
            model = self.cfg.get("answer_model", "gemini-3.5-flash-lite")
            try:
                text = self._call_gemini(model, prompt, timeout=20, max_tokens=1500)
                return self._parse_answer(text, default_question=question_text)
            except Exception as e:
                logging.warning(f"Gemini збій: {e}")

        # Спроба будь-якого налаштованого провайдера
        if self.cfg.get("openrouter_api_key"):
            try:
                model = self.cfg.get("openrouter_model", "nvidia/nemotron-3-ultra-550b-a55b:free")
                text = self._call_openrouter(model, prompt, timeout=25, max_tokens=1500)
                return self._parse_answer(text, default_question=question_text)
            except Exception as e:
                return {"error": f"OpenRouter помилка: {e}"}

        if self.cfg.get("groq_api_key"):
            try:
                model = self.cfg.get("groq_model", "qwen/qwen3.8-27b")
                text = self._call_groq(model, prompt, timeout=15, max_tokens=1500)
                return self._parse_answer(text, default_question=question_text)
            except Exception as e:
                return {"error": f"Groq помилка: {e}"}

        return {"error": "API-ключі не налаштовані. Додай ключ Gemini, OpenRouter або Groq у ⚙ Налаштування."}

    def answer_audio_question(self, wav_bytes: bytes, context_str: str) -> dict:
        prov = self.cfg.get("active_provider", "gemini")

        # 1. Прямий мультимодальний звук через Gemini
        if prov == "gemini" and self.cfg.get("gemini_api_key"):
            prompt = build_audio_prompt(context_str, self.student_name)
            model = self.cfg.get("answer_model", "gemini-3.5-flash-lite")
            try:
                text = self._call_gemini(model, prompt, wav_bytes=wav_bytes, timeout=22, max_tokens=1500)
                return self._parse_answer(text)
            except Exception as e:
                logging.warning(f"Gemini аудіо HTTP/квота помилка: {e}. Перевіряю резерв OpenRouter/Groq...")
                if not (self.cfg.get("openrouter_api_key") or self.cfg.get("groq_api_key")):
                    return {"error": str(e)}

        # 2. OpenRouter або Groq: спочатку розшифровуємо звук у текст діалогу
        transcript = ""
        if self.cfg.get("groq_api_key"):
            try:
                transcript = self._transcribe_groq(wav_bytes)
                if transcript:
                    logging.info(f"Groq Whisper успішно розшифрував аудіо: {transcript[:80]}...")
            except Exception as ge:
                logging.warning(f"Groq Whisper помилка: {ge}")

        if not transcript and self.cfg.get("gemini_api_key"):
            try:
                tr_prompt = "Дослівно розшифруй аудіозапис опитування в класі. Випиши репліки вчителя та учнів."
                transcript = self._call_gemini("gemini-3.5-flash-lite", tr_prompt, wav_bytes=wav_bytes, timeout=14)
                if transcript:
                    logging.info("Gemini Lite розшифрував аудіо для текстової моделі.")
            except Exception:
                pass

        if not transcript:
            return {"error": "Не вдалося розпізнати звук. Перевір мікрофон або API-ключ Gemini/Groq."}

        # Відправляємо розшифрований діалог уроку у супер-модель (Nemotron 3 Ultra / Gemma 4 / Qwen 3.8)
        return self.answer_text_question(transcript, context_str)

    def analyze_background(self, wav_bytes: bytes, current_ctx: LessonContext) -> dict:
        if not self.is_ready():
            return {}

        prompt = (
            "Ти аналітик онлайн-уроку. Прослухай аудіофрагмент поточного уроку.\n"
            f"{current_ctx.get_prompt_context()}\n\n"
            "Відповідай українською мовою. Поверни СТРОГО валідний JSON:\n"
            "{\n"
            '  "has_speech": true,\n'
            '  "topic": "Тема уроку (коротко)",\n'
            '  "teacher_style": "Вимоги вчителя: лаконічність, формули, тон",\n'
            '  "key_terms": ["термін1", "термін2"],\n'
            '  "summary": "1 речення: що пояснювали в цьому фрагменті",\n'
            '  "is_recess": false,\n'
            '  "detected_subject": "algebra"\n'
            "}\n"
            "ПРАВИЛА РОЗПІЗНАВАННЯ:\n"
            "- КАТЕГОРИЧНО ІГНОРУЙ сторонні розмови друзів із Discord / ігор. Аналізуй ТІЛЬКИ мову викладача уроку.\n"
            "- is_recess: true, якщо вчитель оголосив перерву, завершив урок, відпустив учнів, або чути шкільний дзвінок чи розмови на перерві. Якщо йде пояснення уроку — false.\n"
            "- detected_subject: id предмета (algebra, geometry, physics, chemistry, biology, history, english, ukr_lang, informatics), якщо за темою чи словами вчителя чітко зрозуміло предмет, інакше null."
        )

        if self.cfg.get("gemini_api_key"):
            try:
                raw = self._call_gemini("gemini-3.5-flash-lite", prompt, wav_bytes=wav_bytes, timeout=14, max_tokens=600)
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except Exception:
                pass

        if self.cfg.get("groq_api_key"):
            try:
                tr = self._transcribe_groq(wav_bytes)
                if tr:
                    text_p = f"{prompt}\n\nРОЗШИФРОВКА АУДІО:\n«{tr}»"
                    raw = self._call_groq(self.cfg.get("groq_model", "qwen/qwen3.8-27b"), text_p, timeout=12, max_tokens=600)
                    raw = re.sub(r"^```(?:json)?\s*", "", raw)
                    raw = re.sub(r"\s*```$", "", raw)
                    data = json.loads(raw)
                    return data if isinstance(data, dict) else {}
            except Exception:
                pass

        return {}


# --------------------------------------------------------------------------
# Аудіозахоплення з очищенням буфера
# --------------------------------------------------------------------------
class UniversalRecorder:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.pa_stream = None
        self.proctap_stream = None
        self.lock = threading.RLock()
        self.chunks = collections.deque()
        self.total = 0
        self.level = 0.0

    def clear(self):
        """Повне очищення аудіобуфера, щоб старі питання не змішувалися з новими."""
        with self.lock:
            self.chunks.clear()
            self.total = 0
            self.level = 0.0

    def list_sources(self):
        sources = []

        # 1. Браузери (знаходимо кореневий процес для захоплення звуку дерева вкладок)
        browser_sources = []
        if HAS_PROCTAP:
            found_browsers = {}
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    name_lower = p.info["name"].lower()
                    if name_lower in BROWSER_EXES:
                        cmd = p.info.get("cmdline") or []
                        is_root = not any(arg.startswith("--type=") for arg in cmd)
                        if is_root or name_lower not in found_browsers:
                            found_browsers[name_lower] = p.info["pid"]
                except Exception:
                    pass

            for exe_name, pid in found_browsers.items():
                title = BROWSER_EXES[exe_name]
                browser_sources.append((f"🌐 {title} (Загальний мікшер Win10)", {"category": "browser", "pid": pid, "name": title}))

        # 2. Мікрофони
        mic_sources = []
        try:
            wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            for i in range(wasapi["deviceCount"]):
                d = self.pa.get_device_info_by_host_api_device_index(wasapi["index"], i)
                if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice", False):
                    name = d["name"]
                    mic_sources.append((f"🎤 Мікрофон: {name}", {"category": "mic", "dev": d}))
        except Exception:
            pass

        # 3. Віртуальні кабелі (100% апаратна ізоляція від Discord) та загальний звук системи
        cable_sources = []
        sys_sources = []
        default_idx = None
        try:
            default_idx = self.pa.get_default_wasapi_loopback()["index"]
        except Exception:
            pass

        try:
            for d in self.pa.get_loopback_device_info_generator():
                name = d["name"]
                nl = name.lower()
                if any(k in nl for k in ["cable", "virtual", "voicemeeter", "line 1"]):
                    if "cable input" in nl:
                        lbl = f"🎛️ {name} — 100% Ізоляція від Discord (Рекомендовано)"
                    elif "line 1" in nl:
                        lbl = f"🎛️ {name} — Ізольований кабель без Discord"
                    else:
                        lbl = f"🎛️ {name} (Віртуальний кабель)"
                    cable_sources.append((lbl, {"category": "cable", "dev": d}))
                else:
                    item = (f"🔊 Системний звук: {name}", {"category": "system", "dev": d})
                    if d["index"] == default_idx:
                        sys_sources.insert(0, item)
                    else:
                        sys_sources.append(item)
            # Пріоритет: чистий VB-CABLE Input або Line 1 на початку списку
            cable_sources.sort(key=lambda item: 0 if "cable input" in item[0].lower() else (1 if "line 1" in item[0].lower() else 2))
        except Exception:
            pass

        return cable_sources + browser_sources + mic_sources + sys_sources

    def start(self, source_info: dict):
        self.stop()
        self.clear()

        cat = source_info.get("category")
        if cat == "browser" and HAS_PROCTAP:
            pid = source_info["pid"]
            self.proctap_stream = proctap.ProcessAudioCapture(
                pid=pid,
                on_data=self._on_proctap_audio,
                resample_quality="fast"
            )
            self.proctap_stream.start()
        else:
            dev = source_info["dev"]
            src_sr = int(dev["defaultSampleRate"])
            channels = max(1, int(dev["maxInputChannels"]))
            self.pa_stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=src_sr,
                input=True,
                input_device_index=dev["index"],
                frames_per_buffer=1024,
                stream_callback=lambda data, f, t, s: self._on_pa_audio(data, channels, src_sr),
            )

    def stop(self):
        if self.proctap_stream is not None:
            try:
                self.proctap_stream.stop()
                self.proctap_stream.close()
            except Exception:
                pass
            self.proctap_stream = None

        if self.pa_stream is not None:
            try:
                self.pa_stream.stop_stream()
                self.pa_stream.close()
            except Exception:
                pass
            self.pa_stream = None

    def _on_proctap_audio(self, pcm_bytes: bytes, *args, **kwargs):
        try:
            a = np.frombuffer(pcm_bytes, dtype=np.float32)
            if len(a) > 1:
                a = a.reshape(-1, 2).mean(axis=1)
                k = 3
                a = a[: len(a) // k * k].reshape(-1, k).mean(axis=1)
            self._push_audio_chunk(a.astype(np.float32))
        except Exception:
            pass

    def _on_pa_audio(self, data, channels, src_sr):
        try:
            a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            if channels > 1:
                a = a[: len(a) // channels * channels].reshape(-1, channels).mean(axis=1)
            if src_sr != TARGET_SR and len(a) > 1:
                if src_sr % TARGET_SR == 0:
                    k = src_sr // TARGET_SR
                    a = a[: len(a) // k * k].reshape(-1, k).mean(axis=1)
                else:
                    n = int(len(a) * TARGET_SR / src_sr)
                    a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
            self._push_audio_chunk(a.astype(np.float32))
        except Exception:
            pass
        return (None, pyaudio.paContinue)

    def _push_audio_chunk(self, a: np.ndarray):
        if len(a):
            peak = float(np.sqrt(np.mean(a * a)))
            self.level = max(peak, self.level * 0.9)
            with self.lock:
                self.chunks.append(a)
                self.total += len(a)
                limit = MAX_RING_SECONDS * TARGET_SR
                while self.chunks and self.total - len(self.chunks[0]) >= limit:
                    self.total -= len(self.chunks.popleft())

    def get_last(self, seconds: float) -> np.ndarray:
        with self.lock:
            if not self.chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(list(self.chunks))
        need = int(seconds * TARGET_SR)
        return audio[-need:] if len(audio) >= need else audio

    def available_seconds(self) -> float:
        return self.total / TARGET_SR
