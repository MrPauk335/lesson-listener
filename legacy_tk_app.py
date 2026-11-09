"""
Lesson Listener — Розумний AI-суфлер для онлайн-уроків та пар.
- Повна українська локалізація (інтерфейс, промпти, відповіді).
- Чіткий поділ уроків, очищення аудіобуфера після кожного питання (без змішування тем).
- Швидке перемикання звуку: 🎤 Мікрофон / 🌐 Браузер / 🔊 Динаміки.
- Миттєва відповідь через Gemini 3.5 Flash-Lite (1-2 сек).
"""
import collections
import io
import json
import logging
import os
import re
import sys
import threading
import time
import wave
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

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

ANSWER_MODELS = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-flash"]
BG_MODELS = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
# Безкоштовний ліміт: 15 запитів/хв на модель — при 429 стрибаємо на модель з окремою квотою
FALLBACK_CHAIN = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-2.5-flash"]

DEFAULT_CONFIG = {
    "gemini_api_key": "",
    "answer_model": "gemini-3.5-flash-lite",
    "background_model": "gemini-3.5-flash-lite",
    "background_interval_sec": 30,
    "background_listening": True,
    "auto_copy_answer": True,
    "topmost": True,
    "listen_seconds": 25,  # вчитель може спочатку питати інших учнів
    "student_name": "",
    "active_subject": "algebra",
    "audio_category": "mic",
}

BROWSER_EXES = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "browser.exe": "Яндекс Браузер",
    "firefox.exe": "Mozilla Firefox",
    "opera.exe": "Opera",
    "brave.exe": "Brave",
    "zen.exe": "Zen Browser",
}


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
def _student_identity_clause(student_name: str = "") -> str:
    name = (student_name or "").strip()
    if name:
        return (
            f"Ім'я нашого учня — «{name}» (враховуй кличний відмінок, зменшувальні форми та схожі на слух варіанти). "
            f"Якщо вчитель звертається до «{name}» — це запитання нашому учню. Якщо ім'я не названо — наш учень той, кого спитали ОСТАННІМ."
        )
    return "Ім'я нашого учня не задано, тому наш учень — той, до кого вчитель звертається ОСТАННІМ у діалозі."


def build_audio_prompt(context_str: str, student_name: str = "") -> str:
    """Промпт для аудіо із обов'язковою первинною транскрипцією [ДІАЛОГ]:
    це гарантує, що модель не переплутає питання першому учню з питанням нашому учню в кінці."""
    who = _student_identity_clause(student_name)
    return (
        "Ти — надточний інтелектуальний суфлер для учня прямо на онлайн-уроці.\n"
        "В аудіозаписі зазвичай звучить опитування класу: вчитель СПОЧАТКУ може питати 1–3 ІНШИХ учнів, "
        "вони відповідають (правильно або з помилками), вчитель коментує чи робить зауваження, "
        "і в КІНЦІ запису ставить запитання нашому учню.\n\n"
        f"КОНТЕКСТ УРОКУ:\n{context_str}\n\n"
        "ПРАВИЛА РОЗБОРУ ОПИТУВАННЯ В КЛАСІ:\n"
        f"1. ХТО НАШ УЧЕНЬ: {who}\n"
        "2. СПОЧАТКУ ДОСЛІВНО РОЗШИФРУЙ ДІАЛОГ у полі [ДІАЛОГ] від першої до останньої репліки.\n"
        "3. ВІДДІЛИ ЧУЖІ ПИТАННЯ ВІД НАШОГО:\n"
        "   - Якщо вчитель спитав іншого учня (наприклад, Сергія, Олю, Петренка), той відповів, а в кінці вчитель поставив НОВЕ питання нашому учню — відповідай СУВОРО на НОВЕ питання нашому учню!\n"
        "   - ПЕРЕАДРЕСАЦІЯ ПИТАННЯ: Якщо вчитель поставив питання іншому учню, той помилився або промовчав, і вчитель каже нашому учню: «Ні, сідай! А ти як скажеш / відповідай ти / допоможи йому» — знайди у [ДІАЛОГ] це нерозв'язане питання вчителя і дай правильну відповідь на нього!\n"
        "   - КАСКАДНЕ ПИТАННЯ: Якщо нове питання посилається на попередню відповідь однокласника (наприклад: «Правильно, 60. А тепер додай до цього 40 і поділи на 4») — використай значення з попередньої репліки для точного розрахунку!\n"
        "   - ВРАХОВУЙ ЗАУВАЖЕННЯ ВЧИТЕЛЯ: Якщо вчитель щойно насварив попереднього учня за відсутність одиниць СІ, формули чи побутові слова — обов'язково виконай цю вимогу у своїй відповіді.\n"
        "   - ІГНОРУЙ ПОБІЧНІ ФРАЗИ В КІНЦІ: Дисциплінарні репліки типу «Іванов, вимкни мікрофон!» або «Тихо в класі!» не є питанням.\n"
        "4. ЯКІСТЬ ВІДПОВІДІ:\n"
        "   - СУВОРО ЧИСТА УКРАЇНСЬКА МОВА.\n"
        "   - У математиці рахуй бездоганно за пріоритетом дій: спочатку дужки, потім множення/ділення, і лише потім додавання/віднімання.\n"
        "   - Поле [СКАЗАТИ ВГОЛОС] — 1–2 чіткі речення без LaTeX, готові для миттєвого читання вголос.\n\n"
        "СТРОГИЙ ФОРМАТ ВІДПОВІДІ:\n"
        "[ДІАЛОГ]: <коротко репліки з аудіо по черзі від початку до кінця>\n"
        "[ХТО]: <кому адресовано фінальне питання і чому>\n"
        "[ЗАПИТАННЯ]: <точне формулювання запитання, на яке відповідає наш учень>\n"
        "[СКАЗАТИ ВГОЛОС]: <готова лаконічна відповідь нашого учня 1-2 речення>\n"
        "[ПОЯСНЕННЯ]: <наукове обґрунтування, формула словами або покроковий розрахунок>\n"
        "[ТЕРМІНИ]: <ключові терміни>"
    )


def build_text_prompt(question_text: str, context_str: str, student_name: str = "") -> str:
    """Промпт для текстового запитання або текстової стенограми опитування класу."""
    who = _student_identity_clause(student_name)
    return (
        "Ти — надточний інтелектуальний суфлер для учня прямо на уроці.\n"
        "Нижче наведено запитання вчителя або фрагмент діалогу в класі (де вчитель міг спочатку питати інших учнів):\n"
        f"«{question_text}»\n\n"
        f"КОНТЕКСТ УРОКУ:\n{context_str}\n\n"
        "ГОЛОВНІ ВИМОГИ:\n"
        f"1. {who}\n"
        "2. Якщо у фрагменті вчитель спочатку питає інших учнів, а потім нашого (або переадресовує нерозв'язане питання після помилки однокласника) — відповідай саме на питання для нашого учня, враховуючи зауваження вчителя та попередні обчислення.\n"
        "3. ВІДПОВІДАЙ СУВОРО ЧИСТОЮ УКРАЇНСЬКОЮ МОВОЮ!\n"
        "4. Якщо запитання математичне чи обчислювальне — обов'язково сам ТОЧНО порахуй результат за пріоритетом дій (дужки -> множення/ділення -> додавання/віднімання).\n"
        "5. Поле [СКАЗАТИ ВГОЛОС] має містити 1–2 лаконічні, природні речення без LaTeX-розмітки.\n\n"
        "СТРОГИЙ ФОРМАТ:\n"
        "[ЗАПИТАННЯ]: <суть запитання до нашого учня>\n"
        "[СКАЗАТИ ВГОЛОС]: <точна готова відповідь українською мовою>\n"
        "[ПОЯСНЕННЯ]: <обґрунтування або короткий розрахунок>\n"
        "[ТЕРМІНИ]: <ключові терміни>"
    )


class GeminiEngine:
    def __init__(self, api_key: str, answer_model: str, bg_model: str, student_name: str = ""):
        self.api_key = api_key.strip()
        self.answer_model = answer_model
        self.bg_model = bg_model
        self.student_name = student_name
        self.thinking = None  # None | "minimal" | "low" | ... (див. benchmark)
        self.fallback = True  # при HTTP 429 переходити на наступну модель з FALLBACK_CHAIN
        self.session = requests.Session()

    def set_config(self, api_key: str, answer_model: str, bg_model: str, student_name: str = None):
        self.api_key = api_key.strip()
        self.answer_model = answer_model
        self.bg_model = bg_model
        if student_name is not None:
            self.student_name = student_name

    def is_ready(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 10)

    def _call_api(self, model: str, prompt: str, wav_bytes: bytes = None, timeout: int = 12,
                  max_tokens: int = 1500, _tried=None) -> str:
        import base64
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
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
                    logging.warning(f"Модель {model}: збій мережі ({net_err}). Автоперехід на {nxt}...")
                    return self._call_api(nxt, prompt, wav_bytes=wav_bytes, timeout=timeout,
                                          max_tokens=max_tokens, _tried=tried)
            raise
        if resp.status_code == 400 and self.thinking and "thinking" in resp.text.lower():
            logging.warning("Модель не підтримує thinkingConfig — вимикаю.")
            self.thinking = None
            gen_cfg.pop("thinkingConfig", None)
            resp = self.session.post(url, json=payload, timeout=timeout)
        if resp.status_code in (429, 500, 502, 503, 504) and self.fallback:
            tried = set(_tried or ()) | {model}
            nxt = next((m for m in FALLBACK_CHAIN if m not in tried), None)
            if nxt:
                logging.warning(f"Модель {model}: HTTP {resp.status_code}. Автоперехід на {nxt}...")
                return self._call_api(nxt, prompt, wav_bytes=wav_bytes, timeout=timeout,
                                      max_tokens=max_tokens, _tried=tried)

        if resp.status_code != 200:
            err_text = resp.text
            logging.error(f"Gemini API HTTP {resp.status_code}: {err_text}")
            raise RuntimeError(f"HTTP {resp.status_code}: {err_text[:120]}")

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return "[СКАЗАТИ ВГОЛОС]: Не вдалося чітко розпізнати питання вчителя. Будь ласка, повторіть."
        content_parts = candidates[0].get("content", {}).get("parts", [])
        out_texts = [p.get("text", "") for p in content_parts if "text" in p]
        return "\n".join(out_texts).strip()

    def _parse_answer(self, text: str, default_question: str = "Запитання з уроку") -> dict:
        dlg_match = re.search(r"\[(?:ДІАЛОГ|ДИАЛОГ|СТЕНОГРАМА)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        who_match = re.search(r"\[(?:ХТО|КОМУ)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        q_match = re.search(r"\[(?:ЗАПИТАННЯ|ПИТАННЯ|ВОПРОС)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        ans_match = re.search(r"\[(?:СКАЗАТИ ВГОЛОС|СКАЗАТИ|ВІДПОВІДЬ|СКАЗАТЬ ВСЛУХ)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        deep_match = re.search(r"\[(?:ПОЯСНЕННЯ|ОБҐРУНТУВАННЯ|ОБОСНОВАНИЕ)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)
        terms_match = re.search(r"\[(?:ТЕРМІНИ|ТЕРМИНЫ)\]:\s*(.*?)(?=\n\[|$)", text, re.DOTALL | re.IGNORECASE)

        say_aloud = ans_match.group(1).strip() if ans_match else ""
        if not say_aloud:
            lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("[")]
            say_aloud = lines[-1] if lines else text.strip()
        # Очищення від випадкових LaTeX/Markdown символів для зручного читання вголос
        say_aloud = re.sub(r"[\*\$`]+", "", say_aloud)
        say_aloud = re.sub(r"_\{?(\d+)\}?", r"\1", say_aloud).strip()

        question = q_match.group(1).strip() if q_match else default_question

        return {
            "dialogue": dlg_match.group(1).strip() if dlg_match else "",
            "who": who_match.group(1).strip() if who_match else "",
            "question": question,
            "say_aloud": say_aloud,
            "deep": deep_match.group(1).strip() if deep_match else "",
            "terms": terms_match.group(1).strip() if terms_match else "",
        }

    def answer_text_question(self, question_text: str, context_str: str) -> dict:
        if not self.is_ready():
            return {"error": "API-ключ Gemini не налаштовано."}

        prompt = build_text_prompt(question_text, context_str, self.student_name)
        try:
            text = self._call_api(self.answer_model, prompt, timeout=10, max_tokens=1500)
            return self._parse_answer(text, default_question=question_text)
        except Exception as e:
            return {"error": str(e)}

    def answer_audio_question(self, wav_bytes: bytes, context_str: str) -> dict:
        if not self.is_ready():
            return {"error": "API-ключ Gemini не налаштовано."}

        prompt = build_audio_prompt(context_str, self.student_name)
        try:
            text = self._call_api(self.answer_model, prompt, wav_bytes=wav_bytes, timeout=12, max_tokens=1500)
            return self._parse_answer(text)
        except Exception as e:
            return {"error": str(e)}

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
            '  "summary": "1 речення: що пояснювали в цьому фрагменті"\n'
            "}"
        )
        try:
            raw = self._call_api(self.bg_model, prompt, wav_bytes=wav_bytes, timeout=12, max_tokens=600)
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return json.loads(raw)
        except Exception:
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

        # 1. Браузери
        if HAS_PROCTAP:
            found_browsers = {}
            for p in psutil.process_iter(["pid", "name"]):
                try:
                    name_lower = p.info["name"].lower()
                    if name_lower in BROWSER_EXES:
                        if name_lower not in found_browsers:
                            found_browsers[name_lower] = p.info["pid"]
                except Exception:
                    pass

            for exe_name, pid in found_browsers.items():
                title = BROWSER_EXES[exe_name]
                sources.append((f"🌐 {title} (лише вкладки уроку)", {"category": "browser", "pid": pid, "name": title}))

        # 2. Мікрофони
        try:
            wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            for i in range(wasapi["deviceCount"]):
                d = self.pa.get_device_info_by_host_api_device_index(wasapi["index"], i)
                if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice", False):
                    name = d["name"]
                    sources.append((f"🎤 Мікрофон: {name}", {"category": "mic", "dev": d}))
        except Exception:
            pass

        # 3. Системний звук (Динаміки)
        default_idx = None
        try:
            default_idx = self.pa.get_default_wasapi_loopback()["index"]
        except Exception:
            pass

        try:
            for d in self.pa.get_loopback_device_info_generator():
                item = (f"🔊 Системний звук: {d['name']}", {"category": "system", "dev": d})
                if d["index"] == default_idx:
                    sources.insert(0, item)
                else:
                    sources.append(item)
        except Exception:
            pass

        return sources

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


# --------------------------------------------------------------------------
# Головний графічний інтерфейс (Повна локалізація + Поділ уроків)
# --------------------------------------------------------------------------
class LessonListenerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()

        root.title("Lesson Listener — Розумний AI-суфлер для уроків")
        root.geometry("740x710")
        root.minsize(640, 580)
        root.attributes("-topmost", self.cfg.get("topmost", True))

        self.prof_mgr = ProfileManager()
        active_sub_id = self.cfg.get("active_subject", "algebra")
        self.active_profile = self.prof_mgr.get(active_sub_id)

        self.context = LessonContext(self.active_profile)
        self.rec = UniversalRecorder()
        self.gemini = GeminiEngine(
            api_key=self.cfg.get("gemini_api_key", ""),
            answer_model=self.cfg.get("answer_model", "gemini-3.5-flash-lite"),
            bg_model=self.cfg.get("background_model", "gemini-3.5-flash-lite"),
            student_name=self.cfg.get("student_name", ""),
        )
        self.gemini.thinking = self.cfg.get("thinking_level") or None
        self.busy = False
        self.bg_active = True
        self.sources = self.rec.list_sources()

        self._build_ui()
        self.setup_hotkey()

        self._select_default_source()
        threading.Thread(target=self._background_listener_loop, daemon=True).start()

        self.tick()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _select_default_source(self):
        saved_cat = self.cfg.get("audio_category", "mic")
        chosen_idx = 0
        for i, (name, data) in enumerate(self.sources):
            if data.get("category") == saved_cat:
                if saved_cat == "mic" and "fifine" in name.lower():
                    chosen_idx = i
                    break
                chosen_idx = i

        if self.sources:
            self.src_box.current(chosen_idx)
            self.start_source()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 3}

        # --- Шапка: Предмет + Кнопки звуку + Статус ---
        top_bar = ttk.Frame(self.root)
        top_bar.pack(fill="x", **pad)

        ttk.Label(top_bar, text="Урок:", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.sub_var = tk.StringVar()
        sub_list = self.prof_mgr.list_subjects()
        self.sub_box = ttk.Combobox(
            top_bar, textvariable=self.sub_var, state="readonly",
            values=[name for name, _ in sub_list] + ["➕ Додати предмет..."], width=19
        )
        self.sub_box.pack(side="left", padx=4)
        for name, sid in sub_list:
            if sid == self.active_profile.get("id"):
                self.sub_box.set(name)
                break
        self.sub_box.bind("<<ComboboxSelected>>", self._on_subject_changed)

        # Швидкі кнопки джерела звуку
        btn_box = ttk.Frame(top_bar)
        btn_box.pack(side="left", padx=6)
        ttk.Button(btn_box, text="🎤 Мікрофон", command=lambda: self.switch_category("mic"), width=12).pack(side="left", padx=2)
        ttk.Button(btn_box, text="🌐 Браузер", command=lambda: self.switch_category("browser"), width=10).pack(side="left", padx=2)
        ttk.Button(btn_box, text="🔊 Динаміки", command=lambda: self.switch_category("system"), width=11).pack(side="left", padx=2)

        self.api_badge = ttk.Label(top_bar, text="", font=("Segoe UI", 9, "bold"))
        self.api_badge.pack(side="right", padx=4)
        self._update_api_badge()

        # Панель пристрою та гучності
        dev_frame = ttk.Frame(self.root)
        dev_frame.pack(fill="x", **pad)
        ttk.Label(dev_frame, text="Пристрій:").pack(side="left")
        self.src_var = tk.StringVar()
        self.src_box = ttk.Combobox(
            dev_frame, textvariable=self.src_var, state="readonly",
            values=[n for n, _ in self.sources], width=45
        )
        self.src_box.pack(side="left", fill="x", expand=True, padx=4)
        self.src_box.bind("<<ComboboxSelected>>", lambda e: self.start_source())

        self.level_bar = ttk.Progressbar(dev_frame, maximum=100, length=90)
        self.level_bar.pack(side="left", padx=6)
        self.buf_label = ttk.Label(dev_frame, text="0 с", width=5)
        self.buf_label.pack(side="left")

        # Індикатор теми уроку та кнопка Новий Урок
        ctx_bar = ttk.Frame(self.root)
        ctx_bar.pack(fill="x", padx=10, pady=(1, 4))
        self.banner_var = tk.StringVar()
        self._refresh_banner()
        banner_lbl = ttk.Label(
            ctx_bar, textvariable=self.banner_var,
            font=("Segoe UI", 9, "italic"), foreground="#004da8"
        )
        banner_lbl.pack(side="left", fill="x", expand=True)

        btn_new_lesson = ttk.Button(ctx_bar, text="🔄 Новий урок", command=self.on_start_new_lesson)
        btn_new_lesson.pack(side="right")

        # --- Вкладки ---
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=4)

        self.tab_answer = ttk.Frame(self.notebook)
        self.tab_memory = ttk.Frame(self.notebook)
        self.tab_settings = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_answer, text="⚡ Суфлер (F8)")
        self.notebook.add(self.tab_memory, text="🧠 Пам'ять уроку та вчителя")
        self.notebook.add(self.tab_settings, text="⚙️ Налаштування та API")

        self._build_tab_answer()
        self._build_tab_memory()
        self._build_tab_settings()

        self.status = ttk.Label(self.root, text="Готовий до уроку. Натисни F8 під час запитання вчителя.", foreground="#333")
        self.status.pack(fill="x", padx=10, pady=6)

    def switch_category(self, cat: str):
        self.cfg["audio_category"] = cat
        save_config(self.cfg)
        for i, (name, data) in enumerate(self.sources):
            if data.get("category") == cat:
                if cat == "mic" and "fifine" in name.lower():
                    self.src_box.current(i)
                    self.start_source()
                    return
                self.src_box.current(i)
                self.start_source()
                return
        messagebox.showinfo("Звук", f"Пристроїв категорії '{cat}' не знайдено.")

    def _refresh_banner(self):
        sub_name = self.active_profile.get("name", "Урок")
        t_name = self.active_profile.get("teacher_name", "")
        t_str = f" | Вчитель: {t_name}" if t_name else ""
        self.banner_var.set(f"🎓 {sub_name}{t_str} — Тема: {self.context.topic}")

    def on_start_new_lesson(self):
        """Чіткий поділ уроків: очищення буфера звуку і контексту."""
        self.rec.clear()
        self.context.reset_lesson()
        self._refresh_all_views()
        self.txt_aloud.delete("1.0", "end")
        self.txt_aloud.insert("1.0", "Новий урок розпочато! Очікую перше запитання вчителя...")
        self.lbl_question.config(text="—")
        self.txt_deep.delete("1.0", "end")
        self.set_status("🔄 Новий урок розпочато! Аудіобуфер і тему скинуто.")
        logging.info(f"Розпочато новий урок: {self.active_profile.get('name')}")

    # ---------------- Вкладка 1: Суфлер ----------------
    def _build_tab_answer(self):
        f = self.tab_answer

        # Головна кнопка відповіді
        self.btn_answer = tk.Button(
            f, text=f"⚡  СКАЗАТИ ВІДПОВІДЬ НА ПИТАННЯ  ({HOTKEY.upper()})",
            font=("Segoe UI", 12, "bold"), bg="#0a60c2", fg="white",
            activebackground="#084796", activeforeground="white",
            cursor="hand2", relief="flat", padx=10, pady=8,
            command=self.on_trigger_answer
        )
        self.btn_answer.pack(fill="x", padx=10, pady=(6, 4))

        # Поле для швидкого ручного запитання
        row_quick = ttk.Frame(f)
        row_quick.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(row_quick, text="Або напиши питання:").pack(side="left")
        self.ent_quick = ttk.Entry(row_quick, font=("Segoe UI", 10))
        self.ent_quick.pack(side="left", fill="x", expand=True, padx=6)
        self.ent_quick.bind("<Return>", lambda e: self.on_quick_text_answer())
        btn_quick = ttk.Button(row_quick, text="Відповісти ⚡", command=self.on_quick_text_answer)
        btn_quick.pack(side="left")

        # Картка: СКАЗАТИ ВГОЛОС
        card_aloud = ttk.LabelFrame(f, text="🗣️ СКАЗАТИ ВГОЛОС (Автоматично скопійовано в буфер обміну)")
        card_aloud.pack(fill="x", padx=10, pady=4)

        self.txt_aloud = tk.Text(
            card_aloud, height=4, font=("Segoe UI", 12, "bold"),
            wrap="word", bg="#f4f8ff", relief="solid", bd=1
        )
        self.txt_aloud.pack(fill="x", padx=8, pady=6)
        self.txt_aloud.insert("1.0", "Тут з'явиться готова відповідь учня українською мовою...")

        row_copy = ttk.Frame(card_aloud)
        row_copy.pack(fill="x", padx=8, pady=(0, 6))
        self.lbl_time = ttk.Label(row_copy, text="", foreground="#007700", font=("Segoe UI", 9, "bold"))
        self.lbl_time.pack(side="left")
        btn_copy = ttk.Button(row_copy, text="📋 Скопіювати відповідь", command=self.copy_aloud_text)
        btn_copy.pack(side="right")

        # Розпізнане запитання вчителя
        card_q = ttk.LabelFrame(f, text="❓ Розпізнане запитання вчителя")
        card_q.pack(fill="x", padx=10, pady=4)
        self.lbl_question = ttk.Label(card_q, text="—", font=("Segoe UI", 10), wraplength=680, justify="left")
        self.lbl_question.pack(fill="x", padx=8, pady=6)

        # Розгорнуте пояснення
        card_deep = ttk.LabelFrame(f, text="📖 Розгорнуте пояснення та розрахунок (якщо вчитель спитає 'чому?')")
        card_deep.pack(fill="both", expand=True, padx=10, pady=4)

        self.txt_deep = tk.Text(card_deep, font=("Segoe UI", 10), wrap="word", relief="flat")
        sb = ttk.Scrollbar(card_deep, command=self.txt_deep.yview)
        self.txt_deep.config(yscrollcommand=sb.set)
        self.txt_deep.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        sb.pack(side="right", fill="y", pady=6)

    # ---------------- Вкладка 2: Пам'ять уроку ----------------
    def _build_tab_memory(self):
        f = self.tab_memory
        p = {"padx": 10, "pady": 4}

        # 1. Поточний урок наживо (жива персоналізація)
        card_live = ttk.LabelFrame(f, text="🟢 Поточний урок наживо (Автоматична персонализація в реальному часі)")
        card_live.pack(fill="x", **p)

        row_top = ttk.Frame(card_live)
        row_top.pack(fill="x", padx=8, pady=2)
        ttk.Label(row_top, text="Тема уроку:", font=("Segoe UI", 9, "bold"), width=15).pack(side="left")
        self.lbl_live_topic = ttk.Label(row_top, text=self.context.topic, foreground="#0a60c2", font=("Segoe UI", 9, "bold"), wraplength=520)
        self.lbl_live_topic.pack(side="left", fill="x", expand=True)

        row_sty = ttk.Frame(card_live)
        row_sty.pack(fill="x", padx=8, pady=2)
        ttk.Label(row_sty, text="Стиль вчителя:", font=("Segoe UI", 9, "bold"), width=15).pack(side="left")
        self.lbl_live_style = ttk.Label(row_sty, text=self.context.teacher_style, foreground="#333", wraplength=520)
        self.lbl_live_style.pack(side="left", fill="x", expand=True)

        row_trm = ttk.Frame(card_live)
        row_trm.pack(fill="x", padx=8, pady=2)
        ttk.Label(row_trm, text="Терміни уроку:", font=("Segoe UI", 9, "bold"), width=15).pack(side="left")
        terms_txt = ", ".join(self.context.key_terms) if self.context.key_terms else "поки не зафіксовано"
        self.lbl_live_terms = ttk.Label(row_trm, text=terms_txt, foreground="#444", wraplength=520)
        self.lbl_live_terms.pack(side="left", fill="x", expand=True)

        row_ev = ttk.Frame(card_live)
        row_ev.pack(fill="x", padx=8, pady=2)
        ttk.Label(row_ev, text="Хід уроку:", font=("Segoe UI", 9, "bold"), width=15).pack(side="left")
        last_ev = self.context.recent_events[-1] if self.context.recent_events else "Початок уроку"
        self.lbl_live_events = ttk.Label(row_ev, text=last_ev, foreground="#666", wraplength=520)
        self.lbl_live_events.pack(side="left", fill="x", expand=True)

        # 2. Викладач та постійні правила предмету
        card_style = ttk.LabelFrame(f, text="🎭 Викладач та постійні вимоги до відповідей")
        card_style.pack(fill="x", **p)

        row1 = ttk.Frame(card_style)
        row1.pack(fill="x", padx=8, pady=3)
        ttk.Label(row1, text="Ім'я викладача:").pack(side="left")
        self.ent_teacher = ttk.Entry(row1, width=28)
        self.ent_teacher.insert(0, self.active_profile.get("teacher_name", ""))
        self.ent_teacher.pack(side="left", padx=6)
        btn_save_t = ttk.Button(row1, text="💾 Зберегти ім'я", command=self._on_save_teacher_name)
        btn_save_t.pack(side="left")

        self.txt_rules = tk.Text(card_style, height=2, font=("Segoe UI", 10), wrap="word")
        self.txt_rules.pack(fill="x", padx=8, pady=3)
        rules_text = "\n".join(self.active_profile.get("permanent_rules", []))
        self.txt_rules.insert("1.0", rules_text or self.active_profile.get("teacher_style", ""))
        btn_save_rules = ttk.Button(card_style, text="💾 Зберегти правила стилю", command=self._on_save_rules)
        btn_save_rules.pack(anchor="e", padx=8, pady=(0, 3))

        # 3. База минулих уроків
        card_past = ttk.LabelFrame(f, text="📚 База минулих лекцій та уроків цього вчителя")
        card_past.pack(fill="both", expand=True, **p)

        row_acts = ttk.Frame(card_past)
        row_acts.pack(fill="x", padx=8, pady=(3, 1))
        btn_archive = ttk.Button(row_acts, text="💾 Зберегти поточний урок в архів", command=self.on_archive_lesson)
        btn_archive.pack(side="left")
        btn_clear = ttk.Button(row_acts, text="🗑️ Очистити архів", command=self.on_clear_archive)
        btn_clear.pack(side="right")

        self.txt_past = tk.Text(card_past, font=("Segoe UI", 9), wrap="word", relief="flat")
        sb_p = ttk.Scrollbar(card_past, command=self.txt_past.yview)
        self.txt_past.config(yscrollcommand=sb_p.set)
        self.txt_past.pack(side="left", fill="both", expand=True, padx=6, pady=4)
        sb_p.pack(side="right", fill="y", pady=4)
        self._refresh_past_lessons_view()

    def _refresh_memory_tab(self):
        try:
            self.lbl_live_topic.config(text=self.context.topic)
            self.lbl_live_style.config(text=self.context.teacher_style)
            terms_txt = ", ".join(self.context.key_terms) if self.context.key_terms else "поки не зафіксовано"
            self.lbl_live_terms.config(text=terms_txt)
            last_ev = self.context.recent_events[-1] if self.context.recent_events else "Початок уроку"
            self.lbl_live_events.config(text=last_ev)
        except Exception:
            pass

    def _refresh_all_views(self):
        self._refresh_banner()
        self._refresh_memory_tab()

    def on_clear_archive(self):
        if messagebox.askyesno("Очищення архіву", f"Дійсно очистити збережені минулі уроки для «{self.active_profile.get('name')}»?"):
            self.active_profile["past_lessons"] = []
            self.prof_mgr.save(self.active_profile)
            self._refresh_past_lessons_view()
            self.set_status("Архів минулих уроків очищено ✔")

    def _refresh_past_lessons_view(self):
        self.txt_past.delete("1.0", "end")
        past = self.active_profile.get("past_lessons", [])
        if not past:
            self.txt_past.insert("1.0", "В архіві поки немає збережених уроків. Натисни «Зберегти урок в архів» наприкінці пари.")
            return
        for item in reversed(past):
            self.txt_past.insert("end", f"📅 {item.get('date', '—')} | Тема: {item.get('topic', '—')}\n")
            self.txt_past.insert("end", f"   Конспект: {item.get('summary', '—')}\n\n")

    def _on_save_teacher_name(self):
        self.active_profile["teacher_name"] = self.ent_teacher.get().strip()
        self.prof_mgr.save(self.active_profile)
        self._refresh_banner()
        self.set_status(f"Викладача збережено: {self.active_profile['teacher_name']} ✔")

    def _on_save_rules(self):
        raw = self.txt_rules.get("1.0", "end").strip()
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        self.active_profile["permanent_rules"] = lines
        self.active_profile["teacher_style"] = lines[0] if lines else "Академічний"
        self.prof_mgr.save(self.active_profile)
        self.context.teacher_style = self.active_profile["teacher_style"]
        self.set_status("Правила вчителя збережено в профіль ✔")

    def on_archive_lesson(self):
        summary = " ".join([re.sub(r"^\[\d+:\d+:\d+\]\s*", "", e) for e in self.context.recent_events[-4:]])
        if not summary:
            summary = f"Пройдено матеріал за темою {self.context.topic}"
        self.prof_mgr.add_lesson_archive(self.active_profile["id"], self.context.topic, summary)
        self._refresh_past_lessons_view()
        self.set_status(f"Урок «{self.context.topic}» збережено в архів предмета ✔")

    # ---------------- Вкладка 3: Налаштування ----------------
    def _build_tab_settings(self):
        f = self.tab_settings
        p = {"padx": 10, "pady": 6}

        key_frame = ttk.LabelFrame(f, text="🔑 Google Gemini API Key")
        key_frame.pack(fill="x", **p)

        row_k = ttk.Frame(key_frame)
        row_k.pack(fill="x", padx=8, pady=6)
        self.key_var = tk.StringVar(value=self.cfg.get("gemini_api_key", ""))
        self.entry_key = ttk.Entry(row_k, textvariable=self.key_var, show="*", width=42)
        self.entry_key.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.show_key_var = tk.BooleanVar(value=False)
        chk_show = ttk.Checkbutton(
            row_k, text="Показати", variable=self.show_key_var,
            command=lambda: self.entry_key.config(show="" if self.show_key_var.get() else "*")
        )
        chk_show.pack(side="left", padx=4)

        btn_save_key = ttk.Button(row_k, text="💾 Зберегти ключ", command=self.on_save_key)
        btn_save_key.pack(side="left", padx=4)

        opt_frame = ttk.LabelFrame(f, text="⚡ Опції роботи")
        opt_frame.pack(fill="x", **p)

        self.bg_listen_var = tk.BooleanVar(value=self.cfg.get("background_listening", True))
        ttk.Checkbutton(
            opt_frame, text="Фонове прослуховування уроку (аналіз теми та стилю кожні 30 с)",
            variable=self.bg_listen_var, command=self._on_opt_change
        ).pack(anchor="w", padx=8, pady=3)

        self.auto_copy_var = tk.BooleanVar(value=self.cfg.get("auto_copy_answer", True))
        ttk.Checkbutton(
            opt_frame, text="Одразу копіювати відповідь «Сказати вголос» у буфер обміну",
            variable=self.auto_copy_var, command=self._on_opt_change
        ).pack(anchor="w", padx=8, pady=3)

        self.topmost_var = tk.BooleanVar(value=self.cfg.get("topmost", True))
        ttk.Checkbutton(
            opt_frame, text="Вікно поверх усіх інших вікон (Always on top)",
            variable=self.topmost_var,
            command=lambda: (self.root.attributes("-topmost", self.topmost_var.get()), self._on_opt_change())
        ).pack(anchor="w", padx=8, pady=3)

        # Довжина зрізу питання
        sec_frame = ttk.Frame(opt_frame)
        sec_frame.pack(fill="x", padx=8, pady=4)
        ttk.Label(sec_frame, text="Тривалість зрізу питання:").pack(side="left")
        self.sec_var = tk.IntVar(value=self.cfg.get("listen_seconds", 12))
        sec_spin = ttk.Spinbox(sec_frame, from_=5, to=30, textvariable=self.sec_var, width=5, command=self._on_opt_change)
        sec_spin.pack(side="left", padx=6)
        ttk.Label(sec_frame, text="секунд (рекомендовано 20-25 с: вчитель може спочатку питати інших учнів)").pack(side="left")

        name_frame = ttk.Frame(opt_frame)
        name_frame.pack(fill="x", padx=8, pady=4)
        ttk.Label(name_frame, text="Моє ім'я (як кличе вчитель):").pack(side="left")
        self.name_var = tk.StringVar(value=self.cfg.get("student_name", ""))
        ent_name = ttk.Entry(name_frame, textvariable=self.name_var, width=20)
        ent_name.pack(side="left", padx=6)
        ent_name.bind("<FocusOut>", lambda e: self._on_opt_change())
        ent_name.bind("<Return>", lambda e: self._on_opt_change())
        ttk.Label(name_frame, text="(порожньо = відповідаю на останнє питання)").pack(side="left")

        m_frame = ttk.LabelFrame(f, text="🤖 Моделі Gemini")
        m_frame.pack(fill="x", **p)

        r1 = ttk.Frame(m_frame)
        r1.pack(fill="x", padx=8, pady=4)
        ttk.Label(r1, text="Модель відповідей:", width=20).pack(side="left")
        self.ans_model_var = tk.StringVar(value=self.cfg.get("answer_model", "gemini-3.5-flash-lite"))
        box_ans = ttk.Combobox(r1, textvariable=self.ans_model_var, values=ANSWER_MODELS, width=26)
        box_ans.pack(side="left")
        box_ans.bind("<<ComboboxSelected>>", lambda e: self._on_opt_change())

    def _update_api_badge(self):
        if self.gemini.is_ready():
            self.api_badge.config(text="🟢 Gemini Flash-Lite Онлайн", foreground="#008800")
        else:
            self.api_badge.config(text="🔴 Ключ не задано", foreground="#b80000")

    def _on_subject_changed(self, event=None):
        sel = self.sub_box.get()
        if sel == "➕ Додати предмет...":
            new_name = simpledialog.askstring("Новий предмет", "Введіть назву предмета (наприклад, Зарубіжна література):", parent=self.root)
            if new_name and new_name.strip():
                sub_id = re.sub(r"\W+", "_", new_name.lower()).strip("_")
                new_prof = {
                    "id": sub_id,
                    "name": new_name.strip(),
                    "teacher_name": "",
                    "teacher_style": "Академічний стиль, українська мова",
                    "permanent_rules": ["Відповідати українською мовою"],
                    "cumulative_terms": [],
                    "past_lessons": []
                }
                self.prof_mgr.save(new_prof)
                self.prof_mgr.load_all()
                sub_list = self.prof_mgr.list_subjects()
                self.sub_box.config(values=[name for name, _ in sub_list] + ["➕ Додати предмет..."])
                self.sub_box.set(new_name.strip())
                self._switch_to_subject(sub_id)
            else:
                self.sub_box.set(self.active_profile.get("name", "Урок"))
            return

        for name, sid in self.prof_mgr.list_subjects():
            if name == sel:
                self._switch_to_subject(sid)
                break

    def _switch_to_subject(self, sub_id: str):
        self.active_profile = self.prof_mgr.get(sub_id)
        self.cfg["active_subject"] = sub_id
        save_config(self.cfg)

        # Очищуємо аудіобуфер при зміні предмета, щоб не змішувати уроки!
        self.rec.clear()
        self.context.set_profile(self.active_profile)
        self._refresh_all_views()

        self.ent_teacher.delete(0, "end")
        self.ent_teacher.insert(0, self.active_profile.get("teacher_name", ""))
        self.txt_rules.delete("1.0", "end")
        rules_text = "\n".join(self.active_profile.get("permanent_rules", []))
        self.txt_rules.insert("1.0", rules_text or self.active_profile.get("teacher_style", ""))
        self._refresh_past_lessons_view()

        self.set_status(f"Перемкнено на: {self.active_profile.get('name')} ✔ (Буфер звуку очищено)")
        logging.info(f"Перемкнено на предмет: {self.active_profile.get('name')}")

    def _on_opt_change(self):
        self.cfg["answer_model"] = self.ans_model_var.get()
        self.cfg["background_listening"] = self.bg_listen_var.get()
        self.cfg["auto_copy_answer"] = self.auto_copy_var.get()
        self.cfg["topmost"] = self.topmost_var.get()
        self.cfg["listen_seconds"] = self.sec_var.get()
        self.cfg["student_name"] = self.name_var.get().strip()
        self.gemini.student_name = self.cfg["student_name"]
        save_config(self.cfg)

    def on_save_key(self):
        k = self.key_var.get().strip()
        self.cfg["gemini_api_key"] = k
        self.gemini.set_config(k, self.ans_model_var.get(), self.cfg.get("background_model", "gemini-3.5-flash-lite"))
        save_config(self.cfg)
        self._update_api_badge()
        if self.gemini.is_ready():
            messagebox.showinfo("Gemini API", "API-ключ збережено та активовано!")
        else:
            messagebox.showwarning("Gemini API", "Ключ порожній.")

    # ---------------- Логіка аудіо ----------------
    def set_status(self, text):
        self.status.config(text=text)

    def start_source(self):
        idx = self.src_box.current()
        if idx < 0:
            return
        try:
            self.rec.start(self.sources[idx][1])
            src_name = self.sources[idx][0]
            logging.info(f"Аудіоджерело активовано: {src_name}")
            self.set_status(f"Слухаю: {src_name} | Тисни {HOTKEY.upper()} для відповіді")
        except Exception as e:
            logging.error(f"Помилка активації аудіопристрою: {e}", exc_info=True)
            self.set_status(f"Помилка аудіопристрою: {e}")

    def setup_hotkey(self):
        self.root.bind(f"<{HOTKEY.upper()}>", lambda e: self.on_trigger_answer())
        try:
            import keyboard
            keyboard.add_hotkey(HOTKEY, lambda: self.root.after(0, self.on_trigger_answer))
        except Exception:
            pass

    def tick(self):
        try:
            self.level_bar["value"] = min(100, self.rec.level * 400)
            self.rec.level *= 0.8
            self.buf_label.config(text=f"{min(self.rec.available_seconds(), MAX_RING_SECONDS):.0f} с")
            if self.bg_active:
                self.root.after(100, self.tick)
        except Exception:
            pass

    # ---------------- Фоновий аналіз уроку ----------------
    def _background_listener_loop(self):
        while self.bg_active:
            time.sleep(self.cfg.get("background_interval_sec", 30))
            if not self.bg_listen_var.get() or not self.gemini.is_ready() or self.busy:
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
                self.root.after(0, self._refresh_all_views)

    # ---------------- Швидка відповідь текстом ----------------
    def on_quick_text_answer(self):
        q = self.ent_quick.get().strip()
        if not q or self.busy:
            return
        if not self.gemini.is_ready():
            self.set_status("⚠️ API-ключ Gemini не налаштовано!")
            return

        self.busy = True
        self.btn_answer.config(state="disabled", text="⏳ Рахую відповідь...")
        self.set_status(f"Обдумую питання: {q}...")

        def _text_worker():
            t0 = time.time()
            try:
                ctx_str = self.context.get_prompt_context()
                res = self.gemini.answer_text_question(q, ctx_str)
                dt = time.time() - t0
                if "error" in res:
                    logging.error(f"Gemini повернув помилку: {res['error']}")
                    self.root.after(0, lambda: self._on_worker_error(res["error"]))
                    return
                say_txt = res.get("say_aloud") or "Відповідь не сформовано"
                logging.info(f"Текстова відповідь отримана за {dt:.2f}с: {say_txt[:80]}...")
                self.root.after(0, lambda: self._apply_answer(res, dt))
            except Exception as e:
                logging.error(f"Збій у _text_worker: {e}", exc_info=True)
                self.root.after(0, lambda: self._on_worker_error(str(e)))

        threading.Thread(target=_text_worker, daemon=True).start()

    # ---------------- Генерація відповіді по F8 (Аудіо) ----------------
    def on_trigger_answer(self):
        if self.busy:
            logging.warning("F8 натиснуто, але суфлер уже генерує відповідь.")
            return
        if not self.gemini.is_ready():
            logging.warning("F8 натиснуто, але API-ключ Gemini не налаштовано.")
            self.set_status("⚠️ API-ключ Gemini не налаштовано у вкладці Налаштування!")
            return

        # Запобігаємо зрізанню звуку: якщо F8 натиснуто повторно менш ніж через 8 секунд,
        # не беремо мікро-шматок, а використовуємо попередній буфер або повідомляємо
        now = time.time()
        last_f8_time = getattr(self, "_last_f8_ts", 0)
        self._last_f8_ts = now

        # Беремо зріз аудіо (за замовчуванням 20-25 с)
        listen_s = self.cfg.get("listen_seconds", 25)
        audio = self.rec.get_last(listen_s)

        if len(audio) < TARGET_SR * 3:
            self.set_status("⚠️ Буфер звуку надто малий (<3 с) — зачекайте, поки вчитель договорить питання!")
            return
        rms = float(np.sqrt(np.mean(audio * audio)))
        cur_src = self.src_var.get()
        logging.info(f"F8 натиснуто. Джерело: {cur_src}, Довжина: {len(audio)/TARGET_SR:.1f}с, Гучність (RMS): {rms:.5f}")

        if rms < 0.0015:
            logging.warning(f"У звуці тиша (RMS {rms:.5f} < 0.0015).")
            self.set_status(f"⚠️ Тиша на «{cur_src[:22]}...»! Якщо голос поруч — натисни «Мікрофон»!")
            return

        # ВАЖЛИВО: Очищуємо буфер після взяття зрізу, щоб попереднє запитання ніколи не змішувалося з новим!
        self.rec.clear()

        self.busy = True
        self.btn_answer.config(state="disabled", text="⏳ Слухаю та рахую відповідь...")
        self.set_status("🧠 Розпізнаю аудіо та формулюю відповідь українською...")
        threading.Thread(target=self._audio_answer_worker, args=(audio,), daemon=True).start()

    def _audio_answer_worker(self, audio: np.ndarray):
        t0 = time.time()
        try:
            ctx_str = self.context.get_prompt_context()
            wav_bytes = audio_to_wav_bytes(audio, TARGET_SR)
            logging.info(f"Відправка {len(wav_bytes)} байт аудіо в Gemini ({self.gemini.answer_model})...")
            res = self.gemini.answer_audio_question(wav_bytes, ctx_str)
            dt = time.time() - t0

            if "error" in res:
                logging.error(f"Gemini повернув помилку: {res['error']}")
                self.root.after(0, lambda: self._on_worker_error(res["error"]))
                return

            say_txt = res.get("say_aloud") or "Відповідь не сформовано"
            logging.info(
                f"Відповідь від Gemini отримана за {dt:.2f}с:\n"
                f"  ХТО: {res.get('who')}\n"
                f"  ЗАПИТАННЯ: {res.get('question')}\n"
                f"  СКАЗАТИ ВГОЛОС: {say_txt}\n"
                f"  ДІАЛОГ: {res.get('dialogue')}"
            )
            self.root.after(0, lambda: self._apply_answer(res, dt))
        except Exception as e:
            logging.error(f"Збій у _audio_answer_worker: {e}", exc_info=True)
            self.root.after(0, lambda: self._on_worker_error(str(e)))

    def _on_worker_error(self, err_msg: str):
        self.busy = False
        self.btn_answer.config(state="normal", text=f"⚡  СКАЗАТИ ВІДПОВІДЬ НА ПИТАННЯ  ({HOTKEY.upper()})")
        logging.error(f"Помилка відповіді: {err_msg}")
        self.set_status(f"⚠️ Помилка запиту: {err_msg}")

    def _apply_answer(self, res: dict, dt: float):
        self.busy = False
        self.btn_answer.config(state="normal", text=f"⚡  СКАЗАТИ ВІДПОВІДЬ НА ПИТАННЯ  ({HOTKEY.upper()})")

        q = res.get("question", "Запитання з уроку")
        who = res.get("who", "")
        dlg = res.get("dialogue", "")
        aloud = res.get("say_aloud", "")
        deep = res.get("deep", "")
        terms = res.get("terms", "")

        self.context.register_qa(q, aloud)

        q_display = f"{q}  ({who})" if who else q
        self.lbl_question.config(text=q_display)
        self.txt_aloud.delete("1.0", "end")
        self.txt_aloud.insert("1.0", aloud)

        self.txt_deep.delete("1.0", "end")
        details = ""
        if dlg:
            details += f"ХІД ОПИТУВАННЯ В КЛАСІ (ДІАЛОГ):\n{dlg}\n\n"
        if deep:
            details += f"ОБҐРУНТУВАННЯ:\n{deep}\n\n"
        if terms:
            details += f"ТЕРМІНИ:\n{terms}\n"
        self.txt_deep.insert("1.0", details or aloud)

        self.lbl_time.config(text=f"⚡ Відповідь за {dt:.2f} с")
        if self.auto_copy_var.get() and aloud:
            self.copy_to_clipboard(aloud)
            self.set_status(f"✔ Відповідь скопійовано в буфер за {dt:.2f} с!")
        else:
            self.set_status(f"Відповідь готова за {dt:.2f} с")

        self.notebook.select(self.tab_answer)

    def copy_aloud_text(self):
        val = self.txt_aloud.get("1.0", "end").strip()
        if val:
            self.copy_to_clipboard(val)
            self.set_status("Скопійовано в буфер ✔")

    def copy_to_clipboard(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def on_close(self):
        self.bg_active = False
        try:
            self.rec.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = LessonListenerApp(root)
    root.mainloop()
