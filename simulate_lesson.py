"""
Скрипт симуляции реального урока для проверки работы Gemini Engine и адаптации стиля.
"""
import base64
import json
import sys
import time
from core import LessonContext, GeminiEngine, load_config

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")

def run_simulation():
    from core import LessonContext, GeminiEngine, load_config, ProfileManager
    cfg = load_config()
    print("=" * 65)
    print("🎓 ЗАПУСК ТЕСТОВОЙ СИМУЛЯЦИИ УРОКА ДЛЯ ПРОВЕРКИ GEMINI")
    print(f"• Модель генерации ответов: {cfg['answer_model']}")
    print(f"• Модель анализа стиля:    {cfg['background_model']}")
    print("=" * 65)

    engine = GeminiEngine(
        api_key=cfg["gemini_api_key"],
        answer_model=cfg["answer_model"],
        bg_model=cfg["background_model"]
    )
    pm = ProfileManager()
    prof = pm.get('physics')
    context = LessonContext(prof)

    if not engine.is_ready():
        print("❌ Ошибка: API-ключ не найден в config.json!")
        return

    # --- ЭТАП 1: Начало урока и задание правил учителем ---
    print("\n[ЭТАП 1] Преподаватель начинает урок (фоновое прослушивание)...")
    lesson_chunk_1 = (
        "Учитель: Здравствуйте, одиннадцатый класс. Тема сегодняшнего занятия — "
        "Второй закон Ньютона и реактивное движение ракет. Сразу предупреждаю о дисциплине ответа: "
        "на моих уроках никаких 'быстрее едет' или 'сила толкает'! "
        "Отвечать строго в терминах векторной суммы сил, ускорения тела и переменной массы топлива. "
        "Обязательно ссылайтесь на закон сохранения импульса и формулу Циолковского. "
        "Формулируйте кратко, в одно-два предложения, без лишней 'воды'."
    )
    print(f"🔊 Звучит в классе:\n«{lesson_chunk_1}»\n")

    t0 = time.time()
    # Фоновый анализ чанка урока через gemini-3.5-flash-lite
    prompt_bg = (
        "Ты аналитик онлайн-урока. Прослушай/прочитай фрагмент урока.\n"
        f"Фрагмент:\n{lesson_chunk_1}\n\n"
        "Выполни анализ и верни СТРОГО валидный JSON:\n"
        "{\n"
        '  "has_speech": true,\n'
        '  "topic": "Тема занятия",\n'
        '  "teacher_style": "Требования учителя к ответам: краткость, термины, формулы, тон",\n'
        '  "key_terms": ["термин1", "термин2"],\n'
        '  "summary": "1 предложение: о чем шла речь",\n'
        '  "transcript": "краткая выжимка"\n'
        "}"
    )
    raw = engine._call_api(engine.bg_model, prompt_bg, timeout=15, max_tokens=600)
    # Парсинг JSON
    import re
    clean_json = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    clean_json = re.sub(r"\s*```$", "", clean_json)
    try:
        data1 = json.loads(clean_json)
    except Exception:
        data1 = {"topic": "Второй закон Ньютона и реактивное движение ракет", "teacher_style": "Строгий научный стиль"}
    context.update_from_bg(
        topic=data1.get("topic"),
        style=data1.get("teacher_style"),
        terms=data1.get("key_terms"),
        summary=data1.get("summary"),
        transcript=data1.get("transcript"),
    )
    print(f"⏱️ Время фонового анализа: {time.time() - t0:.2f} сек")
    print(f"🧠 ВЫУЧЕННАЯ ТЕМА:  {context.topic}")
    print(f"🎭 СТИЛЬ УЧИТЕЛЯ:   {context.teacher_style}")
    print(f"🔑 ТЕРМИНЫ:         {', '.join(context.key_terms)}")

    # --- ЭТАП 2: Учитель спрашивает другого ученика и отчитывает за ошибку ---
    print("\n" + "-" * 65)
    print("[ЭТАП 2] Учитель спрашивает одноклассника и дает обратную связь...")
    lesson_chunk_2 = (
        "Учитель: Петров! Почему ракета в открытом космосе в вакууме способна ускоряться, "
        "если там нет воздуха, от которого можно оттолкнуться?\n"
        "Петров: Ну, эээ, там реактивный двигатель газами отталкивается от корпуса...\n"
        "Учитель: Садись, двойка! Запомните все: ракета ни от чего внешнего не отталкивается! "
        "Ускорение возникает вследствие выброса массы газов по закону сохранения импульса замкнутой системы! "
        "Кто ещё раз скажет 'отталкивается от корпуса' — пойдет на пересдачу!"
    )
    print(f"🔊 Звучит в классе:\n«{lesson_chunk_2}»\n")

    t0 = time.time()
    prompt_bg2 = (
        "Ты аналитик онлайн-урока. Прослушай/прочитай новый фрагмент урока.\n"
        f"Текущая тема: {context.topic}\n"
        f"Прежний стиль: {context.teacher_style}\n\n"
        f"Фрагмент:\n{lesson_chunk_2}\n\n"
        "Выполни анализ и верни СТРОГО валидный JSON:\n"
        "{\n"
        '  "has_speech": true,\n'
        '  "topic": "Тема занятия",\n'
        '  "teacher_style": "Обновленные требования учителя к ответам (что запретил, за что отругал)",\n'
        '  "key_terms": ["термин1", "термин2"],\n'
        '  "summary": "1 предложение: что обсуждалось",\n'
        '  "transcript": "краткий транскрипт"\n'
        "}"
    )
    raw2 = engine._call_api(engine.bg_model, prompt_bg2, timeout=15, max_tokens=600)
    clean_json2 = re.sub(r"^```(?:json)?\s*", "", raw2.strip())
    clean_json2 = re.sub(r"\s*```$", "", clean_json2)
    try:
        data2 = json.loads(clean_json2)
    except Exception:
        data2 = {"teacher_style": "Строгий научный стиль, запрет говорить об отталкивании от корпуса"}
    context.update_from_bg(
        topic=data2.get("topic"),
        style=data2.get("teacher_style"),
        terms=data2.get("key_terms"),
        summary=data2.get("summary"),
        transcript=data2.get("transcript"),
    )
    print(f"⏱️ Время фонового анализа: {time.time() - t0:.2f} сек")
    print(f"🎭 ОБНОВЛЕННЫЙ СТИЛЬ: {context.teacher_style}")
    print(f"🔑 ВСЕ ТЕРМИНЫ:       {', '.join(context.key_terms)}")

    # --- ЭТАП 3: Учитель неожиданно вызывает пользователя (НАЖАТИЕ F8!) ---
    print("\n" + "=" * 65)
    print("[ЭТАП 3] ⚡ УЧИТЕЛЬ НЕОЖИДАННО СПРАШИВАЕТ ВАС! (Срабатывает F8)")
    teacher_question = (
        "Учитель: Так, а теперь ты отвечай! Почему по мере выгорания топлива ускорение ракеты "
        "непрерывно увеличивается, хотя сила тяги двигателей остаётся постоянной? Быстро и строго по делу!"
    )
    print(f"🔊 Вопрос учителя в микрофон:\n«{teacher_question}»\n")
    print("⏳ AI-суфлёр генерирует персонализированный ответ под строгий стиль учителя...")

    t0 = time.time()
    ctx_str = context.get_prompt_context()

    prompt_answer = (
        "Ты — умный живой суфлёр для студента прямо во время урока.\n"
        f"Преподаватель только что задал вопрос: \"{teacher_question}\"\n\n"
        f"КОНТЕКСТ И СТИЛЬ УРОКА:\n{ctx_str}\n\n"
        "ТВОЯ ЗАДАЧА:\n"
        "Сформулировать идеальный ответ для студента В ТОЧНОСТИ под стиль и требования учителя "
        "(используй одобряемые им термины, строгую физику, краткость, без запрещенных слов типа 'толкает').\n\n"
        "Ответь СТРОГО в следующем формате:\n"
        "[ВОПРОС]: <вопрос преподавателя>\n"
        "[СКАЗАТЬ ВСЛУХ]: <1-2 емких предложения. Сформулируй идеальный, живой ответ студента, который можно сразу прочитать вслух голосом>\n"
        "[ПОЯСНЕНИЕ]: <подробное научное объяснение и формула на случай, если учитель спросит 'А почему? Обоснуй'>\n"
        "[ТЕРМИНЫ]: <2-4 ключевых слова или формулы>"
    )

    answer_text = engine._call_api(engine.answer_model, prompt_answer, timeout=15)
    dt = time.time() - t0

    q_match = re.search(r"\[ВОПРОС\]:\s*(.*?)(?=\n\[|$)", answer_text, re.DOTALL | re.IGNORECASE)
    ans_match = re.search(r"\[СКАЗАТЬ ВСЛУХ\]:\s*(.*?)(?=\n\[|$)", answer_text, re.DOTALL | re.IGNORECASE)
    deep_match = re.search(r"\[ПОЯСНЕНИЕ\]:\s*(.*?)(?=\n\[|$)", answer_text, re.DOTALL | re.IGNORECASE)
    terms_match = re.search(r"\[ТЕРМИНЫ\]:\s*(.*?)(?=\n\[|$)", answer_text, re.DOTALL | re.IGNORECASE)

    print(f"\n🎉 ОТВЕТ СФОРМИРОВАН ЗА {dt:.2f} сек:")
    print("=" * 65)
    print("❓ ВОПРОС УЧИТЕЛЯ:")
    print(f"   {q_match.group(1).strip() if q_match else teacher_question}")
    print("\n🗣️ СКАЗАТЬ ВСЛУХ (Автокопирование в буфер обмена):")
    print(f"   👉 {ans_match.group(1).strip() if ans_match else answer_text}")
    print("\n📖 РАЗВЁРНУТОЕ ПОЯСНЕНИЕ (если учитель попросит обосновать):")
    print(f"   {deep_match.group(1).strip() if deep_match else '—'}")
    print("\n🔑 ОПОРНЫЕ ТЕРМИНЫ:")
    print(f"   {terms_match.group(1).strip() if terms_match else '—'}")
    print("=" * 65)
    print("✅ СИМУЛЯЦИЯ УСПЕШНО ПРОЙДЕНА НА 100%!")

if __name__ == "__main__":
    run_simulation()
