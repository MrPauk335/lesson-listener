"""
Комплексний бенчмарк швидкості та точності Lesson Listener:
1) AUDIO BENCHMARK (5 багато-голосих WAV-сценаріїв, де вчитель спочатку опитує 1-2 інших учнів, а потім нашого).
2) CLASSROOM DIALOGUE STRESS BENCHMARK (5 складних сценаріїв: переадресація питання після 2 помилок однокласників,
   каскадне обчислення від відповіді попереднього учня, вимога одиниць СІ після зауваження вчителя,
   дисциплінарний шум у кінці, зміна питання між учнями).

Запуск: python bench.py
Результати зберігаються у bench_results.json
"""
import json
import os
import statistics
import sys
import time
import numpy as np

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")

from core import (
    GeminiEngine,
    LessonContext,
    ProfileManager,
    audio_to_wav_bytes,
    load_config,
    TARGET_SR,
)

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(HERE, "bench_audio")
STUDENT = "Макс"

# 1. Реальні багато-голосі аудіосценарії (кешовані в bench_audio/*.npy)
AUDIO_CASES = [
    (
        "bio_ribosome",
        "biology",
        "Петренко помиляється про мітохондрію -> Макса питають про рибосоми",
        ["білк", "синтез"],
        ["енерг", "днк клітини"],
        True,
    ),
    (
        "math_priority",
        "algebra",
        "Оля відповідає 7*8=56 -> наступного учня питають 3 + 3 * 33",
        ["102", "сто два"],
        ["56", "п'ятдесят шість"],
        False,
    ),
    (
        "hist_ww2",
        "history",
        "Іра відповідає про 988 рік -> Макса питають про початок Другої світової",
        ["1939"],
        ["988"],
        True,
    ),
    (
        "phys_accel",
        "physics",
        "Сергій формулює 2-й закон Ньютона -> Макса питають що таке прискорення",
        ["швидк", "змін"],
        [],
        True,
    ),
    (
        "math_two_others",
        "algebra",
        "Катя (12/4=3) та Дмитро (15-9=6) -> наступного учня питають 25 * 4",
        ["100", "сто"],
        ["три", "шість"],
        False,
    ),
]

# 2. Стрес-сценарії складних діалогів опитування в класі
DIALOGUE_STRESS_CASES = [
    (
        "redirect_after_2_wrong",
        "physics",
        (
            "Вчитель: Коваленко, сформулюй закон Ома для ділянки кола.\n"
            "Коваленко: Сила струму дорівнює напрузі помножити на опір.\n"
            "Вчитель: Ні, це груба помилка! Бондаренко, виправ його!\n"
            "Бондаренко: Сила струму дорівнює опору поділити на напругу.\n"
            "Вчитель: Теж неправильно, обидва сідайте, двійки! Максе, відповідай ти!"
        ),
        ["прямо пропорційн", "напруз", "обернено", "опор"],
        ["помножити на опір", "опору поділити на напругу"],
        True,
    ),
    (
        "cascade_from_classmate",
        "algebra",
        (
            "Вчитель: Оля, скільки буде п'ятнадцять помножити на чотири?\n"
            "Оля: Шістдесят.\n"
            "Вчитель: Правильно. Денисе, а скільки буде сім на дев'ять?\n"
            "Денис: Шістдесят три.\n"
            "Вчитель: Добре. Макс, а ти візьми відповідь Олі, додай до неї сорок і поділи результат на чотири."
        ),
        ["25", "двадцять п'ять"],
        ["63", "шістдесят три"],
        True,
    ),
    (
        "scold_si_units",
        "physics",
        (
            "Вчитель: Петров, чому дорівнює механічна робота сили 20 Ньютонів на шляху 3 метри?\n"
            "Петров: Буде шістдесят.\n"
            "Вчитель: Що «шістдесят»?! Кілограмів чи метрів? Без одиниць вимірювання в системі СІ відповідь не зараховую! "
            "Наступний — скільки становить робота сили 15 Ньютонів на переміщенні 4 метри?"
        ),
        ["60 дж", "60 джоул", "шістдесят джоул"],
        [],
        False,
    ),
    (
        "discipline_noise_tail",
        "algebra",
        (
            "Вчитель: Софіє, чому дорівнює корінь із вісімдесяти одного?\n"
            "Софія: Дев'ять.\n"
            "Вчитель: Вірно. Максе, чому дорівнює арифметичний квадратний корінь зі ста сорока чотирьох? "
            "Іванов, негайно вимкни мікрофон і прибери телефон з парти!"
        ),
        ["12", "дванадцят"],
        ["9", "дев'ят", "мікрофон"],
        True,
    ),
    (
        "chem_multi_poll",
        "chemistry",
        (
            "Вчитель: Андрію, назви хімічну формулу кухонної солі.\n"
            "Андрій: Натрій хлор, NaCl.\n"
            "Вчитель: Добре. Марино, а формула вуглекислого газу?\n"
            "Марина: CO2.\n"
            "Вчитель: Правильно. Макс, назви хімічну формулу сульфатної (сірчаної) кислоти."
        ),
        ["h2so4", "h₂so₄", "аш два ес о чотири"],
        ["nacl", "co2"],
        True,
    ),
]


def norm(s: str) -> str:
    return (s or "").lower().replace("’", "'").replace("ʼ", "'")


def check(say: str, must_any, must_not) -> bool:
    s = norm(say)
    return any(m in s for m in must_any) and not any(m in s for m in must_not)


def main():
    cfg = load_config()
    key = cfg["gemini_api_key"]
    pm = ProfileManager()
    subjects = {c[1] for c in AUDIO_CASES} | {c[1] for c in DIALOGUE_STRESS_CASES}
    ctxs = {s: LessonContext(pm.get(s)).get_prompt_context() for s in subjects}

    eng = GeminiEngine(key, "gemini-3.5-flash-lite", "gemini-3.5-flash-lite", student_name=STUDENT)
    # Прогрів TLS-сесії
    eng.answer_text_question("2+2?", "")

    print("=" * 95)
    print("🚀 ЧАСТИНА 1: АУДІО-БЕНЧМАРК (РЕАЛЬНИЙ БАГАТОГОЛОСИЙ WAV + ОПИТУВАННЯ ІНШИХ УЧНІВ)")
    print("=" * 95)

    audio_rows = []
    audio_lat = []
    audio_ok = 0

    for cid, subj, desc, must_any, must_not, named in AUDIO_CASES:
        fp = os.path.join(AUDIO_DIR, f"{cid}.npy")
        audio = np.load(fp)
        raw_dur = len(audio) / TARGET_SR
        wav_raw = audio_to_wav_bytes(audio, TARGET_SR, preprocess=False)
        wav_opt = audio_to_wav_bytes(audio, TARGET_SR, preprocess=True)
        saved_pct = 100.0 * (1.0 - len(wav_opt) / len(wav_raw))

        eng.student_name = STUDENT if named else ""
        t0 = time.time()
        res = eng.answer_audio_question(wav_opt, ctxs[subj])
        dt = time.time() - t0
        say = res.get("say_aloud", "")
        q_rec = res.get("question", "")
        good = "error" not in res and check(say, must_any, must_not)
        audio_ok += int(good)
        audio_lat.append(dt)
        audio_rows.append({
            "id": cid,
            "desc": desc,
            "ok": good,
            "latency_s": round(dt, 2),
            "audio_sec": round(raw_dur, 1),
            "wav_saved_pct": round(saved_pct, 1),
            "question": q_rec,
            "say_aloud": say,
        })
        status = "✅ OK " if good else "❌ FAIL"
        print(f"{status} | {cid:16} | {dt:4.2f}s (WAV -{saved_pct:2.0f}%) | Q: {q_rec[:42]:42} -> {say[:65]}")
        time.sleep(1.2)

    print("\n" + "=" * 95)
    print("🧠 ЧАСТИНА 2: СТРЕС-БЕНЧМАРК СКЛАДНИХ ДІАЛОГІВ КЛАСУ (ПЕРЕАДРЕСАЦІЯ, КАСКАД, СІ, ШУМ)")
    print("=" * 95)

    stress_rows = []
    stress_lat = []
    stress_ok = 0

    for cid, subj, dialog_text, must_any, must_not, named in DIALOGUE_STRESS_CASES:
        eng.student_name = STUDENT if named else ""
        t0 = time.time()
        res = eng.answer_text_question(dialog_text, ctxs[subj])
        dt = time.time() - t0
        say = res.get("say_aloud", "")
        q_rec = res.get("question", "")
        good = "error" not in res and check(say, must_any, must_not)
        stress_ok += int(good)
        stress_lat.append(dt)
        stress_rows.append({
            "id": cid,
            "ok": good,
            "latency_s": round(dt, 2),
            "question": q_rec,
            "say_aloud": say,
        })
        status = "✅ OK " if good else "❌ FAIL"
        print(f"{status} | {cid:22} | {dt:4.2f}s | Q: {q_rec[:40]:40} -> {say[:65]}")
        time.sleep(1.0)

    all_lat = audio_lat + stress_lat
    total_ok = audio_ok + stress_ok
    total_n = len(AUDIO_CASES) + len(DIALOGUE_STRESS_CASES)

    print("\n" + "=" * 95)
    print("📊 ПІДСУМКОВА ТАБЛИЦЯ БЕНЧМАРКУ:")
    print(f"  • Аудіо-опитування класу (WAV):   {audio_ok}/{len(AUDIO_CASES)} ({100*audio_ok/len(AUDIO_CASES):.0f}%) | "
          f"avg={statistics.mean(audio_lat):.2f}s | p50={statistics.median(audio_lat):.2f}s | max={max(audio_lat):.2f}s")
    print(f"  • Стрес-діалоги класу (текст):    {stress_ok}/{len(DIALOGUE_STRESS_CASES)} ({100*stress_ok/len(DIALOGUE_STRESS_CASES):.0f}%) | "
          f"avg={statistics.mean(stress_lat):.2f}s | p50={statistics.median(stress_lat):.2f}s | max={max(stress_lat):.2f}s")
    print(f"  • ЗАГАЛЬНА ТОЧНІСТЬ:              {total_ok}/{total_n} ({100*total_ok/total_n:.0f}%) | "
          f"Загальна середня швидкість: {statistics.mean(all_lat):.2f}s")
    print("=" * 95)

    out = {
        "summary": {
            "audio_accuracy": f"{audio_ok}/{len(AUDIO_CASES)}",
            "audio_avg_s": round(statistics.mean(audio_lat), 2),
            "audio_p50_s": round(statistics.median(audio_lat), 2),
            "stress_accuracy": f"{stress_ok}/{len(DIALOGUE_STRESS_CASES)}",
            "stress_avg_s": round(statistics.mean(stress_lat), 2),
            "stress_p50_s": round(statistics.median(stress_lat), 2),
            "total_accuracy": f"{total_ok}/{total_n}",
            "total_accuracy_pct": round(100 * total_ok / total_n, 1),
            "overall_avg_s": round(statistics.mean(all_lat), 2),
        },
        "audio_cases": audio_rows,
        "stress_cases": stress_rows,
    }
    with open(os.path.join(HERE, "bench_results.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
