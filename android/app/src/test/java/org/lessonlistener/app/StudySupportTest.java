package org.lessonlistener.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class StudySupportTest {
    @Test
    public void defaultsUseSupportedStudyModels() {
        assertEquals("gemini-3.5-flash-lite", StudySupport.defaultModel("gemini"));
        assertEquals("google/gemma-4-31b-it:free", StudySupport.defaultModel("openrouter"));
        assertEquals("llama-3.3-70b-versatile", StudySupport.defaultModel("groq"));
    }

    @Test
    public void validatesEditableModelNames() {
        assertEquals("Укажите название модели.", StudySupport.validateModelName("  "));
        assertEquals("Укажите название модели.", StudySupport.validateModelName(null));
        assertNull(StudySupport.validateModelName("google/gemma-4-31b-it:free"));
        assertTrue(StudySupport.validateModelName("m".repeat(161)).contains("слишком длинное"));
        assertTrue(StudySupport.validateModelName("model\nname").contains("недопустимые"));
    }

    @Test
    public void buildsLocalizedPromptsWithContextAndQuestion() {
        String ukrainian = StudySupport.buildPrompt(false, "Поясни завдання.", "Предмет: Алгебра", "  Як знайти x?  ");
        assertTrue(ukrainian.contains("Відповідай українською."));
        assertTrue(ukrainian.contains("Предмет: Алгебра"));
        assertTrue(ukrainian.endsWith("ПИТАННЯ УЧНЯ:\nЯк знайти x?"));

        String russian = StudySupport.buildPrompt(true, "Реши задачу.", "Предмет: Алгебра", "Как найти x?");
        assertTrue(russian.contains("Отвечай по-русски."));
        assertTrue(russian.endsWith("ВОПРОС УЧЕНИКА:\nКак найти x?"));
    }

    @Test
    public void mapsProviderFailuresToSafeHumanReadableMessages() {
        assertTrue(StudySupport.responseError(401).contains("API-ключ"));
        assertTrue(StudySupport.responseError(429).contains("Лимит запросов"));
        assertTrue(StudySupport.responseError(404).contains("Модель не найдена"));
        assertTrue(StudySupport.responseError(503).contains("временно недоступен"));
        assertTrue(StudySupport.responseError(418).contains("HTTP 418"));
    }
}
