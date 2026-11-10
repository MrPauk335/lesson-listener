package org.lessonlistener.app;

import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

final class StudySupport {
    private static final int MAX_MODEL_LENGTH = 160;
    static final int MAX_QUESTION_LENGTH = 20_000;
    static final int MAX_CONTEXT_LENGTH = 12_000;

    private StudySupport() { }

    static boolean shouldReleaseImagePermission(String grantedUri, String selectedUri) {
        return !grantedUri.equals(selectedUri);
    }

    static String defaultModel(String provider) {
        if ("openrouter".equals(provider)) return "google/gemma-4-31b-it:free";
        if ("groq".equals(provider)) return "llama-3.3-70b-versatile";
        return "gemini-3.5-flash-lite";
    }

    static Map<String, String> geminiHeaders(String apiKey) {
        Map<String, String> headers = new HashMap<>();
        headers.put("x-goog-api-key", apiKey);
        return Collections.unmodifiableMap(headers);
    }

    static String validateModelName(String model) {
        String value = model == null ? "" : model.trim();
        if (value.isEmpty()) return "Укажите название модели.";
        if (value.length() > MAX_MODEL_LENGTH || value.chars().anyMatch(Character::isISOControl)) {
            return "Название модели слишком длинное или содержит недопустимые символы.";
        }
        return null;
    }

    static String validateTextInput(String question, String context) {
        if (question != null && question.length() > MAX_QUESTION_LENGTH) {
            return "Вопрос слишком длинный. Максимум — 20 000 символов.";
        }
        if (context != null && context.length() > MAX_CONTEXT_LENGTH) {
            return "Контекст урока слишком длинный. Максимум — 12 000 символов.";
        }
        return null;
    }

    static String buildPrompt(boolean russian, String task, String context, String question) {
        String instructions = russian
                ? "Ты — внимательный помощник ученика во время урока. " + task + "\n"
                    + "Отвечай по-русски. Сначала дай прямой ответ, затем понятное пошаговое объяснение. Не выдумывай данные, если условие неясно. Формулы оформляй читабельно, в конце кратко перечисли важные термины.\n\n"
                    + "КОНТЕКСТ ПРЕДМЕТА:\n"
                : "Ти — уважний помічник учня під час уроку. " + task + "\n"
                    + "Відповідай українською. Спершу дай пряму відповідь, далі зрозуміле покрокове пояснення. Не вигадуй дані, якщо умова нечітка. Формули оформлюй читабельно, наприкінці коротко підсумуй важливі терміни.\n\n"
                    + "КОНТЕКСТ ПРЕДМЕТА:\n";
        String result = instructions + (context == null ? "" : context);
        if (question == null || question.trim().isEmpty()) return result;
        return result + (russian ? "\nВОПРОС УЧЕНИКА:\n" : "\nПИТАННЯ УЧНЯ:\n") + question.trim();
    }

    static String responseError(int status) {
        if (status == 400) return "Провайдер отклонил запрос. Проверьте выбранную модель и формат задания.";
        if (status == 401 || status == 403) return "Провайдер не принял API-ключ или доступ к модели ограничен. Проверьте ключ.";
        if (status == 404) return "Модель не найдена. Укажите актуальное название модели в настройках.";
        if (status == 413) return "Запрос слишком большой. Выберите изображение меньшего размера.";
        if (status == 429) return "Лимит запросов провайдера исчерпан. Подождите или проверьте тариф и квоту.";
        if (status >= 500) return "Сервис провайдера временно недоступен. Попробуйте позже.";
        return "Провайдер вернул HTTP " + status + ". Проверьте настройки и повторите запрос.";
    }
}
